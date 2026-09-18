"""WHAT THE CONFIGURATION MAY NOT BECOME — the invariants a vocabulary sweep must not break.

NEW FILE -> backend/tests/test_configuration_invariants.py

WHY THESE, AND WHY NOW. The set is being broadened caption by caption, and a broadening is the one
kind of edit that can quietly change the SHAPE of the configuration: a line added where a part was
meant, an alias given to a second claimant nothing can choose between, a part hung off a parent
that does not exist. None of those fails an existing test, and each of them is invisible in a diff
of a 1.1 MB JSON file.

THE TEMPLATE BOUNDARY IS THE HARD ONE, and it is a stated product constraint rather than a
preference: the output spread's columns are fixed, so a change may add a PART and never a LINE.
That is mechanically checkable — the template declares 480 canonical keys, every line item except
one is among them, and all 64 `sub__*` items are outside — so it is checked rather than trusted.

THE TIE COUNT IS A RATCHET, NOT A TARGET. 140 aliases today are claimed by two lines with the same
scope, the same `match_priority`, and no label owner to break it — which means declaration order
picks the winner and every other claimant is unreachable for that caption. The seven `is_retained`
movements were one instance and are fixed; the rest are pre-existing and are not this file's job to
resolve. What this file refuses is an INCREASE, because adding one is how a well-meant alias makes
an existing line unreachable.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.line_item_audit import (
    dangling_references, keys_outside_the_template, template_keys,
    unbreakable_ties as _unbreakable_ties, unsigned_terms,
)

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
TEMPLATE = TEMPLATES / "output_csv_hk_v1_template.json"

#: THE ONE LINE ITEM THAT IS NEITHER a template column nor a part is named in
#: `services.line_item_audit.NOT_A_COLUMN`, which `keys_outside_the_template` applies — named there
#: rather than tolerated by a rule, so it cannot grow a second member by accident.

#: Aliases claimed by two lines with nothing able to choose between them. A RATCHET — see the
#: module docstring. Lower it when ties are resolved; never raise it to make a change pass.
#:
#: 140 -> 69 when the `is_oci` section's shared vocabulary was resolved: five of its eight lines
#: carried the SECTION TOTAL's caption alongside their own, on one statement in one section at
#: equal priority, which was the largest unbreakable cluster in the set. The caption now belongs to
#: `is_oci__total_other_comprehensive_income` alone.
#:
#: 69 -> 8 when the six remaining generic captions were denied — the wealth-management carve-outs,
#: a related-party table's narrative column heading, "Trade payables" on the trade-AND-OTHER
#: aggregate, a shareholder loan claimed by a receivable, the hedging captions on the reserves
#: aggregate, and the opening words of "Presented in RMB'000". Each was a caption that could not
#: pick one of its claimants; see `services.spec_alias_curation`.
#:
#: 8 -> 0 when the last three clusters were split rather than denied, because unlike the six above
#: each caption DOES say which claimant it means: a finance-lease "proceeds" caption authored on the
#: repayments line as well (opposite signs), two related-party captions authored on the payables
#: aggregates the related-party amount is a part of, and one zh disposal list copied verbatim onto
#: both the investment-property and the associates line (four of the five name their disposal; the
#: generic 出售投资所得款项 went to the concept that already owned its English twin).
#:
#: AT ZERO THIS IS NOW AN EQUALITY, and that is the point: every alias in the shipped set reaches
#: its line by something the rulebook states — a label owner, a section, a statement, a priority —
#: and never by the order two concepts happen to be declared in. There is no margin left to absorb
#: a regression, which is the strongest form of this test and the reason not to leave one.
_UNBREAKABLE_TIE_CEILING = 0


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def resolved():
    """RESOLVED, because the gate fields are folded in from `inherits` at load time.

    523 of the 527 items declare `inherits` and almost none declares `statements` or
    `section_scope` itself, so reading the file directly sees an unconstrained line everywhere and
    every pair of claimants looks like it overlaps. Measured while writing this: the raw read
    reported 531 unbreakable ties where the resolved read reports 140.
    """
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _template_keys() -> set[str]:
    """`services.line_item_audit.template_keys` against the shipped template — the walk itself is
    imported, so this is only the file read."""
    return template_keys(json.loads(TEMPLATE.read_text(encoding="utf-8")))


# ── the template boundary ────────────────────────────────────────────────────────────────────

def test_every_line_item_is_either_a_template_column_or_a_part(raw):
    """THE PRODUCT CONSTRAINT. The spread's columns are fixed, so configuration work adds PARTS.

    A `sub__*` key is a part: it feeds a line through `parent`/`rollup` and is never printed. Any
    other new key is a new column, which is what this refuses.
    """
    offenders = keys_outside_the_template(
        raw, json.loads(TEMPLATE.read_text(encoding="utf-8")))
    assert offenders == [], (
        f"these line items are neither a template column nor a `sub__` part, so they would print "
        f"a column the spread does not declare: {offenders}")


def test_no_part_is_secretly_a_template_column(raw):
    """The other direction. A `sub__` key that IS a template column would be printed twice — once
    as itself and once through the parent it rolls into."""
    columns = _template_keys()
    both = sorted(i["key"] for i in raw["items"]
                  if i["key"].startswith("sub__") and i["key"] in columns)
    assert both == [], f"`sub__` parts that are also template columns: {both}"


def test_the_boundary_is_not_vacuous(raw):
    """Both halves must be non-empty, or the two tests above pass by having nothing to check."""
    keys = [i["key"] for i in raw["items"]]
    assert sum(1 for k in keys if k.startswith("sub__")) >= 60
    assert len(_template_keys()) >= 400


# ── structural integrity ─────────────────────────────────────────────────────────────────────

def test_every_parent_names_a_line_that_exists(raw):
    """A part whose parent is missing is a figure with nowhere to roll up to — it extracts and
    then reaches no line, which looks identical to not having been authored."""
    dangling = [d for d in dangling_references(raw) if ".parent ->" in d]
    assert dangling == [], dangling


def test_every_term_names_a_line_that_exists(raw):
    """Same property for `terms`, which is how a calculated line names its addends.

    A term is `{"ref": <key>, "sign": +1|-1}` rather than a bare key — the sign is what makes a
    deduction a deduction, and reading the entry as a string is how this test failed first.
    """
    dangling = [d for d in dangling_references(raw) if ".terms.ref ->" in d]
    assert dangling == [], dangling


def test_every_term_declares_a_sign(raw):
    """A term with no sign is summed as an addition by default, so a missing sign on a DEDUCTION
    publishes the wrong total with nothing to show it — the positional-signs failure
    `note_sourced.resolve_sources` records, one layer up in the configuration."""
    assert unsigned_terms(raw) == [], unsigned_terms(raw)


# ── the tie ratchet ──────────────────────────────────────────────────────────────────────────

# `_unbreakable_ties` LIVES IN `services.line_item_audit` NOW, and is imported above rather than
# restated here. It was written in this file and was its only reader until
# `scripts/export_line_items_seed.py` had to answer the same question about a DATABASE row — and
# two spellings of "is this alias tie real" is the two-places-computing-one-quantity bug this
# codebase keeps finding. The docstring that explained the shape moved with it.


def test_no_change_adds_an_unbreakable_alias_tie(resolved):
    """THE RATCHET. Broadening the vocabulary is the work; making an existing line unreachable is
    not, and the two look identical in a diff."""
    ties = _unbreakable_ties(resolved)
    assert len(ties) <= _UNBREAKABLE_TIE_CEILING, (
        f"unbreakable alias ties rose to {len(ties)} against a ceiling of "
        f"{_UNBREAKABLE_TIE_CEILING}. An alias was given to a second line with the same scope and "
        f"the same priority, so declaration order now decides which one a caption reaches. "
        f"New ones will be among: {ties[:12]}")


def test_the_ceiling_is_not_slack(resolved):
    """A ceiling far above the real count stops ratcheting. Kept within 10 of the measurement, so
    resolving ties is rewarded with a lower ceiling rather than absorbed by the margin.
    """
    ties = _unbreakable_ties(resolved)
    assert _UNBREAKABLE_TIE_CEILING - len(ties) <= 10, (
        f"the ceiling is {_UNBREAKABLE_TIE_CEILING} and the real count is {len(ties)} — lower the "
        f"ceiling to {len(ties)}")


def test_the_seven_retained_movements_are_no_longer_tied(resolved):
    """THE ONE SET OF TIES THAT WAS RESOLVED, pinned so it cannot come back. All seven claimed the
    identical retained-earnings vocabulary at priority 10 on one statement and one section."""
    ties = _unbreakable_ties(resolved)
    retained = [t for t in ties if t.count("is_retained__") >= 2]
    assert retained == [], retained
