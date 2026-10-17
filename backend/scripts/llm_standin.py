#!/usr/bin/env python
"""THE LLM STAND-IN: test the model route on a real filing without a model endpoint.

Every request the pipeline sends to its model is recorded EXACTLY as the gateway adapter would send
it (``BedrockGatewayLlmProvider.build_body``: the system text with the JSON-schema instruction
appended, the messages unchanged), keyed by a hash of that body. Someone acting as the model answers
a request from the recorded body alone and writes ``answers/<id>.txt``; the next run parses that text
exactly as the gateway parses a model reply (``strip_fences`` + ``response_schema.model_validate_json``).
A request with no answer ABSTAINS: an empty, valid reply, which every stage treats as "the model said
nothing". Nothing here changes how the app behaves; it registers its own provider for the run.

    python scripts/llm_standin.py capture ../_filings/X.pdf --template output_csv_icon_v1_template.json \\
        --set output_csv_icon_line_items.json             # -> ../_standin/X__output_csv_icon_v1/
    python scripts/llm_standin.py lint     DIR            # mechanical context checks, no model
    python scripts/llm_standin.py sample   DIR --n 12     # stratified sample for the blind pass
    python scripts/llm_standin.py validate DIR            # answers / audit records / key well formed
    python scripts/llm_standin.py replay   ../_filings/X.pdf --template T --set S --out DIR
    python scripts/llm_standin.py report   DIR [--key KEY.json]

The procedure, the answerer's brief, the audit rubric and the key format: docs/testing/llm-standin.md.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
TEMPLATES = BACKEND / "app" / "sample" / "templates"
sys.path.insert(0, str(BACKEND))

from pydantic import BaseModel, Field  # noqa: E402

STAGES = ("line_item_llm", "shared_figures", "gap_closing", "contingent_liabilities", "analysis_llm")
DEFAULT_BUDGET_TOKENS = 32_000
EST_CHARS_PER_TOKEN = 4  # what the batching math itself assumes


# ------------------------------------------------------------------------------------------------
# The stand-in provider
# ------------------------------------------------------------------------------------------------

def body_id(body: dict) -> str:
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False)
                        .encode("utf-8")).hexdigest()[:16]


def gateway_body(*, system: str, messages, response_schema, max_tokens: int) -> dict:
    """The body exactly as the gateway adapter builds it (no network, no key needed)."""
    from app.adapters.bedrock_gateway_llm import BedrockGatewayLlmProvider
    return BedrockGatewayLlmProvider().build_body(system=system, messages=messages,
                                                  response_schema=response_schema,
                                                  max_tokens=max_tokens)


def parse_answer(text: str, response_schema):
    """An answer file's text parsed exactly as the gateway parses the model's reply."""
    from app.adapters._structured import strip_fences
    return response_schema.model_validate_json(strip_fences(text))


def calling_stage() -> str:
    """The innermost pipeline module (app.services.*) on the stack: which stage made the call."""
    f = sys._getframe(1)
    while f is not None:
        mod = f.f_globals.get("__name__", "")
        if mod.startswith("app.services."):
            return mod.rsplit(".", 1)[-1]
        f = f.f_back
    return "unknown"


def payload_of(body: dict) -> Any:
    """The last user message's content, as JSON when it is JSON."""
    msgs = [m for m in body.get("messages", []) if m.get("role") == "user"]
    if not msgs:
        return None
    content = msgs[-1].get("content")
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return content


def line_keys_of(payload: Any) -> list[str]:
    if isinstance(payload, dict) and isinstance(payload.get("line_items"), list):
        return [li["key"] for li in payload["line_items"] if isinstance(li, dict) and li.get("key")]
    found: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("key", "canonical_key", "line_key") and isinstance(v, str) and "__" in v:
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(payload)
    return list(dict.fromkeys(found))


class Recorder:
    """Records every call into OUT, answers from OUT/answers, else abstains."""

    def __init__(self, out: Path):
        self.out = out
        self.answers = out / "answers"
        self.requests = out / "requests"
        self.system = out / "system"
        for d in (self.answers, self.requests, self.system):
            d.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict] = []

    def provider(self):
        rec = self

        class Standin:
            id = "standin"

            def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                                    max_tokens=2048):
                del temperature
                return rec.handle(system=system, messages=messages,
                                  response_schema=response_schema, max_tokens=max_tokens,
                                  stage=calling_stage())
        return Standin()

    def handle(self, *, system, messages, response_schema, max_tokens, stage):
        body = gateway_body(system=system, messages=messages, response_schema=response_schema,
                            max_tokens=max_tokens)
        rid = body_id(body)
        chars = len(json.dumps(body, ensure_ascii=False))
        payload = payload_of(body)
        record = {"id": rid, "stage": stage, "schema": response_schema.__name__,
                  "schema_module": response_schema.__module__, "line_keys": line_keys_of(payload),
                  "body": body, "chars": chars, "est_tokens": chars // EST_CHARS_PER_TOKEN}
        self._write_request(record)
        hit = self.answers / f"{rid}.txt"
        answered = hit.exists()
        self.calls.append({k: record[k] for k in ("id", "stage", "schema", "line_keys", "chars",
                                                  "est_tokens")} | {"answered": answered})
        if answered:
            return parse_answer(hit.read_text(encoding="utf-8"), response_schema), {"model": "standin"}
        return response_schema.model_validate({}), {"model": "standin-abstain"}

    def _write_request(self, record: dict) -> None:
        rid = record["id"]
        (self.requests / f"{rid}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        sys_text = record["body"].get("system", "")
        sh = hashlib.sha1(sys_text.encode("utf-8")).hexdigest()[:12]
        sp = self.system / f"{sh}.md"
        if not sp.exists():
            sp.write_text(f"# System prompt {sh}\n\n```\n{sys_text}\n```\n", encoding="utf-8")
        lines = [f"# Request {rid}", "",
                 f"- stage: `{record['stage']}`  schema: `{record['schema']}`",
                 f"- line keys: {', '.join(record['line_keys']) or '-'}",
                 f"- size: {record['chars']:,} chars, ~{record['est_tokens']:,} tokens",
                 f"- system prompt: [system/{sh}.md](../system/{sh}.md) (not repeated here)",
                 f"- max_tokens: {record['body'].get('max_tokens')}", ""]
        for i, m in enumerate(record["body"].get("messages", [])):
            content = m.get("content")
            try:
                content = json.dumps(json.loads(content), ensure_ascii=False, indent=1)
            except (TypeError, ValueError):
                pass
            lines += [f"## message {i + 1} ({m.get('role')})", "", "```json", str(content), "```", ""]
        (self.requests / f"{rid}.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------------------------------------
# capture / replay
# ------------------------------------------------------------------------------------------------

def default_out(filing: Path, template: str) -> Path:
    tstem = template[:-len("_template.json")] if template.endswith("_template.json") else Path(template).stem
    return REPO / "_standin" / f"{filing.stem}__{tstem}"


def run_capture(filing: Path, template: str, line_set: str, out: Path, focus: str = "config") -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="llm-standin-"))
    os.environ.setdefault("FINEX_DATABASE_URL", f"sqlite:///{scratch}/run.db")
    os.environ.setdefault("FINEX_OBJECT_STORE_ROOT", str(scratch / "objects"))

    from app.config import get_settings, pin_llm
    pin_llm(provider="standin", model="standin", base_url="http://standin.invalid")
    from app.ports.registry import registry
    rec = Recorder(out)
    registry.register("llm", "standin", rec.provider)

    from app.api.routes.extractions import _serialize_rows
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_template
    from app.services import rollups
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    tpl_raw = json.loads((TEMPLATES / template).read_text(encoding="utf-8"))
    cfg = load_line_item_set(json.loads((TEMPLATES / line_set).read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    if focus == "all":
        settings.extraction.llm_focus_only = False

    doc, ctx = run_extraction(filing.read_bytes(), filename=filing.name, ontology=build_working_view(cfg),
                              template=load_template(tpl_raw), line_items=cfg)
    run = out / "run"
    run.mkdir(parents=True, exist_ok=True)
    rows = _serialize_rows(doc)
    _dump(run / "rows.json", rows)
    _dump(run / "spread.json", {f"{b}|{p}": rollups.figures_as_shown(tpl_raw, rows, b, p)
                                for b in ("standalone", "consolidated") for p in ("current", "prior")})
    (run / "log.txt").write_text("\n".join(ctx.logs), encoding="utf-8")
    st = getattr(doc, "structural", None)
    _dump(run / "structural.json", [r.model_dump(mode="json") for r in (getattr(st, "results", None) or [])])
    _dump(run / "notes.json", [n.model_dump(mode="json") if hasattr(n, "model_dump") else dict(vars(n))
                               for n in doc.notes or []])
    _dump(run / "pages.json", [{"index": p.index, "kind": _enum(getattr(p, "kind", "")),
                                "statement": _enum(getattr(p, "statement", "") or ""),
                                "scope": _enum(getattr(p, "scope", "") or ""),
                                "title": (getattr(p, "evidence", None) or {}).get("matched_title")}
                               for p in doc.pages])
    index = {"filing": filing.name, "template": template, "set": line_set, "focus": focus,
             "calls": len(rec.calls), "answered": sum(c["answered"] for c in rec.calls),
             "pending": sum(not c["answered"] for c in rec.calls),
             "by_stage": dict(Counter(c["stage"] for c in rec.calls)), "requests": rec.calls}
    _dump(out / "index.json", index)
    return index


def _enum(v) -> str:
    return str(getattr(v, "value", v))


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def cmd_capture(a) -> int:
    out = a.out or default_out(a.filing, a.template)
    idx = run_capture(a.filing, a.template, a.set, out, a.focus)
    print(f"{out}: {idx['calls']} requests ({idx['answered']} answered, {idx['pending']} pending) "
          f"by stage {idx['by_stage']}")
    return 0


def cmd_replay(a) -> int:
    """Re-run with the answers until a round records no new pending request (at most 3 rounds)."""
    prev = a.out / "index.json"
    seen = {c["id"] for c in json.loads(prev.read_text(encoding="utf-8"))["requests"]} if prev.exists() else set()
    for rnd in range(1, 4):
        r = subprocess.run([sys.executable, __file__, "capture", str(a.filing), "--template", a.template,
                            "--set", a.set, "--out", str(a.out), "--focus", a.focus],
                           capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-2000:])
            return r.returncode
        idx = json.loads(prev.read_text(encoding="utf-8"))
        pending = {c["id"] for c in idx["requests"] if not c["answered"]}
        new = pending - seen
        print(f"round {rnd}: {idx['calls']} requests, {idx['answered']} answered, "
              f"{len(pending)} pending, {len(new)} new pending")
        seen |= {c["id"] for c in idx["requests"]}
        if not new:
            break
    return 0


# ------------------------------------------------------------------------------------------------
# lint
# ------------------------------------------------------------------------------------------------

LINT_CHECKS = ("rows_not_from_statement", "period_label_unclear", "fragment_as_row", "no_unit",
               "duplicate_caption", "note_not_supplied", "entity_doubtful", "oversize")
_TRAILING = {"₹", "rs", "rs.", "in", "of", "to", "for", "from", "at", "by", "on", "with", "and",
             "or", "the", "a", "an", "under", "as", "towards", "including", "less", "net"}
_NOTE_REF = re.compile(r"\bnotes?\b|附注|附註", re.I)
_NOT_A_NOTE = re.compile(r"\bnot (?:in|from) (?:a|any|the) notes?\b", re.I)  # "its figure is not in a note"
_UNIT_KEYS = {"unit", "units", "scale", "currency", "unit_scale", "amounts_in"}


def _num(x) -> float | None:
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("(", "-").replace(")", ""))
    except ValueError:
        return None


def _has_figure(row: dict) -> bool:
    return any(_num(v) is not None for v in (row.get("figures") or {}).values())


def _blocks(payload: dict):
    """(kind, label, block) for each block of rows: statement_rows, notes, other_pages."""
    for b in payload.get("statement_rows") or []:
        yield "statement_rows", f"statement_rows[{b.get('statement')}/{b.get('entity')}]", b
    for b in payload.get("notes") or []:
        yield "notes", f"note {b.get('note')} '{b.get('title')}'", b
    for b in payload.get("other_pages") or []:
        if isinstance(b, dict) and isinstance(b.get("rows"), list):
            yield "other_pages", f"other_pages[{b.get('page', b.get('title'))}]", b


def _mentions_unit(payload) -> bool:
    if isinstance(payload, dict):
        return any(k in _UNIT_KEYS or _mentions_unit(v) for k, v in payload.items())
    if isinstance(payload, list):
        return any(_mentions_unit(v) for v in payload)
    return False


def is_fragment(caption: str) -> str | None:
    c = (caption or "").strip()
    if not c:
        return None
    words = c.split()
    if len(words) > 18:
        return f"{len(words)} words"
    last = words[-1].lower().rstrip(",:;-")
    if last in _TRAILING or c.endswith(("₹", "-", ",", ":")):
        return f"ends with '{words[-1]}'"
    if c.count("(") > c.count(")"):
        return "unclosed '('"
    return None


class RunIndex:
    """What the run itself says about pages and rows, for matching a payload row back."""

    def __init__(self, run: Path):
        def load(n, d):
            p = run / n
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d
        self.pages = {p["index"]: p for p in load("pages.json", [])}
        self.rows = load("rows.json", [])
        self.face_bases: set[str] = set()
        for r in self.rows:
            for v in r.get("values") or []:
                pg = self.pages.get(((v.get("provenance") or {}).get("page_index")))
                if pg and pg.get("kind", "").upper().endswith("FACE") and v.get("basis"):
                    self.face_bases.add(v["basis"])

    def source_of(self, caption: str, figures: dict) -> tuple[dict, dict] | None:
        want = {round(n, 2) for n in (_num(x) for x in (figures or {}).values()) if n is not None}
        best = None
        for r in self.rows:
            if (r.get("source_label") or "").strip() != (caption or "").strip():
                continue
            for v in r.get("values") or []:
                n = _num(v.get("value_raw", v.get("value")))
                if n is not None and round(n, 2) in want:
                    return r, v
            best = best or (r, (r.get("values") or [{}])[0])
        return best


def lint_request(req: dict, run: RunIndex, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> list[dict]:
    hits: list[dict] = []
    payload = payload_of(req["body"])

    def hit(check, evidence):
        hits.append({"check": check, "evidence": evidence})

    if req.get("est_tokens", 0) > budget_tokens:
        hit("oversize", f"~{req['est_tokens']:,} tokens > budget {budget_tokens:,}")
    if not isinstance(payload, dict):
        return hits

    any_figures = False
    for kind, label, block in _blocks(payload):
        rows = [r for r in block.get("rows") or [] if isinstance(r, dict)]
        if any(_has_figure(r) for r in rows):
            any_figures = True
        cols = block.get("columns") or {}
        if cols.get("current") and cols.get("current") == cols.get("prior"):
            hit("period_label_unclear", f"{label}: columns current and prior both read '{cols['current']}'")
        with_current = sum(1 for r in rows if "current" in (r.get("figures") or {}))
        by_caption: dict[str, set] = defaultdict(set)
        for r in rows:
            cap, figs = r.get("caption", ""), r.get("figures") or {}
            odd = [k for k in figs if k not in ("current", "prior")]
            if odd:
                hit("period_label_unclear", f"{label}: '{cap}' figures keyed {odd}: {json.dumps(figs, ensure_ascii=False)}")
            elif "prior" in figs and "current" not in figs and with_current:
                hit("period_label_unclear", f"{label}: '{cap}' has prior {figs['prior']} but no current")
            why = is_fragment(cap) if _has_figure(r) else None
            if why:
                hit("fragment_as_row", f"{label}: '{cap}' ({why}) carries {json.dumps(figs, ensure_ascii=False)}")
            if _has_figure(r):
                by_caption[cap].add(json.dumps(figs, sort_keys=True))
            if kind == "statement_rows" and _has_figure(r):
                src = run.source_of(cap, figs)
                if src:
                    row, val = src
                    pg = run.pages.get((val.get("provenance") or {}).get("page_index"))
                    pkind = (pg or {}).get("kind", "").upper()
                    if row.get("printed_in") == "notes" or pkind.endswith("NOTES"):
                        hit("rows_not_from_statement", f"{label}: '{cap}' comes from page "
                            f"{(pg or {}).get('index')} ({pkind or 'notes'}, printed_in {row.get('printed_in')})")
                    elif pg and pg.get("statement") and pg["statement"] != block.get("statement"):
                        hit("rows_not_from_statement", f"{label}: '{cap}' comes from page {pg['index']} "
                            f"classified as {pg['statement']}")
        for cap, figs in by_caption.items():
            if len(figs) > 1:
                hit("duplicate_caption", f"{label}: '{cap}' appears with {len(figs)} different figures: "
                    + " | ".join(sorted(figs)))
        ent = block.get("entity")
        if kind == "statement_rows" and ent and run.face_bases and ent not in run.face_bases:
            hit("entity_doubtful", f"{label}: entity '{ent}' but the run's faces are "
                f"{sorted(run.face_bases)}")
    if any_figures and not _mentions_unit(payload):
        hit("no_unit", "payload carries figures but states no unit, scale or currency anywhere")
    for li in payload.get("line_items") or []:
        text = f"{li.get('read_from') or ''} {li.get('definition') or ''}"
        if _NOTE_REF.search(_NOT_A_NOTE.sub("", text)) and not li.get("notes_supplied"):
            hit("note_not_supplied", f"{li.get('key')}: '{text.strip()[:160]}' but notes_supplied is empty")
    return hits


def load_requests(d: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((d / "requests").glob("*.json"))}


def current_ids(d: Path) -> list[str]:
    idx = d / "index.json"
    if idx.exists():
        return list(dict.fromkeys(c["id"] for c in json.loads(idx.read_text(encoding="utf-8"))["requests"]))
    return list(load_requests(d))


def cmd_lint(a) -> int:
    reqs = load_requests(a.dir)
    run = RunIndex(a.dir / "run")
    result = {rid: lint_request(reqs[rid], run, a.budget) for rid in current_ids(a.dir) if rid in reqs}
    _dump(a.dir / "lint.json", result)
    print(f"{len(result)} requests linted")
    print(f"{'check':<26}{'requests':>9}  examples")
    for check in LINT_CHECKS:
        ids = [rid for rid, hs in result.items() if any(h["check"] == check for h in hs)]
        print(f"{check:<26}{len(ids):>9}  {', '.join(ids[:3])}")
    print(f"{'(no hit)':<26}{sum(1 for hs in result.values() if not hs):>9}")
    return 0


# ------------------------------------------------------------------------------------------------
# sample
# ------------------------------------------------------------------------------------------------

def _statements(req: dict) -> str:
    p = payload_of(req["body"])
    if isinstance(p, dict):
        s = sorted({li.get("statement") or "-" for li in p.get("line_items") or []})
        return ",".join(s) or "-"
    return "-"


def pick_sample(reqs: dict[str, dict], lint: dict[str, list], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    strata: dict[tuple, list[str]] = defaultdict(list)
    for rid in sorted(reqs):
        checks = tuple(sorted({h["check"] for h in lint.get(rid, [])})) or ("clean",)
        strata[(reqs[rid]["stage"], _statements(reqs[rid]), checks)].append(rid)
    for v in strata.values():
        rng.shuffle(v)
    picked: list[str] = []
    clean = [k for k in strata if k[2] == ("clean",)]
    for k in clean[: max(1, n // 4)]:              # always some with no lint hit
        if strata[k] and len(picked) < n:
            picked.append(strata[k].pop())
    keys = sorted(strata)
    rng.shuffle(keys)
    while len(picked) < n and any(strata.values()):
        for k in keys:
            if strata[k] and len(picked) < n:
                picked.append(strata[k].pop())
    return [{"id": rid, "stage": reqs[rid]["stage"], "statements": _statements(reqs[rid]),
             "line_keys": reqs[rid]["line_keys"],
             "lint": sorted({h["check"] for h in lint.get(rid, [])})} for rid in picked]


def cmd_sample(a) -> int:
    reqs = load_requests(a.dir)
    ids = current_ids(a.dir)
    reqs = {k: reqs[k] for k in ids if k in reqs}
    lp = a.dir / "lint.json"
    lint = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
    picked = pick_sample(reqs, lint, a.n, a.seed)
    _dump(a.dir / "sample.json", {"seed": a.seed, "n": a.n, "ids": picked})
    for p in picked:
        print(f"{p['id']}  {p['stage']:<16} {p['statements']:<28} {','.join(p['lint']) or 'clean'}")
    return 0


# ------------------------------------------------------------------------------------------------
# validate
# ------------------------------------------------------------------------------------------------

PROBLEM_KINDS = Literal["rows_not_from_statement", "period_label_unclear", "fragment_as_row", "no_unit",
                        "duplicate_caption", "note_not_supplied", "definition_unclear",
                        "definition_conflicts_contract", "entity_unclear", "truncated", "other"]
VERDICTS = Literal["answerable", "answerable_with_assumption", "not_answerable"]


class Found(BaseModel):
    block: str
    caption: str
    figures: dict[str, Any]


class PerLine(BaseModel):
    key: str
    needed: str
    found: Found | None = None
    verdict: VERDICTS
    assumption: str | None = None


class Problem(BaseModel):
    kind: PROBLEM_KINDS
    detail: str


class AuditRecord(BaseModel):
    id: str
    line_keys: list[str]
    verdict: VERDICTS
    per_line: list[PerLine] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
    would_need: str = ""
    confidence: float = Field(ge=0, le=1)


class KeyLine(BaseModel):
    current: str | None = None
    prior: str | None = None
    page: int | None = None
    where: str | None = None
    comment: str | None = None


class Key(BaseModel):
    filing: str
    template: str | None = None
    unit: str | None = None
    basis: str = "standalone"
    lines: dict[str, KeyLine]


def load_key(path: Path) -> Key:
    """The key, accepting the scratch shape too (numbers, `units`, `source`, `confidence`)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    lines = {}
    for k, v in (raw.get("lines") or {}).items():
        v = dict(v or {})  # `null`: the filing prints nothing for this line
        for p in ("current", "prior"):
            if v.get(p) is not None and not isinstance(v[p], str):
                v[p] = repr(float(v[p])) if isinstance(v[p], float) else str(v[p])
        if "where" not in v and "source" in v:
            v["where"] = v.pop("source")
        if "comment" not in v and "confidence" in v:
            v["comment"] = f"confidence: {v.pop('confidence')}"
        lines[k] = {f: v.get(f) for f in KeyLine.model_fields}
    return Key(filing=raw.get("filing", path.stem), template=raw.get("template"),
               unit=raw.get("unit", raw.get("units")), basis=raw.get("basis", "standalone"), lines=lines)


def schema_class(req: dict):
    return getattr(importlib.import_module(req["schema_module"]), req["schema"])


def cmd_validate(a) -> int:
    reqs = load_requests(a.dir)
    fails = 0
    for p in sorted((a.dir / "answers").glob("*.txt")):
        req = reqs.get(p.stem)
        if req is None:
            print(f"answers/{p.name}: no recorded request with this id"); fails += 1
            continue
        try:
            parse_answer(p.read_text(encoding="utf-8"), schema_class(req))
        except Exception as e:  # noqa: BLE001 - report every failure
            print(f"answers/{p.name}: {str(e).splitlines()[0][:200]}"); fails += 1
    for p in sorted((a.dir / "audit").glob("*.json")) if (a.dir / "audit").exists() else []:
        try:
            AuditRecord.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"audit/{p.name}: {str(e).splitlines()[0][:200]}"); fails += 1
    if a.key:
        try:
            load_key(a.key)
        except Exception as e:  # noqa: BLE001
            print(f"key {a.key}: {str(e)[:300]}"); fails += 1
    print("ok" if not fails else f"{fails} failures")
    return 1 if fails else 0


# ------------------------------------------------------------------------------------------------
# report
# ------------------------------------------------------------------------------------------------

CAUSES = ("correct", "lost_in_context", "not_in_filing", "model_misread", "unanswered", "deterministic",
          "derived")
CODE_FOR = {
    "statement_rows": "app/services/face_context.py (statement_rows blocks)",
    "notes": "app/services/note_context.py (note blocks)",
    "note_not_supplied": "app/services/line_item_notes.py (which notes a line is given)",
    "payload": "app/services/line_item_llm.py (request payload: line_items, units, size)",
}


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(0.01, 0.005 * abs(b))


SUM_MAX_ROWS = 4  # a printed figure carried as the sum of at most this many rows of one block


def key_is_sum(key_line: KeyLine) -> bool:
    """The key says the line is printed as parts ('A + B', or computed from other lines)."""
    w = (key_line.where or "").lower()
    return "+" in w or w.startswith("computed")


def payload_has(req: dict, figure: float, parts: bool = False) -> list[str]:
    """Where the request carries this figure: a row printing it, compared by magnitude so a
    bracketed '(519.65)' carries 519.65. With `parts` (the key says the line is 'A + B'), also rows
    in one period column that add up to it to the cent: two rows anywhere in the request, or up to
    SUM_MAX_ROWS rows of one block."""
    p = payload_of(req["body"])
    out: list[str] = []
    if not isinstance(p, dict):
        return out
    target = round(abs(figure), 2)
    allcols: dict[str, list[tuple[str, float]]] = {}
    sums: list[str] = []
    for kind, label, b in _blocks(p):
        cols: dict[str, list[tuple[str, float]]] = {}
        for r in b.get("rows") or []:
            for col, v in (r.get("figures") or {}).items():
                n = _num(v)
                if n is None:
                    continue
                if _close(abs(n), target):
                    out.append(f"{label}: '{r.get('caption')}'")
                elif n and abs(n) < target:
                    cols.setdefault(col, []).append((f"{label}: '{r.get('caption')}'", round(abs(n), 2)))
        for col, vals in cols.items():
            allcols.setdefault(col, []).extend(vals)
            hit = parts and _sum_of(vals, target, SUM_MAX_ROWS)
            if hit:
                sums.append(" + ".join(hit) + f" ({col})")
    if parts and not sums:
        for col, vals in allcols.items():
            hit = _sum_of(vals, target, 2)
            if hit:
                sums.append(" + ".join(hit) + f" ({col})")
    return list(dict.fromkeys(out + [f"sum of {x}" for x in sums[:1]]))


def _sum_of(vals: list[tuple[str, float]], target: float, most: int) -> list[str] | None:
    from itertools import combinations
    for n in range(2, most + 1):
        for combo in combinations(vals[:60], n):
            if abs(round(sum(v for _, v in combo), 2) - target) < 0.005:
                return [c for c, _ in combo]
    return None


def prose_has(req: dict, figure: float) -> list[str]:
    """Blocks whose prose (not rows) prints the figure: the table reached the model only as text."""
    p = payload_of(req["body"])
    out = []
    if isinstance(p, dict):
        for kind, label, b in _blocks(p):
            prose = str(b.get("prose") or "")
            if any(_close(abs(n), abs(figure)) for n in
                   (_num(t) for t in re.findall(r"\(?[\d,]+\.\d+\)?", prose)) if n is not None):
                out.append(label)
    return out


# A check that cannot take a figure out of the payload; it is not named as the cause of a figure lost.
NOT_A_LOSS = {"no_unit"}


def classify(key_line: KeyLine, period: str, published: float | None, asking: list[dict],
             answered: set[str], lint: dict[str, list], audits: dict[str, dict]) -> tuple[str, str]:
    printed = _num(getattr(key_line, period))
    if printed is None:
        return ("correct", "filing prints nothing; run published nothing") if published is None else \
               ("not_in_filing", f"filing prints nothing; run published {published}")
    if _close(published, printed):
        return "correct", ""
    if not asking:
        return "deterministic", "the line was not asked"
    if (key_line.where or "").lower().startswith("computed"):
        # no page prints it: the key adds other lines, so the miss follows those lines, not a payload
        return "derived", f"the key computes it ({(key_line.where or '')[:80]}); see its parts"
    had = [(r["id"], b) for r in asking for b in payload_has(r, printed, key_is_sum(key_line))]
    if had:
        rid, where = had[0]
        if rid in answered or any(r["id"] in answered for r in asking):
            return "model_misread", f"payload {rid} had it at {where}"
        return "unanswered", f"payload {rid} had it at {where}; no answer recorded"
    causes = sorted(({h["check"] for r in asking for h in lint.get(r["id"], [])}
                     | {pr["kind"] for r in asking for pr in (audits.get(r["id"]) or {}).get("problems", [])})
                    - NOT_A_LOSS)
    prose = [(r["id"], b) for r in asking for b in prose_has(r, printed)]
    tail = f"; in prose only: {prose[0][1]} ({prose[0][0]})" if prose else ""
    return "lost_in_context", "faults: " + (", ".join(causes) or "none detected (figure absent from payload)") + tail


def fix_site(check: str) -> str:
    if check == "note_not_supplied":
        return CODE_FOR["note_not_supplied"]
    if check in ("rows_not_from_statement", "entity_doubtful", "entity_unclear"):
        return CODE_FOR["statement_rows"]
    if check in ("no_unit", "oversize", "truncated", "definition_unclear", "definition_conflicts_contract"):
        return CODE_FOR["payload"]
    return f"{CODE_FOR['statement_rows']} or {CODE_FOR['notes']}"


def build_report(d: Path, key: Key | None) -> str:
    reqs = load_requests(d)
    ids = [i for i in current_ids(d) if i in reqs]
    idx = json.loads((d / "index.json").read_text(encoding="utf-8")) if (d / "index.json").exists() else {}
    answered = {c["id"] for c in idx.get("requests", []) if c.get("answered")} or \
               {p.stem for p in (d / "answers").glob("*.txt")}
    lint = json.loads((d / "lint.json").read_text(encoding="utf-8")) if (d / "lint.json").exists() else {}
    audits = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (d / "audit").glob("*.json")} \
        if (d / "audit").exists() else {}
    out = [f"# Stand-in report: {d.name}", "", "## Requests", ""]
    sizes = sorted(reqs[i]["est_tokens"] for i in ids)
    out.append(f"- total {len(ids)}, answered {sum(i in answered for i in ids)}, "
               f"pending {sum(i not in answered for i in ids)}")
    for st, n in Counter(reqs[i]["stage"] for i in ids).most_common():
        out.append(f"- stage `{st}`: {n}")
    if sizes:
        out.append(f"- est. tokens: min {sizes[0]:,}, median {sizes[len(sizes) // 2]:,}, max {sizes[-1]:,}, "
                   f"total {sum(sizes):,}")
    out += ["", "## Lint", "", "| check | requests | example |", "| --- | ---: | --- |"]
    for check in LINT_CHECKS:
        evs = [(rid, h["evidence"]) for rid in ids for h in lint.get(rid, []) if h["check"] == check]
        rq = len({r for r, _ in evs})
        ex = "<br>".join(f"`{r}` {e[:160]}".replace("|", "\\|") for r, e in evs[:2])
        out.append(f"| {check} | {rq} | {ex} |")
    out += ["", "## Blind audit", ""]
    if audits:
        out.append("- verdicts: " + ", ".join(f"{k} {v}" for k, v in
                                               Counter(a["verdict"] for a in audits.values()).most_common()))
        kinds = Counter(p["kind"] for a in audits.values() for p in a.get("problems", []))
        out += ["", "| problem | count | example |", "| --- | ---: | --- |"]
        for kind, n in kinds.most_common():
            ex = next(f"`{a['id']}` {p['detail'][:160]}" for a in audits.values()
                      for p in a.get("problems", []) if p["kind"] == kind)
            out.append(f"| {kind} | {n} | {ex.replace('|', chr(92) + '|')} |")
        out += ["", "What the answerer said it would need:", ""]
        out += [f"- `{a['id']}`: {a['would_need']}" for a in audits.values() if a.get("would_need")]
    else:
        out.append("No audit records yet (`audit/<id>.json`).")
    if key:
        spread = json.loads((d / "run" / "spread.json").read_text(encoding="utf-8"))
        table: list[str] = []
        tally: Counter = Counter()
        faults: Counter = Counter()
        for k, kl in key.lines.items():
            asking = [reqs[i] for i in ids if k in reqs[i]["line_keys"]]
            for period in ("current", "prior"):
                pub = (spread.get(f"{key.basis}|{period}") or {}).get(k)
                cause, detail = classify(kl, period, _num(pub), asking, answered, lint, audits)
                tally[cause] += 1
                if cause == "lost_in_context":
                    faults.update(c for c in detail.removeprefix("faults: ").split("; ")[0].split(", ")
                                  if not c.startswith("none"))
                if cause != "correct":
                    where = f"p{kl.page} " if kl.page else ""
                    table.append(f"| {k} | {period} | {getattr(kl, period)} | {where}{(kl.where or '')[:60]} | "
                                 f"{pub} | {cause} | {detail[:120]} |".replace("\n", " "))
        out += ["", "## Against the key", "",
                "- " + ", ".join(f"{c} {tally[c]}" for c in CAUSES if tally[c]), "",
                "| line | period | printed | page / where | published | cause | detail |",
                "| --- | --- | ---: | --- | ---: | --- | --- |"] + table
        out += ["", "## Top fixes", ""]
        if faults:
            for c, n in faults.most_common(5):
                out.append(f"- `{c}`: {n} misses. Code: {fix_site(c)}")
        else:
            out.append("No miss was traced to a named context fault.")
    return "\n".join(out) + "\n"


def cmd_report(a) -> int:
    text = build_report(a.dir, load_key(a.key) if a.key else None)
    (a.dir / "report.md").write_text(text, encoding="utf-8")
    print(f"wrote {a.dir / 'report.md'}")
    return 0


# ------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("capture", "replay"):
        p = sub.add_parser(name)
        p.add_argument("filing", type=Path)
        p.add_argument("--template", required=True)
        p.add_argument("--set", required=True)
        p.add_argument("--out", type=Path, required=(name == "replay"))
        p.add_argument("--focus", default="config", choices=["config", "all"])
    p = sub.add_parser("lint"); p.add_argument("dir", type=Path)
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS, help="est. tokens per request")
    p = sub.add_parser("sample"); p.add_argument("dir", type=Path)
    p.add_argument("--n", type=int, default=12); p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("validate"); p.add_argument("dir", type=Path); p.add_argument("--key", type=Path)
    p = sub.add_parser("report"); p.add_argument("dir", type=Path); p.add_argument("--key", type=Path)
    a = ap.parse_args(argv)
    return {"capture": cmd_capture, "replay": cmd_replay, "lint": cmd_lint, "sample": cmd_sample,
            "validate": cmd_validate, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
