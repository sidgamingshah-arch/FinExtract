"""TWO FIGURES THE REQUEST PRINTED AND A CITATION COULD NOT NAME.

ONE — AN ATTRIBUTION ROW WITHOUT THE TOTAL IT SPLITS. An HKFRS / IFRS income statement prints
"Owners of the Company" / "Non-controlling interests" under the profit for the year and again under
the total comprehensive income. Lai Sun prints both pairs under a bare "Attributable to:", BYD's
second pair arrived under the OCI sub-heading carried from above it, and a citation of "Owners of
the Company" matched two rows with different figures (-1,675,589 and -1,562,216). Each such row now
carries, as its `subheading`, the total it splits, and a citation's `group` names it.

TWO — A COMPARATIVE PRINTED INSIDE A CAPTION. BYD note 30 prints "Issued and fully paid
2,253,204,500 (2023: 2,253,204,500) ordinary shares" with the amount of share capital in the figure
column. The request now carries both counts as the row's `share_counts`, and a citation of
`column` "share_counts" resolves to them; the money figures are left as they are.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import face_context, note_context
from app.services.line_item_llm import REPLY_CONTRACT
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

PL = "profit_and_loss"


def _row(caption, current, prior, *, page=5, banner=None, sub=None):
    li = LineItem(source_label=caption)
    if banner:
        li.section_hint = banner
    if sub:
        li.group_hint = sub
    for label, v in (("current", current), ("prior", prior)):
        li.values[label] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label,
                                          value=Decimal(v), value_raw=Decimal(v),
                                          provenance=Provenance(page_index=page))
    return li


def _doc(rows, pages=(5, 6)):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=i, kind=PageKind.FACE, statement=PL) for i in pages]
    doc.line_items = list(rows)
    return doc


def _lai_sun():
    """Both pairs under a bare "Attributable to:", the total after each pair."""
    return _doc([
        _row("LOSS FOR THE YEAR", "-3237857", "-4255979"),
        _row("Owners of the Company", "-1675589", "-2167836", banner="Attributable to:"),
        _row("Non-controlling interests", "-1562268", "-2088143", banner="Attributable to:"),
        _row("LOSS FOR THE YEAR", "-3237857", "-4255979", page=6),
        _row("Exchange realignments", "243212", "-229062", page=6,
             banner="OTHER COMPREHENSIVE INCOME/(EXPENSE)"),
        _row("TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR", "-2969793", "-4642640", page=6,
             banner="OTHER COMPREHENSIVE INCOME/(EXPENSE)"),
        _row("Owners of the Company", "-1562216", "-2329469", page=6, banner="Attributable to:"),
        _row("Non-controlling interests", "-1407577", "-2313171", page=6,
             banner="Attributable to:"),
    ])


def _sent(doc):
    return [(r["caption"], r.get("subheading")) for r in face_context.face_rows(doc, {PL})[0]["rows"]]


def test_each_pair_names_the_total_it_splits():
    sent = _sent(_lai_sun())
    assert ("Owners of the Company", "LOSS FOR THE YEAR — Attributable to:") in sent
    assert ("Owners of the Company",
            "TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR — Attributable to:") in sent
    assert ("Non-controlling interests",
            "TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR — Attributable to:") in sent
    assert ("LOSS FOR THE YEAR", None) in sent   # a total itself carries none


def test_a_banner_that_names_its_total_is_the_heading():
    """PetroChina prints both attributions after the total comprehensive income, each pair under a
    heading naming its total — the nearest total printed before the row is the wrong one."""
    doc = _doc([
        _row("PROFIT FOR THE YEAR", "183755", "180563"),
        _row("TOTAL COMPREHENSIVE INCOME FOR THE YEAR", "171591", "182577"),
        _row("Owners of the Company", "164684", "161416",
             banner="PROFIT FOR THE YEAR ATTRIBUTABLE TO:"),
        _row("Owners of the Company", "152660", "161896",
             banner="TOTAL COMPREHENSIVE INCOME FOR THE YEAR ATTRIBUTABLE TO:"),
    ])
    heads = [h for c, h in _sent(doc) if c == "Owners of the Company"]
    assert heads == ["PROFIT FOR THE YEAR ATTRIBUTABLE TO:",
                     "TOTAL COMPREHENSIVE INCOME FOR THE YEAR ATTRIBUTABLE TO:"]


def test_a_sub_heading_carried_past_the_total_is_replaced():
    """BYD: the second owners' row arrived under the OCI banner and sub-heading above it."""
    doc = _doc([
        _row("Attributable to owners of the parent 母公司擁有人應佔", "4265638", "4041374",
             banner="PROFIT FOR THE YEAR 年度溢利"),
        _row("TOTAL COMPREHENSIVE INCOME FOR THE YEAR 本年度綜合收益總額", "4283499", "4067099",
             page=6, banner="subsequent periods:", sub="Receivables financing:"),
        _row("Attributable to owners of the parent 母公司擁有人應佔", "4283499", "4067099", page=6,
             banner="subsequent periods:", sub="Receivables financing:"),
    ])
    heads = [h for c, h in _sent(doc) if c.startswith("Attributable to owners")]
    assert heads == ["PROFIT FOR THE YEAR 年度溢利",
                     "TOTAL COMPREHENSIVE INCOME FOR THE YEAR 本年度綜合收益總額 — attributable to:"]


def test_a_caption_that_names_its_total_is_left_alone():
    """CAS tells the two apart in the caption, and earnings per share is not a share of a total."""
    doc = _doc([
        _row("五、净利润", "100", "90"),
        _row("1.归属于母公司股东的净利润", "80", "70", sub="（二）按所有权归属分类"),
        _row("2.少数股东损益", "20", "20", sub="（二）按所有权归属分类"),
        _row("BASIC EARNINGS PER SHARE ATTRIBUTABLE TO OWNERS OF THE COMPANY", "1", "1"),
    ])
    sent = dict(_sent(doc))
    assert sent["1.归属于母公司股东的净利润"] == "（二）按所有权归属分类"
    assert sent["BASIC EARNINGS PER SHARE ATTRIBUTABLE TO OWNERS OF THE COMPANY"] is None


def test_a_citation_names_the_pair_by_its_group():
    doc = _lai_sun()
    face = face_context.face_index(doc)

    def cite(group=""):
        resolved, unresolved = resolve_sources(
            [SourceRef(statement=PL, caption="Owners of the Company", group=group)], [], face)
        assert not unresolved
        return resolved[0]["figures"]["current"]

    assert cite("TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR — Attributable to:") == "-1562216"
    assert cite("LOSS FOR THE YEAR — Attributable to:") == "-1675589"
    assert cite() == "-1675589"      # no group: document order, as before


def test_the_contract_says_a_statement_row_is_named_by_its_subheading():
    assert "Where a statement row carries a `subheading`" in REPLY_CONTRACT
    assert '`column` "share_counts"' in REPLY_CONTRACT


# --- share counts printed in a caption ------------------------------------------------------------

BYD_CAPTION = ("Issued and fully paid ordinary shares 已發行及繳足 2,253,204,500 (2023: 2,253,204,500) "
               "2,253,204,500股（二零二三年：2,253,204,500股）普通股")


def _share_note(caption):
    item = NoteItem(raw_label=caption)
    for label in ("current", "prior"):
        item.set_value(ExtractedValue(value_raw=Decimal("4052228"), value=Decimal("4052228"),
                                      basis=Basis.CONSOLIDATED, period_label=label))
    return [NotesTable(note_number="30", title="SHARE CAPITAL", items=[item])]


def test_the_counts_in_a_caption_are_read_with_their_comparative():
    assert note_context.caption_share_counts(BYD_CAPTION) == {
        "current": "2253204500", "prior": "2253204500"}
    assert note_context.caption_share_counts(
        "720,429,301 (2023: 700,000,000) ordinary shares") == {
        "current": "720429301", "prior": "700000000"}
    assert note_context.caption_share_counts("Balance at 1 January 2023") is None
    assert note_context.caption_share_counts("Loans 1,000 (2023: 2,000)") is None   # not shares


def test_the_request_carries_the_counts_beside_the_amount():
    items = [LineItemDef(key="k", label="k", note_source=NoteSource(note_title_any=["SHARE CAPITAL"]))]
    row = note_context.identified_notes(LineItemSet(items=items), _share_note(BYD_CAPTION))[0]["rows"][0]
    assert row["figures"] == {"current": "4052228", "prior": "4052228"}
    assert row["share_counts"] == {"current": "2253204500", "prior": "2253204500"}
    assert "number of shares" in row["share_counts_are"]


def test_a_citation_of_the_share_counts_resolves_to_both_years():
    notes = _share_note(BYD_CAPTION)
    resolved, unresolved = resolve_sources(
        [SourceRef(note="30", caption=BYD_CAPTION, column="share_counts")], notes)
    assert not unresolved
    assert resolved[0]["figures"] == {"current": "2253204500", "prior": "2253204500"}
    # the whole row is still the amount
    resolved, _ = resolve_sources([SourceRef(note="30", caption=BYD_CAPTION)], notes)
    assert resolved[0]["figures"]["current"] == "4052228"


def test_a_cash_flow_row_about_non_controlling_interests_gets_no_heading():
    """PetroChina's financing section: dividends paid to and contributions from non-controlling
    interests follow the cash flow's own rows, and are not a share of the profit for the year."""
    rows = [_row("Profit for the year", "100", "90"),
            _row("Dividends paid to non-controlling interests", "-5", "-4",
                 banner="CASH FLOWS FROM FINANCING ACTIVITIES")]
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=5, kind=PageKind.FACE, statement="cash_flow")]
    doc.line_items = rows
    sent = face_context.face_rows(doc, {"cash_flow"})[0]["rows"]
    assert all("subheading" not in r for r in sent)


# --- a statement row named by the section it is printed under -----------------------------------

BS = "balance_sheet"


def _laurus():
    """Laurus Labs prints "Financial liabilities Borrowings" under both liabilities sections."""
    doc = _doc([
        _row("Financial liabilities Borrowings", "611.34", "645.58", banner="Non-current liabilities"),
        _row("Total non-current liabilities", "1444.40", "1193.33", banner="Non-current liabilities"),
        _row("Financial liabilities Borrowings", "1786.15", "2047.50", banner="Current liabilities"),
    ], pages=(5,))
    doc.pages = [PageSource(index=5, kind=PageKind.FACE, statement=BS)]
    return doc


def test_a_citation_names_a_statement_row_by_the_section_it_is_under():
    """"Current liabilities" is inside "Non-current liabilities", so a containment match alone
    took the non-current row, printed first, for a citation naming the current section."""
    face = face_context.face_index(_laurus())

    def cite(group=""):
        # The row is shown by its own caption, "Borrowings", under its "Financial liabilities"
        # heading (`face_context.shown_caption`); a citation copies what it was shown.
        resolved, unresolved = resolve_sources(
            [SourceRef(statement=BS, caption="Borrowings", group=group)], [], face)
        assert not unresolved
        return resolved[0]["figures"]["current"]

    assert cite("Current liabilities") == "1786.15"
    assert cite("Non-current liabilities") == "611.34"
    assert cite() == "611.34"        # no group: document order, as before


def test_the_request_sends_the_section_a_citation_can_name():
    rows = face_context.face_rows(_laurus(), {BS})[0]["rows"]
    assert [r.get("under") for r in rows if r["caption"].endswith("Borrowings")] == [
        "Non-current liabilities", "Current liabilities"]


def test_the_contract_says_a_statement_row_is_named_by_its_under():
    from app.services.line_item_llm import FACE_ONLY_REPLY_CONTRACT
    for contract in (REPLY_CONTRACT, FACE_ONLY_REPLY_CONTRACT):
        assert "give its `under` in `group`" in contract
    assert "a statement row's `under` or `subheading`" in SourceRef.model_fields["group"].description
