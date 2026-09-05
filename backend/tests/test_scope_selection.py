"""``scope_selection`` and ``normalisation`` decide WHICH figure is loaded, and they are the two
rulebook blocks a reader cannot check against a total.

A statement prints several columns of equally valid numbers. Read the wrong one and every
downstream check still passes — the column is internally consistent — so the only evidence that
the right column was read is these tests. Each one names the declared field it exercises, and each
is written so that removing the consumption (or emptying the field in the rulebook) fails it:

* ``entity_scope``   — Group vs Company, from the declared signals, with the false positives that
                       would SPLIT a comparative as the negative cases.
* ``period_selection``— current period from the heading DATE, and a restated comparative that
                       never overwrites the original.
* ``units_and_currency`` — resolved per statement header, persisted per fact, and the 1000×
                       subtotal conflict that routes a statement to review.
* ``column_guard``   — two facts differing on a declared dimension are two facts.
* ``normalisation``  — the declared strip rules, and banners recognised whatever their case.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, UnitContext
from app.schemas.ontology import Normalisation, ScopeSelection
from app.services import row_reconstruct as rr
from app.services.row_reconstruct import Word, build_line_items

RULEBOOK = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "hkfrs_hk_china_ontology.json")


def _raw_blocks() -> tuple[dict, dict]:
    raw = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    return copy.deepcopy(raw["scope_selection"]), copy.deepcopy(raw["normalisation"])


def _scope(**edits) -> ScopeSelection:
    """The rulebook's own ``scope_selection``, with ``edits`` applied to it.

    Every "liveness" test below edits the block this way, so what it proves is that the FIELD is
    what drives the behaviour — not a regex that happens to agree with it.
    """
    block, _ = _raw_blocks()
    for path, value in edits.items():
        node = block
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return ScopeSelection.model_validate(block)


def _normalisation(pipeline=None) -> Normalisation:
    _, block = _raw_blocks()
    if pipeline is not None:
        block["pipeline"] = pipeline
    return Normalisation.model_validate(block)


# --- rendering the fixtures --------------------------------------------------------------------

def _pdf_words(data: bytes, page: int = 0) -> list[Word]:
    """Words of a rendered fixture page, exactly as the native-PDF path reads them."""
    fitz = pytest.importorskip("fitz")
    from app.services.pdf_extract import _native_words

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        p = doc[page]
        return _native_words(p, max(p.rect.width, 1.0), max(p.rect.height, 1.0), rotation=0)
    finally:
        doc.close()


def _w(text: str, x: float, y: float, width: float = 0.06) -> Word:
    return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + 0.012))


def _build(words, **kw):
    logs: list[str] = []
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                               log=logs.append, **kw)
    return items, logs


def _slots(item: LineItem) -> dict[tuple[str, str | None], str]:
    return {(v.basis.value, v.period_label): str(v.value) for v in item.values.values()}


# --- entity_scope -----------------------------------------------------------------------------

def test_a_group_company_header_bands_the_page_from_the_declared_signals():
    """The gap this batch exists to close: an HKEX filing heads its columns "Group | Company",
    which the engine's own regexes ("consolidat", "standalone|separate") never matched — so the
    Company's figures were filed as the Group's and added to them."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_group_company_pdf

    items, logs = _build(_pdf_words(make_group_company_pdf()))
    tr = next(i for i in items if "Trade receivables" in i.source_label)
    assert _slots(tr) == {
        ("consolidated", "current"): "3410", ("consolidated", "prior"): "2900",
        ("standalone", "current"): "310", ("standalone", "prior"): "270",
    }
    # …and the decision is auditable from the run log, not only from the figures.
    assert any("entity_scope=two_basis_header" in m for m in logs), logs


def test_emptying_the_declared_signals_stops_the_group_company_detection():
    """Proof the DECLARED signals drive it: with ``entity_scope.signals`` emptied, "Group |
    Company" is not a basis header any more (the only words left are the two the engine read
    before any rulebook declared one, and neither is printed here), so the page reverts to one
    basis and four columns become two periods — which is exactly the mis-load this closes."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_group_company_pdf

    words = _pdf_words(make_group_company_pdf())
    items, logs = _build(words, scope=_scope(**{"entity_scope.signals": []}))
    tr = next(i for i in items if "Trade receivables" in i.source_label)
    assert len({b for b, _ in _slots(tr)}) == 1        # one basis: the split is gone
    assert not any("two_basis_header" in m for m in logs), logs


def test_the_rulebook_file_is_what_the_default_reads(monkeypatch, tmp_path):
    """The same edit made to the shipped rulebook FILE changes an extraction that passes no scope
    at all — which is how every real run reaches this code (``stages.extract`` runs before an
    ontology is attached)."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_group_company_pdf

    raw = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    raw["scope_selection"]["entity_scope"]["signals"] = []
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(raw), encoding="utf-8")

    words = _pdf_words(make_group_company_pdf())
    items, logs = _build(words)
    assert {b for b, _ in _slots(next(i for i in items if "Trade" in i.source_label))} == {
        "consolidated", "standalone"}
    assert any("two_basis_header" in m for m in logs)

    monkeypatch.setattr(rr, "_RULEBOOK_IN_FORCE", edited)
    rr.in_force_rules.cache_clear()
    try:
        items, logs = _build(words)
        tr = next(i for i in items if "Trade" in i.source_label)
        assert len({b for b, _ in _slots(tr)}) == 1
        assert not any("two_basis_header" in m for m in logs), logs
    finally:
        rr.in_force_rules.cache_clear()


def test_consolidated_standalone_headers_still_band():
    """The words the engine read before the rulebook declared any are still read: a page headed
    "Consolidated | Standalone" must not lose its second basis to the new both-or-nothing rule."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_dual_basis_pdf

    items, _ = _build(_pdf_words(make_dual_basis_pdf()))
    tr = next(i for i in items if "Trade receivables" in i.source_label)
    assert _slots(tr) == {
        ("consolidated", "current"): "3410", ("consolidated", "prior"): "2900",
        ("standalone", "current"): "3100", ("standalone", "prior"): "2700",
    }


def test_a_sentence_naming_both_entities_is_not_a_column_header():
    """"The Group and the Company had no material contingent liabilities" is prose. Banding on it
    splits the note's two-column comparative, so last year's guarantees are reported as this
    year's for a second entity."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_group_and_company_note_pdf

    items, logs = _build(_pdf_words(make_group_and_company_note_pdf()))
    total = next(i for i in items if "Total guarantees" in i.source_label)
    assert _slots(total) == {("consolidated", "current"): "1680",
                             ("consolidated", "prior"): "1440"}
    assert not any("entity_scope" in m for m in logs), logs


def test_the_filers_name_in_the_running_header_does_not_band_the_page():
    """"… Company Limited" is printed on every page of a filing, and the filer here is a "Group".
    Both words are in the running header, in separate runs, with no amount on the line — and the
    page still has one basis and two intact periods, because "Group" stands over the caption
    column and a caption bands only the figures it stands over."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_company_limited_header_pdf

    items, logs = _build(_pdf_words(make_company_limited_header_pdf()))
    total = next(i for i in items if "Total assets" in i.source_label)
    assert _slots(total) == {("consolidated", "current"): "6614",
                             ("consolidated", "prior"): "5680"}
    assert not any("entity_scope" in m for m in logs), logs


def _two_column_body(y0: float = 0.13) -> list[Word]:
    return [
        _w("Inventories", 0.10, y0), _w("2,000", 0.74, y0), _w("1,800", 0.86, y0),
        _w("Receivables", 0.10, y0 + 0.04), _w("3,410", 0.74, y0 + 0.04),
        _w("2,900", 0.86, y0 + 0.04),
        _w("Cash", 0.10, y0 + 0.08), _w("1,204", 0.74, y0 + 0.08), _w("980", 0.86, y0 + 0.08),
    ]


def _group_company_page() -> list[Word]:
    """The HKEX house style in words rather than through a renderer: "Group" over one dated pair,
    "Company" over the other, and the scale declared in the statement's own header."""
    words = [
        _w("RMB'000", 0.55, 0.03, 0.06),
        _w("Group", 0.58, 0.055), _w("Company", 0.80, 0.055),
        _w("2024", 0.55, 0.08), _w("2023", 0.67, 0.08),
        _w("2024", 0.79, 0.08), _w("2023", 0.91, 0.08),
    ]
    body = [("Inventories", ("2,000", "1,800", "200", "180")),
            ("Receivables", ("3,410", "2,900", "341", "290")),
            ("Cash", ("1,204", "980", "120", "98"))]
    for i, (label, cells) in enumerate(body):
        y = 0.12 + i * 0.04
        words.append(_w(label, 0.10, y, 0.10))
        words += [_w(v, x, y) for v, x in zip(cells, (0.55, 0.67, 0.79, 0.91))]
    return words


def test_a_row_that_reports_an_amount_is_never_a_basis_header():
    """Each of the next three tests strips the shape down to ONE guard: everything else about the
    row would pass. Here both captions are in the header area, in separate runs, over the value
    columns — and the row reports a figure, so it is a statement line (or prose citing one)."""
    words = [
        _w("Group", 0.72, 0.05), _w("Company", 0.84, 0.05), _w("1,200", 0.94, 0.05),
        _w("2024", 0.74, 0.09), _w("2023", 0.86, 0.09), *_two_column_body(),
    ]
    items, _ = _build(words)
    inv = next(i for i in items if "Inventories" in i.source_label)
    assert _slots(inv) == {("consolidated", "current"): "2000",
                           ("consolidated", "prior"): "1800"}


def test_two_captions_inside_one_phrase_are_not_two_column_captions():
    """Only the clear-air test refuses this one: the words sit over the value columns — one over
    each — in the header area, with no amount on the line. But they are one contiguous phrase,
    which is how a sentence is set and not how two column captions are."""
    words = [
        _w("Group", 0.72, 0.05, 0.06), _w("and", 0.79, 0.05, 0.03),
        _w("the", 0.825, 0.05, 0.025), _w("Company", 0.855, 0.05, 0.06),
        _w("2024", 0.74, 0.09), _w("2023", 0.86, 0.09), *_two_column_body(),
    ]
    items, _ = _build(words)
    inv = next(i for i in items if "Inventories" in i.source_label)
    assert _slots(inv) == {("consolidated", "current"): "2000",
                           ("consolidated", "prior"): "1800"}


def test_two_captions_over_one_column_cannot_band_it():
    """Only the one-column-each test refuses this one: two captions, clear air between them, no
    amounts, both over the value area — but both over the SAME column, so there is nothing for
    them to divide. Splitting on it would put both periods of one column in two bases."""
    words = [
        _w("Group", 0.70, 0.05, 0.05), _w("Company", 0.79, 0.05, 0.06),
        _w("2024", 0.74, 0.09), _w("2023", 0.86, 0.09), *_two_column_body(),
    ]
    items, _ = _build(words)
    inv = next(i for i in items if "Inventories" in i.source_label)
    assert _slots(inv) == {("consolidated", "current"): "2000",
                           ("consolidated", "prior"): "1800"}


def test_a_band_row_printed_tight_against_the_statement_is_still_read():
    """The page geometry is read from the rows as PRINTED, before ``_merge_wrapped_labels`` folds
    label-only lines into the valued line below them: a caption on a merged row no longer sits
    where it was printed, and every guard in ``_basis_bands`` is geometric. Here the band row is
    paragraph-tight against both the dated line above it and the first statement line below it —
    the spacing that makes the merge fire at all."""
    words = [
        _w("2024", 0.55, 0.03), _w("2023", 0.67, 0.03),
        _w("2024", 0.79, 0.03), _w("2023", 0.91, 0.03),
        _w("Group", 0.58, 0.055), _w("Company", 0.80, 0.055),
        # tight below the caption line — this is the row the merge would swallow it into
        _w("Inventories", 0.10, 0.067), _w("2,000", 0.55, 0.067), _w("1,800", 0.67, 0.067),
        _w("200", 0.79, 0.067), _w("180", 0.91, 0.067),
        _w("Receivables", 0.10, 0.15), _w("3,410", 0.55, 0.15), _w("2,900", 0.67, 0.15),
        _w("341", 0.79, 0.15), _w("290", 0.91, 0.15),
        _w("Cash", 0.10, 0.19), _w("1,204", 0.55, 0.19), _w("980", 0.67, 0.19),
        _w("120", 0.79, 0.19), _w("98", 0.91, 0.19),
    ]
    items, _ = _build(words)
    inv = next(i for i in items if i.source_label == "Inventories")
    assert _slots(inv) == {("consolidated", "current"): "2000",
                           ("consolidated", "prior"): "1800",
                           ("standalone", "current"): "200",
                           ("standalone", "prior"): "180"}


def test_the_band_row_itself_is_never_emitted_as_a_line_item():
    """The row that DECLARES the banding is furniture. It is printed paragraph-tight above the year
    captions, so the wrapped-label merge folds the two together and the column header arrived at the
    main loop as a row labelled "Group Company" carrying 2024, 2023, 2024, 2023 — four column YEARS
    filed as amounts, split neatly across the two bases the same row had just defined. A year read
    as a figure is the worst kind of wrong number: 2,024 is a plausible amount, it sits in a real
    value column under a real basis, and no total on the page contradicts it."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_group_company_pdf

    items, _ = _build(_pdf_words(make_group_company_pdf()))
    assert [i.source_label for i in items] == [
        "Investments in subsidiaries", "Trade receivables", "Cash and cash equivalents",
        "Total assets"]
    # …and no fact anywhere on the page is a year.
    assert not [str(v.value) for i in items for v in i.values.values()
                if str(v.value) in {"2024", "2023"}]


def _tall_bilingual_header_page() -> list[Word]:
    """A bilingual face whose header block is NINE printed lines deep before the basis captions.

    Nothing here is unusual: a running header in both languages, the statement title in both, the
    period caption in both, the currency note, the scale — and only then "Group | Company" over the
    dated columns. It is the header of the filings this corpus is made of.
    """
    words = [
        _w("Acme", 0.10, 0.020, 0.05), _w("Holdings", 0.16, 0.020, 0.07),
        _w("艾克美控股有限公司", 0.10, 0.038, 0.14),
        _w("Consolidated", 0.10, 0.056, 0.09), _w("Statement", 0.20, 0.056, 0.07),
        _w("of", 0.28, 0.056, 0.02), _w("Financial", 0.31, 0.056, 0.06),
        _w("Position", 0.38, 0.056, 0.05),
        _w("綜合財務狀況表", 0.10, 0.074, 0.11),
        _w("As", 0.10, 0.092, 0.02), _w("at", 0.13, 0.092, 0.02),
        _w("31", 0.16, 0.092, 0.02), _w("December", 0.19, 0.092, 0.07),
        _w("2024", 0.27, 0.092, 0.03),
        _w("於二零二四年十二月三十一日", 0.10, 0.110, 0.18),
        _w("(Expressed", 0.10, 0.128, 0.07), _w("in", 0.18, 0.128, 0.02),
        _w("Renminbi)", 0.21, 0.128, 0.07),
        _w("RMB'000", 0.55, 0.146, 0.06),
        # the basis captions — printed row nine, which a fixed eight-row bound never reached
        _w("Group", 0.58, 0.164), _w("Company", 0.80, 0.164),
        _w("2024", 0.55, 0.182), _w("2023", 0.67, 0.182),
        _w("2024", 0.79, 0.182), _w("2023", 0.91, 0.182),
    ]
    body = [("Inventories", ("2,000", "1,800", "200", "180")),
            ("Receivables", ("3,410", "2,900", "341", "290")),
            ("Cash", ("1,204", "980", "120", "98"))]
    for i, (label, cells) in enumerate(body):
        y = 0.22 + i * 0.04
        words.append(_w(label, 0.10, y, 0.10))
        words += [_w(v, x, y) for v, x in zip(cells, (0.55, 0.67, 0.79, 0.91))]
    return words


def test_a_tall_bilingual_header_still_bands_the_page():
    """The header-area bound was a fixed count of eight printed rows, which is a bound on the wrong
    quantity: a bilingual filing stacks two running-header lines, two title lines, two period
    captions and a units line before it prints the basis captions, so they land on row nine and the
    Group|Company banding was lost — silently adding the Company's column to the Group's. Bounded by
    the geometry instead (the value-column header band; the page fraction), with every false-positive
    veto untouched, because a false positive SPLITS a comparative."""
    items, logs = _build(_tall_bilingual_header_page())
    inv = next(i for i in items if i.source_label == "Inventories")
    assert _slots(inv) == {("consolidated", "current"): "2000",
                          ("consolidated", "prior"): "1800",
                          ("standalone", "current"): "200",
                          ("standalone", "prior"): "180"}
    assert any("entity_scope=two_basis_header" in m for m in logs), logs
    # The same region is what the units caption is resolved from — one header band, one bound.
    assert any("units=CNY/thousand" in m for m in logs), logs


def test_a_basis_caption_outside_the_header_area_is_ignored():
    """The detector used to scan EVERY row, so a page footer or a footnote could define the bands
    for the whole page. Bounded to the header area the way ``_period_bands`` is — this row would
    pass every other guard (two captions, separate runs, over the value columns, no amounts) and
    is refused on position alone."""
    header = [_w("2024", 0.74, 0.05), _w("2023", 0.86, 0.05)]
    body = [w for i in range(9)
            for w in (_w(f"Item{i}", 0.10, 0.09 + i * 0.04),
                      _w("1,000", 0.74, 0.09 + i * 0.04),
                      _w("900", 0.86, 0.09 + i * 0.04))]
    footer = [_w("Group", 0.72, 0.60), _w("Company", 0.88, 0.60)]
    words = header + body + footer
    items, logs = _build(words)
    assert {b for i in items for b, _ in _slots(i)} == {"consolidated"}
    assert not any("two_basis_header" in m for m in logs), logs


def _company_only_page() -> list[Word]:
    return [
        _w("2024", 0.74, 0.05), _w("2023", 0.86, 0.05),
        _w("Investments", 0.10, 0.09), _w("in", 0.17, 0.09), _w("subsidiaries", 0.20, 0.09),
        _w("8,000", 0.74, 0.09), _w("7,500", 0.86, 0.09),
        _w("Trade", 0.10, 0.13), _w("receivables", 0.16, 0.13),
        _w("310", 0.74, 0.13), _w("270", 0.86, 0.13),
        _w("Cash", 0.10, 0.17), _w("105", 0.74, 0.17), _w("90", 0.86, 0.17),
    ]


def test_a_company_only_marker_files_an_unbanded_page_as_the_company():
    """``company_only_markers``: consolidation eliminates a parent's investment in its
    subsidiaries, so a face that prints one is the Company's own statement. Filed as the Group's
    it adds an intra-group asset to the consolidated balance sheet."""
    items, logs = _build(_company_only_page())
    assert {b for i in items for b, _ in _slots(i)} == {"standalone"}
    assert any("entity_scope=company_only" in m for m in logs), logs


def test_clearing_the_declared_marker_returns_the_page_to_the_default_basis():
    """The marker is the rulebook's, parsed out of the declared sentence — cleared, the page is
    read as consolidated again (``entity_scope.default``)."""
    items, _ = _build(_company_only_page(),
                      scope=_scope(**{"entity_scope.company_only_markers": []}))
    assert {b for i in items for b, _ in _slots(i)} == {"consolidated"}


def test_a_note_is_not_relabelled_by_the_company_only_marker():
    """The rule is declared about the FACE. "Investments in subsidiaries" is exactly what a NOTE
    of a consolidated filing itemises, and relabelling that note standalone breaks the note→face
    tie, which matches on (basis, period)."""
    from app.services.notes_extract import extract_note_tables

    words = [_w("Note", 0.10, 0.02), _w("18:", 0.15, 0.02),
             _w("Investments", 0.20, 0.02), _w("in", 0.28, 0.02),
             _w("subsidiaries", 0.31, 0.02), *_company_only_page()]
    tables = extract_note_tables(words, page_index=1, document_id=None, source_kind="native")
    bases = {v.basis.value for t in tables for it in t.items for v in it.values.values()}
    assert bases == {"consolidated"}


# --- period_selection -------------------------------------------------------------------------

def _comparative_first_page() -> list[Word]:
    """A page that prints the COMPARATIVE on the left — "HKEX filings are not consistently
    current-first"."""
    return [
        _w("2023", 0.74, 0.05), _w("2024", 0.86, 0.05),
        _w("Revenue", 0.10, 0.09), _w("100", 0.74, 0.09), _w("120", 0.86, 0.09),
        _w("Costs", 0.10, 0.13), _w("40", 0.74, 0.13), _w("50", 0.86, 0.13),
        _w("Profit", 0.10, 0.17), _w("60", 0.74, 0.17), _w("70", 0.86, 0.17),
    ]


def test_the_current_period_is_the_latest_heading_date_not_the_leftmost_column():
    items, logs = _build(_comparative_first_page())
    rev = next(i for i in items if "Revenue" in i.source_label)
    assert _slots(rev) == {("consolidated", "prior"): "100",
                           ("consolidated", "current"): "120"}
    assert any("period_selection=by_heading_date" in m for m in logs), logs


def test_a_cjk_dated_header_is_read_as_a_date():
    """The comparative columns of a Chinese filing are headed 二零二四年 / 二零二三年."""
    words = [
        _w("二零二三年", 0.74, 0.05), _w("二零二四年", 0.86, 0.05),
        _w("收益", 0.10, 0.09), _w("1,000", 0.74, 0.09), _w("1,200", 0.86, 0.09),
        _w("成本", 0.10, 0.13), _w("400", 0.74, 0.13), _w("500", 0.86, 0.13),
        _w("溢利", 0.10, 0.17), _w("600", 0.74, 0.17), _w("700", 0.86, 0.17),
    ]
    items, _ = _build(words)
    rev = next(i for i in items if "收益" in i.source_label)
    assert _slots(rev) == {("consolidated", "prior"): "1000",
                           ("consolidated", "current"): "1200"}


def test_an_arabic_year_under_a_cjk_suffix_is_read_as_a_date():
    """A bilingual filing heads its comparative columns "2024年" / "2023年" as readily as it does in
    CJK numerals, and the suffix hid the year from both patterns that could have read it: the digits
    are not word-final for ``\\b(19|20)\\d{2}\\b`` (年 is a word character) and the token is not a CJK
    date. With no readable heading the current period fell back to column POSITION — a year out on
    every filing that prints the comparative first, which is exactly what ``period_selection``
    declares against."""
    words = [
        _w("2023年", 0.74, 0.05, 0.05), _w("2024年", 0.86, 0.05, 0.05),
        _w("收益", 0.10, 0.09), _w("1,000", 0.74, 0.09), _w("1,200", 0.86, 0.09),
        _w("成本", 0.10, 0.13), _w("400", 0.74, 0.13), _w("500", 0.86, 0.13),
        _w("溢利", 0.10, 0.17), _w("600", 0.74, 0.17), _w("700", 0.86, 0.17),
    ]
    items, logs = _build(words)
    rev = next(i for i in items if "收益" in i.source_label)
    assert _slots(rev) == {("consolidated", "prior"): "1000",
                           ("consolidated", "current"): "1200"}
    assert any("period_selection=by_heading_date" in m for m in logs), logs
    # The printed heading is kept as the column's display date, as it is for every other form.
    assert {v.period_label: v.period_display for v in rev.values.values()} == {
        "prior": "2023年", "current": "2024年"}
    # The same form carries the month and the day, and an interim filing prints two periods of one
    # year — so the year alone cannot order them.
    assert rr._period_date("2024年12月31日") == (2024, 12, 31)
    assert rr._period_date("2024年6月30日") == (2024, 6, 30)


def _restated_page(marker: str = "(restated)") -> list[Word]:
    return [
        _w("2024", 0.62, 0.05), _w("2023", 0.74, 0.05), _w("2023", 0.86, 0.05),
        _w(marker, 0.86, 0.068, 0.08),
        _w("Revenue", 0.10, 0.11), _w("120", 0.62, 0.11), _w("100", 0.74, 0.11),
        _w("101", 0.86, 0.11),
        _w("Costs", 0.10, 0.15), _w("50", 0.62, 0.15), _w("40", 0.74, 0.15),
        _w("41", 0.86, 0.15),
        _w("Profit", 0.10, 0.19), _w("70", 0.62, 0.19), _w("60", 0.74, 0.19),
        _w("61", 0.86, 0.19),
    ]


def test_a_restated_comparative_never_overwrites_the_original():
    """``restatement_rule``: "Do not overwrite a previously loaded original comparative; keep both
    with a restatement flag." Two columns of the same year share a period slot, and the ValueKey
    has nowhere to put the flag — so the restated one is stored beside the original, named."""
    items, logs = _build(_restated_page())
    rev = next(i for i in items if "Revenue" in i.source_label)
    assert _slots(rev) == {("consolidated", "current"): "120",
                           ("consolidated", "prior"): "100",
                           ("consolidated", "prior_restated"): "101"}
    assert any("period_selection=kept_both" in m for m in logs), logs


def test_a_chinese_restatement_marker_is_recognised():
    items, _ = _build(_restated_page("經重列"))
    rev = next(i for i in items if "Revenue" in i.source_label)
    assert ("consolidated", "prior_restated") in _slots(rev)


def test_dropping_the_declared_markers_stops_the_restatement_handling():
    """With the marker list gone from ``restatement_rule`` the restated column is just a second
    comparative — which is what the engine did before the rule was implemented."""
    items, _ = _build(_restated_page(),
                      scope=_scope(**{"period_selection.restatement_rule": "keep both"}))
    rev = next(i for i in items if "Revenue" in i.source_label)
    assert ("consolidated", "prior_restated") not in _slots(rev)
    assert ("consolidated", "prior_col2") in _slots(rev)      # kept, but not named as restated


# --- units_and_currency -----------------------------------------------------------------------

def _unit_page(total: str = "6,000") -> list[Word]:
    return [
        _w("RMB'000", 0.72, 0.03, 0.08),
        _w("2024", 0.74, 0.06), _w("2023", 0.86, 0.06),
        _w("Inventories", 0.10, 0.10), _w("2,000", 0.74, 0.10), _w("1,800", 0.86, 0.10),
        _w("Receivables", 0.10, 0.14), _w("3,000", 0.74, 0.14), _w("2,900", 0.86, 0.14),
        _w("Cash", 0.10, 0.18), _w("1,000", 0.74, 0.18), _w("980", 0.86, 0.18),
        _w("Total", 0.10, 0.22), _w("assets", 0.16, 0.22),
        _w(total, 0.74, 0.22), _w("5,680", 0.86, 0.22),
    ]


def test_the_unit_is_resolved_from_the_statement_header_and_kept_on_every_fact():
    items, logs = _build(_unit_page())
    units = {(v.unit_ctx.currency, str(v.unit_ctx.scale_factor), v.unit_ctx.units_label)
             for i in items for v in i.values.values()}
    assert units == {("CNY", "1000", "thousand")}
    assert any("units=CNY/thousand" in m for m in logs), logs
    # The figures themselves are untouched — "never normalise scale silently".
    inv = next(i for i in items if "Inventories" in i.source_label)
    assert _slots(inv)[("consolidated", "current")] == "2000"


def test_a_unit_the_rulebook_does_not_declare_is_not_read_off_the_page():
    items, _ = _build(_unit_page(), scope=_scope(**{"units_and_currency.signals": ["HK$'000"]}))
    units = {(v.unit_ctx.currency, str(v.unit_ctx.scale_factor))
             for i in items for v in i.values.values()}
    assert units == {("", "1")}


_MATRIX_ROWS = [("At 1 January 2024", ["365,138", "1,200", "17,619", "21,494", "21,879"]),
                ("Loss for the year", ["–", "–", "–", "(7,991)", "(7,991)"]),
                ("Dividends paid", ["–", "–", "–", "(1,220)", "(1,220)"])]


def _matrix_page() -> list[Word]:
    """A statement of changes in equity: five component columns, its scale in its own header."""
    right = [0.50, 0.60, 0.70, 0.80, 0.90]
    names = ["Capital", "Premium", "Reserves", "Profits", "Total"]
    words = [_w(n, x - 0.05, 0.10, 0.05) for n, x in zip(names, right)]
    words += [_w("RMB'000", x - 0.05, 0.13, 0.05) for x in right]
    for i, (label, cells) in enumerate(_MATRIX_ROWS):
        y = 0.20 + i * 0.04
        words += [_w(tok, 0.05 + j * 0.05, y, 0.045) for j, tok in enumerate(label.split())]
        words += [_w(v, x - 0.008 * len(v), y, 0.008 * len(v)) for v, x in zip(cells, right)]
    return words


def test_the_matrix_path_also_persists_the_statements_unit():
    """"Persist unit on every fact" is not qualified by layout: a statement of changes in equity
    declares its scale in its own header like any other face, and its facts are the ones an equity
    reconciliation reads."""
    items, _ = _build(_matrix_page(), statement="changes_in_equity")
    assert [i.source_label for i in items] == [label for label, _ in _MATRIX_ROWS]
    units = {(v.unit_ctx.currency, str(v.unit_ctx.scale_factor))
             for i in items for v in i.values.values()}
    assert units == {("CNY", "1000")}


def test_a_thousandfold_subtotal_conflict_routes_the_statement_to_review():
    """"If header scale and a printed subtotal are inconsistent by a factor of 1,000, trust
    neither." The header says RMB'000 and the printed total is in units: 2,000 + 3,000 + 1,000
    against a total of 6,000,000. Neither reading is applied, and the statement is flagged."""
    items, logs = _build(_unit_page("6,000,000"))
    units = {(v.unit_ctx.currency, str(v.unit_ctx.scale_factor))
             for i in items for v in i.values.values()}
    assert units == {("", "1")}
    assert any("units_conflict" in m and m.endswith(":review") for m in logs), logs


def test_no_conflict_is_reported_when_the_subtotal_agrees_with_the_header():
    _items, logs = _build(_unit_page())
    assert not any("units_conflict" in m for m in logs), logs


def test_removing_the_declared_factor_removes_the_conflict_check():
    """The 1,000 is read out of the declared sentence; with the sentence emptied the check has no
    factor to test and does not run (and the header scale is then trusted, as it was before)."""
    items, logs = _build(_unit_page("6,000,000"),
                         scope=_scope(**{"units_and_currency.conflict": ""}))
    units = {str(v.unit_ctx.scale_factor) for i in items for v in i.values.values()}
    assert units == {"1000"}
    assert not any("units_conflict" in m for m in logs), logs


# --- a threaded rulebook beats the module-level default, on every path -------------------------

def _group_company_sheet() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    import io

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BS"
    for row in (["", "RMB'000", None, "RMB'000", None],
                ["", "Group", None, "Company", None],
                ["", "2024", "2023", "2024", "2023"],
                ["Inventories", 2000, 1800, 200, 180],
                ["Trade receivables", 3410, 2900, 310, 270],
                ["Cash and cash equivalents", 1204, 980, 105, 90]):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_a_passed_rulebook_beats_the_module_default_on_every_path(monkeypatch, tmp_path):
    """``build_line_items(scope=…, normalisation=…)`` is how a run pinned to its own rulebook has its
    pages read by that rulebook. It is only worth anything if EVERY path honours it, so this pins the
    rulebook in force to one that declares nothing — no entity signals, no units signals — and then
    passes the real blocks explicitly to each path in turn. Both halves are asserted: the sabotaged
    default really is blind, and the passed blocks really do restore the reading.

    The workbook path is the one this closes. It read ``in_force_rules()`` with no parameter to pass
    anything in, so a run pinned to rulebook A had its spreadsheets read under rulebook B — the same
    figures governed by different rules depending on the file format, which is the one thing a
    pinned rulebook exists to prevent.
    """
    from app.services.excel_extract import extract_workbook

    raw = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    raw["scope_selection"]["entity_scope"]["signals"] = []
    raw["scope_selection"]["units_and_currency"]["signals"] = []
    blind = tmp_path / "blind.json"
    blind.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(rr, "_RULEBOOK_IN_FORCE", blind)
    rr.in_force_rules.cache_clear()

    def units(items) -> set[tuple[str, str]]:
        return {(v.unit_ctx.currency, str(v.unit_ctx.scale_factor))
                for i in items for v in i.values.values()}

    def bases(items) -> set[str]:
        return {v.basis.value for i in items for v in i.values.values()}

    try:
        pinned = {"scope": _scope(), "normalisation": _normalisation()}
        # The default in force is genuinely blind: no unit resolved, and no Group|Company band.
        assert units(_build(_unit_page())[0]) == {("", "1")}
        assert bases(_build(_group_company_page())[0]) == {"consolidated"}
        assert units(_build(_matrix_page(), statement="changes_in_equity")[0]) == {("", "1")}
        assert units(extract_workbook(_group_company_sheet(), document_id="x")) == {("", "1")}
        assert {v.basis.value for i in extract_workbook(_group_company_sheet(), document_id="x")
                for v in i.values.values()} == {"consolidated"}

        # Passed in, the rulebook governs — two-column, matrix, notes (on_face=False) and workbook.
        assert units(_build(_unit_page(), **pinned)[0]) == {("CNY", "1000")}
        assert bases(_build(_group_company_page(), **pinned)[0]) == {"consolidated", "standalone"}
        assert units(_build(_matrix_page(), statement="changes_in_equity",
                            **pinned)[0]) == {("CNY", "1000")}
        assert units(_build(_unit_page(), on_face=False, **pinned)[0]) == {("CNY", "1000")}
        wb_items = extract_workbook(_group_company_sheet(), document_id="x", **pinned)
        assert units(wb_items) == {("CNY", "1000")}
        assert {v.basis.value for i in wb_items for v in i.values.values()} == {
            "consolidated", "standalone"}
    finally:
        rr.in_force_rules.cache_clear()


# --- column_guard -----------------------------------------------------------------------------

def _fact(value: int, *, currency: str = "CNY", scale: int = 1000,
          period: str = "prior") -> ExtractedValue:
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis.CONSOLIDATED, period_label=period,
                          unit_ctx=UnitContext(currency=currency, scale_factor=Decimal(scale)))


def test_two_facts_differing_only_on_currency_are_two_facts():
    """``ValueKey`` is (basis, period_end, period_label) — it cannot express a difference in
    currency or scale, so the second fact used to REPLACE the first in silence."""
    li = LineItem(source_label="Trade receivables")
    dims = rr.guard_dimensions(_scope())
    rr.store_fact(li, _fact(100, currency="CNY"), dims)
    rr.store_fact(li, _fact(110, currency="HKD"), dims)
    assert sorted(str(v.value) for v in li.values.values()) == ["100", "110"]


def test_dropping_currency_from_the_declared_dimensions_collapses_them():
    """Proof the dimension list is read from ``column_guard`` and not assumed: with currency
    removed from the declared identity the two readings ARE the same fact."""
    li = LineItem(source_label="Trade receivables")
    dims = rr.guard_dimensions(_scope(column_guard="Every fact carries the resolved "
                                                  "(entity_scope, period)."))
    rr.store_fact(li, _fact(100, currency="CNY"), dims)
    rr.store_fact(li, _fact(110, currency="HKD"), dims)
    assert [str(v.value) for v in li.values.values()] == ["100"]


def test_a_second_reading_of_one_column_keeps_the_first_figure():
    """Two facts identical on every declared dimension are one fact read twice. Keeping the FIRST
    is the auditable choice: overwriting means the row reports whichever cell the geometry
    happened to visit last."""
    li = LineItem(source_label="Trade receivables")
    logs: list[str] = []
    dims = rr.guard_dimensions(_scope())
    rr.store_fact(li, _fact(100), dims, log=logs.append)
    rr.store_fact(li, _fact(110), dims, log=logs.append)
    assert [str(v.value) for v in li.values.values()] == ["100"]
    assert any("duplicate_fact_dropped" in m for m in logs), logs


def test_the_group_and_company_page_carries_four_distinct_facts_for_one_row():
    """The same canonical row differing on entity_scope and period is four facts, not one — the
    reason the guard exists at all."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_group_company_pdf

    items, _ = _build(_pdf_words(make_group_company_pdf()))
    cash = next(i for i in items if "Cash and cash equivalents" in i.source_label)
    assert len(_slots(cash)) == 4


# --- normalisation.pipeline -------------------------------------------------------------------

@pytest.mark.parametrize(("printed", "expected"), [
    ("Trade receivables (note 12)", "trade receivables"),          # footnote / note markers
    ("其他應付款項（附註12）", "其他应付款项"),
    ("1. Non-operating expenses", "non-operating expenses"),       # leading numbering
    ("(a) Other income", "other income"),
    ("一、非經營開支", "非经营开支"),
    ("Share of profits and losses of:", "share of profits and losses of"),   # trailing colon
    ("Revenue RMB'000", "revenue"),                                # inline unit annotation
    ("二零二三年（未經審核）", "二零二三年"),
    ("Cash​ and­ cash  equivalents", "cash and cash equivalents"),  # zero-width, spaces
    ("研發／開發成本", "研发/开发成本"),                              # ╱／⁄ → /
    ("存貨、應收款項", "存货,应收款项"),                               # 、 → ,
])
def test_the_declared_strip_rules_are_implemented(printed, expected):
    """Every one of these is a step the rulebook's ``pipeline`` lists. An unimplemented step is a
    policy a reviewer can look up and the engine ignores."""
    steps = rr._pipeline_steps(_normalisation(), _scope())
    assert rr.apply_pipeline(printed, steps) == expected


def test_the_pipeline_runs_in_the_order_the_rulebook_declares():
    """The declared list is ORDERED, and the engine applies it in that order — so reordering the
    rulebook reorders the folds, and a step it does not list does not run."""
    _, block = _raw_blocks()
    ids = [sid for sid, _ in rr._pipeline_steps(_normalisation(), _scope())]
    assert ids == ["nfkc", "t2s", "case_fold", "width", "footnote", "numbering",
                   "trailing_colon", "annotation", "whitespace", "wrapped_caption"]
    reversed_ids = [sid for sid, _ in
                    rr._pipeline_steps(_normalisation(list(reversed(block["pipeline"]))), _scope())]
    assert reversed_ids == list(reversed(ids))
    # A rulebook that declares no pipeline gets no folding, rather than a hardcoded one.
    assert rr._pipeline_steps(_normalisation([]), _scope()) == ()


def _units_caption_row() -> list[Word]:
    """A column-header row whose caption is nothing but an inline unit annotation."""
    return [
        _w("人民幣千元", 0.66, 0.05, 0.08), _w("2024", 0.74, 0.05), _w("2023", 0.86, 0.05),
        _w("Inventories", 0.10, 0.09), _w("2,000", 0.74, 0.09), _w("1,800", 0.86, 0.09),
        _w("Receivables", 0.10, 0.13), _w("3,000", 0.74, 0.13), _w("2,900", 0.86, 0.13),
        _w("Cash", 0.10, 0.17), _w("1,000", 0.74, 0.17), _w("980", 0.86, 0.17),
    ]


def test_a_units_caption_row_is_not_a_line_item():
    """"人民幣千元  2024  2023" is a header. Its caption survived the noise test, so the row was
    emitted as a line item whose two "amounts" were the years."""
    items, _ = _build(_units_caption_row())
    assert [i.source_label for i in items] == ["Inventories", "Receivables", "Cash"]


def test_removing_the_annotation_step_lets_the_header_row_through():
    """The same page with the "Strip inline unit and currency annotations" step deleted from the
    declared pipeline: the row comes back as a line item reporting 2024 and 2023."""
    _, block = _raw_blocks()
    without = [s for s in block["pipeline"] if "unit and currency annotation" not in s]
    items, _ = _build(_units_caption_row(), normalisation=_normalisation(without))
    assert any("千元" in i.source_label for i in items)


def test_a_units_caption_row_never_becomes_the_section():
    items, _ = _build(_units_caption_row())
    assert {i.section_hint for i in items} == {None}


# --- normalisation.wrapped_caption_rule + title-case banners -----------------------------------

def test_a_title_case_banner_sets_the_section():
    """A label-only row reached ``section_hint`` only when it was ALL-CAPS or ended in a colon, so
    a filing printing "Non-operating expenses" in title case set NO section — and the section gate
    then had nothing to tell those captions apart from the ones printed elsewhere."""
    words = [
        _w("Non-operating", 0.10, 0.05, 0.10), _w("expenses", 0.21, 0.05),
        _w("Interest", 0.10, 0.12), _w("expense", 0.17, 0.12),
        _w("1,234", 0.74, 0.12), _w("1,000", 0.86, 0.12),
        _w("Bank", 0.10, 0.16), _w("charges", 0.17, 0.16),
        _w("12", 0.74, 0.16), _w("10", 0.86, 0.16),
        _w("Sundry", 0.10, 0.20), _w("losses", 0.17, 0.20),
        _w("5", 0.74, 0.20), _w("6", 0.86, 0.20),
    ]
    items, _ = _build(words)
    assert [i.section_hint for i in items] == ["Non-operating expenses"] * 3


def test_a_fullwidth_colon_subheading_is_a_heading_not_a_wrapped_caption():
    """A CJK sub-heading ends in the FULLWIDTH colon, which ``endswith(":")`` never saw — so the
    heading was taken for the first line of a wrapped caption and glued onto the row below it
    ("調整： 折舊" instead of "折舊"). The rulebook's fullwidth→halfwidth step is what makes the
    colon count, and the heading then correctly leaves the section it introduces alone."""
    words = [
        _w("EXPENSES", 0.10, 0.05),
        _w("調整：", 0.10, 0.150, 0.05),
        _w("折舊", 0.10, 0.163), _w("1,234", 0.74, 0.163), _w("1,000", 0.86, 0.163),
        _w("Bank", 0.10, 0.20), _w("charges", 0.17, 0.20),
        _w("12", 0.74, 0.20), _w("10", 0.86, 0.20),
        _w("Sundry", 0.10, 0.24), _w("losses", 0.17, 0.24),
        _w("5", 0.74, 0.24), _w("6", 0.86, 0.24),
    ]
    items, _ = _build(words)
    assert [i.source_label for i in items] == ["折舊", "Bank charges", "Sundry losses"]
    assert {i.section_hint for i in items} == {"EXPENSES"}


# --- a small figure is a figure ----------------------------------------------------------------
def test_a_two_digit_first_figure_is_not_taken_for_a_note_reference():
    """A bare 1-2 digit token between the label and the figures is a note reference only when a
    real amount follows it — "Revenue 6 45,230 40,110". Read on shape alone it swallows the
    leftmost value of any row whose figures are small, and the damage does not stop at one cell:
    with some rows down to two figures the page's inferred column geometry collapses to two, and
    a row that kept three has its leftmost figure fall outside them and dropped as well. A
    three-column statement then comes back as two, with the years relabelled.
    """
    page = [
        _w("2024", 0.62, 0.05), _w("2023", 0.74, 0.05),
        # Every figure here is two digits, which is exactly the shape a note reference has.
        _w("Costs", 0.10, 0.11), _w("50", 0.62, 0.11), _w("40", 0.74, 0.11),
        _w("Profit", 0.10, 0.15), _w("70", 0.62, 0.15), _w("60", 0.74, 0.15),
    ]
    items, _ = _build(page)
    costs = next(i for i in items if "Costs" in i.source_label)
    assert _slots(costs) == {("consolidated", "current"): "50",
                             ("consolidated", "prior"): "40"}
    assert costs.note_number is None, costs.note_number


def test_a_note_reference_before_real_amounts_is_still_read_as_one():
    """The behaviour the guard must not remove: a genuine note column, proven by the amounts
    that follow it carrying thousands separators."""
    page = [
        _w("2024", 0.62, 0.05), _w("2023", 0.74, 0.05),
        _w("Revenue", 0.10, 0.11), _w("6", 0.40, 0.11),
        _w("45,230", 0.62, 0.11), _w("40,110", 0.74, 0.11),
    ]
    items, _ = _build(page)
    rev = next(i for i in items if "Revenue" in i.source_label)
    assert _slots(rev) == {("consolidated", "current"): "45230",
                           ("consolidated", "prior"): "40110"}
    assert rev.note_number == "6"
