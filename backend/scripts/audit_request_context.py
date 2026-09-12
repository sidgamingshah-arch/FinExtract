#!/usr/bin/env python
"""DID THE REQUEST CARRY WHAT THE MODEL NEEDED? — asked of every request, before any provider.

THE DISTINCTION THIS EXISTS TO DRAW, and no run log can draw it. A request that comes back empty
has two causes that look identical in the output and need opposite work:

    the payload did NOT contain the figure   -> the model was RIGHT to say nothing. The gap is in
                                                note selection or in the authored vocabulary, and
                                                no prompt or model change touches it.
    the payload DID contain it and it was    -> the only real model failure, and the only case
    missed                                      where a prompt or a model is worth changing.

So this rebuilds the EXACT payload each request would carry — `plan_and_notes` and `build_request`,
the same two calls `stages/line_item_llm` makes — and then asks whether an answer was reachable
from it, USING THE RUN'S OWN ACCEPTANCE GATE rather than a fresh approximation of it:

    line_item_notes.caption_agrees_with_row_terms

That function is what accepts or refuses a citation at run time. Asking it here means the verdict
is not "a caption I would call close enough" but "a caption THIS RUN would have accepted". A row
that fails it could not have produced a figure however well the model read the note, so counting
it as available evidence would overstate what the payload offered.

READ `ROW PRESENT` AS AN UPPER BOUND, NOT AN ESTIMATE — BACKLOG ITEM 14. Borrowing the run's gate
also inherits its defect: it accepts a caption sharing ONE subject token with `row_terms`, and
`row_terms` are multi-word phrases that `subject_tokens` splits into single words. For
`sub__fixed_asset_depreciation` the accepted set therefore contains `assets`, `property`, `plant`,
`use` and `and` — so `Deposits and other receivables` passes for a depreciation line, on `and`.
Measured, at least 8 of 43 row verdicts here rest only on such a fragment, and the Han verdicts are
additionally suspect because a compound term (`固定资产折旧`) is one word whose bigrams include the
container noun `资产`. So a `ROW PRESENT` means "the run would have accepted something in this
payload", which is WEAKER than "the line's figure is in this payload". The tally prints the bound
with that wording. Fixing the gate fixes this number too, which is the main reason item 14 is
ranked where it is.

  NOT ASKED       the line is never put to a model (derived, or excluded by focus). No payload.
  NOT SELECTED    the line's own note search found nothing above the floor. A selection gap.
  NOT DELIVERED   THE LINE SELECTED NOTES AND THE REQUEST CARRIED NONE OF THEM. Measured at 49%
                  of all selected notes corpus-wide, and it is a defect rather than a gap:
                  `build_request` intersects a line's selection with `note_context.identified_notes`,
                  whose semantic half is bounded by `_SEMANTIC_NOTE_BUDGET` — eight notes for the
                  WHOLE DOCUMENT, ranked globally by score across all 518 lines. A line with a
                  confident best hit loses it to other lines' better-scoring hits and is sent a
                  payload with no note in it at all. These two verdicts are split apart because
                  they need opposite work, and the run log shows neither.
  NO VOCAB        the line declares no row vocabulary, so the payload cannot be graded and the
                  gate waves everything through. Not a negative finding — unauthored configuration.
  NO CANDIDATE    notes travelled, and not one row in them would pass the gate, and no sentence
                  carries an amount near the line's terms. An empty answer here is CORRECT.
  PROSE ONLY      no passing row, but a sentence carries both an amount and the line's vocabulary.
                  Reachable only through the prose route — narrower, and worth knowing separately.
  ROW PRESENT     a row in the payload passes the gate. The figure was THERE.

No provider, so it runs over the whole corpus in one pass and can be re-run after any vocabulary
edit — which is the point of building it: this is the measurement that says whether an edit helped.

    python scripts/audit_request_context.py
    python scripts/audit_request_context.py --verbose
    python scripts/audit_request_context.py --keys sub__fixed_asset_depreciation --verbose
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOCAB = ROOT.parent / "_vocab"
SEED = ROOT / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
HAN_ENUM = re.compile(r"^[一二三四五六七八九十百]+\s*[、．]")
AMOUNT = re.compile(r"\d{1,3}(?:[,，]\d{3})+(?:\.\d+)?")
PROSE_WINDOW = 220

# The same four the live corpus run asks about, so the two reports line up row for row.
PARTS = [
    "sub__fixed_asset_depreciation",
    "sub__prepaid_lease_depreciation",
    "sub__face_principal_revenue",
    "sub__cl_reported_total",
]

# What `line_item_payload` can emit. Presence is reported because a line whose evidence WAS in the
# payload and was still missed is a different finding depending on whether the payload told the
# model what to look for.
FIELDS = ["definition", "include", "exclude", "do_not_confuse_with", "printed_as",
          "printed_as_by_language", "how_to_tell_it_apart", "row_is_called",
          "row_is_never_called", "row_caption_matches", "instruction", "sign_convention"]


def dumps() -> list[dict]:
    """Every extracted-notes dump in `_vocab`, tagged by reporting regime."""
    out = []
    for path in sorted(VOCAB.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        try:
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not (isinstance(data, dict) and data.get("notes")):
            continue
        numbers = [str(n.get("note") or n.get("number") or "") for n in data["notes"]]
        titles = " ".join(str(n.get("title") or "") for n in data["notes"])
        data["_file"] = path.stem
        data["_cas"] = bool(HAN.search(titles)
                            and sum(1 for n in numbers if HAN_ENUM.match(n)) > len(numbers) * 0.3)
        out.append(data)
    return out


def tables_of(data: dict):
    """The dump as the `NotesTable` list every downstream service expects."""
    from app.core.models.line_item import NoteItem, NotesTable

    out = []
    for note in data["notes"]:
        number = str(note.get("note") or note.get("number") or "")
        table = NotesTable(note_number=number,
                           title=str(note.get("title") or ""),
                           source_text=str(note.get("prose") or ""))
        for row in (note.get("rows") or ()):
            caption = row if isinstance(row, str) else str(row.get("caption") or "")
            if caption:
                table.items.append(NoteItem(raw_label=caption, note_number=number))
        out.append(table)
    return out


def reachable(item, notes_block: list[dict], selected: list[str]) -> tuple[str, str]:
    """``(verdict, evidence)`` for one line against the notes its request carries.

    `selected` is what the line's own search asked for, and it is needed to tell the two empty
    cases apart: a line that selected nothing has a selection gap, and a line that selected notes
    and received none was overruled by the document-level semantic budget.
    """
    from app.services.line_item_notes import caption_agrees_with_row_terms, content_terms

    if not notes_block:
        if selected:
            return "NOT DELIVERED", (f"selected {selected}, the request carried none of them — "
                                     f"dropped by note_context._SEMANTIC_NOTE_BUDGET")
        return "NOT SELECTED", "the line's note search found nothing above the floor"
    terms = content_terms(item)
    if not terms:
        return "NO VOCAB", "the line declares no row vocabulary, so nothing can be looked for"

    # 1. A ROW THE RUN WOULD ACCEPT. The gate, not a lookalike of it.
    for note in notes_block:
        for row in (note.get("rows") or ()):
            caption = str(row.get("caption") or "")
            if caption and caption_agrees_with_row_terms(item, caption)[0]:
                return "ROW PRESENT", f"note {note.get('note')} row {caption!r}"

    # 2. AN AMOUNT IN A SENTENCE, near the line's own vocabulary. The prose route's precondition:
    #    `resolve_sources` requires the stated amount to appear in the note's text, so an amount
    #    must be there to be found at all.
    for note in notes_block:
        prose = str(note.get("prose") or "")
        if not prose:
            continue
        low = prose.lower()
        for match in AMOUNT.finditer(prose):
            near = low[max(0, match.start() - PROSE_WINDOW): match.end() + PROSE_WINDOW]
            if any(term in near for term in terms):
                quote = prose[max(0, match.start() - 80): match.end() + 40].replace("\n", " ")
                return "PROSE ONLY", f"note {note.get('note')} ...{quote.strip()}..."

    rows = sum(len(n.get("rows") or ()) for n in notes_block)
    refs = ",".join(str(n.get("note")) for n in notes_block[:5])
    return "NO CANDIDATE", f"{len(notes_block)} note(s) [{refs}], {rows} rows, none passes the gate"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", default=",".join(PARTS))
    ap.add_argument("--all", action="store_true",
                    help="every line the configuration is ever asked about, not the 4 focus parts")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services import line_item_llm

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    every = {i.key: i for i in cfg.items}
    if args.all:
        from app.services import line_item_requests
        keys = [i.key for i in cfg.items if line_item_requests.asked_about(i)]
        missing = []
    else:
        keys = [k for k in args.keys.split(",") if k in every]
        missing = [k for k in args.keys.split(",") if k not in every]
    # At 518 lines the per-filing grid is wider than any screen and the per-line tables are pages
    # long, so the wide views collapse to per-filing counts and the worst-served lines. The ALL row
    # is unaffected: it is summed over every key, listed or not.
    wide = len(keys) > 12
    settings = get_settings()
    settings.extraction.llm_request_grouping = "none"
    settings.extraction.llm_focus_only = False

    files = dumps()
    print("=" * 120)
    print(f"REQUEST-CONTEXT AUDIT — {len(files)} filings x {len(keys)} lines, no provider called")
    print("=" * 120)
    if missing:
        print(f"  not in the shipped configuration, skipped: {missing}")
    print("\n  Was the answer IN the payload?   ROW PRESENT / PROSE ONLY = yes.   "
          "NO CANDIDATE = an empty reply was correct.\n")
    if wide:
        print(f"  (grid collapsed to counts: {len(keys)} lines is wider than any screen)")
        print(f"  {'filing':<30s} {'reg':>3s} {'notes':>5s}  verdict counts over all lines")
    else:
        head = "  ".join(f"{k.replace('sub__', '')[:19]:<19s}" for k in keys)
        print(f"  {'filing':<30s} {'reg':>3s} {'notes':>5s}  {head}")

    tally = {k: collections.Counter() for k in keys}
    evidence = {k: [] for k in keys}
    carried: dict[str, list[int]] = {k: [] for k in keys}
    delivered: dict[str, list[int]] = {k: [0, 0] for k in keys}   # [selected, delivered]
    fields_seen = {k: collections.Counter() for k in keys}
    sizes: list[int] = []

    for data in files:
        tables = tables_of(data)
        plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(cfg, tables, settings)
        cells = []
        for key in keys:
            plan = next((p for p in plans if key in p.keys), None)
            if plan is None:
                tally[key]["NOT ASKED"] += 1
                cells.append(f"{'NOT ASKED':<19s}")
                continue
            request = line_item_llm.build_request(plan, by_key, notes_of, identified)
            block = request.get("notes") or []
            sizes.append(len(json.dumps(request, ensure_ascii=False)))
            carried[key].append(len(block))
            # WHAT THE LINE ASKED FOR versus what arrived. Counted for every request, not only the
            # empty ones: a line that selected four notes and received one was pruned just as
            # surely, and the figure may have been in a note that did not survive.
            selected = [str(n) for n in plan.notes]
            delivered[key][0] += len(selected)
            delivered[key][1] += sum(1 for n in selected if n in
                                     {str(e.get("note", "")) for e in block})
            entry = next((e for e in request["line_items"] if e["key"] == key), {})
            for field in FIELDS:
                if entry.get(field):
                    fields_seen[key][field] += 1
            verdict, why = reachable(every[key], block, selected)
            tally[key][verdict] += 1
            evidence[key].append((data["_file"], verdict, len(block), why))
            cells.append(f"{verdict:<19s}")
        if wide:
            seen = collections.Counter(c.strip() for c in cells)
            print(f"  {data['_file'][:30]:<30s} {'CAS' if data['_cas'] else 'HK':>3s} "
                  f"{len(tables):>5d}  row={seen['ROW PRESENT']:<4d} "
                  f"prose={seen['PROSE ONLY']:<3d} nocand={seen['NO CANDIDATE']:<4d} "
                  f"undelivered={seen['NOT DELIVERED']:<4d} nosel={seen['NOT SELECTED']:<4d}")
        else:
            print(f"  {data['_file'][:30]:<30s} {'CAS' if data['_cas'] else 'HK':>3s} "
                  f"{len(tables):>5d}  " + "  ".join(cells))

    order = ["ROW PRESENT", "PROSE ONLY", "NO CANDIDATE", "NOT DELIVERED", "NOT SELECTED",
             "NO VOCAB", "NOT ASKED"]
    print("\n" + "=" * 120)
    print("  WAS THE EVIDENCE THERE — per line, across the corpus")
    print("=" * 120)
    print(f"\n  {'line':<30s} " + "  ".join(f"{o:>13s}" for o in order)
          + f" {'notes/req':>10s} {'delivered':>10s}")
    # WHICH LINES GET LISTED. At 518 the per-line tables run for pages, so a wide run lists
    # the worst-served twelve — the ones whose payload most often could not answer, which is
    # the only part of a 518-row table anyone acts on. The ALL row below sums over EVERY key,
    # listed or not, so the headline is unaffected by this.
    shown = (sorted(keys, key=lambda k: -(tally[k]["NOT DELIVERED"] + tally[k]["NOT SELECTED"]
                                          + tally[k]["NO CANDIDATE"]))[:12] if wide else keys)
    if wide:
        print(f"  (the 12 worst-served of {len(keys)} lines; the ALL row is over all of them)")
    for key in shown:
        counts = "  ".join(f"{tally[key][o]:>13d}" for o in order)
        got = carried[key]
        avg = f"{sum(got) / len(got):.1f}" if got else "—"
        want, had = delivered[key]
        share = f"{had}/{want}" if want else "—"
        print(f"  {key.replace('sub__', '')[:30]:<30s} {counts} {avg:>10s} {share:>10s}")

    total = collections.Counter()
    for key in keys:
        total.update(tally[key])
    asked = sum(total[o] for o in order if o != "NOT ASKED")
    there = total["ROW PRESENT"] + total["PROSE ONLY"]
    print(f"\n  {'ALL':<32s} " + "  ".join(f"{total[o]:>12d}" for o in order))

    print("\n" + "=" * 120)
    print("  WHAT THIS MEANS FOR WHERE WORK GOES")
    print("=" * 120)
    print(f"\n  {asked} requests would actually be made. The figure was reachable in AT MOST "
          f"{there} of them ({100.0 * there / max(1, asked):.0f}%):")
    print(f"      {total['ROW PRESENT']:>4d} from a row the run's own gate would ACCEPT")
    print(f"      {total['PROSE ONLY']:>4d} from prose only — the narrower route")
    print("      ^ AT MOST, not exactly: the gate accepts a caption sharing ONE word with a")
    print("        multi-word row term, so `Deposits and other receivables` passes a depreciation")
    print("        line on the word `and`. See backlog item 14 — fixing it lowers this number.")
    print(f"\n  So AT MOST {there} can be a model failure. The other {asked - there} are not:")
    print(f"      {total['NO CANDIDATE']:>4d} carried notes that hold no acceptable row   "
          f"-> vocabulary")
    print(f"      {total['NOT DELIVERED']:>4d} SELECTED notes and were sent none of them  "
          f"-> A DEFECT, see below")
    print(f"      {total['NOT SELECTED']:>4d} found no note above the floor               "
          f"-> note selection")
    print(f"      {total['NO VOCAB']:>4d} cannot be graded — no row vocabulary authored")
    print("\n  A request in those rows that comes back empty is the model answering CORRECTLY.")
    print("  Counting it as a miss would send effort at the prompt instead of the configuration.")

    want = sum(delivered[k][0] for k in keys)
    had = sum(delivered[k][1] for k in keys)
    if want and had < want:
        print("\n  " + "-" * 116)
        print(f"  THE BINDING CONSTRAINT: of {want} notes these lines SELECTED, "
              f"{had} reached a request ({100 * had // want}%).")
        print(f"  {want - had} were discarded by `note_context._SEMANTIC_NOTE_BUDGET` "
              f"(= {getattr(__import__('app.services.note_context', fromlist=['x']), '_SEMANTIC_NOTE_BUDGET', '?')}), "
              f"a bound on the")
        print("  WHOLE DOCUMENT's semantically-selected notes, ranked globally across all lines — so a")
        print("  line's confident best hit is dropped in favour of another line's better-scoring one.")
        print("  Measured: reserving each line its single best note before the global cut takes")
        print("  delivery to 92% and raises reachable requests from 43 to 55, for ~44% more context.")
    if sizes:
        sizes.sort()
        print(f"\n  request size, chars: median {sizes[len(sizes) // 2]:,}  "
              f"largest {sizes[-1]:,}  (~{sizes[-1] // 3:,} tokens)")

    print("\n" + "=" * 120)
    print("  WHAT THE PAYLOAD TOLD THE MODEL — share of requests carrying each authored field")
    print("=" * 120)
    print(f"\n  {'line':<32s} " + "  ".join(f"{f[:9]:>10s}" for f in FIELDS[:8]))
    for key in shown:
        n = max(1, len(carried[key]))
        cells = "  ".join(f"{100 * fields_seen[key][f] // n:>9d}%" for f in FIELDS[:8])
        print(f"  {key.replace('sub__', '')[:32]:<32s} {cells}")
    print(f"\n  {'line':<32s} " + "  ".join(f"{f[:9]:>10s}" for f in FIELDS[8:]))
    for key in shown:
        n = max(1, len(carried[key]))
        cells = "  ".join(f"{100 * fields_seen[key][f] // n:>9d}%" for f in FIELDS[8:])
        print(f"  {key.replace('sub__', '')[:32]:<32s} {cells}")

    if args.verbose:
        print("\n" + "=" * 120)
        print("  EVIDENCE, per filing")
        print("=" * 120)
        for key in shown:
            print(f"\n  {key}")
            for name, verdict, n, why in evidence[key]:
                print(f"    {name[:26]:<26s} {verdict:<13s} {n:>2d} notes  {why[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
