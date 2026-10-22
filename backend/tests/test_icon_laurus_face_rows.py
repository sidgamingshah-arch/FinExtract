"""Four Laurus Labs face rows the ICON set missed, and where they belong (laurus_labs_2025_26_ind).

Measured on the consolidated face (Rs crore, current / prior), deterministic route:

* PROFIT BEFORE TAX computed 1,347.87 / 574.48 against a printed 1,181.88 / 484.29. Exactly two
  rows were left unbound: "Purchase of traded goods" 172.56 / 105.36 (the raw-materials line
  vetoed every purchases caption) and "Finance income" (6.57) / (15.17), printed in brackets in the
  block deducted from EBITDA. Purchases of stock-in-trade now reach raw materials, the CMA place
  for a trader's purchases (the stock-in-trade movement is already on changes in inventories);
  finance income reaches interest received, which is declared a magnitude so the bracket of the
  expense block does not turn an income into a charge. Computed PBT = 1,181.88 / 484.29.
* PROFIT AFTER TAX was never bound: the after-share row ("VIII. Profit for the year after share of
  profit/(loss) from associates and", its tail cut off by row reconstruction) hit the exclude
  hint `share\\s+of`. It binds now; the share row itself and the before-share row still do not.
  The share Laurus prints BELOW tax (0.29 / 3.93) does not reach the computed figure: the fixed
  template's rollup is PBT less taxes, the configured terms must equal it
  (`test_formula_in_config`), and routing the share through non-operating income instead would
  move computed PBT off the printed 1,181.88 / 484.29 by the same amounts. Held back; pinned below.
* CURRENT "Loans" 0.93 was swept into other current assets' {Others}; its own line is other
  advances recoverable (the definition names current 'Loans'). Current "Other financial assets" is
  a mixed caption no ICON line names, so it stays in the sweep.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import rollups
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.normalize import NormalizeStage

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
P, B = "pl_icon__", "bs_icon__"


def _raw() -> dict:
    return json.loads((TEMPLATES / "output_csv_icon_line_items.json").read_text(encoding="utf-8"))


def _template() -> dict:
    return json.loads((TEMPLATES / "output_csv_icon_v1_template.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def view():
    return build_working_view(load_line_item_set(_raw()))


@pytest.fixture(scope="module")
def matcher(view):
    return OntologyMatcher(view, locale="en", settings=get_settings())


@pytest.mark.parametrize("statement, heading, caption, key", [
    ("profit_and_loss", None,
     "VIII. Profit for the year after share of profit/(loss) from associates and", P + "profit_after_tax"),
    ("profit_and_loss", None, "Profit for the year after share of profit of associates",
     P + "profit_after_tax"),
    ("profit_and_loss", "Tax expense",
     "Profit after tax and share of profit/(loss) of associates and joint ventures",
     P + "profit_after_tax"),
    ("profit_and_loss", None, "Profit for the year", P + "profit_after_tax"),
    # The reviewed hint, for a spelling no alias carries ("and" optional after the tax).
    ("profit_and_loss", None, "Net profit after taxation and share of results of associates",
     P + "profit_after_tax"),
    ("profit_and_loss", None, "Profit after tax share of profit of joint ventures",
     P + "profit_after_tax"),
    ("profit_and_loss", "Expenses", "Purchase of traded goods", P + "raw_materials"),
    ("profit_and_loss", "Expenses", "Purchases of stock-in-trade", P + "raw_materials"),
    ("profit_and_loss", "Expenses", "Cost of materials consumed", P + "raw_materials"),
    ("profit_and_loss", "Income", "Finance income", P + "interest_received"),
    ("profit_and_loss", None, "Share of profit in Associates",
     P + "non_operating_income_non_cash_others"),
    ("balance_sheet", "Current assets", "Loans", B + "other_advances_recoverable"),
])
def test_the_laurus_face_rows_reach_their_lines(matcher, statement, heading, caption, key):
    assert matcher.match(caption, statement=statement, section=heading).canonical_key == key


@pytest.mark.parametrize("statement, heading, caption", [
    # The before-share subtotal and the share itself are not profit after tax.
    ("profit_and_loss", None,
     "VI. Profit for the year before share of profit/(loss) from associates and"),
    ("profit_and_loss", None,
     "joint venture (IV-V) VII. Share of profit/(loss) from associates and joint venture, net of tax"),
    ("profit_and_loss", None, "Profit for the year attributable to owners"),
    # A mixed caption no ICON line names: the sweep keeps it.
    ("balance_sheet", "Current assets", "Other financial assets"),
    # Non-current and liability "Loans" are other lines' business.
    ("balance_sheet", "Non-current assets", "Loans"),
    ("balance_sheet", "Current liabilities", "Loans"),
])
def test_the_rows_beside_them_do_not(matcher, statement, heading, caption):
    assert matcher.match(caption, statement=statement, section=heading).canonical_key is None


def _row(label: str, key: str, value: str) -> tuple[DocumentModel, LineItem]:
    li = LineItem(source_label=label, canonical_key=key)
    li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                basis=Basis.CONSOLIDATED, period_label="current",
                                provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf", locale="en")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    doc.line_items = [li]
    return doc, li


def _normalised(view, label, key, value) -> tuple[Decimal, list[str]]:
    doc, li = _row(label, key, value)
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = view
    NormalizeStage().run(doc, ctx)
    ev = next(iter(li.values.values()))
    return ev.value, ev.confidence.flags


def test_interest_received_is_a_magnitude_wherever_the_face_brackets_it(view):
    value, flags = _normalised(view, "Finance income", P + "interest_received", "-6.57")
    assert value == Decimal("6.57")
    assert "sign_rule:natural_positive" in flags and "sign_normalised" in flags
    value, flags = _normalised(view, "Interest income", P + "interest_received", "12.40")
    assert value == Decimal("12.40") and "sign_normalised" not in flags
    # Only the line that declares it: any other line keeps its sign and is flagged instead.
    value, _ = _normalised(view, "Other income", P + "misc_income", "-6.57")
    assert value == Decimal("-6.57")


def _figure(key, label, value, y):
    return {"canonical_key": key, "source_label": label, "values": [{
        "basis": "consolidated", "period_label": "current", "value": str(value),
        "confidence": {"flags": []},
        "provenance": {"page_index": 137, "bbox": {"x0": 0.1, "y0": y, "x1": 0.2, "y1": y + 0.01}}}]}


def test_the_laurus_profit_and_loss_reaches_its_printed_profit_before_tax():
    rows = [
        _figure(P + "total_operating_income", "Revenue from operations", "6812.90", 0.10),
        _figure(P + "misc_income", "Other income", "48.38", 0.12),
        _figure(P + "raw_materials", "Cost of materials consumed", "2794.34", 0.16),
        _figure(P + "raw_materials", "Purchase of traded goods", "172.56", 0.18),
        _figure(P + "changes_in_inventory", "Changes in inventories", "-271.44", 0.20),
        _figure(P + "salary_and_staff_expenses", "Employee benefits expense", "895.45", 0.22),
        _figure(P + "sga_others", "Other expenses", "1444.28", 0.24),
        _figure(P + "depreciation", "Depreciation and amortisation expense", "480.05", 0.28),
        _figure(P + "interest_received", "Finance income", "6.57", 0.30),
        _figure(P + "interest", "Finance costs", "170.73", 0.32),
        _figure(P + "current_tax", "Current tax", "297.52", 0.36),
        _figure(P + "deferred_tax", "Deferred tax", "-5.49", 0.38),
    ]
    shown = rollups.figures_as_shown(_template(), rows, "consolidated", "current")
    assert shown[P + "raw_materials"] == pytest.approx(2966.90)
    assert shown[P + "profit_before_tax"] == pytest.approx(1181.88)
    # Profit before tax less tax is the row BEFORE the share of associates (889.85); the printed
    # profit after tax (890.14) carries the 0.29 share printed below tax, which the template's
    # cascade (PBT less taxes) does not reach. Pinned so a change to that cascade is seen here.
    assert shown[P + "profit_after_tax"] == pytest.approx(889.85)
