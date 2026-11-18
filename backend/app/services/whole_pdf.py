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

PDF = "application/pdf"

#: What a request carries beside its file, counted against `whole_pdf_max_request_mb`: the system
#: text, the line list and the JSON envelope. Generous — the line list of 60 lines is about 20 KB.
REQUEST_OVERHEAD_BYTES = 256 * 1024

#: The line types the model is asked for. "extracted" lines are read off the page; "calculated"
#: lines are totals the template also computes, asked for the figure the filing PRINTS, so the
#: template's checks compare a printed total with its printed parts. "derived" and "intermediate"
#: lines are computed from other lines and are not printed as such.
ASKED_TYPES = ("extracted", "calculated")

#: The order statements are asked in, the primary statements first. A statement not named here
#: comes after them, in the order the set lists it.
STATEMENT_ORDER = ("balance_sheet", "profit_and_loss", "cash_flow", "notes",
                   "covenants_supplemental", "statement_setup")

#: Reply budget per line asked: up to four figures (two bases, two periods), each with its page,
#: caption and the rows of a sum. A reply cut off is asked once more with twice the room
#: (`ports.llm.complete_with_room`).
_TOKENS_PER_LINE = 220
_TOKENS_RESERVE = 1_024
#: …at most this: a statement's whole line list is asked in one request (202 lines on the HK
#: balance sheet), and most of its lines are not printed and cost nothing. The longest reply
#: measured, a balance sheet asked 60 lines at a time, was 3,878 tokens.
_TOKENS_CEILING = 32_000


# ── which pages, in which parts ────────────────────────────────────────────────────────────────

def pages_to_send(doc, *, choice: str, included_pages=None,
                  sections_off=None) -> tuple[list[int], str]:
    """The page indexes to send, in order, and why those.

    "statements" sends the pages the next run would read as the Page Scope screen asks it
    (`page_reading.reader_targets`): the statement faces and notes the classifier found — every
    page when it found none — narrowed by a person's page selection and the sections they switched
    off. "all" sends every page, narrowed the same way.
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
    if included_pages and page_reading.selection_applies(base, included_pages):
        why = "page_scope"
    if sections_off:
        why += f"; sections off: {','.join(sorted(sections_off))}"
    return sorted({int(p.index) for p in chosen}), why


@dataclass
class Part:
    """One file sent: the pages it holds (0-based, of the original), in order, and its bytes."""
    pages: list[int]
    pdf: bytes

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


def build_parts(raw: bytes, pages: list[int], *, max_pages: int,
                max_request_bytes: int) -> tuple[list[Part], list[int]]:
    """The chosen pages as PDF files, each within the request's limits, and the pages that could
    not be sent at all (a single page larger than a whole request).

    Cut into as few parts of at most `max_pages` as will do, of even size (`balanced`); a part
    whose file in base64, with the request's text, is over `max_request_bytes` is halved until it
    fits.
    """
    import fitz  # PyMuPDF, already the app's PDF reader

    max_pages = max(1, int(max_pages))
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

        def fit(chunk: list[int]) -> None:
            data = make(chunk)
            part = Part(pages=list(chunk), pdf=data)
            if part.b64_bytes + REQUEST_OVERHEAD_BYTES <= max_request_bytes:
                parts.append(part)
                return
            if len(chunk) == 1:
                dropped.append(chunk[0])
                return
            mid = len(chunk) // 2
            fit(chunk[:mid])
            fit(chunk[mid:])

        for chunk in balanced(pages, max_pages):
            fit(chunk)
    finally:
        src.close()
    return parts, dropped


def as_document(part: Part, number: int) -> dict:
    """The part as a file block for `ports.llm.LlmMessage.documents` — named neutrally, never by
    the uploaded file's own name."""
    return {"media_type": PDF, "data": base64.b64encode(part.pdf).decode("ascii"),
            "name": f"document-{number}.pdf"}


# ── which lines, in which requests ─────────────────────────────────────────────────────────────

def statement_of(item) -> str:
    st = item.statement or (item.statements[0] if item.statements else None)
    return getattr(st, "value", st) or "other"


def lines_to_ask(item_set) -> list:
    """The template lines a model can be asked for: printed in the output, not an internal part,
    a figure (not a phrase or prose), and read off the page or printed as a total."""
    out = []
    for item in item_set.items:
        if not item.in_output or item.namespace == "internal" or item.key.startswith("sub__"):
            continue
        if item.type not in ASKED_TYPES:
            continue
        if getattr(item, "output_structure", "value") != "value":
            continue
        out.append(item)
    rank = {s: i for i, s in enumerate(STATEMENT_ORDER)}
    order = {item.key: i for i, item in enumerate(out)}
    return sorted(out, key=lambda it: (rank.get(statement_of(it), len(rank)), order[it.key]))


def chunk_lines(lines: list, per_request: int) -> list[list]:
    """Lines in requests of at most `per_request`, never mixing two statements in one request."""
    per_request = max(1, int(per_request))
    chunks: list[list] = []
    current: list = []
    for item in lines:
        if current and (len(current) >= per_request
                        or statement_of(current[-1]) != statement_of(item)):
            chunks.append(current)
            current = []
        current.append(item)
    if current:
        chunks.append(current)
    return chunks


def max_tokens_for(n_lines: int) -> int:
    return min(_TOKENS_CEILING, _TOKENS_RESERVE + max(1, n_lines) * _TOKENS_PER_LINE)


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
- A figure printed both on a statement and in a note: cite the statement.
- Read figures from the financial statements and their notes, not from highlights, summaries, \
the directors' report or the management discussion.
- units: how the statements state their units, as printed (for example "HK$'000", "RMB", \
"₹ in crore", "in millions of U.S. dollars").
"""


def _definition(item, limit: int = 240) -> str:
    text = " ".join((item.definition or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def question(lines: list, part: Part, part_number: int, part_count: int) -> str:
    """The request's text: what the file holds, then the lines asked, one per row."""
    where = (f"The PDF file holds {len(part.pages)} pages of the report"
             + (f" (part {part_number} of {part_count}; the other parts are sent separately — "
                "answer from this part only)" if part_count > 1 else "") + ".")
    statement = statement_of(lines[0]).replace("_", " ") if lines else ""
    rows = []
    for item in lines:
        total = " TOTAL" if item.type == "calculated" else ""
        definition = _definition(item)
        rows.append(f"- {item.key}: {item.label or item.key}{total}"
                    + (f" — {definition}" if definition else ""))
    return (f"{where}\n\nTemplate lines ({statement}):\n" + "\n".join(rows)
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
    import fitz

    out: dict[int, set[Decimal]] = {}
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        for p in pages:
            if 0 <= p < doc.page_count:
                out[p] = printed_numbers(doc.load_page(p).get_text("text"))
    finally:
        doc.close()
    return out


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


def merge(readings: list[Reading]) -> dict[tuple[str, str, str], Reading]:
    """One figure per (line, basis, period). Where the parts gave two, a figure confirmed on its
    page beats one that is not, and a statement page beats a note page; the figure set aside is
    recorded on the one kept when it is a different amount."""
    kept: dict[tuple[str, str, str], Reading] = {}
    for r in readings:
        slot = (r.key, r.basis, r.period)
        held = kept.get(slot)
        if held is None:
            kept[slot] = r
            continue
        better = ((r.check.confirmed, r.on_face) > (held.check.confirmed, held.on_face))
        winner, loser = (r, held) if better else (held, r)
        if loser.amount != winner.amount:
            winner.others.append(f"{loser.amount}@p{_folio(loser.check.page)}")
        winner.others.extend(o for o in loser.others if o not in winner.others)
        kept[slot] = winner
    return kept
