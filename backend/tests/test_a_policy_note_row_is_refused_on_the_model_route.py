"""A model citation of an accounting-policy note's ROW is refused, by the rule the rules reader uses.

The finding (fix_matrix:policy_note). An accounting-policy note states how a balance is measured,
not what it is: 688411 海博思创 2024 五、11「金融资产转移的确认依据及计量方法」 ends a line of prose
on the list marker "(3)", which the reader took for a row of −3 and the rules published as current
FVTOCI financial assets — until `line_item_notes.is_policy_note` kept such notes out of the rules'
reading. Only the rules: under `llm_document_context = "full"` the report carries the policy
chapter and the note fence is off, so a model citing that row was accepted. It is now refused
(`services.answer_checks.refuse_policy_notes`).

As narrow as the rules' rule: a TABLE ROW only (the rules' prose reader reads every note a line
covers, policies included, and a US "Summary of significant accounting policies" states real
amounts in its sentences), and not where the line's own `note_title_any` names the note.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import answer_checks
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")
FVTOCI = "sub__fa_cp_fvtoci_note_total"
MARKER = "利终止；(2)该金融资产已转移，且将金融资产所有权上几乎所有的风险和报酬转移给转入方；"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(autouse=True)
def _restore_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider,
           ex.llm_document_context)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider,
         ex.llm_document_context) = was


def _row(caption, amount, number):
    row = NoteItem(raw_label=caption, note_number=number)
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=60))
    return row


def _policy(number="五、11", title="金融资产转移的确认依据及计量方法",
            chapter="重要会计政策及会计估计"):
    return NotesTable(note_number=number, title=title, chapter_title=chapter,
                      items=[_row(MARKER, "-3", number)],
                      source_text="(3) 该金融资产已转移 … 金额 1,000,000.00 元")


def _doc(notes):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=3, kind=PageKind.FACE, statement="balance_sheet")]
    doc.notes = list(notes)
    li = LineItem(source_label="Profit before tax", canonical_key="pl_profit_before_tax")
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("1000"), provenance=Provenance(page_index=3))
    doc.line_items = [li]
    return doc


def _run(shipped, doc, key, sources, context="full"):
    st = get_settings()
    ctx = PipelineContext(raw_bytes=b"", settings=st)
    ctx.line_items, ctx.ontology = shipped, build_working_view(shipped)
    ex = st.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, [key]
    ex.llm_document_context = context

    class _P:
        id = "policy"

        def complete_structured(self, *, system, messages, response_schema, **_):
            return response_schema.model_validate({"answers": [{
                "key": key, "confidence": 0.9, "reason": "r", "sources": sources}]}), {}

    ctx.registry.register("llm", "policy", lambda: _P())
    st.llm.provider = "policy"
    LineItemLlmStage().run(doc, ctx)
    row = next((r for r in doc.line_items if r.canonical_key == key), None)
    return row, ctx


def _figures(row):
    return [ev.value for ev in (row.values or {}).values() if ev.value is not None] if row else []


def test_the_688411_marker_cited_from_the_policy_chapter_is_refused_under_full(shipped):
    row, ctx = _run(shipped, _doc([_policy()]), FVTOCI,
                    [{"note": "五、11", "caption": MARKER}])
    assert _figures(row) == []
    assert any(f"{FVTOCI}: citation REFUSED note='五、11'" in m
               and "accounting-policy note" in m for m in ctx.logs), ctx.logs[-6:]


def test_without_the_rule_the_same_answer_published_minus_three(shipped, monkeypatch):
    """The control: the citation resolves and reaches publication, so the test above is about the
    rule, not about a citation that never resolved."""
    monkeypatch.setattr(answer_checks, "refuse_policy_notes", lambda item, e, index: (e, []))
    row, _ctx = _run(shipped, _doc([_policy()]), FVTOCI, [{"note": "五、11", "caption": MARKER}])
    assert _figures(row) == [Decimal("-3")]


def test_the_hkfrs_policies_note_is_a_policy_note_too(shipped):
    policies = _policy(number="3", title="MATERIAL ACCOUNTING POLICIES 重大會計政策", chapter="")
    index = answer_checks.NoteIndex([policies])
    entry = {"at": 0, "note": "3", "caption": MARKER, "figures": {"current": "-3"}}
    kept, refused = answer_checks.refuse_policy_notes(SimpleNamespace(note_source=None), [entry],
                                                      index)
    assert kept == [] and refused[0]["refused"] == "policy_note"


def test_a_balance_note_row_and_a_policy_sentence_are_kept():
    """A row of a balance note is a balance; a SENTENCE of a policy note is left to the answer, as
    the rules' prose reader leaves it."""
    balance = NotesTable(note_number="七、18", title="、其他权益工具投资",
                         chapter_title="合并财务报表项目注释",
                         items=[_row("非上市股权投资", "5000", "七、18")])
    index = answer_checks.NoteIndex([_policy(), balance])
    row = {"at": 0, "note": "七、18", "caption": "非上市股权投资", "figures": {"current": "5000"}}
    sentence = {"at": 1, "note": "五、11", "caption": "", "prose": True,
                "figures": {"prose": "1000000.00"}}
    item = SimpleNamespace(note_source=None)
    assert answer_checks.refuse_policy_notes(item, [row, sentence], index) == ([row, sentence], [])


def test_a_line_whose_own_title_patterns_name_the_note_reads_it():
    """The rules route reads a policy note for a line whose author pointed it there; so does the
    model route. No shipped line does."""
    index = answer_checks.NoteIndex([_policy()])
    entry = {"at": 0, "note": "五、11", "caption": MARKER, "figures": {"current": "-3"}}
    named = SimpleNamespace(note_source=SimpleNamespace(note_title_any=[r"金融资产转移"]))
    assert answer_checks.refuse_policy_notes(named, [entry], index) == ([entry], [])


def test_no_shipped_line_names_a_policy_note(shipped):
    """So the exemption above changes nothing on the shipped set."""
    probes = [_policy(), _policy(number="3", title="MATERIAL ACCOUNTING POLICIES", chapter=""),
              _policy(number="1", title="Summary of significant accounting policies",
                      chapter="")]
    entry = lambda n: {"at": 0, "note": n, "caption": MARKER, "figures": {"current": "-3"}}  # noqa: E731
    for item in shipped.items:
        if not getattr(item, "note_source", None):
            continue
        for table in probes:
            kept, refused = answer_checks.refuse_policy_notes(
                item, [entry(table.note_number)], answer_checks.NoteIndex([table]))
            assert refused, (item.key, table.title)
