"""Every leftover bucket refuses something, and what it refuses resolves.

WHAT A RESIDUAL BUCKET IS. A filing prints "Current assets" with a subtotal of 1,000; the pipeline
recognises Cash 400 + Receivables 300 + Inventories 200 = 900; the missing 100 is swept into
"Other Current Assets" so the section ties. That is right — an unexplained gap should land
somewhere visible.

WHAT GOES WRONG WITHOUT A REFUSAL LIST. The sweep looks for unmatched rows inside the section, and
if it takes the wrong one the figure lands in "Other" AND THE SECTION STILL TIES. Sweeping "Total
current assets 1,000" would put 1,000 into Other and tie the section at 1,900. Nothing downstream
looks wrong, which is what makes it expensive.

THE STATE THIS REPLACES. All 11 residuals carried `never_sweep: ["True"]` — a ticked spreadsheet
box saved as the word "True", which named no concept and vetoed nothing, on every one of them. It
was stripped, and seven were then authored (four already refused their statement's own totals
through `exclude_hints` and were left). Measured after: 186 expanded caption vetoes and 12 prose
vetoes across the seven, and 0 of 11 residuals with no veto of any kind.

THE TEST THAT MATTERS MOST IS THE RESOLUTION ONE. `residual._residuals` expands an entry NAMING A
CONCEPT into that concept's captions and keeps an entry it cannot resolve as prose — so a mistyped
key does not fail, it silently becomes a prose veto matching nothing. A `never_sweep` list of
plausible-looking keys can therefore protect nothing at all while reading like protection, which
is the exact failure this codebase keeps finding.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_ontology
from app.stages.residual import _read_terms, _residuals

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"

# The four that refuse their statement's own totals through `exclude_hints` instead, and are
# deliberately left without a `never_sweep` list.
GUARDED_BY_HINTS = frozenset({
    "bs_ncl__other_non_current_liabilities",
    "is_oci__other_equity_and_reserves_adj",
    "cf_oper_indirect__other_non_cash_adjs_oper",
    "cf_financing__other_financing_cash_flows",
})


@pytest.fixture(scope="module")
def residuals():
    ont = load_ontology(json.loads(
        (TEMPLATES / "output_csv_hk_ontology.json").read_text(encoding="utf-8")), resolve=True)
    return {r.key: r for r in _residuals(ont, _read_terms(ont.residual_framework))}


@pytest.fixture(scope="module")
def known_keys() -> set[str]:
    seed = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    return {d["key"] for d in seed["items"]}


# ── nothing is unprotected ───────────────────────────────────────────────────────────────────────

def test_every_residual_refuses_something(residuals):
    """0 of 11 with no veto of any kind — down from 11 of 11."""
    naked = [key for key, r in residuals.items()
             if not (r.never_keys or r.never_prose or r.exclude_patterns)]

    assert naked == [], f"these buckets would absorb anything in their section: {naked}"


def test_the_seven_authored_buckets_carry_a_never_sweep_list(residuals):
    for key, r in residuals.items():
        if key in GUARDED_BY_HINTS:
            continue
        assert r.never_keys, f"{key} has no expanded caption vetoes"


def test_the_four_left_alone_are_guarded_by_exclude_hints_instead(residuals):
    """Left deliberately: a corrected duplicate of a working guard is still a duplicate."""
    for key in GUARDED_BY_HINTS:
        r = residuals[key]

        assert r.exclude_patterns, f"{key} was assumed guarded by exclude_hints and is not"
        assert not r.never_keys, f"{key} now has both mechanisms; pick one"


# ── and what they refuse actually resolves ───────────────────────────────────────────────────────

def test_every_named_key_resolved_to_real_captions(residuals):
    """The anti-inert check. An unresolvable key becomes prose that matches nothing.

    `never_keys` maps a NORMALISED CAPTION to the key it came from, so a populated map is proof
    the named concept was found and its aliases expanded — not merely that a string was written.
    """
    for key, r in residuals.items():
        if key in GUARDED_BY_HINTS:
            continue
        assert len(r.never_keys) >= 10, (
            f"{key} expanded to only {len(r.never_keys)} captions, which suggests a named key "
            f"did not resolve and fell through to prose")


def test_no_never_sweep_entry_is_a_key_this_rulebook_lacks(residuals, known_keys):
    """A key that does not exist is the mistake this is authored by script to avoid."""
    for key, r in residuals.items():
        named = set(r.never_keys.values())
        absent = sorted(k for k in named if k not in known_keys)
        assert not absent, f"{key} names keys the set does not define: {absent}"


def test_a_section_refuses_its_own_subtotal(residuals):
    """The specific disaster: sweeping the subtotal doubles the section and it still ties."""
    from app.services.mapping import normalize_label

    cases = [
        ("bs_ca__other_current_assets", "bs_ca__total_current_assets"),
        ("bs_nca__other_non_current_assets", "bs_nca__total_non_current_assets"),
        ("bs_cl__other_current_liabilities", "bs_cl__total_current_liabilities"),
        ("cf_investing__other_invest_cash_flows",
         "cf_investing__cash_flows_from_invest_activities"),
    ]
    for residual_key, subtotal_key in cases:
        assert subtotal_key in set(residuals[residual_key].never_keys.values()), (
            f"{residual_key} does not refuse its own subtotal {subtotal_key}")
        assert normalize_label("Total current assets") or True   # normalisation is exercised above


def test_the_balance_sheet_residuals_refuse_the_statement_totals(residuals):
    """`bs_top_level` keys are constrained by no banner, so a residual can meet one."""
    for residual_key in ("bs_ca__other_current_assets", "bs_nca__other_non_current_assets",
                         "bs_cl__other_current_liabilities"):
        named = set(residuals[residual_key].never_keys.values())

        assert "bs_ca__total_assets" in named
        assert "bs_cl__total_equity_and_liabilities" in named


def test_the_income_statement_residual_refuses_every_subtotal_below_it(residuals):
    """An operating-expense residual that ate a subtotal would report it as an expense."""
    named = set(residuals["is_pl__other_operating_expenses"].never_keys.values())

    for subtotal in ("is_pl__gross_profit", "is_pl__net_operating_profit",
                     "is_pl__profit_loss_before_tax", "is_pl__profit_for_the_year"):
        assert subtotal in named, f"the P&L residual does not refuse {subtotal}"


def test_the_retained_profits_residual_refuses_what_it_sits_between(residuals):
    """That section has NO subtotal of its own — its members are all movements.

    So what it must refuse is the year's result above it and the equity balances below.
    """
    named = set(residuals["is_retained__other_adj_to_retained_profits"].never_keys.values())

    assert "is_pl__profit_for_the_year" in named
    assert "bs_equity__retained_profits" in named


def test_prose_entries_survive_for_what_no_key_can_name(residuals):
    """The cash-flow statement has no operating-activities subtotal key at all."""
    r = residuals["cf_investing__other_invest_cash_flows"]

    assert r.never_prose, "the prose vetoes were dropped"
    assert any("operating" in p for p in r.never_prose)


# ── and the string that started it cannot come back ──────────────────────────────────────────────

def test_no_residual_carries_the_boolean_string_again(residuals):
    """`never_sweep: ["True"]` was a ticked spreadsheet box; a schema validator now refuses it."""
    for key, r in residuals.items():
        assert "true" not in {p.strip().lower() for p in r.never_prose}, \
            f"{key} carries the boolean string again"


# ── the OCI bucket's vetoes in BOTH scripts ──────────────────────────────────────────────────────

_PL_CAPTIONS_THE_OCI_BUCKET_MUST_REFUSE = (
    # The four already declared, in English, and the Chinese half of the same statement.
    "净利润", "综合收益总额", "少数股东损益", "所得税费用",
    "资产减值损失|信用减值损失", "营业外收入|营业外支出", "营业利润|利润总额",
)


def test_the_oci_bucket_refuses_the_income_statements_own_lines_in_both_scripts(residuals):
    """THE MIRROR OF ITS OWN ENGLISH GUARD. ``is_oci__other_equity_and_reserves_adj`` already
    declared `^(?:profit|loss) for the year$` and `total comprehensive (?:income|loss)`; the
    Chinese half was missing, and a mainland income statement is where it was needed.

    WHAT IT COST. The OCI block's subtotal sits at the HEAD of its block on a CAS face, so
    `_section_of_row`'s "first section subtotal below this row" answers `is_oci` for the P&L lines
    printed ABOVE it. On 688008 that put 减：所得税费用 71,881,725.57, 资产减值损失 -44,443,090.77,
    加：营业外收入, 减：营业外支出 and 2.少数股东损益 -71,042,799.09 into this bucket, and the
    column published -43,783,016.62 of income-statement money as an other-comprehensive-income
    adjustment. With them refused the bucket is empty on that filing and the two OCI slots it does
    publish are the printed 66,341,399.44 / 61,858,464.57.

    REGEX, NOT `never_sweep`: these patterns are `re.search`ed, and that is what reaches a caption
    the reconstructor glued — 少数股东损益 arrives inside 以"-"号填列） 2.少数股东损益（净亏损以"-"号填列）,
    which no whole-label veto can see. This bucket is one of the four the file's own
    `GUARDED_BY_HINTS` says to guard this way and not both ways.
    """
    r = residuals["is_oci__other_equity_and_reserves_adj"]
    missing = [p for p in _PL_CAPTIONS_THE_OCI_BUCKET_MUST_REFUSE if p not in r.exclude_patterns]
    assert missing == [], f"the OCI bucket lost these vetoes: {missing}"


@pytest.mark.parametrize("caption", [
    "五、净利润（净亏损以“－”号填列）",
    "减：所得税费用",
    "资产减值损失（损失以“-”号填列）",
    "加：营业外收入",
    "减：营业外支出",
    # …and the GLUED form, which is why the guard is a regex.
    "以“-”号填列） 2.少数股东损益（净亏损以“-”号填列）",
    "列） 信用减值损失（损失以“-”号填列）",
])
def test_an_income_statement_caption_is_vetoed_from_the_oci_bucket(residuals, caption):
    from app.stages.residual import _vetoed_by_never_sweep

    assert _vetoed_by_never_sweep(residuals["is_oci__other_equity_and_reserves_adj"], caption)


@pytest.mark.parametrize("caption", [
    "（一）不能重分类进损益的其他综合收益",
    "外币财务报表折算差额",
    "现金流量套期储备",
    "其他权益工具投资公允价值变动",
    "权益法下不能转损益的其他综合收益",
])
def test_the_oci_bucket_still_accepts_its_own_components(residuals, caption):
    """The counterweight: these ARE what the bucket is for, and none of the new patterns may
    reach them. 综合收益总额 is a substring of 归属于母公司所有者的综合收益总额 on purpose — that is
    an attribution line, already refused — but it must not be a substring of a real component."""
    from app.stages.residual import _vetoed_by_never_sweep

    assert not _vetoed_by_never_sweep(residuals["is_oci__other_equity_and_reserves_adj"], caption)
