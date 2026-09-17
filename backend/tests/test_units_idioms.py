"""Units/currency detection on real-world filings (Req 14).

Real HK/China annual reports never spell the scale out on the cover: the declaration is the
statement column head 100+ pages in — "RMB’000 RMB’000 人民幣千元 人民幣千元". These tests pin
the three things that made such a document come back with units=null: the page range scanned,
the ``'000``/CJK idioms, and the refusal to fall back to a currency that wasn't declared.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import DocFormat, PageKind
from app.core.stage import PipelineContext
from app.stages.normalize import NormalizeStage, _detect_units

# The actual text of page 102 of the China SCE Group Holdings AR2023 consolidated balance sheet.
PAGE_102 = "2023 2022 二零二三年 二零二二年 RMB’000 RMB’000 人民幣千元 人民幣千元"


def _doc(*face_indexes: int, pages: int = 3) -> DocumentModel:
    """A document whose page classification marks `face_indexes` as statement faces."""
    doc = DocumentModel(filename="ar.pdf", fmt=DocFormat.PDF)
    doc.pages = [PageSource(index=i,
                            kind=PageKind.FACE if i in face_indexes else PageKind.OTHER)
                 for i in range(pages)]
    return doc


def _detect(monkeypatch, page_texts: dict[int, str], doc: DocumentModel | None = None):
    """Run detection over a fake text layer: {page index: text}."""
    import app.services.derived as derived

    monkeypatch.setattr(derived, "document_text",
                        lambda data, fmt: sorted(page_texts.items()))
    return _detect_units(PipelineContext(raw_bytes=b"x"), "pdf", doc)


@pytest.mark.parametrize(("text", "label", "scale", "ccy"), [
    ("(RMB’000)", "thousand", 1_000, "CNY"),                 # curly apostrophe (PDF text layer)
    ("(RMB'000)", "thousand", 1_000, "CNY"),                 # straight apostrophe
    ("HK$'000", "thousand", 1_000, "HKD"),
    ("US$’000", "thousand", 1_000, "USD"),
    ("₹'000", "thousand", 1_000, "INR"),
    ("RMB'000,000", "million", 1_000_000, "CNY"),
    ("人民幣千元", "thousand", 1_000, "CNY"),
    ("港幣百萬", "million", 1_000_000, "HKD"),
    ("人民币百万", "million", 1_000_000, "CNY"),
    ("人民幣億元", "hundred million", 100_000_000, "CNY"),
    ("新台幣萬元", "ten thousand", 10_000, "TWD"),
    ("in HK$'million", "million", 1_000_000, "HKD"),
    ("(Amounts in ₹ crore)", "crore", 10_000_000, "INR"),    # spelled-out forms still work
    ("(Amounts in HK$ million)", "million", 1_000_000, "HKD"),
    ("in thousands of USD", "thousand", 1_000, "USD"),
])
def test_scale_and_currency_idioms(monkeypatch, text, label, scale, ccy):
    got = _detect(monkeypatch, {0: f"Balance Sheet {text}", 1: ""})
    assert got is not None
    assert got.units_label == label
    assert got.scale_factor == Decimal(scale)
    assert got.currency == ccy


def test_page_102_column_head_on_a_late_face_page(monkeypatch):
    # Cover + contents declare nothing; the face page 100+ carries RMB’000 / 人民幣千元.
    doc = _doc(101, pages=120)
    got = _detect(monkeypatch, {0: "China SCE Group Holdings Limited Annual Report 2023",
                                1: "Contents Corporate Information",
                                101: f"CONSOLIDATED BALANCE SHEET {PAGE_102}"}, doc)
    assert got is not None
    assert got.units_label == "thousand"
    assert got.scale_factor == Decimal(1_000)
    assert got.currency == "CNY"
    assert got.source_bbox_page == 101   # provenance points at the declaring face page


def test_front_matter_banner_still_wins_over_face_pages(monkeypatch):
    doc = _doc(2)
    got = _detect(monkeypatch, {0: "(All amounts in ₹ crore)", 1: "", 2: "HK$'000"}, doc)
    assert got is not None and got.units_label == "crore" and got.currency == "INR"


def test_currency_only_front_matter_does_not_pre_empt_a_later_scale(monkeypatch):
    # A stray "RMB" on the cover must not end the search with scale 1 — the real declaration
    # ("'000") is on the statement face, and that is what the figures are printed in.
    doc = _doc(2)
    got = _detect(monkeypatch, {0: "Report of the directors, amounts in RMB", 1: "",
                                2: "CONSOLIDATED INCOME STATEMENT ’000"}, doc)
    assert got is not None
    assert got.units_label == "thousand" and got.scale_factor == Decimal(1_000)
    assert got.currency == "CNY"      # currency carried over from where it was declared


def test_nothing_declared_returns_none(monkeypatch):
    doc = _doc(2)
    got = _detect(monkeypatch, {0: "Annual Report 2023", 1: "Contents",
                                2: "Total assets 17,414 Total equity 9,000"}, doc)
    assert got is None                # never guess a scale for an undeclared document


def test_scale_without_currency_is_not_labelled_inr(monkeypatch):
    got = _detect(monkeypatch, {0: "(Figures in '000)", 1: ""})
    assert got is not None
    assert got.units_label == "thousand" and got.scale_factor == Decimal(1_000)
    assert not got.currency          # no currency asserted when none was declared


def test_thousands_separator_apostrophe_is_not_a_declaration(monkeypatch):
    # Swiss-style grouping (1'000) is a number, not a units declaration.
    got = _detect(monkeypatch, {0: "Total assets 17'000 9'000", 1: ""})
    assert got is None


def test_stage_records_units_from_a_face_page(monkeypatch):
    doc = _doc(101, pages=120)
    import app.services.derived as derived

    monkeypatch.setattr(derived, "document_text", lambda data, fmt: [
        (0, "China SCE Group Holdings Limited"), (1, "Contents"),
        (101, f"CONSOLIDATED BALANCE SHEET {PAGE_102}")])
    NormalizeStage().run(doc, PipelineContext(raw_bytes=b"x"))
    assert doc.unit_context is not None
    assert doc.unit_context.units_label == "thousand"
    assert doc.unit_context.currency == "CNY"


# ── A FACE PAGE WHOSE STATEMENT NEVER RESOLVED IS NOT A STATEMENT PAGE ───────────────────────
#
# `DocumentModel.face_pages` returns every page the classifier called FACE whatever became of its
# statement, and detection reads the first twelve of them — so a page that merely LOOKED like a
# statement face could declare the scale for the whole filing.
#
# MEASURED ON BOTH REFERENCE FILINGS. 000709: 50 FACE pages, 4 of the 12 scanned with no resolved
# statement, three at indices 6-8 AHEAD of the first real statement page at 81. 688008: 22 FACE
# pages, 5 of 12 unresolved at 144-148. Neither filing's unresolved pages declare a scale, so both
# detect correctly today — by luck, not by design.
#
# `services.pdf_extract` already refuses exactly these pages when choosing what to reconstruct, and
# a page not trusted to yield a ROW should not be trusted to declare the units every row is read in.


def _doc_with_statements(*, resolved: dict[int, str], unresolved=(), pages: int = 200):
    """A document whose FACE pages carry a statement for `resolved` and none for `unresolved`."""
    doc = DocumentModel(filename="ar.pdf", fmt=DocFormat.PDF)
    faces = set(resolved) | set(unresolved)
    doc.pages = [PageSource(index=i,
                            kind=PageKind.FACE if i in faces else PageKind.OTHER,
                            statement=resolved.get(i))
                 for i in range(pages)]
    return doc


def test_a_scale_on_an_unresolved_face_page_is_ignored(monkeypatch):
    """THE HARDENING. The unresolved page comes FIRST in document order, which is what made it win:
    a bond-section table headed 单位：万元 would have set the scale for every figure in the filing."""
    doc = _doc_with_statements(resolved={81: "balance_sheet"}, unresolved=(6,))
    got = _detect(monkeypatch, {0: "Annual Report 2024", 1: "",
                                6: "公司债券 单位：万元",
                                81: "合并资产负债表 单位：元 人民币"}, doc)
    assert got is not None
    assert got.units_label is None, f"took the scale off an unresolved page: {got.units_label}"
    assert got.scale_factor == Decimal(1)
    assert got.source_bbox_page is None


def test_the_window_is_not_spent_on_unresolved_pages(monkeypatch):
    """COVERAGE, THE OTHER HALF. Thirteen unresolved faces used to fill the twelve-page window
    entirely and push the real statement out of it; now they are not candidates at all."""
    doc = _doc_with_statements(resolved={120: "balance_sheet"},
                               unresolved=tuple(range(6, 19)))
    texts = {0: "Annual Report 2024", 1: ""}
    texts.update({i: "五年财务摘要" for i in range(6, 19)})
    texts[120] = "合并资产负债表 人民幣千元"
    got = _detect(monkeypatch, texts, doc)
    assert got is not None and got.units_label == "thousand", got
    assert got.source_bbox_page == 120


def test_a_filing_that_resolves_no_statement_still_detects(monkeypatch):
    """THE FALLBACK, and it is load-bearing rather than defensive: one filing in the corpus resolves
    no note or face section at all, and every other test in this file builds its faces WITHOUT a
    statement. Filtering with no fallback would have starved detection instead of sharpening it —
    the same convention `pdf_extract` uses for its own target set, where absence of a signal means
    unconstrained and never "nothing is allowed"."""
    doc = _doc_with_statements(resolved={}, unresolved=(102,))
    got = _detect(monkeypatch, {0: "Annual Report 2023", 1: "", 102: PAGE_102}, doc)
    assert got is not None and got.units_label == "thousand", got
    assert got.currency == "CNY"
    assert got.source_bbox_page == 102
