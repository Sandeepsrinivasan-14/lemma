"""Threshold robustness (M14): how much measurement error would flip this recommendation?

For a concrete patient and a rule, find the smallest simultaneous change of the patient's measurements,
measured in multiples of each observation's declared tolerance (schema `tolerance`), that flips the
decision. A distance of 1.0 or less means ordinary assay noise or rounding can change the outcome.
Presence of data, age, sex, conditions and medications are held fixed; only measured values move.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

import z3

from ..interp import Interpreter
from ..lang import ast as A
from ..patient import Patient
from ..schema import Schema
from ..smt import Problem, build_problem, rv
from .common import EncodingMismatch


@dataclass
class FlipResult:
    patient_id: str
    fires: bool
    distance: Optional[Fraction]  # in tolerance units; None = no plausible measurement change flips it
    changes: dict  # code -> (old, new)
    flipped: Optional[Patient]

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "currently_fires": self.fires,
            "flip_distance_in_tolerances": None if self.distance is None else round(float(self.distance), 3),
            "fragile": self.distance is not None and self.distance <= 1,
            "changes": {k: {"from": float(a), "to": float(b)} for k, (a, b) in self.changes.items()},
        }


def flip_distance(schema: Schema, lib: A.Library, rule: str, patient: Patient, *, problem: Optional[Problem] = None, timeout_ms: int = 5000) -> FlipResult:
    problem = problem or build_problem(schema, {"lib": lib})
    sym, enc = problem.sym, problem.encoder("lib")
    interp = Interpreter(lib, schema)
    fires0 = interp.fires(patient, rule)
    fires = enc.fires(rule)
    o = z3.Optimize()
    o.set(timeout=timeout_ms)
    o.add(problem.domain)
    t = z3.Real("t")
    o.add(t >= 0)
    movable: dict[str, tuple[str, object, int]] = {}
    for c, spec in schema.obs.items():
        so = sym.obs[c]
        ov = patient.obs.get(c)
        if c in lib.used_codes and spec.tolerance > 0 and ov is not None and ov.value is not None:
            movable[so.k.decl().name()] = (c, so.k, int(ov.value / spec.step))
            if so.kp is not None and ov.prior is not None:
                movable[so.kp.decl().name()] = (c + ":prior", so.kp, int(ov.prior / spec.step))
    for var, val in sym.substitution(patient):
        if var.decl().name() in movable:
            continue
        o.add(var == val)
    for name, (label, k, k0) in movable.items():
        c = label.split(":")[0]
        spec = schema.obs[c]
        o.add(z3.ToReal(k - k0) * rv(spec.step) <= t * rv(spec.tolerance))
        o.add(z3.ToReal(k0 - k) * rv(spec.step) <= t * rv(spec.tolerance))
    o.add(z3.Not(fires) if fires0 else fires)
    h = o.minimize(t)
    if o.check() != z3.sat:
        return FlipResult(patient.id, fires0, None, {}, None)
    m = o.model()
    tv = m.eval(t, model_completion=True)
    dist = Fraction(tv.as_fraction()) if hasattr(tv, "as_fraction") else Fraction(tv.as_long())
    flipped = sym.to_patient(m, patient.id + "-flipped")
    if interp.fires(flipped, rule) == fires0:
        raise EncodingMismatch(f"robustness witness did not flip the decision: {flipped.to_dict()}")
    changes = {}
    for c in schema.obs:
        a, b = patient.obs.get(c), flipped.obs.get(c)
        if a and b:
            if a.value != b.value:
                changes[c] = (a.value, b.value)
            if a.prior != b.prior and a.prior is not None and b.prior is not None:
                changes[c + ":prior"] = (a.prior, b.prior)
    return FlipResult(patient.id, fires0, dist, changes, flipped)


def robustness_summary(schema: Schema, lib: A.Library, rule: str, patients: list[Patient], *, limit: int = 200) -> dict:
    problem = build_problem(schema, {"lib": lib})
    sample = patients[:limit]
    results = [flip_distance(schema, lib, rule, p, problem=problem) for p in sample]
    n = len(results)
    buckets = {"<=0.5": 0, "0.5-1": 0, "1-2": 0, "2-5": 0, ">5": 0, "never flips": 0}
    drivers: Counter = Counter()
    for r in results:
        d = r.distance
        if d is None:
            buckets["never flips"] += 1
            continue
        d = float(d)
        key = "<=0.5" if d <= 0.5 else "0.5-1" if d <= 1 else "1-2" if d <= 2 else "2-5" if d <= 5 else ">5"
        buckets[key] += 1
        if d <= 1:
            for c in r.changes:
                drivers[c.split(":")[0]] += 1
    fragile = sum(1 for r in results if r.distance is not None and r.distance <= 1)
    fires_n = sum(1 for r in results if r.fires)
    return {
        "rule": rule,
        "patients_evaluated": n,
        "currently_fire": fires_n,
        "fragile": fragile,
        "fragile_share": round(fragile / n, 4) if n else 0.0,
        "distance_buckets_in_tolerances": buckets,
        "most_common_driver_of_fragile_flips": dict(drivers.most_common(5)),
        "reading": "A patient is fragile if measurement changes within the declared per-observation tolerance can flip the decision.",
        "examples": [r.to_dict() for r in sorted((r for r in results if r.distance is not None), key=lambda r: r.distance)[:5]],
    }
