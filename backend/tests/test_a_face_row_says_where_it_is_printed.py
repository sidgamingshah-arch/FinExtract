"""A STATEMENT ROW IS SENT UNDER THE SECTION AND HEADING IT IS PRINTED UNDER, BY ITS PRINTED CAPTION.

Blind answers to real requests reported the context a statement row carried as wrong in five ways:

* GRAND TOTALS UNDER THE LAST SECTION. 000883's 资产总计 arrived "under" 非流动资产：, its 负债合计
  under 非流动负债：, Laurus's "Total assets" under "Current assets" — the banner in force is the
  reader's section for the MAPPER, which needs one for every row, and the wrong one to show.
* STALE SUB-HEADINGS. On a face a colon heading stayed the `subheading` of every row until the next
  banner: Far East Horizon's profit before tax under "Share of net profits of:", BYD's total
  comprehensive income under "Receivables financing:", and Lai Sun's whole OCI section under
  "profit or loss in subsequent periods:" — the second line of a wrapped heading, the same for the
  items that may and that will not be reclassified.
* A STATEMENT TITLE AS A SECTION: Hon Kwok's every row "under" CONSOLIDATED STATEMENT OF PROFIT OR LOSS.
* HEADINGS IN CAPTIONS. "Equity attributable to owners of the Company Share capital" (Lai Sun),
  "Equity Equity share capital" (Laurus), "Financial Assets Investments" (Asian Paints); and a bare
  total captioned with the heading above it ("CURRENT ASSETS", "EQUITY", "Attributable to:") as
  though that were printed beside its figure.
* CAS ATTRIBUTION ROWS WITH NO TOTAL. 1.归属于母公司股东的净利润 and 2.少数股东损益 carried no subheading
  naming the 五、净利润 they split.

And the definitions of three balance-sheet totals said they were "reported in the current assets
section" or "the current liabilities section"; several were placeholders, or summarised their
rollup as "… General & Admin Expenses and and 22 more".
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import face_context
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources
from app.services.row_reconstruct import Word, build_line_items

_W, _H = 595.0, 842.0
_CHAR, _SPACE = 4.3, 2.2
_LEFT, _INDENT = 62.0, 72.0
_CUR, _PRI = 425.0, 520.0
_STEP = 12.0

LINE_ITEMS = (pathlib.Path(__file__).resolve().parent.parent
              / "app/sample/templates/output_csv_hk_line_items.json")


# ── the reader, on synthetic face pages ─────────────────────────────────────────────────────────

def _line(text: str, x0: float, y: float) -> list[Word]:
    out, x = [], x0
    for t in text.split():
        out.append(Word(text=t, bbox=BBox(x0=x / _W, y0=y / _H,
                                          x1=(x + _CHAR * len(t)) / _W, y1=(y + 8) / _H)))
        x += _CHAR * len(t) + _SPACE
    return out


def _right(text: str, x1: float, y: float) -> list[Word]:
    return _line(text, x1 - _CHAR * len(text), y)


def _page(*lines) -> list[Word]:
    """`lines` are (caption, x0) or (caption, x0, current, prior), one per printed line."""
    words: list[Word] = []
    y = 120.0
    words += _right("2025", _CUR, 100.0) + _right("2024", _PRI, 100.0)
    for spec in lines:
        caption, x0 = spec[0], spec[1]
        if caption:
            words += _line(caption, x0, y)
        if len(spec) > 2:
            words += _right(spec[2], _CUR, y) + _right(spec[3], _PRI, y)
        y += _STEP
    return words


def _read(statement: str, *lines):
    items, _ = build_line_items(_page(*lines), page_index=4, document_id=None,
                                source_kind="native", statement=statement, on_face=True)
    return [(li.source_label, li.group_hint) for li in items]


def test_a_heading_wrapped_onto_a_second_line_is_the_whole_heading():
    """Lai Sun's OCI: two headings whose second lines read the same."""
    got = dict(_read(
        "profit_and_loss",
        ("OTHER COMPREHENSIVE INCOME/(EXPENSE)", _LEFT),
        ("Other comprehensive income that may be reclassified to", _LEFT),
        ("profit or loss in subsequent periods:", _INDENT),
        ("Exchange realignments", _INDENT, "243,212", "(229,062)"),
        ("Share of other comprehensive income of associates", _INDENT, "6,215", "(1,459)"),
        ("Other comprehensive income that will not be reclassified to", _LEFT),
        ("profit or loss in subsequent periods:", _INDENT),
        ("Surplus on revaluation of properties", _INDENT, "16,491", "7,218"),
        ("OTHER COMPREHENSIVE INCOME FOR THE YEAR", _LEFT, "268,064", "(386,661)"),
        ("TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR", _LEFT, "(2,969,793)", "(4,642,640)"),
    ))
    assert got["Exchange realignments"] == (
        "Other comprehensive income that may be reclassified to profit or loss in subsequent "
        "periods:")
    assert got["Surplus on revaluation of properties"] == (
        "Other comprehensive income that will not be reclassified to profit or loss in "
        "subsequent periods:")
    assert got["OTHER COMPREHENSIVE INCOME FOR THE YEAR"] == ""
    assert got["TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR"] == ""


def test_a_sub_heading_ends_at_the_total_after_its_rows():
    """Far East Horizon: "Share of net profits of:" over two indented rows, then the bottom lines."""
    got = dict(_read(
        "profit_and_loss",
        ("Finance costs", _LEFT, "(1,213,009)", "(1,197,000)"),
        ("Share of net profits of:", _LEFT),
        ("Associates", _INDENT, "170,685", "150,000"),
        ("Joint ventures", _INDENT, "90,227", "80,000"),
        ("PROFIT BEFORE TAX", _LEFT, "8,021,363", "7,900,000"),
        ("Income tax expense", _LEFT, "(3,502,126)", "(3,400,000)"),
        ("PROFIT FOR THE YEAR", _LEFT, "4,519,237", "4,500,000"),
    ))
    assert got["Associates"] == got["Joint ventures"] == "Share of net profits of:"
    assert got["PROFIT BEFORE TAX"] == got["Income tax expense"] == got["PROFIT FOR THE YEAR"] == ""


def test_a_row_set_back_to_the_headings_edge_has_left_its_block():
    """BYD: "Receivables financing:" over two indented rows; the exchange differences, printed at
    the heading's own edge, are not receivables financing."""
    got = dict(_read(
        "profit_and_loss",
        ("Receivables financing:", _LEFT),
        ("Changes in fair value", _INDENT, "(202)", "15,652"),
        ("Reversal of impairment", _INDENT, "(280)", "(100)"),
        ("Exchange differences on translation of foreign operations", _LEFT, "18,063", "10,353"),
    ))
    assert got["Changes in fair value"] == "Receivables financing:"
    assert got["Exchange differences on translation of foreign operations"] == ""


def test_a_heading_over_the_rows_beneath_it_is_their_heading_not_their_caption():
    """Lai Sun's equity block: the heading flush with its rows, and a capitalised row under it."""
    got = _read(
        "balance_sheet",
        ("EQUITY", _LEFT),
        ("Equity attributable to owners of the Company", _LEFT),
        ("Share capital", _LEFT, "2,178,944", "2,178,944"),
        ("Reserves", _LEFT, "12,207,475", "13,782,603"),
    )
    assert ("Share capital", "Equity attributable to owners of the Company") in got, got
    assert not any(c.startswith("Equity attributable") for c, _g in got), got


def test_a_one_word_banner_over_a_caption_of_its_own_is_a_banner():
    """Laurus / Asian Paints print "Equity" tight above "Equity share capital"; "Equity" over
    "investments designated at FVOCI" is that caption's first word, and stays one."""
    tight = 9.0
    words = (_right("2025", _CUR, 100.0) + _line("Equity", _LEFT, 120.0)
             + _line("Equity share capital", _INDENT, 120.0 + tight)
             + _right("107.97", _CUR, 120.0 + tight) + _right("107.85", _PRI, 120.0 + tight)
             + _line("Equity", _LEFT, 160.0)
             + _line("investments designated at FVOCI", _INDENT, 160.0 + tight)
             + _right("50.70", _CUR, 160.0 + tight) + _right("40.10", _PRI, 160.0 + tight))
    items, _ = build_line_items(words, page_index=4, document_id=None, source_kind="native",
                                statement="balance_sheet", on_face=True)
    captions = [li.source_label for li in items]
    assert "Equity share capital" in captions, captions
    assert any("investments designated at FVOCI" in c and c.startswith("Equity")
               for c in captions), captions


def test_a_joined_financial_assets_heading_is_shown_as_the_rows_heading():
    """Asian Paints' "Financial Assets Investments", where it still reaches the request joined: the
    face reader now splits the heading off itself (tests/test_schedule_iii_financial_headings.py),
    and a row it still joins is shown by its own caption under its heading."""
    doc = _doc("balance_sheet", [
        _row("Financial Assets Investments", "119.32", banner="Non-Current Assets"),
        _row("Other Financial Assets", "1523.85", banner="Non-Current Assets"),
    ])
    sent = _sent(doc, "balance_sheet")
    assert sent["Investments"] == ("Non-Current Assets", "Financial Assets")
    assert sent["Other Financial Assets"] == ("Non-Current Assets", None)
    res, unres = resolve_sources([SourceRef(statement="balance_sheet", caption="Investments")],
                                 [], face_context.face_index(doc))
    assert not unres and res[0]["figures"]


# ── the request, on a synthetic document ────────────────────────────────────────────────────────

def _row(caption, current, *, page=5, banner=None, sub=None, borrowed=False):
    li = LineItem(source_label=caption, caption_borrowed=borrowed)
    if banner:
        li.section_hint = banner
    if sub:
        li.group_hint = sub
    li.values["current"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                          value=Decimal(current), value_raw=Decimal(current),
                                          provenance=Provenance(page_index=page))
    return li


def _doc(statement, rows, title=None):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=5, kind=PageKind.FACE, statement=statement,
                            evidence={"matched_title": title} if title else {})]
    doc.line_items = list(rows)
    return doc


def _sent(doc, statement):
    return {r["caption"]: (r.get("under"), r.get("subheading"))
            for r in face_context.face_rows(doc, {statement})[0]["rows"]}


def _cas_balance_sheet():
    return _doc("balance_sheet", [
        _row("货币资金", "1", banner="流动资产："),
        _row("流动资产合计", "1", banner="流动资产："),
        _row("固定资产", "2", banner="非流动资产："),
        _row("非流动资产合计", "2", banner="非流动资产："),
        _row("资产总计", "3", banner="非流动资产："),
        _row("短期借款", "1", banner="流动负债："),
        _row("长期借款", "1", banner="非流动负债："),
        _row("非流动负债合计", "1", banner="非流动负债："),
        _row("负债合计", "2", banner="非流动负债："),
    ])


def test_a_grand_total_is_under_no_section():
    sent = _sent(_cas_balance_sheet(), "balance_sheet")
    assert sent["资产总计"] == (None, None)
    assert sent["负债合计"] == (None, None)
    # A section's own total is printed in it, and the rows before it keep it.
    assert sent["非流动资产合计"] == ("非流动资产：", None)
    assert sent["固定资产"] == ("非流动资产：", None)


def test_the_rows_after_a_sections_total_are_not_in_it():
    doc = _doc("balance_sheet", [
        _row("Inventories", "1", banner="CURRENT ASSETS"),
        _row("Total current assets", "1", banner="CURRENT ASSETS"),
        _row("Trade payables", "1", banner="CURRENT LIABILITIES"),
        _row("Total current liabilities", "1", banner="CURRENT LIABILITIES"),
        _row("NET CURRENT ASSETS", "0", banner="CURRENT LIABILITIES"),
        _row("TOTAL ASSETS LESS CURRENT LIABILITIES", "5", banner="CURRENT LIABILITIES"),
        _row("Total assets", "9", banner="Current assets"),
    ])
    sent = _sent(doc, "balance_sheet")
    assert sent["Total current liabilities"][0] == "CURRENT LIABILITIES"
    assert sent["NET CURRENT ASSETS"][0] is None
    assert sent["TOTAL ASSETS LESS CURRENT LIABILITIES"][0] is None
    assert sent["Total assets"][0] is None


def test_a_statement_title_is_not_a_section():
    doc = _doc("profit_and_loss", [
        _row("REVENUE", "1", banner="CONSOLIDATED STATEMENT OF PROFIT OR LOSS"),
        _row("PROFIT FOR THE YEAR", "1", banner="CONSOLIDATED STATEMENT OF PROFIT OR LOSS"),
    ])
    assert _sent(doc, "profit_and_loss")["REVENUE"] == (None, None)


def test_a_bottom_line_closes_the_section_above_it():
    """PetroChina: profit before tax, its tax and the profit for the year "under" FINANCE COSTS."""
    doc = _doc("profit_and_loss", [
        _row("Interest expense", "-5", banner="FINANCE COSTS"),
        _row("PROFIT BEFORE INCOME TAX EXPENSE", "10", banner="FINANCE COSTS"),
        _row("INCOME TAX EXPENSE", "-2", banner="FINANCE COSTS"),
    ])
    sent = _sent(doc, "profit_and_loss")
    assert sent["Interest expense"][0] == "FINANCE COSTS"
    assert sent["PROFIT BEFORE INCOME TAX EXPENSE"][0] is None
    assert sent["INCOME TAX EXPENSE"][0] is None


def test_a_bare_total_says_it_prints_no_caption():
    doc = _doc("balance_sheet", [
        _row("Cash and cash equivalents", "3026571", banner="CURRENT ASSETS"),
        _row("CURRENT ASSETS", "13991585", banner="CURRENT ASSETS", borrowed=True),
        _row("Total current assets", "13991585", banner="CURRENT ASSETS"),
    ])
    sent = _sent(doc, "balance_sheet")
    assert "CURRENT ASSETS" not in sent
    assert "CURRENT ASSETS (total, printed without a caption)" in sent
    # …and a citation copying the caption it was shown resolves to that row.
    face = face_context.face_index(doc)
    res, unres = resolve_sources(
        [SourceRef(statement="balance_sheet",
                   caption="CURRENT ASSETS (total, printed without a caption)")], [], face)
    assert not unres and res[0]["figures"]


def test_a_cas_attribution_row_names_the_net_profit_it_splits():
    doc = _doc("profit_and_loss", [
        _row("五、净利润（净亏损以“－”号填列）", "2219"),
        _row("1.持续经营净利润（净亏损以“－”号填列）", "2219"),
        _row("1.归属于母公司股东的净利润", "1814"),
        _row("2.少数股东损益", "405"),
        _row("六、其他综合收益的税后净额", "205"),
        _row("归属母公司所有者的其他综合收益的税后净额", "171"),
        _row("七、综合收益总额", "2425"),
        _row("归属于母公司所有者的综合收益总额", "1985"),
    ])
    sent = _sent(doc, "profit_and_loss")
    assert sent["1.归属于母公司股东的净利润"][1] == "五、净利润（净亏损以“－”号填列）"
    assert sent["2.少数股东损益"][1] == "五、净利润（净亏损以“－”号填列）"
    assert sent["归属于母公司所有者的综合收益总额"][1] == "七、综合收益总额"
    assert sent["归属母公司所有者的其他综合收益的税后净额"][1] is None
    # A citation naming that heading in `group` resolves.
    res, unres = resolve_sources(
        [SourceRef(statement="profit_and_loss", caption="1.归属于母公司股东的净利润",
                   group="五、净利润（净亏损以“－”号填列）")], [], face_context.face_index(doc))
    assert not unres and res[0]["figures"]


def test_a_citation_naming_the_section_it_was_shown_resolves():
    doc = _doc("balance_sheet", [
        _row("Borrowings", "100", banner="NON-CURRENT LIABILITIES"),
        _row("Borrowings", "40", banner="CURRENT LIABILITIES"),
    ])
    res, _ = resolve_sources([SourceRef(statement="balance_sheet", caption="Borrowings",
                                        group="CURRENT LIABILITIES")], [],
                             face_context.face_index(doc))
    assert res and str(next(iter(res[0]["figures"].values()))) == "40"


# ── the definitions ─────────────────────────────────────────────────────────────────────────────

def _defs():
    items = json.loads(LINE_ITEMS.read_text(encoding="utf-8"))["items"]
    return {i["key"]: i.get("definition") or "" for i in items}


def test_a_grand_total_is_not_defined_as_in_a_current_section():
    d = _defs()
    for key in ("bs_ca__total_assets", "bs_cl__total_liabilities",
                "bs_cl__total_equity_and_liabilities"):
        assert "current assets section" not in d[key], key
        assert "current liabilities section" not in d[key], key


def test_no_definition_summarises_its_rollup_with_a_doubled_and():
    assert not [k for k, v in _defs().items() if " and and " in v]


def test_the_lines_the_blind_answers_named_say_what_they_are():
    d = _defs()
    for key in ("is_pl__interest_expense_capitalised", "is_pl__personnel_and_benefit_expenses",
                "is_pl__other_non_income_related_taxes"):
        assert not d[key].startswith("Extract the reported value for"), key
    assert "资本化" in d["is_pl__interest_expense_capitalised"]
    assert "directors" in d["is_pl__personnel_and_benefit_expenses"]
    assert "税金及附加" in d["is_pl__other_non_income_related_taxes"]
