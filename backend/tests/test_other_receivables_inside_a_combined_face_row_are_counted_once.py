"""OTHER RECEIVABLES THE FACE PRINTS INSIDE A COMBINED ROW ARE NOT ADDED BESIDE IT.

NEW FILE -> backend/tests/test_other_receivables_inside_a_combined_face_row_are_counted_once.py

An IFRS statement of financial position prints one combined "Trade and other receivables" row,
and its note prints the other receivables inside it. Other Receivables(CP) read the note
(CP_P1) and was added into total current assets beside the combined row, which already holds it:

    Vivo Energy Mauritius 2025  note 19  59,809 / 59,026 inside 1,257,948 / 1,253,741 (p67)
                                total current assets 2,798,515 / 3,173,334, printed 2,738,706 / 3,114,308
    佳明 2025/26                note 18   4,444 / 932    inside 126,739 / 93,249 (p63)
    Moroil 2025                 note 14   5,218 / 7,723  (p66)

CP_P1 is now `carved_from_face` (a config change): where the face prints no other-receivables row
of its own, the line is shown and the section does not add it. A face that DOES print the row
(a CAS 其他应收款, bound to `sub__cp_face_other_receivables`) is unchanged — and it is looked for
on every row of that part, not only the last one kept: 688008 prints it on its consolidated (p149)
and its company (p151) balance sheets, and reading only the company's row carved the group's
4,143,856.36 out of current assets.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.stages import note_sourced as stage

SET = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_line_items.json"


def _item(key):
    data = json.loads(SET.read_text("utf-8"))
    return next(x for x in data["items"] if x["key"] == key)


def test_the_note_rung_of_other_receivables_is_carved_from_the_face():
    rungs = {r["id"]: r for r in _item("bs_ca__other_receivables_cp")["cascade"]}
    assert rungs["CP_P1"].get("carved_from_face") is True
    assert not rungs["FROM_THE_FACE"].get("carved_from_face")


def _row(key, basis, period, value):
    row = LineItem(source_label=key, canonical_key=key)
    row.set_value(ExtractedValue(basis=Basis(basis), period_label=period, value=Decimal(value)))
    return row


def _defs():
    face = SimpleNamespace(key="sub__cp_face_other_receivables", route="face",
                           statement="balance_sheet")
    note = SimpleNamespace(key="sub__cp_other_receivables_net", route="note_tables",
                           statement=None)
    parent = SimpleNamespace(
        key="bs_ca__other_receivables_cp", statement="balance_sheet",
        cascade=[SimpleNamespace(id="CP_P1", terms=[SimpleNamespace(ref=note.key)]),
                 SimpleNamespace(id="FROM_THE_FACE", terms=[SimpleNamespace(ref=face.key)])])
    return parent, {face.key: face, note.key: note}


def test_a_face_row_on_the_consolidated_balance_sheet_is_seen_beside_the_companys():
    parent, defs = _defs()
    group = _row("sub__cp_face_other_receivables", "consolidated", "current", "4143856.36")
    company = _row("sub__cp_face_other_receivables", "standalone", "current", "1287570989.98")
    doc = DocumentModel(line_items=[group, company])
    by_key = {li.canonical_key: li for li in doc.line_items}      # keeps the company's row only
    assert stage._face_prints_line(parent, by_key, defs, "consolidated", "current", doc=doc)
    assert stage._face_prints_line(parent, by_key, defs, "standalone", "current", doc=doc)


def test_a_face_that_prints_no_other_receivables_row_carves_the_line():
    parent, defs = _defs()
    doc = DocumentModel(line_items=[])
    assert not stage._face_prints_line(parent, {}, defs, "consolidated", "current", doc=doc)
