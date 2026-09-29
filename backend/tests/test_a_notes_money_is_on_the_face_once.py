"""A balance-sheet line filled from a note does not count money the face already printed elsewhere.

China SCE 1966 prints "Prepayments, other receivables and other assets 15,062,723" citing note 24
and files it under Other Current Assets; note 24 states other receivables at 5,818,375, which
Other Receivables (CP) reads. Published side by side, the 5,818,375 was in total current assets
twice. The residual gives it up; a printed leaf, or a row in another section, keeps it and the
note-sourced figure is withheld.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NoteRef, Provenance
from app.core.stage import PipelineContext
from app.services import derivation
from app.stages.note_sourced import _settle_against_the_face


def _ev(amount: str, face: bool) -> ExtractedValue:
    return ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                          basis=Basis.CONSOLIDATED, period_label="current",
                          provenance=Provenance(source_kind="pdf", page_index=0 if face else 1))


def _face(key: str, amount: str, note: str) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key)
    li.set_value(_ev(amount, True))
    li.note_refs = [NoteRef(raw=note, numbers=[note])]
    return li


def _filled(key: str, amount: str, note: str) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key)
    li.set_value(_ev(amount, False))
    li.derivation = derivation.record(None, basis="consolidated", period_label="current",
                                      derivation=derivation.build(
                                          method="note_sourced:sum_of_1_rows", formula=None,
                                          inputs=[{"label": "x", "note": note, "value": amount}],
                                          result=amount))
    return li


def _item(key, section, parent="", in_output=True):
    return SimpleNamespace(key=key, section_scope=[section], parent=parent, in_output=in_output)


ITEMS = [_item("bs_ca__other_receivables_cp", "bs_ca"), _item("bs_ca__other_current_assets", "bs_ca"),
         _item("bs_ca__trade_and_other_receivables", "bs_ca"),
         _item("bs_nca__secur_and_other_fincl_assets_ltp", "bs_nca"),
         _item("sub__cp_face_trading_fincl_assets", "bs_ca", parent="bs_ca__secur_cp", in_output=False),
         _item("bs_ca__secur_cp", "bs_ca"), _item("bs_ca__finished_goods", "bs_ca"),
         _item("sub__fa_cp_afs_htm_note_total", "bs_ca", parent="bs_ca__secur_cp", in_output=False)]
SCOPES = {"bs_ca__other_current_assets": "exclusive_residual",
          "bs_ca__trade_and_other_receivables": "exclusive_leaf",
          "sub__cp_face_trading_fincl_assets": "exclusive_leaf"}


def _run(rows):
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=0, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=1, kind=PageKind.NOTES))
    doc.line_items = rows
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[SimpleNamespace(canonical_key=k, value_scope=v)
                                             for k, v in SCOPES.items()])
    _settle_against_the_face(doc, ctx, ITEMS)
    return {li.canonical_key: [str(ev.value) for ev in li.values.values()] for li in doc.line_items}


def test_the_residual_gives_up_what_a_line_read_from_its_note():
    got = _run([_face("bs_ca__other_current_assets", "15062723", "24"),
                _filled("bs_ca__other_receivables_cp", "5818375", "24")])
    assert got == {"bs_ca__other_current_assets": ["9244348"],
                   "bs_ca__other_receivables_cp": ["5818375"]}


def test_a_printed_leaf_keeps_its_money_and_the_note_reading_is_withheld():
    got = _run([_face("bs_ca__trade_and_other_receivables", "126739", "18"),
                _filled("bs_ca__other_receivables_cp", "4444", "18")])
    assert got == {"bs_ca__trade_and_other_receivables": ["126739"],
                   "bs_ca__other_receivables_cp": []}


def test_a_note_printed_in_another_section_is_not_read_again_in_this_one():
    got = _run([_face("sub__cp_face_trading_fincl_assets", "344135", "26"),
                _filled("bs_nca__secur_and_other_fincl_assets_ltp", "344135", "26")])
    assert got["bs_nca__secur_and_other_fincl_assets_ltp"] == []


def test_a_lines_own_face_part_is_not_a_duplicate():
    got = _run([_face("sub__cp_face_trading_fincl_assets", "344135", "26"),
                _filled("bs_ca__secur_cp", "344135", "26")])
    assert got == {"sub__cp_face_trading_fincl_assets": ["344135"], "bs_ca__secur_cp": ["344135"]}


def test_a_note_figure_is_written_in_the_statements_units():
    """A note table is stated in the statements' units; written at scale 1 beside RMB'000
    siblings, 1966's other receivables made `bs_ca`'s reconciliation `mixed_scale`."""
    from app.core.models.line_item import UnitContext
    from app.stages.note_sourced import _doc_unit, _write

    doc = DocumentModel(filename="f.pdf")
    doc.unit_context = UnitContext(currency="CNY", scale_factor=Decimal(1000))
    row = LineItem(source_label="x", canonical_key="sub__cp_other_receivables_net_row")
    _write(row, "consolidated", "current", Decimal(5818375), by="note_source",
           unit_ctx=_doc_unit(doc))
    (ev,) = row.values.values()
    assert ev.unit_ctx.scale_factor == Decimal(1000) and ev.unit_ctx.currency == "CNY"
    assert _doc_unit(DocumentModel(filename="g.pdf")) is None


def test_a_siblings_citation_does_not_withhold_a_figure_taken_from_the_face():
    """1966 on the spy route: Securities (CP) took 344,135 from its face part while a sibling part
    cited note 22, which is the face's completed-properties row. Only the notes a slot was
    counted from settle it."""
    line = LineItem(source_label="secur", canonical_key="bs_ca__secur_cp")
    line.set_value(_ev("344135", False))
    line.derivation = derivation.record(None, basis="consolidated", period_label="current",
                                        derivation=derivation.build(
                                            method="cascade:FROM_THE_FACE", formula=None,
                                            inputs=[{"label": "face", "value": "344135",
                                                     "canonical_key": "sub__cp_face_trading_fincl_assets"}],
                                            result="344135"))
    sibling = _filled("sub__fa_cp_afs_htm_note_total", "31", "22")
    got = _run([_face("sub__cp_face_trading_fincl_assets", "344135", "26"),
                _face("bs_ca__finished_goods", "6253504", "22"), line, sibling])
    assert got["bs_ca__secur_cp"] == ["344135"]
