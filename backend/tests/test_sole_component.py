"""``sole_component_of``: a subtotal printed ALONE collapses onto the child the rulebook names.

THE DEFECT THIS CLOSES, reported off a real HKEX filing: the income statement prints one
undifferentiated tax line — "Income tax credit/(expenses) 3,159" — and splits current from deferred
nowhere. Total tax expense was filed and both template children stayed empty, so the analyst had a
total with nothing under it and no way to tell whether the split was missing or genuinely absent.

The inference divides nothing: the whole figure IS the current charge. That is the entire reason it
does not collide with ``global_rules.no_fabricated_split``, and it is why the refusals below matter
more than the acceptance — the moment a deferred amount is evidenced anywhere the filing has a split,
and asserting the whole charge is current would publish a figure the page contradicts.

Every test edits the rulebook or the page and watches the extraction change. Deleting
``sole_component_of`` from the concept turns the whole pass off, which is what makes the field a
declaration rather than a comment.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology
from app.stages.map_ontology import MapOntologyStage

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"

TOTAL = "pl_tax_expense__total_tax_expense"
CURRENT = "pl_tax_expense__current_tax"
DEFERRED = "pl_tax_expense__deferred_tax"


@pytest.fixture(scope="module")
def raw_ontology() -> dict:
    return json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


def _ontology(raw: dict):
    return load_ontology(copy.deepcopy(raw), resolve=True)


def _li(ordinal: int, label: str, value: int | None, *, note: str | None = None) -> LineItem:
    item = LineItem(source_label=label, ordinal=ordinal, role=LineRole.LINE, note_number=note)
    if value is not None:
        item.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label="current",
            provenance=Provenance(page_index=0, bbox=BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.22))))
    return item


def _income_statement(*rows: LineItem) -> DocumentModel:
    doc = DocumentModel(filename="f.pdf", locale="en")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    doc.line_items = list(rows)
    return doc


def _run(doc: DocumentModel, ontology) -> PipelineContext:
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = ontology                          # type: ignore[attr-defined]
    ctx.settings.llm.provider = "stub"               # deterministic, no provider call
    MapOntologyStage().run(doc, ctx)
    return ctx


def _keys(doc: DocumentModel) -> list[str]:
    return [li.canonical_key for li in doc.line_items if li.canonical_key]


def _tax_note(*captions_and_values: tuple[str, int]) -> NotesTable:
    table = NotesTable(note_number="9", title="Income tax expense")
    for ordinal, (label, value) in enumerate(captions_and_values):
        item = NoteItem(raw_label=label, ordinal=ordinal, role=LineRole.LINE)
        item.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label="current", provenance=Provenance(page_index=1)))
        table.items.append(item)
    return table


# --- the inference ------------------------------------------------------------------------------

def test_one_undifferentiated_tax_line_becomes_the_current_tax_charge(raw_ontology):
    """The filing's own wording, verbatim, and its own figure."""
    doc = _income_statement(_li(0, "Income tax credit/(expenses)", 3159))
    ctx = _run(doc, _ontology(raw_ontology))

    assert _keys(doc) == [TOTAL, CURRENT]
    inferred = doc.line_items[-1]
    assert [ev.value for ev in inferred.values.values()] == [Decimal(3159)]
    assert f"inferred_sole_component:{TOTAL}" in inferred.confidence.flags
    # Never mistakable for a caption the filing printed, and never presented as an exact hit.
    assert inferred.source_label == "Current tax"
    assert inferred.confidence.mapping <= 0.75
    assert any(f"sole_component({CURRENT})" in line for line in ctx.logs)


def test_the_inferred_row_carries_the_printed_lines_own_provenance(raw_ontology):
    """Click-to-source has to land on the tax line the figure came from. A row with no provenance
    is a number an analyst cannot check, which is worse than no row."""
    doc = _income_statement(_li(0, "Taxation", -1200, note="9"))
    _run(doc, _ontology(raw_ontology))

    printed, inferred = doc.line_items
    prov = [ev.provenance for ev in inferred.values.values()]
    assert prov and all(p is not None for p in prov)
    assert [(p.page_index, p.bbox) for p in prov] == [
        (ev.provenance.page_index, ev.provenance.bbox) for ev in printed.values.values()]
    assert inferred.note_number == "9"          # and the note the printed line cites


def test_both_periods_are_carried_not_just_the_current_one(raw_ontology):
    """One row, two columns. Collapsing them to a scalar would publish one column as though it were
    the row — the same failure the residual itemisation guards against."""
    row = LineItem(source_label="Income tax expense", ordinal=0, role=LineRole.LINE)
    for period, value in (("2023", -1200), ("2022", -900)):
        row.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label=period, provenance=Provenance(page_index=0)))
    doc = _income_statement(row)
    _run(doc, _ontology(raw_ontology))

    inferred = doc.line_items[-1]
    assert {ev.period_label: ev.value for ev in inferred.values.values()} == {
        "2023": Decimal(-1200), "2022": Decimal(-900)}


def test_the_template_tax_rollup_now_computes_and_agrees_with_the_printed_total(raw_ontology):
    """The point of the whole change, stated as arithmetic.

    ``pl_tax_expense__total_tax_expense`` is a ROLLUP over current + deferred, so with both children
    empty it computed nothing and the grid fell back to the printed figure — a total with no support
    under it. With the sole component filed it computes, and it computes the SAME number the filing
    printed. Nothing is double-counted: the total is built from its children, never from the printed
    row beside them.
    """
    from app.services.rollups import evaluate_rows, figures_as_shown

    template = json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))
    doc = _income_statement(_li(0, "Revenue", 5000), _li(1, "Direct costs", -3000),
                            _li(2, "Administrative expenses", -500),
                            _li(3, "Profit before taxation", 1500),
                            _li(4, "Income tax expense", -300))
    _run(doc, _ontology(raw_ontology))
    rows = [{"canonical_key": li.canonical_key,
             "values": [{"basis": "consolidated", "period_label": "current",
                         "value": float(ev.value), "source": "machine"}
                        for ev in li.values.values()]}
            for li in doc.line_items if li.canonical_key]

    calc = evaluate_rows(template, rows, "consolidated", "current")
    tax = calc["pl_tax_expense__total_tax_expense"]
    assert tax.computable and tax.value == -300.0
    assert calc["pl_profit_for_the_year"].value == 1200.0     # 1500 − 300, once
    shown = figures_as_shown(template, rows, "consolidated", "current")
    assert shown["pl_tax_expense__current_tax"] == -300.0
    assert shown["pl_tax_expense__total_tax_expense"] == -300.0


# --- and every reason it is refused --------------------------------------------------------------

def test_a_deferred_tax_line_the_mapper_claimed_cancels_the_inference(raw_ontology):
    doc = _income_statement(_li(0, "Income tax expense", -1200),
                            _li(1, "Deferred tax", -300))
    ctx = _run(doc, _ontology(raw_ontology))

    assert sorted(_keys(doc)) == sorted([TOTAL, DEFERRED])
    assert CURRENT not in _keys(doc)
    assert any("sole_component_declined" in line for line in ctx.logs)


def test_a_deferred_tax_line_the_mapper_MISSED_cancels_it_too(raw_ontology):
    """The case that matters. An unrecognised "Deferred taxation" row means the split exists and was
    missed; inferring from the total then asserts the whole charge is current, which the page
    contradicts two lines further down. Checking only what the mapper CLAIMED would miss exactly the
    filings this inference is most dangerous on.
    """
    doc = _income_statement(_li(0, "Income tax expense", -1200),
                            _li(1, "Deferred taxation arising on fair value uplift", -300))
    ctx = _run(doc, _ontology(raw_ontology))

    assert doc.line_items[1].canonical_key != DEFERRED          # the mapper did not claim it
    assert CURRENT not in _keys(doc)
    assert any("is printed on the face as" in line for line in ctx.logs)


def test_deferred_tax_disclosed_only_in_the_cited_note_cancels_it(raw_ontology):
    """A filing that splits the charge in its tax note HAS a split. The note is where the split
    usually is, so a face-only check would infer against the disclosure."""
    doc = _income_statement(_li(0, "Income tax expense", -1200, note="9"))
    doc.notes = [_tax_note(("Current tax 當期稅項", -900), ("Deferred tax 遞延稅項", -300))]
    ctx = _run(doc, _ontology(raw_ontology))

    assert CURRENT not in _keys(doc)
    assert any("is disclosed in note 9" in line for line in ctx.logs)


def test_a_note_the_tax_line_does_not_cite_is_not_evidence_about_it(raw_ontology):
    """"…in the tax note IT CITES". Some other note mentioning deferred tax — the balance-sheet
    deferred tax note, say — says nothing about how this charge is composed, and refusing on it would
    make the inference unreachable on any full annual report."""
    doc = _income_statement(_li(0, "Income tax expense", -1200, note="9"))
    uncited = _tax_note(("Deferred tax liabilities 遞延稅項負債", -300))
    uncited.note_number = "24"
    doc.notes = [uncited]
    _run(doc, _ontology(raw_ontology))

    assert CURRENT in _keys(doc)


def test_a_current_tax_line_already_on_the_face_is_left_alone(raw_ontology):
    """Nothing to infer: the child is printed. Adding a second row for it would double the charge."""
    doc = _income_statement(_li(0, "Income tax expense", -1200),
                            _li(1, "Current tax", -1200))
    _run(doc, _ontology(raw_ontology))

    assert _keys(doc).count(CURRENT) == 1


def test_no_tax_line_at_all_infers_nothing(raw_ontology):
    doc = _income_statement(_li(0, "Revenue", 5000))
    _run(doc, _ontology(raw_ontology))
    assert CURRENT not in _keys(doc)


def test_the_grid_says_the_figure_was_inferred_not_matched(raw_ontology):
    """The row's method is "rule", so the inspector used to read "Mapped by rule" — which tells the
    analyst a caption on the page said "Current tax". None does. A figure the pipeline reasoned its
    way to has to say so, or it is indistinguishable from one the filing printed."""
    from app.api.routes.documents import _inspector

    value = {"value": "-300", "provenance": {"page_index": 0},
             "confidence": {"flags": [f"inferred_sole_component:{TOTAL}"]}}
    out = _inspector({"mapping_method": "rule"}, value)
    assert out["tag"] == "inferred"
    assert out["note"].startswith("Inferred:") and TOTAL in out["note"]

    plain = _inspector({"mapping_method": "rule"}, {"value": "-300", "confidence": {"flags": []}})
    assert plain["tag"] == "machine" and plain["note"] == "Mapped by rule"


# --- the declaration is what switches it on ------------------------------------------------------

def test_removing_sole_component_of_from_the_rulebook_turns_the_pass_off(raw_ontology):
    """The property that makes the field a declaration: an author who deletes it gets the previous
    behaviour back — the total filed, both children empty, per no_fabricated_split as written."""
    raw = copy.deepcopy(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == CURRENT:
            del m["sole_component_of"]

    doc = _income_statement(_li(0, "Income tax credit/(expenses)", 3159))
    _run(doc, _ontology(raw))
    assert _keys(doc) == [TOTAL]


def test_the_shipped_rulebook_declares_it_on_exactly_one_concept(raw_ontology):
    """A second declaration would be a second inference nobody asked for, and the sibling-evidence
    test is only as good as the section it was reasoned about. Adding one is a deliberate act."""
    declared = {m["canonical_key"]: m["sole_component_of"] for m in raw_ontology["mappings"]
                if m.get("sole_component_of")}
    assert declared == {CURRENT: TOTAL}
    # …and the prose beside it says what the switch does, so the rulebook is not two sources.
    rule = next(m["decomposition_rule"] for m in raw_ontology["mappings"]
                if m["canonical_key"] == CURRENT)
    assert "sole_component_of" in rule and "no_fabricated_split" in rule
    assert "sole_component_of" in raw_ontology["global_rules"]["no_fabricated_split"]
