"""A printed row states one quantity, however many line items were configured to look for it.

THE DEFECT. A rung's base group is its `required` and `any_of` terms, and `any_of` SUMS whichever
of them resolve. Two sibling parts that read the same note row therefore add that row twice. The
configuration cannot prevent it on its own: the two parts are authored to read different notes, and
whether a filing prints one note both of them match is the filing's choice, not the config's.

WHY THE MODEL'S ROUTE IS WHERE THIS BITES. `stages.line_item_llm` asks, per line item, which row
of the notes holds its figure, and its answer OUTRANKS the declared `note_source` read entirely
(`stages.note_sourced._llm_holds`: "where the model has answered, this fills nothing and the figure
it gave feeds the cascade below exactly as a note-sourced one would"). The prompt does state the
exclusivity rule — "a printed row states one line's figure, so citing it for one line is also
saying it is not another's" — but `llm_request_grouping` ships as "none", ONE REQUEST PER LINE
ITEM, so two competing siblings are asked in separate calls and neither request names the other
line. The model is never shown the conflict, so the rule it was given cannot fire.

MEASURED with `tests.spy_line_item_llm` on 澜起科技 688008: note 七、19's 合计 was cited for both
`sub__ltp_other_fincl_assets_note_total` and `sub__ltp_fincl_assets_note_total`, and one row of
575,243,925.97 reached the LTP column as 1,150,487,851.94. Four rows of that filing were each cited
by between two and eight line items.

WHAT THE GUARD IS. `stages.note_sourced._row_identity` records which printed row each cascade input
came off — caption, amount and page, the SAME rule `periods.summable` already uses for a published
carrier — and `services.line_items._apply_terms` adds a base term whose row is already counted as
nothing. It is arithmetic and not selection: it does not change which row gets cited, only whether
one row can be summed twice.

DELIBERATELY NOT DEDUPLICATED: `adjustment` terms, and every `terms_op` but `sum`. An adjustment
reading the same row as the base is a different fault — the rung computes about zero rather than
about double — and dropping a deduction silently is the more dangerous direction of the two.
`max`/`min`/`first` CHOOSE between candidates instead of adding them, so two candidates that happen
to be one row are not a double count there at all.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable)
from app.core.stage import PipelineContext
from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services.line_items import evaluate
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage
from app.stages.note_sourced import NoteSourcedStage, _row_identity
from tests.spy_line_item_llm import SpyLineItemLlm

_SEED = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")

LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
OTHER = "sub__ltp_other_fincl_assets_note_total"
BARE = "sub__ltp_fincl_assets_note_total"
NOTE_PAGE = 181
TOTAL = Decimal("575243925.97")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def by_key(shipped):
    return {i.key: i for i in shipped.items}


@pytest.fixture(autouse=True)
def _restore_settings():
    """`get_settings` is `lru_cache`d, so the settings object is shared with the whole suite."""
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = was


# --- the identity ------------------------------------------------------------------------------

def _value(amount: str, *, page: int | None = NOTE_PAGE, caption: str | None = "合计",
           box: BBox | None = None) -> ExtractedValue:
    prov = None
    if page is not None:
        prov = Provenance(page_index=page, text_snippet=caption, value_bbox=box)
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                          value=Decimal(amount), value_raw=Decimal(amount), provenance=prov)


def test_two_figures_off_one_row_share_an_identity():
    a = _row_identity(_value("575243925.97"))
    b = _row_identity(_value("575243925.97"))

    assert a is not None and a == b


def test_two_rows_of_one_note_do_not():
    a = _row_identity(_value("575243925.97", caption="合计"))
    b = _row_identity(_value("575243925.97", caption="非上市股权投资"))

    assert a != b, "the caption is what tells two rows of one note apart"


def test_the_same_caption_on_a_different_page_does_not():
    assert _row_identity(_value("1.00", page=181)) != _row_identity(_value("1.00", page=182))


def test_the_same_row_with_a_different_amount_does_not():
    """Amount is part of the rule because a note's total row appears once per COLUMN, and two
    columns of one row are two figures."""
    assert _row_identity(_value("1.00")) != _row_identity(_value("2.00"))


@pytest.mark.parametrize("ev, why", [
    (_value("1.00", page=None), "no page: nothing locates the row"),
    (_value("1.00", caption=None, box=None), "neither a caption nor a box to tell rows apart"),
])
def test_no_discriminator_is_not_deduplicated(ev, why):
    """The CONSERVATIVE direction. A value that cannot be told from a different row carrying the
    same amount is left alone, so the arithmetic stays as it was — a double count a reviewer can
    still see beats silently dropping a real second figure."""
    assert _row_identity(ev) is None, why


def test_a_box_alone_is_enough():
    """Click-to-source gives every table row a box even where the snippet is empty."""
    ident = _row_identity(_value("1.00", caption=None, box=BBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4)))

    assert ident is not None


# --- the arithmetic ----------------------------------------------------------------------------

def _rung(by_key, item_key: str, rung_id: str):
    return next(r for r in by_key[item_key].cascade if r.id == rung_id)


def test_two_any_of_siblings_reading_one_row_are_summed_once(by_key):
    """The shipped rung, with the two parts 688008's note 七、19 put in front of the model."""
    known = {OTHER: TOTAL, BARE: TOTAL}
    one_row = (NOTE_PAGE, "合计", None, str(TOTAL))
    sources = {OTHER: one_row, BARE: one_row}

    got = evaluate(by_key[LTP], known, sources)

    assert got.value == TOTAL, "one printed row, one contribution"


def test_the_same_two_siblings_reading_different_rows_still_add(by_key):
    """The guard must not turn a rung that genuinely sums two disclosures into one that picks one.
    Same amount, same page, DIFFERENT row — which is what a note splitting a holding by class
    prints."""
    known = {OTHER: TOTAL, BARE: TOTAL}
    sources = {OTHER: (NOTE_PAGE, "非上市股权投资", None, str(TOTAL)),
               BARE: (NOTE_PAGE, "私募基金投资", None, str(TOTAL))}

    got = evaluate(by_key[LTP], known, sources)

    assert got.value == TOTAL * 2


def test_no_sources_at_all_is_the_arithmetic_this_had_before(by_key):
    """Every caller with no provenance to hand — the registry walk over a template's formulas, and
    every test that evaluates a rung from literals — must be untouched."""
    got = evaluate(by_key[LTP], {OTHER: TOTAL, BARE: TOTAL})

    assert got.value == TOTAL * 2


def test_a_duplicate_does_not_kill_a_rung_that_required_it(by_key):
    """A `required` term whose figure was already counted is SATISFIED — it was found — so the rung
    resolves. Killing it would publish nothing for a line whose figure is on the page. Asserted on
    a rung built for it: no shipped rung requires two note parts any more."""
    from app.schemas.line_items import LineItemDef

    line = LineItemDef.model_validate({
        "key": "x", "label": "x", "type": "derived", "cascade": [{"id": "R", "terms": [
            {"ref": "a", "role": "required"}, {"ref": "b", "role": "required"}]}]})
    one_row = (NOTE_PAGE, "合计", None, "100")
    got = evaluate(line, {"a": Decimal("100"), "b": Decimal("100")}, {"a": one_row, "b": one_row})

    assert got.resolved, "a figure found twice is still a figure found"
    assert got.value == Decimal("100"), got.value


def test_the_trail_names_a_term_that_was_read_and_not_added(by_key):
    """Otherwise a term read-but-not-added is indistinguishable from one never configured, which is
    the confusion `terms_missing` already caused once."""
    one_row = (NOTE_PAGE, "合计", None, str(TOTAL))
    got = evaluate(by_key[LTP], {OTHER: TOTAL, BARE: TOTAL},
                   {OTHER: one_row, BARE: one_row})

    duped = [i for i in got.inputs if i.get("duplicate_of_row")]
    assert len(duped) == 1, got.inputs
    assert duped[0]["used"] == "0"
    assert duped[0]["value"] == str(TOTAL), "the figure it read is still recorded"


# --- through the two stages, with the model answering ------------------------------------------

def _note(rows: list[tuple[str, str]], *, number: str = "七、19",
          title: str = "其他非流动金融资产") -> NotesTable:
    items = []
    for caption, amount in rows:
        row = NoteItem(raw_label=caption)
        row.values["a"] = ExtractedValue(
            basis=Basis.CONSOLIDATED, period_label="current",
            value=Decimal(amount), value_raw=Decimal(amount),
            provenance=Provenance(page_index=NOTE_PAGE, text_snippet=caption))
        items.append(row)
    return NotesTable(note_number=number, title=title, items=items)


# DECOY NOTES, because the selector scores a heading by how DISTINCTIVE its words are across the
# filing's other headings (`line_item_notes.header_pool` builds an IDF over them). A document
# holding one note gives every token an IDF of zero, nothing clears `MIN_SCORE`, and the request
# goes out with `notes_supplied: []` — which is a property of the fixture, not of the filing. A
# real filing carries ~130 headings; these are enough to make the measure mean something.
_DECOYS = ["货币资金", "应收账款", "存货", "固定资产", "在建工程", "无形资产",
           "长期待摊费用", "递延所得税资产", "短期借款", "应付账款", "应付职工薪酬",
           "营业收入", "营业成本", "管理费用", "研发费用", "财务费用"]


def _run_both_stages(shipped, spy) -> DocumentModel:
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.notes = [_note([("非上市股权投资", "531795561.02"),
                        ("私募基金投资", "43448364.95"),
                        ("合计", str(TOTAL))])]
    doc.notes += [_note([("合计", "1.00")], number=f"七、{i + 40}", title=t)
                  for i, t in enumerate(_DECOYS)]
    doc.line_items = []
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [OTHER, BARE]
    ctx.registry.register("llm", "spy", lambda: spy)
    ctx.settings.llm.provider = "spy"
    LineItemLlmStage().run(doc, ctx)
    NoteSourcedStage().run(doc, ctx)
    return doc


def _cents(value) -> Decimal | None:
    """`periods.concept_value` resolves to a float, so a cent is the precision it actually has."""
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


def _published(doc: DocumentModel, key: str) -> Decimal | None:
    from app.services.periods import concept_value
    rows = [{"canonical_key": li.canonical_key,
             "source_label": li.source_label,
             "values": [{"basis": "consolidated", "period_label": "current",
                         "value": str(ev.value), "value_raw": str(ev.value),
                         "provenance": (ev.provenance.model_dump(mode="json")
                                        if ev.provenance is not None else None)}
                        for ev in (li.values or {}).values() if ev.value is not None]}
            for li in doc.line_items if li.canonical_key == key]
    return _cents(concept_value(rows, "consolidated", "current")) if rows else None


def test_both_siblings_cite_the_one_total_row_and_it_publishes_once(shipped):
    """END TO END on the model's route, which is the route that decides this figure in production.

    Two requests, one per line item — the model is never told the other line exists — and both cite
    the note's own 合计. The column publishes the row ONCE.
    """
    spy = SpyLineItemLlm(only={OTHER, BARE})
    doc = _run_both_stages(shipped, spy)

    # The premise: the spy really did give one row to two lines, in separate requests.
    assert len(spy.requests) == 2, [r.get("line_items") for r in spy.requests]
    assert [len(r.get("line_items") or ()) for r in spy.requests] == [1, 1], (
        "one request per line item is what makes the prompt's exclusivity rule unable to fire")
    shared = {row: sorted(keys) for row, keys in spy.cited_more_than_once().items()}
    assert shared == {("七、19", "合计"): sorted([OTHER, BARE])}, spy.citations

    assert _published(doc, LTP) == TOTAL


def test_and_twice_when_the_model_gives_each_sibling_its_own_row(shipped):
    """The other half, so the test above is known to be measuring the guard and not a rung that
    only ever takes one term. Each part cites a DIFFERENT row of the same note."""
    spy = SpyLineItemLlm(only={OTHER, BARE},
                         cite={OTHER: ("七、19", "非上市股权投资"),
                               BARE: ("七、19", "私募基金投资")})
    doc = _run_both_stages(shipped, spy)

    assert spy.cited_more_than_once() == {}, spy.citations
    # The two rows sum to the same 575,243,925.97 the total row states, which is the point: the
    # note's own total and its two classes are the SAME quantity read two ways, and the guard has
    # to tell "one row twice" from "two rows that happen to add to it".
    assert _published(doc, LTP) == Decimal("531795561.02") + Decimal("43448364.95")
