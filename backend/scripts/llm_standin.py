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
import shutil
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

def _block_text(content):
    """A content given as text blocks, as the one string they spell; anything else as it is."""
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "text" for b in content):
        return "".join(str(b.get("text") or "") for b in content)
    return content


def canonical_body(body: dict) -> dict:
    """The body as the model reads it: every prompt-cache breakpoint taken out.

    The gateway sends the system text, and a message marked for the cache, as a text block ending in
    `cache_control` (`adapters._structured.messages_body`). A breakpoint tells the provider what it
    may cache and changes nothing the model is asked, so a request is RECORDED AND KEYED by this
    form: the string the blocks spell, as the body was before the breakpoints existed. A request
    whose words are unchanged keeps its id, and the answer written for it."""
    out = dict(body)
    if "system" in out:
        out["system"] = _block_text(out["system"])
    out["messages"] = [{**m, "content": _block_text(m.get("content"))}
                       for m in body.get("messages", [])]
    return out


def cache_breakpoints(body: dict) -> list[str]:
    """Where the gateway body asks the provider to cache: "system", "message 1", …"""
    def marked(content) -> bool:
        return isinstance(content, list) and any(
            isinstance(b, dict) and b.get("cache_control") for b in content)
    out = ["system"] if marked(body.get("system")) else []
    out += [f"message {i + 1}" for i, m in enumerate(body.get("messages", []))
            if marked(m.get("content"))]
    return out


def body_id(body: dict) -> str:
    return hashlib.sha1(json.dumps(canonical_body(body), sort_keys=True, ensure_ascii=False)
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


def _content_of(message: dict, *, lead_in: bool = False) -> Any:
    """A message's content, as JSON when it is JSON. `lead_in`: a document message opens with one
    line of prose before its JSON (`line_item_llm.document_message`, `report_message`)."""
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        pass
    if lead_in and isinstance(content, str) and "\n" in content:
        try:
            return json.loads(content.split("\n", 1)[1])
        except (TypeError, ValueError):
            pass
    return content


def payload_of(body: dict) -> Any:
    """The request's payload: the last user message's content, as JSON when it is JSON.

    A REQUEST THAT SENDS A DOCUMENT FIRST — the whole annual report an `anywhere` line is asked
    with, or the whole document of `llm_document_context = "full"` — carries its notes, statements
    and pages in that first message and only the lines in the last. Its blocks are merged in under
    the question's, so a reader looking a cited row up (the grader, the lint) finds it whichever
    message carried it."""
    msgs = [m for m in body.get("messages", []) if m.get("role") == "user"]
    if not msgs:
        return None
    last = _content_of(msgs[-1])
    if isinstance(last, dict) and len(msgs) > 1:
        merged: dict = {}
        for m in msgs[:-1]:
            doc = _content_of(m, lead_in=True)
            if isinstance(doc, dict):
                merged.update(doc)
        if merged:
            return {**merged, **last}
    return last


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
        # A capture records ONE run: requests a previous capture left in a reused folder would be
        # read as this run's. Answers and audits are kept — a replay answers from them.
        for d in (self.requests, self.system):
            if d.exists():
                shutil.rmtree(d)
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
        sent = gateway_body(system=system, messages=messages, response_schema=response_schema,
                            max_tokens=max_tokens)
        # RECORDED IN ITS CANONICAL FORM (`canonical_body`): what the model reads, the key it is
        # answered under, and the shape every reader of a request file expects. Where the gateway
        # body asks for the prompt cache is kept beside it.
        body = canonical_body(sent)
        rid = body_id(body)
        chars = len(json.dumps(body, ensure_ascii=False))
        payload = payload_of(body)
        record = {"id": rid, "stage": stage, "schema": response_schema.__name__,
                  "schema_module": response_schema.__module__, "line_keys": line_keys_of(payload),
                  "body": body, "chars": chars, "est_tokens": chars // EST_CHARS_PER_TOKEN,
                  "cache_breakpoints": cache_breakpoints(sent)}
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
# A heading's enumeration printed INSIDE a caption, after its first word: "… income A. (i) Items".
_INNER_ENUMERATOR = re.compile(r"\S\s+(?:[IVX]{1,5}\.|[A-H]\.|\((?:[ivx]{1,4}|[a-h])\))\s")
# A REFERENCE TO A NOTE of the financial statements, not the word: "Notes Payable(CP)" is "bills
# payable and short-term notes or commercial paper … not guaranteed notes, senior notes", and every
# HK filing's request for it was flagged note_not_supplied for the instrument's name. A reference
# is a note number ("note 12", "Note (3)"), a pointer ("see note", "refer to the notes"), a note a
# determiner names ("the note", "a row of one of its notes", "the related-party note", "the
# accounting-policy note"), or 附注. The instrument's names are read out first (`_NOTE_INSTRUMENT`)
# so "the current portion of long-term notes" is not "the … notes".
_NOTE_INSTRUMENT = re.compile(
    r"\bnotes?\s+(?:payable|receivable|issued|due|programme|program)\b"
    r"|\b(?:short|medium|long)[- ]term\s+notes?\b"
    r"|\b(?:guaranteed|senior|junior|convertible|exchangeable|promissory|subordinated|unsecured|"
    r"secured|perpetual|commercial|bank|treasury|credit[- ]linked|structured|fixed[- ]rate|"
    r"floating[- ]rate|green|capital)\s+notes?\b"
    r"|\b(?:bills?|bonds?|debentures?)\s+(?:and|or|,)\s+notes?\b"
    r"|\bnotes?\s+(?:and|or)\s+(?:bills?|bonds?|debentures?|commercial paper)\b", re.I)
_NOTE_REF = re.compile(
    r"附注|附註"
    r"|\bnotes?\s*(?:no\.?\s*)?[(（]?\s*(?:\d|[ivx]{1,4}\b|[a-z][)）])"   # note 12, Note (3), note (a)
    r"|\b(?:see|refer(?:s|red|ring)?\s+to|per|cf\.?)\s+(?:the\s+)?notes?\b"
    r"|\b(?:the|a|an|its|their|this|that|each|any|every|one\s+of\s+(?:its|the|their))\s+"
    r"(?:(?!of\b|not\b)[\w'’,/-]+\s+){0,6}?notes?\b", re.I)
_NOT_A_NOTE = re.compile(r"\bnot (?:in|from) (?:a|any|the) notes?\b", re.I)  # "its figure is not in a note"


def refers_to_a_note(text: str) -> bool:
    """Whether a line's `read_from` / `definition` points at a note of the financial statements."""
    return bool(_NOTE_REF.search(_NOTE_INSTRUMENT.sub(" ", _NOT_A_NOTE.sub(" ", text or ""))))
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
    # A LONG CAPTION IS A JOIN only when it shows the seam: a heading's own numbering inside it
    # ("X. Other comprehensive income A. (i) Items …") or a bracket it never closes. f1 prints
    # single captions of 19-30 words — "Estimated amount of contracts remaining to be executed on
    # capital account and not provided for (net of capital advances)", the dividend sentences of
    # note 36 — and those are what the filing prints, not fragments.
    if len(words) > 18 and (_INNER_ENUMERATOR.search(c) or c.count("(") != c.count(")")):
        return f"{len(words)} words"
    last = words[-1].lower().rstrip(",:;-")
    # "Other operating expenses, net" is the whole caption: "net" closes it after a comma or a
    # bracket. Measured: 180+ such rows on hkex_0160 were flagged as fragments.
    if last == "net" and len(words) > 1 and words[-2].endswith((",", ")")) and c.endswith("net"):
        return None
    if last in _TRAILING or c.endswith(("₹", "-", ",", ":")):
        return f"ends with '{words[-1]}'"
    if c.count("(") > c.count(")"):
        return "unclosed '('"
    return None


def heads_its_own_caption(caption: str, group: str | None) -> str | None:
    """A caption that still opens with the heading it is sent under, a new caption following it —
    "Gross carrying amount Balance as at 1 April 2023" in the group "Gross carrying amount": the
    heading was joined onto the row and also given as its group."""
    head = str(group or "").split(" / ")[-1].strip().rstrip(":：")
    c = (caption or "").strip()
    if not head or len(c) <= len(head) or not c.casefold().startswith(head.casefold() + " "):
        return None
    # …AND A NEW CAPTION STARTS AFTER IT. "Current tax on profits for the year" under "Current tax:"
    # names its group in its own words; "Balance" after "Gross carrying amount" is a second caption.
    rest = c[len(head):].lstrip()
    return f"opens with its group '{head}'" if rest[:1].isupper() else None


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

    def source_of(self, caption: str, figures: dict, statement: str = "") -> tuple[dict, dict] | None:
        """The run's row a block row was sent from: same caption, one of its figures.

        A caption and figure printed on two statements — "Loss before taxation" on the income
        statement and at the head of the indirect cash flow, a dividend paid on the cash flow and
        in the changes in equity — is two rows, and the one printed on `statement` is the source.
        """
        want = {round(n, 2) for n in (_num(x) for x in (figures or {}).values()) if n is not None}
        best = None
        found = []
        for r in self.rows:
            if (r.get("source_label") or "").strip() != (caption or "").strip():
                continue
            for v in r.get("values") or []:
                n = _num(v.get("value_raw", v.get("value")))
                if n is not None and round(n, 2) in want:
                    found.append((r, v))
                    break
            best = best or (r, (r.get("values") or [{}])[0])
        for r, v in found:
            pg = self.pages.get((v.get("provenance") or {}).get("page_index")) or {}
            if statement and (pg.get("statement") == statement
                              or f"printed_on:{statement}" in (r.get("flags") or [])):
                return r, v
        return found[0] if found else best


_KEY_YEAR = re.compile(r"(?:19|20)\d{2}")


def _key_names_period(key: str) -> bool:
    """A figure key that is a printed heading naming its period — "期末余额", "上期发生额 · 收入",
    "31 December 2024", "2024" — or a period's variant ("current:net"): not an unlabelled one."""
    if key.split(":", 1)[0] in ("current", "prior"):
        return True
    from app.services import note_columns
    return bool(note_columns.relative_slot(key)[0] or note_columns._date_of(key)
                or _KEY_YEAR.fullmatch(key.strip()))


def _x1(v: dict) -> float | None:
    box = (v.get("provenance") or {}).get("bbox") or {}
    return box.get("x1")


def _prior_column_x(rows: list[dict], run: "RunIndex", statement: str) -> float | None:
    """Where the block's prior figures are printed: the median right edge of the prior value of
    rows that print both years."""
    xs = []
    for r in rows:
        figs = r.get("figures") or {}
        if not ("current" in figs and "prior" in figs):
            continue
        src = run.source_of(r.get("caption", ""), figs, statement)
        if src:
            xs += [x for v in src[0].get("values") or [] if v.get("period_label") == "prior"
                   for x in [_x1(v)] if x is not None]
    xs.sort()
    return xs[len(xs) // 2] if xs else None


def _in_prior_column(cap: str, figs: dict, run: "RunIndex", statement: str,
                     prior_x: float | None) -> bool:
    """A statement row printing only its comparative — the current year printed nil ("–") — is
    the filing's own shape, not a mislabel, when its figure stands in the prior column."""
    if prior_x is None:
        return False
    src = run.source_of(cap, figs, statement)
    x = _x1(src[1]) if src else None
    return x is not None and abs(x - prior_x) <= 0.015


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
        prior_x = (_prior_column_x(rows, run, block.get("statement") or "")
                   if kind == "statement_rows" else None)
        by_caption: dict[str, set] = defaultdict(set)
        for r in rows:
            cap, figs = r.get("caption", ""), r.get("figures") or {}
            # A row of a table whose columns are not periods states its own `period` and keys its
            # figures by the printed column (`note_columns.table_layout`); its keys are not periods.
            odd = ([] if "period" in r else [k for k in figs if k not in ("current", "prior")
                                               and not _key_names_period(k)])
            if "period" in r and r["period"] == "unstated" and _has_figure(r):
                hit("period_label_unclear", f"{label}: '{cap}' period unstated: "
                    f"{json.dumps(figs, ensure_ascii=False)}")
            if odd:
                hit("period_label_unclear", f"{label}: '{cap}' figures keyed {odd}: {json.dumps(figs, ensure_ascii=False)}")
            elif ("prior" in figs and "current" not in figs and with_current
                  and not _in_prior_column(cap, figs, run, block.get("statement") or "", prior_x)):
                hit("period_label_unclear", f"{label}: '{cap}' has prior {figs['prior']} but no current")
            why = (is_fragment(cap) or heads_its_own_caption(cap, r.get("group"))
                   if _has_figure(r) else None)
            if why:
                hit("fragment_as_row", f"{label}: '{cap}' ({why}) carries {json.dumps(figs, ensure_ascii=False)}")
            if _has_figure(r):
                # A ROW IS TOLD APART BY THE HEADING IT IS PRINTED UNDER: a note row's `group`, a
                # statement row's `under` (the section banner) and `subheading`. Borrowings under
                # non-current and under current liabilities are two rows, and the request says so.
                place = " / ".join(t for t in (r.get("under", ""), r.get("subheading", ""),
                                               r.get("group", ""), r.get("period", ""), cap) if t)
                by_caption[place].add(json.dumps(figs, sort_keys=True))
            if kind == "statement_rows" and _has_figure(r):
                src = run.source_of(cap, figs, block.get("statement") or "")
                if src:
                    row, val = src
                    pg = run.pages.get((val.get("provenance") or {}).get("page_index"))
                    pkind = (pg or {}).get("kind", "").upper()
                    if row.get("printed_in") == "notes" or pkind.endswith("NOTES"):
                        hit("rows_not_from_statement", f"{label}: '{cap}' comes from page "
                            f"{(pg or {}).get('index')} ({pkind or 'notes'}, printed_in {row.get('printed_in')})")
                    elif (pg and pg.get("statement") and pg["statement"] != block.get("statement")
                          # A PAGE THAT HOLDS TWO STATEMENTS stamps each row with the one it was
                          # printed under (`buckets.PRINTED_ON_FLAG`); the page's own verdict is the
                          # statement that begins on it, and a row above that title is not its.
                          and f"printed_on:{block.get('statement')}" not in (row.get("flags") or [])):
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
        if refers_to_a_note(text) and not li.get("notes_supplied"):
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
    """Where the auditor found a line. Not always a row of figures: a text line (a share-capital
    caption whose counts are in `share_counts`, a movement-schedule heading such as "（1）计提"), a
    sentence of prose (`caption` blank), or a block named with no caption — or only a line of
    text. Twelve carried records in eleven stand-in folders (000883's 1eb90d6005d3596f lineage
    among them) hold such blocks and failed `validate` on the missing `figures`."""
    model_config = {"extra": "allow"}
    block: str | None = None
    caption: str | None = None
    figures: dict[str, Any] | None = None


class PerLine(BaseModel):
    key: str
    needed: str
    found: Found | str | None = None
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
    # THE ADDENDS, SIGNED, PER PERIOD, for a line the key gives as parts too many or too small for
    # `where` to carry: {"current": ["2,046,232.67", "-44,598.86", ...], "prior": [...]}. `where`
    # reads grouped numbers only (so a note number is not an addend), which leaves out 966.91 and
    # an HKFRS note's 465; this states them. Preferred by `key_parts` when given.
    parts: dict[str, list[str]] | None = None
    # A TEXT LINE'S VALUE, as the filing prints it — the auditor's name, the opinion sentence. A
    # list where the filing prints the same value more than one way (the English and the Chinese
    # name; "Renminbi" and "RMB"), any of which is right. Graded by `grade_text`; a text line has
    # no `current`/`prior`, so coverage does not count it.
    text: str | list[str] | None = None


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


def _places(x) -> int:
    """The decimal places a figure is printed to: '387,791,885.96' → 2, '(3,532,249)' → 0. A number
    (a JSON figure, a float the script computed) states no printing, so its own digits are read:
    71610.0 → 0, 1901.5 → 1."""
    if x is None or isinstance(x, (bool, int)):
        return 0
    if isinstance(x, float):
        from decimal import Decimal
        if x != x or x in (float("inf"), float("-inf")):
            return 0
        digits = format(Decimal(repr(x)).normalize(), "f")
        return len(digits.split(".")[1]) if "." in digits else 0
    m = re.search(r"\.(\d+)", str(x))
    return len(m.group(1)) if m else 0


def _same(a: float | None, b: float | None, places_a: int = 2, places_b: int = 2,
          unit_a: float = 1.0, unit_b: float = 1.0) -> bool:
    """Whether two printed figures are the same figure: EXACTLY, at the coarser of the two
    printings, after each one's unit scale. No percentage tolerance — the 0.5% the grader used to
    allow graded a gross receivable 0.499% above the net one the answer cited as right.

    Each figure is on a grid of `unit × 10^-places` (CNY to the cent is 0.01; a figure printed in
    thousands with no decimals is 1,000). The finer one is read at the coarser one's grid, so the
    rounding a filing itself prints still compares: a key given as 3,532,249 equals a row printed
    3,532,249.40, and a key of 19,015,000.00 yuan equals 1,901.50 printed in 万元 (a grid of 100). Two
    figures on one grid compare exactly."""
    if a is None or b is None:
        return a is None and b is None
    step = max(unit_a * 10.0 ** -places_a, unit_b * 10.0 ** -places_b)
    return abs(a * unit_a - b * unit_b) <= step / 2 * (1 + 1e-9)


SUM_MAX_ROWS = 4  # a printed figure carried as the sum of at most this many rows of one block


def key_is_sum(key_line: KeyLine) -> bool:
    """The key says the line is printed as parts ('A + B', or computed from other lines)."""
    w = (key_line.where or "").lower()
    return "+" in w or w.startswith("computed")


def key_nets(key_line: KeyLine) -> bool:
    """The key's parts include one printed negative — a bracketed figure, '(13.39)' — so the parts
    net rather than add."""
    return key_is_sum(key_line) and bool(_BRACKETED.search(key_line.where or ""))


_BRACKETED = re.compile(r"\(\d[\d,]*(?:\.\d+)?\)")


def payload_has(req: dict, figure: float | str, parts: bool = False, nets: bool = False,
                period: str | None = None) -> list[str]:
    """Where the request carries this figure: a row printing it, compared by magnitude so a
    bracketed '(519.65)' carries 519.65, and exactly at the printed precision (`_same`). With
    `parts` (the key says the line is 'A + B'), also rows in one period column that add up to it
    at the figure's precision (to the cent at most): two rows anywhere in the request, or up to
    SUM_MAX_ROWS rows of one block. With `nets` (the key prints a part in brackets), rows of one
    block whose SIGNED figures net to it. `figure` is the key's figure as printed, or a number.

    With `period`, the addends of a sum are that period's figures only: a figure filed under the
    OTHER year (its column key, or the row's `periods` / `period`) is no part of this year's total.
    PetroChina's current impairment adjustment (3,152) is not in its request — one of its three
    parts was sent for the prior year only — yet four unrelated prior-year gains add up to 3,152."""
    p = payload_of(req["body"])
    out: list[str] = []
    if not isinstance(p, dict) or _num(figure) is None:
        return out
    places = _places(figure)
    target = round(abs(_num(figure)), 2)
    tol = 0.5 * 10.0 ** -min(places, 2) * (1 + 1e-9)
    allcols: dict[str, list[tuple[str, float]]] = {}
    sums: list[str] = []
    for kind, label, b in _blocks(p):
        cols: dict[str, list[tuple[str, float]]] = {}
        # A PART PRINTED NEGATIVE NETS. "Current Tax 1,388.98" and "Short/(Excess) provision
        # (13.39)" are 1,375.59, which no sum of magnitudes reaches — and the larger part is above
        # the total, so it has to be a candidate too. Tried only after the magnitudes fail.
        signed: dict[str, list[tuple[str, float]]] = {}
        for r in b.get("rows") or []:
            for col, v in (r.get("figures") or {}).items():
                n = _num(v)
                if n is None:
                    continue
                if _same(abs(n), abs(_num(figure)), _places(v), places):
                    out.append(f"{label}: '{r.get('caption')}'")
                    continue
                where = f"{label}: '{r.get('caption')}'"
                per = (r.get("periods") or {}).get(col) or r.get("period") or col
                if period and per in ("current", "prior") and per != period:
                    continue
                if n and abs(n) < target:
                    cols.setdefault(col, []).append((where, round(abs(n), 2)))
                if n and abs(n) < 2 * target:
                    signed.setdefault(col, []).append((where, round(n, 2)))
        for col, vals in cols.items():
            allcols.setdefault(col, []).extend(vals)
            hit = parts and _sum_of(vals, target, SUM_MAX_ROWS, tol=tol)
            if hit:
                sums.append(" + ".join(hit) + f" ({col})")
        if nets and not sums:
            for col, vals in signed.items():
                if not any(v < 0 for _, v in vals):
                    continue
                hit = _sum_of(vals, target, SUM_MAX_ROWS, signed=True, tol=tol)
                if hit:
                    sums.append(" + ".join(hit) + f" ({col}, net)")
    if parts and not sums:
        for col, vals in allcols.items():
            hit = _sum_of(vals, target, 2, tol=tol)
            if hit:
                sums.append(" + ".join(hit) + f" ({col})")
    return list(dict.fromkeys(out + [f"sum of {x}" for x in sums[:1]]))


def _sum_of(vals: list[tuple[str, float]], target: float, most: int,
            signed: bool = False, tol: float = 0.005) -> list[str] | None:
    """Rows of `vals` adding up to `target` within `tol` (half the target's printed step: to the
    cent for a figure printed to the cent, to the unit for a whole number). With `signed`, the
    figures keep their signs, at least one is negative, and the net is compared by magnitude."""
    from itertools import combinations
    for n in range(2, most + 1):
        for combo in combinations(vals[:60], n):
            if signed and not any(v < 0 for _, v in combo):
                continue
            total = round(sum(v for _, v in combo), 2)
            if abs((abs(total) if signed else total) - target) <= tol:
                return [c for c, _ in combo]
    return None


def prose_has(req: dict, figure: float | str) -> list[str]:
    """Blocks whose prose (not rows) prints the figure, exactly at the printed precision: the
    table reached the model only as text."""
    p = payload_of(req["body"])
    out = []
    if isinstance(p, dict) and _num(figure) is not None:
        # A REPORT REQUEST'S PAGES ARE PROSE TOO: a sentence on any page is citable there by its
        # page and amount (`note_sourced._page_sentence`), and a note's prose is on its pages.
        texts = [(label, str(b.get("prose") or "")) for _kind, label, b in _blocks(p)]
        texts += [(f"page {b.get('page')}", str(b.get("text") or ""))
                  for b in (p.get("pages") or []) if isinstance(b, dict)]
        for label, prose in texts:
            if any(_same(abs(_num(t)), abs(_num(figure)), _places(t), _places(figure)) for t in
                   re.findall(r"\(?\d[\d,]*\.\d+\)?|\d{1,3}(?:,\d{3})+", prose)
                   if _num(t) is not None):
                out.append(label)
    return out


# A check that cannot take a figure out of the payload; it is not named as the cause of a figure lost.
NOT_A_LOSS = {"no_unit"}


def classify(key_line: KeyLine, period: str, published: float | None, asking: list[dict],
             answered: set[str], lint: dict[str, list], audits: dict[str, dict]) -> tuple[str, str]:
    printed_as = getattr(key_line, period)
    printed = _num(printed_as)
    if printed is None:
        return ("correct", "filing prints nothing; run published nothing") if published is None else \
               ("not_in_filing", f"filing prints nothing; run published {published}")
    if published is not None and _same(published, printed, _places(published), _places(printed_as)):
        return "correct", ""
    if not asking:
        return "deterministic", "the line was not asked"
    if (key_line.where or "").lower().startswith("computed"):
        # no page prints it: the key adds other lines, so the miss follows those lines, not a payload
        return "derived", f"the key computes it ({(key_line.where or '')[:80]}); see its parts"
    had = [(r["id"], b) for r in asking for b in payload_has(r, printed_as, key_is_sum(key_line))]
    if had:
        rid, where = had[0]
        if rid in answered or any(r["id"] in answered for r in asking):
            return "model_misread", f"payload {rid} had it at {where}"
        return "unanswered", f"payload {rid} had it at {where}; no answer recorded"
    causes = sorted(({h["check"] for r in asking for h in lint.get(r["id"], [])}
                     | {pr["kind"] for r in asking for pr in (audits.get(r["id"]) or {}).get("problems", [])})
                    - NOT_A_LOSS)
    prose = [(r["id"], b) for r in asking for b in prose_has(r, printed_as)]
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
# coverage
# ------------------------------------------------------------------------------------------------

COVERAGE = ("carried", "carried_other_key", "carried_as_parts", "carried_in_prose", "prose_only",
            "not_carried", "not_asked", "derived")
# `carried_in_prose` is covered: the reply contract lets the model state an amount printed only in
# a note's sentence (`amount` + `quote`, verified against the note's text by
# `services.note_sourced.resolve_sources`) for every line whose route lets a note supply it.
COVERED = ("carried", "carried_other_key", "carried_as_parts", "carried_in_prose")


def prose_stating_lines(set_name: str) -> set[str]:
    """The lines whose answer may state an amount printed only in prose: every line a NOTE may
    supply (`services.line_item_routes.may_read_notes` — all but a declared `face` line). A face
    line's prose figure reaches no answer, so for it a sentence is still `prose_only`."""
    from app.schemas.line_items import load_line_item_set
    from app.services.line_item_routes import may_read_notes
    path = Path(set_name) if Path(set_name).is_absolute() else TEMPLATES / set_name
    items = load_line_item_set(json.loads(path.read_text(encoding="utf-8")), resolve=True).items
    return {i.key for i in items if may_read_notes(i)}


def cascade_parts(set_name: str) -> dict[str, set[str]]:
    """Every line's cascade parts, transitively: a line the model is never asked about (a derived
    parent such as is_pl__sales_revenues) is reached through the requests for its parts."""
    from app.schemas.line_items import load_line_item_set
    path = Path(set_name) if Path(set_name).is_absolute() else TEMPLATES / set_name
    items = load_line_item_set(json.loads(path.read_text(encoding="utf-8")), resolve=True).items
    direct = {i.key: {t.ref for rung in (i.cascade or []) for t in rung.terms if t.ref}
              for i in items}
    out: dict[str, set[str]] = {}
    for k in direct:
        seen, todo = set(), list(direct[k])
        while todo:
            r = todo.pop()
            if r not in seen:
                seen.add(r)
                todo.extend(direct.get(r, ()))
        out[k] = seen
    return out


def set_for_folder(d: Path) -> str:
    """The line-item set a capture folder was run with, from its `<stem>__<template>` name."""
    return d.name.split("__")[-1].removesuffix("_v1") + "_line_items.json"


def unit_multiple(unit: str | None) -> float:
    """How many units of currency one printed unit is: "(x10,000)" as a block states it, or the
    key's own words — '000 / thousand, 万 / ten thousand, million, crore. 1 when nothing says."""
    text = str(unit or "")
    m = re.search(r"\(x\s*([\d,]+)\)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    low = text.lower()
    if "ten thousand" in low or "万" in text or "萬" in text:
        return 1e4
    if "'000" in text or "\u2019000" in text or "thousand" in low or "千" in text:
        return 1e3
    if "million" in low or "百万" in text or "百萬" in text:
        return 1e6
    if "crore" in low:
        return 1e7
    return 1.0


def _rows_printing(req: dict, figure: float | str, key_unit: str | None = None
                   ) -> list[tuple[str, str, str, str]]:
    """(block, caption, figure key, row period) of every row of the request that prints this
    figure, by magnitude and exactly at the printed precision, as `payload_has` compares it.
    `figure` is the key's figure as printed (or a number, read at its own digits). With
    `key_unit`, a block that states a DIFFERENT unit is compared at it: HyperStrong's 关键管理人员报酬
    note prints 1,901.50 in 万元, which is the key's 19,015,000.00 yuan, and the block says
    "(x10,000)"."""
    p = payload_of(req["body"])
    out: list[tuple[str, str, str, str]] = []
    km = unit_multiple(key_unit) if key_unit else None
    want, places = _num(figure), _places(figure)
    if isinstance(p, dict) and want is not None:
        for _kind, label, b in _blocks(p):
            bm = unit_multiple(b.get("unit")) if km is not None and b.get("unit") else None
            rescale = bm is not None and bm != km
            for r in b.get("rows") or []:
                for col, v in (r.get("figures") or {}).items():
                    n = _num(v)
                    if n is not None and (_same(abs(n), abs(want), _places(v), places) or (
                            rescale and _same(abs(n), abs(want), _places(v), places, bm, km))):
                        # A period × measure row states each figure's period in `periods`.
                        per = (r.get("periods") or {}).get(col) or r.get("period") or ""
                        out.append((label, str(r.get("caption") or ""), col, str(per)))
    return out


_KEY_PART = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?")
_KEY_PAIR = re.compile(r"(\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?)\s*/\s*(\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?)")


def key_parts(key_line: KeyLine, period: str) -> list[float]:
    """The addends the key names for this period, as magnitudes (see `key_part_figures`)."""
    return [abs(_num(x)) for x in key_part_figures(key_line, period)]


def key_part_figures(key_line: KeyLine, period: str) -> list[str]:
    """The addends the key's `where` names for this period, when they add up to its figure.

    A key that gives a line as parts writes each addend: "66,532,098.75 + 38,974,805.76 + …" for
    one period, or "'A' 4,209 / 6,915 + 'B' 293,097 / 434,273" for both, current first. Grouped
    numbers only (a thousands separator), so note numbers and years are not read as parts. [] when
    the key names none, or when what it names does not add up to the printed figure — exactly, at
    the coarsest precision among the parts and the figure. Each addend is returned AS PRINTED, so
    it is looked for at its own precision."""
    where = key_line.where or ""
    printed = _num(getattr(key_line, period))
    stated = (key_line.parts or {}).get(period) if key_line.parts else None
    if printed is not None and stated:
        # Signed as the key states them, so a sales return nets; each addend is then looked for
        # by magnitude like every other figure. A zero addend is no figure to look for.
        nums = [_num(x) for x in stated]
        if any(n is None for n in nums) or not _same(
                sum(nums), printed, min(_places(x) for x in stated), _places(getattr(key_line, period))):
            return []
        return [x for x, n in zip(stated, nums) if n]
    if printed is None or "+" not in where:
        return []
    pairs = _KEY_PAIR.findall(where)
    if pairs:
        texts = [a if period == "current" else b for a, b in pairs]
    else:
        texts = _KEY_PART.findall(where)
    texts = [t for t in texts if _num(t) is not None]
    nums = [abs(_num(t)) for t in texts]
    if len(nums) < 2 or not _same(sum(nums), abs(printed), min(_places(t) for t in texts),
                                  _places(getattr(key_line, period))):
        return []
    return texts


def coverage_of(d: Path, key: Key, part_keys: dict[str, set[str]] | None = None,
                prose_lines: set[str] | None = None) -> list[dict]:
    """For every line and period the key says the filing prints: did a request asking about the
    line carry that figure, and under that period's own key? No answer and no run figure is read:
    this is whether the context lets the figure be caught at all, which `report` cannot say once a
    later stage has replaced what the model cited.

    `carried` — a row prints it under `current`/`prior` (or a row whose `period` is that period);
    `carried_other_key` — a row prints it under another key (a column heading, `col2`), so the model
    must work out which period it is; `carried_as_parts` — the key gives it as parts and rows add up
    to it; `carried_in_prose` — only a block's prose prints it, and the line is one whose answer may
    state a prose amount (`prose_lines`, from `prose_stating_lines`); `prose_only` — only a block's
    prose prints it and the line may not state it (or `prose_lines` was not given); `not_carried` —
    no request asking about the
    line has it; `not_asked` — no request asks about the line; `derived` — the key computes the line
    (`where` starts "computed"), so no page prints it and it is not counted. A line the model is
    never asked about is looked for in the requests for its cascade parts (`part_keys`), where given."""
    reqs = load_requests(d)
    ids = [i for i in current_ids(d) if i in reqs]
    out: list[dict] = []
    for k, kl in key.lines.items():
        asking = [reqs[i] for i in ids if k in reqs[i]["line_keys"]]
        if not asking and part_keys:
            asking = [reqs[i] for i in ids if part_keys.get(k, set()) & set(reqs[i]["line_keys"])]
        for period in ("current", "prior"):
            printed_as = getattr(kl, period)
            printed = _num(printed_as)
            if printed is None:
                continue
            row = {"key": k, "period": period, "printed": printed, "request": "", "where": ""}
            if (kl.where or "").lower().startswith("computed"):
                out.append({**row, "status": "derived"})
                continue
            if not asking:
                out.append({**row, "status": "not_asked"})
                continue
            hits = [(r["id"], h) for r in asking for h in _rows_printing(r, printed_as, key.unit)]
            if hits:
                right = [x for x in hits if period in (x[1][2], x[1][3])]
                rid, (label, cap, col, per) = (right or hits)[0]
                out.append({**row, "status": "carried" if right else "carried_other_key", "request": rid,
                            "where": f"{label}: '{cap}' [{col}{'/' + per if per else ''}]"})
                continue
            parts = [(r["id"], w) for r in asking
                     for w in payload_has(r, printed_as, key_is_sum(kl), key_nets(kl), period)]
            if not parts:
                # EVERY ADDEND THE KEY NAMES, each printed in a row of a request about the line —
                # the same claim as a sum found by search, without the search's limit of
                # `SUM_MAX_ROWS` rows (000685's guarantee balance is five guarantees).
                named = key_part_figures(kl, period)
                found = [next(((r["id"], h) for r in asking for h in _rows_printing(r, v)), None)
                         for v in named]
                if named and all(found):
                    parts = [(found[0][0], " + ".join(f"{h[0]}: '{h[1]}'" for _rid, h in found))]
            prose = [(r["id"], b) for r in asking for b in prose_has(r, printed_as)]
            status, (rid, where) = (("carried_as_parts", parts[0]) if parts else
                                    ("carried_in_prose" if prose_lines is not None and k in prose_lines
                                     else "prose_only", prose[0]) if prose else
                                    ("not_carried", (asking[0]["id"], "")))
            out.append({**row, "status": status, "request": rid, "where": where})
    return out


def cmd_coverage(a) -> int:
    """One line per capture folder: how many printed figures the requests carry. Writes
    DIR/coverage.json with every line's status."""
    print("folder | printed | " + " | ".join(COVERAGE) + " | covered")
    total: Counter = Counter()
    part_maps: dict[str, dict[str, set[str]]] = {}
    prose_maps: dict[str, set[str] | None] = {}
    for d in a.dirs:
        kp = next((d / n for n in a.key_names if (d / n).exists()), None)
        if kp is None:
            print(f"{d.name} | no key ({', '.join(a.key_names)})")
            continue
        sname = a.set or set_for_folder(d)
        if sname not in part_maps:
            known = (TEMPLATES / sname).exists() or Path(sname).exists()
            part_maps[sname] = cascade_parts(sname) if known else {}
            prose_maps[sname] = prose_stating_lines(sname) if known else None
        rows = coverage_of(d, load_key(kp), part_maps[sname], prose_maps[sname])
        (d / "coverage.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        c = Counter(r["status"] for r in rows)
        total.update(c)
        n = sum(v for s_, v in c.items() if s_ != "derived")
        cov = sum(c[s] for s in COVERED)
        print(f"{d.name} | {n} | " + " | ".join(str(c[s]) for s in COVERAGE)
              + f" | {cov}/{n} ({100 * cov / n:.0f}%)" if n else f"{d.name} | 0")
    n = sum(v for s_, v in total.items() if s_ != "derived")
    if len(a.dirs) > 1 and n:
        cov = sum(total[s] for s in COVERED)
        print(f"ALL | {n} | " + " | ".join(str(total[s]) for s in COVERAGE) + f" | {cov}/{n} ({100 * cov / n:.0f}%)")
    return 0


# ------------------------------------------------------------------------------------------------
# grade
# ------------------------------------------------------------------------------------------------

GRADES = ("right", "wrong_period", "wrong_figure", "wrong_text", "ambiguous", "unresolved", "missed",
          "cited_unprinted", "rightly_blank", "derived")


def _norm_caption(text) -> str:
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def _source_rows(payload: dict, src) -> list[tuple[dict, dict]]:
    """(block, row) pairs a citation names, matched back the way the contract says it is: the
    block by note / table / statement / entity / page, the row by its caption and group."""
    out = []
    cap = _norm_caption(src.caption)
    for kind, _label, b in _blocks(payload):
        if src.note:
            if kind != "notes" or _norm_caption(b.get("note")) != _norm_caption(src.note):
                continue
            if src.table and _norm_caption(b.get("title")) != _norm_caption(src.table):
                continue
        elif src.page is not None:
            if kind != "other_pages" or str(b.get("page")) != str(src.page):
                continue
        else:
            if kind != "statement_rows":
                continue
            if src.statement and b.get("statement") != src.statement:
                continue
            if src.entity and b.get("entity") != src.entity:
                continue
        # A ROW'S COLUMN LEGEND HOLDS UNTIL A ROW GIVES A NEW ONE: the request sends `columns` only
        # when it changes (`note_context`), so 000709's 合计 rows read "期末余额 · 坏账准备" off the
        # 应收账款 row above them — as the model reads them, and as the gateway resolves them.
        legend = None
        for r in b.get("rows") or []:
            if not isinstance(r, dict):
                continue
            if r.get("columns"):
                legend = r["columns"]
            elif legend:
                r = {**r, "columns": legend}
            if _norm_caption(r.get("caption")) != cap:
                continue
            # A statement row is named by the heading it is printed under (`subheading` or
            # `under`), as the gateway reads it (`note_sourced.resolve_sources`); a note row by
            # its `group`.
            heads = ([r.get("group")] if kind != "statement_rows"
                     else [r.get("subheading"), r.get("under")])
            if src.group and _norm_caption(src.group) not in {_norm_caption(h) for h in heads}:
                continue
            out.append((b, r))
    return out


def _block_column_periods(block: dict | None) -> dict[str, str]:
    """{column heading: period} the BLOCK states for its columns: a row's `periods` ("per column")
    or `columns` legend ({"current": "本期发生额", …}) names the year over a heading, and every row
    of the table printed under that heading is that year's. A heading two rows put in two years is
    left out."""
    seen: dict[str, set] = defaultdict(set)
    for r in (block or {}).get("rows") or []:
        if not isinstance(r, dict):
            continue
        for h, p in (r.get("periods") or {}).items():
            if p in ("current", "prior"):
                seen[str(h)].add(p)
        for p, h in (r.get("columns") or {}).items():
            if p in ("current", "prior"):
                seen[str(h)].add(p)
    return {h: next(iter(ps)) for h, ps in seen.items() if len(ps) == 1}


def _column_period(heading: str, block: dict | None) -> str:
    """The year a table prints over a column: the block's own legend for that heading, else the
    heading's words as the stage reads them (`note_columns.relative_slot`: 本期发生额 → current,
    上期发生额 → prior); "" where neither says."""
    per = _block_column_periods(block).get(str(heading))
    if per:
        return per
    from app.services.note_columns import relative_slot
    return relative_slot(str(heading))[0]


def _row_cell(r: dict, src, period: str, block: dict | None = None):
    """The row's figure for this period AS PRINTED (so its precision can be read), as the citation
    reads it: a named column (its key or its printed heading) filed under the row's own period,
    else the period's own key.

    A row whose period reads `unstated` in a table whose block states the year of the cited column
    (`_column_period`) is filed under that year, as the stage files it
    (`note_sourced` → `note_columns.period_of`): 000685's purchases and 300391's prior purchases
    cite 本期发生额 / 上期发生额 of such rows, and were graded wrong or unresolved although the run
    published the key's figure."""
    figs = r.get("figures") or {}
    if src.column and str(src.column).casefold() == "share_counts" and r.get("share_counts"):
        return (r.get("share_counts") or {}).get(period)   # the counts the caption prints
    if src.column:
        heads = {**{str(v): k for k, v in (r.get("columns") or {}).items()}}
        col = src.column if src.column in figs else heads.get(src.column)
        if col is None:
            return None
        per = (r.get("periods") or {}).get(col) or r.get("period") or ""
        if per == "unstated":
            per = _column_period(col, block) or per
        # A KEY READ OFF A PERIOD × MEASURE HEADER NAMES ITS YEAR FIRST — 'prior:allowance' is the
        # 期初余额 table's 坏账准备 — and the gateway files it there (`note_columns.period_of`).
        base = str(col).split(":", 1)[0]
        if per not in ("current", "prior") and ":" in str(col) and base in ("current", "prior"):
            per = base
        if per in ("current", "prior") and per != period:
            return None
        if per not in ("current", "prior") and col in ("current", "prior") and col != period:
            return None
        if per not in ("current", "prior") and col not in ("current", "prior") and period != "current":
            return None   # a row that states no year is read as the reporting year's, once
        return figs.get(col)
    per = r.get("period") or ""
    if per in ("current", "prior"):
        if per != period:
            return None
        vals = [v for v in figs.values() if _num(v) is not None]
        return vals[0] if len(vals) == 1 else figs.get(src.column or "")
    return figs.get(period)


def grade_answer(payload: dict, answer, kl: KeyLine, period: str, key_unit: str | None,
                 key_basis: str | None = None, computed: float | None = None,
                 own: float | None = None) -> tuple[str, str]:
    """Whether an answer picks the figure the filing prints for this line and period, judged in
    the request it answers: each citation is matched back to its row (an unmatched one is
    `unresolved`, one matching rows with different figures `ambiguous`), its figure for the period
    taken, components added with their signs, and the total compared with the key EXACTLY at the
    printed precision (`_same`: the coarser of the cited figures' and the key's, so a key given to
    fewer decimals than the row compares at the key's) — first at the printed unit, then at the
    block's unit against the key's.

    An answer with no sources leaves the line to the rollup, as a definition says to when the
    filing prints no subtotal. Where the run then publishes the line COMPUTED from its parts
    (`computed`, from `computed_figures`) and that figure is the one the filing prints, the line
    is `derived`, not `missed`: PetroChina's net trade receivables are printed as one face row the
    answer had already given to the gross line, and the rollup publishes the same 71,610.

    An answer whose citations name two or more entities is one figure PER ENTITY, as the stage
    publishes it (`services.line_item_llm.split_by_entity`): only the citations of the key's basis
    — and those naming no entity — are graded.

    A line the run publishes as ITS OWN ROW PLUS THE PARTS PRINTED BESIDE IT (`own`, from
    `_own_figure`) is answered with that row alone: Net Fixed Assets on a CAS balance sheet cites
    固定资产, and the rollup adds 在建工程 and 使用权资产. Such an answer is `right` when it cites the
    row the computation took as the line's own and the computation is the printed figure. Graded
    against the sum, 湖北能源's 固定资产 65,182,826,011.07 was a wrong figure for a printed
    75,643,796,087.48 that the run published exactly."""
    printed = _num(getattr(kl, period))
    if (kl.where or "").lower().startswith("computed"):
        return "derived", "the key computes the line; no page prints it"
    printed_as = getattr(kl, period)
    if not answer.sources:
        if printed is None:
            return "rightly_blank", ""
        if computed is not None and _same(computed, printed, _places(computed), _places(printed_as)):
            return "derived", f"left to the rollup; the run computes {computed:,.2f} from its parts, as printed"
        return "missed", "no sources" + (f"; the run computes {computed:,.2f}" if computed is not None else "")
    km = unit_multiple(key_unit) if key_unit else 1.0
    vals, notes_, bm, places = [], [], None, []
    named = {str(s.entity or "").lower() for s in answer.sources if getattr(s, "entity", None)}
    want = str(key_basis or "consolidated").lower()
    for i, src in enumerate(answer.sources):
        if len(named) > 1 and getattr(src, "entity", None) and str(src.entity).lower() != want:
            continue
        if src.amount not in (None, "") and src.quote:
            n = _num(src.amount)
            if n is not None and (src.period or "current") == period:
                vals.append(n)
                places.append(_places(src.amount))
                notes_.append(f"prose {src.amount}")
            elif period == "prior" and not src.period and printed is not None and any(
                    _same(abs(_num(t) or 0), abs(printed), _places(t), _places(printed_as))
                    for t in _KEY_PART.findall(src.quote)):
                vals.append(printed)           # the comparative the quote prints, read from it
                places.append(_places(printed_as))
                notes_.append("prose comparative in the quote")
            continue
        hits = _source_rows(payload, src)
        if not hits:
            if printed is None:   # nothing to pick for this year; the bad citation counts once
                return "rightly_blank", ""
            return "unresolved", f"no row '{src.caption}' in {src.note or src.statement or src.page}"
        cells = [c for c in (_row_cell(r, src, period, b) for b, r in hits) if _num(c) is not None]
        figs = {_num(c) for c in cells}
        if len(figs) > 1:
            return "ambiguous", f"'{src.caption}' matches rows with {sorted(figs)[:4]}"
        if not figs:
            continue
        places.append(min(_places(c) for c in cells))
        if bm is None and hits[0][0].get("unit"):
            bm = unit_multiple(hits[0][0].get("unit"))
        sign = answer.signs[i] if (str(answer.role) == "component" and i < len(answer.signs)) else 1
        vals.append(sign * figs.pop())
        notes_.append(f"'{src.caption}'")
        if str(answer.role) != "component":
            break
    if not vals:
        return ("rightly_blank", "") if printed is None else ("unresolved", "no figure for the period")
    total = sum(vals)
    tp = min(places) if places else 2

    def same(x) -> bool:
        n = _num(x)
        return n is not None and (_same(abs(total), abs(n), tp, _places(x)) or (
            bm is not None and bool(key_unit) and _same(abs(total), abs(n), tp, _places(x), bm, km)))
    if printed is None:
        return "cited_unprinted", f"cited {' + '.join(notes_)} = {total:,.2f}; the filing prints nothing"
    if same(printed_as):
        return "right", ""
    if (own is not None and computed is not None and same(own)
            and _same(computed, printed, _places(computed), _places(printed_as))):
        return "right", (f"the line's own row; the run adds the parts printed beside it: "
                         f"{computed:,.2f}, as printed")
    other = getattr(kl, "prior" if period == "current" else "current")
    if same(other):
        return "wrong_period", f"{total:,.2f} is the other year's figure"
    return "wrong_figure", f"{' + '.join(notes_)} = {total:,.2f}, the filing prints {printed:,.2f}"


def _text_form(text) -> str:
    """A text compared as printed: `text_lines.fold` (spacing and case do not count), and the full
    stop that ends a sentence does not either."""
    from app.services.text_lines import fold
    return fold(text).rstrip(".。")


def grade_text(payload: dict, answer, kl: KeyLine) -> tuple[str, str]:
    """Whether a text answer gives what the filing prints for the line, by normalised exact match.

    The answer is first checked as the gateway checks it (`text_lines.locate` against the page it
    cites in the request's `text_pages`, or — asked with the whole report — its `pages`): a text
    that page does not print is `unresolved`, refused before it is compared. A figure reply for a
    text line has no text to give and is `missed`."""
    from app.services.text_lines import locate
    accepted = [kl.text] if isinstance(kl.text, str) else list(kl.text or ())
    given = str(getattr(answer, "text", "") or "").strip()
    if not given:
        return ("missed", "no text") if accepted else ("rightly_blank", "")
    pages = (((payload or {}).get("text_pages") or (payload or {}).get("pages"))
             if isinstance(payload, dict) else None)
    if locate(given, getattr(answer, "page", None), pages or []) is None:
        return "unresolved", f"page {getattr(answer, 'page', None)} does not print {given[:60]!r}"
    if not accepted:
        return "cited_unprinted", f"gave {given[:60]!r}; the filing prints nothing"
    if _text_form(given) in {_text_form(t) for t in accepted}:
        return "right", ""
    return "wrong_text", f"gave {given[:80]!r}, the filing prints {accepted[0][:80]!r}"


def _computations(d: Path, basis: str) -> dict:
    """{(line, period): Calculated} for every line the folder's run PUBLISHES COMPUTED from its
    parts, on this basis: the template's arithmetic over the run's rows (`rollups.evaluate_rows`,
    the resolver the grid and the export use) gives it, no analyst value overrides it, and it is
    the figure `run/spread.json` shows. {} when the run or its template is not there."""
    run, idx = d / "run", d / "index.json"
    if not (run / "rows.json").exists() or not idx.exists():
        return {}
    tname = json.loads(idx.read_text(encoding="utf-8")).get("template") or ""
    tpath = TEMPLATES / tname
    if not tname or not tpath.exists():
        return {}
    from app.services import rollups
    tpl = json.loads(tpath.read_text(encoding="utf-8"))
    rows = json.loads((run / "rows.json").read_text(encoding="utf-8"))
    spread_p = run / "spread.json"
    spread = json.loads(spread_p.read_text(encoding="utf-8")) if spread_p.exists() else None
    out: dict = {}
    for period in ("current", "prior"):
        _g, _reported, overridden, *_ = rollups._readers(rows, basis, period, None)
        shown = (spread or {}).get(f"{basis}|{period}") or {}
        for k, c in rollups.evaluate_rows(tpl, rows, basis, period).items():
            if not c.computable or c.value is None or overridden(k):
                continue
            if spread is not None and (shown.get(k) is None or float(shown[k]) != float(c.value)):
                continue
            out[(k, period)] = c
    return out


def computed_figures(d: Path, basis: str) -> dict[tuple[str, str], float]:
    """{(line, period): figure} for every line the folder's run publishes computed from its parts
    (`_computations`)."""
    return {k: float(c.value) for k, c in _computations(d, basis).items()}


def _own_figure(key: str, calc) -> float | None:
    """The line's own figure where the run publishes the line AS THAT FIGURE PLUS THE PARTS
    PRINTED BESIDE IT — the rollup's rule for a `line` whose parts are printed as lines of their
    own (a mainland 固定资产 with 在建工程 and 使用权资产 below it, `rollups.evaluate`), whose first
    component is then the line itself. None for any other computation."""
    first = calc.components[0] if calc.components else None
    if first is not None and first.canonical_key == key and first.value is not None:
        return float(first.value)
    return None


def grade_folder(d: Path, key: Key) -> list[dict]:
    reqs = load_requests(d)
    latest = set(current_ids(d))
    computations = _computations(d, key.basis)
    computed = {k: float(c.value) for k, c in computations.items()}
    own = {k: f for k, c in computations.items() if (f := _own_figure(k[0], c)) is not None}
    out = []
    for ap in sorted((d / "answers").glob("*.txt")):
        req = reqs.get(ap.stem)
        if req is None:
            continue
        try:
            reply = parse_answer(ap.read_text(encoding="utf-8"), schema_class(req))
        except Exception as exc:  # an answer the gateway would reject is graded as such
            out.append({"request": ap.stem, "key": "", "period": "", "grade": "unresolved",
                        "detail": f"reply does not parse: {exc}"[:200]})
            continue
        payload = payload_of(req["body"])
        for ans in getattr(reply, "answers", []) or []:
            kl = key.lines.get(ans.key)
            if kl is None:
                continue
            if kl.text is not None or hasattr(ans, "text"):
                # THE LATEST RUN'S REQUEST ONLY: a text line asked before it had a text request
                # was asked as a figure, and that superseded answer could only ever be blank.
                if ap.stem not in latest:
                    continue
                g, detail = grade_text(payload, ans, kl)
                out.append({"request": ap.stem, "key": ans.key, "period": "text", "grade": g,
                            "detail": detail})
                continue
            for period in ("current", "prior"):
                g, detail = grade_answer(payload, ans, kl, period, key.unit, key.basis,
                                         computed=computed.get((ans.key, period)),
                                         own=own.get((ans.key, period)))
                out.append({"request": ap.stem, "key": ans.key, "period": period, "grade": g,
                            "detail": detail})
    return out


def cmd_grade(a) -> int:
    """One line per folder: how the recorded answers fare against the key, judged in the request
    each answers. Writes DIR/grade.json."""
    print("folder | graded | " + " | ".join(GRADES))
    total: Counter = Counter()
    for d in a.dirs:
        kp = next((d / n for n in a.key_names if (d / n).exists()), None)
        if kp is None or not (d / "answers").exists():
            continue
        rows = grade_folder(d, load_key(kp))
        (d / "grade.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        c = Counter(r["grade"] for r in rows)
        total.update(c)
        print(f"{d.name} | {sum(c.values())} | " + " | ".join(str(c[g]) for g in GRADES))
    if len(a.dirs) > 1:
        print(f"ALL | {sum(total.values())} | " + " | ".join(str(total[g]) for g in GRADES))
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
    p = sub.add_parser("coverage"); p.add_argument("dirs", type=Path, nargs="+")
    p.add_argument("--key-name", dest="key_names", action="append",
                   help="key file name inside each folder, first found wins (default coverage_key.json, "
                        "then sample_key.json)")
    p.add_argument("--set", help="line-item set for cascade parts (default: from the folder name)")
    p = sub.add_parser("grade"); p.add_argument("dirs", type=Path, nargs="+")
    p.add_argument("--key-name", dest="key_names", action="append")
    a = ap.parse_args(argv)
    if a.cmd in ("coverage", "grade") and not a.key_names:
        a.key_names = ["coverage_key.json", "sample_key.json"]
    return {"capture": cmd_capture, "replay": cmd_replay, "lint": cmd_lint, "sample": cmd_sample,
            "validate": cmd_validate, "report": cmd_report, "coverage": cmd_coverage,
            "grade": cmd_grade}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
