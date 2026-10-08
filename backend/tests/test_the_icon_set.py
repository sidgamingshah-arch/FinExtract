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
configuration's arithmetic and caption routing, and one run of the whole pipeline over a GENERATED
Schedule III balance sheet and P&L (`_schedule_iii_pdf`) — every printed total of which the spread
must reproduce — not figures read off a real annual report. The two engine defects that run exposed
(a title printed with its date, a row opened by "(1)") are fixed for Indian filings and pinned at the
end.
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
    # A heading that prints no figure, glued onto the row beside it by row reconstruction — the
    # Schedule III face layout produces every one of these (see `_GLUED_BEFORE` in the builder).
    ("balance_sheet", "Non-current liabilities", "(a) Financial liabilities (i) Borrowings",
     B + "rupee_term_loans"),
    ("balance_sheet", "Current liabilities", "(a) Financial liabilities (i) Borrowings",
     B + "bank_borrowings_applicant_bank"),
    ("balance_sheet", "Current assets", "(b) Financial assets (ii) Trade receivables",
     B + "receivables"),
    ("balance_sheet", "Equity", "Equity (a) Equity share capital", B + "share_capital"),
    ("balance_sheet", "Equity", "Total equity Liabilities", B + "net_worth"),
    ("profit_and_loss", "Expenses", "IV. Expenses Cost of materials consumed", P + "raw_materials"),
    ("profit_and_loss", "Tax expense:", "VI. Tax expense: (1) Current tax", P + "current_tax"),
    # …and the "&" spelling the glued row collides with, told apart on the printed text.
    ("balance_sheet", "Current liabilities", "Total equity & liabilities",
     B + "total_liabilities_and_net_worth"),
    ("balance_sheet", "Current assets", "(iv) Bank balances other than (iii) above",
     B + "fixed_deposits_with_banks"),
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


@pytest.mark.parametrize("heading, caption", [
    ("Current assets", "(d) Other current assets"),
    ("Current liabilities", "Other current liabilities and provisions"),
])
def test_a_face_aggregate_over_a_catch_all_is_left_for_the_sweep(matcher, heading, caption):
    """Bound to its calculated line, the printed figure is REPLACED by the computed one the moment
    the sweep fills the catch-all beneath it — total current assets came out short by exactly that
    row. Unbound, the row is swept into the catch-all with the section's other leftovers."""
    assert matcher.match(caption, statement="balance_sheet", section=heading).canonical_key is None


def test_every_catch_all_sweeps_one_section():
    """`stages/residual` refuses a residual whose own scope and its policy's scope differ ("never
    spans sections"). Left to the default policy (the line's `inherits`, an ICON section id) every
    catch-all differed from its filing scope (bs_cl, bs_ca, ...) and swept nothing."""
    view = build_working_view(load_line_item_set(_raw()))
    residuals = [m for m in view.mappings if m.value_scope == "exclusive_residual"]
    assert sorted(m.canonical_key for m in residuals) == sorted([
        B + "other_current_liabilities_others", B + "net_worth_others",
        B + "other_current_assets_others", B + "other_non_current_assets_others"])
    for m in residuals:
        scopes = set(m.section_scope or []) | {m.residual_policy.section_scope}
        assert len(scopes) == 1, (m.canonical_key, scopes)


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


# ── a Schedule III filing, end to end ───────────────────────────────────────────────────────────
#
# A generated standalone balance sheet and P&L in the Division II layout — the headings that print
# no figure ("(d) Financial assets", "Equity", "Liabilities", "IV. Expenses"), a creditor caption
# wrapped over two lines, the note column — with figures that tie, so every printed total is a
# figure the spread must reproduce. Rupees in lakhs; current year, previous year.

_BS = [
    ("h", "ASSETS"), ("h", "Non-current assets"),
    ("r", "(a) Property, plant and equipment", "3", 4200, 3900),
    ("r", "(b) Capital work-in-progress", "3", 300, 250),
    ("r", "(c) Other intangible assets", "4", 100, 120),
    ("h", "(d) Financial assets"),
    ("r", "(i) Investments", "5", 500, 450),
    ("r", "(ii) Other financial assets", "6", 80, 70),
    ("r", "(e) Other non-current assets", "7", 120, 110),
    ("t", "Total non-current assets", "", 5300, 4900),
    ("h", "Current assets"),
    ("r", "(a) Inventories", "8", 1800, 1600),
    ("h", "(b) Financial assets"),
    ("r", "(i) Investments", "9", 200, 150),
    ("r", "(ii) Trade receivables", "10", 2100, 1900),
    ("r", "(iii) Cash and cash equivalents", "11", 350, 300),
    ("r", "(iv) Bank balances other than (iii) above", "12", 150, 100),
    ("r", "(v) Loans", "13", 60, 50),
    ("r", "(vi) Other financial assets", "6", 40, 50),
    ("r", "(c) Current tax assets (net)", "", 30, 20),
    ("r", "(d) Other current assets", "14", 270, 230),
    ("t", "Total current assets", "", 5000, 4400),
    ("t", "TOTAL ASSETS", "", 10300, 9300),
    ("h", "EQUITY AND LIABILITIES"), ("h", "Equity"),
    ("r", "(a) Equity share capital", "15", 1000, 1000),
    ("r", "(b) Other equity", "16", 4300, 3800),
    ("t", "Total equity", "", 5300, 4800),
    ("h", "Liabilities"), ("h", "Non-current liabilities"), ("h", "(a) Financial liabilities"),
    ("r", "(i) Borrowings", "17", 1500, 1700),
    ("r", "(ii) Lease liabilities", "18", 100, 110),
    ("r", "(b) Provisions", "19", 150, 140),
    ("r", "(c) Deferred tax liabilities (net)", "20", 250, 230),
    ("t", "Total non-current liabilities", "", 2000, 2180),
    ("h", "Current liabilities"), ("h", "(a) Financial liabilities"),
    ("r", "(i) Borrowings", "21", 1200, 900),
    ("h", "(ii) Trade payables"),
    ("r", "- total outstanding dues of micro enterprises and small enterprises", "22", 150, 120),
    ("r", "- total outstanding dues of creditors other than micro enterprises and small "
          "enterprises", "22", 1050, 900),
    ("r", "(iii) Other financial liabilities", "23", 220, 200),
    ("r", "(b) Other current liabilities", "24", 230, 120),
    ("r", "(c) Provisions", "19", 100, 60),
    ("r", "(d) Current tax liabilities (net)", "", 50, 20),
    ("t", "Total current liabilities", "", 3000, 2320),
    ("t", "TOTAL EQUITY AND LIABILITIES", "", 10300, 9300),
]


def _pl(current_tax: str) -> list:
    return [
        ("r", "I. Revenue from operations", "25", 12000, 10500),
        ("r", "II. Other income", "26", 300, 250),
        ("t", "III. Total income (I+II)", "", 12300, 10750),
        ("h", "IV. Expenses"),
        ("r", "Cost of materials consumed", "27", 6500, 5800),
        ("r", "Changes in inventories of finished goods, work-in-progress and stock-in-trade",
         "28", -150, -100),
        ("r", "Employee benefits expense", "29", 1500, 1350),
        ("r", "Finance costs", "30", 280, 300),
        ("r", "Depreciation and amortisation expense", "3", 450, 400),
        ("r", "Other expenses", "31", 1920, 1650),
        ("t", "Total expenses (IV)", "", 10500, 9400),
        ("t", "V. Profit before tax (III-IV)", "", 1800, 1350),
        ("h", "VI. Tax expense:"),
        ("r", f"{current_tax} Current tax", "32", 330, 240),
        ("r", ("(2)" if current_tax == "(1)" else "(b)") + " Deferred tax", "32", 20, 10),
        ("t", "VII. Profit for the year (V-VI)", "", 1450, 1100),
    ]


def _schedule_iii_pdf(*, title_with_date: bool = False, current_tax: str = "(a)",
                      plain_titles: bool = False) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    import io

    def amount(v):
        return f"({abs(v):,})" if v < 0 else f"{v:,}"

    def page(c, title, period, rows):
        w, h = A4
        y = h - 60
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, "SAMPLE INDUSTRIES LIMITED")
        c.drawString(50, y - 16, title)
        c.setFont("Helvetica", 9)
        c.drawString(50, y - 30, period)
        c.drawRightString(w - 50, y - 30, "(Rupees in lakhs)")
        y -= 50
        c.setFont("Helvetica-Bold", 9)
        for x, text in ((50, "Particulars"), (385, "Note"), (470, "31 March 2025"),
                        (w - 50, "31 March 2024")):
            (c.drawString if x == 50 else c.drawRightString)(x, y, text)
        y -= 14
        for kind, label, *figures in rows:
            c.setFont("Helvetica-Bold" if kind in "ht" else "Helvetica", 8.5)
            if len(label) > 64:                       # wraps, figures on the last line
                cut = label.rfind(" ", 0, 64)
                c.drawString(62, y, label[:cut])
                y -= 11
                label = label[cut + 1:]
            c.drawString(50 if kind != "r" or not label.startswith(("(", "-")) else 62, y, label)
            if figures:
                note, cy, py = figures
                c.drawRightString(385, y, note)
                c.drawRightString(470, y, amount(cy))
                c.drawRightString(w - 50, y, amount(py))
            y -= 13
        c.showPage()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(A4[0] / 2, A4[1] / 2, "SAMPLE INDUSTRIES LIMITED")
    c.showPage()
    prefix = "" if plain_titles else "STANDALONE "
    page(c, "Standalone Balance Sheet as at 31 March 2025" if title_with_date
         else f"{prefix}BALANCE SHEET", "As at 31 March 2025", _BS)
    page(c, "Standalone Statement of Profit and Loss for the year ended 31 March 2025"
         if title_with_date else f"{prefix}STATEMENT OF PROFIT AND LOSS",
         "For the year ended 31 March 2025", _pl(current_tax))
    c.save()
    return buf.getvalue()


def _spread(pdf: bytes) -> tuple[dict, list[dict]]:
    """The run's rows as the API serialises them, and the ICON grid's current-year figures."""
    from app.api.routes.extractions import _serialize_rows
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction

    cfg = load_line_item_set(_raw(), resolve=True)
    doc, _ctx = run_extraction(pdf, filename="schedule_iii.pdf", ontology=build_working_view(cfg),
                               template=load_template(_template()), line_items=cfg)
    rows = _serialize_rows(doc)
    return rollups.figures_as_shown(_template(), rows, "standalone", "current"), rows


@pytest.fixture(scope="module")
def schedule_iii():
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    return _spread(_schedule_iii_pdf())


def test_a_schedule_iii_balance_sheet_reaches_every_printed_total(schedule_iii):
    shown, _rows = schedule_iii
    assert {k: shown.get(B + k) for k in (
        "bank_borrowings_applicant_bank", "sundry_creditors_trade", "total_current_liabilities",
        "rupee_term_loans", "total_term_liabilities", "share_capital", "other_reserves",
        "net_worth", "total_liabilities_and_net_worth", "receivables", "inventory",
        "total_current_assets", "net_block", "total_assets", "difference_in_bs")} == {
        "bank_borrowings_applicant_bank": 1200, "sundry_creditors_trade": 150 + 1050,
        "total_current_liabilities": 3000, "rupee_term_loans": 1500, "total_term_liabilities": 2000,
        "share_capital": 1000, "other_reserves": 4300, "net_worth": 5300,
        "total_liabilities_and_net_worth": 10300, "receivables": 2100, "inventory": 1800,
        "total_current_assets": 5000, "net_block": 4500, "total_assets": 10300,
        "difference_in_bs": 0}
    # The catch-alls took the section leftovers, and nothing a section prints as a total.
    assert shown[B + "other_current_assets"] == 270 + 200 + 60 + 40
    assert shown[B + "other_current_liabilities_others"] == 220 + 230 + 100
    assert shown[B + "total_other_non_current_assets"] == 500 + 80 + 120


def test_a_schedule_iii_pnl_reaches_its_lines(schedule_iii):
    shown, _rows = schedule_iii
    assert {k: shown.get(P + k) for k in (
        "total_operating_income", "raw_materials", "changes_in_inventory",
        "salary_and_staff_expenses", "interest", "depreciation", "sga_others", "misc_income",
        "current_tax", "deferred_tax", "profit_before_tax", "profit_after_tax")} == {
        "total_operating_income": 12000, "raw_materials": 6500, "changes_in_inventory": -150,
        "salary_and_staff_expenses": 1500, "interest": 280, "depreciation": 450,
        "sga_others": 1920, "misc_income": 300, "current_tax": 330, "deferred_tax": 20,
        "profit_before_tax": 1800, "profit_after_tax": 1450}


def test_a_title_printed_with_its_date_is_still_a_statement():
    """'Standalone Balance Sheet as at 31 March 2025' on one line is the page's title (an Indian
    filing; `services.regime`)."""
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    shown, _rows = _spread(_schedule_iii_pdf(title_with_date=True))
    assert shown.get(B + "total_assets") == 10300


def test_a_numbered_tax_row_reaches_its_line():
    """Schedule III's own '(1) Current tax' reaches its line; '(1)' is not the amount -1."""
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    shown, _rows = _spread(_schedule_iii_pdf(current_tax="(1)"))
    assert shown.get(P + "current_tax") == 330


def test_a_plain_balance_sheet_title_is_the_companys():
    """A Division I filer with no subsidiaries titles its statements "Balance Sheet", with no
    "Standalone". Schedule III titles a group's statements "Consolidated ...", so on an Indian
    filing a title naming no entity is the company's; it used to keep the consolidated default and
    the standalone spread, the one a CMA is read from, came out empty."""
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    shown, _rows = _spread(_schedule_iii_pdf(plain_titles=True))
    assert shown.get(B + "total_assets") == 10300
    assert shown.get(P + "profit_after_tax") == 1450


def test_every_line_the_model_is_asked_about_is_supplied_what_it_reads():
    """THE LLM ROUTE CAN ONLY CITE WHAT A REQUEST CARRIES. A request for a line with a note set
    carries notes and no statement rows (`line_item_requests.plan_requests`), and a note's TEXT
    travels only for a line declaring a `note_source` (`note_context.identified_notes`). So an
    asked-about ICON line either reads the face (`route: face`, statement rows supplied) or names
    the note headings it lives under; one doing neither is sent note numbers with nothing under
    them. And no line declares `anywhere`, which attaches every non-statement page of an annual
    report to its request."""
    from app.services.line_item_requests import asked_about

    cfg = load_line_item_set(_raw(), resolve=True)
    asked = [i for i in cfg.items if asked_about(i)]
    unsupplied = [i.key for i in asked
                  if i.route != "face"
                  and not list(getattr(i.note_source, "note_terms", None) or ())]
    assert not unsupplied, unsupplied
    assert not [i.key for i in cfg.items if i.route == "anywhere"]
    assert not [i.key for i in asked if i.route == "face" and i.note_source is not None]
