"""THE IND AS SET IS A SECOND GAAP ON THE SAME SPINE, and these are the properties that make it one.

NEW FILE -> backend/tests/test_the_ind_as_set.py

`output_csv_indas_line_items.json` is derived from the shipped HK set: identical concept keys, with
the captions an Indian filing prints under Schedule III Division II added alongside the IFRS/HKFRS
spellings rather than replacing them. That is the whole design claim, and each assertion here is one
half of it — the spine did not move, and the vocabulary did.

WHY A TEST AND NOT A README. A configuration file that nothing reads is the defect this codebase
keeps finding, at every scale; a derived file is worse, because it looks maintained while drifting
from the set it was derived from. These assertions fail the moment the two sets stop sharing a spine.

WHAT IS DELIBERATELY NOT ASSERTED: that any figure is extracted correctly from an Indian filing.
There are no Indian filings in `_filings/`, so nothing here measures extraction — see the set's own
`metadata.changes`, which records that the inherited `note_source` patterns were measured against a
twelve-filing HK/PRC corpus and against no Indian one.
"""
from __future__ import annotations

import json
import pathlib
import re
from decimal import Decimal

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

#: Any Han character. A template for Indian entities carries none in the vocabulary that steers
#: either route — `_HAN` is what several assertions below read.
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN = re.compile(r"[A-Za-z]{3,}")


def _dead_here(value) -> bool:
    """Whether a declared value can NEVER match a caption on an English Indian filing.

    PURE HAN ONLY, and the qualification is the whole correctness of the clean-up this file
    measures. 120 of the patterns carrying Han are ALTERNATIONS whose other branches are English,
    so removing one takes the English with it — and on an exclusion that WIDENS what is accepted.
    The rule here is deliberately the same one the seed was swept with, because a test using a
    looser predicate than the edit it checks cannot catch that edit going too far.
    """
    text = str(value)
    return bool(_HAN.search(text)) and not _LATIN.search(text)

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
INDAS = TEMPLATES / "output_csv_indas_line_items.json"
HK = TEMPLATES / "output_csv_hk_line_items.json"

BS, PL = "balance_sheet", "profit_and_loss"

# The Division II face, as Schedule III prints it. Not a sample: the balance sheet's own line
# sequence and the statement of profit and loss's, in order.
SCHEDULE_III = [
    # THE FIVE SCHEDULE III LINES THAT WERE NEVER EXERCISED. One of them now binds; the other
    # four are listed in `UNPLACED_BY_DESIGN` with the reason, so the boundary of this ontology is
    # a tested statement rather than an absence nobody wrote down.
    ("Depreciation and Amortisation Expense", PL, "is_pl"),
    ("Other Financial Liabilities", BS, "bs_cl"),
    ("Total Income", PL, "is_pl"),
    ("Total Expenses", PL, "is_pl"),
    ("Earnings per equity share", PL, "is_pl"),
    ("Property, Plant and Equipment", BS, "bs_nca"),
    ("Capital work-in-progress", BS, "bs_nca"),
    ("Other Intangible assets", BS, "bs_nca"),
    ("Intangible assets under development", BS, "bs_nca"),
    ("Investment Property", BS, "bs_nca"),
    ("Goodwill", BS, "bs_nca"),
    ("Investments in subsidiaries", BS, "bs_nca"),
    ("Investments in associates", BS, "bs_nca"),
    ("Investments in joint ventures", BS, "bs_nca"),
    ("Right-of-use assets", BS, "bs_nca"),
    ("Deferred tax assets (net)", BS, "bs_nca"),
    ("Other non-current assets", BS, "bs_nca"),
    ("Inventories", BS, "bs_ca"),
    ("Trade receivables", BS, "bs_ca"),
    ("Cash and cash equivalents", BS, "bs_ca"),
    ("Bank balances other than cash and cash equivalents", BS, "bs_ca"),
    ("Other financial assets", BS, "bs_ca"),
    ("Other current assets", BS, "bs_ca"),
    ("Current Tax Assets (Net)", BS, "bs_ca"),
    ("Assets classified as held for sale", BS, "bs_ca"),
    ("Equity share capital", BS, "bs_equity"),
    ("Other Equity", BS, "bs_equity"),
    ("Securities premium", BS, "bs_equity"),
    ("Retained earnings", BS, "bs_equity"),
    ("General reserve", BS, "bs_equity"),
    ("Non-controlling interests", BS, "bs_equity"),
    ("Borrowings", BS, "bs_ncl"),
    ("Lease liabilities", BS, "bs_ncl"),
    ("Deferred tax liabilities (net)", BS, "bs_ncl"),
    ("Provisions", BS, "bs_ncl"),
    ("Other non-current liabilities", BS, "bs_ncl"),
    ("Trade payables", BS, "bs_cl"),
    ("Total outstanding dues of micro enterprises and small enterprises", BS, "bs_cl"),
    ("Total outstanding dues of creditors other than micro enterprises and small enterprises",
     BS, "bs_cl"),
    ("Contract liabilities", BS, "bs_cl"),
    ("Current Tax Liabilities (Net)", BS, "bs_cl"),
    ("Other current liabilities", BS, "bs_cl"),
    ("Revenue from operations", PL, None),
    ("Other income", PL, None),
    ("Cost of materials consumed", PL, None),
    ("Purchases of stock-in-trade", PL, None),
    ("Changes in inventories of finished goods, work-in-progress and stock-in-trade", PL, None),
    ("Employee benefits expense", PL, None),
    ("Finance costs", PL, None),
    ("Other expenses", PL, None),
    ("Exceptional items", PL, None),
    ("Profit before tax", PL, None),
    ("Current tax", PL, None),
    ("Deferred tax", PL, None),
    ("Tax expense", PL, None),
    ("Profit for the year", PL, None),
    ("Share of profit of associates and joint ventures", PL, None),
    ("Other comprehensive income", PL, None),
]


def _set(path: pathlib.Path):
    return load_line_item_set(json.loads(path.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def indas():
    return _set(INDAS)


@pytest.fixture(scope="module")
def matcher(indas):
    return OntologyMatcher(build_working_view(indas), locale="en", settings=get_settings())


def test_it_declares_its_own_keys_and_targets_its_own_template(indas):
    """A set that reused the HK keys would be superseded by the HK file on the next restart —
    `sample.reference` holds the database to the file for a shipped key."""
    assert indas.line_items_key == "output_csv_indas"
    assert indas.target_template_key == "output_csv_indas_v1"


def _template() -> dict:
    return json.loads(
        (TEMPLATES / "output_csv_indas_v1_template.json").read_text(encoding="utf-8"))


def _canonical_keys(node, out: list[str]) -> None:
    if isinstance(node, dict):
        key = node.get("canonical_key")
        if isinstance(key, str) and key:
            out.append(key)
        for value in node.values():
            _canonical_keys(value, out)
    elif isinstance(node, list):
        for value in node:
            _canonical_keys(value, out)


def test_the_template_it_targets_exists_and_declares_that_key():
    assert _template()["template_key"] == "output_csv_indas_v1"


def test_the_template_declares_no_row_its_set_cannot_fill(indas):
    """THE DEFECT THIS SET SHIPPED WITH, and the one that made it badly shaped.

    The template drives the output's SHAPE (`services.statement_rows` emits its order faithfully),
    so a row naming a concept the configuration does not have is a column that can never be filled.
    The set was scoped from 549 items to 270 and the template was left as the HK one with a new key:
    288 of its 480 rows named a scoped-out concept, including two entire statements — Statement
    Setup & Controls and Covenants & Supplemental Data — whose every item had been deleted.

    Asserted as a SUBSET rather than equality: a template may legitimately omit a concept the set
    carries (a part is not a presentation row), but it may never name one the set lacks.
    """
    refs: list[str] = []
    _canonical_keys(_template().get("statements"), refs)
    item_keys = {i.key for i in indas.items}
    sections = set(indas.section_defaults or ()) | set(
        (getattr(indas.vocabulary, "scope_tokens", None) or {}))
    unfillable = sorted({r for r in refs if r not in item_keys and r not in sections})
    assert not unfillable, unfillable


def test_the_template_has_no_empty_section_or_statement():
    """A heading with nothing under it is the same defect one level up.

    The first fix for the row problem tested every node's key against the set, which meant allowing
    section keys — and that made a section unconditionally keepable, so five empty sections and two
    zero-row statements survived. A group is its children; that is what this asserts.
    """
    empty = []
    for statement in _template().get("statements") or ():
        sections = statement.get("sections") or []
        if not sections:
            empty.append(statement.get("label"))
        for section in sections:
            if not (section.get("children") or []):
                empty.append(section.get("canonical_key"))
    assert not empty, empty


def test_the_template_carries_no_chinese_labels():
    """An Indian template captioned 資產負債表 is the HK template wearing a new key."""
    blob = json.dumps(_template(), ensure_ascii=False)
    assert '"zh"' not in blob
    assert not re.search(r"[一-鿿]", blob)


def test_the_prompt_is_written_for_an_indian_filing(indas):
    """THE ARTEFACT THAT ACTIVELY MISLED. It travels on every mapping call, and it read "these are
    HKEX / PRC filings" in the set named for India — the wrong jurisdiction, asserted as an
    instruction."""
    prompt = indas.prompt or ""
    assert prompt, "a configuration with no master prompt sends the model nothing about the filing"
    for wrong in ("HKEX", "PRC", "Hong Kong", "HKFRS"):
        assert wrong.lower() not in prompt.lower(), wrong
    assert "Ind AS" in prompt
    assert "Schedule III" in prompt


def test_no_pure_han_pattern_survives_in_a_positive_list(indas):
    """DEAD WEIGHT, and only the dead weight.

    A pattern that is entirely Han cannot match an English filing, so in an Ind AS set it is a
    declaration that can never fire. A MIXED pattern is different — 120 of the 155 carrying Han are
    alternations whose other branches are English (`fixed\\s+assets?|固定資產|固定资产`) — and those
    stay, because editing inside an alternation to remove one branch is regex surgery whose failure
    mode is a silent hole.
    """
    han, latin = re.compile(r"[一-鿿]"), re.compile(r"[A-Za-z]{3,}")
    offenders = []
    for item in indas.items:
        source = item.note_source
        if source is None:
            continue
        for field in ("note_title_any", "row_caption_any"):
            for pattern in (getattr(source, field, None) or ()):
                if han.search(pattern) and not latin.search(pattern):
                    offenders.append(f"{item.key}.{field}: {pattern[:40]}")
    assert not offenders, offenders[:10]


def test_no_veto_that_could_ever_fire_was_narrowed(indas):
    """THE ASYMMETRY THAT MATTERS, restated now that the Chinese vocabulary is gone.

    `note_title_any` and `row_caption_any` SELECT, so dropping one can only narrow what is read.
    `row_caption_none` and `row_terms_none` VETO, so dropping one WIDENS what is accepted — which
    is how a movement row or an allowance column gets taken for a balance. 8,655 veto values were
    removed from this set and NOT ONE of them could have fired: every one is pure Han, and a Han
    veto cannot match a caption on an English Indian filing.

    So the invariant is not "the veto lists are identical to the HK set's" — they are not, and
    keeping them so kept 8,655 dead declarations in a template for Indian entities. It is that
    every veto which could EVER match an Indian caption is still there, character for character.
    """
    hk = {i.key: i for i in _set(HK).items}
    checked = 0
    for item in indas.items:
        mine, theirs = item.note_source, hk[item.key].note_source
        if mine is None or theirs is None:
            continue
        for field in ("row_caption_none", "row_terms_none"):
            survivors = [v for v in (getattr(theirs, field) or ()) if not _dead_here(v)]
            assert list(getattr(mine, field) or ()) == survivors, (
                f"{item.key}.{field}: a veto that can match an English caption was narrowed")
            checked += len(survivors)
    assert checked, "no veto survived at all, so this asserts nothing"


def test_every_removed_veto_was_unable_to_match_an_english_caption(indas):
    """THE OTHER HALF, and the one that makes the removal a measurement rather than a claim.

    Counted rather than sampled: every value the HK set declares and this set does not must contain
    Han and no Latin word, so `note_sourced`'s `re.search` against a caption of an English filing
    could never have returned a match.
    """
    hk = {i.key: i for i in _set(HK).items}
    latin = re.compile(r"[A-Za-z]{3,}")
    removed = 0
    for item in indas.items:
        mine, theirs = item.note_source, hk[item.key].note_source
        if mine is None or theirs is None:
            continue
        for field in ("row_caption_none", "row_terms_none", "row_terms", "row_caption_any"):
            gone = set(getattr(theirs, field) or ()) - set(getattr(mine, field) or ())
            for value in gone:
                assert _dead_here(value), (
                    f"{item.key}.{field}: {value!r} was removed but could have matched English")
                removed += 1
    # 6,951 since the securities parts' long veto lists were replaced by four vetoes each: those
    # values are gone from the HK set too, so they are no longer removals here.
    # NOT A MAGNITUDE. The HK set's families are being moved to four-rule row vocabularies, so the
    # Chinese-only vetoes there are to strip here fall with every family converted; each removal
    # is still asserted dead above, which is the property this test exists for.
    assert removed > 0, removed


def test_every_concept_is_one_of_the_hk_spine_s(indas):
    """THE DESIGN CLAIM, and it is a SUBSET rather than an equality.

    One engine reads both GAAPs because the concept KEYS are shared and only the captions differ,
    so a figure extracted under Ind AS is comparable with one extracted under HKFRS. But the sets
    are not the same SIZE: Schedule III Division II presents about forty-eight balance-sheet lines
    and twenty in the statement of profit and loss, where the HK spine carries a hundred and twelve
    and eighty-seven — the difference being analytical decomposition an Indian filing does not
    print. So this set is scoped to what Schedule III presents.

    A key that is NOT in the HK spine would be a fork — a new concept nobody can compare across
    GAAPs — and that is what this catches. Scoping is selection; inventing keys is not.
    """
    hk_keys = {i.key for i in _set(HK).items}
    indas_keys = {i.key for i in indas.items}
    assert indas_keys <= hk_keys, sorted(indas_keys - hk_keys)
    assert indas_keys < hk_keys, "the set is meant to be scoped, not a copy of the whole spine"


def test_it_is_the_size_an_ind_as_presentation_set_should_be(indas):
    """The count is a property worth pinning, because scoping is the one thing about this set that
    is easy to undo by accident — a regenerate that skips the scoping pass produces a working
    549-item file that is silently the wrong artefact."""
    lines = [i for i in indas.items if not i.parent]
    parts = [i for i in indas.items if i.parent]
    assert 150 <= len(lines) <= 200, len(lines)
    # Parts are decomposition components of a line, not presentation lines of their own, so they
    # are counted separately rather than against the presentation budget.
    assert parts, "the configured families and their note readings must survive the scoping"


def test_no_derived_parent_lost_a_cascade_term(indas):
    """THE HAZARD SCOPING CREATES, and the reason the selection is closed rather than filtered.

    A `derived` parent computes from its children, so dropping a child it references does not
    remove a line — it silently changes a published figure, and the parent still resolves on fewer
    terms. There is no warning for that anywhere, which is why it is asserted here.
    """
    keys = {i.key for i in indas.items}
    dangling = {}
    for item in indas.items:
        for rung in (item.cascade or ()):
            missing = [t.ref for t in (rung.terms or ())
                       if getattr(t, "ref", "") and t.ref not in keys]
            if missing:
                dangling.setdefault(item.key, []).extend(missing)
    assert not dangling, dangling


#: The one alias deliberately taken away, and why. `bs_cl__trade_payables_cp` carried BOTH halves
#: of the Schedule III trade-payables split, so two printed rows with different figures claimed one
#: key — measured on Asian Paints, the micro-enterprise row (₹216.67 crores) was winning over the
#: principal one (₹2,897.81 crores), understating trade payables by the whole difference. Keeping
#: the principal row means the MSME row goes to its section residual, where it is reviewable.
DELIBERATELY_DROPPED_ALIASES = {
    "bs_cl__trade_payables_cp": {
        "total outstanding dues of micro enterprises and small enterprises",
    },
}


def test_no_english_alias_was_taken_away_from_the_inherited_vocabulary(indas):
    """ADDED ALONGSIDE, NEVER REPLACING. An Ind AS filing that prints an IFRS spelling — and they
    do, because Ind AS is IFRS-converged — must still match.

    TWO EXCEPTIONS, AND BOTH ARE NAMED. The Chinese aliases are gone, because an alias in Han
    cannot match a caption on an English Indian filing and 651 of them made the configuration
    screen unreadable; and one English alias is gone because it made two printed rows claim one
    concept (`DELIBERATELY_DROPPED_ALIASES`). Anything else disappearing is a regression.
    """
    hk = {i.key: i for i in _set(HK).items}
    for item in indas.items:
        allowed = DELIBERATELY_DROPPED_ALIASES.get(item.key, set())
        before = {a for a in (hk[item.key].aliases or ()) if not _HAN.search(a)}
        before -= {a for a in before if a.strip().lower() in allowed}
        after = set(item.aliases or ())
        assert before <= after, (item.key, sorted(before - after))


def test_no_purely_chinese_value_survives_anywhere_in_the_set(indas):
    """A TEMPLATE FOR INDIAN ENTITIES CARRIES NO DEAD CHINESE VOCABULARY. Not as a matter of taste:
    `aliases`, `keyword_hints` and the note vocabulary are what an author reads on the configuration
    screen to decide whether a line is authored, and 125 of 276 items showed Traditional and
    Simplified Chinese there. None of it can match anything an Indian company prints.

    PURE-HAN VALUES ONLY, and a MIXED pattern deliberately survives. 30 items still carry Han
    inside alternations whose other branches are English —

        ^\\s*(?:total\\s+)?derivatives?\\b|derivative\\s+financial\\s+(?:instruments?|assets?)|^\\s*衍生

    — and removing one of those takes the English branches with it. On an exclusion that WIDENS
    what is accepted, which is the one direction this clean-up must never move: a first pass keyed
    on "contains Han" removed 1,917 `row_caption_none` values including the mixed ones and
    `test_every_removed_veto_was_unable_to_match_an_english_caption` caught it. Editing inside an
    alternation is regex surgery whose failure mode is a silent hole, so mixed patterns stay.
    """
    latin = re.compile(r"[A-Za-z]{3,}")
    offenders: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, str):
            if _HAN.search(node) and not latin.search(node):
                offenders.append(f"{path}: {node[:40]}")
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v, path)

    raw = json.loads(INDAS.read_text(encoding="utf-8"))
    for item in raw["items"]:
        walk(item, item["key"])
    # `cascade.note` and `definition` are PROSE: a sentence of Chinese inside an English paragraph
    # is not a pure-Han value and would not be caught above, so both are asserted directly.
    for item in raw["items"]:
        assert not _HAN.search(item.get("definition") or ""), item["key"]
        for rung in (item.get("cascade") or []):
            assert not _HAN.search(str(rung.get("note") or "")), item["key"]
    assert not offenders, offenders[:10]


#: Captions Schedule III mandates that this set deliberately does NOT place, each because the
#: ontology has no concept that means it and mis-binding it would be worse than leaving it to the
#: section residual. They are listed rather than quietly dropped so the gap is reviewable.
UNPLACED_BY_DESIGN = {
    # One half of the trade-payables split. The other half — "creditors other than" — is the
    # principal row and keeps the concept; see DELIBERATELY_DROPPED_ALIASES. India-specific
    # statute (the MSMED Act), so the HK spine has no key for it either.
    "Total outstanding dues of micro enterprises and small enterprises",
    # Schedule III presents Other Financial Liabilities and Other Current Liabilities as SEPARATE
    # lines. The spine has `bs_cl__other_current_liabilities` and
    # `bs_cl__trade_and_other_payables_cp`; the first is the other line and the second means trade
    # AND other, so binding this to either double-counts against a row already claimed.
    "Other Financial Liabilities",
    # Schedule III subtotals. "Total Income" is revenue from operations plus other income and
    # "Total Expenses" is the by-nature expense block; the spine's nearest keys
    # (`is_pl__total_cost_of_sales`, `is_pl__gross_profit`) are different quantities under a
    # by-function presentation.
    "Total Income",
    "Total Expenses",
    # A per-share metric rather than a statement line. Nothing in the spine means it, and the
    # weighted average share count it is computed from is not a concept either — the template's
    # `kpis` block is where a ratio of this kind belongs.
    "Earnings per equity share",
}

#: WHY THE FOUR ABOVE ARE NOT SIMPLY ADDED, which is the decision this file records.
#:
#: `test_every_concept_is_one_of_the_hk_spine_s` requires every Ind AS key to exist in the HK
#: spine: "A key that is NOT in the HK spine would be a fork — a new concept nobody can compare
#: across GAAPs." None of these four has a spine key, so closing them means either forking that
#: key space or extending the HK spine, and extending the spine changes the shipped HK ontology
#: and its template's row count. Both are product decisions, not refactors.
#:
#: The fifth, "Depreciation and Amortisation Expense", needed NO new key: `sub__pbt_depreciation`
#: already means the period's total depreciation, and the only difference between the GAAPs is
#: whether that total is printed on the face (Schedule III) or in the profit-before-tax note
#: (HKFRS). See `test_both_matchers_honour_the_statement_list`.
_UNPLACED_NEEDS_A_NEW_KEY = True


@pytest.mark.parametrize("caption, statement, section", SCHEDULE_III)
def test_every_schedule_iii_caption_is_placed(matcher, caption, statement, section):
    """Each line of the Division II face reaches a concept.

    Placement is asserted, not the specific concept: a caption's home is an accounting judgement
    that belongs in the file where it can be reviewed, while "the caption reaches SOMETHING" is the
    mechanical property — and it is the one that silently fails when an alias is authored onto a
    `derived` key, which `mapping._computed_parent` makes unmatchable. Two captions here were
    authored onto their FACE PART for exactly that reason.
    """
    placed = bool(matcher.match(caption, statement, section).canonical_key)
    if caption in UNPLACED_BY_DESIGN:
        assert not placed, (
            f"{caption!r} is listed as unplaced by design but now binds — if the ontology gained a "
            f"concept for it, take it out of UNPLACED_BY_DESIGN")
        return
    assert placed, caption


def test_it_places_materially_more_of_schedule_iii_than_the_hk_set(matcher):
    """The measurement that says the work was worth doing, and the guard against a regression that
    quietly reverts it. Measured when written: 58 of 58 against the HK set's 32."""
    hk = OntologyMatcher(build_working_view(_set(HK)), locale="en", settings=get_settings())
    mine = sum(bool(matcher.match(c, s, sec).canonical_key) for c, s, sec in SCHEDULE_III)
    theirs = sum(bool(hk.match(c, s, sec).canonical_key) for c, s, sec in SCHEDULE_III)
    assert mine == len(SCHEDULE_III) - len(UNPLACED_BY_DESIGN), (mine, sorted(UNPLACED_BY_DESIGN))
    assert mine > theirs, (mine, theirs)


def test_both_shipped_sets_seed_and_each_template_resolves_its_own():
    """AN ANALYST CAN CHOOSE THE GAAP, which is the point of shipping a second pair.

    The extraction screen's picker lists the stored line-item versions and the template follows the
    chosen one's `target_template_key` (`ExtractionView`'s `activeTemplate(..., cfg.target_template_key)`),
    so "can I pick Ind AS?" reduces to two questions this asserts on a FRESH database: does the
    pair seed at all, and does `config_select` answer each template with its own set.

    THE SECOND HALF IS THE ONE THAT COULD BREAK QUIETLY. `config_select` takes "the most recently
    stored" set for a template, and both sets seed in the same boot — so a set whose
    `target_template_key` were wrong would not fail, it would answer for the OTHER template and an
    Ind AS run would silently map against the 549-item HK spine.
    """
    import pathlib as _pathlib
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import LineItemVersion  # noqa: F401 — registers the tables on Base
    from app.sample.reference import ensure_reference_data
    from app.services.config_select import select_for_template

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{_pathlib.Path(tmp) / 'choice.db'}")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            ensure_reference_data(session)
            keys = {r.line_items_key for r in session.query(LineItemVersion).all()}
            assert {"output_csv_hk", "output_csv_indas"} <= keys, sorted(keys)

            hk = select_for_template(session, "output_csv_hk_v1")
            ind = select_for_template(session, "output_csv_indas_v1")
            assert hk is not None and ind is not None
            assert hk.line_items_key == "output_csv_hk"
            assert ind.line_items_key == "output_csv_indas"
            # And the sets are really different configurations, not one answering twice.
            assert len((ind.definition or {}).get("items") or []) \
                < len((hk.definition or {}).get("items") or [])
        finally:
            # Dispose before the directory goes: on Windows an open SQLite handle blocks rmtree.
            session.close()
            engine.dispose()


def test_the_indian_note_vocabulary_widens_context_and_not_the_deterministic_read(indas):
    """SCHEDULE III NOTE VOCABULARY ON EVERY LINE, AND STILL NO NEW DETERMINISTIC READ.

    THIS TEST USED TO FORBID ADDING A `note_title_any` AT ALL, on the reasoning that
    `note_sourced.select_rows` needs a title match before it can take a row, so a new title could
    move a published figure. The reasoning was right and the conclusion was too strong, and the
    measurement that settled it is the first Indian filing in the corpus: of the 250 Ind AS lines
    the model is asked about, 170 reached it with NO NOTES ATTACHED, because
    `line_item_notes.note_probe` returns `note_terms` FIRST AND ALONE where it exists and 605 of
    those 997 values were Chinese. Withholding note titles did not keep the set safe; it kept the
    set blind.

    WHAT MAKES ADDING THEM SAFE IS THE SECOND GATE, which the old reasoning missed.
    `select_rows` opens

        if not titles or not counts: return []

    where `counts` is `row_caption_any`. BOTH are required. So a `note_source` carrying a title and
    terms but NO `row_caption_any` can never produce a row, whatever it matches — and that is
    exactly the shape written onto the 196 lines that had no `note_source` before. Asserted by
    BEHAVIOUR below rather than by reading the source, against a note whose title those patterns
    do match, because the whole claim is about what the function returns.

    The 65 lines that already carried a full `note_source` keep their `row_caption_any`, so their
    read is still gated by the row vocabulary their author wrote; what changed for them is only
    WHICH note the title finds, from a Chinese heading that matches nothing on an Indian filing to
    the Schedule III heading that does. That is a widening from nothing, not a redirection.
    """
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
    from app.services.note_sourced import select_rows

    from app.services import line_item_routes

    hk = {i.key: i for i in _set(HK).items}
    created = inherited = 0
    for item in indas.items:
        mine, theirs = item.note_source, hk[item.key].note_source
        if str(getattr(item, "type", "") or "") == "derived":
            # The loader refuses a `note_source` on a derived line — its figure is its cascade's.
            assert mine is None, f"{item.key} is derived and may not declare a note_source"
            continue
        if not line_item_routes.may_read_notes(item):
            # A DECLARED `face` LINE IS NEVER GIVEN NOTES, so vocabulary here is read by nothing.
            # `note_sets` skips it (`may_read_notes` is False), `build_request` filters the
            # document's identified notes by what the request's lines selected, and `select_rows`
            # would refuse it for want of `row_caption_any`. What a face line gets instead is the
            # statement block, which is the right context for a figure printed on the face —
            # `build_request`'s own docstring says so. An earlier version of this set wrote note
            # vocabulary onto all 276 lines; 156 of those declarations were inert, which is the
            # same judgement the Chinese aliases got.
            assert mine is None, (
                f"{item.key} declares `face` and carries a note_source, which nothing reads")
            continue
        assert mine is not None, f"{item.key} has no note_source, so it is asked about blind"
        # A title pattern, or none at all for a line that finds its notes BY MEANING — the
        # Securities (CP) parts, whose `note_terms` are the whole of their note selection.
        from app.services.line_item_notes import by_meaning_only
        assert mine.note_title_any or by_meaning_only(item), item.key
        assert mine.note_terms, item.key
        # NO CHINESE ANYWHERE IN THE VOCABULARY THE MODEL IS STEERED BY.
        assert not _HAN.search(" ".join(mine.note_terms)), item.key
        assert not _HAN.search(" ".join(mine.note_title_any)), item.key

        if theirs is None:
            created += 1
            assert not mine.row_caption_any, (
                f"{item.key}: a created note_source declared `row_caption_any`, which opens the "
                f"deterministic note read on a line whose note vocabulary was never measured")
        else:
            inherited += 1
            assert list(mine.row_caption_any) == [
                v for v in theirs.row_caption_any if not _dead_here(v)], item.key

    # 40 created on the note-routed and routeless lines, 65 rewritten onto the
    # Schedule III note their HK concept corresponds to.
    # 62 since the three Securities (CP) Find 2 and non-current parts left both sets.
    # 58 since the four Securities (LTP) Find 2 and split parts left both sets.
    assert created == 40 and inherited == 58, (created, inherited)

    # THE BEHAVIOURAL HALF. A note whose heading matches the PPE family's own title pattern, with a
    # row that any depreciation vocabulary would claim. A line whose note_source was created must
    # still read nothing out of it.
    table = NotesTable(note_number="2A", title="Property, Plant and Equipment", page_index=9)
    row = NoteItem(raw_label="Depreciation for the year", note_number="2A")
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("1085.7"), provenance=Provenance(page_index=9)))
    table.items.append(row)
    by_key = {i.key: i for i in indas.items}
    created_keys = [i.key for i in indas.items
                    if i.note_source is not None and hk[i.key].note_source is None]
    assert created_keys, "nothing was created, so this asserts nothing"
    for key in created_keys:
        item = by_key[key]
        assert item.note_source.note_title_any, key
        assert select_rows(item, [table]) == [], (
            f"{key} took a row off a note it merely names — `row_caption_any` is the second gate "
            f"and this line declares none")
