"""ON A CAS FACE, 三、营业利润 IS NOT NET OPERATING PROFIT.

The HK line Net Operating Profit is the template's operating profit as its rollup adds it: gross
profit plus the other operating income, less the selling, administrative, research and other
operating expenses. Profit before tax adds Net Interest Income(Expense) and Net Other Financial
Inc(Exp) on top of it, so the line stands BEFORE finance costs, investment income, fair-value changes
and the share of associates. On an HKFRS face the printed 'Operating profit' / 'Profit from
operations' row is that subtotal. The CAS subtotal 营业利润 is not: it is struck after 财务费用 and
投资收益 (and 税金及附加, 公允价值变动收益, 资产处置收益). On 澜起科技 the two are 1,412,893,172.57
printed and 1,106,226,448.72 by the rollup; the reconciliation between them is exactly those rows.

At 31e0295 the definition said both — "营业利润 (三、营业利润) on a CAS one ... Read the printed
subtotal" and "as this template's rollup adds them" — and the deterministic route bound 营业利润 to
the line by its zh aliases (营业利润, 营业利润/息税前利润). Every assertion below fails there.
"""
from __future__ import annotations

import json
import pathlib
import re
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"
TEMPLATE = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))

NOP = "is_pl__net_operating_profit"
PBT = "is_pl__profit_loss_before_tax"
OTHERS = "is_pl__other_operating_expenses"


def _items():
    return {i["key"]: i for i in json.loads(SEED.read_text(encoding="utf-8"))["items"]}


def _concepts():
    return {m["canonical_key"]: m
            for m in json.loads(ONTOLOGY.read_text(encoding="utf-8"))["mappings"]}


def _rollups() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            key, rollup = node.get("canonical_key"), node.get("rollup")
            if isinstance(key, str) and isinstance(rollup, dict):
                out[key] = list(rollup.get("children") or [])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(TEMPLATE.get("statements"))
    return out


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped))


def _key(matcher, caption, section=None):
    res = matcher.match(caption, statement="profit_and_loss", section=section)
    return res.canonical_key if res is not None else None


# ── the definition says what the rollup means ─────────────────────────────────────────────────

def test_the_rollup_is_before_finance_costs_and_investment_income():
    """What the definition has to say is read off the template, not asserted beside it."""
    rollups = _rollups()
    assert "is_pl__gross_profit" in rollups[NOP]
    assert "is_pl__other_operating_income" in rollups[NOP]
    assert "is_pl__net_interest_income_expense" not in rollups[NOP]
    assert "is_pl__net_other_financial_inc_exp" not in rollups[NOP]
    assert rollups[PBT][0] == NOP
    assert {"is_pl__net_interest_income_expense", "is_pl__net_other_financial_inc_exp"} \
        <= set(rollups[PBT])


def test_the_definition_does_not_send_the_model_to_the_cas_subtotal():
    text = _items()[NOP]["definition"]
    # The old sentence and the unscoped instruction that went with it.
    assert "on a CAS one" not in text
    assert "Read the printed subtotal" not in text
    # Every sentence that names 营业利润 says it is NOT this line and NOT to cite it.
    named = [s for s in re.split(r"(?<=[.;:])\s+", text) if "营业利润" in s]
    assert named, "the definition must still name the CAS subtotal it is not"
    for sentence in named:
        assert "not 营业利润" in sentence, sentence
    assert "do not cite that row" in text
    cas = text[text.index("On a CAS income statement"):]
    assert "empty sources list" in cas.split(".")[0] + cas.split(".")[1]
    # Why: the CAS subtotal is after finance costs and investment income.
    assert "财务费用" in text and "投资收益" in text


def test_the_definition_still_names_the_hkfrs_row_and_the_rollup():
    text = _items()[NOP]["definition"]
    assert "as this template's rollup adds it" in text
    for printed in ("'Operating profit'", "'Profit from operations'", "'Loss from operating activities'"):
        assert printed in text, printed
    assert "cite that printed row" in text
    for before in ("finance costs", "investment income", "fair-value changes", "associates",
                   "income tax"):
        assert before in text.split("It is not")[0], before


# ── the deterministic route binds what the definition names ───────────────────────────────────

@pytest.mark.parametrize("caption", [
    "三、营业利润（亏损以“－”号填列）",       # every mainland filing in the corpus, consolidated
    "二、营业利润（亏损以“－”号填列）",       # ... and the parent company's statement
    "三、营业利润（亏损以“-”号填列）",
    "填列） 三、营业利润（亏损以“－”号填列）",  # the wrap-merge orphan head
    "三、营业利润", "营业利润", "營業利潤", "营业利润/息税前利润",
])
def test_the_cas_operating_profit_binds_no_line(matcher, caption):
    assert _key(matcher, caption) is None


@pytest.mark.parametrize("caption", [
    "PROFIT FROM OPERATIONS",                # PetroChina (hkex_0857)
    "LOSS FROM OPERATING ACTIVITIES",        # Lai Sun (hkex_0191)
    "Loss from operations 經營虧損",          # Grand Ming (hkex_kaming)
    "Profit from operations 經營溢利",
    "Operating profit",
    "經營溢利", "经营溢利", "经营利润", "經營利潤", "经营亏损",
    # A bilingual HKFRS caption is read by its English half; the veto is for a caption that is
    # 营业利润 and nothing else.
    "Operating profit 營業利潤",
])
def test_an_hkfrs_operating_profit_row_still_binds_the_line(matcher, caption):
    assert _key(matcher, caption) == NOP


def test_the_neighbouring_cas_subtotals_still_bind_their_lines(matcher):
    assert _key(matcher, "四、利润总额（亏损总额以“－”号填列）") == PBT
    assert _key(matcher, "五、净利润（净亏损以“－”号填列）") == "is_pl__profit_loss_bef_extraord_items"


# ── the configuration ─────────────────────────────────────────────────────────────────────────

def test_the_aliases_no_longer_carry_the_cas_subtotal_in_either_file():
    for entry in (_items()[NOP], _concepts()[NOP]):
        zh = entry["aliases_i18n"]["zh"]
        assert not any("营业利润" in a for a in zh), zh
        assert {"经营利润", "经营溢利", "经营亏损"} <= set(zh)
    assert _items()[NOP]["exclude_hints"] == _concepts()[NOP]["exclude_hints"]
    assert _items()[OTHERS]["exclude_hints"] == _concepts()[OTHERS]["exclude_hints"]


# ── the row, unbound, is a subtotal and is not swept ──────────────────────────────────────────

def _row(caption, current, role=LineRole.LINE):
    row = LineItem(source_label=caption, role=role)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal(current), value_raw=Decimal(current),
                                 provenance=Provenance(page_index=0)))
    return row


def _run(shipped, rows):
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss")]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.template_def = TEMPLATE
    MapOntologyStage().run(doc, ctx)
    ResidualStage().run(doc, ctx)
    return rows


def _face(role):
    """澜起科技's consolidated income statement (688008), current year, as the reader gives it."""
    return [
        _row("其中：营业收入", "3638911068.29"),
        _row("其中：营业成本", "1523614938.54"),
        _row("税金及附加", "6325049.35"),
        _row("销售费用", "96006550.08"),
        _row("管理费用", "196262500.24"),
        _row("研发费用", "763469994.44"),
        _row("财务费用", "-240504105.54"),
        _row("加：其他收益", "91576969.33"),
        _row("投资收益（损失以“－”号填列）", "48599096.46"),
        _row("资产减值损失（损失以“-”号填列）", "-44443090.77"),
        _row("三、营业利润（亏损以“－”号填列）", "1412893172.57", role=role),
        _row("四、利润总额（亏损总额以“－”号填列）", "1412617850.07", role=LineRole.TOTAL),
    ]


@pytest.mark.parametrize("role", [LineRole.TOTAL, LineRole.LINE])
def test_the_unbound_cas_operating_profit_is_not_swept(shipped, role):
    """The reader marks 三、营业利润 a statement total, and the residual refuses a total by role. On a
    run where it did not (role LINE), Other Operating Expenses refuses the caption itself — the
    protection never_sweep used to give it through Net Operating Profit's 营业利润 alias."""
    rows = _run(shipped, _face(role))
    op = rows[10]
    assert op.canonical_key != NOP, op.confidence.flags
    assert op.canonical_key != OTHERS, op.confidence.flags
    assert rows[11].canonical_key == PBT
    assert not any(r.canonical_key == NOP for r in rows)
