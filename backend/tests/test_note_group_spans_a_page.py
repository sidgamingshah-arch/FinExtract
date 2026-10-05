"""A note's sub-heading is printed once, and its rows run until the next one — across a page break.

``row_reconstruct`` tracks the heading in a page-local variable, so the rows on the far side of a
break lost it. That matters because the heading is the only thing that gives some rows a meaning
at all: a mainland related-party note prints

    ②应付项目
      长期应付款：                                  <- the group; the caption an author writes
    ---- page break ----
      河钢融资租赁有限公司   856,059,679.92          <- the row; a company name
      合计                 856,059,679.92

and ``services.note_sourced.select_rows`` matches a row by its own caption OR by its group. With
the group lost, that counterparty row could be reached by neither, and
``bs_ncl__due_to_related_parties_ltp`` — the long-term payable to related parties — published
nothing on 000709 although the filing states it plainly. The same break loses 其他应收款 118,850.00
on the receivable side of the same note.

THE CARRY IS RESET ON THE SAME BOUNDARY AS THE NOTE. ``pdf_extract`` clears it when a non-notes
page intervenes, and ``notes_extract`` offers it only to the section that CONTINUES the note the
previous page left open — so a page that opens a new note starts with no heading, and a heading
cannot leak onto a table it does not belong to.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items


def _w(text: str, x: float, y: float, width: float = 0.06) -> Word:
    return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + 0.012))


def _build(words, **kw):
    logs: list[str] = []
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                                on_face=False, log=logs.append, **kw)
    return items


def _heading_and_two_rows():
    """The shape the defect was measured on: a group heading, then indented counterparty rows."""
    return [
        _w("长期应付款：", 0.10, 0.05, 0.10),
        _w("河钢融资租赁有限公司", 0.14, 0.09, 0.14), _w("856,059,679.92", 0.74, 0.09),
        _w("唐山唐钢气体有限公司", 0.14, 0.13, 0.14), _w("54,600,891.70", 0.74, 0.13),
    ]


def _rows_only():
    """The far side of the break: the same rows with their heading left on the page before."""
    return [
        _w("河钢融资租赁有限公司", 0.14, 0.09, 0.14), _w("856,059,679.92", 0.74, 0.09),
        _w("唐山唐钢气体有限公司", 0.14, 0.13, 0.14), _w("54,600,891.70", 0.74, 0.13),
    ]


# --- the heading reaches the rows on the next page ----------------------------------------------

def test_a_row_on_the_next_page_inherits_the_heading():
    items = _build(_rows_only(), carry_group="长期应付款：")

    assert [i.source_label for i in items] == ["河钢融资租赁有限公司", "唐山唐钢气体有限公司"]
    assert {i.group_hint for i in items} == {"长期应付款："}


def test_without_the_carry_those_rows_have_no_group_at_all():
    """The defect, stated as the difference the carry makes."""
    items = _build(_rows_only())

    assert [i.source_label for i in items] == ["河钢融资租赁有限公司", "唐山唐钢气体有限公司"]
    assert {i.group_hint for i in items} == {""}


def test_the_page_reports_the_heading_still_open_when_it_ended():
    """What the caller carries forward — the same shape as ``grid_out``."""
    out: list[str] = []
    _build(_heading_and_two_rows(), group_out=out)

    assert out == ["长期应付款："]


def test_a_page_whose_heading_closed_reports_nothing_to_carry():
    """A section banner ends a sub-heading's scope, so there is nothing open at the page's end and
    the next page must not inherit one."""
    words = _heading_and_two_rows() + [_w("Current liabilities", 0.10, 0.17, 0.16)]
    out: list[str] = []
    _build(words, group_out=out)

    assert out == [""]


# --- and a heading on the page itself still wins -------------------------------------------------

def test_a_heading_printed_on_this_page_replaces_the_carried_one():
    """The carry seeds the page; it does not override what the page prints."""
    items = _build(_heading_and_two_rows(), carry_group="其他应付款：")

    assert {i.group_hint for i in items} == {"长期应付款："}


def test_the_carry_is_offered_only_to_the_section_that_continues_the_note():
    """Stated on the caller, because that is where the boundary is. ``notes_extract`` passes
    ``carry_group`` for the CARRIED section alone — the one continuing the note the previous page
    left open — and ``pdf_extract`` clears it when a non-notes page intervenes."""
    import inspect

    from app.services import notes_extract, pdf_extract

    notes = inspect.getsource(notes_extract.extract_note_tables)
    assert "carry_group=(carry_group if sec is carried else None)" in notes
    extract = inspect.getsource(pdf_extract.extract_pdf)
    # The notes reader is called through `_read_notes`, which a NOTES page and the note text above
    # a cash-flow supplement's title share (see `stages.classify._cf_supplement_extent`). The facts
    # pinned are unchanged: the carried group is what is passed, and what comes back is the last
    # section's group — None included. (The printed-column header now travels as a fourth carry
    # beside it, `notes_header`, on the same boundary; see test_every_printed_column_is_named.)
    assert "carry_group=carry_group, group_out=groups" in extract
    assert "notes_carry, notes_grid, notes_group, notes_header = _read_notes(" in extract
    assert "notes_carry, notes_grid, notes_group,\n                notes_header)" in extract
    # Reset on the same break as the note and the grid.
    assert extract.count("notes_group = None") == 1
    assert "groups[-1] if groups else None" in extract
