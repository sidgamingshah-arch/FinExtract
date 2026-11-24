"""EVERY WHOLE-PDF FIGURE, FOUND AGAIN IN THE RULES' READING OF ITS PAGE — and held to the checks
every model answer is held to (`services.answer_checks`).

WHY. The whole-PDF route (`stages.whole_pdf`) asks the model to read the PDF file itself and give
each line's figure with the page it is printed on. The only check it made was that the number is
printed somewhere on that page (`services.whole_pdf.check_figure`), so none of the refusals the
line-item model route applies reached it: the face's total 营业收入 given for the CAS principal-
revenue part (湖北能源's parent company published 40,617,220.64 of 其他业务 revenue as Sales), a
sentence's US-dollar letters of credit added to yuan (330,047,162.11 "RMB" of contingent
liabilities), a 万元 note row written as 元 (688008's key-management pay 5,849.45 where the rules
publish 58,494,500.00), a derivative's notional on a securities part, a 其中 row added to the row
it breaks down, and a Level 2 or total figure given as Level 3 — each confirmed "on the page".

HOW. The rules read the filing first on this route (`core.pipeline.whole_pdf_pipeline`): their
face rows and note tables are the document's when the whole-PDF stage writes its rows. So each
figure — and each printed row of a figure the model added up — is looked for among the cells the
rules read ON THE PAGE IT CITES, by its amount, and where the page prints that amount more than once
by the box the figure was placed at (`services.whole_pdf.locate`, which draws its boxes the way the
extraction does) and then by the caption (`Place`):

  * a FACE ROW: its caption as printed, its statement, its row id, its unit;
  * a NOTE TABLE ROW: its note number and title, its caption, the cell's column heading and the
    period and basis the rules filed that cell under, its unit;
  * A SENTENCE: an amount the rules read in no row, with a currency or a scale word printed
    straight beside it — its currency and scale are read off the page's own text there, never off
    the model's words;
  * none of these: a figure printed on the page that the rules' reading has no cell for. It keeps
    every check that needs no cell (the face-caption veto reads the page's caption), and a line
    whose column matters goes to review rather than being taken on trust.

Each located place becomes ONE CITATION ENTRY in `answer_checks`' contract, and the answer goes
through `answer_checks.check_answer` — the same call, the same order, the same log lines as the
line-item route. On top of those, the checks only a figure with a column can be held to
(`Judge._column_checks`):

  * THE PERIOD AND THE BASIS: a figure filed under one year but printed in the other year's column
    is RE-FILED where the column is certain (one cell on the page prints it, under a heading that
    names a period), and goes to review with the reason otherwise; the same for the basis.
  * THE COLUMN SELECTOR: a line that reads one column of a table (`note_source.column_heading_any`
    and `column_heading_none` — the two Level 3 parts) is refused a figure printed in a column its
    selector does not admit (a Level 2 or a total figure given as Level 3), and a figure printed in
    a sentence (a transfer out of Level 3, given as a year-end Level 3).
  * NOT A FINANCIAL ASSET: such a part is refused a row captioned as an investment property, a
    biological asset or a liability, or a row of a table titled as one, by its own exclusions
    (`not_a_financial_asset`). A table titled for assets AND liabilities together — the CAS
    fair-value table — is judged by its rows' captions, as the rules judge it.

ACROSS ANSWERS (`Judge.settle`): two parts of one parent holding the same note money are settled
by `answer_checks.settle_siblings`, as on the line-item route.

A REFUSED FIGURE IS NOT WRITTEN, and the row says why (`whole_pdf:refused:<rule>`); a sum keeps its
other rows. Nothing here runs on any other route: it is called only by `stages.whole_pdf`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.core.models.enums import PageKind
from app.services import answer_checks, note_sourced
from app.services import whole_pdf as W

#: The flag every row and figure of the route carries (`stages.whole_pdf.FLAG`; a test pins them).
FLAG = "whole_pdf"
#: The flag the review queue selects a doubtful line on.
REVIEW_FLAG = "low_mapping_confidence"
#: The prefix of a refusal's flag on the row: `whole_pdf:refused:<rule>:<basis>:<period>:<amount>`.
REFUSED = f"{FLAG}:refused"

#: How near two boxes' corners are, as a fraction of the page, to be one printed figure. The rules'
#: cells and `whole_pdf.locate`'s boxes are drawn from the same words, so they agree to the point;
#: this allows for rounding.
_SAME_BOX = 0.006

#: A column heading that names a period, so a figure's column says which year it is: a CAS
#: 期末余额 / 上年年末余额, 本期发生额 / 上期发生额, an HKFRS "2024" / "At 31 December 2023".
_PERIOD_HEADING = re.compile(
    r"期末|期初|年末|年初|本期|上期|本年|上年|当期|當期|(?:19|20)\d{2}|current|prior|this\s+year|"
    r"last\s+year|at\s+31|as\s+at", re.IGNORECASE)


# ── the rules' reading of each page ──────────────────────────────────────────────────────────────

@dataclass
class Cell:
    """One printed figure the rules read: a face row's or a note row's value on one page."""
    kind: str                 # "face" | "note" | "page" (a row of a page that is neither)
    page: int
    amount: Decimal           # unsigned, as printed
    ev: object                # the ExtractedValue
    key: str                  # its key in the row's `values`
    row: object               # LineItem (face, page) or NoteItem (note)
    table: object | None = None
    box: tuple[float, float, float, float] | None = None

    @property
    def caption(self) -> str:
        return str(getattr(self.row, "raw_label", None) or getattr(self.row, "source_label", "")
                   or "")

    @property
    def heading(self) -> str:
        return str(getattr(self.ev, "column_heading", "") or "")

    @property
    def period(self) -> str | None:
        """The year the cell is printed under, where the reading KNOWS it, else None: its
        column's period (`column_period`); a face row's year column; a note cell's label only
        under a heading that names a period. A note table whose columns are levels or classes
        keys its cells by position — 1966's note 46 files both its Level 3 cells as `prior`, under
        'Significant unobservable inputs (Level 3)', in the 2023 table and the 2022 one alike — and
        that position says nothing about the year."""
        cp = getattr(self.ev, "column_period", None)
        if cp in ("current", "prior"):
            return cp
        label = str(getattr(self.ev, "period_label", "") or "")
        if label not in ("current", "prior"):
            return None
        if self.kind == "note" and not _PERIOD_HEADING.search(self.heading):
            return None
        return label

    @property
    def basis(self) -> str:
        if self.kind == "note":
            return note_sourced._table_basis(self.table, self.ev) or "consolidated"
        b = getattr(self.ev, "basis", "")
        return str(getattr(b, "value", b) or "consolidated")

    def provenance(self) -> dict | None:
        prov = getattr(self.ev, "provenance", None)
        return prov.model_dump(mode="json") if prov is not None else None


def _amount(value) -> Decimal | None:
    try:
        return abs(Decimal(str(value))) if value is not None else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _box(ev) -> tuple[float, float, float, float] | None:
    prov = getattr(ev, "provenance", None)
    box = getattr(prov, "value_bbox", None) or getattr(prov, "bbox", None)
    if box is None:
        return None
    return (float(box.x0), float(box.y0), float(box.x1), float(box.y1))


class RulesReading:
    """Every printed figure the extraction placed, by page: the face rows the rules read (still the
    document's rows when the whole-PDF stage judges its answers) and the note tables."""

    def __init__(self, doc):
        kinds = {p.index: p.kind for p in getattr(doc, "pages", None) or ()}
        self.by_page: dict[int, list[Cell]] = {}

        def add(cell: Cell) -> None:
            self.by_page.setdefault(cell.page, []).append(cell)

        for li in getattr(doc, "line_items", None) or ():
            for k, ev in (li.values or {}).items():
                prov = getattr(ev, "provenance", None)
                amount = _amount(ev.value_raw if ev.value_raw is not None else ev.value)
                if prov is None or prov.page_index is None or amount is None:
                    continue
                page = int(prov.page_index)
                add(Cell("face" if kinds.get(page) == PageKind.FACE else "page", page, amount, ev,
                         k, li, None, _box(ev)))
        for table in getattr(doc, "notes", None) or ():
            for row in getattr(table, "items", None) or ():
                for k, ev in (getattr(row, "values", None) or {}).items():
                    prov = getattr(ev, "provenance", None)
                    amount = _amount(getattr(ev, "value", None))
                    if prov is None or prov.page_index is None or amount is None:
                        continue
                    add(Cell("note", int(prov.page_index), amount, ev, k, row, table, _box(ev)))

    def cells_on(self, page: int | None) -> list[Cell]:
        return self.by_page.get(page, []) if page is not None else []

    def find(self, page: int | None, amount: Decimal, caption: str, box, period: str,
             repeats: dict, said: tuple) -> tuple[Cell | None, list[Cell]]:
        """`(the cell that prints this figure, every cell on the page printing its amount)`.

        By the box the figure was placed at first (one printed figure, one box); else the cell
        whose caption reads most like the model's, the year breaking a tie on one row (`locate`'s
        rule); else, alone on the page, the only one. AN IDENTICAL CITATION AGAIN — one sum naming
        the same caption and amount twice — takes the next cell of that amount it has not taken
        (`answer_checks.take_tied_row`, the line-item route's tie-break): two guarantees of the
        same amount are two printed rows, and the same row cannot be added twice.
        """
        want = abs(amount)
        found = [c for c in self.cells_on(page) if c.amount == want]
        if not found:
            return None, []
        hit = None
        if box is not None:
            near = [c for c in found if c.box is not None
                    and abs(c.box[0] - box.x0) <= _SAME_BOX and abs(c.box[1] - box.y0) <= _SAME_BOX]
            hit = near[0] if near else None
        scored = sorted(found, key=lambda c: (-W._likeness(caption, c.caption),
                                              c.period != period, c.page, c.box or (0, 0)))
        # …UNLESS THE BOXED CELL IS NOT THE ROW THE MODEL NAMED. A row and the total that repeats
        # it print one amount; `locate` drew the box by the page's own caption words, which a
        # caption wrapped over two lines reads poorly — 300319 七、2 prints its parent row
        # 以公允价值计量且其变动计入当期损益的金融资产 and its 合计 both as 431,478,888.81, and the box
        # fell on the 合计. The row whose caption is the model's is the one it cited.
        if hit is not None and (W._likeness(caption, scored[0].caption)
                                > W._likeness(caption, hit.caption) + 0.3):
            hit = scored[0]
        if hit is None:
            hit = scored[0]
        ordered = sorted(found, key=lambda c: (c.box or (9, 9))[1])
        hit = answer_checks.take_tied_row(repeats, said, hit, ordered, ident=id)
        return hit, found

    def column_heading_at(self, page: int | None, box) -> str:
        """The heading of the column a figure sits in, from the cells the rules read in that column
        of the same page — for a figure the rules read no cell for (a total row their table missed).
        Numbers are right-aligned, so a column is its cells' right edge."""
        if box is None:
            return ""
        votes: dict[str, int] = {}
        for c in self.cells_on(page):
            if c.kind != "note" or c.box is None or not c.heading:
                continue
            if abs(c.box[2] - box.x1) <= 0.012:
                votes[c.heading] = votes.get(c.heading, 0) + 1
        if not votes:
            return ""
        best = max(votes.values())
        return next(h for h, n in votes.items() if n == best)


@dataclass
class _Edge:
    """A figure's right edge, which is all `RulesReading.column_heading_at` reads."""
    x1: float


# ── one figure's place ───────────────────────────────────────────────────────────────────────────

@dataclass
class Place:
    """Where one printed figure the model gave is, in the rules' reading of its page."""
    kind: str                 # face | note | page | sentence | unplaced
    page: int | None          # 0-based
    amount: Decimal           # signed, as the model gave it
    caption: str              # the printed caption (the model's where nothing is read)
    cell: Cell | None = None
    heading: str = ""
    heading_from: str = ""    # "cell" | "column" | ""
    found: list = field(default_factory=list)   # every cell on the page printing this amount
    currency: str | None = None
    scale_word: Decimal | None = None

    @property
    def certain(self) -> bool:
        """Which period (and basis) the figure's column is, beyond doubt: every cell on the page
        that prints this amount is filed under the same one — a row and the total that repeats
        it, say — and the column says so: a face column, or a note heading that names a period."""
        if self.cell is None or self.cell.period is None:
            return False
        if len({(c.period, c.basis) for c in self.found}) != 1:
            return False
        return self.cell.kind == "face" or bool(_PERIOD_HEADING.search(self.heading))


def _page_caption(page_text, box) -> str:
    """The caption printed left of a figure on its row, off the page's own words."""
    if page_text is None or box is None:
        return ""
    hit = next((w for w in page_text.words if w.box.x0 == box.x0 and w.box.y0 == box.y0), None)
    if hit is None:
        return ""
    return " ".join(w.text for w in W._row_caption(hit, page_text.words))


def _sentence_marks(amount: Decimal, text: str) -> tuple[str | None, Decimal | None]:
    """`(currency, scale word)` printed straight beside the amount in the page's text."""
    if not text or amount is None or amount == 0:
        return None, None
    currency = note_sourced.prose_currency_at(abs(amount), text)
    scale, _prior = note_sourced.prose_context(abs(amount), text)
    return currency, scale


# ── the judgement ───────────────────────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    """What the checks made of one reading: the figure to write and why it moved."""
    value: Decimal                       # in the statements' scale, refused rows left out
    rows: list[tuple[str, Decimal]]      # the kept rows of a sum, in the statements' scale
    row_boxes: list = field(default_factory=list)
    printed_rows: list[tuple[str, Decimal]] = field(default_factory=list)   # the kept, as printed
    dropped: list[dict] = field(default_factory=list)   # refused rows: caption, amount, why, rule
    flags: list[str] = field(default_factory=list)
    review: bool = False
    kept: list[dict] = field(default_factory=list)      # the kept citation entries
    # Per kept row (`rows`, or the one figure): `(from, to)` scales where it was converted, else None.
    scales: list = field(default_factory=list)
    note_refs: list = field(default_factory=list)       # off the rules' face row(s) it was read on
    note_number: str = ""                               # …and their note number, where one
    unchecked: bool = False                             # no check ran; only flags were added


def _detected_unit(ctx, doc):
    """The unit the statements are presented in, read as `normalize` reads it — that stage runs
    after this one, so the document has none yet."""
    try:
        from app.stages.normalize import _detect_units

        fmt = getattr(getattr(doc, "fmt", None), "value", "pdf") or "pdf"
        return _detect_units(ctx, fmt, doc)
    except Exception:  # noqa: BLE001 - no unit read is "no currency to compare", as before
        return None


def _check_view(ctx, doc):
    """The document as the checks read it: its notes and pages, and the statements' unit."""
    if not hasattr(doc, "model_copy"):
        return doc
    from types import SimpleNamespace

    from app.services import whole_pdf_rules
    from app.stages.note_sourced import _prose_basis

    detected = _detected_unit(ctx, doc)
    base = doc.model_copy(update={"unit_context": detected}) if detected is not None else doc
    try:
        face = whole_pdf_rules.rules_face_unit(
            SimpleNamespace(whole_pdf_rules_rows=list(doc.line_items or ())), base,
            _prose_basis(doc) or "consolidated")
    except Exception:  # noqa: BLE001 - an unreadable face unit is the detected one
        face = None
    unit = face or detected
    if unit is None:
        return doc
    return doc.model_copy(update={"unit_context": unit, "line_items": []})


def column_selector(item) -> tuple[list, list] | None:
    """`(admit, refuse)` compiled heading patterns of a line that reads ONE COLUMN of a table, or
    None for a line that reads whole rows."""
    src = getattr(item, "note_source", None)
    admit = list(getattr(src, "column_heading_any", None) or ())
    if not admit:
        return None

    def compiled(patterns):
        out = []
        for raw in patterns or ():
            try:
                out.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                continue
        return out
    return compiled(admit), compiled(getattr(src, "column_heading_none", None))


def _not_an_asset_vetoes(item) -> list:
    """A column-selected part's own exclusions that say the row is not a financial asset at all —
    an investment property, a biological asset, a liability — compiled: its `exclude_hints` and the
    `row_caption_none` patterns that name those. The rules reject such rows by caption."""
    src = getattr(item, "note_source", None)
    raw = list(getattr(item, "exclude_hints", None) or ())
    raw += [p for p in (getattr(src, "row_caption_none", None) or ())
            if re.search(r"propert|biological|liabilit|物業|房地产|生物|负债|負債", p)]
    out = []
    for p in raw:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return out


#: A table title that names ASSETS AND LIABILITIES together: the CAS standard fair-value table
#: 以公允价值计量的资产和负债的期末公允价值, an HKFRS "Assets and liabilities measured at fair value".
_ASSETS_AND_LIABILITIES = re.compile(
    r"(?:金融)?(?:资产|資產)\s*(?:和|及|与|與|、|以及)\s*(?:金融)?(?:负债|負債)"
    r"|assets?\s*(?:and|&|/)\s*(?:financial\s+)?liabilit\w*", re.IGNORECASE)


def not_a_financial_asset(vetoes: list, caption: str, table=None) -> bool:
    """Whether a cited row is one a column-selected part refuses as NOT A FINANCIAL ASSET
    (`_not_an_asset_vetoes`): by the row's caption, or by its note table's title or chapter title.

    A TITLE NAMING ASSETS AND LIABILITIES TOGETHER SAYS NOTHING ABOUT ONE ROW. The CAS standard
    fair-value table is titled 以公允价值计量的资产和负债的期末公允价值, and its 负债 matched the
    parts' own liability pattern, so every nonzero Level 3 figure read from it was refused —
    002273 十三、1's 持续以公允价值计量的资产总额 187,541,328.29 among them, the cell the rules
    route reads and publishes. Such a title is read as a title of assets; its liability rows are
    refused by their own captions (持续以公允价值计量的负债总额, 交易性金融负债), as the rules
    refuse them. A title of liabilities alone, or of investment properties (1966's note 15), still
    refuses every row under it.
    """
    if any(v.search(caption) for v in vetoes if caption):
        return True
    if table is None:
        return False
    for attr in ("title", "chapter_title"):
        text = str(getattr(table, attr, "") or "")
        if not text:
            continue
        text = _ASSETS_AND_LIABILITIES.sub(" assets ", text)
        if any(v.search(text) for v in vetoes):
            return True
    return False


def _note_part_exclusions(item) -> list:
    """An INTERNAL part read from the notes: its own `exclude_hints`, compiled — the captions
    its author said are not this part. `answer_checks.refuse_excluded_face` holds a FACE row to
    them; a part that reads the notes names the note rows that are not it the same way (the
    current FVOCI part: 应收款项融资, which 000709's fair-value table lists among its Level 3
    assets and the balance sheet prints as a receivables row of its own)."""
    if str(getattr(item, "namespace", "") or "") != "internal":
        return []
    if str(getattr(item, "route", "") or "") != "note_tables":
        return []
    out = []
    for raw in getattr(item, "exclude_hints", None) or ():
        try:
            out.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    return out


def _excluded(caption: str, vetoes: list) -> bool:
    from app.services.mapping import normalize_label

    spellings = {str(caption or "").lower(), normalize_label(str(caption or ""))} - {""}
    return any(v.search(t) for v in vetoes for t in spellings)


class Judge:
    """Locates and checks every reading of one run, then settles the parts across answers."""

    def __init__(self, doc, ctx, item_set, texts, log=None):
        self.doc = doc
        self.texts = texts or {}
        self.items = {i.key: i for i in getattr(item_set, "items", None) or ()}
        self.item_list = list(getattr(item_set, "items", None) or ())
        self.log = log or (lambda _line: None)
        self.reading = RulesReading(doc)
        self.index = answer_checks.NoteIndex(getattr(doc, "notes", None))
        # THE CHECKS READ THE STATEMENTS' CURRENCY AND SCALE OFF THE DOCUMENT, which `normalize`
        # sets only later — so they are given a view of it whose unit is the statements': the
        # unit the face rows the rules read DECLARE (`whole_pdf_rules.rules_face_unit`, the unit
        # the rules put a note row into on this route), with the currency read as `normalize`
        # reads it. Its rows are left out so that unit is the one every basis is compared with.
        self.check_doc = _check_view(ctx, doc)
        self.kinds = {p.index: p.kind for p in getattr(doc, "pages", None) or ()}
        self.statements = {p.index: W._statement_value(p)
                           for p in getattr(doc, "pages", None) or ()}
        self.verdicts: dict[int, Verdict] = {}
        # (key, basis) -> flags for a row whose slot(s) were refused or withheld entirely.
        self.row_flags: dict[tuple[str, str], list[str]] = {}
        self.withheld: dict[str, list[str]] = {}
        self.counts: dict[str, int] = {}

    # ── one reading ──

    def _count(self, what: str) -> None:
        self.counts[what] = self.counts.get(what, 0) + 1

    def _place(self, page, amount: Decimal, caption: str, box, period: str, repeats: dict,
               said: tuple) -> Place:
        text = self.texts.get(page) if page is not None else None
        cell, found = self.reading.find(page, amount, caption, box, period, repeats, said)
        if cell is not None:
            row_caption = cell.caption
            # A ROW THE READER MADE OUT OF A SENTENCE is a sentence: its "caption" runs on past
            # a full stop, or the page prints a currency or a scale word against the amount.
            if cell.kind == "note":
                currency, scale = _sentence_marks(amount, getattr(text, "text", ""))
                if "。" in row_caption or (len(row_caption) > 24 and (currency or scale)):
                    return Place("sentence", page, amount, caption or row_caption, cell=None,
                                 currency=currency, scale_word=scale)
            # A CELL THE READER KEPT NO HEADING FOR — a total row below a page's fragment of the
            # table, as 688008's 持续以公允价值计量的资产总额 on p239 — is in the column of the cells
            # printed above it.
            heading, heading_from = cell.heading, "cell" if cell.heading else ""
            if not heading and cell.kind == "note" and cell.box is not None:
                heading = self.reading.column_heading_at(page, _Edge(cell.box[2]))
                heading_from = "column" if heading else ""
            return Place(cell.kind, page, amount, row_caption or caption, cell=cell,
                         heading=heading, heading_from=heading_from, found=found)
        if amount == 0:
            return Place("unplaced", page, amount, caption)
        currency, scale = _sentence_marks(amount, getattr(text, "text", ""))
        if currency or scale:
            return Place("sentence", page, amount, caption, currency=currency, scale_word=scale)
        heading = self.reading.column_heading_at(page, box)
        printed = _page_caption(text, box) if box is not None else ""
        return Place("unplaced", page, amount, printed or caption, heading=heading,
                     heading_from="column" if heading else "")

    def _entry(self, at: int, place: Place, r) -> dict:
        page = place.page
        e: dict = {"at": at, "caption": place.caption, "basis": r.basis,
                   "figures": {r.period: str(place.amount)},
                   "figures_by_basis": {r.basis: {r.period: str(place.amount)}},
                   "page": (page + 1) if page is not None else None}
        cell = place.cell
        if place.kind == "face":
            prov = cell.provenance()
            cell_at = note_sourced.cell_of(prov)
            # THE PRINTED CELL, so two columns of one face row — a statement of changes in
            # equity's closing row, one figure per reserve — are two printed figures to
            # `answer_checks.refuse_repeated_figures`, not one row cited twice.
            e.update(on_face=True, statement=self.statements.get(page, ""),
                     row_id=str(getattr(cell.row, "id", "") or ""),
                     unit=note_sourced._unit_of(cell.row), provenance=prov,
                     cells=[cell_at] if cell_at is not None else [])
        elif place.kind == "note":
            prov = cell.provenance()
            cell_at = note_sourced.cell_of(prov)
            e.update(note=str(getattr(cell.table, "note_number", "") or ""),
                     title=str(getattr(cell.table, "title", "") or ""),
                     unit=note_sourced._unit_of(cell.row), provenance=prov,
                     cells=[cell_at] if cell_at is not None else [],
                     column={"heading": place.heading, "key": cell.key,
                             "cells": [{"key": cell.key}]})
        elif place.kind == "page":
            e.update(off_statement=True, row_id=str(getattr(cell.row, "id", "") or ""),
                     unit=note_sourced._unit_of(cell.row), provenance=cell.provenance())
        elif place.kind == "sentence":
            e.update(prose=True, prose_currency=place.currency,
                     prose_scale=str(place.scale_word) if place.scale_word else None,
                     note="", title="")
        else:
            if self.kinds.get(page) == PageKind.FACE:
                e["on_face"] = True
            elif self.kinds.get(page) != PageKind.NOTES:
                e["off_statement"] = True
        return e

    def _column_checks(self, item, r, places: list[Place], entries: list[dict]
                       ) -> tuple[list[dict], list[dict], list[str], bool, str | None, str | None]:
        """The checks only a figure with a column can be held to. `(kept, refused, flags, review,
        re-file period, re-file basis)`."""
        kept, refused, flags = [], [], []
        review = False
        selector = column_selector(item)
        vetoes = _not_an_asset_vetoes(item) if selector else []
        excluded = _note_part_exclusions(item)
        for place, entry in zip(places, entries):
            if place.amount == 0:
                kept.append(entry)
                continue
            if excluded and place.kind == "note" and _excluded(place.caption, excluded):
                refused.append(answer_checks._refusal(
                    entry, "this part's own caption exclusions name the cited note row — it is "
                           "another line's", "excluded_caption"))
                continue
            if selector is not None:
                admit, deny = selector
                table = place.cell.table if place.cell is not None else None
                if vetoes and not_a_financial_asset(vetoes, place.caption, table):
                    refused.append(answer_checks._refusal(
                        entry, "the cited row is not a financial asset this line reads (an "
                               "investment property, a biological asset or a liability), by its "
                               "caption or by the title of a table that holds only such rows",
                        "not_a_financial_asset"))
                    continue
                if place.kind == "sentence":
                    refused.append(answer_checks._refusal(
                        entry, "this line reads one column of a table, and the figure is stated "
                               "in a sentence — it is in no column the line reads", "column"))
                    continue
                if place.heading:
                    ok = (any(a.search(place.heading) for a in admit)
                          and not any(d.search(place.heading) for d in deny))
                    if not ok:
                        refused.append(answer_checks._refusal(
                            entry, f"the figure is printed in the column {place.heading!r}, which "
                                   f"this line does not read", "column"))
                        continue
                else:
                    review = True
                    flags.append(f"{FLAG}:column_not_confirmed:{place.amount}")
            kept.append(entry)
        refile_period = refile_basis = None
        if len(places) == 1 and kept and places[0].cell is not None and places[0].amount != 0:
            place = places[0]
            cell = place.cell
            if cell.period is not None and cell.period != r.period:
                if place.certain:
                    refile_period = cell.period
                else:
                    review = True
                    flags.append(f"{FLAG}:column_period:{cell.period}:{place.heading or '?'}")
            if cell.basis and cell.basis != r.basis:
                if place.certain and cell.kind == "face":
                    refile_basis = cell.basis
                else:
                    review = True
                    flags.append(f"{FLAG}:column_basis:{cell.basis}")
        elif len(places) > 1:
            periods = {p.cell.period for p in places
                       if p.cell is not None and p.cell.period and p.amount != 0}
            if periods and r.period not in periods:
                review = True
                flags.append(f"{FLAG}:column_period:{','.join(sorted(periods))}:rows")
        return kept, refused, flags, review, refile_period, refile_basis

    def _in_words(self, entry: dict, doc_scale: Decimal) -> tuple[Decimal, str | None]:
        """A sentence's amount in the statements' units: by the scale word printed beside it, and
        an amount stated in full on a filing presented in thousands divided down — the rule the
        line-item route applies (`stages.line_item_llm._prose_in_statement_units`). An amount
        smaller than one unit of the statements is already stated in them."""
        amount = Decimal(str(next(iter(entry["figures"].values()))))
        word = entry.get("prose_scale")
        factor = Decimal(str(word)) if word else None
        scale = doc_scale if doc_scale and doc_scale > 0 else Decimal(1)
        if factor is not None:
            moved = amount * factor / scale
        elif scale != 1 and abs(amount) >= scale:
            moved = amount / scale
        else:
            return amount, None
        if moved == amount:
            return amount, None
        return moved, f"{amount}->{moved}"

    def judge_one(self, r):
        """Locate and check one reading. None when every figure it rests on is refused."""
        item = self.items.get(r.key)
        if item is None or not r.check.confirmed:
            return r
        page = r.check.page
        rows = list(r.rows) or [(r.caption, r.amount)]
        boxes = list(r.row_boxes) if r.rows else [(r.box, r.label_box)]
        while len(boxes) < len(rows):
            boxes.append((None, None))
        repeats: dict = {}
        places = [self._place(page, a, c, boxes[i][0], r.period, repeats,
                              (r.key, r.basis, r.period, c, str(a)))
                  for i, (c, a) in enumerate(rows)]
        entries = [self._entry(i, p, r) for i, p in enumerate(places)]
        for p in places:
            self._count(p.kind)

        kept, refused, flags, review, to_period, to_basis = self._column_checks(
            item, r, places, entries)
        # EACH ROW'S SIGN IS THE SIGN OF ITS AMOUNT. The model gives a row it takes out of a sum as
        # a negative figure, and the figure written adds the signed rows; the checks are told the
        # same, so a 其中 row or a derivative SUBTRACTED from the row it sits in is the line taking
        # it out (`answer_checks.refuse_breakdowns_of_cited_totals`, `refuse_derivatives`), not
        # a second count of it. Telling them every row was added refused 300319's forward taken
        # out of the prior-year FVTPL row, and published 181,124,711.32 for 180,559,509.03.
        signs = [-1 if p.amount < 0 else 1 for p in places]
        checked = answer_checks.check_answer(
            item, kept, doc=self.check_doc, signs=signs, component=bool(r.rows),
            key=r.key, index=self.index, prefix="whole_pdf")
        for line in checked.log:
            self.log(line)
        for bad in refused:
            self.log(f"whole_pdf:{r.key}: citation REFUSED caption={str(bad.get('caption'))[:60]!r} "
                     f"({bad.get('why')})")
        refused = refused + list(checked.refused)
        kept = list(checked.kept)

        # THE FIGURE TO WRITE: the kept rows, each in the statements' scale.
        doc_scale = self._statement_scale(r.basis)
        written: list[tuple[str, Decimal]] = []
        printed_kept: list[tuple[str, Decimal]] = []
        kept_boxes = []
        scales = []
        for e in kept:
            at = int(e.get("at") or 0)
            caption, as_printed = rows[at]
            value = Decimal(str(next(iter((e.get("figures") or {r.period: as_printed}).values()))))
            scale = None
            if e.get("rescaled"):
                scale = (str(e["rescaled"][0]), str(e["rescaled"][1]))
                flags.append(f"{FLAG}:rescaled:{scale[0]}->{scale[1]}:{as_printed}->{value}")
                self._count("rescaled")
            elif e.get("prose"):
                value, moved = self._in_words(e, doc_scale)
                if moved:
                    scale = (str(e.get("prose_scale") or 1), str(doc_scale))
                    flags.append(f"{FLAG}:sentence_in_statement_units:{moved}")
                    self._count("rescaled")
            scales.append(scale)
            written.append((caption, value))
            printed_kept.append((caption, as_printed))
            kept_boxes.append(boxes[at])
        dropped = [{"caption": rows[int(b.get("at") or 0)][0],
                    "amount": str(rows[int(b.get("at") or 0)][1]),
                    "why": b.get("why"), "refused": b.get("refused")} for b in refused]
        for b in dropped:
            flags.append(f"{REFUSED}:{b['refused']}:{r.basis}:{r.period}:{b['amount']}")
            self._count(f"refused:{b['refused']}")
        if not kept:
            self.log(f"whole_pdf:{r.key}[{r.basis}:{r.period}]: {r.amount} NOT written — every "
                     f"figure it rests on was refused")
            self.row_flags.setdefault((r.key, r.basis), []).extend(
                f for f in flags if f.startswith(REFUSED))
            return None
        if to_period is not None:
            flags.append(f"{FLAG}:refiled:{r.period}->{to_period}")
            self.log(f"whole_pdf:{r.key}[{r.basis}]: {r.amount} given as {r.period} is printed in "
                     f"the {to_period} column ({places[0].heading or 'the row'}) — filed there")
            r.period = to_period
            self._count("refiled")
        if to_basis is not None:
            flags.append(f"{FLAG}:refiled_basis:{r.basis}->{to_basis}")
            self.log(f"whole_pdf:{r.key}[{r.period}]: {r.amount} given as {r.basis} is printed on "
                     f"the {to_basis} statement — filed there")
            r.basis = to_basis
            self._count("refiled")
        value = sum((v for _c, v in written), Decimal(0))
        if review:
            r.review = True
            self._count("review")
        refs = []
        numbers = set()
        for p in places:
            if p.kind == "face" and p.cell is not None:
                refs.extend(getattr(p.cell.row, "note_refs", None) or ())
                if getattr(p.cell.row, "note_number", None):
                    numbers.add(str(p.cell.row.note_number))
        self.verdicts[id(r)] = Verdict(
            value=value, rows=written if r.rows else [], row_boxes=kept_boxes if r.rows else [],
            printed_rows=printed_kept if r.rows else [], dropped=dropped, flags=flags,
            review=review, kept=kept, scales=scales, note_refs=refs,
            note_number=next(iter(numbers)) if len(numbers) == 1 else "")
        return r

    def _statement_scale(self, basis: str) -> Decimal:
        from app.stages.note_sourced import _statement_unit

        unit = _statement_unit(self.check_doc, basis)
        try:
            return Decimal(str(getattr(unit, "scale_factor", 1) or 1))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal(1)

    def judge(self, readings: list) -> list:
        """Every reading, located and checked; the wholly refused left out."""
        out = []
        for r in readings:
            got = self.judge_one(r)
            if got is not None:
                out.append(got)
        if self.counts:
            self.log("whole_pdf:checked " + " ".join(f"{k}={v}"
                                                     for k, v in sorted(self.counts.items())))
        return out

    def flags_of(self, r) -> list[str]:
        """The flags the checks put on a reading's slot (made empty for a reading they did not
        judge, so a flag added across answers has somewhere to go)."""
        v = self.verdicts.get(id(r))
        if v is None:
            v = self.verdicts[id(r)] = Verdict(value=r.amount, rows=list(r.rows),
                                               row_boxes=list(r.row_boxes),
                                               printed_rows=list(r.rows), unchecked=True)
        return v.flags

    # ── across answers ──

    def _line_of(self, key: str) -> str:
        """The template line a key's figure is published in: itself, or the nearest ancestor of
        an internal part that is."""
        seen = set()
        while key and key not in seen:
            seen.add(key)
            item = self.items.get(key)
            if item is None:
                return key
            if getattr(item, "in_output", True) and not W.is_part(item):
                return key
            key = str(getattr(item, "parent", "") or "")
        return key

    def _names(self, key: str, caption: str) -> bool:
        """Whether the line's own aliases name a printed caption, as the rules' matcher compares
        them (`services.mapping.normalize_label`)."""
        from app.services.mapping import normalize_label

        item = self.items.get(key)
        if item is None or not caption:
            return False
        want = normalize_label(caption)
        aliases = list(getattr(item, "aliases", None) or ())
        aliases += [a for v in (getattr(item, "aliases_i18n", None) or {}).values() for a in v]
        return bool(want) and any(normalize_label(a) == want for a in aliases if a)

    def _settle_face_rows(self, merged: dict) -> None:
        """ONE PRINTED BALANCE-SHEET ROW, ONE LINE OF ITS SECTION (finding
        face_row_without_statement_line). The face 交易性金融资产 row is read for Securities (CP)
        through its face part, which the question names as a part of a line it never asks; a model
        may file the same row on Other Current Assets or Short Term Securities too, alone or inside
        a sum, and total current assets then counts it twice. `shared_figures` catches a figure
        given whole to two lines; a row added into a sum it does not.

        Where two lines published in the same section of the balance sheet count one face row the
        rules read, the row stays with the line whose own aliases name its caption — the line the
        rules' matcher binds it to — and the other line's figure goes without it (the whole figure,
        or that row of a sum). Where neither or both name it, both go to review."""
        uses: dict[str, list] = {}
        for slot, r in merged.items():
            v = self.verdicts.get(id(r))
            for e in (v.kept if v and not v.unchecked else ()):
                if e.get("on_face") and e.get("row_id") and self.statements.get(
                        (e.get("page") or 0) - 1) == "balance_sheet":
                    uses.setdefault(e["row_id"], []).append((slot, r, v, e))
        for row_id, used in uses.items():
            lines = {self._line_of(slot[0]) for slot, *_ in used}
            if len(lines) < 2:
                continue
            sections = {ln.split("__", 1)[0] for ln in lines}
            if len(sections) != 1 or not next(iter(sections)).startswith("bs_"):
                continue
            caption = str(used[0][3].get("caption") or "")
            keys = sorted({slot[0] for slot, *_ in used})
            owners = [k for k in keys if self._names(k, caption)]
            if len(owners) != 1:
                for slot, r, v, _e in used:
                    r.review = True
                    flag = f"{FLAG}:face_row_on_two_lines:" + ",".join(k for k in keys
                                                                       if k != slot[0])
                    if flag not in v.flags:
                        v.flags.append(flag)
                self.log(f"whole_pdf: face row {caption!r} is counted by {', '.join(keys)} — "
                         f"lines of one section; both go to review")
                continue
            owner = owners[0]
            for slot, r, v, e in used:
                if slot[0] == owner or self._line_of(slot[0]) == self._line_of(owner):
                    continue
                # `v.rows` and its companions follow `v.kept`, one per kept entry.
                at = next(i for i, k in enumerate(v.kept) if k is e)
                v.kept = [k for k in v.kept if k is not e]
                why = (f"the face row {caption!r} is {owner}'s, whose aliases name it — counted "
                       f"there, it is not counted on this line as well")
                amount = (v.printed_rows[at][1] if r.rows and at < len(v.printed_rows)
                          else r.amount)
                v.dropped.append({"caption": caption, "amount": str(amount), "why": why,
                                  "refused": "face_row_of_another_line"})
                v.flags.append(f"{REFUSED}:face_row_of_another_line:{slot[1]}:{slot[2]}:{amount}")
                self._count("refused:face_row_of_another_line")
                self.log(f"whole_pdf:{slot[0]}[{slot[1]}:{slot[2]}]: {why}")
                if not v.kept:
                    self.row_flags.setdefault((slot[0], slot[1]), []).append(
                        f"{REFUSED}:face_row_of_another_line:{slot[1]}:{slot[2]}:{amount}")
                    del merged[slot]
                    continue
                if r.rows:
                    keep = [i for i in range(len(v.rows)) if i != at]
                    v.rows = [v.rows[i] for i in keep]
                    v.printed_rows = [v.printed_rows[i] for i in keep if i < len(v.printed_rows)]
                    v.row_boxes = [v.row_boxes[i] for i in keep if i < len(v.row_boxes)]
                    v.scales = [v.scales[i] for i in keep if i < len(v.scales)]
                    v.value = sum((a for _c, a in v.rows), Decimal(0))

    def settle(self, merged: dict) -> None:
        """Two parts of one parent holding the same note money (`answer_checks.settle_siblings`),
        applied to the merged readings: a withheld part is taken out (its row says which part holds
        its figure), a flagged pair goes to review."""
        from app.services.line_item_notes import claimed_notes

        self._settle_face_rows(merged)
        footprints: dict[str, list] = {}
        for (key, _basis, _period), r in merged.items():
            v = self.verdicts.get(id(r))
            for e in (v.kept if v else ()):
                if e.get("on_face") or e.get("prose") or e.get("off_statement"):
                    continue
                fp = answer_checks.footprint(e, self.index)
                if fp:
                    footprints.setdefault(key, []).append(fp)
        if sum(1 for v in footprints.values() if v) < 2:
            return
        items = self.item_list
        for s in answer_checks.settle_siblings(
                footprints, items, self.index,
                claims=lambda: claimed_notes([i for i in items if getattr(i, "note_source", None)],
                                             self.doc.notes)):
            if s.action == "keep":
                self.log(f"whole_pdf:{s.other}: shares note {s.note} row {s.caption!r} with "
                         f"{s.owner}, but its figure also rests on other rows — left as is")
                continue
            if s.action == "flag":
                for key, other in ((s.owner, s.other), (s.other, s.owner)):
                    for slot, r in merged.items():
                        if slot[0] == key:
                            r.review = True
                            flags = self.flags_of(r)
                            flag = f"llm_note_rows_overlap:{other}"
                            if flag not in flags:
                                flags.append(flag)
                self.log(f"whole_pdf:{s.other}: may hold the same money as {s.owner} (note "
                         f"{s.note} row {s.caption!r}, {s.kind.replace('_', ' ')}) — both go to "
                         f"review")
                continue
            gone = [slot for slot in merged if slot[0] == s.other]
            figures = sorted({str(merged[slot].amount) for slot in gone})
            flags = [f"llm_note_row_claimed_by:{s.owner}"]
            if s.kind == "breakdown":
                flags.append(f"llm_note_row_breakdown_of:{s.owner}")
            if figures:
                flags.append(f"llm_note_row_withheld:{','.join(figures[:4])}")
            for slot in gone:
                self.row_flags.setdefault((slot[0], slot[1]), []).extend(
                    f for f in flags if f not in self.row_flags.get((slot[0], slot[1]), []))
                del merged[slot]
            self.withheld[s.other] = flags
            self.log(f"whole_pdf:{s.other}: note {s.note} row {s.caption!r} "
                     + ("is a 其中 breakdown of a row " + s.owner + " holds, and " + s.parent
                        + " adds the two" if s.kind == "breakdown"
                        else "goes to " + s.owner + ", whose note it is")
                     + "; this part is left empty"
                     + (f" (was {figures[0]})" if figures else ""))
