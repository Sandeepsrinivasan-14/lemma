"""Exact decimal arithmetic helpers.

Lemma evaluates rules with exact rationals so that the concrete interpreter and the
SMT encoding agree to the last digit (floats would disagree at thresholds such as 0.1).
"""
from __future__ import annotations

from fractions import Fraction
from typing import Union

Number = Union[int, float, str, Fraction]


def to_frac(x: Number) -> Fraction:
    if isinstance(x, Fraction):
        return x
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, float):
        return Fraction(repr(x))  # shortest round-trip decimal, e.g. 0.1 -> 1/10
    if isinstance(x, str):
        return Fraction(x)
    raise TypeError(f"cannot convert {type(x).__name__} to Fraction")


def fmt(x: Fraction, max_places: int = 9) -> str:
    """Exact decimal string when possible, else a 4-significant-digit float."""
    for k in range(0, max_places + 1):
        scaled = x * (10**k)
        if scaled.denominator == 1:
            n = int(scaled)
            if k == 0:
                return str(n)
            sign = "-" if n < 0 else ""
            s = str(abs(n)).rjust(k + 1, "0")
            return f"{sign}{s[:-k]}.{s[-k:]}"
    return f"{float(x):.4g}"


def to_float(x: Fraction) -> float:
    return float(x)
