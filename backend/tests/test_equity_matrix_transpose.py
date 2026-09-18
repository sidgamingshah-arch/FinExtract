"""The statement of changes in equity, turned round: one row per equity COMPONENT.

A matrix face has its concepts on the COLUMN axis, so `row_reconstruct._matrix_items` files each
cell under the component column it was printed in — the only honest reading of the page, and one no
consumer can publish: `periods.slot_for` asks for "current" and a component is not a period.
Measured before this existed: 32 of 32 rows on China SCE and 11 of 11 on 佳明集團 carried a
`period_label` that was a column header and every one mapped to
`engine_unclassified_face__changes_in_equity__unresolved_section__*`.

`services.equity_matrix` transposes the BALANCE rows — each one restates a balance sheet's equity
section at a date — and leaves the movements alone, there being no "dividends paid to
non-controlling shareholders" column in the template for one to go in.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis, LineRole, ValueSource
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.services.equity_matrix import (MATCH_STATEMENT_FLAG, TRANSPOSED_FLAG,
                                        transpose_closing_balances)
from app.services.row_reconstruct import EQUITY_BALANCE_FLAG

COMPONENTS = ("Issued capital", "Share premium", "Retained profits", "Total equity")


class _Page:
    def __init__(self, index: int, statement: str):
        self.index, self.statement = index, statement


class _Doc:
    def __init__(self, rows, pages=(106, 107)):
        self.line_items = list(rows)
        self.pages = [_Page(i, "changes_in_equity") for i in pages]


def _balance(label: str, when: str, page: int, amounts: dict[str, str]) -> LineItem:
    """A matrix balance row: one cell per component column, flagged with the date it is at."""
    row = LineItem(source_label=label, ordinal=0, role=LineRole.LINE, source=ValueSource.MACHINE)
    for component, amount in amounts.items():
        row.set_value(ExtractedValue(
            value_raw=Decimal(amount), value=Decimal(amount), basis=Basis.CONSOLIDATED,
            period_label=component, period_display=component, column_index=0,
            provenance=Provenance(page_index=page, bbox=BBox(x0=0, y0=0, x1=1, y1=1)),
        ))
    row.confidence.flags.append(f"{EQUITY_BALANCE_FLAG}:{when}")
    return row


def _movement(label: str, page: int, amounts: dict[str, str]) -> LineItem:
    row = _balance(label, "", page, amounts)
    row.confidence.flags.clear()
    return row


def _two_pages() -> _Doc:
    """China SCE's shape: one page per year, the prior year's close on the FIRST of them."""
    return _Doc([
        _balance("At 1 January 2022", "2022-01-01", 106,
                 dict(zip(COMPONENTS, ("365138", "1200", "21494767", "38793276")))),
        _movement("Loss for the year", 106, {"Retained profits": "-100"}),
        _balance("At 31 December 2022", "2022-12-31", 106,
                 dict(zip(COMPONENTS, ("365138", "1200", "21494667", "38793176")))),
        _balance("At 1 January 2023", "2023-01-01", 107,
                 dict(zip(COMPONENTS, ("365138", "1200", "21494667", "38793176")))),
        _movement("Loss for the year", 107, {"Retained profits": "-7991050"}),
        _balance("At 31 December 2023", "2023-12-31", 107,
                 dict(zip(COMPONENTS, ("365138", "1200", "13503617", "30802126")))),
    ])


def _values(row: LineItem) -> dict[str, str]:
    return {ev.period_label: str(ev.value) for ev in row.values.values()}


def _transposed(doc: _Doc) -> dict[str, LineItem]:
    return {r.source_label: r for r in doc.line_items
            if any(str(f).startswith(MATCH_STATEMENT_FLAG) for f in r.confidence.flags)}


def test_the_component_becomes_the_caption_and_the_row_date_becomes_the_period():
    doc = _two_pages()
    logs: list[str] = []
    assert transpose_closing_balances(doc, log=logs.append) == 4
    rows = _transposed(doc)
    assert set(rows) == set(COMPONENTS)
    assert _values(rows["Retained profits"]) == {"current": "13503617", "prior": "21494667"}
    assert _values(rows["Issued capital"]) == {"current": "365138", "prior": "365138"}
    assert any("current=2023-12-31 prior=2023-01-01" in m for m in logs), logs


def test_the_latest_date_is_the_current_period_across_pages_not_within_one():
    """The defect this is the whole reason for. Page 106's latest balance is 31 December 2022 —
    the PRIOR year's close — so a page-local rule publishes it as the current period and the two
    pages then write two different figures into one slot."""
    doc = _two_pages()
    transpose_closing_balances(doc)
    current = next(ev for ev in _transposed(doc)["Total equity"].values.values()
                   if ev.period_label == "current")
    assert str(current.value) == "30802126"
    assert current.provenance.page_index == 107


def test_a_movement_row_is_not_transposed():
    doc = _two_pages()
    transpose_closing_balances(doc)
    assert "Loss for the year" not in _transposed(doc)


def test_one_balance_date_is_left_alone():
    """With a single date there is nothing to call the comparative, and labelling one column's
    figures as both periods would report this year's balance as last year's."""
    doc = _Doc([_balance("At 31 December 2023", "2023-12-31", 107,
                         dict(zip(COMPONENTS, ("1", "2", "3", "6"))))])
    logs: list[str] = []
    assert transpose_closing_balances(doc, log=logs.append) == 0
    assert len(doc.line_items) == 1
    assert any("skipped(one balance date" in m for m in logs), logs


def test_a_document_with_no_matrix_is_untouched():
    doc = _Doc([_movement("Share capital", 12, {"current": "14202"})])
    assert transpose_closing_balances(doc) == 0
    assert len(doc.line_items) == 1


def test_a_balance_row_printed_somewhere_else_is_not_an_equity_component():
    """A movement schedule is a matrix too — an HKEX property note prints "At 1 April 2024" over
    cost and depreciation columns and closes "At 31 March 2025" — and its balance rows meet the same
    two conditions. Transposing one would publish "Plant and machinery" as an equity component."""
    rows = _two_pages().line_items + [
        _balance("At 31 March 2025", "2025-03-31", 88, {"Buildings": "5000"}),
        _balance("At 31 March 2026", "2026-03-31", 88, {"Buildings": "4200"}),
    ]
    doc = _Doc(rows)                      # pages 106/107 are the equity statement; 88 is a note
    assert transpose_closing_balances(doc) == 4
    assert "Buildings" not in _transposed(doc)
    # And the note's later dates did not become the periods either.
    assert _values(_transposed(doc)["Total equity"]) == {"current": "30802126",
                                                        "prior": "38793176"}


def test_the_transposed_row_declares_the_balance_sheet_as_its_vocabulary():
    """Every `bs_equity__*` concept is scoped to the balance sheet, and these rows were printed on
    the statement of changes in equity — measured: all nine of 佳明's component captions match on
    `balance_sheet` and none of them on `changes_in_equity`, "Share capital" included. The
    provenance still points at the page the figure came off."""
    doc = _two_pages()
    transpose_closing_balances(doc)
    row = _transposed(doc)["Share premium"]
    assert f"{MATCH_STATEMENT_FLAG}:balance_sheet" in row.confidence.flags
    assert row.section_hint == "equity"
    assert all(ev.provenance.page_index in (106, 107) for ev in row.values.values())


def test_the_component_column_index_does_not_travel_with_the_transpose():
    """`column_index` is what every publishing consumer reads as "this figure is a matrix column,
    skip it", and `stages.map_ontology` refuses to bind a row whose every value carries one. On the
    transposed row the component is the caption, so the index says nothing about it."""
    doc = _two_pages()
    transpose_closing_balances(doc)
    for row in _transposed(doc).values():
        assert all(ev.column_index is None for ev in row.values.values())


def test_every_transposed_value_is_marked_as_a_restatement():
    doc = _two_pages()
    transpose_closing_balances(doc)
    for row in _transposed(doc).values():
        assert all(TRANSPOSED_FLAG in (ev.confidence.flags or ())
                   for ev in row.values.values())


def test_a_closing_balance_and_the_next_pages_opening_are_not_added():
    """31 December 2022 and 1 January 2023 are the same figures at the same balance. They are
    distinct DATES, so only one of them can be the comparative — and the other must not also be
    written into it."""
    doc = _two_pages()
    transpose_closing_balances(doc)
    prior = [ev for ev in _transposed(doc)["Total equity"].values.values()
             if ev.period_label == "prior"]
    assert len(prior) == 1
    assert str(prior[0].value) == "38793176"


# ── The restatement never outranks the balance sheet ─────────────────────────────────────────

def _row(label: str, page: int, current: str, prior: str, *, restated: bool) -> dict:
    """A row in the shape `periods` reads — the same dicts `_serialize_rows` serves."""
    def slot(period: str, amount: str) -> dict:
        return {"period_label": period, "basis": "consolidated", "value": amount,
                "provenance": {"page_index": page},
                "confidence": {"flags": [TRANSPOSED_FLAG] if restated else []}}
    return {"source_label": label, "canonical_key": "bs_equity__common_share_capital",
            "values": [slot("current", current), slot("prior", prior)]}


def test_a_restatement_does_not_add_to_a_slot_the_balance_sheet_filled():
    """佳明's balance sheet prints "Share capital 股本" 14,202 and the matrix column is captioned
    "Share capital", so `periods.caption_key` sees two different captions and the general
    same-fact rule cannot fire. Measured without this: 28,404 published for a printed 14,202."""
    from app.services.periods import concept_value

    group = [_row("Share capital 股本", 64, "14202", "14202", restated=False),
             _row("Share capital", 65, "14202", "14202", restated=True)]
    assert concept_value(group, "consolidated", "current") == 14202.0
    assert concept_value(group, "consolidated", "prior") == 14202.0


def test_a_restatement_fills_a_slot_the_balance_sheet_left_empty():
    """佳明's balance sheet prints share capital and one lumped "Reserves", so the matrix is the
    only place the breakdown exists. Asymmetric on purpose — same policy as
    `stages.note_sourced`'s "NEVER OVER THE FILING'S OWN FIGURE"."""
    from app.services.periods import concept_value

    group = [_row("Share premium", 65, "95045", "95045", restated=True)]
    assert concept_value(group, "consolidated", "current") == 95045.0


def test_two_printed_components_on_one_template_line_still_add():
    """China SCE prints "Capital reserve" (-5,130,954) and "Statutory surplus reserve"
    (1,883,822), which are two genuine components of one template line. The guard is about a
    restatement standing beside a PRINTED figure, not about restatements standing beside each
    other."""
    from app.services.periods import concept_value

    group = [_row("Capital reserve", 107, "-5130954", "-3596236", restated=True),
             _row("Statutory surplus reserve", 107, "1883822", "1897880", restated=True)]
    assert concept_value(group, "consolidated", "current") == -3247132.0
    assert concept_value(group, "consolidated", "prior") == -1698356.0
