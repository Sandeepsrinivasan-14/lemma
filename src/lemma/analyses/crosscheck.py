"""Semantics cross-check (E1): the SMT encoding must agree with the concrete interpreter.

Two independent implementations of one semantics are run on the same patients (a synthetic cohort plus
boundary-hugging patients); any disagreement is a bug in Lemma. This does NOT compare against the official
HL7 CQL engine - see the README for that gap.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Optional

import z3

from ..interp import Evaluation
from ..lang import ast as A
from ..patient import Patient
from ..schema import Schema
from ..smt import GridError, Numv, Tri, build_problem
from .testgen import boundary_patients


def _frac(expr) -> Fraction:
    e = z3.simplify(expr)
    if z3.is_int_value(e):
        return Fraction(e.as_long())
    return Fraction(e.numerator_as_long(), e.denominator_as_long())


def compare_on_patient(problem, lib: A.Library, p: Patient) -> list[str]:
    """Mismatch descriptions for one patient across every define in the library (empty = agree)."""
    enc = problem.encoder("lib")
    subst = problem.sym.substitution(p)
    ev = Evaluation(lib, p)
    bad = []
    for name in lib.defines:
        want = ev.define(name)
        got = enc.define(name)
        if isinstance(got, Tri):
            t = z3.is_true(z3.simplify(z3.substitute(got.t, *subst)))
            f = z3.is_true(z3.simplify(z3.substitute(got.f, *subst)))
            val = True if t else (False if f else None)
            if val != want:
                bad.append(f'"{name}": smt={val} interp={want}')
        elif isinstance(got, Numv):
            null = z3.is_true(z3.simplify(z3.substitute(got.null, *subst)))
            if null != (want is None):
                bad.append(f'"{name}": null smt={null} interp={want}')
            elif not null and _frac(z3.substitute(got.v, *subst)) != want:
                bad.append(f'"{name}": value smt={_frac(z3.substitute(got.v, *subst))} interp={want}')
    return bad


def crosscheck(schema: Schema, lib: A.Library, patients: Iterable[Patient], include_boundaries: bool = True) -> dict:
    problem = build_problem(schema, {"lib": lib})
    pts = list(patients)
    if include_boundaries:
        pts += [p for _, p in boundary_patients(schema, lib)]
    checked, skipped, mismatches = 0, 0, []
    for p in pts:
        try:
            bad = compare_on_patient(problem, lib, p)
        except GridError:
            skipped += 1  # value not on the measurement grid: the SMT model cannot represent it
            continue
        checked += 1
        if bad:
            mismatches.append({"patient": p.to_dict(), "differences": bad})
    return {"library": lib.name, "patients_checked": checked, "defines": len(lib.defines), "evaluations": checked * len(lib.defines), "skipped_off_grid": skipped, "mismatches": mismatches}
