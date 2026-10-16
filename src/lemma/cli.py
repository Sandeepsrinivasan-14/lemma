"""Command line interface.

Exit codes (so `lemma` can gate CI):  0 ok / no difference / all invariants hold,
1 difference found / invariant violated / test failures,  2 usage or input error,  3 solver gave up (unknown).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyses.common import load_library
from .analyses.crosscheck import crosscheck
from .analyses.diff import semantic_diff
from .analyses.impact import cohort_impact
from .analyses.invariants import check_invariants, load_invariants
from .analyses.missing import missing_data_analysis
from .analyses.robustness import robustness_summary
from .analyses.testgen import generate_tests, run_tests
from .cohort import generate_cohort, read_cohort_csv, write_cohort_csv
from .fhir import patient_bundle, structural_check
from .lang.check import LemmaCheckError, check_library
from .lang.lexer import LemmaSyntaxError
from .lang.parser import parse_library
from .numeric import fmt
from .paths import examples_dir
from .schema import Schema, load_schema


def _schema(args) -> Schema:
    return load_schema(args.schema or examples_dir() / "schema.toml")


def _patient_lines(schema: Schema, p, indent: str = "    ") -> list[str]:
    lines = [f"{indent}age {fmt(p.age)}, sex {p.sex}"]
    for code, o in p.obs.items():
        unit = schema.obs[code].unit
        bits = []
        if o.value is not None:
            bits.append(f"{fmt(o.value)} {unit}")
        else:
            bits.append("(no current value)")
        if o.prior is not None:
            bits.append(f"previous {fmt(o.prior)} {unit} {fmt(o.gap_hours)} h earlier")
        lines.append(f"{indent}{code}: " + ", ".join(bits))
    missing = [c for c in schema.obs if c not in p.obs]
    if missing:
        lines.append(f"{indent}not recorded: {', '.join(missing)}")
    if p.conditions:
        lines.append(f"{indent}conditions: {', '.join(sorted(p.conditions))}")
    if p.medications:
        lines.append(f"{indent}medications: {', '.join(sorted(p.medications))}")
    return lines


def _emit(args, obj) -> bool:
    if getattr(args, "json", False):
        print(json.dumps(obj, indent=1, default=str))
        return True
    return False


def cmd_check(args) -> int:
    schema, rc = _schema(args), 0
    for path in args.files:
        try:
            lib = parse_library(Path(path).read_text())
        except LemmaSyntaxError as e:
            print(f"{path}:{e.line}:{e.col}: error: {e.msg}")
            rc = 2
            continue
        diags = check_library(lib, schema)
        for d in diags:
            print(f"{path}:{d}")
        if any(d.severity == "error" for d in diags):
            rc = 2
        else:
            rules = [n for n, d in lib.defines.items() if d.kind == "bool"]
            print(f"{path}: ok ({len(lib.defines)} definitions; boolean rules: {', '.join(rules)})")
    return rc


def cmd_diff(args) -> int:
    schema = _schema(args)
    a, b = load_library(args.old, schema), load_library(args.new, schema)
    res = semantic_diff(schema, a, b, args.rule, args.rule_new, mode=args.mode, summary=args.zones)
    if _emit(args, res.to_dict()):
        return {"equivalent": 0, "different": 1}.get(res.status, 3)
    print(f'Rule "{args.rule}": {args.old} -> {args.new}   [{res.mode} mode, {res.seconds:.2f}s]')
    for c in res.syntactic:
        print(f"  text: {c['change']} \"{c['define']}\"" + (f"\n        was: {c['before']}" if c.get("before") else "") + (f"\n        now: {c['after']}" if c.get("after") else ""))
    if res.status == "equivalent":
        print("  EQUIVALENT: no patient in the encoded domain is treated differently.")
    for w in (res.gained, res.lost, *res.transitions.values()):
        if not w:
            continue
        label = {"gained": "GAINED (fires only under the new version)", "lost": "LOST (fires only under the old version)"}.get(w.direction, w.direction)
        print(f"  {label}: old={w.a}, new={w.b}; smallest witness found:")
        print("\n".join(_patient_lines(schema, w.patient)))
        if w.summary:
            print(f"    zone: {json.dumps(w.summary.get('ranges', w.summary), default=str)}")
        if args.fhir:
            bundle = patient_bundle(schema, w.patient, bundle_id=f"lemma-{w.direction}")
            out = Path(args.fhir) / f"{w.direction}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(bundle, indent=1))
            print(f"    FHIR bundle -> {out} (structural problems: {structural_check(bundle) or 'none'})")
    if res.unknown:
        print(f"  UNKNOWN: solver timed out for {', '.join(res.unknown)}; the result above is incomplete.")
    print(f"  scope: {res.note}")
    return {"equivalent": 0, "different": 1}.get(res.status, 3)


def cmd_verify(args) -> int:
    schema = _schema(args)
    lib = load_library(args.rules, schema)
    invs = load_invariants(args.invariants)
    skipped = [i for i in invs if i.rule not in lib.defines]
    invs = [i for i in invs if i.rule in lib.defines]
    if skipped:
        print(f"({len(skipped)} invariant(s) skipped: their rules are not defined in {args.rules})")
    results = check_invariants(schema, lib, invs, inv_source=Path(args.invariants).read_text())
    if _emit(args, [r.to_dict() for r in results]):
        pass
    else:
        for r in results:
            mark = {"holds": "PASS", "violated": "FAIL", "unknown": "????", "error": "ERR "}[r.status]
            print(f"[{mark}] {r.name}: {r.statement}")
            if r.status == "violated" and r.counterexample:
                print("       counterexample:")
                print("\n".join(_patient_lines(schema, r.counterexample, "         ")))
            if r.vacuous:
                print(f"       note: holds vacuously ({r.vacuous})")
            if r.message:
                print(f"       {r.message}")
    if any(r.status == "violated" for r in results):
        return 1
    return 3 if any(r.status in ("unknown", "error") for r in results) else 0


def cmd_missing(args) -> int:
    schema = _schema(args)
    rep = missing_data_analysis(schema, load_library(args.rules, schema), args.rule)
    d = rep.to_dict()
    if _emit(args, d):
        return 0
    print(f'Missing-data analysis for "{args.rule}"')
    for f in d["findings"]:
        pt = f["patient"]
        obs = ", ".join(f"{c}={o.get('value')}" + (f" (previous {o['prior']})" if o.get("prior") is not None else "") for c, o in pt["observations"].items())
        print(f"  - missing {f['observation']} {f['effect']} the rule; e.g. age {pt['age']} {pt['sex']}: {obs or 'nothing recorded'}")
    if not d["findings"]:
        print("  no single missing observation silences or triggers this rule")
    return 0


def cmd_impact(args) -> int:
    schema = _schema(args)
    a, b = load_library(args.old, schema), load_library(args.new, schema)
    cohort = read_cohort_csv(schema, args.cohort) if args.cohort else generate_cohort(schema, args.n, args.seed)
    res = cohort_impact(schema, a, b, args.rule, cohort, args.rule_new)
    if _emit(args, res):
        return 0
    tag = "cohort from CSV" if args.cohort else f"SYNTHETIC cohort (n={len(cohort)}, seed={args.seed})"
    print(f'Impact of {args.old} -> {args.new} on "{args.rule}", {tag}')
    for k in ("a_fires", "b_fires", "gained", "lost", "changed"):
        r = res[k]
        print(f"  {k:8s} {r['k']:>6d} / {r['n']}  {r['rate']:.1%}  (95% CI {r['ci_low']:.1%} to {r['ci_high']:.1%})")
    for gname, entries in res["groups"].items():
        print(f"  by {gname}:")
        for g in entries:
            c = g["changed"]
            print(f"    {g['group']:<18s} n={g['n']:<6d} changed {c['rate']:.1%} ({c['ci_low']:.1%} to {c['ci_high']:.1%})")
    for n in res.get("notes", []):
        print(f"  note: {n}")
    return 0


def cmd_robustness(args) -> int:
    schema = _schema(args)
    lib = load_library(args.rules, schema)
    cohort = read_cohort_csv(schema, args.cohort) if args.cohort else generate_cohort(schema, 500, args.seed)
    res = robustness_summary(schema, lib, args.rule, cohort, limit=args.limit)
    if _emit(args, res):
        return 0
    print(f'Threshold robustness of "{args.rule}" ({res["patients_evaluated"]} patients; tolerance = clinical measurement noise from the schema)')
    print(f"  fragile (decision flips within tolerance): {res['fragile']} ({res['fragile_share']:.1%})")
    return 0


def cmd_tests(args) -> int:
    schema = _schema(args)
    lib = load_library(args.rules, schema)
    tests = generate_tests(schema, lib, args.rule)
    if args.against:
        other = load_library(args.against, schema)
        fails = run_tests(schema, other, args.rule, tests)
        print(f"{len(tests)} boundary tests from {args.rules}; {len(fails)} fail on {args.against}")
        for f in fails:
            print(f"  FAIL {f.get('name', '')}: expected {f.get('expected')}, got {f.get('actual')}")
        return 1 if fails else 0
    if args.out:
        Path(args.out).write_text(json.dumps(tests, indent=1, default=str))
        print(f"{len(tests)} tests -> {args.out}")
    else:
        print(json.dumps(tests, indent=1, default=str))
    return 0


def cmd_cohort(args) -> int:
    schema = _schema(args)
    write_cohort_csv(schema, generate_cohort(schema, args.n, args.seed), args.out)
    print(f"wrote {args.n} SYNTHETIC patients to {args.out}")
    return 0


def cmd_crosscheck(args) -> int:
    schema = _schema(args)
    lib = load_library(args.rules, schema)
    res = crosscheck(schema, lib, generate_cohort(schema, args.n, args.seed))
    print(f"{res['patients_checked']} patients, {res['evaluations']} evaluations, {len(res['mismatches'])} mismatches between SMT encoding and interpreter")
    for m in res["mismatches"][:10]:
        print(f"  {m}")
    return 1 if res["mismatches"] else 0


def cmd_official(args) -> int:
    from .analyses.official import OfficialUnavailable, compare_with_official

    schema = _schema(args)
    cohort = generate_cohort(schema, args.n, args.seed)
    rc = 0
    for path in args.rules:
        lib = load_library(path, schema)
        try:
            r = compare_with_official(schema, lib, cohort)
        except OfficialUnavailable as e:
            print(f"error: {e}\n(run `npm install` in tools/official; needs Node.js)", file=sys.stderr)
            return 2
        if not r["translated"]:
            print(f"{path}: the official translator rejected the exported CQL: {r['translator_errors'][:2]}")
            rc = 1
            continue
        print(f"{path}: {r['evaluations']} evaluations vs {r['translator']} + {r['engine']}: {len(r['mismatches'])} disagreement(s)")
        for m in r["mismatches"][:5]:
            print(f"  {m['define']}: lemma={m['lemma']} official={m['official']}  patient={json.dumps(m['patient']['observations'])}")
        rc = rc or (1 if r["mismatches"] else 0)
    return rc


def cmd_bench(args) -> int:
    from .bench.run import run_all

    ex = examples_dir()
    run_all(args.schema or ex / "schema.toml", args.rules_dir or ex / "rules", args.out)
    print(f"results written to {args.out}/RESULTS.md and lemmabench.json")
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    print(f"Lemma UI on http://{args.host}:{args.port}  (no authentication; synthetic data only)")
    serve(args.host, args.port, args.schema)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lemma", description="Verify and diff clinical decision logic.")
    p.add_argument("--schema", help="schema TOML (default: examples/schema.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("check", cmd_check, "parse and type-check rule files")
    sp.add_argument("files", nargs="+")

    sp = add("diff", cmd_diff, "semantic diff of two versions of a rule")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.add_argument("--rule", required=True)
    sp.add_argument("--rule-new", help="rule name in the new file, if renamed")
    sp.add_argument("--mode", choices=["fires", "strict"], default="fires")
    sp.add_argument("--zones", action="store_true", help="summarise the changed region per variable")
    sp.add_argument("--fhir", metavar="DIR", help="write witness patients as FHIR bundles")
    sp.add_argument("--json", action="store_true")

    sp = add("verify", cmd_verify, "check safety invariants")
    sp.add_argument("rules")
    sp.add_argument("invariants")
    sp.add_argument("--json", action="store_true")

    sp = add("missing", cmd_missing, "what missing data silences or triggers a rule")
    sp.add_argument("rules")
    sp.add_argument("--rule", required=True)
    sp.add_argument("--json", action="store_true")

    sp = add("impact", cmd_impact, "cohort impact and equity of a change")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.add_argument("--rule", required=True)
    sp.add_argument("--rule-new")
    sp.add_argument("--cohort", help="cohort CSV (default: synthetic)")
    sp.add_argument("-n", type=int, default=5000)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--json", action="store_true")

    sp = add("robustness", cmd_robustness, "which patients flip within measurement tolerance")
    sp.add_argument("rules")
    sp.add_argument("--rule", required=True)
    sp.add_argument("--cohort")
    sp.add_argument("--limit", type=int, default=150)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--json", action="store_true")

    sp = add("tests", cmd_tests, "generate boundary tests, or run them against another version")
    sp.add_argument("rules")
    sp.add_argument("--rule", required=True)
    sp.add_argument("--against", help="run the generated tests against this other version")
    sp.add_argument("--out")

    sp = add("cohort", cmd_cohort, "write a synthetic cohort CSV")
    sp.add_argument("out")
    sp.add_argument("-n", type=int, default=1000)
    sp.add_argument("--seed", type=int, default=7)

    sp = add("crosscheck", cmd_crosscheck, "SMT encoding vs interpreter on a cohort")
    sp.add_argument("rules")
    sp.add_argument("-n", type=int, default=200)
    sp.add_argument("--seed", type=int, default=7)

    sp = add("official", cmd_official, "compare against the official HL7 CQL translator and engine (needs Node)")
    sp.add_argument("rules", nargs="+")
    sp.add_argument("-n", type=int, default=200)
    sp.add_argument("--seed", type=int, default=7)

    sp = add("bench", cmd_bench, "run LemmaBench (slow)")
    sp.add_argument("--rules-dir")
    sp.add_argument("--out", default="results")

    sp = add("serve", cmd_serve, "start the local web UI")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (LemmaSyntaxError, LemmaCheckError, KeyError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
