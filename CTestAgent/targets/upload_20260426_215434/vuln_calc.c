/*
 * vuln_calc.c — 一个故意包含多种漏洞的简易字符串计算器
 * 用途：作为多智能体测试系统的被测目标，验证缺陷发现能力
 * 
 * 包含的漏洞清单（共 8 个，答辩时用于对照验证）：
 *
 *   BUG-1: 栈缓冲区溢出 — input 缓冲区仅 64 字节，gets() 无长度限制
 *   BUG-2: 空指针解引用 — parse_number 遇到空串时返回 NULL，调用方未检查
 *   BUG-3: 整数溢出 — 两个大正数相加可能溢出为负数
 *   BUG-4: 除零错误 — 除法运算未检查除数为 0
 *   BUG-5: 数组越界 — history 数组仅 10 项，无边界检查
 *   BUG-6: 内存泄漏 — strdup 分配的内存在 error 路径未释放
 *   BUG-7: Use-After-Free — free 后继续读取 result 指针
 *   BUG-8: 格式化字符串漏洞 — printf 直接使用用户输入作为格式串
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_HISTORY 10

typedef struct {
    char *expression;
    int   result;
} Record;

Record history[MAX_HISTORY];
int    history_count = 0;  /* BUG-5: 无上限检查，超过 10 则越界写入 */

/* BUG-2: 输入为空串时返回 NULL，但调用方不检查 */
int *parse_number(const char *str) {
    if (str == NULL || *str == '\0') {
        return NULL;  /* 返回 NULL */
    }
    int *num = (int *)malloc(sizeof(int));
    *num = atoi(str);
    return num;
}

/* BUG-3: 无溢出保护  BUG-4: 无除零保护 */
int calculate(int a, char op, int b) {
    switch (op) {
        case '+': return a + b;    /* BUG-3: INT_MAX + 1 = 溢出 */
        case '-': return a - b;
        case '*': return a * b;    /* 乘法同样可能溢出 */
        case '/': return a / b;    /* BUG-4: b=0 时触发 SIGFPE */
        default:  return 0;
    }
}

void save_history(const char *expr, int result) {
    /* BUG-5: 不检查 history_count >= MAX_HISTORY，直接越界写入 */
    history[history_count].expression = strdup(expr);
    history[history_count].result     = result;
    history_count++;
}

void print_history() {
    for (int i = 0; i < history_count; i++) {
        /* BUG-8: expression 可能包含 %s %x 等格式符 */
        printf(history[i].expression);  /* BUG-8: 格式化字符串漏洞 */
        printf(" = %d\n", history[i].result);
    }
}

void process_input(const char *input) {
    char left[32], right[32];
    char op;

    /* 简单解析: "数字 运算符 数字" */
    if (sscanf(input, "%31s %c %31s", left, &op, right) != 3) {
        printf("格式错误\n");
        return;
    }

    /* BUG-6: strdup 分配内存，但在错误路径不释放 */
    char *expr_copy = strdup(input);

    int *a = parse_number(left);
    int *b = parse_number(right);

    /* BUG-2: 如果 left 或 right 为空，a 或 b 为 NULL，解引用崩溃 */
    int result = calculate(*a, op, *b);

    printf("结果: %d\n", result);
    save_history(expr_copy, result);

    free(a);
    free(b);

    /* BUG-7: free 之后仍然读取 */
    free(a);          /* double free! */
    printf("验证: a 之前的值是 %d\n", *a);  /* BUG-7: Use-After-Free */

    /* BUG-6: expr_copy 在正常路径也未释放 → 内存泄漏 */
}

int main(int argc, char *argv[]) {
    char input[64];  /* BUG-1: 缓冲区仅 64 字节 */

    if (argc > 1) {
        /* 从命令行参数读取（方便 fuzzer 测试） */
        /* BUG-1: strcpy 不检查长度，argv[1] 超过 63 字节即溢出 */
        strcpy(input, argv[1]);
    } else {
        printf("请输入表达式 (如: 10 + 20): ");
        /* BUG-1: gets 已被废弃，无长度限制 */
        gets(input);
    }

    process_input(input);

    /* 打印历史 */
    print_history();

    /* 没有释放 history 中 strdup 分配的内存 → 额外泄漏 */
    return 0;
}
