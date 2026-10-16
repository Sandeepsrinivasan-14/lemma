"""Data contract: which observations, flags and ranges a site (or a rule library) assumes.

The schema is a TOML file. It plays three roles in Lemma:
  * type information (units and conversions) for the rule checker,
  * the plausible domain the solver searches (so witnesses are realistic),
  * the "data contract" a site declares (precision, tolerance, optional plausibility constraints).
"""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Optional

from .numeric import to_frac


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class ObsSpec:
    code: str
    label: str
    unit: str
    lo: Fraction
    hi: Fraction
    step: Fraction
    typical: Fraction
    tolerance: Fraction  # measurement tolerance in canonical unit (0 = exact)
    conversions: dict = field(default_factory=dict)  # unit -> factor to canonical unit
    loinc: Optional[str] = None
    max_gap_hours: int = 24 * 30

    @property
    def k_lo(self) -> int:
        return math.ceil(self.lo / self.step)

    @property
    def k_hi(self) -> int:
        return math.floor(self.hi / self.step)

    def snap(self, x: Fraction) -> Fraction:
        """Round to the measurement grid and clip into the plausible range."""
        k = round(x / self.step)
        k = max(self.k_lo, min(self.k_hi, k))
        return k * self.step


@dataclass(frozen=True)
class FlagSpec:
    code: str
    label: str
    kind: str  # "condition" | "medication"
    system: Optional[str] = None
    coding: Optional[str] = None


@dataclass
class Schema:
    name: str
    obs: dict[str, ObsSpec]
    conditions: dict[str, FlagSpec]
    medications: dict[str, FlagSpec]
    age_lo: int
    age_hi: int
    age_typical: int
    sexes: list[str]
    constraints: list[tuple[str, str]]  # (name, CQL-lite boolean expression)

    def flag(self, code: str) -> Optional[FlagSpec]:
        return self.conditions.get(code) or self.medications.get(code)

    @property
    def flag_codes(self) -> list[str]:
        return list(self.conditions) + list(self.medications)


def _frac(v, where: str) -> Fraction:
    try:
        return to_frac(v)
    except Exception as e:  # noqa: BLE001
        raise SchemaError(f"{where}: not a number: {v!r}") from e


def parse_schema(data: dict) -> Schema:
    meta = data.get("meta", {})
    demo = data.get("demographics", {})
    obs: dict[str, ObsSpec] = {}
    for code, o in (data.get("observations") or {}).items():
        where = f"observations.{code}"
        for req in ("unit", "min", "max", "step", "typical"):
            if req not in o:
                raise SchemaError(f"{where}: missing '{req}'")
        lo, hi = _frac(o["min"], where), _frac(o["max"], where)
        step, typ = _frac(o["step"], where), _frac(o["typical"], where)
        if step <= 0 or lo > hi or not (lo <= typ <= hi):
            raise SchemaError(f"{where}: need step>0 and min <= typical <= max")
        conv = {u: _frac(f, f"{where}.conversions.{u}") for u, f in (o.get("conversions") or {}).items()}
        obs[code] = ObsSpec(
            code=code,
            label=o.get("label", code),
            unit=o["unit"],
            lo=lo,
            hi=hi,
            step=step,
            typical=typ,
            tolerance=_frac(o.get("tolerance", 0), where),
            conversions=conv,
            loinc=o.get("loinc"),
            max_gap_hours=int(o.get("max_gap_hours", 24 * 30)),
        )

    def flags(section: str, kind: str) -> dict[str, FlagSpec]:
        out = {}
        for code, f in (data.get(section) or {}).items():
            system = "snomed" if "snomed" in f else ("rxnorm" if "rxnorm" in f else None)
            out[code] = FlagSpec(code, f.get("label", code), kind, system, f.get(system) if system else None)
        return out

    conditions = flags("conditions", "condition")
    medications = flags("medications", "medication")
    clash = set(conditions) & set(medications)
    if clash:
        raise SchemaError(f"flag codes used as both condition and medication: {sorted(clash)}")

    constraints = [(c.get("name", f"constraint-{i + 1}"), c["rule"]) for i, c in enumerate(data.get("constraints") or [])]
    sexes = list(demo.get("sexes", ["female", "male"]))
    if not sexes:
        raise SchemaError("demographics.sexes must not be empty")
    return Schema(
        name=meta.get("name", "unnamed"),
        obs=obs,
        conditions=conditions,
        medications=medications,
        age_lo=int(demo.get("age_min", 18)),
        age_hi=int(demo.get("age_max", 100)),
        age_typical=int(demo.get("age_typical", 55)),
        sexes=sexes,
        constraints=constraints,
    )


def load_schema(path: str | Path) -> Schema:
    with open(path, "rb") as f:
        return parse_schema(tomllib.load(f))


def loads_schema(text: str) -> Schema:
    return parse_schema(tomllib.loads(text))
