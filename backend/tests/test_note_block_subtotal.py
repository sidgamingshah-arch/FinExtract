"""A note prints a block's total on a line with no caption, and it used to be thrown away.

A typesetter does not repeat the sub-heading two rows up, so the total of a block inside a note is
printed as a bare figure under the rows it totals:

    Current charge for the year:
      PRC corporate income tax                 600      700
      PRC land appreciation tax ("LAT")         600      500
                                             1,200    1,200      <- no caption at all

That row was DROPPED at row reconstruction, by the first arm of a predicate written for the
opposite shape (a label-only banner, `not value_words`). The `not label` arm swept a valued,
captionless row into a branch whose only escape is guarded by `label and caption`, so control always
reached the bare `continue`. The figure was never parsed and never given a provenance: after that
line it did not exist.

WHAT MAKES THE ROLE THE POINT, and not the recovery. Filed as a plain LINE, this row is a detail of
its own block, and two readers act on that: `stages/reconcile` excludes SUBTOTAL/TOTAL from a note's
details exactly so a note's own total is not added to the rows it totals, and `map_ontology`'s §20
decomposition only considers rows whose role is LINE. As a LINE the figure is double-counted in the
first and doubles a component in the second — so "stop dropping it" on its own would break the
working note→face tie AND decline the whole §20 split. Every test here that fixes the role is
guarding one of those two.

AND WHY IT IS WORTH DOING PROPERLY. The §20 split re-derives a block's total by SUMMING the block's
details. Nothing ever compared that sum to the total the filing printed underneath them; agreement
was assumed. Recovering the printed row turns that assumption into a check — the only check in the
system that needs neither a template nor a mapping, because it compares a printed figure to the
figures printed above it.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat, LineRole
from app.core.models.geometry import BBox
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word, build_line_items

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


# --- the smallest thing that reproduces the layout -------------------------------------------------

def W(t: str, x0: float, y: float, w: float = 0.12, h: float = 0.012) -> Word:
    return Word(text=t, bbox=BBox(x0=x0, y0=y, x1=x0 + w, y1=y + h))


def _rows_to_words(rows: list[list[Word]]) -> list[Word]:
    return [w for r in rows for w in r]


# The tax note's shape, reduced to what the promotion reads: a colon sub-heading, two indented
# valued rows, and a bare figure beneath them in the same column.
_HEAD = [W("Note", 0.10, 0.10, 0.05), W("8:", 0.16, 0.10, 0.03),
         W("Income", 0.20, 0.10, 0.06), W("tax", 0.27, 0.10, 0.03),
         W("expense", 0.31, 0.10, 0.07)]
_BLOCK = [W("Current", 0.10, 0.14, 0.06), W("tax:", 0.17, 0.14, 0.04)]
_D1 = [W("Hong", 0.12, 0.17, 0.04), W("Kong", 0.17, 0.17, 0.04),
       W("profits", 0.22, 0.17, 0.05), W("tax", 0.28, 0.17, 0.03),
       W("1,000", 0.70, 0.17, 0.06)]
_D2 = [W("Mainland", 0.12, 0.20, 0.06), W("China", 0.19, 0.20, 0.04),
       W("EIT", 0.24, 0.20, 0.03), W("2,000", 0.70, 0.20, 0.06)]
_BARE = [W("3,000", 0.70, 0.235, 0.06)]


def _note_items(rows: list[list[Word]]) -> list:
    tables = extract_note_tables(_rows_to_words(rows), page_index=3,
                                 document_id="d", source_kind="pdf_native")
    return [it for t in tables for it in t.items]


def _labels(items) -> list[str]:
    return [it.raw_label for it in items]


def _ni(label, role, value, group="Current tax:", borrowed=None, ordinal=0, components=(),
        prior=None):
    """One note row. `ordinal` and `components` matter because the arithmetic check reads the
    member list the BUILDER recorded rather than re-deriving it from the caption."""
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem

    it = NoteItem(raw_label=label, ordinal=ordinal, role=role, group_hint=group,
                  caption_borrowed=(role is LineRole.SUBTOTAL) if borrowed is None else borrowed,
                  component_ordinals=list(components))
    it.set_value(ExtractedValue(value_raw=Decimal(value), value=Decimal(value),
                                basis=Basis.CONSOLIDATED, period_label="current"))
    if prior is not None:
        it.set_value(ExtractedValue(value_raw=Decimal(prior), value=Decimal(prior),
                                    basis=Basis.CONSOLIDATED, period_label="prior"))
    return it


def _checked(items):
    from app.core.models.line_item import NotesTable
    from app.core.models.reports import ReconciliationReport
    from app.stages.reconcile import _check_block_subtotals

    note = NotesTable(note_number="8", title="Income tax", source_pages=[3])
    note.items = items
    doc = DocumentModel(filename="f.pdf", fmt=DocFormat.PDF)
    doc.notes = [note]
    report = ReconciliationReport()
    _check_block_subtotals(doc, report, Decimal("1"), Decimal("0.001"), None)
    return report


def test_the_bare_subtotal_is_recovered_with_the_blocks_identity():
    """The figure exists, it carries the sub-heading it closes, and it is a SUBTOTAL."""
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, _BARE])

    assert len(items) == 3, _labels(items)
    sub = items[-1]
    assert sub.role is LineRole.SUBTOTAL
    assert "Current tax" in sub.raw_label
    assert sub.group_hint == "Current tax:"
    assert [str(v.value) for v in sub.values.values()] == ["3000"]


def test_the_recovered_row_keeps_its_own_box_to_click():
    """A computed figure has nowhere to send a reader. This one was printed, so it has a page and a
    box of its own — which is the whole difference between recovering the row and re-deriving the
    number from the rows above it."""
    sub = _note_items([_HEAD, _BLOCK, _D1, _D2, _BARE])[-1]

    prov = next(iter(sub.values.values())).provenance
    assert prov is not None
    assert prov.page_index == 3
    assert prov.value_bbox is not None
    # The box is the FIGURE's, not the block heading's: it is what the viewer scrolls to.
    assert prov.value_bbox.x0 == pytest.approx(0.70, abs=0.01)


def test_one_detail_row_does_not_promote_the_figure_under_it():
    """Two is the bar. One row plus a figure beneath it is just as likely a wrapped value or a
    comparative on its own line, and there is nothing for a subtotal to be a subtotal OF."""
    items = _note_items([_HEAD, _BLOCK, _D1, _BARE])

    assert [it.role for it in items] == [LineRole.LINE], _labels(items)
    assert not any(v.value == Decimal("3000") for it in items for v in it.values.values())


def test_a_figure_with_no_block_open_is_still_dropped():
    """No sub-heading, nothing to borrow an identity from. A bare figure under two ordinary rows is
    not a subtotal of anything the page named."""
    items = _note_items([_HEAD, _D1, _D2, _BARE])

    assert all(it.role is LineRole.LINE for it in items), _labels(items)
    assert len(items) == 2, _labels(items)


def test_a_three_column_note_matrix_is_not_flattened_into_detail_rows():
    """The Notes screen supports one label plus two comparative values, not an asset matrix."""
    class MatrixProvider:
        def complete_structured(self, **kwargs):
            return kwargs["response_schema"].model_validate({
                "columns": ["Opening", "Movement", "Closing"],
                "rows": [{"label": "Opening", "cells": [
                    {"column": "Opening", "value_text": "1"},
                    {"column": "Movement", "value_text": "127,999"},
                    {"column": "Closing", "value_text": "232,961"},
                ]}],
            }), {}

    matrix_header = [W("Opening", 0.60, 0.14, 0.05), W("Movement", 0.70, 0.14, 0.06),
                     W("Closing", 0.80, 0.14, 0.05)]
    matrix_row = [W("Opening", 0.12, 0.17, 0.05), W("1", 0.60, 0.17, 0.02),
                  W("127,999", 0.70, 0.17, 0.06), W("232,961", 0.80, 0.17, 0.06)]
    tables = extract_note_tables(_rows_to_words([_HEAD, matrix_header, matrix_row]), page_index=3,
                                 document_id="d", source_kind="pdf_native",
                                 llm_provider=MatrixProvider())

    assert [str(v.value) for v in tables[0].items[0].values.values()] == ["1", "127999", "232961"]


def test_a_comparative_note_keeps_deterministic_period_labels():
    class ComparativeProvider:
        def complete_structured(self, **kwargs):
            assert '"comparative_columns": ["Current year", "Prior year"]' in kwargs["messages"][0]["content"]
            assert "Return every schedule and movement row" in kwargs["messages"][0]["content"]
            return kwargs["response_schema"].model_validate({
                "columns": ["Current year", "Prior year"],
                "rows": [{"section": "Opening", "label": "Opening", "cells": [
                    {"column": "Current year", "value_text": "127,999"},
                    {"column": "Prior year", "value_text": "232,961"},
                ]}],
            }), {}

    row = [W("Opening", 0.12, 0.17, 0.05), W("127,999", 0.70, 0.17, 0.06),
           W("232,961", 0.80, 0.17, 0.06)]
    tables = extract_note_tables(_rows_to_words([_HEAD, row]), page_index=3,
                                 document_id="d", source_kind="pdf_native",
                                 llm_provider=ComparativeProvider())

    assert [value.period_label for value in tables[0].items[0].values.values()] == [
        "current", "prior"]


def test_repeated_comparative_movement_rows_are_merged():
    class ComparativeProvider:
        def complete_structured(self, **kwargs):
            return kwargs["response_schema"].model_validate({
                "columns": ["Current year", "Prior year"],
                "rows": [
                    {"section": "Opening", "label": "Movement", "cells": [
                        {"column": "Current year", "value_text": "127,999"}]},
                    {"section": "Opening", "label": "Movement", "cells": [
                        {"column": "Prior year", "value_text": "232,961"}]},
                ],
            }), {}

    row = [W("Opening", 0.12, 0.14, 0.05), W("Movement", 0.12, 0.17, 0.06),
           W("127,999", 0.70, 0.17, 0.06), W("232,961", 0.80, 0.17, 0.06)]
    tables = extract_note_tables(_rows_to_words([_HEAD, row]), page_index=3,
                                 document_id="d", source_kind="pdf_native",
                                 llm_provider=ComparativeProvider())

    assert len(tables[0].items) == 1
    assert [str(value.value) for value in tables[0].items[0].values.values()] == ["127999", "232961"]


def test_a_figure_outside_the_pages_value_columns_is_not_promoted():
    """A note reference printed in its own narrow column to the left is not a total. The page's
    value band is around x=0.70; this figure sits at 0.40 while the block's rows are in the band, so
    the column test has something to reject it with."""
    stray = [W("32", 0.40, 0.235, 0.03)]
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, stray])

    assert all(it.role is LineRole.LINE for it in items), _labels(items)


def test_a_right_edge_that_is_close_but_not_exact_is_the_same_column():
    """Real extraction does not give two right-aligned numbers the SAME right edge. A synthetic
    fixture does, so an exact-match rule would pass every test here and then reject the printed
    subtotal of every real filing. The slack is what makes the rule about a column rather than
    about a coordinate: this figure sits 0.012 of the page width off the block's edge — inside the
    slack, and well under the 0.08 that separates the measured filing's two period columns."""
    near = [W("3,000", 0.688, 0.235, 0.06)]         # x1 = 0.748 against the block's 0.76
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, near])

    assert items[-1].role is LineRole.SUBTOTAL, _labels(items)
    assert [str(v.value) for v in items[-1].values.values()] == ["3000"]


def test_a_right_edge_a_whole_column_away_is_a_different_column():
    """The other side of the same rule. A figure in the NEXT column along is not this block's
    total, and the slack must not be wide enough to reach it — 0.08 of the page width apart is
    what the measured filing's two period columns are."""
    far = [W("3,000", 0.78, 0.235, 0.06)]           # x1 = 0.84, a full column right of 0.76
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, far])

    assert all(it.role is LineRole.LINE for it in items), _labels(items)


def test_a_bare_figure_on_a_statement_face_is_left_dropped():
    """Notes only. A note is where an uncaptioned block subtotal is printed; a bare number on a
    statement face is far likelier a stray, and the face has a template to check its subtotals
    against in any case."""
    words = _rows_to_words([_BLOCK, _D1, _D2, _BARE])
    face, _ = build_line_items(words, page_index=0, document_id="d",
                               source_kind="pdf_native", on_face=True)
    notes, _ = build_line_items(words, page_index=0, document_id="d",
                                source_kind="pdf_native", on_face=False)

    assert all(li.role is LineRole.LINE for li in face), [li.source_label for li in face]
    assert any(li.role is LineRole.SUBTOTAL for li in notes)


def test_a_date_fragment_under_an_open_block_is_not_promoted():
    """`_is_noise_row` still runs on a promoted row. A period caption that leaked in as a row has
    a "value" that is a date fragment, and an open block above it must not make it a total."""
    year = [W("2024", 0.70, 0.235, 0.05)]
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, year])

    assert all(it.role is LineRole.LINE for it in items), _labels(items)


# --- the block ends on its own subtotal ------------------------------------------------------------

def test_the_recovery_changes_no_other_rows_sub_heading():
    """CLEARING THE BLOCK'S SCOPE WAS TRIED AND REVERTED, and this is the test that pins the revert.

    Closing `group` at the subtotal looked right — the real tax note prints "Deferred tax" straight
    after the current-tax subtotal, at the top level. But the same assignment fires for a row that
    is still INDENTED under the heading, and `group_hint` is the only thing that gives such a row a
    meaning: a bare "Mainland China" is a geography until the line above it makes it a tax figure.
    Stripping it there would take the mapper's fallback away from exactly the rows that need it.

    So recovering the bare figure changes no other row's scope. Whether a row after the subtotal is
    still IN the block is a separate question, answered by indent and only for membership.
    """
    after = [W("Mainland", 0.12, 0.27, 0.06), W("China", 0.19, 0.27, 0.04),
             W("500", 0.71, 0.27, 0.05)]
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, _BARE, after])

    tail = items[-1]
    assert tail.raw_label == "Mainland China"
    assert tail.group_hint == "Current tax:", "the recovery moved another row's scope"
    assert tail.role is LineRole.LINE
    # …and it is not a member of a block that has already been totalled.
    assert items[-2].component_ordinals == [0, 1], items[-2].component_ordinals


def test_a_row_set_flush_with_the_heading_is_not_one_of_its_components():
    """THE DEFECT A CAPTION TEST CANNOT REACH. A sub-heading's scope is not closed at the end of its
    block, so a top-level row printed below the block still carries the heading as its `group_hint`.
    A reader deriving the block's members from that caption pooled the top-level figure into the sum
    and reported an arithmetically correct note as broken.

    Membership is by INDENT instead: a block's details are printed inboard of the caption, and a row
    set flush with it has left the block whatever the scope still says. Here the flush 5,000 leaves
    the block with one member, so the bare figure below is refused rather than checked against the
    wrong rows — a refusal, not a guess.
    """
    flush = [W("Deferred", 0.10, 0.20, 0.06), W("tax", 0.17, 0.20, 0.03),
             W("5,000", 0.70, 0.20, 0.06)]
    bare = [W("1,000", 0.70, 0.235, 0.06)]
    items = _note_items([_HEAD, _BLOCK, _D1, flush, bare])

    assert all(it.role is LineRole.LINE for it in items), _labels(items)
    assert len(items) == 2, _labels(items)


def test_a_block_subtotal_in_a_note_reported_in_millions_is_still_recovered():
    """SMALL FIGURES ARE FIGURES. The promotion first reused the general noise test, called with the
    empty label the row still had at that point — and that function's label-less arm rejects any row
    whose every figure is 1-31 or 1990-2099. So a note reported in HK$ million, where 10 / 20 / 30
    are routine, had its printed block totals silently dropped exactly as before the fix. Narrowed
    to the bare year, which is what a leaked column heading actually is.
    """
    d1 = [W("Hong", 0.12, 0.17, 0.04), W("Kong", 0.17, 0.17, 0.04), W("10", 0.72, 0.17, 0.03)]
    d2 = [W("Mainland", 0.12, 0.20, 0.06), W("China", 0.19, 0.20, 0.04), W("20", 0.72, 0.20, 0.03)]
    bare = [W("30", 0.72, 0.235, 0.03)]
    items = _note_items([_HEAD, _BLOCK, d1, d2, bare])

    assert items[-1].role is LineRole.SUBTOTAL, _labels(items)
    assert [str(v.value) for v in items[-1].values.values()] == ["30"]


def test_a_new_block_does_not_inherit_the_rows_counted_before_it():
    """The count is what the promotion reads, so a new sub-heading has to start it at zero.

    The shape that proves it: two ordinary valued rows with NO block open, then a sub-heading, then
    a SINGLE detail row, then a bare figure. Without the reset the count is three by then and the
    figure is promoted into a total of one row — a total of the wrong rows, since the two above the
    heading belong to nothing. With the reset it is one, and the figure is refused.
    """
    loose1 = [W("Interest", 0.10, 0.14, 0.06), W("income", 0.17, 0.14, 0.05),
              W("100", 0.72, 0.14, 0.04)]
    loose2 = [W("Other", 0.10, 0.17, 0.05), W("income", 0.16, 0.17, 0.05),
              W("200", 0.72, 0.17, 0.04)]
    block = [W("Current", 0.10, 0.20, 0.06), W("tax:", 0.17, 0.20, 0.04)]
    only = [W("Mainland", 0.12, 0.23, 0.06), W("China", 0.19, 0.23, 0.04),
            W("500", 0.72, 0.23, 0.04)]
    bare = [W("500", 0.72, 0.265, 0.04)]
    items = _note_items([_HEAD, loose1, loose2, block, only, bare])

    assert all(it.role is LineRole.LINE for it in items), _labels(items)
    assert len(items) == 3, _labels(items)


def test_a_second_bare_figure_does_not_promote_against_a_closed_block():
    """Once a block has been totalled there is nothing left for a second bare figure to total."""
    second = [W("9,999", 0.70, 0.27, 0.06)]
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, _BARE, second])

    assert sum(1 for it in items if it.role is LineRole.SUBTOTAL) == 1, _labels(items)
    assert not any(v.value == Decimal("9999") for it in items for v in it.values.values())


# --- the arithmetic check --------------------------------------------------------------------------

def _run(pdf_bytes: bytes) -> tuple:
    ontology = load_ontology(
        json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")), resolve=True)
    template = load_template(
        json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))
    ctx = PipelineContext(raw_bytes=pdf_bytes)
    ctx.ontology, ctx.template = ontology, template
    doc = default_pipeline().run(DocumentModel(filename="tax.pdf", fmt=DocFormat.PDF), ctx)
    return doc, ctx


def test_the_printed_subtotal_is_checked_against_the_block_it_totals():
    """Both columns, on the real note's layout. 600 + 600 = 1,200 and 700 + 500 = 1,200 — and 1,200
    is deliberately NOT either period's current tax (1,600 / 1,200), so a check comparing the wrong
    two things could not pass by coincidence in the current column."""
    from tests.fixtures.generate import make_note_with_bare_block_subtotal_pdf

    doc, _ctx = _run(make_note_with_bare_block_subtotal_pdf())
    checks = doc.reconciliation.note_block_subtotals

    assert len(checks) == 2, checks
    by_period = {c.period_label: c for c in checks}
    assert set(by_period) == {"current", "prior"}
    for c in checks:
        assert c.note_number == "11"
        assert c.block == "Current charge for the year:"
        assert c.printed == Decimal("1200")
        assert c.computed == Decimal("1200")
        assert c.difference == Decimal("0")
        assert c.within_tolerance
        assert c.component_count == 2


def test_recovering_the_row_does_not_disturb_the_note_to_face_split():
    """THE REGRESSION THIS FIX COULD HAVE CAUSED. §20 reads this exact note, and the promoted row
    sits in the middle of the block it decomposes. Filed as a LINE it would double a component and
    the split would decline; excluded by its role, the answer is the one the filing printed."""
    from tests.fixtures.generate import make_note_with_bare_block_subtotal_pdf

    doc, ctx = _run(make_note_with_bare_block_subtotal_pdf())
    got = {(li.canonical_key, v.period_label): v.value
           for li in doc.line_items if li.canonical_key
           for v in li.values.values()}

    assert got[("pl_tax_expense__current_tax", "current")] == Decimal("-1600")
    assert got[("pl_tax_expense__current_tax", "prior")] == Decimal("-1200")
    assert got[("pl_tax_expense__deferred_tax", "current")] == Decimal("600")
    assert got[("pl_tax_expense__deferred_tax", "prior")] == Decimal("300")
    assert any("split(pl_tax_expense__total_tax_expense)" in line for line in ctx.logs), \
        "the §20 split stopped firing"


def test_the_recovered_row_is_not_summed_into_the_note_total():
    """THE OTHER READER OF THE ROLE, measured rather than argued.

    `reconcile` excludes SUBTOTAL/TOTAL from a note's details so a note's own total is not added to
    the rows it totals — and the promoted row IS one. The same note is reconciled twice here, once
    with the recovered row carrying the SUBTOTAL role it is given and once with it forced to LINE,
    and the second is the pre-fix behaviour: the subtracted figure moves by exactly the subtotal,
    which is the double count the exclusion exists to prevent."""
    from app.core.models.enums import Basis, LinkRelationship
    from app.core.models.line_item import (
        ExtractedValue,
        LineItem,
        FaceNoteLink,
        NoteItem,
        NotesTable,
    )
    from app.stages.reconcile import ReconcileStage

    def _doc(subtotal_role):
        def note_item(label, role, value):
            it = NoteItem(raw_label=label, ordinal=0, role=role, group_hint="Current tax:",
                          caption_borrowed=role is LineRole.SUBTOTAL)
            it.set_value(ExtractedValue(value_raw=Decimal(value), value=Decimal(value),
                                        basis=Basis.CONSOLIDATED, period_label="current"))
            return it

        note = NotesTable(note_number="8", title="Income tax", source_pages=[3])
        note.items = [note_item("A", LineRole.LINE, "1000"),
                      note_item("B", LineRole.LINE, "2000"),
                      note_item("Current tax — subtotal", subtotal_role, "3000")]
        face = LineItem(source_label="Income tax expense", ordinal=0, role=LineRole.LINE,
                        canonical_key="pl_tax_expense__total_tax_expense")
        face.set_value(ExtractedValue(value_raw=Decimal("3000"), value=Decimal("3000"),
                                      basis=Basis.CONSOLIDATED, period_label="current"))
        doc = DocumentModel(filename="f.pdf", fmt=DocFormat.PDF)
        doc.line_items, doc.notes = [face], [note]
        doc.links = [FaceNoteLink(face_item_id=face.id, notes_table_id=note.id, note_number="8",
                                  relationship=LinkRelationship.ONE_TO_ONE)]
        return ReconcileStage().run(doc, PipelineContext(raw_bytes=b""))

    kept = _doc(LineRole.SUBTOTAL).reconciliation.entries
    mis_roled = _doc(LineRole.LINE).reconciliation.entries
    assert kept and mis_roled

    # Excluded by its ROLE: the note's details are the two LINE rows, so the note total ties the
    # face exactly.
    assert kept[0].residual == Decimal("0"), kept[0]
    assert kept[0].tie_status == "tied", kept[0]

    # AND EXCLUDED BY THE ARITHMETIC WHEN THE ROLE IS WRONG, which is a second, independent
    # defence and is why this half no longer asserts a residual of -3,000.
    #
    # It used to. The assertion recorded what a mis-roled subtotal COSTS — it joins the rows it
    # totals and the note reports 6,000 against a 3,000 face — and it was a fair characterisation
    # while the role was the only thing standing between the two. It is not any more:
    # `reconcile._self_summing_detail` reads a detail that equals the sum of the others as the
    # note's own total when, and only when, dropping it makes the note tie the printed face. Here
    # it does (3,000 = 1,000 + 2,000, and 3,000 is the face), so the tie survives the wrong role.
    #
    # That matters because the role cannot always be right: a movement schedule prints its opening
    # and closing balances under the same words — both "At", with the date lost — so no caption
    # test can tell the total from a detail. On China SCE five one-to-one ties were exactly twice
    # the face for that reason.
    #
    # The WARNING that says which detail was re-read is asserted in `tests/test_reconcile`, not
    # here: `reconcile_face` produces it but `ReconciliationEntry` carries no `warnings` field, so
    # it does not survive the stage boundary.
    assert mis_roled[0].residual == Decimal("0"), mis_roled[0]
    assert mis_roled[0].tie_status == "tied", mis_roled[0]


def test_the_log_separates_a_block_subtotal_from_a_note_to_face_tie():
    """Two different claims. One number covering both would let a run with no face ties at all
    read as validated."""
    from tests.fixtures.generate import make_note_with_bare_block_subtotal_pdf

    _doc, ctx = _run(make_note_with_bare_block_subtotal_pdf())

    assert any("reconcile:block_subtotals=2 broken=0" in line for line in ctx.logs), \
        [line for line in ctx.logs if "reconcile" in line]


def test_a_block_that_does_not_add_up_is_reported_as_a_failed_assertion():
    """Built on the model rather than from a PDF, because the point is the arithmetic and not the
    layout: a printed figure that disagrees with its own components has to be said out loud rather
    than quietly replaced by the computed one."""
    report = _checked([
        _ni("A", LineRole.LINE, "1000", ordinal=0),
        _ni("B", LineRole.LINE, "2000", ordinal=1),
        _ni("Current tax", LineRole.SUBTOTAL, "3500", ordinal=2, components=(0, 1)),
    ])

    assert len(report.note_block_subtotals) == 1
    c = report.note_block_subtotals[0]
    assert c.printed == Decimal("3500") and c.computed == Decimal("3000")
    assert c.difference == Decimal("500") and not c.within_tolerance
    assert any("does not add up" in a for a in report.failed_assertions), report.failed_assertions


def test_a_block_with_one_component_is_not_recorded_as_checked():
    """A check that could not run must never read as one that ran and held — the same honesty the
    structural report's `skipped` rows keep. One component is not a check, and it is also what the
    builder records when a layout left it unsure which rows belong to the block."""
    report = _checked([
        _ni("A", LineRole.LINE, "1000", ordinal=0),
        _ni("Current tax", LineRole.SUBTOTAL, "1000", ordinal=1, components=(0,)),
    ])

    assert report.note_block_subtotals == []
    assert report.failed_assertions == []


def test_a_component_the_pruner_removed_does_not_leave_a_partial_check():
    """The member list is ordinals, and a note's rows can be filtered downstream. A subtotal whose
    components are no longer in the table is not checkable, and half of a sum is not a sum."""
    report = _checked([
        _ni("A", LineRole.LINE, "1000", ordinal=0),
        _ni("Current tax", LineRole.SUBTOTAL, "3000", ordinal=2, components=(0, 1)),
    ])

    assert report.note_block_subtotals == []
    assert report.failed_assertions == []


def test_a_ragged_column_is_not_checked_against_a_partial_sum():
    """A block whose rows are ragged in one period — one row printing a figure only for the current
    year — has no sum to compare in the other, and a partial sum would report a break the page does
    not contain."""
    report = _checked([
        _ni("A", LineRole.LINE, "1000", ordinal=0, prior="1000"),
        _ni("B", LineRole.LINE, "2000", ordinal=1),                  # no prior figure at all
        _ni("Current tax", LineRole.SUBTOTAL, "3000", ordinal=2, components=(0, 1), prior="1000"),
    ])

    periods = [c.period_label for c in report.note_block_subtotals]
    assert periods == ["current"], report.note_block_subtotals
    assert report.failed_assertions == []


def test_two_blocks_under_the_same_heading_are_checked_separately():
    """One note can print a "Current tax:" block for the Group and another for the Company, so the
    caption is not an identity. Each subtotal carries its OWN members, which is why the two cannot
    be confused however alike their headings are."""
    report = _checked([
        _ni("A", LineRole.LINE, "1000", ordinal=0), _ni("B", LineRole.LINE, "2000", ordinal=1),
        _ni("Current tax", LineRole.SUBTOTAL, "3000", ordinal=2, components=(0, 1)),
        _ni("C", LineRole.LINE, "40", ordinal=3), _ni("D", LineRole.LINE, "60", ordinal=4),
        _ni("Current tax", LineRole.SUBTOTAL, "100", ordinal=5, components=(3, 4)),
    ])

    got = [(str(c.printed), str(c.computed)) for c in report.note_block_subtotals]
    assert got == [("3000", "3000"), ("100", "100")], got
    assert all(c.within_tolerance for c in report.note_block_subtotals)


def test_a_note_is_checked_even_when_no_face_line_cites_it():
    """The check runs BEFORE the `doc.links` early exit. A note either adds up or it does not, and
    that is true of a filing whose face cites nothing — which is exactly the run where the face has
    told us least and the note's own arithmetic is worth most."""
    from app.core.models.line_item import NotesTable
    from app.stages.reconcile import ReconcileStage

    note = NotesTable(note_number="8", title="Income tax", source_pages=[3])
    note.items = [_ni("A", LineRole.LINE, "1000", ordinal=0),
                  _ni("B", LineRole.LINE, "2000", ordinal=1),
                  _ni("Current tax", LineRole.SUBTOTAL, "3500", ordinal=2, components=(0, 1))]
    doc = DocumentModel(filename="f.pdf", fmt=DocFormat.PDF)
    doc.notes = [note]
    assert not doc.links, "this test is about the no-links path"

    doc = ReconcileStage().run(doc, PipelineContext(raw_bytes=b""))

    assert len(doc.reconciliation.note_block_subtotals) == 1
    assert not doc.reconciliation.note_block_subtotals[0].within_tolerance
    assert doc.reconciliation.failed_assertions


def test_a_block_that_printed_no_subtotal_does_not_lend_its_rows_to_the_next_one():
    """THE DEFECT A CAPTION TEST CANNOT CATCH, because the captions are equal.

    A note prints the Group's "Current tax:" block and then the Company's, under the same heading,
    and only the second carries a printed subtotal. A reader deriving the members from the caption
    checked the second block's 100 against all four rows and reported it as not adding up — a break
    invented by the reader, in a note that is correct. The subtotal carries its own members, so the
    first block's rows are not reachable from it at all.
    """
    report = _checked([
        _ni("HK profits tax", LineRole.LINE, "1000", ordinal=0),   # Group block: no subtotal
        _ni("PRC EIT", LineRole.LINE, "2000", ordinal=1),
        _ni("Deferred tax", LineRole.LINE, "50", ordinal=2, group="Deferred tax:"),
        _ni("HK profits tax", LineRole.LINE, "40", ordinal=3),     # Company block, same heading
        _ni("PRC EIT", LineRole.LINE, "60", ordinal=4),
        _ni("Current tax", LineRole.SUBTOTAL, "100", ordinal=5, components=(3, 4)),
    ])

    assert len(report.note_block_subtotals) == 1, report.note_block_subtotals
    c = report.note_block_subtotals[0]
    assert c.printed == Decimal("100") and c.computed == Decimal("100")
    assert c.component_count == 2, "the earlier block's rows were pooled in"
    assert report.failed_assertions == [], report.failed_assertions


def test_a_subtotal_the_filing_captioned_itself_is_not_checked_as_a_sum():
    """A SCHEDULE THAT DERIVES IS NOT A SCHEDULE THAT ADDS, and the same tax note contains both.

    The effective-rate reconciliation derives the charge from profit before tax: "Profit before tax
    5,000 / at the statutory rate 1,250 / non-deductible 100 / Sub-total 1,350". `note_row_role`
    reads "Sub-total" off the caption and returns SUBTOTAL — correctly, it IS one — but summing the
    rows above it gives 6,350 and would report a break of the whole profit figure.

    So the check acts only on a row whose caption was BORROWED from the block it closes. That row is
    the only one whose geometry said "I am the total of the rows immediately above me"; a caption
    only says "I am a total", which is not the same claim.
    """
    report = _checked([
        _ni("Profit before tax", LineRole.LINE, "5000", group="", ordinal=0),
        _ni("At the statutory rate", LineRole.LINE, "1250", group="", ordinal=1),
        _ni("Expenses not deductible", LineRole.LINE, "100", group="", ordinal=2),
        _ni("Sub-total", LineRole.SUBTOTAL, "1350", group="", ordinal=3, borrowed=False,
            components=(0, 1, 2)),
    ])

    assert report.note_block_subtotals == []
    assert report.failed_assertions == []


def test_a_notes_grand_total_is_not_one_of_a_later_blocks_components():
    """A note's own grand total is not a detail of anything. The member list is recorded by the
    builder, which never counts a bare-caption promotion's own predecessors across a total — so a
    later block cannot reach back past it."""
    report = _checked([
        _ni("HK profits tax", LineRole.LINE, "1000", group="", ordinal=0),
        _ni("PRC EIT", LineRole.LINE, "2000", group="", ordinal=1),
        _ni("Total tax charge", LineRole.TOTAL, "3000", group="", ordinal=2),
        _ni("HK profits tax", LineRole.LINE, "40", group="", ordinal=3),
        _ni("PRC EIT", LineRole.LINE, "60", group="", ordinal=4),
        _ni("Current tax", LineRole.SUBTOTAL, "100", group="", ordinal=5, components=(3, 4)),
    ])

    assert len(report.note_block_subtotals) == 1, report.note_block_subtotals
    assert report.note_block_subtotals[0].component_count == 2
    assert report.failed_assertions == [], report.failed_assertions


def test_the_recovered_caption_is_the_filings_own_words_and_nothing_else():
    """No injected English. An earlier version appended the word "subtotal", which put untranslated
    English inside a caption whose other half is the filing's words — and this product renders that
    caption in four locales. What says the row is a total is its role."""
    sub = _note_items([_HEAD, _BLOCK, _D1, _D2, _BARE])[-1]

    assert sub.raw_label == "Current tax"
    assert sub.raw_label.isascii()          # this fixture's heading is English; the point is below
    assert "subtotal" not in sub.raw_label.lower()
    assert "total" not in sub.raw_label.lower()
    # The claim is carried by the role and the marker, not by a word in one language.
    assert sub.role is LineRole.SUBTOTAL
    assert sub.caption_borrowed is True


def test_a_row_the_filing_captioned_is_not_marked_as_borrowed():
    """The marker has to mean something, so it must be false everywhere else."""
    items = _note_items([_HEAD, _BLOCK, _D1, _D2, _BARE])

    assert [it.caption_borrowed for it in items] == [False, False, True], _labels(items)


def test_the_outcome_is_served_with_the_run_and_not_only_logged():
    """A CHECK WHOSE RESULT ONLY REACHES A LOG LINE IS NOT A CHECK.

    The first version of this recorded the outcome on the report and logged a count, and nothing
    downstream read either — so a filing whose note block was out by 500 produced one line in a log
    and no other trace. The whole reason to recover the printed figure rather than keep re-deriving
    it is that a disagreement reaches a person, so the payload has to carry it.
    """
    from app.api.routes.extractions import _serialize_notes  # noqa: F401  (import-time contract)
    from tests.fixtures.generate import make_note_with_bare_block_subtotal_pdf

    doc, _ctx = _run(make_note_with_bare_block_subtotal_pdf())
    served = [c.model_dump(mode="json") for c in doc.reconciliation.note_block_subtotals]

    assert len(served) == 2, served
    for c in served:
        # Every field a reader needs to act without re-deriving anything: which note, which block,
        # which column, both figures and the difference between them.
        assert set(c) == {"note_number", "block", "basis", "period_label", "printed", "computed",
                          "difference", "within_tolerance", "component_count"}, sorted(c)
        assert c["note_number"] == "11"
        assert c["printed"] == "1200" and c["computed"] == "1200"

    # And the key the run result carries them under exists in the serializer.
    import inspect

    from app.api.routes import extractions
    src = inspect.getsource(extractions)
    assert '"note_block_subtotals"' in src, "the run result does not serve the checks"


def test_a_break_is_named_in_prose_a_reader_can_act_on():
    """The message has to stand on its own, because it is what a person sees. `failed_assertions` is
    where every reconciliation failure is already collected; note that nothing in the application
    currently READS that list — the serving route out is `note_block_subtotals` on the run result —
    so this asserts the message's content, not its delivery."""
    report = _checked([
        _ni("HK profits tax", LineRole.LINE, "1000", ordinal=0),
        _ni("PRC EIT", LineRole.LINE, "2000", ordinal=1),
        _ni("Current tax", LineRole.SUBTOTAL, "3500", ordinal=2, components=(0, 1)),
    ])

    assert len(report.failed_assertions) == 1, report.failed_assertions
    msg = report.failed_assertions[0]
    # Which note, which block, which column, both figures and the gap — no cross-referencing.
    for fragment in ("Note 8", "Current tax:", "consolidated/current", "3500", "3000", "500"):
        assert fragment in msg, (fragment, msg)


def test_the_borrowed_caption_brings_the_headings_box_so_a_verdict_can_be_recorded():
    """WITHOUT THIS THE ONE ROW A REVIEWER IS MOST LIKELY TO CORRECT IS THE ONE WHOSE VERDICT
    CANNOT BE RECORDED.

    A promoted row has no label words of its own, so `label_bbox` was None — and `_prov_anchor`
    then falls back to the value box's vertical BAND alone, which two sub-tables printed on one
    baseline share. `judgement.apply_judgements` refuses to attribute an acceptance to a shared
    anchor, and serves the finding as a conflict instead. The caption is the block heading's, so
    the heading's box is both available and the honest one to cite.
    """
    from app.api.routes.documents import _prov_anchor

    sub = _note_items([_HEAD, _BLOCK, _D1, _D2, _BARE])[-1]
    prov = next(iter(sub.values.values())).provenance

    assert prov.label_bbox is not None, "the promoted row cites no label geometry"
    # The heading's box, not the figure's: the caption came from the heading row.
    assert prov.label_bbox.y0 == pytest.approx(_BLOCK[0].bbox.y0, abs=0.001)
    assert prov.label_bbox.x0 == pytest.approx(_BLOCK[0].bbox.x0, abs=0.001)

    anchor = _prov_anchor({"source_kind": "pdf", "page_index": prov.page_index,
                           "label_bbox": prov.label_bbox.model_dump()})
    # A real within-source anchor, not either documented no-geometry sentinel.
    assert not anchor.endswith("#nobox"), anchor
    assert anchor != "#noprov", anchor


def test_the_note_payload_carries_the_keys_a_reader_needs_to_join_a_check_to_its_rows():
    """`note_block_subtotals` names its block by `group_hint` and its members by ordinal. Neither
    was served, so a consumer of that list had no way to find the block or its rows in the note
    payload — and a borrowed caption was served indistinguishably from a printed one, which is a
    caption that appears nowhere on the page in that position, unmarked."""
    from app.api.routes.extractions import _serialize_notes
    from tests.fixtures.generate import make_note_with_bare_block_subtotal_pdf

    doc, _ctx = _run(make_note_with_bare_block_subtotal_pdf())
    served = _serialize_notes(doc)
    rows = [r for n in served for r in n["rows"]]
    assert rows, "the note serializer produced no rows"

    borrowed = [r for r in rows if r.get("caption_borrowed")]
    assert len(borrowed) == 1, [(r["label"], r.get("caption_borrowed")) for r in rows]
    row = borrowed[0]
    assert row["role"] == "subtotal"
    # The block, spelled the same way the check spells it, so the two can be joined.
    check = doc.reconciliation.note_block_subtotals[0]
    assert row["group"] == check.block
    # And the members, by the ordinal the same payload gives every row.
    ordinals = {r["ordinal"] for r in rows}
    assert row["component_ordinals"], row
    assert set(row["component_ordinals"]) <= ordinals, (row["component_ordinals"], ordinals)
    assert len(row["component_ordinals"]) == check.component_count

    # Every other row says its caption is its own.
    assert all(r.get("caption_borrowed") is False for r in rows if r is not row)


# ── the net-cash-flow summary, which is a total whose caption does not open with a total word ────

def test_a_net_cash_flow_summary_is_read_as_the_notes_total():
    """A DISPOSAL NOTE CLOSES ITS CASH BLOCK WITH A SUMMARY, not with the word "total".

        Cash and cash equivalents disposed of            (3,902)
        Cash consideration                              200,209
        Consideration receivables                             –
        Net inflow of cash and cash equivalents ...      196,307   <- the total of the three

    200,209 - 3,902 = 196,307 exactly. Filed as a detail it is summed WITH the rows it totals, so
    the note total came to twice the figure the face cites — measured on China SCE note 39, in both
    periods (196,307 -> 392,614 and 585,780 -> 1,171,560), and the tie could never confirm.

    Narrow on purpose: "Net" opens plenty of real detail captions, so only the cash-flow summary
    forms match and `of cash` is required rather than assumed.
    """
    from app.core.models.enums import LineRole
    from app.services.notes_extract import note_row_role

    for caption in ("Net inflow of cash and cash equivalents in respect of the disposal",
                    "Net outflow of cash and cash equivalents in respect of the acquisition",
                    "Net inflow/(outflow) of cash and cash equivalents in respect of",
                    "現金及現金等價物流入淨額"):
        assert note_row_role(caption) is LineRole.TOTAL, caption

    # …and the captions that must STAY details. Each one opens with "Net" or "Cash" and none of
    # them is a total; a filing prints every one of these as an ordinary row.
    for caption in ("Net assets disposed of", "Net trade receivables", "Net book value",
                    "Net cash consideration", "Cash consideration 現金代價",
                    "Cash and cash equivalents disposed of",
                    "Net increase in cash and cash equivalents"):
        assert note_row_role(caption) is LineRole.LINE, caption
