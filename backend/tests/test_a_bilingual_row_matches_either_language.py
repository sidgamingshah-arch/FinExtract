"""A bilingual note row matches a pattern written for either of its languages.

An HKEX note prints every row twice over — "Other receivables 其他應收款項" — and the authored row
patterns are anchored to a caption's whole extent (`^\\s*other\\s+receivables?\\s*$`), so neither the
English pattern nor the Chinese one could match either half. China SCE 1966's note 24 prints its
other receivables at 5,818,375 / 7,028,687, and Other Receivables (CP) was blank.
"""
from __future__ import annotations

import re

from app.services.note_sourced import _matches_any, _script_halves

ANCHORED = [(r"^\s*other\s+receivables?\s*$", re.compile(r"^\s*other\s+receivables?\s*$", re.I)),
            (r"^\s*其他應收款項?\s*$", re.compile(r"^\s*其他應收款項?\s*$"))]


def test_either_half_of_a_bilingual_caption_answers_an_anchored_pattern():
    assert _matches_any("Other receivables 其他應收款項", ANCHORED[:1])
    assert _matches_any("Other receivables 其他應收款項", ANCHORED[1:])


def test_the_halves_are_the_two_scripts():
    assert _script_halves("Prepayments (note) 預付款項（附註）") == [
        "Prepayments (note) 預付款項（附註）", "Prepayments (note)", "預付款項（附註）"]


def test_a_caption_in_one_script_is_matched_as_before():
    assert _script_halves("其他應收款項") == ["其他應收款項"]
    assert _script_halves("Other receivables") == ["Other receivables"]
    assert not _matches_any("Other receivables due from associates", ANCHORED[:1])
