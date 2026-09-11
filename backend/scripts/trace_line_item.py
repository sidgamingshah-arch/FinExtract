#!/usr/bin/env python
"""ONE LINE ITEM, END TO END — every stage that could have filled it, in the order they run.

    python scripts/trace_line_item.py ../_run8/laisun.pdf --key is_pl__sales_revenues

WHAT THIS ANSWERS that no other script does. The eight figure reports say WHAT a line published;
the request capture says what ONE call carried. Neither says why a particular line got the figure
it got — which tier reached it, which notes were put in front of the model for it, whether the
model was offered it at all, and what its cascade did with the parts. This walks one key through
all of that on a real filing, so the answer is measured rather than reasoned about.

THE ORDER BELOW IS THE PIPELINE'S ORDER, not a convenient one:

    1. the declaration        what the configuration says this line is
    2. the LLM gate           whether the model is offered the key, and which rule decided
    3. notes selected         regex-claimed notes, and semantically scored ones with their scores
    4. the request            the batch this line's own rows fell in, and what the batch carried
    5. the deterministic path which rows bound to the key, by which tier, with what figure
    6. the cascade            each rung, its inputs, and which one resolved
    7. the parts              every child, its own route, and its figure
    8. published              the figure that reaches the output, and the flags on it

A provider is only called where one is configured; with none, section 4 reports the batches that
WOULD have been sent, captured from the request the stage builds, which is the same payload.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

RULE = "=" * 100
THIN = "-" * 100


class _Capture:
    """Records every request the mapping stage builds, and answers nothing.

    ANSWERING NOTHING IS DELIBERATE. The trace has to show the deterministic path as it really is,
    and a provider that invents answers would displace it — the exact failure measured earlier in
    this work, where a configured-but-failing provider published figures BELOW the deterministic
    tiers. An empty answer leaves every other tier exactly as it would be with no provider at all,
    so what section 4 prints is the payload and what sections 5-8 print is the real run.
    """

    id = "trace-capture"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, **_):
        user = messages[-1]["content"]
        try:
            payload = json.loads(user)
        except Exception:
            payload = {}
        self.calls.append({"system_chars": len(system), "user_chars": len(user),
                           "payload": payload})
        # `(decision, meta)`, which is the contract both call sites in `mapping.py` unpack.
        return response_schema.model_validate({"mappings": []}), {}


def _money(v):
    if v is None:
        return "—"
    f = float(v)
    return f"{f:,.2f}" if f % 1 else f"{int(f):,}"


def _fig(li, period="current"):
    for ev in (li.values or {}).values():
        if getattr(ev, "period_label", None) == period and ev.value is not None:
            return ev.value
    return None


def _wrap(text: str, width: int = 92, indent: str = " " * 6) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(text, width, initial_indent=indent,
                                   subsequent_indent=indent)) if text else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--key", default="is_pl__sales_revenues")
    args = ap.parse_args()

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services import line_item_notes
    from app.services.documents import run_extraction
    from app.services.mapping import OntologyMatcher
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    view = build_working_view(cfg)
    defs = {i.key: i for i in cfg.items}
    item = defs.get(args.key)
    if item is None:
        print(f"no line item {args.key!r}; the set has {len(defs)}")
        return 2

    capture = _Capture()
    settings = get_settings()
    settings.extraction.llm_mapping = True
    settings.llm.provider = capture.id

    # THE NOTES PRE-PRUNE. `PruneNotesStage` runs at 9 and `map_ontology` at 6, so reading
    # `doc.notes` after the run answers a different question from "what was available to the
    # matcher" — a mistake made once in this work and worth not making twice. Snapshotted by
    # wrapping the prune stage rather than by a second pass, which would double a 27-second run.
    from app.stages.prune_notes import PruneNotesStage
    seen: dict = {}
    original = PruneNotesStage.run

    def _snapshot(self, doc, ctx):
        seen.setdefault("notes", list(doc.notes))
        return original(self, doc, ctx)

    PruneNotesStage.run = _snapshot
    try:
        began = time.time()
        doc, ctx = run_extraction(args.pdf.read_bytes(), filename=args.pdf.name, ontology=view,
                                  template=None, line_items=cfg,
                                  context_cb=lambda c: c.registry.register(
                                      "llm", capture.id, lambda: capture))
        took = time.time() - began
    finally:
        PruneNotesStage.run = original

    print(f"\n{RULE}")
    print(f"{args.pdf.name}  —  {len(doc.pages)} pages, {len(doc.line_items)} rows, "
          f"{len(doc.notes)} notes after pruning, {took:.0f}s")
    print(f"TRACE OF  {args.key}")
    print(RULE)

    # ── 1. THE DECLARATION ────────────────────────────────────────────────────────────────────
    print("\n1. THE DECLARATION — what the configuration says this line is")
    print(THIN)
    ns = getattr(item, "note_source", None)
    print(f"   label            {item.label}")
    print(f"   type             {item.type}      (a derived line's figure is its cascade's; "
          f"`services.line_items.evaluate` branches here)")
    print(f"   extraction_mode  {item.extraction_mode}")
    print(f"   statement        {item.statement}   section_scope={list(item.section_scope)}")
    print(f"   match_priority   {item.match_priority}    alias_matching={item.alias_matching}")
    print(f"   aliases          {len(item.aliases)}: {item.aliases[:4]}{' …' if len(item.aliases) > 4 else ''}")
    print(f"   regex_hints      {item.regex_hints}")
    print(f"   definition       {len(item.definition or '')} chars")
    print(_wrap((item.definition or "(none)")[:400]))
    print(f"   prompt           {len(getattr(item, 'prompt', '') or '')} chars")
    if getattr(item, "prompt", ""):
        print(_wrap(item.prompt[:300]))
    print(f"   note_source      {'declared' if ns else 'none'}")
    if ns:
        for field in ("note_title_any", "row_caption_any", "row_caption_none", "prose_any",
                      "note_terms", "row_terms", "row_terms_none"):
            val = list(getattr(ns, field, []) or [])
            if val:
                print(f"       {field:16s} {len(val)}: {val[:3]}{' …' if len(val) > 3 else ''}")
    print(f"   cascade          {len(item.cascade)} rungs")
    for rung in item.cascade:
        refs = " ".join(f"{'+' if t.sign > 0 else '-'}{t.ref}" for t in rung.terms)
        print(f"       {rung.id:10s} refuse_negative={str(rung.refuse_negative):5s} {refs}")

    # ── 2. THE LLM GATE ───────────────────────────────────────────────────────────────────────
    print("\n2. THE LLM GATE — is the model offered this key at all")
    print(THIN)
    mapper = OntologyMatcher(view, locale="en", settings=settings)
    withheld = args.key in mapper._llm_withheld                  # noqa: SLF001 — the trace's job
    unmatchable = args.key in mapper._unmatchable                # noqa: SLF001
    print(f"   in _llm_withheld    {withheld}    (the model is not offered the key)")
    print(f"   in _computed_parent {args.key in mapper._computed_parent}    "
          f"(its figure is its cascade's)")
    print(f"   in _unmatchable     {unmatchable}    (no printed caption may reach it)")
    never = item._never_asked()                                  # noqa: SLF001
    print(f"   _never_asked       {never or 'no — the model IS asked about this line'}")
    print(f"   offered to model   {'NO' if withheld else 'YES'}")

    # ── 3. THE NOTES SELECTED FOR IT ──────────────────────────────────────────────────────────
    print("\n3. NOTES SELECTED FOR THIS LINE")
    print(THIN)
    notes = list(seen.get("notes") or doc.notes)
    pool = line_item_notes.header_pool(notes)
    hits = line_item_notes.notes_for_line_item(item, pool)
    print(f"   note pool          {len(notes)} notes, {len({n.note_number for n in notes})} "
          f"distinct numbers")
    print(f"   note_probe         {line_item_notes.note_probe(item)[:150]!r}")
    print(f"   row_probe          {line_item_notes.row_probe(item)[:150]!r}")
    if hits:
        print(f"   scored notes       {len(hits)} above the floor:")
        for h in hits:
            print(f"       {h.score:5.3f}  note {(h.note or '?'):>4s}  {(h.title or '')[:70]}")
    else:
        print("   scored notes       none above the floor — nothing in this line's prose names a "
              "note heading")

    # ── 4. THE REQUEST ────────────────────────────────────────────────────────────────────────
    print("\n4. THE REQUEST — what actually went out, and whether this key was in it")
    print(THIN)
    def _cands(call):
        return call["payload"].get("candidates") or []

    print(f"   calls built        {len(capture.calls)}")
    naming = [i for i, c in enumerate(capture.calls, 1)
              if any(k.get("canonical_key") == args.key for k in _cands(c))]
    print(f"   calls offering it  {naming or 'none — no candidate list carries the key'}")
    print(f"   payload blocks     "
          f"{sorted(capture.calls[0]['payload']) if capture.calls else '(no call)'}")
    for i, call in enumerate(capture.calls[:3], 1):
        pay = call["payload"]
        rows = pay.get("source_items") or []
        cands = _cands(call)
        notes_in = pay.get("identified_notes") or []
        sizes = {k: len(json.dumps(v, ensure_ascii=False)) for k, v in pay.items()}
        print(f"\n   call {i}:  {call['user_chars']:,} user chars + {call['system_chars']:,} "
              f"system  (~{(call['user_chars'] + call['system_chars']) // 4:,} tokens)")
        print(f"       rows offered        {len(rows)}")
        print(f"       candidates          {len(cands)}  "
              f"e.g. {[c.get('canonical_key') for c in cands[:3]]}")
        print(f"       identified notes    {len(notes_in)}")
        print("       block sizes         "
              + ", ".join(f"{k}={v:,}" for k, v in
                          sorted(sizes.items(), key=lambda kv: -kv[1])))
        mine = [r for r in rows if args.key in json.dumps(r, ensure_ascii=False)]
        if mine:
            print(f"       rows naming {args.key}:")
            for r in mine[:4]:
                print(f"           {str(r.get('label'))[:60]!r} "
                      f"suggestion={r.get('deterministic_suggestion')}")

    # ── 5-8. THE ROWS, THE CASCADE, THE PARTS, THE FIGURE ─────────────────────────────────────
    rows = [li for li in doc.line_items if li.canonical_key == args.key]
    print("\n5. THE DETERMINISTIC PATH — rows bound to this key")
    print(THIN)
    if not rows:
        print("   no row in the document is bound to this key")
    for li in rows:
        print(f"   {(li.source_label or '')[:60]!r}")
        print(f"       page {getattr(li, 'page_index', '?')}  role={li.role}  "
              f"current={_money(_fig(li))}  prior={_money(_fig(li, 'prior'))}")
        print(f"       flags {[f for f in (li.confidence.flags or [])][:8]}")
        for name, tr in (li.derivation or {}).items():
            print(f"       trail[{name}] method={tr.get('method')} formula={tr.get('formula')}")
            for inp in (tr.get("inputs") or [])[:6]:
                print(f"           {str(inp.get('label'))[:64]:66s} {inp.get('value')}")

    print("\n6. THE CASCADE — which rung resolved, on this filing")
    print(THIN)
    by_key: dict[str, list] = {}
    for li in doc.line_items:
        if li.canonical_key:
            by_key.setdefault(li.canonical_key, []).append(li)
    for rung in item.cascade:
        parts = []
        for t in rung.terms:
            got = next((_fig(r) for r in (by_key.get(t.ref) or []) if _fig(r) is not None), None)
            parts.append(f"{'+' if t.sign > 0 else '-'}{t.ref}={_money(got)}")
        resolved = all(
            any(_fig(r) is not None for r in (by_key.get(t.ref) or [])) for t in rung.terms)
        print(f"   {rung.id:10s} {'RESOLVES' if resolved else 'no':8s} {' '.join(parts)}")

    print("\n7. THE PARTS — every child of this line and how it was filled")
    print(THIN)
    kids = [i for i in cfg.items if getattr(i, "parent", "") == args.key]
    print(f"   {len(kids)} declared")
    for kid in kids:
        krows = by_key.get(kid.key) or []
        val = next((_fig(r) for r in krows if _fig(r) is not None), None)
        how = ""
        for r in krows:
            for tr in (r.derivation or {}).values():
                how = tr.get("method") or ""
                break
        kns = getattr(kid, "note_source", None)
        route = []
        if kns:
            for field in ("note_title_any", "row_caption_any", "prose_any", "note_terms",
                          "row_terms"):
                if getattr(kns, field, None):
                    route.append(field)
        print(f"   {kid.key:58s} {_money(val):>18s}  {how or '(empty)':28s} "
              f"[{','.join(route) or 'no note_source'}]")

    print("\n8. PUBLISHED")
    print(THIN)
    cur = next((_fig(r) for r in rows if _fig(r) is not None), None)
    pri = next((_fig(r, "prior") for r in rows if _fig(r, "prior") is not None), None)
    print(f"   current  {_money(cur)}")
    print(f"   prior    {_money(pri)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
