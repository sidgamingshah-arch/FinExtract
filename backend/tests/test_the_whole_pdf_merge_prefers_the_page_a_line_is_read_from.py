"""WHEN TWO PARTS READ ONE SLOT: the merge prefers the page the line is read from, and a dispute it
cannot settle goes to review with both figures (`services.whole_pdf.merge`, `reads_on_face`;
`stages.whole_pdf._rows`).

The merge ranked (confirmed, on a statement page) for every line, so a note part answered off the
face in part 1 — Sales' revenue-note part given the face's 营业收入 — beat the revenue note's
主营业务收入 read in part 2 (finding parts_and_merge_prefer_face_for_note_parts). A [read from the
statement] line answered off a note page was accepted as a statement line (finding
face_line_from_note_page_accepted). And on a tie the first part's figure stood: 0857 prints a CAS
set and an IFRS set that landed in different parts (finding whole_pdf_0857_two_gaap_sets_split_by_
part; its Level 3 totals are 9,185 CAS and 9,178 IFRS).

The readings below are built by hand; no model is called.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import PageKind
from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
NOTE_PART = "sub__revenue_note_principal_revenue"      # [read from the notes]
FACE_PART = "sub__face_principal_revenue"              # [read from the statement]
FACE_LINE = "bs_nca__land"                             # [read from the statement]
LEVEL_3 = "sub__fa_cp_level_3_printed_total"           # [read from the notes]
FACE_P, NOTE_P = 3, 40                                 # 0-based pages of each kind


@pytest.fixture(scope="module")
def hk():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def items(hk):
    return {item.key: item for item in hk.items}


def _r(key, amount, page, part, status="on_page", caption=""):
    return W.Reading(key=key, basis="consolidated", period="current", amount=D(amount),
                     caption=caption, period_end="", part=part,
                     check=W.Check(status, page), on_face=page == FACE_P)


def _one(merged, key):
    return merged[(key, "consolidated", "current")]


def test_the_page_each_line_is_read_from(items):
    assert W.reads_on_face(items[NOTE_PART]) is False
    assert W.reads_on_face(items[FACE_PART]) is True
    assert W.reads_on_face(items[FACE_LINE]) is True
    assert W.reads_on_face(items["bs_ca__total_current_assets"]) is True    # a statement's own
    assert W.reads_on_face(items["notes__contingent_liabilities"]) is None


def test_a_line_read_from_the_notes_prefers_a_note_page(items):
    face = _r(NOTE_PART, "3638911068.29", FACE_P, 0, caption="营业收入")
    note = _r(NOTE_PART, "3628769555.93", NOTE_P, 1, caption="主营业务")
    kept = _one(W.merge([face, note], items), NOTE_PART)
    assert kept.amount == D("3628769555.93") and kept.others == ["3638911068.29@p4"]
    assert not kept.review and not kept.flags                     # the rule chose
    # Without the line's route, a statement page wins, as before.
    assert _one(W.merge([_r(NOTE_PART, "1", FACE_P, 0), _r(NOTE_PART, "2", NOTE_P, 1)]),
                NOTE_PART).amount == 1


def test_a_statement_line_prefers_a_statement_page(items):
    note = _r(FACE_LINE, "700", NOTE_P, 0)
    face = _r(FACE_LINE, "900", FACE_P, 1)
    kept = _one(W.merge([note, face], items), FACE_LINE)
    assert kept.amount == 900 and kept.on_face and not kept.review


def test_a_confirmed_reading_still_beats_an_unconfirmed_one(items):
    face = _r(NOTE_PART, "100", FACE_P, 0)
    note = _r(NOTE_PART, "200", NOTE_P, 1, status="not_found")
    assert _one(W.merge([face, note], items), NOTE_PART).amount == 100


def test_a_dispute_the_rule_cannot_settle_goes_to_review_with_both_figures(items):
    """0857's Level 3 total: 9,185 in the CAS note (part 1), 9,178 in the IFRS note (part 2), both
    printed where cited, both on note pages. Part 1's figure is kept — and the slot is sent to
    review naming both figures and their parts, never kept silently."""
    cas = _r(LEVEL_3, "9185", 209, 0)
    ifrs = _r(LEVEL_3, "9178", 254, 1)
    kept = _one(W.merge([cas, ifrs], items), LEVEL_3)
    assert kept.amount == 9185 and kept.review
    assert kept.flags == [f"{W.PARTS_DISAGREE}:9185@p210(part 1):9178@p255(part 2)"]
    # The same figure in both parts is no dispute.
    same = _one(W.merge([_r(LEVEL_3, "9185", 209, 0), _r(LEVEL_3, "9185", 209, 1)], items),
                LEVEL_3)
    assert not same.review and not same.flags and not same.others


def test_a_statement_line_kept_off_a_note_page_goes_to_review(items):
    kept = _one(W.merge([_r(FACE_LINE, "700", NOTE_P, 1)], items), FACE_LINE)
    assert kept.review and W.FACE_LINE_OFF_FACE in kept.flags
    # A part read from the statement too; a line read anywhere is not.
    assert W.FACE_LINE_OFF_FACE in _one(W.merge([_r(FACE_PART, "5", NOTE_P, 1)], items),
                                        FACE_PART).flags
    anywhere = _one(W.merge([_r("bs_ca__total_current_assets", "5", NOTE_P, 1)], items),
                    "bs_ca__total_current_assets")
    assert not anywhere.flags and not anywhere.review
    # Where the classifier found no face page of the line's statement, every page it could be
    # read off is another kind: that is not flagged, or a whole statement would go to review.
    unclassified = _one(W.merge([_r(FACE_LINE, "700", NOTE_P, 1)], items,
                                faces={"profit_and_loss"}), FACE_LINE)
    assert not unclassified.review and not unclassified.flags
    assert _one(W.merge([_r(FACE_LINE, "700", NOTE_P, 1)], items, faces={"balance_sheet"}),
                FACE_LINE).review


def test_the_row_carries_the_dispute_and_goes_to_review(hk, items):
    from app.stages.whole_pdf import FLAG, REVIEW_FLAG, _rows

    doc = DocumentModel(filename="x.pdf")
    doc.pages = [PageSource(index=i, kind=PageKind.FACE if i == FACE_P else PageKind.NOTES,
                            statement="balance_sheet" if i == FACE_P else None)
                 for i in range(260)]
    merged = W.merge([_r(LEVEL_3, "9185", 209, 0), _r(LEVEL_3, "9178", 254, 1),
                      _r(FACE_LINE, "700", NOTE_P, 1)], items)
    rows = {row.canonical_key: row for row in _rows(doc, hk, merged, "scripted")}
    level3 = rows[LEVEL_3]
    assert REVIEW_FLAG in level3.confidence.flags
    flags = next(iter(level3.values.values())).confidence.flags
    assert f"{FLAG}:{W.PARTS_DISAGREE}:9185@p210(part 1):9178@p255(part 2)" in flags
    assert level3.confidence.mapping < 0.8                         # below auto-accept
    land = rows[FACE_LINE]
    assert REVIEW_FLAG in land.confidence.flags
    assert f"{FLAG}:{W.FACE_LINE_OFF_FACE}" in next(iter(land.values.values())).confidence.flags
