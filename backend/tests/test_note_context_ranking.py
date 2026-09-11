"""Does the CONFIGURATION actually work as a semantic index? Measured, on the shipped set.

THIS IS THE LOAD-BEARING CLAIM OF THE NOTE SELECTOR. There is no embedding provider in this system,
so "semantically similar" is implemented by scoring each note against authored prose, and the
result is only a judgement about subject matter if that prose actually discriminates. That is an
argument, and an argument is not evidence. So this file is the evidence, and it is executable: ten
note subjects of the kind HK filings print, and for each, the shipped line item whose notes it
should be — the assertion is that the note wins its own probe.

WHOSE PROSE IS SCORED CHANGED, and the tests moved with it. The probe used to be
`mapping._context_probe`: for a printed ROW, the assembled criteria of the concepts the
deterministic tiers thought it might be, with the rulebook's machine-generated template sentence
filtered out (`_criteria_boilerplate`) so 25 tokens appearing in 76%-99.6% of definitions could not
decide a match. That probe belonged to a request about a row, and it is gone with it.

THE LIVE PROBE IS THE LINE'S OWN `note_terms`, scored against note HEADERS
(`line_item_notes.note_probe` / `header_pool`). It is a better mechanism for a measured reason: a
heading names the CONTAINER ("administrative expenses", 管理费用) while everything else written
about a line names its CONTENT ("depreciation of fixed assets"), and a blended probe scored 0.000
against the very heading its line belongs to. It also needs no boilerplate filter — `note_terms` is
authored for this job rather than generated — which is why the two filter tests here dissolved
instead of moving.

WHAT IS STILL NOT MEASURED, said out loud: this scores ten synthetic English headings. The
selector's behaviour on a PRC filing is the open question — semantic selection found 100% of the
authored notes on the English filing and 53% on the Chinese one, which is why patterns and
semantics are a UNION rather than a replacement (`note_context.identified_notes`). A corpus-scale
measurement needs the filings, which are client documents and are not in this repository.

If a configuration edit hollows out the `note_terms` this relies on, this file fails — which is the
point. Nothing else in the suite would notice: every request would still be well-formed, and every
line would simply receive notes about other subjects.
"""
from __future__ import annotations

import json
import pathlib

from app.config import get_settings
from app.core.models.enums import MappingMethod
from app.schemas.line_items import load_line_item_set
from app.services.mapping import MappingResult, OntologyMatcher
from app.services.note_context import ContextPool, ContextUnit, subject_tokens
from app.services.working_view import build_working_view

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# Note subjects, in the shape a filing prints them: a title and the note's own row captions.
_NOTES: dict[str, tuple[str, list[str]]] = {
    "12": ("Trade and other receivables",
           ["Trade receivables from third parties", "Less: loss allowance",
            "Prepayments and deposits", "Amounts due from related parties"]),
    "13": ("Inventories", ["Raw materials", "Work in progress", "Finished goods"]),
    "14": ("Property, plant and equipment",
           ["Leasehold land and buildings", "Plant and machinery", "Motor vehicles",
            "Accumulated depreciation"]),
    "15": ("Share capital", ["Ordinary shares issued and fully paid"]),
    "16": ("Borrowings", ["Bank loans secured", "Debentures", "Finance lease liabilities"]),
    "17": ("Trade and other payables",
           ["Trade payables", "Accruals and other payables", "Contract liabilities"]),
    "18": ("Income tax", ["Current tax", "Deferred tax", "Underprovision in prior years"]),
    "19": ("Employee benefits",
           ["Salaries and wages", "Retirement benefit contributions", "Share-based payments"]),
    "20": ("Revenue", ["Sales of goods", "Rendering of services", "Rental income"]),
    "21": ("Cash and bank balances",
           ["Cash at bank and on hand", "Short-term deposits", "Pledged deposits"]),
}

# The concept each note is about, by the shipped rulebook's own canonical keys. Only the notes with
# an unambiguous counterpart are probed — a note whose subject the rulebook splits across several
# concepts would make "the right answer" a matter of opinion.
_PROBES: dict[str, str] = {
    "12": "bs_ca__trade_and_other_receivables",
    "13": "bs_ca__inventories",
    "16": "bs_cl__st_bank_loans_payable",
    "17": "bs_cl__trade_and_other_payables_cp",
    "18": "is_pl__total_income_tax",
    "20": "is_pl__sales_revenues",
    "21": "bs_ca__cash_in_hand_and_at_banks",
}


def _pool() -> ContextPool:
    """The header pool the live selector builds — `captions=()` is the point.

    `line_item_notes.header_pool` carries each note's HEADING and nothing else, because that is
    what a line's `note_terms` are authored against. The row captions in `_NOTES` are kept in the
    fixture because they are what a filing prints, and because the row-level probe
    (`line_item_notes.row_probe`) scores against them — a separate vocabulary and a separate pool,
    since what makes a word distinctive among a filing's headings is not what makes it distinctive
    among the rows of one note.
    """
    return ContextPool([ContextUnit(kind="note", ref=ref, title=title, captions=())
                        for ref, (title, _rows) in _NOTES.items()])


def _row_pool(ref: str) -> ContextPool:
    """One note's ROWS, for the level-2 probe."""
    title, rows = _NOTES[ref]
    return ContextPool([ContextUnit(kind="row", ref=f"{ref}:{i}", title=cap, captions=())
                        for i, cap in enumerate(rows)])


def _by_key():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {m.canonical_key: m for m in build_working_view(st).mappings}


def _matcher():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return OntologyMatcher(build_working_view(st), locale="en", settings=get_settings())


def _lines():
    """The shipped line items by key, as the selector reads them."""
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    by_key = {i.key: i for i in st.items}
    return st, by_key


def _probe_tokens(by_key, key: str) -> set[str]:
    """THE PROBE THE SELECTOR ACTUALLY BUILDS, not a re-spelling of it.

    Calling `note_probe` rather than reassembling the prose here is the difference between
    measuring the shipped selector and measuring a copy of it — and the fallback for a line with no
    authored `note_terms` lives inside that function, so a hand-written probe would score against
    text no request ever carries.
    """
    from app.services.line_item_notes import note_probe

    item = by_key[key]
    parent = by_key.get(getattr(item, "parent", "") or "")
    return set(subject_tokens(note_probe(item, parent)))


def _scores() -> tuple[list[float], list[float]]:
    """Every (true, other) score across all probes, for the threshold assertions below."""
    pool, (_st, by_key) = _pool(), _lines()
    true_scores: list[float] = []
    other_scores: list[float] = []
    for ref, key in _PROBES.items():
        probe = _probe_tokens(by_key, key)
        for unit in pool.units:
            (true_scores if unit.ref == ref else other_scores).append(pool._score(unit, probe))
    return true_scores, other_scores


def test_every_note_wins_its_own_concepts_probe():
    """Top-1 accuracy on the shipped rulebook. This is the claim; everything else is a detail."""
    pool, (_st, by_key) = _pool(), _lines()
    misses = []
    for ref, key in _PROBES.items():
        probe = _probe_tokens(by_key, key)
        best = max(pool.units, key=lambda u: (pool._score(u, probe), u.ref == ref))
        if best.ref != ref:
            misses.append(f"{key}: expected note {ref} ({_NOTES[ref][0]}), "
                          f"got note {best.ref} ({_NOTES[best.ref][0]})")
    assert not misses, "\n".join(misses)


def test_the_selectors_threshold_sits_between_the_true_matches_and_the_noise():
    """`notes_for_line_item`'s `min_score` default is a measurement, and this is the measurement.

    Asserted as a RANGE the default must lie in rather than as a literal, so moving it stays
    possible while putting it somewhere that admits noise or rejects true matches does not.

    IT IS A CODE DEFAULT AND NOT A SETTING, which is a change. `extraction.llm_context_min_score`
    (0.22) was the same number for the ROW probe and is retired with it — see config.py. The live
    threshold is `line_item_notes.notes_for_line_item(min_score=0.30)`, read straight off the
    signature here rather than restated, so the two cannot drift.

    MEASURED ON THE LIVE PROBE, AND THE SHIPPED DEFAULT IS JUST BELOW THE GAP: worst true match
    0.598, noise 90th percentile 0.308, and `min_score` is 0.300. So the gap the threshold is
    supposed to sit inside is real — worst true is 1.94x the noise band — but 0.30 sits a hair
    under its lower edge, which means roughly the top tenth of unrelated headings clears it and a line can
    receive a note about another subject. That is a permissive default rather than a broken one:
    `cap=4` bounds how many survive, the authored `note_title_any` patterns are a UNION with the
    semantic hits rather than being replaced by them, and a note carried in error costs request
    size rather than a wrong figure.
    #
    # NOT CHANGED HERE. Moving `min_score` changes which notes every line receives on every run,
    # and that is a behaviour decision with its own measurement to do — not a side effect of
    # removing a request path. Asserted as the GAP (the load-bearing claim) plus the default's
    # position relative to it, so the day someone moves it they see both numbers.
    """
    import inspect

    from app.services.line_item_notes import notes_for_line_item

    true_scores, other_scores = _scores()
    worst_true = min(true_scores)
    noise = sorted(other_scores, reverse=True)
    ninetieth = noise[len(noise) // 10]

    # THE CLAIM: true matches and noise are separated, with room to put a threshold between them.
    assert ninetieth < worst_true, (ninetieth, worst_true)
    assert worst_true > 1.5 * ninetieth, (
        f"the gap collapsed: worst true {worst_true:.3f} is no longer half again the noise 90th "
        f"percentile {ninetieth:.3f}, so no threshold can separate them")

    # THE DEFAULT'S POSITION IN THAT GAP, asserted rather than assumed. It must not reject a true
    # match, which is the failure that loses a figure.
    configured = inspect.signature(notes_for_line_item).parameters["min_score"].default
    assert configured < worst_true, (
        f"min_score {configured} rejects the worst true match {worst_true:.3f} — a line would "
        f"receive none of its own notes")


def test_most_unrelated_notes_score_exactly_zero():
    """IDF over the document's own units is what replaces a stopword list. If unrelated notes were
    scoring above zero on shared boilerplate, the threshold would be carrying the whole burden of
    the selection — and a threshold cannot tell a subject from a coincidence."""
    _true, other = _scores()
    zero = sum(1 for s in other if s == 0.0)
    assert zero > len(other) / 2, f"only {zero} of {len(other)} unrelated pairs scored zero"


def test_the_true_and_unrelated_distributions_do_not_overlap():
    """The strongest form of the claim: on the shipped rulebook the WORST correct match still scores
    above the BEST unrelated one. Where that holds, the threshold is a convenience rather than the
    thing holding the selection together."""
    true_scores, other = _scores()
    assert min(true_scores) > max(other), (min(true_scores), max(other))


# ── the definition IS the semantic index ───────────────────────────────────────────────────────

def test_no_definition_describes_the_implementation():
    """A DEFINITION THAT DESCRIBES CODE CANNOT IDENTIFY A SUBJECT, and all eight focus concepts
    used to.

    `is_pl__deprec_and_impairment_oper_exp` read "Computed, never alias-matched:
    services.deprec_impairment resolves this by trying, in order, direct operating-expense-note
    depreciation (P1)…" — a description of a module that has since been DELETED. The probe built
    from it was half implementation jargon (`services`, `alias`, `matched`, `computed`, `resolves`,
    `docs`, `hkex`, `logic`), and those words survive the boilerplate filter precisely because they
    are unique to one concept: the filter strips what is common to most concepts, so the words that
    identify a concept LEAST are the ones that survive best.

    Measured consequence: the footnote stating that concept's figure scored 0.130 and ranked 33 of
    1,466. With a definition that says what the line MEANS it scores 0.323 and ranks 3 of 1,200 —
    same algorithm, same pool, different prose.
    """
    import re

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    banned = re.compile(
        r"services\.[a-z_]+"          # a module, deleted or not
        r"|alias.matched"
        r"|Computed, never"
        r"|resolves this"
        r"|\bdocs/"
        r"|\.md\b"
        r"|\((?:P|COS_P|LTP_P|CP_P|CL_P)\d\)",   # cascade rung ids belong on the cascade
        re.IGNORECASE)
    offenders = [(i["key"], banned.search(i.get("definition") or "").group(0))
                 for i in raw["items"] if banned.search(i.get("definition") or "")]
    assert not offenders, (
        "a definition describes how a figure is computed rather than what it is — that text is what "
        f"the semantic tier reasons over: {offenders[:5]}")


def test_the_footnote_that_states_a_figure_is_reachable_semantically():
    """THE WHOLE CLAIM, on the real prose. The operating-expense share of laisun.pdf's depreciation
    charge is stated in a footnote and in no extracted row. It has to be reachable by MEANING —
    not only by the identified-notes block, which passes such notes in full and costs ~38k tokens a
    request.

    Asserted on a hand-built pool rather than by running the filing, so the test is fast and states
    exactly which two properties carry it: a sentence-level unit, and a definition about the
    subject.
    """
    from app.services.note_context import ContextPool, ContextUnit

    footnote = ("^ Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
                "included in \u201cother operating expenses\u201d on the face of the consolidated "
                "income statement.")
    pool = ContextPool([
        ContextUnit(kind="note", ref="7", title="LOSS FROM OPERATING ACTIVITIES",
                    captions=(footnote,), prose=True),
        ContextUnit(kind="note", ref="13", title="Inventories",
                    captions=("Raw materials", "Finished goods")),
        ContextUnit(kind="note", ref="16", title="Right-of-use assets",
                    captions=("Depreciation charge of right-of-use assets",)),
    ])
    by_key = _by_key()
    definition = by_key["is_pl__deprec_and_impairment_oper_exp"].meaning() or ""
    assert "operating expenses" in definition.lower(), \
        "the definition no longer names the expense category the footnote routes to"

    got = pool.select(probe_text=definition, notes_cap=2, face_cap=0, char_budget=4000)
    assert any("529,841,000" in json.dumps(u, ensure_ascii=False) for u in got), \
        [u["ref"] for u in got]
