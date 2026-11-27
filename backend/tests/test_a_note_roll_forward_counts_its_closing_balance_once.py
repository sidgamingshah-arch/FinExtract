"""A NOTE THAT ROLLS ITS BALANCE FORWARD COUNTS THE BALANCE ONCE: THE CLOSING ONE.

Phoenix Beverages 2025, note 10, "Financial assets at fair value through other comprehensive
income" (printed pages 177-178, MUR '000, the group's 2025 column):

    p177  At 1 July               3,448        p178  Equity securities - Mauritius   39,883
          Addition*              37,792              Equity securities - Réunion      1,202
          Impairment               (193)             At 30 June                      41,085
          Exchange differences       38              Ecocentre Limitee                2,091
          At 30 June             41,085              Run Sky Ltd                     37,792
                                                     Société Civile de Placement      1,202
                                                     At 30 June                      41,085
                                                     Mauritian Rupee                 39,883
                                                     Euro                             1,202
                                                     At 30 June                      41,085

The FVTOCI note part takes every row of the note, and came to 167,633 against the 41,085 the
note and the face (p128) print: the opening balance, the movements, the closing balance and the
components of the three analyses of that closing balance. The restatement rule set aside only the
exact duplicates.

The movement table proves itself — 3,448 + 37,792 - 193 + 38 = 41,085 — so its opening and
movements are set aside, and a run of rows in the note's other tables that closes on that balance
and adds up to it is an analysis of it (`services.note_sourced._inside_a_roll_forward`).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services import note_sourced as NS


def _hit(table, caption, amount, page, note="10", period="current"):
    value = SimpleNamespace(provenance=SimpleNamespace(page_index=page, label_bbox=None),
                            value=Decimal(amount))
    return NS.NoteRowHit(key="sub__ltp_fvtoci_note_total", note_number=note,
                         note_title="FINANCIAL ASSETS AT FAIR VALUE THROUGH OTHER COMPREHENSIVE "
                                    "INCOME", caption=caption, matched_by="\\S", value=value,
                         amount=Decimal(amount), basis="consolidated", period=period,
                         table=table)


def _phoenix_note_10():
    movement, analyses = object(), object()
    return [_hit(movement, "At 1 July", "3448", 90), _hit(movement, "Addition*", "37792", 90),
            _hit(movement, "Impairment", "-193", 90),
            _hit(movement, "Exchange differences", "38", 90),
            _hit(movement, "At 30 June", "41085", 90),
            _hit(analyses, "Equity securities - Mauritius", "39883", 91),
            _hit(analyses, "Equity securities - Réunion", "1202", 91),
            _hit(analyses, "At 30 June", "41085", 91),
            _hit(analyses, "Ecocentre Limitee", "2091", 91),
            _hit(analyses, "Run Sky Ltd", "37792", 91),
            _hit(analyses, "Société Civile de Placement Immobilier", "1202", 91),
            _hit(analyses, "At 30 June", "41085", 91),
            _hit(analyses, "Mauritian Rupee", "39883", 91), _hit(analyses, "Euro", "1202", 91),
            _hit(analyses, "At 30 June", "41085", 91)]


def test_the_closing_balance_of_a_proved_roll_forward_is_the_notes_figure():
    amount, inputs = NS.resolve(_phoenix_note_10(), "sum")[("consolidated", "current")]
    assert amount == Decimal("41085")
    reasons = [i.get("reason") for i in inputs]
    assert reasons[:4] == ["inside_a_roll_forward"] * 4
    assert inputs[4]["counted"] is True                    # the closing balance itself
    assert reasons[5:] == ["analysis_of_the_balance"] * 10


def test_a_table_that_does_not_add_up_is_read_as_it_always_was():
    """Two balance rows whose arithmetic fails are no roll-forward: every row still adds."""
    table = object()
    hits = [_hit(table, "At 1 July", "3448", 90), _hit(table, "Addition", "37792", 90),
            _hit(table, "At 30 June", "40000", 90)]
    amount, _inputs = NS.resolve(hits, "sum")[("consolidated", "current")]
    assert amount == Decimal("81240")


def test_a_list_of_components_with_no_balance_rows_still_adds():
    table = object()
    hits = [_hit(table, "Listed equity securities", "1200", 90),
            _hit(table, "Unlisted equity securities", "800", 90)]
    amount, _inputs = NS.resolve(hits, "sum")[("consolidated", "current")]
    assert amount == Decimal("2000")


def test_another_notes_rows_are_not_taken_as_an_analysis():
    hits = _phoenix_note_10()[:5] + [_hit(object(), "Unlisted debt securities", "500", 92,
                                          note="11")]
    amount, _inputs = NS.resolve(hits, "sum")[("consolidated", "current")]
    assert amount == Decimal("41585")


def test_a_chinese_movement_table_is_proved_the_same_way():
    table = object()
    hits = [_hit(table, "年初余额", "1000", 90, note="15"), _hit(table, "本年增加", "300", 90, note="15"),
            _hit(table, "本年减少", "-100", 90, note="15"), _hit(table, "年末余额", "1200", 90, note="15")]
    amount, _inputs = NS.resolve(hits, "sum")[("consolidated", "current")]
    assert amount == Decimal("1200")
