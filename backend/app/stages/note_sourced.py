"""Fill every line item that declares where in the notes its figure lives.

POSITION IN THE PIPELINE, and why it is this one. Immediately after ``link_notes``, which is where
the five removed derivation services ran. That matters for one reason beyond symmetry: ``normalize``
has already run by then, so the sign pass will not see these figures — they are written with
``value`` and ``value_raw`` both set to the amount the note printed, which is what those services
produced and what the downstream tolerance checks were calibrated against. Running earlier would
subject a note-derived figure to the unsigned-expense cohort vote, and one filing's note breakdown
would flip the sign of every expense on its income statement.

WHAT IT WRITES, AND WHAT IT REFUSES TO WRITE:

* A CHILD gets its figure. Sub-line items are ``in_output: false`` — they exist to explain a parent,
  and they are where the trail lives.
* A PARENT gets it only if the filing did not print one. A printed parent is the filing's own
  statement of the figure; a note-derived one is an inference from a breakdown. Replacing the first
  with the second discards the more authoritative number, so instead the two are left to coexist and
  the disagreement is flagged for review.

Everything it does is reported through ``ctx.log`` per item, because a stage that fills figures
silently is indistinguishable from one that found nothing — and until this stage existed, "found
nothing" was the answer on every run.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext, Stage
from app.services import note_sourced


class NoteSourcedStage(Stage):
    name = "note_sourced"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        line_item_set = getattr(ctx, "line_items", None)
        items = _declared_items(line_item_set)
        if not items:
            ctx.log("note_sourced: no line item declares a note_source")
            return doc

        # A pattern that does not compile is the author's error and is named as such, once, before
        # any selection runs — so it is attributable to a line of configuration rather than showing
        # up as a line that mysteriously stayed empty.
        for item in items:
            for bad in note_sourced.bad_patterns(item):
                ctx.log(f"note_sourced:REFUSED {bad} does not compile")

        by_key = {li.canonical_key: li for li in doc.line_items if li.canonical_key}
        children_of: dict[str, list] = {}
        filled = touched = 0
        for item in items:
            hits = note_sourced.select_rows(item, doc.notes)
            if not hits:
                continue
            # WITHIN ONE NOTE the rows are components: a note that splits depreciation across
            # four assets prints four rows and the note's depreciation IS their sum. The child's
            # own `rollup` says so and defaults to `sum`. What the PARENT does with its several
            # children is a different question, answered below from the parent's declaration.
            rollup = str(getattr(item, "rollup", None) or "sum")
            resolved = note_sourced.resolve(hits, rollup)
            row = by_key.get(item.key)
            if row is None:
                row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
                doc.line_items.append(row)
                by_key[item.key] = row
            touched += 1
            for (basis, period), (amount, inputs) in sorted(resolved.items()):
                _write(row, basis, period, amount)
                row.derivation = note_sourced.derivation.record(
                    row.derivation, basis=basis, period_label=period,
                    derivation=note_sourced.trail(rollup=rollup, item_label=item.label or item.key,
                                                  amount=amount, inputs=inputs))
                filled += 1
            row.is_computed = True
            row.confidence.flags.append(f"note_sourced:{len(hits)}")
            ctx.log(f"note_sourced:{item.key}: {len(hits)} row(s) -> "
                    f"{len(resolved)} slot(s), {rollup} within the note")
            children_of.setdefault(str(getattr(item, "parent", "") or ""), []).append((item, row))

        # THE PARENT IS FILLED IN A SECOND PASS, once all its children are known — because how they
        # combine is the PARENT's declaration, and answering it while walking the children would
        # mean deciding on the first one before the rest existed.
        # THE WHOLE SET, not `items`. `items` is only the 13 that DECLARE a note_source, and a
        # parent declares none — so looking the parent's rollup up in that list found nothing and
        # fell back to `sum`, which summed twelve disclosures of one depreciation charge into a cost
        # twelve times too large. The fallback was the bug, not the lookup.
        parents = _fill_parents(children_of, by_key, doc, ctx,
                                _parent_rollup(getattr(line_item_set, "items", None)))
        ctx.log(f"note_sourced: {touched} item(s) filled from notes, {filled} figure(s), "
                f"{parents} parent(s) resolved")
        return doc


def _declared_items(line_item_set) -> list:
    """Every configured line item carrying a `note_source`, in declared `order`.

    READ FROM THE SET, NOT FROM `ctx.ontology`. The working view the matcher uses is a projection
    that drops the internal sub-line items and whose `OntologyMapping` carries none of
    `note_source`, `parent` or `rollup` — so the declarations simply are not there. `ctx.line_items`
    is the whole configured set, attached by `services.documents._context`.

    ORDER MATTERS for `alternatives`: the first match wins, so the author's ordering IS the
    precedence of sources. Sorted here rather than trusting file order, so that precedence is a
    property of the configuration rather than of how the JSON happened to be written.
    """
    items = getattr(line_item_set, "items", None) or []
    declared = [i for i in items if getattr(i, "note_source", None) is not None]
    return sorted(declared, key=lambda i: (int(getattr(i, "order", 0) or 0), i.key))


def _write(row: LineItem, basis: str, period: str, amount: Decimal) -> None:
    """Put the figure on the row, replacing that slot if it already has one.

    Both `value` and `value_raw` are set to the same amount. `normalize` has already run, so there
    is no later pass to derive one from the other — and `value_raw` is what the audit trail and the
    reconciliation tolerances read.
    """
    for ev in (row.values or {}).values():
        if (_basis(ev) == basis and str(getattr(ev, "period_label", "") or "") == period):
            ev.value = amount
            ev.value_raw = amount
            return
    from app.core.models.enums import Basis
    try:
        basis_enum = Basis(basis) if basis else Basis.CONSOLIDATED
    except ValueError:
        basis_enum = Basis.CONSOLIDATED
    row.set_value(ExtractedValue(basis=basis_enum, period_label=period or None,
                                 value=amount, value_raw=amount))


def _basis(ev) -> str:
    b = getattr(ev, "basis", "")
    return str(getattr(b, "value", b) or "")


def _parent_rollup(all_items) -> dict[str, str]:
    """Each configured key -> the rollup IT declares for its own children.

    TAKES THE WHOLE SET. A parent does not declare a `note_source`, so it is absent from the list
    of declaring items — and a `.get(key, "sum")` against that list silently made every parent a
    summing one.

    THE DECLARATION IS ON THE PARENT, which is what `services.line_items.check_rollups` reads and
    what the shipped set says: the eight focus concepts are the only items in the whole set that
    declare a rollup at all — six `none`, and the depreciation pair `alternatives` — while all
    thirteen children declare nothing. Reading the CHILD's rollup to decide the roll-up (which this
    stage first did) would silently treat every parent as `alternatives`, because a child's
    unstated rollup defaults to `sum` and the incremental fill took the first figure either way.
    """
    return {i.key: str(getattr(i, "rollup", None) or "sum") for i in all_items or ()}


def _fill_parents(children_of: dict[str, list], by_key: dict, doc: DocumentModel,
                  ctx: PipelineContext, declared: dict[str, str]) -> int:
    """Combine each parent's note-sourced children the way the PARENT declares.

    * ``alternatives`` — the children are the same figure disclosed in different notes, so ONE is
      taken: the first in declared order that has a figure. Summing twelve disclosures of one
      depreciation charge would multiply a cost by the number of places the filing mentioned it.
    * ``sum`` — the children are genuine components. Add them.
    * ``none`` — the parenthood carries no arithmetic. Nothing is carried up.

    NEVER OVER THE FILING'S OWN FIGURE. A printed parent is the filing stating the amount; a
    note-derived one is an inference from a breakdown. The printed one stands and the disagreement
    is flagged, because silently preferring the inference would discard the better number and leave
    nothing behind to review.
    """
    resolved = 0
    for parent_key, kids in sorted(children_of.items()):
        if not parent_key:
            continue
        rollup = declared.get(parent_key, "sum")
        if rollup == "none":
            ctx.log(f"note_sourced:{parent_key}: {len(kids)} child(ren) filled, and its rollup is "
                    f"`none` — nothing carried up")
            continue
        parent = by_key.get(parent_key)
        if parent is None:
            parent = LineItem(source_label=parent_key, canonical_key=parent_key)
            doc.line_items.append(parent)
            by_key[parent_key] = parent

        # Per (basis, period), because combining across columns would produce a figure the filing
        # states in neither.
        by_slot: dict[tuple[str, str], list] = {}
        for item, child in kids:
            for ev in (child.values or {}).values():
                if ev.value is None:
                    continue
                by_slot.setdefault((_basis(ev), str(getattr(ev, "period_label", "") or "")),
                                   []).append((item, ev))

        for (basis, period), offers in sorted(by_slot.items()):
            existing = _slot(parent, basis, period)
            if existing is not None and existing.value is not None:
                taken = offers[0][1].value if rollup == "alternatives" else sum(
                    (o[1].value for o in offers), Decimal(0))
                if existing.value != taken:
                    parent.confidence.flags.append(
                        f"note_sourced_differs_from_printed:{offers[0][0].key}")
                    ctx.log(f"note_sourced:{parent_key}: printed {existing.value} kept over "
                            f"note-derived {taken}")
                continue
            if rollup == "alternatives":
                item, ev = offers[0]
                _write(parent, basis, period, ev.value)
                parent.confidence.flags.append(f"note_sourced_from:{item.key}")
                if len(offers) > 1:
                    parent.confidence.flags.append(
                        f"note_sourced_alternatives_available:{len(offers) - 1}")
                    ctx.log(f"note_sourced:{parent_key}: took {item.key}, "
                            f"{len(offers) - 1} other source(s) available")
            else:
                total = sum((ev.value for _i, ev in offers), Decimal(0))
                _write(parent, basis, period, total)
                parent.confidence.flags.append(f"note_sourced_sum_of:{len(offers)}")
            resolved += 1
    return resolved


def _slot(row: LineItem, basis: str, period: str):
    for ev in (row.values or {}).values():
        if _basis(ev) == basis and str(getattr(ev, "period_label", "") or "") == period:
            return ev
    return None
