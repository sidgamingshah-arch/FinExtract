"""``residual_framework``: one block governs all 13 residual concepts, and the engine obeys it.

Every test here edits the rulebook (or leaves it alone) and watches the extraction change, because
that is the only property worth having: a residual block that states a sweep policy the stage does
not execute is worse than no block at all — it is the one place a reviewer can look up what happens
to a row nobody claimed.

The failures being guarded are all failures of ARITHMETIC that nothing downstream can see:

* a plugged residual (reported subtotal − mapped children) makes every section tie by construction,
  so a row extraction missed is absorbed into a plausible number instead of reported as a gap;
* a subtotal caption the mapper failed to claim, swept into that section's Others, double-counts
  the whole section and the section still ties;
* a P&L attribution caption swept into the operating-expense residual merges a flow into a different
  section's arithmetic;
* a narrative sentence or a per-share figure in Others moves the subtotal by whatever it contained.

Three sentences in the block are PROSE and stay prose, because each states in one line what several
read terms already do — ``sweep.candidate_set``, ``itemisation.rule`` and
``reconciliation.identity``. The reference they point at is
``test_the_reconciliation_identity_and_the_candidate_set_are_performed_as_written``: it holds the
arithmetic and the population they describe, so the sentences document tested behaviour rather than
switch it.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology
from app.stages.residual import ResidualStage

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def raw_ontology() -> dict:
    return json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


def _ontology(raw: dict):
    """Loaded the way the extraction worker loads it — RESOLVED, so every concept carries its
    section, its ``face_only``/``note_use`` and its ``temporality``."""
    return load_ontology(copy.deepcopy(raw), resolve=True)


def _li(ordinal: int, label: str, key: str | None, value: int | None,
        role: LineRole = LineRole.LINE, page: int = 0, note: str | None = None) -> LineItem:
    item = LineItem(source_label=label, canonical_key=key, ordinal=ordinal, role=role,
                    note_number=note)
    if value is not None:
        item.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label="current", provenance=Provenance(page_index=page)))
    return item


def _doc(statement: str, items: list[LineItem]) -> DocumentModel:
    doc = DocumentModel(filename="f.pdf", locale="en")
    doc.pages = [PageSource(index=0, statement=statement)]
    doc.line_items = items
    return doc


def _run(doc: DocumentModel, ontology) -> PipelineContext:
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = ontology
    # The sweep refuses to run before the dedicated concepts have been resolved; in the pipeline
    # this is set by the mapping stage.
    ctx.mapping_strategy = "deterministic"
    ResidualStage().run(doc, ctx)
    return ctx


def _report(ctx, key: str) -> dict:
    return next(e for e in ctx.residual_itemisation if e["residual"] == key)


def _current_liabilities(unclaimed_label: str, unclaimed: int = 25, subtotal: int | None = 125,
                        role: LineRole = LineRole.LINE) -> DocumentModel:
    """A minimal current-liabilities section: one dedicated row, one unclaimed row, the subtotal."""
    items = [
        _li(0, "Trade and bills payables", "bs_current_liabilities__current_trade_payables", 100),
        _li(1, unclaimed_label, None, unclaimed, role),
    ]
    if subtotal is not None:
        items.append(_li(2, "Total current liabilities",
                         "bs_current_liabilities__total_current_liabilities", subtotal,
                         LineRole.SUBTOTAL))
    return _doc("balance_sheet", items)


# --- the sweep, and what a component records ---------------------------------------------------

def test_an_unclaimed_face_row_is_swept_and_itemised_with_the_declared_fields(raw_ontology):
    """The row keeps its own label and sign and becomes a named component of the residual — the
    difference between an itemised residual and an unexplained bucket."""
    doc = _current_liabilities("Other taxes payable")
    ctx = _run(doc, _ontology(raw_ontology))

    swept = doc.line_items[1]
    assert swept.canonical_key == "bs_current_liabilities__others"
    assert swept.source_label == "Other taxes payable"      # never relabelled "Others"
    assert "residual_combined" in swept.confidence.flags

    entry = _report(ctx, "bs_current_liabilities__others")
    fields = raw_ontology["residual_framework"]["itemisation"]["component_fields"]
    component = entry["components"][0]
    assert [f for f in fields if f in component] == fields
    assert component["source_row_label"] == "Other taxes payable"
    assert component["sign_as_reported"] == {"consolidated|current": "positive"}
    # source_fact_id is composed in the order global_rules declares.
    assert component["source_fact_id"]["consolidated|current"].count("|") == 7

    recon = entry["reconciliation"][0]
    assert (recon["reported"], recon["dedicated"], recon["residual"]) == (125.0, 100.0, 25.0)
    assert recon["status"] == "tied"
    # "one rounding unit per contributing row": the dedicated row, the component and the subtotal.
    assert recon["tolerance"] == 3.0
    # 25 is 20% of the section subtotal, so both size triggers fire on a section that ties.
    assert "residual_review:component_share_of_subtotal>2%" in swept.confidence.flags
    assert "residual_review:residual_share_of_subtotal>5%" in swept.confidence.flags


def test_a_component_field_the_rulebook_stops_declaring_stops_being_recorded(raw_ontology):
    raw = copy.deepcopy(raw_ontology)
    fields = raw["residual_framework"]["itemisation"]["component_fields"]
    fields.remove("source_fact_id")
    doc = _current_liabilities("Other taxes payable")
    ctx = _run(doc, _ontology(raw))

    component = _report(ctx, "bs_current_liabilities__others")["components"][0]
    assert "source_fact_id" not in component
    assert "source_row_label" in component


def test_the_residual_is_the_sum_of_its_components_not_a_plug(raw_ontology):
    """The subtotal says 200 and the rows account for 125. A plug would make the residual 100 and
    the section tie; the framework's sum leaves the residual at 25 and reports the 75 that is
    missing, which is the only version a reviewer can act on."""
    doc = _current_liabilities("Other taxes payable", unclaimed=25, subtotal=200)
    ctx = _run(doc, _ontology(raw_ontology))

    swept = doc.line_items[1]
    assert swept.values[next(iter(swept.values))].value == Decimal(25)
    recon = _report(ctx, "bs_current_liabilities__others")["reconciliation"][0]
    assert recon["residual"] == 25.0 and recon["diff"] == 75.0
    assert recon["status"] == "unallocated_gap"

    gap = "unallocated_gap:bs_s3_current_liabilities:consolidated|current=75"
    assert gap in swept.confidence.flags
    subtotal_row = doc.line_items[2]
    # …and the section is routed to review: the subtotal row is the section's own claim about
    # itself and would otherwise still read as auto-approvable.
    assert gap in subtotal_row.confidence.flags
    assert "low_mapping_confidence" in subtotal_row.confidence.flags
    assert "residual_review:unallocated_gap" in swept.confidence.flags


def test_a_section_that_prints_no_subtotal_is_itemised_but_unreconciled(raw_ontology):
    doc = _current_liabilities("Other taxes payable", subtotal=None)
    ctx = _run(doc, _ontology(raw_ontology))

    swept = doc.line_items[1]
    assert swept.canonical_key == "bs_current_liabilities__others"
    recon = _report(ctx, "bs_current_liabilities__others")["reconciliation"][0]
    assert recon["status"] == "no_reported_subtotal"
    assert "residual_unreconciled:bs_s3_current_liabilities" in swept.confidence.flags
    assert swept.confidence.mapping == pytest.approx(0.4)


def test_the_reconciliation_identity_and_the_candidate_set_are_performed_as_written(raw_ontology):
    """``reconciliation.identity`` and ``sweep.candidate_set`` are PROSE, and this is the test they
    point at.

    Neither sentence is a switch: the identity is the equation ``rollups.reconcile_section``
    evaluates (its variable terms — tolerance, on_failure, sections_without_reported_subtotal — are
    read from the block and tested above), and the candidate set is the population built from
    ``population``, the four eligibility entries and ``notes_as_source``. What can be tested is that
    the code performs the arithmetic and takes the population the sentences describe, so a reader
    who edits the sentence and sees nothing change has this to read instead.
    """
    doc = _current_liabilities("Other taxes payable", unclaimed=25, subtotal=200)
    # The section cites a note. "on the FACE of the statement" is what keeps the note's own rows out
    # of the candidate set — a note is evidence for a face amount, not a candidate row.
    doc.line_items[0].note_number = "18"
    table = NotesTable(note_number="18", title="Trade and bills payables")
    item = NoteItem(raw_label="Bills payable to a related party", ordinal=0)
    item.set_value(ExtractedValue(
        value=Decimal(15), value_raw=Decimal(15), basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    table.items.append(item)
    doc.notes = [table]
    ctx = _run(doc, _ontology(raw_ontology))

    entry = _report(ctx, "bs_current_liabilities__others")
    assert [c["source_row_label"] for c in entry["components"]] == ["Other taxes payable"]
    recon = entry["reconciliation"][0]
    # "The residual's value is the arithmetic sum of its components" (itemisation.rule).
    assert recon["residual"] == sum(
        float(c["value"]["consolidated|current"]) for c in entry["components"])
    # "reported_section_subtotal − Σ(dedicated) − Σ(residual components) = 0", and the difference
    # from zero is what gets reported.
    assert recon["diff"] == recon["reported"] - recon["dedicated"] - recon["residual"]
    assert (recon["reported"], recon["dedicated"], recon["residual"]) == (200.0, 100.0, 25.0)
    assert recon["diff"] == 75.0 > recon["tolerance"] and recon["status"] == "unallocated_gap"


# --- the prohibitions --------------------------------------------------------------------------

def _without_prohibition(raw: dict, needle: str) -> dict:
    out = copy.deepcopy(raw)
    kept = [p for p in out["residual_framework"]["prohibitions"] if needle not in p.lower()]
    assert len(kept) == len(out["residual_framework"]["prohibitions"]) - 1, needle
    out["residual_framework"]["prohibitions"] = kept
    return out


def _plug_case(raw: dict):
    """A figure routed into the bucket by something other than the sweep — here the LLM
    gap-closer — is a residual carrying a value no component accounts for: the plug, arrived at
    from the other direction."""
    doc = _current_liabilities("Other taxes payable", subtotal=200)
    routed = _li(3, "Accruals and other payables", "bs_current_liabilities__others", 40)
    routed.confidence.method = "llm"
    doc.line_items.append(routed)
    return raw, doc, lambda d, ctx: any(
        f.startswith("residual_plug_suspected:") for f in d.line_items[3].confidence.flags)


def _matched_case(raw: dict):
    """A residual claimed by the mapper's fuzzy tier — what happens when a concept omits
    ``alias_matching: disabled``, since "Others" fuzzes against almost any short caption."""
    doc = _current_liabilities("Other taxes payable")
    doc.line_items[1].canonical_key = "bs_current_liabilities__others"
    doc.line_items[1].confidence.method = "fuzzy"
    return raw, doc, lambda d, ctx: ("residual_alias_populated:fuzzy"
                                     in d.line_items[1].confidence.flags)


def _unitemised_case(raw: dict):
    """``itemise: false`` leaves the bucket holding a figure with nothing behind it."""
    out = copy.deepcopy(raw)
    for m in out["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["residual_policy"]["itemise"] = False
    return out, _current_liabilities("Other taxes payable"), lambda d, ctx: any(
        f.startswith("residual_value_without_components") for f in d.line_items[1].confidence.flags)


def _untested_case(raw: dict):
    """The caption IS a dedicated concept of the section. Swept, the section still ties and a named
    figure is filed as unexplained remainder."""
    doc = _doc("balance_sheet", [
        _li(0, "Trade and bills payables", None, 100),
        _li(1, "Total current liabilities", "bs_current_liabilities__total_current_liabilities",
            100, LineRole.SUBTOTAL),
    ])
    return raw, doc, lambda d, ctx: any(f.startswith("residual_dedicated_not_tested:")
                                        for f in d.line_items[0].confidence.flags)


def _spans_case(raw: dict):
    """A policy pointing at one section while the concept inherits another: the current-liabilities
    bucket sweeping the non-current section, with both sections still tying afterwards."""
    out = copy.deepcopy(raw)
    for m in out["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["residual_policy"]["section_scope"] = "bs_s4_non_current_liabilities"
    doc = _doc("balance_sheet", [
        _li(0, "Interest-bearing bank borrowings",
            "bs_non_current_liabilities__non_current_borrowings", 100),
        _li(1, "Other long-term obligations", None, 25),
        _li(2, "Total non-current liabilities",
            "bs_non_current_liabilities__total_non_current_liabilities", 125, LineRole.SUBTOTAL),
    ])
    return out, doc, lambda d, ctx: (d.line_items[1].canonical_key
                                     == "bs_non_current_liabilities__others")


@pytest.mark.parametrize("needle,case", [
    ("plug", _plug_case),
    ("alias", _matched_case),
    ("itemised component", _unitemised_case),
    ("dedicated concept", _untested_case),
    ("spans", _spans_case),
])
def test_every_prohibition_is_a_guard_the_rulebook_can_also_remove(raw_ontology, needle, case):
    """Each of the five ``prohibitions`` switches on the guard that enforces it.

    They are not restatements of the sweep's per-row terms: every one of them is a way a residual
    ends up holding a figure the sweep never took, which is the failure the section's own subtotal
    cannot show — it ties either way.
    """
    raw, doc, holds = case(raw_ontology)
    ctx = _run(doc, _ontology(raw))
    assert holds(doc, ctx), f"the guard {needle!r} names did not fire"

    raw, doc, holds = case(_without_prohibition(raw_ontology, needle))
    ctx = _run(doc, _ontology(raw))
    assert not holds(doc, ctx), f"deleting the {needle!r} prohibition left the guard in place"


# --- eligibility -------------------------------------------------------------------------------

def test_a_subtotal_caption_the_mapper_missed_is_refused_by_never_sweep(raw_ontology):
    """Printed as an ordinary line and claimed by nobody, "Total current liabilities" would be
    swept into current liabilities' own Others — counting the entire section twice, with the
    section still tying afterwards."""
    doc = _current_liabilities("Total current liabilities", unclaimed=125)
    ctx = _run(doc, _ontology(raw_ontology))

    assert doc.line_items[1].canonical_key is None
    assert any(f.startswith("residual_never_sweep:") for f in doc.line_items[1].confidence.flags)
    assert not hasattr(ctx, "residual_itemisation")

    # …and it is `never_sweep` doing it: with the entry removed the row sweeps in.
    raw = copy.deepcopy(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["never_sweep"].remove("bs_current_liabilities__total_current_liabilities")
    doc = _current_liabilities("Total current liabilities", unclaimed=125)
    _run(doc, _ontology(raw))
    assert doc.line_items[1].canonical_key == "bs_current_liabilities__others"


def test_a_per_share_row_is_ineligible_until_the_eligibility_list_stops_saying_so(raw_ontology):
    """Earnings per share is a ratio in cents. Added into a section subtotal it is nonsense, and
    it is far too small for any rollup to notice."""
    def statement() -> DocumentModel:
        return _doc("profit_and_loss", [
            _li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
                LineRole.SUBTOTAL),
            _li(1, "Basic earnings per share (HK cents)", None, 12),
        ])

    doc = statement()
    _run(doc, _ontology(raw_ontology))
    assert doc.line_items[1].canonical_key is None
    assert "residual_ineligible:per-share figure" in doc.line_items[1].confidence.flags

    raw = copy.deepcopy(raw_ontology)
    elig = raw["residual_framework"]["sweep"]["eligibility"]
    elig[2] = elig[2].replace("per-share figure, ", "")
    # Eligibility 5 comes off with it. A positive earnings-per-share figure ALSO contradicts the
    # sign the operating-expense section expects, so leaving that entry in place would refuse the
    # row for the other reason and prove nothing about this one.
    del elig[4]
    doc = statement()
    _run(doc, _ontology(raw))
    assert doc.line_items[1].canonical_key == "pl_expenses__others"


def test_an_attribution_caption_is_not_merged_into_the_section_above_it(raw_ontology):
    """"Non-controlling interests" printed under the profit-attribution heading is a FLOW belonging
    to its own concept. Left eligible it resolves to the nearest section with a residual — the
    operating expenses — and a period flow lands inside another section's arithmetic."""
    doc = _doc("profit_and_loss", [
        _li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
            LineRole.SUBTOTAL),
        _li(1, "Attributable to non-controlling interests:", None, 40),
    ])
    _run(doc, _ontology(raw_ontology))
    row = doc.line_items[1]
    assert row.canonical_key is None
    assert "residual_ineligible:attribution caption" in row.confidence.flags


def test_a_row_whose_section_cannot_be_resolved_goes_to_review_not_to_a_bucket(raw_ontology):
    """No banner, no subtotal below it, nothing mapped above it: there is no section, and
    ``cross_section: false`` means there is no rescue either."""
    doc = _doc("balance_sheet", [_li(0, "Deposits paid for acquisition of land", None, 60)])
    _run(doc, _ontology(raw_ontology))
    assert doc.line_items[0].canonical_key is None
    assert "residual_section_unresolved" in doc.line_items[0].confidence.flags


def test_the_next_statements_subtotal_never_places_a_row(raw_ontology):
    """A row at the foot of the balance sheet, with no section subtotal of its own below it: the
    income statement's first subtotal is not evidence about it, and neither is the balance sheet's
    own grand total. The section above it is."""
    doc = _doc("balance_sheet", [
        _li(0, "Property, plant and equipment",
            "bs_non_current_assets__property_plant_and_equipment", 700),
        _li(1, "Deposits paid for acquisition of land", None, 60),
        _li(2, "Total assets", "bs_total_assets", 760, LineRole.TOTAL),
    ])
    doc.pages.append(PageSource(index=1, statement="profit_and_loss"))
    pl = _li(3, "Income tax expense", "pl_tax_expense__total_tax_expense", -100,
             LineRole.SUBTOTAL, page=1)
    doc.line_items.append(pl)
    _run(doc, _ontology(raw_ontology))

    assert doc.line_items[1].canonical_key == "bs_non_current_assets__others"


def test_a_row_printed_inside_another_section_is_ineligible_while_the_list_says_so(raw_ontology):
    """Eligibility 4: "The row was printed INSIDE this section … there is no cross-section rescue".

    The profit-attribution section prints no subtotal and has no residual of its own, so a row
    printed under it used to be handed to the nearest section that HAS one — the operating expenses.
    That is the cross-section rescue under another name: a share of profit inside the expense
    section, which still ties afterwards because the expense subtotal never mentioned it.
    """
    def statement() -> DocumentModel:
        return _doc("profit_and_loss", [
            _li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
                LineRole.SUBTOTAL),
            _li(1, "Profit attributable to owners of the parent",
                "pl_profit_attributable_to__owners_of_the_parent", 300),
            _li(2, "Minority share of results", None, 40),
        ])

    doc = statement()
    _run(doc, _ontology(raw_ontology))
    assert doc.line_items[2].canonical_key is None
    assert ("residual_ineligible:printed in another section(pl_s6_profit_attributable_to)"
            in doc.line_items[2].confidence.flags)

    raw = copy.deepcopy(raw_ontology)
    elig = raw["residual_framework"]["sweep"]["eligibility"]
    elig[3] = elig[3].replace("The row was printed INSIDE this section. ", "")
    # Eligibility 5 comes off with it, or it refuses the same row for its own reason: a positive
    # share of results contradicts the sign the expense section expects. Only one entry at a time can
    # be the thing under test.
    del elig[4]
    doc = statement()
    _run(doc, _ontology(raw))
    assert doc.line_items[2].canonical_key == "pl_expenses__others"


def test_cross_section_true_on_one_residual_is_what_enables_the_rescue(raw_ontology):
    raw = copy.deepcopy(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["residual_policy"]["cross_section"] = True
    doc = _doc("balance_sheet", [_li(0, "Deposits paid for acquisition of land", None, 60)])
    _run(doc, _ontology(raw))
    assert doc.line_items[0].canonical_key == "bs_current_liabilities__others"


# --- per-concept policy ------------------------------------------------------------------------

def test_a_residual_populated_by_something_other_than_the_sweep_is_left_alone(raw_ontology):
    raw = copy.deepcopy(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["residual_policy"]["population"] = "manual_only"
    doc = _current_liabilities("Other taxes payable")
    ctx = _run(doc, _ontology(raw))

    assert doc.line_items[1].canonical_key is None
    assert any("not_swept(bs_current_liabilities__others):population=manual_only" in line
               for line in ctx.logs)


def test_a_concept_that_declares_no_population_or_cross_section_inherits_the_framework(
        raw_ontology):
    """"One definition governing all 13" is what the framework's own values have to mean: where a
    concept's policy is silent, the framework's value is the one that governs.

    Every residual in this rulebook happens to spell all five terms out, so reading them off the
    policy object hands back the SCHEMA default for anything an author leaves out and no framework
    value ever reaches a concept — the block would describe the sweep without governing it.
    """
    raw = copy.deepcopy(raw_ontology)
    raw["residual_framework"]["population"] = "manual_only"
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            del m["residual_policy"]["population"]
    doc = _current_liabilities("Other taxes payable")
    ctx = _run(doc, _ontology(raw))

    assert doc.line_items[1].canonical_key is None
    assert any("not_swept(bs_current_liabilities__others):population=manual_only" in line
               for line in ctx.logs)

    # …and the same for the framework's own cross_section: with the concept silent, the framework
    # value is what enables the rescue this rulebook otherwise forbids everywhere.
    raw = copy.deepcopy(raw_ontology)
    raw["residual_framework"]["sweep"]["cross_section"] = True
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            del m["residual_policy"]["cross_section"]
    doc = _doc("balance_sheet", [_li(0, "Deposits paid for acquisition of land", None, 60)])
    _run(doc, _ontology(raw))
    assert doc.line_items[0].canonical_key == "bs_current_liabilities__others"


def test_a_residual_asking_to_be_plugged_is_reported_as_a_rulebook_conflict(raw_ontology):
    """``plug_behaviour: forbidden`` is the framework's word and it wins: the contradiction is
    reported, and the residual is still the sum of its components."""
    raw = copy.deepcopy(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["residual_policy"]["plug"] = True
    doc = _current_liabilities("Other taxes payable", unclaimed=25, subtotal=200)
    ctx = _run(doc, _ontology(raw))

    assert any("rulebook_conflict(bs_current_liabilities__others):plug_forbidden_by_framework"
               in line for line in ctx.logs)
    recon = _report(ctx, "bs_current_liabilities__others")["reconciliation"][0]
    assert recon["residual"] == 25.0 and recon["status"] == "unallocated_gap"


def test_itemise_false_is_honoured_and_reported_against_the_framework(raw_ontology):
    raw = copy.deepcopy(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["residual_policy"]["itemise"] = False
    doc = _current_liabilities("Other taxes payable")
    ctx = _run(doc, _ontology(raw))

    assert doc.line_items[1].canonical_key == "bs_current_liabilities__others"
    assert _report(ctx, "bs_current_liabilities__others")["components"] == []
    assert any("itemisation_required_by_framework" in line for line in ctx.logs)


def test_a_residual_declaring_a_derivation_is_reported_rather_than_derived(raw_ontology):
    """``sweep.derivation: forbidden``. A residual with a derivation is a residual computed from
    something other than the rows it swept — the plug under another name."""
    raw = copy.deepcopy(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["derivation"] = "reported subtotal less mapped children"
    doc = _current_liabilities("Other taxes payable", unclaimed=25, subtotal=200)
    ctx = _run(doc, _ontology(raw))

    assert any("derivation_forbidden_by_framework" in line for line in ctx.logs)
    assert _report(ctx, "bs_current_liabilities__others")["reconciliation"][0]["residual"] == 25.0


def test_a_row_captioned_others_is_one_ordinary_component(raw_ontology):
    """``literal_others_caption``: no privileged treatment and no dedicated concept — but it is
    marked, because a reviewer reading a residual made of a row that already said "Others" needs to
    know that is what happened."""
    doc = _current_liabilities("Others")
    _run(doc, _ontology(raw_ontology))
    row = doc.line_items[1]
    assert row.canonical_key == "bs_current_liabilities__others"
    assert row.source_label == "Others"
    assert "residual_literal_others_caption" in row.confidence.flags

    raw = copy.deepcopy(raw_ontology)
    sweep = raw["residual_framework"]["sweep"]
    sweep["literal_others_caption"] = sweep["literal_others_caption"].replace(
        "'Others'", "'Sundry'")
    doc = _current_liabilities("Others")
    _run(doc, _ontology(raw))
    assert "residual_literal_others_caption" not in doc.line_items[1].confidence.flags


def test_an_aggregation_the_stage_cannot_perform_is_said_out_loud(raw_ontology):
    """``sum_of_components`` is the only aggregation implemented. A rulebook asking for another one
    must not be quietly served sums as though they were what it asked for."""
    raw = copy.deepcopy(raw_ontology)
    raw["residual_framework"]["itemisation"]["aggregation"] = "largest_component"
    doc = _current_liabilities("Other taxes payable")
    ctx = _run(doc, _ontology(raw))
    assert any("unsupported_aggregation(largest_component)" in line for line in ctx.logs)


# --- review triggers ---------------------------------------------------------------------------

def test_the_component_count_trigger_fires_at_the_number_the_rulebook_quotes(raw_ontology):
    items = [_li(0, "Trade and bills payables",
                 "bs_current_liabilities__current_trade_payables", 100)]
    items += [_li(i + 1, f"Sundry liability {i}", None, 5) for i in range(4)]
    items.append(_li(9, "Total current liabilities",
                     "bs_current_liabilities__total_current_liabilities", 120,
                     LineRole.SUBTOTAL))
    doc = _doc("balance_sheet", items)
    _run(doc, _ontology(raw_ontology))
    assert not any(f.startswith("residual_review:component_count")
                   for f in doc.line_items[1].confidence.flags)

    raw = copy.deepcopy(raw_ontology)
    triggers = raw["residual_framework"]["review_triggers"]
    triggers[2] = "component count exceeds 3"
    items = [_li(0, "Trade and bills payables",
                 "bs_current_liabilities__current_trade_payables", 100)]
    items += [_li(i + 1, f"Sundry liability {i}", None, 5) for i in range(4)]
    items.append(_li(9, "Total current liabilities",
                     "bs_current_liabilities__total_current_liabilities", 120,
                     LineRole.SUBTOTAL))
    doc = _doc("balance_sheet", items)
    _run(doc, _ontology(raw))
    assert "residual_review:component_count>3" in doc.line_items[1].confidence.flags


def test_the_residual_share_trigger_stops_firing_when_the_rulebook_drops_it(raw_ontology):
    doc = _current_liabilities("Other taxes payable")
    _run(doc, _ontology(raw_ontology))
    assert "residual_review:residual_share_of_subtotal>5%" in doc.line_items[1].confidence.flags

    raw = copy.deepcopy(raw_ontology)
    raw["residual_framework"]["review_triggers"] = [
        t for t in raw["residual_framework"]["review_triggers"]
        if not t.startswith("|residual|")]
    doc = _current_liabilities("Other taxes payable")
    _run(doc, _ontology(raw))
    assert not any(f.startswith("residual_review:residual_share")
                   for f in doc.line_items[1].confidence.flags)


def test_a_component_a_dedicated_concept_was_vetoed_from_claiming_is_a_review_trigger(
        raw_ontology):
    """"Accumulated depreciation" scores against the depreciation charge, whose ``exclude_hints``
    veto "accumulated". The veto is right — this is a balance, not a charge — but the reviewer has to
    be told that a concept nearly claimed the row, or the residual looks like an ordinary unmatched
    caption."""
    doc = _doc("profit_and_loss", [
        _li(0, "Accumulated depreciation", None, -20),
        _li(1, "Total operating cost", "pl_expenses__total_operating_cost", -100,
            LineRole.SUBTOTAL),
    ])
    ctx = _run(doc, _ontology(raw_ontology))
    row = doc.line_items[0]
    assert row.canonical_key == "pl_expenses__others"
    assert "residual_review:vetoed_dedicated_match" in row.confidence.flags
    rejected = _report(ctx, "pl_expenses__others")["components"][0]["rejected_candidates"]
    assert rejected and rejected[0]["canonical_key"] == (
        "pl_expenses__depreciation_and_amortisation_expense")
    assert rejected[0]["reason"].startswith("exclude_hints:")


def test_a_residual_signed_against_its_section_is_a_review_trigger(raw_ontology):
    """The section's ``sign_convention`` is the one that counts: a residual declares "either" on
    itself because its own sign is indeterminate, so reading the concept would make this
    unfireable.

    Reached through a row signed one way in the current column and the other way in the prior,
    because that is the case eligibility 5 leaves for it. Eligibility 5 refuses a row whose signs
    are UNANIMOUSLY against the section before it can be swept — that is the corruption it exists
    to stop — and a movement that legitimately turns over between periods is not that row. Its prior
    column still leaves the bucket signed against the section, which is what this trigger is for:
    the row was admissible, the resulting residual is still worth a look.
    """
    row = LineItem(source_label="Other taxes payable", ordinal=1, role=LineRole.LINE)
    for period, value in (("current", 25), ("prior", -25)):
        row.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label=period, provenance=Provenance(page_index=0)))
    doc = _doc("balance_sheet", [
        _li(0, "Trade and bills payables", "bs_current_liabilities__current_trade_payables", 100),
        row,
        _li(2, "Total current liabilities",
            "bs_current_liabilities__total_current_liabilities", 125, LineRole.SUBTOTAL),
    ])
    _run(doc, _ontology(raw_ontology))

    assert doc.line_items[1].canonical_key == "bs_current_liabilities__others"   # admitted
    assert ("residual_review:sign_opposite_to_section:positive_expected"
            in doc.line_items[1].confidence.flags)


# --- notes as a source -------------------------------------------------------------------------

def _expenses_doc_with_note() -> DocumentModel:
    """The expense section as HKEX filings print it: one face subtotal citing the expenses note,
    which splits it by nature — including an auditor's-remuneration line no concept covers."""
    doc = _doc("profit_and_loss", [
        _li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
            LineRole.SUBTOTAL, note="8"),
        _li(1, "Other expenses", "pl_expenses__other_expenses", -60, note="8"),
        _li(2, "Staff costs", "pl_expenses__employee_benefits_expense", -10, note="8"),
    ])
    table = NotesTable(note_number="8", title="Operating expenses")
    for label, value in (("Auditor's remuneration", -30), ("Total", -100)):
        item = NoteItem(raw_label=label, ordinal=0,
                        role=LineRole.TOTAL if label == "Total" else LineRole.LINE)
        item.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label="current", provenance=Provenance(page_index=0)))
        table.items.append(item)
    doc.notes = [table]
    return doc


def _note_sourcing_granted(raw_ontology) -> dict:
    """The rulebook edited to permit ONE residual to source from a note it cites.

    The shipped rulebook permits this nowhere: ``sweep.notes_as_source`` is false, and the only
    section whose ``note_use`` allows decomposition (the tax charge) lost its residual when the tax
    bucket was removed — a single line in the tax charge is now inferred, not swept. So every test
    below grants the permission it is about, and this function is the record of the two terms a
    rulebook has to state to turn note sourcing on.
    """
    raw = copy.deepcopy(raw_ontology)
    raw["section_defaults"]["pl_s2_expenses"]["note_use"] = "decomposition_allowed"
    for m in raw["mappings"]:
        if m["canonical_key"] == "pl_expenses__others":
            m["residual_policy"]["notes_as_source"] = True
    return raw


def test_only_a_section_whose_note_use_permits_it_may_source_from_a_note(raw_ontology):
    # As shipped: no section permits it, so the note row is left where it was printed.
    doc = _expenses_doc_with_note()
    _run(doc, _ontology(raw_ontology))
    assert not [li for li in doc.line_items if li.canonical_key == "pl_expenses__others"]

    doc = _expenses_doc_with_note()
    ctx = _run(doc, _ontology(_note_sourcing_granted(raw_ontology)))

    sourced = [li for li in doc.line_items if li.canonical_key == "pl_expenses__others"]
    assert [li.source_label for li in sourced] == ["Auditor's remuneration"]
    assert "residual_note_sourced:8" in sourced[0].confidence.flags
    # The note's own total is not a component — that would count the whole charge twice.
    entry = _report(ctx, "pl_expenses__others")
    assert [c["source"] for c in entry["components"]] == ["note:8"]
    assert entry["reconciliation"][0]["status"] == "tied"


def test_the_frameworks_notes_as_source_is_what_a_silent_concept_inherits(raw_ontology):
    """``sweep.notes_as_source`` is inert only while no concept declares it. With the section
    permitting a note and the concept silent about it, the framework value is what decides — and it
    decides both ways."""
    def silent(value: bool) -> dict:
        raw = _note_sourcing_granted(raw_ontology)
        raw["residual_framework"]["sweep"]["notes_as_source"] = value
        for m in raw["mappings"]:
            if m["canonical_key"] == "pl_expenses__others":
                del m["residual_policy"]["notes_as_source"]
        return raw

    doc = _expenses_doc_with_note()
    _run(doc, _ontology(silent(False)))
    assert not [li for li in doc.line_items if li.canonical_key == "pl_expenses__others"]

    doc = _expenses_doc_with_note()
    _run(doc, _ontology(silent(True)))
    assert [li.source_label for li in doc.line_items
            if li.canonical_key == "pl_expenses__others"] == ["Auditor's remuneration"]


def test_note_use_evidence_only_closes_the_note_as_a_source(raw_ontology):
    raw = _note_sourcing_granted(raw_ontology)
    raw["section_defaults"]["pl_s2_expenses"]["note_use"] = "evidence_only"
    doc = _expenses_doc_with_note()
    ctx = _run(doc, _ontology(raw))

    assert not [li for li in doc.line_items if li.canonical_key == "pl_expenses__others"]
    # A residual asking for the note while its section forbids it is a contradiction, reported.
    assert any("notes_as_source_without_note_use" in line for line in ctx.logs)


def test_a_residual_that_does_not_ask_for_the_note_never_gets_it(raw_ontology):
    raw = _note_sourcing_granted(raw_ontology)
    for m in raw["mappings"]:
        if m["canonical_key"] == "pl_expenses__others":
            m["residual_policy"]["notes_as_source"] = False
    doc = _expenses_doc_with_note()
    _run(doc, _ontology(raw))
    assert not [li for li in doc.line_items if li.canonical_key == "pl_expenses__others"]


def test_face_only_false_also_opens_the_note(raw_ontology):
    """``face_only`` and ``note_use`` are two statements of one policy — "notes are evidence for a
    face amount, never an independent source of one" — and either one may lift it."""
    raw = _note_sourcing_granted(raw_ontology)
    raw["section_defaults"]["pl_s2_expenses"]["note_use"] = "evidence_only"
    raw["section_defaults"]["pl_s2_expenses"]["face_only"] = False
    doc = _expenses_doc_with_note()
    _run(doc, _ontology(raw))
    assert [li.source_label for li in doc.line_items
            if li.canonical_key == "pl_expenses__others"] == ["Auditor's remuneration"]


# --- when the sweep may run --------------------------------------------------------------------

def test_the_sweep_does_not_run_before_the_dedicated_concepts_are_resolved(raw_ontology):
    """``sweep.runs`` says after resolution, and prohibition 4 says why: a row no concept was ever
    asked about is not an unclaimed row. Sweeping first would fill Others with the whole face and
    every section would still tie."""
    doc = _current_liabilities("Other taxes payable")
    for li in doc.line_items:
        li.confidence.method = None
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = _ontology(raw_ontology)
    ResidualStage().run(doc, ctx)

    assert doc.line_items[1].canonical_key is None
    assert any("no mapping has run" in line for line in ctx.logs)


# --- what a real HKEX filing broke ---------------------------------------------------------------

def test_the_bare_sub_captions_of_a_per_share_block_are_per_share_figures(raw_ontology):
    """"LOSS PER SHARE / Basic / Diluted" — only the heading says "per share", and it carries no value.

    THE DEFECT THIS CLOSES, reported off a real HKEX filing: loss per share inside Total tax expense.
    An income statement prints the per-share block as a heading over two rows captioned only "Basic"
    and "Diluted", and those are the rows carrying the figures. The heading is dropped as a header,
    the sub-captions matched nothing, and the sweep put a figure in CENTS into the tax charge — too
    small for any rollup to notice and enough to make profit for the year wrong.

    Matched only under the heading. "Basic" on its own is far too generic a caption to veto a row on.
    """
    doc = _doc("profit_and_loss", [
        _li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
            LineRole.SUBTOTAL),
        _li(1, "LOSS PER SHARE", None, None, LineRole.HEADER),
        _li(2, "Basic", None, -12),
        _li(3, "Diluted", None, -12),
    ])
    _run(doc, _ontology(raw_ontology))

    for row in doc.line_items[2:]:
        assert row.canonical_key is None, row.source_label
        assert "residual_ineligible:per-share figure" in row.confidence.flags

    # …and the same captions with no per-share heading above them are NOT vetoed by this rule: the
    # block ends at the first row that is neither the heading nor one of its sub-captions.
    plain = _doc("profit_and_loss", [
        _li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
            LineRole.SUBTOTAL),
        _li(1, "Basic", None, -12),
    ])
    _run(plain, _ontology(raw_ontology))
    assert "residual_ineligible:per-share figure" not in plain.line_items[1].confidence.flags
    assert plain.line_items[1].canonical_key == "pl_expenses__others"   # it really would be swept


def test_a_bilingual_per_share_sub_caption_is_recognised_in_either_language(raw_ontology):
    """The captions the real filing prints, verbatim: "– Basic －基本" and "– Diluted －攤薄".

    An HKEX statement prints both languages on ONE row, so the sub-caption is never the bare English
    word the pattern was first written for. Matching English only left both rows unrecognised and
    swept — which is the defect the test above describes, still live for every filing that prints a
    Chinese column. Simplified and Traditional both appear in the wild, and a mainland filing prints
    the Chinese alone, so each half has to stand on its own.
    """
    captions = ["– Basic －基本", "– Diluted －攤薄", "基本", "－稀释", "Diluted (restated)"]
    items = [_li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
                 LineRole.SUBTOTAL),
             _li(1, "LOSS PER SHARE 每股虧損", None, None, LineRole.HEADER)]
    items += [_li(i + 2, caption, None, -12) for i, caption in enumerate(captions)]
    doc = _doc("profit_and_loss", items)
    _run(doc, _ontology(raw_ontology))

    for row in doc.line_items[2:]:
        assert row.canonical_key is None, row.source_label
        assert "residual_ineligible:per-share figure" in row.confidence.flags, row.source_label

    # A caption that merely CONTAINS one of those words is an ordinary row and is swept as one: the
    # pattern anchors on the whole caption, or "Basic salary" would go to review on every filing.
    ordinary = _doc("profit_and_loss", [
        _li(0, "Total operating cost", "pl_expenses__total_operating_cost", -100,
            LineRole.SUBTOTAL),
        _li(1, "LOSS PER SHARE 每股虧損", None, None, LineRole.HEADER),
        _li(2, "Basic salary of directors", None, -12),
    ])
    _run(ordinary, _ontology(raw_ontology))
    assert ordinary.line_items[2].canonical_key == "pl_expenses__others"


def test_a_banner_the_statement_has_already_closed_does_not_place_a_row(raw_ontology):
    """A stale sticky banner beat the accounting structure, and the structure was never consulted.

    THE DEFECT THIS CLOSES, reported off a real HKEX filing: "a lot of current assets moved to others
    of non-current assets". ``section_hint`` is STICKY — ``row_reconstruct`` carries the last
    recognised heading down the page — so when the next heading is not recognised (printed with a
    figure on the same line, or as a running header on a continuation page) every row below it wears
    the previous section's banner. Signal 1 answered from that banner and returned FIRST, so signal 2
    — the next section subtotal below, which is the statement's own structure — never ran.

    A section's own subtotal is the end of it: once ``Total non-current assets`` is above this row,
    no banner can put the row back inside that section.
    """
    rows = [
        _li(0, "Property, plant and equipment",
            "bs_non_current_assets__property_plant_and_equipment", 500),
        _li(1, "Total non-current assets",
            "bs_non_current_assets__total_non_current_assets", 500, LineRole.SUBTOTAL),
        # Printed in current assets, still wearing the non-current banner.
        _li(2, "Deposits paid to suppliers", None, 40),
        _li(3, "Total current assets", "bs_current_assets__total_current_assets", 40,
            LineRole.SUBTOTAL),
    ]
    for row in rows:
        row.section_hint = "NON-CURRENT ASSETS"
    doc = _doc("balance_sheet", rows)
    _run(doc, _ontology(raw_ontology))

    assert doc.line_items[2].canonical_key == "bs_current_assets__others"

    # The same banner still places a row printed BEFORE its subtotal — it is only spent afterwards.
    rows = [
        _li(0, "Deposits paid to suppliers", None, 40),
        _li(1, "Total non-current assets",
            "bs_non_current_assets__total_non_current_assets", 40, LineRole.SUBTOTAL),
    ]
    for row in rows:
        row.section_hint = "NON-CURRENT ASSETS"
    doc = _doc("balance_sheet", rows)
    _run(doc, _ontology(raw_ontology))
    assert doc.line_items[0].canonical_key == "bs_non_current_assets__others"


def test_a_row_signed_against_its_section_goes_to_review_not_into_the_bucket(raw_ontology):
    """Eligibility 5, and the number it exists to stop appearing on a statement.

    THE DEFECT THIS CLOSES, measured off a real HKEX filing: a stale banner put cost of sales in the
    INCOME section, whose sign_convention is positive_expected, and an 814,645 COST swept into "Other
    income items". Total income came out at 32,097 against revenue of 868,375, and every total below
    it inherited the error.

    Review trigger 5 could not prevent it — that fires on the finished bucket, by which point the
    figure is in the statement and the analyst is reading it. A row whose sign contradicts the section
    it would be swept into is evidence the SECTION is wrong, not the amount, so it goes to review.
    """
    doc = _doc("profit_and_loss", [
        _li(0, "Revenue", "pl_income__revenue_from_operations", 868_375),
        _li(1, "Cost of sales", None, -814_645),          # a cost, in the income section
        _li(2, "Total income", "pl_income__total_income", 868_375, LineRole.SUBTOTAL),
    ])
    for row in doc.line_items:
        row.section_hint = "REVENUE"
    _run(doc, _ontology(raw_ontology))

    swept = doc.line_items[1]
    assert swept.canonical_key is None
    assert any(f.startswith("residual_sign_contradicts_section") for f in swept.confidence.flags)

    # A row whose sign AGREES is still swept: this refuses the contradiction, not the sweep.
    doc = _doc("profit_and_loss", [
        _li(0, "Revenue", "pl_income__revenue_from_operations", 868_375),
        _li(1, "Sundry income", None, 5_000),
        _li(2, "Total income", "pl_income__total_income", 873_375, LineRole.SUBTOTAL),
    ])
    for row in doc.line_items:
        row.section_hint = "REVENUE"
    _run(doc, _ontology(raw_ontology))
    assert doc.line_items[1].canonical_key == "pl_income__others"


# --- one section, one bucket ---------------------------------------------------------------------

def _two_buckets_on_one_section(raw: dict, first: str) -> dict:
    """``bs_current_liabilities__others`` re-scoped onto the non-current section, which already has
    a bucket of its own — a single-scope concept each, so prohibition 5 has nothing to refuse, and
    two buckets claim one section. ``first`` is the key placed EARLIER in the ``mappings`` array."""
    out = copy.deepcopy(raw)
    for m in out["mappings"]:
        if m["canonical_key"] == "bs_current_liabilities__others":
            m["inherits"] = "bs_s4_non_current_liabilities"
            m["section_scope"] = ["bs_s4_non_current_liabilities"]
            m["residual_policy"]["section_scope"] = "bs_s4_non_current_liabilities"
    keys = [m["canonical_key"] for m in out["mappings"]]
    a, b = keys.index("bs_current_liabilities__others"), keys.index(
        "bs_non_current_liabilities__others")
    lo, hi = min(a, b), max(a, b)
    want_lo = first
    if out["mappings"][lo]["canonical_key"] != want_lo:
        out["mappings"][lo], out["mappings"][hi] = out["mappings"][hi], out["mappings"][lo]
    return out


@pytest.mark.parametrize("first", ["bs_current_liabilities__others",
                                  "bs_non_current_liabilities__others"])
def test_two_buckets_claiming_one_section_are_both_refused_whichever_is_listed_first(
        raw_ontology, first):
    """THE DEFECT THIS CLOSES: the order of the ``mappings`` array was load-bearing, and nothing said so.

    ``by_section`` was built as ``{r.section: r for r in usable}``. Two residuals claiming one
    section therefore resolved by POSITION IN THE FILE — the later one won, the earlier one vanished
    with nothing logged — so re-ordering the array moved a whole section's unclaimed rows into a
    different bucket, on a different statement, with both sections still tying afterwards. The array
    order is presentation: the workbook's Concepts sheet is generated from it and a reviewer sorting
    it into the order a statement reads must not be changing extraction.

    Two buckets on one section is the contradiction prohibition 5 already forbids within a single
    concept. Neither sweeps, both record who contested them, and — the assertion this test exists for
    — the outcome is identical under both orderings.
    """
    doc = _doc("balance_sheet", [
        _li(0, "Interest-bearing bank borrowings",
            "bs_non_current_liabilities__non_current_borrowings", 100),
        _li(1, "Other long-term obligations", None, 25),
        _li(2, "Total non-current liabilities",
            "bs_non_current_liabilities__total_non_current_liabilities", 125, LineRole.SUBTOTAL),
    ])
    ctx = _run(doc, _ontology(_two_buckets_on_one_section(raw_ontology, first)))

    # No bucket sweeps the contested section, so the unclaimed row stays unclaimed and visible
    # rather than being filed by whichever concept happened to be listed second.
    assert doc.line_items[1].canonical_key is None
    contested = [m for m in ctx.logs if "section_contested_by:" in m]
    assert len(contested) == 2, ctx.logs
    assert {"bs_current_liabilities__others", "bs_non_current_liabilities__others"} == {
        m.split("residual:rulebook_conflict(")[1].split(")")[0] for m in contested}


def test_one_bucket_per_section_is_untouched_by_the_contest_guard(raw_ontology):
    """The other side, so the guard above is shown to cost nothing on a well-formed rulebook: the
    shipped file gives each section exactly one bucket, and the sweep runs as it always did."""
    doc = _current_liabilities("Other taxes payable")
    ctx = _run(doc, _ontology(raw_ontology))
    assert doc.line_items[1].canonical_key == "bs_current_liabilities__others"
    assert not [m for m in ctx.logs if "section_contested_by:" in m]
