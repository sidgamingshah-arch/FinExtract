"""Repair and publish the output_csv_hk ontology as one reproducible definition."""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.schemas.loader import load_ontology
from app.services.mapping import normalize_label


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "sample" / "templates" / "output_csv_hk_ontology.json"
TEMPLATE = ROOT / "app" / "sample" / "templates" / "output_csv_hk_v1_template.json"
DATABASE = ROOT / "finex.db"
ONTOLOGY_KEY = "output_csv_hk"
TEMPLATE_KEY = "output_csv_hk_v1"

RESIDUAL_BY_SECTION = {
    "bs_nca": "bs_nca__other_non_current_assets",
    "bs_ca": "bs_ca__other_current_assets",
    "bs_equity": "bs_equity__other_reserves",
    "bs_ncl": "bs_ncl__other_non_current_liabilities",
    "bs_cl": "bs_cl__other_current_liabilities",
    "is_pl": "is_pl__other_operating_expenses",
    "is_oci": "is_oci__other_equity_and_reserves_adj",
    "is_retained": "is_retained__other_adj_to_retained_profits",
    "cf_oper_indirect": "cf_oper_indirect__other_non_cash_adjs_oper",
    "cf_investing": "cf_investing__other_invest_cash_flows",
    "cf_financing": "cf_financing__other_financing_cash_flows",
}


def _nodes(nodes: list[dict]):
    for node in nodes or []:
        yield node
        yield from _nodes(node.get("children") or [])


def _calculated_keys(template: dict) -> set[str]:
    return {
        node["canonical_key"]
        for statement in template.get("statements") or []
        for node in _nodes(statement.get("sections") or [])
        if node.get("canonical_key") and node.get("rollup")
    }


def repair_template_rollups(definition: dict, template: dict) -> dict:
    """Expand only ranges explicitly authored as ``{first}:{last}`` in formulas."""
    template = copy.deepcopy(template)
    mappings = {item["canonical_key"]: item for item in definition.get("mappings") or []}
    section_rows: dict[str, list[str]] = {}
    for statement in template.get("statements") or []:
        for section in statement.get("sections") or []:
            section_rows[section.get("canonical_key") or ""] = [
                node.get("canonical_key") for node in section.get("children") or []
                if node.get("canonical_key")
            ]
    home = {key: section for section, keys in section_rows.items() for key in keys}

    for statement in template.get("statements") or []:
        for node in _nodes(statement.get("sections") or []):
            rollup = node.get("rollup")
            key = node.get("canonical_key")
            if not rollup or not key or key not in mappings:
                continue
            ranges = __import__("re").findall(
                r"\{([^{}]+)\}:\{([^{}]+)\}", mappings[key].get("definition") or "")
            if not ranges:
                continue
            replacements: dict[str, tuple[str, list[str]]] = {}
            for first, last in ranges:
                section = home.get(first)
                if not section or home.get(last) != section:
                    raise RuntimeError(f"Formula range crosses sections: {key}: {first}:{last}")
                keys = section_rows[section]
                start, end = keys.index(first), keys.index(last)
                if start > end:
                    raise RuntimeError(f"Formula range is reversed: {key}: {first}:{last}")
                replacements[first] = (last, keys[start:end + 1])
            expanded: list[str] = []
            consumed_ends: set[str] = set()
            for child in rollup.get("children") or []:
                replacement = replacements.get(child)
                if replacement:
                    end, keys = replacement
                    expanded.extend(keys)
                    consumed_ends.add(end)
                elif child not in consumed_ends:
                    expanded.append(child)
            rollup["children"] = list(dict.fromkeys(expanded))
    return template


def _remove(mapping: dict, aliases: set[str]) -> None:
    blocked = {normalize_label(alias) for alias in aliases}
    mapping["aliases"] = [
        alias for alias in mapping.get("aliases") or []
        if normalize_label(alias) not in blocked
    ]


def _add(mapping: dict, aliases: list[str]) -> None:
    current = list(mapping.get("aliases") or [])
    seen = {normalize_label(alias) for alias in current}
    for alias in aliases:
        if normalize_label(alias) not in seen:
            current.append(alias)
            seen.add(normalize_label(alias))
    mapping["aliases"] = current


def _deduplicate(mapping: dict) -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for alias in mapping.get("aliases") or []:
        normalized = normalize_label(alias)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(alias)
    mapping["aliases"] = unique


def _remove_label_shadows(definition: dict) -> list[tuple[str, str, str]]:
    resolved = load_ontology(copy.deepcopy(definition), resolve=True)
    raw_by_key = {item.get("canonical_key"): item for item in definition.get("mappings") or []}
    labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    statements: dict[str, str] = {}
    for mapping in resolved.mappings:
        statement = getattr(mapping.statement, "value", mapping.statement) or ""
        statements[mapping.canonical_key] = statement
        labels[(statement, normalize_label(mapping.label))].add(mapping.canonical_key)

    removed: list[tuple[str, str, str]] = []
    for key, mapping in raw_by_key.items():
        statement = statements.get(key, "")
        kept: list[str] = []
        for alias in mapping.get("aliases") or []:
            owners = labels.get((statement, normalize_label(alias)), set()) - {key}
            if owners:
                removed.append((key, alias, sorted(owners)[0]))
                continue
            kept.append(alias)
        mapping["aliases"] = kept
    return removed


def repair(definition: dict, template: dict) -> tuple[dict, list[tuple[str, str, str]]]:
    definition = copy.deepcopy(definition)
    mappings = {item["canonical_key"]: item for item in definition.get("mappings") or []}

    for section in definition.get("section_defaults", {}).values():
        section["note_use"] = "decomposition_allowed"
        section["note_use_rationale"] = (
            "A cited note may supply dedicated template concepts before the face aggregate, but "
            "only when same-subsection detail and residual rows reconcile to the face amount."
        )

    for key in _calculated_keys(template):
        mapping = mappings.get(key)
        if mapping is None:
            continue
        mapping["extraction_mode"] = "extract_or_derive"
        mapping["alias_matching"] = "enabled"
        mapping["unit_of_account"] = "subtotal"

    residual_keys = set(RESIDUAL_BY_SECTION.values())
    for mapping in definition.get("mappings") or []:
        key = mapping["canonical_key"]
        if key not in residual_keys:
            continue
        section = next(section for section, residual_key in RESIDUAL_BY_SECTION.items()
                       if residual_key == key)
        mapping["value_scope"] = "exclusive_residual"
        mapping["alias_matching"] = "disabled"
        mapping["residual_policy"] = {
            "framework": "residual_framework",
            "section_scope": section,
            "population": "sweep_only",
            "cross_section": False,
            "notes_as_source": False,
            "plug": False,
            "itemise": True,
        }

    _remove(mappings["is_pl__rents_and_royalty_income"], {"Sales(Revenues)"})
    _remove(mappings["is_pl__profit_for_the_year"], {
        "Dividends on Perpetual Bond",
        "Profit attibutable to the owners/ parents/ group",
    })
    _remove(mappings["is_pl__other_operating_expenses"], {
        "Operating expenses",
        "Administrative expenses",
        "General and administrative expenses",
        "General & administrative expenses",
        "General and administration expenses",
        "General & administration expenses",
    })
    _remove(mappings["is_pl__other_operating_income"], {
        "Interest income", "bank interest", "asset impairment loss",
    })

    _add(mappings["is_pl__general_and_admin_expenses"], [
        "Administrative expense",
        "Administrative expenses",
        "Administration expense",
        "General and administrative expenses",
        "General & administrative expenses",
        "General and administration expenses",
        "General & administration expenses",
    ])
    _add(mappings["is_pl__profit_loss_before_tax"], [
        "Profit before taxation",
        "Loss before taxation",
        "Profit before tax",
        "Loss before tax",
        "Profit/(loss) before tax",
        "Profit/(loss) before taxation",
        "Profit or loss before tax",
    ])
    _add(mappings["is_pl__profit_for_the_year"], [
        "Loss for the year",
        "Profit/(loss) for the year",
        "(Loss)/profit for the year",
    ])
    _add(mappings["is_pl__net_operating_profit"], [
        "Operating profit",
        "Operating loss",
        "Operating income",
        "Profit from operating activities",
        "Loss from operating activities",
    ])
    _add(mappings["is_pl__total_income_tax"], [
        "Tax", "Income tax expense", "Income tax credit", "Tax expense", "Tax credit",
    ])
    _add(mappings["is_pl__other_operating_income"], ["Other gain", "Other gains"])
    _add(mappings["is_pl__interest_income"], ["Bank interest"])
    _add(mappings["cf_oper_indirect__interest_expense"], ["Interest Expense"])
    _add(mappings["is_pl__minority_interests_pl"], [
        "Non-controlling interests",
        "Minority interests",
        "Profit attributable to non-controlling interests",
        "Loss attributable to non-controlling interests",
    ])
    _add(mappings["bs_nca__plant_and_equipment"], [
        "Property, plant and equipment",
        "Property plant and equipment",
        "Property, plant and equipments",
    ])
    _add(mappings["bs_nca__other_fixed_assets"], [
        "Right-of-use assets",
        "Right-of-use asset",
    ])
    _add(mappings["cf_oper_indirect__net_profit_loss"], [
        "Loss before tax",
        "Profit before tax",
        "Profit/(loss) before tax",
    ])
    _add(mappings["cf_oper_indirect__interest_expense"], ["Finance costs"])
    _add(mappings["cf_oper_indirect__income_taxes_paid_indirect"], [
        "Hong Kong profits tax paid, net",
        "Chinese Mainland taxes paid, net",
        "Overseas taxes paid, net",
    ])
    _add(mappings["cf_oper_indirect__interest_paid_oper"], [
        "Interest paid on bank borrowings",
        "Interest paid on guaranteed notes",
    ])
    _add(mappings["cf_oper_indirect__cash_flows_oper_activ_indirect"], [
        "Net cash flows from operating activities",
        "Net cash flows from/(used in) operating activities",
        "Net cash flows used in operating activities",
    ])
    _add(mappings["cf_investing__purchase_ppe"], [
        "Purchase of items of property, plant and equipment",
    ])
    _add(mappings["cf_investing__proceeds_sale_ppe"], [
        "Proceeds from disposal of items of property, plant and equipment",
        "Proceeds from disposal of right-of-use assets",
    ])
    _add(mappings["cf_investing__purchase_investment_property"], [
        "Additions to investment properties",
    ])
    _add(mappings["cf_investing__proceeds_sale_investment_prop"], [
        "Proceeds from disposal of investment properties",
    ])
    _add(mappings["cf_investing__investment_assoc_and_affiliates"], [
        "Investment in joint ventures",
        "Acquisition of an associate",
        "Advances to associates",
        "Advances to joint ventures",
    ])
    _add(mappings["cf_investing__sale_assoc_and_affiliates"], [
        "Proceeds from disposal of a joint venture",
        "Repayment from associates",
        "Repayment from joint ventures",
        "Return of capital from a joint venture",
    ])
    _add(mappings["cf_investing__proceeds_sale_of_subsidiary"], [
        "Proceeds from disposal of a subsidiary",
    ])
    _add(mappings["cf_investing__dividends_received_invest"], [
        "Dividend received from an associate",
        "Dividends received from joint ventures",
        "Dividends received from financial assets at fair value through other comprehensive income",
        "Dividends received from financial assets at fair value through profit or loss",
    ])
    _add(mappings["cf_investing__cash_flows_from_invest_activities"], [
        "Net cash flows from investing activities",
        "Net cash flows used in investing activities",
    ])
    _add(mappings["cf_financing__proceeds_non_cur_borrowings"], [
        "New bank borrowings raised",
    ])
    _add(mappings["cf_financing__repayments_non_cur_borrowings"], [
        "Repayment of bank borrowings",
        "Redemption and repurchase of guaranteed notes",
    ])
    _add(mappings["cf_financing__issue_costs_non_cur_borrowings"], [
        "Bank financing charges",
    ])
    _add(mappings["cf_financing__cash_flows_from_finance_activities"], [
        "Net cash flows from financing activities",
        "Net cash flows used in financing activities",
    ])
    _add(mappings["cf_financing__net_foreign_exchange_difference"], [
        "Effect of foreign exchange rate changes, net",
    ])
    _add(mappings["cf_financing__beg_of_period_cash_and_cash_equiv"], [
        "Cash and cash equivalents at beginning of year",
    ])
    _add(mappings["cf_financing__end_of_period_cash_and_cash_equiv"], [
        "Cash and cash equivalents at end of year",
    ])

    removed = _remove_label_shadows(definition)
    for mapping in definition.get("mappings") or []:
        _deduplicate(mapping)

    binding = definition.setdefault("binding", {})
    binding["unbound_row_policy"] = (
        "Route an unclaimed face value only to the residual for its resolved printed section. "
        "Never borrow a neighbouring concept or cross a section. After all mapping and gap-closing "
        "opportunities, the engine-level face_mapping_contract gives any remaining face value a "
        "unique engine_unclassified_face key outside the ontology/template namespaces. This keeps "
        "the row stored and reviewable without feeding it into an unrelated calculation. Verified "
        "non-additive aggregates replaced by mapped components remain evidence-only."
    )
    framework = definition.get("residual_framework") or {}
    framework["note"] = (
        f"One definition governing the {len(RESIDUAL_BY_SECTION)} output-template subsection "
        "residuals. Each uses an existing template line; no template row is added."
    )
    definition["residual_framework"] = framework
    return definition, removed


def _latest_definition() -> tuple[dict, sqlite3.Row]:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "select id, version, target_template_key, definition "
        "from ontology_versions where ontology_key=? order by version desc limit 1",
        (ONTOLOGY_KEY,),
    ).fetchone()
    connection.close()
    if row is None:
        raise RuntimeError(f"No persisted ontology found for {ONTOLOGY_KEY}")
    definition = json.loads(row["definition"]) if isinstance(row["definition"], str) \
        else row["definition"]
    return definition, row


def _publish(definition: dict, base: sqlite3.Row) -> tuple[str, int]:
    ontology_id = str(uuid.uuid4())
    version = int(base["version"]) + 1
    connection = sqlite3.connect(DATABASE)
    connection.execute(
        "insert into ontology_versions "
        "(id, ontology_key, target_template_key, version, definition, created_at) "
        "values (?,?,?,?,?,?)",
        (ontology_id, ONTOLOGY_KEY, base["target_template_key"], version,
         json.dumps(definition, ensure_ascii=False),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")),
    )
    connection.commit()
    connection.close()
    return ontology_id, version


def _publish_template(template: dict) -> tuple[str, int]:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    base = connection.execute(
        "select version from template_versions where template_key=? order by version desc limit 1",
        (TEMPLATE_KEY,),
    ).fetchone()
    template_id = str(uuid.uuid4())
    version = int(base["version"] if base else 0) + 1
    connection.execute("update template_versions set is_published=0 where template_key=?",
                       (TEMPLATE_KEY,))
    connection.execute(
        "insert into template_versions "
        "(id, template_key, name, version, definition, is_published, created_at) "
        "values (?,?,?,?,?,?,?)",
        (template_id, TEMPLATE_KEY, template.get("name") or "", version,
         json.dumps(template, ensure_ascii=False), 1,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")),
    )
    connection.commit()
    connection.close()
    return template_id, version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-latest-db", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    base_definition, base = _latest_definition()
    source_definition = json.loads(SOURCE.read_text(encoding="utf-8"))
    definition = base_definition if args.from_latest_db else source_definition
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    repaired, removed = repair(definition, template)
    template = repair_template_rollups(repaired, template)
    SOURCE.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    TEMPLATE.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    print(f"source={SOURCE}")
    print(f"calculated_extractable={len(_calculated_keys(template))}")
    print(f"label_shadows_removed={len(removed)}")
    if args.publish:
        template_id, template_version = _publish_template(template)
        ontology_id, version = _publish(repaired, base)
        print(f"published_template_id={template_id}")
        print(f"published_template_version={template_version}")
        print(f"published_id={ontology_id}")
        print(f"published_version={version}")


if __name__ == "__main__":
    main()