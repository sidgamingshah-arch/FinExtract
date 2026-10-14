"""A derived figure's trail adds up to the figure its line shows.

NEW FILE -> backend/tests/test_trails_add_up.py

WHAT WAS WRONG. The engine knows what every term of a rule adds to a figure — `_apply_terms`
records each term's signed contribution, the row read twice that adds nothing, the candidate a
`max` did not take; `combine_terms` knows whether the model's answer is one whole figure or a sum
of signed parts — and the trail writers threw it away. They stored each input's raw figure marked
counted, and every reader added those up. Measured at 12be8d6 on Lai Sun's Securities (LTP): the
trail listed 788,507 + 128,412 + 788,507 (the same printed row, read by a second part) − 549,975,
which is 1,155,451, beside a published 366,944. The ICON stand-in run had 5 of 178 trail slots that
did not add up: a whole answer's corroborating row counted twice, and a deduction shown as an
addition.

WHAT IS ASSERTED. Every writer records what each input put into the figure (`used`, `status`), and
`services.derivation.working` reads it back as adding up to the published figure — while the
fields the engine itself reads (`value`, `counted`, `deducted`, `canonical_key`, `provenance`) stay
exactly as they were, which is what keeps this display-only.
"""
from __future__ import annotations

import json
import os
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NotesTable
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import derivation, note_sourced
from app.services.line_item_llm import combine_terms
from app.services.line_items import evaluate, term_statuses
from tests.test_a_notes_money_is_on_the_face_once import _cascade, _face, _laisun, _part

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "sample" / "templates"
LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
FVTPL = "sub__ltp_fvtpl_note_total"
OTHER = "sub__ltp_other_fincl_assets_note_total"


def _set(name: str):
    raw = json.loads((TEMPLATES / f"output_csv_{name}_line_items.json").read_text(encoding="utf-8"))
    return load_line_item_set(raw, resolve=True)


def _working(row, period: str, basis: str = "consolidated") -> dict:
    ev = next((e for e in row.values.values() if e.period_label == period), None)
    return derivation.working(row.derivation, basis, period,
                              shown=None if ev is None else ev.value)


def _statuses(w: dict) -> list:
    return [t["status"] for t in w["terms"]]


# ── the status rule against the engine ──────────────────────────────────────────────────────

def _op_of(item, rung_id) -> str:
    rung = next((r for r in item.cascade or () if str(r.id) == str(rung_id)), None)
    return str(getattr(rung, "terms_op", None) or getattr(item, "terms_op", None) or "sum")


def _variants(refs: list[str]):
    """Every shape a rung's inputs take: all present, each one absent, every other one, negative
    magnitudes, and two terms reading one printed row."""
    full = {r: Decimal(100 + 7 * n) for n, r in enumerate(refs)}
    yield full, None
    for r in refs:
        yield {k: v for k, v in full.items() if k != r}, None
    yield {k: v for n, (k, v) in enumerate(full.items()) if n % 2 == 0}, None
    yield {k: v for n, (k, v) in enumerate(full.items()) if n % 2 == 1}, None
    yield {k: -v for k, v in full.items()}, None
    if len(refs) >= 2:
        one_row = (1, "Total", (0.6, 0.4), "100")
        shared = dict(full)
        shared[refs[1]] = shared[refs[0]]
        yield shared, {refs[0]: one_row, refs[1]: one_row}


@pytest.mark.parametrize("name", ["hk", "indas", "icon"])
def test_the_status_rule_reproduces_every_shipped_rung(name):
    """`term_statuses` is the display's reading of `_apply_terms`' entries. Over every rung shape
    the three shipped sets declare, what the entered terms add is the engine's figure exactly —
    sums, a `max` with its tie, the `min` of a fixed nought, deductions, rows read twice."""
    evaluations = mismatches = 0
    for item in _set(name).items:
        if not (getattr(item, "cascade", None) or getattr(item, "terms", None)):
            continue
        refs: list[str] = []
        for rung in item.cascade or ():
            refs += [t.ref for t in rung.terms if t.ref and t.ref not in refs]
        refs += [t.ref for t in item.terms or () if t.ref and t.ref not in refs]
        for known, sources in _variants(refs):
            got = evaluate(item, known, sources)
            if not got.resolved:
                continue
            evaluations += 1
            added = sum((adds for status, adds, _r in term_statuses(got.inputs,
                                                                     _op_of(item, got.rung_used))
                         if status in derivation.ENTERED), Decimal(0))
            mismatches += added != got.value
    assert mismatches == 0
    assert evaluations > (100 if name in ("hk", "indas") else 0), evaluations


def test_a_tie_is_counted_once():
    """A `max` marks EVERY tying candidate selected. Counting each doubled the figure: the trail
    showed 4,200 for a published 1,650."""
    inputs = [{"ref": "a", "sign": 1, "role": "any_of", "value": "1650", "used": "1650",
               "selected": True},
              {"ref": "b", "sign": 1, "role": "any_of", "value": "1650", "used": "1650",
               "selected": True},
              {"ref": "c", "sign": 1, "role": "any_of", "value": "900", "used": "900",
               "selected": False}]
    assert [(s, a) for s, a, _r in term_statuses(inputs, "max")] == [
        ("used", Decimal("1650")), ("not_used", 0), ("not_used", 0)]


# ── the cascade writer ──────────────────────────────────────────────────────────────────────

def _ltp_with_the_shared_row():
    rows = _laisun()
    rows.insert(4, _part(OTHER, [("Non-current portion", "788507", "941274", 0.45)]))
    return _cascade(rows)


def test_a_row_two_parts_read_is_added_once_and_the_trail_adds_up():
    """Lai Sun's LTP: the FVTPL row is read by the FVTPL part AND the "other financial assets"
    part. The rung adds it once; the trail now says so — "same row, added once" — and comes to the
    366,944 the line publishes, not 1,155,451."""
    ltp, got = _ltp_with_the_shared_row()
    assert got == {"current": "366944", "prior": "847794"}

    w = _working(ltp, "current")
    assert w["how"] == "adds_up" and w["sum"] == 366944.0
    assert _statuses(w) == ["used", "used", "same_row", "deducted"]
    fvtpl_label = next(i["label"] for i in ltp.derivation["consolidated:current"]["inputs"]
                       if i.get("canonical_key") == FVTPL)
    assert w["terms"][2]["why"] == {"code": "same_row", "line": fvtpl_label}
    assert _working(ltp, "prior")["sum"] == 847794.0

    # The fields the engine reads are as they were: every input counted, the duplicate at its own
    # printed figure.
    inputs = ltp.derivation["consolidated:current"]["inputs"]
    assert all(i["counted"] is True for i in inputs)
    (dup,) = [i for i in inputs if i.get("canonical_key") == OTHER]
    assert dup["value"] == "788507" and dup["used"] == "0"
    assert "ref" not in dup, "the face settlement follows `ref` as it follows `canonical_key`"


def test_the_working_names_terms_the_filing_did_not_print():
    """The answering rung's absent terms, with their roles: LTP's six optional note parts, and on
    the operating-expense depreciation line the cost-of-sales deduction its P3 published without."""
    ltp, _got = _ltp_with_the_shared_row()
    missing = ltp.derivation["consolidated:current"]["missing"]
    assert len(missing) == 6 and {m["role"] for m in missing} == {"any_of"}

    from app.services.line_item_config import load_shipped_set
    from app.stages.note_sourced import _fill_by_cascade

    defs = {i.key: i for i in load_shipped_set().items}
    pbt = LineItem(source_label="Depreciation", canonical_key="sub__pbt_depreciation")
    pbt.set_value(ExtractedValue(value=Decimal("587417"), basis=Basis.CONSOLIDATED,
                                 period_label="current"))
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [pbt]
    parent = defs["is_pl__deprec_and_impairment_oper_exp"]
    _fill_by_cascade(parent, [(defs["sub__pbt_depreciation"], pbt)],
                     {"sub__pbt_depreciation": pbt}, doc, PipelineContext(), defs)
    (line,) = [li for li in doc.line_items if li.canonical_key == parent.key]
    trail = line.derivation["consolidated:current"]
    assert trail["method"] == "cascade:P3"
    assert trail["missing"] == [{"key": "sub__cos_depreciation",
                                 "label": "Cost of sales — depreciation", "role": "adjustment"}]
    w = _working(line, "current")
    assert "Not found: Cost of sales — depreciation." in derivation.working_text(
        w, lambda x: f"{x:,.0f}")


def test_a_part_printed_elsewhere_is_shown_at_its_figure_and_deducted_once():
    """FVTOCI's 128,412 is printed on the face in another line. The trail shows the part at its own
    figure and the face's money as one deduction — 788,507 + 128,412 − 549,975 − 128,412 = 238,532
    — where it used to show the part at its figure AND the deduction while the rung had read it at
    nought."""
    ltp, got = _cascade(_laisun([_face("bs_nca__investments_in_associates", "128412", "25",
                                       y=0.30, prior="119364")]))
    assert got == {"current": "238532", "prior": "728430"}
    w = _working(ltp, "current")
    assert w["how"] == "adds_up" and w["sum"] == 238532.0
    inputs = ltp.derivation["consolidated:current"]["inputs"]
    (fvtoci,) = [i for i in inputs if i.get("canonical_key") == "sub__ltp_fvtoci_note_total"]
    assert (fvtoci["used"], fvtoci["reduced_by"]) == ("128412", "128412")
    (cut,) = [i for i in inputs if i.get("deducted")
              and i.get("canonical_key") == "bs_nca__investments_in_associates"]
    assert (cut["used"], cut["value"]) == ("-128412", "128412")
    assert derivation._why(cut)["code"] == "counted_on_face"
    assert _working(ltp, "prior")["sum"] == 728430.0


def test_a_whole_line_printed_elsewhere_adds_up_to_what_it_publishes():
    """The face prints the LINE's whole figure elsewhere. Its parts are taken out entirely, LTP_P1
    comes to −549,975 and is passed over, LTP_P2 publishes 0 — and the trail says all of it: the
    parts at their figures, the face's money as one deduction of 916,919, and the refused rung."""
    ltp, got = _cascade(_laisun([_face("bs_nca__investments_in_associates", "366944", "25",
                                       y=0.30, prior="847794")]))
    assert got == {"current": "0", "prior": "0"}
    trail = ltp.derivation["consolidated:current"]
    assert trail["result"] == "0" and trail["method"] == "cascade:LTP_P2"
    assert trail["refused"] == [{"rung": "LTP_P1", "why": "negative", "value": "-549975"}]
    (cut,) = [i for i in trail["inputs"] if i.get("face_caption") is not None]
    assert (cut["used"], cut["whole_line"], cut["face_amount"]) == ("-916919", True, "366944")
    assert _working(ltp, "current")["how"] == "adds_up"


def test_a_face_deduction_comes_after_the_figure_a_choosing_rule_took():
    """An `alternatives` rollup takes the FIRST row; the face prints that row's 4,444 in another
    line, so the settlement deducts it. The deduction applies to the figure the rollup took — it is
    not one more row to choose between — and the working read "first(4,444; 4,444; -4,444) = 0"."""
    from tests.test_a_notes_money_is_on_the_face_once import L, NOTES, T, _ev, _face, _note, _prov, _run

    hits = [note_sourced.NoteRowHit(key="bs_ca__other_receivables_cp", note_number="18",
                                    note_title="Receivables", caption=caption, matched_by="other",
                                    value=SimpleNamespace(provenance=_prov(NOTES, y)),
                                    amount=Decimal("4444"), basis="consolidated", period="current")
            for caption, y in (("Other receivables", 0.50), ("Other receivables, net", 0.55))]
    ((_slot, (amount, inputs)),) = note_sourced.resolve(hits, "alternatives").items()
    line = LineItem(source_label="Other receivables", canonical_key="bs_ca__other_receivables_cp")
    line.set_value(_ev("4444", NOTES, 0.50))
    line.derivation = derivation.record(None, basis="consolidated", period_label="current",
                                        derivation=note_sourced.trail(
                                            rollup="alternatives", item_label="x", amount=amount,
                                            inputs=inputs, display={"op": "first"}))
    note_18 = _note("18", [("Trade receivables", "122295", 0.40, L),
                           ("Other receivables", "4444", 0.50, L), ("Total", "126739", 0.60, T)])
    got = _run([_face("bs_ca__trade_and_other_receivables", "126739", "18"), line], [note_18])
    assert got["bs_ca__other_receivables_cp"] == ["0"]

    w = _working(line, "current")
    assert w["how"] == "adds_up" and w["op"] == "first"
    text = derivation.working_text(w, lambda x: f"{x:,.0f}")
    assert text.startswith("first(4,444; 4,444) − 4,444 = 0 — adds up."), text
    assert w["formula"] == ("first(Other receivables — note 18; Other receivables, net — note 18)"
                            " − Counted on the face in “bs_ca__trade_and_other_receivables”")
    (cut,) = [i for i in line.derivation["consolidated:current"]["inputs"] if i.get("deducted")]
    assert cut["role"] == "adjustment"


def test_a_settled_max_compares_each_part_at_what_is_left_of_it():
    """MAX_VALID takes the largest of three readings. The face prints the 1,650 it took in another
    line, so that reading is compared at nothing and the rule takes the next one, 1,200. The working
    says it in the rule's own terms — where it read "max(900; 1,650; 1,200; 1,650) = 1,200": the
    reading at the figure it no longer had, and the face's money as a fourth candidate."""
    from app.core.models import PageKind
    from app.core.models.document import PageSource
    from app.services.line_item_config import load_shipped_set
    from app.stages.note_sourced import _fill_by_cascade, _settle_against_the_face
    from tests.test_a_notes_money_is_on_the_face_once import FACE, NOTES, _ev, _face, _filled

    items = list(load_shipped_set().items)
    defs = {i.key: i for i in items}
    parent = defs["bs_nca__due_from_related_parties_ltp"]
    find_1 = LineItem(source_label="find 1", canonical_key="sub__rp_find_1")
    find_1.set_value(_ev("900", NOTES, 0.20))
    parts = [find_1, _filled("sub__rp_find_2", "1650", "25", y=0.30),
             _filled("sub__rp_find_3", "1200", "26", y=0.40)]
    face = _face("bs_nca__other_non_current_assets", "1650", "25", y=0.30)
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = [face, *parts]
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[])
    _fill_by_cascade(parent, [(defs[p.canonical_key], p) for p in parts],
                     {li.canonical_key: li for li in doc.line_items}, doc, ctx, defs)
    _settle_against_the_face(doc, ctx, items)
    line = next(li for li in doc.line_items if li.canonical_key == parent.key)
    assert [str(ev.value) for ev in line.values.values()] == ["1200"]

    w = _working(line, "current")
    assert w["how"] == "adds_up" and w["op"] == "max"
    text = derivation.working_text(w, lambda x: f"{x:,.0f}")
    assert text.startswith("max(900; 0; 1,200) = 1,200 — adds up."), text
    by_key = {t["canonical_key"]: t for t in w["terms"]}
    assert by_key["sub__rp_find_2"]["why"] == {"code": "reduced", "amount": "1650"}
    face_row = by_key["bs_nca__other_non_current_assets"]
    assert (face_row["status"], face_row["why"]["code"]) == ("not_used", "not_needed")
    assert "Counted on the face" not in w["formula"], w["formula"]


def _run_cascade(parent_key: str, figures: dict[str, str]):
    from app.services.line_item_config import load_shipped_set
    from app.stages.note_sourced import _fill_by_cascade

    defs = {i.key: i for i in load_shipped_set().items}
    rows = []
    for key, amount in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(amount), basis=Basis.CONSOLIDATED,
                                    period_label="current"))
        rows.append(li)
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = list(rows)
    parent = defs[parent_key]
    _fill_by_cascade(parent, [(defs[li.canonical_key], li) for li in rows],
                     {li.canonical_key: li for li in rows}, doc, PipelineContext(), defs)
    return next(li for li in doc.line_items if li.canonical_key == parent_key)


def test_a_max_rung_counts_one_candidate_even_when_two_tie():
    line = _run_cascade("bs_nca__due_from_related_parties_ltp",
                        {"sub__rp_find_1": "1650", "sub__rp_find_2": "1650", "sub__rp_find_3": "900"})
    w = _working(line, "current")
    assert w["how"] == "adds_up" and w["sum"] == 1650.0
    assert _statuses(w) == ["used", "not_used", "not_used"]
    inputs = line.derivation["consolidated:current"]["inputs"]
    assert [i.get("reason") for i in inputs] == [None, "not_selected", "not_selected"]
    assert [i["selected"] for i in inputs] == [True, True, False], "the engine's flags, verbatim"
    assert w["formula"].startswith("max(")


def test_a_min_rung_that_chose_zero_adds_up():
    """CP_ZERO: the smaller of a fixed nought and the overshoot. The trail used to show the
    overshoot counted — 549,975 beside a published 0."""
    line = _run_cascade("bs_ca__secur_and_other_fincl_assets_cp",
                        {"sub__fa_cp_intermediate_residual": "549975"})
    w = _working(line, "current")
    assert line.derivation["consolidated:current"]["method"] == "cascade:CP_ZERO"
    assert w["how"] == "adds_up" and w["sum"] == 0.0
    by_label = {t["label"]: t["status"] for t in w["terms"]}
    assert by_label["fixed number"] == "used"
    assert [s for label, s in by_label.items() if label != "fixed number"] == ["not_used"]


def test_the_prior_year_input_points_at_the_prior_years_cell():
    """A cascade input took its part's FIRST figure's provenance for every period, so last year's
    input opened this year's cell. The trail now carries each period's own cell beside it; the
    engine's `provenance` stays the first figure's."""
    ltp, _got = _ltp_with_the_shared_row()
    cur = {i["canonical_key"]: i for i in ltp.derivation["consolidated:current"]["inputs"]}
    prior = {i["canonical_key"]: i for i in ltp.derivation["consolidated:prior"]["inputs"]}
    assert cur[FVTPL]["period_provenance"]["bbox"]["x0"] == 0.6
    assert prior[FVTPL]["period_provenance"]["bbox"]["x0"] == 0.8
    assert prior[FVTPL]["provenance"]["bbox"]["x0"] == 0.6, "the engine's field moved"
    _f, contributions = derivation.merge_for_basis(ltp.derivation, "consolidated")
    (fvtpl,) = [c for c in contributions if c["canonical_key"] == FVTPL]
    assert fvtpl["source2"]["bbox"]["x0"] == 0.8


# ── the model's route ───────────────────────────────────────────────────────────────────────

def _cited(at, note, caption, current=None, prior=None):
    figures = {k: v for k, v in (("current", current), ("prior", prior)) if v is not None}
    return {"at": at, "note": note, "caption": caption, "figures": figures,
            "basis": "consolidated",
            "provenance": {"page_index": 9, "bbox": {"x0": 0.6, "y0": 0.1 * (at + 1),
                                                     "x1": 0.7, "y1": 0.1 * (at + 1) + 0.01}}}


def _llm_write(resolved, unresolved=(), signs=(), role="whole"):
    from app.stages.line_item_llm import LineItemLlmStage

    component = role == "component"
    figures, unverified = combine_terms(list(resolved), list(unresolved), list(signs), component,
                                        "current")
    doc = DocumentModel(filename="f.pdf")
    row = LineItem(source_label="x", canonical_key="bs_icon__probe")
    doc.line_items = [row]
    item = SimpleNamespace(key="bs_icon__probe", label="Probe")
    answer = SimpleNamespace(role=role, signs=list(signs), confidence=0.9, reason="")
    LineItemLlmStage()._write(doc, {"bs_icon__probe": row}, item, answer, list(resolved), figures,
                              PipelineContext(), unverified, unresolved=list(unresolved),
                              fallback_period="current")
    return row, figures


def test_a_whole_answer_counts_its_first_figure_and_the_rest_corroborate():
    """ICON's other advances: "note 13: Loans to employees" and "note 13: Total" both state the
    line's whole figure. The first is the figure and the second corroborates it — the trail used to
    add both, 108.40 beside a published 54.20."""
    row, _f = _llm_write([_cited(0, "13", "Loans to employees", "54.20", "48.65"),
                          _cited(1, "13", "Total", "54.20", "48.65")])
    cur, prior = _working(row, "current"), _working(row, "prior")
    assert (cur["sum"], prior["sum"]) == (54.20, 48.65)
    assert cur["how"] == prior["how"] == "adds_up"
    trail = row.derivation["consolidated:current"]
    second = trail["inputs"][1]
    assert (second["status"], second["reason"]) == ("not_used", "corroborates")
    assert all(i["counted"] is True for i in trail["inputs"])
    assert (trail["combine"], trail["op"]) == ("whole", "first")


def test_a_component_answer_keeps_each_terms_sign():
    """ICON's domestic receivables: the model said "Total less overseas" and the Total did not
    resolve, so the lone deduction is the published figure. The trail now shows it as a deduction
    and names the citation that was not found — it used to show +1,773.70 for −1,773.70."""
    row, figures = _llm_write([_cited(1, "10", "Trade receivables include", "1773.70")],
                              [{"at": 0, "note": "10", "caption": "Total", "why": "no row"}],
                              signs=[1, -1], role="component")
    trail = row.derivation["consolidated:current"]
    assert trail["result"] == "-1773.70"
    w = _working(row, "current")
    assert w["sum"] == -1773.70 and w["how"] == "adds_up"
    (only,) = trail["inputs"]
    assert (only["status"], only["sign"]) == ("deducted", -1)
    assert "deducted" not in only, "the face settlement skips a deducted input"
    (total,) = trail["unlocated"]
    assert (total["label"], total["status"], total["reason"]) == ("note 10: Total", "missing",
                                                                  "not_located")

    row, _f = _llm_write([_cited(0, "10", "Total", "13281.26", "12000"),
                          _cited(1, "10", "Overseas", "1773.70", "1500")],
                         signs=[1, -1], role="component")
    assert (_working(row, "current")["sum"], _working(row, "prior")["sum"]) == (11507.56, 10500.0)


def test_a_stated_amount_the_note_does_not_contain_is_listed_and_counted():
    """"500,000 less 120,000", the 120,000 not in the note's text. `combine_terms` counts it, and
    the trail now lists it — beside the inputs, never among them."""
    row, _f = _llm_write([_cited(0, "7", "Gross", "500000")],
                         [{"at": 1, "note": "7", "caption": "Less", "amount": "120000"}],
                         signs=[1, -1], role="component")
    trail = row.derivation["consolidated:current"]
    (stated,) = trail["unlocated"]
    assert {k: stated[k] for k in ("value", "sign", "used", "status", "reason", "located")} == {
        "value": "120000", "sign": -1, "used": "-120000", "status": "deducted",
        "reason": "stated_not_located", "located": False}
    assert len(trail["inputs"]) == 1
    assert _working(row, "current")["sum"] == 380000.0


def test_a_whole_answer_whose_first_citation_prints_one_year():
    """The first figure is decided PER YEAR: note 8 prints only last year's work in progress, so
    it is the prior figure and note 28 corroborates it; this year note 28 is the figure."""
    row, _f = _llm_write([_cited(0, "8", "WIP", None, "3108.77"),
                          _cited(1, "28", "Closing WIP", "3412.20", "3108.77")])
    prior = row.derivation["consolidated:prior"]["inputs"]
    assert [(i["status"], i.get("reason")) for i in prior] == [("used", None),
                                                              ("not_used", "corroborates")]
    cur = row.derivation["consolidated:current"]["inputs"]
    assert [(i["status"], i.get("reason")) for i in cur] == [("not_used", "no_figure"),
                                                            ("used", None)]
    assert cur[1]["used"] == "3412.20"


def test_a_models_citations_are_read_in_the_models_order():
    """A whole answer takes the first figure IN THE MODEL'S ORDER. Here that is a stated amount no
    row confirmed (position 0), ahead of a row that resolved (position 1). The rows were listed
    before the stated terms, so the working read "first(120; 100) = 100" — the first of which is
    120."""
    row, figures = _llm_write([_cited(1, "8", "Closing WIP", "120")],
                              [{"at": 0, "note": "8", "caption": "Work in progress",
                                "amount": "100"}])
    assert figures == {"current": Decimal("100")}
    w = _working(row, "current")
    assert w["how"] == "adds_up"
    assert derivation.working_text(w, lambda x: f"{x:,.0f}").startswith(
        "first(100; 120) = 100 — adds up.")
    _f, contributions = derivation.merge_for_basis(row.derivation, "consolidated")
    assert [c["label"] for c in contributions] == ["note 8: Work in progress",
                                                   "note 8: Closing WIP"]


# ── prose and note rows ─────────────────────────────────────────────────────────────────────

RD_1200 = "Depreciation of HK$1,200,000 is included in research and development expenses."
RD_1300 = "Amortisation of HK$1,300,000 is charged to research and development costs."


def test_a_prose_figure_divides_by_the_scale_it_records():
    """A sentence states its amount in full and the line publishes it in the statements' units.
    The trail records the division, so 1,200,000 + 1,300,000 ÷ 1,000 reads as the 2,500 shown."""
    from app.config import get_settings
    from app.core.models.line_item import UnitContext
    from app.stages.note_sourced import NoteSourcedStage

    doc = DocumentModel(filename="f.pdf")
    doc.unit_context = UnitContext(currency="HKD", scale_factor=Decimal(1000))
    doc.notes = [NotesTable(note_number="8", title="Property, plant and equipment",
                            source_pages=[42], source_text=f"{RD_1200} {RD_1300}")]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = _set("hk")
    NoteSourcedStage().run(doc, ctx)
    row = next(li for li in doc.line_items if li.canonical_key == "sub__rd_depreciation")
    (slot, trail), = row.derivation.items()
    basis, _, period = slot.partition(":")
    assert trail["scale"] == "1000" and trail["result"] == "2500"
    assert sum(Decimal(i["used"]) for i in trail["inputs"]) == Decimal("2500000")
    assert _working(row, period, basis)["how"] == "adds_up"
    assert all("stated in full in the sentence" in i["excerpt"] for i in trail["inputs"])

    from app.stages.line_item_llm import LineItemLlmStage

    llm_doc = DocumentModel(filename="f.pdf")
    llm_doc.unit_context = UnitContext(currency="HKD", scale_factor=Decimal(1000))
    held = LineItem(source_label="R&D depreciation", canonical_key="sub__rd_depreciation")
    cited = [{"at": 0, "prose": True, "note": "8", "figures": {"prose": "1200000"},
              "quote": RD_1200, "caption": None, "provenance": {"page_index": 42}}]
    LineItemLlmStage._write_prose(held, llm_doc, Decimal("1200000"), cited, PipelineContext(),
                                  SimpleNamespace(key="sub__rd_depreciation", label="R&D"))
    (slot, trail), = held.derivation.items()
    basis, _, period = slot.partition(":")
    assert trail["scale"] == "1000" and trail["result"] == "1200"
    assert _working(held, period, basis)["how"] == "adds_up"


def test_alternatives_count_only_the_row_taken():
    """An `alternatives` rollup takes the first row. The rows it did not take said "printed twice"
    in the inspector; they now say another row was taken, and add nothing to the working."""
    hits = [note_sourced.NoteRowHit(key="k", note_number="8", note_title="PPE", caption=caption,
                                    matched_by="depreciation", value=None, amount=Decimal(amount),
                                    basis="consolidated", period="current")
            for caption, amount in (("Depreciation", "300"), ("Depreciation charge", "300"))]
    ((_slot, (amount, inputs)),) = note_sourced.resolve(hits, "alternatives").items()
    assert [i["status"] for i in inputs] == ["used", "not_used"]
    assert inputs[1]["reason"] == "alternative"
    trail = note_sourced.trail(rollup="alternatives", item_label="k", amount=amount, inputs=inputs,
                               display={"op": "first"})
    assert (trail["version"], trail["op"]) == (2, "first")
    assert inputs[0]["excerpt"] == "note 'PPE' row matched /depreciation/"
    w = derivation.working({"consolidated:current": trail}, "consolidated", "current",
                           shown=Decimal("300"))
    assert w["how"] == "adds_up" and w["sum"] == 300.0


# ── the reader's verdicts ───────────────────────────────────────────────────────────────────

_RUN_OUTPUT = ROOT.parent / "_run_output"


@pytest.mark.parametrize("name, slots", [("LaiSun_FY2025_en__result.json", 8),
                                         ("SunCreate_FY2024_zh__result.json", 7)])
def test_a_payload_without_the_arithmetic_is_never_judged(name, slots):
    """Runs stored before the trail recorded its arithmetic: half their slots do not add up by the
    naive counted-and-signed rule, for reasons the payload cannot express. They are shown, and never
    called wrong."""
    path = _RUN_OUTPUT / name
    if not path.exists():
        pytest.skip(f"{path} is not checked out")
    stored = json.loads(path.read_text(encoding="utf-8"))
    rows = stored.get("rows") if isinstance(stored, dict) else stored
    got = derivation.audit(rows)
    assert got["counts"]["slots"] == slots
    assert got["counts"]["not_recorded"] == slots
    assert got["problems"] == []


def _v2(result: str, used: str) -> dict:
    return derivation.build(method="cascade:P1", formula="a", result=result,
                            inputs=[{"label": "a", "value": used, "counted": True,
                                     "used": used, "status": "used"}],
                            flags=["rung:P1"], display={"op": "sum"})


def test_an_analyst_edit_and_a_removed_figure_are_explained():
    store = {"consolidated:current": _v2("500", "500")}
    assert derivation.working(store, "consolidated", "current", shown=520,
                              edited=True)["how"] == "edited"

    withheld = derivation.working(store, "consolidated", "current", shown=None,
                                  flags=["shared_figure_removed:kept_on_x"])
    assert withheld["how"] == "withheld"
    assert withheld["reason"] == "shared_figure_removed:kept_on_x"

    def row(flags):
        return {"canonical_key": "sub__x", "flags": flags, "derivation": store,
                "values": [{"basis": "consolidated", "period_label": "current", "value": None}]}

    assert derivation.audit([row(["shared_figure_removed:kept_on_x"])])["problems"] == []
    (problem,) = derivation.audit([row([])])["problems"]
    assert (problem["key"], problem["how"]) == ("sub__x", "withheld")

    # …and a trail whose inputs do not reach its own result is always a problem.
    broken = {"consolidated:current": _v2("500", "400")}
    assert derivation.working(broken, "consolidated", "current", shown=500)["how"] == "differs"


def test_every_trail_on_the_models_route_adds_up():
    """The spy model's route on the shipped HK set — two parts citing one note total — through the
    serializer the screens read."""
    from app.api.routes.extractions import _serialize_rows
    from tests.spy_line_item_llm import SpyLineItemLlm
    from tests.test_a_note_row_is_counted_once import BARE, OTHER as BARE_OTHER, _run_both_stages

    shipped = _set("hk")
    doc = _run_both_stages(shipped, SpyLineItemLlm(only={BARE_OTHER, BARE}))
    got = derivation.audit(_serialize_rows(doc))
    assert got["problems"] == [], got["problems"]
    assert got["counts"]["adds_up"] >= 1, got["counts"]


def _stored_runs() -> list[str]:
    raw = os.environ.get("FINEX_TRAIL_RUNS", "")
    return [p for p in raw.split(os.pathsep) if p]


@pytest.mark.parametrize("path", _stored_runs() or ["<unset>"])
def test_every_trail_in_a_stored_run_adds_up(path):
    """OVER A CORPUS, when one is named: ``FINEX_TRAIL_RUNS=a/rows.json:b/rows.json``. Each file is
    a run's served rows (or a dict carrying them under ``rows``). The measurement runs this over
    every run it makes."""
    if path == "<unset>":
        pytest.skip("FINEX_TRAIL_RUNS names no stored run")
    stored = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    rows = stored.get("rows") if isinstance(stored, dict) else stored
    got = derivation.audit(rows)
    assert got["problems"] == [], (got["counts"], got["problems"][:10])


def test_the_page_label_counts_from_one():
    """The settlement fixtures' notes page is sheet index 1, which a reader calls page 2."""
    ltp, _got = _ltp_with_the_shared_row()
    _f, contributions = derivation.merge_for_basis(ltp.derivation, "consolidated")
    assert {c["src"] for c in contributions if c["canonical_key"] == FVTPL} == {"p.2"}


def test_a_folio_reaches_the_trail_as_it_reaches_every_other_citation():
    """`_prov_dict` gives every value its printed folio; the trail's inputs were served raw."""
    from app.api.routes.extractions import _derivation_with_folios

    store = {"consolidated:current": {"inputs": [
        {"label": "a", "provenance": {"page_index": 185}, "period_provenance": None},
        {"label": "b", "provenance": {"page_index": 3, "printed_page": "iv"}}],
        "unlocated": [{"label": "c", "provenance": {"page_index": 185}}]}}
    served = _derivation_with_folios(store, {185: "184"}.get)
    inputs = served["consolidated:current"]["inputs"]
    assert inputs[0]["provenance"]["printed_page"] == "184"
    assert inputs[0]["period_provenance"] is None
    assert inputs[1]["provenance"]["printed_page"] == "iv"
    assert served["consolidated:current"]["unlocated"][0]["provenance"]["printed_page"] == "184"
    assert "printed_page" not in store["consolidated:current"]["inputs"][0]["provenance"], (
        "the stored trail is the engine's and is not changed")
    assert _derivation_with_folios(store, None) is store
