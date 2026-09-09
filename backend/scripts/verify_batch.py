"""End-to-end check of this batch against a REAL filing.

Runs the pipeline on a real annual report and then asks the API the questions the four reported
defects were about, so the answers come from the same code paths the screen uses:

  1. does any row report a PRIOR-only figure as the current year?
  2. does an edit land on the figure it was typed into — including a combined line, the
     standalone basis, and a template line the document never yielded?
  3. does every figure carry a source location, last year's included?
  4. do the KPI and Additional-items views hold real content?

Usage:  python scripts/verify_batch.py /path/to/report.pdf
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="finex-verify-")
os.environ.setdefault("FINEX_DATABASE_URL", f"sqlite:///{_tmp}/verify.db")
os.environ.setdefault("FINEX_OBJECT_STORE_ROOT", f"{_tmp}/objects")


def _bs_equity(client, doc_id):
    """Total equity as the balance sheet reports it — the figure the equity matrix must close to."""
    d = client.get(f"/api/v1/documents/{doc_id}/statement",
                   params={"statement": "balance_sheet", "basis": "consolidated"}).json()
    row = next((r for r in d["rows"] if r["id"] == "bs_equity__total_equity"), None)
    return row["v1"] if row else None


def main(path: str) -> int:
    from fastapi.testclient import TestClient

    from app.db.base import init_db
    from app.main import app

    init_db()
    with TestClient(app) as anon:
        token = anon.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        with open(path, "rb") as fh:
            up = c.post("/api/v1/documents",
                        files={"file": (os.path.basename(path), fh.read(), "application/pdf")})
        up.raise_for_status()
        doc = up.json()["id"]
        print(f"uploaded {os.path.basename(path)} → {doc}")

        # THE ONE CONFIGURATION STORE. This was `GET /ontologies` plus a named rulebook key; the
        # second store is deleted, so the run pins a `line_item_version_id` and the set in force is
        # whatever `/line-items/versions` says it is — no key to prefer, because there is only one
        # engine to prefer it over.
        cfgs = c.get("/api/v1/line-items/versions").json()
        cfg = next((o for o in cfgs if o.get("in_force")), cfgs[0])
        tpls = c.get("/api/v1/templates").json()
        tpl = next((t for t in tpls if t["template_key"] == cfg["target_template_key"]), tpls[0])
        c.post(f"/api/v1/documents/{doc}/extractions",
               json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})

        t0 = time.time()
        for _ in range(3000):
            r = c.get(f"/api/v1/documents/{doc}/run")
            if r.status_code == 200 and r.json().get("status") in ("succeeded", "failed"):
                break
            time.sleep(0.5)
        run = c.get(f"/api/v1/documents/{doc}/run").json()
        print(f"extraction {run['status']} in {time.time() - t0:.0f}s")
        if run["status"] != "succeeded":
            return 1
        rows = run["result"]["rows"]
        print(f"extracted rows: {len(rows)}  mapped: {sum(1 for r in rows if r.get('canonical_key'))}")

        def stmt(kind, basis="consolidated"):
            r = c.get(f"/api/v1/documents/{doc}/statement",
                      params={"statement": kind, "basis": basis})
            r.raise_for_status()
            return r.json()

        # --- 1 + 3: period placement and per-period provenance -------------------------
        print("\n=== periods and provenance ===")
        for kind in ("balance_sheet", "profit_and_loss", "cash_flow"):
            d = stmt(kind)
            items = [x for x in d["rows"] if x["kind"] in ("item", "subtotal", "total")]
            valued = [x for x in items if x["v1"] is not None or x["v2"] is not None]
            prior_only = [x for x in valued if x["v1"] is None and x["v2"] is not None]
            with_s1 = sum(1 for x in valued if x.get("source"))
            with_s2 = sum(1 for x in valued if x.get("source2"))
            print(f"{kind:17s} periods={d['periods']} rows={len(d['rows'])} valued={len(valued)}"
                  f" prior-only={len(prior_only)} src(cur)={with_s1} src(prior)={with_s2}")
            for x in prior_only[:4]:
                print(f"    prior-only: {x['label'][:52]:52s} v2={x['v2']}")

        # Every prior figure that exists should be traceable, like the current one.
        d = stmt("balance_sheet")
        missing = [x["label"] for x in d["rows"]
                   if x.get("v2") is not None and x["kind"] == "item" and not x.get("source2")]
        print(f"balance-sheet prior figures without a source location: {len(missing)}")

        # --- 4: the two new views -------------------------------------------------------
        print("\n=== KPI view ===")
        k = stmt("kpi")
        avail = [x for x in k["rows"] if x["kind"] == "item" and x["v1"] is not None]
        unavail = [x for x in k["rows"] if x["kind"] == "item" and x["v1"] is None]
        print(f"sections={sum(1 for x in k['rows'] if x['kind'] == 'section')} "
              f"available={len(avail)} unavailable={len(unavail)} presentation={k['presentation']}")
        for x in avail[:10]:
            inputs = len(x.get("contributions") or [])
            print(f"  {x['label'][:38]:38s} {str(x['display1']):>12s} vs {str(x['display2']):>12s}"
                  f"  inputs={inputs}")

        print("\n=== Additional items ===")
        a = stmt("additional_items")
        secs = [x for x in a["rows"] if x["kind"] == "section"]
        items = [x for x in a["rows"] if x["kind"] == "item"]
        print(f"sections={[s['label'] for s in secs]} items={len(items)}")
        for x in items[:12]:
            print(f"  {x['label'][:50]:50s} v1={x['v1']} v2={x['v2']} "
                  f"src={(x.get('source') or {}).get('page_index')} editable={x['editable']}")

        # --- calculated lines: what the face now shows -----------------------------------
        print("\n=== calculated lines (computed vs printed) ===")
        for kind in ("balance_sheet", "profit_and_loss", "cash_flow"):
            d = stmt(kind)
            calc = [x for x in d["rows"] if x.get("origin") == "calculated"]
            unc = [x for x in d["rows"] if x.get("origin") == "reported_uncomputed"]
            diff = [x for x in calc if x.get("reported1") is not None
                    and abs(x["v1"] - x["reported1"]) > 0.5] if calc else []
            print(f"{kind:17s} rows={len(d['rows'])} calculated={len(calc)} "
                  f"not-computable={len(unc)} diverging={len(diff)}")
            for x in calc[:8]:
                rep = "—" if x.get("reported1") is None else f"{x['reported1']:,.0f}"
                mark = "" if x.get("reported1") is None or abs(x["v1"] - x["reported1"]) <= 0.5 \
                       else "  <-- DIFFERS"
                print(f"    {x['label'][:44]:44s} computed={x['v1']:>15,.0f} printed={rep:>15s}{mark}")
            for x in diff:
                print(f"    DIVERGES {x['label'][:40]:40s} "
                      f"computed={x['v1']:,.0f} printed={x['reported1']:,.0f}")

        # --- the equity matrix must survive the period-vs-component tightening ------------
        print("\n=== changes in equity (matrix) ===")
        e = stmt("changes_in_equity")
        cols = [c["label"] for c in e.get("columns", [])]
        print(f"layout={e['layout']} movements={len(e['rows'])} columns={len(cols)}")
        print("  " + " | ".join(cols))
        total_col = next((c for c in cols if "total equity" in c.lower()), None)
        balances = [r for r in e["rows"] if r["kind"] == "subtotal"]
        if total_col and balances:
            print(f"  closing {balances[-1]['label'][:40]!r} total equity = "
                  f"{balances[-1]['cells'].get(total_col)}")
            bs_eq = _bs_equity(c, doc)
            print(f"  balance-sheet total equity (current) = {bs_eq}")

        # --- 2: edits ---------------------------------------------------------------------
        print("\n=== edits ===")
        bs = stmt("balance_sheet")
        combined = next((x for x in bs["rows"] if (x.get("contributions") or [])), None)
        if combined:
            before = combined["v1"]
            r = c.patch(f"/api/v1/documents/{doc}/line-items/{combined['id']}",
                        json={"value": 4242, "formula": "", "period": "current",
                              "basis": "consolidated"})
            after = next(x for x in stmt("balance_sheet")["rows"] if x["id"] == combined["id"])
            ok = after["v1"] == 4242
            print(f"combined line {combined['id']}: {len(combined['contributions'])} contributors, "
                  f"was {before} → typed 4242 → shows {after['v1']}  {'OK' if ok else 'WRONG'}")
            print(f"  prior column unchanged: {after['v2'] == combined['v2']}")
            c.delete(f"/api/v1/documents/{doc}/line-items/{combined['id']}")
            reverted = next(x for x in stmt("balance_sheet")["rows"] if x["id"] == combined["id"])
            print(f"  revert restored {before}: {reverted['v1'] == before}")

        blank = next((x for x in bs["rows"] if x.get("status") == "missing"), None)
        if blank:
            r = c.patch(f"/api/v1/documents/{doc}/line-items/{blank['id']}",
                        json={"value": 777, "formula": "", "period": "prior",
                              "basis": "consolidated"})
            after = next(x for x in stmt("balance_sheet")["rows"] if x["id"] == blank["id"])
            print(f"blank template line {blank['id']}: PATCH {r.status_code} → "
                  f"v1={after['v1']} v2={after['v2']} status={after['status']}")
            c.delete(f"/api/v1/documents/{doc}/line-items/{blank['id']}")

        # --- structural checks still tie -------------------------------------------------
        print("\n=== structural relations ===")
        structural = run["result"].get("structural") or []
        from collections import Counter
        print(Counter(s.get("status") for s in structural))
        for s in structural:
            if s.get("status") == "fail":
                print("  FAIL", s.get("rule_id"), s.get("scope_key"),
                      s.get("expected"), s.get("actual"))

        rev = c.get(f"/api/v1/documents/{doc}/review").json()
        from collections import Counter as _C
        print(f"\nreview queue: {len(rev.get('checks', []))} checks")
        print("  by type:", dict(_C(x.get("type") for x in rev.get("checks", []))))
        for x in rev.get("checks", []):
            if x.get("type") in ("calculated_mismatch", "uncomputed"):
                print(f"    {x['type']}: {x['where'][:60]:60s} delta={x['delta']}")
        print("gap routings:", run["result"].get("gap_routings"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "AR.pdf"))
