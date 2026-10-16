import pytest

from lemma.lang import pretty
from lemma.lang.check import check_library, errors
from lemma.lang.lexer import LemmaSyntaxError
from lemma.lang.parser import parse_library
from conftest import EX


@pytest.mark.parametrize("path", sorted((EX / "rules").glob("*.cql")), ids=lambda p: p.stem)
def test_pretty_roundtrip(path):
    lib = parse_library(path.read_text())
    again = parse_library(pretty.library(lib))
    assert pretty.library(again) == pretty.library(lib)


def _check(schema, body):
    lib = parse_library(f"library T version '1'\n{body}")
    return errors(check_library(lib, schema))


def test_unit_mismatch_is_rejected(schema):
    assert _check(schema, "define \"x\":\n  Latest(\"sbp\") >= 5 'mg/dL'")


def test_unknown_code_is_rejected(schema):
    assert _check(schema, "define \"x\":\n  Latest(\"nope\") >= 5")


def test_unit_conversion_accepted(schema):
    assert not _check(schema, "define \"x\":\n  Latest(\"creatinine\") >= 100 'umol/L'")


def test_retrieve_syntax_gives_hint():
    with pytest.raises(LemmaSyntaxError) as e:
        parse_library("library T\ndefine \"x\":\n  exists [Observation: \"sbp\"]")
    assert "Latest" in str(e.value) or "retrieve" in str(e.value).lower()
