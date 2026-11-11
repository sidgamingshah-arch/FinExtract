"""APPLE'S 10-K LAYOUT: blocks headed with their own net change, and per-share pairs.

Apple's FY2025 10-K (pages 29-30) prints two things the first 10-K reading could not place:

  * its comprehensive-income blocks under headings that name the block AND end in its tax —
    "Change in unrealized gains/losses on derivative instruments, net of tax:" — with an
    "Adjustment for net (gains)/losses realized and included in net income" row beneath. Read with
    that heading, the row opened on the heading's own "change in unrealized gains/losses on …",
    which the block's net-change line reads too, so `map_ontology._block_caption` refused it and
    the row stayed unplaced;
  * earnings per share as a bare "Basic $" / "Diluted $" pair under "Earnings per share:", and the
    shares as "Basic" / "Diluted" under "Shares used in computing earnings per share:". The
    captions alone name no line, so the four lines were empty.

And the unplaced reclassification exposed a rule meant for another case: a face line with a rollup
whose parts are only partly read was published as its printed figure PLUS the parts — right for net
fixed assets on a mainland face, whose parts are printed beside it, and wrong for a block whose
parts are inside its printed net change (`parts_of_printed`). Hedges read 637 + 849 = 1,486.

The figures are Apple's (millions; shares in thousands): 2023 / 2024 / 2025.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import rollups
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.map_ontology import BLOCK_HEADING_FLAG
from tests.test_a_10k_statement_of_comprehensive_income import _map, _row

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = TEMPLATES / "output_csv_usgaap_v1_template.json"
SET = TEMPLATES / "output_csv_usgaap_line_items.json"

OCI = "Other comprehensive income/(loss):"
DERIV = "Change in unrealized gains/losses on derivative instruments, net of tax:"
DEBT = "Change in unrealized gains/losses on marketable debt securities, net of tax:"
ADJUST = "Adjustment for net (gains)/losses realized and included in net income"
EPS_HEAD = "Earnings per share:"
SHARES_HEAD = "Shares used in computing earnings per share:"


@pytest.fixture(scope="module")
def usgaap():
    return load_line_item_set(json.loads(SET.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def template():
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _apple_oci():
    return [
        _row("Change in foreign currency translation, net of tax", ("-765", "395", "-267"),
             banner=OCI),
        _row("Change in fair value of derivative instruments", ("323", "-832", "849"),
             banner=OCI, sub=DERIV),
        _row(ADJUST, ("-1717", "-1337", "-212"), banner=OCI, sub=DERIV),
        _row("Total change in unrealized gains/losses on derivative instruments",
             ("-1394", "-2169", "637"), banner=OCI, sub=DERIV),
        _row("Change in fair value of marketable debt securities", ("1563", "5850", "817"),
             banner=OCI, sub=DEBT),
        _row(ADJUST, ("253", "204", "414"), banner=OCI, sub=DEBT),
        _row("Total change in unrealized gains/losses on marketable debt securities",
             ("1816", "6054", "1231"), banner=OCI, sub=DEBT),
        _row("Total other comprehensive income/(loss)", ("-343", "4280", "1601"), banner=OCI),
    ]


def test_apples_blocks_bind_every_row_to_its_own_line(usgaap):
    doc = _map(_apple_oci(), usgaap)
    assert [r.canonical_key for r in doc.line_items] == [
        "is_oci__foreign_currency_translation",
        "is_oci__cfh_unrealized_gains_losses", "is_oci__cfh_reclassification",
        "is_oci__cash_flow_hedges",
        "is_oci__afs_unrealized_gains_losses", "is_oci__afs_reclassification",
        "is_oci__available_for_sale_securities",
        "is_oci__total_other_comprehensive_income"]
    # Only the two adjustment rows needed their heading; every other row names its line.
    flagged = [r.canonical_key for r in doc.line_items if BLOCK_HEADING_FLAG in r.confidence.flags]
    assert flagged == ["is_oci__cfh_reclassification", "is_oci__afs_reclassification"]


@pytest.mark.parametrize("caption, key", [
    (f"{DERIV.rstrip(':')} {ADJUST}", "is_oci__cfh_reclassification"),
    (f"{DEBT.rstrip(':')} {ADJUST}", "is_oci__afs_reclassification"),
    # A one-row net change stays the net change: "net of reclassification adjustments" is not the
    # reclassification itself.
    ("Net unrealized gains (losses) on available-for-sale securities, net of reclassification "
     "adjustments", "is_oci__available_for_sale_securities"),
    (DERIV.rstrip(":"), "is_oci__cash_flow_hedges"),
    (DEBT.rstrip(":"), "is_oci__available_for_sale_securities"),
])
def test_the_reclassification_read_with_its_heading_is_not_the_blocks_net_change(usgaap, caption,
                                                                                   key):
    matcher = OntologyMatcher(build_working_view(usgaap))
    hit = matcher.match(caption, statement="profit_and_loss",
                        section="other comprehensive income")
    assert hit is not None and hit.canonical_key == key, (caption, hit)


def _apple_per_share():
    return [
        _row("Net income $", ("96995", "93736", "112010"), banner="Operating expenses:", page=49),
        _row("Basic $", ("6.16", "6.11", "7.49"), sub=EPS_HEAD, page=49),
        _row("Diluted $", ("6.13", "6.08", "7.46"), sub=EPS_HEAD, page=49),
        _row("Basic", ("15744231", "15343783", "14948500"), sub=SHARES_HEAD, page=49),
        _row("Diluted", ("15812547", "15408095", "15004697"), sub=SHARES_HEAD, page=49),
    ]


def test_a_10ks_bare_per_share_pairs_bind_by_their_headings(usgaap):
    doc = _map(_apple_per_share(), usgaap)
    assert [r.canonical_key for r in doc.line_items[1:]] == [
        "is_pl__eps_basic", "is_pl__eps_diluted",
        "is_pl__weighted_average_shares_basic", "is_pl__weighted_average_shares_diluted"]
    assert all(BLOCK_HEADING_FLAG in r.confidence.flags for r in doc.line_items[1:])


def test_off_a_10k_a_bare_per_share_row_is_still_not_placed(usgaap):
    doc = _map(_apple_per_share(), usgaap, us_10k=False)
    assert [r.canonical_key for r in doc.line_items[1:]] == [None, None, None, None]


# ── THE ARITHMETIC ──────────────────────────────────────────────────────────────────────────

def _reported(figures):
    return lambda key: figures.get(key)


def test_a_blocks_printed_net_change_stands_when_one_of_its_parts_is_unread(template):
    printed = {"is_oci__cash_flow_hedges": 637.0, "is_oci__cfh_unrealized_gains_losses": 849.0}
    calc = rollups.evaluate(template, _reported(printed))["is_oci__cash_flow_hedges"]
    assert not calc.computable, "the printed net change stands; nothing is added to it"


def test_a_blocks_net_change_is_computed_when_its_parts_are_read(template):
    parts = {"is_oci__cfh_unrealized_gains_losses": 849.0, "is_oci__cfh_reclassification": -212.0}
    calc = rollups.evaluate(template, _reported({**parts, "is_oci__cash_flow_hedges": 637.0}))
    assert calc["is_oci__cash_flow_hedges"].computable
    assert calc["is_oci__cash_flow_hedges"].value == 637.0


def test_a_face_line_whose_parts_are_printed_beside_it_keeps_the_old_rule():
    """The rule the flag is an exception to, unchanged: a `line` whose parts are separate face
    figures (a mainland 固定资产 with 在建工程 below it) is its printed figure plus those parts."""
    tpl = {"statements": [{"type": "balance_sheet", "sections": [{
        "node_id": "bs_nca", "canonical_key": "bs_nca", "role": "header", "children": [
            {"node_id": "a", "canonical_key": "a", "role": "line",
             "rollup": {"op": "sum", "children": ["b", "c"]}},
            {"node_id": "b", "canonical_key": "b", "role": "line"},
            {"node_id": "c", "canonical_key": "c", "role": "line"}]}]}]}
    calc = rollups.evaluate(tpl, _reported({"a": 100.0, "b": 30.0}))["a"]
    assert calc.computable and calc.value == 130.0
    flagged = json.loads(json.dumps(tpl))
    flagged["statements"][0]["sections"][0]["children"][0]["rollup"]["parts_of_printed"] = True
    assert not rollups.evaluate(flagged, _reported({"a": 100.0, "b": 30.0}))["a"].computable


def test_only_the_two_block_lines_carry_the_flag(template):
    flagged = sorted(n["canonical_key"] for s in template["statements"] for sec in s["sections"]
                     for n in sec.get("children") or []
                     if (n.get("rollup") or {}).get("parts_of_printed"))
    assert flagged == ["is_oci__available_for_sale_securities", "is_oci__cash_flow_hedges"]


def test_no_other_shipped_template_carries_the_flag():
    for path in TEMPLATES.glob("*_template.json"):
        if path == TEMPLATE:
            continue
        assert "parts_of_printed" not in path.read_text(encoding="utf-8"), path.name
