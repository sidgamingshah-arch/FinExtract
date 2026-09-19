"""A page can carry two statements, and a row belongs to the one it was printed under.

A mainland filing prints its statements back to back: on 000709, page 85 carries the tail of the
parent-company balance sheet down to 59% of the page and the head of the consolidated income
statement below it, and page 88 carries two cash-flow statements. `classify` names ONE statement
per page, and `pdf_extract` already SPLITS such a page into a batch per statement —
"extract:page=85:split_statement_at=0.590(balance_sheet->profit_and_loss)".

FOUR STAGES THREW THAT AWAY. `residual._sweep`, `normalize`'s sign cohorts and statement-shape
check, `map_ontology`'s concept gate and the face-mapping contract each resolved a row's statement
by PAGE INDEX, so every row above the boundary came back as the page's `profit_and_loss`. On
000709 that swept 长期借款 13,680,240,000.00, 应付债券 3,871,881,509.54, 股本 10,337,121,092.00,
资本公积 22,990,856,773.40, 盈余公积 3,133,992,896.19 and 未分配利润 8,751,800,057.33 — 78bn of
parent-company balance sheet — into `is_pl__other_operating_expenses`, the income statement's
catch-all, where the P&L's own arithmetic then subtracted them as operating charges.

The other half of the same pollution is an "of which" row, which is a BREAKDOWN of the row above
it rather than a line of its own: 其中：营业收入 121,616,519,837.62 swept into that same bucket
subtracted the company's entire revenue, and operating profit came to -119,668,723,602.64 against
a printed 800,840,003.25.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.buckets import PRINTED_ON_FLAG, statement_resolver

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


def _row(label: str, *, page: int = 0, stamp: str | None = None, key: str | None = None,
         value: int = 1, role: LineRole = LineRole.LINE, ordinal: int = 0) -> LineItem:
    item = LineItem(source_label=label, canonical_key=key, ordinal=ordinal, role=role)
    item.set_value(ExtractedValue(
        value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=page)))
    if stamp:
        item.confidence.flags.append(f"{PRINTED_ON_FLAG}:{stamp}")
    return item


def _doc(*rows: LineItem, statement: str = "profit_and_loss") -> DocumentModel:
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement=statement)]
    doc.line_items = list(rows)
    return doc


# ── the resolver ──────────────────────────────────────────────────────────────────────────────

def test_a_row_carrying_no_stamp_takes_the_statement_of_its_page():
    """Which is nearly every row: one statement per page is the ordinary case, and the page label
    has to keep answering for it."""
    doc = _doc(_row("Cost of sales"))
    assert statement_resolver(doc)(doc.line_items[0]) == "profit_and_loss"


def test_a_row_stamped_with_its_own_statement_is_not_read_off_the_page():
    """股本 printed above the income statement's title on a shared page is share capital, and the
    page label saying `profit_and_loss` is a fact about the page, not about the row."""
    doc = _doc(_row("股本", stamp="balance_sheet"))
    assert statement_resolver(doc)(doc.line_items[0]) == "balance_sheet"


def test_a_declared_match_statement_outranks_where_the_row_was_printed():
    """`equity_matrix.MATCH_STATEMENT_FLAG` answers a different question — which statement's
    VOCABULARY names this caption — and a transposed equity balance is a balance-sheet caption
    however the page it came off is labelled. It has to win over both of the others."""
    from app.services.equity_matrix import MATCH_STATEMENT_FLAG

    row = _row("Share premium", stamp="changes_in_equity")
    row.confidence.flags.append(f"{MATCH_STATEMENT_FLAG}:balance_sheet")
    assert statement_resolver(_doc(row, statement="changes_in_equity"))(row) == "balance_sheet"


def test_a_row_with_no_page_and_no_stamp_answers_nothing_rather_than_guessing():
    doc = _doc()
    orphan = LineItem(source_label="Cost of sales")
    assert statement_resolver(doc)(orphan) is None


# ── what the resolver is for: the sweep ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def shipped():
    """The output-CSV configuration, loaded as the extraction worker loads it. The SWEEP is what
    these tests are about, and `ResidualStage.run` only reaches it with a rulebook carrying a
    `residual_framework` — without one it falls back to the v1 template router."""
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_template
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(
        (_SAMPLES / "output_csv_hk_line_items.json").read_text(encoding="utf-8")), resolve=True)
    return (load_template(json.loads(
        (_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))),
        build_working_view(cfg))


def _swept(shipped, rows: list[LineItem], page_statement: str):
    from app.core.stage import PipelineContext
    from app.stages.residual import ResidualStage

    template, ontology = shipped
    doc = _doc(*rows, statement=page_statement)
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = template
    ctx.ontology = ontology
    ctx.mapping_strategy = "deterministic"      # the sweep runs after dedicated mapping has
    ResidualStage().run(doc, ctx)
    return doc


def _section(label: str, *, stamp: str | None) -> list[LineItem]:
    """A current-liabilities section with one unclaimed row in the middle of it."""
    return [
        _row("Trade payables", key="bs_cl__trade_payables_cp", value=100, stamp=stamp, ordinal=0),
        _row(label, value=25, stamp=stamp, ordinal=1),
        _row("Total current liabilities", key="bs_cl__total_current_liabilities", value=125,
             role=LineRole.SUBTOTAL, stamp=stamp, ordinal=2),
    ]


def test_a_balance_sheet_row_above_the_income_statements_title_is_not_swept_into_the_pl(shipped):
    """THE DEFECT, end to end. The page is labelled `profit_and_loss` because that is where its
    title is; the row was printed above it and carries the balance sheet's own stamp."""
    doc = _swept(shipped, _section("股本", stamp="balance_sheet"), page_statement="profit_and_loss")

    assert doc.line_items[1].canonical_key == "bs_cl__other_current_liabilities"
    assert "residual_combined" in doc.line_items[1].confidence.flags


def test_without_the_stamp_the_same_row_lands_in_the_statement_the_page_is_labelled(shipped):
    """The counterweight: this is what the four stages were doing to every row above a mid-page
    boundary, and it is why the stamp had to exist rather than the sweep being taught a special
    case. On 000709 the bucket it reached was `is_pl__other_operating_expenses` — 78bn of
    parent-company borrowings, share capital and reserves read as operating charges."""
    doc = _swept(shipped, _section("股本", stamp=None), page_statement="profit_and_loss")
    assert doc.line_items[1].canonical_key != "bs_cl__other_current_liabilities"


# ── "of which" is a breakdown of the row above it ─────────────────────────────────────────────

@pytest.mark.parametrize("label", [
    "其中：营业收入", "其中:应收利息", "其中：对联营企业和合营企业的投资收益",
    "of which: interest income", "of which trade receivables", "including: staff costs",
])
def test_an_of_which_row_is_not_swept_into_its_sections_residual(shipped, label):
    """Its amount is already inside the row above, so a residual that takes it counts the same
    money twice — and in an EXPENSE residual, net of its charges, it counts the wrong way too."""
    doc = _swept(shipped, _section(label, stamp="balance_sheet"), page_statement="balance_sheet")

    assert doc.line_items[1].canonical_key is None
    assert "residual_ineligible:of which breakdown" in doc.line_items[1].confidence.flags


@pytest.mark.parametrize("label", [
    "其中期间费用",            # no colon: a caption that merely opens with the same two characters
    "Costs of which nothing",  # "of which" mid-caption is not the prefix
    "Accrued charges",
])
def test_a_caption_that_merely_contains_the_words_is_still_swept(shipped, label):
    """The test is the PREFIX, because that is what makes the row a breakdown of the one above."""
    doc = _swept(shipped, _section(label, stamp="balance_sheet"), page_statement="balance_sheet")
    assert doc.line_items[1].canonical_key == "bs_cl__other_current_liabilities"


def test_emptying_the_frameworks_eligibility_sentence_removes_the_exclusion():
    """The rulebook decides which of the hardcoded tests apply — `_read_terms` switches each one on
    by the phrase naming it — so the declaration is what a reviewer edits, not this module."""
    import copy

    from app.schemas.loader import load_ontology
    from app.stages.residual import _read_terms

    raw = json.loads((_SAMPLES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
    fw = load_ontology(copy.deepcopy(raw)).residual_framework
    have = _read_terms(fw).exclusions
    assert "of which breakdown" in have and "sub-enumerated component" in have

    stripped = copy.deepcopy(raw)
    elig = stripped["residual_framework"]["sweep"]["eligibility"]
    elig[2] = elig[2].split(", an of which breakdown")[0] + "."
    left = _read_terms(load_ontology(stripped).residual_framework).exclusions
    # Both clauses go, each by the phrase naming it and neither by the other's.
    assert "of which breakdown" not in left and "sub-enumerated component" not in left
    # …and the rest of the sentence still switches its own tests on.
    assert "section subtotal" in left and "truncated caption" in left

# ── the CAS spine's sub-levels are breakdowns too ─────────────────────────────────────────────
# `row_reconstruct._CAS_STATEMENT_LINE` reads the mainland spine — 一、营业总收入 … 八、每股收益 —
# and says "a component is prefixed 其中： or 加： or 减：, or carries no prefix at all". These
# filings print two more forms underneath those lines, nested two and three deep:
#
#     五、净利润（净亏损以"－"号填列）        1,619,087,223.63
#       （一）持续经营净利润                 1,619,087,223.63
#     六、其他综合收益的税后净额              -37,916,928.33
#         （二）将重分类进损益的其他综合收益      -37,916,928.33
#           6.外币财务报表折算差额              -37,916,928.33
#
# so the sweep took 000709's net profit twice and its OCI total three times, into the income
# statement's expense catch-all.

@pytest.mark.parametrize("label", [
    "（一）持续经营净利润（净亏损以“－”号填列）",
    "（二）将重分类进损益的其他综合收益",
    "(一)按经营持续性分类",
    "6.外币财务报表折算差额",
    "2．将重分类进损益的其他综合收益",
    "（4）企业自身信用风险公允价值变动",
])
def test_a_sub_enumerated_component_of_the_cas_spine_is_not_swept(shipped, label):
    """It is already inside the 一、…八、line above it, and often inside another sub-level too."""
    doc = _swept(shipped, _section(label, stamp="balance_sheet"), page_statement="balance_sheet")

    assert doc.line_items[1].canonical_key is None
    assert "residual_ineligible:sub-enumerated component" in doc.line_items[1].confidence.flags


@pytest.mark.parametrize("label", [
    "1. Revenue",                 # an English enumeration is not the mainland face's sub-level
    "持续经营净利润",               # the same caption with no enumeration is a line of its own
    "（一）",                       # nothing but the marker — `_NOTE_REF_ONLY`'s business
    "其他应收款",
])
def test_a_caption_that_is_not_sub_enumerated_is_still_swept(shipped, label):
    """The counterweight: the test is the MAINLAND sub-level marker, and a caption without one is
    a line of its section like any other."""
    doc = _swept(shipped, _section(label, stamp="balance_sheet"), page_statement="balance_sheet")
    row = doc.line_items[1]
    assert not [f for f in row.confidence.flags
                if f.startswith("residual_ineligible:sub-enumerated")]
    # "（一）" alone is a bare marker and `_NOTE_REF_ONLY` owns it; the other three are swept.
    if label == "（一）":
        assert row.canonical_key is None
        assert "residual_ineligible:note-reference-only row" in row.confidence.flags
    else:
        assert row.canonical_key == "bs_cl__other_current_liabilities"


@pytest.mark.parametrize("label", ["七、70", "七、61"])
def test_a_mainland_note_reference_that_arrived_as_a_whole_label_is_not_swept(shipped, label):
    """Printed in the same form as the statement spine, which is why `_CAS_STATEMENT_LINE` carries
    a negative lookahead for a digit — "A note reference is not a subtotal of anything". It is not
    a line of the section either: 688008 swept 七、70 with an amount of 23,888,571.20."""
    doc = _swept(shipped, _section(label, stamp="balance_sheet"), page_statement="balance_sheet")

    assert doc.line_items[1].canonical_key is None
    assert "residual_ineligible:note-reference-only row" in doc.line_items[1].confidence.flags


def test_the_oci_attribution_line_is_an_attribution_line(shipped):
    """归属母公司所有者的其他综合收益 re-states the line above it split by who owns it.
    归属于母公司所有者的净利润 carries 归属于 and was already excluded; the OCI form drops the 于
    and was not, so 300319's -10,758,236.29 was charged to operating expenses."""
    doc = _swept(shipped, _section("归属母公司所有者的其他综合收益", stamp="balance_sheet"),
                 page_statement="balance_sheet")

    assert doc.line_items[1].canonical_key is None
    assert "residual_ineligible:attribution caption" in doc.line_items[1].confidence.flags


# ── the two chrome lines every CSRC face is headed by ─────────────────────────────────────────

@pytest.mark.parametrize("label,values", [
    ("项目", ["2024", "2023"]),                      # the column header, with no 附注 column
    ("項目", ["2024", "2023"]),
    ("编制单位：河钢股份有限公司", ["2024"]),            # "prepared by <company>"
    ("编制单位：深圳市麦捷微电子科技股份有限公司", ["2024"]),
])
def test_a_csrc_chrome_line_that_landed_on_a_figures_baseline_is_not_a_line_item(label, values):
    """Both are printed above every mainland statement and both land on a figure's baseline, so
    each was published as a line item whose AMOUNT WAS A YEAR — 000709's 编制单位 row reached
    `bs_ca__other_current_assets` with a value of 2024, and 300319's 项目 row reached the income
    statement's catch-all twice, once per basis.

    `项目 附注` was already listed; this is the same header on a statement with no note-reference
    column, where the whole caption is the one word."""
    from app.services.row_reconstruct import _HDR_LABEL, _is_date_ish

    assert _HDR_LABEL.search(label)
    assert all(_is_date_ish(Decimal(v)) for v in values)


@pytest.mark.parametrize("label", ["递延收益项目", "非经常性损益项目", "其他项目支出"])
def test_a_caption_that_merely_ends_in_the_header_word_is_not_chrome(label):
    """项目 is a common TAIL of a real mainland caption, which is why the bare form is ANCHORED.
    An unanchored alternative would have taken all three of these."""
    from app.services.row_reconstruct import _HDR_LABEL

    assert not _HDR_LABEL.search(label)


def test_a_real_caption_naming_a_statement_keeps_its_figures():
    """The statement-name alternatives beside the two new ones are deliberately NOT anchored —
    资产负债表日后事项 ("events after the balance sheet date") matches 资产负债表 — and they are
    safe because every rule here is gated on the row's values all being DATE FRAGMENTS. A caption
    with a real figure is never chrome, whatever its wording."""
    from app.services.row_reconstruct import _HDR_LABEL, _is_date_ish

    label = "资产负债表日后事项"
    assert _HDR_LABEL.search(label), "the statement-name alternative is not anchored"
    # …and the gate is what saves it: 1,234,567 is not a year or a day of the month.
    assert not _is_date_ish(Decimal("1234567"))
    assert _is_date_ish(Decimal("2024")) and _is_date_ish(Decimal("31"))
