#!/usr/bin/env python
"""IS THE NOTE A LINE'S FIGURE LIVES IN ACTUALLY PASSED TO THE MODEL? — per line, at the shipped settings.

THE QUESTION THIS ANSWERS, and why the two neighbouring scripts do not. `stages.line_item_llm` asks
a model WHICH ROW of the supplied notes holds a named line's figure, and its answer outranks the
declared `note_source` read entirely (`stages.note_sourced._llm_holds`). So everything that route
publishes depends on the right note being in the payload — and nothing reported whether it was.

  * `calibrate_line_item_notes.py` reports the RANK of an authored note and a threshold table. That
    calibrates the two knobs; it does not say what happens at the values actually shipped.
  * `audit_request_context.py` rebuilds the payload and asks whether an ANSWER was reachable from
    it, which is the stronger question — but it reads `_vocab/`, a captured corpus, so it cannot be
    pointed at a PDF.

This runs the real selection on a real filing at the real `cap` and `MIN_SCORE`, and gives one
verdict per line the run would ask about:

    SUPPLIED   the line declares `note_title_any`, some note of this filing matches it, and that
               note IS among the ones its request carries. The model can answer.
    MISSED     it declares, a note matches, and that note is NOT in the payload. The model is being
               asked to find a figure from notes that do not contain it — and an empty answer here
               is the model being RIGHT.
    NO MATCH   it declares, and no note of this filing matches: the disclosure is absent, so there
               is nothing to pass. Not a defect.
    UNDECLARED the line declares no `note_title_any` at all, so nothing states which note is right
               and this script cannot judge the supply. Counted, never scored — see below.

WHY `UNDECLARED` IS COUNTED SEPARATELY RATHER THAN PASSED OVER. It is the reviewer's finding about
the related-party lines: they are `route: face` columns with no note declaration anywhere, and they
can still be SUPPLIED notes by the probe. A note arriving for a line nothing declares is not
evidence of anything, in either direction — so it is reported as a population, not graded.

THE GROUND TRUTH IS THE AUTHORED REGEX, for the reason `calibrate_line_item_notes` gives: ~650
hand-written `note_title_any` patterns are what produced the figures in the focus runs, so a note
they match is a note that line really is in. That makes this measurable rather than a matter of
taste. Its limit is the same too — a line whose author never declared anything cannot be graded,
which is exactly what `UNDECLARED` records.

    python scripts/note_context_to_llm.py <pdf> [<pdf> …] [--all] [--verbose]

`--all` grades every line the configuration is asked about; the default honours
`extraction.llm_focus_only`, which is what ships, so the default answer is about the run as
configured rather than a hypothetical one.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED = ROOT / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"
TEMPLATE = ROOT / "app" / "sample" / "templates" / "output_csv_hk_v1_template.json"


# THE GROUND-TRUTH PREDICATE IS THE RUN'S OWN — `line_item_notes.declared_notes`, which is also
# what `note_context.identified_notes` admits note TEXT on and what `note_sets` selects on. This
# script asked the question in its own code at first; on this corpus the two agree exactly (the
# same 421 pairs, the same 170 delivered), so sharing it changes no figure here and removes the way
# this report could drift from the run it reports on.
def audit(path: pathlib.Path, cfg, tpl, settings, grade_all: bool) -> dict:
    from app.services import line_item_llm, line_item_notes
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    was_focus = settings.extraction.llm_focus_only
    was_llm = settings.extraction.llm_mapping
    settings.extraction.llm_mapping = False       # the notes are what is wanted, not a provider
    if grade_all:
        settings.extraction.llm_focus_only = False
    try:
        doc, _ctx = run_extraction(path.read_bytes(), filename=path.name,
                                   ontology=build_working_view(cfg), template=tpl,
                                   line_items=cfg)
        notes = doc.notes or ()
        plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(
            cfg, notes, settings, doc=doc)
    finally:
        settings.extraction.llm_focus_only = was_focus
        settings.extraction.llm_mapping = was_llm

    # THE FOCUS FILTER, APPLIED THE WAY THE STAGE APPLIES IT. `plan_and_notes` plans a request for
    # every asked-about line; `stages.line_item_llm` then keeps only the plans naming a focus key
    # (:109-116) and logs the rest as `requests_skipped_by_focus`. Grading the unfiltered 521 would
    # score a population the run never sends — on 688008 the real number is 22.
    focus = (set(settings.extraction.llm_focus_keys or ())
             if settings.extraction.llm_focus_only and not grade_all else set())
    if focus:
        plans = [p for p in plans if any(k in focus for k in p.keys)]
    asked = {k for p in plans for k in p.keys}
    # NAMED, AND SEPARATELY CARRIED. `notes_supplied` is the note NUMBERS a line selected;
    # `build_request` fills the shared "notes" block from `identified_notes`, which scores only
    # items DECLARING a `note_source` — so a line can name a note whose TEXT never travels, and
    # the model is then pointed at a note it cannot read. Measured on 688008: 59 of 521 lines.
    # Grading against the named set alone reports a payload richer than the one that is sent.
    named = {k: set(map(str, notes_of.get(k, ()) or ())) for k in asked}
    carried: dict[str, set[str]] = {}
    for plan in plans:
        req = line_item_llm.build_request(plan, by_key, notes_of, identified)
        block = {str(n.get("note", "")) for n in (req.get("notes") or ())
                 if (n.get("rows") or n.get("prose"))}
        for k in plan.keys:
            carried[k] = block
    rows = []
    for key in sorted(asked):
        item = by_key.get(key)
        if item is None:
            continue
        declared = set(line_item_notes.declared_notes(item, notes))
        got = carried.get(key, set()) & named.get(key, set())
        named_only = named.get(key, set()) - carried.get(key, set())
        if not getattr(getattr(item, "note_source", None), "note_title_any", None):
            verdict = "UNDECLARED"
        elif not declared:
            verdict = "NO MATCH"
        elif declared & got:
            verdict = "SUPPLIED"
        else:
            verdict = "MISSED"
        rows.append({"key": key, "verdict": verdict, "declared": sorted(declared),
                     "supplied": sorted(got), "named_but_not_carried": sorted(named_only),
                     "pairs_declared": len(declared), "pairs_supplied": len(declared & got)})
    return {"filing": path.name, "notes": len(notes), "asked": len(asked), "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--all", action="store_true",
                    help="grade every asked-about line, ignoring llm_focus_only")
    ap.add_argument("--verbose", action="store_true", help="list every MISSED line")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_template

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    tpl = load_template(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    settings = get_settings()
    print(f"cap and floor as shipped: MIN_SCORE and cap live in services.line_item_notes; "
          f"llm_focus_only={settings.extraction.llm_focus_only}"
          f"{' (overridden to False by --all)' if args.all else ''}")

    total: collections.Counter = collections.Counter()
    for name in args.pdfs:
        path = pathlib.Path(name)
        got = audit(path, cfg, tpl, settings, args.all)
        counts = collections.Counter(r["verdict"] for r in got["rows"])
        total.update(counts)
        total["pairs_declared"] += sum(r["pairs_declared"] for r in got["rows"])
        total["pairs_supplied"] += sum(r["pairs_supplied"] for r in got["rows"])
        graded = counts["SUPPLIED"] + counts["MISSED"]
        share = f"{counts['SUPPLIED'] / graded:.0%}" if graded else "-"
        print(f"\n{'=' * 96}\n{got['filing']}: {got['notes']} notes, "
              f"{got['asked']} lines asked about\n{'=' * 96}")
        print(f"  SUPPLIED   {counts['SUPPLIED']:>4}   the declared note IS in the payload")
        print(f"  MISSED     {counts['MISSED']:>4}   a note matches the declaration and is NOT")
        print(f"  NO MATCH   {counts['NO MATCH']:>4}   nothing in this filing matches; nothing to pass")
        print(f"  UNDECLARED {counts['UNDECLARED']:>4}   no declaration, so the supply cannot be judged")
        hollow = [r for r in got["rows"] if r["named_but_not_carried"]]
        print(f"  and {len(hollow)} line(s) NAME a note whose text never travels — the model is "
              f"pointed at a note it cannot read")
        print(f"  -> of the {graded} lines that CAN be judged, {share} were given AT LEAST ONE "
              f"declared note")
        # AND THE PAIR METRIC, which is the one that matches `calibrate_line_item_notes`. A line
        # whose author declared six notes and was given one passes the line test above and has
        # lost five sixths of its evidence; only this shows that.
        pd = sum(r["pairs_declared"] for r in got["rows"])
        ps = sum(r["pairs_supplied"] for r in got["rows"])
        print(f"  -> of the {pd} (line, declared note) PAIRS, {ps} reached the model"
              + (f" — {ps / pd:.0%}" if pd else ""))
        if args.verbose:
            for r in got["rows"]:
                if r["verdict"] == "MISSED":
                    print(f"     MISSED {r['key']}\n"
                          f"        declared {r['declared']}\n"
                          f"        supplied {r['supplied']}")

    graded = total["SUPPLIED"] + total["MISSED"]
    print(f"\n{'=' * 96}\nALL FILINGS  SUPPLIED={total['SUPPLIED']} MISSED={total['MISSED']} "
          f"NO MATCH={total['NO MATCH']} UNDECLARED={total['UNDECLARED']}")
    if graded:
        print(f"  at least one declared note reached the model on {total['SUPPLIED'] / graded:.1%} "
              f"of the {graded} gradeable line-filing pairs")
    if total["pairs_declared"]:
        print(f"  of the {total['pairs_declared']} (line, declared note) pairs overall, "
              f"{total['pairs_supplied']} reached it — "
              f"{total['pairs_supplied'] / total['pairs_declared']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
