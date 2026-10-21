"""A FACE CITATION NAMES ITS ENTITY — the consolidated block or the standalone one.

A filing prints the group's statements and the parent company's, so one caption is printed twice
under one statement token. A citation naming only the statement and the caption resolved to the
first in document order: on Asian Paints the standalone "Revenue from operations", filed in the
standalone slot, and the consolidated line published nothing. `SourceRef.entity` carries the
block's `entity`; a citation without it resolves as before.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import face_context
from app.services.line_item_llm import REPLY_CONTRACT
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources


def _row(caption, page, basis, current, prior):
    li = LineItem(source_label=caption)
    for label, v in (("current", current), ("prior", prior)):
        li.values[label] = ExtractedValue(basis=basis, period_label=label, value=Decimal(v),
                                          value_raw=Decimal(v),
                                          provenance=Provenance(page_index=page))
    return li


def _face():
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=10, kind=PageKind.FACE, statement="profit_and_loss"),
                 PageSource(index=40, kind=PageKind.FACE, statement="profit_and_loss")]
    # The standalone statements are printed first, as Asian Paints prints them.
    doc.line_items = [_row("Revenue from operations", 10, Basis.STANDALONE, "30000", "29000"),
                      _row("Revenue from operations", 40, Basis.CONSOLIDATED, "34000", "33000"),
                      _row("Other income", 10, Basis.STANDALONE, "500", "400")]
    return face_context.face_index(doc)


def _cite(**kw):
    return resolve_sources([SourceRef(statement="profit_and_loss",
                                      caption=kw.pop("caption", "Revenue from operations"), **kw)],
                           [], _face())


def test_the_entity_picks_the_block():
    resolved, unresolved = _cite(entity="consolidated")
    assert not unresolved
    assert resolved[0]["basis"] == "consolidated"
    assert resolved[0]["figures"] == {"current": "34000", "prior": "33000"}
    resolved, _ = _cite(entity="standalone")
    assert resolved[0]["basis"] == "standalone"
    assert resolved[0]["figures"]["current"] == "30000"


def test_a_citation_without_an_entity_resolves_as_before():
    resolved, unresolved = _cite()
    assert not unresolved and resolved[0]["basis"] == "standalone"


def test_an_entity_the_caption_is_not_printed_under_is_refused_not_widened():
    resolved, unresolved = _cite(caption="Other income", entity="consolidated")
    assert resolved == []
    assert "not in its consolidated block" in unresolved[0]["why"]


def test_the_reply_contract_asks_for_the_entity():
    assert "Give the block's `entity` in `entity`" in " ".join(REPLY_CONTRACT.split())
