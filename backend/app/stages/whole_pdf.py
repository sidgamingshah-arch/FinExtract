"""THE WHOLE PDF TO THE MODEL, IN PLACE OF THE EXTRACTION — the stage (`services.whole_pdf` holds the
pages, the parts, the question and the checks).

IN THE PIPELINE ONLY WHEN THE SWITCH IS ON (`core.pipeline.pipeline_for`): with
`extraction.whole_pdf_to_model` off, the run assembles `default_pipeline()` and this stage is not
in it, so a run is what it was.

On, it runs straight after the extraction (`core.pipeline.whole_pdf_pipeline` says why there).
It sends the chosen pages as the PDF file itself, in parts within the request's limits, asks every
part for the template's lines a few dozen at a time, checks each figure against the page it cites
in the PDF's own text, and writes one row per line and basis.

EVERY FIGURE IS FOUND AGAIN IN THE RULES' READING OF ITS PAGE AND CHECKED THERE
(`services.whole_pdf_locate`): the face row or note row that prints it, its column, its unit, or the
sentence it is stated in. The answer then meets the checks every model answer is held to
(`services.answer_checks`: a vetoed face caption, a derivative, a policy note, a 其中 row added to
its row, one printed figure twice, a sentence in another currency, a note row in another scale), and
the ones only a column can answer: the year and the basis the figure is filed under, the column a
Level 3 part reads, and one printed balance-sheet row on one line. A refused figure is not written
and its row says why; two parts holding one note row are settled as on the line-item route.

Those rows REPLACE the face rows the rules read; the rules' rows are set aside on `PipelineContext.whole_pdf_rules_rows`, and the note
tables the rules read stay in `doc.notes`. The stages that map captions onto lines, and the ones
that only make sense over the rules' own face rows, are then skipped (`KEPT_STAGES` names what
still runs); units, signs, the note-sourced fill and the checks run on the model's figures as they
do on the rules'. A slot the model left empty takes the rules' published figure: the fill runs
mapping, residual and normalize over a copy of the set-aside rows, inside this stage
(`services.whole_pdf_rules.fill_from_rules`), never as stages of the run.

IT NEVER LEAVES A RUN WITH NOTHING. A provider that cannot be sent a file, a first request that
fails (the run's smallest, which also fills the prompt cache), a run where no request succeeds, or
a statement whose own lines no request answered in a part holding its face pages, leaves the
document as the extraction left it and the rules route carries on from there; the run log says
why, in one line starting `whole_pdf:fallback`. Every request is asked again once when it fails,
and as two halves when its reply is cut off (`_request`).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from decimal import Decimal

import app.adapters  # noqa: F401 - registers configured LLM adapters
from app.core.models import DocumentModel
from app.core.models.confidence import ConfidenceVector
from app.core.models.enums import (
    Basis,
    DocFormat,
    LineRole,
    MappingMethod,
    PageKind,
    PrintedIn,
)
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.ports.llm import ROOM_CEILING, complete_with_room, cut_off, takes_documents
from app.ports.registry import registry
from app.services import derivation, review_state
from app.services import whole_pdf as W
from app.services import whole_pdf_locate as L
from app.services.buckets import PRINTED_ON_FLAG

#: What still runs once the model has read the file. The test for each stage is the same two
#: questions: does it need the note tables (which this route now keeps), and is it safe beside face
#: rows the model wrote — that is, it writes no figure onto a line the model answered, and it does
#: not read the rules' face-row mapping, which this route never makes. Every other stage is
#: skipped, and the run log names each one.
#:
#: SKIPPED, and why — each needs what this route does not have, or would overrule the model:
#:   map_line_items, residual  map the rules' face captions onto lines; the model's rows are on
#:                             their lines already, and the rules' rows are set aside.
#:   line_item_llm             the rules route's own model call; this route has asked its question.
#:   assemble_components       adds up rows the line-item call declared as components; none exist.
#:   gap_closing, row_suggestions  place rows that ended the run unplaced: there are none.
#:   face_mapping_contract     audits the face-caption mapping, which this route does not make.
#:   note_tag_gate             zeroes a line whose face row prints no note reference — and the
#:                             model's rows carry none, so it would zero the model's figures.
KEPT_STAGES = frozenset({
    "ingest", "integrity", "language_detect", "classify",
    # Ran before this stage (`core.pipeline.whole_pdf_pipeline`): it read the note tables this
    # route keeps and the face rows it sets aside. Named so the set says everything that ran.
    "extract",
    "whole_pdf",
    "normalize",            # the statements' units, and the template's sign rules
    # THE NOTE-TO-FACE LINKS, from the note references the face rows carry. Writes links and a
    # row's reconciliation role, never a figure. The model's rows carry no note reference today,
    # so it links nothing yet; it is kept so a model row that is given one (from the rules' row it
    # was found on) is linked as on the rules route — which is what `note_sections` narrows a
    # part's notes by, and what reconcile ties.
    "link_notes",
    # THE CONFIGURED CASCADES, on the model's parts: a derived line (Sales (Revenues), the
    # depreciation lines, the related-party lines…) is not asked — its figure is its cascade's,
    # computed from the internal parts the model read, by the same rungs as on the rules route.
    # WITH THE NOTE TABLES it also reads its declared notes, and fills a part the model left empty
    # from the rules' own reading — a blank Level 3 column read as 0 (`blank_column_is_nil`), the
    # parts of a request that failed — while a part the model answered keeps the model's figure
    # (`_llm_holds`, asked of every row the model wrote for the line), and a note figure the model
    # placed on another line is not read a second time (`services.whole_pdf_rules`). It also
    # attaches the disclosures' quoted note text (`line_disclosure.attach`).
    "note_sourced",
    # THE CLASSIFIED CONTINGENT-LIABILITY NARRATIVE for the Disclosures screen, from the note
    # tables and the page text. It writes `doc.contingent_liabilities` and no line item at all.
    "contingent_liabilities",
    # One printed figure the model put on two lines (two parts reading one note row), settled as
    # on the rules route — the figures now carry the boxes it compares.
    "shared_figures",
    # THE NOTES' OWN ARITHMETIC (a note block that does not add up) and, where a face row is
    # linked to a note, the note-to-face tie. It writes a report and a reconciled figure beside the
    # face figure, never over it; confidence reads the untied ones.
    "reconcile",
    # WHICH NOTES ARE PUBLISHED — the ones the face points to. With no note reference on the
    # model's rows nothing is pruned (its own "no evidence to prune on" rule), so the Notes screen
    # shows every note read; once model rows carry references it prunes as on the rules route.
    "prune_notes",
    "confidence",
    "structural",           # the template's checks, on the model's figures
    "disclosure_reading",   # reads page text; has its own switch
    "segment",
})

#: On every row and figure this route writes.
FLAG = review_state.MODEL_FLAG
#: The flag the review queue selects a doubtful figure on — on the figure's SLOT here, never on the
#: row for it (`services.review_state`).
REVIEW_FLAG = review_state.REVIEW_FLAG

#: How far each check's outcome is trusted, as the figure's mapping confidence. Below
#: `extraction.auto_accept_confidence` (0.80) a figure shows as a weak match.
_SCORE = {"on_page": 0.9, "parts_on_page": 0.85, "moved": 0.8, "nil": 0.75, "no_text": 0.6,
          "not_found": 0.3}
#: The checks that send a line to review: the figure is not in the PDF's text where it says, or the
#: page has no text to look in. A nil ("-") is not checkable either, but a nil is what it says.
#: A line given two different figures for one slot goes to review as well.
_REVIEWED = frozenset({"not_found", "no_text"})


#: The most PDF pages Claude reads in one request on a window under a million tokens (600 on one).
_PAGES_UNDER_A_MILLION = 100


def _model_limits(ctx) -> tuple[int, int]:
    """`(window, reply limit)` — the model's `llm.context_window_tokens` and `llm.max_output_tokens`
    (1,000,000 and 128,000 as shipped), the reply limit never more than half the window."""
    llm = getattr(ctx.settings, "llm", None)
    window = int(getattr(llm, "context_window_tokens", 1_000_000) or 1_000_000)
    room = int(getattr(llm, "max_output_tokens", W.DEFAULT_OUTPUT_TOKENS)
               or W.DEFAULT_OUTPUT_TOKENS)
    return window, min(room, window // 2)

class WholePdfStage:
    name = "whole_pdf"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        ex = ctx.settings.extraction
        if not getattr(ex, "whole_pdf_to_model", False):
            return doc
        provider, refused = self._provider(doc, ctx)
        if refused:
            return _fallback(doc, ctx, refused)

        off = set(getattr(ctx, "sections_off", None) or ())
        window, room = _model_limits(ctx)
        max_pages = int(ex.whole_pdf_pages_per_request)
        if window < 1_000_000 and max_pages > _PAGES_UNDER_A_MILLION:
            # Claude reads at most 100 PDF pages a request on a window under a million tokens.
            ctx.log(f"whole_pdf:pages_per_request {max_pages} -> {_PAGES_UNDER_A_MILLION} "
                    f"(llm.context_window_tokens={window} is under 1,000,000)")
            max_pages = _PAGES_UNDER_A_MILLION
        try:
            # A statement a person switched off on the Page Scope screen is not asked for either.
            plan = W.plan(doc, ctx.raw_bytes, ctx.line_items, choice=str(ex.whole_pdf_pages),
                          max_pages=max_pages,
                          max_request_bytes=int(ex.whole_pdf_max_request_mb) * 1024 * 1024,
                          lines_per_request=int(ex.whole_pdf_lines_per_request),
                          included_pages=getattr(ctx, "included_pages", None), sections_off=off,
                          max_input_tokens=window - room)
        except Exception as exc:  # noqa: BLE001 - a file that will not cut is read by the rules
            return _fallback(doc, ctx, f"the PDF could not be cut into parts: "
                                       f"{type(exc).__name__}: {str(exc)[:120]}")
        pages, parts, dropped, lines, chunks = (plan.pages, plan.parts, plan.dropped, plan.lines,
                                                plan.chunks)
        if dropped:
            ctx.log(f"whole_pdf:pages_not_sent(larger than a request)="
                    f"{','.join(str(p + 1) for p in dropped)}")
        if not parts:
            return _fallback(doc, ctx, "no page fits in one request")
        if not chunks:
            return _fallback(doc, ctx, "the line-item set names no line to ask for")
        first = plan.ask(0, plan.first_chunk())
        ctx.log(f"whole_pdf:plan pages={len(pages)} of {len(doc.pages)} "
                f"({plan.why}) parts={len(parts)} "
                f"pages_per_part={','.join(str(len(p.pages)) for p in parts)} "
                f"faces_in_every_part={len(plan.carried)} "
                f"statements_alone_parts={sum(1 for p in parts if p.faces_only)} "
                f"sizes_mb={','.join(f'{p.b64_bytes / 1048576:.1f}' for p in parts)} "
                f"lines={len(lines)} requests={plan.requests()} "
                f"first={first.statement}({len(first)} lines) provider={provider.id} "
                f"window={window} reply_limit={room}")

        documents = [W.as_document(part, i + 1) for i, part in enumerate(parts)]
        asked = self._ask(ctx, provider, plan, documents)
        if asked is None:
            return _fallback(doc, ctx, "the first request failed (see the line above)")
        replies = asked.replies
        if not replies:
            return _fallback(doc, ctx, "no request succeeded")
        lost_statement, lost = _lost_lines(doc, plan, asked)
        if lost:
            ctx.log(f"whole_pdf:lines_lost={len(lost)} (no request that asked them succeeded) "
                    f"keys={','.join(lost)}")
        if lost_statement:
            return _fallback(doc, ctx, lost_statement)

        try:
            texts = W.read_pages(ctx.raw_bytes, pages)
            readings, units, unknown, unreadable = _readings(doc, parts, chunks, replies, texts)
            # EVERY FIGURE FOUND AGAIN IN THE RULES' READING OF ITS PAGE, AND CHECKED there — the
            # checks every model answer is held to, and the column it is printed in — while the
            # rules' face rows are still the document's (`services.whole_pdf_locate`).
            judge = L.Judge(doc, ctx, ctx.line_items, texts, log=ctx.log)
            readings = judge.judge(readings)
            merged = W.merge(readings, {item.key: item for item in ctx.line_items.items},
                             faces={W._statement_value(p) for p in doc.pages
                                    if p.kind == PageKind.FACE and W._statement_value(p)})
            judge.settle(merged)
            # THE SIGN EACH FIGURE ENDS WITH is the one the rules give the cell it was found on,
            # decided on the rules' own rows while they are still the document's (`_RulesSigns`).
            signs = _rules_signs(doc, ctx, merged, judge)
            rows = _rows(doc, ctx.line_items, merged, provider.id, judge, signs)
            text_rows, refused = _text_rows(doc, ctx.line_items, parts, chunks, replies, texts,
                                            provider.id, ctx.log)
            rows += text_rows
        except Exception as exc:  # noqa: BLE001 - replies that cannot be written are not trusted
            return _fallback(doc, ctx, f"the replies could not be written: "
                                       f"{type(exc).__name__}: {str(exc)[:120]}")
        if not rows:
            return _fallback(doc, ctx, "the model found none of the template's lines")
        # THE MODEL'S ROWS REPLACE THE RULES' FACE ROWS, AND ONLY THOSE. What `extract` read off
        # the face is set aside, as it read it, for a later stage to find a model figure in the
        # rules' reading of its page; the note tables it read stay in `doc.notes`, so the stages
        # kept below read the notes exactly as on the rules route.
        ctx.whole_pdf_rules_rows = list(doc.line_items)
        doc.line_items = rows

        statuses: dict[str, int] = {}
        for r in merged.values():
            statuses[r.check.status] = statuses.get(r.check.status, 0) + 1
        answered = {k for k, _, _ in merged}
        ctx.mapping_strategy = "whole_pdf"
        ctx.mapping_strategy_reason = (
            f"the PDF itself was sent to the model ({provider.id}): {len(parts)} part(s), "
            f"{sum(len(p.pages) for p in parts)} pages; the model's rows replace the rules' face "
            f"rows and caption mapping is skipped; the note tables the rules read are kept")
        disputed = sum(1 for r in merged.values()
                       if any(f.startswith(W.PARTS_DISAGREE) for f in r.flags))
        off_face = sum(1 for r in merged.values() if W.FACE_LINE_OFF_FACE in r.flags)
        ctx.whole_pdf = {
            "pages_sent": [p + 1 for p in pages],
            "pages_not_sent": [p + 1 for p in dropped],
            "pages_in_every_part": [p + 1 for p in plan.carried],
            "pages_per_part": [len(p.pages) for p in parts],
            "parts": len(parts), "requests": plan.requests(),
            "first_request": {"statement": first.statement, "lines": len(first)},
            "requests_answered": len(replies), "requests_sent": asked.calls,
            "retries": asked.retries, "halved": asked.halved,
            "requests_failed": asked.failed, "lines_lost": lost,
            "slots_disputed": disputed, "face_lines_off_a_statement_page": off_face,
            "lines_asked": len(lines),
            "lines_answered": len(answered), "figures": len(merged),
            "checks": statuses, "units": units,
            "texts": len(text_rows), "texts_refused": refused,
            "unknown_keys": sorted(unknown), "unreadable_amounts": unreadable,
            # What finding each figure in the rules' reading came to, and what the checks did.
            "checked": dict(sorted(judge.counts.items())),
            # What the rules read, kept beside the model's reading.
            "rules_face_rows": len(ctx.whole_pdf_rules_rows), "notes_kept": len(doc.notes),
        }
        ctx.only_stages = KEPT_STAGES
        ctx.log(f"whole_pdf:read lines={len(answered) + len(text_rows)} of {len(lines)} "
                f"figures={len(merged)} texts={len(text_rows)} texts_refused={refused} "
                f"checks={','.join(f'{k}:{v}' for k, v in sorted(statuses.items()))} "
                f"units={units or '?'} unknown_keys={len(unknown)} unreadable={unreadable} "
                f"slots_disputed={disputed} face_lines_off_a_statement_page={off_face}")
        ctx.log(f"whole_pdf:rules_rows_set_aside={len(ctx.whole_pdf_rules_rows)} "
                f"notes_kept={len(doc.notes)} — the model's rows replace the rules' face rows; "
                f"caption mapping does not run; units, signs, the note-sourced fill and the "
                f"checks run on these figures")
        # A SLOT THE MODEL LEFT EMPTY TAKES THE RULES' PUBLISHED FIGURE, on a row of its own — never
        # one the model holds or a printed figure it placed (`whole_pdf_rules.fill_from_rules`).
        from app.services.whole_pdf_rules import fill_from_rules
        ctx.whole_pdf["filled_from_rules"] = fill_from_rules(doc, ctx, judge,
                                                             [it.key for it in lines])
        return doc

    @staticmethod
    def _provider(doc, ctx):
        """The provider to send the file to, or why there is none (a fallback reason)."""
        if getattr(ctx, "line_items", None) is None:
            return None, "no line-item set configured"
        raw = ctx.raw_bytes or b""
        if doc.fmt not in (DocFormat.PDF, DocFormat.UNKNOWN) or not raw.startswith(b"%PDF-"):
            return None, "the document is not a PDF"
        provider_id = getattr(ctx.settings.llm, "provider", "stub")
        if provider_id == "stub":
            return None, "the stub provider is configured — there is no model to send it to"
        try:
            provider = registry.get("llm", provider_id)
        except Exception as exc:  # noqa: BLE001 - reported as the fallback's reason
            return None, f"provider {provider_id} unavailable: {type(exc).__name__}"
        if not takes_documents(provider):
            return None, f"provider {provider_id} cannot be sent a file"
        return provider, None

    def _ask(self, ctx, provider, plan, documents) -> _Asked | None:
        """Every (part, lines) request, in the plan's order (`services.whole_pdf.Plan.order`): the
        SMALLEST request of each part on its own — it writes the part to the provider's prompt
        cache for the others, and the run's first one answers quickest whether the gateway is up
        and takes the file — then the rest of the part `llm_parallel_requests` at a time, the
        largest first. None when the run's first request fails, after its retry: the file itself
        was refused, or the provider is not answering, and the rules read the filing.

        EACH REQUEST (`_request`) IS ASKED AGAIN ONCE when it fails for any reason but a cut-off
        reply. A reply CUT OFF at its budget is asked again with twice the room where that is
        within `ports.llm.ROOM_CEILING` (`complete_with_room`) — what the wait can deliver — and
        the model's reply limit; a larger request is asked again as two halves, each half the same
        way, at most `_MAX_HALVINGS` deep. A request's first budget runs to the model's reply limit
        (`llm.max_output_tokens`, 128,000 as shipped): the HK balance sheet's 208 lines are
        budgeted 46,784 tokens where they were capped at 32,000. It was raised
        at once, so a cut-off balance sheet in part 1 sent the whole run to the rules (finding
        cut_off_not_retried_for_statements).
        """
        width = max(1, int(getattr(ctx.settings.extraction, "llm_parallel_requests", 1) or 1))
        parts, chunks = plan.parts, plan.chunks
        asked = _Asked()
        order = plan.order()
        total = len(order)
        done = 0

        _window, room = _model_limits(ctx)

        def call(pi: int, lines: W.Chunk):
            budget = W.max_tokens_for(len(lines), room)
            message = {"role": "user", "documents": [documents[pi]],
                       "content": W.question(lines, parts[pi], pi + 1, len(parts),
                                             ctx.line_items, carried=len(plan.carried))}
            # ASKED AGAIN WITH TWICE THE ROOM ONLY WHERE THE WAIT CAN DELIVER IT: `ROOM_CEILING`,
            # 16,000 — a reply comes at about 27 tokens a second (8,000 took most of five minutes,
            # `llm.document_timeout_seconds`), so a doubled retry past it would time out, not
            # finish, and a timeout is not halved. A larger request cut off goes to halves.
            if 2 * budget <= min(room, ROOM_CEILING):
                return complete_with_room(provider, system=W.SYSTEM, messages=[message],
                                          response_schema=W.WholePdfReply, max_tokens=budget)
            return provider.complete_structured(system=W.SYSTEM, messages=[message],
                                                response_schema=W.WholePdfReply,
                                                max_tokens=budget)

        call.budget = lambda lines: W.max_tokens_for(len(lines), room)

        def request(pi: int, ci: int) -> list[_Piece]:
            return _request(call, pi, plan.ask(pi, ci))

        def settle(pi: int, ci: int, pieces: list[_Piece]) -> bool:
            nonlocal done
            done += 1
            ok = False
            for piece in pieces:
                for line in piece.notes:
                    ctx.log(line)
                asked.calls += piece.calls
                asked.retries += piece.retried
                asked.halved += piece.halved
                ctx.llm_failures += piece.failed_calls
                where = (f"part={pi + 1} lines={len(piece.lines)} ({piece.lines.statement})"
                         + (f" half of {len(plan.ask(pi, ci))}"
                            if len(piece.lines) < len(plan.ask(pi, ci)) else ""))
                if piece.error is not None:
                    exc = piece.error
                    ctx.log(f"whole_pdf:request_failed {where} "
                            f"error={type(exc).__name__}: {str(exc)[:200]}")
                    asked.failed.append({"part": pi + 1, "statement": piece.lines.statement,
                                         "lines": [it.key for it in piece.lines],
                                         "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                    continue
                ok = True
                reply, meta = piece.reply, piece.meta or {}
                _account(ctx, meta)
                held = asked.replies.get((pi, ci))
                if held is None:
                    asked.replies[(pi, ci)] = reply
                else:                              # the halves of one request, as one reply
                    held.lines.extend(reply.lines)
                    held.units = held.units or reply.units
                asked.answered.setdefault(pi, set()).update(it.key for it in piece.lines)
                ctx.log(f"whole_pdf:request {where} answered={len(reply.lines)} "
                        f"tokens_in={meta.get('input_tokens', 0)} "
                        f"cached={meta.get('cached_input_tokens', 0)} "
                        f"out={meta.get('output_tokens', 0)}")
            ctx.emit_step(done, total, "whole-PDF requests")
            return ok

        failed_alone: dict[int, dict[str, object]] = {}

        def settled(pi: int, ci: int, pieces: list[_Piece]) -> bool:
            ok = settle(pi, ci, pieces)
            if parts[pi].faces_only:
                # THE LINES OF EVERY PIECE THAT FAILED for any reason but a cut-off reply — the
                # whole request, or one half of it when the other half answered: a half lost here
                # was lost to the statement, as no other part is asked its face lines.
                lost = failed_alone.setdefault(ci, {})
                for p in pieces:
                    if p.error is not None and not cut_off(p.error):
                        lost.update((it.key, it) for it in p.lines if it.key not in lost)
            return ok

        for pi in range(len(parts)):
            mine = [ci for p, ci in order if p == pi]
            if not mine:
                continue
            first_ok = settled(pi, mine[0], request(pi, mine[0]))
            if pi == 0 and not first_ok:
                return None
            rest = mine[1:]
            if width == 1:
                for ci in rest:
                    settled(pi, ci, request(pi, ci))
                continue
            with ThreadPoolExecutor(max_workers=width) as pool:
                futures = [(ci, pool.submit(request, pi, ci)) for ci in rest]
                for ci, fut in futures:          # settled in plan order, whatever finishes first
                    settled(pi, ci, fut.result())
        # A STATEMENT'S REQUEST TO THE STATEMENTS ALONE THAT FAILED, after its retry, is asked once
        # more, whole, in the part holding the notes that carries most of those faces
        # (`Plan.second_part`): its face lines are asked nowhere else, and a statement answered in
        # no part holding its face sends the whole run to the rules (`_lost_lines`). Not one that
        # was cut off at every halving: the same lines would be cut off again. Only the lines that
        # failed: a half that answered is not asked twice.
        for ci, lost in failed_alone.items():
            alt = plan.second_part()
            if alt is None or not lost:
                continue
            again = W.Chunk(list(lost.values()), chunks[ci].statement)
            ctx.log(f"whole_pdf:asked_again part={alt + 1} lines={len(again)} "
                    f"({again.statement}) — "
                    + ("its" if len(again) == len(chunks[ci])
                       else f"{len(again)} of the {len(chunks[ci])} lines of its")
                    + " request to the statements alone failed")
            total += 1
            settle(alt, ci, _request(call, alt, again))
        return asked


#: How many times a cut-off request is halved, at most: 213 lines -> 107 -> 54 -> 27, the last
#: budgeted 6,964 tokens, within `ROOM_CEILING`'s doubling.
_MAX_HALVINGS = 3


@dataclass
class _Piece:
    """What one request (or one half of it) came to: its lines, and its reply or its error, with
    the log lines to write in plan order."""
    lines: W.Chunk
    reply: W.WholePdfReply | None = None
    meta: dict | None = None
    error: BaseException | None = None
    notes: list[str] = field(default_factory=list)
    calls: int = 0
    failed_calls: int = 0
    retried: int = 0
    halved: int = 0


@dataclass
class _Asked:
    """Every reply, keyed (part, chunk) — the halves of a chunk as one — and what was lost."""
    replies: dict[tuple[int, int], W.WholePdfReply] = field(default_factory=dict)
    failed: list[dict] = field(default_factory=list)
    answered: dict[int, set[str]] = field(default_factory=dict)   # part -> keys a reply covered
    calls: int = 0
    retries: int = 0
    halved: int = 0


def _budget(call, lines: W.Chunk) -> int:
    """The reply budget `call` gives `lines` — the run's reply limit's (`_ask`'s `call.budget`)."""
    budget = getattr(call, "budget", None)
    return budget(lines) if callable(budget) else W.max_tokens_for(len(lines))


def _request(call, pi: int, lines: W.Chunk, depth: int = 0) -> list[_Piece]:
    """One request, asked again once on a failure, or as two halves on a cut-off reply. Runs in a
    worker thread: it writes nothing to the run, it returns what happened."""
    piece = _Piece(lines=lines)
    for attempt in (1, 2):
        piece.calls += 1
        try:
            piece.reply, piece.meta = call(pi, lines)
            return [piece]
        except Exception as exc:  # noqa: BLE001 - reported by `settle`, never raised
            piece.failed_calls += 1
            if cut_off(exc):
                if len(lines) > 1 and depth < _MAX_HALVINGS:
                    first, second = lines.halves()
                    piece.notes.append(
                        f"whole_pdf:cut_off part={pi + 1} lines={len(lines)} ({lines.statement}) "
                        f"budget={_budget(call, lines)} — asked again as two halves of "
                        f"{len(first)} and {len(second)} lines")
                    halves = _request(call, pi, first, depth + 1) + _request(call, pi, second,
                                                                              depth + 1)
                    halves[0].notes[:0] = piece.notes
                    halves[0].calls += piece.calls
                    halves[0].failed_calls += piece.failed_calls
                    halves[0].halved += 1
                    return halves
                piece.error = exc
                return [piece]
            if attempt == 1:
                piece.retried += 1
                piece.notes.append(f"whole_pdf:request_retry part={pi + 1} lines={len(lines)} "
                                   f"({lines.statement}) error={type(exc).__name__}: "
                                   f"{str(exc)[:200]} — asked once more")
                continue
            piece.error = exc
    return [piece]


def _lost_lines(doc, plan, asked: _Asked) -> tuple[str | None, list[str]]:
    """`(why the rules must read the filing, or None; the lines no successful request asked)`.

    A STATEMENT NEVER LOSES ITS LINES SILENTLY. A statement's own lines are lost when no request
    that asked them succeeded in a part holding that statement's face pages — then the filing is
    read by the rules route, the reason in the run log. It was only part 1's first request whose
    failure sent the run back; a later one that failed (part 1's income statement, say) left that
    statement's lines blank with a log line (finding partial_failure_blanks_no_fallback). Every
    other line lost (a note's, a part's) is named in the run log and in `ctx.whole_pdf`; the rules
    fill the note parts the model left empty (`note_sourced`, `services.whole_pdf_rules`).
    """
    answered = asked.answered
    lost = sorted(it.key for it in plan.lines
                  if not any(it.key in keys for keys in answered.values()))
    faces: dict[str, set[int]] = {}
    for p in doc.pages:
        st = W._statement_value(p)
        if p.kind == PageKind.FACE and st in W.FACE_STATEMENTS:
            faces.setdefault(st, set()).add(int(p.index))
    for statement in W.FACE_STATEMENTS:
        holders = [pi for pi, part in enumerate(plan.parts)
                   if faces.get(statement, set()) & set(part.pages)]
        if not holders:
            continue
        own = [it.key for chunk in plan.chunks if chunk.statement == statement
               for it in chunk if not W.is_part(it)]
        gone = [k for k in own if not any(k in answered.get(pi, ()) for pi in holders)]
        if gone:
            failed = sorted({f["part"] for f in asked.failed if f["statement"] == statement})
            return (f"the {statement.replace('_', ' ')}'s lines were lost: {len(gone)} of "
                    f"{len(own)} were asked in no request that succeeded in a part holding its "
                    f"face pages (part {', '.join(str(pi + 1) for pi in holders)}; failed in "
                    f"part {', '.join(map(str, failed)) or '?'})"), lost
    return None, lost


def _fallback(doc, ctx, reason: str):
    ctx.log(f"whole_pdf:fallback({reason}) — the filing is read by the rules route")
    return doc


def _account(ctx, meta) -> None:
    meta = meta or {}
    ctx.llm_calls += 1
    ctx.llm_input_tokens += int(meta.get("input_tokens") or 0)
    ctx.llm_output_tokens += int(meta.get("output_tokens") or 0)
    ctx.llm_cached_tokens += int(meta.get("cached_input_tokens") or 0)
    ctx.llm_cache_write_tokens += int(meta.get("cache_write_tokens") or 0)
    if meta.get("model"):
        ctx.llm_model = str(meta["model"])


def _readings(doc, parts, chunks, replies, texts):
    """Every figure the replies gave, parsed, checked against its page and placed on it.

    `texts` is the PDF's own text of the pages sent (`whole_pdf.read_pages`): its numbers are
    what a figure is checked against, its words are where the figure and its caption are drawn —
    the citation a reviewer clicks through to.
    """
    numbers = {p: t.numbers for p, t in texts.items()}
    readings: list[W.Reading] = []
    unknown: set[str] = set()
    unreadable = 0
    units = ""
    kinds = {p.index: p.kind for p in doc.pages}
    for (pi, ci), reply in sorted(replies.items()):
        units = units or (reply.units or "").strip()
        asked = {item.key: item for item in chunks[ci]}
        part = parts[pi]
        for line in reply.lines:
            if line.key not in asked:
                unknown.add(line.key)
                continue
            if W.is_text(asked[line.key]):
                continue                      # a TEXT line's words, written by `_text_rows`
            for fig in line.figures:
                amount = W.parse_amount(fig.amount)
                rows = [(r.caption, W.parse_amount(r.amount)) for r in fig.parts]
                if amount is None or any(a is None for _, a in rows):
                    unreadable += 1
                    continue
                page = part.original_page(int(fig.page or 0))
                check = W.check_figure(amount, [a for _, a in rows], page, part, numbers)
                caption = (fig.caption or "").strip()
                on_page = texts.get(check.page) if check.page is not None else None
                box, label_box = (W.locate(amount, caption, on_page, fig.period) if not rows
                                  else (None, None))
                row_boxes = [W.locate(a, c, on_page, fig.period) for c, a in rows]
                if rows and not box:
                    # A sum is printed nowhere as one figure: it is cited by its first row.
                    box, label_box = next(((b, lb) for b, lb in row_boxes if b), (None, None))
                readings.append(W.Reading(
                    key=line.key, basis=fig.basis, period=fig.period, amount=amount,
                    caption=caption, period_end=(fig.period_end or "").strip(),
                    part=pi, check=check, on_face=kinds.get(check.page) == PageKind.FACE,
                    rows=[(c, a) for c, a in rows], box=box, label_box=label_box,
                    row_boxes=row_boxes))
    return readings, units, unknown, unreadable


def _text_rows(doc, line_item_set, parts, chunks, replies, texts, provider_id: str,
               log) -> tuple[list[LineItem], int]:
    """A row for every TEXT line the model answered, and how many answers were refused.

    CHECKED AS THE RULES ROUTE CHECKS ONE (`stages.line_item_llm`), by the same functions, the
    part's pages standing in for the request's `text_pages`:

      * a PHRASE is copied off a page, so it is written only where the PDF's own text prints it
        (`text_lines.locate`) — on the page it cites, or on exactly one other page of the part, the
        model having cited the printed folio — and otherwise refused (`llm_text_not_on_page`);
      * a NARRATIVE is the model's own few sentences, so it cannot be looked for; it is refused
        when it cites no page, cites a page the part does not carry, runs too long, or writes a
        number none of its pages prints (`text_lines.check_prose`), and its flag says it was
        written, not copied (`llm_prose:pages …`).

    Written as `line_item_llm._put_text` writes one: a `value_text` fact, no figure, on the
    reporting period and the basis the statements print, its provenance on the page — here with
    the box of the printed row a phrase is on, for its citation.
    """
    from app.core.models.enums import Basis
    from app.services import text_lines
    from app.stages.line_item_llm import NARRATIVE_FLAG
    from app.stages.note_sourced import _prose_basis

    try:
        basis = Basis(_prose_basis(doc) or "consolidated")
    except ValueError:
        basis = Basis.CONSOLIDATED
    by_key = {item.key: item for item in line_item_set.items}
    done: dict[str, LineItem] = {}
    refused = 0
    for (pi, ci), reply in sorted(replies.items()):
        part = parts[pi]
        asked = {item.key for item in chunks[ci]}
        # The part's pages as the rules route's page blocks: 1-based report pages, with their text.
        supplied = [{"page": p + 1, "text": texts[p].text if texts.get(p) else ""}
                    for p in part.pages]
        for line in reply.lines:
            item = by_key.get(line.key)
            words = " ".join((line.text or "").split())
            if (line.key not in asked or item is None or not W.is_text(item) or not words
                    or line.key in done):
                continue
            # The model numbers the part's pages; a page outside the part maps to none.
            cited = [part.original_page(int(n)) for n in (line.text_pages or ())]
            cited_pages = [c + 1 for c in cited if c is not None]
            if text_lines.is_prose_line(item):
                why = (text_lines.check_prose(words, cited_pages, supplied)
                       if all(c is not None for c in cited) else
                       "llm_prose_page_not_supplied — it cites a page this part does not carry")
                if why is not None:
                    refused += 1
                    log(f"whole_pdf:{line.key}: narrative REFUSED ({why}) — {words[:120]!r}")
                    continue
                page = cited_pages[0]
                flag = f"{NARRATIVE_FLAG}:pages {', '.join(map(str, dict.fromkeys(cited_pages)))}"
                box = None
            else:
                first = cited_pages[0] if cited_pages else None
                page = text_lines.locate(words, first, supplied)
                if page is None and first is not None:
                    others = [b["page"] for b in supplied if b["page"] != first
                              and text_lines.locate(words, b["page"], [b]) is not None]
                    page = others[0] if len(others) == 1 else None
                if page is None:
                    refused += 1
                    log(f"whole_pdf:{line.key}: text REFUSED (llm_text_not_on_page) — page "
                        f"{first if first is not None else '?'} does not print {words[:80]!r}")
                    continue
                flag = f"llm_text:page {page}"
                box = W.locate_text(words, texts.get(page - 1))
            row = LineItem(source_label=item.label or item.key, canonical_key=item.key,
                           display_label=item.label or None, role=LineRole.LINE)
            row.set_value(ExtractedValue(
                basis=basis, period_label="current", value_text=words, derived=True,
                provenance=Provenance(page_index=page - 1, bbox=box, value_bbox=box,
                                      text_snippet=words[:500],
                                      source_kind="model_pdf",
                                      producer=f"whole_pdf:{provider_id}")))
            row.confidence.method = MappingMethod.LLM.value
            # A text line is served by its ROW (`api.routes.extractions._text_facts`): this score
            # is the one a reader sees, and a narrative the model wrote is in review by it.
            row.confidence.mapping = 0.6 if flag.startswith(NARRATIVE_FLAG) else 0.9
            row.confidence.flags += [FLAG, flag]
            _printed_on(row, item)
            done[line.key] = row
    return list(done.values()), refused


def _printed_on(row: LineItem, item, *, on_face: bool = False) -> None:
    """Stamp the row with the statement it is on, read before the page by every stage that asks
    (`buckets.statement_resolver`).

    A STATEMENT'S LINE IS ON ITS LINE'S STATEMENT: the model was asked it as that statement's line.
    A page is not a statement — a mainland filing ends one statement and starts the next on the
    same page — and resolved by page, on 11033486 the parent company's liabilities and equity,
    printed above the income statement's title on page 136, were read as income-statement rows,
    failed normalize's balance/flow check and lost their line (15 rows, the balance sheet's checks
    with them). `pdf_extract` stamps the same flag on a page it splits. An internal part read off a
    statement page is that statement's, as a face part mapped from a face row is on the rules route.

    EVERY OTHER ROW IS ON NO STATEMENT (an empty stamp, which the resolver reads as None): a part
    read in the notes, a note's or a covenant's or the setup block's line, a text. On the rules
    route `note_sourced`, the model's stage and the export write those after normalize, so no
    statement check ever sees them; resolved by page or by their line's statement, normalize's
    balance/flow check took them off their lines (a notes narrative cited on a balance-sheet page;
    four note parts on 688411).
    """
    if item is None:
        return
    statement = W.statement_of(item)
    if statement in _NORMALISED_STATEMENTS and (on_face or not W.is_part(item)):
        row.confidence.flags.append(f"{PRINTED_ON_FLAG}:{statement}")
    else:
        row.confidence.flags.append(f"{PRINTED_ON_FLAG}:")


#: The statements whose face rows `normalize` orients and checks, on this route as on the rules
#: route. Every other line — a note, a covenant, the setup block, a part read in the notes — is
#: filled on the rules route AFTER normalize, so normalize never sees it; here it is written as read.
_NORMALISED_STATEMENTS = frozenset({"balance_sheet", "profit_and_loss", "cash_flow"})

#: A CAS CASH-FLOW CAPTION THAT PAYS, for a figure found in no cell the rules read (see `_as_read`):
#: the payment words the prompt lists, 支付 only where it is the verb. 股份支付 is a share-based
#: PAYMENT — a noun, and in the indirect method an add-back printed positive (688008 p227:
#: 58,848,996.62 / 122,677,641.08) — and the bare 支付 negated it (finding signs_share_based_payment).
#: But 股份/股权 followed by 支付 (的)现金 is the verb again: 回购股份支付的现金 (a share buy-back) and
#: 购买少数股东股权支付的现金 (a minority stake bought) are payments.
_CAS_PAYMENT = re.compile(r"(?<!股份)(?<!股权)(?<!股權)支付|(?:股份|股权|股權)支付(?=的?[现現]金)"
                          r"|购建|購建|偿还|償還|分配股利")

#: The flags `normalize` writes when a sign rule moved a figure, copied from the rules' cell.
_RULE_FLAGS = ("sign_rule:", "sign_normalised")


def _sign_of(value) -> int:
    if value is None:
        return 0
    value = Decimal(value)
    return (value > 0) - (value < 0)


class _RulesSigns:
    """THE SIGN THE RULES GIVE A PRINTED CELL, FOR A LINE — one sign decision for both routes.

    Every figure the model gives is found again among the cells the rules read
    (`services.whole_pdf_locate`). The sign it is written with is the sign the rules route would
    publish for that same cell on that same line, and it is taken from the rules' own code, run on
    the rules' own rows: a copy of the rows `extract` read, each row the model's figure was found on
    filed on the model's line, put through `NormalizeStage` — the printed sign, the "Less:"/"Add:"
    captions, the line's flip and contra rules, a 其中 breakdown of a row printed as an unsigned
    expense, the CAS outflow blocks (现金流入小计 … 现金流出小计). Nothing of it is written back: the
    copy is read for each cell's sign and thrown away.

    WHY. The route used a caption regex in place of these, and it disagreed with the rules three
    ways (all measured with correct SCRIPTED answers, not a model's):

      * 688008 p227, the indirect method's 股份支付 (+58,848,996.62 / +122,677,641.08): any caption
        holding 支付 was negated, so Other Non-Cash Adjs(Oper) came out -131,336,664.28 /
        -126,327,845.36 for the rules' -13,638,671.04 / 119,027,436.80, and the indirect rollup
        and section reconciliation failed by 117,697,993.24 / 245,355,282.16.
      * 300319 p104, the parent's prior 其中：利息费用 printed -757,766.20 under 财务费用: the rules
        publish +757,766.20 (`sign_rule:as_printed_under_unsigned_expense` — a printed credit under
        an unsigned expense), but the model's row has no parent row, so normalize charged it.
      * 1966 p184, lease interest read in note 7 (1,490 / 20,162): the rules' note-to-face split
        writes a component in the face's convention (finance costs printed (1,492,343)), -1,490 /
        -20,162; the route wrote the note's +1,490 / +20,162.

    A CELL IN A NOTE, for a statement's own line, is written as the rules' split writes it
    (`map_ontology._split_from_disclosure`): the note's printed figure turned to the face's
    convention when a face row citing the note is accounted for by the note's rows in the other
    orientation (`map_ontology._orientation_accounting_for`, the split's own test), and then put
    through normalize as the split row is. A note no face row is accounted for by decides nothing.

    WHAT THE COPY CANNOT KNOW is the rules' mapping of a row the model did not cite. One case
    matters: a 其中 row's parent is "printed as an unsigned expense" when its caption matches its
    line's flip rule (财务费用 → Net Interest Income(Expense)), so a parent the model did not cite is
    filed on the line whose flip rule its caption matches — the line normalize reads it on.
    """

    def __init__(self, doc, ctx, merged: dict, judge: L.Judge | None):
        # (rules row id, line, column key) / (note table id, statement) -> (sign, rule flags)
        self.cells: dict[tuple, tuple[int, list[str]]] = {}
        self.orientations: dict[tuple[int, str], int] = {}
        self._doc = doc
        self._tol = Decimal(str(getattr(getattr(getattr(ctx, "settings", None), "extraction",
                                                None), "recon_abs_tolerance", 1.0)))
        self._statement_of = None
        located: list[tuple[object, str]] = []       # (rules face row, line)
        notes: list[tuple[object, str, str]] = []    # (note cell, line, statement)
        items = {it.key: it for it in getattr(getattr(ctx, "line_items", None), "items", ()) or ()}
        for r in merged.values():
            verdict = judge.verdicts.get(id(r)) if judge is not None else None
            item = items.get(r.key)
            if verdict is None or verdict.unchecked or item is None:
                continue
            statement = W.statement_of(item)
            if statement not in _NORMALISED_STATEMENTS:
                continue
            for entry in verdict.kept:
                place = verdict.place_of(entry)
                if place is None or place.cell is None:
                    continue
                if place.kind in ("face", "page"):
                    located.append((place.cell.row, r.key))
                elif place.kind == "note" and not W.is_part(item):
                    notes.append((place.cell, r.key, statement))
        if located or notes:
            self._decide(ctx, located, notes)

    # ── the copy, and normalize on it ──

    def orientation(self, cell, statement: str) -> int:
        """1 or -1: how the rules' split turns this note cell's table to the face's convention, for
        a line of `statement`; 0 when no face row citing the note is accounted for by it."""
        table = cell.table
        key = (id(table), statement)
        if key in self.orientations:
            return self.orientations[key]
        from app.services.buckets import statement_resolver
        from app.stages.map_ontology import _cited_notes, _orientation_accounting_for

        number = str(getattr(table, "note_number", "") or "")
        found = 0
        if number:
            if self._statement_of is None:
                self._statement_of = statement_resolver(self._doc)
            kinds = {p.index: p.kind for p in self._doc.pages}
            faces = [li for li in self._doc.line_items
                     if number in _cited_notes([li]) and self._statement_of(li) == statement
                     and any(ev.provenance is not None
                             and kinds.get(ev.provenance.page_index) == PageKind.FACE
                             for ev in li.values.values())]
            # The cell's own table first, then every table of its note — the split's order.
            groups = [[table], [t for t in self._doc.notes
                                if str(getattr(t, "note_number", "") or "") == number]]
            for group in groups if faces else ():
                detail = [it for t in group for it in t.items
                          if it.values and it.role is LineRole.LINE]
                if not detail:
                    continue
                ways = set()
                for face in faces:
                    way, short = _orientation_accounting_for(face, {"note": detail}, self._tol)
                    if way and not short:
                        ways.add(way)
                if len(ways) == 1:
                    found = ways.pop()
                    break
        self.orientations[key] = found
        return found

    def _decide(self, ctx, located, notes) -> None:
        from types import SimpleNamespace
        from uuid import uuid4

        from app.stages.normalize import NormalizeStage

        doc = self._doc
        keys_of: dict = {}
        for row, key in located:
            keys_of.setdefault(row.id, [])
            if key not in keys_of[row.id]:
                keys_of[row.id].append(key)
        parents = {li.parent_id for li in doc.line_items if getattr(li, "parent_id", None)}
        flips = []
        for m in getattr(getattr(ctx, "ontology", None), "mappings", None) or ():
            for raw in getattr(getattr(m, "sign_rule", None), "flip_if_label_matches", None) or ():
                try:
                    flips.append((m.canonical_key, re.compile(raw, re.IGNORECASE)))
                except re.error:
                    continue

        def copy(row, key, *, keep_id: bool):
            c = row.model_copy(deep=True)
            if not keep_id:
                c.id = uuid4()
            c.canonical_key = key
            return c

        rows, mine = [], {}
        # IN THE RULES' ORDER: a CAS outflow block is the rows between two printed subtotals.
        for li in doc.line_items:
            base = copy(li, None, keep_id=True)
            if li.id in parents:
                # A PARENT ROW keeps its id, which its 其中 rows point at, and is filed on the line
                # whose flip rule its caption matches — see the class docstring. Its own figure is
                # not read, and it is kept out of the statement's unsigned-expense vote.
                base.canonical_key = next((k for k, rx in flips
                                           if rx.search(li.source_label or "")), None)
                if base.canonical_key:
                    for ev in base.values.values():
                        ev.sign_normalised = True
            rows.append(base)
            for key in keys_of.get(li.id, ()):
                rows.append(copy(li, key, keep_id=False))
                mine[(li.id, key)] = rows[-1]
        split = {}
        for cell, key, statement in notes:
            way = self.orientation(cell, statement)
            if not way or (id(cell.row), key, cell.key) in split:
                continue
            # THE SPLIT ROW the rules write for this note cell: the note's figure in the face's
            # convention, `value_raw` the note's own, settled where it was turned.
            row = LineItem(source_label=cell.caption or key, canonical_key=key, role=LineRole.LINE)
            ev = cell.ev.model_copy(deep=True)
            printed = ev.value_raw if ev.value_raw is not None else ev.value
            ev.value_raw = printed
            ev.value = None if printed is None else Decimal(printed) * way
            ev.sign_normalised = bool(ev.sign_normalised) or way < 0
            row.values = {cell.key: ev}
            rows.append(row)
            split[(id(cell.row), key, cell.key)] = (row, way)
        before = {id(ev): list(ev.confidence.flags) for li in rows for ev in li.values.values()}
        quiet = SimpleNamespace(
            log=lambda *_a, **_k: None, ontology=getattr(ctx, "ontology", None),
            template_def=getattr(ctx, "template_def", None), template=getattr(ctx, "template", None),
            us_10k_filing=bool(getattr(ctx, "us_10k_filing", False)),
            line_items=None, raw_bytes=b"")     # no units to read, no measures to stamp
        NormalizeStage().run(doc.model_copy(update={"line_items": rows, "notes": []}), quiet)

        def read(ev, extra=()):
            added = [f for f in ev.confidence.flags
                     if f not in before.get(id(ev), ()) and f.startswith(_RULE_FLAGS)]
            value = ev.value if ev.value is not None else ev.value_raw
            return _sign_of(value), [*extra, *added]

        for (rid, key), row in mine.items():
            for ck, ev in row.values.items():
                self.cells[("face", rid, key, ck)] = read(ev)
        for (cid, key, ck), (row, way) in split.items():
            for ev in row.values.values():
                self.cells[("note", cid, key, ck)] = read(
                    ev, [f"{FLAG}:note_to_face_convention:{way}"])

    def sign(self, place, key: str) -> tuple[int, int, list[str]] | None:
        """`(the rules' sign, the printed sign, the rules' flags)` for the cell a figure was found
        at, on line `key`; None for a figure the rules read no cell for."""
        cell = getattr(place, "cell", None)
        if cell is None:
            return None
        if place.kind in ("face", "page"):
            got = self.cells.get(("face", cell.row.id, key, cell.key))
        elif place.kind == "note":
            got = self.cells.get(("note", id(cell.row), key, cell.key))
        else:
            got = None
        if got is None:
            return None
        printed = cell.ev.value_raw if getattr(cell.ev, "value_raw", None) is not None \
            else cell.ev.value
        return got[0], _sign_of(printed), got[1]


def _rules_signs(doc, ctx, merged: dict, judge) -> _RulesSigns | None:
    """`_RulesSigns` for the run, or None — and the run log says why — when it cannot be built: the
    figures are then signed as for a figure found on no cell, never lost."""
    try:
        return _RulesSigns(doc, ctx, merged, judge)
    except Exception as exc:  # noqa: BLE001 - a sign decision that fails falls back, it is logged
        ctx.log(f"whole_pdf:rules_signs_unavailable {type(exc).__name__}: {str(exc)[:160]} — "
                f"every figure is signed as for one found on no cell the rules read")
        return None


def _as_read(item, r: W.Reading, flags: list, verdict: L.Verdict | None = None,
             signs: _RulesSigns | None = None) -> tuple[Decimal, bool]:
    """The figure to write, and whether its sign is SETTLED — `ExtractedValue.sign_normalised`,
    which every sign rule in `normalize` honours and leaves alone.

    A FIGURE FOUND ON A CELL THE RULES READ ENDS WITH THE SIGN THE RULES GIVE THAT CELL ON THIS LINE
    (`_RulesSigns`), and is settled: normalize's decision for it was already taken, on the rules'
    own row. In a sum each row is signed by its own cell — except a row the model gave the opposite
    of the sign it is printed with, where the rules leave the printed sign alone: that is the model
    taking the row out of the sum (`services.whole_pdf_locate.Judge.judge_one`), and it stands.

    A FIGURE FOUND ON NO CELL THE RULES READ keeps what the route did before:

      * A CASH-FLOW FIGURE carries the model's own sign, settled: the prompt asks for a payment as a
        negative even where a CSRC statement prints it unsigned, and normalize's caption flips
        (written for the unsigned figure) turned it back positive — 688411's 购买商品、接受劳务支付的
        现金 came out +4,096,524,862.28 against the rules' -4,096,524,862.28. A row captioned as a
        payment (`_CAS_PAYMENT`) is negative whether or not the model remembered to say so; each
        row of a sum by its own caption, so an "other" line netting a receipt keeps the receipt.
      * Any other statement's figure is normalized as a face row is (not settled).

    A FIGURE NO STATEMENT'S RULES APPLY TO (see `_printed_on`): a part read in the notes, a note's,
    covenant's or setup line, is written as the page prints it, settled; a cascade applies its own
    signs to a part, as it does to the note figure `note_sourced` reads on the rules route.
    """
    amount = Decimal(r.amount)
    if item is None:
        return amount, False
    statement = W.statement_of(item)
    if statement not in _NORMALISED_STATEMENTS or (W.is_part(item) and not r.on_face):
        flags.append(f"{FLAG}:as_read")
        return amount, True
    cash_flow = statement == "cash_flow"
    if r.rows and sum((a for _c, a in r.rows), Decimal(0)) == amount:
        figures = list(r.rows)
    else:
        figures = [(r.caption, amount)]
    entries = (list(verdict.kept) if verdict is not None and not verdict.unchecked
               and signs is not None else [])
    if len(entries) != len(figures):
        entries = []                              # not one place per figure: none is used
    value, located, moved = Decimal(0), 0, []
    for i, (caption, figure) in enumerate(figures):
        got = signs.sign(verdict.place_of(entries[i]), item.key) if entries else None
        if got is not None:
            rules, printed, rule_flags = got
            model = _sign_of(figure)
            if not rules or (r.rows and model and printed == rules and model == -printed):
                if rules:
                    flags.append(f"{FLAG}:row_sign_kept:{caption or '?'} {figure}")
                written = figure
            else:
                written = abs(figure) * rules
            located += 1
            for f in rule_flags:
                if f not in flags:
                    flags.append(f)
        elif cash_flow and figure > 0 and _CAS_PAYMENT.search(caption or ""):
            written = -figure
        else:
            written = figure
        if written != figure:
            moved.append(f"{caption or '?'} {figure}->{written}")
        value += written
    if located:
        flags.append(f"{FLAG}:sign_of_rules_cell" if located == len(figures)
                     else f"{FLAG}:sign_of_rules_cell:{located}_of_{len(figures)}")
        flags.extend(f"{FLAG}:sign_moved:{m}" for m in moved)
        return value, True
    if not cash_flow:
        return amount, False
    flags.append(f"{FLAG}:cas_outflow_negated" if value != amount else f"{FLAG}:sign_as_read")
    return value, True


def _citation(r, page: int, kind: str, producer: str, page_kind, provenance,
              verdict: L.Verdict | None = None) -> dict:
    """The figure's trail, in the shape a model's citation on the rules route records
    (`stages.line_item_llm`), so the Workspace's inspector says where it was read and lists each
    printed row it was read from, each clickable to its page: the row itself, or the rows a sum
    was added from, each with its own figure and box.

    `printed_in` says whether the page is a statement or a note: this route reads no note tables,
    so it has no note number to give, and without it a figure read in a note would be worded as
    read "on the face" (`api.routes.documents._citation_reading`).

    WHAT THE CHECKS DID IS IN THE TRAIL TOO (`services.whole_pdf_locate`): each input's value is
    the figure AS PRINTED, a row put into the statements' scale says so beside it, and a row the
    checks refused is listed, not counted, with the rule that refused it — so the trail adds up to
    the figure written and shows the row that was left out."""
    printed_in = "face" if page_kind == PageKind.FACE else "notes"

    def entry(label: str, value, box, label_box, counted: bool = True) -> dict:
        prov = Provenance(page_index=page, bbox=box, value_bbox=box, label_bbox=label_box,
                          text_snippet=label or None, source_kind=kind, producer=producer)
        return {"label": label or r.caption or r.key, "value": str(value), "note": None,
                "counted": counted, "printed_in": printed_in,
                "provenance": prov.model_dump(mode="json")}

    rows = verdict.printed_rows if verdict is not None and r.rows else r.rows
    boxes = verdict.row_boxes if verdict is not None and r.rows else r.row_boxes
    written = verdict.rows if verdict is not None and r.rows else r.rows
    scales = verdict.scales if verdict is not None else []
    result = verdict.value if verdict is not None else r.amount

    def converted(inp: dict, printed, i: int) -> dict:
        # A ROW PUT INTO THE STATEMENTS' SCALE counts its converted figure, and the trail says what
        # was printed and in which units (`services.derivation`'s "rescaled" reading).
        scale = scales[i] if i < len(scales) else None
        if scale is not None and str(inp["value"]) != str(printed):
            inp["printed"] = str(printed)
            inp["rescaled"] = {"from": scale[0], "to": scale[1]}
        return inp

    if r.rows:
        inputs = [converted(entry(c, written[i][1] if i < len(written) else a,
                                  *(boxes[i] if i < len(boxes) else (None, None))), a, i)
                  for i, (c, a) in enumerate(rows)]
    else:
        inputs = [converted({**entry(r.caption, result, r.box, r.label_box),
                             "provenance": provenance.model_dump(mode="json")}, r.amount, 0)]
    for bad in (verdict.dropped if verdict is not None else ()):
        refused = entry(bad.get("caption") or "", bad.get("amount"), None, None, counted=False)
        refused["refused"] = bad.get("refused")
        refused["why"] = bad.get("why")
        inputs.append(refused)
    counted = [i["label"] for i in inputs if i.get("counted")]
    flags = [f"{FLAG}:{r.check.status}"] + list(verdict.flags if verdict is not None else ())
    return derivation.build(
        method=f"whole_pdf:{r.check.status}", formula=" + ".join(counted), inputs=inputs,
        result=result, flags=flags,
        display={"op": "sum" if r.rows else "first",
                 "combine": "component" if r.rows else "whole"})


def _rows(doc, line_item_set, merged: dict, provider_id: str,
          judge: L.Judge | None = None, signs: _RulesSigns | None = None) -> list[LineItem]:
    """One row per (line, basis), in the set's order, holding its current and prior figures.

    THE FIGURE WRITTEN IS THE CHECKED ONE (`judge`, `services.whole_pdf_locate`): in the
    statements' scale, without the rows the checks refused. A line whose every figure for a basis
    was refused, or withheld because a sibling part holds its note money, still gets its row — with
    no figure, and the flags that say why — so the reason is on the line and not only in the log.
    """
    by_key = {item.key: item for item in line_item_set.items}
    order = {item.key: i for i, item in enumerate(line_item_set.items)}
    kinds = {p.index: p.kind for p in doc.pages}
    source = {p.index: getattr(p.source_kind, "value", p.source_kind) for p in doc.pages}
    groups: dict[tuple[str, str], list[W.Reading]] = {}
    for (key, basis, _period), reading in merged.items():
        groups.setdefault((key, basis), []).append(reading)
    empty = [slot for slot in (judge.row_flags if judge is not None else {})
             if slot not in groups]
    for slot in empty:
        groups[slot] = []

    out: list[LineItem] = []
    for ordinal, ((key, basis), group) in enumerate(
            sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 0), kv[0][1]))):
        item = by_key.get(key)
        group.sort(key=lambda r: r.period != "current")
        caption = next((r.caption for r in group if r.caption), "") or (item.label if item else key)
        face = any(kinds.get(r.check.page) == PageKind.FACE for r in group)
        row = LineItem(source_label=caption, display_label=(item.label if item else None),
                       canonical_key=key, ordinal=ordinal,
                       role=LineRole.TOTAL if item is not None and item.type == "calculated"
                       else LineRole.LINE,
                       printed_in=PrintedIn.FACE if face else PrintedIn.NOTES)
        scores = []
        refs: dict[str, object] = {}
        for r in group:
            verdict = judge.verdicts.get(id(r)) if judge is not None else None
            flags = [FLAG, f"{FLAG}:{r.check.status}"]
            if r.check.note:
                flags.append(f"{FLAG}:{r.check.status}:{r.check.note}")
            for other in r.others:
                flags.append(f"{FLAG}:other_reading:{other}")
            # What the merge found: two parts disputing the slot, or a statement line kept off a
            # page that is not a statement's (`services.whole_pdf.merge`).
            flags.extend(f"{FLAG}:{f}" for f in r.flags)
            # What the checks found (`services.whole_pdf_locate`).
            flags.extend(f for f in (verdict.flags if verdict is not None else ()) if f not in flags)
            shown = verdict.printed_rows if verdict is not None and r.rows else r.rows
            if shown:
                flags.append(f"{FLAG}:sum_of_rows:" + " + ".join(
                    f"{c or '?'} {a}" for c, a in shown))
            score = _SCORE.get(r.check.status, 0.3) - (0.2 if r.others or r.review else 0.0)
            scores.append(score)
            # IN REVIEW SLOT BY SLOT, and only on the slot: the doubt is this one figure's, so a
            # line computed from this one is put in review for that slot alone
            # (`stages.note_sourced._carry_review`), and the row's other year is not touched.
            doubtful = r.check.status in _REVIEWED or bool(r.others) or r.review
            if doubtful:
                flags.append(REVIEW_FLAG)
            page = r.check.page if r.check.page is not None else 0
            kind = "model_pdf" if source.get(page) is None else f"model_pdf:{source[page]}"
            producer = f"whole_pdf:{provider_id}"
            # THE CITATION: the figure's own box on its page (the viewer highlights it and the
            # grid links to it — no box, no link), its caption's box, and the caption as printed.
            provenance = Provenance(page_index=page, bbox=r.box, value_bbox=r.box,
                                    label_bbox=r.label_box, text_snippet=r.caption or None,
                                    source_kind=kind, producer=producer)
            if not r.box:
                flags.append(f"{FLAG}:not_placed_on_page")
            # The figure the checks kept, in the statements' scale (the model's where none ran).
            checked = r if verdict is None or verdict.unchecked else replace(
                r, amount=verdict.value, rows=list(verdict.rows), row_boxes=list(verdict.row_boxes))
            value, settled = _as_read(item, checked, flags,
                                      None if verdict is None or verdict.unchecked else verdict,
                                      signs)
            # A SIGN RULE THE RULES' CELL WAS PUT THROUGH is named on the row too, as normalize
            # names it on the rules route.
            for f in flags:
                if f.startswith(_RULE_FLAGS) and f not in row.confidence.flags:
                    row.confidence.flags.append(f)
            row.set_value(ExtractedValue(
                basis=Basis(r.basis), period_label=r.period, column_period=r.period,
                period_display=r.period_end or None,
                value_raw=Decimal(checked.amount), value=value, sign_normalised=settled,
                provenance=provenance,
                confidence=ConfidenceVector(mapping=max(0.0, score), method="llm",
                                            flags=flags)))
            row.derivation = derivation.record(
                row.derivation, basis=r.basis, period_label=r.period,
                derivation=_citation(r, page, kind, producer, kinds.get(page), provenance,
                                     None if verdict is None or verdict.unchecked else verdict))
            # THE NOTE REFERENCES THE FACE PRINTS beside the row the figure was found on, so the
            # links, the note-to-face ties and the published notes work as on the rules route.
            for ref in (verdict.note_refs if verdict is not None else ()):
                refs.setdefault(str(getattr(ref, "raw", ref)), ref)
            # A ROW A CHECK REFUSED IS NAMED ON THE LINE as well as on its figure.
            for f in (verdict.flags if verdict is not None else ()):
                if f.startswith(L.REFUSED) and f not in row.confidence.flags:
                    row.confidence.flags.append(f)
        row.note_refs = list(refs.values())
        if not row.note_number:
            row.note_number = next((v.note_number for r in group
                                    if (v := judge.verdicts.get(id(r)) if judge else None)
                                    and v.note_number), None)
        row.confidence.method = MappingMethod.LLM.value
        # THE ROW'S SCORE IS ITS BEST FIGURE'S: each slot keeps its own score, and a row score is
        # read only by displays that are row-level. The minimum made the whole row weak, both years,
        # for one doubtful figure; the row is weak now only when every figure is.
        row.confidence.mapping = max(0.0, max(scores)) if scores else 0.0
        row.confidence.flags.append(FLAG)
        for f in (judge.row_flags.get((key, basis), ()) if judge is not None else ()):
            if f not in row.confidence.flags:
                row.confidence.flags.append(f)
        _printed_on(row, item, on_face=face)
        out.append(row)
    return out
