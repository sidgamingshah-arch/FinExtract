"""WHAT REACHES A LINE ITEM'S FIGURE, AND WHAT IS REFUSED — the part of the old contract that
outlived the request it was written for.

THIS FILE USED TO BE ABOUT "OFF-CANDIDATE ANSWERS". A row request showed the model a caption and a
list of candidate concepts, and the list was deliberately not a closed menu: an answer naming a
concept that was NOT offered was accepted, provided it was CITED, and the citation was resolved
rather than believed. Twenty-one tests pinned that latitude and its price.

THE LATITUDE HAS NO SUBJECT ANY MORE, because there is no list. Nothing asks a model which concept
a printed row is (`stages.map_ontology` makes no provider call); a request names a LINE ITEM and
asks where its figure is printed. An answer cannot be "off-candidate" when there were no
candidates, so those tests went with the request — their live successors are in
`tests/test_line_item_requests_run.py`, which covers the resolution contract at the level it now
operates: the page and figure come off the ROW, an unresolved citation is reported, an empty
`sources` is a valid answer, components sum with their signs, and a prose amount is verified
against the note's own text.

WHAT SURVIVES HERE is what was never about candidates at all:

  * THE ARCHITECTURE the whole thing rests on — every focus line is `derived` over sub-line items,
    and a PART is an ordinary line item rather than a second-class kind.
  * THE BOUNDARY of what is asked about, which is `line_item_requests.asked_about` now that
    `mapping._llm_withheld` is retired — and the half that must not break with it: a concept the
    model is not asked about is STILL bindable by a printed caption.
  * THE DIGITS COMPARISON that makes a prose citation verifiable at all.
  * THE JOURNEY FROM A PART TO ITS PARENT — the point of solving sub-line items: a part the model
    filled joins its parent's cascade, the parent's provenance is the RUNG that computed it, and
    the figure is printed and exported while still being reviewable.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable, Provenance)
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.line_item_requests import asked_about
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage
from app.stages.note_sourced import NoteSourcedStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# The three kinds of key. That they differ is the whole subject of the file.
SUB = "sub__pbt_oper_exp_depreciation"                    # the model's proper answer
COMPUTED = "is_pl__deprec_and_impairment_oper_exp"        # derived: never the model's to answer
ORDINARY_OFF = "bs_nca__land"                             # `extract`, just not offered for THIS row
DERIVABLE = "bs_ca__net_trade_receivables"                # `extract_or_derive`: not asked about

# DELIBERATELY A SENTENCE THE DETERMINISTIC PROSE ROUTE DOES NOT MATCH. `note_source.prose_any`
# now reads footnotes itself (see test_prose_sourced.py), and on the real wording — "…are INCLUDED
# IN other operating expenses…" — it fills this part with no provider call at all. That is the
# better outcome in production and it would make every test below pass for the wrong reason: the
# figure would be there whether the model answered or not. "relates to" is not one of the declared
# connectives, so `prose_any` stays silent and the model is the only thing that can fill the line —
# which is what these tests are about. The model can still cite it, because the citation is
# verified on the DIGITS appearing in the note's text.
_PROSE = ("^ Depreciation of approximately HK$529,841,000 relates to “other operating expenses” "
          "on the face of the consolidated income statement.")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _note_row(caption: str, amount: str) -> NoteItem:
    row = NoteItem(raw_label=caption)
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=88))
    return row


def _rows_note() -> list[NotesTable]:
    return [NotesTable(note_number="7", title="LOSS FROM OPERATING ACTIVITIES", items=[
        _note_row("Depreciation of property, plant and equipment^", "306456"),
        _note_row("Depreciation of right-of-use assets^", "280961")])]


def _prose_note() -> list[NotesTable]:
    """The footnote and nothing else — the case where no row carries the figure at all."""
    return [NotesTable(note_number="7", title="LOSS FROM OPERATING ACTIVITIES",
                       source_pages=[141], source_text=_PROSE, items=[])]


class _Answers:
    """A provider returning exactly the answer under study, and keeping the request it was sent."""

    id = "answers"

    def __init__(self, answers: list[dict]) -> None:
        self.answers = answers
        self.system = self.user = ""

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.system, self.user = system, messages[-1]["content"]
        return response_schema.model_validate({"answers": self.answers}), {}


def _run(shipped, answers, *, notes=None, extra_rows=()):
    """Run the line-item request stage over one filing, scoped to the line under test.

    Scoped with `llm_focus_keys` because the shipped set plans one request per asked-about line —
    519 of them — and nothing here is about that number.
    """
    doc = DocumentModel(filename="f.pdf")
    doc.notes = list(notes or _rows_note())
    doc.line_items = list(extra_rows)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [SUB]
    provider = _Answers(answers)
    ctx.registry.register("llm", "answers", lambda: provider)
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    return doc, ctx, provider


def _answer(key, sources, *, confidence=0.9, reason="cited"):
    return [{"key": key, "confidence": confidence, "reason": reason, "sources": sources}]


_ROW_CITE = [{"note": "7", "caption": "Depreciation of right-of-use assets"}]
_PROSE_CITE = [{"note": "7", "caption": "Depreciation relating to other operating expenses",
                "quote": _PROSE, "amount": "HK$529,841,000"}]


# ── the architecture everything here follows from ─────────────────────────────────────────────

def test_all_eight_focus_concepts_are_derived_over_sub_line_items(shipped):
    """THE FACT THE REST OF THE FILE DEPENDS ON, asserted so it cannot quietly stop being true. If
    one of these ever became a plain concept, its cascade would no longer govern and the refusal
    below would be wrong for it."""
    focus = ["is_pl__deprec_and_impairment_oper_exp", "is_pl__deprec_and_impairment_cos",
             "bs_ca__secur_and_other_fincl_assets_cp", "bs_nca__secur_and_other_fincl_assets_ltp",
             "notes__contingent_liabilities", "bs_nca__due_from_related_parties_ltp",
             "bs_ca__other_receivables_cp", "is_pl__sales_revenues"]
    by_key = {i.key: i for i in shipped.items}
    for key in focus:
        item = by_key[key]
        assert item.type == "derived", f"{key} is {item.type}, so its cascade would not govern"
        assert item.cascade, f"{key} is derived with no cascade to compute it"
        assert any(i.parent == key for i in shipped.items), f"{key} has no sub-line items"


def test_a_part_OF_a_line_is_still_a_line_item(shipped):
    """THERE IS NO "SUB-LINE ITEM" KIND, and this test used to assert the opposite.

    It read: "a sub-line item is invisible to the matcher … the working view is a projection of the
    matchable concepts and drops every one of them". That projection confused publication with
    recognition. `namespace` says WHERE a figure is published — which output column it lands in, and
    whether the config screen lets an author edit it — and it still does exactly that. Whether the
    engine may RECOGNISE a caption as that concept is a different question, and making it depend on
    publication left 77 real line items unrecognisable: no tier could bind them, `_concept_payload`
    could not offer them, and naming one was refused as a key that names nothing.

    They are ordinary concepts now, and nothing had to be authored to make that work — which is the
    sign the split was accidental. The resolve step already gives every one of them a statement, a
    section scope, a competitive priority and `extraction_mode: extract`.
    """
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    parts = [i.key for i in shipped.items if getattr(i, "parent", "")]
    assert len(parts) >= 77
    assert all(k in matcher._by_key for k in parts), "a declared line item is not a concept"
    # Every definition, not a projection of them.
    assert len(matcher._by_key) == len(shipped.items)


def test_a_line_the_model_is_not_asked_about_is_still_bindable_by_a_caption(shipped):
    """THE HALF THAT MUST NOT BREAK. Leaving a line out of the requests is only safe because the
    caption tiers still bind it — otherwise a printed subtotal would stop being read at all, which
    is a far worse failure than a model guessing at it.

    The boundary is `line_item_requests.asked_about`, which is the one place it is spelled now that
    `mapping._llm_withheld` is retired. That set had two readers — the candidate payload and the
    per-caption call's shortlist — and both went with the row request, so keeping it would have
    left the boundary asserted in a set nothing consults.

    THE SUBJECT IS THE DERIVED PARENT, and it used to be the `extract_or_derive` line below it.
    That narrowed when the derived-parent lock landed, and the narrowing is right for this request:
    `extract_or_derive` says the framework CAN work the figure out, and the old row request had to
    withhold such a line because a model shown a candidate list would GUESS at it. A line-item
    request asks something else — where is this line's figure printed — and a line whose figure is
    in a note is answerable whether or not the arithmetic could also reach it. A derived PARENT
    stays out for a reason that has nothing to do with guessing: its figure is its cascade's, and a
    number written straight onto it skips every rung.
    """
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    by_key = {i.key: i for i in shipped.items}

    assert not asked_about(by_key[COMPUTED]), "the premise: a derived parent is not asked about"
    assert COMPUTED in matcher._unmatchable, "and no caption may be bound to it either"

    # The `extract_or_derive` line IS asked about, and is still bindable by a printed caption —
    # which is the half that must not break whichever way the boundary moves.
    assert asked_about(by_key[DERIVABLE])
    assert DERIVABLE in matcher._mappable_keys(), "still bindable by a printed caption"
    assert DERIVABLE not in matcher._unmatchable


def test_the_comparison_is_on_digits_so_formatting_does_not_matter(shipped):
    """"HK$529,841,000", "529,841,000" and "529841000" are one number. A verifier comparing strings
    would refuse the real figure over a currency prefix, and the line would stay empty for a reason
    no reviewer could see."""
    from app.services.line_item_llm import SourceRef
    from app.services.note_sourced import resolve_sources

    for written in ("HK$529,841,000", "529,841,000", "529841000"):
        ref = SourceRef(**dict(_PROSE_CITE[0], amount=written))
        resolved, unresolved = resolve_sources([ref], _prose_note())
        assert not unresolved, (written, unresolved)
        assert resolved and resolved[0]["figures"]["prose"] == "529841000", written


def test_the_prose_figure_reaches_the_row_without_discarding_what_was_PRINTED(shipped):
    """The row prints the TOTAL depreciation and the footnote states the operating-expense SHARE of
    it — two different quantities. The prose figure becomes the value, the printed one is kept in
    `value_raw`, and the displacement is flagged, because silently losing the number a reader can
    see on the page is not an acceptable way to gain the one they cannot.

    AND IT IS SCALED, which the row path never did. `_apply_prose_value` wrote the amount as
    stated; a sentence states its figure in FULL where a table states it in the statement's units,
    so on a filing presented in thousands that was a thousandfold error on the face of the income
    statement. `stages.line_item_llm._write_prose` divides by `unit_context.scale_factor`, which is
    the same division the deterministic prose route already applied.
    """
    printed = LineItem(source_label="Depreciation of property, plant and equipment",
                       canonical_key=SUB, role=LineRole.LINE)
    printed.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("587417"), value_raw=Decimal("587417"),
                                     provenance=Provenance(page_index=141)))
    doc, _ctx, _p = _run(shipped, _answer(SUB, _PROSE_CITE), notes=_prose_note(),
                         extra_rows=[printed])

    ev = next(iter(printed.values.values()))
    assert ev.value == Decimal("529841000"), "the prose figure did not reach the row"
    assert ev.value_raw == Decimal("587417"), "the printed figure was discarded"
    assert any(f == "prose_value_displaced_printed:587417" for f in printed.confidence.flags)
    assert any(f == "prose_sourced_value:7" for f in printed.confidence.flags)


def _run_to_parent(shipped, answers, notes):
    """The two stages in pipeline order: the request, then the deterministic reader.

    That order is the non-interference rule (`note_sourced._llm_holds`): the request writes first,
    and the declared route then fills what was left empty rather than correcting it.
    """
    doc, _ctx, _p = _run(shipped, answers, notes=notes)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    NoteSourcedStage().run(doc, ctx)
    return doc, ctx


def _figure(doc, key):
    for li in doc.line_items:
        if li.canonical_key == key:
            for ev in li.values.values():
                if ev.value is not None:
                    return ev.value
    return None


def test_a_sub_item_the_model_filled_reaches_the_PARENT_through_its_cascade(shipped):
    """THE POINT OF SOLVING THE SUB-LINE ITEMS, end to end. The model answers with the sub-item and
    cites the footnote; the cascade then computes the parent from it.

    Before this, the cascade read only children its own `note_source` walk had filled — so a
    sub-item the model filled sat on a row nothing read, while the parent stayed empty.
    """
    doc, ctx = _run_to_parent(shipped, _answer(SUB, _PROSE_CITE), _prose_note())

    assert _figure(doc, SUB) == Decimal("529841000"), "the sub-item lost the figure"
    assert _figure(doc, COMPUTED) == Decimal("529841000"), "the cascade did not carry it up"
    assert any("joins the cascade" in line for line in ctx.logs)


def test_the_parents_provenance_is_the_RUNG_that_computed_it(shipped):
    """THE RUNG IS THE PROVENANCE, and it is exactly what answering on the parent would have thrown
    away. "P2" says the figure came from the profit-before-tax note's own operating-expense callout
    rather than from summing the four expense notes — a materially different basis that the number
    alone cannot show."""
    doc, _ctx = _run_to_parent(shipped, _answer(SUB, _PROSE_CITE), _prose_note())

    parent = next(li for li in doc.line_items if li.canonical_key == COMPUTED)
    trail = next(iter(parent.derivation.values()))
    assert trail["method"] == "cascade:P2", trail["method"]
    assert trail["formula"] == SUB


def test_it_is_flagged_for_review_AND_still_printed_and_exported(shipped):
    """WHAT WAS ASKED FOR, and the two halves are not in tension. `_serialize_rows` is the single
    boundary every screen and every export reads, and nothing downstream suppresses a reviewed
    value — so the figure is on the statement and in the workbook while `low_mapping_confidence`
    still puts it in front of a human."""
    from app.api.routes.extractions import _serialize_rows
    from app.services.periods import concept_value
    from app.services.rollups import figures_as_shown

    doc, _ctx = _run_to_parent(shipped, _answer(SUB, _PROSE_CITE), _prose_note())
    wire = _serialize_rows(doc)

    # EXPORTED, through both resolvers the export and the grid use.
    parent = next(r for r in wire if r["canonical_key"] == COMPUTED)
    assert [v["value"] for v in parent["values"]] == ["529841000"], "not on the wire"
    assert concept_value(wire, "consolidated", "current") is not None
    assert figures_as_shown(None, wire, "consolidated", "current")[COMPUTED] == 529841000.0

    # AUDITABLE: the row the model answered says the figure came from PROSE, and its trail carries
    # the SENTENCE, so a reviewer sees the evidence rather than being asked to trust it.
    #
    # NOT `low_mapping_confidence` ANY MORE, and the change is meaningful rather than cosmetic. That
    # flag came from the off-candidate path, which forces review because it SKIPS the
    # statement/section gate. A part is now an in-scope candidate, so the gate was applied and the
    # answer is graded like any other — at 0.85 against the auto-accept threshold. What still marks
    # the row is the prose provenance itself, which is the more precise signal: this figure was not
    # read off a row, and it displaced one that was.
    answered = next(r for r in wire if r["canonical_key"] == SUB)
    assert any(f.startswith("prose_sourced_value:") for f in answered["flags"]), answered["flags"]
    trail = next(iter((answered.get("derivation") or {}).values()))
    assert trail["method"] == "prose_sourced"
    assert "529,841,000" in trail["inputs"][0]["excerpt"]
    assert (trail["inputs"][0]["provenance"] or {}).get("page_index") == 141
