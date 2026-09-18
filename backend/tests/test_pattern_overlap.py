"""Caption-normalisation patterns as config, and the overlap invariant that lets them be.

WHAT THESE TESTS ARE FOR. Three CAS strippers live in `mapping.normalize_label` as module
constants, and each one exists because of a shape a FILING printed — an enumerator, a
sign-convention parenthetical, a wrapped caption's orphaned tail. That makes them vocabulary, and
moving vocabulary out of code is safe only if something replaces what the code was doing for free.
The claim was that the code was holding an ORDER. It was not, quite:

  * On the eight real mainland captions in `scripts/demo_code_vs_config.py`, all six permutations
    of the three patterns agree — 0 of 8 order-sensitive. That corpus carries no orphaned wrap
    fragment, which is the whole point of one of the three rules.
  * Add the string `_CAS_ORPHAN_HEAD`'s own comment quotes and the answer flips: three of six
    orders give `营业利润` and three give `三、营业利润`. `test_the_shipped_order_is_load_bearing`
    below runs it.
  * Substitute one plausible authored orphan head and three of six orders ERASE THE CAPTION.

The last two are both order-dependence and they are not the same defect, so the invariant is
neither "the order is fixed" nor "any order works". It is that no two patterns claim the same
characters. Where they do, the order decides which text is DESTROYED and the survivor is a caption
the filing never printed. Where they do not, a wrong order only UNDER-strips: the caption keeps
more of what was printed, fails to match, and the row lands in its section's residual under its own
label where a reviewer can see it. `parity_normalisation.py` measured that difference on a real
perturbation — 976 of 1,050 moved resolutions fell to unmatched and 74 landed on a DIFFERENT
concept. Overlap is how you get the 74.
"""
from __future__ import annotations

import itertools
import json
import pathlib
import re

import pytest
from pydantic import ValidationError

from app.schemas.line_items import (CaptionTransform, LineItemDef, LineItemSet, MappingVocabulary,
                                    load_line_item_set)
from app.services import mapping

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# The three shipped rules, transcribed field-for-field from `normalize_label`'s own calls:
#   _CAS_SIGN_NOTE.sub(" ", text)                 -> replacement " ", every occurrence
#   _CAS_ORPHAN_HEAD.sub("", text, count=1)       -> replacement "", the first only
#   _CAS_LINE_PREFIX.sub("", text, count=1) x3    -> replacement "", the first, to a fixed point
# Read off `mapping` rather than copied as literals: a copy that drifts would test the copy.
SHIPPED = [
    CaptionTransform(id="cas_sign_note", pattern=mapping._CAS_SIGN_NOTE.pattern,
                     replacement=" ", count=0, passes=1,
                     note="三、营业利润（亏损以“－”号填列） — presentation, not part of the name"),
    CaptionTransform(id="cas_orphan_head", pattern=mapping._CAS_ORPHAN_HEAD.pattern,
                     replacement="", count=1, passes=1,
                     note="填列） glued to the front of the next caption by a wrap merge"),
    CaptionTransform(id="cas_line_prefix", pattern=mapping._CAS_LINE_PREFIX.pattern,
                     replacement="", count=1, passes=3,
                     note="一、 / 1、 / （一） / 其中： / 减： — the statement's own numbering"),
]

# What someone reaching for "drop the fragment up to the first closing bracket" writes. It is not
# a strawman: it is shorter than the shipped pattern, it handles every example in the shipped
# pattern's comment, and nothing about reading that comment suggests the bracket-crossing refusal
# is the load-bearing half.
GREEDY_HEAD = CaptionTransform(id="cas_orphan_head", pattern=r"^.*?[）)]\s*",
                               replacement="", count=1, passes=1)


def _apply(order, text: str) -> str:
    """The declared transforms in `order`, folded as `normalize_label` folds."""
    for t in order:
        text = t.apply(text)
    return re.sub(r"\s+", " ", text).strip()


def _seed() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


def _with_transforms(raw: dict, transforms: list[CaptionTransform]) -> dict:
    return {**raw, "vocabulary": {**raw.get("vocabulary", {}),
                                  "caption_transforms": [t.model_dump() for t in transforms]}}


# ── the shipped patterns pass ────────────────────────────────────────────────────────────────────

def test_the_shipped_cas_patterns_are_accepted_as_config():
    """The three rules `normalize_label` compiles in, declared, must load."""
    vocab = MappingVocabulary(caption_transforms=SHIPPED)

    assert [t.id for t in vocab.caption_transforms] == [
        "cas_sign_note", "cas_orphan_head", "cas_line_prefix"]
    assert vocab.transform_order_check().overlaps == []


def test_the_shipped_patterns_reproduce_the_shipped_fold_on_real_captions():
    """The transcription is faithful, or the acceptance above is testing something else.

    Compared against `normalize_label` rather than against expected strings, because the point is
    that the config says what the code says. The Han fold, the punctuation strip and the note
    citations are `normalize_label`'s other stages and are not declared here, so the comparison is
    the CAS stage only — which is why these six captions are all Simplified and citation-free.
    """
    vocab = MappingVocabulary(caption_transforms=SHIPPED)
    for caption in ("减：营业成本（以“-”号填列）", "三、营业利润（亏损以“－”号填列）",
                    "填列） 三、营业利润（亏损以“－”号填列）", "其中：营业收入",
                    "（二）综合收益总额", "1、营业收入"):
        folded = re.sub(r"\s+", " ", vocab.apply_caption_transforms(caption)).strip()

        assert folded == mapping.normalize_label(caption), caption


def test_a_note_reference_survives_both_the_code_and_the_config():
    """七、70 is a note pointer, not a caption, and stripping it leaves a matchable '70'.

    `_CAS_LINE_PREFIX`'s digit lookahead is the refusal that prevents it. Declared as config the
    refusal has to come across intact, because a note reference that becomes matchable is the one
    failure here that invents a figure rather than losing one.
    """
    vocab = MappingVocabulary(caption_transforms=SHIPPED)

    assert vocab.apply_caption_transforms("七、70") == "七、70"
    assert vocab.apply_caption_transforms("b) Trade receivables") == "b) Trade receivables"


def test_the_shipped_set_loads_with_the_patterns_declared():
    """475 definitions, 2,006 captions, the three rules declared over them."""
    st = load_line_item_set(_with_transforms(_seed(), SHIPPED))

    # 527, not 543: the live configuration was exported into the shipped seed — see
    # `test_retired_derivations.test_the_shipped_set_is_the_configuration_in_force`.
    assert len(st.items) == 535  # 535 since the other-receivables NET split into the two ways a note prints one — the 账面价值 COLUMN of a gross/allowance/net grid, and a row whose own reported amount is the net; one part holds one `measure`, and 000709 needs both readings
    assert len(st.vocabulary.caption_transforms) == 3
    assert st.vocabulary.transform_order_check(st.caption_corpus()).overlaps == []


# ── an overlapping pattern is refused ────────────────────────────────────────────────────────────

def test_a_greedy_orphan_head_is_refused_for_overlapping_the_sign_note():
    with pytest.raises(ValidationError) as exc:
        MappingVocabulary(caption_transforms=[SHIPPED[0], GREEDY_HEAD, SHIPPED[2]])

    assert "OVERLAP" in str(exc.value)


def test_the_refusal_names_both_transforms_the_probe_and_the_contended_text():
    """A message that does not say WHICH patterns and ON WHAT is a message that gets re-derived.

    The probe is shown because the reader has to be able to try it, and the contended text because
    "these two overlap" is a claim and "both claim '（a号 填列 ）'" is the evidence for it.
    """
    with pytest.raises(ValidationError) as exc:
        MappingVocabulary(caption_transforms=[SHIPPED[0], GREEDY_HEAD, SHIPPED[2]])
    message = str(exc.value)

    assert "'cas_sign_note'" in message and "'cas_orphan_head'" in message
    assert "both claim" in message
    probe = re.search(r"disagree on '([^']+)'", message)
    assert probe, message
    a, b = SHIPPED[0], GREEDY_HEAD
    assert _apply([a, b], probe.group(1)) != _apply([b, a], probe.group(1)), \
        "the named probe must actually disagree, or the message is fiction"


def test_the_greedy_head_erases_a_real_caption_in_three_of_six_orders():
    """WHY the refusal exists, priced on a real mainland caption rather than on a probe.

    An erased caption maps to nothing, is swept into its section's residual and itemised under its
    own label, and the section still ties — so there is no broken subtotal for anyone to notice.
    """
    transforms = [SHIPPED[0], GREEDY_HEAD, SHIPPED[2]]
    results = [_apply(order, "减：营业成本（以“-”号填列）")
               for order in itertools.permutations(transforms)]

    assert results.count("") == 3, results
    assert set(results) == {"", "营业成本"}, results


def test_the_shipped_set_is_refused_when_it_declares_an_overlapping_pattern():
    """The refusal reaches through `load_line_item_set`, not only the bare model."""
    with pytest.raises(ValidationError, match="OVERLAP"):
        load_line_item_set(_with_transforms(_seed(), [SHIPPED[0], GREEDY_HEAD, SHIPPED[2]]))


def test_a_pattern_that_swallows_another_whole_is_refused_even_reading_left_to_right():
    """Overlap is a property of the pair, not of which one is declared first.

    Both orders of the same two transforms must be refused, or the check would be enforcing a
    convention about declaration order — the thing it exists to make unnecessary.
    """
    for pair in ([SHIPPED[0], GREEDY_HEAD], [GREEDY_HEAD, SHIPPED[0]]):
        with pytest.raises(ValidationError, match="OVERLAP"):
            MappingVocabulary(caption_transforms=pair)


# ── an empty declaration is accepted and means "use the built-in" ───────────────────────────────

def test_an_empty_declaration_is_accepted_and_changes_nothing():
    """The doctrine `MappingVocabulary` states for every other field, held for this one.

    A set that declares no transforms must behave exactly as it did before the field existed —
    otherwise adding the field to the schema would silently change every rulebook already written.
    """
    vocab = MappingVocabulary()

    assert vocab.caption_transforms == []
    assert vocab.transform_probes(["三、营业利润"]) == [], "no declaration, no corpus, no work"
    assert vocab.transform_order_check().overlaps == []
    assert vocab.apply_caption_transforms("三、营业利润（亏损以“－”号填列）") \
        == "三、营业利润（亏损以“－”号填列）", "empty means the built-in, not the identity applied"


def test_the_shipped_seed_declares_none_and_still_loads():
    """The seed as it ships. This is the regression that matters most: the field is additive."""
    st = load_line_item_set(_seed())

    assert st.vocabulary.caption_transforms == []
    assert st.order_sensitive_probes == []
    # 527, not 543: the live configuration was exported into the shipped seed — see
    # `test_retired_derivations.test_the_shipped_set_is_the_configuration_in_force`.
    assert len(st.items) == 535  # 535 since the other-receivables NET split into the two ways a note prints one — the 账面价值 COLUMN of a gross/allowance/net grid, and a row whose own reported amount is the net; one part holds one `measure`, and 000709 needs both readings


def test_a_single_transform_has_no_order_to_be_dependent_on():
    vocab = MappingVocabulary(caption_transforms=[GREEDY_HEAD])

    assert vocab.transform_order_check().overlaps == []
    assert vocab.transform_order_check().revelations == []


# ── order-dependence WITHOUT overlap is real, and is recorded rather than refused ────────────────

def test_the_shipped_order_is_load_bearing_on_a_caption_the_demo_corpus_omits():
    """THE CLAIM THIS PACKAGE WAS BUILT ON, RETESTED AND FOUND HALF FALSE.

    `demo_code_vs_config.py` reports 0 of 8 order-sensitive and concludes the order is not the
    invariant. Its eight captions carry no orphaned wrap fragment. The string
    `_CAS_ORPHAN_HEAD`'s own comment quotes does, and both it and `_CAS_ORPHAN_HEAD` and
    `_CAS_LINE_PREFIX` are anchored at `^`, so the fragment hides the enumerator until it is
    stripped: strip it first and 三、 goes with it, strip it second and 三、 stays.
    """
    captions = ["填列） 三、营业利润（亏损以“－”号填列）", "填列） 三、营业利润",
                "填列） 三、其中：营业收入（亏损以“－”号填列）", "填列） 其中：营业收入"]
    for caption in captions:
        results = {_apply(order, caption) for order in itertools.permutations(SHIPPED)}

        assert len(results) == 2, f"{caption!r} -> {results!r}"
        assert mapping.normalize_label(caption) in results

    # And the demo's own eight, for contrast — the corpus is why it saw nothing.
    for caption in ("减：营业成本（以“-”号填列）", "二、营业总成本（以“-”号填列）", "一、营业总收入",
                    "其中：营业收入", "（二）综合收益总额", "三、营业利润（亏损以“－”号填列）",
                    "1、营业收入", "七、70"):
        assert len({_apply(order, caption) for order in itertools.permutations(SHIPPED)}) == 1


def test_that_order_dependence_is_not_an_overlap_and_is_not_refused():
    """The two patterns never claim the same character, and `spans` is how that is decided.

    This is the whole reason the refusal is span-based rather than result-based: a result-based
    refusal would refuse the rules this file exists to hold.
    """
    probe = "填列） 三、营业利润（亏损以“－”号填列）"
    sign, orphan, prefix = SHIPPED

    assert [probe[s:e] for s, e in orphan.spans(probe)] == ["填列） "]
    assert [probe[s:e] for s, e in sign.spans(probe)] == ["（亏损以“－”号填列）"]
    assert prefix.spans(probe) == [], "the enumerator is hidden behind the fragment"
    # …and once the fragment is gone the enumerator appears, still claiming nobody else's text.
    revealed = orphan.apply(probe)
    assert [revealed[s:e] for s, e in prefix.spans(revealed)] == ["三、"]
    assert orphan.spans(revealed) == []


def test_a_set_records_which_probes_its_declared_order_is_load_bearing_on():
    """Recorded, not raised — the precedent `dangling_families` sets in the same file.

    A set whose order matters is a set someone can break by reordering a list, and nothing else in
    the system would say so.
    """
    st = load_line_item_set(_with_transforms(_seed(), SHIPPED))

    assert st.order_sensitive_probes, "the shipped three ARE order-sensitive; say so"
    joined = " ".join(st.order_sensitive_probes)
    assert "cas_sign_note" in joined and "cas_orphan_head" in joined
    assert "order_sensitive_probes" not in st.model_dump(), "populated at load, never authored"


def test_a_whitespace_only_disagreement_is_not_recorded_as_one():
    """`normalize_label` collapses whitespace after these rules run, so a space is not a finding.

    Pinned because the naive comparison — raw substitution output — reports the SHIPPED rules as
    disagreeing on this probe, and a list of order-sensitive probes is only worth reading if every
    line in it is real.
    """
    sign, _, prefix = SHIPPED
    probe = prefix.witness() + sign.witness()

    assert prefix.apply(sign.apply(probe)) != sign.apply(prefix.apply(probe)), \
        "raw, the two disagree"
    assert _apply([prefix, sign], probe) == _apply([sign, prefix], probe) == "", \
        "folded, they agree"
    vocab = MappingVocabulary(caption_transforms=SHIPPED)
    assert not [line for line in vocab.transform_order_check().revelations if probe in line]


# ── the probe corpus ────────────────────────────────────────────────────────────────────────────

def test_every_shipped_pattern_gets_a_witness_it_actually_matches():
    """The corpus is derived from the patterns, so a witness generator that lies is a check that
    passes on nothing. The verification is in `_witness_of`; this pins it on the real patterns."""
    for t in SHIPPED + [GREEDY_HEAD]:
        witness = t.witness()

        assert witness, t.id
        assert t.matches(witness), f"{t.id}: {witness!r} does not match {t.pattern!r}"


def test_no_shipped_caption_trips_a_cas_pattern_which_is_why_probes_are_synthesised():
    """THE MEASUREMENT BEHIND THE PROBE DESIGN, asserted so it cannot quietly stop being true.

    A rulebook stores what a caption IS; every CAS pattern exists for what a filing PRINTS around
    it. So a corpus of stored aliases exercises the identity transform and none of the strippers,
    and a check built on aliases alone would pass any patterns at all. If this ever fails, the
    rulebook has started carrying printed forms and the corpus gets stronger, not weaker — but the
    reasoning in `transform_probes` would need rereading.
    """
    st = load_line_item_set(_seed())
    captions = st.caption_corpus()

    assert len(captions) > 1900
    assert [c for c in captions if any(t.matches(c) for t in SHIPPED)] == []


def test_the_corpus_grows_with_the_set_it_is_checking():
    """A vocabulary alone has only its patterns; a set contributes its captions too."""
    vocab = MappingVocabulary(caption_transforms=SHIPPED)
    st = load_line_item_set(_seed())

    bare = vocab.transform_probes()
    with_captions = vocab.transform_probes(st.caption_corpus())

    # three witnesses, plus each ordered pairing of them spaced and unspaced: 3 + 3*2*2 = 15
    assert len(bare) == 15, bare
    assert len(with_captions) > 10 * len(bare)
    assert set(bare) <= set(with_captions), "the pattern-derived probes are never dropped"
    assert with_captions[0] != bare[0], "the set's own text is offered to the message first"


def test_a_caption_no_transform_matches_is_left_out_of_the_corpus():
    """Not a sample — a proof. Every transform is a no-op on such a caption, so the string never
    changes and no later transform can start matching either."""
    vocab = MappingVocabulary(caption_transforms=SHIPPED)

    probes = vocab.transform_probes(["Trade and other receivables"])

    assert "Trade and other receivables" not in probes
    assert any("Trade and other receivables" in p for p in probes), \
        "but it must still be offered wearing each witness"


def test_a_caption_a_transform_does_match_reaches_the_corpus_undecorated():
    vocab = MappingVocabulary(caption_transforms=SHIPPED)

    probes = vocab.transform_probes(["其中：营业收入", "Trade receivables"])

    assert probes[0] == "其中：营业收入"


def test_an_overlap_visible_only_on_a_set_caption_is_still_refused():
    """The set-level half of the check, exercised on a caption the set itself declares.

    `LineItemSet` re-runs the check with its own captions in the corpus. Every attempt to build a
    pair that overlaps on a real caption and NOT on a witness glue failed — a witness is by
    construction the material its pattern claims — so the pair below is caught at both levels, and
    `model_construct` is what isolates them: it skips the nested model's validators, and pydantic
    does not re-validate an already-constructed model instance assigned to a field, so the refusal
    this asserts can only have come from `LineItemSet`'s own validator.
    """
    bracket_gloss = CaptionTransform(id="bracket_gloss", pattern=r"[（(][^（()）]{0,8}[）)]",
                                     replacement=" ")
    leading_head = CaptionTransform(id="leading_head", pattern=r"^.*?[）)]\s*", replacement="")
    st = LineItemSet(items=[LineItemDef(key="is_pl__pbt", aliases=["Profit/(loss) before tax"])])

    assert "Profit/(loss) before tax" in st.caption_corpus()
    assert _apply([bracket_gloss, leading_head], "Profit/(loss) before tax") \
        != _apply([leading_head, bracket_gloss], "Profit/(loss) before tax")
    with pytest.raises(ValidationError, match="OVERLAP"):
        LineItemSet(items=st.items,
                    vocabulary=MappingVocabulary.model_construct(
                        caption_transforms=[bracket_gloss, leading_head]))


# ── the fields, and the refusals that are not about order ───────────────────────────────────────

def test_a_transform_pattern_that_does_not_compile_is_refused_where_it_is_written():
    with pytest.raises(ValidationError) as exc:
        CaptionTransform(id="torn", pattern=r"^\s*at\s+(?:1")

    assert "does not compile" in str(exc.value)
    assert "caption_transforms[torn].pattern" in str(exc.value)


def test_a_transform_needs_an_id_because_every_refusal_names_one():
    with pytest.raises(ValidationError, match="needs an `id`"):
        CaptionTransform(id="  ", pattern="x")


def test_two_transforms_may_not_share_an_id():
    """The message would otherwise name a pattern the reader cannot find."""
    with pytest.raises(ValidationError, match="unique"):
        MappingVocabulary(caption_transforms=[
            CaptionTransform(id="dup", pattern="a"), CaptionTransform(id="dup", pattern="b")])


def test_the_replacement_defaults_to_a_space_not_to_nothing():
    """Dropping a stripped middle glues its neighbours into a token neither caption carried.

    Five of the seven substitutions in `normalize_label` use a space; the two that use "" are the
    two anchored at the front, where the leading space is stripped anyway. The safe default is the
    majority one.
    """
    t = CaptionTransform(id="gloss", pattern=r"\s*[（(][^）)]{0,8}[）)]\s*")

    assert t.replacement == " "
    assert t.apply("Revenue (note 7) from contracts") == "Revenue from contracts"


def test_passes_expresses_the_bounded_fixed_point_loop_the_shipped_prefix_rule_needs():
    """A continuation line can carry an enumerator AND a component marker, so one pass is not
    enough — and the bound is what stops a caption of nothing but markers from spinning."""
    one = CaptionTransform(id="prefix1", pattern=mapping._CAS_LINE_PREFIX.pattern,
                           replacement="", count=1, passes=1)
    three = CaptionTransform(id="prefix3", pattern=mapping._CAS_LINE_PREFIX.pattern,
                             replacement="", count=1, passes=3)

    assert one.apply("三、其中：营业收入") == "其中：营业收入"
    assert three.apply("三、其中：营业收入") == "营业收入"


def test_an_unbounded_number_of_passes_is_refused():
    """The number is config; that the loop is bounded at all is code. Eight is already four times
    what the worst real caption needs — a pattern still moving after that is eating one character
    per pass, which is a different bug and should be read as one."""
    with pytest.raises(ValidationError):
        CaptionTransform(id="spin", pattern="a", passes=99)


def test_count_honours_re_sub_semantics_so_spans_can_be_trusted():
    """`spans` reports what one pass would REPLACE, and the overlap test compares those. A
    `count=1` transform that reported every match would conflict over text it never touches."""
    first_only = CaptionTransform(id="first", pattern=r"[（(][^）)]*[）)]", replacement="", count=1)
    every = CaptionTransform(id="every", pattern=r"[（(][^）)]*[）)]", replacement="", count=0)
    text = "Revenue (a) and cost (b)"

    assert len(first_only.spans(text)) == 1
    assert len(every.spans(text)) == 2
    assert first_only.apply(text) == "Revenue  and cost (b)"


def test_a_zero_width_match_claims_no_characters():
    """A pattern that can match empty reports a match at every position while replacing nothing.
    Counting those as claimed characters would make such a transform overlap everything and refuse
    the set over text neither pattern touches."""
    optional = CaptionTransform(id="opt", pattern=r"\s*", replacement=" ")
    boundary = CaptionTransform(id="boundary", pattern=r"\b", replacement="")

    assert optional.spans("abc") == [], "every match here is empty, so none of them is a claim"
    assert optional.spans("a b") == [(1, 2)], "the one that consumes a character is"
    assert boundary.spans("Revenue 收益") == []
    # A pattern that can only ever match empty cannot contend with anything.
    assert MappingVocabulary(
        caption_transforms=[boundary, SHIPPED[0]]).transform_order_check().overlaps == []


def test_a_whitespace_normaliser_declared_as_a_transform_is_refused():
    """FOUND BY THE CHECK, and worth pinning: `\\s*` -> " " overlaps the sign note.

    It looks like the most harmless rule anyone could declare. It is not — `_CAS_SIGN_NOTE`
    matches `号\\s*填列` because the filing breaks the line inside it, so a transform that inserts
    a space between every pair of characters leaves `号 填 列`, which the sign note no longer
    recognises: the parenthetical survives, is folded into word tokens no alias carries, and the
    caption is unmatchable. That is the twenty-three-caption failure on 四创电子 reintroduced by a
    line of configuration, and it is exactly the class of thing that had to be refusable before
    these patterns could be authored at all.
    """
    normaliser = CaptionTransform(id="collapse_space", pattern=r"\s*", replacement=" ")

    with pytest.raises(ValidationError, match="OVERLAP"):
        MappingVocabulary(caption_transforms=[normaliser, SHIPPED[0]])
