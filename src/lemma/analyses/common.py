"""Shared helpers for analyses: loading libraries, solving, witnesses."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import z3

from ..interp import Interpreter
from ..lang import ast as A
from ..lang.check import LemmaCheckError, check_library, errors
from ..lang.parser import parse_library
from ..patient import Patient
from ..schema import Schema
from ..smt import Problem

DEFAULT_TIMEOUT_MS = 20000

BOUNDS_NOTE = (
    "Proofs hold for patients on the schema's measurement grid inside its plausible ranges, "
    "with at most one prior value per observation that a trend operator uses."
)


class EncodingMismatch(AssertionError):
    """The SMT model disagreed with the concrete interpreter: a bug in Lemma, never a result."""


def load_library(source: str | Path, schema: Schema, *, is_text: bool = False) -> A.Library:
    text = source if is_text else Path(source).read_text()
    lib = parse_library(text)
    diags = check_library(lib, schema)
    if errors(diags):
        raise LemmaCheckError(diags)
    return lib


@dataclass
class SolveResult:
    status: str  # "sat" | "unsat" | "unknown"
    patient: Optional[Patient] = None


def find_patient(problem: Problem, assertions: list, *, minimize: bool = True, timeout_ms: int = DEFAULT_TIMEOUT_MS, pid: str = "") -> SolveResult:
    """Find a patient satisfying the assertions; prefer the smallest, most ordinary-looking one."""
    sym = problem.sym
    s = z3.Solver()
    s.set(timeout=timeout_ms)
    s.add(problem.domain)
    s.add(assertions)
    r = s.check()
    if r == z3.unsat:
        return SolveResult("unsat")
    if r != z3.sat:
        return SolveResult("unknown")
    model = s.model()
    if minimize:
        o = z3.Optimize()
        o.set(timeout=timeout_ms)
        o.add(problem.domain)
        o.add(assertions)
        o.minimize(sym.complexity())
        o.minimize(sym.deviation())
        if o.check() == z3.sat:
            model = o.model()
    return SolveResult("sat", sym.to_patient(model, pid))


def is_satisfiable(problem: Problem, assertions: list, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    s = z3.Solver()
    s.set(timeout=timeout_ms)
    s.add(problem.domain)
    s.add(assertions)
    r = s.check()
    return "sat" if r == z3.sat else ("unsat" if r == z3.unsat else "unknown")


def interp_for(lib: A.Library, schema: Schema) -> Interpreter:
    return Interpreter(lib, schema)
