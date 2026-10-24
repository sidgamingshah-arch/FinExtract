"""Fill every line item that declares where in the notes its figure lives.

POSITION IN THE PIPELINE, and why it is this one. Immediately after ``link_notes``, which is where
the five removed derivation services ran. That matters for one reason beyond symmetry: ``normalize``
has already run by then, so the sign pass will not see these figures — they are written with
``value`` set to the amount the note gives, and ``value_raw`` too wherever the slot has no printed
figure of its own to keep (``_write``), which is what those services produced and what the
downstream tolerance checks were calibrated against. Running earlier would
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
from app.core.models.line_item import ExtractedValue, LineItem, base_note_number
from app.core.stage import PipelineContext, Stage
from app.services import note_sourced
from app.services.face_bases import (accepted_from, is_disclosure, note_basis_of, printed_bases,
                                      refuse_unprinted)


class NoteSourcedStage(Stage):
    name = "note_sourced"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        line_item_set = getattr(ctx, "line_items", None)
        items = _declared_items(line_item_set)
        if not items:
            ctx.log("note_sourced: no line item declares a note_source")
            return doc
        logs_at = len(ctx.logs)

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
        # WHICH NOTES EACH BY-MEANING LINE READS, one sibling per note — resolved once for the run
        # because it compares siblings (`line_item_notes.claimed_notes`).
        from app.services.line_item_notes import claimed_notes
        claimed = claimed_notes(items, doc.notes)
        # WHICH BLOCK OF A TWO-SIDED NOTE IS WHOSE, resolved once — see `note_sourced.block_sides`.
        sides = note_sourced.block_sides(doc, line_item_set)
        by_key = {li.canonical_key: li for li in doc.line_items if li.canonical_key}
        # THE BASES THE FACE STATEMENTS PRINT, read once for the run (`services.face_bases`). A
        # figure for any other basis has no statement to sit on, so every write below asks first.
        # Each helper further down reads the same answer for itself.
        bases = printed_bases(doc)
        # …EXCEPT A DISCLOSURE LINE'S, which may stand on its note's own explicit basis
        # (`face_bases.is_disclosure`, `note_basis_of`).
        defs = {i.key: i for i in all_items}
        # BELOW THIS, the model's figure and the declared note reading's are the same figure.
        tolerance = Decimal(str(getattr(getattr(line_item_set, "cross_check_master", None),
                                        "tolerance", None) or "0.5"))
        # THE FACE'S UNIT PER BASIS, read once: every note row published on a face line is put
        # into it, and the slot it lands in is stamped with it.
        face_units: dict[str, object] = {}

        def face_unit(basis: str):
            if basis not in face_units:
                face_units[basis] = _statement_unit(doc, basis)
            return face_units[basis]

        children_of: dict[str, list] = {}
        filled = touched = 0
        prose_filled = 0
        for item in items:
            # A `prose` LINE SKIPS THE ROW SEARCH. Its figure is a sentence and nothing else, so
            # running the row selection first would let a coincidental caption match publish a
            # tabulated number on a line the author said is never tabulated.
            #
            # THE SIX DEPRECIATION SPLITS BY FUNCTION NOW DECLARE IT — R&D, selling and marketing,
            # G&A, other operating expenses, the PBT note's operating-expense callout, and cost of
            # sales. They used to declare `note_tables` and reach their sentence through the
            # FALLBACK below, which meant a note table was tried first on every filing: and for
            # these six a table that appears to state a functional split is almost always the note
            # TOTAL the split is a component of, which is the mistake
            # `line_item_notes.caption_agrees_with_row_terms` had to catch after the fact. Refused
            # at the route instead.
            #
            # SO THE FALLBACK NOW HAS NO USER IN THE SHIPPED SET, deliberately and not by neglect:
            # they were its only six, and they are the only six lines carrying prose vocabulary at
            # all. It stays because it is a property of the ROUTE rather than of these lines — a
            # `note_tables` line that gains a `prose_subject` gets the sentence as a second chance,
            # which is the right default for a line whose author said its figure IS tabulated.
            hits = ([] if route_of(item) == "prose"
                    else note_sourced.select_rows(item, doc.notes, periods, note_sections,
                                                  claimed, sides))
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
                # ITS SIBLINGS — the other parts of the same parent — bound where a line that finds
                # its notes by meaning may look for the sentence when its own notes do not state it.
                prose = note_sourced.select_prose(
                    item, doc.notes, getattr(line_item_set, "prose_grammar", None),
                    siblings=[i for i in items
                              if getattr(i, "parent", None) and i.parent == getattr(item, "parent", None)])
                if not prose:
                    continue
                row = by_key.get(item.key)
                if row is None:
                    row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
                    doc.line_items.append(row)
                    by_key[item.key] = row
                touched += 1
                basis = _prose_basis(doc, [h.note_number for h in prose])
                scale = _prose_scale(doc)
                # SEVERAL SENTENCES FOR ONE PERIOD ARE AGGREGATED, NOT WRITTEN IN TURN.
                #
                # A note can state one function's charge in more than one sentence — depreciation
                # in one and amortisation in the next — and within a note those are COMPONENTS,
                # exactly as two rows are. `_write` replaces a slot it already holds, so writing
                # each hit as it came published the LAST sentence: measured, two sentences worth
                # 1,200 and 1,300 published 1,300, which is not the answer under any reading. The
                # item's own `rollup` decides, through the same `take_by_rollup` the ROW route
                # reads, so the two routes cannot drift apart. All six shipped prose lines declare
                # `sum`.
                rollup = str(getattr(item, "rollup", None) or "sum")
                by_period: dict[str, list] = {}
                for hit in prose:
                    by_period.setdefault(hit.period, []).append(hit)
                for period, sentences in by_period.items():
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
                    #
                    # SCALED AFTER THE ROLLUP, not before: the division is by one factor, so it is
                    # the same figure either way, and doing it once keeps the trail's stated-in-full
                    # amounts and the published figure in one relationship a reader can check.
                    stated, counted = note_sourced.take_by_rollup(
                        [h.amount for h in sentences], rollup)
                    amount = stated / scale if scale and scale != 1 else stated
                    if refuse_unprinted(doc, row, item.key, basis, period, amount,
                                        by="note_sourced", log=ctx.log, bases=bases):
                        continue
                    # THE MODEL'S ANSWER STANDS HERE TOO — see `_llm_holds`. It is not a correction
                    # of an answer the model gave from the same notes.
                    if _llm_holds(row, basis, period):
                        row.confidence.flags.append(f"prose_deferred_to_llm:{amount}")
                        _compare_with_model(row, item.key, basis, period, amount, tolerance, ctx)
                        ctx.log(f"note_sourced:{item.key}[{basis}:{period}]: prose {amount} "
                                f"NOT written — the model answered this row")
                        continue
                    _write(row, basis, period, amount, by="prose")
                    row.derivation = note_sourced.derivation.record(
                        row.derivation, basis=basis, period_label=period,
                        derivation=note_sourced.trail(
                            rollup="prose", item_label=item.label or item.key, amount=amount,
                            # EVERY SENTENCE THAT CONTRIBUTED, and under `alternatives` the ones
                            # that did not — the same contract the row trail keeps, so a reviewer
                            # reading either route sees what was offered as well as what was taken.
                            inputs=[{"label": f"note {hit.note_number}: {hit.sentence[:160]}",
                                     # The figure AS THE SENTENCE STATES IT, so the division by the
                                     # statement's scale is auditable against the words. The row
                                     # above carries the scaled figure and a `prose_scaled_by:`
                                     # flag, so the two are reconcilable; `excerpt` says so here
                                     # rather than leaving a reader to notice a contributions list
                                     # that reads a thousand times the total above it.
                                     "value": str(hit.amount), "counted": was_counted,
                                     "deducted": False, "note": hit.note_number,
                                     "excerpt": (f"stated in full in the sentence"
                                                 + (f"; the line publishes it divided by {scale}"
                                                    if scale and scale != 1 else "")),
                                     # THE PAGE. Without it a prose citation is an amount a reader
                                     # cannot go and look at — see `ProseHit.provenance`.
                                     "provenance": hit.provenance,
                                     # What the sentence put into the figure, stated in full; the
                                     # trail's `scale` is the division that follows.
                                     "used": str(hit.amount) if was_counted else "0",
                                     "status": (("deducted" if hit.amount < 0 else "used")
                                                if was_counted else "not_used"),
                                     **({} if was_counted else {"reason": "alternative"})}
                                    for hit, was_counted in zip(sentences, counted)],
                            display={"op": "first" if rollup == "alternatives" else "sum",
                                     "scale": str(scale) if scale and scale != 1 else None}))
                    filled += 1
                    prose_filled += 1
                row.is_computed = True
                # SAID ON THE ROW, because a figure read out of a sentence is not the same evidence
                # as one read off a tabulated row, and a reviewer cannot tell them apart from the
                # number. The flag names the note so the sentence can be found.
                row.confidence.flags.append(f"note_sourced_prose:{prose[0].note_number}")
                if scale and scale != 1:
                    row.confidence.flags.append(f"prose_scaled_by:{scale}")
                # SENTENCES, NOT FIGURES. Several sentences for one period now come to ONE figure
                # through the item's rollup, so counting them as figures overstated what was
                # published — `prose_figures` below counts the figures actually written.
                ctx.log(f"note_sourced:{item.key}: no row matched; {len(prose)} sentence(s) read "
                        f"from the PROSE of note {prose[0].note_number} into "
                        f"{len(by_period)} period(s) by rollup={rollup} "
                        f"(matched on {prose[0].matched_by!r})")
                children_of.setdefault(str(getattr(item, "parent", "") or ""), []).append(
                    (item, row))
                continue
            # WITHIN ONE NOTE the rows are components: a note that splits depreciation across
            # four assets prints four rows and the note's depreciation IS their sum. The child's
            # own `rollup` says so and defaults to `sum`. What the PARENT does with its several
            # children is a different question, answered below from the parent's declaration.
            rollup = str(getattr(item, "rollup", None) or "sum")
            # EACH ROW IN THE FACE'S UNIT FIRST — see `_in_face_units`. A rescaled row enters the
            # rollup at its face-unit figure; a slot with a row in another currency is not written.
            unit_flags, foreign = _in_face_units(item.key, hits, face_unit, ctx)
            resolved = note_sourced.resolve(hits, rollup)
            row = by_key.get(item.key)
            if row is None:
                row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
                doc.line_items.append(row)
                by_key[item.key] = row
            touched += 1
            for flag in unit_flags:
                if flag not in row.confidence.flags:
                    row.confidence.flags.append(flag)
            disclosure = is_disclosure(item.key, defs)
            for (basis, period), (amount, inputs) in sorted(resolved.items()):
                if (basis, period) in foreign:
                    a, b = foreign[(basis, period)]
                    ctx.log(f"note_sourced:{item.key}[{basis}:{period}]: {amount} NOT written — "
                            f"a row it reads is printed in {a}, the statement in {b}")
                    continue
                # THE ROWS THE FIGURE WAS COUNTED FROM, in `resolve`'s order, for a disclosure
                # line's note basis.
                counted = [h for h, inp in zip([h for h in hits
                                                if (h.basis, h.period) == (basis, period)], inputs)
                           if inp.get("counted")]
                if refuse_unprinted(doc, row, item.key, basis, period, amount,
                                    by="note_sourced", log=ctx.log, bases=bases,
                                    note_basis=note_basis_of(counted) if disclosure else ""):
                    continue
                # THE MODEL'S ANSWER STANDS — see `_llm_holds`. A `note_source` declaration is one
                # route to this row's figure and the model's reading of the notes is another; where
                # the model has answered, this fills nothing and the figure it gave feeds the
                # cascade below exactly as a note-sourced one would.
                if _llm_holds(row, basis, period):
                    row.confidence.flags.append(f"note_source_deferred_to_llm:{amount}")
                    _compare_with_model(row, item.key, basis, period, amount, tolerance, ctx)
                    ctx.log(f"note_sourced:{item.key}[{basis}:{period}]: {amount} NOT written — "
                            f"the model answered this row; its figure joins the cascade instead")
                    continue
                # THE FIGURE IS IN THE FACE'S UNIT, so it is stamped with it. Written without a
                # unit it took scale 1 beside RMB'000 siblings: China SCE 1966's other receivables
                # (note 24, 5,818,375) made `bs_ca`'s reconciliation `mixed_scale`. The unit is the
                # one THIS BASIS'S FACE carries — see `_statement_unit` — and a row printed in
                # another scale was converted to it above, so the stamp is true of the figure.
                _write(row, basis, period, amount, by="note_source",
                       unit_ctx=face_unit(basis))
                row.derivation = note_sourced.derivation.record(
                    row.derivation, basis=basis, period_label=period,
                    derivation=note_sourced.trail(
                        rollup=rollup, item_label=item.label or item.key, amount=amount,
                        inputs=inputs,
                        # An `alternatives` rollup takes the first row; any other adds them.
                        display={"op": "first" if rollup == "alternatives" else "sum"}))
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

        # EVERY ROW CARRYING THE KEY, not `by_key`'s one. `by_key` is
        # `{li.canonical_key: li for li in doc.line_items}` — last row wins — and a caption printed
        # on TWO statement instances produces two rows for one child key. A mainland filing prints
        # every primary statement twice, once consolidated and once for the parent company, so this
        # is the normal case and not an edge: the second row overwrote the first in `by_key`, the
        # cascade saw only it, and the parent came out carrying whichever entity's slots that row
        # happened to hold.
        #
        # Measured on 000709 before this loop was widened: `is_pl__other_non_operating_inc_exp`
        # published ONLY the parent company's 476,718,290.06 and had no consolidated figure at all,
        # although both 营业外 halves carried consolidated slots (488,570,415.03 and -56,388,111.32,
        # netting 432,182,303.71). A consolidated report served the parent company's number.
        #
        # Nothing here needs to choose between the rows, which is why taking all of them is the
        # whole fix: `_fill_parents` groups its offers by (basis, period) and combines within a
        # slot, so the consolidated rows meet the consolidated rows and the standalone rows the
        # standalone ones. The identity guard below is what keeps a row the note walk already
        # added from joining twice.
        def _enrol_children_filled_elsewhere() -> None:
            for parent_key, kid_keys in _children_by_parent(all_items).items():
                for kid in kid_keys:
                    item = next((i for i in all_items if i.key == kid), None)
                    if item is None:
                        continue
                    for row in doc.line_items:
                        if row.canonical_key != kid:
                            continue
                        if not any(ev.value is not None
                                   for ev in (row.values or {}).values()):
                            continue
                        if any(r is row for _i, r in children_of.get(parent_key, [])):
                            continue
                        children_of.setdefault(parent_key, []).append((item, row))
                        ctx.log(f"note_sourced:{parent_key}: child {kid} was filled elsewhere "
                                f"(model or earlier stage) and joins the cascade")

        _enrol_children_filled_elsewhere()

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
        # AND ENROL AGAIN, because a line can be BOTH — an arithmetic line with no children of its
        # own that is itself somebody's child. `sub__rp_find_1` is exactly that: the spec's first
        # reading is a sum over the related-party rows the balance sheet prints, and it is one of
        # the three readings its parent selects a maximum between.
        #
        # The sweep above ran before the pass that CREATES such a row, so the row did not exist to
        # be enrolled and the parent never saw it: Find 1 computed 1,650 from three face lines and
        # the column it feeds stayed empty. Enrolment writes no figures — it only records which
        # rows are whose children — so running it twice is free, and the identity guard inside
        # keeps a row already enrolled from joining a second time.
        _enrol_children_filled_elsewhere()
        parents = _fill_parents(children_of, by_key, doc, ctx,
                                _parent_rollup(all_items),
                                {i.key: i for i in all_items})
        # …AND AGAIN AFTER, for one whose inputs are a PARENT's computed figure rather than a
        # child's. Idempotent: the pass skips any slot that already carries a value, so a line
        # settled above is not recomputed here.
        standalone += _fill_childless_internal(all_items, children_of, by_key, doc, ctx)
        _settle_against_the_face(doc, ctx, all_items)
        # EVERY FIGURE THIS STAGE REFUSED FOR A BASIS THE STATEMENTS DO NOT PRINT, once per
        # (line, basis, period). Counted off this stage's own refusal lines rather than off the
        # `basis_not_printed:` flags: the model's route flags the same rows before this stage runs,
        # and an internal line refused before it was ever written has no row to carry a flag.
        refused = {line.split("] REFUSED ", 1)[0] for line in ctx.logs[logs_at:]
                   if line.startswith("note_sourced:") and "] REFUSED " in line}
        if refused:
            ctx.log(f"note_sourced:basis_refused={len(refused)} "
                    f"(face bases: {bases.describe()})")
        # …AND EVERY DISCLOSURE FIGURE ACCEPTED ON ITS NOTE'S OWN BASIS instead, counted the same way.
        accepted = {line.split("] ACCEPTED ", 1)[0] for line in ctx.logs[logs_at:]
                    if line.startswith("note_sourced:") and "] ACCEPTED " in line}
        if accepted:
            ctx.log(f"note_sourced:basis_accepted_from_note={len(accepted)} "
                    f"(face bases: {bases.describe()})")
        ctx.log(f"note_sourced: {touched} item(s) filled from notes, {filled} figure(s), "
                f"{parents} parent(s) resolved, {standalone} standalone line(s) computed")
        # THE DISCLOSURE A SUM OF NOTE TRANSACTIONS CARRIES — its items and its note text, quoted.
        # Here, once every cascade has resolved, and before `PruneNotesStage` drops the notes no face
        # row cites (a related-party note is cited by none). See `services.line_disclosure`.
        from app.services import line_disclosure
        carried = line_disclosure.attach(doc, line_item_set)
        if carried:
            ctx.log(f"note_sourced: {carried} line(s) carry the disclosure they were added up from")
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
    for the same reason: they are neither face nor notes, and those of their items that declare a
    `note_source` declare `note_terms` alone — which notes the model is shown — and no row or
    prose pattern for this stage to read.
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

    `anywhere` IS `note_tables` HERE, and that is still true: it searches rows and falls back to
    prose exactly as `note_tables` does. What distinguishes it lives outside this stage, in three
    places — `services.note_sections.open_to` refuses to narrow its note search to its own
    section, `services.pdf_extract` reconstructs EVERY page when any line declares it, and
    `services.line_item_llm` supplies those pages and accepts a citation naming one.

    `note_tables` AND `prose` NOW REFUSE THE FACE, which is not this function's doing and is worth
    saying here because this is where a reader comes to learn what a route means. The refusal is
    enforced where a face figure is actually written — `stages.map_ontology` for the deterministic
    proposal, `stages.line_item_llm` for a citation — and the predicate both read is
    `services.line_item_routes.may_read_face`.
    """
    route = str(getattr(item, "route", "") or "")
    return route if route in ("face", "note_tables", "prose", "anywhere") else "note_tables"


def _row_unit(row: LineItem):
    """The unit context this row's OTHER figures are in, if it has any."""
    for ev in (row.values or {}).values():
        uc = getattr(ev, "unit_ctx", None)
        if uc is not None and (uc.currency or uc.scale_factor != 1):
            return uc
    return None


def _doc_unit(doc):
    """The document's declared unit, where it declares one — see `_row_unit`."""
    uc = getattr(doc, "unit_context", None)
    if uc is not None and (uc.currency or uc.scale_factor != 1):
        return uc
    return None


def _statement_unit(doc, basis: str):
    """The unit the FACE figures of this basis carry, and the document's unit when there are none.

    A note figure joins the statement's own rows in every check that adds them, so it takes their
    unit. The document's unit is not always theirs: on an Indian filing the "(₹ in lakhs)" line is
    page chrome, so the face rows carry scale 1 while `normalize` reads the document as INR×100000,
    and a note figure written at INR×100000 beside them made `structural_checks` skip the balance
    sheet's section reconciliations as `mixed_scale`. On China SCE 1966 the face carries RMB'000,
    which is the document's unit, so the two answers agree. The most common unit wins; a tie goes
    to the first seen.

    THE CURRENCY IS THE DOCUMENT'S WHERE THE FACE NAMES NONE. What the checks compare is the scale;
    no check compares currencies. Those same Indian face rows name no currency, and taking their
    empty one dropped the document's from every note-route figure — the Disclosures amount read
    "1,120.00" where it had read "INR 1,120.00" — and on a mainland filing (face rows at no
    currency, the document at CNY) changed the unit of every note-route figure where the face's
    scale already agreed with the document's.
    """
    from app.core.models.enums import PrintedIn

    counts: dict[tuple[str, str], int] = {}
    first: dict[tuple[str, str], object] = {}
    for li in getattr(doc, "line_items", None) or ():
        if getattr(li, "printed_in", None) != PrintedIn.FACE:
            continue
        for ev in (li.values or {}).values():
            if (getattr(ev, "column_index", None) is not None or ev.value is None
                    or _basis(ev) != basis):
                continue
            uc = getattr(ev, "unit_ctx", None)
            if uc is None:
                continue
            key = (uc.currency or "", str(uc.scale_factor))
            counts[key] = counts.get(key, 0) + 1
            first.setdefault(key, uc)
    declared = _doc_unit(doc)
    if not counts:
        return declared
    best = max(counts.values())
    unit = first[next(k for k in first if counts[k] == best)]
    if not unit.currency and declared is not None and declared.currency:
        unit = unit.model_copy(update={"currency": declared.currency})
    return unit


def _in_face_units(key: str, hits: list, face_unit, ctx) -> tuple[list[str], dict]:
    """Put each selected note row into the unit of the face it is published on.

    A NOTE ROW CARRIES ITS OWN PAGE'S UNIT (`ExtractedValue.unit_ctx`, read off that page's header
    band), and the written figure is stamped with the FACE's (`_statement_unit`). Nothing compared
    the two, so a note printed in RMB millions under a face in RMB'000 was published a thousand
    times too small, and the stamp then hid it from the structural `mixed_scale` refusal too.

    Per row, against the face unit of the row's own basis (`services.units.compare`):

      * both scales declared and different: the row's amount is multiplied into the face's scale,
        the row keeps its printed figure for the trail (`printed`, `rescaled`), and the line gets
        `unit_rescaled:<from>:<to>`;
      * both currencies declared and different: no factor converts one into the other, so the
        slot the row belongs to is not written at all, and the line gets
        `unit_currency_mismatch:<note>:<face>`;
      * a unit the note page did not declare: the row is read as it always was.

    Returns the flags for the line and ``{(basis, period): (note currency, face currency)}`` for the
    slots that must not be written. The rows in `hits` are rescaled in place.
    """
    from app.services.units import compare

    flags: list[str] = []
    foreign: dict[tuple[str, str], tuple[str, str]] = {}
    for hit in hits:
        check = compare(getattr(hit.value, "unit_ctx", None), face_unit(hit.basis))
        if check.currency_mismatch:
            a, b = check.currency_mismatch
            foreign.setdefault((hit.basis, hit.period), (a, b))
            flag = f"unit_currency_mismatch:{a}:{b}"
            if flag not in flags:
                flags.append(flag)
            ctx.log(f"note_sourced:{key}[{hit.basis}:{hit.period}]: note {hit.note_number} row "
                    f"{hit.caption!r} is printed in {a}, the statement in {b} — not used")
            continue
        if check.rescaled:
            source, dest = check.rescaled
            hit.printed = hit.amount
            hit.rescaled = check.rescaled
            hit.amount = hit.amount * check.factor
            flag = f"unit_rescaled:{source}:{dest}"
            if flag not in flags:
                flags.append(flag)
            ctx.log(f"note_sourced:{key}[{hit.basis}:{hit.period}]: note {hit.note_number} row "
                    f"{hit.caption!r} printed {hit.printed} at scale {source}; "
                    f"{hit.amount} at the statement's scale {dest}")
    return flags, foreign


def _unit_of_terms(inputs, by_key: dict, basis: str, period: str):
    """The unit context of the figures a rung was computed FROM.

    A CASCADE'S VALUE IS ITS PARTS' VALUE, so it is in the parts' units. Created without one it
    took ``UnitContext``'s default — no currency, scale 1 — while every sibling on a filing that
    declares a scale carries RMB'000, and `services.structural_checks` then refuses the whole
    relation as `mixed_scale` rather than comparing thousands to units.

    MEASURED on China SCE 1966, which prints "Due to related parties 應付關聯方款項" 2,588,416 /
    2,583,308 at RMB'000. Giving `bs_cl__due_to_related_parties_cp` a face rung published the
    figure correctly and cost the filing its `bs_cl__total_current_liabilities` reconciliation —
    a relation that had tied EXACTLY at 131,532,808 and 123,650,889 — because the one
    cascade-written child arrived at scale 1. The figure was right and the section became
    unverifiable.
    """
    for term in inputs or ():
        row = by_key.get(str((term or {}).get("ref") or ""))
        if row is None:
            continue
        for ev in (row.values or {}).values():
            if (_basis(ev) == basis
                    and str(getattr(ev, "period_label", "") or "") == period):
                uc = getattr(ev, "unit_ctx", None)
                if uc is not None and (uc.currency or uc.scale_factor != 1):
                    return uc
    return None


def _write(row: LineItem, basis: str, period: str, amount: Decimal,
           *, by: str = "", provenance=None, unit_ctx=None) -> None:
    """Put the figure on the row, replacing that slot if it already has one.

    `value` is the new figure. `value_raw` STAYS THE PRINTED FIGURE: where the slot holds one the
    page printed, it is kept, so the two can be compared. Where the slot holds none — a fresh slot,
    or one an earlier writer computed or cited (`ExtractedValue.derived`) — there is nothing
    printed to keep, so `value_raw` is the new figure too and the slot is marked derived.
    `normalize` has already run, so there is no later pass to derive one from the other.

    `value_raw` USED TO BE OVERWRITTEN on every route but the model's, so a cascade that outranks a
    printed figure, or a note row published over a face row, left the page's figure nowhere on
    its own row — only in a flag.

    IT USED TO REPLACE A PRINTED FIGURE IN SILENCE, and that is what `by` and `provenance` are for.
    Measured with a spy provider: a face row matched by exact caption at confidence 1.0 holding
    9999, plus a note row stating 5170, published 5170 with the 9999 nowhere — not in a flag, not in
    the value, not in the trail. Three things were wrong with that and the first two are fixed here.

      * THE DISPLACED FIGURE LEFT NO RECORD. It does now, as `<by>_displaced_printed:<figure>` on
        the row's flags, or `<by>_displaced_derived:<figure>` when what it replaced was itself
        computed or cited, not printed. The spelling follows the two precedents rather than
        inventing a third:
        `stages.line_item_llm._write_prose` already appends `prose_value_displaced_printed:` for
        exactly this case on the prose path, and the parent rollup below already appends
        `note_sourced_differs_from_printed:`. `by` names the route that displaced it, because
        "the model overrode the face" and "a declared note source overrode the face" are different
        events to a reviewer and the flag was the only place left to tell them apart.
      * THE PROVENANCE BECAME A LIE. Only `value`/`value_raw` were assigned, so `basis`,
        `period_label`, `provenance` and `unit_ctx` still described the face row: click-to-source
        highlighted the statement page while the number came off a note page, and the `derivation`
        trail — which does carry the cited note and its own provenance — disagreed with the value
        sitting next to it. A caller that knows where its figure came from now passes `provenance`
        and it replaces the stale one.

    AND IT IS NEVER CLEARED WHEN THERE IS NOTHING TO REPLACE IT WITH. That was the obvious fix and
    it is measurably the wrong one: `periods.summable` deduplicates a fact printed twice only when
    the caption, the amount AND the page all match, so a provenance-less value cannot be recognised
    as a duplicate. The parent-rollup comment below records what that cost — 128,412 + 788,507 +
    788,507 published as 1,705,426 for a line whose rung computes 788,507. So a stale provenance is
    kept and NAMED (`<by>_kept_displaced_provenance`), which is a reviewer's problem to see rather
    than a silent one either way.

    THE THIRD DEFECT IS NOT HERE. `confidence.mapping` fell from the deterministic 1.0 to the
    model's own score with nothing recording that it had been higher; that belongs where the stamp
    is written, `stages.line_item_llm._write`.
    """
    for ev in (row.values or {}).values():
        if (_basis(ev) == basis and str(getattr(ev, "period_label", "") or "") == period):
            figure = ev.value if ev.value is not None else ev.value_raw
            displaced = figure is not None and figure != amount
            if displaced:
                kind = "derived" if ev.derived else "printed"
                row.confidence.flags.append(f"{by or 'value'}_displaced_{kind}:{figure}")
            if provenance is not None:
                ev.provenance = provenance
            elif displaced and getattr(ev, "provenance", None) is not None:
                row.confidence.flags.append(f"{by or 'value'}_kept_displaced_provenance")
            ev.value = amount
            if ev.derived or figure is None:
                ev.value_raw = amount
                ev.derived = True
            elif ev.value_raw is None:
                ev.value_raw = figure
            return
    from app.core.models.enums import Basis
    try:
        basis_enum = Basis(basis) if basis else Basis.CONSOLIDATED
    except ValueError:
        basis_enum = Basis.CONSOLIDATED
    # THE UNIT TRAVELS WITH THE FIGURE — see `_unit_of_terms`. A fresh slot has no unit of its
    # own, so it takes the one its inputs were read in, or failing that the one this row's other
    # column already carries. Omitted, the value defaulted to scale 1 and made its whole section
    # `mixed_scale`.
    unit = unit_ctx or _row_unit(row)
    row.set_value(ExtractedValue(
        basis=basis_enum, period_label=period or None, value=amount, value_raw=amount,
        derived=True, provenance=provenance, **({"unit_ctx": unit} if unit is not None else {})))


def _refigure(ev, amount: Decimal) -> None:
    """Change a slot's figure in place. A printed slot keeps the page's figure in `value_raw`; a
    derived one has none to keep, so `value_raw` moves with it (see `_write`)."""
    ev.value = amount
    if ev.derived:
        ev.value_raw = amount


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


def _compare_with_model(row: LineItem, key: str, basis: str, period: str, amount: Decimal,
                        tolerance: Decimal, ctx) -> None:
    """The model's figure took a slot the declared note reading also fills: compare the two.

    The model's stands (`_llm_holds`); what was missing was any test of whether the two readings
    agree. Where they differ by more than the cross-check tolerance the row carries
    `llm_note_route_disagree:<model>:<note>`, one per slot, so the review queue can put both
    figures in front of a reviewer.
    """
    ev = _slot(row, basis, period)
    held = getattr(ev, "value", None)
    if held is None or amount is None:
        return
    if abs(Decimal(str(held)) - Decimal(str(amount))) <= tolerance:
        return
    row.confidence.flags.append(f"llm_note_route_disagree:{held}:{amount}")
    ctx.log(f"note_sourced:{key}[{basis}:{period}]: the model's {held} and the note reading's "
            f"{amount} disagree")


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

def _prose_basis(doc, notes_cited=()) -> str:
    """The basis a prose figure is filed under.

    A SENTENCE CARRIES NO BASIS COLUMN, unlike a note row, so one has to be chosen — and the NOTE
    it was read from is what knows. In order:

      1. THE NOTE'S OWN BASIS, where every note the figure was read from agrees on one. A
         company-only chapter (母公司财务报表主要项目注释) is tagged STANDALONE by
         `notes_extract`, and a sentence in it describes the company's figures.
      2. THE SOLE BASIS THE FACE STATEMENTS PRINT, when the document is known to print one
         (`services.face_bases`); CONSOLIDATED when it prints both. A note under no company-only
         heading describes the GROUP wherever there is one: that is how both CAS and HKFRS number
         their notes, the group's first and the parent's in a chapter of their own. Read off the
         classifier's page verdicts rather than off the values, because a note page misread as a
         statement carries consolidated figures on a filing that prints only the company's.
      3. For a document whose pages say nothing (no classified page): CONSOLIDATED when any
         consolidated figure exists, otherwise the face's own prevailing basis.

    THE PREVIOUS RULE WAS STEP 3 ALONE, a majority vote over the FACE's values, and it asked the
    wrong population. A mainland filing prints the parent company's statements beside the
    group's, often with more lines: 300319's face carries 335 standalone values against 260
    consolidated, so every prose figure on that filing — every one read from a consolidated note —
    was filed in the parent-company column.

    `notes_cited` is the note numbers the figure was read from; empty keeps steps 2 and 3.
    """
    from app.services.note_sourced import _notes_by_number

    def value(b) -> str:
        return str(getattr(b, "value", b) or "")

    wanted = [n for n in (notes_cited or ()) if n]
    if wanted:
        bases = set()
        for number in wanted:
            for table in _notes_by_number(getattr(doc, "notes", None) or (), str(number)):
                bases.add(value(getattr(table, "basis", None)) or "consolidated")
        if len(bases) == 1:
            return next(iter(bases))

    faces = printed_bases(doc)
    if faces.known:
        return faces.sole or "consolidated"

    seen: dict[str, int] = {}
    for li in doc.line_items:
        for ev in (li.values or {}).values():
            basis = value(getattr(ev, "basis", ""))
            if basis:
                seen[basis] = seen.get(basis, 0) + 1
    if not seen or "consolidated" in seen:
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
        # THREE WAYS ONE PARENT READS ANOTHER, and this used to see only the first. A rung term
        # names it; a direct `terms` declaration names it — `_fill_parents` sends those to
        # `_fill_by_cascade` too, so they are evaluated exactly as a cascade is; or it is simply
        # this parent's CHILD, which a `rollup: sum` parent adds and a cascade parent reads through
        # `known`. Missing the second and third let an outer parent be evaluated before a child
        # parent it contains, marked `done`, and never revisited — so its figure left that child
        # out. `_enrol_with_its_own_parent` adds the child row afterwards, but to a parent that
        # will not be evaluated again.
        terms = [t for rung in (getattr(definition, "cascade", None) or ())
                 for t in (getattr(rung, "terms", None) or ())]
        terms += list(getattr(definition, "terms", None) or ())
        refs = {t.ref for t in terms if getattr(t, "ref", None)}
        refs |= {k for k in children_of
                 if str(getattr(defs.get(k), "parent", "") or "") == key}
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
    def _enrol_with_its_own_parent(key: str) -> None:
        """Record the row just computed for ``key`` as a child of ITS parent.

        A PARENT CAN BE SOMEBODY'S CHILD, and until the row exists there is nothing to enrol.
        The two sweeps in the caller cover a child filled by the model or by
        `_fill_childless_internal`; neither can cover one filled HERE, because this is the pass
        that creates it. `_in_dependency_order` already places a parent after any parent its own
        cascade names, so by the time the outer parent is reached the inner one's row exists — it
        just was not yet anybody's child.

        MEASURED ON 河钢股份 000709. `sub__cp_other_receivables_net` became a two-rung cascade so it
        could read an explicit net COLUMN or a plain reported row, which made it a parent as well
        as a child. Its own figure was computed correctly and `bs_ca__other_receivables_cp`
        published NOTHING: none of that parent's other ten children appears on this filing, so it
        had no enrolled child, no `child_slots`, and no column to evaluate its cascade in.

        Only the COLUMNS come from the children — `_fill_by_cascade` already reads every row's
        value into `known` — so what this restores is which columns the outer cascade may attempt.
        """
        definition = defs.get(key)
        # ITS PARENT, AND EVERY LINE THAT RECEIVES IT (`Term.received`): the related-party part of
        # other receivables is computed in this pass and is Due from Related Parties (CP)'s input too.
        owners = [str(getattr(definition, "parent", "") or "")] + [
            receiver for receiver, ref in _received_terms(defs.values()) + _deducted_terms(
                defs.values()) if ref == key]
        if definition is None:
            return
        row = next((r for r in doc.line_items if r.canonical_key == key), None)
        if row is None or not any(ev.value is not None for ev in (row.values or {}).values()):
            return
        for owner in owners:
            if not owner or any(r is row for _i, r in children_of.get(owner, [])):
                continue
            children_of.setdefault(owner, []).append((definition, row))
            ctx.log(f"note_sourced:{owner}: child {key} is itself a parent, computed in this pass "
                    f"and joins the cascade")

    # REPEATED UNTIL NO NEW PARENT APPEARS, because `_enrol_with_its_own_parent` can make a key a
    # parent that was not one when the order was computed. `_in_dependency_order` builds its list
    # from `children_of` UP FRONT, so a parent enrolled during the pass is not in that list and was
    # never visited: on 河钢股份 000709 `bs_ca__other_receivables_cp` has no other child on the
    # filing, so it entered this function with no children at all, gained one the moment
    # `sub__cp_other_receivables_net` was computed, and published nothing because the walk was
    # already fixed. A second pass costs one recomputation of a sort over eight keys.
    #
    # BOUNDED BY THE CHAIN LENGTH, not by convergence: each pass computes at least one parent that
    # the previous pass could not, so the number of passes cannot exceed the number of parents, and
    # `done` makes re-entry impossible. A cycle therefore stops rather than spinning.
    resolved = 0
    done: set[str] = set()
    bases = printed_bases(doc)
    for _pass in range(len(children_of) + 2):
        ordered = [(k, kids) for k, kids in _in_dependency_order(children_of, defs)
                   if k and k not in done]
        if not ordered:
            break
        done.update(k for k, _kids in ordered)
        for parent_key, kids in ordered:
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
                _enrol_with_its_own_parent(parent_key)
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
            row_of: dict[int, LineItem] = {}
            for item, child in kids:
                for ev in (child.values or {}).values():
                    if ev.value is None:
                        continue
                    row_of[id(ev)] = child
                    by_slot.setdefault((_basis(ev), str(getattr(ev, "period_label", "") or "")),
                                       []).append((item, ev))

            for (basis, period), offers in sorted(by_slot.items()):
                # ONE PRINTED ROW IS ONE QUANTITY, on this path as on the cascade path. The
                # same-row guard was added to `services.line_items._apply_terms`, which only a
                # cascade or `terms` parent reaches; a plain `rollup: sum` parent still added every
                # offer, so two sibling parts citing one note total — asked in separate one-line
                # requests, neither shown the other — published it twice. `_row_identity` is the
                # same test, and it returns None where a row cannot be told apart from another, so
                # an offer with no provenance is never dropped on a guess.
                if rollup != "alternatives":
                    seen_rows: set[tuple] = set()
                    kept = []
                    for item, ev in offers:
                        ident = _row_identity(ev)
                        if ident is not None and ident in seen_rows:
                            ctx.log(f"note_sourced:{parent_key}: {item.key} reads a row already "
                                    f"counted in {basis}/{period} — added once")
                            continue
                        if ident is not None:
                            seen_rows.add(ident)
                        kept.append((item, ev))
                    offers = kept
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
                taken_from = offers[:1] if rollup == "alternatives" else offers
                if refuse_unprinted(doc, parent, parent_key, basis, period,
                                    offers[0][1].value if rollup == "alternatives" else sum(
                                        (o[1].value for o in offers), Decimal(0)),
                                    by="note_sourced", log=ctx.log, bases=bases,
                                    note_basis=(_parts_note_basis(
                                        [row_of[id(ev)] for _i, ev in taken_from], basis, period)
                                        if is_disclosure(parent_key, defs) else "")):
                    continue
                if rollup == "alternatives":
                    item, ev = offers[0]
                    _write(parent, basis, period, ev.value,
                           unit_ctx=getattr(ev, "unit_ctx", None))
                    parent.confidence.flags.append(f"note_sourced_from:{item.key}")
                    if len(offers) > 1:
                        parent.confidence.flags.append(
                            f"note_sourced_alternatives_available:{len(offers) - 1}")
                        ctx.log(f"note_sourced:{parent_key}: took {item.key}, "
                                f"{len(offers) - 1} other source(s) available")
                else:
                    total = sum((ev.value for _i, ev in offers), Decimal(0))
                    _write(parent, basis, period, total,
                           unit_ctx=next((getattr(ev, "unit_ctx", None) for _i, ev in offers
                                          if getattr(ev, "unit_ctx", None) is not None), None))
                    parent.confidence.flags.append(f"note_sourced_sum_of:{len(offers)}")
                resolved += 1
            _enrol_with_its_own_parent(parent_key)
    return resolved


def _cascade_input(term: dict, by_key: dict, defs: dict | None) -> dict:
    """One cascade term as a contribution — carrying the line it REFERS TO, not just its key.

    See the comment at the call site for what this fixes. The label falls back through the
    definition's own label, then the extracted row's printed caption, then the bare key, so a
    referenced line that is configured but was never extracted still reads as a name.
    """
    ref = str(term.get("ref") or "")
    row = by_key.get(ref) if ref else None
    out = {
        "label": _label_of(ref, by_key, defs),
        "value": term.get("value"),
        "counted": True,
        "deducted": term.get("sign", 1) < 0,
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


def _label_of(ref: str, by_key: dict, defs: dict | None) -> str:
    """A referenced line's name: its definition's label, the extracted row's printed caption, the
    bare key — or "fixed number" for a term that refers to no line."""
    row = by_key.get(ref) if ref else None
    definition = (defs or {}).get(ref) if ref else None
    return (getattr(definition, "label", "") or getattr(row, "source_label", "") or ref
            or "fixed number")


def _json_list(value) -> list:
    """A row identity as JSON keeps it: a list, with the cell corner as a list too."""
    return [list(v) if isinstance(v, tuple) else v for v in value]


def _cascade_inputs(got, op: str, by_key: dict, defs: dict | None, sources: dict | None,
                    cells: dict | None) -> list[dict]:
    """A cascade's trail inputs, each saying what it put into the figure.

    `_cascade_input` per term, plus what the evaluation already knew and the trail used to drop:
    the term's sign and role, what it ADDED (`used`) and why (`status`, `reason` — see
    `services.line_items.term_statuses`), the earlier term that already counted a row read twice
    (`via`), and the cell the part printed in THIS period (`period_provenance`, from `cells`).

    ADDED FIELDS ONLY. `label`, `value`, `counted`, `deducted`, `canonical_key` and `provenance` are
    exactly `_cascade_input`'s, because the face settlement follows them (`row_pieces`) and the
    shared-figure detector compares them; and no field is named `ref`, which `row_pieces` follows
    as it follows `canonical_key`. The evaluation's entries are not changed.
    """
    from app.services.line_items import term_statuses

    out: list[dict] = []
    for idx, (entry, (status, adds, reason)) in enumerate(
            zip(got.inputs, term_statuses(got.inputs, op))):
        inp = _cascade_input(entry, by_key, defs)
        inp["sign"] = int(entry.get("sign") or 1)
        inp["role"] = entry.get("role")
        inp["used"] = str(adds)
        inp["status"] = status
        if reason is not None:
            inp["reason"] = reason
        if entry.get("abs"):
            inp["abs"] = True
        if "selected" in entry:
            inp["selected"] = bool(entry["selected"])
        duplicate = entry.get("duplicate_of_row")
        if duplicate is not None:
            inp["duplicate_of_row"] = _json_list(duplicate)
            row = tuple(duplicate)
            first = next((e for e in got.inputs[:idx]
                          if e.get("duplicate_of_row") is None and e.get("ref")
                          and (sources or {}).get(e["ref"]) == row), None)
            if first is not None:
                inp["via_key"] = first["ref"]
                inp["via"] = _label_of(first["ref"], by_key, defs)
        ref = entry.get("ref")
        if ref and cells is not None:
            cell = cells.get(ref) or {}
            # The KEY is written even when the part's figure has no page, so the reader knows not
            # to fall back to `provenance` — which is the part's first figure, possibly last year's.
            inp["period_provenance"] = cell.get("provenance")
            caption = str(cell.get("caption") or "")
            if caption and caption != inp["label"]:
                inp["caption"] = caption
        out.append(inp)
    return out


def _cascade_display(got, parent_def, defs: dict | None) -> dict:
    """How the answering rung combines its terms, which of its terms the filing did not print, and
    which rungs above it were passed over — the trail-level half of a cascade's arithmetic."""
    rung = next((r for r in (getattr(parent_def, "cascade", None) or ())
                 if str(getattr(r, "id", "")) == str(got.rung_used)), None)
    if rung is not None:
        op = str(getattr(rung, "terms_op", None) or "sum")
        terms = list(getattr(rung, "terms", None) or ())
    else:
        op = str(getattr(parent_def, "terms_op", None) or "sum")
        terms = list(getattr(parent_def, "terms", None) or ())
    role_of = {t.ref: t.role for t in terms if getattr(t, "ref", None)}
    missing = [{"key": ref,
                "label": str(getattr((defs or {}).get(ref), "label", "") or ref),
                "role": str(role_of.get(ref, "") or "")}
               for ref in got.missing if ref != "fixed number"]
    return {"op": op, "missing": missing, "refused": list(getattr(got, "refusals", None) or [])}


def _row_identity(ev) -> tuple | None:
    """WHICH PRINTED ROW this figure came off, or None when that cannot be told.

    THE SAME RULE `periods.summable` ALREADY USES — caption, amount and page — because the two are
    answering one question and a second spelling of "these are the same fact" is how they drift.
    Here it identifies a cascade INPUT rather than a published carrier, so that a rung summing two
    terms that read one row adds it once (`services.line_items._apply_terms`).

    WHY IT MATTERS MOST ON THE MODEL'S ROUTE. `stages.line_item_llm` asks, per line item, which row
    of the notes holds its figure, and `llm_request_grouping` ships as "none" — one request per
    line. So two sibling parts competing for one note are asked in SEPARATE calls, neither request
    naming the other line, and the prompt's own rule ("citing it for one line is also saying it is
    not another's") cannot fire because the model is never shown the conflict. Measured with a spy
    provider on 澜起科技 688008: note 七、19's 合计 was cited by both
    `sub__ltp_other_fincl_assets_note_total` and `sub__ltp_fincl_assets_note_total`, and one note
    row of 575,243,925.97 reached the LTP column as 1,150,487,851.94. Four rows of that filing were
    each cited by between two and eight line items.

    NONE WHERE THERE IS NO DISCRIMINATOR, and that is the conservative direction. A value with no
    page, or with neither a caption nor a box, cannot be told apart from a different row carrying
    the same amount — so it is not deduplicated at all and the arithmetic stays as it was. Better a
    double count that a reviewer can still see than a deduplication that silently drops a real
    second figure.
    """
    prov = getattr(ev, "provenance", None)
    page = getattr(prov, "page_index", None) if prov is not None else None
    if page is None:
        return None
    caption = str(getattr(prov, "text_snippet", None) or "").strip()
    box = getattr(prov, "value_bbox", None) or getattr(prov, "bbox", None)
    if not caption and box is None:
        return None
    # The BOX is the cell and the caption is the row; either alone distinguishes two rows of one
    # note, and both together distinguish two columns of one row — which matters because a slot is
    # already per (basis, period) and two terms in one slot reading one cell is the case in hand.
    cell = (round(float(box.x0), 4), round(float(box.y0), 4)) if box is not None else None
    return (int(page), caption, cell, str(ev.value))


def _column_figures(doc: DocumentModel) -> tuple[dict, dict]:
    """Every figure in every column by concept, and the printed row each came off.

    What a cascade is evaluated against — `services.line_items.evaluate` wants "every figure known
    so far" — read once from the document, so `_fill_by_cascade` and the face settlement that runs
    a calculation again evaluate the same thing.

    THE PRINTED ROW EACH FIGURE CAME OFF, per (slot, key) — see `_row_identity` and
    `services.line_items._apply_terms`. Two of a rung's terms whose figures came off the SAME row
    are one quantity found twice, not two quantities, and this is what lets the sum say so.
    """
    slots: dict[tuple[str, str], dict] = {}
    sources: dict[tuple[str, str], dict] = {}
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
            if key not in slots.setdefault(slot, {}):
                slots[slot][key] = ev.value
                identity = _row_identity(ev)
                if identity is not None:
                    sources.setdefault(slot, {})[key] = identity
    return slots, sources


def _column_provenance(doc: DocumentModel) -> dict:
    """``{(basis, period): {key: {"provenance", "caption"}}}`` — where each figure a cascade reads
    was printed, IN ITS OWN COLUMN.

    The same first writer as `_column_figures` — the first row in document order with a figure in
    that column, with a page or without — so the cell named is the one whose figure the rung used.
    For the trail's display only: `_cascade_input`'s `provenance` is the referenced row's first
    figure of any column, which put last year's input on this year's cell, and it stays as it was
    because the engine reads it.
    """
    out: dict[tuple[str, str], dict] = {}
    for row in doc.line_items:
        key = row.canonical_key
        if not key:
            continue
        for ev in (row.values or {}).values():
            if ev.value is None:
                continue
            slot = (_basis(ev), str(getattr(ev, "period_label", "") or ""))
            cells = out.setdefault(slot, {})
            if key in cells:
                continue
            prov = getattr(ev, "provenance", None)
            cells[key] = {
                "provenance": note_sourced.derivation._json_safe_provenance(prov),
                "caption": str(getattr(prov, "text_snippet", None) or "") if prov is not None
                           else ""}
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

    # THE CARRIER A RUNG'S FIGURE ARRIVES ON BESIDE THE PRINTED ROW — see
    # `CascadeRung.adds_to_printed`. One per parent, made the first time a slot needs it. A parent
    # row this function had to create is already such a carrier: it holds nothing printed.
    arrived: list[LineItem] = [parent] if not any(
        ev.value is not None for li in carriers for ev in (li.values or {}).values()) else []

    def arrival() -> LineItem:
        if not arrived:
            row = LineItem(source_label=_arrival_label(parent_def, kids),
                           canonical_key=parent_def.key)
            row.confidence.flags.append(ARRIVED_BESIDE_PRINTED)
            row.is_computed = True
            doc.line_items.append(row)
            arrived.append(row)
        return arrived[0]

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
    slots, sources = _column_figures(doc)
    # Where each of those figures was printed, per column — for the trail's page chips only.
    cells = _column_provenance(doc)
    # A COLUMN WITH NO CHILD FIGURE AT ALL IS NOT THIS PARENT'S TO FILL. Widening `known` above
    # also widened the set of columns this loop would attempt, which would have it evaluate a
    # cascade in a column where none of its own parts appear — so the columns are still taken from
    # the children, and only the VALUES visible within them are widened.
    child_slots = {(_basis(ev), str(getattr(ev, "period_label", "") or ""))
                   for _item, child in kids
                   for ev in (child.values or {}).values() if ev.value is not None}
    slots = {slot: known for slot, known in slots.items() if slot in child_slots}

    filled = 0
    bases = printed_bases(doc)
    for (basis, period), known in sorted(slots.items()):
        got = evaluate_line(parent_def, known, sources.get((basis, period)))
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
        # A RUNG WHOSE FIGURE ARRIVES BESIDE THE PRINTED ONE (`CascadeRung.adds_to_printed`) does
        # not compete with whatever holds the line's printed row — the face's figure, or the
        # model's reading of it — so neither the deferral below nor the contest further down
        # applies to it. It is written on a carrier of its own, `_arrival_row`.
        rung_def = next((r for r in parent_def.cascade or () if r.id == got.rung_used), None)
        beside = rung_def is not None and getattr(rung_def, "adds_to_printed", False)
        # A RUNG THAT TAKES A PART OUT OF THE LINE'S OWN PRINTED ROW (`CascadeRung.
        # deducts_from_printed`) is not a contest either, and not the model's to overrule: whatever
        # holds the printed row — the face's figure or the model's reading of it — is what the part
        # comes out of, so it is computed from those carriers and written into the first of them.
        if rung_def is not None and getattr(rung_def, "deducts_from_printed", False):
            if _deduct_from_printed(parent_def, rung_def, got, carriers, by_key, defs or {}, doc,
                                    ctx, basis, period, bases, sources.get((basis, period)),
                                    cells.get((basis, period))):
                filled += 1
            continue
        # …AND WHERE THAT LINE COULD NOT TAKE THE PART OUT, the part's own line does not take it
        # either: the part is still inside the other line's printed row, and taking it here as well
        # would count it twice in the section.
        unmoved = [str(entry.get("ref")) for entry in got.inputs
                   if entry.get("ref") and _part_not_deducted(doc, str(entry["ref"]), basis, period)]
        if unmoved:
            ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: rung {got.rung_used} NOT "
                    f"applied — {', '.join(unmoved)} could not be taken out of the printed row "
                    f"it is inside")
            continue
        llm_answered = [li for li in carriers
                        if str(getattr(li.confidence, "method", "") or "").lower().endswith("llm")
                        and (ev := _slot(li, basis, period)) is not None and ev.value is not None]
        if llm_answered and not beside:
            ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: cascade "
                    f"{got.rung_used}={got.value} NOT applied — the model answered this line "
                    f"({len(llm_answered)} row(s)); the declared cascade does not overrule it")
            for li in llm_answered:
                li.confidence.flags.append(f"cascade_deferred_to_llm:{got.rung_used}:{got.value}")
            continue
        # A COLUMN THE STATEMENTS DO NOT PRINT IS NOT THIS CASCADE'S TO FILL — and, refused here,
        # it neither writes nor supersedes the printed carriers below.
        if refuse_unprinted(doc, parent, parent_def.key, basis, period, got.value,
                            by="note_sourced", log=ctx.log, bases=bases,
                            note_basis=(_inputs_note_basis(doc, got.inputs, basis, period)
                                        if is_disclosure(parent_def.key, defs or {}) else "")):
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
        if beside:
            # NOT A CONTEST: the printed carriers keep their figures and the rung's is one more
            # carrier of the line, which `concept_value` adds to them.
            target_row, existing = arrival(), None
        if existing is not None and existing.value is not None:
            if existing.value == got.value and len(held) == 1:
                continue
            # WHICH WINS — THE PRINTED FIGURE OR THE RESOLVED RUNG. Two conditions, and both are
            # declarations rather than heuristics:
            #
            #   `type: derived` or `extracted`  the line's figure may be a judgement between
            #                                   disclosures (a `calculated`/`intermediate` line's
            #                                   never is — see the end of this comment)
            #   `rung.outranks_printed`         THIS rung reconstructs something the face does not
            #                                   state
            #
            # AN EXTRACTED LINE MAY DECLARE IT TOO, and the case is a face row that prints MORE than
            # the line. PetroChina's face 'Short-term borrowings' 138,783 binds ST Bank Loans Payable
            # by its caption, and note 29 splits it: 'Short-term borrowings excluding current portion
            # of long-term borrowings' 45,955 and 'Current portion of long-term borrowings' 92,828.
            # The line is the first (its definition says so); a printed figure ordinarily stands
            # over a cascade, so the note rung decided nothing while the line was extracted. Making
            # the line `derived` instead would move its aliases to a face part and re-tag every
            # 短期借款 row the corpus prints — a CAS face row that IS the line. The rung that reads
            # the split declares `outranks_printed`; the face row stays the line wherever the note
            # prints no split, which is the fallback.
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
            if (str(getattr(parent_def, "type", "") or "") not in ("derived", "extracted")
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
                    f"({len(held)} row(s)) — the rung outranks the printed figure")
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
        _write(target_row, basis, period, got.value,
               unit_ctx=_unit_of_terms(got.inputs, by_key, basis, period))
        # THE TRAIL GOES ON THE ROW THAT CARRIES THE FIGURE. `parent` is the first carrier and
        # `target_row` is the one whose slot the rung wrote into; where a line is carried by
        # several printed rows those differ, and a trail on a row with no figure is a trail
        # nobody opening the number can find.
        #
        # AND IT RECORDS THE RUNG'S ARITHMETIC — what each term added, how the rung combines them,
        # which of its terms the filing did not print and which rungs were passed over — so the
        # inspector can show the trail adding up to the figure. Display fields only: the method,
        # the raw-key formula, the result and the flags below are exactly what they were.
        display = _cascade_display(got, parent_def, defs)
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
                inputs=_cascade_inputs(got, display["op"], by_key, defs,
                                       sources.get((basis, period)), cells.get((basis, period))),
                result=got.value,
                flags=[f"rung:{got.rung_used}"]
                     + ([f"rungs_refused:{len(got.refused_rungs)}"] if got.refused_rungs else [])
                     + ([f"terms_missing:{len(got.missing)}"] if got.missing else [])
                     # The figure this rung replaced, in the trail and not only in a flag: the
                     # trail is what a reviewer opens to ask why a number is what it is, and
                     # "it displaced 128,412" is the first thing they need to see.
                     + ([f"displaced_printed:{displaced}"] if displaced is not None else []),
                display=display))
        target_row.confidence.flags.append(f"cascade_rung:{got.rung_used}")
        # Per SLOT, not per row: one basis may be read out of the face's captions and another
        # printed as a line of its own. See `CascadeRung.carved_from_face`.
        from app.services.rollups import CARVED_FROM_FACE
        if rung_def is not None and rung_def.carved_from_face and not _face_prints_line(
                parent_def, by_key, defs or {}, basis, period):
            slot = _slot(target_row, basis, period)
            if slot is not None and CARVED_FROM_FACE not in slot.confidence.flags:
                slot.confidence.flags.append(CARVED_FROM_FACE)
        ctx.log(f"note_sourced:{parent_def.key}[{basis}:{period}]: rung {got.rung_used} "
                f"-> {got.value} from {len(got.inputs)} term(s)")
        filled += 1
    return filled


#: Raised on the carrier a rung's figure is written on beside the line's printed row — see
#: `CascadeRung.adds_to_printed`.
ARRIVED_BESIDE_PRINTED = "arrived_beside_printed"


def _arrival_label(parent_def, kids: list) -> str:
    """What the carrier of an `adds_to_printed` rung's figure is called: the parts it is read from,
    as their own labels say, so the row reads as what it is rather than as a second copy of the
    line ("Other business revenue (revenue note)" beside "加：其他收益")."""
    labels = [str(getattr(item, "label", "") or getattr(item, "key", "")) for item, _row in kids]
    labels = [x for i, x in enumerate(labels) if x and x not in labels[:i]]
    return " + ".join(labels) or str(getattr(parent_def, "label", "") or parent_def.key)


def _face_prints_line(parent_def, by_key: dict, defs: dict, basis: str, period: str) -> bool:
    """Whether the face prints this derived line AS A LINE OF ITS OWN, for one slot.

    Answered by the line's own FACE parts — the terms of its cascade whose route is ``face``. China
    SCE 1966 prints "Due to related parties 應付關聯方款項" 2,588,416, bound to
    ``sub__rp_face_due_to_cp``; the note rung outranks that part and reads the same amount, and the
    face row is counted nowhere but through this line. So a ``carved_from_face`` rung is carved
    only where no face part of the line holds a figure: 000709 prints no such row, and its
    related-party amounts sit inside 其他应付款 and 长期应付款.

    THE LINE'S OWN STATEMENT'S FACE. A face term printed on ANOTHER statement says nothing about
    whether this one prints the line: Deprec & Impairment(Oper Exp) reads the cash-flow add-back
    (`sub__cfo_depreciation`, `cf_oper_indirect__depreciation`) as a fallback rung, and every filing
    prints that row — so the line's note figure was never carved, and the computed profit before tax
    counted the depreciation inside the administrative expenses a second time (BYD 5,465,598).
    """
    own = _statement_name(getattr(parent_def, "statement", None))
    for rung in parent_def.cascade or ():
        for term in rung.terms:
            part = defs.get(term.ref) if term.ref else None
            route = getattr(part, "route", None) if part is not None else None
            if str(getattr(route, "value", route) or "") != "face":
                continue
            printed_on = _statement_name(getattr(part, "statement", None))
            if own and printed_on and printed_on != own:
                continue
            row = by_key.get(term.ref)
            ev = _slot(row, basis, period) if row is not None else None
            if ev is not None and ev.value is not None:
                return True
    return False


def _statement_name(statement) -> str:
    return str(getattr(statement, "value", statement) or "")


def _parts_note_basis(rows, basis: str, period: str) -> str:
    """The explicit note basis every part's figure in this column was accepted on, joined by "+" —
    "" when any part's was not (`face_bases.accepted_from`). A disclosure parent's figure on a basis
    the face does not print stands only on parts that each stood on their note's own basis."""
    sources = [accepted_from(r, basis, period) for r in rows]
    if not sources or not all(sources):
        return ""
    return "+".join(sorted({part for s in sources for part in s.split("+")}))


def _inputs_note_basis(doc, inputs, basis: str, period: str) -> str:
    """`_parts_note_basis` over the lines an evaluation's terms read in this column."""
    refs = {str(e.get("ref")) for e in (inputs or ()) if e.get("ref")}
    rows = [li for li in doc.line_items if li.canonical_key in refs
            and (ev := _slot(li, basis, period)) is not None and ev.value is not None]
    return _parts_note_basis(rows, basis, period)


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
    bases = printed_bases(doc)
    defs = {i.key: i for i in (all_items or ())}
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
                            + (f", refused (below zero, or inputs that contradict each "
                               f"other): {ev.refused_rungs}" if ev.refused_rungs else ""))
                continue
            # Asked before the row exists, so a line refused in every column is never created.
            if refuse_unprinted(doc, row, item.key, basis, period, ev.value,
                                by="note_sourced", log=ctx.log, bases=bases,
                                note_basis=(_inputs_note_basis(doc, getattr(ev, "inputs", None),
                                                               basis, period)
                                            if is_disclosure(item.key, defs) else "")):
                continue
            if row is None:
                row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
                doc.line_items.append(row)
                by_key[item.key] = row
            _write(row, basis, period, ev.value,
                   unit_ctx=_unit_of_terms(getattr(ev, "inputs", None), by_key, basis, period))
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

    PLUS THE LINES A CASCADE RECEIVES (`Term.received`): a figure that leaves another line and
    arrives on this one is this line's child for which columns its cascade is evaluated in, although
    its configured parent is the line it left.
    """
    out: dict[str, list[str]] = {}
    for item in all_items or ():
        parent = str(getattr(item, "parent", "") or "")
        if parent:
            out.setdefault(parent, []).append(item.key)
    for receiver, ref in _received_terms(all_items) + _deducted_terms(all_items):
        if ref not in out.setdefault(receiver, []):
            out[receiver].append(ref)
    return out


def _received_terms(all_items) -> list[tuple[str, str]]:
    """(receiving line, received key) for every `received` term of a cascade in the set."""
    out = []
    for item in all_items or ():
        for rung in getattr(item, "cascade", None) or ():
            for term in getattr(rung, "terms", None) or ():
                if getattr(term, "received", False) and term.ref:
                    out.append((item.key, term.ref))
    return out


def _deducted_terms(all_items) -> list[tuple[str, str]]:
    """(deducting line, part key) for every term of a `deducts_from_printed` rung in the set.

    The part's configured parent is the line it MOVES TO (Trade Payables(CP)); the line it leaves
    (Trade and other payables(CP)) has no child of its own, so without this its cascade would be
    evaluated in no column at all — the same gap `Term.received` closes for the receiving side."""
    out = []
    for item in all_items or ():
        for rung in getattr(item, "cascade", None) or ():
            if not getattr(rung, "deducts_from_printed", False):
                continue
            for term in getattr(rung, "terms", None) or ():
                if term.ref:
                    out.append((item.key, term.ref))
    return out


#: Raised on the slot (and, with the arithmetic, on the row) of a line whose figure is its printed
#: row LESS a part another line takes — see `CascadeRung.deducts_from_printed`. The figure is a
#: derived remainder, not a printing, and this is what tells a reviewer so.
REMAINDER_OF_PRINTED = "remainder_of_printed"
#: Raised on a part's slot where a line could not take that part out of its printed row, so the
#: part's own line does not take it either and the section total cannot move.
NOT_DEDUCTED_FROM_PRINTED = "not_deducted_from_printed"


def _prints_own_face_row(doc: DocumentModel, key: str, basis: str, period: str) -> bool:
    """Whether a row the face PRINTS gives ``key`` a figure in this column — not a figure a stage
    computed (a derivation, a computed carrier), a part read from a note, or the model's answer
    (which `_model_figures` asks about separately)."""
    from app.stages.prune_notes import _is_face_item

    for li in doc.line_items:
        if li.canonical_key != key or li.derivation or li.is_computed:
            continue
        if str(getattr(li.confidence, "method", "") or "").lower().endswith("llm"):
            continue
        ev = _slot(li, basis, period)
        if (ev is not None and ev.value is not None and ev.provenance is not None
                and _is_face_item(li, doc)):
            return True
    return False


def _deduct_from_printed(parent_def, rung, got, carriers: list, by_key: dict, defs: dict,
                         doc: DocumentModel, ctx: PipelineContext, basis: str, period: str,
                         bases, sources, cells) -> bool:
    """Write the line's printed figure LESS the part its rung reads — `CascadeRung.deducts_from_printed`.

    Returns whether the slot was written. Refused, with the reason logged, where the line prints
    nothing in the column, where the part's own line prints a row of its own (the part did not move
    off this row), or where the remainder would fall below zero; a refusal on the last two marks the
    part's slot `NOT_DEDUCTED_FROM_PRINTED`, so the part's own line leaves it too.
    """
    where = f"note_sourced:{parent_def.key}[{basis}:{period}]"
    part_refs = [t.ref for t in rung.terms if t.ref]

    def refuse_part(why: str) -> None:
        for ref in part_refs:
            for li in doc.line_items:
                if li.canonical_key != ref:
                    continue
                slot = _slot(li, basis, period)
                if slot is not None and NOT_DEDUCTED_FROM_PRINTED not in slot.confidence.flags:
                    slot.confidence.flags.append(NOT_DEDUCTED_FROM_PRINTED)
        ctx.log(f"{where}: rung {got.rung_used} NOT applied — {why}; the part stays in the "
                f"printed row and its own line does not take it")

    held = [(li, ev) for li in carriers
            if (ev := _slot(li, basis, period)) is not None and ev.value is not None]
    if not held:
        ctx.log(f"{where}: rung {got.rung_used} takes {got.value} out of the printed row, and the "
                f"line prints none in this column — not applied")
        return False
    owners = sorted({str(getattr(defs.get(ref), "parent", "") or "") for ref in part_refs} - {""})
    for owner in owners:
        if _prints_own_face_row(doc, owner, basis, period):
            ctx.log(f"{where}: rung {got.rung_used} NOT applied — {owner} prints a row of its own "
                    f"in this column, so the part did not move off this line's row")
            return False
        # THE MODEL ANSWERED THE PART'S OWN LINE WITH ANOTHER FIGURE: its own cascade then defers to
        # that answer and does not take the part, so this line must not give it up either.
        answered = _model_figures(doc, owner, basis, period)
        if answered and Decimal(str(got.value)) not in answered:
            ctx.log(f"{where}: rung {got.rung_used} NOT applied — the model answered {owner} with "
                    f"{', '.join(str(a) for a in sorted(answered))}, not the part {got.value}")
            return False
    printed = sum((Decimal(str(ev.value)) for _li, ev in held), Decimal(0))
    remainder = printed - Decimal(str(got.value))
    if getattr(rung, "refuse_negative", True) and remainder < 0:
        refuse_part(f"the printed {printed} less the part {got.value} would be {remainder}")
        return False
    target_row, existing = held[0]
    if refuse_unprinted(doc, target_row, parent_def.key, basis, period, remainder,
                        by="note_sourced", log=ctx.log, bases=bases):
        refuse_part("the column is not one the statements print")
        return False
    # ONE CARRIER HOLDS THE REMAINDER; every other one stops contributing, as an outranking rung's
    # do — `concept_value` sums the carriers, and the remainder is of all of them together.
    for other, ev in held[1:]:
        if ev.value_raw is None:
            ev.value_raw = ev.value
        other.confidence.flags.append(f"superseded_by_cascade:{parent_def.key}:{ev.value}")
        ev.value = None
    caption = str(target_row.source_label or parent_def.label or parent_def.key)
    printed_prov = note_sourced.derivation._json_safe_provenance(
        getattr(existing, "provenance", None))
    # The slot keeps the face row's provenance: the remainder IS that row's figure less a part, so
    # clicking it lands on the row it was computed from.
    _write(target_row, basis, period, remainder, by=REMAINDER_OF_PRINTED,
           provenance=getattr(existing, "provenance", None))
    slot = _slot(target_row, basis, period)
    if slot is not None and REMAINDER_OF_PRINTED not in slot.confidence.flags:
        slot.confidence.flags.append(REMAINDER_OF_PRINTED)
    target_row.confidence.flags.append(
        f"{REMAINDER_OF_PRINTED}:{basis}:{period}:{printed}-{got.value}={remainder}")
    target_row.confidence.flags.append(f"cascade_rung:{got.rung_used}")
    display = _cascade_display(got, parent_def, defs)
    parts = _cascade_inputs(got, display["op"], by_key, defs, sources, cells)
    for inp in parts:
        # THE PART LEAVES THIS LINE: it is entered as the deduction it is here, at its own figure.
        used = _dec_or_none(inp.get("used"))
        if used is not None and inp.get("status") != "not_used":
            inp["used"] = str(-used)
            inp["deducted"] = True
            inp["status"] = "deducted"
            inp["sign"] = -1
            inp["reason"] = "taken_by_its_own_line"
    printed_input = {
        "label": f"Printed “{caption[:120]}”", "value": str(printed), "counted": True,
        "deducted": False, "sign": 1, "role": "required", "used": str(printed), "status": "used",
        "excerpt": "the row as the statement prints it, before the part another line takes",
        **({"provenance": printed_prov} if printed_prov else {})}
    target_row.derivation = note_sourced.derivation.record(
        target_row.derivation, basis=basis, period_label=period,
        derivation=note_sourced.derivation.build(
            method=f"cascade:{got.rung_used}",
            formula=f"{parent_def.key} (printed) - "
                    + " - ".join(i["ref"] for i in got.inputs if i.get("ref")),
            inputs=[printed_input] + parts, result=remainder,
            flags=[f"rung:{got.rung_used}", REMAINDER_OF_PRINTED,
                   f"printed:{printed}"],
            display={**display, "op": "sum"}))
    ctx.log(f"{where}: rung {got.rung_used} — printed {printed} less {got.value} "
            f"({', '.join(part_refs)}) = {remainder}, a remainder of the printed row")
    return True


def _model_figures(doc: DocumentModel, key: str, basis: str, period: str) -> set[Decimal]:
    """The figures the MODEL gave ``key`` in this column — the same test `_fill_by_cascade` uses to
    defer a cascade to the model's answer (`confidence.method` ending in ``llm``)."""
    out: set[Decimal] = set()
    for li in doc.line_items:
        if li.canonical_key != key:
            continue
        if not str(getattr(li.confidence, "method", "") or "").lower().endswith("llm"):
            continue
        ev = _slot(li, basis, period)
        if ev is not None and ev.value is not None:
            out.add(Decimal(str(ev.value)))
    return out


def _part_not_deducted(doc: DocumentModel, ref: str, basis: str, period: str) -> bool:
    """Whether a `deducts_from_printed` rung refused to take ``ref`` out of its printed row in this
    column (`NOT_DEDUCTED_FROM_PRINTED` on the part's slot)."""
    for li in doc.line_items:
        if li.canonical_key != ref:
            continue
        slot = _slot(li, basis, period)
        if slot is not None and NOT_DEDUCTED_FROM_PRINTED in slot.confidence.flags:
            return True
    return False


def _dec_or_none(value) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ── A NOTE'S MONEY IS ON THE FACE ONCE ─────────────────────────────────────────────────────────
#
# A balance-sheet line filled from a note can publish money the face has ALREADY printed in another
# line's row. China SCE 1966 prints "Prepayments, other receivables and other assets 15,062,723"
# and files it under Other Current Assets; its note states other receivables at 5,818,375, which
# Other Receivables (CP) reads — so the same 5,818,375 stood inside the residual as well, total
# current assets rose by exactly that and the section stopped reconciling.
#
# DECIDED BY AMOUNTS, NOT BY NOTE NUMBERS. The first version of this asked "does a face row
# elsewhere cite a note this line counted?", and a note number is the wrong evidence: one note
# routinely holds money that belongs to two lines. Lai Sun's note 26 prints its FVTPL holdings,
# "Less: Portion classified as current (174,822)" and "Non-current portion 788,507"; the face cites
# it for the CURRENT row, and Securities (LTP) — which read only the non-current portion — was
# deleted in both years for sharing the citation (366,944 / 847,794, published blank). The
# question is now whether a face row PRINTS the money: its figure equals an amount the line
# counted — the line's figure, one of its parts', or a note row it read — or equals a total the
# note prints over a row the line read. Checked in BOTH periods wherever both are printed, because
# two amounts agreeing in one year is a coincidence a filing produces; agreeing in both is not.
# Where only one year can be compared — the face row or the line prints the other year nowhere —
# the amount alone settles nothing: the face row must also cite a note the line read, or print
# that note's own total over the row (containment). An LTP line read from note 26 used to go to 0
# against a face row citing an unrelated note 9 that printed the same amount in its one year.
#
# THE CALCULATION STILL RUNS, and that is the other half. The money a face row prints is taken out
# of the line's own calculation — the cascade is evaluated again with that part reduced by it — and
# the line publishes what the calculation then comes to, with the deduction in its trail. It used
# to delete the slot and keep the trail, which is the blank parent above figured parts that a
# reviewer cannot explain. Two answers, by what the other line IS:
#   * an `exclusive_residual` in the same section — "whatever is left of the printed money once the
#     named lines have theirs" — gives the amount up instead: the residual is the face row LESS it,
#     which is that line's own definition. Refused where the amount exceeds what the row holds.
#   * any other row keeps its printed money, and the line's calculation runs without it.
#
# ONLY ROWS OF THIS SET. A face row the mapper left unclassified reaches no line and no total, so it
# duplicates nothing; and a line's own family (its parts, its parents) is never its duplicate.
#
# BALANCE SHEET ONLY. A P&L memo line (a depreciation charge inside an expense row) reads a note an
# expense row cites by design, and is not added into the row it is part of.


class _Piece:
    """One amount a balance-sheet line counted, and where it entered the line's arithmetic.

    `level` is "line" (the published figure), "part" (a cascade term's figure) or "row" (a note row
    the line or a part read). `owner` is the cascade term the money reached the line through, or
    None for a line that is not a cascade. `cell` locates a note row in `doc.notes`.
    """

    __slots__ = ("level", "amount", "owner", "label", "cell", "listed_by", "note")

    def __init__(self, level: str, amount: Decimal, owner: str | None, label: str,
                 cell: tuple | None = None, listed_by: str = "", note: str = ""):
        self.level, self.amount, self.owner, self.label, self.cell = (
            level, amount, owner, label, cell)
        # For a note row: the line whose trail lists it, and its note — what finds the same row
        # in the other year's trail when the row itself cannot be found in the note tables.
        self.listed_by, self.note = listed_by, note


def _note_figure(item, basis: str, period: str) -> Decimal | None:
    for ev in (getattr(item, "values", None) or {}).values():
        if _basis(ev) == basis and str(getattr(ev, "period_label", "") or "") == period:
            raw = ev.value if ev.value is not None else ev.value_raw
            return None if raw is None else Decimal(raw)
    return None


def _note_totals(table, item, basis: str, period: str) -> set[Decimal]:
    """The totals a note prints OVER this row, in one column.

    A total or subtotal row whose recorded components include the row, or — where the
    reconstruction recorded none — every total printed below it. A table that prints no total at
    all is totalled by its line rows, which is the figure a face row citing it states.
    """
    from app.core.models.enums import LineRole

    rows = list(getattr(table, "items", None) or ())
    totals = [r for r in rows if getattr(r, "role", None) in (LineRole.TOTAL, LineRole.SUBTOTAL)]
    out: set[Decimal] = set()
    if totals:
        position = {id(r): i for i, r in enumerate(rows)}
        for total in totals:
            members = list(getattr(total, "component_ordinals", None) or ())
            inside = (getattr(item, "ordinal", None) in members if members
                      else position.get(id(total), -1) > position.get(id(item), 0))
            amount = _note_figure(total, basis, period)
            if inside and amount is not None:
                out.add(amount)
        return out
    figures = [_note_figure(r, basis, period) for r in rows
               if getattr(r, "role", None) not in (LineRole.HEADER, LineRole.SPACER)]
    figures = [f for f in figures if f is not None]
    if len(figures) > 1:
        out.add(sum(figures, Decimal(0)))
    return out


def _deduction_input(piece: _Piece, face_row: LineItem, face_ev, relation: str) -> dict:
    """The trail's line for money the line gave up because a face row prints it."""
    caption = str(face_row.source_label or face_row.canonical_key or "")[:120]
    why = ("prints this amount" if relation == "identity"
           else "prints the note total this amount is part of")
    return {
        "label": f"Counted on the face in “{caption}”",
        "canonical_key": face_row.canonical_key,
        "value": str(piece.amount),
        "counted": True,
        "deducted": True,
        "excerpt": f"{piece.label[:80]} {piece.amount}: the face row {why}, so it is counted "
                   f"there and not here",
        "provenance": note_sourced.derivation._json_safe_provenance(
            getattr(face_ev, "provenance", None)),
        # FOR THE TRAIL'S WORDING: the face row by its caption (the label above, translated), how
        # it prints the money, and whether it is the line's WHOLE figure. What the deduction took
        # out of the arithmetic (`used`) is set by the caller, which knows the cut.
        "face_caption": caption,
        "relation": relation,
        "whole_line": piece.level == "line",
        "face_amount": str(piece.amount),
        # AN ADJUSTMENT, as a cascade's deduction term is: it applies to the figure the trail's
        # rule reached, and is never one of the figures a `first`, `max` or `min` chose between —
        # read as one, the working said "first(4,444; 4,444; -4,444) = 0".
        "role": "adjustment",
    }


def _settle_against_the_face(doc: DocumentModel, ctx: PipelineContext, all_items) -> None:
    from app.services.line_items import evaluate as evaluate_line
    from app.stages.prune_notes import _is_face_item

    defs = {i.key: i for i in all_items}
    parent_of = {i.key: str(getattr(i, "parent", "") or "") for i in all_items}
    kids: dict[str, list[str]] = {}
    for key, parent in parent_of.items():
        if parent:
            kids.setdefault(parent, []).append(key)
    scope_of = {}
    ontology = getattr(ctx, "ontology", None)
    for m in (getattr(ontology, "mappings", None) or ()):
        scope_of[m.canonical_key] = str(getattr(m, "value_scope", "") or "")

    def side(key: str) -> str:
        """Assets or claims. A double count happens within one side's totals, so a face row on the
        OTHER side cannot be printing this line's money: 1966's note 25 "Balances with related
        parties" holds both the amounts due FROM and due TO related parties, the face cites it for
        "Due from related parties" under current assets, and reading it as a duplicate withheld the
        liability's 2,588,416 — putting current liabilities out by exactly that."""
        where = key if key.startswith("bs_") else section(key)
        if where.startswith(("bs_ca", "bs_nca")):
            return "assets"
        if where.startswith(("bs_cl", "bs_ncl", "bs_eq")):
            return "claims"
        return where

    def section(key: str) -> str:
        item = defs.get(key)
        scope = list(getattr(item, "section_scope", None) or ()) if item is not None else []
        return scope[0] if len(scope) == 1 else ""

    def family(key: str) -> set[str]:
        out, todo = {key}, [key]
        while todo:
            for k in kids.get(todo.pop(), ()):
                if k not in out:
                    out.add(k)
                    todo.append(k)
        up = parent_of.get(key, "")
        while up and up not in out:
            out.add(up)
            up = parent_of.get(up, "")
        return out

    by_key: dict[str, list[LineItem]] = {}
    for li in doc.line_items:
        if li.canonical_key:
            by_key.setdefault(li.canonical_key, []).append(li)
    first_row = {k: rows[0] for k, rows in by_key.items()}
    face_pages = {p.index for p in doc.face_pages()}

    # WHAT EVERY FIGURE WAS BEFORE ANY OF THIS RAN. A slot settled in the current year must not
    # change what the prior-year confirmation compares against, and a residual carved for one line
    # must still be recognised as the row it was printed as.
    printed_before = {id(ev): ev.value for li in doc.line_items for ev in (li.values or {}).values()}

    def was(row: LineItem, basis: str, period: str) -> Decimal | None:
        ev = _slot(row, basis, period)
        return None if ev is None else printed_before.get(id(ev), ev.value)

    def figure_before(key: str, basis: str, period: str) -> Decimal | None:
        for row in by_key.get(key, ()):
            got = was(row, basis, period)
            if got is not None:
                return got
        return None

    # A FACE ROW is printed and derived from nothing, and carries a key of this set.
    face = [li for li in doc.line_items
            if li.canonical_key in defs and not li.derivation and not li.is_computed
            and _is_face_item(li, doc)
            and any(ev.provenance is not None for ev in li.values.values())]
    cells = note_sourced.note_cells(doc)

    def is_face_part(key: str) -> bool:
        rows = by_key.get(key, ())
        return bool(rows) and all(not r.derivation and not r.is_computed for r in rows)

    def row_pieces(trail: dict | None, owner: str | None, seen: set[str], basis: str,
                   period: str, listed_by: str) -> list[_Piece]:
        """The note rows a trail counted, following the parts it was built from."""
        out: list[_Piece] = []
        for inp in (trail or {}).get("inputs") or ():
            if not inp or inp.get("counted") is False or inp.get("deducted"):
                continue
            ref = str(inp.get("canonical_key") or inp.get("ref") or "")
            if ref:
                if ref in seen:
                    continue
                seen.add(ref)
                for r in by_key.get(ref, ()):
                    out += row_pieces((r.derivation or {}).get(f"{basis}:{period}"), owner, seen,
                                      basis, period, ref)
                continue
            cell = note_sourced.cell_of(inp.get("provenance"))
            if cell is not None and cell[0] in face_pages:
                continue                          # a face row cited as an input is the face's own
            try:
                amount = Decimal(str(inp.get("value")))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if amount > 0:
                out.append(_Piece("row", amount, owner, str(inp.get("label") or ""), cell,
                                  listed_by, str(inp.get("note") or "")))
        return out

    def listed_then(piece: _Piece, basis: str, period: str) -> Decimal | None:
        """The same row's figure in another year, read off the trail that listed it."""
        for r in by_key.get(piece.listed_by, ()):
            for inp in ((r.derivation or {}).get(f"{basis}:{period}") or {}).get("inputs") or ():
                if (str(inp.get("label") or "") == piece.label
                        and str(inp.get("note") or "") == piece.note
                        and not inp.get("canonical_key")):
                    try:
                        return Decimal(str(inp.get("value")))
                    except (InvalidOperation, ValueError, TypeError):
                        return None
        return None

    def consistent(pieces: list[_Piece], total: Decimal) -> list[_Piece]:
        """Row pieces only where they add up to the figure they explain — an answer whose rows
        carry signs the trail does not record is checked as a whole, never row by row."""
        return pieces if pieces and sum((p.amount for p in pieces), Decimal(0)) == total else []

    def cascade_pieces(item, known: dict, sources, basis: str, period: str):
        """(evaluation, pieces) of a cascade line in one column, from the rung that answers."""
        got = evaluate_line(item, known, sources)
        if not got.resolved:
            return got, []
        pieces: list[_Piece] = []
        derived_parts = 0
        for entry in got.inputs:
            ref = entry.get("ref")
            if (not ref or entry.get("role") == "adjustment" or int(entry.get("sign") or 1) < 0
                    or entry.get("duplicate_of_row") or entry.get("part_is_the_totals_own_row")
                    or entry.get("selected") is False):
                continue
            try:
                used = Decimal(str(entry.get("used")))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if used <= 0 or is_face_part(ref):
                continue
            derived_parts += 1
            label = str(getattr(defs.get(ref), "label", "") or ref)
            pieces.append(_Piece("part", used, ref, label))
            trail = next(((r.derivation or {}).get(f"{basis}:{period}") for r in by_key.get(ref, ())
                          if (r.derivation or {}).get(f"{basis}:{period}")), None)
            pieces += consistent(row_pieces(trail, ref, {ref}, basis, period, ref), used)
        if derived_parts and got.value is not None and got.value > 0:
            pieces.insert(0, _Piece("line", got.value, "*", str(getattr(item, "label", "") or item.key)))
        return got, pieces

    def flat_pieces(item, trail: dict, value: Decimal, basis: str, period: str) -> list[_Piece]:
        rows = consistent(row_pieces(trail, None, {item.key}, basis, period, item.key),
                          value)
        if not rows or value <= 0:
            return []
        return [_Piece("line", value, None, str(getattr(item, "label", "") or item.key))] + rows

    def relation(amount: Decimal, piece: _Piece, basis: str, period: str) -> str | None:
        if amount == piece.amount:
            return "identity"
        if piece.level == "row" and piece.cell in cells:
            table, note_row = cells[piece.cell]
            if amount in _note_totals(table, note_row, basis, period):
                return "contains"
        return None

    def other_period(period: str) -> str | None:
        return {"current": "prior", "prior": "current"}.get(period)

    def trail_notes(trail: dict | None, seen: set[str], basis: str, period: str) -> set[str]:
        """The notes whose rows a trail counted, following the parts it was built from."""
        out: set[str] = set()
        for inp in (trail or {}).get("inputs") or ():
            if not inp or inp.get("counted") is False or inp.get("deducted"):
                continue
            ref = str(inp.get("canonical_key") or inp.get("ref") or "")
            if ref:
                if ref not in seen:
                    seen.add(ref)
                    for r in by_key.get(ref, ()):
                        out |= trail_notes((r.derivation or {}).get(f"{basis}:{period}"), seen,
                                           basis, period)
                continue
            if inp.get("note"):
                out.add(str(inp["note"]))
            located = cells.get(note_sourced.cell_of(inp.get("provenance")))
            if located is not None and getattr(located[0], "note_number", None):
                out.add(str(located[0].note_number))
        return out

    def notes_read(piece: _Piece, line_key: str, basis: str, period: str) -> set[str]:
        """The notes the piece's money was read from: its own note for a row, every note the
        line's (or the part's) trail counted otherwise."""
        if piece.level == "row":
            out = {piece.note} if piece.note else set()
            located = cells.get(piece.cell)
            if located is not None and getattr(located[0], "note_number", None):
                out.add(str(located[0].note_number))
            return out
        key = line_key if piece.level == "line" else (piece.owner or "")
        out = set()
        for r in by_key.get(key, ()):
            out |= trail_notes((r.derivation or {}).get(f"{basis}:{period}"), {key}, basis,
                               period)
        return out

    def cites(face_row: LineItem, notes: set[str]) -> bool:
        """Whether the face row prints a reference to one of these notes ("16(b)" names 16)."""
        tokens = list(face_row.cited_notes())
        tokens += [b for b in (base_note_number(t) for t in tokens) if b]
        return any(note_sourced.same_note(t, n) for t in tokens for n in notes)

    def compared(face_row: LineItem, piece: _Piece, rel: str, line_key: str, basis: str,
                 period: str) -> bool | None:
        """The same relation in the other year — or None where the face row or the line does not
        print that year, so only one year can be compared."""
        other = other_period(period)
        if other is None:
            return None
        face_then = was(face_row, basis, other)
        if face_then is None:
            return None
        if piece.level == "line":
            then = figure_before(line_key, basis, other)
        elif piece.level == "part":
            then = figure_before(piece.owner or "", basis, other)
        else:
            located = cells.get(piece.cell)
            if located is None:
                then = listed_then(piece, basis, other)
            else:
                table, note_row = located
                if rel == "contains":
                    if _note_figure(note_row, basis, other) is None:
                        return None
                    return face_then in _note_totals(table, note_row, basis, other)
                then = _note_figure(note_row, basis, other)
        return None if then is None else then == face_then

    def confirmed(face_row: LineItem, piece: _Piece, rel: str, line_key: str, basis: str,
                  period: str) -> bool:
        """BOTH YEARS where both are printed. Where only one year can be compared, the amount alone
        is a coincidence a filing produces: it settles only when the face row cites a note the
        line read, or when the face row prints that note's own total over the row (containment).
        """
        both = compared(face_row, piece, rel, line_key, basis, period)
        if both is not None:
            return both
        if rel == "contains" or cites(face_row, notes_read(piece, line_key, basis, period)):
            return True
        ctx.log(f"note_sourced:face_settlement: {line_key}[{basis}:{period}] "
                f"{piece.label[:60]!r} {piece.amount} equals {face_row.canonical_key} "
                f"({str(face_row.source_label)[:60]!r}) in one year only, and the face row cites "
                f"no note the line read — not settled")
        return False

    def match(piece: _Piece, others: list[LineItem], key: str, home: str, basis: str,
              period: str, absorbed: dict[int, Decimal]):
        """(face row, its slot, relation, is_residual) printing this piece, or None — and the row
        that would have, had it held enough (a refusal to report)."""
        found, refused = [], None
        for face_row in others:
            fev = _slot(face_row, basis, period)
            printed = was(face_row, basis, period)
            if fev is None or fev.value is None or not printed:
                continue
            rel = relation(printed, piece, basis, period)
            if rel is None or not confirmed(face_row, piece, rel, key, basis, period):
                continue
            if piece.amount > fev.value - absorbed.get(id(fev), Decimal(0)):
                refused = face_row
                continue
            found.append((face_row, fev, rel))
        if not found:
            return None, refused
        here = [f for f in found if section(f[0].canonical_key) == home]
        residual = next((f for f in here
                         if scope_of.get(f[0].canonical_key) == "exclusive_residual"), None)
        chosen = residual or (here[0] if here else found[0])
        return (*chosen, chosen is residual), None

    def piece_id(piece: _Piece) -> tuple:
        return (piece.level, piece.owner, piece.cell or piece.label)

    for item in all_items:
        key = item.key
        home = section(key)
        if not getattr(item, "in_output", True) or not home.startswith("bs_"):
            continue
        rows = [li for li in by_key.get(key, ()) if li.derivation]
        if not rows:
            continue
        mine = family(key)
        others = [r for r in face if r.canonical_key not in mine
                  and side(r.canonical_key) == side(key)]
        if not others:
            continue
        is_cascade = bool(getattr(item, "cascade", None) or getattr(item, "terms", None))
        for row in rows:
            for ev in list(row.values.values()):
                if ev.value is None:
                    continue
                basis, period = _basis(ev), str(getattr(ev, "period_label", "") or "")
                slot = f"{basis}:{period}"
                trail = (row.derivation or {}).get(slot)
                if not trail:
                    continue
                cascade = is_cascade and str(trail.get("method") or "").startswith("cascade:")
                column, sources_by_slot = _column_figures(doc)
                known = dict(column.get((basis, period), {}))
                sources = sources_by_slot.get((basis, period))
                if cascade:
                    got, pieces = cascade_pieces(item, known, sources, basis, period)
                    if not got.resolved or got.value != ev.value:
                        continue                  # the figure is not this calculation's
                else:
                    pieces = flat_pieces(item, trail, ev.value, basis, period)
                if not pieces:
                    continue

                taken: list[tuple] = []           # (piece, face row, its slot, relation, residual)
                done: set[tuple] = set()
                absorbed: dict[int, Decimal] = {}
                reduced: dict[str, Decimal] = {}  # cascade part -> money taken out of it
                whole = False
                for round_ in range(3):
                    gave = False
                    covered: set[str] = set()     # parts already taken whole this pass
                    for piece in pieces:
                        if whole:
                            break
                        if round_ and piece.level == "line":
                            continue              # only the published figure is the line's own
                        if piece_id(piece) in done or (piece.level == "row"
                                                       and piece.owner in covered):
                            continue
                        hit, refused = match(piece, others, key, home, basis, period, absorbed)
                        if hit is None:
                            if refused is not None:
                                row.confidence.flags.append(
                                    f"face_settlement_refused:{refused.canonical_key}")
                            continue
                        face_row, fev, rel, is_residual = hit
                        done.add(piece_id(piece))
                        absorbed[id(fev)] = absorbed.get(id(fev), Decimal(0)) + piece.amount
                        taken.append((piece, face_row, fev, rel, is_residual))
                        if piece.level == "part":
                            covered.add(piece.owner)
                        if is_residual:
                            if piece.level == "line":
                                whole = True      # the residual gives the whole figure up
                            continue
                        gave = True
                        if piece.level == "line":
                            whole = True
                            for part in pieces:
                                if part.level == "part" and part.owner:
                                    reduced[part.owner] = known.get(part.owner) or Decimal(0)
                        elif piece.owner and piece.owner != "*":
                            reduced[piece.owner] = (reduced.get(piece.owner, Decimal(0))
                                                    + piece.amount)
                    if not gave or not cascade or whole:
                        break
                    # A PART REDUCED CAN MOVE THE ANSWER TO ANOTHER RUNG, whose own parts are then
                    # asked the same question.
                    got, pieces = cascade_pieces(item, _reduced(known, reduced), sources,
                                                 basis, period)
                    if not got.resolved:
                        break

                for piece, face_row, fev, rel, is_residual in taken:
                    if not is_residual:
                        continue
                    before = fev.value
                    _refigure(fev, before - piece.amount)
                    face_row.confidence.flags.append(f"carved_out:{key}:{piece.amount}")
                    ctx.log(f"note_sourced:face_settlement: {face_row.canonical_key}[{slot}] "
                            f"{before} less {key} {piece.amount} = {fev.value} (the residual "
                            f"prints the money this line read)")
                given = [t for t in taken if not t[4]]
                if not given:
                    continue
                before = ev.value
                deductions = [_deduction_input(p, f, fe, r) for p, f, fe, r, _x in given]
                if cascade:
                    trial = _reduced(known, reduced)
                    got = evaluate_line(item, trial, sources)
                    if not got.resolved:
                        for _p, face_row, _f, _r, _x in given:
                            row.confidence.flags.append(
                                f"face_settlement_refused:{face_row.canonical_key}")
                        ctx.log(f"note_sourced:face_settlement: {key}[{slot}] {before} KEPT — no "
                                f"rung resolves once the money printed elsewhere is taken out")
                        continue
                    display = _cascade_display(got, item, defs)
                    inputs = _cascade_inputs(got, display["op"], first_row, defs, sources,
                                             _column_provenance(doc).get((basis, period)))
                    for inp in inputs:
                        ref = inp.get("canonical_key")
                        if ref in reduced and known.get(ref) is not None:
                            inp["value"] = str(known[ref])   # its own figure; the cut is below
                    _allocate_cuts(inputs, deductions, given, known, reduced, trial)
                    after = got.value
                    trail_out = note_sourced.derivation.build(
                        method=f"cascade:{got.rung_used}",
                        formula=" + ".join(f"{'-' if i['sign'] < 0 else ''}{i['ref']}"
                                           for i in got.inputs),
                        inputs=inputs + deductions, result=after,
                        flags=[f"rung:{got.rung_used}"]
                              + ([f"rungs_refused:{len(got.refused_rungs)}"]
                                 if got.refused_rungs else [])
                              + ([f"terms_missing:{len(got.missing)}"] if got.missing else [])
                              + ["settled_against_face"],
                        display=display)
                else:
                    after = before - sum((p.amount for p, *_rest in given), Decimal(0))
                    if after < 0:
                        for _p, face_row, _f, _r, _x in given:
                            row.confidence.flags.append(
                                f"face_settlement_refused:{face_row.canonical_key}")
                        continue
                    # EACH DEDUCTION TAKES ITS PIECE OUT, in the trail's own terms: a prose trail's
                    # inputs are stated in full, so its piece is multiplied back by the scale the
                    # figure was divided by.
                    scale = Decimal(str(trail.get("scale") or 1))
                    for deduction, (piece, *_rest) in zip(deductions, given):
                        deduction["used"] = _negated(piece.amount * scale)
                        deduction["status"] = "deducted"
                        deduction["reason"] = "counted_on_face"
                    trail_out = dict(trail)
                    trail_out["inputs"] = list(trail.get("inputs") or []) + deductions
                    trail_out["result"] = str(after)
                    trail_out["flags"] = list(trail.get("flags") or []) + ["settled_against_face"]
                _refigure(ev, after)
                row.derivation = note_sourced.derivation.record(
                    row.derivation, basis=basis, period_label=period, derivation=trail_out)
                for piece, face_row, fev, rel, _x in given:
                    row.confidence.flags.append(
                        f"counted_on_the_face:{face_row.canonical_key}:{piece.amount}")
                    ctx.log(f"note_sourced:face_settlement: {key}[{slot}] {piece.label[:60]!r} "
                            f"{piece.amount} is printed on the face in {face_row.canonical_key} "
                            f"({str(face_row.source_label)[:60]!r}, {rel}) — the line's "
                            f"calculation runs without it: {before} -> {after}")


def _reduced(known: dict, reduced: dict[str, Decimal]) -> dict:
    """`known` with the money printed elsewhere taken out of each part it reached the line through."""
    out = dict(known)
    for ref, cut in reduced.items():
        figure = out.get(ref)
        if figure is not None:
            out[ref] = max(Decimal(0), figure - cut)
    return out


def _negated(amount: Decimal) -> str:
    """``-amount`` as a trail stores it — "0" rather than Decimal's "-0"."""
    return str(-amount) if amount else "0"


def _allocate_cuts(inputs: list[dict], deductions: list[dict], given: list[tuple], known: dict,
                   reduced: dict[str, Decimal], trial: dict) -> None:
    """A settled cascade's trail, as each part's own figure less the money the face prints.

    The rung was re-run on the REDUCED figures, so its entries say what each part added once the
    face's money was taken out. The trail shows each reduced part at its own figure instead
    (`used`, with the cut in `reduced_by`) and gives the cut to the deduction rows that caused it:
    a part's or a row's piece takes up to its own amount out of its part's cut, and a piece that is
    the line's WHOLE figure takes every cut. The cut is the clamped one `_reduced` applied, so a
    piece can take less than it printed (`partial`), and a deduction whose part the answering rung
    no longer uses takes nothing (`not_needed`). By construction the inputs and the deductions add
    up to the settled figure. Display fields only: `value` and `deducted` are the caller's.

    A CANDIDATE A `max`, `min` OR `first` NO LONGER TAKES was compared at what the face left of
    it, so its cut is recorded too (`reduced_by`, its `used` still nothing) — the working shows it
    at that figure. Shown at its own, MAX_VALID read "max(900; 1,650; 1,200) = 1,200" for a
    reading the face had taken all of.
    """
    cuts: dict[str, tuple[int, Decimal]] = {}
    for inp in inputs:
        ref = inp.get("canonical_key")
        if not ref or ref in cuts or ref not in reduced or known.get(ref) is None:
            continue
        own = abs(known[ref]) if inp.get("abs") else known[ref]
        left = abs(trial[ref]) if inp.get("abs") else trial[ref]
        if inp.get("status") not in note_sourced.derivation.ENTERED:
            if inp.get("status") == "not_used" and own - left:
                inp["reduced_by"] = str(own - left)
            continue
        sign = int(inp.get("sign") or 1)
        cuts[ref] = (sign, own - left)
        inp["used"] = str(sign * own)
        inp["reduced_by"] = str(own - left)
    remaining = {ref: cut for ref, (_sign, cut) in cuts.items()}
    for deduction, (piece, *_rest) in zip(deductions, given):
        if piece.level == "line":
            taken = sum((cuts[ref][0] * left for ref, left in remaining.items()), Decimal(0))
            remaining = {ref: Decimal(0) for ref in remaining}
            partial = False
        else:
            sign = cuts.get(piece.owner, (1, Decimal(0)))[0]
            left = remaining.get(piece.owner, Decimal(0))
            amount = min(piece.amount, left)
            if piece.owner in remaining:
                remaining[piece.owner] = left - amount
            taken = sign * amount
            partial = Decimal(0) < amount < piece.amount
        deduction["used"] = _negated(taken)
        deduction["status"] = "deducted" if taken else "not_used"
        deduction["reason"] = "counted_on_face" if taken else "not_needed"
        deduction["partial"] = partial
