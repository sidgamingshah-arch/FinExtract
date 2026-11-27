"""A RUPEE IS THE CURRENCY OF THE COUNTRY THE REPORT IS ABOUT.

NEW FILE -> backend/tests/test_a_rupee_is_the_currency_of_the_country_the_report_is_about.py

Vivo Energy Mauritius 2025 heads every statement "Rs’000". That set the scale and no currency at all
("normalize:units=thousand/unknown_ccy"), and read as a fixed INR it would have stamped Mauritian
rupees Indian. Mauritius, Sri Lanka, Pakistan, Nepal and the Seychelles print their figures in
rupees and say so the same way, so "Rs" and "rupees" name A rupee, and which one is the country the
document names most often (`normalize._resolve_rupee`). "₹" and "INR" stay the Indian rupee, and
"MUR" (Phoenix Beverages' "MUR '000", Gamma Civic's "MUR") the Mauritian one, whatever the document
says.
"""
from __future__ import annotations

import pytest

from app.stages.normalize import _currency_in, _resolve_rupee

MAURITIUS = "vivo energy mauritius limited, port louis, mauritius " * 50


@pytest.mark.parametrize("text", [
    "notes rs’000 rs’000", "revenue rs'000", "(rs. in crores)", "all amounts in crore rupees"])
def test_a_rupee_is_the_rupee_of_the_country_the_document_names(text):
    assert _currency_in(text, MAURITIUS) == "MUR"
    assert _currency_in(text) == "INR"          # no document: India's, as it always was


@pytest.mark.parametrize("text, code", [
    ("mur '000", "MUR"),
    ("in mauritian rupees", "MUR"),
    ("(₹ in crores)", "INR"),
    ("inr million", "INR"),
])
def test_a_named_rupee_is_that_rupee_whatever_the_document_says(text, code):
    assert _currency_in(text, MAURITIUS) == code
    assert _currency_in(text) == code


def test_shareholders_apostrophe_is_not_a_rupee():
    assert _currency_in("statement of changes in shareholders' equity") is None


def test_the_country_named_most_often_decides_which_rupee():
    mauritius = "vivo energy mauritius limited " * 200 + " a market in india "
    assert _resolve_rupee(mauritius) == "MUR"
    india = "hindustan unilever limited, india " * 300 + " a subsidiary in mauritius "
    assert _resolve_rupee(india) == "INR"
    assert _resolve_rupee("colombo, sri lanka " * 5) == "LKR"
    assert _resolve_rupee("no country named") == "INR"


def test_the_indian_ocean_is_not_india():
    """Phoenix Beverages 2025 names India 16 times, and every one is the Indian Ocean. An island
    filing that names the ocean as often as its own country tied with INR, and the tie went to INR."""
    seychelles = ("victoria, mahe, seychelles " * 10) + ("islands of the indian ocean " * 12)
    assert _resolve_rupee(seychelles) == "SCR"
    assert _resolve_rupee("indian ocean " * 5) == "INR"          # no rupee country: India's, as before
    assert _resolve_rupee("mumbai, india. the indian rupee " * 3) == "INR"
