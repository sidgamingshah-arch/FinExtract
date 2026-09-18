"""EVERY authorable field on a line item is reachable through the item edit — and absent, ``null``
and ``[]`` are three different answers.

WHAT WENT WRONG, so nobody trims the edit body back. ``ItemEdit`` accepted THIRTEEN fields while
``LineItemDef`` declares fifty-odd, and the Line Items screen was read-only on a justification it
wrote down: the definitions merely DESCRIBED derivations five services computed, so nothing
downstream read them. That justification expired. Line items is the single configuration engine,
``services.working_view`` builds the matcher from the set, and a run pins
``extraction_runs.line_item_version_id`` — the definitions DRIVE extraction. A field that is
authorable in the schema and unreachable through the edit body is therefore a control the product
claims to have and does not, and the failure is silent: the author saves, the screen says nothing,
and the configuration is unchanged.

SO THE EXHAUSTIVENESS IS ASSERTED AGAINST THE MODEL, not against a transcribed list. ``_ROUND_TRIP``
below carries one entry per ``LineItemDef`` field, and the first test compares its keys to
``LineItemDef.model_fields``. A field added to the schema next year fails THAT test — which is the
only way a new field cannot quietly become unauthorable again, the way these fifty did. The same
test asserts the converse (every accepted edit field writes something) so a control cannot be added
to the body and wired to nothing.

THREE STATES, BECAUSE THE SCHEMA HAS THREE. ``is not None`` cannot tell an omitted field from an
explicit ``null``, so the thirteen-field chain could not express a CLEAR at all. Presence
(``model_fields_set``) can: omitted leaves the stored value alone, ``null`` writes "nothing was
said" where the schema has such a state, and ``[]`` is a CONFIGURED EMPTY that is stored as empty
and never re-defaulted. The last one is not theoretical — 475 of 475 shipped items take their gate
from ``section_defaults``, and ``resolve_line_item_inherits`` folds a section's value in only where
the item is SILENT, so an ``[]`` that is dropped instead of stored comes back as the section's list.
That is asserted here through the resolved read, not just the raw one.

THE LOCALE CONTRACT IS THE ONE THING AN EDITOR CAN BREAK INVISIBLY. ``aliases`` replaces ONE
locale's ``aliases_i18n`` list (and the base list when that locale is the set's default), so editing
the Chinese aliases must leave the English ones byte-identical. A map-shaped write is exactly how
that goes wrong, which is why ``aliases_i18n`` is not accepted as a whole map at all.

AND A REFUSAL IS INFORMATION, NOT AN ERROR TO SWALLOW. The endpoint re-validates against the target
template before it publishes, so every refusal here is something an author has to fix on a control —
and one that cannot be pinned to a control lands in a banner they read once and cannot act on. Ten
of them are driven below, each asserted to name its own field, and none of them may leave a version
behind: a refused edit writes nothing at all.

The probe template and configuration are thrown away afterwards (the ``client`` fixture's database
is session-scoped), following ``test_template_detail_totals.probe_pair``.
"""
from __future__ import annotations

import json
import re

import pytest

API = "/api/v1"

_TPL_KEY = "edit_fields_probe_tpl"
_CFG_KEY = "edit_fields_probe_cfg"
_EDITED = "probe_cash"

_TEMPLATE = {
    "template_key": _TPL_KEY,
    "name": "Authorable-field probe",
    "statements": [{
        "type": "balance_sheet",
        "sections": [
            {"node_id": "tot", "canonical_key": "probe_parent", "label": "Total cash",
             "role": "line"},
            {"node_id": "cash", "canonical_key": _EDITED, "label": "Cash", "role": "line"},
            {"node_id": "other", "canonical_key": "probe_other", "label": "Other",
             "role": "line"},
        ],
    }],
}

# A SECTION LAYER, because "a configured empty is never re-defaulted" is only a real claim about an
# item whose section WOULD have supplied a value. `bs_ca` declares both `section_scope` and
# `match_priority`, and the item inherits it.
_SET = {
    "schema_version": 1,
    "line_items_key": _CFG_KEY,
    "target_template_key": _TPL_KEY,
    "locale": "en",
    "supported_locales": ["en", "zh"],
    "section_defaults": {
        # `note_use` is here for the explicit-null test. It is one of the few fields a section may
        # declare (`SectionDefaults`) that a line item may ALSO still write and that has a real null
        # state, so it is what makes "a declared null beats the section" observable now that
        # `match_priority`, `analyst_bucket` and `face_only` are no longer item-writable.
        # (`sign_convention` is section-declarable and item-writable too, but a null on it is not a
        # "nothing said" state — the item-level field is special-cased into `sign_rule.convention`.)
        "bs_ca": {"statement": "balance_sheet", "section_scope": ["bs_ca"], "match_priority": 5,
                  "note_use": "evidence_only"},
        "bs_nca": {"statement": "balance_sheet", "section_scope": ["bs_nca"]},
    },
    # THE SHARED HALF OF A PROSE RULE, so `note_source.prose_subject` in `_ROUND_TRIP` has a
    # vocabulary to name. A line naming one this set does not carry is refused at load
    # (`_prose_rules_are_complete`), which is what makes the round-trip above a real check on the
    # pair rather than on one field in isolation.
    "prose_grammar": {
        "connective": ["held at", "included in", "计入"],
        "subjects": {"cash": ["cash", "现金"]},
    },
    "items": [
        {"key": _EDITED, "label": "Cash", "inherits": "bs_ca",
         "aliases": ["Cash"], "aliases_i18n": {"en": ["Cash"], "zh": ["現金"]},
         "definition": "as first stored",
         "keyword_hints": ["cash"]},
        {"key": "probe_parent", "label": "Total cash", "aliases": ["Total cash"],
         "inherits": "bs_ca"},
        # PARENTED FROM THE START, so the cycle case below has an edge to close: with every item a
        # root, no single reparent can make a cycle and the check would go untested.
        {"key": "probe_other", "label": "Other", "aliases": ["Other"], "inherits": "bs_ca",
         "parent": "probe_parent"},
        # OFF-TEMPLATE ON PURPOSE — the `namespace` refusal needs a key the template does not
        # declare (13 of 475 shipped items are deliberately `internal`).
        {"key": "probe_internal", "label": "Internal part", "namespace": "internal",
         "aliases": ["Internal part"], "inherits": "bs_ca"},
    ],
}


# ── the contract: one entry per LineItemDef field ─────────────────────────────────────────────
#
# ``LineItemDef field -> (the ItemEdit field that writes it, the value sent)``. What is stored is
# asserted EQUAL to the value sent, which is why every nested object is sent whole: the apply dumps
# a sub-model in JSON mode, so a partially-sent object would come back with its own defaults filled
# in and the comparison would be against this table's idea of them rather than the model's.
# FIELDS THAT CANNOT BE COHERENT WITH THE REST OF ONE PATCH, and are round-tripped on their own
# below instead. This is not an exemption from being authorable — the exhaustiveness test still
# requires an entry in `_ROUND_TRIP` for each — it is that the combined body sets
# `type: "calculated"` and these fields are only legal on another type, so including them would
# assert a shape a screen can never send.
#
# `prompt` and `output_structure`: `LineItemDef` refuses either unless the line is `extracted`. A
# calculated line's figure comes from arithmetic, so the model is never asked about it (the prompt
# would never be sent) and no arithmetic yields a sentence (so text output is incoherent).
#
# `llm_only_if_note_tagged` is the third, and it collides on a DIFFERENT field: the combined body
# also round-trips `extraction_mode: "extract_or_derive"`, and the flag is legal only on `extract`.
# Same reasoning one level along — a derivable line takes its figure from declared arithmetic, so a
# note printed beside a row says nothing about whether that arithmetic should run.
#
# All three are proved authorable by their own tests below.
_NOT_COHERENT_WITH_THE_REST = {"prompt", "output_structure", "llm_only_if_note_tagged",
                               "note_selection"}

_ROUND_TRIP: dict[str, tuple[str, object]] = {
    # meaning — the four the user named, plus the label
    "label": ("label", "Cash and cash equivalents"),
    "definition": ("definition", "IAS 7 cash and cash equivalents, net of nothing."),
    # Extra instruction for THIS line, sent inside its own candidate entry. Round-tripped like any
    # other prose field; the constraint that it is only legal on an `extracted` line is a
    # `LineItemDef` validator and is tested separately, because it is a refusal rather than a
    # write. The probe item this table is applied to is `extracted`, so this round-trips.
    "prompt": ("prompt", "Prefer the note total over the face figure when they disagree."),
    # What the line OUTPUTS: a number, a phrase off the page, or prose the model writes. Carried
    # here so the coverage guard sees it; excluded from the combined body because a non-`value`
    # structure is only legal on an `extracted` line, and asserted on its own below.
    "output_structure": ("output_structure", "phrase"),
    "exclude_criteria": ("exclude_criteria", ["bank overdrafts repayable on demand"]),
    # structure
    "type": ("type", "calculated"),
    "in_output": ("in_output", False),
    "parent": ("parent", "probe_parent"),
    "rollup": ("rollup", "alternatives"),
    "order": ("order", 7),
    "namespace": ("namespace", "template"),
    "value_scope": ("value_scope", "exclusive_residual"),
    "is_gross_parent": ("is_gross_parent", True),
    "children_if_decomposed": ("children_if_decomposed", ["probe_other"]),
    "sole_component_of": ("sole_component_of", "probe_parent"),
    "expected_components": ("expected_components", ["probe_other"]),
    "never_sweep": ("never_sweep", ["probe_parent"]),
    "residual_policy": ("residual_policy", {
        "framework": "residual_framework", "section_scope": "bs_ca",
        "population": "sweep_only", "cross_section": True, "notes_as_source": True,
        # `plug` always ties, so it hides the mapping gap it papers over — authorable, and the
        # screen says so beside it.
        "plug": True, "itemise": False}),
    # the gate
    "inherits": ("inherits", "bs_nca"),
    "statement": ("statement", "balance_sheet"),
    "section_scope": ("section_scope", ["bs_ca", "bs_nca"]),
    "match_priority": ("match_priority", 42),
    "extraction_mode": ("extraction_mode", "extract_or_derive"),
    "scopes": ("scopes", ["balance_sheet", "notes"]),
    "side": ("side", "asset"),
    "allow_contra": ("allow_contra", True),
    # The note-reference threshold: only ask the model when the face prints a note beside the row,
    # and report 0 where it prints none. Legal only on `extraction_mode: extract`, which the probe
    # item is (the default), so it round-trips; the refusal on the other two modes is a validator
    # and is tested in test_note_tag_gate.py because it is a refusal rather than a write.
    "llm_only_if_note_tagged": ("llm_only_if_note_tagged", True),
    # How this line's notes are found: its meaning scored against note headers (`semantic`, the
    # default) or the authored `note_title_any` regexes (`patterns`). Legal only on `extract`, so
    # it joins `_NOT_COHERENT_WITH_THE_REST` for the same reason the flag above does — the combined
    # body round-trips `extraction_mode: "extract_or_derive"`.
    "note_selection": ("note_selection", "patterns"),
    # WHERE AN EXTRACTED LINE'S FIGURE IS READ FROM — the one question that replaced three implicit
    # declarations (`SectionDefaults.where()`, the presence of a `note_source`, and whether that
    # object carried prose patterns) which an author could satisfy two of and not the third.
    # `note_tables` is the value the 60 note-sourced lines migrated to; `prose` skips the row
    # search; `face` keeps the line out of `stages.note_sourced` entirely.
    "route": ("route", "note_tables"),
    # EVERY STATEMENT THIS LINE MAY BE CLAIMED ON. A list because one caption is genuinely printed
    # on two — depreciation on the income statement and again in the cash-flow reconciliation — and
    # with the single `statement` an author had to pick one printing while the other was actively
    # REFUSED by the gate rather than merely unmatched.
    "statements": ("statements", ["profit_and_loss", "cash_flow"]),
    "note_use": ("note_use", "decomposition_allowed"),
    "face_only": ("face_only", True),
    # THE OBJECT THAT REPLACED THE 162-ALTERNATIVE WHITELIST (`_QUALIFYING_RE`, which refused a
    # filing writing "Depreciation charge for the year"). Widening it is the single edit this
    # screen was built to make possible, and it was reachable from nowhere.
    #
    # THE FULL SUB-MODEL, every key. A partially-sent object comes back with its own defaults
    # filled in, so the comparison would be against this table's idea of them rather than the
    # model's — which is why the three semantic groups are here even though they are empty. They
    # are the SAME TWO LEVELS as the patterns beside them, in terms rather than regexes: which note
    # (scored against headers), then which rows inside it (scored against row captions).
    # `prose_subject` AND `prose_landed_in` are the prose route as it is now authored — plain
    # phrases, compiled into patterns by `services.prose_grammar`. `prose_any` is beside them as
    # the raw escape hatch it has become, so this table covers both routes. The subject NAMES a
    # vocabulary in `_SET["prose_grammar"]`; a name that set does not carry is refused at load, so
    # the two have to agree and this is the pair that proves they do.
    "note_source": ("note_source", {
        "note_title_any": [r"^cash and cash equivalents"],
        "row_caption_any": [r"bank balances?"],
        "row_caption_none": [r"restricted"],
        "prose_any": [r"cash[^.]{0,80}?held\s+at\s+bank"],
        "prose_subject": "cash",
        "prose_landed_in": ["bank balances", "现金"],
        "note_terms": ["cash and cash equivalents", "现金及现金等价物"],
        "row_terms": ["bank balances", "银行存款"],
        "row_terms_none": ["restricted"],
        # WHICH NOTE COLUMN the part reads — "" is the primary measure, "allowance" is 坏账准备.
        # A non-default value on purpose: the round trip is what proves the configuration screen
        # can author the field, and `""` would pass whether the screen carried it or not.
        "measure": "allowance"}),
    # recognition
    "aliases": ("aliases", ["Cash at bank", "Bank balances"]),
    "alias_matching": ("alias_matching", "disabled"),
    "pattern": ("pattern", r"^cash\b"),
    "regex_hints": ("regex_hints", [r"cash", r"bank balances?"]),
    "keyword_hints": ("keyword_hints", ["cash", "bank"]),
    "exclude_hints": ("exclude_hints", [r"overdraft"]),
    # measurement
    "temporality": ("temporality", "instant"),
    "unit_of_account": ("unit_of_account", "balance"),
    # THE RENAME, and the reason test_the_two_sign_fields_are_two_questions exists: the model field
    # `sign_convention` is the EXPECTATION review validation reads, and `ItemEdit.sign_convention`
    # is the legacy 3-token spelling that writes `sign_rule.convention` instead.
    "sign_convention": ("sign_expectation", "positive_expected"),
    "sign_rule": ("sign_rule", {"convention": "natural_positive",
                                "flip_if_label_matches": [r"overdraft"]}),
    "analyst_bucket": ("analyst_bucket", "current_assets"),
    # assembly
    # THE OPERATOR IS A SCALAR, not a member of `terms`: it is a property of the GROUP, and a
    # per-term copy would be four ways to disagree about one rule. `max` here rather than the `sum`
    # default, so the round trip proves a NON-default value survives.
    "terms_op": ("terms_op", "max"),
    "terms": ("terms", [
        {"ref": "probe_parent", "const": None, "sign": 1, "abs": False, "role": "required"},
        {"ref": "probe_other", "const": None, "sign": -1, "abs": True, "role": "adjustment"}]),
    "cascade": ("cascade", [
        {"id": "P1",
         "terms": [{"ref": "probe_parent", "const": None, "sign": 1, "abs": False,
                    "role": "required"}],
         # HOW THE TERMS ABOVE COMBINE. Here on the rung as well as on the line, because a rung is
         # the other place terms live — `bs_nca__due_from_related_parties_ltp`'s MAX_VALID rung is a
         # `max` over its three Finds, which is the rule its replaced six rungs could not express.
         "terms_op": "sum",
         "note": "the note's own total, when it states one",
         "refuse_negative": False,
         # SENT WHOLE, like every other nested object in this table — the apply dumps a sub-model in
         # JSON mode, so a rung sent without this comes back carrying the model's default and the
         # comparison would be against this table's idea of a rung rather than the model's.
         # `outranks_printed` decides whether a resolved rung may displace a figure the filing
         # PRINTED, which only a rung that reconstructs something the face does not state should do.
         "outranks_printed": False}]),
    "implemented_by": ("implemented_by", "probe_service"),
    # prose
    "decomposition_rule": ("decomposition_rule", "= total cash − other"),
    "others_rule": ("others_rule", "may hold nothing but immaterial balances"),
    "derivation": ("derivation", "total cash less the other line"),
    "notes_as_source_rationale": ("notes_as_source_rationale",
                                  "the note states the split the face only summarises"),
}

# The two fields of ``LineItemDef`` that this endpoint deliberately does not accept, each with its
# reason served to the screen as ``vocab.not_editable`` rather than left implicit:
#
#   ``key``          — the identity every other declaration names AND this endpoint's own selector,
#                      so an inline rename has no coherent target.
#   ``aliases_i18n`` — reachable one locale at a time through ``aliases`` + ``locale``. A
#                      map-shaped write is precisely how editing zh clobbers en, which is why the
#                      whole map is not writable and the locale tests below pin the alternative.
_LOCKED = {"key", "aliases_i18n"}


# ── the probe pair ────────────────────────────────────────────────────────────────────────────


def _drop(model, key_column, key: str) -> None:
    from sqlalchemy import delete

    from app.db.base import SessionLocal

    with SessionLocal() as session:
        session.execute(delete(model).where(key_column == key))
        session.commit()


@pytest.fixture
def probe(client):
    """A throwaway template and a configuration published against it, removed again afterwards.

    Function-scoped: several tests here publish new versions, and a test that asserted a version
    COUNT would otherwise be reading whatever the previous test left behind.
    """
    from app.db.models import LineItemVersion, TemplateVersion

    r = client.post(f"{API}/templates", json={"definition": _TEMPLATE})
    assert r.status_code == 201, r.text
    tpl = r.json()
    r = client.post(f"{API}/line-items", json={"definition": _SET})
    assert r.status_code == 201, r.text
    try:
        yield tpl, r.json()
    finally:
        _drop(LineItemVersion, LineItemVersion.line_items_key, _CFG_KEY)
        _drop(TemplateVersion, TemplateVersion.template_key, _TPL_KEY)


def _stored(client, version_id: str, key: str = _EDITED) -> dict:
    """One item's definition AS STORED — unresolved, which is what an editor has to show.

    ``GET /line-items/versions/{id}`` serves the definition exactly as authored: an inherited value
    merged in silently would be re-published as though it had been declared on the item.
    """
    r = client.get(f"{API}/line-items/versions/{version_id}")
    assert r.status_code == 200, r.text
    items = r.json()["definition"]["items"]
    return next(d for d in items if d["key"] == key)


def _in_force(client, key: str = _EDITED) -> dict:
    """The same item as the RESOLVED read serves it, section layer folded in.

    The two reads answer different questions and both matter: the raw one says what the author
    declared, this one says what a run would match under.
    """
    r = client.get(f"{API}/line-items?template_key={_TPL_KEY}")
    assert r.status_code == 200, r.text

    def walk(items):
        for item in items:
            if item["key"] == key:
                return item
            found = walk(item.get("children") or [])
            if found is not None:
                return found
        return None

    found = walk(r.json()["items"])
    assert found is not None, f"{key} is not in the configuration in force"
    return found


def _versions(client) -> list[dict]:
    return [v for v in client.get(f"{API}/line-items/versions").json()
            if v["line_items_key"] == _CFG_KEY]


def _patch(client, version_id: str, body: dict):
    return client.patch(f"{API}/line-items/versions/{version_id}/items", json=body)


def _saved(client, version_id: str, body: dict) -> str:
    """Apply an edit that must succeed, and return the id of the version it published."""
    r = _patch(client, version_id, body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] != version_id, "an edit must ADD a version, never mutate the one a run pinned"
    return out["id"]


# ── 1. exhaustiveness, asserted against the model ─────────────────────────────────────────────


def test_every_field_on_the_schema_is_authorable_or_declared_locked():
    """The whole point. A field marked editable that an author cannot change is the defect.

    Compared to ``LineItemDef.model_fields`` rather than to a list transcribed from it, so a field
    added to the schema later FAILS HERE instead of quietly becoming unauthorable — which is how
    thirteen of fifty came to be the whole editor.
    """
    from app.api.routes.line_items import _MODEL_TO_EDIT_FIELD, ItemEdit
    from app.schemas.line_items import LineItemDef

    fields = set(LineItemDef.model_fields)
    # WITHDRAWN, deliberately: read by nothing (all four accept decisions compare against the
    # global `settings.extraction.auto_accept_confidence`). Absent from the model, so accepting it
    # would store a key the upload gate then refuses.
    assert "min_confidence_to_auto_accept" not in fields, (
        "the per-item accept bar was withdrawn; re-add it only alongside code that reads it")
    # NOT A FIELD AT ALL: `children` is a projection of `parent`, computed by `GET /line-items` on
    # every read. Storage is flat, so editing the projection cannot be persisted.
    assert "children" not in fields, "children is computed from parent, not stored"

    missing = sorted(fields - set(_ROUND_TRIP) - _LOCKED)
    assert not missing, (
        f"{len(missing)} field(s) of LineItemDef are neither round-tripped here nor declared "
        f"locked: {missing} — add them to ItemEdit and to _ROUND_TRIP, or lock them with a reason")
    stale = sorted(set(_ROUND_TRIP) - fields)
    assert not stale, f"_ROUND_TRIP names fields the schema no longer has: {stale}"

    accepted = set(ItemEdit.model_fields)
    unreachable = sorted(d for d, (e, _) in _ROUND_TRIP.items() if e not in accepted)
    assert not unreachable, f"ItemEdit accepts no field that writes {unreachable}"
    # The rename table is the ONLY place a wire name differs from a model name. Getting that wrong
    # attributes a sign-expectation refusal to the legacy normalisation control — a different
    # question on a different row of the screen.
    assert {d: e for d, (e, _) in _ROUND_TRIP.items() if d != e} == _MODEL_TO_EDIT_FIELD

    # THE CONVERSE, so a control cannot be added to the body and wired to nothing: `key` and
    # `locale` are selectors, and `sign_convention` is the legacy 3-token spelling that writes
    # `sign_rule.convention`.
    assert accepted - {e for e, _ in _ROUND_TRIP.values()} == {"key", "locale", "sign_convention"}


def test_a_field_no_surface_offers_is_refused_rather_than_stored(client, probe):
    """THE OTHER HALF OF RETIREMENT, and the half that used to be missing.

    Retiring a control removed the invitation to set it on the Line Items screen and nothing else:
    the endpoint went on accepting the field, so a value could still arrive by API call for a
    question no screen asks. That value is then driving extraction while the console says the field
    is not configurable — unreviewable by construction.

    Asserted field by field with its REASON, because a refusal that does not say what decides the
    question instead leaves the author nowhere to go. `_ROUND_TRIP` still carries an entry for each
    of these: the wire shape is unchanged, only the permission is.
    """
    from app.api.routes.line_items import _NOT_CONFIGURABLE

    _tpl, cfg = probe
    checked = 0
    for def_field, (edit_field, value) in sorted(_ROUND_TRIP.items()):
        if def_field not in _NOT_CONFIGURABLE and edit_field not in _NOT_CONFIGURABLE:
            continue
        refused = _NOT_CONFIGURABLE.get(edit_field) or _NOT_CONFIGURABLE[def_field]
        res = client.patch(f"/api/v1/line-items/versions/{cfg['id']}/items",
                           json={"key": _EDITED, edit_field: value})
        assert res.status_code == 422, (
            f"{edit_field} is declared not configurable and the endpoint accepted it "
            f"({res.status_code})")
        detail = res.json()["detail"]
        named = [e for e in detail["errors"] if e["field"] == edit_field]
        assert named, f"the refusal of {edit_field} names no field: {detail['errors']}"
        assert refused in named[0]["message"], (
            f"{edit_field}'s refusal does not say what decides it instead: {named[0]['message']}")
        checked += 1
    assert checked >= 15, f"only {checked} refused fields exercised — the table has 23"


def test_the_fields_that_are_not_editable_say_why(client, probe):
    """A field withheld from the form is a defect; read-only with a reason is a decision.

    The reasons are SERVED (``vocab.not_editable``) rather than written into the screen, so a field
    cannot disappear from the editor with no reason attached to it.
    """
    r = client.get(f"{API}/line-items?template_key={_TPL_KEY}")
    assert r.status_code == 200, r.text
    reasons = r.json()["vocab"]["not_editable"]
    assert set(reasons) == _LOCKED | {"children", "min_confidence_to_auto_accept"}
    assert all(reason.strip() for reason in reasons.values()), reasons
    assert "locale" in reasons["aliases_i18n"], (
        "the whole map is not writable BECAUSE it is written one locale at a time; the reason has "
        "to say so or an author reads it as 'the Chinese aliases are not editable'")


# ── 2. the round trip ─────────────────────────────────────────────────────────────────────────


def test_one_patch_round_trips_a_value_for_every_authorable_field(client, probe):
    """One save, every field, read back off the stored definition.

    Not field-by-field: an editor saves a form, so the fields have to be coherent TOGETHER —
    `calculated` with terms, a residual with a policy, `from_section`'s remedy satisfied. Fifty
    separate patches would each pass on a shape the screen never sends.
    """
    from app.api.routes.line_items import _NOT_CONFIGURABLE

    _tpl, cfg = probe
    body = {"key": _EDITED}
    for _def_field, (edit_field, value) in _ROUND_TRIP.items():
        if _def_field in _NOT_COHERENT_WITH_THE_REST:
            continue
        # A FIELD NO SURFACE OFFERS IS NO LONGER WRITABLE, so sending it here would assert that a
        # refusal is a round-trip. `_ROUND_TRIP` deliberately keeps its entry — the table is the
        # record of what the WIRE carries, and the exhaustiveness test above still needs one per
        # schema field — but the combined body sends only what an author can actually author.
        # Each of these is proved REFUSED, with its reason, by
        # `test_a_field_no_surface_offers_is_refused_rather_than_stored` below.
        if _def_field in _NOT_CONFIGURABLE or edit_field in _NOT_CONFIGURABLE:
            continue
        body[edit_field] = value

    new_id = _saved(client, cfg["id"], body)
    stored = _stored(client, new_id)
    wrong = {f: (stored.get(f), value) for f, (_e, value) in _ROUND_TRIP.items()
             if f not in _NOT_COHERENT_WITH_THE_REST
             and f not in _NOT_CONFIGURABLE
             and _ROUND_TRIP[f][0] not in _NOT_CONFIGURABLE
             and stored.get(f) != value}
    assert not wrong, f"{len(wrong)} field(s) did not round-trip: {wrong}"
    # A SAVE THAT SILENTLY DOES NOTHING IS THE DEFECT BEING FIXED, so the version is asserted to be
    # a different one and the key it edited is named back.
    assert stored["key"] == _EDITED


def test_the_saved_version_is_then_the_one_in_force(client, probe):
    """An edit publishes a NEW version and that version is what the next run maps against.

    Versioned rather than in-place because a run pins the version it used: mutating a stored
    definition would retroactively change how a past run is explained.
    """
    _tpl, cfg = probe
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "definition": "Edited meaning."})

    rows = {v["id"]: v for v in _versions(client)}
    assert set(rows) == {cfg["id"], new_id}, "the edit must add a version, not replace one"
    assert rows[new_id]["in_force"] is True
    assert rows[cfg["id"]]["in_force"] is False
    assert rows[new_id]["version"] == rows[cfg["id"]]["version"] + 1
    assert _in_force(client)["definition"] == "Edited meaning."


# ── 3. absent, null and [] are three different answers ────────────────────────────────────────


def test_a_field_left_out_of_the_body_is_untouched(client, probe):
    """Presence, not truthiness. The thirteen-field chain tested `is not None`, which cannot tell
    an omitted field from an explicit null — so it could not express a clear at all."""
    _tpl, cfg = probe
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "label": "Renamed"})
    stored = _stored(client, new_id)
    assert stored["label"] == "Renamed"
    assert stored["definition"] == "as first stored"
    assert stored["keyword_hints"] == ["cash"]
    assert stored["aliases"] == ["Cash"]
    assert "match_priority" not in stored, (
        "the item never declared one; writing a default would detach it from its section")


def test_an_explicit_null_is_stored_as_null_and_beats_the_section(client, probe):
    """``null`` means "nothing was said" — a real configuration, and not the same as absent.

    Asserted through the RESOLVED read as well, because that is where it costs something: the item
    inherits ``bs_ca``, which declares ``note_use: evidence_only``, and a null that was dropped
    instead of stored would come back carrying the section's value.

    ON SURVIVING FIELDS, and this test has now moved TWICE for the same reason — which is itself
    the point worth recording. It used ``match_priority``, ``analyst_bucket`` and ``face_only``
    until all three were refused as no longer configurable, then ``note_use``, which is refused
    too: decomposition is always allowed, so the question is gone. Each time, the patch was
    rejected before the null-handling it exists to prove was ever exercised.

    The property is about how an explicit ``null`` is TREATED, not about any particular field, so
    it moves again — to ``note_source`` as the nullable object and ``statement`` as the nullable
    scalar ``SectionDefaults`` also declares (``bs_ca`` declares ``balance_sheet``), which is the
    half that costs something. ``statement`` is the ONLY such scalar left: of the twelve fields
    ``SectionDefaults`` carries, eight are refused and the other three are not nullable on
    ``LineItemDef``. If it is ever retired or made multi-valued, this test needs the same move and
    there will be nothing obvious to move it to.
    """
    _tpl, cfg = probe
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "note_source": None,
                                        "statement": None})
    stored = _stored(client, new_id)
    for field in ("note_source", "statement"):
        assert field in stored, f"{field}: an explicit null was dropped rather than stored"
        assert stored[field] is None, f"{field}: {stored[field]!r}"
    assert _in_force(client)["statement"] is None, (
        "the item declares 'nothing said'; the section's value must not be folded back in")


def test_a_field_with_no_null_state_says_what_clears_it(client, probe):
    """A list has no null state, and a refusal that just says "not valid" leaves the author
    guessing which of `[]`, `""` and omitting it was meant."""
    _tpl, cfg = probe
    before = len(_versions(client))
    r = _patch(client, cfg["id"], {"key": _EDITED, "keyword_hints": None})
    assert r.status_code == 422, r.text
    error = r.json()["detail"]["errors"][0]
    assert error["field"] == "keyword_hints"
    assert "[]" in error["message"], error
    assert len(_versions(client)) == before, "a refused edit must publish nothing"


def test_a_configured_empty_list_stays_empty(client, probe):
    """A CONFIGURED EMPTY VALUE MEANS EMPTY, never "fall back to a default".

    ``section_scope`` is the one that proves it: the item inherits ``bs_ca``, whose
    ``section_scope`` is ``['bs_ca']``, and ``resolve_line_item_inherits`` supplies a section value
    only where the item is SILENT. An ``[]`` that is skipped rather than stored is therefore
    indistinguishable from never having been typed — the author clears the gate, saves, and the
    gate is still there.
    """
    _tpl, cfg = probe
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "keyword_hints": [],
                                        "section_scope": []})
    stored = _stored(client, new_id)
    for field in ("keyword_hints", "section_scope"):
        assert stored.get(field) == [], f"{field}: {stored.get(field)!r}"
    # Re-read, resolved: the section layer must not put its list back.
    resolved = _in_force(client)
    assert resolved["section_scope"] == [], (
        f"the cleared gate came back as {resolved['section_scope']!r} from section_defaults")
    assert resolved["keyword_hints"] == []


# ── 4. the locale contract ────────────────────────────────────────────────────────────────────


def test_editing_one_locales_aliases_leaves_the_others_byte_identical(client, probe):
    """Editing Chinese must never clobber English. A map-shaped write is how that happens, which
    is why the map is not writable and this is the only door."""
    _tpl, cfg = probe
    before = _stored(client, cfg["id"])
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "locale": "zh",
                                        "aliases": ["現金及現金等價物"]})
    stored = _stored(client, new_id)
    assert stored["aliases_i18n"]["zh"] == ["現金及現金等價物"]
    assert stored["aliases_i18n"]["en"] == before["aliases_i18n"]["en"] == ["Cash"]
    assert stored["aliases"] == before["aliases"] == ["Cash"], (
        "zh is not the set's default locale, so the base list is not its to touch")


def test_the_default_locale_also_writes_the_base_list(client, probe):
    """The base ``aliases`` list is what non-localised consumers read, so the default locale
    mirrors into it — and only the default locale does."""
    _tpl, cfg = probe
    assert _SET["locale"] == "en", "this test is about the SET's default locale"
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "locale": "en",
                                        "aliases": ["Cash at bank", "Bank balances"]})
    stored = _stored(client, new_id)
    assert stored["aliases"] == ["Cash at bank", "Bank balances"]
    assert stored["aliases_i18n"]["en"] == ["Cash at bank", "Bank balances"]
    assert stored["aliases_i18n"]["zh"] == ["現金"], "the other locale is untouched"


# ── 5. every refusal names the field that caused it ───────────────────────────────────────────


def _refusal(response) -> tuple[str | None, int | None, str]:
    """The field a refusal is addressed to, and the entry within it, from either envelope.

    TWO ENVELOPES, and that is not a defect to hide here. Everything the route refuses itself comes
    back as ``detail: {error, message, errors: [{field, index, message}]}``. A body that does not
    PARSE never reaches the route: ``NoteSource`` compiles its own patterns in a model validator, so
    FastAPI answers with its own ``detail: [{loc, msg}]`` — ``loc`` is ``["body", "note_source"]``
    and the offending group and index are in the message ("row_caption_none[0] '(': missing )").
    Both name the control; this normalises them so the assertion is one assertion.
    """
    detail = response.json()["detail"]
    if isinstance(detail, dict) and "errors" in detail:
        first = detail["errors"][0]
        return first.get("field"), first.get("index"), json.dumps(detail, ensure_ascii=False)
    first = detail[0]
    path = ".".join(str(p) for p in first["loc"] if p != "body")
    hit = re.search(r"(\w+)\[(\d+)\]", str(first.get("msg", "")))
    if hit:
        return f"{path}.{hit.group(1)}", int(hit.group(2)), json.dumps(detail,
                                                                       ensure_ascii=False)
    return path, None, json.dumps(detail, ensure_ascii=False)


# Each case: the body, the field the refusal must be addressed to, and the entry index within it.
# Every one of these is a mistake an author makes on a control, and a refusal they cannot pin to
# that control is a refusal they cannot act on.
_REFUSALS: tuple[tuple[str, dict, str, int | None], ...] = (
    # A regex that does not compile is a SILENT hole, not a crash: the veto simply stops vetoing.
    #
    # ON `regex_hints`, NOT ON `pattern`, AND THAT IS THE POINT OF THE MOVE. This case read
    # `{"pattern": "("}` and expected the regex complaint at entry 0. `pattern` is now retired —
    # `regex_hints` is the list form and is what the matcher reads — so the FIRST refusal on that
    # body is the retirement notice at index None and the regex complaint is pushed to index 1.
    # Asserting the retirement would test the tombstone twice (the `analyst_bucket` case below
    # already covers that shape) and would stop testing regex validation at all, so the case moves
    # to the field that still exists and keeps checking what it was written to check.
    ("regex_hints", {"key": _EDITED, "regex_hints": ["cash", "("]}, "regex_hints", 1),
    ("exclude_hints", {"key": _EDITED, "exclude_hints": ["overdraft", "("]},
     "exclude_hints", 1),
    # A silent sign inversion is one of the most expensive errors on a statement, and this is the
    # only field that causes one. The index belongs to the list, not to the object.
    ("sign_rule", {"key": _EDITED,
                   "sign_rule": {"convention": "natural", "flip_if_label_matches": ["("]}},
     "sign_rule.flip_if_label_matches", 0),
    # The group that replaced the 162-alternative whitelist. Refused by `NoteSource`'s own
    # validator, so this is the case that arrives in FastAPI's envelope — see `_refusal`.
    ("note_source", {"key": _EDITED, "note_source": {"row_caption_none": ["("]}},
     "note_source.row_caption_none", 0),
    ("parent_self", {"key": _EDITED, "parent": _EDITED}, "parent", None),
    # probe_other's parent is probe_parent already, so this closes the loop.
    ("parent_cycle", {"key": "probe_parent", "parent": "probe_other"}, "parent", None),
    # A `ref` naming no key is a term that silently contributes nothing to the sum.
    ("terms_ref", {"key": _EDITED, "terms": [{"ref": "probe_parent"}, {"ref": "no_such_key"}]},
     "terms", 1),
    # A bucket naming no section loses the rows to Others with nothing saying why.
    ("analyst_bucket", {"key": _EDITED, "analyst_bucket": "not_a_section"},
     "analyst_bucket", None),
    # ATTRIBUTED TO `namespace`, NOT TO `key`: the key is fine and is not editable, so an author
    # told "the key does not exist in the target template" would change a field they cannot.
    ("namespace", {"key": "probe_internal", "namespace": "template"}, "namespace", None),
    # A dangling `inherits` is not a load error but a silent no-op leaving the item with NO gate.
    ("inherits", {"key": _EDITED, "inherits": "no_such_section"}, "inherits", None),
)


@pytest.mark.parametrize("case,body,field,index",
                         _REFUSALS, ids=[c[0] for c in _REFUSALS])
def test_a_refusal_names_the_field_that_caused_it(client, probe, case, body, field, index):
    """The endpoint re-validates against the template, so a refusal is information the author
    needs — and one with no field on it lands in a banner they read once and cannot act on."""
    _tpl, cfg = probe
    before_versions = len(_versions(client))
    before_stored = _stored(client, cfg["id"], body["key"])
    r = _patch(client, cfg["id"], body)
    assert r.status_code == 422, r.text
    got_field, got_index, shown = _refusal(r)
    assert got_field == field, f"{case}: refused against {got_field!r}, not {field!r}: {shown}"
    assert got_index == index, f"{case}: named entry {got_index}, not {index}: {shown}"
    assert len(_versions(client)) == before_versions, (
        f"{case}: a refused edit published a version anyway")
    assert _stored(client, cfg["id"], body["key"]) == before_stored, (
        f"{case}: a refused edit changed the stored definition")


def test_a_refused_edit_changes_nothing_at_all(client, probe):
    """Every problem in one pass, and nothing written until there are none: an author fixing three
    fields is told about three, and the definition is left exactly as it was."""
    _tpl, cfg = probe
    before = _stored(client, cfg["id"])
    r = _patch(client, cfg["id"], {"key": _EDITED, "label": "Never stored",
                                   "pattern": "(", "exclude_hints": ["("],
                                   "regex_hints": ["(unclosed"]})
    assert r.status_code == 422, r.text
    fields = {e["field"] for e in r.json()["detail"]["errors"]}
    # `confusable_with` was the third field here and is gone from the model; `regex_hints` takes
    # its place because the property under test is "every problem in one pass", which needs three
    # broken fields and does not care which.
    assert fields == {"pattern", "exclude_hints", "regex_hints"}, fields
    assert _stored(client, cfg["id"]) == before, "a refused edit wrote part of itself"
    assert len(_versions(client)) == 1


# ── 6. the withdrawn control, and the two sign fields ─────────────────────────────────────────


def test_the_withdrawn_accept_bar_is_accepted_and_ignored(client, probe):
    """``min_confidence_to_auto_accept`` was removed deliberately — "remove line item level
    control for now" — because it was read by NOTHING: all four accept decisions compare against
    the global ``settings.extraction.auto_accept_confidence``, and the shipped per-item value was
    STRICTER than the live global bar, so enforcing it would newly route to review every row
    scoring between 0.80 and 0.85.

    A body carrying it is not refused (a client sending a stale field must not be blocked from
    saving the fields that do exist) and the key never reaches the stored definition — storing it
    would put a key in the file that the upload gate then refuses.
    """
    _tpl, cfg = probe
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "min_confidence_to_auto_accept": 0.99,
                                        "label": "Cash, still editable"})
    stored = _stored(client, new_id)
    assert stored["label"] == "Cash, still editable", "the real field still saved"
    assert "min_confidence_to_auto_accept" not in stored
    assert "min_confidence_to_auto_accept" not in json.dumps(
        client.get(f"{API}/line-items/versions/{new_id}").json())


def test_the_two_sign_fields_are_two_questions(client, probe):
    """The two sign questions stay two questions — and the EXPECTATION now belongs to the section.

    They answer different things. The sign a line is EXPECTED to carry is a review trigger, which
    ``test_normalize_sign`` and ``test_validation_block`` both reason over; ``sign_rule.convention``
    is how a value is NORMALISED when it is stored. One name for both is how an author edits one
    thinking they changed the other, which is why both halves are asserted in one test.

    WHAT MOVED. ``sign_expectation`` was the item-level control for the expectation and is now
    refused — "this never varies inside a section — set it on the section" — so the test no longer
    proves it round-trips onto ``sign_convention``; it proves the REFUSAL, which is the current
    contract and is the half an author can act on. The normalisation route is unchanged and is
    still asserted end to end below.
    """
    _tpl, cfg = probe

    # THE EXPECTATION IS A SECTION QUESTION NOW, and the refusal has to say so on its own name.
    r = _patch(client, cfg["id"], {"key": _EDITED, "sign_expectation": "negative_expected"})
    assert r.status_code == 422, r.text
    field, _index, shown = _refusal(r)
    assert field == "sign_expectation", r.text
    assert "section" in shown, f"the refusal must send the author to the section: {shown!r}"

    # THE NORMALISATION ROUTE IS UNCHANGED: the legacy 3-token spelling still maps into
    # `sign_rule.convention` and nowhere else.
    new_id = _saved(client, cfg["id"], {"key": _EDITED, "sign_convention": "expense_contra"})
    stored = _stored(client, new_id)
    assert stored["sign_rule"]["convention"] == "natural_negative", (
        "the legacy 3-token spelling maps into `sign_rule.convention` and nowhere else")
    # And the legacy vocabulary is closed: three tokens, of six real SignConvention values.
    r = _patch(client, new_id, {"key": _EDITED, "sign_convention": "natural_negative"})
    assert r.status_code == 422, r.text
    field, _index, shown = _refusal(r)
    assert field == "sign_convention" and "sign_rule.convention" in shown, (
        "the refusal has to point at the field that CAN express the full vocabulary")


def test_a_prompt_is_refused_because_it_is_merged_into_the_definition(client, probe):
    """``prompt`` is retired, and this asserts the retirement rather than the field.

    It used to prove the field was authorable — "the per-line prompt, on the one type it is sent
    for". The two prose fields asked the same author the same question twice and arrived in the
    same request under different keys, so they are now ONE: `definition` carries the whole
    instruction, `LineItemDef._merge_prompt_into_definition` folds any stored `prompt` into it on
    load, and the shipped seed has had its 61 such lines folded in place.

    What is worth holding is the same thing the neighbouring retirements hold: that the refusal
    ARRIVES rather than the write silently succeeding. A field writable by API and visible on no
    screen is a value nobody can see, review or explain — and for this field it would additionally
    be words the request no longer has a key for.
    """
    _tpl, cfg = probe
    before = len(_versions(client))

    r = _patch(client, cfg["id"],
               {"key": _EDITED, "type": "extracted", "prompt": _ROUND_TRIP["prompt"][1]})

    assert r.status_code == 422, r.text
    field, _index, shown = _refusal(r)
    assert field == "prompt", r.text
    assert "merged into `definition`" in shown, shown
    assert len(_versions(client)) == before, "a refused edit must publish nothing"


def test_output_structure_is_refused_because_no_line_declares_it(client, probe):
    """``output_structure`` is retired, and this asserts the retirement rather than the field.

    It used to prove the field was authorable — "a line can be told to hold a phrase off the page
    rather than a number". Measured against the shipped configuration, NO line item declares it, so
    the control invited an author to set something nothing reads. It is refused now, and what is
    worth holding is that the refusal ARRIVES rather than the write silently succeeding: a field
    writable by API and visible on no screen is a value nobody can see, review or explain.
    """
    _tpl, cfg = probe
    before = len(_versions(client))

    r = _patch(client, cfg["id"],
               {"key": _EDITED, "type": "extracted", "output_structure": "phrase"})

    assert r.status_code == 422, r.text
    field, _index, shown = _refusal(r)
    assert field == "output_structure", r.text
    assert "no line item declares this" in shown, shown
    assert len(_versions(client)) == before, "a refused edit must publish nothing"


def test_the_note_tag_threshold_round_trips_on_an_extract_line(client, probe):
    """A line can be told that a note reference beside the row is its evidence threshold.

    ON `type`, NOT ON `extraction_mode`. This asked for `extraction_mode: "extract"`, which is now
    retired — `type` says how a figure is obtained: extracted, calculated or derived — so the patch
    was refused on the retired field before it reached the threshold it meant to prove. The
    declaration this test is about is `llm_only_if_note_tagged`, and it is legal on an EXTRACTED
    line, which is what `type` now names.
    """
    _tpl, cfg = probe

    new_id = _saved(client, cfg["id"], {"key": _EDITED, "type": "extracted",
                                        "llm_only_if_note_tagged": True})

    assert _stored(client, new_id)["llm_only_if_note_tagged"] is True


def test_the_note_tag_threshold_is_refused_with_a_message_naming_its_own_control(client, probe):
    """The refusal has to reach the control the author can actually change. Attribution is by
    substring over the editable field names, so the validator message spells
    `llm_only_if_note_tagged` verbatim — without that the refusal lands on `extraction_mode`, and
    the author reads "the extraction mode is wrong" about a field they did not touch."""
    _tpl, cfg = probe

    # `value_scope: "exclusive_residual"` rather than the retired `extraction_mode: "derive"`, and
    # NOT `type: "derived"` either — that one is refused first for a different and correct reason ("a
    # derived line needs a cascade or `implemented_by`"), which would make this test pass or fail on
    # the cascade rule instead of on attribution.
    #
    # IT USED TO BE `alias_matching: "disabled"`, which was the never-asked declaration until the
    # others master took that over: the marker is now `value_scope: exclusive_residual`, and
    # `alias_matching` is gone from the model. Either way what this test needs is a body whose ONLY
    # problem is the note-tag threshold, which is what makes the attribution observable.
    r = _patch(client, cfg["id"], {"key": _EDITED, "value_scope": "exclusive_residual",
                                   "llm_only_if_note_tagged": True})

    assert r.status_code == 422, r.text
    named = [e for e in r.json()["detail"]["errors"]
             if e["field"] == "llm_only_if_note_tagged"]
    assert named, r.json()["detail"]
    assert "llm_only_if_note_tagged" in named[0]["message"]


def test_the_note_selection_round_trips_on_an_extract_line(client, probe):
    """A line can decline having the filing's own note reference offered to it first.

    THE VOCABULARY CHANGED WITH THE FIELD'S MEANING. It was `semantic` / `patterns` — which
    selector found a line's notes — and the two were never alternatives: `identified_notes` passes
    a pattern-named note unconditionally and scoring ADDS to it, so `patterns` removed the line
    from the semantic pass and gained nothing. All 539 lines declared neither. The field now says
    whether a note the FILING cites beside this line's face caption goes first.

    ON `type`, NOT ON `extraction_mode`, for the reason given on the note-tag threshold above: the
    mode field is retired and `type` carries the same statement. `note_selection` is legal on an
    extracted line and meaningless on a derived one, which `_coherent` enforces.
    """
    _tpl, cfg = probe

    new_id = _saved(client, cfg["id"], {"key": _EDITED, "type": "extracted",
                                        "note_selection": "any"})

    assert _stored(client, new_id)["note_selection"] == "any"


def test_note_selection_defaults_to_taking_the_filings_own_reference_first(client, probe):
    """THE DEFAULT IS THE POLICY, and the policy changed with the field.

    It used to be `semantic`, because scoring needs no pattern per phrasing and a line nobody wrote
    regexes for still got notes. Scoring is now unconditional, so that is no longer a choice — and
    what the default states instead is that a PRINTED reference outranks a probe. Measured over
    twelve filings: 92 asked-about lines carry one, and 30 cite a note scoring did not deliver.
    """
    from app.schemas.line_items import LineItemDef

    assert LineItemDef(key="x").note_selection == "cited_first"


def test_every_line_defaults_to_a_number_so_existing_configuration_is_unchanged(client, probe):
    """THE COMPATIBILITY ASSERTION. Adding an output structure must not reinterpret 475 shipped
    lines: every one of them means a figure, and a default of anything else would silently take
    them all out of the arithmetic.

    Asserted in the two places it has to hold, which are different statements:

    * STORAGE gains nothing. A line that never declared an output structure still does not carry
      the key, so adding the field did not rewrite a single authored file.
    * THE RESOLVED READ says `value`. Absence has to MEAN a number rather than mean nothing, or
      every existing line would arrive at the arithmetic with no answer to what it is.
    """
    _tpl, cfg = probe

    assert "output_structure" not in _stored(client, cfg["id"])
    assert _in_force(client).get("output_structure", "value") == "value"


def test_prose_is_refused_without_a_prompt_to_write_it_from(client, probe):
    """Prose is specified by the line's prompt and by nothing else, so prose with no prompt has
    nothing to generate from — it would publish an empty cell for a reason no reader could see."""
    _tpl, cfg = probe

    r = client.patch(f"{API}/line-items/versions/{cfg['id']}/items",
                     json={"key": _EDITED, "type": "extracted", "output_structure": "prose",
                           "prompt": ""})

    assert r.status_code == 422, r.text
    fields = [e["field"] for e in r.json()["detail"]["errors"]]
    assert any(f in fields for f in ("output_structure", "prompt")), fields


def test_text_output_is_refused_on_a_line_whose_value_is_arithmetic(client, probe):
    """There is no calculation that produces a sentence. Refused rather than ignored, because a
    calculated line quietly holding text would reach every subtotal that sums it."""
    _tpl, cfg = probe

    r = client.patch(f"{API}/line-items/versions/{cfg['id']}/items",
                     json={"key": _EDITED, "type": "calculated",
                           "terms": [{"ref": _EDITED, "sign": 1}],
                           "output_structure": "prose"})

    assert r.status_code == 422, r.text
    fields = [e["field"] for e in r.json()["detail"]["errors"]]
    assert any(f in fields for f in ("output_structure", "type")), fields


def test_a_prompt_on_any_line_is_folded_into_its_definition(client, probe):
    """`prompt` IS NO LONGER A SEPARATE QUESTION, so there is nothing left to refuse.

    This test used to assert a 422: a prompt on a `calculated` line would have been stored, shown
    and never sent, which is indistinguishable from one that works. The two prose fields are now
    ONE — `LineItemDef._merge_prompt_into_definition` concatenates them on load — and a
    `definition` is legal on every line whatever its type. So the edit is ACCEPTED and the words
    survive in the field that is actually read.

    That is the point of the merge rather than a weakening of the check: the old refusal existed
    because the words would have gone nowhere, and after the merge they go somewhere. What must
    still hold is that nothing is silently dropped, which is what this asserts.
    """
    from app.api.routes.line_items import _NOT_CONFIGURABLE
    from app.schemas.line_items import LineItemSet

    _tpl, cfg = probe

    # 1. THE FIELD IS NO LONGER A QUESTION, so the endpoint refuses it — the same treatment every
    #    other retired control gets, and for the same reason: a value accepted by API for a
    #    question no screen asks is driving extraction unreviewably.
    assert "prompt" in _NOT_CONFIGURABLE
    r = client.patch(f"{API}/line-items/versions/{cfg['id']}/items",
                     json={"key": _EDITED, "prompt": "authored for a question nobody asks"})
    assert r.status_code == 422, r.text
    fields = [e["field"] for e in r.json()["detail"]["errors"]]
    assert "prompt" in fields, fields

    # 2. A SET AUTHORED BEFORE THE MERGE STILL LOADS, with neither half of its prose lost. This is
    #    the half that makes the refusal safe: the words do not need re-authoring, they are folded.
    legacy = LineItemSet.model_validate({
        "line_items_key": "legacy_probe", "target_template_key": "t",
        "items": [{"key": "legacy_line", "label": "Legacy",
                   "definition": "What this line is.",
                   "prompt": "And the extra instruction."}],
    })
    folded = legacy.items[0]
    assert "What this line is." in folded.definition
    assert "And the extra instruction." in folded.definition
    assert folded.prompt == "", "prompt must be emptied by the fold"
