"""A Securities (CP) part is the sum of its note's CURRENT line items, less derivatives and
other receivables — found by meaning, read by four vetoes, with no Find 2 to subtract afterwards.

The rule, as asked: "Sum of all current financial assets which are HTM or AFS excluding derivatives
and other receivables". The deterministic reader now says the same thing the prompt does, which is
what let the three deduction parts go.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services.note_sourced import resolve, select_rows
from app.services.row_reconstruct import Word, build_line_items

SEED = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_line_items.json"
FVTPL = "sub__fa_cp_fvtpl_note_total"


@pytest.fixture(scope="module")
def by_key():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in st.items}


def _row(caption, current=None, prior=None, group="", hint=""):
    row = NoteItem(raw_label=caption, note_number="七、2", group_hint=group)
    if hint:
        row.period_hint = hint
    for label, amount in (("current", current), ("prior", prior)):
        if amount is not None:
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label,
                                         value=Decimal(amount), provenance=Provenance(page_index=1)))
    return row


def _table(title, rows, number="七、2"):
    t = NotesTable(note_number=number, title=title, page_index=1)
    t.items.extend(rows)
    return t


def _sum(item, tables):
    got = resolve(select_rows(item, tables, {"current", "prior"}), "sum")
    return {period: str(amount) for (_b, period), (amount, _t) in got.items()}


def test_300319s_fvtpl_note_is_its_line_items_less_the_forward_contract(by_key):
    """300319's 交易性金融资产 note, as printed: a category row, its 其中 breakdown — one line of which
    is a forward FX contract — and 合计. The breakdown is read, the forward drops out, and the prior
    year is 180,559,509.03 rather than the 181,124,711.32 the note totals."""
    note = _table("、交易性金融资产", [
        _row("以公允价值计量且其变动计入当期损益的金融资产", "431478888.81", "181124711.32"),
        _row("结构性理财产品", "373011600.33", "130559509.03", group="其中："),
        _row("股权投资", "58467288.48", "50000000.00", group="其中："),
        _row("远期结售汇", None, "565202.29", group="其中："),
        _row("合计", "431478888.81", "181124711.32", group="其中："),
    ])
    assert _sum(by_key[FVTPL], [note]) == {"current": "431478888.81", "prior": "180559509.03"}


def test_1966s_note_26_is_its_line_items(by_key):
    note = _table("FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS 按公允值計量且其變動計入損益的金融資產", [
        _row("Listed equity investments, at fair value 上市股本投資，按公允值", None, "53434"),
        _row("Unlisted investments, at fair value 非上市投資，按公允值", "344135", "378539"),
        _row("Total 總計", "344135", "431973"),
    ], number="26")
    assert _sum(by_key[FVTPL], [note]) == {"current": "344135", "prior": "431973"}


def test_other_receivables_are_left_out_and_the_non_current_portion_taken_out(by_key):
    """The part is "only the current portion — not the non-current portion" (its definition). A
    note that prints "Less: non-current portion (100)" says 100 of these holdings are non-current,
    so the current part is its line items less that row: 900 + 20 − 100. It read 920 while the row
    was only vetoed, which counted the non-current 100 on both sides of the balance sheet."""
    note = _table("Financial assets at fair value through profit or loss", [
        _row("Unlisted fund investments", "900"),
        _row("Other receivables", "50"),
        _row("Derivative financial instruments", "30"),
        _row("Structured deposits with embedded derivatives", "20"),
        _row("Less: non-current portion", "-100"),
    ], number="26")
    assert _sum(by_key[FVTPL], [note]) == {"current": "820"}


def test_a_table_under_the_same_number_about_something_else_is_not_read(by_key):
    """300319's 七、2 also carries an ageing table whose own note heading was not recognised."""
    note = _table("、交易性金融资产", [_row("股权投资", "100")])
    between = _table("应收票据分类列示", [_row("银行承兑票据", "5")], number="七、3")
    ageing = _table("按账龄披露", [_row("1 年以内（含1 年）", "14100819.85")])
    assert _sum(by_key[FVTPL], [note, between, ageing]) == {"current": "100"}


def test_a_note_whose_tables_carry_generic_titles_is_read_through_its_heading(by_key):
    """A related-party note prints its subject once and its tables as （1）应收项目 / （2）应付项目:
    the run the heading opens is read, table titles notwithstanding."""
    heading = _table("交易性金融资产", [])
    table = _table("（1）分类列示", [_row("结构性存款", "300")])
    assert _sum(by_key[FVTPL], [heading, table]) == {"current": "300"}


def test_a_movement_table_is_not_a_list_of_line_items(by_key):
    moves = _table("Financial assets at fair value through profit or loss", [
        _row("At 1 January", "378539", hint="current"),
        _row("Disposal", "-9208", hint="current"),
        _row("At 31 December", "344135", hint="current"),
    ], number="26")
    assert _sum(by_key[FVTPL], [moves]) == {}


def test_a_note_about_fvtpl_liabilities_is_not_found_by_meaning(by_key):
    note = _table("交易性金融负债", [_row("衍生金融负债", "100")], number="七、33")
    assert _sum(by_key[FVTPL], [note]) == {}


def test_the_page_header_year_is_not_a_row():
    """"澜起科技股份有限公司 2024 年年度报告" on every 688008 notes page: read as a row, its year
    became a 2,024 line item inside the note beneath it."""
    h = 0.0107

    def w(t, x0, x1, y):
        return Word(text=t, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + h))

    words = [w("澜起科技股份有限公司", 0.109, 0.28, 0.053), w("2024", 0.736, 0.768, 0.050),
             w("年年度报告", 0.77, 0.86, 0.053),
             w("项目", 0.2, 0.235, 0.10), w("期末余额", 0.45, 0.52, 0.10), w("期初余额", 0.70, 0.77, 0.10),
             w("其他债权投资", 0.107, 0.2, 0.12), w("1,000.00", 0.46, 0.52, 0.12),
             w("2,000.00", 0.72, 0.78, 0.12)]
    items, _ = build_line_items(words, page_index=1, document_id="d", source_kind="native",
                                on_face=False)
    assert [(li.source_label, {v.period_label: str(v.value) for v in li.values.values()})
            for li in items] == [("其他债权投资", {"current": "1000.00", "prior": "2000.00"})]


def test_the_prompt_is_the_simple_one(by_key):
    d = by_key["sub__fa_cp_afs_htm_note_total"].definition
    assert d.startswith("Sum of all current financial assets which are available-for-sale (AFS) or "
                        "held-to-maturity (HTM), excluding derivatives and other receivables.")
    assert "note total" not in d.lower() and "non-current portion" in d


# ── the non-current twin ──────────────────────────────────────────────────────────────────────

def test_688008s_other_non_current_financial_assets_note_is_its_line_items(by_key):
    note = _table("其他非流动金融资产", [
        _row("非上市股权投资", "531795561.02", "534338138.95"),
        _row("私募基金投资", "43448364.95", "48529611.96"),
        _row("合计", "575243925.97", "582867750.91"),
    ], number="七、19")
    assert _sum(by_key["sub__ltp_other_fincl_assets_note_total"], [note]) == {
        "current": "575243925.97", "prior": "582867750.91"}


def test_a_non_current_part_leaves_out_associates_and_takes_out_the_current_portion(by_key):
    """"Only the non-current portion — not the current portion": 700 less the current 100."""
    note = _table("Financial assets at fair value through profit or loss", [
        _row("Unlisted equity investments", "700"),
        _row("Investment in an associate designated at FVTPL", "90"),
        _row("Amounts due from related parties", "40"),
        _row("Other receivables", "10"),
        _row("Current portion", "-100"),
    ], number="18")
    assert _sum(by_key["sub__ltp_fvtpl_note_total"], [note]) == {"current": "600"}


LAISUN_26 = [("Listed investments (i)", "36071", "70948"),
             ("Unlisted investments (ii)", "927258", "941274"),
             ("Less: Portion classified as current", "-174822", "-70948"),
             ("Non-current portion", "788507", "941274")]


def test_a_note_that_splits_its_holdings_is_read_by_each_side(by_key):
    """Lai Sun's note 26 prints the FVTPL holdings gross, then the current portion and the
    non-current portion. Both parts read the gross 963,329 / 1,012,222 — the current and the
    non-current line each counted all of it. Each reads its own split row now, and the two are
    the figures the face prints under current and non-current assets."""
    note = _table("FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS",
                  [_row(c, cur, pri) for c, cur, pri in LAISUN_26], number="26")
    assert _sum(by_key[FVTPL], [note]) == {"current": "174822", "prior": "70948"}
    assert _sum(by_key["sub__ltp_fvtpl_note_total"], [note]) == {"current": "788507",
                                                                 "prior": "941274"}


def test_a_note_printing_only_the_current_split_gives_the_non_current_part_the_rest(by_key):
    note = _table("FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS",
                  [_row(c, cur, pri) for c, cur, pri in LAISUN_26[:3]], number="26")
    assert _sum(by_key["sub__ltp_fvtpl_note_total"], [note]) == {"current": "788507",
                                                                 "prior": "941274"}


def test_the_other_sides_split_alone_never_reads_as_a_negative_holding(by_key):
    """Every line item vetoed, the split row is all that is left — and that is not a reading."""
    note = _table("Financial assets at fair value through profit or loss", [
        _row("Derivative financial instruments", "30"),
        _row("Less: Portion classified as current", "-30"),
    ], number="26")
    assert _sum(by_key["sub__ltp_fvtpl_note_total"], [note]) == {}


def test_other_receivables_are_not_read_off_an_allowance_movement_schedule(by_key):
    """Lai Sun's note 28 prints the movement in the impairment allowance under a heading that
    wraps to "...other receivables and other assets are as follows:"; matched as the group, its
    "At the beginning of the reporting period 159,571" was read as the company's other
    receivables. A plain other-receivables row is still read."""
    group = "other receivables and other assets are as follows:"
    moves = _table("DEPOSITS, PREPAYMENTS, OTHER RECEIVABLES AND OTHER ASSETS", [
        _row("At the beginning of the reporting period", "159571", "147924", group=group),
        _row("Impairment loss recognised", "12804", "17746", group=group),
        _row("At the end of the reporting period", "167312", "159571", group=group),
    ], number="28")
    part = by_key["sub__cp_other_receivables_net_row"]
    assert _sum(part, [moves]) == {}
    plain = _table("OTHER RECEIVABLES", [_row("Other receivables", "4444", "932")], number="18")
    assert _sum(part, [plain]) == {"current": "4444", "prior": "932"}


def test_the_cas_fvtoci_equity_caption_is_read_off_the_face(by_key):
    """其他权益工具投资's note prints its movement as COLUMNS and was misread on 688008 and 300319;
    the face row is right on all three mainland filings, so the LTP line reads it there."""
    fvtoci = by_key["sub__ltp_fvtoci_note_total"].note_source.note_terms
    assert "其他权益工具投资" not in fvtoci and "其他權益工具投資" not in fvtoci
    face = by_key["sub__ltp_face_other_equity_instrument_investments"]
    assert face.route == "face" and "其他权益工具投资" in face.aliases
    ltp = by_key["bs_nca__secur_and_other_fincl_assets_ltp"]
    assert [r.id for r in ltp.cascade] == ["LTP_P1", "LTP_P2", "FROM_THE_FACE"]
    assert all(any(t.ref == face.key and t.role == "any_of" for t in r.terms) for r in ltp.cascade)


def _two_sided_note_doc():
    """An Ind AS test filing's note 6: a Non-current table and a Current table under one number,
    each closed by its own Total — the face cites the note from both sections. The reader keeps
    the two as one table and drops their headings."""
    from app.core.models import DocumentModel, PageKind
    from app.core.models.document import PageSource
    from app.core.models.line_item import LineItem, NoteRef
    from app.core.models.enums import LineRole

    note = _table("Other financial assets", [
        _row("Security deposits", "358.20", "336.85"),
        _row("Total", "358.20", "336.85"),
        _row("Interest accrued on fixed deposits", "21.60", "17.85"),
        _row("Insurance claims receivable", "8.75", "12.40"),
        _row("Total", "30.35", "30.25"),
    ], number="6")
    for item in note.items:
        if item.raw_label == "Total":
            item.role = LineRole.TOTAL
    doc = DocumentModel(filename="f1.pdf")
    doc.pages.append(PageSource(index=0, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=1, kind=PageKind.NOTES))

    def face(key, label, cur, pri, hint):
        li = LineItem(source_label=label, canonical_key=key, section_hint=hint)
        for period, v in (("current", cur), ("prior", pri)):
            li.set_value(ExtractedValue(basis=Basis.STANDALONE, period_label=period, value=Decimal(v),
                                        provenance=Provenance(page_index=0)))
        li.note_refs = [NoteRef(raw="6", numbers=["6"])]
        return li

    doc.line_items = [
        face("bs_nca__other_non_current_assets", "(i) Other financial assets", "358.20", "336.85",
             "Non-current assets"),
        # Unclaimed by any line on that filing: its section is the one it was printed in.
        face("engine_unclassified_face__balance_sheet__current_assets__vi_other_financial_assets",
             "(vi) Other financial assets", "30.35", "30.25", "(b) Financial assets"),
    ]
    doc.notes = [note]
    return doc


def test_a_note_printed_as_one_block_per_side_is_read_by_each_side(by_key):
    """The non-current and the current "other financial assets" parts both read 358.20 + 30.35 =
    388.55. Each block's Total is the figure the face prints under one section, so that is whose
    it is: the non-current part reads 358.20, the current part 30.35."""
    from app.services.note_sourced import block_sides

    doc = _two_sided_note_doc()

    class _Set:
        items = list(by_key.values())

    sides = block_sides(doc, _Set)
    got = {}
    for key in ("sub__ltp_other_fincl_assets_note_total", "sub__fa_cp_other_fincl_assets_note_total"):
        hits = select_rows(by_key[key], doc.notes, {"current", "prior"}, None, None, sides)
        got[key] = {period: str(amount) for (_b, period), (amount, _t) in resolve(hits, "sum").items()}
    assert got == {"sub__ltp_other_fincl_assets_note_total": {"current": "358.20", "prior": "336.85"},
                   "sub__fa_cp_other_fincl_assets_note_total": {"current": "30.35", "prior": "30.25"}}


def test_a_block_whose_total_no_face_row_prints_is_read_as_before(by_key):
    from app.services.note_sourced import block_sides

    doc = _two_sided_note_doc()
    doc.line_items[1].set_value(ExtractedValue(basis=Basis.STANDALONE, period_label="current",
                                               value=Decimal("31.00"),
                                               provenance=Provenance(page_index=0)))

    class _Set:
        items = list(by_key.values())

    assert block_sides(doc, _Set) == {}
