"""The statement of changes in equity, read the other way: one column down the MOVEMENT rows.

`transpose_closing_balances` reads the BALANCE rows across every component column. This is the
other half of the same statement, and it needs the opposite axis: a movement's figures are spread
across the components, and `periods.slot_for` asks for a period — "Retained profits" is not one.
Measured before this existed: 28 of China SCE's movement rows and 10 of 佳明's reached
`engine_unclassified_face__changes_in_equity__unresolved_section__*` and published nothing, while
the template's whole retained-earnings block (`is_retained__*` — the dividends, the transfers to
reserves, the prior-period adjustments) sat empty on both filings.

WHICH COLUMN. That block is a reconciliation of RETAINED PROFITS, and the retained-profits column
of the equity statement is that reconciliation as the filing prints it. Nothing else is read, and
the reading is CHECKED before it is published: opening plus the movements must equal closing, per
block, which is what makes picking a column by name safe.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis, LineRole, ValueSource
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.services.equity_matrix import (MATCH_STATEMENT_FLAG, MOVEMENT_FLAG,
                                        RETAINED_PROFITS_KEY, collapse_movement_rows)
from app.services.row_reconstruct import EQUITY_BALANCE_FLAG

COMPONENTS = ("Issued capital", "Retained profits", "Total", "Non-controlling interests")


class _Page:
    def __init__(self, index: int):
        self.index, self.statement = index, "changes_in_equity"


class _Doc:
    def __init__(self, rows, pages=(106, 107)):
        self.line_items = list(rows)
        self.pages = [_Page(i) for i in pages]


class _Item:
    """One line of the resolved rulebook — only what `_retained_captions` reads."""
    def __init__(self, key, label="", aliases=(), aliases_i18n=None):
        self.key, self.label = key, label
        self.aliases = list(aliases)
        self.aliases_i18n = dict(aliases_i18n or {})


class _Rulebook:
    def __init__(self, *items):
        self.items = list(items)


def _rulebook(**kwargs) -> _Rulebook:
    return _Rulebook(_Item(RETAINED_PROFITS_KEY, label="Retained Profits",
                           aliases=["Retained Profits", "Retained earning"],
                           aliases_i18n={"zh": ["保留溢利", "未分配利润"]}, **kwargs))


def _row(label: str, page: int, amounts: dict[str, str], when: str = "") -> LineItem:
    row = LineItem(source_label=label, ordinal=0, role=LineRole.LINE, source=ValueSource.MACHINE)
    for component, amount in amounts.items():
        row.set_value(ExtractedValue(
            value_raw=Decimal(amount), value=Decimal(amount), basis=Basis.CONSOLIDATED,
            period_label=component, period_display=component, column_index=0,
            provenance=Provenance(page_index=page, bbox=BBox(x0=0, y0=0, x1=1, y1=1)),
        ))
    if when:
        row.confidence.flags.append(f"{EQUITY_BALANCE_FLAG}:{when}")
    return row


def _sce() -> _Doc:
    """China SCE's retained-profits column, to the unit, over its two pages.

    21,760,983 + 24,544 - 206,093 - 84,667 = 21,494,767, and
    21,494,767 - 7,991,050 + 14,058     = 13,517,775.

    The comprehensive-income SUBTOTAL is printed in both blocks, as the filing prints it: in this
    column it repeats the year's result exactly, so counting it would double the result and break
    both identities.
    """
    return _Doc([
        _row("At 1 January 2022", 106, {"Retained profits": "21760983", "Total": "21786360"},
             when="2022-01-01"),
        _row("Profit/(loss) for the year", 106,
             {"Retained profits": "24544", "Total equity": "-200692"}),
        _row("Exchange differences on translation", 106, {"Total": "-1551827"}),
        _row("Total comprehensive income for the year", 106,
             {"Retained profits": "24544", "Total": "-1550790"}),
        _row("Dividends paid to non-controlling shareholders", 106,
             {"Non-controlling interests": "-187477"}),
        _row("2021 final dividend", 106, {"Retained profits": "-206093", "Total": "-206665"}),
        _row("Transfer to statutory surplus reserve", 106, {"Retained profits": "-84667"}),
        _row("At 31 December 2022", 106, {"Retained profits": "21494767", "Total": "19710689"},
             when="2022-12-31"),
        _row("At 1 January 2023", 107, {"Retained profits": "21494767", "Total": "19710689"},
             when="2023-01-01"),
        _row("Loss for the year", 107, {"Retained profits": "-7991050"}),
        _row("Total comprehensive income for the year", 107,
             {"Retained profits": "-7991050", "Total": "-8452222"}),
        _row("Transfer statutory surplus reserve to retained profits", 107,
             {"Retained profits": "14058"}),
        _row("At 31 December 2023", 107, {"Retained profits": "13517775", "Total": "9723749"},
             when="2023-12-31"),
    ])


def _emitted(doc: _Doc) -> dict[str, dict[str, str]]:
    out = {}
    for row in doc.line_items:
        if not any(MOVEMENT_FLAG in (ev.confidence.flags or ()) for ev in row.values.values()):
            continue
        out[row.source_label] = {ev.period_label: str(ev.value) for ev in row.values.values()}
    return out


# --- what it reads -----------------------------------------------------------------------------

def test_each_movement_becomes_a_row_with_a_period():
    doc = _sce()
    logs: list[str] = []
    added = collapse_movement_rows(doc, line_items=_rulebook(), log=logs.append)

    assert _emitted(doc) == {
        "Profit/(loss) for the year": {"prior": "24544"},
        "2021 final dividend": {"prior": "-206093"},
        "Transfer to statutory surplus reserve": {"prior": "-84667"},
        "Loss for the year": {"current": "-7991050"},
        "Transfer statutory surplus reserve to retained profits": {"current": "14058"},
    }
    assert added == 5
    assert any("down 'Retained profits' current=2023-12-31 prior=2022-12-31" in m
               for m in logs), logs


def test_the_period_is_the_block_that_closes_beneath_the_movement():
    """The matrix has no period columns, so the period is the one the block CLOSES in. China SCE
    prints one page per year and reprints each close as the next page's opening balance, and the
    2022 movements are the PRIOR year's however the dates are ranked: the second-latest balance
    DATE is 1 January 2023, which closes nothing."""
    doc = _sce()
    collapse_movement_rows(doc, line_items=_rulebook())

    assert _emitted(doc)["2021 final dividend"] == {"prior": "-206093"}
    assert _emitted(doc)["Loss for the year"] == {"current": "-7991050"}


def test_a_movement_printed_in_another_column_is_not_read():
    """"Dividends paid to non-controlling shareholders" is a movement in NCI, not in retained
    profits, and the concepts this feeds are a retained-profits reconciliation."""
    doc = _sce()
    collapse_movement_rows(doc, line_items=_rulebook())

    assert "Dividends paid to non-controlling shareholders" not in _emitted(doc)
    assert "Exchange differences on translation" not in _emitted(doc)


def test_the_comprehensive_income_subtotal_is_not_an_addend():
    """In this column the subtotal repeats the year's result exactly, so reading it as a movement
    doubles the result — and then the block does not reconcile and nothing publishes at all.

    Identified by arithmetic, not by its wording: a row whose figure equals the running sum of
    the addends since the last subtotal IS that subtotal.
    """
    doc = _sce()
    collapse_movement_rows(doc, line_items=_rulebook())

    assert "Total comprehensive income for the year" not in _emitted(doc)


# --- what it declares itself to be -------------------------------------------------------------

def test_the_rows_declare_the_retained_profits_section_so_the_group_bottom_line_is_refused():
    """The gate that keeps this honest. "Loss for the year" read out of the RETAINED PROFITS
    column is the owners' share; `is_pl__profit_for_the_year` is the group's, and the two differ
    by the non-controlling interests. Measured on 佳明 before the section was declared as a
    BANNER (which is what `mapping.section_of_banner` reads) rather than as a namespace: the
    caption mapped to `is_pl__profit_for_the_year` at confidence 1.0 — the mapping
    `spec_alias_curation._THE_ATTRIBUTION_TAIL` exists to refuse.
    """
    import json
    from pathlib import Path

    from app.schemas.line_items import load_line_item_set
    from app.services.mapping import OntologyMatcher
    from app.services.working_view import build_working_view

    doc = _sce()
    collapse_movement_rows(doc, line_items=_rulebook())
    row = next(r for r in doc.line_items if r.source_label == "Loss for the year"
               and any(MOVEMENT_FLAG in (ev.confidence.flags or ()) for ev in r.values.values()))
    assert f"{MATCH_STATEMENT_FLAG}:profit_and_loss" in row.confidence.flags

    seed = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "output_csv_hk_line_items.json")
    cfg = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)
    matcher = OntologyMatcher(build_working_view(cfg))
    assert matcher.match("Loss for the year 年內虧損", statement="profit_and_loss",
                         section=row.section_hint).canonical_key is None
    # …while the movements the block DOES name reach their concepts.
    assert matcher.match("Transfer to statutory surplus reserve 轉撥至法定盈餘儲備",
                         statement="profit_and_loss",
                         section=row.section_hint).canonical_key == \
        "is_retained__transfer_to_reserves"
    assert matcher.match("2021 final dividend 二零二一年末期股息", statement="profit_and_loss",
                         section=row.section_hint).canonical_key == \
        "is_retained__cash_div_common_shares"


def test_the_values_are_not_flagged_as_restatements():
    """Unlike a transposed balance. A dividend charged to retained profits is printed HERE and
    nowhere else, so it is an ordinary addend and `periods.summable` must not refuse it."""
    from app.services.equity_matrix import TRANSPOSED_FLAG

    doc = _sce()
    collapse_movement_rows(doc, line_items=_rulebook())
    flags = [f for row in doc.line_items for ev in row.values.values()
             for f in (ev.confidence.flags or ())]
    assert MOVEMENT_FLAG in flags
    assert TRANSPOSED_FLAG not in flags


# --- what it refuses ---------------------------------------------------------------------------

def test_a_block_that_does_not_reconcile_publishes_nothing():
    """The check that makes reading one column by name safe: a column mis-read by one cell breaks
    opening + movements = closing, and then the block is refused rather than published."""
    doc = _sce()
    broken = next(r for r in doc.line_items if r.source_label == "2021 final dividend")
    ev = next(iter(broken.values.values()))
    ev.value = Decimal("-206094")
    logs: list[str] = []
    collapse_movement_rows(doc, line_items=_rulebook(), log=logs.append)

    assert set(_emitted(doc)) == {"Loss for the year",
                                  "Transfer statutory surplus reserve to retained profits"}
    assert any("refused(block 2022-12-31 does not reconcile" in m for m in logs), logs


def test_a_rulebook_that_names_no_retained_profits_concept_reads_nothing():
    doc = _sce()
    logs: list[str] = []
    assert collapse_movement_rows(doc, line_items=_Rulebook(), log=logs.append) == 0
    assert any(f"skipped(rulebook declares no {RETAINED_PROFITS_KEY}" in m for m in logs), logs


def test_a_matrix_with_no_such_column_reads_nothing():
    """A statement whose components are all reserves — no retained-profits column to read."""
    doc = _Doc([
        _row("At 1 January 2023", 107, {"Hedging reserve": "10"}, when="2023-01-01"),
        _row("Cash flow hedges", 107, {"Hedging reserve": "-3"}),
        _row("At 31 December 2023", 107, {"Hedging reserve": "7"}, when="2023-12-31"),
        _row("At 31 December 2024", 107, {"Hedging reserve": "5"}, when="2024-12-31"),
    ])
    logs: list[str] = []
    assert collapse_movement_rows(doc, line_items=_rulebook(), log=logs.append) == 0
    assert any("skipped(no retained-profits column" in m for m in logs), logs


def test_the_column_is_found_by_the_rulebooks_own_words_in_any_language():
    """Not by a vocabulary of this module's own: a filing printing 未分配利润 is read by the same
    rule as one printing "Retained profits", because both are spellings the rulebook declares for
    the concept whose column this is."""
    doc = _Doc([
        _row("于2022年1月1日", 107, {"未分配利润": "1000"}, when="2022-12-31"),
        _row("本年利润", 107, {"未分配利润": "-100"}),
        _row("于2023年12月31日", 107, {"未分配利润": "900"}, when="2023-12-31"),
        _row("本年利润", 107, {"未分配利润": "-50"}),
        _row("于2024年12月31日", 107, {"未分配利润": "850"}, when="2024-12-31"),
    ])
    logs: list[str] = []
    assert collapse_movement_rows(doc, line_items=_rulebook(), log=logs.append) == 1
    assert _emitted(doc) == {"本年利润": {"current": "-50", "prior": "-100"}}
    assert any("down '未分配利润'" in m for m in logs), logs


def test_a_document_with_no_matrix_is_untouched():
    doc = _Doc([_row("Trade receivables", 12, {"current": "14202"})])
    assert collapse_movement_rows(doc, line_items=_rulebook()) == 0
    assert len(doc.line_items) == 1
