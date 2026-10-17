"""A NOTE TABLE WHOSE COLUMNS ARE NOT PERIODS reaches the model keyed by its printed columns.

Found by the stand-in on the f1 Ind AS filing. Its PP&E schedule (asset classes across; opening,
additions, disposals, closing down; a gross block and an accumulated-depreciation block, once per
year) was sent as caption-only rows — every value of a matrix row carries a `column_index`, which
the request skipped — so note 3 sent {"caption": "Disposals"} four times and the (420.45) /
(519.65) printed in its Total column only in a flattened prose string. Its ageing schedule was sent
with the buckets keyed as periods ({current, current_col1 …}) and its split two-line header as rows
("Particulars Not due Less than" carrying the "6" of "6 months").

Now such a row carries its figures under the printed column, its group, and the period of its
block, read from the table's own dated balance rows (or `unstated`); header lines are joined into
`header` and not sent; a period table is sent exactly as before; and a citation naming the group,
caption, column and period resolves to that one printed figure, sign included.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import note_context
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

CLASSES = ("Freehold land", "Buildings", "Total")


def _matrix_row(caption: str, *amounts: str | None) -> NoteItem:
    row = NoteItem(raw_label=caption)
    for k, (name, amount) in enumerate(zip(CLASSES, amounts)):
        if amount is None:
            continue
        row.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                     basis=Basis.STANDALONE, period_label=name,
                                     period_display=name, column_index=k,
                                     provenance=Provenance(page_index=7)))
    return row


def _ppe() -> NotesTable:
    rows = [
        _matrix_row("Balance as at 1 April 2023", "1245.60", "8412.35", "9657.95"),
        _matrix_row("Additions", None, "612.40", "612.40"),
        _matrix_row("Disposals", None, "-503.35", "-503.35"),
        _matrix_row("Balance as at 31 March 2024", "1245.60", "8521.40", "9767.00"),
        _matrix_row("Additions", None, "486.20", "486.20"),
        _matrix_row("Disposals", None, "-616.25", "-616.25"),
        _matrix_row("Balance as at 31 March 2025", "1245.60", "8391.35", "9636.95"),
        _matrix_row("Accumulated depreciation Balance as at 1 April 2023", None, "1642.30",
                    "1642.30"),
        _matrix_row("Depreciation for the year", None, "286.40", "286.40"),
        _matrix_row("Disposals", None, "-420.45", "-420.45"),
        _matrix_row("Balance as at 31 March 2024", None, "1508.25", "1508.25"),
        _matrix_row("Depreciation for the year", None, "304.85", "304.85"),
        _matrix_row("Disposals", None, "-519.65", "-519.65"),
        _matrix_row("Balance as at 31 March 2025", None, "1293.45", "1293.45"),
    ]
    return NotesTable(note_number="3", title="Property, plant and equipment", items=rows,
                      source_text="(A) Property, plant and equipment Particulars Freehold "
                                  "Buildings Total land Gross carrying amount Balance as at 1 "
                                  "April 2023 1,245.60 8,412.35 9,657.95 Additions ...")


def _identified(tables) -> list[dict]:
    items = [LineItemDef(key="k", label="k", note_source=NoteSource(note_title_any=[".*"]))]
    return note_context.identified_notes(LineItemSet(items=items), tables)


def test_a_movement_note_sends_its_figures_under_the_classes_with_each_blocks_period():
    rows = _identified([_ppe()])[0]["rows"]
    disposals = [r for r in rows if r["caption"] == "Disposals"]
    assert [(r["group"], r["period"], r["figures"]["Total"]) for r in disposals] == [
        ("Gross carrying amount", "prior", "-503.35"),
        ("Gross carrying amount", "current", "-616.25"),
        ("Accumulated depreciation", "prior", "-420.45"),
        ("Accumulated depreciation", "current", "-519.65")]
    assert disposals[3]["period_as_printed"] == "to 31 March 2025"
    opening = rows[0]
    assert opening["figures"] == {"Freehold land": "1245.60", "Buildings": "8412.35",
                                  "Total": "9657.95"}
    # The opening balance is neither year's close: the table says so rather than guessing.
    assert opening["period"] == "unstated" and opening["period_as_printed"] == "as at 1 April 2023"
    closing = rows[6]
    assert (closing["period"], closing["period_as_printed"]) == ("current", "as at 31 March 2025")
    assert not any("columns" in r for r in rows)


def test_a_citation_of_accumulated_depreciation_disposals_total_current_resolves_signed():
    resolved, unresolved = resolve_sources(
        [SourceRef(note="3", group="Accumulated depreciation", caption="Disposals",
                   column="Total", period="current")], [_ppe()])
    assert unresolved == []
    assert resolved[0]["figures"] == {"current": "-519.65"}
    assert resolved[0]["column"]["cells"][0]["period_source"] == "row_block"
    prior, _ = resolve_sources(
        [SourceRef(note="3", group="Accumulated depreciation", caption="Disposals",
                   column="Total", period="prior")], [_ppe()])
    assert prior[0]["figures"] == {"prior": "-420.45"}
    # Without the period the caption still names two rows with different figures: refused.
    _, refused = resolve_sources(
        [SourceRef(note="3", group="Accumulated depreciation", caption="Disposals",
                   column="Total")], [_ppe()])
    assert refused and "period" in refused[0]["why"]


def _aged(caption: str, *figures: tuple[str, str, str | None, float]) -> NoteItem:
    row = NoteItem(raw_label=caption)
    for key, amount, heading, x in figures:
        row.set_value(ExtractedValue(
            value_raw=Decimal(amount), value=Decimal(amount), basis=Basis.STANDALONE,
            period_label=key, period_display="As at 31 March 2025", column_heading=heading,
            provenance=Provenance(page_index=9, value_bbox=BBox(x0=x - 0.05, y0=0.5, x1=x,
                                                                y1=0.51))))
    return row


def _ageing(headed: bool) -> NotesTable:
    h = (lambda name: name) if headed else (lambda name: None)
    rows = [
        _aged("Particulars Not due Less than", ("current_col1", "6", None, 0.45)),
        _aged("months", ("current_col1", "1", None, 0.52), ("prior", "3", None, 0.79)),
        _aged("(i) Undisputed trade receivables - considered good",
              ("current", "10486.15", h("Not due"), 0.36),
              ("current_col1", "2641.95", h("Less than 6 months"), 0.45),
              ("current_col2", "62.70", h("6 months - 1 year"), 0.52),
              ("prior_col5", "13190.80", h("Total"), 0.89)),
    ]
    return NotesTable(note_number="10", title="Trade receivables", items=rows,
                      source_text="Ageing Particulars Not due Less than 6 6 months - 1-2 years "
                                  "More than Total months 1 year 3 years (i) Undisputed trade "
                                  "receivables - considered good 10,486.15 ...")


def test_an_ageing_schedule_sends_its_buckets_as_columns_and_its_header_joined():
    rows = _identified([_ageing(headed=True)])[0]["rows"]
    assert [r["caption"] for r in rows] == ["(i) Undisputed trade receivables - considered good"]
    row = rows[0]
    assert row["figures"] == {"Not due": "10486.15", "Less than 6 months": "2641.95",
                              "6 months - 1 year": "62.70", "Total": "13190.80"}
    assert row["header"].startswith("Particulars Not due Less than 6 6 months")
    assert row["period"] == "unstated"
    # Unheaded, the buckets are still columns — numbered in printed order, never periods.
    bare = _identified([_ageing(headed=False)])[0]["rows"][0]
    assert bare["figures"] == {"column 1": "10486.15", "column 2": "2641.95",
                               "column 3": "62.70", "column 4": "13190.80"}
    resolved, unresolved = resolve_sources(
        [SourceRef(note="10", caption="(i) Undisputed trade receivables - considered good",
                   column="Less than 6 months")], [_ageing(headed=True)])
    assert unresolved == [] and resolved[0]["figures"] == {"current": "2641.95"}
    assert resolved[0]["column"]["assumed"] is True
    # A header line is not a citable row.
    _, refused = resolve_sources([SourceRef(note="10", caption="months", column="column 1")],
                                 [_ageing(headed=False)])
    assert refused


def test_an_ordinary_two_year_note_is_sent_as_before():
    def row(caption, cur, pri):
        item = NoteItem(raw_label=caption)
        for key, amount in (("current", cur), ("prior", pri)):
            item.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                          basis=Basis.STANDALONE, period_label=key))
        return item

    table = NotesTable(note_number="12", title="Cash and cash equivalents",
                       items=[row("Balances with banks", "812.40", "640.10"),
                              row("Cash on hand", "4.25", "3.90")])
    assert _identified([table])[0]["rows"] == [
        {"caption": "Balances with banks", "figures": {"current": "812.40", "prior": "640.10"}},
        {"caption": "Cash on hand", "figures": {"current": "4.25", "prior": "3.90"}}]
