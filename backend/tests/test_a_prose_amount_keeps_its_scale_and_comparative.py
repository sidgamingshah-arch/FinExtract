"""A FIGURE STATED IN A SENTENCE KEEPS ITS SCALE AND ITS COMPARATIVE.

PetroChina's note 36 says the capital commitments "were RMB20,878 (2023: RMB6,050)" in a filing
presented in RMB million. The prose route divided every sentence's amount by the document's scale
(a sentence usually states its figure in full, "HK$529,841,000" against a face in thousands), so
it published 0.020878, and it kept only the amount, so the 2023 figure was dropped.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel
from app.core.models.line_item import LineItem, UnitContext
from app.core.stage import PipelineContext
from app.services.note_sourced import prose_context
from app.stages.line_item_llm import LineItemLlmStage

MILLIONS = UnitContext(currency="CNY", scale_factor=Decimal(1_000_000), units_label="million")
THOUSANDS = UnitContext(currency="HKD", scale_factor=Decimal(1000), units_label="thousand")


def test_the_sentence_says_its_scale_and_its_comparative():
    text = ("the Group's capital commitments contracted but not provided for ... were RMB20,878 "
            "(2023: RMB6,050). These capital commitments")
    assert prose_context(Decimal("20878"), text) == (None, Decimal("6050"))
    assert prose_context(Decimal("20878"), "RMB20,878 million (2023: RMB6,050 million)") == (
        Decimal(1_000_000), Decimal("6050"))
    assert prose_context(Decimal("529841000"), "of HK$529,841,000 are included") == (None, None)


def _written(unit, amount, prior=None, scale_word=None):
    doc = DocumentModel(filename="f.pdf")
    doc.unit_context = unit
    row = LineItem(source_label="Capital commitments", canonical_key="k")
    cited = [{"at": 0, "prose": True, "note": "36", "figures": {"prose": amount},
              "quote": "", "caption": None, "provenance": {"page_index": 286},
              "prose_prior": prior, "prose_scale": scale_word}]
    LineItemLlmStage._write_prose(row, doc, Decimal(amount), cited, PipelineContext(),
                                  SimpleNamespace(key="k", label="k"))
    return {ev.period_label: ev.value for ev in row.values.values()}


def test_an_amount_below_one_unit_of_the_statements_is_already_in_them():
    assert _written(MILLIONS, "20878", prior="6050") == {"current": Decimal("20878"),
                                                         "prior": Decimal("6050")}


def test_a_scale_word_beside_the_amount_is_applied():
    assert _written(MILLIONS, "20878", scale_word="1000000") == {"current": Decimal("20878")}


def test_a_full_amount_is_still_divided_and_so_is_its_comparative():
    assert _written(THOUSANDS, "529841000", prior="665553000") == {
        "current": Decimal("529841"), "prior": Decimal("665553")}
