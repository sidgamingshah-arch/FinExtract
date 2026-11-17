"""A CURRENCY CODE IN A UNITS SIGNAL IS A WORD OF ITS OWN.

`row_reconstruct._unit_signals` takes each signal a rulebook declares and reads the currency off
the signal's own text. The codes were matched as bare substrings, so the "rs" ending "Dollars"
read as rupees: every 10-K statement headed "(Dollars in millions, except per share data)" — a
signal the US GAAP set declares — had its figures stamped INR. Measured on Nike's FY2026 10-K, 166
figures went out INR beside a document unit of USD millions (`stages.normalize` reads the codes as
words and said USD); Welltower's 2024 report read its Schedule III "(Dollars in thousands)" the
same way.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.geometry import BBox
from app.services.row_reconstruct import (
    _CURRENCY_WORDS,
    Word,
    _statement_unit,
    _unit_context,
    _unit_signals,
)

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _currency(signal: str) -> str:
    return next((code for rx, code in _CURRENCY_WORDS if rx.search(signal)), "")


@pytest.mark.parametrize("signal,code", [
    ("Dollars in millions, except", "USD"),
    ("Dollars in thousands", "USD"),
    ("In thousands of dollars", "USD"),
    ("Millions of dollars", "USD"),
    ("$ in millions", "USD"),
    ("US$'000", "USD"),
    ("In millions", ""),
    ("(Amounts in millions, except per share data)", ""),
    ("HK$'000", "HKD"),
    ("Hong Kong dollars in thousands", "HKD"),
    ("RMB million", "CNY"),
    ("人民幣千元", "CNY"),
    ("单位：人民币元", "CNY"),
    ("₹ in Crores", "INR"),
    ("Rs. in crores", "INR"),
    ("Rupees in lakhs", "INR"),
    ("INR in million", "INR"),
    ("EUR million", "EUR"),
    ("GBP'000", "GBP"),
    # A code inside a word names nothing.
    ("years.", ""), ("measure", ""), ("Europe", ""), ("others", ""),
    # Another country's dollar is not a bare "$".
    ("S$ million", ""), ("NT$ thousand", ""),
])
def test_a_signal_names_the_currency_it_spells(signal, code):
    assert _currency(signal) == code


def _shipped_signals(name: str) -> list[str]:
    raw = json.loads((TEMPLATES / name).read_text(encoding="utf-8"))

    def find(node):
        if isinstance(node, dict):
            if "units_and_currency" in node:
                return node["units_and_currency"].get("signals") or []
            return next((r for r in (find(v) for v in node.values()) if r is not None), None)
        if isinstance(node, list):
            return next((r for r in (find(v) for v in node) if r is not None), None)
        return None

    return find(raw) or []


def test_no_us_gaap_signal_reads_as_rupees():
    signals = _shipped_signals("output_csv_usgaap_line_items.json")
    assert signals
    named = {s: _currency(s) for s in signals}
    assert set(named.values()) <= {"USD", ""}, named
    assert named["Dollars in millions, except"] == "USD"
    assert named["In millions, except"] == ""


@pytest.mark.parametrize("name,codes", [
    ("output_csv_hk_line_items.json", {"HKD", "CNY", ""}),
    ("output_csv_indas_line_items.json", {"INR", ""}),
])
def test_the_other_shipped_sets_read_as_before(name, codes):
    named = {s: _currency(s) for s in _shipped_signals(name)}
    assert named and set(named.values()) <= codes, named


def test_a_10k_statement_header_is_read_in_us_dollars():
    from app.schemas.ontology import ScopeSelection

    scope = ScopeSelection()
    scope.units_and_currency.signals = _shipped_signals("output_csv_usgaap_line_items.json")
    header = [[Word(text=t, bbox=BBox(x0=0.1 + 0.08 * i, y0=0.10, x1=0.16 + 0.08 * i, y1=0.11))
               for i, t in enumerate(["(Dollars", "in", "millions,", "except", "per", "share", "data)"])],
              [Word(text="Revenues", bbox=BBox(x0=0.1, y0=0.13, x1=0.2, y1=0.14)),
               Word(text="46,309", bbox=BBox(x0=0.6, y0=0.13, x1=0.7, y1=0.14))]]
    unit = _unit_context(_statement_unit(header, _unit_signals(scope)), 0)
    assert (unit.currency, unit.scale_factor) == ("USD", Decimal(1_000_000))
