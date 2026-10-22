"""THE DETERMINISTIC ROUTE BINDS THE ROW THE DEFINITION NAMES.

The model route reads a line's definition; the deterministic route reads its aliases. Four HK lines
had aliases that contradicted their own definitions, so the two routes put different rows on one
line:

  * Profit for the year is the OWNERS' share (rollup: Bef Extraord Items + extraordinary items +
    Minority Interests(PL)), but its aliases 'Profit for the year' / 净利润 bound the GROUP total.
    The group total is Profit(Loss) Bef Extraord Items; the owners' rows ('Owners of the Company',
    归属于母公司股东的净利润) are Profit for the year. The owners' row printed a second time, under the
    total comprehensive income, is NOT: the stage reads an attribution row's section off the total
    it splits, so the second pair cannot reach the line and be summed with the first.
  * Total Cost of Sales carried 营业总成本 (total operating costs), which its definition excludes.
  * 'Bills payables' sat on Trade financing and 'Commercial paper' on ST Other Loans Payable, while
    Notes Payable(CP)'s definition is bills payable and short-term notes or commercial paper.
  * 'Guaranteed notes' printed in current liabilities fell to the Other Current Liabilities
    residual, while Notes Payable(CP)'s definition sends them to CPLTD Other.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"

OWNERS = "is_pl__profit_for_the_year"
GROUP = "is_pl__profit_loss_bef_extraord_items"
MINORITY = "is_pl__minority_interests_pl"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped))


def _key(matcher, caption, statement, section=None):
    res = matcher.match(caption, statement=statement, section=section)
    return res.canonical_key if res is not None else None


# ── the matcher ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("caption", ["Profit for the year", "LOSS FOR THE YEAR",
                                     "五、净利润（净亏损以“－”号填列）", "四、净利润", "年度溢利"])
def test_the_group_total_is_profit_before_extraordinary_items(matcher, caption):
    assert _key(matcher, caption, "profit_and_loss") == GROUP


@pytest.mark.parametrize("caption", ["1.归属于母公司股东的净利润", "归属于母公司所有者的净利润",
                                     # 澜起科技 / 四创电子 / 海博思创: an Arabic enumerator AND the sign note
                                     "1.归属于母公司股东的净利润（净亏损以“-”号填列）"])
def test_a_cas_owners_row_is_profit_for_the_year(matcher, caption):
    assert _key(matcher, caption, "profit_and_loss", "（二）按所有权归属分类") == OWNERS


def test_the_minority_row_with_an_enumerator_and_the_sign_note_is_minority(matcher):
    assert _key(matcher, "2.少数股东损益（净亏损以“-”号填列）", "profit_and_loss",
                "（二）按所有权归属分类") == MINORITY


@pytest.mark.parametrize("caption", ["Owners of the Company", "Owners of the parent",
                                     "Ordinary shareholders of the parent"])
def test_an_owners_row_under_the_profit_split_is_profit_for_the_year(matcher, caption):
    assert _key(matcher, caption, "profit_and_loss", "Attributable to:") == OWNERS
    assert _key(matcher, caption, "profit_and_loss",
                "PROFIT FOR THE YEAR ATTRIBUTABLE TO:") == OWNERS


def test_the_split_banner_carrying_the_group_figure_is_not_the_owners_share(matcher):
    # PetroChina prints the group total on its banner row "PROFIT FOR THE YEAR ATTRIBUTABLE TO:".
    # The keyword hint 'Profit for the year' put it on the owners' line by the rule tier.
    caption = "PROFIT FOR THE YEAR ATTRIBUTABLE TO:"
    assert _key(matcher, caption, "profit_and_loss", caption) != OWNERS


def test_an_owners_row_under_the_comprehensive_income_split_is_not(matcher):
    assert _key(matcher, "Owners of the Company", "profit_and_loss",
                "TOTAL COMPREHENSIVE INCOME FOR THE YEAR ATTRIBUTABLE TO:") != OWNERS


def test_total_operating_costs_is_not_total_cost_of_sales(matcher):
    assert _key(matcher, "二、营业总成本", "profit_and_loss") != "is_pl__total_cost_of_sales"
    assert _key(matcher, "其中：营业成本", "profit_and_loss") == "is_pl__cost_of_sales"


@pytest.mark.parametrize("caption", ["Bills payables", "Bills payable", "Commercial paper", "应付票据"])
def test_bills_and_commercial_paper_are_notes_payable(matcher, caption):
    assert _key(matcher, caption, "balance_sheet", "Current liabilities") == "bs_cl__notes_payable_cp"


def test_current_guaranteed_notes_are_cpltd_other_and_non_current_ones_are_not(matcher):
    assert _key(matcher, "Guaranteed notes", "balance_sheet",
                "Current liabilities") == "bs_cl__cpltd_other"
    assert _key(matcher, "Guaranteed notes", "balance_sheet",
                "Non-current liabilities") != "bs_cl__cpltd_other"


# ── the configuration ──────────────────────────────────────────────────────────────────────────

def _items():
    return {i["key"]: i for i in json.loads(SEED.read_text(encoding="utf-8"))["items"]}


def _concepts():
    out = {}

    def walk(node):
        if isinstance(node, dict):
            if "canonical_key" in node and ("aliases" in node or "aliases_i18n" in node):
                out[node["canonical_key"]] = node
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(json.loads(ONTOLOGY.read_text(encoding="utf-8")))
    return out


KEYS = [OWNERS, GROUP, "is_pl__total_cost_of_sales", "bs_cl__notes_payable_cp",
        "bs_cl__trade_financing", "bs_cl__st_other_loans_payable", "bs_cl__cpltd_other"]


def test_the_owners_line_is_scoped_to_the_profit_split():
    # `is_pl` first: section membership (residual placement, section closing) reads the first
    # scope, and the owners' row is printed at the top of the split, not as its closing subtotal.
    want = ["is_pl", "profit_attributable_to"]
    assert _items()[OWNERS]["section_scope"] == want
    assert _concepts()[OWNERS]["section_scope"] == want


@pytest.mark.parametrize("key", KEYS)
def test_the_ontology_carries_the_same_aliases(key):
    item, concept = _items()[key], _concepts()[key]
    assert item["aliases"] == concept["aliases"]
    assert item["aliases_i18n"] == concept["aliases_i18n"]


# ── the stage: an attribution row's section is the total it splits ────────────────────────────

def _row(caption, value, section=None, page=0):
    row = LineItem(source_label=caption, role=LineRole.LINE, section_hint=section)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal(value), value_raw=Decimal(value),
                                 provenance=Provenance(page_index=page)))
    return row


def _run(shipped, rows):
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    # The template a run carries: Profit for the year is a calculated line, so the printed row
    # stays on it as validation evidence rather than being unfiled as a gross parent.
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    return [r.canonical_key for r in rows]


def test_a_banner_naming_the_profit_split_stands_after_the_comprehensive_total(shipped):
    """China SCE prints both splits after the total comprehensive loss, each under a banner that
    names its total; the nearest total above the first pair is the comprehensive one."""
    keys = _run(shipped, [
        _row("LOSS FOR THE YEAR", "-8401124"),
        _row("TOTAL COMPREHENSIVE LOSS FOR THE YEAR", "-8900799"),
        _row("Owners of the parent", "-7991050", "Profit/(loss) attributable to:"),
        _row("Non-controlling interests", "-410074", "Profit/(loss) attributable to:"),
        _row("Owners of the parent", "-8452222", "Total comprehensive loss attributable to:"),
        _row("Non-controlling interests", "-448577", "Total comprehensive loss attributable to:"),
    ])
    assert keys[2] == OWNERS and keys[3] == MINORITY, keys
    assert keys[4] != OWNERS and keys[5] != MINORITY, keys


def test_the_stage_binds_only_the_profit_split(shipped):
    """Lai Sun prints both pairs under a bare "Attributable to:"; by the banner alone both
    'Owners of the Company' rows are the profit split, and both would reach the owners' line."""
    rows = [
        _row("LOSS FOR THE YEAR", "-3237857"),
        _row("Owners of the Company", "-1675589", "Attributable to:"),
        _row("Non-controlling interests", "-1562268", "Attributable to:"),
        _row("TOTAL COMPREHENSIVE LOSS FOR THE YEAR", "-2969793"),
        _row("Owners of the Company", "-1562216", "Attributable to:"),
        _row("Non-controlling interests", "-1407577", "Attributable to:"),
    ]
    keys = _run(shipped, rows)
    assert keys[0] == GROUP
    assert keys[1] == OWNERS and keys[2] == MINORITY
    assert keys[4] != OWNERS and keys[5] != MINORITY, keys


def test_the_owners_row_does_not_pull_the_comprehensive_total_into_the_pl_residual(shipped):
    """The owners' share is printed in the attribution tail, after the other comprehensive income.
    Read as an `is_pl` subtotal there, it placed the total comprehensive loss printed above it in
    the income statement, and the residual swept it into Other Operating Expenses (China SCE:
    -8,900,799). For placement the line stands where it is printed: in the split."""
    from app.stages.residual import ResidualStage

    rows = [
        _row("Other expenses", "-10"),
        _row("LOSS FOR THE YEAR", "-8401124"),
        _row("Exchange differences on translation of foreign operations", "-499675",
             "OTHER COMPREHENSIVE INCOME"),
        _row("OTHER COMPREHENSIVE LOSS FOR THE YEAR 年內其他全面虧損", "-499675", "OTHER COMPREHENSIVE INCOME"),
        _row("TOTAL COMPREHENSIVE LOSS FOR THE YEAR", "-8900799", "OTHER COMPREHENSIVE INCOME"),
        _row("Owners of the parent", "-7991050", "Profit/(loss) attributable to:"),
        _row("Non-controlling interests", "-410074", "Profit/(loss) attributable to:"),
        _row("Holders of perpetual securities", "-1", "Profit/(loss) attributable to:"),
    ]
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    assert rows[5].canonical_key == OWNERS
    ResidualStage().run(doc, ctx)
    for r in rows[4:]:
        assert r.canonical_key != "is_pl__other_operating_expenses", (r.source_label,
                                                                       r.confidence.flags)
