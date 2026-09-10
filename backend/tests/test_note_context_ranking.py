"""Does the CONFIGURATION actually work as a semantic index? Measured, on the shipped rulebook.

THIS IS THE LOAD-BEARING CLAIM OF `services.note_context`. There is no embedding provider in this
system, so "semantically similar" is implemented by scoring each note against the authored criteria
— ``definition``, ``include``, aliases — of the concepts a row might be. Those criteria are prose
about what a concept MEANS, which is why the result is a judgement about subject matter rather than
a caption-to-title string comparison. But that is an argument, and an argument is not evidence.

So this file is the evidence, and it is executable. Ten note subjects of the kind HK filings print;
for each, the shipped rulebook's own criteria for the concept that note is about; the assertion is
that the note wins its own probe. It is also the source of the ``llm_context_min_score`` default:
the gap between the worst true score and the unrelated noise is measured here, so the threshold in
config is a number this test justifies rather than a taste.

If a rulebook edit hollows out the definitions this relies on, this test fails — which is the point.
Nothing else in the suite would notice: every request would still be well-formed, and every run
would still report itself as LLM-mapped while quietly carrying context about other subjects.
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
    return ContextPool([ContextUnit(kind="note", ref=ref, title=title, captions=tuple(rows))
                        for ref, (title, rows) in _NOTES.items()])


def _by_key():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {m.canonical_key: m for m in build_working_view(st).mappings}


def _matcher():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return OntologyMatcher(build_working_view(st), locale="en", settings=get_settings())


def _probe_tokens(matcher, key: str) -> set[str]:
    """THE PROBE THE MATCHER ACTUALLY BUILDS, not a re-spelling of it.

    Calling `_context_probe` rather than reassembling the criteria here is the difference between
    measuring the shipped selector and measuring a copy of it — and the boilerplate filter lives
    inside that method, so a hand-written probe would score against text no request ever carries.
    """
    result = MappingResult(canonical_key=key, method=MappingMethod.RULE, confidence=1.0)
    return set(subject_tokens(matcher._context_probe("", result)))


def _scores() -> tuple[list[float], list[float]]:
    """Every (true, other) score across all probes, for the threshold assertions below."""
    pool, matcher = _pool(), _matcher()
    true_scores: list[float] = []
    other_scores: list[float] = []
    for ref, key in _PROBES.items():
        probe = _probe_tokens(matcher, key)
        for unit in pool.units:
            (true_scores if unit.ref == ref else other_scores).append(pool._score(unit, probe))
    return true_scores, other_scores


def test_every_note_wins_its_own_concepts_probe():
    """Top-1 accuracy on the shipped rulebook. This is the claim; everything else is a detail."""
    pool, matcher = _pool(), _matcher()
    misses = []
    for ref, key in _PROBES.items():
        probe = _probe_tokens(matcher, key)
        best = max(pool.units, key=lambda u: (pool._score(u, probe), u.ref == ref))
        if best.ref != ref:
            misses.append(f"{key}: expected note {ref} ({_NOTES[ref][0]}), "
                          f"got note {best.ref} ({_NOTES[best.ref][0]})")
    assert not misses, "\n".join(misses)


def test_the_configured_threshold_sits_between_the_true_matches_and_the_noise():
    """The `llm_context_min_score` default is a measurement, and this is the measurement.

    Asserted as a RANGE the default must lie in rather than as a literal, so tuning the knob
    stays possible while putting it somewhere that admits noise or rejects true matches does not.
    """
    from app.config import get_settings

    true_scores, other_scores = _scores()
    worst_true = min(true_scores)
    noise = sorted(other_scores, reverse=True)
    ninetieth = noise[len(noise) // 10]
    assert ninetieth < worst_true, (ninetieth, worst_true)
    configured = get_settings().extraction.llm_context_min_score
    assert ninetieth < configured < worst_true, (ninetieth, configured, worst_true)


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


def test_the_generated_boilerplate_does_not_decide_the_match():
    """REGRESSION. All 462 shipped definitions share one generated sentence — "Extract the reported
    value for '<label>' from the stated section. Do not calculate or replace it, because the revised
    template does not designate this field as formula-driven." Before `_criteria_boilerplate`
    filtered those words out of the probe, the cash-flow translation adjustment selected the
    TRADE-RECEIVABLES note at 0.381 on three shared words — `amounts`, `from`, `other` — all three
    from the template sentence and none from the concept.

    The fix is asserted by its outcome, not by inspecting the filter: the concept must now reach the
    note about CASH, which is what it is actually about, and must not reach receivables at all.
    """
    matcher = _matcher()
    got = _pool().select(
        probe_text=matcher._context_probe("", MappingResult(
            canonical_key="cf_financing__translation_adj_relating_to_cash",
            method=MappingMethod.RULE, confidence=1.0)),
        notes_cap=3, face_cap=0)
    refs = [u["ref"] for u in got]
    assert "21" in refs, refs          # Cash and bank balances
    assert "12" not in refs, refs      # Trade and other receivables


def test_the_boilerplate_filter_catches_the_template_sentence_and_nothing_else():
    """The 0.6 fraction has to land in the cliff the measurement found: 25 tokens in 76%-99.6% of
    concepts, then `balance` at 47%. Asserted from both sides, because a fraction set too low would
    start eating real financial vocabulary and the symptom would be silently worse selection."""
    drop = _matcher()._criteria_boilerplate()
    # Every word of the generated sentence is gone.
    assert {"extract", "reported", "value", "stated", "section", "calculate", "replace",
            "template", "formula", "driven", "amounts", "from", "other"} <= drop
    # And no word that names a subject is.
    assert not (drop & {"receivables", "inventories", "payables", "cash", "revenue", "tax",
                        "borrowings", "depreciation", "equity", "balance", "sheet", "income"})


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
