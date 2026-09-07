"""`min_confidence_to_auto_accept`: a per-concept accept bar that NOTHING enforces.

WHAT WAS MEASURED, before anything was written. The field is declared twice — `LineItemDef`
(line_items:363) and `OntologyMatcher`'s own `ConceptMapping` (ontology:150) — defaults to 0.85 in
both, and 0 of the 475 shipped definitions override it. No engine reads it. The incumbent decides
acceptance on the GLOBAL `settings.extraction.auto_accept_confidence` (0.80) at mapping:1682,
:1863, :1919 and :2219, which is per-run and cannot express "this one line needs more agreement
than the rest"; the only other mention in the tree is `ontology_projection.SAME`, a passthrough
list whose own comment claims every field in it "is read by live code or by the LLM payload".

So the field is a declared control that reads as a control while being inert — the same class of
defect as concept families resolving to nothing. What is NOT established is what it should DO,
because no consumer has ever applied it: whether it competes with the global knob, replaces it, or
gates only the semantic tier is unauthored. Enforcing a guess would move figures on the strength
of a reading of a field name.

WHAT THE MATCHER THEREFORE DOES, and what this file pins:

  surfaces      `LineItemMatch.min_confidence_to_auto_accept` carries the matched definition's own
                declared bar, so a caller applies the policy it can actually state.
  compares      `clears_auto_accept_bar` is the comparison — the number is config, the comparison
                is code — and it is read-only, touching neither `needs_review` nor the key.
  enforces      nothing. `test_the_matcher_does_not_yet_enforce_the_bar` fails if that changes
                silently, and `test_the_bar_is_read_from_the_definition_not_a_constant` fails if
                the field goes back to being read by nothing.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import LineItemDef, LineItemSet, load_line_item_set
from app.services.line_item_matching import LineItemMatcher

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


def _matcher(*items: dict) -> LineItemMatcher:
    return LineItemMatcher(LineItemSet.model_validate({"items": list(items)}))


@pytest.fixture(scope="module")
def shipped() -> LineItemMatcher:
    return LineItemMatcher(load_line_item_set(json.loads(SEED.read_text(encoding="utf-8"))))


# ── the census that the "surface, do not enforce" decision rests on ──────────────────────────────

def test_nothing_in_the_tree_applies_the_field_to_a_decision():
    """The finding, stated as a test: the INCUMBENT does not read it either.

    Not a grep for tidiness — it is the whole justification for surfacing rather than enforcing.
    `OntologyMatcher` gates on `extraction.auto_accept_confidence`, so if a per-concept consumer
    ever appears this test fails and the matcher should be made equivalent to it instead of
    carrying an unenforced bar.
    """
    from app.services import mapping

    src = pathlib.Path(mapping.__file__).read_text(encoding="utf-8")
    assert "min_confidence_to_auto_accept" not in src, (
        "services.mapping now reads the per-concept bar; port its behaviour into "
        "LineItemMatcher instead of only surfacing the number")
    assert "auto_accept_confidence" in src        # the global knob it uses instead


def test_no_shipped_definition_overrides_the_default(shipped):
    """0 of 475, which is why surfacing the bar changes no figure today.

    Pinned because the comment on `clears_auto_accept_bar` rests on it: with every bar at 0.85 and
    the deterministic path emitting only 1.0 / 0.95 / 0.6 / 0.0, the comparison and `needs_review`
    cannot disagree. Override one and that stops being true — which is the moment somebody has to
    decide who enforces it, so this failing is a prompt, not a nuisance.
    """
    bars = [d.min_confidence_to_auto_accept for d in shipped.set.items]

    assert len(bars) == 475
    assert sorted(set(bars)) == [0.85]
    assert LineItemDef(key="k", label="L").min_confidence_to_auto_accept == 0.85


# ── surfaced on the result ───────────────────────────────────────────────────────────────────────

def test_an_exact_match_carries_the_declared_bar():
    m = _matcher({"key": "a", "label": "Trade receivables",
                  "min_confidence_to_auto_accept": 0.9})
    got = m.match("Trade receivables")

    assert got.confidence == 1.0
    assert got.min_confidence_to_auto_accept == 0.9
    assert got.clears_auto_accept_bar


def test_a_rule_hit_carries_the_declared_bar():
    m = _matcher({"key": "a", "regex_hints": ["turnover"],
                  "min_confidence_to_auto_accept": 0.9})
    got = m.match("Total turnover for the period")

    assert got.confidence == 0.95
    assert got.min_confidence_to_auto_accept == 0.9
    assert got.clears_auto_accept_bar


def test_the_bar_is_read_from_the_definition_not_a_constant():
    """THE TEST THAT FAILS IF THE FIELD GOES BACK TO BEING READ BY NOTHING.

    Two sets identical but for the bar must give two different answers. A matcher that ignored the
    field — or hardcoded 0.85, or read the schema default — would pass every other test in this
    file and fail this one, which is the regression the package exists to prevent.
    """
    strict = _matcher({"key": "a", "regex_hints": ["turnover"],
                       "min_confidence_to_auto_accept": 0.98})
    lax = _matcher({"key": "a", "regex_hints": ["turnover"],
                    "min_confidence_to_auto_accept": 0.5})
    caption = "Total turnover for the period"

    assert strict.match(caption).min_confidence_to_auto_accept == 0.98
    assert lax.match(caption).min_confidence_to_auto_accept == 0.5
    # And the comparison moves with it: one 0.95 rule hit, two verdicts.
    assert not strict.match(caption).clears_auto_accept_bar
    assert lax.match(caption).clears_auto_accept_bar


def test_the_shipped_set_surfaces_its_own_declared_bar(shipped):
    """Read off the registry rather than hardcoded here, so overriding one bar cannot make the
    surfaced number and the declaration drift apart unnoticed."""
    for caption, statement, banner in (
            ("total assets", "balance_sheet", "CURRENT ASSETS"),
            ("revenue", "profit_and_loss", None),
            ("固定資產", "balance_sheet", "NON-CURRENT ASSETS")):
        got = shipped.match(caption, statement, banner)
        assert got.resolved, f"{caption!r} no longer resolves; pick another probe"
        declared = shipped.by_key[got.key].min_confidence_to_auto_accept
        assert got.min_confidence_to_auto_accept == declared
        assert got.clears_auto_accept_bar == (got.confidence >= declared)


# ── absent where there is no definition to read it from ──────────────────────────────────────────

def test_an_unmatched_caption_carries_no_bar():
    """None, not 0.85. An unresolved caption names no definition, so reporting the schema default
    would hand a caller a threshold nobody authored for a line nobody identified."""
    got = _matcher({"key": "a", "aliases": ["Debtors"]}).match("Goodwill")

    assert got.key is None
    assert got.min_confidence_to_auto_accept is None
    assert not got.clears_auto_accept_bar         # nothing resolved, so nothing is acceptable


def test_a_forbidden_tie_carries_no_bar():
    """Two claimants, two declared bars, no key — so there is no single bar to report.

    Deliberately not the max or the min of the two: a review item lists both keys in `tied` and a
    caller that wants their bars can read them off the registry, where a synthesised number would
    look authored.
    """
    m = _matcher({"key": "a", "aliases": ["Notes payable"], "match_priority": 80,
                  "confusable_with": ["b"], "min_confidence_to_auto_accept": 0.9},
                 {"key": "b", "aliases": ["Notes payable"], "match_priority": 80,
                  "confusable_with": ["a"], "min_confidence_to_auto_accept": 0.7})
    got = m.match("Notes payable")

    assert sorted(got.tied) == ["a", "b"]
    assert got.min_confidence_to_auto_accept is None
    assert not got.clears_auto_accept_bar


# ── surfaced, NOT enforced ───────────────────────────────────────────────────────────────────────

def test_the_matcher_does_not_yet_enforce_the_bar():
    """A bar of 0.98 over a 0.95 rule hit fails the comparison and STILL does not set review.

    This is the one place the two verdicts can disagree, and the disagreement is left visible on
    purpose: wiring the bar into `needs_review` decides that a per-concept bar overrides the global
    0.80 the incumbent accepts on, which no consumer has ever asserted. Whoever authors that policy
    changes this test on purpose and says why.
    """
    m = _matcher({"key": "a", "regex_hints": ["turnover"],
                  "min_confidence_to_auto_accept": 0.98})
    got = m.match("Total turnover for the period")

    assert got.key == "a"                         # still the answer
    assert got.confidence == 0.95
    assert not got.needs_review                   # the matcher's own routing is unchanged
    assert not got.clears_auto_accept_bar         # and the caller can see it fell short


def test_the_bar_never_changes_which_line_item_wins():
    """Ordering is priority then declaration order; the accept bar is not a tie-break.

    Checked because the natural mistake when a threshold arrives is to prefer the candidate that
    clears it, which would let a lax bar outrank a real priority and move a figure.
    """
    m = _matcher({"key": "lax", "aliases": ["Deposits"], "match_priority": 10,
                  "min_confidence_to_auto_accept": 0.1},
                 {"key": "strict", "aliases": ["Deposits"], "match_priority": 90,
                  "min_confidence_to_auto_accept": 0.99})
    got = m.match("Deposits")

    assert got.key == "strict"
    assert got.min_confidence_to_auto_accept == 0.99
