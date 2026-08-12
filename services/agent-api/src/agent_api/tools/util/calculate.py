"""Whitelist AST calculator for safe, deterministic arithmetic."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import cast

MAX_EXPRESSION_CHARS = 200
MAX_AST_NODES = 64
MAX_ABS_VALUE = 1e15

BinOpFn = Callable[[int | float, int | float], int | float]
UnaryOpFn = Callable[[int | float], int | float]
FuncFn = Callable[..., int | float]


def _add(a: int | float, b: int | float) -> int | float:
    return a + b


def _sub(a: int | float, b: int | float) -> int | float:
    return a - b


def _mul(a: int | float, b: int | float) -> int | float:
    return a * b


def _truediv(a: int | float, b: int | float) -> int | float:
    return a / b


def _floordiv(a: int | float, b: int | float) -> int | float:
    return a // b


def _mod(a: int | float, b: int | float) -> int | float:
    return a % b


def _pow(a: int | float, b: int | float) -> int | float:
    return a**b


def _pos(a: int | float) -> int | float:
    return +a


def _neg(a: int | float) -> int | float:
    return -a


_BIN_OPS: dict[type[ast.operator], BinOpFn] = {
    ast.Add: _add,
    ast.Sub: _sub,
    ast.Mult: _mul,
    ast.Div: _truediv,
    ast.FloorDiv: _floordiv,
    ast.Mod: _mod,
    ast.Pow: _pow,
}
_UNARY_OPS: dict[type[ast.unaryop], UnaryOpFn] = {
    ast.UAdd: _pos,
    ast.USub: _neg,
}
_FUNCS: dict[str, FuncFn] = {
    "abs": cast(FuncFn, abs),
    "min": cast(FuncFn, min),
    "max": cast(FuncFn, max),
    "round": cast(FuncFn, round),
}


class _Forbidden(Exception):
    """Raised when the expression uses a disallowed construct."""


def compute_calculate(expression: str) -> dict[str, object]:
    """Evaluate a restricted arithmetic expression.

    Returns ``{ok, expression, result, result_type}`` or
    ``{ok: False, error_code, message}``.
    """

    text = expression.strip()
    if not text:
        return {"ok": False, "error_code": "empty", "message": "expression is empty"}
    if len(text) > MAX_EXPRESSION_CHARS:
        return {"ok": False, "error_code": "too_long", "message": "expression too long"}

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return {
            "ok": False,
            "error_code": "syntax",
            "message": "invalid expression syntax",
        }

    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        return {
            "ok": False,
            "error_code": "too_complex",
            "message": "expression too complex",
        }

    try:
        value = _eval_node(tree.body)
    except ZeroDivisionError:
        return {"ok": False, "error_code": "div_zero", "message": "division by zero"}
    except _Forbidden as exc:
        return {"ok": False, "error_code": "forbidden", "message": str(exc)}
    except OverflowError:
        return {"ok": False, "error_code": "overflow", "message": "numeric overflow"}
    except TypeError as exc:
        return {"ok": False, "error_code": "type_error", "message": str(exc)}

    # bool is a subclass of int — reject before accepting ints.
    if isinstance(value, bool):
        return {
            "ok": False,
            "error_code": "type_error",
            "message": "result must be int or float",
        }
    if abs(value) > MAX_ABS_VALUE:
        return {
            "ok": False,
            "error_code": "overflow",
            "message": "result magnitude too large",
        }

    result_type = "int" if isinstance(value, int) else "float"
    return {
        "ok": True,
        "expression": text,
        "result": value,
        "result_type": result_type,
    }


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant):
        # Reject bool explicitly (True/False are Constant in 3.8+).
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _Forbidden("only numeric literals are allowed")
        return node.value

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise _Forbidden(f"unary operator not allowed: {op_type.__name__}")
        return _UNARY_OPS[op_type](_eval_node(node.operand))

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise _Forbidden(f"operator not allowed: {op_type.__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _BIN_OPS[op_type](left, right)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _Forbidden("only direct function calls are allowed")
        name = node.func.id
        if name not in _FUNCS:
            raise _Forbidden(f"function not allowed: {name}")
        if node.keywords:
            raise _Forbidden("keyword arguments are not allowed")
        args = [_eval_node(arg) for arg in node.args]
        return _FUNCS[name](*args)

    raise _Forbidden(f"unsupported expression node: {type(node).__name__}")
