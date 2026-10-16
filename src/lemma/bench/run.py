"""LemmaBench runner: the experiments that decide whether Lemma works.

E1  encoding vs interpreter agreement on gold rules AND every mutant
E2  seeded-bug detection: Lemma (exact) vs boundary-value tests vs fuzzing
E3  a real guideline-style revision (BP v1 -> v2): diff, witnesses, cohort impact
E7  threshold robustness of each gold rule on a synthetic cohort
E8  equity diff for the BP revision on a synthetic cohort

All cohorts here are SYNTHETIC. Results about prevalence, impact or equity describe the generator,
not patients; they exist to exercise and demonstrate the analyses.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from ..analyses.common import load_library
from ..analyses.crosscheck import crosscheck
from ..analyses.diff import semantic_diff
from ..analyses.impact import cohort_impact
from ..analyses.robustness import robustness_summary
from ..analyses.testgen import generate_tests, run_tests
from ..cohort import generate_cohort, random_patients
from ..interp import Interpreter
from ..schema import Schema, load_schema
from .mutants import generate_mutants

GOLD = [
    ("bp_initiation_v1", "Start antihypertensive"),
    ("bp_initiation_v2", "Start antihypertensive"),
    ("metformin", "Recommend metformin"),
    ("aki_alert", "AKI alert"),
    ("statin", "Recommend statin"),
    ("nsaid_analgesia", "Recommend NSAID"),
]


def _detects(schema: Schema, gold, mut, rule: str, patients) -> bool:
    ig, im = Interpreter(gold, schema), Interpreter(mut, schema)
    return any(ig.fires(p, rule) != im.fires(p, rule) for p in patients)


def run_e1_e2(schema: Schema, rules_dir: Path, log=print) -> dict:
    cohort_small = generate_cohort(schema, 200, seed=101)
    uniform = random_patients(schema, 200, seed=202)
    cohort_big = generate_cohort(schema, 2000, seed=303)
    xcheck_pts = generate_cohort(schema, 150, seed=404)
    methods = ["boundary tests (from gold)", "fuzz: 200 uniform patients", "fuzz: 200 cohort patients", "fuzz: 2000 cohort patients"]
    rows, e1 = [], {"libraries": 0, "patients_checked": 0, "evaluations": 0, "mismatches": 0}
    t_all = time.time()
    for name, rule in GOLD:
        text = (rules_dir / f"{name}.cql").read_text()
        gold = load_library(text, schema, is_text=True)
        tests = generate_tests(schema, gold, rule)
        mutants = generate_mutants(schema, text)
        log(f"[{name}] {len(mutants)} mutants, {len(tests)} boundary tests")
        for lib in [gold] + [m.lib for m in mutants]:
            r = crosscheck(schema, lib, xcheck_pts)
            e1["libraries"] += 1
            e1["patients_checked"] += r["patients_checked"]
            e1["evaluations"] += r["evaluations"]
            e1["mismatches"] += len(r["mismatches"])
        for m in mutants:
            t0 = time.time()
            d = semantic_diff(schema, gold, m.lib, rule)
            lemma_s = time.time() - t0
            row = {
                "gold": name,
                "id": m.id,
                "operator": m.operator,
                "description": m.description,
                "lemma": d.status,
                "lemma_seconds": round(lemma_s, 3),
                "detected_by": {},
            }
            row["detected_by"][methods[0]] = bool(run_tests(schema, m.lib, rule, tests))
            row["detected_by"][methods[1]] = _detects(schema, gold, m.lib, rule, uniform)
            row["detected_by"][methods[2]] = _detects(schema, gold, m.lib, rule, cohort_small)
            row["detected_by"][methods[3]] = _detects(schema, gold, m.lib, rule, cohort_big)
            rows.append(row)
    differing = [r for r in rows if r["lemma"] == "different"]
    equivalent = [r for r in rows if r["lemma"] == "equivalent"]
    unknown = [r for r in rows if r["lemma"] == "unknown"]
    summary = {
        "mutants": len(rows),
        "non_equivalent": len(differing),
        "proved_equivalent": len(equivalent),
        "unknown": len(unknown),
        "detection": {"Lemma (exact)": len(differing)},
        "detection_rate": {"Lemma (exact)": 1.0 if differing else None},
        "false_alarms_on_equivalent": 0,
    }
    for m in methods:
        k = sum(1 for r in differing if r["detected_by"][m])
        summary["detection"][m] = k
        summary["detection_rate"][m] = round(k / len(differing), 4) if differing else None
        summary["false_alarms_on_equivalent"] = 0
    union = sum(1 for r in differing if any(r["detected_by"].values()))
    summary["detection"]["any baseline"] = union
    summary["detection_rate"]["any baseline"] = round(union / len(differing), 4) if differing else None
    by_op: dict = defaultdict(lambda: {"non_equivalent": 0, "equivalent": 0, **{m: 0 for m in methods}})
    for r in rows:
        b = by_op[r["operator"]]
        if r["lemma"] == "equivalent":
            b["equivalent"] += 1
        elif r["lemma"] == "different":
            b["non_equivalent"] += 1
            for m in methods:
                b[m] += int(r["detected_by"][m])
    secs = [r["lemma_seconds"] for r in rows]
    summary["lemma_seconds"] = {"mean": round(sum(secs) / len(secs), 3), "max": round(max(secs), 3), "total": round(sum(secs), 1)}
    return {"e1": {**e1, "note": "SMT encoding vs the independent concrete interpreter on gold rules and mutants. Not a comparison with the official HL7 CQL engine."}, "e2": {"summary": summary, "by_operator": by_op, "methods": methods, "rows": rows, "wall_seconds": round(time.time() - t_all, 1)}}


def run_e3_e8(schema: Schema, rules_dir: Path, log=print) -> dict:
    v1 = load_library(rules_dir / "bp_initiation_v1.cql", schema)
    v2 = load_library(rules_dir / "bp_initiation_v2.cql", schema)
    rule = "Start antihypertensive"
    d = semantic_diff(schema, v1, v2, rule, summary=True)
    cohort = generate_cohort(schema, 5000, seed=2026)
    imp = cohort_impact(schema, v1, v2, rule, cohort)
    tests = generate_tests(schema, v1, rule)
    fail = run_tests(schema, v2, rule, tests)
    log("[E3/E8] BP v1 -> v2 done")
    return {
        "e3": {
            "diff": d.to_dict(),
            "boundary_tests_from_v1": len(tests),
            "boundary_tests_failing_on_v2": len(fail),
            "cohort": {"n": imp["n"], "synthetic": True},
            "impact": {k: imp[k] for k in ("a_fires", "b_fires", "gained", "lost", "changed")},
        },
        "e8": {"synthetic": True, "groups": imp["groups"], "notes": imp["notes"]},
    }


def run_e7(schema: Schema, rules_dir: Path, log=print, limit: int = 150) -> dict:
    cohort = generate_cohort(schema, 1000, seed=2027)
    out = {}
    for name, rule in GOLD:
        lib = load_library(rules_dir / f"{name}.cql", schema)
        t0 = time.time()
        rs = robustness_summary(schema, lib, rule, cohort, limit=limit)
        rs["seconds"] = round(time.time() - t0, 1)
        out[name] = rs
        log(f"[E7] {name}: fragile {rs['fragile']}/{rs['patients_evaluated']}")
    return {"synthetic": True, "tolerances": {c: {"unit": s.unit, "tolerance": float(s.tolerance)} for c, s in schema.obs.items()}, "rules": out}


def render_markdown(res: dict) -> str:
    e1, e2 = res["e1"], res["e2"]
    s = e2["summary"]
    L = ["# LemmaBench results", "", "> Generated by `lemma bench`. All cohorts are synthetic. Mutants are generated by the authors' operators, so rates are indicative of this benchmark, not of clinical practice.", ""]
    L += ["## E1 - encoding vs interpreter", "", f"- Libraries checked (gold + mutants): **{e1['libraries']}**", f"- Patient evaluations: **{e1['evaluations']:,}** ({e1['patients_checked']:,} patient-library pairs)", f"- Mismatches: **{e1['mismatches']}**", f"- {e1['note']}", ""]
    L += ["## E2 - seeded-bug detection", "", f"- Mutants: **{s['mutants']}** (non-equivalent **{s['non_equivalent']}**, proved equivalent by Lemma **{s['proved_equivalent']}**, unknown **{s['unknown']}**)", f"- Lemma time per diff: mean {s['lemma_seconds']['mean']} s, max {s['lemma_seconds']['max']} s", "", "| Method | Detected | Rate |", "| --- | --- | --- |"]
    for m, k in s["detection"].items():
        r = s["detection_rate"][m]
        L.append(f"| {m} | {k}/{s['non_equivalent']} | {'n/a' if r is None else f'{r:.1%}'} |")
    L += ["", "Equivalent mutants (edits that change no behaviour) are proved equivalent by Lemma; test-based methods cannot tell \"equivalent\" from \"missed\".", "", "### By mutation operator (non-equivalent mutants)", ""]
    ms = e2["methods"]
    L.append("| Operator | Non-equivalent | Equivalent | " + " | ".join(ms) + " |")
    L.append("| --- | --- | --- | " + " | ".join("---" for _ in ms) + " |")
    for op, b in sorted(e2["by_operator"].items()):
        L.append(f"| {op} | {b['non_equivalent']} | {b['equivalent']} | " + " | ".join(f"{b[m]}" for m in ms) + " |")
    if "e3" in res:
        e3 = res["e3"]
        L += ["", "## E3 - a guideline-style revision (BP v1 -> v2, synthetic cohort)", ""]
        d = e3["diff"]
        L.append(f"- Semantic diff status: **{d['status']}** (gained witness: {'yes' if d['gained'] else 'no'}, lost witness: {'yes' if d['lost'] else 'no'})")
        L.append(f"- Boundary tests generated from v1: {e3['boundary_tests_from_v1']}; failing on v2: **{e3['boundary_tests_failing_on_v2']}**")
        im = e3["impact"]
        L.append(f"- Synthetic cohort (n={e3['cohort']['n']}): fires under v1 {im['a_fires']['rate']:.1%}, under v2 {im['b_fires']['rate']:.1%}; gained {im['gained']['rate']:.1%}, lost {im['lost']['rate']:.1%}")
    if "e7" in res:
        L += ["", "## E7 - threshold robustness (synthetic cohort)", "", "| Rule library | Patients | Fragile (flips within tolerance) | Share |", "| --- | --- | --- | --- |"]
        for name, r in res["e7"]["rules"].items():
            L.append(f"| {name} | {r['patients_evaluated']} | {r['fragile']} | {r['fragile_share']:.1%} |")
    if "e8" in res:
        L += ["", "## E8 - equity diff (BP v1 -> v2, synthetic cohort)", "", "Descriptive only; synthetic data.", ""]
        for gname, entries in res["e8"]["groups"].items():
            L += [f"**{gname}**", "", "| Group | n | Changed | Rate (95% CI) |", "| --- | --- | --- | --- |"]
            for g in entries:
                c = g["changed"]
                L.append(f"| {g['group']} | {g['n']} | {g['gained'] + g['lost']} | {c['rate']:.1%} ({c['ci_low']:.1%} to {c['ci_high']:.1%}) |")
            L.append("")
    return "\n".join(L) + "\n"


def run_all(schema_path: str | Path, rules_dir: str | Path, out_dir: str | Path, log=print) -> dict:
    schema = load_schema(schema_path)
    rules_dir, out_dir = Path(rules_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = run_e1_e2(schema, rules_dir, log)
    res.update(run_e3_e8(schema, rules_dir, log))
    res["e7"] = run_e7(schema, rules_dir, log)
    (out_dir / "lemmabench.json").write_text(json.dumps(res, indent=1, default=str))
    (out_dir / "RESULTS.md").write_text(render_markdown(res))
    return res
