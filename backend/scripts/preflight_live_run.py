#!/usr/bin/env python
"""WHAT A LIVE RUN WOULD SEND, AND WHAT IT WOULD COST — measured without spending anything.

WHY THIS EXISTS. `stages.line_item_llm` has never made a real provider call: every test drives it
with a stub. So the first live run is also the first exercise of the request SIZE, the response
budget and the grouping — and a 413 or a truncated reply discovered on a client filing is an
expensive way to learn a number that can be counted here for free.

Everything below is built from the real document and the real configuration, through the same
`plan_and_notes` / `build_request` the stage calls. Nothing is estimated: the character counts are
`len(json.dumps(request))` on the exact payload, and the token figures divide by 3, which is the
ratio this codebase measured for JSON of identifiers and CJK captions.

WHAT TO LOOK AT, IN ORDER:

  1. REQUESTS and TOKENS TOTAL — the whole bill for one filing, per grouping mode. This is the
     number to take to a rate limit.
  2. THE LARGEST SINGLE REQUEST — the number that decides whether a run 413s. A gateway refusing
     one request loses that request's lines to the deterministic route; it does not fail the run.
  3. RESPONSE BUDGET HEADROOM — `_max_tokens(n) = 512 + 200n` against what a reply of n answers
     with citations actually serialises to. A truncated structured reply does not parse, so the
     whole request is lost.
  4. NOTES PER REQUEST, AND WHERE THEY CAME FROM — a note admitted by a `note_title_any` PATTERN is
     admitted unconditionally, so a wrong pattern puts a whole irrelevant note in the payload and
     invites a citation from it. Split out, because it is the one failure mode here that costs
     accuracy rather than money.

    python scripts/preflight_live_run.py ../_run8/laisun.pdf ../_run8/suncreate.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

MODES = ("none", "identical", "similar", "manual")


def _document(pdf: pathlib.Path):
    """The document as `line_item_llm` sees it — notes BEFORE `prune_notes` drops the uncited."""
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view
    from app.stages.prune_notes import PruneNotesStage

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    settings.extraction.llm_mapping = False          # no call is made while measuring

    snapshot: list = []
    original = PruneNotesStage.run

    def capture(self, doc, ctx):
        snapshot.extend(doc.notes or ())
        return original(self, doc, ctx)

    PruneNotesStage.run = capture
    try:
        doc, _ctx = run_extraction(pdf.read_bytes(), filename=pdf.name,
                                   ontology=build_working_view(cfg), template=None,
                                   line_items=cfg)
    finally:
        PruneNotesStage.run = original
    doc.notes = snapshot or doc.notes
    return cfg, doc


def _pattern_admitted(cfg, notes) -> dict[str, set[str]]:
    """Per line key, the notes a `note_title_any` PATTERN admits — as against a semantic hit.

    The distinction is the point. `note_context.identified_notes` passes a pattern-matched note
    WHOLE and UNCONDITIONALLY, on the stated ground that an author's declaration outranks any
    score. So a pattern that is too broad does not merely add noise to a ranking — it puts the
    entire text of an unrelated note into the request, and the reply contract then invites a
    citation from it.
    """
    import re

    out: dict[str, set[str]] = {}
    for item in cfg.items:
        source = getattr(item, "note_source", None)
        pats = []
        for raw in (getattr(source, "note_title_any", None) or ()):
            try:
                pats.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                continue
        if not pats:
            continue
        hit = {str(getattr(t, "note_number", "") or "")
               for t in notes
               if any(p.search(getattr(t, "title", "") or "") for p in pats)}
        if hit:
            out[item.key] = hit
    return out


def report(pdf: pathlib.Path, focus_only: bool) -> None:
    from app.config import get_settings
    from app.services import line_item_llm, line_item_requests
    from app.stages.line_item_llm import _max_tokens

    cfg, doc = _document(pdf)
    settings = get_settings()
    patterned = _pattern_admitted(cfg, doc.notes)

    print("=" * 100)
    print(f"{pdf.name}: {len(doc.notes)} notes, {len(doc.line_items)} rows"
          f"   focus_only={focus_only}")
    print("=" * 100)

    focus = set(settings.extraction.llm_focus_keys or ()) if focus_only else set()

    print(f"\n  {'mode':>10s} {'requests':>9s} {'lines':>6s} {'chars':>12s} {'~tokens':>9s}"
          f" {'largest req':>12s} {'notes/req':>10s} {'by pattern':>11s}")
    for mode in MODES:
        settings.extraction.llm_request_grouping = mode
        plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(
            cfg, doc.notes, settings)
        if focus:
            plans = [p for p in plans if any(k in focus for k in p.keys)]

        sizes, note_counts, pattern_counts, answers = [], [], [], []
        for plan in plans:
            request = line_item_llm.build_request(plan, by_key, notes_of, identified)
            if not request["line_items"]:
                continue
            sizes.append(len(json.dumps(request, ensure_ascii=False)))
            note_counts.append(len(request["notes"]))
            answers.append(len(request["line_items"]))
            by_pat = {n for k in plan.keys for n in patterned.get(k, ())}
            pattern_counts.append(len(by_pat & set(plan.notes)))
        if not sizes:
            print(f"  {mode:>10s}        (no request)")
            continue
        total = sum(sizes)
        print(f"  {mode:>10s} {len(sizes):>9d} {sum(answers):>6d} {total:>12,d}"
              f" {total // 3:>9,d} {max(sizes):>12,d} {sum(note_counts)/len(note_counts):>10.1f}"
              f" {sum(pattern_counts)/max(1,len(pattern_counts)):>11.1f}")

    # ── the response budget, against the biggest group ─────────────────────────────────────────
    settings.extraction.llm_request_grouping = "identical"
    plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(cfg, doc.notes, settings)
    if focus:
        plans = [p for p in plans if any(k in focus for k in focus and p.keys)]
    widest = max(plans, key=lambda p: len(p.keys)) if plans else None
    if widest is not None:
        n = len(widest.keys)
        # One answer with two citations, serialised from the real schema — not a guess.
        one = line_item_llm.LineItemAnswer(
            key=max((k for k in by_key), key=len),
            sources=[line_item_llm.SourceRef(note="七、25", caption="固定资产折旧及无形资产摊销"),
                     line_item_llm.SourceRef(note="7", caption="Depreciation of right-of-use assets")],
            role="component", signs=[1, -1], confidence=0.92,
            reason="the note's own operating-expense depreciation callout, less the capitalised share")
        envelope = line_item_llm.LineItemReply(answers=[one] * n).model_dump_json()
        need = len(envelope) // 3
        have = _max_tokens(n)
        verdict = "OK" if have > need * 1.3 else ("TIGHT" if have > need else "TOO SMALL")
        print(f"\n  RESPONSE BUDGET on the widest group ({n} line items, {widest.name}):")
        print(f"    a reply of {n} answers with 2 citations each serialises to "
              f"{len(envelope):,} chars ~= {need:,} tokens")
        print(f"    _max_tokens({n}) allows {have:,} tokens   -> {verdict}"
              f"  (headroom {have / max(1, need):.1f}x)")

    # ── where the notes came from ──────────────────────────────────────────────────────────────
    settings.extraction.llm_request_grouping = "none"
    plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(cfg, doc.notes, settings)
    asked = [p for p in plans if not focus or any(k in focus for k in p.keys)]
    pat_only, both = 0, 0
    for plan in asked:
        for key in plan.keys:
            by_pat = patterned.get(key, set())
            for note in plan.notes:
                if note in by_pat:
                    pat_only += 1
                else:
                    both += 1
    print(f"\n  NOTE ADMISSIONS across {len(asked)} single-line requests:"
          f"  {pat_only} by PATTERN (unconditional), {both} by SCORE")
    print(f"  a pattern-admitted note is passed WHOLE whatever it scores, so an over-broad "
          f"`note_title_any`\n  puts an unrelated note in the payload and the contract then "
          f"invites a citation from it.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--all-lines", action="store_true",
                    help="ignore llm_focus_keys and price every asked-about line")
    args = ap.parse_args()
    for raw in args.pdfs:
        pdf = pathlib.Path(raw).resolve()
        if not pdf.exists():
            print(f"{raw}: NOT FOUND")
            continue
        report(pdf, focus_only=not args.all_lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
