"""A notes page can never sit between two face pages.

THE RULE, as the product owner stated it: "there will never be a case that in between two
consecutive face pages there will be a note page." A filing prints its statements one after
another and then its notes; a note does not appear BETWEEN two pages of the statements.

WHAT IT FIXES, measured on a 367-page filing. Its cash-flow statement runs to three pages and
titles only the first and third; the middle one re-prints the column header band ("2025 2024 Notes
HK$'000 HK$'000") over the running header and nothing else. With no title to recognise, the
classifier put it in NOTES — between two pages it had correctly called FACE — so a whole page of
the cash flow was read as note detail rows. The figures were not missing, they were somewhere
else, which is harder to notice. Reclaiming that one page took the filing from 235 face rows to
275, and from 171 mapped to 209.

THE FIXTURE REPRODUCES IT, and what it took is worth recording: a clean synthetic three-page
statement classifies correctly unaided, so the fixture needed the artefact the real page has —
one row whose label opens with its note number, which is what a cash-flow page's narrow Notes
column produces when it merges into the caption. That trips the classifier's numbered-heading
signal, the middle page lands in NOTES, and the rule reclaims it. Without that row the fixture
proves nothing about the rule, which is why it is there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.models import PageKind
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import DocFormat
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template
from app.stages.classify import ClassifyStage

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@dataclass
class _Feat:
    """Only the field the rule reads."""

    notes_banner: bool = False
    note_heading: bool = False


def _pages(*kinds: str) -> list[PageSource]:
    out = []
    for i, k in enumerate(kinds):
        page = PageSource(index=i, kind=PageKind[k.upper()])
        if page.kind is PageKind.FACE:
            page.statement = "cash_flow"
            page.scope = "consolidated"
        out.append(page)
    return out


def _lines(*texts: str) -> tuple[list[dict], float]:
    """One page's decoded lines, in the shape ``_page_lines`` returns."""
    return ([{"text": t, "y": 10.0 * i, "size": 10.0, "bold": False}
             for i, t in enumerate(texts)], 800.0)


# What a statement's continuation page actually opens with: its folio, the running header, and the
# column band. Nothing that reads as a note heading.
_CONTINUATION_TOP = _lines("191", "Annual Report 2024 - 2025   EXAMPLE HOLDINGS",
                           "2025", "2024", "Notes", "HK$'000", "HK$'000",
                           "CASH FLOWS FROM OPERATING ACTIVITIES (continued)")


def _run(pages, feats, cache=None) -> tuple[int, list[str]]:
    ctx = PipelineContext(raw_bytes=b"")
    cache = cache or [_CONTINUATION_TOP] * len(pages)
    n = ClassifyStage._reclaim_statement_continuations(pages, feats, cache, ctx)
    return n, list(ctx.logs)


def test_a_notes_page_between_two_face_pages_becomes_a_face_page():
    pages = _pages("face", "notes", "face")
    n, logs = _run(pages, [_Feat(), _Feat(), _Feat()])

    assert n == 1
    assert pages[1].kind is PageKind.FACE
    assert any("reclaimed_as_face" in line for line in logs), logs


def test_it_inherits_the_statement_it_is_a_continuation_of():
    """Without the statement it would be a face page whose rows have nothing to be gated by, which
    is a different way of losing them."""
    pages = _pages("face", "notes", "face")
    _run(pages, [_Feat(), _Feat(), _Feat()])

    assert pages[1].statement == "cash_flow"
    assert pages[1].scope == "consolidated"
    assert (pages[1].evidence or {}).get("reclaimed_between_face_pages") is True


def test_a_page_carrying_the_notes_running_header_is_left_alone():
    """A page that really is a note announces itself. If one sits between two face pages then a
    NEIGHBOUR is misclassified, and flipping this page would compound that."""
    pages = _pages("face", "notes", "face")
    n, logs = _run(pages, [_Feat(), _Feat(notes_banner=True), _Feat()])

    assert n == 0
    assert pages[1].kind is PageKind.NOTES
    assert any("sandwiched_note_kept" in line for line in logs), logs


def test_the_noisy_numbered_heading_feature_does_not_veto():
    """``note_heading`` fires on any "1. …" run — on the measured filing it fired on the
    continuation page's own printed FOLIO, vetoing the very page this rule exists for. A lexical
    hint does not get to overrule the document's structure."""
    pages = _pages("face", "notes", "face")
    n, _logs = _run(pages, [_Feat(), _Feat(note_heading=True), _Feat()])

    assert n == 1
    assert pages[1].kind is PageKind.FACE


def test_a_page_that_opens_with_a_numbered_note_heading_is_left_alone():
    """THE EXCEPTION TO THE RULE, and it is the HK house style: a filing prints the Group's
    statements, the notes, then the COMPANY's own statement of financial position — so a note page
    genuinely can sit between two face pages. Testing the immediate neighbours keeps a notes section
    of two pages or more out of reach, but a single note page before the Company statement has
    exactly this shape, and reclaiming it would move a real note onto the face.

    A note OPENS with its numbered heading. A statement's continuation opens with the folio, the
    running header and the column band.
    """
    pages = _pages("face", "notes", "face")
    note_top = _lines("29. Cash and cash equivalents",
                      "Cash at banks earns interest at floating rates based on daily bank",
                      "deposit rates.", "2025", "2024")
    n, logs = _run(pages, [_Feat()] * 3,
                   cache=[_CONTINUATION_TOP, note_top, _CONTINUATION_TOP])

    assert n == 0
    assert pages[1].kind is PageKind.NOTES
    assert any("opens with a numbered note heading" in line for line in logs), logs


def test_a_note_reference_deep_in_a_continuation_page_does_not_veto():
    """A cash-flow continuation is full of note REFERENCES ("6(d)") and of rows opening with a
    figure. Asking whether the page CONTAINS a numbered heading answers a different question from
    whether it starts one — and on the measured filing the whole-page feature vetoed the very page
    this rule exists for."""
    pages = _pages("face", "notes", "face")
    deep = _lines("191", "Annual Report 2024 - 2025   EXAMPLE HOLDINGS",
                  "2025", "2024", "Notes", "HK$'000", "HK$'000",
                  "CASH FLOWS FROM OPERATING ACTIVITIES (continued)",
                  "29. Cash and cash equivalents")     # far below the top
    n, _logs = _run(pages, [_Feat()] * 3, cache=[_CONTINUATION_TOP, deep, _CONTINUATION_TOP])

    assert n == 1
    assert pages[1].kind is PageKind.FACE


def test_a_real_notes_section_is_untouched():
    """The conservative half, and it is what keeps the rule safe. A filing's notes section is also
    bounded by face pages — the last statement page before it and whatever face page follows —
    so the test is on the IMMEDIATE neighbours. A run of two or more notes pages is left alone,
    which means a two-page statement continuation is not reclaimed either; that is the accepted
    cost of not converting a 154-page notes section."""
    pages = _pages("face", "notes", "notes", "notes", "face")
    n, _logs = _run(pages, [_Feat()] * 5)

    assert n == 0
    assert [p.kind for p in pages[1:4]] == [PageKind.NOTES] * 3


def test_pages_must_be_neighbours_in_the_document_not_just_in_the_list():
    """A page filtered out upstream would otherwise make two non-adjacent pages look adjacent."""
    pages = _pages("face", "notes", "face")
    pages[2].index = 9                      # a gap: 0, 1, … 9
    n, _logs = _run(pages, [_Feat()] * 3)

    assert n == 0
    assert pages[1].kind is PageKind.NOTES


def test_the_edges_are_not_reclaimed():
    """A notes page with nothing before or after it is not between anything."""
    pages = _pages("notes", "face", "notes")
    n, _logs = _run(pages, [_Feat()] * 3)

    assert n == 0


def test_a_three_page_statement_is_one_statement_end_to_end():
    """The wiring, through the real stage: the middle page IS misclassified here, the rule runs as
    part of classification, and all three pages come out face under one statement with their rows
    on the face rather than in a note."""
    from tests.fixtures.generate import make_statement_spanning_three_pages_pdf

    ontology = load_ontology(
        json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text()), resolve=True)
    template = load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text()))
    ctx = PipelineContext(raw_bytes=make_statement_spanning_three_pages_pdf())
    ctx.ontology, ctx.template = ontology, template
    doc = default_pipeline().run(DocumentModel(filename="f.pdf", fmt=DocFormat.PDF), ctx)

    # The rule actually ran as part of classification — not merely that it would if called.
    assert any("reclaimed_as_face" in line for line in ctx.logs), \
        "the middle page was not reclaimed, so the rule is not wired into the stage"
    assert [p.kind for p in doc.pages] == [PageKind.FACE] * 3
    assert {p.statement for p in doc.pages} == {"cash_flow"}
    assert doc.notes == [], [t.title for t in doc.notes]
    # The untitled middle page's rows reached the FACE instead of becoming note detail.
    labels = [li.source_label for li in doc.line_items]
    for expected in ("Increase in trade receivables", "Interest paid"):
        assert expected in labels, labels
