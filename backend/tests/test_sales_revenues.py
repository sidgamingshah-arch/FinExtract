"""services.sales_revenues — Priority 2 note fallback only. Priority 1 (the face) is already
handled by the ordinary alias-matching mapper; see docs/PRC_Sales_Revenues_Extraction_Logic_Simplified.md."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, UnitContext
from app.services.sales_revenues import compute_note_fallback


def _ev(value, basis="consolidated", period="current"):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency="CNY", scale_factor=Decimal(1)))


def _item(label, value, group_hint="", role=LineRole.LINE):
    it = NoteItem(raw_label=label, group_hint=group_hint, role=role)
    ev = _ev(value)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _note(number, title, items):
    return NotesTable(note_number=number, title=title, items=items)


def _doc(*notes):
    return DocumentModel(notes=list(notes))


def test_extracts_the_zhuying_revenue_row_from_the_revenue_note():
    doc = _doc(_note("30", "营业收入和营业成本", [
        _item("主营业务收入", "1000"),
        _item("主营业务成本", "600"),
    ]))
    result = compute_note_fallback(doc)[("consolidated", "current")]
    assert result.value == Decimal("1000")
    assert result.priority_used == "P2"


def test_cost_column_is_never_selected():
    doc = _doc(_note("30", "营业收入", [_item("主营业务成本", "600")]))
    assert compute_note_fallback(doc) == {}


def test_bare_zhuying_row_without_a_cost_suffix_is_accepted():
    doc = _doc(_note("30", "营业收入", [_item("主营业务", "1000")]))
    result = compute_note_fallback(doc)[("consolidated", "current")]
    assert result.value == Decimal("1000")


def test_note_not_titled_revenue_is_ignored():
    doc = _doc(_note("30", "管理费用", [_item("主营业务收入", "1000")]))
    assert compute_note_fallback(doc) == {}


def test_two_different_figures_for_the_same_period_are_flagged_possible_duplicate():
    doc = _doc(
        _note("30", "营业收入", [_item("主营业务收入", "1000")]),
        _note("31", "营业收入和营业成本", [_item("主营业务收入", "1200")]))
    result = compute_note_fallback(doc)[("consolidated", "current")]
    assert "POSSIBLE_DUPLICATE" in result.flags


# --- the stage: which face reading is allowed to stand ------------------------------------------
# §2 names the Priority 1 caption and §4 forbids total 营业收入 outright, so a reading bound under
# any other Chinese caption is the figure the spec refuses rather than a weaker P1 to defer to.
# Measured on Sun Create Electronics: the filing prints its 1,603,146,551.95 total TWICE, as
# 一、营业总收入 and again as 其中：营业收入 (the total and its own "of which" restatement), while
# the answer the spec asks for is the note's 主营业务 row at 1,589,859,743.31.

from app.core.models.line_item import LineItem                            # noqa: E402
from app.core.stage import PipelineContext                                # noqa: E402
from app.stages.sales_revenues import SalesRevenuesStage                  # noqa: E402

TEMPLATE = {"statements": [{"type": "profit_and_loss", "sections": [
    {"canonical_key": "is_pl", "children": [{"canonical_key": "is_pl__sales_revenues"}]}]}]}

# The note as the filing prints it: one row, revenue and cost side by side.
REVENUE_NOTE = _note("61", "、 营业收入和营业成本", [
    _item("主营业务", "1589859743.31"),
    _item("其他业务", "13286808.64"),
    _item("合计", "1603146551.95", role=LineRole.TOTAL),
])


def _face(label, value, key="is_pl__sales_revenues"):
    li = LineItem(source_label=label, canonical_key=key)
    ev = _ev(value)
    li.values[ev.key.model_dump_json()] = ev
    return li


def _run(*face_rows, notes=(REVENUE_NOTE,)):
    doc = DocumentModel(notes=list(notes), line_items=list(face_rows))
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template_def = TEMPLATE                          # type: ignore[attr-defined]
    SalesRevenuesStage().run(doc, ctx)
    return doc


def _published(doc):
    return [(li.source_label, str(ev.value))
            for li in doc.line_items if li.canonical_key == "is_pl__sales_revenues"
            for ev in li.values.values()]


def test_a_face_total_is_displaced_by_the_note_s_main_business_row():
    doc = _run(_face("其中：营业收入", "1603146551.95"))

    assert _published(doc) == [("is_pl__sales_revenues", "1589859743.31")]


def test_the_refused_reading_is_unbound_for_review_not_silently_dropped():
    """Total operating revenue is a real printed figure — just not this concept's.

    Unbinding hands it to ``face_mapping_contract`` for its own engine key, which is what every
    face row nothing could place gets. Deleting the value instead would lose a figure the
    filing prints on its face.
    """
    doc = _run(_face("其中：营业收入", "1603146551.95"))

    refused = next(li for li in doc.line_items if li.source_label == "其中：营业收入")
    assert refused.canonical_key is None
    assert "spec_refused_caption" in refused.confidence.flags
    assert "requires_concept_review" in refused.confidence.flags
    assert [str(ev.value) for ev in refused.values.values()] == ["1603146551.95"]


def test_the_same_total_printed_under_two_captions_is_not_published_twice():
    """The double count: 一、营业总收入 and 其中：营业收入 are one figure printed twice."""
    doc = _run(_face("一、营业总收入", "1603146551.95"),
               _face("其中：营业收入", "1603146551.95"))

    assert _published(doc) == [("is_pl__sales_revenues", "1589859743.31")]


def test_a_genuine_priority_one_reading_still_stands():
    doc = _run(_face("主营业务收入", "1589859743.31"))

    assert _published(doc) == [("主营业务收入", "1589859743.31")]
    assert next(li for li in doc.line_items).canonical_key == "is_pl__sales_revenues"


def test_an_english_face_caption_is_priority_one_even_beside_a_chinese_note():
    """A bilingual filing pairs an English face caption with a Chinese note.

    On an HKEX filing Turnover IS the revenue line, so §4's prohibition — which is about the
    Chinese pair — must not reach it.
    """
    doc = _run(_face("TURNOVER", "4995768"))

    assert _published(doc) == [("TURNOVER", "4995768")]


def test_a_filing_with_no_revenue_note_keeps_whatever_the_mapper_bound():
    """Nothing to displace the reading WITH, and no jurisdiction over that filing's captions.

    The caption here is deliberately one §2 does NOT admit: an HKEX filing may print its revenue
    line as "Gross proceeds" or under a heading this spec never contemplated, and unbinding it
    would delete a correct figure in the name of a PRC rule that has nothing to say about it.
    """
    doc = _run(_face("Gross proceeds from operations", "500"),
               notes=(_note("12", "管理费用", [_item("主营业务收入", "1000")]),))

    assert _published(doc) == [("Gross proceeds from operations", "500")]


def test_a_refused_reading_with_no_note_figure_to_replace_it_still_goes():
    """§4 is a prohibition, not a preference: a wrong figure is worse than a missing one."""
    doc = _run(_face("其中：营业收入", "1603146551.95"),
               notes=(_note("61", "营业收入和营业成本",
                            [_item("其他业务", "13286808.64")]),))

    assert _published(doc) == []
    assert next(li for li in doc.line_items).canonical_key is None
