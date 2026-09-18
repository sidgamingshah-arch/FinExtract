"""Whose figures a statement presents, when TWO statements share one page.

A mainland filing prints its primary statements in numbered pairs — the consolidated one, then the
parent company's — and a short pair fits on one page. Before this file, every way this codebase
decided a basis was per-page or per-COLUMN:

* ``row_reconstruct._basis_bands`` reads a two-basis column header ("Group | Company") over the
  figures. The mainland layout never prints one: each statement has its own title instead.
* ``PageSource.scope`` is the classifier's verdict about a whole page.
* ``scope_selection.entity_scope.company_only_markers`` infers one entity for a whole page from
  one line item being present.

None can change the answer part-way down a page, and 合并 is tested before 母公司 on purpose (so
"the Company and its subsidiaries" is not read as the Company) — so the parent company's statement
was filed as the Group's, and because the two statements share every caption the two rows met on
one concept and were summed.

``row_reconstruct._title_entity_basis`` is the fourth mechanism and the only y-ordered one: the
title row naming the entity precedes the rows it governs, and it names it explicitly.
"""
from __future__ import annotations

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, DocFormat
from app.core.stage import PipelineContext
from app.stages.classify import ClassifyStage
from app.stages.extract import ExtractStage
from app.stages.ingest import IngestStage
from app.services.row_reconstruct import _title_entity_basis


CONSOL_RECEIPTS = "113973832889.31"
COMPANY_RECEIPTS = "92143224538.02"


@pytest.fixture(scope="module")
def filing():
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_two_cas_cash_flow_statements_on_one_page_pdf

    doc = DocumentModel(filename="filing.pdf", fmt=DocFormat.PDF)
    ctx = PipelineContext(raw_bytes=make_two_cas_cash_flow_statements_on_one_page_pdf())
    for stage in (IngestStage(), ClassifyStage(), ExtractStage()):
        doc = stage.run(doc, ctx)
    return doc, ctx.logs


def _slots(doc: DocumentModel, caption: str) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for li in doc.line_items:
        if li.source_label != caption:
            continue
        for ev in li.values.values():
            if ev.value is None:
                continue
            out[(ev.basis.value, ev.period_label or "")] = str(ev.value)
    return out


# ── the predicate, on its own ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label, want", [
    ("5、合并现金流量表", Basis.CONSOLIDATED),
    ("6、母公司现金流量表", Basis.STANDALONE),
    ("1、合并资产负债表", Basis.CONSOLIDATED),
    ("2、母公司资产负债表", Basis.STANDALONE),
    ("合并现金流量表", Basis.CONSOLIDATED),
    ("母公司利润表", Basis.STANDALONE),
    ("7、合并所有者权益变动表", Basis.CONSOLIDATED),
    # EXHAUSTED BY THE PATTERN, or it is not a title. A supplementary-information note names the
    # statement it belongs to and must not move that note's basis.
    ("母公司现金流量表补充资料", None),
    ("现金流量表补充资料", None),
    # A row caption is not a title, however much of the statement's vocabulary it carries.
    ("销售商品、提供劳务收到的现金", None),
    ("收到的税费返还", None),
    ("项目", None),
    ("", None),
])
def test_only_a_statement_title_naming_an_entity_is_read_as_one(label, want):
    assert _title_entity_basis(label) is want


# ── and reaching the figures ─────────────────────────────────────────────────────────────────────

def test_each_statement_on_the_page_keeps_its_own_entity(filing):
    """THE DEFECT, stated as the figures. One caption, two statements, two entities — and before
    the title switch both slots said `consolidated`, so the parent company's receipts landed in the
    Group's column beside the Group's own."""
    doc, _ = filing
    slots = _slots(doc, "销售商品、提供劳务收到的现金")

    assert slots.get(("consolidated", "current")) == CONSOL_RECEIPTS
    assert slots.get(("standalone", "current")) == COMPANY_RECEIPTS
    # The comparative columns follow the same title, so they must split the same way.
    assert slots.get(("consolidated", "prior")) == "116697367262.93"
    assert slots.get(("standalone", "prior")) == "104391123754.02"


def test_the_two_entities_never_share_a_slot(filing):
    """The consequence that made this worth fixing: a concept reached by two rows is SUMMED, so two
    figures in one (basis, period) slot publish a number the filing prints nowhere. Asserted for
    every caption the page prints twice, not only the first."""
    doc, _ = filing
    for caption in ("销售商品、提供劳务收到的现金", "收到的税费返还",
                    "购买商品、接受劳务支付的现金", "支付的各项税费"):
        slots = _slots(doc, caption)
        assert {b for b, _ in slots} == {"consolidated", "standalone"}, (caption, slots)
        assert len(slots) == 4, (caption, slots)


def test_the_switch_says_so_in_the_run_log(filing):
    """A basis decided by something other than the page verdict has to be visible in the log, the
    same way `entity_scope=page_scope(...)` and `entity_scope=company_only(...)` are — a reviewer
    asking why a figure is the Company's needs the answer without a debugger."""
    _doc, logs = filing
    said = [ln for ln in logs if "entity_scope=statement_title" in ln]
    assert any("standalone" in ln for ln in said), said
