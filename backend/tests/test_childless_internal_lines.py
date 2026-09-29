r"""AN ARITHMETIC LINE WITH NO CHILDREN OF ITS OWN IS NOW COMPUTED — and only where that is safe.

NEW FILE -> backend/tests/test_childless_internal_lines.py

THE GAP, and it was silent. `stages/note_sourced._fill_parents` evaluates a cascade only for a key
present in `children_of`, and `children_of` holds a key only if some configured child of it has a
figure. A configured arithmetic line with NO children was therefore never evaluated at all — its
cascade stored, shown on the configuration screen, versioned, and read on no run.

HOW IT SURFACED, measured while building the securities family. Five such lines (P1, P2, P3, an
intermediate and its residual) produced nothing, and because the balance-sheet line referencing them
took its term as `required`, the rung died and the constant fallback answered: the published figure
was 0.0 where the line's own arithmetic gives 3,467,958. Nothing said why.

WHY THE FIX IS NARROW, which is the whole of its safety. Measured over the shipped set, 31 of the 39
configured arithmetic lines have no children — and every one of the 31 is `namespace: "template"`,
`in_output: True`: the statement subtotals. Those are computed by `services/rollups.evaluate` from
the TEMPLATE's rollup tree, the single entry point the statement API, the Excel export and the KPI
layer all read. Computing them in the stage as well would be a second place computing one published
quantity — the exact failure `_fill_by_cascade`'s own comment warns about, on the arithmetic that
decides a figure. So the pass takes only lines that are off-template AND unpublished, and the first
test below pins that the shipped set has none of those except the one deliberately added.
"""
from __future__ import annotations

import json
import re
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.line_items import build
from app.services.working_view import build_working_view
from app.stages.note_sourced import NoteSourcedStage, _fill_childless_internal

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
RESIDUAL = "sub__fa_cp_intermediate_residual"
SIX = ["sub__fa_cp_fvtpl_note_total", "sub__fa_cp_fvtoci_note_total",
       "sub__fa_cp_afs_htm_note_total", "sub__fa_cp_debt_investments_note_total",
       "sub__fa_cp_investment_and_money_market_securities_note_total",
       "sub__fa_cp_other_fincl_assets_note_total"]
LEVEL3 = "sub__fa_cp_level_3_total"
MAIN = "bs_ca__secur_and_other_fincl_assets_cp"
LTP = "bs_nca__secur_and_other_fincl_assets_ltp"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


# ── THE BOUNDARY ──────────────────────────────────────────────────────────────────────────────

def test_the_template_subtotals_are_not_in_this_pass(shipped):
    """THE TEST THAT KEEPS THIS SAFE. Every childless arithmetic line other than the deliberate
    internal ones is a template subtotal, and those belong to `services/rollups` alone. If one ever
    became eligible here, two places would compute one published figure and the failure would be a
    number that disagrees with itself depending on which reader you ask."""
    kids = {i.parent for i in shipped.items if i.parent}
    childless = [i for i in shipped.items
                 if i.type in ("calculated", "intermediate", "derived")
                 and (i.cascade or i.terms) and i.key not in kids]
    assert childless, "the premise is gone — nothing is childless any more"

    eligible = [i for i in childless
                if str(i.namespace or "") == "internal" and not i.in_output]
    ineligible = [i for i in childless if i not in eligible]

    assert all(str(i.namespace or "") == "template" and i.in_output for i in ineligible), (
        "a childless arithmetic line is neither a template subtotal nor an internal line: "
        + str([(i.key, i.namespace, i.in_output) for i in ineligible
               if not (str(i.namespace or "") == "template" and i.in_output)][:5]))
    # FOUR DELIBERATE LINES, each an arithmetic line with no children of its own: the securities
    # overshoot residual, and the three related-party Finds that `MAX_VALID` chooses between. Pinned
    # by name rather than by count, so a FIFTH becoming eligible is a test failure someone has to
    # look at — that is the whole guard, because anything published must never enter this pass.
    # TWO DELIBERATE LINES: the securities overshoot residual, and Find 1.
    #
    # Find 2 and Find 3 left this population when the spec made them EXTRACT items reading the
    # notes themselves rather than cascading over nine feeders. FIND 1 CAME BACK, and on the
    # strength of what the balance sheet actually prints: the face never shows an "of which related
    # parties" split inside 其他应收款, so the spec's first reading is the TOTAL of the dedicated
    # related-party rows a filing prints — Due from related parties, Amounts due from fellow
    # subsidiaries, 应收关联方款项 — each of which is already a column of this template. That is
    # arithmetic over other lines, which is this pass, and it is why Find 1 shipped as a dead
    # declaration until now: `note_source: null`, `terms: []`, `cascade: []`, no aliases, no route,
    # not an `llm_focus_key`, so nothing in the pipeline could fill it.
    #
    # Pinned by name rather than by count, so a THIRD becoming eligible is a failure someone has to
    # look at — that is the whole guard.
    assert {i.key for i in eligible} == {RESIDUAL, "sub__rp_find_1"}, (
        f"the eligible population changed: {sorted(i.key for i in eligible)}")


def test_a_published_template_line_is_never_written_by_this_pass(shipped):
    """Asserted against the function rather than inferred from the list above: handed the whole
    shipped set and a document, it must not write a single `in_output` row."""
    doc = DocumentModel(filename="f.pdf")
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    # Give every leaf a figure, so every subtotal COULD be computed if the pass let it.
    for item in shipped.items:
        if item.type == "extracted":
            row = LineItem(source_label=item.label or item.key, canonical_key=item.key,
                           role=LineRole.LINE)
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                         value=Decimal("1"), value_raw=Decimal("1"),
                                         provenance=Provenance(page_index=1)))
            doc.line_items.append(row)
    before = {li.canonical_key for li in doc.line_items}
    _fill_childless_internal(shipped.items, {}, {li.canonical_key: li for li in doc.line_items},
                             doc, ctx)
    added = [li.canonical_key for li in doc.line_items if li.canonical_key not in before]
    by_key = {i.key: i for i in shipped.items}
    published = [k for k in added if by_key.get(k) is not None and by_key[k].in_output]
    assert not published, f"the pass wrote published rows: {published}"


# ── THE RESIDUAL ──────────────────────────────────────────────────────────────────────────────

def _run(shipped, figures: dict[str, int]):
    """One document carrying the given per-key figures, through the whole stage."""
    doc = DocumentModel(filename="f.pdf")
    for key, amount in figures.items():
        row = LineItem(source_label=key, canonical_key=key, role=LineRole.LINE)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=7)))
        doc.line_items.append(row)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    NoteSourcedStage().run(doc, ctx)
    return doc


def _figure(doc, key, period="current"):
    for li in doc.line_items:
        if li.canonical_key == key:
            for ev in (li.values or {}).values():
                if str(getattr(ev, "period_label", "") or "") == period and ev.value is not None:
                    return ev.value
    return None


def test_the_residual_is_stored_when_the_expression_goes_below_zero(shipped):
    """WHAT WAS ASKED FOR: "i still need the Intermediate residual as a separate item which is
    stored". Level 3 larger than the note totals drives the expression negative; the securities line
    falls to its zero rung and the residual records how far."""
    doc = _run(shipped, {SIX[0]: 1000, LEVEL3: 5000})

    assert _figure(doc, MAIN) == 0, "the main line did not fall to its zero rung"
    assert _figure(doc, RESIDUAL) == 4000, (
        "the residual is not stored: 0 - (1000 - 5000) = 4000")
    row = next(li for li in doc.line_items if li.canonical_key == RESIDUAL)
    assert any(f.startswith("computed_from_config:") for f in row.confidence.flags), (
        row.confidence.flags)


def test_the_residual_is_absent_when_the_expression_is_positive(shipped):
    """`refuse_negative` on its rung. 0 − a positive number is negative, so the rung is passed over
    and the line stays empty — which is the right answer: there was no overshoot to report."""
    doc = _run(shipped, {SIX[0]: 5000, LEVEL3: 1000})

    assert _figure(doc, MAIN) == 4000, "the main line should publish its own arithmetic"
    assert _figure(doc, RESIDUAL) is None, "a residual was stored where nothing overshot"


def test_the_residual_is_unresolved_when_no_note_was_found_at_all(shipped):
    """THE REASON THE CONSTANT 0 IS NOT A TERM. As a `required` term it would alone satisfy
    `_apply_terms`' base-present test, so a filing disclosing none of the in-scope notes would
    publish a bare 0 — asserting an overshoot of zero where nothing was measured. With the six as
    `any_of` at sign −1, the rung resolves only where at least one was found."""
    doc = _run(shipped, {LEVEL3: 5000})
    assert _figure(doc, RESIDUAL) is None, "a residual was stored with no note total behind it"


def test_the_residual_is_not_published(shipped):
    """It is a diagnostic. `in_output` false and off-template, so it reaches the inspector and the
    trail and never the statement or the export."""
    item = next(i for i in shipped.items if i.key == RESIDUAL)
    assert item.in_output is False
    assert str(item.namespace) == "internal"


def test_the_residual_is_a_part_and_is_unpinned(shipped):
    """WHY IT HAS A PARENT: a REPORTED line must be pinned to a statement and a PART must not
    (`test_line_item_gate`), so a diagnostic line like this has to be a part — and being a part does
    not make it a component, because the securities line declares a CASCADE and `_fill_parents`
    ignores `rollup` when one is present.

    THE PARENT NAMES IT IN ONE RUNG, CP_ZERO, AND ONLY AS A CONDITION. The rule is "if Find 1 −
    Find 3 is negative, this field is zero", and the residual is the line that resolves exactly
    then. No cycle: the residual is computed from the Find parts, never from the parent's figure,
    and CP_ZERO takes min(0, residual), so its magnitude never reaches the published number.
    """
    item = next(i for i in shipped.items if i.key == RESIDUAL)
    assert item.parent == MAIN
    assert item.statement is None and not item.section_scope, (
        "a part pinned to a statement or section loses the note that prints it")

    parent = next(i for i in shipped.items if i.key == MAIN)
    assert parent.cascade, "the parent must declare a cascade, or `rollup` would sum its children"
    naming = [r for r in parent.cascade if any(t.ref == RESIDUAL for t in r.terms)]
    assert [r.id for r in naming] == ["CP_ZERO"], [r.id for r in naming]
    zero = naming[0]
    assert zero.terms_op == "min" and any(t.const == 0.0 for t in zero.terms), (
        "CP_ZERO must be min(0, residual) — anything else lets the overshoot into the figure")
    assert not any(t.ref == MAIN for t in item.cascade[0].terms), "the residual reads its parent"

def test_the_residual_is_the_carry_forward_the_long_term_line_always_described(shipped):
    """WHAT THE RESIDUAL IS FOR: the Level 3 amount the current line could not absorb, carried to
    the long-term line as a DEDUCTION ("treat Find 1 as Find 3" on the current side). The current
    line names it only in CP_ZERO — see the test above."""
    by_key = {i.key: i for i in shipped.items}
    referrers = {i.key for i in shipped.items
                 if any(t.ref == RESIDUAL for rung in (i.cascade or []) for t in rung.terms)
                 or any(t.ref == RESIDUAL for t in (i.terms or []))}
    assert referrers == {LTP, MAIN}, sorted(referrers)

    # …as a DEDUCTION, and `required`, which is what makes the fallback work.
    using = [r for r in by_key[LTP].cascade
             if any(t.ref == RESIDUAL for t in r.terms)]
    assert len(using) == 1, [r.id for r in by_key[LTP].cascade]   # LTP_P1
    for rung in using:
        term = next(t for t in rung.terms if t.ref == RESIDUAL)
        assert term.sign == -1, f"{rung.id} adds the overshoot instead of deducting it"
        assert term.role == "required", (
            f"{rung.id} takes the carry-forward as optional; absent, the rung would then resolve "
            f"WITHOUT it and silently publish the no-carry-forward figure under a rung id that "
            f"says otherwise")

def test_the_long_term_line_falls_through_when_there_was_no_overshoot(shipped):
    """The common case: on every reference filing the current arithmetic is positive, so there is
    no residual and LTP_P2 — Find 1 LTP with no carry-forward — answers. Each Find 1 part is already
    net of derivatives, other receivables and associate investments, so nothing is deducted."""
    from app.services.line_items import evaluate

    ltp = next(i for i in shipped.items if i.key == LTP)
    base = {"sub__ltp_fvtpl_note_total": Decimal(1000), "sub__ltp_fvtoci_note_total": Decimal(50)}

    without = evaluate(ltp, dict(base))
    assert without.value == 1050 and without.rung_used == "LTP_P2", (
        without.value, without.rung_used)

    with_carry = evaluate(ltp, {**base, RESIDUAL: Decimal(200)})
    assert with_carry.value == 850 and with_carry.rung_used == "LTP_P1", (
        with_carry.value, with_carry.rung_used)

    # An overshoot larger than Find 1 LTP is passed over, and the no-carry-forward figure stands.
    too_big = evaluate(ltp, {**base, RESIDUAL: Decimal(5000)})
    assert too_big.value == 1050 and too_big.rung_used == "LTP_P2", (too_big.value, too_big.rung_used)


def test_every_long_term_rung_still_outranks_a_printed_figure(shipped):
    """A PROPERTY A REBUILD OF THIS CASCADE DESTROYED ONCE, caught by
    `test_derived_parent_gate`. Every LTP rung RECONSTRUCTS the non-current portion less three
    deductions, and no balance sheet prints that subtraction — so a caption binding this line found
    a different quantity, 128,412 against the rung's 788,507, and the rung is the answer. Dropping
    the flag lets the printed figure win on every filing, and nothing about the number says so."""
    ltp = next(i for i in shipped.items if i.key == LTP)
    # EVERY RECONSTRUCTING RUNG, which is every rung but `FROM_THE_FACE`. That one reconstructs
    # nothing — it reads the row the balance sheet prints, through a part holding the aliases this
    # `derived` column cannot carry itself — so it is the RESTATING case that
    # `test_derived_parent_gate`'s own name distinguishes, and a restating rung must not outrank the
    # printed figure it IS. An exact partition rather than a filter, so a RECONSTRUCTING rung
    # silently losing the flag still fails here.
    flags = {r.id: r.outranks_printed for r in ltp.cascade}
    assert flags.pop("FROM_THE_FACE") is False, flags
    assert flags and all(flags.values()), flags


# ── THE MAIN LINE'S TWO RUNGS ─────────────────────────────────────────────────────────────────

def test_the_main_line_floors_at_zero_and_says_which_rung_answered(shipped):
    """Find 1 − Find 3 below zero publishes ZERO — the rule: "if the answer is negative, treat Find
    1 as Find 3 and make this field zero". CP_ZERO therefore sits AHEAD of the face: an overshoot
    is an answer (zero), not an absence for the printed row to fill. The face stays last, for a
    filing whose notes give none of Find 1."""
    item = next(i for i in shipped.items if i.key == MAIN)
    assert [r.id for r in item.cascade] == ["CP_INTERMEDIATE", "CP_ZERO", "FROM_THE_FACE"]
    by_id = {r.id: r for r in item.cascade}
    assert by_id["CP_INTERMEDIATE"].refuse_negative is True

    from app.services.line_items import evaluate

    face = "sub__cp_face_trading_fincl_assets"
    # The residual is what the note stage computes from the same inputs: 5000 − 1000.
    ev = evaluate(item, {SIX[0]: Decimal(1000), LEVEL3: Decimal(5000), RESIDUAL: Decimal(4000),
                         face: Decimal(1000)})
    assert ev.value == 0 and ev.rung_used == "CP_ZERO", (ev.value, ev.rung_used)
    assert any("-4000" in r for r in ev.refused_rungs), ev.refused_rungs

    # No note gave Find 1 at all: no overshoot, so the printed row answers.
    ev = evaluate(item, {face: Decimal(1000)})
    assert ev.value == 1000 and ev.rung_used == "FROM_THE_FACE", (ev.value, ev.rung_used)

def test_a_missing_level_3_subtracts_nothing_rather_than_killing_the_rung(shipped):
    """What the removed `CP_P2_MISSING_LEVEL_3` rung existed for. Level 3 is an `adjustment`, so a
    filing printing no fair value hierarchy note simply does not deduct it — one rung instead of a
    second copy of the whole expression."""
    from app.services.line_items import evaluate

    item = next(i for i in shipped.items if i.key == MAIN)
    ev = evaluate(item, {SIX[0]: Decimal(1000)})
    assert ev.value == 1000
    assert ev.rung_used == "CP_INTERMEDIATE"


# ── THE NOTE AND ROW CONFIGURATION, and why it is shaped the way it is ───────────────────────
#
# THIS SECTION RECORDS A MEASUREMENT THAT ARGUES AGAINST A CHANGE, which is the only reason it is
# a test rather than a comment: the shape below looks like an oversight and is not, so an
# unsuspecting tidy-up of it would move real figures.
#
# THE REQUEST. "There are only 2-3 notes that will be there in total. Barring Fair value hierarchy
# note which identifies level 3, note identification logic config has to be same across, only the
# row identification has to be different." Both halves were built and measured on three real
# filings, and they cannot both hold on this corpus — because each category has its OWN note here,
# with the category name in the note's HEADING and a row inside it that just says "Total".
#
#   per-category NOTE config (what is shipped, and what these tests pin)
#       each child finds its own note; the generic `Total` row is unambiguous inside it.
#       2024 report: the FVTPL child reads 15,759,270 and the securities line publishes 3,467,958.
#
#   shared note config + the generic rows the six already had
#       every child sees every note and takes the same `Total` row, and `_apply_terms` sums `any_of`
#       with no de-duplication. Measured: all six read 15,759,270 on the 2024 report and 24,666 on
#       2025032802704 — published 106,846,932 and 147,996 against 3,467,958 and 24,666.
#
#   shared note config + per-category rows
#       the category row does not exist inside a note whose heading IS that category. Measured: the
#       FVTPL child read nothing on the 2024 report and the securities line published 0.0; on
#       2025032802704 the available-for-sale child matched some other row and read -495, a negative
#       asset.
#
# Where a filing genuinely prints two or three notes with the categories as rows, the shared note
# config plus per-category rows is the right shape. On these twelve filings it removes the figure.

def test_each_of_the_six_identifies_its_own_note(shipped):
    """BY MEANING, 2-3 TERMS PER LANGUAGE, NO TITLE REGEX. Each of the six declares its own
    `note_terms`, and a note two of them cover goes to ONE of them (`claimed_notes`), which is what
    keeps a line-item sum from reading the same rows twice."""
    from app.services.line_item_notes import by_meaning_only

    by_key = {i.key: i for i in shipped.items}
    terms = {k: tuple(by_key[k].note_source.note_terms) for k in SIX + [LEVEL3]}
    assert len(set(terms.values())) == len(terms), "two parts share their note terms"
    for k in SIX + [LEVEL3]:
        assert by_meaning_only(by_key[k]), f"{k} still carries a title regex"
        han = [t for t in terms[k] if re.search(r"[\u4e00-\u9fff]", t)]
        latin = [t for t in terms[k] if not re.search(r"[\u4e00-\u9fff]", t)]
        assert 2 <= len(latin) <= 3, (k, latin)
        assert 4 <= len(han) <= 6, (k, han)          # 2-3 each, Traditional and Simplified


def test_a_note_two_siblings_cover_is_read_by_one():
    """1966's note 26 is covered by the FVTPL part and, through 公允值計量 in its Chinese heading,
    by the Level 3 part. It goes to FVTPL, whose header score for it is higher."""
    from types import SimpleNamespace as NS

    from app.services.line_item_notes import claimed_notes

    notes = [NS(title="FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS 按公允值計量且其變動計入損益的金融資產",
                note_number="26", items=[]),
             NS(title="FAIR VALUE AND FAIR VALUE HIERARCHY OF FINANCIAL INSTRUMENTS 公允值及公允值層級",
                note_number="46", items=[]),
             NS(title="TRADE RECEIVABLES 應收貿易賬款", note_number="23", items=[])]

    def part(key, terms):
        return NS(key=key, parent=MAIN, label=key, definition="", aliases=[], aliases_i18n={},
                  note_source=NS(note_terms=terms, note_title_any=[], row_caption_any=["\\S"]))

    got = claimed_notes([part(SIX[0], ["financial assets at fair value through profit or loss"]),
                         part(LEVEL3, ["fair value hierarchy", "公允值計量"])], notes)
    assert got == {SIX[0]: {"26"}, LEVEL3: {"46"}}, got

def test_the_six_row_configs_are_generic_totals_and_that_is_deliberate(shipped):
    """THE ROW RULE IS THE PROMPT'S: the note's line items, less totals, derivatives, other
    receivables and the non-current portion — four vetoes, so there is no Find 2 to subtract."""
    by_key = {i.key: i for i in shipped.items}
    for k in SIX:
        src = by_key[k].note_source
        assert len(src.row_caption_any) <= 4 and len(src.row_caption_none) <= 4, k
        vetoes = " ".join(src.row_caption_none)
        for word in ("total", "derivative", "receiv", "non"):
            assert re.search(word, vetoes), (k, word)



def test_level_3_reads_a_different_note_entirely(shipped):
    """Level 3 comes from the fair value hierarchy note, which is not one of the category notes,
    and its row patterns name the level."""
    by_key = {i.key: i for i in shipped.items}
    l3 = by_key[LEVEL3].note_source
    assert any("hierarch" in t or "fair value" in t for t in l3.note_terms), l3.note_terms
    assert any("level" in p.lower() for p in l3.row_caption_any)
