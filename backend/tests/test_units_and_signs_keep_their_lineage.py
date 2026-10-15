"""Units and signs keep their lineage (CR-07).

FIVE THINGS, each a way a figure used to change without saying so.

  * A NOTE ROW IN ANOTHER SCALE. `stages.note_sourced` stamped every note figure with the face's
    unit and never looked at the unit the note's own page declared, so a note in RMB millions under
    a face in RMB'000 was published a thousand times too small. Both declared and different: the row
    is converted to the face's scale and the line says `unit_rescaled:<from>:<to>`; the working
    shows the printed figure and the two scales. A note in another CURRENCY is not converted at all:
    the slot is not written and the line says `unit_currency_mismatch:<note>:<face>`. A unit the
    note's page did not declare is read as before.
  * THE NOTE-TO-FACE TIE compares like with like: note rows in another scale are converted first,
    and a note in another currency is not graded against the face figure.
  * `_LESS` negated "Less than one year", "Unless:" and "Doubtless:".
  * The label and template sign rules changed figures and named themselves nowhere; each now flags
    `sign_rule:<rule>` and `sign_normalised` on the value and the row.
  * `value_raw` stays the printed figure. Every writer replacing a printed figure keeps it there; a
    slot with no printed figure behind it is `derived`, and its `value_raw` follows its figure.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis, LineRole, PrintedIn
from app.core.models.line_item import (
    ExtractedValue, FaceNoteLink, LineItem, NoteItem, NotesTable, Provenance, UnitContext,
)
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import derivation
from app.services.units import compare
from app.stages.normalize import _LESS, NormalizeStage
from app.stages.note_sourced import NoteSourcedStage, _refigure, _write
from app.stages.reconcile import ReconcileStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
LINE = "sub__expense_notes_depreciation"

THOUSANDS = UnitContext(currency="CNY", scale_factor=Decimal(1000), units_label="thousand",
                        source_bbox_page=3)
MILLIONS = UnitContext(currency="CNY", scale_factor=Decimal(1_000_000), units_label="million",
                       source_bbox_page=42)
USD_THOUSANDS = UnitContext(currency="USD", scale_factor=Decimal(1000), units_label="thousand",
                            source_bbox_page=42)


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _fact(amount: str, unit: UnitContext | None = None, period: str = "current",
          page: int = 42) -> ExtractedValue:
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period, value=Decimal(amount),
                          value_raw=Decimal(amount), provenance=Provenance(page_index=page),
                          **({"unit_ctx": unit} if unit is not None else {}))


def _face(unit: UnitContext | None = THOUSANDS) -> LineItem:
    """A face row in the statement's unit — what `_statement_unit` reads the face's unit from."""
    row = LineItem(source_label="Revenue", canonical_key="is_pl__revenue",
                   printed_in=PrintedIn.FACE)
    row.values["k"] = _fact("98000", unit, page=3)
    return row


def _admin_note(amount: str, unit: UnitContext | None) -> NotesTable:
    item = NoteItem(raw_label="Depreciation")
    item.values["k"] = _fact(amount, unit)
    return NotesTable(note_number="6", title="Administrative expenses", items=[item])


def _run(shipped, notes, rows):
    doc = DocumentModel(filename="f.pdf")
    doc.notes = list(notes)
    doc.line_items = list(rows)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx), ctx


def _row(doc, key: str) -> LineItem | None:
    return next((li for li in doc.line_items if li.canonical_key == key), None)


def _slot(row: LineItem | None, period: str = "current") -> ExtractedValue | None:
    if row is None:
        return None
    return next((ev for ev in row.values.values()
                 if str(ev.period_label or "") == period and ev.value is not None), None)


# ── E2.1: a note figure in the face's unit ─────────────────────────────────────────────────────

def test_a_note_in_millions_under_a_face_in_thousands_is_published_in_thousands(shipped):
    doc, ctx = _run(shipped, [_admin_note("1.3", MILLIONS)], [_face()])

    row = _row(doc, LINE)
    slot = _slot(row)
    assert slot.value == Decimal("1300"), "the millions figure was published as thousands"
    assert slot.unit_ctx.scale_factor == Decimal(1000)
    assert "unit_rescaled:1000000:1000" in row.confidence.flags
    assert any("printed 1.3 at scale 1000000" in line for line in ctx.logs), ctx.logs


def test_the_working_shows_the_printed_figure_and_both_scales(shipped):
    doc, _ctx = _run(shipped, [_admin_note("1.3", MILLIONS)], [_face()])

    row = _row(doc, LINE)
    trail = row.derivation["consolidated:current"]
    (term,) = trail["inputs"]
    assert (term["value"], term["used"], term["printed"]) == ("1300.0", "1300.0", "1.3")
    assert term["rescaled"] == {"from": "1000000", "to": "1000"}

    work = derivation.working(row.derivation, "consolidated", "current",
                              shown=_slot(row).value)
    assert work["how"] == "adds_up"
    (served,) = work["terms"]
    assert served["why"] == {"code": "rescaled", "amount": "1.3", "from": "1000000",
                             "to": "1000"}
    text = derivation.why_text(served["why"], str, "en")
    assert text == ("printed as 1.3 in units of 1,000,000; converted to the statement's units "
                    "of 1,000")
    for locale in ("zh", "ar", "fr"):
        said = derivation.why_text(served["why"], str, locale)
        assert said != text and "1,000,000" in said and "1.3" in said, (locale, said)


def test_a_note_in_another_currency_is_not_published(shipped):
    doc, ctx = _run(shipped, [_admin_note("1.3", USD_THOUSANDS)], [_face()])

    row = _row(doc, LINE)
    assert _slot(row) is None, "a USD figure was published under a CNY statement"
    assert "unit_currency_mismatch:USD:CNY" in row.confidence.flags
    assert any("is printed in USD, the statement in CNY" in line for line in ctx.logs), ctx.logs
    # Not the basis refusal: that log line is counted as one.
    assert not any(line.startswith("note_sourced:basis_refused=") for line in ctx.logs)


@pytest.mark.parametrize("note_unit, face_unit", [
    (None, THOUSANDS),             # the note's page declared nothing
    (MILLIONS, None),              # the face declared nothing
    (UnitContext(currency="CNY"), THOUSANDS),   # a currency read, no scale
])
def test_a_unit_either_side_did_not_declare_is_read_as_before(shipped, note_unit, face_unit):
    doc, _ctx = _run(shipped, [_admin_note("1.3", note_unit)], [_face(face_unit)])

    row = _row(doc, LINE)
    assert _slot(row).value == Decimal("1.3")
    assert not [f for f in row.confidence.flags if f.startswith("unit_")]


def test_compare_names_scales_without_an_exponent():
    check = compare(MILLIONS, THOUSANDS)
    assert check.factor == Decimal(1000) and check.rescaled == ("1000000", "1000")
    assert compare(THOUSANDS, MILLIONS).factor == Decimal("0.001")
    assert compare(THOUSANDS, THOUSANDS).rescaled is None
    assert compare(USD_THOUSANDS, MILLIONS).currency_mismatch == ("USD", "CNY")


# ── E2.2: the note-to-face tie compares like with like ─────────────────────────────────────────

def _tie(note_unit: UnitContext) -> tuple[DocumentModel, PipelineContext, LineItem]:
    face = LineItem(source_label="Trade receivables", canonical_key="bs_ca__trade_receivables",
                    printed_in=PrintedIn.FACE)
    face.values["k"] = _fact("5200", THOUSANDS, page=3)
    note = NotesTable(note_number="14", title="Trade receivables")
    for caption, amount in (("Third parties", "3.0"), ("Related parties", "2.2")):
        item = NoteItem(raw_label=caption, role=LineRole.LINE)
        item.values["k"] = _fact(amount, note_unit)
        note.items.append(item)
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [face]
    doc.notes = [note]
    doc.links = [FaceNoteLink(face_item_id=face.id, notes_table_id=note.id, note_number="14")]
    ctx = PipelineContext(settings=get_settings())
    return ReconcileStage().run(doc, ctx), ctx, face


def test_a_note_in_millions_ties_to_a_face_in_thousands():
    doc, ctx, _face_row = _tie(MILLIONS)

    (entry,) = doc.reconciliation.entries
    assert entry.tie_status == "tied", entry
    assert entry.residual == 0
    assert any(line.startswith("reconcile:note_rows_rescaled=2") for line in ctx.logs), ctx.logs


def test_a_note_in_another_currency_is_not_graded_against_the_face():
    doc, ctx, face = _tie(USD_THOUSANDS)

    assert doc.reconciliation.entries == []
    assert "unit_currency_mismatch:USD:CNY" in face.confidence.flags
    assert any(line.startswith("reconcile:unit_currency_mismatch note 14") for line in ctx.logs)


# ── E2.3: what "Less" means ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("caption", ["Less than one year", "less than 3 months", "Unless:",
                                     "Doubtless: the amount is recoverable"])
def test_a_caption_that_is_not_a_deduction_is_not_negated(caption):
    assert not _LESS.search(caption)
    doc = DocumentModel(filename="f.pdf")
    row = LineItem(source_label=caption, canonical_key="k")
    row.values["k"] = _fact("500")
    doc.line_items = [row]
    NormalizeStage().run(doc, PipelineContext(raw_bytes=b""))

    ev = next(iter(row.values.values()))
    assert ev.value == Decimal(500) and not ev.sign_normalised
    assert not [f for f in row.confidence.flags if f.startswith("sign_rule:")]


@pytest.mark.parametrize("caption", ["Less: impairment", "Less provision for doubtful debts",
                                     "Deduct: tax", "Gross amount less: impairment"])
def test_a_deduction_is_still_one(caption):
    assert _LESS.search(caption)


# ── E2.4: every sign rule that changes a figure names itself ────────────────────────────────────

def _normalise(caption: str, amount: str, *, key: str = "k", ontology=None, template=None):
    doc = DocumentModel(filename="f.pdf")
    row = LineItem(source_label=caption, canonical_key=key)
    row.values["k"] = _fact(amount)
    doc.line_items = [row]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = ontology
    ctx.template = template
    NormalizeStage().run(doc, ctx)
    return row, next(iter(row.values.values()))


def test_less_and_add_name_themselves():
    for caption, amount, rule, figure in (("Less: impairment", "500", "less", -500),
                                          ("Add: exchange gain", "-30", "add", 30)):
        row, ev = _normalise(caption, amount)
        assert ev.value == Decimal(figure) and ev.value_raw == Decimal(amount)
        assert ev.sign_normalised is True
        for flags in (ev.confidence.flags, row.confidence.flags):
            assert f"sign_rule:{rule}" in flags and "sign_normalised" in flags, flags


def test_a_flip_names_itself():
    ontology = SimpleNamespace(
        mappings=[SimpleNamespace(canonical_key="k", sign_rule=SimpleNamespace(
            flip_if_label_matches=["減值"]), sign_convention=None, temporality=None,
            unit_of_account=None, statement=None)],
        global_rules=SimpleNamespace(sign_convention={}))
    row, ev = _normalise("減值虧損", "80", ontology=ontology)
    assert ev.value == Decimal(-80)
    assert "sign_rule:flip" in row.confidence.flags and "sign_normalised" in row.confidence.flags


def test_a_natural_negative_line_names_itself():
    template = {"statements": [{"sections": [
        {"canonical_key": "bs_equity__treasury_shares", "sign": "natural_negative"}]}]}
    row, ev = _normalise("Treasury shares", "427", key="bs_equity__treasury_shares",
                         template=template)
    assert ev.value == Decimal(-427) and ev.value_raw == Decimal(427)
    assert "sign_rule:natural_negative" in ev.confidence.flags
    assert "sign_normalised" in row.confidence.flags


def test_a_rule_that_changes_nothing_records_nothing():
    row, ev = _normalise("Less: impairment", "-500")
    assert ev.value == Decimal(-500) and not ev.sign_normalised
    assert not row.confidence.flags


# ── E2.5: value_raw stays the printed figure ───────────────────────────────────────────────────

def _printed(amount: str) -> LineItem:
    row = LineItem(source_label="Other income", canonical_key="k", printed_in=PrintedIn.FACE)
    row.values["k"] = _fact(amount, page=3)
    return row


def test_a_note_figure_written_over_a_printed_one_keeps_it_in_value_raw():
    row = _printed("100")
    _write(row, "consolidated", "current", Decimal("555"), by="note_source")

    (slot,) = row.values.values()
    assert (slot.value, slot.value_raw, slot.derived) == (Decimal("555"), Decimal("100"), False)
    assert "note_source_displaced_printed:100" in row.confidence.flags


def test_a_derived_slot_moves_value_raw_with_its_figure():
    row = LineItem(source_label="x", canonical_key="k")
    _write(row, "consolidated", "current", Decimal("288.01"), by="note_source")
    _write(row, "consolidated", "current", Decimal("139.53"), by="line_item_llm")

    (slot,) = row.values.values()
    assert (slot.value, slot.value_raw, slot.derived) == (Decimal("139.53"), Decimal("139.53"),
                                                          True)
    # What it replaced was computed, not printed, and the flag says which.
    assert "line_item_llm_displaced_derived:288.01" in row.confidence.flags
    assert not [f for f in row.confidence.flags if "_displaced_printed:" in f]


def test_a_settlement_on_a_derived_slot_moves_value_raw_and_on_a_printed_one_does_not():
    printed = _fact("1000")
    _refigure(printed, Decimal("700"))
    assert (printed.value, printed.value_raw) == (Decimal("700"), Decimal("1000"))

    derived = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                             value=Decimal("1000"), value_raw=Decimal("1000"), derived=True)
    _refigure(derived, Decimal("700"))
    assert (derived.value, derived.value_raw) == (Decimal("700"), Decimal("700"))


def test_a_sentence_written_over_a_derived_slot_moves_value_raw():
    from app.stages.line_item_llm import LineItemLlmStage

    doc = DocumentModel(filename="f.pdf")
    row = LineItem(source_label="R&D depreciation", canonical_key="sub__rd_depreciation")
    _write(row, "consolidated", "current", Decimal("900"), by="note_source")
    cited = [{"at": 0, "prose": True, "note": "8", "figures": {"prose": "1200"},
              "quote": "Depreciation of HK$1,200 is included in research and development "
                       "expenses.", "caption": None, "provenance": {"page_index": 42}}]
    LineItemLlmStage._write_prose(row, doc, Decimal("1200"), cited, PipelineContext(),
                                  SimpleNamespace(key="sub__rd_depreciation", label="R&D"))

    (slot,) = row.values.values()
    assert (slot.value, slot.value_raw) == (Decimal("1200"), Decimal("1200"))
    assert "prose_value_displaced_derived:900" in row.confidence.flags


def test_a_sentence_written_over_a_printed_slot_keeps_it():
    from app.stages.line_item_llm import LineItemLlmStage

    doc = DocumentModel(filename="f.pdf")
    row = _printed("587417")
    cited = [{"at": 0, "prose": True, "note": "8", "figures": {"prose": "529841"},
              "quote": "Depreciation of HK$529,841 is included in other operating expenses.",
              "caption": None, "provenance": {"page_index": 42}}]
    LineItemLlmStage._write_prose(row, doc, Decimal("529841"), cited, PipelineContext(),
                                  SimpleNamespace(key="k", label="k"))

    (slot,) = row.values.values()
    assert (slot.value, slot.value_raw, slot.derived) == (Decimal("529841"), Decimal("587417"),
                                                          False)
    assert "prose_value_displaced_printed:587417" in row.confidence.flags
