"""EACH LINE IS SENT THE NOTE THAT PRINTS ITS FIGURE — `line_item_notes.note_sets`.

Three ways the selection missed the note that prints a line's figure, each measured on the twenty
published filings with `scripts/llm_standin.py coverage`:

  1. THE CHAPTER HEADING WAS NOT SCORED. A CAS filing prints a disclosure as a chapter of
     sub-tables (十四、关联方及关联交易 → 5 → (7) 关键管理人员报酬) and the table arrives titled with
     the sub-heading only, so the heading that says what the disclosure IS reached no score.
  2. A POLICY PARAGRAPH OUT-SCORED THE DISCLOSURE. A policy note prints one sub-heading per subject
     and so matches every line; it prints no figure in any row, so a row-reading line can never
     cite it. On PetroChina it was in 93 of the 96 oversize requests.
  3. A ROW PRINTED UNDER THE LINE'S OWN NAME, IN A NOTE HEADED WITH NONE OF ITS WORDS, was
     unreachable: "Interest on lease liabilities" under FINANCE COSTS, "Trade payable" under
     ACCOUNTS PAYABLE AND ACCRUED LIABILITIES.

And the configuration half: eight template lines declared `route: face` — "its figure is not in a
note" — while the HKEX filings print their figure only in a note.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import LineItemDef, NoteSource, load_line_item_set
from app.services import line_item_notes, line_item_routes

_SEED = (Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")


def _note(number: str, title: str, captions: tuple[str, ...] = (), *, chapter: str = "",
          figures: bool = True) -> NotesTable:
    table = NotesTable(note_number=number, title=title, page_index=9, chapter_title=chapter)
    for caption in captions:
        row = NoteItem(raw_label=caption, note_number=number)
        if figures:
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                         value=Decimal("1"), value_raw=Decimal("1"),
                                         provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


# Unrelated headings with figure rows, so the IDF pool has something to discriminate against and
# the "prints a figure" evidence exists in the filing.
_POOL = ("Inventories", "Share capital", "Income tax", "Cash and cash equivalents",
         "Deferred taxation", "Segment information", "Bank borrowings", "Goodwill")


def _filing(*extra: NotesTable) -> list[NotesTable]:
    notes = [_note(str(i + 1), title, ("Total",)) for i, title in enumerate(_POOL)]
    notes.extend(extra)
    return notes


def _notes_of(item, notes) -> list[str]:
    return [h.note for h in line_item_notes.note_sets([item], notes).get(item.key, [])]


# ── 1. the chapter heading ──────────────────────────────────────────────────────────────────────

def test_a_sub_table_is_scored_with_the_chapter_it_is_printed_under():
    item = LineItemDef(key="notes__rp", label="Related party transactions",
                       aliases_i18n={"zh": ["关联方及关联交易"]})
    notes = _filing(
        _note("七、1", "货币资金", ("合计",), chapter="合并财务报表项目注释"),
        _note("七、37", "应付职工薪酬", ("合计",), chapter="合并财务报表项目注释"),
        _note("十四、5", "关键管理人员报酬", ("关键管理人员报酬",), chapter="关联方及关联交易"),
    )
    assert "十四、5" in _notes_of(item, notes)


def test_a_filing_without_chapters_scores_exactly_as_before():
    pool = line_item_notes.header_pool(_filing())
    assert all(u.captions == () for u in pool.units)


# ── 2. a note that prints no figure is not offered to a row-reading line ───────────────────────

def _lease_line(route: str) -> LineItemDef:
    return LineItemDef(key=f"sub__lease_{route}", label="Lease depreciation", route=route,
                       note_source=NoteSource(note_terms=["right of use assets", "leases"]))


def _policy_filing() -> list[NotesTable]:
    return _filing(
        _note("4", "Leases", figures=False),                       # a policy paragraph
        _note("4", "Right of use assets policy", figures=False),
        _note("22", "Right of use assets", ("Depreciation charge",)),
    )


def test_a_policy_paragraph_is_not_offered_to_a_note_tables_line():
    got = _notes_of(_lease_line("note_tables"), _policy_filing())
    assert "22" in got and "4" not in got


def test_a_prose_line_still_reads_a_paragraph():
    assert "4" in _notes_of(_lease_line("prose"), _policy_filing())


def test_headings_only_notes_are_not_filtered_to_nothing():
    notes = [NotesTable(note_number=str(i), title=t, page_index=1)
             for i, t in enumerate(("Leases", "Inventories", "Income tax"))]
    assert line_item_notes.header_pool(notes).prints_figures is None
    assert "0" in _notes_of(_lease_line("note_tables"), notes)


# ── 3. the note that prints a row under the line's own name ────────────────────────────────────

def test_a_row_under_the_lines_name_reaches_it_when_no_heading_does():
    item = LineItemDef(key="is_pl__lease_interest", label="Lease interest",
                       aliases=["Interest on lease liabilities"])
    notes = _filing(_note("6", "FINANCE COSTS",
                          ("Interest on bank loans", "Interest on lease liabilities")))
    hits = line_item_notes.note_sets([item], notes)[item.key]
    assert [(h.note, h.via) for h in hits if h.via == "named_row"] == [("6", "named_row")]


def test_an_english_plural_does_not_hide_the_row():
    item = LineItemDef(key="bs_cl__tp", label="Trade payables (CP)", aliases=["Trade payables"])
    notes = _filing(_note("27", "ACCOUNTS PAYABLE AND ACCRUED LIABILITIES",
                          ("Trade payable", "Notes payable")))
    assert "27" in _notes_of(item, notes)


def test_nothing_is_added_when_the_set_already_holds_such_a_row():
    item = LineItemDef(key="bs_cl__tp", label="Trade payables", aliases=["Trade payables"])
    notes = _filing(_note("27", "Trade payables", ("Trade payables",)),
                    _note("40", "Related parties", ("Trade payables",)))
    got = _notes_of(item, notes)
    assert "27" in got and "40" not in got


def test_a_one_word_name_and_a_row_without_a_figure_admit_nothing():
    item = LineItemDef(key="x", label="Zzz", aliases=["Debtors", "Interest on lease liabilities"])
    notes = _filing(_note("30", "Other", ("Debtors",)),
                    _note("31", "Another", ("Interest on lease liabilities",), figures=False))
    assert line_item_notes.named_row_notes(item, notes) == []


def test_a_face_line_is_still_given_no_note():
    item = LineItemDef(key="x", label="Zzz", aliases=["Interest on lease liabilities"],
                       route="face")
    notes = _filing(_note("6", "FINANCE COSTS", ("Interest on lease liabilities",)))
    assert _notes_of(item, notes) == []


# ── the configuration ──────────────────────────────────────────────────────────────────────────

_PRINTED_IN_A_NOTE = (
    "is_pl__interest_on_lease_liabilities",      # hkex_0160 note 6, hkex_0191 / 0285 note 8
    "is_pl__interest_expense_capitalised",       # hkex_0160 note 6 Finance costs
    "is_pl__personnel_and_benefit_expenses",     # hkex_0191 note 7, hkex_0285 note 6
    "bs_cl__trade_payables_cp",                  # PetroChina IFRS note 27, kaming note 20
    "bs_cl__st_bank_loans_payable",              # hkex_1966 note 30 Bank loans
    "bs_cl__cpltd_bank",                         # Far East Horizon note 32
    "bs_ca__trade_receivables_gross",            # Kaishan 七、5 应收账款
    "bs_ca__allow_for_doubtful_accounts",        # Kaishan 七、5 坏账准备
)


def _shipped():
    return {i.key: i for i in load_line_item_set(json.loads(_SEED.read_text())).items}


def test_the_lines_the_filings_print_in_a_note_may_read_notes_and_the_face():
    items = _shipped()
    for key in _PRINTED_IN_A_NOTE:
        assert line_item_routes.may_read_notes(items[key]), key
        assert line_item_routes.may_read_face(items[key]), key


def test_the_key_management_part_names_the_key_management_table():
    # The table belongs to the related-party line's key-management PART since the line became the
    # sum of its transaction categories; bound to the line itself, the row was the whole figure.
    part = _shipped()["sub__rpt_key_management"]
    assert {"关键管理人员报酬", "关键管理人员薪酬"} <= set(part.note_source.note_terms)
