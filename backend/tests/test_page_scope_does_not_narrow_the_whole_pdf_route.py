"""PAGE SCOPE DOES NOT NARROW THE WHOLE-PDF ROUTE: when the whole PDF is sent to the model
(`extraction.whole_pdf_to_model`), every page is sent and every line is asked, whatever the page
classifier found and whatever is set on the Page Scope screen (`services.whole_pdf.pages_to_send`,
`plan`, `stages.whole_pdf`).

The question that led here: "why should page scope be impacting in cases where I am passing the
full PDF as object?" It did, three ways. `extraction.whole_pdf_pages = "statements"` (the default)
sent the classifier's statement and note pages; either setting was narrowed by a person's page
selection and the sections they switched off; and a switched-off statement's lines were not asked.
On Vivo Energy Mauritius the classifier found no statement, and the auditor's report was not sent.

Now Page Scope narrows only what THE RULES read beside the model (`pdf_extract`): the reading that
checks the model's figures, fills the lines it leaves empty, and reads the filing when the model
cannot. The classifier's reading is still used after and beside — the faces carried in every part,
the statements alone — and never cuts a page or a line from what is sent. A page the rules left
unread went to the model, so once a part holding it is answered it is not an unread page; and a
switched-off statement whose lines a request answered is checked on the model's figures
(`ctx.whole_pdf["statements_read"]`, `services.unread_pages.sections_left_unread`). A page whose
every part failed, and a statement no request answered, stay unread.

The providers here are STAND-INS that record what they are sent; no model is called.
"""
from __future__ import annotations

import base64
import inspect
import json
import pathlib
import re
from types import SimpleNamespace
from typing import ClassVar

import fitz
import pytest

from app.config import ExtractionSettings, get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import PageKind
from app.core.stage import PipelineContext
from app.ports.registry import registry
from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W
from app.services.unread_pages import sections_left_unread, statements_not_read

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"

O, COVER, N = (PageKind.OTHER, None), (PageKind.COVER, None), (PageKind.NOTES, None)
BS, PL, CF = ((PageKind.FACE, "balance_sheet"), (PageKind.FACE, "profit_and_loss"),
              (PageKind.FACE, "cash_flow"))


@pytest.fixture(scope="module")
def hk():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


def _pdf(n: int, headings: dict[int, str] | None = None) -> bytes:
    """`n` pages, each printing its own number last, so a part's pages can be told apart; a page
    in `headings` prints that heading above it."""
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page()
        if i in (headings or {}):
            page.insert_text((40, 48), headings[i], fontsize=11)
        page.insert_text((40, 72), f"sheet {i + 1}", fontsize=9)
    return doc.tobytes()


def _doc(kinds) -> DocumentModel:
    doc = DocumentModel(filename="x.pdf")
    doc.pages = [PageSource(index=i, kind=k, statement=st) for i, (k, st) in enumerate(kinds)]
    return doc


class _Recording:
    """A STAND-IN that takes files, records the pages each request's file holds and the lines it
    asks, and answers nothing."""
    id = "recording-pdf"
    takes_documents = True

    def __init__(self):
        self.requests: list[tuple[tuple[int, ...], tuple[str, ...]]] = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        del system, temperature, max_tokens
        m = messages[0]
        part = fitz.open(stream=base64.b64decode(m["documents"][0]["data"]), filetype="pdf")
        pages = tuple(int(part.load_page(i).get_text().split()[-1]) - 1
                      for i in range(part.page_count))
        keys = tuple(line[2:].split(":", 1)[0] for line in m["content"].splitlines()
                     if line.startswith("- ") and ":" in line)
        self.requests.append((pages, keys))
        return response_schema.model_validate({"units": "", "lines": []}), {"model": "stand-in"}


@pytest.fixture
def stage(monkeypatch, hk):
    """The whole-PDF stage on a classified document, with Page Scope as given."""
    from app.stages.whole_pdf import WholePdfStage

    s = get_settings()
    for name, value in {"whole_pdf_to_model": True, "llm_parallel_requests": 1}.items():
        monkeypatch.setattr(s.extraction, name, value)

    def _run(kinds, *, included=None, off=None, headings=None):
        provider = _Recording()
        monkeypatch.setitem(registry._factories.setdefault("llm", {}), provider.id,
                            lambda: provider)
        monkeypatch.setattr(s.llm, "provider", provider.id)
        ctx = PipelineContext(raw_bytes=_pdf(len(kinds), headings))
        ctx.line_items = hk
        if included is not None:
            ctx.included_pages = set(included)
        if off:
            ctx.sections_off = set(off)
        WholePdfStage().run(_doc(kinds), ctx)
        return provider.requests, ctx
    return _run


KINDS = [COVER, O, BS, PL, CF, N, N, O]      # a cover, an auditor's report "other", the notes


def test_a_page_selection_and_a_switched_off_section_do_not_narrow_the_whole_pdf_plan(stage, hk):
    free, _ = stage(KINDS)
    # One page chosen, and the balance sheet and the notes switched off on the Page Scope screen.
    scoped, ctx = stage(KINDS, included={2}, off={"balance_sheet", "notes"})
    assert scoped == free, "Page Scope changed what was sent or asked"
    assert {p for pages, _keys in scoped for p in pages} == set(range(len(KINDS)))
    asked = {k for _pages, keys in scoped for k in keys}
    assert {it.key for it in W.lines_to_ask(hk)} <= asked          # every line, all statements
    assert any(it.key in asked for it in W.lines_to_ask(hk) if W.statement_of(it) ==
               "balance_sheet")
    assert any(line.startswith("whole_pdf:page_scope_not_applied(pages chosen: 1; sections off: "
                               "balance_sheet,notes)") for line in ctx.logs)
    plan_line = next(line for line in ctx.logs if line.startswith("whole_pdf:plan "))
    assert f"pages={len(KINDS)} of {len(KINDS)} (every page)" in plan_line
    # Nothing on the plan takes Page Scope any more.
    params = set(inspect.signature(W.plan).parameters)
    assert not params & {"choice", "included_pages", "sections_off"}
    assert set(inspect.signature(W.pages_to_send).parameters) == {"doc"}


def test_the_classifier_still_adds_the_faces_in_every_part_and_the_statements_alone(hk):
    """The faces are carried in every part and asked alone, and the parts holding the notes still
    hold every page: nothing the classifier found cuts a page from what is sent."""
    raw = _pdf(12)
    doc = _doc([COVER, O, BS, PL, CF] + [N] * 6 + [O])
    plan = W.plan(doc, raw, hk, max_pages=5, max_request_bytes=20 * 1024 * 1024,
                  lines_per_request=250)
    notes = [p for p in plan.parts if not p.faces_only]
    assert sorted({p for part in notes for p in part.pages}) == list(range(12))
    assert plan.carried == [2, 3, 4] and all({2, 3, 4} <= set(p.pages) for p in notes)
    assert [p.pages for p in plan.parts if p.faces_only] == [[2, 3, 4]]
    assert plan.lines == W.lines_to_ask(hk)


def test_the_vivo_shape_sends_the_pages_the_classifier_left_out(stage, hk):
    """THE VIVO SHAPE, as the Mauritius filing showed it: the classifier called most sheets notes,
    found no statement face, and called the cover, the auditor's report and a directors' page
    "other". The old "statements" choice sent the notes sheets only — no statement page told the
    auditor's-report finder where to look — so the report, which prints the auditor's opinion and
    nothing else does, was not sent. Now every page goes, the report among them, and every line is
    asked; with no face found there is nothing to carry and no statements-alone part."""
    kinds = [O, O, O] + [N] * 8 + [O]
    report = {1: "INDEPENDENT AUDITOR'S REPORT", 2: "INDEPENDENT AUDITOR'S REPORT (continued)"}
    plan = W.plan(_doc(kinds), _pdf(len(kinds), report), hk, max_pages=600,
                  max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
    assert plan.pages == list(range(len(kinds))) and plan.why == "every page"
    assert not plan.carried and not any(p.faces_only for p in plan.parts) and not plan.dropped
    assert plan.lines == W.lines_to_ask(hk)
    requests, _ctx = stage(kinds, headings=report)
    sent = {p for pages, _keys in requests for p in pages}
    assert {0, 1, 2, 11} <= sent and sent == set(range(len(kinds)))   # the report, cover, other
    assert {k for _p, keys in requests for k in keys} >= {it.key for it in W.lines_to_ask(hk)}
    # A filing the classifier called nothing on at all goes whole too.
    bare = W.plan(_doc([O] * 9), _pdf(9), hk, max_pages=600, max_request_bytes=20 * 1024 * 1024,
                  lines_per_request=250)
    assert bare.pages == list(range(9)) and bare.lines == W.lines_to_ask(hk)


# ── a saved old value of the knob that is gone ─────────────────────────────────────────────────

def _restart() -> None:
    from app.services import settings_state
    settings_state.reset(persisted=False)
    settings_state.load_persisted()


def _rows() -> dict:
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride
    from app.services import settings_state
    with SessionLocal() as s:
        return {r.key: (r.value or {}).get("v") for r in s.execute(
            select(SettingOverride).where(
                SettingOverride.scope == settings_state.SCOPE_EXTRACTION)).scalars().all()}


@pytest.fixture
def overrides(client):
    from app.services import settings_state
    was = get_settings().extraction.whole_pdf_lines_per_request
    settings_state.reset()
    yield client
    settings_state.reset()
    get_settings().extraction.whole_pdf_lines_per_request = was


def test_a_saved_old_knob_value_is_ignored(overrides):
    """The Settings screen saved every knob whenever one changed, so a deployment has a stored
    "statements" for `whole_pdf_pages`. It must not narrow the route again, must not stop the app
    starting, and must not sit in the table looking like configuration."""
    from app.db.base import SessionLocal
    from app.db.models import SettingOverride
    from app.services import settings_state

    with SessionLocal() as s:
        for key, value in (("whole_pdf_pages", "statements"),
                           ("whole_pdf_lines_per_request", 40)):
            s.add(SettingOverride(scope=settings_state.SCOPE_EXTRACTION, key=key,
                                  value={"v": value}))
        s.commit()
    _restart()
    ex = get_settings().extraction
    assert not hasattr(ex, "whole_pdf_pages")
    assert ex.whole_pdf_lines_per_request == 40                  # the other saved value applies
    rows = _rows()
    assert "whole_pdf_pages" not in rows and rows["whole_pdf_lines_per_request"] == 40
    assert "whole_pdf_pages" in settings_state.RETIRED_KNOBS
    assert all(f["key"] != "whole_pdf_pages"
               for f in settings_state.extraction_config()["fields"])
    # A Settings page still open from before sends it: ignored, and nothing is stored for it.
    tok = overrides.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    resp = overrides.patch("/api/v1/settings", headers={"Authorization": f"Bearer {tok}"},
                           json={"extraction": {"whole_pdf_pages": "statements"}})
    assert resp.status_code == 200 and "whole_pdf_pages" not in _rows()
    # …and a config.toml that still names it binds to nothing.
    assert not hasattr(ExtractionSettings(whole_pdf_pages="statements"), "whole_pdf_pages")


# ── what the rules left unread, on a run the model read ────────────────────────────────────────

def test_a_switched_off_statement_is_read_only_when_a_request_answered_its_lines():
    unread = [{"page": 3, "folio": None, "reason": "section_off", "sections": ["balance_sheet"]}]
    pages = [{"index": 2, "kind": "face", "statement": "balance_sheet"}]
    off = {"balance_sheet"}
    # The rules route, and a whole-PDF run that fell back: nothing in `ctx.whole_pdf`.
    assert sections_left_unread(off, None) == off
    assert statements_not_read(unread, sections_left_unread(off, None), pages) == {
        "balance_sheet"}
    # A whole-PDF run whose requests answered the balance sheet's lines: read.
    answered = {"statements_read": ["balance_sheet", "profit_and_loss"]}
    assert sections_left_unread(off, answered) == set()
    assert statements_not_read([], sections_left_unread(off, answered), pages) == set()
    # …one whose requests answered none of them: still not read, though the page went.
    assert sections_left_unread(off, {"statements_read": ["cash_flow"]}) == off
    assert statements_not_read([], sections_left_unread(off, {"statements_read": []}),
                               pages) == {"balance_sheet"}
    # A whole-PDF run stored before this change carries no `statements_read`: its route did not
    # ask a switched-off statement's lines, so the statement stays not read.
    assert sections_left_unread(off, {"pages_sent": [1, 2, 3]}) == off


class _Scripted:
    """A STAND-IN, not a model: answers the balance sheet from a fixed table."""
    id = "scripted-scope"
    takes_documents = True

    TABLE: ClassVar[dict] = {
        "bs_ca__cash_and_cash_equivalents": [("current", "1200"), ("prior", "1000")],
        "bs_ca__accounts_receivable": [("current", "300"), ("prior", "250")],
        "bs_ca__total_current_assets": [("current", "1500"), ("prior", "1250")],
        "bs_ca__total_assets": [("current", "4500"), ("prior", "4250")],
        "bs_cl__total_liabilities": [("current", "2000"), ("prior", "1900")],
        "bs_equity__total_equity": [("current", "2500"), ("prior", "2350")],
        "bs_cl__total_equity_and_liabilities": [("current", "4500"), ("prior", "4250")]}

    def __init__(self, fail_part: int | None = None):
        self.sent: list[dict] = []
        self.fail_part = fail_part

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        del system, temperature, max_tokens
        m = messages[0]
        self.sent.append(m)
        if int(re.search(r"document-(\d+)", m["documents"][0]["name"]).group(1)) == self.fail_part:
            raise RuntimeError("HTTP 504: gateway timeout")
        part = fitz.open(stream=base64.b64decode(m["documents"][0]["data"]), filetype="pdf")
        # The part's own page that prints the balance sheet, as a model would cite it.
        page = next(i + 1 for i in range(part.page_count)
                    if "BALANCE SHEETS" in part.load_page(i).get_text())
        lines = [{"key": key, "figures": [{"basis": "consolidated", "period": p, "amount": a,
                                           "page": page, "caption": key} for p, a in figs]}
                 for key, figs in self.TABLE.items() if f"- {key}:" in m["content"]]
        return (response_schema.model_validate({"units": "in millions of U.S. dollars",
                                                "lines": lines}),
                {"input_tokens": 1, "output_tokens": 1, "model": "scripted"})


_SHEET = ["CONSOLIDATED BALANCE SHEETS", "(in millions of U.S. dollars)",
          "                                   2024        2023",
          "Cash and cash equivalents          1,200       1,000",
          "Accounts receivable                  300         250",
          "Total current assets               1,500       1,250",
          "Total assets                       4,500       4,250",
          "Total liabilities                  2,000       1,900",
          "Total stockholders' equity         2,500       2,350",
          "Total liabilities and stockholders' equity   4,500   4,250"]


def _note(n: int) -> list[str]:
    return ["Notes to consolidated financial statements",
            f"Note {n} Receivables: trade 280, other 20, total 300"]


def _filing(pages) -> bytes:
    filing = fitz.open()
    for lines in pages:
        page = filing.new_page()
        for i, text in enumerate(lines):
            page.insert_text((40, 72 + 16 * i), text, fontsize=9)
    return filing.tobytes()


@pytest.fixture
def extract(monkeypatch):
    """`run_extraction` on the US GAAP pair with the whole PDF on and this stand-in provider."""
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    def _run(provider, raw, *, included_pages=None, sections_off=None, **extraction):
        s = get_settings()
        for name, value in {"whole_pdf_to_model": True, "llm_disclosures": False,
                            "llm_shared_figure_tiebreak": False, "llm_parallel_requests": 1,
                            **extraction}.items():
            monkeypatch.setattr(s.extraction, name, value)
        monkeypatch.setitem(registry._factories.setdefault("llm", {}), provider.id,
                            lambda: provider)
        monkeypatch.setattr(s.llm, "provider", provider.id)
        st = load_line_item_set(json.loads((TEMPLATES / "output_csv_usgaap_line_items.json")
                                           .read_text(encoding="utf-8")), resolve=True)
        tpl = load_template(json.loads((TEMPLATES / "output_csv_usgaap_v1_template.json")
                                       .read_text(encoding="utf-8")))
        return run_extraction(raw, filename="example.pdf", ontology=build_working_view(st),
                              template=tpl, line_items=st, included_pages=included_pages,
                              sections_off=sections_off)
    return _run


def test_a_balance_sheet_switched_off_on_page_scope_is_read_asked_and_checked(extract):
    """End to end on a three-page filing, the balance sheet switched off on the Page Scope
    screen: the rules leave its page unread, the model is sent it and asked its lines, the page is
    not reported unread, and the balance sheet's checks run on the model's figures."""
    provider = _Scripted()
    doc, ctx = extract(provider, _filing([["Annual Report 2024 - Example Corp"], _SHEET,
                                          _note(5)]),
                       sections_off={"balance_sheet"})
    assert any(line.startswith("extract:sections_off=['balance_sheet']") for line in ctx.logs)
    assert ctx.mapping_strategy == "whole_pdf"
    assert ctx.whole_pdf["pages_sent"] == [1, 2, 3]
    assert ctx.whole_pdf["pages_the_rules_did_not_read"] == [2]     # the rules skipped it…
    assert doc.unread_pages == []                                   # …the model did not
    assert "balance_sheet" in ctx.whole_pdf["statements_read"]
    asked = "".join(m["content"] for m in provider.sent)
    assert "- bs_ca__cash_and_cash_equivalents:" in asked
    cash = next(li for li in doc.line_items
                if li.canonical_key == "bs_ca__cash_and_cash_equivalents")
    assert {v.period_label: str(v.value_raw) for v in cash.values.values()} == {
        "current": "1200", "prior": "1000"}
    status = {(r.rule_id, r.details.get("period_label")): r.status
              for r in doc.structural.results}
    assert status[("identity:bs_balances", "current")] == "pass"
    assert not any("structural:statements_not_read" in line for line in ctx.logs)


def test_a_page_whose_every_part_failed_stays_unread(extract):
    """SENT IS NOT READ. Only the balance sheet chosen on Page Scope, three pages a request: the
    parts are [cover, balance sheet, note 5], [balance sheet, note 6, note 7], [balance sheet,
    note 8] and the balance sheet alone. Every request to part 2 fails. The run goes on — the
    balance sheet is answered in the statements-alone part — but notes 6 and 7 were read by
    nobody: the rules left them out of the selection and the model never answered. They stay on
    the run's unread pages; the pages of the parts that answered come off."""
    provider = _Scripted(fail_part=2)
    doc, ctx = extract(provider, _filing([["Annual Report 2024 - Example Corp"], _SHEET,
                                          _note(5), _note(6), _note(7), _note(8)]),
                       included_pages={1}, whole_pdf_pages_per_request=3)
    assert not any(line.startswith("whole_pdf:fallback(") for line in ctx.logs)
    assert ctx.mapping_strategy == "whole_pdf"
    wp = ctx.whole_pdf
    assert wp["pages_per_part"] == [3, 3, 2, 1]
    assert sorted({f["part"] for f in wp["requests_failed"]}) == [2]
    assert sorted(wp["pages_the_rules_did_not_read"]) == [1, 3, 4, 5, 6]
    assert [(e["page"], e["reason"]) for e in doc.unread_pages] == [(4, "out_of_scope"),
                                                                     (5, "out_of_scope")]
    assert "balance_sheet" in wp["statements_read"]


def test_the_review_recompute_reads_what_the_stored_run_says_the_model_read():
    """`documents._structural_for_run` recomputes the checks from the stored run. A switched-off
    balance sheet is "not read" unless the run's `mapping.whole_pdf.statements_read` names it: a
    rules run, a whole-PDF run no request of which answered it, and a whole-PDF run stored before
    every line was asked (no `statements_read`; its route did not ask a switched-off statement's
    lines) all keep it not read."""
    from app.api.routes.documents import _structural_for_run

    template_def = json.loads((TEMPLATES / "output_csv_usgaap_v1_template.json")
                              .read_text(encoding="utf-8"))
    rows = [{"canonical_key": "is_pl__net_sales", "source_label": "Net sales",
             "printed_in": "face", "mapping_method": "alias",
             "values": [{"basis": "consolidated", "period_label": "current", "value": "900",
                         "scale": "1"}]}]
    pages = [{"index": 0, "kind": "other"},
             {"index": 1, "kind": "face", "statement": "balance_sheet"},
             {"index": 2, "kind": "face", "statement": "profit_and_loss"}]

    def reasons(mapping: dict) -> set[str]:
        run = SimpleNamespace(id="r", line_item_version_id=None,
                              options={"sections_off": ["balance_sheet"]},
                              result={"unread_pages": [], "pages": pages, "mapping": mapping})
        out, from_rows = _structural_for_run(None, run, rows=rows, template_def=template_def)
        assert from_rows is True
        return {(r.get("details") or {}).get("reason") or r["status"] for r in out
                if (r.get("details") or {}).get("statement") == "balance_sheet"}

    read = reasons({"strategy": "whole_pdf",
                    "whole_pdf": {"statements_read": ["balance_sheet", "profit_and_loss"]}})
    assert read and "statement_not_read" not in read
    for mapping in ({"strategy": "deterministic"},
                    {"strategy": "whole_pdf", "whole_pdf": {"statements_read": ["profit_and_loss"]}},
                    {"strategy": "whole_pdf", "whole_pdf": {"pages_sent": [1, 2, 3]}}):
        assert reasons(mapping) == {"statement_not_read"}, mapping


class _TakesFiles:
    id = "scope-takes-files"
    takes_documents = True


class _TextOnly:
    id = "scope-text-only"


def test_page_scope_says_the_whole_pdf_goes_only_when_a_run_would_send_it(client, monkeypatch):
    """The Page Scope note (`whole_pdf` on the /pages payload) is on only where the next run sends
    the PDF to the model: the switch on AND a provider that can be sent a file. With the stub, or
    a provider that takes no file, every run falls back to the rules, where Page Scope applies in
    full, and the screen must not say otherwise."""
    from tests.fixtures.generate import make_native_pdf

    raw = make_native_pdf() + b"\n%test page-scope-whole-pdf-flag\n"
    res = client.post("/api/v1/documents", files={"file": ("flag.pdf", raw, "application/pdf")})
    assert res.status_code in (200, 201), res.text
    doc_id = res.json()["id"]
    s = get_settings()
    factories = registry._factories.setdefault("llm", {})
    for provider in (_TakesFiles(), _TextOnly()):
        monkeypatch.setitem(factories, provider.id, lambda provider=provider: provider)

    def flag(on: bool, provider_id: str) -> bool:
        monkeypatch.setattr(s.extraction, "whole_pdf_to_model", on)
        monkeypatch.setattr(s.llm, "provider", provider_id)
        body = client.get(f"/api/v1/documents/{doc_id}/pages").json()
        assert body["format"] == "pdf"
        return body["whole_pdf"]

    assert flag(True, _TakesFiles.id) is True
    assert flag(True, "stub") is False
    assert flag(True, _TextOnly.id) is False
    assert flag(False, _TakesFiles.id) is False
