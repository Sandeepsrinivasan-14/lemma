"""Type, unit and subset checker for CQL-lite.

This is where Lemma's honesty about scope lives: any construct outside the supported,
analysable subset is rejected with a diagnostic instead of being silently approximated.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from ..schema import Schema
from . import ast as A
from .lexer import line_col

SUPPORTED_FUNCS = ["Latest", "Delta", "Ratio", "AgeInYears", "Sex", "HasCondition", "OnMedication", "Coalesce"]


@dataclass
class Diagnostic:
    severity: str  # "error" | "warning"
    code: str
    message: str
    line: int = 0
    col: int = 0

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message, "line": self.line, "col": self.col}

    def __str__(self) -> str:
        loc = f"{self.line}:{self.col}: " if self.line else ""
        return f"{loc}{self.severity} {self.code}: {self.message}"


class LemmaCheckError(ValueError):
    def __init__(self, diags: list[Diagnostic]):
        self.diagnostics = diags
        super().__init__("; ".join(str(d) for d in diags if d.severity == "error") or "check failed")


class Checker:
    def __init__(self, lib: A.Library, schema: Schema):
        self.lib = lib
        self.schema = schema
        self.diags: list[Diagnostic] = []
        self.state: dict[str, str] = {}
        self._src = lib.source
        self._ratio_ok: set[int] = set()

    # -- diagnostics ---------------------------------------------------------
    def err(self, node, code: str, msg: str, severity: str = "error"):
        pos = 0
        if node is not None and getattr(node, "span", None):
            pos = node.span[0]
        line, col = line_col(self._src, pos)
        self.diags.append(Diagnostic(severity, code, msg, line, col))

    # -- driver --------------------------------------------------------------
    def run(self) -> list[Diagnostic]:
        for name in list(self.lib.defines):
            self.resolve(name, None)
        self.lib.diagnostics = self.diags
        return self.diags

    def resolve(self, name: str, ref_node) -> Optional[A.Define]:
        d = self.lib.defines.get(name)
        if d is None:
            self.err(ref_node, "E010", f'unknown define "{name}"')
            return None
        st = self.state.get(name)
        if st == "visiting":
            self.err(ref_node or d.body, "E011", f'define "{name}" is defined in terms of itself (recursion is outside the supported subset)')
            return None
        if st != "done":
            self.state[name] = "visiting"
            self.ex(d.body)
            d.kind = d.body.kind
            self.state[name] = "done"
        return d

    def check_extra(self, node: A.Node, src: str) -> A.Node:
        """Check an expression that is not a define (an invariant condition or a schema constraint)."""
        old = self._src
        self._src = src
        try:
            self.ex(node)
        finally:
            self._src = old
        return node

    # -- expressions ---------------------------------------------------------
    def ex(self, n: A.Node) -> A.Node:
        getattr(self, "c_" + type(n).__name__)(n)
        return n

    def c_Num(self, n: A.Num):
        n.kind, n.unit, n.analyte, n.canon = "num", n.unit_src, None, n.value

    def c_Str(self, n: A.Str):
        n.kind = "str"

    def c_Bool(self, n: A.Bool):
        n.kind = "bool"

    def c_Null(self, n: A.Null):
        n.kind = "null"
        self.err(n, "E060", "the null literal is not supported in CQL-lite v0.1; use `x is null` / `x is not null`")

    def c_Code(self, n: A.Code):
        n.kind = "code"
        self.err(n, "E061", "a quoted code is only valid as the first argument of Latest/Delta/Ratio/HasCondition/OnMedication")

    def c_Duration(self, n: A.Duration):
        n.kind = "dur"
        self.err(n, "E043", "a duration is only valid as the second argument of Delta/Ratio")

    def c_Ref(self, n: A.Ref):
        d = self.resolve(n.name, n)
        if d is None:
            n.kind = "bool"  # keep going without cascading errors
            return
        b = d.body
        n.kind, n.unit, n.analyte = b.kind, b.unit, b.analyte

    def c_Call(self, n: A.Call):
        name, args = n.name, n.args
        if name not in SUPPORTED_FUNCS:
            self.err(n, "E050", f"function {name}() is outside the supported CQL-lite subset (supported: {', '.join(SUPPORTED_FUNCS)})")
            n.kind = "bool"
            return
        if name in ("Latest", "Delta", "Ratio"):
            self.c_observation_call(n)
        elif name in ("HasCondition", "OnMedication"):
            self.arity(n, 1)
            n.kind = "bool"
            if args and isinstance(args[0], A.Code):
                code = args[0].code
                want = self.schema.conditions if name == "HasCondition" else self.schema.medications
                other = self.schema.medications if name == "HasCondition" else self.schema.conditions
                if code not in want:
                    hint = f" (it is a {'medication' if name == 'HasCondition' else 'condition'}; use {'OnMedication' if name == 'HasCondition' else 'HasCondition'})" if code in other else ""
                    self.err(args[0], "E021", f'unknown {"condition" if name == "HasCondition" else "medication"} "{code}"{hint}')
                else:
                    self.lib.used_flags.add(code)
        elif name == "AgeInYears":
            self.arity(n, 0)
            n.kind, n.unit, n.analyte = "num", None, None
        elif name == "Sex":
            self.arity(n, 0)
            n.kind = "str"
        elif name == "Coalesce":
            self.arity(n, 2)
            n.kind = "bool"
            if len(args) == 2:
                a, b = self.ex(args[0]), self.ex(args[1])
                if a.kind != b.kind or a.kind not in ("num", "bool"):
                    self.err(n, "E001", "Coalesce needs two numbers or two booleans")
                    return
                n.kind = a.kind
                if a.kind == "num":
                    n.unit, n.analyte = self.unify(a, b, n)

    def arity(self, n: A.Call, k: int):
        if len(n.args) != k:
            self.err(n, "E002", f"{n.name}() takes {k} argument{'s' if k != 1 else ''}, found {len(n.args)}")

    def c_observation_call(self, n: A.Call):
        name = n.name
        n.kind = "num"
        want = 1 if name == "Latest" else 2
        self.arity(n, want)
        if not n.args or not isinstance(n.args[0], A.Code):
            return
        code = n.args[0].code
        spec = self.schema.obs.get(code)
        if spec is None:
            self.err(n.args[0], "E020", f'unknown observation "{code}" (known: {", ".join(sorted(self.schema.obs))})')
            return
        self.lib.used_codes.add(code)
        n.unit, n.analyte = spec.unit, code
        if name == "Latest":
            return
        if len(n.args) > 1 and not isinstance(n.args[1], A.Duration):
            self.err(n.args[1], "E043", f"{name}() needs a time window such as `48 hours` or `7 days` as second argument")
        self.lib.trend_codes.add(code)
        if name == "Ratio":
            n.unit, n.analyte = None, None
            if spec.lo <= 0:
                self.err(n, "E044", f'Ratio("{code}") needs the schema minimum to be > 0 (it divides by the prior value)')
            if id(n) not in self._ratio_ok:
                self.err(n, "E042", "Ratio(...) is only supported directly compared with a plain number, e.g. Ratio(\"creatinine\", 7 days) >= 1.5")

    def c_Unary(self, n: A.Unary):
        self.ex(n.x)
        if n.op == "not":
            n.kind = "bool"
            if n.x.kind != "bool":
                self.err(n, "E001", "`not` needs a boolean operand")
        else:
            n.kind, n.unit, n.analyte = "num", n.x.unit, n.x.analyte
            if n.x.kind != "num":
                self.err(n, "E001", "unary minus needs a number")

    def c_IsNull(self, n: A.IsNull):
        self.ex(n.x)
        n.kind = "bool"

    def c_Bin(self, n: A.Bin):
        op = n.op
        if op in ("and", "or"):
            self.ex(n.l)
            self.ex(n.r)
            n.kind = "bool"
            if n.l.kind != "bool" or n.r.kind != "bool":
                self.err(n, "E001", f"`{op}` needs boolean operands")
            return
        if op in A.COMPARE_OPS:
            self.c_compare(n)
            return
        # arithmetic
        self.ex(n.l)
        self.ex(n.r)
        n.kind = "num"
        if n.l.kind != "num" or n.r.kind != "num":
            self.err(n, "E001", f"`{op}` needs numeric operands")
            return
        if op in ("+", "-"):
            n.unit, n.analyte = self.unify(n.l, n.r, n)
        elif op == "*":
            l_lit = isinstance(n.l, A.Num) and n.l.unit_src is None
            r_lit = isinstance(n.r, A.Num) and n.r.unit_src is None
            if l_lit:
                n.unit, n.analyte = n.r.unit, n.r.analyte
            elif r_lit:
                n.unit, n.analyte = n.l.unit, n.l.analyte
            else:
                self.err(n, "E040", "multiplication of two non-constant values is non-linear and outside the supported subset (one side must be a plain number)")
        else:  # "/"
            if isinstance(n.r, A.Num) and n.r.unit_src is None and n.r.value != 0:
                n.unit, n.analyte = n.l.unit, n.l.analyte
            else:
                self.err(n, "E041", "division is only supported by a non-zero plain number")

    def c_compare(self, n: A.Bin):
        n.kind = "bool"
        for side, other in ((n.l, n.r), (n.r, n.l)):
            if isinstance(side, A.Call) and side.name == "Ratio":
                if isinstance(other, A.Num) and other.unit_src is None:
                    self._ratio_ok.add(id(side))
                else:
                    self.err(n, "E042", "Ratio(...) must be compared directly with a plain number (no unit)")
        self.ex(n.l)
        self.ex(n.r)
        lk, rk = n.l.kind, n.r.kind
        if lk == "num" and rk == "num":
            self.unify(n.l, n.r, n)
            return
        if lk == "str" and rk == "str":
            if n.op not in ("=", "!="):
                self.err(n, "E001", "strings can only be compared with = or !=")
            for s in (n.l, n.r):
                if isinstance(s, A.Str) and s.value not in self.schema.sexes:
                    self.err(s, "E032", f"'{s.value}' is not a sex value in the schema ({', '.join(self.schema.sexes)})")
            return
        self.err(n, "E001", f"cannot compare {lk} with {rk}")

    # -- units ---------------------------------------------------------------
    def unify(self, a: A.Node, b: A.Node, where: A.Node):
        """Make two numeric operands comparable; convert literals into the other side's canonical unit.

        Returns (unit, analyte) of the pair.
        """
        la = isinstance(a, A.Num)
        lb = isinstance(b, A.Num)
        if la and lb:
            if a.unit_src != b.unit_src:
                self.err(where, "E030", f"incompatible literal units '{a.unit_src}' and '{b.unit_src}'")
            return a.unit, None
        if la:
            self.coerce(a, b, where)
            return b.unit, b.analyte
        if lb:
            self.coerce(b, a, where)
            return a.unit, a.analyte
        if a.unit != b.unit:
            self.err(where, "E030", f"incompatible units: {a.unit or 'no unit'} vs {b.unit or 'no unit'}")
        return a.unit, a.analyte

    def coerce(self, lit: A.Num, target: A.Node, where: A.Node):
        tgt_unit, analyte = target.unit, target.analyte
        if lit.unit_src is None:
            if tgt_unit is not None:
                self.err(lit, "W001", f"unitless number compared with a quantity; assuming the canonical unit '{tgt_unit}'", "warning")
            lit.canon, lit.unit, lit.analyte = lit.value, tgt_unit, analyte
            return
        if tgt_unit is None:
            self.err(lit, "E030", f"unit '{lit.unit_src}' used where a plain number is expected")
            return
        if lit.unit_src == tgt_unit:
            lit.canon, lit.unit, lit.analyte = lit.value, tgt_unit, analyte
            return
        spec = self.schema.obs.get(analyte) if analyte else None
        factor = spec.conversions.get(lit.unit_src) if spec else None
        if factor is None:
            self.err(lit, "E031", f"no conversion from '{lit.unit_src}' to '{tgt_unit}' for \"{analyte}\" in the schema (add it under [observations.{analyte}.conversions])")
            return
        lit.canon, lit.unit, lit.analyte = lit.value * factor, tgt_unit, analyte


def check_library(lib: A.Library, schema: Schema) -> list[Diagnostic]:
    return Checker(lib, schema).run()


def errors(diags: list[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diags if d.severity == "error"]


def require_ok(lib: A.Library, schema: Schema) -> A.Library:
    diags = check_library(lib, schema)
    if errors(diags):
        raise LemmaCheckError(diags)
    return lib
