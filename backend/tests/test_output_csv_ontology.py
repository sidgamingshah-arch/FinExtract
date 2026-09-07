from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook

from app.api.routes.documents import _accounting_checks
from app.core.models.document import DocumentModel, PageSource
from app.core.models.line_item import LineItem, NoteItem, NoteRef, NotesTable
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology
from app.services.mapping import OntologyMatcher, normalize_label
from app.services.rollups import calculated_nodes
from app.services.rollups import evaluate_rows
from app.stages.map_ontology import MapOntologyStage
from app.stages.residual import ResidualStage
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue


SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
ONTOLOGY_DEF = json.loads((SAMPLES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
TEMPLATE_DEF = json.loads((SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))


def _ontology():
    return load_ontology(json.loads(json.dumps(ONTOLOGY_DEF)), resolve=True)


def test_every_arithmetic_workbook_rule_is_compiled_or_explicitly_excluded():
    workbook = load_workbook(
        Path(__file__).resolve().parent.parent / "_exports"
        / "China_HongKong_Financial_Extraction_Ontology_v1.6.xlsx",
        read_only=True, data_only=True)
    field_labels = {
        str(row[0]): str(row[4]).strip()
        for row in workbook["Field Ontology"].iter_rows(min_row=4, values_only=True)
        if row[0] and row[4]
    }
    pattern = re.compile(r"(?:NCA|CA|Equity|NCL|CL|P&L|Cash Flow|Others|Notes)_\d{3}")
    arithmetic = []
    for row in workbook["Computed Field Logic"].iter_rows(min_row=4, values_only=True):
        field_id, computation_type = str(row[0] or ""), str(row[4] or "")
        dependencies = [item for item in pattern.findall(str(row[6] or ""))
                        if item != field_id]
        if computation_type == "COMPUTE_TOTAL" or (
                computation_type == "COMPUTE_RESIDUAL" and dependencies):
            arithmetic.append(field_id)

    labels_to_keys = defaultdict(list)
    pending = [section for statement in TEMPLATE_DEF["statements"]
               for section in statement["sections"]]
    while pending:
        node = pending.pop()
        labels_to_keys[str(node.get("label", "")).strip().casefold()].append(node.get("canonical_key"))
        pending.extend(node.get("children") or [])

    # NCA_010 (Other Fixed Assets), NCA_014 (Net Fixed Assets), and NCA_022 (Other
    # Intangible Assets) are directly reported fields in the active ontology, not formulas
    # to recompute from neighbouring lines.
    # P&L_014 (Deprec & Impairment(COS)) is computed by services.deprec_impairment's priority
    # cascade over note-level datasets, not by the template rollup this workbook rule describes.
    # NCA_037/CA_016 (Secur & Other Fincl Assets(LTP)/(CP)) are likewise computed by
    # services.secur_fincl_assets, not by the wealth-management/pledged rollup this rule describes.
    # NCA_032 (Due from Related Parties(LTP)) and CA_044 (Other Receivables(CP)) are computed by
    # services.related_party_receivables, not by the entrusted-loan/related-party rollup here.
    excluded = {"P&L_091", "Others_011", "Notes_013", "NCA_010", "NCA_022", "NCA_023", "CA_038",
                "P&L_014", "NCA_037", "CA_016", "NCA_032", "CA_044"}
    calculated = set(calculated_nodes(TEMPLATE_DEF))
    compiled = {
        field_id for field_id in arithmetic
        if field_id not in excluded
        and any(key in calculated for key in labels_to_keys.get(field_labels[field_id].casefold(), []))
    }
    assert len(arithmetic) == 71
    assert compiled == set(arithmetic) - excluded


def _value(value: int) -> dict:
    return {
        "basis": "consolidated",
        "period_label": "current",
        "value": str(value),
        "provenance": {"source_kind": "native", "page_index": 1},
    }


def _row(key: str, value: int) -> dict:
    return {"canonical_key": key, "source_label": key, "values": [_value(value)]}

def test_every_calculated_template_node_remains_available_to_extraction():
    by_key = {mapping.canonical_key: mapping for mapping in _ontology().mappings}
    calculated = set(calculated_nodes(TEMPLATE_DEF))

    assert calculated
    assert not calculated - set(by_key)
    assert {by_key[key].extraction_mode for key in calculated} <= {"extract", "extract_or_derive"}

    # Conditional residuals are assigned by the residual stage, while ordinary subtotals may be
    # mapped directly from their printed caption. Neither may become computed-only.
    assert not {key for key in calculated if by_key[key].extraction_mode == "derive"}


def test_printed_calculated_value_is_validation_evidence_not_the_served_total():
    rows = [
        _row("is_pl__net_operating_profit", 100),
        _row("is_pl__net_interest_income_expense", -10),
        _row("is_pl__net_other_financial_inc_exp", -5),
        _row("is_pl__other_income_expense", -5),
        _row("is_pl__profit_loss_before_tax", 75),
    ]

    checks = _accounting_checks(rows, [], "en", [], TEMPLATE_DEF)
    mismatch = next(item for item in checks
                    if item["type"] == "calculated_mismatch"
                    and item["target"] == "is_pl__profit_loss_before_tax")

    assert mismatch["delta"] == "5"
    assert [line[0] for line in mismatch["calc"]][:2] == [
        "Printed in the document", "Computed from components"]


def test_explicit_formula_ranges_include_every_intervening_template_line():
    nodes = calculated_nodes(TEMPLATE_DEF)

    inventory = nodes["bs_ca__inventories"]["rollup"]
    assert inventory["reported_total_key"] == "bs_ca__inventories"
    assert inventory["reported_total_op"] == "diff"

    # Other Fixed Assets is a directly reported field, not a subtotal of the fixed-asset lines.
    assert "bs_nca__other_fixed_assets" not in nodes
    # Other Intangible Assets is likewise reported directly, not an aggregation of other
    # intangible-asset categories.
    assert "bs_nca__other_intangible_assets" not in nodes

    operating = nodes["is_pl__net_operating_profit"]["rollup"]["children"]
    assert "is_pl__selling_and_marketing_expenses" in operating
    assert "is_pl__general_and_admin_expenses" in operating
    assert "is_pl__other_operating_expenses" in operating
    assert "is_pl__capitalized_costs" in operating

    current_liabilities = nodes["bs_cl__total_current_liabilities"]["rollup"]["children"]
    assert "bs_cl__cpltd_bank" in current_liabilities
    assert "bs_cl__trade_payables_cp" in current_liabilities
    assert "bs_cl__other_provisions_cp" in current_liabilities


def test_logic_document_residual_calculations_are_declared_on_the_active_template():
    nodes = calculated_nodes(TEMPLATE_DEF)
    expected_sources = {
        "is_oci__other_equity_and_reserves_adj": "is_oci__total_other_comprehensive_income",
        "cf_oper_indirect__other_non_cash_adjs_oper": "cf_oper_indirect__cash_flows_oper_activ_indirect",
        "cf_investing__other_invest_cash_flows": "cf_investing__cash_flows_from_invest_activities",
        "cf_financing__other_financing_cash_flows": "cf_financing__cash_flows_from_finance_activities",
    }

    for key, source in expected_sources.items():
        rollup = nodes[key]["rollup"]
        assert rollup["reported_total_key"] == source
        assert rollup["reported_total_op"] == "diff"
        assert rollup["use_reported_total_components"] is True


def test_reviewer_corrected_extracted_roles_and_equity_formulas():
    nodes = {node["canonical_key"]: node
             for statement in TEMPLATE_DEF["statements"]
             for section in statement.get("sections", [])
             for node in section.get("children", [])}

    for key in (
        "bs_nca__accum_deprec_and_impairment",
        "bs_nca__accum_goodwill_amortized",
        "bs_nca__accum_intgbl_assets_amort",
        "bs_ca__allow_for_doubtful_accounts",
        "is_pl__goodwill_amortization",
    ):
        assert nodes[key]["role"] == "line"
        assert "rollup" not in nodes[key]
    assert nodes["bs_nca__net_fixed_assets"]["role"] == "line"
    assert nodes["bs_nca__net_fixed_assets"]["rollup"] == {
        "op": "sum",
        "children": [
            "bs_nca__gross_fixed_assets",
            "bs_nca__accum_deprec_and_impairment",
        ],
    }
    assert nodes["bs_nca__net_intangibles"]["rollup"]["children"] == [
        "bs_nca__goodwill",
        "bs_nca__accum_goodwill_amortized",
        "bs_nca__land_use_rights",
        "bs_nca__concessions_licenses_and_trdmrks",
        "bs_nca__patents",
        "bs_nca__start_up_capitalized_expenses",
        "bs_nca__r_and_d_capitalized_expenses",
        "bs_nca__other_intangible_assets",
        "bs_nca__accum_intgbl_assets_amort",
    ]

    retained = nodes["bs_equity__retained_profits"]["rollup"]["children"]
    assert retained == [
        "bs_equity__revaluation_reserves",
        "bs_equity__hedging_reserves",
        "bs_equity__other_reserves",
        "bs_equity__auditor_adj_on_retained_profits",
    ]
    total_equity = nodes["bs_equity__total_equity_and_reserves"]["rollup"]["children"]
    assert total_equity == [
        "bs_equity__permanent_equity",
        "bs_equity__retained_profits",
        "bs_equity__treasury_shares",
        "bs_equity__forex_translation_equity",
        "bs_equity__subordinated_debt_from_related_parties",
        "bs_equity__subordinated_debt_equity",
        "bs_equity__other_equity",
        "bs_equity__policyholders_equity",
        "bs_equity__accum_oth_eqty_rsrv_inc",
        "bs_equity__minority_interest_equity",
    ]


def test_reviewer_identified_subtotals_are_refused_by_residual_concepts():
    matcher = OntologyMatcher(_ontology(), settings=PipelineContext().settings)
    refused = [
        ("bs_ncl__other_non_current_liabilities", "balance_sheet", "NON-CURRENT LIABILITIES",
         "Total assets less current liabilities"),
        ("cf_oper_indirect__other_non_cash_adjs_oper", "cash_flow", None,
         "Cash generated from operations"),
        ("cf_financing__other_financing_cash_flows", "cash_flow", None,
         "NET INCREASE IN CASH AND CASH EQUIVALENTS"),
        ("is_oci__other_equity_and_reserves_adj", "profit_and_loss", None,
         "Total comprehensive loss for the year"),
        ("is_pl__expenses_own_work_capitalized", "profit_and_loss", None,
         "Interest income"),
        ("is_pl__minority_interests_pl", "profit_and_loss", None,
         "Other comprehensive income attributable to non-controlling interests"),
    ]
    for key, statement, section, caption in refused:
        assert not matcher._allowed(key, statement, section, caption)


def test_residual_sweep_enforces_the_same_reviewer_exclusions():
    from app.stages.residual import _Residual, _vetoed_by_never_sweep

    cases = [
        (["cash generated from operations"], "Cash generated from operations"),
        (["net (?:increase|decrease).*cash and cash equivalents"],
         "NET INCREASE/(DECREASE) IN CASH AND CASH EQUIVALENTS"),
        (["non-pledged and non-restricted (?:cash|time deposits)"],
         "Non-pledged and non-restricted time deposits"),
        (["total comprehensive (?:income|loss)"],
         "Total comprehensive loss for the year"),
    ]
    for patterns, caption in cases:
        residual = _Residual("other", "section", None, None,
                             exclude_patterns=tuple(patterns))
        assert _vetoed_by_never_sweep(residual, caption)


def test_operating_subtotal_uses_all_mapped_expenses_not_only_range_endpoints():
    rows = [
        _row("is_pl__gross_profit", 100),
        _row("is_pl__selling_and_marketing_expenses", -10),
        _row("is_pl__general_and_admin_expenses", -20),
        _row("is_pl__other_operating_expenses", -5),
        _row("is_pl__net_operating_profit", 60),
    ]

    calculated = evaluate_rows(TEMPLATE_DEF, rows, "consolidated", "current")

    assert calculated["is_pl__net_operating_profit"].value == 65
    mismatch = next(item for item in _accounting_checks(rows, [], "en", [], TEMPLATE_DEF)
                    if item["type"] == "calculated_mismatch"
                    and item["target"] == "is_pl__net_operating_profit")
    assert mismatch["delta"] == "5"


def test_containment_keeps_a_printed_calculated_total_as_validation_evidence():
    parent = LineItem(source_label="Profit before taxation",
                      canonical_key="is_pl__profit_loss_before_tax")
    child = LineItem(source_label="Net operating profit",
                     canonical_key="is_pl__net_operating_profit")
    doc = DocumentModel(filename="f.pdf", line_items=[child, parent])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = TEMPLATE_DEF

    removed = MapOntologyStage._enforce_containment(doc, _ontology(), ctx)

    assert removed == 0
    assert parent.canonical_key == "is_pl__profit_loss_before_tax"


def test_critical_face_captions_have_their_correct_owner():
    matcher = OntologyMatcher(_ontology(), llm_provider=None)
    expected = {
        "Sales(Revenues)": "is_pl__sales_revenues",
        "TURNOVER": "is_pl__sales_revenues",
        "Cost of sales": "is_pl__cost_of_sales",
        "Administrative expenses": "is_pl__general_and_admin_expenses",
        "Selling and marketing expenses": "is_pl__selling_and_marketing_expenses",
        "Selling & marketting expenses": "is_pl__selling_and_marketing_expenses",
        "Operating income": "is_pl__net_operating_profit",
        "Profit before taxation": "is_pl__profit_loss_before_tax",
        "LOSS FOR THE YEAR": "is_pl__profit_for_the_year",
        "Other revenue and gains": "is_pl__other_operating_income",
        "Dividends on Perpetual Bond": "is_pl__interest_on_capital_market_instruments",
    }

    actual = {
        label: matcher.match(label, statement="profit_and_loss").canonical_key
        for label in expected
    }

    assert actual == expected

# Deprec & Impairment (Oper Exp)/(COS) and Secur & Other Fincl Assets (CP)/(LTP) moved to
# extraction_mode "derive" — computed by services.deprec_impairment and
# services.secur_fincl_assets, never bound to a printed caption — so the priority-ownership
# disambiguation this file used to test for them lives in test_deprec_impairment.py and
# test_secur_fincl_assets.py instead.


def test_a_statement_level_total_reaches_its_concept_whatever_banner_it_is_printed_under():
    """A MAINLAND BALANCE SHEET PRINTS ITS STATEMENT TOTALS INSIDE ANOTHER SECTION.

    资产总计 comes after the non-current block, 负债合计 after the non-current liabilities, and
    负债和所有者权益总计 at the very end of the equity block — so the banner in force where each is
    printed is NEVER the section its compact key namespaces it into (bs_ca__, bs_cl__). The section
    gate therefore refused all three, and on 澜起科技 688008 that had a consequence worse than a
    missing row: 资产总计 was refused on the consolidated sheet and accepted four pages later on the
    parent company's — whose section happened to carry over as current assets — so Total Assets
    published 7,388,035,311.08, the PARENT's balance, as the group's, and Total Liabilities published
    nothing at all.

    Scoping them to ``bs_top_level`` is how this schema says "no banner constrains this concept"
    (see ``OntologyMatcher._scope_tokens``), and it is a statement of intent in the rulebook rather
    than a special case in the reader.
    """
    matcher = OntologyMatcher(_ontology(), llm_provider=None)
    printed_under = {
        ("资产总计", "非流动资产："): "bs_ca__total_assets",
        ("负债合计", "非流动负债："): "bs_cl__total_liabilities",
        ("负债和所有者权益总计", "所有者权益（或股东权益）："): "bs_cl__total_equity_and_liabilities",
        # …and the section totals, which ARE printed in the section they belong to, still resolve
        # there: the change must not have loosened the gate for the concepts it correctly scopes.
        ("流动资产合计", "流动资产："): "bs_ca__total_current_assets",
        ("非流动资产合计", "非流动资产："): "bs_nca__total_non_current_assets",
        ("所有者权益（或股东权益）合计", "所有者权益（或股东权益）："):
            "bs_equity__total_equity_and_reserves",
    }

    actual = {(label, section): matcher.match(label, statement="balance_sheet",
                                              section=section).canonical_key
              for label, section in printed_under}

    assert actual == printed_under


def test_only_the_statement_level_totals_are_scoped_above_their_section():
    """The exemption is three concepts wide, and naming them here is what keeps it that way: a
    concept scoped to no section is claimable under every banner on its statement, which is exactly
    the confident wrong answer the gate exists to prevent."""
    unconstrained = sorted(m.canonical_key for m in _ontology().mappings
                           if m.section_scope == ["bs_top_level"])

    assert unconstrained == ["bs_ca__total_assets", "bs_cl__total_equity_and_liabilities",
                             "bs_cl__total_liabilities"]


def test_each_supported_output_subsection_has_exactly_one_residual():
    ontology = _ontology()
    residuals = [mapping for mapping in ontology.mappings
                 if mapping.value_scope == "exclusive_residual"]
    scopes = [(mapping.residual_policy.section_scope if mapping.residual_policy else "")
              for mapping in residuals]

    assert len(residuals) == 11
    assert len(scopes) == len(set(scopes))
    assert {"is_pl", "is_oci", "is_retained", "cf_oper_indirect", "cf_investing",
            "cf_financing"} <= set(scopes)


def test_an_unclaimed_pl_face_value_reaches_its_existing_subsection_residual():
    unknown = LineItem(source_label="Unclassified operating cost", ordinal=0)
    unknown.set_value(ExtractedValue(
        value=-10, value_raw=-10, basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    subtotal = LineItem(source_label="Net operating profit", ordinal=1,
                        canonical_key="is_pl__net_operating_profit",
                        role=LineRole.SUBTOTAL)
    subtotal.set_value(ExtractedValue(
        value=90, value_raw=90, basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf", line_items=[unknown, subtotal],
                        pages=[PageSource(index=0, statement="profit_and_loss")])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = _ontology()
    ctx.mapping_strategy = "deterministic"

    ResidualStage().run(doc, ctx)

    assert unknown.canonical_key == "is_pl__other_operating_expenses"
    assert "residual_combined" in unknown.confidence.flags


def test_dedicated_mapping_runs_before_the_subsection_residual():
    ontology = _ontology()
    admin = LineItem(source_label="Administrative expenses", ordinal=0,
                     section_hint="Income & Expenses")
    admin.set_value(ExtractedValue(
        value=-20, value_raw=-20, basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    unknown = LineItem(source_label="Unclassified operating cost", ordinal=1,
                       section_hint="Income & Expenses")
    unknown.set_value(ExtractedValue(
        value=-10, value_raw=-10, basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    subtotal = LineItem(source_label="Net operating profit", ordinal=2,
                        section_hint="Income & Expenses")
    subtotal.set_value(ExtractedValue(
        value=70, value_raw=70, basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf", line_items=[admin, unknown, subtotal],
                        pages=[PageSource(index=0, statement="profit_and_loss")])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology, ctx.template = ontology, TEMPLATE_DEF
    ctx.settings.llm.provider = "stub"

    MapOntologyStage().run(doc, ctx)
    assert admin.canonical_key == "is_pl__general_and_admin_expenses"
    assert subtotal.canonical_key == "is_pl__net_operating_profit"
    assert unknown.canonical_key is None

    ResidualStage().run(doc, ctx)
    assert unknown.canonical_key == "is_pl__other_operating_expenses"


def _fact(value: int) -> ExtractedValue:
    return ExtractedValue(value=value, value_raw=value, basis=Basis.CONSOLIDATED,
                          period_label="current", provenance=Provenance(page_index=1))


def _output_note_split(*, child_already_on_face: bool = False):
    ontology = _ontology()
    parent_key = "bs_ca__trade_and_other_receivables"
    child_key = "bs_ca__trade_receivables_gross"
    parent = LineItem(source_label="Trade and other receivables", canonical_key=parent_key,
                      ordinal=0, note_number="15", section_hint="CURRENT ASSETS",
                      note_refs=[NoteRef(raw="15", numbers=["15"])])
    parent.set_value(_fact(150))
    note = NotesTable(note_number="15", source_pages=[1])
    child = NoteItem(raw_label="Trade Receivables(Gross)")
    child.set_value(_fact(100))
    residual = NoteItem(raw_label="Unclassified note balance")
    residual.set_value(_fact(50))
    note.items = [child, residual]
    line_items = [parent]
    if child_already_on_face:
        face_child = LineItem(source_label="Trade Receivables(Gross)", canonical_key=child_key,
                              ordinal=1, section_hint="CURRENT ASSETS")
        face_child.set_value(_fact(100))
        line_items.append(face_child)
    doc = DocumentModel(filename="f.pdf", line_items=line_items, notes=[note],
                        pages=[PageSource(index=0, statement="balance_sheet")])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology, ctx.template = ontology, TEMPLATE_DEF
    matcher = OntologyMatcher(ontology, llm_provider=None)

    added = MapOntologyStage()._split_from_disclosure(doc, ontology, matcher, ctx)
    return doc, ctx, added, parent_key, child_key


def test_output_note_detail_takes_priority_and_residual_keeps_the_face_concept():
    doc, _ctx, added, parent_key, child_key = _output_note_split()

    assert added == 2
    parent = doc.line_items[0]
    assert parent.canonical_key is None
    published = {row.canonical_key: next(iter(row.values.values())).value
                 for row in doc.line_items if row.canonical_key}
    assert published == {child_key: 100, parent_key: 50}
    assert doc.notes[0].items[0].canonical_key == child_key
    assert doc.notes[0].items[1].canonical_key == parent_key


def test_note_detail_never_duplicates_a_concept_already_present_on_the_face():
    doc, ctx, added, parent_key, child_key = _output_note_split(child_already_on_face=True)

    assert added == 0
    assert doc.line_items[0].canonical_key == parent_key
    assert sum(row.canonical_key == child_key for row in doc.line_items) == 1
    assert any("split_declined" in line for line in ctx.logs)


def test_no_alias_steals_another_concepts_exact_label_within_a_statement():
    ontology = _ontology()
    labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for mapping in ontology.mappings:
        statement = getattr(mapping.statement, "value", mapping.statement) or ""
        labels[(statement, normalize_label(mapping.label))].add(mapping.canonical_key)

    shadows = []
    for mapping in ontology.mappings:
        statement = getattr(mapping.statement, "value", mapping.statement) or ""
        for alias in mapping.aliases:
            owners = labels[(statement, normalize_label(alias))] - {mapping.canonical_key}
            if owners:
                shadows.append((mapping.canonical_key, alias, sorted(owners)))

    assert shadows == []


def test_no_alias_is_a_fragment_of_the_workbook_that_authored_it():
    """AN ALIAS IS A CAPTION A FILING PRINTS, and the rulebook was imported from a spreadsheet whose
    cells also hold the extraction INSTRUCTIONS. Six of those came across as aliases —

        "] in the whole Balance Sheet and all Notes (except for notes"
        ")\nFind 2: Sum of items named in 1 included in Note"
        ") include in"                    (on three separate payables/receivables concepts)

    — and an alias like that cannot match anything on purpose; it can only match by accident, at
    the priority its concept carries. An unbalanced bracket, or a newline, is what they have in
    common and no printed caption does: every legitimate parenthetical alias in this rulebook —
    "(Payments for) Purchase of property, plant and equipment" and its two dozen siblings — closes
    what it opens.
    """
    offenders = [
        (mapping.canonical_key, alias)
        for mapping in _ontology().mappings
        for alias in dict.fromkeys(list(mapping.aliases)
                                   + [a for al in mapping.aliases_i18n.values() for a in al])
        if "\n" in alias or _bracket_depth_ever_negative(alias) or _brackets_left_open(alias)
        or _WORKBOOK_INSTRUCTION.search(alias)
    ]

    assert offenders == []


# …and the workbook's own words, which no printed caption uses. "1st priority" / "2nd priority"
# ordered the instructions the spreadsheet gave a human extractor; ``Find 1:`` / ``Find 2:`` named
# their steps. Two of these survived the bracket test above by being balanced prose.
_WORKBOOK_INSTRUCTION = re.compile(r"\b(?:1st|2nd|3rd)\s+priority\b|\bFind\s+\d\s*:|"
                                   r"\bSum of items named\b", re.IGNORECASE)


def _bracket_depth_ever_negative(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch in "(（[［":
            depth += 1
        elif ch in ")）]］":
            depth -= 1
            if depth < 0:
                return True
    return False


def _brackets_left_open(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch in "(（[［":
            depth += 1
        elif ch in ")）]］":
            depth = max(0, depth - 1)
    return depth > 0


def test_no_caption_of_an_asset_is_also_a_caption_of_an_overdraft():
    """An overdraft is the LIABILITY that cash is netted against, and it carried "Cash" and
    "Bank deposits" as aliases at match_priority 81 against bs_ca__cash_in_hand_and_at_banks at 79.
    A balance sheet whose caption is just "Cash" therefore resolved to overdrafts wherever no
    current-asset banner was in force to stop it — a condensed statement, a note table, a
    spreadsheet upload.

    Named narrowly rather than as a blanket asset/liability invariant, because the balance sheet
    really does print one caption on both sides: a derivative, an option, a swap and an entrusted
    loan are an asset or a liability according to their fair value, and the section banner is the
    only thing that tells those apart — which is what the section gate is for.
    """
    matcher = OntologyMatcher(_ontology(), llm_provider=None)

    unbannered = {label: matcher.match(label, statement="balance_sheet").canonical_key
                  for label in ("Cash", "Bank deposits", "Cash and cash equivalents")}

    assert unbannered == {"Cash": "bs_ca__cash_in_hand_and_at_banks",
                          "Bank deposits": "bs_ca__cash_in_hand_and_at_banks",
                          "Cash and cash equivalents": "bs_ca__cash_in_hand_and_at_banks"}


def test_a_carve_out_never_carries_the_caption_of_the_line_it_is_carved_out_of():
    """THE WEALTH-MANAGEMENT CARVE-OUTS TOOK THEIR PARENTS' CAPTIONS, at a higher priority than the
    parents. Each *_from_* concept is a carve-out OUT of a printed line — its own derivation says
    "run the extraction logic of [the parent] first" — so it is computed, never claimed by the
    parent's caption. Measured on the shipped rulebook before this was corrected:

        "Cash and cash equivalents" -> bs_ca__wealth_management_products_cp_from_cash_equ   (81)
        "Cash"                      -> the same                                            (79 lost)
        银行结余及现金 / 现金及现金等价物  -> the same
        "Trade and other receivables", 其他应收款项 -> ..._cp_from_receivables
        其他非流动金融资产 / 衍生金融工具   -> ..._ltp_from_secur_and_fincl

    which is the most common caption on any balance sheet, in either language, resolving to a
    wealth-management product — and it reached the English/HKEX path exactly as it reached the
    mainland one. What the concepts keep is what they ARE: "Wealth management products",
    "Bank wealth management products", and 理财产品 / 银行理财产品, which the rulebook did not have
    at all and which 002273, 002004 and 688008 all print.
    """
    parents = {
        "cash_equ": ("Cash and cash equivalents", "bs_ca__cash_in_hand_and_at_banks"),
        "receivables": ("Trade and other receivables", "bs_ca__trade_and_other_receivables"),
        "secur_and_fincl": ("交易性金融资产", "bs_ca__secur_and_other_fincl_assets_cp"),
    }
    matcher = OntologyMatcher(_ontology(), llm_provider=None)

    reached = {name: matcher.match(caption, statement="balance_sheet",
                                   section="current assets").canonical_key
               for name, (caption, _want) in parents.items()}
    carve_outs = {name: matcher.match(caption, statement="balance_sheet",
                                      section="current assets").canonical_key
                  for name, caption in (("wmp", "Wealth management products"),
                                        ("zh", "理财产品"))}

    assert reached == {name: want for name, (_c, want) in parents.items()}
    assert set(carve_outs.values()) == {"bs_ca__wealth_management_products_cp_from_secur_and_fincl"}


def test_each_mapping_has_unique_normalized_aliases():
    duplicates = []
    for mapping in _ontology().mappings:
        normalized = [normalize_label(alias) for alias in mapping.aliases]
        if len(normalized) != len(set(normalized)):
            duplicates.append(mapping.canonical_key)

    assert duplicates == []

def test_the_two_assembled_depreciation_charges_are_declared_magnitudes():
    """The shipped template says which members arrive as magnitudes, and only those two do.

    Both charges are ASSEMBLED rather than read off the face — the operating share out of the
    PBT note, the cost-of-sales share out of the segment or PPE note — so they are written after
    `normalize` carrying the magnitude they were summed from, while every cost line beside them
    in these two formulas arrives negative. Left undeclared, each subtotal moves by TWICE the
    charge, which on the sample filing is over a billion HKD on operating profit alone.

    Pinned on the shipped definition rather than the generator because the template is what the
    run reads: a declaration lost in a regeneration is a silent one-billion error.
    """
    nodes = calculated_nodes(TEMPLATE_DEF)

    declared = {key: node["rollup"]["cost_magnitude_children"]
                for key, node in nodes.items()
                if node["rollup"].get("cost_magnitude_children")}
    assert declared == {
        "is_pl__total_cost_of_sales": ["is_pl__deprec_and_impairment_cos"],
        "is_pl__net_operating_profit": ["is_pl__deprec_and_impairment_oper_exp"],
    }
    # A name that is not a member of its own rollup adjusts nothing while reading as if it did.
    for key, magnitudes in declared.items():
        assert set(magnitudes) <= set(nodes[key]["rollup"]["children"]), key
    # And the residual rollups these two concepts also feed must NOT be adjusted: those subtract
    # the charge from a reported parent, where its arrival sign is the parent's own.
    for key, node in nodes.items():
        if node["rollup"].get("reported_total_key"):
            assert not node["rollup"].get("cost_magnitude_children"), key
