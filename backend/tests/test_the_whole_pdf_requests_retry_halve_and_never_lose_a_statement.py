"""THE WHOLE-PDF REQUESTS: the smallest goes first, a failed one is asked again once, a cut-off one
too large to double is asked as two halves, and a statement never loses its lines silently
(`stages.whole_pdf.WholePdfStage._ask`, `_request`, `_lost_lines`; `services.whole_pdf.Plan`).

What the stage did (findings whole_pdf_no_room_retry_and_first_request_gate,
cut_off_not_retried_for_statements, partial_failure_blanks_no_fallback, timeouts_and_fallback_cost):

* the first request was the balance sheet's, the largest (213 lines on the HK set), so a gateway
  that stalled held the run up to 2 x 600 s before the fallback could start;
* a reply cut off on the balance sheet (budget 32,000), the income statement (24,564) or the cash
  flow (19,064) was re-raised at once — `complete_with_room` only doubles a budget under
  ROOM_CEILING (16,000);
* only part 1's first request could send the run back to the rules: a later request that failed
  left its statement's lines blank with one log line.

The provider is a SCRIPTED STAND-IN on a synthetic filing (a balance sheet and its notes): it
answers from a fixed table and fails or cuts off where a test says. It shows what the stage does
with a given outcome, never what a model would answer.
"""
from __future__ import annotations

import json
import pathlib
import re

import fitz
import pytest

from app.config import get_settings
from app.ports.llm import cut_off
from app.ports.registry import registry
from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"

_BALANCE_SHEET = ["CONSOLIDATED BALANCE SHEETS", "(in millions of U.S. dollars)",
                  "                                   2024        2023",
                  "Cash and cash equivalents          1,200       1,000",
                  "Accounts receivable                  300         250",
                  "Total current assets               1,500       1,250",
                  "Total assets                       4,500       4,250",
                  "Total liabilities                  2,000       1,900",
                  "Total stockholders' equity         2,500       2,350",
                  "Total liabilities and stockholders' equity   4,500   4,250"]


def _notes(n: int) -> list[str]:
    return ["Notes to consolidated financial statements",
            f"Note {n} Receivables: trade 280, other 20, total 300"]


#: Cover, balance sheet, four note pages: one part at 100 pages a request, two at 3 (the balance
#: sheet carried in both).
_FILING = [["Annual Report 2024 - Example Corp"], _BALANCE_SHEET,
           _notes(5), _notes(6), _notes(7), _notes(8)]

_TABLE = {"bs_ca__cash_and_cash_equivalents": [("current", "1200"), ("prior", "1000")],
          "bs_ca__accounts_receivable": [("current", "300"), ("prior", "250")],
          "bs_ca__total_current_assets": [("current", "1500"), ("prior", "1250")],
          "bs_ca__total_assets": [("current", "4500"), ("prior", "4250")]}


def _pdf(pages):
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        for i, text in enumerate(lines):
            page.insert_text((40, 72 + 16 * i), text, fontsize=9)
    return doc.tobytes()


def _cut_off_error():
    """What a reply cut off at its budget raises: JSON that stops part-way."""
    try:
        W.WholePdfReply.model_validate_json('{"units": "x", "lines": [{"key": "a", "fig')
    except Exception as exc:  # noqa: BLE001 - the error itself is the point
        assert cut_off(exc)
        return exc
    raise AssertionError("not cut off")


class _Provider:
    """A STAND-IN, not a model: records every request; `fail(call, nth)` returns an error to
    raise for the nth request (1 = the first) of that part and statement, or None to answer."""
    id = "scripted-requests"
    takes_documents = True

    def __init__(self, fail=None):
        self.fail = fail or (lambda call, nth: None)
        self.calls: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        content = messages[0]["content"]
        call = {"statement": re.search(r"^Template lines \(([^)]*)\):", content, re.MULTILINE).group(1),
                "keys": re.findall(r"^- ([^\s:]+):", content, re.MULTILINE), "max_tokens": max_tokens,
                "part": int(re.search(r"document-(\d+)", messages[0]["documents"][0]["name"])
                            .group(1))}
        self.calls.append(call)
        nth = sum(1 for c in self.calls
                  if (c["statement"], c["part"]) == (call["statement"], call["part"]))
        error = self.fail(call, nth)
        if error is not None:
            raise error
        lines = [{"key": key, "figures": [{"basis": "consolidated", "period": p, "amount": a,
                                           "page": 1, "caption": key} for p, a in figs]}
                 for key, figs in _TABLE.items() if key in call["keys"]]
        return (response_schema.model_validate({"units": "in millions of U.S. dollars",
                                                "lines": lines}),
                {"input_tokens": 1000, "output_tokens": 100, "model": "scripted"})


@pytest.fixture
def run(monkeypatch):
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    def _run(provider, **extraction):
        s = get_settings()
        for name, value in {"whole_pdf_to_model": True, "whole_pdf_pages": "statements",
                            "llm_disclosures": False, "llm_parallel_requests": 1,
                            **extraction}.items():
            monkeypatch.setattr(s.extraction, name, value)
        monkeypatch.setitem(registry._factories.setdefault("llm", {}), provider.id,
                            lambda: provider)
        monkeypatch.setattr(s.llm, "provider", provider.id)
        st = load_line_item_set(json.loads((TEMPLATES / "output_csv_usgaap_line_items.json")
                                           .read_text(encoding="utf-8")), resolve=True)
        tpl = load_template(json.loads((TEMPLATES / "output_csv_usgaap_v1_template.json")
                                       .read_text(encoding="utf-8")))
        return run_extraction(_pdf(_FILING), filename="example.pdf",
                              ontology=build_working_view(st), template=tpl, line_items=st)
    return _run


def _value(doc, key, period="current"):
    for row in doc.line_items:
        if row.canonical_key == key:
            for ev in row.values.values():
                if ev.period_label == period and ev.value is not None:
                    return str(ev.value)
    return None


def _fell_back(ctx) -> str | None:
    return next((line for line in ctx.logs if line.startswith("whole_pdf:fallback(")), None)


def _of(provider, statement, part=None):
    return [c for c in provider.calls
            if c["statement"] == statement and (part is None or c["part"] == part)]


def test_the_first_request_is_the_smallest(run):
    """The US GAAP set's requests are 47 (balance sheet), 45, 37 and 28 (cash flow) lines. The
    balance sheet, the one statement the synthetic filing prints a face of, is asked against its
    face alone, in a part of its own sent last (`services.whole_pdf.Plan`); the part holding the
    notes goes first, its smallest request first, then the largest."""
    provider = _Provider()
    doc, ctx = run(provider)
    sizes = [len(c["keys"]) for c in provider.calls]
    assert provider.calls[0]["statement"] == "cash flow" and sizes[0] == min(sizes)
    first_part = [len(c["keys"]) for c in provider.calls if c["part"] == 1]
    assert first_part[1:] == sorted(first_part[1:], reverse=True)     # then the largest first
    assert [(c["part"], c["statement"]) for c in provider.calls][-1] == (2, "balance sheet")
    assert ctx.whole_pdf["first_request"] == {"statement": "cash_flow", "lines": sizes[0]}
    assert any("first=cash_flow(" in line for line in ctx.logs if line.startswith("whole_pdf:plan"))
    assert _value(doc, "bs_ca__cash_and_cash_equivalents") == "1200"


def test_a_first_request_that_fails_twice_still_sends_the_run_to_the_rules_at_once(run):
    provider = _Provider(fail=lambda call, nth: RuntimeError("HTTP 503: unavailable")
                         if call["statement"] == "cash flow" else None)
    _doc, ctx = run(provider)
    assert "the first request failed" in (_fell_back(ctx) or "")
    assert [c["statement"] for c in provider.calls] == ["cash flow", "cash flow"]   # one retry
    assert ctx.only_stages is None and ctx.mapping_strategy != "whole_pdf"


def test_a_request_that_fails_is_asked_once_more(run):
    provider = _Provider(fail=lambda call, nth: RuntimeError("HTTP 400: try again")
                         if call["statement"] == "balance sheet" and nth == 1 else None)
    doc, ctx = run(provider)
    assert _fell_back(ctx) is None
    assert len(_of(provider, "balance sheet")) == 2
    assert _value(doc, "bs_ca__cash_and_cash_equivalents") == "1200"
    assert ctx.whole_pdf["retries"] == 1 and ctx.llm_failures == 1
    assert any(line.startswith("whole_pdf:request_retry part=2") for line in ctx.logs)
    assert ctx.whole_pdf["requests_failed"] == [] and ctx.whole_pdf["lines_lost"] == []


def test_a_statement_whose_request_fails_after_its_retry_is_read_by_the_rules(run):
    """It was logged and its lines left blank; the run still reported a whole-PDF read."""
    provider = _Provider(fail=lambda call, nth: RuntimeError("HTTP 500")
                         if call["statement"] == "balance sheet" else None)
    _doc, ctx = run(provider)
    reason = _fell_back(ctx) or ""
    assert "the balance sheet's lines were lost" in reason and "part 1" in reason
    assert ctx.only_stages is None and ctx.mapping_strategy != "whole_pdf"
    assert "stage:map_line_items:start" in ctx.logs           # the rules route carried on


def test_other_lost_lines_are_named_and_the_run_carries_on(run):
    provider = _Provider(fail=lambda call, nth: RuntimeError("HTTP 500")
                         if call["statement"] == "notes" else None)
    doc, ctx = run(provider)
    assert _fell_back(ctx) is None and ctx.mapping_strategy == "whole_pdf"
    notes = _of(provider, "notes")[0]["keys"]
    assert ctx.whole_pdf["lines_lost"] == sorted(notes)
    assert [(f["part"], f["statement"], len(f["lines"])) for f in ctx.whole_pdf["requests_failed"]
            ] == [(1, "notes", len(notes))]
    assert any(line.startswith(f"whole_pdf:lines_lost={len(notes)}") for line in ctx.logs)
    assert _value(doc, "bs_ca__cash_and_cash_equivalents") == "1200"


def test_a_cut_off_reply_too_large_to_double_is_asked_as_two_halves(run):
    """The balance sheet's 47 lines are budgeted 11,364 tokens: twice that is past ROOM_CEILING,
    so `complete_with_room` would re-raise. Its halves (23 and 24 lines) are asked instead."""
    provider = _Provider(fail=lambda call, nth: _cut_off_error()
                         if call["statement"] == "balance sheet" and len(call["keys"]) > 30
                         else None)
    doc, ctx = run(provider)
    sheet = _of(provider, "balance sheet")
    assert [len(c["keys"]) for c in sheet] == [47, 23, 24]
    assert sheet[0]["max_tokens"] == W.max_tokens_for(47) == 11_364
    assert sorted(sheet[1]["keys"] + sheet[2]["keys"]) == sorted(sheet[0]["keys"])
    assert _fell_back(ctx) is None
    assert _value(doc, "bs_ca__cash_and_cash_equivalents") == "1200"
    assert _value(doc, "bs_ca__total_assets", "prior") == "4250"
    assert ctx.whole_pdf["halved"] == 1 and ctx.whole_pdf["lines_lost"] == []
    assert any(line.startswith("whole_pdf:cut_off part=2 lines=47 (balance_sheet)")
               and "two halves of 23 and 24" in line for line in ctx.logs)


def test_a_small_cut_off_reply_is_asked_again_with_twice_the_room(run):
    """The cash flow's 28 lines are budgeted 7,184: within ROOM_CEILING doubled, so the port's
    own retry asks again with 14,368, as for every other caller (`complete_with_room`)."""
    provider = _Provider(fail=lambda call, nth: _cut_off_error()
                         if call["statement"] == "cash flow" and nth == 1 else None)
    _doc, ctx = run(provider)
    assert [c["max_tokens"] for c in _of(provider, "cash flow")] == [7_184, 14_368]
    assert _fell_back(ctx) is None and ctx.whole_pdf["halved"] == 0


def test_the_halving_has_a_bound(run):
    provider = _Provider(fail=lambda call, nth: _cut_off_error()
                         if call["statement"] == "balance sheet" else None)
    _doc, ctx = run(provider)
    sheet = _of(provider, "balance sheet")
    assert min(len(c["keys"]) for c in sheet) >= 47 // 2 ** 3          # three halvings at most
    assert len(sheet) <= 1 + 2 * (2 + 4 + 8)                           # each half also doubled
    assert "the balance sheet's lines were lost" in (_fell_back(ctx) or "")


def test_a_statement_answered_in_another_part_holding_its_face_is_not_lost(run):
    """Two parts holding the notes, the balance sheet carried in both, and the balance sheet alone
    as part 3, where its face lines are asked. Its request there fails, after its retry: it is
    asked again, whole, in part 1, which holds the same face, so the statement keeps its lines and
    the run goes on."""
    provider = _Provider(fail=lambda call, nth: RuntimeError("HTTP 500")
                         if call["statement"] == "balance sheet" and call["part"] == 3 else None)
    doc, ctx = run(provider, whole_pdf_pages_per_request=3)
    assert ctx.whole_pdf["parts"] == 3 and ctx.whole_pdf["pages_in_every_part"] == [2]
    assert ctx.whole_pdf["pages_per_part"] == [3, 3, 1]
    assert _fell_back(ctx) is None
    assert [f["part"] for f in ctx.whole_pdf["requests_failed"]] == [3]
    assert [(c["part"], len(c["keys"])) for c in _of(provider, "balance sheet")
            ] == [(3, 47), (3, 47), (1, 47)]
    assert any(line.startswith("whole_pdf:asked_again part=1 lines=47 (balance_sheet)")
               for line in ctx.logs)
    assert ctx.whole_pdf["lines_lost"] == []
    assert _value(doc, "bs_ca__cash_and_cash_equivalents") == "1200"


def test_a_half_of_a_statements_request_that_fails_is_asked_again_where_the_notes_are(run):
    """The balance sheet's request to the statements alone (part 3) is cut off and asked as two
    halves; the first half answers, the second fails after its retry. Its face lines are asked
    nowhere else, so that half is asked again in part 1, which holds the same face — the
    statement keeps every line and the run is not sent to the rules for 24 lines one retry away.
    (Before 01d4a34's request-size change every part was asked them, so another part covered a
    half that failed in one.)"""
    def fail(call, nth):
        if call["statement"] != "balance sheet" or call["part"] != 3:
            return None
        if len(call["keys"]) > 30:
            return _cut_off_error()
        return RuntimeError("HTTP 500") if len(call["keys"]) == 24 else None   # 23 answers

    provider = _Provider(fail=fail)
    doc, ctx = run(provider, whole_pdf_pages_per_request=3)
    sheet = [(c["part"], len(c["keys"])) for c in _of(provider, "balance sheet")]
    assert _fell_back(ctx) is None, _fell_back(ctx)
    assert sheet == [(3, 47), (3, 23), (3, 24), (3, 24), (1, 24)]
    assert ctx.whole_pdf["lines_lost"] == []
    assert any(line.startswith("whole_pdf:asked_again part=1 lines=24 (balance_sheet)")
               for line in ctx.logs)
    assert _value(doc, "bs_ca__cash_and_cash_equivalents") == "1200"
    assert _value(doc, "bs_ca__total_assets", "prior") == "4250"
