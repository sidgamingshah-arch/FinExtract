"""The statements a template may declare, and the ones a given template actually does.

ONE LIST, TWO READERS. The workbook importer needs the display titles (its `Statement` column is
typed by a human), and the API needs to tell the front end which statements a run's spread contains.
Both were about to hold their own copy — the importer already did — and a screen offering a tab the
importer would refuse is the kind of disagreement nobody notices until an analyst files a support
ticket about an empty grid.

WHY THE FRONT END IS NOT SENT LABELS. It has `ws.stmt.*` translated in all four shipped locales, so
sending an English title would either be ignored or, worse, used — putting "Balance sheet" into an
Arabic UI. Keys travel; the words stay where the words live. ``TITLES`` is for the workbook, which
is authored in English by an admin, and for a caller with no dictionary of its own.
"""
from __future__ import annotations

from app.services.mapping import normalize_statement

# Every statement a template may declare, in the order a set of financial statements is read.
# A template that declares a subset gets that subset, in this order; nothing else is accepted by
# `template_xlsx.import_workbook`, so this is the whole vocabulary.
TITLES: dict[str, str] = {
    "balance_sheet": "Balance sheet",
    "profit_and_loss": "Profit & loss",
    "cash_flow": "Cash flow",
    "changes_in_equity": "Changes in equity",
}
ORDER: tuple[str, ...] = tuple(TITLES)


def declared_statements(definition: dict | None) -> list[dict]:
    """``[{key, title, sections}]`` for the statements a template definition actually declares.

    ORDER IS THE TEMPLATE'S OWN, not this module's: the template is the authority on presentation —
    the same rule the rulebook's concept order follows — so a template that prints its income
    statement first is served that way. `ORDER` only breaks a tie for entries the template leaves
    unordered, which is nothing today but costs nothing to be right about.

    A statement declaring no section is dropped. It cannot render a grid, and offering a tab that
    can only ever be empty is worse than not offering it: the analyst cannot tell "this filing did
    not state it" from "this template has nothing to put there".
    """
    out: list[dict] = []
    for st in ((definition or {}).get("statements") or []):
        if not isinstance(st, dict):
            continue
        # Folded to the canonical spelling, because a definition stores whatever
        # ``StatementType`` validated it to — ``equity_changes`` — while the classifier, the
        # workbook column and the client all say ``changes_in_equity``. One statement, one key
        # leaving this function; see ``mapping.normalize_statement``, which owns the fold.
        key = normalize_statement(str(st.get("type") or "").strip())
        if not key or key not in TITLES:
            continue
        sections = [s for s in (st.get("sections") or []) if isinstance(s, dict)]
        if not sections:
            continue
        if any(o["key"] == key for o in out):        # a malformed definition naming one twice
            continue
        out.append({"key": key, "title": TITLES[key], "sections": len(sections)})
    return out
