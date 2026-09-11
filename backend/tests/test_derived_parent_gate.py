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
the matcher's view, `_computed_parent` is the set it names, and the offer boundary is the union:

    _unmatchable     = _locked | {extraction_mode == "derive"}      no caption may reach it
    _computed_parent = {type == "derived"}                          its figure is its cascade's
    _llm_withheld    = {extraction_mode != "extract"} | _computed_parent | _locked

and the consequence worth stating is that `_computed_parent` is deliberately NOT in `_unmatchable`.
A derived parent that is printed stays reachable by every deterministic tier; what it is refused is
the model's answer.
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
    """The population the gate acts on, counted so a change to it is a decision."""
    from collections import Counter
    counts = Counter(str(i.type) for i in shipped.items)

    assert counts["derived"] == 9, counts
    assert counts["extracted"] == 530, counts
    assert sum(counts.values()) == 539


# ── the gate ──────────────────────────────────────────────────────────────────────────────────

def test_a_derived_parent_is_withheld_from_the_model_whatever_its_mode(shipped, matcher):
    """THE POINT OF THE FILE. All nine, including the one declaring `extract`."""
    derived = [i.key for i in shipped.items if str(i.type) == "derived"]
    assert REVENUE in derived and DEPREC in derived

    offered = {c["canonical_key"]
               for c in matcher._concept_payload(matcher._by_priority(list(matcher._by_key)))}
    for key in derived:
        assert key in matcher._computed_parent, key
        assert key in matcher._llm_withheld, key
        assert key not in offered, f"{key} is a derived parent and was offered to the model"


def test_the_mode_alone_would_have_missed_revenue(shipped, matcher):
    """Named rather than implied: this asserts the hole was real, so a future reader can see why
    the gate reads two fields instead of one."""
    by_mode = {m.canonical_key for m in build_working_view(shipped).mappings
               if m.extraction_mode != "extract"}

    assert REVENUE not in by_mode, "revenue declares `extract`, so the mode cannot withhold it"
    assert REVENUE in matcher._llm_withheld, "…and the type must"


def test_a_printed_derived_parent_stays_readable_by_the_caption_tiers(matcher):
    """The other half, and the reason `_computed_parent` is not folded into `_unmatchable`. Revenue
    IS printed on the face of every HK filing; refusing the printed row would sweep the top line of
    the income statement into a residual."""
    assert REVENUE not in matcher._unmatchable
    assert DEPREC not in matcher._unmatchable
    got = matcher.match("Sales(Revenues)", statement="profit_and_loss", section=None)
    assert got is not None and got.canonical_key == REVENUE


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

    derivable = LineItemDef(key="k", extraction_mode="extract_or_derive")
    assert "extract_or_derive" in (derivable._never_asked() or "")

    plain = LineItemDef(key="k")
    assert plain._never_asked() is None


@pytest.mark.parametrize("field,value", [("llm_only_if_note_tagged", True),
                                         ("note_selection", "patterns")])
def test_the_two_model_facing_flags_are_refused_on_a_derived_line(field, value):
    """Both flags are about what the model is asked and with what, so both have to test the same
    gate. Refused rather than ignored — a flag silently doing nothing is worse than a message."""
    with pytest.raises(ValueError) as exc:
        LineItemDef(key="k", type="derived", implemented_by="x", **{field: value})

    assert field in str(exc.value), "the refusal must name the control the author must change"
    assert "derived" in str(exc.value)


def test_the_shipped_derived_lines_declare_extract_except_the_two_measured_cases(shipped):
    """`extraction_mode` on a derived line decides only whether a PRINTED row may fill it where no
    rung resolves, and the answer is yes — so the expectation is `extract`. Two exceptions, each
    with a measured reason recorded in `scripts/mark_derived_extract.py`: flipping
    `bs_nca__secur_and_other_fincl_assets_ltp` publishes 128,412 where the cascade publishes
    788,507, and `PERIODS` is the priority-90 control above."""
    modes = {i.key: str(i.extraction_mode) for i in shipped.items if str(i.type) == "derived"}
    exceptions = {"bs_nca__secur_and_other_fincl_assets_ltp", PERIODS}

    assert {k for k, v in modes.items() if v != "extract"} == exceptions, modes
