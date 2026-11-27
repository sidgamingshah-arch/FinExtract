"""A CAPTION WHOSE SECOND LINE OPENS ON A CONNECTIVE IS ONE CAPTION, EVEN WHEN ITS FIRST LINE IS
A CAPTION OF ITS OWN.

NEW FILE -> backend/tests/test_a_caption_that_wraps_on_a_connective_is_one_caption.py

Vivo Energy Mauritius 2025 prints, on page 69:

    Payments for purchase of property, plant and equipment
      and intangible assets                                  (300,791)     (263,191)

"Payments for purchase of property, plant and equipment" is an alias, so the vocabulary test that
stops an empty line item swallowing the next caption (`row_reconstruct._is_known_caption`) let it go
as an empty row of its own, and the figures kept "and intangible assets" — a caption that names
nothing, swept into other investing cash flows. A line that opens on a lower-case connective
("and", "of", "for") directly beneath a caption is that caption's second line
(`row_reconstruct._continues_on_a_connective`). In capitals, "And" or "Of" opens a caption.

Two capital-letter totals Gamma Civic 2024 wraps the same way, over a short line of capitals
(p93-94), are pinned here too (`row_reconstruct._is_wrapped_head`): "NET CASH FLOWS USED IN
FINANCING" / "ACTIVITIES" kept only "ACTIVITIES", and financing showed (743,159,285) against the
(399,681,936) printed.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _continues_on_a_connective, _is_wrapped_head

LINE_H = 0.012


def _line(text: str, y0: float, x0: float = 0.56) -> list[Word]:
    out, x = [], x0
    for tok in text.split():
        out.append(Word(text=tok, bbox=BBox(x0=x, y0=y0, x1=x + 0.006 * len(tok), y1=y0 + LINE_H)))
        x += 0.006 * len(tok) + 0.004
    return out


def test_a_second_line_opening_on_and_continues_the_caption_above():
    head = _line("Payments for purchase of property, plant and equipment", 0.500)
    tail = _line("and intangible assets", 0.514, x0=0.565)
    assert _continues_on_a_connective(head, tail)


def test_a_capitalised_and_opens_a_caption_of_its_own():
    head = _line("Payments for purchase of property, plant and equipment", 0.500)
    assert not _continues_on_a_connective(head, _line("And other payments", 0.514))


def test_a_line_far_below_or_set_left_of_the_caption_is_not_its_second_line():
    head = _line("Payments for purchase of property, plant and equipment", 0.500)
    assert not _continues_on_a_connective(head, _line("and intangible assets", 0.560))
    assert not _continues_on_a_connective(head, _line("and intangible assets", 0.514, x0=0.40))


def test_a_capital_letter_total_wraps_into_a_short_line_of_capitals():
    for head, tail in (("NET CASH FLOWS USED IN FINANCING", "ACTIVITIES"),
                       ("NET (DECREASE)/INCREASE IN CASH AND CASH", "EQUIVALENTS"),
                       ("OPERATING PROFIT/(LOSS) BEFORE WORKING", "CAPITAL CHANGES")):
        assert _is_wrapped_head(_line(head, 0.5, 0.05), _line(tail, 0.514, 0.05))


def test_a_capital_letter_banner_does_not_take_the_caption_beneath_it():
    assert not _is_wrapped_head(_line("CASH FLOWS FROM FINANCING ACTIVITIES", 0.5, 0.05),
                                _line("Dividend Paid to Owners of the Company", 0.514, 0.05))
    assert not _is_wrapped_head(_line("NON-CURRENT ASSETS", 0.5, 0.05),
                                _line("PROPERTY", 0.514, 0.05))


def test_a_caption_stopped_on_a_comma_continues_on_the_line_beneath():
    """Far East Horizon 2024 (p180), once "Proceeds from disposal of property, plant and
    equipment" is an alias (the Mauritius alias work): its first line ends on a comma, so the
    figures 1,923,864 / 1,243,767 belong to the whole caption, not to "intangible assets and other
    long-term assets" swept into other investing cash flows."""
    head = _line("Proceeds from disposal of property, plant and equipment,", 0.500)
    tail = _line("intangible assets and other long-term assets", 0.514, x0=0.565)
    assert _continues_on_a_connective(head, tail)
    # A head that ends a caption does not take a line that opens on another word.
    closed = _line("Proceeds from disposal of property, plant and equipment", 0.500)
    assert not _continues_on_a_connective(closed, tail)
