"""Recursive-descent parser for CQL-lite libraries, invariants and single expressions."""
from __future__ import annotations

from fractions import Fraction

from . import ast as A
from .lexer import LemmaSyntaxError, Token, tokenize

_DUR_WORDS = {"hour": 1, "hours": 1, "day": 24, "days": 24}


class Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = tokenize(src)
        self.i = 0

    # -- token helpers -------------------------------------------------------
    @property
    def t(self) -> Token:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Token:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def err(self, msg: str, tok: Token | None = None):
        raise LemmaSyntaxError(msg, (tok or self.t).pos, self.src)

    def advance(self) -> Token:
        tok = self.t
        if tok.kind != "EOF":
            self.i += 1
        return tok

    def at_ident(self, word: str) -> bool:
        return self.t.kind == "IDENT" and self.t.text == word

    def at_op(self, op: str) -> bool:
        return self.t.kind == "OP" and self.t.text == op

    def expect_ident(self, word: str) -> Token:
        if not self.at_ident(word):
            self.err(f"expected '{word}' but found {self.t.text!r}")
        return self.advance()

    def expect_op(self, op: str) -> Token:
        if not self.at_op(op):
            self.err(f"expected '{op}' but found {self.t.text or 'end of input'!r}")
        return self.advance()

    def prev_end(self) -> int:
        return self.toks[self.i - 1].end

    # -- top level -----------------------------------------------------------
    def parse_library(self) -> A.Library:
        self.expect_ident("library")
        if self.t.kind not in ("IDENT", "DQ"):
            self.err("expected a library name")
        name = self.advance().text
        version = None
        if self.at_ident("version"):
            self.advance()
            if self.t.kind not in ("SQ", "DQ"):
                self.err("expected a quoted version string")
            version = self.advance().text
        defines: dict[str, A.Define] = {}
        while self.t.kind != "EOF":
            if not self.at_ident("define"):
                self.err(f"expected 'define' but found {self.t.text!r}")
            start = self.advance().pos
            if self.t.kind not in ("DQ", "IDENT"):
                self.err("expected a define name")
            nt = self.advance()
            if nt.text in defines:
                self.err(f"duplicate define {nt.text!r}", nt)
            self.expect_op(":")
            body = self.parse_expr()
            defines[nt.text] = A.Define(nt.text, body, (start, self.prev_end()))
        return A.Library(name=name, version=version, defines=defines, source=self.src)

    def parse_invariants(self) -> list[A.Invariant]:
        out: list[A.Invariant] = []
        while self.t.kind != "EOF":
            start = self.expect_ident("invariant").pos
            if self.t.kind not in ("DQ", "SQ"):
                self.err("expected a quoted invariant name")
            name = self.advance().text
            self.expect_ident("rule")
            if self.t.kind not in ("DQ", "SQ"):
                self.err("expected a quoted rule name")
            rule = self.advance().text
            if self.at_ident("never"):
                self.advance()
                self.expect_ident("when")
                mode = "never"
            elif self.at_ident("requires"):
                self.advance()
                mode = "requires"
            else:
                self.err("expected 'never when' or 'requires'")
            cond = self.parse_expr()
            source = None
            if self.at_ident("source"):
                self.advance()
                if self.t.kind not in ("DQ", "SQ"):
                    self.err("expected a quoted source string")
                source = self.advance().text
            out.append(A.Invariant(name, rule, mode, cond, source, (start, self.prev_end())))
        return out

    def parse_standalone_expr(self) -> A.Node:
        e = self.parse_expr()
        if self.t.kind != "EOF":
            self.err(f"unexpected {self.t.text!r} after expression")
        return e

    # -- expressions ---------------------------------------------------------
    def parse_expr(self) -> A.Node:
        return self.parse_or()

    def _bin(self, op, l, r, start):
        return A.Bin(op=op, l=l, r=r, span=(start, self.prev_end()))

    def parse_or(self) -> A.Node:
        start = self.t.pos
        left = self.parse_and()
        while self.at_ident("or"):
            self.advance()
            left = self._bin("or", left, self.parse_and(), start)
        return left

    def parse_and(self) -> A.Node:
        start = self.t.pos
        left = self.parse_not()
        while self.at_ident("and"):
            self.advance()
            left = self._bin("and", left, self.parse_not(), start)
        return left

    def parse_not(self) -> A.Node:
        if self.at_ident("not"):
            start = self.advance().pos
            x = self.parse_not()
            return A.Unary(op="not", x=x, span=(start, self.prev_end()))
        return self.parse_cmp()

    def parse_cmp(self) -> A.Node:
        start = self.t.pos
        left = self.parse_add()
        if self.t.kind == "OP" and self.t.text in A.COMPARE_OPS:
            op = self.advance().text
            return self._bin(op, left, self.parse_add(), start)
        if self.at_ident("is"):
            self.advance()
            neg = False
            if self.at_ident("not"):
                self.advance()
                neg = True
            self.expect_ident("null")
            return A.IsNull(x=left, negated=neg, span=(start, self.prev_end()))
        return left

    def parse_add(self) -> A.Node:
        start = self.t.pos
        left = self.parse_mul()
        while self.t.kind == "OP" and self.t.text in ("+", "-"):
            op = self.advance().text
            left = self._bin(op, left, self.parse_mul(), start)
        return left

    def parse_mul(self) -> A.Node:
        start = self.t.pos
        left = self.parse_unary()
        while self.t.kind == "OP" and self.t.text in ("*", "/"):
            op = self.advance().text
            left = self._bin(op, left, self.parse_unary(), start)
        return left

    def parse_unary(self) -> A.Node:
        if self.at_op("-"):
            start = self.advance().pos
            if self.t.kind == "NUM":  # fold negative literals
                num = self.parse_number(start, negative=True)
                return num
            x = self.parse_unary()
            return A.Unary(op="-", x=x, span=(start, self.prev_end()))
        return self.parse_atom()

    def parse_number(self, start: int, negative: bool = False) -> A.Num:
        tok = self.advance()
        v = Fraction(tok.text)
        if negative:
            v = -v
        unit = None
        if self.t.kind == "SQ":
            unit = self.advance().text
        return A.Num(value=v, unit_src=unit, span=(start, self.prev_end()))

    def parse_atom(self) -> A.Node:
        tok = self.t
        if tok.kind == "NUM":
            return self.parse_number(tok.pos)
        if tok.kind == "SQ":
            self.advance()
            return A.Str(value=tok.text, span=(tok.pos, tok.end))
        if tok.kind == "DQ":
            self.advance()
            return A.Ref(name=tok.text, span=(tok.pos, tok.end))
        if tok.kind == "OP" and tok.text == "(":
            self.advance()
            e = self.parse_expr()
            self.expect_op(")")
            return e
        if tok.kind == "IDENT":
            if tok.text in ("true", "false"):
                self.advance()
                return A.Bool(value=tok.text == "true", span=(tok.pos, tok.end))
            if tok.text == "null":
                self.advance()
                return A.Null(span=(tok.pos, tok.end))
            if self.peek().kind == "OP" and self.peek().text == "(":
                return self.parse_call()
            self.err(f"unexpected identifier {tok.text!r} (functions are written Name(...))")
        self.err(f"unexpected {tok.text or 'end of input'!r}")

    def parse_call(self) -> A.Node:
        name_tok = self.advance()
        self.expect_op("(")
        args: list[A.Node] = []
        if not self.at_op(")"):
            while True:
                args.append(self.parse_arg(name_tok.text, len(args)))
                if self.at_op(","):
                    self.advance()
                    continue
                break
        self.expect_op(")")
        return A.Call(name=name_tok.text, args=args, span=(name_tok.pos, self.prev_end()))

    def parse_arg(self, fname: str, idx: int) -> A.Node:
        tok = self.t
        if fname in A.CODE_FUNCS and idx == 0:
            if tok.kind != "DQ":
                self.err(f'{fname}() needs a quoted code as first argument, e.g. {fname}("egfr")')
            self.advance()
            return A.Code(code=tok.text, span=(tok.pos, tok.end))
        if tok.kind == "NUM" and self.peek().kind == "IDENT" and self.peek().text in _DUR_WORDS:
            self.advance()
            word = self.advance().text
            count = Fraction(tok.text)
            return A.Duration(
                hours=count * _DUR_WORDS[word],
                text=f"{tok.text} {word}",
                count=count,
                unit_word=word,
                span=(tok.pos, self.prev_end()),
            )
        return self.parse_expr()


def parse_library(src: str) -> A.Library:
    return Parser(src).parse_library()


def parse_invariants(src: str) -> list[A.Invariant]:
    return Parser(src).parse_invariants()


def parse_expression(src: str) -> A.Node:
    return Parser(src).parse_standalone_expr()
