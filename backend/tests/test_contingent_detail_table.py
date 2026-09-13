r"""EVERY CONTINGENT LIABILITY, ONE BY ONE, WITH WHAT THEY COME TO — and in the workbook.

NEW FILE -> backend/tests/test_contingent_detail_table.py

WHAT THIS ADDS TO WHAT `test_contingent_liabilities.py` ALREADY COVERS. That file pins the note
search, the classification priority, the per-type summary and the unclassified fallback. All four
were already right. What was missing is the DETAIL TABLE: every `ContingentItem` is computed —
description, classification, the basis it was classified on, amount, currency, scale, note number,
note heading, page, counterparty, and whether it restates another note — and then only the GROUPED
views reached the output. `classified_summary` sums per type; `unclassified_items` keeps only what
fits no type. So an item that WAS classified appeared nowhere on its own, and a reader checking a
corporate-guarantee subtotal against the page it cites had nothing to check it against.

§6.5 already referred to that table — `test_an_item_with_no_amount_survives_deduplication` says
"it belongs in the narrative and the detail table, just never in a total" — so this is the missing
half of a design the rest of the module was written against.

THE TOTAL IS THE PART TO BE CAREFUL WITH, and these tests are mostly about it. This module used to
publish ONE blended Decimal onto `notes__contingent_liabilities`, assembled out of 172
hand-enumerated entries, with a MULTIPLE_CURRENCIES_NOT_AGGREGATED flag arbitrating when its groups
spanned unlike units. That derivation was removed and the module docstring says: do not reinstate a
computed total here. So the tests below pin the three things that keep this a TABLE TOTAL and not
that figure coming back —

    * no figure is published onto any line item;
    * no sum ever crosses a currency or a presentation scale;
    * a restatement is counted once, and an item with no amount is counted as unpriced rather than
      as zero — a sum over eight items where three disclosed nothing is not a total of the exposure.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.contingent_liabilities import (attach_contingent_explanation, compute,
                                                 disclosure_explanation)
from tests.test_contingent_liabilities import _doc, _item, _note, _stage_record

PK = ("consolidated", "current")


# ── THE TABLE ─────────────────────────────────────────────────────────────────────────────────

def test_every_disclosed_item_appears_once_in_the_detail_table():
    """INCLUDING THE CLASSIFIED ONES, which is the gap. Two of these three are classified and so
    appeared only inside a per-type subtotal; the third fits no type and appeared only in the
    unclassified list. All three are exposures a reader has to see individually."""
    doc = _doc(_note("35", "对外担保", [
        _item("为子公司提供的连带责任保证", "5000000"),
        _item("开出信用证", "1231000"),
        _item("未决诉讼", "770000"),
    ]))
    result = compute(doc)[PK]

    assert len(result.detail_items) == 3
    assert [r["description"] for r in result.detail_items] == [
        "为子公司提供的连带责任保证", "开出信用证", "未决诉讼"]
    # …and the classification travels with each row, because that is the judgement being checked.
    kinds = {r["description"]: r["classification"] for r in result.detail_items}
    assert kinds["开出信用证"] == "Letters of Credit", kinds
    assert kinds["为子公司提供的连带责任保证"] == "Corporate guarantees", kinds


def test_the_row_says_why_it_was_classified_that_way():
    """A classification a reader cannot question is one they have to take on trust — and the fixed
    priority order means a broad "guarantee" caption can lose to a more specific instrument, which
    is exactly the decision worth showing."""
    doc = _doc(_note("35", "对外担保", [_item("开出保函", "400")]))
    row = compute(doc)[PK].detail_items[0]

    assert row["classification"] == "Bank guarantees"
    assert row["classified_by"], "the row does not say what it was classified on"


def test_the_page_and_note_travel_with_every_row():
    """The point of the table is that a figure can be checked against the page it came from."""
    doc = _doc(_note("36", "或有负债", [_item("为子公司提供的连带责任保证", "5000000")]))
    row = compute(doc)[PK].detail_items[0]

    assert row["note_number"] == "36"
    assert row["note_heading"] == "或有负债"


# ── THE TOTAL ─────────────────────────────────────────────────────────────────────────────────

def test_the_table_totals_what_it_shows():
    """The column sum, so a reader can select the rows and check them against it."""
    doc = _doc(_note("35", "对外担保", [
        _item("为子公司提供的连带责任保证", "5000000"),
        _item("开出信用证", "1231000"),
    ]))
    totals = compute(doc)[PK].detail_totals

    assert len(totals) == 1, totals
    assert totals[0]["amount"] == Decimal("6231000")
    assert totals[0]["item_count"] == 2
    assert totals[0]["unpriced"] == 0


def test_no_sum_ever_crosses_a_currency():
    """THE INVARIANT THE REMOVED DERIVATION BROKE. One blended figure over unlike units is the
    thing `MULTIPLE_CURRENCIES_NOT_AGGREGATED` used to withhold, and the derivation that needed it
    is gone. Two currencies are two subtotals — never one sum, and never a withheld answer."""
    doc = _doc(_note("35", "对外担保", [
        _item("为子公司提供的连带责任保证", "5000000", currency="CNY"),
        _item("为境外子公司提供的连带责任保证", "700000", currency="USD"),
    ]))
    totals = compute(doc)[PK].detail_totals

    assert {(t["currency"], t["amount"]) for t in totals} == {
        ("CNY", Decimal("5000000")), ("USD", Decimal("700000"))}


def test_an_item_with_no_amount_is_unpriced_and_not_zero():
    """§6.5 again: it belongs in the table and never in a total. Counted as unpriced rather than
    added as zero, because a sum that silently treats "not disclosed" as nil understates the
    exposure and says nothing about having done so."""
    doc = _doc(_note("36", "或有负债", [
        _item("为子公司提供的连带责任保证", "5000000"),
        _item("未决诉讼"),
    ]))
    result = compute(doc)[PK]

    assert len(result.detail_items) == 2, "the unpriced item was dropped from the table"
    # The unpriced item carries no currency, so it groups on its own rather than being folded into
    # a currency it never declared.
    priced = [t for t in result.detail_totals if t["amount"] == Decimal("5000000")]
    assert priced, result.detail_totals
    assert sum(t["unpriced"] for t in result.detail_totals) == 1


def test_nothing_is_published_onto_the_line_item():
    """The one rule this module's docstring states outright: the figure for
    notes__contingent_liabilities has to come from configuration, and no total assembled here may
    land on it. The table's own total is for a reader, not for a cell."""
    doc = _doc(_note("35", "对外担保", [_item("为子公司提供的连带责任保证", "5000000")]))
    compute(doc)

    published = [li for li in doc.line_items
                 if li.canonical_key == "notes__contingent_liabilities"
                 and any(ev.value is not None for ev in (li.values or {}).values())]
    assert not published, "a figure was published onto notes__contingent_liabilities"


# ── IT REACHES EVERY READER ───────────────────────────────────────────────────────────────────

def test_the_table_is_on_the_disclosure_entry_every_consumer_reads():
    """`disclosures` is the one list that flows to the Excel Disclosures sheet, the JSON export,
    /analysis and the Disclosures screen — so the table goes on that entry and reaches all four at
    one insertion point."""
    doc = _doc(_note("35", "对外担保", [
        _item("为子公司提供的连带责任保证", "5000000"),
        _item("开出信用证", "1231000"),
    ]))
    working = disclosure_explanation(_stage_record(doc))

    assert len(working["items"]) == 2
    assert {r["classification"] for r in working["items"]} == {
        "Corporate guarantees", "Letters of Credit"}
    assert working["item_totals"][0]["amount"] == "6231000"


def test_it_lands_on_its_own_disclosure_and_no_other():
    doc = _doc(_note("35", "对外担保", [_item("为子公司提供的连带责任保证", "5000000")]))
    entries = attach_contingent_explanation(
        [{"key": "contingent_liabilities"}, {"key": "pledged_assets"}], _stage_record(doc))

    assert entries[0].get("items"), "the table did not reach its own entry"
    assert "items" not in entries[1], "the table leaked onto another disclosure"


def test_the_commentary_is_shown_the_table_it_describes():
    """The narrative payload carries the detail and its totals, so the paragraph describes what the
    reader is looking at. Before this it could only speak about per-type subtotals, and a reader
    comparing it against the detail table found the two talking about different things."""
    from app.services.contingent_liabilities import build_narrative_payload

    doc = _doc(_note("35", "对外担保", [
        _item("为子公司提供的连带责任保证", "5000000"),
        _item("开出信用证", "1231000"),
    ]))
    payload = build_narrative_payload(compute(doc)[PK])

    assert len(payload["detail_items"]) == 2
    assert payload["detail_totals"][0]["amount"] == "6231000"
    # Figures as strings, like every other amount in this payload: a float here is a rounding the
    # model would quote back as if the filing had stated it.
    assert all(isinstance(r["amount"], (str, type(None))) for r in payload["detail_items"])


def test_the_model_cannot_move_a_figure_in_the_table():
    """`enhance_with_llm` rewrites PROSE. The detail table and its totals pass through untouched,
    which is what makes "nothing here can change a total" true of the table as well."""
    from app.services.contingent_liabilities import (ContingentLiabilitiesNarrative,
                                                     enhance_with_llm)

    doc = _doc(_note("35", "对外担保", [_item("为子公司提供的连带责任保证", "5000000")]))
    before = compute(doc)[PK]

    class _Provider:
        def complete_structured(self, **_kw):
            return ContingentLiabilitiesNarrative(
                summary_paragraph="Rewritten.", unclassified_statements=[]), None

    after, _meta = enhance_with_llm(_Provider(), before)
    assert after.summary_paragraph == "Rewritten."
    assert after.detail_items == before.detail_items
    assert after.detail_totals == before.detail_totals
