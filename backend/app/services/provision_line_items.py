"""A template provisions its own line items, and a template line's item cannot be deleted.

THE RULE, in the words it was given in: "as soon as someone uploads a new template, all the line
items from that template should automatically flow into the line items, then the user can add more
to it but not delete any of the items present in the template."

WHAT WAS MISSING. ``routes/templates.create_template`` published a ``TemplateVersion`` and nothing
else, so a newly uploaded template arrived with NO configuration at all: every line served
``mapped: false`` and an author had to write 400-odd items by hand before the template could map
anything. The shipped pair was only ever in the right shape because a build script had produced
both halves together.

THE DISTINCTION ALREADY EXISTED. ``LineItemDef.namespace`` is ``"template" | "internal"``, and the
shipped set is already exactly the shape this rule describes — measured 2026-09-09: 462 ``template``
and 13 ``internal`` over 475 items. So this module wires up a convention rather than inventing one.

WHICH TEMPLATE NODES GET AN ITEM — measured, not assumed. Of the shipped template's 480 nodes
carrying a ``canonical_key``, exactly the 18 whose ``role`` is ``"header"`` have no item, and all
462 ``line`` / ``subtotal`` / ``total`` nodes have one. A header names a section, not a figure, so
provisioning one would create a row that can never hold a value and — under this rule — could never
be deleted either. ``role != "header"`` is therefore the test.

WHERE ``inherits`` COMES FROM, also measured: 462 of 462 shipped items declare ``inherits`` equal to
their NEAREST HEADER ANCESTOR's ``canonical_key``, and the 18 ``section_defaults`` keys are exactly
those 18 header keys. That is the whole section layer: an item declares no statement and no scope of
its own, and folding ``section_defaults`` in supplies both. Getting it wrong is the one configuration
failure that is silent rather than loud — a dangling ``inherits`` is not a load error, it just leaves
the item with no section gate — which is why the publish path checks a RESOLVED load.

RE-UPLOAD KEEPS AUTHORED WORK. When a new template version no longer carries a line, that line's
item is KEPT and demoted to ``namespace: "internal"`` rather than dropped. The aliases, criteria and
descriptors on it are the expensive part and a template revision is not a reason to discard them.
The demotion has a second, deliberate consequence: protection follows the template, so a line the
template no longer demands stops being protected and becomes deletable — the author can then discard
it on purpose, which is different from the system doing it for them.
"""
from __future__ import annotations

from typing import Any, Iterable

# A provisioned item declares as little as possible. `key` is the only required field on
# `LineItemDef`; everything else has a default, and a default the author did not choose is a value
# they then have to discover and undo. `type: "extracted"` is the measured shipped default — 453 of
# the 462 template-namespace items are `extracted` regardless of the node's role or whether it
# carries a rollup, and the 9 `derived` ones were authored deliberately after the fact.
_PROVISIONED_TYPE = "extracted"


def _walk(nodes: Iterable[dict], header: str | None) -> Iterable[tuple[dict, str | None]]:
    """Every node with the canonical_key of its nearest HEADER ancestor.

    The header is carried down rather than looked up afterwards because a section is a position in
    the tree, not a property of the node — an item three levels under ``bs_ca`` still belongs to
    current assets.
    """
    for node in nodes or ():
        key = node.get("canonical_key")
        is_header = node.get("role") == "header"
        yield node, header
        # A header becomes the section for everything beneath it; a non-header does not shadow it.
        yield from _walk(node.get("children") or [], key if (is_header and key) else header)


def template_nodes(definition: dict) -> list[tuple[dict, str | None, str | None]]:
    """(node, nearest header key, statement type) for every node carrying a canonical_key."""
    out: list[tuple[dict, str | None, str | None]] = []
    for statement in (definition or {}).get("statements") or ():
        for node, header in _walk(statement.get("sections") or [], None):
            if node.get("canonical_key"):
                out.append((node, header, statement.get("type")))
    return out


def figure_bearing(definition: dict) -> list[tuple[dict, str | None, str | None]]:
    """The nodes that hold a number.

    Asks ``review_lines.is_statement_line`` rather than spelling ``role != "header"`` here. That
    predicate is this codebase's single answer to "does this node carry a figure": the review
    header counts its population with it, ``get_template_detail``'s walk and ``export._emit_nodes``
    ask it, and ``routes/templates._publish`` reports an upload's line count from it. Its own
    comment records why a second spelling is a bad idea — hand-written, a spacer got published as a
    line item. A provisioned set must contain exactly the lines the template reports, so this has
    to be the same question, not an equivalent one.
    """
    from app.services.review_lines import is_statement_line

    return [t for t in template_nodes(definition)
            if is_statement_line({"role": t[0].get("role")})]


def header_nodes(definition: dict) -> list[tuple[dict, str | None, str | None]]:
    """The sections — everything the predicate above does not call a line."""
    from app.services.review_lines import is_statement_line

    return [t for t in template_nodes(definition)
            if not is_statement_line({"role": t[0].get("role")})]


def _provisioned_item(node: dict, inherits: str | None) -> dict:
    item: dict[str, Any] = {
        "key": node["canonical_key"],
        "label": node.get("label") or node["canonical_key"],
        "namespace": "template",
        "type": _PROVISIONED_TYPE,
        "in_output": True,
    }
    if inherits:
        item["inherits"] = inherits
    labels = node.get("label_i18n") or {}
    if labels:
        # The template's own translations, so a provisioned item is not English-only on a
        # deployment whose output locale is not English.
        item["label_i18n"] = dict(labels)
    return item


def _provisioned_section_default(node: dict, statement: str | None) -> dict:
    """The minimum that makes a section gate REAL: which statement, and which scope.

    Nothing else is guessed. The shipped entries also carry temporality, unit_of_account,
    face_only, note_use, sign_convention and match_priority — every one of those is a judgement
    about the accounting, and a provisioned default that asserted one would be putting a decision
    the author never made behind their name. Left absent, they fall to the schema's own defaults
    and the author can fill them in on the screen.
    """
    entry: dict[str, Any] = {"section_scope": [node["canonical_key"]]}
    if statement:
        entry["statement"] = statement
    return entry


def provision(template_definition: dict, existing: dict | None = None) -> dict:
    """A line-item set definition for this template, preserving anything already authored.

    Three cases, and the third is the one that protects an author's work:

    * a figure-bearing template line with NO item gets a minimal ``namespace: "template"`` item;
    * a figure-bearing template line that already HAS an item keeps every field of it, with
      ``namespace`` forced to ``"template"`` — the template demands this line, so its item is
      protected whatever the stored row happened to say;
    * an item whose key the template NO LONGER carries is kept and demoted to ``"internal"``.

    Returns a definition ready for the publish gates in ``routes/line_items``; it is deliberately
    NOT validated here, so that one door does the validating and the two cannot disagree.
    """
    base = dict(existing or {})
    prior = {i.get("key"): dict(i) for i in (base.get("items") or []) if i.get("key")}

    lines = figure_bearing(template_definition)
    template_keys = {n["canonical_key"] for n, _h, _s in lines}

    items: list[dict] = []
    for node, header, _statement in lines:
        key = node["canonical_key"]
        if key in prior:
            kept = prior[key]
            kept["namespace"] = "template"
            # A template line must have a label to appear in the output; an item that never had one
            # takes the template's, rather than rendering as its own key.
            kept.setdefault("label", node.get("label") or key)
            items.append(kept)
        else:
            items.append(_provisioned_item(node, header))

    # Everything the template dropped, in its original order, demoted.
    for key, item in prior.items():
        if key in template_keys:
            continue
        item["namespace"] = "internal"
        items.append(item)

    defaults = dict(base.get("section_defaults") or {})
    for node, _header, statement in header_nodes(template_definition):
        defaults.setdefault(node["canonical_key"],
                            _provisioned_section_default(node, statement))

    out = dict(base)
    out["items"] = items
    out["section_defaults"] = defaults
    out["target_template_key"] = template_definition.get("template_key")
    out.setdefault("line_items_key", template_definition.get("template_key") or "line_items")
    out.setdefault("schema_version", base.get("schema_version") or 1)
    return out


def protected_keys(definition: dict) -> set[str]:
    """The item keys a delete must refuse — those the template put there."""
    return {i.get("key") for i in (definition or {}).get("items") or ()
            if i.get("namespace") == "template" and i.get("key")}
