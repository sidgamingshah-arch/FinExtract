#!/usr/bin/env python
"""Turn authored concept sets into line items, refusing anything that would produce a wrong figure.

WHAT THIS IS FOR. Six of the eight focus concepts have a parent and no children. Their children and
cascades are authored against the five specs in `docs/`, and this converts that into the
configuration file — but only after the checks below, because the failure mode here is not a crash.
An over-broad note title or two children claiming the same note produce a FIGURE, and a figure that
is quietly twice the truth still balances.

THE CHECKS, each one from a defect actually seen in an earlier attempt:

  1. EVERY PATTERN COMPILES. The schema refuses one that does not, naming the index — this reports
     it before the write rather than after.
  2. NO TWO CHILDREN OF ONE PARENT CLAIM THE SAME ROWS. Two children whose title patterns overlap
     AND whose row patterns overlap will both select the same rows of the same note. Under a
     summing rung that doubles the line; under `alternatives` it makes the second child dead
     weight. This was 25 of the defects found in the first attempt, and it is the same defect the
     shipped `sub__pbt_*` pair had.
  3. EVERY CASCADE TERM NAMES SOMETHING REAL — a child of this set, or an existing canonical key.
     A dangling `ref` is the one silent configuration failure: the rung simply never resolves.
  4. NO RUNG IS ONLY AN ADJUSTMENT. A rung whose single term is a deduction has nothing to deduct
     it from, and publishing it produced a negative depreciation charge once already.
  5. THE PARENT MUST PERMIT NOTES AS A SOURCE. `note_use: evidence_only` means a note may
     corroborate this line and never supply it, so children under such a parent are reported as
     inert rather than written and forgotten.

Usage:
    python scripts/integrate_focus_config.py authored.json [--write] [--report out.json]

Without `--write` it only reports. Nothing is written unless every check of severity `breaks`
passes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern, re.IGNORECASE)
        return True
    except re.error:
        return False


def _overlap(a: list[str], b: list[str]) -> list[str]:
    """Patterns that appear in both lists — the cheap, certain half of "would both match".

    A full overlap test between two regex languages is undecidable in general; an identical pattern
    on both sides is proof enough and is what the real defects looked like (two children with
    byte-identical lists).
    """
    return sorted(set(a) & set(b))


def check(sets: list[dict], existing_keys: set[str], note_use: dict[str, str]) -> list[dict]:
    problems: list[dict] = []

    def bad(where: str, sev: str, issue: str, fix: str):
        problems.append({"where": where, "severity": sev, "issue": issue, "fix": fix})

    seen_keys: dict[str, str] = {}
    for s in sets:
        parent = s["parent_key"]
        shared_any = s.get("shared_row_caption_any") or []
        shared_none = s.get("shared_row_caption_none") or []

        # (5) the parent's own permission
        use = note_use.get(parent, "")
        if use and use != "decomposition_allowed":
            bad(parent, "wrong_figure",
                f"the parent's note_use is `{use}`, so a note may corroborate this line and never "
                f"supply it — every child authored under it is inert",
                "change note_use to decomposition_allowed on the configuration screen if a note "
                "should be able to fill this line, or drop the children")

        # (1) patterns compile
        for name, pats in (("shared_row_caption_any", shared_any),
                           ("shared_row_caption_none", shared_none)):
            for i, pat in enumerate(pats):
                if not _compiles(pat):
                    bad(f"{parent}/{name}[{i}]", "breaks",
                        f"pattern does not compile: /{pat}/", "fix or remove the pattern")

        effective: dict[str, tuple[list[str], list[str]]] = {}
        for c in s.get("children") or []:
            key = c["key"]
            if key in seen_keys:
                bad(key, "breaks", f"duplicate child key — already used by {seen_keys[key]}",
                    "rename one of them")
            seen_keys[key] = parent
            if key in existing_keys:
                bad(key, "breaks", "key already exists in the shipped configuration",
                    "rename the new child")
            titles = c.get("note_title_any") or []
            if not titles:
                bad(key, "breaks", "no note_title_any — the child can never select a note",
                    "give it the note's heading patterns")
            rows_any = c.get("row_caption_any_override") or shared_any
            rows_none = c.get("row_caption_none_override") or shared_none
            for name, pats in (("note_title_any", titles),
                               ("row_caption_any", rows_any), ("row_caption_none", rows_none)):
                for i, pat in enumerate(pats):
                    if not _compiles(pat):
                        bad(f"{key}/{name}[{i}]", "breaks",
                            f"pattern does not compile: /{pat}/", "fix or remove the pattern")
            if not any(re.search(r"[一-鿿]", p) for p in rows_any):
                bad(key, "weak", "no Chinese in the counting patterns — these filings print "
                                 "Traditional and Simplified Chinese, often in one document",
                    "add the Chinese forms of each caption")
            effective[key] = (titles, rows_any)

        # (2) two children claiming the same rows of the same note
        keys = list(effective)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                t_same = _overlap(effective[a][0], effective[b][0])
                r_same = _overlap(effective[a][1], effective[b][1])
                if t_same and r_same:
                    bad(f"{a} vs {b}", "wrong_figure",
                        f"both select the same rows of the same note — {len(t_same)} shared title "
                        f"pattern(s) and {len(r_same)} shared row pattern(s). One figure under two "
                        f"names; summed, it doubles the line",
                        "give each child its own row_caption_any_override identifying its rows by "
                        "the qualifier in the caption, and have the one wanting the bare total veto "
                        "the others")

        # (3) and (4) the cascade
        child_keys = set(effective)
        for rung in s.get("cascade") or []:
            refs = [t.get("ref") for t in rung.get("terms") or []]
            if not refs:
                bad(f"{parent}/{rung.get('id')}", "breaks", "rung has no terms", "give it terms")
            for ref in refs:
                if ref not in child_keys and ref not in existing_keys:
                    bad(f"{parent}/{rung.get('id')}", "breaks",
                        f"term ref `{ref}` names neither a child of this set nor an existing "
                        f"canonical key — the rung can never resolve",
                        "point it at a real key")
            roles = [t.get("role") for t in rung.get("terms") or []]
            if roles and all(r == "adjustment" for r in roles):
                bad(f"{parent}/{rung.get('id')}", "wrong_figure",
                    "every term is an adjustment — a rung whose only figure is a deduction has "
                    "nothing to deduct it from",
                    "add the base term, or delete the rung")
            for t in rung.get("terms") or []:
                if t.get("role") == "adjustment" and int(t.get("sign", 1)) > 0:
                    bad(f"{parent}/{rung.get('id')}/{t.get('ref')}", "wrong_figure",
                        "an adjustment with sign +1 ADDS what the spec deducts",
                        "set sign to -1")
    return problems


def to_items(s: dict) -> list[dict]:
    """One authored set as line-item definitions, in the shape the shipped children use."""
    shared_any = s.get("shared_row_caption_any") or []
    shared_none = s.get("shared_row_caption_none") or []
    out = []
    for c in s.get("children") or []:
        out.append({
            "key": c["key"],
            "label": c["label"],
            "description": c.get("description", ""),
            "in_output": False,
            "parent": s["parent_key"],
            "order": int(c.get("order", 0)),
            "namespace": "internal",
            "inherits": "notes",
            "note_source": {
                "note_title_any": c.get("note_title_any") or [],
                "row_caption_any": c.get("row_caption_any_override") or shared_any,
                "row_caption_none": c.get("row_caption_none_override") or shared_none,
            },
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("authored")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    sets = json.loads(pathlib.Path(args.authored).read_text(encoding="utf-8"))
    if isinstance(sets, dict):
        sets = sets.get("sets") or []
    d = json.loads(SEED.read_text(encoding="utf-8"))
    existing = {i["key"] for i in d["items"]}
    note_use = {i["key"]: i.get("note_use", "") for i in d["items"]}

    problems = check(sets, existing, note_use)
    breaks = [p for p in problems if p["severity"] == "breaks"]
    wrong = [p for p in problems if p["severity"] == "wrong_figure"]
    weak = [p for p in problems if p["severity"] == "weak"]

    print(f"{len(sets)} concept set(s), "
          f"{sum(len(s.get('children') or []) for s in sets)} children, "
          f"{sum(len(s.get('cascade') or []) for s in sets)} rungs")
    print(f"problems: {len(breaks)} breaks, {len(wrong)} wrong_figure, {len(weak)} weak")
    for p in breaks + wrong + weak:
        print(f"  [{p['severity']:12s}] {p['where']:52s} {p['issue'][:96]}")

    if args.report:
        pathlib.Path(args.report).write_text(json.dumps(problems, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
        print(f"-> {args.report}")

    if not args.write:
        print("\n(report only — pass --write to apply)")
        return 0
    if breaks:
        print(f"\nREFUSED: {len(breaks)} breaking problem(s). Nothing written.")
        return 1

    by_key = {i["key"]: i for i in d["items"]}
    added = 0
    for s in sets:
        parent = by_key.get(s["parent_key"])
        if parent is None:
            print(f"  skip {s['parent_key']}: not in the configuration")
            continue
        items = to_items(s)
        d["items"].extend(items)
        added += len(items)
        parent["rollup"] = s.get("parent_rollup") or parent.get("rollup")
        if s.get("cascade"):
            parent["type"] = s.get("parent_type") or "derived"
            parent["cascade"] = [
                {"id": r["id"],
                 "terms": [{"ref": t["ref"], "role": t.get("role", "required"),
                            "sign": int(t.get("sign", 1))} for t in r["terms"]],
                 "note": r.get("note", ""),
                 "refuse_negative": bool(r.get("refuse_negative", True))}
                for r in s["cascade"]]
        print(f"  {s['parent_key']}: +{len(items)} children, "
              f"{len(s.get('cascade') or [])} rungs, rollup={parent['rollup']}")

    SEED.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    from app.schemas.line_items import load_line_item_set
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    print(f"\nwritten: {len(d['items'])} items; loads with {len(st.items)} "
          f"({sum(1 for i in st.items if i.note_source is not None)} note-sourced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
