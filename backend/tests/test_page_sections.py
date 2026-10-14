"""Page Scope by section: what each page is read for, and how a page changes section.

The screen lists the four statements and the notes with their pages, labels every page with what
the next run reads it for, and changes a page with one click — Add to, Move to, Remove from — or
a section with typed pages and a Use switch. The server owns all of it (`services.page_reading`,
`services.page_sections`), because the label is only worth showing if it is the reader's own rule:
the first test runs the reader and holds the label to what it actually read.
"""
from __future__ import annotations

import time

import pytest

from tests.fixtures import generate as g

pytest.importorskip("fitz")


def _unique(data: bytes, tag: str) -> bytes:
    """The fixture's bytes with a PDF comment after the end: the same pages, another document.
    Uploads are de-duplicated by content, and the session's documents outlive a test."""
    return data + f"\n%test {tag}\n".encode()


def _upload(client, data, tag, name="scope.pdf", ctype="application/pdf"):
    if ctype == "application/pdf":
        data = _unique(data, tag)
    res = client.post("/api/v1/documents", files={"file": (name, data, ctype)})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _sections(client, doc_id, items, expect=200):
    res = client.put(f"/api/v1/documents/{doc_id}/sections", json={"sections": items})
    assert res.status_code == expect, res.text
    return res.json()


def _section(payload, sid):
    return next(s for s in payload["sections"] if s["id"] == sid)


def _card(payload, no):
    return next(c for c in payload["pages"] if c["no"] == no)


def _read_for(card):
    return [(r["kind"], r["statement"], r["entity"]) for r in card["reads"]]


def _extract(client, doc_id, **body):
    started = client.post(f"/api/v1/documents/{doc_id}/extractions", json=body)
    assert started.status_code == 202, started.text
    for _ in range(400):
        status = client.get(f"/api/v1/documents/{doc_id}/run-status").json()["status"]
        if status in ("succeeded", "failed", "canceled"):
            break
        time.sleep(0.05)
    return started.json()


def _run_options(run_id):
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as session:
        return session.get(ExtractionRun, run_id).options


# ── the label is the reader's rule ───────────────────────────────────────────────────────────────

def _face(statement, entity=None, y=0.0):
    return {"from_y": y, "kind": "face", "statement": statement, "entity": entity}


_OTHER = {"from_y": 0.0, "kind": "other", "statement": None, "entity": None}


def _notes(y=0.0):
    return {"from_y": y, "kind": "notes", "statement": None, "entity": None}


# Page 2 of the statement-tail fixture cut by a person, the balance sheet's last row above the
# income statement's title (0.1666) — so a switched-off section meets a person's parts, not the
# classifier's split.
def _tail_cut(top, bottom):
    return [{"page": 1, "parts": [top, {**bottom, "from_y": 0.1666}]}]


_READS = [
    ("hk-default", "make_hk_running_header_report_pdf", {}),
    ("hk-selection", "make_hk_running_header_report_pdf", {"included": [3, 4]}),
    # Page 0 is the cover, so a selection of it alone reads nothing, and is ignored.
    ("hk-selection-ignored", "make_hk_running_header_report_pdf", {"included": [0]}),
    ("hk-highlights-read", "make_hk_running_header_report_pdf",
     {"overrides": [{"page": 1, "parts": [_face("profit_and_loss")]}]}),
    ("hk-face-not-read", "make_hk_running_header_report_pdf",
     {"overrides": [{"page": 3, "parts": [_OTHER]}]}),
    ("hk-notes-off", "make_hk_running_header_report_pdf", {"off": ["notes"]}),
    ("hk-selection-notes-off", "make_hk_running_header_report_pdf",
     {"included": [3, 6], "off": ["notes"]}),
    ("hk-selection-ignored-notes-off", "make_hk_running_header_report_pdf",
     {"included": [0], "off": ["notes"]}),
    ("hk-balance-sheet-off", "make_hk_running_header_report_pdf", {"off": ["balance_sheet"]}),
    ("supplement-default", "make_cas_cash_flow_supplement_in_notes_pdf", {}),
    ("supplement-notes-off", "make_cas_cash_flow_supplement_in_notes_pdf", {"off": ["notes"]}),
    ("supplement-cash-flow-off", "make_cas_cash_flow_supplement_in_notes_pdf",
     {"off": ["cash_flow"]}),
    ("tail-default", "make_statement_tail_then_title_pdf", {}),
    ("tail-balance-sheet-off", "make_statement_tail_then_title_pdf", {"off": ["balance_sheet"]}),
    ("tail-profit-off", "make_statement_tail_then_title_pdf", {"off": ["profit_and_loss"]}),
    ("tail-cut-balance-sheet-off", "make_statement_tail_then_title_pdf",
     {"overrides": _tail_cut(_face("balance_sheet", "consolidated"),
                             _face("profit_and_loss", "consolidated")), "off": ["balance_sheet"]}),
    ("tail-cut-notes-above-off", "make_statement_tail_then_title_pdf",
     {"overrides": _tail_cut(_notes(), _face("profit_and_loss")), "off": ["notes"]}),
    ("tail-cut-face-below-off", "make_statement_tail_then_title_pdf",
     {"overrides": _tail_cut(_notes(), _face("profit_and_loss")), "off": ["profit_and_loss"]}),
    ("tail-cut-notes-below-off", "make_statement_tail_then_title_pdf",
     {"overrides": _tail_cut(_face("balance_sheet", "consolidated"), _notes()),
      "off": ["notes"]}),
    ("two-cash-flows", "make_two_cas_cash_flow_statements_on_one_page_pdf", {}),
    ("company-after-notes", "make_company_statement_after_notes_pdf", {}),
    ("annual-report", "make_annual_report_pdf", {}),
]


@pytest.mark.parametrize("fixture, choice", [(f, c) for _, f, c in _READS],
                         ids=[name for name, _, _ in _READS])
def test_the_screen_says_a_page_is_read_for_exactly_what_the_reader_reads(
        monkeypatch, fixture, choice):
    """What the screen would label each page with — the classified pages, the person's choices, the
    shared rule — against the calls the reader makes when a run reads the same file."""
    import app.services.notes_extract as notes_extract
    import app.services.pdf_extract as pdf_extract
    from app.services.documents import analyze_document, run_extraction
    from app.services.page_overrides import apply_to_page_dicts
    from app.services.page_reading import page_reads, reader_targets

    data = getattr(g, fixture)()
    included, overrides, off = choice.get("included"), choice.get("overrides"), choice.get("off")

    up, _ = analyze_document(data, filename=f"{fixture}.pdf")
    pages = [p.model_dump(mode="json") for p in up.pages]
    if overrides:
        pages = apply_to_page_dicts(pages, overrides)
    targeted = {p["index"] for p in reader_targets(pages, included, off)}
    predicted: dict[int, set] = {}
    for p in pages:
        for part in page_reads(p, p["index"] in targeted, sections_off=off):
            predicted.setdefault(p["index"], set()).add((part["kind"], part["statement"]))

    calls: list[tuple] = []
    face_reader, notes_reader = pdf_extract.build_line_items, notes_extract.extract_note_tables

    def _face_reader(words, *args, **kwargs):
        calls.append((kwargs.get("page_index"), "face", kwargs.get("statement")))
        return face_reader(words, *args, **kwargs)

    def _notes_reader(region, *args, **kwargs):
        calls.append((kwargs.get("page_index"), "notes", None))
        return notes_reader(region, *args, **kwargs)

    monkeypatch.setattr(pdf_extract, "build_line_items", _face_reader)
    monkeypatch.setattr(notes_extract, "extract_note_tables", _notes_reader)
    run_extraction(data, filename=f"{fixture}.pdf", included_pages=included,
                   page_overrides=overrides, sections_off=off)
    actual: dict[int, set] = {}
    for index, kind, statement in calls:
        actual.setdefault(index, set()).add((kind, statement))

    assert predicted == actual


# ── the sections, derived ────────────────────────────────────────────────────────────────────────

def test_sections_are_derived_from_how_each_page_is_read(client):
    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), "derived")
    pages = client.get(f"/api/v1/documents/{doc_id}/pages").json()

    assert pages["sections"] == [
        {"id": "balance_sheet", "pages": [5], "read": [5], "on": True},
        {"id": "profit_and_loss", "pages": [4], "read": [4], "on": True},
        {"id": "cash_flow", "pages": [6], "read": [6], "on": True},
        {"id": "changes_in_equity", "pages": [], "read": [], "on": True},
        {"id": "notes", "pages": [7, 8], "read": [7, 8], "on": True},
    ]
    assert (pages["read"], pages["format"], pages["last_run"], pages["locked"]) == (
        5, "pdf", None, False)
    assert _card(pages, 1)["reads"] == [] and _card(pages, 1)["unread"] == "not_classified"
    assert _card(pages, 4)["reads"] == [
        {"from_y": 0.0, "kind": "face", "statement": "profit_and_loss", "entity": "consolidated"}]
    assert _card(pages, 4)["unread"] is None


# ── one click ────────────────────────────────────────────────────────────────────────────────────

def test_one_click_adds_a_page_and_a_second_click_returns_it_to_automatic(client):
    from app.db.base import SessionLocal
    from app.db.models import Document

    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), "one-click")
    added = _sections(client, doc_id, [{"id": "profit_and_loss", "add": [2]}])
    assert _read_for(_card(added, 2)) == [("face", "profit_and_loss", None)]
    assert _card(added, 2)["overridden"] is True
    assert _section(added, "profit_and_loss")["pages"] == [2, 4]

    removed = _sections(client, doc_id, [{"id": "profit_and_loss", "remove": [2]}])
    assert _card(removed, 2)["unread"] == "not_classified"
    assert _card(removed, 2)["overridden"] is False
    with SessionLocal() as session:
        assert session.get(Document, doc_id).page_overrides is None


def test_moving_a_page_and_moving_it_back_keeps_the_classifiers_reading(client):
    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), "move")
    moved = _sections(client, doc_id, [{"id": "notes", "add": [6]}])
    assert _read_for(_card(moved, 6)) == [("notes", None, None)]
    assert _section(moved, "cash_flow")["pages"] == []

    back = _sections(client, doc_id, [{"id": "cash_flow", "add": [6]}])
    assert _read_for(_card(back, 6)) == [("face", "cash_flow", "consolidated")]
    assert _card(back, 6)["overridden"] is False


def test_a_move_back_on_a_carried_continuation_sticks(client):
    """Page 2 is the untitled continuation of page 1's balance sheet. Moving page 1 carries page 2
    with it; moving page 2 back must hold even though page 2's own reading is the classifier's —
    dropping its override would let the carry from page 1 undo the click."""
    doc_id = _upload(client, g.make_consolidated_statement_spanning_two_pages_pdf(), "carry")
    first = _sections(client, doc_id, [{"id": "cash_flow", "add": [1]}])
    assert _read_for(_card(first, 2)) == [("face", "cash_flow", "consolidated")]
    assert _card(first, 2)["overridden"] is False          # by the carry, not by an override

    second = _sections(client, doc_id, [{"id": "balance_sheet", "add": [2]}])
    assert _read_for(_card(second, 2)) == [("face", "balance_sheet", "consolidated")]
    assert _card(second, 2)["overridden"] is True

    third = _sections(client, doc_id, [{"id": "balance_sheet", "add": [1]}])
    assert _card(third, 1)["overridden"] is False
    assert _read_for(_card(third, 2)) == [("face", "balance_sheet", "consolidated")]


def test_an_added_page_takes_the_entity_of_the_page_it_continues():
    from app.services.page_sections import apply_edit

    raw = [{"index": 0, "kind": "face", "statement": "balance_sheet", "scope": "company",
            "evidence": {"matched_title": "Statement of financial position"}},
           {"index": 1, "kind": "other"}, {"index": 2, "kind": "other"}]

    def entities(overrides):
        return {o["page"]: o["parts"][0]["entity"] for o in overrides or []}

    one, _, _, _ = apply_edit(raw, None, None, None, [{"id": "balance_sheet", "add": [2]}],
                              page_count=3)
    assert entities(one) == {1: "company"}
    typed, _, _, kept = apply_edit(raw, None, None, None,
                                   [{"id": "balance_sheet", "pages": "1-3"}], page_count=3)
    assert entities(typed) == {1: "company", 2: "company"} and kept == []
    alone, _, _, _ = apply_edit(raw, None, None, None, [{"id": "balance_sheet", "add": [3]}],
                                page_count=3)
    assert entities(alone) == {2: None}


def test_a_page_cut_into_parts_is_changed_in_the_parts_editor_not_with_one_click(client):
    doc_id = _upload(client, g.make_statement_tail_then_title_pdf(), "parts")
    before = client.get(f"/api/v1/documents/{doc_id}/pages").json()
    refused = client.put(f"/api/v1/documents/{doc_id}/sections",
                         json={"sections": [{"id": "cash_flow", "add": [2]}]})
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert {k: detail[k] for k in ("error", "page", "parts", "section")} == {
        "error": "page_has_parts", "page": 2, "parts": 2, "section": "cash_flow"}
    assert detail["message"]
    assert client.get(f"/api/v1/documents/{doc_id}/pages").json() == before

    removed = _sections(client, doc_id, [{"id": "balance_sheet", "remove": [2]}])
    card = _card(removed, 2)
    assert _read_for(card) == [("face", "profit_and_loss", "consolidated")]
    assert card["parts"][0]["kind"] == "other"


# ── typed pages ──────────────────────────────────────────────────────────────────────────────────

def test_typed_pages_set_the_section_and_never_move_another_sections_page(client):
    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), "typed")
    typed = _sections(client, doc_id, [{"id": "notes", "pages": "4-9"}])
    assert _section(typed, "notes")["pages"] == [7, 8, 9]
    assert typed["kept"] == [
        {"no": 4, "reason": "other_section", "section": "profit_and_loss"},
        {"no": 5, "reason": "other_section", "section": "balance_sheet"},
        {"no": 6, "reason": "other_section", "section": "cash_flow"}]
    assert [_section(typed, s)["pages"] for s in ("profit_and_loss", "balance_sheet",
                                                    "cash_flow")] == [[4], [5], [6]]

    again = _sections(client, doc_id, [{"id": "notes", "pages": "7–8"}])
    assert _section(again, "notes")["pages"] == [7, 8]
    assert not any(c["overridden"] for c in again["pages"])


def test_typed_pages_take_a_page_left_out_of_the_page_selection_back(client):
    """A page selection made before the sections (PUT /scope) can leave a section's page out: it is
    still the section's, and not read. Typing the section's pages asks for them to be read, so the
    page goes back into the selection, as one click on Add to puts it."""
    from app.db.base import SessionLocal
    from app.db.models import Document

    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), "typed-selection")
    client.put(f"/api/v1/documents/{doc_id}/scope", json={"included_pages": [3, 4]})
    left_out = client.get(f"/api/v1/documents/{doc_id}/pages").json()
    assert _card(left_out, 6)["unread"] == "out_of_scope"
    assert _section(left_out, "cash_flow")["read"] == []

    typed = _sections(client, doc_id, [{"id": "cash_flow", "pages": "6"}])
    assert _section(typed, "cash_flow") == {"id": "cash_flow", "pages": [6], "read": [6],
                                            "on": True}
    assert _card(typed, 6)["unread"] is None and typed["kept"] == []
    with SessionLocal() as session:
        document = session.get(Document, doc_id)
        assert (document.page_scope, document.page_overrides) == ([3, 4, 5], None)


@pytest.mark.parametrize("edit", ["add-unread-page", "add-the-selected-page", "save-parts"])
def test_a_page_selection_the_reader_ignores_is_not_brought_back_by_an_edit(client, edit):
    """A selection of the cover alone holds no page a run reads, so the reader ignores it (a
    selection never extracts nothing) and every statement page is read. A page added to a section
    — or given parts in "How this page is read" — must not make that selection count: the next
    run would read the added page and nothing else."""
    from app.db.base import SessionLocal
    from app.db.models import Document

    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), f"ignored-{edit}")
    ignored = client.put(f"/api/v1/documents/{doc_id}/scope", json={"included_pages": [0]}).json()
    assert ignored["read"] == 5

    if edit == "add-unread-page":
        after = _sections(client, doc_id, [{"id": "notes", "add": [9]}])
        assert _section(after, "notes")["read"] == [7, 8, 9]
    elif edit == "add-the-selected-page":
        after = _sections(client, doc_id, [{"id": "profit_and_loss", "add": [1]}])
        assert _section(after, "profit_and_loss")["read"] == [1, 4]
    else:
        res = client.put(f"/api/v1/documents/{doc_id}/page-overrides",
                         json={"overrides": [{"page": 8, "parts": [_notes()]}]})
        assert res.status_code == 200, res.text
        after = res.json()
    assert after["read"] == 6
    assert not any(c["unread"] == "out_of_scope" for c in after["pages"])
    with SessionLocal() as session:
        assert session.get(Document, doc_id).page_scope is None


def test_a_page_selection_the_reader_ignores_leaves_no_page_out(client):
    """With the selection ignored, the notes pages are not read because the notes are switched
    off — not because a selection leaves them out, which would offer Add to put them back into
    a selection that then reads them alone."""
    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), "ignored-label")
    _sections(client, doc_id, [{"id": "notes", "on": False}])
    pages = client.put(f"/api/v1/documents/{doc_id}/scope", json={"included_pages": [0]}).json()
    assert pages["read"] == 3
    assert [_card(pages, no)["unread"] for no in (1, 4, 7, 8)] == [
        "not_classified", None, "section_off", "section_off"]


def test_a_page_selection_that_counts_still_follows_an_edit(client):
    """The other side: a selection holding a page that is read restricts the run, and an edit keeps
    it — a page added to a section joins it."""
    doc_id = _upload(client, g.make_hk_running_header_report_pdf(), "selection-counts")
    client.put(f"/api/v1/documents/{doc_id}/scope", json={"included_pages": [3]})
    after = _sections(client, doc_id, [{"id": "notes", "add": [9]}])
    assert after["read"] == 2
    assert [_card(after, no)["unread"] for no in (4, 5, 9)] == [None, "out_of_scope", None]


@pytest.mark.parametrize("text, error, params", [
    ("abc", "page_not_a_number", {"text": "abc"}),
    ("", "pages_empty", {}),
    ("40", "page_out_of_range", {"page": 40, "pages": 9}),
    # "p." names the folio printed on a page, never its place in the file.
    ("p.7", "page_not_a_number", {"text": "p.7"}),
])
def test_typed_pages_that_cannot_be_read_are_refused_by_name(text, error, params):
    from app.services.page_sections import SectionEditError, parse_pages

    with pytest.raises(SectionEditError) as err:
        parse_pages(text, 9)
    assert err.value.code == error and err.value.params == params and err.value.message


@pytest.mark.parametrize("text, pages", [
    ("8-7", [7, 8]), ("7، 8", [7, 8]), ("7，8", [7, 8]), ("6, 9–10", [6, 9, 10]),
    ("6 9 10", [6, 9, 10]), ("6;7", [6, 7]), ("3 ~ 5", [3, 4, 5]), ([9, 6], [6, 9]),
])
def test_typed_pages_are_read_in_the_ways_people_write_them(text, pages):
    from app.services.page_sections import parse_pages

    assert parse_pages(text, 10) == pages


# ── the Use switch ───────────────────────────────────────────────────────────────────────────────

def test_a_switched_off_section_keeps_its_pages_and_is_not_read(client):
    doc_id = _upload(client, g.make_multipage_pdf(), "switch")
    off = _sections(client, doc_id, [{"id": "notes", "on": False}])
    assert _section(off, "notes") == {"id": "notes", "pages": [2], "read": [], "on": False}
    assert _card(off, 2)["unread"] == "section_off"

    run = _extract(client, doc_id)
    assert _run_options(run["run_id"])["sections_off"] == ["notes"]
    result = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]
    assert result["note_details"] == []
    assert result["rows"], "the face page is still read"

    back = _sections(client, doc_id, [{"id": "notes", "add": [2]}])
    assert _section(back, "notes")["on"] is True


def test_a_switched_off_statement_is_skipped_on_a_page_it_shares():
    """Page 2 carries the balance sheet's last row, then the income statement's title. A switched
    off statement is skipped part by part: the page is still read for the one that is on."""
    from app.services.documents import run_extraction

    def flags(off):
        doc, _ = run_extraction(g.make_statement_tail_then_title_pdf(), filename="tail.pdf",
                                sections_off=off)
        return [{f for f in li.confidence.flags if f.startswith("printed_on:")}
                for li in doc.line_items]

    bs_off = flags(["balance_sheet"])
    assert bs_off and all(f == {"printed_on:profit_and_loss"} for f in bs_off)
    pl_off = flags(["profit_and_loss"])
    assert {"printed_on:balance_sheet"} in pl_off
    assert not any("printed_on:profit_and_loss" in f for f in pl_off)
    assert len(pl_off) == len(flags(None)) - len(bs_off)


def test_a_switched_off_statement_leaves_the_notes_beside_it_read_as_before():
    """Page 4 of the supplement fixture cut by a person: the cash flow on top, notes from 30% down.
    Switching the cash flow off skips its rows and nothing else. With it on, the notes below the
    statement start afresh; dropping the statement's part before the page was read made them
    continue the note page 3 left open, and a second "七、2 交易性金融资产" appeared."""
    from app.services.documents import run_extraction

    data = g.make_cas_cash_flow_supplement_in_notes_pdf()
    cut = [{"page": 3, "parts": [_face("cash_flow"), _notes(0.3)]}]

    def read(off):
        doc, _ = run_extraction(data, filename="supplement.pdf", page_overrides=cut,
                                sections_off=off)
        return doc.line_items, [(n.note_number, n.title) for n in doc.notes]

    rows_on, notes_on = read(None)
    rows_off, notes_off = read(["cash_flow"])
    assert rows_on and rows_off == []
    assert notes_off == notes_on


# ── extracting again ─────────────────────────────────────────────────────────────────────────────

def test_a_page_change_makes_the_next_extraction_a_new_run(client):
    """A page selection used to be adopted silently: changing it and opening the Extraction screen
    handed back the run that read the old pages. Like a saved page correction, it now asks for a new
    run, which records it."""
    doc_id = _upload(client, g.make_multipage_pdf(), "new-run")
    first = _extract(client, doc_id)
    same = _extract(client, doc_id)
    assert (same["run_id"], same.get("adopted")) == (first["run_id"], True)

    client.put(f"/api/v1/documents/{doc_id}/scope", json={"included_pages": [0]})
    second = _extract(client, doc_id)
    assert second["run_id"] != first["run_id"] and not second.get("adopted")
    assert _run_options(second["run_id"])["page_scope"] == [0]
    assert _extract(client, doc_id).get("adopted") is True

    _sections(client, doc_id, [{"id": "notes", "on": False}])
    third = _extract(client, doc_id)
    assert third["run_id"] not in (first["run_id"], second["run_id"])
    assert _run_options(third["run_id"])["sections_off"] == ["notes"]


def test_the_page_scope_screen_says_when_the_last_run_read_other_pages(client):
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun
    from app.services.page_sections import reads_current_pages

    doc_id = _upload(client, g.make_multipage_pdf(), "cue")
    _extract(client, doc_id)
    last = client.get(f"/api/v1/documents/{doc_id}/pages").json()["last_run"]
    assert {k: last[k] for k in ("status", "run_number", "reads_current_pages")} == {
        "status": "succeeded", "run_number": 1, "reads_current_pages": True}
    assert last["run_id"] and last["created_at"]

    changed = _sections(client, doc_id, [{"id": "notes", "on": False}])
    assert changed["last_run"]["reads_current_pages"] is False

    _extract(client, doc_id, force=True)
    again = client.get(f"/api/v1/documents/{doc_id}/pages").json()["last_run"]
    assert again["reads_current_pages"] is True and again["run_number"] == 2

    # A run recorded before the selection and the switches were, on a document that has neither.
    class _Doc:
        page_overrides = page_scope = sections_off = None

    with SessionLocal() as session:
        legacy = session.get(ExtractionRun, again["run_id"]).options
    legacy = {k: v for k, v in legacy.items() if k not in ("page_scope", "sections_off")}
    assert reads_current_pages(legacy, _Doc()) is True


def test_page_changes_wait_for_a_run_in_flight(client):
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    doc_id = _upload(client, g.make_multipage_pdf(), "lock")
    with SessionLocal() as session:
        run = ExtractionRun(id=f"lock-{doc_id[:8]}", document_id=doc_id, status="running",
                            options={}, progress={}, result=None)
        session.add(run)
        session.commit()
        run_id = run.id
    try:
        refused = client.put(f"/api/v1/documents/{doc_id}/sections",
                             json={"sections": [{"id": "notes", "on": False}]})
        assert refused.status_code == 409
        assert refused.json()["detail"]["error"] == "run_in_flight"
        assert refused.json()["detail"]["run_id"] == run_id
        assert client.put(f"/api/v1/documents/{doc_id}/page-overrides",
                          json={"overrides": []}).status_code == 409
        assert client.put(f"/api/v1/documents/{doc_id}/scope",
                          json={"included_pages": [0]}).status_code == 409
        assert client.get(f"/api/v1/documents/{doc_id}/pages").json()["locked"] is True
    finally:
        with SessionLocal() as session:
            session.delete(session.get(ExtractionRun, run_id))
            session.commit()


# ── where it applies, and who may change it ──────────────────────────────────────────────────────

def test_page_scope_is_for_pdfs(client):
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    doc_id = _upload(client, g.make_xlsx(), "xlsx", name="scope.xlsx", ctype=xlsx)
    refused = client.put(f"/api/v1/documents/{doc_id}/sections",
                         json={"sections": [{"id": "notes", "on": False}]})
    assert refused.status_code == 409
    assert {k: refused.json()["detail"][k] for k in ("error", "format")} == {
        "error": "not_paged", "format": "xlsx"}

    pages = client.get(f"/api/v1/documents/{doc_id}/pages").json()
    assert pages["format"] == "xlsx" and pages["sections"] == [] and pages["read"] is None
    assert all(c["reads"] is None for c in pages["pages"])


def test_only_a_role_that_runs_extraction_changes_pages(client, anon_client, auth):
    doc_id = _upload(client, g.make_multipage_pdf(), "role")
    refused = anon_client.put(f"/api/v1/documents/{doc_id}/sections",
                              json={"sections": [{"id": "notes", "on": False}]},
                              headers=auth("reviewer"))
    assert refused.status_code == 403


def test_an_unknown_section_is_refused(client):
    doc_id = _upload(client, g.make_multipage_pdf(), "unknown")
    assert client.put(f"/api/v1/documents/{doc_id}/sections",
                      json={"sections": [{"id": "income", "on": False}]}).status_code == 422


def test_the_scope_route_answers_with_the_whole_page_payload(client):
    doc_id = _upload(client, g.make_multipage_pdf(), "scope-route")
    res = client.put(f"/api/v1/documents/{doc_id}/scope", json={"included_pages": [0]}).json()
    assert (res["included_pages"], res["count"]) == ([0], 1)
    assert {"pages", "sections", "last_run", "read", "locked"} <= set(res)
    assert _card(res, 2)["unread"] == "out_of_scope"
