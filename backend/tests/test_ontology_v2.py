"""The v2.1 rulebook: it must load with NOTHING dropped, and its section layer must resolve.

Two failure modes are covered here. The first is the one ``unknown_keys`` exists for: a block the
schema does not declare is ignored by pydantic, so the rulebook publishes and simply is not the
rulebook that was authored. The second is subtler and cannot be seen from the file at all —
``section_scope``, ``statement``, ``temporality`` and ``face_only`` are authored on ZERO concepts,
only in ``section_defaults``, so without the ``inherits`` fold every concept loads with no section
at all and the section-first binding order the rulebook specifies has nothing to bind against.
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
    assert [m for m in ont.mappings if m.template_note] == []
    assert OntologyMapping(canonical_key="x", template_note="t").template_note == "t"
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
    assert d_and_a.aggregation_note


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
    extraction route is the one call site whose ontology maps a filing, so it is the one that has
    to resolve: unresolved, every concept's statement / section_scope / temporality / face_only is
    None and the section layer is absent, not degraded."""
    import inspect

    from app.api.routes import extractions

    src = inspect.getsource(extractions)
    assert "load_ontology(ont_row.definition, resolve=True)" in src


def test_upload_refuses_an_inherits_naming_a_section_that_does_not_exist(client):
    """Paired with the above: because the extraction path now resolves and raises, a stored
    rulebook must not be able to carry a bad `inherits`. Unrefused, the fold silently contributes
    nothing and the rulebook still reports itself as published."""
    import copy
    import json
    from pathlib import Path

    d = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    bad = copy.deepcopy(json.loads((d / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")))
    bad["ontology_key"] = "inherits_probe"
    bad["mappings"][0]["inherits"] = "bs_s1_non_current_assetz"      # one transposed letter

    r = client.post("/api/v1/ontologies", json={"definition": bad})
    assert r.status_code == 422
    body = json.dumps(r.json())
    assert "bs_s1_non_current_assetz" in body           # names the offender, not just "invalid"
    assert bad["mappings"][0]["canonical_key"] in body


def test_the_shipped_v2_rulebook_still_uploads(client):
    """The guard must not accuse the file it was built for.

    Cleans up after itself. The probe is a copy of v2, so it inherits v2's
    ``metadata.supersedes`` and becomes a SECOND rulebook replacing v1 — leaving it stored makes
    "which rulebook is in force" genuinely ambiguous, and every later test that reads the Template
    screen or picks a rulebook then depends on how two probe keys happen to sort.
    """
    import json
    from pathlib import Path

    d = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    v2 = json.loads((d / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    v2 = {**v2, "ontology_key": "v2_upload_probe"}
    r = client.post("/api/v1/ontologies", json={"definition": v2})
    assert r.status_code == 201, r.text

    from app.db.base import SessionLocal
    from app.db.models import OntologyVersion

    with SessionLocal() as s:
        row = s.get(OntologyVersion, r.json()["id"])
        s.delete(row)
        s.commit()


# --- v2 is the rulebook IN FORCE ----------------------------------------------------------------

def test_the_shipped_rulebook_is_the_one_in_force(client):
    """One rulebook ships, and with nothing stored after it, it is the one a run gets.

    The reason it wins is now the only reason anything wins: it is the LATEST rulebook stored for
    this template. That is a weaker claim than this test used to make — the shipped rulebook no
    longer outranks anything — and it is the honest one. Store a rulebook after it and that one runs
    (``test_the_latest_rulebook_stored_is_the_one_that_runs``), which is what an admin uploading or
    correcting one is asking for.
    """
    from app.db.base import SessionLocal
    from app.services.ontology_select import select_for_template

    with SessionLocal() as s:
        row = select_for_template(s, "hkfrs_hk_china_v1")
        assert row is not None and row.ontology_key == "hkfrs_hk_china"


def test_an_uploaded_replacement_supersedes_the_shipped_rulebook(client):
    """Which rulebook maps a filing must not be a property of insertion order — ``version`` counts
    edits to ONE key, so two rulebooks both at version 1 were a tie and the run used whichever row
    came back first. Adoption is declared by the author, in ``metadata.supersedes``.

    Exercised by UPLOADING a replacement, which is the route that exists now that one rulebook ships.
    It used to read the seeded pair, and that made the test a statement about what happened to be
    seeded rather than about the mechanism. The supersession is computed once, server-side, and
    travels with the row: two sides re-deriving it differently is how they come to disagree.

    Cleans up after itself — leaving a second rulebook stored makes "which one is in force"
    genuinely ambiguous for every later test that picks one.
    """
    from app.db.base import SessionLocal
    from app.db.models import OntologyVersion
    from app.services.ontology_select import select_for_template

    shipped = _v2()
    replacement = {**shipped, "ontology_key": "hkfrs_hk_china_next",
                   "metadata": {**shipped["metadata"], "supersedes": "hkfrs_hk_china"}}
    created = client.post("/api/v1/ontologies", json={"definition": replacement})
    assert created.status_code == 201, created.text
    try:
        by_key = {r["ontology_key"]: r for r in client.get("/api/v1/ontologies").json()}
        assert by_key["hkfrs_hk_china"]["superseded"] is True
        assert by_key["hkfrs_hk_china_next"]["superseded"] is False
        assert by_key["hkfrs_hk_china_next"]["supersedes"] == "hkfrs_hk_china"
        assert by_key["hkfrs_hk_china_next"]["schema_version"] == 2
        with SessionLocal() as s:
            assert select_for_template(s, "hkfrs_hk_china_v1").ontology_key == \
                "hkfrs_hk_china_next"
    finally:
        with SessionLocal() as s:
            s.delete(s.get(OntologyVersion, created.json()["id"]))
            s.commit()


def test_the_list_says_which_rulebook_is_in_force_and_it_is_the_pickers_answer(client):
    """``GET /ontologies`` DECLARES the rulebook in force; the client no longer works it out.

    THE DEFECT THIS CLOSES. The Template screen ranked the served list itself, on
    ``[declares a supersession, version, ontology_key]``, under a comment claiming it mirrored the
    server's picker. It did not. The rule is latest-stored-wins, which needs ``created_at`` — a
    field this payload has never carried, so the client could not have replicated it even in
    principle. The two sides therefore named DIFFERENT rulebooks: the run mapped against the one in
    force, and the screen captioned it "pinned to an older rulebook", telling an analyst their
    extraction had used something it had not.

    So the assertion here is not "the flag equals my expectations" — it is "the flag equals
    ``select_for_template``". Anything weaker would let the two drift apart again.

    Uploaded under a key that sorts EARLIER and declares no supersession, so the row that must be
    flagged is exactly the row the OLD client ranking would have ranked LAST.
    """
    from app.db.base import SessionLocal
    from app.db.models import OntologyVersion
    from app.services.ontology_select import select_for_template

    later = {**_v2(), "ontology_key": "aaa_in_force_probe"}
    later["metadata"] = {k: v for k, v in later["metadata"].items() if k != "supersedes"}
    created = client.post("/api/v1/ontologies", json={"definition": later})
    assert created.status_code == 201, created.text
    try:
        rows = client.get("/api/v1/ontologies").json()
        flagged = {r["id"] for r in rows if r["in_force"]}
        with SessionLocal() as s:
            picked = {
                row.id
                for key in {r["target_template_key"] for r in rows if r["target_template_key"]}
                if (row := select_for_template(s, key)) is not None
            }
        assert flagged == picked
        # …and concretely, for this template that is the newcomer — the row a
        # [supersedes, version, key] ranking would have placed last of the two.
        by_id = {r["id"]: r for r in rows}
        mine = created.json()["id"]
        assert by_id[mine]["in_force"] is True
        assert by_id[mine]["supersedes"] is None
        shipped = next(r for r in rows if r["ontology_key"] == "hkfrs_hk_china")
        assert shipped["in_force"] is False
        # Exactly one rulebook per template is in force — the flag is a choice, not a label that
        # several rows can wear.
        assert len([r for r in rows
                    if r["target_template_key"] == by_id[mine]["target_template_key"]
                    and r["in_force"]]) == 1
    finally:
        with SessionLocal() as s:
            s.delete(s.get(OntologyVersion, created.json()["id"]))
            s.commit()


def test_supersession_only_counts_when_the_replacement_is_actually_stored():
    """A rulebook naming one that was never loaded must not exclude anything, and a v1 does not
    become unusable because some v2 exists elsewhere."""
    from app.services.ontology_select import superseded_keys

    class R:
        def __init__(self, key, sup=None):
            self.ontology_key = key
            self.definition = {"metadata": {"supersedes": sup}} if sup else {}

    assert superseded_keys([R("v1"), R("v2", "v1")]) == {"v1"}
    assert superseded_keys([R("v2", "v1")]) == set()          # the v1 it replaces is absent
    assert superseded_keys([R("v1"), R("v2")]) == set()        # nothing claims a replacement
    assert superseded_keys([R("v1", "v1")]) == set()           # a self-reference is not supersession


def test_the_adopted_unbound_row_policy_is_stated_in_the_rulebook():
    """The decision was to sweep an unclaimed in-section row into Others rather than surface it for
    review. The shipped rulebook has to SAY that: as authored it said the opposite, and an ontology
    documenting a policy the engine deliberately does not follow is worse than one saying nothing —
    it is the only place a reviewer can look up what the pipeline is meant to do."""
    import json
    from pathlib import Path

    d = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    v2 = json.loads((d / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    policy = v2["binding"]["unbound_row_policy"]

    assert "swept into that section's residual" in policy
    # And the exclusions the sweep still applies, because relaxing them is the corruption this
    # project has already fixed once: a narrative sentence in Others moves the subtotal.
    for kept_out in ("narrative sentence", "per-share", "subtotal"):
        assert kept_out in policy
    assert "still routed to review" in policy


def test_the_latest_rulebook_stored_is_the_one_that_runs(client):
    """THE RULE, in one test: whatever was stored last for this template is what the next run maps
    against — uploaded by an admin, or published by correcting a concept from the Template screen.

    THE DEFECT THIS CLOSES, and this file previously asserted its opposite. ``select_for_template``
    had grown five ranking tests: drop declared supersessions, prefer the shipped key, prefer a
    rulebook that declares a supersession, prefer the incumbent key, then highest version. Each was
    added to work around the one before it, and together they meant a rulebook stored AFTER the
    shipped one did not take over — so publishing a corrected 185-concept rulebook beside an obsolete
    173-concept one changed nothing, and the product went on mapping filings with a rulebook whose
    tax bucket the specification had removed. This test used to REQUIRE that, under the name "does
    not displace the incumbent".

    Uploaded under a key that sorts EARLIER than the shipped one, so no sort order can be mistaken
    for the mechanism: it wins on recency alone.
    """
    from app.db.base import SessionLocal
    from app.db.models import OntologyVersion
    from app.services.ontology_select import select_for_template

    later = {**_v2(), "ontology_key": "aaa_uploaded_later"}
    later["metadata"] = {k: v for k, v in later["metadata"].items() if k != "supersedes"}
    created = client.post("/api/v1/ontologies", json={"definition": later})
    assert created.status_code == 201, created.text
    try:
        assert "aaa_uploaded_later" < "hkfrs_hk_china"     # sorts first; recency is what decides
        with SessionLocal() as s:
            assert select_for_template(s, "hkfrs_hk_china_v1").ontology_key == "aaa_uploaded_later"
    finally:
        with SessionLocal() as s:
            s.delete(s.get(OntologyVersion, created.json()["id"]))
            s.commit()

    # …and with it gone the shipped rulebook is the latest again. Nothing is sticky: "in force" is a
    # question about what is stored now, not a title something keeps once it has held it.
    with SessionLocal() as s:
        assert select_for_template(s, "hkfrs_hk_china_v1").ontology_key == "hkfrs_hk_china"
