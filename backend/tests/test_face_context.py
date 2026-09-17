"""THE PRINTED STATEMENT AS CONTEXT — what a face line is given to answer from.

NEW FILE -> backend/tests/test_face_context.py

A line read off the FACE selects no note, so its request's note block is empty and the reply
contract's own advice in that position is to answer with an empty `sources` — 343 of the 506
asked-about lines on the shipped set. `services.face_context.face_rows` is the block that gives
such a line something to answer from: the printed rows of the statement it is gated to.

WHAT THESE TESTS HOLD, and why each is here rather than being obvious:

  * KEYED BY STATEMENT, NOT BY BANNER. Measured on 000709's 255 face rows, the statement resolves
    for all of them and the banner for 163 — so banner-keying would drop a third of the statement —
    and where the banner does resolve it is sometimes wrong (12 changes-in-equity rows came back
    labelled `cash_flow_from_financing_activities`). The banner is a label on the row, never a
    filter.
  * THE MATCHED LINE TRAVELS. That is the deterministic route's proposal, and it is what makes the
    request answerable as "confirm or correct this" rather than "find this from nothing".
  * A PLACEHOLDER KEY IS NOT A LINE. `stages.face_mapping_contract` keys an unplaced row
    `engine_unclassified_face__…`, which names no line and would read as one.
  * THE BLOCK GOES ONCE PER REQUEST. Asserted through `build_request`, because the whole saving is
    that it sits beside the lines rather than inside each of them.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.models.geometry import Provenance
from app.services import face_context
from app.services.line_item_llm import build_request
from app.services.line_item_requests import RequestPlan


def _row(caption, page, *, value="100", key=None, banner=None, sub=None, period="current",
         column_index=None):
    li = LineItem(source_label=caption, canonical_key=key)
    if banner:
        li.section_hint = banner
    if sub:
        li.group_hint = sub
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                    value=Decimal(value), value_raw=Decimal(value),
                                    column_index=column_index,
                                    provenance=Provenance(page_index=page))
    return li


def _doc(rows, statements: dict[int, str]):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=i, kind=PageKind.FACE, statement=st)
                 for i, st in sorted(statements.items())]
    doc.line_items = list(rows)
    return doc


def test_only_the_named_statements_rows_are_supplied():
    doc = _doc([_row("Buildings", 10), _row("Revenue", 20)],
               {10: "balance_sheet", 20: "profit_and_loss"})
    blocks = face_context.face_rows(doc, {"balance_sheet"})
    assert [b["statement"] for b in blocks] == ["balance_sheet"]
    assert [r["caption"] for r in blocks[0]["rows"]] == ["Buildings"]


def test_nothing_is_supplied_for_no_statements():
    """An empty request is not "supply everything" — it is a request with no face context, which is
    what every caller that passes no plan sections gets."""
    doc = _doc([_row("Buildings", 10)], {10: "balance_sheet"})
    assert face_context.face_rows(doc, set()) == []
    assert face_context.face_rows(doc, None) == []


def test_the_deterministic_proposal_travels_with_the_row():
    doc = _doc([_row("Buildings", 10, key="bs_nca__buildings")], {10: "balance_sheet"})
    row = face_context.face_rows(doc, {"balance_sheet"})[0]["rows"][0]
    assert row["line"] == "bs_nca__buildings"


def test_a_placeholder_key_is_not_reported_as_a_line():
    """`engine_unclassified_face__balance_sheet__…` names no line of the output; reporting it as
    one would tell the model a row is already placed when it is precisely the row that is not."""
    doc = _doc([_row("Odd caption", 10, key="engine_unclassified_face__balance_sheet__odd")],
               {10: "balance_sheet"})
    row = face_context.face_rows(doc, {"balance_sheet"})[0]["rows"][0]
    assert "line" not in row, row


def test_the_banner_is_a_label_not_a_filter():
    """A row whose banner resolves to another statement's section is still supplied — measured, 12
    of 000709's changes-in-equity rows carry a cash-flow banner, and filtering on that signal would
    drop them from the context of the lines that need them."""
    doc = _doc([_row("Share capital", 10, banner="CASH FLOW FROM FINANCING ACTIVITIES")],
               {10: "changes_in_equity"})
    rows = face_context.face_rows(doc, {"changes_in_equity"})[0]["rows"]
    assert len(rows) == 1
    assert rows[0]["under"] == "CASH FLOW FROM FINANCING ACTIVITIES"


def test_a_row_with_no_figure_is_omitted():
    """A heading or a spacer. A caption with nothing beside it cannot be the answer to "where is
    this line's figure printed"."""
    bare = LineItem(source_label="NON-CURRENT ASSETS", canonical_key=None)
    doc = _doc([bare, _row("Buildings", 10)], {10: "balance_sheet"})
    rows = face_context.face_rows(doc, {"balance_sheet"})[0]["rows"]
    assert [r["caption"] for r in rows] == ["Buildings"]


def test_a_matrix_column_is_not_reported_as_a_period():
    """`column_index` values are the columns of an equity grid — equity components, not periods —
    so their labels would arrive as periods the filing never stated. Same rule `note_context`
    applies to a note's matrix rows."""
    doc = _doc([_row("Share capital", 10, period="col3", column_index=3)],
               {10: "changes_in_equity"})
    assert face_context.face_rows(doc, {"changes_in_equity"}) == []


def test_rows_are_supplied_in_page_order():
    doc = _doc([_row("Later", 12), _row("Earlier", 10)], {10: "balance_sheet", 12: "balance_sheet"})
    rows = face_context.face_rows(doc, {"balance_sheet"})[0]["rows"]
    assert [r["caption"] for r in rows] == ["Earlier", "Later"]


def test_the_block_goes_once_beside_the_lines_not_inside_each():
    """THE SHAPE, which is the whole saving. Two lines in one request share one block."""
    doc = _doc([_row("Buildings", 10, key="bs_nca__buildings")], {10: "balance_sheet"})
    face = face_context.face_rows(doc, {"balance_sheet"})

    class _Def:
        def __init__(self, key):
            self.key, self.label, self.definition = key, key, "d"
            self.statements, self.section_scope = [], []

    by_key = {"a": _Def("a"), "b": _Def("b")}
    plan = RequestPlan(name="g", keys=("a", "b"), notes=(),
                       sections=(("balance_sheet", "bs_nca"),))
    req = build_request(plan, by_key, {}, [], face)
    assert len(req["line_items"]) == 2
    assert len(req["statement_rows"]) == 1, "the block was multiplied by the request's size"


def test_the_block_is_absent_rather_than_empty_when_there_is_none():
    """A key whose value says nothing still costs the model a line to read, and invites it to infer
    the statement was looked for and not found — the reason `line_item_payload` omits an empty
    `exclude` too."""
    plan = RequestPlan(name="a", keys=("a",), notes=(), sections=())

    class _Def:
        key = label = "a"
        definition = "d"
        statements: list = []
        section_scope: list = []

    req = build_request(plan, {"a": _Def()}, {}, [], [])
    assert "statement_rows" not in req, req.keys()
