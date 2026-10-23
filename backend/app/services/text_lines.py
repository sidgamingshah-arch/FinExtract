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
    never the balance sheet that prints 2024年12月31日. Three lines are sent a page by what they
    ask rather than by a name (`designated_pages`): the comparative date the page that prints the
    prior period's end date, the entity's name the cover and the auditor's report's address, and
    the opinion type is scored without the internal-control audit's sentences.
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


def select_pages(page_texts, names, *, cap: int = TEXT_PAGES_CAP, anchored=(),
                 prefer=()) -> list[int]:
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

    `prefer` (folded substrings, `OPINION_TYPES`) breaks a tie on the number of names: of two pages
    that print as many different names, the one that prints more of `prefer` ranks first, before
    the count of hits is compared.
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
            preferred = sum(1 for p in prefer or () if p in folded)
            scored.append((tier, -distinct, -preferred, -sum(hits), int(index)))
    chosen = sorted(scored)[:max(cap, 0)]
    return sorted(entry[-1] for entry in chosen)


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


# ── what a line's names designate beyond a title ────────────────────────────────────────────
#
# Three lines need a page no name of theirs is printed on, or must not be sent a page that prints
# their names about something else. Each is recognised by a name it carries, as the auditor's
# report is (`REPORT_TITLES`): a folded name of the line EQUAL to one of these.

#: A line that names the COMPARATIVE DATE asks for the prior period's end date. Its page is the one
#: that prints that date (`prior_date_page`), ranked first: on a Shenzhen-format filing the
#: statements head the comparative column 期初余额 / 上年年末余额 and print no date beside the
#: current one, so no name of the line reaches a page that prints 2023年12月31日.
COMPARATIVE_DATE_NAMES = ("Comparative Date", "Prior Period Date", "Previous Statement Date")
#: A line that names the ENTITY asks for its legal name, printed on the cover and in the address of
#: the auditor's report ("To the members of Lai Sun Garment (International) Limited") — pages that
#: print none of the line's names ("Company Name" is a caption the corporate-information page of an
#: HKEX filing need not print). Both are anchored (`entity_pages`).
ENTITY_NAMES = ("Entity Name", "Company Name", "Name of reporting entity")
#: A line that names the AUDIT OPINION asks for the type of the opinion on the FINANCIAL
#: STATEMENTS. Its pages are scored without the sentences about the internal-control audit
#: (`without_internal_control_audit`): PetroChina's governance page says KPMG "issued a standard
#: and unqualified audit opinion" on internal control, and on name count it outranked the
#: chairman's statement that both auditors "have issued unqualified opinions on the financial
#: statements".
OPINION_TYPE_NAMES = ("Audit Opinion", "审计意见类型")
#: The types an auditor's opinion is named by, folded (`select_pages` `prefer`): of the pages that
#: print as many of the line's names, the one that names a type is sent first — PetroChina's
#: chairman's statement ("unqualified opinions") over a page of the auditor's report that prints
#: "our audit opinion" more often and no type. A Chinese type ends in 意见 / 意見 (无保留意见
#: contains 保留意见; both count).
OPINION_TYPES = tuple(fold(t) for t in (
    "无保留意见", "保留意见", "否定意见", "无法表示意见", "無保留意見", "保留意見", "否定意見",
    "無法表示意見", "unqualified opinion", "unmodified opinion", "qualified opinion",
    "adverse opinion", "disclaimer of opinion"))


def _names_one_of(names, wanted) -> bool:
    folded = {fold(n) for n in names or ()}
    return any(fold(w) in folded for w in wanted)


#: What marks a sentence as being about the INTERNAL-CONTROL audit: it names internal control and
#: an audit or an opinion. An auditor's report on the statements says it is "not for the purpose
#: of expressing an opinion on the effectiveness of the Group's internal control" — that sentence
#: goes too, and it names no opinion type.
_INTERNAL_CONTROL = re.compile(r"internalcontrol|内部控制|內部控制|内控|內控")
_AUDIT_OR_OPINION = re.compile(r"audit|opinion|审计|審計|核數|意见|意見")
#: A sentence ends at a full stop (CJK or Latin, a Latin one followed by a space or a line end), a
#: question or exclamation mark, or a semicolon. A line end alone does not end one: an English
#: sentence is broken over lines.
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])|(?<=\.)(?=\s)")


def without_internal_control_audit(text: str) -> str:
    """`text` without its sentences about the internal-control audit — 内部控制审计, "internal
    control over financial reporting", "audited the effectiveness of the internal control system …
    and issued a standard and unqualified audit opinion". For SCORING a page only: the page is sent
    as printed."""
    kept = []
    for sentence in _SENTENCE_END.split(text or ""):
        folded = fold(sentence)
        if _INTERNAL_CONTROL.search(folded) and _AUDIT_OR_OPINION.search(folded):
            continue
        kept.append(sentence)
    return "".join(kept)


_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_CN_DIGITS = "零一二三四五六七八九"
_MONTH_RE = "|".join(_MONTHS)
#: The ways a statement prints a date: 2024年12月31日, 二零二四年十二月三十一日, 31 December 2024,
#: December 31, 2024. A Chinese date is matched with every space removed (`fold` keeps no space),
#: so "2024 年 12 月 31 日" is one too; an English one with its spaces.
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日"),
    re.compile(r"(?P<y>[零〇一二三四五六七八九]{4})年(?P<m>[十一二三四五六七八九]{1,2})月"
               r"(?P<d>[十一二三四五六七八九]{1,3})日"),
    re.compile(r"(?<!\d)(?P<d>\d{1,2}) ?(?P<mn>" + _MONTH_RE + r") ?,? ?(?P<y>\d{4})(?!\d)"),
    re.compile(r"(?<![a-z])(?P<mn>" + _MONTH_RE + r") ?(?P<d>\d{1,2}) ?, ?(?P<y>\d{4})(?!\d)"),
)


def _cn_number(text: str) -> int | None:
    """二零二四 -> 2024 (digit by digit), 十二 -> 12, 三十一 -> 31."""
    t = text.replace("〇", "零")
    if "十" not in t:
        if all(c in _CN_DIGITS for c in t):
            return int("".join(str(_CN_DIGITS.index(c)) for c in t))
        return None
    tens, _, units = t.partition("十")
    try:
        return ((_CN_DIGITS.index(tens) if tens else 1) * 10
                + (_CN_DIGITS.index(units) if units else 0))
    except ValueError:
        return None


def printed_dates(text: str) -> list:
    """Every date `text` prints, as `datetime.date` (`_DATE_PATTERNS`)."""
    import datetime

    folded = fold(text)
    # An English date is read with its spaces (one each): with none, "2024 31 December 2023" in a
    # column head would read as "202431december2023".
    spaced = _SPACE.sub(" ", unicodedata.normalize("NFKC", str(text or "")).casefold())
    found = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(spaced if "mn" in pattern.groupindex else folded):
            g = m.groupdict()
            y = int(g["y"]) if g["y"].isdigit() else _cn_number(g["y"])
            if g.get("mn"):
                mo = _MONTHS.index(g["mn"]) + 1
            else:
                mo = int(g["m"]) if g["m"].isdigit() else _cn_number(g["m"])
            d = int(g["d"]) if g["d"].isdigit() else _cn_number(g["d"])
            try:
                found.append((m.start(), datetime.date(y, mo, d)))
            except (TypeError, ValueError):
                continue
    return [d for _pos, d in found]


def _is_month_end(day) -> bool:
    import datetime

    return (day + datetime.timedelta(days=1)).day == 1


def date_forms(day) -> tuple[str, ...]:
    """The ways a filing prints `day` (`_DATE_PATTERNS`), folded: 2023年12月31日,
    二零二三年十二月三十一日 (and with 〇), 31 December 2023, December 31, 2023."""
    def cn(n: int) -> str:
        if n < 10:
            return _CN_DIGITS[n]
        tens, units = divmod(n, 10)
        return (_CN_DIGITS[tens] if tens > 1 else "") + "十" + (_CN_DIGITS[units] if units else "")
    month = _MONTHS[day.month - 1]
    year_cn = "".join(_CN_DIGITS[int(c)] for c in str(day.year))
    forms = (f"{day.year}年{day.month}月{day.day}日",
             f"{year_cn}年{cn(day.month)}月{cn(day.day)}日",
             f"{year_cn.replace('零', '〇')}年{cn(day.month)}月{cn(day.day)}日",
             f"{day.day} {month} {day.year}", f"{month} {day.day}, {day.year}")
    return tuple(dict.fromkeys(fold(f) for f in forms))


def statement_date(page_texts, doc):
    """The current period's end date: the month-end date the statement face pages print most
    often (the later one on a tie) — the balance sheet's heading, the "At 31 December 2024" rows of
    the equity statement, "Year ended 31 March 2024". Only a face page the run identified by its
    title (`_face_titles`) counts: 000709's company-history pages, classified as faces, print
    2009年12月31日 three times. With no titled face that prints one, the pages that open the
    auditor's report (`report_openings`). None when neither prints a date."""
    from collections import Counter

    by_index = {int(i): t for i, t in page_texts or ()}
    for pages in (sorted(_face_titles(doc)), report_openings(page_texts, doc)):
        counts = Counter(d for i in pages for d in printed_dates(by_index.get(i, ""))
                         if _is_month_end(d))
        if counts:
            return max(counts, key=lambda d: (counts[d], d))
    return None


def prior_period_end(day):
    """The end of the period before the one ending `day`: the same day one year earlier (28
    February for a 29 February)."""
    import datetime

    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return datetime.date(day.year - 1, day.month, 28)


def _report_pages(page_texts, doc) -> set[int]:
    """The pages of the auditor's report(s): from the page that opens each to the statements."""
    starts = _statement_starts(doc)
    out = set()
    for opening in report_openings(page_texts, doc):
        end = min((s for s in starts if s > opening), default=opening + 1)
        out.update(range(opening, end))
    return out


def prior_date_page(page_texts, doc) -> int | None:
    """The 0-based index of THE page that prints the prior period's end date (`prior_period_end`
    of `statement_date`, in any of its printed forms, `date_forms`): a statement face page first,
    then a page of the auditor's report, then a notes page, then any other page — the earliest of
    its kind. On an HKFRS filing it is the statement that prints the comparative date beside the
    current one; on a Shenzhen-format CAS filing, whose statements print none, it is a note that
    compares "截至2024年12月31日 … (2023年12月31日：…)", or the definitions page's 上年同期 entry.
    None when no page prints it (000709)."""
    from app.core.models.enums import PageKind

    current = statement_date(page_texts, doc)
    if current is None:
        return None
    forms = date_forms(prior_period_end(current))
    kinds = {int(getattr(p, "index", -1)): getattr(p, "kind", None)
             for p in getattr(doc, "pages", None) or ()}
    report = _report_pages(page_texts, doc)

    def rank(index: int) -> int:
        kind = kinds.get(index)
        if kind in (PageKind.FACE, PageKind.FACE.value):
            return 0
        if index in report:
            return 1
        if kind in (PageKind.NOTES, PageKind.NOTES.value):
            return 2
        return 3

    printing = [int(i) for i, t in page_texts or ()
                if any(f in form for f in forms for form in _forms(t))]
    return min(printing, key=lambda i: (rank(i), i), default=None)


def cover_page(page_texts) -> int | None:
    """The 0-based index of the cover: the first page with a text layer (hkex_1966's printed cover
    is an image, and its first page with text is the title page)."""
    for i, t in sorted(((int(i), t) for i, t in page_texts or ()), key=lambda x: x[0]):
        if fold(t):
            return i
    return None


def entity_pages(page_texts, doc) -> list[int]:
    """The cover (`cover_page`) and the page that opens the (first) auditor's report — addressed
    "To the members of <the entity>" / 致<the entity>全体股东 — for a line naming the entity."""
    out = []
    cover = cover_page(page_texts)
    if cover is not None:
        out.append(cover)
    openings = report_openings(page_texts, doc) if doc is not None else []
    if openings:
        out.append(openings[0])
    return out


def designated_pages(page_texts, names, doc) -> dict[int, int]:
    """`anchored_pages`, and the pages the lines of `COMPARATIVE_DATE_NAMES` and `ENTITY_NAMES`
    designate: the prior-date page at tier -1, ahead of every other page; the cover and the
    report's address at tier 1, after a named statement's first page and ahead of the pages that
    merely print a name."""
    out = anchored_pages(page_texts, names, doc)
    if doc is not None and _names_one_of(names, COMPARATIVE_DATE_NAMES):
        page = prior_date_page(page_texts, doc)
        if page is not None:
            out[page] = -1
    if _names_one_of(names, ENTITY_NAMES):
        for page in entity_pages(page_texts, doc):
            out[page] = min(out.get(page, 1), 1)
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
    anchored = designated_pages(page_texts, names, doc)
    scored, prefer = page_texts, ()
    if _names_one_of(names, OPINION_TYPE_NAMES):
        scored = [(i, without_internal_control_audit(t)) for i, t in page_texts or ()]
        prefer = OPINION_TYPES
    for index in select_pages(scored, names, cap=cap, anchored=anchored, prefer=prefer):
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
