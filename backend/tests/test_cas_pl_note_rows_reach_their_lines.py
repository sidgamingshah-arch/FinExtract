"""CAS P&L ITEMS THE NOTES PRINT REACH A LINE, so the computed profit before tax reaches print.

The HK template computes Profit(Loss) Before Tax from its components. On a mainland (CAS) income
statement three printed figures reached no component, measured answer-free at b636b25:

  * OTHER-BUSINESS REVENUE. Sales(Revenues) is the principal-business revenue the revenue note prints
    (主营业务, commit b2e3933 — a deliberate design), while cost of sales is the whole 营业成本. The
    note's 其他业务 row therefore left the face's 营业收入 and landed nowhere: 688008 七、61
    10,141,512.36, 000709 5,025,785,386.11, 000685 122,856,135.16. It arrives on Other Operating
    Income, whose aliases already name 其他业务收入, BESIDE the printed 其他收益 row
    (`CascadeRung.adds_to_printed`).
  * THE REST OF 财务费用. The face prints only its 其中 rows, 利息费用 and 利息收入; the exchange row and
    the bank charges are printed in the 财务费用 note alone (688008 七、66: 汇兑损益 -12,311,885.43 and
    其他 280,984.09). They now reach Rlzd Forex Translation G(L) and Other Debt Service Expenses(-) from
    those printed rows — never as the note total less its interest rows.
  * A REVENUE NOTE READ A YEAR OUT. Where 营业收入、营业成本的分解信息 starts on the same page as the
    主营业务/其他业务 table, the two tables were one section; their figures clustered into six bands, the
    first table's 本期发生额|上期发生额 over 收入|成本 header no longer fitted, and its columns were
    labelled by position. 000883's this-year principal revenue 19,832,930,478.27 was filed under the
    PRIOR year; 300257's prior-year revenue slot held this year's cost of sales.

With all three, 688008's current PBT, and both years of 000883 and 300257, equal the printed figure.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import CascadeRung, load_line_item_set
from app.services.periods import concept_value
from app.stages.note_sourced import NoteSourcedStage

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "backend" / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
FILINGS = REPO / "testdata" / "filings"

OOI = "is_pl__other_operating_income"
FX = "is_pl__rlzd_forex_translation_g_l"
OTHER_FIN = "is_pl__other_debt_service_expenses"
OB_PART = "sub__revenue_note_other_business_revenue"
FX_PART = "sub__finance_expense_note_exchange"
CHARGES_PART = "sub__finance_expense_note_other_charges"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _items(shipped):
    return {i.key: i for i in shipped.items}


# ── the configuration ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("part,parent", [(OB_PART, OOI), (FX_PART, FX), (CHARGES_PART, OTHER_FIN)])
def test_each_part_is_an_internal_note_part_wired_into_its_parents_cascade(shipped, part, parent):
    items = _items(shipped)
    p = items[part]
    assert p.namespace == "internal" and p.in_output is False
    assert p.parent == parent
    assert str(getattr(p.statement, "value", p.statement)) == "notes"
    assert p.route == "note_tables"
    # No face alias: no caption on any statement can bind a note part.
    assert not p.aliases and not any((p.aliases_i18n or {}).values())
    refs = [t.ref for rung in items[parent].cascade for t in rung.terms]
    assert refs == [part], refs


def test_other_business_revenue_arrives_beside_the_printed_other_income(shipped):
    rung = _items(shipped)[OOI].cascade[0]
    assert rung.adds_to_printed is True
    assert [t.sign for t in rung.terms] == [1]
    # The line stays a FACE line: its aliases still bind 其他收益 off the statement.
    assert "其他收益" in (_items(shipped)[OOI].aliases_i18n or {}).get("zh", [])


@pytest.mark.parametrize("parent", [FX, OTHER_FIN])
def test_the_finance_expense_rows_enter_negated_and_may_be_either_sign(shipped, parent):
    """The note prints them as part of an EXPENSE: a loss or a charge positive, a gain negative."""
    rung = _items(shipped)[parent].cascade[0]
    assert [t.sign for t in rung.terms] == [-1]
    assert rung.refuse_negative is False
    assert rung.adds_to_printed is False and rung.outranks_printed is False


def test_sales_is_still_the_principal_business_revenue(shipped):
    """b2e3933 stands: the other-business row is not added to Sales."""
    sales = _items(shipped)["is_pl__sales_revenues"]
    assert [t.ref for t in sales.cascade[0].terms] == ["sub__revenue_note_principal_revenue"]


def test_the_rung_field_defaults_off():
    assert CascadeRung(id="x").adds_to_printed is False


# ── the stage, on 688008's notes ───────────────────────────────────────────────────────────────

def _value(amount: str, period: str = "current", measure: str = "", page: int = 218):
    label = f"{period}:{measure}" if measure else period
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label, value=Decimal(amount),
                          value_raw=Decimal(amount), provenance=Provenance(page_index=page))


def _note(number: str, title: str, rows, page: int) -> NotesTable:
    items = []
    for caption, figures in rows:
        item = NoteItem(raw_label=caption)
        for i, (label, amount) in enumerate(figures.items()):
            period, _, measure = label.partition(":")
            item.values[f"v{i}"] = _value(amount, period, measure, page)
        items.append(item)
    return NotesTable(note_number=number, title=title, chapter_title="合并财务报表项目注释",
                      items=items, source_pages=[page], source_text=title)


def _montage_notes() -> list[NotesTable]:
    """688008's 七、61 and 七、66, as the filing prints them."""
    return [
        _note("七、61", "营业收入和营业成本情况", [
            ("主营业务", {"current": "3628769555.93", "current:cost": "1516811244.12",
                       "prior": "2278141066.50", "prior:cost": "933947676.30"}),
            ("其他业务", {"current": "10141512.36", "current:cost": "6803694.42",
                       "prior": "7597431.73", "prior:cost": "5268587.65"}),
            ("合计", {"current": "3638911068.29", "current:cost": "1523614938.54",
                    "prior": "2285738498.23", "prior:cost": "939216263.95"}),
        ], 218),
        _note("七、66", "财务费用", [
            ("利息收入", {"current": "-229985802.58", "prior": "-178905809.38"}),
            ("租赁负债利息费用", {"current": "1512598.38", "prior": "741618.27"}),
            ("汇兑损益", {"current": "-12311885.43", "prior": "-2324577.63"}),
            ("其他", {"current": "280984.09", "prior": "229702.89"}),
            ("合计", {"current": "-240504105.54", "prior": "-180259065.85"}),
        ], 221),
    ]


def _face(key: str, caption: str, current: str, prior: str, *, method=None) -> LineItem:
    row = LineItem(source_label=caption, canonical_key=key)
    row.values["c"] = _value(current, "current", page=154)
    row.values["p"] = _value(prior, "prior", page=154)
    if method is not None:
        row.confidence.method = method
    return row


def _run(shipped, face_rows, notes=None) -> DocumentModel:
    doc = DocumentModel(filename="688008.pdf")
    doc.notes = notes if notes is not None else _montage_notes()
    doc.line_items = list(face_rows)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx)


def _rows(doc, key) -> list[dict]:
    """The carriers of one key as the served rows shape them (`values` as a list)."""
    out = []
    for li in doc.line_items:
        if li.canonical_key != key:
            continue
        row = li.model_dump(mode="json")
        row["values"] = list((row.get("values") or {}).values())
        out.append(row)
    return out


def _figure(doc, key, period="current"):
    """The figure the app shows for the line — `concept_value` over every carrier."""
    got = concept_value(_rows(doc, key), "consolidated", period)
    return None if got is None else Decimal(str(got)).quantize(Decimal("0.01"))


def test_other_business_revenue_is_added_to_the_printed_other_income(shipped):
    printed = _face(OOI, "加：其他收益", "91576969.33", "84437465.04")
    doc = _run(shipped, [printed])
    assert _figure(doc, OOI) == Decimal("101718481.69")          # 91,576,969.33 + 10,141,512.36
    assert _figure(doc, OOI, "prior") == Decimal("92034896.77")  # 84,437,465.04 + 7,597,431.73
    # The printed row keeps the printed figure; the note's row arrives on a carrier of its own.
    assert printed.values["c"].value == Decimal("91576969.33")
    arrived = [li for li in doc.line_items if li.canonical_key == OOI and li is not printed]
    from app.stages.note_sourced import ARRIVED_BESIDE_PRINTED
    assert len(arrived) == 1 and ARRIVED_BESIDE_PRINTED in arrived[0].confidence.flags
    trail = arrived[0].derivation["consolidated:current"]
    assert trail["method"] == "cascade:OB_NOTE"
    # Sales stays the principal row.
    assert _figure(doc, "is_pl__sales_revenues") == Decimal("3628769555.93")


def test_a_model_answer_for_other_income_does_not_stop_the_part_arriving(shipped):
    answered = _face(OOI, "加：其他收益", "91576969.33", "84437465.04", method="llm")
    doc = _run(shipped, [answered])
    assert _figure(doc, OOI) == Decimal("101718481.69")


def test_with_no_printed_other_income_the_part_is_the_line(shipped):
    doc = _run(shipped, [])
    assert _figure(doc, OOI) == Decimal("10141512.36")
    assert len([li for li in doc.line_items if li.canonical_key == OOI]) == 1


def test_the_finance_expense_notes_exchange_and_charges_reach_their_lines(shipped):
    doc = _run(shipped, [])
    # 汇兑损益 -12,311,885.43 is a GAIN inside an expense: +12,311,885.43 on the line.
    assert _figure(doc, FX) == Decimal("12311885.43")
    assert _figure(doc, FX, "prior") == Decimal("2324577.63")
    # 其他 280,984.09 is a charge: -280,984.09.
    assert _figure(doc, OTHER_FIN) == Decimal("-280984.09")
    # Together they are exactly what the face's 其中 rows leave out of 财务费用:
    # -240,504,105.54 - (-229,985,802.58 + 1,512,598.38) = -12,030,901.34, i.e. +12,030,901.34 of PBT.
    assert _figure(doc, FX) + _figure(doc, OTHER_FIN) == Decimal("12030901.34")
    # The interest rows are the face's, and are not read again here.
    for part in (FX_PART, CHARGES_PART):
        trail = next(li for li in doc.line_items if li.canonical_key == part).derivation
        labels = {i["label"] for slot in trail.values() for i in slot["inputs"]}
        assert not any("利息" in label or "合计" in label for label in labels), labels


def test_a_face_that_prints_its_exchange_line_keeps_it(shipped):
    printed = _face(FX, "汇兑收益（损失以“－”号填列）", "5000000.00", "1000000.00")
    doc = _run(shipped, [printed])
    assert _figure(doc, FX) == Decimal("5000000.00")


def test_a_deducted_exchange_row_is_not_read_at_the_wrong_sign(shipped):
    notes = [_note("七、66", "财务费用", [
        ("利息支出", {"current": "100.00"}),
        ("减：汇兑收益", {"current": "40.00"}),
        ("合计", {"current": "60.00"}),
    ], 221)]
    doc = _run(shipped, [], notes)
    assert _figure(doc, FX) is None


# ── the revenue note's two tables, read from the filings ───────────────────────────────────────

def _read(stem: str, first: int, last: int):
    fitz = pytest.importorskip("fitz")
    from app.services.notes_extract import extract_note_tables
    from app.services.pdf_extract import _native_words

    path = FILINGS / f"{stem}.pdf"
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    pdf = fitz.open(path)
    carry = grid = group = header = None
    chapter = ["七", 7, "合并财务报表项目注释", None]
    tables = []
    for i in range(first, last + 1):
        page = pdf[i]
        words = _native_words(page, page.rect.width, page.rect.height, rotation=0)
        grids, groups, headers, opened = [], [], [], []
        got = extract_note_tables(words, page_index=i, document_id="d", source_kind="native",
                                  carry_note=carry, carry_grid=grid, grid_out=grids,
                                  carry_group=group, group_out=groups, chapter=chapter,
                                  carry_header=header, header_out=headers, open_out=opened)
        tables.extend(got)
        carry = (opened[-1] if opened else
                 (got[-1].note_number, got[-1].title, got[-1].basis, got[-1].basis_from)
                 if got else carry)
        grid = grids[-1] if grids else None
        group = groups[-1] if groups else None
        header = headers[-1] if headers else None
    return tables


def _by_label(tables, caption: str) -> dict[str, str]:
    item = next(it for t in tables for it in t.items if it.raw_label == caption)
    return {str(ev.period_label): str(ev.value) for ev in item.values.values()}


@pytest.mark.parametrize("stem,first,last,want", [
    # 湖北能源 000883 七、61: the table opens at the foot of p219 (0-based), its breakdown below it.
    ("000883_hubei_energy_2024_prc", 218, 219,
     {"current": "19832930478.27", "current:cost": "14947978108.70",
      "prior": "18531124354.75", "prior:cost": "14896565207.51"}),
    # 开山 300257 七、61, p209.
    ("300257_kaishan_2024_prc", 208, 209,
     {"current": "4166049386.14", "current:cost": "2768072657.83",
      "prior": "4100247577.84", "prior:cost": "2751264516.23"}),
])
def test_the_revenue_note_is_read_by_its_own_header_not_the_breakdowns(stem, first, last, want):
    tables = _read(stem, first, last)
    assert _by_label(tables, "主营业务") == want
    # The breakdown is a table of the same note, titled by the line that names it.
    assert any(t.title.startswith("营业收入、营业成本的分解信息") for t in tables)
