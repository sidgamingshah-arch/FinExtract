"""THE SEVEN RETAINED-EARNINGS MOVEMENTS ARE READ OFF THE EQUITY STATEMENT — the gate, opened.

NEW FILE -> backend/tests/test_equity_statement_gate.py

WHAT WAS WRONG. Every one of the seven `is_retained` lines is a movement in retained earnings —
dividends declared, transfers to statutory reserves, prior-period restatements — and a filing
prints all of them in the statement of changes in equity (所有者权益变动表), never on the income
statement. They inherited `is_retained`, whose section default declares `statement:
profit_and_loss`, so `mapping._in_statement` refused them on the one statement whose captions they
are and admitted them on one whose captions they are not. Measured before the change, on the lines'
own newly authored Chinese:

    提取法定盈余公积          / changes_in_equity -> None, UNMATCHED
    对所有者（或股东）的分配     / changes_in_equity -> None, UNMATCHED

`statements` IS A LIST AND THIS IS A WIDENING. `_in_statement` admits a concept under ANY statement
it declares ("ONE MATCH IS ENOUGH"), so declaring both keeps the income statement — the CSRC's
older profit-distribution format really does print 利润分配 at the foot of it — and adds the one
that was missing. Nothing is moved and nothing is taken away.

TWO THINGS HAD TO TRAVEL WITH IT, and each is a defect the gate change would otherwise have
exposed rather than caused:

  * THE STATEMENT HAS TWO SPELLINGS. `StatementType` says `equity_changes`; the page classifier
    says `changes_in_equity`. `services.face_context` joined a line's declaration against a page's
    verdict without folding either, so an equity-gated line's statement block came back EMPTY and
    a citation naming its own spelling resolved against nothing. Both sides now go through
    `mapping.normalize_statement`, which the codebase already names as the owner of that fold.
  * A MATRIX ROW PUBLISHES NOTHING. An equity statement's columns are components and its rows are
    movements, so each cell carries a `column_index` and a `period_label` that is the column
    HEADER. Every consumer that publishes a figure skips such a value on purpose. So a claimed
    matrix row yields a line with a key and no figure — while costing the row its unclassified
    storage key and its review flag. `stages.map_ontology` refuses the claim instead.

WHAT IS STILL MISSING is therefore a DECLARATION, not a gate: which equity component column a
movement line reads. See the refusal in `stages.map_ontology` for why inferring it from the key
namespace is not acceptable here.
"""
from __future__ import annotations

import json as _json
import pathlib as _pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import load_line_item_set
from app.services import face_context, line_item_requests
from app.services.mapping import SourceRef, normalize_statement
from app.services.note_sourced import resolve_sources
from app.services.working_view import build_working_view

EQUITY_PAGE = 9
SEED = (_pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
MOVEMENTS = (
    "is_retained__cash_div_pref_shares", "is_retained__proposed_cash_dividends",
    "is_retained__cash_div_common_shares", "is_retained__stock_dividends_nc",
    "is_retained__transfer_to_reserves", "is_retained__prior_period_adjustments",
    "is_retained__other_adj_to_retained_profits",
)
# One authored caption per line, from the set's own zh vocabulary.
CAPTIONS = {
    "is_retained__transfer_to_reserves": "提取法定盈余公积",
    "is_retained__cash_div_common_shares": "对所有者（或股东）的分配",
    "is_retained__prior_period_adjustments": "前期差错更正",
    "is_retained__proposed_cash_dividends": "拟派现金股利",
}


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(_json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    from app.services.mapping import OntologyMatcher

    return OntologyMatcher(build_working_view(shipped))


# ── the declaration ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", MOVEMENTS)
def test_each_movement_line_declares_both_statements(shipped, key):
    item = next(i for i in shipped.items if i.key == key)
    declared = [str(getattr(s, "value", s)) for s in (item.statements or ())]
    assert declared == ["equity_changes", "profit_and_loss"], declared


def test_the_income_statement_is_kept_rather_than_replaced(shipped):
    """A widening, not a move. The CSRC's older profit-distribution format prints 利润分配 at the
    foot of the income statement, so dropping it would trade one unreadable statement for
    another — and `_in_statement` admits a concept under any statement it declares."""
    for key in MOVEMENTS:
        item = next(i for i in shipped.items if i.key == key)
        assert "profit_and_loss" in [str(getattr(s, "value", s)) for s in (item.statements or ())]


# ── the gate ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,caption", sorted(CAPTIONS.items()))
def test_a_movement_caption_now_resolves_on_the_equity_statement(matcher, key, caption):
    got = matcher.match(caption, statement="changes_in_equity", section=None)
    assert getattr(got, "canonical_key", None) == key, (
        f"{caption} -> {getattr(got, 'canonical_key', None)} on changes_in_equity, expected {key}")


@pytest.mark.parametrize("key,caption", sorted(CAPTIONS.items()))
def test_it_still_resolves_on_the_income_statement(matcher, key, caption):
    assert getattr(matcher.match(caption, statement="profit_and_loss", section=None),
                   "canonical_key", None) == key


@pytest.mark.parametrize("caption", sorted(CAPTIONS.values()))
def test_the_balance_sheet_is_still_refused(matcher, caption):
    """The widening must not become "any statement". These are movements over a period; the
    balance sheet states positions at a date, and its equity captions belong to bs_equity."""
    assert matcher.match(caption, statement="balance_sheet", section=None).canonical_key is None


# ── the two spellings ────────────────────────────────────────────────────────────────────────

def _equity_doc(caption="提取法定盈余公积", *, column=False):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=EQUITY_PAGE, kind=PageKind.FACE,
                            statement="changes_in_equity")]
    row = LineItem(source_label=caption)
    row.values["v"] = ExtractedValue(
        basis=Basis.CONSOLIDATED, period_label="保留溢利" if column else "current",
        value=Decimal("100"), value_raw=Decimal("100"),
        column_index=3 if column else None,
        provenance=Provenance(page_index=EQUITY_PAGE))
    doc.line_items = [row]
    return doc


def test_the_two_spellings_of_the_equity_statement_are_folded():
    assert normalize_statement("equity_changes") == "changes_in_equity"
    assert normalize_statement("changes_in_equity") == "changes_in_equity"


def test_an_equity_gated_line_is_supplied_the_equity_statements_rows(shipped):
    """THE DEFECT THE GATE WOULD HAVE EXPOSED. `_sections_of_item` emits the line's own spelling
    (`equity_changes`) and a page carries the classifier's (`changes_in_equity`), so before the
    fold this block came back empty — the model was shown nothing for the very statement it had
    just been given permission to read."""
    by_key = {i.key: i for i in shipped.items}
    key = "is_retained__transfer_to_reserves"
    plan = line_item_requests.RequestPlan(
        name=key, keys=(key,), notes=(),
        sections=line_item_requests._sections_of_item(by_key[key]))
    wanted = line_item_requests.face_statements(plan, by_key)
    assert "equity_changes" in wanted, "the plan must still ask in the line's own spelling"

    blocks = face_context.face_rows(_equity_doc(), wanted)
    assert [b["statement"] for b in blocks] == ["changes_in_equity"], blocks
    assert blocks[0]["rows"][0]["caption"] == "提取法定盈余公积"


def test_a_citation_naming_the_lines_own_spelling_resolves_against_the_page():
    """The other side of the same fold. A model given `equity_changes` on the line and
    `changes_in_equity` on the block could cite either; both must resolve."""
    index = face_context.face_index(_equity_doc())
    for token in ("equity_changes", "changes_in_equity"):
        res, un = resolve_sources(
            [SourceRef(statement=token, caption="提取法定盈余公积")], [], index)
        assert not un, (token, un)
        assert res[0]["figures"] == {"current": "100"}


# ── the matrix refusal ───────────────────────────────────────────────────────────────────────

def _map(doc, shipped):
    from app.core.stage import PipelineContext
    from app.stages.map_ontology import MapOntologyStage

    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    MapOntologyStage().run(doc, ctx)
    return doc.line_items[0], ctx


def test_a_non_matrix_equity_row_binds_and_keeps_its_figure(shipped):
    """WHERE THE WIDENED GATE PAYS OFF TODAY. `_maybe_matrix` returns None when it cannot detect a
    matrix, the page is read by the ordinary comparative path, and its rows carry real period
    labels and no column index — so they bind and publish like any other face row."""
    row, _ctx = _map(_equity_doc(), shipped)
    assert row.canonical_key == "is_retained__transfer_to_reserves", row.canonical_key
    assert next(iter(row.values.values())).value == Decimal("100")


def test_a_matrix_column_row_is_not_claimed(shipped):
    """A matrix cell is a COMPONENT, not a period, and every consumer that publishes a figure
    skips it — so binding one yields a line with a key and no figure, while costing the row the
    unclassified storage key and the review flag that say a figure is sitting there unplaced."""
    row, ctx = _map(_equity_doc(column=True), shipped)
    assert row.canonical_key != "is_retained__transfer_to_reserves", (
        "a matrix row was claimed; it can publish nothing and has now lost its review flag")
    assert any("matrix_column_row_not_claimed" in line for line in ctx.logs), ctx.logs[-4:]


def test_the_refusal_reads_every_figure_and_not_only_the_first(shipped):
    """A row is refused only when ALL of its figures are columns. A movement row that somehow
    carries one real period alongside its columns still has something publishable, and refusing
    it would lose that."""
    doc = _equity_doc(column=True)
    doc.line_items[0].values["real"] = ExtractedValue(
        basis=Basis.CONSOLIDATED, period_label="current",
        value=Decimal("7"), value_raw=Decimal("7"),
        provenance=Provenance(page_index=EQUITY_PAGE))
    row, _ctx = _map(doc, shipped)
    assert row.canonical_key == "is_retained__transfer_to_reserves", row.canonical_key


def test_the_shipped_matrix_fixture_is_all_columns(shipped):
    """The premise of the refusal, measured on a real statement of changes in equity rather than
    asserted. Every figure on every movement row of `tests/test_equity_matrix`'s fixture carries a
    column index, and its period labels are component names."""
    from tests.test_equity_matrix import _matrix_words
    from app.services.row_reconstruct import build_line_items

    rows = build_line_items(_matrix_words(), page_index=EQUITY_PAGE, document_id="d",
                            source_kind="native", statement="changes_in_equity")[0]
    assert rows, "the fixture produced no rows"
    for row in rows:
        values = list(row.values.values())
        assert values and all(getattr(v, "column_index", None) is not None for v in values), (
            f"{row.source_label!r} has a figure that is not a matrix column")
        assert {str(v.period_label) for v in values} <= {
            "Issued capital", "Share premium account", "Other reserves", "Retained profits",
            "Non-controlling interests", "Total equity"}, (
            f"{row.source_label!r} carries a period label that is not a component column")
