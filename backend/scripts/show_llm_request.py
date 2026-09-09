#!/usr/bin/env python
"""Print the ACTUAL request sent to the model for one mapping call.

WHY THIS EXISTS. "What goes into the context" is not answerable from the schema: the request is
assembled at call time out of five separate sources — a code literal, two configuration blocks, the
row being asked about, and one entry per offered concept. A table of field names does not tell you
what the model reads. This prints the real thing.

Sends nothing. A stub provider captures the request the adapter would have posted.

    python scripts/show_llm_request.py                     # one caption, full request
    python scripts/show_llm_request.py --caption "存货"     # a caption of your own
    python scripts/show_llm_request.py --full               # every candidate, not just the first
    python scripts/show_llm_request.py --with-notes         # add a sample filing's notes and face
                                                            # rows, so the DOCUMENT CONTEXT block
                                                            # appears on the row
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import get_settings                                        # noqa: E402
from app.schemas.line_items import load_line_item_set                      # noqa: E402
from app.services.mapping import OntologyMatcher                           # noqa: E402
from app.services.note_context import ContextPool, ContextUnit             # noqa: E402
from app.services.working_view import build_working_view                   # noqa: E402

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


class _Capture:
    """Stands in for the provider and records what it was asked."""

    id = "capture"

    def __init__(self) -> None:
        self.system: str = ""
        self.user: str = ""

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.system = system
        self.user = messages[-1]["content"]
        raise RuntimeError("captured")


def _sample_pool() -> ContextPool:
    """A filing-shaped set of notes and face rows, so the context block is visible without a PDF.

    In a real run `stages.map_ontology` builds this from the document itself; the selection rule is
    identical either way, so what prints here is the real mechanism on stand-in data.
    """
    notes = {
        "12": ("Trade and other receivables",
               ["Trade receivables from third parties", "Less: loss allowance",
                "Prepayments and deposits", "Amounts due from related parties"]),
        "13": ("Inventories", ["Raw materials", "Work in progress", "Finished goods"]),
        "16": ("Borrowings", ["Bank loans secured", "Debentures", "Finance lease liabilities"]),
        "21": ("Cash and bank balances",
               ["Cash at bank and on hand", "Short-term deposits", "Pledged deposits"]),
    }
    faces = [("Trade receivables from third parties", "96200"),
             ("Prepayments and deposits", "12100"),
             ("Raw materials", "9400"),
             ("Cash at bank and on hand", "55300")]
    return ContextPool(
        [ContextUnit(kind="note", ref=ref, title=title, captions=tuple(rows))
         for ref, (title, rows) in notes.items()]
        + [ContextUnit(kind="face", ref="balance_sheet", captions=(cap,), amount=amount,
                       row_id=f"other-{i}")
           for i, (cap, amount) in enumerate(faces)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caption", default="Trade and other receivables")
    ap.add_argument("--statement", default="balance_sheet")
    ap.add_argument("--section", default=None, help="the printed banner above the row")
    ap.add_argument("--full", action="store_true", help="print every candidate")
    ap.add_argument("--with-notes", action="store_true",
                    help="attach a sample filing's notes and face rows, so the selected "
                         "document context appears on the row")
    args = ap.parse_args()

    settings = get_settings()
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    ontology = build_working_view(st)

    spy = _Capture()
    matcher = OntologyMatcher(ontology, locale="en", settings=settings, llm_provider=spy)
    try:
        matcher.match_batch([("row-1", args.caption)], statement=args.statement,
                            sections={"row-1": args.section},
                            context_pool=_sample_pool() if args.with_notes else None)
    except Exception:
        pass
    if not spy.user:
        print("No request was built — the caption resolved before the model was consulted, or no "
              "candidate survived the gate. Try --section, or a caption with no exact alias.")
        return 1

    payload = json.loads(spy.user)
    line = "─" * 78

    print(f"\n{line}\n1. SYSTEM PROMPT — one per call, the same for every caption\n{line}")
    print(spy.system)

    print(f"\n{line}\n2. USER MESSAGE — the keys it carries\n{line}")
    for key, value in payload.items():
        shape = (f"{len(value)} item(s)" if isinstance(value, list)
                 else f"{len(value)} chars" if isinstance(value, str) else type(value).__name__)
        print(f"   {key:26s} {shape}")

    print(f"\n{line}\n3. THE ROW — and what the filing says elsewhere about it\n{line}")
    print(json.dumps(payload.get("source_items"), indent=2, ensure_ascii=False))
    if not args.with_notes:
        print("\n   (no `context` above: this run supplied no document. Re-run with --with-notes\n"
              "    to see the notes and face rows a real filing would contribute.)")

    cands = payload.get("candidates") or []
    print(f"\n{line}\n4. THE CONCEPTS OFFERED — {len(cands)} of them, "
          f"capped by extraction.llm_candidate_cap={settings.extraction.llm_candidate_cap}\n{line}")
    shown = cands if args.full else cands[:1]
    for c in shown:
        print(json.dumps(c, indent=2, ensure_ascii=False))
    if not args.full and len(cands) > 1:
        print(f"\n   … and {len(cands) - 1} more, same shape. Keys present across all of them:")
        keys: dict[str, int] = {}
        for c in cands:
            for k in c:
                keys[k] = keys.get(k, 0) + 1
        for k, n in sorted(keys.items(), key=lambda kv: -kv[1]):
            print(f"     {k:24s} on {n} of {len(cands)}")

    if payload.get("candidate_policy_defaults"):
        print(f"\n{line}\n4b. POLICY STATED ONCE instead of once per candidate\n{line}")
        print(json.dumps(payload["candidate_policy_defaults"], indent=2, ensure_ascii=False))
        print("   Each candidate above that disagrees keeps its own value inline, which overrides.")

    if payload.get("residual_expectations"):
        print(f"\n{line}\n5. RESIDUAL EXPECTATIONS — captions with no dedicated line\n{line}")
        print(json.dumps(payload["residual_expectations"], indent=2, ensure_ascii=False)[:900])

    total = len(spy.system) + len(spy.user)
    print(f"\n{line}\n   system {len(spy.system):,} chars + user {len(spy.user):,} "
          f"= {total:,} chars (~{total // 4:,} tokens)\n{line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
