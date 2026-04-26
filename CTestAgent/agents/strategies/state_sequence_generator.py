# state_sequence_generator.py - 序列与状态策略
"""
序列与状态测试（State/Sequence Testing）策略。

基于 cJSON API 的状态机/调用序列，生成事件流测试用例。
覆盖 创建→操作→查询→删除 等关键状态转换。
"""

import random
import json
from core.message_types import TestCase


class StateSequenceGenerator:
    """序列与状态测试用例生成器"""

    name = "state_sequence"

    # cJSON API 调用序列模板
    # 每个序列模拟一系列 JSON 操作的输入
    SEQUENCE_TEMPLATES = [
        # 序列1：解析→查询→打印（基本流程）
        {
            "name": "parse_query_print",
            "inputs": [
                '{"name": "Alice", "age": 30, "scores": [90, 85, 95]}',
                '{"data": {"nested": true}, "list": [1, 2, 3]}',
            ]
        },
        # 序列2：解析→修改→重新序列化
        {
            "name": "parse_modify_reserialize",
            "inputs": [
                '{"counter": 0}',
                '{"items": []}',
                '{"config": {"debug": false, "level": 1}}',
            ]
        },
        # 序列3：解析空结构→逐步添加→查询
        {
            "name": "build_from_empty",
            "inputs": [
                '{}',
                '[]',
                '{"users": []}',
            ]
        },
        # 序列4：解析→深度遍历→比较
        {
            "name": "parse_traverse_compare",
            "inputs": [
                '{"a": {"b": {"c": {"d": 1}}}}',
                '[[[[1, 2], [3, 4]], [[5, 6], [7, 8]]]]',
            ]
        },
        # 序列5：解析→删除元素→验证
        {
            "name": "parse_delete_verify",
            "inputs": [
                '{"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}',
                '[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]',
            ]
        },
        # 序列6：解析→复制→修改副本→比较
        {
            "name": "parse_duplicate_modify",
            "inputs": [
                '{"original": true, "data": [1, 2, 3]}',
                '{"nested": {"deep": {"value": 42}}}',
            ]
        },
        # 序列7：多次解析不同类型（状态切换）
        {
            "name": "multi_type_parsing",
            "inputs": [
                'null',
                'true',
                '42',
                '"string"',
                '[1, 2]',
                '{"k": "v"}',
            ]
        },
        # 序列8：大对象操作序列
        {
            "name": "large_object_operations",
            "inputs": [
                json.dumps({f"key_{i}": i for i in range(50)}),
                json.dumps([{f"field_{j}": j * 10} for j in range(20)]),
            ]
        },
    ]

    def generate(self, count: int, target_function: str = "cJSON_Parse",
                 **kwargs) -> list[TestCase]:
        """生成状态序列测试用例"""
        cases = []

        # 从预定义序列中提取
        for template in self.SEQUENCE_TEMPLATES:
            seq_name = template["name"]
            for i, input_str in enumerate(template["inputs"]):
                cases.append(TestCase(
                    target_function=target_function,
                    input_text=input_str,
                    input_data=input_str.encode('utf-8'),
                    strategy=f"state_sequence:{seq_name}:step_{i}",
                    metadata={
                        "sequence": seq_name,
                        "step": i,
                        "total_steps": len(template["inputs"])
                    }
                ))

        # 动态生成更多序列用例
        while len(cases) < count:
            extra = self._generate_dynamic_sequence(target_function)
            cases.extend(extra)

        random.shuffle(cases)
        return cases[:count]

    def _generate_dynamic_sequence(self, target_func: str) -> list[TestCase]:
        """动态生成状态序列测试用例"""
        cases = []

        # 生成渐进复杂度的 JSON
        complexities = [
            self._gen_progressive_object,
            self._gen_progressive_array,
            self._gen_type_transition,
        ]

        gen_func = random.choice(complexities)
        inputs = gen_func()

        for i, input_str in enumerate(inputs):
            cases.append(TestCase(
                target_function=target_func,
                input_text=input_str,
                input_data=input_str.encode('utf-8'),
                strategy=f"state_sequence:dynamic:step_{i}",
                metadata={"sequence": "dynamic", "step": i}
            ))

        return cases

    def _gen_progressive_object(self) -> list[str]:
        """生成渐进复杂的对象序列"""
        results = ['{}']
        obj = {}
        for i in range(random.randint(3, 8)):
            key = f"field_{i}"
            value_choices = [i, f"str_{i}", i * 1.5, True, None, [i], {"sub": i}]
            obj[key] = random.choice(value_choices)
            results.append(json.dumps(obj))
        return results

    def _gen_progressive_array(self) -> list[str]:
        """生成渐进增长的数组序列"""
        results = ['[]']
        arr = []
        for i in range(random.randint(3, 10)):
            value_choices = [i, f"item_{i}", None, True, [i]]
            arr.append(random.choice(value_choices))
            results.append(json.dumps(arr))
        return results

    def _gen_type_transition(self) -> list[str]:
        """生成类型转换序列"""
        types = [
            'null', 'true', 'false',
            '0', '42', '-1', '3.14',
            '""', '"hello"',
            '[]', '[1]', '[1, 2, 3]',
            '{}', '{"a": 1}',
        ]
        random.shuffle(types)
        return types[:random.randint(4, 8)]
