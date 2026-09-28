"""TEN LOGIC DEFECTS FROM A TEMPLATE-INDEPENDENT REVIEW OF THE ENGINE — one test group per defect.

NEW FILE -> backend/tests/test_engine_logic_review.py

Found by reading the computation path (`periods`, `rollups`, `line_items`, `formula`, `netting`),
note selection (`note_context`) and the LLM answer path (`services.note_sourced`,
`stages.line_item_llm`, `stages.note_sourced`) without reference to the shipped configuration.
Every defect below was reproduced before it was fixed, and NONE was pinned by the existing 3,445
tests — the suite passed unchanged with all ten fixes applied, which is why each gets its own.

MEASURED ACROSS THE FIVE REFERENCE FILINGS. Deterministic route: 0 figures and 0 structural
statuses moved on all five. LLM route (spy provider): 300319 moves most, and for a reason worth
stating — `_prose_basis` is STANDALONE there (the face carries 335 standalone values against 260
consolidated) while every note row the model cites is consolidated (1,630 against 0), so the whole
route filed the group's figures in the parent-company column; 102 standalone figures leave and 94
consolidated arrive. On kaming, 13 unrelated lines had all carried one prior figure, 3,639,000,
reached through the substring note match; they no longer resolve to it.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.core.stage import PipelineContext
from app.config import get_settings
from app.services import note_sourced as ns


def _row(caption: str, amount: str, *, basis=Basis.CONSOLIDATED, period="current",
         page: int | None = 9) -> NoteItem:
    row = NoteItem(raw_label=caption)
    row.values[f"{basis.value}:{period}"] = ExtractedValue(
        basis=basis, period_label=period, value=Decimal(amount), value_raw=Decimal(amount),
        provenance=(Provenance(page_index=page, text_snippet=caption) if page is not None else None))
    return row


def _table(number: str, *rows: NoteItem, title: str = "") -> NotesTable:
    return NotesTable(note_number=number, title=title or number, items=list(rows))


def _cite(note: str, caption: str, amount: str = ""):
    return SimpleNamespace(note=note, caption=caption, amount=amount, quote="", statement="",
                           page=None)


def _resolve(notes, *cites):
    resolved, unresolved = ns.resolve_sources(list(cites), notes)
    return resolved, unresolved


# ── 1. a cited note number is an identity, not a substring ────────────────────────────────────

def test_a_citation_of_note_12_does_not_read_note_1() -> None:
    """THE DEFECT: `want in got or got in want`, so "1" in "12" admitted note 1 — printed first,
    so its Total won."""
    notes = [_table("1", _row("Total", "111")), _table("12", _row("Total", "1212"))]

    resolved, _ = _resolve(notes, _cite("12", "Total"))

    assert resolved[0]["note"] == "12"
    assert resolved[0]["figures"] == {"current": "1212"}


def test_a_cas_note_does_not_absorb_the_ten_after_it() -> None:
    """七、1 used to accept every row of 七、10 to 七、19."""
    notes = [_table("七、10", _row("合计", "10")), _table("七、1", _row("合计", "1"))]

    resolved, _ = _resolve(notes, _cite("七、1", "合计"))

    assert resolved[0]["note"] == "七、1"


@pytest.mark.parametrize("want, got, same", [
    ("12", "1", False), ("1", "12", False), ("七、1", "七、10", False),
    ("9", "七、9", True), ("七、9", "9", True), ("七、9", "十九、9", False),
    ("Note 12", "12", True), ("附注七、9", "七、9", True), ("１２", "12", True),
])
def test_what_counts_as_the_same_note(want, got, same) -> None:
    assert ns.same_note(want, got) is same


def test_an_exact_number_is_preferred_over_a_half_match() -> None:
    """A bare "9" on a CAS filing names both 七、9 and 十九、9; where one note IS numbered "9"
    exactly, that is the one it named."""
    notes = [_table("七、9", _row("合计", "79")), _table("9", _row("合计", "9"))]

    resolved, _ = _resolve(notes, _cite("9", "合计"))

    assert resolved[0]["note"] == "9"


def test_the_prose_lookup_uses_the_same_identity() -> None:
    notes = [_table("七、10"), _table("七、1")]

    assert ns._note_by_number(notes, "七、1").note_number == "七、1"


# ── 2. an exact caption beats one that merely contains it ─────────────────────────────────────

def test_a_cited_total_is_not_read_off_the_gross_row_above_it() -> None:
    notes = [_table("七、5", _row("应收账款", "100"), _row("应收账款合计", "90"))]

    resolved, _ = _resolve(notes, _cite("七、5", "应收账款合计"))

    assert resolved[0]["caption"] == "应收账款合计"
    assert resolved[0]["figures"] == {"current": "90"}


def test_among_containments_the_closest_length_wins_not_the_first_printed() -> None:
    assert ns.best_caption("应收账款坏账准备", ["应收账款", "应收账款坏账准备合计"]) \
        == "应收账款坏账准备合计"
    assert ns.best_caption("合计", ["应收账款合计", "合计"]) == "合计"
    assert ns.best_caption("存货", ["货币资金"]) is None


# ── 3. a prose amount is a whole printed number ───────────────────────────────────────────────

@pytest.mark.parametrize("stated, text, found", [
    ("841,000", "depreciation of HK$529,841,000", None),        # digits of a longer number
    ("529,841,000", "depreciation of HK$529,841,000", Decimal("529841000")),
    ("529841000", "depreciation of HK$529,841,000", Decimal("529841000")),
    ("1,234.56", "an amount of RMB1,234.56 was", Decimal("1234.56")),   # was always refused
    ("529,841,000", "HK$529, 841,000 wrapped", Decimal("529841000")),
    ("2023529", "in 2023 529 units", None),                      # a plain space separates
    ("529,842,000", "HK$529,841,000", None),
])
def test_a_prose_amount_must_be_a_printed_number(stated, text, found) -> None:
    assert ns._amount_in_text(stated, text) == found


# ── 4. a row figure keeps the basis of its own column ─────────────────────────────────────────

def test_a_resolved_row_carries_its_own_basis() -> None:
    notes = [_table("十九、2", _row("合计", "500", basis=Basis.STANDALONE))]

    resolved, _ = _resolve(notes, _cite("十九、2", "合计"))

    assert resolved[0]["basis"] == "standalone"


def test_a_row_printing_both_bases_does_not_blend_them() -> None:
    """The flat `figures` was keyed by period alone, so the second basis overwrote the first."""
    row = _row("合计", "100")
    row.values["s"] = ExtractedValue(basis=Basis.STANDALONE, period_label="current",
                                     value=Decimal("40"), value_raw=Decimal("40"))
    resolved, _ = _resolve([_table("七、5", row)], _cite("七、5", "合计"))

    assert resolved[0]["basis"] == "consolidated"
    assert resolved[0]["figures"] == {"current": "100"}
    assert resolved[0]["figures_by_basis"]["standalone"] == {"current": "40"}


@pytest.fixture
def _restore_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = was


def test_the_stage_writes_a_company_only_row_into_the_company_column(_restore_settings) -> None:
    """THE STAGE HALF OF THE WIRE. The face is majority CONSOLIDATED, so `_prose_basis` says
    consolidated; the cited row is STANDALONE. The old stage filed it by `_prose_basis` and it
    landed in the group's column.

    NOT THE WHOLE WIRE, and this docstring used to say it was. The row here is built with a
    STANDALONE value by hand, and the real extractor never produced one: a company-only note's rows
    were tagged consolidated until `test_a_company_note_is_the_companys` fixed `notes_extract`,
    which is where the extractor half is pinned."""
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage
    from tests.spy_line_item_llm import SpyLineItemLlm

    seed = (Path(__file__).resolve().parent.parent / "app/sample/templates"
            / "output_csv_hk_line_items.json")
    shipped = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)
    key = "sub__ltp_other_fincl_assets_note_total"
    decoys = ["货币资金", "应收账款", "存货", "固定资产", "在建工程", "无形资产", "短期借款",
              "应付账款", "营业收入", "营业成本", "管理费用", "研发费用"]
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.notes = [_table("七、19", _row("非上市股权投资", "60", basis=Basis.STANDALONE),
                        _row("合计", "60", basis=Basis.STANDALONE), title="其他非流动金融资产")]
    doc.notes += [_table(f"七、{40 + i}", _row("合计", "1"), title=t) for i, t in enumerate(decoys)]
    doc.line_items = []
    for i in range(5):                          # a consolidated majority on the face
        li = LineItem(source_label=f"face {i}", canonical_key=f"bs_ca__face_{i}")
        li.values["c"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                        value=Decimal("1"), value_raw=Decimal("1"))
        doc.line_items.append(li)
    spy = SpyLineItemLlm(only={key}, cite={key: ("七、19", "合计")})
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [key]
    ctx.registry.register("llm", "spy", lambda: spy)
    ctx.settings.llm.provider = "spy"

    LineItemLlmStage().run(doc, ctx)

    written = [ev for li in doc.line_items if li.canonical_key == key
               for ev in (li.values or {}).values() if ev.value is not None]
    assert written, "the stage wrote nothing — the fixture no longer exercises the write"
    assert {str(getattr(ev.basis, "value", ev.basis)) for ev in written} == {"standalone"}


# ── 5. a parent is evaluated after a child that is itself a parent ────────────────────────────

def test_a_sum_parent_waits_for_its_child_parent() -> None:
    """`bs_ca__x` sorts before `sub__y`, declares no cascade, and `sub__y` is its child. The old
    order read only cascade rung terms, so it evaluated `bs_ca__x` first — without `sub__y`."""
    from app.stages.note_sourced import _in_dependency_order

    defs = {"bs_ca__x": SimpleNamespace(cascade=(), terms=(), parent=""),
            "sub__y": SimpleNamespace(cascade=(), terms=(), parent="bs_ca__x")}
    order = [k for k, _ in _in_dependency_order({"bs_ca__x": [], "sub__y": []}, defs)]

    assert order.index("sub__y") < order.index("bs_ca__x")


def test_a_terms_parent_waits_for_a_parent_its_terms_name() -> None:
    from app.stages.note_sourced import _in_dependency_order

    defs = {"a_outer": SimpleNamespace(cascade=(), parent="",
                                       terms=(SimpleNamespace(ref="z_inner"),)),
            "z_inner": SimpleNamespace(cascade=(), terms=(), parent="")}
    order = [k for k, _ in _in_dependency_order({"a_outer": [], "z_inner": []}, defs)]

    assert order == ["z_inner", "a_outer"]


# ── 6. the plain `rollup: sum` path counts one printed row once ───────────────────────────────

def _child(key: str, amount: str, *, page=181, caption="合计") -> LineItem:
    li = LineItem(source_label=key, canonical_key=key)
    li.values["c"] = ExtractedValue(
        basis=Basis.CONSOLIDATED, period_label="current", value=Decimal(amount),
        value_raw=Decimal(amount),
        provenance=Provenance(page_index=page, text_snippet=caption) if page is not None else None)
    return li


def _sum_parent(*kids) -> Decimal | None:
    from app.stages.note_sourced import _fill_parents

    parent_key = "bs_nca__p"
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.line_items = [row for _d, row in kids]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    defs = {parent_key: SimpleNamespace(key=parent_key, cascade=(), terms=(), parent="",
                                        label=parent_key)}
    for d, _row_ in kids:
        defs[d.key] = d
    _fill_parents({parent_key: list(kids)}, {r.canonical_key: r for r in doc.line_items}, doc, ctx,
                  {parent_key: "sum"}, defs)
    parent = next(li for li in doc.line_items if li.canonical_key == parent_key)
    return next((ev.value for ev in parent.values.values() if ev.value is not None), None)


def test_two_siblings_citing_one_printed_row_sum_it_once() -> None:
    """575,243,925.97 reached a sum parent as 1,150,487,851.94 on the model's route."""
    a = (SimpleNamespace(key="sub__a", parent="bs_nca__p"), _child("sub__a", "575243925.97"))
    b = (SimpleNamespace(key="sub__b", parent="bs_nca__p"), _child("sub__b", "575243925.97"))

    assert _sum_parent(a, b) == Decimal("575243925.97")


def test_two_siblings_reading_different_rows_still_add() -> None:
    a = (SimpleNamespace(key="sub__a", parent="bs_nca__p"), _child("sub__a", "100", caption="甲"))
    b = (SimpleNamespace(key="sub__b", parent="bs_nca__p"), _child("sub__b", "100", caption="乙"))

    assert _sum_parent(a, b) == Decimal("200")


def test_a_figure_that_cannot_be_identified_is_never_dropped_on_a_guess() -> None:
    a = (SimpleNamespace(key="sub__a", parent="bs_nca__p"), _child("sub__a", "100", page=None))
    b = (SimpleNamespace(key="sub__b", parent="bs_nca__p"), _child("sub__b", "100", page=None))

    assert _sum_parent(a, b) == Decimal("200")


# ── 7. the duplicate test does not depend on row order ────────────────────────────────────────

def _dict_row(amount, page):
    prov = {"page_index": page} if page is not None else None
    return {"canonical_key": "k", "source_label": "Share capital",
            "values": [{"basis": "consolidated", "period_label": "current", "value": amount,
                        "provenance": prov}]}


def test_a_duplicate_pair_counts_the_same_in_either_order() -> None:
    from app.services.periods import concept_value

    unpaged, paged = _dict_row(100, None), _dict_row(100, 5)

    assert concept_value([unpaged, paged], "consolidated", "current") \
        == concept_value([paged, unpaged], "consolidated", "current")


def test_a_second_printing_on_another_page_is_still_dropped() -> None:
    """The rule the fix must not lose: one fact on the balance sheet and again in the equity
    statement is counted once."""
    from app.services.periods import concept_value

    assert concept_value([_dict_row(14202, 5), _dict_row(14202, 9)],
                         "consolidated", "current") == 14202


# ── 8. netting reads a concept the way the grid does ──────────────────────────────────────────

def _flat(key, label, amount, period="current", **extra):
    return {"canonical_key": key, "source_label": label,
            "values": [{"basis": "consolidated", "period_label": period, "value": amount}],
            **extra}


def test_netting_subtracts_every_row_of_a_concept_not_the_first() -> None:
    from app.services.netting import compute_netting

    rows = [_flat("cos", "Cost of sales", "-18330"), _flat("admin", "Admin A", "-1000"),
            _flat("admin", "Admin B", "-710")]

    got = compute_netting(rows, [{"target_key": "cos", "subtract_keys": ["admin"]}])["cos"]

    assert Decimal(got["net"]) == Decimal("-16620")


def test_netting_reads_a_positionally_labelled_column() -> None:
    from app.services.netting import compute_netting

    sell = {"canonical_key": "sell", "source_label": "Selling",
            "values": [{"basis": "consolidated", "period_label": "col0", "value": "-5"},
                       {"basis": "consolidated", "period_label": "col1", "value": "-4"}]}
    got = compute_netting([_flat("cos", "Cost of sales", "-100"), sell],
                          [{"target_key": "cos", "subtract_keys": ["sell"]}])

    assert Decimal(got["cos"]["net"]) == Decimal("-95")


def test_netting_honours_an_analysts_edit() -> None:
    """TWO rows, the SECOND edited: an edit is the analyst's answer for the whole line and replaces
    the printed rows (`concept_value`). The old read took the first row's printed -1,000."""
    from app.services.netting import compute_netting

    printed = _flat("admin", "Admin (printed)", "-1000")
    edited = _flat("admin", "Admin", "-400", edited=True,
                   edited_slots=["consolidated/current"])
    got = compute_netting([_flat("cos", "Cost of sales", "-100"), printed, edited],
                          [{"target_key": "cos", "subtract_keys": ["admin"]}])

    assert Decimal(got["cos"]["net"]) == Decimal("300")


def test_netting_stays_exact_to_the_cent_guard() -> None:
    """A GUARD rather than a regression — the old read was exact too. It pins the choice of
    `concept_amount` over `concept_value`, whose float would have entered the subtraction."""
    from app.services.netting import compute_netting

    got = compute_netting([_flat("cos", "Cost of sales", "-12251621314.53"),
                           _flat("admin", "Admin", "-1710")],
                          [{"target_key": "cos", "subtract_keys": ["admin"]}])

    assert got["cos"]["net"] == "-12251619604.53"


# ── 9. ROUND with digits ──────────────────────────────────────────────────────────────────────

def test_round_to_two_places() -> None:
    from app.services import formula

    assert formula.evaluate("=ROUND(10/3, 2)", {}) == 3.33
    assert formula.evaluate("ROUND(7.6)", {}) == 8.0


def test_round_refuses_fractional_digits() -> None:
    from app.services import formula

    with pytest.raises(formula.FormulaError):
        formula.evaluate("ROUND(1, 1.5)", {})


# ── 10. only a bilingual heading gains a script projection ────────────────────────────────────

@pytest.mark.parametrize("heading, variants", [
    ("Leases (HKFRS 16) - lessee", ("Leases (HKFRS 16) - lessee",)),
    ("其他应收款（续）", ("其他应收款（续）",)),
    ("12. PROPERTY, PLANT AND EQUIPMENT",
     ("12. PROPERTY, PLANT AND EQUIPMENT", "PROPERTY, PLANT AND EQUIPMENT")),
])
def test_a_monolingual_heading_keeps_exactly_the_variants_it_had(heading, variants) -> None:
    """The first version gated on two RUNS and `_LATIN_RUN` matches a WORD, so every multi-word
    English heading was projected — digits and punctuation stripped — and anchored patterns were
    tested against headings no filing printed. The `_LEAD` variant is the only one expected."""
    from app.services.note_context import title_variants

    assert title_variants(heading) == variants


def test_the_bilingual_heading_it_exists_for_is_still_projected() -> None:
    from app.services.note_context import title_variants

    got = title_variants("FINANCIAL ASSETS AT FAIR VALUE 26. 按公允值計量且其變動計 "
                         "THROUGH PROFIT OR LOSS 入損益的金融資產")

    assert "FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS" in got
    assert "按公允值計量且其變動計入損益的金融資產" in got
