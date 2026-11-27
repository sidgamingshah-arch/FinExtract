"""A SINGLE ENTITY PRINTS ONE SET OF STATEMENTS AND NAMES NO ONE — ITS BASIS IS STILL KNOWN.

NEW FILE -> backend/tests/test_a_single_entity_is_filed_on_its_one_basis.py

Vivo Energy Mauritius 2025 has no subsidiaries. Its statements are titled "STATEMENT OF … AS AT
31 DECEMBER 2025" with two year columns — no "consolidated", no "company", no Group | Company
band — so no page's entity was read and the run logged `face_bases=unknown(page=34: a statement
page whose entity was not read sits ahead of the notes)`.

The product's convention, decided: a single entity's figures are its primary statements and are
filed under the consolidated basis, and the reason is recorded (`single_entity`). The whole-PDF
model is told the same convention, and an answer it files as "standalone" is refiled to that one
basis without a review (it is no second set of statements). Only where NO statement page anywhere
names an entity: a group whose other statements were read keeps an unread page unknown.
"""
from __future__ import annotations

from app.core.models.document import PageSource
from app.core.models.enums import PageKind
from app.services import whole_pdf as W
from app.services.face_bases import SINGLE_ENTITY, face_bases


def _half(side, statement, folio):
    return {"side": side, "kind": "face", "statement": statement, "scope": None,
            "scope_columns": [], "printed_page": folio,
            "evidence": {"state": "face", "matched_title": f"STATEMENT OF {statement} 2025"}}


def _sheet(index, left, right, scope=None):
    return PageSource(index=index, kind=PageKind.FACE, statement=left["statement"], scope=scope,
                      printed_page=left["printed_page"],
                      evidence={"spread": {"fold": 0.4975, "halves": [left, right]}})


def _notes(index):
    return PageSource(index=index, kind=PageKind.NOTES)


def test_statements_that_name_no_entity_are_one_entitys_on_the_consolidated_basis():
    pages = [_sheet(34, _half("left", "profit_and_loss", "66"), _half("right", "balance_sheet", "67")),
             _sheet(35, _half("left", "changes_in_equity", "68"), _half("right", "cash_flow", "69")),
             _notes(36)]
    fb = face_bases(pages)
    assert fb.known and fb.bases == {"consolidated"} and fb.sole == "consolidated"
    assert fb.rule == SINGLE_ENTITY
    assert "single_entity" in fb.describe()
    assert fb.counted_pages == {34, 35}


def test_a_group_whose_other_statement_was_read_keeps_the_unread_one_unknown():
    named = dict(_half("left", "changes_in_equity", "124"), scope="consolidated")
    pages = [PageSource(index=17, kind=PageKind.FACE, statement="profit_and_loss"),
             _sheet(63, named, _half("right", "changes_in_equity", "125")),
             _notes(70)]
    fb = face_bases(pages)
    assert not fb.known and fb.rule == ""


def test_a_statement_page_whose_title_was_not_read_stays_unknown():
    """A classifier miss or a page a person marked: no title read, no evidence of one entity."""
    untitled = dict(_half("left", "profit_and_loss", "66"), evidence={"state": "face"})
    pages = [_sheet(34, untitled, _half("right", "balance_sheet", "67")), _notes(36)]
    assert not face_bases(pages).known
    assert not face_bases([PageSource(index=0, kind=PageKind.FACE,
                                      statement="profit_and_loss"), _notes(1)]).known


def test_a_column_band_names_the_entities():
    page = PageSource(index=5, kind=PageKind.FACE, statement="balance_sheet",
                      scope_columns=["THE GROUP", "THE COMPANY"])
    assert not face_bases([page, _notes(6)]).known


def test_the_whole_pdf_model_is_told_the_convention():
    assert "names no group or company" in W.SYSTEM
    assert 'their basis is "consolidated"' in W.SYSTEM


def test_a_single_entitys_statement_is_filed_on_the_consolidated_basis_through_the_pipeline():
    """End to end: the fixture's one statement is titled "Statement of Financial Position" and names
    no group or company. It is filed on the consolidated basis with the reason said, and its notes,
    which print no basis of their own, are left as they were read."""
    import pytest

    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_multipage_pdf
    from tests.test_a_note_takes_the_faces_basis import _run

    doc, logs = _run(make_multipage_pdf())

    assert "extract:face_bases=consolidated(single_entity: no statement names a group or a " \
           "company)" in logs
    assert doc.notes and all(t.basis is None for t in doc.notes)
