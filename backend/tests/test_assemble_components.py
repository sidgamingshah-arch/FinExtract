"""Several printed rows may be one line item's figure — but only when the model says so.

WHY THIS EXISTS. A line item's amount is often the sum of several printed rows: a note that splits
depreciation by function prints four, and all four belong on the operating-expense line. Before
this, two rows landing on one concept was an ``ambiguous_mapping`` and the line went unfilled,
because that flag cannot tell a genuine component set from a face line repeating the note total
behind it.

THE TWO CASES ARE INDISTINGUISHABLE AFTERWARDS, which is the whole reason the model is asked to
declare which one it is. Both produce a bigger number, on a statement that still balances because
the parent's own row was consumed. So:

* a row the model marked ``role: "component"`` is summed, and each contribution carries its page,
  its caption and the model's own reason for including it;
* two rows that did NOT declare themselves components stay ambiguous, exactly as before;
* a declared component sitting beside a row that states the whole figure is REFUSED and reported,
  because choosing either reading would invent a total the filing does not print.

The trail is written through ``services.derivation``, which is what the statement inspector
already renders as contributions with click-to-source — so the traceback is the one the product
already knows how to show.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.assemble_components import assemble


def _row(label: str, key: str, amount: str, *, component_of: str | None = None,
         sign: int = 1, reason: str | None = None, note: str | None = None,
         basis: str = "consolidated", period: str = "current") -> LineItem:
    item = LineItem(source_label=label, canonical_key=key, ordinal=0, note_number=note)
    item.values = [ExtractedValue(value=Decimal(amount), basis=basis, period_label=period)]
    if component_of:
        item.confidence.flags.append(f"component_of:{component_of}")
        item.confidence.flags.append(f"component_sign:{sign}")
    if reason:
        item.confidence.flags.append(f"llm_reason:{reason}")
    return item


def _doc(*rows: LineItem) -> DocumentModel:
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = list(rows)
    return doc


KEY = "is_pl__deprec_and_impairment_oper_exp"


# ── the case this was built for ──────────────────────────────────────────────────────────────────

def test_declared_components_are_summed_onto_one_line():
    """The note splits the total by function; every part belongs on the one line."""
    doc = _doc(
        _row("R&D depreciation", KEY, "100", component_of=KEY, note="12"),
        _row("Selling & marketing depreciation", KEY, "40", component_of=KEY, note="12"),
        _row("G&A depreciation", KEY, "60", component_of=KEY, note="14"),
    )

    filled = assemble(doc)

    assert filled == 1, filled
    target = doc.line_items[0]
    assert target.values[0].value == Decimal("200")
    assert target.is_computed is True
    assert "assembled_from:3" in target.confidence.flags


def test_a_subtracted_component_is_subtracted():
    """A cascade spelled "the wider disclosure LESS the cost-of-sales share" consumes an input
    negatively, and a contributions list showing it positive does not add up to the figure."""
    doc = _doc(
        _row("Total depreciation", KEY, "250", component_of=KEY),
        _row("Depreciation in cost of sales", KEY, "70", component_of=KEY, sign=-1),
    )

    assemble(doc)

    assert doc.line_items[0].values[0].value == Decimal("180")


def test_each_contribution_carries_where_why_and_the_arithmetic():
    """The three things asked for, and each from the source that can actually supply it.

    WHERE comes off the ROW's own note and caption — never from the model, which was given no
    bounding box, so a location it stated would be a citation that looks authoritative and points
    at the wrong place. WHY is the model's sentence for that row, the one part of the trace only it
    can supply. THE ARITHMETIC is the value, the sign and the total.
    """
    doc = _doc(
        _row("R&D depreciation", KEY, "100", component_of=KEY, note="12",
             reason="note 12 splits depreciation by function; R&D is an operating expense"),
        _row("G&A depreciation", KEY, "60", component_of=KEY, note="14",
             reason="general and administrative depreciation is an operating expense"),
    )

    assemble(doc)

    trail = doc.line_items[0].derivation["consolidated:current"]
    assert trail["method"] == "assembled:declared_components"
    assert trail["result"] == "160"
    assert trail["flags"] == ["components:2"]
    assert len(trail["inputs"]) == 2

    first = trail["inputs"][0]
    assert first["label"] == "R&D depreciation"          # where — the caption as printed
    assert first["note"] == "12"                          # where — the note it came from
    assert first["value"] == "100"                        # the arithmetic
    assert first["deducted"] is False                     # the sign
    assert "splits depreciation by function" in first["excerpt"]   # why


# ── and the protection it must not remove ───────────────────────────────────────────────────────

def test_two_undeclared_rows_on_one_key_are_left_alone():
    """The pre-existing protection. A face line and the note total behind it are the SAME figure
    printed twice; summing them overstates the line and the statement still balances. Rows that
    did not declare themselves components are not touched here, so they remain the
    `ambiguous_mapping` the structural checks already report."""
    doc = _doc(
        _row("Depreciation and impairment", KEY, "500"),
        _row("Depreciation and impairment", KEY, "500"),
    )

    filled = assemble(doc)

    assert filled == 0
    assert [v.values[0].value for v in doc.line_items] == [Decimal("500"), Decimal("500")]
    assert not any("assembled_from" in f for f in doc.line_items[0].confidence.flags)


def test_a_component_beside_a_stated_total_is_refused_and_reported():
    """The model read the same money twice. Neither reading is chosen.

    Adding the component to the stated total overstates the line; discarding it assumes the total
    is complete. Both invent a figure the filing does not print, so the printed one stands and the
    conflict is flagged on the row — a reviewer opening it sees why nothing was assembled.
    """
    doc = _doc(
        _row("Depreciation and impairment", KEY, "500"),
        _row("R&D depreciation", KEY, "100", component_of=KEY),
    )

    filled = assemble(doc)

    assert filled == 0
    flags = doc.line_items[1].confidence.flags
    assert f"component_beside_stated_total:{KEY}" in flags
    assert "low_mapping_confidence" in flags


def test_components_are_summed_within_one_period_not_across():
    """A component set is a sum within one column. Mixing this year's rows with last year's would
    produce a figure the filing states in neither."""
    doc = _doc(
        _row("R&D depreciation", KEY, "100", component_of=KEY),
        _row("G&A depreciation", KEY, "60", component_of=KEY),
        _row("R&D depreciation", KEY, "90", component_of=KEY, period="prior"),
        _row("G&A depreciation", KEY, "50", component_of=KEY, period="prior"),
    )

    assemble(doc)

    target = doc.line_items[0]
    assert target.derivation["consolidated:current"]["result"] == "160"
    assert target.derivation["consolidated:prior"]["result"] == "140"


def test_a_filing_that_prints_every_line_directly_assembles_nothing():
    """Zero is the ordinary answer, and it must not look like a stage that failed to run."""
    doc = _doc(_row("Inventories", "bs_ca__inventories", "42"))

    assert assemble(doc) == 0
    assert doc.line_items[0].derivation is None
