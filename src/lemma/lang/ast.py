"""AST for CQL-lite, the CQL subset Lemma can reason about.

Nodes are plain mutable dataclasses. The checker annotates them in place with
``kind`` ("bool" | "num" | "str"), ``unit`` and ``analyte`` (see lang/check.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional


@dataclass
class Node:
    span: Optional[tuple[int, int]] = field(default=None, repr=False, compare=False, kw_only=True)
    # --- annotations filled by the checker ---
    kind: Optional[str] = field(default=None, repr=False, compare=False, kw_only=True)
    unit: Optional[str] = field(default=None, repr=False, compare=False, kw_only=True)
    analyte: Optional[str] = field(default=None, repr=False, compare=False, kw_only=True)


@dataclass
class Num(Node):
    value: Fraction = Fraction(0)
    unit_src: Optional[str] = None  # unit exactly as written, e.g. 'mg/dL'
    canon: Optional[Fraction] = field(default=None, repr=False, compare=False)  # value in canonical unit (checker)


@dataclass
class Str(Node):
    value: str = ""


@dataclass
class Bool(Node):
    value: bool = False


@dataclass
class Null(Node):
    pass


@dataclass
class Ref(Node):
    """Reference to another define in the same library."""

    name: str = ""


@dataclass
class Code(Node):
    """A quoted code argument such as the "egfr" in Latest("egfr")."""

    code: str = ""


@dataclass
class Duration(Node):
    hours: Fraction = Fraction(0)
    text: str = ""  # as written, e.g. "48 hours"
    count: Fraction = Fraction(0)
    unit_word: str = "hours"


@dataclass
class Call(Node):
    name: str = ""
    args: list = field(default_factory=list)


@dataclass
class Unary(Node):
    op: str = "not"  # "not" | "-"
    x: Optional[Node] = None


@dataclass
class Bin(Node):
    op: str = "and"  # and or < <= > >= = != + - * /
    l: Optional[Node] = None
    r: Optional[Node] = None


@dataclass
class IsNull(Node):
    x: Optional[Node] = None
    negated: bool = False


@dataclass
class Define:
    name: str
    body: Node
    span: tuple[int, int] = (0, 0)
    kind: Optional[str] = None


@dataclass
class Library:
    name: str
    version: Optional[str]
    defines: dict  # name -> Define (insertion ordered)
    source: str = ""
    diagnostics: list = field(default_factory=list)
    trend_codes: set = field(default_factory=set)
    used_codes: set = field(default_factory=set)
    used_flags: set = field(default_factory=set)


@dataclass
class Invariant:
    name: str
    rule: str
    mode: str  # "never" | "requires"
    cond: Node
    source: Optional[str] = None
    span: tuple[int, int] = (0, 0)


COMPARE_OPS = ("<", "<=", ">", ">=", "=", "!=")
CODE_FUNCS = {"Latest", "Delta", "Ratio", "HasCondition", "OnMedication"}
TREND_FUNCS = {"Delta", "Ratio"}


def walk(node: Node):
    """Yield node and all descendants."""
    yield node
    if isinstance(node, Call):
        for a in node.args:
            yield from walk(a)
    elif isinstance(node, Unary):
        yield from walk(node.x)
    elif isinstance(node, Bin):
        yield from walk(node.l)
        yield from walk(node.r)
    elif isinstance(node, IsNull):
        yield from walk(node.x)
