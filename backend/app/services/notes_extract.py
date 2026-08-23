"""Extract the NOTE detail tables from notes pages.

A note like "Note 15: Trade receivables" is followed by its own breakdown (the rows that
give the detail behind the face figure). This groups a notes page's words into note
sections by their headings and reconstructs each note's detail line items — free-format,
whatever rows the note contains — keeping page + bbox provenance. The result populates the
``NotesTable``/``NoteItem`` model so the All-Notes view and export can show the real detail.
"""
from __future__ import annotations

import re

from app.core.models.line_item import NoteItem, NotesTable
from app.services.row_reconstruct import (
    Word, _group_rows, _scan_row, build_line_items, row_tolerance)

# "Note 15: Trade receivables", "Note 15 Trade receivables", "15. Trade receivables"
_HEADING = re.compile(r"^(?:note[s]?\.?\s+)?(?P<no>\d{1,3})\s*[:.\)\-]?\s*(?P<title>.*)$",
                      re.IGNORECASE)


def _is_heading(row: list[Word]) -> tuple[str, str] | None:
    """A heading row names a note (number + optional title) and carries no value column of
    its own — that's what separates 'Note 15: Trade receivables' from a data row."""
    _, _, values = _scan_row(row)
    if values:
        return None
    text = " ".join(w.text for w in row).strip()
    starts_note = text.lower().startswith("note")
    m = _HEADING.match(text)
    if not m:
        return None
    no = m.group("no")
    title = m.group("title").strip(" :.-")
    # Require an explicit "Note" prefix OR a title, so a bare number isn't a false heading.
    if not starts_note and not title:
        return None
    return no, title


def extract_note_tables(words: list[Word], *, page_index: int, document_id: str | None,
                        source_kind: str, scope=None,
                        normalisation=None) -> list[NotesTable]:
    """Split a notes page into note sections and reconstruct each note's detail rows.

    ``scope``/``normalisation`` are the run's own rulebook blocks; a note's columns are read by
    the same rules as the face it supports, or the note→face tie compares figures taken from
    different columns.
    """
    # The same page-derived tolerance the face uses: a note's detail lines are set as tightly as a
    # statement's, and two of them merged into one row interleave their captions (row_reconstruct.
    # row_tolerance). A note whose caption comes out scrambled ties to nothing.
    rows = _group_rows(words, row_tolerance(words, source_kind))
    sections: list[dict] = []
    current: dict | None = None
    for row in rows:
        head = _is_heading(row)
        if head is not None:
            no, title = head
            current = {"no": no, "title": title, "words": []}
            sections.append(current)
        elif current is not None:
            current["words"].extend(row)

    tables: list[NotesTable] = []
    for sec in sections:
        # ``on_face=False``: the rulebook's ``company_only_markers`` rule is declared about the
        # FACE ("presence of …investments_in_subsidiaries on the face is strong evidence the
        # column is company-only"). A note IS where that caption is normally printed, and reading
        # it here would relabel the note of a consolidated statement as the Company's — breaking
        # the note→face tie, which matches on (basis, period).
        items, _ = build_line_items(sec["words"], page_index=page_index,
                                    document_id=document_id, source_kind=source_kind,
                                    on_face=False, scope=scope, normalisation=normalisation)
        if not items and not sec["title"]:
            continue
        table = NotesTable(note_number=sec["no"], title=sec["title"], source_pages=[page_index])
        for li in items:
            ni = NoteItem(raw_label=li.source_label, ordinal=li.ordinal, role=li.role,
                          section_hint=li.section_hint,
                          provenance=li.values and next(iter(li.values.values())).provenance or None)
            for ev in li.values.values():
                ni.set_value(ev)
            table.items.append(ni)
        tables.append(table)
    return tables
