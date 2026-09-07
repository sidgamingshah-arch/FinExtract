"""Do the two matchers give the same answer? Every caption in the rulebook, both engines.

THIS IS THE ONLY THING THAT MAKES THE MERGE REAL. A ported matcher that looks right and is not
equivalent is strictly worse than no port: the figures keep arriving, they just arrive on
different lines, and every subtotal still ties. So the port is held to the incumbent's own answers
rather than to a reading of the spec.

THE CORPUS is the rulebook's own vocabulary, which is the population that actually decides
figures: every label and every alias of every locale, normalised — 1,969 distinct strings, 420 of
them claimed by more than one concept. Each is asked under the section it belongs to AND under
every other section in its statement, because the collisions are what the gate exists to resolve
and asking each caption only where it fits would never exercise it.

A DISAGREEMENT IS A FAILURE, with one exception stated in advance: `OntologyMatcher.match` is a
COMBINATION of tiers and, when an LLM is configured, the model refines the deterministic evidence.
The comparison therefore runs the ontology matcher with the LLM off, which is also the
configuration in which the deterministic path decides a figure on its own.

Run from ``backend``:  ../.venv/Scripts/python.exe scripts/parity_line_items.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from app.config import get_settings  # noqa: E402
from app.schemas.line_items import load_line_item_set  # noqa: E402
from app.schemas.loader import load_ontology  # noqa: E402
from app.services.line_item_matching import LineItemMatcher  # noqa: E402
from app.services.mapping import (SECTION_WORDS, OntologyMatcher, normalize_label,
                                  section_token_of_scope)  # noqa: E402

TEMPLATES = pathlib.Path("app/sample/templates")


def main() -> int:
    raw_ont = json.loads((TEMPLATES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
    ont = load_ontology(raw_ont, resolve=True)
    st = load_line_item_set(
        json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8")))

    settings = get_settings()
    # No `llm_provider`, so `llm_enabled` is False and `match` returns on the exact tier — the
    # deterministic path, which is the one this port has to be equivalent to.
    incumbent = OntologyMatcher(ont, settings=settings)
    assert not incumbent.llm_enabled, "the comparison must run the deterministic path"
    ported = LineItemMatcher(st)
    print(f"  ontology concepts     : {len(ont.mappings)}")
    print(f"  line-item definitions : {len(st.items)}")

    # ── the sections, as a PAGE actually presents them ───────────────────────────────────────
    # THE PROBE MUST ENTER FROM THE BANNER-TEXT SIDE. A first version passed scope ids
    # ("bs_ca", "is_pl", "notes") as the section, and `section_of_banner` — which both engines
    # call — maps printed banner TEXT to a token, so every one of those resolved to None. The
    # section gate never fired, both engines agreed because neither narrowed, and a 100% result
    # said nothing whatever about the section logic. The chain is
    #
    #     scope id --section_token_of_scope--> token <--section_of_banner-- printed banner text
    #
    # so the corpus needs a real banner for each token a concept is scoped to.
    banner_for_token: dict[str, str] = {}
    for token, spellings in SECTION_WORDS:
        if spellings and token not in banner_for_token:
            banner_for_token[token] = spellings[0]

    banners_by_statement: dict[str, set[str]] = collections.defaultdict(set)
    tokens_seen: set[str] = set()
    for m in ont.mappings:
        if not m.statement:
            continue
        for scope in m.section_scope or []:
            token = section_token_of_scope(scope)
            if not token:
                continue                      # a *_top_level scope names no section
            tokens_seen.add(token)
            if (banner := banner_for_token.get(token)):
                banners_by_statement[m.statement.value].add(banner)
    unreachable = sorted(tokens_seen - set(banner_for_token))
    print(f"  section tokens in use : {len(tokens_seen)}")
    if unreachable:
        # Said out loud: a token with no banner spelling cannot be probed, so the gate is not
        # exercised for it. Silence here is what made the first run's 100% misleading.
        print(f"  NOT PROBED (no banner spelling): {', '.join(unreachable)}")

    captions: dict[str, set[str]] = collections.defaultdict(set)
    for m in ont.mappings:
        names = [m.label, *m.aliases]
        for locale_aliases in (m.aliases_i18n or {}).values():
            names.extend(locale_aliases)
        statement = m.statement.value if m.statement else None
        for name in names:
            if (norm := normalize_label(name or "")) and statement:
                captions[norm].add(statement)

    probes: list[tuple[str, str | None, str | None]] = []
    for norm, own_statements in captions.items():
        probes.append((norm, None, None))                     # nothing to narrow by
        for stmt in sorted(own_statements):
            probes.append((norm, stmt, None))                 # statement known, no banner
            for banner in sorted(banners_by_statement[stmt]):
                probes.append((norm, stmt, banner))           # and under every real banner
    print(f"  distinct captions     : {len(captions)}")
    print(f"  probes (caption x banner): {len(probes)}\n")

    agree = 0
    disagreements: list[tuple] = []
    both_unmatched = 0
    for caption, statement, section in probes:
        a = incumbent.match(caption, statement=statement, section=section)
        b = ported.match(caption, statement=statement, section=section)
        if a.canonical_key == b.key:
            agree += 1
            if a.canonical_key is None:
                both_unmatched += 1
        else:
            disagreements.append((caption, statement, section, a.canonical_key, b.key,
                                  a.method, b.method, a.needs_review, b.needs_review))

    print(f"  AGREE     {agree}/{len(probes)}  ({100.0 * agree / max(1, len(probes)):.3f}%)")
    print(f"    of which both unmatched: {both_unmatched}")
    print(f"  DISAGREE  {len(disagreements)}")

    if disagreements:
        by_shape = collections.Counter(
            ("ontology found, port did not" if d[3] and not d[4] else
             "port found, ontology did not" if d[4] and not d[3] else
             "different keys") for d in disagreements)
        print()
        for shape, n in by_shape.most_common():
            print(f"    {shape:32} {n}")
        print("\n  first 15:")
        for caption, stmt, section, a_key, b_key, a_m, b_m, a_r, b_r in disagreements[:15]:
            where = f"{stmt or '-'}/{section or '-'}"
            print(f"    {caption[:38]:38} {where:34}")
            print(f"        ontology -> {str(a_key):48} ({a_m}, review={a_r})")
            print(f"        ported   -> {str(b_key):48} ({b_m}, review={b_r})")

    ok = not disagreements
    print(f"\n  VERDICT: {'the ported matcher is equivalent on the whole rulebook vocabulary' if ok else 'NOT EQUIVALENT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
