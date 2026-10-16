"""Pretty-printer: AST -> CQL-lite source (round-trips through the parser)."""
from __future__ import annotations

from ..numeric import fmt
from . import ast as A

_PREC = {"or": 1, "and": 2, "not": 3, "cmp": 4, "add": 5, "mul": 6, "neg": 7, "atom": 8}


def _prec(n: A.Node) -> int:
    if isinstance(n, A.Bin):
        if n.op == "or":
            return _PREC["or"]
        if n.op == "and":
            return _PREC["and"]
        if n.op in A.COMPARE_OPS:
            return _PREC["cmp"]
        if n.op in ("+", "-"):
            return _PREC["add"]
        return _PREC["mul"]
    if isinstance(n, A.Unary):
        return _PREC["not"] if n.op == "not" else _PREC["neg"]
    if isinstance(n, A.IsNull):
        return _PREC["cmp"]
    if isinstance(n, A.Num) and n.value < 0:
        return _PREC["neg"]
    return _PREC["atom"]


def expr(n: A.Node, parent: int = 0) -> str:
    s = _expr(n)
    return f"({s})" if _prec(n) < parent else s


def _expr(n: A.Node) -> str:
    if isinstance(n, A.Num):
        return fmt(n.value) + (f" '{n.unit_src}'" if n.unit_src else "")
    if isinstance(n, A.Str):
        return f"'{n.value}'"
    if isinstance(n, A.Bool):
        return "true" if n.value else "false"
    if isinstance(n, A.Null):
        return "null"
    if isinstance(n, A.Ref):
        return f'"{n.name}"'
    if isinstance(n, A.Code):
        return f'"{n.code}"'
    if isinstance(n, A.Duration):
        return n.text
    if isinstance(n, A.Call):
        return f"{n.name}({', '.join(expr(a) for a in n.args)})"
    if isinstance(n, A.Unary):
        if n.op == "not":
            return "not " + expr(n.x, _PREC["not"])
        return "-" + expr(n.x, _PREC["neg"])
    if isinstance(n, A.IsNull):
        return f"{expr(n.x, _PREC['cmp'] + 1)} is {'not ' if n.negated else ''}null"
    if isinstance(n, A.Bin):
        p = _prec(n)
        if n.op in ("and", "or"):
            return f"{expr(n.l, p)} {n.op} {expr(n.r, p + 1)}"
        if n.op in A.COMPARE_OPS:
            return f"{expr(n.l, p + 1)} {n.op} {expr(n.r, p + 1)}"
        return f"{expr(n.l, p)} {n.op} {expr(n.r, p + 1)}"
    raise TypeError(f"cannot print {type(n).__name__}")


def library(lib: A.Library) -> str:
    head = f"library {lib.name}"
    if lib.version:
        head += f" version '{lib.version}'"
    parts = [head]
    for d in lib.defines.values():
        parts.append(f'define "{d.name}":\n  {expr(d.body)}')
    return "\n\n".join(parts) + "\n"
