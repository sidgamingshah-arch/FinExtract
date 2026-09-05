"""A bilingual caption reads as two phrases, and one activity is never another.

A filing that sets English and Chinese side by side in the label column lets the pair wrap
together, so merging the printed lines splices the languages into each other. Regrouping by script
fixes the caption — and, on the real filing, immediately created a second problem worth pinning
here: with each language contiguous, two cash-flow subtotal captions that differ by ONE word in
seven scored 0.92 against each other and the financing figure was filed under investing. Both
behaviours are tested together because the second only exists as a consequence of the first.
"""
from __future__ import annotations

import json
import pathlib

from app.core.models.geometry import BBox
from app.services.mapping import (
    EXCLUSIVE_VOCABULARIES, OntologyMatcher, _names_a_different_class)
from app.services.row_reconstruct import Word, _join_words, _regroup_scripts


def _w(text: str, x: float, y: float, w: float = 0.08, h: float = 0.012) -> Word:
    return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + w, y1=y + h))


def _caption(words: list[Word]) -> str:
    return _join_words(_regroup_scripts(words))


# --------------------------------------------------------------------------------------
# A wrapped bilingual caption
# --------------------------------------------------------------------------------------
def test_a_bilingual_caption_that_wrapped_reads_as_two_phrases():
    # Printed as:
    #   Share of other comprehensive      應佔合營公司其他
    #   income of joint ventures          全面收益
    # Read in printed order the two languages interleave and neither is a phrase any more.
    words = [
        _w("Share", 0.10, 0.30), _w("of", 0.17, 0.30), _w("other", 0.21, 0.30),
        _w("comprehensive", 0.28, 0.30), _w("應佔合營公司其他", 0.50, 0.30),
        _w("income", 0.10, 0.32), _w("of", 0.17, 0.32), _w("joint", 0.21, 0.32),
        _w("ventures", 0.28, 0.32), _w("全面收益", 0.50, 0.32),
    ]
    assert _caption(words) == (
        "Share of other comprehensive income of joint ventures 應佔合營公司其他全面收益")


def test_the_printed_order_of_the_languages_is_preserved():
    # A filing that prints Chinese first keeps Chinese first — this reorders runs, it does not
    # impose a language order.
    words = [
        _w("換算海外業務的", 0.10, 0.30), _w("Exchange", 0.40, 0.30), _w("differences", 0.50, 0.30),
        _w("匯兌差額", 0.10, 0.32), _w("on", 0.40, 0.32), _w("translation", 0.46, 0.32),
    ]
    assert _caption(words) == "換算海外業務的匯兌差額 Exchange differences on translation"


def test_a_caption_on_one_line_is_never_reordered():
    # "Goodwill (商譽) impairment" is in the order it was written. Regrouping it would move the
    # parenthetical to the end and break the sentence — so a single-line caption is left alone
    # whatever its script pattern.
    words = [_w("Goodwill", 0.10, 0.30), _w("(商譽)", 0.20, 0.30), _w("impairment", 0.28, 0.30)]
    assert _caption(words) == "Goodwill (商譽) impairment"
    # …and the ordinary bilingual single line is untouched too.
    two = [_w("Trade", 0.10, 0.30), _w("receivables", 0.17, 0.30), _w("貿易應收款項", 0.50, 0.30)]
    assert _caption(two) == "Trade receivables 貿易應收款項"


def test_a_single_script_caption_that_wrapped_is_untouched():
    words = [_w("Total", 0.10, 0.30), _w("assets", 0.17, 0.30),
             _w("less", 0.10, 0.32), _w("current", 0.17, 0.32), _w("liabilities", 0.25, 0.32)]
    assert _caption(words) == "Total assets less current liabilities"


def test_three_printed_lines_regroup_as_one_phrase_per_language():
    words = [
        _w("Net", 0.10, 0.30), _w("decrease", 0.16, 0.30), _w("現金及", 0.50, 0.30),
        _w("in", 0.10, 0.32), _w("cash", 0.14, 0.32), _w("現金等價物", 0.50, 0.32),
        _w("equivalents", 0.10, 0.34), _w("減少淨額", 0.50, 0.34),
    ]
    assert _caption(words) == "Net decrease in cash equivalents 現金及現金等價物減少淨額"


def test_han_runs_join_without_a_space():
    # Chinese is not written with spaces, and one inserted between the runs stops the caption
    # matching an alias the ontology lists verbatim.
    words = [_w("Cash", 0.10, 0.30), _w("現金及", 0.50, 0.30),
             _w("equivalents", 0.10, 0.32), _w("現金等價物", 0.50, 0.32)]
    assert "現金及現金等價物" in _caption(words)


# --------------------------------------------------------------------------------------
# One cash-flow activity is never another
# --------------------------------------------------------------------------------------
_INVESTING = "cf_cash_flow_from_investing_activities__net_cash_used_in_investing_activities"
_FINANCING = "cf_cash_flow_from_financing_activities__net_cash_from_financing_activities"


def test_a_financing_caption_may_not_reach_an_investing_concept():
    assert _names_a_different_class(
        _INVESTING, "Net cash flows used in financing activities 融資活動所用現金流量淨額") is True
    assert _names_a_different_class(
        _FINANCING, "Net cash flows used in financing activities") is False
    assert _names_a_different_class(
        _INVESTING, "Net cash flows from/(used in) investing activities") is False


def test_the_gate_only_fires_when_the_caption_names_exactly_one_activity():
    # No activity word at all: the ordinary tiers decide, as before.
    assert _names_a_different_class(_INVESTING,
                                    "Purchase of property, plant and equipment") is False
    # Two named: a genuinely combined line, left for the ordinary tiers to judge rather than
    # refused outright.
    assert _names_a_different_class(
        _INVESTING, "Cash flows from operating and investing activities") is False
    # A concept outside the vocabulary is never gated by it.
    assert _names_a_different_class("bs_current_assets__cash_and_cash_equivalents",
                                    "Cash used in financing activities") is False


def test_the_vocabulary_is_only_for_genuinely_exclusive_classes():
    # The gate cannot be overridden by evidence, so a merely-usually-true grouping here would
    # refuse correct mappings. IAS 7's three activities are the only entry that earns it.
    assert EXCLUSIVE_VOCABULARIES == (("operating", "investing", "financing"),)


def _reference_matcher() -> OntologyMatcher:
    from app.schemas.loader import load_ontology

    path = (pathlib.Path(__file__).resolve().parents[1]
            / "app/sample/templates/hkfrs_hk_china_ontology.json")
    return OntologyMatcher(load_ontology(json.loads(path.read_text(encoding="utf-8"))))


def test_the_two_cash_flow_subtotals_from_the_real_filing_reach_their_own_concepts():
    """The regression this pair caused, pinned against the reference ontology.

    Both captions are from a real HKFRS filing. They differ by one word in seven, which token
    similarity scores at 0.92 — so before the gate the financing subtotal's figure was filed under
    investing, investing then showed the two summed, and the financing line had none at all.
    """
    m = _reference_matcher()
    investing = m.match("Net cash flows from/(used in) investing activities "
                        "投資活動所得╱（所用）現金流量淨額")
    financing = m.match("Net cash flows used in financing activities 融資活動所用現金流量淨額")
    assert investing.canonical_key == _INVESTING
    # The financing caption must not land on the investing concept. Reaching its own concept is
    # the better outcome; reaching nothing is acceptable. Reaching investing is not.
    assert financing.canonical_key != _INVESTING
    assert financing.canonical_key in (_FINANCING, None)


def test_the_operating_subtotal_is_unaffected():
    m = _reference_matcher()
    res = m.match("Net cash flows from operating activities 經營活動所得現金流量淨額")
    assert res.canonical_key == (
        "cf_cash_flow_from_operating_activities__net_cash_from_operating_activities")
