"""A Latin currency code names a currency only as a word of its own.

The face scan (`normalize._detect_units`) tries `_CCY` in its own order and took the first token
found ANYWHERE in the text. "rs." is in "years.", "others." and "officers.", so a filing whose
scanned pages printed such a word read as rupees before "$" was tried: Alphabet's 2025 10-K came
out "million/INR" and its earnings per share were stamped "INR per share". Measured on the five
reference filings, Asian Paints, Laurus and the three synthetic 10-K filings by replaying the scan on
each run's own pages: only Alphabet moves (INR -> USD).
"""
from __future__ import annotations

import pytest

from app.stages.normalize import _currency_in


@pytest.mark.parametrize("text,code", [
    ("the company and its officers. (in millions, except per share amounts) $ 402,836", "USD"),
    ("for the years. an entrepreneur paid $5", "USD"),
    ("rs. 1,000 crore", "INR"),
    ("(₹ in crores)", "INR"),
    ("(all amounts in crore rupees unless otherwise stated)", "INR"),
    ("hk$'000", "HKD"),
    ("rmb'000", "CNY"),
    ("单位：人民币元", "CNY"),
    ("eur 10 million", "EUR"),
    ("us$ million", "USD"),
])
def test_a_currency_is_read_from_its_own_word(text, code):
    assert _currency_in(text) == code


@pytest.mark.parametrize("text", ["the directors and officers.", "an entrepreneur", "inrush current"])
def test_a_word_containing_a_code_names_no_currency(text):
    assert _currency_in(text) is None
