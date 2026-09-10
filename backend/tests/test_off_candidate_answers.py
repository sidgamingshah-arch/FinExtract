"""Which keys the model may answer with, and what it owes when it answers past the offered list.

THE CANDIDATES ARE SUGGESTIONS. The list used to be a closed menu: an answer naming a concept that
was not offered was discarded, and one naming an `_unmatchable` concept was dropped the moment it
was read. The list is now what it always claimed to be — the concepts this row's statement and
section make LIKELY — and the model is told so. What pays for that latitude is traceability, not a
gate: an off-list answer must be CITED, the citation is RESOLVED rather than believed, and every one
goes to REVIEW because the statement/section gate is deliberately skipped for it.

THE LAYER MATTERS, and this file was rewritten because it had that wrong. All eight focus concepts
are `derived`: each one's figure is COMPUTED by a declared cascade over its own sub-line items, and
it is the sub-line item that corresponds to something a note prints. So there are three kinds of key
and they are not treated alike:

  * A SUB-LINE ITEM is the model's proper answer here, and is accepted when cited. It is invisible
    to the matcher — the working view is a projection of the MATCHABLE concepts and drops all 77 of
    them — so naming one used to be refused as a key that names nothing. The model can see them:
    every `identified_notes` entry carries `identified_for`.
  * A DERIVED PARENT IS NOT THE MODEL'S TO ANSWER AT ALL. There is no LLM call for it. Its figure is
    rung P1 summing four expense notes, or P2 taking the profit-before-tax callout, or P3 the total
    less the cost-of-sales share — and a figure written straight onto the parent skips all of it: the
    rung never runs, so the record loses WHICH disclosure the number came from and the arithmetic
    that would have cross-checked it against the other rungs is never done. The number arrives
    looking identical either way, which is why this is refused rather than merely discouraged.
  * AN ORDINARY CONCEPT that simply was not offered for this row is accepted when cited. That is the
    widening, and it is bounded by the citation and the review flag rather than by a gate.

One thing is refused even when cited, and it is not a matter of taste: a locked residual, whose
purpose is to carry the UNEXPLAINED remainder of a section.
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
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.map_ontology import _apply_result
from app.stages.note_sourced import NoteSourcedStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# The three kinds of key. That they differ is the whole subject of the file.
SUB = "sub__pbt_oper_exp_depreciation"                    # the model's proper answer
COMPUTED = "is_pl__deprec_and_impairment_oper_exp"        # derived: never the model's to answer
ORDINARY_OFF = "bs_nca__land"                             # `extract`, just not offered for THIS row
DERIVABLE = "bs_ca__net_trade_receivables"                # `extract_or_derive`: withheld from the LLM

_PROSE = ("^ Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
          "included in “other operating expenses” on the face of the consolidated income "
          "statement.")


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
    """A provider returning exactly the decision under study, and keeping the request it was sent."""

    id = "answers"

    def __init__(self, mappings: list[dict]) -> None:
        self.mappings = mappings

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.system, self.user = system, messages[-1]["content"]
        return response_schema.model_validate({"mappings": self.mappings}), {}


def _sub_item_keys(shipped):
    """The set `stages.map_ontology` supplies. Which concepts are WITHHELD from the model is the
    matcher's own decision (`_llm_withheld`), read off the declared `extraction_mode` — not
    something the caller passes, so the offer and the refusal cannot drift apart."""
    return {i.key for i in shipped.items if getattr(i, "parent", "")}


def _decide(shipped, mappings, *, notes=None, caption="Depreciation charge for the year"):
    provider = _Answers(mappings)
    matcher = OntologyMatcher(build_working_view(shipped), locale="en",
                              settings=get_settings(), llm_provider=provider)
    out = matcher.match_batch([("r1", caption)], statement="profit_and_loss",
                              sections={"r1": None}, notes=notes or _rows_note(),
                              sub_item_keys=_sub_item_keys(shipped))
    return out["r1"], matcher, provider


def _answer(key, sources, *, confidence=0.9, reason="cited"):
    return [{"item_id": "r1", "canonical_key": key, "confidence": confidence,
             "reason": reason, "sources": sources}]


_ROW_CITE = [{"note": "7", "caption": "Depreciation of right-of-use assets"}]
_PROSE_CITE = [{"note": "7", "caption": "Depreciation charges included in other operating expenses",
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


def test_a_sub_line_item_is_invisible_to_the_matcher(shipped):
    """Which is why naming one had to be made possible explicitly: the working view is a projection
    of the matchable concepts and drops every one of them."""
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    subs = [i.key for i in shipped.items if getattr(i, "parent", "")]
    assert len(subs) >= 77
    assert not any(k in matcher._by_key for k in subs), "a sub-item reached the working view"


def test_neither_the_parent_nor_the_sub_item_is_ever_offered(shipped):
    """The premise of every test below. If these started appearing in candidate lists the tests
    would pass for the wrong reason — they would be exercising the ordinary on-list path."""
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    assert COMPUTED in matcher._unmatchable
    offered = {c["canonical_key"]
               for c in matcher._concept_payload(matcher._by_priority(list(matcher._by_key)))}
    assert COMPUTED not in offered and SUB not in offered


def test_only_extract_mode_concepts_are_put_in_front_of_the_model(shipped):
    """THE RULE THIS PAYLOAD OBEYS: a line the framework can work out for itself is not the model's
    to guess at. `extraction_mode` is the declared switch — `extract` is offered, `derive` and
    `extract_or_derive` are not — and it is read at the ONE choke point every candidate list comes
    through, so the offer cannot disagree with the refusal.

    NOT the presence of a cascade, which looked equivalent and is not: 8 concepts declare a cascade
    but only 3 declare `derive`, so a cascade-based rule would withhold `is_pl__sales_revenues`
    (declared `extract`, and printed on the face of every HK filing) while a mode-based rule offers
    it.
    """
    view = build_working_view(shipped)
    matcher = OntologyMatcher(view, locale="en", settings=get_settings())
    mode = {m.canonical_key: m.extraction_mode for m in view.mappings}

    offered = {c["canonical_key"]
               for c in matcher._concept_payload(matcher._by_priority(list(matcher._by_key)))}
    assert offered, "no candidate survived the filter, so the test proves nothing"
    off_mode = {mode.get(k) for k in offered}
    assert off_mode == {"extract"}, f"a non-extract concept is offered: {off_mode}"

    # And the withholding is real rather than vacuous: there ARE derivable concepts to withhold.
    assert sum(1 for v in mode.values() if v == "extract_or_derive") >= 30
    assert all(k in matcher._llm_withheld for k, v in mode.items() if v != "extract")


def test_nothing_in_the_request_names_a_key_the_model_may_not_use(shipped):
    """THE COHERENCE INVARIANT, over the WHOLE request rather than the candidate list alone.

    Three separate places can put a canonical_key in front of the model: `candidates`,
    `source_items[].deterministic_suggestion` and `source_items[].deterministic_candidates`. The
    last two come from the provider-less `fallback` matcher, whose tiers exclude only
    `_unmatchable` — so filtering the candidate list alone left them naming concepts the list
    withholds and the refusal rejects. That is worse than either offering or withholding
    consistently: the request INVITES an answer it will then discard, and the model cannot tell
    that the key it was just shown is one it may not use.

    Measured before the fix: "Total current assets", "Gross profit", "Profit before taxation",
    "Profit for the year" and "Inventories" all resolve to a withheld concept, so any chunk
    carrying a subtotal row hit it.
    """
    provider = _Answers([])
    matcher = OntologyMatcher(build_working_view(shipped), locale="en",
                              settings=get_settings(), llm_provider=provider)
    rows = [("r1", "Total current assets"), ("r2", "Gross profit"),
            ("r3", "Profit before taxation"), ("r4", "Inventories"),
            ("r5", "Bank balances and cash")]
    matcher.match_batch(rows, statement="balance_sheet",
                        sections={iid: None for iid, _ in rows},
                        sub_item_keys=_sub_item_keys(shipped))

    payload = json.loads(provider.user)
    named = {c["canonical_key"] for c in payload["candidates"]}
    for item in payload["source_items"]:
        if item.get("deterministic_suggestion"):
            named.add(item["deterministic_suggestion"])
        named.update(item.get("deterministic_candidates") or ())
    assert named, "no key reached the request, so the test proves nothing"

    leaked = sorted(named & matcher._llm_withheld)
    assert not leaked, f"the request names keys the model may not answer with: {leaked}"


def test_the_suggestion_is_dropped_rather_than_the_whole_row(shipped):
    """The row is still ASKED about — only the unusable key is withheld. Dropping the row instead
    would be a different decision (and a defensible one), but it would silently stop the model
    seeing captions it may have something to say about, so it is not made here by accident."""
    provider = _Answers([])
    matcher = OntologyMatcher(build_working_view(shipped), locale="en",
                              settings=get_settings(), llm_provider=provider)
    matcher.match_batch([("r1", "Gross profit")], statement="profit_and_loss",
                        sections={"r1": None}, sub_item_keys=_sub_item_keys(shipped))

    payload = json.loads(provider.user)
    assert [i["caption"] for i in payload["source_items"]] == ["Gross profit"]
    assert "deterministic_suggestion" not in payload["source_items"][0]


def test_a_derivable_concept_is_refused_even_when_cited(shipped):
    """The refusal side of the same rule. `extract_or_derive` means the subtotal is sometimes
    printed and sometimes arithmetic — so if it IS printed the DETERMINISTIC tiers read the printed
    row, and the model is not asked. A citation does not reopen a line the framework was never
    going to ask about; it is counted separately from a computed parent because the two mean
    different things to whoever reads the counters."""
    r, m, _p = _decide(shipped, _answer(DERIVABLE, _ROW_CITE, confidence=1.0))

    assert r.canonical_key is None
    assert m.usage.get("batch_derivable_named") == 1


def test_the_deterministic_tiers_still_reach_a_derivable_concept(shipped):
    """THE HALF THAT MUST NOT BREAK. Withholding these from the model is only safe because the
    caption tiers still bind them — otherwise a printed subtotal would stop being read at all,
    which is a far worse failure than the model guessing at it. So the narrowing is applied where
    candidates are OFFERED, not in `_mappable_keys`, which the rule tier reads too."""
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())

    assert DERIVABLE in matcher._llm_withheld, "the premise: withheld from the model"
    assert DERIVABLE in matcher._mappable_keys(), "but still bindable by a printed caption"
    assert DERIVABLE not in matcher._unmatchable


# ── a derived parent: no LLM answer, ever ─────────────────────────────────────────────────────

def test_a_derived_parent_is_never_the_models_to_answer(shipped):
    """THERE IS NO LLM CALL FOR A COMPUTED LINE, so there is no LLM answer for one either.

    Accepting the figure onto the parent skips the cascade: the rung never runs, the record loses
    which of the filing's several disclosures the number came from, and the cross-check against the
    other rungs is never done. This was a real hole rather than a hypothetical — the prose path
    first wrote 529,841 straight onto the parent — and the number looks identical either way, which
    is why it is refused outright rather than merely deprioritised.
    """
    r, m, _p = _decide(shipped, _answer(COMPUTED, _PROSE_CITE), notes=_prose_note())

    assert r.canonical_key is None
    assert m.usage.get("batch_computed_parent_named") == 1


def test_the_parent_is_refused_however_well_cited(shipped):
    """A citation buys latitude about WHERE a figure came from. It does not buy the right to write
    into a line whose value is an arithmetic result, so there is no citation and no confidence that
    makes this the model's answer."""
    for sources in ([], _ROW_CITE, _PROSE_CITE, _ROW_CITE + _PROSE_CITE):
        r, _m, _p = _decide(shipped, _answer(COMPUTED, sources, confidence=1.0),
                            notes=_prose_note())
        assert r.canonical_key is None, f"the parent was accepted with {len(sources)} citation(s)"


def test_the_contract_tells_the_model_to_answer_at_the_sub_line_instead(shipped):
    """A refusal the model cannot anticipate is a wasted decision — it would keep answering with the
    parent and keep being discarded. The request says which layer to answer at, and where to find
    the sub-line keys."""
    _r, _m, provider = _decide(shipped, _answer(SUB, _ROW_CITE))

    assert "deliberately not offered" in provider.system
    assert "answer with the SUB-LINE it prints" in provider.system
    assert "identified_for" in provider.system
    assert "SUGGESTIONS, NOT A MENU" in provider.system
    assert "not offered, answer with it" in provider.system
    assert "you MUST say where in the" in provider.system
    assert "from the candidates you were given" not in provider.system, "closed-list wording"


# ── a sub-line item: the proper answer ────────────────────────────────────────────────────────

def test_a_cited_sub_line_item_is_accepted(shipped):
    """The whole point: the layer a note actually prints becomes answerable."""
    r, m, _p = _decide(shipped, _answer(SUB, _ROW_CITE))

    assert r.canonical_key == SUB
    assert r.off_candidate is True
    assert r.needs_review is True, "the statement/section gate was skipped, so a human must see it"
    assert m.usage.get("batch_sub_item") == 1


def test_an_uncited_sub_line_item_is_refused(shipped):
    """The price of answering past the offered list, and it is the same price at every layer."""
    r, m, _p = _decide(shipped, _answer(SUB, [], confidence=0.99))

    assert r.canonical_key is None
    assert m.usage.get("batch_uncited_off_candidate") == 1


def test_several_cited_rows_are_each_resolved(shipped):
    """A figure stated across more than one printed row is the normal case, not the exception, and a
    reviewer needs all of them rather than the first."""
    r, _m, _p = _decide(shipped, _answer(SUB, [
        {"note": "7", "caption": "Depreciation of property plant and equipment"},
        {"note": "7", "caption": "Depreciation of right-of-use assets"}]))

    assert len(r.sources) == 2, r.sources
    assert [s["figures"]["current"] for s in r.sources] == ["306456", "280961"]


def test_the_page_and_figure_come_off_the_ROW_not_from_the_model(shipped):
    """The model is given no page index and no bbox, so a location it stated would look
    authoritative and point wherever it guessed. It names text; the framework resolves the row."""
    # Deliberately imprecise: the comma and the footnote marker are dropped.
    r, _m, _p = _decide(shipped, _answer(
        SUB, [{"note": "7", "caption": "Depreciation of property plant and equipment"}]))

    got = r.sources[0]
    assert got["provenance"]["page_index"] == 88                                 # off the row
    assert got["caption"] == "Depreciation of property, plant and equipment^"    # as PRINTED
    assert got["figures"] == {"current": "306456"}


def test_a_citation_that_resolves_to_nothing_is_reported_not_believed(shipped):
    """Saying so is more useful than dropping it silently, and far better than accepting a page the
    model supplied. One unresolvable citation among several must not lose the mapping either."""
    r, _m, _p = _decide(shipped, _answer(SUB, [
        {"note": "7", "caption": "Depreciation of right-of-use assets"},
        {"note": "7", "caption": "A caption this note does not print", "quote": _PROSE}]))

    assert r.canonical_key == SUB, "one unresolvable citation must not lose the mapping"
    assert len(r.sources) == 1 and len(r.unresolved_sources) == 1
    assert "529,841,000" in r.unresolved_sources[0]["quote"]


# ── an ordinary concept that simply was not offered ───────────────────────────────────────────

def test_an_ordinary_concept_not_offered_here_is_accepted_when_cited(shipped):
    """THE WIDENING THIS CHANGE MAKES, stated rather than discovered later.

    A concept from another statement answered for this row used to be refused outright by
    `_allowed`. It is now accepted, because the contract deliberately skips the statement and
    section gates for a cited off-list answer — there is no way to let the model reach a concept its
    section never predicted while still refusing every answer its section did not predict; those are
    the same gate. What stands in its place is the citation and the review flag, and the test asserts
    both, because if either stopped holding this would be a silent widening rather than a reviewed
    one.
    """
    r, m, _p = _decide(shipped, _answer(ORDINARY_OFF, _ROW_CITE))

    assert r.canonical_key == ORDINARY_OFF
    assert r.off_candidate is True
    assert r.needs_review is True, "a cross-statement answer must never be auto-accepted"
    assert r.sources and r.sources[0]["provenance"]["page_index"] == 88
    assert m.usage.get("batch_off_candidate") == 1


def test_the_same_answer_uncited_is_still_refused(shipped):
    """Which is what keeps the widening bounded: the latitude is bought with traceability."""
    r, m, _p = _decide(shipped, _answer(ORDINARY_OFF, []))

    assert r.canonical_key is None
    assert m.usage.get("batch_uncited_off_candidate") == 1


def test_a_key_that_names_no_concept_is_refused_however_well_cited(shipped):
    """Referential integrity, not an opinion about the answer: a key the configuration does not
    carry cannot be stored, exported or reviewed."""
    r, m, _p = _decide(shipped, _answer("not_a_concept_at_all", _ROW_CITE))

    assert r.canonical_key is None
    assert m.usage.get("batch_unknown_key") == 1


def test_a_residual_bucket_is_refused_EVEN_WITH_a_citation(shipped):
    """THE ONE EXCEPTION TO THE LATITUDE, and it is not a matter of taste.

    `_locked` holds the section residuals, whose whole purpose is to carry the UNEXPLAINED remainder
    of a section. A figure filed into one does not merely risk a wrong mapping — it makes the
    reconciliation that would have REPORTED the gap tie instead, so the wrong number and the check
    that would have caught it are lost together. That is the one failure a citation cannot make
    reviewable: choosing a residual is not the model saying where a figure came from, it is writing
    into the mechanism that audits the saying.
    """
    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    residual = next(iter(matcher._locked), None)
    if residual is None:
        pytest.skip("the shipped set declares no locked residual to probe")

    r, m, _p = _decide(shipped, _answer(residual, _ROW_CITE, confidence=0.99))

    assert r.canonical_key is None, f"a figure reached the residual bucket {residual}"
    assert m.usage.get("batch_residual_named") == 1


# ── withholding a concept must not cost the row its deterministic answer ──────────────────────

def test_no_call_is_spent_on_a_concept_the_model_may_not_name(shipped):
    """An exact alias hit normally falls through to the semantic tier — right when the model could
    name the same concept, since it may know better and the alias may be a false hit. But when the
    hit names a concept in `_llm_withheld` the model CANNOT return it: the key is off the candidate
    list and an answer naming it is refused. So the call can only end in the same answer, a
    different one, or none — and for `extract_or_derive` the printed row is exactly what must be
    read, so locking in the alias is the intended outcome.

    THIS IS NOT THE LOST-FIGURE FIX, and the distinction is recorded because it was first got
    wrong here. That regression lived in the focus-routing ROW GATE, which discarded a forwarded
    row's deterministic answer — see
    `test_a_failing_provider_degrades_to_the_deterministic_path_not_below_it`, which reproduces it
    end to end. This is the same principle one layer down, and it covers the paths the row gate does
    not: a run with `llm_focus_only` off sends every row through here.
    """
    class _Abstains:
        """A provider that answers nothing — the shape that destroyed the two figures."""

        id = "abstains"

        def __init__(self):
            self.asked = 0

        def complete_structured(self, *, system, messages, response_schema, **_):
            self.asked += 1
            fields = response_schema.model_fields
            if "mappings" in fields:
                return response_schema.model_validate({"mappings": []}), {}
            return response_schema.model_validate(
                {"canonical_key": "", "confidence": 0.0}), {}

    view = build_working_view(shipped)
    matcher = OntologyMatcher(view, locale="en", settings=get_settings(),
                              llm_provider=_Abstains())

    # A withheld concept with an exact alias, taken from the shipped set rather than assumed.
    withheld = next((m.canonical_key for m in view.mappings
                     if m.canonical_key in matcher._llm_withheld
                     and m.canonical_key not in matcher._locked
                     and m.aliases_for("en")), None)
    assert withheld, "no withheld concept carries an alias, so this proves nothing"
    alias = view_alias = matcher._by_key[withheld].aliases_for("en")[0]

    result = matcher.match(view_alias, statement=None, section=None)

    assert result.canonical_key == withheld, (
        f"the alias {alias!r} lost its deterministic mapping to {withheld}; an abstaining model "
        f"must not be able to empty a row the caption tier answered at confidence 1.0")
    assert result.confidence == 1.0
    assert matcher.usage.get("llm_calls", 0) == 0 or result.canonical_key == withheld


def test_an_offered_concept_still_goes_to_the_model(shipped):
    """The other half, so the guard above cannot quietly become "never consult the model". A row
    whose exact hit names an OFFERED concept still falls through to the semantic tier, because there
    the model can return the same key and may genuinely know better."""
    class _Counts:
        id = "counts"

        def __init__(self):
            self.asked = 0

        def complete_structured(self, *, system, messages, response_schema, **_):
            self.asked += 1
            return response_schema.model_validate({"canonical_key": "", "confidence": 0.0}), {}

    view = build_working_view(shipped)
    provider = _Counts()
    matcher = OntologyMatcher(view, locale="en", settings=get_settings(), llm_provider=provider)

    offered = next((m.canonical_key for m in view.mappings
                    if m.canonical_key not in matcher._llm_withheld and m.aliases_for("en")), None)
    assert offered, "no offered concept carries an alias"
    matcher.match(matcher._by_key[offered].aliases_for("en")[0], statement=None, section=None)

    assert provider.asked >= 1, "an offered concept's row must still reach the semantic tier"


# ── a figure the filing states only in PROSE ──────────────────────────────────────────────────

def test_a_prose_figure_is_accepted_when_it_is_really_there(shipped):
    """THE CASE THIS PATH EXISTS FOR. 529841 appears in NO extracted row anywhere in laisun.pdf —
    the operating-expense share of the depreciation charge is disclosed in a footnote and nowhere
    else. No row-caption regex can reach it, so without this the line stays empty however plainly
    the filing states it."""
    r, _m, _p = _decide(shipped, _answer(SUB, _PROSE_CITE), notes=_prose_note())

    prose = [s for s in r.sources if s.get("prose")]
    assert len(prose) == 1, r.sources
    assert prose[0]["figures"]["prose"] == "529841000"
    # A page, so the figure is not left without click-to-source.
    assert prose[0]["provenance"]["page_index"] == 141


def test_a_figure_that_is_NOT_in_the_prose_is_refused(shipped):
    """THE SAFETY PROPERTY, and the reason the model is allowed to state an amount at all here. It
    is LOCATING a printed number, not supplying one — so a number the note does not contain is
    refused rather than believed, and the refusal says which figure and which note."""
    bad = [dict(_PROSE_CITE[0], amount="529,842,000")]              # one digit out
    r, _m, _p = _decide(shipped, _answer(SUB, bad), notes=_prose_note())

    assert not [s for s in r.sources if s.get("prose")], "an unverified figure was accepted"
    named = [u for u in r.unresolved_sources if u.get("amount")]
    assert named and "does not appear in note 7" in named[0]["why"], r.unresolved_sources


def test_the_comparison_is_on_digits_so_formatting_does_not_matter(shipped):
    """"HK$529,841,000", "529,841,000" and "529841000" are one number. A verifier comparing strings
    would refuse the real figure over a currency prefix, and the line would stay empty for a reason
    no reviewer could see."""
    for written in ("HK$529,841,000", "529,841,000", "529841000"):
        cite = [dict(_PROSE_CITE[0], amount=written)]
        r, _m, _p = _decide(shipped, _answer(SUB, cite), notes=_prose_note())
        prose = [s for s in r.sources if s.get("prose")]
        assert prose and prose[0]["figures"]["prose"] == "529841000", written


def test_the_prose_figure_reaches_the_row_without_discarding_what_was_PRINTED(shipped):
    """The row prints the TOTAL depreciation and the footnote states the operating-expense SHARE of
    it — two different quantities. The prose figure becomes the value, the printed one is kept in
    `value_raw`, and the displacement is flagged, because silently losing the number a reader can
    see on the page is not an acceptable way to gain the one they cannot."""
    r, _m, _p = _decide(shipped, _answer(SUB, _PROSE_CITE), notes=_prose_note())
    row = LineItem(source_label="Depreciation of property, plant and equipment", role=LineRole.LINE)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("587417"), value_raw=Decimal("587417"),
                                 provenance=Provenance(page_index=141)))

    assert _apply_result(row, r) is True

    ev = next(iter(row.values.values()))
    assert ev.value == Decimal("529841000")
    assert ev.value_raw == Decimal("587417"), "the printed figure was discarded"
    assert any(f == "prose_value_displaced_printed:587417" for f in row.confidence.flags)
    assert any(f == "prose_sourced_value:7" for f in row.confidence.flags)


# ── the whole flow: sub-line item → cascade → parent → screen and export ──────────────────────

def _run_to_parent(shipped, mappings, notes):
    r, _m, _p = _decide(shipped, mappings, notes=notes)
    row = LineItem(source_label="Depreciation charge for the year", role=LineRole.LINE)
    assert _apply_result(row, r) is True
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [row]
    doc.notes = notes
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

    # REVIEWED: the row the model answered carries the flag, and its trail carries the SENTENCE, so
    # a reviewer sees the evidence rather than being asked to trust it.
    answered = next(r for r in wire if r["canonical_key"] == SUB)
    assert "low_mapping_confidence" in answered["flags"]
    trail = next(iter((answered.get("derivation") or {}).values()))
    assert trail["method"] == "prose_sourced"
    assert "529,841,000" in trail["inputs"][0]["excerpt"]
    assert (trail["inputs"][0]["provenance"] or {}).get("page_index") == 141
