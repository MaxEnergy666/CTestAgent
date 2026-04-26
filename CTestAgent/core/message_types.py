# message_types.py - 所有消息类型定义
"""
定义系统中所有智能体之间传递的消息数据结构。
使用 dataclass 实现，确保类型安全和可读性。
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional
import time
import uuid


# ============================================================
# 枚举类型
# ============================================================

class Phase(Enum):
    """Coordinator Agent 的阶段状态机"""
    INIT = auto()       # 初始化：等待 Analyzer 完成分析
    EXPLORE = auto()    # 初始探索：快速扩大覆盖率
    DEEP = auto()       # 深度挖掘：精准寻找缺陷
    FINALIZE = auto()   # 收尾验证：验证与去重

class TestCaseStatus(Enum):
    """测试用例执行状态"""
    PASS = "PASS"
    CRASH = "CRASH"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"

class FeedbackType(Enum):
    """Reviewer Agent 反馈类型"""
    COVERAGE_GAP = "COVERAGE_GAP"           # 覆盖率缺口
    LOW_DIVERSITY = "LOW_DIVERSITY"         # 多样性不足
    DECISION_TABLE_GAP = "DECISION_TABLE_GAP"   # 决策表缺失组合
    REDUNDANT_CASES = "REDUNDANT_CASES"     # 冗余用例
    PROMISING_AREA = "PROMISING_AREA"       # 接近突破的区域
    STAGNATION = "STAGNATION"              # 覆盖率增长停滞


# ============================================================
# 消息总线基础消息
# ============================================================

@dataclass
class Message:
    """消息总线中传递的基本消息"""
    topic: str              # 消息主题
    data: Any               # 消息数据（具体的数据类型）
    sender: str = ""        # 发送者名称
    timestamp: float = field(default_factory=time.time)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


# ============================================================
# Analyzer Agent 相关数据结构
# ============================================================

@dataclass
class FunctionParameter:
    """函数参数信息"""
    name: str               # 参数名
    type_str: str           # 参数类型字符串（如 "const char *"）
    is_pointer: bool = False    # 是否是指针类型
    is_const: bool = False      # 是否是 const

@dataclass
class FunctionSignature:
    """函数签名信息"""
    name: str                               # 函数名
    return_type: str                        # 返回类型
    params: list[FunctionParameter] = field(default_factory=list)
    header_file: str = ""                   # 所在头文件
    has_pointer_params: bool = False        # 是否含有指针参数
    pointer_param_count: int = 0            # 指针参数数量
    param_count: int = 0                    # 总参数数量

@dataclass
class FunctionInfo:
    """函数详细信息（含风险评估所需数据）"""
    name: str
    signature: FunctionSignature
    lines_of_code: int = 0                  # 代码行数
    cyclomatic_complexity: int = 1          # 圈复杂度
    has_unsafe_calls: bool = False          # 是否包含危险函数调用
    unsafe_calls: list[str] = field(default_factory=list)  # 危险函数列表
    pointer_param_count: int = 0
    param_count: int = 0

@dataclass
class ValueRange:
    """参数值域约束"""
    param_name: str
    valid_values: list[Any] = field(default_factory=list)       # 有效值示例
    invalid_values: list[Any] = field(default_factory=list)     # 无效值示例
    boundary_values: list[Any] = field(default_factory=list)    # 边界值
    description: str = ""

@dataclass
class AnalysisResult:
    """Analyzer Agent 的分析结果"""
    functions: list[FunctionSignature] = field(default_factory=list)        # 所有可测函数
    high_risk_functions: list[str] = field(default_factory=list)           # 高风险函数名列表
    risk_scores: dict[str, float] = field(default_factory=dict)           # 函数名 → 风险分
    call_graph: dict[str, list[str]] = field(default_factory=dict)        # 调用关系图
    param_constraints: dict[str, list[ValueRange]] = field(default_factory=dict)  # 参数约束
    function_infos: dict[str, FunctionInfo] = field(default_factory=dict)  # 函数详细信息
    source_files: list[str] = field(default_factory=list)                  # 源文件列表
    header_files: list[str] = field(default_factory=list)                  # 头文件列表
    total_lines: int = 0
    static_findings: list[dict] = field(default_factory=list)              # 静态漏洞发现


# ============================================================
# Generator Agent 相关数据结构
# ============================================================

@dataclass
class TestCase:
    """测试用例"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    target_function: str = ""               # 目标测试函数
    input_data: bytes = b""                 # 输入数据（原始字节）
    input_text: str = ""                    # 输入数据（文本形式，如 JSON 字符串）
    strategy: str = ""                      # 生成策略名称
    parent_id: str = ""                     # 父用例 ID（如果是变异生成的）
    metadata: dict = field(default_factory=dict)  # 额外元数据

@dataclass
class GenerateCommand:
    """Coordinator Agent 向 Generator Agent 发送的生成指令"""
    strategy_weights: dict[str, float] = field(default_factory=dict)
    focus_functions: list[str] = field(default_factory=list)
    batch_size: int = 50
    phase: Phase = Phase.EXPLORE


# ============================================================
# Executor Agent 相关数据结构
# ============================================================

@dataclass
class ExecutionResult:
    """单个测试用例的执行结果"""
    test_case_id: str = ""
    test_case: Optional[TestCase] = None    # 关联的测试用例
    status: TestCaseStatus = TestCaseStatus.PASS
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    execution_time: float = 0.0
    signal: Optional[int] = None            # 终止信号（如 SIGSEGV=11）
    asan_error: Optional[str] = None        # ASan 报告的错误类型
    coverage_delta: set[str] = field(default_factory=set)   # 新覆盖的代码行
    coverage_lines: set[str] = field(default_factory=set)   # 该用例覆盖的所有行
    branch_delta: set[str] = field(default_factory=set)     # 新覆盖的分支 (file:line:branch)
    branch_covered: set[str] = field(default_factory=set)   # 该用例覆盖到的分支
    branch_total: set[str] = field(default_factory=set)     # 该用例观测到的分支全集


# ============================================================
# Reviewer Agent 相关数据结构
# ============================================================

@dataclass
class ReviewFeedback:
    """Reviewer Agent 生成的反馈建议"""
    type: FeedbackType                      # 反馈类型
    message: str = ""                       # 描述信息
    uncovered_lines: list[str] = field(default_factory=list)    # 未覆盖的行
    missing_conditions: list[str] = field(default_factory=list)  # 缺失的条件组合
    redundant_ids: list[str] = field(default_factory=list)      # 冗余用例 ID
    promising_functions: list[str] = field(default_factory=list) # 有希望的函数/分支
    data: dict = field(default_factory=dict)    # 额外数据

@dataclass
class ReviewResult:
    """Reviewer Agent 的用例审查结果"""
    approved: list[TestCase] = field(default_factory=list)          # 通过的用例
    rejected: list[tuple] = field(default_factory=list)             # 被拒绝的 (TestCase, reason)
    suggestions: list[ReviewFeedback] = field(default_factory=list)  # 建议

@dataclass
class ConfirmedCrash:
    """经 Reviewer Agent 确认的崩溃"""
    crash: Optional[ExecutionResult] = None
    reproducibility: float = 0.0            # 可复现率 (0~1)
    sensitivity: float = 0.0                # 边界敏感性
    stack_hash: str = ""                    # 调用栈指纹
    min_input: Optional[bytes] = None       # 最小化后的输入
    occurrence_count: int = 1               # 触发次数
    crash_type: str = ""                    # 缺陷类型
    source_location: str = ""              # 代码位置（file:line）
    risk_level: str = "MEDIUM"             # 风险等级
    risk_summary: str = ""                 # 风险描述
    metadata: dict = field(default_factory=dict)

@dataclass
class VerifyResult:
    """Reviewer Agent 的崩溃验证结果"""
    confirmed: list[ConfirmedCrash] = field(default_factory=list)
    false_positives: list[ExecutionResult] = field(default_factory=list)


# ============================================================
# Coordinator Agent 相关数据结构
# ============================================================

@dataclass
class RoundSummary:
    """单轮测试的汇总信息"""
    round_number: int = 0
    total_generated: int = 0                # 本轮生成的总用例数
    reviewer_approved: int = 0              # Reviewer 通过的用例数
    total_executed: int = 0                 # 已执行用例数
    new_coverage_lines: int = 0             # 新覆盖的代码行数
    total_coverage_lines: int = 0           # 累计覆盖的代码行数
    total_source_lines: int = 0             # 源代码总行数
    coverage_rate: float = 0.0              # 覆盖率
    new_crashes_found: int = 0              # 新发现的崩溃数
    false_positives_caught: int = 0         # 拦截的误报数
    phase: Phase = Phase.EXPLORE

@dataclass
class RoundMetrics:
    """单轮协作效率指标"""
    total_generated: int = 0
    reviewer_approved: int = 0
    approval_rate: float = 0.0
    new_coverage_lines: int = 0
    new_crashes_found: int = 0
    false_positives_caught: int = 0
    coverage_per_case: float = 0.0          # 每个用例带来的平均覆盖增长
    useful_case_ratio: float = 0.0          # 有价值用例比例
    feedback_hit_rate: float = 0.0          # 反馈后命中新覆盖的比例

@dataclass
class FinalizeCommand:
    """Coordinator Agent 发出的收尾指令"""
    generate_report: bool = True
    minimize_crashes: bool = True

@dataclass
class ConflictReport:
    """Generator Agent 与 Reviewer Agent 之间的分歧报告"""
    generator_proposal: str = ""
    reviewer_objection: str = ""
    context: dict = field(default_factory=dict)

@dataclass
class PhaseChangeNotification:
    """阶段切换通知"""
    old_phase: Phase = Phase.INIT
    new_phase: Phase = Phase.EXPLORE
    reason: str = ""
    new_strategy_weights: dict[str, float] = field(default_factory=dict)


# ============================================================
# Reporter Agent 相关数据结构
# ============================================================

@dataclass
class CoverageData:
    """覆盖率数据"""
    covered_lines: set[str] = field(default_factory=set)     # 已覆盖的行 (file:line)
    total_lines: set[str] = field(default_factory=set)       # 所有可执行行
    branch_covered: set[str] = field(default_factory=set)    # 已覆盖的分支
    branch_total: set[str] = field(default_factory=set)      # 所有分支
    coverage_history: list[float] = field(default_factory=list)  # 覆盖率增长历史

    @property
    def line_rate(self) -> float:
        if not self.total_lines:
            return 0.0
        return len(self.covered_lines) / len(self.total_lines)

    @property
    def branch_rate(self) -> float:
        if not self.branch_total:
            return 0.0
        return len(self.branch_covered) / len(self.branch_total)

    def get_uncovered_lines(self) -> list[str]:
        return list(self.total_lines - self.covered_lines)

    def recent_growth_rate(self, last_n: int = 200) -> float:
        """最近 N 轮的覆盖率增长速度"""
        if len(self.coverage_history) < 2:
            return 1.0  # 初始阶段，返回高增长率
        recent = self.coverage_history[-last_n:]
        if len(recent) < 2:
            return 1.0
        return (recent[-1] - recent[0]) / len(recent)

@dataclass
class BugReport:
    """单个缺陷报告"""
    bug_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    error_type: str = ""                    # 错误类型（segfault, heap-buffer-overflow 等）
    crash: Optional[ConfirmedCrash] = None
    min_input: str = ""                     # 最小触发输入
    stack_trace: str = ""                   # 调用栈
    target_function: str = ""               # 涉及函数
    severity: str = "MEDIUM"                # 严重程度
    description: str = ""

@dataclass
class TestReport:
    """最终测试报告"""
    project_name: str = ""
    total_cases_generated: int = 0
    total_cases_executed: int = 0
    total_cases_passed: int = 0
    total_crashes: int = 0
    confirmed_bugs: list[BugReport] = field(default_factory=list)
    false_positives: int = 0
    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    coverage_data: Optional[CoverageData] = None
    round_metrics: list[RoundMetrics] = field(default_factory=list)
    total_rounds: int = 0
    total_time: float = 0.0
    agent_interaction_stats: dict = field(default_factory=dict)
