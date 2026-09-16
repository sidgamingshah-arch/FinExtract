"""A FACE LINE ASKS FOR A SECTION, THE WAY A NOTE LINE ASKS FOR A NOTE.

NEW FILE -> backend/tests/test_face_section_grouping.py

WHY THIS EXISTS. A line read off the face of a statement selects no note, so `note_sets` produces
no entry for it and `plan_requests` falls it through as a request of its own — measured on the
shipped set, 377 of the 506 asked-about lines, one request each. Each of those requests carries an
empty note block, and the reply contract's own advice to a model in that position is to answer with
an empty `sources`. There is nothing in the request to locate the figure IN.

What such a line needs supplied is the SECTION of the face it may be claimed under, and a section
block is exactly as expensive to repeat as a note block. `group_by_note_set`'s docstring gives the
rule: the supplied context is what a request pays for, so lines needing the SAME context amortise
one copy. Measured on the shipped set those 377 lines occupy FOURTEEN `(statement, section)`
groups, and only two hold a single line.

WHAT THESE TESTS HOLD. That the pairs come off the line's own gate and carry statement TOKENS
rather than enum reprs; that the default mode is untouched; that grouping never loses or duplicates
a line; and that a line sharing no context is never grouped — 39 of the shipped lines name neither
a statement nor a section, and one request for all 39 would save nothing while buying the whole
cross-influence hazard the `"none"` default exists to avoid.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services.line_item_requests import (_by_section, _sections_of_item, asked_about,
                                             plan_requests)

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(autouse=True)
def _restore_grouping():
    """`get_settings` IS `@lru_cache`D, SO THE SETTINGS OBJECT IS SHARED WITH THE WHOLE SUITE.

    Mutating `llm_request_grouping` and leaving it mutated is invisible here and fatal elsewhere:
    the first version of this file left the last parametrised mode in place, and
    `test_note_tag_gate.py::test_no_request_is_spent_on_a_line_with_no_note_tag` then counted
    requests under a grouping nobody asked for. It passed alone and failed in the suite, which is
    the worst way for a test to be wrong.
    """
    st = get_settings()
    was = st.extraction.llm_request_grouping
    try:
        yield
    finally:
        st.extraction.llm_request_grouping = was


def _plans(shipped, mode):
    st = get_settings()
    st.extraction.llm_request_grouping = mode
    return plan_requests(shipped, [], st)


@pytest.mark.parametrize("mode", ["none", "identical", "similar", "manual"])
def test_every_asked_about_line_is_still_in_exactly_one_plan(shipped, mode):
    """THE INVARIANT THE CALLER DEPENDS ON, re-asserted here because this change rewrote the
    fallback loop that used to guarantee it by construction."""
    want = {i.key for i in shipped.items if asked_about(i)}
    keys = [k for p in _plans(shipped, mode) for k in p.keys]
    assert len(keys) == len(set(keys)), "a line is in two plans"
    assert set(keys) == want, "a line is in no plan"


def test_the_default_mode_is_untouched(shipped):
    """`"none"` means one request per line and the reason is in `config`: without the per-line
    baseline there is no telling help from bleed. Grouping is opted into, never inherited."""
    plans = _plans(shipped, "none")
    assert all(len(p.keys) == 1 for p in plans), "the default grouped something"
    assert len(plans) == len({i.key for i in shipped.items if asked_about(i)})


def test_face_lines_collapse_onto_their_sections(shipped):
    """The saving, stated as a number so a regression reads as one."""
    grouped = [p for p in _plans(shipped, "identical") if len(p.keys) > 1 and p.sections]
    assert len(grouped) >= 10, f"only {len(grouped)} shared section groups"
    biggest = max(grouped, key=lambda p: len(p.keys))
    assert len(biggest.keys) > 50, f"largest section group is only {len(biggest.keys)} lines"
    assert all(len(p.sections) == 1 for p in grouped), "a shared group supplies two contexts"


def test_a_statement_travels_as_its_token_not_its_repr(shipped):
    """`str(StatementType.PROFIT_AND_LOSS)` is the repr, and the repr would reach the request as
    the name of the statement to supply — a token nothing recognises."""
    for plan in _plans(shipped, "identical"):
        for statement, _section in plan.sections:
            assert "StatementType" not in statement, statement
            assert statement == statement.lower(), statement


def test_a_line_sharing_no_context_is_never_grouped(shipped):
    """39 of the shipped lines name neither a statement nor a section. One request for all 39
    amortises nothing — there is no block to share — and buys the cross-influence hazard whole."""
    by_key = {i.key: i for i in shipped.items if asked_about(i)}
    loose = [k for k, i in by_key.items() if not _sections_of_item(i)]
    assert loose, "the fixture no longer exercises this case"
    groups = _by_section(list(by_key), by_key)
    for key in loose:
        holding = [g for g, ks in groups.items() if key in ks]
        assert len(holding) == 1 and groups[holding[0]] == [key], (
            f"{key} was grouped with {groups[holding[0]]}")


def test_a_line_scoped_to_two_sections_keeps_its_own_request(shipped):
    """It belongs in neither group's context alone, and the first would supply it half of what its
    own gate allows — a request that cannot answer the question it asks."""
    by_key = {i.key: i for i in shipped.items if asked_about(i)}
    multi = [k for k, i in by_key.items() if len(_sections_of_item(i)) > 1]
    if not multi:
        pytest.skip("the shipped set has no line scoped to two (statement, section) pairs")
    groups = _by_section(list(by_key), by_key)
    for key in multi:
        holding = [ks for ks in groups.values() if key in ks]
        assert holding and holding[0] == [key], f"{key} shares a request: {holding}"
