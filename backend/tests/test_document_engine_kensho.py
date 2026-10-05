"""A hosted document engine — Kensho Extract — can be chosen by an admin and read a filing.

What an administrator can rely on:

* Kensho's answer is read in Kensho's published shape (its open-source converter's
  ``ExtractOutputModel``: a ``content_tree`` of blocks and table cells, each placed by
  ``{x, y, width, height, page_number}`` as fractions of the page, pages from 0), wrapped in
  ``output`` or not, and turned into positioned words — so a table Kensho read is put into rows
  and columns by the same reader as a native page;
* "off" changes nothing; "scanned" reads only pages with no text layer from it; "all" reads every
  page from it; a page it does not return, or a failed call, is read as before and logged;
* the Settings screen offers the switch and says whether addresses and a token are configured —
  never the token.
"""
from __future__ import annotations

import pytest

from app.adapters import kensho_extract
from app.adapters.kensho_extract import output_of, words_by_page
from app.config import get_settings
from app.services.settings_state import reset

pytest.importorskip("fitz")

from tests.fixtures.generate import make_native_pdf


@pytest.fixture(autouse=True)
def _restore_settings():
    yield
    reset()


def _loc(x, y, w, h, page=0):
    return {"x": x, "y": y, "width": w, "height": h, "page_number": page}


def _cell(uid, text, loc, offsets=None):
    node = {"uid": uid, "type": "TABLE_CELL", "content": text, "children": [], "locations": [loc]}
    if offsets is not None:
        node["text_node_data"] = {"texts": [text], "text_locations": [loc],
                                  "character_offsets": [offsets]}
    return node


def _answer(*nodes, wrapped=True):
    doc = {"annotations": [], "content_tree": {
        "uid": "0", "type": "DOCUMENT", "content": None, "locations": None,
        "children": list(nodes)}}
    return {"status": "success", "output": doc, "error": None, "metadata": {}} if wrapped else doc


# ── reading Kensho's answer ──────────────────────────────────────────────────────────────────────

def test_a_wrapped_and_a_bare_answer_are_both_read():
    bare = _answer(wrapped=False)
    assert output_of(bare) is bare and output_of({"status": "success", "output": bare}) is bare
    assert output_of({"status": "pending", "request_id": "r1"}) is None


def test_character_offsets_place_each_word_in_its_own_box():
    """Kensho gives each character's start across the box: "Revenue 1,204" split exactly."""
    text = "Revenue 1,204"
    offsets = [i / len(text) for i in range(len(text))]
    offsets[8:] = [0.80, 0.84, 0.88, 0.92, 0.96]          # the figure sits at the right
    words = words_by_page(_answer(_cell("1", text, _loc(0.1, 0.2, 0.5, 0.02), offsets)))[0]
    assert [w["text"] for w in words] == ["Revenue", "1,204"]
    assert words[1]["bbox"].x0 == pytest.approx(0.1 + 0.80 * 0.5)
    assert words[1]["bbox"].x1 == pytest.approx(0.6)


def test_a_table_is_read_from_its_cells_once_and_by_page():
    table = {"uid": "t", "type": "TABLE", "content": None, "children": [
        _cell("a", "Cash", _loc(0.10, 0.30, 0.20, 0.02, page=2)),
        _cell("b", "1,204", _loc(0.80, 0.30, 0.08, 0.02, page=2))],
        "locations": [_loc(0.1, 0.3, 0.8, 0.02, page=2)]}
    pages = words_by_page(_answer(table))
    assert list(pages) == [2] and [w["text"] for w in pages[2]] == ["Cash", "1,204"]


def test_an_answer_without_positions_is_a_configuration_error_naming_the_fix():
    """Kensho answers without locations unless asked for them; nothing could be placed."""
    from app.adapters.kensho_extract import pages_of

    bare = _answer({"uid": "1", "type": "TEXT", "content": "Revenue 1,204", "children": []})
    with pytest.raises(Exception, match="structured_document_with_locations"):
        pages_of(bare)
    assert pages_of(_answer()) == {}


# ── the pipeline reads a page from it ────────────────────────────────────────────────────────────

class _FakeKensho:
    id = "kensho"
    calls = 0

    def __init__(self, pages=None, fail=False):
        self._pages, self._fail = pages, fail

    def read_pdf(self, data):
        _FakeKensho.calls += 1
        if self._fail:
            raise RuntimeError("401 Unauthorized")
        return self._pages if self._pages is not None else words_by_page(_answer(
            _cell("1", "Cash and equivalents", _loc(0.10, 0.20, 0.30, 0.02)),
            _cell("2", "9,999", _loc(0.80, 0.20, 0.08, 0.02))))


def _run(choice, pages_mode, page_kind, engine):
    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import DocFormat, PageKind
    from app.core.stage import PipelineContext
    from app.ports.registry import Registry
    from app.services.pdf_extract import extract_pdf

    reg = Registry()
    reg.register("document_engine", "kensho", lambda: engine)
    ctx = PipelineContext(raw_bytes=b"")
    ctx.registry = reg
    ctx.settings = get_settings().model_copy(deep=True)
    ctx.settings.extraction.document_reader = choice
    ctx.settings.extraction.document_reader_pages = pages_mode
    ctx.settings.ocr.engine = "stub"
    doc = DocumentModel(filename="f.pdf", content_hash="h")
    doc.fmt = DocFormat.PDF
    doc.pages = [PageSource(index=0, source_kind=page_kind, kind=PageKind.FACE)]
    extract_pdf(make_native_pdf(), doc, ctx)
    return {li.source_label: int(next(iter(li.values.values())).value) for li in doc.line_items}, ctx


def test_off_reads_the_text_layer_and_never_calls_the_engine():
    from app.core.models.enums import PageSourceKind

    _FakeKensho.calls = 0
    rows, _ = _run("off", "all", PageSourceKind.NATIVE, _FakeKensho())
    assert rows["Trade receivables"] == 3410 and _FakeKensho.calls == 0


def test_scanned_mode_leaves_a_native_page_to_its_text_layer():
    from app.core.models.enums import PageSourceKind

    _FakeKensho.calls = 0
    rows, _ = _run("kensho", "scanned", PageSourceKind.NATIVE, _FakeKensho())
    assert rows["Trade receivables"] == 3410 and _FakeKensho.calls == 0


def test_scanned_mode_reads_a_page_with_no_text_layer_from_the_engine():
    from app.core.models.enums import PageSourceKind

    rows, _ = _run("kensho", "scanned", PageSourceKind.SCANNED, _FakeKensho())
    assert rows == {"Cash and equivalents": 9999}


def test_all_mode_reads_every_page_from_the_engine_and_says_where_it_came_from():
    from app.core.models.enums import PageSourceKind

    rows, ctx = _run("kensho", "all", PageSourceKind.NATIVE, _FakeKensho())
    assert rows == {"Cash and equivalents": 9999}
    assert any("document_engine=kensho pages_returned=1" in str(x) for x in ctx.logs)


def test_a_failed_call_reads_the_filing_as_before_and_logs_why():
    from app.core.models.enums import PageSourceKind

    rows, ctx = _run("kensho", "all", PageSourceKind.NATIVE, _FakeKensho(fail=True))
    assert rows["Trade receivables"] == 3410
    assert any("document_engine_failed(kensho: 401 Unauthorized)" in str(x) for x in ctx.logs)


def test_a_page_the_engine_did_not_return_is_read_from_its_text_layer():
    from app.core.models.enums import PageSourceKind

    rows, ctx = _run("kensho", "all", PageSourceKind.NATIVE, _FakeKensho(pages={5: [
        {"text": "x", "bbox": None, "confidence": 1.0}]}))
    assert rows["Trade receivables"] == 3410
    assert any("page=0:engine_no_text(kensho)" in str(x) for x in ctx.logs)


# ── configuration ────────────────────────────────────────────────────────────────────────────────

def test_missing_addresses_and_token_are_errors_naming_the_setting(monkeypatch):
    settings = get_settings().model_copy(deep=True)
    provider = kensho_extract.KenshoExtractProvider(settings)
    with pytest.raises(Exception, match="kensho_submit_url"):
        provider._addresses()
    monkeypatch.delenv(settings.document_engine.kensho_token_env, raising=False)
    with pytest.raises(Exception, match="KENSHO_ACCESS_TOKEN"):
        provider._token(client=None)


def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_the_switch_and_its_status_reach_the_settings_screen(client, monkeypatch):
    monkeypatch.setenv("KENSHO_ACCESS_TOKEN", "secret-token-value")
    body = client.get("/api/v1/settings", headers=_admin(client)).json()
    assert body["extraction"]["document_reader"] == "off"               # off out of the box
    fields = {f["key"]: f for f in body["extraction_fields"]}
    assert tuple(fields["document_reader"]["choices"]) == ("off", "kensho")
    status = body["document_engine"]["kensho"]
    assert status["token_present"] is True and not status["addresses_set"]
    assert "secret-token-value" not in str(body)

    res = client.patch("/api/v1/settings", headers=_admin(client),
                       json={"extraction": {"document_reader": "kensho",
                                            "document_reader_pages": "all"}})
    assert res.status_code == 200
    assert get_settings().extraction.document_reader == "kensho"
    assert get_settings().extraction.document_reader_pages == "all"
    bad = client.patch("/api/v1/settings", headers=_admin(client),
                       json={"extraction": {"document_reader": "nonesuch"}})
    assert bad.status_code == 422
