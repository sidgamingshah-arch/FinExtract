"""A 其中 breakdown is inside the row above it — every line of it, not only the one marked 其中：.

THE DEFECT, measured on 河钢股份 000709 FY2024. The consolidated balance sheet (PDF p84) prints

    其他权益工具                        7,001,608,333.33
      其中：优先股
            永续债                     7,001,608,333.33
    资本公积                           21,990,801,392.04

其他权益工具 mapped to `bs_equity__other_equity`. 其中：优先股 printed no figure and never became a
row, so 永续债 arrived as a bare caption, matched no concept, and the residual sweep put it into
`bs_equity__other_reserves` — the same 7.0bn twice. The template's Equity & Reserves, whose rollup
holds both lines, published 29,744,807,996.29 where its own definition gives 22,743,199,662.96. The
same shape put 应收股利 into other current assets beside the 其他应收款 that contains it (230.8m
consolidated, 794.7m parent), 应付股利 23.3m into other current liabilities beside 其他应付款, and
688008's parent-company 应收股利 40,000,000.00 into its other current assets.

The residual already refused a breakdown BY ITS CAPTION (`_OF_WHICH`). The further lines of one
carry no marker, so the reconstruction now reads the group off the page geometry and records each
line's `parent_id`, and the sweep refuses those rows as well — except under a statement-spine line
(二、营业总成本), whose 其中 group IS the list of lines its section is made of.

THE SECOND DEFECT, from the same block: 归属于母公司所有者权益合计 ("equity attributable to owners
of the parent") was an alias of `bs_equity__equity_and_reserves`. The template's Equity & Reserves
EXCLUDES Permanent Equity — share capital, premium, capital and restricted reserves — and the
attributable total includes it, so the printed figure that line carried was a different quantity
from the one it computes: 11,403,438,067.08 printed against 4,346,118,953.99 on 688008, and on
000709 a structural failure reported against it. No output column is attributable equity
(Permanent Equity + Equity & Reserves), so the caption now maps to nothing and stays a printed row.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.row_reconstruct import Word, build_line_items

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


# ── reading the group off the page ────────────────────────────────────────────────────────────

def _page(lines, *, char: float, statement: str = "balance_sheet"):
    """``lines`` are ``(x0, caption, current)``; a None figure prints a label-only line.

    Each caption is ONE word, the way a run of CJK arrives from the PDF, ``char`` wide per
    character — so where the caption after 其中： starts is a property of the word, as on the page.
    """
    words: list[Word] = []
    for i, (x0, caption, cur) in enumerate(lines):
        y = 0.10 + i * 0.03
        words.append(Word(text=caption, bbox=BBox(x0=x0, y0=y, x1=x0 + char * len(caption),
                                                  y1=y + 0.012)))
        if cur is not None:
            words.append(Word(text=cur, bbox=BBox(x0=0.70, y0=y, x1=0.80, y1=y + 0.012)))
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                                statement=statement)
    return {i.source_label: i for i in items}


def test_the_further_line_of_a_breakdown_names_the_row_it_is_inside():
    """000709's layout, measured off its PDF (x0 as a fraction of page width): the parent at
    0.1105, 其中：优先股 one character in at 0.1256, 永续债 aligned to the caption after 其中： at
    0.1710, and 资本公积 back at the margin."""
    rows = _page([
        (0.1105, "股本", "10,337,121,092.00"),
        (0.1105, "其他权益工具", "7,001,608,333.33"),
        (0.1256, "其中：优先股", None),
        (0.1710, "永续债", "7,001,608,333.33"),
        (0.1105, "资本公积", "21,990,801,392.04"),
    ], char=0.0151)

    assert rows["永续债"].parent_id == rows["其他权益工具"].id
    assert rows["资本公积"].parent_id is None, "the line back at the margin has left the breakdown"
    assert rows["其他权益工具"].parent_id is None and rows["股本"].parent_id is None


def test_a_flush_set_breakdown_is_read_from_its_own_marker_not_from_its_parent():
    """688008 sets 其中： FLUSH with its parent and the 加： lines one character in, so "indented
    relative to the parent" would swallow 加：其他收益 into 营业总成本's breakdown. The edge that
    decides is the caption after 其中：, three characters in. Nested: 财务费用 is itself a line of
    营业总成本's breakdown and has its own."""
    rows = _page([
        (0.1510, "二、营业总成本", "2,345,174,927.11"),
        (0.1510, "其中：营业成本", "1,523,614,938.54"),
        (0.2038, "税金及附加", "6,325,049.35"),
        (0.2038, "财务费用", "240,504,105.54"),
        (0.2038, "其中：利息费用", "1,512,598.38"),
        (0.2568, "利息收入", "229,985,802.58"),
        (0.1685, "加：其他收益", "91,576,969.33"),
    ], char=0.0177, statement="profit_and_loss")

    total = rows["二、营业总成本"].id
    assert rows["其中：营业成本"].parent_id == total
    assert rows["税金及附加"].parent_id == total
    assert rows["财务费用"].parent_id == total
    assert rows["其中：利息费用"].parent_id == rows["财务费用"].id
    assert rows["利息收入"].parent_id == rows["财务费用"].id
    assert rows["加：其他收益"].parent_id is None


def test_a_breakdown_under_a_line_with_no_figure_has_nothing_to_be_inside():
    """其中： breaks down the row printed DIRECTLY above it. When that line printed no figure it
    produced no row, and the row two lines up is not its parent."""
    rows = _page([
        (0.1105, "股本", "867,038,792.00"),
        (0.1105, "其他权益工具", None),
        (0.1256, "其中：优先股", None),
        (0.1710, "永续债", "1.00"),
    ], char=0.0151)

    assert rows["永续债"].parent_id is None


def test_an_english_of_which_opens_no_group():
    """MAINLAND ONLY. An HKEX face's "of which" is English, whose character widths say nothing
    about where the caption after the marker starts — and the HKEX filings must not move."""
    rows = _page([
        (0.10, "Other receivables", "100"),
        (0.12, "of which: interest", "10"),
        (0.18, "dividends", "5"),
    ], char=0.008)

    assert all(r.parent_id is None for r in rows.values())


# ── the sweep ─────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def shipped():
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_template
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(
        (_SAMPLES / "output_csv_hk_line_items.json").read_text(encoding="utf-8")), resolve=True)
    return (load_template(json.loads(
        (_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))),
        build_working_view(cfg))


def _row(label: str, value: str, *, key: str | None = None, ordinal: int,
         role: LineRole = LineRole.LINE, parent: LineItem | None = None) -> LineItem:
    item = LineItem(source_label=label, canonical_key=key, ordinal=ordinal, role=role,
                    section_hint="所有者权益：", parent_id=parent.id if parent else None)
    item.set_value(ExtractedValue(
        value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    if key:
        item.confidence.method = "exact"
    return item


def _swept(shipped, rows):
    from app.core.stage import PipelineContext
    from app.stages.residual import ResidualStage

    template, ontology = shipped
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="balance_sheet")]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = template
    ctx.ontology = ontology
    ctx.mapping_strategy = "deterministic"
    ResidualStage().run(doc, ctx)
    return {li.source_label: li for li in doc.line_items}


def _equity_block():
    """000709's consolidated equity block as reconstruction now hands it to the sweep."""
    other_equity = _row("其他权益工具", "7001608333.33", key="bs_equity__other_equity", ordinal=1)
    return [
        _row("股本", "10337121092.00", key="bs_equity__common_share_capital", ordinal=0),
        other_equity,
        _row("永续债", "7001608333.33", ordinal=2, parent=other_equity),
        _row("资本公积", "21990801392.04", key="bs_equity__capital_and_restricted_reserves",
             ordinal=3),
        _row("其他综合收益", "-274740612.01", key="bs_equity__accum_oth_eqty_rsrv_inc", ordinal=4),
        _row("专项储备", "225231245.48", ordinal=5),
        _row("盈余公积", "3156845103.06", key="bs_equity__capital_and_restricted_reserves",
             ordinal=6),
        _row("未分配利润", "15791100696.16", key="bs_equity__retained_profits", ordinal=7),
        _row("归属于母公司所有者权益合计", "58227967250.06", ordinal=8, role=LineRole.TOTAL),
        _row("少数股东权益", "9354410667.42", key="bs_equity__minority_interest_equity", ordinal=9),
        _row("所有者权益合计", "67582377917.48", key="bs_equity__total_equity_and_reserves",
             ordinal=10, role=LineRole.TOTAL),
    ]


def test_the_perpetual_bonds_are_not_swept_beside_the_line_that_contains_them(shipped):
    rows = _swept(shipped, _equity_block())

    assert rows["永续债"].canonical_key is None
    assert "residual_ineligible:of which breakdown" in rows["永续债"].confidence.flags
    # The sweep itself still runs: the special reserve is a line of its own and is Other Reserves.
    assert rows["专项储备"].canonical_key == "bs_equity__other_reserves"


def test_without_the_parent_the_same_row_is_swept(shipped):
    """The counterweight, and what every run did before: a bare 永续债 is an unclaimed row of the
    equity section like any other."""
    block = _equity_block()
    block[2].parent_id = None
    rows = _swept(shipped, block)

    assert rows["永续债"].canonical_key == "bs_equity__other_reserves"


def test_a_breakdown_of_a_statement_spine_line_is_the_section_itself():
    """二、营业总成本's 其中 group is 营业成本, 税金及附加 … 财务费用 — the lines the section is made of,
    under a TOTAL no residual holds. Refusing them would empty the section, not stop a double
    count. A breakdown of a line, or of a total printed after its components, is refused."""
    from app.stages.residual import _broken_down_rows

    spine = LineItem(source_label="二、营业总成本", role=LineRole.TOTAL)
    line = LineItem(source_label="其他应收款")
    total = LineItem(source_label="营业外收入合计", role=LineRole.TOTAL)
    under_spine = LineItem(source_label="税金及附加", parent_id=spine.id)
    under_line = LineItem(source_label="应收股利", parent_id=line.id)
    under_total = LineItem(source_label="其他", parent_id=total.id)

    out = _broken_down_rows([spine, line, total, under_spine, under_line, under_total])
    assert out == {under_line.id, under_total.id}


# ── the attributable total is not Equity & Reserves ───────────────────────────────────────────

def test_equity_and_reserves_carries_no_caption_that_includes_share_capital():
    """By the template's own rollup, Equity & Reserves excludes Permanent Equity and minority
    interests. A caption for equity attributable to owners INCLUDES share capital, so as an alias
    it filed a different quantity as this line's printed figure."""
    raw = json.loads((_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
    node = next(c for st in raw["statements"] for sec in st["sections"]
                for c in [sec, *(sec.get("children") or [])]
                if c.get("canonical_key") == "bs_equity__equity_and_reserves")
    children = set(node["rollup"]["children"])
    assert "bs_equity__permanent_equity" not in children
    assert "bs_equity__common_share_capital" not in children

    seed = json.loads((_SAMPLES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    item = next(i for i in seed["items"] if i["key"] == "bs_equity__equity_and_reserves")
    aliases = list(item.get("aliases") or []) + [
        a for v in (item.get("aliases_i18n") or {}).values() for a in v]
    assert not [a for a in aliases if "归属于" in a or "歸屬於" in a or "ttributable" in a], aliases


def test_the_attributable_total_maps_to_no_line(shipped):
    """…and with the alias gone it lands nowhere else either: not on Total Equity & Reserves,
    whose caption 所有者权益合计 it ends with, and not on any equity line."""
    from app.config import get_settings
    from app.services.mapping import OntologyMatcher

    _template, ontology = shipped
    settings = get_settings()
    matcher = OntologyMatcher(ontology, locale="zh", settings=settings)
    for caption in ("归属于母公司所有者权益合计", "归属于母公司所有者权益（或股东权益）合计",
                    "归属于母公司股东权益合计"):
        res = matcher.match(caption, statement="balance_sheet", section="所有者权益：")
        assert res.canonical_key is None, (caption, res.canonical_key)
    # The total it ends with still maps where it did.
    assert matcher.match("所有者权益合计", statement="balance_sheet",
                         section="所有者权益：").canonical_key == \
        "bs_equity__total_equity_and_reserves"


def test_equity_and_reserves_is_what_000709_prints_less_its_permanent_equity():
    """THE ARITHMETIC, on the figures 000709 prints, by the template's own rollups: with 永续债
    counted once, Equity & Reserves is 22,743,199,662.96, and Permanent Equity plus it is exactly
    the attributable total the filing prints, 58,227,967,250.06."""
    from app.services import rollups

    tdef = json.loads((_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
    printed = {
        "bs_equity__common_share_capital": Decimal("10337121092.00"),
        "bs_equity__other_equity": Decimal("7001608333.33"),
        "bs_equity__capital_and_restricted_reserves": (Decimal("21990801392.04")
                                                       + Decimal("3156845103.06")),
        "bs_equity__accum_oth_eqty_rsrv_inc": Decimal("-274740612.01"),
        "bs_equity__other_reserves": Decimal("225231245.48"),
        "bs_equity__retained_profits": Decimal("15791100696.16"),
        "bs_equity__minority_interest_equity": Decimal("9354410667.42"),
    }
    calc = rollups.evaluate(tdef, lambda k: printed.get(k))

    reserves = calc["bs_equity__equity_and_reserves"]
    permanent = calc["bs_equity__permanent_equity"]
    assert reserves.computable and reserves.value == Decimal("22743199662.96")
    assert permanent.value + reserves.value == Decimal("58227967250.06")
