"""ON THE WHOLE-PDF ROUTE THE RULES STILL READ THE FILING, and their reading sits beside the model's
(`core.pipeline.whole_pdf_pipeline`, `stages.whole_pdf`, `services.whole_pdf_rules`).

Before, the whole-PDF stage ran straight after the page classifier and, once the model answered,
the extraction was skipped: no note tables, so an empty Notes screen, no contingent-liability
narrative, no quoted note text, and nothing to fill a part the model left empty — a Level 3
column printed blank came out as "not answered" and Securities (CP) was flagged missing_level_3
where the rules read 0; a request that failed moved CP to its face fallback. Now the stage runs
after the extraction: the model's rows replace the face rows, the note tables stay, and the rules
fill only what the model left empty — never a slot the model answered on any of its rows, and
never with a note figure the model placed on another line.

Measured on 688008 (澜起科技, CAS, native text) on the pages each scenario is about: the balance
sheet, note 七、2 (交易性金融资产), the revenue note in both chapters, the fair-value hierarchy
十三、1 (Level 3 printed blank on every row) and the commitments note. Rules-only, that filing
publishes CP 1,783,494,750.68 / 1,703,266,947.76 with missing_level_3 on the prior slot only.

No model is called. The provider is the shipped stand-in (`scripts/llm_standin.py`) answering
from a SCRIPT (`tests.whole_pdf_standin`), so these show what the pipeline does with a given
answer, not what a model would answer.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.whole_pdf_standin import filing_path, rows_of, run_whole_pdf, script_figure, slot

STEM = "688008_montage_2024_prc"
#: 0-based pages: the balance sheets (149 group, 151 company), note 七、2 (181), the revenue note
#: (218 group, 252 company), the fair-value hierarchy (238), key-management pay in 万元 (241) and
#: the commitments note (243-244).
PAGES = [*range(148, 152), *range(180, 183), *range(217, 220), *range(236, 246),
         *range(251, 254)]
FACE_P = 150                    # report pages (1-based) the script cites
NOTE_P = 182
CP = "bs_ca__secur_and_other_fincl_assets_cp"
FVTPL = "sub__fa_cp_fvtpl_note_total"
OTHER = "sub__fa_cp_other_fincl_assets_note_total"
FACE = "sub__cp_face_trading_fincl_assets"
L3_ROWS = "sub__fa_cp_level_3_rows"
REVENUE = "sub__revenue_note_principal_revenue"

_FACE_LINES = {FACE: [script_figure("1783494750.68", page=FACE_P, caption="交易性金融资产"),
                      script_figure("1703266947.76", page=FACE_P, caption="交易性金融资产",
                                    period="prior")]}

pytestmark = pytest.mark.skipif(not filing_path(STEM).exists(), reason=f"{STEM}.pdf not present")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One run per script, cached for the module."""
    done: dict[str, tuple] = {}

    def _run(name: str, script: dict):
        if name not in done:
            with pytest.MonkeyPatch.context() as mp:
                done[name] = run_whole_pdf(mp, tmp_path_factory.mktemp(name), STEM, script,
                                           pages=PAGES)
        return done[name]
    return _run


def _value(doc, key, basis="consolidated", period="current"):
    _row, ev = slot(doc, key, basis, period)
    return None if ev is None else Decimal(str(ev.value))


def _figures(doc, key, basis, period):
    """Every (row, figure) in this slot, over every row of the key."""
    out = []
    for row in rows_of(doc, key):
        for ev in row.values.values():
            if (str(getattr(ev.basis, "value", ev.basis)) == basis and ev.period_label == period
                    and ev.value is not None):
                out.append((row, Decimal(str(ev.value))))
    return out


# ── the order: the extraction first, and what it read is kept ──────────────────────────────────

def _fill_and_keep(run):
    return run("fill", {"units": "元", "lines": {
        **_FACE_LINES,
        FVTPL: [script_figure("1783494750.68", page=NOTE_P, caption="合计"),
                script_figure("1703266947.76", page=NOTE_P, caption="合计", period="prior")],
        # The revenue part answered for the group AND the company — two rows of one line.
        REVENUE: [script_figure("3628769555.93", page=219, caption="主营业务"),
                  script_figure("547176613.67", page=253, caption="主营业务",
                                basis="standalone")]}})


def test_the_rules_read_the_filing_first_and_the_note_tables_stay(run):
    """The extraction ran before the model's answer; its face rows are set aside unmapped, the
    model's rows are the document's, and the note tables are still the document's."""
    doc, ctx, rec = _fill_and_keep(run)
    assert ctx.logs.index("stage:extract:start") < ctx.logs.index("stage:whole_pdf:start")
    assert ctx.logs.count("stage:extract:start") == 1
    assert ctx.mapping_strategy == "whole_pdf"
    assert ctx.whole_pdf_rules_rows and all(li.canonical_key is None
                                            for li in ctx.whole_pdf_rules_rows)
    assert not {id(li) for li in ctx.whole_pdf_rules_rows} & {id(li) for li in doc.line_items}
    assert doc.notes and ctx.whole_pdf["notes_kept"] >= len(doc.notes) > 0
    assert ctx.whole_pdf["rules_face_rows"] == len(ctx.whole_pdf_rules_rows)
    # Every whole-PDF request went to the stand-in with the file, and was answered by the script.
    asked = [c for c in rec.calls if c["schema"] == "WholePdfReply"]
    assert asked and all(c.get("scripted") and c.get("documents") for c in asked)


def test_the_stages_that_need_the_notes_run_and_the_ones_that_would_overrule_the_model_do_not(run):
    _doc, ctx, _rec = _fill_and_keep(run)
    for name in ("link_notes", "note_sourced", "contingent_liabilities", "reconcile",
                 "prune_notes", "normalize", "structural"):
        assert f"stage:{name}:start" in ctx.logs, name
    for name in ("map_line_items", "residual", "line_item_llm", "assemble_components",
                 "gap_closing", "face_mapping_contract", "note_tag_gate", "row_suggestions"):
        assert f"stage:{name}:skipped" in ctx.logs, name


def test_the_contingent_liability_narrative_is_built_on_this_route(run):
    """Finding cl_narrative_and_note_headings:whole_pdf: the stage never ran. It now reads the
    commitments note as on the rules route (rules-only: Commitments of 230,766,126.15)."""
    doc, _ctx, _rec = _fill_and_keep(run)
    narrative = (doc.contingent_liabilities or {}).get("consolidated:current") or {}
    assert "Commitments" in narrative.get("summary_paragraph", "")
    assert narrative.get("detail_items")


# ── the fill: what the model left empty, the rules' reading ────────────────────────────────────

def test_a_level_3_column_printed_blank_reads_the_rules_0_and_cp_is_flagged_as_rules_only(run):
    """Findings blank_level3_whole_pdf / blank_column_is_nil:whole_pdf: the script leaves both
    Level 3 parts out, as a model told "never answer 0 for a line that is not there" may. The
    rules read the blank column as 0, so the current slot carries no missing_level_3 — only the
    prior slot does, exactly as rules-only."""
    doc, ctx, _rec = _fill_and_keep(run)
    row, ev = slot(doc, L3_ROWS)
    assert ev is not None and Decimal(str(ev.value)) == 0
    assert row.confidence.method != "llm"                          # the rules' reading
    assert _value(doc, CP) == Decimal("1783494750.68")
    assert _value(doc, CP, period="prior") == Decimal("1703266947.76")
    flags = {f for r in rows_of(doc, CP) for f in r.confidence.flags if f.startswith("missing_level_3")}
    assert flags == {"missing_level_3:consolidated:prior"}


def test_a_part_the_model_answered_keeps_the_models_figure(run):
    """The model's FVTPL note total stands and the rules' reading of it is not written."""
    doc, ctx, _rec = _fill_and_keep(run)
    row, ev = slot(doc, FVTPL)
    assert row.confidence.method == "llm" and Decimal(str(ev.value)) == Decimal("1783494750.68")
    assert len(_figures(doc, FVTPL, "consolidated", "current")) == 1
    assert any(f"{FVTPL}[consolidated:current]: 1783494750.68 NOT written — the model answered"
               in line for line in ctx.logs)


def test_a_slot_the_model_answered_on_its_other_row_is_not_written_again(run):
    """The stage writes one row per line AND BASIS; `note_sourced` looked one row up per key (the
    company's, the last) and found no group slot on it, so the rules wrote the group's revenue a
    second time. Each answered slot now has exactly one figure, the model's; a slot the model left
    empty (the prior year) is the rules', on a row of their own."""
    from app.services.whole_pdf_rules import RULES_FLAG

    doc, ctx, _rec = _fill_and_keep(run)
    for basis, figure in (("consolidated", "3628769555.93"), ("standalone", "547176613.67")):
        held = _figures(doc, REVENUE, basis, "current")
        assert [(r.confidence.method, v) for r, v in held] == [("llm", Decimal(figure))], basis
    prior = _figures(doc, REVENUE, "consolidated", "prior")
    assert [v for _r, v in prior] == [Decimal("2278141066.50")]
    assert RULES_FLAG in prior[0][0].confidence.flags and prior[0][0].confidence.method != "llm"
    # No model row carries a slot the rules wrote.
    for r in rows_of(doc, REVENUE):
        if r.confidence.method == "llm":
            assert {ev.period_label for ev in r.values.values()} == {"current"}


def test_a_note_row_in_another_unit_is_put_into_the_unit_the_rules_read_off_the_face(run):
    """688008 prints key-management pay in 万元 (5,849.45) under statements in 元. The model's rows
    carry no unit, so the rules' fill compared the note's 万元 with "no declared unit" and wrote
    5,849.45; the conversion is now decided against the unit the rules read off the face, as on the
    rules route (58,494,500.00 / 42,277,100.00)."""
    doc, ctx, _rec = _fill_and_keep(run)
    row, ev = slot(doc, "sub__rpt_key_management")
    assert row.confidence.method != "llm"
    assert Decimal(str(ev.value)) == Decimal("58494500.00")
    assert _value(doc, "sub__rpt_key_management", period="prior") == Decimal("42277100.00")
    assert "unit_rescaled:10000:1" in row.confidence.flags
    # The slot is stamped in the scale the model's rows beside it carry, so no section reads as
    # mixed-scale (the checks compare scales; the currency is the document's, as before).
    model_scales = {e.unit_ctx.scale_factor for r in doc.line_items
                    if r.confidence.method == "llm" for e in r.values.values()}
    assert model_scales == {ev.unit_ctx.scale_factor}


def test_a_note_row_the_model_placed_on_one_line_is_not_read_again_for_another(run):
    """The model files 七、2's structured deposits (1,629,362,905.47) on the "other financial
    assets" part and leaves the FVTPL part empty. The rules' FVTPL reading is the note's two rows;
    the one the model already placed is not read again, so CP counts the deposits once
    (1,783,494,750.68) and not twice (3,412,857,656.15)."""
    doc, ctx, _rec = run("placed_elsewhere", {"units": "元", "lines": {
        **_FACE_LINES,
        OTHER: [script_figure("1629362905.47", page=NOTE_P, caption="结构性存款")]}})
    assert _value(doc, OTHER) == Decimal("1629362905.47")
    assert _value(doc, FVTPL) == Decimal("154131845.21")
    assert _value(doc, CP) == Decimal("1783494750.68")
    assert any("NOT read — the model placed this figure on " + OTHER in line for line in ctx.logs)
    # The year the model did not touch is read whole.
    assert _value(doc, FVTPL, period="prior") == Decimal("1703266947.76")


def test_a_model_figure_that_differs_from_the_rules_stands_and_is_flagged(run):
    doc, ctx, _rec = run("differs", {"units": "元", "lines": {
        **_FACE_LINES,
        FVTPL: [script_figure("1629362905.47", page=NOTE_P, caption="结构性存款")]}})
    row, ev = slot(doc, FVTPL)
    assert row.confidence.method == "llm" and Decimal(str(ev.value)) == Decimal("1629362905.47")
    assert "llm_note_route_disagree:1629362905.47:1783494750.68" in row.confidence.flags
    assert len(_figures(doc, FVTPL, "consolidated", "current")) == 1


def test_a_request_that_fails_leaves_its_parts_to_the_rules_not_to_the_face_fallback(run):
    """Finding failed_request_changes_rung: the request holding the FVTPL part fails (after its
    retry), so the part the model would have answered is empty. The rules read it from note 七、2
    and CP is computed as on the rules route (CP_INTERMEDIATE), not taken from the face
    (FROM_THE_FACE, no Level 3 deducted, no flag). (The request is named by the part it asks: the
    CP family is asked in one request now, `services.whole_pdf.families`, no longer the notes'.)"""
    doc, ctx, _rec = run("notes_fail", {"units": "元", "lines": {
        **_FACE_LINES,
        FVTPL: [script_figure("1783494750.68", page=NOTE_P, caption="合计")]},
        "fail": [{"key": FVTPL}]})
    assert ctx.llm_failures >= 1 and any(line.startswith("whole_pdf:request_failed")
                                         for line in ctx.logs)
    row, ev = slot(doc, FVTPL)
    assert row.confidence.method != "llm" and Decimal(str(ev.value)) == Decimal("1783494750.68")
    cp_flags = {f for r in rows_of(doc, CP) for f in r.confidence.flags}
    assert "cascade_rung:CP_INTERMEDIATE" in cp_flags and "cascade_rung:FROM_THE_FACE" not in cp_flags
    assert _value(doc, CP) == Decimal("1783494750.68")


def test_a_run_the_model_does_not_answer_carries_on_from_the_extraction_already_made(run):
    """A fallback reads nothing twice: the document still holds what the extraction read, and the
    rules route carries on from it."""
    doc, ctx, _rec = run("nothing", {"lines": {}})
    assert any(line.startswith("whole_pdf:fallback(the model found none") for line in ctx.logs)
    assert ctx.logs.count("stage:extract:start") == 1
    assert "stage:map_line_items:start" in ctx.logs and ctx.only_stages is None
    assert ctx.whole_pdf_rules_rows is None and ctx.mapping_strategy != "whole_pdf"
    assert _value(doc, CP) == Decimal("1783494750.68")       # the rules route's own figure


# ── the guards, on their own ───────────────────────────────────────────────────────────────────

def _model_row(key, *slots, caption="x", page=4, rows=()):
    """A row as `stages.whole_pdf._rows` writes one: the flag, the model's method, its slots."""
    from app.core.models.enums import Basis
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem

    row = LineItem(source_label=caption, canonical_key=key)
    for basis, period, amount in slots:
        row.set_value(ExtractedValue(basis=Basis(basis), period_label=period,
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=page, text_snippet=caption)))
        if rows:
            row.derivation = {**(row.derivation or {}), f"{basis}:{period}": {"inputs": [
                {"label": c, "value": a, "provenance": {"page_index": page}} for c, a in rows]}}
    row.confidence.method = "llm"
    row.confidence.flags.append("whole_pdf")
    return row


def test_the_guards_read_only_rows_the_whole_pdf_stage_wrote():
    from app.core.models import DocumentModel
    from app.services import whole_pdf_rules as R
    from app.stages.whole_pdf import FLAG

    assert R.ROW_FLAG == FLAG
    doc = DocumentModel()
    company = _model_row("k", ("standalone", "current", "5"))
    group = _model_row("k", ("consolidated", "current", "7"))
    doc.line_items = [group, company]
    assert R.model_row_holding(doc, "k", "consolidated", "current") is group
    assert R.model_row_holding(doc, "k", "consolidated", "prior") is None
    # A row the model answered on another route (no whole-PDF flag) is not this module's.
    group.confidence.flags.remove("whole_pdf")
    assert R.model_row_holding(doc, "k", "consolidated", "current") is None
    assert R.model_figures(doc) == {4: {Decimal(5): {"k"}}}


def test_the_rules_write_on_a_row_of_their_own_and_an_empty_one_is_taken_back():
    from app.core.models import DocumentModel
    from app.services import whole_pdf_rules as R

    doc = DocumentModel()
    model = _model_row("k", ("standalone", "current", "5"))
    doc.line_items = [model]
    mine = R.own_row(doc, model, "k", "Line K")
    assert mine is not model and R.RULES_FLAG in mine.confidence.flags and mine in doc.line_items
    assert R.own_row(doc, model, "k") is mine                    # one such row per line
    assert R.discard_if_empty(doc, mine) and mine not in doc.line_items
    assert R.own_row(doc, None, "k") is None
    assert not R.discard_if_empty(doc, model)                    # never a row another writer made


def test_a_figure_the_model_cited_is_refused_for_another_line_only_and_never_a_zero():
    from types import SimpleNamespace as NS

    from app.core.models import DocumentModel
    from app.services import whole_pdf_rules as R

    doc = DocumentModel()
    # A sum of two printed rows: each row is as much "placed" as the figure.
    doc.line_items = [_model_row("a", ("consolidated", "current", "1000"),
                                 rows=[("deposits", "400"), ("equity", "600")])]
    figures = R.model_figures(doc)
    assert figures == {4: {Decimal(1000): {"a"}, Decimal(400): {"a"}, Decimal(600): {"a"}}}

    def hit(amount, page=4):
        return NS(amount=Decimal(amount), basis="consolidated", period="current", caption="r",
                  value=NS(provenance=NS(page_index=page)))

    logged: list[str] = []
    kept = R.drop_cited([hit("400"), hit("-600"), hit("0"), hit("400", page=5), hit("7")],
                        figures, "b", logged.append)
    assert [(h.amount, h.value.provenance.page_index) for h in kept] == [
        (Decimal(0), 4), (Decimal(400), 5), (Decimal(7), 4)]
    assert len(logged) == 2 and "placed this figure on a" in logged[0]
    # The line the model put it on keeps its own reading (compared with the model's, not refused).
    assert len(R.drop_cited([hit("400")], figures, "a")) == 1
    # No whole-PDF rows: nothing is refused.
    assert R.model_figures(DocumentModel()) == {}
    assert len(R.drop_cited([hit("400")], R.model_figures(DocumentModel()), "b")) == 1
