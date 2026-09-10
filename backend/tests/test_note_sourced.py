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


def test_two_notes_disclosing_the_same_charge_give_the_parent_ONE_of_them(shipped):
    """THE BUG THIS TEST CAUGHT, and the reason the roll-up reads the PARENT's declaration.

    `is_pl__deprec_and_impairment_oper_exp` declares `rollup: "alternatives"` over twelve children:
    they are twelve places one depreciation charge might be disclosed, not twelve charges. The
    first implementation looked the rollup up among the items that DECLARE a note_source — and a
    parent declares none, so the lookup missed and fell back to `sum`, turning 1,200 and 1,350 into
    2,550. A cost twelve times too large, on a statement that still balances.
    """
    doc, ctx = _run(shipped, [
        NotesTable(note_number="8", title="Research and development expenses",
                   items=[_note_row("Depreciation of property, plant and equipment", "1200")]),
        NotesTable(note_number="9", title="General and administrative expenses",
                   items=[_note_row("Depreciation of property, plant and equipment", "1350")]),
    ])
    assert _figure(doc, "sub__rd_depreciation") == Decimal("1200")
    assert _figure(doc, "sub__ga_depreciation") == Decimal("1350")
    parent = _figure(doc, OPER_EXP)
    assert parent in (Decimal("1200"), Decimal("1350")), parent
    assert parent != Decimal("2550"), "twelve disclosures of one charge were summed"
    # And the alternative that was NOT taken is recorded, so a reviewer can see there was a choice.
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
