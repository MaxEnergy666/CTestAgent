# whitebox_guided.py - 白盒引导策略
"""
白盒引导变异（White-Box Guided Mutation）策略。

依据覆盖率、分支缺口与关键路径反馈进行定向变异。
优先变异能触发新路径覆盖的种子。
"""

import random
import json
import logging
from typing import Optional
from core.message_types import TestCase


logger = logging.getLogger(__name__)


class WhiteBoxGuidedMutator:
    """白盒引导变异测试用例生成器"""

    name = "whitebox_guided"

    def __init__(self):
        self.seed_corpus: list[TestCase] = []       # 能触发新覆盖的种子
        self.coverage_map: dict[str, set] = {}       # 输入 -> 覆盖行

    def update_seeds(self, interesting_cases: list[TestCase]):
        """更新种子库（由覆盖率反馈触发）"""
        before = len(self.seed_corpus)
        self.seed_corpus.extend(interesting_cases)
        # 只保留最近的种子，避免占用过多内存
        if len(self.seed_corpus) > 1000:
            self.seed_corpus = self.seed_corpus[-1000:]
        logger.info(
            f"[OBS][whitebox_seed_update] incoming={len(interesting_cases)}, before={before}, after={len(self.seed_corpus)}"
        )

    def generate(self, count: int, target_function: str = "cJSON_Parse",
                 target_lines: Optional[list[str]] = None, **kwargs) -> list[TestCase]:
        """
        生成白盒引导的测试用例。

        如果有种子库，基于种子变异；否则退化为智能随机生成。
        如果指定了 target_lines，尝试生成针对性输入。
        """
        target_line_count = len(target_lines or [])
        logger.info(
            f"[OBS][whitebox_generate] count={count}, target_function={target_function}, "
            f"target_lines={target_line_count}, seed_pool={len(self.seed_corpus)}"
        )

        cases = []
        mode = "structured"

        if self.seed_corpus:
            # 基于种子变异
            mode = "seed_mutation"
            for _ in range(count):
                seed = random.choice(self.seed_corpus)
                mutated = self._mutate_seed(seed)
                mutated.target_function = target_function
                mutated.strategy = "whitebox_guided:seed_mutation"
                mutated.parent_id = seed.id
                cases.append(mutated)
        else:
            # 无种子时，生成结构多样的 JSON 输入
            for _ in range(count):
                json_str = self._generate_structured_json()
                cases.append(TestCase(
                    target_function=target_function,
                    input_text=json_str,
                    input_data=json_str.encode('utf-8', errors='replace'),
                    strategy="whitebox_guided:structured",
                ))

        logger.info(
            f"[OBS][whitebox_generate_done] mode={mode}, produced={len(cases)}, seed_pool={len(self.seed_corpus)}"
        )

        return cases

    def _mutate_seed(self, seed: TestCase) -> TestCase:
        """对种子进行变异"""
        input_text = seed.input_text
        if not input_text:
            input_text = seed.input_data.decode('utf-8', errors='replace')

        # 多种变异操作
        mutations = [
            self._mutate_value_in_json,
            self._mutate_structure,
            self._mutate_string_content,
            self._mutate_number_value,
            self._mutate_add_field,
            self._mutate_remove_field,
            self._mutate_flip_type,
            self._mutate_byte_level,
        ]

        mutated_text = input_text
        # 应用 1~3 次变异
        for _ in range(random.randint(1, 3)):
            mutation = random.choice(mutations)
            mutated_text = mutation(mutated_text)

        return TestCase(
            target_function=seed.target_function,
            input_text=mutated_text,
            input_data=mutated_text.encode('utf-8', errors='replace'),
            strategy="whitebox_guided:mutated",
            parent_id=seed.id,
            metadata={"mutation": "seed_based"}
        )

    def _mutate_value_in_json(self, text: str) -> str:
        """尝试解析 JSON 并修改其中的值"""
        try:
            obj = json.loads(text)
            obj = self._mutate_json_value(obj)
            return json.dumps(obj)
        except (json.JSONDecodeError, TypeError, ValueError):
            return text

    def _mutate_json_value(self, obj):
        """递归修改 JSON 对象中的值"""
        if isinstance(obj, dict):
            if obj:
                key = random.choice(list(obj.keys()))
                obj[key] = self._random_json_value()
            return obj
        elif isinstance(obj, list):
            if obj:
                idx = random.randint(0, len(obj) - 1)
                obj[idx] = self._random_json_value()
            return obj
        else:
            return self._random_json_value()

    def _mutate_structure(self, text: str) -> str:
        """修改 JSON 结构"""
        try:
            obj = json.loads(text)
            # 包裹或展开一层
            if random.random() < 0.5:
                return json.dumps([obj])       # 包裹成数组
            else:
                return json.dumps({"wrapped": obj})  # 包裹成对象
        except (json.JSONDecodeError, TypeError):
            return text

    def _mutate_string_content(self, text: str) -> str:
        """修改字符串内容"""
        if not text:
            return text
        pos = random.randint(0, len(text) - 1)
        char = chr(random.randint(32, 126))
        return text[:pos] + char + text[pos + 1:]

    def _mutate_number_value(self, text: str) -> str:
        """修改数值"""
        try:
            obj = json.loads(text)
            return json.dumps(self._tweak_numbers(obj))
        except (json.JSONDecodeError, TypeError):
            return text

    def _tweak_numbers(self, obj):
        """递归调整数值"""
        if isinstance(obj, (int, float)):
            tweaks = [
                lambda x: x + 1,
                lambda x: x - 1,
                lambda x: x * 2,
                lambda x: -x,
                lambda x: 0,
                lambda x: 2**31 - 1,
                lambda x: -2**31,
            ]
            return random.choice(tweaks)(obj)
        elif isinstance(obj, dict):
            for k in obj:
                if isinstance(obj[k], (int, float)):
                    obj[k] = self._tweak_numbers(obj[k])
                    break
            return obj
        elif isinstance(obj, list):
            for i in range(len(obj)):
                if isinstance(obj[i], (int, float)):
                    obj[i] = self._tweak_numbers(obj[i])
                    break
            return obj
        return obj

    def _mutate_add_field(self, text: str) -> str:
        """添加字段"""
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                key = f"new_field_{random.randint(0, 999)}"
                obj[key] = self._random_json_value()
                return json.dumps(obj)
            elif isinstance(obj, list):
                obj.append(self._random_json_value())
                return json.dumps(obj)
        except (json.JSONDecodeError, TypeError):
            pass
        return text

    def _mutate_remove_field(self, text: str) -> str:
        """删除字段"""
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and obj:
                key = random.choice(list(obj.keys()))
                del obj[key]
                return json.dumps(obj)
            elif isinstance(obj, list) and obj:
                idx = random.randint(0, len(obj) - 1)
                obj.pop(idx)
                return json.dumps(obj)
        except (json.JSONDecodeError, TypeError):
            pass
        return text

    def _mutate_flip_type(self, text: str) -> str:
        """翻转值的类型"""
        try:
            obj = json.loads(text)
            return json.dumps(self._flip_value_type(obj))
        except (json.JSONDecodeError, TypeError):
            return text

    def _flip_value_type(self, obj):
        """将值转换为不同类型"""
        if obj is None:
            return random.choice([0, "", False, []])
        elif isinstance(obj, bool):
            return random.choice([0, "false", None, []])
        elif isinstance(obj, (int, float)):
            return random.choice([str(obj), None, True, [obj]])
        elif isinstance(obj, str):
            return random.choice([len(obj), None, True, [obj]])
        elif isinstance(obj, list):
            return random.choice([len(obj), None, {"array": obj}])
        elif isinstance(obj, dict):
            return random.choice([list(obj.values()), None, len(obj)])
        return obj

    def _mutate_byte_level(self, text: str) -> str:
        """字节级别变异"""
        if not text:
            return text

        data = bytearray(text.encode('utf-8', errors='replace'))
        if not data:
            return text

        ops = ['flip', 'insert', 'delete', 'replace']
        op = random.choice(ops)

        pos = random.randint(0, len(data) - 1)

        if op == 'flip':
            bit_pos = random.randint(0, 7)
            data[pos] ^= (1 << bit_pos)
        elif op == 'insert':
            data.insert(pos, random.randint(0, 255))
        elif op == 'delete' and len(data) > 1:
            data.pop(pos)
        elif op == 'replace':
            data[pos] = random.randint(0, 255)

        return data.decode('utf-8', errors='replace')

    def _random_json_value(self):
        """生成随机 JSON 值"""
        choices = [
            None,
            True, False,
            random.randint(-1000, 1000),
            random.uniform(-1000, 1000),
            ''.join(random.choices('abcdefghij', k=random.randint(0, 20))),
            [],
            {},
        ]
        return random.choice(choices)

    def _generate_structured_json(self) -> str:
        """生成结构化的 JSON 输入"""
        generators = [
            self._gen_object_with_types,
            self._gen_nested_structure,
            self._gen_array_with_mixed,
        ]
        return random.choice(generators)()

    def _gen_object_with_types(self) -> str:
        """生成包含多种类型值的对象"""
        obj = {}
        type_map = {
            "null_val": None,
            "bool_val": random.choice([True, False]),
            "int_val": random.randint(-1000, 1000),
            "float_val": random.uniform(-100, 100),
            "str_val": "test_string",
            "arr_val": [1, 2, 3],
            "obj_val": {"nested": True},
        }
        # 随机选择几个类型
        keys = random.sample(list(type_map.keys()), random.randint(2, len(type_map)))
        for k in keys:
            obj[k] = type_map[k]
        return json.dumps(obj)

    def _gen_nested_structure(self) -> str:
        """生成随机嵌套结构"""
        depth = random.randint(1, 5)
        result = self._random_json_value()
        for _ in range(depth):
            if random.random() < 0.5:
                result = [result]
            else:
                result = {"level": result}
        return json.dumps(result)

    def _gen_array_with_mixed(self) -> str:
        """生成混合类型数组"""
        length = random.randint(1, 15)
        arr = [self._random_json_value() for _ in range(length)]
        return json.dumps(arr)
