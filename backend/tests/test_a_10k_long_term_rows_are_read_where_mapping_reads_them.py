"""Madrigal Pharmaceuticals' 10-K balance sheet: lease liabilities and a loan payable that reached
the Others buckets, measured on the run's own log.

What the filing prints (thousands):

    Current liabilities:
      Accounts payable                      48,878     43,599
      Accrued liabilities                  260,392    124,695
      Lease liabilities                      1,018        983
      Total current liabilities            310,288    169,277
    Long term liabilities:
      Loan payable, net of discount        339,881    117,569
      Lease liabilities                      6,731      1,018
      Total long term liabilities          346,612    118,587

What the run did, and the three things each test below pins:

  * CAPTION MAPPING placed none of the three rows. The US GAAP lease lines knew only captions with
    "operating" in them, and long-term debt no "Loan payable"; the sweep put the current lease row in
    Other current liabilities and the other two in Other non-current liabilities (339,881 + 6,731 =
    346,612 — the printed total, by coincidence of there being nothing else in the section).
  * THE BANNER. "Long term liabilities:" named no section, so the reader carried "Current
    liabilities:" down over the long-term rows. Caption mapping corrects for that
    (`map_ontology._balance_sheet_past_total`), but the model's answer checks read the bare banner:
    the model's correct citations of 'Loan payable, net of discount' and 'Lease liabilities' for
    the long-term lines were refused as "printed under Current liabilities:, outside this line's
    section". The correction is now recorded on the row (`LineItem.section_in_force`) and read by
    every later stage through `mapping.section_of_row`.
  * THE MODEL'S REQUEST for the eight current-liability lines timed out, and the gateway adapter
    made one attempt (see `test_bedrock_gateway_llm`).
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import face_context
from app.services.mapping import SourceRef, section_of_banner, section_of_row
from app.services.note_sourced import resolve_sources
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
US_TEMPLATE = "output_csv_usgaap_v1_template.json"
CL, LT = "Current liabilities:", "Long term liabilities:"
PAGE = 4


@pytest.fixture(scope="module")
def usgaap():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_usgaap_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


def _row(caption, current, prior, section, role=LineRole.LINE):
    row = LineItem(source_label=caption, role=role, section_hint=section)
    for period, value in (("current", current), ("prior", prior)):
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                     value=Decimal(value), value_raw=Decimal(value),
                                     provenance=Provenance(page_index=PAGE)))
    return row


def _madrigal(long_term_banner):
    """The liabilities as printed, with the banner the reader gave the long-term rows."""
    return [
        _row("Accounts payable", "48878", "43599", CL),
        _row("Accrued liabilities", "260392", "124695", CL),
        _row("Lease liabilities", "1018", "983", CL),
        _row("Total current liabilities", "310288", "169277", CL, LineRole.SUBTOTAL),
        _row("Loan payable, net of discount", "339881", "117569", long_term_banner),
        _row("Lease liabilities", "6731", "1018", long_term_banner),
        _row("Total long term liabilities", "346612", "118587", long_term_banner, LineRole.SUBTOTAL),
        _row("Total liabilities", "656900", "287864", long_term_banner, LineRole.TOTAL),
    ]


def _doc(rows):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="madrigal.pdf")
    doc.pages = [PageSource(index=PAGE, kind=PageKind.FACE, statement="balance_sheet")]
    doc.line_items = rows
    return doc


def _map(rows, line_set):
    doc = _doc(rows)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(line_set)
    ctx.line_items = line_set
    ctx.template_def = json.loads((TEMPLATES / US_TEMPLATE).read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    return doc


# ── the banner ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("banner", ["Long term liabilities:", "Long-term liabilities:",
                                    "LONG-TERM LIABILITIES", "Non-current liabilities:",
                                    "Noncurrent liabilities"])
def test_long_term_liabilities_is_the_non_current_banner(banner):
    assert section_of_banner(banner) == "non_current_liabilities"


def test_the_current_banner_is_still_the_current_one():
    assert section_of_banner("Current liabilities:") == "current_liabilities"
    assert section_of_banner("Current assets:") == "current_assets"


@pytest.mark.parametrize("banner,section", [
    ("Liabilities due after one year:", "non_current_liabilities"),
    ("Deferred credits and other liabilities:", "non_current_liabilities"),
    ("Long-term assets:", "non_current_assets"),
    ("Liabilities due within one year:", "current_liabilities"),
    ("Short-term liabilities", "current_liabilities"),
    ("Stockholders’ deficit:", "equity"),
    ("Shareholders' deficiency", "equity"),
    ("Partners' capital:", "equity"),
])
def test_the_other_spellings_of_a_balance_sheet_banner(banner, section):
    assert section_of_banner(banner) == section


@pytest.mark.parametrize("caption", ["Long-term debt", "Accumulated deficit", "Other liabilities",
                                     "Short-term debt", "Long-term investments"])
def test_a_caption_that_shares_a_word_with_a_banner_is_not_one(caption):
    """The spellings are whole phrases because a banner is matched anywhere in its text."""
    assert section_of_banner(caption) is None


# ── caption mapping ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("long_term_banner", [LT, CL], ids=["banner_read", "banner_carried"])
def test_madrigals_lease_liabilities_and_loan_payable_bind_their_lines(usgaap, long_term_banner):
    """Both ways the reader can leave the long-term rows: under their own banner, or under the
    current banner carried past "Total current liabilities"."""
    doc = _map(_madrigal(long_term_banner), usgaap)
    got = [(r.source_label, r.canonical_key) for r in doc.line_items]
    assert got[2] == ("Lease liabilities", "bs_cl__operating_lease_liabilities_current")
    assert got[4] == ("Loan payable, net of discount", "bs_ncl__long_term_debt")
    assert got[5] == ("Lease liabilities", "bs_ncl__operating_lease_liabilities")
    assert got[6] == ("Total long term liabilities", "bs_ncl__total_non_current_liabilities")
    assert got[0][1] == "bs_cl__accounts_payable" and got[1][1] == "bs_cl__accrued_liabilities"


def test_the_section_mapping_reads_a_row_in_is_recorded_on_it(usgaap):
    """Carried past the current total, the long-term rows are read as non-current — and the row
    says so, for the stages after mapping. A row whose banner stands records nothing."""
    doc = _map(_madrigal(CL), usgaap)
    by_label = {(r.source_label, r.ordinal): r for r in doc.line_items}
    loan = by_label[("Loan payable, net of discount", 4)]
    long_lease = by_label[("Lease liabilities", 5)]
    current_lease = by_label[("Lease liabilities", 2)]
    assert loan.section_hint == CL and section_of_row(loan) == "non_current_liabilities"
    assert section_of_row(long_lease) == "non_current_liabilities"
    assert current_lease.section_in_force is None
    assert section_of_row(current_lease) == "current_liabilities"


def test_a_row_read_in_no_section_is_in_none():
    row = LineItem(source_label="Total liabilities", section_hint=CL, section_in_force="")
    assert section_of_row(row) is None
    assert section_of_row(LineItem(source_label="x", section_hint=CL)) == "current_liabilities"


# ── the model's answer checks ────────────────────────────────────────────────────────────────────

def _cited(caption, sections, rows):
    doc = _doc(rows)
    cite = [SourceRef(statement="balance_sheet", caption=caption)]
    return resolve_sources(cite, [], face_context.face_index(doc), sections=sections)


def test_the_long_term_citations_resolve_where_mapping_read_the_rows():
    """The defect, from the run log: "citation NOT resolved caption='Lease liabilities' (the row of
    that caption is printed under Current liabilities:, outside this line's section)"."""
    rows = _madrigal(CL)
    rows[4].section_in_force = rows[5].section_in_force = "Non-current liabilities"
    res, unresolved = _cited("Loan payable, net of discount", {"non_current_liabilities"}, rows)
    assert res and res[0]["figures"] == {"current": "339881", "prior": "117569"}, unresolved
    # Two rows are captioned "Lease liabilities"; the long-term line takes the long-term one.
    res, unresolved = _cited("Lease liabilities", {"non_current_liabilities"}, rows)
    assert res and res[0]["figures"] == {"current": "6731", "prior": "1018"}, unresolved
    # And the current line still takes the current one.
    res, unresolved = _cited("Lease liabilities", {"current_liabilities"}, rows)
    assert res and res[0]["figures"] == {"current": "1018", "prior": "983"}, unresolved


def test_under_the_bare_carried_banner_the_long_term_citation_is_refused():
    """What the run did: with nothing recorded, the stale banner refuses the long-term line."""
    res, unresolved = _cited("Loan payable, net of discount", {"non_current_liabilities"},
                             _madrigal(CL))
    assert not res
    assert "outside this line's section" in unresolved[0]["why"]
