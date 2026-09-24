"""ONE PRINTED FIGURE CLAIMED BY TWO LINE ITEMS — find them, and let the model break the tie.

THE CASE THIS EXISTS FOR, measured. `sub__ppe_depreciation` and `sub__fixed_asset_depreciation`
both read 366,943,014.10 from the same note on 8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf, because
PP&E's note titles carried 固定资产 — the fixed-assets line's whole subject. Their parent's rung
sums all five asset lines, so the charge was published doubled: 237,254,155.34 against a true
118,627,077.67.

WHY A STAGE AND NOT A CONFIG RULE. Keeping the note-title patterns disjoint fixes the instance and
not the class: any two lines whose vocabulary overlaps on a filing nobody has run will do it again,
and the failure is silent — a doubled figure balances and looks plausible. Two lines claiming one
printed cell is DETECTABLE without knowing anything about the concepts involved, so it is detected
here and the judgement is asked rather than assumed.

AND WHY THE MODEL CANNOT ALREADY SEE IT. The twelve depreciation parts do share one request, so the
model is shown them together — but `build_request` states the contract: "a shared request is still
answerable line by line". Each line carries its own `notes_supplied` and is answered on its own;
nothing asks whether a row may be claimed twice. The parent that adds them is `type: derived`, so
`asked_about` is False and it is never offered at all. The sum where the doubling happens is the one
thing no request contains.

KEEP IN BOTH IS A REAL ANSWER, not a failure to decide. A figure legitimately belongs to two lines
when they are different cuts of the same disclosure — an amount that is both a related-party balance
and an other-receivable, say — and the filing means both. So the question put to the model is
exactly "both, or one of them?", and "both" leaves the document as it was.

WHAT COUNTS AS THE SAME ITEM: the same printed CELL — one page, one box, one amount, in one
(basis, period) slot. Not "the same number": two lines reporting 1,000 from different pages are two
disclosures that happen to agree, and nothing should touch them.

A PARENT AND ITS OWN CHILD ARE NEVER A COLLISION. A cascade or a rollup is meant to carry a child's
figure up; that is the declared arithmetic, not two lines competing. Ancestry is checked through the
configuration rather than guessed from the keys.

WITH NO PROVIDER THIS STAGE CHANGES NOTHING. The collisions are recorded and flagged for review and
every figure stays where it was — the deterministic result is exactly what it was before, which is
what makes the stage safe to have on by default.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel, Field


class SharedFigureDecision(BaseModel):
    """The model's answer: keep the figure on both lines, or on exactly one of them."""

    # "both", or the canonical key of the single line that should keep it. Anything else — an
    # empty string, a key that was not offered — is a decline, and a decline keeps both.
    keep: str = Field(default="", description='"both" or the canonical_key that keeps the figure')
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


TIEBREAK_SYSTEM = (
    "You are a financial-statement analyst reviewing a spread against the filing it came from.\n"
    "ONE printed figure has been mapped onto TWO OR MORE line items. The figure, the caption it "
    "was printed under, its page and the note it belongs to are all given, along with each line "
    "item that claimed it.\n"
    "The arithmetic is not in question and the figure itself is not in doubt — only which line or "
    "lines it belongs on.\n"
    "Answer with `keep`:\n"
    '  * "both" — the figure genuinely belongs on every line that claimed it, because they are '
    "different cuts of the same disclosure and the filing means it to count on each.\n"
    "  * a single canonical_key from the offered list — the figure belongs on that line ALONE and "
    "the others matched it by mistake.\n"
    "Prefer a single key when the lines are alternative names for one concept, or when one line's "
    "subject is contained in another's, because a figure counted twice in a total that adds them "
    "is a misstatement. Prefer \"both\" when each line is a genuinely different measure and a "
    "reader would expect the amount on each.\n"
    "Give a one-sentence rationale and a confidence between 0 and 1."
)


@dataclass
class Collision:
    """One printed cell claimed by more than one line item, in one (basis, period) slot."""

    basis: str
    period: str
    amount: Decimal
    page_index: int | None
    caption: str
    note_number: str
    # (canonical_key, label, definition, index into doc.line_items, the value's dict key)
    claimants: list[tuple[str, str, str, int, str]] = field(default_factory=list)

    @property
    def keys(self) -> list[str]:
        return [c[0] for c in self.claimants]


def _box_key(box, page) -> tuple | None:
    """A printed cell as a comparable tuple. Rounded, because the same cell reaches two lines
    through two code paths whose boxes agree to the page's own precision, not bit for bit."""
    if box is None or page is None:
        return None
    get = (box.get if isinstance(box, dict) else lambda k, d=0.0: getattr(box, k, d))
    return ("cell", int(page), round(float(get("x0", 0.0) or 0.0), 4),
            round(float(get("y0", 0.0) or 0.0), 4),
            round(float(get("x1", 0.0) or 0.0), 4),
            round(float(get("y1", 0.0) or 0.0), 4))


def _cell(ev) -> tuple | None:
    """The printed cell a value came from, off the value's OWN provenance, or None."""
    prov = getattr(ev, "provenance", None)
    if prov is None:
        return None
    return _box_key(getattr(prov, "value_bbox", None) or getattr(prov, "bbox", None),
                    getattr(prov, "page_index", None))


def _trail_cells(li, basis: str, period: str) -> tuple | None:
    """The note rows a NOTE-SOURCED figure was assembled from, as a comparable tuple.

    WHY THIS EXISTS, and it is the whole reason the detector works at all. A figure written by
    `stages.note_sourced` carries NO provenance on the value — measured on
    8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf, where `sub__ppe_depreciation` and
    `sub__fixed_asset_depreciation` both hold 366,943,014.10 with `provenance=None` and
    `page=None`. A cell comparison therefore cannot see them, and they are exactly the class of
    collision worth catching: 98% of a filing's values carry a box, and the 2% that do not are the
    note-sourced lines a cascade then adds together.

    The trail carries what the value lost. `note_sourced._trail_input` records each contributing
    row's note number, caption, amount AND its own provenance, so the rows behind a figure are
    identifiable even though the figure is not. Two lines that summed the same note rows produce
    the same tuple here.

    ONLY THE COUNTED INPUTS. An `alternatives` rollup records the rows it did NOT take, and two
    lines offered the same candidates but taking different ones are not claiming one figure.
    """
    store = getattr(li, "derivation", None) or {}
    entry = store.get(f"{basis}:{period}") or store.get(f"{basis}:{period or ''}")
    if not isinstance(entry, dict):
        return None
    counted = [i for i in (entry.get("inputs") or []) if isinstance(i, dict) and i.get("counted")]
    if not counted:
        return None
    parts: list[tuple] = []
    for inp in counted:
        prov = inp.get("provenance") or {}
        if isinstance(prov, dict):
            cell = _box_key(prov.get("value_bbox") or prov.get("bbox"), prov.get("page_index"))
        else:
            cell = None
        # The row's own box where it has one; its note, caption and amount otherwise — which is
        # still specific enough that two lines matching it summed the same disclosed rows.
        parts.append(cell or ("row", str(inp.get("note", "")), str(inp.get("label", "")),
                              str(inp.get("value", ""))))
    return ("trail", tuple(parts))


def _ancestors(key: str, parent_of: dict[str, str]) -> set[str]:
    """Every key above this one, guarding against a parent cycle a bad edit could introduce."""
    out: set[str] = set()
    cur = parent_of.get(key, "")
    while cur and cur not in out:
        out.add(cur)
        cur = parent_of.get(cur, "")
    return out


def find_collisions(doc, parent_of: dict[str, str] | None = None) -> list[Collision]:
    """Every printed cell that more than one line item claims, in the same slot.

    `parent_of` maps a key to its configured parent; without it no ancestry is known and a
    parent carrying its child's figure would be reported as a collision, so it is required in
    practice and defaulted only for a caller that has no configuration to hand.
    """
    parent_of = parent_of or {}
    groups: dict[tuple, list[tuple[str, str, str, int, str]]] = {}
    meta: dict[tuple, tuple[str, str]] = {}
    for idx, li in enumerate(getattr(doc, "line_items", None) or ()):
        key = str(getattr(li, "canonical_key", "") or "")
        if not key:
            continue
        for vkey, ev in (getattr(li, "values", None) or {}).items():
            amount = getattr(ev, "value", None)
            if amount is None:
                continue
            basis = getattr(getattr(ev, "basis", None), "value", None) or str(
                getattr(ev, "basis", "") or "")
            period = str(getattr(ev, "period_label", "") or "")
            # THE VALUE'S OWN CELL FIRST, then the note rows its trail names. A figure with
            # neither is COMPUTED — a derived parent, a residual remainder, an assembled total —
            # and a computed figure equalling a printed one is arithmetic working, not a contest.
            cell = _cell(ev) or _trail_cells(li, basis, period)
            if cell is None:
                continue
            slot = (basis, period, str(amount), cell)
            groups.setdefault(slot, []).append(
                (key, str(getattr(li, "source_label", "") or ""), "", idx, vkey))
            # THE CAPTION AND NOTE PUT TO THE MODEL. Off the value's provenance where it has
            # some; off the trail's first counted row otherwise, which is where a note-sourced
            # figure keeps them.
            prov = getattr(ev, "provenance", None)
            caption = str(getattr(prov, "text_snippet", "") or "")
            note_no = str(getattr(li, "note_number", "") or "")
            if not caption:
                entry = (getattr(li, "derivation", None) or {}).get(f"{basis}:{period}")
                first = next((i for i in ((entry or {}).get("inputs") or [])
                              if isinstance(i, dict) and i.get("counted")), None)
                if first:
                    caption = str(first.get("label", "") or "")
                    note_no = note_no or str(first.get("note", "") or "")
            meta.setdefault(slot, (caption, note_no))

    out: list[Collision] = []
    for (basis, period, amount, cell), claimants in sorted(groups.items(), key=lambda kv: str(kv[0])):
        distinct = {c[0] for c in claimants}
        if len(distinct) < 2:
            continue
        # A PARENT CARRYING ITS CHILD'S FIGURE IS THE DECLARED ARITHMETIC, not a contest. Drop any
        # claimant that is an ancestor of another claimant; if that leaves fewer than two distinct
        # lines, there was never a collision.
        related = set()
        for k in distinct:
            if _ancestors(k, parent_of) & distinct:
                related.add(k)
        survivors = [c for c in claimants if c[0] not in related]
        if len({c[0] for c in survivors}) < 2:
            continue
        # One entry per key: a concept spread over several carrier rows is one claimant.
        seen: set[str] = set()
        unique = [c for c in survivors if not (c[0] in seen or seen.add(c[0]))]
        caption, note_no = meta[(basis, period, amount, cell)]
        out.append(Collision(basis=basis, period=period, amount=Decimal(amount),
                             page_index=_page_of(cell), caption=caption, note_number=note_no,
                             claimants=unique))
    return out


def _page_of(cell: tuple) -> int | None:
    """The page a contested figure sits on, for the question put to the model.

    A trail-identified figure has one page per contributing ROW, so the first is reported — the
    claimants and the amount identify the contest; the page only tells a reader where to look.
    """
    if not cell:
        return None
    if cell[0] == "cell":
        return int(cell[1])
    for part in (cell[1] or ()):
        if part and part[0] == "cell":
            return int(part[1])
    return None


def _payload(col: Collision, definitions: dict[str, str], locale: str) -> dict:
    return {
        "figure": {
            "amount": str(col.amount),
            "printed_caption": col.caption,
            "page": (col.page_index or 0) + 1,
            "note": col.note_number,
            "basis": col.basis,
            "period": col.period,
        },
        "claimed_by": [
            {"canonical_key": k, "label": label,
             "definition": definitions.get(k, "")}
            for k, label, _d, _i, _v in col.claimants
        ],
        "output_language": locale,
    }


# THE MARKER A FAILED TIE-BREAK CARRIES IN ITS RATIONALE. A constant rather than a bare string in
# two files: `stages.shared_figures` reads it to decide how to log the outcome, and a prefix the two
# sides spell differently is a distinction that silently stops being drawn.
PROVIDER_ERROR = "provider error: "


def resolve(provider, col: Collision, definitions: dict[str, str], *, locale: str = "en",
            max_tokens: int = 400, min_confidence: float = 0.0) -> tuple[str, str, float]:
    """`(keep, rationale, confidence)` — "both" or one offered key.

    A provider that errors, declines, or names a key outside the offered set returns "both", which
    leaves the document untouched. The safe answer is the one that changes nothing.

    BUT THE REASON TRAVELS WITH IT. A failure used to return an EMPTY rationale, so the stage
    recorded and logged it exactly as it records a considered "keep both" — and a duplicate figure
    surviving a failed call became indistinguishable from one the model chose to leave. The error
    now arrives in the rationale, prefixed `PROVIDER_ERROR`, so `doc.shared_figures[].rationale`
    and the stage's log line both say which of the two happened.
    """
    try:
        result, _meta = provider.complete_structured(
            system=TIEBREAK_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(_payload(col, definitions, locale),
                                             ensure_ascii=False, indent=2)}],
            response_schema=SharedFigureDecision, max_tokens=max_tokens,
        )
    except Exception as exc:                # noqa: BLE001 — unreachable provider decides nothing
        # NAMED, NOT SWALLOWED. The decision is unchanged — nothing is deleted on a call that did
        # not answer — but "the provider could not answer" and "the provider said keep both" are
        # opposite facts about a run and must not read the same.
        return "both", f"{PROVIDER_ERROR}{type(exc).__name__}: {exc}"[:300], 0.0
    keep = str(getattr(result, "keep", "") or "").strip()
    rationale = str(getattr(result, "rationale", "") or "")
    conf = float(getattr(result, "confidence", 0.0) or 0.0)
    if conf < min_confidence:
        return "both", rationale, conf
    if keep != "both" and keep not in col.keys:
        # A KEY THAT WAS NOT OFFERED IS NOT AN ANSWER. Treating an unrecognised string as "drop
        # everything else" would let one malformed reply delete every claimant's figure.
        return "both", rationale, conf
    return (keep or "both"), rationale, conf
