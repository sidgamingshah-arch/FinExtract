"""VIVO ENERGY MAURITIUS 2025: THE FOUR STATEMENTS ARE READ AS THEY ARE PRINTED.

NEW FILE -> backend/tests/test_vivo_energy_mauritius_statements_read_as_printed.py

Sheets 34 and 35 of the filing are two-page spreads: the statement of profit or loss and of
comprehensive income (printed page 66) beside the statement of financial position (p67), and the
statement of changes in equity (p68) beside the statement of cash flows (p69). Once the dated titles
made them faces (`test_a_title_printed_with_its_date_is_a_face_on_every_filing`), five reading
defects were left on them, each pinned here on the real pages:

* "( 111,807)" — a space after the bracket — lost its minus (`pdf_extract._join_spaced_open_brackets`);
* "Cash flows from investing activities", in sentence case, was no banner, so it was glued onto
  the next caption and interest received was read under the operating section
  (`mapping.section_of_banner_only`);
* "Payments for purchase of property, plant and equipment / and intangible assets" was split, and
  the figures kept only "and intangible assets" (`row_reconstruct._continues_on_a_connective`);
* the four balance-sheet subtotals print no caption, and none was marked as its section's total
  (`row_reconstruct.FACE_SECTION_TOTAL_FLAG`);
* the three-component statement of changes in equity read no rows at all
  (`row_reconstruct._small_equity_matrix`).

Figures are as printed, in Rs'000.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.enums import Basis, PageKind

BACKEND = pathlib.Path(__file__).resolve().parent.parent
VIVO = BACKEND.parent / "testdata" / "filings" / "sem_vivo_energy_mauritius_2025.pdf"
SEED = BACKEND / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"

pytestmark = pytest.mark.skipif(not VIVO.exists(), reason=f"{VIVO.name} not present")


@pytest.fixture(scope="module")
def vivo():
    """Ingest to extract on the real filing, the statements' two sheets read and nothing else."""
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view
    from app.stages.classify import ClassifyStage
    from app.stages.extract import ExtractStage
    from app.stages.ingest import IngestStage
    from app.stages.integrity import IntegrityStage
    from app.stages.language import LanguageDetectStage

    doc = DocumentModel(filename=VIVO.name)
    ctx = PipelineContext(raw_bytes=VIVO.read_bytes())
    logs: list[str] = []
    ctx.log = logs.append
    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    ctx.ontology, ctx.line_items = build_working_view(shipped), shipped
    for stage in (IngestStage(), IntegrityStage(), LanguageDetectStage(), ClassifyStage()):
        doc = stage.run(doc, ctx)
    for page in doc.pages:
        if page.index not in (34, 35) and page.kind in (PageKind.FACE, PageKind.NOTES):
            page.kind = PageKind.OTHER
    doc = ExtractStage().run(doc, ctx)
    return doc, logs


def _rows(doc, caption: str, page: int):
    return [li for li in doc.line_items if li.source_label == caption
            and any(ev.provenance and ev.provenance.page_index == page
                    for ev in li.values.values())]


def _figures(li) -> list[Decimal]:
    return [ev.value for ev in li.values.values()]


def test_the_tax_charge_keeps_its_minus_and_its_caption(vivo):
    doc, _ = vivo
    tax = _rows(doc, "Income tax expense", 34)
    assert len(tax) == 1
    assert sorted(_figures(tax[0])) == [Decimal("-111807"), Decimal("-106768")]
    assert not _rows(doc, "Income tax expense (", 34)


def test_each_cash_flow_section_opens_under_its_own_sentence_case_banner(vivo):
    doc, _ = vivo
    received = _rows(doc, "Interest received", 35)
    assert len(received) == 1
    assert received[0].section_hint == "Cash flows from investing activities"
    proceeds = _rows(doc, "Proceeds from disposal of property, plant and equipment", 35)
    assert len(proceeds) == 1 and sorted(_figures(proceeds[0])) == [Decimal("1715"),
                                                                     Decimal("3267")]
    assert not [li for li in doc.line_items
                if (li.source_label or "").startswith("Cash flows from investing activities ")]


def test_a_caption_wrapped_on_and_is_one_caption(vivo):
    doc, _ = vivo
    whole = _rows(doc, "Payments for purchase of property, plant and equipment and intangible "
                       "assets", 35)
    assert len(whole) == 1
    assert sorted(_figures(whole[0])) == [Decimal("-300791"), Decimal("-263191")]
    assert not _rows(doc, "and intangible assets", 35)


def test_the_four_uncaptioned_subtotals_are_marked_as_their_sections_totals(vivo):
    from app.services.row_reconstruct import FACE_SECTION_TOTAL_FLAG

    doc, _ = vivo
    totals = {li.source_label: li.get_value(Basis.CONSOLIDATED, period_label="current").value
              for li in doc.line_items if FACE_SECTION_TOTAL_FLAG in li.confidence.flags}
    assert totals == {"Non-current assets": Decimal("2050718"),
                      "Current assets": Decimal("2738706"),
                      "Non-current liabilities": Decimal("258549"),
                      "Current liabilities": Decimal("3323069")}


def test_the_three_component_equity_statement_is_read_by_its_columns(vivo):
    doc, logs = vivo
    assert "extract:page=35:equity_matrix_columns=3" in logs
    closing = [li for li in _rows(doc, "At 31 December 2025", 35)
               if all(getattr(ev, "column_index", None) is not None for ev in li.values.values())]
    assert len(closing) == 1
    by_column = {ev.period_label: ev.value for ev in closing[0].values.values()}
    assert sorted(by_column.values()) == [Decimal("293223"), Decimal("914583"),
                                          Decimal("1207806")]
    opening = [li for li in _rows(doc, "At 01 January 2024", 35)
               if all(getattr(ev, "column_index", None) is not None for ev in li.values.values())]
    assert len(opening) == 1
    assert Decimal("2024") not in _figures(opening[0]) and Decimal("1") not in _figures(opening[0])


def test_the_owners_sub_heading_is_not_glued_onto_the_dividend(vivo):
    """p68 prints "Transactions with owners" alone over "Dividends declared (Note 26)"; folded
    forward it made the caption "Transactions with owners Dividends declared (Note 26)", which no
    dividend line can name (`row_reconstruct._EQUITY_OWNERS_HEADING`)."""
    doc, _logs = vivo
    dividends = [li for li in _rows(doc, "Dividends declared (Note 26)", 35)
                 if all(getattr(ev, "column_index", None) is not None for ev in li.values.values())]
    assert len(dividends) == 2
    assert sorted(min(_figures(li)) for li in dividends) == [Decimal("-504343"),
                                                             Decimal("-486456")]
    assert not [li for li in doc.line_items
                if li.source_label.startswith("Transactions with owners Dividends")]
    assert _rows(doc, "Total transactions with owners", 35)
