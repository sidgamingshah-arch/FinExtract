"""LINE-ITEM REQUESTS — the stage that asks a model where a named line's figure is printed.

NEW FILE -> backend/app/stages/line_item_llm.py

THE ONE PLACE A MODEL IS ASKED ABOUT A FIGURE. `stages.map_ontology` decides which CONCEPT each
printed caption is, deterministically and with no provider call of any kind; this stage asks a
different question at a different level — for a line item the configuration already names, WHICH
ROW of its selected notes holds the number. `services.line_item_llm` holds the contract, the
payload and the resolution; this file is the wiring: which requests to make, in what order, what to
write, and what to say about it in the run record.

IT REPLACES A ROW-DRIVEN PATH, and the two are not the same shape. What used to run was
`mapping.match_batch` — printed rows chunked by (statement, basis, period), each chunk asking the
model to pick a concept from a candidate list. That needed a section gate to grade the answer, a
cap on how many concepts fit in a call, a thread pool to hide the wall-clock cost of many chunks,
and a deterministic fallback for every row it forwarded. None of that has an equivalent here: the
line is given, so there is no list to force-fit and nothing to grade — an answer is a CITATION, and
a citation either resolves to a printed row or is reported as unresolved.

WHERE IT SITS, AND WHY THERE. Immediately before `stages.note_sourced`, which is the deterministic
reader of the same `note_source` declarations. Every site that stage writes a figure asks
`_llm_holds` first, so a line this stage answered keeps that answer and the declared route fills
what was left empty. After `link_notes` because a citation is resolved against extracted note
tables, and after `normalize` for the reason `note_sourced` records: a note-derived figure must not
go through the unsigned-expense cohort vote.

OFF BY DEFAULT IS NOT A THING HERE — `extraction.llm_mapping` is the switch and it ships True, but
with no provider configured (or with the stub) the stage logs why and returns the document
untouched. A run with no provider is a fully deterministic run, which is a defined outcome.
"""
from __future__ import annotations

from decimal import Decimal

import app.adapters  # noqa: F401 - registers configured LLM adapters
from app.core.models import DocumentModel
from app.core.models.enums import MappingMethod
from app.core.models.line_item import LineItem
from app.core.stage import PipelineContext, Stage
from app.ports.registry import registry
from app.services import (face_context, line_item_llm, line_item_notes, line_item_requests,
                          line_item_routes, note_sourced)
from app.services.mapping import authored_guidance
from app.services.working_view import build_working_view
# ONE WRITER FOR A FIGURE, so the two routes into a line cannot disagree about what writing one
# means. `_write` sets both `value` and `value_raw` (there is no later pass to derive one from the
# other — `normalize` has already run), and `_prose_basis`/`_prose_scale` are the answers to the
# two questions a PROSE figure raises that a table row does not: which basis column a sentence
# belongs in, and by how much a full amount has to be divided to match the statements' units.
# Imported from the deterministic stage rather than copied: a second implementation is how a
# thousandfold scale error gets fixed in one place and not the other.
from app.stages.note_sourced import _prose_basis, _prose_scale, _write


# One reply carries a citation list per line item. Sized from the envelope rather than from
# `llm.max_tokens` (a request ceiling shared with every free-form call in the app, which makes
# compatible gateways reserve millions of tokens for a small structured reply and time out before
# answering): roughly 120 tokens per answer with two citations, plus the envelope itself. A group
# of twelve is the largest measured request (the twelve depreciation parts resolving to one note).
_RESPONSE_RESERVE = 512
_RESPONSE_TOKENS_PER_ANSWER = 200


def _max_tokens(n_answers: int) -> int:
    return _RESPONSE_RESERVE + max(1, n_answers) * _RESPONSE_TOKENS_PER_ANSWER


class LineItemLlmStage(Stage):
    name = "line_item_llm"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        line_item_set = getattr(ctx, "line_items", None)
        if line_item_set is None:
            ctx.log("line_item_llm:skipped(no line item set configured)")
            return doc
        if not getattr(ctx.settings.extraction, "llm_mapping", True):
            ctx.log("line_item_llm:skipped(extraction.llm_mapping is off)")
            return doc

        provider_id = getattr(ctx.settings.llm, "provider", "stub")
        if provider_id == "stub":
            ctx.log("line_item_llm:skipped(stub llm provider configured) — every figure on this "
                    "run comes from the deterministic route")
            return doc
        try:
            provider = registry.get("llm", provider_id)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"line_item_llm:skipped(llm provider unavailable: "
                    f"{type(exc).__name__}: {exc})")
            return doc

        # `doc` FOR THE CITATIONS. The notes the filing itself prints against a face caption go
        # into each line's note set ahead of anything a probe scored — see
        # `line_item_notes.cited_notes`. It is the only reason the whole document is passed here
        # rather than just its notes.
        plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(
            line_item_set, doc.notes, ctx.settings, doc=doc)
        if not plans:
            ctx.log("line_item_llm:no line item is asked about (every line is a derived parent or "
                    "declares no note source)")
            return doc

        # WHICH LINE ITEMS THIS RUN ASKS ABOUT. `llm_focus_keys` used to name concepts whose
        # printed ROWS were forwarded to the model; rows are not forwarded any more, and the
        # setting now says what a focus run was always trying to say — spend the provider budget on
        # the lines under review rather than on the whole filing. Applied to the PLANS so a group
        # containing one focus line is still asked whole: the group exists because its members
        # share notes, and splitting it would pay for that note block twice.
        focus = (set(ctx.settings.extraction.llm_focus_keys or ())
                 if getattr(ctx.settings.extraction, "llm_focus_only", False) else set())
        if getattr(ctx.settings.extraction, "llm_focus_only", False) and not focus:
            ctx.log("line_item_llm:focus_only_requested_but_no_focus_keys_configured")
        skipped_by_focus = 0
        if focus:
            kept = [p for p in plans if any(k in focus for k in p.keys)]
            skipped_by_focus = len(plans) - len(kept)
            # NAMED, not only counted: a focus key that names no asked-about line is a key whose
            # figure this run cannot return, and the log said nothing about that for the whole
            # life of the row-level focus gate.
            unanswerable = sorted(k for k in focus if k not in by_key)
            ctx.log(f"line_item_llm:focus keys={len(focus)} requests={len(kept)}/{len(plans)}"
                    + (f" keys_that_name_no_asked_about_line={unanswerable}"
                       if unanswerable else ""))
            plans = kept

        # NO NOTE TAG BESIDE THE FACE ROW, NO REQUEST. `llm_only_if_note_tagged` says the printed
        # note reference IS this line's evidence threshold: with no tag anywhere on the face
        # pointing at the notes this line reads, there is nothing to locate the figure IN, and the
        # figure is reported as zero instead (`stages.note_tag_gate`, which owns the zero and runs
        # whether or not a request was made).
        #
        # RE-POINTED FROM THE ROW GATE. It used to skip forwarding a ROW whose own caption carried
        # no note reference — the model would have been reading the caption and nothing else. The
        # cost it saves is the same and the judgement is the author's either way; what changes is
        # the unit. A LINE is skipped when no face row cites any note the line selected, which is
        # the same question asked where the line is named. The schema refuses the flag on anything
        # but `extraction_mode: extract`, so this set needs no mode test.
        cited = {n for ref in (r for li in doc.line_items for r in li.note_refs)
                 for n in ref.numbers if n}
        cited |= {li.note_number for li in doc.line_items if li.note_number}
        tag_gated = {k for k, item in by_key.items()
                     if getattr(item, "llm_only_if_note_tagged", False)}
        untagged = {k for k in tag_gated if not (set(notes_of.get(k, ())) & cited)}
        if untagged:
            trimmed = []
            for plan in plans:
                keys = tuple(k for k in plan.keys if k not in untagged)
                if keys:
                    trimmed.append(plan if keys == plan.keys
                                   else line_item_requests.RequestPlan(
                                       name=plan.name, keys=keys, notes=plan.notes))
            ctx.log(f"line_item_llm:note_tag_gate_skipped_lines={len(untagged)} "
                    f"(each declares llm_only_if_note_tagged and no face row cites a note it "
                    f"selected, so no request is spent on it — `stages.note_tag_gate` writes the "
                    f"zero)")
            plans = trimmed

        # The rulebook's judgement wording plus the master prompt, once for the run. The reply
        # contract goes FIRST and is not configurable — see `line_item_llm.REPLY_CONTRACT`.
        working = getattr(ctx, "ontology", None) or build_working_view(line_item_set)
        system = line_item_llm.REPLY_CONTRACT + "\n\n" + authored_guidance(working, ctx.settings)

        shared = sum(1 for p in plans if p.shared)
        mode = str(getattr(ctx.settings.extraction, "llm_request_grouping", "none") or "none")
        ctx.log(f"line_item_llm:planned requests={len(plans)} grouping={mode} shared={shared} "
                f"lines={sum(len(p.keys) for p in plans)} notes_identified={len(identified)}"
                + (f" requests_skipped_by_focus={skipped_by_focus}" if skipped_by_focus else ""))
        ctx.emit_step(0, len(plans), "line-item request")

        by_concept = {li.canonical_key: li for li in doc.line_items if li.canonical_key}
        filled = answered = unresolved_total = 0
        # WHICH PRINTED FACE ROW WENT TO WHICH LINE, and which lines are still standing on one the
        # model did not answer about. Both are collected across the WHOLE run and reconciled after
        # the last request, because the two facts arrive in either order: the request that claims a
        # row can be made long after the request that left another line standing on it.
        # `_reconcile_claimed_printed_rows` says what is then done.
        claimed_face_rows: dict[str, str] = {}
        kept_printed: list[tuple[str, LineItem]] = []
        calls = failures = 0
        for done, plan in enumerate(plans, start=1):
            # THE STATEMENT THIS REQUEST'S LINES MAY BE READ FROM, supplied once beside them. A
            # face line selects no note, so without this its request carries nothing to locate a
            # figure in — see `services.face_context`. Built per plan rather than once per document
            # because a plan names its own statements and another plan's statement is noise.
            #
            # THE ROUTE DECIDES, NOT THE GATE. `plan.sections` says where the lines are GATED; a
            # line declared `note_tables` or `prose` is gated to a statement and forbidden to take
            # a figure off it, so `face_statements` intersects the gate with the routes before the
            # block is built. A plan whose lines are all note-only gets no statement block at all.
            face = face_context.face_rows(
                doc, line_item_requests.face_statements(plan, by_key))
            # AND THE PAGES THAT ARE NEITHER, for a request holding a line that declares
            # `anywhere`. Those pages are reconstructed only because such a line exists
            # (`services.pdf_extract`), so for every other request this is empty and the block is
            # omitted. Built per plan rather than once per run for the same reason the statement
            # block is: a request that may not cite these pages should not be paying to read them.
            other_pages = (face_context.other_page_rows(doc)
                           if any(k in by_key and line_item_routes.reads_every_page(by_key[k])
                                  for k in plan.keys)
                           else [])
            request = line_item_llm.build_request(plan, by_key, notes_of, identified, face,
                                                  other_pages)
            if not request["line_items"]:
                continue
            try:
                reply, meta = line_item_llm.ask(
                    provider, system, request,
                    max_tokens=_max_tokens(len(request["line_items"])))
            except Exception as exc:  # noqa: BLE001
                # THE WHOLE REQUEST IS THE UNIT OF FAILURE, on purpose. A reply that does not
                # validate is not a partial answer, and the lines in a failed request fall to the
                # deterministic route — which is a defined outcome rather than a degraded one,
                # because nothing here has overwritten anything yet.
                failures += 1
                # ON THE CONTEXT as well as locally, so the run record and the progress
                # panel can say "attempted and failed" rather than showing nothing.
                ctx.llm_failures += 1
                ctx.log(f"line_item_llm:request({plan.name}) FAILED "
                        f"{type(exc).__name__}: {str(exc)[:160]}")
                ctx.emit_step(done, len(plans), "line-item request")
                continue
            calls += 1
            ctx.llm_calls += 1
            ctx.llm_input_tokens += int((meta or {}).get("input_tokens") or 0)
            ctx.llm_output_tokens += int((meta or {}).get("output_tokens") or 0)
            if (meta or {}).get("model"):
                ctx.llm_model = meta["model"]

            asked = {e["key"] for e in request["line_items"]}
            seen: set[str] = set()
            for answer in reply.answers:
                key = (answer.key or "").strip()
                if key not in asked:
                    # A key nobody asked about in THIS request. Refused rather than applied: the
                    # notes carried here are the ones this request's lines selected, so an answer
                    # about another line rests on notes that line never asked for.
                    ctx.log(f"line_item_llm:foreign_key_ignored({key or '<empty>'}) "
                            f"request={plan.name}")
                    continue
                if key in seen:
                    ctx.log(f"line_item_llm:duplicate_answer_ignored({key}) request={plan.name}")
                    continue
                seen.add(key)
                item = by_key[key]
                # THE SAME FENCE ON THE WAY BACK. Supplying no statement block does not stop
                # a model naming a statement token anyway, and a caption that matches a printed
                # face row would then resolve — publishing, for a line whose author said the
                # figure is in a note, the statement row the request never showed it. Refused by
                # `resolve_sources` and named as itself, so the flag on the row says the PLACE was
                # wrong rather than the caption.
                may_face = line_item_routes.may_read_face(item)
                may_pages = line_item_routes.reads_every_page(item)
                # AND WHETHER A TABLE ROW MAY ANSWER AT ALL. A `prose` line's figure is stated in a
                # sentence; `stages.note_sourced` has always honoured that (`[] if route_of(item)
                # == "prose"`) and this path did not, so a cited note row filled it.
                may_rows = line_item_routes.may_read_table_rows(item)
                resolved, unresolved, _ = line_item_llm.resolve(
                    answer, doc.notes, face_context.face_index(doc), allow_face=may_face,
                    pages=face_context.other_page_index(doc) if may_pages else None,
                    allow_pages=may_pages, allow_rows=may_rows)
                unresolved_total += len(unresolved)
                for bad in unresolved:
                    ctx.log(f"line_item_llm:{key}: citation NOT resolved "
                            f"note={bad.get('note')!r} caption={str(bad.get('caption'))[:60]!r} "
                            f"({bad.get('why')})")
                # THE FLOOR THE ANSWER IS CHECKED AGAINST, and what it now does with a failure.
                #
                # A line that declares `row_terms` has said what its ROW is called, and a citation
                # whose caption shares no DISCRIMINATING word with those terms is not that row — it
                # is almost always the note or expense TOTAL the line is a component of. Measured:
                # the face row "Other operating expenses" (1,026,959, a real income-statement
                # total) was bound to a depreciation part, whose meaning is the depreciation
                # CHARGED TO those expenses. It matched the CONTAINER's name.
                #
                # MARKED, NOT DROPPED, and that is the change. Dropping a refused term published a
                # PARTIAL SUM as the line's whole figure — 500,000 where the model said
                # 500,000 - 120,000 — and, because `signs` is positional and was not filtered with
                # it, moved the survivor onto another term's sign, publishing a declared deduction
                # as an addition. The figure of a refused row is REAL (it came off an extracted
                # row); only its identity is in doubt. So it is counted, the line goes to review,
                # and the term is named so a reader sees which part of the arithmetic is unverified.
                #
                # A PROSE ENTRY IS EXEMPT from the floor, and it has to be: a sentence belongs to no
                # row, so it carries no caption to test, and an empty caption shares no word with
                # anything. What verifies a prose amount is that it appears in the cited note's own
                # text, which `resolve_sources` checks before returning it at all.
                # THE ROW_TERMS FLOOR AND THE VETO CHECK ARE GONE, and their removal is the other
                # half of the two-route split rather than a relaxation decided here.
                #
                # `row_terms`, `row_terms_none`, `row_caption_any` and `row_caption_none` are
                # DETERMINISTIC-route artefacts and are no longer sent to the model
                # (`services.line_item_llm._entry_for` says why). Grading an answer against a
                # constraint the request never carried is precisely the unfairness the split
                # exists to remove: a filled deterministic tab would otherwise make the model's
                # answers fail for reasons the model was never told, so the same definition would
                # score differently depending on authoring the model cannot see.
                #
                # WHAT THIS GIVES UP, said plainly because it was a real signal: a model citing a
                # row the author had explicitly vetoed ("accumulated depreciation" for a
                # depreciation line) used to be counted, marked `row_terms_refused` and sent to
                # review. It is now counted and NOT marked. The deterministic route still enforces
                # every one of those vetoes on its own reading — `services.note_sourced.select_rows`
                # applies `row_caption_none` and `row_terms_none` — so the exclusions are unenforced
                # only on the route whose request never mentioned them.
                #
                # `extraction.llm_vetoes_bind_model_answers` is consequently unread. It is left in
                # the settings schema rather than deleted: it is the switch this behaviour comes
                # back on if the split is judged too strict, and removing it would make restoring
                # it a schema change instead of a default change.
                component = str(answer.role or "").lower() == "component"
                figures, unverified = line_item_llm.combine_terms(
                    resolved, unresolved, list(answer.signs or ()), component,
                    fallback_period=_current_period(doc) or "current")
                if not figures:
                    # NOTHING WAS LOCATED AND NOTHING WAS EVEN CLAIMED. No figure can be published,
                    # so the answer is recorded on the row instead of vanishing — `_write_unanswered`
                    # says why, so the review queue and the workspace can show what the model said.
                    self._write_unanswered(doc, by_concept, item, answer, unresolved, ctx,
                                           kept_printed)
                    continue
                answered += 1
                filled += self._write(doc, by_concept, item, answer, resolved, figures, ctx,
                                      unverified)
                # THE CITATION IS ALSO A CLAIM. A printed face row states ONE line's figure, so
                # naming it here is also saying it is not any other line's — recorded by row
                # identity rather than by caption, because two statements can print the same words.
                for entry in resolved:
                    if not (entry.get("on_face") and entry.get("row_id")):
                        continue
                    rid = str(entry["row_id"])
                    first = claimed_face_rows.setdefault(rid, item.key)
                    if first != item.key:
                        # TWO LINES GIVEN THE SAME PRINTED ROW. Both publish it — the figures are
                        # already written by the time this is known, and this pass withholds a
                        # figure only from a line the model answered EMPTY, which neither of these
                        # is. Said out loud because it is the model contradicting itself about a
                        # row, and the first claimant is the owner only because it was asked first.
                        ctx.log(f"line_item_llm:{item.key}: cites a printed row already given to "
                                f"{first} ({str(entry.get('caption') or '')[:48]!r}) — both lines "
                                f"publish it")
            missing = sorted(asked - seen)
            if missing:
                # Said out loud: these lines were paid for and came back unanswered. They fall to
                # the deterministic route, which is what would have happened with no provider —
                # but "unanswered" and "not asked" are different facts about a run.
                ctx.log(f"line_item_llm:unanswered({plan.name}) {missing}")
            ctx.emit_step(done, len(plans), "line-item request")

        blanked = self._reconcile_claimed_printed_rows(kept_printed, claimed_face_rows, ctx)
        ctx.log(f"line_item_llm:calls={calls} failed={failures} lines_answered={answered} "
                f"figures_written={filled} citations_unresolved={unresolved_total}"
                + (f" printed_rows_blanked_because_claimed_elsewhere={blanked}" if blanked else ""))
        if calls and answered:
            # A NEW STRATEGY LABEL, because the two existing ones would both be untrue here.
            #
            # `map_ontology` reports `deterministic` unconditionally and correctly — no row is
            # offered to a model. But frontend/src/screens/ExtractionView.tsx raises a banner on
            # exactly that label reading "No language model was configured for this run", and on a
            # run where requests were made and answered that is false. The old `llm_description`
            # would be false in the other direction: it claimed captions were mapped by meaning,
            # which is precisely what stopped happening.
            #
            # SAFE TO ADD WITHOUT A PAIRED FRONTEND CHANGE, which the banner's own note records:
            # it tests for the condition it warns about rather than for the absence of one label,
            # so an unrecognised label is additive and no longer reads as a failure. A run that
            # made requests simply shows no deterministic-mapping warning, which is the truth.
            ctx.mapping_strategy = "llm_line_items"
            ctx.mapping_strategy_reason = (
                f"{answered} line item(s) located by the model in {calls} request(s); every "
                f"figure was read off the cited printed row")
        elif calls:
            # Calls were made and nothing came back usable. Left as `deterministic` — which is what
            # the run then is — with the reason saying the provider was asked.
            ctx.mapping_strategy_reason = (
                f"{calls} request(s) made, no line item located; every figure on this run comes "
                f"from the deterministic route")
        elif failures:
            ctx.mapping_strategy_reason = (
                f"all {failures} line-item request(s) failed; every figure on this run comes from "
                f"the deterministic route")
        return doc

    def _write(self, doc, by_concept: dict, item, answer, resolved: list[dict],
               figures: dict, ctx, unverified: list[dict] | None = None) -> int:
        """Write one line item's located figure, with the trail that says where it came from."""
        row = by_concept.get(item.key)
        if row is None:
            row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
            doc.line_items.append(row)
            by_concept[item.key] = row
        # `confidence.method` IS the non-interference signal, not a flag invented here:
        # `note_sourced._llm_holds` tests it, and the review queue and the export read the same
        # field to say where a figure came from. One spelling for "the model answered this".
        # WHAT THIS SUPERSEDES, NAMED. The stamp below is unconditional, so a row the mapper had
        # settled by exact caption at 1.0 came out carrying the model's own score — 0.9, 0.8 —
        # with nothing anywhere saying it had ever been higher or that a deterministic answer had
        # been replaced. Measured: an exact face match at 1.0 became `method=llm, mapping=0.9`, and
        # the only way to know was to diff two runs.
        #
        # Recorded only when the model's score is LOWER, which is the case that reads as a
        # regression to anyone auditing the row. A model agreeing at equal or better confidence is
        # the stage working, and a flag for it would be noise on every answered line.
        prior_method = str(row.confidence.method or "")
        prior_mapping = float(row.confidence.mapping or 0.0)
        row.confidence.method = MappingMethod.LLM.value
        row.confidence.mapping = float(answer.confidence or 0.0)
        if (prior_method and prior_method != MappingMethod.LLM.value
                and prior_mapping > row.confidence.mapping):
            row.confidence.flags.append(
                f"llm_superseded_{prior_method}:{prior_mapping:.2f}")
        row.confidence.flags.append(f"line_item_llm:{len(resolved)} cited row(s)")
        # A FIGURE WITH AN UNVERIFIED TERM IN IT GOES TO REVIEW, and says which term.
        #
        # `combine_terms` counts a refused caption and a stated-but-not-found amount so the
        # arithmetic is the model's own rather than a silent partial sum. The price is that the
        # published figure contains something nobody could confirm, so it must not read as settled.
        # The flag is what the review queue and the grid pick up; each term is named so a reader
        # sees WHICH part of the sum is in doubt rather than being told the whole line is.
        for term in (unverified or ()):
            what = ("stated but not found in the note's text"
                    if term.get("amount") and not term.get("figures")
                    else "caption refused by this line's row terms")
            row.confidence.flags.append(
                f"llm_unverified_term:{str(term.get('caption') or term.get('note') or '?')[:60]}"
                f" ({what})")
        if unverified:
            row.confidence.flags.append(f"llm_unverified_terms:{len(unverified)}")
        # A FIGURE OFF A PAGE THAT IS NEITHER A STATEMENT NOR A NOTE CARRIES AN UNVERIFIED SCALE,
        # and that is said on the row rather than assumed away. `stages.normalize` reads the units
        # a STATEMENT page declares — "RMB'000", "人民币万元" — and scales the document by them; a
        # page the classifier could not name declares nothing this run resolved, so its figures
        # were scaled by the document's units with nothing confirming those units apply to that
        # page. The figure is real and its magnitude is a step less certain than a statement's, so
        # the line goes to review with the page named. Only the `anywhere` route can reach one
        # (`services.note_sourced.resolve_sources`, `allow_pages`).
        off_statement = sorted({int(e["page"]) for e in resolved
                                if e.get("off_statement") and e.get("page") is not None})
        if off_statement:
            row.confidence.flags.append(
                "llm_off_statement_page_scale_unverified:page "
                + ", ".join(str(n) for n in off_statement))
            row.confidence.flags.append("low_mapping_confidence")
        if answer.reason:
            # The model's own stated justification, surfaced rather than only logged: a reviewer
            # asking why these rows are this line sees the reasoning and not only a score. Each
            # row's page, note and caption are recovered here, so the reason is the one part of the
            # trace only the model can supply.
            row.confidence.flags.append(f"llm_reason:{answer.reason}")
        # A ROW figure is filed under the basis its own column carries; only a PROSE figure needs a
        # basis chosen for it, because a sentence carries no column.
        #
        # THAT WAS THE COMMENT AND NOT THE CODE. Every figure went under `_prose_basis(doc)`, the
        # document's majority basis, so a row cited from the company-only notes chapter (which
        # `notes_extract` tags STANDALONE) was written into the CONSOLIDATED slot. The basis now
        # travels with each resolved row (`note_sourced._figures_by_basis`) and is used when the
        # rows agree on one. Rows that disagree cannot be one figure's basis, so they fall back to
        # the document's and the row is flagged rather than guessed at.
        row_bases = {str(e.get("basis")) for e in resolved
                     if e.get("basis") and not e.get("prose")}
        if len(row_bases) == 1:
            basis = next(iter(row_bases))
        else:
            basis = _prose_basis(doc)
            if len(row_bases) > 1:
                row.confidence.flags.append(
                    "llm_cited_rows_disagree_on_basis:" + ",".join(sorted(row_bases)))
        written = 0
        for period, amount in figures.items():
            if period == "prose":
                written += self._write_prose(row, doc, amount, resolved, ctx, item)
                continue
            _write(row, basis, period, amount, by="line_item_llm",
                   provenance=_cited_provenance(resolved))
            row.derivation = note_sourced.derivation.record(
                row.derivation, basis=basis, period_label=period,
                derivation=note_sourced.trail(
                    rollup="line_item_llm", item_label=item.label or item.key, amount=amount,
                    # `value`, NOT `amount`, AND THE SPELLING IS LOAD-BEARING.
                    # `derivation._signed` reads `item["value"]` to produce each contribution's
                    # figure, so an input filed under `amount` reached the inspector, the export
                    # and the trace with `v1: None` — the formula listed its inputs by name and
                    # every one of their figures was blank. Measured against the deterministic
                    # route, which has always written `value` and whose contributions carry real
                    # numbers; the two routes simply disagreed about the key. Nothing warned,
                    # because a missing key is indistinguishable from an input whose period
                    # printed no figure.
                    inputs=[{"label": f"note {e.get('note')}: {str(e.get('caption'))[:160]}",
                             "value": str(e.get("figures", {}).get(period, "")),
                             "note": e.get("note"),
                             "counted": True,
                             "provenance": e.get("provenance")}
                            for e in resolved]))
            written += 1
            ctx.log(f"line_item_llm:{item.key}[{basis}:{period}] = {amount} "
                    f"from {len(resolved)} cited row(s)")
        return written

    def _write_unanswered(self, doc, by_concept: dict, item, answer, unresolved: list[dict],
                          ctx, kept_printed: list | None = None) -> int:
        """The model answered and NOTHING could be located. Record the answer anyway.

        WHAT THIS REPLACES: `if not resolved: continue`. Nothing whatever reached the row — no
        flag, no derivation, no reason — so the model's answer survived only as a line in
        `ctx.logs`. A reviewer asking "what did it say about this line?" had nowhere to look, and
        the review queue had nothing to raise. That is the worst of the three outcomes to leave
        silent, because it is the one where the figure is missing AND the reasoning is hidden.

        NO FIGURE IS WRITTEN. There is nothing to write: every citation named a row that is not in
        the note, or an amount that is not in its text. The row is created if it does not exist so
        the answer has somewhere to live, and it carries the reason, each refused citation and why,
        and a flag the queue can select on.
        """
        row = by_concept.get(item.key)
        if row is None:
            row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
            doc.line_items.append(row)
            by_concept[item.key] = row
        # NOTHING WAS LOCATED, SO NOTHING IS CLAIMED. This used to stamp the method and the score
        # unconditionally, and on a line the deterministic mapper had already settled that did two
        # things neither of which anyone chose:
        #
        #   * IT DESTROYED THE DETERMINISTIC CONFIDENCE. A face row matched by exact caption at
        #     1.0, on a request the model answered with an empty `sources`, came out reading
        #     `llm / 0.00` — the printed figure still sitting there, its trustworthiness replaced
        #     by a score that describes a citation which did not resolve. A reviewer sorting by
        #     confidence saw the filing's own printed number at the bottom.
        #   * IT STOOD THE DECLARED ROUTE DOWN. `note_sourced._llm_holds` is true when the method
        #     ends in `llm` AND the slot holds a value, which is exactly a face row's situation —
        #     so a model answering "I did not find it" made the deterministic note route defer as
        #     if it had. The one route that might still have filled the line was silenced by the
        #     route that could not.
        #
        # THE ANSWER IS STILL RECORDED — the flags below are the whole point of this method, and a
        # non-answer with its refused citations is worth more to a reviewer than silence. What it
        # no longer does is take ownership of a row it could not fill.
        #
        # A ROW WITH NO DETERMINISTIC ANSWER IS STILL CLAIMED, because there is nothing to protect
        # and "the model was asked and found nothing" is then the only thing known about the line.
        # `_llm_holds` stays false for it regardless: the slot has no value.
        #
        # AND KEEPING IT IS PROVISIONAL, which is the one thing this branch cannot settle on its
        # own. "The model found nothing for this line" and "the model gave this line's printed row
        # to another line" are both empty answers here, and only the first means the printed figure
        # is still this line's. The row is recorded in `kept_printed` and the question is settled
        # after the last request — see `_reconcile_claimed_printed_rows`.
        prior_method = str(row.confidence.method or "")
        if prior_method and not prior_method.lower().endswith("llm"):
            row.confidence.flags.append(
                f"llm_located_nothing_kept_{prior_method}:"
                f"{float(row.confidence.mapping or 0.0):.2f}")
            if kept_printed is not None and any(
                    getattr(ev, "value", None) is not None
                    for ev in (row.values or {}).values()):
                kept_printed.append((item.key, row))
        else:
            row.confidence.method = MappingMethod.LLM.value
            row.confidence.mapping = float(answer.confidence or 0.0)
        row.confidence.flags.append(f"llm_answered_nothing_located:{len(unresolved)} citation(s)")
        if answer.reason:
            row.confidence.flags.append(f"llm_reason:{answer.reason}")
        for bad in unresolved:
            row.confidence.flags.append(
                f"llm_citation_unresolved:note {bad.get('note')!r} "
                f"{str(bad.get('caption') or '')[:48]!r} — {str(bad.get('why') or '')[:120]}")
        ctx.log(f"line_item_llm:{item.key}: answered but nothing located — "
                f"{len(unresolved)} citation(s) recorded on the row, no figure written")
        return 0

    @staticmethod
    def _reconcile_claimed_printed_rows(kept_printed: list[tuple[str, LineItem]],
                                        claimed: dict[str, str], ctx) -> int:
        """A line kept its printed figure — unless the model gave that printed row to another line.

        THE CASE THIS EXISTS FOR. Two lines can plausibly take the same printed caption; the model
        is asked about both; it cites the row for ONE of them and answers the other with an empty
        `sources`. Both halves of that answer are deliberate — the empty one is the model saying
        "not this line" — and until this pass only the first half was acted on. The second line
        went on publishing the very figure that had just been established as somebody else's, so
        the filing's number appeared twice under two names, and the duplicate was the one carrying
        a deterministic 1.0 confidence.

        BLANK IS THE ANSWER, NOT REVIEW. There is no figure to show: the printed row states one
        line's figure and the model said which line that is. Leaving it and flagging it would mean
        an export whose totals count the same printed number twice, which no downstream
        reconciliation can unpick — `periods.summable` deduplicates only when caption, amount AND
        page all match, and these two rows differ in caption and page.

        AND IT IS AN EMPTY LINE, NOT A REMOVED ROW. `canonical_key` and the flags stay, so the
        audit trail still says the line was considered, what it had been showing, and which line
        took it. The figures go, which is what makes `note_sourced._llm_holds` false for the row —
        so a declared note source may still fill the line from its note, which is exactly the
        second chance a line whose face row was reassigned should get.

        THE OTHER EMPTY ANSWER IS UNTOUCHED. A line the model simply could not locate, whose
        printed row nobody else claimed, keeps its figure and its deterministic confidence — that
        is what `_write_unanswered` decided and this pass does not revisit it.
        """
        if not kept_printed or not claimed:
            return 0
        blanked = 0
        for key, row in kept_printed:
            owner = claimed.get(str(getattr(row, "id", "") or ""))
            if not owner or owner == key:
                continue
            printed = [str(ev.value) for ev in (row.values or {}).values()
                       if getattr(ev, "value", None) is not None]
            row.values = {}
            row.confidence.flags.append(f"llm_printed_row_claimed_by:{owner}")
            if printed:
                row.confidence.flags.append(
                    f"llm_printed_row_withheld:{','.join(sorted(set(printed))[:4])}")
            # THE REVIEW FLAG, because an emptied line is a thing a reviewer should be shown. It is
            # the same flag `map_ontology` raises for a doubtful mapping, so the queue selects on
            # one spelling rather than on a second invented here.
            row.confidence.flags.append("low_mapping_confidence")
            blanked += 1
            ctx.log(f"line_item_llm:{key}: printed figure withheld — the model gave that printed "
                    f"row to {owner}, so this line is left empty"
                    + (f" (was {printed[0]})" if printed else ""))
        return blanked

    @staticmethod
    def _write_prose(row, doc, amount, resolved: list[dict], ctx, item) -> int:
        """A figure the filing states only in a SENTENCE, and the three things that makes special.

        CARRIED OVER FROM `map_ontology._apply_prose_value`, which was reachable only from the
        row-driven mapping result and is gone with it. What it did is kept because all of it was
        measured; ONE thing is corrected on the way.

        1. IT IS SCALED. A prose figure is stated in FULL where a table is stated in the
           statement's units — "HK$529,841,000" against a face printed in HK$'000. The row route
           needed no division because its figure came off an already-normalised `ExtractedValue`;
           a sentence gets none, and `_apply_prose_value` did not divide. Published unscaled that
           is a thousandfold error on the face of the income statement, and it is the same
           division `stages.note_sourced` applies to its own prose route — one behaviour rather
           than two.
        2. THE PRINTED FIGURE IS KEPT. Where the row already carries a printed amount, the prose
           one becomes `value` and the printed one stays in `value_raw`, flagged. On the reference
           filing the row prints the TOTAL depreciation and the footnote states the
           operating-expense SHARE of it — two different quantities — and silently losing the one
           a reader can see on the page is not an acceptable way to gain the one they cannot.
        3. ONE SLOT, NOT ALL OF THEM. The sentence says which LINE the figure belongs to, not
           which column, so it goes to the reporting period's slot and is never broadcast: a
           single amount spread over two years asserts a figure for a year the filing never gave
           one.
        """
        scale = _prose_scale(doc)
        value = amount / scale if scale and scale != 1 else amount
        target_period = _current_period(doc) or "current"
        basis = _prose_basis(doc)
        slots = list((row.values or {}).values())
        target = next((ev for ev in slots
                       if str(getattr(ev, "period_label", "") or "") == target_period),
                      slots[0] if slots else None)
        if target is None:
            _write(row, basis, target_period, value)
        else:
            printed = target.value if target.value is not None else target.value_raw
            if printed is not None and printed != value:
                row.confidence.flags.append(f"prose_value_displaced_printed:{printed}")
            if target.value_raw is None:
                target.value_raw = printed if printed is not None else value
            target.value = value
            basis = str(getattr(getattr(target, "basis", ""), "value",
                                getattr(target, "basis", "")) or "") or basis
            target_period = str(getattr(target, "period_label", "") or "") or target_period

        prose = [e for e in resolved if e.get("prose")]
        best = prose[0] if prose else {}
        row.derivation = note_sourced.derivation.record(
            row.derivation, basis=basis, period_label=target_period,
            derivation=note_sourced.derivation.build(
                method="prose_sourced", formula=None,
                inputs=[{"label": e.get("caption") or f"note {e.get('note')}",
                         "note": e.get("note"),
                         "value": str(e.get("figures", {}).get("prose", "")),
                         "provenance": e.get("provenance"),
                         # THE SENTENCE, verbatim. It is the only evidence for this figure, so the
                         # inspector shows a reviewer the words rather than asking them to trust it.
                         "excerpt": e.get("quote") or "",
                         "counted": True, "deducted": False}
                        for e in prose],
                result=value, flags=["prose_sourced", f"note:{best.get('note')}"]))
        row.confidence.flags.append(f"prose_sourced_value:{best.get('note')}")
        row.is_computed = True
        ctx.log(f"line_item_llm:{item.key}[{basis}:{target_period}] = {value} from prose in "
                f"note {best.get('note')}"
                + (f" (scaled by {scale})" if scale and scale != 1 else ""))
        return 1


def _cited_provenance(resolved: list[dict]):
    """A real `Provenance` for the row the model cited, so click-to-source lands on the right page.

    `services.note_sourced.resolve_sources` keeps each citation's provenance as a PLAIN DICT — it
    has to, because the same record goes into `derivation` and from there into a JSON column, and a
    pydantic object reaching that flush ends the run (`services.derivation._json_safe_provenance`
    records what that cost). So the object is rebuilt here, at the one place that needs an object
    rather than a payload.

    FILTERED TO THE MODEL'S OWN FIELDS, because the dicts are not all one shape: a matched row
    carries a dumped `Provenance`, while `_prose_provenance` mints
    `{"page_index": …, "source": "note_prose"}` and `source` is not a field — passing it through
    would raise on a prose citation, the path least able to afford losing its page.

    `page_index` is required and has no default, so a dict without one is skipped and the caller
    keeps whatever the slot already had. None is the safe answer: `stages.note_sourced._write`
    never clears a provenance, for the deduplication reason its own docstring gives.
    """
    from app.core.models.geometry import Provenance

    fields = set(Provenance.model_fields)
    for entry in resolved or ():
        prov = entry.get("provenance")
        if not isinstance(prov, dict) or prov.get("page_index") is None:
            continue
        try:
            return Provenance(**{k: v for k, v in prov.items() if k in fields})
        except Exception:  # noqa: BLE001 - a malformed citation must not end the run
            continue
    return None


def _current_period(doc) -> str | None:
    """The label the STATEMENTS use for the reporting year, so a prose figure lands in a real column."""
    for li in doc.line_items:
        for ev in (li.values or {}).values():
            if getattr(ev, "column_index", None) is None and getattr(ev, "period_label", None):
                return str(ev.period_label)
    return None
