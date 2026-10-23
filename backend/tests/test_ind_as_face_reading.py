"""An Ind AS (Schedule III) face reaches the model as the filing prints it.

Three defects, each measured on the two Ind AS filings of the corpus (laurus_labs_2025_26_ind,
asian_paints_2025_26_ind), and the ICON bindings that the Asian Paints profit and loss needed.

* A NOTE COLUMN READ AS FIGURES. Laurus prints "Depreciation and amortisation expense  3,4 & 39A",
  "Other equity  11.6" and "Summary of material accounting policies  2.2" in its Note column; none
  is note-number shaped, so each was read as a figure — {"current_col0": "34"} (standalone "4", the
  "3," having been taken as the note), {"current_col1": "11.6"}, a policies row carrying 2.2 that
  the mapper filed under other current liabilities, and a column legend for a column of notes.
* A CAPTION CUT ACROSS ITS PRINTED LINES. An Indian face sets its figures beside a caption's FIRST
  line, and the line under it was folded forward onto the next caption: "joint venture (IV-V) VII.
  Share of profit/(loss) …", "of tax" carrying the uncaptioned subtotal 2.84, and on Asian Paints
  "PROFIT BEFORE SHARE OF PROFIT IN ASSOCIATES, EXCEPTIONAL ITEMS AND" with its "TAX" read as the
  section of every row below it.
* "crores (x10,000,000), currency not stated" on every Laurus block, which says "All amounts in
  Crore Rupees".
* Asian Paints' computed profit before tax was -29,355.32 against a printed 6,003.44: "Revenue
  from Sale of Products/Services" and "Exceptional Items" reached no ICON line.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.core.models.geometry import BBox
from app.schemas.line_items import load_line_item_set
from app.services import rollups
from app.services.mapping import OntologyMatcher
from app.services.row_reconstruct import Word, build_line_items
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
P = "pl_icon__"

LINE_H = 0.011
CHAR = 0.0065          # width of one character at statement type size, as a page fraction
SPACE = 0.004          # a word space


def _words(text: str, x0: float, y: float) -> list[Word]:
    """A run of words set from x0, a word space apart."""
    out, x = [], x0
    for tok in text.split():
        w = CHAR * len(tok)
        out.append(Word(text=tok, bbox=BBox(x0=x, y0=y, x1=x + w, y1=y + LINE_H)))
        x += w + SPACE
    return out


def _cell(text: str, xc: float, y: float) -> Word:
    """One cell's token, centred on xc."""
    w = CHAR * len(text)
    return Word(text=text, bbox=BBox(x0=xc - w / 2, y0=y, x1=xc + w / 2, y1=y + LINE_H))


NOTE_X, CUR_X, PRI_X = 0.66, 0.78, 0.90


def _row(y: float, caption: str, *cells: str, note: str | None = None, x0: float = 0.05
         ) -> list[Word]:
    out = _words(caption, x0, y)
    if note is not None:
        out += _words(note, NOTE_X - CHAR * len(note) / 2, y)
    for xc, text in zip((CUR_X, PRI_X), cells):
        out.append(_cell(text, xc, y))
    return out


def _read(rows: list[list[Word]], statement: str = "profit_and_loss"):
    words = [w for r in rows for w in r]
    items, _ = build_line_items(words, page_index=0, document_id="d1", source_kind="native",
                                statement=statement)
    return items


def _by_caption(items) -> dict:
    return {li.source_label: li for li in items}


def _figures(li) -> dict[str, str]:
    return {v.period_label: str(v.value) for v in li.values.values()}


def _header(y: float = 0.05) -> list[list[Word]]:
    return [_words("Particulars", 0.05, y) + [_cell("Note", NOTE_X, y)]
            + [_cell("2026", CUR_X, y), _cell("2025", PRI_X, y)]]


# ── (a) the note column ────────────────────────────────────────────────────────────────────────

def _note_column_face(depreciation_note: str) -> list[list[Word]]:
    y = 0.08
    rows = _header()
    for caption, note, cur, pri in [
        ("Revenue from operations", "17", "6,812.90", "5,553.96"),
        ("Other income", "18", "48.38", "59.93"),
        ("Cost of materials consumed", "19", "2,794.34", "2,428.55"),
        ("Depreciation and amortisation expense", depreciation_note, "480.05", "430.09"),
        ("Finance costs", "23B", "170.73", "216.00"),
        ("Other equity", "11.6", "5,192.09", "4,364.71"),
    ]:
        rows.append(_row(y, caption, cur, pri, note=note))
        y += 0.02
    rows.append(_row(y, "Summary of material accounting policies", note="2.2"))
    return rows


@pytest.mark.parametrize("printed", ["3,4 & 39A", "3, 4 & 39A"])
def test_a_note_list_in_the_note_column_is_the_rows_note_not_a_figure(printed):
    rows = _by_caption(_read(_note_column_face(printed)))
    dep = rows["Depreciation and amortisation expense"]
    assert _figures(dep) == {"current": "480.05", "prior": "430.09"}
    assert dep.note_number == "3"


def test_a_sectioned_note_number_is_the_rows_note_not_a_figure():
    rows = _by_caption(_read(_note_column_face("3,4 & 39A")))
    equity = rows["Other equity"]
    assert _figures(equity) == {"current": "5192.09", "prior": "4364.71"}
    assert equity.note_number == "11.6"
    # …and resolves to its parent note, as "16(b)" always has.
    assert equity.cited_notes_among({"11"}) == ["11"]


def test_a_row_that_prints_only_a_note_reference_carries_no_figure():
    items = _read(_note_column_face("3,4 & 39A"))
    assert not any(str(v.value) == "2.2" for li in items for v in li.values.values())
    # No figure was filed under a column of note numbers.
    assert not any("_col" in (v.period_label or "") for li in items for v in li.values.values())


def test_outside_a_note_column_a_small_decimal_is_still_a_figure():
    """No Note header: "2.2" is a figure, as it always was (EPS, a per-share amount)."""
    rows = [_words("Particulars", 0.05, 0.05) + [_cell("2026", CUR_X, 0.05),
                                                 _cell("2025", PRI_X, 0.05)]]
    rows += [_row(0.08, "Basic earnings per share", "2.2", "1.9"),
             _row(0.10, "Diluted earnings per share", "2.1", "1.8")]
    basic = _by_caption(_read(rows))["Basic earnings per share"]
    assert _figures(basic) == {"current": "2.2", "prior": "1.9"}


# ── (b) a caption printed across two lines, its figures beside the first ───────────────────────

def test_a_schedule_iii_caption_keeps_the_line_printed_under_its_figures():
    rows = [
        _row(0.10, "Total tax expense", "292.03", "129.88"),
        _row(0.12, "VI. Profit for the year before share of profit/(loss) from associates and",
             "889.85", "354.41"),
        _words("joint venture (IV-V)", 0.065, 0.1305),
        _row(0.145, "VII. Share of profit/(loss) from associates and joint venture, net of tax",
             "0.29", "3.93"),
    ]
    captions = _by_caption(_read(rows))
    assert ("VI. Profit for the year before share of profit/(loss) from associates and joint "
            "venture (IV-V)") in captions
    assert ("VII. Share of profit/(loss) from associates and joint venture, net of tax"
            in captions)
    assert not any(c.startswith("joint venture") for c in captions)


def test_a_tail_opening_in_lower_case_is_not_the_next_rows_caption():
    """Laurus: "…of associates and joint venture, net" / "of tax" / an uncaptioned subtotal."""
    rows = [
        _row(0.10, "Tax on remeasurement of defined benefit plans", "(0.91)", "0.50"),
        _row(0.12, "Share in other comprehensive income of associates and joint venture, net",
             "0.11", "(0.01)"),
        _words("of tax", 0.05, 0.1305),
        [_cell("2.84", CUR_X, 0.141), _cell("(1.38)", PRI_X, 0.141)],
    ]
    captions = _by_caption(_read(rows))
    share = captions["Share in other comprehensive income of associates and joint venture, net of tax"]
    assert _figures(share) == {"current": "0.11", "prior": "-0.01"}
    assert "of tax" not in captions


def test_an_all_caps_tail_after_a_hanging_and_is_not_a_banner():
    """Asian Paints: "…EXCEPTIONAL ITEMS AND" / "TAX", and "(EBITDA) (I-II)" under EBITDA."""
    rows = [
        _words("EXPENSES", 0.05, 0.07),
        _row(0.085, "Other Expenses", "6,086.25", "5,786.40"),
        _row(0.10, "EARNING BEFORE INTEREST, TAX, DEPRECIATION AND AMORTISATION",
             "7,419.72", "6,578.82"),
        _words("(EBITDA) (I-II)", 0.05, 0.1105),
        _row(0.125, "Finance Costs", "195.41", "227.02"),
        _row(0.14, "PROFIT BEFORE SHARE OF PROFIT IN ASSOCIATES, EXCEPTIONAL ITEMS AND",
             "5,995.46", "5,325.46"),
        _words("TAX", 0.05, 0.1505),
        _row(0.165, "Share of profit in Associates", "165.59", "140.71"),
    ]
    items = _read(rows)
    captions = _by_caption(items)
    assert "EARNING BEFORE INTEREST, TAX, DEPRECIATION AND AMORTISATION (EBITDA) (I-II)" in captions
    assert "PROFIT BEFORE SHARE OF PROFIT IN ASSOCIATES, EXCEPTIONAL ITEMS AND TAX" in captions
    assert all((li.section_hint or "") not in ("TAX", "(EBITDA) (I-II)") for li in items)
    # A total computed from the income and expense blocks (I-II) is in neither: the rows printed
    # after it are no longer read under EXPENSES, as Laurus's "III. … (EBITDA) (I-II)" opens its own.
    assert captions["Other Expenses"].section_hint == "EXPENSES"
    for caption in ("Finance Costs", "Share of profit in Associates"):
        assert captions[caption].section_hint.startswith("EARNING BEFORE INTEREST")


def test_the_next_item_of_an_enumerated_list_is_not_a_tail():
    """Schedule III's trade payables pair: "(A) …; and" is complete, "(B) …" wraps forward."""
    rows = [
        _row(0.10, "(A) total outstanding dues of micro enterprises and small enterprises; and",
             "286", "244"),
        _words("(B) total outstanding dues of creditors other than micro enterprises and small",
               0.05, 0.1105),
        _row(0.12, "enterprises", "9,260", "8,380"),
    ]
    captions = _by_caption(_read(rows, statement="balance_sheet"))
    assert _figures(captions["(A) total outstanding dues of micro enterprises and small "
                             "enterprises; and"]) == {"current": "286", "prior": "244"}
    assert _figures(captions["(B) total outstanding dues of creditors other than micro "
                             "enterprises and small enterprises"]) == {"current": "9260",
                                                                       "prior": "8380"}


_PETROCHINA_HEAD = [   # hkex_0857_petrochina_2024, page index 126: (text, x0, y0, x1, y1)
    ('LIABILITIES', 0.1411, 0.2595, 0.2247, 0.2721),
    ('AND', 0.2290, 0.2595, 0.2610, 0.2721),
    ('SHAREHOLDERS’', 0.1507, 0.2702, 0.2785, 0.2829),
    ('EQUITY', 0.2827, 0.2702, 0.3378, 0.2829),
    ('December', 0.4633, 0.2451, 0.5359, 0.2577),
    ('31,', 0.4768, 0.2558, 0.4979, 0.2684),
    ('2024', 0.5021, 0.2558, 0.5359, 0.2684),
    ('December', 0.5877, 0.2451, 0.6603, 0.2577),
    ('31,', 0.6012, 0.2558, 0.6223, 0.2684),
    ('2023', 0.6265, 0.2558, 0.6603, 0.2684),
    ('December', 0.7121, 0.2451, 0.7847, 0.2577),
    ('31,', 0.7256, 0.2558, 0.7467, 0.2684),
    ('2024', 0.7509, 0.2558, 0.7847, 0.2684),
    ('December', 0.8365, 0.2451, 0.9091, 0.2577),
    ('31,', 0.8500, 0.2558, 0.8711, 0.2684),
    ('2023', 0.8753, 0.2558, 0.9091, 0.2684),
    ('Notes', 0.3740, 0.2702, 0.4154, 0.2829),
    ('The', 0.4619, 0.2702, 0.4880, 0.2829),
    ('Group', 0.4923, 0.2702, 0.5359, 0.2829),
    ('The', 0.5863, 0.2702, 0.6124, 0.2829),
    ('Group', 0.6167, 0.2702, 0.6603, 0.2829),
    ('The', 0.6868, 0.2702, 0.7129, 0.2829),
    ('Company', 0.7172, 0.2702, 0.7847, 0.2829),
    ('The', 0.8112, 0.2702, 0.8373, 0.2829),
    ('Company', 0.8416, 0.2702, 0.9091, 0.2829),
    ('Current', 0.1411, 0.2921, 0.1890, 0.3039),
    ('liabilities', 0.1930, 0.2921, 0.2477, 0.3039),
    ('Short-term', 0.1507, 0.3063, 0.2177, 0.3183),
    ('borrowings', 0.2217, 0.3063, 0.2905, 0.3183),
    ('25', 0.3868, 0.3063, 0.4027, 0.3183),
    ('45,955', 0.4920, 0.3063, 0.5359, 0.3183),
    ('38,979', 0.6164, 0.3063, 0.6603, 0.3183),
    ('49,315', 0.7408, 0.3063, 0.7847, 0.3183),
    ('17,445', 0.8652, 0.3063, 0.9091, 0.3183),
    ('Financial', 0.1507, 0.3207, 0.2041, 0.3327),
    ('liabilities', 0.2081, 0.3207, 0.2583, 0.3327),
    ('held', 0.2623, 0.3207, 0.2884, 0.3327),
    ('for', 0.2924, 0.3207, 0.3086, 0.3327),
    ('trading', 0.3126, 0.3207, 0.3553, 0.3327),
    ('3,808', 0.5000, 0.3207, 0.5359, 0.3327),
    ('1,727', 0.6244, 0.3207, 0.6603, 0.3327),
    ('-', 0.7794, 0.3207, 0.7847, 0.3327),
    ('-', 0.9038, 0.3207, 0.9091, 0.3327),
    ('Derivative', 0.1507, 0.3351, 0.2103, 0.3472),
    ('financial', 0.2143, 0.3351, 0.2637, 0.3472),
    ('liabilities', 0.2677, 0.3351, 0.3179, 0.3472),
    ('8', 0.3908, 0.3351, 0.3988, 0.3472),
    ('7,051', 0.5000, 0.3351, 0.5359, 0.3472),
    ('10,729', 0.6164, 0.3351, 0.6603, 0.3472),
    ('-', 0.7794, 0.3351, 0.7847, 0.3472),
    ('33', 0.8932, 0.3351, 0.9091, 0.3472),
    ('Notes', 0.1508, 0.3499, 0.1874, 0.3619),
    ('payable', 0.1914, 0.3499, 0.2395, 0.3619),
    ('26', 0.3868, 0.3499, 0.4028, 0.3619),
    ('14,895', 0.4921, 0.3499, 0.5359, 0.3619),
    ('20,731', 0.6165, 0.3499, 0.6603, 0.3619),
    ('13,785', 0.7409, 0.3499, 0.7847, 0.3619),
    ('20,006', 0.8653, 0.3499, 0.9092, 0.3619),
    ('Accounts', 0.1508, 0.3643, 0.2092, 0.3763),
    ('payable', 0.2132, 0.3643, 0.2613, 0.3763),
    ('27', 0.3868, 0.3643, 0.4028, 0.3763),
    ('272,785', 0.4841, 0.3643, 0.5359, 0.3763),
    ('289,934', 0.6085, 0.3643, 0.6604, 0.3763),
    ('99,068', 0.7409, 0.3643, 0.7848, 0.3763),
    ('101,615', 0.8573, 0.3643, 0.9092, 0.3763),
    ('Contract', 0.1508, 0.3787, 0.2045, 0.3907),
    ('liabilities', 0.2085, 0.3787, 0.2587, 0.3907),
    ('28', 0.3868, 0.3787, 0.4028, 0.3907),
    ('80,266', 0.4921, 0.3787, 0.5360, 0.3907),
    ('83,940', 0.6165, 0.3787, 0.6604, 0.3907),
    ('59,194', 0.7409, 0.3787, 0.7848, 0.3907),
    ('62,178', 0.8653, 0.3787, 0.9092, 0.3907),
    ('Employee', 0.1508, 0.3931, 0.2117, 0.4051),
    ('compensation', 0.2157, 0.3931, 0.3033, 0.4051),
    ('payable', 0.3073, 0.3931, 0.3554, 0.4051),
    ('29', 0.3868, 0.3931, 0.4028, 0.4051),
    ('8,095', 0.5001, 0.3931, 0.5360, 0.4051),
    ('8,592', 0.6245, 0.3931, 0.6604, 0.4051),
    ('5,884', 0.7489, 0.3931, 0.7848, 0.4051),
    ('6,159', 0.8733, 0.3931, 0.9092, 0.4051),
    ('Taxes', 0.1508, 0.4075, 0.1859, 0.4195),
    ('payable', 0.1899, 0.4075, 0.2380, 0.4195),
    ('30', 0.3869, 0.4075, 0.4028, 0.4195),
    ('60,245', 0.4921, 0.4075, 0.5360, 0.4195),
    ('73,991', 0.6165, 0.4075, 0.6604, 0.4195),
    ('34,857', 0.7409, 0.4075, 0.7848, 0.4195),
    ('46,717', 0.8653, 0.4075, 0.9092, 0.4195),
]


def test_a_column_header_of_dates_takes_no_tail():
    """PetroChina's continuation balance sheet: "LIABILITIES AND" sits on the baseline of its
    column heads' "31, 2024 31, 2023 …", so it reads as a caption ending on AND with figures, and
    "SHAREHOLDERS' EQUITY Notes The Group …" beneath it must not become its tail. Measured
    geometry; with the tail rule unguarded the header reached the model as a row captioned
    "LIABILITIES AND SHAREHOLDERS' EQUITY Notes The Group … Current liabilities" holding 2024 /
    2023, and every current liability was read under "AS OF DECEMBER 31, 2024 (CONTINUED)"."""
    words = [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=e)) for t, a, b, c, e in _PETROCHINA_HEAD]
    items, _ = build_line_items(words, page_index=126, document_id="d1", source_kind="native",
                                statement="balance_sheet")
    assert not any("SHAREHOLDERS" in li.source_label for li in items)
    assert _by_caption(items)["Short-term borrowings"].section_hint == "Current liabilities"


def test_a_signatorys_din_under_the_face_is_not_a_row():
    """Laurus Labs' standalone balance sheet: "DIN: 01424180" under the current column."""
    rows = [_row(0.10, "Provisions", "35.42", "27.63", note="15B"),
            _row(0.12, "Total current liabilities", "3,257.91", "3,402.21"),
            _words("For and on behalf of the Board of Directors", 0.30, 0.16),
            _words("DIN:", 0.30, 0.20) + [_cell("00211921", 0.36, 0.20)]
            + _words("DIN:", 0.72, 0.20) + [_cell("01424180", CUR_X, 0.20)]]
    items = _read(rows, statement="balance_sheet")
    assert not any("1424180" in str(v.value) for li in items for v in li.values.values())


# ── (c) the currency of a face that says "Rupees" ──────────────────────────────────────────────

def test_crore_rupees_declares_the_currency(monkeypatch):
    from app.services import derived
    from app.stages import normalize

    text = ("Standalone Balance Sheet as at March 31, 2026 (All amounts in Crore Rupees except for "
            "share data or as otherwise stated) Particulars Note")
    monkeypatch.setattr(derived, "document_text", lambda _b, _f: [(0, "cover"), (1, ""), (104, text)])
    doc = SimpleNamespace(face_pages=lambda: [SimpleNamespace(index=104, statement="balance_sheet")])
    unit = normalize._detect_units(SimpleNamespace(raw_bytes=b"%PDF"), "pdf", doc)
    assert unit is not None and unit.currency == "INR" and unit.units_label == "crore"


# ── (d) the Asian Paints profit and loss on the ICON set ───────────────────────────────────────

@pytest.fixture(scope="module")
def matcher():
    raw = json.loads((TEMPLATES / "output_csv_icon_line_items.json").read_text(encoding="utf-8"))
    return OntologyMatcher(build_working_view(load_line_item_set(raw)), locale="en",
                           settings=get_settings())


_EBITDA = "EARNING BEFORE INTEREST, TAX, DEPRECIATION AND AMORTISATION (EBITDA) (I-II)"


@pytest.mark.parametrize("heading, caption, key", [
    ("REVENUE FROM OPERATIONS", "Revenue from Sale of Products", P + "gross_sales_total"),
    ("REVENUE FROM OPERATIONS", "Revenue from Sale of Services", P + "gross_sales_total"),
    ("Income", "Sale of services", P + "gross_sales_total"),
    ("REVENUE FROM OPERATIONS", "Other Operating Revenue", P + "other_recurring_operating_income"),
    # Printed after the EBITDA total, under which the reader now files them.
    (_EBITDA, "Share of profit in Associates", P + "non_operating_income_non_cash_others"),
    (_EBITDA, "Exceptional Items", P + "non_operating_expense_non_cash_others"),
    ("EXPENSES", "Exceptional Items", P + "non_operating_expense_non_cash_others"),
])
def test_the_asian_paints_rows_reach_their_lines(matcher, heading, caption, key):
    assert matcher.match(caption, statement="profit_and_loss",
                         section=heading).canonical_key == key


@pytest.mark.parametrize("heading, caption", [
    ("Income", "Revenue from operations (gross)"),
    ("Income", "Sale of scrap"),
    (_EBITDA, "Share of loss of associates"),
])
def test_the_sales_and_associates_lines_take_nothing_else(matcher, heading, caption):
    got = matcher.match(caption, statement="profit_and_loss", section=heading).canonical_key
    assert got not in (P + "gross_sales_total", P + "non_operating_income_non_cash_others")


def _figure(key, label, value, y):
    return {"canonical_key": key, "source_label": label, "values": [{
        "basis": "consolidated", "period_label": "current", "value": str(value),
        "confidence": {"flags": []},
        "provenance": {"page_index": 232, "bbox": {"x0": 0.1, "y0": y, "x1": 0.2, "y1": y + 0.01}}}]}


def test_the_asian_paints_profit_and_loss_reaches_its_printed_profit():
    template = json.loads((TEMPLATES / "output_csv_icon_v1_template.json").read_text("utf-8"))
    rows = [
        _figure(P + "gross_sales_total", "Revenue from Sale of Products", "35416.25", 0.10),
        _figure(P + "gross_sales_total", "Revenue from Sale of Services", "100.12", 0.11),
        _figure(P + "other_recurring_operating_income", "Other Operating Revenue", "67.17", 0.12),
        _figure(P + "misc_income", "Other Income", "723.80", 0.13),
        _figure(P + "raw_materials", "Cost of Materials Consumed", "15384.05", 0.15),
        _figure(P + "raw_materials", "Purchases of Stock-in-Trade", "4014.05", 0.16),
        _figure(P + "changes_in_inventory", "Changes in Inventories", "603.68", 0.17),
        _figure(P + "salary_and_staff_expenses", "Employee Benefits Expense", "2799.59", 0.18),
        _figure(P + "sga_others", "Other Expenses", "6086.25", 0.19),
        _figure(P + "interest", "Finance Costs", "195.41", 0.21),
        _figure(P + "depreciation", "Depreciation and Amortisation Expense", "1228.85", 0.22),
        _figure(P + "non_operating_income_non_cash_others", "Share of profit in Associates",
                "165.59", 0.24),
        _figure(P + "non_operating_expense_non_cash_others", "Exceptional Items", "157.61", 0.26),
        _figure(P + "current_tax", "Current Tax", "1523.02", 0.28),
        _figure(P + "current_tax", "Short/(Excess) tax provision for earlier years", "27.05", 0.29),
        _figure(P + "deferred_tax", "Deferred Tax", "58.68", 0.30),
    ]
    shown = rollups.figures_as_shown(template, rows, "consolidated", "current")
    assert shown[P + "net_sales"] == pytest.approx(35516.37)
    assert shown[P + "total_operating_income"] == pytest.approx(35583.54)
    assert shown[P + "profit_before_tax"] == pytest.approx(6003.44)
    assert shown[P + "profit_after_tax"] == pytest.approx(4394.69)
