#!/usr/bin/env python
"""Does a line item's MEANING find the same notes its authored regexes find?

THE GROUND TRUTH IS THE REGEXES, and this is the one place they can serve as one. 77 line items
declare `note_source.note_title_any` — ~650 hand-written patterns — and those patterns demonstrably
work: they are what produced every figure in the focus runs (587,417 / 788,507 / 934,842 on laisun,
73,408,279.18 / 131,951,282.62 on suncreate). So for any filing, the notes a line item's regexes
match are notes that line item really is in.

That makes the question measurable rather than a matter of taste: for each line item, does the
semantic probe rank the regex-matched notes at the top?

WHAT IS BEING CALIBRATED. Two numbers, and neither carries over from the row-driven path:

  * `min_score` — `ContextPool.select` uses 0.22 for a ROW-caption probe against note-plus-rows
    units. Here both sides differ (a line item's prose, against headers alone), so the score
    distribution differs with them.
  * `cap` — how many notes one line item's request carries.

RECALL IS THE NUMBER THAT MATTERS, not precision. An extra note in the request costs tokens; a
MISSING note costs the figure, because the model cannot cite what it was not shown. So the report
leads with the rank of each authored note, and a threshold is only worth adopting if it keeps them.

    python scripts/calibrate_line_item_notes.py ../_run8/laisun.pdf ../_run8/suncreate.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def _notes_of(pdf: pathlib.Path):
    """The notes as `map_ontology` sees them — BEFORE `prune_notes`, which runs later and drops
    every note no face row cites."""
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view
    from app.stages.prune_notes import PruneNotesStage

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    settings.extraction.llm_mapping = False

    snapshot: list = []
    original = PruneNotesStage.run

    def capture(self, doc, ctx):
        snapshot.extend(doc.notes or ())
        return original(self, doc, ctx)

    PruneNotesStage.run = capture
    try:
        run_extraction(pdf.read_bytes(), filename=pdf.name,
                       ontology=build_working_view(cfg), template=None, line_items=cfg)
    finally:
        PruneNotesStage.run = original
    return cfg, snapshot


def _regex_hits(item, notes) -> set[str]:
    """The notes this line item's AUTHORED patterns match — the ground truth for one filing."""
    import re
    pats = []
    for raw in (getattr(getattr(item, "note_source", None), "note_title_any", None) or ()):
        try:
            pats.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    if not pats:
        return set()
    out = set()
    for table in notes:
        title = (getattr(table, "title", "") or "")
        number = str(getattr(table, "note_number", "") or "")
        if any(p.search(title) or p.search(number) for p in pats):
            out.add(number or title)
    return out


def report(pdf: pathlib.Path) -> dict:
    from app.services.line_item_notes import header_pool, notes_for_line_item

    cfg, notes = _notes_of(pdf)
    pool = header_pool(notes)
    by_key = {i.key: i for i in cfg.items}

    declaring = [i for i in cfg.items
                 if getattr(getattr(i, "note_source", None), "note_title_any", None)]
    print(f"\n{'=' * 100}")
    print(f"{pdf.name}: {len(notes)} notes, {len(pool)} header units, "
          f"{len(declaring)} line items declaring patterns")
    print("=" * 100)

    rows = []
    for item in declaring:
        truth = _regex_hits(item, notes)
        if not truth:
            continue                      # this filing does not carry the note; nothing to measure
        parent = by_key.get(getattr(item, "parent", "") or "")
        # Unbounded and unthresholded, so the RANK of each authored note is visible rather than
        # only whether it survived a cap someone chose.
        ranked = notes_for_line_item(item, pool, min_score=0.0, cap=10_000, parent=parent)
        order = {h.note: (n, h.score) for n, h in enumerate(ranked, 1)}
        for note in sorted(truth):
            rank, score = order.get(note, (None, 0.0))
            rows.append({"key": item.key, "note": note, "rank": rank, "score": score})

    if not rows:
        print("  no line item's patterns matched a note in this filing — nothing to calibrate")
        return {"filing": pdf.name, "rows": []}

    found = [r for r in rows if r["rank"] is not None]
    print(f"\n  {len(rows)} (line item, authored note) pairs to find")
    print(f"  {'rank of the authored note':32s} share")
    for bound in (1, 2, 3, 4, 5, 10):
        n = sum(1 for r in found if r["rank"] and r["rank"] <= bound)
        print(f"    within top {bound:<3d}                      {n:>4} / {len(rows)}"
              f"   {100.0 * n / len(rows):5.1f}%")

    scores = sorted(r["score"] for r in found)
    if scores:
        print(f"\n  score of an authored note: min {scores[0]:.3f}  "
              f"p10 {scores[len(scores) // 10]:.3f}  median {scores[len(scores) // 2]:.3f}  "
              f"max {scores[-1]:.3f}")
    print(f"\n  {'threshold':>10s}  {'authored notes kept':>20s}  {'notes per line item (avg)':>26s}")
    for thr in (0.10, 0.14, 0.18, 0.22, 0.30):
        kept = sum(1 for r in found if r["score"] >= thr)
        per = []
        for item in declaring:
            parent = by_key.get(getattr(item, "parent", "") or "")
            per.append(len(notes_for_line_item(item, pool, min_score=thr, cap=10_000,
                                               parent=parent)))
        avg = sum(per) / max(1, len(per))
        print(f"  {thr:>10.2f}  {kept:>13} / {len(rows)}  {avg:>26.1f}")

    worst = sorted(rows, key=lambda r: (r["rank"] is not None, r["rank"] or 0), reverse=True)[:6]
    print("\n  the hardest pairs (highest rank or not found at all):")
    for r in worst:
        print(f"    rank {str(r['rank']):>5s}  score {r['score']:.3f}  note {r['note']:>4s}  "
              f"{r['key']}")
    return {"filing": pdf.name, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("-o", "--out", default="")
    args = ap.parse_args()

    out = []
    for raw in args.pdfs:
        pdf = pathlib.Path(raw).resolve()
        if not pdf.exists():
            print(f"{raw}: NOT FOUND")
            continue
        out.append(report(pdf))
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
