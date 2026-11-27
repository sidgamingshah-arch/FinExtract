"""THE CLASSIFY LOG NAMES A PRINTED PAGE BY ITS SHEET AND, ON A SPREAD, ITS HALF.

NEW FILE -> backend/tests/test_the_classify_log_names_the_sheet_and_its_half.py

Once a 2-up spread is read as its two halves, a position in the list of printed pages is not a page
number. Vivo Energy Mauritius' log said "notes_before_face=[2, 3, …, 54]->other(first_face=59)":
those were half-pages 2-54 (sheets 2-31) and sheet 34's left half, beside "classify:page=34:…"
lines that index sheets — and both halves of a sheet logged the same "page=34". A line now names
the sheet's 0-based index, plus L or R on a spread; a filing with no spreads logs what it did.
"""
from __future__ import annotations

from app.stages.classify import (_FACE, _NOTES, _PRE, PageFeat, _notes_follow_the_face,
                                 _untitled_face_inside_the_notes_is_notes, _unit_label)


def test_a_half_is_named_by_its_sheet_and_side():
    assert _unit_label(PageFeat(index=34, side="L")) == "34L"
    assert _unit_label(PageFeat(index=34, side="R")) == "34R"
    assert _unit_label(PageFeat(index=12)) == "12"


def test_notes_before_the_face_are_named_by_sheet_and_half():
    feats = [PageFeat(index=0), PageFeat(index=1, side="L"), PageFeat(index=1, side="R"),
             PageFeat(index=2, side="L", strong_title=True), PageFeat(index=2, side="R")]
    logs: list[str] = []
    _notes_follow_the_face([_PRE, _NOTES, _NOTES, _FACE, _NOTES], log=logs.append, feats=feats)
    assert logs == ["classify:notes_before_face=[1L, 1R]->other(first_face=2L)"]


def test_a_filing_without_spreads_logs_its_page_indexes_as_before():
    feats = [PageFeat(index=i) for i in range(5)]
    feats[2].strong_title = True
    logs: list[str] = []
    _notes_follow_the_face([_NOTES, _PRE, _FACE, _NOTES, _NOTES], log=logs.append, feats=feats)
    assert logs == ["classify:notes_before_face=[0]->other(first_face=2)"]


def test_an_untitled_face_run_inside_the_notes_is_named_by_sheet_and_half():
    feats = [PageFeat(index=7, side="R"), PageFeat(index=8, side="L"),
             PageFeat(index=8, side="R"), PageFeat(index=9, side="L")]
    logs: list[str] = []
    _untitled_face_inside_the_notes_is_notes([_NOTES, _FACE, _FACE, _NOTES], feats,
                                             log=logs.append)
    assert logs == ["classify:untitled_face_inside_notes=8L-8R->notes"
                    "(no statement title on any of 2 page(s))"]
