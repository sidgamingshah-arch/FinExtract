"""THE HKFRS EQUITY BLOCK PUBLISHES WHAT THE FILING PRINTS — three defects that interacted.

NEW FILE -> backend/tests/test_hk_equity_block.py

Measured on the two HKEX reference filings (RMB'000 / HK$'000):

* RESERVES COUNTED TWICE. A Hong Kong statement of financial position prints one undifferentiated
  "Reserves 儲備" line and breaks it down in the statement of changes in equity, whose closing
  balances the filing itself says "comprise the consolidated reserves of RMB9,358,611,000". The
  face line bound to `bs_equity__other_reserves` (alias 储备) BESIDE those components, so every
  reserve was counted twice: China SCE 1966 published Equity & Reserves 42,335,984 and 嘉民
  4,529,714, against 12,605,743 and 2,191,372 by the template's own definition. The face line is
  now its own part, `sub__equity_reserves`, and `global_rules.mutually_exclusive_groups` keeps it
  apart from its components — the rule the legacy hkfrs rulebook declared and the shipped set did
  not.

* A ROLLUP CYCLE. Other Reserves (the section residual) deducted Retained Profits and Retained
  Profits deducted Other Reserves, so `rollups.evaluate` marked both cyclic and ran neither — and
  Total Equity & Reserves reached Revaluation, Hedging and Other Reserves only THROUGH that
  never-run Retained Profits residual, so published total equity was 148,757 short on 1966 and
  15,056 short on 嘉民. Total Equity & Reserves now names them itself (the extraction logic's
  "C9 + C22 + C23": permanent equity, Equity & Reserves' components, minority interest), and
  Retained Profits deducts none of them.

* NET ASSETS SWEPT INTO A RESIDUAL. "Net assets 資產淨值" is the same fact as total equity
  (20,482,326 = 20,482,326; 2,376,067 = 2,376,067). Nothing claimed it, so the sweep filed it on
  `bs_equity__other_reserves` (1966) and `bs_ncl__other_non_current_liabilities` (嘉民, 2,386,207
  published for a printed 10,140). Both residuals now refuse it, as they already refuse the
  layout's other subtotals.

Total Equity & Reserves reaching Other Reserves directly also reaches what a CAS filing had swept
into it: 河钢 000709's "其中：永续债" row, the breakdown of 其他权益工具 that Other Equity already holds.
Other Reserves refuses that breakdown, or 000709's published total equity would have gone from
225,231,245.48 short (its 专项储备, which the old rollup missed) to 7,001,608,333.33 over.

And the checks that judge the section had to read the figure the grid publishes: an equity-matrix
closing balance RESTATING a line the face prints is not a second contribution (`periods.summable`
drops it), and Retained Profits is a member of its section, not a subtotal over it.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services import rollups
from app.services.equity_matrix import TRANSPOSED_FLAG
from app.services.line_item_config import load_shipped_set
from app.services.mapping import OntologyMatcher
from app.services.structural_checks import collect_values, section_relations
from app.schemas.loader import load_template
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import _Residual, _by_canonical_key, _vetoed_by_never_sweep

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
HK_TEMPLATE = TEMPLATES / "output_csv_hk_v1_template.json"
SHIPPED_TEMPLATES = (HK_TEMPLATE, TEMPLATES / "output_csv_indas_v1_template.json")

PERMANENT = "bs_equity__permanent_equity"
RETAINED = "bs_equity__retained_profits"
OTHER = "bs_equity__other_reserves"
EQUITY_AND_RESERVES = "bs_equity__equity_and_reserves"
TOTAL_EQUITY = "bs_equity__total_equity_and_reserves"
MINORITY = "bs_equity__minority_interest_equity"
RESERVES_PART = "sub__equity_reserves"


def _template(path: Path = HK_TEMPLATE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _nodes(template: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    def walk(node: dict) -> None:
        out[node["canonical_key"]] = node
        for child in node.get("children") or []:
            walk(child)

    for statement in template["statements"]:
        for section in statement["sections"]:
            walk(section)
    return out


@pytest.fixture(scope="module")
def shipped():
    return load_shipped_set()


@pytest.fixture(scope="module")
def view(shipped):
    return build_working_view(shipped)


# ── the template ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", SHIPPED_TEMPLATES, ids=lambda p: p.name)
def test_no_shipped_template_declares_a_rollup_cycle(path):
    """A cycle is not an error anywhere: `rollups.evaluate` marks the lines cyclic and publishes
    their printed figures, so the subtraction both declarations describe silently never runs."""
    _order, cyclic = rollups._order(rollups.calculated_nodes(_template(path)))
    assert not cyclic, sorted(cyclic)


@pytest.mark.parametrize("path", SHIPPED_TEMPLATES, ids=lambda p: p.name)
def test_total_equity_is_permanent_equity_plus_equity_and_reserves_plus_minority(path):
    """C9 + C22 + C23, written out leaf by leaf so neither subtotal's printed figure is a term."""
    nodes = _nodes(_template(path))
    total = nodes[TOTAL_EQUITY]["rollup"]["children"]
    reserves = nodes[EQUITY_AND_RESERVES]["rollup"]["children"]
    assert len(total) == len(set(total))
    assert set(total) == {PERMANENT, *reserves, MINORITY}


@pytest.mark.parametrize("path", SHIPPED_TEMPLATES, ids=lambda p: p.name)
def test_retained_profits_is_a_reserve_beside_the_others_not_their_subtotal(path):
    nodes = _nodes(_template(path))
    retained = nodes[RETAINED]
    assert retained["role"] == "line"
    deducted = set(retained["rollup"]["children"])
    assert not deducted & {"bs_equity__revaluation_reserves", "bs_equity__hedging_reserves", OTHER}


def _rows(figures: dict[str, list[float]]) -> list[dict]:
    return [{"canonical_key": key, "source_label": f"{key} {i}",
             "values": [{"basis": "consolidated", "period_label": "current", "value": str(v),
                         "provenance": {"source_kind": "native", "page_index": 10 + i}}]}
            for key, values in figures.items() for i, v in enumerate(values)]


# The closing balances of each filing's statement of changes in equity, as filed on their keys,
# and the totals the filing prints. China SCE 1966 p107 / p105; 嘉民 p65 / p64.
CHINA_SCE_1966 = {
    "bs_equity__common_share_capital": [365_138],
    "bs_equity__capital_and_restricted_reserves": [-5_130_954, 1_883_822],
    "bs_equity__other_equity": [30],
    "bs_equity__revaluation_reserves": [82_872],
    OTHER: [18_026, 92_670],
    "bs_equity__hedging_reserves": [-44_811],
    "bs_equity__forex_translation_equity": [-1_060_819],
    RETAINED: [13_517_775],
    MINORITY: [10_758_577],
    TOTAL_EQUITY: [20_482_326],
}
KAMING = {
    "bs_equity__common_share_capital": [14_202],
    "bs_equity__share_premium": [95_045],
    OTHER: [23_523],
    "bs_equity__hedging_reserves": [-8_467],
    "bs_equity__capital_and_restricted_reserves": [75_448],
    "bs_equity__forex_translation_equity": [-6_283],
    RETAINED: [2_182_599],
    TOTAL_EQUITY: [2_376_067],
}


@pytest.mark.parametrize("figures, total, equity_and_reserves, other", [
    (CHINA_SCE_1966, 20_482_326, 12_605_743, 110_696),
    (KAMING, 2_376_067, 2_191_372, 23_523),
], ids=["china_sce_1966", "kaming"])
def test_the_published_equity_lines_are_the_printed_ones(figures, total, equity_and_reserves,
                                                         other):
    shown = rollups.figures_as_shown(_template(), _rows(figures), "consolidated", "current")
    assert shown[TOTAL_EQUITY] == total            # was 20,333,569 and 2,361,011
    assert shown[EQUITY_AND_RESERVES] == equity_and_reserves
    assert shown[OTHER] == other
    assert shown[RETAINED] == figures[RETAINED][0]


# ── the line-item set ───────────────────────────────────────────────────────────────────────────

def test_the_face_reserves_line_binds_to_its_own_part(view):
    matcher = OntologyMatcher(view, llm_provider=None)

    def key(caption: str) -> str | None:
        return matcher.match(caption, statement="balance_sheet",
                             section="EQUITY 權益").canonical_key

    assert key("Reserves 儲備") == RESERVES_PART
    assert key("储备") == RESERVES_PART
    assert key("Other reserves") == OTHER
    assert key("专项储备") == OTHER            # 000709's special reserve keeps its home


def test_the_reserves_part_is_a_part_and_reaches_other_reserves(shipped):
    part = next(i for i in shipped.items if i.key == RESERVES_PART)
    assert part.namespace == "internal" and part.in_output is False
    assert part.parent == OTHER
    assert str(getattr(part.statement, "value", part.statement)) == "balance_sheet"
    assert part.section_scope == ["bs_equity"]
    assert not part.keyword_hints, "a keyword 'reserves' would claim every reserve caption"


def test_the_reserves_aggregate_is_declared_apart_from_its_components(shipped):
    groups = {g.id: g for g in shipped.global_rules.mutually_exclusive_groups}
    group = groups["equity_reserves"]
    keys = {i.key for i in shipped.items}
    assert group.aggregate == RESERVES_PART
    assert set(group.components) <= keys
    assert {RETAINED, OTHER, "bs_equity__capital_and_restricted_reserves"} <= set(group.components)
    # Share capital and minority interest sit beside "Reserves" on the face, not inside it.
    assert not {"bs_equity__common_share_capital", MINORITY} & set(group.components)


def _li(key: str | None, label: str, amount: float, *, restated: bool = False) -> LineItem:
    li = LineItem(source_label=label, canonical_key=key)
    ev = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                        value=Decimal(str(amount)), value_raw=Decimal(str(amount)))
    if restated:
        ev.confidence.flags.append(TRANSPOSED_FLAG)
    li.set_value(ev)
    return li


def test_the_reserves_aggregate_is_unfiled_when_its_components_are_printed(view):
    aggregate = _li(RESERVES_PART, "Reserves 儲備", 2_361_865)
    components = [_li(k, k, v[0], restated=True) for k, v in KAMING.items()
                  if k not in ("bs_equity__common_share_capital", TOTAL_EQUITY)]
    doc = DocumentModel(filename="f.pdf", line_items=[aggregate, *components])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = _template()

    assert MapOntologyStage._enforce_containment(doc, view, ctx) == 1
    assert aggregate.canonical_key is None
    assert f"unfiled_aggregate:{RESERVES_PART}" in aggregate.confidence.flags
    # The components account for it exactly, so the containment is confirmed, not merely declared.
    assert not any(f.startswith("containment_unexplained") for f in aggregate.confidence.flags)
    assert all(c.canonical_key for c in components)


def test_a_lone_reserves_line_stays_filed(view):
    """`no_fabricated_split`: a face printing only Share capital and Reserves keeps the aggregate,
    and its parent carries it to Other Reserves."""
    aggregate = _li(RESERVES_PART, "Reserves 儲備", 2_361_865)
    capital = _li("bs_equity__common_share_capital", "Share capital 股本", 14_202)
    doc = DocumentModel(filename="f.pdf", line_items=[capital, aggregate])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = _template()

    assert MapOntologyStage._enforce_containment(doc, view, ctx) == 0
    assert aggregate.canonical_key == RESERVES_PART


@pytest.mark.parametrize("residual", [OTHER, "bs_ncl__other_non_current_liabilities"])
@pytest.mark.parametrize("caption", ["Net assets 資產淨值", "NET ASSETS 資產淨值", "資產淨值",
                                     "Net (liabilities)/assets"])
def test_net_assets_is_never_swept_into_a_residual(shipped, residual, caption):
    hints = next(i for i in shipped.items if i.key == residual).exclude_hints
    bucket = _Residual(residual, "section", None, None, exclude_patterns=tuple(hints))
    assert _vetoed_by_never_sweep(bucket, caption)


@pytest.mark.parametrize("residual", ["bs_cl__other_current_liabilities",
                                      "bs_ncl__other_non_current_liabilities"])
@pytest.mark.parametrize("caption", ["Total assets less current liabilities 總資產減流動負債",
                                     "NET CURRENT ASSETS/(LIABILITIES) 流動資產╱（負債）淨額",
                                     "Net current liabilities 流動淨負債"])
def test_the_net_asset_layouts_subtotals_are_never_swept(shipped, residual, caption):
    """The same layout prints these between the current and non-current liabilities, and which of
    the two residuals a row reaches depends on where its banner resolved. 嘉民's "Total assets less
    current liabilities 3,078,784" reached the CURRENT one, which had no hint for it, and published
    3,092,261 for a printed 13,477 — total current liabilities 8,972,667 for a printed 5,893,883."""
    hints = next(i for i in shipped.items if i.key == residual).exclude_hints
    bucket = _Residual(residual, "section", None, None, exclude_patterns=tuple(hints))
    assert _vetoed_by_never_sweep(bucket, caption)


@pytest.mark.parametrize("caption", ["永续债", "其中：优先股", "永續債"])
def test_a_cas_other_equity_instruments_breakdown_is_never_swept_into_other_reserves(shipped,
                                                                                    caption):
    """CAS prints 其他权益工具 with an "其中：优先股 / 永续债" breakdown beneath it. On 河钢 000709 the
    永续债 row (7,001,608,333.33, the whole of 其他权益工具 7,001,608,333.33) was swept into Other
    Reserves, so the same money was in Other Equity and Other Reserves. While Total Equity &
    Reserves did not reach Other Reserves that double count stayed inside Equity & Reserves; once
    it does, it would have taken 000709's total equity 7,001,608,333.33 over the printed
    67,582,377,917.48."""
    hints = next(i for i in shipped.items if i.key == OTHER).exclude_hints
    bucket = _Residual(OTHER, "section", None, None, exclude_patterns=tuple(hints))
    assert _vetoed_by_never_sweep(bucket, caption)


def test_the_residuals_still_take_their_own_rows(shipped):
    hints = next(i for i in shipped.items if i.key == OTHER).exclude_hints
    bucket = _Residual(OTHER, "section", None, None, exclude_patterns=tuple(hints))
    assert not _vetoed_by_never_sweep(bucket, "Share option reserve")
    assert not _vetoed_by_never_sweep(bucket, "专项储备")
    assert not _vetoed_by_never_sweep(bucket, "Perpetual capital securities")


# ── the checks read the published figure ───────────────────────────────────────────────────────

def test_a_restated_balance_is_not_counted_twice_by_the_checks():
    face = _li("bs_equity__common_share_capital", "Share capital 股本", 14_202)
    matrix = _li("bs_equity__common_share_capital", "Share capital", 14_202, restated=True)
    alone = _li("bs_equity__share_premium", "Share premium", 95_045, restated=True)
    slot = ("consolidated", "current")

    values = collect_values([face, matrix, alone])
    assert values.get("bs_equity__common_share_capital", slot) == Decimal(14_202)
    assert values.get("bs_equity__share_premium", slot) == Decimal(95_045)

    totals, _rows_by_key = _by_canonical_key(DocumentModel(filename="f.pdf",
                                                           line_items=[face, matrix, alone]))
    assert set(totals["bs_equity__common_share_capital"].values()) == {14_202.0}
    assert set(totals["bs_equity__share_premium"].values()) == {95_045.0}


def test_retained_profits_is_a_member_of_the_equity_reconciliation(view):
    """Both readers of the equity section's identity — the residual stage's and the structural
    checks' — count Retained Profits as a member and reconcile against Total Equity & Reserves.
    Excluded as a subtotal, it left the section 13,517,775 (1966) and 2,182,599 (嘉民) short."""
    members = rollups.section_members(view)["bs_equity"]
    assert RETAINED in members.dedicated and RETAINED not in members.subtotals

    relation = next(r for r in section_relations(load_template(_template()), view)
                    if r.id == "section_reconciliation:bs_equity")
    assert relation.target == TOTAL_EQUITY
    assert RETAINED in relation.components
