"""Test synthesis (M18): boundary-value and missing-data test cases derived from a rule's own thresholds.

For every comparison against a literal, emit patients just below, on and just above the threshold
(on the measurement grid), plus one "measurement missing" patient per observation the library reads.
Expected outcomes come from the reference interpreter on the rule version the tests are generated from,
so the output is a regression suite you can replay against a later version.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Optional

from ..interp import Interpreter
from ..lang import ast as A
from ..numeric import fmt
from ..patient import ObsValue, Patient
from ..schema import Schema


def baseline(schema: Schema, codes) -> Patient:
    """A typical patient: typical age, every observation the library reads present at its typical value."""
    obs = {c: ObsValue(value=schema.obs[c].typical) for c in codes if c in schema.obs}
    return Patient(age=Fraction(schema.age_typical), sex=schema.sexes[0], obs=obs, id="baseline")


def _literals(lib: A.Library):
    """Yield (comparison node, operand node, literal Num) for comparisons of an observable against a number."""
    for d in lib.defines.values():
        for n in A.walk(d.body):
            if isinstance(n, A.Bin) and n.op in A.COMPARE_OPS:
                for a, b in ((n.l, n.r), (n.r, n.l)):
                    if isinstance(b, A.Num) and isinstance(a, A.Call) and a.name in ("Latest", "Delta", "Ratio", "AgeInYears"):
                        yield n, a, b
                    elif isinstance(b, A.Num) and isinstance(a, A.Bin):
                        # e.g. Latest(x) + 1 'mg/dL' < 3 'mg/dL' : not decomposed; skipped by design
                        pass


def boundary_patients(schema: Schema, lib: A.Library) -> list[tuple[str, Patient]]:
    base_codes = sorted(lib.used_codes)
    out: list[tuple[str, Patient]] = []
    seen: set[str] = set()

    def add(name: str, p: Patient):
        key = repr(p.to_dict())
        if key not in seen:
            seen.add(key)
            p.id = name
            out.append((name, p))

    for _node, a, lit in _literals(lib):
        c = lit.canon
        if a.name == "AgeInYears":
            for dv in (-1, 0, 1):
                v = c + dv
                if schema.age_lo <= v <= schema.age_hi and v.denominator == 1:
                    p = baseline(schema, base_codes)
                    p.age = v
                    add(f"age={fmt(v)}", p)
            continue
        code = a.args[0].code
        spec = schema.obs[code]
        if a.name == "Latest":
            targets = [(c + k * spec.step) for k in (-1, 0, 1)]
            for v in targets:
                if spec.lo <= v <= spec.hi and (v / spec.step).denominator == 1:
                    p = baseline(schema, base_codes)
                    p.obs[code] = ObsValue(value=v)
                    add(f"{code}={fmt(v)}", p)
        else:  # Delta / Ratio need a prior value
            prior = spec.typical
            hours = a.args[1].hours
            gap = min(Fraction(int(hours)), Fraction(spec.max_gap_hours)) if hours >= 1 else Fraction(1)
            centre = (prior + c) if a.name == "Delta" else prior * c
            centre = round(centre / spec.step) * spec.step
            for k in (-1, 0, 1):
                v = centre + k * spec.step
                if spec.lo <= v <= spec.hi:
                    p = baseline(schema, base_codes)
                    p.obs[code] = ObsValue(value=v, prior=prior, gap_hours=gap)
                    add(f"{code}={fmt(v)}(prior {fmt(prior)})", p)
            # one outside the time window
            if hours + 1 <= spec.max_gap_hours:
                p = baseline(schema, base_codes)
                p.obs[code] = ObsValue(value=round(centre / spec.step) * spec.step, prior=prior, gap_hours=Fraction(int(hours) + 1))
                add(f"{code} outside {a.args[1].text}", p)
    for code in base_codes:
        p = baseline(schema, base_codes)
        p.obs.pop(code, None)
        add(f"{code} missing", p)
    add("baseline", baseline(schema, base_codes))
    return out


def generate_tests(schema: Schema, lib: A.Library, rule: str) -> list[dict]:
    interp = Interpreter(lib, schema)
    tests = []
    for name, p in boundary_patients(schema, lib):
        v = interp.evaluate(p, rule)
        tests.append({"name": name, "patient": p.to_dict(), "expected": "true" if v is True else "false" if v is False else "null", "fires": v is True})
    return tests


def run_tests(schema: Schema, lib: A.Library, rule: str, tests: list[dict]) -> list[dict]:
    """Replay a suite against a (possibly different) version; return the failing cases."""
    interp = Interpreter(lib, schema)
    failures = []
    for t in tests:
        p = Patient.from_dict(t["patient"])
        got = interp.fires(p, rule)
        if got != t["fires"]:
            failures.append({"name": t["name"], "expected_fires": t["fires"], "got_fires": got, "patient": t["patient"]})
    return failures
