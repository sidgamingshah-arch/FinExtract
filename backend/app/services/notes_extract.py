"""Extract the NOTE detail tables from notes pages.

A note like "Note 15: Trade receivables" is followed by its own breakdown (the rows that
give the detail behind the face figure). This groups a notes page's words into note
sections by their headings and reconstructs each note's detail line items — free-format,
whatever rows the note contains — keeping page + bbox provenance. The result populates the
``NotesTable``/``NoteItem`` model so the All-Notes view and export can show the real detail.
"""
from __future__ import annotations

import re
import statistics

from app.core.models.enums import LineRole
from app.core.models.line_item import NoteItem, NotesTable
from app.services.row_reconstruct import (
    GRID_FLAG, ColumnGrid, Word, _group_rows, _num, _scan_row, build_line_items, row_tolerance)

# "Note 15: Trade receivables", "Note 15 Trade receivables", "15. Trade receivables"
_HEADING = re.compile(r"^(?:note[s]?\.?\s+)?(?P<no>\d{1,3})\s*[:.\)\-]?\s*(?P<title>.*)$",
                      re.IGNORECASE)

# A NOTE'S OWN SUBTOTAL OR TOTAL ROW, from its caption, in both languages the shipped rulebook
# supports. THE one definition of the question "is this note row a total rather than a detail".
#
# WHY IT LIVES HERE. It was written in ``stages.map_ontology`` as ``_DISCLOSURE_TOTAL``, to keep a
# note's own total out of a declared decomposition's components, with a comment explaining that
# ``role`` could not answer the question because every ``NoteItem`` is built ``LineRole.LINE``.
# That was true of the ROW BUILDER and not of the question: the caption is decided here, where the
# ``NoteItem`` is constructed, so the role can carry the verdict and every consumer reads one
# answer instead of re-deriving it. Two consumers do: ``map_ontology``'s disclosure split, and
# ``stages.reconcile``'s "a note's own subtotal isn't a detail" guard.
#
# THAT GUARD HAD NEVER FIRED, and this is the bug that makes this more than a tidy-up.
# ``services.reconcile`` builds a note's total by summing the DETAILS it is handed
# (``note_total += d.value``), so with every row a detail a note's printed total was summed
# alongside the rows it totals: a note that ties perfectly came out at
# ``residual = face - 2 x total = -face``. Measured on a real 270-page bilingual HKEX filing:
# 453 note rows, every one ``LineRole.LINE``, and of 109 reconciliation entries NOT ONE graded
# ``tied`` — 107 ``unconfirmed``, 2 ``untied``, and both of those ``untied`` were served to the
# analyst as "does not tie" assertions that were false. With the role set, 14 entries tie with
# residual exactly 0 (notes 6, 7, 20, 21, 26, 28) and both false assertions disappear.
#
# A PREFIX MATCH, and the asymmetry of the two failure directions is why. A MISS leaves a total
# row a detail, the note total is double-counted, the residual lands nowhere near the face figure
# and the tie grades ``unconfirmed`` — the same non-answer as before, and nothing is restated. A
# FALSE POSITIVE removes a real detail from the note total, which can turn a genuine tie into a
# reported break. So only the shape a filing prints at the START of a caption counts. Measured
# over the same filing's 453 rows this matched 36, every one a printed total or subtotal
# ("Subtotal 小計", "Total revenue 收益總額", "Total tax charge for the year 年內稅項開支總額").
#
# DELIBERATELY NOT ``row_reconstruct._TOTAL_LABEL``, which looks like the same thing: its Chinese
# alternatives are un-anchored and it is used with ``.search()``. On the same 453 rows it matches
# 7 that this rejects, and all 7 are ordinary details or prose — "Share of the joint ventures'
# total comprehensive loss 應佔合營公司的全面虧損總額", "Aggregate carrying amount of the Group's
# investments in the joint ventures 本集團於合營公司的投資賬面總額". Adopting it would delete real
# details from the note total, which is the failure direction that manufactures findings.
#
# "net" and "aggregate" are NOT prefixes here, for the same reason they were rejected in
# ``map_ontology``: "Net investment in leases", "Net book value", "Net carrying value 賬面淨值"
# and "Net assets 資產淨值" are ordinary component captions.
#
# ``小计`` (simplified) is in this alternation and was NOT in ``_DISCLOSURE_TOTAL``, which carried
# ``小計`` traditional and the simplified forms of the other two only. That gap was invisible there
# and would have been an inconsistency here: the subtotal arm below recognises both forms, so
# without it a simplified-Chinese "小计" was refused by the gate and then classifiable by the arm.
# The failure direction was the safe one (a missed total stays a detail and the tie declines), which
# is why nothing caught it.
_NOTE_TOTAL = re.compile(
    r"^\s*(total|sub-?total|合\s*計|總\s*計|小\s*計|合\s*计|总\s*计|小\s*计)", re.I)
# The subtotal arm of the same alternation, read separately only to tell the two roles apart. A
# note's SUBTOTAL is a partial sum inside the note; its TOTAL is the figure the face cites. Both
# are excluded from the details, so nothing downstream depends on getting the distinction right —
# it is served to the reader (the note pane emphasises them differently) and no arithmetic reads it.
_NOTE_SUBTOTAL = re.compile(r"^\s*(sub-?total|小\s*計|小\s*计)", re.I)


def note_row_role(caption: str | None) -> LineRole:
    """The role a note detail row carries, from its printed caption.

    ``LineRole.LINE`` unless the caption opens with a total or subtotal word — see ``_NOTE_TOTAL``
    for why the test is a prefix and why it is not the pattern in ``row_reconstruct``.
    """
    text = caption or ""
    if not _NOTE_TOTAL.match(text):
        return LineRole.LINE
    return LineRole.SUBTOTAL if _NOTE_SUBTOTAL.match(text) else LineRole.TOTAL


def _bare_note_number(row: list[Word]) -> str | None:
    """A note number printed on its own, with no title yet.

    Some filings split the heading across two lines: the first line carries only the note number
    and the next line carries the title. That shape is safe to recover here because it appears at
    the top of the note block, where a naked note number is not a detail row.
    """
    tokens = [w.text.strip() for w in row if w.text.strip()]
    if len(tokens) != 1:
        return None
    text = tokens[0]
    m = re.fullmatch(r"(?:note[s]?\.?\s*)?(?P<no>\d{1,3})\s*[:.\)\-]?", text, re.IGNORECASE)
    return m.group("no") if m is not None else None


def _title_only_row(row: list[Word]) -> str | None:
    """A title line with no numbers and no value column."""
    label_words, _, values = _scan_row(row)
    if values or not label_words:
        return None
    text = " ".join(w.text for w in label_words).strip()
    if not text:
        return None
    if text[0].islower() and text[0].isascii():
        return None
    return text


# CJK SENTENCE punctuation. A note's title is a name and carries none of it; the enumeration comma
# 、 is a different mark and DOES appear in real titles ("收益、其他收入及收益", "現金及現金等價物、
# 受限制現金"), so it is deliberately absent from this class.
_CJK_SENTENCE = re.compile(r"[，。；]")


def _is_heading(row: list[Word]) -> tuple[str, str] | None:
    """A heading row names a note (number + optional title) and carries no value column of
    its own — that's what separates 'Note 15: Trade receivables' from a data row.

    THE PATTERN ALONE IS NOT ENOUGH, because it accepts any row opening with up to three digits and
    a notes page is mostly PROSE. Measured on a 270-page bilingual HKEX filing it took three
    sentence fragments for headings, and each one did more damage than an extra entry: the note
    index is keyed by number, so a fragment claiming a number that a real note already has
    overwrote — or was overwritten by — the real note's table.

        '8,461,842,000元） （附註30(b)）。'                       -> note 8
        '17. 內的合同，因此該等修訂對本集'                          -> note 17
        'note 25 to the financial statements, the Group had the …' -> note 25

    Two refusals, and each is a property of headings rather than a blocklist:

    * a title must not open with a LOWERCASE Latin letter, punctuation or a digit. A heading names
      something ("REVENUE, OTHER INCOME AND GAINS", "Trade receivables"); "to the financial
      statements" is the middle of a sentence, and "36%" or ";" name nothing. This is also what
      catches a number that is the head of a LONGER NUMERAL: the separator class this pattern
      consumes does not include the comma, so "8,461,842,000" leaves its title opening on one —
      and a percentage split across its decimal point ("28.36%" read as note 28) leaves a digit;
    * a title must not contain CJK SENTENCE punctuation (，。；). Real titles use the ENUMERATION
      comma 、 instead, so a Chinese-only heading is untouched while a Chinese sentence is refused.

    A THIRD GUARD WAS WRITTEN AND REMOVED: an explicit test for a grouped numeral. Mutating it away
    changed no test and no measured output, because the first refusal above already covers every
    shape it was for. A guard that cannot fire reads like protection without being any.
    """
    _, _, values = _scan_row(row)
    if values:
        return None
    text = " ".join(w.text for w in row).strip()
    starts_note = text.lower().startswith("note")
    m = _HEADING.match(text)
    if not m:
        return None
    no = m.group("no")
    title = m.group("title").strip(" :.-")
    # Require an explicit "Note" prefix OR a title, so a bare number isn't a false heading.
    if not starts_note and not title:
        return None
    if title:
        if title[0].islower() and title[0].isascii():
            return None
        if not (title[0].isalpha() or ord(title[0]) > 0x2E7F):
            return None                     # punctuation or a digit — names nothing
        if _CJK_SENTENCE.search(title):
            return None
    return no, title


def _has_at_most_two_value_columns(words: list[Word], source_kind: str) -> bool:
    """Whether a note fits the label-plus-two-period detail-table contract."""
    # Count before `_scan_row` applies its face-statement note-reference heuristic. In a
    # roll-forward caption such as "At 1 August 2023", that heuristic can mistake the day
    # number for a note reference and conceal the third matrix column.
    by_baseline: dict[int, list[float]] = {}
    for word in words:
        if _num(word.text) is not None:
            by_baseline.setdefault(round(word.bbox.y0 / 0.01), []).append(
                (word.bbox.x0 + word.bbox.x1) / 2)
    for centres in by_baseline.values():
        bands: list[float] = []
        for centre in sorted(centres):
            if not bands or centre - bands[-1] > 0.04:
                bands.append(centre)
        if len(bands) > 2:
            return False
    return True


# ── A NOTE'S FIRST COLUMN CAN BE A MERGED CATEGORY CELL ──────────────────────────────────────
#
# The CSRC related-party note is captioned in TWO label columns, not one:
#
#     项目名称  |          关联方              |  期末余额 …
#     其他应收款 | 中国电子科技集团公司第三十八研究所 |  88,998.71 …
#               | 中国电子科技集团公司第四十八研究所 | 200,000.00 …
#               | 安徽中电光达通信技术有限公司        |  15,753.60 …
#
# 项目名称 is printed ONCE per block and the eight rows it governs carry nothing but a related
# party's NAME. Read row by row, eight of the nine rows of the 其他应收款 block name no receivable
# class at all, so a service asking "is this row an 其他应收款?" saw one row of nine — and the one
# it saw was 88,998.71 of a 757,464.77 period-end balance.
#
# The cell is also printed VERTICALLY MERGED: on the measured filing (Sun Create Electronics
# 11077098, page index 191) "其他应收款" is drawn as "其他应收" / "款" on two lines that straddle
# the block's first data row, so neither fragment is on any row's own baseline. That is why the
# cells are assembled from the section's geometry here rather than left to the row reader, which
# folds a caption into the row BELOW it and had nowhere to put a caption that spans several.
#
# The carry is written onto ``group_hint`` — "the sub-heading printed above this row WITHIN its
# section", which is exactly what a merged 项目名称 cell is — and only where the row reader left
# it empty, so a colon sub-heading it did read always wins.
_OUTER_COLUMN_GAP = 0.05        # clear air between the category column and the caption column
_MAX_CATEGORY_CHARS = 24        # a category is a caption; a sentence in the margin is not
_CJK_SENTENCE_MARK = re.compile(r"[，。；]")


def _category_cells(rows: list[list[Word]], fmt=None) -> list[tuple[float, str]]:
    """``(top y, caption)`` for each category cell printed in a note's OUTER label column.

    Empty unless the section really has two label columns: the captions have to split into a
    dominant column and a column at least ``_OUTER_COLUMN_GAP`` to its left, and there have to be
    at least two categories and three rows under them. Every one of those is a veto — a note with
    one label column must come out of here unchanged, because ``group_hint`` is read as a row's
    meaning by the mapper and by four services.
    """
    scanned = [(row, _scan_row(row, fmt, extract_note_refs=False)) for row in rows]
    valued = [(row, lw) for row, (lw, _nr, vw) in scanned if lw and vw]
    edges = [round(min(w.bbox.x0 for w in lw), 2) for _row, lw in valued]
    if len(edges) < 3:
        return []
    main = statistics.mode(edges)
    limit = main - _OUTER_COLUMN_GAP
    frags: list[tuple[float, float, str]] = []
    for row, (lw, _nr, _vw) in scanned:
        # Measured on the word's LEFT edge and bounded by the caption column's: a category cell
        # STARTS a clear gap to the left of the captions and ENDS before them. Testing the right
        # edge instead found nothing — "其他应收" is four characters wide and runs to within 0.02
        # of the caption column, which is the cell being wide, not the cell being absent.
        outer = [w for w in lw if w.bbox.x0 <= limit and w.bbox.x1 < main]
        if not outer:
            continue
        text = "".join(w.text for w in sorted(outer, key=lambda w: w.bbox.x0)).strip()
        if text:
            frags.append((min(w.bbox.y0 for w in outer), max(w.bbox.y1 for w in outer), text))
    if not frags:
        return []
    # A merged cell drawn on several lines is ONE category: join fragments that are printed
    # directly below one another, ignoring the data rows interleaved between them.
    frags.sort()
    cells: list[tuple[float, float, str]] = [frags[0]]
    for y0, y1, text in frags[1:]:
        py0, py1, ptext = cells[-1]
        line_h = max(py1 - py0, 1e-4)
        if -0.5 * line_h <= y0 - py1 <= 0.6 * line_h:
            cells[-1] = (py0, y1, ptext + text)
        else:
            cells.append((y0, y1, text))
    out = [(y0, text) for y0, _y1, text in cells
           if len(text) <= _MAX_CATEGORY_CHARS and not _CJK_SENTENCE_MARK.search(text)]
    return out if len(out) >= 2 else []


def _row_top(li) -> float | None:
    """Where a reconstructed row was printed, in page-normalised y.

    Its LABEL's top edge when there is one, and its first figure's otherwise: a row whose caption
    wrapped over the line above its figures is anchored by the caption it is captioned with.
    """
    for ev in li.values.values():
        prov = ev.provenance
        if prov is None:
            continue
        box = prov.label_bbox or prov.bbox
        if box is not None:
            return box.y0
    return None


def _category_for(cells: list[tuple[float, str]], y: float | None) -> str:
    """The category cell governing the row printed at ``y`` — the last one that opens at or
    above it. A row above the first cell belongs to no category, not to the first one."""
    if not cells or y is None:
        return ""
    found = ""
    for top, text in cells:
        if top <= y + 0.004:            # within a line of the cell's own top edge
            found = text
        else:
            break
    return found


def extract_note_tables(words: list[Word], *, page_index: int, document_id: str | None,
                        source_kind: str, scope=None,
                        normalisation=None, llm_provider=None,
                        ai_required: bool = False,
                        carry_note: tuple[str, str] | None = None,
                        log=None,
                        carry_grid: ColumnGrid | None = None,
                        grid_out: list[ColumnGrid | None] | None = None) -> list[NotesTable]:
    """Split a notes page into note sections and reconstruct each note's detail rows.

    ``scope``/``normalisation`` are the run's own rulebook blocks; a note's columns are read by
    the same rules as the face it supports, or the note→face tie compares figures taken from
    different columns.

    ``llm_provider`` and ``ai_required`` remain accepted only for caller compatibility. Note
    tables are reconstructed exclusively from positioned source tokens.

    ``carry_note`` is the (number, title) of the note still open when the PREVIOUS page ended,
    for the caller to pass through page by page. Some filings print a note's own footnote legend
    (the explanations behind its "*"/"^"/"#" markers) a page or more after its table, with no
    heading of its own — prose that opens the page with no note number to claim it. Without a
    carry, that prose has nowhere to attach and is silently dropped from every page it opens
    before the next real heading. Seeding ``current`` with the carried note lets it attach
    instead, exactly as it would if the page break were not there.

    ``log`` is the run log. It was NOT passed through before, and that silence was a real gap: the
    reconstructor logs every scope decision it takes — which column it read as the current period,
    the unit it resolved, and now the two-level column grid — and on a filing whose figures are
    almost all in its notes (a PRC annual report), none of those decisions appeared in the run
    record at all.

    ``carry_grid`` is the two-level column grid read from an earlier page of the note still open,
    and ``grid_out`` collects the grid each section was read with so the caller can carry it to
    the next page. A PRC related-party note prints its 期末余额{账面余额|坏账准备} header once and
    then runs for eight pages; without the carry every page after the first reads four columns
    positionally, which is the mis-load the grid exists to prevent.
    """
    # The same page-derived tolerance the face uses: a note's detail lines are set as tightly as a
    # statement's, and two of them merged into one row interleave their captions (row_reconstruct.
    # row_tolerance). A note whose caption comes out scrambled ties to nothing.
    rows = _group_rows(words, row_tolerance(words, source_kind))
    sections: list[dict] = []
    current: dict | None = None
    # Not appended to ``sections`` until it actually claims a row — a page that opens straight
    # onto a real heading must not leave a spurious empty table behind under the OLD note number.
    carried: dict | None = None
    if carry_note is not None:
        carried = {"no": carry_note[0], "title": carry_note[1], "words": []}
        current = carried
    i = 0
    while i < len(rows):
        row = rows[i]
        head = _is_heading(row)
        if head is not None:
            no, title = head
            current = {"no": no, "title": title, "words": []}
            sections.append(current)
        elif current is None:
            no = _bare_note_number(row)
            if no is not None:
                title = ""
                if i + 1 < len(rows):
                    next_title = _title_only_row(rows[i + 1])
                    if next_title is not None:
                        title = next_title
                        i += 1
                current = {"no": no, "title": title, "words": []}
                sections.append(current)
        elif current is not None:
            if current is carried and carried not in sections:
                sections.append(carried)
            current["words"].extend(row)
        i += 1

    tables: list[NotesTable] = []
    for sec in sections:
        # ``on_face=False``: the rulebook's ``company_only_markers`` rule is declared about the
        # FACE ("presence of …investments_in_subsidiaries on the face is strong evidence the
        # column is company-only"). A note IS where that caption is normally printed, and reading
        # it here would relabel the note of a consolidated statement as the Company's — breaking
        # the note→face tie, which matches on (basis, period).
        seen: list[ColumnGrid | None] = []
        # ONLY THE CARRIED SECTION INHERITS A GRID. A section that starts on its own heading is a
        # different table, and letting it borrow the previous note's header would hand a four-column
        # grid to whatever four columns the next note happens to print — the same wrong-column
        # failure this closes, in the opposite direction.
        items, _ = build_line_items(sec["words"], page_index=page_index,
                                    document_id=document_id, source_kind=source_kind,
                                    on_face=False, scope=scope, normalisation=normalisation,
                                    log=log,
                                    column_grid=(carry_grid if sec is carried else None),
                                    grid_out=seen)
        # What the NEXT page inherits is the grid of the note still open when this page ended, so
        # the carry is whatever the last section was read with — None included.
        carry_grid = seen[0] if seen else None
        if grid_out is not None:
            grid_out.append(carry_grid)
        if not items and not sec["title"]:
            continue
        table = NotesTable(note_number=sec["no"], title=sec["title"], source_pages=[page_index],
                   source_text=" ".join(word.text for word in sec["words"]).strip())
        # The note's own 项目名称 column, if it has one — see `_category_cells` for the nine-row
        # block whose eight continuation rows named no receivable class at all without it.
        cells = _category_cells(_group_rows(sec["words"], row_tolerance(sec["words"], source_kind)))
        for li in items:
            # THE CAPTION DECIDES THE ROLE, EXCEPT WHERE THE BUILDER ALREADY KNEW. Almost every
            # row reaches here as ``LINE`` — the promotion that classifies a face row runs in
            # ``map_ontology``, which never sees a note — so the printed caption is what is left to
            # read it from. The one exception is a row whose caption the builder SYNTHESISED: a
            # note's block subtotal is printed on a bare line, and ``row_reconstruct`` gives it the
            # block's heading and the SUBTOTAL role, because the synthesised caption cannot be
            # re-read to recover what the geometry told it. ``li.role`` therefore wins whenever it
            # says anything other than LINE.
            role = li.role if li.role is not LineRole.LINE else note_row_role(li.source_label)
            ni = NoteItem(raw_label=li.source_label, ordinal=li.ordinal, role=role,
                          # Carried, not re-derived: the caption on such a row is indistinguishable
                          # from a row the filing captioned itself, and the note-internal arithmetic
                          # check only acts on the rows that claim to total the ones above them.
                          caption_borrowed=li.caption_borrowed,
                          # The rows the builder counted, so the arithmetic check does not have to
                          # guess them from a caption that is not an identity.
                          component_ordinals=list(li.component_ordinals),
                          section_hint=li.section_hint,
                          group_hint=(li.group_hint
                                      or _category_for(cells, _row_top(li))),
                          provenance=li.values and next(iter(li.values.values())).provenance or None)
            for ev in li.values.values():
                ni.set_value(ev)
                # The two-level-header call-out travels on the ROW as well as the value, because
                # the notes payload serves the row's flags and the note pane reads them; a value
                # flag alone would be raised where nothing on this screen looks for it.
                if GRID_FLAG in ev.confidence.flags and GRID_FLAG not in ni.confidence.flags:
                    ni.confidence.flags.append(GRID_FLAG)
            table.items.append(ni)
        tables.append(table)
    return without_empty_duplicates(tables)


def without_empty_duplicates(tables: list[NotesTable]) -> list[NotesTable]:
    """Drop a fragment that carries no rows when another fragment of the SAME note carries some.

    A note continued onto the next page is picked up by seeding a carried section with the
    previous page's number and title, which materialises on the first row that follows. On a
    filing whose notes pages carry a running header — "Notes to the Financial Statements" — that
    first row is the header, and when the note's own "(Continued)" heading comes next the carried
    section is left holding nothing but the furniture. The result was a third fragment for a
    two-page note, titled like the note and empty, which inflates the note count and can be
    served as a note with no content.

    Keyed on having no ITEMS rather than on recognising the furniture, because "what is a running
    header" is a per-filing question and this is not: a fragment with no rows, for a note that has
    rows elsewhere, adds nothing to the note however it came about.

    A note that is entirely narrative keeps its fragment — it is the only one for its number, so
    there is nothing for it to be a duplicate of.

    CALLED OVER THE WHOLE DOCUMENT as well as per page, because the fragments it has to compare
    are not always produced together. The carry that continues a note onto the next page is
    threaded across pages by ``services.pdf_extract``, so an empty fragment can be raised on one
    page while the fragment with the rows was raised on the previous one — and a per-page pass
    never sees the pair. That is not hypothetical: a backmatter page classified as notes
    ("Corporate Information") inherited the carry from the last real note and published a second,
    empty fragment under its number.
    """
    with_items = {t.note_number for t in tables if t.items}
    return [t for t in tables
            if t.items or t.note_number not in with_items or _carries_prose(t)]


# A FOOTNOTE LEGEND HAS NO ROWS AND IS NOT FURNITURE. The "*"/"^"/"#" explanations behind a note's
# markers, and its "(a)"/"(i)" narrative paragraphs, are routinely printed a page after the table
# they belong to — which is why the section builder above deliberately keeps a titled but
# item-less section. Figures are STATED in that prose and nowhere else: the depreciation charged
# to "other operating expenses" (services.deprec_impairment reads it for its highest-priority
# candidate) and a guarantee's amount (services.contingent_liabilities likewise). Dropping such a
# fragment does not merely inflate or deflate a note count, it deletes a reported number, so
# emptiness of ROWS alone cannot be the test.
#
# Furniture is what the test above was reaching for: a carried section left holding the running
# header, whose text is nothing beyond the note's own title. That is what this predicate separates
# — presence of legend/narrative structure, or simply more prose than the heading accounts for.
_LEGEND_MARKER = re.compile(r"(?m)^\s*(?:[*^#@†‡]{1,2}|\((?:[a-z]|[ivx]{1,4})\))\s+\S")


def _carries_prose(table: NotesTable) -> bool:
    """Whether a row-less fragment holds content, rather than page furniture."""
    text = (table.source_text or "").strip()
    if not text:
        return False
    if _LEGEND_MARKER.search(text):
        return True
    body = text.replace(table.title or "", "").strip()
    return len(body) >= 200
