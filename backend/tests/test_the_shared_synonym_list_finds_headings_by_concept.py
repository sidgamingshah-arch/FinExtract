"""THE SHARED SYNONYM LIST (`services.heading_synonyms`): a note headed in another spelling of the
line's own concept is added to its request, after everything the configuration found.

Pinned here:
  * a line takes a concept only when one of its terms IS a spelling (measure word set aside), never
    when a term merely contains one;
  * a heading prints a spelling as a phrase, a short spelling only as the whole heading, and the
    longest spelling a heading prints decides its concept;
  * a sibling part's spelling is never handed to the line, and a concept two parts share gives
    each only its own spellings;
  * the notes it adds come after the configuration's, at most `SYNONYM_CAP`, marked
    `via="synonym"`, and never a policy note, a piece printing no figure for a row-reading line,
    another entity's note or a heading the line's `note_terms_none` refuses;
  * `extraction.llm_note_synonyms` off gives the sets exactly as before.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services import heading_synonyms as hs
from app.services import line_item_notes as lin
from app.services import line_item_requests

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def extras(shipped):
    return hs.extras_by_key(shipped)


def _note(number, title, rows=(("Closing balance", "100"),), *, chapter=""):
    table = NotesTable(note_number=number, title=title, page_index=9, chapter_title=chapter)
    for caption, amount in rows:
        row = NoteItem(raw_label=caption, note_number=number)
        if amount is not None:
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                         value=Decimal(amount), value_raw=Decimal(amount),
                                         provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


def _item(shipped, key):
    return next(i for i in shipped.items if i.key == key)


def _groups(*terms):
    return [hs.GROUPS[g][0] for g in hs.named_groups(terms)]


# ── WHICH CONCEPT A LINE NAMES ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("term, group", [
    ("Fixed assets", "property, plant and equipment"),
    ("Property, Plant and Equipment", "property, plant and equipment"),
    ("物業、廠房及設備", "property, plant and equipment"),
    ("Net Trade Receivables", "trade receivables"),           # the measure word set aside
    ("无形资产净额", "intangible assets"),
    ("Short-term borrowings", "short-term borrowings"),
])
def test_a_term_that_is_a_spelling_names_its_concept(term, group):
    assert _groups(term) == [group]


@pytest.mark.parametrize("term", [
    "Profit before income tax",              # holds "income tax"
    "Insurance contract liabilities",        # holds "contract liabilities" — its own group
    "向中央银行借款",                          # holds 银行借款
    "Interest on lease liabilities",         # holds "lease liabilities"
    "Due from related parties",
])
def test_a_term_that_only_contains_a_spelling_names_none_of_its_concepts(term):
    assert "income tax" not in _groups(term)
    assert "contract liabilities" not in _groups(term)
    assert "bank borrowings" not in _groups(term)
    assert "lease liabilities" not in _groups(term)
    assert "related party transactions" not in _groups(term)


# ── WHAT A HEADING PRINTS ───────────────────────────────────────────────────────────────────

def _printed(heading):
    return sorted({hs.GROUPS[s.group][0] for s in hs.printed(heading)})


@pytest.mark.parametrize("heading, groups", [
    ("PROPERTY, PLANT AND EQUIPMENT 13. 物業、廠房及設備", ["property, plant and equipment"]),
    ("、固定资产", ["property, plant and equipment"]),
    ("INVESTMENT PROPERTIES (Continued)", ["investment properties"]),     # plural folded
    ("INVENTORIES (Continued) 21. 存貨（續）", ["inventories"]),           # short, whole heading
    ("、应收票据及应收账款", ["bills receivable", "trade receivables"]),
    # THE LONGEST SPELLING DECIDES: 递延所得税 holds 所得税, 保险合同负债 holds 合同负债,
    # 其他應收賬款 (BYD's spelling of other receivables) holds 應收賬款.
    ("递延所得税资产/递延所得税负债", ["deferred tax"]),
    ("所得税费用表", ["income tax"]),
    ("保险合同负债", ["insurance contract liabilities"]),
    ("应付职工薪酬", ["employee benefits payable"]),
    ("PREPAYMENTS, OTHER RECEIVABLES AND 15. 預付款項、其他應收賬款及其他資 OTHER ASSETS 產",
     ["other receivables", "prepayments"]),
    # A SHORT SPELLING IS THE WHOLE HEADING OR NOTHING.
    ("DEFERRED REVENUE", []),
    ("长期借款分类", ["long-term borrowings"]),
    ("、其他应付款", ["other payables"]),
])
def test_a_heading_prints_a_spelling_as_a_phrase_and_the_longest_decides(heading, groups):
    assert _printed(heading) == groups


# ── THE SIBLING RULE ────────────────────────────────────────────────────────────────────────

def test_a_part_is_never_handed_a_spelling_its_sibling_part_names(extras):
    ppe = set(extras["sub__ppe_depreciation"])
    fixed = set(extras["sub__fixed_asset_depreciation"])
    assert "property and equipment" in ppe and "固定资产" not in ppe and "fixed assets" not in ppe
    assert "固定资产" in fixed and "property and equipment" not in fixed
    # A CONCEPT BOTH PARTS NAME GIVES EACH ONLY ITS OWN SPELLINGS: neither is handed the
    # comma-spelled whole, which neither of them wrote down.
    assert "property, plant and equipment" not in ppe | fixed


def test_a_longer_sibling_term_is_not_a_claim_on_a_heading(shipped):
    """"Depreciation of property, plant and equipment" is a part's term about its own line."""
    got = hs.spellings_for(["property and equipment"],
                           owned=["depreciation of property, plant and equipment"])
    assert "property and equipment" in got and "property, plant and equipment" in got


# ── WHAT IT ADDS TO A LINE'S NOTES ──────────────────────────────────────────────────────────

CAS_RECEIVABLES = [
    _note("五、11", "应收账款", chapter="重要会计政策及会计估计"),
    _note("七、5", "按账龄披露", [("1年以内", "300"), ("1至2年", "40")], chapter="合并财务报表项目注释"),
    _note("七、5", "本期实际核销的应收账款情况", [("核销", "2")], chapter="合并财务报表项目注释"),
    _note("七、9", "存货", chapter="合并财务报表项目注释"),
]


def test_a_line_configured_in_english_reaches_the_cas_receivables_note(shipped, extras):
    item = _item(shipped, "bs_ca__net_trade_receivables")
    before = lin.note_sets([item], CAS_RECEIVABLES).get(item.key, [])
    stats: dict = {}
    after = lin.note_sets([item], CAS_RECEIVABLES, synonyms=extras, stats=stats)[item.key]
    assert [h.note for h in after[:len(before)]] == [h.note for h in before]
    added = [h for h in after if h.via == "synonym"]
    # THE DISCLOSURE, NOT THE POLICY: 五、11 prints 应收账款 under 重要会计政策及会计估计.
    assert [h.note for h in added] == ["七、5"]
    assert added[0].title == "本期实际核销的应收账款情况"
    assert stats["synonym"] == 1


def test_fixed_assets_are_found_for_a_line_that_says_property_plant_and_equipment(shipped):
    item = NS(key="x__ppe", label="Property, plant and equipment", note_source=None,
              aliases=[], aliases_i18n={}, route="note_tables", type="extracted",
              value_scope="", extraction_mode="extract", parent="", cascade=[], terms=[])
    notes = [_note("12", "FIXED ASSETS", [("Cost at 1 January", "900")]),
             _note("13", "INTANGIBLE ASSETS", [("Cost", "50")])]
    extras = {"x__ppe": tuple(hs.spellings_for(["Property, plant and equipment"]))}
    got = lin.note_sets([item], notes, synonyms=extras)
    assert [(h.note, h.via) for h in got["x__ppe"]] == [("12", "synonym")]


def test_the_additions_are_capped_and_ranked_by_the_figures_they_print(shipped, extras):
    item = _item(shipped, "bs_ca__net_trade_receivables")
    notes = [_note("七、5", "应收账款", [("a", "1")]),
             _note("十九、1", "应收账款", [("a", "1"), ("b", "2"), ("c", "3")]),
             _note("十九、9", "应收账款", [("a", "1"), ("b", "2")])]
    got = [h for h in lin.note_sets([item], notes, synonyms=extras)[item.key]
           if h.via == "synonym"]
    assert len(got) == lin.SYNONYM_CAP == 2
    assert [h.note for h in got] == ["十九、1", "十九、9"]


def test_a_row_reading_line_is_not_given_a_piece_that_prints_no_figure(shipped, extras):
    item = _item(shipped, "sub__finance_expense_note_exchange")    # route: note_tables
    figures = _note("8", "FINANCE COSTS 8. 融資成本", [("Interest on bank loans", "70")])
    words = _note("8", "FINANCE COSTS 8. 融資成本", [("Finance costs comprise", None)])
    other = _note("9", "INCOME TAX", [("Current tax", "5")])
    got = lin.note_sets([item], [figures, other], synonyms=extras)[item.key]
    assert ("8", "synonym") in [(h.note, h.via) for h in got]
    got = lin.note_sets([item], [words, other], synonyms=extras).get(item.key, [])
    assert "8" not in [h.note for h in got]


def test_another_entitys_note_is_not_added(shipped, extras):
    item = _item(shipped, "bs_ca__net_trade_receivables")
    notes = [_note("17", "TRADE RECEIVABLES OF ASSOCIATES", [("Due", "10")])]
    got = lin.note_sets([item], notes, synonyms=extras).get(item.key, [])
    assert not [h for h in got if h.via == "synonym"]


# ── THE SWITCH ──────────────────────────────────────────────────────────────────────────────

def test_the_switch_off_plans_exactly_what_the_configuration_alone_finds(shipped):
    from app.config import get_settings
    settings = get_settings()
    was = settings.extraction.llm_note_synonyms
    try:
        settings.extraction.llm_note_synonyms = False
        off = line_item_requests.plan_requests(shipped, CAS_RECEIVABLES, settings)
        settings.extraction.llm_note_synonyms = True
        on = line_item_requests.plan_requests(shipped, CAS_RECEIVABLES, settings)
    finally:
        settings.extraction.llm_note_synonyms = was
    off_notes = {k: p.notes for p in off for k in p.keys}
    on_notes = {k: p.notes for p in on for k in p.keys}
    assert off_notes["bs_ca__net_trade_receivables"] == ()
    assert on_notes["bs_ca__net_trade_receivables"] == ("七、5",)
    # EVERY OTHER LINE KEEPS ITS NOTES, IN ORDER, AS A PREFIX.
    for key, notes in off_notes.items():
        assert on_notes[key][:len(notes)] == notes, key


def test_the_setting_ships_on():
    from app.config import ExtractionSettings
    assert ExtractionSettings().llm_note_synonyms is True


def test_a_synonym_never_brings_a_part_the_note_its_sibling_claims(shipped):
    """Whatever spelling a part is given, a note the deterministic claims give to a sibling part
    of the same figure is not added to it."""
    ppe = _item(shipped, "sub__ppe_depreciation")
    fixed = _item(shipped, "sub__fixed_asset_depreciation")
    notes = [_note("14", "PROPERTY AND EQUIPMENT", [("Depreciation", "70")]),
             _note("七、21", "、固定资产", [("本期计提", "30")], chapter="合并财务报表项目注释")]
    # A spelling list that WOULD reach the sibling's note, to show the fence and not the list.
    reach = {ppe.key: ("property and equipment", "固定资产"),
             fixed.key: ("fixed assets", "property and equipment")}
    stats: dict = {}
    got = lin.note_sets([ppe, fixed], notes, synonyms=reach, stats=stats)
    assert "七、21" not in [h.note for h in got.get(ppe.key, [])]
    assert "14" not in [h.note for h in got.get(fixed.key, [])]
    assert stats["sibling_refused"] >= 1
