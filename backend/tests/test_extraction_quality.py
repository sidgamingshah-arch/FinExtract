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
    return load_ontology(json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")),
                         resolve=True)


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))


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


# --- a summary of a statement is not the statement ----------------------------------------------

def test_a_financial_highlights_page_is_not_a_face_statement(rulebook, template):
    """The highlights page's title matches the P&L pattern outright — "SUMMARY OF STATEMENT OF
    PROFIT OR LOSS" — so it reached the decode carrying a strong title, which outweighs the
    backmatter signal. And the state meant for summaries comes AFTER the statements, so a
    front-matter summary could not be routed there."""
    from tests.fixtures.generate import make_financial_highlights_pdf

    doc, _ = _run(make_financial_highlights_pdf(), rulebook, template)

    kinds = {p.index: (p.kind.value, p.statement) for p in doc.pages}
    # Pages 0-1 are front matter, 2 is the highlights page, 3 is the statement.
    assert kinds[2] == ("other", None), f"the highlights page is a face page: {kinds[2]}"
    assert kinds[3][0] == "face" and kinds[3][1] == "profit_and_loss"


def test_the_figures_a_highlights_page_repeats_are_not_published_twice(rulebook, template):
    """Why the page kind matters. The highlights page repeats concepts the statement also reports,
    so publishing both puts two figures on one canonical key — and on a real filing they differ,
    because a highlights table is rounded, re-based, or simply a different cut."""
    from tests.fixtures.generate import make_financial_highlights_pdf

    doc, _ = _run(make_financial_highlights_pdf(), rulebook, template)

    pages = {p.index for p in doc.pages if p.kind.value == "face"}
    for li in doc.line_items:
        got = {ev.provenance.page_index for ev in li.values.values() if ev.provenance}
        assert got <= pages, f"{li.source_label!r} carries a figure from a non-face page: {got}"
    # One row per concept, and it is the statement's.
    for key in ("pl_income__revenue_from_operations", "pl_gross_profit"):
        rows = [li for li in doc.line_items if li.canonical_key == key]
        assert len(rows) == 1, f"{key} published {len(rows)} times"


def test_the_percentage_change_column_does_not_become_a_period(rulebook, template):
    """The highlights table's third column is a % change, and a page read as a statement gives it
    a period slot of its own — a ratio filed as money under a period nothing else has."""
    from tests.fixtures.generate import make_financial_highlights_pdf

    doc, _ = _run(make_financial_highlights_pdf(), rulebook, template)

    periods = {ev.period_label for li in doc.line_items for ev in li.values.values()}
    assert periods <= {"current", "prior"}, f"an extra period slot appeared: {sorted(periods)}"


def test_a_real_statement_title_is_still_matched(rulebook, template):
    """The guard is a qualifier test, not a ban on the word: the statements themselves are still
    found, which the fixture above asserts, and a filing whose notes discuss a 'summary' of
    anything is unaffected because only TITLE candidates are tested."""
    from app.stages.classify import _SUMMARY_TITLE

    assert _SUMMARY_TITLE.search("SUMMARY OF STATEMENT OF PROFIT OR LOSS")
    assert _SUMMARY_TITLE.search("損益表摘要")                     # the qualifier trails, in Han
    assert _SUMMARY_TITLE.search("FINANCIAL HIGHLIGHTS")
    assert not _SUMMARY_TITLE.search("CONSOLIDATED STATEMENT OF PROFIT OR LOSS")
    assert not _SUMMARY_TITLE.search("綜合現金流量表")
