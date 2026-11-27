"""A NOTE REFERENCE WITH SUB-PARTS, OR A LIST OF THEM, LEAVES THE CAPTION.

NEW FILE -> backend/tests/test_note_references_with_sub_parts_leave_the_caption.py

The row reader takes a note reference out of a caption when it is shaped like one — a small number,
optionally with one bracketed sub-note ("16(b)"). Two Mauritian filings cite notes more finely:

* Gamma Civic 2024 cites "17(a)(ii)", "17(a)(iii)", "28(d)(i)" and "18/26" (p88-89). Each stayed in
  its caption, and "Taxation 17(a)(ii)" matched no alias: the tax charge (99,170,070) /
  (74,215,202) was swept into other operating expenses and the tax line was missing.
* Moroil 2025 cites "5(a), (b)" and "5(a), (b), 11" (p68-69), and the "(b)" was read into the
  caption: "Gain on property revaluation (b)", "Purchase of property, plant and equipment (b),".
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _is_note_ref_token, _note_ref_value, _scan_row


def _row(*cells: tuple[str, float]) -> list[Word]:
    return [Word(text=t, bbox=BBox(x0=x, y0=0.5, x1=x + 0.006 * len(t), y1=0.512))
            for t, x in cells]


def test_a_reference_with_several_sub_parts_is_a_reference():
    for token, note in (("17(a)(ii)", "17(a)(ii)"), ("28(d)(i)", "28(d)(i)"),
                        ("17(a)(iii)", "17(a)(iii)"), ("18/26", "18")):
        assert _is_note_ref_token(token)
        assert _note_ref_value(token) == note
    for figure in ("2024", "1,234", "(5,861)", "12.5"):
        assert not _is_note_ref_token(figure)


def test_gammas_taxation_row_keeps_only_its_caption():
    labels, note, values = _scan_row(_row(("Taxation", 0.06), ("17(a)(ii)", 0.36),
                                          ("(99,170,070)", 0.45), ("(74,215,202)", 0.55)))
    assert " ".join(w.text for w in labels) == "Taxation"
    assert note == "17(a)(ii)"
    assert [w.text for w in values] == ["(99,170,070)", "(74,215,202)"]


def test_moroils_list_of_references_leaves_the_caption():
    labels, note, values = _scan_row(_row(
        ("Purchase", 0.06), ("of", 0.12), ("property,", 0.14), ("plant", 0.20), ("and", 0.24),
        ("equipment", 0.27), ("5(a),", 0.36), ("(b),", 0.39), ("11", 0.42),
        ("(48,732)", 0.55), ("(51,012)", 0.65)))
    assert " ".join(w.text for w in labels) == "Purchase of property, plant and equipment"
    assert note == "5(a)"
    assert [w.text for w in values] == ["(48,732)", "(51,012)"]


def test_a_reference_not_followed_by_a_comma_ends_the_list():
    labels, note, values = _scan_row(_row(("Revenue", 0.06), ("5", 0.36), ("12", 0.55),
                                          ("10", 0.65)))
    assert note is None or note == "5"
    assert [w.text for w in values][-2:] == ["12", "10"]


def test_an_and_after_a_note_number_opens_the_captions_second_line():
    """Hindustan Unilever 2024-25 wraps "Total outstanding dues of creditors other than micro
    enterprises" / "and small enterprises" with its note 22 at the end of the first line (p143). The
    "and" carries a list of references on only when a reference follows it ("3, 4 & 39A")."""
    caption = "Total outstanding dues of creditors other than micro enterprises"
    cells = [(t, 0.06 + 0.04 * k) for k, t in enumerate(caption.split())]
    cells += [("22", 0.60), ("and", 0.62), ("small", 0.65), ("enterprises", 0.69),
              ("10,767", 0.80), ("9,926", 0.90)]
    labels, note, values = _scan_row(_row(*cells))
    assert " ".join(w.text for w in labels) == caption + " and small enterprises"
    assert note == "22"
    assert [w.text for w in values] == ["10,767", "9,926"]
