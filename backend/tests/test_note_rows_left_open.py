"""The note rows f1 printed and the request still did not carry, or carried mislabelled.

f1 is an Ind AS filing (standalone, INR lakhs). Seen on the requests of the stand-in capture:

* NOTE 3. The capital work-in-progress ageing schedule prints "As at 31 March 2025  1,186.25
  177.15  -  -  1,363.40". The caption was rebuilt as "As at 31 March 2025 - -" (the nil cells
  read as caption words), no dated-balance test read that as a date, and both rows were dropped as
  header lines; kept, they still carried the caption's own "31" and "2025" as figures.
* NOTE 22 (and 10). A row printed nil in every column ("(iii) Disputed dues - MSME  -  -  -") was
  dropped, so the request could not say it was printed; where its caption wrapped over four lines
  the head was lost with it.
* NOTE 10. The 31 March 2024 ageing schedule breaks across the page: its last rows and "Total" sit
  on the next page under the same column heading with no title, and were `unstated`.
* NOTES 13, 14, 23. A qualifier heading set flush over the rows — "Unsecured, considered good",
  "Current (unsecured, considered good)", "Current" — was joined onto the first caption under it.

The word geometry is f1's own, read from the PDF.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import note_columns, note_context
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources
from app.services.row_reconstruct import Word, build_line_items

NIL_ROW_FLAG = "printed_nil"     # `row_reconstruct.NIL_ROW_FLAG`

H = 0.009


def _words(rows):
    return [Word(text=t, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H)) for t, x0, x1, y in rows]


def _build(rows, *, on_face=False):
    items, _ = build_line_items(_words(rows), page_index=7, document_id="d", source_kind="native",
                                on_face=on_face)
    return items


def _figures(li):
    return sorted(str(ev.value) for ev in li.values.values())


CWIP_AGEING = [
    ('Particulars', 0.071, 0.149, 0.672), ('Less', 0.366, 0.398, 0.672), ('than', 0.402, 0.435, 0.672), ('1', 0.440, 0.448, 0.672), ('1-2', 0.502, 0.525, 0.672), ('years', 0.529, 0.569, 0.672), ('2-3', 0.623, 0.646, 0.672), ('years', 0.650, 0.689, 0.672), ('More', 0.712, 0.747, 0.672), ('than', 0.752, 0.785, 0.672), ('3', 0.789, 0.798, 0.672), ('Total', 0.893, 0.929, 0.672),
    ('year', 0.417, 0.448, 0.685), ('years', 0.770, 0.809, 0.685),
    ('As', 0.071, 0.086, 0.701), ('at', 0.090, 0.103, 0.701), ('31', 0.107, 0.123, 0.701), ('March', 0.127, 0.166, 0.701), ('2025', 0.170, 0.203, 0.701), ('1,186.25', 0.392, 0.448, 0.701), ('177.15', 0.524, 0.569, 0.701), ('-', 0.685, 0.689, 0.701), ('-', 0.805, 0.809, 0.701), ('1,363.40', 0.873, 0.929, 0.701),
]

# Note 10's 31 March 2025 schedule: every caption wraps, and four rows are nil in every column.
_NIL = [(0.365, 0.370), (0.459, 0.463), (0.552, 0.556), (0.645, 0.650), (0.738, 0.743),
        (0.831, 0.836), (0.925, 0.929)]


def _dashes(y):
    return [('-', x0, x1, y) for x0, x1 in _NIL]


RECEIVABLES_AGEING = [
    ('(i)', 0.071, 0.084, 0.479), ('Undisputed', 0.088, 0.161, 0.479), ('trade', 0.165, 0.199, 0.479),
    ('receivables', 0.071, 0.144, 0.492), ('-', 0.148, 0.152, 0.492), ('considered', 0.156, 0.227, 0.492), ('good', 0.231, 0.263, 0.492), ('10,486.15', 0.298, 0.370, 0.492), ('2,641.95', 0.400, 0.463, 0.492), ('196.40', 0.507, 0.556, 0.492), ('62.70', 0.613, 0.650, 0.492), ('25.20', 0.706, 0.743, 0.492), ('-', 0.831, 0.836, 0.492), ('13,412.40', 0.857, 0.929, 0.492),
    ('(ii)', 0.071, 0.088, 0.506), ('Undisputed', 0.092, 0.165, 0.506), ('trade', 0.169, 0.203, 0.506),
    ('receivables', 0.071, 0.144, 0.519), ('-', 0.148, 0.152, 0.519), ('which', 0.156, 0.194, 0.519), ('have', 0.198, 0.229, 0.519),
    ('significant', 0.071, 0.136, 0.533), ('increase', 0.141, 0.195, 0.533), ('in', 0.199, 0.210, 0.533), ('credit', 0.214, 0.251, 0.533),
    ('risk', 0.071, 0.093, 0.546), *_dashes(0.546),
    ('(iii)', 0.071, 0.091, 0.56), ('Undisputed', 0.095, 0.168, 0.56), ('trade', 0.172, 0.206, 0.56),
    ('receivables', 0.071, 0.144, 0.573), ('-', 0.148, 0.152, 0.573), ('credit', 0.156, 0.193, 0.573), ('impaired', 0.197, 0.254, 0.573), ('-', 0.365, 0.370, 0.573), ('-', 0.459, 0.463, 0.573), ('-', 0.552, 0.556, 0.573), ('88.15', 0.613, 0.650, 0.573), ('70.30', 0.706, 0.743, 0.573), ('80.20', 0.799, 0.836, 0.573), ('238.65', 0.880, 0.929, 0.573),
    ('(iv)', 0.071, 0.092, 0.588), ('Disputed', 0.096, 0.153, 0.588), ('trade', 0.157, 0.191, 0.588),
    ('receivables', 0.071, 0.144, 0.601), ('-', 0.148, 0.152, 0.601), ('considered', 0.156, 0.227, 0.601), ('good', 0.231, 0.263, 0.601), *_dashes(0.601),
    ('Total', 0.071, 0.107, 0.697), ('10,486.15', 0.298, 0.370, 0.697), ('2,641.95', 0.400, 0.463, 0.697), ('196.40', 0.507, 0.556, 0.697), ('150.85', 0.613, 0.650, 0.697), ('95.50', 0.706, 0.743, 0.697), ('80.20', 0.799, 0.836, 0.697), ('13,651.05', 0.857, 0.929, 0.697),
]

OTHER_CURRENT_ASSETS = [
    ('Particulars', 0.071, 0.153, 0.532), ('As', 0.559, 0.578, 0.532), ('at', 0.582, 0.598, 0.532), ('31', 0.603, 0.621, 0.532), ('March', 0.626, 0.673, 0.532), ('2025', 0.677, 0.715, 0.532), ('As', 0.773, 0.792, 0.532), ('at', 0.796, 0.812, 0.532), ('31', 0.817, 0.835, 0.532), ('March', 0.840, 0.887, 0.532), ('2024', 0.891, 0.929, 0.532),
    ('Unsecured,', 0.071, 0.156, 0.549), ('considered', 0.161, 0.244, 0.549), ('good', 0.249, 0.287, 0.549),
    ('Advances', 0.071, 0.136, 0.563), ('to', 0.140, 0.153, 0.563), ('suppliers', 0.158, 0.219, 0.563), ('396.75', 0.668, 0.715, 0.563), ('348.20', 0.882, 0.929, 0.563),
    ('Prepaid', 0.071, 0.121, 0.578), ('expenses', 0.126, 0.190, 0.578), ('124.30', 0.668, 0.715, 0.578), ('112.65', 0.882, 0.929, 0.578),
    ('Total', 0.071, 0.109, 0.623), ('1,196.15', 0.648, 0.715, 0.623), ('1,071.55', 0.863, 0.929, 0.623),
]

OTHER_FINANCIAL_LIABILITIES = [
    ('Particulars', 0.071, 0.153, 0.8), ('As', 0.559, 0.578, 0.8), ('at', 0.582, 0.598, 0.8), ('31', 0.603, 0.621, 0.8), ('March', 0.626, 0.673, 0.8), ('2025', 0.677, 0.715, 0.8), ('As', 0.773, 0.792, 0.8), ('at', 0.796, 0.812, 0.8), ('31', 0.817, 0.835, 0.8), ('March', 0.840, 0.887, 0.8), ('2024', 0.891, 0.929, 0.8),
    ('Current', 0.071, 0.128, 0.817),
    ('Interest', 0.071, 0.123, 0.832), ('accrued', 0.127, 0.181, 0.832), ('but', 0.185, 0.207, 0.832), ('not', 0.212, 0.234, 0.832), ('due', 0.238, 0.262, 0.832), ('41.80', 0.676, 0.715, 0.832), ('44.15', 0.890, 0.929, 0.832),
    ('Creditors', 0.071, 0.132, 0.847), ('for', 0.136, 0.155, 0.847), ('capital', 0.159, 0.204, 0.847), ('goods', 0.208, 0.249, 0.847), ('534.75', 0.668, 0.715, 0.847), ('486.20', 0.882, 0.929, 0.847),
]


# ── note 3: a dated row among nil cells ──────────────────────────────────────────────────────

def test_a_dated_ageing_row_with_nil_cells_is_a_row_without_its_date_as_figures():
    rows = [(li.source_label, _figures(li)) for li in _build(CWIP_AGEING)]
    assert rows == [("As at 31 March 2025", ["1186.25", "1363.40", "177.15"])]


def test_a_dash_inside_a_caption_is_still_the_captions():
    # A caption's own dash, left of every figure, is not a nil cell: it stays in the caption.
    rows = [li.source_label for li in _build(RECEIVABLES_AGEING)]
    assert rows[0] == "(i) Undisputed trade receivables - considered good"


# ── notes 10 and 22: a row printed nil in every column ────────────────────────────────────────

def test_a_nil_row_is_a_row_with_no_figure_and_its_whole_caption():
    items = _build(RECEIVABLES_AGEING)
    assert [li.source_label for li in items] == [
        "(i) Undisputed trade receivables - considered good",
        "(ii) Undisputed trade receivables - which have significant increase in credit risk",
        "(iii) Undisputed trade receivables - credit impaired",
        "(iv) Disputed trade receivables - considered good",
        "Total"]
    nil = [li for li in items if NIL_ROW_FLAG in li.confidence.flags]
    assert [li.source_label[:4] for li in nil] == ["(ii)", "(iv)"]
    assert all(not li.values for li in nil)


def test_a_face_still_drops_a_nil_row():
    rows = [('Capital', 0.071, 0.13, 0.30), ('reserve', 0.135, 0.19, 0.30), ('-', 0.70, 0.705, 0.30),
            ('-', 0.90, 0.905, 0.30),
            ('Retained', 0.071, 0.13, 0.32), ('earnings', 0.135, 0.20, 0.32),
            ('1,000.00', 0.65, 0.705, 0.32), ('900.00', 0.86, 0.905, 0.32)]
    assert [li.source_label for li in _build(rows, on_face=True)] == ["Retained earnings"]


# ── notes 13, 14, 23: a qualifier heading over the rows ──────────────────────────────────────

def test_a_qualifier_heading_is_the_rows_group_not_their_caption():
    items = _build(OTHER_CURRENT_ASSETS)
    assert [(li.source_label, li.group_hint) for li in items] == [
        ("Advances to suppliers", "Unsecured, considered good"),
        ("Prepaid expenses", "Unsecured, considered good"),
        ("Total", "Unsecured, considered good")]
    items = _build(OTHER_FINANCIAL_LIABILITIES)
    assert [(li.source_label, li.group_hint) for li in items] == [
        ("Interest accrued but not due", "Current"), ("Creditors for capital goods", "Current")]


def test_a_caption_that_wraps_is_still_joined():
    # Not a qualifier: the first line of a caption continued on the line with its figures.
    rows = [('Deposits', 0.071, 0.129, 0.334), ('with', 0.133, 0.162, 0.334), ('original', 0.166, 0.216, 0.334), ('maturity', 0.221, 0.278, 0.334), ('of', 0.282, 0.295, 0.334), ('more', 0.300, 0.335, 0.334), ('than', 0.339, 0.370, 0.334), ('three', 0.374, 0.410, 0.334), ('months', 0.414, 0.465, 0.334),
            ('but', 0.071, 0.093, 0.348), ('less', 0.097, 0.123, 0.348), ('than', 0.127, 0.158, 0.348), ('twelve', 0.162, 0.207, 0.348), ('months', 0.211, 0.262, 0.348), ('1,150.00', 0.655, 0.715, 0.348), ('1,050.00', 0.870, 0.929, 0.348)]
    assert [li.source_label for li in _build(rows)] == [
        "Deposits with original maturity of more than three months but less than twelve months"]


# ── the request: nil rows, and a table continued across a page ───────────────────────────────

AGES = ("Not due", "Less than 6 months", "Total")


def _cell(key, amount, heading, x, page):
    return ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount), basis=Basis.STANDALONE,
                          period_label=key, column_heading=heading,
                          provenance=Provenance(page_index=page, value_bbox=BBox(
                              x0=x - 0.05, y0=0.5, x1=x, y1=0.51)))


def _aged(caption, amounts, page, columns=AGES):
    row = NoteItem(raw_label=caption, printed_columns=list(columns))
    for k, (amount, heading, x) in enumerate(zip(amounts, columns, (0.37, 0.46, 0.93))):
        row.set_value(_cell("current" if k == 0 else f"current_col{k}", amount, heading, x, page))
    return row


def _nil(caption, columns=AGES):
    row = NoteItem(raw_label=caption, printed_columns=list(columns))
    row.confidence.flags.append(NIL_ROW_FLAG)
    return row


def _rou() -> NotesTable:
    row = NoteItem(raw_label="Balance as at 31 March 2025")
    row.set_value(ExtractedValue(value_raw=Decimal("1"), value=Decimal("1"), basis=Basis.STANDALONE,
                                 period_label="Total", period_display="Total", column_index=0,
                                 provenance=Provenance(page_index=7)))
    return NotesTable(note_number="4", title="Right-of-use assets", items=[row],
                      source_text="Particulars Total Balance as at 31 March 2025 1.00",
                      source_pages=[7])


def _broken_schedule(*, first_ends_in_total=False, columns=AGES):
    first_rows = [_aged("(i) Undisputed trade receivables - considered good",
                        ("9512.40", "2402.25", "12146.85"), 9),
                  _nil("(iv) Disputed trade receivables - considered good")]
    if first_ends_in_total:
        first_rows.append(_aged("Total", ("9512.40", "2402.25", "12146.85"), 9))
    first = NotesTable(
        note_number="10", title="Trade receivables", items=first_rows, source_pages=[9],
        source_text=("Trade receivables ageing schedule as at 31 March 2024 (outstanding for "
                     "following periods) Particulars Not due Less than 6 months Total "
                     "(i) Undisputed trade receivables - considered good 9,512.40 2,402.25 "
                     "12,146.85 (iv) Disputed trade receivables - considered good - - -"))
    second = NotesTable(
        note_number="10", title="Trade receivables", source_pages=[10],
        items=[_nil("(vi) Disputed trade receivables - credit impaired", columns),
               _aged("Total", ("9512.40", "2402.25", "12343.05"), 10, columns)],
        source_text=("Particulars Not due Less than 6 months Total (vi) Disputed trade "
                     "receivables - credit impaired - - - Total 9,512.40 2,402.25 12,343.05"))
    return [_rou(), first, second]


def _identified(tables) -> dict[str, list[dict]]:
    items = [LineItemDef(key="k", label="k", note_source=NoteSource(note_title_any=[".*"]))]
    return {n["note"]: n["rows"] for n in
            note_context.identified_notes(LineItemSet(items=items), tables)}


def test_a_table_continued_across_a_page_takes_the_period_it_continues():
    rows = _identified(_broken_schedule())["10"]
    assert [(r["caption"][:4], r["period"], r.get("period_as_printed"), r.get("nil"))
            for r in rows] == [
        ("(i) ", "prior", "as at 31 March 2024", None),
        ("(iv)", "prior", "as at 31 March 2024", True),
        ("(vi)", "prior", "as at 31 March 2024 (carried from page 10)", True),
        ("Tota", "prior", "as at 31 March 2024 (carried from page 10)", None)]
    assert all("figures" not in r for r in rows if r.get("nil"))
    # The citation resolves to the carried period, as the request states it.
    resolved, unresolved = resolve_sources(
        [SourceRef(note="10", caption="Total", column="Total", period="prior")],
        _broken_schedule())
    assert unresolved == [] and resolved[0]["figures"] == {"prior": "12343.05"}


def test_a_continuation_that_cannot_be_shown_to_be_the_same_table_stays_unstated():
    # The earlier table ended in its total: what follows is another table.
    rows = _identified(_broken_schedule(first_ends_in_total=True))["10"]
    assert rows[-1]["caption"] == "Total" and rows[-1]["period"] == "unstated"
    # Different columns: not the same table.
    rows = _identified(_broken_schedule(columns=("Not due", "Over 6 months", "Total")))["10"]
    assert rows[-1]["period"] == "unstated"


def test_a_nil_row_is_never_a_header_line():
    table = _broken_schedule()[2]
    lay = note_columns.table_layout(table, (2025, 3, 31))
    assert not lay[id(table.items[0])].get("fragment")
