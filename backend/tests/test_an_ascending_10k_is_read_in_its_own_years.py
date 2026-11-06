"""Three defects a stand-in pass over Alphabet's 2025 10-K found in the additional items.

Alphabet prints 2024 BEFORE 2025, sets its debt maturities as a table whose unit is stated once, in
the sentence above it, and prints its tax note as a "Current:" block followed by a "Deferred:" one.

* EVERY FIGURE THE MODEL CITED FROM A SENTENCE WAS FILED AS THE PRIOR YEAR. `_current_period` took
  the label of the first value it met, which is the first column printed — 2024 on this filing.
  The guarantees, the purchase commitments, the six debt maturities and the fair value of debt
  were published under 2024 with the 2025 cells empty.
* A BARE LINE OF A MONEY TABLE WAS READ AS A COUNT. "2027  1,000" prints no "$" (a US table prints
  it on its first line and its total only), so the sentence route stored it at scale 1 with no
  currency, while "2026 $ 2,000" was money in millions.
* "DEFERRED:" HEADED ONE ROW. After the "Current:" block closed, its pending end fired on the first
  row under "Deferred:" and emptied the group after it, so the deferred "Total" was one of four
  bare "Total" rows of the note and the model's citation of it was refused as a tie.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models.geometry import BBox
from app.services.note_sourced import _page_sentence
from app.services.row_reconstruct import Word, build_line_items
from app.stages.line_item_llm import _current_period


def _doc(*labels):
    values = {i: SimpleNamespace(period_label=p, column_index=None) for i, p in enumerate(labels)}
    return SimpleNamespace(line_items=[SimpleNamespace(values=values)])


def test_the_current_period_is_current_where_the_statements_use_it():
    assert _current_period(_doc("prior", "current")) == "current"


def test_a_filing_with_other_labels_keeps_its_first_one():
    assert _current_period(_doc("FY2025", "FY2024")) == "FY2025"
    assert _current_period(SimpleNamespace(line_items=[])) is None


PAGE = ("The total estimated fair value of the outstanding notes was approximately $9.0 billion and "
        "$45.6 billion as of December 31, 2024 and December 31, 2025, respectively.\n"
        "As of December 31, 2025, the future principal payments for long-term debt were as follows "
        "(in millions):\n 2026 $ 2,000\n 2027 1,000\n 2028 2,676\n 2029 1,764\n 2030 5,500\n"
        " Thereafter 36,145\n Total $ 49,085\nCredit Facility\n"
        "As of December 31, 2025, we had 190,820 employees.\n")


def _entry(amount, quote):
    entry, why = _page_sentence(amount, quote, 73, {73: PAGE})
    assert entry is not None, why
    return entry


def test_a_bare_line_of_a_money_table_is_money_in_the_tables_unit():
    for amount, quote in [("1,000", "2027 1,000"), ("36,145", "Thereafter 36,145")]:
        entry = _entry(amount, quote)
        assert entry["prose_money"] is True, quote
        assert Decimal(entry["prose_scale"]) == Decimal(1_000_000), quote


def test_the_first_line_is_money_as_before():
    assert _entry("2,000", "2026 $ 2,000")["prose_money"] is True


def test_a_count_in_a_sentence_after_the_table_is_still_a_count():
    entry = _entry("190,820", "we had 190,820 employees")
    assert entry["prose_money"] is False and entry["prose_scale"] is None


# ── the tax note's two blocks ────────────────────────────────────────────────────────────────

H = 0.0141
LINES = [  # (text, x0, figures) — Alphabet 2025, note 14, as positioned on its page
    ("Current:", 0.0749, ()), ("Federal and state $", 0.0945, ("15,716", "21,101", "13,378")),
    ("Foreign", 0.0945, ("3,935", "3,852", "5,028")), ("Total", 0.1141, ("19,651", "24,953", "18,406")),
    ("Deferred:", 0.0749, ()), ("Federal and state", 0.0945, ("(7,482)", "(5,350)", "8,243")),
    ("Foreign", 0.0945, ("(247)", "94", "7")), ("Total", 0.1141, ("(7,729)", "(5,256)", "8,250")),
    ("Provision for income taxes $", 0.0749, ("11,922", "19,697", "26,656")),
]


def _words():
    words, y = [], 0.552
    for text, x0, figures in LINES:
        x = x0
        for tok in text.split():
            w = 0.0068 * len(tok)
            words.append(Word(text=tok, bbox=BBox(x0=x, y0=y, x1=x + w, y1=y + H)))
            x += w + 0.0045
        for fx, fig in zip((0.62, 0.76, 0.90), figures):
            words.append(Word(text=fig, bbox=BBox(x0=fx, y0=y, x1=fx + 0.06, y1=y + H)))
        y += 0.0189
    return words


def test_each_row_of_the_deferred_block_is_under_deferred():
    items, _ = build_line_items(_words(), page_index=83, document_id="d", source_kind="native",
                                on_face=False)
    groups = [(li.source_label, li.group_hint) for li in items]
    assert groups[3:6] == [("Federal and state", "Deferred:"), ("Foreign", "Deferred:"),
                           ("Total", "Deferred:")], groups
    assert [g for _, g in groups[:3]] == ["Current:"] * 3, groups
    # Set back to the heading's own edge: the block has ended.
    assert groups[6] == ("Provision for income taxes $", ""), groups
