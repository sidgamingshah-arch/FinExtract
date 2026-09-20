"""A mainland statement NUMBERS its lines, and that numbering is where one caption ends.

TWO SHAPES, ONE CAUSE. Both reconstruction paths fold a label-only line FORWARD into the valued
row beneath it, and neither had any way to stop when the run of label-only lines contained more
than one caption. What ends a caption on a CAS face is its successor's ENUMERATION — 一、…十、 at
the top level, （一） / 1. one level down — because a mainland statement numbers its lines and
nothing else (`_opens_its_own_line`). The vocabulary cannot answer it: a line captioned 其他 is
"other", too generic to admit to `_CAS_FACE_CAPTIONS`, whose own rule keeps fragments and header
words out.

SHAPE 1 — the income statement's OCI block, 300319 page index 103. The caption column is narrow,
so the head carries the enumerator and is indented while the tail hangs back to the margin, and
the figures are drawn on their OWN y-band between them (the amount is vertically centred in a
two-line cell):

    [ 5] x0=0.1498  1.重新计量设定受益计划变动
    [ 6] x0=0.1045  额
    [ 7] x0=0.1498  2.权益法下不能转损益的其他
    [ 8] x0=0.1045  综合收益
    [ 9] x0=0.1498  3.其他权益工具投资公允价值
    [10] x0=0.5202              -10,825,600.00   -284,303.92
    [11] x0=0.1045  变动

Five consecutive label-only rows, so all five went into `pending` and were dumped on row 10:
2.权益法下不能转损益的其他综合收益3.其他权益工具投资公允价值 reached no concept, and the -10,758,236.29
the block belongs to was swept into `is_pl__other_operating_expenses` — other comprehensive income
charged as an operating expense.

SHAPE 2 — the statement of changes in equity, 688008 page index 158. `_matrix_items` applied the
veto in its label-only branch and NOT in its valued branch, so a row that both has figures and
announces itself with 二、/（一）/1． still inherited whatever was pending above it:
其他二、本年期初余额 holding 1,138,740,286.00, 号填列） （一）综合收益总 and
额（二）所有者投入 holding 6,048,987.00 — ten welded captions in all.

THE SAME PATH ALSO THREW AWAY WHAT IT REFUSED. `pending = []` and `pending, tail = [], None`
discard the fragment rather than emit it, so 三、本期增减变动 — the head of its OWN row, whose
figures are printed on the line below it — vanished and the figures were captioned 金额（减少以.
An enumerated line refuses the fold because it STARTS a caption, which is the same reason it must
be kept as the start of one.

MEASURED, on the five-filing corpus: across its 83 face batches the number of valued rows
carrying a welded caption goes from SEVEN to NONE, and no valued row changes on either HKEX filing
or on 000709 — the two simplest PRC balance sheets included. On 300319 the welds had a published
cost: `'的税后净额（一）不能重分类进损益的其他'` and `'变动5.其他（二）将重分类进损益的其他综'` both
missed `residual._CAS_SUB_ENUMERATED` (it is anchored `^\W*`, and a weld does not start with its
own enumerator), so the sweep charged the OCI block to `is_pl__other_operating_expenses` —
-10,758,236.29 / -305,285.33, bit-identical to `is_oci__total_other_comprehensive_income`, on a
filing whose income statement prints no such line. Split, all three captions match the enumeration
gate and the sweep takes none of them.

688008's consolidated equity statement now reads line for
line as printed (一、上年年末余额 / 二、本年期初余额 / 三、本期增减变动金额（减少以“－”号填列） /
（一）综合收益总额 / （二）所有者投入和减少资本 / 1．所有者投入的普通股 /
3．股份支付计入所有者权益的金额 / 4．其他 / （三）利润分配 / 1．提取盈余公积 /
3．对所有者（或股东）的分配), 300319's weld is gone and its P&L residual dropped from two rows to
none, and the two HKEX filings and 000709's balance sheet did not move by a single figure.
"""
from __future__ import annotations

import pytest

from app.core.models.geometry import BBox
from app.services.mapping import normalize_label
from app.services.row_reconstruct import (
    Word,
    _CAS_FACE_CAPTIONS,
    _completes_the_matrix_caption,
    _opens_its_own_line,
    build_line_items,
)

LINE_H = 0.011
STEP = 0.015
GLYPH = 0.016


def _line(y: float, text: str = "", *, x0: float = 0.1045, value: str | None = None,
          value2: str | None = None) -> list[Word]:
    """One printed line. CJK has no spaces, so the whole caption is one Word."""
    out = []
    if text:
        out.append(Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x0 + GLYPH * len(text), y1=y + LINE_H)))
    if value:
        out.append(Word(text=value, bbox=BBox(x0=0.52, y0=y, x1=0.63, y1=y + LINE_H)))
    if value2:
        out.append(Word(text=value2, bbox=BBox(x0=0.81, y0=y, x1=0.90, y1=y + LINE_H)))
    return out


def _captions(*lines: list[Word], statement: str = "profit_and_loss") -> dict[str, list[str]]:
    words = [w for line in lines for w in line]
    items, _ = build_line_items(words, page_index=0, document_id="d1", source_kind="native",
                               statement=statement)
    return {li.source_label: [str(v.value) for v in li.values.values()] for li in items}


# ── what ends a caption ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label", ["一、营业收入", "七、综合收益总额", "（一）综合收益总",
                                   "（二）所有者投入", "1.重新计量设定受益计划变动",
                                   "2.权益法下不能转损益的其他", "7.其他", "1．所有者投入的",
                                   "3．股份支付计入所"])
def test_an_enumerated_line_opens_a_caption(label):
    """The evidence the rule runs on. Every one of these is a line of a printed CAS statement, and
    the enumeration is the only thing that says so — 7.其他 and 4．其他 are "other"."""
    assert _opens_its_own_line([Word(text=label, bbox=BBox(x0=0.1, y0=0.1, x1=0.4, y1=0.11))])


@pytest.mark.parametrize("fragment", ["额", "变动", "综合收益", "合收益的金额", "税后净额",
                                      "的税后净额", "号填列）", "和减少资本", "普通股",
                                      "有者权益的金额", "内部结转"])
def test_a_tail_does_not_open_a_caption(fragment):
    """The counterweight: every tail the two shapes print must NOT look like an opening line, or
    the rule would split a caption in half instead of joining it."""
    assert not _opens_its_own_line(
        [Word(text=fragment, bbox=BBox(x0=0.1, y0=0.1, x1=0.4, y1=0.11))])


# ── shape 1: the OCI block's run of label-only rows ───────────────────────────────────────────

def test_a_run_of_label_only_rows_splits_at_each_enumeration():
    """The 300319 page-103 shape, drawn to scale. Before the fix all five label-only rows were
    dumped on the valued row as one caption."""
    got = _captions(
        _line(0.100, "1.重新计量设定受益计划变动", x0=0.1498),
        _line(0.100 + STEP, "额"),
        _line(0.100 + 2 * STEP, "2.权益法下不能转损益的其他", x0=0.1498),
        _line(0.100 + 3 * STEP, "综合收益"),
        _line(0.100 + 4 * STEP, "3.其他权益工具投资公允价值", x0=0.1498),
        _line(0.100 + 5 * STEP, value="-10,825,600.00", value2="-284,303.92"),
        _line(0.100 + 6 * STEP, "变动"),
    )
    assert "3.其他权益工具投资公允价值变动" in got, list(got)
    assert got["3.其他权益工具投资公允价值变动"] == ["-10825600.00", "-284303.92"]
    # …and neither of the two captions above it is welded to anything
    assert not [c for c in got if c.count("权益法下") and c.count("其他权益工具")], list(got)


def test_a_caption_refused_the_fold_is_handed_to_the_loop_and_not_dropped_inside_pending():
    """`pending = []` discarded the fragment where `out.append(pending)` hands it back to the main
    loop as a row of its own.

    THE SHAPE THAT ACTUALLY REACHES THE FLUSH is 7.其他 followed by an unenumerated caption: the
    statutory layout prints 7.其他 with no figure, `其他` alone is "other" and deliberately absent
    from `_CAS_FACE_CAPTIONS`, so nothing else refuses it the fold. Most valueless CAS lines never
    get this far — 5.现金流量套期储备 is recognised by `_is_known_caption` once the rulebook's
    `numbering` step strips the 5., and is emitted as its own row well before `pending`.

    WHAT THE FLUSH DOES AND DOES NOT RECOVER, stated because the difference is invisible in the
    output: a VALUELESS ROW IS NEVER EMITTED, before this change or after, so 7.其他 does not
    become a line item either way. What the flush buys is that the fragment is no longer carried
    silently inside `pending` past the row that refused it. Measured over the corpus's 83 face
    batches: 23 discards became 20 flushes, no valued row on any of the 1079 pages differs, and
    none of the 20 can set a section (`section_of_banner_only` is None for every one) or open a
    group (none ends in a colon). So the assertion is the one
    `test_an_empty_template_line_is_welded_to_neither_neighbour` makes — each neighbour keeps its
    own caption and its own figures, and nothing else appears."""
    got = _captions(
        _line(0.100, "7.其他", x0=0.1498),
        _line(0.100 + STEP, "归属于少数股东的其他综合收益的", x0=0.1196),
        _line(0.100 + 2 * STEP, "税后净额"),
        _line(0.100 + 3 * STEP, "七、综合收益总额",
              value="330,011,283.77", value2="280,621,256.12"),
    )
    assert got["七、综合收益总额"] == ["330011283.77", "280621256.12"]
    assert not [c for c in got if c.startswith("7.其他归属")], list(got)


def test_a_caption_that_wraps_into_its_own_figures_still_folds_forward():
    """The counterweight, and it has cost a figure before: an enumerated caption may legitimately
    wrap, with its figures beside the TAIL. 四、汇率变动对现金及现金等价物的影 / 响 is drawn that
    way on 300319, and vetoing the forward fold on the FRAGMENT rather than on the valued row cost
    `cf_financing__net_foreign_exchange_difference` all four of its slots."""
    got = _captions(
        _line(0.100, "四、汇率变动对现金及现金等价物的影"),
        _line(0.100 + STEP, "响", value="1,234.56", value2="7,890.12"),
        statement="cash_flow",
    )
    assert "四、汇率变动对现金及现金等价物的影响" in got, list(got)
    assert got["四、汇率变动对现金及现金等价物的影响"] == ["1234.56", "7890.12"]


# ── shape 2: the equity matrix ────────────────────────────────────────────────────────────────

_EQUITY_TAILS = (
    ("（一）综合收益总", "额", "（一）综合收益总额"),
    ("（二）所有者投入", "和减少资本", "（二）所有者投入和减少资本"),
    ("1．所有者投入的", "普通股", "1．所有者投入的普通股"),
    ("3．股份支付计入所", "有者权益的金额", "3．股份支付计入所有者权益的金额"),
    ("2．提取一般风险", "准备", "2．提取一般风险准备"),
)


@pytest.mark.parametrize("head,tail,whole", _EQUITY_TAILS)
def test_the_matrix_reads_a_tail_printed_below_its_figures(head, tail, whole):
    """`_completes_the_caption_above` for the matrix. The numbering has to be stripped HERE — the
    two-column path gets that from the rulebook's declared `numbering` step and this path is handed
    no steps at all — so the join is compared against the bare caption the list holds."""
    box = BBox(x0=0.09, y0=0.1, x1=0.4, y1=0.11)
    assert _completes_the_matrix_caption([Word(text=head, bbox=box)],
                                         [Word(text=tail, bbox=box)]), whole
    assert normalize_label(whole.split("）", 1)[-1].lstrip("．0123456789")) in _CAS_FACE_CAPTIONS \
        or normalize_label(whole) in _CAS_FACE_CAPTIONS


@pytest.mark.parametrize("head,tail", [
    # A complete caption is a movement row, never a tail — the same veto the two-column path has.
    ("（一）综合收益总额", "本年期初余额"),
    ("三、本期增减变动金额", "利润分配"),
    # …and a join the vocabulary does not know is not a caption either.
    ("（一）综合收益总", "股东权益"),
])
def test_the_matrix_refuses_a_tail_it_cannot_recognise(head, tail):
    box = BBox(x0=0.09, y0=0.1, x1=0.4, y1=0.11)
    assert not _completes_the_matrix_caption([Word(text=head, bbox=box)],
                                             [Word(text=tail, bbox=box)])


def _matrix(*rows: tuple[str, ...]) -> list[Word]:
    """A CAS equity matrix: four component columns, a caption column narrow enough to wrap.

    Modelled on 688008 page index 158, where the label column runs to x≈0.41, the first value band
    starts at x≈0.42, and — the load-bearing part — the rows TOUCH: each is ≈0.016 tall and they
    are stepped ≈0.0155 apart, so `_tight_below` sees a gap of about -0.0005. Drawn with the
    two-column path's airier pitch the head is not a wrap of the row beneath it at all, and this
    fixture tested nothing.
    """
    right = [0.52, 0.64, 0.76, 0.90]
    row_h, pitch = 0.016, 0.0155
    words: list[Word] = []
    for j, name in enumerate(["实收资本", "资本公积", "未分配利润", "合计"]):
        words.append(Word(text=name, bbox=BBox(x0=right[j] - 0.08, y0=0.10,
                                               x1=right[j], y1=0.115)))
    for i, row in enumerate(rows):
        y = 0.18 + i * pitch
        label, cells = row[0], row[1:]
        if label:
            words.append(Word(text=label, bbox=BBox(x0=0.09, y0=y,
                                                    x1=0.09 + GLYPH * len(label), y1=y + row_h)))
        for v, x in zip(cells, right):
            if v:
                words.append(Word(text=v, bbox=BBox(x0=x - 0.008 * len(v), y0=y,
                                                    x1=x, y1=y + row_h)))
    return words


def test_a_matrix_row_that_opens_its_own_line_does_not_inherit_the_fragment_above_it():
    """688008's 其他二、本年期初余额. The label-only branch already refused this; the valued branch
    was not asking."""
    items, _ = build_line_items(
        _matrix(("加：会计政策变更",), ("前期差错更正",), ("其他",),
                ("二、本年期初余额", "1,138,740,286.00", "5,432,387,416.86",
                 "3,478,053,735.01", "10,206,619,452.87")),
        page_index=0, document_id="d1", source_kind="native", statement="changes_in_equity")
    labels = [li.source_label for li in items]
    assert "二、本年期初余额" in labels, labels
    assert not [l for l in labels if l.startswith("其他二、")], labels


def test_a_matrix_caption_is_whole_when_its_head_and_tail_straddle_its_figures():
    """688008's 三、本期增减变动金额（减少以“－”号填列）: the head has no figures, the middle line
    has them, and the tail is printed below. All three lines are one caption."""
    items, _ = build_line_items(
        _matrix(("三、本期增减变动",),
                ("金额（减少以", "6,048,987.00", "193,582,481.64",
                 "1,040,329,595.49", "1,189,886,112.04"),
                ("“－”号填列）",)),
        page_index=0, document_id="d1", source_kind="native", statement="changes_in_equity")
    labels = [li.source_label for li in items]
    assert labels == ["三、本期增减变动金额（减少以“－”号填列）"], labels
