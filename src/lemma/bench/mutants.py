"""Seeded-bug generator for LemmaBench.

Each mutant is the gold rule library with ONE realistic edit, the kinds of slip that happen when a
rule is transcribed or updated by hand: an off-by-one threshold, a flipped inclusive/exclusive
boundary, a dropped condition, `and`/`or` mixed up, a unit mix-up, or "missing value treated as normal".
Whether a mutant is actually different from the gold is decided by Lemma itself (equivalent mutants exist).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Optional

from ..lang import ast as A
from ..lang import pretty
from ..lang.check import check_library, errors
from ..lang.lexer import LemmaSyntaxError
from ..lang.parser import parse_library
from ..schema import Schema


@dataclass
class Mutant:
    id: str
    operator: str
    description: str
    source: str
    lib: A.Library


def _nodes(lib: A.Library) -> list[tuple[str, A.Node, Optional[A.Node], str]]:
    """(define, node, parent, slot) for every node, in deterministic order."""
    out: list = []

    def rec(define: str, n: A.Node, parent, slot):
        out.append((define, n, parent, slot))
        if isinstance(n, A.Call):
            for i, a in enumerate(n.args):
                rec(define, a, n, ("args", i))
        elif isinstance(n, A.Unary):
            rec(define, n.x, n, ("x", None))
        elif isinstance(n, A.Bin):
            rec(define, n.l, n, ("l", None))
            rec(define, n.r, n, ("r", None))
        elif isinstance(n, A.IsNull):
            rec(define, n.x, n, ("x", None))

    for name, d in lib.defines.items():
        rec(name, d.body, None, ("body", None))
    return out


def _set(lib: A.Library, define: str, parent, slot, new: A.Node):
    attr, idx = slot
    if parent is None:
        lib.defines[define].body = new
    elif idx is None:
        setattr(parent, attr, new)
    else:
        getattr(parent, attr)[idx] = new


def _literal_context(schema: Schema, cmp_node: A.Bin, lit: A.Num):
    other = cmp_node.l if cmp_node.r is lit else cmp_node.r
    if isinstance(other, A.Call) and other.name == "AgeInYears":
        return Fraction(1), Fraction(5)
    if isinstance(other, A.Call) and other.name == "Ratio":
        return Fraction(1, 10), Fraction(1, 2)
    if isinstance(other, A.Call) and other.args and isinstance(other.args[0], A.Code):
        spec = schema.obs[other.args[0].code]
        small = spec.step if lit.unit_src in (None, spec.unit) else Fraction(1, 10)
        big = max(small, round(abs(lit.value) / 10 / small) * small)
        return small, big
    return None


def generate_mutants(schema: Schema, gold_text: str, max_mutants: int = 400) -> list[Mutant]:
    base = parse_library(gold_text)
    check_library(base, schema)
    gold_pretty = pretty.library(base)
    n_nodes = len(_nodes(parse_library(gold_pretty)))
    out: list[Mutant] = []
    seen = {gold_pretty}

    def emit(operator: str, desc: str, mutate: Callable[[A.Library], bool]):
        lib = parse_library(gold_pretty)
        if not mutate(lib):
            return
        text = pretty.library(lib)
        if text in seen:
            return
        try:
            m = parse_library(text)
        except LemmaSyntaxError:
            return
        if errors(check_library(m, schema)):
            return
        seen.add(text)
        out.append(Mutant(f"M{len(out) + 1:03d}", operator, desc, text, m))

    template = parse_library(gold_pretty)
    check_library(template, schema)
    tnodes = _nodes(template)
    for idx, (define, n, parent, slot) in enumerate(tnodes):
        # ---- thresholds ----
        if isinstance(n, A.Num) and isinstance(parent, A.Bin) and parent.op in A.COMPARE_OPS:
            ctx = _literal_context(schema, parent, n)
            if ctx:
                small, big = ctx
                for label, delta in (("+1 step", small), ("-1 step", -small), ("+10%", big), ("-10%", -big)):
                    if delta == 0:
                        continue

                    def mut(lib, idx=idx, delta=delta):
                        node = _nodes(lib)[idx][1]
                        node.value = node.value + delta
                        return True

                    emit("threshold", f'threshold in "{define}": {pretty.expr(n)} {label}', mut)
        # ---- boundary inclusivity and direction ----
        if isinstance(n, A.Bin) and n.op in ("<", "<=", ">", ">="):
            flip = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}
            rev = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}

            def mut_incl(lib, idx=idx, flip=flip):
                node = _nodes(lib)[idx][1]
                node.op = flip[node.op]
                return True

            def mut_rev(lib, idx=idx, rev=rev):
                node = _nodes(lib)[idx][1]
                node.op = rev[node.op]
                return True

            emit("boundary", f'inclusive/exclusive flipped in "{define}": {pretty.expr(n)}', mut_incl)
            emit("direction", f'comparison direction reversed in "{define}": {pretty.expr(n)}', mut_rev)
        # ---- logic ----
        if isinstance(n, A.Bin) and n.op in ("and", "or"):
            other_op = "or" if n.op == "and" else "and"

            def mut_conn(lib, idx=idx, other_op=other_op):
                _nodes(lib)[idx][1].op = other_op
                return True

            emit("connective", f'"{n.op}" changed to "{other_op}" in "{define}"', mut_conn)
            for side in ("l", "r"):

                def mut_drop(lib, idx=idx, side=side):
                    _, node, parent, slot = _nodes(lib)[idx]
                    keep = node.r if side == "l" else node.l  # drop `side`, keep the other
                    _set(lib, define, parent, slot, keep)
                    return True

                dropped = n.l if side == "l" else n.r
                emit("dropped-condition", f'condition removed in "{define}": {pretty.expr(dropped)}', mut_drop)
        # ---- flags ----
        if isinstance(n, A.Call) and n.name in ("HasCondition", "OnMedication"):

            def mut_flag(lib, idx=idx):
                _, node, parent, slot = _nodes(lib)[idx]
                _set(lib, define, parent, slot, A.Unary(op="not", x=node))
                return True

            emit("negated-flag", f'{pretty.expr(n)} negated in "{define}"', mut_flag)
        # ---- units ----
        if isinstance(n, A.Num) and n.unit_src and isinstance(parent, A.Bin):
            other = parent.l if parent.r is n else parent.r
            code = other.args[0].code if isinstance(other, A.Call) and other.args and isinstance(other.args[0], A.Code) else None
            if code:
                spec = schema.obs[code]
                alts = [u for u in [spec.unit, *spec.conversions] if u != n.unit_src]
                for alt in alts:

                    def mut_unit(lib, idx=idx, alt=alt):
                        _nodes(lib)[idx][1].unit_src = alt
                        return True

                    emit("unit-mixup", f'unit changed in "{define}": {pretty.expr(n)} -> {n.value} \'{alt}\'', mut_unit)
        # ---- missing data ----
        if isinstance(n, A.Call) and n.name == "Latest":
            code = n.args[0].code
            spec = schema.obs[code]

            def mut_null(lib, idx=idx, spec=spec, code=code):
                _, node, parent, slot = _nodes(lib)[idx]
                default = A.Num(value=spec.typical, unit_src=spec.unit)
                _set(lib, define, parent, slot, A.Call(name="Coalesce", args=[node, default]))
                return True

            emit("missing-as-normal", f'missing "{code}" treated as typical value in "{define}"', mut_null)
        if len(out) >= max_mutants:
            break
    return out
