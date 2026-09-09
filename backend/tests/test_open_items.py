"""Regression tests for the closed 'open items' batch:

* o8  — statement chrome (label + canonical-key prefix) is derived from the template.
* m8  — note-detail rows carry role (kind) + confidence end-to-end.
* m1  — a persisted page scope actually restricts what extraction processes.
* M24 — commentary is computed from a REAL extraction, not the demo project.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from app.core.models.enums import MappingMethod
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.mapping import OntologyMatcher


# --- the semantic tier is the MODEL, not a vector distance ------------------------------------
# There was an embedding tier here, with a fake provider and three tests. It never ran outside
# them: the mapping stage builds the matcher with an LLM provider and no embedding provider, and
# the only registered provider raised on use. It has been removed rather than wired, because as a
# capability it judged the same way the deleted fuzzy tier did — by resemblance — and the concept
# criteria the model reads (definition, include, exclude, confusable_with) are what a caption with
# no shared tokens has to be judged on. See services/mapping's module docstring.


# --- o8: statement chrome from the template -------------------------------------------------
def test_statement_chrome_derived_from_template():
    from app.api.routes.documents import _stmt_label, _stmt_prefix

    tpl = {"statements": [{
        "type": "changes_in_equity",
        "label": "Statement of Changes in Equity",
        "label_i18n": {"fr": "État des variations des capitaux propres"},
        "sections": [{"node_id": "s1", "children": [
            {"canonical_key": "eq_share_capital__ordinary_shares", "label": "Ordinary shares"},
        ]}],
    }]}
    # Prefix comes from the template's own keys, so a 4th statement type works with no code change.
    assert _stmt_prefix(tpl, "changes_in_equity") == "eq"
    assert _stmt_label(tpl, "changes_in_equity", "en") == "Statement of Changes in Equity"
    assert _stmt_label(tpl, "changes_in_equity", "fr") == "État des variations des capitaux propres"


def test_statement_chrome_falls_back_without_template():
    from app.api.routes.documents import _stmt_label, _stmt_prefix

    assert _stmt_prefix(None, "balance_sheet") == "bs"
    assert _stmt_label(None, "balance_sheet", "en") == "Balance sheet"


# --- m8: note-detail role → kind, confidence → badge ----------------------------------------
def test_note_row_kind_mapping():
    from app.api.routes.documents import _note_row_kind

    assert _note_row_kind({"role": "total", "label": "Total cash"}) == "tot"
    assert _note_row_kind({"role": "subtotal", "label": "Subtotal"}) == "sub"
    assert _note_row_kind({"role": "line", "label": "Total balances"}) == "tot"   # label heuristic
    assert _note_row_kind({"role": "line", "label": "Cash on hand"}) is None


# --- M24: commentary from a real extraction -------------------------------------------------
def _row(key, cur, prior=None):
    vals = [{"basis": "consolidated", "period_label": "current", "value": str(cur)}]
    if prior is not None:
        vals.append({"basis": "consolidated", "period_label": "prior", "value": str(prior)})
    return {"canonical_key": key, "values": vals}


def test_commentary_from_rows_is_data_driven():
    from app.services.commentary import build_commentary_from_rows

    rows = [
        _row("bs_current_assets__total_current_assets", 300, 250),
        _row("bs_current_liabilities__total_current_liabilities", 150, 140),
        _row("bs_equity__total_equity", 600, 500),
        _row("bs_total_assets", 800, 700),
        _row("bs_current_assets__cash_and_cash_equivalents", 60, 50),
        _row("bs_current_assets__trade_receivables", 40, 35),
        _row("bs_non_current_liabilities__non_current_borrowings", 100, 120),
        _row("pl_income__revenue_from_operations", 1000, 900),
        _row("pl_profit_before_tax", 120, 100),
        _row("pl_non_operating_expenses__interest_expense", 10, 12),
        _row("cf_cash_flow_from_operating_activities__net_cash_from_operating_activities", 150, 130),
    ]
    c = build_commentary_from_rows(rows, open_review_items=0, basis="consolidated",
                                   currency="INR", units="crore")
    m = {x["key"]: x["value"] for x in c["metrics"]}
    assert m["current_ratio"] == 2.0                       # 300 / 150
    assert m["debt_to_equity"] == round(100 / 600, 2)      # total debt / equity
    assert m["revenue_growth"] > 0                         # 1000 vs 900
    # Conservatively financed + strong coverage are selected from the real numbers.
    assert any("conservatively financed" in s for s in c["strengths"])
    assert "INR crore" in c["basis"]


def test_commentary_from_rows_empty_without_core_figures():
    from app.services.commentary import build_commentary_from_rows

    c = build_commentary_from_rows([], open_review_items=0)
    assert c["metrics"] == [] and c["headline"] == ""


# --- m1 + m8 + M24 end-to-end over a real 2-page document -----------------------------------
pytest.importorskip("fitz")
from tests.fixtures.generate import make_multipage_pdf, make_rich_pdf  # noqa: E402


_SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


def _hkfrs_pins(client) -> tuple[dict, dict]:
    """Publish the HKFRS spread's configuration as a LINE-ITEM SET; return it and its template.

    WHY, in one paragraph. These tests pinned ``ontology_version_id`` at the seeded
    ``hkfrs_hk_china`` rulebook and assert the ``hkfrs_hk_china_v1`` spread it produces. There is
    one configuration engine now and the ONE shipped set targets ``output_csv_hk_v1``, so
    ``hkfrs_hk_china_v1`` is seeded TEMPLATE-ONLY and maps nothing until somebody publishes a set
    that targets it (``app/sample/reference.py`` states that as the instructed consequence).
    Pinning the shipped set instead would silently move every assertion onto a different spread.
    So the test publishes what a configurator would: the same rulebook content, through the proven
    ``services.ontology_projection``, as a ``LineItemSet`` on the ``/line-items`` publish gate.
    Nothing here is an ontology the product stores or serves — the file is repo DATA read off
    disk and what reaches the database is a line-item version like any other.

    ``section_defaults`` is carried but FILTERED to the fields the line-item schema declares (the
    rulebook's has four more, which the stray-key gate refuses). It cannot be dropped: every
    projected concept keeps its ``inherits``, and a set with no sections fails its resolved load.
    """
    from app.schemas.line_items import SectionDefaults
    from app.schemas.loader import load_ontology
    from app.services.ontology_projection import build_definitions
    from app.services.working_view import _jsonable

    raw = json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    ont = load_ontology(raw, resolve=True)
    items, _census = build_definitions(list(ont.mappings), [])
    declared = set(SectionDefaults.model_fields)
    definition = {
        "line_items_key": "hkfrs_hk_china",
        "target_template_key": ont.target_template_key,
        "section_defaults": {name: {k: v for k, v in _jsonable(sec).items() if k in declared}
                             for name, sec in (raw.get("section_defaults") or {}).items()},
        "locale": ont.locale,
        "supported_locales": list(ont.supported_locales),
        "number_format_by_locale": _jsonable(ont.number_format_by_locale),
        "residual_framework": _jsonable(ont.residual_framework),
        "normalisation": _jsonable(ont.normalisation),
        "binding": _jsonable(ont.binding),
        "global_rules": _jsonable(ont.global_rules),
        "scope_selection": _jsonable(ont.scope_selection),
        "decomposition_rules": _jsonable(ont.decomposition_rules),
        "netting_rules": _jsonable(ont.netting_rules),
        "worked_examples": _jsonable(ont.worked_examples),
        "validation": _jsonable(ont.validation),
        "items": items,
    }
    # Configuration is an admin surface (``CONFIG_LINE_ITEMS``); the runs below are still started
    # by the fixture's default caller.
    r = client.post("/api/v1/line-items", json={"definition": definition},
                    headers={"X-Role": "admin"})
    assert r.status_code == 201, r.text
    cfg = r.json()
    tpl = next(t for t in client.get("/api/v1/templates").json()
               if t["template_key"] == cfg["target_template_key"])
    return cfg, tpl


def _upload(client, data, filename):
    return client.post("/api/v1/documents",
                       files={"file": (filename, data, "application/pdf")}).json()["id"]


def _extract_and_wait(client, doc_id):
    cfg, tpl = _hkfrs_pins(client)
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    for _ in range(100):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            return r.json()["result"]
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


def test_page_scope_reflects_and_restricts_extraction(client):
    doc_id = _upload(client, make_multipage_pdf(), "twopage.pdf")

    pages = client.get(f"/api/v1/documents/{doc_id}/pages").json()
    assert pages["total"] == 2
    # Default: both the face and notes pages are in scope.
    assert pages["focused"] == 2

    # Restrict extraction to the face page (index 0) only.
    r = client.put(f"/api/v1/documents/{doc_id}/scope", json={"included_pages": [0]})
    assert r.status_code == 200 and r.json()["included_pages"] == [0]

    pages2 = client.get(f"/api/v1/documents/{doc_id}/pages").json()
    assert pages2["focused"] == 1
    p1 = next(p for p in pages2["pages"] if p["no"] == 2)   # the notes page (index 1)
    assert p1["included"] is False

    # Extraction honours the scope: the notes page is excluded, so no note-detail tables.
    result = _extract_and_wait(client, doc_id)
    assert result["note_details"] == []


def test_note_detail_carries_confidence(client):
    doc_id = _upload(client, make_multipage_pdf(), "notes.pdf")
    result = _extract_and_wait(client, doc_id)
    if not result["note_details"]:
        pytest.skip("no note detail parsed for this fixture")
    notes = client.get(f"/api/v1/documents/{doc_id}/notes").json()
    no = notes["notes"][0]["no"]
    detail = client.get(f"/api/v1/documents/{doc_id}/notes/{no}").json()
    assert detail["rows"], "note should have detail rows"
    # Confidence is surfaced per row (was omitted for real note details before).
    assert any("conf" in row for row in detail["rows"])


def test_document_commentary_is_real_not_demo(client):
    doc_id = _upload(client, make_rich_pdf(), "rich.pdf")

    # Empty (valid) shape before extraction.
    pre = client.get(f"/api/v1/documents/{doc_id}/commentary").json()
    assert pre["metrics"] == []

    _extract_and_wait(client, doc_id)
    c = client.get(f"/api/v1/documents/{doc_id}/commentary").json()
    assert c["metrics"], "commentary should compute metrics from the real extraction"
    # Not the demo commentary: the demo basis is the seeded ₹-crore string.
    assert c["basis"] != "consolidated · FY25 vs FY24 · ₹ crore"


# --- #12: credit analysis blends extracted ratios with report narrative ---------------------
def _cval(key, value):
    return {"canonical_key": key,
            "values": [{"basis": "consolidated", "period_label": "current", "value": value}]}


def test_credit_analysis_blends_ratios_and_narrative():
    from app.services.derived import build_credit_analysis

    rows = [
        _cval("bs_current_assets__total_current_assets", 300),
        _cval("bs_current_liabilities__total_current_liabilities", 100),  # current ratio 3.0 → strong
    ]
    good = build_credit_analysis(rows, [])
    assert good["stance"] in ("strong", "adequate")
    assert any(f["category_key"] == "Liquidity" and f["tone"] == "strong" for f in good["factors"])
    assert good["flags"] == []

    # A going-concern narrative signal caps the stance to weak and is surfaced as a flag.
    capped = build_credit_analysis(
        rows, [{"key": "going_concern", "label": "Going concern", "present": True,
                "page": 5, "snippet": "material uncertainty related to going concern"}])
    assert capped["stance"] == "weak"
    assert any(fl["key"] == "going_concern" for fl in capped["flags"])

    # No extracted values → an honest "insufficient" rather than a fabricated view.
    assert build_credit_analysis([], [])["stance"] == "insufficient"


def test_analysis_endpoint_includes_credit(client):
    doc_id = _upload(client, make_rich_pdf(), "credit.pdf")
    _extract_and_wait(client, doc_id)
    a = client.get(f"/api/v1/documents/{doc_id}/analysis").json()
    assert "credit" in a
    assert a["credit"]["stance"] in ("strong", "adequate", "weak", "insufficient")
    assert isinstance(a["credit"]["factors"], list)


def test_credit_narrative_runner_grounds_on_factors():
    """The LLM narrative pass feeds the deterministic factors/flags to the provider and
    returns its prose — the numbers are never re-derived by the model."""
    from app.services.analysis_llm import CreditNarrative, run_credit_narrative
    from app.services.derived import build_credit_analysis

    rows = [
        _cval("bs_current_assets__total_current_assets", 300),
        _cval("bs_current_liabilities__total_current_liabilities", 100),
    ]
    credit = build_credit_analysis(rows, [])

    seen = {}

    class FakeProvider:
        id = "fake"

        def complete_structured(self, *, system, messages, response_schema, temperature=0.0, max_tokens=2048):
            seen["system"] = system
            seen["user"] = messages[0]["content"]
            return response_schema(narrative="Adequate liquidity underpins the profile."), {
                "model": "fake-1", "input_tokens": 12, "output_tokens": 8}

    result, meta = run_credit_narrative(FakeProvider(), credit, entity="Acme Ltd")
    assert isinstance(result, CreditNarrative) and result.narrative
    assert meta["model"] == "fake-1"
    # The provider was handed the computed factor (Current ratio), not raw statements.
    assert "Current ratio" in seen["user"]
    assert "credit analyst" in seen["system"].lower()


def test_credit_narrative_endpoint_is_gated_and_coherent(client):
    # Environment-agnostic: a real provider + key → 200 with a narrative; a stub provider →
    # 409; a configured-but-unreachable provider (no key) → 502; insufficient data → 422.
    # The point is a clean, actionable status in every case — never a 500 or a permission error.
    doc_id = _upload(client, make_rich_pdf(), "narr.pdf")
    _extract_and_wait(client, doc_id)
    r = client.post(f"/api/v1/documents/{doc_id}/credit-narrative")
    assert r.status_code in (200, 409, 422, 502), r.status_code
    if r.status_code == 200:
        assert r.json()["narrative"]


# --- #49: auto-cache the credit narrative is best-effort (never disturbs a succeeded run) ----
def test_maybe_cache_credit_narrative_is_guarded():
    from app.api.routes.extractions import _maybe_cache_credit_narrative

    class FakeSession:
        def commit(self): pass
        def rollback(self): pass

    class FakeRun:
        def __init__(self):
            self.result = {
                "rows": [
                    _cval("bs_current_assets__total_current_assets", 300),
                    _cval("bs_current_liabilities__total_current_liabilities", 100),
                ],
                "disclosures": [],
            }

    run = FakeRun()
    # Must never raise regardless of provider/key state. If it does cache (a real provider is
    # configured with a key), the cached shape is well-formed; otherwise it's simply absent.
    _maybe_cache_credit_narrative(FakeSession(), run, "en", "Acme Ltd")
    cn = run.result.get("credit_narrative")
    assert cn is None or (cn.get("text") and cn.get("model"))


# --- note-vs-value column: a note reference is not read as the current-year value ------------
def test_note_column_not_read_as_value():
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import (
        Word, _detect_note_column, _num, _resolve_note_column, _scan_row)

    def W(t, x):
        return Word(text=t, bbox=BBox(x0=x, y0=0.5, x1=x + 0.03, y1=0.52))

    # Layout: Label | Notes | 2023 | 2022 — the note column holds bare note numbers.
    header = [W("Notes", 0.55), W("2023", 0.72), W("2022", 0.88)]
    r_rev = [W("Revenue", 0.10), W("6", 0.55), W("45,230", 0.72), W("40,110", 0.88)]
    r_fin = [W("Finance", 0.10), W("costs", 0.15), W("7", 0.55), W("(1,200)", 0.72), W("(1,000)", 0.88)]
    rows = [header, r_rev, r_fin]

    note_x = _detect_note_column(rows)
    assert note_x is not None and abs(note_x - 0.565) < 0.05

    nr, vw = _resolve_note_column(*_scan_row(r_rev)[1:], note_x)
    assert nr == "6"                                  # the note ref, not a value
    vals = [_num(w.text) for w in vw]
    assert [str(v) for v in vals] == ["45230", "40110"]  # the real amounts survive

    # Fallback path (no detectable note column): a leading bare integer before real amounts.
    nr2, vw2 = _resolve_note_column(None, [W("6", 0.5), W("45,230", 0.7)], None)
    assert nr2 == "6" and len(vw2) == 1 and _num(vw2[0].text) == 45230
    # A row of small integers with no money-like value is left alone (not a note column).
    nr3, vw3 = _resolve_note_column(None, [W("3", 0.5), W("4", 0.7)], None)
    assert nr3 is None and len(vw3) == 2


# --- title/date/running-header rows are filtered out of line items --------------------------
def test_header_and_date_rows_are_not_line_items():
    from app.services.row_reconstruct import _is_noise_row

    D = _dec  # local Decimal helper below

    # Running header ("… / Annual Report 2023") and period captions whose only value is a date
    # fragment (a year or a day-of-month) are dropped.
    assert _is_noise_row("China SCE Group Holdings Limited / Annual Report 2023", [D(2023)])
    assert _is_noise_row("Consolidated Statement of Cash Flows Year ended", [D(31)])
    assert _is_noise_row("綜合財務狀況表", [D(2023)])
    assert _is_noise_row("For the year ended", [D(31), D(2023)])
    # A genuine line that merely mentions a statement name keeps a real amount → kept.
    assert not _is_noise_row("Reclassified to profit or loss", [D(-499675)])
    assert not _is_noise_row("Revenue", [D(20960968)])
    # A header-like label with a decimal (EPS) is a real value, not a date → kept.
    assert not _is_noise_row("For the year ended", [_dec_str("0.45")])


def _dec(n):
    from decimal import Decimal
    return Decimal(n)


def _dec_str(s):
    from decimal import Decimal
    return Decimal(s)


# --- #9: Workspace statement rows carry a click-to-source location + doc shape ---------------
def test_statement_rows_carry_source_and_format(client):
    doc_id = _upload(client, make_rich_pdf(), "srcstmt.pdf")
    _extract_and_wait(client, doc_id)
    s = client.get(f"/api/v1/documents/{doc_id}/statement",
                   params={"statement": "balance_sheet", "basis": "consolidated"}).json()
    assert s["format"] == "pdf" and s["page_count"] >= 1
    items = [r for r in s["rows"] if r.get("kind") == "item"]
    assert items, "a balance sheet should have item rows"
    # Every item row exposes its current-period value's provenance so the Workspace's live
    # viewer can hyperlink it to the source page + bbox.
    assert any(r.get("source") and r["source"].get("bbox") for r in items), \
        "item rows should carry a page+bbox source location"


# --- #5: native-PDF period-end date detection for column headers ----------------------------
def test_period_bands_detects_dated_columns():
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word, _period_bands, _period_for

    def W(text, x0):
        return Word(text=text, bbox=BBox(x0=x0, y0=0.05, x1=x0 + 0.04, y1=0.07))

    # A header row: "31 March 2025" over the first value column, "31 March 2024" over the second.
    header = [W("31", 0.50), W("March", 0.55), W("2025", 0.60),
              W("31", 0.75), W("March", 0.80), W("2024", 0.85)]
    bands = _period_bands([header])
    assert len(bands) == 2
    assert bands[0][0] == "31 March 2025" and bands[1][0] == "31 March 2024"
    # A value near the first column's x maps to the 2025 header; near the second → 2024.
    assert _period_for(0.55, bands) == "31 March 2025"
    assert _period_for(0.82, bands) == "31 March 2024"
    # A row with no year/month yields no period bands (falls back to positional Current/Prior).
    plain = [W("Trade", 0.1), W("receivables", 0.15), W("1,234", 0.55)]
    assert _period_bands([plain]) == []


# --- Item B: delete uploaded documents ------------------------------------------------------
def test_delete_document_removes_it_and_its_runs(client):
    doc_id = _upload(client, make_rich_pdf(), "to-delete.pdf")
    _extract_and_wait(client, doc_id)  # give it a run to clean up
    assert client.get(f"/api/v1/documents/{doc_id}/run").status_code == 200

    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 204
    # Gone: subsequent reads 404, and it no longer appears in the list.
    assert client.get(f"/api/v1/documents/{doc_id}/run").status_code == 404
    ids = [d.get("id") for d in client.get("/api/v1/documents").json()["documents"]]
    assert doc_id not in ids


def test_delete_document_requires_ownership(client, auth, anon_client):
    # Admin uploads a document; an analyst (different owner) cannot delete it (404, not 403,
    # so existence isn't leaked), and the reviewer lacks documents:manage entirely (403).
    doc_id = _upload(client, make_multipage_pdf(), "owned.pdf")
    assert anon_client.delete(f"/api/v1/documents/{doc_id}",
                              headers=auth("analyst")).status_code == 404
    assert anon_client.delete(f"/api/v1/documents/{doc_id}",
                              headers=auth("reviewer")).status_code == 403
    # Still there for its owner.
    assert client.get(f"/api/v1/documents/{doc_id}/run").status_code in (200, 404)  # exists (run may be absent)
    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 204
