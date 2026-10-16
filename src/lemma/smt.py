"""SMT (Z3) encoding of CQL-lite with null, unit, time and trend semantics.

Representation
  * Every observation value lives on the schema's measurement grid: value = k * step with k an Int.
    Witnesses are therefore realistic measurements (no 29.99999 mg/dL) and linear integer
    arithmetic keeps Z3 fast and its optimiser well defined.
  * Booleans are encoded as a pair (t, f) = (definitely true, definitely false);
    null is "neither". Numbers are (null, value).
  * History is bounded: one latest value plus at most one prior value per observation
    that a rule applies a trend operator to.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import NamedTuple, Optional

import z3

from .lang import ast as A
from .lang.check import Checker, errors
from .lang.parser import parse_expression
from .patient import ObsValue, Patient
from .schema import Schema

TRUE, FALSE = z3.BoolVal(True), z3.BoolVal(False)


class GridError(ValueError):
    """A concrete patient has a value that is not on the schema's measurement grid."""


def rv(x: Fraction):
    if x.denominator == 1:
        return z3.RealVal(x.numerator)
    return z3.RealVal(f"{x.numerator}/{x.denominator}")


class Tri(NamedTuple):
    t: z3.BoolRef
    f: z3.BoolRef


class Numv(NamedTuple):
    null: z3.BoolRef
    v: z3.ArithRef


class Strv(NamedTuple):
    idx: z3.ArithRef


@dataclass
class SymObs:
    code: str
    present: z3.BoolRef
    k: z3.ArithRef
    val: z3.ArithRef
    prior_present: Optional[z3.BoolRef] = None
    kp: Optional[z3.ArithRef] = None
    prior: Optional[z3.ArithRef] = None
    gap: Optional[z3.ArithRef] = None


class SymPatient:
    """A symbolic patient: one set of Z3 variables describing every possible patient."""

    def __init__(self, schema: Schema, trend_codes=(), prefix: str = "p", base: "SymPatient | None" = None, missing=()):
        self.schema = schema
        self.prefix = prefix
        self.trend = set(trend_codes) if base is None else set(base.trend)
        self.missing = set(missing)
        if base is not None:
            self.age, self.sex, self.flags = base.age, base.sex, base.flags
            self.obs = {}
            for c, o in base.obs.items():
                if c in self.missing:
                    self.obs[c] = SymObs(c, FALSE, o.k, o.val, FALSE if o.prior_present is not None else None, o.kp, o.prior, o.gap)
                else:
                    self.obs[c] = o
        else:
            self.age = z3.Int(f"{prefix}.age")
            self.sex = z3.Int(f"{prefix}.sex")
            self.flags = {c: z3.Bool(f"{prefix}.flag.{c}") for c in schema.flag_codes}
            self.obs = {}
            for c, spec in schema.obs.items():
                k = z3.Int(f"{prefix}.{c}.k")
                present = z3.Bool(f"{prefix}.{c}.present")
                so = SymObs(c, present, k, z3.ToReal(k) * rv(spec.step))
                if c in self.trend:
                    so.prior_present = z3.Bool(f"{prefix}.{c}.prior_present")
                    so.kp = z3.Int(f"{prefix}.{c}.kp")
                    so.prior = z3.ToReal(so.kp) * rv(spec.step)
                    so.gap = z3.Int(f"{prefix}.{c}.gap")
                self.obs[c] = so
        self.age_val = z3.ToReal(self.age)

    # -- the plausible domain ------------------------------------------------
    def ranges(self) -> list:
        s = self.schema
        out = [z3.And(self.age >= s.age_lo, self.age <= s.age_hi), z3.And(self.sex >= 0, self.sex < len(s.sexes))]
        for c, spec in s.obs.items():
            o = self.obs[c]
            out.append(z3.Implies(o.present, z3.And(o.k >= spec.k_lo, o.k <= spec.k_hi)))
            out.append(z3.Implies(z3.Not(o.present), o.k == 0))
            if o.prior_present is not None:
                out.append(z3.Implies(o.prior_present, z3.And(o.present, o.kp >= spec.k_lo, o.kp <= spec.k_hi, o.gap >= 1, o.gap <= spec.max_gap_hours)))
                out.append(z3.Implies(z3.Not(o.prior_present), z3.And(o.kp == 0, o.gap == 0)))
        return out

    # -- concrete <-> symbolic -----------------------------------------------
    def substitution(self, p: Patient) -> list:
        """(variable, value) pairs that pin this symbolic patient to a concrete one."""
        s = self.schema
        pairs = []
        if p.age.denominator != 1:
            raise GridError("age must be a whole number of years")
        pairs.append((self.age, z3.IntVal(int(p.age))))
        if p.sex not in s.sexes:
            raise GridError(f"unknown sex {p.sex!r}")
        pairs.append((self.sex, z3.IntVal(s.sexes.index(p.sex))))
        for c, fv in self.flags.items():
            pairs.append((fv, z3.BoolVal(c in p.conditions or c in p.medications)))
        for c, spec in s.obs.items():
            so = self.obs[c]
            ov = p.obs.get(c, ObsValue())
            if c in self.missing:
                continue
            pairs.append((so.present, z3.BoolVal(ov.value is not None)))
            pairs.append((so.k, z3.IntVal(self._k(spec, ov.value))))
            if so.prior_present is not None:
                has_prior = ov.prior is not None and ov.value is not None
                pairs.append((so.prior_present, z3.BoolVal(has_prior)))
                pairs.append((so.kp, z3.IntVal(self._k(spec, ov.prior) if has_prior else 0)))
                gap = ov.gap_hours if has_prior else Fraction(0)
                if gap is None or gap.denominator != 1:
                    raise GridError("gap_hours must be a whole number of hours")
                pairs.append((so.gap, z3.IntVal(int(gap))))
        return pairs

    @staticmethod
    def _k(spec, value: Optional[Fraction]) -> int:
        if value is None:
            return 0
        q = value / spec.step
        if q.denominator != 1:
            raise GridError(f"{spec.code}={value} is not a multiple of the grid step {spec.step}")
        return int(q)

    def to_patient(self, m: z3.ModelRef, pid: str = "") -> Patient:
        s = self.schema

        def iv(x) -> int:
            return m.eval(x, model_completion=True).as_long()

        def bv(x) -> bool:
            return z3.is_true(m.eval(x, model_completion=True))

        obs: dict[str, ObsValue] = {}
        for c, spec in s.obs.items():
            so = self.obs[c]
            if not bv(so.present):
                continue
            ov = ObsValue(value=iv(so.k) * spec.step)
            if so.prior_present is not None and bv(so.prior_present):
                ov.prior = iv(so.kp) * spec.step
                ov.gap_hours = Fraction(iv(so.gap))
            obs[c] = ov
        conds = {c for c in s.conditions if bv(self.flags[c])}
        meds = {c for c in s.medications if bv(self.flags[c])}
        return Patient(age=Fraction(iv(self.age)), sex=s.sexes[iv(self.sex)], obs=obs, conditions=conds, medications=meds, id=pid)

    # -- optimisation hints --------------------------------------------------
    def complexity(self):
        """Number of facts a patient needs: fewer is a smaller, easier-to-read witness."""
        terms = []
        for o in self.obs.values():
            terms.append(z3.If(o.present, 1, 0))
            if o.prior_present is not None:
                terms.append(z3.If(o.prior_present, 1, 0))
        for f in self.flags.values():
            terms.append(z3.If(f, 1, 0))
        return z3.Sum(terms) if terms else z3.IntVal(0)

    def deviation(self):
        """Distance from a 'typical' patient, normalised by range (prefers ordinary-looking values)."""
        s = self.schema
        terms = []

        def dev(k, kt, span):
            return z3.ToReal(z3.If(k >= kt, k - kt, kt - k)) * rv(Fraction(1, max(span, 1)))

        terms.append(dev(self.age, s.age_typical, s.age_hi - s.age_lo))
        for c, spec in s.obs.items():
            o = self.obs[c]
            kt = int(spec.typical / spec.step)
            span = spec.k_hi - spec.k_lo
            terms.append(z3.If(o.present, dev(o.k, kt, span), z3.RealVal(0)))
            if o.prior_present is not None:
                terms.append(z3.If(o.prior_present, dev(o.kp, kt, span), z3.RealVal(0)))
        return z3.Sum(terms)


class Encoder:
    """Encode a checked library's defines over a symbolic patient."""

    def __init__(self, lib: A.Library, schema: Schema, sym: SymPatient):
        self.lib, self.schema, self.sym = lib, schema, sym
        self.memo: dict[str, object] = {}

    def define(self, name: str):
        if name not in self.memo:
            self.memo[name] = self.ex(self.lib.defines[name].body)
        return self.memo[name]

    def fires(self, name: str) -> z3.BoolRef:
        v = self.define(name)
        if not isinstance(v, Tri):
            raise ValueError(f'"{name}" is not a boolean rule')
        return v.t

    def tri(self, name: str) -> Tri:
        v = self.define(name)
        if not isinstance(v, Tri):
            raise ValueError(f'"{name}" is not a boolean rule')
        return v

    def ex(self, n: A.Node):  # noqa: C901
        t = type(n)
        if t is A.Num:
            return Numv(FALSE, rv(n.canon))
        if t is A.Bool:
            return Tri(TRUE, FALSE) if n.value else Tri(FALSE, TRUE)
        if t is A.Str:
            return Strv(z3.IntVal(self.schema.sexes.index(n.value)))
        if t is A.Ref:
            return self.define(n.name)
        if t is A.Call:
            return self.call(n)
        if t is A.Unary:
            x = self.ex(n.x)
            if n.op == "not":
                return Tri(x.f, x.t)
            return Numv(x.null, -x.v)
        if t is A.IsNull:
            x = self.ex(n.x)
            if isinstance(x, Numv):
                isnull = x.null
            elif isinstance(x, Tri):
                isnull = z3.Not(z3.Or(x.t, x.f))
            else:
                isnull = FALSE
            return Tri(z3.Not(isnull), isnull) if n.negated else Tri(isnull, z3.Not(isnull))
        if t is A.Bin:
            return self.binary(n)
        raise TypeError(f"cannot encode {t.__name__}")

    def trend_cond(self, o: SymObs, window_hours: Fraction):
        return z3.And(o.present, o.prior_present, z3.ToReal(o.gap) <= rv(window_hours))

    def call(self, n: A.Call):
        name = n.name
        s = self.sym
        if name == "Latest":
            o = s.obs[n.args[0].code]
            return Numv(z3.Not(o.present), o.val)
        if name == "Delta":
            o = s.obs[n.args[0].code]
            return Numv(z3.Not(self.trend_cond(o, n.args[1].hours)), o.val - o.prior)
        if name == "Ratio":
            raise ValueError("Ratio(...) is only encodable inside a comparison")
        if name == "AgeInYears":
            return Numv(FALSE, s.age_val)
        if name == "Sex":
            return Strv(s.sex)
        if name in ("HasCondition", "OnMedication"):
            f = s.flags[n.args[0].code]
            return Tri(f, z3.Not(f))
        if name == "Coalesce":
            a, b = self.ex(n.args[0]), self.ex(n.args[1])
            if isinstance(a, Numv):
                return Numv(z3.And(a.null, b.null), z3.If(a.null, b.v, a.v))
            na = z3.Not(z3.Or(a.t, a.f))
            return Tri(z3.Or(a.t, z3.And(na, b.t)), z3.Or(a.f, z3.And(na, b.f)))
        raise ValueError(f"unsupported function {name}")

    def binary(self, n: A.Bin):  # noqa: C901
        op = n.op
        if op in ("and", "or"):
            a, b = self.ex(n.l), self.ex(n.r)
            if op == "and":
                return Tri(z3.And(a.t, b.t), z3.Or(a.f, b.f))
            return Tri(z3.Or(a.t, b.t), z3.And(a.f, b.f))
        if op in A.COMPARE_OPS:
            return self.compare(n)
        a, b = self.ex(n.l), self.ex(n.r)
        null = z3.Or(a.null, b.null)
        if op == "+":
            return Numv(null, a.v + b.v)
        if op == "-":
            return Numv(null, a.v - b.v)
        if op == "*":
            return Numv(null, a.v * b.v)
        if op == "/":
            return Numv(a.null, a.v * rv(Fraction(1) / n.r.canon))
        raise ValueError(op)

    @staticmethod
    def _cmp(op: str, x, y):
        return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y, "=": x == y, "!=": x != y}[op]

    def compare(self, n: A.Bin):
        op = n.op
        l, r = n.l, n.r
        ratio, lit, flip = None, None, False
        if isinstance(l, A.Call) and l.name == "Ratio":
            ratio, lit = l, r
        elif isinstance(r, A.Call) and r.name == "Ratio":
            ratio, lit, flip = r, l, True
        if ratio is not None:
            o = self.sym.obs[ratio.args[0].code]
            cond = self.trend_cond(o, ratio.args[1].hours)
            k = rv(lit.canon)
            # ratio = value/prior and prior > 0, so ratio OP k  <=>  value OP k*prior
            eff = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=", "!=": "!="}[op] if flip else op
            c = self._cmp(eff, o.val, k * o.prior)
            return Tri(z3.And(cond, c), z3.And(cond, z3.Not(c)))
        a, b = self.ex(l), self.ex(r)
        if isinstance(a, Strv):
            c = a.idx == b.idx if op == "=" else a.idx != b.idx
            return Tri(c, z3.Not(c))
        null = z3.Or(a.null, b.null)
        c = self._cmp(op, a.v, b.v)
        return Tri(z3.And(z3.Not(null), c), z3.And(z3.Not(null), z3.Not(c)))


# --------------------------------------------------------------------------
@dataclass
class Problem:
    """A symbolic patient, its domain constraints, and one encoder per library."""

    schema: Schema
    sym: SymPatient
    encoders: dict
    domain: list

    def encoder(self, key) -> Encoder:
        return self.encoders[key]


def constraint_library(schema: Schema) -> tuple[A.Library, list[str]]:
    """Parse and check the schema's plausibility constraints as a pseudo-library."""
    defines, msgs = {}, []
    for name, text in schema.constraints:
        node = parse_expression(text)
        defines[name] = A.Define(name, node)
    lib = A.Library(name="constraints", version=None, defines=defines, source="")
    ck = Checker(lib, schema)
    # each constraint has its own source text for line/col
    for name, text in schema.constraints:
        ck._src = text
        ck.ex(lib.defines[name].body)
    errs = errors(ck.diags)
    if errs:
        raise ValueError("schema constraint errors: " + "; ".join(str(e) for e in errs))
    for d in lib.defines.values():
        if d.body.kind != "bool":
            raise ValueError(f"schema constraint {d.name!r} is not a boolean expression")
    return lib, msgs


def build_problem(schema: Schema, libs: dict, prefix: str = "p") -> Problem:
    trend = set()
    for lib in libs.values():
        trend |= lib.trend_codes
    clib, _ = constraint_library(schema)
    trend |= clib.trend_codes
    sym = SymPatient(schema, trend, prefix)
    domain = list(sym.ranges())
    cenc = Encoder(clib, schema, sym)
    for name in clib.defines:
        domain.append(z3.Not(cenc.tri(name).f))  # a plausibility constraint may be true or null, never false
    encoders = {k: Encoder(lib, schema, sym) for k, lib in libs.items()}
    return Problem(schema, sym, encoders, domain)


def eval_bool(expr, subst) -> Optional[bool]:
    v = z3.simplify(z3.substitute(expr, *subst))
    if z3.is_true(v):
        return True
    if z3.is_false(v):
        return False
    return None


def tri_value(tri: Tri, subst) -> Optional[bool]:
    t, f = eval_bool(tri.t, subst), eval_bool(tri.f, subst)
    if t is None or f is None:
        raise ValueError("expression did not reduce to a constant under the substitution")
    if t:
        return True
    if f:
        return False
    return None
