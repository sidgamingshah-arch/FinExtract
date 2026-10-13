"""One note row, one part: two parts of a parent cannot both be given the same printed note row.

From a Lai Sun run on the model route. Note 26 prints the FVTPL holdings and their "Non-current
portion 788,507"; the model cited that row for the non-current FVTPL part AND for the non-current
"other financial assets" part. The parent added the row once, but the second part published
788,507 of other financial assets the filing does not have, and the export listed it twice under
the parent. The row stays with the part whose note it is — by the parts' own `note_terms`.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.services import derivation
from app.services.line_item_config import load_shipped_set
from app.services.note_sourced import cell_of
from app.stages.line_item_llm import LineItemLlmStage

FVTPL, OTHER = "sub__ltp_fvtpl_note_total", "sub__ltp_other_fincl_assets_note_total"


def _prov(y: float) -> Provenance:
    return Provenance(source_kind="native", page_index=285,
                      bbox=BBox(x0=0.81, y0=y, x1=0.87, y1=y + 0.015))


def _note() -> NotesTable:
    table = NotesTable(note_number="26", title="FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS")
    for ordinal, (caption, figure, y) in enumerate((("Listed investments (i)", "36071", 0.30),
                                                    ("Non-current portion", "788507", 0.36),
                                                    ("Unlisted investments (ii)", "927258", 0.40))):
        row = NoteItem(raw_label=caption, ordinal=ordinal)
        row.set_value(ExtractedValue(value=Decimal(figure), basis=Basis.CONSOLIDATED,
                                     period_label="current", provenance=_prov(y)))
        table.items.append(row)
    return table


def _answered(key: str, figure: str) -> LineItem:
    row = LineItem(source_label=key, canonical_key=key)
    row.set_value(ExtractedValue(value=Decimal(figure), basis=Basis.CONSOLIDATED,
                                 period_label="current", provenance=_prov(0.36)))
    row.derivation = derivation.record(None, basis="consolidated", period_label="current",
                                       derivation=derivation.build(
                                           method="line_item_llm:1_cited_row", formula=None,
                                           inputs=[{"label": "note 26: Non-current portion",
                                                    "value": figure}], result=figure))
    return row


@pytest.fixture(scope="module")
def shipped():
    return load_shipped_set()


def _settle(rows, cells, shipped):
    doc = DocumentModel(filename="laisun.pdf")
    doc.notes = [_note()]
    doc.line_items = rows
    ctx = PipelineContext()
    emptied = LineItemLlmStage._settle_shared_note_rows(
        doc, cells, {r.canonical_key: r for r in rows}, shipped, ctx)
    return emptied, ctx


def test_the_row_stays_with_the_part_whose_note_it_is(shipped):
    fvtpl, other = _answered(FVTPL, "788507"), _answered(OTHER, "788507")
    shared = cell_of(_prov(0.36))
    emptied, ctx = _settle([fvtpl, other], {FVTPL: [shared], OTHER: [shared]}, shipped)
    assert emptied == 1
    assert [str(ev.value) for ev in fvtpl.values.values()] == ["788507"]
    assert other.values == {} and other.derivation is None
    assert f"llm_note_row_claimed_by:{FVTPL}" in other.confidence.flags
    assert "llm_note_row_withheld:788507" in other.confidence.flags


def test_a_part_whose_figure_rests_on_other_rows_too_is_left_as_it_is(shipped):
    fvtpl, other = _answered(FVTPL, "788507"), _answered(OTHER, "824578")
    shared, own = cell_of(_prov(0.36)), cell_of(_prov(0.30))
    emptied, ctx = _settle([fvtpl, other], {FVTPL: [shared], OTHER: [shared, own]}, shipped)
    assert emptied == 0
    assert [str(ev.value) for ev in other.values.values()] == ["824578"]
    assert any("left as is" in line for line in ctx.logs)


def test_parts_of_different_parents_may_read_one_row(shipped):
    """The current and non-current lines read different rows of one securities note — and where a
    filing gives both one row, that is the face settlement's question, not this one."""
    ltp, cp = _answered(FVTPL, "788507"), _answered("sub__fa_cp_fvtpl_note_total", "788507")
    shared = cell_of(_prov(0.36))
    emptied, _ctx = _settle([ltp, cp], {FVTPL: [shared], "sub__fa_cp_fvtpl_note_total": [shared]},
                            shipped)
    assert emptied == 0
