"""Face-line containment netting.

Some statements report a line inclusive of others — e.g. a cost of sales stated inclusive of
administrative and selling/marketing expenses. When the ontology declares such a containment,
the clean figure nets the contained lines out:

    net = target − Σ subtract + Σ add        (signed)

Signed arithmetic means it is correct whether expenses are stored as negatives (the usual case:
cost of sales −18,330, admin −1,710 → net −16,620) or as positives. It is deterministic,
non-destructive (the raw figure is preserved and the adjustment is revertable) and only applies
when the target and at least one contained line are actually present for that basis/period.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _num(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _group_by_key(rows: list[dict]) -> dict[str, list[dict]]:
    """Rows grouped by the concept they map to — see :func:`_value`."""
    from app.services.derived import _group_by_key as group

    return group(rows)


def _value(rows_by_key: dict, key: str, basis: str, period: str) -> Decimal | None:
    """One concept's figure for one (basis, period), read across EVERY row that maps to the concept.

    The index is grouped rather than last-wins because a filing carries two statements' rows under
    one key once the Company's own statement is labelled: the Group's balance sheet and the
    Company's both print cash and both print borrowings. Resolving against a single row meant the
    consolidated lookup returned None whenever the Company's row happened to be the surviving one,
    and a netting rule that cannot resolve its target is dropped for the whole run — silently, since
    a policy that did not apply and a policy that could not be read look identical downstream.
    """
    for row in _rows_for(rows_by_key, key):
        for v in row.get("values") or []:
            if (v.get("basis") or "consolidated") == basis and v.get("period_label") == period:
                return _num(v.get("value"))
    return None


def _rows_for(rows_by_key: dict, key: str) -> list[dict]:
    """The rows mapped to one key. Tolerates an ungrouped index so a caller passing the old shape
    is not silently reading a row's ``values`` as a list of rows."""
    group = rows_by_key.get(key)
    if not group:
        return []
    return group if isinstance(group, list) else [group]


def _label_for(rows_by_key: dict, key: str) -> str:
    for row in _rows_for(rows_by_key, key):
        if row.get("source_label"):
            return row["source_label"]
    return key


def build_netting_payload(rows: list[dict], rule, *, basis: str = "consolidated",
                          period: str = "current") -> dict | None:
    """The evidence an LLM needs to decide whether a containment policy applies: the target line
    and the candidate contained lines (labels + values as extracted) plus the policy condition.
    Returns None when the target or all candidates are absent (nothing to evaluate)."""
    by_key = _group_by_key(rows)
    target_key = getattr(rule, "target_key", None) or (rule.get("target_key") if isinstance(rule, dict) else None)
    sub_keys = (getattr(rule, "subtract_keys", None)
                if not isinstance(rule, dict) else rule.get("subtract_keys")) or []
    add_keys = (getattr(rule, "add_keys", None)
                if not isinstance(rule, dict) else rule.get("add_keys")) or []
    condition = (getattr(rule, "condition", None)
                 if not isinstance(rule, dict) else rule.get("condition")) or ""
    label = (getattr(rule, "label", None) if not isinstance(rule, dict) else rule.get("label")) or ""

    def lv(k: str) -> dict | None:
        v = _value(by_key, k, basis, period)
        if v is None:
            return None
        return {"key": k, "label": _label_for(by_key, k), "value": str(v)}

    target = lv(target_key)
    if target is None:
        return None
    subs = [c for c in (lv(k) for k in sub_keys) if c]
    adds = [c for c in (lv(k) for k in add_keys) if c]
    if not subs and not adds:
        return None
    return {"target": target, "subtract_candidates": subs, "add_candidates": adds,
            "condition": condition, "policy": label}


def resolve_netting(provider, rows: list[dict], rules, *, basis: str = "consolidated",
                    period: str = "current", max_tokens: int = 400) -> list[dict]:
    """LLM gate: for each generic netting policy, ask the provider whether it applies to THIS
    statement and which candidate lines are truly contained. Returns the CONFIRMED, resolved rules
    (concrete subtract/add keys the model selected) — safe to feed to compute_netting. A policy
    that doesn't apply, or that the provider can't evaluate, is simply dropped."""
    from app.services.analysis_llm import run_netting_evaluation

    resolved: list[dict] = []
    for rule in rules:
        payload = build_netting_payload(rows, rule, basis=basis, period=period)
        if payload is None:
            continue
        try:
            decision, _meta = run_netting_evaluation(provider, payload, max_tokens=max_tokens)
        except Exception:  # noqa: BLE001 — provider unreachable/misconfigured → don't net
            continue
        if not getattr(decision, "applies", False):
            continue
        cand_sub = {c["key"] for c in payload["subtract_candidates"]}
        cand_add = {c["key"] for c in payload["add_candidates"]}
        sub = [k for k in (decision.subtract_keys or []) if k in cand_sub]   # ground to candidates
        add = [k for k in (decision.add_keys or []) if k in cand_add]
        if not sub and not add:
            continue
        resolved.append({
            "id": (getattr(rule, "id", None) or (rule.get("id") if isinstance(rule, dict) else "") or ""),
            "target_key": payload["target"]["key"], "subtract_keys": sub, "add_keys": add,
            "label": payload["policy"], "condition": payload["condition"],
            "rationale": getattr(decision, "rationale", "") or "",
            "confidence": float(getattr(decision, "confidence", 0.0) or 0.0),
        })
    return resolved


def compute_netting(rows: list[dict], rules, *, basis: str = "consolidated",
                    period: str = "current") -> dict[str, dict]:
    """Per target key, the netted value + a human formula for one basis/period.

    ``rules`` is a list of NettingRule (or dicts with the same fields). Returns
    ``{target_key: {raw, net, formula, label, subtract, add}}`` only for rules whose target and
    at least one contained line are present — so a rule silently no-ops on a document that
    doesn't carry those lines."""
    by_key = _group_by_key(rows)
    out: dict[str, dict] = {}
    for rule in rules:
        target_key = getattr(rule, "target_key", None) or (rule.get("target_key") if isinstance(rule, dict) else None)
        sub_keys = getattr(rule, "subtract_keys", None)
        add_keys = getattr(rule, "add_keys", None)
        label = getattr(rule, "label", None)
        if isinstance(rule, dict):
            sub_keys = rule.get("subtract_keys", []) if sub_keys is None else sub_keys
            add_keys = rule.get("add_keys", []) if add_keys is None else add_keys
            label = rule.get("label", "") if label is None else label
        sub_keys = sub_keys or []
        add_keys = add_keys or []

        target = _value(by_key, target_key, basis, period)
        if target is None:
            continue
        subs = [(k, _value(by_key, k, basis, period)) for k in sub_keys]
        adds = [(k, _value(by_key, k, basis, period)) for k in add_keys]
        present_sub = [(k, v) for k, v in subs if v is not None]
        present_add = [(k, v) for k, v in adds if v is not None]
        if not present_sub and not present_add:
            continue

        net = target - sum((v for _, v in present_sub), Decimal(0)) \
            + sum((v for _, v in present_add), Decimal(0))
        terms = [_label_for(by_key, target_key)]
        terms += [f"− {_label_for(by_key, k)}" for k, _ in present_sub]
        terms += [f"+ {_label_for(by_key, k)}" for k, _ in present_add]
        out[target_key] = {
            "target_key": target_key,
            "raw": str(target),
            "net": str(net),
            "formula": " ".join(terms),
            "label": label or "",
            "subtract": [k for k, _ in present_sub],
            "add": [k for k, _ in present_add],
        }
    return out
