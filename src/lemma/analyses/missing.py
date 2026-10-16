"""Missing-data analysis (M7): which absent measurements silently change a decision?

CQL returns null for a missing value, and null propagates through comparisons. A rule that is
"safe" on complete data can behave very differently when one lab is missing. For each observation
the rule reads, Lemma looks for a patient whose decision flips when ONLY that observation is removed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import z3

from ..interp import Interpreter
from ..lang import ast as A
from ..patient import Patient
from ..schema import Schema
from ..smt import Encoder, SymPatient, build_problem
from .common import BOUNDS_NOTE, DEFAULT_TIMEOUT_MS, EncodingMismatch, find_patient


@dataclass
class MissingFinding:
    code: str
    effect: str  # "silences" (fires -> not fire when missing) | "triggers" (not fire -> fires when missing)
    patient: Patient  # the patient WITH the observation present; remove it to see the flip

    def to_dict(self) -> dict:
        return {"observation": self.code, "effect": self.effect, "patient": self.patient.to_dict()}


@dataclass
class MissingReport:
    rule: str
    findings: list
    checked: list
    unknown: list

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "checked": self.checked,
            "findings": [f.to_dict() for f in self.findings],
            "unknown": self.unknown,
            "scope": BOUNDS_NOTE,
            "reading": {
                "silences": "if this measurement is missing the rule stops firing (a delayed or lost recommendation)",
                "triggers": "if this measurement is missing the rule starts firing (absence of data causes a recommendation)",
            },
        }


def missing_data_analysis(schema: Schema, lib: A.Library, rule: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> MissingReport:
    problem = build_problem(schema, {"lib": lib})
    enc = problem.encoder("lib")
    fires_p = enc.fires(rule)
    interp = Interpreter(lib, schema)
    findings: list[MissingFinding] = []
    unknown: list[str] = []
    checked = sorted(lib.used_codes)
    for code in checked:
        qsym = SymPatient(schema, base=problem.sym, missing={code})
        fires_q = Encoder(lib, schema, qsym).fires(rule)
        for effect, bad in (("silences", z3.And(fires_p, z3.Not(fires_q))), ("triggers", z3.And(z3.Not(fires_p), fires_q))):
            r = find_patient(problem, [bad], timeout_ms=timeout_ms, pid=f"{effect}-{code}")
            if r.status == "unknown":
                unknown.append(f"{code}:{effect}")
            elif r.status == "sat":
                p = r.patient
                q = p.copy()
                q.obs.pop(code, None)
                a, b = interp.fires(p, rule), interp.fires(q, rule)
                if (effect == "silences" and not (a and not b)) or (effect == "triggers" and not (b and not a)):
                    raise EncodingMismatch(f"missing-data witness for {code} did not replay: {p.to_dict()}")
                findings.append(MissingFinding(code, effect, p))
    return MissingReport(rule, findings, checked, unknown)
