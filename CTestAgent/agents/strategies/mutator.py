# mutator.py - 变异操作库
"""
通用变异操作库。

提供多种变异算子，供各策略和 Generator Agent 使用。
包括字节级变异、语法感知变异和字典插入等。
"""

import random
import string
from typing import Optional


class Mutator:
    """通用变异操作库"""

    # JSON 语法关键词字典（用于字典插入变异）
    JSON_TOKENS = [
        '{', '}', '[', ']', ':', ',',
        '"', 'null', 'true', 'false',
        '\\n', '\\t', '\\r', '\\u0000',
        '0', '-1', '1e10', '1e-10',
        '""', '":"', '","',
        '{}', '[]',
    ]

    @staticmethod
    def bit_flip(data: bytes, prob: float = 0.05) -> bytes:
        """比特翻转：随机翻转一些比特"""
        result = bytearray(data)
        for i in range(len(result)):
            if random.random() < prob:
                bit_pos = random.randint(0, 7)
                result[i] ^= (1 << bit_pos)
        return bytes(result)

    @staticmethod
    def byte_insert(data: bytes, max_len: int = 16) -> bytes:
        """字节插入：在随机位置插入随机字节"""
        result = bytearray(data)
        insert_len = random.randint(1, max_len)
        pos = random.randint(0, len(result))
        insert_data = bytes(random.randint(0, 255) for _ in range(insert_len))
        result[pos:pos] = insert_data
        return bytes(result)

    @staticmethod
    def byte_delete(data: bytes, max_len: int = 16) -> bytes:
        """字节删除：删除随机位置的字节"""
        if len(data) <= 1:
            return data
        result = bytearray(data)
        delete_len = random.randint(1, min(max_len, len(result) - 1))
        pos = random.randint(0, len(result) - delete_len)
        del result[pos:pos + delete_len]
        return bytes(result)

    @staticmethod
    def byte_replace(data: bytes) -> bytes:
        """字节替换：替换随机位置的字节"""
        if not data:
            return data
        result = bytearray(data)
        count = random.randint(1, max(1, len(result) // 10))
        for _ in range(count):
            pos = random.randint(0, len(result) - 1)
            result[pos] = random.randint(0, 255)
        return bytes(result)

    @staticmethod
    def boundary_splice(data: bytes) -> bytes:
        """边界拼接：在边界值位置拼接数据"""
        boundary_values = [
            b'\x00', b'\xff', b'\x7f', b'\x80',
            b'\x00\x00', b'\xff\xff',
            b'\x00\x00\x00\x00', b'\xff\xff\xff\xff',
        ]
        result = bytearray(data)
        splice_pos = random.choice([0, len(result) // 2, len(result)])
        boundary = random.choice(boundary_values)
        result[splice_pos:splice_pos] = boundary
        return bytes(result)

    @staticmethod
    def crossover(data1: bytes, data2: bytes) -> bytes:
        """交叉拼接：两个种子交叉拼接"""
        if not data1 or not data2:
            return data1 or data2 or b''

        # 选择交叉点
        pos1 = random.randint(0, len(data1))
        pos2 = random.randint(0, len(data2))

        return data1[:pos1] + data2[pos2:]

    @staticmethod
    def dictionary_insert(data: bytes, tokens: Optional[list[str]] = None) -> bytes:
        """字典插入：插入语法关键词"""
        if tokens is None:
            tokens = Mutator.JSON_TOKENS

        result = bytearray(data)
        token = random.choice(tokens).encode('utf-8')
        pos = random.randint(0, len(result))
        result[pos:pos] = token
        return bytes(result)

    @staticmethod
    def chunk_duplicate(data: bytes) -> bytes:
        """块复制：复制一段内容"""
        if len(data) < 2:
            return data + data
        result = bytearray(data)
        chunk_len = random.randint(1, min(64, len(result)))
        start = random.randint(0, len(result) - chunk_len)
        chunk = result[start:start + chunk_len]
        insert_pos = random.randint(0, len(result))
        result[insert_pos:insert_pos] = chunk
        return bytes(result)

    @staticmethod
    def truncate_or_extend(data: bytes) -> bytes:
        """截断或扩展到极端长度"""
        if random.random() < 0.5:
            # 截断
            new_len = random.randint(0, max(1, len(data) // 2))
            return data[:new_len]
        else:
            # 扩展
            extend_len = random.randint(100, 10000)
            return data + bytes(random.randint(0, 255) for _ in range(extend_len))

    @staticmethod
    def null_or_empty_inject(data: bytes) -> bytes:
        """注入空值/空数据"""
        injections = [
            b'\x00',
            b'',
            b'null',
            b'""',
            b'{}',
            b'[]',
        ]
        if random.random() < 0.3:
            # 完全替换
            return random.choice(injections)
        else:
            # 在随机位置注入
            result = bytearray(data)
            injection = random.choice(injections)
            pos = random.randint(0, len(result))
            result[pos:pos] = injection
            return bytes(result)

    @staticmethod
    def special_char_inject(data: bytes) -> bytes:
        """注入特殊字符"""
        special_chars = [
            b'\x00', b'\x01', b'\x7f', b'\x80', b'\xff',
            b'\n', b'\r', b'\t',
            b'\xef\xbb\xbf',  # UTF-8 BOM
            b'\xfe\xff',      # UTF-16 BE BOM
            b'\xff\xfe',      # UTF-16 LE BOM
        ]
        result = bytearray(data)
        char = random.choice(special_chars)
        pos = random.randint(0, len(result))
        result[pos:pos] = char
        return bytes(result)

    @staticmethod
    def extreme_length_inject(data: bytes) -> bytes:
        """注入极端长度内容"""
        length = random.choice([0, 1, 256, 1024, 4096, 65536])
        fill_byte = random.choice([0, ord('A'), ord('0'), 0xff])
        injection = bytes([fill_byte] * length)

        if random.random() < 0.5:
            return injection  # 完全替换
        else:
            result = bytearray(data)
            pos = random.randint(0, len(result))
            result[pos:pos] = injection
            return bytes(result)

    @staticmethod
    def apply_random_mutation(data: bytes) -> bytes:
        """随机选择一种变异操作并应用"""
        mutations = [
            lambda d: Mutator.bit_flip(d, 0.02),
            Mutator.byte_insert,
            Mutator.byte_delete,
            Mutator.byte_replace,
            Mutator.boundary_splice,
            Mutator.dictionary_insert,
            Mutator.chunk_duplicate,
            Mutator.truncate_or_extend,
            Mutator.null_or_empty_inject,
            Mutator.special_char_inject,
        ]
        mutation = random.choice(mutations)
        return mutation(data)
