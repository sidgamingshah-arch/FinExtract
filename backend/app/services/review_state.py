"""WHICH FIGURES ARE IN REVIEW, AND WHY — one answer for the pipeline and for every reader.

A REVIEW FLAG SITS ON THE THING THAT IS IN DOUBT. A slot is one figure: a line, a basis and a
period (an `ExtractedValue` on a `LineItem`, or a value dict on a served row). Two kinds of doubt
reach it:

* A DOUBT ABOUT ONE FIGURE — it is not on the page it cites, a section that holds it does not add
  up in its column, the rules' figure was settled against the model's pieces of it, a part it was
  computed from is in review. It lives on THAT SLOT only: the slot's own `confidence.flags` carry
  `REVIEW_FLAG` beside the reason.
* A DOUBT ABOUT THE CAPTION OR THE MAPPING — the caption was mapped with low confidence, two
  readings of the concept conflict. It lives on the ROW and means every slot of the row.

The rule, in the order it is asked (`reasons`):

1. the slot carries `REVIEW_FLAG` or `part_in_review:*` — `FIGURE`;
2. the row carries `REVIEW_FLAG` and is not a whole-PDF model row — `CAPTION`, every slot. A model
   row has no caption (the model was asked about the line itself), so its row flag can never be a
   caption doubt: a run stored before this module wrote that flag for one slot, and reading it as
   every slot's was the defect;
3. on a slot that does not carry a score of its own, the row's caption score is below
   `extraction.auto_accept_confidence` — `CAPTION_SCORE`, every such slot;
4. on a slot that carries its own check score (its flags hold the exact flag `MODEL_FLAG`), that
   slot's own score is below the threshold — `FIGURE_SCORE`.

Rules 3 and 4 are for READERS only. The cascades (`stages.note_sourced`) ask `in_review`, which is
flags only (1 and 2): a weak caption score never travels up to a computed line, as before.

AN ANALYST'S EDIT OF A SLOT ANSWERS THAT SLOT'S FIGURE DOUBTS (1 and 4), never a caption doubt (2
and 3) — but only an edit that CHANGED the figure (or a formula of theirs that drives it). A Save
that keeps the machine's figure (a note added, nothing retyped) answers nothing. A revert brings
the review back by itself, since no flag was changed.

A LINE IS LISTED IN REVIEW (`line_weak`) when any of its slots is, or when it is a blank line with
a row-level doubt (the template-declared line nothing determined).
"""
from __future__ import annotations

from typing import Iterable

#: The flag the review queue selects a doubtful figure on.
REVIEW_FLAG = "low_mapping_confidence"
#: On a computed line's slot whose figure rests on a part that is in review: `part_in_review:<part>`.
PART_IN_REVIEW = "part_in_review"
#: On every row and figure the whole-PDF route writes; on a SLOT, the exact flag says the slot
#: carries its own check score (`stages.whole_pdf._SCORE`).
MODEL_FLAG = "whole_pdf"

FIGURE = "figure"
CAPTION = "caption"
CAPTION_SCORE = "caption_score"
FIGURE_SCORE = "figure_score"
#: The reasons an analyst's edit of the slot answers.
_ANSWERED_BY_AN_EDIT = frozenset({FIGURE, FIGURE_SCORE})

#: The slot flags that say WHY a figure is in review, shown beside the reason codes. A prefix list,
#: so a breadcrumb that is not a doubt (`whole_pdf:on_page`, a sign rule) is not shown as one.
REASON_PREFIXES = (
    "whole_pdf:not_found", "whole_pdf:no_text", "whole_pdf:other_reading:",
    "whole_pdf:parts_disagree", "whole_pdf:face_line_not_on_a_statement_page",
    "whole_pdf:column_not_confirmed:",
    "whole_pdf:column_period:", "whole_pdf:column_basis:", "whole_pdf:face_row_on_two_lines:",
    "llm_note_rows_overlap:", "whole_pdf:rules_reading_less_model",
    "whole_pdf:pieces_reach_the_rules_reading", "whole_pdf:rules_reading_in_a_section_that_differs",
    "whole_pdf:rules_split_filled_whole", "whole_pdf:rules_gap_unchecked:",
    f"{PART_IN_REVIEW}:", "not_deducted:", "unallocated_gap:", "residual_plug_suspected:",
)


def _threshold(threshold: float | None) -> float:
    if threshold is not None:
        return float(threshold)
    from app.config import get_settings

    return float(get_settings().extraction.auto_accept_confidence)


def own_score(slot_flags: Iterable[str] | None) -> bool:
    """Whether a slot carries a check score of its own (the whole-PDF route scores each figure)."""
    return MODEL_FLAG in (slot_flags or ())


def is_model_row(flags: Iterable[str] | None, method: str | None) -> bool:
    """Whether the whole-PDF stage wrote this row: its flag, and the model's mapping method — the
    same test as `services.whole_pdf_rules.is_model_row`, on plain values."""
    return MODEL_FLAG in (flags or ()) and str(method or "").lower().endswith("llm")


def flagged(slot_flags: Iterable[str] | None) -> bool:
    """Rule 1: the slot itself says it is in review."""
    flags = slot_flags or ()
    return REVIEW_FLAG in flags or any(str(f).startswith(PART_IN_REVIEW + ":") for f in flags)


def reasons(slot_flags: Iterable[str] | None, slot_mapping, row_flags: Iterable[str] | None,
            row_mapping, row_method: str | None, *, edited: bool = False,
            threshold: float | None = None, scores: bool = True) -> tuple[str, ...]:
    """Why one slot is in review: a tuple of `FIGURE`, `CAPTION`, `CAPTION_SCORE`, `FIGURE_SCORE`
    (empty: it is not). `scores=False` asks the flags only (rules 1 and 2), as the cascades do."""
    slot_flags = tuple(slot_flags or ())
    row_flags = tuple(row_flags or ())
    out: list[str] = []
    if flagged(slot_flags):
        out.append(FIGURE)
    if REVIEW_FLAG in row_flags and not is_model_row(row_flags, row_method):
        out.append(CAPTION)
    if scores:
        limit = _threshold(threshold)
        if own_score(slot_flags):
            if isinstance(slot_mapping, (int, float)) and slot_mapping < limit:
                out.append(FIGURE_SCORE)
        elif isinstance(row_mapping, (int, float)) and row_mapping < limit:
            out.append(CAPTION_SCORE)
    if edited:
        out = [r for r in out if r not in _ANSWERED_BY_AN_EDIT]
    return tuple(out)


def reason_flags(slot_flags: Iterable[str] | None) -> list[str]:
    """The slot's own flags that say why it is in doubt (`REASON_PREFIXES`)."""
    return [str(f) for f in (slot_flags or ()) if str(f).startswith(REASON_PREFIXES)]


# ── the object side: the pipeline's rows ─────────────────────────────────────────────────────────

def _basis_of(ev) -> str:
    b = getattr(ev, "basis", "")
    return str(getattr(b, "value", b) or "")


def slot_of(row, basis: str, period: str):
    """The value `row` holds for `basis`/`period`, or None."""
    for ev in (getattr(row, "values", None) or {}).values():
        if _basis_of(ev) == basis and str(getattr(ev, "period_label", "") or "") == period:
            return ev
    return None


def in_review(row, basis: str, period: str) -> bool:
    """Whether this row's figure for the slot is one a reviewer has been asked to look at, by its
    FLAGS (rules 1 and 2): what the cascades ask before they carry a part's review up to the line
    computed from it. False for a slot with no figure."""
    ev = slot_of(row, basis, period)
    if ev is None or ev.value is None:
        return False
    conf = getattr(row, "confidence", None)
    return bool(reasons(ev.confidence.flags, ev.confidence.mapping,
                        getattr(conf, "flags", None), getattr(conf, "mapping", None),
                        getattr(conf, "method", None), scores=False))


# ── the dict side: a served row (`api.routes.extractions._serialize_rows`) ──────────────────────

def slot_id(value: dict) -> str:
    """`'<basis>/<period>'` — the spelling `edited_slots` records."""
    return f"{value.get('basis') or ''}/{value.get('period_label') or ''}"


_UNKNOWN = object()        # no snapshot to compare with
_NEW_SLOT = object()       # the edit made the slot: the machine had no figure there


def _figure(v):
    """A figure as compared: its number, or its text where it is not one."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return str(v)


def _same_slot(a: dict, b: dict) -> bool:
    return (a.get("basis") or "") == (b.get("basis") or "") and \
        (a.get("period_label") or "") == (b.get("period_label") or "")


def _machine_figure(row: dict, value: dict):
    """The figure the machine extracted for this slot, from the snapshot the first edit of the row
    took (`_original`, `api.routes.documents.edit_document_line_item`)."""
    original = row.get("_original")
    if isinstance(original, dict):
        # Runs edited before the snapshot was a full list: by period label alone.
        label = value.get("period_label")
        return original[label] if label in original else _NEW_SLOT
    if not isinstance(original, list):
        return _UNKNOWN
    values = row.get("values") or []
    at = next((i for i, v in enumerate(values) if v is value), None)
    if at is not None and at < len(original) and isinstance(original[at], dict) \
            and _same_slot(original[at], value):
        return original[at].get("value")
    got = next((o for o in original if isinstance(o, dict) and _same_slot(o, value)), None)
    return _NEW_SLOT if got is None else got.get("value")


def _edited(row: dict, value: dict) -> bool:
    """Whether an analyst ANSWERED this slot's figure: they typed into it, and what they typed
    changed the figure (or a formula of theirs drives it). A Save that keeps the machine's figure —
    a note added, Save pressed with nothing retyped — answers nothing, so the figure stays in
    review, as it did before the review was per slot."""
    slots = row.get("edited_slots")
    if slots is None:
        # A run edited before slots were recorded only has the row-level flag, which meant every
        # slot when it was made (`services.periods.edited_for`).
        typed = bool(row.get("edited"))
    else:
        typed = slot_id(value) in set(slots)
    if not typed:
        return False
    from app.services.formula import stored_formulas

    if slot_id(value) in stored_formulas(row):
        return True
    before = _machine_figure(row, value)
    if before is _UNKNOWN:
        return False
    if before is _NEW_SLOT:
        return True
    return _figure(before) != _figure(value.get("value"))


def _has_figure(value: dict | None) -> bool:
    return isinstance(value, dict) and value.get("value") is not None


def slot_review(row: dict, value: dict | None, threshold: float | None = None) -> tuple[str, ...]:
    """Why the served row's `value` is in review (empty: it is not, or it holds no figure)."""
    if not _has_figure(value):
        return ()
    conf = value.get("confidence") or {}
    return reasons(conf.get("flags"), conf.get("mapping"), row.get("flags"),
                   row.get("mapping_confidence"), row.get("mapping_method"),
                   edited=_edited(row, value), threshold=threshold)


def row_doubt(row: dict, threshold: float | None = None) -> bool:
    """Whether the ROW is in doubt — the caption or the mapping, which means every slot: its review
    flag (not on a model row), or its caption score below the threshold where no figure of it
    carries a score of its own."""
    flags = row.get("flags") or ()
    if REVIEW_FLAG in flags and not is_model_row(flags, row.get("mapping_method")):
        return True
    conf = row.get("mapping_confidence")
    scored = any(own_score((v.get("confidence") or {}).get("flags"))
                 for v in (row.get("values") or ()) if _has_figure(v))
    return (not scored and isinstance(conf, (int, float)) and conf < _threshold(threshold))


def shown_score(row: dict):
    """The ONE score a row-level display shows for a line — the export's Confidence and Conf
    columns, the JSON export's `mapping_confidence`, the grid's row pill, the Export preview: the
    row's own score, or its weakest figure's where a figure carries a score of its own.

    A whole-PDF model row stores its BEST figure's score (`stages.whole_pdf._rows`), so the row is
    weak only when every figure is. A display with one number per line has no place for the other
    year, so it shows the weakest, as it did before the review was per slot: a year in doubt stays
    visible there. On a rules row no figure carries its own score, and this is the row's score."""
    conf = row.get("mapping_confidence")
    own = []
    for v in row.get("values") or ():
        vconf = (v.get("confidence") or {}) if isinstance(v, dict) else {}
        m = vconf.get("mapping")
        if own_score(vconf.get("flags")) and isinstance(m, (int, float)):
            own.append(m)
    if not own:
        return conf
    if isinstance(conf, (int, float)):
        own.append(conf)
    return min(own)


def row_review(row: dict, threshold: float | None = None) -> dict:
    """`{'row': the row-level doubt (every slot), 'slots': ['basis/period', …] in review}`."""
    limit = _threshold(threshold)
    slots = [slot_id(v) for v in (row.get("values") or ())
             if slot_review(row, v, limit)]
    return {"row": row_doubt(row, limit), "slots": list(dict.fromkeys(slots))}


def line_weak(row: dict, threshold: float | None = None) -> bool:
    """Whether the review queue lists this line: any slot in review, or a blank line (no figure)
    with a row-level doubt."""
    limit = _threshold(threshold)
    state = row_review(row, limit)
    if state["slots"]:
        return True
    blank = not any(_has_figure(v) for v in (row.get("values") or ()))
    return blank and state["row"]


def annotate(rows: list[dict], threshold: float | None = None) -> list[dict]:
    """Shallow copies of the served rows, each with `review_slots`, `review_row`, and each value
    with `in_review` and `review_reasons` (the reason codes, then the slot's own reason flags).
    Never stored: it is read again from the flags on every serve, so it cannot go stale after a
    remap, an edit or a revert. The stored `flags` and `mapping_confidence` are served as they
    are."""
    limit = _threshold(threshold)
    out = []
    for row in rows or ():
        if not isinstance(row, dict):
            out.append(row)
            continue
        copy = dict(row)
        values = []
        for v in row.get("values") or ():
            if not isinstance(v, dict):
                values.append(v)
                continue
            why = slot_review(row, v, limit)
            values.append({**v, "in_review": bool(why),
                           "review_reasons": [*why, *reason_flags(
                               (v.get("confidence") or {}).get("flags"))] if why else []})
        if "values" in row:
            copy["values"] = values
        state = row_review(row, limit)
        copy["review_slots"] = state["slots"]
        copy["review_row"] = state["row"]
        # What a row-level display shows for the line (`shown_score`); `mapping_confidence` is
        # served as stored.
        copy["shown_confidence"] = shown_score(row)
        out.append(copy)
    return out
