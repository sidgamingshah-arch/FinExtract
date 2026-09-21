"""A spy provider that answers `stages.line_item_llm` the way a compliant model would.

WHY THIS EXISTS AS A SHARED MODULE. `extraction.llm_mapping` ships True, but with no provider
reachable the stage logs why and returns the document untouched — so a container with no key runs
fully deterministically and every figure measured in it is the deterministic route's. That is a
defined outcome for a RUN and a blind spot for MEASUREMENT: the model's citation outranks the
declared `note_source` read entirely (`stages.note_sourced._llm_holds`), so the route that decides
most of these figures in production is the one hardest to observe.

WHAT MAKES IT A FAIR STAND-IN rather than a rigged one. It decides nothing the request leaves open.
For each line item asked about it takes the FIRST note that line's own configuration selected —
`notes_supplied`, which the request states in the configuration's order — and cites that note's
TOTAL row. That is not a guess about how a model behaves; it is what these parts' definitions
instruct in their own words ("read once from that note's own total or carrying-amount line"), so
citing it is the compliant answer to the request as written.

WHAT IT IS NOT. It exercises no judgement, so it is a WORST PLAUSIBLE CASE and not a prediction of
a real model's error rate: where a filing prints several candidate notes it will cite the first
rather than the right one. Use it to prove that a defect is POSSIBLE and that a guard closes it,
never to claim a figure a real provider would produce.
"""
from __future__ import annotations

import json
import re

# The row a note states its own total on, in both scripts and in English. `合计` is what the
# mainland filings print; `carrying amount` and the 账面价值 family are the HKFRS spellings, and
# every `row_caption_any` in the shipped set already lists them.
_TOTAL_ROW = re.compile(
    r"^\s*(?:合\s*计|合計|總\s*計|总\s*计|總額|总额"
    r"|total\b|carrying\s+(?:amount|value)"
    r"|(?:帐|賬|账)面(?:价值|價值|净值|淨值))",
    re.IGNORECASE)


class SpyLineItemLlm:
    """Cites the total row of the first note each line item selected, and records every exchange.

    `only` narrows which keys it answers for, so a test can put one pair of sibling parts in front
    of the model and leave the rest of a focus list alone. None answers for every key asked.
    """

    id = "spy"

    def __init__(self, *, only: set[str] | None = None,
                 cite: dict[str, tuple[str, str]] | None = None):
        self.only = only
        # An explicit override per key, `{key: (note, caption)}`, for a test that needs to say
        # exactly which row each of two siblings cites — which is the difference between "both read
        # one row" and "each read its own".
        self.cite = dict(cite or {})
        self.requests: list[dict] = []
        self.citations: list[dict] = []

    # Matches `app.ports.llm.LlmProvider.complete_structured`.
    def complete_structured(self, *, system, messages, response_schema,
                            temperature: float = 0.0, max_tokens: int = 2048):
        first = messages[0]
        req = json.loads(first["content"] if isinstance(first, dict) else first.content)
        self.requests.append(req)

        blocks: dict[str, list[dict]] = {}
        for block in req.get("notes") or ():
            blocks.setdefault(str(block.get("note")), []).append(block)

        answers = []
        for line in req.get("line_items") or ():
            key = str(line.get("key") or "")
            if self.only is not None and key not in self.only:
                continue
            cited = self.cite.get(key) or self._total_row(line, blocks)
            if cited is None:
                answers.append({"key": key, "sources": [], "role": "whole",
                                "confidence": 0.0, "reason": "no note row to cite"})
                continue
            note, caption = cited
            answers.append({"key": key, "role": "whole", "confidence": 0.9,
                            "reason": f"note {note} states it on its total line",
                            "sources": [{"note": note, "caption": caption}]})
            self.citations.append({"key": key, "note": note, "caption": caption})

        # A DICT FOR THE META, because `stages.line_item_llm` reads it with `.get` — every real
        # adapter returns a mapping, and a stand-in returning an object fails at the token
        # accounting rather than at the answer, which is a confusing way to learn this.
        return response_schema.model_validate({"answers": answers}), {
            "input_tokens": 0, "output_tokens": 0, "model": "spy", "provider": "spy"}

    @staticmethod
    def _total_row(line: dict, blocks: dict[str, list[dict]]) -> tuple[str, str] | None:
        for ref in line.get("notes_supplied") or ():
            for block in blocks.get(str(ref), ()):
                rows = block.get("rows") or []
                row = next((r for r in rows
                            if _TOTAL_ROW.match(str(r.get("caption") or ""))), None)
                if row is None and rows:
                    row = rows[-1]          # a note with no total line: its last row is its sum
                if row is not None:
                    return str(ref), str(row.get("caption") or "")
        return None

    def cited_more_than_once(self) -> dict[tuple[str, str], list[str]]:
        """`{(note, caption): [key, …]}` for every row more than one line item cited.

        The measurement this class was written for: a printed row states ONE quantity, so a row
        cited for two lines is two lines claiming one figure — and where those lines are `any_of`
        siblings of a rung, the sum adds it twice unless something stops it.
        """
        by_row: dict[tuple[str, str], list[str]] = {}
        for c in self.citations:
            by_row.setdefault((c["note"], c["caption"]), []).append(c["key"])
        return {row: keys for row, keys in by_row.items() if len(keys) > 1}
