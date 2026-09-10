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
    """The whole point, on the shipped declaration for the R&D note."""
    doc, ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200")])])
    assert _figure(doc, "sub__rd_depreciation") == Decimal("1200")
    assert any("sub__rd_depreciation" in line for line in ctx.logs)


def test_rows_inside_one_note_are_added_because_they_are_components(shipped):
    """A note that splits depreciation across assets prints several rows, and the note's
    depreciation is their sum."""
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200"),
               _note_row("Depreciation of right-of-use assets", "300")])])
    assert _figure(doc, "sub__rd_depreciation") == Decimal("1500")


def test_the_veto_removes_a_row_a_counting_pattern_already_claimed(shipped):
    """`row_caption_none` exists for exactly this: "accumulated depreciation" and the movement rows
    of a fixed-asset table match "depreciation" and are not the year's charge. If the veto were
    applied before the counting patterns, or not at all, the figure would be wrong rather than
    absent — and a wrong figure that still ties is the failure nobody sees."""
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200"),
               _note_row("Accumulated depreciation", "9999"),
               _note_row("Exchange difference", "7"),
               _note_row("Disposals", "45")])])
    assert _figure(doc, "sub__rd_depreciation") == Decimal("1200")


def test_a_row_matching_no_counting_pattern_is_not_read(shipped):
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Staff costs", "5000")])])
    assert _figure(doc, "sub__rd_depreciation") is None


def test_a_note_whose_title_does_not_match_is_not_read(shipped):
    """The title gate is what stops the R&D declaration reading the inventories note."""
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="13", title="Inventories",
        items=[_note_row("Depreciation of property, plant and equipment", "1200")])])
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
    doc.notes = [
        NotesTable(note_number="8", title="Research and development expenses",
                   items=[_note_row("Depreciation of property, plant and equipment", "1200")]),
        NotesTable(note_number="9", title="General and administrative expenses",
                   items=[_note_row("Depreciation of property, plant and equipment", "1350")]),
    ]
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
    doc, ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200")])],
        rows=[printed])

    assert _figure(doc, OPER_EXP) == Decimal("5000")
    row = next(li for li in doc.line_items if li.canonical_key == OPER_EXP)
    assert any("differs_from_printed" in f for f in row.confidence.flags), row.confidence.flags
    assert any("kept over" in line for line in ctx.logs)


def test_the_trail_names_the_note_the_row_and_the_pattern_that_matched(shipped):
    """A figure assembled out of note rows is unreviewable without saying which rows. The trail is
    written through `services.derivation`, which the statement inspector already renders with
    click-to-source."""
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200"),
               _note_row("Depreciation of right-of-use assets", "300")])])
    row = next(li for li in doc.line_items if li.canonical_key == "sub__rd_depreciation")
    assert row.derivation, "no trail was recorded"
    trail = next(iter(row.derivation.values()))
    counted = [i for i in trail["inputs"] if i.get("counted")]
    assert len(counted) == 2
    assert {i["note"] for i in counted} == {"8"}
    assert all(i["provenance"] for i in counted), "the trail lost the page it came from"
    # The author's own pattern is named, so a wrong selection is traceable to the configuration
    # line that made it rather than to "the engine".
    assert all("matched /" in i["excerpt"] for i in counted)
    assert str(trail["result"]) == "1500"


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
    target.note_source = NoteSource.model_construct(
        note_title_any=["general"], row_caption_any=["depreciation", "([unclosed"],
        row_caption_none=[])

    doc = DocumentModel(filename="f.pdf")
    doc.notes = [NotesTable(note_number="9", title="General and administrative expenses",
                            items=[_note_row("Depreciation of fixed assets", "1350")])]
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


def test_a_concept_the_configuration_marks_evidence_only_is_not_filled_from_a_note(shipped):
    """THE SET'S OWN RULE, enforced: "Notes are evidence for a face amount, never an independent
    source of one, unless note_use is decomposition_allowed" (`global_rules.face_only_default`).

    `notes__contingent_liabilities` is one of the eight focus concepts and is `evidence_only`, so a
    note may corroborate it and must not supply it. Asserted here because the rule lives in prose
    in the configuration, and prose is not enforcement — and because the refusal must be LOUD: a
    line left empty for a stated reason and a line left empty because nothing matched are
    different facts, and only one of them is a decision.
    """
    from app.schemas.line_items import NoteSource

    edited = shipped.model_copy(deep=True)
    by_key = {i.key: i for i in edited.items}
    assert by_key["notes__contingent_liabilities"].note_use == "evidence_only"

    # Give it a child that WOULD match, so the refusal is the only thing standing in the way.
    child = by_key["sub__cos_depreciation"].model_copy(deep=True)
    child.key = "sub__probe_guarantees"
    child.parent = "notes__contingent_liabilities"
    child.note_source = NoteSource(note_title_any=["contingent"],
                                   row_caption_any=["guarantee"], row_caption_none=[])
    edited.items.append(child)

    doc = DocumentModel(filename="f.pdf")
    doc.notes = [NotesTable(note_number="31", title="Contingent liabilities",
                            items=[_note_row("Corporate guarantees given", "8000")])]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = edited
    doc = NoteSourcedStage().run(doc, ctx)

    # The CHILD is still filled — the selection worked, and the trail is worth having.
    assert _figure(doc, "sub__probe_guarantees") == Decimal("8000")
    # The PARENT is not, and the log says why in the configuration's own terms.
    assert _figure(doc, "notes__contingent_liabilities") is None
    assert any("REFUSED as a note source" in line and "evidence_only" in line
               for line in ctx.logs), ctx.logs


def test_the_seven_concepts_that_permit_decomposition_are_not_blocked_by_the_gate(shipped):
    """The gate must refuse only what the configuration marks, or it would quietly disable the
    mechanism for the other seven focus concepts."""
    permits = {i.key: i.note_use for i in shipped.items}
    assert permits["is_pl__deprec_and_impairment_oper_exp"] == "decomposition_allowed"
    assert permits["is_pl__deprec_and_impairment_cos"] == "decomposition_allowed"
    # And the shipped depreciation fill still happens, which is the same assertion end to end.
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200")])])
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
    doc, ctx = _run(shipped, [
        NotesTable(note_number="8", title="Research and development expenses",
                   items=[_note_row("Depreciation of property, plant and equipment", "1200")]),
        NotesTable(note_number="9", title="General and administrative expenses",
                   items=[_note_row("Depreciation of property, plant and equipment", "1350")]),
    ])
    assert _figure(doc, OPER_EXP) == Decimal("2550"), "P1 did not sum the disclosed subset"
    assert any("rung P1" in line for line in ctx.logs), ctx.logs


def test_an_absent_any_of_term_does_not_kill_the_rung(shipped):
    """P1's four terms are all `any_of`: a filing disclosing one of the four operating-expense
    notes still has a sum. A `required` reading would refuse the rung and fall through to a
    materially different provenance."""
    doc, ctx = _run(shipped, [NotesTable(
        note_number="9", title="General and administrative expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1350")])])
    assert _figure(doc, OPER_EXP) == Decimal("1350")
    assert any("rung P1" in line for line in ctx.logs)


def test_the_trail_names_which_rung_answered(shipped):
    """A charge that came from "total less the cost-of-sales share" rather than from the four
    operating-expense notes has a materially different provenance, and a reviewer cannot see that
    in the number. The rung is recorded on the figure."""
    doc, _ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200")])])
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
    doc, ctx = _run(shipped, [NotesTable(
        note_number="8", title="Research and development expenses",
        items=[_note_row("Depreciation of property, plant and equipment", "1200")])],
        rows=[printed])
    assert _figure(doc, OPER_EXP) == Decimal("9000")
    assert any("kept over cascade" in line for line in ctx.logs), ctx.logs
