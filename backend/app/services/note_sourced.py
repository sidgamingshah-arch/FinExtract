"""Fill a line item from the note rows its own configuration names — for ANY item, from config.

WHY THIS EXISTS, AND WHAT IT REPLACES. `LineItemDef.note_source` says which note a line is read
from and which of that note's rows count: a list of regexes for the note's TITLE, a list for the row
captions that COUNT, and a list for the captions that must be EXCLUDED even when a counting pattern
matched them. Thirteen shipped sub-line items author it, in English, Traditional and Simplified
Chinese — roughly 650 patterns.

NOTHING READ ANY OF THEM. Five concept-specific derivation services used to, and when those were
removed (~2,731 lines, ~562 enumerated entries) nothing took over: a grep for `note_source` across
the whole backend found one hit outside the schema and the edit API, and that hit is a field NAME in
`ontology_projection`'s passthrough list. `OntologyMapping` does not carry the field at all. So
every one of those patterns was authored, shown on the configuration screen, saved, versioned — and
consulted on no run. That is the exact failure this codebase keeps finding, at its largest scale
yet, and it is why the answer to "configure these eight concepts" had to start here rather than with
more configuration.

WHAT THIS IS NOT. It is not a port of those five services. They enumerated concepts: a function per
target, with the note titles and captions written into Python. This reads the DECLARATION, so it
serves the thirteen items that exist and the seventieth nobody has authored yet, with no code change
— which is the whole point of moving the rulebook into configuration.

HOW A FIGURE IS CHOSEN, and the distinction the configuration already makes:

* ``rollup: "sum"``   — the rows are COMPONENTS. Add them. A note that splits depreciation by
  function prints four rows and all four belong on the line.
* ``rollup: "alternatives"`` — the rows are ALTERNATIVE SOURCES FOR ONE FIGURE, never addends. The
  twelve sub-items under ``is_pl__deprec_and_impairment_oper_exp`` are exactly this: twelve places
  the same depreciation charge might be disclosed. Adding them would multiply one cost by twelve.
  The FIRST match in declared ``order`` wins, and the others are recorded in the trail as the
  alternatives that were available but not taken.
* ``rollup: "none"``  — the parenthood carries no arithmetic. The child is filled and left alone.

THE PARENT IS NEVER SILENTLY OVERWRITTEN. A parent the filing PRINTS is the filing's own statement
of the figure, and a note-derived one is an inference from a breakdown; replacing the first with the
second would discard the more authoritative number. So a parent that already carries a figure keeps
it, and the note-derived value is recorded against the child with a flag saying the two coexist.

EVERY FIGURE CARRIES ITS TRAIL, written through ``services.derivation`` — the note number, the row
caption, the page, and the arithmetic — because a figure assembled out of four note rows is
unreviewable otherwise, and the statement inspector already renders that structure with
click-to-source.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.services import derivation

# A pattern an author mistyped must not take the run down, and must not silently match nothing
# either. Both outcomes are reported by `fill`, which returns the refusals alongside the fills.
_FLAGS = re.IGNORECASE


def _compiled(patterns) -> list[tuple[str, re.Pattern | None]]:
    out: list[tuple[str, re.Pattern | None]] = []
    for p in patterns or ():
        try:
            out.append((p, re.compile(p, _FLAGS)))
        except re.error:
            out.append((p, None))
    return out


def _matches_any(text: str, compiled: list[tuple[str, re.Pattern | None]]) -> str | None:
    """The first pattern that matches, or None. Returns the PATTERN so the trail can name it."""
    for raw, rx in compiled:
        if rx is not None and rx.search(text or ""):
            return raw
    return None


def _num(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _basis_of(value) -> str:
    """The basis as the string the trail is keyed on — `Basis` is an enum, and `str()` on it gives
    "Basis.CONSOLIDATED", which keys a slot nothing can look up (see `assemble_components._basis`)."""
    b = getattr(value, "basis", "")
    return str(getattr(b, "value", b) or "")


class NoteRowHit:
    """One note row a line item's declaration selected, with everything the trail needs."""

    __slots__ = ("key", "note_number", "note_title", "caption", "matched_by", "value", "amount",
                 "basis", "period")

    def __init__(self, *, key, note_number, note_title, caption, matched_by, value, amount,
                 basis, period):
        self.key = key
        self.note_number = note_number
        self.note_title = note_title
        self.caption = caption
        self.matched_by = matched_by
        self.value = value
        self.amount = amount
        self.basis = basis
        self.period = period


def select_rows(item, notes, periods: set[str] | None = None) -> list[NoteRowHit]:
    """The note rows THIS item's `note_source` declares, across every note whose title matches.

    Three gates, in the order the declaration reads: the note's title must match, the row's caption
    must match something in `row_caption_any`, and it must match nothing in `row_caption_none`. The
    veto is applied last and unconditionally — its whole purpose is to remove a row a counting
    pattern already claimed, which is how "depreciation" stops picking up "accumulated depreciation"
    and the movement rows of a fixed-asset table.
    """
    src = getattr(item, "note_source", None)
    if src is None:
        return []
    titles = _compiled(getattr(src, "note_title_any", None))
    counts = _compiled(getattr(src, "row_caption_any", None))
    vetoes = _compiled(getattr(src, "row_caption_none", None))
    if not titles or not counts:
        return []

    hits: list[NoteRowHit] = []
    for table in notes or ():
        title = getattr(table, "title", "") or ""
        # The note NUMBER is offered to the title patterns too, because a filing whose note headings
        # were captured without their text still identifies the note by its number.
        if not (_matches_any(title, titles) or _matches_any(str(getattr(table, "note_number", "")),
                                                            titles)):
            continue
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            matched = _matches_any(caption, counts)
            if not matched:
                continue
            if _matches_any(caption, vetoes):
                continue
            for value in (getattr(row, "values", None) or {}).values():
                # A MATRIX COLUMN IS NOT A PERIOD. `ExtractedValue.column_index` is set only for a
                # fact printed in a NAMED COMPONENT column — an industry segment, a class of
                # equity — and those columns are an axis of decomposition, not of time.
                #
                # Measured on laisun.pdf before this guard: the revenue segment note gave
                # `is_pl__sales_revenues` twelve figures keyed `col2` … `col11` alongside
                # `current`/`prior`, and the value that landed in the CURRENT slot was one
                # segment's revenue (2,609,259) rather than the total the face prints
                # (4,995,768). A per-segment figure published as the year's revenue, on a line
                # that then reconciles against nothing.
                if getattr(value, "column_index", None) is not None:
                    continue
                # AND THE COLUMN MUST BE ONE THE STATEMENTS USE. A note prints columns that are not
                # periods at all: measured on suncreate.pdf, the 营业收入和营业成本 note prints revenue
                # and COST side by side, and the cost column arrived as a slot named
                # `current:cost` carrying 2,239,996,631.60 onto the REVENUE line. `periods` is the
                # set the face declares — see the stage, which derives it from the document.
                label = str(getattr(value, "period_label", "") or "")
                if periods is not None and label not in periods:
                    continue
                amount = _num(getattr(value, "value", None)
                              if getattr(value, "value", None) is not None
                              else getattr(value, "value_raw", None))
                if amount is None:
                    continue
                hits.append(NoteRowHit(
                    key=item.key, note_number=str(getattr(table, "note_number", "")),
                    note_title=title, caption=caption, matched_by=matched,
                    value=value, amount=amount,
                    basis=_basis_of(value),
                    period=str(getattr(value, "period_label", "") or "")))
    return hits


def _trail_input(hit: NoteRowHit, *, counted: bool) -> dict:
    return {
        # WHERE IT WAS PRINTED, off the note row itself.
        "label": hit.caption,
        "note": hit.note_number,
        "value": str(hit.amount),
        "provenance": derivation._json_safe_provenance(getattr(hit.value, "provenance", None)),
        # WHY IT WAS SELECTED — the author's own pattern, so a wrong selection is traceable to the
        # line of configuration that made it rather than to "the engine".
        "excerpt": f"note '{hit.note_title}' row matched /{hit.matched_by}/",
        "deducted": False,
        "counted": counted,
    }


def resolve(hits: list[NoteRowHit], rollup: str) -> dict[tuple[str, str], tuple[Decimal, list[dict]]]:
    """Per (basis, period): the figure this item's rows come to, and the trail that explains it.

    `sum` adds them. `alternatives` takes the FIRST and records the rest as available-but-not-taken,
    because those rows are twelve disclosures of one charge rather than twelve charges — summing
    them would multiply a cost by the number of places the filing happened to mention it.
    """
    by_slot: dict[tuple[str, str], list[NoteRowHit]] = {}
    for h in hits:
        by_slot.setdefault((h.basis, h.period), []).append(h)

    out: dict[tuple[str, str], tuple[Decimal, list[dict]]] = {}
    for slot, rows in by_slot.items():
        if rollup == "alternatives":
            taken, rest = rows[0], rows[1:]
            inputs = [_trail_input(taken, counted=True)]
            inputs += [_trail_input(r, counted=False) for r in rest]
            out[slot] = (taken.amount, inputs)
        else:
            total = sum((r.amount for r in rows), Decimal(0))
            out[slot] = (total, [_trail_input(r, counted=True) for r in rows])
    return out


def method_of(rollup: str, count: int) -> str:
    if rollup == "alternatives":
        return f"note_sourced:first_of_{count}_alternatives"
    return f"note_sourced:sum_of_{count}_rows"


def trail(*, rollup: str, item_label: str, amount: Decimal, inputs: list[dict]) -> dict:
    counted = [i for i in inputs if i.get("counted")]
    formula = (" + ".join(i["label"] for i in counted) if rollup != "alternatives"
               else (counted[0]["label"] if counted else None))
    return derivation.build(
        method=method_of(rollup, len(inputs)),
        formula=formula, inputs=inputs, result=amount,
        flags=[f"note_rows:{len(counted)}"]
             + ([f"alternatives_not_taken:{len(inputs) - len(counted)}"]
                if len(inputs) > len(counted) else []))


def bad_patterns(item) -> list[str]:
    """Patterns on this item that do not compile, named so a refusal points at the author's line.

    Reported rather than raised: one mistyped regex on one sub-line item must not take down a run
    over a 300-page filing, and must not silently match nothing either — which is what a bare
    try/except around the whole selection would do.
    """
    src = getattr(item, "note_source", None)
    if src is None:
        return []
    out: list[str] = []
    for field in ("note_title_any", "row_caption_any", "row_caption_none"):
        for raw, rx in _compiled(getattr(src, field, None)):
            if rx is None:
                out.append(f"{item.key}.note_source.{field}: /{raw}/")
    return out


# ── resolving what the MODEL cited ────────────────────────────────────────────────────────────

def resolve_sources(sources, notes) -> tuple[list[dict], list[dict]]:
    """Match each citation the model gave against the extracted rows. Returns (resolved, unresolved).

    WHY THIS EXISTS RATHER THAN TRUSTING THE CITATION. The candidates offered to the model are
    suggestions, and it may answer past them — which is what lets a caption reach the concept its
    section did not predict. The price is that such an answer is only worth the printed row behind
    it, so the model names the row and THIS resolves it: the page, the figure and the note all come
    off the extracted row, never from the model, which was given no page index and no bbox.

    MATCHING IS DELIBERATELY FORGIVING ON PUNCTUATION AND STRICT ON WORDS. A filing prints
    "Depreciation of property, plant and equipment^" and a model quoting it may drop the footnote
    marker or the comma; neither changes which row is meant. A paraphrase does, so the words
    themselves must be there — containment either way, after the punctuation is stripped.

    A CITATION THAT RESOLVES TO NOTHING IS RETURNED AS UNRESOLVED, not dropped and not believed.
    The caller keeps the mapping and flags it, because "the model cited a row we cannot find" and
    "the model cited nothing" are different failures and only one of them is the model's.
    """
    import re as _re

    def norm(text: str) -> str:
        return _re.sub(r"[^0-9a-z一-鿿]+", "", (text or "").lower())

    rows: list[tuple[str, str, object, object]] = []
    for table in notes or ():
        number = str(getattr(table, "note_number", "") or "")
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            if caption:
                rows.append((number, caption, row, table))

    resolved: list[dict] = []
    unresolved: list[dict] = []
    for ref in sources or ():
        want_note = str(getattr(ref, "note", "") or "").strip()
        want_cap = norm(getattr(ref, "caption", ""))
        quote = (getattr(ref, "quote", "") or "").strip()
        hit = None
        if want_cap:
            for number, caption, row, table in rows:
                if want_note and want_note not in number and number not in want_note:
                    continue
                got = norm(caption)
                if got and (want_cap in got or got in want_cap):
                    hit = (number, caption, row, table)
                    break
        if hit is None:
            # THE PROSE CASE. A figure stated in a footnote belongs to no row, so there is nothing
            # to match a caption against — the note is still identified, and the quote is carried
            # so a reviewer can find the sentence. Reported as unresolved because no page or figure
            # was recovered, which is exactly what a reviewer needs to know.
            unresolved.append({"note": want_note,
                               "caption": getattr(ref, "caption", ""),
                               "quote": quote,
                               "why": ("no extracted row in that note matches the caption — it may "
                                       "be stated in prose, which carries no row")})
            continue
        number, caption, row, table = hit
        figures = {}
        prov = None
        for ev in (getattr(row, "values", None) or {}).values():
            if getattr(ev, "column_index", None) is not None:
                continue
            if getattr(ev, "value", None) is None:
                continue
            figures[str(getattr(ev, "period_label", "") or "?")] = str(ev.value)
            if prov is None:
                prov = derivation._json_safe_provenance(getattr(ev, "provenance", None))
        resolved.append({"note": number, "title": getattr(table, "title", "") or "",
                         "caption": caption, "figures": figures, "provenance": prov,
                         "quote": quote})
    return resolved, unresolved
