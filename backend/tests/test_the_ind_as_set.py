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

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
INDAS = TEMPLATES / "output_csv_indas_line_items.json"
HK = TEMPLATES / "output_csv_hk_line_items.json"

BS, PL = "balance_sheet", "profit_and_loss"

# The Division II face, as Schedule III prints it. Not a sample: the balance sheet's own line
# sequence and the statement of profit and loss's, in order.
SCHEDULE_III = [
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


def test_the_veto_lists_were_not_touched(indas):
    """THE ASYMMETRY THAT MATTERS, and the reason the Han sweep was confined to two fields.

    `note_title_any` and `row_caption_any` SELECT, so dropping one can only narrow what is read.
    `row_caption_none` and `row_terms_none` VETO, so dropping one WIDENS what is accepted — which is
    how a movement row or an allowance column gets taken for a balance. Every veto list must still
    be exactly the HK set's.
    """
    hk = {i.key: i for i in _set(HK).items}
    for item in indas.items:
        mine, theirs = item.note_source, hk[item.key].note_source
        if mine is None or theirs is None:
            continue
        for field in ("row_caption_none", "row_terms_none"):
            assert list(getattr(mine, field) or ()) == list(getattr(theirs, field) or ()), (
                f"{item.key}.{field} was modified — a veto may not be narrowed")


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


def test_nothing_was_taken_away_from_the_inherited_vocabulary(indas):
    """ADDED ALONGSIDE, NEVER REPLACING. An Ind AS filing that prints an IFRS spelling — and they
    do, because Ind AS is IFRS-converged — must still match."""
    hk = {i.key: i for i in _set(HK).items}
    for item in indas.items:
        before = set(hk[item.key].aliases or ())
        after = set(item.aliases or ())
        assert before <= after, (item.key, sorted(before - after))


@pytest.mark.parametrize("caption, statement, section", SCHEDULE_III)
def test_every_schedule_iii_caption_is_placed(matcher, caption, statement, section):
    """Each line of the Division II face reaches a concept.

    Placement is asserted, not the specific concept: a caption's home is an accounting judgement
    that belongs in the file where it can be reviewed, while "the caption reaches SOMETHING" is the
    mechanical property — and it is the one that silently fails when an alias is authored onto a
    `derived` key, which `mapping._computed_parent` makes unmatchable. Two captions here were
    authored onto their FACE PART for exactly that reason.
    """
    assert matcher.match(caption, statement, section).canonical_key, caption


def test_it_places_materially_more_of_schedule_iii_than_the_hk_set(matcher):
    """The measurement that says the work was worth doing, and the guard against a regression that
    quietly reverts it. Measured when written: 58 of 58 against the HK set's 32."""
    hk = OntologyMatcher(build_working_view(_set(HK)), locale="en", settings=get_settings())
    mine = sum(bool(matcher.match(c, s, sec).canonical_key) for c, s, sec in SCHEDULE_III)
    theirs = sum(bool(hk.match(c, s, sec).canonical_key) for c, s, sec in SCHEDULE_III)
    assert mine == len(SCHEDULE_III), mine
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
    """`note_terms` ONLY — the distinction that makes this safe without an Indian corpus.

    `note_sourced.select_rows` returns nothing unless a `note_title_any` pattern matches, so a
    title admits a table to the read that produces a PUBLISHED FIGURE. `note_terms` is scoring
    vocabulary and reaches only what the model READS. With no Indian filing to measure against,
    this set may widen the second and must not touch the first.
    """
    hk = {i.key: i for i in _set(HK).items}
    widened = 0
    for item in indas.items:
        mine, theirs = item.note_source, hk[item.key].note_source
        if mine is None or theirs is None:
            assert (mine is None) == (theirs is None), item.key
            continue
        # A SUBSET, NOT AN EQUALITY, and the direction is the whole point. ADDING a title pattern
        # can move a published figure and cannot be justified without an Indian filing to measure
        # it against; REMOVING one can only narrow what is read, and the pure-Han patterns were
        # removed as dead weight. So every pattern this set declares must be one the HK set
        # declares, and nothing new may appear.
        assert set(mine.note_title_any) <= set(theirs.note_title_any), (
            f"{item.key}: a note TITLE was ADDED, which can move a published figure: "
            f"{sorted(set(mine.note_title_any) - set(theirs.note_title_any))}")
        assert set(mine.row_caption_any) <= set(theirs.row_caption_any), item.key
        if list(mine.note_terms) != list(theirs.note_terms):
            assert set(theirs.note_terms) <= set(mine.note_terms), item.key
            widened += 1
    assert widened, "no Indian note vocabulary reached the set at all"
