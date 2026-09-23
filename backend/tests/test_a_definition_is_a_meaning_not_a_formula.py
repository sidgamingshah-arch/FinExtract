"""A LINE'S `definition` SAYS WHAT IT MEANS. IT IS NOT WHERE THE ARITHMETIC LIVES.

NEW FILE -> backend/tests/test_a_definition_is_a_meaning_not_a_formula.py

30 of the 33 `calculated` items defined themselves with the spreadsheet formula they were converted
from, and one `extracted` item did too:

    bs_ca__total_current_assets
      "Calculated target using the exact formula retained in the supplied template:
       ={bs_ca__operating_lease_receivables}+{bs_ca__inventories}+SUM({bs_ca__du..."
    bs_equity__total_equity_and_reserves
      "... =B126+{bs_equity__minority_interest_equity}"

TWO LIVE READERS, which is what made it a defect rather than untidiness:

  * `services/line_item_llm.py` puts `definition` in the request payload — one of the seven keys a
    line carries — so every mapping call told the model that "Total current assets" is a formula
    over internal keys and a spreadsheet cell. That says nothing about which caption to find, and
    invites the model to satisfy an equation instead of locating a printed row.
  * `LineItemDef` returns `self.definition or self.label or self.key` as the line's meaning text,
    which `line_item_notes._blended` uses as the fallback NOTE-SELECTION PROBE. So `SUM`, `B126`
    and `bs_ca__inventories` became vocabulary scored against note headings — and a calculated item
    declares no `note_source`, so the blend is the only probe it has.

AND THE FORMULA WAS REDUNDANT: all 33 carry `cascade: 0`, because the arithmetic is declared in the
TEMPLATE's `rollup` on the same concept. The definition was a second, prose copy of a declaration
that already exists where the row builder reads it.

WHAT THIS DOES NOT ASSERT: that a subtotal is never asked about. It should be —
`line_item_requests.asked_about` documents why, for `extraction_mode: extract_or_derive`: "the
subtotal is printed on some filings and arithmetic on others, and those ARE asked about". The fix
was never to stop asking; it was to tell the model the caption instead of the equation.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.schemas.line_items import load_line_item_set

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SHIPPED = sorted(TEMPLATES.glob("output_csv_*_line_items.json"))

# What a formula looks like in a prose field: an `=` opening a SUM/reference/cell, a `{key}`
# reference to another line, a bare spreadsheet cell, or the boilerplate the conversion left.
FORMULA = re.compile(
    r"=\s*(?:SUM\(|\{|[A-Z]{1,2}\d+)"      # ={key}, =SUM(, =B126
    r"|\{[a-z_]+__[a-z0-9_]*\}"            # a {canonical_key} reference anywhere
    r"|\bSUM\("                            # SUM( anywhere
    r"|exact formula retained",            # the conversion's own sentence
    re.IGNORECASE)


def _items(path: pathlib.Path):
    return load_line_item_set(json.loads(path.read_text(encoding="utf-8")), resolve=True).items


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_no_shipped_definition_carries_a_formula(path):
    """Every shipped set, not just the one this was found in — the conversion that produced these
    ran over whichever file it was pointed at, so the property belongs to all of them."""
    offenders = []
    for item in _items(path):
        text = item.definition or ""
        if FORMULA.search(text):
            offenders.append(f"{item.key}: {text[:90]}")
    assert not offenders, offenders[:10]


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_a_computed_line_still_says_what_it_is(path):
    """REMOVING THE FORMULA MUST NOT LEAVE THE FIELD EMPTY.

    A calculated line is still asked about, so an empty definition would be a worse answer than the
    formula: the model would be asked to locate a line it has been told nothing about. Whatever
    replaces the formula has to be prose long enough to identify the caption.
    """
    thin = []
    for item in _items(path):
        if str(item.type or "") != "calculated":
            continue
        text = (item.definition or "").strip()
        if len(text) < 40 or "{" in text:
            thin.append(f"{item.key}: {text[:60]!r}")
    assert not thin, thin[:10]


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_the_arithmetic_is_declared_where_the_row_builder_reads_it(path):
    """THE REASON THE FORMULA COULD JUST GO, rather than needing to move somewhere.

    A `calculated` concept's sum is the TEMPLATE's `rollup`. This asserts the template really does
    declare one for each of them, so deleting the prose copy loses nothing — if a calculated line
    had no rollup, the formula in its definition would have been the only statement of its
    arithmetic and this test would fail rather than let it be deleted.
    """
    template = TEMPLATES / path.name.replace("_line_items.json", "_v1_template.json")
    if not template.exists():
        pytest.skip(f"no template beside {path.name}")
    rollups: set[str] = set()

    def collect(node) -> None:
        if isinstance(node, dict):
            key = node.get("canonical_key")
            if isinstance(key, str) and key and isinstance(node.get("rollup"), dict):
                rollups.add(key)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(json.loads(template.read_text(encoding="utf-8")).get("statements"))

    calculated = {i.key for i in _items(path) if str(i.type or "") == "calculated"}
    # A calculated concept the template does not present at all has no rollup to carry its
    # arithmetic and no row to render — out of scope for this assertion, not a violation of it.
    presented: set[str] = set()

    def keys(node) -> None:
        if isinstance(node, dict):
            key = node.get("canonical_key")
            if isinstance(key, str) and key:
                presented.add(key)
            for value in node.values():
                keys(value)
        elif isinstance(node, list):
            for value in node:
                keys(value)

    keys(json.loads(template.read_text(encoding="utf-8")).get("statements"))
    without = sorted((calculated & presented) - rollups)
    assert not without, without
