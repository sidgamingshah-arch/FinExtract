"""A STATEMENT ROW PRINTED INSIDE A SECTION IS SENT UNDER IT, ON EVERY PAGE THE SECTION RUNS ACROSS.

The reader names a row's banner from the page it reads the row on. A page that continues a section
prints no banner over its first rows, or only the statement's title again, so those rows came with
no section — or with the title, which is no section — and the request showed them under none:

* 000883 (湖北能源): 流动资产： is printed over 货币资金 and 交易性金融资产 on page 107; 应收票据 down to
  流动资产合计 on page 108 were sent under no section. A blind answer cited 预付款项 under 流动资产：, as
  the page prints it, and the citation was refused because the row had not been shown under it.
  Its standalone sheet lost 非流动资产： from 无形资产 (after the page break) through 非流动资产合计.
* Far East Horizon: the operating rows after the page break were read under CONSOLIDATED STATEMENT
  OF CASH FLOWS, which is a title, and so went under no section down to "Net cash flows from
  operating activities".
* A section's subtotal does not end it: 归属于母公司所有者权益合计 is printed above 少数股东权益 and
  所有者权益合计, and "Net cash flows from operating activities before tax and interest" above the
  interest and tax paid — the section ends at the LAST row that totals it.

Grand totals and bottom lines are still under no section, and close the one in force.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import face_context
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources


def _row(caption, current, page, banner=None, *, prior=None):
    li = LineItem(source_label=caption)
    if banner:
        li.section_hint = banner
    figures = {"current": current} if prior is None else {"current": current, "prior": prior}
    if current is None:
        figures = {"prior": prior}
    for label, value in figures.items():
        li.values[label] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label,
                                          value=Decimal(value), value_raw=Decimal(value),
                                          provenance=Provenance(page_index=page))
    return li


def _doc(statement, rows, pages, title=None):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=p, kind=PageKind.FACE, statement=statement,
                            evidence={"matched_title": title} if title else {}) for p in pages]
    doc.line_items = list(rows)
    return doc


def _sent(doc, statement):
    return [(r["caption"], r.get("under"))
            for r in face_context.face_rows(doc, {statement})[0]["rows"]]


def _hubei_balance_sheet():
    """000883's consolidated balance sheet as the reader reads it: 流动资产： on page 106 only."""
    return _doc("balance_sheet", [
        _row("货币资金", "2268835469.85", 106, "流动资产：", prior="1624897762.96"),
        _row("交易性金融资产", None, 106, "流动资产：", prior="67164039.35"),
        _row("应收票据", "863900.00", 107),
        _row("应收账款", "4995034503.73", 107, prior="4740249486.85"),
        _row("预付款项", "544985125.35", 107, prior="4096810021.82"),
        _row("其他流动资产", "285884359.19", 107, prior="478117398.74"),
        _row("流动资产合计", "9262312018.13", 107, prior="11938664001.14"),
        _row("长期应收款", "3726560.00", 107, "非流动资产：", prior="3726560.00"),
        _row("固定资产", "65182826011.07", 107, "非流动资产：", prior="57874490717.41"),
        _row("无形资产", "1", 108),
        _row("非流动资产合计", "2", 108),
        _row("资产总计", "3", 108),
        _row("短期借款", "4", 108, "流动负债："),
        _row("流动负债合计", "4", 108, "流动负债："),
        _row("负债合计", "5", 108),
        _row("股本", "6", 108, "所有者权益："),
        _row("资本公积", "7", 109),
        _row("归属于母公司所有者权益合计", "13", 109),
        _row("少数股东权益", "1", 109),
        _row("所有者权益合计", "14", 109),
        _row("负债和所有者权益总计", "19", 109),
    ], pages=(106, 107, 108, 109))


def test_a_section_runs_on_across_the_page_break_to_its_total():
    sent = dict(_sent(_hubei_balance_sheet(), "balance_sheet"))
    for caption in ("货币资金", "交易性金融资产", "应收票据", "应收账款", "预付款项", "其他流动资产",
                    "流动资产合计"):
        assert sent[caption] == "流动资产：", caption
    for caption in ("长期应收款", "固定资产", "无形资产", "非流动资产合计"):
        assert sent[caption] == "非流动资产：", caption
    assert sent["流动负债合计"] == "流动负债："


def test_grand_totals_stay_under_no_section_and_close_the_one_in_force():
    sent = dict(_sent(_hubei_balance_sheet(), "balance_sheet"))
    assert sent["资产总计"] is None
    assert sent["负债合计"] is None
    assert sent["负债和所有者权益总计"] is None


def test_a_sections_subtotal_does_not_end_it():
    sent = dict(_sent(_hubei_balance_sheet(), "balance_sheet"))
    for caption in ("股本", "资本公积", "归属于母公司所有者权益合计", "少数股东权益", "所有者权益合计"):
        assert sent[caption] == "所有者权益：", caption


def test_the_citation_the_blind_answer_made_resolves():
    """预付款项 cited under 流动资产：, as the page prints it."""
    doc = _hubei_balance_sheet()
    res, unres = resolve_sources(
        [SourceRef(statement="balance_sheet", caption="预付款项", group="流动资产：")], [],
        face_context.face_index(doc))
    assert not unres and res
    assert str(res[0]["figures"].get("current")) == "544985125.35"


def test_a_continuation_page_read_under_the_title_stays_in_the_section():
    """Far East Horizon: the operating rows after the page break, read under the title."""
    title = "CONSOLIDATED STATEMENT OF CASH FLOWS"
    op = "CASH FLOWS FROM OPERATING ACTIVITIES"
    doc = _doc("cash_flow", [
        _row("Profit before tax", "10", 70, op),
        _row("Finance costs", "1", 70, op),
        _row("Increase in contract assets", "-2", 71, title),
        _row("Net cash flows from operating activities before tax and interest", "9", 71, title),
        _row("Interest paid", "-1", 71, title),
        _row("Income tax paid", "-1", 71, title),
        _row("Net cash flows from operating activities", "7", 71, title),
        _row("Purchase of items of property, plant and equipment", "-3", 71,
             "CASH FLOWS FROM INVESTING ACTIVITIES"),
        _row("NET INCREASE IN CASH AND CASH EQUIVALENTS", "4", 71, title),
        _row("Cash and cash equivalents at beginning of year", "5", 71, title),
    ], pages=(70, 71), title=title)
    sent = dict(_sent(doc, "cash_flow"))
    for caption in ("Increase in contract assets",
                    "Net cash flows from operating activities before tax and interest",
                    "Interest paid", "Income tax paid", "Net cash flows from operating activities"):
        assert sent[caption] == op, caption
    assert sent["NET INCREASE IN CASH AND CASH EQUIVALENTS"] is None
    assert sent["Cash and cash equivalents at beginning of year"] is None


def test_rows_after_a_sections_total_on_the_next_page_are_not_in_it():
    """A section closed by its total is not reopened by the rows on the page after it."""
    doc = _doc("balance_sheet", [
        _row("Inventories", "1", 10, "CURRENT ASSETS"),
        _row("Total current assets", "1", 10, "CURRENT ASSETS"),
        _row("Something printed after it", "1", 11),
    ], pages=(10, 11))
    sent = dict(_sent(doc, "balance_sheet"))
    assert sent["Total current assets"] == "CURRENT ASSETS"
    assert sent["Something printed after it"] is None


def test_a_banner_read_afresh_after_its_total_is_a_new_section_of_that_name():
    """China SCE: the operating and the investing sections both read as "ACTIVITIES" (the second
    line of each wrapped heading); the operating rows after the page break are read under none."""
    doc = _doc("cash_flow", [
        _row("Profit before tax", "10", 80, "ACTIVITIES"),
        _row("Depreciation of property and equipment", "1", 80, "ACTIVITIES"),
        _row("Increase in properties under development", "-2", 81),
        _row("Net cash flows from operating activities", "9", 81),
        _row("Purchases of items of property and equipment", "-3", 81, "ACTIVITIES"),
        _row("Net cash flows from/(used in) investing activities", "-3", 81, "ACTIVITIES"),
        _row("NET DECREASE IN CASH AND CASH EQUIVALENTS", "6", 81),
    ], pages=(80, 81))
    sent = dict(_sent(doc, "cash_flow"))
    assert sent["Increase in properties under development"] == "ACTIVITIES"
    assert sent["Net cash flows from operating activities"] == "ACTIVITIES"
    assert sent["Purchases of items of property and equipment"] == "ACTIVITIES"
    assert sent["Net cash flows from/(used in) investing activities"] == "ACTIVITIES"
    assert sent["NET DECREASE IN CASH AND CASH EQUIVALENTS"] is None


def test_a_cas_profit_statements_bottom_lines_close_the_section_above_them():
    """000685: 其他综合收益 was carried on to 七、综合收益总额, its attribution and earnings per share."""
    doc = _doc("profit_and_loss", [
        _row("五、净利润（净亏损以“－”号填列）", "100", 30),
        _row("3.其他权益工具投资公允价值变动", "1", 30, "其他综合收益"),
        _row("6.外币财务报表折算差额", "2", 30, "其他综合收益"),
        _row("七、综合收益总额", "103", 31),
        _row("归属于母公司所有者的综合收益总额", "90", 31),
        _row("（一）基本每股收益", "0.1", 31),
    ], pages=(30, 31))
    sent = dict(_sent(doc, "profit_and_loss"))
    assert sent["6.外币财务报表折算差额"] == "其他综合收益"
    assert sent["七、综合收益总额"] is None
    assert sent["归属于母公司所有者的综合收益总额"] is None
    assert sent["（一）基本每股收益"] is None


def test_a_cross_section_total_after_a_sections_total_is_not_in_it():
    """PetroChina: TOTAL EQUITY AND NON-CURRENT LIABILITIES after TOTAL NON-CURRENT LIABILITIES."""
    doc = _doc("balance_sheet", [
        _row("Borrowings", "5", 40, "NON-CURRENT LIABILITIES"),
        _row("TOTAL NON-CURRENT LIABILITIES", "5", 40, "NON-CURRENT LIABILITIES"),
        _row("TOTAL EQUITY AND NON-CURRENT LIABILITIES", "9", 40, "NON-CURRENT LIABILITIES"),
    ], pages=(40,))
    sent = dict(_sent(doc, "balance_sheet"))
    assert sent["TOTAL NON-CURRENT LIABILITIES"] == "NON-CURRENT LIABILITIES"
    assert sent["TOTAL EQUITY AND NON-CURRENT LIABILITIES"] is None


def test_a_bare_total_borrowing_a_bottom_lines_words_keeps_its_heading():
    """Asian Paints: "PROFIT FOR THE YEAR ATTRIBUTABLE TO" over its owners' / NCI rows, the bare
    total captioned with it, stays under it."""
    head = "PROFIT FOR THE YEAR ATTRIBUTABLE TO"
    li = _row(head, "50", 20, head)
    li.caption_borrowed = True
    doc = _doc("profit_and_loss", [
        _row("Owners of the Company", "45", 20, head),
        _row("Non-controlling interests", "5", 20, head),
        li,
    ], pages=(20,))
    sent = dict(_sent(doc, "profit_and_loss"))
    assert sent[f"{head} (total, printed without a caption)"] == head
