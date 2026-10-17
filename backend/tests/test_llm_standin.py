"""The LLM stand-in (scripts/llm_standin.py): records what the gateway would send, parses answers as
the gateway parses them, lints the context and classifies misses. No PDF needed."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.adapters.bedrock_gateway_llm import BedrockGatewayLlmProvider
from app.adapters._structured import strip_fences
from app.services.line_item_llm import LineItemReply

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "llm_standin.py"
_spec = importlib.util.spec_from_file_location("llm_standin", _PATH)
standin = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("llm_standin", standin)  # pydantic resolves the module's annotations by name
_spec.loader.exec_module(standin)

SYSTEM = "You locate figures."


def _messages(payload: dict) -> list[dict]:
    return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def _payload(**over) -> dict:
    p = {
        "unit": "₹ in lakhs",
        "line_items": [{"key": "bs_icon__finished_goods", "label": "Finished goods",
                        "notes_supplied": ["8"], "definition": "'Finished goods' row of the note.",
                        "statement": "balance_sheet"}],
        "notes": [{"note": "8", "title": "Inventories",
                   "rows": [{"caption": "Finished goods", "figures": {"current": "2805.45", "prior": "2544.10"}},
                            {"caption": "Total", "figures": {"current": "12396.10", "prior": "11395.42"}}]}],
        "statement_rows": [{"statement": "balance_sheet", "entity": "standalone",
                            "columns": {"current": "31 March 2025", "prior": "31 March 2024"},
                            "rows": [{"caption": "Inventories", "figures": {"current": "12396.10",
                                                                           "prior": "11395.42"}}]}],
    }
    p.update(over)
    return p


def _request(payload: dict, est_tokens: int = 100) -> dict:
    body = BedrockGatewayLlmProvider().build_body(system=SYSTEM, messages=_messages(payload),
                                                  response_schema=LineItemReply, max_tokens=512)
    return {"id": standin.body_id(body), "stage": "line_item_llm", "schema": "LineItemReply",
            "schema_module": LineItemReply.__module__, "line_keys": standin.line_keys_of(payload),
            "body": body, "chars": 400, "est_tokens": est_tokens}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "pages.json").write_text(json.dumps([
        {"index": 1, "kind": "FACE", "statement": "balance_sheet", "scope": "company"},
        {"index": 5, "kind": "NOTES", "statement": "", "scope": ""}]))
    (run / "rows.json").write_text(json.dumps([
        {"source_label": "Inventories", "printed_in": "face",
         "values": [{"basis": "standalone", "value_raw": "12396.10", "provenance": {"page_index": 1}}]},
        {"source_label": "Work-in-progress", "printed_in": "notes",
         "values": [{"basis": "standalone", "value_raw": "3412.20", "provenance": {"page_index": 5}}]}]))
    return tmp_path


# ---- the recorded body and the answer parsing ------------------------------------------------

def test_recorded_body_is_the_gateway_body(tmp_path: Path):
    rec = standin.Recorder(tmp_path)
    msgs = _messages(_payload())
    parsed, meta = rec.handle(system=SYSTEM, messages=msgs, response_schema=LineItemReply,
                              max_tokens=777, stage="line_item_llm")
    assert meta["model"] == "standin-abstain" and parsed == LineItemReply.model_validate({})
    [path] = (tmp_path / "requests").glob("*.json")
    saved = json.loads(path.read_text())
    expected = BedrockGatewayLlmProvider().build_body(system=SYSTEM, messages=msgs,
                                                      response_schema=LineItemReply, max_tokens=777)
    assert saved["body"] == expected
    assert saved["id"] == standin.body_id(expected) == path.stem
    assert saved["line_keys"] == ["bs_icon__finished_goods"] and saved["stage"] == "line_item_llm"
    assert (tmp_path / "requests" / f"{path.stem}.md").exists()
    assert len(list((tmp_path / "system").glob("*.md"))) == 1


def test_answer_file_is_parsed_as_the_gateway_parses(tmp_path: Path):
    rec = standin.Recorder(tmp_path)
    msgs = _messages(_payload())
    body = BedrockGatewayLlmProvider().build_body(system=SYSTEM, messages=msgs,
                                                  response_schema=LineItemReply, max_tokens=512)
    reply = '```json\n{"answers": []}\n```'
    (tmp_path / "answers" / f"{standin.body_id(body)}.txt").write_text(reply)
    parsed, meta = rec.handle(system=SYSTEM, messages=msgs, response_schema=LineItemReply,
                              max_tokens=512, stage="line_item_llm")
    assert meta["model"] == "standin"
    assert parsed == LineItemReply.model_validate_json(strip_fences(reply))
    assert rec.calls[-1]["answered"] is True
    (tmp_path / "answers" / f"{standin.body_id(body)}.txt").write_text("not json")
    with pytest.raises(Exception):
        rec.handle(system=SYSTEM, messages=msgs, response_schema=LineItemReply,
                   max_tokens=512, stage="line_item_llm")


# ---- lint ------------------------------------------------------------------------------------

def _checks(payload: dict, run_dir: Path, est_tokens: int = 100) -> set[str]:
    hits = standin.lint_request(_request(payload, est_tokens), standin.RunIndex(run_dir / "run"), 1000)
    return {h["check"] for h in hits}


def test_clean_request_has_no_lint_hit(run_dir: Path):
    assert _checks(_payload(), run_dir) == set()


def _with_face_row(row: dict, **block) -> dict:
    p = _payload()
    p["statement_rows"][0]["rows"].append(row)
    p["statement_rows"][0].update(block)
    return p


@pytest.mark.parametrize("check,payload", [
    ("rows_not_from_statement", lambda: _with_face_row(
        {"caption": "Work-in-progress", "figures": {"current": "3412.20", "prior": "3108.77"}})),
    ("period_label_unclear", lambda: _with_face_row(
        {"caption": "Borrowings", "figures": {"current": "1.00", "col2": "2.00"}})),
    ("period_label_unclear", lambda: _with_face_row(
        {"caption": "Borrowings", "figures": {"prior": "2.00"}})),
    ("fragment_as_row", lambda: _with_face_row(
        {"caption": "Term loan from State Bank of India (sanctioned ₹", "figures": {"current": "5.00"}})),
    ("no_unit", lambda: {k: v for k, v in _payload().items() if k != "unit"}),
    ("duplicate_caption", lambda: _with_face_row(
        {"caption": "Inventories", "figures": {"current": "99.00", "prior": "98.00"}})),
    ("note_not_supplied", lambda: _payload(line_items=[{
        "key": "bs_icon__x", "notes_supplied": [], "read_from": "the 'Inventories' note",
        "definition": "x"}])),
    ("entity_doubtful", lambda: _with_face_row({"caption": "Inventories", "figures": {}},
                                               entity="consolidated")),
])
def test_each_lint_check_fires(check: str, payload, run_dir: Path):
    assert check in _checks(payload(), run_dir)


def test_oversize_fires_above_budget(run_dir: Path):
    assert _checks(_payload(), run_dir, est_tokens=5000) == {"oversize"}


def test_a_line_said_not_to_be_in_a_note_is_not_note_not_supplied(run_dir: Path):
    p = _payload(line_items=[{"key": "bs_icon__x", "notes_supplied": [],
                              "read_from": "the face of its statement only — its figure is not in a note",
                              "definition": "x"}])
    assert "note_not_supplied" not in _checks(p, run_dir)


# ---- sample / validate shapes ----------------------------------------------------------------

def test_sample_includes_clean_requests_and_spreads_strata(run_dir: Path):
    reqs = {}
    for i in range(6):
        r = _request(_payload(line_items=[{"key": f"bs_icon__k{i}", "notes_supplied": ["8"],
                                           "statement": "balance_sheet" if i % 2 else "profit_and_loss"}]))
        reqs[r["id"]] = r
    ids = sorted(reqs)
    lint = {ids[0]: [{"check": "no_unit"}], ids[1]: [{"check": "fragment_as_row"}]}
    picked = standin.pick_sample(reqs, lint, 4, seed=0)
    assert len(picked) == 4 and len({p["id"] for p in picked}) == 4
    assert any(not p["lint"] for p in picked)
    assert picked == standin.pick_sample(reqs, lint, 4, seed=0)


def test_audit_record_and_scratch_key_shape(tmp_path: Path):
    standin.AuditRecord.model_validate({
        "id": "x", "line_keys": ["k"], "verdict": "answerable_with_assumption",
        "per_line": [{"key": "k", "needed": "the WIP row", "found": None, "verdict": "not_answerable",
                      "assumption": None}],
        "problems": [{"kind": "period_label_unclear", "detail": "both columns read '31 March 2025'"}],
        "would_need": "the column headers", "confidence": 0.6})
    with pytest.raises(Exception):
        standin.AuditRecord.model_validate({"id": "x", "line_keys": [], "verdict": "maybe",
                                            "confidence": 2})
    k = tmp_path / "f.key.json"
    k.write_text(json.dumps({"filing": "f", "basis": "standalone", "units": "₹ in lakhs",
                             "lines": {"a": {"current": 1.5, "prior": None, "source": "face",
                                             "confidence": "certain"}}}))
    key = standin.load_key(k)
    assert key.unit == "₹ in lakhs" and key.lines["a"].current == "1.5" and key.lines["a"].where == "face"


# ---- report classification -------------------------------------------------------------------

def test_report_classifies_each_cause():
    req = _request(_payload())
    rid = req["id"]
    line = standin.KeyLine(current="2805.45", prior=None)
    lint = {rid: [{"check": "fragment_as_row", "evidence": "x"}]}

    def cause(kl, published, asking, answered=frozenset(), audits=None):
        return standin.classify(kl, "current", published, asking, set(answered), lint, audits or {})[0]

    assert cause(line, 2805.45, [req]) == "correct"
    assert cause(line, None, []) == "deterministic"
    assert cause(line, 1.0, [req], {rid}) == "model_misread"
    assert cause(line, None, [req]) == "unanswered"
    missing = standin.KeyLine(current="7777.00")
    c, detail = standin.classify(missing, "current", None, [req], set(), lint, {})
    assert c == "lost_in_context" and "fragment_as_row" in detail
    assert cause(standin.KeyLine(current=None), 5.0, [req]) == "not_in_filing"
