#!/usr/bin/env python
"""Can the model NAME the sub-line item it is now required to answer with?

THE QUESTION THIS SETTLES. A derived parent is refused, and the sub-item is the answer. But the
sub-items are absent from the working view, so the ONLY channel by which the model learns their
keys is `identified_notes[].identified_for`. A sub-item whose note is never identified in a given
filing is therefore a key the model cannot name and a line nothing can fill from the LLM path —
the refusal would be trading a wrong answer for no answer.

So this measures, per filing: of the sub-items declared under the eight focus parents, how many are
NAMED in the request the model actually receives, and which are not — split by whether the miss
matters (the filing has no such note) or is a configuration gap (the note is there and the patterns
missed it).

    python scripts/sub_item_reachability.py _run8/laisun.pdf _run8/suncreate.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FOCUS = ["is_pl__deprec_and_impairment_oper_exp", "is_pl__deprec_and_impairment_cos",
         "bs_ca__secur_and_other_fincl_assets_cp", "bs_nca__secur_and_other_fincl_assets_ltp",
         "notes__contingent_liabilities", "bs_nca__due_from_related_parties_ltp",
         "bs_ca__other_receivables_cp", "is_pl__sales_revenues"]


def _notes_for(pdf: pathlib.Path, cfg):
    """The notes AS `map_ontology` SEES THEM, which is not the same list as the one the run ends
    with — and getting that wrong understates reachability badly.

    `PruneNotesStage` runs late and publishes only the notes a face line REFERENCES, so by the end
    of a run the uncited ones are gone. But `map_ontology` (which builds `identified_notes`) and
    `note_sourced` both run BEFORE it. Reading `doc.notes` after `run_extraction` therefore measures
    a list neither of them ever saw. So the probe snapshots the note list at the moment pruning is
    about to happen, which is the state both stages worked from.

    Returns `(pre_prune, published)` so the pruning's own effect is visible rather than assumed.
    """
    from app.config import get_settings
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view
    from app.stages.prune_notes import PruneNotesStage

    snapshot: list = []
    original = PruneNotesStage.run

    def capture(self, doc, ctx):
        snapshot.extend(doc.notes or ())
        return original(self, doc, ctx)

    settings = get_settings()
    settings.extraction.llm_mapping = False
    PruneNotesStage.run = capture
    try:
        doc, _ctx = run_extraction(pdf.read_bytes(), filename=pdf.name,
                                   ontology=build_working_view(cfg), template=None, line_items=cfg)
    finally:
        PruneNotesStage.run = original
    return snapshot, list(doc.notes or ())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    args = ap.parse_args()

    from app.schemas.line_items import load_line_item_set
    from app.services import note_context

    seed = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "output_csv_hk_line_items.json")
    cfg = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)

    subs = {i.key: i for i in cfg.items if getattr(i, "parent", "") in FOCUS}
    declaring = {k for k, i in subs.items() if getattr(i, "note_source", None) is not None}
    print(f"{len(subs)} sub-items under the 8 focus parents; "
          f"{len(declaring)} declare a note_source")

    for raw in args.pdfs:
        pdf = pathlib.Path(raw).resolve()
        if not pdf.exists():
            print(f"\n{pdf.name}: NOT FOUND")
            continue
        pre, published = _notes_for(pdf, cfg)
        ident = note_context.identified_notes(cfg, pre)
        named = {k for entry in ident for k in (entry.get("identified_for") or ())}

        # Cached so the "is this a real absence or a pattern gap?" question can be answered
        # without re-parsing a 367-page filing each time it is asked.
        titles = [{"number": str(getattr(t, "note_number", "") or ""),
                   "title": getattr(t, "title", "") or "",
                   "rows": len(getattr(t, "items", None) or ())} for t in pre]
        cache = pathlib.Path(f"_notetitles_{pdf.stem}.json")
        cache.write_text(json.dumps(titles, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  note titles cached to {cache}")

        reachable = sorted(declaring & named)
        print(f"\n{pdf.name}: {len(pre)} notes at map_ontology, {len(published)} published after "
              f"pruning; {len(ident)} identified, naming {len(named)} keys")
        print(f"  reachable focus sub-items: {len(reachable)} of {len(declaring)}")

        # THE ROLL-UP THAT MATTERS. A low sub-item count is not itself a problem: a filing
        # discloses ONE of several alternative forms, so most sub-items SHOULD be unreachable in
        # any given filing — HK's "profit before taxation" note and the PRC's 主营业务收入 note are
        # alternatives, not both-expected. What would be a problem is a PARENT with no reachable
        # child at all: that parent can never be answered on the LLM path in this filing, because
        # the model has no key to name.
        print(f"  {'parent':52s} reachable children")
        for parent in FOCUS:
            kids = {k for k, i in subs.items() if i.parent == parent} & declaring
            hit = sorted(kids & named)
            mark = "  " if hit else "->"
            print(f"  {mark}{parent:50s} {len(hit)} of {len(kids)}"
                  + (f"   e.g. {hit[0]}" if hit else "   NONE — no LLM path here"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
