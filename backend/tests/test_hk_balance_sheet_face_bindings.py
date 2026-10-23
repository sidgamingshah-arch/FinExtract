"""HKEX BALANCE-SHEET FACE ROWS REACH THE LINE THEIR DEFINITION NAMES.

Measured on the HKEX reference filings (answer-free captures):

  * Lai Sun 2024/25: the non-current 'Bank borrowings' (10,437,690) and 'Guaranteed notes' (382,438)
    matched no non-current line and fell to the Other Non-Current Liabilities residual; the current
    'Other borrowings' (775,973, amounts due to the late Mr. Lim Por Yen, repayable on demand) sat
    on CPLTD Other through an old alias, so CPLTD Other read 4,653,161 against the 3,877,188 the
    filing prints for the guaranteed notes. A short-term non-bank borrowing is ST Other Loans
    Payable; the current portion of long-term notes is CPLTD Other.
  * Lai Sun prints 'NET CURRENT (LIABILITIES)/ASSETS' below 'Total current liabilities'. The
    residuals' veto read '^net current (?:assets|liabilities)' and the bracket slipped past it, so
    -5,420,025 was swept into Other Non-Current Liabilities and Total Non-Current Liabilities read
    11,090,164 against 16,510,189.
  * Hon Kwok: 'Interest-bearing bank borrowings' (current and non-current) and 'Cash and bank
    balances' fell to the residuals.
  * BYD Electronic prints 'Total assets' before the liabilities; the balance sheet was closed there,
    so every liability row below it was refused as "narrative" and never swept.
  * PetroChina's CAS-format sheet: 'TOTAL LIABILITIES AND SHAREHOLDERS' EQUITY' bound Total
    Liabilities by its keyword, and 'Total shareholders' equity' was swept into Other Reserves.
"""
from __future__ import annotations

import json
import pathlib
import re
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"

CL, NCL = "CURRENT LIABILITIES", "NON-CURRENT LIABILITIES"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped))


def _key(matcher, caption, section):
    res = matcher.match(caption, statement="balance_sheet", section=section)
    return res.canonical_key if res is not None else None


# ── the matcher ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("caption,section,want", [
    ("Bank borrowings", NCL, "bs_ncl__ltd_bank"),
    ("Interest-bearing bank borrowings", NCL, "bs_ncl__ltd_bank"),
    ("Interest-bearing loans 計息貸款", NCL, "bs_ncl__ltd_bank"),
    ("Guaranteed notes", NCL, "bs_ncl__ltd_other"),
    ("Other borrowings", NCL, "bs_ncl__ltd_other"),
    ("Bank borrowings", CL, "bs_cl__cpltd_bank"),
    ("Interest-bearing bank borrowings", CL, "bs_cl__cpltd_bank"),
    ("Interest-bearing loans 計息貸款", CL, "bs_cl__cpltd_bank"),
    ("Guaranteed notes", CL, "bs_cl__cpltd_other"),
    ("Other borrowings", CL, "bs_cl__st_other_loans_payable"),
    ("Cash and bank balances", "CURRENT ASSETS", "bs_ca__cash_in_hand_and_at_banks"),
    ("Total shareholders’ equity", "Shareholders’ equity", "bs_equity__total_equity_and_reserves"),
    ("TOTAL LIABILITIES AND SHAREHOLDERS’ EQUITY", "Shareholders’ equity",
     "bs_cl__total_equity_and_liabilities"),
    ("Total liabilities and equity", "Equity", "bs_cl__total_equity_and_liabilities"),
])
def test_a_face_row_binds_the_line_its_definition_names(matcher, caption, section, want):
    assert _key(matcher, caption, section) == want


def test_current_other_borrowings_are_not_the_current_portion_of_long_term_debt(matcher):
    assert _key(matcher, "Other borrowings", CL) != "bs_cl__cpltd_other"


def test_total_liabilities_still_binds_its_own_row(matcher):
    assert _key(matcher, "Total liabilities", NCL) == "bs_cl__total_liabilities"


@pytest.mark.parametrize("caption", ["NET CURRENT (LIABILITIES)/ASSETS", "CURRENT ASSETS",
                                     "CURRENT LIABILITIES", "EQUITY"])
def test_a_subtotal_or_heading_caption_binds_no_component(matcher, caption):
    assert _key(matcher, caption, CL) is None


# ── the configuration ──────────────────────────────────────────────────────────────────────────

def _items():
    return {i["key"]: i for i in json.loads(SEED.read_text(encoding="utf-8"))["items"]}


def _concepts():
    return {m["canonical_key"]: m
            for m in json.loads(ONTOLOGY.read_text(encoding="utf-8"))["mappings"]}


@pytest.mark.parametrize("key", ["bs_ncl__other_non_current_liabilities",
                                 "bs_cl__other_current_liabilities"])
@pytest.mark.parametrize("caption", ["NET CURRENT (LIABILITIES)/ASSETS",
                                     "NET CURRENT ASSETS/(LIABILITIES) 流動資產╱ （負債） 淨值",
                                     "Net current liabilities", "NET CURRENT ASSETS"])
def test_the_residuals_veto_every_net_current_spelling(key, caption):
    # The line-item set is what the run reads (its working view feeds the residual stage).
    hints = _items()[key].get("exclude_hints") or []
    assert any(re.search(h, caption, re.IGNORECASE) for h in hints), (key, caption)


@pytest.mark.parametrize("key", ["bs_ncl__ltd_bank", "bs_ncl__ltd_other", "bs_cl__cpltd_bank",
                                 "bs_cl__cpltd_other", "bs_cl__st_other_loans_payable",
                                 "bs_ca__cash_in_hand_and_at_banks",
                                 "bs_equity__total_equity_and_reserves",
                                 "bs_cl__total_equity_and_liabilities"])
def test_the_ontology_carries_the_same_aliases(key):
    item, concept = _items()[key], _concepts()[key]
    assert item["aliases"] == concept["aliases"]
    assert item["aliases_i18n"] == concept["aliases_i18n"]


# ── the stage ──────────────────────────────────────────────────────────────────────────────────

def _row(caption, value, section=None, page=0, role=LineRole.LINE):
    row = LineItem(source_label=caption, role=role, section_hint=section)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal(value), value_raw=Decimal(value),
                                 provenance=Provenance(page_index=page)))
    return row


def _run(shipped, rows, pages=(0,)):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=p, statement="balance_sheet") for p in pages]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    return {r.source_label: r for r in rows}


def test_lai_sun_liabilities_reach_their_lines(shipped):
    got = _run(shipped, [
        _row("Tax payable", "737641", CL),
        _row("Bank borrowings", "10886034", CL),
        _row("Other borrowings", "775973", CL),
        _row("Guaranteed notes", "3877188", CL),
        _row("Total current liabilities", "19411610", CL),
        _row("NET CURRENT (LIABILITIES)/ASSETS", "-5420025", CL),
        _row("TOTAL ASSETS LESS CURRENT LIABILITIES", "47615036", CL),
        _row("Lease liabilities ", "524258", NCL),
        _row("Bank borrowings ", "10437690", NCL),
        _row("Guaranteed notes ", "382438", NCL),
        _row("Long-term deposits received", "229815", NCL),
        _row("Total non-current liabilities", "16510189", NCL),
    ])
    assert got["Bank borrowings"].canonical_key == "bs_cl__cpltd_bank"
    assert got["Other borrowings"].canonical_key == "bs_cl__st_other_loans_payable"
    assert got["Guaranteed notes"].canonical_key == "bs_cl__cpltd_other"
    assert got["Bank borrowings "].canonical_key == "bs_ncl__ltd_bank"
    assert got["Guaranteed notes "].canonical_key == "bs_ncl__ltd_other"
    net = got["NET CURRENT (LIABILITIES)/ASSETS"]
    assert net.canonical_key not in ("bs_ncl__other_non_current_liabilities",
                                     "bs_cl__other_current_liabilities"), net.confidence.flags
    assert any(f.startswith("residual_never_sweep:exclude_hints:") for f in net.confidence.flags)
    assert (got["Long-term deposits received"].canonical_key
            == "bs_ncl__other_non_current_liabilities")


def _byd(liability_page):
    return [
        _row("Inventories", "18088651", "CURRENT ASSETS", page=0),
        _row("Total current assets", "60415511", "CURRENT ASSETS", page=0),
        _row("Total assets", "90347204", "CURRENT ASSETS", page=0),
        _row("Tax payable", "512345", CL, page=liability_page),
        _row("Total current liabilities", "50823330", CL, page=liability_page),
    ]


def test_a_statement_total_printed_before_the_liabilities_does_not_close_the_sheet(shipped):
    """BYD Electronic: 'Total assets' is printed above the liabilities, on the previous page."""
    got = _run(shipped, _byd(1), pages=(0, 1))
    tax = got["Tax payable"]
    assert tax.canonical_key == "bs_cl__other_current_liabilities", tax.confidence.flags
    assert "residual_ineligible:narrative row" not in tax.confidence.flags


def test_a_section_subtotal_many_pages_on_is_another_printing(shipped):
    """PetroChina prints a second balance sheet a hundred pages on; it does not reopen the first."""
    got = _run(shipped, _byd(100), pages=(0, 100))
    assert "residual_ineligible:narrative row" in got["Tax payable"].confidence.flags
