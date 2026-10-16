"""The SMT encoding must agree with the independent interpreter."""
from fractions import Fraction

from hypothesis import HealthCheck, given, settings, strategies as st

from lemma.analyses.crosscheck import crosscheck
from lemma.cohort import generate_cohort, random_patients
from lemma.interp import Interpreter
from lemma.patient import ObsValue, Patient
from lemma.analyses.crosscheck import compare_on_patient
from lemma.smt import build_problem


def test_kleene_missing_data(schema, lib):
    v1 = lib("bp_initiation_v1", schema)
    ev = Interpreter(v1, schema)
    p = Patient(age=Fraction(70), sex="female", obs={"sbp": ObsValue(value=Fraction(155))})
    # age>=60 w/o comorbidity and sbp>=150 -> true regardless of the missing dbp
    assert ev.fires(p, "Start antihypertensive") is True
    q = Patient(age=Fraction(40), sex="female", obs={"sbp": ObsValue(value=Fraction(135))})
    assert ev.fires(q, "Start antihypertensive") is False or ev.evaluate(q, "Start antihypertensive") is None


def test_encoding_matches_interpreter_on_cohorts(schema, lib):
    for name in ("bp_initiation_v1", "bp_initiation_v2", "metformin", "aki_alert", "statin", "nsaid_analgesia"):
        r = crosscheck(schema, lib(name, schema), generate_cohort(schema, 60, 5) + random_patients(schema, 60, 6))
        assert r["mismatches"] == [], (name, r["mismatches"][:2])


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_random_patients_agree(schema, lib, data):
    library = lib("aki_alert", schema)
    problem = build_problem(schema, {"lib": library})
    spec = schema.obs["creatinine"]
    k = data.draw(st.integers(int(spec.k_lo), int(spec.k_hi)))
    kp = data.draw(st.integers(1, int(spec.k_hi)))
    gap = data.draw(st.integers(1, int(spec.max_gap_hours)))
    present = data.draw(st.booleans())
    obs = {"creatinine": ObsValue(value=spec.step * k, prior=spec.step * kp, gap_hours=Fraction(gap))} if present else {}
    p = Patient(age=Fraction(data.draw(st.integers(18, 100))), sex="female", obs=obs)
    assert compare_on_patient(problem, library, p) == []
