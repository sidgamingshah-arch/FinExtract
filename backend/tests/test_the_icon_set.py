"""ICON — the Indian bank credit-monitoring (CMA-style) spread — ships as its own pair and reads.

The pair is BUILT, not hand-edited: ``scripts/build_icon_pair.py`` turns the Company v3 workbook draft
(``scripts/sample_data/icon/``) into ``output_csv_icon_v1_template.json`` and
``output_csv_icon_line_items.json``. These tests hold what that build decided:

* the template is the workbook's arithmetic — every "a - b" a ``diff``, expenses kept POSITIVE, a
  deduction printed after "Less:" still subtracted once, a tax credit still a credit;
* every template line has exactly one item and no item is outside the template;
* the captions a Schedule III / Revised Schedule VI filing prints reach the right line — including
  rows printed under an "Expenses" or "Tax expense" heading, and captions behind "I.", "(a)",
  "Less:" or a "(V-VI)" cross-reference, which the matcher itself does not strip;
* the non-monetary workbook rows are KPIs, which compute;
* on a fresh database the pair seeds and its template answers with its own set.

NOT MEASURED ON A FILING: no Indian filing is in the reference corpus. What is asserted here is the
configuration's arithmetic and caption routing, not figures read off a real annual report.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services import line_item_audit, rollups
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
SET = TEMPLATES / "output_csv_icon_line_items.json"
TEMPLATE = TEMPLATES / "output_csv_icon_v1_template.json"
HK = TEMPLATES / "output_csv_hk_line_items.json"
P, B, C = "pl_icon__", "bs_icon__", "cov_icon__"


def _raw(path=SET) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _template() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def matcher():
    return OntologyMatcher(build_working_view(load_line_item_set(_raw())), locale="en",
                           settings=get_settings())


# ── the pair ─────────────────────────────────────────────────────────────────────────────────────

def test_the_pair_names_itself_and_its_own_template():
    raw, tpl = _raw(), _template()
    assert raw["line_items_key"] == "output_csv_icon"
    assert raw["target_template_key"] == tpl["template_key"] == "output_csv_icon_v1"
    assert tpl["name"] == "ICON"


def test_the_build_is_reproducible():
    """The shipped files are what the build script writes: an edit made to them by hand would be
    lost on the next build, so it belongs in the script or its inputs."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_icon_pair", TEMPLATES.parents[2] / "scripts" / "build_icon_pair.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    template, line_items = mod.build()
    assert template == _template()
    assert line_items == _raw()


def test_every_template_line_has_one_item_and_no_item_is_outside_it():
    raw, tpl = _raw(), _template()
    lines = [c["canonical_key"] for st in tpl["statements"] for s in st["sections"]
             for c in s["children"]]
    assert len(lines) == len(set(lines)) == len(raw["items"]) == 182
    assert {i["key"] for i in raw["items"]} == set(lines)
    assert line_item_audit.keys_outside_the_template(raw, tpl) == []
    assert all(s["children"] for st in tpl["statements"] for s in st["sections"])


def test_the_set_is_internally_sound():
    raw = _raw()
    resolved = load_line_item_set(raw, resolve=True)
    assert line_item_audit.dangling_references(raw) == []
    assert line_item_audit.unsigned_terms(raw) == []
    assert line_item_audit.unbreakable_ties(resolved) == []


def test_provisioning_the_template_keeps_every_authored_item():
    from app.services.provision_line_items import protected_keys, provision

    raw = _raw()
    merged = provision(_template(), raw)
    assert len(merged["items"]) == len(raw["items"])
    assert len(protected_keys(merged)) == 182
    by_key = {i["key"]: i for i in raw["items"]}
    for item in merged["items"]:
        assert {k: v for k, v in item.items() if k != "label_i18n"} == by_key[item["key"]]


def test_the_set_reads_indian_filings_and_does_not_negate_expenses():
    raw = _raw()
    assert raw["prompt"] != _raw(HK)["prompt"] and "CMA" in raw["prompt"]
    assert raw["scope_selection"] and raw["normalisation"]
    sign = raw["global_rules"]["sign_convention"]
    # The CMA workbook subtracts expense lines, so an expense stored negative would be ADDED.
    assert "negate on load" not in sign["unsigned_source"].lower()


# ── arithmetic ───────────────────────────────────────────────────────────────────────────────────

def _row(key, value):
    return {"canonical_key": key, "values": [{
        "basis": "consolidated", "period_label": "current", "value": str(value),
        "confidence": {"flags": []}}]}


def _shown(figures: dict) -> dict:
    return rollups.figures_as_shown(_template(), [_row(k, v) for k, v in figures.items()],
                                    "consolidated", "current")


def test_the_operating_statement_computes_as_the_workbook_does():
    shown = _shown({
        P + "domestic_sales": 800, P + "export_sales": 200,
        P + "excise_duty": -50,                 # printed "Less: Excise duty", stored negative
        P + "other_recurring_operating_income": 30, P + "raw_materials": 400,
        P + "power_and_fuel": 60, P + "depreciation": 40, P + "salary_and_staff_expenses": 100,
        P + "sga_others": 70, P + "interest": 45, P + "interest_received": 12,
        P + "misc_income": 8, P + "current_tax": 40,
        P + "deferred_tax": -15,                # a credit
        P + "equity_dividend": 20})
    assert shown[P + "net_sales"] == 950        # 1000 less |excise|, whichever sign it was stored in
    assert shown[P + "total_operating_income"] == 980
    assert shown[P + "total_operating_expenses"] == 670
    assert shown[P + "operating_profit_before_interest"] == 310
    assert shown[P + "operating_profit_after_interest"] == 265
    assert shown[P + "profit_before_tax"] == 285
    assert shown[P + "provision_for_taxes"] == 25
    assert shown[P + "profit_after_tax"] == 260  # the deferred-tax credit reduces tax, not profit
    assert shown[P + "retained_profit"] == 240


def test_the_balance_sheet_reaches_its_totals_through_printed_groups():
    """A filing that prints only 'Trade receivables' and 'Inventories' — not their CMA breakdown —
    still reaches Total Current Assets, because the total rolls up through the groups."""
    shown = _shown({
        B + "bank_borrowings_applicant_bank": 150, B + "sundry_creditors_trade": 120,
        B + "cpltd_rupee_term_loans": 30, B + "rupee_term_loans": 200,
        B + "share_capital": 100, B + "other_reserves": 250, B + "revaluation_reserve": 10,
        B + "cash_and_bank_balances": 40, B + "receivables": 180, B + "inventory": 160,
        B + "land_building_plant_machinery": 420, B + "capital_work_in_progress": 30,
        B + "accumulated_depreciation": -100})
    assert shown[B + "total_current_liabilities"] == 300
    assert shown[B + "total_outside_liabilities"] == 500
    assert shown[B + "net_worth"] == 360
    assert shown[B + "total_current_assets"] == 380
    assert shown[B + "net_block"] == 350
    assert shown[B + "total_assets"] == 730
    assert shown[B + "total_liabilities_and_net_worth"] == 860
    assert shown[B + "difference_in_bs"] == -130
    assert shown[B + "tangible_net_worth"] == 350
    assert shown[B + "net_working_capital"] == 80


def test_the_workbook_ratios_are_kpis_and_compute():
    from app.services.derived import compute_ratios

    rows = [_row(k, v) for k, v in {
        B + "sundry_creditors_trade": 325, B + "receivables": 400, B + "rupee_term_loans": 215,
        B + "share_capital": 100, B + "other_reserves": 250, P + "domestic_sales": 300,
        P + "equity_dividend": 20}.items()]
    got = {r["key"]: r.get("value") for r in compute_ratios(rows, template_def=_template())}
    assert got["current_ratio"] == pytest.approx(1.23, abs=0.01)
    assert got["tol_to_tnw"] == pytest.approx(540 / 350, abs=0.01)
    assert got["ttl_to_tnw"] == pytest.approx(215 / 350, abs=0.01)
    assert got["dividend_rate"] == pytest.approx(20.0)


# ── captions ─────────────────────────────────────────────────────────────────────────────────────

CAPTIONS = [
    # (statement, the heading the row is printed under, caption, line)
    ("profit_and_loss", None, "Revenue from operations", P + "total_operating_income"),
    ("profit_and_loss", None, "I. Revenue from operations", P + "total_operating_income"),
    ("profit_and_loss", "Income", "II. Other income", P + "misc_income"),
    ("profit_and_loss", "Expenses", "Cost of materials consumed", P + "raw_materials"),
    ("profit_and_loss", "IV. Expenses", "Employee benefits expense", P + "salary_and_staff_expenses"),
    ("profit_and_loss", "Expenses", "Finance costs", P + "interest"),
    ("profit_and_loss", "Expenses", "Depreciation and amortisation expense", P + "depreciation"),
    ("profit_and_loss", "Expenses", "Other expenses", P + "sga_others"),
    ("profit_and_loss", "Tax expense:", "(1) Current tax", P + "current_tax"),
    ("profit_and_loss", "Tax expense:", "Deferred tax", P + "deferred_tax"),
    ("profit_and_loss", None, "Profit before tax (V-VI)", P + "profit_before_tax"),
    ("profit_and_loss", "Tax expense:", "Profit for the year", P + "profit_after_tax"),
    ("profit_and_loss", None, "Less: Excise duty", P + "excise_duty"),
    ("profit_and_loss", None, "Power & fuel", P + "power_and_fuel"),
    ("balance_sheet", "Current liabilities", "Trade payables", B + "sundry_creditors_trade"),
    ("balance_sheet", "Current liabilities", "Borrowings", B + "bank_borrowings_applicant_bank"),
    ("balance_sheet", "Non-current liabilities", "Borrowings", B + "rupee_term_loans"),
    ("balance_sheet", "Current liabilities", "Current maturities of long-term borrowings",
     B + "cpltd_rupee_term_loans"),
    ("balance_sheet", "Equity", "Other equity", B + "other_reserves"),
    ("balance_sheet", "Non-current assets", "Property, plant and equipment",
     B + "land_building_plant_machinery"),
    ("balance_sheet", "Current assets", "(a) Inventories", B + "inventory"),
    ("balance_sheet", "Current assets", "Total assets", B + "total_assets"),
    ("balance_sheet", "Current liabilities", "Total equity and liabilities",
     B + "total_liabilities_and_net_worth"),
    ("balance_sheet", "Current liabilities", "Bank guarantees", C + "outstanding_bgs"),
    (None, None, "Claims against the company not acknowledged as debts",
     C + "other_liabilities_not_provided"),
]


@pytest.mark.parametrize("statement, heading, caption, key", CAPTIONS,
                         ids=[f"{c[2]}@{c[1]}" for c in CAPTIONS])
def test_a_printed_caption_reaches_its_line(matcher, statement, heading, caption, key):
    assert matcher.match(caption, statement=statement, section=heading).canonical_key == key


@pytest.mark.parametrize("statement, heading, caption", [
    ("profit_and_loss", "Other comprehensive income", "Remeasurement of defined benefit plans"),
    ("profit_and_loss", "Other comprehensive income", "Current tax"),
    ("profit_and_loss", "Expenses", "Bank guarantees"),
])
def test_a_caption_outside_its_lines_scope_is_refused(matcher, statement, heading, caption):
    assert matcher.match(caption, statement=statement, section=heading).canonical_key is None


# ── shipped ──────────────────────────────────────────────────────────────────────────────────────

def test_the_pair_seeds_and_its_template_answers_with_its_own_set():
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import LineItemVersion  # noqa: F401 — registers the tables on Base
    from app.sample.reference import ensure_reference_data
    from app.services.config_select import select_for_template

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{pathlib.Path(tmp) / 'icon.db'}")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            ensure_reference_data(session)
            icon = select_for_template(session, "output_csv_icon_v1")
            assert icon is not None and icon.line_items_key == "output_csv_icon"
            assert len((icon.definition or {}).get("items") or []) == 182
            assert select_for_template(session, "output_csv_hk_v1").line_items_key == "output_csv_hk"
        finally:
            session.close()
            engine.dispose()
