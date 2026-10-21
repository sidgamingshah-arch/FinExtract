"""A BARE FACE TOTAL THAT CLOSES OVER A BARE SUBTOTAL — the section's total, not a stray.

Lai Sun prints, under EQUITY, share capital and reserves, their uncaptioned subtotal, the
non-controlling interests and then the uncaptioned total equity; Kaming prints the same shape for
its non-current assets (a fixed-assets subtotal, three rows, the total). The first bare line added
up and took the banner, the section's members were emptied, and the real total added up to
nothing and was dropped: total equity reached no request.
"""
from __future__ import annotations

from app.core.models.enums import LineRole
from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items


def _w(text, x0, y0):
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + 0.0055 * max(len(text), 1),
                                     y1=y0 + 0.012))


def _words(rows):
    words, y = [], 0.20
    for caption, cur, pri in rows:
        x = 0.04
        for tok in (caption or "").split():
            words.append(_w(tok, x, y))
            x += 0.0055 * len(tok) + 0.003
        for edge, fig in ((0.37, cur), (0.44, pri)):
            if fig:
                words.append(_w(fig, edge - 0.0055 * len(fig), y))
        y += 0.02
    return words


EQUITY = [("EQUITY", None, None),
          ("Share capital", "2,178,944", "2,178,944"),
          ("Reserves", "12,207,475", "13,782,603"),
          (None, "14,386,419", "15,961,547"),
          ("Non-controlling interests", "16,718,428", "18,177,234"),
          (None, "31,104,847", "34,138,781")]


def _totals(rows):
    items, _ = build_line_items(_words(rows), page_index=0, document_id="d",
                                source_kind="native", statement="balance_sheet", page_fold=None)
    return [(li.source_label, sorted(str(v.value) for v in li.values.values()))
            for li in items if li.role is LineRole.SUBTOTAL]


def test_the_later_bare_total_takes_the_banner_and_the_subtotal_names_its_rows():
    assert _totals(EQUITY) == [
        ("Share capital + Reserves", ["14386419", "15961547"]),
        ("EQUITY", ["31104847", "34138781"]),
    ]


def test_a_single_bare_total_keeps_the_banner_as_before():
    assert _totals(EQUITY[:4]) == [("EQUITY", ["14386419", "15961547"])]


def test_a_bare_line_that_does_not_close_over_the_subtotal_is_still_dropped():
    rows = EQUITY[:5] + [(None, "99,999,999", "88,888,888")]
    assert _totals(rows) == [("EQUITY", ["14386419", "15961547"])]
