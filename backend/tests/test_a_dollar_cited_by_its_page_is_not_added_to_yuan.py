"""A sentence cited BY ITS PAGE carries the currency printed beside its amount, so a US$ amount is
not added to figures in the statements' currency.

The finding (fix_matrix:foreign_currency_prose:full). The currency rule refused a sentence's amount
printed in another currency than the statements' — 湖北能源's two letters of credit in 美元 added to
RMB guarantees — but only for a sentence cited from a NOTE, because only that path read the
currency off the filing's text (`note_sourced.prose_currency_at`). A sentence cited by its PAGE —
the way any of the 87 `anywhere` figure lines may be answered from prose, under the shipped
`anywhere` context and under `full` — recorded none, so a US$ amount on such a page was kept and
published as the statements' currency. `_page_sentence` now reads it beside the occurrence the
citation located, and `services.answer_checks.refuse_foreign_currency` refuses it.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, DocFormat, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, UnitContext
from app.schemas.line_items import load_line_item_set
from app.services import answer_checks
from app.services.mapping import SourceRef
from app.services.note_sourced import _page_sentence, resolve_sources

LC = ("The Group's bank issued letters of credit of US$6,568,662 on behalf of a subsidiary, and "
      "guarantees of RMB10,230,300 to a supplier.")
# Two paragraphs of one page, as far apart as a page prints them: the quote chooses between
# occurrences by the words printed around each.
TWICE = ("Guarantees outstanding were 1,000,000 元 at the year end. "
         + "The directors review the Group's exposure to financial guarantees at each reporting "
           "date and consider the likelihood of an outflow of resources to be remote. " * 4
         + "A standby letter of credit of 1,000,000 美元 was issued to the project's lenders.")


def test_the_currency_beside_a_page_sentences_amount_is_recorded():
    texts = {2: LC}
    dollars, why = _page_sentence("6,568,662", LC, 2, texts)
    assert why == "" and dollars["prose_currency"] == "USD"
    yuan, _ = _page_sentence("10,230,300", LC, 2, texts)
    assert yuan["prose_currency"] == "CNY"
    plain, _ = _page_sentence("2,431", "the Group had 2,431 employees", 3,
                              {3: "the Group had 2,431 employees"})
    assert plain["prose_currency"] is None, "nothing printed beside it reads as the statements'"


def test_the_occurrence_the_quote_chose_decides_the_currency():
    """A page prints one amount twice, in 元 and in 美元: the quote names which sentence is meant,
    and the currency is read beside THAT occurrence, not the first."""
    texts = {5: TWICE}
    lc, _ = _page_sentence("1,000,000", "A standby letter of credit of 1,000,000 美元", 5, texts)
    assert lc["prose_currency"] == "USD"
    guarantees, _ = _page_sentence("1,000,000", "Guarantees outstanding were 1,000,000 元",
                                   5, texts)
    assert guarantees["prose_currency"] == "CNY"


def test_a_page_sentence_in_dollars_is_refused_on_a_yuan_filing():
    resolved, unresolved = resolve_sources(
        [SourceRef(page=2, amount="6,568,662", quote=LC),
         SourceRef(page=2, amount="10,230,300", quote=LC)],
        [], allow_pages=True, pages=[], page_texts={2: LC})
    assert not unresolved and len(resolved) == 2
    doc = DocumentModel(unit_context=UnitContext(currency="CNY"))
    kept, refused = answer_checks.refuse_foreign_currency(doc, resolved)
    assert [e["figures"] for e in kept] == [{"prose": "10230300"}]
    assert refused[0]["refused"] == "foreign_currency" and refused[0]["at"] == 0
    assert "USD" in refused[0]["why"] and "CNY" in refused[0]["why"]


# ── the stage, with the whole report sent ─────────────────────────────────────────────────────

def _pdf(pages: list[str]) -> bytes:
    import fitz
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text, fontsize=9)
    return pdf.tobytes()


PAGES = ["CONSOLIDATED STATEMENT OF FINANCIAL POSITION\nBuildings 4,200",
         "DIRECTORS' REPORT\nLetters of credit of US$1,100,000 were outstanding.\n"
         "Bank guarantees of HK$2,500,000 were given."]


def _doc() -> DocumentModel:
    doc = DocumentModel(filename="ar.pdf")
    doc.fmt = DocFormat.PDF
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet"),
                 PageSource(index=1, kind=PageKind.UNKNOWN, statement=None)]
    row = LineItem(source_label="Buildings", canonical_key="bs_nca__buildings")
    row.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("4200"), value_raw=Decimal("4200"),
                                     provenance=Provenance(page_index=0))
    doc.line_items = [row]
    doc.unit_context = UnitContext(currency="HKD", scale_factor=Decimal(1))
    return doc


def _set():
    return load_line_item_set({
        "section_defaults": {"bs": {"statement": "balance_sheet", "section_scope": ["bs_nca"]},
                             "sup": {"statement": "covenants_supplemental"}},
        "items": [
            {"key": "bs_nca__buildings", "label": "Buildings", "inherits": "bs", "route": "face",
             "definition": "the buildings row", "type": "extracted"},
            {"key": "sup__lc", "label": "Letters of credit", "inherits": "sup",
             "route": "anywhere", "definition": "letters of credit outstanding",
             "type": "extracted"},
            {"key": "sup__guarantees", "label": "Guarantees given", "inherits": "sup",
             "route": "anywhere", "definition": "bank guarantees given", "type": "extracted"}]},
        resolve=True)


class _Answers:
    id = "page-currency"

    def __init__(self, answers: dict):
        self.answers = answers

    def complete_structured(self, *, system, messages, response_schema, **_):
        asked = [e["key"] for e in json.loads(messages[-1]["content"])["line_items"]]
        if response_schema.__name__ != "LineItemReply":
            return response_schema.model_validate({"answers": []}), {}
        return response_schema.model_validate(
            {"answers": [self.answers[k] for k in asked if k in self.answers]}), {}


@pytest.fixture()
def settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ex.llm_document_context,
           st.llm.provider)
    try:
        yield st
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ex.llm_document_context,
         st.llm.provider) = was


def _stage_run(settings, context):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage

    st = _set()
    ctx = PipelineContext(raw_bytes=_pdf(PAGES), settings=settings)
    ctx.line_items, ctx.ontology = st, build_working_view(st)
    ex = settings.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, False, []
    ex.llm_document_context = context
    provider = _Answers({
        "sup__lc": {"key": "sup__lc", "confidence": 0.9, "reason": "r", "sources": [
            {"page": 2, "amount": "1,100,000",
             "quote": "Letters of credit of US$1,100,000 were outstanding."}]},
        "sup__guarantees": {"key": "sup__guarantees", "confidence": 0.9, "reason": "r",
                            "sources": [{"page": 2, "amount": "2,500,000",
                                         "quote": "Bank guarantees of HK$2,500,000 were given."}]}})
    ctx.registry.register("llm", "page-currency", lambda: provider)
    settings.llm.provider = "page-currency"
    doc = _doc()
    LineItemLlmStage().run(doc, ctx)

    def figure(key):
        return [ev.value for li in doc.line_items if li.canonical_key == key
                for ev in li.values.values() if ev.value is not None]

    return figure, ctx


@pytest.mark.parametrize("context", ["anywhere", "full"])
def test_the_stage_publishes_the_hk_dollars_and_not_the_us_dollars(settings, context):
    """Both lines cite a sentence on page 2 of an HK$ filing. The guarantees in HK$ are published;
    the letters of credit in US$ are refused, logged, and leave their line with no figure."""
    figure, ctx = _stage_run(settings, context)
    assert figure("sup__guarantees") == [Decimal("2500000")]
    assert figure("sup__lc") == []
    assert any("sup__lc: citation REFUSED" in m and "USD" in m and "HKD" in m for m in ctx.logs)


def test_without_the_currency_the_same_answer_published_the_us_dollars(settings, monkeypatch):
    """The control: with the currency read beside a page sentence switched off — the code as it
    was — the same answer publishes 1,100,000 US$ as HK$ on the letters-of-credit line."""
    from app.services import note_sourced

    monkeypatch.setattr(note_sourced, "currency_around", lambda *_a, **_k: None)
    figure, _ctx = _stage_run(settings, "full")
    assert figure("sup__lc") == [Decimal("1100000")]
