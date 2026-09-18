"""The other half of the page template: what a filing prints UNDER its statements.

``pdf_extract._page_chrome`` read the top band only, so the closing boilerplate every IFRS filing
prints beneath a statement — "The notes on pages 68 to 159 form part of these consolidated
financial statements" — reached the reader as content. It wraps, so reconstruction made two rows
of it and sent the page range to their value columns; each row was then swept into the residual
bucket of whatever section its page belonged to and published. On 佳明集團 2025/26 that put 68 and
159 into ``is_pl__other_operating_expenses``, 159 and 62 into
``is_oci__other_equity_and_reserves_adj``, 68/159 and 64 into ``bs_equity__other_reserves``, and
68/159 into ``cf_financing__other_financing_cash_flows``.

Naming the line is not enough — that is what the top band's test does, and a WRAPPED sentence has
no piece whose label is the name. So the foot template is refused as WORDS, before either reader
sees the page, which also keeps it out of a note's prose.

The danger in reading that deep is the opposite mistake: the foot band reaches real content. A
PRC note that runs onto a second page prints 合计 near the bottom of the first, and treating that
as chrome would drop a Total wherever it is printed. Two things keep them apart, and both are
tested here: a template is printed at a FIXED y, and a line stating a grouped amount is never
chrome.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def rulebook():
    return load_ontology(
        json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")),
        resolve=True)


@pytest.fixture(scope="module")
def template():
    return load_template(
        json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def run(rulebook, template):
    from tests.fixtures.generate import make_statement_footer_boilerplate_pdf

    ctx = PipelineContext(raw_bytes=make_statement_footer_boilerplate_pdf())
    ctx.ontology, ctx.template = rulebook, template
    doc = default_pipeline().run(DocumentModel(filename="f.pdf", fmt=DocFormat.PDF), ctx)
    return doc, ctx


def _labels(doc):
    return [li.source_label or "" for li in doc.line_items]


# --- the boilerplate is not a row ---------------------------------------------------------------

def test_the_closing_boilerplate_under_a_statement_is_not_a_line_item(run):
    """Neither half of the wrapped sentence survives as a row."""
    doc, _ = run

    assert not [lab for lab in _labels(doc)
                if "notes on pages" in lab or lab.startswith("statements")], _labels(doc)


def test_the_page_range_in_it_never_becomes_an_amount(run):
    """What made the leak publishable: 68 and 159 are page numbers, not dates, so no
    date-fragment rule could refuse them and they were filed as money."""
    doc, _ = run

    printed = {str(v.value) for li in doc.line_items for v in li.values.values()}
    assert "68" not in printed and "159" not in printed, sorted(printed)
    # The folio is the other figure the footer block carries.
    assert not printed & {"61", "62", "63", "64"}, sorted(printed)


def test_the_run_says_how_many_words_it_refused(run):
    """A caption dropped in error has to be explainable from the log alone."""
    _, ctx = run

    assert any("page_foot_chrome_pages=" in line for line in ctx.logs)
    assert any("foot_chrome_words=" in line for line in ctx.logs)


# --- and real content printed just as deep survives ---------------------------------------------

def test_the_deepest_genuine_row_on_the_page_keeps_its_figure(run):
    """0.869 of page height on the filing this was measured on — deeper than a band mirroring the
    top would allow, which is why the band is 0.88 and the other two tests below exist."""
    doc, _ = run

    assert "Total assets less current liabilities" in _labels(doc)
    assert "3078784" in {str(v.value) for li in doc.line_items
                         for v in li.values.values()}


def test_a_total_inside_the_foot_band_is_not_chrome(run):
    """Printed at 0.884, 0.889 and 0.893 — inside the band, on three pages, which clears the
    repeat threshold. It is still a caption: a table ends wherever its rows end, so its Total
    WANDERS, and the template does not."""
    doc, _ = run

    assert _labels(doc).count("Total") == 3, _labels(doc)


def test_the_foot_template_is_the_one_printed_at_a_fixed_depth(run):
    """The discriminator, stated on the chrome set itself. Measured over the five filings: the
    boilerplate's spread is 0.0000 over five pages, while 合计 / 期末余额 / 单位：元 at the foot of a
    PRC note spread 0.0021 to 0.0153."""
    from app.services.pdf_extract import _CHROME_FOOT_ALIGN, _chrome_key

    _, ctx = run

    logged = [line for line in ctx.logs if "page_chrome=" in line]
    assert logged, ctx.logs
    assert _chrome_key("The notes on pages 68 to 159 form part of these consolidated financial") \
        in logged[0]
    assert _chrome_key("Total") not in logged[0]
    assert _CHROME_FOOT_ALIGN <= 0.002


def test_a_line_stating_a_grouped_amount_is_never_chrome():
    """The second protection, independent of the band and of the alignment: whatever repeats and
    wherever it is printed, a line that states a figure is a row."""
    from app.services.pdf_extract import _CHROME_AMOUNT

    assert _CHROME_AMOUNT.search("Total 3,078,784")
    assert _CHROME_AMOUNT.search("15,280,000")
    # …and the footer's own page range is not one.
    assert not _CHROME_AMOUNT.search(
        "The notes on pages 68 to 159 form part of these consolidated financial")
