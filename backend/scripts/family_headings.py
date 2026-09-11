#!/usr/bin/env python
"""THE CANDIDATE NOTE HEADINGS FOR EACH FOCUS FAMILY, from the corpus, with what already claims them.

WHY A DOMAIN PROBE RATHER THAN TERM OVERLAP. `note_header_coverage.py` ranks unclaimed headings by
IDF cosine against a line's own prose, which cannot see the container-for-content case — a
depreciation line shares no words with "PROPERTY, PLANT AND EQUIPMENT". These probes are the
analyst's vocabulary for each family instead: the words a PRC or HK filing actually heads such a
note with, in both scripts. What comes back is every heading in the corpus that could plausibly
belong to the family, with a flag for whether any part of that family already claims it.

READING IT. A heading marked `--` is reachable by nothing in its own family and is where the
authoring effort belongs. A heading marked with a part key is already claimed, and a heading
claimed by MORE than one part of the same family is a double-claim: two rungs competing for one
note is how a figure gets counted twice.

    python scripts/family_headings.py --family deprec_oper
    python scripts/family_headings.py --all
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")
VOCAB = pathlib.Path(__file__).resolve().parent.parent.parent / "_vocab"

# One probe per family — deliberately WIDE. The job is to see everything that could belong, then
# decide; a narrow probe would hide exactly the heading nobody has thought of.
FAMILIES: dict[str, tuple[str, str]] = {
    "revenue": ("is_pl__sales_revenues",
                r"turnover|revenue|sales|segment\s+information|營業額|营业额|營業收入|营业收入|"
                r"主營業務|主营业务|收入|分行業|分行业|分產品|分产品|分地區|分地区|分銷售|分销售|"
                r"合同負債|合同负债|客戶合約|客户合同"),
    "deprec_oper": ("is_pl__deprec_and_impairment_oper_exp",
                    r"depreciation|amortisation|amortization|impairment|property,?\s+plant|"
                    r"right.of.use|investment\s+propert|operating\s+(?:activities|expenses)|"
                    r"administrative|selling|distribution|research|固定資產|固定资产|折舊|折旧|"
                    r"攤銷|摊销|減值|减值|使用權資產|使用权资产|投資性房地產|投资性房地产|"
                    r"無形資產|无形资产|管理費用|管理费用|銷售費用|销售费用|研發費用|研发费用|"
                    r"在建工程|長期待攤|长期待摊"),
    "deprec_cos": ("is_pl__deprec_and_impairment_cos",
                   r"cost\s+of\s+sales|cost\s+of\s+revenue|cost\s+of\s+goods|營業成本|营业成本|"
                   r"主營業務成本|主营业务成本|銷售成本|销售成本|depreciation|折舊|折旧"),
    "fincl_cp": ("bs_ca__secur_and_other_fincl_assets_cp",
                 r"financial\s+assets?|fair\s+value|fvtpl|fvtoci|fvoci|available.for.sale|"
                 r"held.to.maturity|debt\s+(?:investment|instrument|securit)|"
                 r"investment\s+securit|structured\s+deposit|wealth\s+management|"
                 r"交易性金融資產|交易性金融资产|金融資產|金融资产|理財產品|理财产品|"
                 r"結構性存款|结构性存款|可供出售|持有至到期|公允|公平值"),
    "fincl_ltp": ("bs_nca__secur_and_other_fincl_assets_ltp",
                  r"debt\s+investment|other\s+(?:debt|equity)\s+investment|"
                  r"other\s+non.current\s+financial|long.term\s+(?:equity\s+)?investment|"
                  r"financial\s+assets?|fair\s+value|available.for.sale|held.to.maturity|"
                  r"債權投資|债权投资|其他債權投資|其他债权投资|其他權益工具投資|其他权益工具投资|"
                  r"其他非流動金融資產|其他非流动金融资产|長期股權投資|长期股权投资|可供出售|持有至到期"),
    "due_from_rp": ("bs_nca__due_from_related_parties_ltp",
                    r"related\s+part|amounts?\s+due\s+from|amounts?\s+owed\s+by|"
                    r"long.term\s+receivable|關聯方|关联方|關連人士|关连人士|"
                    r"應收關聯|应收关联|同系附屬|同系附属|長期應收款|长期应收款|"
                    r"應收股東|应收股东|應收董事|应收董事|資金往來|资金往来|委託貸款|委托贷款"),
    "other_recv_cp": ("bs_ca__other_receivables_cp",
                      r"other\s+receivable|deposits?,?\s+prepayment|prepayment|deposit|"
                      r"loans?\s+(?:to|and\s+advances)|其他應收款|其他应收款|預付款|预付款|"
                      r"押金|保證金|保证金|備用金|备用金|應收款項|应收款项|其他流動資產|其他流动资产"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    corpus: dict[str, list[tuple[str, str, int]]] = {}
    for path in sorted(VOCAB.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        seen: dict[tuple[str, str], int] = {}
        for n in json.loads(path.read_text(encoding="utf-8")).get("notes") or []:
            t = (n.get("title") or "").strip()
            if t:
                k = (str(n.get("note") or ""), t)
                seen[k] = seen.get(k, 0) + len(n.get("rows") or [])
        corpus[path.stem] = [(num, t, rows) for (num, t), rows in seen.items()]

    lines: list[str] = []

    def say(t: str = "") -> None:
        lines.append(t)
        print(t)

    names = list(FAMILIES) if args.all else [args.family]
    for name in names:
        if name not in FAMILIES:
            say(f"unknown family {name!r}; one of {list(FAMILIES)}")
            continue
        parent, probe = FAMILIES[name]
        rx = re.compile(probe, re.I)
        kids = [i for i in raw["items"] if i.get("parent") == parent]
        # Which part of this family, if any, already claims a heading.
        claimers: list[tuple[str, list[re.Pattern]]] = []
        for kid in kids:
            pats = []
            for p in ((kid.get("note_source") or {}).get("note_title_any") or []):
                try:
                    pats.append(re.compile(p, re.I))
                except re.error:
                    continue
            claimers.append((kid["key"], pats))

        say()
        say("=" * 100)
        say(f"{name}   ->   {parent}   ({len(kids)} parts)")
        say("=" * 100)
        unclaimed = 0
        for filing, notes in corpus.items():
            hits = [(num, t, r) for num, t, r in notes if rx.search(t)]
            if not hits:
                continue
            say(f"--- {filing[:46]}")
            for num, t, nrows in sorted(hits, key=lambda h: -h[2]):
                owners = [k for k, pats in claimers if pats and any(p.search(t) for p in pats)]
                mark = ("--" if not owners
                        else owners[0][:34] if len(owners) == 1
                        else f"!! {len(owners)} parts claim this")
                if not owners:
                    unclaimed += 1
                say(f"   {num:>9s} {t[:56]:58s} {nrows:4d}r  {mark}")
        say()
        say(f"{unclaimed} plausible heading(s) claimed by NO part of this family")

    if args.out:
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
