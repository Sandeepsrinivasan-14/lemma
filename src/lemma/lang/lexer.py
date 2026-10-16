"""Tokenizer for CQL-lite."""
from __future__ import annotations

from dataclasses import dataclass


class LemmaSyntaxError(ValueError):
    def __init__(self, msg: str, pos: int = 0, src: str = ""):
        self.msg = msg
        self.pos = pos
        self.line, self.col = line_col(src, pos)
        super().__init__(f"{msg} (line {self.line}, col {self.col})")


def line_col(src: str, pos: int) -> tuple[int, int]:
    pos = max(0, min(pos, len(src)))
    line = src.count("\n", 0, pos) + 1
    last = src.rfind("\n", 0, pos)
    return line, pos - last


@dataclass
class Token:
    kind: str  # NUM IDENT DQ SQ OP EOF
    text: str
    pos: int
    end: int


_OPS2 = {"<=", ">=", "!=", "<>"}
_OPS1 = set("(),:<>=+-*/")


def tokenize(src: str) -> list[Token]:
    toks: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise LemmaSyntaxError("unterminated comment", i, src)
            i = j + 2
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            while j < n and src[j].isdigit():
                j += 1
            if j < n and src[j] == "." and j + 1 < n and src[j + 1].isdigit():
                j += 1
                while j < n and src[j].isdigit():
                    j += 1
            toks.append(Token("NUM", src[i:j], i, j))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(Token("IDENT", src[i:j], i, j))
            i = j
            continue
        if c in "\"'":
            j = src.find(c, i + 1)
            if j < 0:
                raise LemmaSyntaxError("unterminated string", i, src)
            toks.append(Token("DQ" if c == '"' else "SQ", src[i + 1 : j], i, j + 1))
            i = j + 1
            continue
        two = src[i : i + 2]
        if two in _OPS2:
            toks.append(Token("OP", "!=" if two == "<>" else two, i, i + 2))
            i += 2
            continue
        if c in _OPS1:
            toks.append(Token("OP", c, i, i + 1))
            i += 1
            continue
        if c == "[":
            raise LemmaSyntaxError(
                "retrieve syntax like [Observation: ...] is not part of CQL-lite v0.1; "
                "use Latest(\"code\") / HasCondition(\"code\") instead",
                i,
                src,
            )
        raise LemmaSyntaxError(f"unexpected character {c!r}", i, src)
    toks.append(Token("EOF", "", n, n))
    return toks
