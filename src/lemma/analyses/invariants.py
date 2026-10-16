"""Safety invariants (M4): prove a rule library respects a statement, or refute it with a patient."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import z3

from ..interp import Interpreter
from ..lang import ast as A
from ..lang import pretty
from ..lang.check import Checker, Diagnostic, LemmaCheckError, errors
from ..lang.parser import parse_invariants
from ..patient import Patient
from ..schema import Schema
from ..smt import build_problem
from .common import BOUNDS_NOTE, DEFAULT_TIMEOUT_MS, EncodingMismatch, find_patient, is_satisfiable


@dataclass
class InvariantResult:
    name: str
    rule: str
    statement: str
    status: str  # "holds" | "violated" | "unknown" | "error"
    source: Optional[str] = None
    counterexample: Optional[Patient] = None
    vacuous: Optional[str] = None  # why a "holds" might be empty: "rule never fires" / "condition is impossible"
    message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rule": self.rule,
            "statement": self.statement,
            "status": self.status,
            "source": self.source,
            "counterexample": self.counterexample.to_dict() if self.counterexample else None,
            "vacuous": self.vacuous,
            "message": self.message,
            "scope": BOUNDS_NOTE,
        }


def load_invariants(source: str | Path, *, is_text: bool = False) -> list[A.Invariant]:
    return parse_invariants(source if is_text else Path(source).read_text())


def statement(inv: A.Invariant) -> str:
    cond = pretty.expr(inv.cond)
    if inv.mode == "never":
        return f'"{inv.rule}" must never fire when {cond}'
    return f'whenever "{inv.rule}" fires, {cond} must be true'


def check_invariants(schema: Schema, lib: A.Library, invariants: list[A.Invariant], *, inv_source: str = "", timeout_ms: int = DEFAULT_TIMEOUT_MS) -> list[InvariantResult]:
    problem = build_problem(schema, {"lib": lib})
    enc = problem.encoder("lib")
    interp = Interpreter(lib, schema)
    results: list[InvariantResult] = []
    for inv in invariants:
        res = InvariantResult(inv.name, inv.rule, statement(inv), "error", inv.source)
        if inv.rule not in lib.defines:
            res.message = f'rule "{inv.rule}" is not defined in library {lib.name}'
            results.append(res)
            continue
        ck = Checker(lib, schema)
        ck.check_extra(inv.cond, inv_source)
        errs = errors(ck.diags)
        if errs:
            res.message = "; ".join(str(e) for e in errs)
            results.append(res)
            continue
        if inv.cond.kind != "bool":
            res.message = "the invariant condition must be a boolean expression"
            results.append(res)
            continue
        fires = enc.fires(inv.rule)
        cond = enc.ex(inv.cond)
        bad = z3.And(fires, cond.t) if inv.mode == "never" else z3.And(fires, z3.Not(cond.t))
        r = find_patient(problem, [bad], timeout_ms=timeout_ms, pid=f"counterexample-{inv.name}")
        if r.status == "sat":
            # replay through the independent interpreter before reporting
            p = r.patient
            ok_fire = interp.evaluate(p, inv.rule) is True
            from ..interp import Evaluation

            cv = Evaluation(lib, p).ex(inv.cond)
            ok = ok_fire and ((cv is True) if inv.mode == "never" else (cv is not True))
            if not ok:
                raise EncodingMismatch(f"counterexample for {inv.name!r} did not replay: {p.to_dict()}")
            res.status, res.counterexample = "violated", p
        elif r.status == "unknown":
            res.status = "unknown"
            res.message = "solver timed out"
        else:
            res.status = "holds"
            if is_satisfiable(problem, [fires], timeout_ms) == "unsat":
                res.vacuous = "the rule can never fire, so the invariant holds trivially"
            elif inv.mode == "never" and is_satisfiable(problem, [cond.t], timeout_ms) == "unsat":
                res.vacuous = "the condition can never be true for a plausible patient, so the invariant holds trivially"
        results.append(res)
    return results
