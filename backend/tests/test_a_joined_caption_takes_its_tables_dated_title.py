"""A CAPTION THE READER JOINED FROM TWO PRINTED LINES STILL TAKES THE DATED TITLE OVER ITS TABLE.

NEW FILE -> backend/tests/test_a_joined_caption_takes_its_tables_dated_title.py

Lai Sun's note 33 prints its provision matrix twice, "As at 31 July 2025" and "As at 31 July 2024".
In the 2024 block the reader ran the caption-only line "Expected credit loss rate 0% 1% 0% 100%
12%" into the row below it, and the row went out as "Expected credit loss rate Gross carrying
amount (HK$’000)" with no period: the joined caption is printed nowhere in the note's text, so it
could not be placed under its title. The 2024 gross receivables (470,403) never reached 2024, and
Net Trade Receivables for 2024 published the face's net "Debtors" less the allowance — 359,216
against a printed 413,052.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.note_columns import _joined_caption_at, _title_periods

TEXT = ("As at 31 July 2025 Past due\nCurrent to 30 to Over specific\n"
        "Expected credit loss rate 0% 0% 0% 100% 14%\n"
        "Gross carrying amount (HK$’000) 269,971 38,771 57,933 59,961 426,636\n"
        "Expected credit losses (HK$’000) 445 108 70 59,961 60,584\n"
        "As at 31 July 2024 Past due\nCurrent to 30 to Over specific\n"
        "Expected credit loss rate 0% 1% 0% 100% 12%\n"
        "Gross carrying amount (HK$’000) 321,967 29,451 62,309 56,676 470,403\n"
        "Expected credit losses (HK$’000) 499 151 25 56,676 57,351\n")
ROWS = [SimpleNamespace(raw_label=c) for c in (
    "Gross carrying amount (HK$’000)", "Expected credit losses (HK$’000)",
    "Expected credit loss rate Gross carrying amount (HK$’000)",
    "Expected credit losses (HK$’000)")]


def test_the_joined_row_is_dated_by_the_2024_title():
    dated = _title_periods(ROWS, [True] * len(ROWS), {}, TEXT)
    assert {i: d[0] for i, d in dated.items()} == {
        0: (2025, 7, 31), 1: (2025, 7, 31), 2: (2024, 7, 31), 3: (2024, 7, 31)}


def test_the_joined_caption_is_found_by_its_last_printed_line():
    at, part = _joined_caption_at(ROWS[2].raw_label, TEXT, TEXT.index("As at 31 July 2024"))
    assert part == "Gross carrying amount (HK$’000)"
    assert at == TEXT.index("Gross carrying amount (HK$’000) 321,967")


def test_fewer_than_three_closing_words_do_not_stand_in_for_a_caption():
    assert _joined_caption_at("Expected credit loss rate Total", "Total 1,234", 0) == (
        -1, "Expected credit loss rate Total")
