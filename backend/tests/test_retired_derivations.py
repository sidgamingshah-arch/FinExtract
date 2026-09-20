"""THE FIVE DERIVATION SERVICES ARE GONE. This file is both the record of what was retired with
them and the guard that stops any of it being reinstated.

A DERIVATION, as the word is used here, is a service that COMPUTES a figure for a named output
line out of a hand-enumerated list of note titles, row captions and formula variants — instead of
the figure being read off a caption the CONFIGURATION describes. Five of them shipped, they were
the largest body of filing-specific logic left in the codebase, and they were the reason six of
the eight target output lines had no configuration-driven source at all. They are removed, and
`services/computed_paths.py` — which existed only to arbitrate between a derivation's answer and
the rulebook's own reading — is removed with them, along with the four settings that switched it.

THE COST IS ACCEPTED AND EXPLICIT. Cells that were filled by a derivation go BLANK until the
line-item set names the caption that carries them. That is the intended outcome of this change,
not a regression to be patched around. So, for the next author, the one sentence that matters:

    THESE FIGURES NOW HAVE TO COME FROM CONFIGURATION — an alias in the line-item set naming the
    printed caption, the section banner scoping it, the template's rollups checking it.
    REINSTATING A LITERAL IS NOT A FIX. Neither is a "just in case" fallback, a kept constant, or
    a stage that computes the same number under a new name. If a cell is blank, the answer is a
    line-item definition, and the blank cell is the signal that one is missing.

── WHAT WAS DELETED, AND HOW BIG IT WAS ─────────────────────────────────────────────────────────

    module (both halves, service + stage)          lines   enumerated entries
    app/services/deprec_impairment.py                532   ~208
      app/stages/deprec_impairment.py
    app/services/contingent_liabilities.py — the      761   ~172   ← THE NUMBER HALF ONLY
      NUMBER half only (see the split below)                        (the module SURVIVES)
    app/services/secur_fincl_assets.py               619   ~91
      app/stages/secur_fincl_assets.py
    app/services/related_party_receivables.py        690   ~73
      app/stages/related_party_receivables.py
    app/services/sales_revenues.py                   129   ~18
      app/stages/sales_revenues.py
                                                   2,731   ~562 total
    app/services/computed_paths.py                   269   (the arbiter — `apply_computed`,
                                                            `policy_from`, the precedence policy)

The canonical keys each one published, which are exactly the keys that now need configuration:

    deprec_impairment           is_pl__deprec_and_impairment_oper_exp
                                is_pl__deprec_and_impairment_cos
    secur_fincl_assets          bs_ca__secur_and_other_fincl_assets_cp
                                bs_nca__secur_and_other_fincl_assets_ltp
    related_party_receivables   bs_nca__due_from_related_parties_ltp
                                bs_ca__other_receivables_cp
    sales_revenues              is_pl__sales_revenues
    contingent_liabilities      notes__contingent_liabilities   (the number; the prose stays)

The two depreciation lines keep `kind: derived` on their own configured cascades (P1-P5 and
COS_P1/COS_P2). The other six lost their only source and are `extracted` now, because a `derived`
line with neither a cascade nor an implementer is refused outright by `LineItemDef` — which is
itself part of the guard: the schema will not let a derived line exist with nothing behind it.

── THE ONE THING THAT SURVIVES: contingent_liabilities IS SPLIT ─────────────────────────────────

`contingent_liabilities` is the only one of the five whose output is NARRATIVE — a paragraph for a
human to read, not a number — and disclosure items need a different form of output from a number
or a phrase. So the concept was split down the middle, and both halves matter:

  * ITS NUMBER IS GONE. `_quantifiable_total` assembled a single Decimal onto
    `notes__contingent_liabilities` out of 172 enumerated branches — 26 note titles, 23 amount
    labels, 14 non-exposure phrases, 72 classifier terms, 19 matter types — and the
    `total_quantifiable` field carried it on `ContingentLiabilitiesResult`. Both are removed. So
    is the MULTIPLE_CURRENCIES_NOT_AGGREGATED flag that withheld that total across unlike units,
    because with no total there is nothing to withhold. That figure comes from configuration now.

  * ITS PROSE STAYS. `summary_paragraph`, `classified_summary` (per type AND per currency AND per
    scale) and `unclassified_items` are untouched, `app/stages/contingent_liabilities.py` keeps
    its position in the pipeline after `link_notes`, and the narrative is still read by
    `app/api/routes/extractions.py` (`attach_contingent_explanation` at ~:1365 and the full
    per-period working published at ~:1424) and by `app/services/export.py` (~:805). Thirteen app
    files reference it. A removal that took the narrative with it would break the Disclosures
    screen and the export; one that left the number in place would not have done the job.

Test 4 below asserts BOTH halves in one place, and test 3 asserts the pipeline consequence: four
stage names gone, `contingent_liabilities` still in the list.

── WHAT WAS DELETED, WHAT IT PINNED, AND WHY THAT BEHAVIOUR IS GONE ─────────────────────────────

Six whole test modules — 109 tests, counted off the collected node ids — pinned nothing but a
removed derivation. They are RETIRED, not weakened and not ported: every one of them asserted that
a figure came out of an enumerated table of captions, which is the behaviour being removed.

tests/test_deprec_impairment.py — 30 tests
    Pinned the P1-P5 / COS_P1-COS_P2 formula variants and the note-dataset walk behind them: which
    of ~208 enumerated note titles and row captions fed which variant, the fallback order between
    variants when a note was absent, and the two figures landing on
    `is_pl__deprec_and_impairment_oper_exp` and `is_pl__deprec_and_impairment_cos`. Retired with
    `services/deprec_impairment.py`. The two output lines still exist and still say `derived`, but
    their cascades are now CONFIGURED (P1-P5, COS_P1/COS_P2 as line-item formulae), and the
    formula MECHANISM that evaluates them is live and separately covered — `services/derivation.py`
    and tests/test_formula.py. What is retired is the hand-enumerated INPUT list, not arithmetic.

tests/test_secur_fincl_assets.py — 12 tests
    Pinned the ~91 enumerated captions that assembled `bs_ca__secur_and_other_fincl_assets_cp` and
    `bs_nca__secur_and_other_fincl_assets_ltp` — the current/non-current split rules and the
    instrument-class enumeration that decided which note rows counted as securities. Retired with
    `services/secur_fincl_assets.py`. Both cells are blank until the line-item set names their
    captions; that is the accepted cost, recorded in tests/test_output_csv_ontology.py:329.

tests/test_related_party_receivables.py — 24 tests
    Pinned the ~73 enumerated related-party note captions behind
    `bs_nca__due_from_related_parties_ltp` and `bs_ca__other_receivables_cp`, including the
    subtraction that kept a related-party balance from being counted twice in other receivables.
    Retired with `services/related_party_receivables.py`. The netting IDEA survives where it
    belongs — as configurable netting rules on a line item (`app/schemas/line_items.py`) — but no
    test may pin this service's version of it.

tests/test_sales_revenues.py — 12 tests
    Pinned `is_pl__sales_revenues` being computed from ~18 enumerated revenue captions and the
    principal-operations vocabulary. Retired with `services/sales_revenues.py`. The VOCABULARY
    survives as configuration and is still guarded there: tests/test_spec_conformance.py keeps
    `test_sales_revenues_does_not_alias_total_operating_revenue`,
    `test_sales_revenues_keeps_its_principal_operations_vocabulary` and
    `test_sales_revenues_keeps_the_english_hkex_captions` — which is the shape every one of these
    lines should end in: aliases asserted in the configuration, not a service asserted in Python.

tests/test_computed_paths.py — 15 tests
    Pinned the ARBITER: `apply_computed` choosing between a derivation's figure and the rulebook's
    reading, `policy_from` resolving `computed_path_precedence` ("complex" / "generic" /
    "corroborate") and the per-key `computed_path_by_key` override, and the corroboration branch
    that reported a mismatch between the two answers. Retired with `services/computed_paths.py`
    (269 lines, referenced by 9 files). With one path to a figure there are no two answers to
    arbitrate, no precedence to set, and nothing to corroborate.

tests/test_derivation_stage_contract.py — 16 tests
    Pinned the SHARED CONTRACT the four derivation stages obeyed: that each ran after
    `link_notes`, published through `apply_computed`, honoured `complex_path_enabled` and its
    `complex_path_services` allow-list, and no-op'd cleanly when its concept was switched off.
    Retired because the contract has no parties: the four stages are deleted and the settings with
    them. The general stage protocol it partly exercised is live and covered by
    tests/test_docs_match_the_pipeline.py, which asserts the documented stage list against the
    real one in both directions — a retired stage left in the list fails there.

tests/test_contingent_liabilities.py — NOT deleted; TWO tests retired in place
    The module survives whole, because the prose survives whole, and it still covers the
    paragraph, both tables, the classification priority and the deduplication. Two tests asserted
    nothing but the removed figure and were retired rather than weakened, with the reason recorded
    at the top of that file:
      * `test_no_single_total_is_published_across_unlike_units` — pinned `total_quantifiable is
        None` plus the MULTIPLE_CURRENCIES_NOT_AGGREGATED flag. Neither the field nor the flag
        exists.
      * `test_a_single_currency_still_publishes_its_total` — pinned the one case that DID publish
        a total (CNY 500 + 200 == 700). There is no total to publish from any number of
        currencies.
    The currency-and-scale rule those two guarded is NOT lost. It is asserted through the
    `classified_summary` rows that remain the only answer given
    (`test_each_currency_gets_its_own_row_rather_than_one_converted_sum`,
    `test_the_same_currency_in_two_scales_is_not_added`) and through the `breakdown` rows in
    `test_two_currencies_stay_two_rows_because_the_concept_publishes_no_figure`.

COVERAGE COST, STATED PLAINLY: 109 tests retired, and with them every assertion that any of these
eight cells arrives filled. Nothing in the suite now proves a value for the six configuration-less
lines, because nothing in the product produces one. That is the change, not a hole in it.

WHAT DELIBERATELY STILL SAYS "derivation" AND IS FINE: `app/services/derivation.py` (the generic
formula evaluator a CONFIGURED cascade runs through), the `derivation` field on a line-item
definition and in the extraction response, and the "what stood here" comments left where each
removed path used to be — `app/config.py` (~:304 where the four settings were),
`app/core/pipeline.py` (~:74 where the four stages ran), `app/services/contingent_liabilities.py`
(~:483 where `_quantifiable_total` was). Those comments are the reason nobody reinstates any of
this, so nothing here scans for the word: the line the guards draw is not "the word is absent"
but "the CODE PATH is absent".
"""
from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest

# ── the retired surface, enumerated ─────────────────────────────────────────────────────────────
#
# Asserted UNIMPORTABLE rather than merely unused: an unused module is one import away from a
# reinstated derivation, and these lists are the whole retired surface. Both halves of each
# concept — the service that enumerated the captions and the stage that ran it — because leaving
# either behind leaves something for the next author to wire back up.
RETIRED_SERVICES = [
    "app.services.deprec_impairment",           # 532 lines, ~208 enumerated entries
    "app.services.secur_fincl_assets",          # 619 lines, ~91
    "app.services.related_party_receivables",   # 690 lines, ~73
    "app.services.sales_revenues",              # 129 lines, ~18
    "app.services.computed_paths",              # 269 lines — the arbiter between the two paths
]
RETIRED_STAGES = [
    "app.stages.deprec_impairment",
    "app.stages.secur_fincl_assets",
    "app.stages.related_party_receivables",
    "app.stages.sales_revenues",
]

# The four settings that switched the arbiter. See app/config.py ~:304 for the note left in place.
RETIRED_SETTINGS = [
    "complex_path_enabled",
    "complex_path_services",
    "computed_path_precedence",
    "computed_path_by_key",
]

# The stage names the four retired stages registered under, and the one that stays.
RETIRED_STAGE_NAMES = [
    "deprec_impairment",
    "secur_fincl_assets",
    "related_party_receivables",
    "sales_revenues",
]
SURVIVING_STAGE_NAME = "contingent_liabilities"

# The eight keys these services published onto. Six now have no source at all; the two
# depreciation lines have a configured cascade. Named here so the record is machine-checkable.
FORMERLY_DERIVED_KEYS = [
    "is_pl__deprec_and_impairment_oper_exp",
    "is_pl__deprec_and_impairment_cos",
    "bs_ca__secur_and_other_fincl_assets_cp",
    "bs_nca__secur_and_other_fincl_assets_ltp",
    "bs_nca__due_from_related_parties_ltp",
    "bs_ca__other_receivables_cp",
    "is_pl__sales_revenues",
    "notes__contingent_liabilities",
]

DELETED_SERVICE_NAMES = {
    "deprec_impairment",
    "secur_fincl_assets",
    "related_party_receivables",
    "sales_revenues",
    "contingent_liabilities",   # the number half — the seed must not name it as an implementer
}

_LINE_ITEMS_JSON = (
    Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    / "output_csv_hk_line_items.json")


# ── 1. THE MODULES ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("module", RETIRED_SERVICES + RETIRED_STAGES)
def test_the_retired_derivation_modules_are_not_findable(module):
    """`find_spec` rather than an import attempt, deliberately: a module can be absent from disk
    and still be importable through a package `__init__` re-export or a stale entry left in
    `sys.modules`. `find_spec` returning None is the stronger claim — there is no such module to
    find, so no call site can reach one."""
    assert importlib.util.find_spec(module) is None, (
        f"{module} is findable again — a derivation service was reinstated. Its figure must come "
        f"from the line-item configuration; see this file's docstring.")


def test_the_surviving_half_of_contingent_liabilities_is_still_there():
    """POSITIVE CONTROL for the test above, and the first half of the split. Without this, the
    parametrised absence check would pass just as happily on a repo that had deleted the narrative
    too — which is the failure mode this whole file exists to prevent."""
    assert importlib.util.find_spec("app.services.contingent_liabilities") is not None
    assert importlib.util.find_spec("app.stages.contingent_liabilities") is not None


# ── 2. THE SETTINGS ──────────────────────────────────────────────────────────────────────────────

def test_none_of_the_two_path_precedence_settings_exists():
    """Not absent-by-default, ABSENT. A `complex_path_enabled: bool = False` would still be a
    switch, and a switch is a standing invitation to write the other side of it — which is exactly
    how the codebase ended up with two ways to reach the same eight concepts. The second assertion
    is the broad one: a re-introduction would arrive under a new name, which an enumeration of the
    four old names cannot see."""
    from app.config import ExtractionSettings

    for name in RETIRED_SETTINGS:
        assert name not in ExtractionSettings.model_fields, name
    assert not [f for f in ExtractionSettings.model_fields
                if "complex_path" in f or "computed_path" in f]


def test_a_precedence_setting_cannot_be_smuggled_in_through_the_environment():
    """`extra` on the settings model decides whether `FINEX_EXTRACTION__COMPLEX_PATH_ENABLED=true`
    lands as an attribute something could read. Pydantic must refuse or drop it, not carry it — an
    attribute that only appears when an env var is set is the one a code path would branch on."""
    from app.config import ExtractionSettings

    settings = ExtractionSettings()
    for name in RETIRED_SETTINGS:
        assert not hasattr(settings, name), name


# ── 3. THE PIPELINE: BOTH HALVES OF THE SPLIT, IN ONE PLACE ─────────────────────────────────────

def test_the_four_derivation_stages_left_the_pipeline_and_the_disclosure_stage_did_not():
    """The whole change, asserted as one list.

    Removing a stage means deleting the module, taking it out of the order, and removing its
    settings — NOT leaving a stage that runs and does nothing. A no-op stage still appears in the
    progress stream, still logs a start and a done, and still reads to the next author as a step
    with a body to fill in. So the assertion is on the assembled order, and the length is pinned:
    17 stages, ingest through segment. A count makes an accidental re-addition fail here even if it
    arrives under a name this test never heard of.

    And `contingent_liabilities` MUST still be in it. Its number is gone; its narrative is not, it
    is built from linked notes, and it runs in the same position after `link_notes`.
    """
    from app.core.pipeline import default_pipeline

    names = [stage.name for stage in default_pipeline().stages]

    for retired in RETIRED_STAGE_NAMES:
        assert retired not in names, (
            f"stage {retired!r} is back in the pipeline. Nothing computes these figures now — "
            f"they come from the line-item configuration.")
    # 22, NOT 17. Five stages were added after this test was written, and ALL FIVE are the
    # opposite of what the four retired ones were — not one enumerates a caption or computes a
    # figure of its own:
    #
    #   * `assemble_components` adds up the rows the CONFIGURATION and the model between them
    #     identified as components of one line, which is how a line whose amount is printed as
    #     several rows gets filled without a service written for it.
    #   * `note_sourced` fills a line from the note rows ITS OWN CONFIGURATION names
    #     (`LineItemDef.note_source`). This is the mechanism the retired services took with them:
    #     they read those declarations, and after the removal ~650 authored patterns across 13
    #     sub-line items were read by nothing at all. The replacement enumerates no concept, so it
    #     serves an item nobody has authored yet — which is the distinction this test is guarding.
    #   * `note_tag_gate` reports 0 for a line whose configuration declares
    #     `llm_only_if_note_tagged` and whose row carries no note reference. It enumerates nothing
    #     either: it reads one boolean off each line's own definition and acts on the row's own
    #     citations, so a line nobody has flagged is untouched — the whole stage is a no-op on the
    #     shipped seed, which turns the flag on for nothing.
    #   * `line_item_llm` asks a model WHERE a named line's figure is printed, and the answer is a
    #     citation resolved against the extracted rows (`note_sourced.resolve_sources`) — the page
    #     and the figure come off the ROW. It enumerates nothing: it reads each line's own
    #     definition, aliases, row terms and selected notes. IT IS ALSO A REMOVAL, not only an
    #     addition — the batched ROW request it replaces lived inside `map_line_items`, which now
    #     makes no provider call at all, and the ~1,300 lines that served it are gone.
    #   * `shared_figures` finds one printed figure that TWO line items both claimed and asks the
    #     model which it belongs on. It computes nothing and enumerates nothing: the contest is
    #     identified by the figure's own page and box, or by the note rows its derivation trail
    #     names, so it serves any two lines whose vocabulary happens to overlap on a filing nobody
    #     has run. With no provider it moves no figure at all — it records the contest and flags
    #     the rows, which is why it is safe on by default.
    assert len(names) == 22, names
    assert "assemble_components" in names, (
        "the component assembly is gone, so a line item printed as several rows cannot be filled "
        "at all — that is not a return to the derivations, it is the loss of their replacement")
    assert SURVIVING_STAGE_NAME in names, (
        "the contingent-liabilities stage is gone — that removal took the NARRATIVE with it and "
        "breaks the Disclosures screen and the export. Only its number was to be removed.")
    # The position is load-bearing: the narrative is assembled out of linked notes.
    assert names.index(SURVIVING_STAGE_NAME) > names.index("link_notes")


# ── 4. THE SURVIVING PROSE CONTRACT ─────────────────────────────────────────────────────────────

def test_the_result_carries_no_derived_total_and_the_assembler_is_gone():
    """The number half, asserted on the type and on the module. The field is the visible half of
    the contract (a consumer reads `total_quantifiable`); `_quantifiable_total` is the 172-branch
    assembler behind it. Both, because keeping the private function "for reference" is how a
    literal comes back."""
    import app.services.contingent_liabilities as service
    from app.services.contingent_liabilities import ContingentLiabilitiesResult

    fields = set(getattr(ContingentLiabilitiesResult, "__dataclass_fields__", {}))
    fields |= set(getattr(ContingentLiabilitiesResult, "model_fields", {}))
    assert "total_quantifiable" not in fields, sorted(fields)
    assert not hasattr(service, "_quantifiable_total")

    # WHAT THIS GUARD IS ACTUALLY FOR, and why it is no longer a ban on the word "total".
    #
    # It used to read `assert not [f for f in fields if "total" in f]`. That caught the removed
    # field by its NAME, which was enough while no legitimate total existed. `detail_totals` is
    # one: the item-by-item table's own column sum, per currency and scale, for a reader to check
    # the table against the page. It is not the thing that was removed, and a name check cannot
    # tell the two apart — so the allowlist is explicit and the REAL protections are behavioural,
    # asserted in `test_contingent_detail_table.py`:
    #
    #   test_nothing_is_published_onto_the_line_item  — no figure reaches
    #       notes__contingent_liabilities; that number comes from configuration.
    #   test_no_sum_ever_crosses_a_currency           — no figure spans unlike units, which is
    #       what MULTIPLE_CURRENCIES_NOT_AGGREGATED used to have to withhold.
    #   test_an_item_with_no_amount_is_unpriced_and_not_zero — silence is never read as nil.
    #
    # A NEW total-shaped field therefore fails here until somebody adds it below deliberately, and
    # has to satisfy those three to be correct.
    allowed = {"detail_totals"}
    assert not [f for f in fields if "total" in f and f not in allowed], sorted(fields)


def test_a_two_currency_note_still_yields_two_rows_and_a_paragraph():
    """THE PROSE STILL WORKS END TO END — the half that had to survive.

    Two currencies under one guarantee caption. The concept publishes no figure, so these two rows
    ARE the answer: they must stay two rows (500 CNY and 300 USD are not 800 of anything, and there
    is no total to fold them into any more), and the paragraph a human reads must be non-empty. If
    this test fails, the removal took the narrative with it.
    """
    from app.core.models.document import DocumentModel
    from app.core.models.enums import Basis, LineRole
    from app.core.models.line_item import (
        ExtractedValue, NoteItem, NotesTable, UnitContext)
    from app.services.contingent_liabilities import compute

    def item(label: str, amount: str, currency: str) -> NoteItem:
        note_item = NoteItem(raw_label=label, group_hint="", role=LineRole.LINE)
        value = ExtractedValue(
            value=Decimal(amount), value_raw=Decimal(amount),
            basis=Basis("consolidated"), period_label="current",
            unit_ctx=UnitContext(currency=currency, scale_factor=Decimal("1")))
        note_item.values[value.key.model_dump_json()] = value
        return note_item

    doc = DocumentModel(notes=[NotesTable(note_number="35", title="对外担保", items=[
        item("公司担保", "500", "CNY"),
        item("公司担保", "300", "USD"),
    ])])

    result = compute(doc)[("consolidated", "current")]

    assert len(result.classified_summary) == 2, result.classified_summary
    assert sorted(g["currency"] for g in result.classified_summary) == ["CNY", "USD"]
    assert sorted(g["amount"] for g in result.classified_summary) == [
        Decimal("300"), Decimal("500")]
    assert result.summary_paragraph.strip(), "the narrative paragraph is empty"
    assert result.status == "COMPUTED"


# ── 5. THE SHIPPED CONFIGURATION NAMES NO DELETED SERVICE ───────────────────────────────────────

def test_no_shipped_line_item_names_a_deleted_service_as_its_implementer():
    """`implemented_by` was how a definition DESCRIBED a derivation someone else computed. A seed
    still naming one of the five would be a definition pointing at nothing — it loads, it publishes,
    and the cell is silently unreachable, which is the exact failure this whole removal closes.

    Walked over every item rather than the eight known keys: a reinstatement would arrive as a new
    item, and an eight-key check cannot see one.
    """
    raw = json.loads(_LINE_ITEMS_JSON.read_text(encoding="utf-8"))
    items = raw["items"]
    # 531 since the two direct-method TAX face parts split the template's single Income Taxes Paid(Direct) column, as the two 营业外 parts before them split Other Non-Operating Inc(Exp)
    assert len(items) == 546, f"the shipped set changed size ({len(items)}) — re-read this test"
    # 535 since the other-receivables NET split into the two ways a note prints one — the 账面价值 COLUMN of a gross/allowance/net grid, and a row whose own reported amount is the net; one part holds one `measure`, and 000709 needs both readings

    offenders = [
        (i.get("key"), i.get("implemented_by")) for i in items
        if str(i.get("implemented_by") or "") in DELETED_SERVICE_NAMES]
    assert not offenders, (
        "the shipped line-item set names a deleted derivation service as an implementer: "
        f"{offenders}. Those figures must be described in configuration instead.")

    # And the eight lines the derivations used to fill are still PRESENT — they lost their source,
    # not their existence. A line quietly dropped from the output CSV would be a different and
    # much worse change than a blank cell.
    keys = {i.get("key") for i in items}
    missing = [k for k in FORMERLY_DERIVED_KEYS if k not in keys]
    assert not missing, f"output lines disappeared rather than going blank: {missing}"

    # None of the eight may claim `derived` without a cascade to derive it from. `LineItemDef`
    # refuses that combination outright; this asserts the shipped file is on the right side of it.
    for item in items:
        if item.get("key") in FORMERLY_DERIVED_KEYS and item.get("kind") == "derived":
            assert item.get("formula") or item.get("cascade") or item.get("cascades"), (
                f"{item.get('key')} says derived with no configured cascade and no implementer")


# ── 6. THE NON-NEGOTIABLE: THE APP BOOTS AND THE SEED LOADS ─────────────────────────────────────

def test_the_shipped_line_item_seed_still_loads_through_the_boot_gate():
    """THE STATED NON-NEGOTIABLE. `app/sample/reference.py` raises `ReferenceSeedError` at startup
    if a shipped seed fails its schema, so an edit that left the seed inconsistent — a `derived`
    line with nothing behind it, a stray key the schema drops in silence — does not degrade the
    product, it stops the boot. Six lines changed kind in this removal, which is precisely the kind
    of edit that trips this gate, so it is asserted directly rather than inferred from the suite
    passing."""
    from app.sample.reference import ReferenceSeedError, _load_line_item_set

    raw = json.loads(_LINE_ITEMS_JSON.read_text(encoding="utf-8"))
    try:
        loaded = _load_line_item_set(_LINE_ITEMS_JSON, raw)
    except ReferenceSeedError as exc:            # pragma: no cover — a failing boot
        pytest.fail(f"the shipped line-item seed no longer loads, so the app cannot boot: {exc}")
    assert loaded is not None


def test_the_app_boots_with_the_four_stages_absent(client):
    """The other end of the same non-negotiable: the application actually comes up and serves.
    `client` builds the app, which imports the pipeline, the routes and the settings — the three
    places a dangling reference to a deleted derivation would surface as an ImportError or a
    missing-attribute error at startup rather than at run time."""
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text


def test_the_shipped_set_is_the_configuration_in_force():
    r"""THE SHIPPED SEED IS THE CONFIGURATION, and a fresh machine loads it.

    WHY THIS TEST EXISTS, and it is the reason several counts in this suite moved. The
    configuration used to live only in `line_item_versions` in one developer's `finex.db`: it was
    authored through the UI, it was what every extraction actually ran against, and it was in no
    repository. Nothing here could see it. Two consequences, both measured when it was finally
    exported into this file:

      * `sub__fa_cp_afs_htm_note_total` had an EMPTY `row_caption_any` at v33, v34, v39, v41 and
        v43 — a line structurally incapable of producing a figure, for its whole recorded history,
        invisible because no test reads a database.
      * `is_pl__deprec_and_impairment_cos` had lost its COS_P3 tier when
        `sub__pbt_cos_depreciation` was retired. Deliberate, but nothing recorded it.

    THE CENSUS, so the numbers pinned across this suite have one explanation:

        items 527 · parts 64 · items carrying a note_source 62
        types: extracted 484, calculated 33, derived 10

    527 and not 546 because the nine related-party FEEDERS were collapsed into the three Find items
    the spec asks for, `sub__pbt_cos_depreciation` and `sub__rp_note_entrusted_loans` were retired,
    and six revenue sub-items went in earlier sittings. Every one of the sixteen is a PART, which is
    why the part counts fall further than the item count.

    AND THE GUARANTEE ITSELF: `sample.reference.ensure_reference_data` publishes this file as a
    `LineItemVersion` against an empty database, so a machine that clones the repository and starts
    the services runs THIS configuration. That is asserted here against a real seeding run rather
    than described, because "it is in git" and "it is what loads" are different claims and only the
    second one matters.
    """
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, LineItemVersion
    from app.sample.reference import ensure_reference_data

    raw = json.loads(_LINE_ITEMS_JSON.read_text(encoding="utf-8"))
    shipped = {i["key"] for i in raw["items"]}
    # 531 since the two direct-method TAX face parts split the template's single Income Taxes Paid(Direct) column, as the two 营业外 parts before them split Other Non-Operating Inc(Exp)
    assert len(shipped) == 546, len(shipped)   # 540 since the related-party PAYABLES gained the two readings the note prints as groups — 其他应付款 and 长期应付款 — which is what lets Due to Related Parties(CP) and its LTP twin publish at all   # 538 since the loss allowance split into its two readings — the 坏账准备 COLUMN of a measure grid and an allowance printed as its own ROW — which is what lets CP_P2 subtract it   # 536 since the other-receivables line gained the interest-and-dividends-receivable component its own definition names: a filing that prints 应收利息/应收股利 as siblings of 其他应收款 has not put them inside it   # 535 since the other-receivables NET split into the two ways a note prints one — the 账面价值 COLUMN of a gross/allowance/net grid, and a row whose own reported amount is the net; one part holds one `measure`, and 000709 needs both readings
    # The line that could never produce a figure, now fixed — pinned so it cannot regress to empty.
    afs = next(i for i in raw["items"] if i["key"] == "sub__fa_cp_afs_htm_note_total")
    assert (afs.get("note_source") or {}).get("row_caption_any"), (
        "sub__fa_cp_afs_htm_note_total has no row patterns again, so it can never produce a "
        "figure — see this test's docstring")
    # The retired cost-of-sales tier stays retired, referent and rung together.
    assert "sub__pbt_cos_depreciation" not in shipped
    cos = next(i for i in raw["items"] if i["key"] == "is_pl__deprec_and_impairment_cos")
    assert [r.get("id") for r in (cos.get("cascade") or [])] == ["COS_P1", "COS_P2"]

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'fresh.db'}")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            assert session.query(LineItemVersion).count() == 0, "the fresh database was not fresh"
            ensure_reference_data(session)
            rows = session.query(LineItemVersion).all()
            assert len(rows) == 1, [r.version for r in rows]
            seeded = {i["key"] for i in (rows[0].definition or {}).get("items", [])}
            assert seeded == shipped, (
                "a fresh machine would not run the configuration this repository carries: "
                f"{len(shipped - seeded)} missing, {len(seeded - shipped)} extra")
        finally:
            # DISPOSE BEFORE THE DIRECTORY GOES. On Windows the open SQLite handle makes the
            # temporary directory undeletable, and the cleanup error would fail a test whose
            # assertions all passed.
            session.close()
            engine.dispose()
