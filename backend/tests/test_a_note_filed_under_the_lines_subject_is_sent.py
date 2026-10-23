"""A NOTE FILED UNDER THE LINE'S SUBJECT IS SENT — `line_item_notes.heading_names_line`.

Found by a blind answer to a real request (round 4): BYD Electronic's `bs_ca__net_trade_receivables`
was sent no note at all (request 1712a84870a8411f), where before commit 3dc9f41 ("a heading must
name the line") it was sent note 20 TRADE RECEIVABLES — the note the face row 'Trade receivables
應收貿易款項' cites, and the one that prints the 'Net carrying amount'. The answer abstained.

THE CAUSE. A label or alias holds a heading when the words they share carry more than half of the
name's weight, and a word no heading of the filing prints weighs the most. No heading prints "net":
a note is filed under its SUBJECT and prints every measure of it, gross, allowance and net. So
"Net Trade Receivables" against TRADE RECEIVABLES weighed 8.5 of 22.6 and failed. The same loss,
found by comparing every keyed line's notes_supplied before and after 3dc9f41 over the corpus:
无形资产情况 for Net Intangibles (alias 无形资产净额) and 其他综合收益 for Total Other Comprehensive
Income (alias 其他综合收益的税后净额) on the CAS filings.

THE RULE. A label or alias carrying a measure word (net, gross, 净额, 税后净额, 原值) is also held by a
heading that prints the name without it, AS A PHRASE. Not its words: DEBT INVESTMENT AT FAIR VALUE
THROUGH OTHER COMPREHENSIVE INCOME carries "investment" and "income" and is not "Net investment
income"'s note. And never one word: "Net profit" without "net" is every PROFIT heading's "profit".

TWO LINES THE RULE CANNOT REACH, given note_terms (config): Total Cost of Sales has English names
only, and its definition names 营业成本 as the row — 七、61 营业收入和营业成本 was lost on all eleven
CAS filings; Net Fixed Assets' note_terms (3dc9f41) replaced the probe that had carried the
definition's 在建工程 and 使用权资产, the CAS components the line is the sum of.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import LineItemDef, NoteSource, load_line_item_set
from app.services import line_item_notes

_SEED = (Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")


def _note(number: str, title: str, captions: tuple[str, ...] = ("Total",)) -> NotesTable:
    table = NotesTable(note_number=number, title=title, page_index=9)
    for caption in captions:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("1"), value_raw=Decimal("1"),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


def _notes_of(item, notes) -> list[str]:
    return [h.note for h in line_item_notes.note_sets([item], notes).get(item.key, [])]


_SET = None


def _shipped(key: str) -> LineItemDef:
    global _SET
    if _SET is None:
        _SET = load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)
    return next(i for i in _SET.items if i.key == key)


# BYD Electronic's note headings (hkex_0285, 2024), one per number, as the parser titles them.
_BYD = (
    ("5", "REVENUE, OTHER INCOME AND GAINS 5. 收入、其他收入及收益"),
    ("6", "PROFIT BEFORE TAX 6. 除稅前溢利"),
    ("7", "GOVERNMENT GRANTS AND SUBSIDIES 7. 政府補助及補貼"),
    ("8", "FINANCE COSTS 8. 融資成本"),
    ("11", "INCOME TAX 11. 所得稅"),
    ("13", "PROPERTY, PLANT AND EQUIPMENT 13. 物業、廠房及設備"),
    ("14", "LEASES 14. 租賃"),
    ("15", "PREPAYMENTS, OTHER RECEIVABLES AND 15. 預付款項、其他應收賬款及其他資 OTHER ASSETS 產"),
    ("16", "GOODWILL 16. 商譽"),
    ("17", "OTHER INTANGIBLE ASSETS 17. 其他無形資產"),
    ("18", "OTHER NON-CURRENT FINANCIAL ASSETS 18. 其他非流動金融資產"),
    ("19", "INVENTORIES 19. 存貨"),
    ("20", "TRADE RECEIVABLES 20. 應收貿易款項"),
    ("21", "RECEIVABLES FINANCING 21. 應收款項融資"),
    ("22", "CASH AND BANK BALANCES AND RESTRICTED 22. 現金及銀行結餘以及受限制銀行存 BANK DEPOSIT"),
    ("23", "TRADE AND BILLS PAYABLES 23. 應付貿易賬款及應付票據"),
    ("24", "OTHER PAYABLES, OTHER LIABILITIES AND 24. 其他應付賬款、其他負債及應計費 ACCRUALS 用"),
    ("25", "DERIVATIVE FINANCIAL INSTRUMENTS 25. 衍生金融工具"),
    ("26", "DEFERRED INCOME 26. 遞延收入"),
    ("27", "INTEREST-BEARING LOANS 27. 計息貸款"),
    ("28", "PROVISION 28. 撥備"),
    ("29", "DEFERRED TAX 29. 遞延稅項"),
    ("30", "SHARE CAPITAL 30. 股本"),
    ("31", "RESERVES 31. 儲備"),
)

# A CAS filing's note headings, as 000883 / 002273 print them.
_CAS = (
    ("七、1", "货币资金"), ("七、5", "应收账款"), ("七、21", "固定资产"), ("七、22", "在建工程"),
    ("七、25", "使用权资产情况"), ("七、26", "无形资产情况"), ("七、29", "未经抵销的递延所得税资产"),
    ("七、31", "所有权或使用权受到限制的资产"), ("七、57", "其他综合收益"), ("七、60", "未分配利润"),
    ("七、61", "营业收入和营业成本"), ("七、74", "营业外收入"), ("七、75", "营业外支出"),
    ("七、76", "所得税费用表"), ("七、78", "与经营活动有关的现金"), ("十七、2", "利润分配情况"),
)


def _byd(*extra: NotesTable) -> list[NotesTable]:
    return [_note(n, t) for n, t in _BYD] + list(extra)


def _cas(*extra: NotesTable) -> list[NotesTable]:
    return [_note(n, t, ("合计",)) for n, t in _CAS] + list(extra)


# ── the measure word ───────────────────────────────────────────────────────────────────────────

def test_byd_net_trade_receivables_is_sent_note_20_trade_receivables():
    """The defect: request 1712a84870a8411f went to the model with notes_supplied []."""
    sent = _notes_of(_shipped("bs_ca__net_trade_receivables"), _byd())
    assert "20" in sent, sent
    # and not the two 3dc9f41 removed from the same request (9220a005b00b6607 sent 20, 18, 6)
    assert not {"18", "6"} & set(sent), sent


def test_the_cas_net_and_after_tax_names_are_held_by_the_subject_heading():
    notes = _cas()
    assert "七、26" in _notes_of(_shipped("bs_nca__net_intangibles"), notes)
    assert "七、57" in _notes_of(_shipped("is_oci__total_other_comprehensive_income"), notes)


def test_the_measure_word_is_removed_from_the_text_not_from_the_tokens():
    assert line_item_notes.name_subject("Net Trade Receivables") == ("trade", "receivable")
    assert line_item_notes.name_subject("无形资产净额") == ("无形资产",)
    assert line_item_notes.name_subject("其他综合收益的税后净额") == ("其他综合收益",)
    assert line_item_notes.name_subject("Gross Fixed Assets") == ("fixed", "asset")
    # nothing to remove, or one word left: no subject
    assert line_item_notes.name_subject("Total Cost of Sales") == ()
    assert line_item_notes.name_subject("Net profit") == ()
    assert line_item_notes.name_subject("Net Intangibles") == ()


def test_the_subject_is_a_phrase_not_a_bag_of_its_words():
    """Far East Horizon note 21 carries both words of "Net investment income", apart."""
    item = _shipped("supplemental_data__net_investment_income_insur_co_only")
    notes = [_note("21", "DEBT INVESTMENT AT FAIR VALUE THROUGH OTHER COMPREHENSIVE INCOME"),
             _note("5", "INTEREST INCOME, REVENUE FROM OPERATING LEASES"),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT"), _note("30", "TRADE AND BILLS PAYABLES")]
    assert "21" not in _notes_of(item, notes)


def test_one_word_left_is_not_a_subject():
    net = LineItemDef(key="is_pl__net_profit_t", label="Net profit", aliases=["Net profit"])
    notes = [_note("7", "PROFIT BEFORE TAX"), _note("12", "EARNINGS PER SHARE"),
             _note("13", "PROPERTY, PLANT AND EQUIPMENT"), _note("19", "INVENTORIES")]
    assert "7" not in _notes_of(net, notes)


def test_the_cash_flow_supplement_is_still_not_sent_for_the_cash_flow_totals():
    """经营活动产生的现金流量净额 without 净额 is not printed by 与经营活动有关的现金."""
    notes = _cas()
    for key in ("cf_oper_indirect__cash_flows_oper_activ_indirect",
                "cf_investing__cash_flows_from_invest_activities",
                "cf_financing__cash_flows_from_finance_activities"):
        assert "七、78" not in _notes_of(_shipped(key), notes), key


# ── note_terms_none on the gate ────────────────────────────────────────────────────────────────

def test_a_refused_heading_is_refused_by_the_gate_as_by_covered_notes():
    item = LineItemDef(key="bs_nca__cip_t", label="Construction in progress",
                       note_source=NoteSource(note_terms=["在建工程"],
                                              note_terms_none=["在建工程合同"]))
    notes = [_note("19", "CONTRACT IN PROGRESS 19. 在建工程合同"), _note("七、22", "在建工程"),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT"), _note("30", "SHARE CAPITAL")]
    sent = _notes_of(item, notes)
    assert "七、22" in sent and "19" not in sent, sent


# ── the two lines given note_terms ─────────────────────────────────────────────────────────────

def test_total_cost_of_sales_is_sent_the_cas_revenue_and_cost_note():
    sent = _notes_of(_shipped("is_pl__total_cost_of_sales"), _cas())
    assert "七、61" in sent, sent
    # and still none of the notes 3dc9f41 removed from it on 000883 (31880a71396e4edf)
    assert not {"七、74", "七、75", "七、60"} & set(sent), sent


def test_net_fixed_assets_is_sent_the_notes_of_the_cas_rows_it_adds_up():
    """000883's key: 固定资产 + 在建工程 + 使用权资产. Not the restricted-assets note, which carries
    使用权 and 资产, nor 1966's CONTRACT IN PROGRESS 在建工程合同."""
    notes = _cas(_note("19", "CONTRACT IN PROGRESS 19. 在建工程合同", ("合计",)),
                 # 688411's lessee note fragment: a sublease's income, not the asset
                 _note("七、58", "转租使用权资产取得的收入", ("合计",)))
    sent = _notes_of(_shipped("bs_nca__net_fixed_assets"), notes)
    assert {"七、21", "七、22", "七、25"} <= set(sent), sent
    assert not {"七、31", "19", "七、58"} & set(sent), sent
