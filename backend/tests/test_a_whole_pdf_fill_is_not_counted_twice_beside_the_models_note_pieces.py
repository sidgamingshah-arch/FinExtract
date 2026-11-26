"""A FACE ROW THE RULES FILL IS NOT COUNTED TWICE BESIDE THE NOTE PIECES THE MODEL GAVE FOR IT
(`services.whole_pdf_rules`: `settle_against_model_pieces`, the split face cell in `_fills`,
`review_beside_a_difference`).

On the whole-PDF route a slot the model left empty takes the rules' published figure. The model
can read a face figure AS ITS NOTE PIECES, though, and the fill then counted money twice or left a
total short. Measured with scripted answers on the reference filings, every figure as printed:

  A. A piece on its own line, the face line left empty: the face row was filled whole beside the
     piece. 000709 一年内到期的非流动负债 18,040,355,658.70 (p83) beside the model's CPLTD Bank
     9,936,568,335.00 (note 七、26, p150); China SCE's borrowings 9,817,976 (p105) beside its other
     loans 1,666,788 (note 30, p219).
  B. The face row whole from the model, and a piece the rules split out of that face cell filled
     beside it: 1966 finance costs (1,492,343) with the lease interest 1,490 (note 7, p184).
  C. The other way round: the lease interest on its carved line, the face line empty, and the
     rules' REMAINDER (1,490,853) filled — the carved 1,490 counted nowhere.
  D. The allowance from the model, the gross left empty: the CAS rules publish the face NET row on
     Trade Receivables(Gross), so the allowance came off twice. 688008 应收账款 387,791,885.96 (p150),
     allowance 1,944,451.99 (note 七、5, p184): the gross is the note's 账面余额 389,736,337.95.

Each case runs the REAL pipeline on the filing's own pages, rules-only and then whole-PDF with the
shipped stand-in answering from a SCRIPT built from that rules-only run (`scripts/llm_standin.py`),
changed as the case says. No model is called; a script's answers are the script's.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.services import whole_pdf_rules as WR
from tests.whole_pdf_standin import (
    HK_SET,
    HK_TEMPLATE,
    TEMPLATES,
    filing_path,
    rows_of,
    run_whole_pdf,
    script_figure,
    standin_module,
)

standin = standin_module()

HESTEEL = "000709_hesteel_2024_prc"
#: 0-based: the balance sheet (report pp.82-83), note 七、3 (p130), note 七、26 (p150).
HESTEEL_PAGES = [81, 82, 129, 149]
MONTAGE = "688008_montage_2024_prc"
#: 0-based: the balance sheets (pp.149-151), and the notes from the chapter heading 七、合并财务报表
#: 项目注释 (p182) to note 七、5 (pp.183-184) — the chapter is what numbers the note 七、5.
MONTAGE_PAGES = [148, 149, 150, 181, 182, 183]
CHINA_SCE = "hkex_1966_ar"
#: 0-based: the income statement (p102), the balance sheet (pp.104-105), note 7 (p184), note 30
#: (p219).
CHINA_SCE_PAGES = [101, 103, 104, 183, 218]
KAMING = "hkex_kaming_2025_26"
#: 0-based: the balance sheet (p64), note 20 (pp.125-126).
KAMING_PAGES = [63, 124, 125]
MAIJIE = "300319_maijie_2024_prc"
#: 0-based: the balance sheet (pp.98-99), the chapter heading 七 (p145), notes 七、22 to 七、24
#: (p160), the chapter heading 十一 and note 十一、2, the liquidity-risk table (pp.175-176).
MAIJIE_PAGES = [97, 98, 144, 159, 174, 175]

CPLTD_BANK, CPLTD_OTHER = "bs_cl__cpltd_bank", "bs_cl__cpltd_other"
ST_BANK, ST_OTHER = "bs_cl__st_bank_loans_payable", "bs_cl__st_other_loans_payable"
TCL, TCA = "bs_cl__total_current_liabilities", "bs_ca__total_current_assets"
GROSS, ALLOWANCE = "bs_ca__trade_receivables_gross", "bs_ca__allow_for_doubtful_accounts"
NET = "bs_ca__net_trade_receivables"
INTEREST, LEASE = "is_pl__interest_expense", "is_pl__interest_on_lease_liabilities"
NET_INTEREST = "is_pl__net_interest_income_expense"
TRADE_AND_OTHER, TRADE = "bs_cl__trade_and_other_payables_cp", "bs_cl__trade_payables_cp"
NOTES_PAYABLE = "bs_cl__notes_payable_cp"


def _present(stem):
    return pytest.mark.skipif(not filing_path(stem).exists(), reason=f"{stem}.pdf not present")


def _set():
    from app.schemas.line_items import load_line_item_set

    return load_line_item_set(json.loads((TEMPLATES / HK_SET).read_text(encoding="utf-8")),
                              resolve=True)


def _rules_only(mp, stem, pages):
    from app.config import get_settings
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    settings = get_settings()
    mp.setattr(settings.extraction, "whole_pdf_to_model", False)
    mp.setattr(settings.llm, "provider", "stub")
    cfg = _set()
    tpl = load_template(json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8")))
    doc, _ctx = run_extraction(filing_path(stem).read_bytes(), filename=f"{stem}.pdf",
                               ontology=build_working_view(cfg), template=tpl, line_items=cfg,
                               included_pages=set(pages))
    return doc


def _rows(doc) -> list[dict]:
    from app.api.routes.extractions import _serialize_rows

    return _serialize_rows(doc)


def _shown(doc, period="current", basis="consolidated") -> dict[str, Decimal]:
    """Every line AS THE GRID SHOWS IT (`services.rollups.figures_as_shown`)."""
    from app.services.rollups import figures_as_shown

    template = json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8"))
    return {k: Decimal(str(v)).quantize(Decimal("0.01"))
            for k, v in figures_as_shown(template, _rows(doc), basis, period).items()}


def _filled(doc, key, period="current", basis="consolidated"):
    """`(row, figure)` of the rules-filled row of `key` holding the slot, or (None, None)."""
    for row in rows_of(doc, key):
        if WR.is_model_row(row) or WR.RULES_FLAG not in row.confidence.flags:
            continue
        for ev in row.values.values():
            if (str(getattr(ev.basis, "value", ev.basis)) == basis and ev.period_label == period
                    and ev.value is not None):
                return row, ev
    return None, None


def _model(doc, key):
    return [row for row in rows_of(doc, key) if WR.is_model_row(row)]


def _parts_tell_their_pages(mp):
    """THE STAND-IN IS TOLD WHICH PAGES EACH PART HOLDS from the parts the stage cut
    (`services.whole_pdf.build_parts`), instead of matching each page's text to the filing's: on
    China SCE that match misses the income statement (p102), so a script's answer on it was never
    given. A harness change only — what the stage sends and does is unchanged."""
    import hashlib

    from app.services import whole_pdf as W

    held: dict[str, list[int]] = {}
    build = W.build_parts

    def build_parts(*args, **kwargs):
        parts, dropped = build(*args, **kwargs)
        for part in parts:
            held[hashlib.sha1(part.pdf).hexdigest()] = list(part.pages)
        return parts, dropped

    of = standin.FilingPages.of
    mp.setattr(W, "build_parts", build_parts)
    mp.setattr(standin.FilingPages, "of",
               lambda self, part: held.get(hashlib.sha1(part).hexdigest()) or of(self, part))


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """One run per case, and one rules-only run per filing, cached for the module."""
    done: dict[str, tuple] = {}

    def rules(stem, pages):
        name = f"rules:{stem}"
        if name not in done:
            with pytest.MonkeyPatch.context() as mp:
                done[name] = _rules_only(mp, stem, pages)
        return done[name]

    def _run(name, stem, pages, *, leave_out=(), add=None, patch=None):
        if name not in done:
            rules_doc = rules(stem, pages)
            script = standin.whole_pdf_script_from_result({"rows": _rows(rules_doc)}, _set(),
                                                          leave_out=leave_out)
            script["lines"].update(add or {})
            with pytest.MonkeyPatch.context() as mp:
                if patch is not None:
                    patch(mp)
                done[name] = run_whole_pdf(mp, tmp_path_factory.mktemp(name.replace(":", "_")),
                                           stem, script, pages=pages)
        return done[name]

    _run.rules = rules
    return _run


# ── A: a piece on its own line, the face line left empty ───────────────────────────────────────

def _hesteel(runs):
    return runs("hesteel_pieces", HESTEEL, HESTEEL_PAGES, leave_out=[CPLTD_OTHER, GROSS], add={
        CPLTD_BANK: [script_figure("9936568335.00", page=150, caption="一年内到期的长期借款"),
                     script_figure("8050612800.00", page=150, caption="一年内到期的长期借款",
                                   period="prior")],
        ALLOWANCE: [script_figure("1394836830.02", page=130, caption="坏账准备"),
                    script_figure("1445376980.12", page=130, caption="坏账准备", period="prior")]})


@_present(HESTEEL)
def test_a_the_face_row_less_the_models_piece_from_its_own_note(runs):
    """000709: the model gives CPLTD Bank from note 七、26 and leaves CPLTD Other — the line the rules
    put 一年内到期的非流动负债 on — empty. CPLTD Other is the face row LESS the piece, flagged and sent
    to review, and total current liabilities is what the rules route shows."""
    doc, ctx, _rec = _hesteel(runs)
    rules_doc = runs.rules(HESTEEL, HESTEEL_PAGES)
    for period, face, piece, rest in (
            ("current", "18040355658.70", "9936568335.00", "8103787323.70"),
            ("prior", "15411565079.19", "8050612800.00", "7360952279.19")):
        row, ev = _filled(doc, CPLTD_OTHER, period)
        assert ev is not None, period
        assert ev.value == Decimal(rest), period
        assert ev.value_raw == Decimal(face)                 # what the face prints, kept
        assert f"{WR.LESS_MODEL_FLAG}:{CPLTD_BANK}={piece}" in ev.confidence.flags
        assert WR.REVIEW_FLAG in ev.confidence.flags and WR.REVIEW_FLAG in row.confidence.flags
        assert _shown(doc, period)[TCL] == _shown(rules_doc, period)[TCL], period
    assert any(ln.startswith(f"whole_pdf:rules_less_model:{CPLTD_OTHER}[consolidated:current]")
               and "= 8103787323.70" in ln for ln in ctx.logs)


def _china_sce(runs, name, **kw):
    return runs(name, CHINA_SCE, CHINA_SCE_PAGES, patch=_parts_tell_their_pages, **kw)


@_present(CHINA_SCE)
def test_a_on_an_hkfrs_filing_borrowings_less_the_other_loans(runs):
    """China SCE (HKFRS, RMB'000): the model gives ST Other Loans from note 30 — the note the
    face row 'Interest-bearing bank and other borrowings' cites — and leaves ST Bank Loans empty.
    The bank part is the face row less the other loans: 8,151,188 / 10,089,294."""
    doc, _ctx, _rec = _china_sce(runs, "sce_other_loans", leave_out=[ST_BANK], add={
        ST_OTHER: [script_figure("1666788", page=219, caption="Other loans — secured 其他貸款"),
                   script_figure("653665", page=219, caption="Other loans — secured 其他貸款",
                                 period="prior")]})
    rules_doc = runs.rules(CHINA_SCE, CHINA_SCE_PAGES)
    for period, rest in (("current", "8151188"), ("prior", "10089294")):
        _row, ev = _filled(doc, ST_BANK, period)
        assert ev is not None and ev.value == Decimal(rest), period
        assert _shown(doc, period)[TCL] == _shown(rules_doc, period)[TCL], period


@_present(CHINA_SCE)
def test_pieces_that_reach_the_face_figure_leave_the_slot_empty_and_go_to_review(runs):
    """The model gives ST Other Loans the WHOLE face figure (its note's current total): there is
    nothing left for the bank part, so the slot is not filled, and the model's figure goes to
    review — never a zero or a negative figure on the face line."""
    doc, ctx, _rec = _china_sce(runs, "sce_whole_on_other", leave_out=[ST_BANK], add={
        ST_OTHER: [script_figure("9817976", page=219, caption="Total — current 流動總額")]})
    _row, ev = _filled(doc, ST_BANK, "current")
    assert ev is None
    (model,) = _model(doc, ST_OTHER)
    assert f"{WR.PIECES_REACH_FLAG}:{ST_BANK}" in model.confidence.flags
    assert WR.REVIEW_FLAG in model.confidence.flags
    assert any(ln.startswith(f"whole_pdf:rules_less_model:{ST_BANK}[consolidated:current]")
               and "NOT filled" in ln for ln in ctx.logs)
    # The prior year has no piece: its face row is filled whole.
    _row, ev = _filled(doc, ST_BANK, "prior")
    assert ev is not None and ev.value == Decimal(10742959)


# ── B and C: one face cell the rules split, filled as one unit ─────────────────────────────────

LEASE_FIGURES = [script_figure("1490", page=184, caption="Interest on lease liabilities"),
                 script_figure("20162", page=184, caption="Interest on lease liabilities",
                               period="prior")]


@_present(CHINA_SCE)
def test_b_a_piece_split_from_the_face_cell_is_not_filled_beside_the_models_whole(runs):
    """The model gives finance costs whole off the face (p102) and leaves the lease interest out.
    The rules split 1,490 / 20,162 of lease interest out of that face cell; it is not filled beside
    the model's whole, and net interest is the printed (1,492,343) / (921,124)."""
    rules_doc = runs.rules(CHINA_SCE, CHINA_SCE_PAGES)
    assert any(f.startswith("split_from:" + INTEREST) for r in rows_of(rules_doc, LEASE)
               for f in r.confidence.flags)
    doc, ctx, _rec = _china_sce(runs, "sce_face_whole", leave_out=[LEASE], add={
        INTEREST: [script_figure("-1492343", page=102, caption="Finance costs 財務費用"),
                   script_figure("-921124", page=102, caption="Finance costs 財務費用",
                                 period="prior")]})
    assert not [r for r in rows_of(doc, LEASE) if WR.RULES_FLAG in r.confidence.flags
                and not WR.is_model_row(r)]
    assert any(ln.startswith(f"whole_pdf:rules_fill:{LEASE}[consolidated:current]")
               and "the model holds " + INTEREST in ln for ln in ctx.logs)
    assert ctx.whole_pdf["filled_from_rules"]["split_inside_the_models_whole"] == 2
    for period, printed in (("current", "-1492343"), ("prior", "-921124")):
        assert _shown(doc, period)[NET_INTEREST] == Decimal(printed), period


@_present(CHINA_SCE)
def test_c_the_face_cell_whole_where_the_model_holds_a_carved_piece(runs):
    """The model gives the lease interest on its carved line (note 7, p184) and leaves finance
    costs out. The line takes the face cell's whole — the rules' remainder 1,490,853 plus the
    carved 1,490 — on the face cell itself, not the remainder: net interest is the printed
    (1,492,343) / (921,124), not 1,490 / 20,162 short."""
    doc, ctx, _rec = _china_sce(runs, "sce_lease_only", leave_out=[INTEREST],
                                add={LEASE: LEASE_FIGURES})
    for period, printed, piece in (("current", "-1492343", "-1490"),
                                   ("prior", "-921124", "-20162")):
        _row, ev = _filled(doc, INTEREST, period)
        assert ev is not None and ev.value == Decimal(printed), period
        assert ev.provenance.page_index == 101                 # the face cell, p102
        assert f"{WR.SPLIT_WHOLE_FLAG}:{LEASE}={piece}" in ev.confidence.flags
        assert _shown(doc, period)[NET_INTEREST] == Decimal(printed), period
    assert ctx.whole_pdf["filled_from_rules"]["split_filled_whole"] == 2


@_present(CHINA_SCE)
def test_c_per_year_the_models_whole_one_year_the_face_cell_whole_the_other(runs):
    """The model gives finance costs whole for the current year only, and the lease interest
    (carved) for both. The current year is the model's; the prior year takes the face cell's whole
    (921,124), not the remainder 900,962."""
    doc, _ctx, _rec = _china_sce(runs, "sce_one_year", leave_out=[INTEREST], add={
        INTEREST: [script_figure("-1492343", page=102, caption="Finance costs 財務費用")],
        LEASE: LEASE_FIGURES})
    assert _filled(doc, INTEREST, "current") == (None, None)
    _row, ev = _filled(doc, INTEREST, "prior")
    assert ev is not None and ev.value == Decimal(-921124)
    for period, printed in (("current", "-1492343"), ("prior", "-921124")):
        assert _shown(doc, period)[NET_INTEREST] == Decimal(printed), period


# ── D: the allowance as the piece ──────────────────────────────────────────────────────────────

def _montage(runs, name="montage_allowance", patch=None):
    return runs(name, MONTAGE, MONTAGE_PAGES, leave_out=[GROSS], patch=patch, add={
        ALLOWANCE: [script_figure("1944451.99", page=184, caption="坏账准备"),
                    script_figure("1474643.00", page=184, caption="坏账准备", period="prior")]})


@_present(MONTAGE)
def test_d_the_gross_is_the_face_net_plus_the_models_allowance(runs):
    """688008: the model gives the allowance (note 七、5) and leaves the gross out. The CAS rules
    publish the face's NET 应收账款 on Trade Receivables(Gross); counted negative, the allowance
    makes the gross the net plus the allowance — the note's 账面余额 — and the net trade receivables
    and current assets are the printed figures."""
    doc, _ctx, _rec = _montage(runs)
    rules_doc = runs.rules(MONTAGE, MONTAGE_PAGES)
    for period, gross, net, allowance in (
            ("current", "389736337.95", "387791885.96", "-1944451.99"),
            ("prior", "295728366.40", "294253723.40", "-1474643.00")):
        _row, ev = _filled(doc, GROSS, period)
        assert ev is not None and ev.value == Decimal(gross), period
        assert f"{WR.LESS_MODEL_FLAG}:{ALLOWANCE}={allowance}" in ev.confidence.flags
        shown = _shown(doc, period)
        assert shown[NET] == Decimal(net), period
        assert shown[TCA] == _shown(rules_doc, period)[TCA], period


@_present(HESTEEL)
def test_d_on_hesteel_an_allowance_larger_than_the_net(runs):
    """000709: the allowance 1,394,836,830.02 is larger than the net 1,294,709,355.45. The gross is
    the note's 账面余额 2,689,546,185.47 / 2,595,130,766.28, not a negative net receivable."""
    doc, _ctx, _rec = _hesteel(runs)
    rules_doc = runs.rules(HESTEEL, HESTEEL_PAGES)
    for period, gross, net in (("current", "2689546185.47", "1294709355.45"),
                               ("prior", "2595130766.28", "1149753786.16")):
        _row, ev = _filled(doc, GROSS, period)
        assert ev is not None and ev.value == Decimal(gross), period
        shown = _shown(doc, period)
        assert shown[NET] == Decimal(net), period
        assert shown[TCA] == _shown(rules_doc, period)[TCA], period


# ── what was right stays right ─────────────────────────────────────────────────────────────────

@_present(KAMING)
def test_a_line_whose_own_cascade_takes_out_its_notes_part_is_not_settled_twice(runs):
    """佳明: the model gives Trade Payables(CP) from note 20 and leaves the combined Trade and other
    payables out. That line's own cascade takes the trade part out of the printed row
    (LESS_THE_TRADE_PART), so the fill leaves it to the cascade: 462,962 less 272,534 = 190,428 —
    once, not twice."""
    doc, ctx, _rec = runs("kaming_payables", KAMING, KAMING_PAGES, leave_out=[TRADE_AND_OTHER],
                          add={TRADE: [
                              script_figure("272534", page=125, caption="Trade payables 應付賬款"),
                              script_figure("157164", page=125, caption="Trade payables 應付賬款",
                                            period="prior")]})
    rules_doc = runs.rules(KAMING, KAMING_PAGES)
    assert any(ln.startswith(f"whole_pdf:rules_less_model:{TRADE_AND_OTHER}: not settled here")
               for ln in ctx.logs)
    for period, rest in (("current", "190428.00"), ("prior", "255449.00")):
        shown = _shown(doc, period)
        assert shown[TRADE_AND_OTHER] == Decimal(rest), period
        assert shown[TCL] == _shown(rules_doc, period)[TCL], period
    _row, ev = _filled(doc, TRADE_AND_OTHER, "current")
    assert not any(f.startswith(WR.LESS_MODEL_FLAG) for f in (ev.confidence.flags if ev else ()))


@_present(MONTAGE)
def test_pieces_on_the_lines_the_rules_use_leave_nothing_to_fill(runs):
    """688008, as the definitions ask: the gross (账面余额) and the allowance, both from note 七、5.
    Nothing is filled on either line, nothing is settled, and the net is the printed figure."""
    doc, ctx, _rec = runs("montage_both", MONTAGE, MONTAGE_PAGES, add={
        GROSS: [script_figure("389736337.95", page=184, caption="账面余额"),
                script_figure("295728366.40", page=184, caption="账面余额", period="prior")],
        ALLOWANCE: [script_figure("1944451.99", page=184, caption="坏账准备"),
                    script_figure("1474643.00", page=184, caption="坏账准备", period="prior")]})
    assert _filled(doc, GROSS, "current") == (None, None)
    assert not any(ln.startswith("whole_pdf:rules_less_model:") for ln in ctx.logs)
    assert _shown(doc)[NET] == Decimal("387791885.96")


@_present(MAIJIE)
def test_a_figure_from_a_table_of_every_face_row_is_not_a_piece_of_another(runs):
    """300319: the model gives Trade Payables(CP) 840,284,135.50 off note 十一、2 (p176), the
    liquidity-risk table that prints the carrying amount of every current liability — 应付票据
    587,870,376.36 too. 应付票据 is filled from the face, and the trade payables are no piece of it:
    no run of that table's rows adds up to it. It stays the face figure, and total current
    liabilities is the printed 1,939,045,818.42, as the rules route shows."""
    doc, ctx, _rec = runs("maijie_liquidity_table", MAIJIE, MAIJIE_PAGES,
                          leave_out=[NOTES_PAYABLE, TRADE], add={TRADE: [
                              script_figure("840284135.50", page=176, caption="应付账款"),
                              script_figure("669191429.96", page=176, caption="应付账款",
                                            period="prior")]})
    rules_doc = runs.rules(MAIJIE, MAIJIE_PAGES)
    for period, face in (("current", "587870376.36"), ("prior", "429531988.74")):
        _row, ev = _filled(doc, NOTES_PAYABLE, period)
        assert ev is not None and ev.value == Decimal(face), period
        assert not any(f.startswith(WR.LESS_MODEL_FLAG) for f in ev.confidence.flags), period
        assert _shown(doc, period)[TCL] == _shown(rules_doc, period)[TCL], period
    assert _shown(doc)[TCL] == Decimal("1939045818.42")
    assert not any(ln.startswith(f"whole_pdf:rules_less_model:{NOTES_PAYABLE}[")
                   for ln in ctx.logs)


# ── which note cell is a piece of a face figure: a block of its note that adds up to it ─────────

def _note(number: str, rows, page: int = 0):
    """A note table as the rules read it: `rows` is `[(caption, {column: amount as printed})]`, in
    printed order; each figure gets a box of its own."""
    from app.core.models.geometry import BBox, Provenance
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable

    table = NotesTable(note_number=number)
    for i, (caption, columns) in enumerate(rows):
        item = NoteItem(raw_label=caption, ordinal=i)
        for j, (column, amount) in enumerate(columns.items()):
            x, y = 0.4 + 0.15 * j, 0.1 + 0.03 * i
            item.set_value(ExtractedValue(
                basis="consolidated", period_label=column, value=Decimal(amount),
                value_raw=Decimal(amount),
                provenance=Provenance(page_index=page, bbox=BBox(x0=x, y0=y, x1=x + 0.1,
                                                                 y1=y + 0.02))))
        table.items.append(item)
    return table


def _blocks(*tables):
    from app.core.models import DocumentModel

    return WR._NoteBlocks(DocumentModel(notes=list(tables)))


def _cell(table, caption: str, column: str):
    from app.services.note_sourced import cell_of

    (item,) = [i for i in table.items if i.raw_label == caption]
    (ev,) = [v for v in item.values.values() if v.period_label == column]
    return cell_of(ev.provenance)


def test_a_cell_is_a_piece_where_a_run_of_its_column_or_its_row_adds_up_to_the_face_figure():
    """The cases the fill settles: 000709 七、26 (four rows to 18,040,355,658.70), China SCE note
    30 (bank plus other loans to the 'Total — current' 9,817,976), 688008 七、5 by row (账面余额
    389,736,337.95 | 坏账准备 1,944,451.99 | 账面价值 387,791,885.96)."""
    cpltd = _note("七、26", [("一年内到期的长期借款", {"current": "9936568335.00"}),
                            ("一年内到期的长期应付款", {"current": "4380567512.51"}),
                            ("一年内到期的租赁负债", {"current": "661683403.12"}),
                            ("一年内到期的应付债券", {"current": "3061536408.07"}),
                            ("合计", {"current": "18040355658.70"})])
    borrowings = _note("30", [("Bank loans — secured", {"current": "8151188"}),
                              ("Other loans — secured", {"current": "1666788"}),
                              ("Total — current", {"current": "9817976"}),
                              ("Bank loans — secured, non-current", {"current": "9577784"})],
                       page=1)
    receivables = _note("七、5", [("按组合计提坏账准备", {"gross": "389736337.95",
                                                    "allowance": "1944451.99",
                                                    "ratio": "0.50",
                                                    "net": "387791885.96"})], page=2)
    blocks = _blocks(cpltd, borrowings, receivables)
    assert blocks.adds_up(_cell(cpltd, "一年内到期的长期借款", "current"), "18040355658.70")
    assert blocks.adds_up(_cell(borrowings, "Other loans — secured", "current"), "9817976")
    # The note's own total is the face figure whole: a piece that reaches it.
    assert blocks.adds_up(_cell(borrowings, "Total — current", "current"), "9817976")
    assert blocks.adds_up(_cell(receivables, "按组合计提坏账准备", "allowance"), "387791885.96")
    assert not blocks.adds_up(_cell(borrowings, "Bank loans — secured, non-current", "current"),
                              "9817976")


def test_a_cell_of_a_table_of_every_face_row_or_of_a_sibling_sub_note_is_not_a_piece():
    """The cases the note number alone took as pieces, every figure as printed:

    * 300319 十一、2 (p176) prints every current liability's carrying amount: the trade payables
      840,284,135.50 are no piece of 应付票据 587,870,376.36, and neither 应付票据 nor the 一年内到期的
      非流动负债 8,930,482.75 is a piece of 短期借款 269,920,984.10;
    * 佳明 note 8, one table for 8(a), (b) and (c): the sales commission 82,132 / 272,102 of 8(c) is
      no piece of finance costs 8(a) (107,818) / (96,811) — the capitalised interest of 8(a) is;
    * China SCE note 29, cited by two face rows: 'Current portion of other payables and accruals'
      8,807,854 is no piece of contract liabilities 80,838,861."""
    liquidity = _note("十一、2", [("应付票据", {"current": "587870376.36"}),
                                ("应付账款", {"current": "840284135.50"}),
                                ("其他应付款", {"current": "21864179.13"}),
                                ("其他流动负债", {"current": "60487755.80"}),
                                ("一年内到期的非流动负债", {"current": "8930482.75"}),
                                ("合计", {"current": "1789357913.64"})])
    note8 = _note("8", [("(a) Finance costs Interest on bank and other loans",
                         {"current": "342417", "prior": "380771"}),
                        ("Less: Amount capitalised", {"current": "-234599", "prior": "-283960"}),
                        ("(b) Staff costs", {"current": "119088", "prior": "115150"}),
                        ("Contributions to defined contribution plans",
                         {"current": "2664", "prior": "2617"}),
                        ("Less: Amount capitalised (staff)", {"current": "-56917",
                                                              "prior": "-67590"}),
                        ("(c) Other items Cost of inventories", {"current": "691952",
                                                                 "prior": "489599"}),
                        ("Direct operating expenses", {"current": "117892", "prior": "120574"}),
                        ("Impairment loss reversal", {"prior": "-50"}),
                        ("Sales commission", {"current": "82132", "prior": "272102"}),
                        ("Write-down of inventories", {"current": "45354", "prior": "189817"}),
                        ("Written-off of property, plant and equipment", {"current": "2936"}),
                        ("Depreciation", {"current": "19729", "prior": "21258"}),
                        ("Auditors' remuneration", {"current": "1381", "prior": "1453"}),
                        ("other services", {"current": "25", "prior": "25"})], page=1)
    note29 = _note("29", [("Contract liabilities (a)", {"current": "80838861"}),
                          ("Deposits received", {"current": "1296947"}),
                          ("Accruals", {"current": "894570"}),
                          ("Advances from non-controlling shareholders", {"current": "2353077"}),
                          ("Other payables (c)", {"current": "4254914"}),
                          ("Lease liabilities", {"current": "14727"}),
                          ("Non-current portion — lease liabilities", {"current": "-6381"}),
                          ("Current portion", {"current": "89646715"}),
                          ("Contract liabilities", {"current": "80838861"}),
                          ("Current portion of other payables and accruals",
                           {"current": "8807854"}),
                          ("Total", {"current": "89646715"})], page=2)
    blocks = _blocks(liquidity, note8, note29)
    assert not blocks.adds_up(_cell(liquidity, "应付账款", "current"), "587870376.36")
    assert not blocks.adds_up(_cell(liquidity, "应付票据", "current"), "269920984.10")
    assert not blocks.adds_up(_cell(liquidity, "一年内到期的非流动负债", "current"), "269920984.10")
    assert not blocks.adds_up(_cell(note8, "Sales commission", "current"), "-107818")
    assert not blocks.adds_up(_cell(note8, "Sales commission", "prior"), "-96811")
    assert blocks.adds_up(_cell(note8, "Less: Amount capitalised", "current"), "-107818")
    assert not blocks.adds_up(_cell(note29, "Current portion of other payables and accruals",
                                    "current"), "80838861")
    # And the face row's own note still decides first: a cell of another note is no piece.
    from app.services.note_sourced import same_note

    cell = _cell(note8, "Less: Amount capitalised", "current")
    assert WR._a_piece(cell, {"8(a)", "8"}, Decimal(-107818), blocks, same_note)
    assert not WR._a_piece(cell, {"30"}, Decimal(-107818), blocks, same_note)


def test_a_line_is_measured_against_the_total_of_the_section_it_is_declared_in():
    """Other Reserves' residual rollup lists Interest Payable and Perpetual Bond; reached through
    it first, they were measured against equity's total, and never settled."""
    template = json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8"))
    totals = WR._section_totals(template)
    assert totals["bs_cl__interest_payable"] == TCL
    assert totals["bs_ncl__perpetual_bond"] == "bs_ncl__total_non_current_liabilities"
    assert totals[ST_BANK] == TCL and totals[GROSS] == TCA


# ── visible: a filled figure in a section that does not add up ─────────────────────────────────

@_present(MONTAGE)
def test_a_filled_figure_in_a_section_with_any_difference_goes_to_review(runs):
    """Without the settlement, the gross is filled with the face net and current assets come out
    1,944,451.99 short — inside the checks' 0.1%, so the section passes. The filled figure is now
    sent to review for the difference; the check's status does not move."""
    doc, ctx, _rec = _montage(runs, "montage_no_settlement", patch=lambda mp: mp.setattr(
        WR, "settle_against_model_pieces", lambda doc, ctx: {}))
    (res,) = [r for r in doc.structural.results if r.rule_id == "section_reconciliation:bs_ca"
              and r.scope_key == "consolidated/current"]
    assert res.status == "pass" and res.difference == Decimal("1944451.99")
    row, ev = _filled(doc, GROSS, "current")
    assert ev.value == Decimal("387791885.96")
    flag = f"{WR.SECTION_DIFFERS_FLAG}:bs_ca:consolidated:current:1944451.99"
    assert flag in ev.confidence.flags and WR.REVIEW_FLAG in row.confidence.flags
    assert any(ln.startswith("whole_pdf:rules_reading_review: section_reconciliation:bs_ca")
               for ln in ctx.logs)
    # With the settlement the section adds up, and nothing is flagged for it.
    settled, _ctx, _rec = _montage(runs)
    (res,) = [r for r in settled.structural.results if r.rule_id == "section_reconciliation:bs_ca"
              and r.scope_key == "consolidated/current"]
    assert res.difference == 0
    _row, ev = _filled(settled, GROSS, "current")
    assert not any(f.startswith(WR.SECTION_DIFFERS_FLAG) for f in ev.confidence.flags)


# ── no-op elsewhere ────────────────────────────────────────────────────────────────────────────

def test_the_settlement_and_the_review_are_no_ops_off_the_whole_pdf_route():
    from types import SimpleNamespace

    from app.core.models import DocumentModel
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.models.reports import RuleResult, StructuralReport

    doc = DocumentModel()
    row = LineItem(source_label="短期借款", canonical_key=ST_BANK)
    row.set_value(ExtractedValue(basis="consolidated", period_label="current",
                                 value=Decimal(100), value_raw=Decimal(100)))
    doc.line_items.append(row)
    ctx = SimpleNamespace(log=lambda _m: None, template=None, line_items=None)
    assert WR.settle_against_model_pieces(doc, ctx) == {"slots_less_model": 0,
                                                        "slots_left_empty": 0}
    report = StructuralReport(results=[RuleResult(
        rule_id="section_reconciliation:bs_cl", kind="section_reconciliation",
        scope_key="consolidated/current", status="pass", difference=Decimal(5),
        details={"basis": "consolidated", "period_label": "current", "components": [ST_BANK],
                 "section": "bs_cl"})])
    assert WR.review_beside_a_difference(doc, report, ctx) == 0
    assert row.confidence.flags == [] and row.values[next(iter(row.values))].value == 100
