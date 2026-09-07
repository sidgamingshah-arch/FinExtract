"""Generate the line-item configuration for the eight output lines and their sub-line items.

GENERATED FROM THE SHIPPED CODE, not retyped. The note-title patterns come out of
`deprec_impairment._NOTE_HEADINGS`, the qualifying row captions out of `_QUALIFYING_RE` and the
exclusions out of `_MOVEMENT_EXCLUDE_RE`. Retyping 25 caption alternatives by hand is how a
"faithful port" quietly stops being one, and the whole point of porting first is that the four
services' own test suites can then prove the config equivalent.
"""
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import warnings

warnings.filterwarnings("ignore")

from app.services.deprec_impairment import (_ASSET_NOTE_KEYS, _MOVEMENT_EXCLUDE_RE,
                                            _NOTE_HEADINGS, _OPEX_DIRECT_KEYS, _QUALIFYING_RE,
                                            COS_KEY, OPER_EXP_KEY)

def alternatives(rx) -> list[str]:
    """Split a shipped regex into its TOP-LEVEL alternatives.

    A plain ``.split("|")`` is wrong and was: ``^\\s*at\\s+(?:1|31)`` came apart into
    ``^\\s*at\\s+(?:1`` and ``31)``, neither of which compiles, so the exclusion silently stopped
    excluding. Track group depth and character classes and only cut on a bar that is outside both.
    """
    out, buf, depth, in_class, i = [], [], 0, False, 0
    pat = rx.pattern
    while i < len(pat):
        ch = pat[i]
        if ch == "\\" and i + 1 < len(pat):        # an escape carries its next char verbatim
            buf.append(pat[i:i + 2]); i += 2; continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            out.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    out.append("".join(buf))
    alts = [a.strip() for a in out if a.strip()]
    for a in alts:                                 # never emit what cannot be read back
        re.compile(a)
    return alts


QUALS = alternatives(_QUALIFYING_RE)
EXCL = alternatives(_MOVEMENT_EXCLUDE_RE)
TITLE = {k: p.pattern.replace("\n", " ").strip() for k, p in _NOTE_HEADINGS.items()}

LABEL = {
    "rd_depreciation": "R&D note — depreciation",
    "selling_marketing_depreciation": "Selling & marketing note — depreciation",
    "ga_depreciation": "G&A note — depreciation",
    "operating_expense_depreciation": "Other operating expenses note — depreciation",
    "cos_depreciation": "Cost of sales note — depreciation",
    "ppe_depreciation": "PP&E note — depreciation",
    "prepaid_lease_depreciation": "Prepaid lease payments note — amortisation",
    "fixed_asset_depreciation": "Fixed assets note — depreciation",
    "investment_property_depreciation": "Investment property note — depreciation",
    "cip_depreciation": "Construction in progress note — depreciation",
    "cfo_depreciation": "Cash flow from operations — depreciation add-back",
    "pbt_oper_exp_depreciation": "PBT note — operating-expense depreciation callout",
    "pbt_depreciation": "PBT note — total depreciation",
}
WHY = {
    "cfo_depreciation": "The cash-flow add-back is the last resort: it is the whole group's "
                        "depreciation, so it needs the cost-of-sales share deducted.",
    "pbt_oper_exp_depreciation": "The one dataset that already means operating-expense "
                                 "depreciation, which is why it needs no deduction.",
}


def sub(key, parent, order, *, title_key=None, label=None):
    tk = title_key or key
    return {
        "key": f"sub__{key}", "label": label or LABEL.get(key, key), "type": "extracted",
        "in_output": False, "parent": parent, "order": order,
        # A note-level part is not a template line, so the template key-gate must not demand it.
        "namespace": "internal",
        # The gate arrives from the section layer, authored once — not restated 13 times.
        "inherits": "notes",
        "description": WHY.get(key, f"Depreciation rows inside the note that "
                                    f"{LABEL.get(key, key).split('—')[0].strip()} names."),
        "note_source": {"note_title_any": [TITLE[tk]],
                        "row_caption_any": QUALS, "row_caption_none": EXCL},
    }


items = []
order = 0

# ── the thirteen sub-line items behind the two depreciation lines ────────────────────────────
for k in _OPEX_DIRECT_KEYS:
    order += 1
    items.append(sub(k, OPER_EXP_KEY, order))
for k in _ASSET_NOTE_KEYS:
    order += 1
    items.append(sub(k, OPER_EXP_KEY, order))
for k in ("cfo_depreciation", "pbt_oper_exp_depreciation", "pbt_depreciation"):
    order += 1
    items.append(sub(k, OPER_EXP_KEY, order, title_key="pbt" if k.startswith("pbt") else k))
order += 1
items.append(sub("cos_depreciation", COS_KEY, order))

ref = lambda k, sign=1, role="required": {"ref": f"sub__{k}", "sign": sign, "role": role}

# ── Deprec & Impairment (Oper Exp) — the five-rung cascade, as the spec states it ────────────
items.append({
    "key": OPER_EXP_KEY, "label": "Deprec & Impairment (Oper Exp)", "type": "derived",
    "in_output": True, "order": 1, "implemented_by": "deprec_impairment",
    "inherits": "is_pl", "rollup": "alternatives",
    "description": "Depreciation charged to operating expenses. Never printed as one caption — "
                   "assembled from the notes and resolved by trying the rungs in order.",
    "cascade": [
        {"id": "P1", "note": "The four operating-expense notes, summed. Any subset a filing "
                             "discloses is still a sum, so each term is optional.",
         "terms": [ref(k, 1, "any_of") for k in _OPEX_DIRECT_KEYS]},
        {"id": "P2", "note": "The PBT note's own operating-expense callout. Already means this "
                             "line, so nothing is deducted.",
         "terms": [ref("pbt_oper_exp_depreciation")]},
        {"id": "P3", "note": "Total depreciation less the cost-of-sales share. A filing that "
                             "discloses no cost-of-sales depreciation is taken to charge none, "
                             "so the deduction is optional and the base is not.",
         "terms": [ref("pbt_depreciation"), ref("cos_depreciation", -1, "adjustment")]},
        {"id": "P4", "note": "The asset notes, less the cost-of-sales share.",
         "terms": [ref(k, 1, "any_of") for k in _ASSET_NOTE_KEYS]
                  + [ref("cos_depreciation", -1, "adjustment")]},
        {"id": "P5", "note": "The cash-flow add-back, less the cost-of-sales share.",
         "terms": [ref("cfo_depreciation"), ref("cos_depreciation", -1, "adjustment")]},
    ],
})

items.append({
    "key": COS_KEY, "label": "Deprec & Impairment (COS)", "type": "derived",
    "in_output": True, "order": 2, "implemented_by": "deprec_impairment",
    "inherits": "is_pl", "rollup": "alternatives",
    "description": "Depreciation charged to cost of sales. Read from the cost-of-sales note.",
    "cascade": [
        {"id": "COS_P1", "note": "The cost-of-sales note's depreciation rows.",
         "terms": [ref("cos_depreciation")]},
        # The service has this rung too (deprec_impairment.py:355, :504) and the first port
        # dropped it, which would have blanked this line for any filing that discloses total
        # depreciation and the operating-expense share but no cost-of-sales note.
        {"id": "COS_P2", "note": "Total depreciation less the operating-expense share. Used when "
                                 "the cost-of-sales note itself discloses nothing.",
         "terms": [ref("pbt_depreciation"),
                   {"ref": OPER_EXP_KEY, "sign": -1, "role": "required"}]},
    ],
})

# ── the other six, declared with the derivation that owns them ───────────────────────────────
# Their sub-line items are the NEXT tranche: Find_1/2/3 for the receivables pair, the
# level pools and the negative carryforward for the securities pair, P1/P2 for revenue, and the
# exposure types for contingent liabilities. Declared here so the eight are addressable and the
# screen is complete, with `implemented_by` naming what computes each one until its parts are
# configured too.
OTHERS = [
    ("is_pl__sales_revenues", "Sales (Revenues)", "sales_revenues",
     "The top line. Priority 1 is the face caption the spec accepts; Priority 2 is the revenue "
     "note's own total."),
    ("bs_ca__secur_and_other_fincl_assets_cp", "Secur & Other Fincl Assets (CP)",
     "secur_fincl_assets",
     "Current-portion securities and other financial assets, net of the pools carved out of it."),
    ("bs_nca__secur_and_other_fincl_assets_ltp", "Secur & Other Fincl Assets (LTP)",
     "secur_fincl_assets",
     "The long-term portion, plus any negative current-portion carryforward."),
    ("bs_nca__due_from_related_parties_ltp", "Due from Related Parties (LTP)",
     "related_party_receivables",
     "The largest valid of three searches — the receivable notes, the related-party grouping, "
     "and the related-party transactions note."),
    ("bs_ca__other_receivables_cp", "Other Receivables (CP)", "related_party_receivables",
     "Gross other receivables less the related-party amount carved out of them."),
    ("notes__contingent_liabilities", "Contingent liabilities", "contingent_liabilities",
     "Guarantees, letters of credit, performance bonds and bank guarantees, summed by type."),
]
for i, (key, label, svc, desc) in enumerate(OTHERS, start=3):
    # The section is already in the key — the rulebook's own convention — so deriving `inherits`
    # from it cannot drift from the key it gates.
    items.append({"key": key, "label": label, "type": "derived", "in_output": True,
                  "order": i, "implemented_by": svc, "description": desc,
                  "inherits": key.split("__", 1)[0], "rollup": "none"})

# ── the section layer, PROJECTED FROM THE RULEBOOK'S OWN ENTRIES ─────────────────────────────
# Not retyped. The gate these definitions inherit has to be the SAME gate the 462 ontology
# concepts inherit, or the merge has produced two answers to one question — which is the whole
# failure the merge exists to end. So the 18 entries are read out of the shipped rulebook and
# narrowed to the fields a line item declares.
ONT = json.loads(pathlib.Path("app/sample/templates/output_csv_hk_ontology.json")
                 .read_text(encoding="utf-8"))
_KEEP = ("statement", "section_scope", "temporality", "unit_of_account", "note_use",
         "note_use_rationale", "sign_convention", "match_priority", "face_only",
         "analyst_bucket")
SECTION_DEFAULTS = {
    name: {k: v for k, v in entry.items() if k in _KEEP}
    for name, entry in ONT["section_defaults"].items()
}
# A note-level part is searched in the notes and nowhere else; the rulebook's `notes` section
# says the statement, and this says the search order under it.
SECTION_DEFAULTS["notes"]["scopes"] = ["notes"]

used = sorted({d["inherits"] for d in items if d.get("inherits")})
unknown = [u for u in used if u not in SECTION_DEFAULTS]
if unknown:
    raise SystemExit(f"definitions inherit sections the rulebook does not declare: {unknown}")

document = {
    "schema_version": 1,
    "line_items_key": "output_csv_hk",
    "target_template_key": ONT.get("target_template_key", "output_csv_hk_v1"),
    "locale": ONT.get("locale", "en"),
    "supported_locales": ONT.get("supported_locales", ["en"]),
    "metadata": {
        "name": "Output CSV (HK) line items",
        "version": "2",
        "changes": [
            "merged the ontology's scoping layer in: statement + section_scope via `inherits`",
            "children of the two depreciation lines marked `alternatives`, never summed",
            "COS_P2 restored — the first port dropped a rung the service has",
            "`exclude` split into `exclude_hints` (regex) and `exclude_criteria` (prose)",
        ],
        "breaking_changes": [
            "the seed is an object, not an array",
            "`exclude` renamed to `exclude_hints`",
        ],
    },
    # Authored once per section, claimed by `inherits`. 462 ontology concepts do exactly this;
    # restating the gate per item would be the flat model that cannot be kept consistent.
    "section_defaults": SECTION_DEFAULTS,
    "items": items,
}

# The CONFIGURED definitions only. `build_line_items.py` merges these with the 462
# rulebook concepts to produce the seed the API serves. Two files because the merged
# seed must never be an input to its own build: reading it back would treat 462
# projected definitions as hand-configured ones, and the build would stop being
# reproducible from its sources.
out = pathlib.Path("app/sample/templates/output_csv_hk_line_items_configured.json")
out.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")

# ── validate through the registry, AS RESOLVED ───────────────────────────────────────────────
from app.schemas.line_items import load_line_item_set
from app.services.line_items import build

st = load_line_item_set(document)
defs = st.items
reg = build(defs)
print(f"  section_defaults: {len(SECTION_DEFAULTS)} entries, {len(used)} inherited by definitions")
gated = sum(1 for d in defs if d.statement is not None)
print(f"  gate resolved onto {gated}/{len(defs)} definitions")
print(f"  {len(defs)} definitions: "
      f"{sum(1 for d in defs if d.in_output)} output lines, "
      f"{sum(1 for d in defs if d.parent)} sub-line items")
print(f"  validates: {reg.ok}   problems: {len(reg.problems)}")
for p in reg.problems:
    print(f"     [{p.severity}] {p.key}: {p.message}")
print(f"\n  sub-line items under {OPER_EXP_KEY}:")
for d in reg.children_of(OPER_EXP_KEY):
    print(f"     {d.order:>2}. {d.key:44} {d.label}")
print(f"  sub-line items under {COS_KEY}:")
for d in reg.children_of(COS_KEY):
    print(f"     {d.order:>2}. {d.key:44} {d.label}")
print(f"\n  written to {out}")
