"""When one review card may stand in for another — pinned against the SHIPPED rulebook.

A card is left out of the queue only when a card above it ALREADY TELLS THE READER THE SAME THING:
the same difference, about the same target, in the same column. Anything less than that and the card
is served, because a duplicate is noise a reader can see past while a dropped card is a blocking
break reported nowhere at all — and the coverage band's "reported by a finding above" counter then
states that it WAS reported.

The suppression used to compare bare ``details.target`` strings, and on the equity section of the
shipped ``hkfrs_hk_china`` template that cost two blocking findings and a whole column:

* the template declares ``bs_equity__total_equity`` as a rollup subtotal AND the rulebook asks for
  its section to reconcile, so total equity is the target of TWO relations —
  ``rollup:bs_equity__total_equity`` and ``section_reconciliation:bs_s5_equity``. An equity-closing
  card whose target is that same key — asserting the break between the equity statement's closing row
  and the balance sheet — matched both by string and deleted them, whatever difference they found;
* the key carried no scope while ``chk-equity-closing`` is hardcoded consolidated/current, so that
  same consolidated card also deleted both relations in the STANDALONE column, which it makes no
  claim about at all.

Every relation test here is driven by the real loader, the real evaluator and the real review builder
over the shipped template + ontology, because the collision is the PRODUCER's: it is the template that
puts two relations on one target, and the rulebook that asks for the section reconciliation. A
hand-shaped relation dict cannot demonstrate either, and cannot notice a template revision moving
them. ``tests/test_review_checks.py`` holds the predicate-level tests over synthetic relations; this
file holds the payload a reader would have been served.

THE SAME BLINDNESS ALSO LIVED ON THE CALCULATED PATH, which asked one bare-key question — "does this
template line already have a card at all" — of two findings that say different things. The last three
tests are that half, and a dropped card there is worse still: a dropped relation is at least counted in
``failed_reported_elsewhere``, while nothing counts a calculated card at all, so it left the queue with
nothing anywhere saying it had. The balance card, hardcoded consolidated/current, deleted the standalone
column's printed-subtotal mismatch, and — reporting a 100 break of its own — deleted a 250 mismatch it
says nothing about in its own column.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))
ONTOLOGY = json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))

# The equity section as a filing could print it, with one thing wrong: total equity stands 2,500
# above the components the template says it is made of in the CONSOLIDATED column, and 900 above them
# in the STANDALONE one. Both are the section's own arithmetic, so the rollup and the section
# reconciliation each find the same break — which is the collision the defect needed.
_CONSOLIDATED = {
    "bs_equity__share_capital": 1000,
    "bs_equity__share_premium": 4000,
    "bs_equity__retained_earnings": 3000,
    "bs_equity__reserves": 7000,
    "bs_equity__equity_attributable_to_owners": 8000,
    "bs_equity__non_controlling_interests": 500,
    "bs_equity__total_equity": 11000,                      # 8,000 + 500 → out by 2,500
}
_STANDALONE = {**_CONSOLIDATED, "bs_equity__total_equity": 9400}       # 8,000 + 500 → out by 900

_BS_EQUITY = _CONSOLIDATED["bs_equity__total_equity"]

# A coverage block shaped as the presenter serves it, so `failed_reported_elsewhere` is filled in.
# That count is half of what these tests are about: it is derived from the same predicate that
# suppresses, so a relation dropped from the queue is either on the screen or in this number.
_COV_BLOCK = {"available": True, "aggregate": {}, "statements": [], "skips": [], "alarms": [],
              "run_id": "r", "engine_version": "0"}

_ROLLUP = "chk-structural-rollup:bs_equity__total_equity-"
_SECTION = "chk-structural-section_reconciliation:bs_s5_equity-"


def _items():
    """One LineItem per concept, carrying both columns — the evaluator's input."""
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem

    out = []
    for key in _CONSOLIDATED:
        li = LineItem(source_label=key, canonical_key=key)
        for basis, figures in ((Basis.CONSOLIDATED, _CONSOLIDATED),
                               (Basis.STANDALONE, _STANDALONE)):
            num = Decimal(figures[key])
            li.set_value(ExtractedValue(value=num, value_raw=num, basis=basis,
                                        period_label="current"))
        out.append(li)
    return out


def _rows(closing: int) -> list[dict]:
    """The serialized rows the review builder reads, plus the equity statement's closing row.

    ``closing`` is that row's "Total equity" figure, which is what sets the difference the
    equity-closing card ASSERTS (``closing`` minus the balance sheet's total equity). The row is a
    matrix row — named component columns rather than periods — which is how ``_equity_closing`` finds
    it, and it carries no canonical key because an equity statement's movements map to none.
    """
    out = []
    for key, value in _CONSOLIDATED.items():
        out.append({"canonical_key": key, "source_label": key, "values": [
            {"basis": "consolidated", "period_label": "current", "value": str(value)},
            {"basis": "standalone", "period_label": "current", "value": str(_STANDALONE[key])}]})
    out.append({"canonical_key": None, "source_label": "At 31 December 2024", "values": [
        {"basis": "consolidated", "period_label": "Total equity", "value": str(closing)},
        {"basis": "consolidated", "period_label": "Share capital", "value": "1000"}]})
    return out


def _structural() -> list[dict]:
    """Relation results from the real evaluator over the shipped template + ontology, as
    ``run.result["structural"]`` stores them."""
    from app.schemas.loader import load_ontology, load_template
    from app.services.structural_checks import evaluate_structure

    report = evaluate_structure(load_template(TEMPLATE), _items(),
                                load_ontology(ONTOLOGY, resolve=True))
    return [r.model_dump(mode="json") for r in report.results]


def _served(closing: int) -> dict:
    """The review payload for a run whose equity-closing card asserts ``closing`` − total equity."""
    from app.api.routes.documents import _build_review

    return _build_review(_rows(closing), "doc.pdf", "en", [], _structural(),
                         template_def=TEMPLATE, coverage_block=dict(_COV_BLOCK))


def _relation_cards(review: dict) -> dict[str, str]:
    """The ARITHMETIC relation cards on the queue, as {card id: printed difference}.

    Keyed on the id because that is the only handle that names WHICH relation the card is for: two
    relations targeting one concept in one column share a ``where`` ("target · scope"), and it is
    precisely those two that used to disappear together.

    Guards are excluded: a guard asserts a condition rather than an equality, is never suppressed,
    and is therefore no part of the partition these tests check.
    """
    return {c["id"]: c["delta"] for c in review["checks"]
            if c["type"] == "structural" and c["subject"]["k"] == "structural"}


def _failed_relations() -> list[dict]:
    """Every failed ARITHMETIC relation the evaluator produced for this filing."""
    return [r for r in _structural()
            if r["status"] == "fail" and r["kind"] != "guard" and not r["details"].get("guard")]


def test_the_shipped_template_puts_two_relations_on_total_equity_in_both_columns():
    """THE FIXTURE HAS TO CARRY THE COLLISION, or every test below passes for the wrong reason.

    The defect needs one target owned by two different assertions, in two columns, and it is the
    shipped template and rulebook that produce that: the ``bs_s5_equity`` section's declared subtotal
    is also a template rollup. If a revision separates them, this says so here rather than letting
    the tests below quietly stop testing anything.
    """
    failed = _failed_relations()
    assert {(r["rule_id"], r["scope_key"], r["difference"]) for r in failed} == {
        ("rollup:bs_equity__total_equity", "consolidated/current", "2500"),
        ("rollup:bs_equity__total_equity", "standalone/current", "900"),
        ("section_reconciliation:bs_s5_equity", "consolidated/current", "2500"),
        ("section_reconciliation:bs_s5_equity", "standalone/current", "900"),
    }
    # ONE target under four assertions: the string the old suppression compared cannot tell any of
    # them from the equity-closing card's claim.
    assert {r["details"]["target"] for r in failed} == {"bs_equity__total_equity"}
    # Both relations are declared BLOCKING, which is what makes losing one an assurance gap rather
    # than a missing hint.
    assert {r["details"]["severity"] for r in failed} == {"blocking"}


def test_a_card_claiming_its_own_break_does_not_delete_the_two_it_makes_no_claim_about():
    """THE DEFECT, on the shipped rulebook: an equity-closing card asserting 900 deleted two
    blocking 2,500 breaks that no card reported, while the coverage band said they were reported.

    Same target, a different statement about it. The card says the equity statement closes 900 away
    from the balance sheet; the relations say total equity is 2,500 away from the components the
    template and the rulebook say it is made of. Once the bare target string matched, the 2,500 was
    on no card anywhere.
    """
    review = _served(closing=_BS_EQUITY + 900)
    equity = next(c for c in review["checks"] if c["type"] == "equity_tie")
    assert equity["target"] == "bs_equity__total_equity" and equity["delta"] == "900"

    cards = _relation_cards(review)
    assert cards.get(f"{_ROLLUP}consolidated/current") == "2,500", \
        "a blocking 2,500 rollup break is not on the queue while a card above reports 900"
    assert cards.get(f"{_SECTION}consolidated/current") == "2,500", \
        "a blocking 2,500 section-reconciliation break is not on the queue while a card reports 900"
    # …and the band agrees with the queue: nothing was suppressed, so nothing is counted as reported
    # above. One predicate answers both questions, so the two cannot disagree.
    assert review["coverage"]["failed_reported_elsewhere"] == 0


def test_a_consolidated_card_does_not_delete_a_standalone_column_break():
    """The suppression key carried no scope, while ``chk-equity-closing`` is hardcoded
    consolidated/current — so the consolidated card deleted the standalone column's breaks too.

    The standalone break is deliberately 900, the very difference the card asserts, so the COLUMN is
    the only thing that distinguishes them and this cannot pass for another reason.
    """
    review = _served(closing=_BS_EQUITY + 900)
    cards = _relation_cards(review)
    assert cards.get(f"{_ROLLUP}standalone/current") == "900", \
        "a standalone rollup break is missing from the queue; the card is about the other column"
    assert cards.get(f"{_SECTION}standalone/current") == "900", \
        "a standalone section-reconciliation break is missing from the queue"


def test_a_relation_restating_the_card_is_dropped_and_counted_by_the_same_predicate():
    """The other half of the contract: suppression must keep WORKING, and each relation it drops
    must be accounted for in the number the band prints. Otherwise the fix is a revert.

    Here the equity statement closes 2,500 away — exactly what the two consolidated relations
    report. One break, three cards, and the analyst should meet it once. The standalone pair is a
    different column and stays.
    """
    review = _served(closing=_BS_EQUITY + 2500)
    equity = next(c for c in review["checks"] if c["type"] == "equity_tie")
    assert equity["delta"] == "2,500"

    cards = _relation_cards(review)
    assert set(cards) == {f"{_ROLLUP}standalone/current", f"{_SECTION}standalone/current"}, \
        "the analyst is shown one 2,500 break three times"
    assert set(cards.values()) == {"900"}
    # The two dropped relations are exactly what the band reports as raised by a finding above.
    assert review["coverage"]["failed_reported_elsewhere"] == 2


def test_every_failed_relation_is_either_on_the_queue_or_counted_as_reported_above():
    """The invariant that keeps the count and the queue one quantity: a failed relation is served or
    it is counted, never neither and never both.

    Counted-but-served double-reports a break; dropped-but-uncounted is the reported-nowhere failure
    this file exists for. Checked on both scenarios, because the split between the buckets differs
    while the total does not.
    """
    for closing in (_BS_EQUITY + 900, _BS_EQUITY + 2500):
        review = _served(closing)
        served, counted = _relation_cards(review), \
            review["coverage"]["failed_reported_elsewhere"]
        assert len(served) + counted == len(_failed_relations()) == 4, closing


# --------------------------------------------------------------------------------------------
# The weaker question, on the calculated path: "does this line already have a card HERE at all"
# --------------------------------------------------------------------------------------------


def _two_column_assets_review() -> dict:
    """A run where the balance card claims the consolidated column and the standalone column has a
    printed subtotal its own components do not come to.

    Consolidated: total assets 1,000 against equity and liabilities 900, so ``chk-balance`` is real
    and owns ``bs_total_assets`` in consolidated/current. Standalone: total assets is PRINTED at 950
    while the only component extracted comes to 700 — a 250 break on the same template line, in the
    column the balance card never looked at.
    """
    from app.api.routes.documents import _build_review

    def row(key, consolidated=None, standalone=None):
        values = []
        if consolidated is not None:
            values.append({"basis": "consolidated", "period_label": "current",
                           "value": str(consolidated)})
        if standalone is not None:
            values.append({"basis": "standalone", "period_label": "current",
                           "value": str(standalone)})
        return {"canonical_key": key, "source_label": key, "values": values}

    rows = [row("bs_total_assets", 1000, 950),
            row("bs_non_current_assets__total_non_current_assets", None, 700),
            row("bs_total_equity_and_liabilities", 900, 950)]
    return _build_review(rows, "doc.pdf", "en", [], [], template_def=TEMPLATE)


def test_a_consolidated_card_does_not_delete_a_standalone_printed_subtotal_break():
    """The scope half of the defect, on the OTHER suppression: ``chk-balance`` is hardcoded
    consolidated/current, and the calculated path asked "is this key spoken for" without asking
    where.

    So the standalone column's 250 break between a printed subtotal and its components was dropped
    from the queue — and unlike a dropped relation it was counted by nothing, because
    ``failed_reported_elsewhere`` sums relations only. A real break, on no card, with nothing on the
    screen saying so.
    """
    review = _two_column_assets_review()
    cards = {c["id"]: c["delta"] for c in review["checks"]}
    assert cards.get("chk-balance") == "100"                    # the card that used to delete it
    assert cards.get("chk-calc-standalone-bs_total_assets") == "-250", \
        "a 250 break in the standalone column is missing from the queue and counted nowhere"
    # …and the suppression still WORKS where the card really looked: the balance card reports
    # consolidated total assets, so that column gets no second SUBTOTAL-UNVERIFIED card about it.
    # (A consolidated mismatch would survive — see the next test — but there is none here: the
    # consolidated column extracted no component to compute a total from.)
    assert "chk-uncomputed-consolidated-bs_total_assets" not in cards


def _assets_review(*, current_assets=None) -> dict:
    """One consolidated column where total assets is PRINTED at 950 above the 700 its extracted
    components come to, and the balance identity is out by a DIFFERENT 100 (950 against 1,050).

    With ``current_assets`` given, that second component is extracted too, so the template's
    ``rollup:bs_total_assets`` becomes evaluable and reports the 250 itself — the card that
    legitimately covers the calculated line's mismatch. Left out, the rollup has an unextracted
    component and is skipped, so the mismatch card is the ONLY report of the 250.
    """
    from app.api.routes.documents import _build_review
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.schemas.loader import load_ontology, load_template
    from app.services.structural_checks import evaluate_structure

    figures = {"bs_total_assets": 950,
               "bs_non_current_assets__total_non_current_assets": 700,
               "bs_total_equity_and_liabilities": 1050}
    if current_assets is not None:
        figures["bs_current_assets__total_current_assets"] = current_assets

    items = []
    for key, value in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                    basis=Basis.CONSOLIDATED, period_label="current"))
        items.append(li)
    report = evaluate_structure(load_template(TEMPLATE), items,
                                load_ontology(ONTOLOGY, resolve=True))
    rows = [{"canonical_key": key, "source_label": key,
             "values": [{"basis": "consolidated", "period_label": "current", "value": str(value)}]}
            for key, value in figures.items()]
    return _build_review(rows, "doc.pdf", "en", [],
                         [r.model_dump(mode="json") for r in report.results],
                         template_def=TEMPLATE, coverage_block=dict(_COV_BLOCK))


def test_a_card_reporting_a_different_break_does_not_delete_a_printed_subtotal_mismatch():
    """The magnitude half, on the calculated path: the balance card is about total assets in this
    column, and asking only "is this line spoken for here" deleted a break it says nothing about.

    The identity is out by 100 (950 of assets against 1,050 of equity and liabilities). The printed
    subtotal is out by 250 against the components extracted below it. Two different facts about one
    line, and the second was dropped — in no card, in no counter, and the line still counted as
    indicted because the balance card names it.
    """
    cards = {c["id"]: c["delta"] for c in _assets_review()["checks"]}
    assert cards.get("chk-balance") == "-100"                   # the card that used to delete it
    assert cards.get("chk-calc-consolidated-bs_total_assets") == "-250", \
        "a 250 break between a printed subtotal and its components is reported nowhere"


def test_the_relation_reporting_the_same_break_does_still_cover_the_mismatch():
    """…and the suppression must keep working, or the fix is a revert.

    Extract the second component and the template's own ``rollup:bs_total_assets`` becomes evaluable:
    it reports the same 250 over the same components, because a rollup relation and a calculated
    line's mismatch ARE the same arithmetic. One break, one card — while the balance card's different
    100 stays beside it.
    """
    cards = {c["id"]: c["delta"] for c in _assets_review(current_assets=0)["checks"]}
    assert cards.get("chk-structural-rollup:bs_total_assets-consolidated/current") == "250"
    assert "chk-calc-consolidated-bs_total_assets" not in cards, \
        "the same 250 break is on the queue twice"
    assert cards.get("chk-balance") == "-100"
