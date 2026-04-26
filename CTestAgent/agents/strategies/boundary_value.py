# boundary_value.py - 边界值分析策略
"""
边界值分析（Boundary Value Analysis）策略。

对长度、数值、索引、空值等边界及邻域集中采样。
针对 cJSON 的 JSON 解析功能，测试各种边界条件。
"""

import random
import string
from core.message_types import TestCase


class BoundaryValueAnalyzer:
    """边界值分析测试用例生成器"""

    name = "boundary"

    def generate(self, count: int, target_function: str = "cJSON_Parse",
                 **kwargs) -> list[TestCase]:
        """生成边界值测试用例"""
        cases = []
        generators = [
            self._string_length_boundaries,
            self._number_boundaries,
            self._nesting_depth_boundaries,
            self._array_size_boundaries,
            self._unicode_boundaries,
            self._key_length_boundaries,
            self._special_char_boundaries,
        ]

        # 从各个边界类别中生成
        per_generator = max(count // len(generators), 1)
        for gen_func in generators:
            gen_cases = gen_func(per_generator, target_function)
            cases.extend(gen_cases)

        # 如果不够，补充随机边界值
        while len(cases) < count:
            gen_func = random.choice(generators)
            extra = gen_func(1, target_function)
            cases.extend(extra)

        return cases[:count]

    def _string_length_boundaries(self, count: int, target_func: str) -> list[TestCase]:
        """字符串长度边界"""
        cases = []
        # 边界长度值
        lengths = [0, 1, 2, 127, 128, 255, 256, 1023, 1024, 4095, 4096, 65535, 65536]

        for length in lengths[:count]:
            content = 'a' * length
            json_str = f'"{content}"'
            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8', errors='replace'),
                strategy=f"boundary:string_length:{length}",
                metadata={"boundary_type": "string_length", "length": length}
            ))
        return cases

    def _number_boundaries(self, count: int, target_func: str) -> list[TestCase]:
        """数值边界"""
        cases = []
        numbers = [
            "0", "-0", "1", "-1",
            "2147483647",           # INT_MAX
            "-2147483648",          # INT_MIN
            "2147483648",           # INT_MAX + 1
            "9999999999999999",     # 大整数
            "0.0", "-0.0",
            "1e0", "1e1", "1e-1",
            "1e308",               # 接近 DBL_MAX
            "-1e308",
            "5e-324",              # 接近 DBL_MIN (denormalized)
            "1e309",               # 超出 double 范围
            "1e-400",              # 下溢
            "0.1", "0.01", "0.001",
            "999999999999999999999999999999",  # 极大整数
        ]

        for num in numbers[:count]:
            cases.append(TestCase(
                target_function=target_func,
                input_text=num,
                input_data=num.encode('utf-8'),
                strategy=f"boundary:number:{num}",
                metadata={"boundary_type": "number", "value": num}
            ))
        return cases

    def _nesting_depth_boundaries(self, count: int, target_func: str) -> list[TestCase]:
        """嵌套深度边界"""
        cases = []
        depths = [1, 2, 5, 10, 50, 100, 500, 999, 1000, 1001, 2000]

        for depth in depths[:count]:
            # 嵌套数组
            json_str = '[' * depth + '1' + ']' * depth
            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8'),
                strategy=f"boundary:nesting_depth:{depth}",
                metadata={"boundary_type": "nesting_depth", "depth": depth}
            ))
        return cases

    def _array_size_boundaries(self, count: int, target_func: str) -> list[TestCase]:
        """数组大小边界"""
        cases = []
        sizes = [0, 1, 2, 10, 100, 1000]

        for size in sizes[:count]:
            if size == 0:
                json_str = "[]"
            else:
                elements = ", ".join(str(i) for i in range(size))
                json_str = f"[{elements}]"
            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8'),
                strategy=f"boundary:array_size:{size}",
                metadata={"boundary_type": "array_size", "size": size}
            ))
        return cases

    def _unicode_boundaries(self, count: int, target_func: str) -> list[TestCase]:
        """Unicode 边界"""
        cases = []
        unicode_values = [
            '"\\u0000"',           # NULL 字符
            '"\\u0001"',           # SOH
            '"\\u001f"',           # 最后一个控制字符
            '"\\u0020"',           # 空格
            '"\\u007e"',           # ~
            '"\\u007f"',           # DEL
            '"\\u0080"',           # 扩展 ASCII 起始
            '"\\u00ff"',           # 扩展 ASCII 结束
            '"\\ud800"',           # 高代理起始（无效单独出现）
            '"\\udbff"',           # 高代理结束
            '"\\udc00"',           # 低代理起始
            '"\\udfff"',           # 低代理结束
            '"\\uffff"',           # BMP 最大值
        ]

        for val in unicode_values[:count]:
            cases.append(TestCase(
                target_function=target_func,
                input_text=val,
                input_data=val.encode('utf-8', errors='replace'),
                strategy=f"boundary:unicode:{val}",
                metadata={"boundary_type": "unicode"}
            ))
        return cases

    def _key_length_boundaries(self, count: int, target_func: str) -> list[TestCase]:
        """对象键长度边界"""
        cases = []
        lengths = [0, 1, 127, 128, 255, 256, 1024]

        for length in lengths[:count]:
            key = 'k' * length
            json_str = '{' + f'"{key}": 1' + '}'
            cases.append(TestCase(
                target_function=target_func,
                input_text=json_str,
                input_data=json_str.encode('utf-8'),
                strategy=f"boundary:key_length:{length}",
                metadata={"boundary_type": "key_length", "length": length}
            ))
        return cases

    def _special_char_boundaries(self, count: int, target_func: str) -> list[TestCase]:
        """特殊字符边界"""
        cases = []
        specials = [
            '"\t"', '"\n"', '"\r"', '"\r\n"',
            '"\\/"',                # 转义的斜杠
            '"\\\\"',              # 转义的反斜杠
            '"\\""',               # 转义的引号
            '"\\b"',               # 退格
            '"\\f"',               # 换页
        ]

        for val in specials[:count]:
            cases.append(TestCase(
                target_function=target_func,
                input_text=val,
                input_data=val.encode('utf-8', errors='replace'),
                strategy=f"boundary:special_char",
                metadata={"boundary_type": "special_char"}
            ))
        return cases
