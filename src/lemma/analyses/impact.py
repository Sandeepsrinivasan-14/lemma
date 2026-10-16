"""Cohort impact (M6) and equity diff (M15).

Given two versions of a rule and a cohort, count who gains or loses the recommendation and break the
change down by subgroup. The equity part is a DESCRIPTIVE audit: it shows where changes land in the
data you give it, with confidence intervals. It makes no causal or fairness claim.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Callable, Optional

from ..interp import Interpreter
from ..lang import ast as A
from ..patient import Patient
from ..schema import Schema

MIN_GROUP = 30
AGE_BANDS = [(18, 39, "18-39"), (40, 59, "40-59"), (60, 74, "60-74"), (75, 200, "75+")]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def newcombe_diff(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float, float]:
    """Difference of two proportions p1 - p2 with a Newcombe hybrid-score 95% interval."""
    if n1 == 0 or n2 == 0:
        return 0.0, 0.0, 0.0
    p1, l1, u1 = wilson(k1, n1)
    p2, l2, u2 = wilson(k2, n2)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, lo, hi


def age_band(p: Patient) -> str:
    for lo, hi, name in AGE_BANDS:
        if lo <= p.age <= hi:
            return name
    return "other"


def group_functions(patients: list[Patient]) -> dict[str, Callable[[Patient], str]]:
    fns: dict[str, Callable[[Patient], str]] = {"sex": lambda p: p.sex, "age_band": age_band}
    keys = sorted({k for p in patients for k in p.attrs})
    for k in keys:
        fns[k] = (lambda key: (lambda p: p.attrs.get(key, "(missing)")))(k)
    return fns


def _rate(k: int, n: int) -> dict:
    p, lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": round(p, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}


def cohort_impact(schema: Schema, lib_a: A.Library, lib_b: A.Library, rule: str, patients: list[Patient], rule_b: Optional[str] = None) -> dict:
    rule_b = rule_b or rule
    ia, ib = Interpreter(lib_a, schema), Interpreter(lib_b, schema)
    fns = group_functions(patients)
    rows = []
    for p in patients:
        a, b = ia.fires(p, rule), ib.fires(p, rule_b)
        rows.append((p, a, b))
    n = len(rows)
    gained = sum(1 for _, a, b in rows if b and not a)
    lost = sum(1 for _, a, b in rows if a and not b)
    both = sum(1 for _, a, b in rows if a and b)
    out = {
        "n": n,
        "a_fires": _rate(sum(1 for _, a, _b in rows if a), n),
        "b_fires": _rate(sum(1 for _, _a, b in rows if b), n),
        "gained": _rate(gained, n),
        "lost": _rate(lost, n),
        "fires_under_both": both,
        "neither": n - gained - lost - both,
        "changed": _rate(gained + lost, n),
        "groups": {},
        "notes": [
            "Descriptive audit of the cohort provided; not a causal or fairness claim.",
            f"Groups smaller than {MIN_GROUP} are flagged: their intervals are wide and should not be over-read.",
        ],
    }
    for gname, fn in fns.items():
        by: dict[str, list] = {}
        for p, a, b in rows:
            by.setdefault(fn(p), []).append((a, b))
        total_changed = gained + lost
        entries = []
        for g in sorted(by):
            items = by[g]
            gn = len(items)
            g_gain = sum(1 for a, b in items if b and not a)
            g_lost = sum(1 for a, b in items if a and not b)
            g_changed = g_gain + g_lost
            rest_n, rest_changed = n - gn, total_changed - g_changed
            d, lo, hi = newcombe_diff(g_changed, gn, rest_changed, rest_n)
            fa = sum(1 for a, _ in items if a)
            fb = sum(1 for _, b in items if b)
            entries.append(
                {
                    "group": g,
                    "n": gn,
                    "gained": g_gain,
                    "lost": g_lost,
                    "changed": _rate(g_changed, gn),
                    "a_fire_rate": round(fa / gn, 4),
                    "b_fire_rate": round(fb / gn, 4),
                    "changed_rate_minus_rest": {"diff": round(d, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4), "significant": lo > 0 or hi < 0},
                    "small_group": gn < MIN_GROUP,
                }
            )
        out["groups"][gname] = entries
    return out


def top_changers(schema: Schema, lib_a: A.Library, lib_b: A.Library, rule: str, patients: list[Patient], limit: int = 5, rule_b: Optional[str] = None) -> dict:
    """Example patients who gain / lose the recommendation (for display)."""
    rule_b = rule_b or rule
    ia, ib = Interpreter(lib_a, schema), Interpreter(lib_b, schema)
    gained, lost = [], []
    for p in patients:
        a, b = ia.fires(p, rule), ib.fires(p, rule_b)
        if b and not a and len(gained) < limit:
            gained.append(p.to_dict())
        if a and not b and len(lost) < limit:
            lost.append(p.to_dict())
    return {"gained": gained, "lost": lost}
