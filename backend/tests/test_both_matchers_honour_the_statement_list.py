"""A LINE PRINTED ON TWO STATEMENTS IS CLAIMABLE ON BOTH, IN BOTH ENGINES.

NEW FILE -> backend/tests/test_both_matchers_honour_the_statement_list.py

`statements` is a list because one caption is genuinely printed on more than one statement, and
`mapping.OntologyMatcher._statements_of` states the rule in those words: "ONE MATCH IS ENOUGH now
that the declaration is a list. A concept printed on two statements is admitted under either, which
is the whole point of the multi-select."

THE OTHER ENGINE DID NOT HONOUR IT. `line_item_matching._allowed` gates through
`LineItemDef.claimable_on`, which compared the SINGULAR `self.statement`. The loader folds the
singular into the plural and leaves the singular holding one of them, so a line declaring
`[equity_changes, profit_and_loss]` was claimable only on whichever the singular resolved to.

MEASURED ON THE SHIPPED SETS, which is what makes it a defect rather than tidying: 15 of the 549 HK
lines and 16 of the 276 Ind AS lines declare two statements, and they are exactly the lines that
belong on two — every `is_oci__*` line and every `is_retained__*` appropriation. A filing prints
those in its statement of changes in equity and again in its other-comprehensive-income section,
and on a changes-in-equity page `LineItemMatcher` refused all of them while `OntologyMatcher`
admitted them. The two halves of one decision disagreeing.

The same gate is what kept Schedule III's single "Depreciation and Amortisation Expense" line from
reaching `sub__pbt_depreciation` — the concept that means the period's total depreciation, declared
on `notes` because under HKFRS the total is printed in the profit-before-tax note.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import get_settings
from app.core.models.enums import StatementType
from app.schemas.line_items import load_line_item_set
from app.services.line_item_matching import LineItemMatcher
from app.services.mapping import OntologyMatcher, normalize_statement
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
HK = TEMPLATES / "output_csv_hk_line_items.json"
INDAS = TEMPLATES / "output_csv_indas_line_items.json"


def _set(path: pathlib.Path):
    return load_line_item_set(json.loads(path.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def hk():
    return _set(HK)


@pytest.fixture(scope="module")
def indas():
    return _set(INDAS)


def test_the_shipped_sets_do_declare_two_statements(hk, indas):
    """Without this the rest of the file asserts nothing."""
    assert len([i for i in hk.items if len(i.statements or ()) > 1]) >= 15
    assert len([i for i in indas.items if len(i.statements or ()) > 1]) >= 15


def test_every_declared_statement_is_claimable(hk, indas):
    """THE DEFECT. Each statement in the list must be claimable, not just the one the singular
    happens to hold."""
    for cfg in (hk, indas):
        for item in cfg.items:
            for declared in (item.statements or ()):
                assert item.claimable_on(declared, normalize=normalize_statement), (
                    item.key, getattr(declared, "value", declared),
                    [getattr(s, "value", s) for s in item.statements])


def test_the_oci_lines_are_claimable_on_changes_in_equity(hk):
    """The concrete case, named. `changes_in_equity` is what the page classifier calls it and
    `equity_changes` is what `StatementType` spells it, so this also exercises the fold."""
    by_key = {i.key: i for i in hk.items}
    for key in ("is_oci__total_other_comprehensive_income",
                "is_oci__minimum_pension_liability_adj",
                "is_retained__cash_div_common_shares"):
        item = by_key[key]
        assert item.claimable_on("changes_in_equity", normalize=normalize_statement), key
        assert item.claimable_on("profit_and_loss", normalize=normalize_statement), key


def test_a_line_on_one_statement_is_still_refused_on_another(hk):
    """THE CONTROL. Reading the list must not make the gate permissive — a single-statement line
    keeps being refused elsewhere, which is what the gate is for."""
    by_key = {i.key: i for i in hk.items}
    item = by_key["bs_ca__inventories"]
    assert item.claimable_on("balance_sheet", normalize=normalize_statement)
    assert not item.claimable_on("cash_flow", normalize=normalize_statement)
    assert not item.claimable_on("profit_and_loss", normalize=normalize_statement)


def test_silence_is_still_permissive(hk):
    """A definition that declares no statement allows every statement — unchanged."""
    silent = [i for i in hk.items if not (i.statements or ()) and i.statement is None]
    for item in silent[:20]:
        for want in ("balance_sheet", "profit_and_loss", "cash_flow", "changes_in_equity"):
            assert item.claimable_on(want, normalize=normalize_statement), (item.key, want)


def test_both_engines_agree_on_every_shipped_line(hk, indas):
    """THE INVARIANT THIS FILE IS NAMED FOR. For each line and each statement, the two engines'
    gates must give the same verdict — that is what "the two halves of one decision" means."""
    settings = get_settings()
    for cfg in (hk, indas):
        mine = LineItemMatcher(cfg)
        theirs = OntologyMatcher(build_working_view(cfg), locale="en", settings=settings)
        for item in cfg.items:
            if item.key in mine._unmatchable:
                continue
            for want in ("balance_sheet", "profit_and_loss", "cash_flow", "changes_in_equity"):
                a = item.claimable_on(want, normalize=normalize_statement)
                b = theirs._in_statement(item.key, want)
                # `_in_statement` falls back to the KEY NAMESPACE for a concept that declares
                # nothing, which this predicate deliberately does not — so only compare where the
                # definition actually declares a statement.
                if not (item.statements or ()) and item.statement is None:
                    continue
                assert a == b, (item.key, want, a, b,
                                [getattr(s, "value", s) for s in (item.statements or ())])


def test_the_schedule_iii_depreciation_line_reaches_its_concept(indas):
    """Schedule III presents expenses by nature, so the whole period's depreciation and
    amortisation is ONE face line. `sub__pbt_depreciation` is the concept that means that total —
    declared on `notes` as well, because under HKFRS it is printed in the profit-before-tax note."""
    matcher = LineItemMatcher(indas)
    for caption in ("Depreciation and Amortisation Expense",
                    "Depreciation and Amortization Expense",
                    "Depreciation & Amortisation Expense"):
        hit = matcher.match(caption, "profit_and_loss", "is_pl")
        assert hit.key == "sub__pbt_depreciation", (caption, hit.key, hit.reason)

    item = {i.key: i for i in indas.items}["sub__pbt_depreciation"]
    assert StatementType.PROFIT_AND_LOSS in (item.statements or ())
    assert StatementType.NOTES in (item.statements or ())
    # Route SILENCE, so the face and the note are both open to it. `face` would close the note and
    # `anywhere` would widen extraction to all 293 pages of an integrated report for one line.
    assert not item.route, item.route
