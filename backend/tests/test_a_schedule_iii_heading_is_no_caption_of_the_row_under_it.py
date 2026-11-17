"""A Schedule III heading printed alone over a row is that row's heading, not its caption's head.

Four Indian 2024-25 reports printed one and the face reader glued it onto the first row under it:

    Income                                       Chemplast Sanmar, IndiGo
    Revenue from operations    4   2,387.61   1,655.58        -> "Income Revenue from operations"

    IV Expenses                                  JSW Steel (consolidated), no dot after the number
        Cost of materials consumed  88,324  93,590            -> "IV Expenses Cost of materials …"
    X Tax expense/(credit)   26(a)
        Current tax                  1,986   2,643            -> "X Tax expense/(credit) 26(a) Current tax"

    Items that will not be reclassified to profit or loss      HUL, IndiGo, JSW, Chemplast (wrapped)
        Remeasurements of the net defined benefit plans  (22)  36   -> one caption, no line

Each glued caption reached no line: revenue on both of Chemplast's and IndiGo's statements, current
tax on JSW's consolidated face — where with no tax heading the statement was one section, and its
check took total tax as the total of every line above it — and every OCI component under a heading.

What changed, in `row_reconstruct` and `mapping`:

- "Income" alone is a heading of the rows under it (`_SCHEDULE_III_INCOME_HEADING`), as "Financial
  assets" is. It names no section: "income" is inside too many captions to be a section word.
- The OCI block heading is one too (`_OCI_BLOCK_HEADING`), whatever it is numbered or bulleted
  with, and a heading that wraps is let go when its first row arrives.
- A roman numeral without its dot numbers a face line on an Indian filing (`_is_face_number`), and
  in `mapping._FACE_NUMBERING` where it opens a capitalised heading.
- A numbered line is a banner when its heading is exhausted by section words, asked on the line as
  printed (`_numbered_banner`): the rulebook's normalisation folds "XII" to "xii" first.
- A face heading is no caption's tail (JSW's standalone "Total (A)" took the next block's heading),
  and the row under an OCI heading may open on two enumerators (Asian Paints' "(i) (a)").

And what the rows needed once they were rows of their own, each measured on the filing named:

- `caption_shape.prose_reasons` no longer reads the IAS 1 phrase "items that will (not) be
  reclassified" as a finite verb — JSW's OCI tax lines were refused as narrative rows.
- The Ind AS OCI items line refuses "… other comprehensive income, net of tax" only where the
  caption OPENS with it (the total), not IndiGo's component "Debt instruments through other
  comprehensive income (net of tax)".
"""
from __future__ import annotations

import pytest

from app.core.models.geometry import BBox
from app.services.mapping import (
    section_of_banner,
    section_of_banner_only,
    strip_face_numbering,
)
from app.services.row_reconstruct import Word, build_line_items, enumerated_rows

_W, _H = 595.0, 842.0
_CHAR, _SPACE = 4.3, 2.2
_LEFT, _INDENT, _DEEP = 55.0, 72.0, 86.0
_NOTE, _CUR, _PRI = 330.0, 440.0, 530.0
_STEP = 11.0


def _line(text: str, x0: float, y: float) -> list[Word]:
    out, x = [], x0
    for t in text.split():
        out.append(Word(text=t, bbox=BBox(x0=x / _W, y0=y / _H,
                                          x1=(x + _CHAR * len(t)) / _W, y1=(y + 8) / _H)))
        x += _CHAR * len(t) + _SPACE
    return out


def _right(text: str, x1: float, y: float) -> list[Word]:
    return _line(text, x1 - _CHAR * len(text), y)


def _read(*lines, indian: bool = True, tight: float | None = None):
    """``lines`` are (caption, x0) or (caption, x0, current, prior[, note]). With ``tight`` a
    figure-less line is printed that close above the next, so row clustering merges the two."""
    words: list[Word] = _right("2025", _CUR, 100.0) + _right("2024", _PRI, 100.0)
    y = 115.0
    for spec in lines:
        caption, x0 = spec[0], spec[1]
        words += _line(caption, x0, y)
        if len(spec) > 2:
            words += _right(spec[2], _CUR, y) + _right(spec[3], _PRI, y)
            if len(spec) > 4:
                words += _line(spec[4], _NOTE, y)
        y += tight if (tight is not None and len(spec) == 2) else _STEP
    with enumerated_rows(indian):
        items, _ = build_line_items(words, page_index=7, document_id=None, source_kind="native",
                                    statement="profit_and_loss", on_face=True)
    return [(li.source_label, section_of_banner(li.section_hint or ""), li.group_hint or "")
            for li in items]


def _by_caption(rows):
    return {caption: (section, group) for caption, section, group in rows}


# ── "Income" ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tight", [None, 9.0])
def test_income_alone_heads_the_income_rows_and_is_no_caption(tight):
    """Chemplast Sanmar and IndiGo print "Income" flush with the rows under it."""
    rows = _read(
        ("Income", _LEFT),
        ("Revenue from operations", _LEFT, "2,387.61", "1,655.58", "4"),
        ("Other income", _LEFT, "21.13", "41.71", "5"),
        ("Total Income", _LEFT, "2,408.74", "1,697.29"),
        ("Expenses", _LEFT),
        ("Cost of materials consumed", _LEFT, "991.05", "726.19", "6(a)"),
        tight=tight,
    )
    got = _by_caption(rows)
    assert "Revenue from operations" in got, rows
    assert not any(c.startswith("Income ") for c, _s, _g in rows), rows
    assert got["Revenue from operations"][1] == got["Other income"][1] == "Income"
    # "Expenses" is a section banner and ends the heading's block.
    assert got["Cost of materials consumed"] == ("expenses", ""), rows


def test_income_wrapped_into_its_own_caption_stays_one_caption():
    """"Income" over a line that does not open a caption is the head of that caption."""
    rows = _read(
        ("Income", _LEFT),
        ("from operations", _LEFT, "500", "450"),
    )
    assert [c for c, _s, _g in rows] == ["Income from operations"], rows


def test_income_names_no_section():
    assert section_of_banner("Income") is None
    assert section_of_banner_only("Income") is None


# ── the OCI block heading ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row_x0", [_INDENT, _LEFT])
def test_an_oci_heading_heads_its_block(row_x0):
    """Hindustan Unilever indents the block's rows under the heading; IndiGo prints them flush."""
    rows = _read(
        ("Other comprehensive income", _LEFT),
        ("Items that will not be reclassified to profit or loss", _LEFT),
        ("Remeasurements of the net defined benefit plans", row_x0, "(22)", "36", "39C"),
        ("Tax on above", row_x0, "5", "(9)", "9A"),
        ("Items that will be reclassified to profit or loss", _LEFT),
        ("Fair value of cash flow hedges", row_x0,
         "(2)", "(1)", "18C"),
    )
    got = _by_caption(rows)
    assert "Remeasurements of the net defined benefit plans" in got, rows
    assert not any(c.startswith("Items that will") for c, _s, _g in rows
                   if c not in ("Items that will not be reclassified to profit or loss",
                                "Items that will be reclassified to profit or loss")), rows
    section, group = got["Remeasurements of the net defined benefit plans"]
    assert section == "other_comprehensive_income"
    assert group == "Items that will not be reclassified to profit or loss"
    assert got["Fair value of cash flow hedges"][1] == (
        "Items that will be reclassified to profit or loss")


def test_a_wrapped_oci_heading_is_let_go_at_its_first_row():
    """Chemplast Sanmar wraps the heading and lists the rows with a dash."""
    rows = _read(
        ("Other comprehensive income", _LEFT),
        ("Items that will not be reclassified to profit or loss in", _LEFT),
        ("subsequent periods", _LEFT),
        ("- Remeasurement of defined benefit plans", _LEFT, "(0.52)", "0.63"),
        ("- Income tax expense relating to remeasurement of defined benefit plans", _LEFT,
         "0.13", "(0.22)"),
    )
    got = _by_caption(rows)
    assert "- Remeasurement of defined benefit plans" in got, rows
    assert got["- Remeasurement of defined benefit plans"] == (
        "other_comprehensive_income",
        "Items that will not be reclassified to profit or loss in subsequent periods"), rows


def test_a_caption_still_wrapping_is_not_let_go():
    """A heading's words with an article still to finish them are a caption wrapping on."""
    rows = _read(
        ("Equity attributable to", _LEFT),
        ("owners of the", _LEFT),
        ("Parent", _LEFT, "12,345", "11,000"),
    )
    assert [c for c, _s, _g in rows] == ["Equity attributable to owners of the Parent"], rows


@pytest.mark.parametrize("text,heading", [
    ("Items that will not be reclassified to profit or loss", True),
    ("Items that will be reclassified to profit or loss", True),
    ("Items that may be reclassified subsequently to profit or loss", True),
    ("Items that will not be reclassified to profit or loss in subsequent periods", True),
    ("A (i) Items that will not be reclassified to profit or loss", True),
    ("B (i) Items that will be reclassified to profit or loss", True),
    ("(i) Items that will not be reclassified to profit or loss:", True),
    ("Income", True), ("INCOME", True),
    # captions, not headings
    ("Items that will not be reclassified to profit or loss in", False),
    ("Income tax relating to items that will not be reclassified to profit or loss", False),
    ("Income tax expense", False), ("Other income", False), ("Total income", False),
    ("Income from operations", False),
])
def test_only_the_bare_heading_is_a_heading(text, heading):
    from app.services.row_reconstruct import _is_face_group_heading

    assert _is_face_group_heading(text) is heading


# ── a roman numeral without its dot ─────────────────────────────────────────────────────────────

def _jsw():
    return (
        ("III Total income (I + II)", _LEFT, "169,518", "176,010"),
        ("IV Expenses", _LEFT),
        ("Cost of materials consumed", _INDENT, "88,324", "93,590"),
        ("Total expenses", _INDENT, "163,641", "163,047"),
        ("IX Profit before tax (VII-VIII)", _LEFT, "5,077", "13,380"),
        ("X Tax expense/(credit) 26(a)", _LEFT),
        ("Current tax", _INDENT, "1,986", "2,643"),
        ("Deferred tax", _INDENT, "(182)", "733"),
        ("Total tax expense/(credit)", _INDENT, "1,586", "4,407"),
        ("XI Net Profit for the year (IX-X)", _LEFT, "3,491", "8,973"),
        ("XII Other comprehensive income / (loss)", _LEFT),
        ("A (i) Items that will not be reclassified to profit or loss", _LEFT),
        ("a) Remeasurement gain/(loss) of the defined benefit plans", _DEEP, "43", "4"),
        ("(ii) Income tax relating to items that will not be reclassified to profit or loss",
         _INDENT, "(145)", "(344)"),
    )


def test_a_dotless_schedule_iii_face_reads_its_headings():
    """JSW Steel's consolidated profit and loss, as printed."""
    rows = _read(*_jsw())
    got = _by_caption(rows)
    for caption in ("Cost of materials consumed", "Current tax",
                    "a) Remeasurement gain/(loss) of the defined benefit plans"):
        assert caption in got, (caption, rows)
    assert got["Cost of materials consumed"][0] == "expenses"
    assert got["Current tax"][0] == got["Deferred tax"][0] == "tax_expense"
    assert got["Total tax expense/(credit)"][0] == "tax_expense"
    # The numbered line after the tax block opens a section of its own, as "IX." does.
    assert got["XI Net Profit for the year (IX-X)"][0] != "tax_expense"
    remeasurement = got["a) Remeasurement gain/(loss) of the defined benefit plans"]
    assert remeasurement == ("other_comprehensive_income",
                             "A (i) Items that will not be reclassified to profit or loss"), rows


def test_the_dotless_number_is_read_on_an_indian_filing_only():
    from app.services.row_reconstruct import _is_face_number, _is_roman_number

    with enumerated_rows(True):
        assert _is_face_number("IV") and _is_roman_number("XII") and _is_face_number("IV.")
        assert not _is_face_number("A")         # a letter keeps its dot: it may be an article
        assert _is_face_number("A.")
    with enumerated_rows(False):
        assert not _is_face_number("IV") and not _is_roman_number("XII")
        assert _is_face_number("IV.") and _is_roman_number("XII.")


@pytest.mark.parametrize("text,section", [
    ("IV Expenses", "expenses"),
    ("IV. Expenses", "expenses"),
    ("X Tax expense/(credit)", "tax_expense"),
    ("VIII. Tax expense:", "tax_expense"),
    ("XII Other comprehensive income / (loss)", "other_comprehensive_income"),
    ("III. Total income (I + II)", None),
    ("I Revenue from operations", None),        # a numbered caption, not a banner
    ("Tax expense/(credit)", None),             # unnumbered: the aside is still a word
])
def test_a_numbered_line_is_a_banner_when_its_heading_is(text, section):
    assert section_of_banner_only(text) == section


@pytest.mark.parametrize("text,stripped", [
    ("IV Expenses", "Expenses"),
    ("X Tax expense/(credit)", "Tax expense/(credit)"),
    ("II. Other income", "Other income"),
    # not numbered: no capitalised heading after the letters, or no space
    ("VAT receivable", "VAT receivable"),
    ("Investments in X Ltd", "Investments in X Ltd"),
    ("IV expenses", "IV expenses"),
    ("A Shares", "A Shares"),
])
def test_the_dotless_number_opens_a_capitalised_heading(text, stripped):
    assert strip_face_numbering(text) == stripped


def test_a_heading_after_a_total_is_not_spliced_into_it():
    """JSW Steel's standalone face: "Total (A)" and then the next block's heading."""
    rows = _read(
        ("X Other comprehensive income/ (loss)", _LEFT),
        ("A i) Items that will not be reclassified to profit or loss", _LEFT),
        ("(a) Re-measurements of the defined benefit plans", _DEEP, "3", "-"),
        ("Total (A)", _INDENT, "(44)", "2,174"),
        ("B i) Items that will be reclassified to profit or loss", _LEFT),
        ("(a) The effective portion of gains and loss on hedging", _DEEP, "555", "(248)"),
    )
    got = _by_caption(rows)
    assert "Total (A)" in got, rows
    assert got["(a) The effective portion of gains and loss on hedging"][1] == (
        "B i) Items that will be reclassified to profit or loss"), rows


# ── what the rows under an OCI heading need, once they are their own rows ──────────────────────

@pytest.mark.parametrize("caption", [
    "ii) Income tax relating to items that will not be reclassified to profit or loss",
    "(ii) Income tax relating to items that will be reclassified to profit or loss",
    "Income tax relating to items that may be reclassified subsequently to profit or loss",
])
def test_the_ias_1_name_of_an_oci_block_is_no_sentence(caption):
    """JSW Steel's OCI tax lines were refused by the residual as narrative rows."""
    from app.services.caption_shape import prose_reasons

    assert prose_reasons(caption) == []


def test_a_sentence_about_oci_items_is_still_one():
    from app.services.caption_shape import prose_reasons

    assert "finite_verb" in prose_reasons(
        "Items that will not be reclassified to profit or loss which are recognised in the period")


def _oci_items_refuses(caption: str) -> bool:
    import json
    import pathlib
    import re

    seed = (pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
            / "output_csv_indas_line_items.json")
    items = json.loads(seed.read_text(encoding="utf-8"))["items"]
    hints = next(i for i in items
                 if i["key"] == "is_oci__other_comprehensive_income_items")["exclude_hints"]
    return any(re.search(h, caption.lower()) for h in hints)


@pytest.mark.parametrize("caption,refused", [
    # IndiGo 2024-25's component, 40 / 6 — refused before, and the OCI check failed by it
    ("Debt instruments through other comprehensive income (net of tax) 17.c.", False),
    ("Equity instruments through other comprehensive income, net of tax", False),
    ("Remeasurements of defined benefit plans (net of tax)", False),
    # the totals the exclusion is for
    ("Other comprehensive income, net of tax", True),
    ("Other comprehensive income/(loss), net of tax", True),
    ("X. Other comprehensive income (net of tax)", True),
    ("Other comprehensive income / (loss) for the year, net of tax", True),
    ("Total other comprehensive income", True),
])
def test_the_ind_as_oci_items_line_refuses_the_total_only(caption, refused):
    assert _oci_items_refuses(caption) is refused


def test_a_row_under_an_oci_heading_may_open_on_two_enumerators():
    """Asian Paints: "A. Items that will not be reclassified to Profit or Loss" over "(i) (a)
    Remeasurement of the defined benefit plans", and "B. …" over "(i) (a) Net fair value …"."""
    rows = _read(
        ("OTHER COMPREHENSIVE INCOME (OCI)", _LEFT),
        ("A. Items that will not be reclassified to Profit or Loss", _LEFT),
        ("(i) (a) Remeasurement of the defined benefit plans", _LEFT, "(23.18)", "0.47"),
        ("(b) Income tax on remeasurement", _INDENT, "5.83", "(0.12)"),
        ("B. Items that will be reclassified to Profit or Loss", _LEFT),
        ("(i) (a) Net fair value loss on debt instruments", _LEFT, "(18.99)", "3.67"),
    )
    got = _by_caption(rows)
    assert got.get("(i) (a) Remeasurement of the defined benefit plans", (None, ""))[1] == (
        "A. Items that will not be reclassified to Profit or Loss"), rows
    assert got.get("(i) (a) Net fair value loss on debt instruments", (None, ""))[1] == (
        "B. Items that will be reclassified to Profit or Loss"), rows
