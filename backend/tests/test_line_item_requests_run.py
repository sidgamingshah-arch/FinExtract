"""THE LINE-ITEM REQUEST, END TO END — the only call a run makes about a figure.

NEW FILE -> backend/tests/test_line_item_requests_run.py

WHAT THIS REPLACES. `test_off_candidate_answers.py`, `test_batch_context_payload.py` and
`test_llm_mapping_path.py` covered a ROW request: printed captions chunked by statement, a
candidate list to choose from, a statement/section gate to grade the answer against, and an
"off-candidate" contract for an answer that went past the list. That request is gone
(`stages.map_ontology` makes no provider call), and most of what those files pinned describes a
problem that does not exist at this level — there is no list to force-fit, because the LINE IS
GIVEN and only its location is asked for.

WHAT SURVIVES THE MOVE, and is pinned here, is the half that was never about candidates:

  * A CITATION IS RESOLVED, NOT BELIEVED. The page and the figure come off the extracted ROW; the
    model is given no page index and no bbox.
  * A CITATION THAT RESOLVES TO NOTHING IS REPORTED, not dropped and not believed.
  * A PROSE AMOUNT IS THE ONE FIGURE THE MODEL MAY STATE, it is verified against the note's own
    text, and it is SCALED — a sentence states an amount in full where a table states it in the
    statement's units.
  * A DERIVED PARENT IS NEVER ASKED ABOUT. Its figure is its declared cascade's.
  * THE TWO ROUTES DO NOT OVERWRITE EACH OTHER (`note_sourced._llm_holds`).
  * ROW TERMS ARE A FLOOR on the answer: a caption sharing no subject word with the line's own
    `row_terms` is the container, not the content.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable, Provenance)
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import line_item_llm, line_item_requests
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

SUB = "sub__pbt_oper_exp_depreciation"                 # a part: asked about
PARENT = "is_pl__deprec_and_impairment_oper_exp"       # derived: never asked about


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _note(number: str, title: str, rows: list[tuple[str, str]], *, prose: str = ""):
    table = NotesTable(note_number=number, title=title, page_index=9, source_text=prose)
    for caption, amount in rows:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


def _doc(*notes):
    doc = DocumentModel(filename="f.pdf")
    doc.notes = list(notes)
    # One face row so the run has a period label and a prevailing basis to file against.
    face = LineItem(source_label="Profit before tax", canonical_key="pl_profit_before_tax")
    face.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                  value=Decimal("1000"), value_raw=Decimal("1000"),
                                  provenance=Provenance(page_index=1)))
    doc.line_items = [face]
    return doc


class Answers:
    """A provider that answers whatever the test hands it, and records what it was asked.

    `only_asked` filters the answers down to the keys the request actually carried, which is how a
    well-behaved model replies. A test about an answer naming a line nobody asked about turns it
    off — that is the case being measured.
    """

    id = "answers"

    def __init__(self, answers: list[dict], only_asked: bool = True):
        self._answers = answers
        self._only_asked = only_asked
        self.systems: list[str] = []
        self.requests: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.systems.append(system)
        self.requests.append(json.loads(messages[-1]["content"]))
        asked = {e["key"] for e in self.requests[-1]["line_items"]}
        mine = ([a for a in self._answers if a["key"] in asked]
                if self._only_asked else list(self._answers))
        return response_schema.model_validate({"answers": mine}), {
            "model": "answers", "input_tokens": 1, "output_tokens": 1}


@pytest.fixture(autouse=True)
def _restore_extraction_settings():
    """`get_settings()` is a PROCESS-WIDE singleton, and `_run` writes to it.

    Without this, a test here that sets `llm_request_grouping="identical"` leaves it set for
    every test that runs afterwards in the same process — measured, it made
    `test_row_terms_gate::test_the_config_default_is_the_attributable_mode` fail with
    `'identical' == 'none'` while passing on its own. A test that changes another file's result is
    worse than a failing one, because the failure is attributed to innocent code.
    """
    from app.config import get_settings as _gs

    ex = _gs().extraction
    before = {k: getattr(ex, k) for k in
              ("llm_mapping", "llm_focus_only", "llm_focus_keys", "llm_request_grouping")}
    try:
        yield
    finally:
        for k, v in before.items():
            setattr(ex, k, v)


def _run(shipped, doc, answers, only_asked: bool = True, **extraction):
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    # SCOPED TO THE LINE UNDER TEST. With no focus the shipped set plans one request per asked-about
    # line — 519 of them — and none of these tests is about that number; each would spend 519 spy
    # calls to assert something about one. A test may widen it by passing `llm_focus_only=False`.
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [SUB]
    for name, value in extraction.items():
        setattr(ctx.settings.extraction, name, value)
    provider = Answers(answers, only_asked=only_asked)
    ctx.registry.register("llm", "answers", lambda: provider)
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    return ctx, provider


def _value(doc, key, period="current"):
    for li in doc.line_items:
        if li.canonical_key != key:
            continue
        for ev in (li.values or {}).values():
            if str(getattr(ev, "period_label", "") or "") == period:
                return ev.value
    return None


# ── the figure comes off the ROW ───────────────────────────────────────────────────────────────

def test_the_page_and_figure_come_off_the_row_not_from_the_model(shipped):
    """The model names a note and a caption. Everything else is recovered here."""
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    ctx, _p = _run(shipped, doc, [
        {"key": SUB, "sources": [{"note": "7",
                                  "caption": "Depreciation of property, plant and equipment"}],
         "confidence": 0.9, "reason": "the PBT note's own depreciation row"}])

    assert _value(doc, SUB) == Decimal("5170")
    trail = [li for li in doc.line_items if li.canonical_key == SUB][0]
    # The page is the extracted row's, which the model was never given.
    written = str(trail.derivation)
    assert "9" in written and "line_item_llm" in written

    # AND THE INPUT'S OWN FIGURE SURVIVES THE TRIP TO A CONTRIBUTION.
    #
    # This is asserted through `derivation.merge_for_basis` rather than on the raw trail because
    # that is what the inspector, the trace and the workbook all read, and the raw trail looked
    # perfectly healthy while the contribution came out empty: the inputs were filed under
    # `amount` and `derivation._signed` reads `value`, so every LLM-located line displayed its
    # formula with blank figures beside each name. A substring check on `str(derivation)` cannot
    # see that — the number is in there either way — so the assertion has to go through the
    # consumer.
    from app.services.derivation import merge_for_basis

    formula, contributions = merge_for_basis(trail.derivation, "consolidated")
    assert formula, "the trail recorded no formula"
    assert contributions, "the trail produced no contributions"
    assert [c["v1"] for c in contributions] == [5170.0], (
        f"the cited row's own figure did not reach the contribution: {contributions}")


def test_a_citation_that_resolves_to_nothing_is_reported_not_believed(shipped):
    doc = _doc(_note("7", "PROFIT BEFORE TAX", [("Auditor's remuneration", "120")]))
    ctx, _p = _run(shipped, doc, [
        {"key": SUB, "sources": [{"note": "7", "caption": "Depreciation charge for the year"}],
         "confidence": 0.9}])

    assert _value(doc, SUB) is None
    assert any("citation NOT resolved" in line for line in ctx.logs), ctx.logs[-6:]


def test_an_empty_sources_is_a_valid_answer_and_writes_nothing(shipped):
    """"This filing does not state it" is an answer, not a failure — and nothing is invented."""
    doc = _doc(_note("7", "PROFIT BEFORE TAX", [("Auditor's remuneration", "120")]))
    ctx, _p = _run(shipped, doc, [{"key": SUB, "sources": [], "confidence": 0.0}])

    assert _value(doc, SUB) is None
    assert not any("FAILED" in line for line in ctx.logs), ctx.logs[-6:]


# ── several rows, one line ─────────────────────────────────────────────────────────────────────

def test_components_are_summed_with_their_declared_signs(shipped):
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170"),
                      ("Depreciation of investment property", "830"),
                      ("Depreciation capitalised into development costs", "200")]))
    ctx, _p = _run(shipped, doc, [
        {"key": SUB, "role": "component", "signs": [1, 1, -1], "confidence": 0.8,
         "sources": [{"note": "7", "caption": "Depreciation of property, plant and equipment"},
                     {"note": "7", "caption": "Depreciation of investment property"},
                     {"note": "7", "caption": "Depreciation capitalised into development costs"}]}])

    assert _value(doc, SUB) == Decimal("5800")


def test_rows_declared_whole_are_not_added_together(shipped):
    """A face line and the note total behind it are one figure printed twice. Adding them
    overstates the line, which is the one error the arithmetic downstream cannot see."""
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170"),
                      ("Total depreciation", "5170")]))
    ctx, _p = _run(shipped, doc, [
        {"key": SUB, "role": "whole", "confidence": 0.9,
         "sources": [{"note": "7", "caption": "Depreciation of property, plant and equipment"},
                     {"note": "7", "caption": "Total depreciation"}]}])

    assert _value(doc, SUB) == Decimal("5170")


# ── prose ──────────────────────────────────────────────────────────────────────────────────────

def test_a_prose_amount_is_verified_against_the_note_text_and_scaled(shipped):
    """The one figure the model may state. It is checked against the note's own words, and it is
    divided by the statement's scale — a sentence states an amount in FULL where a table states it
    in the statement's units, and publishing one unscaled is a thousandfold error."""
    prose = ("^ Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
             "included in 'other operating expenses'")
    doc = _doc(_note("7", "PROFIT BEFORE TAX", [("Auditor's remuneration", "120")], prose=prose))
    from app.core.models.document import UnitContext
    doc.unit_context = UnitContext(scale_factor=1000)
    ctx, _p = _run(shipped, doc, [
        {"key": SUB, "confidence": 0.7,
         "sources": [{"note": "7", "caption": "", "quote": prose, "amount": "529,841,000"}]}])

    assert _value(doc, SUB) == Decimal("529841")


def test_a_prose_figure_that_is_not_in_the_note_is_published_but_flagged(shipped):
    """A STATED AMOUNT THAT IS NOT IN THE NOTE'S TEXT, and the posture that changed.

    This asserted `_value(doc, SUB) is None` — the figure was refused outright, on the rule that
    "a figure the model stated rather than located is refused". That rule caught a fabricated
    RMB 6.6 bn on the corpus run, and it also threw away the model's arithmetic whenever one term
    of a sum could not be confirmed, publishing a silent partial sum instead.

    THE DECISION IS NOW THE OTHER WAY: the term is counted, the figure is published, and the line
    is flagged for review with the unverified term NAMED. A reader sees the whole claim and which
    part of it nobody could confirm. The refusal is no longer silent in either direction — it used
    to lose the figure without saying so, and it would now publish it without saying so, so the
    flag is the load-bearing half of this test.
    """
    prose = "Depreciation charges of approximately HK$529,841,000 are included in expenses"
    doc = _doc(_note("7", "PROFIT BEFORE TAX", [("Auditor's remuneration", "120")], prose=prose))
    ctx, _p = _run(shipped, doc, [
        {"key": SUB, "confidence": 0.7,
         "sources": [{"note": "7", "caption": "", "quote": prose, "amount": "999,999,000"}]}])

    # It is still reported as not located — that fact has not changed.
    assert any("citation NOT resolved" in line for line in ctx.logs), ctx.logs[-6:]
    row = next((li for li in doc.line_items if li.canonical_key == SUB), None)
    assert row is not None, "the answer left no trace on the row at all"
    flags = " ".join(row.confidence.flags)
    # …and the line cannot read as settled.
    assert "llm_unverified_term" in flags or "llm_answered_nothing_located" in flags, flags


# ── what is never asked about ──────────────────────────────────────────────────────────────────

def test_a_derived_parent_is_never_in_a_request(shipped):
    """Its figure is its declared cascade's. A number written straight onto the parent skips every
    rung, so the run loses which disclosure it came from and the cross-check is never done."""
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    _ctx, provider = _run(shipped, doc, [])

    offered = {e["key"] for request in provider.requests for e in request["line_items"]}
    assert SUB in offered, "the part is the model's proper answer and must be asked about"
    assert PARENT not in offered
    assert not line_item_requests.asked_about(
        next(i for i in shipped.items if i.key == PARENT))


def test_an_answer_naming_a_line_this_request_did_not_ask_about_is_refused(shipped):
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    ctx, _p = _run(shipped, doc, [
        {"key": PARENT, "confidence": 0.9,
         "sources": [{"note": "7",
                      "caption": "Depreciation of property, plant and equipment"}]}],
        only_asked=False)

    assert _value(doc, PARENT) is None
    assert any("foreign_key_ignored" in line for line in ctx.logs), ctx.logs[-6:]


# ── the two routes ─────────────────────────────────────────────────────────────────────────────

def test_the_answer_is_stamped_so_the_deterministic_route_defers_to_it(shipped):
    """`note_sourced._llm_holds` reads `confidence.method`. One spelling for "the model answered
    this", shared with the review queue and the export — not a flag invented in the stage."""
    from app.stages.note_sourced import _llm_holds

    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    _ctx, _p = _run(shipped, doc, [
        {"key": SUB, "confidence": 0.9,
         "sources": [{"note": "7",
                      "caption": "Depreciation of property, plant and equipment"}]}])

    row = [li for li in doc.line_items if li.canonical_key == SUB][0]
    assert _llm_holds(row, "consolidated", "current")


# ── grouping, focus, and the request itself ────────────────────────────────────────────────────

def test_the_notes_travel_once_beside_the_lines_not_inside_each_one(shipped):
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    _ctx, provider = _run(shipped, doc, [], llm_focus_only=False,
                          llm_request_grouping="identical")

    for request in provider.requests:
        assert "notes" in request and isinstance(request["notes"], list)
        for entry in request["line_items"]:
            # The line says WHICH notes are its own; it does not carry their content.
            assert "notes_supplied" in entry
            assert not any(isinstance(v, list) and v and isinstance(v[0], dict)
                           for v in entry.values()), entry


def test_focus_keys_restrict_which_lines_are_asked_about(shipped):
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    _ctx, focused = _run(shipped, doc, [], llm_focus_only=True, llm_focus_keys=[SUB])
    _ctx2, wide = _run(shipped, _doc(), [], llm_focus_only=False)

    offered = {e["key"] for request in focused.requests for e in request["line_items"]}
    everything = {e["key"] for request in wide.requests for e in request["line_items"]}
    assert SUB in offered and SUB in everything
    assert len(offered) < len(everything)
    assert everything == {i.key for i in shipped.items
                          if line_item_requests.asked_about(i)}


def test_the_reply_contract_goes_first_and_the_master_prompt_after_it(shipped):
    """The contract is the shape of the answer and is not configurable; everything after it is the
    deployment's own judgement wording. An admin may change any rule of judgement without being
    able to produce a reply the system cannot use."""
    doc = _doc(_note("7", "PROFIT BEFORE TAX", [("Auditor's remuneration", "120")]))
    _ctx, provider = _run(shipped, doc, [])

    system = provider.systems[0]
    assert system.startswith(line_item_llm.REPLY_CONTRACT)
    assert "Policies to follow:" in system


def test_no_provider_means_a_fully_deterministic_run(shipped):
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.settings.llm.provider = "stub"
    LineItemLlmStage().run(doc, ctx)

    assert _value(doc, SUB) is None
    assert any("stub llm provider" in line for line in ctx.logs), ctx.logs


def test_a_request_that_fails_leaves_its_lines_to_the_deterministic_route(shipped):
    """The whole request is the unit of failure: nothing has been written when it raises, so the
    lines fall to the declared route exactly as they would with no provider at all."""
    class Refuses:
        id = "refuses"

        def complete_structured(self, **_):
            raise RuntimeError("413 request too large")

    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = False
    ctx.registry.register("llm", "refuses", lambda: Refuses())
    ctx.settings.llm.provider = "refuses"
    LineItemLlmStage().run(doc, ctx)

    assert _value(doc, SUB) is None
    assert any("FAILED" in line for line in ctx.logs), ctx.logs[-6:]
    assert any("calls=0" in line for line in ctx.logs), ctx.logs[-3:]


# ── what the run record says about itself ──────────────────────────────────────────────────────

def test_a_run_that_located_a_line_does_not_claim_to_be_deterministic(shipped):
    """`map_ontology` reports `deterministic` unconditionally and correctly — no row is offered to
    a model. But the extraction screen raises a banner on exactly that label reading "No language
    model was configured for this run", and on a run where requests were made and answered that is
    false. So this stage names itself."""
    doc = _doc(_note("7", "PROFIT BEFORE TAX",
                     [("Depreciation of property, plant and equipment", "5170")]))
    ctx, _p = _run(shipped, doc, [
        {"key": SUB, "confidence": 0.9,
         "sources": [{"note": "7",
                      "caption": "Depreciation of property, plant and equipment"}]}])

    assert ctx.mapping_strategy == "llm_line_items"
    assert "located by the model" in ctx.mapping_strategy_reason


def test_a_run_where_nothing_was_located_stays_deterministic(shipped):
    """Calls made, nothing came back usable. That run IS deterministic, and the reason says the
    provider was asked — which is the difference between "asked and got nothing" and "not asked"."""
    doc = _doc(_note("7", "PROFIT BEFORE TAX", [("Auditor's remuneration", "120")]))
    ctx, _p = _run(shipped, doc, [{"key": SUB, "sources": [], "confidence": 0.0}])

    assert ctx.mapping_strategy != "llm_line_items"
    assert "no line item located" in ctx.mapping_strategy_reason


# ── what an author wrote reaches the request ───────────────────────────────────────────────────

def test_the_lines_own_disambiguation_prose_reaches_the_request(shipped):
    """`section_disambiguation` is the sentence that separates a line from its look-alike, and it
    is consumed rather than decorative: the prose in the configuration is the prose in the request.

    MOVED FROM `test_binding_order.py`, where it read the sentence out of the candidate payload a
    per-caption call was handed. That was the field's only reader, and prose needs a reader that
    reads. It now travels from the LINE ITEM as `how_to_tell_it_apart` — which is where it belongs:
    two note headings differing by one word is exactly the case a locator gets wrong, and the
    author's sentence is the only thing that settles it.
    """
    from app.services.line_item_llm import line_item_payload

    item = next(i for i in shipped.items if line_item_requests.asked_about(i))
    item = item.model_copy(update={
        "section_disambiguation": "MARKER: the current one is the one due within a year."})

    entry = line_item_payload(item, ("7",))
    assert entry["how_to_tell_it_apart"].startswith("MARKER:")

    # …and a line that authors none carries no such key, rather than an empty string the model has
    # to interpret.
    bare = item.model_copy(update={"section_disambiguation": ""})
    assert "how_to_tell_it_apart" not in line_item_payload(bare, ("7",))


def test_what_a_line_declares_about_its_row_reaches_the_request(shipped):
    """`row_terms` is the author's statement of the CONTENT's name as against the note HEADING's —
    the container-for-content error this whole layer exists around. It is also the floor the answer
    is checked against, so withholding it would grade the model against a constraint it was never
    given."""
    from app.services.line_item_llm import line_item_payload

    item = next(i for i in shipped.items
                if line_item_requests.asked_about(i)
                and getattr(getattr(i, "note_source", None), "row_terms", None))
    entry = line_item_payload(item, ("7",))

    assert entry["row_is_called"] == list(item.note_source.row_terms)
    assert entry["key"] == item.key and entry["notes_supplied"] == ["7"]
