"""A mainland face prints the WHOLE statutory caption list, and the empty rows are not banners.

A CSRC/CAS balance sheet prints every caption the form defines whether or not the entity has the
balance: a fabless chip designer prints 结算备付金, 应收保费, 买入返售金融资产 and a dozen more with
both columns blank. So "a printed line with no figures beside it" — which on an IFRS face is
strong evidence of a section banner — is the NORMAL case here, and reading those rows as banners
displaced the section for everything printed after them.

Measured on 澜起科技 688008 FY2024, three did it, and each one cascaded: the section gate restricts
the matcher to the declared section's concepts, so a mis-sectioned block does not merely land in
the wrong bucket — its captions reach NO concept at all. 投资性房地产 and 固定资产 mapped exactly on
the consolidated sheet and were unclassified on the parent company's, four pages later, because a
blank 其他权益工具投资 above them had declared the equity section.

The second half of this module is about the other end of the same statement: 流动资产合计 /
资产总计 / 负债合计 carry no enumeration, were tagged as ordinary lines, and were swept into their
section's residual bucket — so bs_nca__other_non_current_assets carried the 12.2bn total assets.
"""
from __future__ import annotations

from app.core.models.enums import LineRole
from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items


def _w(text: str, x: float, y: float, width: float = 0.09) -> Word:
    return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + 0.012))


def _rows(*lines, statement: str | None = None, pitch: float = 0.030):
    """``lines`` are ``(label, current, prior)``; a None figure prints an empty row.

    ``pitch`` is the baseline-to-baseline distance. The default is loose enough that no two lines
    read as one wrapped caption, which is what the section-banner tests want; the wrap tests set it
    to a real statement's leading so the reconstructor's tightness test can fire.
    """
    words: list[Word] = []
    for i, (label, cur, pri) in enumerate(lines):
        y = 0.10 + i * pitch
        words.append(_w(label, 0.10, y))
        if cur is not None:
            words.append(_w(cur, 0.60, y))
        if pri is not None:
            words.append(_w(pri, 0.80, y))
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                                statement=statement)
    return items


def _sections(items) -> dict[str, str | None]:
    return {i.source_label: i.section_hint for i in items}


# ── a blank row is not a banner ────────────────────────────────────────────────────────────────

def test_a_blank_row_naming_a_member_of_a_section_is_not_that_sections_banner():
    """一年内到期的非流动资产 — "non-current assets falling due within one year" — is a CURRENT
    asset, and it CONTAINS 非流动资产. Blank on this filing, it was read as the non-current banner,
    and the two current assets printed after it (其他流动资产, then the section's own total) were
    scoped to non-current assets."""
    items = _rows(
        ("流动资产：", None, None),
        ("货币资金", "6,843,296,852.61", "5,743,574,648.73"),
        ("一年内到期的非流动资产", None, None),
        ("其他流动资产", "85,986,107.30", "65,624,870.99"),
        statement="balance_sheet")

    assert _sections(items) == {"货币资金": "流动资产：", "其他流动资产": "流动资产："}


def test_a_blank_equity_investment_caption_does_not_declare_the_equity_section():
    """其他权益工具投资 ("other equity instrument investments") is a non-current ASSET containing
    权益. It carried a balance on the consolidated sheet and none on the parent company's, which is
    why the same filing mapped 投资性房地产 on one and lost it on the other."""
    items = _rows(
        ("非流动资产：", None, None),
        ("长期股权投资", "3,075,299,188.13", "3,028,017,448.18"),
        ("其他权益工具投资", None, None),
        ("投资性房地产", "313,156,660.60", "319,818,657.07"),
        statement="balance_sheet")

    assert _sections(items) == {"长期股权投资": "非流动资产：", "投资性房地产": "非流动资产："}


def test_other_comprehensive_income_is_an_equity_caption_on_a_balance_sheet():
    """THE ONE EXHAUSTION CANNOT DECIDE. 其他综合收益 is the whole of the OCI banner an income
    statement prints AND the whole of a caption the balance sheet prints in its equity block. The
    statement decides: a balance sheet has five sections and other-comprehensive-income is not one
    of them, so on a balance sheet the row is a caption. It was scoping 盈余公积 and 未分配利润 to a
    section of the balance sheet no concept lives in."""
    items = _rows(
        ("所有者权益（或股东权益）：", None, None),
        ("资本公积", "5,625,969,898.50", "5,432,387,416.86"),
        ("其他综合收益", None, None),
        ("盈余公积", "286,559,941.59", "253,807,247.64"),
        ("未分配利润", "615,696,415.83", "659,115,508.11"),
        statement="balance_sheet")

    assert set(_sections(items).values()) == {"所有者权益（或股东权益）："}


def test_the_same_caption_is_still_the_oci_banner_on_an_income_statement():
    """The refusal above is scoped to the balance sheet and nothing else — on the statement that
    does print an OCI section, a blank 其他综合收益 is still its banner."""
    items = _rows(
        ("其他综合收益", None, None),
        ("外币财务报表折算差额", "63,478,161.04", "64,919,177.82"),
        statement="profit_and_loss")

    assert _sections(items) == {"外币财务报表折算差额": "其他综合收益"}


# ── the totals ─────────────────────────────────────────────────────────────────────────────────

def test_a_mainland_face_total_is_a_total():
    """None of these is enumerated, so the 一、二、 test that recognises the income statement's
    spine says nothing about them. 合计 / 总计 / 小计 end the caption and a detail line never
    carries one, which is why a caption test is safe HERE and was refused for English."""
    items = _rows(
        ("货币资金", "6,843,296,852.61", "5,743,574,648.73"),
        ("流动资产合计", "9,461,304,025.38", "8,296,126,766.99"),
        ("非流动资产合计", "2,757,607,361.00", "2,401,414,214.28"),
        ("资产总计", "12,218,911,386.38", "10,697,540,981.27"),
        ("经营活动现金流入小计", "3,905,113,516.28", "2,527,048,205.36"),
        statement="balance_sheet")

    assert {i.source_label: i.role for i in items} == {
        "货币资金": LineRole.LINE,
        "流动资产合计": LineRole.TOTAL,
        "非流动资产合计": LineRole.TOTAL,
        "资产总计": LineRole.TOTAL,
        "经营活动现金流入小计": LineRole.TOTAL,
    }


def test_a_note_table_total_is_left_to_the_note_reader():
    """``on_face`` gates it. A note's own 合计 row is read by the note→face tie, and relabelling it
    here would take it out of the details it is the total of."""
    words: list[Word] = []
    for i, (label, cur) in enumerate((("押金、保证金", "4,114,812.47"), ("合计", "4,143,856.36"))):
        y = 0.10 + i * 0.030
        words += [_w(label, 0.10, y), _w(cur, 0.60, y)]
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                                on_face=False)

    assert {i.source_label: i.role for i in items} == {"押金、保证金": LineRole.LINE,
                                                       "合计": LineRole.LINE}


# ── the statement's own chrome ─────────────────────────────────────────────────────────────────

def test_a_mainland_statements_title_and_column_header_are_not_line_items():
    """Both land on a figure's baseline, so both were published as line items whose amount was a
    year — 合并资产负债表 carrying 2024 and 项目 附注 carrying 2024 and 2023, on each of the four
    balance-sheet pages, swept into bs_ca__other_current_assets."""
    items = _rows(
        ("合并资产负债表", "2024", None),
        ("项目附注", "2024", "2023"),
        ("货币资金", "6,843,296,852.61", "5,743,574,648.73"),
        statement="balance_sheet")

    assert [i.source_label for i in items] == ["货币资金"]


def test_a_caption_that_merely_names_the_statement_keeps_its_figures():
    """The refusal is gated on every value being a date fragment, so a real note caption that
    opens with the statement's name is untouched."""
    items = _rows(("资产负债表日后事项", "1,234.50", "900.00"), statement="balance_sheet")

    assert [(i.source_label, str(next(iter(i.values.values())).value)) for i in items] \
        == [("资产负债表日后事项", "1234.50")]


# ── a caption that wraps the other way ─────────────────────────────────────────────────────────

def test_a_caption_whose_remainder_is_printed_under_its_figures_is_folded_back():
    """THE OTHER WRAP SHAPE. The wrap merge folds a label-only line FORWARD into the valued row
    beneath it, which is the shape of a caption whose figures sit beside its LAST line. A mainland
    equity block is the reverse — the figures sit beside the FIRST line and the caption's remainder
    is printed under them — so folding forward glued the tail of one caption onto the head of the
    next and handed the mapper "（或股东权益）合计少数股东权益" holding the minority interest, while
    the parent's equity total kept a caption truncated to 归属于母公司所有者权益 and matched nothing.

    Both shapes have identical geometry, so the tail is recognised on its own WORDS: 或 is "or", and
    a parenthetical alternative has nothing on its line to be an alternative to.
    """
    items = _rows(
        ("归属于母公司所有者权益", "11,403,438,067.08", "10,191,406,155.95"),
        ("（或股东权益）合计", None, None),
        ("少数股东权益", "-6,932,502.17", "15,213,296.92"),
        statement="balance_sheet", pitch=0.014)

    assert [i.source_label for i in items] == ["归属于母公司所有者权益（或股东权益）合计", "少数股东权益"]


def test_a_caption_broken_inside_its_parenthesis_is_folded_back():
    """The structural half of the same shape, and independent of the vocabulary: a line carrying a
    closing bracket that nothing on it opened was written on an earlier line."""
    items = _rows(
        ("所有者权益（或股东权", "11,396,505,564.91", "10,206,619,452.87"),
        ("益）合计", None, None),
        statement="balance_sheet", pitch=0.014)

    assert [i.source_label for i in items] == ["所有者权益（或股东权益）合计"]


def test_an_enumerator_is_not_a_wrapped_tail():
    """"1)" closes a bracket it never opened and is the OPENING of a caption, not a continuation."""
    items = _rows(
        ("Trade receivables", "3,410", "2,900"),
        ("1) Amounts due within one year", "3,000", "2,500"),
        statement="balance_sheet", pitch=0.014)

    assert [i.source_label for i in items] == ["Trade receivables",
                                               "1) Amounts due within one year"]


def test_a_tail_is_only_folded_into_a_row_that_carries_figures():
    """A tail whose head has no figures has nothing to be the tail OF here — both lines are
    label-only and the forward merge already owns that case."""
    items = _rows(
        ("所有者权益（或股东权", None, None),
        ("益）合计", None, None),
        ("实收资本（或股本）", "1,144,789,273.00", "1,138,740,286.00"),
        statement="balance_sheet", pitch=0.014)

    assert [i.source_label for i in items] == ["实收资本（或股本）"]
