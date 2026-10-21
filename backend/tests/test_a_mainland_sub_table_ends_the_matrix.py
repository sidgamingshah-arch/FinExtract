"""A MAINLAND SUB-TABLE HEADING UNDER A MATRIX ENDS IT.

000685 prints the financing-liability movement (six columns) and, under it on the same page,
"79、现金流量表补充资料" — the indirect reconciliation. Only Latin enumerated headings ("(a) Capital
work-in-progress") ended a matrix, so the reconciliation's rows, 固定资产折旧 among them, were lost.
"""
from __future__ import annotations

import pytest

from app.services.row_reconstruct import _SUB_TABLE_HEADING


@pytest.mark.parametrize("text", ["（4） 以净额列报现金流量的说明", "79、现金流量表补充资料",
                                  "（1） 现金流量表补充资料", "(a) Capital work-in-progress"])
def test_a_numbered_heading_starts_a_sub_table(text):
    assert _SUB_TABLE_HEADING.match(text)


@pytest.mark.parametrize("text", ["（一）", "2024 年", "合计", "Total 1,000"])
def test_a_bare_number_or_a_caption_does_not(text):
    assert not _SUB_TABLE_HEADING.match(text)
