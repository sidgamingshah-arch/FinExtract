"""A line whose value is WORDS must pass through the arithmetic untouched, not crash it or join it.

WHY THIS FILE IS THE LOAD-BEARING TEST OF `output_structure`. The design rests on one claim: that
because a text fact leaves `ExtractedValue.value` as None, every site that sums, subtracts or
compares figures skips it by the guard it already has. That claim is what allowed a 139-site survey
to be answered with ONE new field instead of 139 edits — so if it is wrong anywhere, it is wrong
silently, and the symptom is a wrong number in a credit report rather than an error.

An argument is not evidence, so the claim is executed here. Each test drives a real service over a
document holding a text-valued row beside ordinary figures, and asserts BOTH halves:

  * the arithmetic does not raise, and
  * the text row contributes NOTHING — the total is the same as if the row were not there at all.

The second half is the one that matters. A crash is loud and gets fixed. A text row silently
counted as 0 leaves every total still balancing, with nothing anywhere saying a line was skipped.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, Provenance


def _figure(amount: str, *, period: str = "current") -> ExtractedValue:
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                          value=Decimal(amount), value_raw=Decimal(amount),
                          provenance=Provenance(page_index=1))


def _words(text: str, *, period: str = "current") -> ExtractedValue:
    """A text fact, shaped exactly as the pipeline will produce one: `value` and `value_raw` stay
    None, which is the whole mechanism by which the arithmetic skips it."""
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                          value_text=text, provenance=Provenance(page_index=1))


def _row(key: str, label: str, ev: ExtractedValue, *, role: LineRole = LineRole.LINE) -> LineItem:
    li = LineItem(source_label=label, canonical_key=key, role=role)
    li.set_value(ev)
    return li


def _doc(*rows: LineItem) -> DocumentModel:
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = list(rows)
    return doc


def test_a_text_fact_leaves_the_numeric_fields_alone():
    """The mechanism itself, stated once. If a text fact ever carried a `value`, every guard
    downstream would let it into the arithmetic and the rest of this file would be meaningless."""
    ev = _words("Unqualified opinion")
    assert ev.value is None and ev.value_raw is None and ev.reconciled is None
    assert ev.value_text == "Unqualified opinion"


def test_a_phrase_that_reads_like_a_number_cannot_also_be_a_number():
    """THE FABRICATED-FIGURE CASE, and the reason the invariant is enforced rather than assumed.

    Pydantic parses `Decimal` in lax mode, so a phrase LIFTED FROM THE PAGE whose text happens to
    read "2024" is accepted into `value` as a number. From there it is indistinguishable from a
    figure: summed into subtotals, negated by the unsigned-expense pass, subtracted in the
    note-to-face tie. An audit-opinion year would become a financial figure, and every total
    containing it would still balance — so nothing downstream could ever report it.

    Making the two states mutually exclusive at CONSTRUCTION is what turns "a text fact leaves
    `value` as None" from a convention every producer must remember into a property of the type.
    """
    with pytest.raises(ValueError, match="must carry no figure"):
        ExtractedValue(basis=Basis.CONSOLIDATED, value_text="2024", value=Decimal("2024"))
    with pytest.raises(ValueError, match="must carry no figure"):
        ExtractedValue(basis=Basis.CONSOLIDATED, value_text="1,000", value_raw=Decimal("1000"))
    with pytest.raises(ValueError, match="must carry no figure"):
        ExtractedValue(basis=Basis.CONSOLIDATED, value_text="Qualified",
                       reconciled=Decimal("5"))


def test_words_cannot_claim_their_sign_was_flipped():
    """`sign_normalised` records the one place the engine changes a reported number's sign. A
    sentence has no sign, so the flag on a text fact asserts an audit trail for a transformation
    that cannot have happened."""
    with pytest.raises(ValueError, match="no sign to flip"):
        ExtractedValue(basis=Basis.CONSOLIDATED, value_text="Qualified", sign_normalised=True)


def test_the_ordinary_facts_are_all_still_legal():
    """The guard must refuse only the incoherent pair — a figure, words alone, and an empty slot
    are each a real state the pipeline produces."""
    ExtractedValue(basis=Basis.CONSOLIDATED, value=Decimal("400"), value_raw=Decimal("400"))
    ExtractedValue(basis=Basis.CONSOLIDATED, value_text="Unqualified opinion")
    ExtractedValue(basis=Basis.CONSOLIDATED)


def test_a_text_fact_is_not_a_figure_to_the_one_function_that_answers_that():
    """`structural_checks._printed` is the MODEL door — the single place the structural checks read
    a figure off an ExtractedValue. Its guard is what makes 45 reported crash sites safe."""
    from app.services import structural_checks

    printed = getattr(structural_checks, "_printed", None)
    if printed is None:
        pytest.skip("structural_checks._printed has been renamed; update this probe")
    assert printed(_words("Going concern basis")) is None
    assert printed(_figure("100")) == Decimal("100")


def test_a_text_row_contributes_nothing_to_a_section_total():
    """THE SILENT-FAILURE TEST. A prose row sitting in a section must not be counted as zero — it
    must not be counted at all, and the total must equal the sum of the real figures."""
    from app.services.structural_checks import collect_values

    with_text = collect_values([
        _row("bs_ca__inventories", "Inventories", _figure("400")),
        _row("bs_ca__cash_in_hand_and_at_banks", "Cash", _figure("600")),
        _row("notes__audit_opinion", "Audit opinion", _words("Unqualified")),
    ])
    without = collect_values([
        _row("bs_ca__inventories", "Inventories", _figure("400")),
        _row("bs_ca__cash_in_hand_and_at_banks", "Cash", _figure("600")),
    ])
    # `MappedValues.values` is keyed by canonical_key. The text concept must not appear there AT
    # ALL — not as 0, which would let a section total balance while a line had been dropped.
    assert "notes__audit_opinion" not in with_text.values, (
        f"a text row was collected as a figure: {with_text.values.get('notes__audit_opinion')}")
    # And the figures that ARE there are byte-identical to the run without the text row, so the
    # text row changed nothing about the arithmetic rather than merely not crashing it.
    assert with_text.values == without.values
    assert with_text.contributors == without.contributors


def test_the_component_assembler_does_not_treat_words_as_an_addend():
    """`assemble_components` sums the rows the model declared components of one line. A text row
    reaching it would be `Decimal("Unqualified")`."""
    from app.services.assemble_components import assemble

    doc = _doc(
        _row("notes__audit_opinion", "Audit opinion", _words("Unqualified")),
        _row("bs_ca__inventories", "Inventories", _figure("400")),
    )
    filled = assemble(doc)          # must not raise
    assert isinstance(filled, int)


def test_a_text_row_is_still_a_statement_line():
    """`review_lines.is_statement_line` decides which rows the review header counts, off `role`
    alone. A text row is a real line — it must be counted, not filtered out as a caption, or a
    prose line would silently never reach review."""
    from app.services.review_lines import is_statement_line

    assert is_statement_line({"role": "line", "value": "Unqualified"}) is True
    assert is_statement_line({"role": "header"}) is False


def test_the_row_dict_door_coerces_words_to_nothing_rather_than_to_zero():
    """THE SECOND DOOR. A value reaches the arithmetic two ways: off the model (`_printed`, above)
    and through a serialised ROW DICT, where `periods._num` and its clones coerce with `float()`.

    `_num` must answer None for words — not raise, and not 0.0. None means "no figure here" and is
    what every summation already skips; 0.0 would be counted, and a section total that includes a
    zero for an audit opinion still balances, which is exactly the failure nobody would see.
    """
    from app.services.periods import _num

    assert _num("Unqualified opinion") is None
    assert _num(None) is None
    assert _num("1,234") == 1234.0        # the thousands separator still parses


def test_the_grid_resolver_ignores_a_text_row(monkeypatch):
    """`rollups.figures_as_shown` is the single resolver the grid, the export and the structural
    checks all read. It takes ROW DICTS, so this is the row-dict door end to end."""
    from app.services.rollups import figures_as_shown

    rows = [
        {"canonical_key": "bs_ca__inventories", "role": "line",
         "values": [{"basis": "consolidated", "period_label": "current", "value": "400"}]},
        {"canonical_key": "notes__audit_opinion", "role": "line",
         "values": [{"basis": "consolidated", "period_label": "current",
                     "value": "Unqualified opinion"}]},
    ]
    out = figures_as_shown(None, rows, "consolidated", "current")
    assert out.get("bs_ca__inventories") == 400.0
    # The text concept must be absent, not present as 0.0.
    assert "notes__audit_opinion" not in out or out["notes__audit_opinion"] is None


def test_the_two_stages_that_do_the_most_arithmetic_run_clean_over_a_text_row():
    """THE CLAIM, AT FULL STAGE SCALE. The 139-site survey named `stages/normalize.py` and
    `stages/residual.py` as carrying most of the reported crash sites — the per-row sign pass, the
    unsigned-expense unanimity vote, the section sweep, the residual reconciliation.

    Every one of those claims assumed the text would arrive IN `value_raw`, because the survey ran
    before the representation was chosen. With text in its own field those sites are gated by the
    `raw is None` check they already had, so this runs the REAL stages and asserts three things at
    once: neither raises, the text fact is still there afterwards, and the figures beside it are
    bit-for-bit what they were. The third is what rules out the silent failure — a stage that
    "handled" the text row by folding it into a total would pass the first two.
    """
    from app.config import get_settings
    from app.core.stage import PipelineContext
    from app.stages.normalize import NormalizeStage
    from app.stages.residual import ResidualStage

    doc = _doc(
        _row("is_pl__sales_revenues", "Revenue", _figure("1000")),
        _row("is_pl__cost_of_sales", "Cost of sales", _figure("400")),
        _row("notes__audit_opinion", "Audit opinion", _words("Unqualified opinion")),
    )
    ctx = PipelineContext(settings=get_settings())
    for stage in (NormalizeStage(), ResidualStage()):
        doc = stage.run(doc, ctx)

    texts = [v.value_text for li in doc.line_items for v in li.values.values() if v.value_text]
    assert texts == ["Unqualified opinion"], "the text fact did not survive the stages"
    figures = {li.canonical_key: str(v.value)
               for li in doc.line_items for v in li.values.values() if v.value is not None}
    assert figures == {"is_pl__sales_revenues": "1000", "is_pl__cost_of_sales": "400"}


def test_a_text_fact_survives_a_round_trip_through_the_model():
    """It has to reach the export and the screens, so it must serialise — the point of the field is
    that the value is CARRIED while being kept out of the arithmetic."""
    row = _row("notes__audit_opinion", "Audit opinion", _words("Unqualified opinion"))
    again = LineItem.model_validate_json(row.model_dump_json())
    texts = [v.value_text for v in again.values.values()]
    assert texts == ["Unqualified opinion"]
