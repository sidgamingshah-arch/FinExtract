"""WHAT THE CONFIGURATION MAY NOT BECOME — the invariants a vocabulary sweep must not break.

NEW FILE -> backend/tests/test_configuration_invariants.py

WHY THESE, AND WHY NOW. The set is being broadened caption by caption, and a broadening is the one
kind of edit that can quietly change the SHAPE of the configuration: a line added where a part was
meant, an alias given to a second claimant nothing can choose between, a part hung off a parent
that does not exist. None of those fails an existing test, and each of them is invisible in a diff
of a 1.1 MB JSON file.

THE TEMPLATE BOUNDARY IS THE HARD ONE, and it is a stated product constraint rather than a
preference: the output spread's columns are fixed, so a change may add a PART and never a LINE.
That is mechanically checkable — the template declares 480 canonical keys, every line item except
one is among them, and all 64 `sub__*` items are outside — so it is checked rather than trusted.

THE TIE COUNT IS A RATCHET, NOT A TARGET. 140 aliases today are claimed by two lines with the same
scope, the same `match_priority`, and no label owner to break it — which means declaration order
picks the winner and every other claimant is unreachable for that caption. The seven `is_retained`
movements were one instance and are fixed; the rest are pre-existing and are not this file's job to
resolve. What this file refuses is an INCREASE, because adding one is how a well-meant alias makes
an existing line unreachable.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.line_item_audit import (
    dangling_references, keys_outside_the_template, self_denying_note_sources, template_keys,
    unbreakable_ties as _unbreakable_ties, unsigned_terms,
)

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
TEMPLATE = TEMPLATES / "output_csv_hk_v1_template.json"

#: THE ONE LINE ITEM THAT IS NEITHER a template column nor a part is named in
#: `services.line_item_audit.NOT_A_COLUMN`, which `keys_outside_the_template` applies — named there
#: rather than tolerated by a rule, so it cannot grow a second member by accident.

#: Aliases claimed by two lines with nothing able to choose between them. A RATCHET — see the
#: module docstring. Lower it when ties are resolved; never raise it to make a change pass.
#:
#: 140 -> 69 when the `is_oci` section's shared vocabulary was resolved: five of its eight lines
#: carried the SECTION TOTAL's caption alongside their own, on one statement in one section at
#: equal priority, which was the largest unbreakable cluster in the set. The caption now belongs to
#: `is_oci__total_other_comprehensive_income` alone.
#:
#: 69 -> 8 when the six remaining generic captions were denied — the wealth-management carve-outs,
#: a related-party table's narrative column heading, "Trade payables" on the trade-AND-OTHER
#: aggregate, a shareholder loan claimed by a receivable, the hedging captions on the reserves
#: aggregate, and the opening words of "Presented in RMB'000". Each was a caption that could not
#: pick one of its claimants; see `services.spec_alias_curation`.
#:
#: 8 -> 0 when the last three clusters were split rather than denied, because unlike the six above
#: each caption DOES say which claimant it means: a finance-lease "proceeds" caption authored on the
#: repayments line as well (opposite signs), two related-party captions authored on the payables
#: aggregates the related-party amount is a part of, and one zh disposal list copied verbatim onto
#: both the investment-property and the associates line (four of the five name their disposal; the
#: generic 出售投资所得款项 went to the concept that already owned its English twin).
#:
#: AT ZERO THIS IS NOW AN EQUALITY, and that is the point: every alias in the shipped set reaches
#: its line by something the rulebook states — a label owner, a section, a statement, a priority —
#: and never by the order two concepts happen to be declared in. There is no margin left to absorb
#: a regression, which is the strongest form of this test and the reason not to leave one.
_UNBREAKABLE_TIE_CEILING = 0


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def resolved():
    """RESOLVED, because the gate fields are folded in from `inherits` at load time.

    523 of the 527 items declare `inherits` and almost none declares `statements` or
    `section_scope` itself, so reading the file directly sees an unconstrained line everywhere and
    every pair of claimants looks like it overlaps. Measured while writing this: the raw read
    reported 531 unbreakable ties where the resolved read reports 140.
    """
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _template_keys() -> set[str]:
    """`services.line_item_audit.template_keys` against the shipped template — the walk itself is
    imported, so this is only the file read."""
    return template_keys(json.loads(TEMPLATE.read_text(encoding="utf-8")))


# ── the template boundary ────────────────────────────────────────────────────────────────────

def test_every_line_item_is_either_a_template_column_or_a_part(raw):
    """THE PRODUCT CONSTRAINT. The spread's columns are fixed, so configuration work adds PARTS.

    A `sub__*` key is a part: it feeds a line through `parent`/`rollup` and is never printed. Any
    other new key is a new column, which is what this refuses.
    """
    offenders = keys_outside_the_template(
        raw, json.loads(TEMPLATE.read_text(encoding="utf-8")))
    assert offenders == [], (
        f"these line items are neither a template column nor a `sub__` part, so they would print "
        f"a column the spread does not declare: {offenders}")


def test_no_part_is_secretly_a_template_column(raw):
    """The other direction. A `sub__` key that IS a template column would be printed twice — once
    as itself and once through the parent it rolls into."""
    columns = _template_keys()
    both = sorted(i["key"] for i in raw["items"]
                  if i["key"].startswith("sub__") and i["key"] in columns)
    assert both == [], f"`sub__` parts that are also template columns: {both}"


def test_the_boundary_is_not_vacuous(raw):
    """Both halves must be non-empty, or the two tests above pass by having nothing to check."""
    keys = [i["key"] for i in raw["items"]]
    assert sum(1 for k in keys if k.startswith("sub__")) >= 60
    assert len(_template_keys()) >= 400


# ── structural integrity ─────────────────────────────────────────────────────────────────────

def test_every_parent_names_a_line_that_exists(raw):
    """A part whose parent is missing is a figure with nowhere to roll up to — it extracts and
    then reaches no line, which looks identical to not having been authored."""
    dangling = [d for d in dangling_references(raw) if ".parent ->" in d]
    assert dangling == [], dangling


def test_every_term_names_a_line_that_exists(raw):
    """Same property for `terms`, which is how a calculated line names its addends.

    A term is `{"ref": <key>, "sign": +1|-1}` rather than a bare key — the sign is what makes a
    deduction a deduction, and reading the entry as a string is how this test failed first.
    """
    dangling = [d for d in dangling_references(raw) if ".terms.ref ->" in d]
    assert dangling == [], dangling


def test_every_term_declares_a_sign(raw):
    """A term with no sign is summed as an addition by default, so a missing sign on a DEDUCTION
    publishes the wrong total with nothing to show it — the positional-signs failure
    `note_sourced.resolve_sources` records, one layer up in the configuration."""
    assert unsigned_terms(raw) == [], unsigned_terms(raw)


# ── the tie ratchet ──────────────────────────────────────────────────────────────────────────

# `_unbreakable_ties` LIVES IN `services.line_item_audit` NOW, and is imported above rather than
# restated here. It was written in this file and was its only reader until
# `scripts/export_line_items_seed.py` had to answer the same question about a DATABASE row — and
# two spellings of "is this alias tie real" is the two-places-computing-one-quantity bug this
# codebase keeps finding. The docstring that explained the shape moved with it.


def test_no_change_adds_an_unbreakable_alias_tie(resolved):
    """THE RATCHET. Broadening the vocabulary is the work; making an existing line unreachable is
    not, and the two look identical in a diff."""
    ties = _unbreakable_ties(resolved)
    assert len(ties) <= _UNBREAKABLE_TIE_CEILING, (
        f"unbreakable alias ties rose to {len(ties)} against a ceiling of "
        f"{_UNBREAKABLE_TIE_CEILING}. An alias was given to a second line with the same scope and "
        f"the same priority, so declaration order now decides which one a caption reaches. "
        f"New ones will be among: {ties[:12]}")


def test_the_ceiling_is_not_slack(resolved):
    """A ceiling far above the real count stops ratcheting. Kept within 10 of the measurement, so
    resolving ties is rewarded with a lower ceiling rather than absorbed by the margin.
    """
    ties = _unbreakable_ties(resolved)
    assert _UNBREAKABLE_TIE_CEILING - len(ties) <= 10, (
        f"the ceiling is {_UNBREAKABLE_TIE_CEILING} and the real count is {len(ties)} — lower the "
        f"ceiling to {len(ties)}")


def test_the_seven_retained_movements_are_no_longer_tied(resolved):
    """THE ONE SET OF TIES THAT WAS RESOLVED, pinned so it cannot come back. All seven claimed the
    identical retained-earnings vocabulary at priority 10 on one statement and one section."""
    ties = _unbreakable_ties(resolved)
    retained = [t for t in ties if t.count("is_retained__") >= 2]
    assert retained == [], retained


# ── a gate that vetoes what it requires ──────────────────────────────────────────────────────

#: The most exact duplicates any one `note_source` gate pair may carry between its inclusion list
#: and its exclusion list. A RATCHET, like the tie ceiling above: lower it when a declaration is
#: repaired, never raise it to make one pass.
#:
#: 44 -> 11 when `sub__rp_find_3` was repaired. It carried 44 of its 79 `row_terms` verbatim in
#: `row_terms_none`, and all four captions the spec names — 其他应收款, 一年内到期的长期应收款,
#: 长期应收款, 发放贷款及垫款 — were `required=True` and `denied=True` at once, so the line selected
#: zero rows on every filing ever run.
#:
#: 11 is now the highest, on `sub__cp_current_loans_advances_net` and `sub__cp_funds_placed_net`,
#: and those two are NOT known to be broken: they publish figures, and an overlap on
#: `impairment`/`信用减值准备` is the shape of a list that admits a family and then excludes one
#: member of it. Left at the measurement rather than "fixed" on the strength of this count alone.
_SELF_DENIAL_CEILING = 11


def test_no_note_source_vetoes_the_captions_it_requires(raw):
    """`row_caption_none` is applied AFTER `row_caption_any`, so a pattern in both lists can never
    admit a row. Enough of them and the declaration is unsatisfiable while still reading, field by
    field, like careful authoring — and NOTHING else catches it: the schema accepts both lists,
    every pattern compiles, the publish gates see one edit at a time, and a run reports only that
    the line found no rows, which is what a filing that does not disclose the figure looks like.
    """
    offenders = self_denying_note_sources(raw, threshold=_SELF_DENIAL_CEILING)
    assert offenders == [], (
        f"these declarations veto a large share of what they require, so they may be "
        f"unsatisfiable: {offenders}")


def test_the_three_finds_can_each_admit_the_captions_the_spec_names(raw):
    """THE REGRESSION THIS FILE EXISTS FOR, on the line it was found on.

    The governing rule is "select the highest amount among Find 1, Find 2 and Find 3", and it is
    configured exactly that way — `bs_nca__due_from_related_parties_ltp` declares a MAX_VALID rung
    over the three. A Find that can never produce a figure does not make the answer smaller; it
    makes the selection a maximum over fewer readings than the rule names, silently.

    So what is pinned is that each Find's own gates ADMIT the four captions the spec lists, and
    that the exclusions the spec also lists still deny. 委托贷款 is in the spec's own words —
    "但不包括委托贷款" — and 关联方组合 is the expected-credit-loss staging row whose 账面余额 is a
    gross figure rather than a balance.
    """
    import re

    by_key = {i["key"]: i for i in raw["items"]}
    spec_captions = ("其他应收款", "一年内到期的长期应收款", "长期应收款", "发放贷款及垫款")
    spec_exclusions = ("委托贷款", "应付账款", "关联方组合", "账面余额", "交易金额", "合计")

    # THE TWO FINDS MEET THE FOUR CAPTIONS AT DIFFERENT LEVELS, which is the design and not an
    # inconsistency. Find 2 reads the four receivable NOTES, so the captions are what identifies
    # the note — they belong to `note_title_any`, and its row gates pick the related-party rows
    # INSIDE. Find 3 reads the related-party note, whose title names the chapter and inside which
    # the four captions are the GROUPING headers over counterparty rows — so for Find 3 they belong
    # to `row_caption_any`. Asserting one shape for both is what this test did first, and it failed
    # on the Find that works.
    # FIND 3'S ROW GATES MOVED to its gross half when the net had to be computed: the
    # 关联方应收应付款项 note prints 账面余额 and 坏账准备 and no net column, so Find 3 became
    # gross-less-allowance and the two halves carry the note_source between them.
    # FIND 2 NOW FINDS ITS NOTES BY MEANING (note_terms, no title regex), so for it the four
    # captions are headings its terms must COVER; Find 3 still admits them as grouping rows.
    from types import SimpleNamespace as _NS

    from app.services.line_item_notes import heading_covered
    find2 = by_key["sub__rp_find_2"]["note_source"]
    for caption in spec_captions:
        assert heading_covered(_NS(note_source=_NS(note_terms=find2["note_terms"])), caption), (
            f"sub__rp_find_2's note terms no longer cover {caption!r}")
    assert find2.get("row_caption_none"), "sub__rp_find_2 declares no row veto at all"
    where = {"sub__rp_find_3_gross": "row_caption_any"}
    for key, field in where.items():
        src = by_key[key].get("note_source") or {}
        admits = [re.compile(p) for p in (src.get(field) or ())]
        vetoes = [re.compile(p) for p in (src.get("row_caption_none") or ())]
        assert admits and vetoes, f"{key} declares no {field} or no row veto at all"

        for caption in spec_captions:
            assert any(p.search(caption) for p in admits), (
                f"{key}.{field} no longer admits {caption!r}")

        for caption in spec_exclusions:
            assert any(p.search(caption) for p in vetoes), (
                f"{key} no longer excludes {caption!r}, which the spec names as an exclusion")

    # AND THE CONTRADICTION ITSELF, on the line it was found on: Find 3 required all four captions
    # in `row_caption_any` AND denied all four in `row_caption_none`, so no row could ever pass.
    src3 = by_key["sub__rp_find_3_gross"]["note_source"]
    admits3 = [re.compile(p) for p in src3["row_caption_any"]]
    vetoes3 = [re.compile(p) for p in src3["row_caption_none"]]
    for caption in spec_captions:
        assert any(p.search(caption) for p in admits3), caption
        assert not any(p.search(caption) for p in vetoes3), (
            f"Find 3 vetoes {caption!r}, which it also requires — no row can pass both gates, "
            f"which is the state it shipped in and produced zero rows on every filing")


def test_the_three_finds_all_feed_the_line_that_selects_between_them(raw):
    """A Find is only worth repairing if it still reaches the selection. All three name the same
    parent, and that parent's MAX_VALID rung names all three as `any_of` terms."""
    by_key = {i["key"]: i for i in raw["items"]}
    finds = [f"sub__rp_find_{n}" for n in (1, 2, 3)]
    parents = {by_key[k].get("parent") for k in finds}
    assert parents == {"bs_nca__due_from_related_parties_ltp"}, parents

    rungs = by_key["bs_nca__due_from_related_parties_ltp"].get("cascade") or []
    chosen = next((r for r in rungs if r.get("terms_op") == "max"), None)
    assert chosen is not None, "the parent no longer selects a MAXIMUM between the three readings"
    refs = {str(t.get("ref")) for t in (chosen.get("terms") or [])}
    assert refs == set(finds), refs
    assert all(str(t.get("role")) == "any_of" for t in chosen["terms"]), (
        "a Find that is not `any_of` makes the whole rung refuse when that Find is absent")
