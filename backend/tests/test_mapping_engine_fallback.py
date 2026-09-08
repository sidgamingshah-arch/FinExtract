"""The deterministic fallback engine: `extraction.mapping_engine = "line_items"`.

WHAT IT IS FOR, stated plainly because the temptation is to read it as progress. It maps every row
from the merged line-item configuration through `LineItemMatcher`, which ports the incumbent's
DETERMINISTIC tiers — the statement/section/veto gate, the alias index over every locale, label
ownership, `match_priority`, the mutually-confusable refusal, the regex/keyword rule tier — and is
held to 11,433 of 11,433 rulebook captions by `scripts/parity_line_items.py`.

It does NOT have the semantic tier, and that tier is where the value is. So this is a WEAKER path
by construction, kept for a run that must complete with an unreachable gateway, an expired key, or
nothing leaving the machine. A row the model would have decided is left UNMAPPED here rather than
guessed at by a weaker tier: an unmapped face row is visible in the review queue, a plausible
wrong concept is not.

Making this path look good on a particular filing by adding the captions that filing happens to
print would be exactly the overfitting the generic framework exists to avoid.
"""
from __future__ import annotations

from app.config import Settings
from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.line_item_matching import LineItemMatch
from app.services.mapping import MappingMethod
from app.stages.map_ontology import MapOntologyStage, _apply_result


def _row(label: str, section: str | None = None) -> LineItem:
    li = LineItem(source_label=label, section_hint=section)
    li.values["current"] = ExtractedValue(value=None, basis=Basis.CONSOLIDATED,
                                          period_label="current")
    return li


def _ctx(engine: str) -> PipelineContext:
    settings = Settings()
    settings.extraction.mapping_engine = engine          # type: ignore[assignment]
    ctx = PipelineContext(settings=settings)
    # The stage returns early without an ontology, so the fallback needs one present to be reached
    # at all — it does not READ it, but the guard above it does.
    ctx.ontology = object()
    return ctx


# ── the switch ───────────────────────────────────────────────────────────────────────────────────

def test_the_default_engine_is_the_incumbent():
    """The fallback must never become the default by accident: it is the weaker path."""
    assert Settings().extraction.mapping_engine == "ontology"


def test_only_two_engines_are_accepted():
    import typing

    from app.config import ExtractionSettings

    allowed = typing.get_args(ExtractionSettings.model_fields["mapping_engine"].annotation)
    assert set(allowed) == {"ontology", "line_items"}


# ── it maps, and it says how ─────────────────────────────────────────────────────────────────────

def test_the_fallback_maps_rows_from_the_merged_configuration():
    doc = DocumentModel(document_id="d1")
    doc.line_items.extend([_row("Total assets", "CURRENT ASSETS"),
                           _row("Cash and cash equivalents", "CURRENT ASSETS")])
    ctx = _ctx("line_items")

    MapOntologyStage().run(doc, ctx)

    mapped = [li.canonical_key for li in doc.line_items if li.canonical_key]
    assert mapped, "the fallback mapped nothing at all"
    assert "bs_ca__total_assets" in mapped


def test_the_run_records_that_the_weaker_engine_decided_it():
    """A run decided without the model must never read downstream as a full-capability one."""
    doc = DocumentModel(document_id="d1")
    doc.line_items.append(_row("Total assets", "CURRENT ASSETS"))
    ctx = _ctx("line_items")

    MapOntologyStage().run(doc, ctx)

    assert ctx.mapping_strategy == "line_items_deterministic"
    assert "semantic tier is not ported" in ctx.mapping_strategy_reason


def test_the_fallback_makes_no_llm_calls():
    doc = DocumentModel(document_id="d1")
    doc.line_items.extend([_row("Total assets", "CURRENT ASSETS"),
                           _row("Something no alias claims at all")])
    ctx = _ctx("line_items")

    MapOntologyStage().run(doc, ctx)

    assert ctx.llm_calls == 0


def test_a_caption_nothing_claims_is_left_unmapped_not_guessed():
    """The whole point. A plausible wrong concept is invisible; an unmapped row is not."""
    doc = DocumentModel(document_id="d1")
    doc.line_items.append(_row("Wholly unrecognisable prose about the directors"))
    ctx = _ctx("line_items")

    MapOntologyStage().run(doc, ctx)

    assert doc.line_items[0].canonical_key is None


def test_it_reports_progress_so_a_long_run_is_not_silent():
    seen: list[tuple[int, int, str]] = []
    doc = DocumentModel(document_id="d1")
    doc.line_items.extend([_row(f"Row {i}") for i in range(30)])
    ctx = _ctx("line_items")
    ctx.step_cb = lambda d, t, l: seen.append((d, t, l))

    MapOntologyStage().run(doc, ctx)

    assert seen, "the fallback reported no progress"
    assert seen[0][1] == 30 and seen[-1][0] == 30


# ── the adapter, and the one place either engine writes a row ────────────────────────────────────

def test_the_adapter_supplies_what_apply_reads():
    """`_apply_result` reads seven attributes off whatever it is handed."""
    got = LineItemMatch("bs_ca__cash_equivalents", MappingMethod.EXACT, 1.0).as_mapping_result()

    for field in ("canonical_key", "confidence", "method", "allocation_status", "reason",
                  "rerouted_from", "needs_review", "computed_claim"):
        assert hasattr(got, field), f"the adapter is missing {field}"
    assert got.allocation_status == "direct_exclusive"


def test_the_adapter_claims_no_allocation_status_for_a_rule_hit():
    """Set only for an exact hit, the one case its meaning is unambiguous."""
    got = LineItemMatch("bs_ca__cash_equivalents", MappingMethod.RULE, 0.95).as_mapping_result()

    assert got.allocation_status is None


def test_the_adapter_fabricates_no_reroute_or_computed_claim():
    """Honestly empty: this rulebook declares no concept families, and the computed-claim refusal
    is not reproduced — the `derive` locks keep such a concept out of every tier instead."""
    got = LineItemMatch("k", MappingMethod.EXACT, 1.0).as_mapping_result()

    assert got.rerouted_from is None
    assert got.computed_claim is None


def test_both_engines_write_a_row_through_one_function():
    """A second copy of the row-writing is how two engines start flagging differently."""
    li = _row("Cash")
    wrote = _apply_result(li, LineItemMatch("bs_ca__cash_equivalents", MappingMethod.RULE, 0.6,
                                            needs_review=True).as_mapping_result())

    assert wrote
    assert li.canonical_key == "bs_ca__cash_equivalents"
    assert li.confidence.method == "rule"
    assert "low_mapping_confidence" in li.confidence.flags


def test_a_computed_concept_printed_on_the_face_becomes_a_subtotal():
    """Carried over from the incumbent path unchanged by the hoist: an unclaimed face row with a
    value would otherwise be swept into its section's Others, adding a subtotal OF the section
    back INTO it under another name."""
    li = _row("Gross profit")
    li.role = LineRole.LINE

    class _Claimed:
        canonical_key = None
        computed_claim = "is_pl__gross_profit"

    assert _apply_result(li, _Claimed()) is False
    assert li.role is LineRole.SUBTOTAL
    assert "low_mapping_confidence" in li.confidence.flags
