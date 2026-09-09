"""Folding a repeated policy out of the candidate block must not change what any concept says.

The fold is a SIZE optimisation on the largest part of the request. The way an optimisation like
this goes wrong is not by crashing — it is by handing a concept a policy its author never wrote,
which then changes a mapping decision and is invisible in the output. Every test here guards that
one boundary.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

FOLD = OntologyMatcher._fold_shared_fields


def _c(key: str, **fields) -> dict:
    return {"canonical_key": key, "label": key, **fields}


def _effective(candidates: list[dict], defaults: dict, field: str) -> list:
    """What each candidate's policy actually IS after the fold — the default unless it overrides."""
    return [c.get(field, defaults.get(field)) for c in candidates]


def test_the_prevailing_value_is_stated_once_and_the_exception_stays_inline():
    before = [_c("a", decomposition_rule="never fabricate"),
              _c("b", decomposition_rule="never fabricate"),
              _c("c", decomposition_rule="fixed priority order")]
    after, defaults = FOLD(before)
    assert defaults == {"decomposition_rule": "never fabricate"}
    assert "decomposition_rule" not in after[0] and "decomposition_rule" not in after[1]
    assert after[2]["decomposition_rule"] == "fixed priority order"


def test_no_concepts_effective_policy_changes():
    """The property the whole fold rests on, asserted as a round trip rather than by inspection."""
    before = [_c("a", decomposition_rule="x"), _c("b", decomposition_rule="x"),
              _c("c", decomposition_rule="y"), _c("d", decomposition_rule="x")]
    after, defaults = FOLD(before)
    assert _effective(after, defaults, "decomposition_rule") == ["x", "x", "y", "x"]


def test_a_field_missing_from_one_candidate_is_never_folded():
    """THE OVER-ASSERTION GUARD, and the only way this optimisation could change a decision. A
    default lifted over a SILENT candidate hands it a policy the rulebook never gave it — and on
    the shipped set 84 of 475 items carry no `decomposition_rule` at all, so silence in a block is
    the normal case, not a corner one."""
    before = [_c("a", decomposition_rule="x"), _c("b", decomposition_rule="x"),
              _c("c")]
    after, defaults = FOLD(before)
    assert defaults == {}
    assert [c.get("decomposition_rule") for c in after] == ["x", "x", None]


def test_an_empty_string_counts_as_silence():
    """A field present but empty is not a policy. Treating "" as a value would let the fold name it
    the prevailing one and then strip the concepts that actually stated something."""
    before = [_c("a", decomposition_rule=""), _c("b", decomposition_rule="x"),
              _c("c", decomposition_rule="x")]
    after, defaults = FOLD(before)
    assert defaults == {}


def test_a_tie_has_no_prevailing_value_and_is_left_alone():
    """Two values at two apiece: naming either as the default would be arbitrary, and would make the
    request depend on dict ordering rather than on the rulebook."""
    before = [_c("a", decomposition_rule="x"), _c("b", decomposition_rule="x"),
              _c("c", decomposition_rule="y"), _c("d", decomposition_rule="y")]
    after, defaults = FOLD(before)
    assert defaults == {}
    assert [c["decomposition_rule"] for c in after] == ["x", "x", "y", "y"]


def test_a_value_used_once_is_not_a_default():
    before = [_c("a", decomposition_rule="x"), _c("b", decomposition_rule="y"),
              _c("c", decomposition_rule="z")]
    _after, defaults = FOLD(before)
    assert defaults == {}


def test_a_short_candidate_list_is_left_alone():
    """Below three candidates the fold saves nothing and costs locality."""
    before = [_c("a", decomposition_rule="x"), _c("b", decomposition_rule="x")]
    after, defaults = FOLD(before)
    assert defaults == {} and after == before


@pytest.mark.parametrize("field", ["canonical_key", "label", "definition", "include", "exclude"])
def test_identifying_fields_are_never_foldable(field):
    """A concept's own criteria belong to that concept whatever the rest of the list says. Folding
    `definition` or `exclude` would state one concept's criteria as though they governed the others
    — and two concepts sharing a definition is a configuration error to surface, not a redundancy
    to compress."""
    assert field not in OntologyMatcher._FOLDABLE_FIELDS


def test_the_shipped_rulebook_actually_folds_on_a_section_scoped_batch():
    """LIVENESS. A fold that never fires on real data is a mechanism that reads as working and does
    nothing — the failure this codebase has hit repeatedly (three payload caps declared in config
    and read by no code). Real runs send section-scoped subgroups, so that is the batch shape
    measured here, and the assertion is that the block genuinely gets smaller."""
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    matcher = OntologyMatcher(build_working_view(st), locale="en", settings=get_settings())
    keys = matcher._by_priority(list(matcher._by_key))
    candidates = matcher._concept_payload(keys)[:16]
    assert len(candidates) >= OntologyMatcher._FOLD_MIN_CANDIDATES
    folded, defaults = OntologyMatcher._fold_shared_fields(candidates)
    assert defaults, "nothing folded on the shipped rulebook"
    before = len(json.dumps(candidates, ensure_ascii=False))
    after = len(json.dumps(folded, ensure_ascii=False)) + len(json.dumps(defaults,
                                                                        ensure_ascii=False))
    assert after < before, (before, after)
    # And every concept still says exactly what it said.
    for field in defaults:
        assert _effective(folded, defaults, field) == [c.get(field) for c in candidates]
