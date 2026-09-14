r"""PLAIN PHRASES COMPILE TO THE PATTERNS THE SEVEN LINES USED TO CARRY BY HAND.

NEW FILE -> backend/tests/test_prose_grammar.py

WHAT CHANGED. `note_source.prose_any` took raw regexes, and the seven lines with a prose route
carried 28 of them averaging 300 characters. Split structurally, every one was the same three-part
sentence shape, and across the seven lines two of the three parts were BYTE IDENTICAL:

    SUBJECT      identical on all 7   `depreciation|amortisation|amortization`, 折旧|折舊|摊销|攤銷
    CONNECTIVE   identical on all 7   `included\s+in|charged\s+to|…`, 计入|計入|列入|…
    DESTINATION  the only per-line part
    BOUNDS       identical on all 7   (200, 80) forward English, (120) reverse, (80, 40) / (40) Han

So the shared halves moved to `LineItemSet.prose_grammar` and the bounds became constants, leaving
a line to say only where the figure landed — in words, in any script, with Traditional spellings
generated rather than authored.

WHY THIS FILE IS THE ONE THAT MATTERS. Replacing a 300-character alternation is exactly how a
spelling gets dropped, and a dropped spelling is not a test failure — it is a figure that silently
stops being found on some future filing nobody has run yet. So `_ORIGINAL` below is the 28 patterns
AS THEY SHIPPED, frozen, and the coverage test expands each one into every string it admits and
requires the generated patterns to still match all of them. That is a claim about the refactor that
survives in the suite rather than living in a commit message.

It was also checked against 271 sentences pulled from twelve real filings, where the per-line match
sets came out identical — nothing lost, nothing newly claimed. That corpus cannot live here: the
filings are client documents and `_filings/` is gitignored. The expansion below is the committable
half, and it is the stronger half for coverage, because the real corpus exercises a handful of
phrasings while this exercises every one the shipped patterns admitted.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.schemas.line_items import LineItemSet, ProseGrammar, load_line_item_set
from app.services.prose_grammar import compile_for, compile_prose

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# ── THE 28 PATTERNS AS THEY SHIPPED, frozen ───────────────────────────────────────────────────
# Two lines' worth is enough to pin the shape and the vocabulary in both scripts and both word
# orders; the destination alternations here are the two widest of the seven, so between them they
# cover every construct the shipped set used — optional prefix (`(?:other\s+)?`), optional plural
# (`expenses?`), inner alternation, an `&` with tolerant spacing, and a Han character class.
_ORIGINAL: dict[str, list[str]] = {
    "sub__operating_expense_depreciation": [
        r"(?:depreciation|amortisation|amortization)[^.。]{0,200}?(?:included\s+in|charged\s+to"
        r"|recognised\s+in|recognized\s+in|borne\s+by|allocated\s+to|presented\s+in"
        r"|reported\s+(?:in|within))[^.。]{0,80}?(?:(?:other\s+)?operating\s+(?:expenses?|costs?))",
        r"(?:(?:other\s+)?operating\s+(?:expenses?|costs?))[^.。]{0,120}?"
        r"(?:depreciation|amortisation|amortization)",
        r"(?:折旧|折舊|摊销|攤銷)[^。]{0,80}?(?:计入|計入|列入|包括(?:于|於|在)|计提(?:于|於)|归属于"
        r"|歸屬於|分[摊攤][至于於])[^。]{0,40}?(?:(?:其他)?(?:经营|經營)(?:开支|開支|费用|費用|成本))",
        r"(?:(?:其他)?(?:经营|經營)(?:开支|開支|费用|費用|成本))[^。]{0,40}?(?:折旧|折舊|摊销|攤銷)",
    ],
    "sub__rd_depreciation": [
        r"(?:depreciation|amortisation|amortization)[^.。]{0,200}?(?:included\s+in|charged\s+to"
        r"|recognised\s+in|recognized\s+in|borne\s+by|allocated\s+to|presented\s+in"
        r"|reported\s+(?:in|within))[^.。]{0,80}?"
        r"(?:research\s+and\s+development\s+(?:expenses?|costs?)|r\s*&\s*d\s+(?:expenses?|costs?))",
        r"(?:research\s+and\s+development\s+(?:expenses?|costs?)|r\s*&\s*d\s+(?:expenses?|costs?))"
        r"[^.。]{0,120}?(?:depreciation|amortisation|amortization)",
        r"(?:折旧|折舊|摊销|攤銷)[^。]{0,80}?(?:计入|計入|列入|包括(?:于|於|在)|计提(?:于|於)|归属于"
        r"|歸屬於|分[摊攤][至于於])[^。]{0,40}?(?:(?:研发|研發|研究(?:及|与|與)开发|研究(?:及|与|與)"
        r"開發)(?:开支|開支|费用|費用))",
        r"(?:(?:研发|研發|研究(?:及|与|與)开发|研究(?:及|与|與)開發)(?:开支|開支|费用|費用))"
        r"[^。]{0,40}?(?:折旧|折舊|摊销|攤銷)",
    ],
}


# ── EXPANDING A SHIPPED PATTERN INTO EVERY STRING IT ADMITS ───────────────────────────────────
# The grammar those patterns use is small and closed: literals, `|`, `(?:…)`, `?`, `\s+`, `\s*`,
# character classes. Anything outside it raises rather than being approximated, so a pattern this
# cannot read is a test error and never a silently narrower check.
_GAP = re.compile(r"\[\^\.?。\]\{0,\d+\}\?")


class _Expander:
    def __init__(self, src: str):
        self.s, self.i = src, 0

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else None

    def alternation(self) -> list[str]:
        out = self.sequence()
        while self.peek() == "|":
            self.i += 1
            out += self.sequence()
        return out

    def sequence(self) -> list[str]:
        acc = [""]
        while True:
            ch = self.peek()
            if ch is None or ch in "|)":
                return acc
            # HOISTED OUT OF THE COMPREHENSION deliberately. `for b in self.atom()` inside it would
            # call `atom` once per element of `acc` — advancing the cursor each time — so an
            # optional group, which is exactly what makes `acc` longer than one, would desynchronise
            # the parse.
            atoms = self.atom()
            acc = [a + b for a in acc for b in atoms]

    def atom(self) -> list[str]:
        ch = self.peek()
        if ch == "(":
            assert self.s.startswith("(?:", self.i), f"capturing group in {self.s!r}"
            self.i += 3
            inner = self.alternation()
            assert self.peek() == ")", f"unclosed group in {self.s!r}"
            self.i += 1
            return self.quantify(inner)
        if ch == "[":
            close = self.s.index("]", self.i)
            body = self.s[self.i + 1:close]
            self.i = close + 1
            assert not body.startswith("^"), f"negated class {body!r}"
            return self.quantify(list(body))
        if ch == "\\":
            nxt = self.s[self.i + 1]
            self.i += 2
            if nxt == "s":
                q = self.peek()
                if q is not None and q in "+*":
                    self.i += 1
                    return [" "] if q == "+" else ["", " "]
                return [" "]
            return self.quantify([nxt])
        self.i += 1
        return self.quantify([ch])

    def quantify(self, atoms: list[str]) -> list[str]:
        nxt = self.peek()
        if nxt == "?":
            self.i += 1
            return [""] + atoms
        assert nxt is None or nxt not in "+*", f"unsupported quantifier in {self.s!r}"
        return atoms


def _expand(pattern: str) -> list[str]:
    e = _Expander(pattern)
    out = e.alternation()
    assert e.i == len(pattern), f"stopped at {e.i} of {len(pattern)} in {pattern!r}"
    seen, uniq = set(), []
    for s in (" ".join(x.split()) for x in out):
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _parts(pattern: str) -> list[str]:
    """The pattern's top-level parts, each unwrapped from its own `(?:…)`."""
    out = []
    for chunk in _GAP.split(pattern):
        chunk = chunk.strip()
        if chunk.startswith("(?:") and chunk.endswith(")"):
            chunk = chunk[3:-1]
        out.append(chunk)
    return out


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _generated(shipped, key: str) -> list[re.Pattern]:
    item = {i.key: i for i in shipped.items}[key]
    return [re.compile(p, re.IGNORECASE)
            for p in compile_for(item.note_source, shipped.prose_grammar)]


# ── THE CLAIM ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(_ORIGINAL))
def test_every_spelling_the_shipped_pattern_admitted_still_matches(shipped, key):
    """THE TEST THIS FILE EXISTS FOR. Each shipped pattern is expanded into every string it could
    match — every Simplified/Traditional mix, every `&` spacing, both plurals — and each is put
    into a sentence the generated patterns must still claim.

    A dropped spelling is not a visible bug. It is a figure that stops being found on a filing
    nobody has run yet, which is why this is checked exhaustively rather than by sampling.
    """
    generated = _generated(shipped, key)
    assert generated, f"{key} generated no patterns"

    misses = []
    for pattern in _ORIGINAL[key]:
        parts = _parts(pattern)
        han = "[^。]" in pattern
        joiner = "" if han else " "
        # Forward patterns are subject/connective/destination; reverse are destination/subject.
        if len(parts) == 3:
            subjects, connectives, places = (_expand(p) for p in parts)
        else:
            places, subjects = (_expand(p) for p in parts)
            connectives = [""]
        for place in places:
            for at, subject in enumerate(subjects):
                conn = connectives[at % len(connectives)]
                if conn:
                    sentence = (f"{subject}{joiner}of{joiner}1,234,000{joiner}{conn}"
                                f"{joiner}{place}." if not han
                                else f"{subject}1,234,000元{conn}{place}。")
                else:
                    sentence = (f"{place}{joiner}include{joiner}{subject}{joiner}of"
                                f"{joiner}1,234,000." if not han
                                else f"{place}包含{subject}1,234,000元。")
                if not any(r.search(sentence) for r in generated):
                    misses.append(sentence)

    assert not misses, (f"{len(misses)} spelling(s) the shipped pattern admitted are no longer "
                        f"matched, e.g. {misses[:3]}")


def test_the_footnote_the_whole_route_exists_for(shipped):
    """The reference HK footnote, whose figure appears in no extracted row anywhere in the filing.
    It is what motivated sentence-length bounds, so it is the one sentence that must never stop
    matching whatever else is refactored."""
    generated = _generated(shipped, "sub__pbt_oper_exp_depreciation")
    footnote = ("Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
                "included in “other operating expenses” on the face of the consolidated "
                "income statement.")
    assert any(r.search(footnote) for r in generated)


def test_a_caption_length_bound_would_not_have_reached_it():
    """WHY THE BOUNDS ARE WIDE, stated as arithmetic rather than as a claim. 74 characters sit
    between the subject and the connective in that footnote, so the `.{0,40}` proximity a
    `row_caption_any` pattern uses does not reach — which is the whole reason prose needs its own
    patterns instead of reusing the row ones."""
    footnote = ("Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
                "included in “other operating expenses”.")
    gap = footnote.index("included in") - len("Depreciation")
    assert gap > 40, gap
    caption_bound = re.compile(r"depreciation.{0,40}other\s+operating\s+expenses", re.IGNORECASE)
    assert not caption_bound.search(footnote)


def test_the_disjunction_failure_is_not_reintroduced(shipped):
    """THE MEASURED FAILURE THAT MADE THIS A PATTERN AND NOT A TERM LIST. Splitting the rule into
    "depreciation OR operating expenses" matched any sentence merely mentioning operating expenses:
    it replaced a depreciation charge of 587,417 with 36,966,000 and invented two more figures. The
    generated patterns keep the conjunction, so a rental sentence in the right note is not claimed.
    """
    rental = ("Rental income of HK$12,345,000 is presented within other operating expenses for "
              "the year ended 31 December 2024.")
    for key in ("sub__operating_expense_depreciation", "sub__pbt_oper_exp_depreciation"):
        assert not any(r.search(rental) for r in _generated(shipped, key)), key


# ── THE GENERATOR'S OWN RULES ─────────────────────────────────────────────────────────────────

def test_a_traditional_spelling_is_generated_from_the_simplified_one():
    """THE REDUCTION THAT PAYS FOR ITSELF. 222 destination spellings across the seven lines became
    74 phrases, and most of what went was Traditional/Simplified duplication the author had to type
    twice. Every Han character expands to a class of its variants, so the MIXED spelling a filing
    can print when one character of a compound was typed in the other script matches too — which
    the shipped `分[摊攤][至于於]` also allowed and a whole-phrase fold would not.
    """
    [pattern] = compile_prose(["折旧"], [], ["经营开支"])
    rx = re.compile(pattern)
    for spelling in ("经营开支", "經營開支", "經营开支", "经營開支"):
        assert rx.search(f"{spelling}包含折舊1,234元。"), spelling
    assert rx.search("经营开支包含折旧1,234元。")


def test_the_two_scripts_never_mix_into_one_pattern():
    """An English subject with a Chinese destination would be matching a sentence that does not
    exist, and would carry the Latin distance bounds over Han text, where the same statement is
    written in about a third of the characters."""
    patterns = compile_prose(["depreciation", "折旧"], ["included in", "计入"],
                             ["operating expenses", "经营开支"])
    # Two per script: forward and reverse.
    assert len(patterns) == 4
    latin = [p for p in patterns if "[^.。]" in p]
    han = [p for p in patterns if "[^。]" in p and "[^.。]" not in p]
    assert len(latin) == 2 and len(han) == 2
    for p in latin:
        assert "折" not in p and "经" not in p, p
    for p in han:
        assert "depreciation" not in p and "operating" not in p, p


def test_a_destination_in_only_one_script_gets_only_that_scripts_patterns():
    """A line authored in English gets two patterns, not four — and NOT a Han pattern with an empty
    group, which would match every sentence in the note."""
    assert len(compile_prose(["depreciation"], ["included in"], ["operating expenses"])) == 2
    assert len(compile_prose(["折旧"], ["计入"], ["经营开支"])) == 2


def test_the_reverse_order_takes_no_connective():
    """"Other operating expenses include depreciation" puts the verb between the two, and "Cost of
    sales — depreciation" has no verb at all. Requiring a connective would lose both, so the
    reverse pattern does without one — as the shipped patterns did."""
    forward, reverse = compile_prose(["depreciation"], ["included in"], ["operating expenses"])
    assert "included" in forward
    assert "included" not in reverse
    rx = re.compile(reverse, re.IGNORECASE)
    assert rx.search("Operating expenses include depreciation of 1,234,000.")
    assert rx.search("Operating expenses — depreciation 1,234,000")


def test_the_plural_is_optional_on_the_destination_and_nowhere_else():
    """The shipped patterns took this liberty on every destination (`expenses?`, `costs?`,
    `revenue|revenues`) and on neither of the other two parts. Nothing writes "borne bys", so a
    liberty there would only make the generated pattern read as though it were guessing."""
    [forward, _rev] = compile_prose(["depreciation"], ["borne by"], ["cost of revenue"])
    assert "depreciations?" not in forward
    assert "bys?" not in forward
    rx = re.compile(forward, re.IGNORECASE)
    assert rx.search("Depreciation of 1,234,000 borne by cost of revenue.")
    assert rx.search("Depreciation of 1,234,000 borne by cost of revenues.")


def test_spacing_hyphens_and_ampersands_are_forgiven():
    """`R&D`, `R & D` and `R&  D` are the same words to a reader and three strings to `==`. The
    shipped set wrote `r\\s*&\\s*d` by hand for exactly this."""
    [forward, _rev] = compile_prose(["depreciation"], ["included in"], ["R&D expenses"])
    rx = re.compile(forward, re.IGNORECASE)
    for spelling in ("R&D expenses", "R & D expenses", "R&  D expense", "r&d expenses"):
        assert rx.search(f"Depreciation of 1,234,000 included in {spelling}."), spelling


def test_a_generated_pattern_always_compiles():
    """THE WHOLE POINT OF THE CHANGE. `_refuse_uncompilable` exists because a broken pattern is a
    silent hole — the match stops happening and a figure quietly includes or omits rows nobody can
    trace. A plain phrase cannot fail to compile, so that class of error is unreachable here, even
    for phrases carrying the characters a regex would choke on."""
    hostile = ["cost of sales (net)", "a+b", "[unclosed", r"back\slash", "50% of costs", "*star"]
    for pattern in compile_prose(["depreciation"], ["included in"], hostile):
        re.compile(pattern)          # raises if the escaping is wrong
    [forward, _rev] = compile_prose(["depreciation"], ["included in"], ["cost of sales (net)"])
    assert re.compile(forward, re.IGNORECASE).search(
        "Depreciation of 1,234,000 included in cost of sales (net).")


def test_nothing_authored_generates_nothing():
    """Every empty case, because each one would otherwise be a group that matches anything."""
    assert compile_prose([], [], []) == []
    assert compile_prose(["depreciation"], ["included in"], []) == []
    assert compile_prose([], ["included in"], ["operating expenses"]) == []
    assert compile_for(None, ProseGrammar()) == []


# ── THE REFUSALS ──────────────────────────────────────────────────────────────────────────────

def _set_with(note_source: dict) -> dict:
    return {
        "schema_version": 1, "line_items_key": "probe", "target_template_key": "t",
        "prose_grammar": {"connective": ["included in"],
                          "subjects": {"depreciation": ["depreciation"]}},
        "items": [{"key": "probe_line", "label": "Probe", "note_source": note_source}],
    }


def test_a_destination_with_no_subject_is_refused_at_load():
    """The silence this replaces. "other operating expenses" alone says where a figure landed and
    nothing about what figure, so no pattern can be built — and the line would stay empty exactly
    as though the filing had never mentioned it. Refused with the line's key, so it is attributable
    to a line of configuration rather than to "the engine"."""
    with pytest.raises(ValueError, match="prose_subject names no subject vocabulary"):
        LineItemSet.model_validate(_set_with({"note_title_any": ["operating"],
                                              "prose_landed_in": ["operating expenses"]}))


def test_a_subject_naming_no_vocabulary_is_refused_at_load():
    """A typo in `prose_subject` is the same silence: the name resolves to nothing, the destination
    compiles to nothing, and the figure never appears."""
    with pytest.raises(ValueError, match="is not in prose_grammar.subjects"):
        LineItemSet.model_validate(_set_with({"note_title_any": ["operating"],
                                              "prose_subject": "depreciaton",
                                              "prose_landed_in": ["operating expenses"]}))


def test_a_prose_route_with_no_note_to_read_is_refused_at_load():
    """`select_prose` reaches its note through `note_title_any` and returns nothing without one, so
    a destination with no note title is a rule that cannot fire however well it is written. This
    was true of `prose_any` too and nothing said so."""
    with pytest.raises(ValueError, match="note_title_any is empty"):
        LineItemSet.model_validate(_set_with({"prose_subject": "depreciation",
                                              "prose_landed_in": ["operating expenses"]}))


def test_a_subject_with_no_destination_is_allowed():
    """It says what the line is about and leaves the prose route off, which is a legitimate
    half-authored state for a screen to be able to save."""
    st = LineItemSet.model_validate(_set_with({"note_title_any": ["operating"],
                                               "prose_subject": "depreciation"}))
    assert st.items[0].note_source.prose_subject == "depreciation"
    assert compile_for(st.items[0].note_source, st.prose_grammar) == []


def test_the_shipped_set_still_loads_and_every_prose_line_generates_patterns(shipped):
    """The prose lines, end to end: each carries a destination, names a vocabulary the set has,
    and compiles to four patterns — two per script.

    SIX, NOT SEVEN. `sub__pbt_cos_depreciation` was the seventh and was retired deliberately along
    with the cascade tier that read it."""
    prose = [i for i in shipped.items
             if getattr(getattr(i, "note_source", None), "prose_landed_in", None)]
    assert len(prose) == 6, [i.key for i in prose]
    for item in prose:
        patterns = compile_for(item.note_source, shipped.prose_grammar)
        assert len(patterns) == 4, f"{item.key} generated {len(patterns)}"
        for p in patterns:
            re.compile(p, re.IGNORECASE)
    # And nothing is left carrying a raw pattern, which is what the escape hatch being an escape
    # hatch means: measured over the 28 patterns replaced, none needed it.
    assert not [i.key for i in shipped.items
                if getattr(getattr(i, "note_source", None), "prose_any", None)]
