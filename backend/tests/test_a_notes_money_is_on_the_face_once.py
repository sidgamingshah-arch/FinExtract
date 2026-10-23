"""A balance-sheet line filled from a note does not count money the face already printed elsewhere.

China SCE 1966 prints "Prepayments, other receivables and other assets 15,062,723" citing note 24
and files it under Other Current Assets; note 24 states other receivables at 5,818,375, which
Other Receivables (CP) reads. Published side by side, the 5,818,375 was in total current assets
twice. The residual gives it up; any other face row keeps it, and the line's calculation runs
without it.

DECIDED BY AMOUNTS. A face row prints a line's money when its figure equals an amount the line
counted, or a total the note prints over a row the line read — never because the two cite one note
number. Lai Sun's note 26 holds the current AND the non-current FVTPL holdings; the face cites it
for the current row, and Securities (LTP), which read only the non-current portion, was deleted for
sharing the citation.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NoteRef, NotesTable,
                                       Provenance)
from app.core.stage import PipelineContext
from app.services import derivation
from app.stages.note_sourced import _settle_against_the_face

FACE, NOTES = 0, 1


def _prov(page: int, y: float, period: str = "current") -> Provenance:
    x = 0.6 if period == "current" else 0.8
    return Provenance(source_kind="pdf", page_index=page,
                      bbox=BBox(x0=x, y0=y, x1=x + 0.1, y1=y + 0.01))


def _ev(amount: str, page: int, y: float = 0.5, period: str = "current") -> ExtractedValue:
    return ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                          basis=Basis.CONSOLIDATED, period_label=period,
                          provenance=_prov(page, y, period))


def _face(key: str, amount: str, note: str, y: float = 0.5, prior: str | None = None) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key)
    li.set_value(_ev(amount, FACE, y))
    if prior is not None:
        li.set_value(_ev(prior, FACE, y, "prior"))
    li.note_refs = [NoteRef(raw=note, numbers=[note])]
    return li


def _filled(key: str, amount: str, note: str, y: float = 0.5, prior: str | None = None) -> LineItem:
    """A line filled from ONE note row printed at `y` on the notes page."""
    li = LineItem(source_label=key, canonical_key=key)
    store = None
    for period, figure in (("current", amount), ("prior", prior)):
        if figure is None:
            continue
        li.set_value(_ev(figure, NOTES, y, period))
        store = derivation.record(store, basis="consolidated", period_label=period,
                                  derivation=derivation.build(
                                      method="note_sourced:sum_of_1_rows", formula=None,
                                      inputs=[{"label": "x", "note": note, "value": figure,
                                               "provenance": _prov(NOTES, y, period).model_dump(
                                                   mode="json")}],
                                      result=figure))
    li.derivation = store
    return li


def _note(number: str, rows: list[tuple[str, str, float, LineRole]]) -> NotesTable:
    """A note table: (caption, current figure, y, role) per row."""
    table = NotesTable(note_number=number, title=f"note {number}", source_pages=[NOTES])
    for ordinal, (caption, figure, y, role) in enumerate(rows):
        item = NoteItem(raw_label=caption, ordinal=ordinal, role=role)
        item.set_value(_ev(figure, NOTES, y))
        table.items.append(item)
    return table


def _item(key, section, parent="", in_output=True):
    return SimpleNamespace(key=key, section_scope=[section], parent=parent, in_output=in_output)


ITEMS = [_item("bs_ca__other_receivables_cp", "bs_ca"), _item("bs_ca__other_current_assets", "bs_ca"),
         _item("bs_ca__trade_and_other_receivables", "bs_ca"),
         _item("bs_nca__secur_and_other_fincl_assets_ltp", "bs_nca"),
         _item("sub__cp_face_trading_fincl_assets", "bs_ca", parent="bs_ca__secur_cp", in_output=False),
         _item("bs_ca__secur_cp", "bs_ca"), _item("bs_ca__finished_goods", "bs_ca"),
         _item("sub__fa_cp_afs_htm_note_total", "bs_ca", parent="bs_ca__secur_cp", in_output=False),
         _item("bs_ca__due_from_related_parties_cp", "bs_ca"),
         _item("bs_cl__due_to_related_parties_cp", "bs_cl")]
SCOPES = {"bs_ca__other_current_assets": "exclusive_residual",
          "bs_ca__trade_and_other_receivables": "exclusive_leaf",
          "sub__cp_face_trading_fincl_assets": "exclusive_leaf"}
T, L = LineRole.TOTAL, LineRole.LINE


def _run(rows, notes=(), items=ITEMS):
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = rows
    doc.notes = list(notes)
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[SimpleNamespace(canonical_key=k, value_scope=v)
                                             for k, v in SCOPES.items()])
    _settle_against_the_face(doc, ctx, items)
    return {li.canonical_key: [str(ev.value) for ev in li.values.values()] for li in doc.line_items}


NOTE_24 = _note("24", [("Prepayments", "9244348", 0.40, L), ("Other receivables", "5818375", 0.50, L),
                       ("Total", "15062723", 0.60, T)])


def test_the_residual_gives_up_what_a_line_read_from_its_note():
    got = _run([_face("bs_ca__other_current_assets", "15062723", "24"),
                _filled("bs_ca__other_receivables_cp", "5818375", "24")], [NOTE_24])
    assert got == {"bs_ca__other_current_assets": ["9244348"],
                   "bs_ca__other_receivables_cp": ["5818375"]}


def test_a_printed_leaf_keeps_its_money_and_the_line_calculates_without_it():
    """佳明 kaming prints "Trade and other receivables" 126,739 as its own column; the 4,444 of
    other receivables its note breaks out are inside it. The line still publishes — what its
    calculation comes to once that money is taken out — with the deduction in its trail."""
    note_18 = _note("18", [("Trade receivables", "122295", 0.40, L),
                           ("Other receivables", "4444", 0.50, L), ("Total", "126739", 0.60, T)])
    rows = [_face("bs_ca__trade_and_other_receivables", "126739", "18"),
            _filled("bs_ca__other_receivables_cp", "4444", "18")]
    got = _run(rows, [note_18])
    assert got == {"bs_ca__trade_and_other_receivables": ["126739"],
                   "bs_ca__other_receivables_cp": ["0"]}
    line = rows[1]
    (deduction,) = [i for i in line.derivation["consolidated:current"]["inputs"] if i.get("deducted")]
    assert deduction["value"] == "4444"
    assert deduction["canonical_key"] == "bs_ca__trade_and_other_receivables"
    assert "counted_on_the_face:bs_ca__trade_and_other_receivables:4444" in line.confidence.flags


def test_money_printed_in_another_section_is_taken_out_not_deleted():
    """1966's LTP read note 26's 344,135, which the face prints as the CURRENT FVTPL row: the same
    money in the wrong column. The line keeps a figure — the calculation without it."""
    got = _run([_face("sub__cp_face_trading_fincl_assets", "344135", "26"),
                _filled("bs_nca__secur_and_other_fincl_assets_ltp", "344135", "26")])
    assert got["bs_nca__secur_and_other_fincl_assets_ltp"] == ["0"]


def test_a_shared_note_number_is_not_shared_money():
    """Lai Sun's note 26: the face cites it for the current 174,822; LTP read its non-current
    portion 788,507. One note, two amounts — nothing to settle."""
    note_26 = _note("26", [("Listed investments", "36071", 0.30, L),
                           ("Unlisted investments", "927258", 0.35, L),
                           ("Less: Portion classified as current", "-174822", 0.40, L),
                           ("Non-current portion", "788507", 0.45, L)])
    got = _run([_face("sub__cp_face_trading_fincl_assets", "174822", "26", prior="70948"),
                _filled("bs_nca__secur_and_other_fincl_assets_ltp", "788507", "26", y=0.45,
                        prior="941274")], [note_26])
    assert got["bs_nca__secur_and_other_fincl_assets_ltp"] == ["788507", "941274"]


def test_an_amount_agreeing_in_one_year_only_is_a_coincidence():
    got = _run([_face("sub__cp_face_trading_fincl_assets", "344135", "9", prior="1000"),
                _filled("bs_nca__secur_and_other_fincl_assets_ltp", "344135", "26",
                        prior="2000")])
    assert got["bs_nca__secur_and_other_fincl_assets_ltp"] == ["344135", "2000"]


def test_an_unclassified_face_row_duplicates_nothing():
    """A row the mapper left unclassified reaches no line and no total."""
    got = _run([_face("engine_unclassified_face__balance_sheet__x", "344135", "26"),
                _filled("bs_nca__secur_and_other_fincl_assets_ltp", "344135", "26")])
    assert got["bs_nca__secur_and_other_fincl_assets_ltp"] == ["344135"]


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


def test_a_note_figure_takes_the_unit_its_statements_face_carries():
    """An Indian filing prints "(₹ in lakhs)" as page chrome: the face rows carry scale 1 while
    the document reads INR×100000, and a note figure written at the document's unit beside them
    made the balance sheet's section reconciliations `mixed_scale`. The face's scale wins; with no
    face rows the document's unit; and on 1966 the two are the same RMB'000.

    THE CURRENCY IS THE DOCUMENT'S WHERE THE FACE NAMES NONE. No check compares currencies, so
    taking the face's empty one bought nothing and lost the label: the Disclosures amount read
    "1,120.00" where it had read "INR 1,120.00", and on a mainland filing (face rows at no
    currency, the document at CNY) every note-route figure stopped carrying the document's unit."""
    from app.core.models.enums import PrintedIn
    from app.core.models.line_item import UnitContext
    from app.stages.note_sourced import _doc_unit, _statement_unit

    def doc_with(face_unit: UnitContext | None,
                 declared: UnitContext | None = None) -> DocumentModel:
        doc = DocumentModel(filename="f.pdf")
        doc.unit_context = declared or UnitContext(currency="INR", scale_factor=Decimal(100000))
        if face_unit is not None:
            row = LineItem(source_label="Cash", canonical_key="bs_ca__cash",
                           printed_in=PrintedIn.FACE)
            row.set_value(ExtractedValue(basis=Basis.STANDALONE, period_label="current",
                                         value=Decimal("300"), value_raw=Decimal("300"),
                                         unit_ctx=face_unit))
            doc.line_items = [row]
        return doc

    chrome = _statement_unit(doc_with(UnitContext()), "standalone")
    assert chrome.scale_factor == Decimal(1) and chrome.currency == "INR"

    # A mainland filing: 单位：元 names the currency, the face rows carry none. The note-route unit
    # is the document's, exactly as it was before the face's unit was read.
    cas = doc_with(UnitContext(), declared=UnitContext(currency="CNY", scale_factor=Decimal(1)))
    assert _statement_unit(cas, "standalone") == _doc_unit(cas)

    # A face that names its own currency keeps it.
    hkd = _statement_unit(doc_with(UnitContext(currency="HKD", scale_factor=Decimal(1000))),
                          "standalone")
    assert (hkd.currency, hkd.scale_factor) == ("HKD", Decimal(1000))

    declared = _statement_unit(doc_with(None), "standalone")
    assert declared.scale_factor == Decimal(100000) and declared.currency == "INR"

    thousands = _statement_unit(
        doc_with(UnitContext(currency="CNY", scale_factor=Decimal(1000))), "standalone")
    assert thousands.scale_factor == Decimal(1000) and thousands.currency == "CNY"

    # A basis the face carries no row for falls back to the document's unit.
    assert _statement_unit(doc_with(UnitContext()), "consolidated").scale_factor == Decimal(100000)


def test_a_siblings_citation_does_not_withhold_a_figure_taken_from_the_face():
    """1966 on the spy route: Securities (CP) took 344,135 from its face part while a sibling part
    cited note 22, which is the face's completed-properties row. Only the money a slot was
    counted from settles it."""
    line = LineItem(source_label="secur", canonical_key="bs_ca__secur_cp")
    line.set_value(_ev("344135", NOTES))
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


def test_a_note_printed_on_the_other_side_of_the_balance_sheet_is_not_a_duplicate():
    """1966's note 25 holds due-from AND due-to balances; the face cites it for the asset row.
    The liability read from it is not money the asset row printed."""
    got = _run([_face("bs_ca__due_from_related_parties_cp", "2588416", "25"),
                _filled("bs_cl__due_to_related_parties_cp", "2588416", "25")])
    assert got["bs_cl__due_to_related_parties_cp"] == ["2588416"]


# ── the shipped HK cascade: Lai Sun's Securities (LTP), the run the user exported ──────────────

def _shipped():
    from app.services.line_item_config import load_shipped_set

    items = list(load_shipped_set().items)
    return items, {i.key: i for i in items}


def _part(key: str, rows: list[tuple[str, str, str, float]]) -> LineItem:
    """A part the model filled from cited note rows: (caption, current, prior, y)."""
    li = LineItem(source_label=key, canonical_key=key)
    store = None
    for period, idx in (("current", 1), ("prior", 2)):
        total = sum(Decimal(r[idx]) for r in rows)
        li.set_value(_ev(str(total), NOTES, rows[0][3], period))
        store = derivation.record(store, basis="consolidated", period_label=period,
                                  derivation=derivation.build(
                                      method=f"line_item_llm:{len(rows)}_cited_rows", formula=None,
                                      inputs=[{"label": r[0], "note": "26", "value": r[idx],
                                               "counted": True,
                                               "provenance": _prov(NOTES, r[3], period).model_dump(
                                                   mode="json")} for r in rows],
                                      result=str(total)))
    li.derivation = store
    return li


def _laisun(extra_face=()):
    """The parts and face rows of the user's exported Lai Sun run (HK$'000)."""
    face_part = "sub__ltp_face_other_non_current_fincl_assets"
    rows = [_face("sub__cp_face_trading_fincl_assets", "174822", "26", y=0.10, prior="70948"),
            _face(face_part, "788507", "26", y=0.20, prior="941274"),
            _face(face_part, "128412", "25", y=0.25, prior="119364"),
            _part("sub__ltp_fvtpl_note_total", [("Non-current portion", "788507", "941274", 0.45)]),
            _part("sub__ltp_fvtoci_note_total", [("Listed investment", "7620", "13860", 0.50),
                                                 ("Unlisted investments", "120792", "105504", 0.55)]),
            *extra_face]
    overshoot = LineItem(source_label="overshoot", canonical_key="sub__fa_cp_intermediate_residual")
    overshoot.set_value(ExtractedValue(value=Decimal("549975"), basis=Basis.CONSOLIDATED,
                                       period_label="current"))
    overshoot.set_value(ExtractedValue(value=Decimal("212844"), basis=Basis.CONSOLIDATED,
                                       period_label="prior"))
    return rows + [overshoot]


def _cascade(rows):
    from app.stages.note_sourced import _fill_by_cascade

    items, defs = _shipped()
    parent = defs["bs_nca__secur_and_other_fincl_assets_ltp"]
    doc = DocumentModel(filename="laisun.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = rows
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[])
    by_key = {li.canonical_key: li for li in rows}
    kids = [(defs[li.canonical_key], li) for li in rows
            if getattr(defs.get(li.canonical_key), "parent", "") == parent.key]
    _fill_by_cascade(parent, kids, by_key, doc, ctx, defs)
    _settle_against_the_face(doc, ctx, items)
    (ltp,) = [li for li in doc.line_items if li.canonical_key == parent.key]
    return ltp, {ev.period_label: str(ev.value) for ev in ltp.values.values()}


def test_laisun_ltp_keeps_its_calculation():
    """788,507 + 128,412 − the 549,975 the current line could not absorb, and 941,274 + 119,364 −
    212,844 — published blank in both years before, because note 26 is also the current row's."""
    ltp, got = _cascade(_laisun())
    assert got == {"current": "366944", "prior": "847794"}
    assert not any(f.startswith("counted_on_the_face") for f in ltp.confidence.flags)


def test_a_part_printed_in_another_line_is_taken_out_and_the_cascade_runs_again():
    """FVTOCI printed on the face in another non-current line: the cascade runs without it, and
    the deduction is in the trail."""
    other = _face("bs_nca__investments_in_associates", "128412", "25", y=0.30, prior="119364")
    ltp, got = _cascade(_laisun([other]))
    assert got == {"current": str(788507 - 549975), "prior": str(941274 - 212844)}
    trail = ltp.derivation["consolidated:current"]
    assert "settled_against_face" in trail["flags"]
    (cut,) = [i for i in trail["inputs"] if i.get("deducted") and i.get("canonical_key")
              == "bs_nca__investments_in_associates"]
    assert cut["value"] == "128412"
