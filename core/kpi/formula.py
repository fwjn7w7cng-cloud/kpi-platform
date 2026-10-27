"""Безопасный расчёт своих формул KPI из config.yaml: «sales / akb», «(collect + sales) / 2».

Разрешены только числа, имена готовых показателей, + − × ÷ и скобки. Никакого кода клиента
(и чужого) не выполняется. Деление на ноль даёт 0.
"""
import ast
import operator

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.USub: operator.neg, ast.UAdd: operator.pos}


class FormulaError(ValueError):
    pass


def check(expr, names):
    """Проверить формулу заранее: пусто — всё хорошо, иначе текст ошибки."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return f"формула «{expr}» не читается"
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id not in names:
            return f"формула «{expr}»: нет показателя «{n.id}» (есть: {', '.join(sorted(names))})"
        if not isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant, ast.Load,
                              *OPS)):
            return f"формула «{expr}»: можно только числа, показатели, + - * / и скобки"
        if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float)):
            return f"формула «{expr}»: только числа"
    return ""


def evaluate(expr, values):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            return float(values.get(n.id, 0) or 0)
        if isinstance(n, ast.UnaryOp):
            return OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Div) and b == 0:
                return 0.0
            return OPS[type(n.op)](a, b)
        raise FormulaError(f"нельзя в формуле: {type(n).__name__}")
    return ev(ast.parse(expr, mode="eval"))
