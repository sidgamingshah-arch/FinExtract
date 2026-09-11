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
from app.services import (line_item_llm, line_item_notes, line_item_requests,
                          note_sourced)
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

        plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(
            line_item_set, doc.notes, ctx.settings)
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
        calls = failures = 0
        for done, plan in enumerate(plans, start=1):
            request = line_item_llm.build_request(plan, by_key, notes_of, identified)
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
                resolved, unresolved, figures = line_item_llm.resolve(answer, doc.notes)
                unresolved_total += len(unresolved)
                for bad in unresolved:
                    ctx.log(f"line_item_llm:{key}: citation NOT resolved "
                            f"note={bad.get('note')!r} caption={str(bad.get('caption'))[:60]!r} "
                            f"({bad.get('why')})")
                if not resolved:
                    continue
                # THE FLOOR THE ANSWER IS CHECKED AGAINST. A line that declares `row_terms` has
                # said what its ROW is called, and a citation whose caption shares no subject word
                # with those terms is not that row — it is almost always the note or expense TOTAL
                # the line is a component of. Measured: the face row "Other operating expenses"
                # (1,026,959, a real income-statement total) was bound to a depreciation part,
                # whose meaning is the depreciation CHARGED TO those expenses. It matched the
                # CONTAINER's name.
                #
                # A PROSE ENTRY IS EXEMPT, and it has to be: a sentence belongs to no row, so it
                # carries no caption to test — `resolve_sources` returns it with `prose: True` and
                # whatever caption the model gave, usually empty. Tested anyway, an empty caption
                # shares no word with anything and every prose figure would be refused. What
                # verifies a prose amount is that it appears in the cited note's own text, which
                # `resolve_sources` has already checked before it is returned at all.
                refused = [e for e in resolved
                           if not e.get("prose")
                           and not line_item_notes.caption_agrees_with_row_terms(
                               item, str(e.get("caption") or ""))[0]]
                if refused:
                    for entry in refused:
                        ctx.log(f"line_item_llm:{key}: row_terms_refused "
                                f"{str(entry.get('caption'))[:60]!r} — the caption shares no "
                                f"subject word with this line's own row terms")
                    resolved = [e for e in resolved if e not in refused]
                    if not resolved:
                        continue
                    component = str(answer.role or "").lower() == "component"
                    figures = line_item_llm.figures_of(resolved, list(answer.signs or ()),
                                                       component)
                if not figures:
                    continue
                answered += 1
                filled += self._write(doc, by_concept, item, answer, resolved, figures, ctx)
            missing = sorted(asked - seen)
            if missing:
                # Said out loud: these lines were paid for and came back unanswered. They fall to
                # the deterministic route, which is what would have happened with no provider —
                # but "unanswered" and "not asked" are different facts about a run.
                ctx.log(f"line_item_llm:unanswered({plan.name}) {missing}")
            ctx.emit_step(done, len(plans), "line-item request")

        ctx.log(f"line_item_llm:calls={calls} failed={failures} lines_answered={answered} "
                f"figures_written={filled} citations_unresolved={unresolved_total}")
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
               figures: dict, ctx) -> int:
        """Write one line item's located figure, with the trail that says where it came from."""
        row = by_concept.get(item.key)
        if row is None:
            row = LineItem(source_label=item.label or item.key, canonical_key=item.key)
            doc.line_items.append(row)
            by_concept[item.key] = row
        # `confidence.method` IS the non-interference signal, not a flag invented here:
        # `note_sourced._llm_holds` tests it, and the review queue and the export read the same
        # field to say where a figure came from. One spelling for "the model answered this".
        row.confidence.method = MappingMethod.LLM.value
        row.confidence.mapping = float(answer.confidence or 0.0)
        row.confidence.flags.append(f"line_item_llm:{len(resolved)} cited row(s)")
        if answer.reason:
            # The model's own stated justification, surfaced rather than only logged: a reviewer
            # asking why these rows are this line sees the reasoning and not only a score. Each
            # row's page, note and caption are recovered here, so the reason is the one part of the
            # trace only the model can supply.
            row.confidence.flags.append(f"llm_reason:{answer.reason}")
        # A ROW figure is filed under the basis its own column carries; only a PROSE figure needs a
        # basis chosen for it, because a sentence carries no column.
        basis = _prose_basis(doc)
        written = 0
        for period, amount in figures.items():
            if period == "prose":
                written += self._write_prose(row, doc, amount, resolved, ctx, item)
                continue
            _write(row, basis, period, amount)
            row.derivation = note_sourced.derivation.record(
                row.derivation, basis=basis, period_label=period,
                derivation=note_sourced.trail(
                    rollup="line_item_llm", item_label=item.label or item.key, amount=amount,
                    inputs=[{"label": f"note {e.get('note')}: {str(e.get('caption'))[:160]}",
                             "amount": str(e.get("figures", {}).get(period, "")),
                             "provenance": e.get("provenance")}
                            for e in resolved]))
            written += 1
            ctx.log(f"line_item_llm:{item.key}[{basis}:{period}] = {amount} "
                    f"from {len(resolved)} cited row(s)")
        return written

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


def _current_period(doc) -> str | None:
    """The label the STATEMENTS use for the reporting year, so a prose figure lands in a real column."""
    for li in doc.line_items:
        for ev in (li.values or {}).values():
            if getattr(ev, "column_index", None) is None and getattr(ev, "period_label", None):
                return str(ev.period_label)
    return None
