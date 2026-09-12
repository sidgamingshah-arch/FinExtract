"""MAIN LINE ITEM -> SUB-LINE ITEM -> the printed rows -> the page. One clickable chain.

NEW FILE -> backend/tests/test_main_to_sub_trace.py

WHAT WAS BROKEN, and it was two independent severings of the same hop.

A derived parent's figure comes from its declared CASCADE over sub-line items, and the rung's trail
recorded each input as::

    {"label": "sub__pbt_oper_exp_depreciation", "value": 529841}

Two things follow, and a reader saw both:

  1. THE LABEL WAS A RAW KEY. Every other contribution in the inspector is a printed caption, so
     this one line read as configuration internals leaking onto the screen.
  2. THERE WAS NOTHING TO CLICK. `derivation._contribution` hardcoded ``canonical_key: None`` with
     the comment "an input is a note line, not a mapped concept" — true of a note-row input, and
     exactly false of a cascade input, which IS another line item with its own figure, its own
     citations and its own row. So the chain stopped dead at the parent and the sub-line's notes
     were unreachable from the number they explain.

Both are fixed at the source rather than in the renderer: the cascade trail carries the referenced
line's key, its human label and — where the referenced row has one — its provenance, so the
existing contribution renderer can offer the hop without knowing anything new.

WHAT IS ASSERTED HERE is the contract the UI depends on, at the boundary the UI reads
(`derivation.merge_for_basis`), plus the distinction that makes it meaningful: a NOTE-ROW input
still carries no key, because it genuinely is not a concept.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import derivation, note_sourced


def _store(trail: dict) -> dict:
    return derivation.record(None, basis="consolidated", period_label="current",
                             derivation=trail)


def _cascade_trail(inputs: list[dict]) -> dict:
    """The shape `stages/note_sourced._fill_by_cascade` writes, via the same helper it uses."""
    return derivation.build(method="cascade:P2",
                            formula=" + ".join(str(i.get("label")) for i in inputs),
                            inputs=inputs, result=Decimal("529841"), flags=["rung:P2"])


def test_a_cascade_input_carries_the_line_it_refers_to(monkeypatch):
    """THE HOP. A parent assembled from a sub-line offers that sub-line as a clickable concept."""
    from app.stages.note_sourced import _cascade_input

    class _Def:
        label = "PBT note — operating-expense depreciation callout"

    term = {"ref": "sub__pbt_oper_exp_depreciation", "value": Decimal("529841"),
            "sign": 1, "role": "required"}
    got = _cascade_input(term, by_key={}, defs={"sub__pbt_oper_exp_depreciation": _Def()})

    assert got["canonical_key"] == "sub__pbt_oper_exp_depreciation"
    assert got["label"] == _Def.label, "the raw key reached the screen instead of the line's name"
    assert got["value"] == Decimal("529841")
    assert got["counted"] is True


def test_the_label_falls_back_without_inventing_one():
    """A referenced line that is configured but never extracted still reads as a name, and one
    that is neither falls back to its key rather than to a blank."""
    from app.stages.note_sourced import _cascade_input

    class _Row:
        source_label = "Depreciation charge printed on the page"
        values: dict = {}

    by_definition = _cascade_input({"ref": "sub__x", "value": None, "sign": 1, "role": "any_of"},
                                   by_key={"sub__x": _Row()}, defs={})
    assert by_definition["label"] == _Row.source_label

    bare = _cascade_input({"ref": "sub__y", "value": None, "sign": 1, "role": "any_of"},
                          by_key={}, defs={})
    assert bare["label"] == "sub__y"

    # A `const` term refers to no line, so it must NOT be offered as a clickable concept.
    const = _cascade_input({"ref": "", "value": Decimal("5"), "sign": 1, "role": "adjustment"},
                           by_key={}, defs={})
    assert "canonical_key" not in const
    assert const["label"] == "fixed number"


def test_a_deduction_is_carried_as_a_deduction():
    """A rung that SUBTRACTS an input must say so, or the contributions column will not add up to
    the figure printed above it."""
    from app.stages.note_sourced import _cascade_input

    got = _cascade_input({"ref": "sub__z", "value": Decimal("100"), "sign": -1,
                          "role": "adjustment"}, by_key={}, defs={})
    assert got["deducted"] is True


def test_the_key_survives_to_the_contribution_the_ui_reads():
    """THE BOUNDARY THAT MATTERS. `_contribution` used to blank every input's key, so fixing the
    trail alone would have changed nothing a reader could click."""
    trail = _cascade_trail([{"label": "PBT note — operating-expense depreciation callout",
                             "canonical_key": "sub__pbt_oper_exp_depreciation",
                             "value": "529841", "counted": True, "deducted": False}])

    formula, contributions = derivation.merge_for_basis(_store(trail), "consolidated")

    assert formula
    assert len(contributions) == 1
    got = contributions[0]
    assert got["canonical_key"] == "sub__pbt_oper_exp_depreciation", (
        "the sub-line's key was dropped on the way to the inspector, so the hop is not clickable")
    assert got["v1"] == 529841.0
    assert got["label"] == "PBT note — operating-expense depreciation callout"


def test_a_note_row_input_still_carries_no_key():
    """THE DISTINCTION, and why the fix is in the data rather than in the renderer. A note row is
    a place in a document, not a configured line — there is nothing to navigate to — so passing a
    key through for it would offer the reader a link to nowhere."""
    trail = note_sourced.trail(
        rollup="sum", item_label="PBT note — total depreciation", amount=Decimal("587417"),
        inputs=[{"label": "Depreciation of property, plant and equipment", "value": "306456",
                 "counted": True, "provenance": {"page_index": 246}},
                {"label": "Depreciation of right-of-use assets", "value": "280961",
                 "counted": True, "provenance": {"page_index": 246}}])

    _formula, contributions = derivation.merge_for_basis(_store(trail), "consolidated")

    assert len(contributions) == 2
    assert [c["canonical_key"] for c in contributions] == [None, None]
    # …and each still carries the page, which is the end of the chain.
    assert all(c["source"] for c in contributions)


def test_the_whole_chain_on_a_real_filing():
    """END TO END, on the reference filing: a main line names a sub-line, and that sub-line's own
    trail names the printed rows with their pages.

    Skipped rather than failed where the filing is not checked out — `_filings/` and `_run8/` hold
    client documents and are gitignored, so this cannot be a hard dependency of the suite.
    """
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    pdf = root.parent / "_run8" / "laisun.pdf"
    if not pdf.exists():
        pytest.skip("the reference filing is not checked out")
    pytest.importorskip("fitz")

    from app.api.routes.extractions import _serialize_rows
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    seed = root / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"
    cfg = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    settings.extraction.llm_mapping = False
    doc, _ctx = run_extraction(pdf.read_bytes(), filename="laisun.pdf",
                               ontology=build_working_view(cfg), template=None, line_items=cfg)
    rows = _serialize_rows(doc)
    by_key = {r["canonical_key"]: r for r in rows if r.get("canonical_key")}

    parent = by_key.get("is_pl__deprec_and_impairment_oper_exp")
    assert parent and parent.get("derivation"), "the parent carries no trail on this filing"
    _f, contributions = derivation.merge_for_basis(parent["derivation"], "consolidated")
    keyed = [c for c in contributions if c.get("canonical_key")]
    assert keyed, f"the parent names no sub-line: {[c['label'] for c in contributions]}"

    # …and the hop lands somewhere that can be traced further.
    child_key = keyed[0]["canonical_key"]
    child = by_key.get(child_key)
    assert child, f"the parent points at {child_key}, which is not in the run's rows"
    assert child.get("derivation"), f"{child_key} is the end of the chain with no citations"
    _f2, child_contributions = derivation.merge_for_basis(child["derivation"], "consolidated")
    assert any(c.get("source") for c in child_contributions), (
        f"{child_key} names its inputs but none carries a page: {child_contributions}")
