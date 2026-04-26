# reviewer.py - 评审者 Agent
"""
Reviewer Agent（评审者）：系统的"质检官"和"战略顾问"。⭐ 核心创新

三大职责：
1. 测试用例质量审查（执行前预筛选）
2. 执行结果交叉验证（崩溃复现验证）
3. 覆盖率缺口分析与战略建议
"""

import hashlib
import logging
import os
import re
from pathlib import Path
from difflib import SequenceMatcher

from core.base_agent import BaseAgent
from core.message_bus import MessageBus
from core.message_types import (
    Message, TestCase, ExecutionResult, TestCaseStatus,
    ReviewResult, ReviewFeedback, FeedbackType,
    ConfirmedCrash, VerifyResult, CoverageData
)

logger = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    """
    评审者智能体。

    独立审查测试过程，发现其他智能体的盲区和错误：
    - 预筛选低质量用例，避免浪费执行时间
    - 交叉验证崩溃结果，减少误报
    - 分析覆盖率缺口，指导 Generator 定向生成
    """

    def __init__(self, bus: MessageBus, config: dict = None):  # type: ignore
        super().__init__("Reviewer", bus, config)

        # 审查参数
        self.approval_threshold = config.get("approval_threshold", 0.4) if config else 0.4
        self.crash_verify_count = config.get("crash_verify_count", 3) if config else 3
        self.diversity_weight = config.get("diversity_weight", 0.3) if config else 0.3
        self.coverage_weight = config.get("coverage_weight", 0.4) if config else 0.4
        self.validity_weight = config.get("validity_weight", 0.3) if config else 0.3
        self.max_new_crashes_per_round = int(config.get("max_new_crashes_per_round", 12)) if config else 12
        self.api_review_interval = max(1, int((config or {}).get("api_review_interval", 3)))
        self._review_batch_count = 0
        self._obs_signature_log_count = 0

        # 历史记录
        self.executed_inputs: list[str] = []     # 已执行用例的输入指纹
        self.known_crash_hashes: set[str] = set()
        self.crash_occurrence_counter: dict[str, int] = {}
        self.coverage_data = CoverageData()

        # 被测项目上下文
        self.source_dir = str(config.get("source_dir", "")) if config else ""
        self.entry_function = str(config.get("entry_function", "")) if config else ""
        self.is_json_like_target = self._detect_json_like_target()
        self.is_expression_cli_target = self._detect_expression_cli_target()

        # 执行器引用（用于崩溃复现）
        self.executor_harness = None
        self.executor_work_dir = None

    def setup(self):
        """注册消息订阅"""
        self.subscribe("data:test_cases", self.on_test_cases)
        self.subscribe("data:execution_results", self.on_execution_results)
        self.subscribe("data:coverage_update", self.on_coverage_update)

    def on_test_cases(self, msg: Message):
        """接收 Generator 生成的测试用例，进行预筛选"""
        cases = msg.data
        if not isinstance(cases, list):
            return

        self._review_batch_count += 1

        self.log_info(f"开始审查 {len(cases)} 个测试用例")
        review_result = self.review_test_cases(cases)
        self._apply_api_review_hints(cases, review_result)

        suggestion_types = [s.type.value for s in review_result.suggestions]
        self.log_info(
            f"[OBS][review] batch={self._review_batch_count}, approved={len(review_result.approved)}, "
            f"rejected={len(review_result.rejected)}, suggestions={suggestion_types}"
        )

        self.log_info(
            f"审查完成: 通过 {len(review_result.approved)}/{len(cases)}, "
            f"拒绝 {len(review_result.rejected)}"
        )

        # 发布通过审查的用例给 Executor
        if review_result.approved:
            self.publish("data:approved_cases", review_result.approved)

        # 发布反馈给 Generator
        for suggestion in review_result.suggestions:
            self.publish("feedback:review", suggestion)

    def on_execution_results(self, msg: Message):
        """接收 Executor 的执行结果，进行交叉验证"""
        results = msg.data
        if not isinstance(results, list):
            return

        # 提取崩溃用例
        crashes = [r for r in results if isinstance(r, ExecutionResult)
                   and r.status == TestCaseStatus.CRASH]

        if crashes:
            self.log_info(f"开始交叉验证 {len(crashes)} 个崩溃")
            verify_result = self.verify_crashes(crashes)

            self.log_info(
                f"验证完成: 确认 {len(verify_result.confirmed)}, "
                f"误报 {len(verify_result.false_positives)}"
            )

            # 发布确认的崩溃
            if verify_result.confirmed:
                self.publish("data:verified_crashes", verify_result.confirmed)

            # 发布验证统计（供 Reporter 计算误报）
            self.publish("data:verify_stats", {
                "confirmed": len(verify_result.confirmed),
                "false_positives": len(verify_result.false_positives),
                "total_crashes": len(crashes),
            })

        # 更新覆盖率数据并分析缺口
        for r in results:
            if isinstance(r, ExecutionResult) and r.coverage_lines:
                self.coverage_data.covered_lines.update(r.coverage_lines)
                if r.branch_total:
                    self.coverage_data.branch_total.update(r.branch_total)
                if r.branch_covered:
                    self.coverage_data.branch_covered.update(r.branch_covered)

        self.log_info(
            f"[OBS][execution_results] total_results={len(results)}, crashes={len(crashes)}, "
            f"covered_lines={len(self.coverage_data.covered_lines)}, total_lines={len(self.coverage_data.total_lines)}, "
            f"history_len={len(self.coverage_data.coverage_history)}, "
            f"branch_covered={len(self.coverage_data.branch_covered)}/{len(self.coverage_data.branch_total)}"
        )

        # 生成覆盖率分析反馈
        feedbacks = self.analyze_coverage_gaps()
        feedback_types = [fb.type.value for fb in feedbacks]
        self.log_info(f"[OBS][feedback_emit] count={len(feedbacks)}, types={feedback_types}")
        for fb in feedbacks:
            self.publish("feedback:review", fb)

        # 生成策略建议给 Coordinator
        if feedbacks:
            self.publish("feedback:strategy", feedbacks)

    def on_coverage_update(self, msg: Message):
        """接收覆盖率增量种子通知（仅观测，不推进 history/total_lines）"""
        data = msg.data
        if isinstance(data, list):
            interesting = [tc for tc in data if isinstance(tc, TestCase)]
            self.log_info(
                f"[OBS][coverage_update_recv] interesting_cases={len(interesting)}, "
                f"covered_lines_now={len(self.coverage_data.covered_lines)}, "
                f"total_lines_now={len(self.coverage_data.total_lines)}, "
                f"history_len={len(self.coverage_data.coverage_history)}"
            )
            return

        self.log_info(
            f"[OBS][coverage_update_recv] unexpected_payload_type={type(data).__name__}"
        )

    # ================================================================
    # 职责 1：测试用例质量审查
    # ================================================================

    def review_test_cases(self, cases: list[TestCase]) -> ReviewResult:
        """对测试用例进行预筛选"""
        approved = []
        rejected = []
        suggestions = []

        for case in cases:
            # 检查 1：去重
            if self._is_duplicate(case):
                rejected.append((case, "DUPLICATE"))
                continue

            # 检查 2：有效性
            validity = self._assess_validity(case)

            # 检查 3：预期覆盖估算
            estimated_coverage = self._estimate_coverage(case)

            # 检查 4：多样性
            diversity = self._compute_diversity(case, approved)

            # 综合评分
            total_score = (
                validity * self.validity_weight +
                estimated_coverage * self.coverage_weight +
                diversity * self.diversity_weight
            )

            if total_score >= self.approval_threshold:
                approved.append(case)
                # 记录输入指纹
                self.executed_inputs.append(self._input_fingerprint(case))
            else:
                rejected.append((case, f"LOW_SCORE:{total_score:.2f}"))

        # 生成建议
        if len(cases) > 0 and len(approved) / len(cases) < 0.3:
            suggestions.append(ReviewFeedback(
                type=FeedbackType.LOW_DIVERSITY,
                message=f"用例通过率过低({len(approved)}/{len(cases)}), 建议增大变异力度"
            ))

        # 控制历史记录大小
        if len(self.executed_inputs) > 5000:
            self.executed_inputs = self.executed_inputs[-3000:]

        return ReviewResult(approved, rejected, suggestions)

    def _is_duplicate(self, case: TestCase) -> bool:
        """检查是否与已有用例过于相似"""
        fingerprint = self._input_fingerprint(case)

        # 精确匹配
        if fingerprint in self.executed_inputs:
            return True

        # 相似度检查（抽样比较）
        if len(self.executed_inputs) > 0:
            sample_size = min(50, len(self.executed_inputs))
            import random
            samples = random.sample(self.executed_inputs, sample_size)
            for fp in samples:
                if self._similarity(fingerprint, fp) > 0.95:
                    return True

        return False

    def _assess_validity(self, case: TestCase) -> float:
        """评估用例的有效性（0~1）"""
        if not self.is_json_like_target:
            if self.is_expression_cli_target:
                text = case.input_text or (case.input_data or b"").decode("utf-8", errors="replace")
                stripped = text.strip()
                if not stripped:
                    return 0.2

                score = 0.35
                printable_ratio = sum(1 for c in stripped if c.isprintable()) / max(len(stripped), 1)
                if printable_ratio >= 0.9:
                    score += 0.25
                elif printable_ratio < 0.6:
                    score -= 0.15

                if re.search(r"[-+]?\d+\s*[+\-*/]\s*[-+]?\d+", stripped):
                    score += 0.3

                if any(op in stripped for op in ["+", "-", "*", "/"]):
                    score += 0.1

                if len(stripped) <= 256:
                    score += 0.1

                if "\x00" in text:
                    score -= 0.2

                return max(0.0, min(score, 1.0))

            payload = case.input_data or (case.input_text or "").encode("utf-8", errors="replace")
            if not payload:
                return 0.2

            score = 0.6
            length = len(payload)
            if 1 <= length <= 4096:
                score += 0.2

            unique_ratio = len(set(payload)) / max(length, 1)
            score += min(0.2, unique_ratio * 0.25)
            return min(score, 1.0)

        text = case.input_text or ""

        # 空输入得低分但不是0（可以测试边界）
        if not text:
            return 0.3

        # 检查是否像合法 JSON
        score = 0.5  # 基础分

        # 包含 JSON 特征字符加分
        json_chars = {'{', '}', '[', ']', '"', ':', ','}
        json_char_count = sum(1 for c in text if c in json_chars)
        if json_char_count > 0:
            score += 0.2 * min(json_char_count / max(len(text), 1) * 5, 1.0)

        # 长度合理加分
        if 1 <= len(text) <= 10000:
            score += 0.2

        # 完全是乱码减分
        printable_ratio = sum(1 for c in text if c.isprintable()) / max(len(text), 1)
        score += 0.1 * printable_ratio

        return min(score, 1.0)

    def _estimate_coverage(self, case: TestCase) -> float:
        """估算用例可能触发的覆盖（0~1）"""
        if not self.is_json_like_target:
            if self.is_expression_cli_target:
                text = case.input_text or (case.input_data or b"").decode("utf-8", errors="replace")
                stripped = text.strip()
                if not stripped:
                    return 0.2

                score = 0.25

                if re.search(r"[-+]?\d+\s*[+\-*/]\s*[-+]?\d+", stripped):
                    score += 0.4

                if "/" in stripped:
                    score += 0.1

                if any(token in stripped for token in ["2147483647", "-2147483648", "999999999", "0"]):
                    score += 0.15

                if re.search(r"%[xndsp]", stripped.lower()):
                    score += 0.1

                if len(stripped) > 64:
                    score += 0.05

                return min(score, 1.0)

            payload = case.input_data or (case.input_text or "").encode("utf-8", errors="replace")
            if not payload:
                return 0.2

            score = 0.55
            length = len(payload)
            if 8 <= length <= 2048:
                score += 0.2
            elif length > 2048:
                score += 0.1

            # 文件型目标通常依赖特征头，前几个字节多样性越高越可能穿透分支。
            head = payload[:16]
            head_div = len(set(head)) / max(len(head), 1)
            score += min(0.25, head_div * 0.25)
            return min(score, 1.0)

        text = case.input_text or ""

        score = 0.5  # 基础分

        # 包含多种 JSON 特性加分
        features = [
            '"' in text,               # 字符串
            any(c.isdigit() for c in text),  # 数字
            'null' in text,            # null
            'true' in text or 'false' in text,  # 布尔
            '[' in text,               # 数组
            '{' in text,               # 对象
            '\\' in text,              # 转义
        ]
        feature_ratio = sum(features) / len(features)
        score += feature_ratio * 0.3

        # 嵌套深度加分（更深的嵌套覆盖更多路径）
        nesting = text.count('{') + text.count('[')
        if nesting > 1:
            score += min(nesting / 10, 0.2)

        return min(score, 1.0)

    def _compute_diversity(self, case: TestCase, current_batch: list[TestCase]) -> float:
        """计算与当前批次的差异度（0~1）"""
        if not current_batch:
            return 1.0  # 第一个用例多样性最高

        fingerprint = self._input_fingerprint(case)

        # 与当前批次中的用例比较相似度
        max_sim = 0.0
        sample = current_batch[-20:] if len(current_batch) > 20 else current_batch
        for other in sample:
            other_fp = self._input_fingerprint(other)
            sim = self._similarity(fingerprint, other_fp)
            max_sim = max(max_sim, sim)

        # 多样性 = 1 - 最大相似度
        return 1.0 - max_sim

    def _input_fingerprint(self, case: TestCase) -> str:
        """计算输入的指纹"""
        text = case.input_text or case.input_data.decode('utf-8', errors='replace')
        # 使用 MD5 的前 16 位作为指纹
        return hashlib.md5(text.encode('utf-8', errors='replace')).hexdigest()[:16]

    def _similarity(self, fp1: str, fp2: str) -> float:
        """计算两个指纹的相似度"""
        if fp1 == fp2:
            return 1.0
        return SequenceMatcher(None, fp1, fp2).ratio()

    # ================================================================
    # 职责 2：执行结果交叉验证
    # ================================================================

    def verify_crashes(self, crashes: list[ExecutionResult]) -> VerifyResult:
        """对崩溃进行交叉验证"""
        confirmed = []
        false_positives = []

        grouped: dict[str, list[ExecutionResult]] = {}
        for crash in crashes:
            stack_hash = self._build_crash_signature(crash)
            grouped.setdefault(stack_hash, []).append(crash)

        if self.max_new_crashes_per_round > 0 and len(grouped) > self.max_new_crashes_per_round:
            ordered_keys = sorted(
                grouped.keys(),
                key=lambda key: self._assess_severity(grouped[key][0]),
                reverse=True,
            )
            grouped = {k: grouped[k] for k in ordered_keys[: self.max_new_crashes_per_round]}

        for stack_hash, crash_group in grouped.items():
            representative = crash_group[0]
            self.crash_occurrence_counter[stack_hash] = self.crash_occurrence_counter.get(stack_hash, 0) + len(crash_group)
            is_unique = stack_hash not in self.known_crash_hashes

            if not is_unique:
                # 重复崩溃，不需要再验证
                continue

            # 验证 2：评估崩溃严重性
            severity = self._assess_severity(representative)

            if severity > 0:
                crash_type = self._classify_crash_type(representative)
                source_loc = self._extract_source_location(representative.stderr or "")
                risk_level, risk_summary = self._assess_risk(crash_type, representative, source_loc)

                sample_inputs = []
                for item in crash_group[:3]:
                    if item.test_case and item.test_case.input_text:
                        sample_inputs.append(item.test_case.input_text[:200])

                self.known_crash_hashes.add(stack_hash)
                confirmed.append(ConfirmedCrash(
                    crash=representative,
                    reproducibility=1.0,  # 简化：首次发现认为可复现
                    sensitivity=0.5,
                    stack_hash=stack_hash,
                    occurrence_count=self.crash_occurrence_counter.get(stack_hash, len(crash_group)),
                    crash_type=crash_type,
                    source_location=source_loc,
                    risk_level=risk_level,
                    risk_summary=risk_summary,
                    metadata={
                        "sample_inputs": sample_inputs,
                        "group_size": len(crash_group),
                    },
                ))
            else:
                false_positives.extend(crash_group)

        return VerifyResult(confirmed, false_positives)

    def _hash_stack_trace(self, stderr: str) -> str:
        """对调用栈进行哈希去重"""
        # 提取关键信息进行哈希
        lines = stderr.split('\n')
        key_lines = []
        for line in lines:
            # 保留包含函数名和地址的行
            if '#' in line or 'ERROR' in line or 'at ' in line:
                key_lines.append(line.strip())

        if not key_lines:
            # 如果没有典型的调用栈，使用整个 stderr
            key_lines = [stderr[:500]]

        hash_input = '\n'.join(key_lines)
        return hashlib.md5(hash_input.encode('utf-8', errors='replace')).hexdigest()[:12]

    def _build_crash_signature(self, crash: ExecutionResult) -> str:
        """构建崩溃签名：同一类型+同一位置+同一栈视为同一缺陷。"""
        # 1) 基础栈哈希
        stack_hash = self._hash_stack_trace(crash.stderr or "")

        # 2) 崩溃类型粗分类
        crash_type = self._classify_crash_type(crash)

        # 3) 源码定位（若有）
        src_loc = self._extract_source_location(crash.stderr or "")

        # 4) 组合（不再使用输入哈希，避免同一缺陷重复上报）
        parts = [
            crash_type,
            str(crash.signal or 0),
            str(crash.return_code),
            src_loc or "",
            stack_hash,
        ]

        sig = "|".join(parts)
        if self._obs_signature_log_count < 80:
            self.log_info(
                f"[OBS][crash_signature] crash_type={crash_type}, rc={crash.return_code}, "
                f"signal={crash.signal}, src='{src_loc}', stack_hash={stack_hash[:8]}"
            )
            self._obs_signature_log_count += 1
        return hashlib.md5(sig.encode('utf-8', errors='replace')).hexdigest()[:12]

    def _assess_risk(self, crash_type: str, crash: ExecutionResult, source_loc: str) -> tuple[str, str]:
        """输出风险等级与简短风险说明。"""
        token = (crash_type or "").lower()

        if any(x in token for x in ["heap-buffer-overflow", "stack-buffer-overflow", "use_after_free", "use-after-free", "invalid_free"]):
            level = "HIGH"
            summary = "可能导致内存破坏、进程崩溃，严重时可能被利用执行任意代码"
        elif "null_deref" in token or crash.signal in {11, 6}:
            level = "MEDIUM"
            summary = "可能导致拒绝服务（进程异常退出），影响服务可用性"
        elif crash.return_code != 0:
            level = "MEDIUM"
            summary = "异常退出路径可被输入触发，可能造成业务中断或错误结果"
        else:
            level = "LOW"
            summary = "存在异常执行路径，建议继续验证触发条件与影响范围"

        if source_loc:
            summary = f"{summary}（位置: {source_loc}）"
        return level, summary

    def _extract_source_location(self, stderr: str) -> str:
        """从 stderr 提取源码定位（file.c:line）"""
        m = re.search(r'([\w\-./\\]+\.c):(\d+)', stderr)
        if m:
            return f"{os.path.basename(m.group(1))}:{m.group(2)}"
        return ""

    def _classify_crash_type(self, crash: ExecutionResult) -> str:
        """崩溃类型粗分类"""
        stderr = (crash.stderr or "").lower()
        if crash.asan_error:
            return crash.asan_error
        if "use after free" in stderr or "use-after-free" in stderr:
            return "use_after_free"
        if "null" in stderr and "dereference" in stderr:
            return "null_deref"
        if crash.signal == 11:
            return "segv"
        if crash.signal == 6:
            return "abort"
        if crash.return_code != 0:
            return f"nonzero_rc_{crash.return_code}"
        return "crash"

    def _assess_severity(self, crash: ExecutionResult) -> float:
        """评估崩溃的严重性（0~1）"""
        score = 0.0

        # 被信号终止
        if crash.signal:
            score += 0.5
            if crash.signal == 11:  # SIGSEGV
                score += 0.3
            elif crash.signal == 6:  # SIGABRT
                score += 0.2

        # ASan 错误
        if crash.asan_error:
            score += 0.5
            if "heap-buffer-overflow" in (crash.asan_error or ""):
                score += 0.3
            elif "use-after-free" in (crash.asan_error or ""):
                score += 0.3

        # 有错误输出
        if crash.stderr:
            score += 0.1

        # 非零退出码（即便无 signal/stderr）
        if crash.return_code != 0:
            score += 0.3

        return min(score, 1.0)

    # ================================================================
    # 职责 3：覆盖率缺口分析
    # ================================================================

    def analyze_coverage_gaps(self) -> list[ReviewFeedback]:
        """分析覆盖率缺口并生成战略建议"""
        feedbacks = []

        # 检测覆盖率增长是否停滞
        history_len = len(self.coverage_data.coverage_history)
        growth_rate = self.coverage_data.recent_growth_rate(last_n=200)
        uncovered = self.coverage_data.get_uncovered_lines()
        self.log_info(
            f"[OBS][coverage_gap_input] history_len={history_len}, growth_rate={growth_rate:.6f}, "
            f"covered_lines={len(self.coverage_data.covered_lines)}, total_lines={len(self.coverage_data.total_lines)}, "
            f"uncovered={len(uncovered)}"
        )

        if len(self.coverage_data.coverage_history) > 50 and growth_rate < 0.001:
            feedbacks.append(ReviewFeedback(
                type=FeedbackType.STAGNATION,
                message="覆盖率增长停滞，建议切换策略或增加变异多样性"
            ))

        # 找出未覆盖的区域
        if len(uncovered) > 10:
            feedbacks.append(ReviewFeedback(
                type=FeedbackType.COVERAGE_GAP,
                uncovered_lines=uncovered[:100],  # 最多反馈 100 行
                message=f"发现 {len(uncovered)} 行代码未覆盖，建议定向生成"
            ))

        return feedbacks

    def _apply_api_review_hints(self, cases: list[TestCase], review_result: ReviewResult):
        """调用 API 提供复审建议，作为现有规则评审的补充层。"""
        if not self.api_advisor.enabled:
            return
        if not cases:
            return
        if self._review_batch_count % self.api_review_interval != 0:
            return

        rejected_reasons = [str(reason) for _, reason in review_result.rejected[:30]]
        context = {
            "approval_threshold": round(float(self.approval_threshold), 4),
            "total_cases": len(cases),
            "approved_cases": len(review_result.approved),
            "rejected_cases": len(review_result.rejected),
            "approval_rate": round(len(review_result.approved) / max(len(cases), 1), 4),
            "recent_reject_reasons": rejected_reasons,
            "known_crash_hashes": len(self.known_crash_hashes),
            "covered_lines": len(self.coverage_data.covered_lines),
        }
        expected_schema = {
            "threshold_delta": -0.05,
            "extra_feedback": [
                {
                    "type": "LOW_DIVERSITY",
                    "message": "short suggestion",
                    "promising_functions": ["func_a"],
                }
            ],
            "reason": "short explanation",
        }

        decision = self.api_advisor.ask_json(
            task="基于当前审查结果给出阈值微调与补充反馈建议",
            context=context,
            expected_schema=expected_schema,
            max_tokens=450,
            temperature=0.15,
        )
        if not decision:
            return

        delta = decision.get("threshold_delta", 0.0)
        try:
            delta_value = max(-0.2, min(0.2, float(delta)))
        except Exception:
            delta_value = 0.0

        if delta_value != 0.0:
            self.approval_threshold = max(0.1, min(0.9, self.approval_threshold + delta_value))

        extra_feedback = decision.get("extra_feedback", [])
        if isinstance(extra_feedback, list):
            for item in extra_feedback:
                if not isinstance(item, dict):
                    continue
                feedback_type = self._parse_feedback_type(str(item.get("type", "")))
                if feedback_type is None:
                    continue
                message = str(item.get("message", "")).strip()
                promising = [str(x).strip() for x in item.get("promising_functions", []) if str(x).strip()]
                uncovered = [str(x).strip() for x in item.get("uncovered_lines", []) if str(x).strip()]

                review_result.suggestions.append(ReviewFeedback(
                    type=feedback_type,
                    message=message,
                    promising_functions=promising[:10],
                    uncovered_lines=uncovered[:100],
                    data={"source": "api"},
                ))

        reason = str(decision.get("reason", "")).strip()
        if reason:
            self.log_info(f"API 复审建议已应用: {reason}")

    @staticmethod
    def _parse_feedback_type(name: str) -> FeedbackType | None:
        """将 API 返回的字符串解析为 FeedbackType。"""
        token = str(name or "").strip().upper()
        if not token:
            return None

        for feedback_type in FeedbackType:
            if token in {feedback_type.name.upper(), str(feedback_type.value).upper()}:
                return feedback_type
        return None

    def _detect_json_like_target(self) -> bool:
        """粗略判断当前目标是否以 JSON 解析为主。"""
        source_token = self.source_dir.lower()
        entry_token = self.entry_function.lower()
        return (
            "json" in source_token or
            "cjson" in source_token or
            "json" in entry_token
        )

    def _detect_expression_cli_target(self) -> bool:
        """识别命令行表达式解析类目标（如 vuln_calc）。"""
        source_token = self.source_dir.lower()
        entry_token = self.entry_function.lower()

        if any(token in source_token for token in ["calc", "expression"]) or "process_input" in entry_token:
            return True

        source_path = Path(self.source_dir)
        if not source_path.exists() or not source_path.is_dir():
            return False

        for c_file in source_path.glob("*.c"):
            try:
                content = c_file.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                continue

            if (
                "sscanf(" in content
                and "%c" in content
                and any(token in content for token in ["argv[1]", "gets(", "fgets("])
            ):
                return True

            if "calculate(" in content and "process_input(" in content:
                return True

        return False
