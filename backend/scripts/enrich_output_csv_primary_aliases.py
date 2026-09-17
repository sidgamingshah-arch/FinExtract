"""Enrich primary Output CSV mappings with vetted bilingual aliases.

Copies Chinese aliases only where the same English label/alias exists in the shipped
HKFRS China ontology. Covenant and supplemental mappings are intentionally excluded.
The operation is idempotent and removes normalized duplicates within each locale.

AND IT FILTERS WHAT IT COPIES THROUGH `spec_alias_curation`, which it did not do for most of its
life. Transfer is keyed on the ENGLISH wording, so a concept carrying a BORROWED English alias was
handed every Chinese spelling the reference files under that wording — and the curation that later
denied the English caption could not reach the Chinese already copied across. Measured: all seven
`is_retained` movement lines carried "Retained Profits", so all seven were handed
`bs_equity__retained_earnings`' 留存收益 / 保留溢利 / 累计亏损 / 未分配利润, and kept them after the
English denial landed (see `_SECTION_NAME_IS_NOT_A_MOVEMENT`). Curating the transfer is what makes
the denial hold on a re-run instead of needing a second clean-up after every one.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.services.mapping import normalize_label
from app.services.spec_alias_curation import curate_aliases

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "sample" / "templates" / "output_csv_hk_ontology.json"
HKFRS = ROOT / "app" / "sample" / "templates" / "hkfrs_hk_china_ontology.json"
PRIMARY_PREFIXES = ("bs_", "is_", "pl_", "cf_", "eq_")
MAINLAND_CORE_ALIASES = {
    "bs_ca__cash_equivalents": ["现金等价物"],
    "bs_ca__cash_in_hand_and_at_banks": ["货币资金"],
    "is_pl__sales_revenues": ["营业收入", "营业总收入", "主营业务收入"],
    "is_pl__gross_profit": ["营业毛利"],
    "is_pl__profit_loss_before_tax": ["利润总额"],
    "is_pl__total_income_tax": ["所得税费用"],
    "is_pl__profit_for_the_year": ["净利润", "本年净利润"],
    "cf_oper_indirect__cash_flows_oper_activ_indirect": ["经营活动产生的现金流量净额"],
}


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        key = normalize_label(value)
        if value and key and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _chinese_by_english(ontology: dict) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for mapping in ontology.get("mappings", []):
        localized = mapping.get("aliases_i18n") or {}
        chinese = localized.get("zh") or []
        if not chinese:
            continue
        english = [mapping.get("label", ""), *(mapping.get("aliases") or []),
                   *(localized.get("en") or [])]
        for alias in english:
            key = normalize_label(alias)
            if key:
                index[key].update(chinese)
    return index


def enrich(definition: dict, reference: dict) -> tuple[int, int]:
    chinese_index = _chinese_by_english(reference)
    enriched_mappings = 0
    added_chinese = 0
    for mapping in definition.get("mappings", []):
        key = mapping.get("canonical_key", "")
        if not key.startswith(PRIMARY_PREFIXES):
            continue
        localized = mapping.setdefault("aliases_i18n", {})
        english = _unique([mapping.get("label", ""), *(mapping.get("aliases") or []),
                           *(localized.get("en") or [])])
        mapping["aliases"] = english
        localized["en"] = english
        transferred = set()
        for alias in english:
            transferred.update(chinese_index.get(normalize_label(alias), set()))
        transferred.update(MAINLAND_CORE_ALIASES.get(key, []))
        # THE DENIALS APPLY TO WHAT IS COPIED IN, not only to what the generator harvested. Both
        # the incoming transfer and whatever the concept already carries go through, so the pass
        # is also a clean-up: a spelling a denial reached after an earlier run copied it is
        # removed the next time this runs rather than surviving until somebody notices.
        chinese = curate_aliases(key, _unique([*(localized.get("zh") or []),
                                               *sorted(transferred)]))
        if chinese != localized.get("zh", []):
            added_chinese += len(set(map(normalize_label, chinese))
                                - set(map(normalize_label, localized.get("zh") or [])))
            localized["zh"] = chinese
            enriched_mappings += 1
    return enriched_mappings, added_chinese


def main() -> int:
    definition = json.loads(OUTPUT.read_text(encoding="utf-8"))
    reference = json.loads(HKFRS.read_text(encoding="utf-8"))
    mappings, aliases = enrich(definition, reference)
    OUTPUT.write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"enriched_mappings={mappings} added_chinese_aliases={aliases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
