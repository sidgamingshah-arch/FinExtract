"""A LINE'S DEFINITION NAMES THE PRINTED ROW THE TEMPLATE'S ROLLUP MAKES IT.

The definition is sent to the model verbatim, and the model chooses the row it cites from it. Four
HK definitions were boilerplate ("the subtotal the filing PRINTS as '<label>' ... where the filing
prints no subtotal, answer with an empty sources list") that contradicted the template's own
arithmetic, so a blind reader picked a row the template does not mean, or none:

  * Profit(Loss) Bef Extraord Items = Profit(Loss) Before Tax + Total Income Tax. No CAS or HKFRS
    filing prints "before extraordinary items", so the reader answered empty; the row it is, is the
    group's net profit (净利润 / Profit for the year) before the split to non-controlling interests.
  * Profit for the year = Bef Extraord Items + extraordinary items + Minority Interests(PL). That is
    the OWNERS' share, not the printed group total "Profit for the year" the definition named.
  * Total Cost of Sales sums the cost-of-sales rows; a filing that prints one row ('Direct costs')
    and no subtotal prints the total in that row, which "no subtotal -> empty" told the reader to skip.
  * Net Fixed Assets = Gross Fixed Assets + Accum Deprec, and Gross Fixed Assets takes in Other Fixed
    Assets (使用权资产) and Construction in Progress (在建工程). The definition said none of that, so a
    reader cited 固定资产 alone.

Each assertion reads the relation from the template and the set, so it holds for whichever filing
the line is asked about, and fails on the old text.

Two more were the generic "Extract the reported value for '<label>'" text, which named no row:

  * Dividends Paid(Fin). A CAS cash flow statement prints dividends and interest paid as ONE row
    (分配股利、利润或偿付利息支付的现金), and a blind reader left the line empty because that row is
    not dividends alone. The combined row is this line's figure; otherwise the dividends-paid row.
  * Notes Payable(CP). A reader could not tell whether guaranteed notes due within a year are notes
    payable. They are not: the line is bills payable and short-term notes or commercial paper, and
    bonds, guaranteed and senior notes and the current portion of long-term notes are CPLTD Other,
    the line whose aliases carry them.
"""
from __future__ import annotations

import json
import pathlib

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SET = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
ONTOLOGY = json.loads((TEMPLATES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
TEMPLATE = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))

ITEMS = {i["key"]: i for i in SET["items"]}


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


ROLLUPS = _rollups()
BOILERPLATE_EMPTY = "Where the filing prints no subtotal on this line"


def _definition(key: str) -> str:
    return ITEMS[key]["definition"]


def test_profit_before_extraordinary_items_is_the_group_net_profit():
    key = "is_pl__profit_loss_bef_extraord_items"
    assert ROLLUPS[key] == ["is_pl__profit_loss_before_tax", "is_pl__total_income_tax"]
    text = _definition(key)
    # After tax, before the non-controlling share: the printed group net profit.
    assert "净利润" in text and "Profit for the year" in text
    assert "non-controlling" in text
    assert BOILERPLATE_EMPTY not in text


def test_profit_for_the_year_is_the_owners_share_because_its_rollup_takes_out_minorities():
    key = "is_pl__profit_for_the_year"
    assert "is_pl__minority_interests_pl" in ROLLUPS[key]
    assert "is_pl__profit_loss_bef_extraord_items" in ROLLUPS[key]
    text = _definition(key)
    assert "owners of the parent" in text and "归属于母公司" in text
    # It says which printed row it is NOT: the group total belongs to Bef Extraord Items.
    assert ITEMS["is_pl__profit_loss_bef_extraord_items"]["label"] in text
    assert "PRINTS as 'Profit for the year'" not in text


def test_a_lone_cost_of_sales_row_is_the_total_cost_of_sales():
    key = "is_pl__total_cost_of_sales"
    assert "is_pl__cost_of_sales" in ROLLUPS[key]
    text = _definition(key)
    assert BOILERPLATE_EMPTY not in text
    assert "single cost-of-sales row and no subtotal" in text
    assert "直接成本" in ITEMS["is_pl__cost_of_sales"]["aliases_i18n"]["zh"]
    assert "直接成本" in text and "Direct costs" in text
    # Total operating costs on a CAS statement is a different, wider figure.
    assert "营业总成本" in text


def test_net_fixed_assets_names_every_class_gross_fixed_assets_takes_in():
    key = "bs_nca__net_fixed_assets"
    assert ROLLUPS[key] == ["bs_nca__gross_fixed_assets", "bs_nca__accum_deprec_and_impairment"]
    classes = ROLLUPS["bs_nca__gross_fixed_assets"]
    text = _definition(key)
    for child in ("bs_nca__other_fixed_assets", "bs_nca__construction_in_progress"):
        assert child in classes
        zh = ITEMS[child]["aliases_i18n"]["zh"]
        assert zh[0] in text, (child, zh[0])
    for caption in ITEMS["bs_nca__other_fixed_assets"]["aliases_i18n"]["zh"]:
        assert caption in text, caption


def test_a_combined_dividends_and_interest_row_is_dividends_paid():
    key = "cf_financing__dividends_paid_fin"
    combined = "分配股利、利润或偿付利息支付的现金"
    # The deterministic route already binds the combined row here; the definition now says so too.
    assert combined in ITEMS[key]["aliases_i18n"]["zh"]
    text = _definition(key)
    assert combined in text
    assert "dividends and interest paid as one row" in text and "interest included" in text
    assert "row the statement prints for dividends paid" in text
    assert "Extract the reported value for" not in text


def test_notes_payable_is_bills_and_short_term_paper_and_names_where_bonds_go():
    key = "bs_cl__notes_payable_cp"
    text = _definition(key)
    assert "Extract the reported value for" not in text
    assert "Bills payable" in text and "commercial paper" in text and "应付票据" in text
    for excluded in ("bonds", "guaranteed notes", "senior notes", "current portion of long-term notes"):
        assert excluded in text, excluded
    # It names, by its label, the line the set gives bonds and senior notes in current liabilities.
    home = ITEMS["bs_cl__cpltd_other"]
    assert {"Bonds", "Senior notes", "Notes payables"} <= set(home["aliases"])
    assert f"'{home['label']}'" in text


BORROWINGS = ("bs_cl__st_bank_loans_payable", "bs_cl__st_other_loans_payable", "bs_cl__cpltd_bank",
              "bs_cl__cpltd_other")


def _label(key: str) -> str:
    return f"'{ITEMS[key]['label']}'"


def _refers_to_a_note(text: str) -> bool:
    import importlib.util
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "llm_standin.py"
    spec = importlib.util.spec_from_file_location("llm_standin_defs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.refers_to_a_note(text)


@pytest.mark.parametrize("key", BORROWINGS)
def test_a_borrowing_line_says_what_it_is_not_extract_the_label(key):
    """Blind answers read the four placeholders ("Extract the reported value for 'ST Bank Loans
    Payable' ...") as definition_unclear on 1966, PetroChina, Hubei Energy, Zhongshan, Crystal
    Optech, Lai Sun and Far East Horizon: whether other loans, the current portion of long-term debt
    or a CAS 短期借款 of credit loans belong to the line."""
    text = _definition(key)
    assert "Extract the reported value for" not in text
    assert "within one year" in text and "current liabilities section" in text
    # A line the model may read only on the face is not told to read a note (the stand-in lint's
    # note_not_supplied, and the reply contract's read_from).
    assert not _refers_to_a_note(text)


def test_short_term_bank_loans_is_the_bank_part_and_names_its_peers():
    key = "bs_cl__st_bank_loans_payable"
    text = _definition(key)
    # hkex_1966: face 'Interest-bearing bank and other borrowings' 9,817,976 is bank 8,151,188 plus
    # other loans 1,666,788; the line is the bank part, the other loans are ST Other Loans Payable.
    assert "Interest-bearing bank and other borrowings" in text
    assert "bank loans part only" in text
    # PetroChina: 'Short-term borrowings' 138,783 takes in the current portion 92,828; the line is
    # 'Short-term borrowings excluding current portion of long-term borrowings' 45,955.
    assert "Short-term borrowings excluding current portion of long-term borrowings" in text
    # CAS: 短期借款 as printed, whatever its breakdown lists (信用借款 on Hubei Energy, Zhongshan).
    assert "短期借款" in ITEMS[key]["aliases_i18n"]["zh"]
    assert "'短期借款' as printed" in text and "信用借款" in text
    for peer in ("bs_cl__st_other_loans_payable", "bs_cl__cpltd_bank", "bs_cl__overdrafts",
                 "bs_cl__trade_financing", "bs_cl__notes_payable_cp", "bs_cl__cpltd_other"):
        assert _label(peer) in text, peer


def test_short_term_other_loans_names_the_bank_line_and_the_due_to_lines():
    key = "bs_cl__st_other_loans_payable"
    text = _definition(key)
    assert "Other borrowings" in ITEMS[key]["aliases"] and "'Other borrowings'" in text
    assert "Interest-bearing bank and other borrowings" in text
    for peer in ("bs_cl__st_bank_loans_payable", "bs_cl__cpltd_bank", "bs_cl__cpltd_other",
                 "bs_cl__entrusted_loan_payables_third_parties_cp", "bs_cl__due_to_related_parties_cp",
                 "bs_cl__due_to_directors_cp", "bs_cl__due_to_sholder_cp", "bs_cl__notes_payable_cp"):
        assert _label(peer) in text, peer


def test_cpltd_bank_is_the_current_portion_of_long_term_bank_loans_and_sums_its_rows():
    key = "bs_cl__cpltd_bank"
    text = _definition(key)
    # Far East Horizon prints it as '... bank loans – secured' and '– unsecured': the line is both.
    assert "Current portion of long term bank loans" in text and "their total" in text
    # The CAS face row for every current portion binds to CPLTD Other, and the definition agrees.
    combined = "一年内到期的非流动负债"
    assert combined in ITEMS["bs_cl__cpltd_other"]["aliases_i18n"]["zh"]
    assert combined not in ITEMS[key]["aliases_i18n"]["zh"]
    assert f"'{combined}' is {_label('bs_cl__cpltd_other')}" in text
    for peer in ("bs_cl__st_bank_loans_payable", "bs_cl__cpltd_other", "bs_cl__cpltd_convertible",
                 "bs_cl__cpltd_subordinated", "bs_cl__lease_liabilities_cp", "bs_cl__overdrafts",
                 "bs_ncl__ltd_bank"):
        assert _label(peer) in text, peer


def test_cpltd_other_is_bonds_and_notes_due_within_a_year_not_other_loans():
    key = "bs_cl__cpltd_other"
    text = _definition(key)
    # Lai Sun: current 'Guaranteed notes' 3,877,188 is the line; current 'Other borrowings' is not.
    assert "Guaranteed notes" in ITEMS[key]["aliases"] and "'Guaranteed notes'" in text
    assert "'一年内到期的非流动负债' as printed" in text
    for peer in ("bs_cl__st_other_loans_payable", "bs_cl__st_bank_loans_payable",
                 "bs_cl__cpltd_bank", "bs_cl__cpltd_convertible", "bs_cl__cpltd_subordinated",
                 "bs_cl__notes_payable_cp", "bs_ncl__ltd_other"):
        assert _label(peer) in text, peer


@pytest.mark.parametrize("key", ["bs_nca__net_fixed_assets", "cf_financing__dividends_paid_fin",
                                 "bs_cl__notes_payable_cp", *BORROWINGS])
def test_the_ontology_carries_the_same_definition(key):
    concepts = [c for c in ONTOLOGY["concepts"] if c.get("canonical_key") == key] \
        if "concepts" in ONTOLOGY else []
    if not concepts:
        found: list[dict] = []

        def walk(node) -> None:
            if isinstance(node, dict):
                if node.get("canonical_key") == key and "definition" in node:
                    found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(ONTOLOGY)
        concepts = found
    assert concepts and all(c["definition"] == _definition(key) for c in concepts)
