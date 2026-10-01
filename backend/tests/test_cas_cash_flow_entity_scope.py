"""Whose cash flows a mainland filing's cash-flow pages present — the Group's or the company's.

Two defects, both in ``stages.classify``, both measured on the three CAS reference filings:

1. A PAGE THAT ENDS IN ANOTHER STATEMENT. 河钢股份 000709 page 88 opens with 5、合并现金流量表 and
   prints 6、母公司现金流量表 part-way down; the company's statement runs on to page 89. A mid-page
   title was looked for only when the top of the page named nothing, so page 88 handed the GROUP's
   run on to page 89, and the company rows printed there above 7、合并所有者权益变动表 were filed
   consolidated. The consolidated operating cash flow captured was 13,718,703,924.02: the Group's
   printed 9,678,206,759.05 plus the company's 4,040,497,164.97 (investing, financing and closing
   cash the same way, in both years).

2. THE CASH-FLOW SUPPLEMENT IS A NOTE. 现金流量表补充资料 sits in 七、合并财务报表项目注释 on all
   three filings. Read as a face page past the notes that "re-presents" the cash-flow statement, it
   was called the COMPANY's, so its Group figures were added to the company's (688008's standalone
   operating cash flow 1,904,623,231.54 = company 213,301,725.40 + Group 1,691,321,506.14). And
   the whole page was read as a face, so the tail of the note above its title and the cash
   movement / cash composition / foreign-currency tables below its reconciliation were filed as
   cash flows (688008's standalone "other financing cash flows" 20,200,964,701.28).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat, PageKind
from app.core.stage import PipelineContext
from app.stages import classify
from app.stages.classify import ClassifyStage
from app.stages.extract import ExtractStage
from app.stages.ingest import IngestStage


def _run(data: bytes) -> tuple[DocumentModel, list[str]]:
    doc = DocumentModel(filename="filing.pdf", fmt=DocFormat.PDF)
    ctx = PipelineContext(raw_bytes=data)
    for stage in (IngestStage(), ClassifyStage(), ExtractStage()):
        doc = stage.run(doc, ctx)
    return doc, ctx.logs


def _slots(doc: DocumentModel, page: int) -> dict[str, dict[tuple[str, str], Decimal]]:
    """{caption: {(basis, period): value}} for the face rows read off one page."""
    out: dict[str, dict[tuple[str, str], Decimal]] = {}
    for li in doc.line_items:
        for ev in li.values.values():
            if ev.provenance is None or ev.provenance.page_index != page or ev.value is None:
                continue
            out.setdefault(li.source_label, {})[(ev.basis.value, ev.period_label or "")] = \
                Decimal(str(ev.value))
    return out


# ── 1. the page that ends in the company's statement ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def pair():
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_cas_cash_flow_pair_running_onto_next_page_pdf

    return _run(make_cas_cash_flow_pair_running_onto_next_page_pdf())


def test_the_page_records_the_statement_it_ends_in(pair):
    doc, logs = pair
    first = doc.pages[0]
    assert first.statement == "cash_flow" and first.scope == "consolidated"
    assert first.evidence["closing_title"] == "6、母公司现金流量表"
    assert first.evidence["scope_after_closing_title"] == "company"
    assert any("page=0:closing_title=" in m for m in logs), logs


def test_the_next_page_continues_the_companys_statement(pair):
    """THE DEFECT, as page 1's evidence: the run above its own title is the company's."""
    doc, _ = pair
    second = doc.pages[1]
    assert second.evidence["statement_before_title"] == "cash_flow"
    assert second.evidence["scope_before_title"] == "company"


def test_the_companys_rows_are_filed_as_the_companys(pair):
    """…and as the figures. Before the fix the company's 4,040,497,164.97 was filed CONSOLIDATED
    beside the Group's 9,678,206,759.05 and the two were summed into one published figure."""
    doc, _ = pair
    page0, page1 = _slots(doc, 0), _slots(doc, 1)
    assert page0["经营活动产生的现金流量净额"] == {
        ("consolidated", "current"): Decimal("9678206759.05"),
        ("consolidated", "prior"): Decimal("11213085443.28")}
    assert page1["经营活动产生的现金流量净额"] == {
        ("standalone", "current"): Decimal("4040497164.97"),
        ("standalone", "prior"): Decimal("7395438906.05")}
    assert page1["六、期末现金及现金等价物余额"] == {
        ("standalone", "current"): Decimal("14389609727.03"),
        ("standalone", "prior"): Decimal("24895265487.32")}
    # The equity statement below page 1's own title is the Group's again.
    assert {b for b, _ in page1["一、上年期末余额"]} == {"consolidated"}


def test_a_title_with_no_figures_between_is_not_a_second_statement():
    """A page that prints its title twice — no statement between them — does not end in another
    statement; only figures between two titles make the second one a statement of its own."""
    lines = [{"text": "合并现金流量表", "y": 60.0}, {"text": "母公司现金流量表", "y": 90.0},
             {"text": "销售商品、提供劳务收到的现金", "y": 120.0},
             {"text": "92,143,224,538.02", "y": 120.0}]
    assert classify._closing_statement(lines, lines[:1], 60.0) == (None, None, None)
    lines.insert(1, {"text": "113,973,832,889.31", "y": 75.0})
    name, title, y = classify._closing_statement(lines, lines[:1], 60.0)
    assert (name, title, y) == ("cash_flow", "母公司现金流量表", 90.0)


# ── 2. the supplement printed in the notes ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def supplement():
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_cas_cash_flow_supplement_in_notes_pdf

    return _run(make_cas_cash_flow_supplement_in_notes_pdf())


def test_the_supplement_is_the_groups_note(supplement):
    """Printed in 七、合并财务报表项目注释, so it is the Group's — not the company's re-presentation
    of a statement already shown, which is what the past-the-notes rule took it for."""
    doc, logs = supplement
    kinds = [p.kind for p in doc.pages]
    assert kinds == [PageKind.FACE, PageKind.NOTES, PageKind.NOTES, PageKind.FACE,
                     PageKind.NOTES], kinds
    page = doc.pages[3]
    assert page.statement == "cash_flow"
    assert page.scope == "consolidated"
    assert any("page=3:entity_scope=supplement_in_notes(consolidated" in m for m in logs), logs


def test_the_supplements_rows_are_filed_as_the_groups(supplement):
    doc, _ = supplement
    rows = _slots(doc, 3)
    assert rows["净利润"] == {("consolidated", "current"): Decimal("1340736124.50"),
                            ("consolidated", "prior"): Decimal("451147481.14")}
    assert rows["经营活动产生的现金流量净额"] == {
        ("consolidated", "current"): Decimal("1691321506.14"),
        ("consolidated", "prior"): Decimal("731249699.11")}
    assert {b for slots in rows.values() for b, _ in slots} == {"consolidated"}


def test_only_the_reconciliation_is_read_as_a_face(supplement):
    """Note 78's tail above the title goes to the notes reader; the cash movement below the
    reconciliation's closing row is not a cash-flow line either."""
    doc, logs = supplement
    page = doc.pages[3]
    assert page.evidence["notes_above_title"] is True
    assert 0.0 < page.evidence["matched_title_y"] < page.evidence["face_ends_at_y"] < 1.0
    rows = _slots(doc, 3)
    for caption in ("回购库存股", "支付新租赁准则下租金", "合计", "现金的期末余额", "减：现金的期初余额"):
        assert caption not in rows, (caption, rows.get(caption))
    noted = {item.raw_label for note in doc.notes if 3 in note.source_pages for item in note.items}
    assert {"回购库存股", "支付新租赁准则下租金"} <= noted, noted
    assert any("page=3:notes_above_supplement_title=" in m for m in logs), logs
    assert any("page=3:supplement_ends_at=" in m for m in logs), logs


def test_a_supplement_in_the_parent_companys_chapter_is_the_companys():
    """The entity is the CHAPTER's: printed under 十九、母公司财务报表主要项目注释 the same untitled
    supplement is the parent company's."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_cas_cash_flow_supplement_in_notes_pdf

    doc, _ = _run(make_cas_cash_flow_supplement_in_notes_pdf(chapter="十九、母公司财务报表主要项目注释"))
    assert doc.pages[3].scope == "company"
    assert {b for slots in _slots(doc, 3).values() for b, _ in slots} == {"standalone"}


@pytest.mark.parametrize("rows, want", [
    ([("七、合并财务报表项目注释", 100.0)], "consolidated"),
    ([("十九、", 100.0), ("母公司财务报表主要项目注释", 100.0)], "company"),
    ([("七、合并财务报表项目注释", 100.0), ("十九、母公司财务报表主要项目注释", 500.0)], "company"),
    ([("1、货币资金", 100.0)], None),
    ([("十九、补充资料", 100.0)], None),
])
def test_the_statement_notes_chapter_is_read_off_its_heading(rows, want):
    lines = [{"text": t, "y": y} for t, y in rows]
    assert classify._statement_notes_chapter(lines) == want


def test_the_extent_spans_pages_and_returns_trailing_pages_to_the_notes():
    """688008's shape: titled at the foot of one page, closing on the next — and 300319's: an
    untitled page after the closing one, FACE on figure density alone, is the notes again."""
    feat = classify.PageFeat
    feats = [
        feat(index=0, statement="cash_flow", matched_title="5、合并现金流量表", matched_title_y=0.1),
        feat(index=1),
        feat(index=2, statement="cash_flow", matched_title="79、现金流量表补充资料",
             matched_title_y=0.8, amounts_above_title=True),
        feat(index=3),
        feat(index=4),
    ]
    cache = [([], 1000.0), ([], 1000.0),
             ([{"text": "79、现金流量表补充资料", "y": 800.0}, {"text": "补充资料", "y": 850.0}],
              1000.0),
             ([{"text": "净利润", "y": 100.0}, {"text": "经营活动产生的现金流量净额", "y": 400.0},
               {"text": "1,691,321,506.14", "y": 399.0},
               {"text": "2、不涉及现金收支的重大投资和筹资活动：", "y": 440.0}], 1000.0),
             ([{"text": "现金的期末余额", "y": 100.0}], 1000.0)]
    path = ["face", "notes", "face", "face", "face"]
    out, extent = classify._cf_supplement_extent(path, feats, cache)
    assert out == ["face", "notes", "face", "face", "notes"]
    assert extent[2] == {"supplement": True, "notes_above_title": True}
    assert extent[3] == {"face_ends_at_y": pytest.approx(0.44)}
    # The statements' own face run, before any note, is never a supplement run.
    assert 0 not in extent


def test_no_closing_row_leaves_the_run_as_it_was():
    feat = classify.PageFeat
    feats = [feat(index=0), feat(index=1, statement="cash_flow",
                                 matched_title="现金流量表补充资料", matched_title_y=0.2),
             feat(index=2)]
    cache = [([], 1000.0), ([{"text": "现金流量表补充资料", "y": 200.0}], 1000.0), ([], 1000.0)]
    path = ["notes", "face", "face"]
    out, extent = classify._cf_supplement_extent(path, feats, cache)
    assert out == path and extent == {}
