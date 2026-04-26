# executor.py - 执行者 Agent
"""
Executor Agent（执行者）：系统的"试验场"。

负责编译、运行、收集结果：
1. 编译被测程序（gcc + gcov 插桩 + ASan）
2. 自动生成测试驱动（harness）
3. 运行测试用例，捕获崩溃/超时/内存错误
4. 收集覆盖率数据并广播
"""

import os
import re
import time
import logging
import subprocess
from pathlib import Path

from core.base_agent import BaseAgent
from core.message_bus import MessageBus
from core.message_types import (
    Message,
    TestCase,
    ExecutionResult,
    TestCaseStatus,
    AnalysisResult,
    FunctionSignature,
    FunctionParameter,
)

logger = logging.getLogger(__name__)


class ExecutorAgent(BaseAgent):
    """执行者智能体。"""

    # MinGW gcc 路径（Windows）
    MINGW_GCC = r"C:\msys64\mingw64\bin\gcc.exe"
    MINGW_GCOV = r"C:\msys64\mingw64\bin\gcov.exe"

    def __init__(self, bus: MessageBus, config: dict = None):  # type: ignore
        super().__init__("Executor", bus, config)

        cfg = config or {}
        self.source_dir = os.path.normpath(cfg.get("source_dir", "./targets/cjson"))
        self.output_dir = os.path.normpath(cfg.get("output_dir", "./output"))
        self.timeout = int(cfg.get("timeout_per_case", 5))
        self.enable_gcov = bool(cfg.get("enable_gcov", True))

        # Windows 上默认禁用 ASan（MinGW 通常不稳定）
        if os.name == "nt":
            self.enable_asan = False
        else:
            self.enable_asan = bool(cfg.get("enable_asan", True))

        self.gcc_path = self._find_gcc()
        self.gcov_path = self._find_gcov()

        self.harness_path: str = ""
        self.use_native_main = False
        self.native_main_input_mode = "stdin"
        self.force_harness_main_rewrite = False
        self.compiled = False
        self.analysis_result: AnalysisResult | None = None
        self.selected_signature: FunctionSignature | None = None
        self.compile_failure_count = 0
        self.max_compile_failures = max(1, int(cfg.get("max_compile_failures", 3)))
        self.compile_blocked = False
        self._fatal_compile_error_sent = False
        self.last_compile_error = ""

        # API triage 限流，避免每个异常用例都请求模型导致超时堆积。
        self.api_max_requests_per_batch = int(cfg.get("api_max_requests_per_batch", 3))
        raw_statuses = cfg.get("api_triage_statuses", ["CRASH"])
        if not isinstance(raw_statuses, list):
            raw_statuses = ["CRASH"]
        self.api_triage_statuses = {str(s).strip().upper() for s in raw_statuses if str(s).strip()}
        if not self.api_triage_statuses:
            self.api_triage_statuses = {"CRASH"}
        self._api_requests_in_batch = 0
        self._api_limit_log_emitted = False

        # 覆盖率跟踪
        self.global_covered_lines: set[str] = set()
        self.global_covered_branches: set[str] = set()
        self.global_total_branches: set[str] = set()

        # 工作目录
        self.work_dir = os.path.normpath(os.path.join(self.output_dir, "executor_work"))
        os.makedirs(self.work_dir, exist_ok=True)

    def _find_gcc(self) -> str:
        """查找可用 gcc。"""
        import shutil

        gcc_in_path = shutil.which("gcc")
        if gcc_in_path:
            return gcc_in_path
        if os.name == "nt" and os.path.exists(self.MINGW_GCC):
            return self.MINGW_GCC
        return "gcc"

    def _find_gcov(self) -> str:
        """查找可用 gcov。"""
        import shutil

        gcov_in_path = shutil.which("gcov")
        if gcov_in_path:
            return gcov_in_path
        if os.name == "nt" and os.path.exists(self.MINGW_GCOV):
            return self.MINGW_GCOV
        return "gcov"

    def setup(self):
        self.subscribe("data:approved_cases", self.on_approved_cases)
        self.subscribe("data:analysis_result", self.on_analysis_result)

    def on_analysis_result(self, msg: Message):
        if isinstance(msg.data, AnalysisResult):
            self.analysis_result = msg.data
            self.selected_signature = self._select_entry_signature()
            if self.selected_signature is not None:
                self.log_info(f"自动选择通用入口函数: {self.selected_signature.name}")

        self.log_info("开始编译被测程序...")
        try:
            self._compile()
            self.log_info("编译完成")
        except Exception as e:
            self._handle_compile_failure(e, stage="analysis")

    def on_approved_cases(self, msg: Message):
        cases = msg.data
        if not isinstance(cases, list):
            return

        if self.compile_blocked:
            self.log_warning("编译已连续失败，执行器进入阻断状态，等待协调器终止本次任务")
            if not self._fatal_compile_error_sent:
                self._publish_compile_error(
                    self.last_compile_error or "连续编译失败，执行器无法运行测试用例",
                    fatal=True,
                    stage="execution",
                )
                self._fatal_compile_error_sent = True
            return

        if not self.compiled:
            self.log_warning("被测程序尚未编译，尝试编译...")
            try:
                self._compile()
            except Exception as e:
                self._handle_compile_failure(e, stage="execution")
                return

        self.log_info(f"开始执行 {len(cases)} 个测试用例")
        self._api_requests_in_batch = 0
        self._api_limit_log_emitted = False

        baseline_lines = set(self.global_covered_lines)
        baseline_branches = set(self.global_covered_branches)

        # native main 场景下先跑一次安全探针输入，确保 gcda/gcov 基线可用。
        self._run_coverage_probe()

        results: list[ExecutionResult] = []
        interesting_cases: list[TestCase] = []

        for case in cases:
            if not isinstance(case, TestCase):
                continue

            result = self._execute_single(case)
            results.append(result)

        # 覆盖率按批次收集，避免每个用例都执行 gcov 导致吞吐下降。
        if self.enable_gcov and results:
            lines, branches, total_branches = self._collect_coverage_snapshot()
            new_lines = lines - baseline_lines
            new_branches = branches - baseline_branches

            self.global_covered_lines.update(lines)
            self.global_covered_branches.update(branches)
            self.global_total_branches.update(total_branches)

            anchor = results[-1]
            anchor.coverage_delta = new_lines
            anchor.coverage_lines = lines
            anchor.branch_delta = new_branches
            anchor.branch_covered = branches
            anchor.branch_total = total_branches

            self.log_info(
                f"[OBS][coverage_batch] cases={len(results)}, new_lines={len(new_lines)}, "
                f"new_branches={len(new_branches)}, total_lines={len(lines)}, "
                f"total_branches={len(branches)}/{len(total_branches)}, "
                f"anchor_case={anchor.test_case_id}, anchor_status={anchor.status.value}"
            )

            if anchor.test_case is not None and (new_lines or new_branches):
                interesting_cases.append(anchor.test_case)

        crashes = [r for r in results if r.status == TestCaseStatus.CRASH]
        timeouts = [r for r in results if r.status == TestCaseStatus.TIMEOUT]
        self.log_info(
            f"执行完成: {len(results)} 个用例, {len(crashes)} 个崩溃, {len(timeouts)} 个超时"
        )

        self.publish("data:execution_results", results)

        if interesting_cases:
            interesting_ids = [tc.id for tc in interesting_cases if isinstance(tc, TestCase)]
            self.log_info(
                f"[OBS][coverage_update_emit] count={len(interesting_ids)}, case_ids={interesting_ids[:10]}"
            )
            self.publish("data:coverage_update", interesting_cases)

    def _publish_compile_error(self, error_text: str, fatal: bool, stage: str):
        detail = (error_text or "").strip() or "编译失败"
        hint = self._guess_compile_hint(detail)
        if hint and hint not in detail:
            detail = f"{detail}\n诊断提示: {hint}"

        first_line = detail.splitlines()[0].strip() if detail.splitlines() else detail
        payload = {
            "type": "COMPILE_FAILED",
            "stage": stage,
            "fatal": bool(fatal),
            "failureCount": int(self.compile_failure_count),
            "maxFailures": int(self.max_compile_failures),
            "message": first_line[:240],
            "detail": detail[:2000],
            "sourceDir": self.source_dir,
        }
        if hint:
            payload["hint"] = hint

        self.publish("data:executor_error", payload)

    def _handle_compile_failure(self, error: Exception, stage: str):
        detail = str(error).strip() or "编译失败"
        self.compiled = False
        self.last_compile_error = detail
        self.compile_failure_count += 1

        fatal = self.compile_failure_count >= self.max_compile_failures
        if fatal:
            self.compile_blocked = True

        self.log_error(
            f"编译失败[{self.compile_failure_count}/{self.max_compile_failures}] ({stage}): {detail[:240]}"
        )
        self._publish_compile_error(detail, fatal=fatal, stage=stage)

        if fatal:
            self._fatal_compile_error_sent = True
            self.log_error("连续编译失败达到阈值，执行器将停止继续尝试编译")

    def _guess_compile_hint(self, error_text: str) -> str:
        source_hint = self._detect_invalid_source_prefix()
        if source_hint:
            return source_hint

        lowered = (error_text or "").lower()
        if "no such file or directory" in lowered and "gcc" in lowered:
            return "未找到 gcc，可检查 MinGW 安装路径与 PATH 配置"

        return ""

    def _detect_invalid_source_prefix(self) -> str:
        source_dir = Path(self.source_dir)
        if not source_dir.exists():
            return ""

        for src in source_dir.glob("*.c"):
            try:
                head = src.read_text(encoding="utf-8", errors="ignore")[:160]
            except Exception:
                continue

            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*/\*", head)
            if match:
                token = match.group(1)
                return f"{src.name} 文件开头存在疑似非法前缀 '{token}'，请删除后重试"

        return ""

    def _run_coverage_probe(self):
        """运行一次低风险探针输入，帮助 gcov 在高崩溃目标上产出可用覆盖率基线。"""
        if not (self.compiled and self.enable_gcov and self.use_native_main):
            return

        try:
            env = os.environ.copy()
            gcc_dir = os.path.dirname(self.gcc_path)
            if gcc_dir and gcc_dir not in env.get("PATH", ""):
                env["PATH"] = gcc_dir + os.pathsep + env.get("PATH", "")

            if os.name != "nt":
                env["GCOV_PREFIX"] = self.work_dir
                env["GCOV_PREFIX_STRIP"] = "99"

            probe_file = os.path.join(self.work_dir, "__coverage_probe_input.dat")
            with open(probe_file, "wb") as f:
                # 用较短缓冲，尽量先触发可达路径而不是立即崩溃。
                f.write(b"IMG\x00\x00\x00\x10\x00\x00\x00\x10AAAAAAAAAA")

            if self.native_main_input_mode == "argv_file":
                subprocess.run(
                    [self.harness_path, probe_file],
                    capture_output=True,
                    timeout=max(1, min(self.timeout, 2)),
                    cwd=self.work_dir,
                    env=env,
                )
            elif self.native_main_input_mode == "argv_text":
                subprocess.run(
                    [self.harness_path, "10 + 20"],
                    capture_output=True,
                    timeout=max(1, min(self.timeout, 2)),
                    cwd=self.work_dir,
                    env=env,
                )
            else:
                subprocess.run(
                    [self.harness_path],
                    input=b"probe\n",
                    capture_output=True,
                    timeout=max(1, min(self.timeout, 2)),
                    cwd=self.work_dir,
                    env=env,
                )

            lines, branches, total_branches = self._collect_coverage_snapshot()
            if lines or branches:
                new_lines = lines - self.global_covered_lines
                new_branches = branches - self.global_covered_branches
                self.global_covered_lines.update(lines)
                self.global_covered_branches.update(branches)
                self.global_total_branches.update(total_branches)
                if new_lines or new_branches:
                    self.log_info(
                        f"覆盖探针新增 行={len(new_lines)} 分支={len(new_branches)}"
                    )
        except Exception as e:
            self.log_debug(f"覆盖探针执行失败(忽略): {e}")
        finally:
            try:
                os.remove(os.path.join(self.work_dir, "__coverage_probe_input.dat"))
            except Exception:
                pass

    def _compile(self):
        """编译被测程序。"""
        source_dir = Path(self.source_dir)
        c_sources = list(source_dir.glob("*.c"))
        if not c_sources:
            raise RuntimeError(f"未找到 C 源文件: {source_dir}")

        self._clean_work_dir()
        self.global_covered_lines.clear()
        self.global_covered_branches.clear()
        self.global_total_branches.clear()

        # 若目标已有 main，且 Analyzer 已选中可调用入口函数，则优先使用生成 harness。
        # 这样可以在不依赖交互式 main 的前提下更稳定地探索有效路径。
        has_native_main = False
        for src in c_sources:
            try:
                content = src.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"\bint\s+main\s*\(", content):
                    has_native_main = True
                    break
            except Exception:
                continue

        prefer_generated_harness = self.selected_signature is not None
        use_native_main = has_native_main and not prefer_generated_harness

        self.use_native_main = use_native_main
        self.force_harness_main_rewrite = bool(has_native_main and not use_native_main)
        if self.force_harness_main_rewrite:
            self.log_info("检测到 native main，改用函数级 harness 执行并重命名原始 main")

        harness_src = None
        if not use_native_main:
            harness_code = self._generate_harness()
            harness_src = os.path.join(self.work_dir, "harness.c")
            with open(harness_src, "w", encoding="utf-8") as f:
                f.write(harness_code)

        output_name = "target_program" if use_native_main else "harness"
        self.harness_path = os.path.join(self.work_dir, output_name)
        if os.name == "nt":
            self.harness_path += ".exe"

        source_files = [str(s) for s in c_sources]
        if harness_src:
            source_files.append(harness_src)

        compile_cmd = [self.gcc_path]
        compile_cmd.extend(source_files)
        compile_cmd.extend(["-o", self.harness_path])
        compile_cmd.extend(["-I", str(source_dir)])
        compile_cmd.extend(["-Wall", "-g", "-lm"])

        if self.force_harness_main_rewrite:
            compile_cmd.append("-Dmain=ctestagent_hidden_main")

        if self.enable_gcov:
            compile_cmd.extend(["--coverage", "-fprofile-arcs", "-ftest-coverage"])
        if self.enable_asan:
            compile_cmd.extend(["-fsanitize=address", "-fno-omit-frame-pointer"])

        self.log_info(f"编译命令: {' '.join(compile_cmd)}")

        compile_env = os.environ.copy()
        gcc_dir = os.path.dirname(self.gcc_path)
        if gcc_dir and gcc_dir not in compile_env.get("PATH", ""):
            compile_env["PATH"] = gcc_dir + os.pathsep + compile_env.get("PATH", "")

        result = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            cwd=self.work_dir,
            env=compile_env,
            timeout=60,
        )

        if result.returncode != 0:
            error_msg = (result.stderr or result.stdout or "未知错误").strip()
            self.log_error(f"编译错误 (rc={result.returncode}):\n{error_msg}")
            raise RuntimeError(f"编译失败: {error_msg[:500]}")

        self.compiled = True
        self.compile_failure_count = 0
        self.compile_blocked = False
        self._fatal_compile_error_sent = False
        self.last_compile_error = ""
        if self.use_native_main:
            self.native_main_input_mode = self._detect_native_main_input_mode(c_sources)
            self.log_info(f"native main 输入模式: {self.native_main_input_mode}")
        self.log_info(f"编译成功: {self.harness_path}")

    def _detect_native_main_input_mode(self, c_sources: list[Path]) -> str:
        """识别 native main 程序更可能的输入方式（stdin / argv_file / argv_text）。"""
        combined = ""
        for src in c_sources:
            try:
                combined += "\n" + src.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

        text = combined.lower()
        has_argv = "argv[1]" in text
        has_file_open = "fopen(" in text or "open(" in text
        has_stdin = any(token in text for token in ["getchar(", "fgets(", "stdin", "gets("])

        # 典型文件输入程序：主函数依赖 argv[1]，并出现文件打开调用。
        if has_argv and has_file_open and not has_stdin:
            return "argv_file"

        # 混合场景下优先判断常见的“无输入参数”提示。
        if has_argv and has_file_open and re.search(r"no\s+input\s+file|can't\s+open\s+file|usage", text):
            return "argv_file"

        # 命令行文本输入程序：存在 argv[1]，但无文件读取逻辑。
        # 对这类目标优先走 argv 文本参数，避免 stdin 随机字节长期卡在同一路径。
        if has_argv and not has_file_open:
            return "argv_text"

        return "stdin"

    def _with_harness_main_guard(self, code: str) -> str:
        """在 harness 源码前注入 main 宏保护，避免 -Dmain 重命名到 harness 入口。"""
        return (
            "#ifdef main\n"
            "#undef main\n"
            "#endif\n"
            f"{code.lstrip()}"
        )

    def _generate_harness(self) -> str:
        """根据被测项目生成测试驱动 C 代码。"""
        source_dir = Path(self.source_dir)
        target_name = source_dir.name.lower()

        has_tinyexpr_header = (source_dir / "tinyexpr.h").exists()
        has_fuzzgoat_header = (source_dir / "fuzzgoat.h").exists()
        has_cjson_header = (source_dir / "cJSON.h").exists()

        if target_name == "tinyexpr" or has_tinyexpr_header:
            return self._with_harness_main_guard(r'''
#include "tinyexpr.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char *argv[]) {
    char *input = NULL;
    size_t input_len = 0;

    if (argc > 1) {
        FILE *f = fopen(argv[1], "rb");
        if (!f) return 1;
        fseek(f, 0, SEEK_END);
        input_len = (size_t)ftell(f);
        fseek(f, 0, SEEK_SET);
        input = (char *)malloc(input_len + 1);
        if (!input) { fclose(f); return 1; }
        fread(input, 1, input_len, f);
        input[input_len] = '\0';
        fclose(f);
    } else {
        size_t capacity = 4096;
        input = (char *)malloc(capacity);
        if (!input) return 1;
        input_len = 0;
        int c;
        while ((c = getchar()) != EOF) {
            if (input_len + 1 >= capacity) {
                capacity *= 2;
                char *new_input = (char *)realloc(input, capacity);
                if (!new_input) { free(input); return 1; }
                input = new_input;
            }
            input[input_len++] = (char)c;
        }
        input[input_len] = '\0';
    }

    int err = 0;
    (void)te_interp(input, &err);
    int err2 = 0;
    te_expr *expr = te_compile(input, NULL, 0, &err2);
    if (expr) {
        (void)te_eval(expr);
        te_free(expr);
    }

    free(input);
    return 0;
}
''')

        if target_name == "fuzzgoat" or has_fuzzgoat_header:
            return self._with_harness_main_guard(r'''
#include "fuzzgoat.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char *argv[]) {
    char *input = NULL;
    size_t input_len = 0;

    if (argc > 1) {
        FILE *f = fopen(argv[1], "rb");
        if (!f) return 1;
        fseek(f, 0, SEEK_END);
        input_len = (size_t)ftell(f);
        fseek(f, 0, SEEK_SET);
        input = (char *)malloc(input_len + 1);
        if (!input) { fclose(f); return 1; }
        fread(input, 1, input_len, f);
        input[input_len] = '\0';
        fclose(f);
    } else {
        size_t capacity = 4096;
        input = (char *)malloc(capacity);
        if (!input) return 1;
        input_len = 0;
        int c;
        while ((c = getchar()) != EOF) {
            if (input_len + 1 >= capacity) {
                capacity *= 2;
                char *new_input = (char *)realloc(input, capacity);
                if (!new_input) { free(input); return 1; }
                input = new_input;
            }
            input[input_len++] = (char)c;
        }
        input[input_len] = '\0';
    }

    json_value *v1 = json_parse((const json_char *)input, input_len);
    if (v1) {
        json_value_free(v1);
    }

    json_settings settings;
    memset(&settings, 0, sizeof(settings));
    char error_buf[json_error_max] = {0};
    json_value *v2 = json_parse_ex(&settings, (const json_char *)input, input_len, error_buf);
    if (v2) {
        json_value_free_ex(&settings, v2);
    }

    free(input);
    return 0;
}
''')

        if has_cjson_header:
            return self._with_harness_main_guard(r'''
#include "cJSON.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char *argv[]) {
    char *input = NULL;
    size_t input_len = 0;

    if (argc > 1) {
        FILE *f = fopen(argv[1], "rb");
        if (!f) return 1;
        fseek(f, 0, SEEK_END);
        input_len = (size_t)ftell(f);
        fseek(f, 0, SEEK_SET);
        input = (char *)malloc(input_len + 1);
        if (!input) { fclose(f); return 1; }
        fread(input, 1, input_len, f);
        input[input_len] = '\0';
        fclose(f);
    } else {
        size_t capacity = 4096;
        input = (char *)malloc(capacity);
        if (!input) return 1;
        input_len = 0;
        int c;
        while ((c = getchar()) != EOF) {
            if (input_len + 1 >= capacity) {
                capacity *= 2;
                char *new_input = (char *)realloc(input, capacity);
                if (!new_input) { free(input); return 1; }
                input = new_input;
            }
            input[input_len++] = (char)c;
        }
        input[input_len] = '\0';
    }

    cJSON *json = cJSON_Parse(input);
    if (json) {
        char *printed = cJSON_Print(json);
        if (printed) free(printed);

        char *compact = cJSON_PrintUnformatted(json);
        if (compact) free(compact);

        (void)cJSON_GetArraySize(json);
        cJSON_IsObject(json);
        cJSON_IsArray(json);
        cJSON_IsString(json);
        cJSON_IsNumber(json);
        cJSON_IsBool(json);
        cJSON_IsNull(json);

        if (cJSON_IsObject(json) || cJSON_IsArray(json)) {
            cJSON *child = json->child;
            while (child) {
                if (cJSON_IsString(child)) (void)cJSON_GetStringValue(child);
                if (cJSON_IsNumber(child)) (void)cJSON_GetNumberValue(child);
                child = child->next;
            }
        }

        cJSON *dup = cJSON_Duplicate(json, 1);
        if (dup) {
            (void)cJSON_Compare(json, dup, 1);
            cJSON_Delete(dup);
        }

        cJSON_Delete(json);
    }

    free(input);
    return 0;
}
''')

        if self.selected_signature is not None:
            return self._generate_generic_function_harness(source_dir, self.selected_signature)

        # 通用兜底 harness（不假设具体函数）
        return self._with_harness_main_guard(r'''
#include <stdio.h>

int main(int argc, char *argv[]) {
    FILE *f = NULL;
    if (argc > 1) {
        f = fopen(argv[1], "rb");
        if (!f) return 1;
    } else {
        f = stdin;
    }

    int c;
    while ((c = fgetc(f)) != EOF) {
        volatile unsigned char b = (unsigned char)c;
        (void)b;
    }

    if (argc > 1 && f) {
        fclose(f);
    }
    return 0;
}
''')

    def _clean_work_dir(self):
        """清理上一次运行残留的覆盖率和中间产物，避免污染本轮结果。"""
        work_path = Path(self.work_dir)
        if not work_path.exists():
            return

        patterns = [
            "*.gcda",
            "*.gcno",
            "*.gcov",
            "input_*.dat",
            "harness.c",
            "harness.exe",
            "target_program.exe",
        ]
        for pattern in patterns:
            for path in work_path.rglob(pattern):
                try:
                    path.unlink()
                except Exception:
                    pass

    def _select_entry_signature(self) -> FunctionSignature | None:
        """自动挑选一个适合通用 harness 调用的目标函数。"""
        if self.analysis_result is None or not self.analysis_result.functions:
            return None

        functions = [
            f for f in self.analysis_result.functions
            if f.name and f.name != "main" and all("..." not in (p.type_str or "") for p in f.params)
        ]
        if not functions:
            return None

        if self.config.get("entry_function"):
            for func in functions:
                if func.name == self.config.get("entry_function"):
                    return func

        by_name = {f.name: f for f in functions}
        for name in self.analysis_result.high_risk_functions:
            func = by_name.get(name)
            if func is not None:
                return func

        ranked = sorted(
            functions,
            key=lambda f: (
                self._estimate_callability(f),
                -(f.param_count or 0),
                f.name,
            ),
            reverse=True,
        )
        return ranked[0] if ranked else None

    def _estimate_callability(self, signature: FunctionSignature) -> int:
        """估算函数是否适合自动构造参数并调用。"""
        score = 0
        if signature.param_count == 0:
            score += 10

        for param in signature.params:
            type_str = (param.type_str or "").lower()
            if "..." in type_str:
                score -= 20
                continue
            if "char" in type_str and "*" in type_str:
                score += 6
            elif any(token in type_str for token in ["int", "long", "short", "size_t", "float", "double", "bool"]):
                score += 4
            elif "*" in type_str:
                score += 1
            else:
                score += 2

        return score

    def _generate_generic_function_harness(self, source_dir: Path, signature: FunctionSignature) -> str:
        """为通用 C 项目生成自动调用单个目标函数的 harness。"""
        header_includes = []
        for header in sorted(source_dir.glob("*.h")):
            header_includes.append(f'#include "{header.name}"')

        if not header_includes and signature.header_file:
            header_name = os.path.basename(signature.header_file)
            if header_name.endswith(".h"):
                header_includes.append(f'#include "{header_name}"')

        prototype = ""
        if not header_includes:
            params = ", ".join(param.type_str for param in signature.params) if signature.params else "void"
            prototype = f"extern {signature.return_type} {signature.name}({params});\n\n"

        param_setup = []
        arg_names = []
        for index, param in enumerate(signature.params):
            safe_name = re.sub(r"\W+", "_", param.name or f"arg_{index}")
            if not safe_name or safe_name[0].isdigit():
                safe_name = f"arg_{index}"
            param_setup.append(self._generate_generic_param_setup(param, safe_name, index))
            arg_names.append(safe_name)

        call_args = ", ".join(arg_names)
        call_stmt = f"    {signature.name}({call_args});" if arg_names else f"    {signature.name}();"
        include_block = "\n".join(header_includes)
        param_block = "\n".join(param_setup)

        harness_code = f'''{include_block}
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <signal.h>

{prototype}static jmp_buf g_ctestagent_jump_env;
static volatile sig_atomic_t g_ctestagent_signal_code = 0;

static void ctestagent_signal_handler(int sig) {{
    g_ctestagent_signal_code = sig;
    longjmp(g_ctestagent_jump_env, 1);
}}

static long long read_signed_value(const unsigned char *data, size_t len, size_t offset) {{
    long long value = 0;
    size_t i = 0;
    for (i = 0; i < sizeof(value); ++i) {{
        size_t idx = offset + i;
        unsigned char byte = idx < len ? data[idx] : (unsigned char)(len & 0xFF);
        value |= ((long long)byte) << (i * 8);
    }}
    return value;
}}

static double read_double_value(const unsigned char *data, size_t len, size_t offset) {{
    long long raw = read_signed_value(data, len, offset);
    return (double)raw / 1024.0;
}}

int main(int argc, char *argv[]) {{
    const char *input_path = argc > 1 ? argv[1] : NULL;
    FILE *f = NULL;
    unsigned char *input_bytes = NULL;
    size_t input_len = 0;

    if (input_path != NULL) {{
        f = fopen(input_path, "rb");
    }}

    if (f != NULL) {{
        fseek(f, 0, SEEK_END);
        input_len = (size_t)ftell(f);
        fseek(f, 0, SEEK_SET);
        input_bytes = (unsigned char *)malloc(input_len + 1);
        if (input_bytes == NULL) {{
            fclose(f);
            return 1;
        }}
        fread(input_bytes, 1, input_len, f);
        input_bytes[input_len] = '\\0';
        fclose(f);
    }} else {{
        input_len = 0;
        input_bytes = (unsigned char *)malloc(1);
        if (input_bytes == NULL) {{
            return 1;
        }}
        input_bytes[0] = '\\0';
    }}

    char *input_text = (char *)input_bytes;
{param_block}

    g_ctestagent_signal_code = 0;
    int trapped_signal = 0;
    void (*old_sigsegv)(int) = signal(SIGSEGV, ctestagent_signal_handler);
    void (*old_sigabrt)(int) = signal(SIGABRT, ctestagent_signal_handler);
    void (*old_sigfpe)(int) = signal(SIGFPE, ctestagent_signal_handler);
    void (*old_sigill)(int) = signal(SIGILL, ctestagent_signal_handler);
#ifdef SIGBUS
    void (*old_sigbus)(int) = signal(SIGBUS, ctestagent_signal_handler);
#endif

    if (setjmp(g_ctestagent_jump_env) == 0) {{
{call_stmt}
    }} else {{
        trapped_signal = (int)g_ctestagent_signal_code;
    }}

    signal(SIGSEGV, old_sigsegv);
    signal(SIGABRT, old_sigabrt);
    signal(SIGFPE, old_sigfpe);
    signal(SIGILL, old_sigill);
#ifdef SIGBUS
    signal(SIGBUS, old_sigbus);
#endif

    free(input_bytes);

    if (trapped_signal > 0) {{
        return 128 + trapped_signal;
    }}

    return 0;
}}
'''

        return self._with_harness_main_guard(harness_code)

    def _generate_generic_param_setup(self, param: FunctionParameter, name: str, index: int) -> str:
        """根据参数类型生成通用参数映射代码。"""
        type_str = (param.type_str or "int").strip()
        decl_type = self._normalize_param_decl_type(type_str, param.name)
        lower_type = decl_type.lower()
        lower_name = (name or "").lower()
        offset = index * 8

        if "..." in lower_type:
            return f"    /* 可变参数暂不自动构造，使用占位值 */\n    void *{name} = NULL;"

        if "*" in lower_type:
            if "char" in lower_type:
                source = "input_path" if any(token in lower_name for token in ["file", "path", "name"]) else "input_text"
                return f"    {decl_type} {name} = ({decl_type}){source};"
            if "void" in lower_type:
                return f"    {decl_type} {name} = ({decl_type})input_bytes;"
            return f"    {decl_type} {name} = NULL;"

        if any(token in lower_type for token in ["double", "float"]):
            return f"    {decl_type} {name} = ({decl_type})read_double_value(input_bytes, input_len, {offset});"

        if "bool" in lower_type:
            return f"    {decl_type} {name} = ({decl_type})(input_len % 2);"

        if any(token in lower_type for token in ["int", "long", "short", "size_t", "ssize_t", "unsigned", "signed"]):
            value_expr = f"read_signed_value(input_bytes, input_len, {offset})"
            if "size_t" in lower_type:
                value_expr = f"(input_len > 0 ? input_len : (size_t){index + 1})"
            return f"    {decl_type} {name} = ({decl_type})({value_expr});"

        return f"    {decl_type} {name} = ({decl_type})0;"

    def _normalize_param_decl_type(self, type_str: str, param_name: str) -> str:
        """将“类型+参数名”格式归一成可直接声明变量的类型字符串。"""
        normalized = (type_str or "").strip()
        if not normalized:
            return "int"

        name = (param_name or "").strip()
        if name:
            # 仅移除末尾参数名，保留指针星号与 const 修饰。
            tail_pattern = rf"^(.*?)(\b{re.escape(name)}\b)(\s*\[[^\]]*\])?\s*$"
            match = re.match(tail_pattern, normalized)
            if match:
                candidate = (match.group(1) or "").rstrip()
                if candidate:
                    normalized = candidate

        return normalized

    def _execute_single(self, case: TestCase) -> ExecutionResult:
        """执行单个测试用例。"""
        result = ExecutionResult(test_case_id=case.id, test_case=case)

        input_file = os.path.join(self.work_dir, f"input_{case.id}.dat")
        try:
            input_data = case.input_data or case.input_text.encode("utf-8", errors="replace")
            with open(input_file, "wb") as f:
                f.write(input_data)
        except Exception as e:
            result.status = TestCaseStatus.ERROR
            result.stderr = f"写入输入文件失败: {e}"
            return result

        start_time = time.time()
        try:
            env = os.environ.copy()
            gcc_dir = os.path.dirname(self.gcc_path)
            if gcc_dir and gcc_dir not in env.get("PATH", ""):
                env["PATH"] = gcc_dir + os.pathsep + env.get("PATH", "")

            if self.enable_asan:
                env["ASAN_OPTIONS"] = "detect_leaks=0:abort_on_error=1:symbolize=1"
            if self.enable_gcov and os.name != "nt":
                env["GCOV_PREFIX"] = self.work_dir
                env["GCOV_PREFIX_STRIP"] = "99"

            if self.use_native_main:
                if self.native_main_input_mode == "argv_file":
                    proc = subprocess.run(
                        [self.harness_path, input_file],
                        capture_output=True,
                        timeout=self.timeout,
                        cwd=self.work_dir,
                        env=env,
                    )
                elif self.native_main_input_mode == "argv_text":
                    argv_text = (case.input_text or "").strip()
                    if not argv_text:
                        argv_text = (case.input_data or b"").decode("utf-8", errors="replace").strip()

                    # 命令行参数不允许 NUL，且过长参数容易触发平台限制。
                    argv_text = argv_text.replace("\x00", "")[:4096]
                    if not argv_text:
                        argv_text = "1 + 1"

                    proc = subprocess.run(
                        [self.harness_path, argv_text],
                        capture_output=True,
                        timeout=self.timeout,
                        cwd=self.work_dir,
                        env=env,
                    )
                else:
                    # 对 stdin 型 native main 程序通过标准输入传入测试数据。
                    proc = subprocess.run(
                        [self.harness_path],
                        input=input_data,
                        capture_output=True,
                        timeout=self.timeout,
                        cwd=self.work_dir,
                        env=env,
                    )
            else:
                proc = subprocess.run(
                    [self.harness_path, input_file],
                    capture_output=True,
                    timeout=self.timeout,
                    cwd=self.work_dir,
                    env=env,
                )

            result.execution_time = time.time() - start_time
            result.return_code = proc.returncode
            result.stdout = proc.stdout.decode("utf-8", errors="replace")[:2000]
            result.stderr = proc.stderr.decode("utf-8", errors="replace")[:5000]

            if proc.returncode == 0:
                result.status = TestCaseStatus.PASS
            elif proc.returncode < 0:
                result.status = TestCaseStatus.CRASH
                result.signal = -proc.returncode
            elif proc.returncode > 128:
                result.status = TestCaseStatus.CRASH
                result.signal = proc.returncode - 128
            else:
                if self._detect_asan_error(result.stderr):
                    result.status = TestCaseStatus.CRASH
                    result.asan_error = self._extract_asan_type(result.stderr)
                else:
                    result.status = TestCaseStatus.ERROR

        except subprocess.TimeoutExpired:
            result.execution_time = time.time() - start_time
            result.status = TestCaseStatus.TIMEOUT
        except Exception as e:
            result.execution_time = time.time() - start_time
            result.status = TestCaseStatus.ERROR
            result.stderr = str(e)

        # API 增强：仅在异常场景进行语义分类，帮助后续 Reviewer/Reporter 聚类。
        self._apply_api_execution_hints(result)

        if result.status == TestCaseStatus.CRASH:
            crash_dir = os.path.join(self.output_dir, "crashes")
            os.makedirs(crash_dir, exist_ok=True)
            crash_file = os.path.join(crash_dir, f"crash_{case.id}.dat")
            try:
                with open(input_file, "rb") as src:
                    with open(crash_file, "wb") as dst:
                        dst.write(src.read())
            except Exception:
                pass

        try:
            os.remove(input_file)
        except Exception:
            pass

        return result

    def _detect_asan_error(self, stderr: str) -> bool:
        asan_keywords = [
            "AddressSanitizer",
            "ERROR: AddressSanitizer",
            "heap-buffer-overflow",
            "stack-buffer-overflow",
            "heap-use-after-free",
            "stack-use-after-return",
            "SEGV",
        ]
        return any(kw in stderr for kw in asan_keywords)

    def _apply_api_execution_hints(self, result: ExecutionResult):
        """调用 API 对异常执行结果做语义 triage；失败时保持原始结果。"""
        if not self.api_advisor.enabled:
            return
        status_name = result.status.value.upper() if isinstance(result.status, TestCaseStatus) else str(result.status).upper()
        if status_name not in self.api_triage_statuses:
            return

        # 本地已可确定类型时不必再调用 API。
        if result.asan_error and result.asan_error != "unknown_asan_error":
            return
        if result.signal == 11:
            result.asan_error = result.asan_error or "segv"
            return
        if result.signal == 6:
            result.asan_error = result.asan_error or "abort"
            return
        if not (result.stderr or result.stdout):
            return

        if self._api_requests_in_batch >= self.api_max_requests_per_batch:
            if not self._api_limit_log_emitted:
                self.log_debug(
                    f"本批次 API triage 已达到上限({self.api_max_requests_per_batch})，其余用例走本地分类"
                )
                self._api_limit_log_emitted = True
            return
        self._api_requests_in_batch += 1

        input_preview = ""
        target_function = ""
        if result.test_case is not None:
            target_function = str(result.test_case.target_function or "")
            input_preview = (result.test_case.input_text or "")[:240]

        context = {
            "status": result.status.value,
            "return_code": int(result.return_code),
            "signal": result.signal,
            "asan_error": result.asan_error or "",
            "target_function": target_function,
            "stderr_preview": (result.stderr or "")[:800],
            "stdout_preview": (result.stdout or "")[:500],
            "input_preview": input_preview,
        }
        expected_schema = {
            "crash_type": "heap-buffer-overflow",
            "confidence": 0.8,
            "reason": "short explanation",
        }

        decision = self.api_advisor.ask_json(
            task="对当前执行异常进行语义分类，给出紧凑 triage 标签",
            context=context,
            expected_schema=expected_schema,
            max_tokens=300,
            temperature=0.1,
        )
        if not decision:
            return

        crash_type = str(decision.get("crash_type", "")).strip()
        if crash_type and (not result.asan_error or result.asan_error == "unknown_asan_error"):
            result.asan_error = crash_type

        if result.test_case is not None:
            confidence = decision.get("confidence", 0.0)
            try:
                confidence_value = max(0.0, min(1.0, float(confidence)))
            except Exception:
                confidence_value = 0.0

            result.test_case.metadata["api_crash_type"] = crash_type or result.asan_error or ""
            result.test_case.metadata["api_crash_confidence"] = confidence_value

        reason = str(decision.get("reason", "")).strip()
        if reason:
            self.log_debug(f"API 崩溃分类建议: {reason}")

    def _extract_asan_type(self, stderr: str) -> str:
        patterns = [
            r"ERROR: AddressSanitizer: (\S+)",
            r"(heap-buffer-overflow)",
            r"(stack-buffer-overflow)",
            r"(heap-use-after-free)",
            r"(use-after-poison)",
            r"(SEGV)",
        ]
        for pattern in patterns:
            match = re.search(pattern, stderr)
            if match:
                return match.group(1)
        return "unknown_asan_error"

    def _collect_coverage_snapshot(self) -> tuple[set[str], set[str], set[str]]:
        """收集 gcov 覆盖率快照（行覆盖 + 分支覆盖）。"""
        covered_lines: set[str] = set()
        covered_branches: set[str] = set()
        total_branches: set[str] = set()
        gcda_count = 0
        gcno_count = 0
        gcov_count = 0
        if not self.enable_gcov:
            return covered_lines, covered_branches, total_branches

        try:
            gcda_files = list(Path(self.work_dir).rglob("*.gcda"))
            gcda_count = len(gcda_files)
            if not gcda_files:
                all_files = list(Path(self.work_dir).rglob("*"))
                gcno_files = [f for f in all_files if f.suffix == ".gcno"]
                gcno_count = len(gcno_files)
                if gcno_files and not hasattr(self, "_gcda_warned"):
                    self._gcda_warned = True
                    self.log_warning(
                        f"发现 {len(gcno_files)} 个 .gcno 文件但无 .gcda 文件。gcno 示例: {gcno_files[0]}"
                    )
                self.log_info(
                    f"[OBS][gcov_snapshot] gcda={gcda_count}, gcno={gcno_count}, gcov={gcov_count}, "
                    f"covered_lines={len(covered_lines)}, covered_branches={len(covered_branches)}/{len(total_branches)}"
                )
                return covered_lines, covered_branches, total_branches

            gcov_env = os.environ.copy()
            gcc_dir = os.path.dirname(self.gcc_path)
            if gcc_dir:
                gcov_env["PATH"] = gcc_dir + os.pathsep + gcov_env.get("PATH", "")

            for gcda in gcda_files:
                subprocess.run(
                    [self.gcov_path, "-b", "-c", str(gcda)],
                    capture_output=True,
                    cwd=str(gcda.parent),
                    env=gcov_env,
                    timeout=30,
                )

            gcov_files = list(Path(self.work_dir).rglob("*.gcov"))
            gcov_count = len(gcov_files)
            for gcov_file in gcov_files:
                try:
                    with open(gcov_file, "r", errors="ignore") as f:
                        filename = gcov_file.stem
                        current_line_no = ""
                        for line in f:
                            parts = line.split(":", 2)
                            if len(parts) >= 2:
                                exec_count = parts[0].strip()
                                line_no = parts[1].strip()
                                if line_no.isdigit():
                                    current_line_no = line_no
                                    if exec_count not in ("-", "#####", "====="):
                                        count_match = re.match(r"^(\d+)", exec_count)
                                        if count_match is not None:
                                            covered_lines.add(f"{filename}:{line_no}")

                            branch_match = re.match(r"\s*branch\s+(\d+)\s+(.+)", line)
                            if not branch_match or not current_line_no:
                                continue

                            branch_index = branch_match.group(1)
                            branch_info = branch_match.group(2).strip().lower()
                            branch_id = f"{filename}:{current_line_no}:b{branch_index}"
                            total_branches.add(branch_id)

                            if "never executed" in branch_info:
                                continue

                            taken_match = re.search(r"taken\s+(-?\d+)", branch_info)
                            if taken_match:
                                try:
                                    if int(taken_match.group(1)) > 0:
                                        covered_branches.add(branch_id)
                                except Exception:
                                    pass
                except Exception:
                    pass

            if (covered_lines or total_branches) and not hasattr(self, "_coverage_logged"):
                self._coverage_logged = True
                self.log_info(
                    f"gcov 覆盖率: 行={len(covered_lines)}, 分支={len(covered_branches)}/{len(total_branches)} "
                    f"(gcda={len(gcda_files)}, gcov={len(gcov_files)})"
                )

            self.log_info(
                f"[OBS][gcov_snapshot] gcda={gcda_count}, gcno={gcno_count}, gcov={gcov_count}, "
                f"covered_lines={len(covered_lines)}, covered_branches={len(covered_branches)}/{len(total_branches)}"
            )

        except Exception as e:
            self.log_warning(f"覆盖率收集失败: {e}")

        return covered_lines, covered_branches, total_branches
