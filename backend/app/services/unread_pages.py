"""The pages a run did not read, and why, so that an unread page is never taken for an absent one.

A cash-flow page left out of the page selection, a balance sheet whose section was switched off and
a scanned page with no OCR engine to read it all produce the same thing downstream: no rows. Without
a record of it, a statement nobody read reads exactly like a statement the filing does not contain,
and the coverage report files its relations as STATEMENT_ABSENT, outside the denominator, so the
validation looks complete. The reader records each page it meant to read and did not; the run keeps
the list as ``result.unread_pages``, and coverage, the Workspace and the export read it from there.

One entry per page, ``{page, folio, reason, sections}``:

* ``page`` is the 1-based sheet number (the number Page Scope shows under the page), ``folio`` the
  page number printed on the paper, or None when the page prints none;
* ``reason`` is ``out_of_scope`` (the page selection leaves it out), ``section_off`` (every section
  it is read for is switched off) or ``scanned_no_ocr`` (it has no text layer and no OCR engine read
  it; ``ocr`` then says whether OCR is switched ``off`` or no engine is set up, ``none``);
* ``sections`` are the sections the page would have been read for (statement keys and "notes").

Only pages the reader TARGETS are listed. A directors' report page is not read on any run and is not
an omission, so it is not here.
"""
from __future__ import annotations

from app.services.page_reading import page_reads, section_of
from app.services.statements import ACTIVE_STATEMENTS

OUT_OF_SCOPE = "out_of_scope"
SECTION_OFF = "section_off"
SCANNED_NO_OCR = "scanned_no_ocr"
REASONS = (OUT_OF_SCOPE, SECTION_OFF, SCANNED_NO_OCR)


def _get(p, key, default=None):
    if isinstance(p, dict):
        return p.get(key, default)
    v = getattr(p, key, default)
    return getattr(v, "value", v)


def page_sections(p) -> list[str]:
    """The sections a page is read for when nothing is switched off, in reading order."""
    out: list[str] = []
    for part in page_reads(p, True):
        s = section_of(part)
        if s and s not in out:
            out.append(s)
    return out


def entry(p, reason: str, *, ocr: str | None = None) -> dict:
    """One unread page, from a ``PageSource`` or a persisted page dict."""
    out = {"page": int(_get(p, "index", 0) or 0) + 1,
           "folio": _get(p, "printed_page") or None,
           "reason": reason,
           "sections": page_sections(p)}
    if ocr:
        out["ocr"] = ocr
    return out


def statements_not_read(unread: list[dict] | None, sections_off=None, pages=None) -> set[str]:
    """The statements this run did not read on at least one page meant for them.

    The sections of every unread page, and every statement section switched off that the classifier
    found on some page: a page shared between a switched-off statement and one still on is read for
    the second only, so it is not an unread page, and the first statement was still not read.
    """
    out = {s for e in (unread or []) for s in (e.get("sections") or []) if s in ACTIVE_STATEMENTS}
    off = set(sections_off or ()) & ACTIVE_STATEMENTS
    if off:
        found = {s for p in (pages or []) for s in page_sections(p)}
        out |= off & found
    return out


def page_label(e: dict) -> str:
    """The page as the rest of the product cites it: the printed folio, else the sheet number."""
    return f"p.{e.get('folio') or e.get('page')}"
