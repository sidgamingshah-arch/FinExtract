#!/usr/bin/env python
"""How many forwarded rows want an answer the model cannot give.

THE QUESTION, raised by `trace_line_item.py` on `is_pl__sales_revenues`. The focus row gate
(`stages.map_ontology`) forwards a row whose deterministic answer IS a focus concept, so the model
can confirm or correct it. But a focus concept in `_llm_withheld` is not in any candidate list and
its `deterministic_suggestion` is stripped from the row — so the model is handed a caption, told
nothing about what the deterministic tiers made of it, and cannot return the concept the row
resolved to. The call is spent and can never come back with the answer the focus run wants.

Measured on the reference filing, revenue's own row ('TURNOVER') is forwarded in call 6 with
`deterministic_suggestion = None` and `deterministic_candidates = []`. This counts how often that
happens across the whole run, so the cost is a number rather than an anecdote.

    python scripts/unanswerable_forwards.py ../_run8/laisun.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    args = ap.parse_args()

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.mapping import OntologyMatcher
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    view = build_working_view(cfg)
    settings = get_settings()
    focus = set(settings.extraction.llm_focus_keys or ())

    class Capture:
        id = "unanswerable-probe"

        def __init__(self):
            self.rows: list[tuple[int, str, object]] = []

        def complete_structured(self, *, system, messages, response_schema, **_):
            payload = json.loads(messages[-1]["content"])
            call = len({c for c, _l, _s in self.rows}) + 1
            for item in payload.get("source_items") or []:
                self.rows.append((call, str(item.get("caption") or ""),
                                  item.get("deterministic_suggestion")))
            return response_schema.model_validate({"mappings": []}), {}

    cap = Capture()
    settings.extraction.llm_mapping = True
    settings.llm.provider = cap.id
    run_extraction(args.pdf.read_bytes(), filename=args.pdf.name, ontology=view,
                   template=None, line_items=cfg,
                   context_cb=lambda c: c.registry.register("llm", cap.id, lambda: cap))

    # What the DETERMINISTIC tiers make of each forwarded caption — the answer the row gate used to
    # decide to forward it, recomputed here because the request no longer carries it.
    det = OntologyMatcher(view, locale="en", settings=settings)
    withheld_focus = {k for k in focus if k in det._llm_withheld}

    unanswerable, blind, total = [], 0, len(cap.rows)
    for call, caption, suggestion in cap.rows:
        if suggestion is None:
            blind += 1
        res = det.match(caption, statement=None, section=None)
        key = res.canonical_key if res else None
        if key in withheld_focus:
            unanswerable.append((call, caption, key))

    print(f"\n{args.pdf.name}")
    print(f"  forwarded rows                      {total}")
    print(f"  …with no deterministic_suggestion   {blind}  "
          f"(either unresolved, or resolved to a withheld key and stripped)")
    print(f"  …whose deterministic answer is a WITHHELD focus key: {len(unanswerable)}")
    for call, caption, key in unanswerable[:20]:
        print(f"      call {call:>3}  {caption[:52]:54s} -> {key}")
    print(f"\n  withheld focus keys: {len(withheld_focus)} of {len(focus)} configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
