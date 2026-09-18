"""What the shipped configuration may not become — the invariants, in app code.

WHY THESE LIVE HERE AND NOT ONLY IN A TEST. `tests/test_configuration_invariants` wrote them first
and was the only reader, which was right while the only way the seed changed was an author editing
the JSON. It is not the only way any more: `scripts/export_line_items_seed.py` writes the seed from
a DATABASE row, so the same three questions have to be answerable outside pytest — and a second
spelling of "is this alias tie real" is exactly the two-places-computing-one-quantity bug this
codebase keeps finding. The test now imports these; it does not restate them.

THE THREE ARE NOT THE PUBLISH GATES, and the difference is worth stating. `api/routes/line_items`
already refuses an edit whose key resolves against no template column, whose field the schema does
not declare, or which recognises nothing at all — those are properties of ONE edit, checked where
an author is present to be told why. These are properties of what the repository SHIPS: that the
set still adds only parts and never columns, that no caption has become unreachable, and that every
`parent` and `terms.ref` still names something. An edit can pass all three gates and still break
one of these, because the gates never compare the set against the template's column list in the
other direction and never look at two items at once.
"""
from __future__ import annotations

import collections
import re
from typing import Any

from app.services.mapping import normalize_label

#: The one shipped line item that is neither a template column nor a part. Named rather than
#: tolerated by a rule, so it cannot grow a second member by accident.
NOT_A_COLUMN: frozenset[str] = frozenset({"bs_ca_residual_L3"})

#: A part — `in_output: false`, so it publishes into no column. The template boundary is a product
#: constraint: a change may add one of these and never a printed line.
PART_PREFIX = "sub__"

#: The three configuration-editing e2e probes build their strings as `E2E alias ${Date.now()}`, so
#: a database an e2e suite has run against carries rows that must never reach the shipped file.
#: `scripts/reconcile_reference_data` deletes such rows; this recognises them.
_E2E_PROBE = re.compile(r"E2E (?:alias|includes|netting) \d{10,}")


def template_keys(template: dict) -> set[str]:
    """Every `canonical_key` the template declares, at any depth."""
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if key := node.get("canonical_key"):
                out.add(str(key))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(template)
    return out


def keys_outside_the_template(raw: dict, template: dict) -> list[str]:
    """Printed keys the template declares no column for — each one an output column that is not
    there. A part is exempt by definition: it publishes nowhere."""
    declared = template_keys(template)
    return sorted(
        str(i.get("key") or "") for i in (raw.get("items") or [])
        if not str(i.get("key") or "").startswith(PART_PREFIX)
        and str(i.get("key") or "") not in declared
        and str(i.get("key") or "") not in NOT_A_COLUMN)


def dangling_references(raw: dict) -> list[str]:
    """Every `parent` and `terms.ref` naming a key the set does not contain."""
    keys = {str(i.get("key") or "") for i in (raw.get("items") or [])}
    out: list[str] = []
    for item in raw.get("items") or []:
        key = str(item.get("key") or "")
        parent = str(item.get("parent") or "")
        if parent and parent not in keys:
            out.append(f"{key}.parent -> {parent}")
        for term in item.get("terms") or []:
            ref = str((term or {}).get("ref") or "")
            if ref and ref not in keys:
                out.append(f"{key}.terms.ref -> {ref}")
    return sorted(out)


def unsigned_terms(raw: dict) -> list[str]:
    """Terms with no explicit sign. Summed as an addition by default, so a missing sign on a
    DEDUCTION publishes the wrong total with nothing to show it."""
    return sorted(f"{i.get('key')} -> {t.get('ref')}" for i in (raw.get("items") or [])
                  for t in (i.get("terms") or []) if t.get("sign") not in (1, -1))


def unbreakable_ties(line_items) -> list[str]:
    """Aliases two lines claim with nothing able to choose between them.

    TAKES A RESOLVED SET. The gate fields are folded in from `inherits` at LOAD time and almost no
    item declares `statements` or `section_scope` itself, so reading the raw file sees an
    unconstrained line everywhere and every pair of claimants looks like it overlaps. Measured
    while this was written: the raw read reported 531 ties where the resolved read reported 140.

    Sharing an alias is NOT a defect and the set does it hundreds of times deliberately — `无形资产`
    sits on balance-sheet and income-statement lines, `bank wealth management products` on the
    current and non-current variants — because `mapping._in_statement` and `_in_section` separate
    them. Two further tie-breakers apply before a tie is real: `match_priority`, and
    `_prefer_label_owners`, which prefers the concept whose own LABEL is the caption.

    What is left is the shape that has no answer: same alias, overlapping scope, equal priority,
    and the caption is nobody's label. `match()` then returns whichever claimant declaration order
    reached first, and the others are unreachable for that caption however well a filing prints it.
    """
    items = list(line_items.items)
    by_key = {i.key: i for i in items}

    def tok(x: Any) -> str:
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


def junk_markers(definition: Any) -> list[str]:
    """The e2e probe strings a configuration-editing test suite leaves in a database it ran
    against. A row carrying one is test residue and must never become the shipped file."""
    import json as _json

    return sorted(set(_E2E_PROBE.findall(_json.dumps(definition or {}, ensure_ascii=False))))
