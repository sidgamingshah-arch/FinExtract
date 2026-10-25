"""China SCE's and Far East Horizon's dividends to non-controlling shareholders are their own line.

Both cash flow statements print the dividends their subsidiaries paid to non-controlling shareholders
as a financing row of its own, beside the dividends paid to the company's own shareholders:

    China SCE 2023       'Dividends paid to non-controlling shareholders of subsidiaries
                          支付附屬公司非控股股東的股息'   (122,425) / (187,477)
    Far East Horizon 2024 'Dividends paid to non-controlling shareholders'   (260,463) / (123,762)

Neither caption was an alias of Dvds Paid(Minority S'holders) — the line named 'interests', and the
filings print 'shareholders' — so both rows were swept into Other Financing Cash Flows. Binding them
was not safe while Dividends Paid(Fin) was the printed row LESS this line: China SCE's prior-year
'Dividends paid' (206,019) would have lost the minority figure. That residual is gone since the
user's decision landed at 70c13d9 (see test_a_line_is_its_printed_row_not_a_remainder_of_its_siblings),
so the row moves out of Other Financing into its own line and the financing total does not move.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import rollups
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
SEED = TEMPLATES / "output_csv_hk_line_items.json"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"

MINORITY = "cf_financing__dvds_paid_minority_sholders"
DIVIDENDS = "cf_financing__dividends_paid_fin"
OTHER_FIN = "cf_financing__other_financing_cash_flows"
CFF = "cf_financing__cash_flows_from_finance_activities"
FINANCING = "CASH FLOWS FROM FINANCING ACTIVITIES"

CHINA_SCE = "Dividends paid to non-controlling shareholders of subsidiaries 支付附屬公司非控股股東的股息"
FAR_EAST_HORIZON = "Dividends paid to non-controlling shareholders"


@pytest.fixture(scope="module")
def matcher():
    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return OntologyMatcher(build_working_view(shipped))


@pytest.mark.parametrize("caption", [CHINA_SCE, FAR_EAST_HORIZON])
def test_the_non_controlling_shareholders_row_binds_the_minority_line(matcher, caption):
    res = matcher.match(caption, statement="cash_flow", section=FINANCING)
    assert res is not None and res.canonical_key == MINORITY


def test_the_dividends_paid_row_beside_it_stays_dividends_paid(matcher):
    for caption in ("Dividends paid 支付股息", "Dividends paid"):
        res = matcher.match(caption, statement="cash_flow", section=FINANCING)
        assert res is not None and res.canonical_key == DIVIDENDS


def test_the_same_caption_in_the_statement_of_changes_in_equity_is_not_a_cash_flow(matcher):
    """China SCE prints the same words as a column movement of its equity statement."""
    res = matcher.match(CHINA_SCE, statement="changes_in_equity", section=None)
    assert res is None or res.canonical_key != MINORITY


def test_the_line_items_and_the_rulebook_carry_the_same_aliases():
    items = {i["key"]: i for i in json.loads(SEED.read_text(encoding="utf-8"))["items"]}
    concepts = {m["canonical_key"]: m
                for m in json.loads(ONTOLOGY.read_text(encoding="utf-8"))["mappings"]}
    for source in (items[MINORITY], concepts[MINORITY]):
        for aliases in (source["aliases"], source["aliases_i18n"]["en"]):
            assert "Dividends paid to non-controlling shareholders" in aliases
            assert "Dividends paid to non-controlling shareholders of subsidiaries" in aliases
    assert items[MINORITY]["aliases"] == concepts[MINORITY]["aliases"]


def _shown(**figures) -> dict[str, float]:
    rows = [{"canonical_key": key, "source_label": key, "printed_in": "face",
             "values": [{"basis": "consolidated", "period_label": "current", "value": str(v),
                         "provenance": {"source_kind": "native", "page_index": 111}}]}
            for key, v in figures.items()]
    return rollups.figures_as_shown(TEMPLATE, rows, "consolidated", "current")


def test_moving_the_row_out_of_other_financing_leaves_the_financing_total_as_printed():
    """China SCE 2023: Net cash flows used in financing activities (13,389,527) — the swept rows
    and the minority dividends, before and after the row has a line of its own."""
    before = _shown(**{OTHER_FIN: -11181558 - 122425, "cf_financing__interest_paid_fin": -2085544,
                       CFF: -13389527})
    after = _shown(**{OTHER_FIN: -11181558, MINORITY: -122425,
                      "cf_financing__interest_paid_fin": -2085544, CFF: -13389527})
    assert before[CFF] == after[CFF] == -13389527
    assert after[MINORITY] == -122425 and DIVIDENDS not in after
