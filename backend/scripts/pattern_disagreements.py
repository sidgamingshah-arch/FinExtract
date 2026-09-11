#!/usr/bin/env python
"""WHICH PATTERN fired, on WHAT heading, and is the pattern wrong or the terms wrong?

`why_zero_score.py` reports that a line's `note_title_any` claims a note its own `note_terms` score
0.000 against. That is a DISAGREEMENT, not a verdict: either the pattern is too broad (it claimed a
note the line has nothing to do with) or the terms are missing the vocabulary (the note IS the
line's and nothing says so). Fixing the wrong one of those makes things worse, so this names the
exact pattern that matched and puts the evidence for both readings side by side.

WHAT DECIDES IT, and none of it is taste:

  * CAN THE NOTE HOLD A FIGURE AT ALL? A note with no extracted table rows and no amount in its
    text cannot be any line's source. In a CAS filing the accounting-POLICY chapter is full of
    these — "基础" (basis of preparation), "记账本位币" (functional currency), "会计处理方法"
    (accounting treatment) — prose that NAMES a concept without disclosing a number for it. A
    pattern claiming one of those is wrong whatever the line.
  * IS THE HEADING A HEADING? A unit whose title starts mid-sentence or mid-parenthesis is a
    continuation fragment, not a note title. Matching one is an artefact of extraction, not an
    authoring decision.
  * DOES THE PATTERN NAME THE SUBJECT OR MERELY A WORD IN IT? Printed beside the heading, this is
    the judgement a reviewer can make in a second and a script cannot make at all — so the report
    shows the pattern SOURCE, not a verdict.

    python scripts/pattern_disagreements.py ../_run8/suncreate.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

# An amount as a CAS statement prints one: grouped thousands, optionally bracketed/negative.
AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}\b")
# A title that begins inside a sentence: a stray closing bracket, a delimiter with no enumerator,
# or a continuation particle. These are extraction fragments rather than headings.
FRAGMENT = re.compile(r"^\s*[）)】」』]|^\s*[、．,.]\s*[^\s]|^\s*(?:其中|加|减|附注|注)")


def _notes_of(pdf: pathlib.Path):
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
        run_extraction(pdf.read_bytes(), filename=pdf.name,
                       ontology=build_working_view(cfg), template=None, line_items=cfg)
    finally:
        PruneNotesStage.run = original
    return cfg, snap


def report(pdf: pathlib.Path) -> None:
    from app.services.line_item_notes import header_pool, note_probe, notes_for_line_item
    from app.services.note_context import subject_tokens

    cfg, notes = _notes_of(pdf)
    pool = header_pool(notes)
    by_key = {i.key: i for i in cfg.items}

    # Every extracted unit by note number, so a claimed note can be examined: does it carry rows,
    # does its text carry an amount, is its title a fragment?
    units: dict[str, list] = {}
    for t in notes:
        units.setdefault(str(getattr(t, "note_number", "") or ""), []).append(t)

    def _capacity(number: str) -> tuple[int, bool, bool]:
        """(rows, any amount in the text, every title is a fragment) for a note number."""
        group = units.get(number, [])
        rows = sum(len(getattr(t, "items", None) or ()) for t in group)
        text = " ".join((getattr(t, "source_text", "") or "") for t in group)
        has_amount = bool(AMOUNT.search(text))
        titles = [(getattr(t, "title", "") or "") for t in group]
        all_frag = bool(titles) and all(FRAGMENT.match(x) for x in titles)
        return rows, has_amount, all_frag

    print("=" * 100)
    print(f"{pdf.name}: pattern-vs-terms disagreements, with the pattern that fired")
    print("=" * 100)

    buckets: dict[str, list] = {}
    for item in cfg.items:
        source = getattr(item, "note_source", None)
        raws = list(getattr(source, "note_title_any", None) or ())
        if not raws:
            continue
        compiled = []
        for raw in raws:
            try:
                compiled.append((raw, re.compile(raw, re.IGNORECASE)))
            except re.error:
                continue
        if not compiled:
            continue
        parent = by_key.get(getattr(item, "parent", "") or "")
        ranked = notes_for_line_item(item, pool, min_score=0.0, cap=10_000, parent=parent)
        score_of = {h.note: h.score for h in ranked}
        terms = list(getattr(source, "note_terms", None) or ())
        probe = note_probe(item, parent)

        for table in notes:
            title = getattr(table, "title", "") or ""
            number = str(getattr(table, "note_number", "") or "")
            fired = [raw for raw, pat in compiled if pat.search(title)]
            if not fired:
                continue
            if score_of.get(number, 0.0) > 0.0:
                continue                              # pattern and terms agree; not our subject
            rows, has_amount, _frag = _capacity(number)
            is_frag = bool(FRAGMENT.match(title))
            if rows == 0 and not has_amount:
                bucket = "A  the note can hold NO figure (no rows, no amount) -> the PATTERN is wrong"
            elif is_frag:
                bucket = "B  the title is a continuation FRAGMENT -> extraction artefact"
            elif rows == 0:
                bucket = "C  no rows but an amount in the text -> prose only, judge by hand"
            else:
                bucket = "D  the note HAS rows -> the pattern may be right and the TERMS thin"
            buckets.setdefault(bucket, []).append(
                (item.key, number, title, fired[0], rows, has_amount, terms, probe))

    total = sum(len(v) for v in buckets.values())
    print(f"\n  {total} disagreements\n")
    for bucket in sorted(buckets):
        rows = buckets[bucket]
        print(f"  {'-' * 96}")
        print(f"  {len(rows):>4}  {bucket}")
        print(f"  {'-' * 96}")
        for key, number, title, pat, nrows, amt, terms, _probe in rows[:10]:
            print(f"    {key}")
            print(f"       note {number:<9s} rows={nrows:<4d} amount={'Y' if amt else 'N'}"
                  f"  title {title[:56]!r}")
            print(f"       fired {pat!r}")
            if terms:
                print(f"       note_terms {terms[:6]}")
            else:
                print(f"       note_terms  (none — the probe is blended prose)")
        if len(rows) > 10:
            print(f"    … and {len(rows) - 10} more")
        print()

    # The patterns responsible, ranked — this is the edit list.
    blame: dict[tuple[str, str], int] = {}
    for bucket, rows in buckets.items():
        if not bucket.startswith(("A", "B")):
            continue
        for key, _n, _t, pat, *_ in rows:
            blame[(key, pat)] = blame.get((key, pat), 0) + 1
    print("=" * 100)
    print("  THE EDIT LIST — patterns claiming notes that can hold no figure, worst first")
    print("=" * 100)
    for (key, pat), n in sorted(blame.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3d} bad claims   {key}")
        print(f"                  {pat!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    args = ap.parse_args()
    for raw in args.pdfs:
        pdf = pathlib.Path(raw).resolve()
        if pdf.exists():
            report(pdf)
        else:
            print(raw, "NOT FOUND")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
