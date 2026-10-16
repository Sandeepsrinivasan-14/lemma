"""Differential test against the official HL7 CQL translator/engine. Skipped when Node deps are not installed."""
import pytest

from lemma.analyses.official import available, compare_with_official
from lemma.cohort import generate_cohort

pytestmark = pytest.mark.skipif(not available(), reason="run `npm install` in tools/official (needs Node.js)")


@pytest.mark.parametrize("name", ["bp_initiation_v2", "metformin", "statin", "nsaid_analgesia"])
def test_matches_official_engine(schema, lib, name):
    r = compare_with_official(schema, lib(name, schema), generate_cohort(schema, 80, 21))
    assert r["translated"], r.get("translator_errors")
    assert r["mismatches"] == []


def test_float_boundary_is_the_only_known_divergence(schema, lib):
    """cql-execution uses IEEE floats for Decimal: 1.2 - 0.9 < 0.3 there, exactly 0.3 in Lemma."""
    r = compare_with_official(schema, lib("aki_alert", schema), generate_cohort(schema, 80, 21))
    assert r["translated"]
    for m in r["mismatches"]:
        o = m["patient"]["observations"]["creatinine"]
        assert m["lemma"] == "true" and m["official"] == "false"
        assert abs(float(o["value"]) - float(o["prior"]) - 0.3) < 1e-9 or float(o["value"]) / float(o["prior"]) - 1.5 < 1e-9
