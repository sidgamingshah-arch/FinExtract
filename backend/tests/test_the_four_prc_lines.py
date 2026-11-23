"""Sales(Revenues), Due from Related Parties (CP), and Contingent Liabilities on CAS filings.

Measured on three 2024 CAS annual reports whose live runs were reported: 湖北能源 (000883), 四创电子
(600990) and 诚志股份 (000990), against docs/PRC_Sales_Revenues_Extraction_Logic_Simplified.md,
docs/PRC_Related_Party_and_Other_Receivables_Extraction_Logic.md and
docs/PRC_Contingent_Liabilities_Extraction_Logic_Revised.md. Each test names what the filing printed
and what was published instead.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, UnitContext
from app.schemas.line_items import load_line_item_set
from app.services.contingent_liabilities import compute
from app.services.mapping import SourceRef
from app.services.note_sourced import currency_iso, prose_currency_at, resolve_sources
from app.services.notes_extract import without_empty_duplicates
from app.stages.line_item_llm import (
    _refuse_excluded_face_citations, _refuse_foreign_currency_prose)

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def by_key():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in st.items}


# ── Sales(Revenues): the face's 营业收入 is not principal-business revenue ─────────────────────

def _face(caption, at=0, value="40617220.64"):
    return {"at": at, "note": "", "statement": "profit_and_loss", "caption": caption,
            "on_face": True, "row_id": f"r{at}", "figures": {"current": value}}


def test_an_answer_of_the_face_total_is_refused_for_the_face_revenue_part(by_key):
    """湖北能源's parent company prints 一、营业收入 40,617,220.64, all of it 其他业务; the model gave
    it as the face's principal revenue and Sales(Revenues) published it."""
    item = by_key["sub__face_principal_revenue"]
    kept, refused = _refuse_excluded_face_citations(
        item, [_face("一、营业收入"), _face("其中：营业收入", at=1, value="20030698490.80")])
    assert kept == [] and len(refused) == 2
    assert all(r["refused"] == "excluded_caption" for r in refused)
    assert "figures" not in refused[0]


def test_a_face_row_captioned_principal_business_revenue_is_kept(by_key):
    item = by_key["sub__face_principal_revenue"]
    entry = _face("一、主营业务收入")
    assert _refuse_excluded_face_citations(item, [entry]) == ([entry], [])


def test_a_vetoed_row_cited_as_one_term_of_several_is_left_alone(by_key):
    """An exclusion says whether a caption standing alone IS the line; it says nothing about a row
    cited beside others."""
    item = by_key["sub__face_principal_revenue"]
    note_row = {"at": 1, "note": "七、61", "caption": "主营业务", "figures": {"current": "1"}}
    entries = [_face("营业收入"), note_row]
    assert _refuse_excluded_face_citations(item, entries) == (entries, [])


def test_a_line_without_exclusions_is_untouched(by_key):
    item = by_key["is_pl__other_operating_income"]
    entry = _face("营业收入")
    if getattr(item, "exclude_hints", None):
        pytest.skip("this line gained exclusions")
    assert _refuse_excluded_face_citations(item, [entry]) == ([entry], [])


def test_the_face_part_tells_the_model_what_it_is_held_to(by_key):
    """Holding the model to an exclusion is fair only when the request says it in words."""
    definition = by_key["sub__face_principal_revenue"].definition
    assert "主营业务收入" in definition and "营业收入" in definition and "其他业务收入" in definition


# ── a dollar is not added to yuan ──────────────────────────────────────────────────────────────

HUBEI_LC = ("（6）基于瓦亚加发电与 EMPRESA ELECTRICIDAD DEL PERU 签订的 PPA 协议，瓦亚加发电在 ICBC "
            "Peru Bank 开具信用证，截止 2024 年 12 月 31 日止，尚未结清的信用证余额为 6,568,662.00 美元。"
            "于中国农业银行股份有限公司长阳土家族自治县支行申请开具保函10,230,300.00 元。")


@pytest.mark.parametrize("amount, iso", [
    ("6568662.00", "USD"), ("10230300.00", "CNY")])
def test_the_currency_printed_beside_a_sentences_amount(amount, iso):
    assert prose_currency_at(Decimal(amount), HUBEI_LC) == iso


def test_an_amount_with_no_currency_beside_it_reads_none():
    assert prose_currency_at(Decimal("1885020"), "At 31 December 2024 ... 1,885,020") is None


@pytest.mark.parametrize("token, iso", [
    ("美元", "USD"), ("US$", "USD"), ("港元", "HKD"), ("HK$", "HKD"), ("元", "CNY"),
    ("人民币", "CNY"), ("CNY", "CNY"), ("", None), ("shares", None)])
def test_currency_tokens(token, iso):
    assert currency_iso(token) == iso


def _prose(amount, currency):
    return {"at": 0, "note": "十六、2", "caption": "", "prose": True,
            "figures": {"prose": amount}, "prose_currency": currency}


def test_a_prose_amount_in_another_currency_is_refused():
    """湖北能源's contingent liabilities: 323,426,677.00 of 保函 in 元 plus 6,620,485.11 of letters
    of credit in 美元 were published as one RMB figure, 330,047,162.11."""
    doc = DocumentModel(unit_context=UnitContext(currency="CNY"))
    yuan, dollars, unmarked = (_prose("25000000.00", "CNY"), _prose("6568662.00", "USD"),
                               _prose("1000", None))
    kept, refused = _refuse_foreign_currency_prose(doc, [yuan, dollars, unmarked])
    assert kept == [yuan, unmarked]
    assert len(refused) == 1 and refused[0]["refused"] == "foreign_currency"
    assert "USD" in refused[0]["why"] and "CNY" in refused[0]["why"]


def test_a_filing_that_declares_no_currency_refuses_nothing():
    doc = DocumentModel()
    entries = [_prose("6568662.00", "USD")]
    assert _refuse_foreign_currency_prose(doc, entries) == (entries, [])


# ── one caption printed on many rows ───────────────────────────────────────────────────────────

def _row(caption, amount):
    row = NoteItem(raw_label=caption, note_number="十四、4")
    row.values["current"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                           value=Decimal(amount), value_raw=Decimal(amount))
    return row


def test_the_same_citation_again_takes_the_next_row_that_prints_that_caption():
    """诚志股份 十四、4: thirteen guarantees print as "技有限公司南京诚志永清能源科" (a wrapped
    counterparty name). The answer cited that caption once per row and every citation resolved to
    the first row — 6,220,000 thirteen times, 804,190,000 published for 2,103,890,000."""
    wrapped = "技有限公司南京诚志永清能源科"
    note = NotesTable(note_number="十四、4", title="关联担保情况",
                      items=[_row(wrapped, "6220000.00"), _row(wrapped, "24130000.00"),
                             _row(wrapped, "29300000.00")])
    cite = [SourceRef(note="十四、4", caption=wrapped) for _ in range(3)]
    resolved, unresolved = resolve_sources(cite, [note], None, allow_face=False)
    assert not unresolved, unresolved
    got = sorted(Decimal(e["figures"]["current"]) for e in resolved)
    assert got == [Decimal("6220000.00"), Decimal("24130000.00"), Decimal("29300000.00")]


def test_a_caption_cited_once_still_reads_the_first_row():
    wrapped = "技有限公司南京诚志永清能源科"
    note = NotesTable(note_number="十四、4", title="关联担保情况",
                      items=[_row(wrapped, "6220000.00"), _row(wrapped, "24130000.00")])
    resolved, _ = resolve_sources([SourceRef(note="十四、4", caption=wrapped)], [note], None,
                                  allow_face=False)
    assert [e["figures"]["current"] for e in resolved] == ["6220000.00"]


# ── a note's heading fragment is not an empty duplicate ────────────────────────────────────────

def test_a_heading_fragment_with_its_own_title_is_kept():
    """四创电子 十六、2: "2、 或有事项" heads two fragments titled by lawsuits; the first has a row, so
    the heading was dropped as an "empty duplicate" and nothing said 或有事项 any more."""
    heading = NotesTable(note_number="十六、2", title="、 或有事项", source_text="")
    case = NotesTable(note_number="十六、2", title="四创电子与合肥时代智慧高新投资管理有限公司建设工程合同案件",
                      items=[NoteItem(raw_label="违约金")])
    carried = NotesTable(note_number="十六、2", title="四创电子与合肥时代智慧高新投资管理有限公司建设工程合同案件",
                         source_text="四创电子股份有限公司2024 年年度报告")
    kept = without_empty_duplicates([heading, case, carried])
    assert heading in kept and case in kept
    assert carried not in kept, "a fragment repeating its note's title is still furniture"


def test_a_sub_heading_further_down_a_note_is_not_kept():
    """CAS prints "（8）与合营企业或联营企业投资相关的或有负债" inside the note on interests in
    associates. Kept, it made that note a contingency note: 湖北能源's 十、3 summarised financial
    information was summed into Contingent liabilities, 178,247,836,052.64."""
    table = NotesTable(note_number="十、3", title="重要合营企业的主要财务信息",
                       items=[NoteItem(raw_label="流动资产")])
    sub = NotesTable(note_number="十、3", title="与合营企业或联营企业投资相关的或有负债",
                     source_text="")
    kept = without_empty_duplicates([table, sub])
    assert table in kept and sub not in kept


# ── the contingent-liabilities narrative ───────────────────────────────────────────────────────

def _ev(value):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
                          period_label="current",
                          unit_ctx=UnitContext(currency="CNY", scale_factor=Decimal(1)))


def _item(label, value):
    it = NoteItem(raw_label=label)
    ev = _ev(value)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _summary(*notes):
    doc = DocumentModel(notes=list(notes))
    result = compute(doc)[("consolidated", "current")]
    return {(r["type"], r.get("currency") or ""): Decimal(r["amount"])
            for r in result.classified_summary}, result


def test_a_fragment_titled_by_a_lawsuit_is_read_under_its_notes_contingency_heading():
    """四创电子 2024 p197: the 保函 and 信用证 totals are stated in the second lawsuit's fragment."""
    heading = NotesTable(note_number="十六、2", title="、 或有事项", chapter_title="承诺及或有事项")
    case = NotesTable(
        note_number="十六、2", title="四创电子与乌鲁木齐市公安局建设工程合同案件",
        chapter_title="承诺及或有事项",
        source_text=("四创电子向乌鲁木齐市水磨沟区人民法院提起诉讼，要求乌鲁木齐市公安局支付合同款及逾期利息、"
                     "履约保证金及逾期利息共计992.77 万元。 截至2024 年末，该案件尚未判决。 2．开出保函、信用证 "
                     "截至2024 年12 月31 日止，公司各类尚未到期的保函总额为11,752.35 万元 "
                     "截至2024 年12 月31 日止，公司各类尚未到期的国内信用证总额为123.10 万元"))
    got, _ = _summary(heading, case)
    assert got[("Bank guarantees", "")] == Decimal("117523500")
    assert got[("Letters of Credit", "")] == Decimal("1231000")
    # The 992.77 万元 is money the company is SUING FOR, not an exposure it carries.
    assert all(v != Decimal("9927700") for v in got.values())


def test_a_sentence_opened_in_the_title_is_read_whole_and_classified_by_its_instrument():
    """湖北能源 2024 十六、2: a fragment titled "…出具的保函金" whose text is "额为25,000,000.00 元",
    and two whose titles carry the word 履约保函."""
    head = NotesTable(note_number="十六、2", title="、或有事项")
    one = NotesTable(note_number="十六、2",
                     title="基于兵团第五师双河市90 团10 万千瓦光伏发电项目对湖北能源集团双河新能源发展有限公司出具的保函金",
                     source_text="额为25,000,000.00 元。")
    two = NotesTable(note_number="十六、2",
                     title="基于湖北电力市场售电公司关于开展2024 年售电公司签订市场化交易协议并办理银行履约保函保险事项的通",
                     source_text="知对湖北能源集团售电有限公司出具的保函金额为120,000,000.00 元。")
    lc = NotesTable(note_number="十六、2", title="资产负债表日存在的重要或有事项",
                    source_text=("截止 2024 年 12 月 31 日止，尚未结清的信用证余额为 6,568,662.00 美元。 "
                                 "尚未结清的信用证余额为 51,823.11 美元。"))
    got, _ = _summary(head, one, two, lc)
    assert got[("Bank guarantees", "")] == Decimal("25000000.00")
    assert got[("Performance bonds", "")] == Decimal("120000000.00")
    # In its own currency, never added to the yuan.
    assert got[("Letters of Credit", "USD")] == Decimal("6620485.11")


def test_a_guarantee_sub_heading_does_not_qualify_the_rest_of_its_related_party_note():
    """湖北能源 十四、5 holds purchases and leases beside "(4) 关联担保情况"."""
    guarantees = NotesTable(note_number="十四、5", title="关联担保情况",
                            items=[_item("湖北荆州煤炭港务有限公司", "300000000")])
    purchases = NotesTable(note_number="十四、5", title="购销商品、提供和接受劳务的关联交易",
                           items=[_item("三峡高科信息技术有限责任公司", "3281551.19")])
    got, result = _summary(guarantees, purchases)
    assert got == {("Corporate guarantees", "CNY"): Decimal("300000000")}
    assert not result.unclassified_items


def test_a_date_read_as_a_rows_figure_is_not_an_exposure():
    """湖北能源 十四、5 (4): the four guarantees print no 担保金额, only their dates; each row's
    figure was read as the year — 2020, 2019, 2019, 2018."""
    table = NotesTable(note_number="十四、5", title="关联担保情况",
                       source_text="湖北荆州煤炭港务有限公司（注 1） 2020 年 12 月 09 日 2034 年 12 月 09 日 否",
                       items=[_item("湖北荆州煤炭港务有限公司（注1）", "2020")])
    got, result = _summary(table)
    assert got == {}
    assert [i["amount"] for i in result.detail_items] in ([], [None])


def test_counterparty_rows_of_a_related_party_guarantee_table_are_corporate_guarantees():
    """诚志股份 十四、4: nineteen guarantees, each row captioned by the subsidiary guaranteed."""
    table = NotesTable(note_number="十四、4", title="关联担保情况",
                       items=[_item("南京诚志清洁能源有限公司", "100000000.00"),
                              _item("安徽诚志显示玻璃有限公司", "10000000.00")])
    got, _ = _summary(table)
    assert got == {("Corporate guarantees", "CNY"): Decimal("110000000.00")}


# ── money a face line gave up is not taken out of the line that received it ───────────────────

def test_a_line_that_deducted_a_part_is_recognised_as_having_given_it_up():
    """诚志股份 2024 parent company: Other Receivables(CP) deducted 关联方往来 651,977,554.59, and the
    face-settlement step then took the same amount off Due from Related Parties(CP) — because the
    face 其他应收款 "still prints it" — so it was counted on neither line."""
    from app.core.models.line_item import LineItem
    from app.stages.note_sourced import _deducted_in

    sender = LineItem(source_label="Other Receivables(CP)",
                      canonical_key="bs_ca__other_receivables_cp")
    sender.derivation = {"standalone:current": {"inputs": [
        {"canonical_key": "sub__cp_other_receivables_net", "value": "724403938.75",
         "deducted": False, "status": "used"},
        {"canonical_key": "sub__cp_other_receivables_rp", "value": "651977554.59",
         "deducted": True, "status": "deducted"}]}}
    assert _deducted_in([sender], "sub__cp_other_receivables_rp", "standalone:current")
    assert not _deducted_in([sender], "sub__cp_other_receivables_rp", "standalone:prior")
    assert not _deducted_in([sender], "sub__cp_other_receivables_net", "standalone:current")
    # A deduction the trail records as NOT applied gave nothing up.
    sender.derivation["standalone:current"]["inputs"][1]["status"] = "not_used"
    assert not _deducted_in([sender], "sub__cp_other_receivables_rp", "standalone:current")


# ── a model's figure from a note printed in 万元 is put into the statements' 元 ──────────────────

def _face_doc():
    from app.core.models.enums import PrintedIn
    from app.core.models.line_item import LineItem
    doc = DocumentModel(unit_context=UnitContext(currency="CNY"))
    li = LineItem(source_label="营业收入", canonical_key="x", printed_in=PrintedIn.FACE)
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("1603146551.95"),
                                    unit_ctx=UnitContext(currency="CNY", scale_factor=Decimal(1),
                                                         units_label="元"))
    doc.line_items.append(li)
    return doc


def test_a_cited_note_row_printed_in_wan_yuan_is_put_into_the_statements_scale():
    """四创电子 2024 十四、5 (7) 关键管理人员报酬, "单位：万元": 476.37 / 421.67. The rules route read
    4,763,700.00 / 4,216,700.00; the model's figure was taken as printed and outranked it."""
    from app.core.stage import PipelineContext
    from app.stages.line_item_llm import _in_statement_scale

    wan = {"currency": "CNY", "scale_factor": "10000", "units_label": "万元"}
    entry = {"at": 0, "note": "十四、5", "caption": "关键管理人员报酬", "basis": "consolidated",
             "figures": {"current": "476.37", "prior": "421.67"}, "unit": wan}
    face = {"at": 1, "note": "", "caption": "营业收入", "on_face": True,
            "figures": {"current": "1"}, "unit": wan}
    ctx = PipelineContext(raw_bytes=b"")
    out = _in_statement_scale(_face_doc(), [entry, face], ctx, "sub__rpt_key_management")
    assert Decimal(out[0]["figures"]["current"]) == Decimal("4763700")
    assert Decimal(out[0]["figures"]["prior"]) == Decimal("4216700")
    assert out[0]["printed_figures"] == {"current": "476.37", "prior": "421.67"}
    assert Decimal(str(out[0]["unit"]["scale_factor"])) == 1
    assert out[1] is face, "a face row is the statement's own and is not rescaled"


def test_a_note_row_in_the_statements_scale_is_untouched():
    from app.core.stage import PipelineContext
    from app.stages.line_item_llm import _in_statement_scale

    entry = {"at": 0, "note": "七、61", "caption": "主营业务", "basis": "consolidated",
             "figures": {"current": "1589859743.31"},
             "unit": {"currency": "CNY", "scale_factor": "1", "units_label": "元"}}
    out = _in_statement_scale(_face_doc(), [entry], PipelineContext(raw_bytes=b""), "k")
    assert out == [entry]


# ── a total printed on the face and again in the supplement is one total ──────────────────────

def test_a_total_printed_twice_is_reported_once():
    """四创电子 2024: 经营活动产生的现金流量净额 244,695,836.40 on the face and at the foot of the
    supplement; the residual check reported 489,391,672.80."""
    from app.core.models.enums import LineRole
    from app.core.models.line_item import LineItem
    from app.stages.residual import _by_canonical_key

    def row(caption, value, role, basis=Basis.CONSOLIDATED):
        li = LineItem(source_label=caption, canonical_key="cf_total", role=role)
        li.values[caption + basis.value] = ExtractedValue(basis=basis, period_label="current",
                                                          value=Decimal(value))
        return li

    doc = DocumentModel()
    doc.line_items = [row("经营活动产生的现金流量净额", "244695836.40", LineRole.TOTAL),
                      row("经营活动产生的现金流量净额", "244695836.40", LineRole.TOTAL),
                      row("经营活动产生的现金流量净额", "177219546.46", LineRole.TOTAL,
                          Basis.STANDALONE)]
    totals, _rows = _by_canonical_key(doc)
    assert totals["cf_total"]["consolidated|current"] == pytest.approx(244695836.40)
    assert totals["cf_total"]["standalone|current"] == pytest.approx(177219546.46)

    lines = DocumentModel()
    lines.line_items = [row("其他", "10", LineRole.LINE), row("其他", "10", LineRole.LINE)]
    assert _by_canonical_key(lines)[0]["cf_total"]["consolidated|current"] == 20.0, \
        "two LINES of one key are two amounts and are still added"


# ── the whole report waits as long as a file does ─────────────────────────────────────────────

def test_a_request_carrying_the_whole_report_waits_the_document_timeout():
    """湖北能源 2024: request(report:balance_sheet#1) FAILED ReadTimeout at 120 seconds."""
    from types import SimpleNamespace

    from app.adapters._structured import request_timeout

    settings = SimpleNamespace(llm=SimpleNamespace(timeout_seconds=120,
                                                   document_timeout_seconds=600))
    question = {"role": "user", "content": "{}"}
    assert request_timeout(settings, [question]) == 120
    report = {"role": "user", "content": "the report", "cache": True, "long_read": True}
    assert request_timeout(settings, [report, question]) == 600
    shared = {"role": "user", "content": "shared notes", "cache": True}
    assert request_timeout(settings, [shared, question]) == 120


def test_ask_marks_only_a_report_as_a_long_read():
    from app.services import line_item_llm

    seen = []

    class _P:
        def complete_structured(self, *, system, messages, response_schema, max_tokens, **_kw):
            seen.append(messages)
            return response_schema(answers=[]), None

    line_item_llm.ask(_P(), "s", {"line_items": []}, max_tokens=10, document="r", long_read=True)
    line_item_llm.ask(_P(), "s", {"line_items": []}, max_tokens=10, document="n")
    assert seen[0][0].get("long_read") is True
    assert "long_read" not in seen[1][0]
