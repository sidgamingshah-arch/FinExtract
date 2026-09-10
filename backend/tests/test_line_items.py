"""Line-item definitions: validation, evaluation order, the arithmetic, and what the editor sees.

The eight output lines and their sub-line items are configuration now, so the questions these
tests answer are the ones a configurator has to get right before anyone trusts it: does it refuse
a formula that cannot be computed, does it evaluate inputs before the things that need them, and
does its arithmetic behave the way the shipped cascades say it behaves. The last one matters most,
and it changed character: the five-rung cascade below used to be a PORT of `services.deprec_impairment`
(~208 hand-enumerated note titles, row captions and formula variants), and these tests checked the
port against the service. That service is DELETED and the figure must now come from configuration,
so there is nothing left to be a parity check against — the shipped cascade IS the definition of
the arithmetic, which makes every assertion here more load-bearing, not less. Retired pins for the
removed service live in test_retired_derivations.py.

The last section adds the two facts the Line Items EDITOR is built on. The screen was read-only,
and a read-only screen may render whatever it likes: nothing it shows can be refused, and nothing
it shows can be mistaken for something the author wrote. Neither holds any more, so both of those
now need pinning — what the controls may offer (`vocab`) and which of an item's values it actually
declared rather than inherited (`declared_fields`).
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from typing import get_args

import pytest
from pydantic import ValidationError

from app.core.models.enums import SignConvention, StatementType
from app.schemas.line_items import (AliasMatching, CascadeRung, ExtractionMode, LineItemDef,
                                    LineItemType, Namespace, NoteSource, NoteUse, Rollup,
                                    SearchScope, Side, SignExpectation, Temporality, Term,
                                    UnitOfAccount, ValueScope, load_line_item_set)
from app.services.buckets import BUCKET_KEYS
from app.services.line_items import build, check_rollups, evaluate_all

OPER = "is_pl__deprec_and_impairment_oper_exp"
OPEX_SUBS = ["sub__rd_depreciation", "sub__selling_marketing_depreciation",
             "sub__ga_depreciation", "sub__operating_expense_depreciation"]
ASSET_SUBS = ["sub__ppe_depreciation", "sub__prepaid_lease_depreciation",
              "sub__fixed_asset_depreciation", "sub__investment_property_depreciation",
              "sub__cip_depreciation"]
COS = "sub__cos_depreciation"


def _sub(key: str, parent: str = OPER) -> LineItemDef:
    return LineItemDef(key=key, type="extracted", in_output=False, parent=parent,
                       scopes=["notes"], side="none")


def _deprec_cascade() -> LineItemDef:
    """The five rungs as the generated configuration states them — the only statement of them left.

    `implemented_by` is a neutral label here on purpose: no service implements this line any more.
    """
    r = lambda k, sign=1, role="required": Term(ref=k, sign=sign, role=role)
    return LineItemDef(
        key=OPER, type="derived", in_output=True, implemented_by="rulebook_derivation",
        cascade=[
            CascadeRung(id="P1", terms=[r(k, 1, "any_of") for k in OPEX_SUBS]),
            CascadeRung(id="P2", terms=[r("sub__pbt_oper_exp_depreciation")]),
            CascadeRung(id="P3", terms=[r("sub__pbt_depreciation"), r(COS, -1, "adjustment")]),
            CascadeRung(id="P4", terms=[r(k, 1, "any_of") for k in ASSET_SUBS] + [r(COS, -1, "adjustment")]),
            CascadeRung(id="P5", terms=[r("sub__cfo_depreciation"), r(COS, -1, "adjustment")]),
        ])


def _registry():
    subs = [_sub(k) for k in OPEX_SUBS + ASSET_SUBS
            + ["sub__cfo_depreciation", "sub__pbt_oper_exp_depreciation",
               "sub__pbt_depreciation"]]
    subs.append(_sub(COS, parent="is_pl__deprec_and_impairment_cos"))
    subs.append(LineItemDef(key="is_pl__deprec_and_impairment_cos", type="derived",
                            implemented_by="rulebook_derivation",
                            cascade=[CascadeRung(id="COS_P1", terms=[Term(ref=COS)])]))
    return build(subs + [_deprec_cascade()])


def _run(**extracted):
    reg = _registry()
    assert reg.ok, [p.message for p in reg.problems]
    vals = {k: Decimal(str(v)) for k, v in extracted.items()}
    return reg, evaluate_all(reg, vals)


# --- the cascade, rung by rung ------------------------------------------------------------------

def test_p1_sums_the_operating_expense_notes():
    _, out = _run(sub__rd_depreciation=100, sub__ga_depreciation=250)

    assert out[OPER].value == Decimal("350")
    assert out[OPER].rung_used == "P1"


def test_p1_still_sums_when_a_filing_discloses_only_some_of_the_four():
    """Each opex term is `any_of`, so one of four is still a sum and not a refusal — that is what
    the spec means by summing the notes a filing happens to print."""
    _, out = _run(sub__operating_expense_depreciation=90)

    assert out[OPER].value == Decimal("90") and out[OPER].rung_used == "P1"


def test_p2_is_used_when_no_opex_note_was_found():
    _, out = _run(sub__pbt_oper_exp_depreciation=520)

    assert out[OPER].value == Decimal("520") and out[OPER].rung_used == "P2"


def test_p3_deducts_the_cost_of_sales_share():
    _, out = _run(sub__pbt_depreciation=800, sub__cos_depreciation=180)

    assert out[OPER].value == Decimal("620") and out[OPER].rung_used == "P3"


def test_p3_takes_no_deduction_when_the_filing_discloses_none():
    """A filing with no cost-of-sales depreciation is taken to charge none, so the deduction is
    an `adjustment` and the figure it is deducted from is `required`."""
    _, out = _run(sub__pbt_depreciation=800)

    assert out[OPER].value == Decimal("800") and out[OPER].rung_used == "P3"


def test_p3_is_skipped_entirely_when_its_base_is_missing():
    """The base is required, so a rung with only the deduction present must fall through rather
    than publish a negative depreciation charge."""
    _, out = _run(sub__cos_depreciation=180, sub__cfo_depreciation=900)

    assert out[OPER].rung_used == "P5"
    assert out[OPER].value == Decimal("720")


def test_p4_sums_the_asset_notes_then_deducts():
    _, out = _run(sub__ppe_depreciation=400, sub__cip_depreciation=50,
                  sub__cos_depreciation=100)

    assert out[OPER].value == Decimal("350") and out[OPER].rung_used == "P4"


def test_the_rungs_are_tried_in_order_and_the_first_that_resolves_wins():
    """Every rung computable at once: P1 must win, and the lower rungs must not contribute."""
    _, out = _run(sub__rd_depreciation=10, sub__pbt_oper_exp_depreciation=999,
                  sub__pbt_depreciation=888, sub__cfo_depreciation=777)

    assert out[OPER].rung_used == "P1" and out[OPER].value == Decimal("10")


def test_a_filing_that_discloses_nothing_produces_no_figure_rather_than_zero():
    """Publishing 0 would assert the group charged no depreciation. It asserts nothing."""
    _, out = _run()

    assert out[OPER].value is None and out[OPER].rung_used is None


def test_the_cost_of_sales_line_reads_its_own_sub_line_item():
    _, out = _run(sub__cos_depreciation=180)

    assert out["is_pl__deprec_and_impairment_cos"].value == Decimal("180")


# --- the arithmetic itself ----------------------------------------------------------------------

def test_absolute_value_is_separate_from_sign():
    """A filing prints accumulated depreciation negative or in brackets. `abs` takes the
    magnitude, `sign` decides whether to add or subtract it — collapsing the two would make one
    of them inexpressible."""
    reg = build([
        _sub("sub__accum"),
        LineItemDef(key="calc__charge", type="calculated",
                    terms=[Term(ref="sub__accum", abs=True), Term(const=120000, sign=-1)]),
    ])
    out = evaluate_all(reg, {"sub__accum": Decimal("-1842330")})

    assert out["calc__charge"].value == Decimal("1722330")


def test_a_missing_required_input_makes_the_result_blank_not_zero():
    reg = build([
        _sub("sub__revenue"), _sub("sub__cos"),
        LineItemDef(key="calc__gross", type="calculated",
                    terms=[Term(ref="sub__revenue"), Term(ref="sub__cos", sign=-1)]),
    ])
    out = evaluate_all(reg, {"sub__revenue": Decimal("500")})

    assert out["calc__gross"].value is None
    assert out["calc__gross"].missing == ["sub__cos"], "the blocking term must be named"


def test_a_formula_may_reference_another_formula():
    reg = build([
        _sub("sub__a"), _sub("sub__b"),
        LineItemDef(key="mid__total", type="intermediate",
                    terms=[Term(ref="sub__a"), Term(ref="sub__b")]),
        LineItemDef(key="out__net", type="calculated",
                    terms=[Term(ref="mid__total"), Term(ref="sub__b", sign=-1)]),
    ])
    out = evaluate_all(reg, {"sub__a": Decimal("70"), "sub__b": Decimal("30")})

    assert out["mid__total"].value == Decimal("100")
    assert out["out__net"].value == Decimal("70"), "dependencies must be evaluated first"


# --- validation ---------------------------------------------------------------------------------

def test_a_formula_that_depends_on_itself_is_reported_with_the_whole_cycle():
    reg = build([
        LineItemDef(key="a", type="calculated", terms=[Term(ref="b")]),
        LineItemDef(key="b", type="calculated", terms=[Term(ref="a")]),
    ])

    assert not reg.ok
    msg = " ".join(p.message for p in reg.problems)
    assert "depends on itself" in msg and "->" in msg, msg


def test_a_term_naming_a_line_that_does_not_exist_is_an_error():
    reg = build([LineItemDef(key="a", type="calculated", terms=[Term(ref="ghost")])])

    assert not reg.ok
    assert "ghost" in reg.problems[0].message


def test_a_parent_that_is_not_a_line_item_is_an_error():
    reg = build([_sub("sub__x", parent="not_a_line")])

    assert not reg.ok
    assert "not a line item" in reg.problems[0].message


def test_a_duplicate_key_is_an_error():
    reg = build([_sub("sub__x"), _sub("sub__x")])

    assert not reg.ok
    assert "twice" in reg.problems[0].message


def test_an_intermediate_can_never_reach_the_output():
    """Enforced on the model rather than trusted to whoever edits the file."""
    d = LineItemDef(key="m", type="intermediate", in_output=True, terms=[Term(ref="x")])

    assert d.in_output is False


def test_a_computed_line_with_no_terms_is_refused_at_load():
    with pytest.raises(ValidationError, match="needs at least one term"):
        LineItemDef(key="m", type="calculated")


def test_a_term_needs_exactly_one_of_a_reference_or_a_number():
    with pytest.raises(ValidationError, match="exactly one"):
        Term(ref="a", const=5)
    with pytest.raises(ValidationError, match="exactly one"):
        Term()


def test_from_section_is_refused_without_the_balance_sheet_in_scope():
    """There is no section banner in a note or a cash-flow statement to resolve it against, so a
    definition that asks for one is a configuration error rather than a silent 'none'.

    Since the merge, the gate satisfies this as well as the search order does — a definition
    confined to `section_scope: ['bs_ca']` can read a side even while it searches the notes — so
    the refusal now needs BOTH to be absent. See `test_line_item_gate.py` for that half.
    """
    with pytest.raises(ValidationError, match="from_section"):
        LineItemDef(key="x", type="extracted", side="from_section", scopes=["notes"])


# --- side resolution ----------------------------------------------------------------------------

@pytest.mark.parametrize("section,expected", [
    ("current_assets", "asset"), ("non_current_assets", "asset"),
    ("current_liabilities", "liability"), ("non_current_liabilities", "liability"),
    ("equity", "equity"),
])
def test_the_side_comes_from_the_section_a_caption_sits_under(section, expected):
    d = LineItemDef(key="x", type="extracted", side="from_section",
                    scopes=["balance_sheet", "notes"])

    assert d.resolved_side(section) == expected


def test_a_statement_total_has_no_section_so_the_side_stays_unanswered():
    """Total assets sits at the END of the asset side, under no banner. Reporting "none" lets the
    caller say the side is unanswered instead of guessing one."""
    d = LineItemDef(key="x", type="extracted", side="from_section",
                    scopes=["balance_sheet"])

    assert d.resolved_side(None) == "none"
    assert d.resolved_side("unresolved_section") == "none"


def test_an_explicit_side_is_not_overridden_by_the_section():
    d = LineItemDef(key="x", type="extracted", side="asset", scopes=["balance_sheet"])

    assert d.resolved_side("current_liabilities") == "asset"


# --- rollup check -------------------------------------------------------------------------------

def test_sub_line_items_that_do_not_come_to_their_parent_are_a_warning_not_a_refusal():
    """A filing routinely discloses only some of the parts, so a disagreement is a reviewer's
    business rather than a reason to publish nothing."""
    reg = build([_sub("sub__a"), _sub("sub__b"),
                 LineItemDef(key=OPER, type="derived", implemented_by="rulebook_derivation")])
    for d in reg.by_key.values():
        if d.key.startswith("sub__"):
            d.parent = OPER
    problems = check_rollups(reg, {OPER: Decimal("500"), "sub__a": Decimal("100"),
                                   "sub__b": Decimal("150")})

    assert [p.severity for p in problems] == ["warning"]
    assert "come to 250" in problems[0].message


def test_a_parent_whose_children_do_sum_raises_nothing():
    reg = build([_sub("sub__a"), _sub("sub__b"),
                 LineItemDef(key=OPER, type="derived", implemented_by="rulebook_derivation")])
    for d in reg.by_key.values():
        if d.key.startswith("sub__"):
            d.parent = OPER

    assert check_rollups(reg, {OPER: Decimal("250"), "sub__a": Decimal("100"),
                               "sub__b": Decimal("150")}) == []


# ── patterns must be readable back ───────────────────────────────────────────────────────────────
# The seed for these definitions was generated by splitting a shipped 34-alternative regex on
# "|", which tore `^\s*at\s+(?:1|31)` into two fragments that compile nowhere. A pattern that
# does not compile is worse than a missing one: the exclusion stops excluding, the wrong note
# rows are summed, and the figure looks ordinary. These pin the refusal at the point of writing.

def test_a_note_pattern_that_does_not_compile_is_refused():
    with pytest.raises(ValidationError) as exc:
        LineItemDef(key="sub__x", type="extracted", parent=OPER,
                    note_source=NoteSource(row_caption_none=[r"^\s*at\s+(?:1"]))

    assert "does not compile" in str(exc.value)
    assert "row_caption_none[0]" in str(exc.value)


def test_the_intact_alternation_is_accepted():
    d = LineItemDef(key="sub__x", type="extracted", parent=OPER,
                    note_source=NoteSource(row_caption_none=[r"^\s*at\s+(?:1|31)"]))

    assert d.note_source.row_caption_none == [r"^\s*at\s+(?:1|31)"]


def test_an_uncompilable_exclusion_on_the_line_itself_is_refused():
    with pytest.raises(ValidationError, match="does not compile"):
        LineItemDef(key="x", type="extracted", exclude_hints=["total("])


def test_every_pattern_in_the_shipped_seed_compiles():
    """The generator's own output, checked as data rather than trusted as generated."""
    seed = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
    raw = json.loads(seed.read_text(encoding="utf-8"))
    defs = load_line_item_set(raw).items                  # the validator above does the work

    patterns = [v for d in defs if d.note_source
                for f in ("note_title_any", "row_caption_any", "row_caption_none")
                for v in getattr(d.note_source, f)]
    assert len(patterns) > 700, "the seed lost its caption patterns"
    assert build(defs).ok


# ── what GET /line-items tells the editor ────────────────────────────────────────────────────────
# Two facts, both of which only became facts when the Line Items screen stopped being read-only.
#
#   * `vocab` — every value a control may offer. A select offering a token the publish gate refuses
#     is worse than no control at all: the author authors, saves, and is told no by a validator two
#     layers down, with the work already typed in. So the closed sets are DERIVED from the same
#     aliases the loader validates with, and these tests hold them to that rather than to a list
#     retyped beside them — a retyped list is how `SearchScope` acquired a second spelling of
#     `income_statement` that nothing else in the backend spells.
#   * `declared_fields` — which values the item itself DECLARED. All 475 shipped items take their
#     gate from `section_defaults`, and once folded an inherited value is indistinguishable from a
#     declared one; the editor's inherited badge is this field and nothing else.
#
# NEITHER TEST MAY ASSUME WHICH SET IS IN FORCE. `config_select` answers "the latest one stored",
# the suite publishes configurations of its own, and this file is not the first to run — so an
# assertion about the 475-item shipped set read through `GET /line-items` passes alone and fails in
# the suite (measured: a 183-item probe set was in force). What is asserted against the endpoint is
# therefore true of ANY set it can serve, and the claims that are specifically about the SHIPPED set
# are read off the seed, the way `test_every_pattern_in_the_shipped_seed_compiles` above does.


def test_every_closed_set_the_editor_offers_is_the_schema_s_own(client):
    """The served vocabulary is the schema's, not a curated copy of it.

    Held to `get_args` of the alias each list describes, so a token added to `SearchScope` or a
    bucket added to `BUCKET_KEYS` reaches the screen the moment it exists and cannot be half-added:
    a list the screen offers and the alias does not declare is a refusal on save, and a token the
    alias declares and the list omits is a value an author cannot reach at all.
    """
    r = client.get("/api/v1/line-items")
    assert r.status_code == 200, r.text
    body = r.json()
    vocab = body["vocab"]

    # `statements` and `sign_conventions` are ENUMS, served by `.value`; the rest are `Literal`
    # aliases. `analyst_buckets` is neither — it is `services.buckets.BUCKET_KEYS`, which is the
    # list `_validate_against_target_template` refuses an unknown bucket against, and before that
    # refusal existed `bucket_of` filed those rows in Others with nothing saying why.
    expected = {
        "statements": [s.value for s in StatementType],
        "sign_conventions": [c.value for c in SignConvention],
        "scopes": list(get_args(SearchScope)),
        "sides": list(get_args(Side)),
        "rollups": list(get_args(Rollup)),
        "namespaces": list(get_args(Namespace)),
        "types": list(get_args(LineItemType)),
        "value_scopes": list(get_args(ValueScope)),
        "extraction_modes": list(get_args(ExtractionMode)),
        "alias_matching": list(get_args(AliasMatching)),
        "temporalities": list(get_args(Temporality)),
        "units_of_account": list(get_args(UnitOfAccount)),
        "sign_expectations": list(get_args(SignExpectation)),
        "note_uses": list(get_args(NoteUse)),
        "caption_normalizations": list(
            get_args(NoteSource.model_fields["caption_normalization"].annotation)),
        "term_roles": list(get_args(Term.model_fields["role"].annotation)),
        "analyst_buckets": list(BUCKET_KEYS),
    }
    missing = sorted(set(expected) - set(vocab))
    assert not missing, f"the editor has no options to render these controls from: {missing}"
    assert {k: vocab[k] for k in expected} == expected

    # NONE OF THEM SERVED EMPTY. A select rendered from an empty list offers the author nothing, and
    # on this screen that is indistinguishable from a field nobody wired up — which is the failure
    # this whole block exists to make loud rather than visual.
    empty = sorted(k for k, v in expected.items() if not v)
    assert not empty, f"the editor would render these controls with no options: {empty}"

    # THE FOUR LISTS THAT COME FROM THE SET rather than from an alias, checked against the set
    # instead of for non-emptiness: `section_scope`, `residual_policy.framework` and `.population`
    # are FREE strings on the model, so what is served is a datalist of what this set already
    # declares — and a set declaring no residual policy has no framework to suggest. Inventing one
    # would be the curated list this block refuses.
    for suggestions in ("inherits_options", "section_scope_tokens",
                        "residual_frameworks", "residual_populations"):
        options = vocab[suggestions]
        assert options == sorted(set(options)), f"{suggestions} must be sorted and unique"

    # `inherits`, though, is NOT free: it names a `section_defaults` entry of THIS set, and a
    # dangling one is not a load error but a silent no-op that leaves the item with no gate at all.
    # So the options can only come from the set the same response is serving.
    assert vocab["inherits_options"] == sorted(body["set"]["section_defaults"])

    # The banner tokens an author may re-enter must at least include the ones the set is already
    # gated on, or a section an item is confined to today cannot be typed back in tomorrow.
    declared_scope = {s for sec in body["set"]["section_defaults"].values()
                      for s in (sec.get("section_scope") or [])}
    assert declared_scope <= set(vocab["section_scope_tokens"])

    # The legacy 3-token UI spelling the Template screen still sends. It is kept accepted, so every
    # token it offers must name a real `SignConvention` — it can express three of the six, and the
    # screen offers the full six under a different label.
    from app.api.routes.line_items import _SIGN_FROM_UI

    assert set(vocab["legacy_sign_conventions"]) == set(_SIGN_FROM_UI)
    assert set(_SIGN_FROM_UI.values()) <= set(vocab["sign_conventions"])

    # WHAT IS NOT AUTHORABLE, AND WHY — served rather than left absent, because "silently missing
    # from the form" and "read-only for a reason" look identical to a reader and only one of them
    # is a decision. Each one must genuinely be refused by the edit body, `key` excepted: it is
    # that body's own SELECTOR, not a value it writes.
    from app.api.routes.line_items import ItemEdit

    assert all(reason.strip() for reason in vocab["not_editable"].values()), \
        "a field held read-only without a reason is just a missing field"
    still_accepted = sorted((set(vocab["not_editable"]) - {"key"}) & set(ItemEdit.model_fields))
    assert not still_accepted, \
        f"the screen calls these read-only while the edit body writes them: {still_accepted}"


def test_declared_fields_is_what_the_item_declared_not_what_it_inherited(client):
    """`declared_fields` is the key set of the STORED dict, never of the resolved payload.

    The served item is RESOLVED — all 475 shipped items fold a gate in from `section_defaults` —
    and serving the unfolded shape instead would show a screen full of items that appear to
    constrain nothing. But once folded, an inherited value cannot be told apart from a declared one
    or from a model default, and that distinction is the one an editor has to draw: SAVING a field
    the item never declared turns the section's value into the item's own and silently detaches it
    from the section, so the next edit to that section no longer reaches it.

    Pinned against `GET /versions/{id}`, which serves the definition exactly as stored, so the two
    endpoints cannot disagree about what an item said. Whichever version happens to be in force,
    because the rule is a property of the endpoint and not of one configuration.
    """
    body = client.get("/api/v1/line-items").json()
    stored = client.get(f"/api/v1/line-items/versions/{body['version']['id']}")
    assert stored.status_code == 200, stored.text
    definition = stored.json()["definition"]
    raw = {d["key"]: d for d in definition["items"]}

    # Storage is flat and the payload NESTS sub-line items under their parent, so the whole tree has
    # to be walked before it can be compared with what was stored.
    served: dict[str, dict] = {}

    def walk(items):
        for item in items:
            served[item["key"]] = item
            walk(item["children"])

    walk(body["items"])
    assert set(served) == set(raw), "every stored item must be reachable in the nested payload"
    for key, item in served.items():
        assert item["declared_fields"] == sorted(raw[key]), \
            f"{key}: declared_fields must be the stored dict's keys, not the resolved payload's"
        assert "key" in item["declared_fields"], f"{key}: a stored item declares its own key"
        if item["inherits"] and "statement" not in item["declared_fields"]:
            # An INHERITED gate, on the payload the screen actually renders: present as a value,
            # absent as a declaration. Whether any item in the set in force is in this position is
            # a property of that set, so the shipped-set count is pinned below.
            assert item["statement"] is not None, \
                f"{key}: the payload must carry the gate the item is matched under"

    # THE FACT THE INHERITED BADGE DEPENDS ON, on the SHIPPED set — read off the seed, because
    # `config_select` serves the LATEST stored version and by the time this file runs the suite has
    # published configurations of its own. Resolved exactly the way `GET /line-items` resolves it
    # (`load_line_item_set(..., resolve=True)`), so the two cannot answer differently.
    seed = json.loads((pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
                       / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    declared = {d["key"]: sorted(d) for d in seed["items"]}
    resolved = {d.key: d for d in load_line_item_set(seed, resolve=True).items}

    # Thirteen of the 475 say nothing about `statement` themselves and are gated all the same,
    # through `inherits`. Each must resolve to a gate that IS present while `declared_fields` stays
    # silent about it — the resolved payload alone cannot say which of the two happened, and an
    # editor that saved the resolved value back would turn the section's gate into the item's own.
    inherited = [k for k, d in resolved.items() if d.inherits and "statement" not in declared[k]]
    assert len(inherited) == 14, \
        f"the shipped set inherits 13 gates rather than declaring them; found {len(inherited)}"
    for key in inherited:
        item = resolved[key]
        assert item.statement is not None, f"{key}: resolution must fold the section's gate in"
        assert item.statement == seed["section_defaults"][item.inherits]["statement"], \
            f"{key}: the resolved gate must be the section's, not a model default"

    # AND IT IS NOT THE PAYLOAD'S KEYS FILTERED BY WHAT THE ITEM DECLARED, which is the near-miss
    # that would satisfy everything above: a declared key need not survive the load at all. 394 of
    # the 475 shipped items carry `note_use_rationale`, `LineItemDef` declares no such field (the
    # model spells the surviving one `notes_as_source_rationale`, recovered from the section layer),
    # and the loader drops it. An editor intersecting `declared_fields` with the served shape would
    # therefore stop reporting a key the author can read in the file they published.
    dropped = {f for fields in declared.values() for f in fields} - set(LineItemDef.model_fields)
    assert dropped == {"note_use_rationale"}, \
        f"a stored key the model no longer drops, or a new one it does: {sorted(dropped)}"
    assert sum(1 for f in declared.values() if "note_use_rationale" in f) == 394
