"""HKEX PROFIT-OR-LOSS FACE ROWS REACH THE LINE THEIR DEFINITION NAMES, so the computed bottom lines
reach print.

The HK template computes Profit before tax, Profit before extraordinary items and Profit for the year
from their components (`rollups.figures_as_shown`): a printed bottom line is evidence, the arithmetic
is the figure. So every face row has to land on the line it is, once, and a subtotal must never be
swept into a component. Measured at ce19bb2 (answer-free), the computed bottom lines were off on every
HKEX filing in the corpus, for five reasons each of these tests pins:

  * DEPRECIATION COUNTED TWICE. Deprec & Impairment(Oper Exp) and (COS) are read from the notes and
    added into Net Operating Profit / Total Cost of Sales — while the face's administrative expenses,
    cost of sales and other expense lines already hold that depreciation. BYD's PBT was short by
    exactly its 5,465,598 of depreciation, Hon Kwok's by its 29,941. Every rung is `carved_from_face`.
  * CAPTIONS THE ALIASES MISSED, swept into Other Operating Expenses or left unbound: "Research and
    development expenses", "Government grants and subsidies", "PROFIT BEFORE INCOME TAX EXPENSE",
    "PROFIT FROM OPERATIONS", "Loss from operations", "Income tax credit/(expenses)", "Changes in fair
    value of investment properties", "Tax and surcharges", "Holders of perpetual securities" ...
  * A SUBTOTAL SWEPT AS A COMPONENT: PetroChina's "TOTAL OPERATING EXPENSES" went into Other Operating
    Expenses beside the rows it totals.
  * TWO INCOME STATEMENTS ON ONE BASIS: PetroChina's CAS statements were read as face beside its IFRS
    ones (`statement_sets.unread_set_pages`).
  * THE REVENUE BLOCK: Far East Horizon prints its revenue as four rows and no total; three of them
    were counted again beside the note's total revenue.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"
OTHERS = "is_pl__other_operating_expenses"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped))


def _key(matcher, caption, section=None):
    res = matcher.match(caption, statement="profit_and_loss", section=section)
    return res.canonical_key if res is not None else None


# ── the captions, as the corpus prints them ────────────────────────────────────────────────────

@pytest.mark.parametrize("caption,key", [
    # BYD Electronic (hkex_0285), both pre-labelled Other Operating Expenses in its requests.
    ("Research and development expenses 研究及開發費用", "is_pl__research_and_development"),
    ("Government grants and subsidies 政府補助及補貼", "is_pl__grants_and_subsidies"),
    # PetroChina (hkex_0857), IFRS statement of comprehensive income.
    ("PROFIT BEFORE INCOME TAX EXPENSE", "is_pl__profit_loss_before_tax"),
    ("PROFIT FROM OPERATIONS", "is_pl__net_operating_profit"),
    ("TOTAL NET FINANCE COSTS", "is_pl__net_interest_income_expense"),
    ("Taxes other than income taxes", "is_pl__other_non_income_related_taxes"),
    ("Selling, general and administrative expenses", "is_pl__general_and_admin_expenses"),
    ("Employee compensation costs", "is_pl__personnel_and_benefit_expenses"),
    ("Exchange gain", "is_pl__rlzd_forex_translation_g_l"),
    ("Exchange loss", "is_pl__rlzd_forex_translation_g_l"),
    ("SHARE OF PROFIT OF ASSOCIATES AND JOINT VENTURES", "is_pl__income_loss_from_related_party"),
    # Far East Horizon (hkex_3360).
    ("Tax and surcharges", "is_pl__other_non_income_related_taxes"),
    ("Other income and gains", "is_pl__other_operating_income"),
    ("Selling and distribution costs", "is_pl__selling_and_marketing_expenses"),
    # 佳明 Grand Ming (hkex_kaming).
    ("Income tax credit/(expenses) 所得稅收益／ （支出）", "is_pl__total_income_tax"),
    ("Other income and gains/(losses), net 其他收入及收益╱ （虧損） 淨額",
     "is_pl__other_operating_income"),
    ("Changes in fair value of investment properties 投資物業之公平值變動",
     "is_pl__fmv_adj_on_investment_properties"),
    ("Loss from operations 經營虧損", "is_pl__net_operating_profit"),
    # China SCE (hkex_1966).
    ("Changes in fair value of investment properties, net 投資物業公允值變動淨額",
     "is_pl__fmv_adj_on_investment_properties"),
    ("Write down to net realisable value of completed properties held for sale and properties "
     "under development 持作出售己落成物業及發展中物業減值至可變現淨值", "is_pl__asset_impairment_loss"),
])
def test_each_printed_row_binds_the_line_its_definition_names(matcher, caption, key):
    assert _key(matcher, caption) == key


# ── the configuration ──────────────────────────────────────────────────────────────────────────

def _items():
    return {i["key"]: i for i in json.loads(SEED.read_text(encoding="utf-8"))["items"]}


def _concepts():
    return {m["canonical_key"]: m
            for m in json.loads(ONTOLOGY.read_text(encoding="utf-8"))["mappings"]}


@pytest.mark.parametrize("key", ["is_pl__deprec_and_impairment_oper_exp",
                                 "is_pl__deprec_and_impairment_cos"])
def test_depreciation_read_from_the_notes_is_carved_from_the_face(key):
    """The face's expense lines already hold the depreciation a note states: added again, it took
    BYD's profit before tax from 4,740,416 to -725,182."""
    rungs = _items()[key]["cascade"]
    assert rungs and all(r.get("carved_from_face") is True for r in rungs)


def test_the_revenue_note_total_is_read_once():
    """Lai Sun's revenue note reprints "Total turnover" 4,995,768 under each disaggregation; summed,
    Sales(Revenues) read 38,271,495."""
    assert _items()["sub__revenue_note_principal_revenue"].get("rollup") == "alternatives"


@pytest.mark.parametrize("key", ["is_pl__research_and_development", "is_pl__grants_and_subsidies",
                                 "is_pl__net_operating_profit", "is_pl__profit_loss_before_tax",
                                 "is_pl__total_income_tax", "is_pl__other_operating_income",
                                 "is_pl__fmv_adj_on_investment_properties", OTHERS])
def test_the_ontology_carries_the_same_aliases_and_vetoes(key):
    item, concept = _items()[key], _concepts()[key]
    assert item.get("aliases") == concept.get("aliases")
    assert item.get("aliases_i18n") == concept.get("aliases_i18n")
    assert item.get("exclude_hints") == concept.get("exclude_hints")


# ── the stages ─────────────────────────────────────────────────────────────────────────────────

def _row(caption, current, prior=None, page=0, section=None, role=LineRole.LINE):
    row = LineItem(source_label=caption, role=role, section_hint=section)
    for period, value in (("current", current), ("prior", prior)):
        if value is not None:
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                         value=Decimal(value), value_raw=Decimal(value),
                                         provenance=Provenance(page_index=page)))
    return row


def _ctx(shipped):
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    return ctx


def _doc(rows, pages=None):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = pages or [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss")]
    doc.line_items = rows
    return doc


def test_a_subtotal_caption_is_never_swept_into_other_operating_expenses(shipped):
    """PetroChina's IFRS face: "TOTAL OPERATING EXPENSES" (2,704,027) is the total of the rows above
    it, and swept beside them it counted them twice."""
    rows = [_row("REVENUE", "2937981"),
            _row("Purchases, services and other", "-1938093"),
            _row("Exploration expenses, including exploratory dry holes", "-20862"),
            _row("TOTAL OPERATING EXPENSES", "-1958955"),
            _row("PROFIT FROM OPERATIONS", "979026"),
            _row("PROFIT BEFORE INCOME TAX EXPENSE", "979026"),
            _row("INCOME TAX EXPENSE", "-100"),
            _row("PROFIT FOR THE YEAR", "978926")]
    doc, ctx = _doc(rows), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    assert rows[1].canonical_key == OTHERS and rows[2].canonical_key == OTHERS
    assert rows[3].canonical_key != OTHERS, rows[3].confidence.flags
    assert rows[4].canonical_key == "is_pl__net_operating_profit"
    assert rows[5].canonical_key == "is_pl__profit_loss_before_tax"


def test_the_residuals_vetoes_read_each_half_of_a_bilingual_caption(shipped):
    """佳明 opens its statement of comprehensive income with "Loss for the year 年內虧損". The OCI
    residual refuses "^(?:profit|loss) for the year$" — anchored, so the bilingual caption slipped
    past it and the loss for the year was swept into the OCI's Others."""
    rows = [_row("Revenue 收益", "868375"),
            _row("Loss for the year 本年虧損", "-349498"),
            _row("Loss for the year 年內虧損", "-349498", page=1,
                 section="OTHER COMPREHENSIVE INCOME"),
            _row("Exchange differences on translation of foreign operations 換算海外業務之匯兌差額",
                 "11806", page=1, section="OTHER COMPREHENSIVE INCOME"),
            _row("Total comprehensive income for the year 年內全面收益總額", "-344759", page=1,
                 section="OTHER COMPREHENSIVE INCOME")]
    pages = [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss"),
             PageSource(index=1, kind=PageKind.FACE, statement="profit_and_loss")]
    doc, ctx = _doc(rows, pages), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    assert rows[2].canonical_key != "is_oci__other_equity_and_reserves_adj", rows[2].confidence.flags


def test_the_owners_share_is_after_the_perpetual_securities_holders_share(matcher):
    """Far East Horizon's profit for the year is split three ways; the owners' share (3,862,461)
    is what is left after the non-controlling interests AND the holders of perpetual securities
    (32,188). Left unbound, the computed owners' share came out 3,894,649."""
    for banner in ("Attributable to:", "PROFIT FOR THE YEAR ATTRIBUTABLE TO:"):
        assert _key(matcher, "Holders of perpetual securities", banner) == \
            "is_pl__minority_interests_pl"
    assert _key(matcher, "Holders of perpetual securities",
                "TOTAL COMPREHENSIVE INCOME FOR THE YEAR ATTRIBUTABLE TO:") != \
        "is_pl__minority_interests_pl"


def test_the_perpetual_holders_share_is_deducted_once(shipped):
    """Far East Horizon prints both splits under a bare "Attributable to:". The perpetual holders'
    share of profit is a deduction like the NCI's (flipped, as the NCI's is); the second pair's row,
    a share of the total comprehensive income, is not a share of the profit and stays off it."""
    from app.stages.normalize import NormalizeStage

    rows = [_row("PROFIT FOR THE YEAR", "4519237"),
            _row("Ordinary shareholders of the parent", "3862461", section="Attributable to:"),
            _row("Holders of perpetual securities", "32188", section="Attributable to:"),
            _row("Non-controlling interests", "624588", section="Attributable to:"),
            _row("TOTAL COMPREHENSIVE INCOME FOR THE YEAR", "5160565"),
            _row("Ordinary shareholders of the parent", "4503868", section="Attributable to:"),
            _row("Holders of perpetual securities", "32188", section="Attributable to:"),
            _row("Non-controlling interests", "624509", section="Attributable to:")]
    doc, ctx = _doc(rows), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    NormalizeStage().run(doc, ctx)
    minority = "is_pl__minority_interests_pl"
    assert rows[2].canonical_key == minority and rows[3].canonical_key == minority
    assert rows[6].canonical_key != minority and rows[7].canonical_key != minority
    assert all(next(iter(r.values.values())).value < 0 for r in (rows[2], rows[3]))


def test_only_one_income_statement_per_basis_feeds_the_face(shipped):
    """PetroChina prints CAS and IFRS statements. The CAS owners' row (164,676) reached Profit for
    the year beside the IFRS one (164,684), and its net profit and taxation were swept."""
    from app.services.statement_sets import SET_NOT_READ_FLAG, unread_set_pages

    cas = [_row("Operating income", "2937981", page=0), _row("Less: Taxation", "-57755", page=0),
           _row("Net profit", "183747", page=0), _row("Shareholders of the Company", "164676", page=0,
                                                     section="Attributable to:")]
    ifrs = [_row("REVENUE", "2937981", page=1), _row("INCOME TAX EXPENSE", "-57753", page=1),
            _row("PROFIT FOR THE YEAR", "183755", page=1),
            _row("Owners of the Company", "164684", page=1,
                 section="PROFIT FOR THE YEAR ATTRIBUTABLE TO:")]
    pages = [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss",
                        evidence={"framework": "CAS"}),
             PageSource(index=1, kind=PageKind.FACE, statement="profit_and_loss",
                        evidence={"framework": "IFRS"})]
    doc, ctx = _doc(cas + ifrs, pages), _ctx(shipped)
    assert unread_set_pages(doc) == {0: "CAS"}
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    assert all(r.canonical_key is None for r in cas), [(r.source_label, r.canonical_key) for r in cas]
    assert all(any(f.startswith(SET_NOT_READ_FLAG) for f in r.confidence.flags) for r in cas)
    assert ifrs[3].canonical_key == "is_pl__profit_for_the_year"
    assert [r for r in doc.line_items if r.canonical_key == "is_pl__profit_for_the_year"] == [ifrs[3]]


def test_a_filing_with_one_statement_set_reads_every_page(shipped):
    from app.services.statement_sets import unread_set_pages

    doc = _doc([_row("REVENUE", "1")], [PageSource(index=0, kind=PageKind.FACE,
                                                    statement="profit_and_loss",
                                                    evidence={"framework": "IFRS"})])
    assert unread_set_pages(doc) == {}


def test_the_revenue_block_is_the_face_revenue(shipped):
    """Far East Horizon prints its revenue as four rows above the cost of sales and no total; the
    note's "Total revenue" 37,749,156 is their sum. Bound to Interest Income and swept into Others,
    three of them were counted twice."""
    rows = [_row("Interest income", "21182108"),
            _row("Revenue from operating leases", "6350698"),
            _row("Revenue from contracts with customers", "10353429"),
            _row("Tax and surcharges", "-137079"),
            _row("Cost of sales", "-20734304"),
            _row("Other income and gains", "1893476"),
            _row("Administrative expenses", "-4738808")]
    doc, ctx = _doc(rows), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    assert [r.canonical_key for r in rows[:4]] == ["sub__face_principal_revenue"] * 4
    assert rows[4].canonical_key == "is_pl__cost_of_sales"
    assert rows[5].canonical_key == "is_pl__other_operating_income"


def test_one_revenue_row_above_the_cost_of_sales_moves_nothing(shipped):
    rows = [_row("Notes HK$’", "0"), _row("REVENUE", "1086515"), _row("Cost of sales", "-505170"),
            _row("Gross profit", "581345", role=LineRole.SUBTOTAL), _row("Other income", "43930")]
    doc, ctx = _doc(rows), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    assert rows[0].canonical_key is None
    assert rows[4].canonical_key == "is_pl__other_operating_income"


def test_a_revenue_row_with_no_cost_of_sales_below_it_takes_nothing(shipped):
    """PetroChina's IFRS face prints no cost of sales: the rows after its revenue are costs."""
    rows = [_row("REVENUE", "2937981"), _row("Purchases, services and other", "-1938093"),
            _row("Employee compensation costs", "-179257")]
    doc, ctx = _doc(rows), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    assert rows[1].canonical_key is None
    assert rows[2].canonical_key == "is_pl__personnel_and_benefit_expenses"


def test_the_comprehensive_income_copy_binds_where_the_income_statement_prints_none(shipped):
    """BYD prints its profit for the year only as the heading over the owners' row; the copy the
    statement of comprehensive income opens with — under a stale attribution banner — is the only
    row printing it."""
    rows = [_row("PROFIT BEFORE TAX 除稅前溢利", "4740416"),
            _row("Income tax expense 所得稅開支", "-474778"),
            _row("Attributable to owners of the parent 母公司擁有人應佔", "4265638",
                 section="PROFIT FOR THE YEAR 年度溢利"),
            _row("PROFIT FOR THE YEAR 年度溢利", "4265638", page=1,
                 section="Attributable to owners of the parent 母公司擁有人應佔")]
    pages = [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss"),
             PageSource(index=1, kind=PageKind.FACE, statement="profit_and_loss")]
    doc, ctx = _doc(rows, pages), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    assert rows[3].canonical_key == "is_pl__profit_loss_bef_extraord_items", rows[3].confidence.flags


def test_the_copy_stays_unbound_where_the_income_statement_prints_its_own(shipped):
    rows = [_row("PROFIT FOR THE YEAR", "25927"),
            _row("PROFIT FOR THE YEAR", "25927", page=1,
                 section="TOTAL COMPREHENSIVE INCOME FOR THE YEAR ATTRIBUTABLE TO:")]
    pages = [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss"),
             PageSource(index=1, kind=PageKind.FACE, statement="profit_and_loss")]
    doc, ctx = _doc(rows, pages), _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    assert rows[0].canonical_key == "is_pl__profit_loss_bef_extraord_items"
    assert rows[1].canonical_key != "is_pl__profit_loss_bef_extraord_items"


def test_a_face_part_printed_on_another_statement_does_not_stop_the_carve(shipped):
    """Deprec & Impairment(Oper Exp) falls back to the cash-flow add-back; every filing prints that
    row, and counting it as "the face prints this line" left the note figure uncarved."""
    from app.stages.note_sourced import _face_prints_line

    defs = {i.key: i for i in shipped.items}
    line = defs["is_pl__deprec_and_impairment_oper_exp"]
    add_back = _row("Depreciation", "5465598")
    add_back.canonical_key = "sub__cfo_depreciation"
    by_key = {"sub__cfo_depreciation": add_back, "cf_oper_indirect__depreciation": add_back}
    assert not _face_prints_line(line, by_key, defs, "consolidated", "current")
    # A related-party line whose own balance-sheet face part prints a figure is still not carved.
    rp = defs["bs_cl__due_to_related_parties_cp"]
    face = _row("Due to related parties", "2588416")
    face.canonical_key = "sub__rp_face_due_to_cp"
    assert _face_prints_line(rp, {"sub__rp_face_due_to_cp": face}, defs, "consolidated", "current")


# ── the arithmetic: a swept residual is a signed sum on a filing that signs its costs ─────────────

def _spread_row(key, value, method="exact", period="prior"):
    return {"canonical_key": key, "mapping_method": method,
            "values": [{"basis": "consolidated", "period_label": period, "value": str(value),
                        "confidence": {"flags": []}}]}


def _pbt(rows, period="prior"):
    from app.services import rollups

    template = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
    return rollups.figures_as_shown(template, rows, "consolidated", period)[
        "is_pl__profit_loss_before_tax"]


def test_swept_gains_that_outweigh_swept_costs_are_added_not_spent():
    """Hon Kwok 2023/24, prior year: Others is 98,973 + 26,979 - 34,893 = 91,059 at the printed
    signs, on a face whose costs are printed in parentheses. Spent as a magnitude it entered the
    profit before tax as -91,059; the face prints 299,195."""
    rows = [_spread_row("is_pl__sales_revenues", 1049421, "cascade"),
            _spread_row("is_pl__cost_of_sales", -493202),
            _spread_row("is_pl__other_operating_income", 35549),
            _spread_row("is_pl__fmv_adj_on_investment_properties", -56016),
            _spread_row("is_pl__general_and_admin_expenses", -110288),
            _spread_row(OTHERS, 98973, "residual"), _spread_row(OTHERS, -34893, "residual"),
            _spread_row(OTHERS, 26979, "residual"),
            _spread_row("is_pl__interest_expense", -216139),
            _spread_row("is_pl__interest_on_lease_liabilities", -1189)]
    assert _pbt(rows) == 299195


def test_on_a_face_that_prints_its_costs_unsigned_the_residual_is_still_a_cost():
    """A mainland face prints 销售费用 / 管理费用 unsigned: every cost, the swept ones included, is
    spent as a magnitude, as before."""
    rows = [_spread_row("is_pl__sales_revenues", 1000, "cascade"),
            _spread_row("is_pl__cost_of_sales", 600),
            _spread_row("is_pl__general_and_admin_expenses", 100),
            _spread_row(OTHERS, 50, "residual")]
    assert _pbt(rows) == 250
