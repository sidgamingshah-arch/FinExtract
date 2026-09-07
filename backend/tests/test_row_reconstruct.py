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


def test_a_chinese_chapter_prefixed_note_column_keeps_its_chapter():
    """The chapter is HALF THE IDENTITY, and this test used to assert it away.

    A CSRC filing numbers its notes WITHIN each top-level chapter — 七、合并财务报表项目注释 runs
    1..80, 十九、母公司财务报表主要项目注释 restarts at 1 — so on 澜起科技 688008 fifteen of
    forty-eight numbers named two different notes. The face already prints the identity in full
    (the 附注 column reads 七、1) and the reader was throwing the chapter away, leaving a citation
    that could not say which note it meant. Other Receivables (CP) published 2,484,202,201.08
    against a printed 4,143,856.36 because the parent company's note and the group's were pooled.

    An English filing prints no chapter and its citations stay bare — the test below pins that.
    """
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
    assert line.note_number == "七、1"
    assert [ref.numbers for ref in line.note_refs] == [["七、1"]]
    # …and the figures are untouched: the note column is still a note column. `_is_note_number`
    # only ever asked whether a token is note-reference SHAPED, which has not changed.
    assert _slots(line) == {("consolidated", "current"): "451359912.13",
                            ("consolidated", "prior"): "389309367.78"}


def test_an_unprefixed_note_reference_stays_bare():
    """An English/HKEX filing has no chapters, and its citations must not grow one."""
    items = _build([
        _w("Trade", 0.10, 0.20, 0.16, 0.22),
        _w("receivables", 0.17, 0.20, 0.28, 0.22),
        _w("15", 0.52, 0.20, 0.55, 0.22),
        _w("3,410", 0.72, 0.20, 0.80, 0.22),
        _w("2,900", 0.86, 0.20, 0.94, 0.22),
    ])

    line = next(item for item in items if item.source_label == "Trade receivables")
    assert line.note_number == "15"


def test_the_chapter_separator_a_filing_prints_is_normalised():
    """A filing sets 、 or a comma or a full stop; the note side writes one spelling.

    If the two disagree the citation stops matching the note it names, which is the failure this
    whole change exists to end.
    """
    from app.services.row_reconstruct import _note_ref_value

    assert _note_ref_value("七、9") == "七、9"
    assert _note_ref_value("七 、 9") == "七、9"
    assert _note_ref_value("七,9") == "七、9"
    assert _note_ref_value("七.9") == "七、9"
    assert _note_ref_value("十九、2") == "十九、2"
    assert _note_ref_value("9") == "9"
    assert _note_ref_value("16(b)") == "16(b)"
    assert _note_ref_value("1,234.56") is None


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


# ── a mainland statement's own top-level lines ─────────────────────────────────────────────────
#
# The residual framework's eligibility list already forbids sweeping "a section subtotal,
# statement total, …" into a bucket, and that guard reads `role`. A CAS face numbers its
# statement-level lines 一、…八、 and nothing else, so untagged they were LINE rows and the sweep
# took them: measured on two filings, is_pl__other_operating_expenses carried the operating
# profit, the pre-tax profit and the net profit added together (4.49bn against a company with
# 3.64bn of revenue), and cf_oper_indirect__other_non_cash_adjs_oper carried 6.70bn of CLOSING
# CASH as a non-cash adjustment.


def _cas_face(label: str, on_face: bool = True):
    words = [
        _w(label, 0.10, 0.20, 0.30, 0.21),
        _w("1,412,617,850.07", 0.60, 0.20, 0.75, 0.21),
        _w("其他项目", 0.10, 0.24, 0.20, 0.25),
        _w("96,006,550.08", 0.62, 0.24, 0.75, 0.25),
    ]
    items, _ = build_line_items(words, page_index=153, document_id="d1",
                                source_kind="native", on_face=on_face)
    return next(li for li in items if (li.source_label or "").startswith(label[:4]))


def test_an_enumerated_face_caption_is_a_statement_total():
    from app.core.models.enums import LineRole

    for label in ("一、营业总收入", "三、营业利润", "四、利润总额", "五、净利润",
                  "六、其他综合收益的税后净额", "八、每股收益"):
        assert _cas_face(label).role is LineRole.TOTAL, label


def test_an_unenumerated_caption_beside_it_is_an_ordinary_line():
    """A component is prefixed 其中：/加：/减：, or carries no prefix at all."""
    from app.core.models.enums import LineRole

    for label in ("其中：营业收入", "加：其他收益", "减：所得税费用", "销售费用"):
        assert _cas_face(label).role is LineRole.LINE, label


def test_a_note_reference_in_the_same_form_is_not_a_total():
    """The same page prints its note references as 七、61 / 七、70, and one of those arrives as a
    row's whole label when the caption beside it is lost. A note reference is not a subtotal."""
    from app.core.models.enums import LineRole

    assert _cas_face("七、61").role is LineRole.LINE
    assert _cas_face("七、70").role is LineRole.LINE


def test_an_enumeration_in_the_MIDDLE_of_a_caption_promotes_nothing():
    """Anchored, because a caption carrying one mid-string is a reconstruction failure.

    An empty 资产处置收益 row absorbs the caption printed beneath it and arrives as
    "资产处置收益（损失以“－”号填列） 三、营业利润（亏损以“－”号填列）". Reading the enumeration
    out of the middle of that would tag a row whose caption belongs to a different line — the
    row needs the review queue, not a promotion that hides it.
    """
    from app.core.models.enums import LineRole

    glued = "资产处置收益（损失以“－”号填列） 三、营业利润（亏损以“－”号填列）"
    assert _cas_face(glued).role is LineRole.LINE


def test_the_same_numbering_inside_a_note_means_nothing_about_totals():
    """Inside a note it is a sub-note enumeration, not the statement's spine."""
    from app.core.models.enums import LineRole

    assert _cas_face("三、营业利润", on_face=False).role is LineRole.LINE


# ── a column needs a figure in it ──────────────────────────────────────────────────────────────
#
# 688008's consolidated balance sheet prints its title and its column header with a period —
# "合并资产负债表 2024 年12 月31 日" and "项目 附注 2024 年12 月31 日 2023 年12 月31 日" — and
# "2024", "12" and "31" all read as numbers, so both rows contributed x-centres of their own,
# left of the real value columns. Together with the page's FOLIO ("150 / 256", whose 150 and 256
# are neither years nor days of the month) they clustered into a THIRD band: the real
# current-year column became column 1 and every figure in it was labelled `current_col1`, a slot
# no screen and no export reads. 21 of 23 rows on the page lost their current-year figure, and
# Other Receivables (CP) published 2,484,202,211.08 against a printed 4,143,856.36.


def _cn_balance_sheet(*, folio: bool = True) -> list[Word]:
    """The page as 688008 prints it: title, header band, three rows, and the folio."""
    words = [
        _w("合并资产负债表", 0.463, 0.119, 0.560, 0.128),
        _w("2024", 0.449, 0.140, 0.480, 0.148),
        _w("年12", 0.489, 0.140, 0.520, 0.148),
        _w("月31", 0.533, 0.140, 0.564, 0.148),
        _w("项目", 0.248, 0.192, 0.272, 0.200),
        _w("附注", 0.419, 0.192, 0.443, 0.200),
        _w("2024", 0.519, 0.189, 0.550, 0.197),
        _w("年12", 0.558, 0.189, 0.589, 0.197),
        _w("2023", 0.727, 0.189, 0.758, 0.197),
        _w("年12", 0.767, 0.189, 0.798, 0.197),
    ]
    for i, (label, note, cur, pri) in enumerate((
            ("货币资金", "七、1", "6,843,296,852.61", "5,743,574,648.73"),
            ("应收账款", "七、5", "387,791,885.96", "294,253,723.40"),
            ("其他应收款", "七、9", "4,143,856.36", "3,887,733.35"),
    )):
        y = 0.224 + i * 0.018
        words += [
            _w(note, 0.398, y, 0.430, y + 0.008),
            _w(cur, 0.566, y, 0.680, y + 0.008),
            _w(pri, 0.768, y, 0.882, y + 0.008),
            _w(label, 0.169, y + 0.002, 0.239, y + 0.010),
        ]
    if folio:
        words += [_w("150", 0.490, 0.916, 0.512, 0.924),
                  _w("/", 0.520, 0.916, 0.526, 0.924),
                  _w("256", 0.528, 0.916, 0.550, 0.924)]
    return words


def _bs_row(words, label):
    items, _ = build_line_items(words, page_index=149, document_id="d1", source_kind="native",
                                statement="balance_sheet", on_face=True,
                                page_scope="consolidated")
    return next(li for li in items if (li.source_label or "").endswith(label))


def test_a_title_and_a_header_date_do_not_create_a_value_column():
    row = _bs_row(_cn_balance_sheet(), "其他应收款")

    assert _slots(row) == {("consolidated", "current"): "4143856.36",
                           ("consolidated", "prior"): "3887733.35"}


def test_the_page_folio_is_not_a_figure():
    """"150 / 256" is neither a year nor a day of the month, so a date-fragment test cannot see
    it — and those two numbers were the only non-date support the phantom column had."""
    with_folio = _slots(_bs_row(_cn_balance_sheet(folio=True), "其他应收款"))
    without = _slots(_bs_row(_cn_balance_sheet(folio=False), "其他应收款"))

    assert with_folio == without
    assert ("consolidated", "current") in with_folio


def test_every_row_on_the_page_keeps_its_current_year_figure():
    words = _cn_balance_sheet()
    for label, cur in (("货币资金", "6843296852.61"), ("应收账款", "387791885.96"),
                       ("其他应收款", "4143856.36")):
        assert _slots(_bs_row(words, label))[("consolidated", "current")] == cur, label


# ── a complete caption is a line item, not the head of a wrap ─────────────────────────────────
#
# A CSRC filing prints every line of the standard statement layout whether the filer uses it or
# not, so a balance-sheet page carries 衍生金融资产 and 应收票据 with no figures beside them. On
# 688008 the intra-cell leading equals the row pitch, so a label-only row a full row above the
# figures is spaced exactly like a caption's continuation — and the wrap merge ate them:
#
#     衍生金融资产                       (no figures this year)
#     应收票据                           (no figures this year)
#     七、5   387,791,885.96   294,253,723.40
#     应收账款
#
# came out as ONE row, "衍生金融资产应收票据应收账款", holding 应收账款's figures. Four such rows
# on that page carried eleven captions between them, and the ten empty line items were gone.
#
# Geometry cannot separate them. What can is MEANING: a wrapped first line is an incomplete
# fragment, a line item is a complete caption. See `_CAS_FACE_CAPTIONS` and `known_captions`.


def _empty_items_then_a_valued_row() -> list[Word]:
    """The shape as 688008 prints it: two empty line items, then one that has figures.

    The label sits ~0.002 BELOW its own figures, which is how a vertically-centred cell prints,
    and consecutive line items are a full 0.017 apart.
    """
    return [
        _w("交易性金融资产", 0.169, 0.243, 0.29, 0.253),
        _w("1,783,494,750.68", 0.566, 0.241, 0.68, 0.251),
        _w("衍生金融资产", 0.169, 0.260, 0.27, 0.270),
        _w("应收票据", 0.169, 0.277, 0.24, 0.287),
        _w("七、5", 0.398, 0.2926, 0.43, 0.3026),
        _w("387,791,885.96", 0.579, 0.2929, 0.69, 0.3029),
        _w("294,253,723.40", 0.782, 0.2929, 0.89, 0.3029),
        _w("应收账款", 0.169, 0.2948, 0.24, 0.3048),
    ]


def _labels(words):
    items, _ = build_line_items(words, page_index=149, document_id="d1", source_kind="native",
                                statement="balance_sheet", on_face=True,
                                page_scope="consolidated")
    return [li.source_label for li in items]


def test_an_empty_line_item_is_not_folded_into_the_next_valued_row():
    """The empty items do not become rows — a row needs a figure, and that was true before.

    What changed is that they no longer take 应收账款's caption with them: the glued label is
    gone and the valued row is itself. Asserted as the absence of the glue plus the presence of
    the real caption, because those are the two things that were wrong.
    """
    labels = _labels(_empty_items_then_a_valued_row())

    assert "衍生金融资产应收票据应收账款" not in labels
    assert "应收账款" in labels, labels
    assert not any(l and "衍生金融资产" in l and l != "衍生金融资产" for l in labels), labels


def test_the_valued_row_keeps_its_own_caption_and_figures():
    words = _empty_items_then_a_valued_row()
    items, _ = build_line_items(words, page_index=149, document_id="d1", source_kind="native",
                                statement="balance_sheet", on_face=True,
                                page_scope="consolidated")

    row = next(li for li in items if li.source_label == "应收账款")
    assert row.note_number == "七、5"
    assert _slots(row) == {("consolidated", "current"): "387791885.96",
                           ("consolidated", "prior"): "294253723.40"}


def test_a_genuinely_wrapped_caption_still_merges():
    """The other half of the contract, and the reason the test is MEANING and not geometry: an
    incomplete fragment has to keep merging or a caption comes out truncated.

    The continuation is printed BELOW the figures — what a vertically-centred cell looks like — so
    it folds BACKWARD into the row it completes rather than forward into the next one. This once
    asserted the truncated 负债和所有者权益（或, on the grounds that the fragment was at least not
    orphaned and had not swallowed the caption beneath it; the whole caption is what the filing
    prints, and it is what the concept's alias is written against.
    """
    labels = _labels([
        _w("负债和所有者权益（或", 0.204, 0.093, 0.38, 0.103),
        _w("7,388,035,311.08", 0.566, 0.0996, 0.68, 0.1096),
        _w("股东权益）总计", 0.151, 0.1095, 0.274, 0.1195),
    ])

    assert labels == ["负债和所有者权益（或股东权益）总计"], labels


def test_an_english_wrap_is_untouched_by_the_caption_test():
    """No rulebook, no CAS caption — the English path merges on geometry exactly as before."""
    items, _ = build_line_items([
        _w("Property,", 0.10, 0.300, 0.18, 0.315),
        _w("plant", 0.19, 0.300, 0.24, 0.315),
        _w("and", 0.25, 0.300, 0.29, 0.315),
        _w("equipment", 0.10, 0.318, 0.22, 0.333),
        _w("12,500", 0.72, 0.318, 0.82, 0.333),
    ], page_index=0, document_id="d1", source_kind="native")

    assert [li.source_label for li in items] == ["Property, plant and equipment"]


def test_the_prefix_a_cas_caption_carries_does_not_hide_it():
    """A CAS statement writes 其中：/加：/减： in front of a line it is qualifying, and the list
    holds the bare form — so "其中：应收利息" has to be recognised as 应收利息."""
    from app.services.row_reconstruct import _is_known_caption

    for text in ("应收利息", "其中：应收利息", "减：所得税费用", "加：其他收益"):
        words = [_w(text, 0.169, 0.20, 0.30, 0.21)]
        assert _is_known_caption(words, (), frozenset()), text


def test_a_caption_fragment_is_not_recognised():
    """The failure direction that matters: a fragment must NOT be treated as complete, or a real
    wrap stops merging and a caption comes out truncated."""
    from app.services.row_reconstruct import _is_known_caption

    for text in ("负债和所有者权益（或", "股东权益）总计", "项目", "小计", "准备", "列）"):
        words = [_w(text, 0.169, 0.20, 0.30, 0.21)]
        assert not _is_known_caption(words, (), frozenset()), text
