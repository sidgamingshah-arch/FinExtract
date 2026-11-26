"""WHAT A PART SAYS ABOUT ITSELF REACHES THE LINE IT IS ADDED INTO — and the configured cascades
treat a model's figure as they treat the printed one (`stages.note_sourced`).

Three findings of the audit, each about the cascade rather than the reading:

  * part_review_not_on_cp_and_orphan. A Level 3 figure not printed on the page it cited went to
    review on its own row, while Securities (CP) and LTP, computed from it, read as settled. Now
    every computed line whose figure rests on a part in review is in review too, naming the part,
    slot by slot, through every line in between (the Level 3 total, the overshoot residual).
  * face_fallback. CP's FROM_THE_FACE rung reads the printed face row alone and deducts no Level 3.
    Whether it should is the spec owner's decision, so the figure does not move — but where the
    filing prints a Level 3 figure for the slot, the slot goes to review naming it.
  * outranks_printed_never_applies. A rung that `outranks_printed` (ST Bank Loans Payable's
    NOTE_SPLIT of PetroChina's 'Short-term borrowings' 138,783 into 45,955 + 92,828 of current
    portion) outranks the printed row on the rules route, but deferred to ANY model answer — so a
    model that read the same printed 138,783 kept it, while CPLTD Bank filled the 92,828 again.
    Now the rung outranks a model figure that IS the printed whole it splits, on both model routes;
    any other model answer still stands.

No model is called: the whole-PDF case runs the real pipeline on a small synthetic filing with the
shipped stand-in answering from a SCRIPT (`tests.whole_pdf_standin`); the others run the note stage
on rows built as each route writes them.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import fitz
import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.periods import concept_value
from app.stages.note_sourced import NoteSourcedStage
from tests.whole_pdf_standin import rows_of, run_whole_pdf, script_figure, slot

REPO = pathlib.Path(__file__).resolve().parents[2]
SEED = REPO / "backend" / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"

CP = "bs_ca__secur_and_other_fincl_assets_cp"
LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
FVTPL = "sub__fa_cp_fvtpl_note_total"
LTP_FVTPL = "sub__ltp_fvtpl_note_total"
FACE_CP = "sub__cp_face_trading_fincl_assets"
L3_PRINTED = "sub__fa_cp_level_3_printed_total"
L3 = "sub__fa_cp_level_3_total"
RESIDUAL = "sub__fa_cp_intermediate_residual"
ST_BANK = "bs_cl__st_bank_loans_payable"
CPLTD = "bs_cl__cpltd_bank"
REVIEW = "low_mapping_confidence"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


# ── a part in review puts the line it makes in review ──────────────────────────────────────────

def _pdf(pages) -> bytes:
    d = fitz.open()
    for lines in pages:
        p = d.new_page()
        y = 72
        for t in lines:
            p.insert_text((40, y), t, fontsize=9)
            y += 16
    return d.tobytes()


#: A face, its FVTPL note and a fair-value hierarchy, in RMB'000 (the audit's sim_cp shape).
FILING = _pdf([
    ["Annual report 2024"],
    ["CONSOLIDATED STATEMENT OF FINANCIAL POSITION", "RMB'000          2024      2023",
     "Current assets", "Financial assets at fair value through profit or loss   1,000   900",
     "Cash and cash equivalents   2,000   1,800", "Total current assets   3,000   2,700",
     "Non-current assets", "Financial assets at fair value through profit or loss   800   700",
     "Total non-current assets   800   700", "Total assets   3,800   3,400"],
    ["NOTES TO THE CONSOLIDATED FINANCIAL STATEMENTS",
     "20. FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS   2024   2023",
     "Listed equity investments   600   500", "Structured deposits   400   400",
     "Total   1,000   900", "Non-current: unlisted funds   800   700"],
    ["46. FAIR VALUE HIERARCHY  As at 31 December 2024", "Level 1   Level 2   Level 3   Total",
     "Financial assets at FVTPL   600   0   300   900",
     "Structured deposits   0   0   200   200",
     "Total financial assets   600   0   500   1,100"],
])


@pytest.fixture(scope="module")
def doubtful_level_3(tmp_path_factory):
    """Level 3 given as 1,200, cited on the hierarchy page, which prints no such figure."""
    script = {"units": "RMB'000", "lines": {
        FACE_CP: [script_figure("1000", page=2, caption="Financial assets at fair value through "
                                                        "profit or loss")],
        FVTPL: [script_figure("1000", page=3, caption="Total")],
        LTP_FVTPL: [script_figure("800", page=3, caption="Non-current: unlisted funds")],
        L3_PRINTED: [script_figure("1200", page=4, caption="Total financial assets")]}}
    with pytest.MonkeyPatch.context() as mp:
        return run_whole_pdf(mp, tmp_path_factory.mktemp("doubtful"), FILING, script)


def _slot_flags(doc, key, basis="consolidated", period="current") -> list[str]:
    _row, ev = slot(doc, key, basis, period)
    return [] if ev is None else list(ev.confidence.flags)


def test_the_doubtful_level_3_is_in_review_on_its_own_row(doubtful_level_3):
    doc, _ctx, _rec = doubtful_level_3
    assert REVIEW in _slot_flags(doc, L3_PRINTED)
    assert "whole_pdf:not_found" in _slot_flags(doc, L3_PRINTED)


def test_cp_and_ltp_computed_from_it_are_in_review_and_name_what_they_rest_on(doubtful_level_3):
    """CP_ZERO publishes 0 and LTP_P1 takes the 200 overshoot out of 800: both rest on the doubtful
    1,200, through the Level 3 total and the residual, and both now say so."""
    doc, ctx, _rec = doubtful_level_3
    _row, cp = slot(doc, CP)
    _row, ltp = slot(doc, LTP)
    assert cp is not None and Decimal(str(cp.value)) == 0
    assert ltp is not None and Decimal(str(ltp.value)) == 600
    assert f"part_in_review:{L3_PRINTED}" in _slot_flags(doc, L3)
    assert f"part_in_review:{L3}" in _slot_flags(doc, RESIDUAL)
    for key in (CP, LTP):
        flags = _slot_flags(doc, key)
        assert REVIEW in flags and f"part_in_review:{RESIDUAL}" in flags, (key, flags)
        # The row names the slot in a breadcrumb; it carries no bare review flag, which would
        # put the row's other slots in review too (`services.review_state`).
        assert any(f"part_in_review:{RESIDUAL}:consolidated:current" in r.confidence.flags
                   for r in rows_of(doc, key))
        assert not any(REVIEW in r.confidence.flags for r in rows_of(doc, key))
    assert any(f"note_sourced:{CP}[consolidated:current]: goes to review" in line
               for line in ctx.logs)


def test_a_part_that_is_not_in_review_leaves_the_line_settled(tmp_path):
    """Every part confirmed on its page: CP is computed from them and carries no review flag."""
    script = {"units": "RMB'000", "lines": {
        FACE_CP: [script_figure("1000", page=2, caption="Financial assets at fair value through "
                                                        "profit or loss")],
        FVTPL: [script_figure("1000", page=3, caption="Total")]}}
    with pytest.MonkeyPatch.context() as mp:
        doc, _ctx, _rec = run_whole_pdf(mp, tmp_path, FILING, script)
    _row, cp = slot(doc, CP)
    assert cp is not None and Decimal(str(cp.value)) > 0
    assert REVIEW not in _slot_flags(doc, CP)
    assert not any(f.startswith("part_in_review:") for f in _slot_flags(doc, CP))


# ── the note stage on rows as each route writes them ──────────────────────────────────────────

def _value(amount: str, period: str, page: int, flags=()):
    ev = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period, value=Decimal(amount),
                        value_raw=Decimal(amount), provenance=Provenance(page_index=page))
    ev.confidence.flags.extend(flags)
    return ev


def _row(key: str, caption: str, current: str, prior: str | None = None, *, page: int = 10,
         method: str | None = None, whole_pdf: bool = False, note: str | None = None) -> LineItem:
    row = LineItem(source_label=caption, canonical_key=key, note_number=note)
    row.values["c"] = _value(current, "current", page)
    if prior is not None:
        row.values["p"] = _value(prior, "prior", page)
    if method:
        row.confidence.method = method
    if whole_pdf:
        row.confidence.flags.append("whole_pdf")
    return row


def _note(number: str, title: str, rows, page: int = 279) -> NotesTable:
    items = []
    for caption, (current, prior) in rows:
        item = NoteItem(raw_label=caption)
        item.values["c"] = _value(current, "current", page)
        item.values["p"] = _value(prior, "prior", page)
        items.append(item)
    return NotesTable(note_number=number, title=title, items=items, source_pages=[page],
                      source_text=title)


def _run(shipped, rows, notes=()) -> tuple[DocumentModel, PipelineContext]:
    doc = DocumentModel(filename="x.pdf")
    doc.notes = list(notes)
    doc.line_items = list(rows)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx), ctx


def _figure(doc, key, period="current"):
    rows = []
    for li in doc.line_items:
        if li.canonical_key != key:
            continue
        row = li.model_dump(mode="json")
        row["values"] = list((row.get("values") or {}).values())
        rows.append(row)
    got = concept_value(rows, "consolidated", period)
    return None if got is None else Decimal(str(got)).quantize(Decimal("1"))


def _flags_of(doc, key) -> list[str]:
    return [f for li in doc.line_items if li.canonical_key == key for f in li.confidence.flags]


def test_the_face_fallback_names_the_level_3_it_does_not_deduct(shipped):
    """No Find 1 part: CP answers from the face (FROM_THE_FACE) — 344,135 — while the filing prints a
    Level 3 total of 300,000 for the slot. The figure does not move; the slot goes to review with
    the Level 3 figure named. On the rules route, rows as the extraction writes them."""
    face = _row(FACE_CP, "Financial assets at fair value through profit or loss", "344135")
    level3 = _row(L3_PRINTED, "Total financial assets", "300000")
    doc, ctx = _run(shipped, [face, level3])
    assert _figure(doc, CP) == Decimal("344135")
    assert "cascade_rung:FROM_THE_FACE" in _flags_of(doc, CP)
    cp = next(li for li in doc.line_items if li.canonical_key == CP)
    slot_flags = next(ev.confidence.flags for ev in cp.values.values())
    assert f"not_deducted:{L3}:300000" in slot_flags and REVIEW in slot_flags
    # On the row, the breadcrumb that names the slot; no bare review flag.
    assert f"not_deducted:{L3}:300000:consolidated:current" in cp.confidence.flags
    assert REVIEW not in cp.confidence.flags
    assert any("does not deduct" in line and "CP_INTERMEDIATE" in line for line in ctx.logs)


def test_the_face_fallback_with_no_level_3_says_nothing(shipped):
    face = _row(FACE_CP, "Financial assets at fair value through profit or loss", "344135")
    doc, _ctx = _run(shipped, [face])
    assert _figure(doc, CP) == Decimal("344135")
    assert not any(f.startswith("not_deducted:") for f in _flags_of(doc, CP))
    assert REVIEW not in _flags_of(doc, CP)


def test_cp_zero_reaches_level_3_through_the_residual_and_is_not_flagged(shipped):
    """CP_ZERO's 0 is Level 3 accounted for (the overshoot goes to LTP), not a rung without it."""
    fvtpl = _row(FVTPL, "Total", "1000")
    level3 = _row(L3_PRINTED, "Total financial assets", "1200")
    doc, _ctx = _run(shipped, [fvtpl, level3])
    assert _figure(doc, CP) == 0
    assert "cascade_rung:CP_ZERO" in _flags_of(doc, CP)
    assert not any(f.startswith("not_deducted:") for f in _flags_of(doc, CP))


NOTE_29 = [
    ("Short-term borrowings excluding current portion of long-term borrowings", ("45955", "38979")),
    ("Current portion of long-term borrowings", ("92828", "109801")),
    ("Total", ("138783", "148780")),
]


@pytest.mark.parametrize("whole_pdf", [False, True], ids=["line_item_llm", "whole_pdf"])
def test_an_outranking_rung_takes_over_a_model_figure_that_is_the_printed_whole(shipped,
                                                                              whole_pdf):
    """The model answered ST Bank Loans Payable with the face's 138,783 — the printed row note 29
    splits. As on the rules route, the rung outranks it: 45,955, and CPLTD Bank's 92,828 beside it
    add up to the printed row, so the section does not count the current portion twice."""
    model = _row(ST_BANK, "Short-term borrowings", "138783", "148780", page=227, method="llm",
                 whole_pdf=whole_pdf, note="29")
    doc, ctx = _run(shipped, [model], [_note("29", "BORROWINGS", NOTE_29)])
    assert _figure(doc, ST_BANK) == Decimal("45955")
    assert _figure(doc, ST_BANK, "prior") == Decimal("38979")
    assert _figure(doc, CPLTD) == Decimal("92828")
    assert _figure(doc, ST_BANK) + _figure(doc, CPLTD) == Decimal("138783")
    assert "cascade_over_model_printed:NOTE_SPLIT" in model.confidence.flags
    assert model.values["c"].value_raw == Decimal("138783")
    assert any("the model answered with the printed row rung NOTE_SPLIT splits" in line
               for line in ctx.logs)


@pytest.mark.parametrize("whole_pdf", [False, True], ids=["line_item_llm", "whole_pdf"])
def test_a_model_figure_that_is_its_own_reading_still_stands(shipped, whole_pdf):
    """A model answer that is not the printed whole is the model's reading, and the cascade does
    not overrule it — the non-interference rule, unchanged."""
    model = _row(ST_BANK, "Short-term borrowings", "45000", "38000", page=227, method="llm",
                 whole_pdf=whole_pdf, note="29")
    doc, _ctx = _run(shipped, [model], [_note("29", "BORROWINGS", NOTE_29)])
    assert _figure(doc, ST_BANK) == Decimal("45000")
    assert any(f.startswith("cascade_deferred_to_llm:NOTE_SPLIT") for f in model.confidence.flags)
