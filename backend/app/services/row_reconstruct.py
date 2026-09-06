"""Words → line items, shared by the native-PDF and OCR paths.

Both the native text layer (PyMuPDF words) and the scanned path (OCR words) produce the
same thing: positioned words with a normalized bounding box. This module groups them into
rows, separates label / note-ref / value columns, and emits ``LineItem``s whose
``ExtractedValue.provenance`` carries the page + normalized bbox — so click-to-source works
identically whether the value came from a text layer or from OCR. Values are read here
(deterministically); semantic mapping to canonical concepts happens later.

Two layouts are handled. Most statement faces are *two-column comparatives* (current / prior,
optionally × consolidated / standalone). A statement of changes in equity is a *matrix*: its
columns are equity components (share capital, share premium, each reserve, retained profits,
total, non-controlling interests, total equity) and its rows are movements. Reading a matrix
with the two-column reconstruction produces nonsense — the columns are attributed to periods
that do not exist and the movement date is read as a value — so it gets its own path
(``_detect_matrix`` … ``_matrix_items``) that names every column from the header band.
"""
from __future__ import annotations

import json
import re
import statistics
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from app.core.models.confidence import ConfidenceVector
from app.core.models.enums import Basis, LineRole, ValueSource
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteRef, UnitContext
from app.schemas.ontology import Normalisation, ScopeSelection
from app.services.han import to_simplified
# The section vocabulary is a property of how statements are PRINTED, not of any ontology, so
# reading a banner here uses the same function mapping does rather than a second copy of it.
from app.services.mapping import section_of_banner, section_of_banner_only

_NUM = re.compile(r"^\(?-?[\d,]*\.?\d+\)?$")
_NOTE = re.compile(r"^note[s]?\.?$", re.IGNORECASE)
# A column header for the note-reference column (English + Chinese). Real statements print it
# once at the top; the cells beneath it hold bare note numbers, not monetary values.
_NOTE_HDR = re.compile(r"^(notes?|附註|附注)$", re.IGNORECASE)


# The shape of a printed note reference: a small integer, optionally with the sub-note letter the
# filing prints beside it ("16(b)", "8a"), and optionally carrying the separator that followed it
# because a row can cite several ("14, 16(b)"). Deliberately NOT "anything numeric": a monetary
# amount carries thousands separators, a decimal part or accounting parentheses, and reading one as
# a note reference DELETES a reported figure — see :func:`_scan_row`.
# A note reference token is a bare 1-2 digit note number, optionally with a suffix like "(b)".
# Three-digit bare numbers are far more likely to be real amounts on note detail rows, and the
# broad 1-3 digit form was swallowing them before the row could reach the value parser.
_NOTE_REF_TOKEN = re.compile(r"^\d{1,2}(?:\s*\([a-z]{1,3}\)|[a-z]{1,2})?[.,;]?$",
                             re.IGNORECASE)
_CHINESE_CHAPTER_NOTE_REF = re.compile(
    r"^[一二三四五六七八九十]{1,3}\s*[、,，.]\s*(?P<no>\d{1,2})[.,;]?$"
)

# How close a word has to sit to the one before it to be the NEXT WORD OF THE SAME CAPTION rather
# than the first word of a cell. A word space at statement type sizes is well under 0.01 of the page
# width and the gap to a note column is upwards of 0.1, so the threshold only has to tell
# "guaranteed notes" from "…            Note 14".
#
# Deliberately not named _CAPTION_GAP: that name is already taken further down this module, for the
# clear air between two COLUMN CAPTIONS (0.03), and a second module-level assignment of it silently
# rebound this one — which is exactly the kind of collision a one-word name invites.
_WORD_GAP = 0.02


def _is_note_ref_token(t: str) -> bool:
    """Is this token shaped like a note reference (and therefore not an amount)?"""
    s = t.strip()
    return bool(s) and bool(_NOTE_REF_TOKEN.match(s) or _CHINESE_CHAPTER_NOTE_REF.match(s))


def _note_ref_value(t: str) -> str | None:
    """Normalize a printed note-reference cell to the note's own subsection identifier."""
    s = t.strip().strip(".,;")
    if _NOTE_REF_TOKEN.match(s):
        return s
    match = _CHINESE_CHAPTER_NOTE_REF.match(s)
    return match.group("no") if match is not None else None


def _tight_after(prev: Word, w: Word) -> bool:
    """Is ``w`` printed as the next word of ``prev``'s caption, rather than in a column of its own?"""
    return (w.bbox.x0 - prev.bbox.x1) <= _WORD_GAP


def _is_note_number(t: str) -> bool:
    """A bare 1–2 digit integer — the shape of a note reference (never a formatted amount).

    A row often cites several notes ("14, 16(b)", "8, 13"), so the token carries the separator
    that followed it; it is still a note reference, not an amount.
    """
    return _note_ref_value(t) is not None


def _is_money_like(t: str, fmt=None) -> bool:
    """A numeric token that is NOT a bare note number (has a separator/decimal/sign, or ≥3 digits
    — i.e. a real amount). Used to confirm a leading small integer is a note ref, not a value."""
    return _num(t, fmt) is not None and not _is_note_number(t)


# A running-header / statement-title / period-caption label. These carry the entity or the
# statement name + a period date, not a financial line — and the only "number" on them is a
# date fragment (a year or a day-of-month).
_RUNNING_HDR = re.compile(r"annual report|interim report|年報|年度報告|中期報告", re.IGNORECASE)
_HDR_LABEL = re.compile(
    r"statement of|year ended|for the (year|period)|as at\b|as of\b|period ended|"
    # The notes pages' own running header. It belongs in THIS list rather than with the structural
    # page-chrome test, because `page_chrome` is never handed to services.notes_extract — so for a
    # row that leaked out of a NOTE page the chrome set is empty and only this label test can see
    # it. Still gated (below) on every extracted value being a date fragment, which is exactly the
    # shape of the leak: the label keeps the words and "31"/"2025" go to the value columns.
    r"notes to (the )?financial statements|財務報表附註|财务报表附注|"
    r"截至|止年度|財務狀況|现金流量|現金流量|權益變動|权益变动|全面收益|損益及其他|损益及其他|"
    r"綜合.{0,8}表|综合.{0,8}表",
    re.IGNORECASE)


def _chrome_key(text: str) -> str:
    """The page-chrome spelling of a caption — imported from the detector that builds the set, so
    the key a row is tested with and the key the set was keyed on cannot drift apart."""
    from app.services.pdf_extract import _chrome_key as key

    return key(text)


def _is_date_ish(d) -> bool:
    """A value that is really a date fragment: a year (1990–2099) or a day-of-month (1–31),
    with no fractional part (so a real figure like 0.45 or 12,345 never qualifies)."""
    try:
        iv = int(d)
    except (TypeError, ValueError):
        return False
    if iv != d:
        return False
    a = abs(iv)
    return 1 <= a <= 31 or 1990 <= a <= 2099


# The MIXED date form — Arabic numerals under the CJK date suffix: "2023年", "2024年12月31日". A
# bilingual HK/PRC filing heads its comparative columns this way as often as it does in CJK
# numerals, and the suffix is exactly what hid it: "2023年" is not a bare year (the digits are not
# word-final, so `\b(19|20)\d{2}\b` does not match — 年 is a word character) and it is not a CJK
# date either. Read as neither, the column had no heading a date could be parsed from, so which of
# the two columns was the current period fell back to position — a year out on every filing that
# prints the comparative first, which is the mis-load `period_selection` is declared against.
_MIXED_DATE = r"(?:\d{1,4}\s*[年月日])+"
# A period caption written in CJK numerals — "二零二三年" (2023), "二零二二年" (2022) — is how
# HK/PRC filings head their comparative columns. Also the plain Arabic-numeral year, and the mixed
# form above.
_PERIOD_TOKEN = re.compile(rf"[〇零一二三四五六七八九十]{{2,6}}年|{_MIXED_DATE}|\b(19|20)\d{{2}}\b|"
                           r"[〇零一二三四五六七八九十]{1,2}月|[〇零一二三四五六七八九十]{1,3}日")


def _is_period_only_label(label: str) -> bool:
    """True when the label is nothing but period captions (a column-header row).

    "二零二三年 二零二二年" heads the comparative columns; it is not a line item, whatever
    numbers happen to land on its baseline. Requires that removing the period tokens leaves
    no substantive word, so "Profit for the year ended 2023" is never mistaken for a header.
    """
    if not label or not label.strip():
        return False
    rest = _PERIOD_TOKEN.sub(" ", label)
    rest = re.sub(r"[\s\-–—/、,，.。()（）:：'\u2019\"]+", " ", rest).strip()
    return not rest


def _is_heading_with_note_only(label: str, vals: list) -> bool:
    """A section heading whose only "value" is a note reference that drifted into the row.

    Statement sections ("EQUITY HOLDERS OF THE PARENT", "NON-CURRENT ASSETS") carry no amount;
    when the sole number on the line is note-sized, it is the note column, not a figure.
    Restricted to labels with no lower-case letters so a real caption keeps its value.
    """
    if not vals or not label:
        return False
    letters = [c for c in label if c.isalpha() and c.isascii()]
    if not letters or any(c.islower() for c in letters):
        return False
    return all(_is_date_ish(v) for v in vals)


def _is_noise_row(label: str, vals: list,
                  steps: tuple[tuple[str, object], ...] = (),
                  signals: tuple[tuple[Basis, str], ...] = (),
                  page_chrome: frozenset[str] = frozenset()) -> bool:
    """A title / running-header / period-caption line that leaked in as a row. Dropped only when
    the label is header-like AND every extracted value is a date fragment — so a genuine line that
    merely mentions a statement name (its value being a real amount) is never removed.

    The caption is normalised with the rulebook's pipeline first, which is what makes the
    DECORATED forms of a column-header row recognisable: "2024 RMB'000" and
    "二零二三年（未經審核）" print a year as their only "value", but the inline unit annotation and
    the audit-status note left them looking like captions, so each was emitted as a line item
    whose amount was a year.
    """
    if _RUNNING_HDR.search(label):
        return True
    # THE FILING'S OWN RUNNING HEADER, whatever it says. The regex above is a word list and a
    # filing whose header is just its own name matches none of it; ``pdf_extract._page_chrome``
    # answers the same question structurally, from the caption being printed at the top of page
    # after page. Unlike every other rule here this one does NOT require the values to be date
    # fragments: a header that landed on a figure's baseline carries that figure, and the figure
    # is exactly what made it publishable as a line item.
    if page_chrome and _chrome_key(label) in page_chrome:
        return True
    # …AND THE SAME HEADER WITH ITS DATE SPLIT OFF. The chrome set is keyed on the whole
    # top-of-page text line ("Notes to Financial Statements 31 July 2025"), but when that header
    # lands on a figure's baseline the row keeps only the words in its LABEL and sends "31" and
    # "2025" to its value columns — so the label's key is a strict PREFIX of the chrome key and the
    # equality test above misses it. Gated on every value being a date fragment, the same
    # condition the rules below already apply, so a genuine caption that merely opens like the
    # header keeps its figures.
    if page_chrome and vals and all(_is_date_ish(v) for v in vals):
        key = _chrome_key(label)
        if key and any(c.startswith(key) for c in page_chrome):
            return True
    norm = apply_pipeline(label, steps)
    if _is_period_only_label(norm or label):
        return True
    # Nothing but annotations: after the declared strips the row has no caption at all, and its
    # only figures are date fragments — a units/period header line, not a financial line.
    if not norm and bool(vals) and all(_is_date_ish(v) for v in vals):
        return True
    if _is_heading_with_note_only(label, vals):
        return True
    if _is_basis_caption_row(label, vals, signals):
        return True
    return bool(vals) and bool(_HDR_LABEL.search(label)) and all(_is_date_ish(v) for v in vals)


@dataclass
class Word:
    text: str
    bbox: BBox   # normalized [0,1] in READING space — what row/column logic uses
    # Where the word actually sits on the rendered page, when that differs from reading space
    # (a page whose text runs sideways — see ``services.pdf_extract._reading_space``). Provenance
    # and therefore click-to-source must use THIS box: the viewer highlights the page as drawn,
    # so a reading-space box would land the highlight in the wrong place.
    page_bbox: BBox | None = None

    @property
    def source_bbox(self) -> BBox:
        return self.page_bbox or self.bbox


def _num(t: str, fmt=None) -> Decimal | None:
    """Parse a token to a MONETARY Decimal. With no ``fmt`` this uses the fast US-format path
    (comma thousands, dot decimal); when a locale ``NumberFormat`` is supplied it delegates to
    ``services.numbers.parse_number`` so EU decimal-comma (``1.234,56``), Indian grouping
    (``1,23,456``) and Arabic-Indic digits parse correctly (Req 12).

    A PERCENTAGE IS NOT A FIGURE THIS READS. ``_NUM`` used to end in ``%?`` and the body stripped
    the sign, so "45.2%" became the amount 45.2 and "(3.1)%" the amount -3.1 — filed on a template
    line, summed into a subtotal, and exported as money. Filings print percentages in their own
    columns ("% of revenue", effective tax rate, gearing), and nothing downstream can tell such a
    figure from a real one once it is a bare Decimal on a line item. Refused at the door, before
    either path, so the locale parser cannot let "45,2%" through the other side.
    """
    if "%" in t:
        return None
    if fmt is not None:
        from app.services.numbers import parse_number

        p = parse_number(t, fmt)
        return p.value_raw if p.ok else None
    if not _NUM.match(t.strip()):
        return None
    s = t.strip().replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        d = Decimal(s)
        return -d if neg else d
    except InvalidOperation:
        return None


# One line height on A4 at 10pt, give or take — which is the problem with it as a row tolerance.
# Kept as the default for inexact geometry only; see :func:`row_tolerance`.
_DEFAULT_Y_TOL = 0.012


def row_tolerance(words: list[Word], source_kind: str) -> float:
    """How far apart two words may sit vertically and still be one printed row.

    DERIVED FROM THE PAGE when the coordinates are exact, generous when they are not. The axis is
    whether the geometry can be trusted — not what kind of table it is.

    WHY A FIXED FRACTION OF THE PAGE CANNOT WORK. ``_DEFAULT_Y_TOL`` is about ONE line height, so it
    lands inside the range of leadings a filing actually uses: measured on real output, the centres
    of consecutive lines sit 0.0107 apart at 9pt leading and 0.0166 at 14pt, with the tolerance at
    0.012 in between. Everything tighter than about 11pt merges.

    AND THE MERGE IS NOT A NEAR MISS. ``_group_rows`` orders a row left to right, so two printed
    lines folded into one row come back with their words INTERLEAVED BY X. "Reversal of impairment
    of property, plant and" over "equipment, net" reads out as "Reversal equipment, of impairment
    net of property, plant and" — a caption that matches no alias in any rulebook, and one that
    reaches the analyst looking like an extraction curiosity rather than a geometry bug. Half a
    line height separates the same lines with room to spare: words of ONE line overlap vertically
    by 100%, and the tightest leading measured still sits 1.5x the tolerance away.

    OCR KEEPS THE GENEROUS DEFAULT, for the opposite reason. There a word's y comes from a
    recognised image, and residual skew after deskewing moves a word by a real fraction of a line
    across the width of a table — so the tolerance has to absorb drift WITHIN a line. Tightening it
    there would split one row into two and strand the figures away from their caption, which is a
    worse failure than a merged caption.
    """
    return _line_tol(words) if source_kind == "native" else _DEFAULT_Y_TOL


def _group_rows(words: list[Word], y_tol: float = _DEFAULT_Y_TOL) -> list[list[Word]]:
    """Cluster words into visual rows by vertical position, then order left→right.

    ``y_tol`` is the caller's, because only the caller knows how exact its coordinates are — see
    :func:`row_tolerance`."""
    ordered = sorted(words, key=lambda w: (w.bbox.y0, w.bbox.x0))
    rows: list[list[Word]] = []
    for w in ordered:
        yc = (w.bbox.y0 + w.bbox.y1) / 2
        placed = False
        for row in rows:
            ryc = sum((x.bbox.y0 + x.bbox.y1) / 2 for x in row) / len(row)
            if abs(yc - ryc) <= y_tol:
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w.bbox.x0)
    rows.sort(key=lambda r: min(w.bbox.y0 for w in r))
    return rows


def _is_banner_line(text: str, steps: tuple[tuple[str, object], ...]) -> bool:
    """Whether a label-only PRINTED LINE is a section banner rather than part of a caption.

    Two conditions, and the second is what makes the first safe to act on. The line must name a
    section, and it must not be a single English word: "Equity" is both the equity banner and the
    first word of "Equity investments designated at FVOCI", and when that caption wraps across two
    lines the two are the same text on the same geometry — nothing can tell them apart, and guessing
    banner truncates the caption to "investments designated at FVOCI" and scopes a non-current asset
    to equity. Multi-word banners ("Current assets", "Operating activities") and CJK ones
    ("流動資產", "權益") carry no such ambiguity, because a CJK caption that merely begins with a
    section compound is written as one unbroken run and never appears as a line of its own.

    The cost is a title-case one-word English banner, which stays unrecognised. ALL-CAPS "EQUITY"
    is already a banner to ``_looks_like_header``, so the gap is narrow and it errs toward keeping a
    caption whole — a truncated caption maps to whatever its tail resembles, which is worse than a
    missing section.
    """
    if section_of_banner_only(apply_pipeline(text, steps)) is None:
        return False
    return len(text.split()) >= 2 or bool(_HAN.search(text))


def _label_sub_lines(label_words: list[Word]) -> list[list[Word]]:
    """The PRINTED lines a row's label words came from, in print order.

    ``_group_rows`` clusters with a deliberately generous tolerance so a slightly skewed scan still
    groups (see its docstring), and that generosity merges a section banner printed immediately
    ABOVE its first line item into that item's row. The words keep their own y, so the printed lines
    are still recoverable here — and a caption printed on ONE line always yields exactly one of
    them, which is what makes acting on a two-line split safe.
    """
    if not label_words:
        return []
    tol = max(0.45 * _median([w.bbox.y1 - w.bbox.y0 for w in label_words]), 0.001)
    lines: list[list[Word]] = []
    for w in sorted(label_words, key=lambda x: ((x.bbox.y0 + x.bbox.y1) / 2, x.bbox.x0)):
        yc = (w.bbox.y0 + w.bbox.y1) / 2
        if lines:
            prev = sum((x.bbox.y0 + x.bbox.y1) / 2 for x in lines[-1]) / len(lines[-1])
            if abs(yc - prev) <= tol:
                lines[-1].append(w)
                continue
        lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w.bbox.x0)
    return lines


def _any_banner_line(label_words: list[Word], steps: tuple[tuple[str, object], ...]) -> bool:
    """Whether any PRINTED LINE of these label words is a section banner.

    Per line and not over the joined text, because row clustering sorts a row by x: a bilingual
    banner whose English and Chinese halves share the left margin comes back interleaved —
    "Current 流動資產 assets" — and no exhaustive test can see a banner in that. Splitting back into
    printed lines recovers "Current assets" and "流動資產", each of which is one.

    This subsumes a narrower rule that used to live in ``_is_wrapped_head`` (a bilingual pair naming
    the same section is one banner, not a caption wrapping into its translation). That rule was
    needed while ``_is_banner_line`` matched a section phrase as a SUBSTRING, since the interleaved
    join still contained one; under the exhaustive test it does not, and reading the printed lines
    answers the question where it is actually decidable. ``_is_wrapped_head`` is about whether a
    caption wraps, and it is better off not also deciding what a banner is.
    """
    return any(_is_banner_line(_join_words(line), steps) for line in _label_sub_lines(label_words))


def _split_banner_prefix(label_words: list[Word], steps: tuple[tuple[str, object], ...]
                         ) -> tuple[str | None, list[Word]]:
    """``(banner, caption words)`` when a valued row's label begins with section banners printed on
    their own line(s) above the caption; ``(None, label_words)`` when it does not.

    THE DEFECT THIS CLOSES, off a real HKFRS condensed balance sheet: the section banner is printed
    directly above the first line item of its section, close enough that row clustering merges the
    two. The row then carries a value, so the label-only banner branch never sees it, and the
    same-line split above it is gated on a colon this format does not print. ``section`` therefore
    stayed on the PREVIOUS banner — "Non-current assets" — and every current asset and current
    liability inherited it, which sent them all to ``bs_non_current_assets__others``.

    Geometry decides it, not the words, and there are two geometries. The banner may sit on its own
    PRINTED LINE above the caption (row clustering then merges the two), or BESIDE it on one baseline
    separated by clear air. Both are handled below; the second is why the whole-label text is never
    tested on its own.

    Requiring the banner to occupy a whole line, or a whole horizontally-separated run of one, is
    what keeps this from firing on the many real captions that merely begin with a section phrase —
    "Equity investments designated at FVOCI" (a non-current asset), "Total current assets",
    "长期负债的流动部分" (current portion of long-term debt), "Revenue", "Taxation". Seventy-three
    captions in the shipped rulebook start with a section phrase; every one of them is printed on a
    single line, so none of them reaches the banner test at all.
    """
    lines = _label_sub_lines(label_words)
    if not lines:
        return None, label_words
    banner: str | None = None
    i = 0
    while i < len(lines) - 1:
        text = _join_words(_regroup_scripts(lines[i]))
        if not _is_banner_line(text, steps):
            break
        # A banner does not wrap into the line beneath it; a long caption does. Reuse the grammar
        # test that already tells those apart — without it "Equity" printed above "investments
        # designated at FVOCI" reads as the equity banner and the caption is truncated to its tail.
        if _is_wrapped_head(lines[i], lines[i + 1], steps):
            break
        banner = text                      # the NEAREST banner wins, as elsewhere
        i += 1
    # The banner may also sit BESIDE the caption on one printed line, which is the shape a filing
    # produces when it prints the heading and its first item on a single baseline rather than on
    # consecutive lines. The sub-line walk above cannot see that — one baseline is one sub-line — so
    # the remaining line is split by horizontal WHITESPACE instead, the same way `_basis_bands` tells
    # two column captions apart from one sentence naming both. Measured on this shape: the gap
    # between "流動資產" and "Inventories" is 0.085 of the page width while spacing inside either
    # phrase is 0.004, so `_CAPTION_GAP` separates them with two orders of magnitude to spare, and
    # "Equity investments designated at FVOCI" stays a single run.
    #
    # Per printed LINE, never over the whole label: `_x_runs` welds across a line break, because the
    # x of a following line's first word is far to the LEFT of the previous line's last word and a
    # negative gap trivially satisfies its threshold.
    caption_lines = lines[i:]
    if caption_lines:
        runs = _x_runs(caption_lines[0], _CAPTION_GAP)
        if len(runs) >= 2:
            head = _join_words(_regroup_scripts(runs[0]))
            if _is_banner_line(head, steps):
                banner = head
                caption_lines = [[w for run in runs[1:] for w in run], *caption_lines[1:]]
    if banner is None:
        return None, label_words
    caption = [w for line in caption_lines for w in line]
    return (banner, caption) if caption else (None, label_words)


def _scan_row(row: list[Word], fmt=None, *, extract_note_refs: bool = True) -> tuple[list[Word], str | None, list[Word]]:
    """Split one visual row into (label words, note-ref, value words).

    A "Note"/"Notes" token printed in a cell of its own, plus the *single* following number, is a
    note reference and not a value — the value lives in the far-right column, so it must not be
    consumed as one.

    BUT "notes" is also an ordinary word of the balance sheet's own vocabulary: guaranteed notes,
    convertible notes, promissory notes, notes payable. Firing on the word alone read "Interest on
    guaranteed notes 201,551 221,188" as the caption "Interest on guaranteed", a note reference of
    "201,551", and one figure — the other was consumed and DELETED, which is the worst outcome
    available here. Two things have to hold before the word is read as a keyword:

    * the token after it is shaped like a note reference (:func:`_is_note_ref_token`) — never an
      amount. A token that is not is left where it is, so a figure can no longer be swallowed even
      if everything else about the row misleads; and
    * the word is not printed tight against the caption it would otherwise belong to
      (:func:`_tight_after`). "Guaranteed notes 36 3,877,188" keeps its caption whole and lets the
      note column place the 36 geometrically (:func:`_resolve_note_column`), which is the better
      evidence anyway; "Cash and cash equivalents        Note 14  1,000" still reads as a keyword.
    """
    label_words: list[Word] = []
    note_ref: str | None = None
    value_words: list[Word] = []
    i = 0
    while i < len(row):
        tok = row[i].text.strip()
        if (extract_note_refs and _NOTE.match(tok) and i + 1 < len(row) and _is_note_ref_token(row[i + 1].text)
                and not (i and _tight_after(row[i - 1], row[i]))):
            note_ref = _note_ref_value(row[i + 1].text)
            i += 2
            continue
        # A BARE NOTE NUMBER STANDING BETWEEN THE LABEL AND THE FIGURES, as in
        # "Revenue  6  45,230  40,110" — where 6 is note 6 and not the current-year amount.
        #
        # THE MONEY-LIKE LOOKAHEAD IS WHAT MAKES THAT SAFE, and without it this branch ate the
        # first value column of any row whose figures are small: in "Costs 50 40 41" it took 50
        # as a note reference. Worse than losing one cell, it collapsed the page's column
        # geometry — with two of three rows down to two figures, the inferred columns became two,
        # and a row that HAD kept three then had its leftmost figure fall outside them and
        # dropped too. A three-column statement came back as two, with the years relabelled.
        #
        # `_resolve_note_column` already carries this rule with the guard (see its final clause),
        # but it cannot help here: it returns early once `note_ref` is set, so a token taken on
        # shape alone is never reconsidered. The guard therefore belongs on both.
        if (extract_note_refs and note_ref is None and label_words and not value_words
                and _is_note_ref_token(tok)
                and any(_is_money_like(w.text, fmt) for w in row[i + 1:])
                and not _tight_after(row[i - 1], row[i])):
            note_ref = _note_ref_value(tok)
            i += 1
            continue
        if _num(tok, fmt) is not None:
            value_words.append(row[i])
        elif not value_words:   # text before any number is part of the label
            label_words.append(row[i])
        i += 1
    return label_words, note_ref, value_words


def _row_box(row: list[Word]) -> BBox:
    b = row[0].bbox
    for w in row[1:]:
        b = b.union(w.bbox)
    return b


def _looks_like_header(label_words: list[Word],
                       steps: tuple[tuple[str, object], ...] = ()) -> bool:
    """A section header (e.g. "Non-current assets", "ASSETS") is a standalone label line,
    NOT a wrapped continuation — never fold it into a neighbouring valued row.

    The caption is put through the rulebook's normalisation first (minus the case fold, which
    would erase the ALL-CAPS shape being tested) so the printed decorations do not hide the
    shape: a footnote-marked banner ("ASSETS*"), a caption carrying a zero-width space, and a
    CJK sub-heading ending in the FULLWIDTH colon ("其他全面收益：") are all headers, and the
    last of those used to be read as a wrapped continuation and glued onto the row below it.
    """
    text = apply_pipeline(" ".join(w.text for w in label_words), steps,
                          exclude=("case_fold", "trailing_colon"))
    if not text:
        return True
    if text.rstrip(":").isupper():          # ALL-CAPS banners
        return True
    if text.endswith(":"):                  # "Represented by:" style headers
        return True
    return False


# Words that cannot END a finished caption ("TOTAL ASSETS LESS CURRENT …") or that can only
# CONTINUE one ("… AND CASH EQUIVALENTS", "… FOR THE YEAR"). Their presence is what separates a
# wrapped ALL-CAPS label from a genuine ALL-CAPS section banner ("ASSETS", "EQUITY").
# Bare adjectives and connectives cannot end a finished caption, so a line ending in one is a
# wrapped head. "TOTAL COMPREHENSIVE" / "LOSS FOR THE YEAR" is the case that matters most: losing
# the head leaves the tail to be mapped as the profit-or-loss bottom line it is not.
_HEAD_INCOMPLETE = re.compile(
    r"\b(less|in|and|or|of|for|to|from|with|net|total|other|that|which|current|non"
    r"|comprehensive|gross|accumulated|retained|attributable)\s*$",
    re.IGNORECASE)
_CONT_STARTS = re.compile(r"^\s*(and|or|of|for|to|in|from|with|that|which|upon)\b",
                          re.IGNORECASE)
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def _CJK_ONLY(text: str) -> bool:  # noqa: N802 - reads as a predicate at call sites
    """Text that carries Han characters and no Latin words — a translation line."""
    return bool(_HAN.search(text)) and not re.search(r"[A-Za-z]{2,}", text)


def _is_wrapped_head(head: list[Word], cont: list[Word],
                     steps: tuple[tuple[str, object], ...] = ()) -> bool:
    """Whether an ALL-CAPS label-only line is the first line of a WRAPPED caption.

    Statement faces print both: banners that head a group ("ASSETS", "NON-CURRENT
    LIABILITIES") and long captions that wrap ("TOTAL ASSETS LESS CURRENT" / "LIABILITIES",
    "NET DECREASE IN CASH" / "AND CASH EQUIVALENTS"). Treating every ALL-CAPS line as a banner
    discards the head and leaves the valued row labelled with a meaningless tail — which then
    maps to whatever concept that tail resembles. Grammar tells them apart: a wrapped head ends
    mid-phrase, or its continuation begins with a connective.
    """
    head_text = " ".join(w.text for w in head).strip()
    cont_text = " ".join(w.text for w in cont).strip()
    if not head_text or not cont_text:
        return False
    # A caption ending in a colon INTRODUCES the rows beneath it and never wraps into them —
    # the same statement `_heads_indented_block` makes on the matrix path. Tested through the
    # rulebook's fullwidth→halfwidth fold, because a CJK sub-heading ends in "：": untested, it
    # fell to the bilingual rule below and the heading was glued onto the first row under it.
    if _ends_with_colon(head_text, steps):
        return False
    # A bilingual filing prints the translation of the SAME caption on the next line. A
    # CJK-only continuation is therefore never a new banner — and without this the Chinese
    # line breaks the chain between an English wrap and the row carrying the figures.
    if _CJK_ONLY(cont_text):
        return True
    # In a bilingual filing the translation is appended to the SAME row, so the head's last
    # English word is not the row's last word. Grammar is judged on the Latin portion only.
    head_latin = re.sub(r"\s+", " ", _HAN.sub(" ", head_text)).strip()
    return bool(_HEAD_INCOMPLETE.search(head_latin)) or bool(_CONT_STARTS.match(cont_text))


_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[A-Za-z]")


def _script_of(text: str) -> str | None:
    """Which script a token is written in, or None for digits/punctuation that belong to
    whichever script surrounds them."""
    if _HAN.search(text):
        return "han"
    if _ARABIC.search(text):
        return "arabic"
    if _LATIN.search(text):
        return "latin"
    return None


def _label_lines(words: list[Word]) -> list[list[Word]]:
    """A label's words split back into the physical lines they were printed on.

    A merged caption arrives as line-one's words followed by line-two's, so consecutive words
    with the same vertical centre are one printed line.
    """
    if not words:
        return []
    heights = sorted(max(w.bbox.y1 - w.bbox.y0, 1e-6) for w in words)
    tol = heights[len(heights) // 2] * 0.6
    lines: list[list[Word]] = []
    cur = [words[0]]
    ref = (words[0].bbox.y0 + words[0].bbox.y1) / 2
    for w in words[1:]:
        yc = (w.bbox.y0 + w.bbox.y1) / 2
        if abs(yc - ref) <= tol:
            cur.append(w)
        else:
            lines.append(cur)
            cur = [w]
            ref = yc
    lines.append(cur)
    return lines


def _regroup_scripts(words: list[Word]) -> list[Word]:
    """Keep each language contiguous in a caption that wrapped across printed lines.

    A bilingual filing sets the two languages side by side in the label column and lets the pair
    wrap together::

        Share of other comprehensive      應佔合營公司其他
        income of joint ventures          全面收益

    Reading that in printed order — which is what merging the two lines does — splices the
    languages into each other: "Share of other comprehensive 應佔合營公司其他 income of joint
    ventures 全面收益". The figures are right and the caption is complete, but neither language is
    a phrase any more, so an alias cannot match it and a model reading it has to reassemble two
    interleaved sentences before it can decide what the line is. Grouping the runs by script
    restores both: "Share of other comprehensive income of joint ventures" followed by
    "應佔合營公司其他全面收益".

    Applied only to a caption that genuinely spans more than one line, and only when the scripts
    actually alternate more than once. A single line reading "Goodwill (商譽) impairment" is in the
    order it was written, and reordering it would be the mistake this avoids.
    """
    if len(_label_lines(words)) < 2:
        return words
    runs: list[tuple[str | None, list[Word]]] = []
    for w in words:
        s = _script_of(w.text)
        if runs and (s is None or s == runs[-1][0]):
            runs[-1][1].append(w)
        else:
            runs.append((s, [w]))
    # A leading run of digits or punctuation belongs to whatever script follows it.
    if len(runs) > 1 and runs[0][0] is None:
        runs[1][1][:0] = runs[0][1]
        runs.pop(0)
    scripts = [s for s, _ in runs if s is not None]
    # Two runs is one language after the other — already contiguous, nothing to regroup.
    if len(set(scripts)) < 2 or len(runs) <= 2:
        return words
    order: list[str] = []
    for s in scripts:
        if s not in order:
            order.append(s)
    out: list[Word] = []
    for script in order:
        for s, ws in runs:
            if s == script:
                out.extend(ws)
    return out if len(out) == len(words) else words


def _join_words(words: list[Word]) -> str:
    """Words as a caption. No space is inserted between two Han tokens: Chinese is not written
    with spaces, and one inserted between "應佔合營公司其他" and "全面收益" stops the caption
    matching the alias an ontology actually lists."""
    parts: list[str] = []
    for w in words:
        if parts and _HAN.search(w.text) and _HAN.search(parts[-1][-1:]):
            parts[-1] = parts[-1] + w.text
        else:
            parts.append(w.text)
    return " ".join(parts).strip()


def _wrap_reaches_a_value(rows: list[list[Word]], idx: int, fmt=None,
                          steps: tuple[tuple[str, object], ...] = (), max_lines: int = 3) -> bool:
    """Whether the label-only line at ``idx`` begins a caption that reaches a valued row.

    THE DEFECT THIS CLOSES, reported off the notes to a real filing: a caption that wraps over MORE
    THAN TWO lines lost everything but its tail. "Deposits paid for acquisition of" / "land use
    rights in the" / "PRC  2,500" was published as "land use rights in the PRC" — a note detail a
    reader cannot identify and the mapper cannot place.

    The wrap test required the IMMEDIATE next row to carry values, which is true of the last
    continuation line and false of every earlier one. So on a three-line caption the first line
    failed the test, was emitted as a label-only row, and was skipped.

    Bounded at ``max_lines`` continuation lines, and each STEP is still checked for tight spacing and
    label-column alignment by the caller as it walks — a note page is mostly prose, and an unbounded
    look-ahead would glue a paragraph onto the first figure beneath it.
    """
    for step in range(1, max_lines + 1):
        j = idx + step
        if j >= len(rows):
            return False
        label_words, _note, value_words = _scan_row(rows[j], fmt)
        if value_words:
            return True
        if not label_words:
            return False
        # A banner or a colon sub-heading introduces what follows; it never continues a caption, so
        # the chain stops here rather than reaching past it for a figure.
        text = _join_words(label_words)
        if _looks_like_header(label_words, steps) or _is_banner_line(text, steps):
            return False
        if not _wrap_adjacent(_row_box(rows[j - 1]), _row_box(rows[j]), label_words):
            return False
    return False


def _is_page_title(label_words: list[Word], page_title: str | None) -> bool:
    """Is this label line (part of) the statement TITLE the classifier matched on this page?

    A title is a label-only line sitting directly above the first item, which is the shape of a
    wrapped caption — so a filing that prints "Balance Sheet" in title case had its title folded
    onto its first line item ("Balance Sheet Cash and cash equivalents") and that row then mapped
    to nothing. ALL-CAPS titles never showed it, because ``_looks_like_header`` already refuses
    those, which is why this survived a real 367-page filing.

    Matched by containment, because the classifier reports the title as ONE string even when the
    page prints it over two lines ("Consolidated Statement of" / "Financial Position"), so each
    printed line is a fragment of it. The caller only applies this ABOVE the first valued row — the
    zone a title can occupy — so a body caption that happens to echo a word of the title is
    untouched.
    """
    if not page_title or not label_words:
        return False
    from app.services.mapping import normalize_label

    text = normalize_label(_join_words(label_words))
    return bool(text) and text in normalize_label(page_title)


def _merge_wrapped_labels(rows: list[list[Word]], fmt=None,
                          steps: tuple[tuple[str, object], ...] = (),
                          page_title: str | None = None) -> list[list[Word]]:
    """Fold a label-only line into the following valued row when the two are clearly one
    wrapped label: tight vertical spacing *and* left-alignment inside the label column.

    Conservative on purpose — a wrong merge corrupts a label. A label-only line that reads
    like a section header, that is the page's own statement title, or that is loosely spaced /
    mis-aligned, is left untouched (the main loop then simply skips it, as before).
    """
    out: list[list[Word]] = []
    pending: list[Word] = []
    seen_value = False
    for idx, row in enumerate(rows):
        label_words, note_ref, value_words = _scan_row(row, fmt)
        if value_words:
            out.append(pending + row if pending else row)
            pending = []
            seen_value = True
            continue
        if not seen_value and _is_page_title(label_words, page_title):
            out.append(pending + row if pending else row)   # chrome: never a caption's head
            pending = []
            continue
        # Label-only (or note-only) line: candidate wrapped-label continuation.
        nxt = rows[idx + 1] if idx + 1 < len(rows) else None
        is_wrap = (
            nxt is not None
            and label_words
            and note_ref is None
            # An ALL-CAPS banner is not a continuation — unless grammar shows the caption
            # actually wraps into the next line (see `_is_wrapped_head`).
            # `_looks_like_header` accepts only ALL-CAPS or a trailing colon, so a filing that
            # prints "Current assets" in title case — or the CJK half of a bilingual banner — was
            # taken for a wrapped-label continuation and folded into the first item beneath it,
            # losing the section for the whole block. A recognised banner is not a continuation.
            and (not (_looks_like_header(label_words, steps)
                      or _any_banner_line(label_words, steps))
                 or _is_wrapped_head(label_words, _scan_row(nxt, fmt)[0], steps))
            # …and the caption reaches a figure. Not necessarily on the NEXT row: a caption may
            # wrap over three or more lines, and requiring the value immediately dropped every line
            # but the last two (see `_wrap_reaches_a_value`).
            and _wrap_reaches_a_value(rows, idx, fmt, steps)
            and _wrap_adjacent(_row_box(row), _row_box(nxt), _scan_row(nxt, fmt)[0])
        )
        if is_wrap:
            pending = pending + row
        else:
            out.append(pending + row if pending else row)
            pending = []
    if pending:                                          # trailing label-only text, no value
        out.append(pending)
    return out


def _tight_below(cur: BBox, nxt: BBox) -> bool:
    """True when `nxt` is the next printed line of the same text block as `cur` — the vertical
    half of the wrap test, also used to walk a stacked column-header band line by line."""
    gap = nxt.y0 - cur.y1
    line_h = max(cur.y1 - cur.y0, 1e-4)
    return -0.5 * line_h <= gap <= 0.6 * line_h


def _wrap_adjacent(cur: BBox, nxt: BBox, nxt_label: list[Word]) -> bool:
    """True when `cur` sits directly above `nxt`'s label with paragraph-tight spacing.

    A NEXT ROW WITH NO LABEL AT ALL HAS NOTHING TO ALIGN TO, and comparing the caption's left edge
    against the row's own box then compares it against the first FIGURE — half a page to the right,
    so the test could only fail. That is a real printed shape, not an edge case: a table cell whose
    caption wraps over two lines has its figures set on the line BETWEEN them, which is how

        中电科技（合肥）博微信息发展有限责任公
                    95,239.27   28,571.78   68,604.36   20,581.31
        司

    is drawn. The caption row and the bare-figure row were emitted separately, the figure row had
    no caption, and reconstruction dropped it — measured on Sun Create Electronics 11077098, note
    6(1), where it lost exactly one party of a nine-party block and with it 95,239.27 of a
    757,464.77 period-end balance. The vertical tightness `_tight_below` already required IS the
    whole test here, and the caller's other vetoes (a banner is not a continuation, the caption has
    to reach a figure) are unchanged.
    """
    if not _tight_below(cur, nxt):                       # tight spacing (same text block)
        return False
    if not nxt_label:
        return True
    label_x0 = min(w.bbox.x0 for w in nxt_label)
    return abs(cur.x0 - label_x0) <= 0.06                # left-aligned in the label column


# ── The rulebook's scope_selection / normalisation blocks ────────────────────────────────────
#
# WHY reconstruction reads the rulebook at all. ``scope_selection`` is a statement about how a
# printed PAGE is read — which column is the Group's, which column is the current period, what
# scale the figures are in — and every one of those decisions is taken here, before mapping has
# an ontology in hand. Left to the engine's own regexes the declared block was decoration: a
# filing headed "Group | Company" (the HKEX house style) got NO basis bands at all, so every
# Company figure was filed as consolidated and quietly added to the Group's.
#
# The blocks are read from the rulebook shipped as the one in force, because ``stages.extract``
# runs before an ontology is attached to the run. A caller that does hold the run's pinned
# rulebook passes it instead (``build_line_items(scope=…, normalisation=…)``).
_RULEBOOK_IN_FORCE = (Path(__file__).resolve().parents[1] / "sample" / "templates"
                      / "hkfrs_hk_china_ontology.json")


class MissingRulebookError(RuntimeError):
    """The shipped rulebook this module reads its page rules from is not on disk."""


@lru_cache(maxsize=1)
def in_force_rules() -> tuple[ScopeSelection | None, Normalisation | None]:
    """``(scope_selection, normalisation)`` of the rulebook in force, or ``(None, None)``.

    Only those two blocks are validated, not the whole rulebook: this is consulted for every page,
    and nothing else in the definition says anything about how a page is read. A block that will not
    VALIDATE governs nothing rather than stopping the extraction — the run still produces figures,
    and the log records which rules were applied.

    A MISSING FILE is a different thing and is now raised. It used to return ``(None, None)`` beside
    the invalid-block case, which is how consolidating the two rulebook generations into one file
    silently switched off every declared page rule: the path here still named the old filename, the
    read failed, and Group/Company banding, period selection and unit resolution all reverted to
    engine defaults. Extraction went on succeeding — every Company figure filed as consolidated and
    added into the Group's. Thirteen tests caught it, and each one reported a wrong basis rather than
    a missing rulebook, so the cause took finding. An absent shipped file is a packaging defect, and
    ``sample/reference.py`` already treats one as a startup failure for the same reason.
    """
    if not _RULEBOOK_IN_FORCE.exists():
        raise MissingRulebookError(
            f"the shipped rulebook {_RULEBOOK_IN_FORCE} is missing, so the scope_selection and "
            f"normalisation rules every page is read under would silently not apply")
    try:
        raw = json.loads(_RULEBOOK_IN_FORCE.read_text(encoding="utf-8"))
    except ValueError:
        return None, None
    scope = norm = None
    if isinstance(raw.get("scope_selection"), dict):
        try:
            scope = ScopeSelection.model_validate(raw["scope_selection"])
        except Exception:                       # noqa: BLE001 — see the docstring
            scope = None
    if isinstance(raw.get("normalisation"), dict):
        try:
            norm = Normalisation.model_validate(raw["normalisation"])
        except Exception:                       # noqa: BLE001
            norm = None
    return scope, norm


# ── normalisation.pipeline ───────────────────────────────────────────────────────────────────
#
# The rulebook authors the text pipeline as an ORDERED list of steps in prose. Each declared step
# is matched to an implementation by the words it uses and they are applied in the order declared,
# so a step the rulebook adds, reorders or deletes changes what the engine does. That is the only
# way the list can be read as a specification: an unimplemented step is a policy a reviewer can
# look up and the engine ignores.
#
# The steps are used for COMPARISON only — never to rewrite ``source_label``, which has to stay
# exactly as printed for provenance.
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff\u00ad]")
# ``╱ ／ ⁄ → /``, ``（） → ()``, ``、 → ,`` and the fullwidth colon are named by the declared step;
# NFKC alone leaves ╱ and 、 untouched.
_WIDTH_MAP = {ord(c): "/" for c in "╱／⁄"} | {
    ord("（"): "(", ord("）"): ")", ord("、"): ",", ord("："): ":", ord("﹕"): ":",
    ord("；"): ";", ord("，"): ",", ord("　"): " ",
}
_SUPERSCRIPT = re.compile("[*\u2020\u2021#\u00b9\u00b2\u00b3\u2070-\u209f]+")
# EVERY FORM IS DELIMITED. With both brackets optional and no anchor this matched a bare
# "notes <digits>" anywhere, and the digit cap then ate a four-digit year only PARTLY:
# "Senior notes 2025" became "Senior 5" — the head noun deleted and a token fabricated, on a
# caption the shipped rulebook carries four aliases for. The rulebook's own footnote step names
# only bracketed forms and the bare CJK marker ("trailing digits in parentheses, superscripts,
# '(note 12)', '附註12', '(附注12)'"), so those are what this recognises, plus a citation leading a
# caption with the colon that delimits it. ``(?!\d)`` refuses a digit run too long to be a note
# number instead of taking a prefix of it. Duplicated in ``mapping._NOTE_CITATION`` — a note
# reference is one shape and both copies must recognise it, so a change to either belongs in both.
_NOTE_MARKER = re.compile(
    r"[(（]\s*(?:notes?|附註|附注)\s*\.?\s*\d{1,3}(?!\d)[a-z]?"
    r"(?:\s*[(（][a-z0-9]{1,3}[)）])?\s*[)）]"
    r"|(?:附註|附注)\s*\d{1,3}(?!\d)"
    r"|^\s*notes?\s*\.?\s*\d{1,3}(?!\d)[a-z]?\s*[:：]"
    r"|[(（]\s*(?:notes?|附註|附注)\s*$",
    re.IGNORECASE)
_TRAILING_PAREN_DIGITS = re.compile(r"\s*[(（]\s*\d{1,3}[a-z]?\s*[)）]\s*$")
_LEADING_NUMBERING = re.compile(
    r"^\s*(?:[(（]\s*(?:\d{1,3}|[a-z]|[ivx]{1,4}|[一二三四五六七八九十]{1,3})\s*[)）]"
    # The comma is in the delimiter set because the declared width step has already mapped 、 to
    # it by the time this runs — the rulebook lists the steps in that order.
    r"|(?:\d{1,3}|[一二三四五六七八九十]{1,3})\s*[.、,，·)]"
    r"|[-－–—•·‧])\s*")
_TRAILING_SUBCAPTION = re.compile(r"[\s:;\-－–—]+$")
_AUDIT_STATUS = re.compile(
    r"[(（]?\s*(?:un)?audited\s*[)）]?"
    r"|[(（]?\s*(?:未經審核|未经审核|經審核|经审核|未經審計|未经审计)\s*[)）]?", re.IGNORECASE)
# The literal examples a declared step names ('(a)', "RMB'000", '（未經審核）') are the step's own
# vocabulary; harvesting them means the rulebook, not this module, lists the tokens.
_QUOTED_EXAMPLE = re.compile(r"'([^']{1,24})'|\"([^\"]{1,24})\"|“([^”]{1,24})”")


def _quoted_examples(step: str) -> tuple[str, ...]:
    out: list[str] = []
    for m in _QUOTED_EXAMPLE.finditer(step):
        tok = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if tok:
            out.append(tok)
    return tuple(out)


def _fold_for_match(text: str) -> str:
    """Whitespace- and apostrophe-insensitive form, so "RMB '000" matches the declared "RMB'000".

    Han is folded to Simplified on this side too. The rulebook writes its signals in Traditional
    (人民幣千元) because that is how a Hong Kong filing prints them, and by the time the annotation
    step runs the declared t2s fold has already turned the caption Simplified — so comparing the
    two unfolded matched nothing at all.
    """
    t = to_simplified(unicodedata.normalize("NFKC", text)).translate(_WIDTH_MAP).lower()
    for q in "’‘`´":
        t = t.replace(q, "'")
    return re.sub(r"\s+", "", t)


_EMPTY_BRACKETS = re.compile(r"[(（\[]\s*[)）\]]")


def _drop_empty_brackets(text: str) -> str:
    """Remove the brackets a stripped annotation was printed inside: "（附註12）" reaches the note
    pattern already emptied by the literal strip, and "其他應付款項( )" is not a caption."""
    return _EMPTY_BRACKETS.sub(" ", text)


# Any glyph a filing may print where the declared literal writes a straight apostrophe. The units
# annotation is the case that matters: a rulebook declares "RMB'000" and a real PDF prints RMB’000
# with the typographic quote, which is a different codepoint.
_APOSTROPHES = "'’‘`´"


def _strip_literals(text: str, literals: tuple[str, ...]) -> str:
    """Remove each declared literal, matched insensitively to spacing and apostrophe glyph.

    THE APOSTROPHE INSENSITIVITY HAS TO BE IN THE PATTERN, not only in the fold. ``_fold_for_match``
    normalises the LITERAL's quote to a straight one, and the pattern built from it was then matched
    against the caption UNFOLDED — so the declared "RMB'000" stripped a caption printing the straight
    quote and left one printing the typographic quote exactly as it was. Since a typographic quote is
    what a typeset filing actually prints, the rulebook's declared unit-and-currency annotation step
    was inert on the documents it exists for: "RMB’000" survived normalisation as a caption, and
    being ALL-CAPS with no colon it was then read as a section banner and scoped every row beneath
    it. Matching a character CLASS at the apostrophe's position is what makes the declared step do
    what it says.
    """
    for lit in literals:
        folded = _fold_for_match(lit)
        if not folded:
            continue
        # Rebuilt as a spacing-tolerant pattern rather than a plain replace: the printed caption
        # sets "RMB '000" and "人民幣 千元" with the space the alias does not carry.
        pat = r"\s*".join(f"[{re.escape(_APOSTROPHES)}]" if ch == "'" else re.escape(ch)
                          for ch in folded)
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)
    return text


_PIPELINE_IMPL: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nfkc", re.compile(r"\bnfkc\b", re.IGNORECASE)),
    ("t2s", re.compile(r"simplified", re.IGNORECASE)),
    ("case_fold", re.compile(r"case[\s-]?fold|lower\s?case", re.IGNORECASE)),
    ("width", re.compile(r"full-?width|half-?width", re.IGNORECASE)),
    ("footnote", re.compile(r"footnote", re.IGNORECASE)),
    ("numbering", re.compile(r"leading numbering|bullet", re.IGNORECASE)),
    ("trailing_colon", re.compile(r"trailing colon", re.IGNORECASE)),
    ("annotation", re.compile(r"unit and currency annotation", re.IGNORECASE)),
    ("whitespace", re.compile(r"whitespace|zero-width|soft hyphen", re.IGNORECASE)),
    # The wrapped-caption step is structural, not lexical: `_merge_wrapped_labels` reassembles the
    # caption from the printed lines before any of this runs, which is what the step asks for.
    ("wrapped_caption", re.compile(r"wrapped caption", re.IGNORECASE)),
)


def _pipeline_steps(norm: Normalisation | None,
                    scope: ScopeSelection | None = None) -> tuple[tuple[str, object], ...]:
    """The declared pipeline as (step id, callable) pairs, in the order the rulebook lists them.

    A declared step no implementation recognises is skipped rather than guessed at; an
    implementation whose step the rulebook drops stops running. Both directions matter — the
    rulebook is the specification, and this is the list of it that is actually in force.
    """
    if norm is None or not norm.pipeline:
        return ()
    unit_words = tuple(s for s in ((scope.units_and_currency.signals if scope else []) or [])
                       if s and s.strip())
    out: list[tuple[str, object]] = []
    for declared in norm.pipeline:
        for sid, pat in _PIPELINE_IMPL:
            if not pat.search(declared):
                continue
            examples = _quoted_examples(declared)
            if sid == "nfkc":
                out.append((sid, lambda t: unicodedata.normalize("NFKC", t)))
            elif sid == "t2s":
                out.append((sid, to_simplified))
            elif sid == "case_fold":
                out.append((sid, str.lower))
            elif sid == "width":
                out.append((sid, lambda t: t.translate(_WIDTH_MAP)))
            elif sid == "footnote":
                out.append((sid, lambda t, lits=examples: _drop_empty_brackets(
                    _TRAILING_PAREN_DIGITS.sub(
                        "", _SUPERSCRIPT.sub("", _NOTE_MARKER.sub(" ",
                                                                  _strip_literals(t, lits)))))))
            elif sid == "numbering":
                out.append((sid, lambda t: _LEADING_NUMBERING.sub("", t)))
            elif sid == "trailing_colon":
                out.append((sid, lambda t: _TRAILING_SUBCAPTION.sub("", t)))
            elif sid == "annotation":
                # The declared units_and_currency signals are the same annotations printed inline
                # over a column, so one list serves both jobs.
                lits = examples + unit_words
                out.append((sid, lambda t, lits=lits: _drop_empty_brackets(
                    _AUDIT_STATUS.sub(" ", _strip_literals(t, lits)))))
            elif sid == "whitespace":
                out.append((sid, lambda t: re.sub(r"\s+", " ", _ZERO_WIDTH.sub("", t))))
            else:                                # wrapped_caption — see _PIPELINE_IMPL
                out.append((sid, lambda t: t))
            break
    return tuple(out)


def apply_pipeline(text: str, steps: tuple[tuple[str, object], ...] = (), *,
                   exclude: tuple[str, ...] = ()) -> str:
    """Run the declared pipeline over a caption, skipping the ``exclude``d step ids.

    ``exclude`` exists for the two places where a step would destroy the very property being
    tested: ``case_fold`` erases the ALL-CAPS shape ``_looks_like_header`` reads, and
    ``trailing_colon`` erases the colon that separates a sub-heading from a section banner.
    """
    for sid, fn in steps:
        if sid in exclude:
            continue
        text = fn(text)                          # type: ignore[operator]
    return text.strip()


# Words that can only CONTINUE a caption, never open one. The mirror of ``_HEAD_INCOMPLETE``, which
# catches a wrapped caption's HEAD ("TOTAL COMPREHENSIVE" / "LOSS FOR THE YEAR"); this catches its
# TAIL. A statement title that wraps is the case that matters: "CONSOLIDATED STATEMENT OF PROFIT OR
# LOSS" / "AND OTHER COMPREHENSIVE INCOME" prints a second line which, read on its own, IS the other
# comprehensive income banner — so the title's leftovers scoped an entire income statement into OCI
# and re-homed its tax line. Nothing in a financial statement opens a section with "and".
_TAIL_CONTINUATION = re.compile(r"^\s*(and|or|及|与|與|和)\b", re.IGNORECASE)


def _looks_like_wrapped_tail(caption: str) -> bool:
    """Whether a normalised caption is the TAIL of a caption that wrapped, not a line of its own."""
    return bool(caption) and _TAIL_CONTINUATION.match(caption) is not None


def _is_units_caption(label_words: list[Word]) -> bool:
    """Whether a label-only row is the column-units caption rather than a section banner.

    A units caption is printed once per value column, so a two-period statement prints
    "RMB'000 RMB'000" and its bilingual twin "人民幣千元 人民幣千元". The banner branch drops a
    caption that NORMALISES AWAY, which catches the single form and not the repeated one — and the
    repeated one then scoped every row beneath it to a unit. Every word has to be a units token, so
    a genuine banner that merely mentions one ("TOTAL, in thousands") is untouched.
    """
    words = [w.text.strip() for w in label_words if w.text.strip()]
    return bool(words) and all(_UNITS_TOKEN.search(w) for w in words)


def _ends_with_colon(text: str, steps: tuple[tuple[str, object], ...]) -> bool:
    """Whether a caption ends in a colon, fullwidth one included.

    A bilingual filing writes "其他全面收益：" with the FULLWIDTH colon, which a bare
    ``endswith(":")`` does not see — so the sub-heading was taken for a section banner and
    displaced the section for every row beneath it. The rulebook's fullwidth→halfwidth step is
    what makes it count.
    """
    return apply_pipeline(text, steps, exclude=("case_fold", "trailing_colon")).endswith(":")


# ── scope_selection.entity_scope ─────────────────────────────────────────────────────────────
#
# Which side of a two-basis column header a declared signal names. ``entity_scope.signals`` is one
# flat list ("Group", "Consolidated", 本集團, 綜合, "Company", 本公司), so the side has to be read
# off the wording — but the VOCABULARY is the rulebook's: a signal it does not declare is not
# detected, and one it adds is.
_GROUP_WORDS = re.compile(r"group|consolidat|集團|集团|綜合|综合|合併|合并", re.IGNORECASE)
_COMPANY_WORDS = re.compile(r"company|standalone|separate|公司|單獨|单独", re.IGNORECASE)
# Read with the v2 signal list alone, a statement headed "Consolidated | Standalone" would lose
# its Company side ("Standalone"/"Separate" are not declared) and the both-or-nothing rule would
# then refuse the whole header. These are the two words the engine read before any rulebook
# declared one, kept as a compatibility vocabulary — not as a widening of it.
_LEGACY_SIGNALS = ("Consolidated", "Standalone", "Separate")


def _norm_signal(text: str) -> str:
    """A header cell reduced to its bare word, for an ANCHORED comparison against a signal."""
    t = unicodedata.normalize("NFKC", text).translate(_WIDTH_MAP).strip()
    return t.strip("()[]{}:;,.'\"“”‘’*†‡#-–—_ ").lower()


def _entity_signals(scope: ScopeSelection | None) -> tuple[tuple[Basis, str], ...]:
    declared = list((scope.entity_scope.signals if scope else []) or [])
    out: list[tuple[Basis, str]] = []
    for sig in declared + [s for s in _LEGACY_SIGNALS if s not in declared]:
        word = _norm_signal(sig)
        if not word:
            continue
        if _GROUP_WORDS.search(word):
            out.append((Basis.CONSOLIDATED, word))
        elif _COMPANY_WORDS.search(word):
            out.append((Basis.STANDALONE, word))
    return tuple(dict.fromkeys(out))


def _signal_side(token: str, signals: tuple[tuple[Basis, str], ...]) -> Basis | None:
    """The basis a header CELL names, or None.

    Anchored on the whole cell, and deliberately not sharing ``_CONSOL``/``_STANDALONE`` with
    ``_matrix_basis``: that one scans whole-page text, where "the Company" and "… Company
    Limited" appear constantly. Here the cell must BE the signal, so a running header and a note
    sentence about the Company cannot define a band.
    """
    word = _norm_signal(token)
    if not word:
        return None
    return next((b for b, s in signals if s == word), None)


def _is_basis_caption_row(label: str, vals: list,
                          signals: tuple[tuple[Basis, str], ...]) -> bool:
    """The two-basis BAND ROW itself — a caption made of nothing but basis signals, over "amounts"
    that are all date fragments.

    "Group" / "Company" is printed paragraph-tight above the year captions, so
    ``_merge_wrapped_labels`` folds the two lines together and the column header reaches the main
    loop as a row labelled "Group Company" carrying 2024, 2023, 2024, 2023. The row that DECLARES
    the banding is furniture; emitted as data it files the column YEARS as figures, and a year read
    as a figure is the worst kind of wrong number — 2,024 is a plausible amount, it sits in a real
    value column under a real basis, and no downstream total contradicts it.

    Recognised from the same declared ``entity_scope.signals`` that define the banding: EVERY token
    of the caption has to be one of them. That is what keeps a sentence naming both entities ("The
    Group and the Company had no material contingent liabilities") and a genuine line whose figures
    merely happen to be small ("Number of employees  28  25") reporting their values.
    """
    if not vals or not all(_is_date_ish(v) for v in vals):
        return False
    tokens = [t for t in re.split(r"[\s/|·、,，()（）:：]+", label) if t]
    if not tokens:
        return False
    return all(_signal_side(t, signals) is not None for t in tokens)


# The page classifier's own verdict on whose figures a page presents, as a basis.
#
# ``classify._scope_of`` resolves this from the page TITLE ("consolidated statement of financial
# position" vs "statement of financial position") and, for a face-titled page with no consolidation
# token, from whether the page sits after the notes — which is where an HKEX filing prints the
# Company's own statement. It is written to ``PageSource.scope`` and, until this mapping existed,
# read by nothing: the reconstructor was never told, so a Company-only page with no two-basis column
# header read as consolidated and its figures were ADDED to the Group's under the same keys.
#
# "mixed" is deliberately absent. A page captioned for both entities is exactly the case
# :func:`_basis_bands` exists for, and only the caption geometry can say which COLUMNS are whose; a
# page-wide basis would file half of them wrongly. A page whose scope is mixed but where no band
# survived the geometric guards keeps the consolidated default rather than guessing.
_PAGE_SCOPE_BASIS: dict[str, Basis] = {
    "consolidated": Basis.CONSOLIDATED,
    "company": Basis.STANDALONE,
}


def _company_only_stems(scope: ScopeSelection | None) -> tuple[tuple[str, ...], ...]:
    """Caption stems for each declared ``company_only_markers`` entry.

    The marker is declared as a canonical_key inside a sentence ("Presence of
    bs_non_current_assets__investments_in_subsidiaries on the face is strong evidence the column
    is company-only, since consolidation eliminates it"), so the stems come from the key's own
    tail: ``investments_in_subsidiaries`` → "investment" + "subsidiar".
    """
    stop = {"in", "of", "and", "to", "the", "for", "on", "at", "a"}
    out: list[tuple[str, ...]] = []
    for marker in ((scope.entity_scope.company_only_markers if scope else []) or []):
        for tail in re.findall(r"[a-z][a-z0-9_]*__([a-z0-9_]+)", marker):
            stems = []
            for tok in tail.split("_"):
                if tok in stop or len(tok) < 3:
                    continue
                stems.append(tok[:-3] if tok.endswith("ies") else tok.rstrip("s"))
            if stems:
                out.append(tuple(stems))
    return tuple(out)


def _names_company_only(label: str, stems: tuple[tuple[str, ...], ...]) -> bool:
    low = label.lower()
    return any(all(s in low for s in group) for group in stems)


# THE CAS FACE FORMAT'S OWN ENUMERATION of its top-level lines: 一、营业总收入, 二、营业总成本,
# 三、营业利润, 四、利润总额, 五、净利润, 六、其他综合收益的税后净额, 七、综合收益总额, 八、每股收益.
# A mainland statement numbers its statement-level lines and nothing else — a component is
# prefixed 其中： or 加： or 减：, or carries no prefix at all — so this is structural evidence of
# a total, not a phrase test on the wording.
#
# THE DISTINCTION MATTERS BECAUSE A CAPTION TEST WAS ALREADY REFUSED HERE, and rightly: a test
# broad enough to catch "Total current tax" also catches "Total return on funds", which is a
# detail line. An enumerated caption cannot be a detail line: the enumeration is the statement's
# spine and a filing prints at most eight of them.
#
# The negative lookahead for a digit is load-bearing. The same page prints its NOTE REFERENCES in
# the same form — 七、61, 七、70 — and one of those arrives as a row's whole label when the
# caption beside it is lost. A note reference is not a subtotal of anything.
_CAS_STATEMENT_LINE = re.compile(r"^[一二三四五六七八九十]+、\s*(?![0-9０-９])[^\s]")

_CONSOL = re.compile(r"consolidat", re.IGNORECASE)
_STANDALONE = re.compile(r"standalone|separate", re.IGNORECASE)
# No column header is printed in the lower half of a page. This bounds the region below when the
# page reports no figure at all (a page of prose), which is the only case the value block cannot.
_HDR_PAGE_FRACTION = 0.5
# Clear air between two column captions. Word spacing inside one phrase is an order of magnitude
# tighter, which is what separates a two-caption band row from a sentence naming both entities.
_CAPTION_GAP = 0.03


def _carries_amounts(row: list[Word], fmt=None) -> bool:
    """Whether a row reports a real figure (a year or a day-of-month is not one)."""
    for w in row:
        tok = w.text.strip()
        d = _num(tok, fmt)
        if d is not None and _is_money_like(tok, fmt) and not _is_date_ish(d):
            return True
    return False


def _header_region(rows: list[list[Word]], fmt=None) -> list[list[Word]]:
    """The rows that make up the statement's column-header band, bounded by GEOMETRY.

    A basis caption, a period caption, a units caption and a restatement marker are all parts of one
    printed thing — the header band over the value columns — so they share one bound. That bound
    used to be a count of eight printed rows, which is a bound on the wrong quantity: a bilingual
    filing stacks a running header in two languages, a statement title in two languages, a
    "for the year ended …" caption in two languages and a units line before it reaches the basis
    captions, which then land on row 9 and the Group|Company banding was lost — on exactly the
    filings this corpus is made of.

    The geometry that DOES bound the band is the value block: every column caption is printed ABOVE
    the first figure of the statement (the first row is included, because a units caption is
    sometimes set on it). The page fraction is the fallback bound for a page that reports no figure
    at all, where "above the figures" says nothing. Widening the region cannot loosen detection:
    every guard in :func:`_basis_bands` is geometric and each one is a veto.

    THE PAGE FRACTION IS THE FALLBACK, NOT A SECOND BOUND, and applying it as both discarded real
    headers. A mainland annual report prints several notes to a page — 688008's page 219 carries
    notes 59, 60 and 61 — and note 61's own header band sits at y=0.68/0.70, so every caption over
    its columns was thrown away by a 0.5 cut-off while "above this table's first figure" had
    already bounded the band correctly. What was lost is the two-level grid: the note prints
    本期发生额 | 上期发生额 over 收入 | 成本 | 收入 | 成本, and with the header invisible the four
    columns fell back to positional labels — so the CURRENT period's cost was published as the
    PRIOR period's revenue. Sales (Revenues) read 1,516,811,244.12 for 2023 against a printed
    2,278,141,066.50, and the figure it took is this year's cost of sales.
    """
    first = next((i for i, r in enumerate(rows) if _carries_amounts(r, fmt)), None)
    if first is not None:
        return rows[:first + 1]
    # No figure anywhere, so "above the figures" says nothing and the page fraction is all there is.
    return [r for r in rows if _row_box(r).y0 <= _HDR_PAGE_FRACTION]


def _value_area(value_bands: list[float],
                col_xs: list[list[tuple[float, str]]]) -> tuple[float, float] | None:
    """The horizontal extent of the page's figures, from the detected columns when there are any
    and from the figures themselves when the page is too sparse to have columns."""
    xs = list(value_bands) or sorted(x for row in col_xs for x, _ in row)
    return (xs[0], xs[-1]) if len(xs) >= 2 else None


def _over_value_columns(xc: float, area: tuple[float, float] | None) -> bool:
    """Whether a caption stands OVER the figures it would band.

    Prose mentioning the Group and the Company sits in the label column; a column caption sits
    above the numbers. Without this test one sentence on a notes page defines the page's bands.
    """
    if area is None:
        return False
    lo, hi = area
    pad = max(0.05, (hi - lo) * 0.25)
    return lo - pad <= xc <= hi + pad


def _nearest_col(x: float, value_bands: list[float]) -> int | None:
    if not value_bands:
        return None
    return min(range(len(value_bands)), key=lambda i: abs(value_bands[i] - x))


def _basis_bands(rows: list[list[Word]], value_bands: list[float] | None = None,
                 area: tuple[float, float] | None = None, *,
                 signals: tuple[tuple[Basis, str], ...] = (), fmt=None, log=None,
                 page_index: int | None = None) -> list[tuple[Basis, float]]:
    """Detect a two-basis column header (Group | Company, Consolidated | Standalone) and return
    each basis caption's horizontal centre, so value columns can be attributed to a basis.

    Every guard here answers a way the previous version got it wrong, and each of them is why the
    result is empty (single-basis, everything consolidated) rather than approximate:

    * It scanned EVERY row, so any prose row mentioning one of the words could define the bands —
      on a notes page "The Group and the Company had no material contingent liabilities" is such
      a row. Bounded now to the header REGION (see :func:`_header_region`), which narrows the
      existing Consolidated/Standalone detection too: that is the intent, a basis caption is part
      of the column header.
    * It ran on the MERGED rows, where a wrapped basis caption has been glued onto the period
      line — the geometry the tests below rely on then describes the merged block, not the print.
      The caller passes the unmerged rows.
    * ``_basis_for`` returns the NEAREST band, so ONE stray caption relabels a whole page. Both
      sides must be present (both-or-nothing) and each must sit over the value columns.
    * A false positive does not merely mislabel: it SPLITS a two-column comparative, so last
      year's figures are read as this year's for another entity. Corrupting the periods is worse
      than leaving the Group/Company gap open, so every test is a veto.
    """
    signals = signals or _entity_signals(None)
    for row in _header_region(rows, fmt):
        # A band row captions columns; a row that also reports an amount is a statement line, or
        # prose citing one.
        if _carries_amounts(row, fmt):
            continue
        hits: list[tuple[Basis, float, int]] = []
        for ri, run in enumerate(_x_runs(row, _CAPTION_GAP)):
            for w in run:
                side = _signal_side(w.text, signals)
                if side is not None and _over_value_columns(_xc(w), area):
                    hits.append((side, _xc(w), ri))
        if len({b for b, _, _ in hits}) < 2:
            continue                              # both-or-nothing
        group = [(x, ri) for b, x, ri in hits if b is Basis.CONSOLIDATED]
        company = [(x, ri) for b, x, ri in hits if b is Basis.STANDALONE]
        if {ri for _, ri in group} & {ri for _, ri in company}:
            # One contiguous phrase naming both ("The Group and the Company had no…") is a
            # sentence. Two captions stand apart, each over its own columns.
            continue
        cols_g = {_nearest_col(x, value_bands or []) for x, _ in group}
        cols_c = {_nearest_col(x, value_bands or []) for x, _ in company}
        if value_bands and cols_g & cols_c:
            continue                              # both captions over one column: nothing to split
        if not value_bands and min(abs(gx - cx) for gx, _ in group for cx, _ in company) < 0.08:
            continue                              # too close together to be separate columns
        bands = [(b, x) for b, x, _ in hits]
        if log:
            named = ",".join(f"{b.value}@{x:.2f}" for b, x in bands)
            log(f"extract:page={page_index}:entity_scope=two_basis_header({named})")
        return bands
    return []


def _basis_for(x: float, bands: list[tuple[Basis, float]]) -> Basis:
    """The basis nearest to a figure that sits under no detected column — the fallback. Columns
    themselves are assigned by :func:`_basis_of_columns`, which does not use distance alone."""
    if not bands:
        return Basis.CONSOLIDATED
    return min(bands, key=lambda b: abs(b[1] - x))[0]


def _basis_of_columns(value_bands: list[float],
                      bands: list[tuple[Basis, float]]) -> dict[int, Basis]:
    """Which basis each value column belongs to.

    A basis caption governs a CONTIGUOUS run of columns — that is what a band is — and it may be
    anchored anywhere over that run: left-aligned in one filing, centred in another. Nearest-
    caption assignment therefore breaks on the middle columns of a four-column page: with "Group"
    printed over its first column and "Company" over its own, the Group's comparative comes out
    0.001 nearer the Company caption, and last year's Group figures are read as the Company's
    current year. Equal runs when the columns divide evenly by the number of captions — a
    two-basis comparative prints the same periods for each entity — and otherwise the nearest
    caption, made monotonic so the runs stay contiguous.
    """
    if not value_bands:
        return {}
    if not bands:
        return {i: Basis.CONSOLIDATED for i in range(len(value_bands))}
    ordered = [b for b, _ in sorted(bands, key=lambda b: b[1])]
    # A basis can be captioned twice ("Group" and "Consolidated" over the same pair); dedupe
    # keeping printed order, or the run count would exceed the number of bands.
    ordered = list(dict.fromkeys(ordered))
    n, k = len(value_bands), len(ordered)
    if k == 1:
        return {i: ordered[0] for i in range(n)}
    if n % k == 0:
        width = n // k
        return {i: ordered[min(i // width, k - 1)] for i in range(n)}
    centres = sorted({b[1] for b in bands})
    out: dict[int, Basis] = {}
    seen = 0
    for i, x in enumerate(value_bands):
        idx = min(range(len(centres)), key=lambda j: abs(centres[j] - x))
        seen = max(seen, min(idx, k - 1))
        out[i] = ordered[seen]
    return out


# Value columns are printed on a tight vertical alignment — every figure in the current-period
# column shares an x-centre to within a fraction of the column gap. This is the width within
# which two figures are taken to be in the same column.
#
# It is measured on x-CENTRES, and a centre drifts with the width of the number printed in a
# right-aligned column: "1,204,500" and "980" in one column are ~0.025 of the page apart. So the
# tolerance has to be wider than that drift and narrower than the gap between two comparative
# columns (~0.12 of the page). The matrix path clusters right EDGES instead, which do not drift, and
# its columns are pitched several times closer together — so it needs its own, much tighter value
# (`_MATRIX_COL_TOL`). Both used to be spelled `_COL_TOL`: the matrix definition came second and
# silently governed this path too, at 0.012, where a right-aligned column of mixed-width figures
# fragments into clusters too small to survive `_COL_MIN_ROWS` — the page then had no columns at
# all and every row fell back to positional order, which is the mis-load `_value_column_bands`
# exists to prevent.
_COL_TOL = 0.035
# A column has to be used by several rows to be a column at all, so a stray figure in a footnote
# or a page number never becomes one.
_COL_MIN_ROWS = 3


def _value_column_bands(value_xs: list[list[tuple[float, str]]]) -> list[float]:
    """The x-centres of the statement's value columns, from the figures on the page.

    A row's period CANNOT be taken from the order of its own values. When a filing reports a line
    in one period only — "Pledged deposits" with a prior-year figure and no current one — the
    single figure sits under the PRIOR column, and reading it as "the first value, therefore
    current" files real money against the wrong year. It is silent: the row looks fine, and only
    the section subtotal reveals it, over-stating one period and under-stating the other by the
    same amount.

    A note-reference column is excluded, because it is not a period. Statements print note refs
    in their own narrow column, and where enough rows carry one it aligns as tightly as any money
    column — so taken as column 0 it makes every real figure on the page one period too late, and
    a whole page of current-year figures is filed against the prior year. What distinguishes it is
    its contents: bare 1–2 digit integers, never formatted amounts.

    Returns [] when the page has no columnar structure to speak of, and the caller falls back to
    positional order.
    """
    flat = sorted((x, t) for xs in value_xs for x, t in xs)
    if not flat:
        return []
    clusters: list[list[tuple[float, str]]] = [[flat[0]]]
    for item in flat[1:]:
        if item[0] - clusters[-1][-1][0] <= _COL_TOL:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    def is_note_column(cluster: list[tuple[float, str]]) -> bool:
        notes = sum(1 for _, t in cluster if _is_note_number(t))
        return notes >= max(2, int(0.8 * len(cluster)))

    kept = [c for c in clusters if len(c) >= _COL_MIN_ROWS and not is_note_column(c)]
    if len(kept) < 2:
        return []                       # nothing to disambiguate; order is as good as position
    return [sorted(x for x, _ in c)[len(c) // 2] for c in kept]   # median resists one outlier


def _column_index(x: float, bands: list[float]) -> int | None:
    """Which value column a figure sits in, or None when it is nowhere near one."""
    if not bands:
        return None
    idx = min(range(len(bands)), key=lambda i: abs(bands[i] - x))
    return idx if abs(bands[idx] - x) <= _COL_TOL * 2 else None


def _detect_note_column(rows: list[list[Word]]) -> float | None:
    """The x-centre of the note-reference column, if the statement has one. Found from a
    'Notes'/'附註' column header that is BACKED by a vertical run of bare note numbers beneath
    it (≥2), so an inline 'Note 14' mention in prose isn't mistaken for a column. Returns None
    when there's no such column (then a per-row heuristic handles a leading note number)."""
    header_xs: list[float] = []
    for row in rows:
        for w in row:
            if _NOTE_HDR.match(w.text.strip()):
                header_xs.append((w.bbox.x0 + w.bbox.x1) / 2)
    best, best_n = None, 0
    for cx in header_xs:
        n = 0
        for row in rows:
            if any(abs((w.bbox.x0 + w.bbox.x1) / 2 - cx) <= 0.03 and _is_note_number(w.text)
                   for w in row):
                n += 1
        if n > best_n:
            best, best_n = cx, n
    return best if best_n >= 2 else None


def _resolve_note_column(note_ref: str | None, value_words: list[Word],
                         note_x: float | None, fmt=None) -> tuple[str | None, list[Word]]:
    """Separate the note-reference cell from the monetary values. When a note column was detected,
    a value token sitting in it (and shaped like a note number) is the reference. Otherwise, a
    leading bare 1–2 digit integer followed by a real amount is treated as the note ref — the
    common ``Revenue  6  45,230  40,110`` layout, where '6' is Note 6, not the current-year value."""
    if note_ref is not None or not value_words:
        return note_ref, value_words
    if note_x is not None:
        kept: list[Word] = []
        for vw in value_words:
            xc = (vw.bbox.x0 + vw.bbox.x1) / 2
            if note_ref is None and abs(xc - note_x) <= 0.03 and _is_note_number(vw.text):
                note_ref = _note_ref_value(vw.text)
            else:
                kept.append(vw)
        return note_ref, kept
    if (len(value_words) >= 2 and _is_note_number(value_words[0].text)
            and any(_is_money_like(w.text, fmt) for w in value_words[1:])):
        return _note_ref_value(value_words[0].text), value_words[1:]
    return note_ref, value_words


_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
           r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
# A CJK date, written without spaces: 二零二三年, 十二月三十一日, 二零二三年十二月三十一日.
_CJK_DATE = r"(?:[〇零一二三四五六七八九十]{1,4}[年月日])+"
# A single word that can belong to a date-column header phrase.
_DATEISH_WORD = re.compile(
    rf"^(?:{_MONTHS}|{_CJK_DATE}|{_MIXED_DATE}|\d{{1,4}}(?:st|nd|rd|th)?|"
    r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|"
    r"as|at|year|years?|period|ended|ending|for|the|fy|q[1-4]|h[12]|,)$", re.IGNORECASE)
# A phrase only counts as a period header if it actually carries a year or month name. The CJK
# year belongs here too: without it the comparative columns of a Chinese filing carried no
# heading at all, so which of them was the current period fell back to position — and a filing
# printing the comparative first had every figure read a year out.
_DATE_PHRASE = re.compile(rf"(?:19|20)\d{{2}}|{_MONTHS}|[〇零一二三四五六七八九十]{{2,4}}年",
                          re.IGNORECASE)


def _period_bands(rows: list[list[Word]], fmt=None) -> list[tuple[str, float]]:
    """Detect date-like column headers (e.g. '31 March 2025' / '2024' / 'FY2025') in the statement's
    header band and return each period phrase's (label, x-centre). Used to give value columns a real
    period-end DATE for display; empty when no dated header is found (native PDFs without a parsable
    header fall back to positional Current/Prior).

    Bounded by the same header region the basis captions are, because they are parts of one printed
    band: a tall bilingual header pushed the year captions past a fixed eight-row bound, and the
    periods then fell back to column position on the filings that most need the date read.
    """
    best: list[tuple[str, float]] = []
    for row in _header_region(rows, fmt):
        phrases: list[list[Word]] = []
        run: list[Word] = []
        for w in row:
            dateish = bool(_DATEISH_WORD.match(w.text.strip(" .")))
            # A wide horizontal gap means a new column — flush the current phrase even between
            # two date-ish words (e.g. "…2025    31 March 2024" are two separate headers).
            gap = run and (w.bbox.x0 - run[-1].bbox.x1) > 0.05
            if dateish and not gap:
                run.append(w)
            else:
                if run:
                    phrases.append(run); run = []
                if dateish:
                    run.append(w)
        if run:
            phrases.append(run)
        bands: list[tuple[str, float]] = []
        for ph in phrases:
            text = " ".join(w.text for w in ph).strip(" ,.")
            if _DATE_PHRASE.search(text):         # keep only phrases with a real year/month
                xc = sum((w.bbox.x0 + w.bbox.x1) / 2 for w in ph) / len(ph)
                bands.append((text, xc))
        if len(bands) > len(best):
            best = bands
        if len(bands) >= 2:                        # a two-column header is a confident match
            break
    return best


def _period_for(x: float, bands: list[tuple[str, float]]) -> str | None:
    """The detected period label whose column is nearest this value's x-centre, or None."""
    if not bands:
        return None
    return min(bands, key=lambda b: abs(b[1] - x))[0]


# ── THE TWO-LEVEL COLUMN GRID: a period band over a measure band ─────────────────────────────
#
# A PRC note's column header is TWO printed rows, not one, and both of them name something:
#
#     项目    |      本期发生额      |      上期发生额          ← the PERIOD band
#             |   收入   |   成本   |   收入   |   成本        ← the MEASURE band
#
# Everything above this point models exactly two axes — basis x period — and labels a period by
# POSITION unless every column carries a parseable DATE (`_DATE_PHRASE`, `_period_date`). Neither
# half of that header carries a date, so on the filing this was measured against (Sun Create
# Electronics, 11077098, 210pp, locale=zh) note 61 came out as four positional columns:
#
#     current = 1,589,859,743.31   ← 本期 收入, correct by luck
#     prior   = 1,389,417,976.40   ← 本期 成本. A COST PUBLISHED AS A REVENUE.
#     col2    = 1,920,773,532.54   ← 上期 收入, unreachable by anything reading "prior"
#     col3    = 1,590,200,224.12   ← 上期 成本
#
# `is_pl__sales_revenues` therefore served this year's COST as last year's revenue, and the same
# shape in the related-party note 6(1) (期末余额{账面余额|坏账准备} / 期初余额{...}) put a bad-debt
# PROVISION in the "prior" slot, so no service could compute gross-minus-provision for one period.
#
# THE MEASURE IS EXPRESSED AS A SUFFIX ON THE PERIOD LABEL, the way this file already spells a
# variant of a period ("current_restated", f"{base}_col{c}" below). `ValueKey` gains no dimension:
#
#   * the PRIMARY measure of a period keeps the BARE label — "current" / "prior". Primary is the
#     amount itself (收入, 账面余额, 金额, 期末余额), so `periods.split_current_prior` and every
#     consumer that reads "current"/"prior" receives the RIGHT column with no edit at all;
#   * every OTHER measure is addressable as "<period>:<slug>" — "current:cost",
#     "current:allowance", "current:ratio" — so a service that needs the second measure can now
#     ask for it by name.
#
# A cost can never again occupy a revenue slot: the two now have different keys.

# The standard DATELESS PRC period captions, and which slot each one is: 0 is the period being
# reported, 1 is the comparative. Slot comes from the CAPTION'S OWN SEMANTICS and never from
# x-position, for the same reason `_column_periods` prefers the heading date over position — a
# filing may print the comparative on the left, and then position files every figure a year out.
#
# Longest first, because the test is containment: 本期发生额 has to be decided before 本期, and
# 上年同期数 before 上年. Both scripts are listed rather than folded through `to_simplified`,
# which is a no-op when the opencc converter is not installed.
_PRC_PERIOD_CAPTIONS: tuple[tuple[str, int], ...] = (
    ("上年同期数", 1), ("上期发生额", 1), ("上期發生額", 1),
    ("本期发生额", 0), ("本期發生額", 0),
    ("期末余额", 0), ("期末餘額", 0), ("期初余额", 1), ("期初餘額", 1),
    ("上年同期", 1), ("本期数", 0), ("本年数", 0),
    ("本期", 0), ("上期", 1), ("本年", 0), ("上年", 1),
    ("期末", 0), ("期初", 1), ("年末", 0), ("年初", 1),
)

# The measure captions printed in the band UNDER a period caption, and the slug each one takes.
# An empty slug means PRIMARY — the amount itself, which keeps the bare period label.
_MEASURE_CAPTIONS: tuple[tuple[str, str], ...] = (
    ("账面余额", ""), ("賬面餘額", ""), ("帳面餘額", ""),
    ("坏账准备", "allowance"), ("壞賬準備", "allowance"), ("壞帳準備", "allowance"),
    ("期末余额", ""), ("期末餘額", ""),
    ("收入", ""), ("收益", ""), ("金额", ""), ("金額", ""),
    ("成本", "cost"),
    ("比例", "ratio"), ("占比", "ratio"), ("佔比", "ratio"),
    ("数量", "quantity"), ("數量", "quantity"),
)


def _caption_text(run: list[Word]) -> str:
    """One header phrase, folded for matching against the caption tables above."""
    return unicodedata.normalize("NFKC", "".join(w.text for w in run)).strip(" ,.:;（）()")


def _prc_period_slot(text: str) -> int | None:
    """Which period a dateless PRC caption names — 0 reported, 1 comparative — or None."""
    for caption, slot in _PRC_PERIOD_CAPTIONS:
        if caption in text:
            return slot
    return None


def _measure_slug(text: str) -> str | None:
    """The measure a caption names: "" for the primary amount, a slug otherwise, None for a
    caption that names no measure this reader knows."""
    for caption, slug in _MEASURE_CAPTIONS:
        if caption in text:
            return slug
    return None


@dataclass
class ColumnGrid:
    """A page's value columns read as PERIOD x MEASURE rather than period alone.

    ``slot_of`` is the period slot (0 reported, 1 comparative) and ``measure_of`` the measure slug
    ("" = primary) per value column; ``captions`` carries what was actually printed over each
    column, for the log line and for ``period_display``. ``columns`` is the column count the grid
    was read against, so a continuation page whose figures cluster into a different number of
    columns cannot inherit it (see ``build_line_items(column_grid=…)``).
    """
    columns: int
    slot_of: dict[int, int]
    measure_of: dict[int, str]
    captions: dict[int, tuple[str, str]]

    @property
    def two_level(self) -> bool:
        """Whether a MEASURE band was actually read. False for the plain dateless comparative
        (`本期发生额 | 上期发生额` over two columns), which gains a period ORDER from its captions
        and nothing else — there is no interpretation for a reviewer to be told about."""
        return any(self.measure_of.values())

    def describe(self) -> str:
        return ",".join(f"{self.captions[c][0]}/{self.captions[c][1] or 'primary'}"
                        for c in sorted(self.captions))


def _runs_of_columns(value_bands: list[float], centres: list[float]) -> dict[int, int]:
    """Which caption each value column belongs to, by the same rules as :func:`_basis_of_columns`.

    A band caption governs a CONTIGUOUS run of columns and may be anchored anywhere over it, so
    nearest-caption assignment breaks on the middle columns of a four-column page — which is
    exactly the shape a two-level PRC header has. Equal runs when the columns divide evenly by the
    number of captions (a period x measure grid prints the same measures under each period), and
    otherwise the nearest caption made monotonic so the runs stay contiguous.
    """
    n, k = len(value_bands), len(centres)
    if not n or not k:
        return {}
    if k == 1:
        return {i: 0 for i in range(n)}
    ordered = sorted(range(k), key=lambda j: centres[j])
    if n % k == 0:
        width = n // k
        return {i: ordered[min(i // width, k - 1)] for i in range(n)}
    out: dict[int, int] = {}
    seen = 0
    for i, x in enumerate(value_bands):
        j = min(range(k), key=lambda t: abs(centres[t] - x))
        seen = max(seen, min(ordered.index(j), k - 1))
        out[i] = ordered[seen]
    return out


def _prc_period_row(rows: list[list[Word]], value_bands: list[float],
                    area: tuple[float, float] | None, fmt=None
                    ) -> tuple[int, list[tuple[str, int, float]]] | None:
    """The header row that captions the value columns with dateless PRC period captions, as
    ``(index within the header region, [(caption, slot, x-centre), …])``.

    ACCEPTED ONLY AS A CLEAN TWO-SLOT PARTITION: exactly the reported period and its comparative,
    one caption each. A movement schedule captions its columns 期初余额 | 本期增加 | 本期减少 |
    期末余额 on ONE row — three of those contain 本期/期末 and would land in slot 0 together —
    and there the printed order is the only thing that means anything, so it keeps the positional
    reading it has today rather than being forced into two periods it does not have.
    """
    region = _header_region(rows, fmt)
    for idx, row in enumerate(region):
        if _carries_amounts(row, fmt):
            continue
        hits: list[tuple[str, int, float]] = []
        for run in _x_runs(row, _CAPTION_GAP):
            xc = sum(_xc(w) for w in run) / len(run)
            if not _over_value_columns(xc, area):
                continue
            text = _caption_text(run)
            slot = _prc_period_slot(text)
            if slot is not None:
                hits.append((text, slot, xc))
        if len(hits) != 2 or {s for _, s, _ in hits} != {0, 1}:
            continue
        return idx, hits
    return None


def _measure_band(region: list[list[Word]], after: int, value_bands: list[float],
                  area: tuple[float, float] | None, fmt=None) -> dict[int, tuple[str, str]] | None:
    """The measure caption per value column, read from the header row(s) BELOW the period band.

    Only the next two printed rows are considered: the measure band is the second line of one
    printed header block, and looking further down reaches the note's own first data row.
    """
    for row in region[after + 1:after + 3]:
        if _carries_amounts(row, fmt):
            continue
        found: dict[int, tuple[str, str]] = {}
        for run in _x_runs(row, _CAPTION_GAP):
            xc = sum(_xc(w) for w in run) / len(run)
            if not _over_value_columns(xc, area):
                continue
            text = _caption_text(run)
            slug = _measure_slug(text)
            if slug is None:
                continue
            col = _nearest_col(xc, value_bands)
            # Within a column's own width: a measure caption is printed over the figures it
            # names, and a caption that lands between two columns names neither.
            if col is not None and abs(value_bands[col] - xc) <= 0.06 and col not in found:
                found[col] = (text, slug)
        if found:
            return found
    return None


def _period_measure_grid(rows: list[list[Word]], value_bands: list[float],
                         area: tuple[float, float] | None, fmt=None) -> ColumnGrid | None:
    """Read the page's value columns as PERIOD x MEASURE, or None to keep today's behaviour.

    Every condition below is a veto, because the failure direction matters: a missed grid leaves
    the positional reading that is already there, while a false grid would relabel a column that
    is genuinely a period as a measure of another one — and then two periods' figures share a key.

    A PERIOD SPAN WITH ONE COLUMN KEEPS TODAY'S BEHAVIOUR EXACTLY, save for one thing it can only
    gain: `项目 | 本期发生额 | 上期发生额` over two columns is an ordinary comparative, and the
    grid then carries the period ORDER its captions state and no measure at all. That is the half
    of `period_selection` a dateless filing had no way to express — a note printing the
    comparative first read every figure a year out, and nothing downstream could see it.
    """
    if not value_bands:
        return None
    found = _prc_period_row(rows, value_bands, area, fmt)
    if found is None:
        return None
    idx, hits = found
    region = _header_region(rows, fmt)
    slot_by_caption = _runs_of_columns(value_bands, [x for _, _, x in hits])
    if not slot_by_caption:
        return None
    spans: dict[int, list[int]] = {}
    for col, which in slot_by_caption.items():
        spans.setdefault(which, []).append(col)
    if not any(len(cols) > 1 for cols in spans.values()):
        # One column per period caption: a plain comparative. Order from the captions, no measures.
        return ColumnGrid(
            columns=len(value_bands),
            slot_of={c: hits[which][1] for c, which in slot_by_caption.items()},
            measure_of={c: "" for c in slot_by_caption},
            captions={c: (hits[which][0], "") for c, which in slot_by_caption.items()},
        )
    measures = _measure_band(region, idx, value_bands, area, fmt)
    if measures is None:
        return None
    for which, cols in spans.items():
        if len(cols) == 1:
            continue
        # EVERY column of a multi-column span must be captioned, and exactly one of them must be
        # the primary amount. A span with two primaries has nothing to give the bare period label
        # to, and a span with an unread column would silently keep a positional label beside two
        # suffixed ones — a worse mixture than the positional reading it replaced.
        if any(c not in measures for c in cols):
            return None
        if sum(1 for c in cols if measures[c][1] == "") != 1:
            return None
    return ColumnGrid(
        columns=len(value_bands),
        slot_of={c: hits[which][1] for c, which in slot_by_caption.items()},
        measure_of={c: (measures[c][1] if c in measures else "")
                    for c in slot_by_caption},
        captions={c: (hits[which][0], measures[c][0] if c in measures else "")
                  for c, which in slot_by_caption.items()},
    )


# The flag a value carries when its column was read from a two-level header. Raised on
# ``ExtractedValue.confidence.flags`` — the mechanism `stages/confidence.py` already uses for
# ``balance_mismatch`` / ``note_untied`` and that ``_serialize_rows`` already serves per value —
# so a reviewer is told the column was INTERPRETED from a period band over a measure band rather
# than read straight off one caption.
GRID_FLAG = "column_grid:period_x_measure"


# ── scope_selection.period_selection ─────────────────────────────────────────────────────────
#
# "Identify the current period from the column heading date, not from column position; HKEX
# filings are not consistently current-first." Reading position instead files every figure a year
# out on any filing that prints the comparative on the left — and nothing downstream can see it,
# because each column is internally consistent.
_CJK_DIGIT = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CJK_NUM = re.compile(r"^[〇零一二三四五六七八九十]{1,4}$")
_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun",
                "jul", "aug", "sep", "oct", "nov", "dec")


def _cjk_number(text: str) -> int | None:
    """A CJK numeral as an integer: 二零二三 → 2023, 十二 → 12, 三十一 → 31."""
    if not _CJK_NUM.match(text):
        return None
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CJK_DIGIT.get(left, 1) if left else 1
        ones = _CJK_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    out = 0
    for ch in text:
        out = out * 10 + _CJK_DIGIT[ch]
    return out


def _period_date(text: str) -> tuple[int, int, int] | None:
    """``(year, month, day)`` read from a column heading, or None when it names no year.

    Month and day default to 0 so "2024" ranks below "31 December 2024" only when the two are
    genuinely different periods; what matters is that the ORDER is the printed dates' order.
    """
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text)
    year = month = day = 0
    m = re.search(r"(?:19|20)\d{2}", t)
    if m:
        year = int(m.group(0))
    else:
        m = re.search(r"([〇零一二三四五六七八九十]{2,4})年", t)
        if m:
            year = _cjk_number(m.group(1)) or 0
    if not year:
        return None
    mn = re.search(r"([〇零一二三四五六七八九十]{1,3})月", t)
    if mn:
        month = _cjk_number(mn.group(1)) or 0
    else:
        low = t.lower()
        for i, name in enumerate(_MONTH_NAMES, start=1):
            if name in low:
                month = i
                break
    # The mixed form: a bilingual filing writes "2024年12月31日" with Arabic digits under the CJK
    # suffixes, so the month and day are there to be read even though neither the CJK numeral nor
    # the English month name is.
    if not month:
        am = re.search(r"(\d{1,2})\s*月", t)
        if am:
            month = int(am.group(1))
    dn = re.search(r"([〇零一二三四五六七八九十]{1,3})日", t)
    ad = re.search(r"(\d{1,2})\s*日", t)
    if dn:
        day = _cjk_number(dn.group(1)) or 0
    elif ad:
        day = int(ad.group(1))
    else:
        dm = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?=[a-z])", t, re.IGNORECASE)
        if dm:
            day = int(dm.group(1))
    # A numeric date ("31/12/2024", "2024-12-31") carries month and day in its own separators.
    nd = re.search(r"\b(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})\b", t)
    if nd and not month:
        a, b, c = (int(g) for g in nd.groups())
        if a > 31:                     # y-m-d
            month, day = b, c
        else:                          # d-m-y
            month, day = b, a
    return year, month, day


def _restated_markers(scope: ScopeSelection | None) -> tuple[str, ...]:
    """The captions ``restatement_rule`` says mark a restated comparative.

    Harvested from the declared sentence ("Where a comparative column is labelled restated / 重列
    / 經重列, load it as the comparative and set restated: true") rather than listed here, so a
    filing spelling it a fourth way is closed by editing the rulebook. Nothing is assumed when the
    sentence names nothing: restatement handling then simply does not apply.
    """
    rule = (scope.period_selection.restatement_rule if scope else "") or ""
    m = re.search(r"labelled\s+([^,.;]+)", rule, re.IGNORECASE)
    if not m:
        return ()
    return tuple(t for t in (p.strip().lower() for p in m.group(1).split("/")) if t)


def _is_restated(head: str, markers: tuple[str, ...]) -> bool:
    low = to_simplified(unicodedata.normalize("NFKC", head or "")).lower()
    return any(to_simplified(mk) in low for mk in markers)


def _restated_columns(rows: list[list[Word]], value_bands: list[float],
                      markers: tuple[str, ...], fmt=None) -> set[int]:
    """Value columns whose header carries a restatement marker.

    Read from the header region directly rather than from ``_period_bands``, because the marker is
    not a date: "(restated)" / "經重列" is printed on its own line under the year, so the period
    phrase never contains it and the column looked like an ordinary comparative.
    """
    out: set[int] = set()
    if not markers or not value_bands:
        return out
    for row in _header_region(rows, fmt):
        for w in row:
            if not _is_restated(_norm_signal(w.text), markers):
                continue
            col = _nearest_col(_xc(w), value_bands)
            if col is not None and abs(value_bands[col] - _xc(w)) <= 0.06:
                out.add(col)
    return out


def _column_periods(basis_cols: dict[Basis, list[int]], value_bands: list[float],
                    period_bands: list[tuple[str, float]], *,
                    restated: tuple[str, ...] = (), restated_cols: set[int] | None = None,
                    grid: ColumnGrid | None = None,
                    log=None, page_index: int | None = None) -> dict[tuple[Basis, int], str]:
    """The period label for every (basis, value column).

    Ordering is by the column HEADING DATE when every column of a basis carries one, then by the
    dateless PRC period caption when the header states one (``grid``), and by printed position
    otherwise — a page whose header cannot be read offers nothing better, and guessing would be
    the mistake this exists to prevent.

    ``grid`` also carries the MEASURE band, and a non-primary measure becomes a SUFFIX on the
    period label: "current:cost", "current:allowance". See :class:`ColumnGrid` for why the suffix
    rather than a new dimension on ``ValueKey``, and for the cost figure that was published as a
    revenue until it existed.

    ``restatement_rule``: a comparative headed "(restated)" loads as the comparative. When the
    original is printed BESIDE it the two share a slot, and the restated column takes a
    distinguished label instead of overwriting the original — the rulebook's "keep both with a
    restatement flag", within a ``ValueKey`` that has nowhere to put the flag itself.
    """
    out: dict[tuple[Basis, int], str] = {}
    for basis, cols in basis_cols.items():
        heads = {c: (_period_for(value_bands[c], period_bands) or "") for c in cols}
        dates = {c: _period_date(heads[c]) for c in cols}
        flags = {c: _is_restated(heads[c], restated) or c in (restated_cols or set())
                 for c in cols}
        groups = sorted({d for d in dates.values() if d}, reverse=True)
        by_date = len(groups) > 1 and all(dates[c] for c in cols)
        # The measure suffix per column; empty for every column that is a primary amount, and for
        # every page with no grid at all.
        suffix = {c: "" for c in cols}
        if by_date:
            slot_of = {c: groups.index(dates[c]) for c in cols}
            order = sorted(cols, key=lambda c: (slot_of[c], flags[c]))
            if order != sorted(cols) and log:
                log(f"extract:page={page_index}:period_selection=by_heading_date"
                    f"({'|'.join(heads[c] for c in order)})")
        elif grid is not None and all(c in grid.slot_of for c in cols):
            # The caption's own semantics, not the column's x-position: a filing may print the
            # comparative on the left, and 本期/上期 says which is which where no date does.
            slot_of = {c: grid.slot_of[c] for c in cols}
            suffix = {c: grid.measure_of.get(c, "") for c in cols}
            # Sorted so the PRIMARY measure of a slot is labelled first and therefore keeps the
            # bare "current"/"prior" that every existing consumer reads.
            order = sorted(cols, key=lambda c: (slot_of[c], suffix[c] != "", flags[c], c))
        else:
            slot_of = {c: i for i, c in enumerate(sorted(cols))}
            order = sorted(cols, key=lambda c: (slot_of[c], flags[c]))
        used: set[str] = set()
        for c in order:
            i = slot_of[c]
            base = "current" if i == 0 else "prior" if i == 1 else f"col{i}"
            label = f"{base}:{suffix[c]}" if suffix[c] else base
            if label in used:
                label = f"{base}_restated" if flags[c] else f"{base}_col{c}"
                if log:
                    log(f"extract:page={page_index}:period_selection=kept_both"
                        f"({basis.value}/{label})")
            elif flags[c] and log:
                log(f"extract:page={page_index}:period_selection=restated({basis.value}/{base})")
            used.add(label)
            out[(basis, c)] = label
    return out


# ── scope_selection.units_and_currency ───────────────────────────────────────────────────────
#
# "Resolve currency and scale from the statement header, not the cover page, and re-resolve per
# statement. Persist unit on every fact; never normalise scale silently." Reconstruction runs per
# PAGE, so the header it reads is the statement's own — a cover-page banner cannot reach it — and
# the resolved unit is written onto every fact rather than onto the document.
_SCALE_WORDS: tuple[tuple[re.Pattern[str], Decimal, str], ...] = (
    (re.compile(r"'0{3}|thousand", re.IGNORECASE), Decimal(1_000), "thousand"),
    (re.compile(r"千元|千"), Decimal(1_000), "thousand"),
    (re.compile(r"million", re.IGNORECASE), Decimal(1_000_000), "million"),
    (re.compile(r"百萬元|百万元|百萬|百万"), Decimal(1_000_000), "million"),
    (re.compile(r"billion", re.IGNORECASE), Decimal(1_000_000_000), "billion"),
    (re.compile(r"億元|亿元|億|亿"), Decimal(100_000_000), "hundred million"),
)
_CURRENCY_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"rmb|cny|人民幣|人民币|元人民幣", re.IGNORECASE), "CNY"),
    (re.compile(r"hk\$|hkd|港幣|港币|港元", re.IGNORECASE), "HKD"),
    (re.compile(r"us\$|usd|美元", re.IGNORECASE), "USD"),
    (re.compile(r"eur|€", re.IGNORECASE), "EUR"),
    (re.compile(r"gbp|£", re.IGNORECASE), "GBP"),
    (re.compile(r"₹|inr|rs\.?", re.IGNORECASE), "INR"),
)


def _unit_signals(scope: ScopeSelection | None) -> tuple[tuple[str, str, Decimal, str], ...]:
    """``(folded signal, currency, scale, scale word)`` for every declared units signal.

    Longest first, so "RMB million" is tested before a signal that is a prefix of it. A currency
    or scale the rulebook does not declare is not read off the page: the signal list is the
    vocabulary, and adding to it is how a filing in a new currency is supported.
    """
    out: list[tuple[str, str, Decimal, str]] = []
    for sig in ((scope.units_and_currency.signals if scope else []) or []):
        folded = _fold_for_match(sig)
        if not folded:
            continue
        ccy = next((c for rx, c in _CURRENCY_WORDS if rx.search(sig)), "")
        scale = next(((s, w) for rx, s, w in _SCALE_WORDS if rx.search(sig)), None)
        if not ccy and scale is None:
            continue
        out.append((folded, ccy, scale[0] if scale else Decimal(1), scale[1] if scale else ""))
    return tuple(sorted(out, key=lambda t: -len(t[0])))


def _unit_context(unit: tuple[str, Decimal, str] | None, page_index: int) -> UnitContext:
    """The resolved unit as it is persisted on a fact. An unresolved unit stays EMPTY — the scale
    is never guessed, because "never normalise scale silently" cuts both ways."""
    if unit is None:
        return UnitContext()
    return UnitContext(currency=unit[0], scale_factor=unit[1], units_label=unit[2],
                       source_bbox_page=page_index)


def _statement_unit(rows: list[list[Word]], signals: tuple[tuple[str, str, Decimal, str], ...],
                    fmt=None) -> tuple[str, Decimal, str] | None:
    """``(currency, scale, printed label)`` declared in this statement's header, or None.

    "From the statement header, not the cover page" is a statement about GEOMETRY, so the region is
    the one the column captions come from: the units caption is printed with the column heads, never
    among the figures.
    """
    for row in _header_region(rows, fmt):
        blob = _fold_for_match(" ".join(w.text for w in row))
        for folded, ccy, scale, word in signals:
            if folded in blob:
                return ccy, scale, word or folded
    return None


_TOTAL_LABEL = re.compile(r"^\s*(?:total|sub-?total)\b|總額|总额|合計|合计|總計|总计|小計|小计",
                          re.IGNORECASE)


def _conflict_factor(scope: ScopeSelection | None) -> Decimal | None:
    """The factor ``units_and_currency.conflict`` calls inconsistent, from the declared sentence
    ("inconsistent by a factor of 1,000"). No factor declared, no check."""
    text = (scope.units_and_currency.conflict if scope else "") or ""
    m = re.search(r"factor of\s+([\d,]+)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def _scale_conflict(rows: list[list[Word]], factor: Decimal, fmt=None,
                    steps: tuple[tuple[str, object], ...] = (),
                    signals: tuple[tuple[Basis, str], ...] = ()) -> str | None:
    """A printed subtotal inconsistent with the lines it totals by exactly ``factor``.

    That is the shape of the conflict the rulebook describes: the header declares one scale and a
    figure on the page is printed in another. Trusting either half silently mis-states the
    statement by three orders of magnitude, so the caller drops the unit and routes the statement
    to review — the one case where reporting nothing is the correct answer.
    """
    run: list[Decimal] = []
    for row in rows:
        label_words, _, value_words = _scan_row(row, fmt)
        label = apply_pipeline(_join_words(label_words), steps)
        vals = [d for d in (_num(w.text, fmt) for w in
                            sorted(value_words, key=lambda w: w.bbox.x0)) if d is not None]
        # A header or running-header row that leaked in would corrupt the sum, and a year is
        # indistinguishable from an amount once it is in it — so the same noise test the main loop
        # applies decides what counts as a line here, with the same declared basis signals (a
        # "Group Company 2024 2023" band row would otherwise add a year to the running total).
        if not label or not vals or _is_noise_row(label, vals, steps, signals):
            continue
        first = vals[0]
        if _TOTAL_LABEL.search(label):
            total = sum(run)
            if len(run) >= 2 and total and first:
                ratio = abs(first / total)
                for probe in (factor, Decimal(1) / factor):
                    if probe and abs(ratio - probe) <= probe / 100:
                        return f"subtotal({first})/sum({total})~{probe}"
            run = []
            continue
        run.append(first)
    return None


# ── scope_selection.column_guard ─────────────────────────────────────────────────────────────


_GUARD_DIMS = ("entity_scope", "period", "currency", "unit")


def guard_dimensions(scope: ScopeSelection | None) -> tuple[str, ...]:
    """The dimensions the rulebook says identify a fact, read off ``column_guard`` itself:
    "Every fact carries the resolved (entity_scope, period, currency, unit). Two facts for the
    same canonical_key that differ on any of these are distinct facts, not duplicates."
    """
    text = (scope.column_guard if scope else "") or ""
    m = re.search(r"\(([^)]*)\)", text)
    if not m:
        return ()
    named = [d.strip().lower().replace(" ", "_") for d in m.group(1).split(",")]
    return tuple(d for d in named if d in _GUARD_DIMS)


def _fact_identity(ev: ExtractedValue, dims: tuple[str, ...]) -> tuple:
    parts: list[object] = []
    for d in dims:
        if d == "entity_scope":
            parts.append(ev.basis.value)
        elif d == "period":
            parts.append((ev.period_end, ev.period_label, ev.period_display))
        elif d == "currency":
            parts.append(ev.unit_ctx.currency)
        elif d == "unit":
            parts.append(str(ev.unit_ctx.scale_factor))
    return tuple(parts)


def store_fact(li: LineItem, ev: ExtractedValue, dims: tuple[str, ...] = (), *,
               log=None, where: str = "") -> None:
    """Store a fact on a row, keeping two facts apart when the rulebook says they are distinct.

    ``LineItem.values`` is keyed by (basis, period_end, period_label), which cannot express a
    difference in currency or scale — so a second fact differing only there used to REPLACE the
    first in silence, which is the duplicate-collapse ``column_guard`` forbids. It is kept under a
    distinguished period_label instead.

    And when two facts agree on every declared dimension they really are one fact read twice, so
    the FIRST is kept: a printed figure is not improved by a second reading of the same column,
    and overwriting it means the row silently reports whichever cell the geometry happened to
    visit last. Either way the collision is logged rather than hidden.
    """
    key = ev.key.model_dump_json()
    existing = li.values.get(key)
    if existing is None:
        li.set_value(ev)
        return
    if not dims or _fact_identity(existing, dims) == _fact_identity(ev, dims):
        if log:
            log(f"extract:{where}duplicate_fact_dropped({ev.basis.value}/{ev.period_label})")
        return
    base = ev.period_label or "col"
    label, n = f"{base}#2", 2
    while li.get_value(ev.basis, ev.period_end, label) is not None:
        n += 1
        label = f"{base}#{n}"
    li.set_value(ev.model_copy(update={"period_label": label}))
    if log:
        log(f"extract:{where}distinct_fact({ev.basis.value}/{label})")


# ── Matrix statements (consolidated statement of changes in equity) ──────────────────────
#
# A matrix face has one value column per equity COMPONENT, captioned by a bilingual header band
# ("Share" / "premium" / "account" / "股份" / "溢價賬" stacked over the column). Detection is
# geometric so a mis-classified page still parses: ≥5 value columns on ≥3 rows is a layout no
# two-column comparative produces (four columns is the maximum there — 2 bases × 2 periods).
_MATRIX_MIN_COLS = 5
_MATRIX_MIN_ROWS = 3
# Value cells are RIGHT-aligned in a printed statement, so a column's right edge is its stable
# anchor (a cell's centre drifts with the width of the number in it). Distinct from `_COL_TOL`,
# which is the comparative path's CENTRE tolerance: one name per meaning, or the file's last
# definition governs both paths and the other constant is dead.
_MATRIX_COL_TOL = 0.012
# Footnote markers hang off reserve columns ("(3,596,236)*", "–*"). Left in place the token
# simply fails to parse and the column silently loses its value.
_FOOTMARK = re.compile(r"[*†#‡]+$")
_NIL_CELL = re.compile(r"^[-–—−]$")
# Header tokens that caption the units or the note reference for a column, not the component.
_UNITS_TOKEN = re.compile(r"[’'`]0{3}|千元|百萬元|億元|亿元|thousands?|millions?|billions?|"
                          r"lakhs?|crores?", re.IGNORECASE)
_NOTE_TOKEN = re.compile(r"note|附註|附注", re.IGNORECASE)


def _xc(w: Word) -> float:
    return (w.bbox.x0 + w.bbox.x1) / 2


def _median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _line_tol(words: list[Word]) -> float:
    """Row-clustering tolerance for a matrix, derived from the page's own line height.

    The default tolerance is deliberately generous so a slightly skewed scan still groups; in a
    matrix that generosity merges *adjacent* movement lines, which interleaves two captions and
    shifts every figure onto the wrong row. Half a line height keeps the lines apart.
    """
    hs = [w.bbox.y1 - w.bbox.y0 for w in words]
    return max(0.45 * _median(hs), 0.001)


def _cell_text(t: str) -> str:
    return _FOOTMARK.sub("", t.strip())


def _matrix_cells(row: list[Word], fmt=None) -> list[Word]:
    """The value-shaped tokens of a row. A nil dash counts: it carries no amount but it does
    mark a column position, which is what the column geometry is derived from."""
    out: list[Word] = []
    for w in row:
        t = _cell_text(w.text)
        if _NIL_CELL.match(t) or _num(t, fmt) is not None:
            out.append(w)
    return out


def _strong_cells(cells: list[Word], fmt=None) -> int:
    """How many cells are a real amount or a nil dash — i.e. not a bare 1–2 digit integer.
    A footnote line that prints a figure one digit per glyph ("9 , 3 5 8 , 6 1 1 , 0 0 0",
    as bilingual filings do) lands digits in the value columns; requiring a majority of real
    amounts keeps that line out of the matrix."""
    return sum(1 for w in cells
               if _NIL_CELL.match(_cell_text(w.text))
               or _is_money_like(_cell_text(w.text), fmt))


@dataclass
class _Matrix:
    rows: list[list[Word]]
    bands: list[tuple[float, float]]     # (left, right) x-extent of each component column
    first_data: int                      # row index of the first movement row
    pitch: float                         # median column pitch, the scale for header heuristics


def _pitch(edges: list[float]) -> float:
    return _median([edges[i + 1] - edges[i] for i in range(len(edges) - 1)]) or 1.0


def _detect_matrix(rows: list[list[Word]], fmt=None) -> _Matrix | None:
    """Geometry of a matrix face, or None when the page is not one.

    Columns come from the value rows themselves (clustered right edges) rather than from the
    header, because the header is what we then have to *attribute* to them — deriving both from
    the header would let a missing caption invent a column.
    """
    data_idx: list[int] = []
    cells_by_row: dict[int, list[Word]] = {}
    for i, row in enumerate(rows):
        cells = _matrix_cells(row, fmt)
        if len(cells) >= _MATRIX_MIN_COLS and _strong_cells(cells, fmt) * 2 >= len(cells):
            data_idx.append(i)
            cells_by_row[i] = cells
    if len(data_idx) < _MATRIX_MIN_ROWS:
        return None

    groups: list[list[float]] = []
    for e in sorted(w.bbox.x1 for i in data_idx for w in cells_by_row[i]):
        if groups and e - groups[-1][-1] <= _MATRIX_COL_TOL:
            groups[-1].append(e)
        else:
            groups.append([e])
    # A column of the matrix appears on most rows. A one-off cluster is a date fragment in a
    # label ("At 1 January 2022") or an inline note reference, not a column.
    min_support = max(2, (len(data_idx) + 2) // 3)
    edges = [_median(g) for g in groups if len(g) >= min_support]
    if len(edges) < _MATRIX_MIN_COLS:
        return None
    # Trim clusters standing off on their own — a note column, or label digits that happened to
    # line up — so the value area is the evenly pitched run of component columns.
    while len(edges) > _MATRIX_MIN_COLS and edges[1] - edges[0] > 2.5 * _pitch(edges):
        edges.pop(0)
    while len(edges) > _MATRIX_MIN_COLS and edges[-1] - edges[-2] > 2.5 * _pitch(edges):
        edges.pop()
    pitch = _pitch(edges)
    bands = [(edges[0] - pitch if k == 0 else edges[k - 1], edges[k])
             for k in range(len(edges))]
    return _Matrix(rows=rows, bands=bands, first_data=data_idx[0], pitch=pitch)


def _band_of(w: Word, bands: list[tuple[float, float]]) -> int | None:
    """The component column this word sits in, by x-centre — the same banding idea
    ``_period_bands``/``_basis_bands`` use for periods and bases."""
    xc = _xc(w)
    for k, (left, right) in enumerate(bands):
        if left < xc <= right:
            return k
    return None


def _x_runs(row: list[Word], gap: float) -> list[list[Word]]:
    """Split a header row into horizontally contiguous phrases. A caption sits inside one
    column with clear air on both sides; a *spanner* ("Attributable to owners of the parent")
    is one tight phrase laid across several columns and must not be read as any of their names.
    """
    runs: list[list[Word]] = []
    for w in row:
        if runs and w.bbox.x0 - runs[-1][-1].bbox.x1 <= gap:
            runs[-1].append(w)
        else:
            runs.append([w])
    return runs


def _caption_words(row: list[Word], m: _Matrix) -> dict[int, list[Word]]:
    """The words of one header row that genuinely caption a single column, keyed by column."""
    out: dict[int, list[Word]] = {}
    # Word spacing scales with the column width, so the "clear air" that separates two captions
    # is measured against the pitch rather than a fixed fraction of the page.
    for run in _x_runs(row, max(0.004, 0.15 * m.pitch)):
        spanned = {b for b in (_band_of(w, m.bands) for w in run) if b is not None}
        if len(spanned) >= 2 and len(run) >= 3:      # a group spanner, not a column caption
            continue
        for w in run:
            if w.bbox.x1 - w.bbox.x0 > m.pitch:      # too wide to belong to one column
                continue
            if _UNITS_TOKEN.search(w.text) or _NOTE_TOKEN.search(w.text):
                continue
            k = _band_of(w, m.bands)
            if k is not None:
                out.setdefault(k, []).append(w)
    return out


def _join_caption(parts: list[str]) -> str:
    """Join caption fragments, honouring the hyphen a printed column header wraps on
    ("Non-" / "controlling" / "interests" is one word plus two, not three)."""
    text = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if text and not text.endswith("-"):
            text += " "
        text += p
    return text.strip()


def _matrix_column_names(m: _Matrix) -> list[str] | None:
    """Name every component column from the header band, or None if that cannot be done.

    Returns None rather than a partial answer: a column we cannot name is a column whose figures
    we cannot attribute, and a made-up or duplicated name would silently merge two components.
    """
    per_row = [_caption_words(m.rows[i], m) for i in range(m.first_data)]
    # The header band is anchored by the rows that caption most columns at once (the last line
    # of the English captions, the Chinese line, the units line).
    anchor = [i for i, caps in enumerate(per_row)
              if len(caps) >= max(3, (len(m.bands) + 1) // 2)]
    if not anchor:
        return None
    start, end = min(anchor), max(anchor)
    # A three-line caption ("Share" / "premium" / "account") puts its first line ABOVE the row
    # that names every column, so extend upward through tightly spaced lines that caption
    # something — but no further, or the statement title and the group spanner join the names.
    while (start > 0 and per_row[start - 1]
           and _tight_below(_row_box(m.rows[start - 1]), _row_box(m.rows[start]))):
        start -= 1

    names: list[str] = []
    for k in range(len(m.bands)):
        words = [w for i in range(start, end + 1) for w in per_row[i].get(k, [])]
        latin = [w.text for w in words if re.search(r"[A-Za-z]", w.text)]
        # Prefer the English caption as the key; a Chinese-only filing keeps its own wording.
        name = _join_caption(latin) or _join_caption([w.text for w in words])
        if not name:
            return None
        names.append(name)
    return names if len(set(names)) == len(names) else None


def _heads_indented_block(words: list[Word]) -> bool:
    """A caption that HEADS the indented rows beneath it rather than wrapping into the next
    valued row — "Other comprehensive income/(loss) for the year:" and its CJK twin ending in
    the fullwidth colon. Gluing such a caption onto the first row below it corrupts that label.
    """
    if _looks_like_header(words):
        return True
    return " ".join(w.text for w in words).strip().endswith(("：", "﹕"))


def _matrix_basis(words: list[Word], page_scope: str | None = None) -> Basis:
    """One basis for the whole matrix. Its columns are components, so the Consolidated/Standalone
    banding used for comparatives would read them as bases and split the row apart.

    ``page_scope`` — the page classifier's verdict on WHOSE statement the page is — decides it when
    there is one, for the same reason it does in :func:`build_line_items` and one more besides: a
    matrix has no basis header band to outrank it, and its own words are the WORST evidence
    available. The word scan below only ever sees the component captions and the movement rows,
    which name no entity at all; on a sideways page it does not even see the title, because the
    title is printed upright and dropped as chrome. A statement of changes in equity printed past
    the notes for the Company alone — which is what the reserve note of an HKEX filing is — carries
    nothing for the scan to catch, so without the page's scope it defaulted to CONSOLIDATED and the
    Company's reserves were served as the Group's.
    """
    if page_scope in _PAGE_SCOPE_BASIS:
        return _PAGE_SCOPE_BASIS[page_scope]
    text = " ".join(w.text for w in words)
    if _STANDALONE.search(text) and not _CONSOL.search(text):
        return Basis.STANDALONE
    return Basis.CONSOLIDATED


def _is_matrix_noise(label: str, row_text: str, vals: list) -> bool:
    """A running header / page footer / statement title whose stray year landed in a value column.

    Deliberately laxer than ``_is_noise_row`` in one respect: a period-only label is *not* noise
    here, because "At 1 January 2023" / "於二零二三年一月一日" IS the opening-balance movement
    row. Chrome is recognised instead from the whole printed line — the page footer's company
    name sits in the label column while its "Annual Report 2023" sits over the value columns.
    """
    if _RUNNING_HDR.search(row_text):
        return True
    if not vals or not all(_is_date_ish(v) for v in vals):
        return False
    return bool(_HDR_LABEL.search(label)) or _is_period_only_label(label)


def _matrix_items(m: _Matrix, names: list[str], *, page_index: int, document_id: str | None,
                  source_kind: str, ordinal_start: int, fmt=None,
                  unit_ctx: UnitContext | None = None, dims: tuple[str, ...] = (),
                  log=None, page_scope: str | None = None) -> tuple[list[LineItem], int]:
    """One LineItem per MOVEMENT ROW, its values keyed by component-column name.

    Why one item per row rather than one per cell: ``LineItem.values`` is already a dict keyed by
    ``ValueKey(basis, period_end, period_label)``, so a single row holds as many named values as
    it has columns, and ``_serialize_rows`` ships ``period_label`` + ``period_display`` per value
    — the API and the statement view therefore render named component columns with no change
    downstream (``excel_extract`` already puts real column-header text in ``period_label``).
    One item per cell would multiply a 14-column statement into ~200 rows, destroy the row
    ordering the statement view relies on, and flood mapping with duplicate labels.

    The positional "current"/"prior" keys are deliberately NOT used: a component is not a period,
    and labelling it so would feed equity components into period-over-period arithmetic.
    """
    items: list[LineItem] = []
    ordinal = ordinal_start
    basis = _matrix_basis([w for row in m.rows for w in row], page_scope)
    value_left = m.bands[0][0]
    pending: list[Word] = []                 # label lines waiting for the row that has figures
    tail: BBox | None = None                 # box of the LAST pending line, for the wrap test
    for i in range(m.first_data, len(m.rows)):
        row = m.rows[i]
        label_words = [w for w in row if _xc(w) <= value_left]
        cells = [w for w in _matrix_cells(row, fmt) if _band_of(w, m.bands) is not None]

        # A movement in equity always touches at least its component and a total column, so a
        # lone figure on a line is chrome (a page footer's year), not a row of the matrix.
        if len(cells) < 2 or _strong_cells(cells, fmt) * 2 < len(cells):
            # No amounts on this line. Either the first line of a wrapped movement caption, or
            # a footnote whose glyph-split digits fell in the columns (cells but no amounts).
            if cells or not label_words:
                pending, tail = [], None
                continue
            box = _row_box(label_words)
            # Only vertical adjacency is required: the label column of a matrix holds nothing
            # else, and a bilingual continuation line is indented to sit under the *Chinese*
            # caption ("十二月三十一日" beneath "於二零二三年"), so the left-edge test
            # ``_wrap_adjacent`` applies to a two-column face would reject a genuine wrap.
            if tail is not None and not _tight_below(tail, box):
                pending = []                       # not a continuation, a new block
            pending, tail = pending + label_words, box
            if _heads_indented_block(pending):
                pending, tail = [], None
            continue

        if tail is not None and not _tight_below(tail, _row_box(label_words or row)):
            pending = []
        label_words, pending, tail = pending + label_words, [], None
        label = _join_words(_regroup_scripts(label_words))
        if not label:
            continue
        vals = [d for d in (_num(_cell_text(w.text), fmt) for w in cells) if d is not None]
        if _is_matrix_noise(label, " ".join(w.text for w in row), vals):
            continue

        # No section_hint, deliberately. A statement of changes in equity has no section banners on
        # its ROW axis — its rows are movements and its sections are the component COLUMNS — and the
        # rulebook declares no equity-statement concepts or section scopes for one to be compared
        # against. A sticky banner here would be a mechanism with nothing to gate.
        li = LineItem(source_label=label, ordinal=ordinal, role=LineRole.LINE,
                      source=ValueSource.MACHINE)
        label_bbox = _union([w.source_bbox for w in label_words])
        for cw in cells:
            # A nil dash is printed for "no movement"; it is not a figure, so no value is
            # emitted for it — the same reason the two-column path never invents a zero.
            dec = _num(_cell_text(cw.text), fmt)
            if dec is None:
                continue
            k = _band_of(cw, m.bands)
            prov = Provenance(
                # source_bbox, not the reading-space box: a sideways page is reconstructed in
                # reading space but RENDERED as drawn, so the viewer's highlight has to be given
                # the box on the page. (The two are the same box on an upright page.)
                document_id=document_id, page_index=page_index, bbox=cw.source_bbox,
                value_bbox=cw.source_bbox, label_bbox=label_bbox, text_snippet=label,
                source_kind=source_kind, producer=f"extract:{source_kind}@0.1.0",
            )
            store_fact(li, ExtractedValue(
                value_raw=dec, value=dec, basis=basis,
                # The component name is both the key and the column header shown in the UI.
                period_label=names[k], period_display=names[k],
                # Which column it is, left to right as printed. Kept because the printed order
                # cannot be recovered from the page box once that box is page-space: on a sideways
                # page the columns advance down the page's y, not across its x.
                column_index=k,
                unit_ctx=unit_ctx or UnitContext(), provenance=prov,
            ), dims, log=log, where=f"page={page_index}:matrix:")
        if li.values:
            items.append(li)
            ordinal += 1
    return items, ordinal


def _maybe_matrix(words: list[Word], *, statement: str | None,
                  fmt=None) -> tuple[_Matrix | None, list[str] | None]:
    """Matrix geometry + column names for a matrix page, else (None, None).

    Skips the (re-)grouping entirely for pages that cannot be a matrix, so the two-column
    reconstruction of every other statement face costs exactly what it did before.
    """
    if statement != "changes_in_equity":
        numeric = sum(1 for w in words if _num(_cell_text(w.text), fmt) is not None
                      or _NIL_CELL.match(_cell_text(w.text)))
        if numeric < _MATRIX_MIN_COLS * _MATRIX_MIN_ROWS:
            return None, None
    m = _detect_matrix(_group_rows(words, _line_tol(words)), fmt)
    if m is None:
        return None, None
    return m, _matrix_column_names(m)


# How far a figure's RIGHT EDGE may sit from a column's, as a fraction of page width, and still be
# the same column. Financial columns are right-aligned, so the right edge is the stable one — the
# left edge moves with every digit.
#
# THE THIRD SAME-COLUMN TOLERANCE IN THIS FILE, and named separately on purpose, because it answers
# a different question from the other two. `_COL_TOL` is the comparative path's CENTRE tolerance;
# `_MATRIX_COL_TOL` (0.012) is the matrix path's right-edge tolerance, for clustering the cells of a
# grid whose columns are already known to exist. This one asks whether ONE figure belongs to a
# column established by two or three rows above it, with no grid to fall back on, so it is
# deliberately looser: 0.02 of an A4 width is about 12pt — wider than the drift between two
# right-aligned numbers, and far narrower than the gap between two period columns of a statement
# (the measured filing's are 0.08 apart). One name per meaning, as `_MATRIX_COL_TOL`'s own comment
# insists; tuning one of the three says nothing about the others.
_COLUMN_EDGE_SLACK = 0.02

# How far inboard of its heading a row must start to be INSIDE that heading's block, as a fraction
# of page width. A block's details are indented; a row set flush with the caption has left it. The
# measured filing indents by 12pt against an A4 width, so ~0.02 — this is half of that, small enough
# that a filing indenting less still parses and large enough that sub-pixel drift on a row that is
# genuinely flush does not read as an indent.
_INDENT_MIN = 0.01


def build_line_items(words: list[Word], *, page_index: int, document_id: str | None,
                     source_kind: str, ordinal_start: int = 0,
                     number_format=None, statement: str | None = None,
                     log=None, scope: ScopeSelection | None = None,
                     normalisation: Normalisation | None = None,
                     on_face: bool = True,
                     page_scope: str | None = None,
                     page_title: str | None = None,
                     page_chrome: frozenset[str] = frozenset(),
                     column_grid: ColumnGrid | None = None,
                     grid_out: list[ColumnGrid | None] | None = None
                     ) -> tuple[list[LineItem], int]:
    """Reconstruct line items from positioned words. Returns (items, next_ordinal).

    Both bases are extracted in one pass: a two-basis header band (Group | Company,
    Consolidated | Standalone — from ``scope_selection.entity_scope.signals``) attributes each
    value column to its basis, and within a basis the column HEADING DATE decides which period a
    column is (``scope_selection.period_selection``). ``number_format`` (a locale
    ``NumberFormat``) makes value parsing locale-correct; omit for the US default.

    ``statement`` is the page classifier's verdict; ``"changes_in_equity"`` selects the matrix
    path, which a matrix layout also selects on its own so a mis-classified page still parses.
    ``log`` (``ctx.log``) records why a matrix page was skipped or fell back, and every scope
    decision taken from the rulebook — which basis header was found, which column was read as the
    current period, the unit resolved for the statement, and each conflict that routed it to
    review.

    ``scope``/``normalisation`` override the rulebook in force (see :func:`in_force_rules`), and
    ``on_face`` says whether these rows are a statement FACE: the ``company_only_markers`` rule is
    declared about the face, and a note listing the Company's investments in subsidiaries must not
    relabel the basis of a note that belongs to the consolidated statements.

    ``page_scope`` is the page classifier's ``PageSource.scope`` ("consolidated" / "company" /
    "mixed" / None). It is the third and weakest way a basis is decided, and the three are ranked:
    a two-basis header band on the page wins, because caption geometry attributes each COLUMN and
    nothing else can; the page scope comes next, because a title naming the entity is direct
    evidence about the whole page; ``company_only_markers`` is last, because it infers the entity
    from one line item being present. See :data:`_PAGE_SCOPE_BASIS`. On a MATRIX page the same
    verdict is instead the first thing consulted, because a matrix has no basis band to outrank it
    and its own words name no entity — see :func:`_matrix_basis`.

    ``column_grid`` is a :class:`ColumnGrid` read from an EARLIER page of the same note, used only
    when these rows carry no header of their own and cluster into the same number of columns. A
    PRC related-party note runs over eight pages and prints its two-level header once, on the
    first: without the carry, page two onward reads four columns positionally again — which is the
    defect the grid exists to close, reappearing three rows later. ``grid_out``, when given, is
    appended the grid these rows were actually read with, so the caller can carry it forward."""
    if scope is None or normalisation is None:
        in_force_scope, in_force_norm = in_force_rules()
        scope = scope if scope is not None else in_force_scope
        normalisation = normalisation if normalisation is not None else in_force_norm
    steps = _pipeline_steps(normalisation, scope)
    dims = guard_dimensions(scope)
    unit_signals = _unit_signals(scope)

    matrix, names = _maybe_matrix(words, statement=statement, fmt=number_format)
    if matrix is not None and names is not None:
        if log:
            log(f"extract:page={page_index}:equity_matrix_columns={len(names)}")
        # A matrix face declares its unit in its own header like any other statement, and
        # "persist unit on every fact" is not qualified by layout.
        return _matrix_items(matrix, names, page_index=page_index, document_id=document_id,
                             source_kind=source_kind, ordinal_start=ordinal_start,
                             fmt=number_format,
                             unit_ctx=_unit_context(_statement_unit(matrix.rows, unit_signals,
                                                                   number_format), page_index),
                             dims=dims, log=log,
                             # FACE ONLY, exactly as the two-column path applies it below.
                             page_scope=page_scope if on_face else None)
    if statement == "changes_in_equity":
        # A named matrix we cannot attribute is worse than nothing: every figure would be filed
        # under a period that does not exist. Report it and emit no rows for the page. A page
        # with no matrix layout at all is a genuinely two-column equity statement (small
        # entities present one), so that one falls through to the normal reconstruction.
        if matrix is not None:
            if log:
                log(f"extract:page={page_index}:equity_matrix_unnamed_columns"
                    f"={len(matrix.bands)}(skipped)")
            return [], ordinal_start
        if log:
            log(f"extract:page={page_index}:equity_no_matrix_layout(two_column_path)")

    items: list[LineItem] = []
    ordinal = ordinal_start
    # ALL the page geometry is read from the UNMERGED rows. `_merge_wrapped_labels` folds a
    # label-only line into the next valued line, so after it a basis caption no longer sits where
    # it was printed: merged into a statement line, the caption row carries that line's AMOUNTS and
    # `_basis_bands` correctly refuses it — the page then reads as single-basis and the Company's
    # column is added to the Group's. (Value words are untouched by the merge, so the note column
    # and the value columns come out the same either way.)
    raw_rows = _group_rows(words, row_tolerance(words, source_kind))
    entity_signals = _entity_signals(scope)
    # real period-end dates for column headers, if any
    period_bands = _period_bands(raw_rows, number_format)
    note_x = _detect_note_column(raw_rows)      # x of the note-ref column, so it isn't read as a value
    # Where the value columns actually are. A first pass over the page's figures (after the note
    # column is removed, so note references never look like a column) so a row reporting only one
    # of two periods still files that figure under the period it is printed in.
    col_xs: list[list[tuple[float, str]]] = []
    for row in raw_rows:
        _lw, _nr, _vw = _scan_row(row, number_format, extract_note_refs=on_face)
        if on_face:
            _nr, _vw = _resolve_note_column(_nr, _vw, note_x, number_format)
        xs = [((w.bbox.x0 + w.bbox.x1) / 2, w.text) for w in _vw
              if _num(w.text, number_format) is not None]
        if xs:
            col_xs.append(xs)
    value_bands = _value_column_bands(col_xs)
    bands = _basis_bands(raw_rows, value_bands, _value_area(value_bands, col_xs),
                         signals=entity_signals, fmt=number_format,
                         log=log, page_index=page_index)
    rows = _merge_wrapped_labels(raw_rows, number_format, steps, page_title=page_title)
    if not bands and on_face and page_scope in _PAGE_SCOPE_BASIS:
        # The classifier read the entity off the page's own title (or off its position past the
        # notes, which is what an untitled Company statement is). No column header names an entity
        # here — that is why no band was found — so the verdict covers the whole page: one band,
        # and `_basis_of_columns` gives every column that basis.
        #
        # FACE ONLY, for the same reason `company_only_markers` is: ``PageSource.scope`` is assigned
        # inside the classifier's ``if state == _FACE`` branch and describes a STATEMENT. A note
        # listing the Company's investments in subsidiaries belongs to the consolidated statements
        # it is a note to, and relabelling its basis would break the note-to-face tie.
        bands = [(_PAGE_SCOPE_BASIS[page_scope], 0.5)]
        if log:
            log(f"extract:page={page_index}:entity_scope=page_scope({page_scope})")
    if not bands and on_face:
        # company_only_markers: "Presence of …__investments_in_subsidiaries on the face is strong
        # evidence the column is company-only, since consolidation eliminates it." A single-basis
        # page carrying that line is the Company's statement, and filing it as the Group's adds
        # the Company's investment in its own subsidiaries to the consolidated balance sheet.
        stems = _company_only_stems(scope)
        for row in rows:
            lw, _nr, vw = _scan_row(row, number_format)
            if vw and _names_company_only(_join_words(lw), stems):
                # One band covers the page: `_basis_for` returns the nearest of one.
                bands = [(Basis.STANDALONE, 0.5)]
                if log:
                    log(f"extract:page={page_index}:entity_scope=company_only"
                        f"(marker:{_join_words(lw)!r})")
                break
    # units_and_currency: resolved from THIS statement's header (a page cannot see the cover), and
    # written onto every fact below rather than onto the document.
    unit = _statement_unit(raw_rows, unit_signals, number_format)
    factor = _conflict_factor(scope)
    if unit is not None and factor:
        clash = _scale_conflict(rows, factor, number_format, steps, entity_signals)
        if clash:
            # "Trust neither: route the statement to review." Dropping the unit is what makes that
            # visible downstream — a fact with no unit is not converted by anything.
            if log:
                log(f"extract:page={page_index}:units_conflict={clash}"
                    f"(header={unit[2]}):review")
            unit = None
    if unit is not None and log:
        log(f"extract:page={page_index}:units={unit[0] or 'unknown_ccy'}/{unit[2]}")
    unit_ctx = _unit_context(unit, page_index)
    # Group the value columns by basis (via the header band) once for the page, then let each
    # column's own heading date say which period it is.
    col_basis = _basis_of_columns(value_bands, bands)
    basis_cols: dict[Basis, list[int]] = {}
    for i in range(len(value_bands)):
        basis_cols.setdefault(col_basis[i], []).append(i)
    restated = _restated_markers(scope)
    # THE TWO-LEVEL COLUMN GRID (see :class:`ColumnGrid`). Read from the UNMERGED rows for the same
    # reason the basis bands are: after `_merge_wrapped_labels` a caption row no longer sits where
    # it was printed. A page with no such header keeps the grid its note's first page established,
    # provided its figures cluster into the same number of columns.
    grid = _period_measure_grid(raw_rows, value_bands,
                                _value_area(value_bands, col_xs), number_format)
    if grid is None and column_grid is not None and column_grid.columns == len(value_bands):
        grid = column_grid
        if log and grid.two_level:
            log(f"extract:page={page_index}:column_grid=carried({grid.describe()})")
    elif grid is not None and grid.two_level and log:
        # ONCE PER PAGE, naming every (period/measure) pair that was read. The reviewer's second
        # signal is per value — see `GRID_FLAG` where the facts are stored below.
        log(f"extract:page={page_index}:column_grid=period_x_measure({grid.describe()})")
    if grid_out is not None:
        grid_out.append(grid)
    col_periods = _column_periods(
        basis_cols, value_bands, period_bands, restated=restated,
        restated_cols=_restated_columns(raw_rows, value_bands, restated, number_format),
        grid=grid, log=log, page_index=page_index)
    section: str | None = None
    # THE SUB-HEADING WITHIN THE SECTION, kept separately from it and for a different job.
    #
    # The banner branch below deliberately refuses to let a colon sub-heading DISPLACE the section,
    # and that is right — the rows under "Adjustments for:" are still operating-activities rows. But
    # refusing to let it displace the section is not the same as throwing it away, and throwing it
    # away loses the only thing that gives some rows a meaning at all: a tax note printing
    #
    #     Under-provision in prior years, net:
    #       Mainland China                          136,626
    #
    # has a row whose caption is a GEOGRAPHY. "Mainland China" is not a tax concept and no alias,
    # rule or model should make it one; what makes that figure an under-provision of current tax is
    # the line above it. So the sub-heading is carried as its own field, available to a reader that
    # has failed on the caption, and it never touches the section gate.
    group: str = ""
    # WHICH VALUED ROWS THIS SUB-HEADING HAS GATHERED — their ordinals, not just a count. An
    # uncaptioned figure below them is readable as their subtotal, and the row that gets promoted
    # CARRIES THIS LIST so nothing downstream has to guess which rows it totals. Guessing was a
    # defect: a sub-heading's scope is not closed at the end of its block, so a reader re-deriving
    # the members from `group_hint` pooled in rows printed after the block and called a correct note
    # broken.
    #
    # Two is the bar. One detail row plus a figure underneath it is just as likely a wrapped value or
    # a comparative printed on its own line, and there is nothing for a subtotal to be a subtotal OF.
    #
    # MEMBERSHIP IS BY INDENT, measured against the heading's own left edge. A block's details are
    # printed inboard of the caption that introduces them, and a row set flush with it has left the
    # block whatever `group` still says — which is the difference between "Hong Kong 1,000" and the
    # top-level "Deferred tax 5,000" two lines further down. Without it a flush row joined the sum.
    block_ordinals: list[int] = []
    # The RIGHT EDGES of the figures already filed under this sub-heading, which is what an
    # uncaptioned figure has to line up with to be read as their total. See the promotion below.
    block_value_x1s: list[float] = []
    # The sub-heading's own left edge and label box: the first decides membership by indent, the
    # second becomes the promoted row's `label_bbox`, since the caption it is given is the heading's.
    group_x0: float | None = None
    group_label_bbox: BBox | None = None
    for row in rows:
        label_words, note_ref, value_words = _scan_row(
            row, number_format, extract_note_refs=on_face)
        if on_face:
            note_ref, value_words = _resolve_note_column(
                note_ref, value_words, note_x, number_format)

        if value_words:
            # Only for a VALUED row: a label-only row is already handled by the banner branch
            # below, and splitting one there would hand that branch the caption instead of the
            # banner it is looking for.
            merged_banner, label_words = _split_banner_prefix(label_words, steps)
            if merged_banner:
                section = merged_banner
                group = ""
        label = _join_words(_regroup_scripts(label_words))

        # A FIGURE WITH NO CAPTION, DIRECTLY UNDER A BLOCK IT TOTALS.
        #
        # A note prints an intermediate subtotal on a bare line. The real filing's tax note is the
        # measured case: "Current tax:" heads two detail rows, and the block's total is printed
        # under them with no caption of its own, because the caption is the sub-heading two rows up
        # and a typesetter does not repeat it.
        #
        # It was DROPPED, and by the first arm of the predicate below. That predicate was written
        # for the label-only banner case (`not value_words`); the `not label` arm swept in the
        # OPPOSITE shape — numeric columns, no caption — into a branch whose only escape is guarded
        # by `label and caption`, both empty here, so control always reached the bare `continue`.
        # The figure was never parsed, never given a `Provenance`, never appended: after that line
        # it did not exist.
        #
        # It was recovered only INCIDENTALLY, and by coincidence. `map_ontology`'s §20 split
        # re-derives the same number from `group_hint` by summing the block's detail rows, so on
        # this one note the printed figure and the computed one agree — but that is a sum of
        # components, not the figure the filing printed, it carries no box of its own to click, and
        # it only happens at all for an aggregate the rulebook marks `decomposition_allowed`. Every
        # other uncaptioned block subtotal in every other note was simply gone.
        #
        # THE ROLE IS THE LOAD-BEARING HALF, not the recovery. Filed as a `LINE` — the value both
        # construction sites here hardcode — this row is a detail of its own block, and two live
        # readers act on that:
        #
        #   * `stages/reconcile.py` excludes SUBTOTAL/TOTAL from a note's details precisely so a
        #     note's own total is not added to the rows it totals. As a LINE the 3,000 joins the
        #     1,000 and 2,000 it sums, and the note tie regrades to `unconfirmed`.
        #   * `map_ontology`'s decomposition skips anything whose role is not LINE. As a LINE this
        #     row ALSO resolves through the `group_hint` fallback, `_summed_columns` doubles, the
        #     orientation search finds a column unaccounted for, and the whole §20 split declines.
        #
        # So "stop dropping it" on its own destroys the recovery it was meant to make principled.
        # `notes_extract` already honours a builder role other than LINE, and it has to come from
        # here: the synthesised caption cannot be re-read to recover it.
        #
        # NARROW ON PURPOSE. Notes only (`on_face` false) — a note is where an uncaptioned block
        # subtotal is printed, while a bare number on a statement face is far likelier a stray. A
        # block must actually be open, and have gathered at least two valued rows. The figure must
        # sit under one of the page's own value columns, so a note reference in a narrow left
        # column is not promoted into a total. And `_is_noise_row` still runs, so a date fragment
        # or page chrome under an open block is refused like any other row.
        promoted = False
        # `group` is not tested here: members only accumulate under a heading, so two of them imply
        # one. Testing both would be a guard that cannot fire, which this file has removed before.
        if value_words and not label and not on_face and len(block_ordinals) >= 2:
            # IN THE SAME COLUMN AS THE ROWS IT TOTALS, measured against those rows rather than
            # against the page. Financial columns are right-aligned, so the RIGHT edge is what a
            # column shares and the left edge is not (it moves with the digit count).
            #
            # Compared to the BLOCK's own figures, not to the page's `value_bands`, and that is what
            # makes it a guard rather than a decoration: the bands need several rows of agreeing
            # evidence before they report a column at all, so on a short table they are empty and a
            # test written against them is inert exactly where a note is smallest. (A band test was
            # written here first and then removed — deleting it left the whole suite green, which is
            # the definition of a guard that is not one. Its stated job, rejecting a figure that
            # shares an edge but sits outside every column, is unreachable in any case: the band
            # tolerance is wider than this slack, so anything passing here is already inside a
            # band.) A note reference printed in its own narrow column to the left has no edge in
            # common with the block and is refused.
            aligned = any(abs(w.bbox.x1 - x1) <= _COLUMN_EDGE_SLACK
                          for w in value_words for x1 in block_value_x1s)
            # A PERIOD HEADER THAT LEAKED IN AS A ROW is the one thing an open block above it must
            # not turn into a total, and the general noise test cannot be used for it here: called
            # with the empty label this row still has, `_is_noise_row`'s label-less arm fires on
            # ANY row whose every figure is 1-31 or 1990-2099 — so it silently refused legitimate
            # subtotals of 30, or of 1,995 in thousands, which is routine in a note reported in
            # millions. Narrowed to the bare year, which is what a leaked column heading actually
            # is. The row still faces the full noise test below, under the caption it ends up with.
            bare_vals = [d for d in (_num(w.text, number_format) for w in value_words)
                         if d is not None]
            all_years = bool(bare_vals) and all(1990 <= abs(int(v)) <= 2099
                                                for v in bare_vals if int(v) == v)
            if aligned and not all_years:
                # THE BLOCK'S OWN CAPTION, AS THE FILING PRINTED IT, and nothing appended. An
                # earlier version appended the English word "subtotal", which put untranslated
                # English inside a caption whose other half is the filing's words — "本年度即期稅項
                # — subtotal" on a Chinese filing, shipped verbatim to a screen rendering in zh.
                # Every other note row's label is the filing's own text and this one is too. What
                # says the row is a total is its ROLE, and what says the caption was taken from
                # elsewhere on the page is `caption_borrowed`; neither needs a word in one language.
                label = group.rstrip(":：")
                promoted = True

        if not label or not value_words:
            # A label-only banner ("NON-CURRENT LIABILITIES", 流動負債) carries no amount, but it
            # scopes every row beneath it — the same caption under two banners is two different
            # concepts. Remember it before dropping the row.
            #
            # A sub-heading ending in a colon usually introduces a group WITHIN the section
            # rather than a new one, so by default it must not displace it: the rows under
            # "Adjustments for:" are still operating-activities rows, and losing that scope is
            # what lets an operating add-back resolve to an investing concept.
            #
            # Unless the sub-heading names a section in its own right. The income statement
            # splits both profit and total comprehensive income with "Attributable to:" /
            # "Total comprehensive income attributable to:", printing the same two captions
            # under each — so that colon heading is the only thing distinguishing them.
            #
            # A banner is recognised whatever its CASE. `_looks_like_header` accepts only
            # ALL-CAPS or a trailing colon, so a filing that prints "Non-operating expenses" in
            # title case set NO section at all — and the section gate then had nothing to tell
            # that section's captions apart from the ones printed identically elsewhere.
            #
            # A row that normalises to NOTHING is not a banner either: the units caption
            # ("RMB'000") and an audit-status note ("（未經審核）") are label-only rows that scope
            # no concept, and taking one as the section put a unit where a section belongs.
            caption = apply_pipeline(label, steps)
            banner = section_of_banner(caption)
            if label and caption and not value_words and (_looks_like_header(label_words, steps)
                                                          or banner is not None):
                if _is_units_caption(label_words):
                    # A units caption is not a section, whatever it is printed in. The declared
                    # unit-and-currency annotation step strips the currencies the RULEBOOK NAMES,
                    # and it is now apostrophe-insensitive so it reaches the typographic quote a
                    # real filing prints — but it can only strip what it was told about, and a
                    # caption it has not been told about ("HK$'000") still arrives here ALL-CAPS
                    # with no colon and would be read as a banner. This is the backstop for that,
                    # and it is about the TOKENS rather than about the rulebook's vocabulary.
                    continue
                if _looks_like_wrapped_tail(caption):
                    # The leftovers of a caption that wrapped, which is not a section even when it
                    # reads as one on its own line — see ``_TAIL_CONTINUATION``.
                    continue
                if banner is not None or not _ends_with_colon(label, steps):
                    section = label
                    group = ""          # a new section ends the sub-heading's scope
                else:
                    group = label
                # THE ONE BOUNDARY THAT DECIDES ANYTHING, and three others were removed for saying
                # so falsely: a promotion needs members, and members only accumulate under a
                # heading, so a reset paired with `group = ""` is code a reader reasons about for
                # nothing (each was deleted individually and the suite stayed green). A new block
                # starts with no rows behind it, no column edges, and this heading's geometry.
                block_ordinals, block_value_x1s = [], []
                group_label_bbox = _union([w.source_bbox for w in label_words])
                group_x0 = min((w.bbox.x0 for w in label_words), default=None)
            continue

        # Drop running-header / statement-title / period-caption lines that leaked in as rows
        # (their only "value" is a date fragment) — never a genuine financial line.
        row_vals = [d for d in (_num(w.text, number_format) for w in value_words) if d is not None]
        if _is_noise_row(label, row_vals, steps, entity_signals, page_chrome):
            continue

        # A heading is often printed on the SAME line as its first figure, so it never appears as
        # a label-only row: "Total comprehensive loss attributable to: … Owners of the parent"
        # is one row carrying the owners' amount. The heading still scopes this row and the rows
        # under it — without that, the second "Non-controlling interests" of the income statement
        # stays under the profit split and is added to the first, which is meaningless.
        head = re.split(r"[:：]", label)[0] if re.search(r"[:：]", label) else ""
        if head and section_of_banner(head) is not None:
            section = head.strip()
            group = ""

        # SUBTOTAL only for the row this builder itself promoted. Deliberately not inferred from
        # the caption: a caption test broad enough to catch "Total current tax" also catches
        # "Total return on funds", which is a detail line, and demoting a detail out of a note's
        # details breaks the note→face tie it belongs to. The promoted row is the one case where
        # this function KNOWS, because it is the one it synthesised the caption for.
        # A STATEMENT-LEVEL LINE OF A MAINLAND FACE IS A TOTAL, and saying so is what keeps it out
        # of a section's residual bucket: the residual framework's eligibility list already
        # excludes "a section subtotal, statement total, …", and that guard reads `role`. Untagged,
        # 三、营业利润 / 四、利润总额 / 五、净利润 were swept into is_pl__other_operating_expenses —
        # measured on TWO filings, so an expense bucket carried the operating profit, the
        # pre-tax profit and the net profit added together. `on_face` because inside a NOTE the
        # same numbering is a sub-note enumeration and means nothing about totals.
        statement_line = on_face and bool(_CAS_STATEMENT_LINE.match(label))
        li = LineItem(source_label=label, ordinal=ordinal,
                      role=(LineRole.SUBTOTAL if promoted
                            else LineRole.TOTAL if statement_line else LineRole.LINE),
                      caption_borrowed=promoted,
                      component_ordinals=list(block_ordinals) if promoted else [],
                      section_hint=section, group_hint=group, source=ValueSource.MACHINE)
        # THE HEADING'S BOX FOR A BORROWED CAPTION. A promoted row has no label words of its own, so
        # this was None — and `_prov_anchor` then falls back to the value box's vertical band alone,
        # which two sub-tables printed on one baseline share. The judgement layer refuses to attribute
        # an acceptance to a shared anchor, so the one note row a reviewer is most likely to correct
        # would have been the one whose verdict could not be recorded. The caption is the heading's,
        # so the heading's box is the honest one to cite.
        label_bbox = (group_label_bbox if promoted
                      else _union([w.source_bbox for w in label_words]))
        # Place each value in its own column within its basis — by the column's period when the
        # page has columns, else by the order the figures are printed in.
        per_basis: dict[Basis, int] = {}
        # Once the page's value columns are known, a figure that sits under NONE of them is not
        # a figure: it is a note reference the note-column heuristics did not catch, printed in
        # its own narrow column to the left. Taken as a value it claims the current period and
        # displaces the row's real figures. Only dropped when the row has at least one figure
        # that IS under a column, so a row the bands do not describe still reports positionally.
        in_col = {id(w): _column_index((w.bbox.x0 + w.bbox.x1) / 2, value_bands)
                  for w in value_words}
        drop_outliers = bool(value_bands) and any(v is not None for v in in_col.values())
        for vw in sorted(value_words, key=lambda w: w.bbox.x0):
            dec = _num(vw.text, number_format)
            if dec is None:
                continue
            if drop_outliers and in_col[id(vw)] is None:
                continue
            xc = (vw.bbox.x0 + vw.bbox.x1) / 2
            col = _column_index(xc, value_bands)
            basis = col_basis[col] if col is not None else _basis_for(xc, bands)
            # The column this figure is printed in decides its period, and the column's HEADING
            # decides which period that is. Order is the fallback for a page with no columnar
            # structure, and for a figure that sits under no column.
            k = per_basis.get(basis, 0)
            if col is not None and col in basis_cols.get(basis, []):
                k = basis_cols[basis].index(col)
            per_basis[basis] = max(per_basis.get(basis, 0), k) + 1
            period_label = col_periods.get(
                (basis, col) if col is not None else (basis, -1),
                "current" if k == 0 else "prior" if k == 1 else f"col{k}")
            prov = Provenance(
                document_id=document_id, page_index=page_index, bbox=vw.source_bbox,
                value_bbox=vw.source_bbox, label_bbox=label_bbox, text_snippet=label,
                source_kind=source_kind, producer=f"extract:{source_kind}@0.1.0",
            )
            display = _period_for(xc, period_bands)      # display-only date, if detected
            conf = ConfidenceVector()
            if grid is not None and grid.two_level and col is not None and col in grid.captions:
                # THE REVIEW CALL-OUT, on the existing per-value signal: this figure's column was
                # INTERPRETED from a period band over a measure band, not read off one caption.
                # `stages/confidence.py` raises `balance_mismatch`/`note_untied` the same way and
                # `_serialize_rows` already serves `values[].confidence.flags` to every screen.
                conf.flags.append(GRID_FLAG)
                period_cap, measure_cap = grid.captions[col]
                # The printed header, both levels of it, so the reviewer reads "本期发生额 成本"
                # rather than an internal token whose suffix they would have to decode.
                display = display or " ".join(t for t in (period_cap, measure_cap) if t) or None
            store_fact(li, ExtractedValue(
                value_raw=dec, value=dec, basis=basis,
                period_label=period_label,
                period_display=display,
                unit_ctx=unit_ctx, provenance=prov, confidence=conf,
            ), dims, log=log, where=f"page={page_index}:")
        if note_ref:
            li.note_refs.append(NoteRef(raw=note_ref, numbers=[note_ref]))
            li.note_number = note_ref
        items.append(li)
        ordinal += 1
        # A BLOCK'S MEMBERS END AT ITS SUBTOTAL, but `group` IS LEFT ALONE.
        #
        # Clearing it was tried and reverted. It looked right — the tax note prints "Deferred tax"
        # straight after the current-tax subtotal with no sub-heading of its own — but the same
        # assignment fires for a row that is still INDENTED under the heading, and `group_hint` is
        # the only thing that gives such a row a meaning (a bare "Mainland China" is a geography
        # until the line above it makes it a tax figure; see the comment where `group` is declared).
        # Stripping it there would have taken the mapper's fallback away from exactly the rows that
        # need it, so this change alters no row's scope. Whether a row after the subtotal is still in
        # the block is a question about INDENT, and it is answered below for membership only.
        #
        # Emptying the member list is what stops a second bare figure promoting against a block that
        # has already been totalled.
        if promoted:
            block_ordinals, block_value_x1s = [], []
        elif not on_face and group_x0 is not None and label_words:
            # A MEMBER OF THE BLOCK ONLY IF IT IS INDENTED INBOARD OF THE HEADING. A block's details
            # are printed inside the caption that introduces them; a row set flush with it has left
            # the block whatever `group` still says. Without this test the top-level "Deferred tax
            # 5,000" two lines below a "Current tax:" block joined that block's sum, and the bare
            # figure underneath was then reported as not adding up — a break invented by the reader,
            # in a note that is correct.
            #
            # A flush-set block layout therefore recovers nothing, and that is the intended trade:
            # accepting the first detail row's own edge as the block's indent would fix it and
            # immediately reintroduce the worse failure, since there would be no outdent left to end
            # the block on and it would swallow the next category.
            #
            # Nothing at all on a statement FACE, which is where nearly every row in a filing is:
            # the promotion cannot fire there, so this bookkeeping is work for a closed branch.
            if min(w.bbox.x0 for w in label_words) > group_x0 + _INDENT_MIN:
                block_ordinals.append(ordinal - 1)      # `ordinal` was incremented just above
                block_value_x1s.extend(w.bbox.x1 for w in value_words)
    return items, ordinal


def _union(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    b = boxes[0]
    for o in boxes[1:]:
        b = b.union(o)
    return b
