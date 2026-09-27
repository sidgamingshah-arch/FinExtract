"""A CAS STATEMENT TITLE PRINTED MID-PAGE IS ENUMERATED AND QUALIFIED — and is still a title.

NEW FILE -> backend/tests/test_an_enumerated_title_is_still_a_title.py

THE REGRESSION. c1dbbf9 raised the mid-page coverage floor from 0.35 to 0.70 so a line of
narrative ("Strong balance sheet supporting", 0.43) could not latch a face in an Indian integrated
report's front matter. Its message recorded the change as inert on the shipped corpus. It was not:
a mainland filing prints each statement's title MID-PAGE, below the previous statement's last
table, and prints it ENUMERATED and QUALIFIED BY BASIS:

    1、合并资产负债表      0.56 of its line
    2、母公司资产负债表    0.50
    3、合并利润表          0.43
    7、合并所有者权益变动表  (the pattern names 权益变动表) 0.45
    现金流量表补充资料      0.56   — the indirect-method schedule

MEASURED on the three CAS reference filings, deterministic route, before c1dbbf9 -> after:

    000709   505 figures, 20 pass / 12 fail   ->  292 figures, ALL 49 relations skipped
             (no balance sheet at all: pages 81-86 resolved no statement)
    300319   519 figures, 24 pass / 6 fail    ->  414 figures, 12 pass / 4 fail
    688008   482 figures                      ->  403 figures

THE FIX measures the stricter floor against the title's BODY — enumerator stripped, only the
script the match is written in, and the words that say whose statement, whose equity, or which
schedule removed. The title band's 0.35 floor still measures the raw line, as calibrated. With it,
all three filings return EXACTLY to their pre-c1dbbf9 figures on both routes — 0 figures and 0
structural statuses different — and every Asian Paints case c1dbbf9 was written for stays refused.
"""
from __future__ import annotations

import pytest

from app.stages.classify import (_MID_PAGE_TITLE_COVERAGE, _covers_title, _mid_page_statement,
                                 _title_body)


def _lines(*texts: str, start: float = 36.0, step: float = 14.0) -> list[dict]:
    return [{"text": t, "y": start + i * step, "size": 10.0, "bold": False}
            for i, t in enumerate(texts)]


# ── the CAS titles the floor refused ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("match, title", [
    ("资产负债表", "1、合并资产负债表"),
    ("资产负债表", "2、母公司资产负债表"),
    ("利润表", "3、合并利润表"),
    ("现金流量表", "（五）合并现金流量表"),
    ("权益变动表", "7、合并所有者权益变动表"),
    ("权益变动表", "8、母公司股东权益变动表"),
    ("现金流量表", "现金流量表补充资料"),
    ("资产负债表", "合并资产负债表（续）"),
])
def test_an_enumerated_qualified_cas_title_passes_the_mid_page_floor(match, title) -> None:
    assert _covers_title(match, title, _MID_PAGE_TITLE_COVERAGE), _title_body(match, title)


def test_a_bilingual_title_is_measured_in_the_script_it_matched() -> None:
    """0.52 of its raw line — the title-band test records it — and the same title twice."""
    assert _covers_title("statement of cash flows",
                         "CONSOLIDATED STATEMENT OF CASH FLOWS 綜合現金流量表",
                         _MID_PAGE_TITLE_COVERAGE)


def test_000709s_balance_sheet_page_resolves_again() -> None:
    """Page 81: the auditor's report ends in the title band, and 1、合并资产负债表 opens mid-page."""
    lines = _lines("河钢股份有限公司2024 年年度报告全文",
                   "（五）评价财务报表的总体列报、结构和内容",
                   "项。",
                   "1、合并资产负债表",
                   "货币资金 5,530,573,519.00 4,432,731,269.95")
    statement, _combined, title, _ambig = _mid_page_statement(lines, lines[:3])

    assert statement == "balance_sheet", (statement, title)
    assert title == "1、合并资产负债表"


# ── what c1dbbf9 was written to refuse, still refused ─────────────────────────────────────────

@pytest.mark.parametrize("match, line", [
    ("balance sheet", "Strong balance sheet supporting"),              # Asian Paints p42
    ("income statement", "Ten-year income statement highlights and ratios"),
    ("资产负债表", "公司资产负债表的主要变动原因分析"),                      # an MD&A-style sentence
    ("利润表", "利润表项目"),
])
def test_a_sentence_containing_a_statement_name_is_still_refused(match, line) -> None:
    assert not _covers_title(match, line, _MID_PAGE_TITLE_COVERAGE), _title_body(match, line)


def test_the_title_band_floor_still_measures_the_raw_line() -> None:
    """Nothing about the calibrated 0.35 path moves: no body, no stripping."""
    assert _covers_title("balance sheet", "Strong balance sheet supporting")        # 0.43 >= 0.35
    assert not _covers_title("资产负债表", "关于公司资产负债表主要项目的重大变动情况说明")


def test_the_body_keeps_the_enumerator_out_and_the_name_in() -> None:
    assert _title_body("资产负债表", "1、合并资产负债表") == "资产负债表"
    assert _title_body("statement of cash flows",
                       "CONSOLIDATED STATEMENT OF CASH FLOWS 綜合現金流量表") == \
        "STATEMENT OF CASH FLOWS"
    assert _title_body("balance sheet", "Strong balance sheet supporting") == \
        "Strong balance sheet supporting"
