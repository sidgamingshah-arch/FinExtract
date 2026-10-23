"""A REQUEST IS SENT THE NOTES THAT CAN CARRY THE LINE'S FIGURE OR ITS PARTS — `line_item_notes`.

Found by blind answers to real requests (round 2), each a note supplied for a line it cannot carry:

  1. A HEADING SHARING ONE WORD. 营业外收入 / 营业外支出 / 未分配利润 for Gross Profit and Total Cost
     of Sales on 营业 or 利润 alone; 未分配利润 / 利润分配情况 / 所得税费用表 for the PBT depreciation
     parts on 利润 alone; TAX / FINANCE COSTS / DEFERRED TAX for staff costs; CONTINGENT
     LIABILITIES for "Interest on lease liabilities"; the deferred-tax note for profit before tax;
     the cash-flow supplement 与经营活动有关的现金 for operating profit.
  2. ANOTHER ENTITY'S FIGURES UNDER A MATCHING CAPTION. 十、3 重要联营企业的主要财务信息 (an
     associate's 净利润 / 资产合计 / 负债合计) and the segment note for the group's totals; Lai Sun's
     note 43 PARTLY-OWNED SUBSIDIARIES for the group's profit and non-current assets; the
     change-in-ownership note 十、2 for profit for the year.
  3. THE NOTE THE LINE'S OWN NOTE POINTS TO, NOT SENT. "Staff costs (including directors' and
     chief executive officer's remuneration — note 9)" with note 9 left out.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import LineItemDef, NoteSource, load_line_item_set
from app.services import line_item_notes, note_context

_SEED = (Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")


def _note(number: str, title: str, captions: tuple[str, ...] = ("合计",), *, chapter: str = "",
          prose: str = "") -> NotesTable:
    table = NotesTable(note_number=number, title=title, page_index=9, chapter_title=chapter,
                       prose_text=prose or None)
    for caption in captions:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("1"), value_raw=Decimal("1"),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


def _notes_of(item, notes) -> list[str]:
    return [h.note for h in line_item_notes.note_sets([item], notes).get(item.key, [])]


def _shipped(key: str) -> LineItemDef:
    st = load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)
    return next(i for i in st.items if i.key == key)


# A CAS filing's note headings, as 000883 prints them.
_CAS = (("七、1", "货币资金"), ("七、21", "固定资产"), ("七、29", "未经抵销的递延所得税资产"),
        ("七、60", "未分配利润"), ("七、61", "营业收入和营业成本"), ("七、74", "营业外收入"),
        ("七、75", "营业外支出"), ("七、76", "所得税费用表"), ("七、78", "与经营活动有关的现金"),
        ("十七、2", "利润分配情况"))


def _cas(*extra: NotesTable) -> list[NotesTable]:
    return [_note(n, t) for n, t in _CAS] + list(extra)


# ── 1. a heading that shares one word with the line ─────────────────────────────────────────────

def test_a_heading_sharing_one_bigram_is_not_sent():
    gross = LineItemDef(key="is_pl__gross_profit_t", label="Gross Profit",
                        aliases_i18n={"zh": ["毛利", "毛利润", "营业毛利"]},
                        definition="The subtotal the filing PRINTS as 'Gross Profit'.")
    notes = _cas()
    sent = _notes_of(gross, notes)
    assert not {"七、60", "七、74", "七、75"} & set(sent), sent


def test_the_pbt_depreciation_parts_are_not_sent_retained_earnings_or_income_tax():
    notes = _cas()
    for key in ("sub__pbt_depreciation", "sub__pbt_oper_exp_depreciation"):
        sent = _notes_of(_shipped(key), notes)
        assert not {"七、60", "十七、2", "七、76"} & set(sent), (key, sent)


def test_the_identified_note_is_not_marked_for_a_line_whose_name_it_does_not_hold():
    """`identified_for` reads the same gate: the income-tax note is not tagged for the PBT
    depreciation parts (blind requests efcb490da5da9c87, 260ca3e4bc76e5f9)."""
    st = load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)
    blocks = note_context.identified_notes(st, _cas())
    tagged = {k for b in blocks if str(b.get("note")) == "七、76" for k in b["identified_for"]}
    assert not {"sub__pbt_depreciation", "sub__pbt_oper_exp_depreciation"} & tagged, tagged


def test_an_authored_term_is_held_by_two_of_its_words_not_one():
    staff = LineItemDef(key="is_pl__staff_t", label="Personnel & Benefit Expenses",
                        note_source=NoteSource(note_terms=["staff costs", "before tax",
                                                           "from operating activities"]))
    notes = [_note("7", "LOSS FROM OPERATING ACTIVITIES", ("Staff costs",)),
             _note("8", "FINANCE COSTS", ("Interest on bank loans",)),
             _note("11", "TAX", ("Current tax",)),
             _note("39", "DEFERRED TAX", ("At 1 April",)),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT", ("Cost",))]
    sent = _notes_of(staff, notes)
    assert "7" in sent
    assert not {"8", "11", "39"} & set(sent), sent


def test_a_label_held_only_by_its_weakest_word_is_not_sent():
    lease = LineItemDef(key="is_pl__lease_interest_t", label="Interest on lease liabilities",
                        aliases=["Interest on lease liabilities"])
    notes = [_note("45", "CONTINGENT LIABILITIES", ("Guarantees given",)),
             _note("30", "COMPLETED PROPERTIES FOR SALE", ("At cost",)),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT", ("Cost",))]
    assert "45" not in _notes_of(lease, notes)


# ── 2. another entity's figures ────────────────────────────────────────────────────────────────

def test_an_associates_note_is_not_sent_for_the_groups_line_on_a_matching_caption():
    net = LineItemDef(key="is_pl__net_profit_t", label="Net profit",
                      aliases_i18n={"zh": ["净利润"]})
    notes = [_note("七、1", "货币资金"), _note("七、21", "固定资产"),
             _note("七、78", "与经营活动有关的现金"),
             _note("十、3", "重要联营企业的主要财务信息", ("净利润", "资产合计"),
                   chapter="在其他主体中的权益"),
             _note("十八、6", "报告分部的财务信息", ("净利润",), chapter="其他重要事项")]
    sent = _notes_of(net, notes)
    assert not {"十、3", "十八、6"} & set(sent), sent


def test_a_line_about_the_entity_is_still_sent_its_note():
    assoc = LineItemDef(key="bs_nca__assoc_t", label="Investments in associates",
                        aliases=["Investments in associates"])
    notes = [_note("23", "INVESTMENTS IN ASSOCIATES", ("Share of net assets",)),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT", ("Cost",)),
             _note("11", "TAX", ("Current tax",))]
    assert "23" in _notes_of(assoc, notes)


def test_a_change_in_ownership_note_is_not_sent_for_profit_for_the_year():
    item = _shipped("is_pl__profit_for_the_year")
    notes = _cas(_note("十、2", "交易对于少数股东权益及归属于母公司所有者权益的影响",
                       ("差额",), chapter="在其他主体中的权益"))
    assert "十、2" not in _notes_of(item, notes)


def test_a_subtotal_is_not_reached_by_a_caption_in_some_other_note():
    """A line the template adds up (`terms`) is printed on its statement; a note row carrying its
    name is a subtotal of something else — Lai Sun's capital-management "Equity attributable to
    owners of the Company" for profit for the year."""
    item = _shipped("is_pl__profit_for_the_year")
    notes = [_note("48", "FINANCIAL RISK MANAGEMENT OBJECTIVES AND POLICIES",
                   ("Equity attributable to owners of the Company",)),
             _note("11", "TAX", ("Current tax",)),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT", ("Cost",))]
    assert "48" not in _notes_of(item, notes)


def test_a_leaf_line_still_reaches_the_note_that_prints_its_row():
    lease = LineItemDef(key="is_pl__lease_interest_t", label="Interest on lease liabilities",
                        aliases=["Interest on lease liabilities"])
    notes = [_note("8", "FINANCE COSTS", ("Interest on bank loans",
                                          "Interest on lease liabilities")),
             _note("24", "INVESTMENTS IN JOINT VENTURES", ("Interest on lease liabilities",)),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT", ("Cost",))]
    sent = _notes_of(lease, notes)
    assert "8" in sent and "24" not in sent, sent


# ── 3. the note the line's own note points to ──────────────────────────────────────────────────

def test_the_note_a_supplied_note_points_to_for_the_line_is_sent():
    staff = _shipped("is_pl__personnel_and_benefit_expenses")
    notes = [_note("7", "LOSS FROM OPERATING ACTIVITIES", ("Wages and salaries",),
                   prose="Write-down of properties 528,000 430,600 Staff costs (including "
                         "directors’ and chief executive officer’s remuneration — note 9): "
                         "1,330,601 1,409,600"),
             _note("9", "DIRECTORS’ AND CHIEF EXECUTIVE’S REMUNERATION", ("Fees",)),
             _note("11", "TAX", ("Current tax",))]
    sent = line_item_notes.note_sets([staff], notes)[staff.key]
    assert ("9", "referenced") in {(h.note, h.via) for h in sent}


def test_a_reference_in_a_sentence_about_something_else_is_not_followed():
    item = LineItemDef(key="sub__ip_dep_t", label="Investment property depreciation",
                       note_source=NoteSource(note_terms=["investment properties"]))
    notes = [_note("15", "INVESTMENT PROPERTIES", ("At 1 April",),
                   prose="The investment properties of the Group are leased to third parties "
                         "under operating leases, further summary details of which are "
                         "included in note 16 to the financial statements."),
             _note("16", "LEASES", ("Right-of-use assets",)),
             _note("11", "TAX", ("Current tax",))]
    assert "16" not in _notes_of(item, notes)


# ── 4. the lines whose names did not name their note ───────────────────────────────────────────

def test_the_commitment_borrowing_and_lease_lines_name_their_notes():
    hk = [_note("44", "COMMITMENTS", ("Contracted, but not provided for",)),
          _note("37", "BANK BORROWINGS", ("Bank loans — secured",)),
          _note("16", "LEASES", ("Depreciation charge",)),
          _note("45", "CONTINGENT LIABILITIES", ("Guarantees given",)),
          _note("40", "SHARE CAPITAL", ("Issued and fully paid",)),
          _note("14", "PROPERTY, PLANT AND EQUIPMENT", ("Cost",)),
          _note("41", "FINANCIAL GUARANTEES", ("Guarantees given to banks in connection with loan "
                                               "facilities granted to joint ventures",)),
          _note("11", "TAX", ("Current tax",))]
    for key, note in (("capital_and_lease_commitments__capital_commitments", "44"),
                      ("capital_and_lease_commitments__capital_commitments_for_fixed_assets",
                       "44"),
                      ("bs_cl__st_bank_loans_payable", "37"),
                      ("bs_nca__gross_fixed_assets", "14"),
                      ("bs_nca__net_fixed_assets", "14"),
                      ("off_balance_sheet_data__off_balance_sheet_liabilities", "45")):
        assert note in _notes_of(_shipped(key), hk), key
    assert "40" not in _notes_of(_shipped("capital_and_lease_commitments__capital_commitments"),
                                 hk)


def test_a_heading_holding_a_terms_head_is_sent_when_a_row_is_the_lines_row():
    """1966's FINANCIAL GUARANTEES holds only the head of "related party guarantees", and prints
    the row the line's own row rule takes; a heading holding the head with no such row is not
    sent, and neither is one holding a word that is not the head."""
    item = _shipped("sub__cl_related_party_guarantee_balance")
    notes = [_note("41", "FINANCIAL GUARANTEES",
                   ("Guarantees given to banks in connection with loan facilities granted to "
                    "joint ventures and associates",)),
             _note("42", "PERFORMANCE GUARANTEES", ("Total",)),
             _note("44", "RELATED COMPANIES", ("Guarantees given to banks for related companies",)),
             _note("11", "TAX", ("Current tax",)),
             _note("14", "PROPERTY, PLANT AND EQUIPMENT", ("Cost",))]
    sent = _notes_of(item, notes)
    assert "41" in sent and "42" not in sent and "44" not in sent, sent
