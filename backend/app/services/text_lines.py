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
    at most `TEXT_PAGES_CAP` — and AHEAD OF THEM the pages the line's definition names
    (`anchored_pages`): the statement face the run identified under a title the line names, and
    the opening page of the auditor's report when the line names the report. Ranked by count
    alone, the reporting date was sent three accounting-policy pages that say 资产负债表日 and
    never the balance sheet that prints 2024年12月31日.
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


def select_pages(page_texts, names, *, cap: int = TEXT_PAGES_CAP, anchored=()) -> list[int]:
    """The 0-based indices of the pages that print the most of `names`, at most `cap`, in page order.

    A page counts a name when the name, folded, is in the page's folded text. Ranked by how many
    different names the page prints, then by how often, then by position — so the auditor's report,
    which prints "Independent Auditor's Report", "Opinion" and "Basis for opinion", outranks the
    governance page that mentions the auditor once. A name under `MIN_NAME_CHARS` is not used.

    `anchored` (`anchored_pages`, ``{index: tier}``; a plain sequence is all tier 0) are the pages
    the line's DEFINITION names — the statement face the run identified by a title the line
    names, the auditor's report — and they are ranked before every other page, tier 0 before
    tier 1, by the same score within a tier, inside the same `cap`: a page that prints a name many
    times (an accounting-policy page that says 资产负债表日 eight times) no longer displaces the
    page that prints the value.
    """
    wanted = [n for n in dict.fromkeys(fold(x) for x in names or ()) if len(n) >= MIN_NAME_CHARS]
    if not wanted:
        return []
    tiers = (dict(anchored) if hasattr(anchored, "items")
             else {int(i): 0 for i in anchored or ()})
    scored = []
    for index, text in page_texts or ():
        folded = fold(text)
        hits = [folded.count(n) for n in wanted]
        distinct = sum(1 for h in hits if h)
        tier = tiers.get(int(index), 2)
        if distinct or tier < 2:
            scored.append((tier, -distinct, -sum(hits), int(index)))
    chosen = sorted(scored)[:max(cap, 0)]
    return sorted(i for _t, _d, _n, i in chosen)


#: The titles an auditor's report prints at its head, folded when compared. A line NAMES the
#: auditor's report when one of its printed names contains one of these ("Independent Auditor's
#: Report", 审计报告).
REPORT_TITLES = ("审计报告", "審計報告", "核數師報告", "Auditor's Report", "Auditors' Report")
#: The auditor's report opens at most this many pages before the statements it reports on. The
#: longest in the corpus (hkex_1966) is ten pages.
REPORT_WINDOW = 15
#: A page OPENS the auditor's report when the report's title is printed within this many leading
#: characters of its folded text — straight under the running header. In the corpus the opening
#: page prints it within the first 100 (一、审计报告 on a Shenzhen-format report, 审计报告 on a
#: Shanghai one, "INDEPENDENT AUDITOR'S REPORT" on an HKEX one); a governance page that refers to
#: "the Independent Auditor's Report on pages 53 to 60" does so in a sentence, further down.
REPORT_TOP_CHARS = 150


def _face_titles(doc) -> dict[int, str]:
    """``{0-based index: folded title}`` of the statement face pages the run classified, by the
    title the classifier matched on each (`PageSource.evidence["matched_title"]`). A continuation
    page, which prints no title, is not in it."""
    from app.core.models.enums import PageKind

    out = {}
    for p in getattr(doc, "pages", None) or ():
        if getattr(p, "kind", None) not in (PageKind.FACE, PageKind.FACE.value):
            continue
        title = fold((getattr(p, "evidence", None) or {}).get("matched_title") or "")
        if title:
            out[int(getattr(p, "index", -1))] = title
    return out


def _statement_starts(doc) -> list[int]:
    """The 0-based indices where a run of statement face pages begins: a face page of a named
    statement whose preceding page is not one."""
    from app.core.models.enums import PageKind

    faces = {int(getattr(p, "index", -1)) for p in getattr(doc, "pages", None) or ()
             if getattr(p, "kind", None) in (PageKind.FACE, PageKind.FACE.value)
             and getattr(p, "statement", None)}
    return sorted(i for i in faces if i - 1 not in faces)


def report_openings(page_texts, doc) -> list[int]:
    """The 0-based indices of the pages that OPEN an auditor's report: for each run of statement
    faces (`_statement_starts`), the earliest page within `REPORT_WINDOW` pages before it whose
    head (`REPORT_TOP_CHARS`) prints a `REPORT_TITLES` title. A filing that prints two sets of
    statements (PetroChina: CAS, then IFRS) has two reports, and both are found."""
    titles = [fold(t) for t in REPORT_TITLES]
    heads = {int(i): fold(t)[:REPORT_TOP_CHARS] for i, t in page_texts or ()}
    out = []
    for start in _statement_starts(doc):
        for i in range(max(start - REPORT_WINDOW, 0), start):
            if i in heads and any(t in heads[i] for t in titles):
                out.append(i)
                break
    return sorted(set(out))


def anchored_pages(page_texts, names, doc) -> dict[int, int]:
    """``{0-based index: tier}``: the pages a text line's names DESIGNATE, as opposed to merely
    print —

      * a statement face page the run identified under a title that contains one of the names
        (合并资产负债表 is the title of 1、合并资产负债表; "Consolidated Statement of Financial
        Position" the title of the HKFRS face) — where the statements print their date, their
        unit, their comparative column and the scope word of their title. The FIRST page titled
        with each name is tier 0, where the statement's heading is printed; a further page
        printing the same title (a balance sheet over three pages, each headed) is tier 1, so
        two named statements both reach the request before either one's second page;
      * the opening page of the auditor's report (`report_openings`), tier 0, when one of the
        names is the report's title — where the opinion paragraph names the statements' date and
        the opinion.

    A name printed on a page is not enough: "Annual Report" is printed in the running header of
    every face page, and 年度报告 in the header of every mainland page."""
    wanted = [n for n in dict.fromkeys(fold(x) for x in names or ()) if len(n) >= MIN_NAME_CHARS]
    if not wanted or doc is None:
        return {}
    out: dict[int, int] = {}
    titled = sorted(_face_titles(doc).items())
    for name in wanted:
        pages = [i for i, title in titled if name in title]
        for rank, i in enumerate(pages):
            out[i] = min(out.get(i, 1), 0 if rank == 0 else 1)
    if any(fold(t) in n for n in wanted for t in REPORT_TITLES):
        for i in report_openings(page_texts, doc):
            out[i] = 0
    return out


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
    anchored = anchored_pages(page_texts, names, doc)
    for index in select_pages(page_texts, names, cap=cap, anchored=anchored):
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
