"""The balance-sheet identity must actually reach its operands, and say so when it cannot.

WHAT WAS WRONG. `confidence.py` checked assets = equity + liabilities against
`bs_total_assets` / `bs_total_equity_and_liabilities` — the LEGACY 183-concept rulebook's
spelling. Measured on what ships: neither key occurs in the 462-concept rulebook or the
475-definition line-item set. The live spellings are `bs_ca__total_assets` and
`bs_cl__total_equity_and_liabilities`.

So `by_key.get(_ASSETS, [])` returned an empty list on every document, the loop body never ran, no
`validation` signal was set, no `balance_mismatch` was ever raised — and nothing logged a skip.
The most basic check there is on a balance sheet was silently absent, and indistinguishable from a
filing that states no total.

That is a class of failure, not an incident: a protective rule whose operands were renamed out
from under it. The tests below pin both halves of the fix — the identity resolves against the
live key-space, AND its inability to resolve is reported rather than swallowed.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.stages.confidence import (_ASSET_KEYS, _EQ_LIAB_KEYS, ConfidenceStage,
                                   _resolve_operands)

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _row(key: str, amount: str) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key)
    li.values["current"] = ExtractedValue(value=Decimal(amount), basis=Basis.CONSOLIDATED,
                                          period_label="current")
    return li


def _run(*rows: LineItem) -> tuple[DocumentModel, PipelineContext]:
    doc = DocumentModel(document_id="d1")
    doc.line_items.extend(rows)
    ctx = PipelineContext()
    ConfidenceStage().run(doc, ctx)
    return doc, ctx


# ── the operands must exist in the model that actually ships ─────────────────────────────────────

def test_the_declared_operands_exist_in_the_shipped_line_item_set():
    """The test that would have caught the dead check.

    A protective rule naming a key nothing defines is not a harmless typo — it is a check that
    has stopped running, with no signal that it has.
    """
    seed = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    keys = {d["key"] for d in seed["items"]}

    assert any(k in keys for k in _ASSET_KEYS), \
        f"no asset operand exists in the shipped set; looked for {_ASSET_KEYS}"
    assert any(k in keys for k in _EQ_LIAB_KEYS), \
        f"no equity+liabilities operand exists in the shipped set; looked for {_EQ_LIAB_KEYS}"


def test_the_legacy_spelling_is_still_accepted():
    """The rulebook is swappable; the identity is the same identity in every generation."""
    got = _resolve_operands({"bs_total_assets": ["r"], "bs_total_equity_and_liabilities": ["r"]})

    assert got == ("bs_total_assets", "bs_total_equity_and_liabilities")


def test_the_live_spelling_is_preferred_when_both_are_present():
    got = _resolve_operands({k: ["r"] for k in (*_ASSET_KEYS, *_EQ_LIAB_KEYS)})

    assert got == (_ASSET_KEYS[0], _EQ_LIAB_KEYS[0])


def test_neither_spelling_present_resolves_to_nothing_rather_than_guessing():
    assert _resolve_operands({"bs_ca__cash_equivalents": ["r"]}) == (None, None)


# ── the identity fires, on the live spelling ─────────────────────────────────────────────────────

def test_a_balance_sheet_that_balances_is_marked_validated():
    doc, _ = _run(_row(_ASSET_KEYS[0], "1000"), _row(_EQ_LIAB_KEYS[0], "1000"))
    signals = [ev.confidence.validation for li in doc.line_items for ev in li.values.values()]

    assert signals == [1.0, 1.0]


def test_a_balance_sheet_that_does_not_balance_is_flagged():
    """This is the whole point of the check, and it has never once fired in production."""
    doc, _ = _run(_row(_ASSET_KEYS[0], "1000"), _row(_EQ_LIAB_KEYS[0], "1400"))
    flags = [f for li in doc.line_items for ev in li.values.values() for f in ev.confidence.flags]

    assert "balance_mismatch" in flags
    assert all(ev.confidence.validation == 0.4
               for li in doc.line_items for ev in li.values.values())


def test_a_difference_inside_the_tolerance_still_ties():
    """Rounding, not a mis-mapping. The tolerance is the shared reconciliation knob."""
    doc, _ = _run(_row(_ASSET_KEYS[0], "1000"), _row(_EQ_LIAB_KEYS[0], "1000.4"))
    flags = [f for li in doc.line_items for ev in li.values.values() for f in ev.confidence.flags]

    assert "balance_mismatch" not in flags


def test_the_tolerance_comes_from_the_shared_setting_not_a_private_copy():
    """`confidence` held a private `Decimal(1)` while `extraction.recon_abs_tolerance` — read at
    six other sites and exposed on the Settings screen — carried the identical default.

    A second copy is not a missing setting, it is a duplicated one: an operator who widens the
    tolerance gets it applied everywhere EXCEPT the balance sheet, which is the one place they
    were most likely thinking of.
    """
    from app.config import get_settings
    import app.stages.confidence as mod

    assert not hasattr(mod, "_TOL"), "the private tolerance copy is back"
    assert get_settings().extraction.recon_abs_tolerance is not None

    doc = DocumentModel(document_id="d1")
    doc.line_items.extend([_row(_ASSET_KEYS[0], "1000"), _row(_EQ_LIAB_KEYS[0], "1003")])
    ctx = PipelineContext()
    ctx.settings.extraction.recon_abs_tolerance = 5.0     # an operator widening it
    ConfidenceStage().run(doc, ctx)
    flags = [f for li in doc.line_items for ev in li.values.values() for f in ev.confidence.flags]

    assert "balance_mismatch" not in flags, "the shared tolerance was not honoured"


# ── and when it cannot run, it says so ───────────────────────────────────────────────────────────

def test_an_unresolvable_identity_is_logged_rather_than_skipped_silently():
    """The failure being fixed was SILENCE, not a wrong answer.

    An empty `by_key.get(...)` made a renamed operand look exactly like a filing that states no
    total, so the check's absence was unobservable from the run record.
    """
    doc, ctx = _run(_row("bs_ca__cash_equivalents", "50"))
    logged = [line for line in ctx.logs if "balance_identity_not_checked" in line]

    assert logged, f"no skip was recorded; logs were {ctx.logs}"
    assert "total_assets" in logged[0]
    assert "total_equity_and_liabilities" in logged[0]
    # The message names what it looked for, so the fix is obvious from the log alone.
    assert _ASSET_KEYS[0] in logged[0]


def test_a_document_missing_only_one_operand_still_reports_which():
    doc, ctx = _run(_row(_ASSET_KEYS[0], "1000"))
    logged = [line for line in ctx.logs if "balance_identity_not_checked" in line]

    assert logged
    assert "total_equity_and_liabilities" in logged[0]
    assert "missing=total_equity_and_liabilities" in logged[0]


def test_a_balanced_document_logs_no_skip():
    _, ctx = _run(_row(_ASSET_KEYS[0], "1000"), _row(_EQ_LIAB_KEYS[0], "1000"))

    assert not [line for line in ctx.logs if "balance_identity_not_checked" in line]
