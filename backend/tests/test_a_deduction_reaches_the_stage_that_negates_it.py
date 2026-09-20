"""``sign: natural_negative`` is a declaration the normalize stage has to be able to READ.

The stage has always implemented the rule — :func:`_natural_negative_keys` collects the template
lines marked ``sign: natural_negative`` and the per-row loop turns each one's figure into
``-abs(raw)``. What it could not do was find the template: it asked ``ctx.template_def``, and
``services.documents._context`` sets ``ctx.template``. Nothing in the codebase has ever set
``template_def``, so the lookup returned ``None`` on every run, ``_natural_negative_keys(None)``
returned the empty set, and ``contra`` was False for every row of every filing.

WHY THAT IS EXPENSIVE RATHER THAN MERELY UNTIDY. All thirteen lines the template marks
``natural_negative`` are ``op: "sum"`` children of their parents — treasury shares, accumulated
depreciation, the allowance for doubtful accounts, accumulated amortisation, interest expense, the
dividend lines, transfers to reserves. A deduction that arrives positive is therefore ADDED to the
total it should be taken out of, and the total is wrong by twice the deduction. On 澜起科技 688008
库存股 published +427,557,874.81, so the statement screen showed

    Total Equity & Reserves           12,251,621,314.53   against a printed 11,396,505,564.91
    Total Equity & Liabilities        13,074,027,136.00   against a printed 12,218,911,386.38

— each out by exactly 2 x 427,557,874.81, in both periods and on both bases. Because
``rollups.figures_as_shown`` puts COMPUTED ahead of PRINTED so the grid and the KPI layer agree,
the printed total could not correct it: the filing's own figure was on the page and the screen
showed the arithmetic instead.

These tests pin the WIRE, not just the rule. A test that hands ``NormalizeStage`` a context with
``template_def`` set would have passed throughout the whole period the bug existed, so the ones
below set the attribute ``services.documents`` actually sets, and one reads the real template.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.schemas.loader import load_template
from app.services import rollups
from app.stages.normalize import NormalizeStage, _natural_negative_keys

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
_TEMPLATE = _SAMPLES / "output_csv_hk_v1_template.json"


@pytest.fixture(scope="module")
def template_dict() -> dict:
    return json.loads(_TEMPLATE.read_text(encoding="utf-8"))


def _row(key: str, current: str, prior: str, label: str = "减：库存股") -> tuple[DocumentModel, LineItem]:
    li = LineItem(source_label=label, canonical_key=key)
    for period, amount in (("current", current), ("prior", prior)):
        li.set_value(ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                                    basis=Basis.CONSOLIDATED, period_label=period,
                                    provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.pages = [PageSource(index=0, statement="balance_sheet")]
    doc.line_items = [li]
    return doc, li


def _run(doc: DocumentModel, *, template=None, attr: str = "template") -> PipelineContext:
    """Run the stage the way `services.documents` wires it: the template on ``ctx.template``."""
    ctx = PipelineContext(raw_bytes=b"")
    if template is not None:
        setattr(ctx, attr, template)
    NormalizeStage().run(doc, ctx)
    return ctx


# --- the wire ----------------------------------------------------------------------------------

def test_the_stage_reads_the_attribute_services_documents_actually_sets(template_dict):
    """``ctx.template``, which is the only one anything sets. This is the regression."""
    doc, li = _row("bs_equity__treasury_shares", "427557874.81", "300031332.07")
    _run(doc, template=load_template(template_dict))

    assert [str(ev.value) for ev in _by_period(li)] == ["-427557874.81", "-300031332.07"]
    assert all(ev.sign_normalised for ev in li.values.values())


def test_the_stage_still_reads_template_def_where_a_caller_sets_that(template_dict):
    """The four other stages accept either name; this one must not become the odd one out."""
    doc, li = _row("bs_equity__treasury_shares", "427557874.81", "300031332.07")
    _run(doc, template=template_dict, attr="template_def")

    assert [str(ev.value) for ev in _by_period(li)] == ["-427557874.81", "-300031332.07"]


def test_a_model_and_a_plain_dict_are_both_understood(template_dict):
    for template in (template_dict, load_template(template_dict)):
        doc, li = _row("bs_equity__treasury_shares", "427557874.81", "300031332.07")
        _run(doc, template=template)
        assert _value_at(li, "current") == Decimal("-427557874.81")


def test_no_template_leaves_the_figure_alone_rather_than_guessing():
    """A run with no template cannot know what is a deduction, and must not invent one."""
    doc, li = _row("bs_equity__treasury_shares", "427557874.81", "300031332.07")
    _run(doc, template=None)

    assert _value_at(li, "current") == Decimal("427557874.81")
    assert not any(ev.sign_normalised for ev in li.values.values())


# --- the rule ----------------------------------------------------------------------------------

def test_every_declared_deduction_is_collected_from_the_real_template(template_dict):
    keys = _natural_negative_keys(template_dict)

    assert "bs_equity__treasury_shares" in keys
    assert keys, "the template declares natural_negative lines; none were found"
    # Named rather than counted, so adding a fourteenth line does not fail this test while
    # dropping treasury shares out of the set silently would.
    for key in ("bs_ca__allow_for_doubtful_accounts", "bs_nca__accum_deprec_and_impairment",
                "bs_nca__accum_intgbl_assets_amort", "is_pl__interest_expense",
                "is_retained__transfer_to_reserves"):
        assert key in keys


def test_every_declared_deduction_is_summed_by_its_parents(template_dict):
    """The rule is only correct because the parents ADD. If one ever subtracted, negating the
    child here would double the deduction — so the two declarations are checked against each
    other rather than each being trusted alone."""
    keys = _natural_negative_keys(template_dict)
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            rollup = node.get("rollup")
            if isinstance(rollup, dict):
                for child in rollup.get("children") or []:
                    if child in keys:
                        seen.add(child)
                        assert rollup.get("op") == "sum", (
                            f"{child} is natural_negative but {node.get('canonical_key')} "
                            f"combines it with op={rollup.get('op')!r}")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(template_dict)
    assert "bs_equity__treasury_shares" in seen


def test_a_figure_the_filing_already_printed_negative_is_not_flipped_back(template_dict):
    """``-abs(raw)`` and not ``-raw``: the declaration says which way the line points, so a filing
    that prints the deduction in parentheses and one that prints it bare must agree."""
    doc, li = _row("bs_equity__treasury_shares", "-427557874.81", "300031332.07")
    _run(doc, template=load_template(template_dict))

    assert _value_at(li, "current") == Decimal("-427557874.81")
    assert _value_at(li, "prior") == Decimal("-300031332.07")


def test_a_line_the_template_does_not_mark_is_untouched(template_dict):
    doc, li = _row("bs_equity__common_share_capital", "1144789273.00", "1138740286.00",
                   label="股本")
    _run(doc, template=load_template(template_dict))

    assert _value_at(li, "current") == Decimal("1144789273.00")
    assert not any(ev.sign_normalised for ev in li.values.values())


def test_value_raw_keeps_what_the_page_printed(template_dict):
    """So the flip can be audited against the filing."""
    doc, li = _row("bs_equity__treasury_shares", "427557874.81", "300031332.07")
    _run(doc, template=load_template(template_dict))

    assert _value_at(li, "current") == Decimal("-427557874.81")
    assert next(ev for ev in li.values.values()
                if ev.period_label == "current").value_raw == Decimal("427557874.81")


# --- what the screen then shows ----------------------------------------------------------------

def test_the_equity_totals_tie_to_the_printed_figures_once_the_deduction_is_negative(template_dict):
    """688008's own numbers, through the resolver the grid and the export both read.

    The point of the fix stated as arithmetic: with the deduction negative, both totals land on
    what the filing prints. The assertion is against the PRINTED figures, so it fails if the
    arithmetic drifts in either direction.
    """
    printed_total_equity = Decimal("11396505564.91")
    printed_balancing_total = Decimal("12218911386.38")
    rows = [
        _serialised("bs_equity__common_share_capital", "1144789273.00"),
        _serialised("bs_equity__capital_and_restricted_reserves", "5912529840.09"),
        _serialised("bs_equity__treasury_shares", "-427557874.81"),
        _serialised("bs_equity__accum_oth_eqty_rsrv_inc", "255293498.30"),
        _serialised("bs_equity__retained_profits", "4518383330.50"),
        _serialised("bs_equity__minority_interest_equity", "-6932502.17"),
        _serialised("bs_cl__total_liabilities", "822405821.47"),
    ]
    shown = rollups.figures_as_shown(template_dict, rows, "consolidated", "current")

    assert _cents(shown["bs_equity__total_equity_and_reserves"]) == printed_total_equity
    assert _cents(shown["bs_cl__total_equity_and_liabilities"]) == printed_balancing_total


def test_the_same_totals_are_out_by_twice_the_deduction_when_it_arrives_positive(template_dict):
    """The defect, measured — so the test above is known to be testing the thing that was wrong."""
    treasury = Decimal("427557874.81")
    rows = [
        _serialised("bs_equity__common_share_capital", "1144789273.00"),
        _serialised("bs_equity__capital_and_restricted_reserves", "5912529840.09"),
        _serialised("bs_equity__treasury_shares", str(treasury)),
        _serialised("bs_equity__accum_oth_eqty_rsrv_inc", "255293498.30"),
        _serialised("bs_equity__retained_profits", "4518383330.50"),
        _serialised("bs_equity__minority_interest_equity", "-6932502.17"),
        _serialised("bs_cl__total_liabilities", "822405821.47"),
    ]
    shown = rollups.figures_as_shown(template_dict, rows, "consolidated", "current")

    assert (_cents(shown["bs_equity__total_equity_and_reserves"])
            - Decimal("11396505564.91")) == 2 * treasury
    assert (_cents(shown["bs_cl__total_equity_and_liabilities"])
            - Decimal("12218911386.38")) == 2 * treasury


# --- helpers -----------------------------------------------------------------------------------

def _by_period(li: LineItem) -> list[ExtractedValue]:
    order = {"current": 0, "prior": 1}
    return sorted(li.values.values(), key=lambda ev: order.get(ev.period_label, 9))


def _value_at(li: LineItem, period: str) -> Decimal:
    return next(ev.value for ev in li.values.values() if ev.period_label == period)


def _cents(value: float) -> Decimal:
    """``figures_as_shown`` resolves to ``float``, so a cent is the precision it actually has —
    11,396,505,564.91 round-trips exactly and 12,251,621,314.53 arrives as ...314.529999."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _serialised(key: str, value: str) -> dict:
    """One row in the shape `_serialize_rows` produces, which is what `rollups` reads."""
    return {
        "canonical_key": key,
        "source_label": key,
        "values": [{"basis": "consolidated", "period_label": "current",
                    "value": value, "value_raw": value}],
    }
