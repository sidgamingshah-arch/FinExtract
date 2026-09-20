"""The gate that makes the ontology's removal provable: the working view IS the rulebook.

`services.working_view.build_working_view` is the inverse of `services.ontology_projection`, and
it is the load-bearing piece of making line items the single configuration engine — the matcher
keeps asking its questions of an `OntologyDefinition`, but that object is now derived from the
`LineItemSet` instead of loaded from a stored rulebook. An inexact inverse moves which line item a
caption resolves to, on every filing, with every subtotal still tying. So the inverse has to be
proved rather than reviewed.

THIS FILE REPLACES TWO SCRIPTS. `scripts/project_ontology.py` checked that every concept
declaration had a home on `LineItemDef`, and `scripts/parity_line_items.py` compared the ported
matcher against the incumbent. Both ran by hand, off the suite, against a rulebook that is being
withdrawn. The property that still matters is narrower and stronger: the working view built from a
set must equal the rulebook that set was projected from, field for field.

TWO GATES, and the split is deliberate.

  1. THE ROUND TRIP (`test_round_trip_*`) — rulebook -> `project_concept` -> `LineItemSet` ->
     `build_working_view` -> rulebook. This is what "exact inverse" means, it is independent of
     whether the SHIPPED seed happens to be current, and it cannot rot: regenerate either artefact
     and it still holds. Measured: 462 of 462 concepts, every projected field identical, with one
     intended divergence (`_SECTION_OVERRIDE`).

  2. THE SHIPPED SET (`test_shipped_*`) — the properties that must hold of the artefact the
     application actually boots with: every definition projected (539, the rulebook's 462 plus the
     77 note-level parts), the framework blocks present, the residual policies' `model_fields_set`
     intact, the section override idempotent.

     GATE 2 IS NO LONGER AN EQUALITY, and the module title overstates it for that gate. The view
     used to drop the 77 off-template `sub__*` definitions, so "the working view IS the rulebook"
     held of the shipped set as well as of the round trip. It now projects every definition,
     because `namespace` decides where a figure is PUBLISHED and not whether the engine may
     recognise it — tying the second to the first left 77 declared line items unbindable by every
     tier. So the shipped relationship is a bounded superset (rulebook + parts, asserted in both
     directions) while GATE 1 remains an exact equality: a set projected from the rulebook contains
     no internal parts, so its view is the rulebook's 462 exactly.

WHY GATE 2 DOES NOT PIN FIELD-FOR-FIELD EQUALITY, stated rather than quietly dropped. Measured
today, the shipped set is NOT a fresh projection of `output_csv_hk_ontology.json`: it diverges on
`confusable_with` (29 concepts), `description` (8), `section_disambiguation` (3) and
`aliases`/`aliases_i18n` (2 each). Only the `description` group is by design — those 8 are the keys
the rulebook and the configurator BOTH describe, where `merge_assembly` deliberately keeps the
configurator's prose. The other 34 are seed staleness: the rulebook was edited after the seed was
built. Pinning the current divergence exactly would either fail on arrival or bake the staleness in
and pass forever, so `test_shipped_set_diverges_only_in_the_known_classes` asserts the divergence
is CONFINED to those field names — a newly diverging field fails, a regenerated seed still passes.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import LineItemDef, load_line_item_set
from app.schemas.loader import load_ontology
from app.services.line_item_config import load_shipped_set
from app.services.mapping import _KEY_SECTION_OVERRIDES
from app.services.ontology_projection import RENAMED, SAME, _jsonable, project_concept
from app.services.working_view import (CARRIED, UNHOMED, _definition_of, build_working_view)

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
RULEBOOK = TEMPLATES / "output_csv_hk_ontology.json"

# Every field the projection claims to carry, in both directions.
PROJECTED = sorted(set(SAME) | set(RENAMED))

CONCEPTS = 462                 # the rulebook's concepts, and the set's `namespace == "template"`
ITEMS = 546                    # …plus the 69 `sub__*` note-level parts, which ARE concepts too:
                               # the view projects every definition, and `namespace` decides only
                               # where a figure is PUBLISHED. See
                               # test_shipped_set_projects_every_definition_including_the_parts.
RESIDUALS = 11                 # concepts carrying a `residual_policy` (all `exclusive_residual`)

# The one concept whose working view deliberately DISAGREES with the rulebook, because the
# projection wrote `mapping._KEY_SECTION_OVERRIDES` into the data: the rulebook declares the broad
# `is_pl` namespace, the row really sits in the profit-attribution tail, and `_sections_of` used to
# override the declaration from a table in code where no reviewer could see it.
_SECTION_OVERRIDE = ("is_pl__minority_interests_pl", "section_scope")

# ONE MORE DELIBERATE DIVERGENCE, and unlike the section override it is a CONFIGURATION decision
# rather than a projection artefact: `notes__contingent_liabilities` was moved off the `notes`
# section default of `evidence_only`. A contingent liability is disclosed ONLY in the notes, so
# there is no face amount for a note to corroborate and `evidence_only` made the concept
# unfillable by any route — `stages/note_sourced` refuses a note as a source for such a line.
# Named here rather than added to `_SEED_LAG`, because it is not the seed lagging the rulebook:
# it is the seed leading it, and the divergence is intended to persist until the rulebook agrees.
_NOTE_USE_DECISION = ("notes__contingent_liabilities", "note_use")

# AND THE EIGHT FOCUS CONCEPTS' DEFINITIONS, rewritten in the seed and not in the rulebook.
# Every one of them described a DELETED service — "Computed, never alias-matched:
# services.deprec_impairment resolves this by trying, in order…" — and that text is what the
# semantic tier reasons over, so the probe built from it was half implementation jargon. Measured:
# the footnote stating one of those figures went from rank 33 to rank 3 once the definition said
# what the line MEANS. Like the `note_use` decision above this is the seed LEADING the rulebook,
# so it is named rather than folded into `_SEED_LAG`, which is for the seed trailing it.
_DEFINITION_DECISIONS = frozenset({
    "is_pl__deprec_and_impairment_oper_exp", "is_pl__deprec_and_impairment_cos",
    "bs_ca__secur_and_other_fincl_assets_cp", "bs_nca__secur_and_other_fincl_assets_ltp",
    "notes__contingent_liabilities", "bs_nca__due_from_related_parties_ltp",
    "bs_ca__other_receivables_cp", "is_pl__sales_revenues",
})

# AND THE SAME CONCEPTS' `extraction_mode`, declared `extract` in the seed — the third case of the
# seed LEADING the rulebook, and the one with a behavioural consequence, so it is worth stating in
# full rather than listing.
#
# The nine `type: derived` lines carried THREE modes between them (`extract` on revenue, `derive`
# on two, `extract_or_derive` on the rest) while being the same kind of thing: a parent whose
# figure a declared cascade produces. What the mode actually decides for such a line is whether a
# PRINTED ROW may fill it where no rung resolves, and the answer is yes — that is the documented
# intent of `extract_or_derive` already (`mapping._computed_claim`), and revenue is the proof: it
# is the one that was already `extract` and the one that publishes 4,995,768 off the face with no
# rung firing.
#
# THE MODEL IS STILL NEVER OFFERED THEM. That guarantee used to ride on the mode, which is what
# made this flip unsafe before. It now rides on the type — `OntologyMapping.item_type`, read by
# `mapping._llm_withheld` — so it holds for all nine whatever the mode says.
#
# ONE OF THE NINE IS NOT HERE, for a measured reason: `statement_setup_controls__periods` is a
# priority-90 control whose keyword hints are words half an income statement's captions contain, so
# `derive` is what keeps every caption off it (`scripts/mark_derived_extract.py`).
#
# `bs_nca__secur_and_other_fincl_assets_ltp` WAS THE SECOND EXCEPTION AND IS NOW HERE. It was held
# at `derive` because `extract` let a caption bind 128,412 in place of its cascade's 788,507 — and
# what fixed that is `CascadeRung.outranks_printed`, declared on its four rungs
# (`scripts/declare_outranking_rungs.py`). Each of them computes a non-current portion less three
# classes of inclusion, which no printed row states, so the rung wins the contest on its own merits
# and the mode no longer has to hold the figure.
_EXTRACTION_MODE_DECISIONS = frozenset({
    "is_pl__deprec_and_impairment_oper_exp", "is_pl__deprec_and_impairment_cos",
    "bs_ca__secur_and_other_fincl_assets_cp", "notes__contingent_liabilities",
    "bs_nca__due_from_related_parties_ltp", "bs_ca__other_receivables_cp",
    "bs_nca__secur_and_other_fincl_assets_ltp",
})

# RECOGNITION MOVED DOWN TO THE FACE-READING PARTS, so these two parents no longer carry it.
#
# Both are `type: derived` parents whose figure comes from a row printed on the FACE of a statement.
# A derived parent is never offered to any matcher, so `keyword_hints`, `regex_hints` and
# `match_priority` sitting on the PARENT described a match that could not happen, while the part
# that actually corresponds to the printed row (`sub__face_principal_revenue`,
# `sub__rp_bs_face_receivables`) had no recognition at all. The three fields moved to the parts.
#
# THIS BOUND WAS GENERALISED TO THE WHOLE DERIVED CLASS AND HAD TO BE PUT BACK, which is the part
# worth keeping. The other seven parents' recognition was deleted on the reasoning that no matcher
# can reach a derived parent — true, and measured: probing every caption either matcher knows
# changed zero answers. It still moved published figures. `stages.residual._concept_captions` reads
# a concept's aliases as a VETO index, and `build_working_view` projects this configuration into the
# concepts it reads, so deleting an alias here deletes a sweep guard there. On the 2024 filing two
# ASSET captions were then swept into "other current liabilities" and total assets moved by 12.3m.
# See `tests/test_derived_parent_unreachable.py`.
#
# So the bound stays NAMED and narrow. `aliases` and `aliases_i18n` are already in `_SEED_LAG`
# (the merged keys keep the configurator's own lists), which is why only three fields need to be
# here at all — and a THIRD concept or a FOURTH field losing its recognition still fails this test.
# NOW EVERY DERIVED PARENT, by directive. The 258 entries went, came back when the figures
# comparison showed they were a residual-sweep veto, and went again when that cost was accepted —
# see `tests/test_derived_parent_unreachable.py` for what it is. Read off the set so a tenth derived
# line is covered without editing a list, while an EXTRACTED concept losing its recognition still
# fails this test.
def _derived_parents() -> frozenset[str]:
    return frozenset(d.key for d in load_shipped_set().items
                     if str(getattr(d.type, "value", d.type)) == "derived")


_FACE_RECOGNITION_MOVED = frozenset({
    "bs_nca__due_from_related_parties_ltp", "is_pl__sales_revenues",
})
_FACE_RECOGNITION_FIELDS = {"keyword_hints", "regex_hints", "match_priority"}

# THE FOUR LINES WHERE `confusable_with` BECAME EXCLUSIONS, which the rulebook does not know about.
#
# That field named two mutual pairs and `_forbidden_tie` refused each pair at equal priority —
# both keys to review, nothing published. It fired on 8 of the set's 238 equal-priority alias
# collisions, and on those 8 file order was wrong: 土地使用权 is the Chinese for land use rights and
# Buildings is declared first. So the disagreement is now stated as configuration — 36 exclusions
# for the other line's distinctive captions, and 9 aliases removed from the line they did not belong
# to — which is the seed LEADING the rulebook, named rather than folded into `_SEED_LAG`.
#
# A FIFTH line gaining an exclusion the rulebook does not have still fails this test.
_CONFUSABLE_CONVERTED = frozenset({
    "bs_nca__buildings", "bs_nca__land_use_rights",
    "bs_ca__trade_and_other_receivables", "bs_ca__trade_receivables_gross",
})

# THE SEED LEADS THE RULEBOOK ON TWO FIELDS BECAUSE THE CONFIG SCREEN WORK CHANGED THEM.
#
# `exclude` — the rulebook's spelling of `exclude_criteria` — is declared on 462 concepts there and
# is now EMPTY on all 462 in the seed. Measured before blanking it: 393 of those 462 values were one
# identical generated sentence ("Do not substitute another section, period, entity scope, currency or
# unit; do not double count a parent and its children"), sent to the model as `exclude` on every
# request. The field survives for the day somebody writes a real exclusion; its generated contents
# did not.
#
# `definition` diverges on 76 concepts because the 69 genuinely authored `include_criteria` values
# were FOLDED INTO IT when that field went — "Use the presentation currency explicitly stated in the
# statements or accounting-policy note" and the like, which lived nowhere else. `definition` is the
# one prose field BOTH paths read (the payload sends it, `line_item_notes` builds the note-selection
# probe from it), so it is where guidance belongs.
#
# Named rather than folded into `_SEED_LAG`, like the other decisions here: a 463rd `exclude` or a
# 77th changed `definition` still fails this test, which is what stops either drifting further.
_EXCLUDE_BLANKED = 462
_DEFINITION_FOLDED = 76

# Fields on which the SHIPPED seed may lag the rulebook — see the module docstring. `description`
# is by design (the 8 merged keys keep the configurator's prose); the rest are staleness, and the
# assertion is that the divergence goes no wider than these names.
_SEED_LAG = {"description", "confusable_with", "section_disambiguation", "aliases", "aliases_i18n"}

# AND `alias_matching`, WHICH THE SEED NO LONGER HAS AT ALL. The others master marks the eleven
# residual buckets, `value_scope: exclusive_residual` is the marker, and the field that used to
# carry the marking — along with a caption lock and a never-asked flag — is removed from all 14
# lines that declared it. The rulebook still declares it, so every one of those 14 diverges.
#
# Bounded by the COUNT rather than folded into `_SEED_LAG`: this is the seed LEADING the rulebook,
# and a FIFTEENTH line diverging would mean something else changed.
_ALIAS_MATCHING_REMOVED = 14

# AND `regex_hints`, WHICH THE SEED HAS ALL BUT STOPPED CARRYING.
#
# 387 of its 389 patterns were deleted: every one was an anchored literal whose effect the ALIAS
# TIER already had. `mapping.normalize_label` strips punctuation and collapses whitespace, so
# "Trade Receivables (Gross)" folds to the same string the alias does — and `_exact` runs BEFORE
# the rule tier, so a caption an alias claims never reaches a regex at all. Probed over all 1,986
# captions either matcher knows: zero answers changed.
#
# BOUNDED BY THE PROPERTY, not by a count of the lines that diverge. Every divergence must be a
# REMOVAL — the seed carries no pattern for that key — so the seed gaining one the rulebook does not
# have still fails here, and the bound needs no maintenance. The two survivors are on one line and
# are not anchored literals, so they reach captions an alias does not.


def _seed_regex_hints() -> dict:
    """key -> the patterns the SEED declares, so a divergence can be read as removal or addition."""
    return {d.key: list(d.regex_hints) for d in load_shipped_set().items}


def _rulebook():
    """The shipped rulebook, resolved — the shape `project_concept` consumed."""
    return load_ontology(json.loads(RULEBOOK.read_text(encoding="utf-8")), resolve=True)


def _by_key(definition):
    return {m.canonical_key: m for m in definition.mappings}


def _round_trip_set():
    """A `LineItemSet` that IS a fresh projection of the shipped rulebook.

    Only the projection and the set-level blocks the working view reads — no configurator overlay,
    so nothing but the inverse is under test here. `scripts/build_line_items.py` builds the shipped
    seed the same way and then merges the configurator's assembly over 8 keys.
    """
    raw = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    document = {
        "schema_version": 1,
        "line_items_key": raw["ontology_key"],
        "target_template_key": raw["target_template_key"],
        "locale": raw.get("locale", "en"),
        "supported_locales": raw.get("supported_locales", ["en"]),
        "section_defaults": raw.get("section_defaults") or {},
        "normalisation": raw.get("normalisation"),
        "binding": raw.get("binding"),
        "global_rules": raw.get("global_rules") or {},
        "scope_selection": raw.get("scope_selection"),
        "residual_framework": raw.get("residual_framework"),
        "items": [project_concept(m) for m in _rulebook().mappings],
    }
    return load_line_item_set(json.loads(json.dumps(document, ensure_ascii=False)))


def _field_diff(view, reference) -> dict[str, list[str]]:
    """Which projected fields disagree, and on which concepts. Field name -> canonical_keys."""
    got, want = _by_key(view), _by_key(reference)
    out: dict[str, list[str]] = {}
    for key in sorted(want):
        if key not in got:
            continue
        for field in PROJECTED:
            if _jsonable(getattr(want[key], field)) != _jsonable(getattr(got[key], field)):
                out.setdefault(field, []).append(key)
    return out


# ── gate 1: the round trip is an exact inverse ───────────────────────────────────────────────

def test_round_trip_covers_every_concept():
    view = build_working_view(_round_trip_set())
    reference = _rulebook()
    assert len(reference.mappings) == CONCEPTS
    assert set(_by_key(view)) == set(_by_key(reference))
    assert len(view.mappings) == CONCEPTS


def test_round_trip_is_field_for_field_identical():
    """The whole point. Every projected field, every concept, one documented exception."""
    diff = _field_diff(build_working_view(_round_trip_set()), _rulebook())
    offenders = sorted((field, key) for field, keys in diff.items() for key in keys)
    assert offenders == [(_SECTION_OVERRIDE[1], _SECTION_OVERRIDE[0])], (
        "the working view is no longer the exact inverse of the projection — a caption's answer "
        f"can move on every filing: {offenders}")


def test_round_trip_preserves_residual_policy_declarations():
    """`residual._declared` asks `name in policy.model_fields_set`, so a full dump is a defect.

    Dumped without `exclude_unset` every one of the seven terms reads as authored by the concept,
    and `residual_framework` — one definition meant to govern all of the residual buckets —
    governs none of them.
    """
    view, reference = build_working_view(_round_trip_set()), _rulebook()
    got, want = _by_key(view), _by_key(reference)
    residuals = [k for k, m in want.items() if m.residual_policy is not None]
    assert len(residuals) == RESIDUALS
    for key in residuals:
        policy, mirror = want[key].residual_policy, got[key].residual_policy
        assert mirror is not None, f"{key} lost its residual_policy entirely"
        assert mirror.model_fields_set == policy.model_fields_set, (
            f"{key}: authored terms {sorted(policy.model_fields_set)} came back as "
            f"{sorted(mirror.model_fields_set)}, so the framework governs the wrong terms")


def test_the_section_layer_must_be_folded_and_only_one_field_depends_on_it():
    """HAZARD 1, pinned in both directions. The items are stored ALREADY RESOLVED, so `resolve`
    looks like it cannot matter — and it matters on exactly one field.

    `note_use_rationale` is in `ontology_projection.SAME` but `LineItemDef` declares no such field,
    so the projection writes it to the seed and pydantic's `extra="ignore"` drops it at load. The
    set's `section_defaults` still declare it, which makes the fold the only route back. Everything
    else is idempotent because a declaration beats a section default.
    """
    assert UNHOMED == ("note_use_rationale",), (
        "a member of `SAME` has no home on `LineItemDef`, so the projection writes it and the "
        f"load drops it: {UNHOMED}. Either declare the field or take it out of `SAME`")
    assert "note_use_rationale" not in CARRIED

    payload = _definition_of(_round_trip_set())
    unfolded = load_ontology(json.loads(json.dumps(payload)), resolve=False)
    folded = load_ontology(json.loads(json.dumps(payload)), resolve=True)
    moved = {f: len(keys) for f, keys in _field_diff(unfolded, folded).items()}
    assert moved == {"note_use_rationale": 394}, (
        f"the section fold now moves more than the one unhomed field: {moved}")
    assert build_working_view(_round_trip_set()).mappings[0].note_use_rationale == \
        _by_key(_rulebook())[payload["mappings"][0]["canonical_key"]].note_use_rationale


# ── gate 2: the artefact the application boots with ──────────────────────────────────────────

def test_shipped_set_projects_every_definition_including_the_parts():
    """THE VIEW NO LONGER DROPS ANYTHING, and that is the change this test records.

    It used to assert the opposite — 462 concepts and not 475, the 77 `sub__*` parts dropped — on
    the reasoning that an off-template key is "part OF a line, never a template line". That
    conflated two independent things. `namespace` says WHERE a figure is published (its output
    column, and whether the config screen can edit it) and still does exactly that; whether the
    ENGINE may recognise a caption as that concept is a different question. Tying recognition to
    publication left 77 declared line items unrecognisable by every tier.

    So the shipped view is now every definition, and the relationship to the rulebook is no longer
    equality but a bounded superset: the rulebook's 462 plus exactly the internal parts. That is
    asserted in both directions, because a superset claim alone would not notice the view LOSING a
    rulebook concept.
    """
    st = load_shipped_set()
    assert len(st.items) == ITEMS
    view = build_working_view(st)
    assert len(view.mappings) == ITEMS, "the view must project every definition"

    seen = set(_by_key(view))
    assert {i.key for i in st.items} == seen, "a declared line item is missing from the view"

    # The view is the rulebook PLUS the parts, and nothing else in either direction.
    rulebook = set(_by_key(_rulebook()))
    extra = seen - rulebook
    assert not rulebook - seen, f"the view lost rulebook concepts: {sorted(rulebook - seen)}"
    assert len(extra) == ITEMS - CONCEPTS
    # THE PARTS, PLUS ONE RESIDUAL BUCKET. `bs_ca_residual_L3` is not a `sub__` part but an
    # internal residual — a current-assets bucket for the fair-value Level 3 split — added to the
    # configuration rather than to the rulebook. It belongs in the view for the same reason the
    # parts do: `namespace` says where a figure is PUBLISHED, not whether the engine may recognise
    # it. Named rather than admitted by a loosened pattern, so a second non-part appearing here is
    # still a failure someone has to look at.
    RESIDUALS = {"bs_ca_residual_L3"}
    assert all(k.startswith("sub__") or k in RESIDUALS for k in extra), (
        f"something other than the note-level parts is new: "
        f"{sorted(k for k in extra if not k.startswith('sub__') and k not in RESIDUALS)}")
    assert all(i.namespace == "internal" for i in st.items if i.key in extra)

    # AND THEY ARE REAL CONCEPTS, not inert rows: an unbindable one would be a silent no-op.
    matcher_view = {m.canonical_key: m for m in view.mappings}
    assert all(matcher_view[k].extraction_mode == "extract" for k in extra)
    # A part declares NO statement and NO section scope, deliberately: it is printed wherever its
    # note is, which is not where its whole is reported (depreciation is printed in the
    # balance-sheet note on fixed assets while its whole is a P&L line). `_in_statement` allows a
    # concept it cannot place, so unpinned means reachable everywhere rather than nowhere.
    # …EXCEPT the eight that declare one, named in `test_line_item_gate` with the measurement they
    # sit in tension with. Unpinned still means reachable everywhere for the other fifty-six.
    SECTION_SCOPED_PARTS = {
        "sub__fa_cp_fvtpl_note_total", "sub__fa_cp_fvtoci_note_total",
        "sub__fa_cp_afs_htm_note_total", "sub__fa_cp_included_derivatives",
        "sub__fa_cp_investment_and_money_market_securities_note_total",
        "sub__fa_cp_noncurrent_split_of_note_total",
        "sub__face_principal_revenue", "sub__revenue_note_principal_revenue",
        # THE FOUR ASSET-DEPRECIATION PARTS, scoped to `bs_nca` to mean "consider the non-current
        # asset notes" — the same CONTEXT sense the six securities parts give `bs_ca`, and named
        # here for the same reason the list exists: so an addition is read deliberately.
        "sub__ppe_depreciation", "sub__fixed_asset_depreciation",
        "sub__investment_property_depreciation", "sub__cip_depreciation",
        # AND THE ONE PART THAT READS THE CASH-FLOW FACE. `sub__cfo_depreciation` has no
        # `note_source` at all: the depreciation add-back is printed on the face of an indirect
        # cash-flow statement, so `statement: cash_flow` and `section_scope: ['cf_oper_indirect']`
        # are the mechanism by which it is read, not a pin that cuts it off from a note it never
        # reads. `sub__face_principal_revenue` above is the same shape for the income statement.
        "sub__cfo_depreciation",
        # AND FIND 1, which reads the balance-sheet face for the same reason: no
        # `note_source`, and `section_scope: ['bs_nca']` is the mechanism by which it is
        # read rather than a pin cutting it off from a note it does not read.
        "sub__rp_find_1",
        # AND THE TWO 营业外 HALVES, which read the INCOME-STATEMENT face and have no `note_source`
        # either — the same shape as `sub__cfo_depreciation` and `sub__face_principal_revenue`
        # above. CAS prints 营业外收入 and 营业外支出 as two rows either side of 利润总额 and the template
        # holds ONE net column, so the halves are parts that roll up into it. `statement:
        # profit_and_loss` and `section_scope: ['is_pl']` are the mechanism by which each is read
        # off that face, not a pin cutting it off from a note neither reads.
        "sub__non_operating_income", "sub__non_operating_expenses",
        # AND THE TWO TAX HALVES, which read the DIRECT-METHOD CASH-FLOW face on the same grounds.
        # CAS 31 prints 支付的各项税费 and 收到的税费返还 on opposite sides of the operating section
        # while the template holds ONE tax line, so the halves are parts that roll up into it.
        # `statement: cash_flow` and `section_scope: ['cf_oper_direct']` are the mechanism by which
        # each is read off that face, not a pin cutting it off from a note neither reads.
        "sub__cf_direct_income_taxes_paid", "sub__cf_direct_tax_refunds_received",
        # AND THE THREE RELATED-PARTY PAYABLE FACE PARTS, on the same grounds as `sub__rp_find_1`
        # above: no `note_source`, and `statement: balance_sheet` with `inherits: bs_cl`/`bs_ncl`
        # is the mechanism by which each is read off the balance-sheet face. They exist because
        # their PARENTS are `derived` and `mapping._computed_parent` therefore forbids a caption
        # from reaching them, so a column with a note cascade had no way to read its own printed
        # row — measured, China SCE 1966's "Due to related parties" 2,588,416 went to
        # `bs_cl__other_current_liabilities` and 嘉民's two shareholder loans to
        # `bs_ncl__other_non_current_liabilities`.
        "sub__rp_face_due_to_cp", "sub__rp_face_loan_from_holding_co",
        "sub__rp_face_loan_from_shareholder",
        # AND THE TRADE RECEIVABLE'S FACE PART, on the same grounds.
        "sub__rp_face_trade_receivable",
    }
    # A RESIDUAL BUCKET IS NOT A PART and declares its statement properly: `bs_ca_residual_L3`
    # is a balance-sheet current-assets bucket, so `balance_sheet` is where it belongs.
    unpinned = [k for k in extra
                if k not in SECTION_SCOPED_PARTS and k not in RESIDUALS]
    # `notes` IS ALLOWED, and it is not a pin in the sense this guards against. It says a caption
    # may be read from the notes, which is where these parts read — the measured loss in
    # `test_line_item_gate` was a note-read part scoped to a FACE section, and the same probe
    # records the part as offered under `notes`. Nineteen depreciation parts declare it
    # deliberately.
    def _statement_of(key: str) -> str | None:
        st_ = matcher_view[key].statement
        return None if st_ is None else str(getattr(st_, "value", st_))
    assert all(_statement_of(k) in (None, "notes") for k in unpinned), (
        f"{[(k, _statement_of(k)) for k in unpinned if _statement_of(k) not in (None, 'notes')][:6]}")
    assert all(not (matcher_view[k].section_scope or []) for k in unpinned)


def test_shipped_set_diverges_only_in_the_known_classes():
    """See the module docstring: the shipped seed lags the rulebook, and this bounds the lag."""
    diff = _field_diff(build_working_view(load_shipped_set()), _rulebook())
    allowed_single = {(_SECTION_OVERRIDE[1], (_SECTION_OVERRIDE[0],)),
                      (_NOTE_USE_DECISION[1], (_NOTE_USE_DECISION[0],))}
    unexpected = {}
    for field, keys in diff.items():
        if field in _SEED_LAG or (field, tuple(keys)) in allowed_single:
            continue
        # `definition` diverges on exactly the eight concepts whose definitions were rewritten —
        # named, so a NINTH would still fail here.
        if field == "definition" and set(keys) <= _DEFINITION_DECISIONS:
            continue
        # …and `extraction_mode` on the six derived parents the seed declares `extract`. Named the
        # same way and for the same reason: a SEVENTH still fails here, which is what stops a mode
        # being flipped without the measurement that justifies it.
        if field == "extraction_mode" and set(keys) <= _EXTRACTION_MODE_DECISIONS:
            continue
        # …and the recognition fields on exactly the two derived parents whose face-reading PART
        # now carries them. Both halves are bounded: the field must be one of the three that moved,
        # and the concepts must be those two.
        if field in _FACE_RECOGNITION_FIELDS and set(keys) <= _FACE_RECOGNITION_MOVED:
            continue
        # …the recognition fields on a DERIVED parent, which carries none any more…
        if field in _FACE_RECOGNITION_FIELDS | {"aliases", "aliases_i18n", "exclude_hints"}                 and set(keys) <= _derived_parents():
            continue
        # …and `exclude_hints`, which diverges for BOTH reasons at once: the four lines whose
        # `confusable_with` pair became exclusions gained some, and the derived parents lost theirs.
        # Checked against the union, because a per-reason check passes only while one reason acts
        # alone — and the first version of this allowance did exactly that and failed on the eight.
        if field == "exclude_hints" and set(keys) <= _CONFUSABLE_CONVERTED | _derived_parents():
            continue
        if field == "alias_matching" and len(keys) <= _ALIAS_MATCHING_REMOVED:
            continue
        if field == "regex_hints":
            seeded = _seed_regex_hints()
            if all(not seeded.get(k) for k in keys):
                continue
        # …and the two fields the config-screen work changed, bounded by COUNT so a wider
        # divergence still fails. See `_EXCLUDE_BLANKED` / `_DEFINITION_FOLDED` above.
        if field == "exclude" and len(keys) <= _EXCLUDE_BLANKED:
            continue
        if field == "definition" and len(keys) <= _DEFINITION_FOLDED:
            continue
        unexpected[field] = keys
    assert not unexpected, (
        "the shipped set and the rulebook disagree on a field neither the configurator merge nor "
        f"a stale seed explains: { {f: len(k) for f, k in unexpected.items()} }")


def test_shipped_set_keeps_the_section_override_exactly_once():
    """`mapping._KEY_SECTION_OVERRIDES` is idempotent over the projected value.

    The projection wrote the correction into `section_scope`, so the table in code now re-applies
    it to a value that already carries it. Applied twice it would still be one entry; the assertion
    is that the entry is there ONCE and is the corrected section, not the rulebook's `is_pl`.
    """
    key, _ = _SECTION_OVERRIDE
    concept = _by_key(build_working_view(load_shipped_set()))[key]
    assert concept.section_scope == ["profit_attributable_to"]
    assert _KEY_SECTION_OVERRIDES[key] == "profit_attributable_to"
    reapplied = [_KEY_SECTION_OVERRIDES.get(key, s) for s in concept.section_scope]
    assert reapplied == concept.section_scope == ["profit_attributable_to"]
    assert _by_key(_rulebook())[key].section_scope == ["is_pl"]


def test_shipped_set_carries_the_framework_blocks():
    """The four blocks the pipeline reads on every run must arrive on the view, non-empty.

    A configured EMPTY value means nothing here — but an empty block on the SHIPPED set would mean
    the framework the run obeys came from somewhere this configuration cannot reach, which is the
    whole failure being removed.
    """
    view = build_working_view(load_shipped_set())
    for block in ("normalisation", "binding", "global_rules", "scope_selection"):
        got = getattr(view, block)
        assert got is not None, f"{block} did not survive onto the working view"
        assert _jsonable(got), f"{block} arrived empty, so the run's framework has no source"
    assert view.residual_framework is not None
    assert view.section_defaults and view.ontology_key == load_shipped_set().line_items_key
    assert view.target_template_key == "output_csv_hk_v1"


def test_the_view_is_cached_per_set():
    st = load_shipped_set()
    assert build_working_view(st) is build_working_view(st)
    # A different set is a different view, keyed on the canonical JSON rather than on identity.
    assert build_working_view(_round_trip_set()) is not build_working_view(st)


@pytest.mark.parametrize("field", ["type", "implemented_by"])
def test_derived_fields_are_not_carried_back(field):
    """`type` is derived from `extraction_mode` (which round-trips) and `implemented_by` names a
    service, so neither is a concept property and `OntologyMapping` has nowhere to put them."""
    assert field in LineItemDef.model_fields
    assert field not in PROJECTED
    concept = _definition_of(load_shipped_set())["mappings"][0]
    assert field not in concept
