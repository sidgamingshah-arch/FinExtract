"""THE WORKING VIEW: ``OntologyDefinition`` must load with NOTHING dropped, and its section layer
must resolve.

WHAT THIS FILE IS NOW, because its name changed with the merge (it was ``test_ontology_v2.py``).
``OntologyDefinition`` is no longer a stored, selectable or user-visible thing — there is no
ontology store, no ontology route and no ontology picker. It survived the merge as the MATCHER'S
WORKING VIEW: the object ``services.working_view.build_working_view`` derives from the line-item set
a run pins, so the proven matcher can be fed the single configuration engine's data without
rewriting 3,000 lines. Its schema and its section layer are therefore still live, and this file is
where they are pinned; every test that POSTED an ontology, listed ontologies or asked
``ontology_select`` which one was in force is repointed onto ``/line-items`` and
``services.config_select``, or retired where the behaviour itself is gone (see the retirement notes
at the foot of this module).

Two failure modes are covered here. The first is the one ``unknown_keys`` exists for: a block the
schema does not declare is ignored by pydantic, so the definition loads and simply is not the
definition that was authored. The second is subtler and cannot be seen from the file at all —
``section_scope``, ``statement``, ``temporality`` and ``face_only`` are authored on ZERO concepts,
only in ``section_defaults``, so without the ``inherits`` fold every concept loads with no section
at all and the section-first binding order has nothing to bind against.

THE FIXTURE FILE. The schema and section-layer tests below read
``app/sample/templates/hkfrs_hk_china_ontology.json``. It is a FIXTURE, not a shipped
configuration: nothing seeds it, no run reads it, and the one shipped configuration is
``output_csv_hk_line_items.json``. It is kept as the widest realistic input for the working view's
schema — 183 concepts, 19 sections and every optional block populated.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.schemas.loader import (
    UnknownInheritsError,
    load_ontology,
    resolve_inherits,
    unknown_keys,
)
from app.schemas.ontology import Equivalence, OntologyMapping, ResidualPolicy

SAMPLES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
V2 = SAMPLES / "hkfrs_hk_china_ontology.json"
V1 = SAMPLES / "hkfrs_hk_china_ontology.json"


def _v2() -> dict:
    return json.loads(V2.read_text(encoding="utf-8"))


def _by_key(ont) -> dict:
    return {m.canonical_key: m for m in ont.mappings}


# --- the whole file survives the door ---------------------------------------------------------

def test_v2_rulebook_loads_with_nothing_dropped():
    raw = _v2()
    ont = load_ontology(raw)
    assert unknown_keys(raw, ont, limit=500) == []
    assert len(ont.mappings) == 183
    assert len(ont.section_defaults) == 19
    # The six new top-level blocks are objects, not swallowed keys.
    assert ont.normalisation and ont.normalisation.pipeline
    assert ont.binding and ont.binding.order
    assert ont.scope_selection and ont.scope_selection.entity_scope.default == "consolidated"
    assert ont.residual_framework and ont.residual_framework.population == "sweep_only"
    assert ont.validation and len(ont.validation.identities) == 19


def test_v2_loads_resolved_with_nothing_dropped():
    """Resolution must stay inside the declared schema: a folded key the concept model does not
    declare would be dropped exactly as an undeclared authored one is."""
    raw = _v2()
    resolved = resolve_inherits(raw)
    assert unknown_keys(resolved, load_ontology(raw, resolve=True), limit=500) == []


def test_a_rulebook_with_no_section_layer_is_untouched_by_resolution():
    """A rulebook with no section layer at all: asking for resolution must be a no-op, not a
    different definition — the same two loaders read every rulebook, uploaded ones included.

    Written from a literal rather than from a shipped file. It used to read the thin generation,
    which had no section layer; the one rulebook that ships now has one, and an uploaded schema-1
    rulebook is what this protects.
    """
    raw = {"schema_version": 1, "ontology_key": "k", "target_template_key": "t",
           "mappings": [{"canonical_key": "bs_current_assets__inventories", "label": "Inventories",
                         "aliases": ["Inventories"]}]}
    assert unknown_keys(raw, load_ontology(raw)) == []
    assert resolve_inherits(raw) is raw
    assert load_ontology(raw, resolve=True).model_dump() == load_ontology(raw).model_dump()


# --- the field shapes, not just their presence -------------------------------------------------

def test_residual_concepts_carry_typed_sweep_terms():
    ont = load_ontology(_v2())
    m = _by_key(ont)["bs_non_current_assets__others"]
    # A string, not a bool and not a dict: the residuals are populated by the sweep only, and
    # reading "disabled" as truthy would leave alias matching on for all 13 of them.
    assert m.alias_matching == "disabled"
    assert m.match_priority == 0                       # an int, so it can be ordered against 78
    assert isinstance(m.residual_policy, ResidualPolicy)
    assert m.residual_policy.plug is False and m.residual_policy.cross_section is False
    assert m.residual_policy.section_scope == "bs_s1_non_current_assets"
    assert m.never_sweep and all(isinstance(k, str) for k in m.never_sweep)
    assert len(m.expected_components) == 5
    residuals = [x for x in ont.mappings if x.value_scope == "exclusive_residual"]
    # 13: every section owns a sweep bucket EXCEPT the tax charge, which deliberately has none. A
    # tax figure is small enough that no rollup notices what lands beside it, so a row nothing
    # claimed there reaches review instead — measured on a real filing, loss per share was being
    # swept into Total tax expense. Other comprehensive income gained one for the opposite reason:
    # it was the section without a bucket, and the sweep used to walk an unrecognised OCI line
    # backwards into the nearest section that had one.
    assert len(residuals) == 13
    assert all(x.alias_matching == "disabled" and x.residual_policy for x in residuals)


def test_equivalence_keeps_its_authored_with_spelling():
    """``with`` is a Python keyword, so the field is aliased — and the dump has to speak the
    authored spelling, because that dump is what the upload gate diffs the submitted JSON
    against. An un-aliased dump reports `equivalence.with` as an undeclared key."""
    ont = load_ontology(_v2())
    eq = _by_key(ont)["bs_equity__total_equity"].equivalence
    assert isinstance(eq, Equivalence)
    assert eq.with_ == "bs_net_assets"
    assert eq.relation == "identical_reported_amount"
    assert Equivalence(with_="x").model_dump() == {"with": "x", "relation": "", "rule": ""}


def test_concept_level_prose_and_containment_fields_survive():
    ont = load_ontology(_v2())
    by_key = _by_key(ont)
    reserves = by_key["bs_equity__reserves"]
    assert reserves.is_gross_parent is True
    assert len(reserves.children_if_decomposed) == 4
    assert by_key["pl_income__total_income"].derivation.startswith("sum of")
    assert by_key["bs_non_current_assets__land_of_use_rights"].section_disambiguation
    # The one template_note the file carried was on cf_s4_effect_of_foreign_exchange_rate_changes:
    # "the template declares this node with role: header … the role should be corrected to 'line'".
    # The cash-flow revision corrected it and retired that key, so the shipped file needs none — the
    # field still has to survive a load, which is what the model assertion below holds.
    # ``notes_as_source_rationale`` was on the tax residual, which the tax-bucket removal retired
    # with it: no shipped concept sources from a note now, so none carries the rationale for doing
    # so. Held the same way as ``template_note`` above — the field survives a load, and a rulebook
    # that turns note sourcing back on has somewhere to say why.
    assert [m for m in ont.mappings if m.notes_as_source_rationale] == []
    assert OntologyMapping(canonical_key="x", notes_as_source_rationale="r"
                           ).notes_as_source_rationale == "r"
    assert by_key["pl_expenses__employee_benefits_expense"].note_use == "evidence_only"
    assert by_key["bs_equity__total_equity"].unit_of_account == "subtotal"
    d_and_a = by_key["cf_cash_flow_from_operating_activities__depreciation_and_amortisation"]


def test_nested_blocks_are_modelled_not_free_dicts():
    ont = load_ontology(_v2())
    g = ont.global_rules
    assert g.face_only_default and "face_only" in g.face_only_default
    assert g.sign_convention["expenses_and_outflows"].startswith("Stored NEGATIVE")
    groups = {grp.id: grp for grp in g.mutually_exclusive_groups}
    # The income-statement and cash-flow revision adds two: `oci_composition` (the printed other
    # comprehensive income subtotal versus its two IAS 1 categories) and `cf_starting_point` (a cash
    # flow starts from profit before tax OR profit for the year, and the template's operating rollup
    # lists both children because exactly one is ever printed).
    assert set(groups) == {"equity_reserves", "associate_jv_share", "oci_composition",
                           "cf_starting_point"}
    assert groups["equity_reserves"].aggregate == "bs_equity__reserves"
    # Eight, not four: the balance-sheet revision moved share premium, treasury shares and shares
    # held for award schemes into the reserves rollup, and the exclusivity group has to cover every
    # component the rollup lists or three of them can be loaded alongside the aggregate.
    assert len(groups["equity_reserves"].components) == 8

    md = ont.metadata
    # No `supersedes`: one rulebook ships, so there is no predecessor for it to name. The field is
    # still live — `test_an_uploaded_replacement_supersedes_the_shipped_rulebook` uploads one that
    # uses it — and a value naming a key that ships nowhere would be a declaration pointing at
    # nothing.
    assert md.supersedes == "" and md.concept_count == 183
    # ``retained_defects`` is deliberately NOT asserted non-empty: its only entry recorded the two
    # canonical-key typos, which the balance-sheet revision fixed, and a list that keeps a fixed
    # defect in it is how a reader comes to distrust the block.
    # Neither list carries anything, and both are meant to be empty on this file: they describe a
    # DELTA against a predecessor, and there is none. Held as == [] rather than dropped so a value
    # appearing without one has to be deliberate.
    assert md.breaking_changes == [] and md.retained_defects == []

    netting = {n.id: n for n in ont.netting_rules}
    assert netting["cogs_inclusive_of_opex"].evidence_required is True
    assert netting["cogs_inclusive_of_opex"].on_apply
    assert netting["gross_expense_note_split"].decompose_into == [
        "pl_tax_expense__current_tax", "pl_tax_expense__deferred_tax"]

    examples = {e.id: e for e in ont.worked_examples}
    assert "residual_sweep_current_assets" in examples
    assert examples["residual_sweep_current_assets"].resolution
    assert examples["residual_sweep_current_assets"].reconciliation
    assert examples["residual_must_not_plug"].id == "residual_must_not_plug"

    ids = {i.id: i for i in ont.validation.identities}
    assert ids["pl_tci_tie"].severity == "blocking"
    # cf_to_bs_cash ships blocking since the revised spec called it a failure; x_check_dep is the
    # example of the other severity, and both carry the note that travels with the finding.
    assert ids["cf_to_bs_cash"].severity == "blocking" and ids["cf_to_bs_cash"].note
    assert ids["x_check_dep"].severity == "warning" and ids["x_check_dep"].note


# --- extraction_mode ---------------------------------------------------------------------------

def test_extraction_mode_accepts_derive_without_meaning_do_not_extract():
    """The v2 file uses ``extract_or_derive`` (7 concepts) and ``derive`` (1) where v1 only had
    ``extract``/``do_not_extract``. Both new values stay EXTRACTABLE: a subtotal the framework can
    derive is still one a filing may print on the face, and refusing the printed row would sweep
    it into the section residual instead of mapping it."""
    from app.services.mapping import OntologyMatcher

    ont = load_ontology(_v2())
    modes = {m.canonical_key: m.extraction_mode for m in ont.mappings}
    derived = [k for k, v in modes.items() if v == "extract_or_derive"]
    assert modes["pl_profit_before_exceptional_items_and_tax"] == "derive"
    assert "pl_income__total_income" in derived
    assert not [k for k, v in modes.items() if v == "do_not_extract"]

    extractable = set(OntologyMatcher(ont)._extractable_keys())
    assert "pl_profit_before_exceptional_items_and_tax" in extractable
    assert set(derived) <= extractable


def test_do_not_extract_is_still_the_only_value_that_suppresses():
    from app.schemas.ontology import OntologyDefinition, OntologyMapping
    from app.services.mapping import OntologyMatcher

    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[OntologyMapping(canonical_key="a", extraction_mode="derive"),
                  OntologyMapping(canonical_key="b", extraction_mode="do_not_extract")],
    )
    assert OntologyMatcher(ont)._extractable_keys() == ["a"]


# --- the inherits resolver ---------------------------------------------------------------------

def test_section_layer_reaches_every_concept_only_after_resolution():
    raw = _v2()
    unresolved = load_ontology(raw)
    # The four section-only fields are authored on no concept whatsoever, which is what makes the
    # fold load-bearing rather than a convenience.
    assert not [m for m in unresolved.mappings if m.section_scope or m.statement]

    ont = load_ontology(raw, resolve=True)
    placed = [m for m in ont.mappings if m.section_scope and m.statement]
    assert len(placed) == 183
    assert all(m.temporality and m.face_only is True for m in placed)

    m = _by_key(ont)["bs_current_assets__inventories"]
    assert m.section_scope == ["bs_s2_current_assets"]
    assert m.statement.value == "balance_sheet"
    assert m.temporality == "instant" and m.unit_of_account == "balance"
    assert m.note_use == "evidence_only" and m.sign_convention == "positive_expected"
    # The section's generic criteria arrive too, for a concept that states none of its own.
    assert m.include and m.include[0].startswith("The face amount")
    # …including prose a section carries but no concept does: a key the fold produces has to be
    # declared on the concept model or resolution loses it exactly as the gate says it would.
    assert _by_key(ont)["pl_tax_expense__current_tax"].note_use_rationale.startswith(
        "HKEX filings")


def test_a_key_declared_on_the_concept_beats_the_inherited_one():
    ont = load_ontology(_v2(), resolve=True)
    m = _by_key(ont)["bs_non_current_assets__others"]
    # The section says every concept under it is an exclusive leaf, positive, priority 50. This
    # one is the section's residual and says otherwise; if inheritance won, the residual would be
    # alias-matchable at ordinary priority and its sign would be a review trigger on every filing.
    assert m.value_scope == "exclusive_residual"     # section default: exclusive_leaf
    assert m.sign_convention == "either"             # section default: positive_expected
    assert m.match_priority == 0                     # section default: 50
    # …while the keys it is silent about still arrive from the section.
    assert m.section_scope == ["bs_s1_non_current_assets"]
    assert m.temporality == "instant"

    totals = _by_key(ont)["pl_income__total_income"]
    assert totals.extraction_mode == "extract_or_derive"   # section default: extract
    assert totals.unit_of_account == "subtotal"            # section default: flow

    # An override is not a merge: the concept's own criteria REPLACE the section's generic ones.
    ppe = _by_key(ont)["bs_non_current_assets__property_plant_and_equipment"]
    assert not any("Section subtotals" in x for x in ppe.exclude)


def test_unknown_inherits_is_a_clear_error_not_a_silent_no_op():
    raw = _v2()
    raw["mappings"][0]["inherits"] = "bs_s1_non_currrent_assets"     # one transposed letter
    with pytest.raises(UnknownInheritsError) as exc:
        resolve_inherits(raw)
    msg = str(exc.value)
    assert raw["mappings"][0]["canonical_key"] in msg
    assert "bs_s1_non_currrent_assets" in msg
    assert "bs_s1_non_current_assets" in msg          # names the sections that DO exist
    with pytest.raises(UnknownInheritsError):
        load_ontology(raw, resolve=True)
    # The read path stays tolerant, by design: one bad stored row must not 500 the ontology
    # editor, the language-parity page or an extraction run.
    assert len(load_ontology(raw).mappings) == 183


def test_resolution_does_not_mutate_the_definition_it_was_given():
    """Callers pass the ``definition`` of a live DB row; folding in place would rewrite the row's
    concepts with their section's values on the next flush."""
    raw = _v2()
    before = copy.deepcopy(raw)
    resolve_inherits(raw)
    assert raw == before


# --- the gate still bites ----------------------------------------------------------------------

def test_a_mistyped_key_in_the_v2_shape_is_still_reported():
    raw = _v2()
    raw["residual_framwork"] = {"note": "typo'd block name"}
    raw["mappings"][0]["never_sweeep"] = ["x"]
    raw["mappings"][1]["residual_policy"] = {"framework": "residual_framework", "plugg": False}
    raw["section_defaults"]["bs_s1_non_current_assets"]["face_onlyy"] = True
    raw["residual_framework"]["sweep"]["cross_sections"] = False
    raw["worked_examples"][0]["reconcilliation"] = "x"
    found = unknown_keys(raw, load_ontology(raw), limit=500)
    assert "residual_framwork" in found
    assert "mappings[0].never_sweeep" in found
    assert "mappings[1].residual_policy.plugg" in found
    assert "section_defaults.bs_s1_non_current_assets.face_onlyy" in found
    assert "residual_framework.sweep.cross_sections" in found
    assert "worked_examples[0].reconcilliation" in found


def test_a_bad_value_in_a_section_default_is_refused_on_both_paths():
    """A section default is inherited by every concept under it, so a misspelt value there is a
    misspelt value on twelve concepts at once — one no downstream comparison will ever match.
    Declaring the section vocabularies as closed sets is what turns that into a 422."""
    from pydantic import ValidationError as PydanticValidationError

    raw = _v2()
    raw["section_defaults"]["pl_s2_expenses"]["sign_convention"] = "negative_expcted"
    for kwargs in ({}, {"resolve": True}):
        with pytest.raises(PydanticValidationError, match="sign_convention"):
            load_ontology(raw, **kwargs)


# --- the resolver has to run where a filing is actually mapped -----------------------------------

def test_the_extraction_path_resolves_the_section_layer():
    """A resolver whose only callers are tests and the seeder does not describe production. The
    extraction route is the one call site whose configuration maps a filing, so it is the one that
    has to resolve: unresolved, every item's statement / section_scope / temporality / face_only is
    None and the section layer is absent, not degraded.

    THE CALL IT PROBES MOVED WITH THE ENGINE. It was
    ``load_ontology(ont_row.definition, resolve=True)`` against an ``ontology_versions`` row. There
    is no ontology row to load: the run resolves a ``line_item_versions`` row and hands the folded
    set to ``build_working_view``, which is what builds the OntologyDefinition this file is about.
    The resolve is on the LINE-ITEM load now, and that is the one that has to be there.
    """
    import inspect

    from app.api.routes import extractions

    src = inspect.getsource(extractions)
    assert "load_line_item_set(cfg_row.definition, resolve=True)" in src
    assert "build_working_view(st)" in src


def test_upload_refuses_an_inherits_naming_a_section_that_does_not_exist(client):
    """Paired with the above: because the extraction path resolves and raises, a stored
    configuration must not be able to carry a bad `inherits`. Unrefused, the fold silently
    contributes nothing and the configuration still reports itself as published.

    The door is ``POST /line-items``; it was ``POST /ontologies``. Same gate, same 422, same
    requirement that the message name the offender.
    """
    import copy
    import json
    from pathlib import Path

    d = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    bad = copy.deepcopy(json.loads(
        (d / "output_csv_hk_line_items.json").read_text(encoding="utf-8")))
    bad["line_items_key"] = "inherits_probe"
    section = bad["items"][0]["inherits"]
    bad["items"][0]["inherits"] = f"{section}_zz"                    # names no section_defaults key

    r = client.post("/api/v1/line-items", json={"definition": bad})
    assert r.status_code == 422
    body = json.dumps(r.json())
    assert f"{section}_zz" in body                      # names the offender, not just "invalid"
    assert bad["items"][0]["key"] in body


def test_the_shipped_configuration_still_uploads(client):
    """The guard must not accuse the file the product itself ships.

    Cleans up after itself: leaving a second set stored for this template would make it the one IN
    FORCE (latest-stored-wins), and every later test that reads the Template screen or picks a
    configuration would then be answered by a probe.
    """
    import json
    from pathlib import Path

    d = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    shipped = json.loads((d / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    shipped = {**shipped, "line_items_key": "shipped_upload_probe"}
    r = client.post("/api/v1/line-items", json={"definition": shipped})
    assert r.status_code == 201, r.text

    from app.db.base import SessionLocal
    from app.db.models import LineItemVersion

    with SessionLocal() as s:
        row = s.get(LineItemVersion, r.json()["id"])
        s.delete(row)
        s.commit()


# --- the shipped configuration is the one IN FORCE ----------------------------------------------

_SHIPPED_CFG_KEY = "output_csv_hk"
_SHIPPED_CFG_TEMPLATE = "output_csv_hk_v1"


def _shipped_set() -> dict:
    """The one shipped CONFIGURATION — the thing a run actually maps against.

    Not ``_v2()``: that is the working-view fixture (see the module docstring). These selection
    tests are about which stored ``line_item_versions`` row is in force, so they publish copies of
    the real shipped file against the real shipped template.
    """
    return json.loads((SAMPLES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))


def test_the_shipped_configuration_is_the_one_in_force(client):
    """One configuration ships, and with nothing stored after it, it is the one a run gets.

    The reason it wins is the only reason anything wins: it is the LATEST set stored for this
    template. Store one after it and that one runs
    (``test_the_latest_configuration_stored_is_the_one_that_runs``), which is what an admin
    uploading or correcting one is asking for.

    ``services.config_select`` replaced ``services.ontology_select``, and the row it returns is a
    ``line_item_versions`` row keyed ``line_items_key``.
    """
    from app.db.base import SessionLocal
    from app.services.config_select import select_for_template

    with SessionLocal() as s:
        row = select_for_template(s, _SHIPPED_CFG_TEMPLATE)
        assert row is not None and row.line_items_key == _SHIPPED_CFG_KEY


def test_the_list_says_which_configuration_is_in_force_and_it_is_the_pickers_answer(client):
    """``GET /line-items/versions`` DECLARES the set in force; the client no longer works it out.

    THE DEFECT THIS CLOSES. The Template screen ranked the served list itself, on
    ``[declares a supersession, version, key]``, under a comment claiming it mirrored the server's
    picker. It did not. The rule is latest-stored-wins, which needs ``created_at`` — a field that
    payload never carried, so the client could not have replicated it even in principle. The two
    sides therefore named DIFFERENT configurations: the run mapped against the one in force, and the
    screen captioned it "pinned to an older rulebook", telling an analyst their extraction had used
    something it had not.

    So the assertion here is not "the flag equals my expectations" — it is "the flag equals
    ``select_for_template``". Anything weaker would let the two drift apart again.

    Uploaded under a key that sorts EARLIER than the shipped one, so the row that must be flagged is
    exactly the row a key-ordered client ranking would have ranked LAST.
    """
    from app.db.base import SessionLocal
    from app.db.models import LineItemVersion
    from app.services.config_select import select_for_template

    later = {**_shipped_set(), "line_items_key": "aaa_in_force_probe"}
    created = client.post("/api/v1/line-items", json={"definition": later})
    assert created.status_code == 201, created.text
    try:
        rows = client.get("/api/v1/line-items/versions").json()
        flagged = {r["id"] for r in rows if r["in_force"]}
        with SessionLocal() as s:
            picked = {
                row.id
                for key in {r["target_template_key"] for r in rows if r["target_template_key"]}
                if (row := select_for_template(s, key)) is not None
            }
        assert flagged == picked
        # …and concretely, for this template that is the newcomer.
        by_id = {r["id"]: r for r in rows}
        mine = created.json()["id"]
        assert by_id[mine]["in_force"] is True
        shipped = next(r for r in rows if r["line_items_key"] == _SHIPPED_CFG_KEY)
        assert shipped["in_force"] is False
        # Exactly one configuration per template is in force — the flag is a choice, not a label
        # that several rows can wear.
        assert len([r for r in rows
                    if r["target_template_key"] == by_id[mine]["target_template_key"]
                    and r["in_force"]]) == 1
    finally:
        with SessionLocal() as s:
            s.delete(s.get(LineItemVersion, created.json()["id"]))
            s.commit()


# RETIRED: test_an_uploaded_replacement_supersedes_the_shipped_rulebook.
# RETIRED: test_supersession_only_counts_when_the_replacement_is_actually_stored.
#
# Both pinned SUPERSESSION, which is deliberately removed. The first uploaded a replacement
# declaring ``metadata.supersedes`` and asserted the served list flagged the predecessor
# ``superseded: True``; the second unit-tested ``ontology_select.superseded_keys`` — that a
# declaration naming a key which is not stored excludes nothing, and that a self-reference is not
# supersession.
#
# ``services.ontology_select`` is gone, replaced by ``services.config_select``, and with it went
# ``metadata.supersedes`` as a selection input, ``superseded_keys`` and the ``superseded`` /
# ``supersedes`` fields on the served rows (see ``config_select``'s docstring and
# ``extractions.rulebook_record``). There is ONE selection rule now — the latest set stored for the
# template wins — so a declaration about a key cannot decide what runs, and a second, declarative
# answer to "is this current" could only ever contradict the first. Nothing is left to assert.
#
# The mechanism these two were really guarding, "which configuration maps a filing must not be a
# property of insertion order", is asserted by
# ``test_the_latest_configuration_stored_is_the_one_that_runs`` below, which orders on ``created_at``
# and proves it with a key that sorts the other way. Do not reinstate supersession without the
# selection code that would read it.


def test_the_adopted_unbound_row_policy_is_stated_in_the_working_view_fixture():
    """The decision was to sweep an unclaimed in-section row into Others rather than surface it for
    review. A definition documenting a policy the engine deliberately does not follow is worse than
    one saying nothing — it is the only place a reviewer can look up what the pipeline is meant to
    do, and as authored this file said the opposite.

    ASKED OF THE WORKING-VIEW FIXTURE, not of the shipped configuration. The shipped set carries its
    own ``binding.unbound_row_policy`` in different words ("Route an unclaimed face value only to
    the residual for its resolved printed section…"), so repointing this test at it would assert
    prose that file does not contain. What is pinned here is that the block SURVIVES the load and
    still says the adopted thing; holding the shipped configuration's own wording to the same three
    exclusions is a data change, not a test repoint, and is left for whoever owns that file.
    """
    v2 = _v2()
    policy = v2["binding"]["unbound_row_policy"]

    assert "swept into that section's residual" in policy
    # And the exclusions the sweep still applies, because relaxing them is the corruption this
    # project has already fixed once: a narrative sentence in Others moves the subtotal.
    for kept_out in ("narrative sentence", "per-share", "subtotal"):
        assert kept_out in policy
    assert "still routed to review" in policy


def test_the_latest_configuration_stored_is_the_one_that_runs(client):
    """THE RULE, in one test: whatever was stored last for this template is what the next run maps
    against — uploaded by an admin, or published by correcting an item from the Template screen.

    THE DEFECT THIS CLOSES, and this file previously asserted its opposite. Selection had grown five
    ranking tests: drop declared supersessions, prefer the shipped key, prefer a definition that
    declares a supersession, prefer the incumbent key, then highest version. Each was added to work
    around the one before it, and together they meant a definition stored AFTER the shipped one did
    not take over — so publishing a corrected set beside an obsolete one changed nothing, and the
    product went on mapping filings with a definition whose tax bucket the specification had removed.
    This test used to REQUIRE that, under the name "does not displace the incumbent".

    Uploaded under a key that sorts EARLIER than the shipped one, so no sort order can be mistaken
    for the mechanism: it wins on recency alone.
    """
    from app.db.base import SessionLocal
    from app.db.models import LineItemVersion
    from app.services.config_select import select_for_template

    later = {**_shipped_set(), "line_items_key": "aaa_uploaded_later"}
    created = client.post("/api/v1/line-items", json={"definition": later})
    assert created.status_code == 201, created.text
    try:
        assert "aaa_uploaded_later" < _SHIPPED_CFG_KEY     # sorts first; recency is what decides
        with SessionLocal() as s:
            assert select_for_template(
                s, _SHIPPED_CFG_TEMPLATE).line_items_key == "aaa_uploaded_later"
    finally:
        with SessionLocal() as s:
            s.delete(s.get(LineItemVersion, created.json()["id"]))
            s.commit()

    # …and with it gone the shipped configuration is the latest again. Nothing is sticky: "in force"
    # is a question about what is stored now, not a title something keeps once it has held it.
    with SessionLocal() as s:
        assert select_for_template(s, _SHIPPED_CFG_TEMPLATE).line_items_key == _SHIPPED_CFG_KEY
