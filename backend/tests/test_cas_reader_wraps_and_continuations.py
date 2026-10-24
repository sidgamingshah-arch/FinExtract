"""Two mainland reader faults: a continued note table read under the wrong period, and a face
caption whose second line is set LEFT of its first.

1. 河钢股份 000709 prints 七、3（1）以摊余成本计量的应收账款 as a 期末余额 table and, under "（续）", a
   期初余额 table. Read as one section the second table took the first one's header, and the
   opening gross 2,595,130,766.28 went out as a second `current` 应收账款. See
   `notes_extract._CONTINUED_MARK`. Its inventory note's continuation (七、7, over 账面余额 |
   跌价准备 | 账面价值) needed the provision read as a measure as well — see
   `row_reconstruct._MEASURE_CAPTIONS`.

2. A CAS face indents a caption by its level and sets its second line back at the column's edge.
   Judged by left alignment and by reach, two blank two-line captions above a subtotal came out as
   "收到的现金净额收到其他与投资活动有关的现金投资活动现金流入小计" (600990, 000685), and the tail
   of a valued caption took the next one ("金经营活动现金流出小计"). See `row_reconstruct._HANG_MIN`.

Geometry is the filings' own, from the native text layer.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word, build_line_items

H = 0.0105


def _w(text: str, x0: float, y: float, x1: float | None = None) -> Word:
    x1 = x1 if x1 is not None else x0 + 0.0125 * len(text)
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


# ── 1. a continued note table ─────────────────────────────────────────────────────────────────

def _receivables_page() -> list[Word]:
    """000709 page index 129, 七、3（1）: the closing table, （续）, the opening table."""
    words = [_w("3、", 0.131, 0.5768, 0.157), _w("应收账款", 0.166, 0.5768, 0.237),
             _w("（1）以摊余成本计量的应收账款", 0.130, 0.6067, 0.387)]

    def table(y: float, period: str, gross: str, allowance: str, net: str) -> list[Word]:
        return [_w(period, 0.557, y, 0.617), _w("项目", 0.158, y + 0.006, 0.188),
                _w("账面余额", 0.346, y + 0.024, 0.406), _w("坏账准备", 0.562, y + 0.024, 0.623),
                _w("账面价值", 0.773, y + 0.024, 0.834),
                _w("应收账款", 0.095, y + 0.048, 0.156), _w(gross, 0.377, y + 0.048, 0.483),
                _w(allowance, 0.578, y + 0.048, 0.684), _w(net, 0.799, y + 0.048, 0.905),
                _w("合计", 0.158, y + 0.072, 0.188), _w(gross, 0.377, y + 0.072, 0.483),
                _w(allowance, 0.578, y + 0.072, 0.684), _w(net, 0.799, y + 0.072, 0.905)]

    words += table(0.6435, "期末余额", "2,689,546,185.47", "1,394,836,830.02", "1,294,709,355.45")
    words.append(_w("（续）", 0.130, 0.7454, 0.184))
    words += table(0.7728, "期初余额", "2,595,130,766.28", "1,445,376,980.12", "1,149,753,786.16")
    return words


def _note_rows(words, carry=None):
    tables = extract_note_tables(words, page_index=129, document_id="d", source_kind="native",
                                 chapter=["七", 7, "合并财务报表项目注释", "七、2"],
                                 carry_note=carry)
    return [(t.note_number, li.raw_label, {v.period_label: str(v.value) for v in li.values.values()})
            for t in tables for li in t.items]


def test_a_continued_table_headed_opening_balance_is_the_prior_period():
    rows = _note_rows(_receivables_page())
    receivables = [r for r in rows if r[1] == "应收账款"]
    assert receivables == [
        ("七、3", "应收账款", {"current": "2689546185.47", "current:allowance": "1394836830.02",
                            "current:net": "1294709355.45"}),
        ("七、3", "应收账款", {"prior": "2595130766.28", "prior:allowance": "1445376980.12",
                            "prior:net": "1149753786.16"}),
    ]


def test_the_continuation_mark_is_no_row_of_either_table():
    assert all("续" not in (label or "") for _n, label, _v in _note_rows(_receivables_page()))


def _inventory_continuation() -> list[Word]:
    """000709 page index 137: the page opens on （续） and the 期初余额 table of 存货分类."""
    words = [_w("（续）", 0.130, 0.0986, 0.184), _w("期初余额", 0.543, 0.1234, 0.604),
             _w("项目", 0.173, 0.1355, 0.203), _w("账面余额", 0.353, 0.1475, 0.414),
             _w("跌价准备", 0.534, 0.1475, 0.594), _w("账面价值", 0.724, 0.1475, 0.784)]
    y = 0.1765
    for caption, gross, provision, net in (
            ("原材料", "10,320,959,869.83", "130,909,852.89", "10,190,050,016.94"),
            ("自制半成品", "4,780,180,293.93", None, "4,780,180,293.93"),
            ("库存商品", "3,705,957,798.92", "13,678,302.26", "3,692,279,496.66")):
        words += [_w(caption, 0.091, y), _w(gross, 0.351, y, 0.464), _w(net, 0.731, y, 0.844)]
        if provision:
            words.append(_w(provision, 0.551, y, 0.645))
        y += 0.0242
    return words


def test_an_inventory_continuation_is_the_prior_period_and_its_provision_a_measure():
    rows = {label: vals for _n, label, vals in
            _note_rows(_inventory_continuation(), carry=("七、7", "存货分类"))}
    assert rows["原材料"] == {"prior": "10320959869.83", "prior:impairment": "130909852.89",
                           "prior:net": "10190050016.94"}
    assert rows["自制半成品"] == {"prior": "4780180293.93", "prior:net": "4780180293.93"}


def test_an_empty_provision_column_takes_no_other_measures_column():
    """开山 300257 七、22 在建工程情况 (page index 188): 减值准备 is printed over a column that holds no
    figure, between the 账面余额 and 账面价值 bands, and must not take either of them."""
    words = [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d)) for t, a, b, c, d in [
        ("（1）在建工程情况", 0.0952, 0.7761, 0.2544, 0.7885),
        ("期末余额", 0.3451, 0.8294, 0.4056, 0.8401), ("期初余额", 0.6921, 0.8294, 0.7526, 0.8401),
        ("项目", 0.1288, 0.8391, 0.1591, 0.8498),
        ("账面余额", 0.2294, 0.8488, 0.2899, 0.8595), ("减值准备", 0.3451, 0.8488, 0.4056, 0.8595),
        ("账面价值", 0.4606, 0.8488, 0.5211, 0.8595), ("账面余额", 0.5764, 0.8488, 0.6368, 0.8595),
        ("减值准备", 0.6921, 0.8488, 0.7526, 0.8595), ("账面价值", 0.8078, 0.8488, 0.8683, 0.8595),
        ("印尼SGI地热", 0.0952, 0.8656, 0.1859, 0.8763),
        ("40,649,886.50", 0.2177, 0.8656, 0.3085, 0.8763),
        ("40,649,886.50", 0.4490, 0.8656, 0.5398, 0.8763),
        ("50,703,181.00", 0.5647, 0.8656, 0.6555, 0.8763),
        ("50,703,181.00", 0.7961, 0.8656, 0.8869, 0.8763),
        ("其他零星工程", 0.0952, 0.8799, 0.1859, 0.8906),
        ("247,383,862.94", 0.2177, 0.8799, 0.3085, 0.8906),
        ("247,383,862.94", 0.4490, 0.8799, 0.5398, 0.8906),
        ("189,539,069.64", 0.5647, 0.8799, 0.6555, 0.8906),
        ("189,539,069.64", 0.7961, 0.8799, 0.8869, 0.8906)]]
    rows = {label: vals for _n, label, vals in
            _note_rows(words, carry=("七、22", "在建工程情况"))}
    assert rows["其他零星工程"] == {"current": "247383862.94", "current:net": "247383862.94",
                                "prior": "189539069.64", "prior:net": "189539069.64"}


# ── 2. a face that wraps to the left ──────────────────────────────────────────────────────────

def _boxes(rows: list[tuple]) -> list[Word]:
    """(text, x0, y0, x1, y1) as the text layer gives them."""
    return [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d)) for t, a, b, c, d in rows]


def _read(rows: list[tuple], *, statement: str, page: int, scope: str = "consolidated"):
    items, _ = build_line_items(_boxes(rows), page_index=page, document_id="d",
                                source_kind="native", statement=statement, on_face=True,
                                page_scope=scope)
    return items


def _valued(items) -> dict[str, dict[str, str]]:
    return {li.source_label: {v.period_label: str(v.value) for v in li.values.values()}
            for li in items if li.values}


# 四创电子 600990, consolidated cash flow statement, page index 77.
_SUN_CREATE_CF = [
    ("支付的各项税费", 0.1687, 0.6559, 0.2925, 0.6685),
    ("65,467,650.05", 0.6185, 0.6548, 0.7113, 0.6692),
    ("86,833,839.46", 0.8006, 0.6548, 0.8933, 0.6692),
    ("支付其他与经营活动有关现", 0.1687, 0.6728, 0.3806, 0.6853),
    ("金", 0.151, 0.689, 0.1688, 0.7016),
    ("89,365,652.61", 0.6185, 0.6797, 0.7113, 0.694),
    ("107,653,984.40", 0.7925, 0.6797, 0.8933, 0.694),
    ("经营活动现金流出小计", 0.1863, 0.7057, 0.3629, 0.7182),
    ("1,846,739,034.02", 0.5984, 0.7046, 0.7112, 0.719),
    ("2,217,405,946.09", 0.7805, 0.7046, 0.8933, 0.719),
    ("经营活动产生的现金流量净额", 0.151, 0.7225, 0.3806, 0.7351),
    ("244,695,836.40", 0.6105, 0.7213, 0.7113, 0.7356),
    ("108,234,967.17", 0.7925, 0.7213, 0.8933, 0.7356),
    ("二、投资活动产生的现金流量：", 0.151, 0.7393, 0.399, 0.7519),
    ("收回投资收到的现金", 0.1687, 0.756, 0.3278, 0.7686),
    ("取得投资收益收到的现金", 0.1687, 0.7728, 0.3631, 0.7854),
    ("处置固定资产、无形资产和其", 0.1687, 0.7895, 0.3971, 0.8021),
    ("他长期资产收回的现金净额", 0.151, 0.8058, 0.3629, 0.8183),
    ("9,174,713.00", 0.6266, 0.7965, 0.7113, 0.8109),
    ("31,812.39", 0.8288, 0.7965, 0.8933, 0.8109),
    ("处置子公司及其他营业单位", 0.1687, 0.8226, 0.3806, 0.8351),
    ("收到的现金净额", 0.151, 0.8387, 0.2748, 0.8512),
    ("收到其他与投资活动有关的", 0.1687, 0.8555, 0.3806, 0.8681),
    ("现金", 0.151, 0.8716, 0.1865, 0.8842),
    ("投资活动现金流入小计", 0.1863, 0.8885, 0.3629, 0.901),
    ("9,174,713.00", 0.6266, 0.8872, 0.7113, 0.9016),
    ("31,812.39", 0.8288, 0.8872, 0.8933, 0.9016),
]


def test_two_blank_hung_captions_do_not_take_the_subtotal():
    items = _read(_SUN_CREATE_CF, statement="cash_flow", page=77)
    rows = _valued(items)
    assert rows["投资活动现金流入小计"] == {"current": "9174713.00", "prior": "31812.39"}
    labels = [li.source_label for li in items]
    assert not any(label != "投资活动现金流入小计" and label.endswith("投资活动现金流入小计")
                   for label in labels)


def test_a_hung_tail_stays_with_its_valued_caption():
    rows = _valued(_read(_SUN_CREATE_CF, statement="cash_flow", page=77))
    assert rows["支付其他与经营活动有关现金"] == {"current": "89365652.61", "prior": "107653984.40"}
    assert rows["经营活动现金流出小计"] == {"current": "1846739034.02", "prior": "2217405946.09"}


def test_the_derecognition_line_and_the_hedging_line_are_two_rows():
    """海博思创 688411, parent income statement (page index 139): both lines print 0 / 0, and the
    glued row was swept into other operating expenses."""
    rows = _valued(_read([
        ("其中：对联营企业和合营企", 0.2038, 0.2769, 0.4153, 0.2893),
        ("业的投资收益", 0.1510, 0.2930, 0.2566, 0.3054),
        ("25,926,041.09", 0.5987, 0.2849, 0.7131, 0.2973),
        ("-7,815,430.07", 0.7781, 0.2849, 0.8925, 0.2973),
        ("以摊余成本计量的金", 0.2479, 0.3098, 0.4066, 0.3222),
        ("融资产终止确认收益", 0.1510, 0.3259, 0.3096, 0.3383),
        ("0", 0.7045, 0.3178, 0.7133, 0.3302),
        ("0", 0.8839, 0.3178, 0.8927, 0.3302),
        ("净敞口套期收益（损失以", 0.2038, 0.3427, 0.3977, 0.3551),
        ("“-”号填列）", 0.1510, 0.3588, 0.2653, 0.3713),
        ("0", 0.7045, 0.3507, 0.7133, 0.3631),
        ("0", 0.8839, 0.3507, 0.8927, 0.3631),
        ("公允价值变动收益（损失以", 0.2038, 0.3757, 0.4153, 0.3881),
        ("“－”号填列）", 0.1510, 0.3918, 0.2742, 0.4042),
        ("-9,559,681.87", 0.5987, 0.3836, 0.7131, 0.3961),
        ("0", 0.8839, 0.3836, 0.8927, 0.3961),
    ], statement="income_statement", page=139, scope="standalone"))
    assert "以摊余成本计量的金融资产终止确认收益" in rows
    assert "净敞口套期收益（损失以“-”号填列）" in rows
    assert not any("确认收益净敞口" in label for label in rows)


def test_the_page_number_is_not_a_blank_captions_figure():
    """中山公用 000685, parent income statement (page index 142): the page ends on a blank three-line
    caption, its folio "143" was read as the line's prior-year figure and its head was lost."""
    items = _read([
        ("143", 0.8820, 0.9288, 0.9049, 0.9395),
        ("其中：对联营企业和合", 0.1748, 0.8253, 0.3515, 0.8379),
        ("营企业的投资收益", 0.1042, 0.8414, 0.2456, 0.8540),
        ("836,797,951.42", 0.5025, 0.8333, 0.6263, 0.8458),
        ("607,551,130.02", 0.7725, 0.8333, 0.8963, 0.8458),
        ("以摊余成本计量", 0.2279, 0.8627, 0.3517, 0.8752),
        ("的金融资产终止确认收益（损失", 0.1042, 0.8788, 0.3516, 0.8913),
        ("以“-”号填列）", 0.1042, 0.8950, 0.2367, 0.9076),
    ], statement="income_statement", page=142, scope="standalone")
    assert not any("143" in vals.values() for vals in _valued(items).values())
    assert not any(li.source_label.startswith("的金融资产") for li in items)


def test_a_caption_straddling_its_figures_under_a_valued_line_keeps_them():
    """开山 300257 (page index 123): 7.其他 prints a figure, and the next caption's two lines
    straddle its own figures."""
    rows = _valued(_read([
        ("7.其他", 0.1405, 0.5986, 0.1859, 0.6093),
        ("66,587,011.81", 0.5185, 0.5986, 0.6168, 0.6093),
        ("归属于少数股东的其他综合收益的", 0.1103, 0.6131, 0.3371, 0.6238),
        ("税后净额", 0.0952, 0.6274, 0.1556, 0.6380),
        ("6,584,012.03", 0.5262, 0.6202, 0.6170, 0.6309),
        ("2,474,571.74", 0.7961, 0.6202, 0.8869, 0.6309),
        ("七、综合收益总额", 0.0952, 0.6419, 0.2161, 0.6526),
        ("432,470,319.14", 0.5110, 0.6419, 0.6170, 0.6526),
        ("519,328,737.80", 0.7810, 0.6419, 0.8869, 0.6526),
    ], statement="income_statement", page=123))
    assert rows["归属于少数股东的其他综合收益的税后净额"] == {"current": "6584012.03",
                                                  "prior": "2474571.74"}


def test_a_bilingual_caption_wrapping_to_the_right_still_joins():
    """China SCE 1966 wraps to the RIGHT, both scripts on each line; the left-wrap rule is not its
    layout."""
    items = _read([
        ("Changes in fair value of investment", 0.119, 0.400, 0.330, 0.411),
        ("投資物業公允值變動", 0.340, 0.400, 0.450, 0.411),
        ("properties, net", 0.134, 0.4135, 0.240, 0.4245),
        ("淨額", 0.250, 0.4135, 0.280, 0.4245),
        ("(3,754,084)", 0.610, 0.4135, 0.711, 0.4245),
        ("(25,422)", 0.793, 0.4135, 0.893, 0.4245),
    ], statement="cash_flow", page=101)
    labels = [li.source_label for li in items if li.values]
    assert len(labels) == 1 and labels[0].startswith("Changes in fair value")


def test_a_wrapped_breakdown_line_stays_in_its_breakdown():
    """四创电子 600990 (page index 74): read whole, 以摊余成本计量的金融 / 资产终止确认收益 starts at its
    tail's margin, and judged by that edge it left 投资收益's 其中 breakdown — its -1,300,261.11,
    already inside 投资收益, was swept into other operating expenses a second time."""
    items = _read([
        ("投资收益（损失以“-”号填列）", 0.2040, 0.0750, 0.4302, 0.0894),
        ("6,592,631.61", 0.6391, 0.0747, 0.7237, 0.0891),
        ("739,747.47", 0.8206, 0.0747, 0.8931, 0.0891),
        ("其中：", 0.2040, 0.0930, 0.2572, 0.1056),
        ("对联营企业和合营企业", 0.2512, 0.0930, 0.4276, 0.1056),
        ("的投资收益", 0.1510, 0.1091, 0.2395, 0.1217),
        ("以摊余成本计量的金融", 0.2480, 0.1259, 0.4246, 0.1385),
        ("资产终止确认收益", 0.1510, 0.1420, 0.2923, 0.1546),
        ("-1,300,261.11", 0.8036, 0.1328, 0.8931, 0.1472),
        ("汇兑收益（损失以“－”号填列）", 0.2040, 0.1579, 0.4365, 0.1723),
    ], statement="income_statement", page=74)
    by_label = {li.source_label: li for li in items}
    parent = by_label["投资收益（损失以“-”号填列）"]
    line = by_label["以摊余成本计量的金融资产终止确认收益"]
    assert line.parent_id == parent.id
