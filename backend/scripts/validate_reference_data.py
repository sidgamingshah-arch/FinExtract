#!/usr/bin/env python
"""Validate the shipped reference data: the checks the load and publish gates do not run.

THE GAP THIS CLOSES. Every file in ``app/sample/templates`` was put through deliberately broken
copies of itself, and each copy was offered to every door the product has: the schema loaders, the
boot seed (``sample.reference``), the upload routes, the matcher and the run. Most breaks are
refused at load. A long list is not, and a broken file of that kind publishes, boots healthy and
then computes something else:

  * a TEMPLATE whose rollups form a cycle (both lines are then never computed and no review card is
    raised), whose ``reported_total_key`` names nothing (a residual silently becomes the sum of its
    parts: 85 became 15, still marked computable), whose rollup names a child by a ``node_id`` that
    differs from its ``canonical_key`` (the gate checks node ids, the evaluator reads canonical
    keys), or which repeats a ``node_id`` (a structural check rewired onto an exchange-rate line);
  * a LINE-ITEM SET with a duplicate key, a cascade term naming no line, a cascade cycle, a part
    whose figure reaches no output column, an aliased part with no statement gate, a
    ``section_scope`` naming no section (a typo WIDENS the gate), a template column it no longer
    configures or a printed key the template has no column for — the registry
    (``services.line_items.build``) sees several of these and enforces none of them, and the boot
    seed is weaker than the upload door, so a set the door refuses still boots into force.

None of it is caught until a figure is wrong in front of somebody. This script runs every one of
those checks on the files themselves, so the repository cannot ship them.

WHAT IT RUNS, by the kind of file:

  * every TEMPLATE goes through the boot gate first (``sample.reference._load_template``: schema,
    undeclared keys, ``loader.validate_template``) and then through the structural checks below;
  * every LIVE LINE-ITEM SET goes through the boot gate (``sample.reference._load_line_item_set``),
    the registry verdict, and the publish gate (``_validate_against_target_template`` and
    ``_recognises_anything`` from ``routes.line_items``, run against the shipped template rather
    than a database row), and then through the checks below;
  * the files the product does NOT read — ``FILE_ROLES`` says which, and
    ``app/sample/templates/README.md`` says why — are loaded through their own loaders, and
    anything they say that the template or the live set does not is a WARN: drift that is visible,
    not a defect that can move a figure.

THREE LEVELS. ``ERROR`` fails the run (exit 1). ``WARN`` is suspicious but legitimate in the shipped
data, and is printed so it stays visible. ``KNOWN`` is an ERROR this script finds today that another
piece of work is fixing: ``KNOWN_DEFECTS`` names each one exactly, and a known defect that is no
longer found is reported, so the list shrinks as the fixes land instead of becoming a place errors
hide (``tests/test_reference_data_validator.py`` fails on a stale entry).

WHAT IS RULED OUT is in ``RULED_OUT``, with the reason for each — a probe finding that is not a
property of a file, or that something else already pins.

    cd backend
    python scripts/validate_reference_data.py                  # the report; exit 1 on any ERROR
    python -m scripts.validate_reference_data --errors-only    # the same, ERRORs and KNOWN only
    python scripts/validate_reference_data.py --dir /tmp/copy  # a copy of the templates directory
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import textwrap
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterable

BACKEND = pathlib.Path(__file__).resolve().parent.parent
# FIRST on the path, not merely present: the package is installed editable, and from another
# checkout (a worktree, a copy) `import app` would otherwise resolve to THAT checkout's app — this
# script would then validate one tree's files with another tree's gates.
if sys.path[0] != str(BACKEND):
    sys.path.insert(0, str(BACKEND))

TEMPLATES = BACKEND / "app" / "sample" / "templates"

ERROR = "ERROR"
WARN = "WARN"
KNOWN = "KNOWN"

# ── which files the product reads ─────────────────────────────────────────────────────────────
# MEASURED, NOT ASSUMED. App code opens exactly five of the twelve files: `sample.reference` seeds
# the primary template and the two (template, line-item set) pairs at every boot, and
# `services.line_item_config.SEED` / `row_reconstruct.in_force_rules` read the HK set off disk. The
# other seven are named in app code only inside docstrings and comments; with all seven deleted from
# a copy of the backend, a boot on a fresh database still published the five, and the API, export
# and extraction tests still passed (the only failures were tests opening a deleted file as their
# own fixture). They are read by build scripts and tests, never by a boot or a run.
# `tests/test_reference_data_validator.py` holds this table to what `sample.reference` actually
# seeds and to what app code actually names.
LIVE = "live"
BUILD_INPUT = "build input"
LEGACY = "legacy"

FILE_ROLES: dict[str, tuple[str, str]] = {
    "hkfrs_hk_china_template.json": (
        LIVE, "seeded at boot as the primary template, with no configuration beside it"),
    "output_csv_hk_v1_template.json": (
        LIVE, "seeded at boot; the template every output_csv_hk run targets"),
    "output_csv_hk_line_items.json": (
        LIVE, "seeded at boot with output_csv_hk_v1; THE configuration, also read off disk by "
              "line_item_config and row_reconstruct.in_force_rules"),
    "output_csv_indas_v1_template.json": (LIVE, "seeded at boot"),
    "output_csv_indas_line_items.json": (LIVE, "seeded at boot with output_csv_indas_v1"),
    "output_csv_hk_ontology.json": (
        BUILD_INPUT, "input of scripts/build_line_items.py and a test fixture; no app code reads "
                     "it, and it no longer rebuilds the shipped set"),
    "output_csv_hk_line_items_configured.json": (
        BUILD_INPUT, "the hand-maintained half of scripts/build_line_items.py's merge"),
    "output_csv_hk_rules.json": (LEGACY, "read by tests/test_rulebook_rules.py only"),
    "output_csv_hk_validation.json": (
        LEGACY, "read by tests/test_validation_rules.py and "
                "tests/test_structural_reaches_the_run.py only"),
    "hkfrs_hk_china_ontology.json": (
        LEGACY, "a test fixture and the input of the HKFRS builder scripts; no app code reads it"),
    "hkfrs_hk_china_rules.json": (LEGACY, "read by tests/test_rulebook_rules.py only"),
    "hkfrs_hk_china_validation.json": (LEGACY, "read by tests/test_validation_rules.py only"),
}


# ── the known defects ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class KnownDefect:
    """One ERROR this script finds on the shipped files and that another piece of work removes.

    Matched on all three of (file, check, subject), so an entry excuses exactly the finding it was
    written for: a second cycle, or the same check on another key, is still an ERROR.
    """

    file: str
    check: str
    subject: str
    why: str


_HK_SET = "output_csv_hk_line_items.json"
_INDAS_SET = "output_csv_indas_line_items.json"

#: The Ind AS subtotals whose `terms` are the HK set's, verbatim — 19 of 19 identical — and so name
#: 230 keys the Ind AS set does not define. A run computes these subtotals from the TEMPLATE's
#: rollup, so no published figure reads them; the registry calls every one an error, and the
#: configuration screen shows all 230. `tests/test_formula_in_config.py` holds the HK copy to its
#: template and nothing holds this one.
_INDAS_FOREIGN_TERMS = (
    "bs_ca__total_current_assets", "bs_cl__total_current_liabilities",
    "bs_equity__equity_and_reserves", "bs_equity__permanent_equity",
    "bs_equity__total_equity_and_reserves", "bs_nca__total_non_current_assets",
    "bs_ncl__total_non_current_liabilities", "cf_financing__cash_flows_from_finance_activities",
    "cf_investing__cash_flows_from_invest_activities",
    "cf_oper_direct__cash_flows_oper_activ_direct",
    "cf_oper_indirect__cash_flows_oper_activ_indirect", "is_pl__gross_profit",
    "is_pl__net_interest_income_expense", "is_pl__net_operating_profit",
    "is_pl__net_other_financial_inc_exp", "is_pl__other_income_expense",
    "is_pl__profit_for_the_year", "is_pl__total_cost_of_sales", "is_pl__total_income_tax",
)

#: Every entry is a defect somebody is removing, not a defect accepted. Remove an entry when its fix
#: lands — `tests/test_reference_data_validator.py` fails while one is listed and no longer found.
KNOWN_DEFECTS: tuple[KnownDefect, ...] = (
    # FOUND BY THIS VALIDATOR, AND NOT YET OWNED BY ANY FIX. See `_INDAS_FOREIGN_TERMS`. Listed so
    # the shipped data passes while the finding is reported, not so it is forgotten: the fix is to
    # derive these `terms` from the Ind AS template's rollups (or drop them), which is a
    # configuration change to measure and land on its own.
    *(KnownDefect(_INDAS_SET, "dangling-ref", key,
                  "HK terms copied into the Ind AS set; UNOWNED — reported, not yet fixed")
      for key in _INDAS_FOREIGN_TERMS),
)


#: Probe findings this script does NOT turn into a check, and why. Kept as data so the report says
#: so, and so the list is reviewed together with the checks rather than forgotten in a commit.
RULED_OUT: tuple[tuple[str, str], ...] = (
    ("a refused HK pair is only an ERROR log line at boot, and reverting a broken file does not "
     "restore the shipped version",
     "runtime behaviour of sample.reference, not a property of a file, and owned by the "
     "startup-gate work; this script guarantees the other half, that no shipped file reaches that "
     "path broken"),
    ("an unknown rollup op on a stored definition is evaluated as a plain sum",
     "reachable only by a database row that bypassed the gate; a shipped file goes through the "
     "schema, which refuses the op (boot-gate)"),
    ("a calculated line's terms disagree with its template rollup",
     "pinned for the HK set by tests/test_formula_in_config.py; the Ind AS set's terms are the HK "
     "set's verbatim and are reported here as dangling-ref"),
)


# ── findings and the report ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Finding:
    level: str              # ERROR or WARN
    file: str               # the file's name inside the templates directory
    check: str              # stable id: what KNOWN_DEFECTS and the tests name
    subject: str            # the key, node or rule the finding is about
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    known: list[tuple[Finding, KnownDefect]] = field(default_factory=list)
    stale: list[KnownDefect] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)       # name -> what it is

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == ERROR]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARN]

    @property
    def exit_code(self) -> int:
        # A stale allowlist entry fails too: it would wait there to excuse the defect coming back.
        return 1 if self.errors() or self.stale else 0

    def render(self, *, errors_only: bool = False) -> str:
        """The report, grouped by file, every finding on a header line and a wrapped message."""
        by_file: dict[str, list[tuple[str, Finding, str]]] = collections.defaultdict(list)
        for f in self.findings:
            if errors_only and f.level != ERROR:
                continue
            by_file[f.file].append((f.level, f, ""))
        for f, entry in self.known:
            by_file[f.file].append((KNOWN, f, entry.why))
        order = {ERROR: 0, KNOWN: 1, WARN: 2}
        out: list[str] = []
        for name in sorted(set(self.files) | set(by_file)):
            out.append(f"{name}  [{self.files.get(name, 'unclassified')}]")
            rows = sorted(by_file.get(name, []), key=lambda r: (order[r[0]], r[1].check,
                                                                  r[1].subject))
            if not rows:
                out.append("  ok")
            for level, f, why in rows:
                out.append(f"  {level:<5}  {f.check}  {f.subject}")
                text = f.message + (f" (known defect: {why})" if why else "")
                out.extend(textwrap.wrap(text, width=100, initial_indent=" " * 9,
                                         subsequent_indent=" " * 9))
            out.append("")
        for entry in self.stale:
            out.append(f"STALE ALLOWLIST ENTRY  {entry.file}  {entry.check}  {entry.subject}: no "
                       f"longer found — remove it from KNOWN_DEFECTS.")
        if self.stale:
            out.append("")
        if not errors_only:
            out.append("Not checked here, on purpose:")
            for what, why in RULED_OUT:
                out.extend(textwrap.wrap(f"{what} — {why}.", width=100, initial_indent="  - ",
                                         subsequent_indent="    "))
            out.append("")
        verdict = "FAIL" if self.errors() else "PASS"
        entries = "entry" if len(self.stale) == 1 else "entries"
        out.append(f"{verdict}: {len(self.errors())} error(s), {len(self.known)} known defect(s) "
                   f"on the KNOWN_DEFECTS list, {len(self.warnings())} warning(s), "
                   f"{len(self.stale)} stale allowlist {entries} over {len(self.files)} file(s).")
        return "\n".join(out)


class _Collector:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def error(self, file: str, check: str, subject: str, message: str) -> None:
        self.findings.append(Finding(ERROR, file, check, subject, message))

    def warn(self, file: str, check: str, subject: str, message: str) -> None:
        self.findings.append(Finding(WARN, file, check, subject, message))


def _shape(raw: Any) -> str:
    """What kind of reference file a parsed JSON document is, read off its own envelope."""
    if not isinstance(raw, dict):
        return "unknown"
    if "statements" in raw and "template_key" in raw:
        return "template"
    if "items" in raw and "line_items_key" in raw:
        return "line_items"
    if "mappings" in raw and "ontology_key" in raw:
        return "ontology"
    if "rules_key" in raw:
        return "rules"
    if "validation_key" in raw:
        return "validation"
    return "unknown"


def _few(values: Iterable[str], n: int = 6) -> str:
    values = list(values)
    shown = ", ".join(values[:n])
    return shown + (f" (+{len(values) - n} more)" if len(values) > n else "")


def _locales() -> set[str]:
    """The locale codes a label or an alias may be filed under: the language registry's own."""
    from app.schemas.languages import SEED_LANGUAGES

    return set(SEED_LANGUAGES)


# ── templates ─────────────────────────────────────────────────────────────────────────────────
def _template_nodes(raw: dict) -> list[tuple[dict, int, str]]:
    """(node, depth, statement type) for every node, at any depth — the schema allows any."""
    out: list[tuple[dict, int, str]] = []

    def walk(nodes: Any, depth: int, statement: str) -> None:
        for node in nodes or ():
            if isinstance(node, dict):
                out.append((node, depth, statement))
                walk(node.get("children"), depth + 1, statement)

    for stmt in raw.get("statements") or ():
        if isinstance(stmt, dict):
            walk(stmt.get("sections"), 0, str(stmt.get("type") or ""))
    return out


def _cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components with more than one member, or a member naming itself.

    The components rather than the first back-edge a walk meets, because "there is a cycle" is not
    actionable and "these lines form one" is — and because a component is the same answer whatever
    order the template lists its nodes in, so an allowlist entry can name it.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = [0]

    def strongconnect(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in edges.get(v, ()):
            if w not in edges:
                continue
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1 or v in edges.get(v, ()):
                found.append(sorted(component))

    for v in edges:
        if v not in index:
            strongconnect(v)
    return sorted(found)


def _check_template(c: _Collector, name: str, raw: dict, *, configured: bool) -> None:
    """The boot gate, then what the gate never asks.

    ``configured`` says whether a live line-item set targets this template: a figure line with no
    header ancestor is then provisioned with no section gate at all, which is an ERROR there and a
    curiosity on a template nothing configures.
    """
    from app.sample.reference import ReferenceSeedError, _load_template
    from app.schemas.loader import load_template
    from app.services.provision_line_items import figure_bearing, template_nodes

    key = str(raw.get("template_key") or name)
    try:
        _load_template(pathlib.Path(name), raw)
    except ReferenceSeedError as exc:
        c.error(name, "boot-gate", key, f"the boot seed refuses this file: {exc}")
        # A file the gate refuses on a REFERENCE (a missing child, say) still loads, and what is
        # checked below is still worth saying; one whose shape does not load is not walked at all.
        try:
            load_template(raw)
        except Exception:                          # noqa: BLE001 — reported just above
            return

    nodes = _template_nodes(raw)
    by_key = {str(n.get("canonical_key")): n for n, _d, _s in nodes if n.get("canonical_key")}
    by_node_id: dict[str, list[dict]] = collections.defaultdict(list)
    for n, _d, _s in nodes:
        by_node_id[str(n.get("node_id"))].append(n)

    # A REPEATED node_id. `TemplateDefinition._unique_keys` counts canonical keys only and
    # `node_ids()` is a set, so the copies collapse before anything can count them; then
    # `structural_checks.relations` resolves the id last-wins and checks a subtotal against the
    # wrong line while `rollups.evaluate`, reading canonical keys, computes the right one.
    for node_id, carriers in sorted(by_node_id.items()):
        if len(carriers) > 1:
            c.error(name, "duplicate-node-id", node_id,
                    f"{len(carriers)} nodes carry node_id {node_id!r} (canonical keys "
                    f"{_few(sorted(str(n.get('canonical_key')) for n in carriers))}); the gate's "
                    f"node_ids() is a set, so a rollup naming it resolves to whichever node the "
                    f"structural check meets last")

    calculated: dict[str, list[str]] = {}
    for n, depth, _s in nodes:
        k = str(n.get("canonical_key") or "")
        rollup = n.get("rollup") or {}
        if not k or not isinstance(rollup, dict):
            continue
        children = [str(x) for x in rollup.get("children") or ()]
        rtk = rollup.get("reported_total_key")
        if children or rtk:
            calculated[k] = children

        for child in children:
            # NAMED BY node_id WHERE THE TWO DIFFER. The gate checks node ids
            # (`loader.validate_template`), the evaluator reads canonical keys, so this child is
            # accepted on upload and read as absent by `rollups.evaluate`: the subtotal omits it
            # and stays computable. Latent only while every node_id equals its canonical_key.
            named = by_node_id.get(child) or []
            if child not in by_key and named:
                c.error(name, "rollup-child-node-id", k,
                        f"rollup child {child!r} is the node_id of "
                        f"{named[0].get('canonical_key')!r}; rollups.evaluate reads children as "
                        f"canonical keys, so it reads nothing for this child and the subtotal "
                        f"silently omits it")

        # A reported_total_key NAMING NOTHING. `evaluate` reads the printed figure of that key, gets
        # None, and the residual flips from "printed less parts" to "the sum of the parts" — a
        # plausible figure, still marked computable, on the 34 shipped residual rollups.
        if rtk and str(rtk) not in by_key:
            c.error(name, "reported-total-key", k,
                    f"reported_total_key {rtk!r} is not a canonical_key in this template, so the "
                    f"residual is computed from its components alone and published as if it were "
                    f"the residual")
        if rollup.get("use_reported_total_components") and rtk:
            total = by_key.get(str(rtk)) or {}
            total_children = [str(x) for x in (total.get("rollup") or {}).get("children") or ()]
            if not total_children:
                c.error(name, "reported-total-components", k,
                        f"use_reported_total_components is set but {rtk!r} declares no rollup "
                        f"children, so the evaluator falls back to this rollup's own children "
                        f"without saying so")
            # THE BYPASS `Rollup._magnitude_members_are_children` LEAVES OPEN: with the flag set it
            # does not check the names at all, and the members evaluated are the reported total's
            # children — so a stray name reads as a handled sign convention and changes nothing.
            effective = {x for x in total_children if x != k}
            stray = [x for x in rollup.get("cost_magnitude_children") or () if x not in effective]
            if total_children and stray:
                c.error(name, "cost-magnitude-children", k,
                        f"cost_magnitude_children {_few(stray)} are not among the components "
                        f"this rollup evaluates (the children of {rtk!r}), so the declared sign "
                        f"convention changes nothing")

        # DEPTH. `rollups.calculated_nodes`, `node_roles` and `node_labels` walk a section and its
        # direct children only; the schema allows any depth and `structural_checks` walks all of
        # it, so a subtotal nested one level deeper stops being computed while still being asserted.
        role = str(n.get("role") or "line")
        if depth >= 2 and role != "header":
            if k in calculated or role in ("subtotal", "total"):
                c.error(name, "node-depth", k,
                        f"a {role}{' with a rollup' if k in calculated else ''} at depth {depth}; "
                        f"rollups reads sections and their direct children only, so it is never "
                        f"computed and loses its role, while the structural check still asserts it")
            else:
                c.warn(name, "node-depth", k,
                       f"a line at depth {depth}; rollups reads sections and their direct children "
                       f"only, so it has no label when it is named as a component")

    for members in _cycles(calculated):
        c.error(name, "rollup-cycle", ", ".join(members),
                "these rollups depend on each other, so rollups._order marks every one cyclic: "
                "none is ever computed, the printed figure is served unverified, and no review "
                "card is raised")

    # LOCALE CODES. `label_i18n` is a plain dict, so 'ZH' or 'xx-NOPE' is accepted and the authored
    # translation is silently never served (resolve_label('zh') falls back to English).
    known = _locales()
    labelled: list[tuple[str, dict]] = [(str(n.get("canonical_key")), n.get("label_i18n") or {})
                                        for n, _d, _s in nodes]
    labelled += [(f"statement:{s.get('type')}", s.get("label_i18n") or {})
                 for s in raw.get("statements") or () if isinstance(s, dict)]
    kpis = raw.get("kpis") or {}
    labelled += [(f"kpi:{x.get('key')}", x.get("label_i18n") or {})
                 for block in ("intermediates", "ratios") for x in kpis.get(block) or ()
                 if isinstance(x, dict)]
    for subject, labels in labelled:
        bad = sorted(str(loc) for loc in labels if loc not in known)
        if bad:
            c.error(name, "locale", subject,
                    f"label_i18n is filed under {_few(bad)}, which the language registry does not "
                    f"know ({', '.join(sorted(known))}); the label is never served")

    # A FIGURE LINE WITH NO HEADER ABOVE IT. Provisioning carries the nearest header down as the
    # item's `inherits`, so this line's item gets no statement and no section gate, and a caption
    # can bind it from any statement. The workbook importer refuses the shape; the JSON door does
    # not.
    figures = {id(node) for node, _h, _s in figure_bearing(raw)}
    headless = [str(n.get("canonical_key")) for n, header, _s in template_nodes(raw)
                if header is None and id(n) in figures]
    if headless:
        message = (f"{len(headless)} figure line(s) sit under no header ({_few(headless)}); a line "
                   f"item provisioned from one has no inherits, so no statement or section gate")
        if configured:
            c.error(name, "headless-line", _few(headless, 3), message)
        else:
            c.warn(name, "headless-line", key,
                   message + " — harmless while no line-item set targets this template")


# ── line-item sets ────────────────────────────────────────────────────────────────────────────
class _ShippedTemplateSession:
    """The one query the publish gate makes, answered with the SHIPPED template.

    `_validate_against_target_template` asks a database session for the newest stored version of
    the set's target template. Running it against the file instead keeps one implementation of the
    upload door: the check below is the door's own code, not a second spelling of it.
    """

    def __init__(self, template_key: str, raw: dict | None) -> None:
        self._row = None if raw is None else SimpleNamespace(
            id=f"shipped:{template_key}", template_key=template_key, version=0, definition=raw)

    def execute(self, _statement: Any) -> "_ShippedTemplateSession":
        return self

    def scalars(self) -> "_ShippedTemplateSession":
        return self

    def first(self):
        return self._row


def _publish_gate(c: _Collector, name: str, st, template_raw: dict | None) -> None:
    from fastapi import HTTPException

    from app.api.routes.line_items import _recognises_anything, _validate_against_target_template

    try:
        _validate_against_target_template(
            _ShippedTemplateSession(st.target_template_key, template_raw), st)
    except HTTPException as exc:
        detail = exc.detail
        entries = detail.get("errors") if isinstance(detail, dict) else None
        if not entries:
            message = detail.get("message") if isinstance(detail, dict) else detail
            c.error(name, "publish-gate", st.line_items_key,
                    f"the upload door refuses it: {message}")
        by_location: dict[str, list[str]] = collections.defaultdict(list)
        for e in entries or ():
            # `item:<key>` on the door's side; the bare key here, so a finding names a line the way
            # every other check in this report does.
            location = str(e.get("location") or st.line_items_key)
            by_location[location.removeprefix("item:")].append(str(e.get("message")))
        for location, messages in sorted(by_location.items()):
            c.error(name, "publish-gate", location,
                    "the upload door refuses it: " + "; ".join(messages))
    if not _recognises_anything(st):
        c.error(name, "publish-gate", st.line_items_key,
                "the upload door refuses it: no item carries an alias, pattern or hint, so it "
                "recognises nothing")


#: The registry's problems, sorted into the checks this report names. Read off the message because
#: `services.line_items.Problem` carries no kind; anything unrecognised stays `registry`, so a new
#: kind of problem is still reported, just under the general name.
_REGISTRY_KINDS = (
    ("defined twice", "duplicate-key"),
    ("term names", "dangling-ref"),
    ("depends on itself", "formula-cycle"),
    ("parent", "dangling-parent"),
)


def _registry_check(message: str) -> str:
    return next((check for phrase, check in _REGISTRY_KINDS if phrase in message), "registry")


def _refs(item) -> list[str]:
    out = [t.ref for t in item.terms if t.ref]
    for rung in item.cascade:
        out.extend(t.ref for t in rung.terms if t.ref)
    return out


def _wiring(items: list) -> dict[str, str]:
    """Every non-output item whose figure reaches no output column, with the reason.

    HOW A FIGURE TRAVELS, as `stages.note_sourced._fill_parents` moves it: a line that declares a
    cascade or terms is computed from exactly what those name, and a child its formula does not
    name is DROPPED (the run log still says it "joins the cascade"); a parent that declares neither
    combines its children by its `rollup`, unless that is `none` — and only an output column can
    take a figure that way, because a part reads its own figure and keeps it over its children's.
    A fixpoint from the output columns outwards, so a chain of parts is followed however long.
    """
    by_key: dict[str, Any] = {}
    for d in items:
        by_key.setdefault(d.key, d)
    named_by: dict[str, set[str]] = collections.defaultdict(set)
    for d in by_key.values():
        for ref in _refs(d):
            named_by[ref].add(d.key)

    def computes(d) -> bool:
        return bool(d.cascade or d.terms)

    reached = {k for k, d in by_key.items() if d.in_output}
    changed = True
    while changed:
        changed = False
        for k, d in by_key.items():
            if k in reached:
                continue
            parent = by_key.get(d.parent) if d.parent else None
            if (named_by.get(k, set()) & reached) or (
                    parent is not None and parent.in_output and not computes(parent)
                    and parent.rollup != "none"):
                reached.add(k)
                changed = True

    why: dict[str, str] = {}
    for k, d in by_key.items():
        if k in reached:
            continue
        parent = by_key.get(d.parent) if d.parent else None
        if named_by.get(k):
            why[k] = (f"only {_few(sorted(named_by[k]))} name it, and none of them reaches an "
                      f"output column either")
        elif not d.parent:
            why[k] = ("it has no parent and no cascade or terms names it, so its figure reaches "
                      "nothing")
        elif parent is None:
            why[k] = f"its parent {d.parent!r} is not a line item"
        elif computes(parent):
            why[k] = (f"its parent {d.parent} computes from its own cascade/terms, which do not "
                      f"name it, so its figure is dropped (the run log still says it joins the "
                      f"cascade)")
        elif parent.rollup == "none":
            why[k] = f"its parent {d.parent}'s rollup is 'none', so nothing is carried up"
        else:
            why[k] = (f"its parent {d.parent} is a part that declares no cascade or terms; a part "
                      f"keeps the figure it reads itself, so this one is lost")
    return why


def _balancing_identity(item, rollup_children: dict[str, list[str]], section_of: dict[str, str]
                        ) -> str | None:
    """Why `children_if_decomposed` states an IDENTITY rather than containment, or None.

    Two shapes, and both are arithmetic that balances rather than a whole and its parts:

      * the list spans BOTH SIDES of the balance sheet — an asset figure and a liability or equity
        figure. Nothing contains both; "retained profits = total assets − liabilities − …" is the
        balance-sheet identity rearranged;
      * the list names a total BESIDE EVERY PART that total is built from — "closing cash, opening
        cash and the period's flows" is the cash reconciliation rearranged, and summing it would
        count the flows twice.
    """
    from app.schemas.line_items import SECTION_SIDE

    children = list(item.children_if_decomposed)
    sides = {SECTION_SIDE.get(section_of.get(k, "")) for k in children} - {None}
    if "asset" in sides and sides & {"liability", "equity"}:
        return ("it lists figures from both sides of the balance sheet, which no line contains: "
                "that is the balance-sheet identity rearranged")
    listed = set(children)
    for k in children:
        parts = rollup_children.get(k) or []
        if len(parts) >= 2 and set(parts) <= listed:
            return (f"it lists {k} beside every part {k} is built from ({_few(parts)}): that is "
                    f"the identity {k} = Σ parts rearranged, and summing the list counts the parts "
                    f"twice")
    return None


def _check_line_item_set(c: _Collector, name: str, raw: dict,
                         templates: dict[str, tuple[str, dict]]) -> None:
    """A live set: the boot gate, the registry, the publish gate, and what all three leave open."""
    from app.sample.reference import ReferenceSeedError, _load_line_item_set
    from app.schemas.line_items import load_line_item_set
    from app.services import line_item_audit
    from app.services.line_item_matching import Vocabulary
    from app.services.line_items import build
    from app.services.provision_line_items import figure_bearing
    from app.services.rollups import calculated_nodes

    key = str(raw.get("line_items_key") or name)
    try:
        _load_line_item_set(pathlib.Path(name), raw)
    except ReferenceSeedError as exc:
        c.error(name, "boot-gate", key, f"the boot seed refuses this file: {exc}")
    try:
        st = load_line_item_set(raw, resolve=True)
    except Exception:                                  # noqa: BLE001 — reported by the boot gate
        return

    target = templates.get(st.target_template_key)
    if target is None:
        c.error(name, "target-template", st.target_template_key or "(none)",
                f"target_template_key {st.target_template_key!r} names no shipped template, so "
                f"nothing can be validated against it and no run can use it")
    template_raw = target[1] if target else None

    # THE REGISTRY VERDICT — duplicate keys, a term or cascade rung naming no line, a formula that
    # depends on itself, a parent that is not a line. `GET /line-items` shows it and nothing
    # enforces it: a cascade term typo publishes a figure that silently dropped one of its inputs.
    # One finding per line and kind, not per problem: a subtotal whose 50 terms name 31 missing
    # keys is one thing to fix, and 31 rows saying so bury everything else in the report.
    grouped: dict[tuple[str, str, str], list[str]] = collections.defaultdict(list)
    for problem in build(list(st.items)).problems:
        grouped[(problem.severity, _registry_check(problem.message), problem.key)].append(
            problem.message)
    for (severity, check, subject), messages in sorted(grouped.items()):
        report = c.error if severity == "error" else c.warn
        if check == "dangling-ref":
            refs = [m.split("'")[1] if m.count("'") >= 2 else m for m in messages]
            report(name, check, subject,
                   f"its terms or cascade rungs name {len(refs)} key(s) that are not line items in "
                   f"this set ({_few(refs)}); such a term never carries a figure, so an any_of "
                   f"input is silently dropped and a required one kills its rung")
        else:
            report(name, check, subject, "; ".join(dict.fromkeys(messages)))

    _publish_gate(c, name, st, template_raw)

    raw_items = {str(i.get("key")): i for i in raw.get("items") or () if isinstance(i, dict)}
    by_key: dict[str, Any] = {}
    for d in st.items:
        by_key.setdefault(d.key, d)

    # LOCALES. `aliases_i18n` is a plain dict, and the matcher indexes every locale's aliases
    # whatever the locale is called — a mistyped code makes its aliases live at confidence 1.0 for
    # every document and invisible to every locale-scoped read.
    known = _locales()
    for loc in st.supported_locales:
        if loc not in known:
            c.error(name, "locale", "supported_locales",
                    f"{loc!r} is not a locale the language registry knows "
                    f"({', '.join(sorted(known))})")
    undeclared: collections.Counter = collections.Counter()
    for d in by_key.values():
        bad = sorted(loc for loc in d.aliases_i18n if loc not in known)
        if bad:
            c.error(name, "locale", d.key,
                    f"aliases_i18n is filed under {_few(bad)}, which the language registry does "
                    f"not know; the matcher indexes them anyway, at full confidence, while every "
                    f"locale-scoped read leaves them out")
        undeclared.update(loc for loc, names in d.aliases_i18n.items()
                          if names and loc in known and loc not in st.supported_locales)
    if undeclared:
        filed = _few(f"{loc} ({n} items)" for loc, n in sorted(undeclared.items()))
        c.warn(name, "locale", "supported_locales",
               f"aliases are filed under {filed} but supported_locales says "
               f"{st.supported_locales}; the matcher uses them, the locale-scoped reads do not")

    # THE TEMPLATE BOUNDARY, both directions. Every figure line of the fixed template has a
    # definition (a missing one publishes a blank column on every filing), and every definition is
    # either a template column or a `sub__` part — any other key is a printed column the template
    # does not have. The publish gate checks only `namespace: template` keys, one direction.
    if template_raw is not None:
        columns = [str(n.get("canonical_key")) for n, _h, _s in figure_bearing(template_raw)]
        for k in columns:
            if k not in by_key:
                c.error(name, "template-key-missing", k,
                        "the template prints this line and the set does not configure it, so its "
                        "column is blank on every filing")
        for k in line_item_audit.keys_outside_the_template(raw, template_raw):
            c.error(name, "template-boundary", k,
                    f"neither a template column nor a {line_item_audit.PART_PREFIX} part: a new "
                    f"printed key, which the fixed template does not allow")
        column_set = set(columns)
        for d in by_key.values():
            if d.key.startswith(line_item_audit.PART_PREFIX) and (
                    d.namespace != "internal" or d.in_output):
                c.error(name, "template-boundary", d.key,
                        f"a part must be namespace 'internal' with in_output false; this one is "
                        f"namespace {d.namespace!r}, in_output {d.in_output}")
            if d.key in column_set and (d.namespace != "template" or not d.in_output):
                c.error(name, "template-boundary", d.key,
                        f"a template column configured as namespace {d.namespace!r}, in_output "
                        f"{d.in_output}, so its fixed column prints nothing")

    # WIRING. A part exists to feed a line; one whose figure reaches no output column extracts a
    # number nobody sees, and the count pins cannot tell (adding a part bumps them by design).
    unwired = _wiring(list(st.items))
    for k, why in sorted(unwired.items()):
        if k.startswith(line_item_audit.PART_PREFIX):
            c.error(name, "orphan-part", k, why)
        else:
            c.warn(name, "orphan-part", k,
                   why + " — an internal item that is not a part, named in "
                         "line_item_audit.NOT_A_COLUMN")

    # THE STATEMENT GATE ON AN ALIASED ITEM. Every gate field fails OPEN: an item carrying aliases
    # and no statement is claimable on every statement, at confidence 1.0 with no review.
    for d in by_key.values():
        if not (d.aliases or any(d.aliases_i18n.values())):
            continue
        if not d.statements:
            c.error(name, "face-gate", d.key,
                    "carries aliases and resolves to no statement, so a caption on ANY statement "
                    "binds it at full confidence")
        if not d.section_scope:
            declared_empty = "section_scope" in raw_items.get(d.key, {})
            if declared_empty:
                c.warn(name, "face-gate", d.key,
                       "carries aliases and declares an EMPTY section_scope, so it is claimable "
                       "under any banner of its statements — deliberate, judging by the "
                       "explicit declaration")
            else:
                c.error(name, "face-gate", d.key,
                        "carries aliases and resolves to no section_scope, so a caption under any "
                        "banner binds it")

    # SECTION SCOPE IDS. An id resolving to no banner constrains NOTHING — a typo widens the gate
    # instead of narrowing it. Known: a section of this set, a banner token, a compact scope
    # token, an id the vocabulary resolves, or a `*_top_level` statement-total sentinel.
    voc = Vocabulary(st.vocabulary)
    known_ids = (set(st.section_defaults) | {b.token for b in st.vocabulary.section_banners}
                 | set(st.vocabulary.scope_tokens))

    def unknown(ids: Iterable[str]) -> list[str]:
        return [s for s in ids if s and s not in known_ids and not s.endswith("_top_level")
                and voc.token_of_scope(s) is None]

    for section, defaults in sorted(st.section_defaults.items()):
        bad = unknown(defaults.section_scope)
        if bad:
            c.error(name, "section-scope", f"section_defaults.{section}",
                    f"section_scope {_few(bad)} names no section, so it constrains nothing")
    for k, item in sorted(raw_items.items()):
        bad = unknown(str(s) for s in item.get("section_scope") or ())
        if bad:
            c.error(name, "section-scope", k,
                    f"section_scope {_few(bad)} names no section, so it constrains nothing and a "
                    f"caption under any banner binds this line")

    # GROSS PARENTS, against the template that actually computes the aggregate.
    if template_raw is not None:
        rollup_children: dict[str, list[str]] = {}
        section_of: dict[str, str] = {}
        for stmt in template_raw.get("statements") or ():
            for header in stmt.get("sections") or ():
                for n in [header, *(header.get("children") or ())]:
                    rollup_children[str(n.get("canonical_key"))] = [
                        str(x) for x in (n.get("rollup") or {}).get("children") or ()]
                    section_of[str(n.get("canonical_key"))] = str(header.get("canonical_key"))
        calculated = set(calculated_nodes(template_raw))
        inert: list[str] = []
        for d in by_key.values():
            if d.children_if_decomposed and not d.is_gross_parent:
                c.warn(name, "gross-parent", d.key,
                       "children_if_decomposed without is_gross_parent is read by nothing")
            if not d.is_gross_parent:
                continue
            if not d.children_if_decomposed:
                c.warn(name, "gross-parent", d.key,
                       "is_gross_parent with no children_if_decomposed declares nothing")
                continue
            if d.key in calculated and d.extraction_mode == "extract_or_derive":
                inert.append(d.key)
            stray = [x for x in d.children_if_decomposed if x not in rollup_children.get(d.key, [])]
            if stray:
                c.error(name, "gross-parent-not-in-rollup", d.key,
                        f"children_if_decomposed names {_few(stray)}, which the template's rollup "
                        f"for this line does not: the two copies of what it contains disagree")
            why = _balancing_identity(d, rollup_children, section_of)
            if why:
                c.error(name, "gross-parent-identity", d.key,
                        f"is_gross_parent on a balancing identity, not a container: {why}")
        if inert:
            c.warn(name, "gross-parent", "(set)",
                   f"{len(inert)} is_gross_parent declaration(s) sit on aggregates the template "
                   f"calculates with extraction_mode extract_or_derive, which "
                   f"map_ontology._enforce_containment skips, so they are inert: {_few(inert, 4)}")


def _check_build_input_set(c: _Collector, name: str, raw: dict,
                           live_sets: dict[str, tuple[str, dict]]) -> None:
    """The configurator's hand-maintained half of the seed build: it must load; drift is a WARN."""
    from app.schemas.line_items import load_line_item_set

    try:
        load_line_item_set(raw, resolve=False)
        st = load_line_item_set(raw, resolve=True)
    except Exception as exc:                            # noqa: BLE001
        c.error(name, "load", str(raw.get("line_items_key") or name),
                f"does not load, so scripts/build_line_items.py cannot run: {exc}")
        return
    live = live_sets.get(st.line_items_key)
    if live is None:
        return
    # FIELD BY FIELD over what this file DECLARES: the build merges these declarations over the
    # ontology's projection, so the seed's item legitimately carries more — what matters is whether
    # a declared value still is the shipped one.
    seed = {str(i.get("key")): i for i in live[1].get("items") or ()}
    fields: collections.Counter = collections.Counter()
    differ: list[str] = []
    for item in raw.get("items") or ():
        shipped = seed.get(str(item.get("key")))
        if shipped is None:
            continue
        moved = [f for f in item if _canon(item[f]) != _canon(shipped.get(f))]
        if moved:
            differ.append(str(item.get("key")))
            fields.update(moved)
    absent = sorted(d.key for d in st.items if d.key not in seed)
    if differ or absent:
        c.warn(name, "stale-build-input", st.line_items_key,
               f"{len(differ)} of its {len(st.items)} definitions declare values {live[0]} no "
               f"longer carries (most often {_few(f for f, _n in fields.most_common())}"
               + (f"; {len(absent)} are not in it at all" if absent else "")
               + "), so scripts/build_line_items.py would put back what was since curated")


# ── the files nothing reads ───────────────────────────────────────────────────────────────────
_KEY_SHAPED = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)*__[a-z0-9_]+\b")

#: The blocks an unread file carries a copy of. The run reads the LIVE SET's copy
#: (`services.working_view`, `row_reconstruct.in_force_rules`), so a copy that differs is a rule
#: an author can edit with no effect.
_FRAMEWORK_BLOCKS = ("scope_selection", "normalisation", "binding", "residual_framework",
                     "global_rules")


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _pruned(value: Any) -> Any:
    """`value` with empty members dropped: an empty list and an absent key say the same thing."""
    if isinstance(value, dict):
        out = {k: _pruned(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_pruned(v) for v in value]
    return value


def _framework_drift(c: _Collector, name: str, raw: dict,
                     live: tuple[str, dict] | None) -> None:
    if live is None:
        return
    differ = [b for b in _FRAMEWORK_BLOCKS if b in raw
              and _canon(_pruned(raw.get(b))) != _canon(_pruned(live[1].get(b)))]
    for block in differ:
        c.warn(name, "unread-copy-drift", block,
               f"this copy of {block} differs from {live[0]}'s, which is the one a run reads; an "
               f"edit here governs nothing")


def _not_in_template(c: _Collector, name: str, subject: str, keys: Iterable[str],
                     template: tuple[str, set[str]] | None, what: str) -> None:
    if template is None:
        return
    missing = sorted({k for k in keys if k and k not in template[1]})
    if missing:
        c.warn(name, "unread-drift", subject,
               f"{what} {_few(missing)}, which {template[0]} does not declare")


def _prose_guards(c: _Collector, name: str, where: str, guards, keys) -> None:
    """A guard is PROSE, so no key check reaches it: the key-shaped words in one are checked."""
    for i, guard in enumerate(guards):
        _not_in_template(c, name, f"{where}[{i}]",
                         _KEY_SHAPED.findall(f"{guard.rule} {guard.note}"), keys, "names")


def _rebuild_drift(c: _Collector, raws: dict[str, Any], shapes: dict[str, str],
                   live_sets: dict[str, tuple[str, dict]]) -> None:
    """What `scripts/build_line_items.py` would lose: the live set's keys neither input defines.

    The build writes the ontology's concepts merged with the configured half, and nothing else — so
    a definition the live set has gained since (a part, a residual bucket) is not in either input,
    and a rebuild drops it without a word.
    """
    ontologies = {str(raws[n].get("ontology_key") or ""): n
                  for n in raws if shapes[n] == "ontology"}
    for name in sorted(n for n in raws if shapes[n] == "line_items"
                       and FILE_ROLES.get(n, ("",))[0] == BUILD_INPUT):
        set_key = str(raws[name].get("line_items_key") or "")
        live, ontology = live_sets.get(set_key), ontologies.get(set_key)
        if live is None or ontology is None:
            continue
        built = {str(m.get("canonical_key")) for m in raws[ontology].get("mappings") or ()}
        built |= {str(i.get("key")) for i in raws[name].get("items") or ()}
        shipped = [str(i.get("key")) for i in live[1].get("items") or ()]
        lost = [k for k in shipped if k not in built]
        if lost:
            c.warn(ontology, "stale-build-input", set_key,
                   f"rebuilt from this file and {name}, the set would hold {len(built)} "
                   f"definitions where {live[0]} holds {len(shipped)}: {len(lost)} would be "
                   f"dropped ({_few(lost, 4)})")


def _check_unread(c: _Collector, name: str, shape: str, raw: dict,
                  templates: dict[str, tuple[str, dict]],
                  live_by_target: dict[str, tuple[str, dict]]) -> None:
    """An ontology, rules or validation file. It must load (a script or a test reads it); anything
    it says that the template does not is drift, and a WARN."""
    from app.schemas.loader import (load_ontology, load_template, unknown_keys,
                                    validate_ontology_against_template)
    from app.schemas.rulebook_rules import load_rulebook_rules
    from app.schemas.validation_rules import load_validation_rules

    target_key = str(raw.get("target_template_key") or "")
    target = templates.get(target_key)
    template = None
    if target is None:
        c.warn(name, "unread-drift", target_key or "(none)",
               f"target_template_key {target_key!r} names no shipped template")
    else:
        try:
            template = load_template(target[1])
        except Exception:                              # noqa: BLE001 — the template reports it
            template = None
    keys = (target[0], template.all_canonical_keys()) if template is not None else None
    live = live_by_target.get(target_key)
    subject = str(raw.get("ontology_key") or raw.get("rules_key") or raw.get("validation_key")
                  or name)

    loaders = {"ontology": load_ontology, "rules": load_rulebook_rules,
               "validation": load_validation_rules}
    try:
        model = loaders[shape](raw)
        if shape == "ontology":
            load_ontology(raw, resolve=True)
    except Exception as exc:                            # noqa: BLE001
        c.error(name, "load", subject,
                f"does not load, so the script or test reading it fails: {exc}")
        return

    stray = unknown_keys(raw, model, limit=50)
    if stray:
        c.warn(name, "unread-unknown-field", subject,
               f"keys the schema does not declare, dropped in silence on load: {_few(stray)}")

    if shape == "ontology":
        if template is not None:
            for e in validate_ontology_against_template(model, template):
                c.warn(name, "unread-drift", e.location, e.message)
        counts = collections.Counter(m.canonical_key for m in model.mappings)
        for k, n in sorted(counts.items()):
            if n > 1:
                c.warn(name, "unread-drift", k,
                       f"defined {n} times; scripts/build_line_items.py would merge the copies")
        _not_in_template(c, name, "equivalence", (m.equivalence.with_ for m in model.mappings
                                                  if m.equivalence), keys,
                         "equivalence names")
        _not_in_template(c, name, "confusable_with",
                         (x for m in model.mappings for x in m.confusable_with), keys,
                         "confusable_with names")
        equivalences = [m.canonical_key for m in model.mappings if m.equivalence]
        if equivalences:
            c.warn(name, "unread-inert", "equivalence",
                   f"{len(equivalences)} concept(s) declare an equivalence ({_few(equivalences)}); "
                   f"the projection into a line-item set drops the field, so no run evaluates it")
        thresholds = [str(m.get("canonical_key")) for m in raw.get("mappings") or ()
                      if isinstance(m, dict) and "min_confidence_to_auto_accept" in m]
        if thresholds:
            c.warn(name, "unread-inert", "min_confidence_to_auto_accept",
                   f"{len(thresholds)} concept(s) declare an accept bar ({_few(thresholds)}) that "
                   f"nothing reads; the live bar is settings.extraction.auto_accept_confidence")
        if live is not None:
            _concept_drift(c, name, raw, live)
    elif shape == "rules":
        for rule, missing in sorted(model.unknown_keys(keys[1] if keys else set()).items()):
            if keys is not None:
                c.warn(name, "unread-drift", rule,
                       f"names {_few(missing)}, which {keys[0]} does not declare")
        # The check above walks validation, netting and decomposition only — a group naming a
        # missing key passes it.
        for group in model.global_rules.mutually_exclusive_groups:
            _not_in_template(c, name, f"mutually_exclusive_group:{group.id or '?'}",
                             [group.aggregate, *group.components], keys, "names")
        _prose_guards(c, name, "validation.cross_concept_guards",
                      model.validation.cross_concept_guards, keys)
        authored = {block: n for block, n in model.blocks_present().items()
                    if block in ("netting_rules", "decomposition_rules", "identities",
                                 "cross_concept_guards") and n}
        authored_groups = len(model.global_rules.mutually_exclusive_groups)
        if authored_groups:
            authored["mutually_exclusive_groups"] = authored_groups
        if authored:
            c.warn(name, "unread-inert", subject,
                   f"carries {_few(f'{n} {b}' for b, n in sorted(authored.items()))}, and no run "
                   f"reads this file — a run reads the live set's copy of these blocks")
    else:
        for identity, missing in sorted(model.unknown_keys(keys[1] if keys else set()).items()):
            if keys is not None:
                c.warn(name, "unread-drift", identity,
                       f"names {_few(missing)}, which {keys[0]} does not declare")
        _prose_guards(c, name, "cross_concept_guards", model.cross_concept_guards, keys)
        if model.identities or model.cross_concept_guards:
            c.warn(name, "unread-inert", subject,
                   f"carries {len(model.identities)} identities and "
                   f"{len(model.cross_concept_guards)} guards, and no run reads this file — a run "
                   f"evaluates the live set's own `validation` block")
    if shape != "validation":
        _framework_drift(c, name, raw, live)


def _concept_drift(c: _Collector, name: str, raw: dict, live: tuple[str, dict]) -> None:
    """How far the ontology's copy of each concept has drifted from the live set's.

    One summary, because the useful fact is the size of the gap: `scripts/build_line_items.py`
    projects this file's fields over the seed, so every concept counted here is a curated edit a
    rebuild would undo. Both sides RESOLVED, because that is the shape each one is matched in.
    """
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_ontology

    try:
        live_items = {d.key: d for d in load_line_item_set(live[1], resolve=True).items}
        resolved = {m.canonical_key: m for m in load_ontology(raw, resolve=True).mappings}
    except Exception:                                  # noqa: BLE001 — reported elsewhere
        return

    def i18n(d) -> dict[str, frozenset]:
        return {loc: frozenset(v) for loc, v in (d.aliases_i18n or {}).items() if v}

    def same_list(field: str):
        return lambda m, d: set(getattr(m, field) or ()) != set(getattr(d, field) or ())

    fields = {
        "aliases": same_list("aliases"),
        "aliases_i18n": lambda m, d: i18n(m) != i18n(d),
        "exclude_hints": same_list("exclude_hints"),
        "keyword_hints": same_list("keyword_hints"),
        "regex_hints": same_list("regex_hints"),
        "match_priority": lambda m, d: m.match_priority != d.match_priority,
        "extraction_mode": lambda m, d: m.extraction_mode != d.extraction_mode,
        "item_type/type": lambda m, d: m.item_type != d.type,
    }
    counts = {f: sorted(k for k, m in resolved.items()
                        if k in live_items and test(m, live_items[k]))
              for f, test in fields.items()}
    counts = {f: ks for f, ks in counts.items() if ks}
    if counts:
        c.warn(name, "unread-copy-drift", "concepts",
               f"differs from {live[0]} on "
               + "; ".join(f"{f} for {len(ks)}" for f, ks in counts.items())
               + " concept(s) — the live set is what a run reads, and rebuilding it from this file "
                 "would undo those edits")


# ── the run ───────────────────────────────────────────────────────────────────────────────────
def validate_files(raws: dict[str, Any], *, complete: bool = False) -> Report:
    """Validate parsed reference files, keyed by their file name.

    ``complete`` says ``raws`` is the WHOLE templates directory, so a file the role table lists and
    the directory lacks, or the reverse, is worth saying. A test validating two files is not.
    """
    c = _Collector()
    shapes = {name: _shape(raw) for name, raw in raws.items()}
    roles = {name: FILE_ROLES.get(name, ("unclassified", ""))[0] for name in raws}

    for name, raw in raws.items():
        if shapes[name] == "unknown":
            c.error(name, "load", name, "not a reference file this script recognises "
                                        "(no template, line-item, ontology, rules or validation "
                                        "envelope)")

    def live(name: str) -> bool:
        return roles[name] in (LIVE, "unclassified")

    set_names = [n for n in raws if shapes[n] == "line_items" and live(n)]
    targets = {str(raws[n].get("target_template_key") or "") for n in set_names}

    templates: dict[str, tuple[str, dict]] = {}
    for name in sorted(n for n in raws if shapes[n] == "template"):
        raw = raws[name]
        tkey = str(raw.get("template_key") or "")
        if tkey in templates:
            c.error(name, "duplicate-template-key", tkey,
                    f"{templates[tkey][0]} ships the same template_key, so the boot seed publishes "
                    f"the two files as versions of one template")
        templates.setdefault(tkey, (name, raw))
        _check_template(c, name, raw, configured=tkey in targets)

    live_sets: dict[str, tuple[str, dict]] = {}
    live_by_target: dict[str, tuple[str, dict]] = {}
    for name in sorted(set_names):
        raw = raws[name]
        live_sets.setdefault(str(raw.get("line_items_key") or ""), (name, raw))
        live_by_target.setdefault(str(raw.get("target_template_key") or ""), (name, raw))
        _check_line_item_set(c, name, raw, templates)
    for name in sorted(n for n in raws if shapes[n] == "line_items" and not live(n)):
        _check_build_input_set(c, name, raws[name], live_sets)
    for name in sorted(n for n in raws if shapes[n] in ("ontology", "rules", "validation")):
        _check_unread(c, name, shapes[name], raws[name], templates, live_by_target)
    _rebuild_drift(c, raws, shapes, live_sets)

    if complete:
        for name in sorted(set(raws) - set(FILE_ROLES)):
            c.warn(name, "unclassified", name,
                   "not in FILE_ROLES or app/sample/templates/README.md, so nobody has said "
                   "whether the product reads it")
        for name in sorted(set(FILE_ROLES) - set(raws)):
            c.warn(name, "unclassified", name,
                   "listed in FILE_ROLES but not shipped; drop it from the table and the README")

    report = Report(files={n: _describe(n, shapes.get(n, "")) for n in
                           set(raws) | (set(FILE_ROLES) if complete else set())})
    excused: set[KnownDefect] = set()
    for f in c.findings:
        entry = next((k for k in KNOWN_DEFECTS if f.level == ERROR
                      and (k.file, k.check, k.subject) == (f.file, f.check, f.subject)), None)
        if entry is None:
            report.findings.append(f)
        else:
            report.known.append((f, entry))
            excused.add(entry)
    report.stale = [k for k in KNOWN_DEFECTS if k.file in raws and k not in excused]
    return report


def _describe(name: str, shape: str) -> str:
    role, what = FILE_ROLES.get(name, ("unclassified", shape))
    return f"{role}: {what}" if what else role


def validate(directory: pathlib.Path = TEMPLATES) -> Report:
    """Validate every ``*.json`` file in a templates directory (the shipped one by default)."""
    raws: dict[str, Any] = {}
    unreadable: list[Finding] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raws[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            unreadable.append(Finding(ERROR, path.name, "load", path.name, f"not JSON: {exc}"))
    report = validate_files({n: r for n, r in raws.items()}, complete=True)
    report.findings[:0] = unreadable
    for f in unreadable:
        report.files.setdefault(f.file, _describe(f.file, ""))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", type=pathlib.Path, default=TEMPLATES,
                        help="the templates directory to validate (default: the shipped one)")
    parser.add_argument("--errors-only", action="store_true",
                        help="print ERROR and KNOWN findings only")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = validate(args.dir)
    print(report.render(errors_only=args.errors_only))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
