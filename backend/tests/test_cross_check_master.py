"""WHICH PRINTED SUBTOTALS ARE COMPARED, AND HOW TIGHTLY, IS CONFIGURATION.

NEW FILE -> backend/tests/test_cross_check_master.py

WHAT WAS ALREADY THERE. The config plan called for building a cross-check master, warning that
without one "the system loses the ability to notice its arithmetic disagrees with the filing". It
has that ability and uses it: `_calculated_checks` reads the printed subtotal off the filing,
compares it against what the line's components come to, and raises a `calculated_mismatch` card
carrying both figures, the difference, every component and fix text naming what to look at. That
runs on every review queue today.

WHAT WAS MISSING WAS THE DECLARATION. The comparison ran over whatever the template declared a
rollup for, at a tolerance hardcoded as `_CALC_TOLERANCE = 0.5` in a routes module — so the
configuration said nothing about which of its own figures are checked against the page, and
tightening that was a code change. It matters more now that `terms` lives in the configuration: the
config declares 31 of the 33 formulas, and this is the list saying which of them the printed page
gets a vote on.

TWO PROPERTIES, AND THE SECOND IS THE ONE THAT COULD HURT.

  * A line the master marks is compared, at the master's tolerance.
  * AN EMPTY MASTER MEANS EVERY CALCULATED LINE, not none. "Nobody has declared this yet" must not
    read as "do not check anything" — a set with no master, or a review built from a run that named
    no configuration, gets exactly the behaviour that existed before the master did. A master that
    marked nothing would otherwise switch every comparison off in silence, which is the worst
    possible failure for a check whose whole job is to notice a disagreement.
"""
from __future__ import annotations

import json
import pathlib

from app.api.routes.documents import _CALC_TOLERANCE, _CrossCheck
from app.schemas.line_items import load_line_item_set

SEED = (pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def _shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")))


def test_the_shipped_master_marks_every_calculated_line() -> None:
    """The declaration of CURRENT behaviour, which is what makes adopting it safe: the comparison
    already ran on all of them, so writing them down changes nothing."""
    st = _shipped()
    calculated = {d.key for d in st.items
                  if str(getattr(d.type, "value", d.type)) == "calculated"}

    assert len(calculated) == 33, len(calculated)
    assert set(st.cross_check_master.keys) == calculated, (
        sorted(calculated ^ set(st.cross_check_master.keys)))
    assert st.cross_check_master.tolerance == _CALC_TOLERANCE


def test_an_empty_master_compares_everything() -> None:
    """THE DEFAULT THAT MUST NOT INVERT. A caller with no configuration — a review built from a run
    that named none, or a test driving the builders directly — keeps the old behaviour."""
    empty = _CrossCheck()

    assert empty.compares("bs_ca__total_assets")
    assert empty.compares("anything at all")
    assert empty.tolerance == _CALC_TOLERANCE


def test_a_marked_line_is_compared_and_an_unmarked_one_is_not() -> None:
    check = _CrossCheck(["bs_ca__total_assets"], tolerance=25.0)

    assert check.compares("bs_ca__total_assets")
    assert not check.compares("bs_cl__total_liabilities")
    assert check.tolerance == 25.0


def test_the_master_is_read_off_the_set_and_survives_a_missing_one() -> None:
    """`_CrossCheck.of` is what the routes call with whatever `_line_item_set_for_run` returned —
    which is `None` for a run that named no configuration, and for one whose stored definition no
    longer loads."""
    resolved = _CrossCheck.of(_shipped())
    assert len(resolved.keys) == 33
    assert resolved.compares("bs_ca__total_assets")

    fallback = _CrossCheck.of(None)
    assert not fallback.keys
    assert fallback.compares("bs_ca__total_assets"), "a missing set must not switch checking off"


def test_a_key_naming_no_line_is_refused_at_load() -> None:
    """The same rule `others_master` gets, for the same reason: a key naming nothing is a comparison
    that silently stopped happening, and nothing else would ever say so."""
    import pytest
    from pydantic import ValidationError

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    raw["cross_check_master"]["keys"] = list(raw["cross_check_master"]["keys"]) + ["no_such_line"]

    with pytest.raises(ValidationError, match="no_such_line"):
        load_line_item_set(raw)
