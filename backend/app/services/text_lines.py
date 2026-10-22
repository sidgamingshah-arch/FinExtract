"""TEXT-VALUED LINES — the lines whose value is words the filing prints, not a figure.

NEW FILE -> backend/app/services/text_lines.py

WHAT WAS WRONG. The auditor's opinion, the entity's name, the presentation currency, the accounting
framework, the auditor, the reporting date: each is a line of the template, and each was sent to
the model as if it were a figure. The request carried the line and nothing else — no page, no note,
no statement row, because none of those blocks is selected for a line on no statement — and the
reply contract said "Do NOT state a figure. You are locating a printed number", with no field a
text could go in. So the question could not be answered, and every one of those requests came back
empty by construction.

WHAT A TEXT LINE IS. `LineItemDef.output_structure == "phrase"`: words lifted FROM the page
(`schemas.line_items.OutputStructure`). Such a line is asked in a request of its own kind:

  * IT CARRIES THE PAGES THAT PRINT IT (`text_pages`): the page text of the pages that name the
    line, selected by the line's printed names (`line_item_notes.printed_names` — its aliases in
    every locale), as the `anywhere` route selects its pages for a share count
    (`face_context.other_page_rows`). Not every page: the pages that print most of the names first,
    at most `TEXT_PAGES_CAP`.
  * ITS REPLY IS THE TEXT, AS PRINTED, WITH ITS PAGE (`line_item_llm.LineItemTextReply`), and the
    text is CHECKED AGAINST THAT PAGE'S TEXT (`locate`) and refused when it is not there — the
    prose amount's rule (`note_sourced.resolve_sources`) applied to words: the model LOCATES what
    the page prints, it does not write it.

A `prose` line (text the model GENERATES from the line's prompt) is not one of these: nothing it
writes can be checked against a page, and no shipped line declares it.
"""
from __future__ import annotations

import re
import unicodedata

#: At most this many pages go with one text line. The value is printed on one page; the others are
#: there because a name is printed on more than one (the auditor on the corporate-information page
#: and at the foot of the auditor's report).
TEXT_PAGES_CAP = 3
#: A page's text is cut at this many characters. An annual report's densest page is about 5,000;
#: the cap stops a page that is mostly a table from being the request.
TEXT_PAGE_CHARS = 8000
#: A printed name shorter than this (folded) selects nothing: "Opinion" and "Units" are printed on
#: half the pages of a report, and a name that names every page selects none.
MIN_NAME_CHARS = 4

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                         "­": None})
_SPACE = re.compile(r"\s+")
#: A word broken over a line end: "finan-\ncial".
_HYPHEN_BREAK = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")


def is_text_line(item) -> bool:
    """A line whose value is words lifted off the page (`output_structure: phrase`)."""
    return str(getattr(item, "output_structure", "value") or "value") == "phrase"


def fold(text) -> str:
    """The comparison form of printed text: NFKC, curly quotes straightened, soft hyphens and every
    space removed, case folded. A PDF text layer breaks a sentence over lines and spaces CJK text
    at random ("规 定编制"), so spacing is never part of what was printed."""
    t = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTES)
    return _SPACE.sub("", t).casefold()


def _forms(page_text: str) -> tuple[str, ...]:
    """The page folded as printed, and with words broken over a line end rejoined."""
    return fold(page_text), fold(_HYPHEN_BREAK.sub("", page_text or ""))


def select_pages(page_texts, names, *, cap: int = TEXT_PAGES_CAP) -> list[int]:
    """The 0-based indices of the pages that print the most of `names`, at most `cap`, in page order.

    A page counts a name when the name, folded, is in the page's folded text. Ranked by how many
    different names the page prints, then by how often, then by position — so the auditor's report,
    which prints "Independent Auditor's Report", "Opinion" and "Basis for opinion", outranks the
    governance page that mentions the auditor once. A name under `MIN_NAME_CHARS` is not used.
    """
    wanted = [n for n in dict.fromkeys(fold(x) for x in names or ()) if len(n) >= MIN_NAME_CHARS]
    if not wanted:
        return []
    scored = []
    for index, text in page_texts or ():
        folded = fold(text)
        hits = [folded.count(n) for n in wanted]
        distinct = sum(1 for h in hits if h)
        if distinct:
            scored.append((-distinct, -sum(hits), int(index)))
    chosen = sorted(scored)[:max(cap, 0)]
    return sorted(i for _d, _n, i in chosen)


def text_pages(page_texts, names, doc=None, *, cap: int = TEXT_PAGES_CAP,
               chars: int = TEXT_PAGE_CHARS) -> list[dict]:
    """The `text_pages` block of a text line's request: ``[{"page", "printed_page"?, "text"}]``.

    `page` is the page's 1-BASED POSITION IN THE FILE, the number a citation copies back — the same
    convention as `face_context.other_page_rows`. `printed_page` is the folio, when the page prints
    one and the document knows it.
    """
    by_index = {int(i): t for i, t in page_texts or ()}
    folios = {}
    for p in (getattr(doc, "pages", None) or ()):
        folio = str(getattr(p, "printed_page", "") or "").strip()
        if folio:
            folios[int(getattr(p, "index", -1))] = folio
    out = []
    for index in select_pages(page_texts, names, cap=cap):
        text = (by_index.get(index) or "").strip()
        entry: dict = {"page": index + 1}
        if index in folios:
            entry["printed_page"] = folios[index]
        entry["text"] = text[:chars]
        out.append(entry)
    return out


def locate(text: str, page: int | None, supplied: list[dict]) -> int | None:
    """The 1-based page of `supplied` that prints `text`, or None — the check a text answer passes.

    The page the answer names is tried first, and it must be one the request supplied: a page
    nobody sent cannot be where the model read it. With no page named, the first supplied page that
    prints the text is taken. Compared folded (`fold`), so line breaks and spacing do not count and
    every printed character does.
    """
    want = fold(text)
    if not want:
        return None
    pages = [b for b in supplied or () if b.get("page") is not None]
    if page is not None:
        pages = [b for b in pages if int(b["page"]) == int(page)]
    for block in pages:
        if any(want in form for form in _forms(str(block.get("text") or ""))):
            return int(block["page"])
    return None
