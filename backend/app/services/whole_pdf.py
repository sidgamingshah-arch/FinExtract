"""THE WHOLE PDF TO THE MODEL — the template's lines read off the file itself, not off rows the app
rebuilt from it (`extraction.whole_pdf_to_model`, `stages.whole_pdf`).

The rules route reads a filing in three steps: the text layer is rebuilt into rows and columns
(`row_reconstruct`, `notes_extract`), each row is placed on a template line by its caption
(`map_ontology`), and the model is asked only where a named line's figure is printed among those
rows (`line_item_llm`). A table whose columns the rebuild cannot see is lost at the first step,
whatever the model could have read. This route skips all three: the PDF FILE goes to the model — a
provider that takes files reads each page as its text and as a picture, so a table keeps its
columns — with the template's lines, and the model returns each line's figures with the page it
read them on.

WHAT STAYS FROM THE RULES ROUTE, and why each:

* The page classifier. Cheap, runs before extraction anyway (it is what the Page Scope screen
  shows), and it chooses the pages worth sending: a third to a half of an annual report is
  statements and notes, and every page sent costs its text and its picture on every request.
* The PDF's own text, as the record a figure is checked against (as the credit-memo tool keeps
  its extract beside the file, ADR-0053 there). Every figure is looked for on the page it cites;
  one that is not there is marked unconfirmed and sent to review, never silently trusted.
* Units, signs and the template's checks — `normalize`, `confidence` and `structural` run on the
  model's figures exactly as on the rules'. The checks compare a printed total with its printed
  components, both read by the model, so a misread figure shows as a total that does not add up.

NOTHING HERE LOGS A FILE OR ITS TEXT. A log line names pages, counts and keys.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.models.enums import PageKind
from app.core.models.geometry import BBox

PDF = "application/pdf"

#: What a request carries beside its file, counted against `whole_pdf_max_request_mb`: the system
#: text, the line list and the JSON envelope. Generous — the line list of 60 lines is about 20 KB.
REQUEST_OVERHEAD_BYTES = 256 * 1024

#: The line types the model is asked for.
#:
#: EVERY EXTRACTED LINE, WHATEVER ITS ROUTE — the rule, and the same one the rules route keeps
#: (`line_item_requests.asked_about`): a line whose figure is read off the filing is asked about,
#: whether its `route` says the face, the note tables, anywhere in the report or the narrative.
#: That includes the INTERNAL PARTS (`sub__…`, `in_output: false`) a derived line's cascade adds
#: up, and the TEXT lines (a phrase or prose: the company's name, the currency, the auditor). Both
#: were left out at first, so the HK set's Sales (Revenues) — a derived line whose cascade reads
#: `sub__revenue_note_principal_revenue`, else `sub__face_principal_revenue` — had nothing to add
#: up, and 11033486's first run had no revenue.
#:
#: "calculated" lines are asked for the total the filing PRINTS, so the template's checks compare
#: a printed total with its printed parts.
#:
#: NOT ASKED: a "derived" line — its figure is its configured cascade's, computed from the parts
#: by `note_sourced` on this route as on the rules route (asked directly, the model gives the
#: face's revenue where the cascade gives the revenue note's principal-business revenue); an
#: "intermediate" working figure; and a line whose `extraction_mode` is "derive", which the app
#: computes or a person fills (the HK set's analyst, status, target currency, periods and control
#: totals) — there is nothing printed to read.
ASKED_TYPES = ("extracted", "calculated")

#: The order statements are asked in, the primary statements first. A statement not named here
#: comes after them, in the order the set lists it.
STATEMENT_ORDER = ("balance_sheet", "profit_and_loss", "cash_flow", "notes",
                   "covenants_supplemental", "statement_setup")

#: Reply budget per line asked: up to four figures (two bases, two periods), each with its page,
#: caption and the rows of a sum. A reply cut off is asked once more with twice the room
#: (`ports.llm.complete_with_room`) where twice the room is within `ports.llm.ROOM_CEILING`; a
#: larger request cut off is asked again as two halves instead (`stages.whole_pdf`, `halves`).
_TOKENS_PER_LINE = 220
_TOKENS_RESERVE = 1_024
#: …at most this: a statement's whole line list is asked in one request (202 lines on the HK
#: balance sheet), and most of its lines are not printed and cost nothing. The longest reply
#: measured, a balance sheet asked 60 lines at a time, was 3,878 tokens.
_TOKENS_CEILING = 32_000


# ── which pages, in which parts ────────────────────────────────────────────────────────────────

def pages_to_send(doc, *, choice: str, included_pages=None, sections_off=None,
                  raw: bytes | None = None) -> tuple[list[int], str]:
    """The page indexes to send, in order, and why those.

    "statements" sends the pages the next run would read as the Page Scope screen asks it
    (`page_reading.reader_targets`): the statement faces and notes the classifier found — every
    page when it found none — narrowed by a person's page selection and the sections they switched
    off. "all" sends every page, narrowed the same way.

    …AND THE AUDITOR'S REPORT AND THE COVER (`report_pages`), which no reader target includes: the
    Accountant, the auditor's opinion and the audit-opinion text are printed nowhere else, so
    without them those lines could only be refused as "not on the page" (finding
    auditor_pages_not_sent). Added only where no page selection applies — a person who chose pages
    chose them.
    """
    from app.services import page_reading

    pages = list(doc.pages)
    if choice == "all":
        chosen = page_reading.narrow_targets(pages, included_pages, sections_off)
        why, base = "all", pages
    else:
        chosen = page_reading.reader_targets(pages, included_pages, sections_off)
        base = page_reading.default_targets(pages)
        why = ("statements" if any(p.kind in (PageKind.FACE, PageKind.NOTES) for p in pages)
               else "all(no statement or notes page was classified)")
    selected = bool(included_pages and page_reading.selection_applies(base, included_pages))
    if selected:
        why = "page_scope"
    out = {int(p.index) for p in chosen}
    if choice != "all" and not selected:
        added = {name: [p for p in found if p not in out]
                 for name, found in report_pages(doc, raw).items()}
        for name, found in added.items():
            if found:
                out.update(found)
                why += f"; +{name} {','.join(str(p + 1) for p in found)}"
    if sections_off:
        why += f"; sections off: {','.join(sorted(sections_off))}"
    return sorted(out), why


#: THE HEADING OF AN AUDITOR'S REPORT, as the corpus prints it: an HKEX report's running header
#: ("INDEPENDENT AUDITOR'S REPORT 獨立核數師報告", on every page of it; 0857's IFRS set "Independent
#: auditor's report to the shareholders"), a CSRC report's section heading ("第十节 财务报告
#: 一、审计报告", on its first page only) and a 10-K's — in any case…
_AUDITOR_HEADING = re.compile(
    r"INDEPENDENT\s+AUDITOR[’'`]?S[’']?\s+REPORT|獨立核數師報告|独立核数师报告|"
    r"[一二三四五六七八九十]\s*[、.．]\s*(?:审计报告|審計報告)|"
    r"REPORT\s+OF\s+INDEPENDENT\s+REGISTERED\s+PUBLIC\s+ACCOUNTING\s+FIRM", re.IGNORECASE)
#: …and "AUDITOR'S REPORT" in capitals, as a heading (0857's CAS set: "AUDITOR'S REPORT
#: (continued)"), never the phrase in a sentence ("the auditor's report").
_AUDITOR_CAPS = re.compile(r"\bAUDITOR[’'`]?S[’']?\s+REPORT\b")
#: How far before a statement page an auditor's report may start. The longest in the corpus is
#: 1966's, ten pages (92-101).
_AUDITOR_REACH = 15


def report_pages(doc, raw: bytes | None) -> dict[str, list[int]]:
    """The pages, outside the statements and notes, that print lines the template asks for.

    * ``auditor's report`` — every page the classifier calls `AUDITOR_REPORT`, and, as no filing in
      the corpus is classified so (688008's report pages are faces with no statement, the HKEX
      filings' are "other"), the pages from the report's heading up to a set of statements: before
      each run of statement pages, the latest page within `_AUDITOR_REACH` that carries the
      heading, and the run of pages before that one that carry it too (a running header), read off
      the PDF's own text. Before EACH run, as a filing that prints two sets of statements (0857: CAS,
      then IFRS) prints an auditor's report before each.
    * ``cover`` — every page classified `COVER`. (None of the five reference filings has one: their
      covers are classified "other" and are not sent. The company's name, the one line a cover
      would serve, is printed at the head of every statement page.)
    """
    pages = list(doc.pages)
    kinds = {int(p.index): p.kind for p in pages}
    auditor = sorted(i for i, k in kinds.items() if k == PageKind.AUDITOR_REPORT)
    cover = sorted(i for i, k in kinds.items() if k == PageKind.COVER)
    faces = {int(p.index) for p in pages if p.kind == PageKind.FACE and _statement_value(p)}
    starts = sorted(f for f in faces if f - 1 not in faces)
    if starts and raw and not auditor:
        auditor = _auditor_runs(raw, starts, faces)
    return {"auditor's report": auditor, "cover": [c for c in cover if c not in auditor]}


def _statement_value(page) -> str:
    from app.services.statements import ACTIVE_STATEMENTS

    st = getattr(page.statement, "value", page.statement)
    return st if st in ACTIVE_STATEMENTS else ""


def _auditor_runs(raw: bytes, starts: list[int], faces: set[int]) -> list[int]:
    import fitz

    try:
        pdf = fitz.open(stream=raw, filetype="pdf")
    except Exception:  # noqa: BLE001 - a file that will not open has no report to find
        return []
    try:
        def heads(i: int) -> bool:
            top = (pdf.load_page(i).get_text("text") or "")[:600]
            return bool(_AUDITOR_HEADING.search(top) or _AUDITOR_CAPS.search(top))
        out: set[int] = set()
        for first in starts:
            window = [i for i in range(max(0, first - _AUDITOR_REACH), first)
                      if i not in faces and 0 <= i < pdf.page_count]
            latest = next((i for i in reversed(window) if heads(i)), None)
            if latest is None or any(i in faces for i in range(latest, first)):
                continue                  # none, or the report of an earlier set of statements
            start = latest
            while start - 1 in window and heads(start - 1):
                start -= 1
            out.update(i for i in range(start, first) if i not in faces)
        return sorted(out)
    finally:
        pdf.close()


@dataclass
class Part:
    """One file sent: the pages it holds (0-based, of the original), in order, and its bytes.

    `faces_only`: the statements alone (`Plan`, `statement_pages`) — the part a statement's
    [read from the statement] lines are asked in, with the rest of that statement's lines."""
    pages: list[int]
    pdf: bytes
    faces_only: bool = False

    @property
    def b64_bytes(self) -> int:
        return 4 * ((len(self.pdf) + 2) // 3)

    def original_page(self, page_of_part: int) -> int | None:
        """The original 0-based page index of this part's page `page_of_part` (1 = its first)."""
        if 1 <= page_of_part <= len(self.pages):
            return self.pages[page_of_part - 1]
        return None


def _runs(pages: list[int]) -> list[tuple[int, int]]:
    """Consecutive page indexes as (first, last) runs: [3,4,5,9] -> [(3,5),(9,9)]."""
    out: list[tuple[int, int]] = []
    for p in pages:
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def balanced(pages: list[int], max_pages: int) -> list[list[int]]:
    """The pages in as few runs of at most `max_pages` as will do, of even size: 101 pages at 100
    a request are 51 and 50, not 100 and 1 — the largest request is what a provider refuses or
    times out on."""
    if not pages:
        return []
    count = -(-len(pages) // max(1, int(max_pages)))
    size = -(-len(pages) // count)
    return [pages[i:i + size] for i in range(0, len(pages), size)]


def build_parts(raw: bytes, pages: list[int], *, max_pages: int, max_request_bytes: int,
                carry=(), overhead_bytes: int = REQUEST_OVERHEAD_BYTES
                ) -> tuple[list[Part], list[int]]:
    """The chosen pages as PDF files, each within the request's limits, and the pages that could
    not be sent at all (a single page larger than a whole request).

    Cut into as few parts of at most `max_pages` as will do, of even size (`balanced`); a part
    whose file in base64, with the request's text (`overhead_bytes`), is over `max_request_bytes`
    is halved until it fits.

    `carry`: PAGES EVERY PART HOLDS — the statement faces (`face_pages`), so a note read in the
    second part is read with the statements in view (finding parts_and_merge_prefer_face_for_note_
    parts). The other pages are cut as above into parts of at most `max_pages` minus the carried
    ones, each sent with them in the report's order — so carrying makes the parts SMALLER, never
    drops a page. Where the carried pages leave no room (they alone fill a request), or a part
    holding them does not fit even with one other page, those pages are sent as before, with
    nothing carried.
    """
    import fitz  # PyMuPDF, already the app's PDF reader

    max_pages = max(1, int(max_pages))
    carried = sorted({int(p) for p in carry} & set(pages))
    rest = [p for p in pages if p not in set(carried)]
    if carried and (not rest or len(carried) >= max_pages):
        carried, rest = [], list(pages)          # nothing to carry them beside, or no room
    parts: list[Part] = []
    dropped: list[int] = []
    src = fitz.open(stream=raw, filetype="pdf")
    try:
        def make(chunk: list[int]) -> bytes:
            out = fitz.open()
            try:
                for first, last in _runs(chunk):
                    # Without links and annotations: the model reads the page, and a link to a
                    # page not in the part is broken anyway (MuPDF says so on stdout).
                    out.insert_pdf(src, from_page=first, to_page=last, links=False,
                                   annots=False)
                return out.tobytes(garbage=3, deflate=True)
            finally:
                out.close()

        def fit(chunk: list[int], with_carried: bool) -> None:
            held = sorted(set(chunk) | set(carried)) if with_carried else list(chunk)
            part = Part(pages=held, pdf=make(held))
            if part.b64_bytes + overhead_bytes <= max_request_bytes:
                parts.append(part)
                return
            if len(chunk) == 1:
                if with_carried:
                    fit(chunk, False)            # the page alone, as it was sent before
                else:
                    dropped.append(chunk[0])
                return
            mid = len(chunk) // 2
            fit(chunk[:mid], with_carried)
            fit(chunk[mid:], with_carried)

        room = max_pages - len(carried)
        for chunk in balanced(rest, room):
            fit(chunk, bool(carried))
        if carried and not any(set(carried) <= set(p.pages) for p in parts):
            for chunk in balanced(carried, max_pages):   # no part could hold them: on their own
                fit(chunk, False)
    finally:
        src.close()
    return parts, dropped


def face_pages(doc, pages: list[int]) -> list[int]:
    """The statement faces among the pages sent: a page classified a face of a known statement."""
    sent = set(pages)
    return sorted(int(p.index) for p in doc.pages
                  if int(p.index) in sent and p.kind == PageKind.FACE and _statement_value(p))


def as_document(part: Part, number: int) -> dict:
    """The part as a file block for `ports.llm.LlmMessage.documents` — named neutrally, never by
    the uploaded file's own name."""
    return {"media_type": PDF, "data": base64.b64encode(part.pdf).decode("ascii"),
            "name": f"document-{number}.pdf"}


# ── which lines, in which requests ─────────────────────────────────────────────────────────────

def statement_of(item) -> str:
    st = item.statement or (item.statements[0] if item.statements else None)
    return getattr(st, "value", st) or "other"


def read_by(item_set) -> set[str]:
    """The keys some other line reads: a term of a cascade rung or of a formula, or the `parent`
    a part rolls up into."""
    keys = {item.key for item in item_set.items}
    out: set[str] = set()
    for item in item_set.items:
        for rung in item.cascade or ():
            out.update(t.ref for t in rung.terms if t.ref)
        out.update(t.ref for t in (getattr(item, "terms", None) or ()) if t.ref)
        if item.parent and item.parent in keys:
            out.add(item.key)
    return out


def lines_to_ask(item_set) -> list:
    """Every line the model is asked for (`ASKED_TYPES`): each extracted line whatever its route,
    internal parts and text lines included, and each printed total.

    NOT A PART NOTHING READS. A part publishes no column of its own; its figure reaches the output
    only through the line that reads it — a cascade, a formula, the parent it rolls up into. One
    that nothing reads is asked for nothing: the HK set's `bs_ca_residual_L3` (no definition, no
    parent, named by `line_item_audit.NOT_A_COLUMN`) was asked blind, as "Residual L3 [PART; read
    from the statement]", and a row the model filed there reached no line (findings
    residual_L3_asked_blind, residual_l3_asked_without_definition).
    """
    read = read_by(item_set)
    out = []
    for item in item_set.items:
        if item.type not in ASKED_TYPES or getattr(item, "extraction_mode", "") == "derive":
            continue
        if is_part(item) and item.key not in read:
            continue
        out.append(item)
    rank = {s: i for i, s in enumerate(STATEMENT_ORDER)}
    order = {item.key: i for i, item in enumerate(out)}
    return sorted(out, key=lambda it: (rank.get(statement_of(it), len(rank)), order[it.key]))


class Chunk(list):
    """The lines one request asks, and the statement its question names (`question`'s heading,
    "Template lines (<statement>)") — the statement of every line in it that is a statement's own
    line; a part asked beside its siblings (`families`) may be of another."""

    def __init__(self, lines=(), statement: str = ""):
        super().__init__(lines)
        self.statement = statement or (statement_of(self[0]) if self else "")

    def halves(self) -> tuple[Chunk, Chunk]:
        mid = len(self) // 2
        return Chunk(self[:mid], self.statement), Chunk(self[mid:], self.statement)


def _terms_of(item_set):
    """Each summing rung of every cascade, as (line, rung id, [(ref, sign)])."""
    for item in item_set.items:
        for rung in item.cascade or ():
            if rung.terms_op == "sum":
                yield item, rung.id, [(t.ref, t.sign) for t in rung.terms if t.ref]


def families(item_set, asked: list) -> dict[str, str]:
    """`part key -> family id`: THE PARTS ONE CASCADE ADDS UP, asked in one request.

    Two parts are of one family when one summing rung adds both, directly or through an internal
    working line that is not asked (`sub__fa_cp_level_3_total`, `sub__fa_cp_intermediate_
    residual`) — so Securities (CP)'s Find 1 parts, its Level 3 parts, and Securities (LTP)'s parts
    that its residual reaches, are one family. Each was asked in the request of its own statement,
    and the shipped set declares `statement: notes` on three of CP's six Find 1 parts and none on
    the rest, so they went in three requests that never saw each other, and nothing stopped one
    holding being read into two of them (finding find1_siblings_double_count_whole_pdf: 688008's
    结构性存款 inside 交易性金融资产).

    Only parts: a statement's own line is asked in its statement's request, always.
    """
    by_key = {item.key: item for item in item_set.items}
    parts = {item.key for item in asked if is_part(item)}

    def reach(ref: str, seen: frozenset = frozenset()) -> set[str]:
        if ref in parts:
            return {ref}
        item = by_key.get(ref)
        if item is None or ref in seen or not item.cascade or not is_part(item):
            return set()
        out: set[str] = set()
        for rung in item.cascade:
            for t in rung.terms:
                if t.ref:
                    out |= reach(t.ref, seen | {ref})
        return out

    root: dict[str, str] = {}

    def find(x: str) -> str:
        while root.setdefault(x, x) != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for _item, _rung, terms in _terms_of(item_set):
        group = sorted(set().union(*(reach(ref) for ref, _sign in terms)) if terms else set())
        for other in group[1:]:
            root[find(other)] = find(group[0])
    return {key: find(key) for key in parts}


def sibling_groups(item_set, lines) -> list[tuple[str, list[str]]]:
    """`(line, [part keys])` for each summing rung that adds two or more of these parts with a
    plus sign — the parts the question says one holding goes into at most one of."""
    asked = {item.key for item in lines if is_part(item)}
    out: list[tuple[str, list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for item, _rung, terms in _terms_of(item_set):
        group = [ref for ref, sign in terms if sign == 1 and ref in asked]
        key = tuple(sorted(group))
        if len(group) >= 2 and key not in seen:
            seen.add(key)
            out.append((item.key, group))
    return out


def chunk_lines(lines: list, per_request: int, item_set=None) -> list[Chunk]:
    """Lines in requests of at most `per_request`, never mixing two statements' own lines in one
    request.

    With `item_set`, A CASCADE'S PARTS GO TOGETHER (`families`): every part of a family that is
    asked under more than one statement is asked in the request of the statement most of them are
    asked under (on a tie, the later in `STATEMENT_ORDER`, "other" last), so a request holding one
    sibling holds them all.
    """
    per_request = max(1, int(per_request))
    home: dict[str, str] = {}
    if item_set is not None:
        family = families(item_set, lines)
        members: dict[str, list] = {}
        for item in lines:
            if item.key in family:
                members.setdefault(family[item.key], []).append(item)
        rank = {s: i for i, s in enumerate(STATEMENT_ORDER)}
        for group in members.values():
            counts: dict[str, int] = {}
            for item in group:
                counts[statement_of(item)] = counts.get(statement_of(item), 0) + 1
            if len(counts) > 1:
                where = max(counts, key=lambda s: (counts[s], rank.get(s, len(rank))))
                home.update({item.key: where for item in group})
    buckets: dict[str, list] = {}
    for item in lines:
        buckets.setdefault(home.get(item.key) or statement_of(item), []).append(item)
    rank = {s: i for i, s in enumerate(STATEMENT_ORDER)}
    first_seen = {s: i for i, s in enumerate(dict.fromkeys(statement_of(it) for it in lines))}
    chunks: list[Chunk] = []
    for statement in sorted(buckets, key=lambda s: (rank.get(s, len(rank)),
                                                      first_seen.get(s, len(first_seen)))):
        group = buckets[statement]
        for i in range(0, len(group), per_request):
            chunks.append(Chunk(group[i:i + per_request], statement))
    return chunks


def max_tokens_for(n_lines: int) -> int:
    return min(_TOKENS_CEILING, _TOKENS_RESERVE + max(1, n_lines) * _TOKENS_PER_LINE)


#: What a request carries beside its file and its question: the reply schema the adapter adds to
#: the system text, the JSON envelope, the message wrapper.
_ENVELOPE_BYTES = 32 * 1024


@dataclass
class Plan:
    """What a run sends: the pages and why, the parts, the faces every part carries, the lines
    and the requests they are asked in.

    A STATEMENT'S [read from the statement] LINES ARE ASKED ONCE, AGAINST THE STATEMENTS ALONE
    (`face_keys`, the part `faces_only`). A balance sheet's 180 face lines were asked in every
    part, each part carrying the faces beside half the notes, so the same face pages were read for
    them twice and their definitions — 42,052 of the balance-sheet question's 52,373 estimated
    tokens on the HK set — sent twice, in the run's largest requests. Now:

    * the `faces_only` part holds every page the classifier calls a statement face and nothing
      else (7 to 20 pages on the reference filings), and is asked each statement's WHOLE line
      list (`face_statements`) — every line of it, so the model still puts each printed row on
      exactly one line and the rows no line describes into the section's "other" line, with the
      printed totals beside them;
    * the parts that hold the notes are asked every line but those face lines: the statements'
      lines read from the notes or anywhere (Trade Receivables(Gross) off the 应收账款 breakdown,
      the printed totals, which may be read either side), all three statements' in one request
      (`STATEMENTS_REST`), and the notes' and the other sections' requests as before. They still
      carry the faces, so a note is read with its statement in view.

    Measured on the five reference filings (no model; `tests/test_the_whole_pdf_question_keeps_
    every_deciding_clause.py`): 688008's largest request 197,882 estimated input tokens -> 154,719
    (140,020 before 01d4a34), its whole run 2,183,151 -> 1,663,775 in 13 requests instead of 14,
    and the question text paid fresh on every request 232,720 -> 131,566.

    A statement no face page was found of is asked where the notes are, whole, as before.
    """
    pages: list[int]
    why: str
    parts: list[Part]
    dropped: list[int]
    carried: list[int]
    lines: list
    chunks: list[Chunk]
    overhead_bytes: int
    #: The lines asked only in the `faces_only` parts: the [read from the statement] lines of
    #: `face_statements`' requests. Empty when there is no such part.
    face_keys: frozenset = frozenset()
    #: The statements whose whole request the `faces_only` parts are asked.
    face_statements: frozenset = frozenset()

    def ask(self, pi: int, ci: int) -> Chunk:
        """The lines of chunk `ci` that part `pi` is asked: a `faces_only` part a face statement's
        whole request and nothing else; another part every other request, the face statements'
        own lines but their face lines in one (`STATEMENTS_REST`). Every line of the chunk where
        the plan has no `faces_only` part."""
        chunk = self.chunks[ci]
        if not self.face_keys:
            return chunk
        empty = Chunk([], chunk.statement)
        if self.parts[pi].faces_only:
            return chunk if chunk.statement in self.face_statements else empty
        return empty if chunk.statement in self.face_statements else chunk

    def requests(self) -> int:
        return len(self.order())

    def second_part(self) -> int | None:
        """Where a statement's request to the statements alone is asked again when it fails
        (`stages.whole_pdf`): the part holding the notes that holds most of the `faces_only`
        pages — every carried face where the faces are carried — or None when none holds any."""
        alone = {p for part in self.parts if part.faces_only for p in part.pages}
        held = [(len(alone & set(part.pages)), -pi) for pi, part in enumerate(self.parts)
                if not part.faces_only]
        best = max(held, default=(0, 0))
        return -best[1] if best[0] else None

    def first_chunk(self) -> int:
        """THE SMALLEST REQUEST GOES FIRST (finding whole_pdf_no_room_retry_and_first_request_
        gate): it writes the part to the provider's prompt cache for the others, and it is the one
        whose failure sends the run to the rules — so it should be the quickest to answer, not the
        213-line balance sheet that waited up to 2 x 600 s before the fallback could start. This
        is the first part's."""
        return self._first(0)

    def _first(self, pi: int) -> int:
        asked = [ci for ci in range(len(self.chunks)) if self.ask(pi, ci)]
        return min(asked or range(len(self.chunks)),
                   key=lambda i: (len(self.ask(pi, i)), i))

    def order(self) -> list[tuple[int, int]]:
        """Every (part, chunk) request in the order it is sent: each part's smallest request on
        its own, then the rest of that part, the largest first so the slowest starts soonest. A
        chunk that asks a part no line is not sent to it."""
        out: list[tuple[int, int]] = []
        for pi in range(len(self.parts)):
            sizes = {ci: len(self.ask(pi, ci)) for ci in range(len(self.chunks))}
            first = self._first(pi)
            rest = sorted((ci for ci, n in sizes.items() if n and ci != first),
                          key=lambda ci: (-sizes[ci], ci))
            out.extend((pi, ci) for ci in ([first] if sizes.get(first) else []) + rest)
        return out


#: THE STATEMENTS' LINES READ FROM THE NOTES OR ANYWHERE, asked in one request of each part that
#: holds the notes, once their face lines go to the statements alone (`Plan`). A request never
#: mixed two statements' lines, so the model would put each printed row on exactly one line and
#: the rows no line describes into the section's "other" line — rules about a statement's FACE
#: rows, and no face line, no "other" line, is in this request. Asked one statement at a time they
#: were three requests of 22, 14 and 6 lines on the HK set, each sending the 64 to 94 pages of its
#: part again. The question still heads each statement's lines with its name (`question`).
STATEMENTS_REST = "statements_read_from_the_notes"


def statements_rest(chunks: list[Chunk], statements, keys, per_request: int) -> list[Chunk]:
    """What the parts holding the notes are asked of these statements once their face lines
    (`keys`) go to the statements alone: their other lines, all the statements' in one request
    (`STATEMENTS_REST`), at most `per_request` a request."""
    rest = [it for chunk in chunks if chunk.statement in statements for it in chunk
            if it.key not in keys]
    per = max(1, int(per_request))
    return [Chunk(rest[i:i + per], STATEMENTS_REST) for i in range(0, len(rest), per)]


def statement_pages(doc, pages: list[int]) -> list[int]:
    """Every page sent that the classifier calls a statement face, whether or not it named the
    statement: a continuation it did not name (000709's 利润分配 rows of the statement of changes
    in equity, page 93) is a face all the same."""
    sent = set(pages)
    return sorted(int(p.index) for p in doc.pages
                  if int(p.index) in sent and p.kind == PageKind.FACE)


def face_keys(chunks: list[Chunk], statements: set[str], item_set=None) -> frozenset:
    """The lines asked against the statements alone, and there only: the [read from the
    statement] lines of these statements' own requests.

    With `item_set`, NEVER ONE PART OF A FAMILY WITHOUT THE OTHERS (`families`): a part read off
    the face whose cascade also adds a part read from the notes stays where the notes are, with
    its siblings, so the "Added together" rule still sees the whole group in one request. (None of
    the shipped sets has such a family in a statement's request today.)"""
    keys = {it.key for chunk in chunks if chunk.statement in statements for it in chunk
            if str(getattr(it.route, "value", it.route) or "") == "face"}
    if item_set is not None:
        family = families(item_set, [it for chunk in chunks for it in chunk])
        members: dict[str, set[str]] = {}
        for key, root in family.items():
            members.setdefault(root, set()).add(key)
        keys = {k for k in keys if k not in family or members[family[k]] <= keys}
    return frozenset(keys)


def plan(doc, raw: bytes, item_set, *, choice: str, max_pages: int, max_request_bytes: int,
         lines_per_request: int, included_pages=None, sections_off=None) -> Plan:
    """The run's request plan, from the classified pages and the line-item set.

    The lines first, because the questions' size is part of each request's size: THE WHOLE
    DEFINITIONS make the largest question (the HK balance sheet's, 213 lines) about 200,000
    characters, near `REQUEST_OVERHEAD_BYTES` on its own, so the room a part leaves for its text is
    the largest question's own size (UTF-8, as the body is sent), the system text and an envelope.

    Then the parts that hold the pages sent (`build_parts`, the faces carried in each), and, where
    some pages sent are statement faces and some are not, THE STATEMENTS ALONE as a part of their
    own, sent last (`Plan`): the run's first request stays the first notes part's smallest, which
    answers quickest whether the provider takes the file.
    """
    off = set(sections_off or ())
    lines = [it for it in lines_to_ask(item_set) if statement_of(it) not in off]
    chunks = chunk_lines(lines, lines_per_request, item_set)
    widest = max((len(question(c, Part(pages=list(range(max(1, int(max_pages)))), pdf=b""),
                               2, 2, item_set, carried=1).encode("utf-8")) for c in chunks),
                 default=0)
    overhead = max(REQUEST_OVERHEAD_BYTES, widest + len(SYSTEM.encode("utf-8")) + _ENVELOPE_BYTES)
    pages, why = pages_to_send(doc, choice=choice, included_pages=included_pages,
                               sections_off=off, raw=raw)
    carry = face_pages(doc, pages)
    parts, dropped = build_parts(raw, pages, max_pages=max_pages,
                                 max_request_bytes=max_request_bytes, carry=carry,
                                 overhead_bytes=overhead)
    carried = carry if len(parts) > 1 and all(set(carry) <= set(p.pages) for p in parts) else []
    keys: frozenset = frozenset()
    whole: frozenset = frozenset()
    statements = statement_pages(doc, [p for p in pages if p not in set(dropped)])
    if parts and statements and len(statements) < len(pages) - len(dropped):
        found = {_statement_value(p) for p in doc.pages
                 if int(p.index) in set(statements) and _statement_value(p)}
        whole = frozenset(found & set(FACE_STATEMENTS))
        keys = face_keys(chunks, whole, item_set)
        rest = statements_rest(chunks, whole, keys, lines_per_request) if keys else []
        if keys and not rest and all(c.statement in whole for c in chunks):
            # NOTHING WOULD BE LEFT TO ASK THE PARTS HOLDING THE NOTES (the Ind AS set: its 111
            # lines are all read from the statements): the run's first request would not be the
            # first part's, and the stage's first-request gate — that request fails, the rules
            # read the filing at once — would never fire, so a provider that is down was asked 12
            # times before the run fell back. Nothing is split off: the plan is the one before.
            keys = frozenset()
        if keys:
            alone, _ = build_parts(raw, statements, max_pages=max_pages,
                                   max_request_bytes=max_request_bytes, overhead_bytes=overhead)
            for part in alone:
                part.faces_only = True
            parts = parts + alone
            if not alone:
                keys = frozenset()
        if keys:
            chunks = chunks + rest
    return Plan(pages=pages, why=why, parts=parts, dropped=dropped, carried=carried, lines=lines,
                chunks=chunks, overhead_bytes=overhead, face_keys=keys,
                face_statements=whole if keys else frozenset())


# ── the question and the reply ─────────────────────────────────────────────────────────────────

SYSTEM = """You read a company's annual report from the PDF file sent with the question, and fill \
named template lines with the figures the report prints.

For every line you can find, give its figures, one for each basis and period the report prints:
- basis: "consolidated" for the group's (consolidated) statements; "standalone" for the company's \
own (parent-only, standalone, company-level) statements.
- period: "current" for the latest financial year the statement prints, "prior" for the year \
before it. Leave out older columns.
- amount: the figure exactly as printed, in the statement's own units (do not scale it), written \
as a plain number: digits, "." for decimals, and a leading "-" where the report prints it negative \
(in brackets or with a minus sign). No thousands separators, currency or unit words. A row printed \
as nil ("-", "—") is 0. One exception, in the cash flow statement: a payment or outflow \
(支付…, 购建…, 偿还…, 分配股利…, purchases, repayments, dividends paid) is negative even where \
the statement prints it unsigned under an outflow subtotal (…现金流出小计); every other figure \
keeps the sign it is printed with.
- page: the page OF THIS PDF FILE the figure is printed on, counting the file's first page as 1. \
Not the page number printed on the page.
- caption: the row's caption exactly as printed.
- period_end: the column heading's date as printed, if it prints one.

Rules:
- A line whose definition covers several printed rows (an "other" line, or a line the report \
splits into rows) is their sum: give the sum as amount and list each row in parts, with its \
caption and amount as printed.
- A line marked TOTAL is a total the template also adds up: give the total the report PRINTS for \
it, if it prints one. Do not add it up yourself.
- Every printed row of the statement goes to exactly one line: never put one printed row on two \
lines. A printed row that no line above describes goes into its section's "other" line (the line \
whose name says other), added to it and listed in its parts. A printed subtotal or total goes on \
its TOTAL line, never into an "other" line.
- Leave out a line the report does not print. Never answer 0 for a line that is not there.
- Read each line where it says: [read from the statement], [read from the notes], [read from \
anywhere in the report] or [read from the narrative]. A line with none: a figure printed both on \
a statement and in a note is cited on the statement.
- A line marked PART is an input the template adds into the line it names: give the figure its \
definition describes, from where it says to read it. A PART may read a printed row that another \
line also reads; the one-row-one-line rule is for the statement's own lines. The exception is the \
parts listed together under "Added together": the template sums them into one line, so one \
printed holding or row goes into at most one of them.
- A definition's "Not this line" names rows that belong to another line: never answer the line \
with them.
- A line marked TEXT asks for words, not a figure: give them in text, exactly as printed for a \
name, a date or a phrase, and in text_pages the page of this PDF file that prints them. A TEXT \
line read from the narrative is a few sentences in the report's own words: text_pages lists every \
page it is written from, and every number in it must be printed on those pages. Give no figures \
for a TEXT line.
- Read figures from the financial statements and their notes, not from highlights, summaries, \
the directors' report or the management discussion.
- units: how the statements state their units, as printed (for example "HK$'000", "RMB", \
"₹ in crore", "in millions of U.S. dollars").
"""


#: WHERE A LINE IS READ, its configured route, as the question names it.
_READ_FROM = {"face": "read from the statement", "note_tables": "read from the notes",
              "anywhere": "read from anywhere in the report", "prose": "read from the narrative"}


def is_text(item) -> bool:
    """A line whose answer is words, not a figure: a phrase copied off a page, or prose — the
    rules route's own test (`text_lines.is_text_line`)."""
    from app.services.text_lines import is_text_line

    return is_text_line(item)


def is_part(item) -> bool:
    """An internal part: an input a cascade adds into another line, never printed as a column."""
    return item.namespace == "internal" or item.key.startswith("sub__") or not item.in_output


def definition_of(item) -> str:
    """THE LINE'S WHOLE DEFINITION, with what its `exclude_hints` rule out said in words where it
    does not already say so (`not_this_line`).

    Whole, as the line-item model route sends it (`services.line_item_llm.line_item_payload`). It
    was cut at 240 characters, and 452 of the 519 lines the HK set asks have longer ones — so the
    sentences the recent fixes wrote for the model were never sent: Sales' "on a CAS income
    statement ONLY 主营业务收入 … 营业收入 and 营业总收入 are NOT this line", the contingent-liability
    parts' one-currency rule, Level 3's "a total row that prints that column blank or nil reads 0"
    and its exclusions (findings question_carries_only_240_chars, whole_pdf_definitions_cut_at_240,
    whole_pdf_definitions_truncated, prompt_truncated_selectors_absent).
    """
    text = " ".join((item.definition or "").split())
    extra = not_this_line(item, text)
    return f"{text} {extra}".strip() if extra else text


#: A regex token a caption cannot be read out of: a class, a group, a quantifier, a backreference.
_UNREADABLE = re.compile(r"[\[\]{}()|?*+.]|\\[dDwWSpPkK0-9]")


def hint_caption(hint: str) -> list[tuple[str, str]]:
    """What an `exclude_hints` regex rules out, as `(how, caption)` pairs a person can read — how
    is "is" (the whole caption), "begins" or "contains" (`re.search`, as the matcher applies it,
    case-insensitive) — or [] when it cannot be read as captions.

    Read: a literal, with `\\s`, `\\s+` or `\\s*` as a space, an escaped punctuation mark as itself,
    `^`/`$` anchors and a `\\b` at either end; and one group of literal alternatives,
    ``^(?:资产净值|净资产)``, as each of them. Anything else (a character class, an optional group,
    a wildcard) is not guessed at.
    """
    s = (hint or "").strip()
    begins, ends = s.startswith("^"), s.endswith("$") and not s.endswith("\\$")
    s = s[1 if begins else 0:len(s) - (1 if ends else 0)]
    s = re.sub(r"^(?:\\s\*)+", "", s)
    s = re.sub(r"^\\b|\\b$", "", s)
    alternatives = [s]
    group = re.fullmatch(r"\(\?:([^()]*)\)", s)
    if group:
        alternatives = group.group(1).split("|")
    how = "is" if begins and ends else ("begins" if begins else "contains")
    out = []
    for alt in alternatives:
        text = re.sub(r"\\s[+*]?", " ", alt)
        literal = re.sub(r"\\([^A-Za-z0-9\s])", "", text)        # what is left once escapes go
        if not text.strip() or _UNREADABLE.search(literal) or "\\" in literal:
            return []
        caption = " ".join(re.sub(r"\\([^A-Za-z0-9\s])", r"\1", text).split())
        out.append((how, caption))
    return out


def not_this_line(item, definition: str = "") -> str:
    """A "Not this line: …" sentence for the captions the line's `exclude_hints` rule out that its
    definition does not already name, or "" — the rules route vetoes them by pattern
    (`mapping.excluded`), and the model was never told.

    Written short, as every caption costs every request that asks the line: "Not this line: rows
    captioned “A”, “B”; captions beginning “C”; captions containing “D”." — the same three kinds
    `hint_caption` reads ("is" the whole caption, "begins", "contains")."""
    said = definition.casefold()
    found: dict[str, list[str]] = {"is": [], "begins": [], "contains": []}
    for hint in getattr(item, "exclude_hints", None) or ():
        for how, caption in hint_caption(hint):
            if caption.casefold() not in said and caption not in found[how]:
                found[how].append(caption)
    phrases = []
    for how, lead in (("is", "rows captioned"), ("begins", "captions beginning"),
                      ("contains", "captions containing")):
        if found[how]:
            phrases.append(f"{lead} " + ", ".join(f"“{c}”" for c in found[how]))
    return f"Not this line: {'; '.join(phrases)}." if phrases else ""


# ── the question's size ────────────────────────────────────────────────────────────────────────
#
# THE DEFINITIONS ARE SENT WHOLE (`definition_of`), and they are most of every request's text: on
# the HK set the balance sheet's 208 lines are 46,827 of its question's 52,373 estimated tokens.
# Three things make that smaller without taking a word of what decides a line out of it:
#
# * A SENTENCE SEVERAL LINES OF ONE REQUEST SHARE IS SENT ONCE (`shared_sentences`), in a block of
#   numbered rules before the lines, and each line cites it by number where the sentence stood:
#   "Where the amount is only part of a larger row the balance sheet prints, … do not split it out
#   of that row." is in 27 balance-sheet definitions, "It is one of the lines this template's rollup
#   adds into Total Current Liabilities." in 28, the fixed-asset classes' "Take the class at the
#   amount the balance sheet carries it: …" in 8.
# * THE RULES ROUTE'S REPLY WORDS ARE SAID IN THIS ROUTE'S (`for_this_route`): a definition is
#   written for the line-item model route's reply, which lists the rows it read as "sources", so
#   224 of its sentences say "answer with an empty sources list". This reply has no sources list:
#   a line not printed is left out (`SYSTEM`). Where that is all a sentence says — "Where the
#   balance sheet prints no such row, answer with an empty sources list." — it restates `SYSTEM`'s
#   "Leave out a line the report does not print" and is not sent (`_RESTATES_SYSTEM`); where it is
#   the end of a condition ("…the fixed asset lines already carry net amounts: …"), the condition
#   is kept and only the reply words change.
# * The "Not this line" sentence is written short (`not_this_line`).

#: A sentence end inside a definition: ". " before anything but a lower-case letter, so "e.g. the"
#: stays one sentence. Splitting there and joining with one space gives the text back exactly
#: (`definition_of` has already folded its whitespace).
_SENTENCE_END = re.compile(r"(?<=[.。])\s(?![a-z])")
#: The other route's reply words, and this route's for the same thing.
_SOURCES_LIST = re.compile(r"([Aa])nswer (this line )?with an empty sources list")
#: A sentence that says nothing but what `SYSTEM` says of every line: leave it out when it is not
#: printed.
_RESTATES_SYSTEM = re.compile(
    r"Where (?:the (?:balance sheet|statement|section|filing) prints no such row|no such row is "
    r"printed), leave it out\.")
#: A sentence shorter than this (estimated tokens) is not worth a citation: "[R12]" is about three.
_SHARED_MIN_TOKENS = 12


def for_this_route(definition: str) -> str:
    """The definition with the rules route's reply words said in this route's, and without the
    sentences that only restate `SYSTEM`'s "leave out a line the report does not print"."""
    text = _SOURCES_LIST.sub(lambda m: ("L" if m.group(1) == "A" else "l")
                             + ("eave this line out" if m.group(2) else "eave it out"), definition)
    return " ".join(s for s in sentences(text) if not _RESTATES_SYSTEM.fullmatch(s))


def sentences(text: str) -> list[str]:
    """The definition's sentences, in order; `" ".join` of them is the text again."""
    return [s for s in _SENTENCE_END.split(text) if s]


def _tokens(text: str) -> int:
    from app.services.line_item_llm import estimated_tokens

    return estimated_tokens(text)


def shared_sentences(definitions: list[str]) -> list[str]:
    """The sentences two or more of these definitions hold, worth a citation
    (`_SHARED_MIN_TOKENS`), in the order they first appear — the request's numbered rules."""
    seen: dict[str, int] = {}
    for text in definitions:
        for s in dict.fromkeys(sentences(text)):
            seen[s] = seen.get(s, 0) + 1
    return [s for s, n in seen.items() if n > 1 and _tokens(s) >= _SHARED_MIN_TOKENS]


def cite_shared(definition: str, rules: dict[str, int]) -> str:
    """The definition with each sentence in `rules` written as its number, "[R3]", where it
    stood."""
    return " ".join(f"[R{rules[s]}]" if s in rules else s for s in sentences(definition))


_RULE = re.compile(r"^R(\d+)\. (.*)$")
_CITED = re.compile(r"\[R(\d+)\]")


def row_as_read(text: str, key: str) -> str:
    """What a question says of one line: its row, from its key to its end, with every "[Rn]" it
    cites written out as the rule — the text the model reads for that line. "" when the question
    does not ask it."""
    rules: dict[str, str] = {}
    row = ""
    for line in text.splitlines():
        m = _RULE.match(line)
        if m:
            rules[m.group(1)] = m.group(2)
        elif line.startswith(f"- {key}: "):
            row = line
    return _CITED.sub(lambda m: rules.get(m.group(1), m.group(0)), row)


def question(lines: list, part: Part, part_number: int, part_count: int,
             item_set=None, carried: int = 0) -> str:
    """The request's text: what the file holds, the rules several of its lines share, then the
    lines asked, one per row, then the parts the template adds together (`sibling_groups`, with
    `item_set`).

    Each line's definition is its whole definition (`definition_of`), said for this route
    (`for_this_route`), with each sentence it shares with another line of the request cited by
    number (`shared_sentences`, `cite_shared`). Expanding the citations gives back every sentence
    `for_this_route` kept, in its place — `tests/test_the_whole_pdf_question_keeps_every_
    deciding_clause.py` checks it line by line."""
    if part.faces_only:
        where = (f"The PDF file holds the {len(part.pages)} pages of the report that print the "
                 f"financial statements themselves, without their notes"
                 + (f" (part {part_number} of {part_count}; the notes are in the other parts, "
                    "sent separately — answer from this part only)" if part_count > 1 else "")
                 + ". A line these pages do not print, leave out: the lines read from the notes "
                   "are asked again with the notes.")
    else:
        where = (f"The PDF file holds {len(part.pages)} pages of the report"
                 + (f" (part {part_number} of {part_count}; the other parts are sent separately "
                    "— answer from this part only"
                    + ("; every part holds the statements themselves, the notes are split "
                       "between the parts" if carried else "") + ")" if part_count > 1 else "")
                 + ".")
    statement = (getattr(lines, "statement", "") or (statement_of(lines[0]) if lines else "")
                 ).replace("_", " ")
    definitions = [for_this_route(definition_of(item)) for item in lines]
    shared = shared_sentences(definitions)
    rules = {s: i for i, s in enumerate(shared, start=1)}
    rows = []
    for item, definition in zip(lines, definitions):
        marks = [m for m in (
            "TOTAL" if item.type == "calculated" else "",
            "TEXT" if is_text(item) else "",
            f"PART of {item.parent}" if is_part(item) and item.parent else
            ("PART" if is_part(item) else ""),
            _READ_FROM.get(str(getattr(item.route, "value", item.route) or ""), "")) if m]
        definition = cite_shared(definition, rules)
        rows.append(f"- {item.key}: {item.label or item.key}"
                    + (f" [{'; '.join(marks)}]" if marks else "")
                    + (f" — {definition}" if definition else ""))
    common = ("Rules several lines share — a definition below that cites [R1] says what R1 says, "
              "where the citation stands, for that line:\n"
              + "\n".join(f"R{i}. {s}" for i, s in enumerate(shared, start=1)) + "\n\n"
              if shared else "")
    groups = sibling_groups(item_set, lines) if item_set is not None else []
    added = ("\n\nAdded together — the template sums each group below into the line it names, so "
             "one printed holding or row goes into at most one part of a group, never into two, "
             "even where two of their definitions could each describe it:\n"
             + "\n".join(f"Into {line}: {', '.join(keys)}." for line, keys in groups)
             if groups else "")
    if getattr(lines, "statement", "") == STATEMENTS_REST:
        heads = [statement_of(item).replace("_", " ") for item in lines]
        body = ("These are the statements' lines read from the notes or anywhere in the report; "
                "the statements' other lines are asked with the statements alone, separately.")
        for i, (head, row) in enumerate(zip(heads, rows)):
            if i == 0 or head != heads[i - 1]:
                body += f"\n\nTemplate lines ({head}):"
            body += "\n" + row
        listed = body
    else:
        listed = f"Template lines ({statement}):\n" + "\n".join(rows)
    return (f"{where}\n\n{common}{listed}" + added
            + "\n\nAnswer every line above that the report prints; leave out the rest.")


def _as_text(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return value


class WholePdfRow(BaseModel):
    """One printed row of a figure that is a sum of rows."""
    caption: str = ""
    amount: str = ""

    @field_validator("amount", mode="before")
    @classmethod
    def amount_as_text(cls, value):
        return _as_text(value)


class WholePdfFigure(BaseModel):
    basis: Literal["consolidated", "standalone"] = "consolidated"
    period: Literal["current", "prior"] = "current"
    amount: str
    page: int = 0
    caption: str = ""
    period_end: str = ""
    parts: list[WholePdfRow] = Field(default_factory=list)

    @field_validator("amount", mode="before")
    @classmethod
    def amount_as_text(cls, value):
        return _as_text(value)


class WholePdfLine(BaseModel):
    key: str
    figures: list[WholePdfFigure] = Field(default_factory=list)
    # A TEXT line's answer: the words, and the pages of the file they are on (1 = its first) —
    # a phrase's one page; every page a narrative is written from.
    text: str = ""
    text_pages: list[int] = Field(default_factory=list)


class WholePdfReply(BaseModel):
    """The model's reply: the statements' units as printed, and the lines it found."""
    # Its whole budget is for the JSON (`adapters.openai_llm.build_body`).
    compact_json_reply: ClassVar[bool] = True
    units: str = ""
    lines: list[WholePdfLine] = Field(default_factory=list)


# ── reading the figure, and finding it on its page ─────────────────────────────────────────────

_NIL = {"-", "—", "–", "nil", "none"}


def parse_amount(text) -> Decimal | None:
    """The figure the model wrote, as a signed Decimal; None when it is no number."""
    s = str(text or "").strip()
    if s.lower() in _NIL:
        return Decimal(0)
    s = s.replace("−", "-").replace("–", "-")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if s.startswith("-"):
        negative, s = True, s[1:]
    s = re.sub(r"[^\d.]", "", s.replace(",", ""))
    if not s or s.count(".") > 1:
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if negative else value


_GROUPED = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SPACED = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:\.\d+)?")


def printed_numbers(text: str) -> set[Decimal]:
    """Every number printed in a page's text, unsigned: "1,234,567", "1 234 567", "(1,234.5)"."""
    out: set[Decimal] = set()
    for pattern, strip in ((_GROUPED, ","), (_SPACED, "   ")):
        for match in pattern.finditer(text or ""):
            token = match.group(0).rstrip(",")
            for ch in strip:
                token = token.replace(ch, "")
            try:
                out.add(Decimal(token))
            except InvalidOperation:
                continue
    return out


@dataclass
class Check:
    """Whether a figure was found in the PDF's own text, and where."""
    status: str               # on_page | moved | parts_on_page | nil | no_text | not_found
    page: int | None          # the original 0-based page the figure is printed on
    note: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status in ("on_page", "moved", "parts_on_page")


def check_figure(amount: Decimal, parts: list[Decimal], page: int | None, part: Part,
                 texts: dict[int, set[Decimal]]) -> Check:
    """Look for the figure on the page it cites, in the PDF's own text.

    A sum of rows is confirmed when every row is on the page and the rows add up to it. A figure
    not on its page but on exactly one other page of the part is moved there — a model citing the
    printed folio rather than the file's page is the common cause.
    """
    if amount == 0 and not parts:
        return Check("nil", page)
    wanted = [abs(p) for p in parts] if parts else [abs(amount)]
    if parts and sum(parts) != amount:
        return Check("not_found", page, f"rows add to {sum(parts)}, not {amount}")
    if page is not None:
        found = texts.get(page)
        if found is None or not found:
            return Check("no_text", page)
        if all(w in found or w == 0 for w in wanted):
            return Check("parts_on_page" if parts else "on_page", page)
    elsewhere = [p for p in part.pages if p != page
                 and all(w in texts.get(p, ()) or w == 0 for w in wanted)]
    if len(elsewhere) == 1:
        return Check("moved", elsewhere[0], f"cited page {_folio(page)}, printed on "
                                            f"page {_folio(elsewhere[0])}")
    return Check("not_found", page, ("not on the cited page" if not elsewhere
                                     else f"not on the cited page; on {len(elsewhere)} others"))


def _folio(page: int | None) -> str:
    return "?" if page is None else str(page + 1)


def page_numbers(raw: bytes, pages: list[int]) -> dict[int, set[Decimal]]:
    """The numbers printed on each page, from the PDF's own text layer (none on a scanned page)."""
    return {p: text.numbers for p, text in read_pages(raw, pages).items()}


@dataclass
class PageWord:
    text: str
    box: BBox                 # page space, 0..1 — where the viewer draws the highlight


@dataclass
class PageText:
    """One page of the PDF's own text: its numbers (the check) and its words with their boxes
    (the citation)."""
    numbers: set[Decimal]
    words: list[PageWord]
    upright: bool = True
    text: str = ""            # the page's text, for a TEXT line's check (`text_lines`)


def read_pages(raw: bytes, pages: list[int]) -> dict[int, PageText]:
    """Each page's numbers and words, from the PDF's own text layer — nothing on a scanned page.

    THE BOXES ARE THE RULES ROUTE'S: each word's box divided by the page's size and clamped, as
    `pdf_extract._native_words` keeps a figure's provenance, so a whole-PDF figure is highlighted
    where a rules-route figure on the same page would be.
    """
    import fitz

    from app.services.pdf_extract import text_rotation

    out: dict[int, PageText] = {}
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        for p in pages:
            if not 0 <= p < doc.page_count:
                continue
            page = doc.load_page(p)
            w, h = max(page.rect.width, 1.0), max(page.rect.height, 1.0)
            words = [PageWord(text=t, box=BBox(x0=_clamp(x0 / w), y0=_clamp(y0 / h),
                                               x1=_clamp(x1 / w), y1=_clamp(y1 / h)))
                     for x0, y0, x1, y1, t, *_ in page.get_text("words") if t.strip()]
            try:
                upright = text_rotation(page) in (0, 360)
            except Exception:  # noqa: BLE001 - an unreadable angle is read as upright
                upright = True
            plain = page.get_text("text")
            out[p] = PageText(numbers=printed_numbers(plain), words=words, upright=upright,
                              text=plain)
    finally:
        doc.close()
    return out


def locate_text(text: str, page: PageText | None) -> BBox | None:
    """The box of the printed row that holds a phrase, or None — for its citation. Compared as
    the rules route compares a phrase with its page (`text_lines.fold`)."""
    from app.services.text_lines import fold

    if page is None or not page.upright:
        return None
    want = fold(text)
    rows: list[list[PageWord]] = []
    for w in sorted(page.words, key=lambda w: (w.box.y0, w.box.x0)):
        if rows and _same_row(rows[-1][0].box, w.box):
            rows[-1].append(w)
        else:
            rows.append([w])
    for row in rows:
        joined = fold("".join(w.text for w in sorted(row, key=lambda w: w.box.x0)))
        if want and (want in joined or (len(joined) >= 4 and joined in want)):
            inside = [w.box for w in row if fold(w.text) and fold(w.text) in want]
            return _union(inside) or _union([w.box for w in row])
    return None


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


# A footnote marker glued on is part of the printed word ("1,897,880*", "(3,596,236)*" on China
# SCE's statement of changes in equity) and not of the figure.
_WORD_NUMBER = re.compile(
    r"^[(（]?[-−–]?[$¥€£₹]?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?[)）]?[*#^†‡]*$")


def _word_value(text: str) -> Decimal | None:
    """The unsigned number one printed word is, or None: "1,234.5", "(1,234.5)", "-300"."""
    m = _WORD_NUMBER.match(text.strip())
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", "") + (m.group(2) or ""))
    except InvalidOperation:
        return None


def _same_row(a: BBox, b: BBox) -> bool:
    """Whether two words sit on one printed row: their heights overlap by half the smaller."""
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return overlap > 0.5 * max(1e-6, min(a.y1 - a.y0, b.y1 - b.y0))


def _union(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return BBox(x0=min(b.x0 for b in boxes), y0=min(b.y0 for b in boxes),
                x1=max(b.x1 for b in boxes), y1=max(b.y1 for b in boxes))


def _row_caption(hit: PageWord, words: list[PageWord]) -> list[PageWord]:
    """The caption of the row a figure is printed on: the words left of it on its row, up to the
    first figure (a note reference or another column)."""
    left = sorted((w for w in words if w is not hit and w.box.x1 <= hit.box.x0 + 1e-6
                   and _same_row(w.box, hit.box)), key=lambda w: w.box.x0)
    caption: list[PageWord] = []
    for w in left:
        if _word_value(w.text) is not None:
            break
        caption.append(w)
    return caption


_LOOSE = re.compile(r"[\s:：,，.。()（）\[\]【】\-—–_/、*#^$]+")


def _likeness(caption: str, printed: str) -> float:
    """How nearly the caption the model gave is the caption printed on a row, 0..1."""
    import difflib

    a, b = _LOOSE.sub("", caption or "").lower(), _LOOSE.sub("", printed or "").lower()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b)) * 0.5 + 0.5
    return difflib.SequenceMatcher(None, a, b).ratio()


def locate(amount: Decimal, caption: str, page: PageText | None,
           period: str = "current") -> tuple[BBox | None, BBox | None]:
    """`(the figure's box, its caption's box)` on its page, or Nones when it cannot be placed.

    The figure is a printed word equal to it, unsigned; where it is printed more than once (a
    total and the same total on the other side of a balance sheet), the one whose row's caption is
    most like the caption the model gave. Where one row prints it twice — share capital unchanged
    from one year to the next — the current year is the left of the two and the prior year the
    one after it, the order every statement in the corpus prints its years in. A nil, or a figure
    not printed as one word, is placed by its caption alone, if a row prints a caption like it. On
    a page printed sideways the figure's own box is given and no caption box: its row does not run
    across the page.
    """
    if page is None:
        return None, None
    want = abs(amount) if amount is not None else None
    hits = ([w for w in page.words if _word_value(w.text) == want]
            if want is not None and want != 0 else [])
    if hits:
        if not page.upright:
            return hits[0].box, None
        scored = [(_likeness(caption, " ".join(c.text for c in cap)), i, h, cap)
                  for i, h in enumerate(hits) for cap in [_row_caption(h, page.words)]]
        top = max(t[0] for t in scored)
        best = min((t for t in scored if t[0] >= top - 1e-9), key=lambda t: t[1])
        # The same figure again on the best row, left to right: the year is its position.
        on_row = sorted((t for t in scored if t[0] >= top - 1e-9
                         and _same_row(t[2].box, best[2].box)), key=lambda t: t[2].box.x0)
        if len(on_row) > 1:
            best = on_row[min(1, len(on_row) - 1)] if period == "prior" else on_row[0]
        return best[2].box, _union([c.box for c in best[3]])
    if not page.upright or not caption:
        return None, None
    # BY CAPTION ALONE: the row whose leading words read most like the caption, when one is close.
    best_score, best_box = 0.0, None
    rows: list[list[PageWord]] = []
    for w in sorted(page.words, key=lambda w: (w.box.y0, w.box.x0)):
        if rows and _same_row(rows[-1][0].box, w.box):
            rows[-1].append(w)
        else:
            rows.append([w])
    for row in rows:
        lead: list[PageWord] = []
        for w in sorted(row, key=lambda w: w.box.x0):
            if _word_value(w.text) is not None:
                break
            lead.append(w)
        score = _likeness(caption, " ".join(w.text for w in lead))
        if score > best_score:
            best_score, best_box = score, _union([w.box for w in lead])
    return (best_box, best_box) if best_score >= 0.8 else (None, None)


# ── putting the answers together ───────────────────────────────────────────────────────────────

@dataclass
class Reading:
    """One figure the model gave, with its check: the line, basis and period it is for."""
    key: str
    basis: str
    period: str
    amount: Decimal
    caption: str
    period_end: str
    part: int
    check: Check
    on_face: bool
    rows: list[tuple[str, Decimal]] = field(default_factory=list)
    others: list[str] = field(default_factory=list)   # different figures read for the same slot
    # WHERE IT IS PRINTED, for the citation: the figure's box and its caption's on its page, and
    # for a sum of rows each row's (`locate`). None where the text layer cannot place it.
    box: BBox | None = None
    label_box: BBox | None = None
    row_boxes: list[tuple[BBox | None, BBox | None]] = field(default_factory=list)
    # What the merge found (`merge`): flags for the row, and whether the slot goes to review.
    flags: list[str] = field(default_factory=list)
    review: bool = False


#: The statements whose own lines are read off a face page.
FACE_STATEMENTS = ("balance_sheet", "profit_and_loss", "cash_flow")


def reads_on_face(item) -> bool | None:
    """Where the line's figure is printed, by its route: True on a statement page ([read from the
    statement], and a statement's own line with no route or "anywhere", which the prompt tells to
    cite the statement), False on a note page ([read from the notes], [read from the narrative]),
    None where either will do (a part or a note line read anywhere)."""
    route = str(getattr(getattr(item, "route", None), "value", getattr(item, "route", None)) or "")
    if route == "face":
        return True
    if route in ("note_tables", "prose"):
        return False
    if statement_of(item) in FACE_STATEMENTS and not is_part(item):
        return True
    return None


#: On the row of a [read from the statement] line whose figure the model cited on a page that is
#: not a statement's (finding face_line_from_note_page_accepted).
FACE_LINE_OFF_FACE = "face_line_not_on_a_statement_page"
#: On the row where two parts gave two figures for one slot and nothing chose between them.
PARTS_DISAGREE = "parts_disagree"


def merge(readings: list[Reading], items: dict | None = None, faces: set[str] | None = None
          ) -> dict[tuple[str, str, str], Reading]:
    """One figure per (line, basis, period).

    Where the parts gave two: a figure confirmed on its page beats one that is not; then THE PAGE
    THE LINE IS READ FROM (`reads_on_face`, with `items` — key to line) — a line read from the
    notes prefers a note page, a statement's line a statement page. (It was "a statement page
    beats a note page" for every line, so a note part answered off the face in part 1 — Sales
    from 营业收入 — beat the revenue note's 主营业务收入 read in part 2.) A different figure set
    aside is recorded on the one kept (`other_reading`).

    WHERE THE RULE CANNOT CHOOSE — two figures equally confirmed, on pages of the same kind — the
    earlier part's is kept, and the slot GOES TO REVIEW with both figures named (`PARTS_DISAGREE`):
    a filing that prints the statements twice, as 0857 prints a CAS and an IFRS set, can have one
    set read in each part, and part 1's figure never stands silently.

    A [read from the statement] line kept off a page that is not a statement's goes to review too
    (`FACE_LINE_OFF_FACE`): the second part once held notes only and was still asked the
    statements' lines, so a line the face does not print could be filled from a note. `faces`, the
    statements the classifier found a face page of: a line of a statement it found none of could
    only be read off another kind of page, and is not flagged for it.
    """
    items = items or {}

    def fit(r: Reading) -> tuple[bool, bool, bool]:
        # A line that may be read either side still prefers the statement page, as the prompt
        # tells the model to cite one ("a figure printed both on a statement and in a note").
        want = reads_on_face(items[r.key]) if r.key in items else None
        return r.check.confirmed, want is None or r.on_face == want, r.on_face

    kept: dict[tuple[str, str, str], Reading] = {}
    for r in readings:
        slot = (r.key, r.basis, r.period)
        held = kept.get(slot)
        if held is None:
            kept[slot] = r
            continue
        better = fit(r) > fit(held)
        winner, loser = (r, held) if better else (held, r)
        if loser.amount != winner.amount:
            other = f"{loser.amount}@p{_folio(loser.check.page)}"
            winner.others.append(other)
            if fit(r) == fit(held) and loser.check.confirmed:
                winner.review = True
                flag = (f"{PARTS_DISAGREE}:{winner.amount}@p{_folio(winner.check.page)}"
                        f"(part {winner.part + 1}):{other}(part {loser.part + 1})")
                if flag not in winner.flags:
                    winner.flags.append(flag)
        winner.others.extend(o for o in loser.others if o not in winner.others)
        # A dispute the set-aside figure had with a third part stays on the slot.
        disputes = [f for f in loser.flags if f.startswith(PARTS_DISAGREE)
                    and f not in winner.flags]
        if disputes:
            winner.flags.extend(disputes)
            winner.review = True
        kept[slot] = winner
    for r in kept.values():
        item = items.get(r.key)
        route = str(getattr(getattr(item, "route", None), "value", getattr(item, "route", None))
                    or "") if item is not None else ""
        if (route == "face" and not r.on_face and r.check.page is not None
                and (faces is None or statement_of(item) in faces)):
            r.review = True
            if FACE_LINE_OFF_FACE not in r.flags:
                r.flags.append(FACE_LINE_OFF_FACE)
    return kept
