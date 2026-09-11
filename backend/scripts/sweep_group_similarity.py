#!/usr/bin/env python
"""AT WHAT THRESHOLD DOES `similar` GROUPING ACTUALLY DIFFER FROM `identical`?

THE DEFECT THIS MEASURES. `extraction.llm_request_grouping = "similar"` produces byte-identical
output to `"identical"` on both reference filings at the shipped `llm_group_similarity = 0.8` — 34
requests on laisun, 49 on suncreate. An operator choosing "similar" expecting a saving gets none,
and nothing says so.

WHY THAT CAN HAPPEN AT ALL. `line_item_notes.group_by_note_set` merges two lines when their note
sets overlap by at least the threshold, measured as Jaccard — |A n B| / |A u B|. Two facts about
the real note sets decide whether any pair ever clears it:

  * THE SETS ARE SMALL. After `notes_for_line_item`'s `cap=4`, and with most lines receiving fewer,
    a line's set is typically 1 to 3 notes. Jaccard on small sets is coarse: {7} vs {7,12} scores
    0.50, {7,12} vs {7,19} scores 0.33. There is no value near 0.8 that such a pair reaches, so at
    0.8 only IDENTICAL sets merge — which is the other mode.
  * SO THE INTERESTING RANGE IS LOW, and it is bounded below by the point where everything merges
    into one request, which is not grouping either.

WHAT THIS PRINTS, per threshold: requests, lines per request, the largest request, total tokens, and
whether the answer differs from `identical`. The value to adopt — if any — is the one that merges
lines sharing MOST of their notes without merging lines that share one.

    python scripts/sweep_group_similarity.py ../_run8/laisun.pdf ../_run8/suncreate.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

STEPS = (1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.34, 0.30, 0.25, 0.20, 0.10)


def document(pdf: pathlib.Path):
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view
    from app.stages.prune_notes import PruneNotesStage

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    get_settings().extraction.llm_mapping = False
    snap: list = []
    original = PruneNotesStage.run

    def capture(self, doc, ctx):
        snap.extend(doc.notes or ())
        return original(self, doc, ctx)

    PruneNotesStage.run = capture
    try:
        doc, _ctx = run_extraction(pdf.read_bytes(), filename=pdf.name,
                                   ontology=build_working_view(cfg), template=None,
                                   line_items=cfg)
    finally:
        PruneNotesStage.run = original
    doc.notes = snap or doc.notes
    return cfg, doc


def plan_for(cfg, doc, settings, mode, similarity=None):
    from app.services import line_item_llm

    settings.extraction.llm_request_grouping = mode
    if similarity is not None:
        settings.extraction.llm_group_similarity = similarity
    plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(cfg, doc.notes, settings)
    sizes, lines = [], []
    for plan in plans:
        request = line_item_llm.build_request(plan, by_key, notes_of, identified)
        if not request["line_items"]:
            continue
        sizes.append(len(json.dumps(request, ensure_ascii=False)))
        lines.append(len(request["line_items"]))
    shape = tuple(sorted(tuple(sorted(p.keys)) for p in plans))
    return {"requests": len(sizes), "lines": sum(lines), "chars": sum(sizes),
            "largest": max(sizes) if sizes else 0, "shape": shape}


def report(pdf: pathlib.Path) -> None:
    from app.config import get_settings

    cfg, doc = document(pdf)
    settings = get_settings()

    none_ = plan_for(cfg, doc, settings, "none")
    ident = plan_for(cfg, doc, settings, "identical")

    print("=" * 108)
    print(f"{pdf.name}")
    print("=" * 108)
    print(f"  {'mode / threshold':>18s} {'requests':>9s} {'lines':>6s} {'~tokens':>9s} "
          f"{'largest':>9s} {'vs identical':>13s}")
    print(f"  {'none':>18s} {none_['requests']:>9d} {none_['lines']:>6d} "
          f"{none_['chars']//3:>9,d} {none_['largest']:>9,d} {'—':>13s}")
    print(f"  {'identical':>18s} {ident['requests']:>9d} {ident['lines']:>6d} "
          f"{ident['chars']//3:>9,d} {ident['largest']:>9,d} {'(baseline)':>13s}")

    first_diff = None
    collapsed = None
    for thr in STEPS:
        got = plan_for(cfg, doc, settings, "similar", thr)
        differs = got["shape"] != ident["shape"]
        if differs and first_diff is None:
            first_diff = thr
        if got["requests"] <= 1 and collapsed is None:
            collapsed = thr
        note = "DIFFERS" if differs else "same as identical"
        print(f"  {('similar ' + format(thr, '.2f')):>18s} {got['requests']:>9d} "
              f"{got['lines']:>6d} {got['chars']//3:>9,d} {got['largest']:>9,d} {note:>13s}")

    print()
    if first_diff is None:
        print("  NO THRESHOLD IN 0.10..1.00 CHANGES THE GROUPING. `similar` is not a mode on this "
              "filing;\n  every merge it would make, `identical` already makes.")
    else:
        print(f"  FIRST THRESHOLD THAT CHANGES ANYTHING: {first_diff:.2f}")
        if collapsed:
            print(f"  everything collapses into one request at: {collapsed:.2f}")
        print(f"  so the usable band is below {first_diff:.2f} and above "
              f"{collapsed if collapsed else 0.0:.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    args = ap.parse_args()
    for raw in args.pdfs:
        pdf = pathlib.Path(raw).resolve()
        if pdf.exists():
            report(pdf)
        else:
            print(f"{raw}: NOT FOUND")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
