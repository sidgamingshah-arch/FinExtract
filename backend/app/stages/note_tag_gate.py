"""A line that is only disclosed in a note reports ZERO where the face prints no note reference.

WHAT THIS IMPLEMENTS. `LineItemDef.llm_only_if_note_tagged` declares that a note reference printed
beside the row is this line's evidence threshold. Two things follow, and this stage is the second:

  * `stages.map_ontology` spends no provider call on such a row when it carries no note tag — the
    model would be reading the caption and nothing else.
  * The line's value becomes 0 (a numeric output) or "" (a text one), HERE.

ABSENCE IS A FACT, NOT A GAP, which is the whole reason the zero is written rather than the line
left blank. A blank cell says "we did not find it"; a zero says "the filing does not disclose it".
For a line that is only ever disclosed in a note, those are different statements, and only the
second is true when the filing prints no note against it.

THE ZERO IS UNCONDITIONAL, and the cost is real: it overwrites whatever the deterministic tiers read
off the page. A caption tier that matched a figure on a note-less row has that figure replaced. That
is the requested behaviour — the note tag is the authority, not the caption — and it is the one
place in this pipeline where a tier's answer is deliberately discarded rather than preserved, so:

  * `value_raw` KEEPS THE PRINTED FIGURE, as it does for every other substitution here (see
    `map_ontology._apply_prose_value` and `normalize`'s sign flip). The displaced number stays
    auditable against the zero that replaced it.
  * The row carries `note_tag_absent_zeroed:<figure>` naming what was displaced, so a reader sees
    the figure the tier had read rather than having to infer that anything happened.

WHERE IT RUNS, and the position is load-bearing. Every stage that WRITES a figure runs before this
one — `normalize` (8), `note_sourced` (10), `assemble_components` (12), `reconcile` (13) — so a zero
written here is undone by nothing. It runs BEFORE the structural checks and the segmentation, so
they see the zero rather than the figure it replaced: a reconciliation that tied against a number
this stage then removed would report a tie that is no longer true.

A LINE THE FILING NEVER PRINTED AT ALL IS LEFT ALONE. This zeroes a line that HAS a row and whose
row has no note tag. A configured line no row claimed is a different fact — nothing was read, so
nothing is being contradicted — and materialising a zero for it would assert non-disclosure on the
strength of an extraction gap.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models import DocumentModel
from app.core.stage import PipelineContext

_FLAG = "note_tag_absent_zeroed"


def _cited(li) -> bool:
    """Whether the face printed a note reference beside this row.

    Read the way `stages.map_ontology._cited_notes` and `stages.residual` read it — off
    `note_refs`/`note_number` — and NOT off `doc.links`. `link_notes` builds those at stage 9, and
    a reader here would work while a reader in the gate would not; one definition that holds in both
    places is worth more than the convenience.
    """
    for ref in (getattr(li, "note_refs", None) or ()):
        if any(n for n in (*(ref.numbers or ()), *(ref.subrefs or ()))):
            return True
    return bool((getattr(li, "note_number", "") or "").strip())


class NoteTagGateStage:
    name = "note_tag_gate"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        items = getattr(getattr(ctx, "line_items", None), "items", None) or ()
        wanted = {i.key: i for i in items if getattr(i, "llm_only_if_note_tagged", False)}
        if not wanted:
            return doc

        zeroed = kept = 0
        for li in doc.line_items:
            item = wanted.get(li.canonical_key or "")
            if item is None:
                continue
            if _cited(li):
                kept += 1
                continue

            # TEXT OR NUMBER, off the line's own declared output shape. `value_text` and a numeric
            # `value` are mutually exclusive on `ExtractedValue` (its validator refuses both), so
            # the two cases are written into different fields rather than one coerced into the
            # other.
            as_text = str(getattr(item, "output_structure", "value") or "value") != "value"
            for ev in (li.values or {}).values():
                printed = ev.value if ev.value is not None else ev.value_text
                if printed is None and (ev.value_text or "") == "":
                    continue
                if ev.value_raw is None and ev.value is not None:
                    ev.value_raw = ev.value
                if as_text:
                    ev.value = None
                    ev.value_text = ""
                else:
                    ev.value_text = None
                    ev.value = Decimal(0)
                ev.confidence.flags.append(f"{_FLAG}:{printed}")
                zeroed += 1
            if _FLAG not in li.confidence.flags:
                li.confidence.flags.append(_FLAG)

        if zeroed or kept:
            ctx.log(f"note_tag_gate:zeroed={zeroed} kept={kept} "
                    f"lines_declaring_the_flag={len(wanted)} "
                    f"(zeroed = the face printed no note reference, so the line is reported as "
                    f"not disclosed; the displaced figure is on the value's flags)")
        return doc
