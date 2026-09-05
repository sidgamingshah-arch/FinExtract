"""``sign_convention`` is an expectation, and ``temporality``/``unit_of_account`` are an identity.

Three declarations meet in the normalize stage, and they are easy to confuse with each other:

* ``sign_rule`` NORMALISES — "Less:" makes a figure negative, and the ontology author's
  ``flip_if_label_matches`` regexes correct a specific caption. It changes values, and it keeps
  doing so.
* ``sign_convention`` EXPECTS — the rulebook's own words: "a concept whose sign_convention is
  positive_expected or negative_expected but whose loaded value carries the opposite sign is a
  review trigger, not an auto-correction". Silently flipping such a figure would hide the reason it
  arrived wrong (almost always: the row is on the wrong concept) behind a plausible number, while
  the template's subtotal identities quietly stopped meaning anything.
* ``temporality`` + ``unit_of_account`` IDENTIFY the fact — a position at a date or a movement over
  a period. The balance-sheet non-controlling-interests BALANCE and the P&L attribution FLOW share
  their caption word for word, so nothing in the caption stops the period flow being filed on the
  balance sheet, where it is individually plausible and every subtotal still ties. They are
  different facts and must never merge.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology
from app.stages.normalize import NormalizeStage

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def raw_ontology() -> dict:
    return json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


def _ontology(raw: dict):
    return load_ontology(copy.deepcopy(raw), resolve=True)


def _row(label: str, key: str, value: int, statement: str) -> tuple[DocumentModel, LineItem]:
    li = LineItem(source_label=label, canonical_key=key)
    li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                basis=Basis.CONSOLIDATED, period_label="current",
                                provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf", locale="en")
    doc.pages = [PageSource(index=0, statement=statement)]
    doc.line_items = [li]
    return doc, li


def _run(doc: DocumentModel, ontology) -> PipelineContext:
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = ontology
    NormalizeStage().run(doc, ctx)
    return ctx


def _value(li: LineItem) -> ExtractedValue:
    return next(iter(li.values.values()))


# --- sign_convention: expected, never coerced --------------------------------------------------

def test_an_expense_arriving_positive_is_flagged_and_left_alone(raw_ontology):
    doc, li = _row("Cost of sales", "pl_expenses__cost_of_goods_sold", 500, "profit_and_loss")
    _run(doc, _ontology(raw_ontology))

    assert _value(li).value == Decimal(500)            # NOT flipped to -500
    assert "sign_opposite_to_expected:negative_expected" in _value(li).confidence.flags
    assert f"sign_opposite_to_expected:{li.canonical_key}" in li.confidence.flags
    assert _value(li).confidence.sign == pytest.approx(0.35)
    assert li.canonical_key == "pl_expenses__cost_of_goods_sold"


def test_the_expectation_is_the_one_the_rulebook_declares(raw_ontology):
    """Editing the section's ``sign_convention`` to "either" has to stop the finding — otherwise
    the field is decoration and the engine is running on a hardcoded opinion of its own."""
    raw = copy.deepcopy(raw_ontology)
    raw["section_defaults"]["pl_s2_expenses"]["sign_convention"] = "either"
    doc, li = _row("Cost of sales", "pl_expenses__cost_of_goods_sold", 500, "profit_and_loss")
    _run(doc, _ontology(raw))

    assert not any(f.startswith("sign_opposite_to_expected") for f in li.confidence.flags)
    assert _value(li).confidence.sign == pytest.approx(1.0)


def test_an_asset_arriving_negative_is_flagged(raw_ontology):
    doc, li = _row("Inventories", "bs_current_assets__inventories", -80, "balance_sheet")
    _run(doc, _ontology(raw_ontology))
    assert "sign_opposite_to_expected:positive_expected" in _value(li).confidence.flags
    assert _value(li).value == Decimal(-80)


def test_a_figure_with_the_expected_sign_is_not_flagged(raw_ontology):
    doc, li = _row("Inventories", "bs_current_assets__inventories", 80, "balance_sheet")
    _run(doc, _ontology(raw_ontology))
    assert li.confidence.flags == []
    assert _value(li).confidence.sign == pytest.approx(1.0)


def test_sign_rule_still_normalises_and_the_expectation_only_judges_the_result(raw_ontology):
    """"Less: cost of sales" printed unsigned is normalised to negative by ``sign_rule`` — and the
    expectation then has nothing to report. The two declarations do different jobs, and the older
    one keeps its job."""
    doc, li = _row("Less: cost of sales", "pl_expenses__cost_of_goods_sold", 500,
                   "profit_and_loss")
    _run(doc, _ontology(raw_ontology))

    assert _value(li).value == Decimal(-500)
    assert not any(f.startswith("sign_opposite_to_expected") for f in li.confidence.flags)


# --- paren_means_negative: removed, not reported ------------------------------------------------

def test_the_duplicate_paren_switch_is_gone_from_the_schema_and_the_rulebooks():
    """The field stated the same rule as ``number_format_by_locale[<locale>].negative`` — which is
    the one the parser reads, at EXTRACTION. So the global copy could never be honoured afterwards:
    the printed text is not retained, and a figure already negated cannot be told apart from one
    printed with a minus.

    It was previously answered with a contradiction DETECTOR that logged the disagreement and marked
    the negatives unverified. That was better than pretending, but it left an author a switch to
    flip and a warning as the reward. Deleting it leaves parentheses decided in exactly one place,
    and an author who now types the key gets a 422 naming it rather than a no-op.
    """
    import json
    from pathlib import Path

    from app.schemas.loader import load_ontology, unknown_keys
    from app.schemas.ontology import GlobalRules

    assert "paren_means_negative" not in GlobalRules.model_fields

    d = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    for f in ("hkfrs_hk_china_ontology.json", "hkfrs_hk_china_ontology.json"):
        raw = json.loads((d / f).read_text(encoding="utf-8"))
        assert "paren_means_negative" not in (raw.get("global_rules") or {}), f
        assert unknown_keys(raw, load_ontology(raw)) == [], f

    # The live switch still works: the locale's number format is what decodes a parenthesis.
    from app.services.numbers import parse_number

    fmt = load_ontology(
        json.loads((d / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))).number_format("en")
    assert any("paren" in str(m).lower() for m in fmt.negative)
    got = parse_number("(600)", fmt)
    assert got.ok and got.value == Decimal(-600) and got.is_negative_paren


def test_declaring_the_removed_switch_is_now_refused_at_the_door(raw_ontology):
    """The point of removing it rather than ignoring it: the mistake becomes visible."""
    from app.schemas.loader import load_ontology, unknown_keys

    raw = copy.deepcopy(raw_ontology)
    raw["global_rules"]["paren_means_negative"] = False
    assert "global_rules.paren_means_negative" in unknown_keys(raw, load_ontology(raw))


# --- temporality / unit_of_account: the NCI guard ----------------------------------------------

def test_the_pl_attribution_flow_is_never_filed_as_the_equity_balance(raw_ontology):
    """"Non-controlling interests" on the profit-and-loss face, mapped to the equity BALANCE. The
    figure is plausible, the equity total still ties, and the balance sheet is wrong by a year's
    profit attribution. The row keeps its value and provenance and loses the concept."""
    doc, li = _row("Non-controlling interests", "bs_equity__non_controlling_interests", 40,
                   "profit_and_loss")
    ctx = _run(doc, _ontology(raw_ontology))

    assert li.canonical_key is None
    assert _value(li).value == Decimal(40)             # the figure is not destroyed
    flag = next(f for f in li.confidence.flags if f.startswith("balance_flow_confusion:"))
    assert "temporality:instant!=duration" in flag
    assert "unit_of_account:balance!=flow" in flag
    assert "low_mapping_confidence" in li.confidence.flags
    assert any("balance_flow_confusion" in line for line in ctx.logs)


def test_the_same_caption_on_the_balance_sheet_is_left_exactly_where_it_is(raw_ontology):
    doc, li = _row("Non-controlling interests", "bs_equity__non_controlling_interests", 40,
                   "balance_sheet")
    _run(doc, _ontology(raw_ontology))
    assert li.canonical_key == "bs_equity__non_controlling_interests"
    assert not any(f.startswith("balance_flow_confusion") for f in li.confidence.flags)


def test_the_finding_follows_the_declared_temporality_and_unit(raw_ontology):
    """Declare the equity section a duration flow and the same row stops being a confusion — the
    engine is reading the rulebook, not a built-in table of which statement is which."""
    raw = copy.deepcopy(raw_ontology)
    raw["section_defaults"]["bs_s5_equity"]["temporality"] = "duration"
    raw["section_defaults"]["bs_s5_equity"]["unit_of_account"] = "flow"
    doc, li = _row("Non-controlling interests", "bs_equity__non_controlling_interests", 40,
                   "profit_and_loss")
    _run(doc, _ontology(raw))

    assert li.canonical_key == "bs_equity__non_controlling_interests"
    assert not any(f.startswith("balance_flow_confusion") for f in li.confidence.flags)


def test_unit_of_account_alone_is_enough_to_refuse_the_merge(raw_ontology):
    """With the temporality agreeing, the balance-versus-flow difference is the whole finding."""
    raw = copy.deepcopy(raw_ontology)
    raw["section_defaults"]["bs_s5_equity"]["temporality"] = "duration"
    doc, li = _row("Non-controlling interests", "bs_equity__non_controlling_interests", 40,
                   "profit_and_loss")
    _run(doc, _ontology(raw))

    assert li.canonical_key is None
    flag = next(f for f in li.confidence.flags if f.startswith("balance_flow_confusion:"))
    assert "unit_of_account:balance!=flow" in flag and "temporality" not in flag


def test_a_subtotal_is_not_compared_on_units(raw_ontology):
    """``unit_of_account: subtotal`` is neither a balance nor a flow, so a printed total would look
    foreign on every statement it appears on if it were compared."""
    doc, li = _row("Total assets", "bs_total_assets", 900, "balance_sheet")
    _run(doc, _ontology(raw_ontology))
    assert li.canonical_key == "bs_total_assets"
    assert li.confidence.flags == []


def test_a_statement_the_rulebook_says_nothing_about_yields_no_finding(raw_ontology):
    """The statement of changes in equity carries no concepts of its own here, so there is no
    expectation to test a row against — and an absent declaration must not become a guess."""
    doc, li = _row("Non-controlling interests", "bs_equity__non_controlling_interests", 40,
                   "equity_changes")
    _run(doc, _ontology(raw_ontology))
    assert li.canonical_key == "bs_equity__non_controlling_interests"


# --- sign_convention.unsigned_source: the one transformation, and its boundary -------------------

_E = "pl_expenses__"
_UNSIGNED = {f"{_E}cost_of_goods_sold": 600, f"{_E}selling_and_marketing_expenses": 120,
             f"{_E}depreciation_and_amortisation_expense": 30}


def _statement(figures: dict, statement: str = "profit_and_loss") -> DocumentModel:
    """A statement's worth of rows, so a cohort can be judged rather than a single row."""
    doc = DocumentModel(filename="f.pdf", locale="en")
    doc.pages = [PageSource(index=0, statement=statement)]
    for key, num in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(num), value_raw=Decimal(num),
                                    basis=Basis.CONSOLIDATED, period_label="current",
                                    provenance=Provenance(page_index=0)))
        doc.line_items.append(li)
    return doc


def _by_key(doc: DocumentModel) -> dict[str, ExtractedValue]:
    return {li.canonical_key: _value(li) for li in doc.line_items}


def test_a_filing_that_prints_its_expenses_unsigned_is_negated_on_load(raw_ontology):
    """``global_rules.sign_convention.unsigned_source``, which had no mechanism at all.

    THE DEFECT THIS CLOSES: the rulebook declared "where a filing prints expenses as unsigned
    positives in a by-nature list, negate on load and set sign_normalised: true on the fact so the
    transformation is auditable", and nothing implemented it — a declaration with no mechanism, which
    is the one thing this rulebook is not allowed to contain. Leaving such a filing alone is not
    neutral: the template's subtotals are SIGNED sums, so ``pl_gross_profit = sum(revenue,
    cost_of_goods_sold)`` came out as revenue PLUS cost, every subtotal above it was wrong by twice
    the cost base, and every KPI built on one carried the error with it.

    ``value_raw`` keeps what the page printed. That is what makes the flip auditable rather than
    merely done — the two figures side by side, plus the flag and the log line.
    """
    doc = _statement(_UNSIGNED)
    _run(doc, _ontology(raw_ontology))

    for key, ev in _by_key(doc).items():
        assert ev.value == -abs(ev.value_raw), key      # negated
        assert ev.value_raw > 0, key                    # and the printed figure is untouched
        assert ev.sign_normalised is True, key
        assert any(f.startswith("sign_normalised:unsigned_source") for f in ev.confidence.flags)

    # …and NOT then reported as carrying the wrong sign, because it no longer does.
    for li in doc.line_items:
        assert not [f for f in li.confidence.flags if f.startswith("sign_opposite_to_expected")]


def test_one_positive_expense_among_negative_siblings_is_flagged_and_never_flipped(raw_ontology):
    """The boundary between the rulebook's two sign sentences, which say opposite things.

    ``validation`` — "a concept whose sign_convention is … but whose loaded value carries the
    opposite sign is a review trigger, NOT an auto-correction" — governs the individual case, and it
    has to keep governing it: one positive expense among negative siblings is the row that landed on
    the wrong concept, and flipping it would hide the mis-mapping behind a plausible figure while
    every subtotal went on tying. So the unsigned-source rule must not reach it.

    Unanimity is what separates them, and the whole cohort is what is tested — not the row. So the
    cohort here is deliberately WIDE ENOUGH to clear the size floor (four concepts, three of them
    positive): the point is that a single negative expense proves the filing does use signs, which
    makes a house style impossible however many positives sit beside it. Testing this with two rows
    would have proved only that the floor works.
    """
    doc = _statement({f"{_E}cost_of_goods_sold": 600,
                      f"{_E}selling_and_marketing_expenses": 120,
                      f"{_E}depreciation_and_amortisation_expense": 30,
                      f"{_E}general_and_administrative_expenses": -90})
    _run(doc, _ontology(raw_ontology))

    values = _by_key(doc)
    assert all(ev.sign_normalised is False for ev in values.values())
    assert values[f"{_E}cost_of_goods_sold"].value == 600      # left exactly as reported
    for key in (f"{_E}cost_of_goods_sold", f"{_E}selling_and_marketing_expenses",
                f"{_E}depreciation_and_amortisation_expense"):
        assert "sign_opposite_to_expected:negative_expected" in values[key].confidence.flags


def test_too_few_concepts_to_be_a_convention_are_left_alone_and_said_so(raw_ontology):
    """Two positive expenses are two rows to look at, not a house style.

    Three independent mis-mappings that all fall the same way is a far weaker explanation than one
    presentation convention; two is not. The decision is LOGGED either way — "we saw two positive
    expenses and left them alone" is what a reviewer chasing a failing subtotal needs to find.
    """
    doc = _statement({f"{_E}cost_of_goods_sold": 600,
                      f"{_E}selling_and_marketing_expenses": 120})
    ctx = _run(doc, _ontology(raw_ontology))

    assert all(ev.sign_normalised is False for ev in _by_key(doc).values())
    assert any("unsigned_source" in line and "not applied" in line for line in ctx.logs)


def test_deleting_the_sentence_deletes_the_transformation(raw_ontology):
    """The rulebook drives it. A transformation the engine performs whatever the rulebook says is
    not a specification, and this one changes a reported number's sign — the last place to keep a
    behaviour the rulebook cannot switch off."""
    edited = copy.deepcopy(raw_ontology)
    del edited["global_rules"]["sign_convention"]["unsigned_source"]

    doc = _statement(_UNSIGNED)
    _run(doc, _ontology(edited))

    values = _by_key(doc)
    assert all(ev.sign_normalised is False for ev in values.values())
    assert all(ev.value > 0 for ev in values.values())
    # Still reported, though: the figures are as-printed and the expectation check says so.
    assert all("sign_opposite_to_expected:negative_expected" in ev.confidence.flags
               for ev in values.values())


def test_a_sign_indeterminate_concept_is_never_negated(raw_ontology):
    """"Subtotals, working-capital movements, fair-value changes, OCI and net cash flows are
    sign-indeterminate. Retain the reported sign; never coerce." A positive fair-value change on a
    statement whose expenses ARE being negated keeps its sign, because it is not an expense."""
    doc = _statement({**_UNSIGNED, "pl_exceptional_items__fair_value_change_gains": 45,
                      "pl_income__revenue_from_operations": 1000})
    _run(doc, _ontology(raw_ontology))

    values = _by_key(doc)
    assert values[f"{_E}cost_of_goods_sold"].value == -600          # the cohort was negated
    assert values["pl_exceptional_items__fair_value_change_gains"].value == 45
    assert values["pl_exceptional_items__fair_value_change_gains"].sign_normalised is False
    assert values["pl_income__revenue_from_operations"].value == 1000


def test_the_transformation_reaches_the_served_row_so_a_reviewer_can_see_it(raw_ontology):
    """"…so the transformation is auditable" — which means auditable by the person reviewing the
    spread, not only in a log line.

    The flip is the one place this pipeline changes a reported number's sign, so a reviewer looking
    at -600 has to be able to find out that the page said 600. Three things carry it: ``value_raw``
    on the fact, ``sign_normalised`` beside it, and the flag — and the flag is what actually travels,
    on the value and on the row, through the same payload the Workspace colours each number from.
    """
    from app.api.routes.extractions import _serialize_rows

    doc = _statement(_UNSIGNED)
    _run(doc, _ontology(raw_ontology))
    served = {r["canonical_key"]: r for r in _serialize_rows(doc)}

    row = served[f"{_E}cost_of_goods_sold"]
    assert row["values"][0]["value"] == "-600"
    assert any(f.startswith("sign_normalised:unsigned_source")
               for f in row["values"][0]["confidence"]["flags"])
    assert "sign_normalised:unsigned_source" in row["flags"]
