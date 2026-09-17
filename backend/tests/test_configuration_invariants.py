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

import collections
import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.mapping import normalize_label

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
TEMPLATE = TEMPLATES / "output_csv_hk_v1_template.json"

#: The one line item that is not a template column and is not a part. Named rather than tolerated
#: by a rule, so it cannot grow a second member by accident.
_NOT_A_COLUMN = {"bs_ca_residual_L3"}

#: Aliases claimed by two lines with nothing able to choose between them. A RATCHET — see the
#: module docstring. Lower it when ties are resolved; never raise it to make a change pass.
_UNBREAKABLE_TIE_CEILING = 140


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
    out: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            if key := node.get("canonical_key"):
                out.add(key)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    return out


# ── the template boundary ────────────────────────────────────────────────────────────────────

def test_every_line_item_is_either_a_template_column_or_a_part(raw):
    """THE PRODUCT CONSTRAINT. The spread's columns are fixed, so configuration work adds PARTS.

    A `sub__*` key is a part: it feeds a line through `parent`/`rollup` and is never printed. Any
    other new key is a new column, which is what this refuses.
    """
    columns = _template_keys()
    offenders = sorted(i["key"] for i in raw["items"]
                       if not i["key"].startswith("sub__")
                       and i["key"] not in columns
                       and i["key"] not in _NOT_A_COLUMN)
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
    keys = {i["key"] for i in raw["items"]}
    dangling = sorted(f"{i['key']} -> {i['parent']}" for i in raw["items"]
                      if i.get("parent") and i["parent"] not in keys)
    assert dangling == [], dangling


def test_every_term_names_a_line_that_exists(raw):
    """Same property for `terms`, which is how a calculated line names its addends.

    A term is `{"ref": <key>, "sign": +1|-1}` rather than a bare key — the sign is what makes a
    deduction a deduction, and reading the entry as a string is how this test failed first.
    """
    keys = {i["key"] for i in raw["items"]}
    dangling = sorted(f"{i['key']} -> {t.get('ref')}" for i in raw["items"]
                      for t in (i.get("terms") or []) if t.get("ref") not in keys)
    assert dangling == [], dangling


def test_every_term_declares_a_sign(raw):
    """A term with no sign is summed as an addition by default, so a missing sign on a DEDUCTION
    publishes the wrong total with nothing to show it — the positional-signs failure
    `note_sourced.resolve_sources` records, one layer up in the configuration."""
    unsigned = sorted(f"{i['key']} -> {t.get('ref')}" for i in raw["items"]
                      for t in (i.get("terms") or []) if t.get("sign") not in (1, -1))
    assert unsigned == [], unsigned


# ── the tie ratchet ──────────────────────────────────────────────────────────────────────────

def _unbreakable_ties(line_items) -> list[str]:
    """Aliases two lines claim with nothing able to choose between them.

    Sharing an alias is NOT a defect and the set does it 346 times deliberately — `无形资产` sits on
    balance-sheet and income-statement lines, `bank wealth management products` on the current and
    non-current variants — because `mapping._in_statement` and `_in_section` separate them. Two
    further tie-breakers apply before a tie is real: `match_priority`, and `_prefer_label_owners`,
    which prefers the concept whose own LABEL is the caption.

    What is left is the shape that has no answer: same alias, overlapping scope, equal priority,
    and the caption is nobody's label. `match()` then returns whichever claimant declaration order
    reached first, and the others are unreachable for that caption however well a filing prints it.
    """
    items = list(line_items.items)
    by_key = {i.key: i for i in items}

    def tok(x) -> str:
        return str(getattr(x, "value", x) or "")

    def scopes(item) -> set[tuple[str, str]]:
        statements = [tok(x) for x in (getattr(item, "statements", None) or ())]
        sections = [tok(x) for x in (getattr(item, "section_scope", None) or ())]
        # A missing half is UNCONSTRAINED, which the gates read as "matches anything" — so it
        # overlaps every value rather than none.
        return {(s or "", g or "") for s in (statements or [""]) for g in (sections or [""])}

    def overlap(a: set, b: set) -> bool:
        return any((sa == sb or not sa or not sb) and (ga == gb or not ga or not gb)
                   for sa, ga in a for sb, gb in b)

    owner: dict[str, list[str]] = collections.defaultdict(list)
    for item in items:
        names = list(getattr(item, "aliases", None) or ())
        for values in (getattr(item, "aliases_i18n", None) or {}).values():
            names.extend(values or ())
        for name in names:
            norm = normalize_label(name)
            if norm and item.key not in owner[norm]:
                owner[norm].append(item.key)

    labels = {normalize_label(getattr(i, "label", "") or ""): i.key for i in items}
    out: list[str] = []
    for norm, claimants in owner.items():
        if len(claimants) < 2:
            continue
        label_owner = labels.get(norm)
        for x in range(len(claimants)):
            for y in range(x + 1, len(claimants)):
                a, b = claimants[x], claimants[y]
                if label_owner in (a, b):
                    continue
                if int(getattr(by_key[a], "match_priority", 0) or 0) != \
                        int(getattr(by_key[b], "match_priority", 0) or 0):
                    continue
                if overlap(scopes(by_key[a]), scopes(by_key[b])):
                    out.append(f"{norm!r}: {a} vs {b}")
    return sorted(out)


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
