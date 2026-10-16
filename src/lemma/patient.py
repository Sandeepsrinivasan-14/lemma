"""Concrete patient model used by the interpreter, cohorts and witnesses."""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from .numeric import fmt, to_frac


@dataclass
class ObsValue:
    """Latest value of an observation, optionally with one prior value for trend rules.

    All values are stored in the schema's canonical unit.
    """

    value: Optional[Fraction] = None
    prior: Optional[Fraction] = None
    gap_hours: Optional[Fraction] = None  # hours between prior and latest


@dataclass
class Patient:
    age: Fraction
    sex: str
    obs: dict[str, ObsValue] = field(default_factory=dict)
    conditions: set[str] = field(default_factory=set)
    medications: set[str] = field(default_factory=set)
    attrs: dict[str, str] = field(default_factory=dict)  # extra categorical columns (for equity analysis)
    id: str = ""

    def latest(self, code: str) -> Optional[Fraction]:
        o = self.obs.get(code)
        return None if o is None else o.value

    def copy(self) -> "Patient":
        return Patient(
            age=self.age,
            sex=self.sex,
            obs={k: ObsValue(v.value, v.prior, v.gap_hours) for k, v in self.obs.items()},
            conditions=set(self.conditions),
            medications=set(self.medications),
            attrs=dict(self.attrs),
            id=self.id,
        )

    # -- JSON ----------------------------------------------------------------
    def to_dict(self) -> dict:
        def num(x: Optional[Fraction]):
            if x is None:
                return None
            s = fmt(x)
            return int(s) if "." not in s and "e" not in s else float(s)

        obs = {}
        for code, o in sorted(self.obs.items()):
            if o.value is None and o.prior is None:
                continue
            entry = {"value": num(o.value)}
            if o.prior is not None:
                entry["prior"] = num(o.prior)
                entry["gap_hours"] = num(o.gap_hours)
            obs[code] = entry
        d = {
            "id": self.id,
            "age": num(self.age),
            "sex": self.sex,
            "observations": obs,
            "conditions": sorted(self.conditions),
            "medications": sorted(self.medications),
        }
        if self.attrs:
            d["attributes"] = dict(self.attrs)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Patient":
        obs: dict[str, ObsValue] = {}
        for code, e in (d.get("observations") or {}).items():
            if isinstance(e, dict):
                obs[code] = ObsValue(
                    None if e.get("value") is None else to_frac(e["value"]),
                    None if e.get("prior") is None else to_frac(e["prior"]),
                    None if e.get("gap_hours") is None else to_frac(e["gap_hours"]),
                )
            else:
                obs[code] = ObsValue(None if e is None else to_frac(e))
        return Patient(
            age=to_frac(d.get("age", 50)),
            sex=d.get("sex", "female"),
            obs=obs,
            conditions=set(d.get("conditions") or []),
            medications=set(d.get("medications") or []),
            attrs=dict(d.get("attributes") or {}),
            id=str(d.get("id", "")),
        )
