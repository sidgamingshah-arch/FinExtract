"""THE LINE ITEMS FORM HAS ONE INVENTORY OF ITSELF, NOT THREE.

NEW FILE -> backend/tests/test_form_field_lists.py

`screens/LineItems.tsx` renders every control through one filter, `fld(name, render)`, and keeps
three lists of names beside it:

  * ``GROUP_FIELDS`` — membership per banner. A banner has to say how many controls are inside it
    BEFORE the controls render (React builds the heading first), so the count comes from walking
    this list through the same filter. A name listed here with no control inflates that count.
  * ``CONDITIONAL_FIELDS`` — the names ``withheldReason`` may speak about, and the set the form
    header counts as "withheld". A name here with no control inflates the header instead.
  * ``RETIRED_FIELDS`` — controls the server refuses. A name here with no control guards nothing.

WHAT THIS COSTS WHEN IT DRIFTS, and it had drifted on all three: removing the retired controls left
8 names inflating banner counts, 13 ``withheldReason`` rules speaking about controls that no longer
existed, and 33 dead entries in ``RETIRED_FIELDS``. The visible failure is a banner announcing four
fields over a group showing three, and a header saying six controls are withheld on a form that has
none of them — which is exactly the reading an author cannot distinguish from a load failure.

The rule is therefore an IDENTITY, not a subset: a name is in ``GROUP_FIELDS`` if and only if a
``fld`` call renders it, in exactly one group.

`parent` IS THE CASE THAT MAKES THIS WORTH A TEST. It was retired on a measured "13 of 475", taken
against an older set. Against the shipped 539 it is declared by exactly the 77 note-read parts —
every one of them — and it is the only field saying which whole a part explains, which the workspace
trace and the Line Items sheet's indentation both walk. A stale measurement in a comment retired the
one field the hierarchy depends on, and nothing failed.

WHY A SOURCE TEST. There is no vitest setup in `frontend/`, and the Playwright suite needs both
servers. Same idiom as `test_render_phase_reseed` and `test_origin_contract`: read the source,
assert the property, fail on the commit that breaks it.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "frontend" / "src" / "screens" / "LineItems.tsx").read_text(encoding="utf-8")
SEED = json.loads(
    (ROOT / "backend" / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
    .read_text(encoding="utf-8"))


def _block(head: str, close: str) -> str:
    start = SRC.index(head)
    return SRC[start:SRC.index(close, start)]


def _names(text: str) -> list[str]:
    """Quoted strings in the CODE, not in the comments beside it.

    Every one of these lists carries prose explaining it, and that prose quotes labels and former
    field names — `"How the figure is obtained"` above `assembly` is the example that caught this.
    Reading those as members made the test fail on a form that was correct, which is the worst kind
    of assertion: it teaches the next author to delete the explanation.
    """
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    stripped = re.sub(r"^\s*//.*$", "", stripped, flags=re.M)
    return re.findall(r'"([^"]+)"', stripped)


RENDERED = set(re.findall(r'\{fld\("([^"]+)"', SRC))
# `SIMPLE` WAS HERE — the per-type simple-form allowlists (`COMMON_SIMPLE`, `MEANING_SIMPLE`,
# `NOTE_SOURCE_SIMPLE`) read out of the source. The simple/advanced toggle is gone and so are they,
# for the reason the two retired tests below record: a second inventory of which controls matter has
# to be kept in step by hand, and it fell out of step the first time it was touched.
GROUPED = _names(_block("const GROUP_FIELDS", "} as const;"))
CONDITIONAL = set(_names(_block("const CONDITIONAL_FIELDS", "\n];")))
RETIRED = set(_names(_block("const RETIRED_FIELDS = new Set([", "]);")))


def test_every_control_is_in_exactly_one_group() -> None:
    """The identity, in the direction that inflates a banner count."""
    assert sorted(GROUPED) == sorted(RENDERED), (
        f"listed with no control: {sorted(set(GROUPED) - RENDERED)}; "
        f"rendered but ungrouped: {sorted(RENDERED - set(GROUPED))}")
    assert len(GROUPED) == len(set(GROUPED)), (
        f"in two groups at once: {sorted(n for n in set(GROUPED) if GROUPED.count(n) > 1)}")


def test_no_withholding_rule_speaks_about_a_control_that_is_not_there() -> None:
    """A withheld reason is rendered UNDER its control, so one without a control is a count in the
    header and a sentence nobody can reach."""
    assert not CONDITIONAL - RENDERED, sorted(CONDITIONAL - RENDERED)


def test_nothing_is_retired_unless_it_guards_a_control() -> None:
    """`RETIRED_FIELDS` is a filter, not a changelog. A name with no control is inert, and the
    measurements it carried belong in prose where they cannot be mistaken for live rules.

    The two exceptions are deliberate and named: `requiredNow` forces `cascade` and `implemented_by`
    back onto a derived line's form, so both must be retired AND rendered — a derived line that has
    neither is refused by the server, and withholding the controls would leave an author with a
    refusal and nothing on screen to answer it.
    """
    forced = {"cascade", "implemented_by"}
    assert RETIRED & RENDERED == forced, sorted(RETIRED & RENDERED)
    assert not RETIRED - RENDERED, sorted(RETIRED - RENDERED)


def test_parent_is_authorable_because_every_part_declares_it() -> None:
    """THE STALE-MEASUREMENT REGRESSION. If `parent` is ever retired again, this says why not."""
    parts = [i for i in SEED["items"] if i.get("note_source")]
    # 60 items carry a `note_source` — see the census in `test_retired_derivations`.
    #
    # SIXTY-ONE UNTIL FIND 1 STOPPED READING A NOTE. `sub__rp_find_1` is labelled
    # "related-party receivables in the BALANCE SHEET" and now reads that face, declared as
    # `section_scope: ['bs_nca']` against a `face_only` section; its two siblings keep the
    # note readings their own labels describe.
    #
    # SIXTY-TWO UNTIL `sub__cfo_depreciation` STOPPED READING A NOTE. It was moved to the cash-flow
    # section and its `note_source` removed outright (`inherits` notes -> cf_oper_indirect,
    # `statement` none -> cash_flow, `note_use` decomposition_allowed -> evidence_only), which is
    # coherent: the depreciation add-back is printed ON THE FACE of an indirect cash-flow statement,
    # so a line that takes it there needs no note. It declares that with
    # `section_scope: ['cf_oper_indirect']` against `section_defaults[...].face_only`, the same way
    # `sub__face_principal_revenue` declares it for the income statement.
    # 61 since Find 3 split into a gross half and an allowance half, each declaring `parent`.
    assert len(parts) == 65, len(parts)   # 65: the three Securities (CP) Find 2 and non-current parts left the set   # 68 with the related-party TRADE RECEIVABLE's two note readings — the 账面余额 gross and the 坏账准备 allowance it is net of, one part per `measure` of the same 应收账款 group   # 66 for the same reason the part count moved: the two related-party payable groups   # 62: the other-receivables net's two readings are two
    # parts, and it is itself no longer one of them — a derived parent is not note-sourced.
    assert all(i.get("parent") for i in parts), (
        "a note-read part with no parent has nothing to trace back to")
    assert "parent" in RENDERED and "parent" not in RETIRED, (
        "`parent` links each of the 77 parts to the whole it explains — retiring it makes a new "
        "part unauthorable and the set's only hierarchy unreadable")


def test_the_band_numbers_cover_the_groups_that_render() -> None:
    """`BANDS` drives expand-all/collapse-all. A number with no group leaves the control claiming to
    open a section that is not there; a group with no number gets a banner that does not respond."""
    bands = set(_names(_block("const BANDS = [", "];")) or
                re.findall(r"\d+", _block("const BANDS = [", "];")))
    rendered_bands = set(re.findall(r"band\((\d+), ", SRC))
    assert bands == rendered_bands, f"BANDS={sorted(bands)} vs rendered={sorted(rendered_bands)}"


# TWO TESTS RETIRED HERE, and what they were guarding is worth keeping written down.
#
# `test_the_simple_form_lists_name_only_controls_that_exist` held the simple-form allowlists to the
# same identity as the three lists above — a name with no control behind it contributes nothing.
# It had already caught one drift: `COMMON_SIMPLE` was `["label", "statement", "type"]` after the
# `statement` control went, so the entry was dead, and `definition` was reachable only through
# `MEANING_SIMPLE`, which only a caption-matched line got — the simple form for a CALCULATED line
# was `label`, `type`, `terms`, with no way to say what the line means.
#
# `test_every_type_can_say_what_the_line_means` pinned `definition` into `COMMON_SIMPLE` for exactly
# that reason.
#
# BOTH ARE MOOT: there is no simple form. The lists are deleted and every control is on the form,
# with `withheldReason` removing the ones this line's selections make meaningless and saying why.
# That filtering is DERIVED from the selections, so it cannot fall out of step the way a hand-kept
# allowlist did — which is what the second drift proved, when `route` was added to the form and not
# to `COMMON_SIMPLE` and the control rendered invisibly to every author.


# ── the two tabs, and the contract that the deterministic one cannot reach the model ──────────

DETERMINISTIC_TAB = set(_names(_block("const DETERMINISTIC_FIELDS = new Set([", "]);")))


def test_the_deterministic_tab_is_exactly_what_the_request_withholds() -> None:
    """THE TWO-ROUTE CONTRACT, asserted ACROSS THE LANGUAGE BOUNDARY.

    The screen promises that the deterministic tab cannot change what the model is asked — that is
    the whole reason it is a separate tab and the reason it may be left blank. The promise is kept
    in Python (`services.line_item_llm.line_item_payload` sends none of those fields) and MADE in
    TypeScript (`DETERMINISTIC_FIELDS`), and nothing connects the two.

    So this does. For every field the screen files under "Patterns", the payload built from an item
    carrying a value for it must contain no trace of that value. A field added to the tab that the
    payload still sends would be a lie on the screen; a field REMOVED from the payload but left off
    the tab is merely untidy, and is the other assertion below.
    """
    from app.services.line_item_config import load_shipped_set
    from app.services.line_item_llm import line_item_payload

    st = load_shipped_set()
    # The line with the most authored recognition of any in the set — 39 row regexes, 51 row terms,
    # aliases and a full prose block — so every field on the tab has a value to leak.
    item = next(i for i in st.items if i.key == "sub__cp_other_receivables_rp")
    payload = line_item_payload(item, ("24",))

    ns = item.note_source
    values: dict[str, list[str]] = {
        "aliases": list(item.aliases or ()),
        "exclude_hints": list(item.exclude_hints or ()),
        "note_source.row_caption_any": list(ns.row_caption_any or ()),
        "note_source.row_caption_none": list(ns.row_caption_none or ()),
        "note_source.prose_any": list(ns.prose_any or ()),
        "note_source.prose_landed_in": list(ns.prose_landed_in or ()),
        # paired into the same control as the regex lists above, so they are on the tab too
        "note_source.row_terms": list(ns.row_terms or ()),
        "note_source.row_terms_none": list(ns.row_terms_none or ()),
    }
    assert any(values[f] for f in values), "the fixture must carry recognition, or this is vacuous"

    # STRUCTURAL, NOT SUBSTRING, and the first version of this test got that wrong. Serialising the
    # payload and looking for each authored string inside it reported three fields as leaking —
    # because `row_terms_none` contains "advances", "ities" and "back", and `row_caption_any`
    # contains 关联方, every one of which legitimately occurs inside the line's own DEFINITION prose.
    # The contract is not "these characters never appear"; it is "the request carries no field
    # holding these lists". So assert on keys and on whole values.
    # `read_from` IS THE ROUTE, NOT RECOGNITION. It says which PLACES this line's figure may be
    # taken from — the statement, a note's rows, a note's prose, the whole filing — which is the
    # same question `printed_in` half-answers and which the resolver now enforces
    # (`services.note_sourced.resolve_sources`, `allow_face`/`allow_pages`). A request that did not
    # carry it invited a citation the run would then refuse, and refusing a citation the request
    # implicitly permitted is the same unfairness as grading against a withheld constraint.
    ALLOWED_KEYS = {"key", "label", "notes_supplied", "definition", "exclude", "printed_in",
                    "statement", "read_from", "sign_convention"}
    assert set(payload) <= ALLOWED_KEYS, (
        f"the request grew a key the two-route split has not been reasoned about: "
        f"{sorted(set(payload) - ALLOWED_KEYS)}")

    # And no value in the request IS one of the deterministic lists, whatever key it arrived under —
    # which catches a rename as well as an addition.
    sent_lists = [v for v in payload.values() if isinstance(v, list)]
    for field, vals in values.items():
        if not vals:
            continue
        assert list(vals) not in sent_lists, f"{field} is sent to the model under some key"
        # `notes_supplied` is a list of note numbers; nothing else list-shaped may overlap a
        # recognition list at all, which is a stronger and still false-positive-free check.
        for sent in sent_lists:
            shared = set(map(str, sent)) & set(map(str, vals))
            assert not shared, f"{field} shares {sorted(shared)[:3]} with a list in the request"


def test_every_field_on_the_deterministic_tab_is_a_real_control() -> None:
    """The tab is a filter over controls, so a name with no control filters nothing — the same
    identity the three lists above are held to, for the same reason."""
    assert not DETERMINISTIC_TAB - RENDERED, sorted(DETERMINISTIC_TAB - RENDERED)


def test_the_tab_split_leaves_the_meaning_fields_on_the_llm_side() -> None:
    """THE OTHER DIRECTION, because a tab that swallowed the definition would be worse than no tab.

    `definition` is the whole of what the model is told about a line after the prompt merge, and
    the placing and assembly questions decide which request is made at all. None of them may drift
    onto the patterns tab, where an author would not think to look.
    """
    must_stay = {"definition", "exclude_criteria", "label", "type", "route", "statements",
                 "section_scope", "parent", "order", "terms", "cascade",
                 "note_source", "note_source.note_title_any",
                 "llm_only_if_note_tagged", "note_selection"}
    on_the_wrong_tab = sorted(must_stay & DETERMINISTIC_TAB)
    assert not on_the_wrong_tab, on_the_wrong_tab
    # And they are real controls, so the assertion is not about names nobody renders.
    assert must_stay <= RENDERED, sorted(must_stay - RENDERED)
