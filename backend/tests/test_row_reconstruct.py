"""Row reconstruction (shared by native-PDF and OCR paths): value/label/note splitting
and the conservative wrapped-label merge."""
from __future__ import annotations

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items


def _w(text: str, x0: float, y0: float, x1: float, y1: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1))


def _build(words: list[Word]):
    items, _ = build_line_items(words, page_index=0, document_id="d1", source_kind="native")
    return items


def _slots(item) -> dict[tuple[str, str | None], str]:
    return {(v.basis.value, v.period_label): str(v.value) for v in item.values.values()}


def test_single_line_row_splits_label_note_and_values():
    items = _build([
        _w("Trade", 0.10, 0.20, 0.16, 0.22),
        _w("receivables", 0.17, 0.20, 0.28, 0.22),
        _w("Note", 0.50, 0.20, 0.55, 0.22),
        _w("15", 0.56, 0.20, 0.58, 0.22),
        _w("3,410", 0.72, 0.20, 0.80, 0.22),
        _w("2,900", 0.86, 0.20, 0.94, 0.22),
    ])
    assert len(items) == 1
    li = items[0]
    assert li.source_label == "Trade receivables"
    assert li.note_number == "15"                                   # note ref, not a value
    cur = li.get_value(Basis.CONSOLIDATED, period_label="current")
    prior = li.get_value(Basis.CONSOLIDATED, period_label="prior")
    assert cur is not None and int(cur.value) == 3410
    assert prior is not None and int(prior.value) == 2900


def test_a_note_reference_is_recorded_as_a_citation_on_both_fields():
    """``note_number`` and ``note_refs`` are the SAME fact — the note this row points at.

    Pinned because the field's own comment used to say it was "set when this item lives inside a
    note", and a reader that believed it discarded every face row printing a note reference. There
    is no membership meaning available to it: the rows printed inside a note are ``NoteItem``s on a
    ``NotesTable``, and this row is a face row with a figure of its own. Whether a row is face or
    note is the PAGE's classification, never this.
    """
    items = _build([
        _w("Trade", 0.10, 0.20, 0.16, 0.22),
        _w("receivables", 0.17, 0.20, 0.28, 0.22),
        _w("15", 0.56, 0.20, 0.58, 0.22),
        _w("3,410", 0.72, 0.20, 0.80, 0.22),
    ])
    li = items[0]

    assert li.note_number == "15"
    assert [r.numbers for r in li.note_refs] == [["15"]], (
        "the two fields must carry one answer; a reader treating them as different facts is how "
        "the citation got read as membership")
    # The row is still a row of the statement, with its own figure.
    assert li.source_label == "Trade receivables"
    assert int(li.get_value(Basis.CONSOLIDATED, period_label="current").value) == 3410


def test_a_chinese_chapter_prefixed_note_column_is_linked_to_its_subsection():
    items = _build([
        _w("项目", 0.10, 0.15, 0.16, 0.17),
        _w("附注", 0.50, 0.15, 0.55, 0.17),
        _w("2024", 0.70, 0.15, 0.76, 0.17),
        _w("2023", 0.84, 0.15, 0.90, 0.17),
        _w("货币资金", 0.10, 0.20, 0.20, 0.22),
        _w("七、1", 0.52, 0.20, 0.57, 0.22),
        _w("451,359,912.13", 0.70, 0.20, 0.80, 0.22),
        _w("389,309,367.78", 0.84, 0.20, 0.94, 0.22),
    ])

    line = next(item for item in items if item.source_label == "货币资金")
    assert line.note_number == "1"
    assert [ref.numbers for ref in line.note_refs] == [["1"]]


def test_wrapped_label_is_merged_into_the_valued_line():
    """A label that wraps across two tight, left-aligned lines is stitched back together
    rather than truncated to the fragment on the valued line."""
    items = _build([
        _w("Property,", 0.10, 0.30, 0.18, 0.315),
        _w("plant", 0.19, 0.30, 0.24, 0.315),
        _w("and", 0.25, 0.30, 0.29, 0.315),
        # continuation line, tight spacing (gap << line height), same left edge:
        _w("equipment", 0.10, 0.318, 0.22, 0.333),
        _w("12,500", 0.72, 0.318, 0.82, 0.333),
    ])
    assert len(items) == 1
    li = items[0]
    assert li.source_label == "Property, plant and equipment"
    cur = li.get_value(Basis.CONSOLIDATED, period_label="current")
    assert cur is not None and int(cur.value) == 12500
    # provenance still anchors on the value's own bbox
    assert cur.provenance is not None and cur.provenance.bbox is not None


def test_section_header_is_not_merged_into_next_item():
    """An ALL-CAPS / standalone header line must stay a header, not be folded into the
    first item below it."""
    items = _build([
        _w("NON-CURRENT", 0.10, 0.30, 0.24, 0.315),
        _w("ASSETS", 0.25, 0.30, 0.33, 0.315),
        _w("Goodwill", 0.10, 0.318, 0.20, 0.333),
        _w("8,000", 0.72, 0.318, 0.80, 0.333),
    ])
    assert len(items) == 1
    assert items[0].source_label == "Goodwill"                      # header not swallowed


def test_a_second_figure_in_one_column_does_not_replace_the_first():
    """Two figures inside the SAME value column (a footnote-marked repeat, an OCR double read)
    land on one (basis, period) key. ``column_guard`` says facts differing on nothing declared are
    duplicates, so the first printed figure is kept — overwriting means the row reports whichever
    cell the geometry happened to visit last, with nothing in the output to show it happened."""
    logs: list[str] = []
    words = [
        _w("Trade", 0.10, 0.20, 0.16, 0.212), _w("receivables", 0.17, 0.20, 0.28, 0.212),
        _w("3,410", 0.72, 0.20, 0.78, 0.212), _w("3,411", 0.73, 0.20, 0.79, 0.212),
        _w("2,900", 0.86, 0.20, 0.92, 0.212),
        _w("Inventories", 0.10, 0.24, 0.20, 0.252),
        _w("2,000", 0.72, 0.24, 0.78, 0.252), _w("1,800", 0.86, 0.24, 0.92, 0.252),
        _w("Cash", 0.10, 0.28, 0.16, 0.292),
        _w("1,204", 0.72, 0.28, 0.78, 0.292), _w("980", 0.86, 0.28, 0.92, 0.292),
    ]
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                               log=logs.append)
    tr = next(i for i in items if "Trade" in i.source_label)
    cur = tr.get_value(Basis.CONSOLIDATED, period_label="current")
    assert cur is not None and int(cur.value) == 3410
    assert len(tr.values) == 2                                  # not three, and not overwritten
    assert any("duplicate_fact_dropped" in m for m in logs), logs


def test_a_basis_caption_governs_a_contiguous_run_of_columns():
    """A band caption may be printed left-aligned over its first column or centred over the pair,
    so the columns cannot be handed to the NEAREST caption: on a four-column Group | Company page
    the Group's comparative is a hair nearer the Company caption, and last year's Group figures
    are then read as the Company's current year."""
    from app.core.models.enums import Basis as B
    from app.services.row_reconstruct import _basis_of_columns

    # "Group" printed over the first column, "Company" over the third — the HKEX house style.
    bands = [(B.CONSOLIDATED, 0.527), (B.STANDALONE, 0.782)]
    cols = [0.533, 0.655, 0.781, 0.899]
    assert _basis_of_columns(cols, bands) == {0: B.CONSOLIDATED, 1: B.CONSOLIDATED,
                                             2: B.STANDALONE, 3: B.STANDALONE}
    # …and the same holds when each caption is centred over its own pair.
    assert _basis_of_columns(cols, [(B.CONSOLIDATED, 0.60), (B.STANDALONE, 0.84)]) == {
        0: B.CONSOLIDATED, 1: B.CONSOLIDATED, 2: B.STANDALONE, 3: B.STANDALONE}


def _right_aligned(text: str, right: float, y: float) -> Word:
    """A figure as a statement prints it: right-aligned, so its width — and therefore its
    x-CENTRE — depends on how many digits it has."""
    w = 0.008 * len(text)
    return _w(text, right - w, y, right, y + 0.012)


def test_a_column_of_mixed_width_figures_is_still_one_column():
    """Two constants called ``_COL_TOL`` were defined at module scope. The later one — the matrix
    path's 0.012, measured on right EDGES, which do not drift — silently governed the comparative
    path too, where the tolerance is measured on x-centres that drift with the width of the number
    printed in the column: "1,204,500" and "980" right-aligned in one column are ~0.024 of the page
    apart at their centres.

    So a column of mixed-width figures fragmented into clusters too small to clear
    ``_COL_MIN_ROWS``, the page came out with NO columns at all, and every row fell back to
    positional order — which is precisely the mis-load ``_value_column_bands`` exists to prevent:
    "Pledged deposits" is printed for the prior year only, and read positionally its single figure
    claims the current year, over-stating one period and under-stating the other."""
    from app.services import row_reconstruct as rr

    # One name per meaning: centres drift, right edges do not, so the two paths cannot share a value.
    assert rr._COL_TOL > rr._MATRIX_COL_TOL

    rows = [("Property, plant and equipment", "1,204,500", "1,100,400"),
            ("Inventories", "1,204,500", "1,100,400"),
            ("Trade receivables", "980", "890"),
            ("Cash", "112", "105"),
            ("Pledged deposits", "", "2,031")]                     # prior year only
    words: list[Word] = []
    for i, (label, cur, prior) in enumerate(rows):
        y = 0.20 + i * 0.04
        words.append(_w(label, 0.10, y, 0.10 + 0.01 * len(label), y + 0.012))
        if cur:
            words.append(_right_aligned(cur, 0.80, y))
        if prior:
            words.append(_right_aligned(prior, 0.94, y))

    items = _build(words)
    pledged = next(i for i in items if "Pledged" in i.source_label)
    assert list(_slots(pledged)) == [("consolidated", "prior")], _slots(pledged)
    assert _slots(pledged)[("consolidated", "prior")] == "2031"
    # …and the rows that report both periods are unaffected.
    inv = next(i for i in items if i.source_label == "Inventories")
    assert _slots(inv) == {("consolidated", "current"): "1204500",
                           ("consolidated", "prior"): "1100400"}


def test_loosely_spaced_label_line_is_not_merged():
    """A label-only line far above the next valued line (paragraph gap) is a separate
    heading, not a wrapped continuation."""
    items = _build([
        _w("Other", 0.10, 0.20, 0.16, 0.215),
        _w("reserves", 0.17, 0.20, 0.27, 0.215),
        # big vertical gap → different block:
        _w("Retained", 0.10, 0.40, 0.19, 0.415),
        _w("earnings", 0.20, 0.40, 0.29, 0.415),
        _w("5,100", 0.72, 0.40, 0.80, 0.415),
    ])
    assert len(items) == 1
    assert items[0].source_label == "Retained earnings"             # "Other reserves" dropped, not merged


# --- "notes" is a word of the balance sheet's own vocabulary ----------------------------------

def _tok(text: str, x0: float, y0: float = 0.30) -> Word:
    """One printed word, sized from its text so the gaps between words are realistic."""
    return _w(text, x0, y0, x0 + 0.011 * len(text), y0 + 0.014)


def test_a_caption_ending_in_notes_keeps_its_caption_and_both_figures():
    """The row that made this urgent: "Interest on guaranteed notes 201,551 221,188". Reading the
    caption's last word as the note keyword took the FIRST figure as the note reference and dropped
    it — a reported number deleted, silently, with the caption truncated to "Interest on guaranteed"
    so nothing downstream could even map it."""
    items = _build([_tok("Interest", 0.10), _tok("on", 0.195), _tok("guaranteed", 0.225),
                    _tok("notes", 0.34), _tok("201,551", 0.62), _tok("221,188", 0.80)])
    assert len(items) == 1
    assert items[0].source_label == "Interest on guaranteed notes"
    assert items[0].note_number is None
    assert sorted(str(v.value) for v in items[0].values.values()) == ["201551", "221188"]


def test_a_caption_ending_in_notes_still_takes_its_note_from_the_note_column():
    """The other half: the same caption WITH a real note reference printed in the note column.
    The caption stays whole and the reference is still found — by where it sits, which is the
    better evidence, rather than by the word before it."""
    header = [_tok("Notes", 0.60, y0=0.20), _tok("2025", 0.72, y0=0.20), _tok("2024", 0.86, y0=0.20)]
    body = [_tok("Guaranteed", 0.10), _tok("notes", 0.216),
            _tok("36", 0.61), _tok("3,877,188", 0.72), _tok("2,151,000", 0.86)]
    # A note column is only detected when a run of bare note numbers backs its header.
    other = [_tok("Bank", 0.10, y0=0.36), _tok("borrowings", 0.16, y0=0.36),
             _tok("37", 0.61, y0=0.36), _tok("10,886,034", 0.72, y0=0.36),
             _tok("2,523,016", 0.86, y0=0.36)]
    items = _build(header + body + other)
    by_label = {i.source_label: i for i in items}
    assert by_label["Guaranteed notes"].note_number == "36"
    assert sorted(str(v.value) for v in by_label["Guaranteed notes"].values.values()) == [
        "2151000", "3877188"]


def test_an_inline_note_keyword_in_a_cell_of_its_own_still_reads_as_one():
    """The layout the keyword exists for: no note column, the reference printed inline as
    "Note 14" in a cell well clear of the caption."""
    items = _build([_tok("Cash", 0.10), _tok("and", 0.16), _tok("bank", 0.21),
                    _tok("Note", 0.55), _tok("14", 0.60),
                    _tok("1,000", 0.72), _tok("900", 0.86)])
    assert len(items) == 1
    assert items[0].source_label == "Cash and bank"
    assert items[0].note_number == "14"
    assert sorted(str(v.value) for v in items[0].values.values()) == ["1000", "900"]


# ── the column header of a table that does not start at the top of the page ────────────────────
#
# A mainland annual report prints several notes to a page. 688008 (澜起科技) page 219 carries
# notes 59, 60 and 61, so note 61's own header band sits at y=0.68/0.70 — and every caption over
# its columns was thrown away by a page-fraction cut-off that was meant to be the FALLBACK bound
# for a page carrying no figure at all, not a second bound applied on top of "above this table's
# first figure". What was lost is the two-level grid the note prints:
#
#     项目 |     本期发生额     |     上期发生额
#          |  收入   |  成本   |  收入   |  成本
#     主营业务 | 3,628,769,555.93 | 1,516,811,244.12 | 2,278,141,066.50 | 933,947,676.30
#
# With the header invisible the four columns fell back to positional labels, so the CURRENT
# period's COST was published as the PRIOR period's revenue: Sales (Revenues) read
# 1,516,811,244.12 for 2023 against a printed 2,278,141,066.50.


def _revenue_cost_table(top: float) -> list[Word]:
    """Note 61's table as the filing prints it, with its header band starting at ``top``."""
    words = [
        _w("项目", 0.10, top, 0.14, top + 0.008),
        _w("本期发生额", 0.34, top - 0.008, 0.44, top),
        _w("上期发生额", 0.64, top - 0.008, 0.74, top),
        _w("收入", 0.30, top + 0.016, 0.34, top + 0.024),
        _w("成本", 0.47, top + 0.016, 0.51, top + 0.024),
        _w("收入", 0.60, top + 0.016, 0.64, top + 0.024),
        _w("成本", 0.77, top + 0.016, 0.81, top + 0.024),
    ]
    # All three printed rows, because the value COLUMNS are clustered from the figures: one row
    # establishes no bands, and a fixture that omitted the rest would be testing that rather than
    # the header band this exists for.
    for i, (label, cur, cur_cost, prior, prior_cost) in enumerate((
        ("主营业务", "3,628,769,555.93", "1,516,811,244.12",
                     "2,278,141,066.50", "933,947,676.30"),
        ("其他业务", "10,141,512.36", "6,803,694.42", "7,597,431.73", "5,268,587.65"),
        ("合计", "3,638,911,068.29", "1,523,614,938.54",
                 "2,285,738,498.23", "939,216,263.95"),
    )):
        y = top + 0.033 + i * 0.017
        words += [
            _w(label, 0.10, y, 0.18, y + 0.008),
            _w(cur, 0.26, y, 0.38, y + 0.008),
            _w(cur_cost, 0.43, y, 0.55, y + 0.008),
            _w(prior, 0.56, y, 0.68, y + 0.008),
            _w(prior_cost, 0.74, y, 0.84, y + 0.008),
        ]
    return words


def _main_business(words: list[Word]):
    items, _ = build_line_items(words, page_index=218, document_id="d1", source_kind="native")
    return next(li for li in items if li.source_label == "主营业务")


def test_a_two_level_header_below_mid_page_is_still_read():
    li = _main_business(_revenue_cost_table(top=0.684))

    assert _slots(li) == {
        ("consolidated", "current"): "3628769555.93",
        ("consolidated", "current:cost"): "1516811244.12",
        ("consolidated", "prior"): "2278141066.50",
        ("consolidated", "prior:cost"): "933947676.30",
    }


def test_the_same_table_at_the_top_of_a_page_reads_identically():
    """The bound is "above this table's first figure", so WHERE on the page changes nothing."""
    low = _slots(_main_business(_revenue_cost_table(top=0.684)))
    high = _slots(_main_business(_revenue_cost_table(top=0.120)))

    assert low == high


def test_the_current_period_cost_is_never_published_as_the_prior_revenue():
    """The consequence, stated as the figure a reader would have taken.

    Named separately from the slot assertion above because this is the defect: each column was
    internally consistent, so nothing downstream could see that the 2023 revenue column held
    2024's cost of sales.
    """
    li = _main_business(_revenue_cost_table(top=0.684))

    prior = li.get_value(Basis.CONSOLIDATED, period_label="prior")
    assert prior is not None and str(prior.value) == "2278141066.50"
    assert str(prior.value) != "1516811244.12"
