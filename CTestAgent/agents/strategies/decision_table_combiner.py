# decision_table_combiner.py - 组合/决策表策略
"""
组合测试/决策表（Decision Table / Combinatorial Testing）策略。

对多条件逻辑构造组合，覆盖关键条件组合与约束分支。
针对 cJSON 的多维度输入特征进行组合覆盖。
"""

import json
import random
import itertools
from core.message_types import TestCase


class DecisionTableCombiner:
    """组合/决策表测试用例生成器"""

    name = "decision_table"

    # JSON 值类型维度
    VALUE_TYPES = ["null", "true", "false", "integer", "float", "string", "array", "object"]

    # JSON 结构特征维度
    STRUCTURES = ["flat", "nested_1", "nested_2", "nested_deep"]

    # 字符串内容维度
    STRING_CONTENTS = ["empty", "ascii", "unicode", "escaped", "long"]

    # 数值范围维度
    NUMBER_RANGES = ["zero", "positive", "negative", "large", "small_float", "scientific"]

    def generate(self, count: int, target_function: str = "cJSON_Parse",
                 **kwargs) -> list[TestCase]:
        """
        生成组合测试用例。

        使用 pairwise 思想，对多个维度进行两两组合覆盖。
        """
        cases = []

        # 维度组合1：根对象类型 × 内部值类型
        combo1_cases = self._combine_root_and_values(target_function)
        cases.extend(combo1_cases)

        # 维度组合2：结构深度 × 值类型
        combo2_cases = self._combine_structure_and_type(target_function)
        cases.extend(combo2_cases)

        # 维度组合3：多字段对象的条件组合
        combo3_cases = self._combine_object_fields(target_function)
        cases.extend(combo3_cases)

        # 维度组合4：数组元素的类型混合
        combo4_cases = self._combine_array_elements(target_function)
        cases.extend(combo4_cases)

        random.shuffle(cases)
        return cases[:count]

    def _combine_root_and_values(self, target_func: str) -> list[TestCase]:
        """组合1：根类型 × 内部值类型"""
        cases = []

        root_types = ["array", "object"]
        value_types = ["null", "true", "false", "0", "1.5", '"str"', "[]", "{}"]

        for root, val in itertools.product(root_types, value_types):
            if root == "array":
                json_str = f"[{val}]"
            else:
                json_str = '{' + f'"key": {val}' + '}'

            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8'),
                strategy=f"decision_table:root_{root}:val_{val[:10]}",
                metadata={"combo": f"root={root},val={val}"}
            ))
        return cases

    def _combine_structure_and_type(self, target_func: str) -> list[TestCase]:
        """组合2：嵌套结构 × 值类型"""
        cases = []

        values = {
            "null": "null",
            "bool": "true",
            "int": "42",
            "float": "3.14",
            "string": '"hello"',
        }

        structures = {
            "flat_array": lambda v: f"[{v}]",
            "flat_object": lambda v: '{' + f'"k": {v}' + '}',
            "nested_array": lambda v: f"[[{v}]]",
            "nested_object": lambda v: '{' + f'"a": ' + '{' + f'"b": {v}' + '}' + '}',
            "array_in_object": lambda v: '{' + f'"arr": [{v}]' + '}',
            "object_in_array": lambda v: '[' + '{' + f'"k": {v}' + '}' + ']',
        }

        for (vname, vval), (sname, sfunc) in itertools.product(
            values.items(), structures.items()
        ):
            json_str = sfunc(vval)
            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8'),
                strategy=f"decision_table:struct_{sname}:type_{vname}",
                metadata={"combo": f"struct={sname},type={vname}"}
            ))
        return cases

    def _combine_object_fields(self, target_func: str) -> list[TestCase]:
        """组合3：多字段对象 — 字段存在性组合（决策表）"""
        cases = []

        # 模拟一个有3个可选字段的对象
        fields = {
            "name": '"Alice"',
            "age": '30',
            "active": 'true',
        }

        # 生成所有字段存在/不存在的组合（2^3 = 8种）
        field_names = list(fields.keys())
        for r in range(len(field_names) + 1):
            for combo in itertools.combinations(field_names, r):
                obj_fields = []
                for fname in combo:
                    obj_fields.append(f'"{fname}": {fields[fname]}')

                json_str = '{' + ', '.join(obj_fields) + '}'
                cases.append(TestCase(
                    target_function=target_func,
                    input_text=json_str,
                    input_data=json_str.encode('utf-8'),
                    strategy=f"decision_table:fields:{'_'.join(combo) if combo else 'empty'}",
                    metadata={"combo": f"fields={combo}"}
                ))
        return cases

    def _combine_array_elements(self, target_func: str) -> list[TestCase]:
        """组合4：数组中不同类型元素的混合"""
        cases = []

        element_types = {
            "null": "null",
            "bool": "true",
            "int": "1",
            "float": "1.5",
            "string": '"s"',
            "empty_array": "[]",
            "empty_object": "{}",
        }

        type_names = list(element_types.keys())

        # 两两组合
        for t1, t2 in itertools.combinations(type_names, 2):
            v1, v2 = element_types[t1], element_types[t2]
            json_str = f"[{v1}, {v2}]"
            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8'),
                strategy=f"decision_table:array_mix:{t1}_{t2}",
                metadata={"combo": f"array=[{t1},{t2}]"}
            ))

        # 三元组合（抽样）
        for t1, t2, t3 in itertools.combinations(type_names, 3):
            v1, v2, v3 = element_types[t1], element_types[t2], element_types[t3]
            json_str = f"[{v1}, {v2}, {v3}]"
            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8'),
                strategy=f"decision_table:array_mix:{t1}_{t2}_{t3}",
                metadata={"combo": f"array=[{t1},{t2},{t3}]"}
            ))

        return cases
