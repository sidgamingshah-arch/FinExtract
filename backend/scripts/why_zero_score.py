#!/usr/bin/env python
"""WHY an authored note scores 0.000 against its own line item — the cause, per pair.

THE QUESTION THIS ANSWERS. `calibrate_line_item_notes.py` measures RECALL against the authored
regexes as ground truth, and on the PRC filing 46 of 125 (line item, authored note) pairs score
exactly 0.000 — not "low", zero. A zero is not a threshold problem: no `min_score` can recover a
pair that shares no weighted token with its own note's heading. So lowering the floor cannot be the
fix for those, and knowing WHICH cause applies decides what the fix is.

FOUR CAUSES ARE POSSIBLE, and they need different work:

  1. NO PROBE AT ALL — the line authors no `note_terms`, so `note_probe` falls back to
     `_blended(item, parent)`: the line's own English prose. Against a Chinese heading that shares
     no token, so the score is structurally zero. FIX: author `note_terms` in the filing's script.
  2. NO UNIT TEXT — the note carries a number but no title, so `header_pool` builds a unit whose
     text is empty. FIX: nothing in configuration; the heading was not extracted.
  3. SCRIPT MISMATCH — both sides have text, but one is Han and the other Latin. `subject_tokens`
     emits Latin words and Han character BIGRAMS, so the two token sets cannot intersect however
     well the words correspond. FIX: author the other script's terms (`aliases_i18n` already
     carries per-language vocabulary; `note_terms` does not).
  4. IDF ZERO — the tokens DO intersect, but every shared token appears in so many of this
     filing's headings that its weight is zero. FIX: more specific terms.

Cause 1 and 3 are configuration work and are measurable per line. Cause 2 is an extraction defect.
Cause 4 means the terms are too generic to discriminate.

    python scripts/why_zero_score.py ../_run8/suncreate.pdf ../_run8/laisun.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def _has_han(text: str) -> bool:
    return any("㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" for ch in (text or ""))


def _notes_of(pdf: pathlib.Path):
    """The notes as the request path sees them — BEFORE `prune_notes`."""
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
    """The notes this line's own `note_title_any` patterns match — the ground truth."""
    import re

    source = getattr(item, "note_source", None)
    pats = []
    for raw in (getattr(source, "note_title_any", None) or ()):
        try:
            pats.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    if not pats:
        return set()
    out = set()
    for table in notes:
        title = (getattr(table, "title", "") or "")
        if any(p.search(title) for p in pats):
            out.add(str(getattr(table, "note_number", "") or ""))
    return out


def report(pdf: pathlib.Path) -> dict:
    from app.services.line_item_notes import header_pool, note_probe, notes_for_line_item
    from app.services.note_context import subject_tokens

    cfg, notes = _notes_of(pdf)
    pool = header_pool(notes)
    by_key = {i.key: i for i in cfg.items}
    units = {u.ref: u for u in pool.units}
    declaring = [i for i in cfg.items
                 if getattr(getattr(i, "note_source", None), "note_title_any", None)]

    print("=" * 100)
    print(f"{pdf.name}: {len(notes)} notes, {len(declaring)} line items declaring patterns")
    print("=" * 100)

    causes: dict[str, list] = {}
    total = 0
    for item in declaring:
        truth = _regex_hits(item, notes)
        if not truth:
            continue
        parent = by_key.get(getattr(item, "parent", "") or "")
        ranked = notes_for_line_item(item, pool, min_score=0.0, cap=10_000, parent=parent)
        score_of = {h.note: h.score for h in ranked}
        probe_text = note_probe(item, parent)
        probe = set(subject_tokens(probe_text))
        authored = list(getattr(getattr(item, "note_source", None), "note_terms", None) or ())
        for note in sorted(truth):
            total += 1
            if score_of.get(note, 0.0) > 0.0:
                continue
            unit = units.get(note)
            unit_text = (getattr(unit, "title", "") or "") if unit else ""
            unit_tokens = set(subject_tokens(unit_text))
            if not unit_text.strip():
                cause = "2 no unit text (the heading was not extracted)"
            elif not authored:
                cause = "1 no note_terms (probe is the line's blended English prose)"
            elif not probe:
                cause = "1 note_terms produce no tokens"
            elif not (probe & unit_tokens):
                same = _has_han(probe_text) == _has_han(unit_text)
                cause = ("4 tokens overlap nowhere, same script — the terms name something else"
                         if same else
                         "3 script mismatch (terms in one script, heading in the other)")
            else:
                cause = "5 tokens intersect but every shared token has IDF weight zero"
            causes.setdefault(cause, []).append(
                (item.key, note, unit_text[:44], ("han" if _has_han(probe_text) else "latin")
                 + "/" + ("han" if _has_han(unit_text) else "latin")))

    zero = sum(len(v) for v in causes.values())
    print(f"\n  {zero} of {total} authored pairs score EXACTLY 0.000 "
          f"({100.0 * zero / max(1, total):.0f}%) — no threshold can recover these\n")
    for cause in sorted(causes):
        rows = causes[cause]
        print(f"  {len(rows):>4}  cause {cause}")
        for key, note, title, scripts in rows[:4]:
            print(f"          note {note:>7s} [{scripts:>11s}] {title!r}")
            print(f"          {key}")
        if len(rows) > 4:
            print(f"          … and {len(rows) - 4} more")
        print()
    return {"filing": pdf.name, "total": total,
            "zero": {c: len(v) for c, v in causes.items()}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    args = ap.parse_args()
    out = []
    for raw in args.pdfs:
        pdf = pathlib.Path(raw).resolve()
        if not pdf.exists():
            print(f"{raw}: NOT FOUND")
            continue
        out.append(report(pdf))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
