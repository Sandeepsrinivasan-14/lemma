"""Semantic diff (M3): which patients get a different answer from rule version A vs B?

Unlike a text diff this is computed on behaviour. For two versions of a rule, Lemma finds
  * a smallest realistic patient who GAINS the recommendation (B fires, A does not),
  * a smallest realistic patient who LOSES it (A fires, B does not),
  * or proves there is none (the versions are equivalent within the stated bounds).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

import z3

from ..interp import Interpreter, tri_label
from ..lang import ast as A
from ..lang import pretty
from ..numeric import fmt
from ..patient import Patient
from ..schema import Schema
from ..smt import build_problem
from .common import BOUNDS_NOTE, DEFAULT_TIMEOUT_MS, EncodingMismatch, find_patient, is_satisfiable


@dataclass
class Witness:
    direction: str  # "gained" | "lost" | "differs"
    patient: Patient
    a: str  # result of version A: true | false | null
    b: str  # result of version B
    summary: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {"direction": self.direction, "patient": self.patient.to_dict(), "a": self.a, "b": self.b}
        if self.summary is not None:
            d["zone"] = self.summary
        return d


@dataclass
class DiffResult:
    rule: str
    status: str  # "equivalent" | "different" | "unknown"
    gained: Optional[Witness] = None
    lost: Optional[Witness] = None
    transitions: dict = field(default_factory=dict)  # strict mode: "true->null" -> Witness
    unknown: list = field(default_factory=list)
    syntactic: list = field(default_factory=list)
    seconds: float = 0.0
    mode: str = "fires"
    note: str = BOUNDS_NOTE

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "status": self.status,
            "mode": self.mode,
            "gained": self.gained.to_dict() if self.gained else None,
            "lost": self.lost.to_dict() if self.lost else None,
            "transitions": {k: v.to_dict() for k, v in self.transitions.items()},
            "unknown": self.unknown,
            "syntactic_changes": self.syntactic,
            "seconds": round(self.seconds, 3),
            "note": self.note,
        }


def syntactic_changes(a: A.Library, b: A.Library) -> list[dict]:
    out = []
    for name, d in a.defines.items():
        if name not in b.defines:
            out.append({"define": name, "change": "removed", "before": pretty.expr(d.body), "after": None})
        else:
            x, y = pretty.expr(d.body), pretty.expr(b.defines[name].body)
            if x != y:
                out.append({"define": name, "change": "changed", "before": x, "after": y})
    for name, d in b.defines.items():
        if name not in a.defines:
            out.append({"define": name, "change": "added", "before": None, "after": pretty.expr(d.body)})
    return out


def _state(tri) -> str:
    return "true" if tri is True else ("false" if tri is False else "null")


def _verify(schema: Schema, lib_a, lib_b, rule_a, rule_b, patient: Patient, want_a: Optional[bool], want_b: Optional[bool], fires_only: bool):
    """Replay the witness through the independent interpreter; any disagreement is a Lemma bug."""
    ra = Interpreter(lib_a, schema).evaluate(patient, rule_a)
    rb = Interpreter(lib_b, schema).evaluate(patient, rule_b)
    if fires_only:
        ok = (ra is True) == bool(want_a) and (rb is True) == bool(want_b)
    else:
        ok = ra == want_a and rb == want_b
    if not ok:
        raise EncodingMismatch(f"witness replay disagrees with the solver: A={ra} B={rb}, expected A={want_a} B={want_b}; patient={patient.to_dict()}")
    return _state(ra), _state(rb)


def zone_summary(schema: Schema, problem, assertions: list, timeout_ms: int = 8000) -> dict:
    """Bounding box of a zone: for each variable, can it be absent/present and what range can it take."""
    sym = problem.sym
    base = list(problem.domain) + list(assertions)

    def sat(extra) -> bool:
        s = z3.Solver()
        s.set(timeout=timeout_ms)
        s.add(base + extra)
        return s.check() == z3.sat

    def bound(expr, extra, lo: bool) -> Optional[Fraction]:
        o = z3.Optimize()
        o.set(timeout=timeout_ms)
        o.add(base + extra)
        h = o.minimize(expr) if lo else o.maximize(expr)
        if o.check() != z3.sat:
            return None
        v = o.lower(h) if lo else o.upper(h)
        try:
            return Fraction(v.as_fraction()) if hasattr(v, "as_fraction") else Fraction(v.as_long())
        except Exception:  # noqa: BLE001
            return None

    out: dict = {"age": {}, "observations": {}, "flags": {}}
    out["age"] = {"min": int(bound(sym.age, [], True)), "max": int(bound(sym.age, [], False))}
    for c, spec in schema.obs.items():
        o = sym.obs[c]
        can_p, can_a = sat([o.present]), sat([z3.Not(o.present)])
        entry = {"presence": "always" if can_p and not can_a else ("never" if can_a and not can_p else "sometimes")}
        if can_p:
            lo = bound(o.val, [o.present], True)
            hi = bound(o.val, [o.present], False)
            entry["min"], entry["max"] = (fmt(lo) if lo is not None else None), (fmt(hi) if hi is not None else None)
            entry["unit"] = spec.unit
        out["observations"][c] = entry
    for c, f in sym.flags.items():
        can_t, can_f = sat([f]), sat([z3.Not(f)])
        out["flags"][c] = "always" if can_t and not can_f else ("never" if can_f and not can_t else "sometimes")
    return out


def semantic_diff(
    schema: Schema,
    lib_a: A.Library,
    lib_b: A.Library,
    rule: str,
    rule_b: Optional[str] = None,
    *,
    mode: str = "fires",
    summary: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> DiffResult:
    t0 = time.time()
    rule_b = rule_b or rule
    for lib, name in ((lib_a, rule), (lib_b, rule_b)):
        if name not in lib.defines:
            raise KeyError(f'rule "{name}" is not defined in library {lib.name} {lib.version or ""}'.strip())
    problem = build_problem(schema, {"a": lib_a, "b": lib_b})
    ta, tb = problem.encoder("a").tri(rule), problem.encoder("b").tri(rule_b)
    res = DiffResult(rule=rule, status="equivalent", mode=mode, syntactic=syntactic_changes(lib_a, lib_b))

    def attempt(direction: str, assertion, want_a, want_b, key: Optional[str] = None):
        r = find_patient(problem, [assertion], timeout_ms=timeout_ms, pid=direction)
        if r.status == "unknown":
            res.unknown.append(key or direction)
            return None
        if r.status == "unsat":
            return None
        a, b = _verify(schema, lib_a, lib_b, rule, rule_b, r.patient, want_a, want_b, fires_only=(mode == "fires"))
        w = Witness(direction, r.patient, a, b)
        if summary:
            w.summary = zone_summary(schema, problem, [assertion])
        return w

    if mode == "fires":
        res.gained = attempt("gained", z3.And(tb.t, z3.Not(ta.t)), False, True)
        res.lost = attempt("lost", z3.And(ta.t, z3.Not(tb.t)), True, False)
    elif mode == "strict":
        states = {"true": (lambda x: x.t), "false": (lambda x: x.f), "null": (lambda x: z3.Not(z3.Or(x.t, x.f)))}
        want = {"true": True, "false": False, "null": None}
        for sa in states:
            for sb in states:
                if sa == sb:
                    continue
                w = attempt("differs", z3.And(states[sa](ta), states[sb](tb)), want[sa], want[sb], key=f"{sa}->{sb}")
                if w:
                    res.transitions[f"{sa}->{sb}"] = w
    else:
        raise ValueError("mode must be 'fires' or 'strict'")

    found = res.gained or res.lost or res.transitions
    res.status = "different" if found else ("unknown" if res.unknown else "equivalent")
    res.seconds = time.time() - t0
    return res
