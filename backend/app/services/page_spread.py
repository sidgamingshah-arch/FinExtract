r"""A PDF PAGE THAT HOLDS TWO PRINTED PAGES SIDE BY SIDE, and where its gutter is.

WHY THIS EXISTS. An Indian integrated annual report is routinely published as a 2-up spread: one
1188x792pt landscape PDF page carries two printed A4 pages, each with its own folio. Measured on
Asian Paints' Integrated Annual Report 2025-26, PDF page index 183 carries

    LEFT   Balance Sheet as at 31st March 2026
    RIGHT  Statement of Profit and Loss for the year ended 31st March 2026

and page index 182 prints the folios "362" and "363" on the one page. Every page of that filing is
a spread.

WHAT THAT DID TO THE READERS, measured before this module existed. Two printed pages share their
baselines, so `row_reconstruct._group_rows` — which clusters words by vertical position across the
whole page — folded a row of the balance sheet together with the row of the profit and loss printed
at the same height:

    Property, Plant and Equipment   2A   6,040.18   6,285.40   30,621.70   29,270.69
                                         \_ PPE, the two years  _/ \_ Revenue from Sale of
                                                                      Products, the two years _/

So PPE's row carried Revenue's figures. Four value columns where the page prints two, which
collapsed the inferred column geometry for the whole page, and captions fused across the gutter
into text that matches no alias in any rulebook:

    "Current Assets Other Expenses"
    "Financial Assets EARNING BEFORE INTEREST, TAX,"

THE GUTTER IS FOUND FROM THE WORDS, NOT FROM THE PAGE SIZE. A page's aspect ratio does not settle
it — a single wide table (an equity matrix, a PPE cost-and-depreciation schedule) is landscape too,
and splitting one down the middle would strand every figure away from its caption, which is a worse
failure than the one this fixes. So the test is about what is PRINTED: an empty vertical band near
the middle, and two halves that each look like a page in their own right.

FOUR CONDITIONS, AND THE LAST IS THE ONE THAT MAKES IT SAFE:

1. AN EMPTY VERTICAL BAND. No word's box crosses it, it is at least `_MIN_GUTTER` of the width, and
   its centre lies in `_CENTRE_BAND` — a spread's fold is near the middle by construction.
2. BOTH HALVES SUBSTANTIAL. Each carries at least `_MIN_WORDS_PER_HALF` words, so a wide table with
   one stray figure past the middle is not a spread, and neither is a mostly-empty page.
3. THE BAND RUNS THE PAGE'S HEIGHT. It must be empty across at least `_MIN_VERTICAL_COVER` of the
   rows that carry any word. A table with a wide column break at mid-page has an empty band too,
   but only for the few rows that happen not to fill it.
4. EACH HALF HAS ITS OWN CAPTION COLUMN, ON MANY ROWS. This is the discriminator between two
   printed pages and one wide table, and nothing else here does that job. Each half must carry at
   least `_MIN_CAPTION_WORDS` non-numeric words beginning inside the leftmost `_CAPTION_ZONE` of
   THAT HALF's own width, spread over at least `_MIN_CAPTION_ROWS` distinct printed rows. The row
   count is the part that matters: a wide table's right half does carry wordy tokens near the
   middle — its column headers — and a word count alone accepted them, splitting the shipped
   equity-matrix fixture in two. See :func:`_has_own_caption_column`.

A page that fails any of them is read exactly as it was before, so this is inert on every
single-page filing — which is the whole shipped corpus.
"""
from __future__ import annotations

import re

# The fold has to be a real space, not the gap between two columns of one table.
_MIN_GUTTER = 0.025
# A spread's fold is near the middle. Binding is not printed dead centre on every filing, so the
# band is generous — but a "gutter" at x=0.25 is a column break and this refuses it.
_CENTRE_BAND = (0.40, 0.60)
_MIN_WORDS_PER_HALF = 30
_MIN_VERTICAL_COVER = 0.80
# Each half's own left margin, as a fraction of that half's width.
_CAPTION_ZONE = 0.35
_MIN_CAPTION_WORDS = 8
# Distinct printed rows a half's own caption column must occupy. Six is above the height of a
# stacked bilingual column-header band (the equity matrix's is five lines) and far below the row
# count of any real printed statement page.
_MIN_CAPTION_ROWS = 6

# A word that carries meaning rather than an amount. Deliberately not `_num` from
# `row_reconstruct`: that parses against a rulebook's number format and this only needs to know
# that a token is not a bare figure, so importing it would couple this module to a rulebook for no
# gain and create an import cycle.
_NUMERIC = re.compile(r"^[\s(]*[-+]?[\d,. ']+\s*[)%]?[\s.,;]*$")


def _is_wordy(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2:
        return False
    return not _NUMERIC.match(t)


def _empty_bands(words, *, step: float = 0.005) -> list[tuple[float, float]]:
    """Maximal x-intervals inside `_CENTRE_BAND` that no word's box crosses.

    Sampled rather than swept, because a word box is an interval and the question asked of each
    candidate x is only "does anything cross here" — `step` at 0.005 is half the narrowest gutter
    this would accept, so a real fold cannot fall between two samples.
    """
    lo, hi = _CENTRE_BAND
    occupied: list[bool] = []
    xs: list[float] = []
    x = lo
    while x <= hi + 1e-9:
        xs.append(x)
        occupied.append(any(w.bbox.x0 <= x <= w.bbox.x1 for w in words))
        x += step
    bands: list[tuple[float, float]] = []
    start: float | None = None
    for x, busy in zip(xs, occupied):
        if not busy and start is None:
            start = x
        elif busy and start is not None:
            bands.append((start, x - step))
            start = None
    if start is not None:
        bands.append((start, xs[-1]))
    return bands


def _has_own_caption_column(half, x0: float, x1: float) -> bool:
    """Whether this half prints a column of captions down its OWN left margin — condition 4.

    COUNTED OVER DISTINCT ROWS, not over words, and that is what makes this the discriminator
    rather than a formality. A wide single table HAS wordy tokens near the middle of the page —
    they are its column headers — and counting words alone accepted them: the shipped equity-matrix
    fixture puts "Other reserves" / "Retained profits" / 其他儲備 / RMB'000 in the right half's
    left margin, eight wordy words, and the page was split down the middle with every figure
    stranded from its caption. Those words sit on FOUR rows in the header band and nowhere else.

    A printed page's captions run down the whole page instead: the right half of the Asian Paints
    spread starts "Revenue from Sale of Products", "Other Operating Revenue", "Other Income",
    "Total Income (I)", "EXPENSES", "Cost of Materials Consumed" … at its own margin, row after
    row. Requiring `_MIN_CAPTION_ROWS` distinct rows separates the two without needing to know
    which kind of page it is.
    """
    if x1 <= x0:
        return False
    edge = x0 + (x1 - x0) * _CAPTION_ZONE
    inside = [w for w in half if w.bbox.x0 <= edge and _is_wordy(w.text)]
    if len(inside) < _MIN_CAPTION_WORDS:
        return False
    rows = {int(((w.bbox.y0 + w.bbox.y1) / 2) * 200) for w in inside}
    return len(rows) >= _MIN_CAPTION_ROWS


def _covers_the_height(words, centre: float) -> bool:
    """Condition 3 — the band is empty for most rows, not only for a few."""
    rows: dict[int, bool] = {}
    for w in words:
        # 200 horizontal slices of the page: about a line each on a full page of text, so a row
        # that straddles two slices costs at most one false "occupied" and cannot make an
        # occupied band look empty.
        band = int(((w.bbox.y0 + w.bbox.y1) / 2) * 200)
        crosses = w.bbox.x0 <= centre <= w.bbox.x1
        rows[band] = rows.get(band, False) or crosses
    if not rows:
        return False
    clear = sum(1 for crossed in rows.values() if not crossed)
    return clear / len(rows) >= _MIN_VERTICAL_COVER


def gutter_x(words) -> float | None:
    """The x of the fold of a 2-up spread, or None when this page is one printed page.

    `words` are `core.models.geometry`-normalised, so the return value is a fraction of the page
    width and directly comparable with any word's `bbox`.
    """
    if len(words) < 2 * _MIN_WORDS_PER_HALF:
        return None
    candidates = [(b1 - b0, (b0 + b1) / 2) for b0, b1 in _empty_bands(words)
                  if (b1 - b0) >= _MIN_GUTTER]
    if not candidates:
        return None
    # WIDEST FIRST. A spread's fold is the biggest empty space on the page; a narrow column break
    # that also happens to fall inside the centre band is tried only if the fold is refused.
    for _width, centre in sorted(candidates, reverse=True):
        left = [w for w in words if (w.bbox.x0 + w.bbox.x1) / 2 < centre]
        right = [w for w in words if (w.bbox.x0 + w.bbox.x1) / 2 >= centre]
        if len(left) < _MIN_WORDS_PER_HALF or len(right) < _MIN_WORDS_PER_HALF:
            continue
        if not _covers_the_height(words, centre):
            continue
        if not (_has_own_caption_column(left, 0.0, centre)
                and _has_own_caption_column(right, centre, 1.0)):
            continue
        return centre
    return None


def halves(words, centre: float):
    """`(left, right)` — the words of each printed page, split at the fold."""
    left = [w for w in words if (w.bbox.x0 + w.bbox.x1) / 2 < centre]
    right = [w for w in words if (w.bbox.x0 + w.bbox.x1) / 2 >= centre]
    return left, right


def page_fold(words, width: float, height: float, rotation: int | None = 0) -> float | None:
    """The fold of a WHOLE PAGE, or None — `gutter_x` behind the one test only the page can answer.

    A 2-UP SPREAD IS A LANDSCAPE SHEET. Two portrait pages set side by side are wider than they are
    tall, so a sheet that is taller than it is wide — in READING space, which is the page turned
    for a statement printed sideways — holds one printed page, and no band of empty space on it is
    a fold. `gutter_x` cannot ask this, because it is handed normalised words and the page's shape
    is gone by then; and without it, it misfired. Measured over the five reference filings: 31
    pages called spreads, every one of them portrait — 河钢股份 000709's related-party note pages
    among them, where a figure column on the right read as a second printed page and the rows
    were rebuilt from half of each line. None of those filings is a spread at all.
    """
    quarter = (rotation or 0) % 180 == 90
    wide, tall = (height, width) if quarter else (width, height)
    if wide <= tall:
        return None
    return gutter_x(words)
