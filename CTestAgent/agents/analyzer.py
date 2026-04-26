# analyzer.py - 分析者 Agent
"""
Analyzer Agent（分析者）：系统的"侦察兵"。

在测试开始前对被测代码进行深度分析，为其他智能体提供情报：
1. 函数签名提取：解析 C 头文件，提取公开 API
2. 风险热点标注：根据代码复杂度、指针操作、危险函数等标注高风险函数
3. 参数约束推断：分析参数类型推断有效值域
4. 调用图构建：分析函数调用关系
"""

import os
import re
import logging
from pathlib import Path

from core.base_agent import BaseAgent
from core.message_bus import MessageBus
from core.message_types import (
    AnalysisResult, FunctionSignature, FunctionParameter,
    FunctionInfo, ValueRange, Message
)

logger = logging.getLogger(__name__)

# 危险函数列表（可能导致内存错误）
UNSAFE_FUNCTIONS = {
    'malloc', 'calloc', 'realloc', 'free',
    'strcpy', 'strncpy', 'strcat', 'strncat',
    'sprintf', 'snprintf', 'vsprintf',
    'memcpy', 'memmove', 'memset',
    'gets', 'fgets', 'scanf', 'fscanf',
    'atoi', 'atof', 'atol',
    'strlen',  # 对 NULL 指针调用会崩溃
}


class AnalyzerAgent(BaseAgent):
    """
    分析者智能体。

    负责在测试前对被测 C 项目进行静态分析，
    提取函数签名、评估风险、推断参数约束。
    """

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__("Analyzer", bus, config or {})
        self.source_dir = (config or {}).get("source_dir", "./targets/cjson")
        self.analysis_result = None

    def setup(self):
        """注册消息订阅"""
        self.subscribe("cmd:start_analysis", self.on_start_analysis)

    def on_start_analysis(self, msg: Message):
        """收到分析指令后，开始分析"""
        self.log_info("开始分析被测代码...")

        try:
            result = self.analyze()
            self.analysis_result = result
            self.log_info(
                f"分析完成: {len(result.functions)} 个函数, "
                f"{len(result.high_risk_functions)} 个高风险函数, "
                f"{result.total_lines} 行代码"
            )
            # 发布分析结果
            self.publish("data:analysis_result", result)
        except Exception as e:
            self.log_error(f"分析失败: {e}")
            raise

    def analyze(self) -> AnalysisResult:
        """执行完整的静态分析"""
        result = AnalysisResult()
        seen_function_names: set[str] = set()

        # 收集源文件
        source_dir = Path(self.source_dir)
        for f in source_dir.glob("*.h"):
            result.header_files.append(str(f))
        for f in source_dir.glob("*.c"):
            result.source_files.append(str(f))

        # 1. 从头文件提取函数签名
        for header in result.header_files:
            functions = self._parse_header(header)
            for func in functions:
                if func.name in seen_function_names:
                    continue
                result.functions.append(func)
                seen_function_names.add(func.name)

        # 1.1 从 .c 定义中补充提取可调用函数，提升通用 C 项目的适配能力
        for src in result.source_files:
            functions = self._parse_source_definitions(src)
            for func in functions:
                if func.name in seen_function_names:
                    continue
                result.functions.append(func)
                seen_function_names.add(func.name)

        # 2. 分析源文件，构建函数详细信息
        source_content = {}
        total_lines = 0
        for src in result.source_files:
            with open(src, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                source_content[src] = content
                total_lines += content.count('\n')
        result.total_lines = total_lines
        result.static_findings = self._scan_static_findings(source_content)

        # 3. 为每个函数构建详细信息并计算风险评分
        for func_sig in result.functions:
            func_info = self._analyze_function(func_sig, source_content)
            result.function_infos[func_sig.name] = func_info
            risk_score = self._compute_risk_score(func_info)
            result.risk_scores[func_sig.name] = risk_score

        # 4. 确定高风险函数（风险分 > 0.5）
        result.high_risk_functions = [
            name for name, score in result.risk_scores.items()
            if score >= 0.5
        ]
        # 按风险分降序排序
        result.high_risk_functions.sort(
            key=lambda n: result.risk_scores.get(n, 0), reverse=True
        )

        # API 增强：基于语义理解对高风险函数排序做微调。
        self._apply_api_analysis_hints(result)

        # 5. 构建调用图
        result.call_graph = self._build_call_graph(source_content, result.functions)

        # 6. 推断参数约束
        result.param_constraints = self._infer_constraints(result.functions)

        return result

    def _strip_comments_for_scan(self, line_text: str, in_block_comment: bool) -> tuple[str, bool]:
        """移除注释与字符串字面量，避免静态规则命中无效文本。"""
        output: list[str] = []
        i = 0
        in_string: str | None = None

        while i < len(line_text):
            ch = line_text[i]
            nxt = line_text[i + 1] if i + 1 < len(line_text) else ""

            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue

            if in_string is not None:
                # 字符串内容用空格占位，避免误命中关键字。
                if ch == "\\" and i + 1 < len(line_text):
                    output.append(" ")
                    output.append(" ")
                    i += 2
                    continue
                if ch == in_string:
                    in_string = None
                output.append(" ")
                i += 1
                continue

            if ch in {'"', "'"}:
                in_string = ch
                output.append(" ")
                i += 1
                continue

            if ch == "/" and nxt == "/":
                break

            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue

            output.append(ch)
            i += 1

        return "".join(output), in_block_comment

    def _scan_static_findings(self, source_content: dict[str, str]) -> list[dict]:
        """轻量级静态漏洞扫描：输出可定位的问题清单。"""
        findings: list[dict] = []

        pattern_specs = [
            {
                "type": "UNBOUNDED_INPUT",
                "severity": "HIGH",
                "regex": r"\bgets\s*\(",
                "description": "使用 gets 读取输入且无长度限制，容易触发栈缓冲区溢出。",
                "risk": "可能导致内存破坏、程序崩溃，严重时可能被利用执行任意代码。",
                "recommendation": "改用 fgets 并显式传入缓冲区长度。",
            },
            {
                "type": "UNSAFE_COPY",
                "severity": "HIGH",
                "regex": r"\bstrcpy\s*\(",
                "description": "检测到 strcpy，若目标缓冲区长度不足会造成溢出。",
                "risk": "可能导致内存越界写入和控制流破坏。",
                "recommendation": "改用 strncpy/strlcpy，并在复制前检查目标缓冲区长度。",
            },
            {
                "type": "UNSAFE_FORMAT",
                "severity": "HIGH",
                "regex": r"\b(v?sprintf)\s*\(",
                "description": "检测到 sprintf/vsprintf，缺少长度约束。",
                "risk": "可能导致缓冲区溢出并触发崩溃或任意代码执行。",
                "recommendation": "使用 snprintf/vsnprintf 并限制最大输出长度。",
            },
            {
                "type": "FORMAT_STRING_RISK",
                "severity": "HIGH",
                "regex": r"\bprintf\s*\(\s*[A-Za-z_]\w*\s*\)",
                "description": "printf 直接使用变量作为格式字符串，存在格式化字符串漏洞风险。",
                "risk": "可导致信息泄露、内存写入和潜在远程利用。",
                "recommendation": "使用固定格式字符串，如 printf(\"%s\", user_input)。",
            },
            {
                "type": "UNBOUNDED_SCANF",
                "severity": "MEDIUM",
                "regex": r"\bscanf\s*\(\s*\"[^\"]*%s",
                "description": "scanf 使用 %s 且未设置宽度，可能写越界。",
                "risk": "输入过长时可能导致缓冲区溢出和拒绝服务。",
                "recommendation": "为 %s 指定最大宽度，例如 %31s。",
            },
            {
                "type": "DOUBLE_FREE_PATTERN",
                "severity": "HIGH",
                "regex": r"\bfree\s*\(\s*([A-Za-z_]\w*)\s*\)",
                "description": "同一变量可能被重复 free，存在 double free 风险。",
                "risk": "可导致堆元数据损坏，触发崩溃或可利用漏洞。",
                "recommendation": "free 后立即置空并确保不会重复释放。",
            },
            {
                "type": "UNGUARDED_DIVISION",
                "severity": "MEDIUM",
                "regex": r"(?:\b[A-Za-z_]\w*|\d+|\))\s*/\s*([A-Za-z_]\w+)",
                "description": "检测到变量参与除法运算，需确认除数不为 0。",
                "risk": "可能触发除零异常导致程序崩溃。",
                "recommendation": "在除法前添加除数为 0 的显式检查。",
            },
        ]

        compiled_specs = [
            {
                **spec,
                "compiled_regex": re.compile(str(spec["regex"])),
            }
            for spec in pattern_specs
        ]

        max_findings = 200
        for file_path, content in source_content.items():
            file_name = os.path.basename(file_path)
            line_texts = content.splitlines()

            sanitized_lines: list[str] = []
            in_block_comment = False
            for line_text in line_texts:
                sanitized, in_block_comment = self._strip_comments_for_scan(line_text, in_block_comment)
                sanitized_lines.append(sanitized)

            # 检测 double free：同名变量出现两次及以上 free 调用。
            free_hits: dict[str, list[int]] = {}
            for line_no, line_text in enumerate(sanitized_lines, start=1):
                m = re.search(r"\bfree\s*\(\s*([A-Za-z_]\w*)\s*\)", line_text)
                if m:
                    var_name = m.group(1)
                    free_hits.setdefault(var_name, []).append(line_no)

            for var_name, lines in free_hits.items():
                if len(lines) < 2:
                    continue
                findings.append({
                    "id": f"SF_{len(findings) + 1:04d}",
                    "type": "DOUBLE_FREE_PATTERN",
                    "severity": "HIGH",
                    "file": file_name,
                    "line": lines[1],
                    "function": "",
                    "code": line_texts[lines[1] - 1].strip() if lines[1] - 1 < len(line_texts) else "",
                    "description": f"变量 {var_name} 存在重复 free 调用迹象。",
                    "risk": "可能导致堆损坏与崩溃，严重时可被利用。",
                    "recommendation": "统一释放路径并在 free 后置空指针。",
                })

            for spec in compiled_specs:
                if spec["type"] == "DOUBLE_FREE_PATTERN":
                    continue

                regex = spec["compiled_regex"]
                for line_no, line_text in enumerate(sanitized_lines, start=1):
                    if not line_text.strip():
                        continue
                    if not regex.search(line_text):
                        continue

                    findings.append({
                        "id": f"SF_{len(findings) + 1:04d}",
                        "type": spec["type"],
                        "severity": spec["severity"],
                        "file": file_name,
                        "line": line_no,
                        "function": "",
                        "code": line_texts[line_no - 1].strip() if line_no - 1 < len(line_texts) else "",
                        "description": spec["description"],
                        "risk": spec["risk"],
                        "recommendation": spec["recommendation"],
                    })

                    if len(findings) >= max_findings:
                        return findings

        return findings

    def _parse_source_definitions(self, source_path: str) -> list[FunctionSignature]:
        """
        从 .c 文件中提取函数定义。

        主要用于补齐“只有源码、没有头文件”的通用 C 项目。
        仅提取非 static 函数，避免生成 harness 时调用不可见的内部符号。
        """
        functions = []

        with open(source_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        source_name = os.path.basename(source_path)

        # 轻量级提取函数定义，尽量兼容跨行声明
        pattern = re.compile(
            r'^\s*(?!static\b)(?!typedef\b)(?!return\b)(?!if\b)(?!for\b)(?!while\b)(?!switch\b)'
            r'([A-Za-z_][\w\s\*\(\)]*?)\s+([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*\{',
            re.MULTILINE,
        )

        for match in pattern.finditer(content):
            return_type = re.sub(r'\s+', ' ', match.group(1)).strip()
            func_name = match.group(2).strip()
            params_str = match.group(3).strip()

            if not func_name or func_name in {"main"}:
                continue

            params = self._parse_params(params_str)
            pointer_count = sum(1 for p in params if p.is_pointer)

            functions.append(FunctionSignature(
                name=func_name,
                return_type=return_type,
                params=params,
                header_file=source_name,
                has_pointer_params=pointer_count > 0,
                pointer_param_count=pointer_count,
                param_count=len(params),
            ))

        if functions:
            self.log_info(f"从 {source_name} 补充提取到 {len(functions)} 个函数定义")

        return functions

    def _parse_header(self, header_path: str) -> list[FunctionSignature]:
        """
        解析 C 头文件，提取函数签名。

        使用正则表达式解析 CJSON_PUBLIC(type) funcname(params) 格式。
        """
        functions = []

        with open(header_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        header_name = os.path.basename(header_path)

        # 匹配 CJSON_PUBLIC(return_type) func_name(params)
        # 也匹配普通函数声明
        patterns = [
            # CJSON_PUBLIC(type) name(params);
            r'CJSON_PUBLIC\(([^)]+)\)\s+(\w+)\s*\(([^)]*)\)\s*;',
            # 普通函数声明: type name(params);
            r'^(?:extern\s+)?(\w[\w\s\*]+?)\s+(\w+)\s*\(([^)]*)\)\s*;',
        ]

        seen_names: set[str] = set()

        # 先匹配 CJSON_PUBLIC 宏风格，再回退匹配普通 C 函数声明
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            for return_type, func_name, params_str in matches:
                return_type = return_type.strip()
                func_name = func_name.strip()

                # 跳过宏/控制结构误匹配
                if not func_name or func_name in {"if", "for", "while", "switch", "return"}:
                    continue
                if func_name in seen_names:
                    continue

                params = self._parse_params(params_str.strip())
                pointer_count = sum(1 for p in params if p.is_pointer)

                func_sig = FunctionSignature(
                    name=func_name,
                    return_type=return_type,
                    params=params,
                    header_file=header_name,
                    has_pointer_params=pointer_count > 0,
                    pointer_param_count=pointer_count,
                    param_count=len(params)
                )
                functions.append(func_sig)
                seen_names.add(func_name)

        self.log_info(f"从 {header_name} 提取到 {len(functions)} 个函数签名")
        return functions

    def _parse_params(self, params_str: str) -> list[FunctionParameter]:
        """解析函数参数列表字符串"""
        params = []

        if not params_str or params_str.strip() == 'void':
            return params

        for param_str in params_str.split(','):
            param_str = param_str.strip()
            if not param_str:
                continue

            param = self._parse_single_param(param_str)
            if param:
                params.append(param)

        return params

    def _parse_single_param(self, param_str: str) -> FunctionParameter:
        """解析单个参数声明"""
        param_str = param_str.strip()

        is_const = 'const' in param_str
        is_pointer = '*' in param_str

        # 去掉 const 关键字来简化解析
        clean = param_str.replace('const', '').strip()

        # 尝试分离类型和名称
        # 处理 "char *value", "cJSON *item", "int index" 等
        parts = clean.split()
        if not parts:
            return FunctionParameter(name="unknown", type_str=param_str,
                                     is_pointer=is_pointer, is_const=is_const)

        # 如果最后一个 token 不含 * 且不是类型关键字，视为变量名
        if len(parts) >= 2:
            last_token = parts[-1]
            name = last_token.replace('*', '').strip()

            # 处理“无名参数”场景，如 "json_value *"、"int *"
            if not name:
                name = f"param_{id(param_str) % 1000}"
                type_str = param_str
            else:
                try:
                    type_str = param_str.rsplit(last_token, 1)[0].strip()
                    if not type_str:
                        type_str = param_str
                except ValueError:
                    # 防御性兜底，避免 empty separator 等异常
                    type_str = param_str
        else:
            # 只有一个 token，可能是 "void" 或其他
            name = parts[0].replace('*', '').strip()
            type_str = param_str
            if not name:
                name = f"param_{id(param_str) % 1000}"

        return FunctionParameter(
            name=name,
            type_str=param_str,
            is_pointer=is_pointer,
            is_const=is_const
        )

    def _analyze_function(self, func_sig: FunctionSignature,
                          source_content: dict) -> FunctionInfo:
        """分析单个函数的详细信息"""
        func_info = FunctionInfo(
            name=func_sig.name,
            signature=func_sig,
            pointer_param_count=func_sig.pointer_param_count,
            param_count=func_sig.param_count
        )

        # 在源文件中查找函数实现
        for src_path, content in source_content.items():
            func_body = self._extract_function_body(func_sig.name, content)
            if func_body:
                func_info.lines_of_code = func_body.count('\n') + 1
                func_info.cyclomatic_complexity = self._compute_cyclomatic_complexity(func_body)
                func_info.unsafe_calls = self._find_unsafe_calls(func_body)
                func_info.has_unsafe_calls = len(func_info.unsafe_calls) > 0
                break

        return func_info

    def _extract_function_body(self, func_name: str, source: str) -> str:
        """从源代码中提取函数体"""
        # 查找函数定义的开始位置
        # 匹配: 返回类型 函数名(参数) { ... }
        pattern = rf'\b{re.escape(func_name)}\s*\([^)]*\)\s*\{{'
        match = re.search(pattern, source)

        if not match:
            return ""

        # 找到匹配的左大括号，然后用括号匹配找到函数体结尾
        start = match.end() - 1  # 从 { 开始
        brace_count = 0
        end = start

        for i in range(start, len(source)):
            if source[i] == '{':
                brace_count += 1
            elif source[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = i + 1
                    break

        return source[start:end]

    def _compute_cyclomatic_complexity(self, func_body: str) -> int:
        """
        计算圈复杂度。
        圈复杂度 = 独立路径数 ≈ 1 + 条件语句数
        """
        complexity = 1

        # 统计条件关键字
        keywords = ['if', 'else if', 'elif', 'while', 'for', 'case', '&&', '||', '?']
        for kw in keywords:
            if kw in ('&&', '||', '?'):
                complexity += func_body.count(kw)
            else:
                # 用正则匹配完整关键字
                complexity += len(re.findall(rf'\b{kw}\b', func_body))

        return complexity

    def _find_unsafe_calls(self, func_body: str) -> list[str]:
        """查找函数体中的危险函数调用"""
        found = []
        for func in UNSAFE_FUNCTIONS:
            if re.search(rf'\b{func}\s*\(', func_body):
                found.append(func)
        return found

    def _compute_risk_score(self, func: FunctionInfo) -> float:
        """
        为每个函数计算风险分数（0~1），分数越高越应优先测试。

        评分维度：
        - 指针参数比例 (权重 0.3)
        - 圈复杂度 (权重 0.3)
        - 代码行数 (权重 0.2)
        - 危险函数调用 (权重 0.2)
        """
        score = 0.0

        # 指针参数越多，内存错误风险越高
        ptr_ratio = func.pointer_param_count / max(func.param_count, 1)
        score += ptr_ratio * 0.3

        # 圈复杂度越高，逻辑错误风险越高
        complexity_norm = min(func.cyclomatic_complexity / 20.0, 1.0)
        score += complexity_norm * 0.3

        # 代码行数越多，缺陷概率越大
        loc_norm = min(func.lines_of_code / 200.0, 1.0)
        score += loc_norm * 0.2

        # 是否包含危险函数调用（malloc, strcpy, sprintf 等）
        if func.has_unsafe_calls:
            score += 0.2

        return min(score, 1.0)

    def _build_call_graph(self, source_content: dict,
                          functions: list[FunctionSignature]) -> dict[str, list[str]]:
        """构建函数调用关系图"""
        func_names = {f.name for f in functions}
        call_graph = {f.name: [] for f in functions}

        for src_path, content in source_content.items():
            for func in functions:
                func_body = self._extract_function_body(func.name, content)
                if not func_body:
                    continue

                # 查找函数体中调用了哪些其他已知函数
                for other_name in func_names:
                    if other_name == func.name:
                        continue
                    if re.search(rf'\b{re.escape(other_name)}\s*\(', func_body):
                        if other_name not in call_graph[func.name]:
                            call_graph[func.name].append(other_name)

        return call_graph

    def _infer_constraints(self, functions: list[FunctionSignature]) -> dict[str, list[ValueRange]]:
        """
        根据函数参数类型推断值域约束。

        为 Generator Agent 提供输入生成的参考范围。
        """
        constraints = {}

        for func in functions:
            func_constraints = []
            for param in func.params:
                vr = self._infer_param_constraint(param, func.name)
                if vr:
                    func_constraints.append(vr)

            if func_constraints:
                constraints[func.name] = func_constraints

        return constraints

    def _infer_param_constraint(self, param: FunctionParameter,
                                 func_name: str) -> ValueRange:
        """推断单个参数的值域约束"""
        type_str = param.type_str.lower()

        if 'char' in type_str and '*' in type_str:
            # 字符串/字符指针参数
            return ValueRange(
                param_name=param.name,
                valid_values=['""', '"hello"', '"{}"', '"[]"', '"null"'],
                invalid_values=['NULL', '(very long string)', '(binary data)'],
                boundary_values=['""', '"\\0"', '(max length string)'],
                description=f"字符串参数 {param.name}，可能接受 JSON 字符串"
            )
        elif 'int' in type_str:
            return ValueRange(
                param_name=param.name,
                valid_values=['0', '1', '10', '100'],
                invalid_values=['-1', '-2147483648', '2147483647'],
                boundary_values=['0', '-1', '1', '2147483647', '-2147483648'],
                description=f"整型参数 {param.name}"
            )
        elif 'double' in type_str or 'float' in type_str:
            return ValueRange(
                param_name=param.name,
                valid_values=['0.0', '1.0', '3.14', '-1.0'],
                invalid_values=['NaN', 'Inf', '-Inf', '1e308'],
                boundary_values=['0.0', '-0.0', '1e-308', '1e308', '-1e308'],
                description=f"浮点参数 {param.name}"
            )
        elif 'size_t' in type_str:
            return ValueRange(
                param_name=param.name,
                valid_values=['0', '1', '1024', '65536'],
                invalid_values=['(size_t)-1', '0'],
                boundary_values=['0', '1', '4294967295'],
                description=f"size_t 参数 {param.name}"
            )
        elif 'cjson' in type_str and '*' in type_str:
            return ValueRange(
                param_name=param.name,
                valid_values=['(valid cJSON object)'],
                invalid_values=['NULL', '(freed pointer)', '(invalid pointer)'],
                boundary_values=['NULL', '(empty object)', '(deeply nested)'],
                description=f"cJSON 指针参数 {param.name}"
            )
        elif 'bool' in type_str or 'cjson_bool' in type_str:
            return ValueRange(
                param_name=param.name,
                valid_values=['0', '1'],
                invalid_values=['-1', '2', '999'],
                boundary_values=['0', '1'],
                description=f"布尔参数 {param.name}"
            )

        return ValueRange(
            param_name=param.name,
            description=f"未知类型参数: {param.type_str}"
        )

    def _apply_api_analysis_hints(self, result: AnalysisResult):
        """调用 API 提供风险排序建议；失败时保持原始静态分析输出。"""
        if not self.api_advisor.enabled:
            return

        top_candidates = sorted(
            result.risk_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:20]

        context = {
            "source_files": [os.path.basename(p) for p in result.source_files[:20]],
            "function_count": len(result.functions),
            "static_top_risks": [{"name": n, "score": round(float(s), 4)} for n, s in top_candidates],
            "pointer_heavy": [f.name for f in result.functions if f.pointer_param_count >= 2][:20],
        }
        expected_schema = {
            "high_risk_functions": ["func_name"],
            "risk_adjustments": {"func_name": 0.1},
            "reason": "short explanation",
        }

        decision = self.api_advisor.ask_json(
            task="结合静态风险信息，给出高风险函数优先级建议",
            context=context,
            expected_schema=expected_schema,
            max_tokens=500,
            temperature=0.1,
        )
        if not decision:
            return

        known_names = {f.name for f in result.functions}

        # 1) 处理风险分微调
        adjustments = decision.get("risk_adjustments", {})
        if isinstance(adjustments, dict):
            for name, delta in adjustments.items():
                fn = str(name).strip()
                if fn not in known_names:
                    continue
                old_score = float(result.risk_scores.get(fn, 0.0))
                new_score = min(1.0, max(0.0, old_score + float(delta)))
                result.risk_scores[fn] = new_score

        # 2) 处理高风险函数排序建议
        api_rank = [str(x).strip() for x in decision.get("high_risk_functions", []) if str(x).strip() in known_names]
        if api_rank:
            merged = []
            seen = set()
            for name in api_rank + result.high_risk_functions:
                if name in seen:
                    continue
                seen.add(name)
                merged.append(name)
            result.high_risk_functions = merged[:30]

        # 3) 最终兜底按分数排序一次
        result.high_risk_functions = sorted(
            set(result.high_risk_functions),
            key=lambda n: result.risk_scores.get(n, 0.0),
            reverse=True,
        )

        reason = str(decision.get("reason", "")).strip()
        if reason:
            self.log_info(f"API 分析增强已应用: {reason}")
