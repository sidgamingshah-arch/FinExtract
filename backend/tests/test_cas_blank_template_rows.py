"""A blank line of the CAS statement layout is a line item of its own, not the head of the next.

The combined general/financial-enterprise layout prints every line whether the filer uses it or
not. 四创电子 600990 and 海博思创 688411 print the full layout, so their income statement carries
利息支出 … 分保费用 with no figures, and the reader welded the last three onto the next caption that
has figures:

    提取保险责任准备金净额保单红利支出分保费用税金及附加   10,584,581.75   18,311,544.18

That row bound nothing it named and was swept into other operating expenses, so net operating
profit was off by the 税金及附加 figure (10,584,581.75 on 600990, 21,632,118.19 on 688411). The
balance sheets did the same to 其他应收款, 应付职工薪酬, 长期借款 and — through the 2024
其中：数据资源 lines — to 商誉, 开发支出 and 合同资产. See `_CAS_FACE_CAPTIONS` and
`_is_enumerated_other` in ``row_reconstruct``.

Geometry is 600990's: a 0.0168 row pitch, captions at x 0.204, figures centred on their rows.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

PITCH = 0.0168


def _w(text: str, x0: float, y0: float, x1: float | None = None) -> Word:
    x1 = x1 if x1 is not None else x0 + 0.012 * len(text)
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y0 + 0.0105))


def _face(rows: list[tuple], *, statement: str, x: float = 0.204):
    """rows: (caption, note or None, current or None, prior or None), one per printed line."""
    words: list[Word] = []
    y = 0.30
    for caption, note, cur, prior in rows:
        words.append(_w(caption, x, y))
        if note:
            words.append(_w(note, 0.446, y, 0.47))
        if cur:
            words.append(_w(cur, 0.631, y - 0.0005, 0.72))
        if prior:
            words.append(_w(prior, 0.800, y - 0.0005, 0.89))
        y += PITCH
    items, _ = build_line_items(words, page_index=73, document_id="d1", source_kind="native",
                                statement=statement, on_face=True, page_scope="consolidated")
    return items


def _by_label(items) -> dict[str, dict[tuple[str, str | None], str]]:
    return {li.source_label: {(v.basis.value, v.period_label): str(v.value)
                              for v in li.values.values()} for li in items}


def test_the_blank_insurance_lines_do_not_take_the_taxes_and_surcharges_row():
    items = _face([
        ("其中：营业成本", "七、61", "1,392,823,053.96", "1,591,607,928.76"),
        ("利息支出", None, None, None),
        ("手续费及佣金支出", None, None, None),
        ("退保金", None, None, None),
        ("赔付支出净额", None, None, None),
        ("提取保险责任准备金净额", None, None, None),
        ("保单红利支出", None, None, None),
        ("分保费用", None, None, None),
        ("税金及附加", "七、62", "10,584,581.75", "18,311,544.18"),
        ("销售费用", "七、63", "78,034,426.57", "91,472,939.93"),
    ], statement="profit_and_loss")
    rows = _by_label(items)

    assert not any("分保费用" in label and label != "分保费用" for label in rows), sorted(rows)
    assert rows["税金及附加"] == {("consolidated", "current"): "10584581.75",
                                ("consolidated", "prior"): "18311544.18"}, sorted(rows)


def test_the_blank_premium_lines_stay_apart():
    items = _face([
        ("其中：营业收入", "七、61", "1,603,146,551.95", "1,926,601,476.83"),
        ("利息收入", None, None, None),
        ("已赚保费", None, None, None),
        ("手续费及佣金收入", None, None, None),
        ("二、营业总成本", None, "1,774,845,093.60", "2,001,097,545.17"),
    ], statement="profit_and_loss")
    labels = list(_by_label(items))

    assert not any("已赚保费" in label for label in labels), labels
    assert "二、营业总成本" in labels, labels


def test_blank_financial_balance_sheet_lines_do_not_take_the_line_below():
    items = _face([
        ("应收分保账款", None, None, None),
        ("应收分保合同准备金", None, None, None),
        ("其他应收款", "七、9", "36,770,065.47", "41,000,000.00"),
        ("卖出回购金融资产款", None, None, None),
        ("吸收存款及同业存放", None, None, None),
        ("代理买卖证券款", None, None, None),
        ("代理承销证券款", None, None, None),
        ("应付职工薪酬", "七、39", "4,695,082.12", "5,100,000.00"),
        ("应付手续费及佣金", None, None, None),
        ("应付分保账款", None, None, None),
        ("持有待售负债", None, None, None),
        ("一年内到期的非流动负债", "七、43", "257,554,218.29", "200,000,000.00"),
        ("保险合同准备金", None, None, None),
        ("长期借款", "七、45", "637,223,387.35", "600,000,000.00"),
    ], statement="balance_sheet", x=0.169)
    rows = _by_label(items)

    for caption in ("其他应收款", "应付职工薪酬", "一年内到期的非流动负债", "长期借款"):
        assert caption in rows, (caption, sorted(rows))
    assert rows["长期借款"][("consolidated", "current")] == "637223387.35"
    assert rows["其他应收款"][("consolidated", "current")] == "36770065.47"


def test_the_2024_data_resource_line_is_a_line_of_its_own():
    items = _face([
        ("无形资产", "七、26", "395,754,020.43", "400,000,000.00"),
        ("其中：数据资源", None, None, None),
        ("开发支出", None, "269,675,582.59", "274,799,316.76"),
        ("其中：数据资源", None, None, None),
        ("商誉", None, "12,000,000.00", "12,000,000.00"),
    ], statement="balance_sheet", x=0.169)
    rows = _by_label(items)

    assert "开发支出" in rows and "商誉" in rows, sorted(rows)
    assert not any("数据资源" in label for label in rows), sorted(rows)


def test_a_blank_enumerated_other_does_not_take_the_minority_oci_line():
    items = _face([
        ("6.外币财务报表折算差额", None, "-1,000,000.00", "2,000,000.00"),
        ("7.其他", None, None, None),
        ("归属于少数股东的其他综合收益的税后净额", None, "-35,000.00", "12,000.00"),
    ], statement="profit_and_loss")
    labels = list(_by_label(items))

    assert "归属于少数股东的其他综合收益的税后净额" in labels, labels
    assert not any(label.startswith("7.其他") for label in labels), labels


def test_a_caption_that_really_wraps_still_joins():
    """The other half: a fragment is not a caption, and still folds onto its figures."""
    items = _face([
        ("其中：营业成本", "七、61", "1,392,823,053.96", "1,591,607,928.76"),
        ("提取保险责任准备金净", None, None, None),
        ("额", None, "5,000.00", "6,000.00"),
        ("税金及附加", "七、62", "10,584,581.75", "18,311,544.18"),
    ], statement="profit_and_loss")
    labels = list(_by_label(items))

    assert "提取保险责任准备金净额" in labels, labels
    assert "税金及附加" in labels, labels


def test_enumerated_other_is_complete_only_when_nothing_follows_it():
    from app.services.row_reconstruct import _is_enumerated_other

    for text in ("7.其他", "4．其他", "（三）其他", "三、其他"):
        assert _is_enumerated_other([_w(text, 0.2, 0.3)]), text
    for text in ("其他", "7.其他综合收益", "（二）其他综合收益的税后净额", "其他收益"):
        assert not _is_enumerated_other([_w(text, 0.2, 0.3)]), text
