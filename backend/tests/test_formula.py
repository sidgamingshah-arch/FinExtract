"""Safe formula engine (Req 10): formulas compute values, reference other line items, and
reject anything outside the whitelist."""
from __future__ import annotations

import pytest

from app.services.formula import FormulaError, evaluate


def _res(mapping):
    def r(name):
        return mapping[name]  # raises KeyError for unknown → FormulaError
    return r


def test_arithmetic_and_functions():
    assert evaluate("=1 + 2 * 3", _res({})) == 7.0
    assert evaluate("(1 + 2) * 3", _res({})) == 9.0
    assert evaluate("=SUM(1, 2, 3)", _res({})) == 6.0
    assert evaluate("=ABS(-5)", _res({})) == 5.0
    assert evaluate("=MAX(1, 9, 4)", _res({})) == 9.0


def test_references_resolve():
    m = {"bs_current_assets__inventories": 2000, "bs_current_assets__trade_receivables": 3410}
    assert evaluate("=bs_current_assets__inventories + bs_current_assets__trade_receivables",
                    _res(m)) == 5410.0


def test_unknown_reference_raises():
    with pytest.raises(FormulaError):
        evaluate("=missing_key + 1", _res({}))


def test_disallowed_constructs_raise():
    for bad in ["__import__('os').system('x')", "1 ** 2", "1 // 2", "lambda: 1", "a and b"]:
        with pytest.raises(FormulaError):
            evaluate(bad, _res({"a": 1, "b": 2}))
    with pytest.raises(FormulaError):
        evaluate("=1/0", _res({}))


def test_edit_with_formula_computes_value(client):
    import time

    from tests.fixtures.generate import make_rich_pdf

    doc_id = client.post("/api/v1/documents",
                         files={"file": ("rich.pdf", make_rich_pdf(), "application/pdf")}).json()["id"]
    # Line items is the single configuration engine: ``GET /ontologies`` and the run's
    # ``ontology_version_id`` pin are gone with the ontology store, so the shipped
    # configuration is read from ``/line-items/versions`` and pinned as a
    # ``line_item_version_id``.
    from app.sample.reference import shipped_line_items_key

    cfgs = client.get("/api/v1/line-items/versions").json()
    cfg = next((c for c in cfgs if c["line_items_key"] == shipped_line_items_key()), cfgs[0])
    tpls = client.get("/api/v1/templates").json()
    tpl = next((t for t in tpls if t["template_key"] == cfg["target_template_key"]), tpls[0])
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    for _ in range(100):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)

    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    keys = {r["canonical_key"] for r in rows if r.get("canonical_key")}
    # The two keys are the shipped CONFIGURATION's, because the configuration is what decides which
    # canonical keys a run produces and there is only one of them now. They were
    # ``bs_current_assets__inventories`` / ``bs_current_assets__trade_receivables`` — the
    # hkfrs_hk_china_v1 spread, which the retired ontology pair targeted. The mechanism under test
    # (a formula edit referencing another extracted concept) is unchanged.
    inv, ref = "bs_ca__inventories", "bs_ca__cash_in_hand_and_at_banks"
    assert inv in keys and ref in keys

    # Edit inventories to a formula referencing another extracted concept → value computed.
    ref_val = next(float(v["value"]) for r in rows if r["canonical_key"] == ref
                   for v in r["values"] if v["period_label"] == "current")
    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/{inv}",
                     json={"formula": f"={ref} + 100"})
    assert r.status_code == 200, r.text
    assert float(r.json()["value"]) == ref_val + 100

    # A bad formula is rejected, not silently applied.
    bad = client.patch(f"/api/v1/documents/{doc_id}/line-items/{inv}", json={"formula": "=nope + 1"})
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "bad_formula"
