"""Recalculate a workbook's formulas in Python — the subset a template workbook uses.

A test helper, so a filled workbook can be checked by its OWN arithmetic without Excel or
LibreOffice: openpyxl writes formulas and keeps no computed values. Covers what the Company v3
workbook's first three sheets use — ROUND, SUM, IF, IFERROR, YEAR, + - * / ^, the comparisons,
"" and TRUE/FALSE, cell and range references with or without `$`, and references to other sheets —
with Excel's semantics where they differ from Python's: a blank cell is 0 in arithmetic and equal
to both 0 and ""; text in arithmetic is #VALUE!; IF evaluates only the branch it takes; ROUND rounds
half away from zero. Anything else raises, so an unsupported formula cannot pass by accident.

    book = Evaluator(openpyxl.load_workbook(path))
    book.value("Balance Sheet", "F134")
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal

from openpyxl.utils import column_index_from_string, get_column_letter


class ExcelError(Exception):
    pass


_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>(?:'(?P<qs>[^']+)'!|(?P<us>[A-Za-z_][\w.]*)!)?\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)
            (?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<fn>[A-Z][A-Z0-9.]*)(?=\()
  | (?P<bool>TRUE|FALSE)\b
  | (?P<op><>|<=|>=|[-+*/^=<>&(),])
""", re.X)


def _tokens(text: str):
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ExcelError(f"cannot read {text[pos:pos + 20]!r} in {text!r}")
        pos = m.end()
        if m.group("ws"):
            continue
        if m.group("ref"):
            out.append(("ref", (m.group("qs") or m.group("us"), m.group("c1"), int(m.group("r1")),
                                m.group("c2"), int(m.group("r2")) if m.group("r2") else None)))
        elif m.group("str") is not None:
            out.append(("str", m.group("str")[1:-1].replace('""', '"')))
        elif m.group("num"):
            out.append(("num", float(m.group("num"))))
        elif m.group("fn"):
            out.append(("fn", m.group("fn")))
        elif m.group("bool"):
            out.append(("bool", m.group("bool") == "TRUE"))
        else:
            out.append(("op", m.group("op")))
    return out


class _Parser:
    """Recursive descent over Excel precedence: comparison < & < +- < */ < ^ < unary."""

    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok != ("op", value):
            raise ExcelError(f"expected {value!r}, found {tok!r}")
        self.i += 1
        return tok

    def parse(self):
        node = self.compare()
        if self.i != len(self.t):
            raise ExcelError(f"unparsed tail {self.t[self.i:]!r}")
        return node

    def compare(self):
        node = self.concat()
        while self.peek()[0] == "op" and self.peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self.take()[1]
            node = ("cmp", op, node, self.concat())
        return node

    def concat(self):
        node = self.additive()
        while self.peek() == ("op", "&"):
            self.take()
            node = ("cat", node, self.additive())
        return node

    def additive(self):
        node = self.term()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.power()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.power())
        return node

    def power(self):
        node = self.unary()
        while self.peek() == ("op", "^"):
            self.take()
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        if self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            return ("neg", self.unary()) if op == "-" else self.unary()
        return self.atom()

    def atom(self):
        kind, val = self.take()
        if kind in ("num", "str", "bool"):
            return ("lit", val)
        if kind == "ref":
            return ("ref", val)
        if kind == "fn":
            self.take("(")
            args = []
            if self.peek() != ("op", ")"):
                args.append(self.compare())
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.compare())
            self.take(")")
            return ("fn", val, args)
        if (kind, val) == ("op", "("):
            node = self.compare()
            self.take(")")
            return node
        raise ExcelError(f"unexpected {kind} {val!r}")


def _num(v):
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        base = _dt.date(1899, 12, 30)
        d = v.date() if isinstance(v, _dt.datetime) else v
        return float((d - base).days)
    raise ExcelError(f"#VALUE! {v!r} in arithmetic")


def _round(x: float, n: int) -> float:
    q = Decimal(1).scaleb(-n)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


class Evaluator:
    def __init__(self, workbook):
        self.wb = workbook
        self.memo: dict[tuple[str, str], object] = {}
        self.parsed: dict[str, tuple] = {}
        self.stack: set[tuple[str, str]] = set()

    def value(self, sheet: str, coord: str):
        key = (sheet, coord)
        if key in self.memo:
            return self.memo[key]
        if key in self.stack:
            raise ExcelError(f"circular reference at {sheet}!{coord}")
        raw = self.wb[sheet][coord].value
        if isinstance(raw, str) and raw.startswith("="):
            self.stack.add(key)
            try:
                tree = self.parsed.get(raw)
                if tree is None:
                    tree = self.parsed[raw] = _Parser(_tokens(raw[1:])).parse()
                try:
                    out = self.eval(tree, sheet)
                except ExcelError as e:
                    out = e
            finally:
                self.stack.discard(key)
        else:
            out = raw
        self.memo[key] = out
        return out

    def _cell(self, sheet, coord):
        v = self.value(sheet, coord)
        if isinstance(v, ExcelError):
            raise v
        return v

    def eval(self, node, sheet):
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "ref":
            other, c1, r1, c2, r2 = node[1]
            if c2 is not None:
                raise ExcelError("a range outside a function")
            return self._cell(other or sheet, f"{c1}{r1}")
        if kind == "neg":
            return -_num(self.eval(node[1], sheet))
        if kind == "bin":
            a, b = _num(self.eval(node[2], sheet)), _num(self.eval(node[3], sheet))
            op = node[1]
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "^":
                return a ** b
            if b == 0:
                raise ExcelError("#DIV/0!")
            return a / b
        if kind == "cat":
            return f"{self.eval(node[1], sheet) or ''}{self.eval(node[2], sheet) or ''}"
        if kind == "cmp":
            a, b = self.eval(node[2], sheet), self.eval(node[3], sheet)
            if a is None:
                a = "" if isinstance(b, str) else 0.0
            if b is None:
                b = "" if isinstance(a, str) else 0.0
            if isinstance(a, str) != isinstance(b, str):
                eq = False
                lt = not isinstance(a, str)            # numbers sort before text
            else:
                if not isinstance(a, str):
                    a, b = _num(a), _num(b)
                eq, lt = a == b, a < b
            return {"=": eq, "<>": not eq, "<": lt, ">": not lt and not eq,
                    "<=": lt or eq, ">=": not lt}[node[1]]
        if kind == "fn":
            return self.call(node[1], node[2], sheet)
        raise ExcelError(f"cannot evaluate {node!r}")

    def _range(self, ref, sheet):
        other, c1, r1, c2, r2 = ref
        if c2 is None:
            return [self._cell(other or sheet, f"{c1}{r1}")]
        out = []
        for col in range(column_index_from_string(c1), column_index_from_string(c2) + 1):
            for row in range(r1, r2 + 1):
                out.append(self._cell(other or sheet, f"{get_column_letter(col)}{row}"))
        return out

    def call(self, name, args, sheet):
        if name == "IF":
            cond = self.eval(args[0], sheet)
            if isinstance(cond, str):
                raise ExcelError("#VALUE! text as a condition")
            branch = args[1] if _num(cond) else (args[2] if len(args) > 2 else ("lit", False))
            return self.eval(branch, sheet)
        if name == "IFERROR":
            try:
                return self.eval(args[0], sheet)
            except ExcelError:
                return self.eval(args[1], sheet)
        if name == "SUM":
            total = 0.0
            for a in args:
                items = self._range(a[1], sheet) if a[0] == "ref" else [self.eval(a, sheet)]
                for v in items:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        total += float(v)
            return total
        if name == "ROUND":
            return _round(_num(self.eval(args[0], sheet)), int(_num(self.eval(args[1], sheet))))
        if name == "YEAR":
            v = self.eval(args[0], sheet)
            if isinstance(v, (_dt.date, _dt.datetime)):
                return float(v.year)
            raise ExcelError("#VALUE! YEAR of a non-date")
        raise ExcelError(f"unsupported function {name}")
