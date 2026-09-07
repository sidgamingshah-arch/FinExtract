"""Line-item definitions: validation, evaluation order, and the arithmetic.

The eight output lines and their sub-line items are configuration now, so the questions these
tests answer are the ones a configurator has to get right before anyone trusts it: does it refuse
a formula that cannot be computed, does it evaluate inputs before the things that need them, and
does its arithmetic behave the way the shipped cascades behave. The last one matters most — the
config is a PORT of `deprec_impairment`'s five rungs, and a port that quietly disagrees is worse
than no port at all.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.line_items import (CascadeRung, LineItemDef, NoteSource, Term,
                                    load_line_item_set)
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
    """The five rungs as the generated configuration states them."""
    r = lambda k, sign=1, role="required": Term(ref=k, sign=sign, role=role)
    return LineItemDef(
        key=OPER, type="derived", in_output=True, implemented_by="deprec_impairment",
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
                            implemented_by="deprec_impairment",
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
                 LineItemDef(key=OPER, type="derived", implemented_by="deprec_impairment")])
    for d in reg.by_key.values():
        if d.key.startswith("sub__"):
            d.parent = OPER
    problems = check_rollups(reg, {OPER: Decimal("500"), "sub__a": Decimal("100"),
                                   "sub__b": Decimal("150")})

    assert [p.severity for p in problems] == ["warning"]
    assert "come to 250" in problems[0].message


def test_a_parent_whose_children_do_sum_raises_nothing():
    reg = build([_sub("sub__a"), _sub("sub__b"),
                 LineItemDef(key=OPER, type="derived", implemented_by="deprec_impairment")])
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
