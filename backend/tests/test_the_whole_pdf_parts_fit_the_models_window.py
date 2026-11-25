"""A whole-PDF part is cut to fit the model's window, not only its page and size limits.

The shipped defaults are a 1,000,000-token window and 128,000-token replies (`llm.context_window_
tokens`, `llm.max_output_tokens`), with 600 pages a part. At 600 pages a part could hold more than
the window: so a part whose pages' estimated tokens (`services.whole_pdf.page_tokens`: each page's
text and its picture) and question (`overhead_tokens`) are over the window less a reply's room is
halved, as a part over the size limit is (`build_parts`). Measured on the reference filings (no
model) the largest request at the defaults is 653,138 estimated tokens (1966, every page sent), so
no shipped run is cut by it; a smaller window cuts sooner.
"""
from __future__ import annotations

import fitz

from app.services import whole_pdf as W


def _pdf(n: int, words: int = 400) -> bytes:
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((40, 60), f"page {i + 1} " + "figure " * words, fontsize=6)
    out = doc.tobytes()
    doc.close()
    return out


def test_each_page_costs_its_text_and_its_picture():
    raw = _pdf(3, words=0)
    tokens = W.page_tokens(raw, [0, 1, 2])
    assert set(tokens) == {0, 1, 2}
    assert all(W.PAGE_PICTURE_TOKENS < t < W.PAGE_PICTURE_TOKENS + 10 for t in tokens.values())


def test_a_part_over_the_window_is_halved_until_it_fits():
    raw = _pdf(40)
    pages = list(range(40))
    tokens = W.page_tokens(raw, pages)
    whole = sum(tokens.values())
    limit = whole // 3                      # room for about a third of the pages, with the question
    parts, dropped = W.build_parts(raw, pages, max_pages=600, max_request_bytes=64 * 1024 * 1024,
                                   max_input_tokens=limit, tokens_of=tokens, overhead_tokens=500)
    assert not dropped and len(parts) >= 3
    assert sorted(p for part in parts for p in part.pages) == pages
    assert all(sum(tokens[p] for p in part.pages) + 500 <= limit for part in parts)


def test_without_a_window_the_part_is_as_before():
    raw = _pdf(40)
    pages = list(range(40))
    parts, _ = W.build_parts(raw, pages, max_pages=600, max_request_bytes=64 * 1024 * 1024)
    assert [p.pages for p in parts] == [pages]


def test_the_faces_carried_in_every_part_count_against_each_part():
    raw = _pdf(30)
    pages = list(range(30))
    tokens = W.page_tokens(raw, pages)
    limit = sum(tokens.values()) // 2
    parts, _ = W.build_parts(raw, pages, max_pages=600, max_request_bytes=64 * 1024 * 1024,
                             carry=[0, 1], max_input_tokens=limit, tokens_of=tokens)
    assert len(parts) >= 2 and all({0, 1} <= set(p.pages) for p in parts)
    assert all(sum(tokens[p] for p in part.pages) <= limit for part in parts)


def test_the_stage_takes_the_window_and_the_reply_limit_from_the_settings():
    from types import SimpleNamespace

    from app.stages.whole_pdf import _model_limits

    shipped = SimpleNamespace(settings=SimpleNamespace(llm=SimpleNamespace(
        context_window_tokens=1_000_000, max_output_tokens=128_000)))
    assert _model_limits(shipped) == (1_000_000, 128_000)
    small = SimpleNamespace(settings=SimpleNamespace(llm=SimpleNamespace(
        context_window_tokens=200_000, max_output_tokens=128_000)))
    assert _model_limits(small) == (200_000, 100_000)      # a reply never more than half the window


def test_a_reply_budget_is_the_lines_room_up_to_the_reply_limit():
    assert W.max_tokens_for(208) == 1_024 + 208 * 220 == 46_784
    assert W.max_tokens_for(208, 32_000) == 32_000
    assert W.max_tokens_for(1_000) == W.DEFAULT_OUTPUT_TOKENS == 128_000


def test_a_lone_page_over_the_window_goes_without_the_faces_or_not_at_all():
    """A note page the window cannot hold beside the faces is sent without them; one it cannot hold
    at all is not sent (`dropped`), as a page over the size limit is."""
    raw = _pdf(6)
    pages = list(range(6))
    tokens = W.page_tokens(raw, pages)
    tokens[5] = 10 ** 6                                   # one page no window holds
    one = max(t for p, t in tokens.items() if p != 5)
    parts, dropped = W.build_parts(raw, pages, max_pages=600, max_request_bytes=64 * 1024 * 1024,
                                   carry=[0], max_input_tokens=3 * one, tokens_of=tokens)
    assert dropped == [5]
    assert all(sum(tokens[p] for p in part.pages) <= 3 * one for part in parts)
    assert {p for part in parts for p in part.pages} == set(pages[:5])   # the face in each


def test_faces_that_leave_no_room_for_a_page_are_not_carried():
    """Four faces and the question that fill the window: carrying them would make every note page a
    part of its own, each still over the window. They are not carried, and the parts fit."""
    raw = _pdf(12)
    pages = list(range(12))
    tokens = W.page_tokens(raw, pages)
    per = max(tokens.values())
    limit = 4 * per + 500
    parts, dropped = W.build_parts(raw, pages, max_pages=600, max_request_bytes=64 * 1024 * 1024,
                                   carry=[0, 1, 2, 3], max_input_tokens=limit, tokens_of=tokens,
                                   overhead_tokens=600)
    assert not dropped and sorted(p for part in parts for p in part.pages) == pages
    assert all(sum(tokens[p] for p in part.pages) + 600 <= limit for part in parts)
    assert len(parts) <= 4


def test_a_window_under_a_million_reads_at_most_100_pages_a_request():
    from app.stages import whole_pdf as S

    assert S._PAGES_UNDER_A_MILLION == 100


def test_a_doubled_retry_stays_within_what_the_wait_delivers():
    """The first budget runs to the model's reply limit; the retry with twice the room only where
    that is within `ROOM_CEILING` (16,000), what a reply at about 27 tokens a second delivers in
    the 600-second wait. A larger cut-off request is halved, not asked to write 93,568 tokens."""
    import inspect

    from app.ports.llm import ROOM_CEILING
    from app.stages import whole_pdf as S

    assert ROOM_CEILING == 16_000
    assert "2 * budget <= min(room, ROOM_CEILING)" in inspect.getsource(S.WholePdfStage._ask)
