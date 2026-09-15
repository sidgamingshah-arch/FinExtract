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

from decimal import Decimal, InvalidOperation

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

        # WHICH SECTION EACH NOTE IS ALIGNED TO, resolved once for the run rather than per line:
        # the map is built from `doc.links` and every line is then narrowed against the same
        # answer. `section_scope` had no reader in the note path before this — see
        # `services.note_sections` for the signal it uses and how far it reaches.
        from app.services.note_sections import note_sections as _resolve_note_sections
        note_sections = _resolve_note_sections(doc, line_item_set)
        ctx.log(f"note_sourced:section of {len(note_sections)} note(s) resolved from the "
                f"note-to-face links")

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
        prose_filled = 0
        for item in items:
            # A `prose` LINE SKIPS THE ROW SEARCH. Its figure is a sentence and nothing else, so
            # running the row selection first would let a coincidental caption match publish a
            # tabulated number on a line the author said is never tabulated. `note_tables` — the
            # value every migrated line carries — searches rows and keeps the prose FALLBACK below,
            # which is what the six depreciation splits need.
            hits = ([] if route_of(item) == "prose"
                    else note_sourced.select_rows(item, doc.notes, periods, note_sections))
            if not hits:
                # A FIGURE THE FILING STATES ONLY IN PROSE, and this is the one route to it.
                #
                # Measured on the reference HK filing: the operating-expense share of the
                # depreciation charge is disclosed in a footnote and nowhere else — "Depreciation
                # charges of approximately HK$529,841,000 (2024: HK$665,553,000) are included in
                # 'other operating expenses'" — and 529841 appears in NO extracted row anywhere in
                # the document. No row-caption pattern and no row term can reach it, so the line
                # stayed empty however plainly the filing stated it.
                #
                # A FALLBACK, NOT AN ALTERNATIVE, which is why it sits inside `if not hits`. A row
                # is the filing's own tabulation and a sentence is a narrative restatement of it,
                # so prose competing with rows would sometimes replace the first with the second.
                # Here it can only fill what would otherwise be empty.
                # THE SET'S GRAMMAR, passed in: the subject and connective vocabularies every
                # prose line shares live on the SET, so `select_prose` is given one item and
                # cannot reach them itself. Without it only raw `prose_any` patterns are consulted.
                prose = note_sourced.select_prose(
                    item, doc.notes, getattr(line_item_set, "prose_grammar", None))
                if not prose:
                    continue
                row = by_key.get(item.key)
                if row is None:
                    row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
                    doc.line_items.append(row)
                    by_key[item.key] = row
                touched += 1
                basis = _prose_basis(doc)
                scale = _prose_scale(doc)
                for hit in prose:
                    # A PROSE FIGURE IS STATED IN FULL; A TABLE IS STATED IN THE STATEMENT'S UNITS.
                    #
                    # This stage runs at 10 and `normalize` at 8, so nothing scales what is written
                    # here — and the two routes disagree by exactly the scale factor. Measured on
                    # the reference filing, whose statements are in HK$'000 (`unit_context`
                    # scale_factor 1000): the ROW route gives 587,417 for the total depreciation
                    # charge, while the footnote states "HK$529,841,000" and arrives as
                    # 529,841,000. Published unscaled that is a THOUSANDFOLD error on the face of
                    # the income statement.
                    #
                    # Prose states the amount in full because that is how prose is written — "HK$
                    # 529,841,000", never "HK$529,841 thousand" — and the amount pattern requires
                    # thousands separators, so a "HK$529.8 million" phrasing does not reach here at
                    # all. Scaled down, 529,841 is less than the 587,417 total, which is the check
                    # a SHARE of that total has to pass.
                    amount = hit.amount / scale if scale and scale != 1 else hit.amount
                    # THE MODEL'S ANSWER STANDS HERE TOO — see `_llm_holds`. Prose is already a
                    # fallback for a row route that found nothing; it is not a correction of an
                    # answer the model gave from the same notes.
                    if _llm_holds(row, basis, hit.period):
                        row.confidence.flags.append(f"prose_deferred_to_llm:{amount}")
                        ctx.log(f"note_sourced:{item.key}[{basis}:{hit.period}]: prose {amount} "
                                f"NOT written — the model answered this row")
                        continue
                    _write(row, basis, hit.period, amount)
                    row.derivation = note_sourced.derivation.record(
                        row.derivation, basis=basis, period_label=hit.period,
                        derivation=note_sourced.trail(
                            rollup="prose", item_label=item.label or item.key, amount=amount,
                            inputs=[{"label": f"note {hit.note_number}: {hit.sentence[:160]}",
                                     # The figure AS THE SENTENCE STATES IT, so the division by the
                                     # statement's scale is auditable against the words. The row
                                     # above carries the scaled figure and a `prose_scaled_by:`
                                     # flag, so the two are reconcilable; `excerpt` says so here
                                     # rather than leaving a reader to notice a contributions list
                                     # that reads a thousand times the total above it.
                                     "value": str(hit.amount), "counted": True,
                                     "deducted": False, "note": hit.note_number,
                                     "excerpt": (f"stated in full in the sentence"
                                                 + (f"; the line publishes it divided by {scale}"
                                                    if scale and scale != 1 else "")),
                                     # THE PAGE. Without it a prose citation is an amount a reader
                                     # cannot go and look at — see `ProseHit.provenance`.
                                     "provenance": hit.provenance}]))
                    filled += 1
                    prose_filled += 1
                row.is_computed = True
                # SAID ON THE ROW, because a figure read out of a sentence is not the same evidence
                # as one read off a tabulated row, and a reviewer cannot tell them apart from the
                # number. The flag names the note so the sentence can be found.
                row.confidence.flags.append(f"note_sourced_prose:{prose[0].note_number}")
                if scale and scale != 1:
                    row.confidence.flags.append(f"prose_scaled_by:{scale}")
                ctx.log(f"note_sourced:{item.key}: no row matched; {len(prose)} figure(s) taken "
                        f"from the PROSE of note {prose[0].note_number} "
                        f"(matched on {prose[0].matched_by!r})")
                children_of.setdefault(str(getattr(item, "parent", "") or ""), []).append(
                    (item, row))
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
                # THE MODEL'S ANSWER STANDS — see `_llm_holds`. A `note_source` declaration is one
                # route to this row's figure and the model's reading of the notes is another; where
                # the model has answered, this fills nothing and the figure it gave feeds the
                # cascade below exactly as a note-sourced one would.
                if _llm_holds(row, basis, period):
                    row.confidence.flags.append(f"note_source_deferred_to_llm:{amount}")
                    ctx.log(f"note_sourced:{item.key}[{basis}:{period}]: {amount} NOT written — "
                            f"the model answered this row; its figure joins the cascade instead")
                    continue
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
        if prose_filled:
            ctx.log(f"note_sourced:prose_figures={prose_filled} (a figure the filing states in a "
                    f"sentence rather than a row — see the note_sourced_prose flag on each row)")

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
        # THE ARITHMETIC LINES THAT HAVE NO CHILDREN OF THEIR OWN — BEFORE the parents, so a
        # parent's cascade can reference one. `bs_nca__secur_and_other_fincl_assets_ltp` does
        # exactly that: its rungs name the CURRENT securities line's overshoot residual, which is
        # computed from that line's note-sourced children and must therefore exist before any
        # parent cascade is evaluated.
        standalone = _fill_childless_internal(all_items, children_of, by_key, doc, ctx)
        parents = _fill_parents(children_of, by_key, doc, ctx,
                                _parent_rollup(all_items),
                                {i.key: i for i in all_items})
        # …AND AGAIN AFTER, for one whose inputs are a PARENT's computed figure rather than a
        # child's. Idempotent: the pass skips any slot that already carries a value, so a line
        # settled above is not recomputed here.
        standalone += _fill_childless_internal(all_items, children_of, by_key, doc, ctx)
        ctx.log(f"note_sourced: {touched} item(s) filled from notes, {filled} figure(s), "
                f"{parents} parent(s) resolved, {standalone} standalone line(s) computed")
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

    A LINE THAT LIVES ON THE FACE IS NOT FILLED FROM NOTES. "Where in the report does this line
    live?" is one answer — `SectionDefaults.where()`, folded in from `inherits` — and it decides
    WHICH SEARCH a line gets: `face` means the figure is claimed off the statement, so a
    `note_source` on such a line is a declaration the pipeline must not act on.

    IT IS A GUARD, NOT A CHANGE, and that is measured: of the 396 items in the twelve face sections
    NONE declares a `note_source`, so nothing is excluded today. It exists because the failure it
    prevents is silent — a face line quietly taking a figure out of a note reconciles against
    nothing and looks plausible — and because the config screen now offers `note_source` on every
    line, so the combination is one edit away.

    NO SECTION MEANS OPEN. A line that names no `inherits` — the three related-party Find lines
    are the shipped case — is not restricted to anything and keeps the note route. Absence is
    "nothing was said", never "face", which is the same convention `section_scope` and `statement`
    already use. The five `either` sections (`supplemental_data`, `credit_compliance`,
    `capital_and_lease_commitments`, `off_balance_sheet_data`, `statement_setup_controls`) are open
    for the same reason: they are neither face nor notes, and none of their 60 items declares a
    `note_source` anyway.
    """
    items = getattr(line_item_set, "items", None) or []
    declared = [i for i in items if getattr(i, "note_source", None) is not None]
    sections = getattr(line_item_set, "section_defaults", None) or {}

    def _lives_on_the_face(item) -> bool:
        """Whether this line's figure is read off the STATEMENT rather than out of a note.

        THE LINE'S OWN `route` ANSWERS IT NOW, where this used to infer the answer from the section
        the line inherits. That inference is exactly what `route` replaces: `where()` is derived
        from `face_only` and `scopes`, so "is this a face line" was two derivations away from
        anything an author wrote, and an author who declared a `note_source` on a face line had no
        way to see that the pipeline would refuse to act on it.

        `route: None` KEEPS THE OLD READING, so a set authored before the field existed behaves
        exactly as it did — the section inference is the fallback, not the primary. Adding the
        question re-routes nothing on its own.
        """
        route = str(getattr(item, "route", "") or "")
        if route:
            return route == "face"
        section = sections.get(str(getattr(item, "inherits", "") or ""))
        return section is not None and section.where() == "face"

    declared = [i for i in declared if not _lives_on_the_face(i)]
    return sorted(declared, key=lambda i: (int(getattr(i, "order", 0) or 0), i.key))


def route_of(item) -> str:
    """This line's note route, with the legacy inference behind it — the ONE reader of that fallback.

    `prose` skips the row search entirely; `note_tables` searches rows and falls back to prose;
    `face` never reaches this stage at all (`_declared_items` filters it). A line that declares
    nothing is read as `note_tables`, which is what carrying a `note_source` has always meant.
    """
    route = str(getattr(item, "route", "") or "")
    return route if route in ("face", "note_tables", "prose", "anywhere") else "note_tables"


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


def _llm_holds(row: LineItem, basis: str, period: str) -> bool:
    """Whether the MODEL already answered this row in this column.

    THE NON-INTERFERENCE RULE BETWEEN THE TWO ROUTES, asked at every site this stage writes a
    figure. Every concept is offered to the model now — derived parents included — so that some of
    a filing's addition and subtraction can be the model's to do: it can read four note rows and
    say they are one subtotal, which no `note_source` declaration and no cascade rung anticipated.
    Where it has done that, a declared route is the OTHER way of reaching the figure rather than a
    correction of it, so this stage fills what the model left empty and leaves the rest alone.

    `confidence.method` is the test because it is what the mapper stamps when a concept came from
    the model, and the same field the review queue and the export read to say where a figure came
    from — so "the model answered this" has one spelling rather than a flag invented here.
    """
    if not str(getattr(row.confidence, "method", "") or "").lower().endswith("llm"):
        return False
    ev = _slot(row, basis, period)
    return ev is not None and ev.value is not None


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


# `_note_permission` WAS HERE. It mapped each key to its `note_use` so `_fill_parents` could
# refuse a note filling an `evidence_only` line. `note_use` is no longer a question — see the
# comment at that gate for the measurement showing it could not fire on this set — so the map
# had one caller and that caller stopped asking.




def _prose_scale(doc):
    """The factor a full prose amount must be divided by to match the statements' units.

    `unit_context.scale_factor` is what `normalize` applies to the face and the note tables, and it
    is 1000 for a filing presented in thousands. A sentence states its figure in full, so it needs
    the same division — and it does not get it, because this stage runs after `normalize`.
    """
    unit = getattr(doc, "unit_context", None)
    factor = getattr(unit, "scale_factor", None) if unit is not None else None
    try:
        return Decimal(str(factor)) if factor else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def _prose_basis(doc) -> str:
    """The basis a prose figure is filed under.

    A SENTENCE CARRIES NO BASIS COLUMN, unlike a note row, so one has to be chosen. The document's
    own prevailing basis is the only defensible answer: a footnote in a consolidated filing is
    describing the consolidated figures, and filing it anywhere else would put it in a slot the
    face never populates and the reconciliation never reads.
    """
    seen: dict[str, int] = {}
    for li in doc.line_items:
        for ev in (li.values or {}).values():
            basis = str(getattr(getattr(ev, "basis", ""), "value", getattr(ev, "basis", "")) or "")
            if basis:
                seen[basis] = seen.get(basis, 0) + 1
    if not seen:
        return "consolidated"
    return max(seen.items(), key=lambda kv: kv[1])[0]

def _in_dependency_order(children_of: dict[str, list], defs: dict) -> list[tuple[str, list]]:
    """The parents, each after any parent its own cascade depends on.

    WHY THE ORDER MATTERS. A rung term may name another PARENT rather than a part. The case this
    was written for was `is_pl__deprec_and_impairment_cos`'s COS_P3, which was
    `sub__pbt_depreciation - is_pl__deprec_and_impairment_oper_exp`: evaluated before the
    operating-expense parent had been written, that term was missing and — being role `required` —
    the rung could not resolve. Sorted alphabetically, which is what this replaced, `cos` came
    first every time, so COS_P3 was unreachable by construction.

    THAT PARTICULAR RUNG IS GONE — `sub__pbt_cos_depreciation` was retired with the cost-of-sales
    tier — BUT THE ORDERING IS STILL LOAD-BEARING, and for a reason worth stating exactly, because
    an earlier version of this docstring named the wrong one and a reader trusting it would protect
    the wrong thing.

    MEASURED ON THE SHIPPED SET. `is_pl__deprec_and_impairment_cos`'s surviving COS_P2 is
    `sub__pbt_depreciation - is_pl__deprec_and_impairment_oper_exp`, both role `required`: it names
    the OTHER PARENT, so `depends` sees it and this function places `cos` after `oper_exp`. It is
    emitted LAST of the eight parents, and that is this sort doing its job — alphabetically `cos`
    would come first, exactly the arrangement that made COS_P3 unreachable.

    WHAT DOES *NOT* DEPEND ON THIS SORT, stated because it reads as though it should:
    `oper_exp`'s P3/P4/P5 each deduct `sub__cos_depreciation`, and that is a CHILD key. Every ref
    in `oper_exp`'s cascade is a `sub__*` child, none is a parent key, so `depends[oper_exp]` is
    empty — and it needs to be, because children are filled in the pass BEFORE any parent cascade
    is evaluated. What makes that deduction visible is the widened `known` map, not this order; see
    the comment at `_fill_parents`' `known` for the 57,576 overstatement that proved it, and note
    that the failure is silent in one direction — `sub__cos_depreciation` is role `adjustment`, so
    its absence resolves the rung WITHOUT the deduction rather than failing it.

    A SORT AND NOT A FULL TOPOLOGICAL WALK, deliberately. `services.line_items.build` already does
    the real ordering for the registry, and duplicating it here would be a second implementation of
    the same graph. What this needs is narrower: one pass placing each parent after the parents it
    names, with alphabetical order as the tiebreak so the result is stable. A CYCLE cannot hang it
    — the iteration is bounded by the number of parents and anything still unplaced is emitted in
    declared order, which is the same behaviour as before for a set that has no cross-parent terms
    at all (every other parent in the shipped set).
    """
    parents = sorted(children_of)
    depends: dict[str, set[str]] = {}
    for key in parents:
        definition = defs.get(key)
        refs = {t.ref for rung in (getattr(definition, "cascade", None) or ())
                for t in (getattr(rung, "terms", None) or ()) if getattr(t, "ref", None)}
        depends[key] = {r for r in refs if r in children_of and r != key}

    out: list[str] = []
    placed: set[str] = set()
    for _pass in range(len(parents)):
        progressed = False
        for key in parents:
            if key in placed or depends[key] - placed:
                continue
            out.append(key)
            placed.add(key)
            progressed = True
        if not progressed:
            break
    out.extend(k for k in parents if k not in placed)      # a cycle, or a ref outside the set
    return [(k, children_of[k]) for k in out]


def _fill_parents(children_of: dict[str, list], by_key: dict, doc: DocumentModel,
                  ctx: PipelineContext, declared: dict[str, str],
                  defs: dict) -> int:
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
    for parent_key, kids in _in_dependency_order(children_of, defs):
        if not parent_key:
            continue
        # THE PERMISSION GATE IS GONE. `note_use` is no longer a question — decomposition is always
        # allowed — so there is nothing here to refuse.
        #
        # AND IT COULD NEVER HAVE FIRED ON THIS SET, which is why removing it moves no figure.
        # Measured before removing: the keys this loop visits are the parents of items carrying a
        # `note_source`, of which there are 8, and all 8 resolve to `decomposition_allowed`. Of the
        # 70 items that resolved to `evidence_only`, ZERO are a note-sourced parent, ZERO carry a
        # `note_source` of their own and ZERO have any children at all — they are the covenant,
        # supplemental and statement-setup lines, which no note fills because nothing declares a
        # note route to them. The gate was protecting a case the configuration could not express.
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
            resolved += _fill_by_cascade(parent_def, kids, by_key, doc, ctx, defs)
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


def _cascade_input(term: dict, by_key: dict, defs: dict | None) -> dict:
    """One cascade term as a contribution — carrying the line it REFERS TO, not just its key.

    See the comment at the call site for what this fixes. The label falls back through the
    definition's own label, then the extracted row's printed caption, then the bare key, so a
    referenced line that is configured but was never extracted still reads as a name.
    """
    ref = str(term.get("ref") or "")
    row = by_key.get(ref) if ref else None
    definition = (defs or {}).get(ref) if ref else None
    label = (getattr(definition, "label", "") or getattr(row, "source_label", "") or ref
             or "fixed number")
    out = {
        "label": label,
        "value": term.get("value"),
        "counted": True,
        "deducted": term.get("sign", 1) < 0,
        "excerpt": f"role={term.get('role')}",
    }
    if ref:
        # WHAT MAKES THE HOP CLICKABLE. The inspector renders a contribution with a
        # `canonical_key` as a line the reader can open; without it the same row is inert text.
        out["canonical_key"] = ref
        values = list(getattr(row, "values", {}).values()) if row is not None else []
        prov = next((getattr(v, "provenance", None) for v in values
                     if getattr(v, "provenance", None) is not None), None)
        if prov is not None:
            out["provenance"] = note_sourced.derivation._json_safe_provenance(prov)
    return out


def _fill_by_cascade(parent_def, kids: list, by_key: dict, doc: DocumentModel,
                     ctx: PipelineContext, defs: dict | None = None) -> int:
    """Evaluate the parent's declared cascade against its note-sourced children, per column.

    PER (BASIS, PERIOD), because a rung is only an arithmetic within one column: mixing the current
    year's notes with the prior year's would compute a figure the filing states in neither.

    The trail records WHICH RUNG ANSWERED and which were passed over — a depreciation charge that
    came from "total less the cost-of-sales share" rather than from the four operating-expense
    notes is a materially different provenance, and a reviewer cannot see it in the number.
    """
    from app.services.line_items import evaluate as evaluate_line

    # EVERY ROW CARRYING THE KEY, IN DOCUMENT ORDER — not `by_key`, which is built as
    # `{li.canonical_key: li for li in doc.line_items}` and therefore keeps the LAST of however
    # many rows share a key.
    #
    # THE BUG THAT MADE THIS NECESSARY, measured on laisun. Four rows carry
    # `bs_nca__secur_and_other_fincl_assets_ltp` — two financial-asset classes on the consolidated
    # face and the same two standalone — and the mapper says so
    # (`map_line_items:split_declined(...): 4 rows carry it`). `by_key` handed back the fourth,
    # whose consolidated slot was EMPTY, so the rung wrote there: no contest with the printed
    # figures ever happened, and `_write` created the value with no provenance. `periods.summable`
    # deduplicates a fact printed twice only when the caption, the amount AND the page all match,
    # so a provenance-less duplicate cannot be recognised — and `concept_value`, which is what the
    # grid, the checks and the export all read, ADDED all three: 128,412 + 788,507 + 788,507 =
    # 1,705,426 published for a line whose rung computes 788,507.
    carriers = [li for li in doc.line_items if li.canonical_key == parent_def.key]
    parent = carriers[0] if carriers else by_key.get(parent_def.key)
    if parent is None:
        parent = LineItem(source_label=parent_def.label or parent_def.key,
                          canonical_key=parent_def.key)
        doc.line_items.append(parent)
        carriers = [parent]
        by_key[parent_def.key] = parent

    # THE CASCADE SEES EVERY FIGURE IN THE COLUMN, not only this parent's own children.
    #
    # THE BUG THIS FIXES, measured. `known` was built from `kids` alone, so a rung term naming a key
    # outside the parent's children was invisible — and five shipped terms are exactly that, all
    # between the two depreciation parents:
    #
    #   oper_exp P3/P4/P5  deduct `sub__cos_depreciation`, a child of the COS parent
    #   cos      COS_P2    `sub__pbt_depreciation - is_pl__deprec_and_impairment_oper_exp` — a
    #                      child of oper_exp AND the oper_exp parent itself. STILL SHIPPED, and it
    #                      is what `_in_dependency_order` detects to place `cos` last.
    #   cos      COS_P3    the same shape, RETIRED with the cost-of-sales tier — see below
    #
    # COS_P3 AND ITS PART ARE NO LONGER IN THE SHIPPED SET: the cost-of-sales tier was removed
    # deliberately with `sub__pbt_cos_depreciation`. It is kept in this account because it is the
    # clearer of the two failures and the one that explains why the ordering exists at all; the
    # three oper_exp deductions are live and are what still depends on it.
    #
    # The two failed differently and the first is the dangerous one. `sub__cos_depreciation` is
    # role `adjustment`, so `_apply_terms` treats its absence as "does not apply" and the rung
    # STILL RESOLVES — without the deduction. Reproduced against the shipped set: P3 publishes
    # 587,417 where 529,841 is correct, a 57,576 overstatement on the income statement, and the
    # only trace is a `terms_missing:1` flag nothing distinguishes from a filing that genuinely
    # disclosed no cost-of-sales depreciation. COS_P3's two terms are role `required`, so it simply
    # never resolved at all.
    #
    # `evaluate`'s own contract is "`known` maps key -> value for everything evaluated so far",
    # which is what this now supplies. Keyed by canonical_key across every row, because a cascade
    # term names a concept and not a parenthood — and `_children_by_parent` is still what decides
    # which children a parent OWNS, which is a different question.
    slots: dict[tuple[str, str], dict] = {}
    for row in doc.line_items:
        key = row.canonical_key
        if not key:
            continue
        for ev in (row.values or {}).values():
            if ev.value is None:
                continue
            slot = (_basis(ev), str(getattr(ev, "period_label", "") or ""))
            # FIRST WRITER WINS per (key, slot): several printed rows can carry one concept, and
            # `concept_value` is what resolves that for publication. A cascade term wants one
            # number, and taking the first in document order is at least deterministic — where it
            # matters the contest has already been settled onto a single carrier above.
            slots.setdefault(slot, {}).setdefault(key, ev.value)
    # A COLUMN WITH NO CHILD FIGURE AT ALL IS NOT THIS PARENT'S TO FILL. Widening `known` above
    # also widened the set of columns this loop would attempt, which would have it evaluate a
    # cascade in a column where none of its own parts appear — so the columns are still taken from
    # the children, and only the VALUES visible within them are widened.
    child_slots = {(_basis(ev), str(getattr(ev, "period_label", "") or ""))
                   for _item, child in kids
                   for ev in (child.values or {}).values() if ev.value is not None}
    slots = {slot: known for slot, known in slots.items() if slot in child_slots}

    filled = 0
    for (basis, period), known in sorted(slots.items()):
        got = evaluate_line(parent_def, known)
        if not got.resolved:
            ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: no cascade rung resolved "
                    f"from {len(known)} child figure(s)"
                    + (f"; refused {got.refused_rungs}" if got.refused_rungs else ""))
            continue
        # THE MODEL'S ANSWER IS NOT THE CASCADE'S TO OVERRULE, and this is the whole of the
        # non-interference rule between the two routes.
        #
        # Every concept is now offered to the model, derived parents included, precisely so that
        # some of a filing's addition and subtraction can be the model's to do — it can read four
        # note rows and say they are one subtotal, which no cascade rung anticipated. Where it has
        # done that, the declared cascade is the OTHER way of reaching the same figure, not a
        # correction of it: so this stage fills what the model left empty and leaves what it
        # answered exactly as it is.
        #
        # THE TEST IS THE ROW'S MAPPING METHOD, not a flag on the value: `confidence.method` is
        # what the mapper stamps when a concept came from the model (`MappingMethod.LLM`), and it
        # is the same field the review queue and the export read to say where a figure came from.
        llm_answered = [li for li in carriers
                        if str(getattr(li.confidence, "method", "") or "").lower().endswith("llm")
                        and (ev := _slot(li, basis, period)) is not None and ev.value is not None]
        if llm_answered:
            ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: cascade "
                    f"{got.rung_used}={got.value} NOT applied — the model answered this line "
                    f"({len(llm_answered)} row(s)); the declared cascade does not overrule it")
            for li in llm_answered:
                li.confidence.flags.append(f"cascade_deferred_to_llm:{got.rung_used}:{got.value}")
            continue
        # THE CONTEST IS ACROSS EVERY CARRIER, because the figure a reader sees is
        # `concept_value` over all of them — deciding it against one row would leave the others
        # adding underneath. `held` is every (row, slot) already carrying a figure in this column.
        held = [(li, ev) for li in carriers
                if (ev := _slot(li, basis, period)) is not None and ev.value is not None]
        # WRITE INTO AN EXISTING SLOT WHERE THERE IS ONE, so the figure keeps that slot's
        # provenance. A fresh slot would have none, and a provenance-less value is exactly what
        # `summable` cannot deduplicate.
        target_row, existing = held[0] if held else (parent, _slot(parent, basis, period))
        displaced = None
        if existing is not None and existing.value is not None:
            if existing.value == got.value and len(held) == 1:
                continue
            # WHICH WINS — THE PRINTED FIGURE OR THE RESOLVED RUNG. Two conditions, and both are
            # declarations rather than heuristics:
            #
            #   `type: derived`          the line's figure is assembled at all
            #   `rung.outranks_printed`  THIS rung reconstructs something the face does not state
            #
            # THE SECOND IS THE LOAD-BEARING ONE and it has to be per RUNG, not per line. Measured
            # on the reference filings, one cascade wants each answer:
            #
            #   LTP_P1 computes the non-current portion of the financial-asset notes less three
            #   classes of inclusion plus a carry-forward — 788,507, where a caption binds 128,412
            #   for a different quantity. The rung must win, or the arithmetic the author wrote
            #   decides nothing whenever the matcher happens to bind a row.
            #
            #   Revenue's P6 reconstructs the top line from ONE axis of a segment table, and this
            #   cascade's P1 is the face itself. A printed figure here is exactly what the top rung
            #   was looking for, so letting P6 displace it published 2,609,259 — one industry
            #   segment — over the 4,995,768 the face prints.
            #
            # Rung ORDER and rung MAGNITUDE both separate those two cases on these two filings, and
            # neither means anything: the real difference is whether the rung restates the face or
            # computes past it, which only the cascade's author knows. Hence the declaration.
            #
            # `calculated`/`intermediate` lines reach this function too (it is entered for
            # `cascade` OR `terms`) and keep the printed figure unconditionally — their `terms` are
            # a sum over other lines, not a judgement about which disclosure to believe.
            rung = next((r for r in (getattr(parent_def, "cascade", None) or ())
                         if str(getattr(r, "id", "")) == str(got.rung_used)), None)
            if (str(getattr(parent_def, "type", "") or "") != "derived"
                    or not getattr(rung, "outranks_printed", False)):
                parent.confidence.flags.append("note_sourced_differs_from_printed:cascade")
                ctx.log(f"note_sourced:{parent_def.key}: printed {existing.value} kept over "
                        f"cascade {got.rung_used}={got.value}")
                continue
            # THE DISPLACED FIGURES TRAVEL. A published number that replaced others must be
            # auditable against them — the same rule `stages.note_tag_gate` follows when it zeroes a
            # printed figure — so each goes in a flag, `value_raw` keeps it on its own row, and the
            # list goes into the trail below.
            displaced = ", ".join(str(ev.value) for _li, ev in held)
            target_row.confidence.flags.append(f"cascade_over_printed:{displaced}")
            ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: cascade "
                    f"{got.rung_used}={got.value} TAKEN OVER printed {displaced} "
                    f"({len(held)} row(s)) — a derived line's figure is its cascade's")
            # EVERY OTHER CARRIER STOPS CONTRIBUTING. Writing the rung onto one row and leaving the
            # rest is what produced 1,705,426: `concept_value` sums the carriers, so a figure that
            # is meant to BE the line rather than to join it has to silence the others. The printed
            # amount stays on the row in `value_raw`, as the row is real printed evidence and a
            # reader must still be able to see it.
            for other, ev in held[1:]:
                if ev.value_raw is None:
                    ev.value_raw = ev.value
                other.confidence.flags.append(
                    f"superseded_by_cascade:{parent_def.key}:{ev.value}")
                ev.value = None
        _write(target_row, basis, period, got.value)
        # THE TRAIL GOES ON THE ROW THAT CARRIES THE FIGURE. `parent` is the first carrier and
        # `target_row` is the one whose slot the rung wrote into; where a line is carried by
        # several printed rows those differ, and a trail on a row with no figure is a trail
        # nobody opening the number can find.
        target_row.derivation = note_sourced.derivation.record(
            target_row.derivation, basis=basis, period_label=period,
            derivation=note_sourced.derivation.build(
                method=f"cascade:{got.rung_used}",
                formula=" + ".join(
                    f"{'-' if i['sign'] < 0 else ''}{i['ref']}" for i in got.inputs),
                # A CASCADE INPUT IS ANOTHER LINE ITEM, AND IT SAYS SO NOW.
                #
                # These inputs are the SUB-LINE ITEMS the parent is assembled from — the one hop
                # that makes a main line traceable to the note rows underneath it — and the trail
                # recorded each as `label: "sub__pbt_oper_exp_depreciation"` with
                # `canonical_key: None`. Two consequences, both visible to a reader:
                #
                #   * the inspector printed a RAW KEY where every other contribution prints a
                #     caption, because the label was all it had;
                #   * and with no `canonical_key` there was nothing to click. `_contribution`
                #     comments that "an input is a note line, not a mapped concept", which is true
                #     of a note-row input and exactly false of this one — so the chain stopped at
                #     the parent and the sub-line's own citations were unreachable from it.
                #
                # `ref` IS the referenced line's key, so the link needs no new data: the key is
                # carried, the human label is looked up, and the referenced row's own provenance
                # rides along so the contribution is clickable to the page as well as navigable to
                # the line. A `const` term has no `ref` and stays a plain labelled number.
                inputs=[_cascade_input(i, by_key, defs) for i in got.inputs],
                result=got.value,
                flags=[f"rung:{got.rung_used}"]
                     + ([f"rungs_refused:{len(got.refused_rungs)}"] if got.refused_rungs else [])
                     + ([f"terms_missing:{len(got.missing)}"] if got.missing else [])
                     # The figure this rung replaced, in the trail and not only in a flag: the
                     # trail is what a reviewer opens to ask why a number is what it is, and
                     # "it displaced 128,412" is the first thing they need to see.
                     + ([f"displaced_printed:{displaced}"] if displaced is not None else [])))
        target_row.confidence.flags.append(f"cascade_rung:{got.rung_used}")
        ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: rung {got.rung_used} "
                f"-> {got.value} from {len(got.inputs)} term(s)")
        filled += 1
    return filled


def _slot(row: LineItem, basis: str, period: str):
    for ev in (row.values or {}).values():
        if _basis(ev) == basis and str(getattr(ev, "period_label", "") or "") == period:
            return ev
    return None


def _fill_childless_internal(all_items, children_of: dict[str, list], by_key: dict,
                             doc: DocumentModel, ctx: PipelineContext) -> int:
    """Compute an OFF-TEMPLATE INTERNAL line whose figure is pure arithmetic over other lines.

    THE GAP THIS CLOSES, and it was silent. `_fill_parents` evaluates a cascade only for a key that
    appears in `children_of`, and `children_of` holds a key only if some configured child of it has
    a figure. So a configured arithmetic line with NO children of its own was never evaluated at
    all: its `cascade` was stored, shown on the configuration screen, versioned — and read on no
    run. Measured while building the securities family: five such lines produced nothing, and
    because the line that referenced them took its term as `required`, the rung died and the
    constant fallback answered — the balance-sheet line published 0.0 with nothing saying why.

    WHY IT IS THIS NARROW, and the narrowness is the whole safety argument. Measured over the
    shipped set, 31 of the 39 configured arithmetic lines have no children — and every one of the
    31 is `namespace: "template"`, `in_output: True`: the statement subtotals (total assets, total
    current liabilities, gross profit, profit for the year). Those are computed by
    `services/rollups.evaluate` from the TEMPLATE's rollup tree, which is the single entry point
    the statement API, the Excel export and the KPI layer all read. Computing them here as well
    would be a second place computing one published quantity — the bug `_fill_by_cascade`'s own
    comment warns about, on the arithmetic that decides a figure.

    So this takes ONLY lines that are off-template and not published:

      * `namespace == "internal"` — outside the template, so no template rollup computes them
      * `in_output` false — not a published row
      * no children, so `_fill_parents` never had a chance at them
      * a `cascade` or `terms` to evaluate, and no figure already

    Measured against the shipped set, that population is EMPTY: every internal sub-line item either
    reads a note or has children. So this pass can add nothing to any existing configuration, and
    what it enables is a new internal line whose value is arithmetic — which is what
    `sub__fa_cp_intermediate_residual` is.

    PER (BASIS, PERIOD), and only in columns where an input actually appears: a rung is an
    arithmetic within one column, and evaluating one in a column none of its inputs reach would
    compute a figure the filing states in neither.
    """
    from app.services.line_items import evaluate as evaluate_line

    have_children = set(children_of) | set(_children_by_parent(all_items))
    candidates = [
        i for i in (all_items or ())
        if str(getattr(i, "namespace", "") or "") == "internal"
        and not getattr(i, "in_output", False)
        and i.key not in have_children
        and (getattr(i, "cascade", None) or getattr(i, "terms", None))
    ]
    if not candidates:
        return 0

    # Every figure in the document, per column — the same shape `_fill_by_cascade` builds, and for
    # the same reason: a term names a concept, not a parenthood.
    slots: dict[tuple[str, str], dict] = {}
    for row in doc.line_items:
        key = row.canonical_key
        if not key:
            continue
        for ev in (row.values or {}).values():
            if ev.value is None:
                continue
            slot = (_basis(ev), str(getattr(ev, "period_label", "") or ""))
            slots.setdefault(slot, {}).setdefault(key, ev.value)

    # IN DECLARED ORDER, so one of these may reference another declared before it. Not a full
    # topological walk for the reason `_in_dependency_order` gives: `services.line_items.build`
    # already does the real ordering and a second implementation of that graph is a second thing
    # to drift.
    filled = 0
    for item in sorted(candidates, key=lambda i: (int(getattr(i, "order", 0) or 0), i.key)):
        row = next((li for li in doc.line_items if li.canonical_key == item.key), None)
        wrote = []
        for (basis, period), known in sorted(slots.items()):
            refs = {t.ref for rung in (getattr(item, "cascade", None) or ())
                    for t in (getattr(rung, "terms", None) or ()) if getattr(t, "ref", None)}
            refs |= {t.ref for t in (getattr(item, "terms", None) or ())
                     if getattr(t, "ref", None)}
            # NOT THIS LINE'S COLUMN IF NONE OF ITS INPUTS IS IN IT.
            if not (refs & set(known)):
                continue
            if row is not None and _slot(row, basis, period) is not None:
                continue
            ev = evaluate_line(item, dict(known))
            if ev.value is None:
                if ev.rung_used is None and getattr(item, "cascade", None):
                    ctx.log(f"note_sourced:{item.key}: no rung resolved in {basis}/{period}"
                            + (f", refused for computing below zero: {ev.refused_rungs}"
                               if ev.refused_rungs else ""))
                continue
            if row is None:
                row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
                doc.line_items.append(row)
                by_key[item.key] = row
            _write(row, basis, period, ev.value)
            row.confidence.flags.append(f"computed_from_config:{ev.rung_used or 'terms'}")
            wrote.append(f"{basis}/{period}={ev.value}")
        if wrote:
            filled += 1
            ctx.log(f"note_sourced:{item.key}: computed from its own declaration — "
                    + ", ".join(wrote))
    return filled


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
