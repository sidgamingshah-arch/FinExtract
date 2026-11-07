"""A US Form 10-K's note footnotes and column-heading lines are not rows.

Alphabet's 2025 debt note sent the model three rows that are not rows:

* "Maturity Coupon Rate Rate" with figures 2,024 and 2,025 — the column-heading line, whose terms
  columns' names left of the years made it a captioned row;
* "Financing Rate (SOFR) plus 0.52%, reset quarterly." with -2 — the "(2)" that opens the next
  footnote, read as a negative figure and given the line above as its caption;
* "exchange rates." with 7 — "(3) … See Note 7 for further details": "(3)" read as -3, and then the
  sentence's "7" as the next figure.

Footnotes are taken out before the rows are built, and a row whose only figures are consecutive
years is the heading line (`row_reconstruct.us_10k_rows`). Hong Kong and mainland filings print
"(1) Trade receivables  1,234" as real rows and the readers are calibrated on them, so both
readings are switched on for a filing DETECTED as a Form 10-K only (`regime.is_us_10k_filing`,
decided in classify). Every filing in testdata/filings — 21 HKEX, mainland and Indian reports,
PetroChina's SEC-registered one among them — is detected as not a 10-K and read exactly as before.

Read with the switch on and off, every face and notes page of Alphabet and the three synthetic
10-K filings differ only by rows taken out, and each one taken out is one of these shapes: the three
above, two more heading lines of years (Alphabet's AOCI reclassification table, Bluegate's "Fiscal
2026 Fiscal 2025 Fiscal 2024" three times) and an exhibit-index line with two dates for figures.
"""
from __future__ import annotations

import pytest

from app.core.models.geometry import BBox
from app.services.regime import is_us_10k_filing
from app.services.row_reconstruct import Word, build_line_items, us_10k_rows


@pytest.mark.parametrize("pages", [
    ["UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C. 20549 FORM 10-K "
     "ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934"],
    ["FORM 10-K", "Report of Independent Registered Public Accounting Firm"],
])
def test_a_10k_is_detected(pages):
    assert is_us_10k_filing(pages)


@pytest.mark.parametrize("pages", [
    # A dual-listed issuer's annual report: the SEC named, but the form is a 20-F.
    ["The Company files an annual report on Form 20-F with the Securities and Exchange Commission."],
    # A 10-K mentioned with nothing else of the SEC's.
    ["Its peer's Form 10-K for the year reported a loss."],
    ["二零二四年年報 HK$'000 Hong Kong Financial Reporting Standards"],
])
def test_a_report_that_is_not_a_10k_is_not_detected(pages):
    assert not is_us_10k_filing(pages)


# Alphabet 2025 10-K, page 72: the debt table from its lead-in sentence to its footnotes, every word
# as the PDF positions it (text, x0, y0, x1, y1 as fractions of the page).
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
    ('(1)', 0.0706, 0.8366, 0.0823, 0.8449),
    ('Includes', 0.1074, 0.8366, 0.1621, 0.8493),
    ('$500', 0.1689, 0.8366, 0.2016, 0.8493),
    ('million', 0.2085, 0.8366, 0.2502, 0.8493),
    ('of', 0.2569, 0.8366, 0.2692, 0.8493),
    ('floating-rate', 0.2760, 0.8366, 0.3537, 0.8493),
    ('notes', 0.3605, 0.8366, 0.3965, 0.8493),
    ('due', 0.4034, 0.8366, 0.4279, 0.8493),
    ('in', 0.4347, 0.8366, 0.4462, 0.8493),
    ('2028.', 0.4530, 0.8366, 0.4898, 0.8493),
    ('Interest', 0.4967, 0.8366, 0.5457, 0.8493),
    ('is', 0.5526, 0.8366, 0.5632, 0.8493),
    ('calculated', 0.5701, 0.8366, 0.6363, 0.8493),
    ('using', 0.6431, 0.8366, 0.6783, 0.8493),
    ('the', 0.6851, 0.8366, 0.7056, 0.8493),
    ('compounded', 0.7124, 0.8366, 0.7974, 0.8493),
    ('Secured', 0.8043, 0.8366, 0.8590, 0.8493),
    ('Overnight', 0.8659, 0.8366, 0.9296, 0.8493),
    ('Financing', 0.1074, 0.8503, 0.1711, 0.8630),
    ('Rate', 0.1752, 0.8503, 0.2062, 0.8630),
    ('(SOFR)', 0.2103, 0.8503, 0.2610, 0.8630),
    ('plus', 0.2651, 0.8503, 0.2920, 0.8630),
    ('0.52%,', 0.2959, 0.8503, 0.3417, 0.8630),
    ('reset', 0.3458, 0.8503, 0.3785, 0.8630),
    ('quarterly.', 0.3826, 0.8503, 0.4428, 0.8630),
    ('(2)', 0.0706, 0.8639, 0.0823, 0.8722),
    ('Principal,', 0.1074, 0.8639, 0.1678, 0.8766),
    ('unamortized', 0.1761, 0.8639, 0.2570, 0.8766),
    ('discount,', 0.2653, 0.8639, 0.3241, 0.8766),
    ('and', 0.3324, 0.8639, 0.3569, 0.8766),
    ('debt', 0.3652, 0.8639, 0.3938, 0.8766),
    ('issuance', 0.4021, 0.8639, 0.4601, 0.8766),
    ('costs', 0.4684, 0.8639, 0.5027, 0.8766),
    ('for', 0.5109, 0.8639, 0.5281, 0.8766),
    ('the', 0.5364, 0.8639, 0.5568, 0.8766),
    ('euro-denominated', 0.5651, 0.8639, 0.6844, 0.8766),
    ('notes', 0.6927, 0.8639, 0.7286, 0.8766),
    ('include', 0.7369, 0.8639, 0.7835, 0.8766),
    ('the', 0.7918, 0.8639, 0.8122, 0.8766),
    ('effect', 0.8205, 0.8639, 0.8562, 0.8766),
    ('of', 0.8645, 0.8639, 0.8767, 0.8766),
    ('foreign', 0.8850, 0.8639, 0.9299, 0.8766),
    ('exchange', 0.1074, 0.8775, 0.1711, 0.8902),
    ('rates.', 0.1752, 0.8775, 0.2120, 0.8902),
    ('(3)', 0.0706, 0.8912, 0.0823, 0.8994),
    ('Total', 0.1074, 0.8912, 0.1384, 0.9039),
    ('current', 0.1432, 0.8912, 0.1890, 0.9039),
    ('portion', 0.1938, 0.8912, 0.2387, 0.9039),
    ('of', 0.2435, 0.8912, 0.2558, 0.9039),
    ('long-term', 0.2606, 0.8912, 0.3227, 0.9039),
    ('debt', 0.3275, 0.8912, 0.3561, 0.9039),
    ('is', 0.3609, 0.8912, 0.3715, 0.9039),
    ('included', 0.3763, 0.8912, 0.4311, 0.9039),
    ('within', 0.4359, 0.8912, 0.4735, 0.9039),
    ('accrued', 0.4782, 0.8912, 0.5306, 0.9039),
    ('expenses', 0.5354, 0.8912, 0.5983, 0.9039),
    ('and', 0.6031, 0.8912, 0.6276, 0.9039),
    ('other', 0.6324, 0.8912, 0.6659, 0.9039),
    ('current', 0.6707, 0.8912, 0.7165, 0.9039),
    ('liabilities.', 0.7213, 0.8912, 0.7809, 0.9039),
    ('See', 0.7857, 0.8912, 0.8119, 0.9039),
    ('Note', 0.8162, 0.8912, 0.8472, 0.9039),
    ('7', 0.8520, 0.8912, 0.8602, 0.9039),
    ('for', 0.8650, 0.8912, 0.8821, 0.9039),
    ('further', 0.8869, 0.8912, 0.9294, 0.9039),
]


def _rows():
    items, _ = build_line_items([Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d))
                                 for t, a, b, c, d in WORDS],
                                page_index=71, document_id="d", source_kind="native", on_face=False)
    return {li.source_label: {ev.period_label: str(ev.value) for ev in li.values.values()}
            for li in items}


REAL = {"Total face value of long-term debt": {"prior": "12000", "current": "49085"},
        "Unamortized discount and debt issuance costs(2)": {"prior": "-118", "current": "-542"},
        "Less: current portion of long-term notes(3)": {"prior": "-999", "current": "-1996"},
        "Total long-term debt $": {"prior": "10883", "current": "46547"}}


def test_on_a_10k_the_table_reads_its_rows_and_nothing_else():
    with us_10k_rows(True):
        rows = _rows()
    for caption, figures in REAL.items():
        assert rows.get(caption) == figures, (caption, rows)
    assert len(rows) == 8, rows          # the four issues and the four totals
    assert not [c for c in rows if c.startswith("Maturity") or "SOFR" in c or "exchange rates" in c], rows


def test_off_a_10k_the_same_words_read_as_they_always_have():
    # The gate, not the words, is what changes the reading: with the switches off the heading line
    # and the two footnote rows are still there, and so are the real rows.
    rows = _rows()
    for caption, figures in REAL.items():
        assert rows.get(caption) == figures, (caption, rows)
    assert rows.get("Maturity Coupon Rate Rate") == {"prior": "2024", "current": "2025"}, rows
    assert any("SOFR" in c for c in rows), rows


def test_a_numbered_row_with_an_amount_is_not_a_footnote():
    from app.services.row_reconstruct import _without_footnotes
    line = [("(1)", 0.071, 0.50, 0.090, 0.512), ("Amounts", 0.107, 0.50, 0.16, 0.512),
            ("due", 0.165, 0.50, 0.19, 0.512), ("from", 0.195, 0.50, 0.22, 0.512),
            ("affiliates", 0.225, 0.50, 0.29, 0.512), ("and", 0.295, 0.50, 0.32, 0.512),
            ("other", 0.325, 0.50, 0.36, 0.512), ("parties", 0.365, 0.50, 0.41, 0.512),
            ("1,234", 0.86, 0.50, 0.92, 0.512)]
    words = [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d)) for t, a, b, c, d in line]
    assert _without_footnotes(words) == words
