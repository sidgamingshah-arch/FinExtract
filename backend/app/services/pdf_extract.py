"""PDF → line items. Native pages use the PyMuPDF text layer; scanned pages route through
the configured OCR provider. Both converge on the shared word→line-item reconstruction, so
every value carries page + normalized-bbox provenance regardless of source.
"""
from __future__ import annotations

import re

from app.core.models.enums import PageKind, PageSourceKind, PrintedIn
from app.core.models.geometry import BBox
from app.core.stage import PipelineContext
from app.services.mapping import known_captions
from app.services.buckets import PRINTED_ON_FLAG
from app.services.row_reconstruct import Word, build_line_items


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def text_rotation(page) -> int:
    """The dominant reading direction of the page's text, in degrees clockwise: 0, 90, 180 or 270.

    ``page.rect`` already accounts for a page's /Rotate attribute, so a page marked landscape
    needs nothing. What it does not cover is text DRAWN sideways on an upright page, which wide
    statements use — a statement of changes in equity with fourteen component columns is
    routinely printed rotated to fit. There the words are laid out bottom-to-top, so grouping
    them into rows by shared y finds no rows at all and the page yields nothing.

    The writing direction comes from the span's own ``dir`` unit vector, weighted by how much
    text is drawn that way, so a single rotated stamp or watermark cannot outvote the body.

    The angle is expressed the way a PDF ``/Rotate`` is — the CLOCKWISE rotation that would bring
    the text upright — because it is stored on the same field (``PageSource.rotation``, which
    ``stages.ingest`` fills from ``/Rotate``) and the integrity report renders both with the same
    sentence. Text whose words advance UP the page (``dir=(0,-1)``) therefore reads as 90: turning
    the page a quarter-turn clockwise is what makes it readable.
    """
    weights: dict[int, float] = {}
    for line in _text_lines(page):
        chars = sum(len(sp.get("text", "")) for sp in line.get("spans", []))
        if not chars:
            continue
        deg = _dir_rotation(line.get("dir"))
        weights[deg] = weights.get(deg, 0.0) + chars
    if not weights:
        return 0
    return max(weights, key=lambda d: weights[d])


def _text_lines(page) -> list[dict]:
    """Every text line of the page, with its own ``dir`` and ``bbox``."""
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:                        # a malformed page must not stop extraction
        return []
    return [line for block in blocks for line in block.get("lines", [])]


_CHROME_BAND = 0.12          # the top eighth of the page, where a running header is printed
# …AND THE FOOT OF THE PAGE, where the other half of the template is printed. A statement page
# carries a footer as reliably as a header — the folio, the entity's own name, and the boilerplate
# every IFRS filing prints under its statements ("The notes on pages 68 to 159 form part of these
# consolidated financial statements") — and none of it was being read as chrome.
#
# MEASURED ON 佳明集團 2025/26, whose footer block sits at y 0.915-0.940 of every face page. The
# sentence wraps, so reconstruction made TWO rows of it, and the page range landed in their value
# columns: 'The notes on pages' carrying 68 and 159, and 'statements. 分。' carrying the folio. All
# of it was swept into the residual bucket of whatever section the page belonged to, and published
# — `is_pl__other_operating_expenses` 68/159, `is_oci__other_equity_and_reserves_adj` 159 and 62,
# `bs_equity__other_reserves` 68/159 and 64, `cf_financing__other_financing_cash_flows` 68/159.
#
# 0.88, which is where the footer BLOCK starts and not where its English sentence does. The
# boilerplate is printed twice, once in each language, and the Chinese half sits a little higher:
# 0.892 against 0.902 on this filing. A band that reached only the English half left the Chinese
# one to be read on its own, which is worse than reading neither — the two were arriving in the
# same row, so dropping one of them would have promoted the other to a row of its own.
#
# THE DEEPEST GENUINE ROW MEASURED on the two HK filings is 0.898 (a figure of 15,280,000 in a
# note on page 28), so the band does overlap real content. That is what ``_CHROME_AMOUNT`` below
# is for, and why the repeat threshold is on PAGES: a line stating a grouped amount is never
# chrome however deep it is printed, and a line that is not printed on three pages is not a
# template. The band's job is only to say where to look.
_CHROME_FOOT_BAND = 0.88
_CHROME_MIN_PAGES = 3        # fewer repeats than this is a coincidence, not a template
# HOW FAR UP OR DOWN A FOOT LINE MAY WANDER between the pages it repeats on and still be the page
# template. A template is printed at a FIXED place; content lands wherever the content above it
# ended. That is the whole difference, and it is a wide one — measured over the five filings:
#
#   the boilerplate under 佳明's statements   spread 0.0000 over 5 pages
#   合计 (Total) at the foot of a PRC note    spread 0.0021 - 0.0120
#   期末余额 / 期初余额 / 单位：元             spread 0.0083 - 0.0153
#
# Without this test the foot band made CHROME KEYS out of 合计, 期末余额, 期初余额 and 单位：元 —
# the last row and the column headers of any PRC note long enough to run onto a second page — and
# a chrome key is grounds for dropping a row wherever it is printed. 0.001 of page height is under
# a point on A4: the same place, not merely a similar one.
_CHROME_FOOT_ALIGN = 0.001
# A line that states a GROUPED amount is a row, not chrome, whatever band it is printed in and
# however often its caption repeats. Cheap, and it keeps the two protections independent: the
# threshold decides what repeats, this decides what could have been a figure.
_CHROME_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def _chrome_key(text: str) -> str:
    """A top-of-page caption reduced to what repeats: case-folded, collapsed whitespace, and with
    every run of digits dropped.

    The digits are what makes a running header LOOK different on every page — "Acme Holdings
    Limited / Annual Report 2024   64" and the same line on page 65 are the same header. Dropping
    them is also why the key is never compared against a financial caption: those are matched
    whole, by a reader that has the row's figures, not by this.
    """
    import re as _re

    return " ".join(_re.sub(r"\d+", "", str(text or "")).split()).casefold()


def _page_chrome(pdf, targets) -> tuple[frozenset[str], dict[int, tuple[tuple[float, ...], ...]]]:
    """The captions this filing prints at the top or the FOOT of page after page — its template,
    as ``(keys, foot_boxes)``: the keys every band contributes, and — for the FOOT band only —
    where on each page those lines are printed, in the page-relative box the word reader uses.

    WHY THE FOOT GETS BOXES AND THE HEAD DOES NOT. A running header is one line and lands on one
    row, so naming it is enough: ``row_reconstruct._is_noise_row`` matches the key and drops that
    row. The closing boilerplate under a statement is a SENTENCE, and a sentence wraps — so
    reconstruction cut it into pieces, and no piece's label is the key any more. The boxes let
    the reader refuse the words instead of the row, which is the only test a wrap cannot defeat,
    and it covers the notes pages too (``notes_extract`` is never handed the key set, and its
    prose reader would otherwise read the boilerplate as a note's narrative).

    WHY NOT A WORD LIST. Both the classifier and the row reader carry a regex of header wording
    ("annual report", "年報", …), and a filing whose running header is just its own name — which
    is the common HK house style — matches neither. The header then reached the reader as an
    ordinary label and, on a page where a figure happened to sit on its baseline, was published as
    a line item: an entity name carrying money, on the face of a statement.

    The signal used instead is structural and needs no vocabulary: a caption printed in the top
    band of at least ``_CHROME_MIN_PAGES`` of the pages being read is the page template, not a
    financial line. A statement caption cannot qualify — "Trade receivables" is printed once, on
    one page, in the body — and the threshold is on PAGES rather than occurrences so a word
    repeated many times down one page is untouched.

    Read from the text layer only. A scanned page contributes nothing here (it has no lines to
    read), and it does not need to: the header it repeats is the same one the native pages state.
    """
    heads: dict[str, set[int]] = {}
    foots: dict[str, set[int]] = {}
    tops: dict[str, list[float]] = {}
    # page -> the foot-band lines found on it, as (key, page-relative box).
    feet: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = {}
    for ps in targets:
        if ps.index >= pdf.page_count:
            continue
        try:
            page = pdf[ps.index]
            width = max(page.rect.width, 1.0)
            height = max(page.rect.height, 1.0)
            for line in _text_lines(page):
                box = line.get("bbox") or (0, 0, 0, 0)
                head = box[3] / height <= _CHROME_BAND
                foot = box[1] / height >= _CHROME_FOOT_BAND
                if not (head or foot):
                    continue
                text = "".join(sp.get("text", "") for sp in line.get("spans", []))
                if _CHROME_AMOUNT.search(text):
                    continue
                key = _chrome_key(text)
                if not key:
                    continue
                if head:
                    heads.setdefault(key, set()).add(ps.index)
                    continue
                foots.setdefault(key, set()).add(ps.index)
                tops.setdefault(key, []).append(box[1] / height)
                feet.setdefault(ps.index, []).append(
                    (key, (box[0] / width, box[1] / height,
                           box[2] / width, box[3] / height)))
        except Exception:                    # a malformed page must not stop extraction
            continue
    keys = {k for k, pages in heads.items() if len(pages) >= _CHROME_MIN_PAGES}
    foot_keys = {k for k, pages in foots.items()
                 if len(pages) >= _CHROME_MIN_PAGES
                 and max(tops[k]) - min(tops[k]) <= _CHROME_FOOT_ALIGN}
    boxes = {page: tuple(box for key, box in found if key in foot_keys)
             for page, found in feet.items()}
    return (frozenset(keys | foot_keys),
            {page: found for page, found in boxes.items() if found})


def _without_foot_chrome(words: list, boxes: tuple[tuple[float, ...], ...]) -> list:
    """``words`` with those printed inside one of this page's foot-template lines removed.

    Containment, not a y band: the boilerplate's box is the box of ITS line, so a figure printed
    at the same depth in another column — which the measurements say exists, a note's last row can
    reach 0.898 — is outside it and kept. The word's centre is the test, so a word straddling the
    line's edge goes with whichever line holds most of it.
    """
    if not boxes:
        return words
    kept = []
    for word in words:
        bb = word.bbox
        cx, cy = (bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in boxes):
            continue
        kept.append(word)
    return kept


def _dir_rotation(direction) -> int:
    """One text line's ``dir`` unit vector as a rotation in the sense :func:`text_rotation` returns.

    Factored out so the page's dominant angle and the per-line angle used to spot the page's
    horizontal chrome cannot drift apart: both read the same vector through this one function.
    """
    dx, dy = (direction or (1.0, 0.0))[:2]
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 180
    # y grows DOWNWARD in PyMuPDF page space, so words advancing in -y run up the page.
    return 90 if dy < 0 else 270


def _to_reading_space(box: BBox, rotation: int) -> BBox:
    """A page-space box expressed in reading space, for text drawn at ``rotation`` degrees.

    Reading space is where "down the page" is the direction successive lines advance and "across"
    is the direction words advance — which is what row grouping and column detection assume. The
    transform is the inverse rotation about the unit square, so the result stays normalized.
    """
    if rotation in (0, 360):
        return box
    if rotation == 90:                       # text runs bottom-to-top
        # Across the printed row is -y (words advance up the page), so reading-space x runs with
        # 1-y; successive rows advance in +x, so reading-space y is the page's x. Getting this
        # pair the wrong way round still yields rows — it yields them mirrored and bottom-to-top,
        # which reverses a statement's columns and its movements without failing anything.
        return BBox(x0=_clamp(1.0 - box.y1), y0=_clamp(box.x0),
                    x1=_clamp(1.0 - box.y0), y1=_clamp(box.x1))
    if rotation == 270:                      # text runs top-to-bottom
        return BBox(x0=_clamp(box.y0), y0=_clamp(1.0 - box.x1),
                    x1=_clamp(box.y1), y1=_clamp(1.0 - box.x0))
    return BBox(x0=_clamp(1.0 - box.x1), y0=_clamp(1.0 - box.y1),
                x1=_clamp(1.0 - box.x0), y1=_clamp(1.0 - box.y0))


def _rotation_at(lines: list[tuple[tuple[float, float, float, float], int]],
                 box: tuple[float, float, float, float]) -> int | None:
    """The rotation of the text line this word belongs to, or None if no line claims it.

    Matched by the word's centre against the line boxes, smallest box first, because a word is
    laid out inside exactly one line and nested boxes only ever mean a tighter fit is available.
    """
    best: tuple[int, float] | None = None
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for (lx0, ly0, lx1, ly1), rot in lines:
        if lx0 - 0.5 <= cx <= lx1 + 0.5 and ly0 - 0.5 <= cy <= ly1 + 0.5:
            area = max(lx1 - lx0, 0.0) * max(ly1 - ly0, 0.0)
            if best is None or area < best[1]:
                best = (rot, area)
    return None if best is None else best[0]


def _native_words(page, w: float, h: float, rotation: int | None = None) -> list[Word]:
    """The page's words, in reading space, with the words drawn at some OTHER angle removed.

    A page whose statement is printed sideways still prints its chrome upright: the page number,
    the running header, and on an HKEX filing the statement's own title and period. Those words
    are a quarter-turn from the body, so putting them through the body's transform lands them in
    the middle of the matrix — inventing rows, and gluing a caption to the running header. They
    are identified by their own line's ``dir``, not by position, so a genuinely sideways word near
    the page edge is kept and an upright figure in the body is still dropped.

    Only rotated pages pay for this: an upright page has no other angle to disagree with, so the
    word list is built exactly as before.
    """
    rot = text_rotation(page) if rotation is None else rotation
    upright = rot in (0, 360)
    lines: list[tuple[tuple[float, float, float, float], int]] = []
    if not upright:
        lines = [(tuple(ln.get("bbox") or (0.0, 0.0, 0.0, 0.0)), _dir_rotation(ln.get("dir")))
                 for ln in _text_lines(page)
                 if any(sp.get("text", "").strip() for sp in ln.get("spans", []))]
    out: list[Word] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        if not text.strip():
            continue
        if lines:
            at = _rotation_at(lines, (x0, y0, x1, y1))
            if at is not None and at != rot:
                continue
        page_box = BBox(x0=_clamp(x0 / w), y0=_clamp(y0 / h),
                        x1=_clamp(x1 / w), y1=_clamp(y1 / h))
        if upright:
            out.append(Word(text=text, bbox=page_box))
        else:
            # Layout logic reads the rotated box; provenance keeps the page-space one so
            # click-to-source still highlights where the figure is actually drawn.
            out.append(Word(text=text, bbox=_to_reading_space(page_box, rot),
                            page_bbox=page_box))
    return out


def extract_pdf(data: bytes, doc, ctx: PipelineContext, *, scope=None,
                normalisation=None) -> int:
    """Extract line items from a PDF into ``doc.line_items``. Returns the count added.

    ``scope``/``normalisation`` are the reading rules of the rulebook the RUN was pinned to
    (``stages.extract.reconstruction_rules``). Omitting them falls back to the rulebook shipped as
    the one in force, which is what a run started without an ontology is read with — a run that
    does name a rulebook must be read by that one, or its pin only decided how its figures were
    mapped and not which column they came from.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        ctx.log("extract:pymupdf_missing")
        return 0
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # corruption already surfaced by the integrity stage
        ctx.log(f"extract:pdf_open_failed:{exc}")
        return 0

    # Prefer statement pages (face + notes). Also include SCANNED pages even if they didn't
    # classify — a scanned page has no text layer to match a title against, so it would
    # otherwise be dropped before ever reaching the OCR path. Fall back to all pages if
    # nothing was classified at all.
    from app.services.notes_extract import without_empty_duplicates
    from app.services.statements import ACTIVE_STATEMENTS

    targets = [p for p in doc.pages
               if p.kind is PageKind.NOTES
               or (p.kind is PageKind.FACE and p.statement in ACTIVE_STATEMENTS)
               or (p.source_kind == PageSourceKind.SCANNED
                   and p.statement in ACTIVE_STATEMENTS)]
    if not targets:
        targets = list(doc.pages)

    # A LINE DECLARED `anywhere` MEANS EVERY PAGE, and this is where that has to be honoured.
    #
    # The route's name promised the whole filing and it could not deliver it — a page nothing
    # reconstructs has no rows, so "search everywhere" had nowhere to search. `note_sections.
    # open_to` lifting the section narrowing was the only behaviour the route had, and that only
    # widens which NOTES a line may read. The pages beyond the statements and the notes — a
    # directors' report, a five-year summary, an unclassified schedule — were never read at all,
    # by any route.
    #
    # GATED ON THE CONFIGURATION, NOT ON A SETTING. Reading every page costs a reconstruction pass
    # per page and puts rows with no statement and no note into `doc.line_items`, so it happens
    # only where an author asked for it. None of the shipped 527 lines declares `anywhere`, so this
    # widening costs a shipped run nothing.
    #
    # WHAT SUCH A ROW IS AND IS NOT. It is a printed caption with figures and a page provenance,
    # citable by page (`services.face_context.other_page_rows`). It is NOT a statement row: its
    # page carries no statement, so it reaches no alias matching in `stages.map_ontology` through
    # the statement gate, and the units its figures are stated in are the DOCUMENT's rather than a
    # verified page scale — `stages.line_item_llm` flags a figure taken off one for that reason.
    from app.services import line_item_routes

    if line_item_routes.any_reads_every_page(getattr(ctx, "line_items", None)):
        # BY INDEX, NOT BY OBJECT. `PageSource` is a pydantic model and compares by field value,
        # so two blank pages of the same size are `==` and an identity test written as `not in`
        # would report a page already targeted as newly widened.
        already = {int(getattr(t, "index", -1)) for t in targets}
        widened = [p for p in doc.pages if int(getattr(p, "index", -1)) not in already]
        if widened:
            targets = list(doc.pages)
            ctx.log(f"extract:anywhere_route_widened_pages={len(widened)} "
                    f"(a line declares route=anywhere, so every page is reconstructed and not "
                    f"only the statements and the notes)")

    # Honour an explicit user page scope (from the Page Scope screen): keep only pages the
    # user chose to include. An empty selection is treated as "no restriction" so a stray
    # empty list can never silently extract nothing.
    if ctx.included_pages:
        scoped = [p for p in targets if p.index in ctx.included_pages]
        if scoped:
            targets = scoped
            ctx.log(f"extract:page_scope_applied={sorted(ctx.included_pages)}")

    number_format = _resolve_number_format(ctx, doc)
    chrome, chrome_feet = _page_chrome(pdf, targets)
    if chrome:
        ctx.log(f"extract:page_chrome={sorted(chrome)[:6]}")
    if chrome_feet:
        ctx.log(f"extract:page_foot_chrome_pages={len(chrome_feet)}")
    ocr = None
    added = 0
    ordinal = len(doc.line_items)
    # The (number, title) of the note still open at the end of the last NOTES page seen, so a
    # footnote legend that opens its page with no heading of its own (see ``extract_note_tables``)
    # still attaches to the note it explains. A non-NOTES page in between breaks the run.
    notes_carry: tuple[str, str] | None = None
    # The two-level column grid (period band over measure band) the last NOTES page was read with.
    # A PRC related-party note prints that header once and runs for eight pages; without the carry
    # every page after the first reads its four columns positionally again — a 坏账准备 provision
    # back in the "prior" slot three rows later. Reset with `notes_carry`, on the same break.
    notes_grid = None
    # The SUB-HEADING still open when the last NOTES page ended, carried for the same reason and
    # reset on the same boundary. A mainland note prints `长期应付款：` at the foot of a page
    # and its counterparty rows at the top of the next; a row whose caption is a company name has
    # nothing else to identify it by. See `row_reconstruct.build_line_items`' `carry_group`.
    notes_group: str | None = None
    # THE NOTES' TOP-LEVEL CHAPTER, as ``[numeral, highest ordinal seen]``. A mainland filing
    # numbers its notes WITHIN each chapter, so the chapter is half of a note's identity — see
    # ``notes_extract.read_chapter``. A mutable cell because the reader updates it while walking a
    # page and the strictly-increasing rule that identifies a chapter needs the highest ordinal so
    # far.
    #
    # DELIBERATELY NOT RESET WITH ``notes_carry``, which the two lines above are. Those two carry
    # state about ONE note and a non-notes page ends that note; a chapter heads a run of notes and
    # is printed once at its start, so a filing that puts a face page or a signature page inside
    # its notes would lose every chapter after it. There is nothing to reset TO either: the
    # chapter only ever advances.
    notes_chapter: list = [None, 0, ""]
    # THE RULEBOOK'S OWN VOCABULARY, so reconstruction can tell a wrapped caption's first
    # line from a line item that simply has no figures this year. A CSRC balance sheet prints
    # every template line whether the filer uses it or not, and on such a filing the two are
    # spaced identically — see ``services.mapping.known_captions`` for what it cost. Read once
    # per run rather than per page: the rulebook does not change while a document is read.
    captions = known_captions(getattr(ctx, "ontology", None)) \
        if getattr(ctx, "ontology", None) is not None else frozenset()

    for ps in targets:
        if ps.index >= pdf.page_count:
            continue
        page = pdf[ps.index]
        rect = page.rect
        w, h = max(rect.width, 1.0), max(rect.height, 1.0)

        if ps.source_kind == PageSourceKind.SCANNED:
            if ocr is None:                      # resolve the OCR provider lazily, once
                ocr = _resolve_ocr(ctx)
            if ocr is None:
                ctx.log(f"extract:page={ps.index}:scanned_no_ocr")
                continue
            words = _ocr_words_for(page, ocr, ctx)
            source_kind = "ocr"
        else:
            # Text drawn sideways is read in reading space; the page records the angle so the
            # run is auditable and the viewer knows the page is not upright.
            rot = text_rotation(page)
            if rot:
                ps.rotation = rot
                ctx.log(f"extract:page={ps.index}:text_rotation={rot}")
            words = _native_words(page, w, h, rotation=rot)
            source_kind = "native"

        if not words:
            continue
        # THE FOOT TEMPLATE IS NOT CONTENT. Dropped here, before either reader sees the page, so
        # the face reader cannot build a row out of the boilerplate's wrapped halves and the notes
        # reader cannot read it as a note's prose.
        feet = chrome_feet.get(ps.index) or ()
        if feet:
            words, before = _without_foot_chrome(words, feet), len(words)
            if len(words) != before:
                ctx.log(f"extract:page={ps.index}:foot_chrome_words={before - len(words)}")
            if not words:
                continue
        # Notes pages → note detail tables (the breakdowns behind the face figures); every
        # other page → face line items. Both keep page + bbox provenance.
        if ps.kind == PageKind.NOTES:
            from app.services.notes_extract import extract_note_tables
            grids: list = []
            groups: list = []
            tables = extract_note_tables(words, page_index=ps.index,
                                         document_id=doc.content_hash, source_kind=source_kind,
                                         scope=scope, normalisation=normalisation,
                                         carry_note=notes_carry, log=ctx.log,
                                         carry_grid=notes_grid, grid_out=grids,
                                         carry_group=notes_group, group_out=groups,
                                         chapter=notes_chapter,
                                         known_captions=captions)
            doc.notes.extend(tables)
            notes_carry = ((tables[-1].note_number, tables[-1].title) if tables
                           else notes_carry)
            # The note still open when this page ended is the LAST section, so its grid — None
            # included — is what the next page's continuation inherits.
            notes_grid = grids[-1] if grids else None
            notes_group = groups[-1] if groups else None
            continue
        notes_carry = None
        notes_grid = None
        notes_group = None
        # ``ps.statement`` (from the classifier) is what tells the reconstructor that a page is a
        # component matrix rather than a two-column comparative; ``ctx.log`` records the cases
        # where a matrix page could not be attributed and was skipped.
        #
        # ``ps.scope`` is the same classifier's verdict on WHOSE figures the page presents. It was
        # computed and dropped here, so a Company-only statement of financial position — which an
        # HKEX filing prints on its own page past the notes, with no column header naming an entity
        # — was reconstructed as the Group's and added to it under the same canonical keys.
        evidence = ps.evidence or {}
        split_y = evidence.get("matched_title_y")
        prior_statement = evidence.get("statement_before_title")
        prior_scope = evidence.get("scope_before_title")
        # THE PAGE'S OWN TITLE TRAVELS WITH THE DEFAULT BATCH. `build_line_items` needs it to
        # tell the statement's heading from the head of a wrapped caption — they have the same
        # shape and sit in the same place — and without it the title is glued onto the first
        # caption beneath it: "Balance Sheet Cash and cash equivalents", which then maps to
        # nothing and is counted as an unresolved face row. Every page used to pass it; the
        # split-batch rewrite below kept it only for the batch AFTER a mid-page statement
        # change, leaving the ordinary single-statement page — which is nearly every page — with
        # None.
        #
        # The pre-split batch of a split page keeps None deliberately: its rows belong to the
        # statement that ENDED above the title, whose own heading is on an earlier page.
        batches = [(words, ps.statement, ps.scope,
                    str(evidence.get("matched_title") or "") or None)]
        # NO LOWER BOUND ON THE TITLE'S POSITION. The classifier is the authority on whether a
        # statement is still running above the title — it records `statement_before_title` only
        # when an AMOUNT is printed above it — so a second fraction here could only overrule that
        # verdict, and did: a title at y=0.178 with a grand total and three signature lines above
        # it was refused a split, and the two statements were read as one batch. `before and
        # after` below is the real bound: a split that leaves nothing on one side is not a split.
        if (ps.kind == PageKind.FACE and prior_statement and isinstance(split_y, (int, float))
                and 0.0 < split_y < 0.95):
            before = [word for word in words if (word.bbox.y0 + word.bbox.y1) / 2 < split_y]
            after = [word for word in words if word not in before]
            if before and after:
                batches = [(before, prior_statement, prior_scope, None),
                           (after, ps.statement, ps.scope,
                            str(evidence.get("matched_title") or "") or None)]
                ctx.log(f"extract:page={ps.index}:split_statement_at={split_y:.3f}"
                        f"({prior_statement}->{ps.statement})")

        items = []
        for batch_words, statement, page_scope, page_title in batches:
            batch_items, ordinal = build_line_items(
                batch_words, page_index=ps.index, document_id=doc.content_hash,
                source_kind=source_kind, ordinal_start=ordinal, number_format=number_format,
                statement=statement, log=ctx.log, scope=scope, normalisation=normalisation,
                page_scope=page_scope, page_title=page_title,
                page_chrome=chrome, known_captions=captions)
            # THE SPLIT HAS TO SURVIVE THE PAGE, or only this loop knows about it.
            #
            # `ps.statement` is one verdict for the whole page, and four stages downstream —
            # `residual._sweep`, `normalize`'s sign cohorts, `map_ontology`'s concept gate and the
            # face-mapping contract — looked the row's statement up by page index. So the rows this
            # branch just built as BALANCE SHEET were read back as the page's `profit_and_loss`,
            # and 000709's parent-company borrowings, share capital and reserves were swept into
            # the income statement's catch-all. Stamped on the row, which is the only thing that
            # knows which side of the boundary it came off. See `buckets.statement_resolver`.
            if len(batches) > 1 and statement:
                for li in batch_items:
                    li.confidence.flags.append(f"{PRINTED_ON_FLAG}:{statement}")
            items.extend(batch_items)
        if ps.kind == PageKind.FACE:
            # Said HERE because here is where it is known: this branch reads the FACE of a
            # statement (the notes branch above returns note tables, not line items). Only a page
            # the classifier actually called a face is stamped — an unclassified page that reached
            # the reader because it is scanned is left for the segment stage to attribute, which
            # is the last thing that sees every row and every page kind together.
            for li in items:
                li.printed_in = PrintedIn.FACE
        doc.line_items.extend(items)
        added += len(items)
    # Applied over the accumulated tables, not only within a page: the note-continuation carry
    # is threaded ACROSS pages here, so an empty fragment can be raised on one page while the
    # fragment carrying the rows was raised on the one before it.
    doc.notes[:] = without_empty_duplicates(doc.notes)
    ctx.log(f"extract:pdf_line_items={added} note_tables={len(doc.notes)}")
    return added


def extract_image(data: bytes, doc, ctx: PipelineContext, *, scope=None,
                  normalisation=None) -> int:
    """Extract line items from a standalone image (PNG/JPG/TIFF) by sending the bytes straight
    to the configured OCR provider, then reconstructing rows exactly like a scanned PDF page.
    With no OCR engine configured (the default ``stub``), logs a clear 'OCR not configured'
    marker and adds nothing — never a silent empty success."""
    ocr = _resolve_ocr(ctx)
    if ocr is None:
        ctx.log("extract:image_no_ocr(configure an OCR engine; default is stub)")
        return 0
    lang = (ctx.settings.ocr.languages or ["en"])[0]
    try:
        result = ocr.recognize(data, lang=lang)
    except NotImplementedError:
        ctx.log("extract:image_ocr_not_implemented")
        return 0
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"extract:image_ocr_failed({exc})")
        return 0
    words = [Word(text=w["text"], bbox=w["bbox"]) for w in result.get("words", [])]
    if not words:
        ctx.log("extract:image_ocr_no_words")
        return 0
    items, _ = build_line_items(words, page_index=0, document_id=doc.content_hash,
                                source_kind="ocr", scope=scope, normalisation=normalisation)
    doc.line_items.extend(items)
    ctx.log(f"extract:image_line_items={len(items)}")
    return len(items)


def _resolve_number_format(ctx: PipelineContext, doc):
    """The locale ``NumberFormat`` for value parsing: the ontology's per-locale format keyed
    by the document's detected locale. Returns None for English or an unset locale so the fast
    US regex path is used unchanged — locale-aware parsing (EU decimal-comma, Indian grouping)
    activates only for a non-English document that has a declared format."""
    loc = doc.locale
    if not loc or loc == "en":
        return None
    ontology = getattr(ctx, "ontology", None)
    if ontology is None:
        return None
    by_locale = getattr(ontology, "number_format_by_locale", None) or {}
    return by_locale.get(loc)


def _resolve_ocr(ctx: PipelineContext):
    engine = ctx.settings.ocr.engine
    if engine == "stub":
        return None
    try:
        return ctx.registry.get("ocr", engine)
    except Exception as exc:
        ctx.log(f"extract:ocr_unavailable({exc})")
        return None


def _ocr_words_for(page, ocr, ctx: PipelineContext) -> list[Word]:
    """Rasterize a page and OCR it; OCR bboxes are already normalized (see OcrProvider)."""
    try:
        import fitz  # noqa: F401 - page already comes from fitz

        pix = page.get_pixmap(dpi=ctx.settings.ocr.dpi)
        png = pix.tobytes("png")
        result = ocr.recognize(png, lang=(ctx.settings.ocr.languages or ["en"])[0])
    except NotImplementedError:
        ctx.log("extract:ocr_not_implemented")
        return []
    except Exception as exc:
        ctx.log(f"extract:ocr_failed({exc})")
        return []
    return [Word(text=w["text"], bbox=w["bbox"]) for w in result.get("words", [])]
