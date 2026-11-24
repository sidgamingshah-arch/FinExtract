"""One set of checks for every model answer — `services.answer_checks`.

The refusals on a model's citations were written inside `stages.line_item_llm`, so only that
stage's answers passed through them, and a second route that asks a model for figures (the whole
PDF) had none. They now live in one module that works on a documented CITATION ENTRY, and
`check_answer` applies them in one order. What this file measures:

  * the stage's old names ARE the shared checks, so the stage's own answers are judged as before;
  * `check_answer` applies the rules in the documented order and hands back what it kept, what it
    refused and the log lines the stage writes, in the stage's own words;
  * the repeated-citation tie-break is the shared `take_tied_row`, and a sum that still adds ONE
    printed figure twice — no other tied row left to give it — counts it once;
  * the rules route reaches none of it.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, PrintedIn
from app.core.models.geometry import BBox
from app.core.models.line_item import (
    ExtractedValue,
    LineItem,
    NoteItem,
    NotesTable,
    Provenance,
    UnitContext,
)
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import answer_checks
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
SECURITIES = "sub__fa_cp_investment_and_money_market_securities_note_total"


@pytest.fixture(scope="module")
def by_key():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in st.items}


def _face_doc(notes=()):
    """A CNY filing whose face prints in 元, with the notes given."""
    doc = DocumentModel(unit_context=UnitContext(currency="CNY"))
    li = LineItem(source_label="营业收入", canonical_key="x", printed_in=PrintedIn.FACE)
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("1603146551.95"),
                                    unit_ctx=UnitContext(currency="CNY", scale_factor=Decimal(1),
                                                         units_label="元"))
    doc.line_items.append(li)
    doc.notes = list(notes)
    return doc


def _boxed_row(caption, figure, y, *, number="12", page=40, group=""):
    row = NoteItem(raw_label=caption, note_number=number, group_hint=group)
    row.set_value(ExtractedValue(
        value=Decimal(figure), basis=Basis.CONSOLIDATED, period_label="current",
        provenance=Provenance(source_kind="native", page_index=page,
                              bbox=BBox(x0=0.80, y0=y, x1=0.88, y1=y + 0.012))))
    return row


def _cited(row, at, number="12"):
    """The entry `resolve_sources` returns for a whole-row citation of `row`."""
    ev = next(iter(row.values.values()))
    return {"at": at, "note": number, "title": "", "caption": row.raw_label,
            "figures": {"current": str(ev.value)}, "basis": "consolidated",
            "provenance": ev.provenance.model_dump(mode="json")}


# ── the stage's names are the shared checks ──────────────────────────────────────────────────

def test_the_stages_names_are_the_shared_checks(by_key):
    """The stage keeps its old names for the callers that reach them there, and each gives exactly
    what the shared check gives — so the stage's own route is judged as it was."""
    from app.stages import line_item_llm as stage

    item = by_key[SECURITIES]
    swap = {"at": 0, "note": "22", "title": "Derivative financial instruments",
            "caption": "Interest rate swaps", "figures": {"current": "950000"}}
    assert stage._refuse_derivative_citations(item, [swap]) == answer_checks.refuse_derivatives(
        item, [swap])
    assert stage._derivative_vetoes(item) and len(stage._derivative_vetoes(item)) == len(
        answer_checks.derivative_vetoes(item))
    face = {"at": 0, "statement": "profit_and_loss", "caption": "一、营业收入", "on_face": True,
            "row_id": "r0", "figures": {"current": "1"}}
    revenue = by_key["sub__face_principal_revenue"]
    assert stage._refuse_excluded_face_citations(revenue, [face]) == (
        answer_checks.refuse_excluded_face(revenue, [face]))
    dollars = {"at": 0, "note": "十六、2", "prose": True, "figures": {"prose": "6568662.00"},
               "prose_currency": "USD"}
    doc = _face_doc()
    assert stage._refuse_foreign_currency_prose(doc, [dollars]) == (
        answer_checks.refuse_foreign_currency(doc, [dollars]))


def test_check_answer_refuses_in_order_and_hands_back_the_stages_log_lines(by_key):
    """One answer with a swap, a dollar sentence and a 万元 note row: the swap and the dollars are
    refused with no figure and their own position, the 万元 row is kept in 元, and the log lines
    are the stage's — the rescale first, then each refusal."""
    wan = {"currency": "CNY", "scale_factor": "10000", "units_label": "万元"}
    swap = {"at": 0, "note": "22", "title": "Derivative financial instruments",
            "caption": "Interest rate swaps", "figures": {"current": "950000"}}
    dollars = {"at": 1, "note": "十六、2", "caption": "", "prose": True,
               "figures": {"prose": "6568662.00"}, "prose_currency": "USD"}
    wan_row = {"at": 2, "note": "十四、5", "caption": "关键管理人员报酬", "basis": "consolidated",
               "figures": {"current": "476.37"}, "unit": wan}
    got = answer_checks.check_answer(by_key[SECURITIES], [swap, dollars, wan_row],
                                     doc=_face_doc(), signs=[1, 1, 1], component=True,
                                     key=SECURITIES)
    assert [r["refused"] for r in got.refused] == ["derivative", "foreign_currency"]
    assert [r["at"] for r in got.refused] == [0, 1]
    assert all("figures" not in r and "amount" not in r for r in got.refused)
    assert len(got.kept) == 1 and Decimal(got.kept[0]["figures"]["current"]) == Decimal("4763700")
    assert got.log[0].startswith(f"line_item_llm:{SECURITIES}: note '十四、5' row ")
    assert "put into the statement's scale" in got.log[0]
    assert got.log[1] == (f"line_item_llm:{SECURITIES}: citation REFUSED note='22' "
                          f"caption='Interest rate swaps' (this line excludes derivatives, and "
                          f"the cited row is one)")
    assert got.log[2].startswith(f"line_item_llm:{SECURITIES}: citation REFUSED note='十六、2'")
    assert list(answer_checks.ORDER) == [
        "derivative", "policy_note", "breakdown_of_cited_total", "same_figure_twice",
        "excluded_caption", "foreign_currency", "statement_scale"]


def test_the_whole_answer_rule_judges_what_the_row_rules_leave(by_key):
    """The excluded-face rule refuses an answer made ONLY of vetoed face rows. It runs after the
    row-level refusals, so an answer of the face's 营业收入 plus a policy-note row — a pair that
    would otherwise pass as "not only face rows" — loses both."""
    policy = NotesTable(note_number="五、11", title="金融资产转移的确认依据及计量方法",
                        chapter_title="重要会计政策及会计估计")
    policy.items.append(NoteItem(raw_label="(3)", note_number="五、11"))
    policy.items[0].set_value(ExtractedValue(value=Decimal("-3"), basis=Basis.CONSOLIDATED,
                                             period_label="current"))
    face = {"at": 0, "statement": "profit_and_loss", "caption": "一、营业收入", "on_face": True,
            "row_id": "r0", "figures": {"current": "40617220.64"}}
    marker = {"at": 1, "note": "五、11", "caption": "(3)", "figures": {"current": "-3"}}
    got = answer_checks.check_answer(by_key["sub__face_principal_revenue"], [face, marker],
                                     doc=_face_doc([policy]))
    assert got.kept == []
    assert sorted(r["refused"] for r in got.refused) == ["excluded_caption", "policy_note"]


# ── the repeated-citation tie-break, and the sum that still repeats a figure ──────────────────

def test_an_identical_citation_takes_the_next_tied_row_and_stays_once_they_are_all_taken():
    rows = ["r0", "r1", "r2"]
    repeats: dict = {}
    said = ("十四、4", "", "", "", "技有限公司南京诚志永清能源科", "")
    taken = [answer_checks.take_tied_row(repeats, said, "r0", rows, ident=lambda r: r)
             for _ in range(3)]
    assert taken == ["r0", "r1", "r2"]
    assert answer_checks.take_tied_row(repeats, said, "r0", rows, ident=lambda r: r) == "r0"
    # Another citation — another column, say — is another key, and starts again.
    assert answer_checks.take_tied_row(repeats, said[:-1] + ("期初余额",), "r0", rows,
                                       ident=lambda r: r) == "r0"


def test_a_sum_citing_one_row_twice_counts_it_once():
    """A note prints ONE "Bank loans" row; an answer adding it twice resolves both citations to it
    (there is no other tied row to advance to), and the second is refused — the line is 500, not
    1,000. A whole answer, or the row taken with opposite signs, is left to the answer."""
    row = _boxed_row("Bank loans", "500", 0.30)
    table = NotesTable(note_number="12", title="BORROWINGS", items=[row])
    refs = [SourceRef(note="12", caption="Bank loans"), SourceRef(note="12", caption="Bank loans")]
    resolved, unresolved = resolve_sources(refs, [table])
    assert len(resolved) == 2 and not unresolved
    index = answer_checks.NoteIndex([table])
    kept, refused = answer_checks.refuse_repeated_figures(resolved, index, signs=[1, 1],
                                                          component=True)
    assert [e["at"] for e in kept] == [0] and [r["refused"] for r in refused] == [
        "same_figure_twice"]
    assert answer_checks.refuse_repeated_figures(resolved, index, signs=[1, 1],
                                                 component=False) == (resolved, [])
    assert answer_checks.refuse_repeated_figures(resolved, index, signs=[1, -1],
                                                 component=True) == (resolved, [])


def test_a_face_row_cited_twice_in_a_sum_counts_once():
    a = {"at": 0, "statement": "balance_sheet", "caption": "Cash", "on_face": True,
         "row_id": "f1", "figures": {"current": "10"}}
    b = dict(a, at=1)
    kept, refused = answer_checks.refuse_repeated_figures(
        [a, b], answer_checks.NoteIndex([]), signs=[1, 1], component=True)
    assert kept == [a] and refused[0]["at"] == 1


# ── the rules route reaches none of it ───────────────────────────────────────────────────────

def test_the_rules_route_reaches_none_of_the_model_checks(by_key, monkeypatch):
    """With every model check made to fail loudly, the rules' own reading of a 其中 note is what it
    was — 300319 七、2's parent read through its breakdown, 431,478,888.81 — and the model stage,
    switched off, returns before any check."""
    from app.config import get_settings
    from app.services.note_sourced import resolve, select_rows
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage

    def boom(*_a, **_k):
        raise AssertionError("a model check ran on the rules route")

    for name in ("check_answer", "settle_siblings", "footprint", "refuse_policy_notes",
                 "refuse_breakdowns_of_cited_totals", "refuse_repeated_figures"):
        monkeypatch.setattr(answer_checks, name, boom)
    rows = [
        _boxed_row("以公允价值计量且其变动计入当期损益的金融资产", "431478888.81", 0.30, number="七、2"),
        _boxed_row("结构性理财产品", "373011600.33", 0.32, number="七、2", group="其中："),
        _boxed_row("权益工具投资", "58467288.48", 0.34, number="七、2", group="其中："),
    ]
    table = NotesTable(note_number="七、2", title="、交易性金融资产", items=rows)
    got = resolve(select_rows(by_key["sub__fa_cp_fvtpl_note_total"], [table], {"current"}), "sum")
    assert {p: str(v) for (_b, p), (v, _t) in got.items()} == {"current": "431478888.81"}

    settings = get_settings()
    was = settings.extraction.llm_mapping
    try:
        settings.extraction.llm_mapping = False
        st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
        ctx = PipelineContext(raw_bytes=b"", settings=settings)
        ctx.line_items, ctx.ontology = st, build_working_view(st)
        doc = _face_doc([table])
        LineItemLlmStage().run(doc, ctx)
    finally:
        settings.extraction.llm_mapping = was
    assert any("line_item_llm:skipped(extraction.llm_mapping is off)" in m for m in ctx.logs)


def test_no_rules_module_imports_the_model_checks():
    """The checks are reached from the model stage only. The rules' modules reuse nothing of them:
    it is the other way round (`note_sourced.breakdown_groups`, `line_item_notes.is_policy_note`)."""
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    rules = ["stages/note_sourced.py", "services/line_item_notes.py", "stages/map_ontology.py",
             "stages/residual.py", "services/line_item_matching.py"]
    for path in rules:
        text = (root / path).read_text(encoding="utf-8")
        assert "answer_checks" not in text, path
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                   if "answer_checks" in p.read_text(encoding="utf-8")
                   and p.name != "answer_checks.py")
    # note_sourced.py names it in docstrings and reaches `take_tied_row` from `resolve_sources`,
    # which only the model route calls.
    assert set(users) <= {"stages/line_item_llm.py", "services/note_sourced.py",
                          "stages/whole_pdf.py", "services/whole_pdf.py",
                          "services/whole_pdf_rules.py", "services/whole_pdf_locate.py"}, users
