"""A derived parent is never the model's to answer — keyed on its TYPE, not on its mode.

WHAT WAS WRONG. `_llm_withheld` read `extraction_mode != "extract"`, on the reasoning that a line
the framework can work out for itself is withheld by its mode. That is true of 41 of the shipped
concepts and it left one hole, which is the hole this file exists to keep shut:

    is_pl__sales_revenues     type: derived     extraction_mode: extract     8 cascade rungs

Both declarations are correct and they say different things. `derived` says a declared cascade
produces the figure — `services.line_items.evaluate` branches on `type`, so the published number is
the cascade's whatever any caption or any model says. `extract` says a filing that PRINTS the
subtotal must have the printed row read, which is not academic: revenue publishes 4,995,768 off the
face of the reference filing with no rung firing at all. Keyed on the mode alone, revenue was the
one derived parent the model was offered — and the measured consequence is in this suite already
(`test_failing_provider_degrades`): asked about a row the alias tier had answered correctly at
4,995,768, a live run mapped it to nothing and a low-precedence rung filled the line with
2,609,259 instead.

SO THE TWO AXES ARE SEPARATED. `OntologyMapping.item_type` carries the configuration's `type` into
the matcher's view and `_computed_parent` is the set it names:

    _computed_parent = {type == "derived"}                          its figure is its cascade's
    _unmatchable     = _locked | {extraction_mode == "derive"} | _computed_parent

AND `_computed_parent` IS IN `_unmatchable`, which it deliberately was not when this file was
written. That changed by instruction: "derived parents will never be offered to LLM — for one last
time — this is locked forever. Even on deterministic route there is no semantic needed on them or
aliases or anything at all." So a derived parent is refused the model's answer AND every caption
tier, and it took four agreeing indexes before `"TURNOVER"` stopped resolving to one.

WHAT THAT COST AND HOW IT WAS PAID. Revenue IS printed on the face of every HK filing, and refusing
the printed row would have swept the top line of the income statement into a residual — measured,
4,995,768 became 2,609,259. The answer was not to unlock the parent but to move the RECOGNITION
down to the part that reads the face: `sub__face_principal_revenue` carries the aliases now, and
the face figure enters through cascade rung P1 with a trail saying which disclosure it came from.

THE OFFER BOUNDARY IS NO LONGER A MATCHER SET AT ALL. `_llm_withheld` is retired with the row
request it served; `line_item_requests.asked_about` and `LineItemDef._never_asked` are the one
spelling, and they answer for three declarations — `type: derived`, `alias_matching: disabled`,
`extraction_mode: derive`.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import get_settings
from app.schemas.line_items import LineItemDef, load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# The two the mode alone could not withhold, and the one it still does.
REVENUE = "is_pl__sales_revenues"                        # derived + extract  -> the hole
DEPREC = "is_pl__deprec_and_impairment_oper_exp"         # derived + extract  -> flipped, measured
PERIODS = "statement_setup_controls__periods"            # derived + derive   -> left alone


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())


# ── the declaration reaches the matcher ───────────────────────────────────────────────────────

def test_every_definitions_type_is_carried_into_the_working_view(shipped):
    """`type` is not in `SAME`, so it is written by name in `_concept_of`. If that line is ever
    dropped the gate below degrades silently to the old mode-only rule — every concept would
    default to `extracted` and every derived parent would be offered again."""
    view = build_working_view(shipped)
    carried = {m.canonical_key: m.item_type for m in view.mappings}
    declared = {i.key: str(i.type) for i in shipped.items}

    assert carried == declared, "the working view and the configuration disagree about `type`"


def test_the_types_the_shipped_set_declares(shipped):
    """The population the gate acts on, counted so a change to it is a decision.

    THE COUNT MOVED, AND THIS IS THE DECISION IT RECORDS. 33 lines were `extracted` while three
    other declarations said they were subtotals — `role: subtotal`/`total` with a template
    `rollup` naming 2 to 34 children each, `unit_of_account: "subtotal"` on exactly those 33, and
    `extraction_mode: extract_or_derive`. They are `calculated` now, so the three-value type means
    what it says: read off the page, worked out by arithmetic, or produced by a declared cascade.

    Measured before the flip: every one of the 66 published subtotal cells across the two
    reference filings is unchanged, because `evaluate` reports the document's figure for a
    computed line that names no terms and the template's own rollup check is what compares it
    against the components.
    """
    from collections import Counter
    counts = Counter(str(i.type) for i in shipped.items)

    # TEN, not nine: `sub__fa_cp_intermediate_residual` is the securities line's overshoot
    # diagnostic, a derived line computed from its own cascade and published nowhere.
    # 10 derived lines, not 13: the three `sub__rp_find_*` lines became `extracted` when the
    # spec collapsed their feeders into them — they now read the notes themselves instead of
    # cascading over nine parts.
    assert counts["derived"] == 10, counts
    assert counts["calculated"] == 33, counts
    # 486 since the two 营业外 face parts were added — both `extracted`, because each is a
    # printed row read off the income statement rather than a figure computed from others.
    # 488 since the two direct-method TAX parts were added, `extracted` for the same reason: each
    # is a row printed on the face of the cash-flow statement.
    assert counts["extracted"] == 488, counts
    assert sum(counts.values()) == 531  # 531 since the two direct-method TAX face parts split the template's single Income Taxes Paid(Direct) column, as the two 营业外 parts before them split Other Non-Operating Inc(Exp)
    # AND THE DISCRIMINATOR THE SECTION ROLL-UPS READ IS UNTOUCHED. `rollups.section_members` keys
    # on `unit_of_account == "subtotal"` and nothing else; flattening it alongside the type takes
    # all 20 sections to `no_reported_subtotal`, so the two must not move together.
    assert sum(1 for i in shipped.items
               if str(getattr(i, "unit_of_account", "") or "") == "subtotal") == 33


# ── the gate ──────────────────────────────────────────────────────────────────────────────────

def test_a_derived_parent_is_withheld_from_the_model_whatever_its_mode(shipped, matcher):
    """THE POINT OF THE FILE. All nine, including the one declaring `extract`."""
    from app.services.line_item_requests import asked_about

    by_key = {i.key: i for i in shipped.items}
    derived = [i.key for i in shipped.items if str(i.type) == "derived"]
    assert REVENUE in derived and DEPREC in derived

    for key in derived:
        assert key in matcher._computed_parent, key
        # NO REQUEST NAMES IT — `asked_about` is where the offer boundary lives now that
        # `_llm_withheld` and the candidate payload are retired with the row request.
        assert not asked_about(by_key[key]), f"{key} is a derived parent and is asked about"
        # …AND NO CAPTION REACHES IT EITHER, which is the half the instruction added.
        assert key in matcher._unmatchable, key


def test_the_mode_alone_would_have_missed_revenue(shipped, matcher):
    """Named rather than implied: this asserts the hole was real, so a future reader can see why
    the gate reads two fields instead of one."""
    by_mode = {m.canonical_key for m in build_working_view(shipped).mappings
               if m.extraction_mode != "extract"}

    from app.services.line_item_requests import asked_about

    revenue = next(i for i in shipped.items if i.key == REVENUE)
    assert REVENUE not in by_mode, "revenue declares `extract`, so the mode cannot withhold it"
    assert not asked_about(revenue), "…and the type must"
    assert "derived" in (revenue._never_asked() or ""), "and the reason must name the type"


def test_a_printed_derived_parent_is_refused_and_its_part_reads_the_face_instead(matcher, shipped):
    """THIS TEST USED TO ASSERT THE OPPOSITE, and the reversal is the instruction: "even on
    deterministic route there is no semantic needed on them or aliases or anything at all". So a
    derived parent is refused by every caption tier too, not only by the request.

    THE COST WAS REAL AND IS PAID ELSEWHERE. Revenue is printed on the face of every HK filing, and
    refusing the printed row on its own swept the top line of the income statement into a residual
    — measured, 4,995,768 became 2,609,259. The recognition moved DOWN to the part that reads the
    face rather than the lock being loosened, so the face figure still arrives, through cascade
    rung P1, with a trail naming the disclosure it came from.
    """
    assert REVENUE in matcher._unmatchable
    assert DEPREC in matcher._unmatchable
    got = matcher.match("Sales(Revenues)", statement="profit_and_loss", section=None)
    assert got is None or got.canonical_key != REVENUE, (
        "a caption resolved to a derived parent; the lock needs all four indexes to agree")

    # AND THE PART CARRIES IT. Without this the assertion above is just a lost figure.
    part = next((i for i in shipped.items if i.key == "sub__face_principal_revenue"), None)
    assert part is not None, "the face-reading part is gone, so nothing reads the printed row"
    assert any((a or "").strip() for a in (part.aliases or [])), (
        "the part carries no alias, so the face figure has no route in")
    assert getattr(part, "parent", "") == REVENUE


def test_the_one_derived_line_that_no_caption_may_reach(matcher):
    """`statement_setup_controls__periods` keeps `derive`, and this is the measurement that decided
    it: it is a priority-90 control whose keyword hints are `months`, `year ended`, `six months`,
    `quarter`, `period` — words half an income statement's captions contain. `derive` is what keeps
    every caption off it. See `scripts/mark_derived_extract.py`."""
    assert PERIODS in matcher._unmatchable
    assert PERIODS in matcher._computed_parent


# ── the configuration side says the same thing ────────────────────────────────────────────────

def test_never_asked_names_the_type_before_the_mode():
    """`LineItemDef._never_asked` is the configuration's spelling of the same gate, and it returns
    the REASON so a refusal can say which field to change. A derived line is answered by its type
    even where its mode would also have withheld it — naming the mode there would send an author
    to change a field that is not what withheld the line."""
    derived = LineItemDef(key="k", type="derived", implemented_by="x")
    assert "derived" in (derived._never_asked() or "")

    # A `derive` MODE is the other true answer: the framework computes the figure and no printed
    # caption may claim it, so there is no source for a request to locate.
    computed = LineItemDef(key="k", extraction_mode="derive")
    assert "derive" in (computed._never_asked() or "")

    # `extract_or_derive` IS ASKED ABOUT, and this assertion used to say the reverse. It means
    # "printed on some filings, arithmetic on others" — and a request asks WHERE a figure is
    # printed, so a figure sitting in a note is locatable whether or not the arithmetic could also
    # reach it. Withholding it was the old, too-wide boundary that answered for 41 concepts.
    derivable = LineItemDef(key="k", extraction_mode="extract_or_derive")
    assert derivable._never_asked() is None

    plain = LineItemDef(key="k")
    assert plain._never_asked() is None


@pytest.mark.parametrize("field,value", [("llm_only_if_note_tagged", True),
                                         ("note_selection", "any")])
def test_the_two_model_facing_flags_are_refused_on_a_derived_line(field, value):
    """Both flags are about what the model is asked and with what, so both have to test the same
    gate. Refused rather than ignored — a flag silently doing nothing is worse than a message."""
    with pytest.raises(ValueError) as exc:
        LineItemDef(key="k", type="derived", implemented_by="x", **{field: value})

    assert field in str(exc.value), "the refusal must name the control the author must change"
    assert "derived" in str(exc.value)


def test_the_shipped_derived_lines_declare_extract_except_the_one_measured_case(shipped):
    """`extraction_mode` on a derived line decides only whether a PRINTED row may fill it where no
    rung resolves, and the answer is yes — so the expectation is `extract`. One exception, with its
    measured reason in `scripts/mark_derived_extract.py`: `PERIODS` is the priority-90 control
    whose keyword hints are words half an income statement contains.

    `bs_nca__secur_and_other_fincl_assets_ltp` was the second exception until its rungs declared
    `outranks_printed` — see `test_a_reconstructing_rung_outranks_a_printed_figure`."""
    modes = {i.key: str(i.extraction_mode) for i in shipped.items if str(i.type) == "derived"}

    assert {k for k, v in modes.items() if v != "extract"} == {PERIODS}, modes


# ── which figure wins: the printed row or the resolved rung ────────────────────────────────────

def test_a_reconstructing_rung_outranks_a_printed_figure_and_a_restating_one_does_not(shipped):
    """THE DECLARATION THAT SETTLES THE CONTEST, and why it is per RUNG rather than per line.

    Both of these are `type: derived` and they want opposite answers, measured on the reference
    filings:

      * every `LTP_*` rung computes the non-current portion of the in-scope financial-asset notes
        less derivatives, less other receivables, less equity-method investments. No balance sheet
        prints that subtraction, so a caption binding this line found a different quantity —
        128,412 against the rung's 788,507 — and the rung is the answer.
      * revenue's `P1` IS the face ("主营业务收入 reported on the face of the income statement"), and
        `P5`-`P8` are axis reconstructions that should SUM to it. A printed figure here is what the
        cascade's own top rung was looking for; letting `P6` displace it published 2,609,259 — one
        industry segment — over the face's 4,995,768.

    Rung ORDER and rung MAGNITUDE each separate those two cases on these two filings and neither
    means anything, which is the whole reason this is authored rather than inferred.
    """
    by_key = {i.key: i for i in shipped.items}

    ltp = by_key["bs_nca__secur_and_other_fincl_assets_ltp"]
    assert ltp.cascade, "the line under test has no cascade"
    assert all(r.outranks_printed for r in ltp.cascade), [r.id for r in ltp.cascade]

    revenue = by_key[REVENUE]
    assert revenue.cascade
    assert not any(r.outranks_printed for r in revenue.cascade), [
        r.id for r in revenue.cascade if r.outranks_printed]


def test_outranking_is_off_by_default_so_an_unexamined_cascade_changes_nothing(shipped):
    """Off is the behaviour that was in force, so a cascade nobody has read the rungs of keeps it.
    Only the four `LTP_*` rungs are on, out of every rung in the set."""
    from app.schemas.line_items import CascadeRung
    assert CascadeRung(id="X").outranks_printed is False

    on = [(i.key, r.id) for i in shipped.items for r in (i.cascade or []) if r.outranks_printed]
    # TWO LINES CARRY IT, and both reconstruct a quantity no balance sheet prints.
    #
    #   securities LTP  — the non-current portion of the in-scope financial-asset notes, less
    #                     derivatives, less other receivables, less equity-method investments, less
    #                     the current line's overshoot. All four rungs.
    #   related-party   — MAX_VALID(Find 1, Find 2, Find 3): related-party amounts extracted from
    #     LTP             WITHIN four receivable captions, net of entrusted loans. A caption binding
    #                     this line found something else entirely, which is why the rung is the
    #                     answer and not the printed row.
    assert on == [("bs_nca__due_from_related_parties_ltp", "MAX_VALID")] + [
        ("bs_nca__secur_and_other_fincl_assets_ltp", f"LTP_P{n}") for n in (1, 2, 3, 4)], on
