"""A face caption that wraps over five lines keeps its first line.

A US balance sheet prints the share capital as ONE caption: the classes, the par value, the shares
authorised per class and the shares issued and outstanding per class at both dates. Alphabet's 2025
10-K sets it over five lines, the figures beside the last:

    Class A, Class B, and Class C stock and additional paid-in capital, $0.001
    par value per share: 300,000 shares authorized (Class A 180,000, Class B
    60,000, Class C 60,000); 12,211 (Class A 5,835, Class B 861, Class C
    5,515) and 12,088 (Class A 5,822, Class B 837, Class C 5,429) shares
    issued and outstanding                                        84,800      93,126

A wrapped caption was let reach its figures over three continuation lines, so the first line was let
go as a row of its own and the line was published as "par value per share: 300,000 shares
authorized…" — a caption that names no concept. A face now allows five; a note keeps three, because a
note page is mostly prose.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

H = 0.0141
PITCH = 0.0126
X0 = 0.105

LINES = [
    "Class A, Class B, and Class C stock and additional paid-in capital, $0.001",
    "par value per share: 300,000 shares authorized (Class A 180,000, Class B",
    "60,000, Class C 60,000); 12,211 (Class A 5,835, Class B 861, Class C",
    "5,515) and 12,088 (Class A 5,822, Class B 837, Class C 5,429) shares",
    "issued and outstanding",
]


def _line(text: str, y: float) -> list[Word]:
    out, x = [], X0
    for tok in text.split():
        w = 0.0068 * len(tok)
        out.append(Word(text=tok, bbox=BBox(x0=x, y0=y, x1=x + w, y1=y + H)))
        x += w + 0.0045
    return out


def _page(lines: list[str]) -> list[Word]:
    words: list[Word] = []
    y = 0.60
    words += _line("Retained earnings", y)
    words += [Word(text="245,084", bbox=BBox(x0=0.735, y0=y, x1=0.79, y1=y + H)),
              Word(text="324,055", bbox=BBox(x0=0.86, y0=y, x1=0.915, y1=y + H))]
    y += 2 * PITCH
    for i, text in enumerate(lines):
        words += _line(text, y + i * PITCH)
    last = y + (len(lines) - 1) * PITCH
    words += [Word(text="84,800", bbox=BBox(x0=0.735, y0=last, x1=0.785, y1=last + H)),
              Word(text="93,126", bbox=BBox(x0=0.86, y0=last, x1=0.91, y1=last + H))]
    return words


def _captions(words: list[Word], on_face: bool) -> list[str]:
    items, _ = build_line_items(words, page_index=48, document_id="d", source_kind="native",
                                on_face=on_face)
    return [li.source_label for li in items if li.values]


def test_a_five_line_face_caption_keeps_its_first_line():
    captions = _captions(_page(LINES), on_face=True)
    capital = [c for c in captions if "issued and outstanding" in c]
    assert len(capital) == 1, captions
    assert capital[0].startswith("Class A, Class B, and Class C stock and additional paid-in capital"), capital


def test_the_wrap_is_still_bounded():
    # Five continuation lines: a caption of six lines is reached, a seventh line above it is not.
    lines = ["First extra line of text", "Second extra line of text"] + LINES
    captions = _captions(_page(lines), on_face=True)
    capital = [c for c in captions if "issued and outstanding" in c]
    assert len(capital) == 1, captions
    assert capital[0].startswith("Second extra line"), capital


def test_a_note_keeps_three_continuation_lines():
    captions = _captions(_page(LINES), on_face=False)
    capital = [c for c in captions if "issued and outstanding" in c]
    assert len(capital) == 1 and capital[0].startswith("par value per share"), captions


# ── A CAPTION THAT BREAKS AFTER ITS PAR VALUE'S COLON ─────────────────────────────────────────
#
# Apple prints the same caption broken after a colon:
#
#     Common stock and additional paid-in capital, $0.00001 par value:
#     50,400,000 shares authorized; 15,116,786 and 15,550,061 shares
#     issued and outstanding, respectively                          83,276      73,812
#
# A line ending in a colon introduces the rows beneath it, so the head was let go and the line was
# published as "50,400,000 shares authorized; …". A count of shares is never a caption's first word.

APPLE = [
    "Common stock and additional paid-in capital, $0.00001 par value:",
    "50,400,000 shares authorized; 15,116,786 and 15,550,061 shares",
    "issued and outstanding, respectively",
]


def test_a_colon_before_a_share_count_continues_the_caption():
    captions = _captions(_page(APPLE), on_face=True)
    capital = [c for c in captions if "issued and outstanding" in c]
    assert len(capital) == 1, captions
    assert capital[0].startswith("Common stock and additional paid-in capital"), capital


def test_a_colon_heading_over_a_caption_still_heads_it():
    lines = ["Stockholders' equity:", "Common stock, $0.01 par value; 1,000 shares authorized"]
    captions = _captions(_page(lines), on_face=True)
    assert not any(c.startswith("Stockholders") for c in captions), captions
