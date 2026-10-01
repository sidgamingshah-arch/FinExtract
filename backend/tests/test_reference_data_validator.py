"""THE REFERENCE-DATA VALIDATOR, held to the shipped files and to the breaks it exists to catch.

NEW FILE -> backend/tests/test_reference_data_validator.py

`scripts/validate_reference_data.py` runs the checks the load and publish gates do not. Every
break exercised below was first made on a copy of a shipped file and offered to every door the
product has — and was ACCEPTED SILENTLY by all of them: it loaded, booted, published and then
computed something else. So each test here is one of those copies, and asserts the validator names
it, by check, where the gates said nothing.

THREE THINGS ARE PINNED BESIDE THE BREAKS:

  * the shipped files carry no ERROR, and every entry on the known-defect list is still found — so
    the list shrinks when a fix lands instead of quietly excusing whatever comes next;
  * the role table (which files the product reads) is what `sample.reference` actually seeds and
    what app code actually names — measured here, not restated;
  * the templates directory's README classifies every file it ships.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import pathlib
import shutil
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = BACKEND / "app" / "sample" / "templates"

HK_TEMPLATE = "output_csv_hk_v1_template.json"
HK_SET = "output_csv_hk_line_items.json"
INDAS_TEMPLATE = "output_csv_indas_v1_template.json"
INDAS_SET = "output_csv_indas_line_items.json"
HK_ONTOLOGY = "output_csv_hk_ontology.json"
HK_RULES = "output_csv_hk_rules.json"
HK_VALIDATION = "output_csv_hk_validation.json"


def _load_validator():
    """The script, loaded by path — it is a script, not a package module."""
    path = BACKEND / "scripts" / "validate_reference_data.py"
    spec = importlib.util.spec_from_file_location("_validate_reference_data", path)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE it runs: its dataclasses resolve their (postponed) annotations through
    # `sys.modules`, and an unregistered module makes the class statement itself raise.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V = _load_validator()


@pytest.fixture(scope="module")
def shipped() -> dict:
    return {p.name: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(TEMPLATES.glob("*.json"))}


@pytest.fixture(scope="module")
def report():
    return V.validate()


def _run(shipped: dict, names: tuple[str, ...], mutate=None):
    """Validate deep copies of the named shipped files, after `mutate(files)` has broken them."""
    files = {n: copy.deepcopy(shipped[n]) for n in names}
    if mutate is not None:
        mutate(files)
    return V.validate_files(files)


def _found(report, check: str, subject: str | None = None, *, level: str = "ERROR") -> list:
    pool = report.errors() if level == "ERROR" else report.warnings()
    return [f for f in pool if f.check == check and (subject is None or f.subject == subject)]


def _nodes(template: dict):
    def walk(nodes):
        for n in nodes or ():
            yield n
            yield from walk(n.get("children"))
    for stmt in template["statements"]:
        yield from walk(stmt["sections"])


def _node(template: dict, key: str) -> dict:
    return next(n for n in _nodes(template) if n.get("canonical_key") == key)


def _item(st: dict, key: str) -> dict:
    return next(i for i in st["items"] if i["key"] == key)


# ── the shipped files ─────────────────────────────────────────────────────────────────────────

def test_the_shipped_reference_data_carries_no_error(report):
    """CALIBRATED ON THE FILES AS THEY SHIP: everything left is a WARN, or a KNOWN defect someone
    is fixing. Any new ERROR here is a defect a change has just introduced."""
    assert not report.errors(), "\n" + report.render(errors_only=True)
    assert report.exit_code == 0


def test_every_known_defect_is_still_found(report):
    """THE ALLOWLIST MUST SHRINK. An entry nothing matches any more is a fix that landed — remove
    it from `KNOWN_DEFECTS`, or it waits there to excuse the same defect coming back."""
    assert not report.stale, (
        "these KNOWN_DEFECTS entries no longer match a finding — their fix has landed, so remove "
        "them: " + "; ".join(f"{k.file} {k.check} {k.subject}" for k in report.stale))


def test_a_known_defect_is_an_error_and_never_a_warning(report):
    """The list excuses ERRORs. A WARN is already reported without failing, so an entry for one
    would only hide it from the report."""
    assert report.known
    assert {f.level for f, _k in report.known} == {"ERROR"}
    assert all((f.file, f.check, f.subject) == (k.file, k.check, k.subject)
               for f, k in report.known)


def test_a_known_defect_excuses_only_the_finding_it_names(shipped):
    """The Ind AS gross profit's copied HK terms are KNOWN; the same defect on a key the list does
    not name is still an ERROR."""
    def foreign_term(files):
        _item(files[INDAS_SET], "is_pl__profit_loss_before_tax").setdefault("terms", []).append(
            {"ref": "is_pl__no_such_line", "sign": 1})

    r = _run(shipped, (INDAS_TEMPLATE, INDAS_SET), foreign_term)
    assert _found(r, "dangling-ref", "is_pl__profit_loss_before_tax")
    assert "is_pl__gross_profit" in {k.subject for f, k in r.known if f.check == "dangling-ref"}


def test_a_fixed_known_defect_is_reported_as_a_stale_entry(shipped):
    """The mechanism behind the test above, on a copy where the fix has landed: the Ind AS gross
    profit's terms trimmed to keys the set has, its entry is named stale and no longer excuses
    anything — while every other entry, still matched, is not called stale."""
    def trim(files):
        st = files[INDAS_SET]
        keys = {i["key"] for i in st["items"]}
        item = _item(st, "is_pl__gross_profit")
        item["terms"] = [t for t in item["terms"] if t["ref"] in keys]

    r = _run(shipped, (INDAS_TEMPLATE, INDAS_SET), trim)
    assert not _found(r, "dangling-ref", "is_pl__gross_profit")
    assert [(k.file, k.check, k.subject) for k in r.stale] == [
        (INDAS_SET, "dangling-ref", "is_pl__gross_profit")]
    assert "STALE ALLOWLIST ENTRY" in r.render()


def test_the_report_is_grouped_by_file_and_the_exit_code_follows_the_errors(tmp_path, capsys):
    """The CLI end to end, on a copy of the directory: clean exits 0, one broken file exits 1, and
    the finding is printed under that file's own heading."""
    copy_dir = tmp_path / "templates"
    shutil.copytree(TEMPLATES, copy_dir)
    assert V.main(["--dir", str(copy_dir)]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().splitlines()[-1].startswith("PASS")

    path = copy_dir / HK_TEMPLATE
    template = json.loads(path.read_text(encoding="utf-8"))
    leaf = _node(template, "supplemental_data__exchange_rate_period_end")
    leaf["node_id"] = "is_pl__cost_of_sales"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    assert V.main(["--dir", str(copy_dir), "--errors-only"]) == 1
    out = capsys.readouterr().out
    block = out.split(f"{HK_TEMPLATE}  [")[1].split("\n\n")[0]
    assert "ERROR  duplicate-node-id  is_pl__cost_of_sales" in block
    assert out.rstrip().splitlines()[-1].startswith("FAIL: 1 error(s)")


# ── templates: what every gate accepted ───────────────────────────────────────────────────────

@pytest.mark.parametrize("template_name", [HK_TEMPLATE, INDAS_TEMPLATE])
def test_a_rollup_cycle_is_an_error_in_either_regime(shipped, template_name):
    def cycle(files):
        _node(files[template_name], "is_pl__total_cost_of_sales")["rollup"]["children"].append(
            "is_pl__gross_profit")

    assert _found(_run(shipped, (template_name,), cycle), "rollup-cycle",
                  "is_pl__gross_profit, is_pl__total_cost_of_sales")


def test_a_repeated_node_id_is_an_error(shipped):
    """Probe 1b, variant A: an exchange-rate leaf took Cost of Sales' node_id, and the structural
    check for Total Cost of Sales was rewired onto it while the evaluator read the right line."""
    def repeat(files):
        _node(files[HK_TEMPLATE], "supplemental_data__exchange_rate_period_end")["node_id"] = \
            "is_pl__cost_of_sales"

    assert _found(_run(shipped, (HK_TEMPLATE,), repeat), "duplicate-node-id",
                  "is_pl__cost_of_sales")


def test_a_reported_total_key_naming_nothing_is_an_error(shipped):
    """Probe 8: the residual 100 − 15 = 85 became 15, still marked computable."""
    def typo(files):
        _node(files[HK_TEMPLATE], "bs_nca__deriv_and_hedg_assets_ltp")["rollup"][
            "reported_total_key"] = "bs_nca__deriv_and_hedg_assets_ltpX"

    assert _found(_run(shipped, (HK_TEMPLATE,), typo), "reported-total-key",
                  "bs_nca__deriv_and_hedg_assets_ltp")


def test_a_child_named_by_a_node_id_that_is_not_its_key_is_an_error(shipped):
    """Probe 12: the gate checks node ids, the evaluator reads canonical keys — −700 became −600."""
    def split(files):
        template = files[HK_TEMPLATE]
        _node(template, "is_pl__goods_and_services")["node_id"] = "pl_goods_node"
        rollup = _node(template, "is_pl__total_cost_of_sales")["rollup"]
        for field in ("children", "cost_magnitude_children"):
            rollup[field] = ["pl_goods_node" if c == "is_pl__goods_and_services" else c
                             for c in rollup.get(field) or ()]

    r = _run(shipped, (HK_TEMPLATE,), split)
    assert _found(r, "rollup-child-node-id", "is_pl__total_cost_of_sales")
    assert not _found(r, "boot-gate"), "the gate accepts this shape — which is the point"


def test_a_label_filed_under_an_unknown_locale_is_an_error(shipped):
    """Probe 10: 'ZH' and 'xx-NOPE' were accepted and the Chinese label was never served."""
    def mislabel(files):
        _node(files[HK_TEMPLATE], "is_pl__gross_profit")["label_i18n"].update(
            {"xx-NOPE": "Bogus", "ZH": "毛利"})

    assert _found(_run(shipped, (HK_TEMPLATE,), mislabel), "locale", "is_pl__gross_profit")


def test_a_stray_magnitude_member_behind_reported_total_components_is_an_error(shipped):
    """Probe 9b: the schema's own check is skipped when `use_reported_total_components` is set."""
    def stray(files):
        _node(files[HK_TEMPLATE], "cf_investing__other_invest_cash_flows")["rollup"][
            "cost_magnitude_children"] = ["cf_investing__no_such_line"]

    assert _found(_run(shipped, (HK_TEMPLATE,), stray), "cost-magnitude-children",
                  "cf_investing__other_invest_cash_flows")


def test_a_subtotal_nested_below_a_line_is_an_error(shipped):
    """Probe 11a: rollups reads sections and their direct children only, so it stopped computing."""
    def nest(files):
        section = next(s for s in files[HK_TEMPLATE]["statements"]
                       if s["type"] == "profit_and_loss")["sections"][0]
        node = next(n for n in section["children"] if n["canonical_key"] == "is_pl__gross_profit")
        section["children"].remove(node)
        revenue = next(n for n in section["children"]
                       if n["canonical_key"] == "is_pl__sales_revenues")
        revenue.setdefault("children", []).append(node)

    assert _found(_run(shipped, (HK_TEMPLATE,), nest), "node-depth", "is_pl__gross_profit")


def test_a_headless_line_is_an_error_on_a_configured_template(shipped):
    """Probe 11b: a figure line at statement top level provisions an item with no section gate."""
    def hoist(files):
        stmt = next(s for s in files[HK_TEMPLATE]["statements"] if s["type"] == "profit_and_loss")
        section = stmt["sections"][0]
        node = next(n for n in section["children"] if n["canonical_key"] == "is_pl__gross_profit")
        section["children"].remove(node)
        stmt["sections"].append(node)

    r = _run(shipped, (HK_TEMPLATE, HK_SET), hoist)
    assert _found(r, "headless-line")
    # …and only a curiosity where nothing configures the template: the shipped HKFRS one.
    alone = _run(shipped, ("hkfrs_hk_china_template.json",))
    assert _found(alone, "headless-line", level="WARN") and not _found(alone, "headless-line")


def test_what_the_boot_gate_refuses_is_still_reported(shipped):
    """The control: a missing child IS refused at boot, and the validator runs that gate first."""
    def dangle(files):
        _node(files[HK_TEMPLATE], "is_pl__total_cost_of_sales")["rollup"]["children"].append(
            "is_pl__no_such_line")

    assert _found(_run(shipped, (HK_TEMPLATE,), dangle), "boot-gate")


# ── line-item sets: what every gate accepted ──────────────────────────────────────────────────

def _set_run(shipped, mutate):
    return _run(shipped, (HK_TEMPLATE, HK_SET), lambda files: mutate(files[HK_SET]))


def test_a_duplicate_key_is_an_error(shipped):
    """Probe 1: both definitions went live — the matcher kept the last, the registry the first."""
    def duplicate(st):
        st["items"].append({**copy.deepcopy(_item(st, "bs_nca__land")), "label": "Land DUPLICATE"})

    assert _found(_set_run(shipped, duplicate), "duplicate-key", "bs_nca__land")


def test_a_cascade_term_naming_no_line_is_an_error(shipped):
    """Probe 2: MAX(100, 500, 200) published 200 — one Find quietly dropped."""
    def typo(st):
        rung = _item(st, "bs_nca__due_from_related_parties_ltp")["cascade"][0]
        for term in rung["terms"]:
            if term["ref"] == "sub__rp_find_2":
                term["ref"] = "sub__rp_find_2_TYPO"

    assert _found(_set_run(shipped, typo), "dangling-ref", "bs_nca__due_from_related_parties_ltp")


def test_a_cascade_cycle_is_an_error(shipped):
    """Probe 3: the printed parent fed back into Find 1 and was published 11x too large."""
    def cycle(st):
        _item(st, "sub__rp_find_1")["cascade"][0]["terms"].append(
            {"ref": "bs_nca__due_from_related_parties_ltp", "role": "any_of", "sign": 1})

    assert _found(_set_run(shipped, cycle), "formula-cycle")


def test_an_alias_filed_under_an_unknown_locale_is_an_error(shipped):
    """Probe 5: matched at confidence 1.0 while invisible to every locale-scoped read."""
    def mislabel(st):
        _item(st, "bs_nca__land")["aliases_i18n"]["xx_INVALID"] = ["Zebra land"]

    assert _found(_set_run(shipped, mislabel), "locale", "bs_nca__land")


_ORPHAN = {"key": "sub__orphan_probe", "label": "Orphan probe", "namespace": "internal",
           "in_output": False, "inherits": "notes"}


def test_a_part_nothing_reads_is_an_error(shipped):
    """Probe 6a: the part's figure stayed on its own key and reached no published line."""
    assert _found(_set_run(shipped, lambda st: st["items"].append(dict(_ORPHAN))),
                  "orphan-part", "sub__orphan_probe")


def test_a_part_its_parents_cascade_does_not_name_is_an_error(shipped):
    """Probe 6b: the run log said the part "joins the cascade"; the parent published without it."""
    orphan = {**_ORPHAN, "parent": "bs_nca__due_from_related_parties_ltp"}
    found = _found(_set_run(shipped, lambda st: st["items"].append(orphan)),
                   "orphan-part", "sub__orphan_probe")
    assert found and "computes from its own cascade" in found[0].message


def test_a_part_unwired_from_its_parent_is_an_error(shipped):
    """Probe 6c: depreciation published 20 against 70, the item count unchanged."""
    def unwire(st):
        for item in st["items"]:
            for rung in item.get("cascade") or ():
                rung["terms"] = [t for t in rung["terms"] if t.get("ref") != "sub__rd_depreciation"]

    assert _found(_set_run(shipped, unwire), "orphan-part", "sub__rd_depreciation")


def test_a_part_whose_parent_is_a_part_that_computes_nothing_is_an_error(shipped):
    """Probe 12b: the income half was enrolled under the expenses part and disappeared."""
    def reparent(st):
        _item(st, "sub__non_operating_income")["parent"] = "sub__non_operating_expenses"

    assert _found(_set_run(shipped, reparent), "orphan-part", "sub__non_operating_income")


def test_a_parent_naming_no_line_is_an_error(shipped):
    """Probe 12a: a PHANTOM row was written under the typo and the real parent understated."""
    def typo(st):
        _item(st, "sub__non_operating_income")["parent"] = "is_pl__other_non_operating_inc_exp_TYPO"

    assert _found(_set_run(shipped, typo), "dangling-parent", "sub__non_operating_income")


@pytest.mark.parametrize("strip", [
    ("statement", "section_scope", "inherits"),    # probe 7a: everything that placed it
    ("statement",),                                # probe 7c: caught by nothing before
])
def test_an_aliased_part_without_a_statement_gate_is_an_error(shipped, strip):
    """A depreciation caption on ANY statement bound to a cash-flow part at confidence 1.0."""
    def ungate(st):
        item = _item(st, "sub__cfo_depreciation")
        for field in strip:
            item.pop(field, None)
        if strip == ("statement",):
            item["statement"] = None

    assert _found(_set_run(shipped, ungate), "face-gate", "sub__cfo_depreciation")


def test_a_new_aliased_part_with_no_gate_is_an_error(shipped):
    """Probe 7b: 'Zebra income' matched on the balance sheet, the cash flow and the P&L alike."""
    part = {"key": "sub__probe_face_part", "label": "Probe", "namespace": "internal",
            "in_output": False, "parent": "is_pl__other_non_operating_inc_exp",
            "aliases": ["Zebra income"]}
    found = _found(_set_run(shipped, lambda st: st["items"].append(part)), "face-gate",
                   "sub__probe_face_part")
    assert len(found) == 2, "no statement AND no section scope"


def test_a_section_scope_naming_no_section_is_an_error(shipped):
    """Probe 9: the typo WIDENED the gate — 'Land' under CURRENT LIABILITIES matched at 1.0."""
    def typo(st):
        _item(st, "bs_nca__land")["section_scope"] = ["bs_xyz"]

    assert _found(_set_run(shipped, typo), "section-scope", "bs_nca__land")


def test_the_shipped_non_section_scope_ids_are_not_errors(report):
    """The calibration that made probe 9 checkable: `bs_top_level`, `profit_attributable_to` and
    the five compact sections no banner names are deliberate, and the shipped sets use them."""
    assert not [f for f in report.findings if f.check == "section-scope"]


def test_a_template_line_the_set_does_not_configure_is_an_error(shipped):
    """Probe 10a: 'Work in progress' went unmatched and its fixed column blank on every filing."""
    def drop(st):
        st["items"] = [i for i in st["items"] if i["key"] != "bs_ca__wip"]

    assert _found(_set_run(shipped, drop), "template-key-missing", "bs_ca__wip")


@pytest.mark.parametrize("namespace", ["template", "internal"])
def test_a_key_that_is_neither_a_column_nor_a_part_is_an_error(shipped, namespace):
    """Probes 10b/10c: the boot seed published both; the upload door refused only the first."""
    item = {"key": "bs_nca__probe_new_column", "label": "Probe", "namespace": namespace,
            "inherits": "bs_nca", "aliases": ["Zebra land"]}
    if namespace == "internal":
        item["in_output"] = False
    r = _set_run(shipped, lambda st: st["items"].append(item))
    assert _found(r, "template-boundary", "bs_nca__probe_new_column")
    assert bool(_found(r, "publish-gate", "bs_nca__probe_new_column")) is (namespace == "template")


def test_what_only_the_upload_door_refuses_is_reported(shipped):
    """The boot seed skips the publish gate's pairing checks; a group naming a missing key boots."""
    def group(st):
        st["global_rules"]["mutually_exclusive_groups"] = [{
            "id": "probe_land_group", "aggregate": "bs_nca__land",
            "components": ["bs_nca__buildings", "bs_nca__land_probe_missing"]}]

    assert _found(_set_run(shipped, group), "publish-gate",
                  "mutually_exclusive_group:probe_land_group")


def test_what_the_boot_gate_refuses_on_a_set_is_still_reported(shipped):
    def mistype(st):
        _item(st, "bs_nca__land")["type"] = "extractd"

    assert _found(_set_run(shipped, mistype), "boot-gate")


def test_a_gross_parent_child_outside_the_template_rollup_is_an_error(shipped):
    """The two copies of what a subtotal contains must agree: 30 of 31 shipped lists are subsets of
    the template's rollup, and the one that is not is the retained-profits identity."""
    def widen(st):
        _item(st, "bs_nca__gross_fixed_assets")["children_if_decomposed"].append("bs_nca__goodwill")

    r = _set_run(shipped, widen)
    assert _found(r, "gross-parent-not-in-rollup", "bs_nca__gross_fixed_assets")
    assert not _found(r, "gross-parent-identity", "bs_nca__gross_fixed_assets")


def test_a_gross_parent_declared_on_a_balancing_identity_is_an_error(shipped):
    """A list spanning both sides of the balance sheet is the balance-sheet identity rearranged."""
    def identity(st):
        children = _item(st, "bs_cl__total_liabilities")["children_if_decomposed"]
        children.append("bs_ca__total_assets")

    assert _found(_set_run(shipped, identity), "gross-parent-identity", "bs_cl__total_liabilities")


# ── the files nothing reads: drift is a WARN, a file that will not load is an ERROR ──────────

def test_an_unread_file_naming_a_key_the_template_lacks_is_a_warning(shipped):
    """Ontology probe 2: the template-key check exists and nothing called it on this file."""
    def rename(files):
        next(m for m in files[HK_ONTOLOGY]["mappings"]
             if m["canonical_key"] == "bs_nca__land")["canonical_key"] = "bs_nca__land_probe_typo"

    r = _run(shipped, (HK_TEMPLATE, HK_SET, HK_ONTOLOGY), rename)
    assert _found(r, "unread-drift", "mapping:bs_nca__land_probe_typo", level="WARN")
    assert not [f for f in r.errors() if f.file == HK_ONTOLOGY]


def test_a_misspelt_field_in_an_unread_file_is_a_warning(shipped):
    """Ontology probe 3d: `extraction_mod` was dropped and the line extracted after all."""
    def misspell(files):
        mapping = next(m for m in files[HK_ONTOLOGY]["mappings"]
                       if m["canonical_key"] == "bs_nca__land")
        mapping["extraction_mod"] = "do_not_extract"

    r = _run(shipped, (HK_TEMPLATE, HK_ONTOLOGY), misspell)
    found = _found(r, "unread-unknown-field", level="WARN")
    assert found and "extraction_mod" in found[0].message


def test_a_rules_group_naming_a_missing_key_is_a_warning(shipped):
    """Rules probe 6r: `RulebookRules.unknown_keys` walks netting, decomposition and identities and
    skips the groups, so a prohibition naming nothing passed the check written to stop it."""
    def group(files):
        files[HK_RULES]["global_rules"]["mutually_exclusive_groups"] = [{
            "id": "probe_land_group", "aggregate": "bs_nca__land",
            "components": ["bs_nca__buildings", "bs_nca__land_probe_missing"]}]

    r = _run(shipped, (HK_TEMPLATE, HK_RULES), group)
    assert _found(r, "unread-drift", "mutually_exclusive_group:probe_land_group", level="WARN")
    assert _found(r, "unread-inert", level="WARN"), "a rule here governs nothing, and says so"


def test_an_undeclared_block_or_field_in_an_unread_file_is_a_warning(shipped):
    """Rules probe 4a and validation probe 5d: a new top-level rule type, and a `tolerance` on an
    identity, were both dropped on load without a word."""
    def undeclared(files):
        files[HK_RULES]["probe_rule_type"] = [{"id": "probe", "keys": ["bs_nca__land"]}]
        files[HK_VALIDATION]["identities"] = [
            {"id": "probe_identity",
             "expr": "bs_ca__total_assets = bs_cl__total_equity_and_liabilities",
             "severity": "warning", "tolerance": 5}]

    r = _run(shipped, (HK_TEMPLATE, HK_RULES, HK_VALIDATION), undeclared)
    found = {f.file: f.message for f in _found(r, "unread-unknown-field", level="WARN")}
    assert "probe_rule_type" in found.get(HK_RULES, "")
    assert "tolerance" in found.get(HK_VALIDATION, "")


def test_a_validation_guard_naming_a_missing_key_is_a_warning(shipped):
    """Validation probe 5b: a guard is prose, so no key check ever reached it."""
    def guard(files):
        files[HK_VALIDATION]["cross_concept_guards"] = [
            {"rule": "never load bs_nca__land_probe_missing together with bs_nca__buildings"}]

    r = _run(shipped, (HK_TEMPLATE, HK_VALIDATION), guard)
    found = _found(r, "unread-drift", "cross_concept_guards[0]", level="WARN")
    assert found and "bs_nca__land_probe_missing" in found[0].message
    assert "bs_nca__buildings" not in found[0].message


def test_an_unread_file_that_will_not_load_is_an_error(shipped):
    """Drift is a WARN; a file the scripts and tests that read it cannot load is not."""
    def break_severity(files):
        files[HK_VALIDATION]["identities"] = [
            {"id": "x", "expr": "bs_ca__total_assets = bs_cl__total_equity_and_liabilities",
             "severity": "fatal"}]

    assert _found(_run(shipped, (HK_TEMPLATE, HK_VALIDATION), break_severity), "load")


# ── which files the product reads ─────────────────────────────────────────────────────────────

def test_the_role_table_lists_exactly_what_the_boot_seeds_as_live():
    """`FILE_ROLES` says which files are live. Held to `sample.reference`'s own lists and to the
    one file app code reads off disk, so the table cannot drift from the seeder."""
    from app.sample import reference
    from app.services import line_item_config

    seeded = {reference._TEMPLATE.name, line_item_config.SEED.name}
    seeded |= {p.name for pair in reference._EXTRA_PAIRS for p in pair}
    live = {name for name, (role, _what) in V.FILE_ROLES.items() if role == V.LIVE}
    assert live == seeded


def test_every_shipped_file_is_classified():
    shipped_names = {p.name for p in TEMPLATES.glob("*.json")}
    assert shipped_names == set(V.FILE_ROLES)


def _code_strings(path: pathlib.Path) -> list[str]:
    """Every string literal in a module that is NOT a docstring or a bare string statement — the
    strings code can open a file with, as opposed to prose about one."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = {id(node.value) for node in ast.walk(tree)
             if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in prose]


def test_no_app_code_names_a_file_the_role_table_calls_unread():
    """THE MEASUREMENT BEHIND THE ROLE TABLE, repeated on every run. The unread files are named in
    app code only in docstrings and comments; a string literal naming one is code that may open
    it, and the table (and the README) would then be wrong."""
    unread = {name for name, (role, _what) in V.FILE_ROLES.items() if role != V.LIVE}
    hits = sorted(f"{path.relative_to(BACKEND)}: {name}"
                  for path in (BACKEND / "app").rglob("*.py")
                  for literal in _code_strings(path)
                  for name in unread if name in literal)
    assert not hits, hits


def test_the_templates_readme_classifies_every_shipped_file():
    """One row per file, carrying the role the validator gives it."""
    rows = [line for line in (TEMPLATES / "README.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("|")]
    for name, (role, _what) in V.FILE_ROLES.items():
        row = next((r for r in rows if f"`{name}`" in r), None)
        assert row is not None, f"README.md has no row for {name}"
        assert f"| {role} |" in row, f"README.md calls {name} something other than {role!r}: {row}"
