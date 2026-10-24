"""A SHORT-TERM BORROWINGS ROW THAT TAKES IN THE CURRENT PORTION IS SPLIT BY ITS NOTE.

PetroChina (hkex_0857) prints one face row, 'Short-term borrowings' 138,783 / 148,780 (RMB million),
and its note 29 splits it:

    Short-term borrowings excluding current portion of long-term borrowings    45,955    38,979
    Current portion of long-term borrowings                                     92,828   109,801
                                                                               138,783   148,780

ST Bank Loans Payable is the first row by its definition, and the current portion belongs to CPLTD
Bank. The face row bound the line by its caption and stood: a printed figure stands over a cascade
of an extracted line, so the line published 138,783 / 148,780 against a key of 45,955 / 38,979.

Two note parts read the two printed rows, and each line's NOTE_SPLIT rung takes `first` over BOTH
as `required` terms — so neither line moves unless the note prints the whole split, and the section
total cannot move. ST Bank's rung declares `outranks_printed`, because the face row states more than
the line; CPLTD Bank's does not, so a face row of its own always stands. Where no note prints the
split, the face row is the line: every CAS 短期借款, which is the line as printed.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.periods import concept_value
from app.stages.note_sourced import NoteSourcedStage

REPO = pathlib.Path(__file__).resolve().parents[2]
SEED = REPO / "backend" / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"

ST_BANK = "bs_cl__st_bank_loans_payable"
CPLTD = "bs_cl__cpltd_bank"
EXCL = "sub__st_borrowings_excl_current_portion"
CP = "sub__st_borrowings_current_portion_of_ltd"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _items(shipped):
    return {i.key: i for i in shipped.items}


# ── the configuration ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("part,parent", [(EXCL, ST_BANK), (CP, CPLTD)])
def test_each_part_is_an_internal_note_part_wired_into_its_parents_cascade(shipped, part, parent):
    items = _items(shipped)
    p = items[part]
    assert p.namespace == "internal" and p.in_output is False
    assert p.parent == parent
    assert str(getattr(p.statement, "value", p.statement)) == "notes"
    assert list(p.section_scope or []) == []
    assert p.route == "note_tables"
    # No face alias: no caption on any statement can bind a note part.
    assert not p.aliases and not any((p.aliases_i18n or {}).values())
    refs = {t.ref for rung in items[parent].cascade for t in rung.terms}
    assert part in refs, refs


def test_both_rungs_need_both_printed_rows_and_read_their_own(shipped):
    items = _items(shipped)
    st, cp = items[ST_BANK].cascade, items[CPLTD].cascade
    assert [r.id for r in st] == ["NOTE_SPLIT"] and [r.id for r in cp] == ["NOTE_SPLIT"]
    for rung, own in ((st[0], EXCL), (cp[0], CP)):
        assert rung.terms_op == "first"
        assert [t.ref for t in rung.terms][0] == own
        assert {t.ref for t in rung.terms} == {EXCL, CP}
        assert all(t.role == "required" and t.sign == 1 for t in rung.terms)
        # A printed row each, never a printed total less a part.
        assert not any(t.sign < 0 or t.role == "adjustment" for t in rung.terms)
    assert st[0].outranks_printed is True
    assert cp[0].outranks_printed is False
    assert not cp[0].carved_from_face


def test_both_lines_stay_face_lines(shipped):
    """Neither became `derived`: their aliases still bind the printed rows, CAS 短期借款 included."""
    items = _items(shipped)
    assert str(items[ST_BANK].type) == "extracted" and str(items[CPLTD].type) == "extracted"
    assert "短期借款" in (items[ST_BANK].aliases_i18n or {}).get("zh", [])


# ── the stage, on PetroChina's note 29 ─────────────────────────────────────────────────────────

def _value(amount: str, period: str, page: int):
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period, value=Decimal(amount),
                          value_raw=Decimal(amount), provenance=Provenance(page_index=page))


def _note(number: str, title: str, rows, page: int = 279) -> NotesTable:
    items = []
    for caption, (current, prior) in rows:
        item = NoteItem(raw_label=caption)
        item.values["c"] = _value(current, "current", page)
        item.values["p"] = _value(prior, "prior", page)
        items.append(item)
    return NotesTable(note_number=number, title=title, items=items, source_pages=[page],
                      source_text=title)


NOTE_29 = [
    ("Short-term borrowings excluding current portion of long-term borrowings", ("45955", "38979")),
    ("Current portion of long-term borrowings", ("92828", "109801")),
    ("Total", ("138783", "148780")),
    ("Long-term borrowings", ("98072", "143198")),
]


def _face(key: str, caption: str, current: str, prior: str, note: str = "29") -> LineItem:
    row = LineItem(source_label=caption, canonical_key=key, note_number=note)
    row.values["c"] = _value(current, "current", 227)
    row.values["p"] = _value(prior, "prior", 227)
    return row


def _run(shipped, face_rows, notes) -> DocumentModel:
    doc = DocumentModel(filename="0857.pdf")
    doc.notes = notes
    doc.line_items = list(face_rows)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx)


def _figure(doc, key, period="current"):
    rows = []
    for li in doc.line_items:
        if li.canonical_key != key:
            continue
        row = li.model_dump(mode="json")
        row["values"] = list((row.get("values") or {}).values())
        rows.append(row)
    got = concept_value(rows, "consolidated", period)
    return None if got is None else Decimal(str(got)).quantize(Decimal("1"))


def test_the_face_row_is_split_into_the_bank_part_and_the_current_portion(shipped):
    face = _face(ST_BANK, "Short-term borrowings", "138783", "148780")
    doc = _run(shipped, [face], [_note("29", "BORROWINGS", NOTE_29)])
    assert _figure(doc, ST_BANK) == Decimal("45955")
    assert _figure(doc, ST_BANK, "prior") == Decimal("38979")
    assert _figure(doc, CPLTD) == Decimal("92828")
    assert _figure(doc, CPLTD, "prior") == Decimal("109801")
    # Together they are the printed face row, so the section's total does not move.
    assert _figure(doc, ST_BANK) + _figure(doc, CPLTD) == Decimal("138783")
    assert _figure(doc, ST_BANK, "prior") + _figure(doc, CPLTD, "prior") == Decimal("148780")
    # The printed figure stays on its row, auditable.
    assert face.values["c"].value_raw == Decimal("138783")
    assert any(f.startswith("cascade_over_printed:") for f in face.confidence.flags)


def test_a_note_that_prints_only_one_half_moves_nothing(shipped):
    """`first` over two REQUIRED terms: the bank part alone is not the split."""
    face = _face(ST_BANK, "Short-term borrowings", "138783", "148780")
    doc = _run(shipped, [face], [_note("29", "BORROWINGS", [NOTE_29[0], NOTE_29[3]])])
    assert _figure(doc, ST_BANK) == Decimal("138783")
    assert _figure(doc, CPLTD) is None


def test_a_short_term_borrowings_note_with_no_split_leaves_the_face_row(shipped):
    """A CAS 短期借款 note itemises by kind of loan: the line is the face row as printed."""
    face = _face(ST_BANK, "短期借款", "879408568.50", "1070193573.26", note="七、32")
    notes = [_note("七、32", "短期借款", [
        ("信用借款", ("500000000.00", "600000000.00")),
        ("保证借款", ("379408568.50", "470193573.26")),
        ("合计", ("879408568.50", "1070193573.26")),
    ], 200)]
    doc = _run(shipped, [face], notes)
    assert _figure(doc, ST_BANK) == Decimal("879408568")
    assert _figure(doc, CPLTD) is None


def test_a_printed_cpltd_row_of_its_own_stands(shipped):
    """CPLTD Bank's rung fills only a column its face leaves empty."""
    st = _face(ST_BANK, "Short-term borrowings", "138783", "148780")
    cp = _face(CPLTD, "Current portion of long-term bank loans", "90000", "100000")
    doc = _run(shipped, [st, cp], [_note("29", "BORROWINGS", NOTE_29)])
    assert _figure(doc, CPLTD) == Decimal("90000")
