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
    return load_ontology(json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")),
                         resolve=True)


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))


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


@pytest.fixture(scope="module")
def partial_split(rulebook, template):
    from tests.fixtures.generate import make_decomposed_note_pdf

    return _run(make_decomposed_note_pdf(unmapped_residual=True), rulebook, template)


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


def test_mapped_note_items_take_priority_and_unmapped_note_values_keep_the_face_key(
        partial_split):
    doc, _ctx = partial_split
    rows = [li for li in doc.line_items if li.canonical_key]

    assert any(li.canonical_key == "bs_current_assets__trade_receivables" for li in rows)
    assert any(li.canonical_key == "bs_current_assets__due_from_related_parties" for li in rows)
    residual = next(li for li in rows if li.canonical_key == _AGGREGATE)
    assert residual.source_label == "Unclassified note balance"
    assert "note_residual_to_face_item" in residual.confidence.flags
    assert {ev.period_label: ev.value for ev in residual.values.values()} == {
        "current": Decimal("590"), "prior": Decimal("500")}

    parent = next(li for li in doc.line_items
                  if li.source_label.startswith("Prepayments, other receivables"))
    assert parent.canonical_key is None
    decomposition = {
        "bs_current_assets__trade_receivables",
        "bs_current_assets__due_from_related_parties",
        _AGGREGATE,
    }
    current = sum(ev.value for li in rows if li.canonical_key in decomposition
                  for ev in li.values.values()
                  if ev.period_label == "current")
    assert current == Decimal("5000")


def test_note_rows_persist_the_template_or_face_key_they_support(partial_split):
    doc, _ctx = partial_split
    table = next(note for note in doc.notes if note.note_number == "18")
    by_label = {item.raw_label: item.canonical_key for item in table.items
                if item.role is LineRole.LINE}

    assert by_label["Trade receivables"] == "bs_current_assets__trade_receivables"
    assert by_label["Due from related parties"] == (
        "bs_current_assets__due_from_related_parties")
    assert by_label["Unclassified note balance"] == _AGGREGATE

    from app.api.routes.extractions import _serialize_notes
    serialized = [row for note in _serialize_notes(doc) if note["no"] == "18"
                  for row in note["rows"] if row["role"] == "line"]
    serialized_by_label = {row["label"]: row for row in serialized}
    assert serialized_by_label["Trade receivables"]["canonical_key"] == (
        "bs_current_assets__trade_receivables")
    assert serialized_by_label["Unclassified note balance"]["supports_face_key"] == _AGGREGATE


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


def test_a_disclosures_own_total_row_is_refused_as_a_component(split):
    """A note's own total is not one of its details, decided ONCE by the row's role.

    THIS USED TO BE A UNIT TEST OF A REGEX, and its docstring said the guard could not be shown to
    bite through the pipeline. That was wrong in a way worth recording: it could not be shown
    through the DISCLOSURE SPLIT (on the deterministic path "Total trade receivables" maps to
    nothing, so it never reaches the sum), but the identical guard in ``stages.reconcile`` bites on
    every note that prints its own total — which is most of them — and it was never firing, because
    every ``NoteItem`` was built ``LineRole.LINE``. A note's printed total was therefore summed
    alongside the rows it totals, so a note that ties came out at ``residual = face - 2 x total``.

    Measured on a real 270-page bilingual HKEX filing: 453 note rows, and of 109 reconciliation
    entries NOT ONE graded ``tied`` — with two of them served to the analyst as "does not tie"
    assertions that were false. With the role set, 14 tie at residual exactly 0.

    So the assertion is now made where it counts: through the pipeline, on the fixture that prints
    a note total, at residual 0.
    """
    from app.services.notes_extract import note_row_role

    doc, _ctx = split
    note = next(n for n in doc.notes if n.note_number == "18")
    by_role = {}
    for it in note.items:
        by_role.setdefault(str(it.role), []).append(it.raw_label)
    # The four itemised rows are details; the printed "Total" is not.
    assert by_role["LineRole.TOTAL"] == ["Total"], by_role
    assert len(by_role["LineRole.LINE"]) == 4, by_role

    # THE ASSERTION THAT FAILS WITH THE DEFECT RESTORED. The note itemises exactly the aggregate,
    # so it ties; with its own total counted as a detail the note total doubles and the residual is
    # -5,000 rather than 0.
    entries = {(e.face_key, e.period_label): e
               for e in doc.reconciliation.entries if e.note_number == "18"}
    face = "Prepayments, other receivables and other assets"
    for period, raw in (("current", 5000), ("prior", 4400)):
        entry = entries[(face, period)]
        assert entry.tie_status == "tied", (period, entry.tie_status, entry.residual)
        assert entry.residual == 0 and entry.raw_face == raw

    # …and the caption test itself, in both languages the rulebook supports.
    for caption in ("Total", "Total trade receivables", "  TOTAL  ", "合計", "总计", "總計"):
        assert note_row_role(caption) is LineRole.TOTAL, caption
    for caption in ("Sub-total", "Subtotal", "小計", "小计"):
        assert note_row_role(caption) is LineRole.SUBTOTAL, caption
    for caption in ("Trade receivables", "Prepaid income tax", "Due from related parties",
                    "Net investment in leases", "Net book value", "Notes receivable",
                    # The un-anchored variant in ``row_reconstruct._TOTAL_LABEL`` matches these two
                    # by searching for 總額 anywhere; both are ordinary note rows, and adopting that
                    # pattern would delete real details from the note total.
                    "Share of the joint ventures' total comprehensive loss 應佔合營公司的全面虧損總額",
                    "Aggregate carrying amount of the Group's investments 本集團的投資賬面總額"):
        assert note_row_role(caption) is LineRole.LINE, caption

    # THE ACCEPTED COST, asserted so it is a decision on the record rather than a surprise: a real
    # instrument whose caption begins with "Total" is called a total too. It is then left out of the
    # note total, the components fall short of the aggregate, the arithmetic gate refuses and the
    # aggregate stands exactly as printed — a declined split, never a wrong figure. Distinguishing
    # the two lexically is not possible; both failure directions end in a decline, which is what
    # makes the prefix the right trade.
    assert note_row_role("Total return swap receivable") is LineRole.TOTAL


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
    assert any(reason in line for reason in ("account for", "itemises", "outside"))


# ================================================================================================
# REQUIREMENT 20: the face prints one line and only the note has the split
# ================================================================================================
#
# The pass above splits a combined caption whose containment the RULEBOOK declares
# (``is_gross_parent``/``mutually_exclusive_groups``). §20 is the other authority for the same act:
# the TEMPLATE declares a total's components, and the rulebook's ``note_use`` says whether they may
# be read out of the note. It fires automatically and the rulebook overrides it — and the rulebook
# every test in this module loads is ``hkfrs_hk_china_ontology.json`` (see the ``rulebook``
# fixture), whose section default is ``evidence_only`` ("Notes are evidence for a face amount, never
# an independent source of one"), with the tax section named as its one exception and the reason
# given. That default is NOT a property of the stage: on ``output_csv_hk_ontology.json``, the file
# that drives the output CSV, ``decomposition_allowed`` is the section default on 394 of 462
# concepts. So read every COUNT in this section as an hkfrs count — the ``rulebook`` fixture is the
# only rulebook the §20 tests below load (one later test builds its own output_csv matcher, and it
# asserts nothing about note_use).

@pytest.fixture(scope="module")
def tax_split(rulebook, template):
    from tests.fixtures.generate import make_tax_note_split_pdf

    return _run(make_tax_note_split_pdf(), rulebook, template)


def test_the_note_permitted_arm_admits_only_what_both_definitions_authorise(rulebook, template):
    """Neither half is inferred, and on the hkfrs pair that admits exactly one aggregate.

    A candidate needs BOTH: a template ``rollup`` naming its components, and a concept whose
    ``note_use`` permits a note to be the source. ``hkfrs_hk_china_ontology.json`` permits it on the
    tax section alone — its author's stated intent, not a limitation here — so this is also the
    assertion that the default really is a refusal.

    THE "EXACTLY ONE" IS THE FILE'S, NOT THE FUNCTION'S. This asserts one admitted pair because the
    fixture loads hkfrs with ``hkfrs_hk_china_template.json``; the same call on
    ``output_csv_hk_ontology.json`` + ``output_csv_hk_v1_template.json`` admits 52. So a change that
    made this function permissive on the output_csv pair would not move this assertion — it is a
    test of the hkfrs data as much as of the code.
    """
    from app.stages.map_ontology import _note_permitted_decompositions

    admitted = _note_permitted_decompositions(rulebook, template.model_dump(mode="json"))
    assert admitted == [("pl_tax_expense__total_tax_expense",
                         ["pl_tax_expense__current_tax", "pl_tax_expense__deferred_tax"],
                         "pl_s5_tax_expense")]

    # THE OVERRIDE, asserted rather than assumed: 180 of hkfrs's 183 concepts say ``evidence_only``
    # and are therefore refused, whatever the template declares about them.
    permitted = {m.canonical_key for m in rulebook.mappings
                 if m.note_use == "decomposition_allowed"}
    assert len(permitted) == 3, permitted
    refused = {m.canonical_key for m in rulebook.mappings if m.note_use == "evidence_only"}
    assert len(refused) > 100 and "bs_current_assets__cash_and_cash_equivalents" in refused


def test_the_face_total_is_replaced_by_the_components_the_note_prints(tax_split):
    """§20 end to end. The filing prints ONE tax line; the split lives only in note 11."""
    doc, _ctx = tax_split
    got = {li.canonical_key: {ev.period_label: ev.value for ev in li.values.values()}
           for li in doc.line_items if li.canonical_key}

    assert got["pl_tax_expense__current_tax"] == {"current": Decimal("1200"),
                                                  "prior": Decimal("1050")}
    # The deferred CREDIT stays negative: the note prints it in parentheses and the split is
    # therefore not merely additive, which is the case a sign bug would sail through.
    assert got["pl_tax_expense__deferred_tax"] == {"current": Decimal("-200"),
                                                   "prior": Decimal("-150")}


def test_the_printed_tax_total_is_kept_as_validation_evidence(tax_split):
    """The calculated total is served from its components; the printed value remains for validation."""
    doc, _ctx = tax_split
    parent = next(li for li in doc.line_items
                  if (li.source_label or "").startswith("Income tax expense"))

    assert parent.canonical_key == "pl_tax_expense__total_tax_expense"
    assert "reported_validation_for_calculated" in parent.confidence.flags
    assert {ev.value for ev in parent.values.values()} == {Decimal("1000"), Decimal("900")}
    # THE ASSERTION THAT MATTERS: the components come to what the face printed, in both columns.
    for period, printed in (("current", Decimal("1000")), ("prior", Decimal("900"))):
        total = sum(ev.value for li in doc.line_items
                    if li.canonical_key in ("pl_tax_expense__current_tax",
                                            "pl_tax_expense__deferred_tax")
                    for ev in li.values.values() if ev.period_label == period)
        assert total == printed, period


def test_a_component_read_from_the_note_says_so_and_points_at_the_note_row(tax_split):
    """A figure the face never printed under this caption must not be mistakable for one it did,
    and click-to-source has to land on the note row it was read from."""
    doc, _ctx = tax_split
    child = next(li for li in doc.line_items
                 if li.canonical_key == "pl_tax_expense__deferred_tax")

    assert any(f == "split_from:pl_tax_expense__total_tax_expense"
               for f in child.confidence.flags), child.confidence.flags
    prov = [ev.provenance for ev in child.values.values()]
    assert all(p is not None for p in prov)
    assert {p.page_index for p in prov} == {1}, "the note page, where the component is printed"


# --- the awkward filing: two tables, a qualifying sub-heading, and the opposite sign -------------
#
# Everything above uses a tidy note: one table, self-describing captions, and figures printed in the
# same orientation as the face. A real HKEX tax note has none of those, and each of the three
# departures on its own was enough to stop the split reading it. This fixture reproduces all three
# together, so the three fixes are pinned by the outcome and not only by their unit behaviour.

@pytest.fixture(scope="module")
def awkward_tax(rulebook, template):
    from tests.fixtures.generate import make_hkex_tax_note_pdf

    return _run(make_hkex_tax_note_pdf(), rulebook, template)


def test_the_awkward_note_still_decomposes_the_face_line(awkward_tax):
    """The outcome, in the face's own convention.

    Ground truth: current tax is three printed rows (600 + 600 + 400 = 1,600) and deferred tax is a
    credit of 600, printed in the note as positive charges; the face prints the total as ``(1,000)``.
    So the components must reach the statement as -1,600 and +600, which come to the -1,000 the page
    shows -- a deferred CREDIT is positive once the charge is negative, and getting that backwards
    is the bug this asserts against.
    """
    doc, _ctx = awkward_tax
    got = {li.canonical_key: {ev.period_label: ev.value for ev in li.values.values()}
           for li in doc.line_items if li.canonical_key}

    assert got["pl_tax_expense__current_tax"] == {"current": Decimal("-1600"),
                                                  "prior": Decimal("-1200")}
    assert got["pl_tax_expense__deferred_tax"] == {"current": Decimal("600"),
                                                   "prior": Decimal("300")}
    # And they tie into the rollup that motivated the orientation in the first place: the template
    # makes profit for the year the SUM of profit before tax and the tax line.
    assert got["pl_profit_before_tax"]["current"] == Decimal("5000")
    assert (got["pl_tax_expense__current_tax"]["current"]
            + got["pl_tax_expense__deferred_tax"]["current"]) == Decimal("-1000")


def test_only_the_table_that_accounts_for_the_total_is_read(awkward_tax):
    """D1: a cited note number is not one table, and the other one restates its components.

    Note 11 prints the components on one page and the effective-rate reconciliation on the next. The
    reconciliation lists "Land appreciation tax 600" -- the SAME 600 the components table already
    lists as "PRC land appreciation tax" -- and "Deferred tax (600)", the same credit again. Reading
    the union of both tables counts both twice: current tax becomes 2,200 instead of 1,600 and
    deferred 1,200 instead of 600.

    AND THE ARITHMETIC GATE CANNOT SEE IT, which is why the order of evaluation is the fix and not
    the gate. The two restatements are equal and opposite in every column, so the pooled components
    still come to the total the face printed; the total ties while both components are wrong. Only
    reading a single table that accounts for the aggregate on its own -- and never looking at the
    union once one does -- gets the components right.
    """
    doc, _ctx = awkward_tax
    child = next(li for li in doc.line_items
                 if li.canonical_key == "pl_tax_expense__current_tax")

    assert "split_summed_rows:3" in child.confidence.flags, child.confidence.flags
    assert {ev.value for ev in child.values.values()} == {Decimal("-1600"), Decimal("-1200")}
    assert Decimal("-2200") not in {ev.value for ev in child.values.values()}, \
        "the derivation table's restated component was counted as a second component"


def test_a_row_whose_caption_is_a_geography_is_named_by_its_sub_heading(awkward_tax):
    """D2: "Mainland China 400" is a tax figure only because of the line printed above it.

    The note prints "Under-provision in prior years, net:" and then breaks it down by geography. The
    caption alone names no concept -- and must not, since no alias should make a place into a tax --
    so the sub-heading it sits under is what carries the meaning to it.
    """
    doc, _ctx = awkward_tax
    rows = [it for t in doc.notes if str(t.note_number) == "11" for it in t.items]
    geography = next(it for it in rows if (it.raw_label or "").startswith("Mainland China"))

    assert geography.group_hint.startswith("Under-provision in prior years"), geography.group_hint
    # The sub-heading is carried, not conflated: the rows that DO name themselves keep their own
    # sub-heading and are unaffected by it.
    cit = next(it for it in rows if (it.raw_label or "").startswith("PRC corporate"))
    assert cit.group_hint == "Current charge for the year:"
    # And the figure it contributes is in the published component.
    child = next(li for li in doc.line_items
                 if li.canonical_key == "pl_tax_expense__current_tax")
    assert child.values, "the geography row's 400 never reached the face"
    assert {ev.value_raw for ev in child.values.values()} == {Decimal("1600"), Decimal("1200")}


def test_the_sign_flip_is_recorded_on_every_value_it_touched(awkward_tax):
    """D3: the engine changed a reported sign, so it says so and keeps what the page said.

    ``value_raw`` holds the note's own figure and ``value`` carries the face's convention, which is
    the contract those two fields already have for the rulebook's own sign rule. Without
    ``sign_normalised`` the pair is indistinguishable from a filing that printed it that way.
    """
    doc, _ctx = awkward_tax
    children = [li for li in doc.line_items
                if li.canonical_key in ("pl_tax_expense__current_tax",
                                        "pl_tax_expense__deferred_tax")]
    assert len(children) == 2

    for child in children:
        assert "split_sign_flipped" in child.confidence.flags, child.canonical_key
        for ev in child.values.values():
            assert ev.sign_normalised is True
            assert ev.value == -ev.value_raw, (child.canonical_key, ev.period_label)


def test_the_split_says_it_flipped_the_signs(awkward_tax):
    """The log is where an analyst finds out why the note and the statement disagree on sign."""
    _doc, ctx = awkward_tax
    fired = [line for line in ctx.logs
             if line.startswith("map_line_items:split(pl_tax_expense__total_tax_expense)")]
    assert len(fired) == 1, ctx.logs
    assert "signs flipped to the face convention" in fired[0]


def test_the_note_tie_agrees_with_the_split_about_the_same_note(awkward_tax):
    """The other note-vs-face comparator in the pipeline must not contradict this one.

    ``reconcile`` grades how well a cited note ties back to the face figure, and it read the note's
    figures in the note's own convention: against a face of (1,000) and a note total of 1,000 it
    reported a residual of -2,000 and graded the note ``unconfirmed`` -- "this note is not a
    breakdown of that figure" -- about the very note the split had just decomposed the figure with.
    Two comparators disagreeing on the same evidence is worse than either being wrong, because a
    reader has no way to tell which to believe.
    """
    doc, _ctx = awkward_tax
    face = next(li for li in doc.line_items
                if (li.source_label or "").startswith("Income tax expense"))
    entries = [e for e in doc.reconciliation.entries if e.face_item_id == str(face.id)]

    assert len(entries) == 2, [(e.period_label, str(e.residual)) for e in entries]
    assert {e.period_label for e in entries} == {"current", "prior"}
    for e in entries:
        assert e.residual == Decimal(0), (e.period_label, e.residual)
        assert e.within_tolerance is True, e.period_label


def test_the_note_tie_orientation_needs_every_period_to_agree():
    """And it is not free to flip one period to make it fit.

    The orientation is settled for a whole face line before any of its periods is graded, so a note
    that reconciles in one period and is sign-wrong in the other gets no flip at all and is graded
    exactly as it was before this existed.
    """
    from decimal import Decimal as D

    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
    from app.services.reconcile import NoteDetail, ReconcileInput, reconcile_face

    # The pure function honours whatever orientation it is handed, and defaults to the note's own.
    details = [NoteDetail(item_id="a", value=D(1600), maps_to_distinct_template_line=False),
               NoteDetail(item_id="b", value=D(-600), maps_to_distinct_template_line=False)]
    plain = reconcile_face(ReconcileInput(face_item_id="f", note_number="11",
                                          raw_face_value=D(-1000), details=details))
    assert plain.residual == D(-2000)
    flipped = reconcile_face(ReconcileInput(face_item_id="f", note_number="11",
                                            raw_face_value=D(-1000), details=details,
                                            note_orientation=-1))
    assert flipped.residual == D(0)

    # And the stage refuses the flip when the two periods do not agree about it. Here the CURRENT
    # column ties exactly under the negation (-1,000 against a disclosed 1,000) and the prior column
    # ties under neither (-900 against 500). A rule that settled the orientation column by column
    # would take the flip on the strength of the one column that likes it, publish a residual of
    # zero there, and hide the fact that the note does not reconcile at all.
    def ev(value, period):
        return ExtractedValue(value=D(str(value)), value_raw=D(str(value)),
                              basis=Basis.CONSOLIDATED, period_label=period)

    face = LineItem(source_label="Income tax expense", canonical_key="pl_tax_expense__x")
    face.set_value(ev(-1000, "current"))
    face.set_value(ev(-900, "prior"))
    note = NotesTable(note_number="11")
    row = NoteItem(raw_label="Current tax")
    row.set_value(ev(1000, "current"))
    row.set_value(ev(500, "prior"))
    note.items.append(row)

    from app.core.models.document import DocumentModel
    from app.core.models.line_item import FaceNoteLink
    from app.core.stage import PipelineContext
    from app.stages.reconcile import ReconcileStage

    doc = DocumentModel(filename="f.pdf")
    doc.line_items.append(face)
    doc.notes.append(note)
    doc.links.append(FaceNoteLink(face_item_id=face.id, notes_table_id=note.id,
                                  note_number="11"))
    ReconcileStage().run(doc, PipelineContext(raw_bytes=b""))

    got = {e.period_label: e.residual for e in doc.reconciliation.entries}
    # As printed throughout, so both residuals report the real distance and neither is flattered.
    assert got == {"current": Decimal(-2000), "prior": Decimal(-1400)}, got


def test_a_later_stage_does_not_re_derive_the_sign_the_split_settled():
    """The split runs BEFORE normalize, and normalize recomputes a sign from ``value_raw``.

    A published component keeps the note row's caption, and a note routinely prints "Add: ..." or
    "Less: ...". Normalize's sign pass keys on exactly those words and recomputes the figure from
    ``value_raw`` -- which is the NOTE's figure -- so it silently undid the orientation while
    ``sign_normalised`` and ``split_sign_flipped`` went on claiming the flip had happened. Nothing
    caught it downstream either: the split clears the aggregate's ``canonical_key``, so the rollup
    that would have failed no longer had a target.
    """
    from app.core.models.document import DocumentModel
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.stage import PipelineContext
    from app.stages.normalize import NormalizeStage

    def fact(value, raw, period, flipped):
        return ExtractedValue(value=Decimal(str(value)), value_raw=Decimal(str(raw)),
                              basis=Basis.CONSOLIDATED, period_label=period,
                              sign_normalised=flipped)

    doc = DocumentModel(filename="f.pdf")
    # As the split publishes it: the caption is the note row's, value_raw is the note's figure, and
    # value carries the face's convention.
    flipped_row = LineItem(source_label="Add: Mainland China",
                           canonical_key="pl_tax_expense__current_tax")
    flipped_row.set_value(fact(-1600, 1600, "current", True))
    # An ordinary row with the same shape of caption, which normalize SHOULD still act on.
    ordinary = LineItem(source_label="Less: accumulated depreciation",
                        canonical_key="bs_non_current_assets__property_plant_and_equipment")
    ordinary.set_value(fact(500, 500, "current", False))
    doc.line_items += [flipped_row, ordinary]

    NormalizeStage().run(doc, PipelineContext(raw_bytes=b""))

    kept = next(iter(flipped_row.values.values()))
    assert kept.value == Decimal(-1600), "normalize re-derived a sign the split had settled"
    assert kept.value_raw == Decimal(1600), "the note's own figure must survive for the audit"
    # ...and the pass is still doing its job on everything else.
    assert next(iter(ordinary.values.values())).value == Decimal(-500)


def test_only_the_columns_the_aggregate_printed_are_published(rulebook, template):
    """The gate tests the columns the AGGREGATE carries. A column only the note has was never
    tested against anything, so publishing it asserts an unchecked figure — and applies the
    orientation to it, which is a sign decided by arithmetic that column took no part in.

    Extraction emits a positional column for a table with extra numeric columns (a maturity date,
    a coupon rate read as a figure), so this is not hypothetical: those arrive as ``col2``/``col3``
    alongside the reported periods, and the note's rows carry them while the face's line does not.
    """
    from app.config import get_settings
    from app.core.models.document import DocumentModel
    from app.core.models.enums import Basis, PrintedIn
    from app.core.models.line_item import (
        ExtractedValue,
        LineItem,
        NoteItem,
        NoteRef,
        NotesTable,
    )
    from app.core.stage import PipelineContext
    from app.services.mapping import OntologyMatcher
    from app.stages.map_ontology import MapOntologyStage

    def fact(value, period):
        return ExtractedValue(value=Decimal(str(value)), value_raw=Decimal(str(value)),
                              basis=Basis.CONSOLIDATED, period_label=period)

    doc = DocumentModel(filename="f.pdf")
    from app.core.models.document import PageSource
    doc.pages.append(PageSource(index=0, statement="profit_and_loss"))
    parent = LineItem(source_label="Income tax expense",
                      canonical_key="pl_tax_expense__total_tax_expense",
                      printed_in=PrintedIn.FACE, note_number="11",
                      note_refs=[NoteRef(raw="11", numbers=["11"])])
    parent.set_value(fact(-1000, "current"))
    doc.line_items.append(parent)

    note = NotesTable(note_number="11", source_pages=[1])
    for caption, cur, extra in (("Current tax", 1600, 25), ("Deferred tax", -600, 7)):
        row = NoteItem(raw_label=caption)
        row.set_value(fact(cur, "current"))
        row.set_value(fact(extra, "col2"))       # the column the face never printed
        note.items.append(row)
    doc.notes.append(note)

    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology, ctx.template = rulebook, template
    matcher = OntologyMatcher(rulebook, settings=ctx.settings)
    added = MapOntologyStage()._split_from_disclosure(doc, rulebook, matcher, ctx)

    assert added == 2, ctx.logs
    published = {li.canonical_key: {ev.period_label: ev.value for ev in li.values.values()}
                 for li in doc.line_items if (li.canonical_key or "").startswith("pl_tax_expense__")}
    assert published == {
        "pl_tax_expense__total_tax_expense": {"current": Decimal("-1000")},
        "pl_tax_expense__current_tax": {"current": Decimal("-1600")},
        "pl_tax_expense__deferred_tax": {"current": Decimal("600")},
    }, published


def test_reconciled_note_matrix_closing_cells_promote_category_facts():
    from app.config import get_settings
    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import Basis, PrintedIn
    from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NoteRef, NotesTable
    from app.core.stage import PipelineContext
    from app.services.mapping import OntologyMatcher
    from app.schemas.loader import load_ontology, load_template
    from app.stages.map_ontology import MapOntologyStage

    def fact(value, column):
        return ExtractedValue(value=Decimal(str(value)), value_raw=Decimal(str(value)),
                              basis=Basis.CONSOLIDATED, period_label=column)

    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=0, statement="balance_sheet"))
    parent = LineItem(source_label="Property, plant and equipment",
                      canonical_key="bs_nca__plant_and_equipment", printed_in=PrintedIn.FACE,
                      note_number="14", note_refs=[NoteRef(raw="14", numbers=["14"])])
    parent.set_value(fact(100, "current"))
    doc.line_items.append(parent)
    closing = NoteItem(raw_label="At 31 December 2025")
    closing.set_value(fact(40, "Land"))
    closing.set_value(fact(60, "Buildings"))
    doc.notes.append(NotesTable(note_number="14", items=[closing]))
    raw_ontology = json.loads((_SAMPLES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
    raw_template = json.loads((_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology, ctx.template = load_ontology(raw_ontology, resolve=True), load_template(raw_template)
    matcher = OntologyMatcher(ctx.ontology, settings=ctx.settings)

    added = MapOntologyStage._promote_reconciled_matrix_closings(
        doc, matcher, lambda _row: "balance_sheet", ctx)

    assert added == 2
    assert parent.canonical_key is None and parent.role is LineRole.SUBTOTAL
    promoted = {row.canonical_key: next(iter(row.values.values())).value
                for row in doc.line_items if row.canonical_key}
    assert promoted == {"bs_nca__land": Decimal("40"), "bs_nca__buildings": Decimal("60")}


# --- the orientation gate itself, at the unit -----------------------------------------------------

def _fact(value, period, basis=None):
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue

    return ExtractedValue(value=Decimal(str(value)), value_raw=Decimal(str(value)),
                          basis=basis or Basis.CONSOLIDATED, period_label=period)


def _rowlike(*facts):
    from app.core.models.line_item import LineItem

    li = LineItem()
    for f in facts:
        li.set_value(f)
    return li


def test_one_orientation_must_hold_for_every_column():
    """The gate's whole protection: a flip is a statement about PRESENTATION, so it is true of the
    entire disclosure or of none of it.

    Components right in one period and sign-wrong in the other are a real error, and flipping
    per column would wave it through -- which is exactly what this gate exists to catch. Here the
    aggregate is negative in one column and positive in the other while the components are positive
    in both, so EACH orientation accounts for one column and NEITHER accounts for both: as
    disclosed the prior column ties and the current one does not, flipped it is the other way
    round. A per-column rule would have accepted this outright.
    """
    from app.stages.map_ontology import _orientation_accounting_for

    parent = _rowlike(_fact(-1000, "current"), _fact(900, "prior"))
    hits = {"a": [_rowlike(_fact(600, "current"), _fact(600, "prior"))],
            "b": [_rowlike(_fact(400, "current"), _fact(300, "prior"))]}

    orientation, unaccounted = _orientation_accounting_for(parent, hits, Decimal(1))
    assert orientation == 0
    # Reported as-disclosed, which is the comparison an analyst can repeat against the printed note.
    assert unaccounted == ["consolidated/current"]


def test_the_flip_is_taken_only_when_the_negation_accounts_for_everything():
    """And when it does hold across every column, it is taken and reported as -1."""
    from app.stages.map_ontology import _orientation_accounting_for

    parent = _rowlike(_fact(-1000, "current"), _fact(-900, "prior"))
    hits = {"a": [_rowlike(_fact(1600, "current"), _fact(1200, "prior"))],
            "b": [_rowlike(_fact(-600, "current"), _fact(-300, "prior"))]}

    assert _orientation_accounting_for(parent, hits, Decimal(1)) == (-1, [])

    # As-disclosed wins outright when it works, so a filing that needs no flip never gets one.
    same = _rowlike(_fact(1000, "current"), _fact(900, "prior"))
    assert _orientation_accounting_for(same, hits, Decimal(1)) == (1, [])


def test_a_column_the_components_are_silent_about_is_a_failure():
    """Not a column to skip: publishing components for one period and nothing for the other deletes
    the other period's figure from the statement.

    The current column here would reconcile under the flip -- -1,000 against a disclosed 1,000 --
    and it is the SILENT prior column that refuses that orientation, which is the whole point: one
    orientation has to account for every column the aggregate carries, and there is no orientation
    under which a missing figure accounts for a printed one.
    """
    from app.stages.map_ontology import _orientation_accounting_for

    parent = _rowlike(_fact(-1000, "current"), _fact(-900, "prior"))
    hits = {"a": [_rowlike(_fact(1000, "current"))]}

    orientation, unaccounted = _orientation_accounting_for(parent, hits, Decimal(1))
    assert orientation == 0
    assert unaccounted == ["consolidated/current", "consolidated/prior"]


# --- the units caption is not a section ----------------------------------------------------------

def test_a_doubled_units_caption_does_not_scope_the_rows_under_it(awkward_tax):
    """A units caption is printed once per value column, so a two-period statement prints it twice
    and the doubled form survives the normalisation that drops the single one. Taken as a banner it
    put a unit where a section belongs on every row beneath it."""
    doc, _ctx = awkward_tax
    hints = {li.section_hint for li in doc.line_items}
    assert not any(h and ("000" in h or "RMB" in h) for h in hints), hints
