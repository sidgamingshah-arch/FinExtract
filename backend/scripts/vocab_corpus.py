#!/usr/bin/env python
"""THE REAL DISCLOSURE VOCABULARY, extracted from every filing in the object store.

WHY THIS EXISTS. Every `note_source` value in the shipped set was authored against TWO filings, and
the risk that names is overfitting: a `row_caption_any` pattern that matches laisun's exact wording
and nothing else looks like configuration and behaves like a hard-coded constant. There are seven
filings in the store — two HK/English, at least three PRC/Chinese including a STAR-market A-share —
and this dumps what they actually print so a value can be judged against all of them.

WHAT IT DUMPS, per filing: every note number with its heading, and every row caption inside it,
plus the face captions of each statement. Nothing is scored or matched; this is the corpus a
human (or an agent wearing an analyst's hat) reads before proposing a pattern.

    python scripts/vocab_corpus.py --out ../_vocab

Deterministic and LLM-free — it stops after the extract/notes stages, so it is cheap enough to
re-run when a filing is added.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")
STORE = pathlib.Path(__file__).resolve().parent.parent / "_object_store"
DB = pathlib.Path(__file__).resolve().parent.parent / "finex.db"


def _filings(extra_dirs: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    """(filename, path) for every filing available, deduplicated by CONTENT so the same report
    uploaded twice and also dropped in a folder is read once.

    TWO SOURCES, because there are two ways a filing arrives. The object store holds what was
    uploaded through the app — indexed by content hash, with its original filename in `documents`.
    A folder is the cheaper path for building vocabulary: no upload, no run, no database row, just
    PDFs. Both are read, and a duplicate between them is dropped.
    """
    out: list[tuple[str, pathlib.Path]] = []
    seen: set[str] = set()

    if DB.exists():
        names: dict[str, str] = {}
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as con:
            for name, digest in con.execute("select filename, content_hash from documents"):
                names.setdefault(str(digest), str(name))   # first filename wins for a re-upload
        for digest, name in names.items():
            blob = STORE / digest
            if blob.exists() and digest not in seen:
                seen.add(digest)
                out.append((name, blob))

    import hashlib
    for folder in extra_dirs:
        if not folder.exists():
            print(f"  (no such folder: {folder})")
            continue
        for pdf in sorted(folder.rglob("*.pdf")):
            digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if digest in seen:
                print(f"  (already in the store, skipped: {pdf.name})")
                continue
            seen.add(digest)
            out.append((pdf.name, pdf))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("../_vocab"))
    ap.add_argument("--only", default="", help="substring of a filename, to do one")
    ap.add_argument("--dir", action="append", type=pathlib.Path, default=[],
                    help="a folder of PDFs to read as well as the object store; repeatable")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    view = build_working_view(cfg)
    settings = get_settings()
    settings.extraction.llm_mapping = False

    # `_filings` DIR defaults to `../_filings`, so dropping PDFs there needs no flag at all.
    dirs = list(args.dir) or [pathlib.Path(__file__).resolve().parent.parent.parent / "_filings"]
    filings = [(n, p) for n, p in _filings(dirs) if args.only in n]
    print(f"{len(filings)} filing(s)")

    index = []
    for name, blob in filings:
        stem = pathlib.Path(name).stem.replace(" ", "_")
        print(f"  {name} …", flush=True)
        # NOTES ARE SNAPSHOTTED BEFORE THE PRUNE, which runs at stage 9 and keeps only notes cited
        # from the face. The vocabulary question is what the filing PRINTS, not what survived a
        # citation check — a note nobody cited is exactly the kind a pattern is missing.
        from app.stages.prune_notes import PruneNotesStage
        seen: dict = {}
        original = PruneNotesStage.run

        def _snap(self, doc, ctx, _seen=seen, _orig=original):
            _seen.setdefault("notes", list(doc.notes))
            return _orig(self, doc, ctx)

        PruneNotesStage.run = _snap
        try:
            doc, _ctx = run_extraction(blob.read_bytes(), filename=name, ontology=view,
                                       template=None, line_items=cfg)
        finally:
            PruneNotesStage.run = original

        notes = seen.get("notes") or doc.notes
        payload = {
            "filename": name,
            "pages": len(doc.pages),
            "currency": getattr(getattr(doc, "unit_context", None), "currency", None),
            "scale": str(getattr(getattr(doc, "unit_context", None), "scale_factor", "") or ""),
            "notes": [
                {
                    "note": str(getattr(t, "note_number", "") or ""),
                    "title": (getattr(t, "title", "") or "").strip(),
                    "pages": list(getattr(t, "source_pages", None) or []),
                    "rows": [(getattr(r, "raw_label", "") or "").strip()
                             for r in (getattr(t, "items", None) or [])],
                    # The surrounding PROSE, truncated — this is where a figure stated only in a
                    # sentence lives, and `prose_any` is authored against it.
                    "prose": (getattr(t, "source_text", "") or "")[:1200],
                }
                for t in notes
            ],
            "face": [
                {"statement": str(getattr(p, "statement", "") or ""),
                 "captions": sorted({(li.source_label or "").strip() for li in doc.line_items
                                     if getattr(li, "page_index", None) == p.index
                                     and (li.source_label or "").strip()})}
                for p in doc.pages if getattr(p, "statement", None)
            ],
        }
        path = args.out / f"{stem}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        index.append({"filename": name, "file": path.name, "pages": payload["pages"],
                      "notes": len(payload["notes"]),
                      "distinct_note_numbers": len({n["note"] for n in payload["notes"]}),
                      "currency": payload["currency"], "scale": payload["scale"]})
        print(f"      {len(payload['notes'])} note tables, "
              f"{len({n['note'] for n in payload['notes']})} distinct numbers -> {path.name}")

    (args.out / "INDEX.json").write_text(json.dumps(index, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    print(f"\nwrote {len(index)} corpus file(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
