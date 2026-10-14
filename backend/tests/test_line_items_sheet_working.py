"""The Line Items sheet carries the working beside each derived figure.

NEW FILE -> backend/tests/test_line_items_sheet_working.py

The sheet already wrote a derived line's trail as its own rows beneath it, every one marked
"counted", and a SUM only where those rows reproduced the published figure — which, on Lai Sun's
LTP, they did not, because the same printed row was listed twice and counted twice. Each trail row
now says what it did ("added", "same row, added once", "deducted"), the figure's cell carries the
working as a comment in the reader's language, and a sub-line holding a figure the answering rung
did not read says so. No column moves: Source is still the last.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

openpyxl = pytest.importorskip("openpyxl")

from app.api.routes.extractions import _serialize_rows  # noqa: E402
from app.core.models import DocumentModel  # noqa: E402
from app.services.export_line_items import build_line_items_sheet  # noqa: E402
from app.services.line_item_config import load_shipped_set  # noqa: E402
from tests.test_a_notes_money_is_on_the_face_once import _cascade, _laisun, _part  # noqa: E402

LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
PERIODS = [("consolidated", "current"), ("consolidated", "prior")]


def _sheet(locale: str = "en"):
    rows = _laisun()
    rows.insert(4, _part("sub__ltp_other_fincl_assets_note_total",
                         [("Non-current portion", "788507", "941274", 0.45)]))
    ltp, got = _cascade(rows)
    assert got == {"current": "366944", "prior": "847794"}
    # The cascade wrote the line into the same list of rows it was handed.
    assert any(li is ltp for li in rows)
    doc = DocumentModel(filename="laisun.pdf")
    doc.line_items = rows
    served = _serialize_rows(doc)
    items = [i for i in load_shipped_set().items if i.key == LTP or i.parent == LTP]
    wb = openpyxl.Workbook()
    build_line_items_sheet(wb, SimpleNamespace(items=items), served, period_cols=PERIODS,
                           locale=locale)
    ws = wb["Line Items"]
    at = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 2).value == LTP)
    return ws, at


HOW, SOURCE = 7, 8


def test_the_working_rides_on_the_figure_and_each_trail_row_has_a_status():
    ws, at = _sheet()
    comment = ws.cell(at, 5).comment
    assert comment is not None, "the figure carries no working"
    assert comment.text.startswith("788,507 + 128,412 − 549,975 = 366,944 — adds up"), comment.text
    assert ws.cell(at, 6).comment.text.startswith("941,274 + 119,364 − 212,844 = 847,794")
    assert [ws.cell(at + k, HOW).value for k in (1, 2, 3, 4)] == [
        "added", "added", "same row, added once", "deducted"]
    assert [ws.cell(at + k, 3).value for k in (1, 2, 3, 4)] == ["note row"] * 4
    assert ws.max_column == SOURCE, "a column was added to the sheet"
    assert "already added as" in (ws.cell(at + 3, 1).comment.text or "")

    zh, zh_at = _sheet("zh")
    assert "相符" in zh.cell(zh_at, 5).comment.text
    assert zh.cell(zh_at + 3, HOW).value == "同一行，只计一次"


def test_a_sub_line_the_rung_did_not_use_says_so():
    """The face part of LTP prints figures the answering rung (LTP_P1, from the notes) did not
    read; it is one of FROM_THE_FACE's terms. Beside its own figure it says so."""
    ws, _at = _sheet()
    row = next(r for r in range(1, ws.max_row + 1)
               if ws.cell(r, 2).value == "sub__ltp_face_other_non_current_fincl_assets")
    assert str(ws.cell(row, HOW).value).endswith("not used by the winning rung"), (
        ws.cell(row, HOW).value)
    read = next(r for r in range(1, ws.max_row + 1)
                if ws.cell(r, 2).value == "sub__ltp_fvtpl_note_total")
    assert "not used by the winning rung" not in str(ws.cell(read, HOW).value or "")


def test_the_live_sum_still_reproduces_the_servers_figure():
    """With the row read twice no longer counted, the trail rows reproduce the published figure, so
    the parent's cell becomes a live sum over them — skipping the row added once — and it evaluates
    to exactly what the server published."""
    ws, at = _sheet()
    assert ws.cell(at, 5).value == f"=E{at + 1}+E{at + 2}+E{at + 4}"
    total = sum(ws.cell(at + k, 5).value for k in (1, 2, 4))
    assert total == 366944
    assert ws.cell(at, 6).value == f"=F{at + 1}+F{at + 2}+F{at + 4}"
