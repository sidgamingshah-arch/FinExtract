"""THE 10-K TEMPLATE CARRIES THE STATEMENT OF COMPREHENSIVE INCOME.

Built by `scripts/build_usgaap_pair.py` as four sections of the statement of operations (the HK
template's `is_oci` is the precedent): other comprehensive income, the attribution of comprehensive
income, and the available-for-sale securities' and the cash flow hedges' blocks. Pinned here:

  * the lines exist, are face lines of the statement of operations, and are scoped by the section
    a row is printed under — the OCI banner, or a title or banner naming comprehensive income;
  * the arithmetic is the template's own, as rollups and an identity, and it evaluates: each
    block's net change is its change plus its reclassification, total OCI the five components,
    comprehensive income net income plus total OCI; the section reconciliation adds the blocks'
    net changes alone, so a filer printing only those reconciles and one printing all three rows of
    each block is not counted twice;
  * a block's bare rows, printed twice (Alphabet 2025), bind the right lines on a 10-K, read with
    the heading over them (`map_ontology._block_caption`); a heading that names a line itself lends
    its rows nothing; nothing else on any other filing is read that way;
  * the net income the statement opens on is a line of its own and never doubles the statement of
    operations' net income;
  * no EPS or share-count line reads a caption naming comprehensive income;
  * a 10-K "Less:" row that prints both signs keeps them (`normalize._prints_both_signs`).

The figures are Alphabet's 2025 10-K, page 50 (millions): 2023 / 2024 / 2025.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_template
from app.services import face_context
from app.services.mapping import OntologyMatcher, SourceRef
from app.services.note_sourced import resolve_sources
from app.services.structural_checks import evaluate_structure, section_relations
from app.services.working_view import build_working_view
from app.stages.map_ontology import BLOCK_HEADING_FLAG, MapOntologyStage
from app.stages.normalize import NormalizeStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = TEMPLATES / "output_csv_usgaap_v1_template.json"
SET = TEMPLATES / "output_csv_usgaap_line_items.json"
PL = "profit_and_loss"

TITLE = "CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME"
OCI = "Other comprehensive income (loss):"
AFS, CFH = "Available-for-sale investments:", "Cash flow hedges:"
CHANGE = "Change in net unrealized gains (losses)"
RECLASS = "Less: reclassification adjustment for net (gains) losses included in net income"
FX_ROW = ("Change in foreign currency translation adjustment, net of income tax benefit (expense) "
          "of $63, $(49) and $180")
AFS_NET_ROW = "Net change, net of income tax benefit (expense) of $(698), $(190), and $(276)"
CFH_NET_ROW = "Net change, net of income tax benefit (expense) of $2, $(151), and $174"
AFS_RCL_NAMED = ("Reclassification adjustment for (gains) losses on available-for-sale securities "
                 "included in net income")

NI = "is_oci__net_income"
FX = "is_oci__foreign_currency_translation"
AFS_NET, AFS_CHG, AFS_RCL = ("is_oci__available_for_sale_securities",
                             "is_oci__afs_unrealized_gains_losses", "is_oci__afs_reclassification")
CFH_NET, CFH_CHG, CFH_RCL = ("is_oci__cash_flow_hedges", "is_oci__cfh_unrealized_gains_losses",
                             "is_oci__cfh_reclassification")
PENSION, OTHER = "is_oci__pension_and_postretirement", "is_oci__other"
TOTAL, CI = "is_oci__total_other_comprehensive_income", "is_oci__comprehensive_income"
NCI, PARENT = ("is_oci__comprehensive_income_attributable_to_nci",
               "is_oci__comprehensive_income_attributable_to_parent")
SECTIONS = {
    "is_oci": [NI, FX, AFS_NET, CFH_NET, PENSION, OTHER, TOTAL, CI],
    "is_oci_attribution": [NCI, PARENT],
    "is_oci_afs": [AFS_CHG, AFS_RCL],
    "is_oci_cfh": [CFH_CHG, CFH_RCL],
}


@pytest.fixture(scope="module")
def raw():
    return (json.loads(TEMPLATE.read_text(encoding="utf-8")),
            json.loads(SET.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def usgaap():
    return load_line_item_set(json.loads(SET.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(usgaap):
    return OntologyMatcher(build_working_view(usgaap), locale="en", settings=get_settings())


def _pl(raw):
    (pl,) = [st for st in raw[0]["statements"] if st["type"] == PL]
    return pl


def _nodes(raw):
    return {n["canonical_key"]: n for sec in _pl(raw)["sections"] for n in sec["children"]}


# ── the lines ──────────────────────────────────────────────────────────────────────────────────

def test_comprehensive_income_is_four_sections_of_the_statement_of_operations(raw):
    sections = {sec["node_id"]: [n["canonical_key"] for n in sec["children"]]
                for sec in _pl(raw)["sections"]}
    assert {k: v for k, v in sections.items() if k != "is_pl"} == SECTIONS
    assert not any(k.startswith("is_oci") for k in sections["is_pl"])


def test_every_comprehensive_income_line_is_a_face_line_scoped_by_its_banner(raw):
    items = {i["key"]: i for i in raw[1]["items"]}
    for key in (k for keys in SECTIONS.values() for k in keys):
        item = items[key]
        assert item["statements"] == [PL] and item["route"] == "face", key
        assert item["namespace"] == "template" and item["in_output"] is True, key
        assert item["section_scope"] == ["is_oci", "total_comprehensive_income_attributable_to"], key
        assert item["temporality"] == "duration" and item["sign_convention"] == "either", key
        assert "measure" not in item and "note_source" not in item, key
    # …and no statement-of-operations line is scoped where they are.
    for item in raw[1]["items"]:
        if item["key"].startswith("is_pl__"):
            assert "is_oci" not in item["section_scope"], item["key"]


# ── the arithmetic ─────────────────────────────────────────────────────────────────────────────

def test_the_arithmetic_is_written_as_the_templates_rollups_and_an_identity(raw):
    nodes = _nodes(raw)
    # `parts_of_printed`: a block's parts are inside its printed net change, so one part unread
    # leaves the printed figure standing rather than adding the other to it
    # (`test_an_apple_style_10k_reads_its_blocks_by_heading.py`).
    assert nodes[AFS_NET]["rollup"] == {"op": "sum", "children": [AFS_CHG, AFS_RCL],
                                        "parts_of_printed": True}
    assert nodes[CFH_NET]["rollup"] == {"op": "sum", "children": [CFH_CHG, CFH_RCL],
                                        "parts_of_printed": True}
    assert nodes[TOTAL]["rollup"] == {"op": "sum", "children": [FX, AFS_NET, CFH_NET, PENSION, OTHER]}
    assert nodes[CI]["rollup"] == {"op": "sum", "children": [NI, TOTAL]}
    assert _pl(raw)["identities"] == [{
        "id": "comprehensive_income_opens_on_net_income", "lhs": NI,
        "rhs": {"op": "sum", "children": ["is_pl__profit_for_the_year"]}}]
    items = {i["key"]: i for i in raw[1]["items"]}
    for key in (AFS_NET, CFH_NET, TOTAL, CI):
        assert items[key]["extraction_mode"] == "extract_or_derive", key
        assert [t["ref"] for t in items[key]["terms"]] == nodes[key]["rollup"]["children"], key
        assert all(t["sign"] == 1 for t in items[key]["terms"]), key
        assert key in raw[1]["cross_check_master"]["keys"], key
    assert not set(raw[1]["others_master"]["keys"]) & {k for ks in SECTIONS.values() for k in ks}


def _item(key, figures, page=50):
    li = LineItem(source_label=key, canonical_key=key)
    for period, v in zip(("col2", "prior", "current"), figures):
        if v is None:
            continue
        li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                    value=Decimal(v), value_raw=Decimal(v),
                                    provenance=Provenance(page_index=page)))
    return li


ALPHABET = {
    "is_pl__profit_for_the_year": ("73795", "100118", "132170"),
    NI: ("73795", "100118", "132170"),
    FX: ("735", "-1673", "2522"),
    AFS_CHG: ("1344", "-116", "1146"), AFS_RCL: ("1168", "782", "-169"), AFS_NET: ("2512", "666", "977"),
    CFH_CHG: ("168", "775", "-779"), CFH_RCL: ("-214", "-166", "164"), CFH_NET: ("-46", "609", "-615"),
    TOTAL: ("3201", "-398", "2884"), CI: ("76996", "99720", "135054"),
}


def _structure(raw, usgaap, figures):
    report = evaluate_structure(load_template(raw[0]), [_item(k, v) for k, v in figures.items()],
                                ontology=build_working_view(usgaap))
    out: dict[str, set[str]] = {}
    for r in report.results:
        if "oci" in r.rule_id or "comprehensive" in r.rule_id:
            out.setdefault(r.rule_id, set()).add(r.status if r.status != "skipped"
                                                 else f"skipped:{r.details.get('reason')}")
    return out


def test_alphabets_comprehensive_income_adds_up_in_every_relation(raw, usgaap):
    got = _structure(raw, usgaap, ALPHABET)
    for rule in ("rollup:" + AFS_NET, "rollup:" + CFH_NET, "rollup:" + CI,
                 "identity:comprehensive_income_opens_on_net_income",
                 "section_reconciliation:is_oci"):
        assert got[rule] == {"pass"}, (rule, got[rule])
    # Total OCI's own rollup names pension and other, which Alphabet does not print and no
    # leftover line accounts for: unknown, not nil. The section reconciliation checks the sum.
    assert got["rollup:" + TOTAL] == {"skipped:components_not_mapped"}


def test_a_block_read_with_the_wrong_sign_breaks_its_net_change(raw, usgaap):
    """The figure the "Less:" rule used to publish for 2024's reclassification, -|782|."""
    got = _structure(raw, usgaap, {**ALPHABET, AFS_RCL: ("-1168", "-782", "-169")})
    assert "fail" in got["rollup:" + AFS_NET]


def test_a_filer_printing_only_each_blocks_net_change_reconciles(raw, usgaap):
    """Microsoft prints "Net change related to investments" and no change or reclassification
    rows: the section reconciliation adds the net change, not the parts it does not print."""
    net_only = {k: v for k, v in ALPHABET.items()
                if k not in (AFS_CHG, AFS_RCL, CFH_CHG, CFH_RCL)}
    got = _structure(raw, usgaap, net_only)
    assert got["section_reconciliation:is_oci"] == {"pass"}
    assert got["rollup:" + AFS_NET] == {"skipped:components_not_mapped"}


def test_the_reconciliation_counts_a_block_once(raw, usgaap):
    """Printed all three rows of a block, the change and the reclassification are not added beside
    the net change: they are another section's."""
    rel = next(r for r in section_relations(load_template(raw[0]), build_working_view(usgaap))
               if r.id == "section_reconciliation:is_oci")
    assert rel.target == TOTAL
    assert set(rel.components) == {FX, AFS_NET, CFH_NET, PENSION, OTHER}


# ── the blocks' bare rows ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("caption", [CHANGE, RECLASS, AFS_NET_ROW, CFH_NET_ROW,
                                     "Available-for-sale investments:", "Cash flow hedges:"])
def test_a_blocks_bare_row_or_heading_binds_no_line_by_itself(matcher, caption):
    hit = matcher.match(caption, statement=PL, section=OCI)
    assert hit.canonical_key is None, (caption, hit.canonical_key)


@pytest.mark.parametrize("caption,key", [
    (FX_ROW, FX),
    ("Other comprehensive income (loss)", TOTAL),
    ("Comprehensive income $", CI),
    ("Net unrealized gains (losses) on available-for-sale securities, net of tax", AFS_NET),
    ("Net change related to investments, net of tax", AFS_NET),
    ("Total change in unrealized gains/losses on marketable debt securities", AFS_NET),
    ("Change in fair value of marketable debt securities", AFS_CHG),
    (AFS_RCL_NAMED, AFS_RCL),
    ("Net change related to derivatives, net of tax", CFH_NET),
    ("Unrealized gains (losses) on cash flow hedges, net of tax", CFH_NET),
    ("Change in fair value of derivative instruments", CFH_CHG),
    ("Reclassification of net (gains) losses on cash flow hedges to net income", CFH_RCL),
    ("Pension and other postretirement benefit plans, net of tax", PENSION),
    ("Share of other comprehensive income of equity method investees", OTHER),
    ("Comprehensive income attributable to noncontrolling interests", NCI),
    ("Comprehensive income attributable to Bluegate", PARENT),
])
def test_a_caption_naming_its_component_binds_its_line(matcher, caption, key):
    hit = matcher.match(caption, statement=PL, section=OCI)
    assert hit.canonical_key == key and hit.confidence >= 0.9, (caption, hit.canonical_key)


def _row(caption, figures, *, banner=None, sub="", page=50):
    li = LineItem(source_label=caption, section_hint=banner, group_hint=sub)
    for period, v in zip(("col2", "prior", "current"), figures):
        li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                    value=Decimal(v), value_raw=Decimal(v),
                                    provenance=Provenance(page_index=page)))
    return li


def _alphabet_rows():
    """Alphabet's statement of operations' close and its statement of comprehensive income."""
    return [
        _row("Income before income taxes", ("85717", "119815", "158826"),
             banner="Costs and expenses:", page=49),
        _row("Net income $", ("73795", "100118", "132170"), banner="Costs and expenses:", page=49),
        _row("Net income $", ("73795", "100118", "132170"), banner=TITLE),
        _row(FX_ROW, ("735", "-1673", "2522"), banner=OCI),
        _row(CHANGE, ("1344", "-116", "1146"), banner=OCI, sub=AFS),
        _row(RECLASS, ("1168", "782", "-169"), banner=OCI, sub=AFS),
        _row(AFS_NET_ROW, ("2512", "666", "977"), banner=OCI, sub=AFS),
        _row(CHANGE, ("168", "775", "-779"), banner=OCI, sub=CFH),
        _row(RECLASS, ("-214", "-166", "164"), banner=OCI, sub=CFH),
        _row(CFH_NET_ROW, ("-46", "609", "-615"), banner=OCI, sub=CFH),
        _row("Other comprehensive income (loss)", ("3201", "-398", "2884"), banner=OCI),
        _row("Comprehensive income $", ("76996", "99720", "135054"), banner=OCI),
    ]


def _map(rows, usgaap, *, us_10k=True):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="goog.pdf")
    doc.pages = [PageSource(index=p, kind=PageKind.FACE, statement=PL) for p in (49, 50)]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(usgaap)
    ctx.line_items = usgaap
    ctx.template_def = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    ctx.us_10k_filing = us_10k
    MapOntologyStage().run(doc, ctx)
    return doc


def test_a_10k_blocks_bare_rows_bind_their_lines_read_with_the_heading(usgaap):
    doc = _map(_alphabet_rows(), usgaap)
    keys = [(r.group_hint, r.canonical_key) for r in doc.line_items[3:]]
    assert keys == [("", FX), (AFS, AFS_CHG), (AFS, AFS_RCL), (AFS, AFS_NET),
                    (CFH, CFH_CHG), (CFH, CFH_RCL), (CFH, CFH_NET), ("", TOTAL), ("", CI)]
    flagged = {r.canonical_key for r in doc.line_items if BLOCK_HEADING_FLAG in r.confidence.flags}
    assert flagged == {AFS_CHG, AFS_RCL, AFS_NET, CFH_CHG, CFH_RCL, CFH_NET}


def test_only_a_10k_reads_a_row_with_its_heading(usgaap):
    doc = _map(_alphabet_rows(), usgaap, us_10k=False)
    assert all(not r.canonical_key for r in doc.line_items if r.group_hint)


def test_a_heading_that_names_a_line_lends_its_rows_nothing(usgaap):
    """A pension block prints its parts and their net change under a heading the pension line
    takes by itself: read with it, every row would be "pension …", added on one line."""
    head = "Pension and other postretirement benefit plans:"
    rows = [_row("Net actuarial gain (loss) arising during the year", ("-50", "20", "10"),
                 banner=OCI, sub=head),
            _row("Amortization of net actuarial loss", ("5", "6", "7"), banner=OCI, sub=head),
            _row("Net change", ("-45", "26", "17"), banner=OCI, sub=head)]
    doc = _map(rows, usgaap)
    assert [r.canonical_key for r in doc.line_items] == [None, None, None]


def test_the_opening_net_income_is_its_own_line_and_not_the_pls_twice(usgaap):
    doc = _map(_alphabet_rows(), usgaap)
    by_key: dict[str, list] = {}
    for r in doc.line_items:
        by_key.setdefault(r.canonical_key, []).append(r)
    assert [r.section_hint for r in by_key["is_pl__profit_for_the_year"]] == ["Costs and expenses:"]
    assert [r.section_hint for r in by_key[NI]] == [TITLE]


@pytest.mark.parametrize("section", [None, "Costs and expenses:"])
def test_net_income_printed_under_no_comprehensive_banner_is_the_pls(matcher, section):
    hit = matcher.match("Net income", statement=PL, section=section)
    assert hit.canonical_key == "is_pl__profit_for_the_year", (section, hit.canonical_key)


def test_the_request_names_each_blocks_rows_by_their_heading():
    """A model is shown the block's heading as the row's `subheading`, and a citation's `group`
    picks the block."""
    doc = DocumentModel(filename="goog.pdf")
    doc.pages = [PageSource(index=50, kind=PageKind.FACE, statement=PL)]
    doc.line_items = _alphabet_rows()[2:]
    rows = face_context.face_rows(doc, {PL})[0]["rows"]
    subs = [(r["caption"], r.get("subheading")) for r in rows if r["caption"] == CHANGE]
    assert subs == [(CHANGE, AFS), (CHANGE, CFH)]
    face = face_context.face_index(doc)
    resolved, unresolved = resolve_sources(
        [SourceRef(statement=PL, caption=RECLASS, group=CFH)], [], face)
    assert not unresolved and resolved[0]["figures"]["current"] == "164"


# ── EPS ────────────────────────────────────────────────────────────────────────────────────────

EPS = ("is_pl__eps_basic", "is_pl__eps_diluted", "is_pl__weighted_average_shares_basic",
       "is_pl__weighted_average_shares_diluted")


@pytest.mark.parametrize("caption", ["Comprehensive income per share – basic",
                                     "Basic comprehensive income per share",
                                     "Comprehensive income per common share, diluted"])
def test_no_per_share_line_reads_a_comprehensive_income_caption(matcher, caption):
    hit = matcher.match(caption, statement=PL, section=None)
    assert hit.canonical_key not in EPS, (caption, hit.canonical_key)


def test_each_per_share_line_excludes_comprehensive(raw):
    items = {i["key"]: i for i in raw[1]["items"]}
    for key in EPS:
        assert "(?i)comprehensive" in items[key]["exclude_hints"], key


# ── a 10-K "Less:" row printing both signs ─────────────────────────────────────────────────────

def _less(figures):
    li = LineItem(source_label=RECLASS)
    for period, v in zip(("col2", "prior", "current"), figures):
        li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                    value=Decimal(v), value_raw=Decimal(v)))
    return li


def _normalised(figures, us_10k):
    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [_less(figures)]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.us_10k_filing = us_10k
    NormalizeStage().run(doc, ctx)
    return [str(ev.value) for ev in doc.line_items[0].values.values()]


def test_a_10k_less_row_printing_both_signs_keeps_them():
    assert _normalised(("1168", "782", "-169"), True) == ["1168", "782", "-169"]
    assert _normalised(("-214", "-166", "164"), True) == ["-214", "-166", "164"]


def test_a_less_row_is_still_a_deduction_off_a_10k_or_printed_one_way():
    assert _normalised(("1168", "782", "-169"), False) == ["-1168", "-782", "-169"]
    assert _normalised(("1168", "782", "169"), True) == ["-1168", "-782", "-169"]
