"""A WHOLE-PDF SUM THAT TAKES A ROW OUT OF THE ROW IT SITS IN KEEPS THE SUBTRACTION
(`services.whole_pdf_locate.Judge.judge_one`, `services.answer_checks`).

On the whole-PDF route the model gives each row of a sum as a SIGNED amount: a row it takes out
is given as a negative figure, and the figure written adds the signed rows. The checks were told
every row was added (`signs=[1] * len(entries)`), so:

  * 300319 七、2 prints the prior-year 以公允价值计量且其变动计入当期损益的金融资产 181,124,711.32
    with 其中：远期结售汇 565,202.29, a forward. The model taking the forward OUT of the row
    (181,124,711.32 − 565,202.29 = 180,559,509.03, the rules' figure) had the forward refused as
    "a derivative", and 181,124,711.32 was published — with the derivative the line excludes;
  * the current year's 431,478,888.81 less its 其中：权益工具投资 58,467,288.48 had the subtracted
    row refused as "a 其中 row added to the row it breaks down".

Now each row's sign is the sign of its amount, as the figure written reads it. A 其中 row
subtracted from its row is the line taking a component out, which the breakdown check already
allows; and a derivative subtracted from the row it is printed inside is the line excluding it,
which the derivative check now allows too. Adding them is still refused.

Each test drives the REAL pipeline on the real filing's pages, with the shipped stand-in
answering from a SCRIPT (`tests.whole_pdf_standin`): a scripted answer shows what the pipeline
does with that answer, never what a model would answer.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.whole_pdf_standin import filing_path, run_whole_pdf, script_figure, slot

STEM = "300319_maijie_2024_prc"
PAGES = [*range(96, 100), *range(144, 147), *range(175, 178)]
FVTPL = "sub__fa_cp_fvtpl_note_total"
CP = "bs_ca__secur_and_other_fincl_assets_cp"
ROW = "以公允价值计量且其变动计入当期损益的金融资产"

SCRIPTS = {
    # The prior year: the row, less the forward printed inside it.
    "forward_out": {"units": "元", "lines": {FVTPL: [script_figure(
        "180559509.03", page=146, caption="交易性金融资产", period="prior",
        parts=[(ROW, "181124711.32"), ("其中：远期结售汇", "-565202.29")])]}},
    # The current year: the row, less one of its 其中 rows.
    "equity_out": {"units": "元", "lines": {FVTPL: [script_figure(
        "373011600.33", page=146, caption="交易性金融资产",
        parts=[(ROW, "431478888.81"), ("权益工具投资", "-58467288.48")])]}},
    # The prior year: the forward ADDED to the row it is printed inside.
    "forward_in": {"units": "元", "lines": {FVTPL: [script_figure(
        "181689913.61", page=146, caption="交易性金融资产", period="prior",
        parts=[(ROW, "181124711.32"), ("其中：远期结售汇", "565202.29")])]}},
}

_needs_filing = pytest.mark.skipif(not filing_path(STEM).exists(),
                                   reason=f"{STEM}.pdf not present")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    done: dict[str, tuple] = {}

    def _run(name: str):
        if name not in done:
            with pytest.MonkeyPatch.context() as mp:
                done[name] = run_whole_pdf(mp, tmp_path_factory.mktemp(name), STEM,
                                           SCRIPTS[name], pages=PAGES)
        return done[name]
    return _run


def _value(doc, key, period="current"):
    _row, ev = slot(doc, key, "consolidated", period)
    return None if ev is None else Decimal(str(ev.value))


def _flags(doc, key, period="current"):
    _row, ev = slot(doc, key, "consolidated", period)
    return list(ev.confidence.flags) if ev is not None else []


@_needs_filing
def test_a_forward_taken_out_of_the_row_it_is_printed_in_is_not_refused(run):
    """The prior-year FVTPL is the rules' 180,559,509.03: the forward is out, as the line says."""
    doc, ctx, _rec = run("forward_out")
    assert _value(doc, FVTPL, "prior") == Decimal("180559509.03")
    assert not any(f.startswith("whole_pdf:refused") for f in _flags(doc, FVTPL, "prior"))
    assert _value(doc, CP, "prior") == Decimal("180559509.03")
    assert not any("远期结售汇" in line and "REFUSED" in line for line in ctx.logs)


@_needs_filing
def test_a_qizhong_row_taken_out_of_its_row_is_not_refused(run):
    """The current-year figure is the row less the 其中 row the model took out."""
    doc, _ctx, _rec = run("equity_out")
    assert _value(doc, FVTPL) == Decimal("373011600.33")
    assert not any(f.startswith("whole_pdf:refused") for f in _flags(doc, FVTPL))


@_needs_filing
def test_a_forward_added_to_the_row_it_is_printed_in_is_still_refused(run):
    """Adding the derivative is what the line excludes: it is refused, as before."""
    doc, _ctx, _rec = run("forward_in")
    assert _value(doc, FVTPL, "prior") == Decimal("181124711.32")
    assert "whole_pdf:refused:derivative:consolidated:prior:565202.29" in _flags(
        doc, FVTPL, "prior")


# ── the derivative check on its own, as every route calls it ──────────────────────────────────

def _fvtpl_item():
    import json
    import pathlib

    from app.schemas.line_items import load_line_item_set

    seed = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "output_csv_hk_line_items.json")
    items = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)
    return next(i for i in items.items if i.key == FVTPL)


def _note_7_2():
    from app.core.models.enums import Basis
    from app.core.models.geometry import BBox, Provenance
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable

    def row(caption, prior, y, group=""):
        r = NoteItem(raw_label=caption, note_number="七、2", group_hint=group)
        r.set_value(ExtractedValue(value=Decimal(prior), basis=Basis.CONSOLIDATED,
                                   period_label="prior",
                                   provenance=Provenance(source_kind="native", page_index=145,
                                                         bbox=BBox(x0=0.7, y0=y, x1=0.8,
                                                                   y1=y + 0.012))))
        return r
    return NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        row(ROW, "181124711.32", 0.30), row("结构性理财产品", "130559509.03", 0.32, "其中："),
        row("股权投资", "50000000.00", 0.34, "其中："), row("远期结售汇", "565202.29", 0.36, "其中：")])


def _checked(sources, signs):
    from app.core.models import DocumentModel
    from app.services import answer_checks
    from app.services.mapping import SourceRef
    from app.services.note_sourced import resolve_sources

    table = _note_7_2()
    resolved, _ = resolve_sources([SourceRef(note="七、2", caption=c) for c in sources], [table])
    return answer_checks.check_answer(_fvtpl_item(), resolved, doc=DocumentModel(), signs=signs,
                                      component=len(sources) > 1,
                                      index=answer_checks.NoteIndex([table]))


def test_the_derivative_check_keeps_a_forward_subtracted_from_its_row():
    checked = _checked([ROW, "远期结售汇"], [1, -1])
    assert [e["caption"] for e in checked.kept] == [ROW, "远期结售汇"]
    assert checked.refused == []


def test_the_derivative_check_refuses_a_forward_added_or_standing_alone():
    for sources, signs in (([ROW, "远期结售汇"], [1, 1]), (["远期结售汇"], [-1]),
                           (["结构性理财产品", "远期结售汇"], [1, -1])):
        checked = _checked(sources, signs)
        assert [r["refused"] for r in checked.refused] == ["derivative"], (sources, signs)
