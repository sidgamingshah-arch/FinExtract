"""A DERIVED PARENT IS REACHED BY NO MATCHER — and its aliases are still load-bearing.

NEW FILE -> backend/tests/test_derived_parent_unreachable.py

THE OPEN QUESTION THIS CLOSES, and the second one it opened.

`map_ontology` asserted that "a derived parent is reached by nothing — not the model, not an alias,
not a regex, not a semantic probe… four separate indexes in the backend now agree on it", and the
config plan flagged that claim as unverified: reading the locks in `line_item_matching` suggested
only 3 of the 9 derived lines were excluded, which would make the 258 recognition entries sitting on
them live rather than inert.

THE MATCHING HALF OF THE CLAIM IS TRUE. `_unmatchable` has THREE clauses, not two:

    alias_matching == "disabled"  or  extraction_mode == "derive"  or  type == "derived"

the third covers a derived parent regardless of the other two, and all 9 of 9 are in the set. Probed
at the level of the ANSWER — every caption either matcher knows, 2,068 of them, each line's label and
every alias in every locale — nothing binds a derived parent. `note_sets` builds them no note probe
(`asked_about` is False), nothing puts them in a request, and `line_item_payload` is never called for
one.

AND "INERT" DID NOT FOLLOW FROM IT, which is what this file exists to record. The 258 entries were
deleted on that reasoning and it moved published figures on a real filing:

    2024 Annual Report, deterministic run, before -> after the deletion
      3 face rows stopped being unclassified and were SWEPT into residual buckets
        "Financial assets at fair value through profit or loss"  (current assets)
                                          -> bs_cl__other_current_liabilities
        "Debt investment at fair value through other comprehensive income"
                                          -> bs_cl__other_current_liabilities
        "Financial assets at fair value through profit or loss"  (non-current)
                                          -> bs_nca__other_non_current_assets
     14 calculated figures moved with them, including total assets
        (168,156,062 -> 180,447,374) and total equity and liabilities

Two of the three put an ASSET caption into a LIABILITY bucket. The cause is a use of `aliases` that
has nothing to do with matching: `stages.residual._concept_captions` reads a concept's aliases as a
VETO index — "prohibition 4 and `never_sweep` are both vetoes… a row printed 其他应收款 names its
dedicated concept exactly as surely as one printed 'Other receivables', and a guard that cannot see
the caption cannot protect the row". Those aliases were what kept those captions OUT of the Others
buckets. The concept it reads is an `OntologyMapping`, which is why the path looked unrelated — but
`build_working_view` PROJECTS the line-item configuration into that view, so deleting an alias here
deletes the veto there.

So the aliases are restored, and the lesson is in the shape of the check rather than in the comment:
a no-answer-changed probe over the matcher was the wrong instrument, because the alias index is read
by something that is not the matcher. The figures comparison is what caught it.
"""
from __future__ import annotations

import json
import pathlib

from app.schemas.line_items import load_line_item_set
from app.services import line_item_notes, line_item_requests
from app.services.line_item_matching import LineItemMatcher

SEED = (pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def _set():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")))


def _derived(st) -> list:
    return [d for d in st.items if str(getattr(d.type, "value", d.type)) == "derived"]


def test_every_derived_line_is_locked_out_of_the_matcher() -> None:
    st = _set()
    derived = _derived(st)
    unmatchable = LineItemMatcher(st)._unmatchable

    assert len(derived) == 9, len(derived)
    assert all(d.key in unmatchable for d in derived), (
        [d.key for d in derived if d.key not in unmatchable])


def test_no_caption_in_the_whole_set_resolves_to_a_derived_line() -> None:
    """THE ANSWER-LEVEL ASSERTION. Every label and every alias of every line, in every locale — the
    surface a filing's captions are drawn from — and not one of them may bind a derived parent.

    Necessary and, as the docstring records, NOT sufficient for calling the aliases inert.
    """
    st = _set()
    matcher = LineItemMatcher(st)
    derived = {d.key for d in _derived(st)}

    captions: set[str] = set()
    for d in st.items:
        captions.add(d.label or "")
        captions.update(d.aliases)
        for values in (d.aliases_i18n or {}).values():
            captions.update(values)
    captions.discard("")

    assert len(captions) > 1_500, f"only {len(captions)} captions probed — the surface shrank"
    bound = [(c, matcher.match(c).key) for c in sorted(captions)
             if matcher.match(c).key in derived]
    assert not bound, bound[:5]


def test_no_derived_line_is_asked_about_or_given_a_note_probe() -> None:
    """The other routes. `note_sets` consults `asked_about` before scoring, so a line it rejects
    gets no note set — and a line with no note set is in no request."""
    st = _set()
    derived = _derived(st)

    assert not [d.key for d in derived if line_item_requests.asked_about(d)]
    selected = line_item_notes.note_sets(st.items, [])
    assert not [d.key for d in derived if d.key in selected]


def test_a_derived_parents_aliases_are_kept_because_the_residual_sweep_reads_them() -> None:
    """THE REGRESSION THIS FILE WAS WRITTEN FOR, from the other direction.

    Deleting these 258 entries swept two ASSET captions into "other current liabilities" and moved
    total assets by 12.3m on a real filing. `stages.residual._concept_captions` reads a concept's
    aliases as a veto index, and `build_working_view` projects this configuration into the concepts
    it reads — so an alias here is a sweep guard there, whatever the matcher does with it.

    Pinned on the two lines the filing actually exercised, and on the total, so the next author who
    reads "reached by nothing" and reaches for the delete key fails here instead of on a balance
    sheet.
    """
    st = _set()
    by_key = {d.key: d for d in st.items}
    derived = {d.key for d in _derived(st)}

    for key in ("bs_ca__secur_and_other_fincl_assets_cp",
                "bs_nca__secur_and_other_fincl_assets_ltp"):
        item = by_key[key]
        assert key in derived, key
        every = list(item.aliases) + [a for v in (item.aliases_i18n or {}).values() for a in v]
        assert any("fair value through profit" in a.lower() for a in every), (
            f"{key} lost the alias that keeps that caption out of the Others buckets")

    carried = sum(len(by_key[k].aliases)
                  + sum(len(v) for v in (by_key[k].aliases_i18n or {}).values())
                  + len(by_key[k].regex_hints) + len(by_key[k].keyword_hints)
                  + len(by_key[k].exclude_hints)
                  for k in derived)
    assert carried == 258, carried
