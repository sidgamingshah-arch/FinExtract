r"""A COMPANY-ONLY NOTE'S ROWS BELONG TO THE COMPANY'S COLUMN, not to the group's.

NEW FILE -> backend/tests/test_note_table_basis.py

A CSRC filing repeats every material balance for the parent alone, under the chapter heading
母公司财务报表(主要)项目注释, and `notes_extract` already marks those tables `basis=STANDALONE` from
that heading. Its comment states the contract: "the note's ``basis`` is set from it, so a consumer
computing a consolidated figure can decline a company-only note instead of summing across bases."

`note_sourced` was not that consumer. It read the basis off the row's VALUE — which comes from the
note's own columns and says nothing about whose statements the note explains — so the producer
honoured the distinction and nothing downstream did.

MEASURED ON 1223214527, whose revenue note is printed twice: note 七、35 (the group's, p179) reads
411,974,409.31 and note 十八、4 (the parent's, p206) reads 408,721,552.34, both captioned 主营业务.
Both were filed under `consolidated`, so which one reached the published figure was decided by
nothing better than which pages the classifier happened to read as notes — it published the PARENT's
408,721,552.34 as the group's revenue, and with page detection widened it published their sum,
820,695,961.65, against the 422,239,300 the filing states in its own MD&A.

Swept across the twelve reference filings: 96 figures move, all on CAS filings, and they move
BETWEEN the two bases rather than appearing or vanishing — on d84d0937 consolidated other current
assets rises 427,501,368 and standalone falls by exactly that, and on 8ad0c02c the pair is
429,679,513 each way. Equal and opposite is the signature of a figure that was in the wrong column
rather than of one being invented.
"""
from __future__ import annotations

from app.core.models.enums import Basis
from app.services.note_sourced import _table_basis


class _Table:
    def __init__(self, basis=None) -> None:
        self.basis = basis


class _Value:
    def __init__(self, basis="consolidated") -> None:
        self.basis = basis


def test_a_company_only_note_files_its_rows_under_standalone():
    """THE WHOLE POINT. The note's chapter says these figures are the parent's; the row's own
    column cannot say so and was being believed instead."""
    assert _table_basis(_Table(Basis.STANDALONE), _Value("consolidated")) == "standalone"


def test_a_note_that_declares_no_basis_leaves_the_row_alone():
    """THE NARROWING NEVER INVENTS. Every English filing and every chapter that is not the parent
    company's declares nothing, and there the row's own basis is still the answer — which is why
    this change moves no figure on the seven non-CAS filings in the corpus."""
    assert _table_basis(_Table(None), _Value("consolidated")) == "consolidated"
    assert _table_basis(_Table(None), _Value("standalone")) == "standalone"


def test_a_consolidated_chapter_is_honoured_the_same_way():
    """The mechanism is not a special case for the parent: a chapter that declares the group's
    basis is believed over the row's just as readily."""
    assert _table_basis(_Table(Basis.CONSOLIDATED), _Value("standalone")) == "consolidated"


def test_an_enum_and_a_bare_string_both_resolve():
    """`Basis` is an enum and `str()` on it gives "Basis.CONSOLIDATED", which keys a slot nothing
    can look up — the same trap `_basis_of` documents. A stored definition can also carry the plain
    string, so both spellings have to land on the same slot key."""
    assert _table_basis(_Table(Basis.STANDALONE), _Value()) == "standalone"
    assert _table_basis(_Table("standalone"), _Value()) == "standalone"
    assert "Basis." not in _table_basis(_Table(Basis.CONSOLIDATED), _Value())


def test_an_empty_declaration_falls_back_rather_than_keying_an_empty_slot():
    """An empty string is not a basis. Believing it would file the row under "" — a slot the grid,
    the checks and the export all look up and none of them find."""
    assert _table_basis(_Table(""), _Value("consolidated")) == "consolidated"
