"""The caption-normalisation CHARACTER INVENTORIES left Python. The shapes did not.

WHAT THIS FILE IS FOR, and it is one claim above all others: `mapping.build_caption_patterns()`,
run on the built-in inventory, rebuilds all eight caption patterns CHARACTER FOR CHARACTER against
the literals it replaced — sources and flags. That is the only proof strong enough for this
migration. `scripts/parity_normalisation.py` pins the fold of 24,029 captions and is the tripwire,
but it says of itself that its corpus is a floor and not a certificate: its variants are
reverse-engineered from the patterns' own comments, so a printed shape nobody thought of has no
probe. Identical source has no corpus and no gap — two identical regexes accept identical
languages, on strings nobody has imagined yet.

THE SECOND CLAIM is that the migration is not decoration. `demo_code_vs_config.py` section 2 shows
`Land use rights ("LUR")` reaching `bs_nca__land_use_rights` while the same caption printed with a
full-width quote reaches `bs_nca__land` — a different asset, unflagged, and with the quote list
inside a regex there was nowhere to say the mark counts. Here it is a three-element declaration,
and the test watches the caption arrive at the right concept.

THE THIRD is that nothing a declaration can say switches a REFUSAL off. Half of what these
patterns are worth is what they will not touch: `七、70` must stay unmatchable, `b) Trade
receivables` must survive `_CAS_ORPHAN_HEAD`, `Profit/(loss) before tax` must survive
`_ABBREV_GLOSS`. Those live in the SHAPE, and an empty declaration falls back to the built-in
rather than emptying anything — so a set cannot disarm a refusal by declaring nothing, which is
the only way anyone would try.

Run:  ../.venv/Scripts/python.exe -m pytest tests/test_normalisation_vocabulary.py -q -p no:randomly
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import mapping, row_reconstruct
from app.services.han import has_han
from app.services.line_item_matching import LineItemMatcher
from app.services.mapping import build_caption_patterns, label_segments, normalize_label

SEED = pathlib.Path("app/sample/templates/output_csv_hk_line_items.json")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE LITERALS THIS MIGRATION REPLACED, captured from the shipped module before it was touched:
# `(pattern source, flags)` exactly as `re.compile` reported them. Transcribed by machine and not
# by hand — the point of the comparison is destroyed by a typo in the thing being compared to.
#
# Flags are in here because they are half of what a pattern means: 34 is IGNORECASE|UNICODE and 96
# is VERBOSE|UNICODE, and a rebuilt `_ABBREV_GLOSS` compiled without VERBOSE matches a bracket
# followed by literal spaces and the word "an opening bracket". It would still be a pattern, still
# compile, and never fire.
#
# Two orders of the same set appear on purpose and are preserved: the four Latin-lineage patterns
# spell the bracket inventory `[(（]` and the colon inventory `[:：]`, the three CAS ones spell them
# `[（(]` and `[：:]`. No regex engine can tell those apart. The SOURCE can, which is why
# `_placeholders` renders one inventory two ways instead of picking a winner and calling the
# difference cosmetic.
SHIPPED = {
    "_ABBREV_GLOSS":          ('[(（]\\s*                     # an opening bracket, either width\n        ["\'“”‘’「」『』《》]\\s*        # …whose content opens with a quotation mark\n        [^)）]*?                      # the abbreviation itself, never crossing the bracket\n        \\s*["\'“”‘’「」『』《》]\\s*     # …and closes with one\n        [)）]',
                               96),
    "_NOTE_CITATION":         ('[(（]\\s*(?:notes?|附註|附注)\\s*\\.?\\s*\\d{1,3}(?!\\d)[a-z]?(?:\\s*[(（][a-z0-9]{1,3}[)）])?\\s*[)）]|(?:附註|附注)\\s*\\d{1,3}(?!\\d)|^\\s*notes?\\s*\\.?\\s*\\d{1,3}(?!\\d)[a-z]?\\s*[:：]|[(（]\\s*(?:notes?|附註|附注)\\s*$',
                               34),
    "_BRACKETED_NUMBER":      ('[(（]\\s*\\d{1,4}\\s*[)）]',
                               32),
    "_TRAILING_NUMERIC_NOTE": ('\\b\\d{1,3}\\s*[(（][a-z0-9]{1,3}[)）]\\s*$',
                               34),
    "_CAS_LINE_PREFIX":       ('^\\s*(?:[一二三四五六七八九十]+\\s*[、.]\\s*(?![0-9０-９])|\\d{1,2}\\s*、\\s*(?![0-9０-９])|[（(]\\s*[一二三四五六七八九十]{1,3}\\s*[）)]|其\\s*中\\s*[：:]|[加减]\\s*[：:])\\s*',
                               32),
    "_CAS_SIGN_NOTE":         ('[（(][^（()）]{0,24}号\\s*填列\\s*[）)]',
                               32),
    "_CAS_ORPHAN_HEAD":       ('^[^（(]*?[㐀-䶿一-鿿][^（(]{0,10}[）)]\\s*',
                               32),
    "_HAN_RUN":               ('[㐀-䶿一-鿿豈-\ufaff]+(?:\\s*[㐀-䶿一-鿿豈-\ufaff]+)*',
                               32),
}

# Which attribute of the built pattern set each shipped name became.
FIELDS = {
    "_ABBREV_GLOSS": "abbrev_gloss",
    "_NOTE_CITATION": "note_citation",
    "_BRACKETED_NUMBER": "bracketed_number",
    "_TRAILING_NUMERIC_NOTE": "trailing_numeric_note",
    "_CAS_LINE_PREFIX": "cas_line_prefix",
    "_CAS_SIGN_NOTE": "cas_sign_note",
    "_CAS_ORPHAN_HEAD": "cas_orphan_head",
    "_HAN_RUN": "han_run",
}


@pytest.mark.parametrize("name", list(SHIPPED))
def test_rebuilt_source_is_the_shipped_literal_character_for_character(name):
    """The claim the whole migration rests on, one pattern at a time.

    Parametrised rather than looped so a failure names the pattern in the test id: eight of these
    go red together the moment `_placeholders` renders a class in a different order, and "one
    assert failed somewhere in a for loop" is how that gets mistaken for a real behaviour change.
    """
    source, flags = SHIPPED[name]
    built = getattr(build_caption_patterns(), FIELDS[name])
    assert built.pattern == source
    assert built.flags == flags


def test_the_module_names_are_the_fold_the_module_runs():
    """The eight `_NAME` constants are the SAME objects the fold uses, not copies of them.

    Two consumers reach for them by name — `tests/test_pattern_overlap.py` builds a
    `CaptionTransform` from each CAS pattern's `.pattern`, `scripts/demo_code_vs_config.py` prints
    two of them to argue the disjointness is designed in. If those names ever became a second
    compiled set, both would be reasoning about a pattern the fold does not run, which is the
    "two answers to one question" this migration exists to end.
    """
    for name, field in FIELDS.items():
        assert getattr(mapping, name) is getattr(mapping._CAPTION_PATTERNS, field)


def test_the_note_citation_duplicate_is_still_byte_identical():
    """`row_reconstruct._NOTE_MARKER` is a deliberate duplicate, and this is the only thing
    watching it.

    `mapping._NOTE_CITATION`'s own comment says why the copy exists (row_reconstruct imports this
    module, so this one cannot import back) and states the obligation: "a note reference is one
    shape and both copies must recognise it, so a change to either belongs in both until then".
    `scripts/parity_normalisation.py` names this as its sharpest gap — it holds mapping's copy and
    is completely silent about row_reconstruct's, so the two can drift with a green run there.

    Pinned HERE because this is the change that could have broken it: `_NOTE_CITATION` is now
    built from an inventory and its twin is still a literal, so the two are one declaration away
    from disagreeing. Byte equality and not behavioural, because that is what the obligation says
    — the shared home both should import is still the right eventual fix.
    """
    assert row_reconstruct._NOTE_MARKER.pattern == mapping._NOTE_CITATION.pattern
    assert row_reconstruct._NOTE_MARKER.flags == mapping._NOTE_CITATION.flags


# ══════════════════════════════════════════════════════════════════════════════════════════════
# EMPTY MEANS THE BUILT-IN


@pytest.mark.parametrize("declared", [
    None,
    {},
    {"quote_marks": []},                                  # an entry declared empty
    {"brackets": [], "digit_ranges": [], "han_ranges": []},   # several at once
])
def test_an_absent_or_empty_entry_falls_back_to_the_builtin(declared):
    """The same rule `line_item_matching.Vocabulary` already applies to the nine migrated tables.

    IT IS ALSO THE REFUSAL. `digit_ranges` is what `_CAS_LINE_PREFIX`'s lookahead consumes, and
    that lookahead is the reason a note reference printed as its row's whole label — 七、70 —
    stays unmatchable instead of becoming a bare "70" the rulebook might place. If declaring the
    entry empty EMPTIED it, `(?![])` would be the way to disarm that, so "empty declares nothing"
    is the difference between a configurable vocabulary and a configurable refusal.
    """
    built = build_caption_patterns(declared)
    for name, field in FIELDS.items():
        assert getattr(built, field).pattern == SHIPPED[name][0]
    assert normalize_label("七、70", built) == "七 70"


def test_an_entry_no_pattern_reads_is_refused():
    """Declared config that nothing consults reads as a control, which is the objection this
    migration answers — so it arrives from the other side too and is refused, not ignored.

    Pydantic ignores unknown keys, which is right for a document format and wrong here: a
    `quotemarks` that silently does nothing looks in the file exactly like a `quote_marks` that
    works, and the difference only shows up as a figure on the wrong line.
    """
    with pytest.raises(ValueError, match="not read by any caption pattern"):
        build_caption_patterns({"quotemarks": ['"']})


@pytest.mark.parametrize("bad, match", [
    ({"quote_marks": ["]"]}, "cannot appear unescaped"),
    ({"quote_marks": ["-"]}, "cannot appear unescaped"),
    ({"quote_marks": ["\\"]}, "cannot appear unescaped"),
    ({"cas_enumerators": ["一二"]}, "not a single character"),
    ({"han_ranges": [["FAFF", "3400"]]}, "runs backwards"),
    ({"han_ranges": [["not-hex", "3400"]]}, "hex codepoints"),
    ({"brackets": [["("]]}, r"\[open, close\] pair"),
])
def test_a_declaration_that_would_change_the_SHAPE_is_refused(bad, match):
    """An inventory entry may add characters. It may not restructure the pattern around them.

    `]`, `\\`, `^` and `-` are the four characters that mean something inside `[...]`, and a
    declaration carrying one would either break the compile or — worse — quietly widen the class
    into a range. They are refused rather than escaped because escaping changes the built SOURCE,
    and identical source is the guarantee the first test in this file buys.
    """
    with pytest.raises(ValueError, match=match):
        build_caption_patterns(bad)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE MIGRATION IS NOT DECORATION: the demo's unflagged wrong asset, fixed by three list entries


@pytest.fixture(scope="module")
def matcher() -> LineItemMatcher:
    return LineItemMatcher(load_line_item_set(json.loads(SEED.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("mark_open, mark_close, what", [
    ("＂", "＂", "full-width straight quote"),
    ("﹁", "﹂", "CJK vertical corner bracket"),
])
def test_a_quote_mark_the_inventory_did_not_carry_put_the_figure_on_another_asset(
        matcher, mark_open, mark_close, what):
    """The measured case from `demo_code_vs_config.py` section 2, now with the fix beside it.

    `Land use rights ("LUR")` folds to 'land use rights' and hits the alias at the EXACT tier,
    confidence 1.0. The same caption with a mark the built-in list does not carry folds to 'land
    use rights lur', misses every alias, and is placed by the RULE tier on `bs_nca__land` — a
    different asset, and both non-current, so the balance sheet still ties.

    IT IS FLAGGED, AND THE BRIEF FOR THIS WORK SAID IT WAS NOT — measured on both matchers, the
    caption comes back `needs_review=True` at confidence 0.6 with reason "several rule hints
    fired; ambiguous", below the 0.85 auto-accept. So the failure this demonstrates is a REVIEW
    QUEUE ENTRY, not a silent wrong asset, and that is worth having straight: what flagged it was
    the ambiguity check noticing two hints, not anything knowing a quote mark had been missed.
    Fold `Land use rights (＂LUR＂)` in a section where only one rule hint fires and the same miss
    is unflagged; the asserted claim here is only the one that was actually run.

    Declaring the mark makes the two folds identical and the caption arrives where it belongs, at
    EXACT and unflagged. Three list entries, no release. That is the argument for moving the
    inventory, and the number behind it is the negative control in
    `scripts/parity_normalisation.py`: with this one pattern disabled, 1,050 of 1,993 rulebook
    captions fold differently and 74 resolve onto a DIFFERENT concept.
    """
    ascii_caption = 'Land use rights ("LUR")'
    printed = f"Land use rights ({mark_open}LUR{mark_close})"
    declared = build_caption_patterns({
        "quote_marks": list(mapping._BUILTIN_CAPTION_INVENTORY["quote_marks"])
                       + [mark_open, mark_close]})

    assert normalize_label(ascii_caption) == "land use rights"
    right = matcher.match(ascii_caption, "balance_sheet", "NON-CURRENT ASSETS")
    assert (right.key, right.needs_review) == ("bs_nca__land_use_rights", False)

    # As shipped: the mark is not vocabulary, so the abbreviation survives as a word.
    assert normalize_label(printed) == "land use rights lur"
    wrong = matcher.match(printed, "balance_sheet", "NON-CURRENT ASSETS")
    assert wrong.key == "bs_nca__land"                    # a different asset…
    assert wrong.needs_review                             # …and it does reach a reviewer
    # The per-item accept bar this used to compare against has been REMOVED: it was read by
    # nothing, the incumbent included, whose accept decisions all use the global
    # `extraction.auto_accept_confidence` (0.80). The claim that survives is the one that
    # matters — the wrong answer arrives below the exact tier's certainty, so it is not accepted
    # as an identity the way the correctly-folded caption is.
    assert wrong.confidence < right.confidence

    # Declared: the same fold as the ASCII caption, the same concept, and no review.
    assert normalize_label(printed, declared) == normalize_label(ascii_caption)
    fixed = matcher.match(normalize_label(printed, declared), "balance_sheet",
                          "NON-CURRENT ASSETS")
    assert (fixed.key, fixed.needs_review) == ("bs_nca__land_use_rights", False)


def test_the_latin_note_word_is_stored_singular_and_the_template_pluralises():
    """`note_word_latin` holds "note"; `_NOTE_CITATION` accepts "notes".

    The split is deliberate and it is the one this codebase makes everywhere: the WORD is a fact
    about the printed page, the `s?` is English morphology and belongs to the mechanism. A set
    forced to declare both spellings would eventually declare one, and a caption citing "(memos
    12)" would then keep a fabricated token — the same defect the quote mark above causes.
    """
    both = build_caption_patterns({"note_word_latin": ["note", "memo"]})
    assert normalize_label("Trade receivables (memo 12)") == "trade receivables memo 12"
    assert normalize_label("Trade receivables (memo 12)", both) == "trade receivables"
    assert normalize_label("Trade receivables (memos 12)", both) == "trade receivables"
    assert normalize_label("Trade receivables (note 12)", both) == "trade receivables"


def test_a_component_marker_can_be_added_and_row_reconstruct_already_knows_one_we_do_not():
    """`cas_component_markers` is authorable — and there is a fourth marker already in the repo.

    `row_reconstruct._cas_face_caption` strips `^(?:其中|加|减|其他)?[:：]` before comparing a
    printed CAS line to the face-caption list. `_CAS_LINE_PREFIX` carries three of those four:
    其他：营业收入 folds to '其他 营业收入' here and to '营业收入' there, so one module reads that
    line as a component of the total above it and the other reads 其他 as part of the concept's
    name. Two answers to one question, in the same pipeline.

    NOT FIXED HERE, deliberately: adding 其他 changes the fold of a real caption class, which
    `scripts/parity_normalisation.py` would report and someone has to measure against a mainland
    filing — and `row_reconstruct.py` is not this package's file. What this test does is prove the
    fix is now a one-entry edit rather than a release, and pin the disagreement so it cannot be
    discovered twice.
    """
    assert normalize_label("其他：营业收入") == "其他 营业收入"
    wider = build_caption_patterns({
        "cas_component_markers": list(mapping._BUILTIN_CAPTION_INVENTORY[
            "cas_component_markers"]) + ["其他"]})
    assert normalize_label("其他：营业收入", wider) == "营业收入"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE REFUSALS ARE IN THE SHAPE, AND THE SHAPE DID NOT MOVE


@pytest.mark.parametrize("caption, folded", [
    # `_CAS_LINE_PREFIX`'s digit lookahead: a note reference that arrived as a row's whole label
    # must stay unmatchable, never become a bare "70" the rulebook could plausibly place.
    ("七、70", "七 70"),
    # `_CAS_ORPHAN_HEAD` requires a Han character, which keeps it off the English path entirely.
    ("b) Trade receivables", "b trade receivables"),
    # `_ABBREV_GLOSS` requires QUOTES: a parenthetical that carries meaning is not a gloss.
    ("Profit/(loss) before tax", "profit loss before tax"),
    ("Credited/(charged) to profit or loss during the year",
     "credited charged to profit or loss during the year"),
    # `_NOTE_CITATION`'s brackets and its `(?!\d)`: a four-digit year is not a note number, and
    # half of one is not either — the version without this cap turned "Senior notes 2025" into
    # "senior 5", deleting the head noun the rulebook carries four aliases for.
    ("Senior notes 2025", "senior notes 2025"),
    # A leading parenthetical `_CAS_ORPHAN_HEAD` must not read as an orphaned wrap fragment.
    ("（一）综合收益总额", "综合收益总额"),
])
def test_the_refusals_survive_a_widened_inventory(caption, folded):
    """Every character inventory widened as far as it can be widened, on the captions the
    patterns exist to LEAVE ALONE.

    A vocabulary that can turn a refusal off is not a vocabulary, it is a switch. These fold the
    same through the built-in inventory and through one carrying every mark, bracket, delimiter
    and marker the shipped patterns know plus the ones a filing might print — because what refuses
    them is the shape: an anchor, a lookahead, a required quote, a required Han character.
    """
    inv = mapping._BUILTIN_CAPTION_INVENTORY
    widened = build_caption_patterns({
        "quote_marks": list(inv["quote_marks"]) + ["＂", "﹁", "﹂"],
        "brackets": list(inv["brackets"]) + [["［", "］"]],
        "colon_marks": list(inv["colon_marks"]) + ["﹕"],
        "note_word_latin": ["note", "memo"],
        "note_word_han": list(inv["note_word_han"]) + ["註"],
        "cas_component_markers": list(inv["cas_component_markers"]) + ["其他"],
        "cas_enumerator_delimiters": list(inv["cas_enumerator_delimiters"]) + ["．"],
    })
    assert normalize_label(caption) == folded
    assert normalize_label(caption, widened) == folded


def test_the_sequence_and_the_loop_bound_are_still_code():
    """The stacked caption the bounded prefix loop exists for folds identically either way.

    `normalize_label`'s comment records the order that matters and why (the sign note first,
    because removing it is what exposes the orphaned tail; the tail next, because it sits in FRONT
    of the enumerator the prefix rule is anchored to) and the loop is bounded at three so a
    caption of nothing but markers cannot spin. None of that is in the inventory, and a
    declaration cannot reach it: this probe carries a wrap fragment, a sign note, an enumerator
    and a component marker on one line and comes out the same.
    """
    stacked = "填列） 三、营业利润（亏损以“－”号填列） 其中：营业收入"
    declared = build_caption_patterns({"cas_component_markers": ["其中", "加", "减", "其他"]})
    assert normalize_label(stacked) == "营业利润 其中 营业收入"
    assert normalize_label(stacked, declared) == normalize_label(stacked)


def test_passing_the_builtin_patterns_explicitly_is_the_same_as_passing_nothing():
    """The default argument is the built-in set, over a sample of every corpus kind.

    Forty-odd call sites across the stages, the services and the scripts call `normalize_label`
    with one argument, and `line_item_matching` opens its docstring refusing to reimplement the
    fold. If the parameterised path and the default path could disagree, that refusal would be
    worth nothing.
    """
    built = build_caption_patterns()
    for caption in ('Land use rights ("LUR")', "Deferred tax credited for the year (note 32)",
                    "Right-of-use assets 16(a)", "Cost of sales 銷售成本",
                    "三、营业利润（亏损以“－”号填列）", "其中：营业收入", "（二）综合收益总额",
                    "填列） 三、营业利润", "受限制现金（附註(a)）", "Senior notes 2025", ""):
        assert normalize_label(caption, built) == normalize_label(caption)
        assert label_segments(caption, built) == label_segments(caption)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# WHAT WRITING THE HAN RANGES DOWN MADE VISIBLE


def test_the_han_run_range_was_corrected_from_the_range_nfc_left_behind():
    """The bug this used to PIN, now asserted as fixed — and its own docstring predicted the flip.

    WHAT WAS WRONG. `_HAN_RUN`'s third range read `豈-﫿`. `豈` reads as U+F900 (CJK COMPATIBILITY
    IDEOGRAPH-F900) and IS U+8C48 — F900's canonical decomposition, i.e. exactly what an NFC pass
    over the source file leaves behind. So the range was 8C48-FAFF: it began in the middle of the
    main CJK block it already covered and ran through the HANGUL syllables (AC00-D7AF) and the
    PRIVATE USE AREA (E000-F8FF) to reach FAFF. Two copies of one intended inventory were
    different SETS — `han._CJK` carried F900 intact, so `has_han("한")` was False while
    `_HAN_RUN.search("한")` was True and the two modules disagreed about what Chinese is.

    IT COST A REAL SPLIT. A caption mixing Hangul with Han lost its per-script split, because the
    Han run ate the Hangul and left no other half — the third assertion below is that split
    working now.

    UNREACHABLE ON THE SHIPPED CORPUS, which is why the fix was safe to take:
    `scripts/parity_normalisation.py` reports 0 of 24,029 captions changed. The correction is a
    four-character edit to one inventory entry, and it is now data rather than a literal.
    """
    assert mapping._HAN_RUN.search("한") is None          # U+D55C, a Hangul syllable
    assert mapping._HAN_RUN.search("") is None      # private use
    assert mapping._HAN_RUN.search("豈") is not None   # the compatibility ideograph meant

    # The two modules now agree about what Chinese is, which is the point.
    for ch in ("한", "", "豈", "营", "一"):
        assert bool(mapping._HAN_RUN.search(ch)) == has_han(ch), f"disagreement on {ch!r}"

    # And the split the bug was eating.
    assert label_segments("매출 销售成本") == ["매출 销售成本", "매출", "销售成本"]

    assert mapping._BUILTIN_CAPTION_INVENTORY["han_ranges"] == [
        ["3400", "4DBF"], ["4E00", "9FFF"], ["F900", "FAFF"]]
    # And the narrower inventory the orphan-head refusal asks for is still its own entry.
    assert mapping._BUILTIN_CAPTION_INVENTORY["han_ranges_required"] == [
        ["3400", "4DBF"], ["4E00", "9FFF"]]



# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE SEED CARRIES THE INVENTORY, AND WHAT IT CARRIES IS WHAT THE FOLD RUNS


def test_the_emitted_block_survives_json_and_rebuilds_the_shipped_fold():
    """What `scripts/build_line_items.py` writes has to be loadable BY THE FOLD, not merely valid
    JSON — so the emit is round-tripped through `json` and fed back to the builder.

    THIS IS THE MISTAKE THE ROUND TRIP CATCHES, and it is not hypothetical: emitting the Han
    ranges as the CHARACTERS they name rather than as codepoints is exactly how the U+8C48
    corruption below got into the shipped literal in the first place. Such a file reads fine, loads
    fine, and rebuilds a different pattern. Tuples are the other one — `json` renders them as
    arrays and the reload gives lists, so a builder that emitted pairs as tuples would pass in
    memory and fail from disk.
    """
    emitted = json.loads(json.dumps(
        {k: list(v) for k, v in mapping._BUILTIN_CAPTION_INVENTORY.items()}, ensure_ascii=False))
    built = build_caption_patterns(emitted)
    for name, field in FIELDS.items():
        assert getattr(built, field).pattern == SHIPPED[name][0]
        assert getattr(built, field).flags == SHIPPED[name][1]
    # F900, since the NFC corruption this comment warns about has now been corrected in the
    # inventory. The point the assertion makes is unchanged: the block must survive JSON as
    # CODEPOINT STRINGS, because writing the ranges as the characters they name is how U+F900
    # became U+8C48 in the first place.
    assert emitted["han_ranges"] == [["3400", "4DBF"], ["4E00", "9FFF"], ["F900", "FAFF"]]


def test_the_seed_carries_the_inventory_once_the_builder_has_been_re_run():
    """The seed is `build_line_items.py`'s OUTPUT and not this package's file, so it is checked
    where it stands rather than rewritten here.

    `MappingVocabulary` declares no `caption_characters` field yet, so even a regenerated seed has
    the block dropped on load — which is why this reads the raw JSON, and why the builder prints
    that state on every run instead of leaving it to be discovered. When the field lands and the
    builder has been re-run, this stops skipping; the assertion it then makes is that the file the
    matcher loads and the fold the matcher runs carry the same inventory.
    """
    raw = json.loads(SEED.read_text(encoding="utf-8"))
    block = (raw.get("vocabulary") or {}).get("caption_characters")
    if block is None:
        pytest.skip("seed predates the caption inventory — re-run scripts/build_line_items.py")
    assert block == {k: list(v) for k, v in mapping._BUILTIN_CAPTION_INVENTORY.items()}
    built = build_caption_patterns(block)
    for name, field in FIELDS.items():
        assert getattr(built, field).pattern == SHIPPED[name][0]
