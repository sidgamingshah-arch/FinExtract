r"""A CAS NOTE HEADING THAT SWALLOWS A STATEMENT NAME IS NOT THAT STATEMENT'S FACE.

NEW FILE -> backend/tests/test_classify_note_headings.py

`_TITLE_NEGATIVE` already disqualified 资产负债表日后事项 — the standard CAS subsequent-events note,
which contains 资产负债表 verbatim and was resolving as a balance-sheet FACE title. Two more shapes
of the same swallowing were live, both measured on a 287-page Shenzhen-listed filing (10972689):

    78、现金流量表项目          note 78, "cash flow statement ITEMS" — PDF p.227. It matched
                             现金流量表 STRONG at 0.95 and took pp.228-230 as continuations.
    资产负债表日存在的重要承诺     "significant commitments existing AT the balance sheet DATE"
                             — PDF p.268, read as a balance-sheet face.

A statement name with 项目 after it names that statement's LINE ITEMS, which is what a CAS filing
calls the note decomposing them; 合并财务报表项目注释 heads the entire notes block. And 资产负债表日
is a DATE — whatever follows it, the heading is about the date — so the second arm GENERALISES the
日后事项 case above rather than sitting beside it as another special case.

WHAT THIS COST, and it is why the arms are worth the reach they have. On the reference corpus these
two disqualifiers change 94 published statement figures across the six CAS filings and none at all
on the six non-CAS ones. The one figure checked against the filing's own words is a tenfold
correction: 3bfe0c0e published cost of sales of 1,122,699,876.79 where the filing states revenue of
11,163 万元 (111,630,000) and reports it down 90.68%; it now publishes 118,791,058.48. The
`cf_financing__unexplained_adjustments` plug — which exists to absorb what does not reconcile —
falls from 1,759,050,621.91 to 364,752,830.21 on that filing and from 16,016,755,289 to
6,127,429,882 on b09ca2c1, so the cash-flow statements tie considerably better than before.
"""
from __future__ import annotations

from app.stages.classify import _TITLE_NEGATIVE


def test_a_statement_name_followed_by_items_is_a_note_heading():
    """项目 after the name means "the line items OF that statement" — the note, not the face."""
    for line in ("78、现金流量表项目", "现金流量表项目", "资产负债表项目",
                 "合并财务报表项目注释", "利润表项目", "所有者权益变动表项目"):
        assert _TITLE_NEGATIVE.search(line), line


def test_the_balance_sheet_date_is_a_date_and_not_a_title():
    """资产负债表日 names the reporting date, so every heading built on it is about the date."""
    for line in ("资产负债表日存在的重要承诺", "资产负债表日后事项", "資產負債表日後事項",
                 "资产负债表日之后的调整事项", "资产负债表日的公允价值"):
        assert _TITLE_NEGATIVE.search(line), line


def test_a_real_statement_title_survives_both_arms():
    """THE LINE THEY MUST NOT CROSS. `_title_candidates` joins adjacent lines into one candidate, so
    the text tested here really can carry the statement name and its date together — and there 日
    ends the candidate, which is why the date arm requires a NON-DIGIT after 日."""
    for line in ("合并资产负债表", "母公司资产负债表", "合并利润表", "合并现金流量表",
                 "合并所有者权益变动表", "8、母公司所有者权益变动表",
                 "合并资产负债表 2024年12月31日", "资产负债表 2024年12月31日",
                 "Consolidated Statement of Financial Position",
                 "綜合損益及其他全面收益表"):
        assert not _TITLE_NEGATIVE.search(line), line


def test_the_existing_disqualifiers_are_untouched():
    """The arms were added to a list that already carried the contents page, the auditor's prose
    and the note banner; a regression there would start the face region in the front matter."""
    for line in ("Notes to the consolidated financial statements", "附註", "目录",
                 "In our opinion", "we have audited", "財務報表附註"):
        assert _TITLE_NEGATIVE.search(line), line
