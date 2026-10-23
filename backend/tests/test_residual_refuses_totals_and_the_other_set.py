"""A total is never swept as a component, and a dual-standards filing's other set feeds nothing.

PetroChina prints a CAS balance sheet and cash flow statement beside its IFRS ones. Both were read
as one consolidated face, so every balance-sheet total came out at about twice the printed figure.
`statement_sets.ONE_SET_STATEMENTS` now names the balance sheet and the cash flow statement as well
as the income statement. Once the CAS rows were out, the IFRS sheet's own subtotal captions were
swept into the residuals — 'TOTAL EQUITY AND NON-CURRENT LIABILITIES' into Other NCL, 'NET CURRENT
LIABILITIES' and 'TOTAL ASSETS LESS CURRENT LIABILITIES' into Other Reserves, 'CASH FLOWS GENERATED
FROM OPERATIONS' into Other non-cash adjustments beside the working-capital rows it totals — because
each residual refused subtotals by its own list of captions. `residual._TOTAL_CAPTION` is one rule
for every balance-sheet and cash-flow residual, in both scripts.

The sweep also refuses a statement-of-changes-in-equity matrix row, as `map_ontology` already does
(Lai Sun's standalone matrix was swept into Other Reserves).
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
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


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


def _run(rows, pages, shipped):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = pages
    doc.line_items = rows
    ctx = _ctx(shipped)
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    return doc


def _swept(row) -> bool:
    return row.confidence.method == "residual"


def _face(statement, index=0, framework=None):
    return PageSource(index=index, kind=PageKind.FACE, statement=statement,
                      evidence={"framework": framework} if framework else {})


# ── the rule, caption by caption ───────────────────────────────────────────────────────────────

TOTALS = [
    # PetroChina IFRS
    "TOTAL EQUITY AND NON-CURRENT LIABILITIES", "NET CURRENT LIABILITIES",
    "TOTAL ASSETS LESS CURRENT LIABILITIES", "CASH FLOWS GENERATED FROM OPERATIONS",
    "NET CASH FLOWS USED FOR INVESTING ACTIVITIES",
    # PetroChina CAS (English)
    "Sub-total of cash inflows", "Net (decrease)/increase in cash and cash equivalents",
    "(Decrease)/increase in cash and cash equivalents",
    "Cash and cash equivalents at the end of the period",
    # China SCE
    "Net cash flows from/(used in) investing activities 投資活動所得╱ （所用） 現金流量淨額",
    "AT END OF YEAR",
    # Grand Ming
    "Operating (loss)/profit before working capital changes 營運資金變動前的營運（虧損）╱溢利",
    "Net cash used in investing activities 投資活動所用現金淨額",
    # Hon Kwok
    "Cash and cash equivalents as stated in the statement of cash flows",
    # Lai Sun
    "NET CURRENT (LIABILITIES)/ASSETS", "Cash (used in)/generated from operations",
    # Han only, both scripts
    "流動負債淨額", "經營所得現金", "現金及現金等價物增加╱（減少）淨額", "年初之現金及現金等價物",
    "加：期初现金及现金等价物余额", "经营活动现金流入小计", "经营活动产生的现金流量净额",
    "负债和所有者权益（或股东权益）总计", "權益總額", "资产总值减流动负债",
]

COMPONENTS = [
    "Net cash received from disposal of subsidiaries and other business units",
    "Net cash paid for the acquisition of subsidiaries and other business entities",
    "处置子公司及其他营业单位收到的现金净额", "取得子公司及其他营业单位支付的现金净额",
    "Cash paid relating to other operating activities", "收到其他与经营活动有关的现金",
    "Net (increase)/decrease in inventories", "Net foreign exchange loss",
    "Write-down of inventories to net realisable value 存貨撇減至可變現淨值",
    "Impairment of trade receivables, net 應收貿易款項減值淨值",
    "Gain on disposal of subsidiaries, net 出售附屬公司的收益淨額",
    "Cash at bank and on hand", "Cash and bank balances", "Capital surplus",
    "Net assets of a disposal group classified as held for sale",
    "(Increase)/decrease in pledged deposits", "Decrease in cash and bank balances pledged",
    "汇率变动对现金及现金等价物的影响", "经营性应收项目的减少", "Share of net profits of associates",
]


@pytest.mark.parametrize("caption", TOTALS)
def test_a_total_caption_reads_as_a_total(caption):
    from app.stages.residual import _reads_as_total

    assert _reads_as_total(caption), caption


@pytest.mark.parametrize("caption", COMPONENTS)
def test_a_component_that_mentions_a_totals_words_is_not_one(caption):
    from app.stages.residual import _reads_as_total

    assert _reads_as_total(caption) is None, caption


# ── the rule, at the sweep ─────────────────────────────────────────────────────────────────────

def test_the_ifrs_balance_sheets_own_subtotals_are_never_swept(shipped):
    """PetroChina's IFRS statement of financial position (figures in RMB million, 2024). Each of
    the three subtotals is printed as a LINE; swept, Other NCL took 2,115,434 and Other Reserves
    2,115,434 + 46,473."""
    rows = [
        _row("Property, plant and equipment", "1270000", section="NON-CURRENT ASSETS"),
        _row("Total non-current assets", "2161907", section="NON-CURRENT ASSETS",
             role=LineRole.SUBTOTAL),
        _row("Inventories", "190000", section="CURRENT ASSETS"),
        _row("Total current assets", "590844", section="CURRENT ASSETS", role=LineRole.SUBTOTAL),
        _row("Short-term borrowings", "45955", section="CURRENT LIABILITIES"),
        _row("Total current liabilities", "637317", section="CURRENT LIABILITIES",
             role=LineRole.SUBTOTAL),
        _row("NET CURRENT LIABILITIES", "-46473"),
        _row("TOTAL ASSETS LESS CURRENT LIABILITIES", "2115434"),
        _row("Share capital", "183021", section="EQUITY"),
        _row("Total equity", "1709623", section="EQUITY", role=LineRole.SUBTOTAL),
        _row("Long-term borrowings", "180000", section="NON-CURRENT LIABILITIES"),
        _row("Total non-current liabilities", "405811", section="NON-CURRENT LIABILITIES",
             role=LineRole.SUBTOTAL),
        _row("TOTAL EQUITY AND NON-CURRENT LIABILITIES", "2115434",
             section="NON-CURRENT LIABILITIES"),
    ]
    _run(rows, [_face("balance_sheet")], shipped)
    for r in (rows[6], rows[7], rows[12]):
        assert not _swept(r), (r.source_label, r.canonical_key, r.confidence.flags)
        assert any(f.startswith("residual_ineligible:total caption") for f in r.confidence.flags) \
            or r.canonical_key, (r.source_label, r.confidence.flags)


def test_cash_generated_from_operations_is_not_swept_beside_its_components(shipped):
    """The IFRS cash flow statement prints its reconciliation on the face: profit, adjustments,
    working-capital movements, then CASH FLOWS GENERATED FROM OPERATIONS (461,866 / 519,430),
    which the Other non-cash adjustments bucket took as one more adjustment."""
    sec = "CASH FLOWS FROM OPERATING ACTIVITIES"
    rows = [
        _row("Profit for the year", "183755", "180563", section=sec),
        _row("Capitalised exploratory costs charged to expense", "9000", "8000", section=sec),
        _row("(Increase)/decrease in inventories", "-5000", "12000", section=sec),
        _row("CASH FLOWS GENERATED FROM OPERATIONS", "461866", "519430", section=sec),
        _row("Income taxes paid", "-55334", "-62583", section=sec),
        _row("NET CASH FLOWS FROM OPERATING ACTIVITIES", "406532", "456847", section=sec,
             role=LineRole.SUBTOTAL),
        _row("Capital expenditures", "-290000", "-270000", section="CASH FLOWS FROM INVESTING ACTIVITIES"),
        _row("NET CASH FLOWS USED FOR INVESTING ACTIVITIES", "-307347", "-255750",
             section="CASH FLOWS FROM INVESTING ACTIVITIES"),
    ]
    _run(rows, [_face("cash_flow")], shipped)
    assert not _swept(rows[3]), rows[3].confidence.flags
    assert not _swept(rows[7]), rows[7].confidence.flags
    # A component of the same section is still swept.
    assert rows[1].canonical_key == "cf_oper_indirect__other_non_cash_adjs_oper", \
        (rows[1].canonical_key, rows[1].confidence.flags)


def test_a_section_total_is_not_swept_into_the_next_sections_bucket(shipped):
    """China SCE: 'Net cash flows from/(used in) investing activities' (4,044,304 / -2,604,642) was
    printed on the page the financing activities open, and swept into Other financing cash flows;
    the cash at 'AT END OF YEAR' with it."""
    fin = "CASH FLOWS FROM FINANCING ACTIVITIES"
    rows = [
        _row("Net cash flows from/(used in) investing activities 投資活動所得╱ （所用） 現金流量淨額",
             "4044304", "-2604642", section=fin),
        _row("Capital reduction of subsidiaries 附屬公司之股本減少", "-1000", "-2000", section=fin),
        _row("Net cash flows used in financing activities 融資活動所用現金流量淨額",
             "-9000000", "-1000000", section=fin, role=LineRole.SUBTOTAL),
        _row("AT END OF YEAR", "3932025", "8156453", section=fin),
    ]
    _run(rows, [_face("cash_flow")], shipped)
    assert not _swept(rows[0]), rows[0].confidence.flags
    assert not _swept(rows[3]), rows[3].confidence.flags
    assert rows[1].canonical_key == "cf_financing__other_financing_cash_flows", \
        (rows[1].canonical_key, rows[1].confidence.flags)


def test_the_income_statement_keeps_its_own_list(shipped):
    """The rule is the balance sheet's and the cash flow's; the income statement's Others keeps
    the concept's own exclude_hints (a 'Total …' there is refused by those)."""
    from app.stages.residual import _TOTAL_CAPTION_STATEMENTS

    assert _TOTAL_CAPTION_STATEMENTS == {"balance_sheet", "cash_flow"}


# ── the matrix ─────────────────────────────────────────────────────────────────────────────────

def test_a_statement_of_changes_in_equity_matrix_row_is_not_swept(shipped):
    """Lai Sun's standalone statement of changes in equity (page index 350): each cell is filed
    under its component column, and the rows were swept into Other Reserves."""
    row = LineItem(source_label="Release of reserve upon lapse of share options",
                   section_hint="EQUITY")
    for col, (label, value) in enumerate((("Share option reserve", "-120"),
                                          ("Retained profits", "120")), start=2):
        row.set_value(ExtractedValue(basis=Basis.STANDALONE, period_label=label,
                                     value=Decimal(value), value_raw=Decimal(value),
                                     column_index=col, provenance=Provenance(page_index=0)))
    rows = [_row("Share capital", "100", section="EQUITY"), row,
            _row("Total equity", "1000", section="EQUITY", role=LineRole.SUBTOTAL)]
    _run(rows, [_face("balance_sheet")], shipped)
    assert not _swept(row), (row.canonical_key, row.confidence.flags)
    assert "residual_ineligible:matrix column row" in row.confidence.flags


# ── one statement set ──────────────────────────────────────────────────────────────────────────

def test_only_one_balance_sheet_and_cash_flow_per_basis_feed_the_face(shipped):
    """PetroChina: the CAS balance sheet and cash flow pages are the set the HK template does not
    read; their rows are neither bound nor swept."""
    from app.services.statement_sets import SET_NOT_READ_FLAG, unread_set_pages

    cas = [_row("Cash at bank and on hand", "216246", page=0, section="Current assets"),
           _row("Accounts receivable", "70000", page=0, section="Current assets"),
           _row("Total current assets", "590844", page=0, section="Current assets",
                role=LineRole.SUBTOTAL),
           _row("Cash received from sales of goods or rendering of services", "3200000", page=1),
           _row("Sub-total of cash inflows", "3242409", page=1)]
    ifrs = [_row("Cash and cash equivalents", "172477", page=2, section="CURRENT ASSETS"),
            _row("Total current assets", "590844", page=2, section="CURRENT ASSETS",
                 role=LineRole.SUBTOTAL)]
    pages = [_face("balance_sheet", 0, "CAS"), _face("cash_flow", 1, "CAS"),
             _face("balance_sheet", 2, "IFRS")]
    assert unread_set_pages(DocumentModel(filename="f.pdf", pages=pages)) == {0: "CAS", 1: "CAS"}
    _run(cas + ifrs, pages, shipped)
    for r in cas:
        assert r.canonical_key is None, (r.source_label, r.canonical_key)
        assert any(f.startswith(SET_NOT_READ_FLAG) for f in r.confidence.flags)
    assert ifrs[1].canonical_key == "bs_ca__total_current_assets"
