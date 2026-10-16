"""Where bundled examples live (repo checkout first, then an optional LEMMA_HOME override)."""
from __future__ import annotations

import os
from pathlib import Path


def examples_dir() -> Path:
    env = os.environ.get("LEMMA_HOME")
    if env and (Path(env) / "examples").is_dir():
        return Path(env) / "examples"
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "examples" / "schema.toml").exists():
            return parent / "examples"
    return Path.cwd() / "examples"
