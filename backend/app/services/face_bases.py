"""Which bases a document's face statements print — consolidated, standalone, or both.

ONE QUESTION, ASKED TWICE. `services.pdf_extract` asks it before the first notes page is read, so a
note that prints no basis of its own can be read on the basis the statements print. Every writer of
a derived figure asks it again afterwards — `stages.note_sourced`, `stages.line_item_llm` and
`services.assemble_components` — and refuses a figure for a basis the statements do not print. It
is the DOCUMENT's answer, not a statement's: `notes__contingent_liabilities` lives on the "notes"
statement, which has no face rows of its own.

READ OFF THE CLASSIFIER'S PAGE VERDICTS, NOT OFF THE FACE VALUES. `stages.classify` decides whose
figures each statement page presents (`PageSource.scope`) before extraction starts, including the
two halves of a page cut at a second title and a person's override part by part. The face VALUES
are the wrong population: a note page the classifier mistook for a statement carries the row
builder's consolidated default, and on two of the Indian test filings such pages (f1 page 12,
f5 page 8) hold 52 and 37 consolidated face values on filings that print only the company's
statements. A rule over values would see two bases there and never apply.

AN UNREAD STATEMENT AHEAD OF THE NOTES LEAVES THE ANSWER UNKNOWN. A mainland filing prints the
group's statements (合并) and then the parent company's (母公司) before its notes. If a 合并 page
failed to resolve and only the 母公司 pages did, a "sole company scope" answer would re-base the
group's notes to standalone. So the answer fails closed: unknown, which applies no default and
refuses nothing.

AN UNREAD "STATEMENT" INSIDE THE NOTES IS NOT COUNTED. A face page found among the notes with no
entity read is far more often a note table the classifier took for a statement (f1 page 12, f5
page 8), so it is listed under `ignored_pages` and does not decide the answer. A statement past the
notes whose entity WAS read — an HKEX Company statement of financial position, a mainland cash-flow
supplement printed in its chapter — is counted like any other.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from app.core.models.enums import PageKind, PrintedIn

# The entity a page verdict names, and the bases its figures are filed under.
_ENTITY_BASES: dict[str, tuple[str, ...]] = {
    "consolidated": ("consolidated",),
    "company": ("standalone",),
    "mixed": ("consolidated", "standalone"),
}

# The row flag a refused figure leaves: `basis_not_printed:<basis>:<period>`.
REFUSED_FLAG = "basis_not_printed"


def _text(value) -> str:
    return str(getattr(value, "value", value) or "")


@dataclass(frozen=True)
class FaceBases:
    """The bases the face statements print, and how that was decided.

    `known` is False when the pages cannot say (no statement page, or a statement page ahead of the
    notes whose entity was not read); `reason` then says why. `counted_pages` are the statement
    pages that decided the answer and `ignored_pages` the unread ones inside the notes.
    """

    bases: frozenset[str] = frozenset()
    known: bool = False
    reason: str = ""
    ignored_pages: tuple[int, ...] = ()
    counted_pages: frozenset[int] = frozenset()

    @property
    def sole(self) -> str | None:
        """The one basis the statements print, or None when they print two or it is not known."""
        if self.known and len(self.bases) == 1:
            return next(iter(self.bases))
        return None

    def prints(self, basis) -> bool:
        """Whether a figure on ``basis`` has a statement to sit on. True whenever the answer is not
        known: an unread face refuses nothing."""
        return not self.known or _text(basis) in self.bases

    def describe(self) -> str:
        """The answer as the run log states it: ``standalone``, ``consolidated,standalone`` or
        ``unknown(<reason>)``, with the unread pages inside the notes named."""
        text = ",".join(sorted(self.bases)) if self.known else f"unknown({self.reason})"
        if self.ignored_pages:
            text += "; not counted: page=" + ",".join(str(i) for i in self.ignored_pages)
        return text


def page_entities(page) -> list[str | None]:
    """The entity of every statement printed on one page, as the classifier or a person read it.

    A person's override names one entity per face part (`services.page_overrides`). Otherwise the
    page's own scope, plus the entity of the statement still running above a mid-page title and of
    the statement a closing title opens — a page cut in two prints two statements.
    """
    evidence = getattr(page, "evidence", None) or {}
    parts = evidence.get("override_parts")
    if parts:
        return [part.get("entity") for part in parts if part.get("kind") == "face"]
    out = [getattr(page, "scope", None)]
    if evidence.get("statement_before_title"):
        out.append(evidence.get("scope_before_title"))
    if evidence.get("closing_title"):
        out.append(evidence.get("scope_after_closing_title"))
    return out


def face_bases(pages: Iterable) -> FaceBases:
    """The bases this document's statement pages print, from the classifier's page verdicts."""
    from app.services.statements import ACTIVE_STATEMENTS

    pages = list(pages or ())
    faces = sorted((p for p in pages if getattr(p, "kind", None) == PageKind.FACE
                    and getattr(p, "statement", None) in ACTIVE_STATEMENTS),
                   key=lambda p: int(getattr(p, "index", 0)))
    if not faces:
        return FaceBases(reason="no face statement page")
    first_notes = min((int(p.index) for p in pages if getattr(p, "kind", None) == PageKind.NOTES),
                      default=None)
    bases: set[str] = set()
    counted: set[int] = set()
    ignored: list[int] = []
    for page in faces:
        index = int(page.index)
        for entity in page_entities(page):
            if entity in _ENTITY_BASES:
                bases.update(_ENTITY_BASES[entity])
                counted.add(index)
            elif first_notes is not None and index > first_notes:
                if index not in ignored:
                    ignored.append(index)
            else:
                return FaceBases(reason=f"page={index}: a statement page whose entity was not "
                                        f"read sits ahead of the notes")
    if not counted:
        return FaceBases(reason="no statement page says whose figures it presents",
                         ignored_pages=tuple(ignored))
    return FaceBases(bases=frozenset(bases), known=True, ignored_pages=tuple(ignored),
                     counted_pages=frozenset(counted))


def _values(row) -> list:
    values = getattr(row, "values", None) or {}
    return list(values.values()) if isinstance(values, dict) else list(values)


def printed_bases(doc) -> FaceBases:
    """The page answer, widened by the bases the face rows of its counted pages carry.

    A page read as one entity can still print a second entity's column — a Group|Company band the
    row builder found on a page whose title named only the group — and figures on that basis have
    a statement to sit on. Values on pages that did not decide the answer are not read, for the
    reason the module docstring gives.
    """
    fb = face_bases(getattr(doc, "pages", None) or ())
    if not fb.known:
        return fb
    seen = set(fb.bases)
    for li in getattr(doc, "line_items", None) or ():
        if getattr(li, "printed_in", None) != PrintedIn.FACE:
            continue
        for ev in _values(li):
            if getattr(ev, "column_index", None) is not None or getattr(ev, "value", None) is None:
                continue
            prov = getattr(ev, "provenance", None)
            if prov is None or getattr(prov, "page_index", None) not in fb.counted_pages:
                continue
            basis = _text(getattr(ev, "basis", ""))
            if basis:
                seen.add(basis)
    return fb if seen == set(fb.bases) else replace(fb, bases=frozenset(seen))


def refuse_unprinted(doc, row, key: str, basis, period: str, amount, *, by: str, log=None,
                     bases: FaceBases | None = None) -> bool:
    """Refuse a derived figure on a basis this filing's statements do not print.

    Returns True when the figure must not be written. The row (when there is one) is flagged
    ``basis_not_printed:<basis>:<period>`` once, and the run log names the figure, so a refused
    figure is visible on the row and in the run record rather than silently absent.
    """
    fb = bases if bases is not None else printed_bases(doc)
    basis = _text(basis)
    if fb.prints(basis):
        return False
    flag = f"{REFUSED_FLAG}:{basis}:{period}"
    if row is not None and flag not in row.confidence.flags:
        row.confidence.flags.append(flag)
    if log is not None:
        log(f"{by}:{key}[{basis}:{period}] REFUSED {amount}: this filing's statements print "
            f"{', '.join(sorted(fb.bases))} figures only, so a {basis} figure has no statement "
            f"to sit on")
    return True
