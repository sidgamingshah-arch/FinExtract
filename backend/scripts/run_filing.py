#!/usr/bin/env python
"""Run one filing through the real pipeline with no browser and no dev server.

WHY THIS EXISTS. Every recorded run in `_run_output/` was made by uploading through the frontend,
so reproducing one — or triaging a new filing — needed a running backend, a running Vite server and
a person clicking. That is a poor way to answer "what does the deterministic route make of this
document", which is a question worth asking of every new filing before any provider budget is
spent on it.

THE REAL CODE PATH, not a reimplementation of it. This drives the API in process through
FastAPI's TestClient: POST /documents, POST /documents/{id}/extractions, poll /run, then read the
same statement and run endpoints the screens read. A script that assembled the pipeline itself
would be a second spelling of "how a run is made", and the two would drift on the first change to
the route.

    python scripts/run_filing.py path/to/filing.pdf --no-llm --out _scratch/run

`--no-llm` selects the stub provider, which is what "the deterministic route" means here: the
lexical mapping ensemble runs, the batch/description tiers are wired but answer deterministically,
and nothing leaves the machine. It is the same switch `tests/conftest.py` uses, and it is NOT the
same as turning the LLM tiers off — measured, that is worse, because a dozen behaviours exist to
consult that path.

Writes, into --out:
    <stem>__result.json     the complete run result the API would serve
    <stem>__run.log         the run's own stage-by-stage log
    <stem>__summary.json    what mapped, what did not, and the review queue's own counts
    <stem>__unmapped.csv    every face caption that reached no concept, with its page
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _isolate(scratch: Path) -> None:
    """A database and object store of this script's own.

    Never the developer's `backend/finex.db`: a triage run would otherwise publish rows into a
    workspace someone else is reading, and — worse for the answer — inherit whatever rulebook
    versions previous runs left in force. Set before anything imports `app.config`, because
    settings are read once at import.
    """
    os.environ.setdefault("FINEX_DATABASE_URL", f"sqlite:///{scratch}/run.db")
    os.environ.setdefault("FINEX_OBJECT_STORE_ROOT", str(scratch / "objects"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, default=REPO / "_scratch" / "run")
    ap.add_argument("--no-llm", action="store_true",
                    help="the deterministic route: the stub provider, no network call")
    ap.add_argument("--template-key", default="output_csv_hk_v1")
    ap.add_argument("--locale", default=None,
                    help="output locale; omitted lets the run detect the filing's own")
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--keep", action="store_true", help="keep the scratch database")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"no such file: {args.pdf}", file=sys.stderr)
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="finex-run-"))
    _isolate(scratch)
    if args.no_llm:
        os.environ["FINEX_LLM__PROVIDER"] = "stub"

    from fastapi.testclient import TestClient

    from app.db.base import init_db
    from app.main import app

    init_db()
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.pdf.stem

    with TestClient(app) as anon:
        token = anon.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        # THE RULEBOOK IN FORCE FOR THE TEMPLATE, chosen the way the upload screen chooses it, so
        # the run maps against the same rules a real upload would. Named explicitly rather than
        # left to the server's default: "which rulebook answered" is the first thing a triage
        # report has to be able to state.
        templates = c.get("/api/v1/templates").json()
        tpl = next((t for t in templates if t["template_key"] == args.template_key), None)
        if tpl is None:
            print(f"no template named {args.template_key}; stored: "
                  f"{sorted({t['template_key'] for t in templates})}", file=sys.stderr)
            return 2
        ontologies = [o for o in c.get("/api/v1/ontologies").json()
                      if o["target_template_key"] == args.template_key]
        ont = next((o for o in ontologies if o.get("in_force")), ontologies[0] if ontologies
                   else None)

        print(f"template {tpl['template_key']} v{tpl.get('version')}")
        if ont is not None:
            print(f"rulebook {ont['ontology_key']} v{ont.get('version')}  (id {ont['id']})")

        with args.pdf.open("rb") as fh:
            up = c.post("/api/v1/documents",
                        files={"file": (args.pdf.name, fh.read(), "application/pdf")})
        if up.status_code >= 400:
            print(f"upload refused: {up.status_code} {up.text}", file=sys.stderr)
            return 1
        doc_id = up.json()["id"]
        print(f"document {doc_id}  ({args.pdf.name})")

        options = {"template_version_id": tpl["id"]}
        if ont is not None:
            options["ontology_version_id"] = ont["id"]
        if args.locale:
            options["locale"] = args.locale
        started = c.post(f"/api/v1/documents/{doc_id}/extractions", json=options)
        if started.status_code >= 400:
            print(f"extraction refused: {started.status_code} {started.text}", file=sys.stderr)
            return 1

        # POLLED on `run-status`, which is the read that answers WITHOUT a run id and while the
        # run is still in flight — `GET /documents/{id}/run` refuses until there is a result,
        # which is exactly the state a running run is not in. The LAST PHASE IS PRINTED as it
        # changes: a 200-page filing spends minutes inside one stage, and a script that printed
        # nothing until the end is indistinguishable from one that has hung.
        deadline = time.time() + args.timeout
        status, phase, run_id = "", "", ""
        while time.time() < deadline:
            st = c.get(f"/api/v1/documents/{doc_id}/run-status").json()
            status = st.get("status") or ""
            run_id = st.get("run_id") or run_id
            now = str((st.get("progress") or {}).get("phase") or "")
            if now and now != phase:
                phase = now
                print(f"  … {phase}")
            if status in ("succeeded", "failed", "canceled"):
                break
            time.sleep(0.5)
        print(f"run {run_id or '?'} {status or 'timed out'}")

        # The run's own reads: `/{doc}/run` carries the result the screens render, and
        # `/extractions/{run_id}` carries the pipeline's log tail. Both are asked for even on a
        # FAILED run — the log is the whole point of triaging one.
        served = c.get(f"/api/v1/documents/{doc_id}/run")
        served = served.json() if served.status_code < 400 else {}
        result = served.get("result") or {}
        detail = c.get(f"/api/v1/extractions/{run_id}") if run_id else None
        detail = detail.json() if detail is not None and detail.status_code < 400 else {}

        (args.out / f"{stem}__result.json").write_text(
            json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        logs = detail.get("logs") or detail.get("log") or ""
        if isinstance(logs, list):
            logs = "\n".join(str(x) for x in logs)
        (args.out / f"{stem}__run.log").write_text(
            f"# run {run_id}  status {status}\n{logs}\n", encoding="utf-8")
        if not result:
            print("the run produced no result — read the log", file=sys.stderr)

        rows = result.get("rows") or []
        summary = _summarise(result, rows, tpl, ont, status,
                             served.get("rulebook") or detail.get("rulebook"))
        (args.out / f"{stem}__summary.json").write_text(
            json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
        _write_unmapped(args.out / f"{stem}__unmapped.csv", rows)

    if not args.keep:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\nwrote {args.out}/{stem}__{{result,summary,unmapped,run.log}}")
    for line in _report(summary):
        print(line)
    return 0 if status == "succeeded" else 1


def _summarise(result: dict, rows: list[dict], tpl: dict, ont: dict | None,
               status: str, recorded_rulebook: dict | None = None) -> dict:
    from app.stages.face_mapping_contract import is_unclassified_face_key

    unclassified = [r for r in rows if is_unclassified_face_key(r.get("canonical_key"))]
    unmapped = [r for r in rows if not r.get("canonical_key")]
    by_method: dict[str, int] = {}
    for r in rows:
        by_method[str(r.get("mapping_method") or "—")] = \
            by_method.get(str(r.get("mapping_method") or "—"), 0) + 1
    return {
        "status": status,
        "entity": result.get("entity"),
        "filename": result.get("filename"),
        "locale": result.get("locale"),
        "pages": result.get("page_count"),
        "template": {"key": tpl["template_key"], "version": tpl.get("version")},
        # WHAT THE RUN RECORDED, not what this script asked for. The two agreeing is the normal
        # case; them differing is the single most useful thing a triage report can say, because a
        # rulebook fix that is not in force explains a wrong figure on its own.
        "rulebook_requested": ({"key": ont["ontology_key"], "version": ont.get("version")}
                               if ont else None),
        "rulebook_recorded": recorded_rulebook,
        "mapping": result.get("mapping"),
        "rows": len(rows),
        # `notes` is a COUNT in the served result and `note_details` the tables themselves.
        # Both are read defensively: this script summarises whatever the endpoint serves, and a
        # triage tool that dies on the shape of a field is a triage tool that ran for ten minutes
        # and told you nothing.
        "notes": _count(result.get("notes")),
        "note_details": _count(result.get("note_details")),
        "mapped": sum(1 for r in rows
                      if r.get("canonical_key")
                      and not is_unclassified_face_key(r.get("canonical_key"))),
        "engine_unclassified_face": len(unclassified),
        "no_canonical_key": len(unmapped),
        "by_mapping_method": dict(sorted(by_method.items(), key=lambda kv: -kv[1])),
        "units": result.get("units"),
        "reconciliation_entries": _count(result.get("reconciliation")),
        "structural": _structural(result.get("structural")),
    }


def _count(value) -> int:
    """How many of a field the result reports, whatever shape it arrived in."""
    if isinstance(value, int):
        return value
    return len(value or [])


def _structural(structural) -> dict | None:
    """The structural report's own tally, from either shape the endpoint serves.

    A dict carries the counts; a LIST is the per-relation results, and the tally is then this
    script's to compute — by `status`, which is the field every result carries.
    """
    if isinstance(structural, dict):
        return {k: v for k, v in structural.items()
                if k in ("passed", "failed", "skipped", "errors", "failed_assertions")}
    if isinstance(structural, list):
        out: dict[str, int] = {}
        for res in structural:
            key = str((res or {}).get("status") or "—")
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
    return None


def _write_unmapped(path: Path, rows: list[dict]) -> None:
    """Every face caption that reached no concept, with the page it was printed on.

    The triage artefact: a deterministic run's characteristic failure is not a wrong number but a
    caption nothing claimed, and reading that out of the full result by hand is what this file
    exists to save.
    """
    from app.stages.face_mapping_contract import is_unclassified_face_key

    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["source_label", "printed_in", "note", "page", "current", "prior", "why"])
        for r in rows:
            key = r.get("canonical_key")
            if key and not is_unclassified_face_key(key):
                continue
            vals = {v.get("period_label"): v for v in (r.get("values") or [])}
            prov = (vals.get("current") or vals.get("prior") or {}).get("provenance") or {}
            w.writerow([
                r.get("source_label") or "", r.get("printed_in") or "", r.get("note") or "",
                prov.get("printed_page") or prov.get("page_index") or "",
                (vals.get("current") or {}).get("value") or "",
                (vals.get("prior") or {}).get("value") or "",
                "engine_unclassified_face" if key else "no_canonical_key",
            ])


def _report(summary: dict) -> list[str]:
    out = [
        f"entity   {summary.get('entity') or '—'}",
        f"locale   {summary.get('locale')}   pages {summary.get('pages')}",
        f"rows     {summary['rows']}  ({summary['mapped']} mapped, "
        f"{summary['engine_unclassified_face']} unclassified face, "
        f"{summary['no_canonical_key']} with no key)",
        f"notes    {summary['notes']}  ({summary['note_details']} extracted tables)",
        f"mapping  {summary.get('mapping')}",
    ]
    top = list(summary["by_mapping_method"].items())[:8]
    out.append("methods  " + ", ".join(f"{k}={v}" for k, v in top))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
