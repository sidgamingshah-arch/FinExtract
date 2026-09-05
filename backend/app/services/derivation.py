"""How a computed figure was reached, in the shape the statement inspector already renders.

The eight concepts governed by docs/*_Extraction_Logic*.md are assembled from note-level datasets
rather than read off a caption, and each service builds a full evidence trail while doing it. That
trail used to end at the stage: only the FIRST evidence item's page reference survived, as the
value's provenance, and the rest was dropped. What a reviewer saw was `computed:deprec_impairment:P3`
and one page number — the priority that won, but not the figures it won with.

A row's derivation is stored per ``basis:period``, because that is the granularity the services
compute at: Oper Exp for the current year and for the prior year are two separate cascades that may
resolve at different priorities from different notes. :func:`merge_for_basis` folds the two periods
back into one contributions list, so a reader sees each input once with both of its figures beside
it — the same shape `item_row` builds for a concept combined from several printed lines, and
therefore the same rendering, with no new UI.

Inputs are identified by (dataset, note, line item) rather than by position. The current and prior
cascades can draw on different numbers of notes, so zipping two lists would pair unrelated inputs
and print last year's figure against this year's caption.
"""
from __future__ import annotations

from typing import Any


def _slot(basis: str, period_label: str) -> str:
    return f"{basis}:{period_label}"


def _identity(item: dict) -> tuple:
    return (item.get("dataset") or "", str(item.get("note") or ""),
            str(item.get("label") or ""))


def input_from_evidence(evidence: dict) -> dict:
    """One evidence record as an input row: what it was, where it came from, what it said."""
    return {
        "dataset": evidence.get("dataset_key"),
        "note": evidence.get("note_number"),
        "note_heading": evidence.get("note_heading"),
        "label": evidence.get("line_item"),
        "value": evidence.get("value"),
        "provenance": evidence.get("provenance"),
        # An evidence record the restatement control kept as corroboration rather than as an
        # addend — see services.restatement. Shown, but never presented as part of the sum.
        "counted": not (evidence.get("duplicate_of_note") or
                        evidence.get("duplicate_of_earlier_source")),
        "duplicate_of_note": evidence.get("duplicate_of_note"),
    }


def build(*, method: str, formula: str | None, inputs: list[dict], result: Any,
          flags: list[str] | None = None) -> dict:
    """One period's derivation: the rule that produced the figure, and what it consumed."""
    return {
        "method": method,
        "formula": formula,
        "result": None if result is None else str(result),
        "inputs": inputs,
        "flags": list(flags or []),
    }


def record(store: dict | None, *, basis: str, period_label: str, derivation: dict) -> dict:
    """`store` with this (basis, period)'s derivation added — the row's own accumulator."""
    out = dict(store or {})
    out[_slot(basis, period_label)] = derivation
    return out


def merge_for_basis(store: dict | None, basis: str) -> tuple[str | None, list[dict]]:
    """(display formula, contributions) for one basis, both periods folded together.

    The formula is the CURRENT period's, falling back to the prior period's when the current year
    produced no figure — a row showing only last year's number should still explain that number
    rather than nothing. Contributions keep first-seen order from the current period, then any
    input only the prior period used, so the list reads down the page in the order the inputs were
    found rather than in dictionary order.
    """
    store = store or {}
    current = store.get(_slot(basis, "current")) or {}
    prior = store.get(_slot(basis, "prior")) or {}
    formula = current.get("formula") or prior.get("formula")

    prior_by_identity = {_identity(i): i for i in (prior.get("inputs") or [])}
    contributions: list[dict] = []
    seen: set[tuple] = set()
    for item in current.get("inputs") or []:
        identity = _identity(item)
        seen.add(identity)
        twin = prior_by_identity.get(identity)
        contributions.append(_contribution(item, twin))
    for item in prior.get("inputs") or []:
        identity = _identity(item)
        if identity in seen:
            continue
        contributions.append(_contribution(None, item))
    return formula, contributions


def _to_num(text: Any) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _contribution(current: dict | None, prior: dict | None) -> dict:
    """One input as the inspector's contribution row, carrying both periods' figures.

    Deliberately the same keys `item_row` emits for a combined concept: label, canonical_key,
    v1/v2, method, residual, src/source, src2/source2, counted/counted2. A second shape would
    mean a second renderer, and the one that exists already handles click-to-source.
    """
    ref = current or prior or {}
    return {
        "label": _label(ref),
        "canonical_key": None,          # an input is a note line, not a mapped concept
        "v1": _to_num((current or {}).get("value")),
        "v2": _to_num((prior or {}).get("value")),
        "method": ref.get("dataset"),
        "residual": False,
        "src": _src_label(current),
        "source": (current or {}).get("provenance"),
        "src2": _src_label(prior),
        "source2": (prior or {}).get("provenance"),
        "counted": bool((current or {}).get("counted", True)) if current else False,
        "counted2": bool((prior or {}).get("counted", True)) if prior else False,
    }


def _label(item: dict) -> str:
    """The input's caption, qualified by the note it was printed under.

    A cascade routinely draws the same caption — "Depreciation" — from four different notes, and
    four rows reading "Depreciation" with different figures explain nothing. The note number is
    what distinguishes them, so it is part of the label rather than only a column.
    """
    label = str(item.get("label") or item.get("dataset") or "").strip()
    note = item.get("note")
    heading = item.get("note_heading")
    if note and heading:
        return f"{label} — note {note} ({heading})" if label else f"note {note} ({heading})"
    if note:
        return f"{label} — note {note}" if label else f"note {note}"
    return label


def _src_label(item: dict | None) -> str:
    prov = (item or {}).get("provenance") or {}
    page = prov.get("page_index") if isinstance(prov, dict) else None
    return f"p.{page}" if page else ""
