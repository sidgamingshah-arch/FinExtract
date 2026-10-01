"""A model citation of a DERIVATIVE is refused on a line that excludes derivatives.

Measured on 嘉民 (kaming): the model cited note 22 "Derivative financial instruments", row
"Interest rate swaps (note 30(c)(i))" — the swaps' NOTIONAL amount, 950,000 — for a current
securities part whose label and definition both say "excluding derivatives", and Securities (CP)
published 950,000. The line's own derivative veto (`row_caption_none`) now binds model answers too.

Only that veto: Level 3 and the related-party parts exclude other things and are untouched, and a
structured deposit with embedded derivatives — which the securities definitions include — is kept.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.stages.line_item_llm import _derivative_vetoes, _refuse_derivative_citations

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def by_key():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in st.items}


def _cite(caption, title, at=0):
    return {"at": at, "note": "22", "title": title, "caption": caption,
            "figures": {"current": "950000"}}


def test_the_kaming_swaps_citation_is_refused(by_key):
    item = by_key["sub__fa_cp_investment_and_money_market_securities_note_total"]
    kept, refused = _refuse_derivative_citations(item, [_cite(
        "Interest rate swaps (note 30(c)(i)) 利率掉期合約（附註30(c)(i)）",
        "DERIVATIVE FINANCIAL INSTRUMENTS 22. 衍生金融工具")])
    assert kept == [] and len(refused) == 1
    # A refused citation carries NO figure, so nothing downstream can count it.
    assert "figures" not in refused[0] and "amount" not in refused[0]
    assert "excludes derivatives" in refused[0]["why"]


def test_a_row_in_a_derivatives_note_is_refused_whatever_its_caption(by_key):
    item = by_key["sub__fa_cp_other_fincl_assets_note_total"]
    kept, refused = _refuse_derivative_citations(item, [_cite(
        "Bank of China Limited", "Derivative financial instruments")])
    assert not kept and refused


def test_a_structured_deposit_with_embedded_derivatives_is_kept(by_key):
    item = by_key["sub__fa_cp_other_fincl_assets_note_total"]
    entry = _cite("Structured deposits with embedded derivatives 結構性存款",
                  "Other financial assets")
    kept, refused = _refuse_derivative_citations(item, [entry])
    assert kept == [entry] and not refused


def test_an_ordinary_securities_row_is_kept(by_key):
    item = by_key["sub__fa_cp_fvtpl_note_total"]
    entry = _cite("Listed equity securities", "Financial assets at fair value through profit or loss")
    assert _refuse_derivative_citations(item, [entry]) == ([entry], [])


def test_only_the_securities_parts_hold_model_answers_to_the_derivative_rule(by_key):
    held = {k for k, i in by_key.items() if _derivative_vetoes(i)}
    assert held and all(k.startswith(("sub__fa_cp_", "sub__ltp_")) for k in held), sorted(held)
    for untouched in ("sub__fa_cp_level_3_total", "sub__rp_find_2", "sub__rp_find_3_gross",
                      "sub__rp_trade_receivable_gross", "sub__rp_other_payable"):
        assert untouched not in held, untouched


def _stage_run():
    from decimal import Decimal

    from app.config import get_settings
    from app.core.models import DocumentModel, NotesTable
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage

    key = "sub__fa_cp_investment_and_money_market_securities_note_total"
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    row = NoteItem(raw_label="Interest rate swaps (note 30(c)(i)) 利率掉期合約（附註30(c)(i)）")
    row.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("950000"), value_raw=Decimal("950000"))
    doc = DocumentModel(filename="ar.pdf")
    doc.notes = [NotesTable(note_number="22", title="DERIVATIVE FINANCIAL INSTRUMENTS 22. 衍生金融工具",
                            items=[row])]
    settings = get_settings()
    ex = settings.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), settings.llm.provider)
    try:
        ctx = PipelineContext(raw_bytes=b"", settings=settings)
        ctx.line_items, ctx.ontology = st, build_working_view(st)
        ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, [key]

        class _P:
            id = "swaps"

            def complete_structured(self, *, system, messages, response_schema, **_):
                return response_schema.model_validate({"answers": [{
                    "key": key, "confidence": 0.9, "reason": "r",
                    "sources": [{"note": "22", "caption": "Interest rate swaps (note 30(c)(i))"}]}]}), {}

        ctx.registry.register("llm", "swaps", lambda: _P())
        settings.llm.provider = "swaps"
        LineItemLlmStage().run(doc, ctx)
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, settings.llm.provider) = was
    return next(li for li in doc.line_items if li.canonical_key == key), ctx


def test_end_to_end_the_swaps_notional_is_not_published():
    """The stage, not just the helper: the model's citation resolves to a real extracted row, and
    the line still publishes nothing — the refusal is on the row and in the log."""
    line, ctx = _stage_run()
    assert not any(v.value is not None for v in line.values.values()), line.values
    assert any("citation REFUSED" in m and "excludes derivatives" in m for m in ctx.logs), ctx.logs[-5:]
    assert any("llm_citation_unresolved" in f for f in line.confidence.flags)


def test_without_the_rule_the_same_answer_would_publish_the_notional(monkeypatch):
    """The control: the scenario above really reaches publication, so the test above is about the
    rule and not about a citation that never resolved."""
    import app.stages.line_item_llm as stage
    monkeypatch.setattr(stage, "_refuse_derivative_citations", lambda item, resolved: (resolved, []))
    line, _ctx = _stage_run()
    assert any(str(v.value) == "950000" for v in line.values.values()), line.values
