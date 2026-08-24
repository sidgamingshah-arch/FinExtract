"""Two things the reader published that were never figures on a statement.

Both were found by auditing a real filing against its printed pages, and both are silent: nothing
downstream can tell a bad row from a good one once it is a caption and a Decimal on a line item.

* A PERCENTAGE read as money. ``_NUM`` ended in ``%?`` and the parser stripped the sign, so
  "45.2%" became the amount 45.2 — filed on a template line, summed into a subtotal, exported.
* THE FILING'S OWN RUNNING HEADER read as a line item. The two regexes that catch a running header
  (one in the classifier, one in the reader) are word lists — "annual report", 年報 — and the
  common HK house style is the entity's name alone, which matches neither.
"""
from __future__ import annotations

import json
from decimal import Decimal
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
    return load_ontology(json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text()),
                         resolve=True)


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text()))


def _run(data: bytes, rulebook, template) -> tuple[DocumentModel, PipelineContext]:
    ctx = PipelineContext(raw_bytes=data)
    ctx.ontology, ctx.template = rulebook, template
    doc = default_pipeline().run(DocumentModel(filename="f.pdf", fmt=DocFormat.PDF), ctx)
    return doc, ctx


# --- a percentage is not an amount --------------------------------------------------------------

def test_a_printed_percentage_is_not_read_as_a_figure():
    """The parser, directly. A filing prints percentages in their own columns — % of revenue, the
    effective tax rate, gearing — and each one read as money is a fabricated amount on a real
    line."""
    from app.services.row_reconstruct import _num

    assert _num("45.2%") is None
    assert _num("(3.1)%") is None, "accounting parentheses do not make a percentage an amount"
    assert _num("12%") is None
    # …and nothing about a real figure changed.
    assert _num("1,204") == 1204
    assert _num("(500)") == -500
    assert _num("0.45") == Decimal("0.45")


def test_a_percentage_is_refused_whatever_the_locale_rules_say():
    """Refused before either parse path, so the locale parser cannot admit "45,2%" through the
    other side — the rulebook picks that path, and a filing in French or German takes it."""
    from app.schemas.ontology import NumberFormat
    from app.services.row_reconstruct import _num

    eu = NumberFormat(decimal=",", thousands=".")
    assert _num("45,2%", eu) is None
    assert _num("1.234,56", eu) == Decimal("1234.56")


# --- the filing's own running header ------------------------------------------------------------

def test_the_running_header_is_not_published_as_a_line_item(rulebook, template):
    """A header that is JUST THE COMPANY NAME, with a figure on its baseline.

    No word list catches it, and no date-fragment rule either: the amount beside it is 8,461, not
    a year or a day-of-month. What identifies it is that the filing prints it at the top of page
    after page — which is what ``pdf_extract._page_chrome`` measures.
    """
    from tests.fixtures.generate import make_named_running_header_pdf

    doc, ctx = _run(make_named_running_header_pdf(), rulebook, template)

    labels = [li.source_label for li in doc.line_items]
    assert "Lai Sun Garment (International) Limited" not in labels
    # The statement's own rows are untouched — the point is that ONLY the chrome went.
    assert labels == ["Trade receivables", "Cash and cash equivalents"]
    assert not any("8461" == str(v.value) for li in doc.line_items for v in li.values.values()), (
        "the header's figure reached a line item")
    # And the run says what it treated as chrome, so a caption wrongly dropped is explainable.
    assert any("page_chrome=" in line for line in ctx.logs)


def test_a_caption_printed_once_is_never_treated_as_chrome():
    """The threshold that keeps the rule safe. Chrome is defined by repeating across PAGES, so a
    statement caption — printed once, in the body of one page — cannot qualify however it is
    worded, and a word repeated many times down a single page cannot either."""
    from app.services.pdf_extract import _CHROME_MIN_PAGES, _chrome_key

    assert _CHROME_MIN_PAGES >= 3
    # The key drops digits, which is what makes a header identical page to page…
    assert _chrome_key("Acme Holdings Limited / Annual Report 2024   64") == \
        _chrome_key("Acme Holdings Limited / Annual Report 2024   65")
    # …and it still tells two different captions apart.
    assert _chrome_key("Trade receivables") != _chrome_key("Cash and cash equivalents")


def test_the_notes_pages_of_that_filing_still_parse(rulebook, template):
    """The chrome set is built over every page being read, notes included, so the guard has to not
    eat the notes: the note table behind the face figure is still there."""
    from tests.fixtures.generate import make_named_running_header_pdf

    doc, _ = _run(make_named_running_header_pdf(), rulebook, template)
    assert [n.note_number for n in doc.notes] == ["16"]
    assert [li.source_label for li in doc.line_items][0] == "Trade receivables"
