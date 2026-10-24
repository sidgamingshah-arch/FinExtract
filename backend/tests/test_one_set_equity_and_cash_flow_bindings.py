"""PetroChina's IFRS equity and cash flow reach their printed totals — and the filings that share a cause.

Measured answer-free at 12be7b2 (RMB million, current / prior, printed in brackets):

* TOTAL EQUITY 1,364,783 / 1,286,931 (1,709,623 / 1,635,296). The face prints Share capital,
  Retained earnings and Reserves BESIDE each other and adds all three into the owners' total.
  'Retained earnings' bound nothing (the line carried 'Retained earning') and was swept into Other
  Reserves; once it binds Retained Profits, the containment pass unfiled 'Reserves' as the
  undifferentiated aggregate of a component the face prints. The face's own subtotal proves the two
  are siblings, so the aggregate stays (`MapOntologyStage._printed_beside`).
* NET CASH FLOWS FROM OPERATING ACTIVITIES 461,824 / 519,430 (406,532 / 456,847). 'Income taxes
  paid' bound the DIRECT-method part `sub__cf_direct_income_taxes_paid`, which no indirect total
  reaches; 佳明 2025/26's 'Income tax paid 已付稅項' did the same (prior 743,096 against 461,376).
* NET CASH FLOWS USED FOR FINANCING ACTIVITIES -93,349 / -64,304 (-178,876 / -146,862): 'Dividends
  paid to owners of the Company' was refused as an attribution caption, and 'TRANSLATION OF FOREIGN
  CURRENCY' was swept in as a financing cash flow. Hon Kwok 2023/24 swept its 'Effect of foreign
  exchange rates changes, net' and the two rows BELOW its closing cash ('Cash and bank balances',
  'Non-pledged time deposits') the same way: 1,275,800 against a printed 36,711.
* TOTAL NON-CURRENT ASSETS 2,163,931 (2,161,907): the column header 'December December Notes' was
  read as a row whose figure was the year 2024.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.row_reconstruct import _is_noise_row
from app.services.structural_checks import _signs_established
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"

RETAINED = "bs_equity__retained_profits"
RESERVES_PART = "sub__equity_reserves"
OTHER_FINANCING = "cf_financing__other_financing_cash_flows"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped))


def _row(caption, current, prior=None, page=0, section=None, role=LineRole.LINE):
    row = LineItem(source_label=caption, role=role, section_hint=section)
    for period, value in (("current", current), ("prior", prior)):
        if value is not None:
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                         value=Decimal(value), value_raw=Decimal(value),
                                         provenance=Provenance(page_index=page)))
    return row


def _run(rows, pages, shipped):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = pages
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    return doc


def _face(statement, index=0):
    return PageSource(index=index, kind=PageKind.FACE, statement=statement)


def _swept(row) -> bool:
    return row.confidence.method == "residual"


# ── the equity block ───────────────────────────────────────────────────────────────────────────

def _petrochina_equity(page=0):
    eq = "EQUITY"
    return [
        _row("Share capital", "183021", "183021", page, eq),
        _row("Retained earnings", "987271", "919700", page, eq),
        _row("Reserves", "344840", "348365", page, eq),
        _row("TOTAL EQUITY ATTRIBUTABLE TO OWNERS OF THE COMPANY", "1515132", "1451086", page, eq),
        _row("NON-CONTROLLING INTERESTS", "194491", "184210", page, eq),
        _row("TOTAL EQUITY", "1709623", "1635296", page, eq, role=LineRole.TOTAL),
    ]


def test_retained_earnings_binds_retained_profits(matcher):
    res = matcher.match("Retained earnings", statement="balance_sheet", section="EQUITY")
    assert res.canonical_key == RETAINED, res


def test_reserves_printed_beside_retained_earnings_both_stand(shipped):
    """The owners' total on the same page adds Share capital, Retained earnings AND Reserves, so
    'Reserves' is not the aggregate that holds retained earnings: neither is unfiled or swept."""
    rows = _petrochina_equity()
    _run(rows, [_face("balance_sheet")], shipped)
    retained, reserves = rows[1], rows[2]
    assert retained.canonical_key == RETAINED, (retained.canonical_key, retained.confidence.flags)
    assert not _swept(retained)
    assert reserves.canonical_key == RESERVES_PART, (reserves.canonical_key,
                                                      reserves.confidence.flags)
    assert any(f.startswith("printed_beside_components:") for f in reserves.confidence.flags)


def test_reserves_holding_a_component_printed_beside_it_is_still_unfiled(shipped):
    """The arithmetic decides, not the page: when the total adds up WITHOUT the component, the
    component is inside the aggregate and the aggregate is unfiled as before."""
    eq = "EQUITY"
    rows = [_row("Share capital", "100", "100", 0, eq),
            _row("Reserves", "500", "400", 0, eq),
            _row("Retained profits", "300", "250", 0, eq),
            _row("TOTAL EQUITY", "600", "500", 0, eq, role=LineRole.TOTAL)]
    _run(rows, [_face("balance_sheet")], shipped)
    assert rows[1].canonical_key is None, (rows[1].canonical_key, rows[1].confidence.flags)
    assert any(f.startswith("unfiled_aggregate:") for f in rows[1].confidence.flags)


def test_reserves_whose_components_are_on_another_page_is_still_unfiled(shipped):
    """China SCE / 佳明: one undifferentiated 'Reserves' on the face, its components the closing
    balances of the statement of changes in equity on another page."""
    eq = "EQUITY"
    rows = [_row("Share capital", "100", "100", 0, eq),
            _row("Reserves", "500", "400", 0, eq),
            _row("TOTAL EQUITY", "600", "500", 0, eq, role=LineRole.TOTAL),
            _row("Retained profits", "300", "250", 1, eq)]
    _run(rows, [_face("balance_sheet", 0), _face("balance_sheet", 1)], shipped)
    assert rows[1].canonical_key is None, (rows[1].canonical_key, rows[1].confidence.flags)


# ── the operating section ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("caption", ["Income taxes paid", "Income tax paid",
                                     "Income tax paid 已付稅項", "Hong Kong profits tax paid",
                                     "Overseas taxes paid"])
def test_an_english_tax_paid_row_binds_the_indirect_line(matcher, caption):
    res = matcher.match(caption, statement="cash_flow",
                        section="CASH FLOWS FROM OPERATING ACTIVITIES")
    assert res.canonical_key == "cf_oper_indirect__income_taxes_paid_indirect", res


def test_the_depreciation_add_back_binds_the_cash_flow_line(matcher):
    """Lai Sun 2024/25 and Far East Horizon 2024: 'Depreciation of property, plant and equipment'
    bound only the P&L's part `sub__cfo_depreciation`, so the cash flow's Depreciation line (and
    the operating total) missed 306,456 and 3,151,116. Deprec & Impairment(Oper Exp) still reads
    the cash flow line as its fallback rung P6."""
    res = matcher.match("Depreciation of property, plant and equipment", statement="cash_flow",
                        section="CASH FLOWS FROM OPERATING ACTIVITIES")
    assert res.canonical_key == "cf_oper_indirect__depreciation", res


def test_the_mainland_direct_method_row_keeps_its_part(matcher):
    res = matcher.match("支付的各项税费", statement="cash_flow", section="经营活动产生的现金流量")
    assert res.canonical_key == "sub__cf_direct_income_taxes_paid", res


# ── the financing section and the roll-forward ─────────────────────────────────────────────────

@pytest.mark.parametrize("caption,key", [
    ("Dividends paid to owners of the Company", "cf_financing__dividends_paid_fin"),
    ("Dividend paid", "cf_financing__dividends_paid_fin"),
    ("NET CASH FLOWS USED FOR FINANCING ACTIVITIES",
     "cf_financing__cash_flows_from_finance_activities"),
    ("Net cash used in financing activities 融資活動所用現金淨額",
     "cf_financing__cash_flows_from_finance_activities"),
    ("NET CASH FLOWS USED FOR INVESTING ACTIVITIES",
     "cf_investing__cash_flows_from_invest_activities"),
    ("Net cash used in investing activities 投資活動所用現金淨額",
     "cf_investing__cash_flows_from_invest_activities"),
    ("TRANSLATION OF FOREIGN CURRENCY", "cf_financing__net_foreign_exchange_difference"),
    ("Effect of foreign exchange rates changes, net",
     "cf_financing__net_foreign_exchange_difference"),
    ("Effect of exchange rate changes on cash and cash equivalents",
     "cf_financing__net_foreign_exchange_difference"),
])
def test_financing_and_roll_forward_captions_bind(matcher, caption, key):
    banner = ("CASH FLOWS FROM INVESTING ACTIVITIES" if key.startswith("cf_investing")
              else "CASH FLOWS FROM FINANCING ACTIVITIES")
    res = matcher.match(caption, statement="cash_flow", section=banner)
    assert res.canonical_key == key, res


def test_a_cash_flow_with_the_owners_is_not_an_attribution_line(shipped):
    """A cash flow statement splits nothing by owner: the row is swept as a financing cash flow."""
    fin = "CASH FLOWS FROM FINANCING ACTIVITIES"
    rows = [_row("Repayments of short-term borrowings", "-637760", "-652101", section=fin),
            _row("Distribution to owners of the Company under the scheme", "-5000", "-4000",
                 section=fin),
            _row("NET CASH FLOWS USED FOR FINANCING ACTIVITIES", "-642760", "-656101",
                 section=fin, role=LineRole.SUBTOTAL)]
    _run(rows, [_face("cash_flow")], shipped)
    row = rows[1]
    assert row.canonical_key == OTHER_FINANCING, (row.canonical_key, row.confidence.flags)
    assert "residual_ineligible:attribution caption" not in row.confidence.flags


def test_a_balance_sheet_attribution_line_is_still_refused(shipped):
    eq = "EQUITY"
    rows = [_row("Share capital", "100", "100", section=eq),
            _row("Amount attributable to owners of the Company", "50", "40", section=eq),
            _row("TOTAL EQUITY", "150", "140", section=eq, role=LineRole.TOTAL)]
    _run(rows, [_face("balance_sheet")], shipped)
    assert "residual_ineligible:attribution caption" in rows[1].confidence.flags, \
        rows[1].confidence.flags


def test_rows_below_the_closing_cash_are_not_swept_into_financing(shipped):
    """Hon Kwok: the analysis of cash under 'CASH AND CASH EQUIVALENTS AT END OF YEAR' is not a
    financing cash flow; the statement ends at its closing cash."""
    fin = "CASH FLOWS FROM FINANCING ACTIVITIES"
    rows = [_row("New bank loans", "812034", "3380172", section=fin),
            _row("Net cash flows from financing activities", "36711", "349271", section=fin,
                 role=LineRole.SUBTOTAL),
            _row("Cash and cash equivalents at beginning of year", "1747149", "1847908",
                 section=fin),
            _row("Effect of foreign exchange rates changes, net", "-55023", "-75435", section=fin),
            _row("CASH AND CASH EQUIVALENTS AT END OF YEAR", "1270169", "1747149", section=fin),
            _row("Cash and bank balances", "1090904", "1511603", section=fin),
            _row("Non-pledged time deposits", "203208", "240294", section=fin)]
    _run(rows, [_face("cash_flow")], shipped)
    assert rows[0].canonical_key == OTHER_FINANCING, rows[0].confidence.flags
    assert rows[3].canonical_key == "cf_financing__net_foreign_exchange_difference", \
        (rows[3].canonical_key, rows[3].confidence.flags)
    for r in rows[5:]:
        assert not _swept(r), (r.source_label, r.canonical_key, r.confidence.flags)


def test_a_later_printing_of_the_cash_flow_statement_is_not_narrative(shipped):
    """河钢 000709: the indirect-method supplement is printed as a note many pages after the face's
    closing cash, and the run reads it as the cash flow statement. Its unbound rows are still swept
    into Other non-cash adjustments (投资损失 -649,740,308.49), not cut as text under the face."""
    fin, op = "CASH FLOWS FROM FINANCING ACTIVITIES", "CASH FLOWS FROM OPERATING ACTIVITIES"
    rows = [_row("Net cash flows from financing activities", "-2887", "9454", 0, fin,
                 role=LineRole.SUBTOTAL),
            _row("Cash and cash equivalents at beginning of year", "29984", "19838", 0, fin),
            _row("Cash and cash equivalents at end of year", "18709", "29984", 0, fin),
            _row("Net profit", "800", "1189", 9, op),
            _row("Investment losses (gains shown with a minus)", "-649", "-686", 9, op),
            _row("Net cash flows from operating activities", "9678", "11213", 9, op,
                 role=LineRole.SUBTOTAL)]
    _run(rows, [_face("cash_flow", 0), _face("cash_flow", 9)], shipped)
    row = rows[4]
    assert row.canonical_key == "cf_oper_indirect__other_non_cash_adjs_oper", \
        (row.canonical_key, row.confidence.flags)


def test_a_statement_total_needs_no_sign_evidence_of_its_own():
    """The closing cash is the roll-forward's result, in `cf_top_level`: its relation is established
    by the sections its components are printed in."""
    rel = SimpleNamespace(statement="cash_flow", target="end", components=("beg", "cfo", "cff"),
                          magnitudes=())
    section_of = {"end": "cf_top_level", "beg": "cf_financing", "cfo": "cf_oper_indirect",
                  "cff": "cf_financing"}
    assert _signs_established(rel, section_of, frozenset({"cf_financing", "cf_oper_indirect"}))


# ── the balance sheet's column header and receivables ──────────────────────────────────────────

def test_a_dated_column_header_with_its_note_column_title_is_not_a_row():
    assert _is_noise_row("December December Notes", [Decimal("2024")])
    assert _is_noise_row("Notes 31 December 2024 31 December 2023", [Decimal("2024")])
    # a real caption, or a real amount, is kept
    assert not _is_noise_row("Deferred tax assets", [Decimal("2024")])
    assert not _is_noise_row("December December Notes", [Decimal("26765")])


def test_accounts_receivable_binds_trade_receivables(matcher):
    res = matcher.match("Accounts receivable", statement="balance_sheet", section="CURRENT ASSETS")
    assert res.canonical_key == "bs_ca__trade_receivables_gross", res
