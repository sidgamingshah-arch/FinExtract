"""The half the merge brought over from the ontology: WHERE a line item may be claimed from.

WHY THIS FILE IS SEPARATE from `test_line_items.py`. That file tests assembly — cascades, term
roles, formula arithmetic — which is what the configurator always did. This file tests the gate,
which is what the ontology did and what decides whether a figure lands on the right line at all.

The numbers these tests defend, measured on the shipped rulebook rather than assumed:

  1,969  distinct normalised caption strings across 462 concepts
    420  of them claimed by MORE THAN ONE concept
     96  claimed across DIFFERENT statements — `intangible assets` is claimed by two
          balance-sheet concepts and two P&L concepts, and nothing in the caption separates them
      0  of the 420 that the merged model resolves worse than the ontology does
          (`scripts/project_ontology.py` is the standing check)

Strip the gate and those 420 resolve at the exact tier, confidence 1.0, `needs_review=False`:
wrong figures on the face, not blanks, with every subtotal still tying. That is the failure mode
these tests exist to make impossible to reintroduce quietly.
"""
from __future__ import annotations

import json
import pathlib
import typing
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.models.enums import StatementType
from app.schemas.line_items import (CascadeRung, LineItemDef, SearchScope, Term,
                                    UnknownInheritsError, load_line_item_set)
from app.services.line_items import build, check_rollups, evaluate

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
OPER = "is_pl__deprec_and_impairment_oper_exp"


def _seed():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")))


def _seed_raw() -> dict:
    """The seed as JSON, for the blocks the loaded set does not expose as objects —
    `section_defaults`, whose `face_only` flag says which sections are read off the FACE."""
    return json.loads(SEED.read_text(encoding="utf-8"))


# ── permissive when silent ───────────────────────────────────────────────────────────────────────
# Every gate field is optional and absence means "nothing was said", never "nothing is allowed".
# A definition that declares no statement must behave exactly as it did before the gate existed,
# which is what makes the gate adoptable one line at a time.

def test_a_silent_definition_is_claimable_anywhere():
    d = LineItemDef(key="x")

    assert d.claimable_on("profit_and_loss")
    assert d.claimable_on("balance_sheet")
    assert d.claimable_under("bs_ca")
    assert d.claimable_under(None)


def test_an_empty_section_scope_is_unconstrained_not_closed():
    assert LineItemDef(key="x").claimable_under("anything_at_all")


def test_an_unnamed_statement_is_not_a_reason_to_refuse():
    """Refusing what the classifier could not name would DELETE rows rather than mis-file them."""
    assert LineItemDef(key="x", statement="balance_sheet").claimable_on(None)


def test_a_statement_total_sits_under_no_banner_and_is_still_claimable():
    assert LineItemDef(key="x", section_scope=["bs_ca"]).claimable_under(None)


# ── the refusals that decide the 420 collisions ──────────────────────────────────────────────────

def test_a_statement_declaration_refuses_the_other_statements():
    d = LineItemDef(key="x", statement="balance_sheet")

    assert d.claimable_on("balance_sheet")
    assert not d.claimable_on("profit_and_loss")


def test_the_statement_gate_accepts_the_enum_and_the_token_alike():
    d = LineItemDef(key="x", statement="profit_and_loss")

    assert d.claimable_on(StatementType.PROFIT_AND_LOSS)
    assert d.claimable_on("profit_and_loss")
    assert not d.claimable_on(StatementType.BALANCE_SHEET)


def test_a_section_scope_refuses_a_caption_under_another_banner():
    d = LineItemDef(key="x", section_scope=["bs_ca"])

    assert d.claimable_under("bs_ca")
    assert d.claimable_under("  BS_CA  ")      # the banner arrives however the page printed it
    assert not d.claimable_under("bs_cl")


def test_the_cross_statement_collision_the_rulebook_actually_has():
    """`intangible assets` is claimed on both the balance sheet and the P&L.

    Four concepts in the shipped rulebook carry this alias — two balance-sheet, two P&L — and the
    caption is identical in every one. Only the statement separates them.
    """
    bs = LineItemDef(key="bs_nca__other_intangible_assets", statement="balance_sheet",
                     section_scope=["bs_nca"], aliases=["Intangible assets"])
    pl = LineItemDef(key="is_pl__impairment_intgbl", statement="profit_and_loss",
                     section_scope=["is_pl"], aliases=["Intangible assets"])

    assert bs.claimable_on("balance_sheet") and not pl.claimable_on("balance_sheet")
    assert pl.claimable_on("profit_and_loss") and not bs.claimable_on("profit_and_loss")


# ── the vocabulary that must not drift ───────────────────────────────────────────────────────────

def test_the_search_scope_vocabulary_is_the_statement_vocabulary():
    """The first version of this schema said `income_statement`.

    That token appeared NOWHERE else in the backend — the classifier, `StatementType` and every
    template say `profit_and_loss`. Read as a gate, that one spelling refuses every P&L concept on
    every page. Pinning it here means the two vocabularies cannot drift apart again in silence.
    """
    scopes = set(typing.get_args(SearchScope))
    statements = {s.value for s in StatementType}

    assert "income_statement" not in scopes
    assert "profit_and_loss" in scopes
    # `front_matter` is the only scope that is not a statement; everything else must spell its
    # statement exactly as StatementType does.
    assert scopes - statements == {"front_matter"}


def test_from_section_needs_somewhere_to_read_a_section_from():
    with pytest.raises(ValidationError, match="from_section"):
        LineItemDef(key="x", side="from_section", scopes=["notes"])


def test_from_section_is_satisfied_by_the_gate_as_well_as_the_search_order():
    """A definition gated to the balance sheet can read a side even if it searches the notes."""
    d = LineItemDef(key="x", side="from_section", scopes=["notes"], section_scope=["bs_ca"])

    assert d.resolved_side("bs_ca") == "asset"
    assert d.resolved_side("bs_cl") == "liability"
    assert d.resolved_side(None) == "none"       # unanswered, not guessed


# ── the section layer: authored once, not 462 times ──────────────────────────────────────────────

def test_a_section_default_reaches_the_items_that_inherit_it():
    st = load_line_item_set({
        "section_defaults": {"bs_ca": {"statement": "balance_sheet",
                                       "section_scope": ["bs_ca"], "match_priority": 80}},
        "items": [{"key": "a", "inherits": "bs_ca"}]})

    assert st.items[0].statement == StatementType.BALANCE_SHEET
    assert st.items[0].section_scope == ["bs_ca"]
    assert st.items[0].match_priority == 80


def test_a_declaration_on_the_item_beats_the_section():
    st = load_line_item_set({
        "section_defaults": {"bs_ca": {"statement": "balance_sheet", "match_priority": 80}},
        "items": [{"key": "a", "inherits": "bs_ca", "match_priority": 99}]})

    assert st.items[0].match_priority == 99


def test_items_sharing_a_section_do_not_share_its_lists():
    st = load_line_item_set({
        "section_defaults": {"s": {"section_scope": ["bs_ca"]}},
        "items": [{"key": "a", "inherits": "s"}, {"key": "b", "inherits": "s"}]})
    st.items[0].section_scope.append("mutated")

    assert st.items[1].section_scope == ["bs_ca"]


def test_a_dangling_inherits_is_refused_and_names_every_offender():
    """A silent no-op here is the failure the fold exists to prevent one level up.

    The item would validate, load, and carry none of its section's gate — no `section_scope`, so
    nothing could ever place it.
    """
    with pytest.raises(UnknownInheritsError) as exc:
        load_line_item_set({"section_defaults": {"real": {}},
                            "items": [{"key": "a", "inherits": "ghost"},
                                      {"key": "b", "inherits": "phantom"}]})

    assert "a inherits 'ghost'" in str(exc.value)
    assert "b inherits 'phantom'" in str(exc.value)
    assert "real" in str(exc.value)          # says what WAS declared, so the fix is obvious


def test_resolve_false_shows_what_an_item_declares_itself():
    """An editor needs the raw declaration; a matcher needs the folded one."""
    doc = {"section_defaults": {"s": {"statement": "balance_sheet"}},
           "items": [{"key": "a", "inherits": "s"}]}

    assert load_line_item_set(doc, resolve=True).items[0].statement is not None
    assert load_line_item_set(doc, resolve=False).items[0].statement is None


def test_a_bare_array_still_loads():
    """The shape the seed had before the envelope existed."""
    st = load_line_item_set([{"key": "a"}, {"key": "b"}])

    assert [d.key for d in st.items] == ["a", "b"]


# ── the shipped seed ─────────────────────────────────────────────────────────────────────────────

def test_the_shipped_seed_carries_its_template_binding():
    """A bare JSON array had nowhere to say this, which is why the key-gate could not be copied."""
    st = _seed()

    assert st.target_template_key
    assert st.section_defaults, "the gate must be authored once per section, not per item"


def test_every_reported_line_resolves_a_gate_and_parts_deliberately_do_not():
    """A REPORTED line is pinned to a statement. A PART OF one is deliberately not, and that
    exemption is the whole content of this test.

    The rule was `d.statement is None` for every definition, and for a reported line the reason
    given is right: pinned to nothing, it is claimable on any statement. But a part cannot be
    pinned that way, and two attempts at pinning it both broke a real reading:

      * `notes` (as shipped, from `section_defaults`) — made a part unofferable for a FACE row,
        which is what started this: a part can perfectly well take its value off the face.
      * the PARENT's sections plus `notes` — bounded, and wrong for a subtler reason. It encodes
        where the WHOLE is reported onto where the PART is printed, and those differ by design.
        Depreciation is the case that proves it: the figure is printed in the BALANCE-SHEET note on
        fixed assets, whose banner resolves to `non_current_assets`, while its whole
        (`is_pl__deprec_and_impairment_oper_exp`) is a profit-and-loss line. Measured under that
        scoping, `sub__ppe_depreciation` was offered under `income_and_expenses` and `notes` and
        REFUSED under `non_current_assets` — the PP&E-note reading was lost entirely.

    Any single statement or section is wrong for the same reason, so a part declares neither.

    WHAT REPLACES THE GATE, because something must: no part declares an alias — zero, measured,
    with zero collisions against a template concept's aliases — so the exact, rule and fuzzy tiers
    have no evidence that could bind a caption to one. A part is reachable by the MODEL, whose
    answers carry confidence, a reason and the review machinery, and by the `note_source` walk,
    which reads declared patterns rather than the gate. That is asserted here, because it is the
    only thing standing where the statement gate used to.
    """
    st = _seed()

    reported = [d for d in st.items if not d.parent]
    unglazed = [d.key for d in reported if d.statement is None]
    assert not unglazed, f"these would be claimable on any statement: {unglazed}"

    parts = [d for d in st.items if d.parent]
    # the parts are 64: the nine related-party feeders became the three Find items the spec asks for, and `sub__pbt_cos_depreciation`, `sub__rp_note_entrusted_loans` and six revenue sub-items were retired.
    assert len(parts) >= 64
    # A NOTE-READ PART DECLARES NO FACE STATEMENT AND NO FACE SECTION. Two admissions, both
    # from this docstring's own measurement rather than from convenience:
    #
    #   * `notes` is allowed. The measured loss was under `non_current_assets`; the same probe
    #     records the part as OFFERED under `notes`, which is where a note-read part belongs.
    #   * A part SCOPED TO A FACE SECTION is allowed to name its statement, because it is read off
    #     the face and not from a note at all — `sub__face_principal_revenue` against
    #     `sub__revenue_note_principal_revenue` is that pair. The configuration says which is
    #     which: `section_defaults[<scope>].face_only`.
    # THE DISCRIMINATOR IS WHETHER THE PART READS A NOTE AT ALL, because that is what the measured
    # loss was about: a NOTE-READ part scoped to a FACE section could not reach the note that
    # prints it. A part with no `note_source` reads the face by construction —
    # `sub__face_principal_revenue` against `sub__revenue_note_principal_revenue` is that pair —
    # and for it a face statement and a face section are the mechanism, not a hazard.
    face_sections = {k for k, v in (_seed_raw().get("section_defaults") or {}).items()
                     if isinstance(v, dict) and v.get("face_only")}
    # EIGHT PARTS DECLARE A SECTION, AND THEY MEAN SOMETHING DIFFERENT BY IT. The section on a
    # part is CONTEXT — it restricts the line to the notes aligned to that section — and not a
    # claim about where the part's figure is published. The six securities parts scoped to `bs_ca`
    # are saying "consider the current-asset notes", which is why they are not inert: the FVTPL
    # note total reads 15,759,270 on the 2024 annual report.
    #
    # WHAT THIS DOCSTRING MEASURED IS STILL TRUE and is a different mechanism: scoping
    # `sub__ppe_depreciation` left it REFUSED under `non_current_assets` in the face MATCHER, which
    # is the gate this test is about. A part whose whole is reported on one statement while its
    # figure is printed in another statement's note must not be pinned for matching — depreciation
    # is exactly that, and it declares no section.
    #
    # So the two coexist: a section narrows the NOTES a part will read, and the matcher gate stays
    # unpinned for any part whose note lives outside its whole's statement. The eight are named so
    # a NINTH is read deliberately rather than absorbed.
    SECTION_SCOPED_PARTS = {
        "sub__fa_cp_fvtpl_note_total", "sub__fa_cp_fvtoci_note_total",
        "sub__fa_cp_afs_htm_note_total", "sub__fa_cp_included_derivatives",
        "sub__fa_cp_investment_and_money_market_securities_note_total",
        "sub__fa_cp_noncurrent_split_of_note_total",
        "sub__face_principal_revenue", "sub__revenue_note_principal_revenue",
    }
    pinned = [d.key for d in parts
              if d.note_source is not None
              and d.key not in SECTION_SCOPED_PARTS
              and (any(s in face_sections for s in (d.section_scope or ()))
                   or (d.statement is not None
                       and str(getattr(d.statement, "value", d.statement)) != "notes"))]
    assert not pinned, (
        "a part pinned to a statement or section loses the note that prints it — see the docstring:"
        f" {pinned[:6]}")
    # THE PROTECTION THAT REPLACES THE GATE. If a part ever declares an alias, a deterministic tier
    # could bind a caption to it with nothing left to refuse the claim.
    #
    # TWO FACE-READING PARTS NOW DO, AND THE EXEMPTION IS NAMED RATHER THAN WIDENED. Both read a row
    # off a FACE statement rather than out of a note — their `note_source.note_title_any` matches
    # "consolidated balance sheet" and "consolidated income statement", not a note heading — so they
    # need aliases for the deterministic route to find them at all, which is the whole reason a face
    # part exists as a part.
    #
    # THE RISK IS REAL AND IS NOT CLOSED BY THIS LIST. A part declares no statement and no section
    # (asserted just above, for the reason the docstring gives), so an alias on one is bindable from
    # ANY statement, and `mapping.match` now returns on the first exact alias hit — so "Revenue" on
    # a cash-flow statement can reach `sub__face_principal_revenue` with nothing left to refuse it.
    # Pinning exactly these two to their own statement is coherent with the docstring's argument —
    # it objects to pinning a part printed in a NOTE whose whole sits on another statement, and a
    # face part is definitionally on one named statement — but it changes where a part may bind on
    # every run, so it belongs with the no-figures-move check rather than with this test. Recorded
    # as backlog item 13.
    #
    # WHAT THIS STILL GUARANTEES, and why it is kept: a THIRD part acquiring aliases fails here.
    _FACE_PARTS_WITH_ALIASES = {"sub__rp_bs_face_receivables", "sub__face_principal_revenue"}
    aliased = [d.key for d in parts
               if (d.aliases or d.aliases_i18n) and d.key not in _FACE_PARTS_WITH_ALIASES]
    assert not aliased, (
        f"these parts declare aliases, so a deterministic tier could bind a caption to one with no "
        f"statement gate to stop it: {aliased}")


def test_the_note_level_parts_are_off_template():
    """13 sub-line items are parts of a line, not lines, so the template must not demand them."""
    st = _seed()
    subs = [d for d in st.items if d.key.startswith("sub__")]

    # 14 since the profit-before-tax note's COST-OF-SALES depreciation callout was added on
    # review — a third reader of that note, distinguished from the other two by the qualifier
    # in its caption. See `is_pl__deprec_and_impairment_cos`'s COS_P2 rung.
    # 78 — plus `sub__fa_cp_intermediate_residual`, which is off-template for the same reason
    # every other part is: the template does not print it.
    # the parts are 64: the nine related-party feeders became the three Find items the spec asks for, and `sub__pbt_cos_depreciation`, `sub__rp_note_entrusted_loans` and six revenue sub-items were retired.
    assert len(subs) == 64
    assert all(d.namespace == "internal" for d in subs)
    assert all(d.namespace == "template" for d in st.items if d.in_output)


# ── a rung below zero is refused, and the next is tried ──────────────────────────────────────────

def test_a_rung_computing_below_zero_is_passed_over():
    """A rung computing below zero is passed over and the next is tried.

    This began as parity with the deleted `services.deprec_impairment`, whose `_first_valid` skipped
    a negative candidate and flagged NEGATIVE_RESIDUAL; the first port of it had no such refusal, so
    a rung computing -50 would have won in the config. The service is gone and the figure must now
    come from configuration, so this is no longer a parity check against anything: the cascade
    evaluator's own refusal is the definition, and it is pinned here.
    """
    d = LineItemDef(key="out", type="derived", cascade=[
        CascadeRung(id="P1", terms=[Term(ref="a"), Term(ref="b", sign=-1)]),
        CascadeRung(id="P2", terms=[Term(ref="c")]),
    ])
    got = evaluate(d, {"a": Decimal("10"), "b": Decimal("60"), "c": Decimal("42")})

    assert got.value == Decimal("42")
    assert got.rung_used == "P2"
    assert got.refused_rungs == ["P1 computed -50"]


def test_a_definition_that_may_legitimately_be_negative_opts_out():
    d = LineItemDef(key="out", type="derived", cascade=[
        CascadeRung(id="P1", refuse_negative=False,
                    terms=[Term(ref="a"), Term(ref="b", sign=-1)])])
    got = evaluate(d, {"a": Decimal("10"), "b": Decimal("60")})

    assert got.value == Decimal("-50")
    assert got.rung_used == "P1"


def test_every_refused_rung_is_reported_when_none_survives():
    """A silent skip looks identical to a rung whose inputs were simply absent."""
    d = LineItemDef(key="out", type="derived", cascade=[
        CascadeRung(id="P1", terms=[Term(ref="a", sign=-1)])])
    got = evaluate(d, {"a": Decimal("5")})

    assert got.value is None
    assert got.refused_rungs == ["P1 computed -5"]


# ── alternatives are never summed ────────────────────────────────────────────────────────────────

def _sub(key: str) -> LineItemDef:
    return LineItemDef(key=key, type="extracted", in_output=False, parent=OPER)


def test_children_that_are_alternatives_are_not_summed():
    """The twelve parts of the Oper Exp line restate ONE figure; summing them means nothing.

    The rule came across from the deleted `services.deprec_impairment`, whose module docstring said
    they must never be summed "since they routinely restate the same figure"; `rollup="alternatives"`
    in the shipped configuration is now the only place that fact is written down, which is why it is
    pinned here. `parent` was carrying display nesting and arithmetic rollup at once, so this check
    reported the parent as disagreeing with a total that had no meaning.
    """
    reg = build([_sub("sub__a"), _sub("sub__b"),
                 LineItemDef(key=OPER, type="derived", implemented_by="rulebook_derivation",
                             rollup="alternatives")])

    assert check_rollups(reg, {OPER: Decimal("100"), "sub__a": Decimal("100"),
                               "sub__b": Decimal("100")}) == []


def test_children_that_really_are_addends_are_still_checked():
    reg = build([_sub("sub__a"), _sub("sub__b"),
                 LineItemDef(key=OPER, type="derived", implemented_by="x", rollup="sum")])
    problems = check_rollups(reg, {OPER: Decimal("500"), "sub__a": Decimal("100"),
                                   "sub__b": Decimal("150")})

    assert [p.severity for p in problems] == ["warning"]
    assert "come to 250" in problems[0].message


def test_the_shipped_depreciation_lines_declare_their_children_alternatives():
    by_key = {d.key: d for d in _seed().items}

    assert by_key[OPER].rollup == "alternatives"
    assert by_key["is_pl__deprec_and_impairment_cos"].rollup == "alternatives"


def test_the_cost_of_sales_line_kept_both_of_its_rungs():
    """Both rungs of the cost-of-sales cascade are shipped; the first port of them dropped one.

    COS_P1 and COS_P2 came across from the deleted `services.deprec_impairment` (~208 enumerated
    note titles, row captions and formula variants), which is no longer there to be compared with —
    this configuration is now the whole of the line's arithmetic. A filing that discloses total
    depreciation and the operating-expense share but no cost-of-sales note is blanked if the second
    rung goes missing again, and nothing else would catch it.
    """
    cos = {d.key: d for d in _seed().items}["is_pl__deprec_and_impairment_cos"]

    # TWO RUNGS. A third once read the profit-before-tax note's own cost-of-sales callout
    # through `sub__pbt_cos_depreciation`; the part and the rung were retired deliberately, and the
    # subtraction that was COS_P3 is now COS_P2. The precedence that remains is the one that
    # matters: a figure the filing STATES in its cost-of-sales note beats one inferred by
    # difference.
    assert [r.id for r in cos.cascade] == ["COS_P1", "COS_P2"]


# ── containment: the discriminator the first pass omitted ────────────────────────────────────────

def test_a_gross_parent_names_the_children_it_contains():
    """31 caption collisions had no discriminator until these came across.

    They were all one shape — `bs_ca__trade_and_other_receivables` against
    `bs_ca__trade_receivables_gross`: same statement, same banner, same priority, one containing
    the other. `mapping.py` and `map_ontology.py` both read these, so leaving them out was a
    correctness loss, not a documentation one: with nothing to separate a gross parent from its
    child, the caption resolves to whichever was declared first and the two are then loaded
    additively, double-counting a figure the filing printed once.
    """
    d = LineItemDef(key="bs_ca__trade_and_other_receivables", is_gross_parent=True,
                    children_if_decomposed=["bs_ca__trade_receivables_gross"])

    assert d.is_gross_parent
    assert "bs_ca__trade_receivables_gross" in d.children_if_decomposed


def test_a_look_alike_pair_is_answered_by_the_banner_not_by_prose():
    """REPLACES `test_the_prose_that_answers_a_look_alike_pair_is_carried`.

    That test asserted `section_disambiguation` was carried, on the claim it "resolves 30 of the 420
    collisions". The field is gone: 395 of 539 lines declared it and the values hold THIRTEEN
    distinct strings, every one generated as `"Bind only to {statement} / {section}."` — which
    distinguishes nothing, because it restates the gate the line already declares.

    What actually separates a look-alike pair is the BANNER. Measured on this set, 170 contested
    captions are same-statement and different sub-heading: `"Financial asset at FVTPL"` is claimed
    by both `bs_ca` and `bs_nca`, `"Shareholders / holding companies"` by `bs_nca` (due from) and
    `bs_ncl` (due to). That discriminator is `section_scope`, it is authored per line, and it is
    still on the form.
    """
    current = LineItemDef(key="x", statement="balance_sheet", section_scope=["bs_ca"])
    non_current = LineItemDef(key="y", statement="balance_sheet", section_scope=["bs_nca"])

    # The field is off the model. Pydantic IGNORES an unknown key rather than refusing it (see
    # `loader.unknown_keys` for why), so a stored value does not break a load — it lands nowhere,
    # which is what `scripts/project_ontology.py` reports as an unhomed declaration.
    assert not hasattr(current, "section_disambiguation")
    assert "section_disambiguation" not in LineItemDef.model_fields
    assert current.statement == non_current.statement
    assert current.section_scope != non_current.section_scope, (
        "the banner is the only thing separating this pair")
