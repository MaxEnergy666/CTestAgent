#include <stdio.h>

static int calculate(int left, char op, int right, int *ok) {
    *ok = 1;

    if (op == '+') {
        // BUG-2: signed integer overflow is not checked.
        return left + right;
    }

    if (op == '-') {
        return left - right;
    }

    if (op == '*') {
        return left * right;
    }

    if (op == '/') {
        // BUG-1: division by zero is intentionally not checked.
        return left / right;
    }

    *ok = 0;
    return 0;
}

int main(void) {
    char line[128];
    char op;
    char extra;
    int left;
    int right;
    int ok;
    int result;

    if (fgets(line, sizeof(line), stdin) == NULL) {
        printf("ERROR: empty input\n");
        return 1;
    }

    if (sscanf(line, "%d %c %d %c", &left, &op, &right, &extra) != 3) {
        printf("ERROR: expected format: number operator number\n");
        return 1;
    }

    result = calculate(left, op, right, &ok);
    if (!ok) {
        printf("ERROR: unsupported operator '%c'\n", op);
        return 1;
    }

    printf("%d\n", result);
    return 0;
}
