"""TRADE RECEIVABLES (GROSS) IS THE AMOUNT BEFORE THE LOSS ALLOWANCE, AND ITS NOTE IS FOUND BY ITS NAME.

The 342-definition rewrite (73e1c2b) said of `bs_ca__trade_receivables_gross`: "where the face
prints trade receivables net of the allowance, as it normally does, this line is that printed
figure". The line is the GROSS amount — the template also has 'Allow for Doubtful Accounts(-)', and
Net Trade Receivables is the two added, the allowance carried negative. Blind answers followed the
text and took the face's net 应收账款 on ten CAS filings (19 rows right -> wrong; on 300257 the
rollup then took the allowance off a figure it had already come off).

The same rewrite moved the line's NOTES. With no `note_terms`, `line_item_notes.note_probe` falls
back to `_blended` — label, definition and aliases — and the longer text diluted the cosine on the
CAS receivables headings below the 0.25 floor: 000685 lost 七、5 and 300319 lost 七、4 (the notes
the key's figure is printed in; answer-free coverage 982 -> 979), 000709 / 002273 / 300257 lost
the parent company's receivables note, and the named-row widening brought in notes that merely
print a 应收账款 row (七、31 / 七、57 / 七、44 restricted assets, 九、2 a business combination). The
gross line now carries the vocabulary its neighbour — the allowance, which reads the same note —
already carries, so its notes no longer depend on what its definition says; and a heading that
names a PAYABLES note or a prepayments-and-other-receivables note is refused, which the
allowance's terms alone would let through on BYD (23 TRADE AND BILLS PAYABLES, 15 PREPAYMENTS,
OTHER RECEIVABLES ...).

Also pinned here, from the same review: the two set lines still carrying 'Extract the reported X
from the Balance Sheet', the four ontology mappings older than the set, the closing-cash and
unexplained-cash lines that still said 'financing section' after 4d5fbe9 moved them to
`cf_top_level`, and Purchase PPE's leasehold land / land use rights split.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

from app.schemas.line_items import load_line_item_set
from app.services import line_item_notes

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SET = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
ONTOLOGY = json.loads((TEMPLATES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
ITEMS = {i["key"]: i for i in SET["items"]}
CONCEPTS = {m["canonical_key"]: m for m in ONTOLOGY["mappings"]}
LOADED = {i.key: i for i in load_line_item_set(SET).items}

GROSS = "bs_ca__trade_receivables_gross"
ALLOWANCE = "bs_ca__allow_for_doubtful_accounts"
NET = "bs_ca__net_trade_receivables"


def _d(key: str) -> str:
    return ITEMS[key]["definition"]


# ── what the three lines are ───────────────────────────────────────────────────────────────────

def test_gross_is_before_the_allowance_and_the_face_net_row_is_not_it():
    text = _d(GROSS)
    assert "this line is that printed figure" not in text
    assert "before the loss allowance" in text
    # CAS: the 账面余额 合计 of the receivables breakdown, before 坏账准备 — not its 账面价值.
    for word in ("账面余额", "合计", "坏账准备", "账面价值"):
        assert word in text, word
    assert "net of the allowance, and that net row is not this line" in text
    assert "Where the balance sheet itself prints the gross amount and the allowance as rows of " \
           "their own, this line is the gross row" in text
    assert ITEMS[ALLOWANCE]["label"] in text and ITEMS[NET]["label"] in text
    assert "gross amount less the allowance" in text


def test_the_allowance_is_the_loss_allowance_carried_negative_not_a_placeholder():
    text = _d(ALLOWANCE)
    assert not text.startswith("Extract the reported")
    assert "坏账准备" in text and "loss allowance" in text
    assert "negative figure" in text
    assert ITEMS[GROSS]["label"] in text and ITEMS[NET]["label"] in text
    # A balance, not the year's charge.
    assert ITEMS["is_pl__bad_debt_expense"]["label"] in text


def test_net_trade_receivables_is_the_printed_trade_receivables_row():
    text = _d(NET)
    # It used to be "the subtotal the filing PRINTS as 'Net Trade Receivables'", a caption no
    # filing prints, while the face's trade receivables row — which IS the net figure — was left
    # to no line once the gross line stopped taking it.
    assert "PRINTS as 'Net Trade Receivables'" not in text
    assert "net of the loss allowance" in text and "CAS 应收账款 as printed" in text
    assert ITEMS[GROSS]["label"] in text and ITEMS[ALLOWANCE]["label"] in text
    assert "rollup" in text and "template" in text
    # The rollup the text describes is the one the template declares.
    assert [t["ref"] for t in ITEMS[NET]["terms"]] == [GROSS, ALLOWANCE]


# ── which notes the gross line is sent ─────────────────────────────────────────────────────────

def test_the_gross_line_is_found_by_its_neighbours_note_terms_not_by_its_definition():
    gross, allowance = LOADED[GROSS], LOADED[ALLOWANCE]
    assert list(gross.note_source.note_terms) == list(allowance.note_source.note_terms)
    # The probe is the authored terms alone, so rewording the definition cannot move a note.
    assert line_item_notes.note_probe(gross) == " ".join(allowance.note_source.note_terms)
    assert gross.definition not in line_item_notes.note_probe(gross)
    # Terms with no row rule steer which notes the model is shown and nothing else: the
    # deterministic note route does not read rows for this line.
    assert not line_item_notes.by_meaning_only(gross)
    assert not list(gross.note_source.row_caption_any)


def _unit(ref: str, title: str):
    from app.services.note_context import ContextUnit, _with_tokens
    return _with_tokens(ContextUnit(kind="note", ref=ref, title=title))


def _table(number: str, title: str, caption: str, chapter: str = ""):
    ev = SimpleNamespace(value="100.00", period_label="current")
    row = SimpleNamespace(raw_label=caption, label=caption, values={"c": ev}, group_hint="")
    return SimpleNamespace(note_number=number, title=title, chapter_title=chapter, items=[row],
                           source_text="", prose_text="", basis=None, source_pages=[1])


def test_a_receivables_heading_names_the_gross_line_and_a_payables_heading_does_not():
    gross = LOADED[GROSS]
    pool = line_item_notes.header_pool([
        _table("20", "TRADE RECEIVABLES 20. 應收貿易款項", "Trade receivables"),
        _table("23", "TRADE AND BILLS PAYABLES 23. 應付貿易賬款及應付票據", "Trade payables"),
        _table("15", "PREPAYMENTS, OTHER RECEIVABLES AND OTHER ASSETS 15. 預付款項、其他應收賬款",
               "Other receivables"),
        _table("七、5", "按账龄披露", "1年以内", chapter="应收账款"),
    ])
    named = {u.ref: line_item_notes.heading_names_line(gross, u, pool=pool) for u in pool.units}
    assert named == {"20": True, "23": False, "15": False, "七、5": True}


def test_the_gross_line_is_sent_the_receivables_note_not_a_restricted_asset_table():
    """000685's shape: the receivables note arrives as sub-tables under the 应收账款 chapter, and
    the restricted-asset table prints an 应收账款 row of its own."""
    tables = [
        _table("七、5", "按坏账计提方法分类披露", "合计", chapter="应收账款"),
        _table("七、5", "本期实际核销的应收账款情况", "实际核销的应收账款", chapter="应收账款"),
        _table("七、4", "应收票据分类列示", "银行承兑票据", chapter="应收票据"),
        _table("七、7", "应收款项融资分类列示", "应收票据", chapter="应收款项融资"),
        _table("七、31", "、所有权或使用权受到限制的资产", "应收账款"),
        _table("九、2", "合并日被合并方资产、负债的账面价值", "应收账款"),
        _table("七、20", "应付账款列示", "应付账款", chapter="应付账款"),
    ]
    sets = line_item_notes.note_sets([LOADED[GROSS]], tables)
    notes = [h.note for h in sets.get(GROSS, [])]
    assert notes and notes[0] == "七、5", notes
    assert not {"七、31", "九、2", "七、20"} & set(notes), notes


# ── the rest of the placeholders and stale texts ───────────────────────────────────────────────

def test_no_hk_set_line_carries_any_extract_the_reported_placeholder():
    left = [i["key"] for i in SET["items"]
            if (i.get("definition") or "").startswith("Extract the reported")]
    assert not left, left


def test_accumulated_intangible_amortisation_says_what_it_is():
    text = _d("bs_nca__accum_intgbl_assets_amort")
    assert "accumulated amortisation" in text.lower()
    assert "negative figure" in text and ITEMS["bs_nca__net_intangibles"]["label"] in text
    assert ITEMS["bs_nca__accum_goodwill_amortized"]["label"] in text


def test_the_ontology_carries_the_set_text_for_every_line_this_review_touched():
    for key in (GROSS, ALLOWANCE, NET, "bs_nca__accum_intgbl_assets_amort",
                "is_pl__sales_revenues", "is_pl__personnel_and_benefit_expenses",
                "is_pl__interest_expense_capitalised", "is_pl__other_non_income_related_taxes",
                "cf_financing__end_of_period_cash_and_cash_equiv",
                "cf_financing__unexplained_adjustment_to_cash", "cf_investing__purchase_ppe"):
        assert CONCEPTS[key]["definition"] == ITEMS[key]["definition"], key


def test_closing_and_unexplained_cash_are_at_the_foot_of_the_statement_not_in_financing():
    for key in ("cf_financing__end_of_period_cash_and_cash_equiv",
                "cf_financing__unexplained_adjustment_to_cash"):
        assert ITEMS[key]["section_scope"] == ["cf_top_level"], key
        text = _d(key)
        assert "reported in the financing section" not in text, key
        assert "not part of the financing activities section" in text, key
        assert "template" in text and "rollup" in text, key
    assert ITEMS["cf_financing__unexplained_adjustment_to_cash"]["label"] in _d(
        "cf_financing__end_of_period_cash_and_cash_equiv")


def test_purchase_ppe_keeps_leasehold_land_and_sends_land_use_rights_where_the_aliases_do():
    ppe, intangibles = _d("cf_investing__purchase_ppe"), _d("cf_investing__purchase_intangible_assets")
    included, excluded = ppe.split("Not this line:", 1)
    assert "leasehold land" in included and "leasehold land" not in excluded
    assert "land use rights" in excluded and ITEMS["cf_investing__purchase_intangible_assets"]["label"] in excluded
    # The aliases agree: leasehold land purchases are Purchase PPE's, land use rights the
    # intangible line's — and the intangible line's own text says the same.
    assert any("leasehold land" in a.lower() for a in ITEMS["cf_investing__purchase_ppe"]["aliases"])
    assert any("land use rights" in a.lower()
               for a in ITEMS["cf_investing__purchase_intangible_assets"]["aliases"])
    assert "land use rights" in intangibles.split("Not this line:", 1)[0].lower()
    assert "leasehold land" in intangibles.split("Not this line:", 1)[1]

