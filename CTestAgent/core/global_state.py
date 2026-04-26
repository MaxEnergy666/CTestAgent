# global_state.py - 全局测试状态
"""
维护系统级别的全局测试状态。
包括覆盖率数据、缺陷列表、轮次统计等。
供 Coordinator Agent 做阶段切换和终止判定使用。
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from core.message_types import (
    Phase, CoverageData, ConfirmedCrash, RoundSummary,
    RoundMetrics, TestCase, ExecutionResult
)


class GlobalTestState:
    """
    全局测试状态管理器。

    集中记录测试过程中的核心数据：
    - 当前阶段
    - 覆盖率信息
    - 发现的缺陷
    - 各轮次统计
    - 时间预算
    """

    def __init__(self, config: Optional[dict] = None):
        config = config or {}

        # 阶段状态
        self.phase: Phase = Phase.INIT
        self.round_number: int = 0

        # 覆盖率
        self.coverage = CoverageData()

        # 缺陷记录
        self.confirmed_crashes: list[ConfirmedCrash] = []
        self.known_crash_hashes: set[str] = set()

        # 用例记录
        self.total_generated: int = 0
        self.total_executed: int = 0
        self.total_passed: int = 0
        self.total_crashed: int = 0
        self.total_timeout: int = 0
        self.false_positives: int = 0

        # 轮次统计
        self.round_summaries: list[RoundSummary] = []
        self.round_metrics_list: list[RoundMetrics] = []

        # 当前轮次的临时数据
        self._current_round_generated: int = 0
        self._current_round_approved: int = 0
        self._current_round_executed: int = 0
        self._current_round_new_coverage: int = 0
        self._current_round_new_crashes: int = 0
        self._current_round_false_positives: int = 0

        # 时间管理
        self.start_time: float = time.time()
        self.max_time_seconds: int = config.get("max_time_seconds", 1800)
        self.max_rounds: int = config.get("max_rounds", 200)
        self.max_rounds_hard_cap: int = max(
            int(config.get("max_rounds_hard_cap", 500)),
            self.max_rounds,
        )

        # 阶段切换配置
        self.explore_to_deep_threshold: float = config.get("explore_to_deep_threshold", 0.6)
        self.stagnation_limit: int = config.get("stagnation_limit", 500)
        self.stop_on_stagnation: bool = bool(config.get("stop_on_stagnation", False))
        self.no_progress_round_limit: int = max(0, int(config.get("no_progress_round_limit", 12)))
        self.no_progress_rescue_enabled: bool = bool(config.get("no_progress_rescue_enabled", True))
        # <=0 表示不限救援次数。
        self.no_progress_rescue_max_attempts: int = int(config.get("no_progress_rescue_max_attempts", 0))
        self.no_progress_stop_enabled: bool = bool(config.get("no_progress_stop_enabled", False))
        self.no_progress_min_rounds_before_stop: int = max(
            0,
            int(config.get("no_progress_min_rounds_before_stop", max(self.no_progress_round_limit * 8, 240))),
        )
        self.no_progress_min_seconds_before_stop: int = max(
            0,
            int(
                config.get(
                    "no_progress_min_seconds_before_stop",
                    min(self.max_time_seconds, max(900, int(self.max_time_seconds * 0.5))),
                )
            ),
        )
        self.no_progress_rescue_cooldown_rounds: int = max(
            1,
            int(config.get("no_progress_rescue_cooldown_rounds", max(1, self.no_progress_round_limit))),
        )
        self.no_progress_rescue_used: int = 0
        self.no_progress_rescue_pending: bool = False
        self.no_progress_last_rescue_round: int = -10**9

        # 终止目标：优先按覆盖目标结束，轮次仅作为硬上限兜底
        self.coverage_goal_line: float = max(0.0, min(1.0, float(config.get("coverage_goal_line", 1.0))))
        self.coverage_goal_branch: float = max(0.0, min(1.0, float(config.get("coverage_goal_branch", 1.0))))
        self.last_stop_reason: str = ""

        # 源代码行数（由 Analyzer 设置）
        self.total_source_lines: int = 0

    def start_new_round(self):
        """开始新一轮测试"""
        self.round_number += 1
        self._current_round_generated = 0
        self._current_round_approved = 0
        self._current_round_executed = 0
        self._current_round_new_coverage = 0
        self._current_round_new_crashes = 0
        self._current_round_false_positives = 0

    def record_generated(self, count: int):
        """记录本轮生成的用例数"""
        self._current_round_generated += count
        self.total_generated += count

    def record_approved(self, count: int):
        """记录本轮通过审查的用例数"""
        self._current_round_approved += count

    def record_execution(self, result: ExecutionResult):
        """记录单个执行结果"""
        self._current_round_executed += 1
        self.total_executed += 1

        from core.message_types import TestCaseStatus
        if result.status == TestCaseStatus.PASS:
            self.total_passed += 1
        elif result.status == TestCaseStatus.CRASH:
            self.total_crashed += 1
        elif result.status == TestCaseStatus.TIMEOUT:
            self.total_timeout += 1

        # 更新覆盖率
        new_lines = result.coverage_delta
        if new_lines:
            self._current_round_new_coverage += len(new_lines)
            self.coverage.covered_lines.update(new_lines)

        if result.coverage_lines:
            self.coverage.covered_lines.update(result.coverage_lines)

        if result.branch_total:
            self.coverage.branch_total.update(result.branch_total)

        if result.branch_covered:
            self.coverage.branch_covered.update(result.branch_covered)

        if result.branch_delta:
            self.coverage.branch_covered.update(result.branch_delta)

    def record_confirmed_crash(self, crash: ConfirmedCrash):
        """记录确认的崩溃"""
        if crash.stack_hash and crash.stack_hash in self.known_crash_hashes:
            return

        self.confirmed_crashes.append(crash)
        self.known_crash_hashes.add(crash.stack_hash)
        self._current_round_new_crashes += 1

    def record_false_positive(self):
        """记录误报"""
        self.false_positives += 1
        self._current_round_false_positives += 1

    def finish_round(self) -> RoundSummary:
        """结束当前轮次，返回汇总信息"""
        coverage_rate = self.current_coverage_rate
        self.coverage.coverage_history.append(coverage_rate)

        summary = RoundSummary(
            round_number=self.round_number,
            total_generated=self._current_round_generated,
            reviewer_approved=self._current_round_approved,
            total_executed=self._current_round_executed,
            new_coverage_lines=self._current_round_new_coverage,
            total_coverage_lines=len(self.coverage.covered_lines),
            total_source_lines=self.total_source_lines,
            coverage_rate=coverage_rate,
            new_crashes_found=self._current_round_new_crashes,
            false_positives_caught=self._current_round_false_positives,
            phase=self.phase
        )
        self.round_summaries.append(summary)

        # 计算轮次效率指标
        metrics = RoundMetrics(
            total_generated=self._current_round_generated,
            reviewer_approved=self._current_round_approved,
            approval_rate=(
                self._current_round_approved / max(self._current_round_generated, 1)
            ),
            new_coverage_lines=self._current_round_new_coverage,
            new_crashes_found=self._current_round_new_crashes,
            false_positives_caught=self._current_round_false_positives,
            coverage_per_case=(
                self._current_round_new_coverage / max(self._current_round_executed, 1)
            ),
            useful_case_ratio=(
                (self._current_round_new_coverage + self._current_round_new_crashes)
                / max(self._current_round_executed, 1)
            ),
        )
        self.round_metrics_list.append(metrics)

        if summary.new_coverage_lines > 0 or summary.new_crashes_found > 0:
            self.no_progress_rescue_used = 0
            self.no_progress_rescue_pending = False

        return summary

    def consume_no_progress_rescue_flag(self) -> bool:
        """读取并清空“需要救援轮次”的标记。"""
        pending = bool(self.no_progress_rescue_pending)
        self.no_progress_rescue_pending = False
        return pending

    @property
    def current_coverage_rate(self) -> float:
        """当前覆盖率"""
        if self.total_source_lines == 0:
            return 0.0
        return min(1.0, len(self.coverage.covered_lines) / self.total_source_lines)

    @property
    def elapsed_time(self) -> float:
        """已用时间（秒）"""
        return time.time() - self.start_time

    @property
    def time_remaining(self) -> float:
        """剩余时间（秒）"""
        return max(0, self.max_time_seconds - self.elapsed_time)

    @property
    def is_time_up(self) -> bool:
        """时间是否用完"""
        return self.elapsed_time >= self.max_time_seconds

    @property
    def is_rounds_up(self) -> bool:
        """轮次是否用完"""
        return self.round_number >= self.max_rounds_hard_cap

    @property
    def current_branch_coverage_rate(self) -> float:
        """当前分支覆盖率"""
        if not self.coverage.branch_total:
            return 0.0
        return min(1.0, len(self.coverage.branch_covered) / len(self.coverage.branch_total))

    def is_coverage_goal_reached(self) -> bool:
        """是否达到覆盖目标（语句 + 分支）。"""
        if self.total_source_lines <= 0:
            return False

        line_ok = self.current_coverage_rate >= self.coverage_goal_line

        # 分支全集为空时，表示当前目标暂不可统计分支率，此时仅使用行覆盖目标判定。
        if not self.coverage.branch_total:
            branch_ok = True
        else:
            branch_ok = self.current_branch_coverage_rate >= self.coverage_goal_branch

        return line_ok and branch_ok

    def should_transition_to_deep(self) -> bool:
        """是否应切换到 DEEP 阶段"""
        if self.phase != Phase.EXPLORE:
            return False
        return self.current_coverage_rate >= self.explore_to_deep_threshold

    def should_stop(self) -> bool:
        """是否应终止测试"""
        # 时间预算耗尽
        if self.is_time_up:
            if not self.last_stop_reason:
                self.last_stop_reason = "time_budget_exhausted"
            return True

        # 达到白盒覆盖目标（语句 + 分支）
        if self.is_coverage_goal_reached():
            if not self.last_stop_reason:
                self.last_stop_reason = "coverage_goal_reached"
            return True

        # 达到硬上限轮次
        if self.is_rounds_up:
            if not self.last_stop_reason:
                self.last_stop_reason = "round_hard_cap_reached"
            return True

        # 可选：覆盖率长时间停滞提前停止
        if self.stop_on_stagnation and len(self.coverage.coverage_history) >= self.stagnation_limit:
            recent = self.coverage.coverage_history[-self.stagnation_limit:]
            if len(set(f"{x:.4f}" for x in recent)) <= 1:
                if not self.last_stop_reason:
                    self.last_stop_reason = "coverage_stagnation"
                return True

        # 连续多个轮次既无新覆盖也无新缺陷，判定为低效循环并提前停止。
        if self.no_progress_round_limit > 0 and len(self.round_summaries) >= self.no_progress_round_limit:
            recent_rounds = self.round_summaries[-self.no_progress_round_limit :]
            no_progress = all(
                int(r.new_coverage_lines or 0) <= 0 and int(r.new_crashes_found or 0) <= 0
                for r in recent_rounds
            )
            if no_progress:
                rescue_unlimited = self.no_progress_rescue_max_attempts <= 0
                rescue_available = rescue_unlimited or self.no_progress_rescue_used < self.no_progress_rescue_max_attempts
                rescue_due = (
                    self.round_number - self.no_progress_last_rescue_round
                ) >= self.no_progress_rescue_cooldown_rounds

                if self.no_progress_rescue_enabled and rescue_available and rescue_due:
                    self.no_progress_rescue_used += 1
                    self.no_progress_last_rescue_round = self.round_number
                    self.no_progress_rescue_pending = True
                    return False

                # 默认不因“无进展”提前停止，优先让智能体继续探索。
                if not self.no_progress_stop_enabled:
                    return False

                # 低覆盖阶段不应过早停机。
                if self.round_number < self.no_progress_min_rounds_before_stop:
                    return False
                if self.elapsed_time < self.no_progress_min_seconds_before_stop:
                    return False

                # 若仅因救援冷却导致本轮未触发救援，也继续运行。
                if self.no_progress_rescue_enabled and rescue_available:
                    return False

                if not self.last_stop_reason:
                    self.last_stop_reason = "no_progress_round_limit"
                return True

        return False

    def get_stagnation_rounds(self) -> int:
        """获取覆盖率停滞的轮次数"""
        if len(self.coverage.coverage_history) < 2:
            return 0
        history = self.coverage.coverage_history
        stagnation = 0
        current = history[-1]
        for rate in reversed(history[:-1]):
            if abs(rate - current) < 0.0001:
                stagnation += 1
            else:
                break
        return stagnation

    def __repr__(self):
        return (
            f"GlobalTestState(phase={self.phase.name}, round={self.round_number}, "
            f"line_coverage={self.current_coverage_rate:.2%}, "
            f"branch_coverage={self.current_branch_coverage_rate:.2%}, "
            f"crashes={len(self.confirmed_crashes)}, "
            f"elapsed={self.elapsed_time:.0f}s)"
        )
