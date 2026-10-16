from pathlib import Path

import pytest

from lemma.analyses.common import load_library
from lemma.schema import load_schema

EX = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(scope="session")
def schema():
    return load_schema(EX / "schema.toml")


@pytest.fixture(scope="session")
def lib():
    def _load(name, schema_):
        return load_library(EX / "rules" / f"{name}.cql", schema_)
    return _load
