"""WHAT THE WHOLE-PDF ROUTE SENDS: every page — the auditor's report among them — and the statement
faces in every part (`services.whole_pdf.pages_to_send`, `build_parts`, `plan`).

* The auditor's report was never sent, so the Accountant, the auditor's opinion and the audit-
  opinion text could only be refused as "not on the page" (finding auditor_pages_not_sent). It was
  then found by its heading before the statements and added to the classifier's pages. Now every
  page of the file goes, whatever the classifier found (`test_page_scope_does_not_narrow_the_
  whole_pdf_route.py`), so the report goes with the rest and nothing has to find it.
* The notes go in two or three parts, and a part that held notes only read a note with no
  statement in view (finding parts_and_merge_prefer_face_for_note_parts). Every part carries the
  faces; the parts are cut smaller to make room, and no page is dropped.
* The statements' own [read from the statement] lines are asked once, against the statement faces
  alone, a part of its own sent last (`Plan.face_keys`): the parts holding the notes carry the
  faces still, and are asked every other line.

Measured on the five reference filings (classified, no model), and on small synthetic files.
"""
from __future__ import annotations

import json
import os
import pathlib

import fitz
import pytest

from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import PageKind
from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W
from app.services.line_item_llm import estimated_tokens

BACKEND = pathlib.Path(__file__).resolve().parent.parent
FILINGS = BACKEND.parent / "testdata" / "filings"
TEMPLATES = BACKEND / "app" / "sample" / "templates"

#: The auditor's report of each reference filing, 1-based pages (the audit measured them).
AUDITOR = {"688008_montage_2024_prc": range(147, 150), "000709_hesteel_2024_prc": range(79, 82),
           "300319_maijie_2024_prc": range(95, 98), "hkex_1966_ar": range(92, 102),
           "hkex_kaming_2025_26": range(54, 62)}
#: At 100 pages a request, every page of the file sent: the parts holding the notes, the faces each
#: of them carries, and the part that holds the statement faces alone (every page classified a
#: face, a continuation it did not name included: 000709's p93, 688008's report pages 145-149).
#: (With the classifier's statement and note pages only, they were [64, 63], [73, 73], [94],
#: [94, 93] and [58, 58].)
PARTS = {"688008_montage_2024_prc": ([97, 97, 96], 17, 22),
         "000709_hesteel_2024_prc": ([81, 81, 80], 17, 22),
         "300319_maijie_2024_prc": ([76, 76, 74], 19, 20), "hkex_1966_ar": ([98, 98, 96], 11, 11),
         "hkex_kaming_2025_26": ([85, 84], 7, 7)}
#: What one page costs a request as a picture, as the audit estimated it (the text is counted by
#: the app's own estimator); the window the requests are measured against.
IMAGE_TOKENS_PER_PAGE = 1_600
WINDOW_TOKENS = 900_000


@pytest.fixture(scope="module")
def hk():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def classified(tmp_path_factory):
    """Each reference filing, classified (ingest to classify, no model), cached for the module."""
    from app.core.stage import PipelineContext
    from app.stages.classify import ClassifyStage
    from app.stages.ingest import IngestStage
    from app.stages.integrity import IntegrityStage
    from app.stages.language import LanguageDetectStage

    done: dict[str, tuple] = {}

    def _get(stem: str):
        path = FILINGS / f"{stem}.pdf"
        if not path.exists():
            pytest.skip(f"{stem}.pdf not present")
        if stem not in done:
            raw = path.read_bytes()
            doc, ctx = DocumentModel(filename=path.name), PipelineContext(raw_bytes=raw)
            for stage in (IngestStage(), IntegrityStage(), LanguageDetectStage(),
                          ClassifyStage()):
                doc = stage.run(doc, ctx)
            done[stem] = (doc, raw)
        return done[stem]
    return _get


@pytest.mark.parametrize("stem", sorted(AUDITOR))
def test_the_auditors_report_is_sent_as_every_page_is(classified, hk, stem):
    doc, raw = classified(stem)
    pages, why = W.pages_to_send(doc)
    assert pages == list(range(len(doc.pages))) and why == "every page"
    assert {p - 1 for p in AUDITOR[stem]} <= set(pages)
    plan = W.plan(doc, raw, hk, max_pages=100, max_request_bytes=20 * 1024 * 1024,
                  lines_per_request=250)
    assert plan.pages == pages and not plan.dropped
    assert {p - 1 for p in AUDITOR[stem]} <= {p for part in plan.parts for p in part.pages}


@pytest.mark.parametrize("stem", sorted(PARTS))
def test_every_part_carries_the_faces_and_no_page_is_dropped(classified, hk, stem):
    doc, raw = classified(stem)
    plan = W.plan(doc, raw, hk, max_pages=100, max_request_bytes=20 * 1024 * 1024,
                  lines_per_request=250)
    sizes, carried, alone = PARTS[stem]
    notes = [p for p in plan.parts if not p.faces_only]
    assert [len(p.pages) for p in notes] == sizes and len(plan.carried) == carried
    assert [len(p.pages) for p in plan.parts if p.faces_only] == [alone]
    assert plan.parts[-1].faces_only                       # sent last
    assert not plan.dropped and all(len(p.pages) <= 100 for p in plan.parts)
    assert sorted({p for part in notes for p in part.pages}) == plan.pages
    faces = W.face_pages(doc, plan.pages)
    assert all(set(faces) <= set(part.pages) for part in plan.parts)
    assert plan.parts[-1].pages == W.statement_pages(doc, plan.pages)
    assert plan.carried == (faces if len(notes) > 1 else [])


@pytest.mark.parametrize("stem", sorted(PARTS))
def test_every_request_stays_within_the_providers_limits(classified, hk, stem):
    """Each request's body (the part in base64, its question, the system text) within the
    configured 20 MB, and its estimated input within a one-million-token window's 900,000."""
    doc, raw = classified(stem)
    plan = W.plan(doc, raw, hk, max_pages=100, max_request_bytes=20 * 1024 * 1024,
                  lines_per_request=250)
    pdf = fitz.open(stream=raw, filetype="pdf")
    text = {i: estimated_tokens(pdf[i].get_text("text") or "") for i in plan.pages}
    system = len(W.SYSTEM.encode("utf-8"))
    for pi, ci in plan.order():
        part, chunk = plan.parts[pi], plan.ask(pi, ci)
        question = W.question(chunk, part, pi + 1, len(plan.parts), hk, len(plan.carried))
        body = part.b64_bytes + len(question.encode("utf-8")) + system
        assert body + 32 * 1024 <= 20 * 1024 * 1024
        assert len(question.encode("utf-8")) + system <= plan.overhead_bytes
        tokens = (estimated_tokens(W.SYSTEM) + estimated_tokens(question)
                  + sum(text[p] for p in part.pages) + IMAGE_TOKENS_PER_PAGE * len(part.pages))
        assert tokens <= WINDOW_TOKENS, (pi, chunk.statement, tokens)


#: The largest request and the whole run, estimated as above, at 100 pages a request: before
#: 01d4a34 (definitions cut at 240 characters, nothing carried) and at 01d4a34 (whole definitions,
#: faces in every part, every line asked in every part), both on the classifier's statement and note
#: pages only — and the ceiling now, EVERY PAGE SENT, the statements' face lines asked once against
#: the faces alone, a sentence several lines share sent once (`services.whole_pdf.Plan`,
#: `question`). Sending every page costs more than the classifier's pages did (the run 1.03 to 1.76
#: times 01d4a34's, which sent fewer pages); the ceiling is the measured figure with 2% of room, so a question that grows is
#: seen.
SIZES = {
    "688008_montage_2024_prc": ((140_020, 1_719_268), (197_882, 2_183_151), (239_600, 3_626_400)),
    "000709_hesteel_2024_prc": ((171_085, 2_149_033), (227_868, 2_626_293), (204_600, 3_185_200)),
    "300319_maijie_2024_prc": ((227_916, 1_512_553), (270_661, 1_644_377), (189_800, 2_951_300)),
    "hkex_1966_ar": ((220_491, 2_831_722), (282_702, 3_335_155), (254_500, 3_780_000)),
    "hkex_kaming_2025_26": ((141_933, 1_760_015), (195_441, 2_160_590), (230_200, 2_276_300))}


@pytest.mark.parametrize("stem", sorted(SIZES))
def test_the_requests_stay_within_their_measured_size(classified, hk, stem):
    doc, raw = classified(stem)
    plan = W.plan(doc, raw, hk, max_pages=100, max_request_bytes=20 * 1024 * 1024,
                  lines_per_request=250)
    pdf = fitz.open(stream=raw, filetype="pdf")
    text = {i: estimated_tokens(pdf[i].get_text("text") or "") for i in plan.pages}
    sizes = []
    for pi, ci in plan.order():
        part = plan.parts[pi]
        question = W.question(plan.ask(pi, ci), part, pi + 1, len(plan.parts), hk,
                              len(plan.carried))
        sizes.append(estimated_tokens(W.SYSTEM) + estimated_tokens(question)
                     + sum(text[p] for p in part.pages) + IMAGE_TOKENS_PER_PAGE * len(part.pages))
    _old, _head, (cap_max, cap_run) = SIZES[stem]
    assert max(sizes) <= cap_max and sum(sizes) <= cap_run


# ── synthetic files ────────────────────────────────────────────────────────────────────────────

def _pdf(pages: list[list[str]]) -> bytes:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        for i, text in enumerate(lines):
            # MuPDF's built-in CJK font, so the Chinese headings are in the text layer.
            page.insert_text((40, 72 + 16 * i), text, fontsize=9, fontname="china-s")
    return doc.tobytes()


def _doc(kinds) -> DocumentModel:
    doc = DocumentModel(filename="x.pdf")
    doc.pages = [PageSource(index=i, kind=k, statement=st)
                 for i, (k, st) in enumerate(kinds)]
    return doc


O, N = (PageKind.OTHER, None), (PageKind.NOTES, None)
BS, PL = (PageKind.FACE, "balance_sheet"), (PageKind.FACE, "profit_and_loss")


def test_a_report_and_a_cover_go_with_every_other_page_classified_or_not():
    """The auditor's report and the cover are sent because every page is — a classified one, and
    one the classifier called "other" (the HKEX filings' report pages), alike."""
    doc = _doc([(PageKind.COVER, None), (PageKind.AUDITOR_REPORT, None), O, BS, N])
    assert W.pages_to_send(doc) == ([0, 1, 2, 3, 4], "every page")


def test_the_parts_are_cut_smaller_to_carry_the_faces():
    raw = _pdf([[f"page {i + 1}"] for i in range(10)])
    parts, dropped = W.build_parts(raw, list(range(10)), max_pages=5,
                                   max_request_bytes=20 * 1024 * 1024, carry=[0, 1])
    assert [p.pages for p in parts] == [[0, 1, 2, 3, 4], [0, 1, 5, 6, 7], [0, 1, 8, 9]]
    assert not dropped
    second = fitz.open(stream=parts[1].pdf, filetype="pdf")
    assert [second.load_page(i).get_text().strip() for i in range(second.page_count)] == [
        "page 1", "page 2", "page 6", "page 7", "page 8"]       # in the report's order
    assert parts[1].original_page(3) == 5                        # its own page 3 is the 6th


def test_faces_that_fill_a_request_are_not_carried():
    raw = _pdf([[f"page {i + 1}"] for i in range(6)])
    parts, _ = W.build_parts(raw, list(range(6)), max_pages=3,
                             max_request_bytes=20 * 1024 * 1024, carry=[0, 1, 2])
    assert [p.pages for p in parts] == [[0, 1, 2], [3, 4, 5]]


def test_a_part_too_large_with_its_faces_is_halved_and_each_half_keeps_them():
    raw = _pdf([[f"page {i + 1}"] for i in range(6)])
    whole, _ = W.build_parts(raw, list(range(6)), max_pages=10, max_request_bytes=10 ** 9,
                             carry=[0])
    room = W.REQUEST_OVERHEAD_BYTES + whole[0].b64_bytes * 2 // 3
    parts, dropped = W.build_parts(raw, list(range(6)), max_pages=10, max_request_bytes=room,
                                   carry=[0])
    assert len(parts) > 1 and not dropped and all(0 in p.pages for p in parts)
    assert sorted({p for part in parts for p in part.pages}) == list(range(6))


def test_the_question_says_every_part_holds_the_statements():
    text = W.question(W.Chunk([], "notes"), W.Part(pages=[0, 1], pdf=b""), 2, 2, carried=1)
    assert "part 2 of 2" in text and "every part holds the statements themselves" in text
    assert "every part holds" not in W.question(W.Chunk([], "notes"),
                                                W.Part(pages=[0], pdf=b""), 2, 2)


def test_the_shipped_limits_are_a_million_token_window_and_600_pages():
    """The shipped model's limits: 600 pages a request (what Claude reads on a 1,000,000-token
    window), a 1,000,000-token window and 128,000-token replies — the code's defaults and
    config.toml alike."""
    import tomllib

    from app.config import ExtractionSettings, LlmSettings

    shipped = tomllib.loads((BACKEND / "config.toml").read_text(encoding="utf-8"))
    assert shipped["extraction"]["whole_pdf_pages_per_request"] == 600
    assert shipped["llm"]["context_window_tokens"] == 1_000_000
    assert shipped["llm"]["max_output_tokens"] == 128_000
    assert ExtractionSettings().whole_pdf_pages_per_request == 600
    assert (LlmSettings().context_window_tokens, LlmSettings().max_output_tokens) == (
        1_000_000, 128_000)
    assert os.path.exists(BACKEND / "config.toml")
