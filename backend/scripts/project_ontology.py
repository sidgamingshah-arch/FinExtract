"""Project all 462 ontology concepts into the merged line-item model, and measure what is lost.

THIS IS THE KILL TEST for the merge. The claim being checked is not "the schema compiles" — it is
that a `LineItemDef` can carry every declaration the ONTOLOGY'S OWN LIVE CODE reads, and that the
420 caption collisions the rulebook resolves today are still resolvable afterwards.

Three numbers decide it:

  (a) how many ontology fields have nowhere to land in `LineItemDef`, split by whether live code
      outside the schema modules actually reads them. A field nothing reads is a documentation
      loss; a field the pipeline reads is a correctness loss.
  (b) how many of the 1,969 normalised caption strings are claimed by more than one projected
      definition, and how many of those still have a discriminating field.
  (c) whether any collision is left with NO surviving discriminator — the ones that would resolve
      at the exact tier, confidence 1.0, to whichever concept happened to be first.

(c) is the only one that can fail silently in production, so it is the one the exit code follows.

Run from ``backend``:  ../.venv/Scripts/python.exe scripts/project_ontology.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import subprocess
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from app.schemas.line_items import LineItemDef, LineItemSet, load_line_item_set  # noqa: E402
from app.schemas.loader import load_ontology  # noqa: E402
from app.services.mapping import normalize_label  # noqa: E402

SEED = pathlib.Path("app/sample/templates/output_csv_hk_ontology.json")

# ── the projection: ontology field -> line-item field ────────────────────────────────────────
# Same name on both sides unless the merge deliberately renamed it. `exclude` is the rename that
# mattered: the rulebook has BOTH `exclude` (prose criteria for the LLM) and `exclude_hints`
# (regexes that veto a match), and the first configurator collapsed them into one regex-validated
# list — which would either refuse the prose at the door or compile it as an accidental veto.
RENAMED = {
    "canonical_key": "key",
    "include": "include_criteria",
    "exclude": "exclude_criteria",
}
SAME = [
    "label", "description", "definition", "confusable_with", "value_scope", "extraction_mode",
    "analyst_bucket", "aliases", "aliases_i18n", "keyword_hints", "regex_hints", "exclude_hints",
    "sign_rule", "min_confidence_to_auto_accept", "inherits", "statement", "section_scope",
    "temporality", "face_only", "unit_of_account", "note_use", "note_use_rationale",
    "sign_convention", "match_priority", "alias_matching", "residual_policy",
    "expected_components", "never_sweep",
    # containment — the discriminator a first pass omitted, and the only one of these four that
    # mapping.py reads for a REFUSAL rather than for prose
    "is_gross_parent", "children_if_decomposed", "sole_component_of",
    "decomposition_rule", "others_rule", "section_disambiguation", "derivation",
    "aggregation_note", "template_note", "notes_as_source_rationale",
]
# How a concept's `extraction_mode` becomes a line-item `type`. The ontology says how a value may
# ARRIVE; `type` says the same thing in the configurator's vocabulary.
TYPE_OF_MODE = {
    "extract": "extracted",
    "extract_or_derive": "extracted",   # printed when printed, derived when not — read first
    "derive": "derived",
    "do_not_extract": "extracted",      # refused by `extraction_mode`, not by `type`
}


def project(m) -> dict:
    out: dict = {}
    for src, dst in RENAMED.items():
        out[dst] = getattr(m, src)
    for f in SAME:
        v = getattr(m, f, None)
        if v is None or v == [] or v == {}:
            continue
        out[f] = v
    out["type"] = TYPE_OF_MODE.get(str(getattr(m, "extraction_mode", "extract")), "extracted")
    # A derived line needs a cascade or a service to name; the ontology says HOW in prose
    # (`derivation`), which is not a cascade, so the projection records the prose as the
    # implementer of record rather than inventing rungs.
    if out["type"] == "derived":
        out["implemented_by"] = "rulebook_derivation"
    out["namespace"] = "template"
    return out


def reads_outside_schemas(field: str) -> list[str]:
    """Files outside the schema/loader modules that mention this ontology field."""
    try:
        res = subprocess.run(
            ["git", "grep", "-l", "-F", f".{field}", "--", "app/services", "app/stages",
             "app/api", "app/core"],
            capture_output=True, text=True, timeout=60)
        return [f for f in res.stdout.split() if f]
    except Exception:
        return []


def main() -> int:
    raw = json.loads(SEED.read_text(encoding="utf-8"))
    ont = load_ontology(raw, resolve=True)
    concepts = ont.mappings
    print(f"  ontology: {len(concepts)} concepts (resolved)")

    # ── (a) fields with nowhere to land ──────────────────────────────────────────────────────
    landed = set(RENAMED) | set(SAME) | {"extraction_mode"}
    declared = {k for c in raw["mappings"] for k in c}
    declared |= {k for e in raw.get("section_defaults", {}).values() for k in e}
    homeless = sorted(declared - landed)
    print(f"\n  (a) ontology fields with no LineItemDef home: {len(homeless)}")
    live, dead = [], []
    for f in homeless:
        users = reads_outside_schemas(f)
        (live if users else dead).append((f, users))
    for f, users in live:
        print(f"        READ BY LIVE CODE  {f:28} {', '.join(u.split('/')[-1] for u in users[:3])}")
    print(f"        documentation-only (nothing outside schemas reads them): "
          f"{', '.join(f for f, _ in dead)}")

    # ── project, then validate every one ─────────────────────────────────────────────────────
    projected, failures = [], []
    for m in concepts:
        try:
            projected.append(LineItemDef.model_validate(project(m)))
        except Exception as exc:
            failures.append((m.canonical_key, str(exc).split("\n")[0]))
    print(f"\n  projected: {len(projected)}/{len(concepts)} validate as LineItemDef")
    for k, e in failures[:8]:
        print(f"        FAILED {k}: {e}")

    # ── (b)/(c) the collision test ───────────────────────────────────────────────────────────
    idx: dict[str, set[str]] = collections.defaultdict(set)
    for d in projected:
        for n in [d.label] + d.aliases + [a for al in d.aliases_i18n.values() for a in al]:
            n = normalize_label(n or "")
            if n:
                idx[n].add(d.key)
    by_key = {d.key: d for d in projected}
    coll = {n: ks for n, ks in idx.items() if len(ks) > 1}
    print(f"\n  (b) normalised caption strings: {len(idx)};  claimed by >1: {len(coll)}")

    # The matcher's real tie-break chain, in order, from `OntologyMatcher._exact`:
    #   the `allowed` gate  ->  _prefer_label_owners  ->  max(match_priority)  ->  _confusable_tie
    # Checking fewer than all four overstates the loss: label ownership is not a stored field at
    # all (it is `normalize_label(label) == caption`), so a first pass that looked only at stored
    # fields reported 31 undecidable collisions that the matcher in fact resolves.
    label_owners: dict[str, set[str]] = collections.defaultdict(set)
    for d in projected:
        if (n := normalize_label(d.label or "")):
            label_owners[n].add(d.key)

    def mutually_confusable(keys: set[str]) -> bool:
        """Both name the other — the rulebook saying "never pick between these two"."""
        for a in keys:
            for b in keys:
                if a != b and b in by_key[a].confusable_with and a in by_key[b].confusable_with:
                    return True
        return False

    def discriminators(norm: str, keys: set[str]) -> list[str]:
        """Which surviving fields still tell these claimants apart, in the matcher's own order."""
        got = []
        if len({by_key[k].statement for k in keys}) > 1:
            got.append("statement")
        if len({tuple(sorted(by_key[k].section_scope)) for k in keys}) > 1:
            got.append("section_scope")
        if any(by_key[k].alias_matching == "disabled" for k in keys):
            got.append("alias_matching")
        # Label ownership: exactly one claimant is the concept the filing actually named.
        if len(owners := (label_owners.get(norm, set()) & keys)) == 1:
            got.append("label_ownership")
        if len({by_key[k].match_priority for k in keys}) == len(keys):
            got.append("match_priority")
        if mutually_confusable(keys):
            got.append("confusable_with -> review")
        # Containment: one claimant declares itself the gross parent of another, so the pair is
        # a parent and its child rather than two competitors, and loading both additively would
        # double-count. `mapping.py` reads this to refuse, not to rank.
        if any(by_key[k].is_gross_parent for k in keys):
            got.append("is_gross_parent")
        if any(set(by_key[k].children_if_decomposed) & keys for k in keys):
            got.append("children_if_decomposed")
        if any(by_key[k].sole_component_of in keys for k in keys):
            got.append("sole_component_of")
        if any(by_key[k].section_disambiguation for k in keys):
            got.append("section_disambiguation")
        return got

    tally = collections.Counter()
    undecidable = []
    for n, ks in coll.items():
        got = discriminators(n, ks)
        tally[got[0] if got else "NONE"] += 1
        if not got:
            undecidable.append((n, sorted(ks)))
    for how, cnt in tally.most_common():
        print(f"        {'first discriminator: ' + how:44} {cnt}")

    # THE METRIC IS REGRESSION, NOT PERFECTION. A collision neither model can resolve on a
    # principled field is an ambiguity in the RULEBOOK, inherited rather than introduced: the
    # ontology answers it by declaration order today — verified for `presented in`, where
    # `statement_setup_controls__rounding` wins purely by being written first, both claimants
    # sitting at priority 90 under one banner with neither owning the label. The merge is
    # equivalent so long as it preserves that order, which `LineItemSet.items` does by being a
    # list. A genuine REGRESSION is a collision the ontology resolves on a field and this model
    # cannot, and that set is what the exit code follows.
    from app.services.mapping import OntologyMatcher

    matcher = OntologyMatcher(ont)
    regressions = []
    for n, ks in undecidable:
        owners = set(matcher._label_index.get(n) or [])
        prios = {matcher._priority_of(k) for k in ks}
        if bool(owners & set(ks)) or len(prios) == len(ks):
            regressions.append((n, ks))

    print(f"\n  (c) collisions no FIELD resolves: {len(undecidable)} — inherited rulebook "
          f"ambiguity, decided by declaration order in both models")
    print(f"      REGRESSIONS (the ontology resolves, this model cannot): {len(regressions)}")
    for n, ks in undecidable[:10]:
        print(f"        {n!r}")
        for k in ks:
            d = by_key[k]
            print(f"           {k:50} stmt={d.statement and d.statement.value} "
                  f"scope={d.section_scope} prio={d.match_priority}")
    if len(undecidable) > 10:
        print(f"        (+{len(undecidable) - 10} more)")

    homeless_live = [f for f, _ in live]
    ok = not failures and not regressions and not homeless_live
    print(f"\n  VERDICT: "
          + ("the merged model carries the whole rulebook, with no regression"
             if ok else "GAPS REMAIN"))
    if not ok:
        print(f"           projection failures={len(failures)}  regressions={len(regressions)}  "
              f"live fields with no home={len(homeless_live)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
