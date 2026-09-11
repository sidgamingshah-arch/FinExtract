"""Which line items share a request — the reader `llm_request_grouping` never had.

NEW FILE -> backend/tests/test_request_grouping.py

THE SETTING WAS DECLARED AND READ BY NOTHING. `extraction.llm_request_grouping` and
`llm_group_similarity` configured a decision no code was making, because requests are row-driven:
`mapping.match_batch` takes printed rows chunked by (statement, basis, period) and asks which
concept each row is. `services.line_item_requests.plan_requests` is the reader, and these are the
properties a caller may depend on.

THE ONE THAT MATTERS MOST is the partition: every line item the model is asked about appears in
exactly one plan. Without it a mode change would silently drop a line (asked about in `none`, in no
group under `manual`) and the only symptom would be a figure that stopped appearing.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import get_settings
from app.schemas.line_items import LineItemSet, RequestGroup, load_line_item_set
from app.services import line_item_requests
from app.services.line_item_requests import RequestPlan, asked_about, coverage, plan_requests

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _settings(mode: str, similarity: float = 0.8):
    s = get_settings()
    s.extraction.llm_request_grouping = mode
    s.extraction.llm_group_similarity = similarity
    return s


# ── the population ────────────────────────────────────────────────────────────────────────────

def test_the_population_is_the_lines_the_model_is_asked_about(shipped):
    """`asked_about` has to be the SAME boundary as `mapping._llm_withheld`, or a plan carries a
    line no request can answer. Two exclusions, and `extraction_mode` is deliberately not one of
    them: `extract_or_derive` lines ARE asked about, and eight of the nine derived parents declare
    `extract` while never being asked about."""
    derived = [i for i in shipped.items if str(i.type) == "derived"]
    residual = [i for i in shipped.items if str(i.value_scope) == "exclusive_residual"]
    assert derived and residual, "the shipped set no longer has the cases under test"

    assert not any(asked_about(i) for i in derived)
    assert not any(asked_about(i) for i in residual)
    # …and the mode is not consulted: a derivable line is asked about.
    derivable = [i for i in shipped.items
                 if str(i.extraction_mode) == "extract_or_derive" and str(i.type) != "derived"]
    assert derivable and all(asked_about(i) for i in derivable)


# ── the partition, in every mode ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["none", "identical", "similar", "manual"])
def test_every_asked_about_line_is_in_exactly_one_plan(shipped, mode):
    """THE PROPERTY A CALLER DEPENDS ON. Without it, changing the mode drops a line and the only
    symptom is a figure that stops appearing."""
    plans = plan_requests(shipped, [], _settings(mode))
    expected = {i.key for i in shipped.items if asked_about(i)}

    seen: list[str] = [k for plan in plans for k in plan.keys]
    assert sorted(seen) == sorted(expected), mode
    assert len(seen) == len(set(seen)), f"{mode}: a line item is in two plans"


def test_no_plan_names_a_derived_parent_or_a_residual(shipped):
    for mode in ("none", "identical", "similar", "manual"):
        named = {k for plan in plan_requests(shipped, [], _settings(mode)) for k in plan.keys}
        by_key = {i.key: i for i in shipped.items}
        assert not [k for k in named if str(by_key[k].type) == "derived"], mode
        assert not [k for k in named
                    if str(by_key[k].value_scope) == "exclusive_residual"], mode


def test_none_is_one_request_per_line(shipped):
    plans = plan_requests(shipped, [], _settings("none"))
    assert all(len(plan.keys) == 1 for plan in plans)
    assert not any(plan.shared for plan in plans)


# ── the manual master ─────────────────────────────────────────────────────────────────────────

def _with_groups(shipped, groups: list[RequestGroup]) -> LineItemSet:
    return shipped.model_copy(update={"request_groups": groups})


def test_a_declared_group_becomes_one_request_and_keeps_its_name(shipped):
    """The authored name travels, which is the point of authoring one: "Depreciation, one note" in
    a run log is a decision a reader can check, where "group 3" is not."""
    parts = [i.key for i in shipped.items
             if getattr(i, "parent", "") == "is_pl__deprec_and_impairment_oper_exp"][:4]
    assert len(parts) == 4
    cfg = _with_groups(shipped, [RequestGroup(name="Depreciation, one note", members=parts)])

    plans = plan_requests(cfg, [], _settings("manual"))
    mine = [p for p in plans if p.name == "Depreciation, one note"]
    assert len(mine) == 1
    assert sorted(mine[0].keys) == sorted(parts)
    assert mine[0].shared


def test_a_part_built_master_is_usable(shipped):
    """THE FALLBACK IS THE FEATURE. A master naming one group out of seventy-seven lines is the
    normal state of one being built; a mode that only asked about the named lines would make
    authoring the first group a regression."""
    parts = [i.key for i in shipped.items
             if getattr(i, "parent", "") == "is_pl__deprec_and_impairment_oper_exp"][:3]
    cfg = _with_groups(shipped, [RequestGroup(name="one group", members=parts)])

    plans = plan_requests(cfg, [], _settings("manual"))
    expected = {i.key for i in shipped.items if asked_about(i)}

    assert {k for p in plans for k in p.keys} == expected
    assert sum(1 for p in plans if p.shared) == 1
    assert len(plans) == 1 + (len(expected) - len(parts))


def test_manual_over_an_empty_master_behaves_as_none(shipped):
    """Stated rather than left to be discovered: an empty master is the state of every shipped set,
    and it must degrade rather than fail."""
    manual = plan_requests(_with_groups(shipped, []), [], _settings("manual"))
    none = plan_requests(shipped, [], _settings("none"))
    assert {p.keys for p in manual} == {p.keys for p in none}


# ── the refusals ──────────────────────────────────────────────────────────────────────────────

def test_an_unknown_member_is_refused(shipped):
    """A typo would silently shrink the group — an author would see a group of four asking about
    three, with nothing saying which was dropped."""
    with pytest.raises(ValueError) as exc:
        _with_groups(shipped, [RequestGroup(name="g", members=["sub__nope"])]).model_validate(
            _with_groups(shipped, [RequestGroup(name="g", members=["sub__nope"])]).model_dump())
    assert "sub__nope" in str(exc.value)


def test_a_key_in_two_groups_is_refused(shipped):
    """Both requests would claim the line and the second answer would overwrite the first, with
    nothing recording that a contest happened."""
    part = next(i.key for i in shipped.items if asked_about(i) and getattr(i, "parent", ""))
    raw = _with_groups(shipped, [RequestGroup(name="a", members=[part]),
                                 RequestGroup(name="b", members=[part])]).model_dump()
    with pytest.raises(ValueError) as exc:
        LineItemSet.model_validate(raw)
    assert part in str(exc.value)


@pytest.mark.parametrize("kind", ["derived", "residual"])
def test_a_member_the_model_is_never_asked_about_is_refused(shipped, kind):
    """Refused rather than dropped, for the reason `llm_only_if_note_tagged` is refused rather than
    ignored: a member that silently does nothing is worse than a message."""
    if kind == "derived":
        key = next(i.key for i in shipped.items if str(i.type) == "derived")
    else:
        key = next(i.key for i in shipped.items if str(i.value_scope) == "exclusive_residual")
    raw = _with_groups(shipped, [RequestGroup(name="g", members=[key])]).model_dump()

    with pytest.raises(ValueError) as exc:
        LineItemSet.model_validate(raw)
    assert key in str(exc.value)


# ── coverage, the number that says whether a master is finished ───────────────────────────────

def test_coverage_counts_named_against_asked_about(shipped):
    """A master covering 19 of 77 and one covering all 77 look identical as a list of groups."""
    assert coverage(shipped) == (0, len([i for i in shipped.items if asked_about(i)]))

    parts = [i.key for i in shipped.items
             if getattr(i, "parent", "") == "is_pl__deprec_and_impairment_oper_exp"][:5]
    named, total = coverage(_with_groups(shipped, [RequestGroup(name="g", members=parts)]))
    assert named == 5 and total > 5


# ── the note set a plan carries ───────────────────────────────────────────────────────────────

def test_a_plans_notes_are_deduplicated_by_number(shipped):
    """A note split across pages arrives as several tables carrying one number — measured, note 4
    of the reference filing arrives as seven fragments — so a set of four hits is routinely one
    note seen four times, and two lines whose hits are fragments of the same note need the SAME
    note."""
    class _Hit:
        def __init__(self, note):
            self.note, self.title, self.score = note, "", 1.0

    assert line_item_requests._note_keys(
        [_Hit("6"), _Hit("6"), _Hit("6"), _Hit("14")]) == ("6", "14")
    assert line_item_requests._note_keys([]) == ()


def test_a_plan_is_hashable_so_a_caller_can_dedupe_them():
    plan = RequestPlan(name="n", keys=("a", "b"), notes=("6",))
    assert len({plan, RequestPlan(name="n", keys=("a", "b"), notes=("6",))}) == 1
    assert plan.shared and not RequestPlan(name="n", keys=("a",), notes=()).shared
