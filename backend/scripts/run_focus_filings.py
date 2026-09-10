#!/usr/bin/env python
"""Run a filing through the pipeline with the CURRENT line-item configuration, and report the
figures the eight focus concepts came to — with the note and the cascade rung each one came from.

WHY IN-PROCESS rather than through the API. The point is the FIGURES and their provenance, and this
is the shortest path to both: the run is handed the configuration straight off disk, so what is
measured is the file an author just edited rather than whatever version happens to be published in
the database. It also means a result survives a database that has been cleared, which this one has
(0 extraction runs).

WHAT IT PRINTS, per focus concept:
  * the figure, per (basis, period);
  * WHERE IT CAME FROM — the cascade rung that answered, or the note rows that were summed;
  * and, when it is empty, WHY — no note matched, no rung resolved, or the configuration refused
    the concept as a note source (`note_use: evidence_only`).

The third is the one worth having. A concept that came out empty because the filing does not
disclose it and one that came out empty because its configuration cannot reach it look identical in
a spreadsheet, and only one of them is something to fix.

    python scripts/run_focus_filings.py ../_run8/suncreate.pdf [--out results.json] [--no-llm]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

FOCUS = [
    "is_pl__deprec_and_impairment_oper_exp",
    "is_pl__deprec_and_impairment_cos",
    "bs_ca__secur_and_other_fincl_assets_cp",
    "bs_nca__secur_and_other_fincl_assets_ltp",
    "notes__contingent_liabilities",
    "bs_nca__due_from_related_parties_ltp",
    "bs_ca__other_receivables_cp",
    "is_pl__sales_revenues",
]


def _figures(row) -> list[dict]:
    out = []
    for ev in (row.values or {}).values():
        basis = getattr(ev, "basis", "")
        out.append({
            "basis": str(getattr(basis, "value", basis) or ""),
            "period": str(getattr(ev, "period_label", "") or ""),
            "value": None if ev.value is None else str(ev.value),
            "value_raw": None if ev.value_raw is None else str(ev.value_raw),
            "text": ev.value_text,
            "page": getattr(getattr(ev, "provenance", None), "page_index", None),
        })
    return [f for f in out if f["value"] is not None or f["text"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--no-llm", action="store_true",
                    help="deterministic tiers only — for validating the plumbing without spending "
                         "a provider budget")
    args = ap.parse_args()

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    settings = get_settings()
    if args.no_llm:
        settings.extraction.llm_mapping = False

    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    view = build_working_view(st)
    pdf = pathlib.Path(args.pdf).resolve()
    data = pdf.read_bytes()

    print(f"filing        : {pdf.name} ({len(data):,} bytes)")
    print(f"configuration : {len(st.items)} items, "
          f"{sum(1 for i in st.items if i.note_source is not None)} note-sourced")
    print(f"llm mapping   : {settings.extraction.llm_mapping}"
          f"  focus_only={settings.extraction.llm_focus_only}"
          f"  concurrency={settings.extraction.llm_max_concurrency}")
    print("running…", flush=True)

    began = time.time()
    doc, ctx = run_extraction(data, filename=pdf.name, ontology=view, template=None,
                              line_items=st)
    took = time.time() - began

    by_key: dict[str, list] = {}
    for li in doc.line_items:
        if li.canonical_key:
            by_key.setdefault(li.canonical_key, []).append(li)

    # Why a concept is empty, in the configuration's own terms.
    defs = {i.key: i for i in st.items}
    kids_of: dict[str, list[str]] = {}
    for i in st.items:
        if i.parent:
            kids_of.setdefault(i.parent, []).append(i.key)

    result = {"filing": pdf.name, "pages": len(doc.pages), "seconds": round(took, 1),
              "line_items": len(doc.line_items), "notes": len(doc.notes),
              "llm_calls": getattr(ctx, "llm_calls", None),
              "llm_tokens_in": getattr(ctx, "llm_input_tokens", None),
              "llm_tokens_out": getattr(ctx, "llm_output_tokens", None),
              "concepts": []}

    for key in FOCUS:
        d = defs.get(key)
        rows = by_key.get(key) or []
        figures = [f for r in rows for f in _figures(r)]
        entry = {"key": key, "label": getattr(d, "label", ""), "figures": figures,
                 "rows": len(rows), "children": len(kids_of.get(key) or []),
                 "children_filled": sum(1 for k in kids_of.get(key) or [] if by_key.get(k)),
                 "flags": sorted({f for r in rows for f in (r.confidence.flags or [])}),
                 "trail": [], "why_empty": ""}
        for r in rows:
            for slot, tr in (r.derivation or {}).items():
                entry["trail"].append({
                    "slot": slot, "method": tr.get("method"), "formula": tr.get("formula"),
                    "result": str(tr.get("result")),
                    "inputs": [{"label": i.get("label"), "value": i.get("value"),
                                "counted": i.get("counted"), "deducted": i.get("deducted"),
                                "note": i.get("note")}
                               for i in (tr.get("inputs") or [])],
                })
        if not figures:
            if d is None:
                entry["why_empty"] = "not in the configuration"
            elif str(getattr(d, "note_use", "")) != "decomposition_allowed" and kids_of.get(key):
                entry["why_empty"] = (f"configuration refuses notes as a source "
                                      f"(note_use={d.note_use}) — the children are read but "
                                      f"nothing may be concluded onto this line")
            elif not kids_of.get(key):
                entry["why_empty"] = ("no sub-line items configured, and no caption on the face "
                                      "reached it")
            elif entry["children_filled"] == 0:
                entry["why_empty"] = (f"{len(kids_of[key])} sub-line item(s) configured, none "
                                      f"matched a note in this filing")
            else:
                entry["why_empty"] = (f"{entry['children_filled']} child(ren) filled but no "
                                      f"cascade rung resolved")
        result["concepts"].append(entry)

    result["log"] = [line for line in (ctx.logs or [])
                     if any(t in line for t in ("note_sourced", "map_line_items", "rung",
                                                "assemble", "REFUSED"))][:400]

    line = "─" * 96
    print(f"\n{line}\n{pdf.name}  —  {len(doc.pages)} pages, {took:.0f}s, "
          f"{len(doc.line_items)} rows, {len(doc.notes)} notes\n{line}")
    for c in result["concepts"]:
        got = ", ".join(f"{f['period']}={f['value'] or f['text']}" for f in c["figures"])
        print(f"\n  {c['key']}")
        print(f"     {c['label']}")
        if got:
            print(f"     FIGURE   {got}")
            for t in c["trail"]:
                print(f"     via      {t['method']}  ({t['formula']})")
                for i in t["inputs"][:6]:
                    mark = "−" if i["deducted"] else ("+" if i["counted"] else " ·")
                    print(f"        {mark} {str(i['label'])[:58]:58s} {i['value']}"
                          + (f"   note {i['note']}" if i.get("note") else ""))
        else:
            print(f"     EMPTY    {c['why_empty']}")
        print(f"     children {c['children_filled']}/{c['children']} filled")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
