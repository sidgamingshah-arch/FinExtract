"""services.restatement — the rule that decides when two identical figures are one figure."""
from __future__ import annotations

from decimal import Decimal

from app.services.restatement import RestatementLedger

HKD = "HKD"
ONE = Decimal(1)


def test_the_first_contribution_is_never_a_restatement():
    ledger = RestatementLedger()
    assert ledger.is_restatement(Decimal("100"), HKD, ONE, "28") is False


def test_the_same_amount_from_a_different_note_is_a_restatement():
    # The English and Traditional Chinese printings of one note.
    ledger = RestatementLedger()
    ledger.is_restatement(Decimal("100"), HKD, ONE, "28")
    assert ledger.is_restatement(Decimal("100"), HKD, ONE, "28A") is True


def test_the_same_amount_from_the_same_note_is_counted():
    # Two lines of one note may legitimately charge the same figure.
    ledger = RestatementLedger()
    ledger.is_restatement(Decimal("60"), HKD, ONE, "28")
    assert ledger.is_restatement(Decimal("60"), HKD, ONE, "28") is False


def test_a_different_amount_is_never_a_restatement():
    ledger = RestatementLedger()
    ledger.is_restatement(Decimal("100"), HKD, ONE, "28")
    assert ledger.is_restatement(Decimal("101"), HKD, ONE, "28A") is False


def test_the_same_number_in_a_different_scale_is_a_different_figure():
    # 500 thousand and 500 million are not one disclosure printed twice.
    ledger = RestatementLedger()
    ledger.is_restatement(Decimal("500"), HKD, Decimal(1000), "28")
    assert ledger.is_restatement(Decimal("500"), HKD, Decimal(1000000), "28A") is False


def test_the_same_number_in_a_different_currency_is_a_different_figure():
    ledger = RestatementLedger()
    ledger.is_restatement(Decimal("500"), "HKD", ONE, "28")
    assert ledger.is_restatement(Decimal("500"), "USD", ONE, "28A") is False


def test_a_third_printing_is_also_a_restatement():
    ledger = RestatementLedger()
    ledger.is_restatement(Decimal("100"), HKD, ONE, "28")
    assert ledger.is_restatement(Decimal("100"), HKD, ONE, "28A") is True
    assert ledger.is_restatement(Decimal("100"), HKD, ONE, "28B") is True


def test_the_originating_note_is_recoverable_for_the_evidence_trail():
    ledger = RestatementLedger()
    ledger.is_restatement(Decimal("100"), HKD, ONE, "28")
    assert ledger.source_of(Decimal("100"), HKD, ONE) == "28"


def test_an_unseen_fingerprint_has_no_source():
    assert RestatementLedger().source_of(Decimal("1"), HKD, ONE) is None


def test_a_missing_currency_or_scale_still_fingerprints():
    # Not every caller has unit context; None must behave as its own value, not crash.
    ledger = RestatementLedger()
    assert ledger.is_restatement(Decimal("100"), None, None, "8") is False
    assert ledger.is_restatement(Decimal("100"), None, None, "9") is True
