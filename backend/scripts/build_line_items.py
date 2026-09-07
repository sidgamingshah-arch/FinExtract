"""Build the line-item seed: the 462 rulebook concepts merged with the configurator's own parts.

THIS IS THE CONTENT HALF OF THE MERGE. The schema change made `LineItemDef` able to carry every
declaration a concept makes; this makes the line-item set actually BE the rulebook rather than 21
definitions describing one corner of it.

    454  rulebook concepts the configurator never described  ->  projected
      8  keys both describe — exactly the output lines       ->  gate from the rulebook,
                                                                 assembly from the configurator
     13  `sub__*` note-level parts                           ->  configurator only
    ---
    475  definitions

The configurator's 21 are not retyped here either: they are read from `generate_line_items.py`'s
output, which derives them from the shipped services' own constants. Two generators feeding one
seed, neither of them hand-maintained.

Run from ``backend``:
    ../.venv/Scripts/python.exe scripts/generate_line_items.py   # the 21, from the services
    ../.venv/Scripts/python.exe scripts/build_line_items.py      # merge with the 462
"""
from __future__ import annotations

import json
import pathlib
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from app.schemas.loader import load_ontology  # noqa: E402
from app.services.mapping import (CONCEPT_FAMILIES, EXCLUSIVE_VOCABULARIES,
                                  HEADING_ROW_SECTIONS, SECTION_WORDS,
                                  _BUILTIN_CAPTION_INVENTORY, _COMPACT_SECTION_TOKENS,
                                  _STATEMENT_OF_PREFIX,
                                  _STATEMENT_SPELLINGS)  # noqa: E402
from app.services.line_items import build  # noqa: E402
from app.services.ontology_projection import build_definitions  # noqa: E402
from app.schemas.line_items import MappingVocabulary, load_line_item_set  # noqa: E402

# The vocabulary block's key for the caption-normalisation character inventories. Named here
# because two places have to agree on it — this writer and `MappingVocabulary` — and today only
# one of them declares it (see `report_caption_characters`).
CAPTION_CHARS_KEY = "caption_characters"

TEMPLATES = pathlib.Path("app/sample/templates")
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"
CONFIGURED = TEMPLATES / "output_csv_hk_line_items_configured.json"
SEED = TEMPLATES / "output_csv_hk_line_items.json"

# The section-layer fields a line item declares. Narrowed from the rulebook's 18 entries rather
# than retyped, so the gate these definitions inherit is the SAME gate the concepts inherit — two
# answers to that question is the failure the merge exists to end.
SECTION_FIELDS = ("statement", "section_scope", "temporality", "unit_of_account", "note_use",
                  "note_use_rationale", "sign_convention", "match_priority", "face_only",
                  "analyst_bucket")


def report_caption_characters(loaded) -> None:
    """Say out loud whether the emitted inventory SURVIVES the schema, and stay quiet if it does.

    WHY A BUILD SCRIPT PRINTS THIS. `MappingVocabulary` has no `caption_characters` field yet and
    pydantic ignores unknown keys, so the block below is written to the seed, is diffable and
    reviewable there, and is dropped on load — which is exactly the failure this whole merge is
    against: configuration that reads like a control and controls nothing. It is not left silent
    and it is not left out. `mapping.build_caption_patterns` already consumes a declaration of
    this shape and falls back to the built-in per entry, so the only missing piece is the field;
    once it exists this function goes quiet on its own and the `SAME` line becomes the assertion
    that the seed and the fold agree.
    """
    declared = CAPTION_CHARS_KEY in MappingVocabulary.model_fields
    if not declared:
        print(f"\n  caption inventory       : {len(_BUILTIN_CAPTION_INVENTORY)} entries written to "
              f"vocabulary.{CAPTION_CHARS_KEY}, DROPPED ON LOAD")
        print(f"     `MappingVocabulary` does not declare `{CAPTION_CHARS_KEY}` yet. Needed shape:")
        print(f"     {CAPTION_CHARS_KEY}: dict[str, list] = Field(default_factory=dict)"
              f"   # empty -> mapping._BUILTIN_CAPTION_INVENTORY, entry by entry")
        print(f"     entries: {', '.join(_BUILTIN_CAPTION_INVENTORY)}")
        return
    got = getattr(loaded.vocabulary, CAPTION_CHARS_KEY, None) or {}
    same = {k: [list(x) if isinstance(x, list) else x for x in v]
            for k, v in (got or {}).items()} == {
        k: [list(x) if isinstance(x, list) else x for x in v]
        for k, v in _BUILTIN_CAPTION_INVENTORY.items()}
    print(f"\n  caption inventory       : {len(got)} entries round-tripped   "
          f"{'SAME as the built-in fold' if same else 'DIFFERS FROM THE BUILT-IN FOLD'}")


def main() -> int:
    raw = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    ont = load_ontology(raw, resolve=True)

    # The configurator's own definitions, from the file `generate_line_items.py` derives from the
    # shipped services. NOT from SEED — that is this script's OUTPUT, and reading it back would
    # treat 462 projected definitions as hand-configured ones and make the build unreproducible.
    if not CONFIGURED.exists():
        print(f"  ABORT: {CONFIGURED.name} is missing — run generate_line_items.py first")
        return 1
    current = load_line_item_set(json.loads(CONFIGURED.read_text(encoding="utf-8")),
                                 resolve=False)
    configured = [d.model_dump(mode="json", exclude_defaults=True) for d in current.items]
    for d, src in zip(configured, current.items):
        d["key"] = src.key                      # exclude_defaults can drop an empty-ish key
    print(f"  rulebook concepts      : {len(ont.mappings)}")
    print(f"  configurator definitions: {len(configured)}")

    items, census = build_definitions(list(ont.mappings), configured)
    print(f"\n  projected               : {census['projected']}")
    print(f"  merged (both describe)  : {census['merged']}")
    print(f"  configurator only       : {census['configurator_only']}")
    print(f"  total                   : {len(items)}")

    item_keys = {d["key"] for d in items}
    dropped = [fid for fid, siblings in CONCEPT_FAMILIES
               if not all(s in item_keys for s in siblings)]
    if dropped:
        print(f"\n  families NOT emitted (siblings this key-space does not define): "
              f"{len(dropped)}/{len(CONCEPT_FAMILIES)}")
        print(f"     {', '.join(dropped)}")

    sections = {
        name: {k: v for k, v in entry.items() if k in SECTION_FIELDS}
        for name, entry in raw["section_defaults"].items()
    }
    sections.setdefault("notes", {})["scopes"] = ["notes"]

    unknown = sorted({d["inherits"] for d in items if d.get("inherits")} - set(sections))
    if unknown:
        print(f"\n  ABORT: definitions inherit sections the rulebook does not declare: {unknown}")
        return 1

    document = {
        "schema_version": 1,
        "line_items_key": "output_csv_hk",
        "target_template_key": raw.get("target_template_key", "output_csv_hk_v1"),
        "locale": raw.get("locale", "en"),
        "supported_locales": raw.get("supported_locales", ["en"]),
        "metadata": {
            "name": "Output CSV (HK) line items",
            "version": "3",
            "supersedes": "2",
            "changes": [
                f"absorbed all {len(ont.mappings)} rulebook concepts: "
                f"{census['projected']} projected, {census['merged']} merged with configured "
                f"assembly, {census['configurator_only']} configurator-only parts",
                "declaration order follows the rulebook, so ties decided by order agree",
            ],
            "breaking_changes": [
                f"the set is now {len(items)} definitions, not {len(configured)}",
            ],
        },
        "section_defaults": sections,
        # THE MAPPING VOCABULARIES, LIFTED OUT OF PYTHON. Every one of these decides which line
        # item a caption resolves to, and every one lived in `services.mapping` as a module
        # constant no configuration could reach — so a reviewer could read all 475 definitions and
        # still not know why a row landed where it did. Read from the constants rather than
        # retyped, for the same reason the section layer is: a retyped vocabulary is a second
        # answer to one question, which is the thing this merge exists to end.
        "vocabulary": {
            # Order is preserved and load-bearing: longest heading first, so "non current
            # liabilities" is never read as "current liabilities".
            "section_banners": [{"token": token, "headings": list(headings),
                                 "heading_row": token in HEADING_ROW_SECTIONS}
                                for token, headings in SECTION_WORDS],
            # Tested before the banners: an umbrella heading must scope nothing rather than
            # resolve to whichever of its sections is declared first.
            "umbrella_banners": [{
                "id": "equity_and_liabilities",
                "groups": [["equity", "权益"], ["liabilit", "负债"]],
                "note": "IFRS statements print EQUITY AND LIABILITIES above the Equity, "
                        "Non-current and Current sub-banners; reading it as equity would refuse "
                        "every liability line item beneath it.",
            }],
            "scope_tokens": dict(_COMPACT_SECTION_TOKENS),
            "statement_prefixes": dict(_STATEMENT_OF_PREFIX),
            "statement_spellings": dict(_STATEMENT_SPELLINGS),
            "exclusive_vocabularies": [
                {"id": "cash_flow_activities", "members": list(vocab),
                 "note": "IAS 7 divides cash flows into exactly three activities and a statement "
                         "labels each subtotal with its own, so naming one rules out the others."}
                for vocab in EXCLUSIVE_VOCABULARIES],
            # ONLY THE FAMILIES THIS KEY-SPACE ACTUALLY HAS. `mapping.CONCEPT_FAMILIES` was
            # written for the HKFRS rulebook: its siblings are keys like
            # `bs_current_liabilities__current_lease_liabilities`, and the output_csv_hk set
            # defines NONE of the 22 keys across all 9 families. Emitting them anyway produced 9
            # families that resolved to nothing while looking, in the file and on the screen,
            # exactly like working configuration — the "config field nothing reads" failure this
            # merge is supposed to end. So the builder filters and reports what it dropped, and
            # for this rulebook the honest answer is that it has no families at all (which also
            # says something worth knowing: family re-routing has never applied to this path).
            "families": [{"id": fid, "siblings": list(siblings)}
                         for fid, siblings in CONCEPT_FAMILIES
                         if all(s in item_keys for s in siblings)],
            # Empty on purpose: the projection writes each correction straight into the line
            # item's own `section_scope`, so the definition declares the section it is gated to
            # instead of a table overriding it from the side.
            "section_overrides": {},
            # THE CAPTION-NORMALISATION CHARACTER INVENTORIES, which decide what a printed caption
            # FOLDS TO before any of the vocabularies above are consulted — so they decide the
            # answers of all of them. Read from `mapping._BUILTIN_CAPTION_INVENTORY` rather than
            # retyped, for the reason the rest of this block is: a retyped inventory is a second
            # answer to one question. Measured, this is the largest single lever in the block —
            # disabling one of its eight consumers (`_ABBREV_GLOSS`) moves 1,050 of 1,993 caption
            # resolutions and puts 74 on a DIFFERENT concept.
            CAPTION_CHARS_KEY: {k: list(v) for k, v in _BUILTIN_CAPTION_INVENTORY.items()},
        },
        # One definition governing every exclusive_residual line item. A per-item `residual_policy`
        # overrides a term only where its author wrote that term down, which is why the projection
        # dumps a policy with `exclude_unset`.
        "residual_framework": (raw.get("residual_framework") or None),
        "items": items,
    }

    SEED.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")

    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")))
    reg = build(st.items)
    print(f"\n  validates               : {reg.ok}   problems: {len(reg.problems)}")
    for p in reg.problems[:10]:
        print(f"     [{p.severity}] {p.key}: {p.message}")
    report_caption_characters(st)
    gated = sum(1 for d in st.items if d.statement is not None)
    print(f"  gate resolved onto      : {gated}/{len(st.items)}")
    print(f"  in output template      : {sum(1 for d in st.items if d.in_output)}")
    print(f"  off-template parts      : {sum(1 for d in st.items if d.namespace == 'internal')}")
    print(f"\n  written to {SEED}  ({SEED.stat().st_size // 1024} KB)")
    return 0 if reg.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
