"""Text-valued lines — the auditor's opinion, the entity's name, the currency, the framework, the
auditor — are answerable: their request carries the pages that print them, the reply can give the
text as printed with its page, the text is checked against that page, the stage writes it onto the
line and the export writes it (`services.text_lines`, `services.line_item_llm.TEXT_REPLY_CONTRACT`).

Before this, each of these lines was sent as a figure request with nothing in it — no page, no note,
no statement row — under a contract that forbade stating anything, so none could be answered.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.routes.extractions import _build_supplemental_rows, _serialize_rows
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import DocFormat
from app.core.models.line_item import UnitContext
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import line_item_llm, text_lines
from app.services.line_item_notes import printed_names
from app.services.line_item_requests import RequestPlan
from app.stages import line_item_llm as stage

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
HK_SET = TEMPLATES / "output_csv_hk_line_items.json"
HK_TEMPLATE = TEMPLATES / "output_csv_hk_v1_template.json"

#: The text lines of the HK template, every one of them read off a page as printed words.
TEXT_KEYS = {
    "notes__auditor_s_opinion",
    *(f"statement_setup_controls__{k}" for k in (
        "customer_s_name", "customer_statement_type", "rounding", "source_currency",
        "statement_date", "audit_opinion_stmt_source", "accounting_standard", "accountant",
        "statement_type", "reconcile_to")),
}

AUDIT_PAGE = ("Independent Auditor's Report\nOPINION\nIn our opinion, the consolidated financial "
              "statements give a true and\nfair view of the consolidated financial position.\n"
              "Ernst & Young\nCertified Public Accountants\nHong Kong")
PAGES = [(0, "Annual Report 2024"), (1, "Chairman's statement. The auditor was thanked."),
         (2, AUDIT_PAGE), (3, "Corporate Information\nAUDITOR\nErnst & Young\nCertified Public "
                              "Accountants\nRegistered Public Interest Entity Auditor")]


@pytest.fixture(scope="module")
def items():
    cfg = load_line_item_set(json.loads(HK_SET.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in cfg.items}


# ── the configuration ─────────────────────────────────────────────────────────────────────────

def test_every_text_line_of_the_hk_set_is_read_as_printed_words(items):
    assert {k for k, i in items.items() if text_lines.is_text_line(i)} == TEXT_KEYS


def test_every_text_line_names_the_pages_that_print_it_in_both_scripts(items):
    """A name under four characters selects nothing, so each line needs longer ones — an English
    one for an HKEX filing and a Chinese one for a mainland filing."""
    for key in TEXT_KEYS:
        names = [n for n in printed_names(items[key]) if len(text_lines.fold(n)) >= 4]
        assert any(n.isascii() for n in names), key
        assert any(not n.isascii() for n in names), key


# ── the pages a request carries ───────────────────────────────────────────────────────────────

def test_the_pages_that_print_most_of_the_names_are_selected_not_every_page():
    names = ["Independent Auditor", "Certified Public Accountants", "Auditor"]
    assert text_lines.select_pages(PAGES, names) == [1, 2, 3]
    assert text_lines.select_pages(PAGES, names, cap=2) == [2, 3]
    assert text_lines.select_pages(PAGES, ["Opinion"]) == [2]


def test_a_name_too_short_to_discriminate_selects_nothing():
    assert text_lines.select_pages(PAGES, ["Opi", "AR"]) == []


def test_the_block_numbers_pages_from_one_and_carries_their_folio():
    doc = SimpleNamespace(pages=[PageSource(index=2, printed_page="73")])
    blocks = text_lines.text_pages(PAGES, ["Certified Public Accountants"], doc)
    assert [b["page"] for b in blocks] == [3, 4]
    assert blocks[0]["printed_page"] == "73" and "Ernst & Young" in blocks[0]["text"]


# ── the pages a line's definition names come first ────────────────────────────────────────────
#
# A mainland filing, shaped as 000685 prints it: the auditor's report opens on p5 (一、审计报告
# under the running header), the consolidated balance sheet is p7 and the parent's p8, and the
# accounting-policy pages p10-p12 say 资产负债表日 again and again. Ranked by how often a page
# prints the line's names, statement_date was sent the three policy pages — none prints the date.

HEADER = "中山公用事业集团股份有限公司2024 年年度报告全文 \n"
POLICY = HEADER + "资产负债表日" * 6 + "合并资产负债表 按照资产负债表日的汇率折算。"
CAS_PAGES = [
    (0, "2024 年年度报告"),
    (3, HEADER + "第四节 公司治理 \n" + "报告期内，公司董事会按照内部控制规范体系的要求，"
        "对公司内部控制的有效性进行了评价。" * 4 + "会计师事务所出具了标准无保留意见的内部控制审计报告。"),
    (4, HEADER + "第十节 财务报告 \n一、审计报告 \n审计意见类型 \n标准的无保留意见 \n审 计 报 告 \n"
        "我们审计了财务报表，包括2024 年12 月31 日的合并及母公司资产负债表。"),
    (5, HEADER + "关键审计事项 审计报告"),
    (6, HEADER + "1、合并资产负债表 \n编制单位：中山公用 \n2024 年12 月31 日 \n单位：元"),
    (7, HEADER + "2、母公司资产负债表 \n2024 年12 月31 日 \n单位：元"),
    (9, POLICY), (10, POLICY), (11, POLICY),
]


def _cas_doc():
    from app.core.models.enums import PageKind

    def face(i, statement, title):
        return PageSource(index=i, kind=PageKind.FACE, statement=statement,
                          evidence={"matched_title": title} if title else {})
    return SimpleNamespace(pages=[
        PageSource(index=0, kind=PageKind.COVER), PageSource(index=3, kind=PageKind.OTHER),
        PageSource(index=4, kind=PageKind.OTHER), PageSource(index=5, kind=PageKind.OTHER),
        face(6, "balance_sheet", "1、合并资产负债表"), face(7, "balance_sheet", "2、母公司资产负债表"),
        PageSource(index=9, kind=PageKind.NOTES), PageSource(index=10, kind=PageKind.NOTES),
        PageSource(index=11, kind=PageKind.NOTES)])


def test_the_statement_face_and_the_auditors_report_are_sent_before_pages_that_repeat_the_name(
        items):
    """statement_date: 'as the statements or the auditor's report print it' — the balance sheet
    the run identified by its title, and the report's opening page, outrank three policy pages
    that print 资产负债表日 six times each. Before this the request carried p10-p12 only."""
    key = "statement_setup_controls__statement_date"
    blocks = text_lines.text_pages(CAS_PAGES, printed_names(items[key]), _cas_doc())
    assert [b["page"] for b in blocks][:2] == [5, 7]
    assert all(any(text_lines.fold("2024年12月31日") in f for f in text_lines._forms(b["text"]))
               for b in blocks[:2])


def test_the_auditors_report_opens_where_its_title_heads_the_page_before_the_statements():
    """The report opens on p5, the page whose head prints 审计报告 inside the window before the
    first statement face. The governance page that names the internal-control 审计报告 in a sentence
    and the key-audit-matter page further into the report do not open it."""
    assert text_lines.report_openings(CAS_PAGES, _cas_doc()) == [4]


def test_a_face_is_anchored_by_the_title_the_run_identified_it_by_not_by_a_running_header(items):
    """statement_type names 年度报告, which the running header prints on every page, the faces
    included: it is not sent the faces. rounding names 资产负债表, which both balance sheets' titles
    contain: the first one titled with it (the consolidated) is tier 0, the parent's a further page.
    statement_date names 合并资产负债表 — the consolidated title only — and 审计报告."""
    doc = _cas_doc()
    assert text_lines.anchored_pages(CAS_PAGES, printed_names(
        items["statement_setup_controls__statement_type"]), doc) == {}
    assert text_lines.anchored_pages(CAS_PAGES, printed_names(
        items["statement_setup_controls__rounding"]), doc) == {6: 0, 7: 1}
    assert text_lines.anchored_pages(CAS_PAGES, printed_names(
        items["statement_setup_controls__statement_date"]), doc) == {4: 0, 6: 0}


def test_the_first_page_of_each_named_statement_comes_before_a_further_page_of_one():
    """reconcile_to on an HKEX filing: the balance sheet over three headed pages and the statement
    of changes in equity, whose 'At 31 December 2022' row prints the comparative date. Ranked by
    score alone, the three balance-sheet pages filled the request (hkex_1966)."""
    from app.core.models.enums import PageKind

    sofp = "CONSOLIDATED STATEMENT OF FINANCIAL POSITION\n31 December 2023\n2023 2022"
    pages = [(i, sofp) for i in (103, 104, 105)] + [
        (106, "CONSOLIDATED STATEMENT OF CHANGES IN EQUITY\nAt 31 December 2022")]
    doc = SimpleNamespace(pages=[
        PageSource(index=i, kind=PageKind.FACE, statement=s, evidence={"matched_title": t})
        for i, s, t in [(103, "balance_sheet", sofp.split("\n")[0]),
                        (104, "balance_sheet", sofp.split("\n")[0]),
                        (105, "balance_sheet", sofp.split("\n")[0]),
                        (106, "changes_in_equity", "CONSOLIDATED STATEMENT OF CHANGES IN EQUITY")]])
    names = ["Consolidated Statement of Financial Position",
             "Consolidated Statement of Changes in Equity"]
    assert text_lines.anchored_pages(pages, names, doc) == {103: 0, 104: 1, 105: 1, 106: 0}
    assert [b["page"] for b in text_lines.text_pages(pages, names, doc)] == [104, 105, 107]


#: Primary-statement titles as the classifier matches them on the corpus (`matched_title`).
ZH_TITLES = ("1、合并资产负债表", "2、母公司资产负债表", "3、合并利润表", "7、合并所有者权益变动表")
EN_TITLES = ("CONSOLIDATED STATEMENT OF FINANCIAL POSITION", "CONSOLIDATED STATEMENT OF PROFIT OR LOSS",
             "Consolidated Income Statement", "CONSOLIDATED STATEMENT OF CHANGES IN EQUITY")


def test_each_line_that_reads_the_statements_names_a_statement_title_in_both_scripts(items):
    """The lines whose definition sends them to the statements — the date, the scale, the scope
    word of the titles, the comparative date — each name a title the run identifies a face by, in
    both scripts; the reporting date also names the auditor's report. (Each with its OWN spelling:
    a title given to two of these lines would be an alias tie, `test_configuration_invariants`.)"""
    for key in ("statement_date", "rounding", "customer_statement_type", "reconcile_to"):
        names = [text_lines.fold(n) for n in printed_names(items[f"statement_setup_controls__{key}"])
                 if len(text_lines.fold(n)) >= text_lines.MIN_NAME_CHARS]
        assert any(n in text_lines.fold(t) for n in names for t in ZH_TITLES), key
        assert any(n in text_lines.fold(t) for n in names for t in EN_TITLES), key
    names = [text_lines.fold(n) for n in printed_names(items["statement_setup_controls__statement_date"])]
    for title in ("审计报告", "Auditor's Report"):
        assert any(text_lines.fold(title) in n for n in names), title


# ── the comparative date, the entity's name, the opinion type ────────────────────────────────

#: A Shenzhen-format CAS filing: the balance sheet heads its comparative column 期初余额 and prints
#: no date beside 2024年12月31日; a risk note compares "(2023年12月31日：…)"; a governance page
#: prints the date too. The shape of 000685 (p136 face, p261 note, p64 governance).
SZ_FACE = (HEADER + "1、合并资产负债表 \n2024 年12 月31 日 \n单位：元 \n项目 期末余额 期初余额 \n"
           "流动资产： \n货币资金 1,000.00 900.00")
SZ_PAGES = [
    (0, "中山公用事业集团股份有限公司 \n2024 年年度报告"),
    (63, HEADER + "我们认为：公司2023年度财务报表在所有重大方面公允反映了公司2023年12月31日的财务状况。"),
    (131, HEADER + "一、审计报告 \n审计意见类型 \n标准的无保留意见 \n致中山公用事业集团股份有限公司全体股东："
          "我们审计了财务报表，包括2024 年12 月31 日的合并及母公司资产负债表。"),
    (135, SZ_FACE),
    (137, HEADER + "2、母公司资产负债表 \n2024 年12 月31 日 \n项目 期末余额 期初余额"),
    (140, HEADER + "7、合并所有者权益变动表 \n项目 2024 年度 \n一、上年年末余额"),
    (260, HEADER + "截至2024年12月31日，公司以浮动利率计息的银行借款人民币4,943,055,043.05元"
          "（2023年12月31日：人民币4,906,568,364.87元）。"),
]


def _sz_doc():
    from app.core.models.enums import PageKind

    def face(i, statement, title):
        return PageSource(index=i, kind=PageKind.FACE, statement=statement,
                          evidence={"matched_title": title})
    return SimpleNamespace(pages=[
        PageSource(index=0, kind=PageKind.COVER), PageSource(index=63, kind=PageKind.OTHER),
        PageSource(index=131, kind=PageKind.OTHER),
        face(135, "balance_sheet", "1、合并资产负债表"), face(137, "balance_sheet", "2、母公司资产负债表"),
        face(140, "changes_in_equity", "7、合并所有者权益变动表"),
        PageSource(index=260, kind=PageKind.NOTES)])


def _carries(blocks, text) -> list[int]:
    want = text_lines.fold(text)
    return [b["page"] for b in blocks if any(want in f for f in text_lines._forms(b["text"]))]


def test_the_comparative_date_is_read_off_the_page_that_prints_it_when_the_statements_do_not(items):
    """reconcile_to on a Shenzhen-format CAS filing (000685, 000709, 000883, 002273, 300257, 300319,
    300391, 301577): the statements print 期初余额 and 上年年末余额, never 2023年12月31日, so the
    request carried the auditor's report and the parent balance sheet and no page that prints the
    value. The prior period's end date — one year before the statement date the balance sheet
    prints — is printed in a risk note, which is ranked first, ahead of the governance page that
    prints it too."""
    key = "statement_setup_controls__reconcile_to"
    doc = _sz_doc()
    import datetime
    assert text_lines.statement_date(SZ_PAGES, doc) == datetime.date(2024, 12, 31)
    assert text_lines.prior_date_page(SZ_PAGES, doc) == 260
    blocks = text_lines.text_pages(SZ_PAGES, printed_names(items[key]), doc)
    assert 261 in [b["page"] for b in blocks]
    assert _carries(blocks, "2023年12月31日") == [261]


def test_a_statement_that_prints_the_comparative_date_is_the_page_it_is_read_from():
    """On an HKEX filing the statements print it: the equity statement's 'At 31 March 2023' row
    (hkex_0160, whose balance sheet columns print years only) is ranked before the notes that
    print the date as well."""
    from app.core.models.enums import PageKind

    pages = [(57, "CONSOLIDATED STATEMENT OF FINANCIAL POSITION\n31 March 2024\n2024 2023\nHK$'000"),
             (59, "CONSOLIDATED STATEMENT OF CHANGES IN EQUITY\nAt 1 April 2022\nAt 31 March 2023 and "
                  "1 April 2023\nAt 31 March 2024"),
             (80, "Notes. At 31 March 2023, the Group's bank loans were secured.")]
    doc = SimpleNamespace(pages=[
        PageSource(index=57, kind=PageKind.FACE, statement="balance_sheet",
                   evidence={"matched_title": "CONSOLIDATED STATEMENT OF FINANCIAL POSITION"}),
        PageSource(index=59, kind=PageKind.FACE, statement="changes_in_equity",
                   evidence={"matched_title": "CONSOLIDATED STATEMENT OF CHANGES IN EQUITY"}),
        PageSource(index=80, kind=PageKind.NOTES)])
    assert text_lines.prior_date_page(pages, doc) == 59
    assert text_lines.designated_pages(pages, ["Comparative Date"], doc)[59] == -1


def test_no_page_is_designated_when_no_page_prints_the_prior_date():
    """000709 prints 2023年12月31日 nowhere (its five-year table says 2023年末): nothing is
    ranked first, and the line's own names select its pages as before."""
    pages = [(i, t) for i, t in SZ_PAGES if i != 260 and i != 63]
    assert text_lines.prior_date_page(pages, _sz_doc()) is None
    assert -1 not in text_lines.designated_pages(pages, ["Comparative Date"], _sz_doc()).values()


def test_the_statement_date_is_read_from_the_titled_statements_not_any_page_called_a_face():
    """000709's company-history pages are classified as faces with no title and print
    2009年12月31日 three times; the balance sheet the run identified by its title prints the date
    once. The titled statement decides."""
    from app.core.models.enums import PageKind
    import datetime

    pages = [(81, "1、合并资产负债表 \n2024年12月31日 \n单位：元"),
             (96, "2009年12月31日 2009年12月31日 2009年12月31日 公司历史沿革")]
    doc = SimpleNamespace(pages=[
        PageSource(index=81, kind=PageKind.FACE, statement="balance_sheet",
                   evidence={"matched_title": "1、合并资产负债表"}),
        PageSource(index=96, kind=PageKind.FACE)])
    assert text_lines.statement_date(pages, doc) == datetime.date(2024, 12, 31)


def test_a_date_is_matched_in_every_form_a_filing_prints_it():
    import datetime

    text = ("截至2024 年 12 月 31 日 二零二三年十二月三十一日 At 31 March 2024 "
            "December 31, 2023 二〇二二年六月三十日")
    assert text_lines.printed_dates(text) == [
        datetime.date(2024, 12, 31), datetime.date(2023, 12, 31), datetime.date(2022, 6, 30),
        datetime.date(2024, 3, 31), datetime.date(2023, 12, 31)]
    forms = text_lines.date_forms(datetime.date(2023, 12, 31))
    for printed in ("2023年12月31日", "二零二三年十二月三十一日", "31 December 2023",
                    "December 31, 2023"):
        assert text_lines.fold(printed) in forms
    assert text_lines.prior_period_end(datetime.date(2024, 2, 29)) == datetime.date(2023, 2, 28)


def test_the_opinion_type_is_not_read_from_the_internal_control_audit(items):
    """audit_opinion_stmt_source on hkex_0857: the governance page (p66) says KPMG audited the
    internal control system 'and issued a standard and unqualified audit opinion', and on name
    count it was sent, with the two auditor's reports, ahead of the chairman's statement (p6) that
    names the opinion on the financial statements. Its internal-control sentences no longer count,
    and of the pages printing as many names the one that names an opinion type comes first."""
    key = "statement_setup_controls__audit_opinion_stmt_source"
    report = ("INDEPENDENT AUDITOR'S REPORT\nOpinion\nIn our opinion, the consolidated financial "
              "statements give a true and fair view.\nBasis for Opinion\nWe are independent. "
              "Opinion. Our opinion. Our opinion.")
    pages = [
        (5, "CHAIRMAN'S REPORT\nThe financial statements have been audited by KPMG Huazhen LLP and "
            "KPMG, respectively, and both firms \nhave issued unqualified opinions on the "
            "financial statements."),
        (65, "CORPORATE GOVERNANCE\nThe Company had engaged KPMG Huazhen LLP which \naudited the "
             "effectiveness of the internal control system in \nrelation to financial report of "
             "the Company and issued a \nstandard and unqualified audit opinion. \nThey put "
             "forward independent opinions and review opinions."),
        (119, report), (123, "Key audit matters, in forming our audit opinion. Our opinion."),
        (219, report)]
    blocks = text_lines.text_pages(pages, printed_names(items[key]), None)
    assert [b["page"] for b in blocks] == [6, 120, 220]
    # On a CAS filing the governance page's 内部控制审计报告 is not sent; the report's type is.
    blocks = text_lines.text_pages(CAS_PAGES, printed_names(items[key]), _cas_doc())
    assert [b["page"] for b in blocks] == [5]


def test_internal_control_sentences_are_dropped_for_scoring_only():
    text = ("审计意见类型 标准的无保留意见。会计师事务所出具了标准无保留意见的内部控制审计报告。"
            "We conducted our audit. Our audit is not for the purpose of expressing an opinion on "
            "the effectiveness of the Group's internal control.")
    kept = text_lines.without_internal_control_audit(text)
    assert "标准的无保留意见" in kept and "内部控制审计报告" not in kept
    assert "We conducted our audit." in kept and "internal control" not in kept


def test_the_entitys_name_is_read_off_the_cover_and_the_address_of_the_auditors_report(items):
    """customer_s_name on hkex_0160 and hkex_0191: the legal name is printed on the cover
    ('Hon Kwok Land Investment Company, Limited') and in the report's address ('To the members of
    Lai Sun Garment (International) Limited'); the request carried the contents and the
    corporate-information page, which print neither."""
    from app.core.models.enums import PageKind

    key = "statement_setup_controls__customer_s_name"
    pages = [(0, "Stock Code: 160\nHon Kwok Land Investment Company, Limited\nAnnual Report 2023/24\n"),
             (1, "CONTENTS\nCorporate Information 2\nIndependent Auditor's Report 50"),
             (2, "CORPORATE INFORMATION\nDIRECTORS\nJames Sing-Wai Wong (Chairman)"),
             (49, "INDEPENDENT AUDITOR'S REPORT\nTo the members of Hon Kwok Land Investment "
                  "Company, Limited"),
             (57, "CONSOLIDATED STATEMENT OF FINANCIAL POSITION\n31 March 2024")]
    doc = SimpleNamespace(pages=[
        PageSource(index=57, kind=PageKind.FACE, statement="balance_sheet",
                   evidence={"matched_title": "CONSOLIDATED STATEMENT OF FINANCIAL POSITION"})])
    blocks = text_lines.text_pages(pages, printed_names(items[key]), doc)
    assert [b["page"] for b in blocks] == [1, 2, 50]
    assert _carries(blocks, "Hon Kwok Land Investment Company, Limited") == [1, 50]


def test_the_scope_word_is_read_off_a_statement_of_comprehensive_income(items):
    """hkex_0857 titles its IFRS income statement 'CONSOLIDATED STATEMENT OF COMPREHENSIVE
    INCOME' (p227): customer_statement_type names it, so that face is anchored."""
    from app.core.models.enums import PageKind

    title = "CONSOLIDATED STATEMENT OF COMPREHENSIVE INCOME"
    doc = SimpleNamespace(pages=[PageSource(index=226, kind=PageKind.FACE,
                                            statement="profit_and_loss",
                                            evidence={"matched_title": title})])
    names = printed_names(items["statement_setup_controls__customer_statement_type"])
    assert text_lines.anchored_pages([(226, title)], names, doc) == {226: 0}


def test_the_comparative_date_definition_says_what_to_give_when_the_statements_print_none(items):
    definition = items["statement_setup_controls__reconcile_to"].definition
    assert "期初余额" in definition and "one year before the statement date" in definition
    assert "cited from that page" in definition


def test_a_text_request_carries_its_pages_and_no_note_or_statement(items):
    key = "statement_setup_controls__accountant"
    plan = RequestPlan(name=key, keys=(key,), notes=())
    blocks = text_lines.text_pages(PAGES, printed_names(items[key]))
    request = line_item_llm.build_text_request(plan, {key: items[key]}, blocks)
    line = request["line_items"][0]
    assert line["key"] == key and line["output"] == "text"
    assert "notes_supplied" not in line and "statement" not in line
    assert set(request) == {"line_items", "text_pages"}
    assert any("Ernst & Young" in b["text"] for b in request["text_pages"])


# ── the reply, and the check against the page ─────────────────────────────────────────────────

def test_the_text_reply_holds_a_text_and_its_page():
    reply = line_item_llm.LineItemTextReply.model_validate_json(json.dumps(
        {"answers": {"items": [{"key": "k", "text": "Ernst & Young", "page": 3}]}}))
    assert reply.answers[0].text == "Ernst & Young" and reply.answers[0].page == 3
    assert "Do NOT state a figure" not in line_item_llm.TEXT_REPLY_CONTRACT


def test_the_figure_reply_is_unchanged_so_no_figure_request_changes():
    fields = set(line_item_llm.LineItemAnswer.model_fields)
    assert fields == {"key", "sources", "role", "signs", "confidence", "reason"}


def test_a_text_is_found_across_line_breaks_and_spacing():
    blocks = [{"page": 3, "text": AUDIT_PAGE}]
    said = ("In our opinion, the consolidated financial statements give a true and fair view of "
            "the consolidated financial position.")
    assert text_lines.locate(said, 3, blocks) == 3
    assert text_lines.locate(said, None, blocks) == 3


def test_a_text_the_cited_page_does_not_print_is_refused():
    blocks = [{"page": 3, "text": AUDIT_PAGE}, {"page": 4, "text": PAGES[3][1]}]
    assert text_lines.locate("KPMG", 3, blocks) is None
    assert text_lines.locate("Registered Public Interest Entity Auditor", 3, blocks) is None
    assert text_lines.locate("Registered Public Interest Entity Auditor", 4, blocks) == 4
    # A page nobody supplied is not where the model read it.
    assert text_lines.locate("Annual Report 2024", 1, blocks) is None


# ── the stage ─────────────────────────────────────────────────────────────────────────────────

class _Provider:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def complete_structured(self, *, system, messages, response_schema, max_tokens):
        self.calls.append({"system": system, "request": json.loads(messages[-1]["content"]),
                           "schema": response_schema})
        return response_schema.model_validate({"answers": self.answers}), {}


def _run_text(items, monkeypatch, answers, keys):
    monkeypatch.setattr("app.services.derived.document_text", lambda data, fmt: PAGES)
    doc = DocumentModel(fmt=DocFormat.PDF, pages=[PageSource(index=i) for i, _ in PAGES])
    ctx = PipelineContext(raw_bytes=b"%PDF")
    provider = _Provider(answers)
    by_key = {k: items[k] for k in keys}
    plans = [RequestPlan(name=k + ":text", keys=(k,), notes=()) for k in keys]
    out = stage.LineItemLlmStage()._ask_text(doc, ctx, provider, plans, by_key, {})
    return doc, ctx, provider, out


def test_the_stage_writes_a_located_text_onto_the_line_with_its_page(items, monkeypatch):
    key = "statement_setup_controls__accountant"
    doc, ctx, provider, (calls, failed, answered) = _run_text(
        items, monkeypatch, [{"key": key, "text": "Ernst & Young", "page": 3,
                              "confidence": 0.95}], [key])
    assert (calls, failed, answered) == (1, 0, 1)
    assert provider.calls[0]["schema"] is line_item_llm.LineItemTextReply
    assert provider.calls[0]["system"] == line_item_llm.TEXT_REPLY_CONTRACT
    assert provider.calls[0]["request"]["text_pages"]
    row = next(li for li in doc.line_items if li.canonical_key == key)
    (ev,) = row.values.values()
    assert ev.value_text == "Ernst & Young" and ev.value is None and ev.value_raw is None
    assert ev.provenance.page_index == 2


def test_the_stage_refuses_a_text_no_supplied_page_prints(items, monkeypatch):
    key = "statement_setup_controls__accountant"
    doc, ctx, _p, (_c, _f, answered) = _run_text(
        items, monkeypatch, [{"key": key, "text": "PricewaterhouseCoopers", "page": 3}], [key])
    assert answered == 0
    # Nothing is written, and no empty row is left to blank the export's own reading of the line.
    assert not [li for li in doc.line_items if li.canonical_key == key]
    assert any("text REFUSED (llm_text_not_on_page)" in line for line in ctx.logs)


def test_a_text_line_is_taken_out_of_a_figure_plan_which_keeps_its_other_lines(items):
    text_key, figure_key = "statement_setup_controls__accountant", "bs_ca__total_assets"
    by_key = {text_key: items[text_key], figure_key: items[figure_key]}
    plan = RequestPlan(name="group", keys=(figure_key, text_key), notes=("7",))
    figures, texts = stage._split_text_plans([plan], by_key)
    assert [p.keys for p in figures] == [(figure_key,)] and figures[0].notes == ("7",)
    assert [p.keys for p in texts] == [(text_key,)] and texts[0].notes == ()
    alone = RequestPlan(name=figure_key, keys=(figure_key,), notes=())
    assert stage._split_text_plans([alone], by_key) == ([alone], [])


# ── the export ────────────────────────────────────────────────────────────────────────────────

def _doc_with_texts(items, monkeypatch, texts: dict[str, tuple[str, int]]):
    answers = [{"key": k, "text": t, "page": p, "confidence": 0.9} for k, (t, p) in texts.items()]
    pages = dict(PAGES)
    pages.update({10: "The presentation currency of these financial statements is Renminbi."})
    monkeypatch.setattr("app.services.derived.document_text",
                        lambda data, fmt: sorted(pages.items()))
    doc = DocumentModel(fmt=DocFormat.PDF, pages=[PageSource(index=i) for i in sorted(pages)],
                        unit_context=UnitContext(currency="CNY", scale_factor=Decimal(1000)))
    ctx = PipelineContext(raw_bytes=b"%PDF")
    by_key = {k: items[k] for k in texts}
    plans = [RequestPlan(name=k, keys=(k,), notes=()) for k in texts]
    stage.LineItemLlmStage()._ask_text(doc, ctx, _Provider(answers), plans, by_key, {})
    return doc


def _supp(doc):
    template = json.loads(HK_TEMPLATE.read_text(encoding="utf-8"))
    rows = _build_supplemental_rows(template_def=template, base_rows=_serialize_rows(doc),
                                    disclosures=[], entity_name="Detected Name", doc_model=doc,
                                    ctx=None, options={})
    return {r["canonical_key"]: r for r in rows}


def test_the_export_writes_the_located_text_on_the_page_it_was_read(items, monkeypatch):
    doc = _doc_with_texts(items, monkeypatch, {
        "statement_setup_controls__accountant": ("Ernst & Young", 3),
        "notes__auditor_s_opinion": ("In our opinion, the consolidated financial statements give "
                                     "a true and fair view of the consolidated financial "
                                     "position.", 3)})
    assert _serialize_rows(doc) == []           # no figure row carries a text
    rows = _supp(doc)
    accountant = rows["statement_setup_controls__accountant"]["values"][0]
    assert accountant["value"] == "Ernst & Young"
    assert accountant["provenance"]["page_index"] == 2
    opinion = rows["notes__auditor_s_opinion"]["values"][0]["value"]
    assert opinion.startswith("In our opinion")


def test_a_controlled_setup_value_stays_and_the_printed_words_are_its_evidence(items, monkeypatch):
    doc = _doc_with_texts(items, monkeypatch, {
        "statement_setup_controls__source_currency": ("Renminbi", 11)})
    value = _supp(doc)["statement_setup_controls__source_currency"]["values"][0]
    assert value["value"] == "CNY"
    assert "printed_as:Renminbi" in value["confidence"]["flags"]
    assert value["provenance"]["page_index"] == 10


def test_without_a_located_text_the_export_says_what_it_said_before(items, monkeypatch):
    doc = _doc_with_texts(items, monkeypatch, {})
    rows = _supp(doc)
    assert rows["statement_setup_controls__customer_s_name"]["values"][0]["value"] == "Detected Name"
    assert not rows["statement_setup_controls__accountant"]["values"]
