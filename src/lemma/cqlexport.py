"""Export a Lemma library to real (HL7) CQL so it can be run by the official translator and engine.

The patient is passed as CQL *parameters* (obs_<code>, obs_<code>_prior, obs_<code>_gap, cond_<c>, med_<m>,
Age, Sex), in an Unfiltered context: no FHIR data model is needed, and every Lemma construct maps to a
standard CQL operator, so this is an independent check of Lemma's three-valued semantics.
Units are already canonicalised by the Lemma checker; the exported CQL compares plain decimals.
"""
from __future__ import annotations

from . import numeric
from .lang import ast as A
from .patient import Patient
from .schema import Schema


def _q(name: str) -> str:
    return '"' + name.replace('"', '\\"') + '"'


class _Exporter:
    def __init__(self, lib: A.Library):
        self.lib = lib
        self.obs, self.trend, self.conds, self.meds = set(), set(), set(), set()
        self.uses_age = self.uses_sex = False

    def ex(self, n: A.Node) -> str:  # noqa: C901
        t = type(n)
        if t is A.Num:
            return numeric.fmt(n.canon)
        if t is A.Bool:
            return "true" if n.value else "false"
        if t is A.Str:
            return "'" + n.value.replace("'", "\\'") + "'"
        if t is A.Ref:
            return _q(n.name)
        if t is A.Unary:
            return f"not ({self.ex(n.x)})" if n.op == "not" else f"-({self.ex(n.x)})"
        if t is A.IsNull:
            return f"(({self.ex(n.x)}) is {'not ' if n.negated else ''}null)"
        if t is A.Bin:
            return f"({self.ex(n.l)} {n.op} {self.ex(n.r)})"
        if t is A.Call:
            return self.call(n)
        raise TypeError(f"cannot export {t.__name__}")

    def call(self, n: A.Call) -> str:
        name = n.name
        if name == "Latest":
            c = n.args[0].code
            self.obs.add(c)
            return f"obs_{c}"
        if name in ("Delta", "Ratio"):
            c, h = n.args[0].code, numeric.fmt(n.args[1].hours)
            self.obs.add(c)
            self.trend.add(c)
            v, p, g = f"obs_{c}", f"obs_{c}_prior", f"obs_{c}_gap"
            guard = f"{v} is null or {p} is null or {g} is null or {g} > {h}"
            if name == "Delta":
                return f"(if {guard} then null else {v} - {p})"
            return f"(if {guard} or {p} <= 0 then null else {v} / {p})"
        if name == "AgeInYears":
            self.uses_age = True
            return "Age"
        if name == "Sex":
            self.uses_sex = True
            return "Sex"
        if name == "HasCondition":
            self.conds.add(n.args[0].code)
            return f"cond_{n.args[0].code}"
        if name == "OnMedication":
            self.meds.add(n.args[0].code)
            return f"med_{n.args[0].code}"
        if name == "Coalesce":
            return f"Coalesce({self.ex(n.args[0])}, {self.ex(n.args[1])})"
        raise ValueError(f"unsupported function {name}")


def export_cql(lib: A.Library) -> tuple[str, dict]:
    """Return (CQL text, parameter spec {name: kind})."""
    e = _Exporter(lib)
    body = []
    for name, d in lib.defines.items():
        body.append(f"define {_q(name)}:\n  {e.ex(d.body)}\n")
    params: dict[str, str] = {}
    if e.uses_age:
        params["Age"] = "Decimal"
    if e.uses_sex:
        params["Sex"] = "String"
    for c in sorted(e.obs):
        params[f"obs_{c}"] = "Decimal"
        if c in e.trend:
            params[f"obs_{c}_prior"] = "Decimal"
            params[f"obs_{c}_gap"] = "Decimal"
    for c in sorted(e.conds):
        params[f"cond_{c}"] = "Boolean"
    for c in sorted(e.meds):
        params[f"med_{c}"] = "Boolean"
    head = f"library {lib.name}Export version '{lib.version or '0'}'\n\n"
    head += "".join(f"parameter {k} {v}\n" for k, v in params.items()) + "\ncontext Unfiltered\n\n"
    return head + "\n".join(body), params


def patient_params(p: Patient, params: dict) -> dict:
    out: dict = {}
    for k in params:
        if k == "Age":
            out[k] = float(p.age)
        elif k == "Sex":
            out[k] = p.sex
        elif k.startswith("cond_"):
            out[k] = k[5:] in p.conditions
        elif k.startswith("med_"):
            out[k] = k[4:] in p.medications
        else:
            body = k[4:]
            field = ""
            for suffix, f in (("_prior", "prior"), ("_gap", "gap")):
                if body.endswith(suffix):
                    body, field = body[: -len(suffix)], f
            o = p.obs.get(body)
            raw = None if o is None else {"": o.value, "prior": o.prior, "gap": o.gap_hours}[field]
            out[k] = None if raw is None else float(raw)
    return out
