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

        # THE STATEMENT'S OWN PERIODS, taken from the face rather than assumed. A note-sourced
        # figure belongs in a column the STATEMENTS use; a note's other columns are a different
        # axis, and a figure from one of those published as the year's amount reconciles against
        # nothing. Derived rather than hardcoded to `current`/`prior`, so a filing presenting three
        # columns still works.
        periods = {str(getattr(ev, "period_label", "") or "")
                   for li in doc.line_items for ev in (li.values or {}).values()
                   if getattr(ev, "column_index", None) is None
                   and getattr(ev, "period_label", None)}
        if not periods:
            periods = None                      # nothing to filter against; do not filter
            ctx.log("note_sourced: the face declares no period labels — every note column allowed")
        else:
            ctx.log(f"note_sourced: face periods {sorted(periods)}")

        all_items = getattr(line_item_set, "items", None) or []
        by_key = {li.canonical_key: li for li in doc.line_items if li.canonical_key}
        children_of: dict[str, list] = {}
        filled = touched = 0
        for item in items:
            hits = note_sourced.select_rows(item, doc.notes, periods)
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

        # CHILDREN FILLED BY ANYTHING ELSE COUNT TOO, and this is the difference between solving
        # the sub-line items and solving the parents. All eight focus concepts are `derived`: the
        # parent is computed by cascade over its sub-items, and a sub-item is what corresponds to a
        # printed note row. A sub-item filled by the MODEL — naming it and citing the row or the
        # footnote it came from — must feed the same cascade as one filled by a `note_source`
        # declaration, or the model's answer would sit on a row nothing reads while the parent
        # stayed empty.
        #
        # Collected from the document rather than from this stage's own walk, because "which
        # children have a figure" is a property of the document at this point in the pipeline and
        # not of how each figure arrived.
        for parent_key, kid_keys in _children_by_parent(all_items).items():
            for kid in kid_keys:
                row = by_key.get(kid)
                if row is None or not any(ev.value is not None
                                          for ev in (row.values or {}).values()):
                    continue
                already = {c.key for _i, c in children_of.get(parent_key, [])
                           if getattr(c, "key", None)}
                if kid in already:
                    continue
                item = next((i for i in all_items if i.key == kid), None)
                if item is not None and not any(
                        r is row for _i, r in children_of.get(parent_key, [])):
                    children_of.setdefault(parent_key, []).append((item, row))
                    ctx.log(f"note_sourced:{parent_key}: child {kid} was filled elsewhere "
                            f"(model or earlier stage) and joins the cascade")

        # THE PARENT IS FILLED IN A SECOND PASS, once all its children are known — because how they
        # combine is the PARENT's declaration, and answering it while walking the children would
        # mean deciding on the first one before the rest existed.
        # THE WHOLE SET, not `items`. `items` is only the 13 that DECLARE a note_source, and a
        # parent declares none — so looking the parent's rollup up in that list found nothing and
        # fell back to `sum`, which summed twelve disclosures of one depreciation charge into a cost
        # twelve times too large. The fallback was the bug, not the lookup.
        parents = _fill_parents(children_of, by_key, doc, ctx,
                                _parent_rollup(all_items), _note_permission(all_items),
                                {i.key: i for i in all_items})
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


def _note_permission(all_items) -> dict[str, str]:
    """Each configured key -> its `note_use`, which decides whether a NOTE may fill it at all.

    THE SET'S OWN RULE, quoted from `global_rules.face_only_default`: "Notes are evidence for a face
    amount, never an independent source of one, unless note_use is decomposition_allowed." So a
    concept marked `evidence_only` may be CORROBORATED by a note and must not be FILLED from one —
    and `notes__contingent_liabilities`, one of the eight focus concepts, is exactly that.

    Read off the PARENT, because the parent is the concept being filled. The thirteen children all
    resolve to `evidence_only` themselves, inherited from the `notes` section they live in, which
    says where the selection machinery sits rather than what may be concluded from it. Gating on
    the child would refuse every fill in the set.
    """
    return {i.key: str(getattr(i, "note_use", "") or "") for i in all_items or ()}


def _fill_parents(children_of: dict[str, list], by_key: dict, doc: DocumentModel,
                  ctx: PipelineContext, declared: dict[str, str],
                  permitted: dict[str, str], defs: dict) -> int:
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
        # THE PERMISSION GATE, before the arithmetic. A concept the configuration marks
        # `evidence_only` is one the notes may corroborate and must not supply — see
        # `_note_permission`. Refused out loud, because a line left empty for a stated reason and
        # a line left empty because nothing matched are different facts.
        use = permitted.get(parent_key, "")
        if use and use != "decomposition_allowed":
            ctx.log(f"note_sourced:{parent_key}: REFUSED as a note source — its note_use is "
                    f"`{use}`, so a note is evidence for this line and never the source of it")
            continue
        parent_def = defs.get(parent_key)
        # A DECLARED CASCADE IS THE ANSWER, and `rollup` is only the summary of it.
        #
        # `is_pl__deprec_and_impairment_oper_exp` declares five rungs: sum the four
        # operating-expense notes; failing that the PBT note's own callout; failing that total
        # depreciation LESS the cost-of-sales share; and so on. `rollup: "alternatives"` says
        # roughly "the children are not addends", which is true and far too coarse — reading it
        # instead of the cascade takes the first child on its own (1,200) where the configuration
        # says to sum four notes. The cascade also carries what a rollup cannot express at all:
        # optional terms (`any_of`), signed deductions (`adjustment`, `sign: -1`), and a refusal to
        # accept a negative candidate.
        #
        # Evaluated by `services.line_items.evaluate`, which already implements all of it — a
        # second copy here would be the two-places-computing-one-quantity bug on the arithmetic
        # that decides a published figure.
        if parent_def is not None and (getattr(parent_def, "cascade", None)
                                       or getattr(parent_def, "terms", None)):
            resolved += _fill_by_cascade(parent_def, kids, by_key, doc, ctx)
            continue
        rollup = declared.get(parent_key, "sum")
        if rollup == "none":
            ctx.log(f"note_sourced:{parent_key}: {len(kids)} child(ren) filled, its rollup is "
                    f"`none` and it declares no cascade — nothing carried up")
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


def _fill_by_cascade(parent_def, kids: list, by_key: dict, doc: DocumentModel,
                     ctx: PipelineContext) -> int:
    """Evaluate the parent's declared cascade against its note-sourced children, per column.

    PER (BASIS, PERIOD), because a rung is only an arithmetic within one column: mixing the current
    year's notes with the prior year's would compute a figure the filing states in neither.

    The trail records WHICH RUNG ANSWERED and which were passed over — a depreciation charge that
    came from "total less the cost-of-sales share" rather than from the four operating-expense
    notes is a materially different provenance, and a reviewer cannot see it in the number.
    """
    from app.services.line_items import evaluate as evaluate_line

    parent = by_key.get(parent_def.key)
    if parent is None:
        parent = LineItem(source_label=parent_def.label or parent_def.key,
                          canonical_key=parent_def.key)
        doc.line_items.append(parent)
        by_key[parent_def.key] = parent

    slots: dict[tuple[str, str], dict] = {}
    for _item, child in kids:
        for ev in (child.values or {}).values():
            if ev.value is None:
                continue
            slot = (_basis(ev), str(getattr(ev, "period_label", "") or ""))
            slots.setdefault(slot, {})[child.canonical_key] = ev.value

    filled = 0
    for (basis, period), known in sorted(slots.items()):
        got = evaluate_line(parent_def, known)
        if not got.resolved:
            ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: no cascade rung resolved "
                    f"from {len(known)} child figure(s)"
                    + (f"; refused {got.refused_rungs}" if got.refused_rungs else ""))
            continue
        existing = _slot(parent, basis, period)
        if existing is not None and existing.value is not None:
            if existing.value != got.value:
                parent.confidence.flags.append("note_sourced_differs_from_printed:cascade")
                ctx.log(f"note_sourced:{parent_def.key}: printed {existing.value} kept over "
                        f"cascade {got.rung_used}={got.value}")
            continue
        _write(parent, basis, period, got.value)
        parent.derivation = note_sourced.derivation.record(
            parent.derivation, basis=basis, period_label=period,
            derivation=note_sourced.derivation.build(
                method=f"cascade:{got.rung_used}",
                formula=" + ".join(
                    f"{'-' if i['sign'] < 0 else ''}{i['ref']}" for i in got.inputs),
                inputs=[{"label": i["ref"], "value": i["value"], "counted": True,
                         "deducted": i["sign"] < 0, "excerpt": f"role={i['role']}"}
                        for i in got.inputs],
                result=got.value,
                flags=[f"rung:{got.rung_used}"]
                     + ([f"rungs_refused:{len(got.refused_rungs)}"] if got.refused_rungs else [])
                     + ([f"terms_missing:{len(got.missing)}"] if got.missing else [])))
        parent.confidence.flags.append(f"cascade_rung:{got.rung_used}")
        ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: rung {got.rung_used} "
                f"-> {got.value} from {len(got.inputs)} term(s)")
        filled += 1
    return filled


def _slot(row: LineItem, basis: str, period: str):
    for ev in (row.values or {}).values():
        if _basis(ev) == basis and str(getattr(ev, "period_label", "") or "") == period:
            return ev
    return None


def _children_by_parent(all_items) -> dict[str, list[str]]:
    """parent key -> the keys of its sub-line items, from the configuration.

    Needed because a cascade term names a child by key, and a child filled by the model arrives as
    a row with that key and no other connection to its parent.
    """
    out: dict[str, list[str]] = {}
    for item in all_items or ():
        parent = str(getattr(item, "parent", "") or "")
        if parent:
            out.setdefault(parent, []).append(item.key)
    return out
