"""A PRINTED ROW GIVEN TO ONE LINE IS NOT ALSO ANOTHER'S — the fourth outcome of a citation.

NEW FILE -> backend/tests/test_claimed_printed_row.py

THE THREE OUTCOMES WERE INCOMPLETE. `tests/test_face_citation.py` pins confirm / correct / leave,
and LEAVE was stated too simply: an empty `sources` kept the printed figure and its deterministic
confidence, full stop. But an empty answer has two meanings and only one of them is "I could not
find this line".

    the model found nothing for this line        -> the printed figure is still this line's
    the model gave this line's printed row       -> the printed figure is that OTHER line's, and
    to a DIFFERENT line                             this line has none

Both arrive as an empty `sources`. Acting on only the first published the same printed number twice
under two names, and the duplicate was the copy carrying a deterministic 1.0 — so a reviewer
sorting by confidence saw the wrong one first. Nothing downstream can unpick it either:
`periods.summable` deduplicates a fact printed twice only when the caption, the amount AND the page
all match, and two lines standing on one row differ in caption.

WHY IT IS A PASS AFTER THE LAST REQUEST rather than a check at the moment of writing. The two
halves of the answer arrive in either order — the request that claims a row can be made long after
the request that left another line standing on it — so a decision taken inside the loop would be
right or wrong depending on the plan order. `_reconcile_claimed_printed_rows` runs once, when every
claim is known.

AND IT IS AN EMPTY LINE, NOT A DELETED ROW. `canonical_key` and the flags stay, so the trail says
the line was considered, what it had been showing, and which line took it. The figures go, which
makes `note_sourced._llm_holds` false — so a declared note source may still fill the line, which is
the second chance a line whose face row was reassigned should get.
"""
from __future__ import annotations

import json as _json
import pathlib as _pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import load_line_item_set
from app.stages.line_item_llm import LineItemLlmStage

FACE_PAGE = 5
BUILDINGS, LAND = "bs_nca__buildings", "bs_nca__land"
_SEED = (_pathlib.Path(__file__).resolve().parent.parent
         / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(autouse=True)
def _restore_extraction_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = (
            was[0], was[1], was[2], was[3])


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(_json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)


def _face_row(caption, *, value, key):
    li = LineItem(source_label=caption, canonical_key=key)
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal(value), value_raw=Decimal(value),
                                    provenance=Provenance(page_index=FACE_PAGE))
    li.confidence.method = "exact"
    li.confidence.mapping = 1.0
    return li


def _run(shipped, answers: dict[str, list[dict]]):
    """Two face lines, each its own request, each answered as `answers` says.

    `answers` maps a line key to that line's `sources`. A key absent from it is not answered at
    all, which is the "unanswered" case and not the "empty sources" case.
    """
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view

    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=FACE_PAGE, kind=PageKind.FACE, statement="balance_sheet")]
    doc.line_items = [_face_row("Buildings", value="9999", key=BUILDINGS),
                      _face_row("Land", value="4200", key=LAND)]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [BUILDINGS, LAND]

    class _P:
        id = "answers"

        def complete_structured(self, *, system, messages, response_schema, **_):
            asked = [e["key"] for e in _json.loads(messages[-1]["content"])["line_items"]]
            return response_schema.model_validate(
                {"answers": [{"key": k, "confidence": 0.9, "reason": "r",
                              "sources": answers[k]}
                             for k in asked if k in answers]}), {}

    ctx.registry.register("llm", "answers", lambda: _P())
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    by_key = {r.canonical_key: r for r in doc.line_items}
    return by_key, ctx


def test_the_printed_figure_is_withheld_when_the_model_gave_that_row_to_another_line(shipped):
    """Land is answered with the BUILDINGS row; Buildings answers empty. Buildings goes blank."""
    rows, ctx = _run(shipped, {LAND: [{"statement": "balance_sheet", "caption": "Buildings"}],
                               BUILDINGS: []})

    assert not [ev for ev in rows[BUILDINGS].values.values() if ev.value is not None], \
        "the printed figure stood although the model had given that row to another line"
    flags = rows[BUILDINGS].confidence.flags
    assert f"llm_printed_row_claimed_by:{LAND}" in flags, flags
    assert "llm_printed_row_withheld:9999" in flags, flags
    assert "low_mapping_confidence" in flags, flags
    assert any("printed figure withheld" in line for line in ctx.logs), \
        "the emptying happened silently"


def test_the_line_that_cited_the_row_publishes_its_figure(shipped):
    """The other half of the same run: the claim itself is honoured and is where 9999 went."""
    rows, _ctx = _run(shipped, {LAND: [{"statement": "balance_sheet", "caption": "Buildings"}],
                                BUILDINGS: []})
    assert next(iter(rows[LAND].values.values())).value == Decimal("9999")


def test_it_holds_whichever_request_comes_first(shipped):
    """THE SAME RUN WITH THE ROLES SWAPPED, which swaps the order the two facts arrive in. The
    reconciliation is a pass after the last request, so the outcome must not depend on it."""
    rows, _ctx = _run(shipped, {BUILDINGS: [{"statement": "balance_sheet", "caption": "Land"}],
                                LAND: []})
    assert not [ev for ev in rows[LAND].values.values() if ev.value is not None], \
        "the order of the two requests changed the outcome"
    assert f"llm_printed_row_claimed_by:{BUILDINGS}" in rows[LAND].confidence.flags
    assert next(iter(rows[BUILDINGS].values.values())).value == Decimal("4200")


def test_a_line_nobody_claimed_keeps_its_printed_figure(shipped):
    """THE CONTROL, and the behaviour `test_face_citation` pins. Both lines answer empty, nothing
    is claimed, and both printed figures stand with their deterministic confidence."""
    rows, _ctx = _run(shipped, {BUILDINGS: [], LAND: []})
    assert next(iter(rows[BUILDINGS].values.values())).value == Decimal("9999")
    assert rows[BUILDINGS].confidence.method == "exact"
    assert "llm_located_nothing_kept_exact:1.00" in rows[BUILDINGS].confidence.flags
    assert not [f for f in rows[BUILDINGS].confidence.flags if "claimed_by" in f]


def test_confirming_your_own_row_is_not_a_claim_against_yourself(shipped):
    """A line citing the row it was already proposed is the CONFIRM outcome. The claim ledger
    records it, and the owner is the line itself, so nothing is withheld."""
    rows, _ctx = _run(shipped, {BUILDINGS: [{"statement": "balance_sheet", "caption": "Buildings"}],
                                LAND: []})
    assert next(iter(rows[BUILDINGS].values.values())).value == Decimal("9999")
    assert not [f for f in rows[BUILDINGS].confidence.flags if "claimed_by" in f]
    assert next(iter(rows[LAND].values.values())).value == Decimal("4200")


def test_a_line_the_model_never_answered_is_not_touched(shipped):
    """UNANSWERED IS NOT AN EMPTY ANSWER. A line missing from the reply reaches
    `_write_unanswered` not at all, so it is never a candidate for withholding — even when its row
    was claimed. That is deliberate: the model did not say this line was not it."""
    rows, _ctx = _run(shipped, {LAND: [{"statement": "balance_sheet", "caption": "Buildings"}]})
    assert next(iter(rows[BUILDINGS].values.values())).value == Decimal("9999")
    assert not [f for f in rows[BUILDINGS].confidence.flags if "claimed_by" in f]


def test_a_row_with_no_printed_figure_is_never_a_candidate(shipped):
    """Nothing to withhold and nothing to flag. Pinned directly on the pass, because a row with no
    value can only reach it through a bug in the caller."""
    row = LineItem(source_label="Buildings", canonical_key=BUILDINGS)
    row.confidence.method = "exact"

    class _Ctx:
        logs: list = []

        def log(self, m):
            self.logs.append(m)

    ctx = _Ctx()
    n = LineItemLlmStage._reconcile_claimed_printed_rows(
        [(BUILDINGS, row)], {str(row.id): LAND}, ctx)
    assert n == 1, "a row recorded as kept is reconciled on identity, not on having a figure"
    assert f"llm_printed_row_claimed_by:{LAND}" in row.confidence.flags
    assert not [f for f in row.confidence.flags if "withheld:" in f], \
        "nothing was printed, so nothing may be reported as withheld"


def test_the_pass_does_nothing_when_no_row_was_claimed():
    row = LineItem(source_label="Buildings", canonical_key=BUILDINGS)

    class _Ctx:
        def log(self, m):
            raise AssertionError(f"nothing should be logged: {m}")

    assert LineItemLlmStage._reconcile_claimed_printed_rows([(BUILDINGS, row)], {}, _Ctx()) == 0
    assert LineItemLlmStage._reconcile_claimed_printed_rows([], {"x": LAND}, _Ctx()) == 0
    assert row.confidence.flags == []


def test_two_lines_given_the_same_row_both_publish_and_the_run_says_so(shipped):
    """THE MODEL CONTRADICTING ITSELF, which this pass does not resolve and does not hide.

    Both lines cited the BUILDINGS row, so neither answered empty and neither is a candidate for
    withholding — the rule is about an empty answer, not about deduplicating citations. The first
    claimant owns the row only because it was asked first, which is not a fact worth acting on, so
    what happens instead is that the run names it.
    """
    rows, ctx = _run(shipped, {BUILDINGS: [{"statement": "balance_sheet", "caption": "Buildings"}],
                               LAND: [{"statement": "balance_sheet", "caption": "Buildings"}]})
    assert next(iter(rows[BUILDINGS].values.values())).value == Decimal("9999")
    assert next(iter(rows[LAND].values.values())).value == Decimal("9999")
    assert any("already given to" in line for line in ctx.logs), \
        f"a printed row went to two lines in silence: {ctx.logs[-4:]}"
