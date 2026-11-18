"""THE WHOLE PDF TO THE MODEL, IN PLACE OF THE EXTRACTION — the stage (`services.whole_pdf` holds the
pages, the parts, the question and the checks).

IN THE PIPELINE ONLY WHEN THE SWITCH IS ON (`core.pipeline.pipeline_for`): with
`extraction.whole_pdf_to_model` off, the run assembles `default_pipeline()` and this stage is not
in it, so a run is what it was.

On, it runs straight after the page classifier. It sends the chosen pages as the PDF file itself,
in parts within the request's limits, asks every part for the template's lines a few dozen at a
time, checks each figure against the page it cites in the PDF's own text, and writes one row per
line and basis. The stages that rebuild rows from the page, read note tables and map captions are
then skipped (`KEPT_STAGES` names what still runs); units, signs and the checks run on the model's
figures as they do on the rules'.

IT NEVER LEAVES A RUN WITH NOTHING. A provider that cannot be sent a file, a first request that
fails (the one that also fills the prompt cache), or a run where no request succeeds, leaves the
document untouched and the rules route reads the filing; the run log says why, in one line
starting `whole_pdf:fallback`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
from app.ports.llm import complete_with_room, takes_documents
from app.ports.registry import registry
from app.services import derivation
from app.services import whole_pdf as W
from app.services.buckets import PRINTED_ON_FLAG

#: What still runs once the model has read the file. Every other stage works on rows the rules
#: rebuilt from the page or on note tables, and there are none: they are skipped, and the run log
#: names each one.
KEPT_STAGES = frozenset({
    "ingest", "integrity", "language_detect", "classify", "whole_pdf",
    "normalize",            # the statements' units, and the template's sign rules
    "confidence",
    "structural",           # the template's checks, on the model's figures
    "disclosure_reading",   # reads page text; has its own switch
    "segment",
})

#: On every row and figure this route writes.
FLAG = "whole_pdf"
#: The flag the review queue selects a doubtful line on (as `stages.line_item_llm._review`).
REVIEW_FLAG = "low_mapping_confidence"

#: How far each check's outcome is trusted, as the figure's mapping confidence. Below
#: `extraction.auto_accept_confidence` (0.80) a figure shows as a weak match.
_SCORE = {"on_page": 0.9, "parts_on_page": 0.85, "moved": 0.8, "nil": 0.75, "no_text": 0.6,
          "not_found": 0.3}
#: The checks that send a line to review: the figure is not in the PDF's text where it says, or the
#: page has no text to look in. A nil ("-") is not checkable either, but a nil is what it says.
#: A line given two different figures for one slot goes to review as well.
_REVIEWED = frozenset({"not_found", "no_text"})


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
        try:
            pages, why = W.pages_to_send(doc, choice=str(ex.whole_pdf_pages),
                                         included_pages=getattr(ctx, "included_pages", None),
                                         sections_off=off)
            parts, dropped = W.build_parts(
                ctx.raw_bytes, pages, max_pages=int(ex.whole_pdf_pages_per_request),
                max_request_bytes=int(ex.whole_pdf_max_request_mb) * 1024 * 1024)
        except Exception as exc:  # noqa: BLE001 - a file that will not cut is read by the rules
            return _fallback(doc, ctx, f"the PDF could not be cut into parts: "
                                       f"{type(exc).__name__}: {str(exc)[:120]}")
        if dropped:
            ctx.log(f"whole_pdf:pages_not_sent(larger than a request)="
                    f"{','.join(str(p + 1) for p in dropped)}")
        if not parts:
            return _fallback(doc, ctx, "no page fits in one request")
        # A statement a person switched off on the Page Scope screen is not asked for either.
        lines = [it for it in W.lines_to_ask(ctx.line_items) if W.statement_of(it) not in off]
        chunks = W.chunk_lines(lines, int(ex.whole_pdf_lines_per_request))
        if not chunks:
            return _fallback(doc, ctx, "the line-item set names no line to ask for")
        ctx.log(f"whole_pdf:plan pages={sum(len(p.pages) for p in parts)} of {len(doc.pages)} "
                f"({why}) parts={len(parts)} "
                f"sizes_mb={','.join(f'{p.b64_bytes / 1048576:.1f}' for p in parts)} "
                f"lines={len(lines)} requests={len(parts) * len(chunks)} "
                f"provider={provider.id}")

        documents = [W.as_document(part, i + 1) for i, part in enumerate(parts)]
        replies = self._ask(ctx, provider, parts, documents, chunks)
        if replies is None:
            return _fallback(doc, ctx, "the first request failed (see the line above)")
        if not replies:
            return _fallback(doc, ctx, "no request succeeded")

        try:
            texts = W.read_pages(ctx.raw_bytes, pages)
            readings, units, unknown, unreadable = _readings(doc, parts, chunks, replies, texts)
            merged = W.merge(readings)
            rows = _rows(doc, ctx.line_items, merged, provider.id)
        except Exception as exc:  # noqa: BLE001 - replies that cannot be written are not trusted
            return _fallback(doc, ctx, f"the replies could not be written: "
                                       f"{type(exc).__name__}: {str(exc)[:120]}")
        if not rows:
            return _fallback(doc, ctx, "the model found none of the template's lines")
        doc.line_items = rows

        statuses: dict[str, int] = {}
        for r in merged.values():
            statuses[r.check.status] = statuses.get(r.check.status, 0) + 1
        answered = {k for k, _, _ in merged}
        ctx.mapping_strategy = "whole_pdf"
        ctx.mapping_strategy_reason = (
            f"the PDF itself was sent to the model ({provider.id}): {len(parts)} part(s), "
            f"{sum(len(p.pages) for p in parts)} pages; row reconstruction and mapping skipped")
        ctx.whole_pdf = {
            "pages_sent": [p + 1 for part in parts for p in part.pages],
            "pages_not_sent": [p + 1 for p in dropped],
            "parts": len(parts), "requests": len(parts) * len(chunks),
            "requests_answered": len(replies), "lines_asked": len(lines),
            "lines_answered": len(answered), "figures": len(merged),
            "checks": statuses, "units": units,
            "unknown_keys": sorted(unknown), "unreadable_amounts": unreadable,
        }
        ctx.only_stages = KEPT_STAGES
        ctx.log(f"whole_pdf:read lines={len(answered)} of {len(lines)} figures={len(merged)} "
                f"checks={','.join(f'{k}:{v}' for k, v in sorted(statuses.items()))} "
                f"units={units or '?'} unknown_keys={len(unknown)} unreadable={unreadable}")
        ctx.log("whole_pdf:extraction_skipped — the stages that rebuild rows, read note tables "
                "and map captions do not run; units, signs and the checks run on these figures")
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

    def _ask(self, ctx, provider, parts, documents, chunks):
        """Every (part, lines) request, the first of each part on its own — it writes the part to
        the provider's prompt cache for the others — then the rest of the part
        `llm_parallel_requests` at a time. None when the run's first request fails: the file
        itself was refused, or the provider is not answering, and the rules read the filing."""
        width = max(1, int(getattr(ctx.settings.extraction, "llm_parallel_requests", 1) or 1))
        replies: dict[tuple[int, int], W.WholePdfReply] = {}
        total = len(parts) * len(chunks)
        done = 0

        def ask(pi: int, ci: int):
            message = {"role": "user", "documents": [documents[pi]],
                       "content": W.question(chunks[ci], parts[pi], pi + 1, len(parts))}
            return complete_with_room(provider, system=W.SYSTEM, messages=[message],
                                      response_schema=W.WholePdfReply,
                                      max_tokens=W.max_tokens_for(len(chunks[ci])))

        def settle(pi: int, ci: int, call) -> bool:
            nonlocal done
            done += 1
            try:
                reply, meta = call()
            except Exception as exc:  # noqa: BLE001 - one request's failure is reported, not raised
                ctx.llm_failures += 1
                ctx.log(f"whole_pdf:request_failed part={pi + 1} lines={len(chunks[ci])} "
                        f"({W.statement_of(chunks[ci][0])}) "
                        f"error={type(exc).__name__}: {str(exc)[:200]}")
                ctx.emit_step(done, total, "whole-PDF requests")
                return False
            _account(ctx, meta)
            replies[(pi, ci)] = reply
            ctx.log(f"whole_pdf:request part={pi + 1} lines={len(chunks[ci])} "
                    f"({W.statement_of(chunks[ci][0])}) answered={len(reply.lines)} "
                    f"tokens_in={meta.get('input_tokens', 0)} "
                    f"cached={meta.get('cached_input_tokens', 0)} "
                    f"out={meta.get('output_tokens', 0)}")
            ctx.emit_step(done, total, "whole-PDF requests")
            return True

        for pi in range(len(parts)):
            first_ok = settle(pi, 0, lambda pi=pi: ask(pi, 0))
            if pi == 0 and not first_ok:
                return None
            rest = list(range(1, len(chunks)))
            if width == 1:
                for ci in rest:
                    settle(pi, ci, lambda pi=pi, ci=ci: ask(pi, ci))
                continue
            with ThreadPoolExecutor(max_workers=width) as pool:
                futures = [(ci, pool.submit(ask, pi, ci)) for ci in rest]
                for ci, fut in futures:          # settled in plan order, whatever finishes first
                    settle(pi, ci, fut.result)
        return replies


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
        asked = {item.key for item in chunks[ci]}
        part = parts[pi]
        for line in reply.lines:
            if line.key not in asked:
                unknown.add(line.key)
                continue
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


def _citation(r, page: int, kind: str, producer: str, page_kind, provenance) -> dict:
    """The figure's trail, in the shape a model's citation on the rules route records
    (`stages.line_item_llm`), so the Workspace's inspector says where it was read and lists each
    printed row it was read from, each clickable to its page: the row itself, or the rows a sum
    was added from, each with its own figure and box.

    `printed_in` says whether the page is a statement or a note: this route reads no note tables,
    so it has no note number to give, and without it a figure read in a note would be worded as
    read "on the face" (`api.routes.documents._citation_reading`)."""
    printed_in = "face" if page_kind == PageKind.FACE else "notes"

    def entry(label: str, value, box, label_box) -> dict:
        prov = Provenance(page_index=page, bbox=box, value_bbox=box, label_bbox=label_box,
                          text_snippet=label or None, source_kind=kind, producer=producer)
        return {"label": label or r.caption or r.key, "value": str(value), "note": None,
                "counted": True, "printed_in": printed_in,
                "provenance": prov.model_dump(mode="json")}

    if r.rows:
        inputs = [entry(c, a, *(r.row_boxes[i] if i < len(r.row_boxes) else (None, None)))
                  for i, (c, a) in enumerate(r.rows)]
    else:
        inputs = [{**entry(r.caption, r.amount, r.box, r.label_box),
                   "provenance": provenance.model_dump(mode="json")}]
    counted = [i["label"] for i in inputs]
    return derivation.build(
        method=f"whole_pdf:{r.check.status}", formula=" + ".join(counted), inputs=inputs,
        result=r.amount, flags=[f"{FLAG}:{r.check.status}"],
        display={"op": "sum" if r.rows else "first",
                 "combine": "component" if r.rows else "whole"})


def _rows(doc, line_item_set, merged: dict, provider_id: str) -> list[LineItem]:
    """One row per (line, basis), in the set's order, holding its current and prior figures."""
    by_key = {item.key: item for item in line_item_set.items}
    order = {item.key: i for i, item in enumerate(line_item_set.items)}
    kinds = {p.index: p.kind for p in doc.pages}
    source = {p.index: getattr(p.source_kind, "value", p.source_kind) for p in doc.pages}
    groups: dict[tuple[str, str], list[W.Reading]] = {}
    for (key, basis, _period), reading in merged.items():
        groups.setdefault((key, basis), []).append(reading)

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
        for r in group:
            flags = [FLAG, f"{FLAG}:{r.check.status}"]
            if r.check.note:
                flags.append(f"{FLAG}:{r.check.status}:{r.check.note}")
            for other in r.others:
                flags.append(f"{FLAG}:other_reading:{other}")
            if r.rows:
                flags.append(f"{FLAG}:sum_of_rows:" + " + ".join(
                    f"{c or '?'} {a}" for c, a in r.rows))
            score = _SCORE.get(r.check.status, 0.3) - (0.2 if r.others else 0.0)
            scores.append(score)
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
            row.set_value(ExtractedValue(
                basis=Basis(r.basis), period_label=r.period, column_period=r.period,
                period_display=r.period_end or None,
                value_raw=Decimal(r.amount), value=Decimal(r.amount),
                provenance=provenance,
                confidence=ConfidenceVector(mapping=max(0.0, score), method="llm",
                                            flags=flags)))
            row.derivation = derivation.record(
                row.derivation, basis=r.basis, period_label=r.period,
                derivation=_citation(r, page, kind, producer, kinds.get(page), provenance))
        row.confidence.method = MappingMethod.LLM.value
        row.confidence.mapping = max(0.0, min(scores)) if scores else 0.0
        row.confidence.flags.append(FLAG)
        # THE STATEMENT THE ROW IS ON is its line's: the model was asked it as that statement's
        # line. A page is not a statement — a mainland filing ends one statement and starts the
        # next on the same page — and without this every stage that resolves a row's statement
        # (`buckets.statement_resolver`) read the PAGE's: on 11033486 the parent company's
        # liabilities and equity, printed above the income statement's title on page 136, were
        # read as income-statement rows, failed normalize's balance/flow check and lost their
        # line (15 rows, the balance sheet's checks with them). `pdf_extract` stamps the same
        # flag on a page it splits.
        if item is not None:
            row.confidence.flags.append(f"{PRINTED_ON_FLAG}:{W.statement_of(item)}")
        if any(r.check.status in _REVIEWED or r.others for r in group):
            row.confidence.flags.append(REVIEW_FLAG)
        out.append(row)
    return out
