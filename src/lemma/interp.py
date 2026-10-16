"""Concrete reference interpreter for CQL-lite with CQL's three-valued logic.

It is deliberately independent of the SMT encoder: two implementations of the same
semantics that must agree (see experiment E1 and the Hypothesis tests).
Values: True/False/None for booleans, Fraction/None for numbers, str for sex.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Optional

from .lang import ast as A
from .patient import Patient
from .schema import Schema


class Evaluation:
    def __init__(self, lib: A.Library, patient: Patient):
        self.lib = lib
        self.p = patient
        self.memo: dict[str, object] = {}

    def define(self, name: str):
        if name not in self.memo:
            self.memo[name] = self.ex(self.lib.defines[name].body)
        return self.memo[name]

    def ex(self, n: A.Node):  # noqa: C901 - one flat dispatcher keeps the semantics readable
        t = type(n)
        if t is A.Num:
            return n.canon
        if t is A.Bool or t is A.Str:
            return n.value
        if t is A.Ref:
            return self.define(n.name)
        if t is A.Call:
            return self.call(n)
        if t is A.Unary:
            v = self.ex(n.x)
            if v is None:
                return None
            return (not v) if n.op == "not" else -v
        if t is A.IsNull:
            v = self.ex(n.x)
            isnull = v is None
            return (not isnull) if n.negated else isnull
        if t is A.Bin:
            return self.binary(n)
        raise TypeError(f"cannot evaluate {t.__name__}")

    def call(self, n: A.Call):
        name = n.name
        if name == "Latest":
            return self.p.latest(n.args[0].code)
        if name in ("Delta", "Ratio"):
            o = self.p.obs.get(n.args[0].code)
            if o is None or o.value is None or o.prior is None or o.gap_hours is None:
                return None
            if o.gap_hours > n.args[1].hours:
                return None
            if name == "Delta":
                return o.value - o.prior
            if o.prior <= 0:
                return None
            return o.value / o.prior
        if name == "AgeInYears":
            return self.p.age
        if name == "Sex":
            return self.p.sex
        if name == "HasCondition":
            return n.args[0].code in self.p.conditions
        if name == "OnMedication":
            return n.args[0].code in self.p.medications
        if name == "Coalesce":
            a = self.ex(n.args[0])
            return a if a is not None else self.ex(n.args[1])
        raise ValueError(f"unsupported function {name}")

    def binary(self, n: A.Bin):
        op = n.op
        if op == "and":
            a = self.ex(n.l)
            b = self.ex(n.r)
            if a is False or b is False:
                return False
            if a is None or b is None:
                return None
            return True
        if op == "or":
            a = self.ex(n.l)
            b = self.ex(n.r)
            if a is True or b is True:
                return True
            if a is None or b is None:
                return None
            return False
        a, b = self.ex(n.l), self.ex(n.r)
        if a is None or b is None:
            return None
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "=":
            return a == b
        if op == "!=":
            return a != b
        raise ValueError(op)


class Interpreter:
    def __init__(self, lib: A.Library, schema: Schema):
        self.lib = lib
        self.schema = schema

    def evaluate(self, patient: Patient, define: str) -> Optional[bool]:
        return Evaluation(self.lib, patient).define(define)

    def fires(self, patient: Patient, define: str) -> bool:
        """CDS convention: a rule fires only when it is definitely true (null does not fire)."""
        return self.evaluate(patient, define) is True

    def run_all(self, patient: Patient) -> dict:
        ev = Evaluation(self.lib, patient)
        return {name: ev.define(name) for name in self.lib.defines}


def tri_label(v) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, Fraction):
        from .numeric import fmt

        return fmt(v)
    return str(v)
