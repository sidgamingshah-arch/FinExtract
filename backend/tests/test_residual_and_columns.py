"""A printed face line always reaches the face, and always in the period it was printed in.

Two independent failures both silently removed real money from a statement:

* a line that matched no specific concept was dropped, so the section subtotal stopped tying;
* a line reporting only ONE of the two comparative periods had that figure filed as "current"
  because it was the first value in its row, regardless of which column it sat under.

Neither is visible by reading a row — only the section subtotal reveals them, which is why the
structural rollups matter as much as the routing.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items


def _row(y: float, label: str, cells: list[tuple[float, str]]) -> list[Word]:
    out = [Word(text=t, bbox=BBox(x0=0.10 + i * 0.09, y0=y, x1=0.17 + i * 0.09, y1=y + 0.01))
           for i, t in enumerate(label.split())]
    for x, text in cells:
        out.append(Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + 0.06, y1=y + 0.01)))
    return out


CUR, PRIOR = 0.74, 0.86        # the two comparative value columns
NOTE_COL = 0.62                # the narrow note-reference column to their left


def _period_map(item) -> dict[str, str]:
    return {v.period_label: str(v.value) for v in item.values.values()}


def test_a_line_reported_in_one_period_only_keeps_that_period():
    """"Pledged deposits" with a prior-year figure and no current one. Read as "first value,
    therefore current" it overstates the current period and understates the prior by the same
    amount — which is exactly how the balance sheet failed to tie."""
    words = (
        _row(0.10, "Trade receivables", [(CUR, "198,330"), (PRIOR, "466,350")])
        + _row(0.13, "Restricted cash", [(CUR, "1,564,401"), (PRIOR, "3,866,093")])
        + _row(0.16, "Cash and equivalents", [(CUR, "4,884,525"), (PRIOR, "9,118,953")])
        + _row(0.19, "Pledged deposits", [(PRIOR, "2,031,012")])
    )
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native")
    pledged = next(i for i in items if "Pledged" in i.source_label)
    assert _period_map(pledged) == {"prior": "2031012"}


def test_a_note_reference_column_is_not_read_as_a_period():
    """Note references align as tightly as any money column. Taken as column 0 they make every
    real figure one period too late — a whole page of current-year amounts filed as prior."""
    words = (
        _row(0.10, "Depreciation of equipment", [(NOTE_COL, "14"), (CUR, "80,427"),
                                                 (PRIOR, "70,896")])
        + _row(0.13, "Depreciation of ROU assets", [(NOTE_COL, "16"), (CUR, "22,145"),
                                                    (PRIOR, "58,451")])
        + _row(0.16, "Amortisation", [(NOTE_COL, "17"), (CUR, "167"), (PRIOR, "166")])
        + _row(0.19, "Finance costs", [(NOTE_COL, "10"), (CUR, "1,492,343"),
                                       (PRIOR, "3,166,738")])
    )
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native")
    dep = next(i for i in items if "Depreciation of equipment" in i.source_label)
    assert _period_map(dep) == {"current": "80427", "prior": "70896"}


def test_a_note_reference_inside_a_list_is_still_a_note_reference():
    """A row citing several notes carries the separator with the token ("8," of "8, 13"). While
    that parsed as the number eight it invented a value in the note column."""
    from app.services.numbers import NumberFormat, parse_number

    assert parse_number("8,", NumberFormat()).ok is False
    assert parse_number("8", NumberFormat()).value_raw == Decimal(8)
    assert parse_number("8,211,620", NumberFormat()).value_raw == Decimal(8211620)


def test_an_unmapped_face_line_is_routed_to_its_own_sections_residual_bucket():
    """The section is found from the statement's own structure — a section runs up to its
    subtotal — so it works on an income statement, which prints no section banners at all."""
    import json
    from pathlib import Path

    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import Basis, LineRole
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.stage import PipelineContext
    from app.schemas.loader import load_template
    from app.stages.residual import ResidualStage

    tpl_path = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
                / "hkfrs_hk_china_template.json")
    template = load_template(json.loads(tpl_path.read_text(encoding="utf-8")))

    def li(ordinal: int, label: str, key: str | None, value: int,
           role: LineRole = LineRole.LINE) -> LineItem:
        item = LineItem(source_label=label, canonical_key=key, ordinal=ordinal, role=role)
        item.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label="current", provenance=Provenance(page_index=0)))
        return item

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="balance_sheet")]
    doc.line_items = [
        li(0, "Trade and bills payables", "bs_current_liabilities__current_trade_payables", 100),
        li(1, "Some caption no concept covers", None, 25),
        li(2, "Total current liabilities",
           "bs_current_liabilities__total_current_liabilities", 125, LineRole.SUBTOTAL),
    ]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = template
    ResidualStage().run(doc, ctx)

    routed = doc.line_items[1]
    assert routed.canonical_key == "bs_current_liabilities__others"
    assert "residual_combined" in routed.confidence.flags

    # And the section now ties: 100 + 25 == 125, which is the point of routing it.
    from app.services.structural_checks import evaluate_structure

    report = evaluate_structure(template, doc.line_items)
    rollup = next(r for r in report.results
                  if r.rule_id == "rollup:bs_current_liabilities__total_current_liabilities")
    assert rollup.status == "pass"


def test_narrative_printed_below_the_statement_is_not_routed_into_it():
    """Below the closing total comes prose — the note on what cash equivalents comprise — and it
    arrives as rows because it carries figures. Routed into a section it corrupts that section's
    subtotal by whatever the sentence happened to contain."""
    import json
    from pathlib import Path

    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import Basis, LineRole
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.stage import PipelineContext
    from app.schemas.loader import load_template
    from app.stages.residual import ResidualStage

    tpl_path = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
                / "hkfrs_hk_china_template.json")
    template = load_template(json.loads(tpl_path.read_text(encoding="utf-8")))

    def li(ordinal: int, label: str, key: str | None, value: int,
           role: LineRole = LineRole.LINE) -> LineItem:
        item = LineItem(source_label=label, canonical_key=key, ordinal=ordinal, role=role)
        item.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label="current", provenance=Provenance(page_index=0)))
        return item

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="cash_flow")]
    doc.line_items = [
        li(0, "New bank borrowings",
           "cf_cash_flow_from_financing_activities__proceeds_from_borrowings", 900),
        li(1, "Cash and cash equivalents at end of year",
           "cf_closing_cash_and_cash_equivalents", 4000, LineRole.TOTAL),
        li(2, "in the consolidated statement of cash flows comprise the following", None,
           4_696_114),
    ]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = template
    ResidualStage().run(doc, ctx)

    assert doc.line_items[2].canonical_key is None
