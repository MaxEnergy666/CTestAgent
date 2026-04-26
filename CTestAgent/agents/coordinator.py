# coordinator.py - 协调者 Agent
"""
Coordinator Agent（协调者）：系统的"总指挥"。

不直接参与测试，负责编排其他智能体的工作流程：
1. 任务调度：根据阶段向各智能体发送工作指令
2. 阶段管理：INIT → EXPLORE → DEEP → FINALIZE
3. 终止判定：综合覆盖率、缺陷速度、时间预算
4. 冲突仲裁：裁决 Generator 与 Reviewer 的分歧
"""

import logging
import time

from core.base_agent import BaseAgent
from core.agent_api import normalize_weights
from core.message_bus import MessageBus
from core.global_state import GlobalTestState
from core.message_types import (
    Message, Phase, AnalysisResult, GenerateCommand,
    FinalizeCommand, PhaseChangeNotification,
    RoundSummary, ConfirmedCrash, ExecutionResult,
    TestCaseStatus, ReviewFeedback, FeedbackType
)

logger = logging.getLogger(__name__)

# 各阶段的策略权重
EXPLORE_WEIGHTS = {
    "equivalence": 0.35,
    "boundary": 0.35,
    "random_error_guess": 0.20,
    "error_guess": 0.10,
}

DEEP_WEIGHTS = {
    "decision_table": 0.35,
    "state_sequence": 0.25,
    "whitebox_guided": 0.30,
    "error_guess": 0.10,
}


class CoordinatorAgent(BaseAgent):
    """
    协调者智能体。

    管理测试全生命周期，驱动 生成→审查→执行→反馈 的迭代循环。
    """

    def __init__(self, bus: MessageBus, config: dict = None):  # type: ignore
        super().__init__("Coordinator", bus, config)

        # 全局状态
        self.global_state = GlobalTestState(config)

        # 配置
        self.batch_size = config.get("batch_size", 50) if config else 50
        self.max_batch_size = max(
            int((config or {}).get("max_batch_size", self.batch_size * 4)),
            self.batch_size,
        )
        self.rescue_batch_multiplier = max(1.0, float((config or {}).get("rescue_batch_multiplier", 2.0)))
        self.stagnation_batch_multiplier = max(1.0, float((config or {}).get("stagnation_batch_multiplier", 1.35)))
        self.max_rounds = config.get("max_rounds", 200) if config else 200
        self.source_dir = str(config.get("source_dir", "")) if config else ""
        self.entry_function = str(config.get("entry_function", "")) if config else ""
        self.explore_weights = dict(config.get("explore_weights", EXPLORE_WEIGHTS)) if config else EXPLORE_WEIGHTS.copy()
        self.deep_weights = dict(config.get("deep_weights", DEEP_WEIGHTS)) if config else DEEP_WEIGHTS.copy()
        self.api_round_interval = max(1, int((config or {}).get("api_round_interval", 3)))

        initial_phase_name = str(config.get("initial_phase", "EXPLORE")).upper() if config else "EXPLORE"
        self.initial_phase = Phase.EXPLORE
        if initial_phase_name == "DEEP":
            self.initial_phase = Phase.DEEP

        # 分析结果缓存
        self.analysis_result = None
        self._analysis_ready = False
        default_focus = self._default_focus_function()
        self._focus_functions: list[str] = [default_focus] if default_focus else []

        # 运行状态
        self._round_complete = False
        self._waiting_for_results = False
        self._fatal_executor_error = ""

    def _resolve_round_batch_size(self, rescue_round: bool) -> int:
        """根据当前状态动态调整 batch，提升停滞阶段探索效率。"""
        target = int(self.batch_size)
        if rescue_round:
            target = int(round(self.batch_size * self.rescue_batch_multiplier))
        else:
            stagnation_rounds = self.global_state.get_stagnation_rounds()
            trigger = max(6, self.global_state.no_progress_round_limit // 2)
            if stagnation_rounds >= trigger:
                target = int(round(self.batch_size * self.stagnation_batch_multiplier))

        target = max(1, min(target, self.max_batch_size))
        return target

    def _default_focus_function(self) -> str:
        """根据配置推断默认入口函数"""
        if self.entry_function:
            return self.entry_function

        # 通用 C 场景：由 Analyzer 的 high_risk_functions/function 列表驱动
        # 仅在确无分析结果时返回空字符串，避免绑定特定项目入口
        return ""

    def setup(self):
        """注册消息订阅"""
        self.subscribe("data:analysis_result", self.on_analysis_complete)
        self.subscribe("data:execution_results", self.on_execution_results)
        self.subscribe("data:verified_crashes", self.on_verified_crashes)
        self.subscribe("data:executor_error", self.on_executor_error)
        self.subscribe("feedback:strategy", self.on_strategy_feedback)
        self.subscribe("data:report_ready", self.on_report_ready)

    # ================================================================
    # 主控制流
    # ================================================================

    def run_testing_loop(self):
        """运行主测试循环"""
        self.log_info("=" * 60)
        self.log_info("多智能体协同测试系统启动")
        self.log_info("=" * 60)

        # 阶段 1：启动分析
        self.global_state.phase = Phase.INIT
        self.log_info("[阶段 INIT] 启动代码分析...")
        self.publish("cmd:start_analysis", {})

        # 注意：不要在 on_analysis_complete 回调内直接进入 _run_rounds，
        # 否则会阻塞同一条 data:analysis_result 对其他订阅者（如 Reporter）的分发。
        # 在同步消息总线中，publish 返回后再启动轮次，确保分析消息已被完整分发。
        if not self._analysis_ready:
            self.log_error("分析未完成，无法启动测试循环")
            return

        self._run_rounds(self._focus_functions)

    def on_analysis_complete(self, msg: Message):
        """Analyzer 完成分析后，启动测试循环"""
        self.analysis_result = msg.data

        if isinstance(msg.data, AnalysisResult):
            self.global_state.total_source_lines = msg.data.total_lines
            fallback_functions = [f.name for f in (msg.data.functions or []) if getattr(f, "name", "")]
            fallback_functions = fallback_functions[:5]
            focus_functions = msg.data.high_risk_functions[:5] or fallback_functions
        else:
            default_focus = self._default_focus_function()
            focus_functions = [default_focus] if default_focus else []

        self._focus_functions = focus_functions
        self._analysis_ready = True

        self.log_info(
            f"分析完成，源代码 {self.global_state.total_source_lines} 行, "
            f"重点函数: {focus_functions}"
        )

        # 切换到 EXPLORE 阶段
        target_phase = self.initial_phase if self.initial_phase in {Phase.EXPLORE, Phase.DEEP} else Phase.EXPLORE
        phase_reason = "分析完成，开始初始探索" if target_phase == Phase.EXPLORE else "分析完成，按配置直接进入深度挖掘"
        self._transition_to_phase(target_phase, phase_reason)

    def _run_rounds(self, focus_functions: list[str]):
        """运行测试迭代"""
        while not self.global_state.should_stop():
            if self._fatal_executor_error:
                self.log_error("检测到执行器致命错误，提前结束测试循环")
                break

            self.global_state.start_new_round()
            round_num = self.global_state.round_number

            self.log_info(f"\n--- 第 {round_num} 轮 (阶段: {self.global_state.phase.name}) ---")

            # 检查阶段切换
            if self.global_state.should_transition_to_deep():
                self._transition_to_phase(Phase.DEEP, "覆盖率达到阈值，切换到深度挖掘")

            # 选择当前阶段的策略权重
            if self.global_state.phase == Phase.EXPLORE:
                weights = self.explore_weights.copy()
            elif self.global_state.phase == Phase.DEEP:
                weights = self.deep_weights.copy()
            else:
                weights = self.explore_weights.copy()

            # API 增强：让 Coordinator 基于当前轮状态给出策略微调建议。
            round_focus = list(focus_functions)
            rescue_round = self.global_state.consume_no_progress_rescue_flag()
            if rescue_round:
                self.log_warning(
                    f"检测到连续无进展，触发救援轮次 #{self.global_state.no_progress_rescue_used}"
                )
                self._apply_no_progress_rescue_plan(weights, round_focus)
            else:
                self._apply_api_round_plan(weights, round_focus)

            round_batch_size = self._resolve_round_batch_size(rescue_round)
            if round_batch_size != self.batch_size:
                self.log_info(
                    f"本轮启用自适应批量: {self.batch_size} -> {round_batch_size}"
                )

            self.log_info(
                f"[OBS][dispatch] round={round_num}, phase={self.global_state.phase.name}, "
                f"rescue={rescue_round}, batch={round_batch_size}, weights={weights}, focus={round_focus}"
            )

            # 发送生成指令
            cmd = GenerateCommand(
                strategy_weights=weights,
                focus_functions=round_focus,
                batch_size=round_batch_size,
                phase=self.global_state.phase,
            )
            self.publish("cmd:generate", cmd)

            # 同步消息总线下，上面的 publish 会触发完整的
            # Generate -> Review -> Execute -> Verify 链
            # 在回调中更新 global_state

            # 结束本轮
            summary = self.global_state.finish_round()
            self._log_round_summary(summary)

        # 测试循环结束
        self._finalize()

    def on_execution_results(self, msg: Message):
        """接收执行结果，更新全局状态"""
        results = msg.data
        if not isinstance(results, list):
            return

        for result in results:
            if isinstance(result, ExecutionResult):
                self.global_state.record_execution(result)

        executed = len([r for r in results if isinstance(r, ExecutionResult)])
        self.global_state.record_generated(executed)  # 近似：已执行≈已生成
        self.global_state.record_approved(executed)

    def on_verified_crashes(self, msg: Message):
        """接收验证后的崩溃"""
        crashes = msg.data
        if isinstance(crashes, list):
            for crash in crashes:
                if isinstance(crash, ConfirmedCrash):
                    self.global_state.record_confirmed_crash(crash)
                    self.log_info(
                        f"🐛 确认新缺陷! hash={crash.stack_hash}, "
                        f"累计 {len(self.global_state.confirmed_crashes)} 个"
                    )

    def on_executor_error(self, msg: Message):
        """接收执行器错误，致命错误时触发提前终止。"""
        data = msg.data if isinstance(msg.data, dict) else {}
        if not data:
            return

        error_type = str(data.get("type", "")).upper()
        fatal = bool(data.get("fatal", False))
        if not fatal:
            return

        if error_type != "COMPILE_FAILED":
            return

        detail = str(data.get("message") or data.get("detail") or "执行器编译失败").strip()
        if not detail:
            detail = "执行器编译失败"

        self._fatal_executor_error = detail
        self.global_state.last_stop_reason = "executor_compile_failed"
        self.log_error(f"执行器致命编译失败: {detail}")

    def on_strategy_feedback(self, msg: Message):
        """接收 Reviewer 的策略反馈"""
        # 记录日志
        if isinstance(msg.data, list):
            type_counter: dict[str, int] = {}
            for fb in msg.data:
                if isinstance(fb, ReviewFeedback):
                    key = fb.type.value
                    type_counter[key] = type_counter.get(key, 0) + 1
                    self.log_debug(f"Reviewer 策略建议: {fb.type.value} - {fb.message}")
            if type_counter:
                self.log_info(f"[OBS][strategy_feedback_recv] types={type_counter}")

    def on_report_ready(self, msg: Message):
        """报告生成完成"""
        self.log_info("测试报告已生成")

    # ================================================================
    # 阶段管理
    # ================================================================

    def _transition_to_phase(self, new_phase: Phase, reason: str):
        """切换测试阶段"""
        old_phase = self.global_state.phase
        if old_phase == new_phase:
            return

        self.global_state.phase = new_phase
        self.log_info(f"阶段切换: {old_phase.name} -> {new_phase.name} ({reason})")

        # 通知其他智能体
        if new_phase == Phase.DEEP:
            weights = self.deep_weights.copy()
        elif new_phase == Phase.EXPLORE:
            weights = self.explore_weights.copy()
        else:
            weights = {}

        notification = PhaseChangeNotification(
            old_phase=old_phase,
            new_phase=new_phase,
            reason=reason,
            new_strategy_weights=weights,
        )
        self.publish("cmd:phase_change", notification)

    def _finalize(self):
        """收尾阶段"""
        self._transition_to_phase(Phase.FINALIZE, "测试循环结束，开始收尾")

        self.log_info("=" * 60)
        self.log_info("测试循环结束，开始生成报告")
        self.log_info(f"总轮次: {self.global_state.round_number}")
        self.log_info(f"总用例: {self.global_state.total_executed}")
        self.log_info(
            f"覆盖率: 行={self.global_state.current_coverage_rate:.2%}, "
            f"分支={self.global_state.current_branch_coverage_rate:.2%}"
        )
        if self.global_state.last_stop_reason:
            self.log_info(f"停止原因: {self.global_state.last_stop_reason}")
        self.log_info(f"确认缺陷: {len(self.global_state.confirmed_crashes)}")
        self.log_info(f"总耗时: {self.global_state.elapsed_time:.1f}s")
        self.log_info("=" * 60)

        # 通知 Reporter 生成报告
        self.publish("cmd:finalize", FinalizeCommand(
            generate_report=True,
            minimize_crashes=True,
        ))

    def _log_round_summary(self, summary: RoundSummary):
        """记录单轮汇总"""
        self.log_info(
            f"轮次 {summary.round_number} 汇总: "
            f"生成={summary.total_generated}, "
            f"执行={summary.total_executed}, "
            f"新覆盖={summary.new_coverage_lines}行, "
            f"覆盖率={summary.coverage_rate:.2%}, "
            f"新崩溃={summary.new_crashes_found}"
        )

    def _apply_api_round_plan(self, weights: dict[str, float], focus_functions: list[str]):
        """调用 API 给出本轮策略微调建议（非强制，失败自动回退本地策略）。"""
        self._apply_api_round_plan_with_mode(weights, focus_functions, force=False)

    def _apply_api_round_plan_with_mode(
        self,
        weights: dict[str, float],
        focus_functions: list[str],
        force: bool,
    ):
        """调用 API 给出本轮策略微调建议（支持强制调用）。"""
        if not self.api_advisor.enabled:
            return
        if self.global_state.round_number <= 0 and not force:
            return
        if not force and self.global_state.round_number % self.api_round_interval != 0:
            return

        context = {
            "phase": self.global_state.phase.name,
            "round": self.global_state.round_number,
            "coverage_rate": round(self.global_state.current_coverage_rate, 6),
            "confirmed_crashes": len(self.global_state.confirmed_crashes),
            "elapsed_seconds": int(self.global_state.elapsed_time),
            "time_remaining": int(self.global_state.time_remaining),
            "current_weights": weights,
            "focus_functions": focus_functions,
            "recent_round": self.global_state.round_summaries[-1].__dict__ if self.global_state.round_summaries else {},
        }
        expected_schema = {
            "strategy_weights": {
                "equivalence": 0.0,
                "boundary": 0.0,
                "decision_table": 0.0,
                "state_sequence": 0.0,
                "whitebox_guided": 0.0,
                "random_error_guess": 0.0,
                "error_guess": 0.0,
            },
            "focus_functions": ["function_name"],
            "reason": "short explanation",
        }

        decision = self.api_advisor.ask_json(
            task="为当前轮次推荐策略权重与重点函数",
            context=context,
            expected_schema=expected_schema,
            max_tokens=500,
            temperature=0.1,
        )
        if not decision:
            return

        api_weights = normalize_weights(decision.get("strategy_weights", {}))
        if api_weights:
            weights.clear()
            weights.update(api_weights)

        api_focus = [str(x).strip() for x in decision.get("focus_functions", []) if str(x).strip()]
        if api_focus:
            focus_functions.clear()
            focus_functions.extend(api_focus[:5])

        reason = str(decision.get("reason", "")).strip()
        if reason:
            self.log_info(f"API 调度建议已采纳: {reason}")

    def _apply_no_progress_rescue_plan(self, weights: dict[str, float], focus_functions: list[str]):
        """无进展救援：切换到高多样性+白盒导向，并强制请求 API 建议。"""
        rescue_weights = {
            "whitebox_guided": 0.35,
            "random_error_guess": 0.25,
            "boundary": 0.18,
            "equivalence": 0.10,
            "decision_table": 0.07,
            "state_sequence": 0.05,
        }
        weights.clear()
        weights.update(rescue_weights)

        if self.analysis_result is not None:
            api_focus = []
            if isinstance(self.analysis_result, AnalysisResult):
                api_focus = list(self.analysis_result.high_risk_functions[:5])
            if api_focus:
                focus_functions.clear()
                focus_functions.extend(api_focus)

        self._apply_api_round_plan_with_mode(weights, focus_functions, force=True)
