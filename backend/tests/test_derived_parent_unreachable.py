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

The aliases were restored, then removed again by directive with that cost accepted — see
`test_no_derived_parent_carries_recognition` below for the figures and the traced mechanism. The
lesson that survives all three steps is in the shape of the check rather than in any comment: a
no-answer-changed probe over the matcher was the wrong instrument, because the alias index is read
by something that is not the matcher. Comparing published figures is what caught it.
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

    # TEN — the tenth is `sub__fa_cp_intermediate_residual`, and it must be locked out of the
    # matcher exactly like the other nine: its figure is its cascade's, so no caption reaches it.
    # 10, not 13 — the three related-party Find lines are `extracted` now. See
    # `test_derived_parent_gate` for why.
    # 11 since Find 1 became `derived`: the spec's first reading is a SUM over the related-party rows the balance sheet prints, so it is arithmetic over other lines rather than a row of its own.
    # 12 since Find 3 split into a gross half and an allowance half: the 关联方应收应付款项 note prints 账面余额 and 坏账准备 and no net column, so the 淨金額 the spec asks for is computed, which made Find 3 itself derived.
    assert len(derived) == 17, len(derived)   # 17 with the related-party TRADE RECEIVABLE, whose 应收账款 group the note tabulates and the face does not print on any filing in the corpus: instructed to take the double count against Due from Related Parties(LTP), which reads the same group through Find 3. Its two template aliases moved to `sub__rp_face_trade_receivable` rather than going, because unlike the payable twin below nothing pins them to the column itself.   # 16 with the two related-party PAYABLE parents — Due to Related Parties(CP) and its LTP twin — each computed from the group its note prints (其他应付款, 长期应付款). Their template aliases went with the change, for the reason this file holds: a derived parent's figure is its cascade's, and the compound labels they carried ("Due to related parties / Associates / Jointly controlled entities / fellow subsidiaries/Affiliates") matched nothing on any filing. The TRADE PAYABLE stays on the face and the receivable no longer does: `test_a_resolved_tie_kept_a_home_for_every_caption_it_split` pins "Trade payables to related parties" to it, and a derived parent is unmatchable.   # 13 since the other-receivables net became a
    # two-rung cascade; like every derived line it is now locked out of the matcher, which is
    # why its two leaves took its place in `llm_focus_keys`.
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


def test_no_derived_parent_carries_recognition() -> None:
    """REMOVED BY DIRECTIVE, WITH THE COST MEASURED AND ACCEPTED.

    The 258 entries were deleted, restored, and deleted again. The middle step is the one worth
    keeping, because it says what these aliases were for: `stages.residual._concept_captions` reads
    a concept's aliases as a VETO index, and `build_working_view` projects this configuration into
    the concepts it reads — so an alias here was also a sweep guard there. Removing them moves
    published figures on a real filing:

        2024 Annual Report, deterministic run
          "Financial assets at fair value through profit or loss" (current assets)
                                        -> bs_cl__other_current_liabilities
          "Debt investment at fair value through other comprehensive income"
                                        -> bs_cl__other_current_liabilities
          "Financial assets at fair value through profit or loss" (non-current)
                                        -> bs_nca__other_non_current_assets
          14 calculated figures move with them; total assets 168,156,062 -> 180,447,374

    Two of the three put an ASSET caption in a LIABILITY bucket. Traced: the row's `section_hint`
    says CURRENT ASSETS and its resolved section is None, because a banner is SPENT once its own
    subtotal is above the row ("Total current assets"), at which point the structure BELOW decides —
    and below is current liabilities. That cascade is deliberate and measured against a different
    filing. Eligibility rule 5, "the row's sign agrees with the section's sign_convention", is in
    force and cannot catch it: an asset and a liability are both stored positive, so the guard has
    no opinion. The bucket is flagged for review on four separate triggers, so the misplacement is
    visible to a reviewer rather than silent — but the published figure is wrong until reviewed.

    A SIDE guard — refusing a row whose banner names an asset section from a liability bucket — is
    the fix and is NOT implemented here: it changes the residual sweep for every filing and is a
    decision of its own.
    """
    for d in _derived(_set()):
        assert not d.aliases, d.key
        assert not d.aliases_i18n, d.key
        assert not d.regex_hints, d.key
        assert not d.keyword_hints, d.key
        assert not d.exclude_hints, d.key
