# generator.py - 生成者 Agent
"""
Generator Agent（生成者）：系统的"攻击手"。

负责根据策略权重综合运用多种生成策略产出测试用例：
1. 等价类划分 2. 边界值分析 3. 组合/决策表
4. 状态序列  5. 随机+错误猜测 6. 白盒引导
接收 Reviewer Agent 反馈，自适应调整生成方向。
"""

import random
import logging
import re
import struct
import hashlib
from pathlib import Path
from core.base_agent import BaseAgent
from core.agent_api import normalize_weights
from core.message_bus import MessageBus
from core.message_types import (
    Message, TestCase, GenerateCommand, ReviewFeedback,
    FeedbackType, AnalysisResult, PhaseChangeNotification, Phase
)

from agents.strategies.equivalence_partitioner import EquivalencePartitioner
from agents.strategies.boundary_value import BoundaryValueAnalyzer
from agents.strategies.decision_table_combiner import DecisionTableCombiner
from agents.strategies.state_sequence_generator import StateSequenceGenerator
from agents.strategies.random_error_guesser import RandomErrorGuesser
from agents.strategies.whitebox_guided import WhiteBoxGuidedMutator
from agents.strategies.mutator import Mutator

logger = logging.getLogger(__name__)


class GeneratorAgent(BaseAgent):
    """
    生成者智能体。

    综合运用 6 种测试用例生成策略，根据 Coordinator 的指令
    和 Reviewer 的反馈动态调整策略权重。
    """

    def __init__(self, bus: MessageBus, config: dict = None):  # type: ignore
        super().__init__("Generator", bus, config)

        # 通用 C 场景：默认目标函数留空，优先由 Analyzer 结果驱动
        source_dir = ""
        if config:
            source_dir = str(config.get("source_dir", ""))
        self.default_target_function = str((config or {}).get("entry_function", "") or "").strip()

        # 初始化 6 种策略
        self.strategies = {
            "equivalence": EquivalencePartitioner(),
            "boundary": BoundaryValueAnalyzer(),
            "decision_table": DecisionTableCombiner(),
            "state_sequence": StateSequenceGenerator(),
            "random_error_guess": RandomErrorGuesser(),
            "whitebox_guided": WhiteBoxGuidedMutator(),
        }

        # 默认策略权重（EXPLORE 阶段）
        self.strategy_weights = {
            "equivalence": 0.35,
            "boundary": 0.35,
            "random_error_guess": 0.20,
            "error_guess": 0.10,  # 映射到 random_error_guess
        }

        # 分析结果
        self.analysis_result = None
        self.focus_functions = [self.default_target_function] if self.default_target_function else []
        self.batch_size = config.get("batch_size", 50) if config else 50
        self.api_round_interval = max(1, int((config or {}).get("api_round_interval", 3)))
        self._generate_command_count = 0

        # 种子库
        self.seed_corpus: list[TestCase] = []
        self.generic_c_seed_templates: list[dict] = []
        self.generic_input_profile = "generic"

        # 尝试从目标目录预加载初始种子（如 fuzzgoat/input-files）
        self._preload_target_seeds(source_dir)

    def _preload_target_seeds(self, source_dir: str):
        """从被测目录预加载示例输入作为初始种子"""
        if not source_dir:
            return

        source_path = Path(source_dir)
        input_dir = source_path / "input-files"
        if not input_dir.exists() or not input_dir.is_dir():
            return

        loaded = 0
        for f in sorted(input_dir.iterdir()):
            if not f.is_file():
                continue
            try:
                raw = f.read_bytes()
            except Exception:
                continue

            tc = TestCase(
                target_function=self.default_target_function,
                input_data=raw,
                input_text=raw.decode('utf-8', errors='replace'),
                strategy="seed_file",
                metadata={"seed_file": str(f.name)},
            )
            self.seed_corpus.append(tc)
            loaded += 1

        if loaded > 0:
            self.log_info(f"已从 {input_dir} 预加载 {loaded} 个初始种子")

            # 通知白盒引导策略
            try:
                self.strategies["whitebox_guided"].update_seeds(self.seed_corpus)
            except Exception:
                pass

        self.generic_c_seed_templates = self._build_generic_c_seed_templates(source_path)
        if self.generic_c_seed_templates:
            self.log_info(f"已构建 {len(self.generic_c_seed_templates)} 个通用 C 启发式种子模板")

    def setup(self):
        """注册消息订阅"""
        self.subscribe("cmd:generate", self.on_generate_command)
        self.subscribe("data:analysis_result", self.on_analysis_result)
        self.subscribe("feedback:review", self.on_reviewer_feedback)
        self.subscribe("cmd:phase_change", self.on_phase_change)
        self.subscribe("data:coverage_update", self.on_coverage_update)

    def on_analysis_result(self, msg: Message):
        """接收 Analyzer 的分析结果"""
        self.analysis_result = msg.data
        if isinstance(msg.data, AnalysisResult):
            fallback_funcs = [f.name for f in (msg.data.functions or [])[:5] if getattr(f, 'name', '')]
            self.focus_functions = msg.data.high_risk_functions[:5] or fallback_funcs or self.focus_functions
        self.log_info(f"收到分析结果，重点函数: {self.focus_functions}")

    def on_generate_command(self, msg: Message):
        """接收 Coordinator 的生成指令"""
        cmd = msg.data
        self._generate_command_count += 1
        current_phase = Phase.EXPLORE
        cmd_weights = None
        if isinstance(cmd, GenerateCommand):
            self.strategy_weights = cmd.strategy_weights or self.strategy_weights
            self.focus_functions = cmd.focus_functions or self.focus_functions
            batch_size = cmd.batch_size or self.batch_size
            current_phase = cmd.phase
            cmd_weights = dict(cmd.strategy_weights or {})
        else:
            batch_size = self.batch_size

        self.log_info(f"开始生成 {batch_size} 个测试用例 (权重: {self._format_weights()})")
        self.log_info(
            f"[OBS][generate_command] index={self._generate_command_count}, phase={current_phase.name if isinstance(current_phase, Phase) else current_phase}, "
            f"batch={batch_size}, cmd_weights={cmd_weights}, focus={self.focus_functions[:5]}"
        )

        heuristic_cases = self._generate_generic_c_seed_cases(batch_size, current_phase)

        # API 增强：允许在本地策略前做权重微调，并注入少量高价值种子输入。
        api_seed_cases = self._apply_api_generation_plan(batch_size, current_phase)
        remaining = max(0, batch_size - len(heuristic_cases) - len(api_seed_cases))
        profile_cases = self._generate_profile_cases(remaining, current_phase)
        remaining = max(0, remaining - len(profile_cases))
        strategy_cases = self._generate_batch(remaining)
        test_cases_raw = heuristic_cases + api_seed_cases + profile_cases + strategy_cases
        test_cases = self._deduplicate_cases(test_cases_raw, batch_size)
        refill_count = 0

        # 去重后若不足，补齐一轮本地策略，保证吞吐。
        if len(test_cases) < batch_size:
            refill = self._generate_batch(batch_size - len(test_cases))
            refill_count = len(refill)
            test_cases = self._deduplicate_cases(test_cases + refill, batch_size)

        if len(test_cases) > batch_size:
            test_cases = test_cases[:batch_size]

        self.log_info(
            f"[OBS][generate_counts] heuristic={len(heuristic_cases)}, api_seed={len(api_seed_cases)}, "
            f"profile={len(profile_cases)}, strategy={len(strategy_cases)}, refill={refill_count}, "
            f"raw_total={len(test_cases_raw)}, final_total={len(test_cases)}"
        )

        self.log_info(f"已生成 {len(test_cases)} 个测试用例")
        self.publish("data:test_cases", test_cases)

    def on_reviewer_feedback(self, msg: Message):
        """接收 Reviewer 的质量反馈，自适应调整"""
        feedback = msg.data
        if not isinstance(feedback, ReviewFeedback):
            return

        self.log_info(f"收到 Reviewer 反馈: {feedback.type.value} - {feedback.message}")

        if feedback.type == FeedbackType.COVERAGE_GAP:
            # 针对未覆盖区域，使用白盒引导定向补洞
            gap_cases = self.strategies["whitebox_guided"].generate(
                count=20,
                target_function=self.focus_functions[0] if self.focus_functions else self.default_target_function,
                target_lines=feedback.uncovered_lines,
            )
            self.log_info(f"针对覆盖率缺口生成 {len(gap_cases)} 个用例")
            self.publish("data:test_cases", gap_cases)

        elif feedback.type == FeedbackType.LOW_DIVERSITY:
            # 增大随机/错误猜测权重
            self.strategy_weights["random_error_guess"] = self.strategy_weights.get("random_error_guess", 0.2) + 0.15
            self._normalize_weights()
            self.log_info(f"增大随机权重，新权重: {self._format_weights()}")

        elif feedback.type == FeedbackType.DECISION_TABLE_GAP:
            # 对决策表缺失组合做补全
            combo_cases = self.strategies["decision_table"].generate(
                count=30,
                target_function=self.focus_functions[0] if self.focus_functions else self.default_target_function,
            )
            self.log_info(f"针对决策表缺口生成 {len(combo_cases)} 个用例")
            self.publish("data:test_cases", combo_cases)

        elif feedback.type == FeedbackType.REDUNDANT_CASES:
            # 从种子队列中移除低价值种子
            ids_to_remove = set(feedback.redundant_ids)
            self.seed_corpus = [s for s in self.seed_corpus if s.id not in ids_to_remove]
            self.log_info(f"清理 {len(ids_to_remove)} 个冗余种子")

        elif feedback.type == FeedbackType.PROMISING_AREA:
            # 集中力量对有希望的区域密集变异
            self.focus_functions = feedback.promising_functions or self.focus_functions
            intensive_cases = self._generate_intensive(count=50)
            self.log_info(f"针对有希望的区域生成 {len(intensive_cases)} 个密集变异用例")
            self.publish("data:test_cases", intensive_cases)

        elif feedback.type == FeedbackType.STAGNATION:
            # 覆盖率停滞，大幅增加变异多样性
            self.strategy_weights = {
                "random_error_guess": 0.40,
                "whitebox_guided": 0.30,
                "boundary": 0.15,
                "equivalence": 0.15,
            }
            self.log_info("覆盖率停滞，切换到高多样性策略")

        self.log_info(
            f"[OBS][feedback_applied] type={feedback.type.value}, "
            f"weights={self._format_weights()}, focus={self.focus_functions[:5]}, seed_pool={len(self.seed_corpus)}"
        )

    def on_phase_change(self, msg: Message):
        """接收阶段切换通知"""
        if isinstance(msg.data, PhaseChangeNotification):
            notification = msg.data
            if notification.new_strategy_weights:
                self.strategy_weights = notification.new_strategy_weights
            self.log_info(
                f"阶段切换: {notification.old_phase.name} -> {notification.new_phase.name}, "
                f"新权重: {self._format_weights()}"
            )

    def on_coverage_update(self, msg: Message):
        """接收覆盖率更新，将有价值的用例加入种子库"""
        # 这里接收的是有新覆盖的用例，加入白盒引导的种子库
        if isinstance(msg.data, list):
            interesting = [tc for tc in msg.data if isinstance(tc, TestCase)]
            if interesting:
                before = len(self.seed_corpus)
                self.seed_corpus.extend(interesting)
                self.strategies["whitebox_guided"].update_seeds(interesting)
                # 控制种子库大小
                if len(self.seed_corpus) > 500:
                    self.seed_corpus = self.seed_corpus[-500:]
                self.log_info(
                    f"[OBS][seed_update] incoming={len(interesting)}, before={before}, after={len(self.seed_corpus)}"
                )
                return

            self.log_info("[OBS][seed_update] incoming=0, skip")
            return

        self.log_info(f"[OBS][seed_update] unexpected_payload_type={type(msg.data).__name__}")

    def _generate_batch(self, batch_size: int) -> list[TestCase]:
        """根据策略权重生成一批测试用例"""
        cases = []

        # 计算每种策略应生成的数量
        strategy_counts = self._allocate_by_weights(batch_size)

        for strategy_name, count in strategy_counts.items():
            if count <= 0:
                continue

            # 映射策略名到实际策略对象
            actual_name = self._resolve_strategy_name(strategy_name)
            strategy = self.strategies.get(actual_name)

            if strategy is None:
                self.log_warning(f"未知策略: {strategy_name}")
                continue

            target_func = random.choice(self.focus_functions) if self.focus_functions else self.default_target_function

            try:
                generated = strategy.generate(
                    count=count,
                    target_function=target_func,
                )
                cases.extend(generated)
                self.log_debug(f"策略 {strategy_name} 生成 {len(generated)} 个用例")
            except Exception as e:
                self.log_error(f"策略 {strategy_name} 生成失败: {e}")

        return cases

    def _generate_intensive(self, count: int = 50) -> list[TestCase]:
        """密集变异生成（用于有希望的区域）"""
        cases = []

        # 如果有种子，基于种子密集变异
        if self.seed_corpus:
            for _ in range(count):
                seed = random.choice(self.seed_corpus)
                mutated_data = Mutator.apply_random_mutation(seed.input_data)
                cases.append(TestCase(
                    target_function=seed.target_function or self.default_target_function,
                    input_data=mutated_data,
                    input_text=mutated_data.decode('utf-8', errors='replace'),
                    strategy="intensive_mutation",
                    parent_id=seed.id,
                ))
        else:
            # 无种子时，使用多策略混合
            cases = self._generate_batch(count)

        return cases

    def _allocate_by_weights(self, total: int) -> dict[str, int]:
        """根据权重分配各策略的生成数量"""
        counts = {}
        remaining = total

        # 过滤并标准化权重
        weights = {k: v for k, v in self.strategy_weights.items() if v > 0}
        total_weight = sum(weights.values())

        if total_weight == 0:
            # 均匀分配
            per = total // len(self.strategies)
            for name in self.strategies:
                counts[name] = per
            return counts

        for name, weight in weights.items():
            count = int(total * (weight / total_weight))
            counts[name] = count
            remaining -= count

        # 将剩余分配给权重最大的策略
        if remaining > 0 and weights:
            max_strategy = max(weights, key=lambda k: weights[k])
            counts[max_strategy] = counts.get(max_strategy, 0) + remaining

        return counts

    def _resolve_strategy_name(self, name: str) -> str:
        """解析策略名到实际策略对象名"""
        mapping = {
            "equivalence": "equivalence",
            "boundary": "boundary",
            "decision_table": "decision_table",
            "state_sequence": "state_sequence",
            "random": "random_error_guess",
            "random_error_guess": "random_error_guess",
            "error_guess": "random_error_guess",
            "whitebox_guided": "whitebox_guided",
        }
        return mapping.get(name, name)

    def _normalize_weights(self):
        """归一化策略权重"""
        total = sum(self.strategy_weights.values())
        if total > 0:
            self.strategy_weights = {
                k: v / total for k, v in self.strategy_weights.items()
            }

    def _format_weights(self) -> str:
        """格式化权重信息用于日志"""
        return ", ".join(f"{k}={v:.0%}" for k, v in self.strategy_weights.items())

    def _build_generic_c_seed_templates(self, source_path: Path) -> list[dict]:
        """为通用 C 目标构造启发式种子模板（字符串 + 二进制）。"""
        if not source_path.exists():
            return []

        text_parts = []
        for c_file in sorted(source_path.glob("*.c")):
            try:
                text_parts.append(c_file.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
        all_text = "\n".join(text_parts)
        lower_text = all_text.lower()

        templates: list[dict] = []

        looks_like_expression_target = (
            "sscanf(" in lower_text and
            "%c" in lower_text and
            any(token in lower_text for token in ["argv[1]", "gets(", "fgets("])
        )

        # 通用文本种子：优先覆盖合法路径与典型异常路径。
        text_seeds = [
            "",
            "0",
            "1 + 1",
            "2147483647 + 1",
            "-2147483648 - 1",
            "8 / 0",
            "%x%x%x%n",
            "A" * 80,
            "1 * 1",
            "100 - 50",
        ]

        if looks_like_expression_target:
            self.generic_input_profile = "expression_text"
            text_seeds.extend([
                "10 + 20",
                "999999999 + 999999999",
                "1 / 0",
                "-1 * -1",
                "0 - 2147483647",
                "2147483647 + 2147483647",
                "-2147483648 / -1",
                "7*8",
                "12    +    34",
                "100/5",
                "1 +",
                "+ 1 2",
                "abc + 1",
                "1 + abc",
                "%x %x %n",
                "1 + 2 + 3",
                "1 / 000000",
            ])
        else:
            self.generic_input_profile = "generic_text"

        for seed in text_seeds:
            templates.append({"kind": "text", "payload": seed})

        # 文件型二进制输入目标（如 fread struct）
        looks_like_binary_file_target = (
            "fread(" in lower_text and
            "argv[1]" in lower_text and
            "struct" in lower_text
        )
        if looks_like_binary_file_target:
            self.generic_input_profile = "binary_file"
            def build_image_like(width: int, height: int, data: bytes) -> bytes:
                header = b"IMG\x00"
                return header + struct.pack("<i", width) + struct.pack("<i", height) + data[:10].ljust(10, b"\x00")

            binary_seeds = [
                build_image_like(16, 16, b"ABCDEFGHIJ"),
                build_image_like(0x7FFFFFFF, 1, b"AAAAAAAAAA"),
                build_image_like(1, 0, b"BBBBBBBBBB"),
                build_image_like(-1, 200, b"CCCCCCCCCC"),
                build_image_like(1024, 2, b"DDDDEEEEFF"),
            ]

            for blob in binary_seeds:
                templates.append({"kind": "binary", "payload": blob})

        return templates

    def _generate_generic_c_seed_cases(self, batch_size: int, phase: Phase) -> list[TestCase]:
        """在热身阶段注入通用 C 种子，快速覆盖基础可达路径。"""
        if not self.generic_c_seed_templates or batch_size <= 0:
            return []

        if phase == Phase.FINALIZE:
            return []

        if self.generic_input_profile == "expression_text":
            limit = min(max(8, batch_size // 2), 24)
        else:
            limit = min(max(2, batch_size // 3), 12)

        selected = random.sample(
            self.generic_c_seed_templates,
            k=min(limit, len(self.generic_c_seed_templates)),
        )

        target_func = self.focus_functions[0] if self.focus_functions else self.default_target_function
        cases: list[TestCase] = []
        for item in selected:
            kind = str(item.get("kind") or "text")
            payload = item.get("payload")
            if kind == "binary" and isinstance(payload, (bytes, bytearray)):
                data = bytes(payload)
                text = data.hex()[:240]
            else:
                text = str(payload or "")
                data = text.encode("utf-8", errors="replace")

            cases.append(TestCase(
                target_function=target_func,
                input_data=data,
                input_text=text,
                strategy="generic_c_seed",
                metadata={
                    "source": "generic_seed_template",
                    "kind": kind,
                    "phase": phase.name if isinstance(phase, Phase) else str(phase),
                },
            ))

        return cases

    def _generate_profile_cases(self, remaining: int, phase: Phase) -> list[TestCase]:
        """根据识别到的输入画像补充定向样例。"""
        if remaining <= 0:
            return []

        if self.generic_input_profile == "expression_text":
            return self._generate_expression_guided_cases(remaining, phase)

        return []

    def _generate_expression_guided_cases(self, remaining: int, phase: Phase) -> list[TestCase]:
        """为表达式解析类目标生成高命中率输入，减少无效 JSON/乱码占比。"""
        if remaining <= 0:
            return []

        target_func = self.focus_functions[0] if self.focus_functions else self.default_target_function
        limit = min(remaining, max(10, remaining // 2))

        boundary_numbers = [
            -2147483648,
            -100000,
            -1,
            0,
            1,
            2,
            7,
            10,
            42,
            99999,
            2147483647,
        ]
        operators = ["+", "-", "*", "/"]
        malformed_templates = [
            "1 +",
            "+ 1 2",
            "1 ? 2",
            "abc + 1",
            "1 + abc",
            "",
            "   ",
            "%x%x%x%n",
            "1 + 2 + 3",
        ]

        cases: list[TestCase] = []
        for _ in range(limit):
            roll = random.random()
            if roll < 0.68:
                left = random.choice(boundary_numbers)
                right = random.choice(boundary_numbers)
                if random.random() < 0.35:
                    right = random.randint(-500000, 500000)
                op = random.choice(operators)

                left_pad = " " * random.randint(0, 3)
                mid_pad_l = " " * random.randint(0, 4)
                mid_pad_r = " " * random.randint(0, 4)
                right_pad = " " * random.randint(0, 3)
                payload = f"{left_pad}{left}{mid_pad_l}{op}{mid_pad_r}{right}{right_pad}".strip()
            elif roll < 0.9:
                payload = random.choice(malformed_templates)
            else:
                huge_left = random.choice([2147483647, 999999999, -2147483648])
                huge_right = random.choice([0, 1, -1, 2147483647])
                payload = f"{huge_left} {random.choice(operators)} {huge_right}"

            if not payload:
                payload = "0 + 0"

            data = payload.encode("utf-8", errors="replace")
            cases.append(TestCase(
                target_function=target_func,
                input_data=data,
                input_text=payload,
                strategy="expression_guided",
                metadata={
                    "source": "profile",
                    "profile": "expression_text",
                    "phase": phase.name if isinstance(phase, Phase) else str(phase),
                },
            ))

        return cases

    def _deduplicate_cases(self, cases: list[TestCase], limit: int) -> list[TestCase]:
        """批次内去重，减少明显重复样例导致的无效执行。"""
        if not cases or limit <= 0:
            return []

        seen: set[str] = set()
        deduped: list[TestCase] = []
        for case in cases:
            payload = case.input_data or (case.input_text or "").encode("utf-8", errors="replace")
            key = hashlib.sha1(payload).hexdigest()
            if key in seen:
                continue

            seen.add(key)
            deduped.append(case)
            if len(deduped) >= limit:
                break

        return deduped

    def _apply_api_generation_plan(self, batch_size: int, phase: Phase) -> list[TestCase]:
        """调用 API 提供生成规划建议；失败时不影响原本地策略。"""
        if not self.api_advisor.enabled:
            return []
        if self._generate_command_count % self.api_round_interval != 0:
            return []

        focus = list(self.focus_functions[:8])
        top_constraints = {}
        if isinstance(self.analysis_result, AnalysisResult) and self.analysis_result.param_constraints:
            for name in focus[:5]:
                ranges = self.analysis_result.param_constraints.get(name, [])[:3]
                top_constraints[name] = [
                    {
                        "param": r.param_name,
                        "valid": r.valid_values[:3],
                        "boundary": r.boundary_values[:3],
                    }
                    for r in ranges
                ]

        context = {
            "phase": phase.name if isinstance(phase, Phase) else str(phase),
            "batch_size": batch_size,
            "focus_functions": focus,
            "strategy_weights": self.strategy_weights,
            "seed_pool_size": len(self.seed_corpus),
            "param_constraints": top_constraints,
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
            "seed_inputs": ["{\"k\":\"v\"}"],
            "reason": "short explanation",
        }

        decision = self.api_advisor.ask_json(
            task="基于当前阶段与风险约束，给出测试生成策略权重和少量高价值输入",
            context=context,
            expected_schema=expected_schema,
            max_tokens=700,
            temperature=0.2,
        )
        if not decision:
            return []

        allowed = {
            "equivalence",
            "boundary",
            "decision_table",
            "state_sequence",
            "whitebox_guided",
            "random_error_guess",
            "error_guess",
        }
        api_weights = normalize_weights(decision.get("strategy_weights", {}), allowed_keys=allowed)
        if api_weights:
            self.strategy_weights = api_weights

        api_focus = [str(x).strip() for x in decision.get("focus_functions", []) if str(x).strip()]
        if api_focus:
            self.focus_functions = api_focus[:8]

        api_seed_cases = self._build_api_seed_cases(decision.get("seed_inputs", []), batch_size, phase)

        reason = str(decision.get("reason", "")).strip()
        if reason:
            self.log_info(f"API 生成规划已应用: {reason}")

        return api_seed_cases

    def _build_api_seed_cases(self, seed_inputs: object, batch_size: int, phase: Phase) -> list[TestCase]:
        """将 API 返回的输入样例转为 TestCase。"""
        if not isinstance(seed_inputs, list) or batch_size <= 0:
            return []

        cases: list[TestCase] = []
        limit = min(max(1, batch_size // 4), 12)

        for item in seed_inputs:
            input_text = ""
            target_func = ""
            if isinstance(item, str):
                input_text = item
            elif isinstance(item, dict):
                input_text = str(item.get("input_text") or item.get("text") or "")
                target_func = str(item.get("target_function") or "").strip()

            input_text = input_text.strip()
            if not input_text:
                continue

            chosen_target = target_func or (self.focus_functions[0] if self.focus_functions else self.default_target_function)
            payload = input_text.encode("utf-8", errors="replace")

            cases.append(TestCase(
                target_function=chosen_target,
                input_data=payload,
                input_text=input_text,
                strategy="api_seed_hint",
                metadata={
                    "source": "api",
                    "phase": phase.name if isinstance(phase, Phase) else str(phase),
                },
            ))

            if len(cases) >= limit:
                break

        return cases
