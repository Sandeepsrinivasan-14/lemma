"""Service layer shared by the HTTP API and the CLI-facing helpers.

Everything here takes plain text (rule source, invariant source) so a UI can send what the user typed.
Z3 contexts are process-global and not thread safe, so every solver call goes through `Z3_LOCK`.
"""
from __future__ import annotations

import hashlib
import threading
from fractions import Fraction
from typing import Optional

from .analyses.common import load_library
from .analyses.diff import semantic_diff
from .analyses.impact import cohort_impact, top_changers
from .analyses.invariants import check_invariants, load_invariants
from .analyses.missing import missing_data_analysis
from .analyses.robustness import robustness_summary
from .cohort import generate_cohort
from .fhir import patient_bundle, structural_check
from .interp import Interpreter
from .lang import ast as A
from .lang import pretty
from .lang.check import LemmaCheckError, check_library
from .lang.lexer import LemmaSyntaxError
from .lang.parser import parse_library
from .numeric import fmt, to_frac
from .patient import ObsValue, Patient
from .schema import Schema
from .smt import constraint_library

Z3_LOCK = threading.RLock()


class Workspace:
    def __init__(self, schema: Schema):
        self.schema = schema
        self._libs: dict[str, A.Library] = {}
        self._constraints = constraint_library(schema)[0]
        self._cohorts: dict[tuple, list[Patient]] = {}

    # -- libraries -----------------------------------------------------------
    def lib(self, text: str) -> A.Library:
        key = hashlib.sha1(text.encode()).hexdigest()
        if key not in self._libs:
            self._libs[key] = load_library(text, self.schema, is_text=True)
            if len(self._libs) > 64:
                self._libs.pop(next(iter(self._libs)))
        return self._libs[key]

    def cohort(self, n: int, seed: int) -> list[Patient]:
        key = (n, seed)
        if key not in self._cohorts:
            self._cohorts[key] = generate_cohort(self.schema, n, seed)
            if len(self._cohorts) > 4:
                self._cohorts.pop(next(iter(self._cohorts)))
        return self._cohorts[key]

    # -- schema for UIs ------------------------------------------------------
    def schema_info(self) -> dict:
        s = self.schema
        return {
            "name": s.name,
            "age": {"min": s.age_lo, "max": s.age_hi, "typical": s.age_typical},
            "sexes": s.sexes,
            "observations": {
                c: {"label": o.label, "unit": o.unit, "min": float(o.lo), "max": float(o.hi), "step": float(o.step), "typical": float(o.typical), "tolerance": float(o.tolerance), "conversions": {u: float(f) for u, f in o.conversions.items()}}
                for c, o in s.obs.items()
            },
            "conditions": {c: f.label for c, f in s.conditions.items()},
            "medications": {c: f.label for c, f in s.medications.items()},
        }

    # -- check ---------------------------------------------------------------
    def check(self, text: str) -> dict:
        try:
            lib = parse_library(text)
        except LemmaSyntaxError as e:
            return {"ok": False, "diagnostics": [{"severity": "error", "code": "E000", "message": e.msg, "line": e.line, "col": e.col}], "rules": [], "literals": []}
        diags = check_library(lib, self.schema)
        ok = not any(d.severity == "error" for d in diags)
        rules = [n for n, d in lib.defines.items() if d.kind == "bool"] if ok else []
        return {"ok": ok, "diagnostics": [d.to_dict() for d in diags], "rules": rules, "literals": self.literals(lib) if ok else [], "uses": sorted(lib.used_codes), "uses_trend": sorted(lib.trend_codes)}

    def literals(self, lib: A.Library) -> list[dict]:
        out = []
        for dname, d in lib.defines.items():
            for n in A.walk(d.body):
                if not (isinstance(n, A.Bin) and n.op in A.COMPARE_OPS):
                    continue
                for a, b in ((n.l, n.r), (n.r, n.l)):
                    if not (isinstance(b, A.Num) and isinstance(a, A.Call)) or b.span is None:
                        continue
                    info = self._slider(a, b)
                    if info:
                        out.append({"define": dname, "text": pretty.expr(n), "value": float(b.value), "unit": b.unit_src, "start": b.span[0], "end": b.span[1], **info})
        out.sort(key=lambda x: x["start"])
        return out

    def _slider(self, a: A.Call, lit: A.Num) -> Optional[dict]:
        s = self.schema
        if a.name == "AgeInYears":
            return {"min": s.age_lo, "max": s.age_hi, "step": 1, "subject": "age"}
        if a.name == "Ratio":
            return {"min": 0.5, "max": 3.0, "step": 0.05, "subject": f"{a.args[0].code} ratio"}
        if a.name in ("Latest", "Delta") and a.args and isinstance(a.args[0], A.Code):
            spec = s.obs[a.args[0].code]
            factor = Fraction(1) if lit.unit_src in (None, spec.unit) else spec.conversions.get(lit.unit_src, Fraction(1))
            if a.name == "Latest":
                lo, hi, step = spec.lo / factor, spec.hi / factor, spec.step / factor if factor == 1 else Fraction(1, 10)
            else:
                lo, hi, step = Fraction(0), (spec.hi - spec.lo) / 4 / factor, spec.step / factor if factor == 1 else Fraction(1, 10)
            return {"min": float(lo), "max": float(hi), "step": float(step), "subject": spec.label if a.name == "Latest" else f"change in {spec.label}"}
        return None

    # -- analyses ------------------------------------------------------------
    def diff(self, a: str, b: str, rule: str, rule_b: Optional[str] = None, summary: bool = False, mode: str = "fires") -> dict:
        la, lb = self.lib(a), self.lib(b)
        with Z3_LOCK:
            res = semantic_diff(self.schema, la, lb, rule, rule_b, summary=summary, mode=mode)
        d = res.to_dict()
        d["fhir"] = {}
        for w in (res.gained, res.lost):
            if w:
                bundle = patient_bundle(self.schema, w.patient, bundle_id=f"lemma-{w.direction}")
                d["fhir"][w.direction] = {"bundle": bundle, "structural_problems": structural_check(bundle)}
        return d

    def verify(self, source: str, invariants_text: str) -> dict:
        lib = self.lib(source)
        all_invs = load_invariants(invariants_text, is_text=True)
        invs = [i for i in all_invs if i.rule in lib.defines]
        skipped = [i.name for i in all_invs if i.rule not in lib.defines]
        with Z3_LOCK:
            results = check_invariants(self.schema, lib, invs, inv_source=invariants_text)
        return {"results": [r.to_dict() for r in results], "violated": sum(r.status == "violated" for r in results), "holds": sum(r.status == "holds" for r in results), "skipped": skipped}

    def missing(self, source: str, rule: str) -> dict:
        with Z3_LOCK:
            return missing_data_analysis(self.schema, self.lib(source), rule).to_dict()

    def impact(self, a: str, b: str, rule: str, n: int = 3000, seed: int = 7, rule_b: Optional[str] = None) -> dict:
        la, lb = self.lib(a), self.lib(b)
        cohort = self.cohort(min(n, 20000), seed)
        res = cohort_impact(self.schema, la, lb, rule, cohort, rule_b)
        res["examples"] = top_changers(self.schema, la, lb, rule, cohort, 4, rule_b)
        res["cohort"] = {"synthetic": True, "n": len(cohort), "seed": seed}
        return res

    def robustness(self, source: str, rule: str, n: int = 100, seed: int = 7) -> dict:
        cohort = self.cohort(1000, seed)
        with Z3_LOCK:
            return robustness_summary(self.schema, self.lib(source), rule, cohort, limit=min(n, 300))

    # -- zone grid (concrete evaluation) ---------------------------------------
    def plausible(self, p: Patient) -> bool:
        ev = Interpreter(self._constraints, self.schema)
        return all(ev.evaluate(p, name) is not False for name in self._constraints.defines)

    def grid(self, a: str, b: str, rule: str, x: dict, y: dict, base: dict, rule_b: Optional[str] = None, nx: int = 56, ny: int = 56) -> dict:
        """Classify a grid of patients: 0 neither, 1 fires under both, 2 lost (A only), 3 gained (B only), -1 implausible."""
        la, lb = self.lib(a), self.lib(b)
        ia, ib = Interpreter(la, self.schema), Interpreter(lb, self.schema)
        rule_b = rule_b or rule
        template = Patient.from_dict(base)

        def axis(spec: dict, n: int):
            var = spec["var"]
            if var == "age":
                lo, hi, step = Fraction(self.schema.age_lo), Fraction(self.schema.age_hi), Fraction(1)
            else:
                o = self.schema.obs[var]
                lo, hi, step = o.lo, o.hi, o.step
            if spec.get("min") is not None:
                lo = max(lo, to_frac(spec["min"]))
            if spec.get("max") is not None:
                hi = min(hi, to_frac(spec["max"]))
            n = max(2, min(n, int((hi - lo) / step) + 1))
            vals = []
            for i in range(n):
                v = lo + (hi - lo) * Fraction(i, n - 1)
                vals.append(round(v / step) * step)
            return var, sorted(set(vals))

        xv, xs = axis(x, nx)
        yv, ys = axis(y, ny)
        cells = []
        for yval in ys:
            row = []
            for xval in xs:
                p = template.copy()
                for var, val in ((xv, xval), (yv, yval)):
                    if var == "age":
                        p.age = val
                    else:
                        p.obs[var] = ObsValue(value=val, prior=p.obs.get(var, ObsValue()).prior, gap_hours=p.obs.get(var, ObsValue()).gap_hours)
                if not self.plausible(p):
                    row.append(-1)
                    continue
                fa, fb = ia.fires(p, rule), ib.fires(p, rule_b)
                row.append(1 if fa and fb else 2 if fa else 3 if fb else 0)
            cells.append(row)
        counts = {k: sum(1 for r in cells for c in r if c == k) for k in (-1, 0, 1, 2, 3)}
        return {"x": {"var": xv, "values": [float(v) for v in xs]}, "y": {"var": yv, "values": [float(v) for v in ys]}, "cells": cells, "counts": counts}
