"""A derived figure is written only on a basis the filing's face statements print.

Every writer of a derived figure asks `services.face_bases` first — the deterministic note route
(prose, rows, parents, cascades, internal lines), the model's cited rows and prose, and the
assembled components — and a figure for a basis the statements do not print is refused: the row is
flagged `basis_not_printed:<basis>:<period>` and the run log names the figure. On a filing whose
statements are all the company's, a consolidated figure has no statement to sit on; written, it
would be the only thing on a Consolidated tab the filing never printed.

The answer is the DOCUMENT's, not a statement's: `notes__contingent_liabilities` lives on the
"notes" statement, which has no face rows, and is judged by the statements the filing prints. A
filing whose face could not be read refuses nothing.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NoteRef, NotesTable
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.face_bases import REFUSED_FLAG
from app.stages.note_sourced import NoteSourcedStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
PARENT = "is_pl__other_non_operating_inc_exp"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _page(index: int, scope: str | None, kind: PageKind = PageKind.FACE,
          statement: str | None = "profit_and_loss") -> PageSource:
    return PageSource(index=index, kind=kind, scope=scope,
                      statement=statement if kind == PageKind.FACE else None)


def _face_row(key: str, caption: str, slots: dict[tuple[Basis, str], str]) -> LineItem:
    row = LineItem(source_label=caption, canonical_key=key)
    for i, ((basis, period), amount) in enumerate(slots.items()):
        row.values[f"k{i}"] = ExtractedValue(
            basis=basis, period_label=period, value=Decimal(amount),
            value_raw=Decimal(amount), provenance=Provenance(page_index=88))
    return row


def _halves() -> list[LineItem]:
    """000709's 营业外 halves, on both statements (see test_note_sourced)."""
    return [
        _face_row("sub__non_operating_income", "加：营业外收入",
                  {(Basis.CONSOLIDATED, "current"): "488570415.03",
                   (Basis.STANDALONE, "current"): "480646078.44"}),
        _face_row("sub__non_operating_expenses", "减：营业外支出",
                  {(Basis.CONSOLIDATED, "current"): "-56388111.32",
                   (Basis.STANDALONE, "current"): "-3927788.38"}),
    ]


def _run(shipped, pages, rows, notes=()):
    doc = DocumentModel(filename="f.pdf")
    doc.pages = list(pages)
    doc.notes = list(notes)
    doc.line_items = list(rows)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx), ctx


def _slots(doc, key: str) -> dict[tuple[str, str], Decimal]:
    return {(ev.basis.value, ev.period_label): ev.value
            for li in doc.line_items if li.canonical_key == key
            for ev in li.values.values() if ev.value is not None}


def _flags(doc, key: str) -> list[str]:
    return [f for li in doc.line_items if li.canonical_key == key for f in li.confidence.flags]


def test_a_parent_is_not_written_on_a_basis_the_face_does_not_print(shipped):
    doc, ctx = _run(shipped, [_page(0, "company")], _halves())

    assert _slots(doc, PARENT) == {("standalone", "current"): Decimal("476718290.06")}
    assert f"{REFUSED_FLAG}:consolidated:current" in _flags(doc, PARENT)
    assert any("REFUSED 432182303.71" in line for line in ctx.logs), ctx.logs
    assert any(line.startswith("note_sourced:basis_refused=") for line in ctx.logs)


def test_a_filing_that_prints_both_is_written_on_both(shipped):
    doc, ctx = _run(shipped, [_page(0, "company"), _page(1, "consolidated")], _halves())

    assert _slots(doc, PARENT) == {("consolidated", "current"): Decimal("432182303.71"),
                                   ("standalone", "current"): Decimal("476718290.06")}
    assert not any(f.startswith(REFUSED_FLAG) for f in _flags(doc, PARENT))
    assert not any(line.startswith("note_sourced:basis_refused=") for line in ctx.logs)


def test_an_unread_face_refuses_nothing(shipped):
    doc, _ctx = _run(shipped, [_page(0, None)], _halves())

    assert set(_slots(doc, PARENT)) == {("consolidated", "current"), ("standalone", "current")}


_COMPANY_ONLY = (lambda: [_page(0, "company", statement="balance_sheet"),
                           _page(1, None, PageKind.NOTES)])


def _contingent_note(table_basis: Basis | None, value_basis: Basis) -> NotesTable:
    """Note 28, bank guarantees 1,120 / 980, read on ``value_basis`` under ``table_basis``."""
    note = NotesTable(note_number="28", title="Contingent liabilities", basis=table_basis,
                      source_pages=[1])
    row = NoteItem(raw_label="Bank guarantees")
    for period, amount in (("current", "1120"), ("prior", "980")):
        row.set_value(ExtractedValue(basis=value_basis, period_label=period,
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=1)))
    note.items.append(row)
    return note


def _cash_citing_28() -> LineItem:
    face = _face_row("bs_ca__cash", "Cash", {(Basis.STANDALONE, "current"): "300",
                                            (Basis.STANDALONE, "prior"): "250"})
    face.note_refs = [NoteRef(raw="28", numbers=["28"])]
    return face


def test_the_notes_line_is_judged_by_the_documents_face(shipped):
    """`notes__contingent_liabilities` has no statement of its own to print it; a company-only
    filing's contingent-liability note fills it on standalone."""
    doc, ctx = _run(shipped, _COMPANY_ONLY(), [_cash_citing_28()],
                    [_contingent_note(Basis.STANDALONE, Basis.STANDALONE)])

    assert _slots(doc, "notes__contingent_liabilities") == {
        ("standalone", "current"): Decimal("1120"), ("standalone", "prior"): Decimal("980")}
    assert not any(f.startswith(REFUSED_FLAG) for li in doc.line_items
                   for f in li.confidence.flags)


# ── each route that writes a derived figure asks before it writes ─────────────────────────────

_PART = "sub__cl_contingency_note_exposures"


def test_a_note_row_on_a_basis_the_face_does_not_print_is_refused(shipped):
    """The row route: the note's figures are the GROUP's (a note the row builder read under no
    basis of its own) on a filing whose statements are all the company's. The part is written in
    no consolidated column, its row says why, and the run log names each refused figure."""
    doc, ctx = _run(shipped, _COMPANY_ONLY(), [_cash_citing_28()],
                    [_contingent_note(None, Basis.CONSOLIDATED)])

    assert _slots(doc, _PART) == {}
    assert {f"{REFUSED_FLAG}:consolidated:current",
            f"{REFUSED_FLAG}:consolidated:prior"} <= set(_flags(doc, _PART))
    assert any(line.startswith(f"note_sourced:{_PART}[consolidated:current] REFUSED 1120")
               for line in ctx.logs), ctx.logs

    # The same note on a filing whose face could not be read is written as it always was, so the
    # assertions above measure the refusal and not a row that was never selected.
    doc, _ctx = _run(shipped, [_page(0, None, statement="balance_sheet")], [_cash_citing_28()],
                     [_contingent_note(None, Basis.CONSOLIDATED)])
    assert _slots(doc, _PART) == {("consolidated", "current"): Decimal("1120"),
                                  ("consolidated", "prior"): Decimal("980")}


def test_a_note_figure_is_written_in_its_faces_scale_and_the_documents_currency(shipped):
    """An Indian filing prints "(Rupees in lakhs)" as page chrome: the face rows carry scale 1 and
    no currency while the document reads INR x 100,000. The note-route figure takes the FACE's
    scale, so the balance sheet's sections add it to its siblings, and the DOCUMENT's currency,
    which the face does not name: written without it, the Disclosures amount lost its "INR"."""
    from app.core.models.enums import PrintedIn
    from app.core.models.line_item import UnitContext

    face = _cash_citing_28()
    face.printed_in = PrintedIn.FACE
    for ev in face.values.values():
        ev.provenance = Provenance(page_index=0)
    doc = DocumentModel(filename="f.pdf")
    doc.pages = _COMPANY_ONLY()
    doc.unit_context = UnitContext(currency="INR", scale_factor=Decimal(100000),
                                   units_label="lakhs")
    doc.notes = [_contingent_note(Basis.STANDALONE, Basis.STANDALONE)]
    doc.line_items = [face]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    NoteSourcedStage().run(doc, ctx)

    units = {(ev.basis.value, ev.period_label): (ev.unit_ctx.currency, ev.unit_ctx.scale_factor)
             for li in doc.line_items if li.canonical_key == _PART for ev in li.values.values()}
    assert units == {("standalone", "current"): ("INR", Decimal(1)),
                     ("standalone", "prior"): ("INR", Decimal(1))}


RD_1200 = "Depreciation of HK$1,200 is included in research and development expenses."


def _rd_note(basis: Basis | None) -> NotesTable:
    """A note whose figure is in its SENTENCE — see test_note_sourced._prose_note."""
    return NotesTable(note_number="8", title="Property, plant and equipment", basis=basis,
                      source_pages=[1], source_text=RD_1200)


def test_a_sentence_on_a_basis_the_face_does_not_print_is_refused(shipped):
    """The prose route: a sentence read from a note that says it is the group's is filed under
    consolidated, and a filing that prints only the company's statements has no column for it."""
    pages = [_page(0, "company"), _page(1, None, PageKind.NOTES)]
    doc, ctx = _run(shipped, pages, [], [_rd_note(Basis.CONSOLIDATED)])

    assert _slots(doc, "sub__rd_depreciation") == {}
    assert f"{REFUSED_FLAG}:consolidated:current" in _flags(doc, "sub__rd_depreciation")
    assert any(line.startswith("note_sourced:sub__rd_depreciation[consolidated:current] "
                               "REFUSED 1200") for line in ctx.logs), ctx.logs

    doc, _ctx = _run(shipped, [_page(0, "company"), _page(1, None, PageKind.NOTES)], [],
                     [_rd_note(Basis.STANDALONE)])
    assert _slots(doc, "sub__rd_depreciation") == {("standalone", "current"): Decimal("1200")}


def test_an_arithmetic_line_is_not_computed_on_a_basis_the_face_does_not_print(shipped):
    """An internal line computed from its own declaration (`sub__rp_find_1`, the related-party
    rows the balance sheet prints) is computed only in the columns the statements print, and a
    line refused in a column is never created for it."""
    def rows():
        return [_face_row("bs_nca__due_from_directors", "Due from directors",
                          {(Basis.CONSOLIDATED, "current"): "100",
                           (Basis.STANDALONE, "current"): "60"})]

    doc, ctx = _run(shipped, _COMPANY_ONLY(), rows())

    assert _slots(doc, "sub__rp_find_1") == {("standalone", "current"): Decimal("60")}
    assert any(line.startswith("note_sourced:sub__rp_find_1[consolidated:current] REFUSED 100")
               for line in ctx.logs), ctx.logs

    doc, _ctx = _run(shipped, [_page(0, None, statement="balance_sheet")], rows())
    assert _slots(doc, "sub__rp_find_1") == {("consolidated", "current"): Decimal("100"),
                                             ("standalone", "current"): Decimal("60")}


# ── the model's route ─────────────────────────────────────────────────────────────────────────

_CITED = "sub__ltp_other_fincl_assets_note_total"
_DECOYS = ["货币资金", "应收账款", "存货", "固定资产", "在建工程", "无形资产", "长期待摊费用",
           "递延所得税资产", "短期借款", "应付账款", "应付职工薪酬", "营业收入", "营业成本",
           "管理费用", "研发费用", "财务费用"]


def _llm_doc(pages) -> tuple[DocumentModel, PipelineContext]:
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage
    from tests.spy_line_item_llm import SpyLineItemLlm

    def note(rows, number="七、19", title="其他非流动金融资产"):
        table = NotesTable(note_number=number, title=title)
        for caption, amount in rows:
            item = NoteItem(raw_label=caption)
            item.values["a"] = ExtractedValue(
                basis=Basis.CONSOLIDATED, period_label="current", value=Decimal(amount),
                value_raw=Decimal(amount), provenance=Provenance(page_index=7,
                                                                 text_snippet=caption))
            table.items.append(item)
        return table

    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.pages = list(pages)
    doc.notes = [note([("非上市股权投资", "531795561.02"), ("合计", "531795561.02")])]
    doc.notes += [note([("合计", "1.00")], number=f"七、{i + 40}", title=t)
                  for i, t in enumerate(_DECOYS)]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [_CITED]
    spy = SpyLineItemLlm(only={_CITED}, cite={_CITED: ("七、19", "非上市股权投资")})
    ctx.registry.register("llm", "spy", lambda: spy)
    ctx.settings.llm.provider = "spy"
    LineItemLlmStage().run(doc, ctx)
    return doc, ctx


def test_the_model_route_is_refused_the_same_way():
    """The model cites a note row the row builder filed consolidated, on a filing whose statements
    are all the company's: no consolidated slot is written, and the row says why."""
    doc, ctx = _llm_doc([_page(0, "company", statement="balance_sheet"),
                         _page(1, None, PageKind.NOTES)])

    assert ("consolidated", "current") not in _slots(doc, _CITED)
    assert f"{REFUSED_FLAG}:consolidated:current" in _flags(doc, _CITED)
    assert any(line.startswith(f"line_item_llm:{_CITED}[consolidated:current] REFUSED")
               for line in ctx.logs)

    doc, _ctx = _llm_doc([])
    assert _slots(doc, _CITED) == {("consolidated", "current"): Decimal("531795561.02")}


def test_the_models_sentence_is_refused_the_same_way():
    """The model cites a SENTENCE of a note that says it is the group's, on a filing whose
    statements are all the company's: nothing is written, and the row says why."""
    from types import SimpleNamespace

    from app.stages.line_item_llm import LineItemLlmStage

    def write(note_basis: Basis):
        doc = DocumentModel(filename="f.pdf")
        doc.pages = _COMPANY_ONLY()
        doc.notes = [_rd_note(note_basis)]
        row = LineItem(source_label="R&D depreciation", canonical_key="sub__rd_depreciation")
        ctx = PipelineContext()
        cited = [{"prose": True, "note": "8", "figures": {"prose": "1200"}, "quote": RD_1200,
                  "caption": None, "provenance": None}]
        wrote = LineItemLlmStage._write_prose(
            row, doc, Decimal("1200"), cited, ctx,
            SimpleNamespace(key="sub__rd_depreciation", label="R&D depreciation"))
        return wrote, row, ctx

    wrote, row, ctx = write(Basis.CONSOLIDATED)
    assert wrote == 0 and not row.values
    assert f"{REFUSED_FLAG}:consolidated:current" in row.confidence.flags
    assert any(line.startswith("line_item_llm:sub__rd_depreciation[consolidated:current] "
                               "REFUSED 1200") for line in ctx.logs), ctx.logs

    wrote, row, _ctx = write(Basis.STANDALONE)
    assert wrote == 1
    assert {(ev.basis.value, ev.period_label): ev.value for ev in row.values.values()} == {
        ("standalone", "current"): Decimal("1200")}


def test_a_cascade_does_not_take_over_a_printed_slot_on_an_unprinted_basis():
    """A cascade refused in a column neither writes nor supersedes the rows printed there."""
    from app.services.line_item_config import load_shipped_set
    from app.stages.note_sourced import _fill_by_cascade

    items = list(load_shipped_set().items)
    defs = {i.key: i for i in items}
    parent = defs["bs_nca__secur_and_other_fincl_assets_ltp"]

    def run(pages):
        printed = _face_row(parent.key, "Financial assets at FVTPL",
                            {(Basis.CONSOLIDATED, "current"): "128412"})
        part = _face_row("sub__ltp_fvtpl_note_total", "Non-current portion",
                         {(Basis.CONSOLIDATED, "current"): "788507"})
        doc = DocumentModel(filename="f.pdf")
        doc.pages = list(pages)
        doc.line_items = [printed, part]
        ctx = PipelineContext()
        by_key = {li.canonical_key: li for li in doc.line_items}
        _fill_by_cascade(parent, [(defs[part.canonical_key], part)], by_key, doc, ctx, defs)
        return printed, ctx

    printed, ctx = run([_page(0, "company", statement="balance_sheet"),
                        _page(1, None, PageKind.NOTES)])
    assert _slots(DocumentModel(line_items=[printed]), parent.key) == {
        ("consolidated", "current"): Decimal("128412")}
    assert not any(f.startswith(("superseded_by_cascade", "cascade_over_printed"))
                   for f in printed.confidence.flags), printed.confidence.flags
    assert f"{REFUSED_FLAG}:consolidated:current" in printed.confidence.flags

    # The same rows on a filing whose face could not be read: the cascade takes the slot, as it
    # always has — so the assertions above are measuring the refusal and not a rung that never ran.
    printed, _ctx = run([])
    assert any(f.startswith("cascade_over_printed") for f in printed.confidence.flags)
