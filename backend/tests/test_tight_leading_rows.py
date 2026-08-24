"""Two printed lines are two rows, however tightly the filing set them.

``_group_rows`` clusters words into visual rows and then orders each row LEFT TO RIGHT, so two
printed lines folded into one row do not come back merely joined — they come back with their words
INTERLEAVED BY X. Every caption below is a real one from a 367-page HKEX filing, and the middle
column is what the extractor actually produced:

    Reversal of impairment of property, plant and   ->  "Reversal equipment, of impairment net of
      equipment, net                                     property, plant and"
    Impairment of debtors, net                      ->  "Impairment Bad debts written of debtors,
    Bad debts written off                                off net"

A caption like that matches no alias in any rulebook, so the line maps to nothing and reaches the
analyst looking like a curiosity rather than a geometry bug. The cause was a row tolerance fixed at
0.012 of page height — about ONE line height, which lands inside the range of leadings filings
actually use, so everything set tighter than roughly 11pt merged.
"""
from __future__ import annotations

import io

import pytest

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

from app.services.row_reconstruct import Word, build_line_items, row_tolerance


def _render(lines: list[tuple[str, str | None]], leading: float = 10.0, size: float = 10.0) -> bytes:
    """A page whose lines are set ``leading`` points apart — 10pt on 10pt type by default, which is
    tighter than the old tolerance could resolve and entirely ordinary in a filing's notes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 100
    c.setFont("Helvetica", size)
    for text, value in lines:
        c.drawString(72, y, text)
        if value is not None:
            c.drawRightString(430, y, value)
        y -= leading
    c.showPage()
    c.save()
    return buf.getvalue()


def _words(data: bytes) -> list[Word]:
    import fitz

    from app.services.pdf_extract import _native_words

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        p = doc[0]
        return _native_words(p, max(p.rect.width, 1.0), max(p.rect.height, 1.0), rotation=0)
    finally:
        doc.close()


def _read(lines, **kw) -> list[tuple[str, list[str]]]:
    items, _ = build_line_items(_words(_render(lines, **kw)), page_index=0, document_id=None,
                                source_kind="native")
    return [(li.source_label, [str(v.value) for v in li.values.values()]) for li in items]


def test_a_caption_wrapping_at_tight_leading_is_not_interleaved():
    """FROM A REAL RUN. The caption wraps and the figures sit on its SECOND line, so the two lines
    must be one row — joined in printed order, not sorted into each other."""
    assert _read([("Reversal of impairment of property, plant and", None),
                  ("equipment, net", "(19,155)"),
                  ("Reversal of impairment of right-of-use assets, net", "(25,364)")]) == [
        ("Reversal of impairment of property, plant and equipment, net", ["-19155"]),
        ("Reversal of impairment of right-of-use assets, net", ["-25364"]),
    ]


def test_two_adjacent_single_line_rows_stay_two_rows():
    """The other half of the same defect, and the one no wrapped-label logic could ever fix: these
    two captions do not wrap at all. Merged, they interleaved into "Impairment Bad debts written of
    debtors, off net" and the filing lost both lines."""
    assert _read([("Impairment of debtors, net", "2,875"),
                  ("Bad debts written off", "1,172")]) == [
        ("Impairment of debtors, net", ["2875"]),
        ("Bad debts written off", ["1172"]),
    ]


def test_two_wrapped_captions_in_a_row_do_not_bleed_into_each_other():
    """Four printed lines, two captions, each wrapping — the shape that produced "Write-back
    Write-back of of impairment impairment of of advances amounts due and from other associates
    receivables", where the doubled words are the two captions' openings alternating."""
    assert _read([("Write-back of impairment of advances and", None),
                  ("other receivables", "(915)"),
                  ("Write-back of impairment of amounts due", None),
                  ("from associates", "(5,874)")]) == [
        ("Write-back of impairment of advances and other receivables", ["-915"]),
        ("Write-back of impairment of amounts due from associates", ["-5874"]),
    ]


def test_the_tolerance_follows_the_type_size_not_the_page():
    """WHY it is derived rather than fixed. A row tolerance has to sit below the leading and above
    zero; a constant fraction of the PAGE cannot, because the same constant is a different fraction
    of a line for every type size. Half the page's own median line height always can."""
    small = _words(_render([("Cash and bank balances", "2,379")], size=6.0))
    large = _words(_render([("Cash and bank balances", "2,379")], size=20.0))
    assert row_tolerance(small, "native") < row_tolerance(large, "native")
    # …and 6pt type set at 7pt still resolves, which a fixed 0.012 of an A4 page never could.
    assert _read([("Impairment of debtors, net", "2,875"),
                  ("Bad debts written off", "1,172")], leading=7.0, size=6.0) == [
        ("Impairment of debtors, net", ["2875"]),
        ("Bad debts written off", ["1172"]),
    ]


def test_ocr_keeps_the_generous_tolerance():
    """The axis is whether the coordinates can be TRUSTED. A recognised image carries residual skew
    after deskewing, which moves a word by a real fraction of a line across the width of a table —
    so the tolerance there has to absorb drift WITHIN a row. Tightening it would split one row in
    two and strand the figures away from their caption, which is the worse failure."""
    words = _words(_render([("Cash and bank balances", "2,379")]))
    assert row_tolerance(words, "ocr") > row_tolerance(words, "native")
    from app.services.row_reconstruct import _DEFAULT_Y_TOL

    assert row_tolerance(words, "ocr") == _DEFAULT_Y_TOL
