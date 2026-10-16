from fractions import Fraction

from lemma.analyses.diff import semantic_diff
from lemma.analyses.invariants import check_invariants, load_invariants
from lemma.analyses.missing import missing_data_analysis
from lemma.analyses.testgen import generate_tests, run_tests
from lemma.analyses.impact import cohort_impact, wilson
from lemma.cohort import generate_cohort, read_cohort_csv, write_cohort_csv
from lemma.fhir import patient_bundle, structural_check
from lemma.interp import Interpreter
from conftest import EX

RULE = "Start antihypertensive"


def test_self_diff_is_equivalent(schema, lib):
    v1 = lib("bp_initiation_v1", schema)
    assert semantic_diff(schema, v1, v1, RULE).status == "equivalent"


def test_v1_v2_gain_witness_is_real(schema, lib):
    v1, v2 = lib("bp_initiation_v1", schema), lib("bp_initiation_v2", schema)
    d = semantic_diff(schema, v1, v2, RULE)
    assert d.status == "different" and d.gained and not d.lost
    p = d.gained.patient
    assert Interpreter(v2, schema).fires(p, RULE) and not Interpreter(v1, schema).fires(p, RULE)


def test_boundary_tests_from_v1_miss_the_change(schema, lib):
    """The motivating example: a hand-written boundary suite passes while the diff proves a change."""
    v1, v2 = lib("bp_initiation_v1", schema), lib("bp_initiation_v2", schema)
    assert run_tests(schema, v2, RULE, generate_tests(schema, v1, RULE)) == []


def test_invariants_hold_and_catch_a_bug(schema, lib):
    invs = load_invariants(EX / "invariants" / "safety.lemma")
    met = lib("metformin", schema)
    ok = check_invariants(schema, met, [i for i in invs if i.rule == "Recommend metformin"])
    assert ok and all(r.status == "holds" for r in ok)
    from lemma.analyses.common import load_library
    text = (EX / "rules" / "metformin.cql").read_text()
    assert ">= 45" in text
    broken = load_library(text.replace(">= 45", ">= 25", 1), schema, is_text=True)
    res = check_invariants(schema, broken, [i for i in invs if i.rule == "Recommend metformin"])
    assert any(r.status == "violated" and r.counterexample for r in res)


def test_missing_data_finds_silencer(schema, lib):
    rep = missing_data_analysis(schema, lib("aki_alert", schema), "AKI alert")
    assert any(f.code == "creatinine" and f.effect == "silences" for f in rep.findings)


def test_fhir_bundle_structure(schema, lib):
    d = semantic_diff(schema, lib("bp_initiation_v1", schema), lib("bp_initiation_v2", schema), RULE)
    assert structural_check(patient_bundle(schema, d.gained.patient)) == []


def test_wilson_interval_sane():
    p, lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi and 0.39 < lo and hi < 0.61
    assert wilson(0, 10)[1] == 0.0


def test_cohort_csv_roundtrip(schema, tmp_path):
    pts = generate_cohort(schema, 40, 3)
    f = tmp_path / "c.csv"
    write_cohort_csv(schema, pts, f)
    back = read_cohort_csv(schema, f)
    assert [p.to_dict() for p in back] == [p.to_dict() for p in pts]


def test_impact_counts(schema, lib):
    v1, v2 = lib("bp_initiation_v1", schema), lib("bp_initiation_v2", schema)
    r = cohort_impact(schema, v1, v2, RULE, generate_cohort(schema, 400, 2))
    assert r["changed"]["k"] == r["gained"]["k"] + r["lost"]["k"]
