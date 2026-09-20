"""A caption whose last line is printed on the next page, and the alias that doubled a total.

ROW RECONSTRUCTION RUNS PER PAGE, so the head is gone by the time the tail is read. Two captions
in the corpus are drawn that way, and one of them is the case this module's own docstring called
impossible — `opens_a_bracket_it_never_closes` records that on 澜起科技 688008 "the consolidated
balance sheet's last line is printed 负债和所有者权益（或 with its figures, and 股东权益）总计 is the
first text on the next page, above that page's own running header. Nothing on either page can put
them back together." It also records the cost: 12,218,911,386.38, the balance sheet's balancing
total, summed into `bs_ca__other_current_assets`.

`extract_pdf` now carries the previous page's last CAPTIONED row forward — the row, its statement
and its page index — and `build_line_items` reports back through `spliced_out` when this page
opened with the rest of its caption, in the same in/out shape `carry_group`/`group_out` already
uses for the note-continuation carry. The completed caption is written back onto the row that
holds the figures, provenance snippet included.

MEASURED, all 1079 pages of the five filings. Every (previous page's last captioned row with
figures, this page's first label-only row) pair was enumerated; four satisfy the vocabulary and
TWO satisfy every condition:

    688008 page index 150->151   负债和所有者权益（或 + 股东权益）总计
                                 -> `bs_cl__total_equity_and_liabilities` 12,218,911,386.38,
                                    which ties: 822,405,821.47 liabilities + 11,396,505,564.91
                                    equity, and equals `bs_ca__total_assets`. Two structural
                                    relations went from unverifiable to passing.
    300319 page index 102->103   归属母公司所有者的其他综合收益 + 的税后净额

The other two are 000709's equity statement opening two pages with a row of NIL DASHES: `- - - -`
normalises away, so the join "completes" 二、本年期初余额 and 1．提取盈余公积 — captions that are
complete already. The printable-text condition refuses them.

AND THE ALIAS THAT MADE THE SECOND SPLICE SAFE. `is_oci__total_other_comprehensive_income`
declared BOTH 其他综合收益的税后净额 (the total) and 归属母公司所有者的其他综合收益的税后净额 (the
parent's share) among its `zh` aliases. A CAS income statement prints both lines, so both bound
the concept and `concept_value` — which sums a concept's carriers, deduplicating only a fact whose
caption, amount AND page all match — added them. Measured: 000709 published -75,833,856.66 against
a printed -37,916,928.33, and 688008 published 132,682,798.88 against 66,341,399.44 — each EXACTLY
double, on the same page in 000709's case and one page apart in 688008's. Completing 300319's
truncated attribution caption would have added a third instance of the same defect, which is why
the alias goes first: an attribution line is a breakdown of the total, not the total.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.services.mapping import normalize_label
from app.services.row_reconstruct import (
    Word,
    _caption_continued_from_the_previous_page,
    _chrome_key,
    _pipeline_steps,
    in_force_rules,
)

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
LINE_H = 0.011
GLYPH = 0.016


@pytest.fixture(scope="module")
def steps():
    scope, norm = in_force_rules()
    return _pipeline_steps(norm, scope)


@pytest.fixture(scope="module")
def known():
    from app.schemas.line_items import load_line_item_set
    from app.services.mapping import known_captions
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(
        json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8")),
        resolve=True)
    return known_captions(build_working_view(cfg))


def _rows(*lines: tuple[str, tuple[str, ...]]) -> list[list[Word]]:
    """One row per line: (caption, value tokens). The running header comes first, as on a page."""
    out = []
    for i, (text, vals) in enumerate(lines):
        y = 0.08 + i * 0.03
        row = []
        if text:
            row.append(Word(text=text, bbox=BBox(x0=0.10, y0=y,
                                                 x1=0.10 + GLYPH * len(text), y1=y + LINE_H)))
        for j, v in enumerate(vals):
            row.append(Word(text=v, bbox=BBox(x0=0.60 + 0.15 * j, y0=y,
                                              x1=0.70 + 0.15 * j, y1=y + LINE_H)))
        out.append(row)
    return out


def _ask(rows, carry, steps, known):
    """The running header is row 0 of every fixture below, and `page_chrome` is how the reader
    knows it is one — `pdf_extract._page_chrome` collects exactly these keys off the real pages.
    Passed empty, the header itself reads as the page's first body row and nothing can splice."""
    chrome = frozenset({_chrome_key(w.text) for w in rows[0]})
    return _caption_continued_from_the_previous_page(
        rows, None, steps, known, carry, chrome, ())


# ── the two real splices ──────────────────────────────────────────────────────────────────────

def test_the_balance_sheets_balancing_total_is_rejoined(steps, known):
    """688008 page index 150->151, the case the module docstring called impossible."""
    rows = _rows(("澜起科技股份有限公司2024 年年度报告", ()),
                 ("股东权益）总计", ()),
                 ("流动资产：", ()))
    got = _ask(rows, "负债和所有者权益（或", steps, known)
    assert got is not None
    _orphan, whole = got
    assert whole == "负债和所有者权益（或股东权益）总计"


def test_the_oci_attribution_caption_is_rejoined(steps, known):
    """300319 page index 102->103."""
    rows = _rows(("深圳市麦捷微电子科技股份有限公司2024 年年度报告全文", ()),
                 ("的税后净额", ()),
                 ("（一）不能重分类进损益的其他综合收益", ("-10,825,600.00", "-284,303.92")))
    got = _ask(rows, "归属母公司所有者的其他综合收益", steps, known)
    assert got is not None
    assert got[1] == "归属母公司所有者的其他综合收益的税后净额"


# ── and everything it must refuse ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frag", ["- - - -", "- - -", "-", "—", "－ －"])
def test_a_row_of_nil_dashes_is_not_a_caption_tail(steps, known, frag):
    """000709's statement of changes in equity opens two pages this way. `- - - -` normalises
    AWAY, so without the printable-text condition the join "completes" a caption that was already
    complete — and 二、本年期初余额 carries nine component figures."""
    rows = _rows(("河钢股份有限公司2024 年年度报告全文", ()), (frag, ()))
    assert _ask(rows, "二、本年期初余额", steps, known) is None
    assert _ask(rows, "1．提取盈余公积", steps, known) is None


def test_a_complete_caption_opening_a_page_is_not_a_tail(steps, known):
    """The ordinary case — a page simply continues a statement — and by far the commonest."""
    rows = _rows(("河钢股份有限公司2024 年年度报告全文", ()), ("应收账款", ()))
    assert _ask(rows, "货币资金", steps, known) is None


def test_a_first_row_that_carries_figures_is_not_a_tail(steps, known):
    """A tail has no figures of its own: its figures are on the page before, beside its head."""
    rows = _rows(("澜起科技股份有限公司2024 年年度报告", ()),
                 ("股东权益）总计", ("12,218,911,386.38",)))
    assert _ask(rows, "负债和所有者权益（或", steps, known) is None


def test_a_join_the_vocabulary_does_not_know_is_refused(steps, known):
    rows = _rows(("澜起科技股份有限公司2024 年年度报告", ()), ("股东权益）总计", ()))
    assert _ask(rows, "货币资金", steps, known) is None
    assert _ask(rows, "", steps, known) is None


def test_only_the_pages_first_body_row_is_asked(steps, known):
    """Nothing may be stepped over to reach the fragment, or a caption three rows down would be
    spliced onto a head it has nothing to do with. Chrome is skipped; a body row is not."""
    rows = _rows(("澜起科技股份有限公司2024 年年度报告", ()),
                 ("流动资产：", ()),
                 ("股东权益）总计", ()))
    assert _ask(rows, "负债和所有者权益（或", steps, known) is None


# ── the alias that doubled a published total ──────────────────────────────────────────────────

def test_the_oci_total_does_not_claim_the_parents_share_of_it():
    """A CAS income statement prints 六、其他综合收益的税后净额 AND
    归属母公司所有者的其他综合收益的税后净额. With both aliased to the total, `concept_value` summed
    them: 000709 published -75,833,856.66 for a printed -37,916,928.33 and 688008 published
    132,682,798.88 for 66,341,399.44 — each exactly double."""
    seed = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    item = next(i for i in seed["items"]
                if i.get("key") == "is_oci__total_other_comprehensive_income")
    every = list(item.get("aliases") or [])
    for group in (item.get("aliases_i18n") or {}).values():
        every += list(group or [])
    folded = {normalize_label(a) for a in every}
    assert normalize_label("其他综合收益的税后净额") in folded, "the total's own caption must stay"
    for attribution in ("归属母公司所有者的其他综合收益的税后净额",
                        "归属于少数股东的其他综合收益的税后净额"):
        assert normalize_label(attribution) not in folded, attribution
