"""A cash flow row printed in the statement's own columns reaches its section, however long it is.

Measured answer-free at c2e2daf (HK$'000, current / prior, printed in brackets):

* Hon Kwok 2023/24 — Net cash flows from operating activities -25,663 / 123,722 (86,994 /
  163,638), end cash 1,176,707 / 1,682,714 (1,270,169 / 1,747,149). 'Decrease in properties held
  for sale under development and completed properties held for sale' 112,169 (14 words) and 'Fair
  value losses on financial assets at fair value through profit or loss' 488 (13 words) were
  refused by the residual as narrative for their LENGTH; so was the investing row 'Decrease/
  (increase) in non-pledged time deposits with original maturity of more than three months when
  acquired' (-19,195). 'Purchases of items of property, plant and equipment' bound nothing.
* Far East Horizon 2024 — CFO 10,271,969 / 3,356,718 (9,965,983 / 3,726,527), end cash 26,249,082
  / 19,385,630 (19,786,521 / 18,852,540): three fair-value rows and 'Decrease in restricted cash
  related to asset-backed securitisations, collective fund trusts and litigation' refused the same
  way, and three investing rows with them — 'Purchase of items of property, plant and equipment,
  intangible assets and other long-term assets' (8,411,868) among them.
* Lai Sun 2024/25 — CFO 142,127 / -1,029,152 (292,696 / -758,358): three fair-value rows refused,
  and 'CASH FLOWS FROM OPERATING ACTIVITIES (continued)' was not read as the banner it repeats, so
  'Interest received' 150,273, printed under "Cash generated from operations", bound the investing
  concept (CFI 261,479 against 286,808).
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind, PrintedIn
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.row_reconstruct import Word, _is_banner_line, build_line_items
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"

OPER = "CASH FLOWS FROM OPERATING ACTIVITIES"
INVEST = "CASH FLOWS FROM INVESTING ACTIVITIES"
OTHER_NON_CASH = "cf_oper_indirect__other_non_cash_adjs_oper"
OTHER_INVESTING = "cf_investing__other_invest_cash_flows"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped))


def _row(caption, current=None, prior=None, section=OPER, role=LineRole.LINE, cols=None):
    row = LineItem(source_label=caption, role=role, section_hint=section,
                   printed_in=PrintedIn.FACE)
    for period, value in (cols or (("current", current), ("prior", prior))):
        if value is not None:
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                         value=Decimal(value), value_raw=Decimal(value),
                                         provenance=Provenance(page_index=0)))
    return row


def _run(rows, shipped):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="cash_flow")]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    return doc


def _flags(row):
    return (row.canonical_key, row.confidence.flags)


# Hon Kwok 2023/24's operating section, as printed (HK$'000).
def _hon_kwok_operating():
    return [
        _row("Profit before tax", "72241", "299195"),
        _row("Finance costs", "312523", "217328"),
        _row("Fair value losses on financial assets at fair value through profit or loss",
             "488", "3013"),
        _row("Fair value losses/(gains) on investment properties, net", "141123", "-42957"),
        _row("Decrease in properties held for sale under development and completed properties "
             "held for sale", "112169", "36903"),
        _row("Decrease in trade receivables", "3404", "3435"),
        _row("Net cash flows from operating activities", "86994", "163638", role=LineRole.TOTAL),
    ]


def test_a_long_row_in_the_statement_columns_is_swept_into_its_section(shipped):
    rows = _hon_kwok_operating()
    _run(rows, shipped)
    for row in (rows[2], rows[4]):
        assert row.canonical_key == OTHER_NON_CASH, _flags(row)
        assert not any("narrative row" in f for f in row.confidence.flags), _flags(row)


def test_a_long_row_with_no_section_of_its_own_takes_its_neighbours(shipped):
    """Far East Horizon: the restricted-cash row is printed between two working-capital rows on a
    page that repeats no banner. Eligible, it is placed as its neighbours are."""
    rows = [
        _row("Profit before tax", "8021363", "10425260"),
        _row("Increase in prepayments, other receivables and other assets", "-584157", "-408380",
             section=None),
        _row("Decrease in restricted cash related to asset-backed securitisations, collective "
             "fund trusts and litigation", "261318", "730256", section=None),
        _row("Decrease in trade and bills payables", "-1645275", "-4377357", section=None),
        _row("Net cash flows from operating activities", "9965983", "3726527",
             role=LineRole.TOTAL, section=None),
    ]
    _run(rows, shipped)
    assert rows[2].canonical_key == OTHER_NON_CASH, _flags(rows[2])


@pytest.mark.parametrize("row", [
    # A sentence: a finite verb in a long span.
    lambda: _row("The comparative amounts have been reclassified to conform with the current "
                 "year's presentation of the statement", "1200", "900"),
    # A figure the reader could only key positionally: no period heading is above it.
    lambda: _row("Rupen K. Bhatt Milind Sarwate R J Jeyamurugan Partner Chairman of Audit "
                 "Committee CFO", cols=(("current_col0", "46930"), ("current_col1", "109854"))),
    # The phrase list applies to every row, short or long.
    lambda: _row("Other adjustments as follows", "12", "10"),
])
def test_prose_is_still_refused_as_narrative(shipped, row):
    rows = _hon_kwok_operating()
    prose = row()
    rows.insert(3, prose)
    _run(rows, shipped)
    assert any(f == "residual_ineligible:narrative row" for f in prose.confidence.flags), \
        _flags(prose)


def test_a_long_note_row_keeps_the_length_rule(shipped):
    """Only a FACE row's columns are the statement's; a note row's length still counts."""
    rows = _hon_kwok_operating()
    note = rows[4]
    note.printed_in = PrintedIn.NOTES
    from app.stages.residual import _is_narrative_row
    assert _is_narrative_row(note)
    assert not _is_narrative_row(rows[2])


def test_a_long_han_run_is_still_captions_merged():
    """688008: three investing captions on one row. The Han budget stands for a face row too."""
    from app.stages.residual import _is_narrative_row
    merged = _row("收回的现金净额处置子公司及其他营业单位收到的现金净额收到其他与投资活动有关的现金",
                  prior="11306000.00", section="投资活动产生的现金流量")
    assert _is_narrative_row(merged)


# ── the banner repeated over a continuation page ──────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "CASH FLOWS FROM OPERATING ACTIVITIES (continued)",
    "CASH FLOWS FROM INVESTING ACTIVITIES (continued)",
    "Current assets (cont'd)",
    "流動資產（續）",
])
def test_a_continued_banner_is_a_banner(text):
    assert _is_banner_line(text, ())


@pytest.mark.parametrize("text", [
    "Consolidated statement of changes in equity (continued)",
    "Notes to the financial statements (continued)",
    "Gain on lease modification",
    "CASH FLOWS FROM OPERATING ACTIVITIES",   # unchanged: not exhausted by the vocabulary
])
def test_other_lines_are_not(text):
    assert not _is_banner_line(text, ())


LINE_H = 0.011
PITCH = 0.022


def _w(text, x0, y0, x1):
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y0 + LINE_H))


def _lai_sun_page_192():
    """Lai Sun 2024/25 p.192 as printed: the column heads, the repeated banner tight over the
    first row, and the operating tail with 'Interest received' under 'Cash generated'."""
    words = [_w("2025", 0.70, 0.08, 0.74), _w("2024", 0.84, 0.08, 0.88),
             _w("HK$’000", 0.68, 0.095, 0.74), _w("HK$’000", 0.82, 0.095, 0.88),
             _w("CASH FLOWS FROM OPERATING ACTIVITIES (continued)", 0.10, 0.12, 0.50)]
    rows = [("Gain on lease modification", "(75,978)", "(27,652)"),
            ("Foreign exchange differences, net", "24,163", "45,931"),
            ("Cash generated from operations", "1,699,439", "922,014"),
            ("Interest received", "150,273", "195,375"),
            ("Interest paid on bank borrowings", "(1,197,567)", "(1,517,924)")]
    y = 0.12 + LINE_H + 0.002          # tight under the banner, as row clustering merges them
    for caption, cur, pri in rows:
        words += [_w(caption, 0.10, y, 0.10 + 0.008 * len(caption)),
                  _w(cur, 0.68, y, 0.74), _w(pri, 0.82, y, 0.88)]
        y += PITCH
    return words


def test_the_rows_under_a_continued_banner_carry_its_section():
    items, _ = build_line_items(_lai_sun_page_192(), page_index=192, document_id="d",
                                source_kind="native", statement="cash_flow")
    by = {li.source_label: li for li in items}
    assert "Gain on lease modification" in by, sorted(by)
    for caption in ("Gain on lease modification", "Interest received"):
        assert (by[caption].section_hint or "").startswith(OPER), (caption,
                                                                    by[caption].section_hint)


def test_interest_received_under_the_operating_banner_is_not_the_investing_line(matcher):
    res = matcher.match("Interest received", statement="cash_flow",
                        section=f"{OPER} (continued)")
    assert getattr(res, "canonical_key", None) != "cf_investing__interest_income_rcvd_invest"


# ── the investing rows the definitions name ───────────────────────────────────────────────────

@pytest.mark.parametrize("caption,key", [
    ("Purchases of items of property, plant and equipment", "cf_investing__purchase_ppe"),
    ("Purchase of items of property, plant and equipment, intangible assets and other long-term "
     "assets", "cf_investing__purchase_ppe"),
    ("Proceeds from disposal of property, plant and equipment, intangible assets and other "
     "long-term assets", "cf_investing__proceeds_sale_ppe"),
    ("Purchase of shareholdings for joint ventures",
     "cf_investing__investment_assoc_and_affiliates"),
    ("Purchase of shareholdings for associates", "cf_investing__investment_assoc_and_affiliates"),
    ("Purchase of a shareholding in a joint venture",
     "cf_investing__investment_assoc_and_affiliates"),
    ("Advances of loans to a joint venture", "cf_investing__investment_assoc_and_affiliates"),
])
def test_investing_rows_bind_the_line_their_definition_names(matcher, caption, key):
    res = matcher.match(caption, statement="cash_flow", section=INVEST)
    assert getattr(res, "canonical_key", None) == key, res


def test_the_aliases_are_mirrored_in_the_ontology():
    seed = {it["key"]: it for it in json.loads(SEED.read_text(encoding="utf-8"))["items"]}
    onto = {m["canonical_key"]: m
            for m in json.loads(ONTOLOGY.read_text(encoding="utf-8"))["mappings"]}
    for key in ("cf_investing__purchase_ppe", "cf_investing__proceeds_sale_ppe",
                "cf_investing__investment_assoc_and_affiliates"):
        assert seed[key]["aliases"] == onto[key]["aliases"], key
        assert seed[key]["aliases_i18n"]["en"] == onto[key]["aliases_i18n"]["en"], key


def test_a_long_investing_row_binds_or_sweeps_inside_investing(shipped):
    rows = [
        _row("Purchase of items of property, plant and equipment, intangible assets and other "
             "long-term assets", "-8411868", "-2558961", section=INVEST),
        _row("Realized gains on derecognition of financial assets at fair value through profit "
             "or loss", "331429", "412295", section=INVEST),
        _row("Net cash flows used in investing activities", "-3535085", "-2397246",
             section=INVEST, role=LineRole.TOTAL),
    ]
    _run(rows, shipped)
    assert rows[0].canonical_key == "cf_investing__purchase_ppe", _flags(rows[0])
    assert rows[1].canonical_key == OTHER_INVESTING, _flags(rows[1])


# ── below the closing cash ────────────────────────────────────────────────────────────────────

def test_the_reconciliation_under_an_unbound_closing_cash_stays_out(shipped):
    """China SCE 2024: 'AT END OF YEAR' binds nothing, so no statement total closed the cash flow
    statement; with the length rule gone the reconciliation printed beneath it — 'Less:
    Non-pledged time deposits with original maturity over three months when acquired' — was swept
    into Other financing cash flows (-952,500 / -962,500). The closing caption closes it."""
    fin = "CASH FLOWS FROM FINANCING ACTIVITIES"
    rows = [
        _row("New bank and other borrowings", "942574", "11142125", section=fin),
        _row("Repayment of bank and other borrowings", "-7732473", "-16077298", section=fin),
        _row("Net cash flows used in financing activities", "-6789899", "-4935173", section=fin,
             role=LineRole.TOTAL),
        _row("Cash and cash equivalents at beginning of year", "8156453", "15677587", section=fin),
        _row("AT END OF YEAR", "3932025", "8156453", section=None),
        _row("Less: Non-pledged time deposits with original maturity over three months when "
             "acquired", "-952500", "-962500", section=None),
    ]
    _run(rows, shipped)
    assert rows[5].canonical_key != "cf_financing__other_financing_cash_flows", _flags(rows[5])
    assert any(f.startswith("residual_ineligible:") for f in rows[5].confidence.flags), \
        _flags(rows[5])


# ── a bilingual caption whose English half the vocabulary knows ───────────────────────────────

def test_a_known_english_caption_keeps_its_chinese_line_and_its_figures(shipped):
    """BYD Electronic 2024 p.90 prints the English caption on one line and the Chinese beneath it,
    beside the figures. A complete English caption was let go as a row of its own, so the figures
    kept only 購買物業、廠房及設備項目 — and once 'Purchases of items of property, plant and
    equipment' became an alias, that is what happened to it."""
    from app.services.mapping import known_captions
    known = known_captions(build_working_view(shipped))
    words = [_w("2024", 0.70, 0.08, 0.74), _w("2023", 0.84, 0.08, 0.88),
             _w("CASH FLOWS FROM INVESTING ACTIVITIES", 0.10, 0.12, 0.45),
             _w("Purchases of items of property, plant and equipment", 0.10, 0.15, 0.45),
             _w("購買物業、廠房及設備項目", 0.10, 0.162, 0.30),
             _w("(2,848,294)", 0.66, 0.162, 0.74), _w("(3,851,371)", 0.80, 0.162, 0.88),
             _w("Purchases of other intangible assets", 0.10, 0.19, 0.40),
             _w("購買其他無形資產", 0.10, 0.202, 0.25),
             _w("(1,000)", 0.68, 0.202, 0.74), _w("(2,000)", 0.82, 0.202, 0.88)]
    items, _ = build_line_items(words, page_index=90, document_id="d", source_kind="native",
                                statement="cash_flow", known_captions=known)
    labels = [li.source_label for li in items if li.values]
    assert "Purchases of items of property, plant and equipment 購買物業、廠房及設備項目" in labels, \
        labels
