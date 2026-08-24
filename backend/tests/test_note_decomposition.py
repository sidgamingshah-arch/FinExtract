"""Splitting a combined caption into the components a disclosure itemises — within one section only.

THE RULE, as the product owner stated it: "If there are line items that need to be split within the
same section it should be allowed. Across two sections can be left as is."

The distinction is not arbitrary. A filing that prints one line for "Prepayments, other receivables
and other assets" and itemises it in the note it cites is REPORTING those components; publishing
them is reading, not inference. Deciding how much of one printed amount falls on each side of a
boundary the page never drew — the twelve-month cut between current and non-current borrowings — IS
inference, and the maturity table that would settle it is not arithmetic this stage reads.

So: components in the aggregate's own section are published and the aggregate becomes a subtotal;
components anywhere else leave the aggregate exactly as printed.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat, LineRole, PrintedIn
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
_AGGREGATE = "bs_current_assets__prepayments_other_receivables_and_other_assets"


@pytest.fixture(scope="module")
def rulebook():
    return load_ontology(json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text()),
                         resolve=True)


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text()))


def _run(data: bytes, rulebook, template):
    ctx = PipelineContext(raw_bytes=data)
    ctx.ontology, ctx.template = rulebook, template
    return default_pipeline().run(DocumentModel(filename="f.pdf", fmt=DocFormat.PDF), ctx), ctx


@pytest.fixture(scope="module")
def split(rulebook, template):
    from tests.fixtures.generate import make_decomposed_note_pdf

    return _run(make_decomposed_note_pdf(), rulebook, template)


@pytest.fixture(scope="module")
def refused(rulebook, template):
    from tests.fixtures.generate import make_decomposed_note_pdf

    return _run(make_decomposed_note_pdf(cross_section=True), rulebook, template)


# --- the gate, on the shipped rulebook ----------------------------------------------------------

def test_the_gate_admits_only_containments_whose_children_share_the_parents_section(rulebook):
    """The rule as data. Both of the rulebook's declaration blocks are read
    (``is_gross_parent``/``children_if_decomposed`` and
    ``global_rules.mutually_exclusive_groups``), and a containment is splittable only when every
    declared child resolves to the parent's own single section."""
    from app.stages.map_ontology import _pairs_to_keep_apart, _same_section_decompositions

    declared = {a for a, _c, _w in _pairs_to_keep_apart(rulebook)}
    admitted = {a for a, _c, _s in _same_section_decompositions(rulebook)}
    assert admitted < declared, "the gate must exclude something, or it is not a gate"

    # The two the shipped rulebook declares across sections: their children sit in the
    # non-operating and exceptional-item sections, not in income and expenses.
    assert "pl_income__other_income" in declared - admitted
    assert "pl_expenses__other_expenses" in declared - admitted
    # …and the balance-sheet aggregates whose children are all in their own section.
    for key in (_AGGREGATE, "bs_current_assets__cash_and_cash_equivalents",
                "bs_current_liabilities__other_payables_and_accruals", "bs_equity__reserves"):
        assert key in admitted, key


def test_every_admitted_child_really_resolves_to_the_parents_section(rulebook):
    """Asserted over the whole rulebook rather than on the examples above, so a future edit that
    moves one child into another section cannot pass this file."""
    from app.stages.map_ontology import _same_section_decompositions

    scope = {m.canonical_key: tuple(m.section_scope or ()) for m in rulebook.mappings}
    for aggregate, children, section in _same_section_decompositions(rulebook):
        assert scope.get(aggregate) == (section,), aggregate
        for child in children:
            assert scope.get(child) == (section,), f"{child} is not in {section}"


# --- the split ----------------------------------------------------------------------------------

def test_the_components_are_published_from_the_disclosure(split):
    doc, _ctx = split
    got = {li.canonical_key: {ev.period_label: ev.value for ev in li.values.values()}
           for li in doc.line_items if li.canonical_key}

    assert got["bs_current_assets__trade_receivables"] == {"current": Decimal("3410"),
                                                           "prior": Decimal("3000")}
    assert got["bs_current_assets__due_from_related_parties"] == {"current": Decimal("1000"),
                                                                  "prior": Decimal("900")}
    assert got["bs_current_assets__prepaid_income_tax"] == {"current": Decimal("590"),
                                                            "prior": Decimal("500")}


def test_the_aggregate_survives_as_an_auditable_subtotal(split):
    """Not deleted. The filing printed 5,000 and a reader has to be able to see it, and see that the
    components below account for it — which is exactly how the containment pass treats a parent
    whose children were printed on the face."""
    doc, _ctx = split
    parent = next(li for li in doc.line_items
                  if li.source_label.startswith("Prepayments, other receivables"))

    assert parent.canonical_key is None, "the aggregate is still filed, so its money is counted twice"
    assert parent.role is LineRole.SUBTOTAL
    assert {ev.value for ev in parent.values.values()} == {Decimal("5000"), Decimal("4400")}
    assert any(f.startswith("decomposed_into:") for f in parent.confidence.flags)


def test_nothing_is_counted_twice(split):
    """The property the whole design turns on: the components replace the aggregate, they do not
    join it. Summed over the section, the published figures equal what the face printed."""
    doc, _ctx = split
    section = {"bs_current_assets__trade_receivables",
               "bs_current_assets__due_from_related_parties",
               "bs_current_assets__prepaid_income_tax"}
    total = sum(ev.value for li in doc.line_items if li.canonical_key in section
                for ev in li.values.values() if ev.period_label == "current")
    assert total == Decimal("5000")          # the printed aggregate, exactly


def test_each_component_points_at_the_row_it_was_read_from(split):
    """Click-to-source has to land on the itemised line in the note, not on the combined caption and
    not on nothing: the published figure came from that row."""
    doc, _ctx = split
    child = next(li for li in doc.line_items
                 if li.canonical_key == "bs_current_assets__trade_receivables")
    prov = [ev.provenance for ev in child.values.values()]

    assert all(p is not None for p in prov)
    assert {p.page_index for p in prov} == {1}, "the note page, where the component is printed"
    assert all(p.bbox is not None for p in prov)


def test_a_component_says_it_came_from_a_split(split):
    """A row the filing never printed under this caption must never be mistakable for one it did."""
    doc, _ctx = split
    child = next(li for li in doc.line_items
                 if li.canonical_key == "bs_current_assets__prepaid_income_tax")
    assert f"split_from:{_AGGREGATE}" in child.confidence.flags


def test_the_components_reach_the_section_an_analyst_reads(split):
    """The reason the components are stamped FACE. Their figures were read off a NOTES page, and the
    segmentation excludes note rows from face membership — so without the stamp the section would
    end up with neither the components nor the aggregate they replaced."""
    doc, _ctx = split
    ids = {str(li.id) for li in doc.line_items
           if li.canonical_key and li.canonical_key.startswith("bs_current_assets__")}
    seg = next(s for s in doc.buckets.segments if s.bucket == "current_assets")

    assert ids <= set(seg.face_item_ids), "a split component is missing from its own section"
    for li in doc.line_items:
        if f"split_from:{_AGGREGATE}" in li.confidence.flags:
            assert li.printed_in is PrintedIn.FACE


def test_one_concept_itemised_twice_is_summed_not_taken_once(split):
    """A note routinely splits one template concept across two disclosed rows — here prepaid tax
    under a mainland and an overseas heading. Taking either row alone would publish 400 or 190 where
    the filing means 590, and the aggregate would then not tie."""
    doc, _ctx = split
    child = next(li for li in doc.line_items
                 if li.canonical_key == "bs_current_assets__prepaid_income_tax")

    assert {ev.period_label: ev.value for ev in child.values.values()} == {
        "current": Decimal("590"), "prior": Decimal("500")}
    # Said on the row, because its provenance can only point at one of the two rows it came from.
    assert "split_summed_rows:2" in child.confidence.flags


def test_a_disclosures_own_total_row_is_refused_as_a_component():
    """``_DISCLOSURE_TOTAL`` tested directly, and the docstring says why it is not tested through
    the pipeline: on the deterministic path a total caption maps to NOTHING ("Total trade
    receivables" resolves to None, there being no string-similarity tier), so it never reaches the
    sum and the guard cannot be shown to bite. It bites on the LLM path, where a semantic matcher
    reading "Total trade receivables" against a trade-receivables definition has every reason to
    accept it — and then the concept is counted twice, the sum doubles, and a legitimate split is
    refused by an arithmetic failure that came from the note's own layout.

    Asserting it here rather than pretending a fixture covers it: a test that passes with the guard
    deleted is not evidence, and this one at least fails if the pattern stops matching.

    ``role`` cannot do this job. Every NoteItem in production carries ``LineRole.LINE`` — all three
    readers hardcode it — which is also why reconcile's identical guard never fires."""
    from app.stages.map_ontology import _DISCLOSURE_TOTAL

    for caption in ("Total", "Total trade receivables", "Sub-total", "Subtotal", "  TOTAL  ",
                    "合計", "总计"):
        assert _DISCLOSURE_TOTAL.match(caption), caption
    for caption in ("Trade receivables", "Prepaid income tax", "Due from related parties",
                    "Net investment in leases", "Net book value", "Notes receivable"):
        assert not _DISCLOSURE_TOTAL.match(caption), caption

    # THE ACCEPTED COST, asserted so it is a decision on the record rather than a surprise: a real
    # instrument whose caption begins with "Total" is excluded too. The components then fall short of
    # the aggregate, the arithmetic gate refuses, and the aggregate stands exactly as printed — a
    # declined split, never a wrong figure. Distinguishing the two lexically is not possible; both
    # failure directions here end in a decline, which is what makes the prefix the right trade.
    assert _DISCLOSURE_TOTAL.match("Total return swap receivable")


# --- what it refuses ----------------------------------------------------------------------------

def test_a_component_in_another_section_leaves_the_aggregate_standing(refused):
    """The user's rule, end to end: one component of the same note is a NON-CURRENT asset, so this
    aggregate is not split. The aggregate keeps its concept and its printed figures."""
    doc, ctx = refused
    parent = next(li for li in doc.line_items
                  if li.source_label.startswith("Prepayments, other receivables"))

    assert parent.canonical_key == _AGGREGATE
    assert parent.role is LineRole.LINE
    assert {ev.value for ev in parent.values.values()} == {Decimal("5000"), Decimal("4400")}
    assert any("split_declined" in m for m in ctx.logs), ctx.logs


def test_the_out_of_section_component_is_not_published_at_all(refused):
    """Not published under the section it does not belong to, and not published under its own
    either: a caption matched with the aggregate's section as the candidate scope cannot resolve to
    a concept outside it. The section test and the section-scoped match are one rule twice."""
    doc, _ctx = refused
    keys = {li.canonical_key for li in doc.line_items if li.canonical_key}
    assert "bs_non_current_assets__property_plant_and_equipment" not in keys


def test_the_refusal_is_logged_with_its_reason(refused):
    """A split that silently does not happen is indistinguishable from a filing that had nothing to
    split, and the two need different actions from whoever reads the run."""
    _doc, ctx = refused
    line = next(m for m in ctx.logs if "split_declined" in m)
    assert _AGGREGATE in line
    assert "account for" in line or "itemises" in line
