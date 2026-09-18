"""The stage that reads `LineItemDef.note_source` — the mechanism 650 authored patterns waited for.

WHY THIS FILE MATTERS MORE THAN MOST. Thirteen shipped sub-line items author `note_source`: which
note a figure lives in, which of its rows count, and which rows must be excluded even when a
counting pattern claimed them — in English, Traditional and Simplified Chinese, roughly 650 patterns
in total. Until `stages/note_sourced.py` existed, NOTHING read any of them. Five concept-specific
derivation services used to; they were removed, and no generic reader took their place, so every one
of those patterns was authored, versioned, displayed on the configuration screen, and consulted on
no run.

So these tests are the difference between a mechanism and a claim, and they are written against the
SHIPPED configuration rather than against fixtures invented here — a reader that works only on
hand-made declarations would prove nothing about the thirteen that exist.

THE TWO ARITHMETICS, which is what most of this file is about. `rollup` means different things at
the two levels, and conflating them was a real bug caught by the test below:

  * WITHIN one note, rows are COMPONENTS — a note splitting depreciation across four assets prints
    four rows and the note's depreciation is their sum.
  * ACROSS notes to the parent, the children are ALTERNATIVE DISCLOSURES of one figure. The shipped
    set says so: `is_pl__deprec_and_impairment_oper_exp` declares `rollup: "alternatives"` over its
    twelve children. Summing them multiplies one depreciation charge by the number of places the
    filing happened to mention it.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.stages.note_sourced import NoteSourcedStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

OPER_EXP = "is_pl__deprec_and_impairment_oper_exp"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _note_row(caption: str, amount: str, *, period: str = "current") -> NoteItem:
    item = NoteItem(raw_label=caption)
    item.values["k"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                      value=Decimal(amount), value_raw=Decimal(amount),
                                      provenance=Provenance(page_index=42))
    return item


def _prose_note(*sentences: str, number: str = "8",
                title: str = "Property, plant and equipment") -> NotesTable:
    """A note whose figures are in its SENTENCES rather than its rows.

    THE SIX FUNCTIONAL DEPRECIATION SPLITS DECLARE `route: prose`, so this is how they are fed. A
    sentence has to carry a GROUPED amount to be read at all (`note_sourced._PROSE_AMOUNT`), which
    is why every figure below is written with a thousands separator: a bare "300" is not an amount
    a filing states in prose and the reader does not treat it as one.
    """
    # `source_pages`, because a prose figure's only click-to-source is the note's own page —
    # `note_sourced._prose_provenance` reads it off the table, there being no row to copy a
    # `Provenance` from. A note without it yields a figure a reviewer cannot go and look at.
    return NotesTable(note_number=number, title=title, source_pages=[42],
                      source_text=" ".join(sentences))


RD_1200 = "Depreciation of HK$1,200 is included in research and development expenses."
RD_1300 = "Amortisation of HK$1,300 is charged to research and development costs."
GA_1350 = "Depreciation of HK$1,350 is included in administrative expenses."


def _run(shipped, notes: list[NotesTable], rows: list[LineItem] | None = None):
    doc = DocumentModel(filename="f.pdf")
    doc.notes = notes
    doc.line_items = list(rows or [])
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx), ctx


def _figure(doc, key: str, period: str = "current") -> Decimal | None:
    for li in doc.line_items:
        if li.canonical_key != key:
            continue
        for ev in li.values.values():
            if str(ev.period_label or "") == period and ev.value is not None:
                return ev.value
    return None


def test_the_shipped_configuration_still_declares_note_sources_to_read():
    """LIVENESS, first. If the shipped set stopped declaring `note_source`, every test below would
    pass by finding nothing — which is precisely the state this stage was written to end."""
    raw = json.loads(SEED.read_text(encoding="utf-8"))
    declaring = [i for i in raw["items"] if i.get("note_source")]
    assert len(declaring) >= 13, f"only {len(declaring)} items declare a note_source"
    patterns = sum(len(i["note_source"].get(f) or [])
                   for i in declaring
                   for f in ("note_title_any", "row_caption_any", "row_caption_none"))
    assert patterns > 400, f"only {patterns} authored patterns — the corpus has shrunk"


def test_a_figure_is_read_out_of_the_note_the_configuration_names(shipped):
    """The whole point: an ASSET note whose SENTENCE names the function the charge landed in.

    The children share one note set, so what identifies them is the destination the sentence
    names — and it is a sentence, not a row, because a functional split of a depreciation charge is
    disclosed in narrative and a note TABLE that appears to state one is almost always the note
    total the split is a component of. That is what `route: prose` says.
    """
    doc, ctx = _run(shipped, [_prose_note(RD_1200)])
    assert _figure(doc, "sub__rd_depreciation") == Decimal("1200")
    assert any("sub__rd_depreciation" in line for line in ctx.logs)


def test_sentences_inside_one_note_are_added_because_they_are_components(shipped):
    """A note stating one function's charge in two sentences — depreciation and amortisation — and
    the function's figure is their sum, exactly as two ROWS of one note are components.

    THE DEFECT THIS CAUGHT. The prose branch used to write each hit as it came, and `_write`
    replaces a slot it already holds, so two sentences published the LAST of them: 1,300 for a
    charge of 2,500. It was latent while prose was only a fallback for these six and the reference
    filing stated each share in one sentence; declaring `route: prose` made it their only path, and
    the item's own `rollup` — `sum` on all six — now decides through the same
    `note_sourced.take_by_rollup` the row route reads.
    """
    doc, _ctx = _run(shipped, [_prose_note(RD_1200, RD_1300)])
    assert _figure(doc, "sub__rd_depreciation") == Decimal("2500")


def test_the_veto_removes_a_row_a_counting_pattern_already_claimed(shipped):
    """`row_caption_none` exists for exactly this: "accumulated depreciation" and the movement rows
    of a fixed-asset table match "depreciation" and are not the year's charge. If the veto were
    applied before the counting patterns, or not at all, the figure would be wrong rather than
    absent — and a wrong figure that still ties is the failure nobody sees."""
    doc, _ctx = _run(shipped, [_prose_note(
        RD_1200,
        # QUALIFIED BY THE SAME FUNCTION, so it matches a counting pattern and the veto has to be
        # what removes it — the point of the test. A sentence about "accumulated depreciation"
        # alone would match no counting pattern at all and prove nothing.
        "Accumulated depreciation of HK$9,999 is included in research and development expenses.",
        "Exchange differences of HK$1,007 arose on translation.")])
    assert _figure(doc, "sub__rd_depreciation") == Decimal("1200")


def test_a_row_matching_no_counting_pattern_is_not_read(shipped):
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="8", title="Property, plant and equipment",
        items=[_note_row("Staff costs", "5000")])])
    assert _figure(doc, "sub__rd_depreciation") is None


def test_a_note_whose_title_does_not_match_is_not_read(shipped):
    """The title gate, with a row that WOULD be claimed in an in-scope note — so the title is the
    only thing refusing it. Inventories is not one of the five notes the children share."""
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="13", title="Inventories",
        items=[_note_row("Depreciation included in research and development expenses", "1200")])])
    assert _figure(doc, "sub__rd_depreciation") is None


def test_alternatives_takes_one_child_when_the_parent_declares_no_cascade(shipped):
    """THE ROLLUP PATH, tested where it actually applies.

    `rollup: "alternatives"` is the coarse statement "these children are not addends", and it
    governs only a parent that declares no cascade. Where a cascade IS declared it wins — the
    shipped depreciation parents declare one, and the test below this asserts that instead.

    This test previously asserted the rollup behaviour on a depreciation parent and passed for the
    wrong reason: the stage was reading the rollup and ignoring the five rungs beside it. Once the
    cascade was read, the same inputs correctly summed to 2,550 and this assertion failed — which
    is how the defect was found.
    """
    edited = shipped.model_copy(deep=True)
    parent = next(i for i in edited.items if i.key == OPER_EXP)
    parent.cascade = []                 # leave only the rollup to speak
    parent.rollup = "alternatives"

    doc = DocumentModel(filename="f.pdf")
    # ONE asset note breaking its depreciation down by function: two children read two different
    # SENTENCES of the same note.
    doc.notes = [_prose_note(RD_1200, GA_1350)]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = edited
    doc = NoteSourcedStage().run(doc, ctx)

    taken = _figure(doc, OPER_EXP)
    assert taken in (Decimal("1200"), Decimal("1350")), taken
    assert taken != Decimal("2550"), "alternatives summed what it should have chosen between"
    row = next(li for li in doc.line_items if li.canonical_key == OPER_EXP)
    assert any("alternatives_available" in f for f in row.confidence.flags), row.confidence.flags


def test_the_parent_keeps_the_figure_the_filing_printed(shipped):
    """A printed parent is the filing stating the amount; a note-derived one is an inference from a
    breakdown. Preferring the inference would discard the better number and leave nothing to
    review, so the printed figure stands and the disagreement is flagged."""
    printed = LineItem(source_label="Depreciation and impairment", canonical_key=OPER_EXP)
    printed.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("5000"), value_raw=Decimal("5000")))
    doc, ctx = _run(shipped, [_prose_note(RD_1200)], rows=[printed])

    assert _figure(doc, OPER_EXP) == Decimal("5000")
    row = next(li for li in doc.line_items if li.canonical_key == OPER_EXP)
    assert any("differs_from_printed" in f for f in row.confidence.flags), row.confidence.flags
    assert any("kept over" in line for line in ctx.logs)


def test_the_trail_names_the_note_and_every_sentence_that_contributed(shipped):
    """A figure assembled out of a note is unreviewable without saying what it was assembled from.
    The trail is written through `services.derivation`, which the statement inspector already
    renders with click-to-source.

    EVERY CONTRIBUTING SENTENCE, not just the one that happened to be written last — the same
    contract the row trail keeps. Before the prose branch aggregated, each hit recorded its own
    single-input trail over the top of the previous one, so a two-sentence figure was explained by
    one sentence that did not add up to it.
    """
    doc, _ctx = _run(shipped, [_prose_note(RD_1200, RD_1300)])
    row = next(li for li in doc.line_items if li.canonical_key == "sub__rd_depreciation")
    assert row.derivation, "no trail was recorded"
    trail = next(iter(row.derivation.values()))
    counted = [i for i in trail["inputs"] if i.get("counted")]
    assert len(counted) == 2, trail["inputs"]
    assert {i["note"] for i in counted} == {"8"}
    assert all(i["provenance"] for i in counted), "the trail lost the page it came from"
    # The SENTENCE is quoted, so a wrong selection is traceable to the words that caused it.
    assert any("research and development expenses" in i["label"] for i in counted)
    assert all("stated in full in the sentence" in i["excerpt"] for i in counted)
    assert str(trail["result"]) == "2500"


def test_an_uncompilable_pattern_is_refused_AT_CONFIGURATION_TIME(shipped):
    """The guarantee is stronger than the stage's own defence, and it belongs to the schema.

    `NoteSource` validates every pattern when the configuration LOADS, naming the field and the
    index — so a mistyped regex is refused on the author's save, attributed to
    `row_caption_any[1]`, and can never reach a run. That is the right place for it: a pattern that
    silently matched nothing would leave a line mysteriously empty on a 300-page filing, with
    nothing to connect the empty line to the typo that caused it.

    The stage keeps `note_sourced.bad_patterns` as defence in depth, for a set assembled in code
    that bypassed validation, but on every real path this refusal fires first.
    """
    from app.schemas.line_items import NoteSource

    with pytest.raises(ValueError, match="does not compile"):
        NoteSource(note_title_any=["general"],
                   row_caption_any=["depreciation", "([unclosed"], row_caption_none=[])

    # And the refusal points at the exact list entry, not just at the item.
    try:
        NoteSource(note_title_any=["general"], row_caption_any=["ok", "([bad"])
    except ValueError as e:
        assert "row_caption_any[1]" in str(e), str(e)


def test_the_stage_still_survives_a_pattern_validation_never_saw(shipped):
    """Defence in depth, on the one path that can produce one: a set built with `model_construct`,
    which skips validators. One bad pattern must cost its own row and nothing else."""
    from app.schemas.line_items import NoteSource

    broken = shipped.model_copy(deep=True)
    target = next(i for i in broken.items if i.key == "sub__ga_depreciation")
    # ON `prose_any`, WHICH IS THE ONE PROSE FIELD THAT CAN CARRY A BROKEN PATTERN. The rest of the
    # prose route is authored in plain phrases and generated, and a plain phrase cannot fail to
    # compile — so the escape hatch is where the silent hole would be, and where `bad_patterns`
    # looks. The generated vocabulary is kept beside it, so the good half can still deliver.
    target.note_source = NoteSource.model_construct(
        note_title_any=["general"], row_caption_any=[], row_caption_none=[],
        prose_any=["([unclosed"], prose_subject="depreciation",
        prose_landed_in=["administrative expenses"])

    doc = DocumentModel(filename="f.pdf")
    doc.notes = [_prose_note(GA_1350, number="9",
                             title="General and administrative expenses")]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = broken

    doc = NoteSourcedStage().run(doc, ctx)          # must not raise

    assert any("REFUSED" in line and "sub__ga_depreciation" in line for line in ctx.logs)
    # The GOOD pattern beside it still worked — one bad pattern is one bad pattern, not a dead item.
    assert _figure(doc, "sub__ga_depreciation") == Decimal("1350")


def test_a_run_with_no_configuration_says_so_rather_than_doing_nothing_quietly(shipped):
    """A run whose configuration never arrived and a run whose notes matched nothing are different
    facts, and the log has to distinguish them."""
    doc = DocumentModel(filename="f.pdf")
    ctx = PipelineContext(settings=get_settings())
    NoteSourcedStage().run(doc, ctx)
    assert any("no line item declares a note_source" in line for line in ctx.logs)


def test_the_stage_is_registered_in_the_one_pipeline_registry():
    """A stage nothing runs is the defect this whole exercise is about."""
    from app.core.pipeline import default_pipeline

    names = [getattr(s, "name", type(s).__name__) for s in default_pipeline().stages]
    assert "note_sourced" in names, names
    # AFTER normalize, deliberately: a note-derived figure must not be put through the
    # unsigned-expense cohort vote, which would flip the sign of every expense on the statement.
    assert names.index("note_sourced") > names.index("normalize"), names


def test_a_note_now_fills_its_parent_because_decomposition_is_always_allowed(shipped):
    """THE `note_use` GATE IS GONE, and this asserts its removal rather than its behaviour.

    It used to assert the set's own prose rule — "Notes are evidence for a face amount, never an
    independent source of one, unless note_use is decomposition_allowed"
    (`global_rules.face_only_default`) — by giving an `evidence_only` parent a child that WOULD
    match and checking the parent stayed empty with a loud `REFUSED as a note source` log.

    `note_use` is no longer a question: decomposition is always allowed. So the same scenario now
    FILLS the parent, which is the whole of what removing the question means.

    THE HONEST SCOPE OF THAT CHANGE, because the two statements are different and only one is a
    no-op. On the SHIPPED configuration the gate could not fire — of the 70 items resolving to
    `evidence_only`, none was a note-sourced parent, none carried a `note_source` and none had a
    child, so no shipped figure moves. For an ARBITRARY set it could, and this test is that case:
    it builds the parent-with-a-matching-child the shipped set does not contain. Keeping it as the
    demonstration of the difference is more useful than deleting it.
    """
    from app.schemas.line_items import NoteSource

    edited = shipped.model_copy(deep=True)
    by_key = {i.key: i for i in edited.items}
    PROBE = "notes__pledged_assets"

    # Give it a child that matches, exactly as the retired test did.
    child = by_key["sub__cos_depreciation"].model_copy(deep=True)
    child.key = "sub__probe_guarantees"
    child.parent = PROBE
    child.note_source = NoteSource(note_title_any=["contingent"],
                                   row_caption_any=["guarantee"], row_caption_none=[])
    # THE ROUTE MUST MATCH WHAT THE PROBE IS AUTHORED FOR. It was copied off a line that declares
    # `prose`, and its own `note_source` is row patterns — so left as it was, the probe searched
    # sentences for a vocabulary it does not have and the scenario this test builds could not
    # arise. The gate under test is `note_use`, not the route.
    child.route = "note_tables"
    edited.items.append(child)

    doc = DocumentModel(filename="f.pdf")
    doc.notes = [NotesTable(note_number="31", title="Contingent liabilities",
                            items=[_note_row("Corporate guarantees given", "8000")])]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = edited
    doc = NoteSourcedStage().run(doc, ctx)

    # The child is filled, as it always was — the selection was never what the gate stopped.
    assert _figure(doc, "sub__probe_guarantees") == Decimal("8000")
    # And the PARENT is filled now, where it used to be refused.
    assert _figure(doc, PROBE) == Decimal("8000")
    # No refusal is logged, because there is no longer a permission to refuse on.
    assert not any("REFUSED as a note source" in line for line in ctx.logs), ctx.logs


def test_the_seven_concepts_that_permit_decomposition_are_not_blocked_by_the_gate(shipped):
    """The gate must refuse only what the configuration marks, or it would quietly disable the
    mechanism for the other seven focus concepts."""
    permits = {i.key: i.note_use for i in shipped.items}
    assert permits["is_pl__deprec_and_impairment_oper_exp"] == "decomposition_allowed"
    assert permits["is_pl__deprec_and_impairment_cos"] == "decomposition_allowed"
    # And the shipped depreciation fill still happens, which is the same assertion end to end.
    doc, _ctx = _run(shipped, [_prose_note(RD_1200)])
    assert _figure(doc, OPER_EXP) == Decimal("1200")


# ── the declared cascade, which is the aggregation `rollup` can only summarise ────────────────

def test_the_declared_cascade_decides_the_parent_not_the_rollup(shipped):
    """THE DEFECT THIS FIXED, and it was in the first version of this stage.

    `is_pl__deprec_and_impairment_oper_exp` declares FIVE cascade rungs. P1 is "the four
    operating-expense notes, summed — any subset a filing discloses is still a sum". Its `rollup`
    is `alternatives`, which says roughly "the children are not addends": true, and far too coarse.
    Reading the rollup instead of the cascade took the FIRST child on its own — 1,200 where the
    configuration says 1,200 + 1,350.

    The cascade also carries what a rollup cannot express at all: optional terms (`any_of`), signed
    deductions (`adjustment`, `sign: -1`), and a refusal to accept a negative candidate.
    """
    # ONE asset note, two function sentences — P1 sums the two children that read them.
    doc, ctx = _run(shipped, [_prose_note(RD_1200, GA_1350)])
    assert _figure(doc, OPER_EXP) == Decimal("2550"), "P1 did not sum the disclosed subset"
    assert any("rung P1" in line for line in ctx.logs), ctx.logs


def test_an_absent_any_of_term_does_not_kill_the_rung(shipped):
    """P1's four terms are all `any_of`: a filing disclosing one of the four operating-expense
    notes still has a sum. A `required` reading would refuse the rung and fall through to a
    materially different provenance."""
    doc, ctx = _run(shipped, [_prose_note(GA_1350, number="9")])
    assert _figure(doc, OPER_EXP) == Decimal("1350")
    assert any("rung P1" in line for line in ctx.logs)


def test_the_trail_names_which_rung_answered(shipped):
    """A charge that came from "total less the cost-of-sales share" rather than from the four
    operating-expense notes has a materially different provenance, and a reviewer cannot see that
    in the number. The rung is recorded on the figure."""
    doc, _ctx = _run(shipped, [_prose_note(RD_1200)])
    row = next(li for li in doc.line_items if li.canonical_key == OPER_EXP)
    assert row.derivation, "the cascade wrote no trail"
    trail = next(iter(row.derivation.values()))
    assert trail["method"] == "cascade:P1", trail["method"]
    assert any(f.startswith("cascade_rung:") for f in row.confidence.flags), row.confidence.flags


def test_a_cost_of_sales_deduction_is_subtracted_not_added(shipped):
    """Rungs P3, P4 and P5 all carry `sub__cos_depreciation` with `sign: -1` and
    `role: "adjustment"` — the operating-expense charge is the total LESS the cost-of-sales share.
    A sign read the wrong way would overstate an expense by twice the deduction, and the statement
    would still balance."""
    from app.services.line_items import evaluate as evaluate_line

    parent = next(i for i in shipped.items if i.key == OPER_EXP)
    # P1 and P2's inputs deliberately absent, so the cascade reaches P3.
    known = {"sub__pbt_depreciation": Decimal("5000"),
             "sub__cos_depreciation": Decimal("1800")}
    got = evaluate_line(parent, known)
    assert got.rung_used == "P3", got.rung_used
    assert got.value == Decimal("3200"), got.value


def test_an_adjustment_on_its_own_is_not_an_answer(shipped):
    """A rung whose only figure is a deduction has nothing to deduct it from. Before the roles were
    separated this published a NEGATIVE depreciation charge."""
    from app.services.line_items import evaluate as evaluate_line

    parent = next(i for i in shipped.items if i.key == OPER_EXP)
    got = evaluate_line(parent, {"sub__cos_depreciation": Decimal("1800")})
    assert got.value is None or got.value >= 0, got.value


def test_the_cascade_does_not_overwrite_the_figure_the_filing_printed(shipped):
    """Same rule as the rollup path: a printed parent is the filing stating the amount."""
    printed = LineItem(source_label="Depreciation and impairment", canonical_key=OPER_EXP)
    printed.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("9000"), value_raw=Decimal("9000")))
    doc, ctx = _run(shipped, [_prose_note(RD_1200)], rows=[printed])
    assert _figure(doc, OPER_EXP) == Decimal("9000")
    assert any("kept over cascade" in line for line in ctx.logs), ctx.logs


# ── the three readers of the profit-before-tax note ───────────────────────────────────────────

def _pbt_note():
    """A real-shaped PBT note: the tabulated total, and the narrative callouts that split it.

    TWO ROUTES IN ONE NOTE, which is what the shipped set now asks for.
    `sub__pbt_depreciation` — the TOTAL — declares `note_tables` and reads the printed row, because
    a total is what a note tabulates. `sub__pbt_oper_exp_depreciation` — the operating-expense
    SHARE — declares `prose`, because a functional split is what a note states in a sentence. The
    note carries both, as a filing does.
    """
    return NotesTable(
        note_number="7", title="Profit before taxation",
        items=[_note_row("Depreciation of property, plant and equipment", "5000")],
        source_text=("Depreciation of HK$1,800 is included in cost of sales. "
                     "Depreciation of HK$3,200 is included in administrative expenses."),
    )


def test_two_children_read_the_pbt_note_by_different_routes(shipped):
    """THE DEFECT THIS FIXED. `sub__pbt_oper_exp_depreciation` and `sub__pbt_depreciation` carried
    BYTE-IDENTICAL title, counting and veto lists — so they selected the same rows and were one
    figure under two names. P2 therefore always resolved with the TOTAL, the operating-expense line
    was overstated by the cost-of-sales share, and the later rungs were unreachable on any filing
    whose PBT note mentioned depreciation at all.

    Each child is now identified by the ROUTE as well as by the qualifier: the total is the row
    the note tabulates and the share is the sentence it states, so the two cannot select the same
    evidence even if their vocabularies overlapped.

    TWO CHILDREN, NOT THREE. `sub__pbt_cos_depreciation` — the reader of the note's cost-of-sales
    callout — was retired deliberately, along with the cascade tier that consumed it. The
    cost-of-sales parent now reaches that share by subtraction instead (COS_P2), which is the tier
    that used to be COS_P3. See `test_the_cos_cascade_is_the_note_then_the_subtraction`.
    """
    doc, _ctx = _run(shipped, [_pbt_note()])
    assert _figure(doc, "sub__pbt_depreciation") == Decimal("5000")          # the tabulated total
    assert _figure(doc, "sub__pbt_oper_exp_depreciation") == Decimal("3200")  # the stated share
    # The retired reader stays retired: nothing in the set claims the callout row directly.
    assert not any(i.key == "sub__pbt_cos_depreciation" for i in shipped.items)
    # The total is still the total — 5,000, not the 10,000 that summing all three rows would give.
    assert Decimal("1800") + Decimal("3200") == Decimal("5000")


def test_the_total_row_refuses_the_qualified_callouts(shipped):
    """A caption qualified by "cost of sales" or "administrative expenses" is a SHARE of the total,
    not the total. Without the veto the total child sums all three rows to 10,000."""
    doc, _ctx = _run(shipped, [_pbt_note()])
    assert _figure(doc, "sub__pbt_depreciation") == Decimal("5000"), "the total absorbed a callout"


def test_the_pbt_notes_cost_of_sales_callout_is_no_longer_read_directly(shipped):
    """THE RETIRED TIER, pinned so it is not reinstated by accident.

    A rung once read the PBT note's own cost-of-sales callout through
    `sub__pbt_cos_depreciation`, on the reasoning that many filings state that share inside the PBT
    note and nowhere else. Both the part and the rung were retired deliberately.

    So a PBT note ALONE no longer gives the cost-of-sales parent a figure: COS_P1 needs the
    dedicated cost-of-sales note, and COS_P2 needs the operating-expense parent to subtract from,
    which a lone PBT note does not supply either. The line reports nothing rather than a share
    inferred from one row — and nothing is the honest answer here, which is why this is a test and
    not a gap.
    """
    doc, _ctx = _run(shipped, [_pbt_note()])
    assert _figure(doc, "is_pl__deprec_and_impairment_cos") is None
    assert not any(i.key == "sub__pbt_cos_depreciation" for i in shipped.items)


def test_the_cost_of_sales_note_still_outranks_the_pbt_callout(shipped):
    """COS_P1 is the cost-of-sales note itself. A filing with both must use the dedicated note —
    inserting the new rung above it would have changed which source wins."""
    doc, ctx = _run(shipped, [
        _pbt_note(),
        _prose_note("Depreciation of HK$1,750 is included in cost of sales.",
                    number="6", title="Cost of sales"),
    ])
    assert _figure(doc, "is_pl__deprec_and_impairment_cos") == Decimal("1750")
    assert any("rung COS_P1" in line for line in ctx.logs), ctx.logs


def test_the_cos_cascade_is_the_note_then_the_subtraction(shipped):
    """The rungs are tried in declared order, so the order IS the precedence of sources.

    TWO RUNGS, NOT THREE. The middle tier — the PBT note's own cost-of-sales callout, read through
    `sub__pbt_cos_depreciation` — was retired with its part, and the subtraction that was COS_P3 is
    now COS_P2. The precedence that remains is the one that matters: a figure the filing STATES in
    its cost-of-sales note beats one inferred by difference.
    """
    cos = next(i for i in shipped.items if i.key == "is_pl__deprec_and_impairment_cos")
    assert [r.id for r in cos.cascade] == ["COS_P1", "COS_P2"]
    # The last rung is still the subtraction, and still last.
    refs = [(t.ref, t.sign) for t in cos.cascade[-1].terms]
    assert ("is_pl__deprec_and_impairment_oper_exp", -1) in refs, refs
    assert ("sub__pbt_depreciation", 1) in refs, refs


def test_a_matrix_column_is_not_a_period(shipped):
    """MEASURED ON laisun.pdf, before this guard existed.

    `ExtractedValue.column_index` is set only for a fact printed in a NAMED COMPONENT column — an
    industry segment, a class of equity. That axis is decomposition, not time. The revenue
    segment note gave `is_pl__sales_revenues` twelve figures keyed `col2` … `col11` beside
    `current`/`prior`, and the value that reached the CURRENT slot was ONE SEGMENT's revenue
    (2,609,259) instead of the total the face prints (4,995,768).

    A per-segment figure published as the year's revenue is the worst shape of this defect: the
    line looks populated, the number is plausible, and it reconciles against nothing.
    """
    from app.core.models.line_item import NoteItem
    from app.services import note_sourced as svc

    row = NoteItem(raw_label="Depreciation included in research and development expenses")
    # A period fact and a matrix-column fact on the same row.
    row.values["p"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("1200"), value_raw=Decimal("1200"))
    row.values["m"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="col3",
                                     value=Decimal("400"), value_raw=Decimal("400"),
                                     column_index=3)
    note = NotesTable(note_number="8", title="Property, plant and equipment", items=[row])

    sub = next(i for i in shipped.items if i.key == "sub__rd_depreciation")
    hits = svc.select_rows(sub, [note])

    assert [h.period for h in hits] == ["current"], [h.period for h in hits]
    assert [str(h.amount) for h in hits] == ["1200"]


def test_a_note_column_the_statements_do_not_use_is_not_a_period(shipped):
    """MEASURED ON suncreate.pdf. The 营业收入和营业成本 note prints revenue and COST side by side, and
    the cost column arrived as a slot named `current:cost` carrying 2,239,996,631.60 onto the
    REVENUE line — a cost of sales published as revenue, on a line that reconciles against nothing.

    The allowlist is the set of period labels the FACE declares, derived from the document rather
    than hardcoded to current/prior, so a filing presenting three columns still works.
    """
    from app.core.models.line_item import NoteItem
    from app.services import note_sourced as svc

    row = NoteItem(raw_label="Depreciation included in research and development expenses")
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("1200"), value_raw=Decimal("1200"))
    row.values["b"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current:cost",
                                     value=Decimal("999"), value_raw=Decimal("999"))
    note = NotesTable(note_number="8", title="Property, plant and equipment", items=[row])
    sub = next(i for i in shipped.items if i.key == "sub__rd_depreciation")

    kept = svc.select_rows(sub, [note], {"current", "prior"})
    assert [(h.period, str(h.amount)) for h in kept] == [("current", "1200")]

    # No allowlist means no filtering — a document whose face declares no periods must not lose
    # every note figure.
    both = svc.select_rows(sub, [note], None)
    assert len(both) == 2


def test_the_allowlist_comes_from_the_face_not_from_a_literal(shipped):
    """A filing presenting three columns must not have its third silently dropped, which is what a
    hardcoded {current, prior} would do."""
    from app.core.models.line_item import NoteItem
    from app.core.stage import PipelineContext
    from app.stages.note_sourced import NoteSourcedStage

    face = LineItem(source_label="Revenue", canonical_key="is_pl__sales_revenues")
    for label in ("current", "prior", "prior_2"):
        face.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label,
                                      value=Decimal("1"), value_raw=Decimal("1")))
    # ASKED OF A ROW-ROUTED LINE, because the allowlist is about note COLUMNS and a sentence has
    # none — `select_prose` reads a period off the parenthetical year in the words, never off a
    # column, so it can neither honour nor violate a column allowlist. `sub__rd_depreciation`
    # declares `prose` and is the wrong line to ask; `sub__ppe_depreciation` reads the same asset
    # note through its rows and is the right one.
    row = NoteItem(raw_label="Depreciation of property, plant and equipment")
    for label, amount in (("current", "10"), ("prior", "20"), ("prior_2", "30"),
                          ("current:cost", "99")):
        row.values[label] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label,
                                           value=Decimal(amount), value_raw=Decimal(amount))
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [face]
    doc.notes = [NotesTable(note_number="8", title="Property, plant and equipment",
                            items=[row])]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    NoteSourcedStage().run(doc, ctx)

    assert any("face periods" in line and "prior_2" in line for line in ctx.logs), ctx.logs
    got = {f["period"]: f["value"] for f in
           [{"period": str(ev.period_label or ""), "value": str(ev.value)}
            for li in doc.line_items if li.canonical_key == "sub__ppe_depreciation"
            for ev in li.values.values() if ev.value is not None]}
    assert got == {"current": "10", "prior": "20", "prior_2": "30"}, got


# ── the route: where an extracted line's figure is read from ──────────────────────────────────
#
# ONE ASKED QUESTION replacing three inferred ones. Before `route`, "is this a note line" was
# answered by `SectionDefaults.where()` (itself derived from `face_only` and `scopes`), "does it
# have a note route" by whether a `note_source` object was present, and "is it prose" by whether
# that object happened to carry `prose_subject`/`prose_any`. An author could satisfy two of the
# three and not the last, and every way of getting it wrong was silent.

def test_a_face_route_line_is_not_filled_from_a_note(shipped):
    """`route: "face"` keeps a line out of this stage even when it declares a note_source.

    The guard existed before, keyed on the section the line inherits; it is now the line's own
    declaration. Asserted because the failure it prevents is the quiet kind — a face line taking a
    figure out of a note reconciles against nothing and looks entirely plausible on the statement.
    """
    edited = shipped.model_copy(deep=True)
    by_key = {i.key: i for i in edited.items}
    probe = by_key["sub__cos_depreciation"].model_copy(deep=True)
    probe.key = "sub__probe_face_routed"
    probe.parent = ""
    probe.route = "face"

    # THE POSITIVE CONTROL, and it is here because this test passed VACUOUSLY when first written:
    # the note was titled "Property, plant and equipment" and `sub__cos_depreciation` selects its
    # note by `cost of sales`, so NOTHING was selected and the assertion below held for entirely
    # the wrong reason. An identical line differing only in its route proves the note really does
    # match, so a `None` above can only be the route.
    control = probe.model_copy(deep=True)
    control.key = "sub__probe_control"
    control.route = "note_tables"

    edited.items += [probe, control]

    doc, _ctx = _run(edited, [NotesTable(
        note_number="8", title="Cost of sales",
        items=[_note_row("Depreciation of property, plant and equipment", "4321")])])

    assert _figure(doc, "sub__probe_control") == Decimal("4321"), (
        "the control must be filled, or this test proves nothing about the route")
    assert _figure(doc, "sub__probe_face_routed") is None


def test_a_prose_route_line_skips_the_row_search(shipped):
    """`route: "prose"` means the figure is a SENTENCE and nothing else.

    A `note_tables` line searches rows and falls back to prose; a `prose` line does not search rows
    at all. Without that, a coincidental caption match would publish a tabulated number on a line
    its author said is never tabulated — and the number would look right, because it came off a
    real extracted row in the right note.

    Proved by giving the same line a note whose row DOES match its patterns and asserting nothing
    is taken from it: the row route is what is switched off, not the matching.
    """
    edited = shipped.model_copy(deep=True)
    by_key = {i.key: i for i in edited.items}

    rows_only = by_key["sub__cos_depreciation"].model_copy(deep=True)
    rows_only.key = "sub__probe_rows"
    rows_only.parent = ""
    rows_only.route = "note_tables"

    prose_only = rows_only.model_copy(deep=True)
    prose_only.key = "sub__probe_prose"
    prose_only.route = "prose"

    edited.items += [rows_only, prose_only]

    # THE NOTE TITLE MATTERS: `sub__cos_depreciation` selects its note by `cost of sales`, not by
    # the asset note. Titled wrongly, every assertion below would pass for the wrong reason —
    # nothing selected because no note matched, rather than nothing selected because of the route.
    notes = [NotesTable(note_number="8", title="Cost of sales",
                        items=[_note_row("Depreciation of property, plant and equipment", "4321")])]
    doc, _ctx = _run(edited, notes)

    # Identical configuration, identical note — the ROUTE is the only difference between them.
    assert _figure(doc, "sub__probe_rows") == Decimal("4321")
    assert _figure(doc, "sub__probe_prose") is None


def test_a_line_that_declares_no_route_is_read_the_way_it_always_was(shipped):
    """`route: None` IS NOT "face" — it means nothing was said, and the section inference stands.

    This is what makes the field safe to add: a set authored before `route` existed behaves exactly
    as it did, so introducing the question re-routes no shipped figure. The 67 shipped items left
    unset are the `either` and `notes` sections that declare no note_source anyway.
    """
    edited = shipped.model_copy(deep=True)
    by_key = {i.key: i for i in edited.items}
    probe = by_key["sub__cos_depreciation"].model_copy(deep=True)
    probe.key = "sub__probe_unset"
    probe.parent = ""
    probe.route = None
    edited.items.append(probe)

    doc, _ctx = _run(edited, [NotesTable(
        note_number="8", title="Cost of sales",
        items=[_note_row("Depreciation of property, plant and equipment", "4321")])])

    # `sub__cos_depreciation` inherits `notes`, so the legacy inference reads it from the note.
    assert _figure(doc, "sub__probe_unset") == Decimal("4321")


def test_a_note_sourced_line_declares_which_part_of_a_note_it_is_read_from(shipped):
    """THE ROUTE MATCHES THE VOCABULARY, asserted in both directions.

    THIS REVERSES AN EARLIER DECISION, and the earlier reasoning is worth stating because it was
    not wrong, only outweighed. All the note-sourced lines used to declare `note_tables`, the six
    carrying prose vocabulary included, on the grounds that prose was a FALLBACK: on one reference
    filing the operating-expense share of depreciation is stated only in a footnote, and on another
    filing the same line could be a printed row — so `note_tables` plus the fallback covered both
    and `prose` would have emptied the line wherever it was tabulated.

    WHAT OUTWEIGHS IT. For these six the tabulated reading is the one that is usually WRONG. They
    are functional splits of a depreciation charge — the share that landed in R&D, in selling and
    marketing, in G&A, in other operating expenses, in the PBT note's operating-expense callout,
    in cost of sales — and a note table that appears to state one is almost always the note TOTAL
    the split is a component of. That is the mistake
    `line_item_notes.caption_agrees_with_row_terms` was written to catch after the fact, measured
    at the time as a face total of 1,026,959 bound to a depreciation component. Refusing the table
    at the route is the same refusal, made where the author can see it.

    THE COST, stated: a filing that tabulates a functional split and states it nowhere in prose now
    leaves these six empty. None of the five filings in the corpus is that filing — the four
    function splits produce nothing from either route on all five, and the two that do produce a
    figure (on China SCE) already came from prose through the fallback — so the change moved no
    published figure. The parents keep their other rungs either way.
    """
    declared = [i for i in shipped.items if getattr(i, "note_source", None) is not None]
    # 62: the other-receivables net's two readings each declare their own `note_source`, and the
    # parent they feed declares none.
    assert len(declared) == 63, len(declared)

    with_prose = [i for i in declared
                  if (i.note_source.prose_subject or i.note_source.prose_any)]
    assert len(with_prose) == 6, [i.key for i in with_prose]
    # EVERY line carrying prose vocabulary declares `prose`…
    assert {i.route for i in with_prose} == {"prose"}, sorted(
        {(i.key, i.route) for i in with_prose if i.route != "prose"})
    # …and every OTHER note-sourced line declares `note_tables`, so no line is routed to a part of
    # a note it has no vocabulary for. A `prose` line with no prose vocabulary would read nothing
    # at all, and a `note_tables` line is the default the other 56 keep.
    others = [i for i in declared if i not in with_prose]
    assert {i.route for i in others} == {"note_tables"}, sorted(
        {(i.key, i.route) for i in others if i.route != "note_tables"})
    # The six are exactly the functional depreciation splits, named so a reader does not have to
    # infer the population from the vocabulary test above.
    assert {i.key for i in with_prose} == {
        "sub__rd_depreciation", "sub__selling_marketing_depreciation", "sub__ga_depreciation",
        "sub__operating_expense_depreciation", "sub__pbt_oper_exp_depreciation",
        "sub__cos_depreciation"}


# ── one caption, two statement instances, two entities ───────────────────────────────────────────

def _face_row(key: str, caption: str, slots: dict[tuple[Basis, str], str]) -> LineItem:
    """A row an earlier stage already mapped and filled, as the face route leaves it."""
    row = LineItem(source_label=caption, canonical_key=key)
    for i, ((basis, period), amount) in enumerate(slots.items()):
        row.values[f"k{i}"] = ExtractedValue(
            basis=basis, period_label=period, value=Decimal(amount),
            value_raw=Decimal(amount), provenance=Provenance(page_index=88))
    return row


def test_a_part_printed_on_two_statements_reaches_its_parent_from_both(shipped):
    """`by_key` KEEPS ONE ROW PER KEY, and that used to be all the cascade ever saw.

    ``by_key`` is ``{li.canonical_key: li for li in doc.line_items}`` — last row wins. A caption
    printed on TWO statement instances yields two rows for one part key, which is the NORMAL case
    for a mainland filing: it prints every primary statement twice, consolidated and then parent
    company. The second row overwrote the first, the cascade saw only it, and the parent came out
    carrying whichever entity's slots that row happened to hold.

    Measured on 000709 before the sweep was widened: `is_pl__other_non_operating_inc_exp` published
    only the parent company's 476,718,290.06 and had NO consolidated figure at all, although both
    营业外 halves carried consolidated slots. A consolidated report served the parent's number.

    The fix takes every row carrying the key. Nothing has to choose between them, because
    `_fill_parents` groups its offers by (basis, period) — so the consolidated rows meet the
    consolidated rows and the standalone rows the standalone ones.
    """
    parent = "is_pl__other_non_operating_inc_exp"
    rows = [
        _face_row("sub__non_operating_income", "加：营业外收入",
                  {(Basis.CONSOLIDATED, "current"): "488570415.03",
                   (Basis.STANDALONE, "current"): "480646078.44"}),
        # The expense half arrives already negated — `stages/normalize` applies the part's
        # `sign_rule` to the CAS caption, which is upstream of this stage.
        _face_row("sub__non_operating_expenses", "减：营业外支出",
                  {(Basis.CONSOLIDATED, "current"): "-56388111.32",
                   (Basis.STANDALONE, "current"): "-3927788.38"}),
    ]
    doc, _ctx = _run(shipped, [], rows)

    got = {(ev.basis.value, ev.period_label): ev.value
           for li in doc.line_items if li.canonical_key == parent
           for ev in li.values.values() if ev.value is not None}
    assert got == {("consolidated", "current"): Decimal("432182303.71"),
                   ("standalone", "current"): Decimal("476718290.06")}


def test_the_direct_method_tax_halves_net_on_their_own_parent(shipped):
    """The same shape on the cash-flow face, and the reason the two tax parts exist.

    CAS 31 prints taxes as two rows on opposite sides of the operating section — 支付的各项税费
    above 经营活动现金流出小计 and 收到的税费返还 above 经营活动现金流入小计 — while this template
    carries ONE Income Taxes Paid(Direct) column. So the column is the net of the two, and neither
    caption may bind it directly: bound to the gross it reported taxes before refunds, and the
    refund caption reached no concept at all and was swept into the operating section's residual
    bucket together with two other homeless direct-method rows.
    """
    parent = "cf_oper_direct__income_taxes_paid_direct"
    rows = [
        _face_row("sub__cf_direct_income_taxes_paid", "支付的各项税费",
                  {(Basis.CONSOLIDATED, "current"): "-1483160446.61",
                   (Basis.CONSOLIDATED, "prior"): "-1896906274.50"}),
        _face_row("sub__cf_direct_tax_refunds_received", "收到的税费返还",
                  {(Basis.CONSOLIDATED, "current"): "134180854.19",
                   (Basis.CONSOLIDATED, "prior"): "4043103.53"}),
    ]
    doc, _ctx = _run(shipped, [], rows)

    assert _figure(doc, parent, "current") == Decimal("-1348979592.42")
    assert _figure(doc, parent, "prior") == Decimal("-1892863170.97")


# ── Find 1: the balance sheet's own related-party reading ────────────────────────────────────────

_RP_PARENT = "bs_nca__due_from_related_parties_ltp"


def _rp_run(shipped, pairs):
    """A document whose face rows are already mapped, as `map_ontology` leaves them (stage 4; this
    stage is 26), then this stage. Returns {canonical_key: current-period figure}."""
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [_face_row(key, key, {(Basis.CONSOLIDATED, "current"): amount})
                      for key, amount in pairs]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    doc = NoteSourcedStage().run(doc, ctx)
    out: dict[str, Decimal] = {}
    for li in doc.line_items:
        for ev in li.values.values():
            if ev.value is not None and str(ev.period_label or "") == "current":
                out[li.canonical_key] = ev.value
    return out, ctx


def test_find_1_sums_the_related_party_rows_the_balance_sheet_prints(shipped):
    """FIND 1 IS A SUM OF LINES ALREADY MAPPED, not a caption reader.

    The face never prints an "of which related parties" split inside 其他应收款. What it prints,
    when it discloses related-party receivables at all, is a dedicated ROW — Due from related
    parties, Amounts due from fellow subsidiaries, 应收关联方款项 — and this template has a column
    for each. So Find 1 totals those columns. Giving it aliases would have put a second claimant on
    every one of those captions at equal priority with no label owner, which is the
    unbreakable-alias-tie shape `test_configuration_invariants` holds at zero.

    Find 1 shipped as a dead declaration — `note_source: null`, `statement: null`, `terms: []`,
    `cascade: []`, no aliases, no route, and not among `config.toml`'s 85 `llm_focus_keys`. Nothing
    in the pipeline could fill it, so "the highest of Find 1, Find 2 and Find 3" was a maximum over
    at most two readings.
    """
    got, _ctx = _rp_run(shipped, [
        ("bs_ca__due_from_related_parties_cp", "1000"),
        ("bs_ca__due_from_jvs_and_partnerships", "250"),
        ("bs_nca__due_from_sholder_ltp", "400"),
        ("bs_ca__trade_receivables_related_parties", "700"),
    ])
    assert got.get("sub__rp_find_1") == Decimal("2350")


def test_find_1_excludes_entrusted_loans(shipped):
    """THE SPEC'S OWN EXCLUSION, in its own words: 但不包括委托贷款.

    Expressed by NOT naming the six entrusted-loan columns as terms, so there is no pattern to get
    wrong. TRADE IS NOT EXCLUDED — see the test below; that is wider than the four captions the
    spec lists and is a deliberate instruction on top of them.
    """
    got, _ctx = _rp_run(shipped, [
        ("bs_ca__due_from_related_parties_cp", "1000"),
        ("bs_ca__entrusted_loan_receivables_related_parties_cp", "9999"),
        ("bs_nca__entrusted_loan_receivables_shareholders_ltp", "8888"),
    ])
    assert got.get("sub__rp_find_1") == Decimal("1000"), "an entrusted loan reached Find 1"

    # And a face that prints ONLY entrusted loans leaves Find 1 empty rather than reporting them.
    only_entrusted, _ctx = _rp_run(shipped, [
        ("bs_ca__entrusted_loan_receivables_related_parties_cp", "8000"),
        ("bs_nca__entrusted_loan_receivables_shareholders_ltp", "2000"),
    ])
    assert "sub__rp_find_1" not in only_entrusted


def test_find_1_counts_related_party_trade_receivables(shipped):
    """TRADE IS IN, AND IT IS WIDER THAN THE SPEC'S TEXT.

    The spec names Other receivables, Current portion of long-term receivables, Long-term
    receivables and Loans and advances — none of which is trade. Counting the related-party trade
    receivable as well is a deliberate instruction on top of that, so what FACE_SUM produces is
    "related-party receivables on the face" rather than "the spec's four captions on the face".
    Pinned here because the difference is invisible in the number.
    """
    got, _ctx = _rp_run(shipped, [("bs_ca__trade_receivables_related_parties", "700")])
    assert got.get("sub__rp_find_1") == Decimal("700")


def test_the_trade_and_other_aggregate_is_a_fallback_and_never_double_counts(shipped):
    """"Trade and other receivables — related parties" CONTAINS what FACE_SUM adds up separately.

    Its trade half is `bs_ca__trade_receivables_related_parties` and its other half is
    `bs_ca__due_from_related_parties_cp`, so summing it beside them counts the same balance twice.
    It is therefore a RUNG BELOW rather than a term: `evaluate` skips a rung that resolves nothing,
    so the aggregate is reached only when no specific related-party receivable row was printed —
    the filing whose whole related-party receivable would otherwise be missed.
    """
    # Nothing specific printed: the aggregate answers.
    alone, _ctx = _rp_run(shipped, [
        ("bs_ca__trade_and_other_receivables_related_parties", "2500")])
    assert alone.get("sub__rp_find_1") == Decimal("2500")

    # Both printed: the specific rows win and the aggregate is ignored, so 1,700 and never 3,400.
    both, _ctx = _rp_run(shipped, [
        ("bs_ca__due_from_related_parties_cp", "1000"),
        ("bs_ca__trade_receivables_related_parties", "700"),
        ("bs_ca__trade_and_other_receivables_related_parties", "1700"),
    ])
    assert both.get("sub__rp_find_1") == Decimal("1700"), (
        "the aggregate was summed beside the rows it contains, so the balance is counted twice")


def test_find_1_reaches_the_column_that_selects_between_the_three(shipped):
    """AN ORDERING GAP THAT MADE THE FIX INVISIBLE, pinned so it cannot come back.

    Find 1 is BOTH an arithmetic line with no children of its own — computed by
    `_fill_childless_internal` — and itself a child of the column that selects between the three
    readings. The sweep that enrols "children filled elsewhere" ran BEFORE the pass that creates
    such a row, so the row did not exist to be enrolled: Find 1 computed 1,650 and the column it
    feeds stayed empty. Enrolment now runs again after the compute pass.
    """
    got, ctx = _rp_run(shipped, [
        ("bs_ca__due_from_related_parties_cp", "1000"),
        ("bs_ca__due_from_jvs_and_partnerships", "650"),
    ])
    assert got.get("sub__rp_find_1") == Decimal("1650")
    assert got.get(_RP_PARENT) == Decimal("1650"), (
        "Find 1 has a figure but the column that selects between the three Finds is empty — "
        "the enrolment sweep no longer sees a row created by the compute pass")
    assert any("rung MAX_VALID" in ln for ln in ctx.logs), ctx.logs[-5:]


def test_the_selection_is_a_maximum_over_whichever_finds_answered(shipped):
    """The spec's rule, both ways round, and the case where none answers.

    A maximum and not a sum: the three Finds are three readings of ONE quantity, so summing would
    count the same related-party receivable up to three times. And nothing disclosed anywhere
    leaves the column EMPTY rather than publishing zero — `_apply_terms` returns None for an empty
    base precisely so a total over absent lines does not assert the filing reported nil.
    """
    face_wins, _ = _rp_run(shipped, [
        ("bs_ca__due_from_related_parties_cp", "1000"),
        ("bs_ca__due_from_jvs_and_partnerships", "650"),
        ("sub__rp_find_2", "300"),
    ])
    assert face_wins.get(_RP_PARENT) == Decimal("1650")

    note_wins, _ = _rp_run(shipped, [
        ("bs_ca__due_from_jvs_and_partnerships", "250"),
        ("sub__rp_find_2", "9000"),
    ])
    assert note_wins.get(_RP_PARENT) == Decimal("9000")

    silent, _ = _rp_run(shipped, [("bs_ca__inventories", "777")])
    assert "sub__rp_find_1" not in silent
    assert _RP_PARENT not in silent, "an undisclosed related-party balance published a figure"


# ── the measure suffix: reading a note column other than the primary one ─────────────────────────

def _measure_note(number: str, title: str, rows) -> NotesTable:
    """A mainland two-level note: `项目 | 账面余额 | 坏账准备` under each period. `row_reconstruct`
    gives the primary measure the BARE period label and suffixes every other one, so the allowance
    arrives as `current:allowance`."""
    table = NotesTable(note_number=number, title=title)
    for caption, gross, allowance in rows:
        item = NoteItem(raw_label=caption)
        item.values["g"] = ExtractedValue(
            basis=Basis.CONSOLIDATED, period_label="current",
            value=Decimal(gross), value_raw=Decimal(gross), provenance=Provenance(page_index=9))
        if allowance is not None:
            item.values["a"] = ExtractedValue(
                basis=Basis.CONSOLIDATED, period_label="current:allowance",
                value=Decimal(allowance), value_raw=Decimal(allowance),
                provenance=Provenance(page_index=9))
        table.items.append(item)
    return table


def test_a_part_can_ask_for_the_allowance_column_by_name(shipped):
    """THE CONVENTION EXISTED AND NOTHING COULD REACH IT.

    `row_reconstruct` emits a note's second measure as `"<period>:<slug>"` — "current:allowance" —
    expressly so that "a service that needs the second measure can now ask for it by name", and no
    service could: `select_rows` admits only labels the FACE declares, and a face declares periods,
    not measures. `NoteSource.measure` is that name.

    Both halves are asserted together because each is useless alone: the gross part must keep
    taking the primary measure, and the allowance part must take the suffixed one AND report it
    under the bare period, or the rung that deducts it cannot see it. That last step is what made
    the first version of this dead code — the allowance was found, summed, and filed under
    `current:allowance` where nothing read it.
    """
    from app.services import note_sourced as svc

    by_key = {d.key: d for d in shipped.items}
    note = _measure_note("十二、6", "关联方应收应付款项", [
        ("应收账款：甲公司", "1000", "400"),
        ("应收账款：乙公司", "500", "100"),
    ])

    gross = svc.select_rows(by_key["sub__rp_find_3_gross"], [note], {"current", "prior"}, None)
    allowance = svc.select_rows(by_key["sub__rp_find_3_allowance"], [note],
                                {"current", "prior"}, None)

    assert sum(h.amount for h in gross) == Decimal("1500")
    assert sum(h.amount for h in allowance) == Decimal("500")
    assert {h.period for h in gross} == {"current"}
    assert {h.period for h in allowance} == {"current"}, (
        "the allowance was reported under its suffixed label, so the rung that deducts it in "
        "`current` cannot see it — see this test's docstring")


def test_find_3_is_the_net_of_the_two_columns(shipped):
    """FIND 3 IS COMPUTED, NOT READ. The 关联方应收应付款项 note prints 账面余额 and 坏账准备 and no
    net column, so the 淨金額 the spec asks for is gross LESS allowance.

    Measured on 000709 before this: Find 3 read the primary measure alone and reported
    952,910,390.11 where the note's own net is 554,344,602.47 — overstated by 398,565,787.64, and
    565,528,459.41 of that was one counterparty whose allowance EQUALS its gross, counted at face
    value for a net of nothing.
    """
    doc = DocumentModel(filename="f.pdf")
    doc.notes = [_measure_note("十二、6", "关联方应收应付款项", [
        ("应收账款：甲公司", "1000", "400"),
        ("其他应收款：乙公司", "500", "100"),
    ])]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    doc = NoteSourcedStage().run(doc, ctx)

    assert _figure(doc, "sub__rp_find_3_gross") == Decimal("1500")
    assert _figure(doc, "sub__rp_find_3_allowance") == Decimal("500")
    assert _figure(doc, "sub__rp_find_3") == Decimal("1000"), "Find 3 is not net of the allowance"


def test_a_note_with_no_allowance_column_deducts_nothing(shipped):
    """The allowance is an `adjustment`, so its absence does not kill the rung — a note that prints
    only a balance answers with that balance. `refuse_negative` on the rung catches the opposite
    mistake, an allowance larger than the gross, which would mean the two columns had been read the
    wrong way round."""
    doc = DocumentModel(filename="f.pdf")
    doc.notes = [_measure_note("十二、6", "关联方应收应付款项", [
        ("应收账款：甲公司", "1000", None),
    ])]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    doc = NoteSourcedStage().run(doc, ctx)

    assert _figure(doc, "sub__rp_find_3_allowance") is None
    assert _figure(doc, "sub__rp_find_3") == Decimal("1000")


def test_the_llm_is_told_the_same_rule_the_cascade_applies(shipped):
    """THE ENDPOINT HAS TO BE ABLE TO SEGREGATE THESE TOO.

    `bs_nca__due_from_related_parties_ltp` is one of `config.toml`'s 85 `llm_focus_keys`, its
    `definition` is the only text the model gets for it, and `_llm_holds` means the model's answer
    stands over the cascade's. So a definition that disagrees with the configuration is not
    documentation drift — it is a second, contradictory rule with the authority to win.

    It HAD disagreed, on both of the things this work changed: it said "including entrusted loans
    routed through a bank", which the spec excludes in its own words, and "Excludes trade
    receivables from related parties", which is no longer true. It said nothing about the loss
    allowance at all, which is the distinction that moves this figure by 399 million on one filing.
    """
    definition = {d.key: d for d in shipped.items}[
        "bs_nca__due_from_related_parties_ltp"].definition

    assert "INCLUDING trade receivables" in definition
    assert "EXCLUDES ENTRUSTED LOANS" in definition
    assert "including entrusted loans" not in definition, (
        "the model is told to include entrusted loans, which every Find excludes")
    assert "Excludes trade receivables" not in definition, (
        "the model is told to exclude trade receivables, which every Find now includes")
    for word in ("淨金額", "NET of any loss allowance", "关联方组合"):
        assert word in definition, f"the model is not told about {word!r}"

    # And the two halves must tell it which COLUMN each one is, in the filing's own words, or a
    # model asked for one will answer with the other.
    halves = {d.key: d.definition for d in shipped.items
              if d.key in ("sub__rp_find_3_gross", "sub__rp_find_3_allowance")}
    assert "账面余额" in halves["sub__rp_find_3_gross"]
    assert "坏账准备" in halves["sub__rp_find_3_allowance"]
    assert "Do NOT read 坏账准备" in halves["sub__rp_find_3_gross"]
    assert "POSITIVE" in halves["sub__rp_find_3_allowance"]
