"""A US debt note's terms table is read as rows, and its maturity schedule keeps its first year.

ALPHABET'S 2025 NOTE 6, two defects in a row:

* THE TERMS TABLE PASSED FOR A MATRIX AND READ AS NOTHING. Each issue prints its terms as ranges
  beside its balances — "2020 US dollar notes  2027 - 2060  0.80% - 2.25%  0.93% - 2.33%  10,000
  9,000" — and the ranges' dashes, nil cells to the matrix test, made three rows of eight
  "columns". The matrix reader made no row of it, so "Total face value of long-term debt  12,000
  49,085" never reached the model, and the matrix's header was carried onto the next page, where
  it swallowed the first line of the maturity schedule. A note matrix that reads no rows BECAUSE
  its nil cells are a range's dashes is now read as rows (`_ranges_read_as_cells`); a matrix that
  reads rows, or reads none for any other reason, is left as it was. Scanned over every face and
  notes page of the five reference filings, Asian Paints, Laurus and the three synthetic 10-K
  filings with the old code and the new: no row of any of them changes.
* THE SCHEDULE'S FIRST YEAR WAS VETOED. "2026 $ 2,000" prints 2,000, which reads as the year 2000,
  and `_caption_years` refuses a date as the row's amount, so the schedule began at 2027. An amount
  printed with a thousands separator is never a date.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

# Alphabet 2025 10-K, page 72: from the table's lead-in sentence to "Total long-term debt", every
# word as the PDF positions it (text, x0, y0, x1, y1 as fractions of the page).
WORDS = [
    ('Total', 0.1074, 0.5716, 0.1418, 0.5857),
    ('outstanding', 0.1464, 0.5716, 0.2308, 0.5857),
    ('long-term', 0.2353, 0.5716, 0.3043, 0.5857),
    ('debt', 0.3089, 0.5716, 0.3407, 0.5857),
    ('is', 0.3452, 0.5716, 0.3570, 0.5857),
    ('summarized', 0.3615, 0.5716, 0.4505, 0.5857),
    ('below', 0.4550, 0.5716, 0.4977, 0.5857),
    ('(in', 0.5022, 0.5716, 0.5203, 0.5857),
    ('millions,', 0.5249, 0.5716, 0.5839, 0.5857),
    ('except', 0.5884, 0.5716, 0.6365, 0.5857),
    ('percentages):', 0.6410, 0.5716, 0.7409, 0.5857),
    ('Effective', 0.6093, 0.6102, 0.6637, 0.6215),
    ('Interest', 0.6674, 0.6102, 0.7145, 0.6215),
    ('Rate', 0.6477, 0.6203, 0.6761, 0.6316),
    ('As', 0.7680, 0.6014, 0.7847, 0.6127),
    ('of', 0.7883, 0.6014, 0.8006, 0.6127),
    ('December', 0.8043, 0.6014, 0.8674, 0.6127),
    ('31,', 0.8710, 0.6014, 0.8892, 0.6127),
    ('Maturity', 0.3975, 0.6203, 0.4483, 0.6316),
    ('Coupon', 0.4950, 0.6203, 0.5444, 0.6316),
    ('Rate', 0.5480, 0.6203, 0.5763, 0.6316),
    ('2024', 0.7626, 0.6203, 0.7916, 0.6316),
    ('2025', 0.8655, 0.6203, 0.8945, 0.6316),
    ('Debt', 0.0749, 0.6364, 0.1112, 0.6505),
    ('2016', 0.0749, 0.6554, 0.1112, 0.6695),
    ('US', 0.1158, 0.6554, 0.1384, 0.6695),
    ('dollar', 0.1430, 0.6554, 0.1829, 0.6695),
    ('notes', 0.1874, 0.6554, 0.2274, 0.6695),
    ('2026', 0.4047, 0.6554, 0.4411, 0.6695),
    ('2.00%', 0.5140, 0.6554, 0.5603, 0.6695),
    ('2.23%', 0.6403, 0.6554, 0.6866, 0.6695),
    ('$', 0.7330, 0.6554, 0.7421, 0.6695),
    ('2,000', 0.7780, 0.6554, 0.8188, 0.6695),
    ('$', 0.8359, 0.6554, 0.8450, 0.6695),
    ('2,000', 0.8809, 0.6554, 0.9218, 0.6695),
    ('2020', 0.0749, 0.6743, 0.1112, 0.6884),
    ('US', 0.1158, 0.6743, 0.1384, 0.6884),
    ('dollar', 0.1430, 0.6743, 0.1829, 0.6884),
    ('notes', 0.1874, 0.6743, 0.2274, 0.6884),
    ('2027', 0.3776, 0.6743, 0.4139, 0.6884),
    ('-', 0.4184, 0.6743, 0.4239, 0.6884),
    ('2060', 0.4284, 0.6743, 0.4648, 0.6884),
    ('0.80%', 0.4821, 0.6743, 0.5284, 0.6884),
    ('-', 0.5329, 0.6743, 0.5384, 0.6884),
    ('2.25%', 0.5429, 0.6743, 0.5892, 0.6884),
    ('0.93%', 0.6083, 0.6743, 0.6546, 0.6884),
    ('-', 0.6592, 0.6743, 0.6646, 0.6884),
    ('2.33%', 0.6691, 0.6743, 0.7154, 0.6884),
    ('10,000', 0.7689, 0.6743, 0.8188, 0.6884),
    ('9,000', 0.8809, 0.6743, 0.9218, 0.6884),
    ('2025', 0.0749, 0.6933, 0.1112, 0.7074),
    ('US', 0.1158, 0.6933, 0.1384, 0.7074),
    ('dollar', 0.1430, 0.6933, 0.1829, 0.7074),
    ('notes(1)', 0.1874, 0.6933, 0.2404, 0.7074),
    ('2028', 0.3776, 0.6933, 0.4139, 0.7074),
    ('-', 0.4184, 0.6933, 0.4239, 0.7074),
    ('2075', 0.4284, 0.6933, 0.4648, 0.7074),
    ('3.88%', 0.4821, 0.6933, 0.5284, 0.7074),
    ('-', 0.5329, 0.6933, 0.5384, 0.7074),
    ('5.70%', 0.5429, 0.6933, 0.5892, 0.7074),
    ('4.00%', 0.6083, 0.6933, 0.6546, 0.7074),
    ('-', 0.6592, 0.6933, 0.6646, 0.7074),
    ('5.79%', 0.6691, 0.6933, 0.7154, 0.7074),
    ('0', 0.8097, 0.6933, 0.8188, 0.7074),
    ('22,500', 0.8718, 0.6933, 0.9218, 0.7074),
    ('2025', 0.0749, 0.7122, 0.1112, 0.7263),
    ('Euro', 0.1158, 0.7122, 0.1502, 0.7263),
    ('notes(2)', 0.1548, 0.7122, 0.2077, 0.7263),
    ('2028', 0.3776, 0.7122, 0.4139, 0.7263),
    ('-', 0.4184, 0.7122, 0.4239, 0.7263),
    ('2064', 0.4284, 0.7122, 0.4648, 0.7263),
    ('2.38%', 0.4821, 0.7122, 0.5284, 0.7263),
    ('-', 0.5329, 0.7122, 0.5384, 0.7263),
    ('4.38%', 0.5429, 0.7122, 0.5892, 0.7263),
    ('2.57%', 0.6083, 0.7122, 0.6546, 0.7263),
    ('-', 0.6592, 0.7122, 0.6646, 0.7263),
    ('4.51%', 0.6691, 0.7122, 0.7154, 0.7263),
    ('0', 0.8097, 0.7122, 0.8188, 0.7263),
    ('15,585', 0.8718, 0.7122, 0.9218, 0.7263),
    ('Total', 0.1020, 0.7311, 0.1410, 0.7452),
    ('face', 0.1456, 0.7311, 0.1783, 0.7452),
    ('value', 0.1828, 0.7311, 0.2245, 0.7452),
    ('of', 0.2291, 0.7311, 0.2445, 0.7452),
    ('long-term', 0.2490, 0.7311, 0.3243, 0.7452),
    ('debt', 0.3288, 0.7311, 0.3633, 0.7452),
    ('12,000', 0.7689, 0.7311, 0.8188, 0.7452),
    ('49,085', 0.8718, 0.7311, 0.9218, 0.7452),
    ('Unamortized', 0.0749, 0.7498, 0.1675, 0.7639),
    ('discount', 0.1720, 0.7498, 0.2329, 0.7639),
    ('and', 0.2374, 0.7498, 0.2646, 0.7639),
    ('debt', 0.2692, 0.7498, 0.3009, 0.7639),
    ('issuance', 0.0749, 0.7624, 0.1394, 0.7765),
    ('costs(2)', 0.1439, 0.7624, 0.1950, 0.7765),
    ('(118)', 0.7861, 0.7624, 0.8243, 0.7765),
    ('(542)', 0.8891, 0.7624, 0.9272, 0.7765),
    ('Less:', 0.0749, 0.7810, 0.1139, 0.7951),
    ('current', 0.1184, 0.7810, 0.1693, 0.7951),
    ('portion', 0.1738, 0.7810, 0.2237, 0.7951),
    ('of', 0.2283, 0.7810, 0.2419, 0.7951),
    ('long-term', 0.2464, 0.7810, 0.3154, 0.7951),
    ('notes(3)', 0.0749, 0.7936, 0.1278, 0.8077),
    ('(999)', 0.7861, 0.7936, 0.8243, 0.8077),
    ('(1,996)', 0.8755, 0.7936, 0.9272, 0.8077),
    ('Total', 0.1066, 0.8126, 0.1456, 0.8267),
    ('long-term', 0.1501, 0.8126, 0.2254, 0.8267),
    ('debt', 0.2299, 0.8126, 0.2643, 0.8267),
    ('$', 0.7330, 0.8126, 0.7421, 0.8267),
    ('10,883', 0.7689, 0.8126, 0.8188, 0.8267),
    ('$', 0.8359, 0.8126, 0.8450, 0.8267),
    ('46,547', 0.8718, 0.8126, 0.9218, 0.8267),
]


def _rows(words, **kw):
    items, _ = build_line_items(words, page_index=71, document_id="d", source_kind="native",
                                on_face=False, **kw)
    return {li.source_label: {ev.period_label: str(ev.value) for ev in li.values.values()}
            for li in items}


def test_the_terms_table_is_read_as_rows():
    rows = _rows([Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d)) for t, a, b, c, d in WORDS])
    assert rows.get("Total face value of long-term debt") == {"prior": "12000", "current": "49085"}, rows
    assert rows.get("Total long-term debt $") == {"prior": "10883", "current": "46547"}, rows


def _schedule():
    words, y = [], 0.20
    def line(*cells):
        nonlocal y
        for text, x0, x1 in cells:
            words.append(Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + 0.0127)))
        y += 0.019
    line(("As", 0.075, 0.09), ("of", 0.095, 0.11), ("December", 0.115, 0.18), ("31,", 0.185, 0.20),
         ("2025,", 0.205, 0.24), ("payments", 0.245, 0.31), ("were", 0.315, 0.35), ("as", 0.355, 0.37),
         ("follows", 0.375, 0.42), ("(in", 0.425, 0.44), ("millions):", 0.445, 0.51))
    line(("2026", 0.075, 0.111), ("$", 0.799, 0.808), ("2,000", 0.881, 0.922))
    for year, amount in (("2027", "1,000"), ("2028", "2,676"), ("2029", "1,764"), ("2030", "5,500")):
        line((year, 0.075, 0.111), (amount, 0.883, 0.924))
    line(("Thereafter", 0.075, 0.15), ("36,145", 0.875, 0.924))
    line(("Total", 0.075, 0.11), ("$", 0.799, 0.808), ("49,085", 0.875, 0.924))
    return words


def test_the_maturity_schedule_keeps_its_first_year():
    rows = _rows(_schedule())
    firsts = [c for c in rows if c.startswith("2026")]
    assert firsts and rows[firsts[0]] == {"current": "2000"}, rows
    assert [c[:4] for c in rows if c[:2] == "20"] == ["2026", "2027", "2028", "2029", "2030"], rows
