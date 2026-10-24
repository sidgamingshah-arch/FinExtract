"""Cash flow rows that kept China SCE's, 佳明's and BYD Electronic's figures off print.

Measured answer-free at c2e2daf (thousands, current / prior; printed in brackets):

* CHINA SCE (hkex_1966) CFO 2,044,146 / 15,297,250 (5,094,092 / 16,429,204) and CFI 4,094,176 /
  -3,298,480 (4,044,304 / -2,604,642). Three rows are the whole gap in both years:
  - 'Write down to net realisable value of completed properties held for sale and properties
    under development' (2,951,850 / 989,975) reached no line: the caption was an alias of the
    income statement's Asset impairment loss only, and the residual refuses a caption that long.
  - 'Proceeds from disposal of financial assets at fair value through profit or loss' (48,224 /
    835,817) reached no line: the line carried only '... at FVTPL'.
  - 'Interest received' (98,096 / 141,979), printed under OPERATING beside the taxes paid, went to
    Interest Income Rcvd(Invest). The page it is printed on (page index 109) continues the
    operating section under the tail of the statement's wrapped title ('CASH FLOWS', printed at
    the head of every cash flow page), which names no section, so the investing line's alias was
    taken ungated.
* 佳明 (hkex_kaming_2025_26) CFF -170,862 / -372,160 (-170,883 / -372,128): 'Effect of foreign
  exchange rate change / on cash and cash equivalents' wraps with its Chinese interleaved, and the
  tail (21 / -32) was read as a row of its own and swept into Other Financing Cash Flows.
* BYD ELECTRONIC (hkex_0285) 'Depreciation of property, plant and equipment' (4,875,202 /
  2,787,817) is printed as an English line over its Chinese translation, which carries the figures.
  The English is a caption the rulebook knows, so the reader took it for a line item with no
  figures and dropped it; '物業、廠房及設備折舊' alone matched nothing and was swept into Other
  Non-Cash Adjs(Oper).
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
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher, known_captions
from app.services.pdf_extract import _chrome_key
from app.services.row_reconstruct import Word, build_line_items
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"

OPERATING = "CASH FLOWS FROM OPERATING ACTIVITIES 經營活動現金流量"
INVESTING = "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動現金流量"
INTEREST_OPER = "cf_oper_indirect__interest_inc_received_oper"
INTEREST_INVEST = "cf_investing__interest_income_rcvd_invest"
#: The banner China SCE's rows on page index 109 are read under: the second line of the wrapped
#: title "CONSOLIDATED STATEMENT OF / CASH FLOWS", which names no section.
TITLE_TAIL = "CASH FLOWS"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def ontology(shipped):
    return build_working_view(shipped)


@pytest.fixture(scope="module")
def matcher(ontology):
    return OntologyMatcher(ontology)


# ── the rows' own captions ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("caption,section,key", [
    ("Write down to net realisable value of completed properties held for sale and properties "
     "under development 持作出售已落成物業及發展中物業減值至可變現淨值", OPERATING,
     "cf_oper_indirect__amortization_and_asset_impairment"),
    ("Proceeds from disposal of financial assets at fair value through profit or loss "
     "出售按公允值計量且其變動計入損益的金融資產所得款項", INVESTING,
     "cf_investing__proceeds_sale_fncl_assets"),
    ("Interest received 已收利息", OPERATING, INTEREST_OPER),
    ("Effect of foreign exchange rate change on cash and cash equivalents "
     "外幣匯率變動對現金及現金等價物的影響", "Financing activities 融資活動",
     "cf_financing__net_foreign_exchange_difference"),
])
def test_each_row_binds_the_line_its_definition_names(matcher, caption, section, key):
    res = matcher.match(caption, statement="cash_flow", section=section)
    assert res.canonical_key == key, (res.canonical_key, [c.canonical_key for c in res.candidates])


def test_interest_received_under_investing_stays_investing(matcher):
    """The two interest-received lines are told apart by the section only, as both define."""
    res = matcher.match("Interest received 已收利息", statement="cash_flow", section=INVESTING)
    assert res.canonical_key == INTEREST_INVEST


def test_the_income_statement_write_down_is_still_asset_impairment(matcher):
    res = matcher.match("Write down to net realisable value of completed properties held for sale "
                        "and properties under development", statement="profit_and_loss")
    assert res.canonical_key == "is_pl__asset_impairment_loss"


# ── a row is read in the section it is printed under ────────────────────────────────────────────

def _row(caption, current, prior, page, section):
    row = LineItem(source_label=caption, role=LineRole.LINE, section_hint=section)
    for period, value in (("current", current), ("prior", prior)):
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                     value=Decimal(value), value_raw=Decimal(value),
                                     provenance=Provenance(page_index=page)))
    return row


def _map(rows, pages, shipped, ontology):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = pages
    doc.line_items = rows
    # China SCE prints the tail of its wrapped title, "CASH FLOWS", at the head of every cash flow
    # page; the reader finds it repeated and records it as page furniture.
    doc.page_chrome = [_chrome_key(TITLE_TAIL)]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = ontology
    ctx.line_items = shipped
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    return rows


def _china_sce(continued_under):
    title_tail = continued_under
    return [
        _row("Profit/(loss) before tax 除稅前溢利╱（虧損）", "-8211620", "819428", 108, OPERATING),
        _row("Finance costs 財務費用", "1492343", "921124", 108, OPERATING),
        _row("Increase in properties under development 發展中物業增加", "-7512766", "-9912704",
             109, title_tail),
        _row("Cash generated from operations 經營產生所得現金", "5600401", "17472502", 109,
             title_tail),
        _row("Interest received 已收利息", "98096", "141979", 109, title_tail),
        _row("PRC corporate income tax paid 已付中國企業所得稅", "-559917", "-846790", 109,
             title_tail),
        _row("Net cash flows from operating activities 經營活動所得現金流量淨額", "5094092",
             "16429204", 109, title_tail),
        _row("Additions to investment properties 增添投資物業", "-1612223", "-2595241", 110,
             INVESTING),
        _row("Interest received 已收利息", "11", "12", 110, INVESTING),
    ]


def _pages():
    return [PageSource(index=i, kind=PageKind.FACE, statement="cash_flow") for i in (108, 109, 110)]


@pytest.mark.parametrize("continued_under", [TITLE_TAIL, None])
def test_a_row_on_a_continuation_page_is_read_in_the_section_in_force(shipped, ontology,
                                                                       continued_under):
    """China SCE page index 109: no banner, only the tail of the wrapped title — or nothing."""
    rows = _map(_china_sce(continued_under), _pages(), shipped, ontology)
    operating_interest, investing_interest = rows[4], rows[8]
    assert operating_interest.canonical_key == INTEREST_OPER, (
        operating_interest.canonical_key, operating_interest.confidence.flags)
    assert investing_interest.canonical_key == INTEREST_INVEST


def test_a_banner_the_row_was_read_under_still_decides(shipped, ontology):
    """The carried section is only consulted when the row's own banner names none."""
    rows = _china_sce(TITLE_TAIL)
    rows[4].section_hint = INVESTING
    _map(rows, _pages(), shipped, ontology)
    assert rows[4].canonical_key == INTEREST_INVEST


# ── the reader: one bilingual caption over two printed lines ────────────────────────────────────

def _w(text, x0, y0, y1, width=None):
    han = sum(1 for ch in text if "㐀" <= ch <= "鿿")
    w = width if width is not None else 0.0075 * (len(text) - han) + 0.0155 * han
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + w, y1=y1))


def _latin(text, x0, y0, y1):
    out, x = [], x0
    for t in text.split():
        out.append(_w(t, x, y0, y1))
        x = out[-1].bbox.x1 + 0.004
    return out


def _header():
    return [_w("2024", 0.73, 0.120, 0.133), _w("2023", 0.86, 0.120, 0.133)]


def _read(words, known=frozenset()):
    items, _ = build_line_items(words, page_index=89, document_id=None, source_kind="native",
                                statement="cash_flow", on_face=True, page_scope="consolidated",
                                known_captions=known)
    return {li.source_label: {p: str(v.value) for p, v in
                              ((ev.period_label, ev) for ev in li.values.values())}
            for li in items}


def _byd_page():
    """BYD Electronic 2024, page index 89, as printed (page-fraction coordinates)."""
    return _header() + [
        *_latin("Losses on disposal of items of property, plant and equipment", 0.096, 0.3554,
                0.3685),
        _w("出售物業、廠房及設備項目的虧損", 0.096, 0.3703, 0.3853),
        _w("191,737", 0.707, 0.3713, 0.3846), _w("147,049", 0.840, 0.3714, 0.3846),
        *_latin("Depreciation of property, plant and equipment", 0.096, 0.3886, 0.4017),
        _w("物業、廠房及設備折舊", 0.096, 0.4035, 0.4185),
        _w("4,875,202", 0.694, 0.4046, 0.4179), _w("2,787,817", 0.828, 0.4047, 0.4178),
        *_latin("Amortisation of other intangible assets", 0.096, 0.4218, 0.4350),
        _w("其他無形資產攤銷", 0.350, 0.4207, 0.4356),
        _w("932,779", 0.707, 0.4217, 0.4350), _w("3,362", 0.857, 0.4218, 0.4350),
    ]


def test_an_english_line_over_its_chinese_translation_is_one_caption(ontology):
    """BYD: the English caption is one the rulebook knows, and is still not an empty line item
    when the line beneath it is its translation carrying the figures."""
    got = _read(_byd_page(), known_captions(ontology))
    caption = "Depreciation of property, plant and equipment 物業、廠房及設備折舊"
    assert caption in got, sorted(got)
    assert got[caption] == {"current": "4875202", "prior": "2787817"}
    assert "物業、廠房及設備折舊" not in got


def test_the_translated_caption_binds_the_cash_flow_depreciation(matcher):
    res = matcher.match("Depreciation of property, plant and equipment 物業、廠房及設備折舊",
                        statement="cash_flow", section=OPERATING)
    assert res.canonical_key == "cf_oper_indirect__depreciation"


def test_a_known_english_caption_over_an_english_row_is_still_its_own_line(ontology):
    """The exception is the translation only: a known caption with no figures, over the next
    English caption, stays a line of its own and is not glued onto it."""
    words = _header() + [
        *_latin("Depreciation of property, plant and equipment", 0.096, 0.3886, 0.4017),
        *_latin("Amortisation of other intangible assets", 0.096, 0.4035, 0.4166),
        _w("932,779", 0.707, 0.4035, 0.4166), _w("3,362", 0.857, 0.4035, 0.4166),
    ]
    got = _read(words, known_captions(ontology))
    assert "Amortisation of other intangible assets" in got, sorted(got)


def _kaming_page():
    """佳明 2025/26, page index 67: the Chinese, set taller, interleaves the English wrap."""
    return _header() + [
        *_latin("Net decrease in cash and cash", 0.095, 0.6532, 0.6668),
        _w("現金及現金等價物減少淨額", 0.410, 0.6438, 0.6756),
        *_latin("equivalents", 0.112, 0.6669, 0.6804),
        _w("(9,350)", 0.750, 0.6669, 0.6804), _w("(28,906)", 0.867, 0.6684, 0.6804),
        _w("外幣匯率變動對現金及", 0.410, 0.6847, 0.7165),
        *_latin("Effect of foreign exchange rate change", 0.095, 0.6941, 0.7076),
        _w("現金等價物的影響", 0.426, 0.6983, 0.7301),
        *_latin("on cash and cash equivalents", 0.112, 0.7077, 0.7212),
        _w("21", 0.777, 0.7077, 0.7212), _w("(32)", 0.895, 0.7077, 0.7212),
        _w("年初的現金及現金等價物", 0.410, 0.7255, 0.7573),
        *_latin("Cash and cash equivalents", 0.095, 0.7349, 0.7484),
        *_latin("at the beginning of the year", 0.112, 0.7486, 0.7621),
        _w("33,624", 0.746, 0.7486, 0.7621), _w("62,562", 0.871, 0.7501, 0.7621),
    ]


def test_an_interleaved_bilingual_wrap_is_one_caption():
    got = _read(_kaming_page())
    caption = ("Effect of foreign exchange rate change on cash and cash equivalents "
               "外幣匯率變動對現金及現金等價物的影響")
    assert caption in got, sorted(got)
    assert got[caption] == {"current": "21", "prior": "-32"}
    assert not any(c.startswith("on cash and cash equivalents") for c in got), sorted(got)
    # the neighbours keep their own captions
    assert "Net decrease in cash and cash equivalents 現金及現金等價物減少淨額" in got
    assert any(c.startswith("Cash and cash equivalents") and "beginning" in c for c in got)


def test_an_investing_row_on_a_continuation_page_stays_investing(shipped, ontology):
    """The same carry the other way: interest received printed in an investing section that runs
    on to a page with no banner is investing, although both lines now name 'Interest received'."""
    rows = [
        _row("Additions to investment properties 增添投資物業", "-1612223", "-2595241", 108,
             INVESTING),
        _row("Interest received 已收利息", "11", "12", 109, TITLE_TAIL),
        _row("Net cash flows from investing activities 投資活動所得現金流量淨額", "4044304",
             "-2604642", 109, TITLE_TAIL),
    ]
    _map(rows, _pages(), shipped, ontology)
    assert rows[1].canonical_key == INTEREST_INVEST, (rows[1].canonical_key,
                                                      rows[1].confidence.flags)
