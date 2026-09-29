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


def test_other_receivables_and_the_non_current_portion_are_not_line_items_of_it(by_key):
    note = _table("Financial assets at fair value through profit or loss", [
        _row("Unlisted fund investments", "900"),
        _row("Other receivables", "50"),
        _row("Derivative financial instruments", "30"),
        _row("Structured deposits with embedded derivatives", "20"),
        _row("Less: non-current portion", "-100"),
    ], number="26")
    assert _sum(by_key[FVTPL], [note]) == {"current": "920"}


def test_a_table_under_the_same_number_about_something_else_is_not_read(by_key):
    """300319's 七、2 also carries an ageing table whose own note heading was not recognised."""
    note = _table("、交易性金融资产", [_row("股权投资", "100")])
    ageing = _table("按账龄披露", [_row("1 年以内（含1 年）", "14100819.85")])
    assert _sum(by_key[FVTPL], [note, ageing]) == {"current": "100"}


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
