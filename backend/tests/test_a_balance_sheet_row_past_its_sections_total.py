"""A balance-sheet row printed after its section's own total, under the banner the reader carried.

A US balance sheet prints "Current assets:", its rows, "Total current assets" and then the
non-current rows with no banner over them (the liabilities likewise). The reader carries a banner
down the page until another replaces it, so those rows arrive under "Current assets" /
"Current liabilities". Two stages read that stale banner:

* THE MAPPER (`map_ontology`) gated each row against it and refused every non-current line, so the
  10-K template scoped each non-current line to the current section too. `face_context.block_context`
  already closes a section at its own total for the request text; the mapper now reads the same
  close (`map_ontology._balance_sheet_past_total`) — in the OTHER HALF of the same side, not in no
  section: matched in no section, a 10-K's liability 'Deferred income taxes' took the deferred tax
  ASSET line, which shares the caption.
* THE SWEEP (`residual._section_of_row`) placed such a row by the next subtotal below it, across a
  new banner: on a 10-K that prints no "Total liabilities", two long-term liability rows were swept
  into the equity leftover by "Total shareholders' equity". A row past its own section's total now
  stops at the next banner.

Neither reaches an HKEX, CAS or Ind AS face beyond what is pinned here: every section there opens
with its own banner, so the mapper changes only the section a grand or net total is read in (and
those bind the line they bound), and the sweep's walk is unchanged for a row after a grand total.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind, ValueSource
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.face_context import banners_past_their_total, block_context
from app.services.working_view import build_working_view
from app.stages import map_ontology, residual
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
BS = "balance_sheet"

CA, CL, EQ = "Current assets:", "Current liabilities:", "Equity:"


def _set(name):
    return load_line_item_set(json.loads((TEMPLATES / f"output_csv_{name}_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def usgaap():
    return _set("usgaap")


@pytest.fixture(scope="module")
def hk():
    return _set("hk")


def _row(caption, current, prior=None, section=None, page=4, role=LineRole.LINE):
    row = LineItem(source_label=caption, role=role, section_hint=section)
    for period, value in (("current", current), ("prior", prior if prior is not None else current)):
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                     value=Decimal(value), value_raw=Decimal(value),
                                     provenance=Provenance(page_index=page)))
    return row


def _doc(rows, pages=(4,)):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=p, kind=PageKind.FACE, statement=BS) for p in pages]
    doc.line_items = rows
    return doc


def _run(rows, line_set, template, *, sweep=False, pages=(4,)):
    doc = _doc(rows, pages)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(line_set)
    ctx.line_items = line_set
    ctx.template_def = json.loads((TEMPLATES / template).read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    if sweep:
        ResidualStage().run(doc, ctx)
    return {r.source_label + ("" if i == 0 else f"#{i}"): r.canonical_key
            for i, r in [(sum(1 for x in rows[:n] if x.source_label == r.source_label), r)
                         for n, r in enumerate(rows)]}


US_TEMPLATE = "output_csv_usgaap_v1_template.json"
HK_TEMPLATE = "output_csv_hk_v1_template.json"


def _cascade():
    """A 10-K balance sheet with no non-current banner, as Cascade Software prints it (thousands):
    it prints "Total liabilities", and 'Deferred income taxes' on both sides."""
    return [
        _row("Cash and cash equivalents", "38723", "7796", CA),
        _row("Accounts receivable, net", "69905", "56924", CA),
        _row("Other current assets", "25723", "26021", CA),
        _row("Total current assets", "134351", "90741", CA),
        _row("Property and equipment, net", "204966", "135591", CA),
        _row("Deferred income taxes", "1200", "900", CA),
        _row("Other long-term assets", "40565", "36460", CA),
        _row("Total assets", "381082", "263692", CA),
        _row("Accounts payable", "27724", "21996", CL),
        _row("Accrued liabilities", "13709", "12564", CL),
        _row("Total current liabilities", "41433", "34560", CL),
        _row("Long-term debt", "40152", "42688", CL),
        _row("Deferred income taxes", "2835", "2618", CL),
        _row("Operating lease liabilities", "24823", "15497", CL),
        _row("Total liabilities", "109243", "95363", CL),
        _row("Retained earnings", "271839", "168329", "Stockholders’ equity:"),
        _row("Total stockholders’ equity", "271839", "168329", "Stockholders’ equity:"),
        _row("Total liabilities and stockholders’ equity", "381082", "263692",
             "Stockholders’ equity:"),
    ]


# ── the close the request text already reads ─────────────────────────────────────────────────────

def test_a_row_after_its_sections_own_total_is_past_that_banner():
    rows = _cascade()
    past = banners_past_their_total(rows, BS)
    under = [u for u, _s in block_context(rows, BS)]
    labels = [r.source_label for r in rows]
    assert [labels[i] for i, p in enumerate(past) if p == CA] == [
        "Property and equipment, net", "Deferred income taxes", "Other long-term assets"]
    assert [labels[i] for i, p in enumerate(past) if p == CL] == [
        "Long-term debt", "Deferred income taxes", "Operating lease liabilities"]
    # A grand total is in no section, and is not past a total of its own.
    assert past[labels.index("Total assets")] == "" and under[labels.index("Total assets")] == ""
    assert past[labels.index("Total liabilities")] == ""
    # Every row past a total is one the request text shows under no section.
    assert all(under[i] == "" for i, p in enumerate(past) if p)


def test_the_request_text_is_unchanged():
    """`block_context` is read from the same pass; what it returns is what it returned."""
    rows = _cascade()
    assert block_context(rows, BS) == [
        (u, s) for u, s in zip([CA] * 4 + [""] * 4 + [CL] * 3 + [""] * 4
                               + ["Stockholders’ equity:"] * 2 + [""], [""] * 18)]


def test_a_row_after_a_grand_total_is_not_past_a_total_of_its_own():
    """PetroChina's CAS sheet: "Total liabilities", then its equity rows under the carried
    non-current banner (the equity heading glued to the first caption)."""
    ncl = "Non-current liabilities"
    rows = [_row("Long-term borrowings", "98072", section=ncl),
            _row("Total non-current liabilities", "405827", section=ncl),
            _row("Total liabilities", "1043144", section=ncl),
            _row("Capital surplus", "121812", section=ncl),
            _row("Undistributed profits", "982234", section=ncl)]
    assert banners_past_their_total(rows, BS) == [""] * 5


# ── the mapper ───────────────────────────────────────────────────────────────────────────────────

def test_a_10k_non_current_row_binds_its_line_with_its_own_scope_alone(usgaap):
    got = _run(_cascade(), usgaap, US_TEMPLATE)
    assert got["Property and equipment, net"] == "bs_nca__property_plant_and_equipment"
    assert got["Long-term debt"] == "bs_ncl__long_term_debt"
    # The same caption on both sides, told apart by the side the carried banner names.
    assert got["Deferred income taxes"] == "bs_nca__deferred_tax_assets"
    assert got["Deferred income taxes#1"] == "bs_ncl__deferred_tax_liabilities"
    # Printed on both sides by some filers, so an alias of the current AND the non-current line:
    # below the current liabilities' total, as here, it is the non-current one (Cascade prints
    # 24,823 there; Alphabet 2025 prints its 12,744 the same way).
    assert got["Operating lease liabilities"] == "bs_ncl__operating_lease_liabilities"
    # The rows still inside their sections, and the grand totals, bind as before.
    assert got["Accrued liabilities"] == "bs_cl__accrued_liabilities"
    assert got["Total assets"] == "bs_ca__total_assets"
    assert got["Total liabilities"] == "bs_cl__total_liabilities"


def test_under_the_stale_banner_the_non_current_lines_are_refused(usgaap, monkeypatch):
    """The defect, pinned: with the close not read, the carried banner refuses them."""
    monkeypatch.setattr(map_ontology, "_balance_sheet_past_total", lambda doc: {})
    got = _run(_cascade(), usgaap, US_TEMPLATE)
    assert got["Property and equipment, net"] != "bs_nca__property_plant_and_equipment"
    assert got["Long-term debt"] != "bs_ncl__long_term_debt"


def test_in_no_section_the_side_is_lost(usgaap, monkeypatch):
    """Why the row is read in the other half rather than in no section: matched in none, the
    liability 'Deferred income taxes' takes the asset line, which shares the caption."""
    real = map_ontology._balance_sheet_past_total
    monkeypatch.setattr(map_ontology, "_balance_sheet_past_total",
                        lambda doc: {k: None for k in real(doc)})
    got = _run(_cascade(), usgaap, US_TEMPLATE)
    assert got["Deferred income taxes#1"] == "bs_nca__deferred_tax_assets"


def test_the_other_half_is_not_read_twice():
    """An IFRS face prints the non-current half first. A row after "Total current assets" there,
    under the carried banner, is past both halves: it is matched in no section, not as a
    non-current asset."""
    nca, ca = "Non-current assets", "Current assets"
    rows = [_row("Property, plant and equipment", "100", section=nca),
            _row("Total non-current assets", "100", section=nca),
            _row("Inventories", "50", section=ca),
            _row("Total current assets", "50", section=ca),
            _row("Assets classified as held for sale", "7", section=ca),
            _row("TOTAL ASSETS", "157", section=ca)]
    moved = map_ontology._balance_sheet_past_total(_doc(rows))
    assert moved == {id(rows[4]): None, id(rows[5]): None}


def test_a_missed_cas_banner_reads_the_non_current_half(hk):
    """A CAS sheet whose 非流动资产： the reader missed: the rows after 流动资产合计 are the
    non-current assets, which the carried 流动资产： banner refused."""
    ca = "流动资产："
    rows = [_row("货币资金", "318", section=ca), _row("流动资产合计", "654", section=ca),
            _row("长期股权投资", "56", section=ca), _row("固定资产", "1599", section=ca),
            _row("资产总计", "2691", section=ca)]
    got = _run(rows, hk, HK_TEMPLATE)
    assert got["长期股权投资"] == "bs_nca__investments_in_associates"
    assert got["固定资产"] == "bs_nca__net_fixed_assets"
    assert got["资产总计"] == "bs_ca__total_assets"


def _china_sce():
    """China SCE 2023, page index 104 (thousands): each section opens with its own banner, and the
    net totals follow "Total current liabilities" under the carried banner."""
    cl, ncl = "CURRENT LIABILITIES 流動負債", "NON-CURRENT LIABILITIES 非流動負債"
    return [
        _row("Trade and bills payables 貿易應付款項及票據", "11650518", section=cl),
        _row("Interest-bearing bank and other borrowings 計息銀行及其他貸款", "9817976", section=cl),
        _row("Total current liabilities 流動負債總額", "131532808", section=cl),
        _row("NET CURRENT ASSETS/(LIABILITIES) 流動資產╱ （負債） 淨額", "-12870355", section=cl),
        _row("TOTAL ASSETS LESS CURRENT LIABILITIES 總資產減流動負債", "36356879", section=cl),
        _row("Interest-bearing bank and other borrowings 計息銀行及其他貸款", "10275584", section=ncl),
        _row("Lease liabilities 租賃負債", "6381", section=ncl),
        _row("Total non-current liabilities 非流動負債總額", "15874553", section=ncl),
        _row("Net assets 資產淨值", "20482326", section=ncl),
    ]


def _cas():
    """河钢 000709's consolidated sheet, as the CSRC layout prints it."""
    ca, nca, cl, ncl, eq = "流动资产：", "非流动资产：", "流动负债：", "非流动负债：", "所有者权益："
    return [
        _row("货币资金", "31820751348", section=ca), _row("其他流动资产", "5530573519", section=ca),
        _row("流动资产合计", "65399867425", section=ca),
        _row("长期股权投资", "5682202919", section=nca), _row("固定资产", "159955661517", section=nca),
        _row("非流动资产合计", "203703876880", section=nca), _row("资产总计", "269103744306", section=nca),
        _row("短期借款", "70379904166", section=cl), _row("流动负债合计", "146621985558", section=cl),
        _row("长期借款", "36499165549", section=ncl), _row("租赁负债", "2522694328", section=ncl),
        _row("非流动负债合计", "54899380830", section=ncl), _row("负债合计", "201521366389", section=ncl),
        _row("股本", "10337121092", section=eq), _row("未分配利润", "15791100696", section=eq),
        _row("所有者权益合计", "67582377917", section=eq),
        _row("负债和所有者权益总计", "269103744306", section=eq),
    ]


@pytest.mark.parametrize("layout,reached", [
    (_china_sce, ["NET CURRENT ASSETS/(LIABILITIES) 流動資產╱ （負債） 淨額",
                  "TOTAL ASSETS LESS CURRENT LIABILITIES 總資產減流動負債", "Net assets 資產淨值"]),
    (_cas, ["资产总计", "负债合计", "负债和所有者权益总计"]),
])
def test_on_an_hkex_or_cas_face_only_the_grand_totals_are_reached(hk, monkeypatch, layout,
                                                                    reached):
    rows = layout()
    moved = map_ontology._balance_sheet_past_total(_doc(rows))
    assert sorted(r.source_label for r in rows if id(r) in moved) == sorted(reached)
    assert set(moved.values()) == {None}
    after = _run(layout(), hk, HK_TEMPLATE)
    monkeypatch.setattr(map_ontology, "_balance_sheet_past_total", lambda doc: {})
    before = _run(layout(), hk, HK_TEMPLATE)
    assert after == before


# ── the sweep ────────────────────────────────────────────────────────────────────────────────────

def _line(caption, ordinal, key=None, section=None):
    return LineItem(source_label=caption, ordinal=ordinal, role=LineRole.LINE,
                    source=ValueSource.MACHINE, section_hint=section, canonical_key=key)


def _signal_args():
    ordered = [
        _line("Total current liabilities", 0, "bs_cl__total_current_liabilities", CL),
        _line("Long-term debt", 1, "bs_ncl__long_term_debt", CL),
        _line("Asset retirement obligations", 2, None, CL),
        _line("Common stock", 3, "bs_equity__common_stock", EQ),
        _line("Total shareholders' equity", 4, "bs_equity__equity_attributable_to_owners", EQ),
    ]
    placeable = {"bs_cl": ("current_liabilities", BS), "bs_ncl": ("non_current_liabilities", BS),
                 "bs_equity": ("equity", BS)}
    return ordered, {
        "ordered": ordered, "placeable": placeable,
        "section_by_key": {"bs_cl__total_current_liabilities": "bs_cl",
                           "bs_ncl__long_term_debt": "bs_ncl",
                           "bs_equity__common_stock": "bs_equity",
                           "bs_equity__equity_attributable_to_owners": "bs_equity"},
        "subtotal_of": {"bs_cl__total_current_liabilities": "bs_cl",
                        "bs_equity__equity_attributable_to_owners": "bs_equity"},
        "statement": BS, "statement_of": lambda li: BS, "closed_at": {"bs_cl": 0}}


def test_a_row_past_its_total_is_not_placed_across_the_next_banner():
    ordered, args = _signal_args()
    row = ordered[2]
    assert ResidualStage._section_of_row(idx=2, li=row, past_total=frozenset({id(row)}),
                                         **args) == "bs_ncl"


def test_any_other_row_still_walks_down_to_the_next_subtotal():
    """A row after a grand total, or one the reader gave no section: the walk is unchanged."""
    ordered, args = _signal_args()
    row = ordered[2]
    assert ResidualStage._section_of_row(idx=2, li=row, **args) == "bs_equity"
    row.section_hint = None
    assert ResidualStage._section_of_row(idx=2, li=row, past_total=frozenset({id(row)}),
                                         **args) == "bs_equity"


def _bluegate():
    """Bluegate Retail's liabilities (millions): no "Total liabilities", so the equity total is the
    next subtotal below the long-term rows. 'Asset retirement obligations' is a row no line names
    and no word of which the equity leftover refuses."""
    return [
        _row("Accounts payable", "58666", "56812", CL),
        _row("Total current liabilities", "58666", "56812", CL),
        _row("Long-term debt", "33401", "36132", CL),
        _row("Asset retirement obligations", "1287", "1195", CL),
        _row("Common stock", "805", "807", EQ),
        _row("Retained earnings", "101204", "99816", EQ),
        _row("Total Bluegate shareholders’ equity", "102009", "100623", EQ),
        _row("Total equity", "102009", "100623", EQ),
        _row("Total liabilities, redeemable noncontrolling interest, and equity", "195363",
             "194762", EQ),
    ]


def test_a_long_term_row_is_swept_with_the_liabilities_not_into_equity(usgaap):
    got = _run(_bluegate(), usgaap, US_TEMPLATE, sweep=True)
    assert got["Asset retirement obligations"] == "bs_ncl__other_non_current_liabilities"


def test_by_the_next_subtotal_it_went_into_equity(usgaap, monkeypatch):
    """The defect, pinned: walking across the "Equity:" banner, the equity total placed it."""
    monkeypatch.setattr(residual, "_rows_past_their_total", lambda doc: frozenset())
    got = _run(_bluegate(), usgaap, US_TEMPLATE, sweep=True)
    assert got["Asset retirement obligations"] == "bs_equity__other_equity"


def test_a_row_after_a_grand_total_is_still_placed_by_the_subtotal_below(hk):
    """PetroChina's CAS shape: equity rows after "Total liabilities", under the carried
    non-current banner, above a fresh "Equity attributable to equity holders" banner. They are
    above the equity subtotal they are part of, and are swept into equity as before."""
    ncl, eq = "Non-current liabilities", "Equity attributable to equity"
    rows = [_row("Long-term borrowings", "98072", section=ncl),
            _row("Total non-current liabilities", "98072", section=ncl),
            _row("Total liabilities", "98072", section=ncl),
            _row("Capital surplus", "121812", section=ncl),
            _row("Non-controlling interests", "194492", section=eq),
            _row("Total shareholders’ equity", "316304", section=eq)]
    got = _run(rows, hk, HK_TEMPLATE, sweep=True)
    assert got["Capital surplus"] == "bs_equity__other_reserves"
