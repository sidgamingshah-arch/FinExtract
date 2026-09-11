"""`llm_only_if_note_tagged`: the note reference is the line's evidence threshold.

THE CONFIGURED BEHAVIOUR, in two halves. A line declaring this flag says that a note reference
printed beside the row is what makes its figure believable:

  * NO CALL is spent on a row carrying no note tag — the model would be reading the caption and
    nothing else (`stages.map_ontology`, the focus row gate).
  * THE VALUE BECOMES 0, or "" for a text output (`stages.note_tag_gate`).

THE ZERO IS UNCONDITIONAL AND THAT IS THE POINT. It overwrites whatever the deterministic tiers read
off the page, which makes it the one place in this pipeline where a tier's answer is deliberately
discarded rather than preserved — everywhere else the house rule is the opposite, and this file
exists partly so that exception stays visible. What makes it defensible rather than lossy is that
the displaced figure is kept: `value_raw` holds the printed number and the flag names it.

WHY ZERO RATHER THAN BLANK. A blank cell says "we did not find it"; a zero says "the filing does not
disclose it". For a line only ever disclosed in a note, those are different statements, and only the
second is true when no note is printed against it.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, NoteRef, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import LineItemDef, load_line_item_set
from app.services.working_view import build_working_view
from app.stages.note_tag_gate import NoteTagGateStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

KEY = "bs_ca__cash_in_hand_and_at_banks"      # a real `extract` line on the shipped set


def _row(key: str, amount: str | None = "1234", *, note: str | None = None, text: str = ""):
    li = LineItem(source_label="Cash and cash equivalents", role=LineRole.LINE)
    li.canonical_key = key
    if note:
        li.note_number = note
        li.note_refs.append(NoteRef(raw=note, numbers=[note]))
    ev = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                        provenance=Provenance(page_index=2))
    if text:
        ev.value_text = text
    else:
        ev.value = Decimal(amount)
        ev.value_raw = Decimal(amount)
    li.set_value(ev)
    return li


def _set_with_flag(*, key: str = KEY, output_structure: str = "value"):
    """The shipped set with the flag turned on for one line, so the run is otherwise real."""
    raw = json.loads(SEED.read_text(encoding="utf-8"))
    hit = False
    for item in raw["items"]:
        if item["key"] == key:
            item["llm_only_if_note_tagged"] = True
            item["extraction_mode"] = "extract"
            item["output_structure"] = output_structure
            hit = True
    assert hit, f"{key} is not in the shipped set"
    return load_line_item_set(raw, resolve=True)


def _run(cfg, rows):
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=2, statement="balance_sheet")]
    doc.line_items = list(rows)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(cfg)
    ctx.line_items = cfg
    NoteTagGateStage().run(doc, ctx)
    return doc, ctx


def _ev(li):
    return next(iter(li.values.values()))


# ── the declaration ───────────────────────────────────────────────────────────────────────────

def test_the_flag_is_off_by_default_so_no_shipped_line_changes_meaning():
    assert LineItemDef(key="x").llm_only_if_note_tagged is False
    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    on = [i.key for i in shipped.items if i.llm_only_if_note_tagged]
    assert not on, f"the shipped seed turns the flag on for {on}; every figure there now depends " \
                   f"on a note reference being printed"


@pytest.mark.parametrize("mode", ["extract_or_derive", "derive"])
def test_the_flag_is_refused_on_a_line_the_model_is_not_asked_about(mode):
    """A flag that silently did nothing on 41 of the shipped concepts would be worse than a
    message: the author would set it, see no effect, and have nothing to read. So it is refused,
    and the refusal names the field verbatim because the edit door attributes a message to a
    control by finding the field name as a substring."""
    with pytest.raises(ValueError) as exc:
        LineItemDef(key="k", extraction_mode=mode, llm_only_if_note_tagged=True)
    assert "llm_only_if_note_tagged" in str(exc.value)
    assert mode in str(exc.value)


def test_it_is_accepted_on_an_extract_line():
    assert LineItemDef(key="k", extraction_mode="extract",
                       llm_only_if_note_tagged=True).llm_only_if_note_tagged is True


# ── the zeroing ───────────────────────────────────────────────────────────────────────────────

def test_no_note_tag_reports_zero_and_keeps_the_printed_figure():
    """The substitution, and the audit trail that makes it reviewable rather than lossy."""
    cfg = _set_with_flag()
    doc, ctx = _run(cfg, [_row(KEY, "1234")])

    ev = _ev(doc.line_items[0])
    assert ev.value == Decimal(0), "a line with no note tag must report zero, not the caption's read"
    assert ev.value_raw == Decimal("1234"), "the printed figure must stay auditable against the zero"
    assert any(f == "note_tag_absent_zeroed:1234" for f in ev.confidence.flags), ev.confidence.flags
    assert "note_tag_absent_zeroed" in doc.line_items[0].confidence.flags
    assert any("note_tag_gate:zeroed=1" in line for line in ctx.logs), ctx.logs


def test_a_note_tag_leaves_the_figure_alone():
    """The other half, so the flag cannot become "this line is always zero"."""
    cfg = _set_with_flag()
    doc, ctx = _run(cfg, [_row(KEY, "1234", note="12")])

    ev = _ev(doc.line_items[0])
    assert ev.value == Decimal("1234")
    assert not [f for f in ev.confidence.flags if f.startswith("note_tag_absent_zeroed")]
    assert any("kept=1" in line for line in ctx.logs)


def test_a_subreference_counts_as_a_tag():
    """A row citing "12(a)" points at a note as surely as one citing "12" — `NoteRef.subrefs` is
    where that lands, and a reader that only looked at `numbers` would zero the row."""
    cfg = _set_with_flag()
    row = _row(KEY, "1234")
    row.note_refs.append(NoteRef(raw="12(a)", numbers=[], subrefs=["12(a)"]))
    doc, _ctx = _run(cfg, [row])

    assert _ev(doc.line_items[0]).value == Decimal("1234")


def test_a_text_output_reports_empty_string_rather_than_zero():
    """`output_structure` decides which of the two the line reports, and `ExtractedValue` refuses
    text alongside a number — so the two cases are written to different fields rather than one
    being coerced into the other."""
    cfg = _set_with_flag(output_structure="phrase")
    doc, _ctx = _run(cfg, [_row(KEY, None, text="approximately HK$3 million")])

    ev = _ev(doc.line_items[0])
    assert ev.value_text == ""
    assert ev.value is None, "a text line must not report the number 0"


def test_a_line_the_filing_never_printed_is_left_alone():
    """Zeroing here would assert non-disclosure on the strength of an EXTRACTION GAP. Nothing was
    read, so nothing is being contradicted — that is a different fact from "printed with no note"."""
    cfg = _set_with_flag()
    doc, _ctx = _run(cfg, [_row("bs_ca__raw_materials", "999")])

    assert _ev(doc.line_items[0]).value == Decimal("999")
    assert not any(li.canonical_key == KEY for li in doc.line_items), \
        "the gate must not materialise a row for a line no page printed"


def test_a_line_without_the_flag_is_untouched():
    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    doc, _ctx = _run(shipped, [_row(KEY, "1234")])

    assert _ev(doc.line_items[0]).value == Decimal("1234")


# ── the position, which is what makes the zero stick ──────────────────────────────────────────

def test_it_runs_after_every_stage_that_writes_a_figure():
    """A zero written before `normalize`, `note_sourced`, `assemble_components` or `reconcile` would
    be overwritten by them; one written after the structural checks would let a reconciliation
    report a tie against a figure that is no longer there."""
    from app.core.pipeline import default_pipeline

    names = [getattr(s, "name", type(s).__name__) for s in default_pipeline().stages]
    here = names.index("note_tag_gate")
    for writer in ("normalize", "note_sourced", "assemble_components", "reconcile"):
        assert names.index(writer) < here, f"{writer} runs after the gate and would undo the zero"
    for reader in ("structural", "segment"):
        assert names.index(reader) > here, f"{reader} would see the figure the gate removed"


# ── the call that is not spent ────────────────────────────────────────────────────────────────

def test_no_provider_call_is_spent_on_a_row_with_no_note_tag():
    """The first half of the flag. A row whose line declares the threshold and which carries no note
    reference is decided deterministically and never forwarded — the model would be reading the
    caption and nothing else."""
    from app.stages.map_ontology import MapOntologyStage

    class Counts:
        id = "counts"

        def __init__(self):
            self.asked = 0

        def complete_structured(self, *, system, messages, response_schema, **_):
            self.asked += 1
            return response_schema.model_validate({"mappings": []}), {}

    settings = get_settings()
    focus = set(settings.extraction.llm_focus_keys or ())
    if not focus:
        pytest.skip("focus routing is off, so no row reaches the gate under test")

    # THE FOCUS KEY MUST BE ONE THE MODEL IS ACTUALLY ASKED ABOUT, AND CARRY AN ALIAS — discovered
    # rather than assumed. The eight wholes are all withheld from the model anyway, so picking one
    # would make this pass for the wrong reason, proving only that a withheld concept is not asked
    # about. `_never_asked` is the predicate for "withheld", not `extraction_mode` alone: the
    # derived parents now declare `extract` (so a printed subtotal is still read) and are withheld
    # by their TYPE, which is the same test `LineItemDef._coherent` applies to the flag itself.
    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    by_shipped = {i.key: i for i in shipped.items}
    key = next((k for k in sorted(focus)
                if by_shipped.get(k) is not None
                and by_shipped[k]._never_asked() is None
                and any((a or "").strip() for a in (by_shipped[k].aliases or []))), None)
    if key is None:
        pytest.skip("no focus key is a line the model is asked about AND carries an alias")

    cfg = _set_with_flag(key=key)
    by_key = {i.key: i for i in cfg.items}
    alias = next(a for a in by_key[key].aliases if (a or "").strip())

    provider = Counts()
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    row = LineItem(source_label=alias, role=LineRole.LINE)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("500"), value_raw=Decimal("500"),
                                 provenance=Provenance(page_index=0)))
    doc.line_items = [row]

    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(cfg)
    ctx.line_items = cfg
    ctx.settings.extraction.llm_mapping = True
    ctx.registry.register("llm", "counts", lambda: provider)
    ctx.settings.llm.provider = "counts"
    MapOntologyStage().run(doc, ctx)

    assert provider.asked == 0, (
        f"a call was spent on a row for {key}, which declares llm_only_if_note_tagged and carries "
        f"no note reference")
    assert any("note_tag_gate_skipped_calls=" in line for line in ctx.logs), ctx.logs[-6:]
