"""THE NOTES A LINE'S OWN CAPTIONS NAME, for a filing whose statements print no note references.

WHY. The Workspace's NOTE column shows the reference a statement prints beside a line ("12",
"七、3") once it is linked to a note the run read (`extractions._linked_notes`). A US 10-K prints no
such column on any statement, and many mainland filings print none either (河钢股份 000709's
balance sheet is 项目 | 期末余额 | 期初余额), so on those filings the column was empty on every line,
and a reader had no way from the spread to the note that explains a figure.

WHAT IT IS. For each line, the notes whose TITLE prints one of the line's own captions — its label
and its aliases, in every language the configuration lists them in — by the rules the shared
synonym list applies to note headings (`services.heading_synonyms`): a caption is printed as a
phrase, its English words consecutively with plurals folded, or its Chinese characters in order,
and a one-word caption only as the whole title. "Inventories" names "4. Inventory"; "Accrued
liabilities" names "Accrued Liabilities and Other Current Liabilities"; "Long-term debt" does not
name "Debt".

THE LINE'S OWN WORDS ONLY, NOT THE SYNONYM LIST'S. Measured on 河钢 000709 through the HK set: the
list's concept for "notes payable" includes 应付票据, so LTD Other and CPLTD Other — whose alias
"Notes payables" means HK senior notes — were shown 七、20 应付票据, the bills-payable note. A
search may add a candidate the configuration did not write; a column a reader takes as a link may
not, so a title names a line only by a caption that line lists itself.

WHAT IT IS NOT. A display aid, labelled as one wherever it is shown. It is not a link the filing
made, so it feeds no reconciliation, no note-to-face tie and no figure: a note matched by its title
may be a policy note or an analysis, and only a printed reference says the filing meant it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from app.services import heading_synonyms as hs

# A note's number and its separator, as a title arrives with them: "4.", "Note 12 –", "(3)",
# "七、", "12(b)". The title proper is what follows.
_LEAD = re.compile(
    r"^\s*(?:notes?\s*)?[(（]?(?:\d{1,3}[a-z]?|[一二三四五六七八九十]{1,3})[)）]?"
    r"(?:\s*[(（][a-z0-9ivx]{1,4}[)）])?[\s.:、．\-–—]*", re.I)
# At most this many notes per line: a caption that names five notes names none of them usefully.
CAP = 2


def title_of(title: str) -> str:
    """A note's title without its number."""
    return _LEAD.sub("", str(title or ""), count=1).strip()


def _spelling(term: str) -> hs.Spelling | None:
    words, han = hs.shape(hs._subject(term))
    if not words and not han:
        return None
    return hs.Spelling(str(term), -1, words, han)


def notes_named(terms: Iterable[str], titles: dict[str, str], *, cap: int = CAP) -> list[str]:
    """The note numbers among `titles` (number -> printed title) whose title names one of `terms`,
    best first: a title that IS the caption, then the shorter title. At most `cap`."""
    terms = [t for t in terms if str(t or "").strip()]
    spellings = [s for s in (_spelling(t) for t in terms) if s is not None]
    if not spellings:
        return []
    hits: list[tuple[int, int, str]] = []
    for number, title in titles.items():
        head = title_of(title)
        words, han = hs.shape(head)
        if not words and not han:
            continue
        exact = any((s.words, s.han) == (words, han) for s in spellings)
        if exact or any(hs._prints(words, han, s) for s in spellings):
            hits.append((0 if exact else 1, len(words) + len(han), str(number)))
    hits.sort()
    out: list[str] = []
    for _rank, _size, number in hits:
        if number not in out:
            out.append(number)
    return out[:cap]
