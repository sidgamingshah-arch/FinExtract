"""WHAT THE WHOLE-PDF ROUTE SENDS: the auditor's report beside the statements and notes, and the
statement faces in every part (`services.whole_pdf.pages_to_send`, `report_pages`, `build_parts`,
`plan`).

* The auditor's report was never sent, so the Accountant, the auditor's opinion and the audit-
  opinion text could only be refused as "not on the page" (finding auditor_pages_not_sent). No
  filing in the corpus is classified `AUDITOR_REPORT` — 688008's report pages are faces with no
  statement, the HKEX filings' are "other" — so the report is found by its heading before the
  statements.
* Four of the five reference filings go in two parts, and the second held notes only, so a note
  read there was read with no statement in view (finding parts_and_merge_prefer_face_for_note_
  parts). Every part now carries the faces; the parts are cut smaller to make room, and no page is
  dropped.
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
#: Parts before (54+53, 63+63, 91, 83+83, 51+50) and after: the parts holding the notes, the faces
#: each of them carries, and the part that holds the statement faces alone (every page classified a
#: face, a continuation it did not name included: 000709's p93, 688008's report pages 145-149).
PARTS = {"688008_montage_2024_prc": ([64, 63], 17, 20),
         "000709_hesteel_2024_prc": ([73, 73], 17, 18),
         "300319_maijie_2024_prc": ([94], 0, 20), "hkex_1966_ar": ([94, 93], 11, 11),
         "hkex_kaming_2025_26": ([58, 58], 7, 7)}
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
def test_the_auditors_report_is_sent_beside_the_statements(classified, stem):
    doc, raw = classified(stem)
    before, _ = W.pages_to_send(doc, choice="statements")            # no text: as it was
    pages, why = W.pages_to_send(doc, choice="statements", raw=raw)
    report = [p - 1 for p in AUDITOR[stem]]
    assert not set(report) & set(before)
    assert set(pages) == set(before) | set(report)
    assert f"+auditor's report {','.join(str(p) for p in AUDITOR[stem])}" in why
    # A person's page selection is theirs: nothing is added to it.
    chosen, why = W.pages_to_send(doc, choice="statements", raw=raw,
                                  included_pages=set(before[:5]))
    assert chosen == before[:5] and why == "page_scope"


@pytest.mark.parametrize("stem", sorted(PARTS))
def test_every_part_carries_the_faces_and_no_page_is_dropped(classified, hk, stem):
    doc, raw = classified(stem)
    plan = W.plan(doc, raw, hk, choice="statements", max_pages=100,
                  max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
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
    plan = W.plan(doc, raw, hk, choice="statements", max_pages=100,
                  max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
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


#: The largest request and the whole run, estimated as above: before 01d4a34 (definitions cut at
#: 240 characters, nothing carried), at 01d4a34 (whole definitions, faces in every part, every line
#: asked in every part), and the ceiling now — the statements' face lines asked once against the
#: faces alone, a sentence several lines share sent once (`services.whole_pdf.Plan`, `question`).
SIZES = {
    "688008_montage_2024_prc": ((140_020, 1_719_268), (197_882, 2_183_151), (160_000, 1_700_000)),
    "000709_hesteel_2024_prc": ((171_085, 2_149_033), (227_868, 2_626_293), (190_000, 2_000_000)),
    "300319_maijie_2024_prc": ((227_916, 1_512_553), (270_661, 1_644_377), (235_000, 1_400_000)),
    "hkex_1966_ar": ((220_491, 2_831_722), (282_702, 3_335_155), (245_000, 2_500_000)),
    "hkex_kaming_2025_26": ((141_933, 1_760_015), (195_441, 2_160_590), (160_000, 1_600_000))}


@pytest.mark.parametrize("stem", sorted(SIZES))
def test_the_requests_are_back_near_their_size_before_the_whole_definitions(classified, hk, stem):
    doc, raw = classified(stem)
    plan = W.plan(doc, raw, hk, choice="statements", max_pages=100,
                  max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
    pdf = fitz.open(stream=raw, filetype="pdf")
    text = {i: estimated_tokens(pdf[i].get_text("text") or "") for i in plan.pages}
    sizes = []
    for pi, ci in plan.order():
        part = plan.parts[pi]
        question = W.question(plan.ask(pi, ci), part, pi + 1, len(plan.parts), hk,
                              len(plan.carried))
        sizes.append(estimated_tokens(W.SYSTEM) + estimated_tokens(question)
                     + sum(text[p] for p in part.pages) + IMAGE_TOKENS_PER_PAGE * len(part.pages))
    (_old_max, old_run), (head_max, head_run), (cap_max, cap_run) = SIZES[stem]
    assert max(sizes) <= cap_max < head_max
    assert sum(sizes) <= cap_run < min(old_run, head_run)


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


def test_a_csrc_report_is_found_by_its_first_page_heading_and_a_sentence_is_not_a_heading():
    raw = _pdf([["Directors' report: the auditor's report is on the next pages."],
                ["第十节 财务报告", "一、审计报告", "我们认为，后附的财务报表…"],
                ["关键审计事项 …"], ["我们的目标是 …"], ["合并资产负债表"], ["七、合并财务报表项目注释"]])
    doc = _doc([O, O, O, O, BS, N])
    assert W.report_pages(doc, raw) == {"auditor's report": [1, 2, 3], "cover": []}


def test_each_set_of_statements_brings_its_own_report():
    """0857 prints a CAS set and an IFRS set, each after its own auditor's report."""
    raw = _pdf([["AUDITOR'S REPORT", "Opinion"], ["AUDITOR'S REPORT (continued)"],
                ["BALANCE SHEETS"], ["INCOME STATEMENTS"], ["Notes"],
                ["Independent auditor's report to the shareholders"],
                ["Independent auditor's report to the shareholders (continued)"],
                ["STATEMENT OF FINANCIAL POSITION"], ["Notes"]])
    doc = _doc([O, O, BS, PL, N, O, O, BS, N])
    assert W.report_pages(doc, raw)["auditor's report"] == [0, 1, 5, 6]


def test_a_classified_report_and_cover_are_sent_without_reading_the_text():
    doc = _doc([(PageKind.COVER, None), (PageKind.AUDITOR_REPORT, None), BS, N])
    pages, why = W.pages_to_send(doc, choice="statements")
    assert pages == [0, 1, 2, 3] and "+auditor's report 2" in why and "+cover 1" in why


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
