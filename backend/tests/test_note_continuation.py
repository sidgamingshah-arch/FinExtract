"""A note printed across pages, with its heading repeated, is ONE note.

Extraction builds a ``NotesTable`` per (heading occurrence, page), because that is the unit the
arithmetic needs: a tax note prints its components in one table and the effective-rate
RECONCILIATION in another, and a decomposition that pooled them would count a restated component
twice. For READING, the note number is the unit instead — an analyst asked for note 22 and a page
break is not part of its meaning. So the two live at different layers, and this module pins the
reading layer.

WHAT IT COST TO GET THIS WRONG, measured on a 270-page bilingual HKEX filing: 63 tables for 28
notes, note 38 spread over seven of them, notes 15 and 20 over five, note 6 over four. The index
was a dict comprehension keyed by the note number, so those did not merge — they OVERWROTE, and
the last one won. Every symptom followed from that single line:

* every title in the list read "(Continued)", because the last table is a continuation;
* a note's detail pane showed the last fragment's rows and called it the note — note 38's 54 rows
  were served as 3;
* the page served was the LAST fragment's, so click-to-source jumped to a continuation page while
  the note's own heading and table sat on an earlier one.

The second half of the module is the heading detector, because the index is keyed by note number
and a sentence fragment that claims a number a real note already has collides with it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.routes.documents import _note_index
from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


# --- the index: one entry per note, assembled ----------------------------------------------------

def _table(no: str, title: str, page: int, labels: list[str]) -> dict:
    """A note table as ``_serialize_notes`` emits it — ``page`` already 1-based."""
    return {"no": no, "title": title, "page": page,
            "rows": [{"label": lab, "role": "line", "confidence": 1.0, "values": []}
                     for lab in labels]}


def test_a_notes_tables_are_assembled_rather_than_overwriting_one_another():
    """The defect, at the unit. Three tables under one number are one note of six rows."""
    index = _note_index([
        _table("38", "BUSINESS COMBINATION 38. 業務合併", 239, ["Cash", "Receivables"]),
        _table("38", "BUSINESS COMBINATION (Continued) 38. 業務合併（續）", 240, ["Payables"]),
        _table("38", "BUSINESS COMBINATION (Continued) 38. 業務合併（續）", 241,
               ["Goodwill", "Consideration", "Net outflow"]),
    ])

    assert list(index) == ["38"]
    note = index["38"]
    assert [r["label"] for r in note["rows"]] == [
        "Cash", "Receivables", "Payables", "Goodwill", "Consideration", "Net outflow"]


def test_the_note_starts_where_it_starts_and_says_how_far_it_runs():
    """``page`` is the note's own first page — what click-to-source must land on — and ``pages``
    is every page it spans, so a reader sent to the first one knows the rest is overleaf."""
    index = _note_index([
        _table("15", "INVESTMENT PROPERTIES 15. 投資物業", 196, ["At 1 January"]),
        _table("15", "INVESTMENT PROPERTIES (Continued) 15. 投資物業（續）", 197, ["Additions"]),
        _table("15", "INVESTMENT PROPERTIES (Continued) 15. 投資物業（續）", 199, ["At 31 December"]),
    ])

    assert index["15"]["page"] == 196
    assert index["15"]["pages"] == [196, 197, 199]


def test_the_lowest_page_wins_whatever_order_the_tables_arrive_in():
    """Not the first table SEEN. Tables arrive in page order today; a note's start must not depend
    on that staying true, because nothing downstream would notice if it changed."""
    index = _note_index([
        _table("20", "INVESTMENTS IN JOINT VENTURES (Continued) 20. （續）", 210, ["b"]),
        _table("20", "INVESTMENTS IN JOINT VENTURES 20. 於合營公司的投資", 207, ["a"]),
    ])

    assert index["20"]["page"] == 207
    assert index["20"]["pages"] == [207, 210]


def test_a_continuation_heading_does_not_get_to_name_the_note():
    """A continuation title names the note no worse, but it names it less well — and it was the
    one being shown, for every multi-page note in the filing."""
    for continued in ("REVENUE (Continued) 6. 收益（續）", "REVENUE 6. 收益（续）",
                      "REVENUE (cont.) 6. 收益"):
        index = _note_index([
            _table("6", continued, 181, ["b"]),
            _table("6", "REVENUE, OTHER INCOME AND GAINS 6. 收益、其他收入及收益", 180, ["a"]),
        ])
        assert index["6"]["title"] == "REVENUE, OTHER INCOME AND GAINS 6. 收益、其他收入及收益", \
            continued


def test_a_note_printed_once_is_unchanged():
    """The single-table case has to come through untouched, including its page span."""
    index = _note_index([_table("7", "FINANCE COSTS 7. 財務費用", 184, ["Interest"])])

    assert index["7"]["page"] == 184
    assert index["7"]["pages"] == [184]
    assert [r["label"] for r in index["7"]["rows"]] == ["Interest"]


# --- the heading detector -------------------------------------------------------------------------
#
# The three refusals below are the three shapes a real filing actually produced. Each one had taken
# a note number that a real note also has, so the index either lost the real note's table to the
# fragment or the fragment's to the real note.

@pytest.mark.parametrize("text,why", [
    ("8,461,842,000元） （附註30(b)）。", "the number is the head of a longer numeral"),
    ("17. 內的合同，因此該等修訂對本集", "a Chinese sentence, not a title"),
    ("note 25 to the financial statements, the Group had the 及結餘外，於年內，本集團與關",
     "the middle of an English sentence"),
    ("28. 36% (2024: 28.36%) in Porchester", "a percentage split across its decimal point"),
    ("14. ;", "punctuation names nothing"),
])
def test_prose_is_not_a_note_heading(text, why):
    from app.core.models.geometry import BBox
    from app.services.notes_extract import _is_heading
    from app.services.row_reconstruct import Word

    row = [Word(text=tok, bbox=BBox(x0=0.1 + i * 0.05, y0=0.2, x1=0.14 + i * 0.05, y1=0.21))
           for i, tok in enumerate(text.split(" "))]
    assert _is_heading(row) is None, why


@pytest.mark.parametrize("text,no", [
    # The bilingual form an HKEX filing prints: number, English title, number again, Chinese title.
    ("6. REVENUE, OTHER INCOME AND GAINS 6. 收益、其他收入及收益", "6"),
    ("11. INCOME TAX 11. 所得稅", "11"),
    # The enumeration comma 、 belongs in a title and must not be mistaken for sentence punctuation.
    ("27. CASH AND CASH EQUIVALENTS, 27. 現金及現金等價物、受限制現金", "27"),
    ("Note 15: Trade receivables", "15"),
    ("15. Trade receivables", "15"),
    # A Chinese-only heading, which is the case the sentence-punctuation guard must not catch.
    ("17. 無形資產", "17"),
])
def test_a_real_heading_still_reads_as_one(text, no):
    from app.core.models.geometry import BBox
    from app.services.notes_extract import _is_heading
    from app.services.row_reconstruct import Word

    row = [Word(text=tok, bbox=BBox(x0=0.1 + i * 0.05, y0=0.2, x1=0.14 + i * 0.05, y1=0.21))
           for i, tok in enumerate(text.split(" "))]
    got = _is_heading(row)
    assert got is not None, text
    assert got[0] == no, got


# --- end to end -----------------------------------------------------------------------------------

def test_a_two_page_note_is_served_as_one_note_from_the_pipeline():
    """Through the real reader: a note whose heading is reprinted on the next page comes back as one
    note carrying both pages' rows, named by its opening heading."""
    from app.api.routes.extractions import _serialize_notes
    from tests.fixtures.generate import make_hkex_tax_note_pdf

    ontology = load_ontology(
        json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text()), resolve=True)
    template = load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text()))
    ctx = PipelineContext(raw_bytes=make_hkex_tax_note_pdf())
    ctx.ontology, ctx.template = ontology, template
    doc = default_pipeline().run(DocumentModel(filename="f.pdf", fmt=DocFormat.PDF), ctx)

    # The fixture prints note 11 on two pages, the second headed "(Continued)".
    tables = [t for t in doc.notes if str(t.note_number) == "11"]
    assert len(tables) == 2, [t.title for t in tables]

    index = _note_index(_serialize_notes(doc))
    assert "11" in index
    note = index["11"]
    assert "Continued" not in note["title"], note["title"]
    assert note["pages"] == [2, 3], note["pages"]
    assert note["page"] == 2
    # Both tables' rows, not just the continuation's.
    labels = [r["label"] for r in note["rows"]]
    assert any(lab.startswith("PRC corporate income tax") for lab in labels), labels
    assert any(lab.startswith("At the statutory rate") for lab in labels), labels
