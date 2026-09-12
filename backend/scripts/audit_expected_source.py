#!/usr/bin/env python
"""DOES THE NOTE WE EXPECT THE FIGURE TO BE IN ACTUALLY REACH THE MODEL? One question, per line.

THE QUESTION, AND WHY IT IS NOT THE ONE `audit_request_context.py` ASKS. That script asks whether
the payload contained ANYTHING the run would accept — a row whose caption clears the gate. This one
asks the stricter and more useful thing: for the note the CONFIGURATION SAYS this line's figure is
printed in, is that note in the request?

    audit_request_context  ->  "could this payload have produced an answer at all?"
    this script            ->  "did the payload contain the source we expect the answer to be in?"

A payload can pass the first and fail the second, and that combination is the worst case there is:
the model is handed a note that looks answerable and never shown the note the figure is actually
in, so a confident wrong answer is the likely outcome rather than an empty one.

WHERE "EXPECTED" COMES FROM, and it is not my opinion. `note_source.note_title_any` is the author's
own statement of which note holds this line — a regex over the note HEADING, written per line, and
the same field `note_context.identified_notes` compiles to decide what to pass unconditionally. So
the expectation is read out of the shipped configuration, not guessed:

    EXPECTED    the notes in THIS filing whose heading matches the line's own `note_title_any`
    DELIVERED   the notes the request actually carries (`build_request`)

and the verdict is whether those two intersect.

  DELIVERED       an expected note is in the payload. The model was shown where to look.
  MISSING         the filing HAS a note matching the line's pattern and the request did NOT carry
                  it. This is the finding: the evidence exists, was identified, and did not travel.
  ABSENT          no note in this filing matches the line's pattern, so there is nothing to
                  deliver. Either the filing genuinely lacks the disclosure or the pattern does not
                  fit this filing's wording — `--why` separates those by showing near misses.
  UNDECLARED      the line declares no `note_title_any`, so the configuration states no expectation
                  and this script has nothing to check. Reported, never counted as a pass.

    python scripts/audit_expected_source.py
    python scripts/audit_expected_source.py --why
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

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
HAN_ENUM = re.compile(r"^[一二三四五六七八九十百]+\s*[、．]")


def dumps() -> list[dict]:
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
    from app.core.models.line_item import NoteItem, NotesTable

    out = []
    for note in data["notes"]:
        number = str(note.get("note") or note.get("number") or "")
        table = NotesTable(note_number=number, title=str(note.get("title") or ""),
                           source_text=str(note.get("prose") or ""))
        for row in (note.get("rows") or ()):
            caption = row if isinstance(row, str) else str(row.get("caption") or "")
            if caption:
                table.items.append(NoteItem(raw_label=caption, note_number=number))
        out.append(table)
    return out


def patterns_of(item) -> list:
    """The line's own `note_title_any`, compiled. An uncompilable pattern is skipped, not fatal —
    `note_sourced.bad_patterns` is where an author is told about it."""
    source = getattr(item, "note_source", None)
    out = []
    for raw in (getattr(source, "note_title_any", None) or ()):
        try:
            out.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    return out


def expected_notes(item, tables) -> list[str]:
    """The note numbers in THIS filing whose heading (or number) the line's pattern matches.

    Heading OR number, because that is exactly what `identified_notes` matches on — anything
    narrower here would report a note as unexpected that the run itself treats as declared.
    """
    from app.services.note_context import matches_title

    pats = patterns_of(item)
    if not pats:
        return []
    got: list[str] = []
    for table in tables:
        title = getattr(table, "title", "") or ""
        number = str(getattr(table, "note_number", "") or "")
        # Through the same matcher `identified_notes` uses, or this script would report an
        # expectation the run does not share — in either direction.
        if any(matches_title(p, title) or p.search(number) for p in pats):
            if number not in got:
                got.append(number)
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--why", action="store_true",
                    help="for each MISSING, show what the request carried instead")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services import line_item_llm, line_item_requests
    from app.services.line_item_notes import content_terms

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    settings.extraction.llm_request_grouping = "none"
    settings.extraction.llm_focus_only = False

    # The 77 that declare what to look for — the same denominator the context audit settled on.
    items = [i for i in cfg.items if line_item_requests.asked_about(i) and content_terms(i)]
    files = dumps()

    print("=" * 118)
    print(f"EXPECTED-SOURCE AUDIT — {len(files)} filings x {len(items)} lines, no provider")
    print("=" * 118)
    print("\n  For the note the CONFIGURATION says this line's figure is in, did the request carry"
          " it?\n")
    print(f"  {'filing':<30s} {'reg':>3s} {'delivered':>10s} {'missing':>8s} {'absent':>7s} "
          f"{'undeclared':>11s}   worst misses")

    tally = collections.Counter()
    per_line = collections.defaultdict(collections.Counter)
    misses: list[tuple] = []

    for data in files:
        tables = tables_of(data)
        plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(cfg, tables, settings)
        seen = collections.Counter()
        local: list[str] = []
        for item in items:
            want = expected_notes(item, tables)
            if not patterns_of(item):
                verdict = "UNDECLARED"
            elif not want:
                verdict = "ABSENT"
            else:
                plan = next((p for p in plans if item.key in p.keys), None)
                carried: set[str] = set()
                if plan is not None:
                    request = line_item_llm.build_request(plan, by_key, notes_of, identified)
                    carried = {str(n.get("note", "")) for n in (request.get("notes") or [])}
                hit = [n for n in want if n in carried]
                verdict = "DELIVERED" if hit else "MISSING"
                if not hit:
                    misses.append((data["_file"], item.key, want, sorted(carried)))
                    if len(local) < 3:
                        local.append(item.key.replace("sub__", "")[:22])
            seen[verdict] += 1
            tally[verdict] += 1
            per_line[item.key][verdict] += 1
        print(f"  {data['_file'][:30]:<30s} {'CAS' if data['_cas'] else 'HK':>3s} "
              f"{seen['DELIVERED']:>10d} {seen['MISSING']:>8d} {seen['ABSENT']:>7d} "
              f"{seen['UNDECLARED']:>11d}   {', '.join(local)}")

    total = sum(tally.values())
    checkable = tally["DELIVERED"] + tally["MISSING"]
    print("\n" + "=" * 118)
    print("  THE ANSWER")
    print("=" * 118)
    print(f"\n  {total} (filing, line) pairs over {len(items)} lines and {len(files)} filings.\n")
    print(f"    DELIVERED   {tally['DELIVERED']:>5d}   the expected note WAS in the request")
    print(f"    MISSING     {tally['MISSING']:>5d}   the filing has it and the request did NOT "
          f"carry it")
    print(f"    ABSENT      {tally['ABSENT']:>5d}   no note in the filing matches the line's "
          f"pattern")
    print(f"    UNDECLARED  {tally['UNDECLARED']:>5d}   the line declares no expected note")
    if checkable:
        print(f"\n  OF THE {checkable} PAIRS WHERE AN EXPECTATION COULD BE CHECKED — the filing has "
              f"the note and")
        print(f"  the configuration names it — THE MODEL WAS SHOWN IT IN "
              f"{tally['DELIVERED']} ({100.0 * tally['DELIVERED'] / checkable:.0f}%).")
        print(f"  It was withheld in {tally['MISSING']} "
              f"({100.0 * tally['MISSING'] / checkable:.0f}%).")
    print(f"\n  ABSENT is not a delivery failure and is not counted above: there is nothing to "
          f"deliver.")
    print("  It is either a filing that does not make the disclosure or a pattern that does not fit")
    print("  its wording — two different pieces of work, and `--why` is where they separate.")

    print("\n" + "=" * 118)
    print("  LINES WHOSE EXPECTED NOTE IS MOST OFTEN WITHHELD")
    print("=" * 118)
    ranked = sorted(per_line.items(), key=lambda kv: -kv[1]["MISSING"])
    print(f"\n  {'line':<44s} {'delivered':>10s} {'missing':>8s} {'absent':>7s}")
    for key, counts in ranked[:14]:
        if not counts["MISSING"]:
            break
        print(f"  {key.replace('sub__', '')[:44]:<44s} {counts['DELIVERED']:>10d} "
              f"{counts['MISSING']:>8d} {counts['ABSENT']:>7d}")

    if args.why:
        print("\n" + "=" * 118)
        print("  WHAT THE REQUEST CARRIED INSTEAD (first 30 misses)")
        print("=" * 118)
        for name, key, want, carried in misses[:30]:
            print(f"\n  {name[:30]} · {key}")
            print(f"      expected note(s): {want}")
            print(f"      request carried : {carried or '(nothing)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
