# equivalence_partitioner.py - 等价类划分策略
"""
等价类划分（Equivalence Partitioning）策略。

将输入域划分为有效类和无效类，从每类中选取代表值。
针对 cJSON_Parse 的 JSON 字符串输入，划分 JSON 类型等价类。
"""

import json
import random
from core.message_types import TestCase


class EquivalencePartitioner:
    """等价类划分测试用例生成器"""

    name = "equivalence"

    # JSON 有效等价类
    VALID_CLASSES = {
        "null": [
            "null",
        ],
        "boolean_true": [
            "true",
        ],
        "boolean_false": [
            "false",
        ],
        "integer_positive": [
            "0", "1", "42", "100", "999999",
        ],
        "integer_negative": [
            "-1", "-42", "-999999",
        ],
        "float_positive": [
            "0.0", "1.5", "3.14159", "1e10", "1.23e4",
        ],
        "float_negative": [
            "-0.0", "-1.5", "-3.14", "-1e10",
        ],
        "float_special": [
            "1e-10", "1e20", "1.7976931348623157e308",
        ],
        "string_empty": [
            '""',
        ],
        "string_simple": [
            '"hello"', '"world"', '"test"',
        ],
        "string_with_escapes": [
            '"hello\\nworld"', '"tab\\there"', '"quote\\"here"',
            '"backslash\\\\"', '"slash\\/"',
        ],
        "string_unicode": [
            '"\\u0041"', '"\\u4e2d\\u6587"', '"\\u0000"',
        ],
        "array_empty": [
            "[]",
        ],
        "array_single": [
            "[1]", '["a"]', "[null]", "[true]",
        ],
        "array_multiple": [
            "[1, 2, 3]", '["a", "b", "c"]', "[1, \"two\", 3.0, null, true]",
        ],
        "array_nested": [
            "[[1, 2], [3, 4]]", '[[[1]]]',
        ],
        "object_empty": [
            "{}",
        ],
        "object_single": [
            '{"key": "value"}', '{"num": 42}', '{"flag": true}',
        ],
        "object_multiple": [
            '{"a": 1, "b": 2, "c": 3}',
            '{"name": "test", "value": 42, "active": true}',
        ],
        "object_nested": [
            '{"outer": {"inner": 1}}',
            '{"a": {"b": {"c": 1}}}',
        ],
        "mixed_complex": [
            '{"users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]}',
            '[{"key": [1, 2, 3]}, {"key": [4, 5, 6]}]',
        ],
    }

    # JSON 无效等价类
    INVALID_CLASSES = {
        "empty_string": [
            "",
        ],
        "incomplete_object": [
            "{", "}", '{"key"', '{"key":', '{"key": }',
        ],
        "incomplete_array": [
            "[", "]", "[1,", "[1, ",
        ],
        "trailing_comma": [
            "[1, 2, 3,]", '{"a": 1,}',
        ],
        "missing_quotes": [
            "{key: value}", "{key: 1}",
        ],
        "single_quotes": [
            "{'key': 'value'}",
        ],
        "invalid_escape": [
            '"\\x41"', '"\\a"',
        ],
        "invalid_number": [
            "+1", "01", "1.", ".1", "1e", "1e+", "NaN", "Infinity", "-Infinity",
        ],
        "invalid_literal": [
            "True", "False", "NULL", "None", "undefined",
        ],
        "random_text": [
            "hello", "abc123", "not json at all",
        ],
        "binary_like": [
            "\x00", "\xff\xfe",
        ],
    }

    def generate(self, count: int, target_function: str = "cJSON_Parse",
                 **kwargs) -> list[TestCase]:
        """
        生成等价类划分测试用例。

        从有效类和无效类中各选取代表值。
        """
        cases = []

        # 收集所有等价类
        all_valid = []
        for class_name, values in self.VALID_CLASSES.items():
            for v in values:
                all_valid.append((class_name, v))

        all_invalid = []
        for class_name, values in self.INVALID_CLASSES.items():
            for v in values:
                all_invalid.append((class_name, v))

        # 分配比例：60% 有效，40% 无效
        valid_count = int(count * 0.6)
        invalid_count = count - valid_count

        # 从有效类抽样
        valid_samples = self._sample_from_classes(all_valid, valid_count)
        for class_name, value in valid_samples:
            cases.append(TestCase(
                target_function=target_function,
                input_text=value,
                input_data=value.encode('utf-8', errors='replace'),
                strategy=f"equivalence:valid:{class_name}",
                metadata={"equiv_class": class_name, "validity": "valid"}
            ))

        # 从无效类抽样
        invalid_samples = self._sample_from_classes(all_invalid, invalid_count)
        for class_name, value in invalid_samples:
            cases.append(TestCase(
                target_function=target_function,
                input_text=value,
                input_data=value.encode('utf-8', errors='replace'),
                strategy=f"equivalence:invalid:{class_name}",
                metadata={"equiv_class": class_name, "validity": "invalid"}
            ))

        return cases

    def _sample_from_classes(self, items: list[tuple], count: int) -> list[tuple]:
        """从等价类列表中抽样，尽量保证每个类都有代表"""
        if count >= len(items):
            return items[:]

        # 先确保每个类至少有一个代表
        classes = {}
        for class_name, value in items:
            if class_name not in classes:
                classes[class_name] = []
            classes[class_name].append((class_name, value))

        result = []
        # 每类选一个
        for class_name, values in classes.items():
            if len(result) >= count:
                break
            result.append(random.choice(values))

        # 剩余名额随机补充
        remaining = count - len(result)
        if remaining > 0:
            pool = [item for item in items if item not in result]
            if pool:
                result.extend(random.sample(pool, min(remaining, len(pool))))

        return result
