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


def test_the_template_it_targets_exists_and_declares_that_key():
    raw = json.loads((TEMPLATES / "output_csv_indas_v1_template.json").read_text(encoding="utf-8"))
    assert raw["template_key"] == "output_csv_indas_v1"


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
        assert list(mine.note_title_any) == list(theirs.note_title_any), (
            f"{item.key}: a note TITLE was added or changed, which can move a published figure "
            f"and cannot be justified without an Indian filing to measure it against")
        assert list(mine.row_caption_any) == list(theirs.row_caption_any), item.key
        if list(mine.note_terms) != list(theirs.note_terms):
            assert set(theirs.note_terms) <= set(mine.note_terms), item.key
            widened += 1
    assert widened, "no Indian note vocabulary reached the set at all"
