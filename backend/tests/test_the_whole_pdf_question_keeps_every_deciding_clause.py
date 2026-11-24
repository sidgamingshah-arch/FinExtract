"""THE WHOLE-PDF REQUESTS ARE SMALLER, AND NOTHING THAT DECIDES A LINE IS LOST FROM THEM
(`services.whole_pdf.question`, `for_this_route`, `shared_sentences`, `row_as_read`; `Plan.ask`,
`Plan.face_keys`, `Plan.second_part`).

01d4a34 sent every definition whole, with a "Not this line" sentence, and carried the statement
faces into every part. The HK balance sheet's question grew from 18,457 to 52,373 estimated tokens,
and 688008's largest request from about 140,020 to 197,882. Now:

* a sentence several lines of one request share is sent once, as a numbered rule the lines cite;
  the other route's reply words ("answer with an empty sources list") are said in this route's, and
  a sentence that says only what `SYSTEM` says of every line ("leave out a line the report does not
  print") is not sent; the "Not this line" sentence is written short;
* a statement's [read from the statement] lines are asked once, in a part that holds the statement
  faces alone — with the rest of that statement's lines, so each printed row still goes to exactly
  one line — and the parts holding the notes are asked every other line.

What decides a line must still reach it: its exclusions, its "Not this line" captions, its column
and basis selectors, current or non-current, the components it leaves out. Each is cut out of the
line's FULL definition here and looked for in the row the model reads (its row, every citation
written out). The questions are built from the shipped HK set; no model is called.
"""
from __future__ import annotations

import json
import pathlib
import re

import fitz
import pytest

from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import PageKind
from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W
from app.services.line_item_llm import estimated_tokens

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"

#: A clause that decides what a line is: what it is not, which column or basis, which side of a
#: year, which components it leaves out.
DECIDES = re.compile(
    r"Not this line|\bnot\b|\bNOT\b|[Ee]xclud|Do not|\bnever\b|other than|rather than|\bless\b|"
    r"\bbefore\b|\bafter\b|column|账面余额|账面价值|合计|Level 3|第三层次|\bcurrent\b|non-current|"
    r"NON-CURRENT|one year|\bONLY\b|\bonly\b|standalone|parent|consolidated")
QUOTED = re.compile(r"'([^']{2,80})'|“([^”]{1,80})”")


@pytest.fixture(scope="module")
def hk():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


def _reworded(text: str) -> str:
    return (text.replace("answer with an empty sources list", "leave it out")
            .replace("Answer with an empty sources list", "Leave it out")
            .replace("answer this line with an empty sources list", "leave this line out"))


def _clauses(item) -> list[tuple[str, str]]:
    whole = " ".join((item.definition or "").split())
    out = [("sentence", s) for s in W.sentences(_reworded(whole)) if DECIDES.search(s)]
    out += [("quoted", m.group(1) or m.group(2)) for m in QUOTED.finditer(whole)]
    out += [("exclude_hint", caption) for hint in item.exclude_hints or ()
            for _how, caption in W.hint_caption(hint)]
    return out


def _plan(hk) -> W.Plan:
    """A two-part filing with its faces carried, and the faces alone as part 3 — the shape four
    of the five reference filings take — without a PDF: only the questions are measured."""
    lines = W.lines_to_ask(hk)
    chunks = W.chunk_lines(lines, 250, hk)
    faces = list(range(40, 57))
    statements = set(W.FACE_STATEMENTS)
    keys = W.face_keys(chunks, statements, hk)
    chunks = chunks + W.statements_rest(chunks, statements, keys, 250)
    return W.Plan(pages=list(range(110)), why="statements",
                  parts=[W.Part(pages=sorted(set(range(47)) | set(faces)), pdf=b""),
                         W.Part(pages=sorted(set(range(57, 110)) | set(faces)), pdf=b""),
                         W.Part(pages=faces, pdf=b"", faces_only=True)],
                  dropped=[], carried=faces, lines=lines, chunks=chunks, overhead_bytes=0,
                  face_keys=keys,
                  face_statements=frozenset(statements))


def _questions(hk):
    plan = _plan(hk)
    for pi, ci in plan.order():
        lines = plan.ask(pi, ci)
        yield plan, pi, lines, W.question(lines, plan.parts[pi], pi + 1, len(plan.parts), hk,
                                          carried=len(plan.carried))


def test_every_deciding_clause_of_every_line_reaches_its_question(hk):
    checked, lost = 0, []
    for _plan_, _pi, lines, text in _questions(hk):
        for item in lines:
            row = W.row_as_read(text, item.key)
            assert row, item.key
            for kind, clause in _clauses(item):
                checked += 1
                found = (clause.casefold() in row.casefold() if kind == "exclude_hint"
                         else clause in row)
                if not found:
                    lost.append((item.key, kind, clause[:80]))
    assert checked > 4_000 and lost == []


def test_the_check_sees_a_clause_that_is_lost(hk):
    """Control: a definition cut at 240 characters, as before 01d4a34, loses clauses the check
    finds."""
    item = next(i for i in W.lines_to_ask(hk) if i.key == "bs_ncl__ltd_bank")
    cut = " ".join((item.definition or "").split())[:240]
    assert [c for kind, c in _clauses(item) if kind == "sentence" and c not in cut]


def test_a_sentence_several_lines_share_is_sent_once_and_cited(hk):
    plan = _plan(hk)
    alone = len(plan.parts) - 1
    sheet = next(ci for ci, c in enumerate(plan.chunks) if c.statement == "balance_sheet")
    text = W.question(plan.ask(alone, sheet), plan.parts[alone], alone + 1, len(plan.parts), hk,
                      carried=len(plan.carried))
    shared = ("Where the amount is only part of a larger row the balance sheet prints, it has no "
              "row of its own: leave it out and do not split it out of that row.")
    assert text.count(shared) == 1                         # 27 definitions hold it
    number = re.search(r"^R(\d+)\. " + re.escape(shared) + "$", text, re.MULTILINE).group(1)
    assert text.count(f"[R{number}]") >= 20
    assert "sources list" not in text
    assert "Where the balance sheet prints no such row" not in text   # says only what SYSTEM says
    assert estimated_tokens(text) < 47_000                 # 52,373 when every line said it


def test_the_statements_alone_are_asked_each_statements_whole_request_and_nothing_else(hk):
    plan = _plan(hk)
    alone = len(plan.parts) - 1
    rest = next(ci for ci, c in enumerate(plan.chunks) if c.statement == W.STATEMENTS_REST)
    for ci, chunk in enumerate(plan.chunks):
        on_faces = plan.ask(alone, ci)
        if chunk.statement in W.FACE_STATEMENTS:
            assert [i.key for i in on_faces] == [i.key for i in chunk]      # whole: totals too
        else:
            assert not on_faces
        for pi in (0, 1):
            notes = plan.ask(pi, ci)
            assert not {i.key for i in notes} & plan.face_keys
            if chunk.statement in W.FACE_STATEMENTS:
                assert not notes                       # its other lines are in `rest`
            else:
                assert notes == chunk
    # The three statements' lines read from the notes or anywhere, in one request of each notes
    # part: 22, 14 and 6 lines on the HK set, three requests before.
    statements = [c for c in plan.chunks if c.statement in W.FACE_STATEMENTS]
    assert [i.key for i in plan.chunks[rest]] == [i.key for c in statements for i in c
                                                  if i.key not in plan.face_keys]
    assert len(plan.chunks[rest]) == 42
    routes = {str(getattr(i.route, "value", i.route)) for i in plan.chunks[rest]}
    assert "face" not in routes and "anywhere" in routes     # Trade Receivables(Gross), totals
    # A cascade's parts are never split between the statements alone and the notes.
    family = W.families(hk, plan.lines)
    for root in set(family.values()):
        group = {k for k, r in family.items() if r == root}
        assert group <= plan.face_keys or not group & plan.face_keys, group
    asked = {i.key for pi, ci in plan.order() for i in plan.ask(pi, ci)}
    assert asked == {i.key for i in plan.lines}                # every line asked somewhere
    assert plan.second_part() == 0                            # a failed faces request goes there
    # No request is sent that asks nothing; each part's smallest goes first.
    assert all(plan.ask(pi, ci) for pi, ci in plan.order())
    assert plan.order()[0] == (0, plan.first_chunk())


def test_a_part_holding_the_notes_is_told_where_the_statements_are(hk):
    plan = _plan(hk)
    alone = len(plan.parts) - 1
    sheet = next(ci for ci, c in enumerate(plan.chunks) if c.statement == "balance_sheet")
    rest = next(ci for ci, c in enumerate(plan.chunks) if c.statement == W.STATEMENTS_REST)
    faces = W.question(plan.ask(alone, sheet), plan.parts[alone], 3, 3, hk, carried=17)
    notes = W.question(plan.ask(0, rest), plan.parts[0], 1, 3, hk, carried=17)
    assert faces.startswith("The PDF file holds the 17 pages of the report that print the "
                            "financial statements themselves, without their notes (part 3 of 3")
    assert notes.startswith("The PDF file holds 57 pages of the report (part 1 of 3")
    assert "the statements' other lines are asked with the statements alone" in notes
    # Each statement's lines still under its own name.
    assert [line for line in notes.splitlines() if line.startswith("Template lines")] == [
        "Template lines (balance sheet):", "Template lines (profit and loss):",
        "Template lines (cash flow):"]
    assert estimated_tokens(notes) < 12_000                  # 42 lines, not 208 + 107 + 81


# ── the plan, on a small synthetic filing ─────────────────────────────────────────────────────

def _pdf(n: int) -> bytes:
    doc = fitz.open()
    for i in range(n):
        doc.new_page().insert_text((40, 72), f"page {i + 1}", fontsize=9)
    return doc.tobytes()


def _doc(kinds) -> DocumentModel:
    doc = DocumentModel(filename="x.pdf")
    doc.pages = [PageSource(index=i, kind=k, statement=st) for i, (k, st) in enumerate(kinds)]
    return doc


O, N = (PageKind.OTHER, None), (PageKind.NOTES, None)
BS, PL, FACE = ((PageKind.FACE, "balance_sheet"), (PageKind.FACE, "profit_and_loss"),
                (PageKind.FACE, None))


def test_the_plan_sends_the_faces_alone_last_and_a_statement_with_no_face_where_the_notes_are(hk):
    """A face page the classifier named no statement of is not a reader target, and is not sent
    (page 4 here); the statements alone are every face page that is sent."""
    doc = _doc([O, BS, BS, FACE, PL, N, N, N])
    plan = W.plan(doc, _pdf(8), hk, choice="statements", max_pages=100,
                  max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
    assert [(p.pages, p.faces_only) for p in plan.parts] == [([1, 2, 4, 5, 6, 7], False),
                                                              ([1, 2, 4], True)]
    assert plan.face_statements == {"balance_sheet", "profit_and_loss"}
    flow = next(ci for ci, c in enumerate(plan.chunks) if c.statement == "cash_flow")
    assert plan.ask(0, flow) == plan.chunks[flow] and not plan.ask(1, flow)   # no face found
    assert plan.requests() == len(plan.order()) < 2 * len(plan.chunks)


def test_a_filing_of_faces_only_or_of_notes_only_is_sent_as_before(hk):
    for kinds in ([O, BS, PL], [O, N, N]):
        plan = W.plan(_doc(kinds), _pdf(3), hk, choice="statements", max_pages=100,
                      max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
        assert not any(p.faces_only for p in plan.parts) and plan.face_keys == frozenset()
        assert all(plan.ask(0, ci) == c for ci, c in enumerate(plan.chunks))


# ── a set whose every line is read off the statements ─────────────────────────────────────────

CF = (PageKind.FACE, "cash_flow")


@pytest.fixture(scope="module")
def indas():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_indas_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


def test_a_set_of_face_lines_only_keeps_the_runs_first_request_in_the_first_part(indas):
    """The Ind AS set's 111 lines are every one [read from the statement]. Asked against the
    statements alone, the parts holding the notes would be asked nothing: the run's first request
    would not be the first part's, so the stage's first-request gate (`stages.whole_pdf._ask`: the
    first part's first request fails -> the rules read the filing at once) would never fire, and a
    provider that is down would be asked every statement twice, then again where the notes are,
    before the run fell back. Nothing is left to ask the notes parts, so nothing is split off: the
    plan is the one before the statements were asked alone."""
    doc = _doc([O, BS, PL, CF, N, N, N])
    plan = W.plan(doc, _pdf(7), indas, choice="statements", max_pages=100,
                  max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
    assert plan.order(), "no request at all"
    assert plan.order()[0][0] == 0                        # the run's first request: part 1's
    assert all(plan.ask(0, ci) for ci in range(len(plan.chunks)))
    assert not any(p.faces_only for p in plan.parts) and plan.face_keys == frozenset()


def test_a_dead_provider_on_a_set_of_face_lines_only_is_left_after_the_first_request(indas):
    """The same plan through the stage's requests: two calls (the first request and its retry),
    then None — the rules read the filing — as on every other set."""
    from types import SimpleNamespace

    from app.stages.whole_pdf import WholePdfStage

    doc = _doc([O, BS, PL, CF, N, N, N])
    plan = W.plan(doc, _pdf(7), indas, choice="statements", max_pages=100,
                  max_request_bytes=20 * 1024 * 1024, lines_per_request=250)
    calls = []

    class Down:                       # a STAND-IN provider that never answers, not a model
        id = "down"

        def complete_structured(self, **kw):
            calls.append(kw)
            raise RuntimeError("HTTP 503: unavailable")

    logs: list[str] = []
    ctx = SimpleNamespace(
        settings=SimpleNamespace(extraction=SimpleNamespace(llm_parallel_requests=1)),
        line_items=indas, log=logs.append, emit_step=lambda *a, **k: None, llm_failures=0)
    documents = [W.as_document(p, i + 1) for i, p in enumerate(plan.parts)]
    assert WholePdfStage()._ask(ctx, Down(), plan, documents) is None
    assert len(calls) == 2
