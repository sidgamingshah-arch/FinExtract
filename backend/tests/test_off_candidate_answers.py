"""The candidates are SUGGESTIONS. The model may answer past them, and must then say where from.

WHAT CHANGED AND WHY. The candidate list used to be a closed menu: an answer naming a concept that
was not offered was discarded, and an answer naming an `_unmatchable` one — a locked residual or a
`derive` concept — was dropped the moment it was read. That is why the two depreciation concepts
could never be answered however plainly a filing stated them: they are excluded from every
candidate list by construction ("a concept the model cannot see is a concept the model cannot
pick"), so the model had no key it was permitted to attach the figure to.

The list is now what it always claimed to be — the concepts this row's statement and section make
LIKELY — and the model is told so. What pays for that latitude is traceability, not a gate:

  * An off-candidate answer MUST be cited. `sources` names the note and the row caption(s), and an
    uncited one is discarded, because a mapping nobody can trace to a printed row is not reviewable
    and the statement/section gate that would have caught a wrong one is deliberately skipped.
  * SEVERAL ROWS ARE EXPECTED. A figure stated across more than one printed row is normal, and each
    row cited is resolved and reported separately.
  * THE CITATION IS RESOLVED, NEVER BELIEVED. The model is given no page index and no bounding box,
    so a location it stated would look authoritative and point wherever it guessed. It names text;
    `note_sourced.resolve_sources` matches that against the extracted rows and takes the page and
    the figure OFF THE ROW.
  * EVERY ONE GOES TO REVIEW. The gate was skipped, so a human sees it rather than the engine
    deciding it is fine.

Two things are still refused, and neither is an opinion about the answer: a key that names no
concept at all cannot be stored, exported or reviewed; and an off-candidate answer with no citation
cannot be traced.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# `_unmatchable` on the shipped set (`extraction_mode: derive`), so it is never offered as a
# candidate — which is exactly the concept this whole mechanism exists to make answerable.
OFF_LIST = "is_pl__deprec_and_impairment_oper_exp"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _note_row(caption: str, amount: str) -> NoteItem:
    row = NoteItem(raw_label=caption)
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=88))
    return row


def _notes() -> list[NotesTable]:
    return [NotesTable(note_number="7", title="LOSS FROM OPERATING ACTIVITIES", items=[
        _note_row("Depreciation of property, plant and equipment^", "306456"),
        _note_row("Depreciation of right-of-use assets^", "280961")])]


class _Answers:
    """A provider that returns exactly the decision the test wants to study."""

    id = "answers"

    def __init__(self, mappings: list[dict]) -> None:
        self.mappings = mappings

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.system = system
        self.user = messages[-1]["content"]
        return response_schema.model_validate({"mappings": self.mappings}), {}


def _decide(shipped, mappings, caption="Depreciation charge for the year"):
    provider = _Answers(mappings)
    matcher = OntologyMatcher(build_working_view(shipped), locale="en",
                              settings=get_settings(), llm_provider=provider)
    out = matcher.match_batch([("r1", caption)], statement="profit_and_loss",
                              sections={"r1": None}, notes=_notes())
    return out["r1"], matcher, provider


def test_the_concept_under_test_really_is_never_offered(shipped):
    """The premise. If this concept started appearing in candidate lists, every test below would
    pass for the wrong reason — they would be testing the ordinary on-list path."""
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    assert OFF_LIST in matcher._unmatchable
    offered = {c["canonical_key"]
               for c in matcher._concept_payload(matcher._by_priority(list(matcher._by_key)))}
    assert OFF_LIST not in offered


def test_a_cited_answer_past_the_candidates_is_accepted(shipped):
    """The whole point: a caption can now reach the concept its section never predicted."""
    r, matcher, _ = _decide(shipped, [{
        "item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.9,
        "reason": "the footnote assigns this charge to other operating expenses",
        "sources": [{"note": "7", "caption": "Depreciation of property plant and equipment"}]}])

    assert r.canonical_key == OFF_LIST
    assert r.off_candidate is True
    assert matcher.usage.get("batch_off_candidate") == 1


def test_several_cited_rows_are_each_resolved(shipped):
    """A figure stated across more than one printed row is the normal case, not the exception, and
    a reviewer needs all of them rather than the first."""
    r, _m, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.9,
        "sources": [{"note": "7", "caption": "Depreciation of property plant and equipment"},
                    {"note": "7", "caption": "Depreciation of right-of-use assets"}]}])

    assert len(r.sources) == 2, r.sources
    assert [s["figures"]["current"] for s in r.sources] == ["306456", "280961"]


def test_the_page_and_the_figure_come_off_the_ROW_not_from_the_model(shipped):
    """The model is given no page index and no bbox. A location it stated would look authoritative
    and point wherever it guessed, so the citation is text and the framework resolves it."""
    r, _m, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.9,
        # Deliberately imprecise: the comma and the footnote marker are dropped.
        "sources": [{"note": "7", "caption": "Depreciation of property plant and equipment"}]}])

    got = r.sources[0]
    assert got["provenance"]["page_index"] == 88          # off the extracted row
    assert got["caption"] == "Depreciation of property, plant and equipment^"   # as PRINTED
    assert got["figures"] == {"current": "306456"}


def test_a_citation_that_matches_no_row_is_reported_unresolved_not_believed(shipped):
    """A figure stated in PROSE belongs to no row, so there is nothing to match a caption against.
    Saying so is more useful than dropping it — and far better than accepting an unverified page."""
    r, _m, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.8,
        "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"},
                    {"note": "7",
                     "caption": "Depreciation charges included in other operating expenses",
                     "quote": "^ Depreciation charges of approximately HK$529,841,000 …"}]}])

    assert r.canonical_key == OFF_LIST, "one unresolvable citation must not lose the mapping"
    assert len(r.sources) == 1 and len(r.unresolved_sources) == 1
    assert "529,841,000" in r.unresolved_sources[0]["quote"]


def test_an_uncited_answer_past_the_candidates_is_discarded(shipped):
    """The price of the latitude. The statement and section gates are skipped for such an answer,
    so a citation is the only thing standing between it and an unreviewable mapping."""
    r, matcher, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.95, "sources": []}])

    assert r.canonical_key is None
    assert matcher.usage.get("batch_uncited_off_candidate") == 1


def test_a_key_that_names_no_concept_is_refused_however_well_cited(shipped):
    """Referential integrity, not an opinion about the answer: a key the configuration does not
    carry cannot be stored, exported or reviewed."""
    r, matcher, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": "not_a_concept_at_all", "confidence": 0.9,
        "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"}]}])

    assert r.canonical_key is None
    assert matcher.usage.get("batch_unknown_key") == 1


def test_every_off_candidate_answer_goes_to_review(shipped):
    """The gate that would have checked it was skipped, so a human sees it rather than the engine
    deciding it is fine — whatever confidence the model reported."""
    r, _m, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": OFF_LIST, "confidence": 1.0,
        "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"}]}])

    assert r.needs_review is True, "an unreviewed off-candidate answer has no gate behind it"


def test_the_contract_tells_the_model_the_candidates_are_suggestions(shipped):
    """The behaviour above is worthless if the request still says "choose from the candidates you
    were given" — the model would keep answering as though the list were closed."""
    _r, _m, provider = _decide(shipped, [{
        "item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.9,
        "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"}]}])

    system = provider.system
    assert "SUGGESTIONS, NOT A MENU" in system
    assert "not offered, answer with it" in system
    assert "you MUST say where in the" in system
    assert "from the candidates you were given" not in system, "the closed-list wording survived"


def test_a_wrong_statement_answer_is_now_ACCEPTED_when_cited_and_flagged(shipped):
    """THE WIDENING THIS CHANGE MAKES, stated rather than discovered later.

    A concept from another statement answered for this row used to be refused outright by
    `_allowed`. It is now accepted — because it is off-candidate, and the contract deliberately
    skips the statement and section gates for a cited off-candidate answer. There is no way to give
    the model latitude to reach a concept its section never predicted while still refusing every
    answer its section did not predict; those are the same gate.

    What stands in place of the gate is the citation and the review flag: the row the model named is
    resolved, the page and figure come off that row, and a human sees it. The test asserts all
    three, because if any of them stopped holding this would be a silent widening rather than a
    reviewed one.
    """
    r, _m, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": "bs_ca__net_trade_receivables", "confidence": 0.9,
        "reason": "cited to a real row",
        "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"}]}])

    assert r.canonical_key == "bs_ca__net_trade_receivables"
    assert r.off_candidate is True
    assert r.needs_review is True, "a cross-statement answer must never be auto-accepted"
    assert r.sources and r.sources[0]["provenance"]["page_index"] == 88


def test_the_same_answer_without_a_citation_is_still_refused(shipped):
    """Which is what keeps the widening bounded: the latitude is bought with traceability, so an
    uncited cross-statement answer is refused exactly as it was before."""
    r, matcher, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": "bs_ca__net_trade_receivables", "confidence": 0.9,
        "sources": []}])

    assert r.canonical_key is None
    assert matcher.usage.get("batch_uncited_off_candidate") == 1


def test_a_residual_bucket_is_refused_EVEN_WITH_a_citation(shipped):
    """THE ONE EXCEPTION TO THE LATITUDE, and it is not a matter of taste.

    `_locked` holds the section residuals, whose whole purpose is to carry the UNEXPLAINED
    remainder of a section. A figure filed into one does not merely risk a wrong mapping — it makes
    the reconciliation that would have REPORTED the gap tie instead. So the wrong number and the
    check that would have caught it are lost together, which is the one failure a citation cannot
    make reviewable.

    That is why this is refused where a computed concept is accepted: choosing a residual is not the
    model picking where a figure comes from, it is writing into the mechanism that audits the
    picking.
    """
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    residual = next(iter(matcher._locked), None)
    if residual is None:
        pytest.skip("the shipped set declares no locked residual to probe")

    r, m, _p = _decide(shipped, [{
        "item_id": "r1", "canonical_key": residual, "confidence": 0.99,
        "reason": "cited, and still not allowed",
        "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"}]}])

    assert r.canonical_key is None, f"a figure reached the residual bucket {residual}"
    assert m.usage.get("batch_residual_named") == 1


def test_a_computed_concept_is_the_case_the_latitude_is_FOR(shipped):
    """The distinction stated as a pair, so neither half can drift. Both concepts are
    `_unmatchable` and neither is ever offered; one is refused and one is accepted, and the
    difference is what the concept IS rather than how it was named."""
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    assert OFF_LIST in matcher._computed_only, "the probe concept is no longer a computed one"
    assert OFF_LIST not in matcher._locked

    cited = [{"item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.9,
              "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"}]}]
    assert _decide(shipped, cited)[0].canonical_key == OFF_LIST

    uncited = [{"item_id": "r1", "canonical_key": OFF_LIST, "confidence": 0.9, "sources": []}]
    assert _decide(shipped, uncited)[0].canonical_key is None
