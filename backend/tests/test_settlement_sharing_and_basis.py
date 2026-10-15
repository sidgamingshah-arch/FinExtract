"""Settlement, sharing and basis eligibility.

1. FACE SETTLEMENT NEEDS MORE THAN ONE YEAR'S COINCIDENCE. A balance-sheet line read from a note
   gives up money a face row elsewhere prints (`stages.note_sourced._settle_against_the_face`). It
   is decided by amounts; where both years are printed the face row must match in both. Where only
   one year can be compared, the amount alone settles nothing: the face row must cite a note the
   line read, or print that note's own total over the row the line read (containment). Before, a
   single-year match settled — an LTP line read from note 26 went to 0 against an unrelated
   note-9 row.
2. A PARENT AND ITS PART MAY BOTH STAND ON ONE PRINTED ROW the model cited for both
   (`LineItemLlmStage._settle_shared_printed_rows`): neither is emptied.
3. A SHARED FIGURE IS REMOVED ONLY ON A CONFIDENT ANSWER (`SharedFiguresStage`): below
   `extraction.llm_shared_figure_min_confidence` the contest is flagged `shared_figure_unresolved`
   and nothing is removed.
4. A DISCLOSURE LINE MAY STAND ON ITS NOTE'S OWN BASIS (`services.face_bases`). On a filing whose
   statements print only the group's, a contingent-liability note headed for the company is
   accepted for `notes__contingent_liabilities` and its parts when the note's basis is explicit (a
   母公司 chapter, a Group|Company band, or a carried explicit basis), and refused, as a statement
   line's figure is, when it is a default reading.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NoteRef, NotesTable
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import derivation
from app.services.face_bases import ACCEPTED_FLAG, REFUSED_FLAG, is_disclosure
from app.stages.note_sourced import NoteSourcedStage, _settle_against_the_face

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
FACE, NOTES = 0, 1


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


# ── 1. face settlement ────────────────────────────────────────────────────────────────────────

def _prov(page: int, y: float, period: str = "current") -> Provenance:
    x = 0.6 if period == "current" else 0.8
    return Provenance(source_kind="pdf", page_index=page,
                      bbox=BBox(x0=x, y0=y, x1=x + 0.1, y1=y + 0.01))


def _ev(amount: str, page: int, y: float = 0.5, period: str = "current") -> ExtractedValue:
    return ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                          basis=Basis.CONSOLIDATED, period_label=period,
                          provenance=_prov(page, y, period))


def _face(key: str, amount: str, note: str | None, y: float = 0.5,
          prior: str | None = None) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key)
    li.set_value(_ev(amount, FACE, y))
    if prior is not None:
        li.set_value(_ev(prior, FACE, y, "prior"))
    if note:
        li.note_refs = [NoteRef(raw=note, numbers=[note])]
    return li


def _filled(key: str, amount: str, note: str, y: float = 0.5,
            prior: str | None = None) -> LineItem:
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
    table = NotesTable(note_number=number, title=f"note {number}", source_pages=[NOTES])
    for ordinal, (caption, figure, y, role) in enumerate(rows):
        item = NoteItem(raw_label=caption, ordinal=ordinal, role=role)
        item.set_value(_ev(figure, NOTES, y))
        table.items.append(item)
    return table


def _item(key, section, parent="", in_output=True):
    return SimpleNamespace(key=key, section_scope=[section], parent=parent, in_output=in_output)


ITEMS = [_item("bs_ca__other_receivables_cp", "bs_ca"), _item("bs_ca__other_current_assets", "bs_ca"),
         _item("bs_nca__secur_and_other_fincl_assets_ltp", "bs_nca"),
         _item("sub__cp_face_trading_fincl_assets", "bs_ca", parent="bs_ca__secur_cp",
               in_output=False),
         _item("bs_ca__secur_cp", "bs_ca"), _item("bs_ca__finished_goods", "bs_ca")]
LTP, FVTPL_CP = "bs_nca__secur_and_other_fincl_assets_ltp", "sub__cp_face_trading_fincl_assets"


def _settle(rows, notes=()):
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = rows
    doc.notes = list(notes)
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[SimpleNamespace(
        canonical_key="bs_ca__other_current_assets", value_scope="exclusive_residual")])
    _settle_against_the_face(doc, ctx, ITEMS)
    got = {li.canonical_key: {ev.period_label: str(ev.value) for ev in li.values.values()}
           for li in doc.line_items}
    return got, ctx


def test_one_year_of_agreement_with_an_unrelated_note_settles_nothing():
    """THE PROBE. LTP read note 26's 344,135; a face row citing note 9 prints 344,135 in the one
    year it prints. It used to settle: LTP published 0. Nothing ties the two but the amount."""
    got, ctx = _settle([_face(FVTPL_CP, "344135", "9"), _filled(LTP, "344135", "26")])

    assert got[LTP] == {"current": "344135"}
    assert any(f"{LTP}[consolidated:current]" in line and "in one year only" in line
               for line in ctx.logs), ctx.logs


def test_a_face_row_printing_one_year_cannot_confirm_a_line_that_prints_two():
    """The line prints both years and the face row only one: only one year can be compared, so
    the same rule applies — and the prior-year figure the face row does not print is no evidence."""
    got, _ctx = _settle([_face(FVTPL_CP, "344135", "9"),
                         _filled(LTP, "344135", "26", prior="2000")])

    assert got[LTP] == {"current": "344135", "prior": "2000"}


def test_one_year_settles_when_the_face_row_cites_the_note_the_line_read():
    """1966's LTP read note 26's 344,135, which the face prints as the current FVTPL row citing
    note 26: the same money in the wrong column, settled on one year because the citation ties it."""
    got, _ctx = _settle([_face(FVTPL_CP, "344135", "26"), _filled(LTP, "344135", "26")])

    assert got[LTP] == {"current": "0"}


def test_a_qualified_citation_names_the_bare_note_the_line_read():
    """A mainland face cites 七、9; the line's trail names the same note as 9."""
    got, _ctx = _settle([_face(FVTPL_CP, "344135", "七、9"), _filled(LTP, "344135", "9")])

    assert got[LTP] == {"current": "0"}


def test_one_year_settles_through_the_notes_own_total():
    """China SCE 1966: the face prints note 24's total 15,062,723 — here citing no note at all —
    and Other Receivables (CP) read 5,818,375 inside it. Containment through the note's own total
    is the tie, so the residual gives the money up on one year."""
    note_24 = _note("24", [("Prepayments", "9244348", 0.40, LineRole.LINE),
                           ("Other receivables", "5818375", 0.50, LineRole.LINE),
                           ("Total", "15062723", 0.60, LineRole.TOTAL)])
    got, _ctx = _settle([_face("bs_ca__other_current_assets", "15062723", None),
                         _filled("bs_ca__other_receivables_cp", "5818375", "24")], [note_24])

    assert got == {"bs_ca__other_current_assets": {"current": "9244348"},
                   "bs_ca__other_receivables_cp": {"current": "5818375"}}


def test_a_citation_does_not_overrule_a_prior_year_that_disagrees():
    """Both years printed and the prior year differs: not the same money, whatever the face row
    cites."""
    got, _ctx = _settle([_face(FVTPL_CP, "344135", "26", prior="1000"),
                         _filled(LTP, "344135", "26", prior="2000")])

    assert got[LTP] == {"current": "344135", "prior": "2000"}


def test_two_years_of_agreement_settle_whatever_the_notes():
    """THE SECOND PROBE, pinned as the rule decides it. Other receivables read 500 / 400 from note
    12; finished goods, citing note 9, prints 500 / 400. Both years agree, which the rule takes as
    the same money: the line's calculation runs without it and publishes 0 / 0, with the deduction
    in its trail and the flag on the row. A note link is asked for only where one year can be
    compared."""
    rows = [_face("bs_ca__finished_goods", "500", "9", prior="400"),
            _filled("bs_ca__other_receivables_cp", "500", "12", prior="400")]
    got, _ctx = _settle(rows)

    assert got["bs_ca__other_receivables_cp"] == {"current": "0", "prior": "0"}
    assert got["bs_ca__finished_goods"] == {"current": "500", "prior": "400"}
    assert "counted_on_the_face:bs_ca__finished_goods:500" in rows[1].confidence.flags


# ── 2. the model route: a parent and its part share a printed row ─────────────────────────────

class _Ctx:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, message: str) -> None:
        self.logs.append(message)


def _claimant(key: str, amount: str) -> LineItem:
    row = LineItem(source_label=key, canonical_key=key)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal(amount), value_raw=Decimal(amount)))
    return row


PARENT_TAX, PART_TAX = "cf_oper_direct__income_taxes_paid_direct", "sub__cf_direct_income_taxes_paid"


def _share(keys, shipped):
    from app.stages.line_item_llm import LineItemLlmStage

    printed = LineItem(source_label="Income taxes paid", canonical_key=PARENT_TAX)
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [printed]
    rid = str(printed.id)
    rows = {k: _claimant(k, "-500") for k in keys}
    ctx = _Ctx()
    n = LineItemLlmStage._settle_shared_printed_rows(
        doc, {rid: list(keys)}, {k: [rid] for k in keys}, rows, ctx, shipped)
    return n, rows, ctx


def test_a_parent_and_its_part_both_keep_the_row_they_cite(shipped):
    """cf_oper_direct__income_taxes_paid_direct and its part sub__cf_direct_income_taxes_paid
    cite the one printed "Income taxes paid". The part is inside the parent, so the row is counted
    once; before, the part was emptied and the parent's breakdown lost it."""
    n, rows, ctx = _share([PARENT_TAX, PART_TAX], shipped)

    assert n == 0
    assert all(rows[k].values for k in (PARENT_TAX, PART_TAX))
    assert not any("claimed_by" in f for k in rows for f in rows[k].confidence.flags)
    assert any(f"line_item_llm:{PART_TAX}: shares the printed row" in line
               and "its parent" in line for line in ctx.logs), ctx.logs


def test_an_unrelated_claimant_is_still_emptied(shipped):
    """A third line citing the same row is no family of either: it is emptied, as before."""
    other = "cf_financing__interest_paid_fin"
    n, rows, _ctx = _share([PARENT_TAX, PART_TAX, other], shipped)

    assert n == 1
    assert not rows[other].values
    assert f"llm_printed_row_claimed_by:{PARENT_TAX}" in rows[other].confidence.flags
    assert rows[PARENT_TAX].values and rows[PART_TAX].values


# ── 3. a shared figure is removed only on a confident answer ──────────────────────────────────

class _Provider:
    def __init__(self, keep: str, confidence: float) -> None:
        self.keep, self.confidence = keep, confidence

    def complete_structured(self, **_kw):
        from app.services.shared_figures import SharedFigureDecision
        return SharedFigureDecision(keep=self.keep, rationale="because",
                                    confidence=self.confidence), {}


def _contest(monkeypatch, provider, floor=None):
    from app.ports.registry import registry
    from app.stages.shared_figures import SharedFiguresStage

    settings = get_settings()
    monkeypatch.setattr(settings.llm, "provider", "test-provider", raising=False)
    if floor is not None:
        monkeypatch.setattr(settings.extraction, "llm_shared_figure_min_confidence", floor)
    monkeypatch.setattr(registry, "get", lambda kind, name: provider, raising=False)

    def line(key):
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("366943014.10"),
                                    provenance=Provenance(page_index=7, bbox=BBox(
                                        x0=0.6, y0=0.3, x1=0.7, y1=0.31))))
        return li

    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [line("ppe"), line("fixed")]
    ctx = _Ctx()
    ctx.locale = "en"
    ctx.line_items = SimpleNamespace(items=[SimpleNamespace(key=k, parent=p, definition="")
                                            for k, p in (("ppe", "deprec"), ("fixed", "deprec"),
                                                         ("deprec", ""))])
    SharedFiguresStage().run(doc, ctx)
    return doc, ctx


def test_the_default_floor_is_point_six():
    assert get_settings().extraction.llm_shared_figure_min_confidence == 0.6


def test_an_answer_below_the_floor_removes_nothing(monkeypatch):
    """"Keep it on fixed" at confidence 0.4: before, the figure was removed from ppe. Now the
    contest is left for review exactly as with no provider, and the proposal is recorded."""
    doc, ctx = _contest(monkeypatch, _Provider("fixed", 0.4))

    assert all(len(li.values) == 1 for li in doc.line_items)
    assert all(any(f.startswith("shared_figure_unresolved:") for f in li.confidence.flags)
               for li in doc.line_items)
    assert not any(f.startswith("shared_figure_removed") for li in doc.line_items
                   for f in li.confidence.flags)
    (record,) = doc.shared_figures
    assert (record["keep"], record["proposed"], record["confidence"]) == ("", "fixed", 0.4)
    assert any("below 0.60" in line and "nothing removed" in line for line in ctx.logs)


def test_an_answer_at_the_floor_removes_the_figure(monkeypatch):
    doc, _ctx = _contest(monkeypatch, _Provider("fixed", 0.6))

    assert {li.canonical_key: len(li.values) for li in doc.line_items} == {"ppe": 0, "fixed": 1}
    assert doc.shared_figures[0]["keep"] == "fixed"


def test_the_floor_is_a_setting(monkeypatch):
    doc, _ctx = _contest(monkeypatch, _Provider("fixed", 0.4), floor=0.3)

    assert {li.canonical_key: len(li.values) for li in doc.line_items} == {"ppe": 0, "fixed": 1}


def test_keep_on_both_below_the_floor_is_unresolved_too(monkeypatch):
    """An unsure "both" is not a decision either: the figures stay, as they would, and the contest
    reads as unresolved rather than as considered."""
    doc, _ctx = _contest(monkeypatch, _Provider("both", 0.2))

    assert all(len(li.values) == 1 for li in doc.line_items)
    assert all(any(f.startswith("shared_figure_unresolved:") for f in li.confidence.flags)
               and not any(f.startswith("shared_figure_kept_on_both") for f in
                           li.confidence.flags)
               for li in doc.line_items)


# ── 4. basis eligibility ──────────────────────────────────────────────────────────────────────

_PART = "sub__cl_contingency_note_exposures"
_LINE = "notes__contingent_liabilities"


def _contingent_note(basis_from: str) -> NotesTable:
    """Note 28, the company's bank guarantees 1,120 / 980, on a note whose basis came from
    `basis_from`."""
    note = NotesTable(note_number="28", title="Contingent liabilities", basis=Basis.STANDALONE,
                      basis_from=basis_from, source_pages=[1])
    row = NoteItem(raw_label="Bank guarantees")
    for period, amount in (("current", "1120"), ("prior", "980")):
        row.set_value(ExtractedValue(basis=Basis.STANDALONE, period_label=period,
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=1)))
    note.items.append(row)
    return note


def _group_only_filing(shipped, notes, extra_rows=()):
    """A filing whose statements print the group's only; the face cites note 28."""
    face = LineItem(source_label="Cash", canonical_key="bs_ca__cash")
    for period, amount in (("current", "300"), ("prior", "250")):
        face.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                      value=Decimal(amount), value_raw=Decimal(amount),
                                      provenance=Provenance(page_index=88)))
    face.note_refs = [NoteRef(raw="28", numbers=["28"])]
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, scope="consolidated",
                            statement="balance_sheet"),
                 PageSource(index=1, kind=PageKind.NOTES)]
    doc.notes = list(notes)
    doc.line_items = [face, *extra_rows]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx), ctx


def _slots(doc, key):
    return {(ev.basis.value, ev.period_label): ev.value
            for li in doc.line_items if li.canonical_key == key
            for ev in li.values.values() if ev.value is not None}


def _flags(doc, key):
    return [f for li in doc.line_items if li.canonical_key == key for f in li.confidence.flags]


def test_contingent_liabilities_and_its_parts_are_disclosures_and_a_pl_part_is_not(shipped):
    defs = {i.key: i for i in shipped.items}

    assert is_disclosure(_LINE, defs) and is_disclosure(_PART, defs)
    assert not is_disclosure("sub__ppe_depreciation", defs), \
        "read from a note, but a part of a profit-and-loss line"
    assert not is_disclosure("bs_ca__cash", defs)


@pytest.mark.parametrize("basis_from", ["chapter", "band", "carried"])
def test_a_company_note_on_a_group_only_filing_fills_the_disclosure(shipped, basis_from):
    """THE PROBE. Before, the part and the line were both empty, flagged basis_not_printed and
    logged REFUSED 1120. The note says it is the company's, so the disclosure is the company's."""
    doc, ctx = _group_only_filing(shipped, [_contingent_note(basis_from)])

    want = {("standalone", "current"): Decimal("1120"), ("standalone", "prior"): Decimal("980")}
    assert _slots(doc, _PART) == want
    assert _slots(doc, _LINE) == want
    for key in (_PART, _LINE):
        assert {f"{ACCEPTED_FLAG}:{basis_from}:standalone:current",
                f"{ACCEPTED_FLAG}:{basis_from}:standalone:prior"} <= set(_flags(doc, key))
        assert not any(f.startswith(REFUSED_FLAG) for f in _flags(doc, key))
    assert any(line.startswith(f"note_sourced:{_PART}[standalone:current] ACCEPTED 1120")
               for line in ctx.logs), ctx.logs
    assert any(line.startswith("note_sourced:basis_accepted_from_note=") for line in ctx.logs)


@pytest.mark.parametrize("basis_from", ["default", ""])
def test_a_default_reading_is_still_refused(shipped, basis_from):
    """A basis the note does not print — the face's one basis read onto it, or no record of
    where it came from — is no evidence, and the refusal stands."""
    doc, ctx = _group_only_filing(shipped, [_contingent_note(basis_from)])

    assert _slots(doc, _PART) == {} and _slots(doc, _LINE) == {}
    assert f"{REFUSED_FLAG}:standalone:current" in _flags(doc, _PART)
    assert any(line.startswith(f"note_sourced:{_PART}[standalone:current] REFUSED 1120")
               for line in ctx.logs), ctx.logs


def test_a_statement_line_keeps_the_refusal_on_an_explicit_note(shipped):
    """A 母公司 note's depreciation on a group-only filing: sub__ppe_depreciation feeds a
    profit-and-loss line, so a company figure still has no statement to sit on."""
    note = NotesTable(note_number="八、1", title="物业、厂房及设备", basis=Basis.STANDALONE,
                      basis_from="chapter", source_pages=[1])
    row = NoteItem(raw_label="本期折旧")
    row.set_value(ExtractedValue(basis=Basis.STANDALONE, period_label="current",
                                 value=Decimal("77"), value_raw=Decimal("77"),
                                 provenance=Provenance(page_index=1)))
    note.items.append(row)
    doc, ctx = _group_only_filing(shipped, [note])

    assert not any(ev.basis == Basis.STANDALONE for li in doc.line_items
                   if li.canonical_key != "bs_ca__cash" for ev in li.values.values()
                   if ev.value is not None)
    assert not any(" ACCEPTED " in line for line in ctx.logs)
    assert any(line.startswith("note_sourced:sub__ppe_depreciation[standalone:current] "
                               "REFUSED 77") for line in ctx.logs), ctx.logs


# ── 4b. the reader records why a note has its basis ───────────────────────────────────────────

def _w(text, x0, y0, width=0.06):
    from app.services.row_reconstruct import Word
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + 0.010))


def _lines(*lines):
    return [_w(tok, 0.06 + j * 0.2, 0.10 + i * 0.02)
            for i, line in enumerate(lines) for j, tok in enumerate(line)]


def _read(words, **kw):
    from app.services.notes_extract import extract_note_tables
    return [t for t in extract_note_tables(words, page_index=5, document_id=None,
                                           source_kind="native", **kw) if t.items]


def test_a_company_chapter_is_recorded():
    (table,) = _read(_lines(["1.", "应收账款"], ["账面余额", "1,000", "900"]),
                     chapter=["十九", 19, "母公司财务报表主要项目注释"])

    assert (table.basis, table.basis_from) == (Basis.STANDALONE, "chapter")


def test_a_continued_company_note_is_carried_and_a_continued_default_is_not():
    (carried,) = _read(_lines(["债务重组", "1,000", "900"]),
                       carry_note=("十八、1", "投资收益", Basis.STANDALONE, "chapter"))
    assert (carried.basis, carried.basis_from) == (Basis.STANDALONE, "carried")

    (again,) = _read(_lines(["债务重组", "1,000", "900"]),
                     carry_note=("十八、1", "投资收益", Basis.STANDALONE, "carried"))
    assert again.basis_from == "carried"

    (default,) = _read(_lines(["Bank loans", "500", "400"]),
                       carry_note=("5", "Borrowings", Basis.STANDALONE, "default"))
    assert (default.basis, default.basis_from) == (Basis.STANDALONE, "default")


def test_the_faces_one_basis_is_recorded_as_a_default():
    (table,) = _read(_lines(["3.", "Other financial assets"], ["Security deposits", "300", "270"]),
                     default_basis=Basis.STANDALONE)

    assert (table.basis, table.basis_from) == (Basis.STANDALONE, "default")


def test_a_group_and_company_band_is_recorded():
    """The HKEX house style: "Group" over one dated pair of columns, "Company" over the other."""
    words = [_w("12.", 0.06, 0.03, 0.03), _w("Trade", 0.10, 0.03),
             _w("receivables", 0.17, 0.03, 0.1),
             _w("Group", 0.58, 0.055), _w("Company", 0.80, 0.055),
             _w("2025", 0.55, 0.08), _w("2024", 0.67, 0.08), _w("2025", 0.79, 0.08),
             _w("2024", 0.91, 0.08)]
    for i, (label, cells) in enumerate((("Receivables", ("3,410", "2,900", "341", "290")),
                                        ("Allowance", ("(10)", "(8)", "(1)", "(1)")))):
        y = 0.12 + i * 0.04
        words.append(_w(label, 0.10, y, 0.10))
        words += [_w(v, x, y) for v, x in zip(cells, (0.55, 0.67, 0.79, 0.91))]
    (table,) = _read(words)

    assert (table.basis, table.basis_from) == (None, "band")


def test_a_note_that_says_nothing_records_nothing():
    (table,) = _read(_lines(["3.", "Other financial assets"], ["Security deposits", "300", "270"]))

    assert (table.basis, table.basis_from) == (None, "")


def test_the_pdf_walker_carries_why():
    import inspect

    from app.services import pdf_extract

    assert "tables[-1].basis_from" in inspect.getsource(pdf_extract)


def _model_cites_the_company_note(shipped, basis_from: str):
    """The model route: asked about the part, it cites note 28's bank guarantees."""
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage
    from tests.spy_line_item_llm import SpyLineItemLlm

    note = _contingent_note(basis_from)
    note.items[0].values = {}
    for period, amount in (("current", "1120"), ("prior", "980")):
        note.items[0].set_value(ExtractedValue(
            basis=Basis.STANDALONE, period_label=period, value=Decimal(amount),
            value_raw=Decimal(amount),
            provenance=Provenance(page_index=1, text_snippet="Bank guarantees",
                                  bbox=BBox(x0=0.6 if period == "current" else 0.8, y0=0.5,
                                            x1=0.7 if period == "current" else 0.9, y1=0.51))))
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, scope="consolidated",
                            statement="balance_sheet"),
                 PageSource(index=1, kind=PageKind.NOTES)]
    doc.notes = [note]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ex = ctx.settings.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), ctx.settings.llm.provider)
    try:
        ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, [_PART]
        spy = SpyLineItemLlm(only={_PART}, cite={_PART: ("28", "Bank guarantees")})
        ctx.registry.register("llm", "spy", lambda: spy)
        ctx.settings.llm.provider = "spy"
        LineItemLlmStage().run(doc, ctx)
    finally:
        ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ctx.settings.llm.provider = was
    return doc, ctx


def test_the_models_citation_of_a_company_note_fills_the_disclosure(shipped):
    doc, ctx = _model_cites_the_company_note(shipped, "chapter")

    assert ("standalone", "current") in _slots(doc, _PART), ctx.logs[-12:]
    assert f"{ACCEPTED_FLAG}:chapter:standalone:current" in _flags(doc, _PART)
    assert any(line.startswith(f"line_item_llm:{_PART}[standalone:current] ACCEPTED")
               for line in ctx.logs)


def test_the_models_citation_of_a_default_reading_is_refused(shipped):
    doc, ctx = _model_cites_the_company_note(shipped, "default")

    assert ("standalone", "current") not in _slots(doc, _PART)
    assert f"{REFUSED_FLAG}:standalone:current" in _flags(doc, _PART)
