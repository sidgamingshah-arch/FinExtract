"""A PER-SHARE AMOUNT AND A SHARE COUNT ARE NOT MONEY, and no money scale reaches them.

A 10-K's statement of operations prints, under net income,

    Earnings per share:                              Basic    $ 6.12
    Shares used in computing earnings per share:     Basic    15,343,783

under "(In millions, except number of shares, which are reflected in thousands, and per-share
amounts)". The row reader resolves one scale per page, so both rows carried scale 1,000,000: an
export in absolute units published EPS as 6,120,000, the share count was multiplied by the money
scale too, and the request told the model both were millions of dollars.

What is pinned here:

  * the share scale is read off what the filing says about SHARES, and a header that excepts
    only per-share amounts leaves shares in the statements' own scale (`units.share_scale`);
  * a figure on a line declaring `measure: per_share` / `shares` is restated in that measure —
    "USD per share", "shares (thousands)" — with its printed figure untouched, and a set whose lines
    declare none is not touched at all (`units.stamp_measures`);
  * `describe` and `compare` read the measure before the scale;
  * the served rows say so, and every export writes such a figure as printed whatever units were
    asked for;
  * a stored set or fact that predates the field dumps exactly as it did.
"""
from __future__ import annotations

import io
import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.models import Basis, ExtractedValue, LineItem, UnitContext
from app.core.models.enums import PrintedIn
from app.core.models.geometry import Provenance
from app.schemas.line_items import LineItemDef, load_line_item_set
from app.services import units

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
NORTHWIND = ("(In millions, except number of shares, which are reflected in thousands, and "
             "per-share amounts)")
CASCADE = "(In thousands, except per share amounts)"

MILLIONS = UnitContext(currency="", scale_factor=Decimal(1_000_000), units_label="million",
                       source_bbox_page=3)
THOUSANDS = UnitContext(currency="", scale_factor=Decimal(1_000), units_label="thousand",
                        source_bbox_page=3)
DOC_MILLIONS = UnitContext(currency="USD", scale_factor=Decimal(1_000_000), units_label="million")

EPS, SHARES, REVENUE = "is_pl__eps_basic", "is_pl__weighted_average_shares_basic", "is_pl__revenue"


# ── reading the share scale ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,scale", [
    # Named for the shares themselves.
    (NORTHWIND, Decimal(1_000)),
    ("(In millions, except number of shares, which are reflected in\nthousands, and per-share "
     "amounts)", Decimal(1_000)),
    ("(Dollars in millions, shares in thousands, except per share amounts)", Decimal(1_000)),
    ("Weighted-average shares outstanding (in millions)", Decimal(1_000_000)),
    ("Shares used in computing earnings per share (in thousands):", Decimal(1_000)),
    ("Number of shares ('000)", Decimal(1_000)),
    # Excepted from the header's scale without one of their own: printed in units.
    ("(In thousands, except share and per share data)", Decimal(1)),
    # Nothing said about shares: the caller takes the statement's scale.
    (CASCADE, None),
    ("(In millions, except per common share amounts)", None),
    ("(Amounts in millions, except per share data)", None),
    ("repurchased 10 million shares for $1 billion", None),
    ("Common stock, $0.01 par value: 50,400,000 shares authorized; 15,116,786 shares issued", None),
])
def test_the_share_scale_is_what_the_filing_says_about_shares(text, scale):
    assert units.share_scale(text) == scale


# ── the unit of a per-share amount and of a share count ────────────────────────────────────────

def test_a_per_share_amount_is_in_its_currency_per_share_at_a_scale_of_one():
    unit = units.per_share_unit(MILLIONS, DOC_MILLIONS)
    assert (unit.measure, unit.currency, unit.scale_factor) == ("per_share", "USD", Decimal(1))
    assert units.describe(unit, DOC_MILLIONS) == "USD per share"
    cents = units.per_share_unit(UnitContext(currency="CNY", scale_factor=Decimal(1000)), cents=True)
    assert cents.scale_factor == Decimal("0.01")
    assert units.describe(cents) == "CNY cents per share"


@pytest.mark.parametrize("printed,stated,said", [
    (MILLIONS, Decimal(1_000), "shares (thousands)"),        # the header names the shares' scale
    (THOUSANDS, None, "shares (thousands)"),                 # "except per share amounts"
    (MILLIONS, Decimal(1), "shares"),                        # "except share and per share data"
    (UnitContext(), None, "shares (scale not stated)"),      # nothing read anywhere
])
def test_a_share_count_is_in_shares_never_in_money(printed, stated, said):
    unit = units.shares_unit(printed, None, stated=stated)
    assert unit.measure == "shares" and unit.currency == ""
    assert units.describe(unit, DOC_MILLIONS) == said


def test_a_money_scale_converts_nothing_that_is_not_money():
    per_share = units.per_share_unit(MILLIONS, DOC_MILLIONS)
    count = units.shares_unit(MILLIONS, stated=Decimal(1_000))
    assert units.compare(per_share, MILLIONS) == units.SAME
    assert units.compare(MILLIONS, per_share) == units.SAME
    assert units.compare(count, MILLIONS) == units.SAME
    # Two share counts in different share scales rescale like any two figures.
    assert units.compare(count, units.shares_unit(MILLIONS, stated=Decimal(1))).factor == 1000
    # A currency the two sides declare differently is still a mismatch.
    assert units.compare(per_share, UnitContext(currency="CNY")).currency_mismatch == ("USD", "CNY")


def test_a_block_of_money_and_per_share_rows_states_each_rows_unit():
    rows = [{"_unit": "USD millions (x1,000,000)"}, {"_unit": "USD per share"},
            {"_unit": "shares (thousands)"}]
    assert units.block_units(rows) == "varies by row; see each row's unit"
    assert [r["unit"] for r in rows] == ["USD millions (x1,000,000)", "USD per share",
                                         "shares (thousands)"]


# ── stamping ───────────────────────────────────────────────────────────────────────────────────

def _row(key, label, figures, unit=MILLIONS, group="", page=3):
    li = LineItem(source_label=label, canonical_key=key, group_hint=group,
                  printed_in=PrintedIn.FACE)
    for period, value in figures.items():
        li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                    basis=Basis.CONSOLIDATED, period_label=period,
                                    unit_ctx=unit.model_copy(),
                                    provenance=Provenance(page_index=page)))
    return li


def _set(**measures):
    return SimpleNamespace(items=[LineItemDef(key=k, label=k, measure=m)
                                  for k, m in measures.items()] + [LineItemDef(key=REVENUE)])


def _doc(*rows, unit=DOC_MILLIONS):
    return SimpleNamespace(line_items=list(rows), unit_context=unit, fmt="pdf",
                           pages=[SimpleNamespace(index=3, statement="profit_and_loss")])


def _pdf(*lines: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(3):
        c.showPage()
    y = 720
    for line in lines:
        c.setFont("Helvetica", 9)
        c.drawString(72, y, line)
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def test_a_figure_on_a_measured_line_is_restated_in_its_measure_and_nothing_else_moves():
    eps = _row(EPS, "Basic $", {"current": "6.12", "prior": "6.13"}, group="Earnings per share:")
    count = _row(SHARES, "Basic", {"current": "15343783", "prior": "15744231"},
                 group="Shares used in computing earnings per share:")
    revenue = _row(REVENUE, "Total net sales", {"current": "391035"})
    logged: list[str] = []
    raw = _pdf("CONSOLIDATED STATEMENTS OF OPERATIONS", NORTHWIND)
    n = units.stamp_measures(_doc(eps, count, revenue), _set(**{EPS: "per_share", SHARES: "shares"}),
                             raw=raw, log=logged.append)
    assert n == 4
    for ev in eps.values.values():
        assert units.describe(ev.unit_ctx, DOC_MILLIONS) == "USD per share"
        assert "unit_measure:per_share" in ev.confidence.flags
    for ev in count.values.values():
        assert units.describe(ev.unit_ctx) == "shares (thousands)"
        assert ev.unit_ctx.scale_factor == Decimal(1_000)
    # THE FIGURE IS THE ONE THE PAGE PRINTED.
    assert sorted(ev.value for ev in eps.values.values()) == [Decimal("6.12"), Decimal("6.13")]
    assert sorted(ev.value_raw for ev in count.values.values()) == [Decimal(15343783),
                                                                   Decimal(15744231)]
    # A money line is not touched.
    assert [ev.unit_ctx for ev in revenue.values.values()] == [MILLIONS]
    assert any("is_pl__eps_basic: 2 figure(s) stated in USD per share" in m for m in logged)
    # Idempotent.
    assert units.stamp_measures(_doc(eps, count, revenue), _set(**{EPS: "per_share",
                                                                   SHARES: "shares"})) == 0


def test_a_header_excepting_only_per_share_amounts_leaves_shares_in_the_statements_scale():
    count = _row(SHARES, "Basic", {"current": "7433"}, unit=THOUSANDS,
                 group="Weighted average shares outstanding:")
    units.stamp_measures(_doc(count), _set(**{SHARES: "shares"}), raw=_pdf(CASCADE))
    (ev,) = count.values.values()
    assert units.describe(ev.unit_ctx) == "shares (thousands)" and ev.value == Decimal(7433)


def test_the_row_states_its_own_share_scale_before_the_page_does():
    count = _row(SHARES, "Weighted-average shares outstanding (in millions) – basic",
                 {"current": "15344"})
    units.stamp_measures(_doc(count), _set(**{SHARES: "shares"}), raw=_pdf(NORTHWIND))
    (ev,) = count.values.values()
    assert units.describe(ev.unit_ctx) == "shares (millions)"


def test_a_row_the_models_citation_created_reads_the_cited_caption():
    """`line_item_llm._write` creates a row captioned with the LINE's label and records the cited
    caption in the trail; a per-share figure printed in cents says so there."""
    row = _row(EPS, "Earnings per share — basic", {"current": "42.5"})
    row.derivation = {"consolidated:current": {"inputs": [{"label": "note : Basic (RMB cents)"}]}}
    units.stamp_measures(_doc(row, unit=UnitContext(currency="CNY")), _set(**{EPS: "per_share"}))
    (ev,) = row.values.values()
    assert units.describe(ev.unit_ctx) == "CNY cents per share"


def test_the_printed_row_the_model_cited_for_a_measured_line_is_restated_too():
    """A bare "Basic" binds no line: the model's answer writes the figure onto a row of its own and
    the printed row stays, unbound — and every flat export lists it. Cited only by per-share lines,
    its figure is a per-share amount. One cited by a money line as well is left alone."""
    bare = _row("engine_unclassified_face__profit_and_loss__eps", "Basic $", {"current": "6.12"})
    shared = _row("engine_unclassified_face__profit_and_loss__x", "Basic", {"current": "7"})
    cited = {str(bare.id): [EPS], str(shared.id): [EPS, REVENUE]}
    n = units.stamp_measures(_doc(bare, shared), _set(**{EPS: "per_share"}), cited=cited)
    assert n == 1
    assert [units.describe(ev.unit_ctx, DOC_MILLIONS) for ev in bare.values.values()] == [
        "USD per share"]
    assert [ev.unit_ctx for ev in shared.values.values()] == [MILLIONS]


def test_a_set_whose_lines_declare_no_measure_is_not_touched():
    """The HK, Ind AS and ICON sets: their EPS rows keep the unit the reader gave them, so nothing
    that reads a unit — the request text, the note-to-face tie, the checks, the exports — moves."""
    eps = _row("is_pl__basic_eps", "Basic (RMB cents)", {"current": "42.5"})
    before = [ev.unit_ctx.model_copy() for ev in eps.values.values()]
    hk = load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                       .read_text(encoding="utf-8")), resolve=True)
    assert all(i.measure == "money" for i in hk.items)
    assert units.stamp_measures(_doc(eps), hk, raw=b"not read") == 0
    assert [ev.unit_ctx for ev in eps.values.values()] == before


@pytest.mark.parametrize("name", ["output_csv_hk_line_items.json", "output_csv_indas_line_items.json",
                                  "output_csv_icon_line_items.json"])
def test_no_other_shipped_set_declares_a_measure(name):
    raw = json.loads((TEMPLATES / name).read_text(encoding="utf-8"))
    assert not any("measure" in item for item in raw["items"]), name
    loaded = load_line_item_set(raw, resolve=False)
    assert not any("measure" in item for item in loaded.model_dump(mode="json")["items"]), name


def test_a_money_fact_and_a_money_line_dump_as_they_did_before_the_field():
    assert "measure" not in UnitContext(currency="USD").model_dump()
    assert "measure" not in LineItemDef(key="x").model_dump()
    assert UnitContext(measure="shares").model_dump()["measure"] == "shares"
    assert LineItemDef(key="x", measure="per_share").model_dump()["measure"] == "per_share"
    # And the dump reads back as the same unit.
    unit = units.per_share_unit(MILLIONS, DOC_MILLIONS)
    assert UnitContext(**unit.model_dump()) == unit


def test_a_text_line_cannot_declare_a_measure():
    with pytest.raises(ValueError, match="measure"):
        LineItemDef(key="x", output_structure="phrase", measure="shares")


def test_the_statements_unit_is_never_taken_from_a_per_share_figure():
    """A figure the model or a note route writes beside the face takes the face's unit
    (`note_sourced._statement_unit`); a per-share amount is not that unit, however many there are."""
    from app.stages.note_sourced import _statement_unit

    revenue = _row(REVENUE, "Total net sales", {"current": "391035"})
    eps = [_row(EPS, "Basic $", {p: "6.12" for p in ("current", "prior", "col2", "col3")})
           for _ in range(3)]
    doc = _doc(revenue, *eps)
    units.stamp_measures(doc, _set(**{EPS: "per_share"}))
    assert _statement_unit(doc, "consolidated").scale_factor == Decimal(1_000_000)


# ── served, and exported ───────────────────────────────────────────────────────────────────────

def test_a_served_figure_that_is_not_money_says_so_and_a_money_figure_is_served_as_before():
    from app.api.routes.extractions import _measure_fields

    money = ExtractedValue(value=Decimal(1), basis=Basis.CONSOLIDATED, unit_ctx=MILLIONS)
    eps = money.model_copy(update={"unit_ctx": units.per_share_unit(MILLIONS, DOC_MILLIONS)})
    assert _measure_fields(money, DOC_MILLIONS) == {}
    assert _measure_fields(eps, DOC_MILLIONS) == {"measure": "per_share", "unit": "USD per share"}


def _served(key, label, value, *, measure=None, unit=None, scale="1000000"):
    v = {"period_label": "current", "basis": "consolidated", "value": value, "scale": scale}
    if measure:
        v.update(measure=measure, unit=unit)
    return {"source_label": label, "canonical_key": key, "values": [v]}


SERVED = [_served(REVENUE, "Total net sales", "391035"),
          _served(EPS, "Basic $", "6.12", measure="per_share", unit="USD per share", scale="1"),
          _served(SHARES, "Basic", "15343783", measure="shares", unit="shares (thousands)",
                  scale="1000")]


def test_the_csv_converts_the_money_and_carries_eps_and_shares_as_printed():
    from app.services.export import build_rows_csv

    text = build_rows_csv(SERVED, scale=1e6).decode("utf-8-sig").splitlines()
    assert text[1:] == ["Total net sales,391035000000", "Basic $,6.12", "Basic,15343783"]


def test_the_flat_workbook_converts_the_money_and_carries_eps_and_shares_as_printed():
    import openpyxl

    from app.services.export import build_rows_xlsx

    ws = openpyxl.load_workbook(io.BytesIO(build_rows_xlsx(SERVED, filename="f", scale=1e6))).active
    current = {ws.cell(r, 2).value: ws.cell(r, 7).value for r in range(2, ws.max_row + 1)}
    # Written as an unconverted figure is: the served value, untouched.
    assert current == {"Total net sales": 391035000000, "Basic $": "6.12", "Basic": "15343783"}


def test_the_statement_workbook_labels_eps_and_shares_with_their_unit_and_keeps_the_cents():
    import openpyxl

    from app.services.export import build_statement_workbook

    template = {"template_key": "t", "statements": [{"type": "profit_and_loss", "sections": [
        {"node_id": "is_pl", "canonical_key": "is_pl", "label": "Operations", "role": "header",
         "children": [{"node_id": k, "canonical_key": k, "label": label, "role": "line"}
                      for k, label in ((REVENUE, "Total revenues"),
                                       (EPS, "Earnings per share — basic"),
                                       (SHARES, "Weighted-average shares — basic"))]}]}]}
    data = build_statement_workbook(SERVED, template, scale=1e6, include=set(),
                                    units_caption="Amounts in USD absolute")
    ws = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    cells = {ws.cell(r, 1).value: ws.cell(r, 3) for r in range(1, ws.max_row + 1)
             if ws.cell(r, 1).value}
    assert cells["Total revenues"].value == 391035000000
    eps = cells["Earnings per share — basic (USD per share)"]
    assert eps.value == pytest.approx(6.12) and eps.number_format.startswith("#,##0.00")
    assert cells["Weighted-average shares — basic (shares, thousands)"].value == 15343783


def test_the_json_export_says_what_a_figure_that_is_not_money_is_in():
    from app.services.export import build_rows_json

    payload = json.loads(build_rows_json(SERVED, filename="f"))
    by_key = {r["canonical_key"]: r["values"][0] for r in payload["line_items"]}
    assert "unit" not in by_key[REVENUE]
    assert by_key[EPS]["unit"] == "USD per share" and by_key[EPS]["value"] == "6.12"
    assert (by_key[SHARES]["measure"], by_key[SHARES]["unit"]) == ("shares", "shares (thousands)")


def test_the_checks_rebuilt_from_served_rows_keep_the_measure():
    from app.services.structural_checks import items_of_rows

    items = {li.canonical_key: li for li in items_of_rows(SERVED)}
    assert [ev.unit_ctx.measure for ev in items[EPS].values.values()] == ["per_share"]
    assert [ev.unit_ctx.measure for ev in items[REVENUE].values.values()] == ["money"]
