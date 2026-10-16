"""Synthetic cohort generator and CSV I/O.

The generator is a stand-in so the whole toolchain runs without any real patient data.
Its distributions are plausible-looking but NOT validated against real populations; never read
epidemiology off results computed on it. The CSV reader lets you plug in a real (credentialed)
cohort instead, and keeps the same analyses.
"""
from __future__ import annotations

import csv
import math
import random
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Optional

from .numeric import fmt, to_frac
from .patient import ObsValue, Patient
from .schema import Schema

DEFAULT_MISSING = {"hba1c": 0.35, "ldl": 0.30, "egfr": 0.10, "creatinine": 0.08, "sbp": 0.03, "dbp": 0.05}


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def generate_cohort(schema: Schema, n: int = 1000, seed: int = 7, missing: Optional[dict] = None) -> list[Patient]:
    rng = random.Random(seed)
    miss = {**DEFAULT_MISSING, **(missing or {})}
    out: list[Patient] = []
    for i in range(n):
        age = int(_clip(rng.gauss(57, 16), schema.age_lo, schema.age_hi))
        sex = rng.choice(schema.sexes)
        male = 1 if sex == "male" else 0
        diabetes = rng.random() < _logistic(-4.2 + 0.035 * (age - 18))
        ckd = rng.random() < _logistic(-5.0 + 0.04 * (age - 18) + 0.8 * diabetes)
        ascvd = rng.random() < _logistic(-5.2 + 0.045 * (age - 18) + 0.5 * diabetes + 0.4 * male)
        conditions = {c for c, on in (("diabetes", diabetes), ("ckd", ckd), ("ascvd", ascvd)) if on and c in schema.conditions}
        medications = set()
        if "metformin" in schema.medications and diabetes and rng.random() < 0.45:
            medications.add("metformin")
        if "nsaid" in schema.medications and rng.random() < 0.12:
            medications.add("nsaid")

        egfr_true = _clip(108 - 0.85 * max(age - 25, 0) + rng.gauss(0, 9) - (32 if ckd else 0), 5, 130)
        sbp_true = 112 + 0.33 * (age - 25) + (4 if diabetes else 0) + rng.gauss(0, 14)
        dbp_true = 0.58 * sbp_true + rng.gauss(0, 7)
        latent = {
            "sbp": sbp_true,
            "dbp": dbp_true,
            "egfr": egfr_true,
            "creatinine": 0.95 * (85.0 / egfr_true) ** 0.85 * math.exp(rng.gauss(0, 0.08)),
            "hba1c": (7.3 + rng.gauss(0, 1.3)) if diabetes else (5.5 + rng.gauss(0, 0.4)),
            "ldl": rng.gauss(118, 33) - (10 if ascvd else 0),
        }
        obs: dict[str, ObsValue] = {}
        for code, spec in schema.obs.items():
            if rng.random() < miss.get(code, 0.12):
                continue
            raw = latent.get(code)
            if raw is None:  # unknown observation: generic noise around the typical value
                raw = float(spec.typical) + rng.gauss(0, float(spec.hi - spec.lo) / 8)
            obs[code] = ObsValue(value=spec.snap(to_frac(round(raw, 6))))

        # prior creatinine value (trend operators): usually stable, occasionally a real rise
        spec = schema.obs.get("creatinine")
        if spec and "creatinine" in obs and rng.random() < 0.40:
            ratio = rng.uniform(1.4, 2.5) if rng.random() < 0.06 else math.exp(rng.gauss(0, 0.12))
            prior = spec.snap(to_frac(round(float(obs["creatinine"].value) / ratio, 6)))
            gap = min(rng.choice([6, 12, 24, 36, 48, 72, 96, 120, 168, 240, 300]), spec.max_gap_hours)
            if prior > 0:
                obs["creatinine"].prior, obs["creatinine"].gap_hours = prior, Fraction(gap)

        # plausibility: diastolic below systolic
        if "sbp" in obs and "dbp" in obs and obs["dbp"].value >= obs["sbp"].value:
            obs["dbp"].value = schema.obs["dbp"].snap(obs["sbp"].value - 5)
        out.append(Patient(age=Fraction(age), sex=sex, obs=obs, conditions=conditions, medications=medications, id=f"P{i + 1:05d}"))
    return out


def random_patients(schema: Schema, n: int = 200, seed: int = 1) -> list[Patient]:
    """Uniformly random plausible patients (no clinical correlations): a plain fuzzing baseline."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        obs = {}
        for code, spec in schema.obs.items():
            if rng.random() < 0.85:
                obs[code] = ObsValue(value=spec.snap(spec.lo + (spec.hi - spec.lo) * Fraction(rng.randrange(0, 10001), 10000)))
        if "sbp" in obs and "dbp" in obs and obs["dbp"].value >= obs["sbp"].value:
            obs["dbp"].value = schema.obs["dbp"].snap(obs["sbp"].value - 5)
        for code, spec in schema.obs.items():
            if code in obs and rng.random() < 0.15 and spec.max_gap_hours >= 24:
                prior = spec.snap(spec.lo + (spec.hi - spec.lo) * Fraction(rng.randrange(1, 10001), 10000))
                if prior > 0:
                    obs[code].prior, obs[code].gap_hours = prior, Fraction(rng.randrange(1, spec.max_gap_hours + 1))
        out.append(
            Patient(
                age=Fraction(rng.randint(schema.age_lo, schema.age_hi)),
                sex=rng.choice(schema.sexes),
                obs=obs,
                conditions={c for c in schema.conditions if rng.random() < 0.3},
                medications={c for c in schema.medications if rng.random() < 0.3},
                id=f"R{i + 1:05d}",
            )
        )
    return out


# -- CSV -----------------------------------------------------------------------
def _columns(schema: Schema, attr_keys: Iterable[str]) -> list[str]:
    cols = ["id", "age", "sex"]
    cols += [f"attr:{k}" for k in attr_keys]
    cols += [f"cond:{c}" for c in schema.conditions]
    cols += [f"med:{c}" for c in schema.medications]
    for c in schema.obs:
        cols += [f"obs:{c}", f"obs:{c}:prior", f"obs:{c}:gap_hours"]
    return cols


def write_cohort_csv(schema: Schema, patients: list[Patient], path: str | Path) -> None:
    attr_keys = sorted({k for p in patients for k in p.attrs})
    cols = _columns(schema, attr_keys)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in patients:
            row = {"id": p.id, "age": fmt(p.age), "sex": p.sex}
            for k in attr_keys:
                row[f"attr:{k}"] = p.attrs.get(k, "")
            for c in schema.conditions:
                row[f"cond:{c}"] = int(c in p.conditions)
            for c in schema.medications:
                row[f"med:{c}"] = int(c in p.medications)
            for c in schema.obs:
                o = p.obs.get(c)
                if o:
                    row[f"obs:{c}"] = "" if o.value is None else fmt(o.value)
                    row[f"obs:{c}:prior"] = "" if o.prior is None else fmt(o.prior)
                    row[f"obs:{c}:gap_hours"] = "" if o.gap_hours is None else fmt(o.gap_hours)
            w.writerow([row.get(c, "") for c in cols])


def read_cohort_csv(schema: Schema, path: str | Path) -> list[Patient]:
    out: list[Patient] = []
    with open(path, newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            obs: dict[str, ObsValue] = {}
            for c in schema.obs:
                v, pr, gp = row.get(f"obs:{c}", ""), row.get(f"obs:{c}:prior", ""), row.get(f"obs:{c}:gap_hours", "")
                if v != "" or pr != "":
                    obs[c] = ObsValue(to_frac(v) if v != "" else None, to_frac(pr) if pr != "" else None, to_frac(gp) if gp != "" else None)
            out.append(
                Patient(
                    age=to_frac(row.get("age") or 50),
                    sex=row.get("sex") or schema.sexes[0],
                    obs=obs,
                    conditions={c for c in schema.conditions if row.get(f"cond:{c}") in ("1", "true", "True")},
                    medications={c for c in schema.medications if row.get(f"med:{c}") in ("1", "true", "True")},
                    attrs={k[5:]: v for k, v in row.items() if k.startswith("attr:") and v != ""},
                    id=row.get("id") or f"row{i + 1}",
                )
            )
    return out
