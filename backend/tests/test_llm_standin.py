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

def test_a_capture_into_a_reused_folder_keeps_only_its_own_requests(tmp_path: Path):
    """A request a previous capture left behind was read as this run's (note 4's lost heading was
    a stale file). Answers and audits stay: a replay answers from them."""
    for d, name in (("requests", "old.json"), ("system", "old.md"), ("answers", "a.txt"),
                    ("audit", "a.json")):
        (tmp_path / d).mkdir()
        (tmp_path / d / name).write_text("{}")
    standin.Recorder(tmp_path)
    assert not list((tmp_path / "requests").iterdir()) and not list((tmp_path / "system").iterdir())
    assert (tmp_path / "answers" / "a.txt").exists() and (tmp_path / "audit" / "a.json").exists()


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


def test_one_caption_under_two_printed_headings_is_not_a_duplicate(run_dir: Path):
    """f1's standalone balance sheet prints "(ia) Lease liabilities" under non-current AND under
    current liabilities, and each row says which in `under`. Two rows, told apart, not one caption
    with two figures; the same caption under the SAME heading still is."""
    p = _payload()
    rows = p["statement_rows"][0]["rows"]
    rows += [{"caption": "(ia) Lease liabilities", "under": "Non-current liabilities",
              "figures": {"current": "955.23", "prior": "809.15"}},
             {"caption": "(ia) Lease liabilities", "under": "Current liabilities",
              "figures": {"current": "196.80", "prior": "172.45"}}]
    hits = standin.lint_request(_request(p, 100), standin.RunIndex(run_dir / "run"), 1000)
    assert not [h for h in hits if h["check"] == "duplicate_caption" and "Lease" in h["evidence"]]
    rows[-1]["under"] = "Non-current liabilities"
    hits = standin.lint_request(_request(p, 100), standin.RunIndex(run_dir / "run"), 1000)
    assert [h for h in hits if h["check"] == "duplicate_caption" and "Lease" in h["evidence"]]


def test_oversize_fires_above_budget(run_dir: Path):
    assert _checks(_payload(), run_dir, est_tokens=5000) == {"oversize"}


def test_a_line_said_not_to_be_in_a_note_is_not_note_not_supplied(run_dir: Path):
    p = _payload(line_items=[{"key": "bs_icon__x", "notes_supplied": [],
                              "read_from": "the face of its statement only — its figure is not in a note",
                              "definition": "x"}])
    assert "note_not_supplied" not in _checks(p, run_dir)


_NOTES_PAYABLE_CP = (
    "Bills payable and short-term notes or commercial paper, reported in the current liabilities "
    "section of the balance sheet — on a CAS statement '应付票据' (bank and commercial acceptance "
    "bills payable). Not bonds, guaranteed notes, senior notes or the current portion of long-term "
    "notes, even where they are printed under current liabilities: those are 'CPLTD Other'.")


@pytest.mark.parametrize("read_from,definition,fires", [
    # the instrument's name is not a reference to a note: every HK filing's request was flagged
    ("the face of its statement only — its figure is not in a note", _NOTES_PAYABLE_CP, False),
    ("", "Extract the reported value for 'Notes Payable(CP)' from the stated section.", False),
    ("", "Medium-term notes and guaranteed notes due within one year.", False),
    # a reference to a note still is
    ("a row of one of its notes only — its figure is not on the face of a statement", "x", True),
    ("", "Depreciation for the period in the property, plant and equipment note.", True),
    ("", "As disclosed in note 12 (borrowings).", True),
    ("", "See Note (3) for the maturity analysis.", True),
    ("", "Use the currency stated in the statements or accounting-policy note.", True),
    ("", "按财务报表附注披露的金额填列。", True),
])
def test_note_not_supplied_reads_a_reference_to_a_note_not_the_word(read_from, definition, fires,
                                                                    run_dir: Path):
    p = _payload(line_items=[{"key": "bs_cl__notes_payable_cp", "notes_supplied": [],
                              "read_from": read_from, "definition": definition}])
    assert ("note_not_supplied" in _checks(p, run_dir)) is fires
    p["line_items"][0]["notes_supplied"] = ["12"]
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


@pytest.mark.parametrize("found", [
    {"block": "七、53 、股本", "caption": "股份总数"},                         # a text line (000883)
    {"block": "note 28 SHARE CAPITAL", "caption": "720,429,301 ordinary shares",
     "share_counts": {"current": "720429301", "prior": "720429301"}},     # counts, not figures
    {"block": "36 COMMITMENTS", "caption": None},                        # prose: no caption
    {"block": None, "caption": "Issued and fully paid ordinary shares"},
    "Capital commitments are stated only in the note's prose",           # a line of text
    {"block": "8 Inventories", "caption": "Finished goods", "figures": {"current": "2805.45"}},
])
def test_an_audit_record_validates_with_a_found_block_without_figures(found, tmp_path: Path):
    rec = {"id": "1eb90d6005d3596f", "line_keys": ["k"], "verdict": "answerable_with_assumption",
           "per_line": [{"key": "k", "needed": "the share count", "found": found,
                         "verdict": "answerable_with_assumption", "assumption": None}],
           "confidence": 0.7, "carried_from": "9c3d47ab4a14b652"}
    standin.AuditRecord.model_validate(rec)
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "1eb90d6005d3596f.json").write_text(json.dumps(rec, ensure_ascii=False),
                                                               encoding="utf-8")
    assert standin.main(["validate", str(tmp_path)]) == 0


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


def test_report_sees_a_figure_carried_as_parts_or_in_brackets():
    """A key line printed as 'A + B' is carried when A and B are rows of one block; a bracketed
    '(519.65)' carries 519.65. Neither is a figure lost in context."""
    notes = [{"note": "30", "title": "Finance costs",
              "rows": [{"caption": "Other borrowing costs", "figures": {"current": "38.20", "prior": "41.55"}},
                       {"caption": "Bank charges", "figures": {"current": "29.65", "prior": "26.80"}},
                       {"caption": "Disposals", "figures": {"current": "(519.65)", "prior": "(420.45)"}}]}]
    req = _request(_payload(notes=notes))
    rid = req["id"]
    lint = {rid: [{"check": "no_unit", "evidence": "x"}]}
    c, detail = standin.classify(standin.KeyLine(current="67.85", where="note 30: other + bank charges"), "current", None, [req], set(), lint, {})
    assert c == "unanswered" and "sum of note 30 'Finance costs': 'Other borrowing costs' + note 30 'Finance costs': 'Bank charges'" in detail
    # only when the key says the line is made of parts
    c, _ = standin.classify(standin.KeyLine(current="67.85"), "current", None, [req], set(), lint, {})
    assert c == "lost_in_context"
    c, _ = standin.classify(standin.KeyLine(current="519.65"), "current", None, [req], {rid}, lint, {})
    assert c == "model_misread"
    # a figure the rows cannot make is still lost; a missing unit is not named as what lost it
    c, detail = standin.classify(standin.KeyLine(current="7777.00"), "current", None, [req], set(), lint, {})
    assert c == "lost_in_context" and "no_unit" not in detail


def test_report_says_when_a_lost_figure_is_only_in_prose():
    notes = [{"note": "3", "title": "PPE", "prose": "Disposals - - (452.30) (38.40) (28.95) (519.65)",
              "rows": [{"caption": "Disposals"}]}]
    req = _request(_payload(notes=notes))
    c, detail = standin.classify(standin.KeyLine(current="519.65"), "current", None, [req], set(), {}, {})
    assert c == "lost_in_context" and "in prose only: note 3 'PPE'" in detail


def test_key_null_line_means_not_printed(tmp_path: Path):
    k = tmp_path / "key.json"
    k.write_text(json.dumps({"filing": "f", "lines": {"a": None}}))
    line = standin.load_key(k).lines["a"]
    assert line.current is None and line.prior is None
    assert standin.classify(line, "current", None, [], set(), {}, {})[0] == "correct"


def test_report_puts_a_computed_key_line_down_to_its_parts():
    req = _request(_payload())
    line = standin.KeyLine(current="7777.00", where="computed (ICON arithmetic): +Domestic +Export")
    c, _ = standin.classify(line, "current", None, [req], set(), {}, {})
    assert c == "derived"
    assert standin.classify(line, "current", 7777.0, [req], set(), {}, {})[0] == "correct"


def test_coverage_says_whether_each_printed_figure_reached_a_request_about_its_line(tmp_path: Path):
    """Coverage reads no answer and no run figure: only whether a request asking about the line
    carried what the filing prints, and under that period's own key."""
    notes = [{"note": "8", "title": "Inventories",
              "rows": [{"caption": "Finished goods", "figures": {"current": "2805.45", "col2": "2544.10"}},
                       {"caption": "Raw materials", "figures": {"current": "100.00", "prior": "90.00"}},
                       {"caption": "Stores", "figures": {"current": "20.00", "prior": "10.00"}}]}]
    req = _request(_payload(notes=notes))
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / f"{req['id']}.json").write_text(json.dumps(req))
    (tmp_path / "index.json").write_text(json.dumps({"requests": [{"id": req["id"]}]}))
    key = standin.Key(filing="x", template="t", lines={
        "bs_icon__finished_goods": standin.KeyLine(current="2805.45", prior="2544.10"),
        "bs_icon__raw_materials": standin.KeyLine(current="9999.00")})
    got = {(r["key"], r["period"]): r["status"] for r in standin.coverage_of(tmp_path, key)}
    assert got == {("bs_icon__finished_goods", "current"): "carried",
                   ("bs_icon__finished_goods", "prior"): "carried_other_key",
                   ("bs_icon__raw_materials", "current"): "not_asked"}
    parts = standin.Key(filing="x", template="t", lines={
        "bs_icon__finished_goods": standin.KeyLine(current="120.00", where="note 8: raw + stores"),
        })
    assert [r["status"] for r in standin.coverage_of(tmp_path, parts)] == ["carried_as_parts"]
    missing = standin.Key(filing="x", template="t", lines={
        "bs_icon__finished_goods": standin.KeyLine(current="7777.00")})
    assert [r["status"] for r in standin.coverage_of(tmp_path, missing)] == ["not_carried"]


def test_a_row_printed_above_the_next_statements_title_is_its_own_statements(run_dir: Path):
    """A page that holds the end of one statement and the start of the next is classified as the
    one that begins on it. 688008's company balance sheet closes at the top of page 153 above
    合并利润表; its 负债和所有者权益总计 sits in the balance-sheet block, stamped so, and is not a row
    "from" the income statement. The same row unstamped still is."""
    run = run_dir / "run"
    pages = json.loads((run / "pages.json").read_text())
    pages.append({"index": 7, "kind": "FACE", "statement": "profit_and_loss", "scope": "company"})
    (run / "pages.json").write_text(json.dumps(pages))
    rows = json.loads((run / "rows.json").read_text())
    rows.append({"source_label": "负债和所有者权益总计", "printed_in": "face",
                 "flags": ["printed_on:balance_sheet"],
                 "values": [{"basis": "standalone", "value_raw": "9712265458.47",
                             "provenance": {"page_index": 7}}]})
    (run / "rows.json").write_text(json.dumps(rows))
    payload = _with_face_row({"caption": "负债和所有者权益总计", "figures": {"current": "9712265458.47"}})
    assert "rows_not_from_statement" not in _checks(payload, run_dir)
    rows[-1]["flags"] = []
    (run / "rows.json").write_text(json.dumps(rows))
    assert "rows_not_from_statement" in _checks(payload, run_dir)


def test_a_caption_and_figure_printed_on_two_statements_is_sourced_from_the_blocks(run_dir: Path):
    """佳明 prints "Loss before taxation" on the income statement and atop its indirect cash flow."""
    run = run_dir / "run"
    pages = json.loads((run / "pages.json").read_text())
    pages += [{"index": 7, "kind": "FACE", "statement": "profit_and_loss", "scope": "company"}]
    (run / "pages.json").write_text(json.dumps(pages))
    rows = json.loads((run / "rows.json").read_text())
    for page in (7, 1):
        rows.append({"source_label": "Loss before taxation", "printed_in": "face",
                     "values": [{"basis": "standalone", "value_raw": "-150.00",
                                 "provenance": {"page_index": page}}]})
    (run / "rows.json").write_text(json.dumps(rows))
    payload = _with_face_row({"caption": "Loss before taxation", "figures": {"current": "-150.00"}})
    assert "rows_not_from_statement" not in _checks(payload, run_dir)


def test_coverage_reaches_a_derived_line_through_its_parts_and_does_not_count_a_computed_one(tmp_path: Path):
    """is_pl__sales_revenues is never asked about: its cascade reads a part that is. The part's
    request carrying the printed figure covers it. A key line the key itself computes is printed
    nowhere, so it is `derived` and not counted."""
    req = _request(_payload())          # asks about bs_icon__finished_goods only
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / f"{req['id']}.json").write_text(json.dumps(req))
    (tmp_path / "index.json").write_text(json.dumps({"requests": [{"id": req["id"]}]}))
    key = standin.Key(filing="x", template="t", lines={
        "parent": standin.KeyLine(current="2805.45"),
        "computed_line": standin.KeyLine(current="1.00", where="computed: A - B")})
    got = {r["key"]: r["status"] for r in
           standin.coverage_of(tmp_path, key, {"parent": {"bs_icon__finished_goods"}})}
    assert got == {"parent": "carried", "computed_line": "derived"}
    assert standin.set_for_folder(Path("x__output_csv_hk_v1")) == "output_csv_hk_line_items.json"


def test_coverage_nets_a_part_the_filing_prints_in_brackets():
    """'Current Tax' 1,388.98 and 'Short/(Excess) provision' (13.39) are the 1,375.59 the key
    prints: the larger part is above the total and the smaller is negative, so no sum of magnitudes
    reaches it. Netting is tried only when the key itself prints a bracketed part."""
    notes = [{"note": "TAX", "title": "Tax Expense",
              "rows": [{"caption": "Current Tax", "figures": {"current": "1523.02", "prior": "1388.98"}},
                       {"caption": "Short/(Excess) provision", "figures": {"current": "27.05",
                                                                           "prior": "-13.39"}}]}]
    req = _request(_payload(notes=notes))
    nets = standin.KeyLine(prior="1375.59",
                           where="'Current Tax' 1,523.02 / 1,388.98 + 'Short/(Excess)' 27.05 / (13.39)")
    assert standin.key_nets(nets)
    hit = standin.payload_has(req, 1375.59, True, True)
    assert hit and "net" in hit[-1]
    # Without a bracketed part in the key, the magnitudes alone are compared, as before.
    assert not standin.key_nets(standin.KeyLine(prior="1375.59", where="'Current Tax' + 'Short'"))
    assert standin.payload_has(req, 1375.59, True, False) == []


def test_coverage_reads_a_whole_number_printed_in_prose():
    """'were RMB20,878 (2023: RMB6,050)' prints both figures with no decimals; they are carried as
    prose, which coverage reports as prose_only rather than not carried."""
    notes = [{"note": "36", "title": "COMMITMENTS", "rows": [],
              "prose": "were RMB20,878 (2023: RMB6,050). These capital commitments"}]
    req = _request(_payload(notes=notes))
    assert standin.prose_has(req, 20878.0) and standin.prose_has(req, 6050.0)


def _folder_with(tmp_path: Path, payload: dict) -> dict:
    req = _request(payload)
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / f"{req['id']}.json").write_text(json.dumps(req))
    (tmp_path / "index.json").write_text(json.dumps({"requests": [{"id": req["id"]}]}))
    return req


def test_a_prose_amount_is_covered_for_a_line_that_may_state_one(tmp_path: Path):
    """PetroChina's capital commitments are printed only in a sentence. The reply contract lets
    the model state that amount (with its quote) for any line a note may supply, so the figure is
    `carried_in_prose` and covered; for a declared `face` line it stays `prose_only`."""
    notes = [{"note": "36", "title": "COMMITMENTS", "rows": [],
              "prose": "were RMB20,878 (2023: RMB6,050). These capital commitments"}]
    _folder_with(tmp_path, _payload(notes=notes))
    key = standin.Key(filing="x", template="t", lines={
        "bs_icon__finished_goods": standin.KeyLine(current="20878", prior="6050")})
    may = {r["period"]: r["status"] for r in standin.coverage_of(tmp_path, key, None,
                                                                 {"bs_icon__finished_goods"})}
    assert may == {"current": "carried_in_prose", "prior": "carried_in_prose"}
    assert "carried_in_prose" in standin.COVERED
    may_not = {r["status"] for r in standin.coverage_of(tmp_path, key, None, set())}
    assert may_not == {"prose_only"}


def test_the_lines_that_may_state_a_prose_amount_are_the_lines_a_note_may_supply():
    lines = standin.prose_stating_lines("output_csv_hk_line_items.json")
    assert "capital_and_lease_commitments__capital_commitments" in lines
    assert "supplemental_data__num_outstanding_common_shares" in lines          # route anywhere
    assert "is_pl__net_premium_earned" not in lines                             # route face


def test_every_addend_the_key_names_covers_its_line(tmp_path: Path):
    """000685's related-party guarantee balance is five guarantees, one more than the sum search
    tries. The key names each; every one printed in a row of the request is the figure carried."""
    parts = ["66532098.75", "38974805.76", "42036260.33", "38750000.00", "41437500.00"]
    notes = [{"note": "十七、84", "title": "关联担保情况",
              "rows": [{"caption": f"party {i}", "figures": {"担保金额": v}} for i, v in enumerate(parts)]}]
    _folder_with(tmp_path, _payload(notes=notes))
    where = "关联担保情况 66,532,098.75 + 38,974,805.76 + 42,036,260.33 + 38,750,000.00 + 41,437,500.00"
    key = standin.Key(filing="x", template="t", lines={
        "bs_icon__finished_goods": standin.KeyLine(current="227730664.84", where=where)})
    assert [r["status"] for r in standin.coverage_of(tmp_path, key)] == ["carried_as_parts"]
    # Both periods written as "current / prior" pairs, current first.
    pairs = standin.KeyLine(current="-323878", prior="-468536",
                            where="'A' 4,209 / 6,915 + 'B' 293,097 / 434,273 + 'C' 26,572 / 27,348")
    assert standin.key_parts(pairs, "current") == [4209.0, 293097.0, 26572.0]
    assert standin.key_parts(pairs, "prior") == [6915.0, 434273.0, 27348.0]
    # Named parts that do not add up to the figure are not its parts.
    assert standin.key_parts(standin.KeyLine(current="1.00", where="1,000 + 2,000"), "current") == []


def test_a_table_in_another_unit_is_compared_at_its_unit(tmp_path: Path):
    """HyperStrong's 关键管理人员报酬 prints 1,901.50 in 万元; the key states 19,015,000.00 yuan."""
    notes = [{"note": "十四、5", "title": "关键管理人员报酬", "unit": "CNY ten thousands (x10,000)",
              "rows": [{"caption": "关键管理人员报酬", "figures": {"current": "1901.50", "prior": "1395.31"}}]}]
    _folder_with(tmp_path, _payload(notes=notes))
    key = standin.Key(filing="x", template="t", unit="CNY yuan (单位：元)", lines={
        "bs_icon__finished_goods": standin.KeyLine(current="19015000.00", prior="13953100.00")})
    assert {r["status"] for r in standin.coverage_of(tmp_path, key)} == {"carried"}
    assert standin.unit_multiple("HK$'000") == standin.unit_multiple("HKD thousands (x1,000)") == 1000


def test_grade_judges_an_answer_in_the_request_it_answers():
    """A blind answer is graded by matching its citation back to the row it names, in the request
    it answers, and comparing that row's figure for each period with the key."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef
    notes = [{"note": "8", "title": "Inventories",
              "rows": [{"caption": "Finished goods", "figures": {"current": "2805.45", "prior": "2544.10"}},
                       {"caption": "Total", "figures": {"current": "12396.10", "prior": "11395.42"}}]},
             {"note": "9", "title": "Other",
              "rows": [{"caption": "Total", "figures": {"current": "1.00", "prior": "2.00"}}]}]
    payload = _payload(notes=notes)
    kl = standin.KeyLine(current="2805.45", prior="2544.10")

    def grade(sources, period="current", role="whole", signs=()):
        ans = LineItemAnswer(key="k", sources=sources, role=role, signs=list(signs))
        return standin.grade_answer(payload, ans, kl, period, None)[0]

    fg = SourceRef(note="8", caption="Finished goods")
    assert grade([fg]) == "right" and grade([fg], "prior") == "right"
    assert grade([SourceRef(note="8", caption="Total")]) == "wrong_figure"
    assert grade([SourceRef(caption="Total", note="")]) == "unresolved"   # no statement row 'Total'
    assert grade([SourceRef(note="8", caption="Raw materials")]) == "unresolved"
    assert grade([]) == "missed"
    swapped = standin.KeyLine(current="9.00", prior="2805.45")   # the row's current is the key's prior
    ans = LineItemAnswer(key="k", sources=[SourceRef(note="8", caption="Finished goods")])
    assert standin.grade_answer(payload, ans, swapped, "current", None)[0] == "wrong_period"
    dup = _payload(notes=[{"note": "8", "title": "A", "rows": [
        {"caption": "Total", "figures": {"current": "5.00"}}, {"caption": "Total", "figures": {"current": "6.00"}}]}])
    ans = LineItemAnswer(key="k", sources=[SourceRef(note="8", caption="Total")])
    assert standin.grade_answer(dup, ans, standin.KeyLine(current="5.00"), "current", None)[0] == "ambiguous"
    parts = LineItemAnswer(key="k", role="component", signs=[1, -1], sources=[
        SourceRef(note="8", caption="Total"), SourceRef(note="8", caption="Finished goods")])
    assert standin.grade_answer(payload, parts, standin.KeyLine(current="9590.65"), "current", None)[0] == "right"


def test_grade_reads_a_prose_comparative_a_yearless_row_once_and_the_printed_unit():
    """Three ways a right answer was graded wrong: a sentence amount applied to both years (its
    comparative is in the quote), a movement row that states no year counted for both years, and a
    figure the key holds in the printed unit rescaled by the block's unit."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef
    payload = _payload(notes=[{"note": "36", "title": "COMMITMENTS", "unit": "CNY millions (x1,000,000)",
        "prose": "Capital commitments RMB20,878 (2023: RMB6,050).",
        "rows": [{"caption": "Charge for the year", "figures": {"Total": "48.00"}, "period": "unstated"}]}])
    prose = LineItemAnswer(key="k", sources=[SourceRef(note="36", caption="Capital commitments",
                           amount="20,878", quote="Capital commitments RMB20,878 (2023: RMB6,050).")])
    kl = standin.KeyLine(current="20,878", prior="6,050")
    assert standin.grade_answer(payload, prose, kl, "current", None)[0] == "right"
    assert standin.grade_answer(payload, prose, kl, "prior", None)[0] == "right"
    row = LineItemAnswer(key="k", sources=[SourceRef(note="36", caption="Charge for the year", column="Total")])
    once = standin.KeyLine(current="48.00", prior=None)
    assert standin.grade_answer(payload, row, once, "current", None)[0] == "right"
    assert standin.grade_answer(payload, row, once, "prior", None)[0] == "rightly_blank"


def test_grade_takes_a_two_entity_answer_on_the_keys_basis():
    """An answer citing each entity's rows is one figure per entity, as the stage files it
    (`services.line_item_llm.split_by_entity`), so it is graded on the key's basis alone. Added
    together, the group's and the company's current tax read 2,992.59 where the group prints
    1,550.07."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef

    def block(entity, tax, earlier):
        return {"statement": "profit_and_loss", "entity": entity,
                "columns": {"current": "2025-26", "prior": "2024-25"},
                "rows": [{"caption": "Current Tax", "figures": {"current": tax}},
                         {"caption": "Earlier years", "figures": {"current": earlier}}]}
    payload = _payload(statement_rows=[block("consolidated", "1523.02", "27.05"),
                                       block("standalone", "1419.31", "23.21")])
    cite = [SourceRef(statement="profit_and_loss", entity=e, caption=c)
            for e in ("consolidated", "standalone") for c in ("Current Tax", "Earlier years")]
    ans = LineItemAnswer(key="k", role="component", signs=[1, 1, 1, 1], sources=cite)
    group = standin.KeyLine(current="1550.07")
    assert standin.grade_answer(payload, ans, group, "current", None, "consolidated")[0] == "right"
    company = standin.KeyLine(current="1442.52")
    assert standin.grade_answer(payload, ans, company, "current", None, "standalone")[0] == "right"
    one = LineItemAnswer(key="k", role="component", signs=[1, 1], sources=cite[:2])
    assert standin.grade_answer(payload, one, group, "current", None, "standalone")[0] == "right", \
        "an answer naming one entity is graded as it stands"


def test_grade_leaves_out_a_line_the_key_computes():
    """Like coverage: a key line whose `where` starts "computed" is printed nowhere, so no citation
    can be right or wrong about it."""
    from app.services.line_item_llm import LineItemAnswer
    ans = LineItemAnswer(key="k", sources=[])
    kl = standin.KeyLine(current="5.00", where="computed: revenue less cost of sales")
    assert standin.grade_answer(_payload(), ans, kl, "current", None)[0] == "derived"


def test_grade_names_a_statement_row_by_the_section_it_is_under():
    """Laurus prints "Financial liabilities Borrowings" under non-current and under current
    liabilities: a citation naming the row's `under` in `group` picks one, as the stage does."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef
    payload = _payload(statement_rows=[{"statement": "balance_sheet", "entity": "consolidated",
        "rows": [{"caption": "Financial liabilities Borrowings", "under": "Non-current liabilities",
                  "figures": {"current": "611.34"}},
                 {"caption": "Financial liabilities Borrowings", "under": "Current liabilities",
                  "figures": {"current": "1786.15"}}]}])
    kl = standin.KeyLine(current="1786.15")

    def grade(group):
        ans = LineItemAnswer(key="k", sources=[SourceRef(
            statement="balance_sheet", caption="Financial liabilities Borrowings", group=group)])
        return standin.grade_answer(payload, ans, kl, "current", None)[0]

    assert grade("Current liabilities") == "right"
    assert grade("Non-current liabilities") == "wrong_figure"
    assert grade("") == "ambiguous"


def test_a_text_line_is_graded_by_the_words_it_gives_not_scored_blank():
    """A text key line (the auditor, the opinion) is graded by normalised exact match against what
    the filing prints, after the gateway's own check that the page it cites prints it. A blank was
    graded `rightly_blank` before — the key had no figure for it — however plainly it was printed."""
    from app.services.line_item_llm import LineItemAnswer, LineItemTextAnswer
    payload = {"line_items": [{"key": "k"}], "text_pages": [
        {"page": 83, "text": "The engagement partner is Wendy Kee.\nErnst &  Young\nCertified "
                             "Public Accountants\nHong Kong"}]}
    kl = standin.KeyLine(text=["Ernst & Young", "安永"])

    def grade(text, page=83):
        return standin.grade_text(payload, LineItemTextAnswer(key="k", text=text, page=page), kl)[0]

    assert grade("Ernst & Young") == "right"
    assert grade("ERNST &YOUNG") == "right"                  # case and spacing do not count
    said = {"line_items": [{"key": "o"}], "text_pages": [{"page": 9, "text": "In our opinion, a\n"
                                                                             "true view."}]}
    opinion = LineItemTextAnswer(key="o", text="In our opinion, a true view", page=9)
    assert standin.grade_text(said, opinion, standin.KeyLine(text="In our opinion, a true view."))[0] \
        == "right"                                             # nor the stop that ends it
    assert grade("Certified Public Accountants") == "wrong_text"
    assert grade("KPMG") == "unresolved"                      # the page does not print it
    assert grade("Ernst & Young", page=84) == "unresolved"    # a page the request did not supply
    assert grade("") == "missed"
    assert standin.grade_text(payload, LineItemAnswer(key="k"), kl)[0] == "missed"
    assert standin.grade_text(payload, LineItemTextAnswer(key="k"), standin.KeyLine(text=[]))[0] \
        == "rightly_blank"


def test_a_text_key_line_loads_and_is_not_counted_as_a_figure(tmp_path):
    path = tmp_path / "key.json"
    path.write_text(json.dumps({"filing": "x", "lines": {
        "statement_setup_controls__accountant": {"text": ["Ernst & Young"], "page": 83}}}),
        encoding="utf-8")
    kl = standin.load_key(path).lines["statement_setup_controls__accountant"]
    assert kl.text == ["Ernst & Young"] and kl.current is None and kl.prior is None
def test_grade_files_an_unstated_rows_column_under_the_year_its_table_states():
    """000685's purchases table prints 本期发生额 | 获批的交易额度 | 上期发生额: rows with the
    approved-limit column went out `period: unstated`, their neighbours `per column` with the
    years. A column citation of an unstated row is filed under the year the block states for that
    heading — as the stage files it — and, where no row of the block states it (300391's one-row
    purchases table), under the year the heading's words name. A heading that names no year keeps
    the old reading: the reporting year's, once."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef
    payload = _payload(notes=[
        {"note": "十七、84", "title": "购销商品、提供和接受劳务的关联交易", "unit": "CNY units (x1)", "rows": [
            {"caption": "污泥处理费", "period": "unstated",
             "figures": {"本期发生额": "21750735.29", "获批的交易额度": "54300000.00",
                         "上期发生额": "44340688.41"}},
            {"caption": "采购自来水", "period": "per column",
             "periods": {"本期发生额": "current", "上期发生额": "prior"},
             "figures": {"本期发生额": "17047658.93", "上期发生额": "11951210.73"}}]},
        {"note": "十四、5", "title": "采购商品/接受劳务情况表", "unit": "CNY units (x1)", "rows": [
            {"caption": "采购商品", "period": "unstated",
             "figures": {"本期发生额": "0.00", "获批的交易额度": "0.00", "上期发生额": "13933183.66"}}]}])

    def grade(note, captions, kl, period):
        ans = LineItemAnswer(key="k", role="component", signs=[1] * 2 * len(captions), sources=[
            SourceRef(note=note, caption=c, column=col, period="unstated")
            for c in captions for col in ("本期发生额", "上期发生额")])
        return standin.grade_answer(payload, ans, kl, period, None)[0]

    both = standin.KeyLine(current="38798394.22", prior="56291899.14")
    assert grade("十七、84", ["污泥处理费", "采购自来水"], both, "current") == "right"
    assert grade("十七、84", ["污泥处理费", "采购自来水"], both, "prior") == "right"
    one = standin.KeyLine(current="0.00", prior="13933183.66")
    assert grade("十四、5", ["采购商品"], one, "current") == "right"
    assert grade("十四、5", ["采购商品"], one, "prior") == "right"
    limit = LineItemAnswer(key="k", sources=[SourceRef(note="十四、5", caption="采购商品",
                                                       column="获批的交易额度")])
    assert standin.grade_answer(payload, limit, standin.KeyLine(current="0.00"), "current", None)[0] == "right"
    assert standin.grade_answer(payload, limit, standin.KeyLine(current="0.00"), "prior",
                                None)[0] == "rightly_blank"


# ---- figures compare exactly, at the printed precision ------------------------------------------

def test_grade_compares_a_figure_exactly_at_its_printed_precision():
    """The grader allowed 0.5%, which graded five wrong rows right: 688008's net trade receivables
    (387,791,885.96, the face row cited) for the gross line (389,736,337.95, an allowance of
    0.499%), BYD's PPE purchases without the leasehold land (-2,848,294 for -2,849,091). A figure
    printed to the cent compares to the cent; one printed in thousands, in thousands."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef
    payload = _payload(notes=[{"note": "七、5", "title": "应收账款", "rows": [
        {"caption": "应收账款", "figures": {"current": "387,791,885.96", "prior": "294,253,723.40"}},
        {"caption": "Purchases of PPE", "figures": {"current": "(2,848,294)"}},
        {"caption": "Depreciation", "figures": {"current": "3,532,249.40"}}]}])

    def grade(caption, kl, period="current", unit=None):
        ans = LineItemAnswer(key="k", sources=[SourceRef(note="七、5", caption=caption)])
        return standin.grade_answer(payload, ans, kl, period, unit)[0]

    gross = standin.KeyLine(current="389,736,337.95", prior="295,728,366.40")
    assert grade("应收账款", gross) == "wrong_figure"
    assert grade("应收账款", gross, "prior") == "wrong_figure"
    assert grade("应收账款", standin.KeyLine(current="387,791,885.96")) == "right"
    assert grade("应收账款", standin.KeyLine(current="387,791,885.97")) == "wrong_figure"   # one cent
    assert grade("Purchases of PPE", standin.KeyLine(current="-2,849,091")) == "wrong_figure"
    assert grade("Purchases of PPE", standin.KeyLine(current="-2,848,294")) == "right"
    # A key given to fewer decimals than the row compares at the key's precision.
    assert grade("Depreciation", standin.KeyLine(current="3,532,249")) == "right"
    assert grade("Depreciation", standin.KeyLine(current="3,524,658")) == "wrong_figure"


def test_a_figure_in_another_unit_compares_at_the_coarser_printing():
    """1,901.50 printed in 万元 is a figure to the hundred yuan: it is the key's 19,015,000.00 and
    19,015,040.00 (the filing's own rounding), and not 19,015,123.45."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef
    payload = _payload(notes=[{"note": "十四、5", "title": "关键管理人员报酬", "unit": "CNY ten thousands (x10,000)",
                               "rows": [{"caption": "关键管理人员报酬", "figures": {"current": "1901.50"}}]}])
    ans = LineItemAnswer(key="k", sources=[SourceRef(note="十四、5", caption="关键管理人员报酬")])
    unit = "CNY yuan (单位：元)"
    for key, want in (("19,015,000.00", "right"), ("19,015,040.00", "right"),
                      ("19,015,123.45", "wrong_figure"), ("19,100,000.00", "wrong_figure")):
        assert standin.grade_answer(payload, ans, standin.KeyLine(current=key), "current", unit)[0] == want, key
    assert standin._same(1901.5, 19015040.0, 2, 2, 1e4, 1.0)
    assert not standin._same(3524658.0, 3532249.0, 0, 0)


def test_coverage_does_not_carry_a_figure_by_an_unrelated_row_near_it(tmp_path: Path):
    """000685's gross trade receivables (1,879,998,245.12) are not in their request; coverage said
    `carried_other_key` because '其他非流动资产' printed a figure within 0.5% of it. A near figure
    carries nothing; the exact one does, and so does the exact figure printed in another unit."""
    rows = [{"caption": "其他非流动资产", "figures": {"current": "1,200.00", "prior": "1,875,000,000.00"}},
            {"caption": "应收票据", "figures": {"current": "1,215,045,260.01"}}]
    _folder_with(tmp_path, _payload(statement_rows=[{"statement": "balance_sheet", "entity": "consolidated",
                                                     "rows": rows}], notes=[]))
    key = standin.Key(filing="x", template="t", lines={
        "bs_icon__finished_goods": standin.KeyLine(current="1879998245.12", prior="1215045260.01")})
    status = {r["period"]: r["status"] for r in standin.coverage_of(tmp_path, key)}
    assert status == {"current": "not_carried", "prior": "carried_other_key"}
    req = _request(_payload(notes=[], statement_rows=[{"statement": "balance_sheet", "rows": rows}]))
    assert standin.payload_has(req, "1,879,998,245.12") == []
    assert standin.prose_has(_request(_payload(notes=[{"note": "1", "title": "t", "rows": [],
                                                      "prose": "receivables of 1,875,000,000.00"}])),
                             "1,879,998,245.12") == []


def test_a_sum_is_made_of_the_periods_own_figures(tmp_path: Path):
    """PetroChina's current impairment adjustment, 3,152, is not in its request (one of its parts
    was sent for the prior year only); four unrelated prior-year gains add up to 3,152, and coverage
    called it carried. The addends of this year's total are this year's figures."""
    rows = [{"caption": "Accrual of provision", "figures": {"current": "743", "prior": "43"}},
            {"caption": "Write down in inventories", "figures": {"current": "2,367", "prior": "6,411"}},
            {"caption": "Impairment of other non-current assets", "figures": {"prior": "259"}},
            {"caption": "Gain A", "figures": {"current": "-291", "prior": "-267"}},
            {"caption": "Gain B", "figures": {"current": "-865", "prior": "-102"}},
            {"caption": "Gain C", "figures": {"current": "-4673", "prior": "-2008"}},
            {"caption": "Gain D", "figures": {"current": "-11", "prior": "-775"}}]
    _folder_with(tmp_path, _payload(notes=[], statement_rows=[{"statement": "cash_flow", "entity": "consolidated",
                                                               "rows": rows}]))
    key = standin.Key(filing="x", template="t", lines={"bs_icon__finished_goods": standin.KeyLine(
        current="3,152", prior="6,713", where="'Accrual' 743 / 43 + 'Write down' 2,367 / 6,411 + 'Impairment' 42 / 259",
        parts={"current": ["743", "2,367", "42"], "prior": ["43", "6,411", "259"]})})
    status = {r["period"]: r["status"] for r in standin.coverage_of(tmp_path, key)}
    assert status == {"current": "not_carried", "prior": "carried_as_parts"}


def test_a_line_left_to_the_rollup_and_computed_as_printed_is_derived(tmp_path: Path):
    """PetroChina prints one 'Accounts receivable' row; the answer gives it to the gross line and
    leaves the net line to the rollup with no sources, as its definition says when no subtotal is
    printed. The run publishes the net line COMPUTED from its parts, 71,610 / 69,006 — the printed
    figures. That is `derived`, not `missed`; a computed figure the filing does not print stays
    `missed`."""
    from app.services.line_item_llm import LineItemReply as Reply
    req = _folder_with(tmp_path, _payload(line_items=[{"key": "bs_ca__net_trade_receivables", "label": "Net",
                                                        "notes_supplied": [], "statement": "balance_sheet"}]))
    (tmp_path / "index.json").write_text(json.dumps({"template": "output_csv_hk_v1_template.json",
                                                     "requests": [{"id": req["id"]}]}))
    (tmp_path / "answers").mkdir()
    (tmp_path / "answers" / f"{req['id']}.txt").write_text(json.dumps(
        {"answers": [{"key": "bs_ca__net_trade_receivables", "sources": []}]}))
    Reply.model_validate_json((tmp_path / "answers" / f"{req['id']}.txt").read_text())
    run = tmp_path / "run"
    run.mkdir()
    (run / "rows.json").write_text(json.dumps([{
        "canonical_key": "bs_ca__trade_receivables_gross", "source_label": "Accounts receivable",
        "printed_in": "face", "role": "line", "is_computed": False, "mapping_method": "llm", "flags": [],
        "values": [{"period_label": p, "basis": "consolidated", "value": v, "value_raw": v}
                   for p, v in (("current", "71610"), ("prior", "69006"))]}]))
    computed = standin.computed_figures(tmp_path, "consolidated")
    assert computed[("bs_ca__net_trade_receivables", "current")] == 71610.0
    key = standin.Key(filing="x", basis="consolidated", lines={
        "bs_ca__net_trade_receivables": standin.KeyLine(current="71610", prior="69500")})
    grades = {r["period"]: r["grade"] for r in standin.grade_folder(tmp_path, key)}
    assert grades == {"current": "derived", "prior": "missed"}
    # Without a run that computes it, an empty answer is a miss, as before.
    (run / "rows.json").write_text("[]")
    assert {r["grade"] for r in standin.grade_folder(tmp_path, key)} == {"missed"}


def test_grade_reads_a_column_legend_down_to_the_rows_it_is_not_repeated_on():
    """The request sends a row's `columns` only when it changes, so 000709's 合计 rows carry none
    and read the legend of the 应收账款 row above them. A citation of 合计 by "期初余额 · 坏账准备"
    names the （续） table's cell, keyed 'prior:allowance' — the prior year — as the gateway
    resolves it; it was graded unresolved."""
    from app.services.line_item_llm import LineItemAnswer
    from app.services.mapping import SourceRef
    closing = {"current": "期末余额 · 账面余额", "current:allowance": "期末余额 · 坏账准备"}
    opening = {"prior": "期初余额 · 账面余额", "prior:allowance": "期初余额 · 坏账准备"}
    payload = _payload(notes=[
        {"note": "七、3", "title": "以摊余成本计量的应收账款", "unit": "CNY units (x1)", "rows": [
            {"caption": "应收账款", "columns": closing,
             "figures": {"current": "2689546185.47", "current:allowance": "1394836830.02"}},
            {"caption": "合计",
             "figures": {"current": "2689546185.47", "current:allowance": "1394836830.02"}},
            {"caption": "应收账款", "columns": opening,
             "figures": {"prior": "2595130766.28", "prior:allowance": "1445376980.12"}},
            {"caption": "合计",
             "figures": {"prior": "2595130766.28", "prior:allowance": "1445376980.12"}}]}])
    ans = LineItemAnswer(key="k", sources=[
        SourceRef(note="七、3", table="以摊余成本计量的应收账款", caption="合计", column=col)
        for col in ("期末余额 · 坏账准备", "期初余额 · 坏账准备")])
    kl = standin.KeyLine(current="1394836830.02", prior="1445376980.12")
    assert standin.grade_answer(payload, ans, kl, "current", None)[0] == "right"
    assert standin.grade_answer(payload, ans, kl, "prior", None)[0] == "right"
