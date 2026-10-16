"""Differential test against the official HL7 CQL toolchain (E10).

Lemma rules are exported to real CQL (cqlexport), compiled by the official translator
(@cqframework/cql) and run by the cql-execution engine, on the same patients as Lemma's
interpreter. Every definition's three-valued result is compared.
Needs Node.js and `npm install` in tools/official (npm registry only; no Java).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..cqlexport import export_cql, patient_params
from ..interp import Evaluation
from ..lang import ast as A
from ..patient import Patient
from ..schema import Schema
from .testgen import boundary_patients

TOOL_DIR = Path(__file__).resolve().parents[3] / "tools" / "official"


class OfficialUnavailable(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("node") is not None and (TOOL_DIR / "node_modules" / "@cqframework").exists()


def _label(v) -> str:
    return "null" if v is None else ("true" if v is True else "false" if v is False else str(v))


def run_official(cql: str, rules: list[str], cases: list[dict]) -> dict:
    if not available():
        raise OfficialUnavailable("Node.js and `npm install` in tools/official are required")
    with tempfile.TemporaryDirectory() as d:
        job = Path(d) / "job.json"
        job.write_text(json.dumps({"cql": cql, "rules": rules, "cases": cases}))
        proc = subprocess.run(["node", "run.mjs", str(job)], cwd=TOOL_DIR, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise OfficialUnavailable(proc.stderr[-800:])
    return json.loads(proc.stdout)


def compare_with_official(schema: Schema, lib: A.Library, patients: list[Patient], include_boundaries: bool = True) -> dict:
    cql, params = export_cql(lib)
    pts = list(patients)
    if include_boundaries:
        pts += [p for _, p in boundary_patients(schema, lib)]
    rules = list(lib.defines)
    out = run_official(cql, rules, [patient_params(p, params) for p in pts])
    if out["translator_errors"]:
        return {"translated": False, "translator_errors": out["translator_errors"], "cql": cql}
    mism = []
    for p, res in zip(pts, out["results"]):
        ev = Evaluation(lib, p)
        for r in rules:
            mine, theirs = ev.define(r), res[r]
            if isinstance(mine, bool) or mine is None:
                if _label(mine) != _label(theirs):
                    mism.append({"patient": p.to_dict(), "define": r, "lemma": _label(mine), "official": _label(theirs)})
    return {"translated": True, "patients": len(pts), "evaluations": len(pts) * len(rules), "mismatches": mism, "engine": out.get("engine"), "translator": out.get("translator_version")}
