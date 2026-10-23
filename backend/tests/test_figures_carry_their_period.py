"""Every figure a request sends carries its period, and a fragment of a sentence is not a figure.

Seen on the stand-in captures of the published filings:

* CAS movement schedules (澜起 七、25, 600990 七、25 …) print "一、账面原值 1.期初余额 2.本期增加金额 …
  4.期末余额 二、累计折旧 1.期初余额 …" with no date: every row went out `period: unstated`, and both
  blocks were grouped "1." — the enumerator of "1.期初余额".
* 000883 七、61 prints "本期发生额 · 收入 | 上期发生额 · 收入": columns that name their period in words.
* Far East Horizon note 47 prints its fair value hierarchy twice, under "As at 31 December 2024"
  and "As at 31 December 2023", with the same captions; hkex_0160 note 13 prints "At 31 March
  2024:" over its cost and depreciation rows, and "Total" over three columns.
* Fragments read as figures: "Corporate Bonds (Phase Two) have been fully redeemed on" 30, BYD's
  "2023 and the circular of the Company dated" 25 (mapped to a cash flow line), "At 31 December
  2023 and" 1 / 2024, a hierarchy header "Level" 1 2 3, "… are set out in note" 9.
* PetroChina prints its statements under CAS and under IFRS; the two sets were one block.
"""
from __future__ import annotations

import json
from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.services import face_context, note_columns
from app.services.row_reconstruct import Word, build_line_items, cut_sentence_figures
from app.stages.classify import statement_frameworks


# ── fragments ───────────────────────────────────────────────────────────────────────────────

def _d(*xs):
    return [Decimal(str(x)) for x in xs]


def test_a_sentence_cut_at_a_date_or_a_note_reference_is_not_a_row():
    assert cut_sentence_figures("Corporate Bonds (Phase Two) have been fully redeemed on", _d(30))
    assert cut_sentence_figures("2023 and the circular of the Company dated", _d(25))
    assert cut_sentence_figures("At 31 December 2023 and", _d(1, 2024))
    assert cut_sentence_figures("At 31 March 2024:", _d(31))
    assert cut_sentence_figures("Level", _d(1, 2, 3))
    assert cut_sentence_figures("(b) In the opinion of the directors … are set out in note 董事認為",
                                _d(9))


def test_a_caption_that_is_whole_keeps_its_figures():
    assert not cut_sentence_figures("Balance at 1 January", _d(1234))      # a real amount
    assert not cut_sentence_figures("Dividend income", _d(30, 18))         # no cut
    assert not cut_sentence_figures("Other income", _d(25))
    assert not cut_sentence_figures("Trade receivables", _d(1, 2))         # two words, not a header
    assert not cut_sentence_figures("Interest paid on", _d("25.50"))      # not a day
    assert not cut_sentence_figures("Amount due to", _d(-25))             # signed: a figure


H = 0.009


def _words(rows):
    return [Word(text=t, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H)) for t, x0, x1, y in rows]


def test_the_reader_drops_the_cut_sentence_and_keeps_the_row_beside_it():
    rows = [("Bank", 0.10, 0.14, 0.30), ("borrowings", 0.15, 0.24, 0.30),
            ("1,234", 0.70, 0.75, 0.30), ("1,000", 0.85, 0.90, 0.30),
            ("have", 0.10, 0.14, 0.32), ("been", 0.15, 0.19, 0.32), ("fully", 0.20, 0.24, 0.32),
            ("redeemed", 0.25, 0.33, 0.32), ("on", 0.34, 0.36, 0.32), ("30", 0.73, 0.75, 0.32)]
    items, _ = build_line_items(_words(rows), page_index=3, document_id="d", source_kind="native")
    assert [li.source_label for li in items] == ["Bank borrowings"]


# ── CAS movement schedule ───────────────────────────────────────────────────────────────────

def _cas_cell(amount, heading, idx):
    return ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                          basis=Basis.CONSOLIDATED, period_label=heading, period_display=heading,
                          column_index=idx, column_heading=heading,
                          provenance=Provenance(page_index=5))


def _cas_row(caption, amount):
    row = NoteItem(raw_label=caption)
    row.set_value(_cas_cell(amount, "房屋及建筑物", 0))
    return row


CAS_TEXT = ("项目 房屋及建筑物 一、账面原值 1.期初余额 100.00 2.本期增加金额 20.00 4.期末余额 120.00 "
            "二、累计折旧 1.期初余额 40.00 2.本期增加金额 10.00 (1)计提 10.00 4.期末余额 50.00")


def _cas_table():
    return NotesTable(note_number="七、25", title="使用权资产", source_text=CAS_TEXT, items=[
        _cas_row("1.期初余额", "100"), _cas_row("2.本期增加金额", "20"), _cas_row("4.期末余额", "120"),
        _cas_row("1.期初余额", "40"), _cas_row("2.本期增加金额", "10"), _cas_row("(1)计提", "10"),
        _cas_row("4.期末余额", "50")])


def test_a_cas_movement_schedule_states_its_section_and_the_period_its_words_name():
    table = _cas_table()
    lay = note_columns.table_layout(table)
    got = [(r.raw_label, lay[id(r)]["group"], lay[id(r)]["period"]) for r in table.items]
    assert got == [("1.期初余额", "一、账面原值", "prior"),
                   ("2.本期增加金额", "一、账面原值", "current"),
                   ("4.期末余额", "一、账面原值", "current"),
                   ("1.期初余额", "二、累计折旧", "prior"),
                   ("2.本期增加金额", "二、累计折旧", "current"),
                   ("(1)计提", "二、累计折旧", "current"),
                   ("4.期末余额", "二、累计折旧", "current")]


def test_relative_slot_reads_the_mainland_period_words():
    assert note_columns.relative_slot("4.期末余额") == ("current", "期末")
    assert note_columns.relative_slot("上期发生额 · 收入") == ("prior", "上期")
    assert note_columns.relative_slot("本期期初") == ("", "")
    assert note_columns.relative_slot("Additions") == ("", "")


# ── columns that name their period in words ─────────────────────────────────────────────────

def _headed(key, amount, heading, x):
    return ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                          basis=Basis.CONSOLIDATED, period_label=key, column_heading=heading,
                          provenance=Provenance(page_index=5, value_bbox=BBox(
                              x0=x - 0.05, y0=0.5, x1=x, y1=0.51)))


def test_columns_that_name_their_period_in_words_send_each_figures_period():
    from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
    from app.services import note_context

    row = NoteItem(raw_label="主营业务")
    for key, amount, heading, x in (("current", "1983", "本期发生额 · 收入", 0.4),
                                    ("current:cost", "1494", "本期发生额 · 成本", 0.55),
                                    ("prior", "1853", "上期发生额 · 收入", 0.7),
                                    ("prior:cost", "1489", "上期发生额 · 成本", 0.85)):
        row.set_value(_headed(key, amount, heading, x))
    table = NotesTable(note_number="七、61", title="营业收入和营业成本", items=[row],
                       source_text="本期发生额 上期发生额 项目 收入 成本 收入 成本 主营业务 1,983 1,494 1,853 1,489")
    items = [LineItemDef(key="k", label="k", note_source=NoteSource(note_title_any=[".*"]))]
    sent = note_context.identified_notes(LineItemSet(items=items), [table])[0]["rows"][0]
    assert sent["period"] == note_columns.PER_COLUMN
    assert sent["periods"] == {"本期发生额 · 收入": "current", "本期发生额 · 成本": "current",
                               "上期发生额 · 收入": "prior", "上期发生额 · 成本": "prior"}


# ── a table printed once per year under a dated title ───────────────────────────────────────

def _level_row(caption, amount):
    row = NoteItem(raw_label=caption)
    row.set_value(_cas_cell(amount, "Level 3", 2))
    return row


def test_a_table_under_its_own_as_at_title_takes_that_date():
    table = NotesTable(
        note_number="47", title="FAIR VALUE HIERARCHY",
        source_text=("Assets measured at fair value As at 31 December 2023 Level 1 Level 2 Level 3 "
                     "Financial assets at fair value through profit or loss 3,753,048 "
                     "Carrying amount at 31 December 2024 5,000"),
        items=[_level_row("Financial assets at fair value through profit or loss", "3753048"),
               _level_row("Carrying amount at 31 December 2024", "5000")])
    lay = note_columns.table_layout(table, newest=(2024, 12, 31))
    first = lay[id(table.items[0])]
    assert (first["period"], first["period_text"]) == ("prior", "as at 31 December 2023")
    # A sentence is not a title.
    assert not note_columns._AS_AT_TITLE.search("the fair values as at 31 December 2024 are")
    assert not note_columns._AS_AT_TITLE.search("As at 31 December 2024, the Group had")


# ── a heading printed over several columns ──────────────────────────────────────────────────

def test_a_heading_printed_twice_is_two_keys_and_its_bare_name_is_refused():
    row = NoteItem(raw_label="Cost")
    for k, (amount, heading, x) in enumerate((("817", "Buildings", 0.3), ("2826", "Total", 0.4),
                                              ("723", "Buildings", 0.5), ("4330", "Total", 0.9))):
        ev = _headed(f"c{k}", amount, heading, x)
        ev.column_index = k
        row.set_value(ev)
    table = NotesTable(note_number="13", title="PPE", items=[row],
                       source_text="At 31 March 2024: Cost 817 2,826 723 4,330")
    lay = note_columns.table_layout(table, newest=(2024, 3, 31))[id(row)]
    assert sorted(lay["keys"].values()) == ["Buildings", "Buildings (2)", "Total", "Total (2)"]
    assert lay["period"] == "current"                          # "At 31 March 2024:" over it
    assert note_columns.pick_laid_out(row, lay, "Total (2)")["figures"] == {"current": "4330"}
    refused = note_columns.pick_laid_out(row, lay, "Total")
    assert refused["figures"] == {} and "names 2 columns" in refused["why"]


# ── two statement sets ──────────────────────────────────────────────────────────────────────

def _face(i, statement):
    return PageSource(index=i, kind=PageKind.FACE, statement=statement)


def test_a_filing_printing_cas_and_ifrs_statements_tags_each_set():
    pages = [PageSource(index=0), _face(1, "balance_sheet"), PageSource(index=2),
             _face(3, "balance_sheet")]
    texts = ["prepared in accordance with Accounting Standards for Business Enterprises", "",
             "in accordance with IFRS Accounting Standards", ""]
    assert statement_frameworks(pages, texts) == {1: "CAS", 3: "IFRS"}
    # One set only: nothing to keep apart.
    assert statement_frameworks(pages[:2], texts[:2]) == {}


def _li(caption, page, value, key=None):
    li = LineItem(source_label=caption, canonical_key=key)
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal(value), value_raw=Decimal(value),
                                    provenance=Provenance(page_index=page))
    return li


def test_statement_lines_read_the_ifrs_set_and_the_cas_set_is_kept_apart():
    # A statement outside `statement_sets.ONE_SET_STATEMENTS` (the equity statement): its CAS set
    # is still sent, apart. The balance sheet's is not sent at all — see
    # `test_one_statement_set_in_requests`.
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [_face(10, "changes_in_equity"), _face(20, "changes_in_equity")]
    doc.pages[0].evidence["framework"] = "CAS"
    doc.pages[1].evidence["framework"] = "IFRS"
    doc.line_items = [_li("Share capital", 10, "183021", "bs_equity__common_share_capital"),
                      _li("Share capital", 20, "183021", "bs_equity__common_share_capital")]
    blocks = face_context.face_rows(doc, {"changes_in_equity"})
    assert [(b["set"], "kept_apart" in b) for b in blocks] == [("IFRS", False), ("CAS", True)]
    assert blocks[0]["rows"][0]["line"] == "bs_equity__common_share_capital"
    assert "line" not in blocks[1]["rows"][0]             # no CAS row is proposed for a line
    assert "IFRS" in blocks[1]["kept_apart"]
    # On the balance sheet the CAS set is not sent.
    for page in doc.pages:
        page.statement = "balance_sheet"
    blocks = face_context.face_rows(doc, {"balance_sheet"})
    assert len(blocks) == 1 and "kept_apart" not in blocks[0]
    assert [r["figures"] for r in blocks[0]["rows"]] == [{"current": "183021"}]


# ── lint calibration ────────────────────────────────────────────────────────────────────────

def test_lint_reads_a_net_caption_and_a_heading_named_period_as_whole():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "llm_standin.py"
    spec = importlib.util.spec_from_file_location("llm_standin_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.is_fragment("Other operating expenses, net") is None
    assert mod.is_fragment("Interest income net of") is not None
    assert mod._key_names_period("期末余额") and mod._key_names_period("current:net")
    assert mod._key_names_period("31 December 2024") and not mod._key_names_period("Level 3")
    rows = [{"caption": "Revenue", "figures": {"current": "10", "prior": "9"}},
            {"caption": "Advances", "figures": {"prior": "-3200"}}]

    class Run:
        @staticmethod
        def source_of(cap, figs, statement=""):
            x = 0.9 if "prior" in figs and "current" not in figs else 0.7
            vals = [{"period_label": "current", "provenance": {"bbox": {"x1": 0.7}}},
                    {"period_label": "prior", "provenance": {"bbox": {"x1": 0.9}}}]
            return {"values": vals}, {"provenance": {"bbox": {"x1": x}}}

    px = mod._prior_column_x(rows, Run, "cash_flow")
    assert px == 0.9 and mod._in_prior_column("Advances", rows[1]["figures"], Run, "", px)
    assert json.dumps(rows)  # the rows are the request's own shape
