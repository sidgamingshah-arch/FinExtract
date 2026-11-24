"""A LINE THE MODEL SKIPS IS FILLED FROM THE RULES (`services.whole_pdf_rules.fill_from_rules`).

On the whole-PDF route the rules' face rows were only set aside, never mapped, and only the note
parts were filled back from the rules. So a face line the model left out — or whose request failed —
stayed BLANK where the rules had read a figure, and so did Periods and Statement Date, which the
export reads off the column headings the rules' rows carry. Measured with SCRIPTED answers (not a
model's): 688008's Periods and Statement Date blank; 佳明's statements blank wherever the script gave
nothing (115 of the 171 slots the rules publish).

Now every slot of an asked line the model left empty takes the RULES' PUBLISHED FIGURE — the
set-aside rows put through the rules' own mapping and signs — on a row of its own, flagged
`whole_pdf:rules_reading`. A slot any model row holds stays the model's; a printed figure the model
placed on a line is not read again; a slot the checks refused the model's figure for is filled only
when the rules' figure passes the same checks.

Each case runs the REAL pipeline on the real filing's pages, rules-only and then whole-PDF with the
shipped stand-in answering from a SCRIPT built from that rules-only run
(`scripts/llm_standin.py whole_pdf_script_from_result`), minus the lines a case leaves out. No model
is called; a script's answers are the script's.
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

MONTAGE = "688008_montage_2024_prc"
#: 0-based: the balance sheets (149-153), the income statements (153-155), the cash-flow
#: statements (155-157).
MONTAGE_PAGES = list(range(148, 158))
KAMING = "hkex_kaming_2025_26"
#: 0-based: the statements, report pages 62-68.
KAMING_PAGES = list(range(61, 68))

INVENTORIES = "bs_ca__inventories"            # p150 存货 352,196,407.33 / 482,386,216.41
SELLING = "is_pl__selling_and_marketing_expenses"   # p154 销售费用 96,006,550.08 / 89,960,686.45
PPE_PAID = "cf_investing__purchase_ppe"       # p157 购建固定资产…支付的现金 379,121,899.19 (unsigned)
PREPAY = "bs_ca__prepayments_cp"              # p150 预付款项 4,394,165.14 / 3,132,626.35
OTHER_CA = "bs_ca__other_current_assets"      # p150 其他流动资产 85,986,107.30 / 65,624,870.99
PERIODS = "statement_setup_controls__periods"
STATEMENT_DATE = "statement_setup_controls__statement_date"


def _present(stem):
    return pytest.mark.skipif(not filing_path(stem).exists(), reason=f"{stem}.pdf not present")


def _set():
    from app.schemas.line_items import load_line_item_set

    return load_line_item_set(json.loads((TEMPLATES / HK_SET).read_text(encoding="utf-8")),
                              resolve=True)


def _statement_keys(statement: str) -> list[str]:
    from app.services import whole_pdf as W

    return [it.key for it in W.lines_to_ask(_set()) if W.statement_of(it) == statement]


def _rules_only(mp, stem, pages):
    """The rules route on these pages, as a run with no model key reads them: the whole-PDF switch
    off and the stub provider (the reference measurements' setting)."""
    from app.config import get_settings
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    settings = get_settings()
    mp.setattr(settings.extraction, "whole_pdf_to_model", False)
    mp.setattr(settings.llm, "provider", "stub")
    cfg = _set()
    tpl = load_template(json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8")))
    raw = filing_path(stem).read_bytes()
    doc, ctx = run_extraction(raw, filename=f"{stem}.pdf", ontology=build_working_view(cfg),
                              template=tpl, line_items=cfg, included_pages=set(pages))
    assert ctx.whole_pdf is None
    return doc, ctx


def _result(doc) -> dict:
    from app.api.routes.extractions import _serialize_rows

    return {"rows": _serialize_rows(doc)}


def _slots(doc) -> dict[tuple[str, str, str], Decimal]:
    """`{(key, basis, period): figure}` — the published figure of each slot, the sum over every row
    of the key, as the export's grid adds a line's carriers."""
    out: dict[tuple[str, str, str], Decimal] = {}
    for row in doc.line_items:
        if not row.canonical_key:
            continue
        for ev in (row.values or {}).values():
            if ev.value is None:
                continue
            slot = (row.canonical_key, str(getattr(ev.basis, "value", ev.basis)),
                    str(ev.period_label))
            out[slot] = out.get(slot, Decimal(0)) + Decimal(str(ev.value))
    return out


def _carriers(doc, key, basis, period):
    return [row for row in rows_of(doc, key)
            if any(str(getattr(ev.basis, "value", ev.basis)) == basis
                   and ev.period_label == period and ev.value is not None
                   for ev in row.values.values())]


def _setup(doc) -> dict[str, str | None]:
    """Periods and Statement Date as the export writes them."""
    from app.api.routes.extractions import _build_supplemental_rows, _serialize_rows

    template = json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8"))
    rows = _build_supplemental_rows(template_def=template, base_rows=_serialize_rows(doc),
                                    disclosures=[], entity_name="x", doc_model=doc, ctx=None,
                                    options={})
    out = {}
    for row in rows:
        if row["canonical_key"] in (PERIODS, STATEMENT_DATE):
            out[row["canonical_key"]] = row["values"][0]["value"] if row["values"] else None
    return out


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """One run per (filing, case), cached for the module."""
    done: dict[str, tuple] = {}

    def _run(name: str, stem: str, pages, script=None, *, leave_out=()):
        if name not in done:
            with pytest.MonkeyPatch.context() as mp:
                if script is None:
                    rules = runs_rules(stem, pages)
                    script = standin.whole_pdf_script_from_result(
                        _result(rules[0]), _set(), leave_out=leave_out)
                done[name] = run_whole_pdf(mp, tmp_path_factory.mktemp(name), stem, script,
                                           pages=pages)
        return done[name]

    def runs_rules(stem: str, pages):
        name = f"rules:{stem}"
        if name not in done:
            with pytest.MonkeyPatch.context() as mp:
                done[name] = _rules_only(mp, stem, pages)
        return done[name]

    _run.rules = runs_rules
    return _run


# ── face lines the model left out ──────────────────────────────────────────────────────────────

@_present(MONTAGE)
def test_a_balance_sheet_line_a_pnl_line_and_the_whole_cash_flow_left_out_take_the_rules_figures(
        runs):
    """The script answers every asked line as the rules read it, except a balance-sheet line, a P&L
    line and every cash-flow line: each of those slots now carries the rules-only figure, on a row
    of its own flagged `whole_pdf:rules_reading` — and every other slot is the model's, unchanged."""
    left_out = [INVENTORIES, SELLING, *_statement_keys("cash_flow")]
    doc, ctx, _rec = runs("montage_left_out", MONTAGE, MONTAGE_PAGES, leave_out=left_out)
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    assert ctx.mapping_strategy == "whole_pdf"
    rules, got = _slots(rules_doc), _slots(doc)

    # The figures the filing PRINTS (p150, p154, p157), as the rules publish them.
    assert rules[(INVENTORIES, "consolidated", "current")] == Decimal("352196407.33")
    assert rules[(INVENTORIES, "consolidated", "prior")] == Decimal("482386216.41")
    assert rules[(SELLING, "consolidated", "current")] == Decimal("96006550.08")
    assert rules[(PPE_PAID, "consolidated", "current")] == Decimal("-379121899.19")
    filled = [slot for slot in rules if slot[0] in left_out]
    assert len(filled) >= 20
    for slot in filled:
        assert got.get(slot) == rules[slot], slot
        # No model row carries it: the rules' rows do, and — for a line with a cascade — the
        # rows `note_sourced` writes from them, as on the rules route.
        assert not any(WR.is_model_row(row) for row in _carriers(doc, *slot)), slot
    from_rules = [slot for slot in filled
                  if any(WR.RULES_FLAG in row.confidence.flags for row in _carriers(doc, *slot))]
    assert len(from_rules) >= 20
    # The rules' sign: the CAS outflow block the rules negate, settled.
    (row,) = _carriers(doc, PPE_PAID, "consolidated", "current")
    ev = next(e for e in row.values.values() if e.period_label == "current")
    assert ev.value_raw == Decimal("379121899.19") and ev.sign_normalised
    assert WR.RULES_FLAG in ev.confidence.flags
    filled_log = [ln for ln in ctx.logs if ln.startswith("whole_pdf:rules_fill filled")]
    assert filled_log and f"filled {ctx.whole_pdf['filled_from_rules']['slots']} slots" \
        in filled_log[0]


@_present(MONTAGE)
def test_no_line_is_filled_twice_and_the_answered_lines_stay_the_models(runs):
    """No slot is carried by a model row and a rules row at once; each rules-filled slot has as many
    carriers as on the rules route; and no printed cell a model figure sits on is read again."""
    from app.services.note_sourced import cell_of

    left_out = [INVENTORIES, SELLING, *_statement_keys("cash_flow")]
    doc, _ctx, _rec = runs("montage_left_out", MONTAGE, MONTAGE_PAGES, leave_out=left_out)
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    model_cells = {cell_of(ev.provenance) for row in doc.line_items if WR.is_model_row(row)
                   for ev in row.values.values() if ev.value is not None} - {None}
    seen = {}
    for row in doc.line_items:
        for ev in row.values.values():
            if ev.value is None or not row.canonical_key:
                continue
            slot = (row.canonical_key, str(getattr(ev.basis, "value", ev.basis)), ev.period_label)
            seen.setdefault(slot, []).append(row)
    for slot, rows in seen.items():
        mine = [r for r in rows if WR.RULES_FLAG in r.confidence.flags
                and not r.is_computed and not WR.is_model_row(r)]
        if not mine:
            continue
        assert not any(WR.is_model_row(r) for r in rows), slot
        assert len(mine) == len([r for r in _carriers(rules_doc, *slot)
                                 if not r.is_computed]), slot
        for r in mine:
            ev = next(e for e in r.values.values() if e.period_label == slot[2]
                      and str(getattr(e.basis, "value", e.basis)) == slot[1])
            assert cell_of(ev.provenance) not in model_cells, slot
    # A line the model answered: every one of its slots is the model's figure.
    for slot in (("bs_ca__cash_in_hand_and_at_banks", "consolidated", "current"),
                 ("bs_ca__total_current_assets", "standalone", "prior")):
        assert all(WR.is_model_row(r) for r in _carriers(doc, *slot)), slot


@_present(MONTAGE)
def test_periods_and_statement_date_come_out_as_on_the_rules_route(runs):
    """The script gives no column heading, so the export's Periods and Statement Date were blank;
    each model figure now carries the heading the rules read for its year on its page."""
    doc, _ctx, _rec = runs("montage_left_out", MONTAGE, MONTAGE_PAGES,
                           leave_out=[INVENTORIES, SELLING, *_statement_keys("cash_flow")])
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    assert _setup(rules_doc) == {PERIODS: "2024 / 2023", STATEMENT_DATE: "2024"}
    assert _setup(doc) == _setup(rules_doc)
    model = [ev for row in doc.line_items if WR.is_model_row(row) for ev in row.values.values()]
    assert model and all(WR.PERIOD_FLAG in ev.confidence.flags for ev in model
                         if ev.period_label in ("current", "prior"))


# ── what the model answered stays the model's ──────────────────────────────────────────────────

def _disagreeing_script(rules_doc) -> dict:
    """The rules' own answers, except: inventories (current) a figure the page does not print and a
    heading of the model's own; Other Current Assets given the 预付款项 row, which the checks refuse
    (it is Prepayments' face row, and Prepayments is answered with it); and Prepayments (prior) left
    to the rules."""
    script = standin.whole_pdf_script_from_result(_result(rules_doc), _set())
    lines = script["lines"]
    lines[INVENTORIES] = [
        {**script_figure("352196400.00", page=150, caption="存货"), "period_end": "2024年12月31日"},
        script_figure("482386216.41", page=150, caption="存货", period="prior")]
    lines[OTHER_CA] = [script_figure("4394165.14", page=150, caption="预付款项")]
    lines[PREPAY] = [script_figure("4394165.14", page=150, caption="预付款项")]
    return script


@_present(MONTAGE)
def test_a_model_answer_that_disagrees_with_the_rules_is_kept(runs):
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    doc, _ctx, _rec = runs("montage_disagree", MONTAGE, MONTAGE_PAGES,
                          _disagreeing_script(rules_doc))
    (row,) = _carriers(doc, INVENTORIES, "consolidated", "current")
    assert WR.is_model_row(row)
    ev = next(e for e in row.values.values() if e.period_label == "current")
    assert ev.value == Decimal("352196400.00")          # not the rules' 352,196,407.33
    assert ev.period_display == "2024年12月31日"           # the model's heading is kept
    assert WR.PERIOD_FLAG not in ev.confidence.flags
    assert _slots(doc)[(INVENTORIES, "consolidated", "current")] == Decimal("352196400.00")


@_present(MONTAGE)
def test_a_slot_the_checks_refused_is_filled_only_with_a_rules_figure_that_passes_them(runs):
    """Other Current Assets was given 预付款项, Prepayments' own face row: the checks refuse it
    (face_row_of_another_line), and the slot is empty. The rules read 其他流动资产 for the line, a
    figure the same checks pass, so the slot takes it — and Prepayments keeps the model's figure,
    while its prior slot, which the script left out, takes the rules'."""
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    doc, ctx, _rec = runs("montage_disagree", MONTAGE, MONTAGE_PAGES,
                          _disagreeing_script(rules_doc))
    model = [r for r in rows_of(doc, OTHER_CA) if WR.is_model_row(r)]
    assert any(f.startswith("whole_pdf:refused:face_row_of_another_line:consolidated:current")
               for r in model for f in r.confidence.flags)
    got = _slots(doc)
    assert got[(OTHER_CA, "consolidated", "current")] == Decimal("85986107.30")
    assert all(not WR.is_model_row(r) for r in _carriers(doc, OTHER_CA, "consolidated", "current"))
    assert any("the checks refused the model's figure; the rules' 85986107.30" in ln
               for ln in ctx.logs)
    assert got[(PREPAY, "consolidated", "current")] == Decimal("4394165.14")
    assert all(WR.is_model_row(r) for r in _carriers(doc, PREPAY, "consolidated", "current"))
    assert got[(PREPAY, "consolidated", "prior")] == Decimal("3132626.35")


@_present(MONTAGE)
def test_a_refused_slot_stays_empty_when_the_rules_figure_fails_the_checks_too(runs, monkeypatch,
                                                                               tmp_path):
    """The rules never bind a caption the checks veto (`answer_checks.refuse_excluded_face` is the
    rules' own exclusion), so on a real filing the rules' figure for a refused slot passes. The
    other branch, forced: a rules figure the checks fail leaves the slot empty, and says why."""
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    monkeypatch.setattr(WR._RulesCheck, "passes",
                        lambda self, row, ev, key: (False, "forced by the test"))
    doc, ctx, _rec = run_whole_pdf(monkeypatch, tmp_path, MONTAGE, _disagreeing_script(rules_doc),
                                   pages=MONTAGE_PAGES)
    assert (OTHER_CA, "consolidated", "current") not in _slots(doc)
    assert any(ln.startswith(f"whole_pdf:rules_fill:{OTHER_CA}[consolidated:current]: left empty")
               and "forced by the test" in ln for ln in ctx.logs)
    assert ctx.whole_pdf["filled_from_rules"]["refused_by_the_checks"] >= 1


# ── cascades ───────────────────────────────────────────────────────────────────────────────────

RECEIPTS = "cf_oper_direct__cash_receipts_from_customers"   # p156 销售商品、提供劳务收到的现金
OTHER_RECEIPTS = "sub__cf_direct_other_operating_receipts"   # p156 收到其他与经营活动有关的现金


@_present(MONTAGE)
def test_a_part_filled_from_the_rules_beside_a_model_total_is_not_counted_twice(runs):
    """Cash Receipts from Customers takes 收到其他与经营活动有关的现金 beside its printed row (an
    `adds_to_printed` rung over a part). The script leaves the part out, and answers the line for the
    current year WITH the part's row in its sum, for the prior year with the printed row alone.
    Current: the part's row is the model's (it sits in the model's sum), so the rules do not fill the
    part and the rung adds nothing — the line is the model's 3,871,782,445.35, not that plus
    295,347,782.24 again. Prior: the rules fill the part, which arrives beside the model's printed
    2,297,192,754.40 — 2,536,088,928.95, the rules-only figure."""
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    script = standin.whole_pdf_script_from_result(_result(rules_doc), _set(),
                                                  leave_out=[OTHER_RECEIPTS])
    script["lines"][RECEIPTS] = [
        script_figure("3871782445.35", page=156, caption="销售商品、提供劳务收到的现金",
                      parts=[("销售商品、提供劳务收到的现金", "3576434663.11"),
                             ("收到其他与经营活动有关的现金", "295347782.24")]),
        script_figure("2297192754.40", page=156, caption="销售商品、提供劳务收到的现金",
                      period="prior")]
    doc, ctx, _rec = runs("montage_cascade", MONTAGE, MONTAGE_PAGES, script)
    rules, got = _slots(rules_doc), _slots(doc)
    assert rules[(RECEIPTS, "consolidated", "current")] == Decimal("3871782445.35")
    assert got[(RECEIPTS, "consolidated", "current")] == Decimal("3871782445.35")
    assert (OTHER_RECEIPTS, "consolidated", "current") not in got
    assert any(ln.startswith(f"whole_pdf:rules_fill:{OTHER_RECEIPTS}[consolidated:current]")
               and "NOT read — the model placed this figure on " + RECEIPTS in ln
               for ln in ctx.logs)
    assert got[(OTHER_RECEIPTS, "consolidated", "prior")] == Decimal("238896174.55")
    assert got[(RECEIPTS, "consolidated", "prior")] == rules[(RECEIPTS, "consolidated", "prior")] \
        == Decimal("2536088928.95")
    # The company's statement, which the script did not answer at all, is the rules' throughout.
    for period in ("current", "prior"):
        assert got[(RECEIPTS, "standalone", period)] == rules[(RECEIPTS, "standalone", period)]


# ── a statement lost to a failed request ───────────────────────────────────────────────────────

@_present(MONTAGE)
def test_a_cash_flow_statement_lost_to_a_failed_request_is_the_rules_figures(runs):
    """Every request asking the cash-flow lines fails: the statement is lost, and the filing is read
    by the rules route (`stages.whole_pdf._lost_lines`), so each of its slots is the rules-only
    figure, as for a statement the model answered nothing for (the first test)."""
    rules_doc, _ = runs.rules(MONTAGE, MONTAGE_PAGES)
    script = standin.whole_pdf_script_from_result(_result(rules_doc), _set())
    script["fail"] = [{"statement": "cash flow"}]
    doc, ctx, _rec = runs("montage_cf_failed", MONTAGE, MONTAGE_PAGES, script)
    assert any(ln.startswith("whole_pdf:fallback(the cash flow's lines were lost")
               for ln in ctx.logs)
    rules, got = _slots(rules_doc), _slots(doc)
    cash_flow = [s for s in rules if s[0] in set(_statement_keys("cash_flow"))]
    assert cash_flow and all(got.get(s) == rules[s] for s in cash_flow)


# ── HKFRS, thousands ───────────────────────────────────────────────────────────────────────────

@_present(KAMING)
def test_an_hkfrs_filing_the_model_answered_one_line_of_is_the_rules_figures_elsewhere(runs):
    """佳明 (HKFRS, HK$'000): the script answers one face line and nothing else — the way the stand-in
    answered most of this filing's statements, which it can tell only some pages of. Every other slot
    the rules publish on the statements is now the rules' figure; the answered line is the model's."""
    rules_doc, _ = runs.rules(KAMING, KAMING_PAGES)
    rules = _slots(rules_doc)
    full = standin.whole_pdf_script_from_result(_result(rules_doc), _set())
    face = {it.key for it in _asked_face_lines()}
    # A line printed on the one statement page the stand-in tells apart in this part (report p65).
    key = next(k for k, figs in full["lines"].items()
               if k in face and isinstance(figs, list) and all(f["page"] == 65 for f in figs))
    doc, ctx, _rec = runs("kaming_one_line", KAMING, KAMING_PAGES, {"lines": {key: full["lines"][key]}})
    assert ctx.mapping_strategy == "whole_pdf"
    got = _slots(doc)
    slots = [s for s in rules if s[0] in face]
    assert len(slots) >= 60
    missing = [s for s in slots if got.get(s) != rules[s]]
    assert not missing, missing[:5]
    answered = [s for s in rules if s[0] == key]
    assert answered and all(WR.is_model_row(r) for s in answered for r in _carriers(doc, *s))
    assert _setup(doc) == _setup(rules_doc)


def _asked_face_lines():
    from app.services import whole_pdf as W

    return [it for it in W.lines_to_ask(_set())
            if W.statement_of(it) in W.FACE_STATEMENTS and not W.is_part(it)]


# ── no-op elsewhere ────────────────────────────────────────────────────────────────────────────

def test_the_fill_is_a_no_op_off_the_whole_pdf_route():
    from types import SimpleNamespace

    from app.core.models import DocumentModel

    doc = DocumentModel()
    out = WR.fill_from_rules(doc, SimpleNamespace(whole_pdf_rules_rows=None, log=lambda _m: None))
    assert out["slots"] == 0 and doc.line_items == []
