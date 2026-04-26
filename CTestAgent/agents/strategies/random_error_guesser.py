# random_error_guesser.py - 随机+错误猜测策略
"""
随机测试 + 错误猜测（Random Testing + Error Guessing）策略。

随机扰动叠加经验型易错值（null、空串、超长、特殊字符等）。
基于测试经验，注入常见的易触发缺陷的输入模式。
"""

import random
import string
from core.message_types import TestCase


class RandomErrorGuesser:
    """随机测试 + 错误猜测测试用例生成器"""

    name = "random_error_guess"

    # 经验型易错输入
    ERROR_GUESS_INPUTS = [
        # 空值和 NULL 相关
        "",
        "\x00",
        "\x00\x00\x00",
        "null",
        "NULL",
        "nil",
        "None",

        # 空白和不可见字符
        " ",
        "\t",
        "\n",
        "\r\n",
        "   ",
        "\t\n\r",

        # 超长输入
        "a" * 10000,
        '"' + "x" * 10000 + '"',
        '{"key": "' + "v" * 50000 + '"}',

        # 特殊字符注入
        '{"key": "\x00"}',
        '{"key": "\xff"}',
        "\xff\xfe\xfd",
        "\xef\xbb\xbf{}",          # BOM + JSON
        "\xef\xbb\xbf",            # 仅 BOM

        # 格式边界
        '{"": ""}',                 # 空键
        '{"": 0}',
        '{" ": 1}',                 # 空格键
        '{"a": }',                  # 缺值
        '{: "value"}',              # 缺键

        # 重复键
        '{"a": 1, "a": 2}',
        '{"a": 1, "a": 2, "a": 3}',

        # 深嵌套
        '{"a":' * 100 + '1' + '}' * 100,
        '[' * 500 + ']' * 500,

        # 畸形 JSON
        '{"key": undefined}',
        '{"key": NaN}',
        '{"key": Infinity}',
        '{"key": -Infinity}',
        "{key: value}",
        "{'key': 'value'}",
        '{"key": 01}',
        '{"key": .5}',
        '{"key": +1}',

        # 截断输入
        '{"key": "val',
        '{"key": [1, 2, ',
        '{"incomplete',
        '[1, 2, 3',

        # 混合编码
        '{"key": "中文"}',
        '{"key": "日本語"}',
        '{"emoji": "😀🎉"}',

        # 转义攻击
        '{"key": "\\\\\\\\\\\\"}',
        '{"key": "\\/\\/\\/"}',
        '{"key": "\\"\\"\\"\\""}',

        # 数值溢出
        '{"num": 99999999999999999999999999999999999999999999}',
        '{"num": -99999999999999999999999999999999999999999999}',
        '{"num": 1e99999}',
        '{"num": 0.00000000000000000000000000000001}',
    ]

    def generate(self, count: int, target_function: str = "cJSON_Parse",
                 **kwargs) -> list[TestCase]:
        """生成随机+错误猜测测试用例"""
        cases = []

        # 50% 来自经验型错误猜测
        error_guess_count = count // 2
        # 50% 来自纯随机生成
        random_count = count - error_guess_count

        # 错误猜测用例
        eg_inputs = self.ERROR_GUESS_INPUTS[:]
        random.shuffle(eg_inputs)
        for inp in eg_inputs[:error_guess_count]:
            cases.append(TestCase(
                target_function=target_function,
                input_text=inp,
                input_data=inp.encode('utf-8', errors='replace'),
                strategy="error_guess",
                metadata={"sub_strategy": "error_guess"}
            ))

        # 纯随机用例
        for _ in range(random_count):
            inp = self._generate_random_input()
            cases.append(TestCase(
                target_function=target_function,
                input_text=inp,
                input_data=inp.encode('utf-8', errors='replace'),
                strategy="random",
                metadata={"sub_strategy": "random"}
            ))

        # 如果超过经验库的数量，补充更多随机
        while len(cases) < count:
            inp = self._generate_random_input()
            cases.append(TestCase(
                target_function=target_function,
                input_text=inp,
                input_data=inp.encode('utf-8', errors='replace'),
                strategy="random",
                metadata={"sub_strategy": "random"}
            ))

        return cases[:count]

    def _generate_random_input(self) -> str:
        """生成随机输入"""
        generators = [
            self._random_bytes_string,
            self._random_json_like,
            self._random_mutated_json,
            self._random_printable,
        ]
        return random.choice(generators)()

    def _random_bytes_string(self) -> str:
        """生成随机字节串"""
        length = random.randint(1, 1000)
        return ''.join(chr(random.randint(0, 255)) for _ in range(length))

    def _random_json_like(self) -> str:
        """生成类 JSON 的随机字符串"""
        templates = [
            lambda: '{' + self._random_kv_pairs(random.randint(0, 5)) + '}',
            lambda: '[' + ', '.join(
                self._random_value() for _ in range(random.randint(0, 10))
            ) + ']',
            lambda: self._random_value(),
        ]
        return random.choice(templates)()

    def _random_kv_pairs(self, count: int) -> str:
        """生成随机键值对"""
        pairs = []
        for _ in range(count):
            key = ''.join(random.choices(string.ascii_lowercase, k=random.randint(1, 10)))
            val = self._random_value()
            pairs.append(f'"{key}": {val}')
        return ', '.join(pairs)

    def _random_value(self) -> str:
        """生成随机 JSON 值"""
        choices = [
            lambda: str(random.randint(-1000, 1000)),
            lambda: f"{random.uniform(-1000, 1000):.6f}",
            lambda: f'"{self._random_string()}"',
            lambda: random.choice(["true", "false", "null"]),
            lambda: "[]",
            lambda: "{}",
        ]
        return random.choice(choices)()

    def _random_string(self) -> str:
        """生成随机字符串内容"""
        length = random.randint(0, 50)
        chars = string.printable.replace('"', '').replace('\\', '')
        return ''.join(random.choices(chars, k=length))

    def _random_mutated_json(self) -> str:
        """对有效 JSON 进行随机变异"""
        base_jsons = [
            '{"key": "value"}',
            '[1, 2, 3]',
            '{"a": 1, "b": [2, 3]}',
            'null',
            '"hello"',
        ]
        base = random.choice(base_jsons)
        return self._mutate_string(base)

    def _mutate_string(self, s: str) -> str:
        """对字符串进行随机变异"""
        if not s:
            return s

        mutations = [
            # 删除随机字符
            lambda s: s[:max(0, len(s)//2)] + s[len(s)//2 + 1:] if len(s) > 1 else s,
            # 插入随机字符
            lambda s: s[:len(s)//2] + chr(random.randint(0, 127)) + s[len(s)//2:],
            # 替换随机字符
            lambda s: s[:max(0, len(s)//2)] + chr(random.randint(0, 127)) + s[min(len(s), len(s)//2 + 1):] if s else s,
            # 重复部分内容
            lambda s: s + s[:min(10, len(s))],
            # 截断
            lambda s: s[:max(1, random.randint(0, len(s)))],
            # 翻转
            lambda s: s[::-1],
        ]

        result = s
        for _ in range(random.randint(1, 3)):
            mutation = random.choice(mutations)
            result = mutation(result)

        return result

    def _random_printable(self) -> str:
        """生成随机可打印字符串"""
        length = random.randint(1, 200)
        return ''.join(random.choices(string.printable, k=length))
