"""The last coverage misses on the published filings: figures a filing prints for a line that no
request asking about the line carried.

Measured with `scripts/llm_standin.py coverage` over the 20 published filings. Each test below
pins one shape, with the word geometry copied from the page it was measured on (page indices are
0-based), so the reader is exercised on what the PDF actually draws:

* a CAS movement table's narrow caption column wraps "（1）计提" as "（1）计" / figures / "提", and the
  first line was read as a sub-table heading (000883, 688008, 002273, 000685);
* the matrix reader dropped every caption whose figure line straddles it (688008 page 199);
* a figure too wide for its cell wraps its last digits onto the next line (002273, 000685);
* a borrowings note's "Maturity" column prints a year that was read as the year's amount (Far
  East Horizon note 32);
* "30 June 2022 as a result of …" opened a note 30 (Far East Horizon);
* a share count printed on a dated row under "Issued and fully paid ordinary shares:" (note 35);
* a cash-flow supplement whose closing caption wraps (000685 七、79);
* the stand-in's own measurement: a prose amount, five named parts, a 万元 table.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.geometry import BBox
from app.services import notes_extract
from app.services.notes_extract import extract_note_tables
from app.services import row_reconstruct as rr
from app.services.row_reconstruct import Word, _tight_below



def _words(rows) -> list[Word]:
    return [Word(text=t, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1)) for row in rows for t, x0, y0, x1, y1 in row]


def _tables(rows, carry=("七、21", "固定资产")):
    """A notes page read as the run reads one: inside chapter 七 with note 七、21 open, so a
    numbered movement row ("1.期初余额") is a row of that note and not note 1."""
    chapter = ["七", 7, "合并财务报表项目注释", "七、21"] if carry else None
    return extract_note_tables(_words(rows), page_index=0, document_id=None, source_kind="native",
                               carry_note=carry, chapter=chapter)


def _values(tables) -> set[Decimal]:
    return {ev.value for t in tables for it in t.items for ev in (it.values or {}).values()
            if ev.value is not None}


# 000883 page 188: 二、累计折旧 of 七、21 固定资产. "（1）计" / figures / "提".
_HUBEI_ACCUMULATED_DEPRECIATION = [
    [('二、累计折旧', 0.0952, 0.4598, 0.1859, 0.4704)],
    [('1.期初余额', 0.1254, 0.4791, 0.2010, 0.4898), ('11,857,643,469.58', 0.2312, 0.4796, 0.3469, 0.4897), ('14,488,264,463.30', 0.3661, 0.4796, 0.4818, 0.4897), ('260,979,141.83', 0.5215, 0.4796, 0.6168, 0.4897), ('508,551,393.81', 0.6564, 0.4796, 0.7517, 0.4897), ('27,115,438,468.52', 0.7709, 0.4796, 0.8866, 0.4897)],
    [('2.本期增加', 0.1254, 0.4984, 0.2010, 0.5091)],
    [('973,845,440.72', 0.2516, 0.5060, 0.3469, 0.5161), ('1,876,068,424.77', 0.3730, 0.5060, 0.4820, 0.5161), ('37,438,031.20', 0.5282, 0.5060, 0.6166, 0.5161), ('77,169,716.67', 0.6633, 0.5060, 0.7517, 0.5161), ('2,964,521,613.36', 0.7776, 0.5060, 0.8866, 0.5161)],
    [('金额', 0.0952, 0.5126, 0.1254, 0.5233)],
    [('（1）计', 0.1556, 0.5320, 0.2087, 0.5427)],
    [('943,105,039.76', 0.2516, 0.5396, 0.3469, 0.5497), ('1,869,842,946.73', 0.3730, 0.5396, 0.4820, 0.5497), ('37,391,485.32', 0.5282, 0.5396, 0.6166, 0.5497), ('77,095,531.42', 0.6633, 0.5396, 0.7517, 0.5497), ('2,927,435,003.23', 0.7776, 0.5396, 0.8866, 0.5497)],
    [('提', 0.0952, 0.5463, 0.1103, 0.5569)],
    [('（2）外', 0.1482, 0.5631, 0.2010, 0.5738)],
    [('币财务报表折算', 0.0952, 0.5773, 0.2010, 0.5880), ('14,646,814.72', 0.2584, 0.5778, 0.3469, 0.5879), ('6,225,478.04', 0.4002, 0.5778, 0.4820, 0.5879), ('46,545.88', 0.5554, 0.5778, 0.6166, 0.5879), ('74,185.25', 0.6905, 0.5778, 0.7517, 0.5879), ('20,993,023.89', 0.7981, 0.5778, 0.8866, 0.5879)],
    [('差额', 0.0952, 0.5916, 0.1254, 0.6023)],
    [('（3）投', 0.1482, 0.6061, 0.2010, 0.6168)],
    [('16,093,586.24', 0.2584, 0.6137, 0.3469, 0.6239), ('16,093,586.24', 0.7981, 0.6137, 0.8866, 0.6239)],
    [('资性房地产转入', 0.0952, 0.6204, 0.2010, 0.6311)],
]

# 688008 page 199: 七、21 (1) 固定资产情况, read as a movement matrix (six asset-class columns).
_MONTAGE_FIXED_ASSETS = [
    [('（1）', 0.1072, 0.3673, 0.1492, 0.3813), ('固定资产情况', 0.1778, 0.3673, 0.2786, 0.3813)],
    [('√适用□不适用', 0.1072, 0.3904, 0.2332, 0.4044)],
    [('单位：元', 0.6551, 0.4060, 0.7255, 0.4207), ('币种：人民币', 0.7432, 0.4060, 0.8491, 0.4207)],
    [('境外土地所', 0.2225, 0.4224, 0.2981, 0.4350)],
    [('项目', 0.1375, 0.4294, 0.1677, 0.4420), ('房屋及建筑物', 0.3245, 0.4294, 0.4153, 0.4420), ('运输工具', 0.4457, 0.4294, 0.5062, 0.4420), ('电子设备', 0.5515, 0.4294, 0.6120, 0.4420), ('办公设备', 0.6612, 0.4294, 0.7217, 0.4420), ('合计', 0.7864, 0.4294, 0.8166, 0.4420)],
    [('有权', 0.2451, 0.4364, 0.2754, 0.4490)],
    [('一、账面原值：', 0.1072, 0.4509, 0.2131, 0.4636)],
    [('1.期初余', 0.1375, 0.4634, 0.1941, 0.4779)],
    [('11,386,446.63', 0.2169, 0.4706, 0.3037, 0.4847), ('513,186,545.38', 0.3227, 0.4706, 0.4172, 0.4847), ('2,087,026.59', 0.4362, 0.4706, 0.5155, 0.4847), ('206,004,094.83', 0.5346, 0.4706, 0.6290, 0.4847), ('19,786,017.25', 0.6481, 0.4706, 0.7348, 0.4847), ('752,450,130.68', 0.7545, 0.4706, 0.8489, 0.4847)],
    [('额', 0.1072, 0.4793, 0.1224, 0.4919)],
    [('2.本期增', 0.1375, 0.4917, 0.1941, 0.5063)],
    [('169,927.77', 0.2358, 0.4990, 0.3037, 0.5130), ('3,691,482.15', 0.3378, 0.4990, 0.4172, 0.5130), ('33,046,796.35', 0.5422, 0.4990, 0.6290, 0.5130), ('2,700,188.71', 0.6555, 0.4990, 0.7349, 0.5130), ('39,608,394.98', 0.7622, 0.4990, 0.8489, 0.5130)],
    [('加金额', 0.1072, 0.5076, 0.1526, 0.5203)],
    [('（1）购', 0.1526, 0.5201, 0.1980, 0.5348)],
    [('3,186,599.91', 0.3378, 0.5274, 0.4172, 0.5414), ('32,273,974.57', 0.5422, 0.5274, 0.6290, 0.5414), ('2,648,921.39', 0.6555, 0.5274, 0.7349, 0.5414), ('38,109,495.87', 0.7622, 0.5274, 0.8489, 0.5414)],
    [('置', 0.1072, 0.5360, 0.1224, 0.5486)],
    [('（2）外', 0.1526, 0.5485, 0.1980, 0.5632)],
    [('169,927.77', 0.2358, 0.5559, 0.3037, 0.5699), ('504,882.24', 0.3491, 0.5559, 0.4170, 0.5699), ('772,821.78', 0.5610, 0.5559, 0.6288, 0.5699), ('51,267.32', 0.6745, 0.5559, 0.7349, 0.5699), ('1,498,899.11', 0.7696, 0.5559, 0.8489, 0.5699)],
    [('币折算差异', 0.1072, 0.5644, 0.1828, 0.5770)],
    [('3.本期减', 0.1377, 0.5768, 0.1945, 0.5915)],
    [('745,003.34', 0.5610, 0.5842, 0.6288, 0.5983), ('1,012,237.21', 0.6555, 0.5842, 0.7349, 0.5983), ('1,757,240.55', 0.7696, 0.5842, 0.8489, 0.5983)],
    [('少金额', 0.1072, 0.5927, 0.1526, 0.6054)],
    [('（1）处', 0.1526, 0.6053, 0.1980, 0.6199)],
    [('745,003.34', 0.5610, 0.6126, 0.6288, 0.6266), ('1,012,237.21', 0.6555, 0.6126, 0.7349, 0.6266), ('1,757,240.55', 0.7696, 0.6126, 0.8489, 0.6266)],
    [('置或报废', 0.1072, 0.6212, 0.1677, 0.6339)],
    [('4.期末余', 0.1375, 0.6337, 0.1941, 0.6483)],
    [('11,556,374.40', 0.2169, 0.6410, 0.3037, 0.6550), ('516,878,027.53', 0.3227, 0.6410, 0.4172, 0.6550), ('2,087,026.59', 0.4362, 0.6410, 0.5155, 0.6550), ('238,305,887.84', 0.5346, 0.6410, 0.6290, 0.6550), ('21,473,968.75', 0.6481, 0.6410, 0.7348, 0.6550), ('790,301,285.11', 0.7545, 0.6410, 0.8489, 0.6550)],
    [('额', 0.1072, 0.6496, 0.1224, 0.6622)],
    [('二、累计折旧', 0.1072, 0.6640, 0.1980, 0.6766)],
    [('1.期初余', 0.1375, 0.6766, 0.1941, 0.6912)],
    [('18,704,600.76', 0.3304, 0.6839, 0.4172, 0.6979), ('1,896,781.40', 0.4362, 0.6839, 0.5155, 0.6979), ('109,955,121.61', 0.5346, 0.6839, 0.6290, 0.6979), ('10,086,234.78', 0.6481, 0.6839, 0.7348, 0.6979), ('140,642,738.55', 0.7545, 0.6839, 0.8489, 0.6979)],
    [('额', 0.1072, 0.6925, 0.1224, 0.7051)],
    [('2.本期增', 0.1375, 0.7050, 0.1941, 0.7195)],
    [('16,788,426.76', 0.3304, 0.7122, 0.4172, 0.7263), ('190,245.19', 0.4475, 0.7122, 0.5153, 0.7263), ('46,722,585.92', 0.5422, 0.7122, 0.6290, 0.7263), ('5,455,354.03', 0.6555, 0.7122, 0.7349, 0.7263), ('69,156,611.90', 0.7622, 0.7122, 0.8489, 0.7263)],
    [('加金额', 0.1072, 0.7209, 0.1526, 0.7335)],
    [('（1）计', 0.1526, 0.7333, 0.1980, 0.7479)],
    [('16,781,863.90', 0.3304, 0.7406, 0.4172, 0.7546), ('190,245.19', 0.4475, 0.7406, 0.5153, 0.7546), ('46,521,879.69', 0.5422, 0.7406, 0.6290, 0.7546), ('5,422,740.34', 0.6555, 0.7406, 0.7349, 0.7546), ('68,916,729.12', 0.7622, 0.7406, 0.8489, 0.7546)],
    [('提', 0.1072, 0.7492, 0.1224, 0.7619)],
    [('（2）外', 0.1526, 0.7617, 0.1980, 0.7764)],
    [('6,562.86', 0.3643, 0.7690, 0.4170, 0.7830), ('200,706.23', 0.5610, 0.7690, 0.6288, 0.7830), ('32,613.69', 0.6745, 0.7690, 0.7349, 0.7830), ('239,882.78', 0.7811, 0.7690, 0.8489, 0.7830)],
    [('币折算差异', 0.1072, 0.7776, 0.1828, 0.7902)],
    [('3.本期减', 0.1375, 0.7901, 0.1941, 0.8048)],
    [('745,003.34', 0.5610, 0.7975, 0.6288, 0.8115), ('1,012,237.21', 0.6555, 0.7975, 0.7349, 0.8115), ('1,757,240.55', 0.7696, 0.7975, 0.8489, 0.8115)],
    [('少金额', 0.1072, 0.8060, 0.1526, 0.8186)],
    [('（1）处', 0.1526, 0.8184, 0.1980, 0.8331)],
    [('745,003.34', 0.5610, 0.8258, 0.6288, 0.8399), ('1,012,237.21', 0.6555, 0.8258, 0.7349, 0.8399), ('1,757,240.55', 0.7696, 0.8258, 0.8489, 0.8399)],
    [('置或报废', 0.1072, 0.8343, 0.1677, 0.8470)],
    [('4.期末余', 0.1375, 0.8469, 0.1941, 0.8615)],
    [('35,493,027.52', 0.3304, 0.8542, 0.4172, 0.8682), ('2,087,026.59', 0.4362, 0.8542, 0.5155, 0.8682), ('155,932,704.19', 0.5346, 0.8542, 0.6290, 0.8682), ('14,529,351.60', 0.6481, 0.8542, 0.7348, 0.8682), ('208,042,109.90', 0.7545, 0.8542, 0.8489, 0.8682)],
    [('额', 0.1072, 0.8628, 0.1224, 0.8755)],
]

# 002273 page 149: the 累计折旧 block, whose total column wraps "479,980,535.2" / "4".
_CRYSTAL_ACCUMULATED_DEPRECIATION = [
    [('二、累计折旧', 0.0952, 0.1653, 0.1859, 0.1760)],
    [('10,951,037.4', 0.5602, 0.1821, 0.6510, 0.1928), ('76,921,692.1', 0.6737, 0.1821, 0.7645, 0.1928), ('1,896,601,430', 0.7882, 0.1821, 0.8865, 0.1928)],
    [('1.期初余额', 0.1254, 0.1893, 0.2010, 0.1999), ('373,581,683.34', 0.2881, 0.1893, 0.3940, 0.1999), ('1,435,147,017.89', 0.4141, 0.1893, 0.5351, 0.1999)],
    [('4', 0.6435, 0.1964, 0.6511, 0.2071), ('5', 0.7568, 0.1964, 0.7644, 0.2071), ('.82', 0.8638, 0.1964, 0.8865, 0.2071)],
    [('19,966,240.8', 0.6737, 0.2111, 0.7645, 0.2217), ('479,980,535.2', 0.7882, 0.2111, 0.8865, 0.2217)],
    [('2.本期增加金额', 0.1254, 0.2182, 0.2312, 0.2289), ('112,401,721.00', 0.2881, 0.2182, 0.3940, 0.2289), ('346,212,932.19', 0.4292, 0.2182, 0.5351, 0.2289), ('1,399,641.19', 0.5602, 0.2182, 0.6510, 0.2289)],
    [('6', 0.7568, 0.2253, 0.7644, 0.2360), ('4', 0.8790, 0.2253, 0.8865, 0.2360)],
    [('19,966,240.8', 0.6737, 0.2398, 0.7645, 0.2505), ('479,980,535.2', 0.7882, 0.2398, 0.8865, 0.2505)],
    [('（1）计提', 0.1556, 0.2470, 0.2238, 0.2577), ('112,401,721.00', 0.2881, 0.2470, 0.3940, 0.2577), ('346,212,932.19', 0.4292, 0.2470, 0.5351, 0.2577), ('1,399,641.19', 0.5602, 0.2470, 0.6510, 0.2577)],
    [('6', 0.7568, 0.2541, 0.7644, 0.2648), ('4', 0.8790, 0.2541, 0.8865, 0.2648)],
]

# Far East Horizon page 316: note 32, "Effective interest rate (%) | Maturity | RMB'000" per year.
_FEH_BORROWINGS = [
    [('32.', 0.1429, 0.1920, 0.1655, 0.2071), ('INTEREST-BEARING', 0.1714, 0.1920, 0.3216, 0.2071), ('BANK', 0.3265, 0.1920, 0.3700, 0.2071), ('AND', 0.3749, 0.1920, 0.4098, 0.2071), ('OTHER', 0.4146, 0.1920, 0.4675, 0.2071), ('BORROWINGS', 0.4724, 0.1920, 0.5813, 0.2071)],
    [('2024', 0.5042, 0.2461, 0.5339, 0.2582), ('2023', 0.7620, 0.2471, 0.7904, 0.2590)],
    [('Effective', 0.4141, 0.2670, 0.4667, 0.2791), ('Effective', 0.6735, 0.2680, 0.7238, 0.2799)],
    [('annual', 0.4251, 0.2825, 0.4667, 0.2946), ('annual', 0.6839, 0.2835, 0.7238, 0.2954)],
    [('interest', 0.4199, 0.2979, 0.4667, 0.3100), ('interest', 0.6791, 0.2989, 0.7238, 0.3108)],
    [('rate', 0.4190, 0.3187, 0.4431, 0.3308), ('(%)', 0.4468, 0.3187, 0.4667, 0.3308), ('Maturity', 0.5005, 0.3187, 0.5524, 0.3308), ('RMB’000', 0.5852, 0.3186, 0.6381, 0.3308), ('rate', 0.6784, 0.3197, 0.7013, 0.3316), ('(%)', 0.7050, 0.3197, 0.7238, 0.3316), ('Maturity', 0.7601, 0.3197, 0.8095, 0.3316), ('RMB’000', 0.8448, 0.3192, 0.8953, 0.3316)],
    [('Current', 0.1810, 0.3400, 0.2271, 0.3521)],
    [('Lease', 0.1810, 0.3618, 0.2137, 0.3737), ('liabilities', 0.2174, 0.3618, 0.2698, 0.3737), ('(Note', 0.2735, 0.3618, 0.3058, 0.3737), ('14(b))', 0.3095, 0.3618, 0.3438, 0.3737), ('3.64~4.90', 0.4077, 0.3608, 0.4676, 0.3729), ('2025', 0.5228, 0.3608, 0.5524, 0.3729), ('277,212', 0.5901, 0.3608, 0.6382, 0.3729), ('3.64~4.90', 0.6675, 0.3618, 0.7247, 0.3737), ('2024', 0.7811, 0.3618, 0.8095, 0.3737), ('280,495', 0.8494, 0.3618, 0.8952, 0.3737)],
    [('Bank', 0.1810, 0.3825, 0.2093, 0.3944), ('loans', 0.2130, 0.3825, 0.2440, 0.3944), ('–', 0.2477, 0.3825, 0.2544, 0.3944), ('secured', 0.2581, 0.3825, 0.3040, 0.3944), ('3.45~4.50', 0.4077, 0.3815, 0.4676, 0.3936), ('2025', 0.5228, 0.3815, 0.5524, 0.3936), ('63,570', 0.5975, 0.3815, 0.6381, 0.3936), ('3.80~4.00', 0.6675, 0.3825, 0.7247, 0.3944), ('2024', 0.7811, 0.3825, 0.8095, 0.3944), ('27,553', 0.8566, 0.3825, 0.8952, 0.3944)],
    [('Current', 0.1810, 0.4032, 0.2253, 0.4151), ('portion', 0.2290, 0.4032, 0.2725, 0.4151), ('of', 0.2762, 0.4032, 0.2878, 0.4151), ('long', 0.2915, 0.4032, 0.3179, 0.4151), ('term', 0.3216, 0.4032, 0.3492, 0.4151), ('bank', 0.3529, 0.4032, 0.3816, 0.4151)],
    [('loans', 0.1947, 0.4187, 0.2256, 0.4306), ('–', 0.2293, 0.4187, 0.2360, 0.4306), ('secured', 0.2397, 0.4187, 0.2856, 0.4306), ('3.10~5.40', 0.4077, 0.4177, 0.4676, 0.4298), ('2025', 0.5228, 0.4177, 0.5524, 0.4298), ('5,754,196', 0.5791, 0.4177, 0.6381, 0.4298), ('2.90~5.40', 0.6675, 0.4187, 0.7247, 0.4306), ('2024', 0.7811, 0.4187, 0.8095, 0.4306), ('5,472,032', 0.8392, 0.4187, 0.8952, 0.4306)],
    [('Bank', 0.1810, 0.4394, 0.2093, 0.4513), ('loans', 0.2130, 0.4394, 0.2440, 0.4513), ('–', 0.2477, 0.4394, 0.2544, 0.4513), ('unsecured', 0.2581, 0.4394, 0.3194, 0.4513), ('2.35~7.08', 0.4077, 0.4384, 0.4676, 0.4505), ('2025', 0.5228, 0.4384, 0.5524, 0.4505), ('19,089,020', 0.5716, 0.4384, 0.6382, 0.4505), ('2.90~7.18', 0.6675, 0.4394, 0.7247, 0.4513), ('2024', 0.7811, 0.4394, 0.8095, 0.4513), ('22,229,037', 0.8320, 0.4394, 0.8952, 0.4513)],
    [('Current', 0.1810, 0.4602, 0.2253, 0.4721), ('portion', 0.2290, 0.4602, 0.2725, 0.4721), ('of', 0.2762, 0.4602, 0.2878, 0.4721), ('long', 0.2915, 0.4602, 0.3179, 0.4721), ('term', 0.3216, 0.4602, 0.3492, 0.4721), ('bank', 0.3529, 0.4602, 0.3816, 0.4721)],
    [('loans', 0.1947, 0.4756, 0.2256, 0.4875), ('–', 0.2293, 0.4756, 0.2360, 0.4875), ('unsecured', 0.2397, 0.4756, 0.3011, 0.4875), ('0.80~7.12', 0.4077, 0.4746, 0.4676, 0.4867), ('2025', 0.5228, 0.4746, 0.5524, 0.4867), ('58,403,121', 0.5716, 0.4746, 0.6382, 0.4867), ('1.30~5.55', 0.6675, 0.4756, 0.7247, 0.4875), ('2024', 0.7811, 0.4756, 0.8095, 0.4875), ('47,341,854', 0.8320, 0.4756, 0.8952, 0.4875)],
]


def test_a_bracketed_row_caption_wrapped_over_its_figures_is_a_row_not_a_heading():
    tables = _tables(_HUBEI_ACCUMULATED_DEPRECIATION)
    assert Decimal("2927435003.23") in _values(tables)          # the depreciation charge
    assert Decimal("14646814.72") in _values(tables)            # （2）外币财务报表折算差额
    assert not [t.title for t in tables if t.title in ("计", "外", "投")]


def test_a_real_sub_table_heading_still_opens_its_section():
    """"（1）应收账款分类" over its column header: the next line carries no figure."""
    rows = [
        [("1.期初余额", 0.1254, 0.10, 0.2010, 0.11), ("100.00", 0.70, 0.10, 0.75, 0.11)],
        [("（2）", 0.10, 0.13, 0.13, 0.14), ("应收账款分类", 0.14, 0.13, 0.25, 0.14)],
        [("项目", 0.10, 0.15, 0.13, 0.16), ("期末余额", 0.50, 0.15, 0.56, 0.16),
         ("期初余额", 0.70, 0.15, 0.76, 0.16)],
        [("应收账款", 0.10, 0.17, 0.16, 0.18), ("50.00", 0.50, 0.17, 0.55, 0.18),
         ("40.00", 0.70, 0.17, 0.75, 0.18)],
    ]
    assert "应收账款分类" in [t.title for t in _tables(rows)]


def test_a_matrix_keeps_the_rows_whose_figures_straddle_their_caption():
    caption = BBox(x0=0.14, y0=0.4917, x1=0.20, y1=0.5063)       # "2.本期增"
    assert rr._figures_beside(caption, BBox(x0=0.24, y0=0.4985, x1=0.85, y1=0.5125))
    assert not _tight_below(caption, BBox(x0=0.24, y0=0.4985, x1=0.85, y1=0.5125))
    assert not rr._figures_beside(caption, BBox(x0=0.24, y0=0.53, x1=0.85, y1=0.54))   # a line down
    values = _values(_tables(_MONTAGE_FIXED_ASSETS))
    assert Decimal("68916729.12") in values                     # （1）计提, 合计
    assert Decimal("752450130.68") in values                    # 1.期初余额, the first movement
    assert Decimal("140642738.55") in values                    # 二、累计折旧 1.期初余额


def test_a_figure_wrapped_in_its_cell_is_put_back_together():
    fused = {w.text for w in rr._fuse_wrapped_numbers(_words(_CRYSTAL_ACCUMULATED_DEPRECIATION))}
    assert {"479,980,535.24", "19,966,240.86", "1,896,601,430.82"} <= fused
    assert Decimal("479980535.24") in _values(_tables(_CRYSTAL_ACCUMULATED_DEPRECIATION))
    # A whole amount is never extended by the figure below it.
    rows = [[("1,234.56", 0.60, 0.10, 0.66, 0.11)], [("7", 0.65, 0.115, 0.66, 0.125)]]
    assert [w.text for w in rr._fuse_wrapped_numbers(_words(rows))] == ["1,234.56", "7"]


def test_a_year_under_a_maturity_heading_is_not_an_amount():
    tables = _tables(_FEH_BORROWINGS, carry=None)
    rows = {it.raw_label: {ev.value for ev in it.values.values()} for t in tables for it in t.items}
    secured = next(v for k, v in rows.items() if k.startswith("Current portion") and "secured" in k
                   and "unsecured" not in k)
    assert Decimal("5754196") in secured and Decimal("2025") not in secured


def _row(text: str) -> list[Word]:
    out, x = [], 0.14
    for t in text.split():
        out.append(Word(text=t, bbox=BBox(x0=x, y0=0.2, x1=min(x + 0.05, 1.0), y1=0.21)))
        x = min(x + 0.06, 0.94)
    return out


@pytest.mark.parametrize("text", ["30 June 2022 as a result of the declaration of the final dividend",
                                  "31 December 2024, the balance of subordinated tranches",
                                  "31 July 2025 and 31 July 2024 were determined"])
def test_a_day_of_the_month_does_not_open_a_note(text):
    assert notes_extract._is_heading(_row(text)) is None


def test_a_note_titled_after_its_number_still_opens():
    assert notes_extract._is_heading(_row("30. TRADE AND BILLS PAYABLES"))[0] == "30"


# ── the cash-flow supplement whose closing caption wraps (000685 七、79) ──────────────────────

def test_a_supplement_closing_caption_wrapped_over_two_lines_closes_it():
    from app.stages import classify
    feat = classify.PageFeat
    feats = [feat(index=242), feat(index=243),
             feat(index=244, statement="cash_flow", matched_title="79、现金流量表补充资料",
                  matched_title_y=0.6),
             feat(index=245), feat(index=246)]
    cache = [([], 1000.0), ([{"text": "支付往来款", "y": 100.0}], 1000.0),
             ([{"text": "79、现金流量表补充资料", "y": 600.0}], 1000.0),
             ([{"text": "经营活动产生的现金流量", "y": 300.0}, {"text": "净额", "y": 312.0},
               {"text": "-248,671,370.90", "y": 306.0},
               {"text": "2．不涉及现金收支的重大投资", "y": 330.0}], 1000.0),
             ([{"text": "一、现金", "y": 100.0}], 1000.0)]
    out, extent = classify._cf_supplement_extent(["notes", "face", "face", "face", "face"], feats, cache)
    # 七、78 before the title and the cash composition after the close are notes again.
    assert out == ["notes", "notes", "face", "face", "notes"]
    assert extent[2]["supplement"] is True
    assert extent[3]["face_ends_at_y"] == pytest.approx(0.33)


def test_an_untitled_page_before_the_supplement_title_is_notes_even_when_its_close_is_unread():
    from app.stages import classify
    feat = classify.PageFeat
    feats = [feat(index=0), feat(index=1),
             feat(index=2, statement="cash_flow", matched_title="现金流量表补充资料", matched_title_y=0.2),
             feat(index=3)]
    cache = [([], 1000.0), ([], 1000.0), ([{"text": "现金流量表补充资料", "y": 200.0}], 1000.0),
             ([], 1000.0)]
    out, extent = classify._cf_supplement_extent(["notes", "face", "face", "face"], feats, cache)
    assert out == ["notes", "notes", "face", "face"] and extent == {}


# ── the note that prints the row, under a sub-heading that names the line ─────────────────────

def _table(number: str, title: str, rows: list[tuple[str, str, str]]):
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
    table = NotesTable(note_number=number, title=title, page_index=9)
    for caption, group, value in rows:
        row = NoteItem(raw_label=caption, note_number=number, group_hint=group)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(value), value_raw=Decimal(value),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


def test_a_row_under_a_sub_heading_that_names_the_line_is_that_lines_row():
    """Far East Horizon note 35 prints its share count on dated rows under "Issued and fully paid
    ordinary shares:"; the EPS note prints "Weighted average number of ordinary shares"."""
    from types import SimpleNamespace
    from app.services import line_item_notes
    item = SimpleNamespace(key="k", label="Num Outstanding Common Shares",
                           aliases=["Issued ordinary shares"], aliases_i18n={})
    share_capital = _table("35", "SHARE CAPITAL", [
        ("At 31 December 2024 (Note (i))", "Issued and fully paid ordinary shares:", "4320075666")])
    eps = _table("12", "EARNINGS PER SHARE", [
        ("Weighted average number of ordinary shares in issue", "", "4318000000")])
    hits = line_item_notes.named_row_notes(item, [eps, share_capital])
    assert [h.note for h in hits] == ["35"]


# ── CONFIG: the notes three lines are sent ────────────────────────────────────────────────────

def _items():
    from app.schemas.line_items import load_line_item_set
    path = Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_line_items.json"
    return {i.key: i for i in load_line_item_set(json.loads(path.read_text(encoding="utf-8")),
                                                 resolve=True).items}


@pytest.mark.parametrize("key, notes, want", [
    # BYD: the staff-cost rows are in 6 PROFIT BEFORE TAX; "costs" alone sent FINANCE COSTS.
    ("is_pl__personnel_and_benefit_expenses",
     [("6", "PROFIT BEFORE TAX 6. 除稅前溢利"), ("8", "FINANCE COSTS 8. 融資成本"),
      ("22", "CASH AND BANK BALANCES")], "6"),
    ("is_pl__personnel_and_benefit_expenses",
     [("七、39", "应付职工薪酬列示"), ("七、61", "营业收入和营业成本情况")], "七、39"),
    # Lai Sun prints its capitalised interest in 8 FINANCE COSTS.
    ("is_pl__interest_expense_capitalised",
     [("8", "FINANCE COSTS"), ("30", "COMPLETED PROPERTIES FOR SALE"),
      ("7", "LOSS FROM OPERATING ACTIVITIES")], "8"),
    # 开山 prints the allowance only in 七、5 应收账款.
    ("bs_ca__allow_for_doubtful_accounts",
     [("七、5", "按坏账计提方法分类披露 应收账款"), ("七、37", "合同负债"), ("七、4", "应收票据")], "七、5"),
])
def test_the_line_is_sent_the_note_its_figure_is_printed_in(key, notes, want):
    from app.services import line_item_notes
    tables = [_table(n, t, [("Total", "", "1.00")]) for n, t in notes]
    hits = line_item_notes.notes_for_line_item(_items()[key], line_item_notes.header_pool(tables))
    assert hits and hits[0].note == want


# ── the `anywhere` route reads only the pages that print one of its lines' names ───────────────

class _Page:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


def test_the_anywhere_route_reconstructs_only_the_pages_that_print_a_name():
    from types import SimpleNamespace
    from app.services.pdf_extract import _pages_printing_a_name
    line = SimpleNamespace(key="k", route="anywhere", aliases=["Issued ordinary shares"],
                           aliases_i18n={"zh": ["已发行普通股"]})
    pdf = [_Page("Directors' report. Issued and fully paid ordinary shares 720,429,301"),
           _Page("Five-year financial summary 2020 2021 2022"),
           _Page("报告期末已发行普通股总数"),
           _Page("")]                                        # no text layer: kept for OCR
    pages = [SimpleNamespace(index=i) for i in range(4)]
    kept = _pages_printing_a_name(pdf, pages, SimpleNamespace(items=[line]))
    assert [p.index for p in kept] == [0, 2, 3]
    # A line naming no row of two words keeps every page, as `other_page_rows` sends every page.
    bare = SimpleNamespace(key="b", route="anywhere", aliases=["Employees"], aliases_i18n={})
    assert len(_pages_printing_a_name(pdf, pages, SimpleNamespace(items=[line, bare]))) == 4

