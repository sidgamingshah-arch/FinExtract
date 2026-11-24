"""A WHOLE-PDF FIGURE IS FOUND AGAIN WHERE THE RULES READ ITS PAGE, AND HELD TO THE CHECKS EVERY MODEL
ANSWER IS HELD TO (`services.whole_pdf_locate`, `services.answer_checks`).

Before, the whole-PDF route checked only that a figure's number was printed somewhere on the page
it cited. So the failures the line-item route had already been fixed for came back on it, each
"confirmed on the page": the audit reproduced them with hand-made readings (scratch sim.py) —
湖北能源's parent-company Sales from the face's total 营业收入 (40,617,220.64, all of it 其他业务),
contingent liabilities with US-dollar letters of credit added to yuan (330,047,162.11), a 万元
key-management row added to figures in 元. And the column was never asked: a Level 2 or total
figure given as Level 3, a figure filed under the other year.

Each test here drives the REAL pipeline, extraction included, on the real filing's pages, with the
shipped stand-in answering from a SCRIPT (`tests.whole_pdf_standin`): a scripted answer shows what
the pipeline does with that answer, never what a model would answer. Where a test says what was
published before, it runs the same script with the new checks switched off (`checks_off`).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import whole_pdf_locate as L
from tests.whole_pdf_standin import filing_path, rows_of, run_whole_pdf, script_figure, slot

CP = "bs_ca__secur_and_other_fincl_assets_cp"
FVTPL = "sub__fa_cp_fvtpl_note_total"
FVTOCI = "sub__fa_cp_fvtoci_note_total"
OTHER = "sub__fa_cp_other_fincl_assets_note_total"
FACE_CP = "sub__cp_face_trading_fincl_assets"
L3_PRINTED = "sub__fa_cp_level_3_printed_total"
L3 = "sub__fa_cp_level_3_total"
KMP = "sub__rpt_key_management"
RPT = "notes__related_party_transactions"
CL_PART = "sub__cl_contingency_note_exposures"
CL = "notes__contingent_liabilities"
FACE_REVENUE = "sub__face_principal_revenue"
SALES = "is_pl__sales_revenues"

_R = lambda a, b: list(range(a, b))  # noqa: E731 - 0-based page ranges, as the Page Scope stores them

#: (filing, 0-based pages read and sent) per scenario family.
FILINGS = {
    "883": ("000883_hubei_energy_2024_prc", _R(107, 115) + [219, 267, 268, 283]),
    "990": ("600990_sun_create_2024_prc", _R(70, 76) + _R(186, 190)),
    "688": ("688008_montage_2024_prc", _R(148, 152) + _R(180, 183) + _R(217, 220)
            + _R(236, 246) + _R(251, 254)),
    "319": ("300319_maijie_2024_prc", _R(96, 100) + _R(144, 147) + _R(175, 178)),
    "kaming": ("hkex_kaming_2025_26", _R(62, 66) + _R(127, 130)),
    "1966": ("hkex_1966_ar", _R(101, 106) + _R(195, 199) + _R(213, 216) + _R(253, 256)),
    "709": ("000709_hesteel_2024_prc", _R(80, 85) + _R(196, 199)),
    "1966settle": ("hkex_1966_ar", _R(101, 106) + [213]),
    "411": ("688411_hyperstrong_2024_prc", _R(172, 184)),
}

#: 000883 十六、2 (p269): seven 保函 in 元 and two letters of credit in 美元.
_CL_ROWS = [("保函", "25000000.00"), ("保函", "120000000.00"), ("保函", "145000000.00"),
            ("保函", "2110000.00"), ("保函", "1000000.00"), ("保函", "20086377.00"),
            ("保函", "10230300.00"), ("信用证", "6568662.00"), ("信用证", "51823.11")]
_688_FACE = {FACE_CP: [script_figure("1783494750.68", page=150, caption="交易性金融资产"),
                       script_figure("1703266947.76", page=150, caption="交易性金融资产",
                                     period="prior")]}
_688_FVTPL = [script_figure("1783494750.68", page=182,
                            caption="以公允价值计量且其变动计入当期损益的金融资产"),
              script_figure("1703266947.76", page=182,
                            caption="以公允价值计量且其变动计入当期损益的金融资产", period="prior")]

SCRIPTS = {
    "883": {"units": "元", "lines": {
        # The parent company's face 营业收入 — its revenue note prints only 其他业务.
        FACE_REVENUE: [script_figure("40617220.64", page=114, caption="一、营业收入",
                                     basis="standalone")],
        CL_PART: [script_figure("330047162.11", page=269, caption="保函及信用证", parts=_CL_ROWS)]}},
    "990": {"units": "元", "lines": {
        KMP: [script_figure("476.37", page=189, caption="关键管理人员报酬"),
              script_figure("421.67", page=189, caption="关键管理人员报酬", period="prior")]}},
    "688": {"units": "元", "lines": {
        **_688_FACE,
        # 十四、5 prints 单位：万元.
        KMP: [script_figure("5849.45", page=242, caption="关键管理人员报酬"),
              script_figure("4227.71", page=242, caption="关键管理人员报酬", period="prior")],
        # 十三、1 total row: Level 1 176,402,753.81 | Level 2 2,204,606,831.44 | Total; Level 3 blank.
        # And p240's sentence on a 2023 equity investment moved OUT of Level 3, as prior Level 3.
        L3_PRINTED: [script_figure("2204606831.44", page=239, caption="持续以公允价值计量的资产总额"),
                     script_figure("136212831.30", page=240, caption="第三层次转入第一层次",
                                   period="prior")],
        # 七、2: the FVTPL row, and its 其中：结构性存款 given to the "other" part as well.
        FVTPL: _688_FVTPL,
        OTHER: [script_figure("1629362905.47", page=182, caption="结构性存款")]}},
    "688-year": {"units": "元", "lines": {
        **_688_FACE,
        # 七、2's 期初余额 given as the current year.
        FVTPL: [script_figure("1703266947.76", page=182, caption="合计")]}},
    "688-face": {"units": "元", "lines": {
        **_688_FACE,
        FVTPL: _688_FVTPL,
        # The face 交易性金融资产 row on two statement lines as well: inside a sum, and whole.
        "bs_ca__other_current_assets": [script_figure(
            "1869480857.98", page=150, caption="其他流动资产",
            parts=[("交易性金融资产", "1783494750.68"), ("其他流动资产", "85986107.30")])],
        "bs_ca__short_term_securities": [script_figure("1783494750.68", page=150,
                                                       caption="交易性金融资产")],
        # A Level 3 column printed blank, answered as nil.
        L3_PRINTED: [script_figure("0", page=239, caption="持续以公允价值计量的资产总额")]}},
    "319": {"units": "元", "lines": {
        # 七、2: the row and the 其中 rows that break it down, added together.
        FVTPL: [script_figure("862957777.62", page=146, caption="交易性金融资产", parts=[
            ("以公允价值计量且其变动计入当期损益的金融资产", "431478888.81"),
            ("结构性理财产品", "373011600.33"), ("权益工具投资", "58467288.48")])]}},
    "kaming": {"units": "HK$'000", "lines": {
        # Note 22: the interest rate swaps' NOTIONAL amount.
        FVTPL: [script_figure("950000", page=129, caption="Interest rate swaps (note 30(c)(i))")]}},
    "1966": {"units": "RMB'000", "lines": {
        # Note 15: the INVESTMENT PROPERTIES' Level 3 table, Total 33,880,100 / 39,216,242.
        L3_PRINTED: [script_figure("39216242", page=198, caption="Total", period="prior")]}},
    "1966settle": {"units": "RMB'000", "lines": {
        # The face row 'Prepayments, other receivables and other assets' as Other Current Assets,
        # and note 25's other receivables, which Other Receivables (CP) is computed from.
        "bs_ca__other_current_assets": [
            script_figure("15062723", page=104, caption="Prepayments, other receivables and other "
                                                        "assets 預付款項、其他應收款項及其他資產"),
            script_figure("13315135", page=104, caption="Prepayments, other receivables and other "
                                                        "assets 預付款項、其他應收款項及其他資產",
                          period="prior")],
        "sub__cp_other_receivables_net_row": [
            script_figure("5818375", page=214, caption="Other receivables 其他應收款項"),
            script_figure("7028687", page=214, caption="Other receivables 其他應收款項",
                          period="prior")]}},
    "411": {"units": "元", "lines": {
        # 五、11 金融资产转移的确认依据及计量方法 — an accounting POLICY — ends a line on "(3)",
        # which the reader took for a row of -3.
        FVTOCI: [script_figure("-3", page=183, caption="(3)")]}},
    "709": {"units": "元", "lines": {
        # 十三、1: （一）应收款项融资 at Level 3 — a receivables row of the balance sheet.
        FVTOCI: [script_figure("909834646.75", page=198, caption="（一）应收款项融资")]}},
}


def _present(family: str) -> bool:
    return filing_path(FILINGS[family][0]).exists()


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One run per (script, checks on/off), cached for the module."""
    done: dict[tuple, tuple] = {}

    def _run(name: str, *, checks_off: bool = False):
        family = name.split("-")[0]
        if not _present(family):
            pytest.skip(f"{FILINGS[family][0]}.pdf not present")
        if (name, checks_off) not in done:
            stem, pages = FILINGS[family]
            with pytest.MonkeyPatch.context() as mp:
                if checks_off:
                    from app.stages import note_sourced as stage_ns
                    mp.setattr(L.Judge, "judge_one", lambda self, r: r)
                    mp.setattr(L.Judge, "settle", lambda self, merged: None)
                    # …and the face settlement's old test of a face row: derived from nothing.
                    mp.setattr(stage_ns, "_printed_face_row",
                               lambda li: not li.is_computed and not li.derivation)
                done[(name, checks_off)] = run_whole_pdf(
                    mp, tmp_path_factory.mktemp(name + ("-off" if checks_off else "")), stem,
                    SCRIPTS[name], pages=pages)
        return done[(name, checks_off)]
    return _run


def _value(doc, key, basis="consolidated", period="current"):
    _row, ev = slot(doc, key, basis, period)
    return None if ev is None else Decimal(str(ev.value))


def _flags(doc, key) -> list[str]:
    return [f for row in rows_of(doc, key) for f in row.confidence.flags]


def _slot_flags(doc, key, basis="consolidated", period="current") -> list[str]:
    _row, ev = slot(doc, key, basis, period)
    return [] if ev is None else list(ev.confidence.flags)


# ── the line-item route's refusals, now on this route ──────────────────────────────────────────

def test_the_face_total_revenue_is_not_the_parent_companys_principal_revenue(run):
    """Finding cas_sales_total_revenue / excluded_face_citations:whole_pdf. The face part given
    the parent's 营业收入 is refused by the part's own caption exclusions, so Sales (Revenues)
    for the parent company is left unanswered instead of publishing 40,617,220.64 of 其他业务."""
    doc, ctx, _rec = run("883")
    assert _value(doc, FACE_REVENUE, "standalone") is None
    assert _value(doc, SALES, "standalone") is None
    assert any(f.startswith("whole_pdf:refused:excluded_caption:standalone:current:40617220.64")
               for f in _flags(doc, FACE_REVENUE))
    assert any("NOT written — every figure it rests on was refused" in line for line in ctx.logs)
    off, _ctx, _rec = run("883", checks_off=True)
    assert _value(off, SALES, "standalone") == Decimal("40617220.64")


def test_dollar_letters_of_credit_are_not_added_to_yuan(run):
    """Finding foreign_currency_prose:whole_pdf. The two 信用证 sentences print 美元 beside their
    amounts, read off the page's own text; they are refused and the 保函 in 元 stay:
    323,426,677.00, not 330,047,162.11."""
    doc, _ctx, _rec = run("883")
    assert _value(doc, CL) == Decimal("323426677.00")
    refused = [f for f in _slot_flags(doc, CL_PART) if f.startswith("whole_pdf:refused:")]
    assert sorted(refused) == sorted(
        [f"whole_pdf:refused:foreign_currency:consolidated:current:{a}"
         for a in ("6568662.00", "51823.11")])
    # The trail lists the refused rows, not counted, with the rule.
    trail = next(iter(slot(doc, CL_PART)[0].derivation.values()))
    left_out = [i for i in trail["inputs"] if not i["counted"]]
    assert {i["refused"] for i in left_out} == {"foreign_currency"}
    off, _ctx, _rec = run("883", checks_off=True)
    assert _value(off, CL) == Decimal("330047162.11")


def test_a_wan_yuan_key_management_row_is_put_into_yuan(run):
    """Finding in_statement_scale:whole_pdf on 四创电子 (the dea5644 case): 十四、5 (7) prints
    单位：万元, the figure given as printed is put into the statements' 元 — 4,763,700.00 /
    4,216,700.00, the rules route's own reading — and Related Party Transactions adds 元 to 元."""
    doc, _ctx, _rec = run("990")
    assert _value(doc, KMP) == Decimal("4763700.00")
    assert _value(doc, KMP, period="prior") == Decimal("4216700.00")
    assert _value(doc, RPT) == Decimal("4763700.00")
    assert any(f.startswith("whole_pdf:rescaled:") and f.endswith(":476.37->4763700.00")
               for f in _slot_flags(doc, KMP))
    _row, ev = slot(doc, KMP)
    assert ev.value_raw == Decimal("4763700.00")


def test_688008_key_management_pay_matches_the_rules_route(run):
    """Finding note_units_not_rescaled: 688008 p242 prints 5,849.45 / 4,227.71 in 万元; the rules
    route publishes 58,494,500.00 / 42,277,100.00, and so does this route now."""
    doc, _ctx, _rec = run("688")
    assert _value(doc, KMP) == Decimal("58494500.00")
    assert _value(doc, KMP, period="prior") == Decimal("42277100.00")
    assert _value(doc, RPT) == Decimal("58494500.00")
    off, _ctx, _rec = run("688", checks_off=True)
    assert _value(off, KMP) == Decimal("5849.45")


def test_a_derivatives_notional_is_refused_on_a_securities_part(run):
    """Finding derivative_refusal:whole_pdf on 佳明: note 22's interest rate swaps (notional
    950,000) are a derivative, which the FVTPL part excludes by its own row vetoes."""
    doc, ctx, _rec = run("kaming")
    assert _value(doc, FVTPL) is None
    assert _value(doc, CP) is None
    assert any(f == "whole_pdf:refused:derivative:consolidated:current:950000"
               for f in _flags(doc, FVTPL))
    assert any("this line excludes derivatives" in line for line in ctx.logs)


def test_a_qizhong_row_is_not_added_to_the_row_it_breaks_down(run):
    """Finding qizhong_breakdowns / earlier_fixes_whole_pdf on 300319 七、2: the row 431,478,888.81
    and its 其中 rows 373,011,600.33 and 58,467,288.48 added together are 862,957,777.62. The
    breakdown is read once (the rules' own reading of the shape, `note_sourced.breakdown_groups`)."""
    doc, _ctx, _rec = run("319")
    assert _value(doc, FVTPL) == Decimal("431478888.81")
    assert _value(doc, CP) == Decimal("431478888.81")
    off, _ctx, _rec = run("319", checks_off=True)
    assert _value(off, FVTPL) == Decimal("862957777.62")


# ── the column the figure is printed in ────────────────────────────────────────────────────────

def test_a_level_2_total_given_as_level_3_is_refused_and_cp_is_the_rules_figure(run):
    """Findings page_check_number_not_column / page_check_is_number_anywhere: 688008's hierarchy
    total row prints 2,204,606,831.44 under 第二层次. Its cell carries no heading in the rules'
    reading, so the column is the one the cells above it are in. Refused, the rules read the blank
    Level 3 column as 0 and CP is 1,783,494,750.68 / 1,703,266,947.76 with missing_level_3 on the
    prior slot only — rules-only exactly. Without the check CP was 0 (CP_ZERO)."""
    doc, ctx, _rec = run("688")
    assert any("REFUSED" in line and "第二层次" in line and L3_PRINTED in line
               for line in ctx.logs)
    assert _value(doc, L3) == 0
    assert _value(doc, CP) == Decimal("1783494750.68")
    assert _value(doc, CP, period="prior") == Decimal("1703266947.76")
    assert "missing_level_3" not in _slot_flags(doc, CP)
    assert "missing_level_3" in _slot_flags(doc, CP, period="prior")
    off, _ctx, _rec = run("688", checks_off=True)
    assert _value(off, L3_PRINTED) == Decimal("2204606831.44")


def test_a_transfer_out_of_level_3_in_a_sentence_is_not_the_prior_years_level_3(run):
    """Finding cas_prior_level3_from_prose: p240 says a 2023 equity investment of 约人民币
    136,212,831.30元 moved from Level 3 to Level 1. Given as prior Level 3 it would take CP prior to
    1,567,054,116.46 and hide missing_level_3; a Level 3 part reads one column of a table, and a
    sentence is in none."""
    doc, _ctx, _rec = run("688")
    assert _value(doc, L3_PRINTED, period="prior") is None
    assert "whole_pdf:refused:column:consolidated:prior:136212831.30" in _flags(doc, L3_PRINTED)
    assert _value(doc, CP, period="prior") == Decimal("1703266947.76")


def test_a_blank_level_3_column_answered_as_nil_stands(run):
    """Findings level3_blank_column_and_columns / blank_level3_whole_pdf: a nil adds nothing and is
    never refused; CP's current slot carries no missing_level_3, as rules-only."""
    doc, _ctx, _rec = run("688-face")
    assert _value(doc, L3_PRINTED) == 0
    assert _value(doc, CP) == Decimal("1783494750.68")
    assert "missing_level_3" not in _slot_flags(doc, CP)


def test_a_figure_printed_in_the_other_years_column_is_filed_there(run):
    """Finding page_check_is_number_anywhere: 1,703,266,947.76 given as the CURRENT FVTPL is printed
    under 七、2's 期初余额 — every cell on the page printing it is in that column — so it is filed
    as the prior year, and the rules read the current year's own figure."""
    doc, ctx, _rec = run("688-year")
    assert _value(doc, FVTPL, period="prior") == Decimal("1703266947.76")
    assert _value(doc, FVTPL) == Decimal("1783494750.68")
    model = [r for r in rows_of(doc, FVTPL) if "whole_pdf:rules_reading" not in r.confidence.flags]
    assert any("whole_pdf:refiled:current->prior" in ev.confidence.flags
               for r in model for ev in r.values.values())
    assert any("is printed in the prior column (期初余额) — filed there" in line
               for line in ctx.logs)


def test_an_investment_property_level_3_total_is_not_financial_assets(run):
    """Finding level3_scope_ip_liabilities on 1966: note 15's investment-property Level 3 total
    39,216,242 given as the prior printed Level 3 would take CP prior from 53,434 to 0."""
    doc, _ctx, _rec = run("1966")
    assert _value(doc, L3_PRINTED, period="prior") is None
    assert "whole_pdf:refused:not_a_financial_asset:consolidated:prior:39216242" in _flags(
        doc, L3_PRINTED)
    assert _value(doc, CP) == 0
    assert _value(doc, CP, period="prior") == Decimal("53434")


def test_receivables_financing_is_not_the_current_fvoci_part(run):
    """Finding fvoci_takes_receivables_financing_000709: 十三、1 lists （一）应收款项融资
    909,834,646.75 at Level 3; the balance sheet prints it as a receivables row. The FVOCI part's
    own exclusions now name it, so it is refused, CP stays blank as rules-only, and the rules read
    both Level 3 rows (1,321,518,115.73)."""
    doc, _ctx, _rec = run("709")
    assert _value(doc, FVTOCI) is None
    assert "whole_pdf:refused:excluded_caption:consolidated:current:909834646.75" in _flags(
        doc, FVTOCI)
    assert _value(doc, CP) is None
    assert _value(doc, L3) == Decimal("1321518115.73")


# ── across answers ─────────────────────────────────────────────────────────────────────────────

def test_two_parts_of_cp_do_not_both_take_the_structured_deposits(run):
    """Finding find1_siblings_double_count_whole_pdf: 结构性存款 1,629,362,905.47 is a 其中 row of
    the FVTPL row the FVTPL part holds; given to the "other" part too, CP would count it twice. The
    other part is left empty and says which part holds its figure."""
    doc, _ctx, _rec = run("688")
    assert _value(doc, OTHER) is None
    assert {f"llm_note_row_claimed_by:{FVTPL}", f"llm_note_row_breakdown_of:{FVTPL}",
            "llm_note_row_withheld:1629362905.47"} <= set(_flags(doc, OTHER))
    assert _value(doc, CP) == Decimal("1783494750.68")
    off, _ctx, _rec = run("688", checks_off=True)
    assert _value(off, OTHER) == Decimal("1629362905.47")


def test_the_face_securities_row_is_counted_on_one_line_of_current_assets(run):
    """Finding face_row_without_statement_line: the face 交易性金融资产 row is Securities (CP)'s,
    through its face part, whose aliases name it. Filed also inside Other Current Assets and as
    Short Term Securities, it is taken out of both: OCA keeps its own 其他流动资产 85,986,107.30."""
    doc, _ctx, _rec = run("688-face")
    assert _value(doc, "bs_ca__other_current_assets") == Decimal("85986107.30")
    assert _value(doc, "bs_ca__short_term_securities") is None
    assert ("whole_pdf:refused:face_row_of_another_line:consolidated:current:1783494750.68"
            in _flags(doc, "bs_ca__short_term_securities"))
    assert _value(doc, FACE_CP) == Decimal("1783494750.68")
    assert _value(doc, CP) == Decimal("1783494750.68")


def test_the_model_rows_carry_the_note_references_the_face_prints(run):
    """A figure found on a rules face row takes that row's note references, so the note links, the
    ties and the published notes work as on the rules route (A1 left this to the locating)."""
    doc, _ctx, _rec = run("688")
    row, _ev = slot(doc, FACE_CP)
    assert "七、2" in row.cited_notes()


def test_the_face_settlement_runs_on_the_models_face_rows(run):
    """Finding fix_matrix:settlement_deducted_in: every whole-PDF row carries a trail, so the step
    that takes a computed line's money out of the face row that also prints it found no face row on
    this route and was inert. A model figure read off ONE printed row is that row now. On 1966 the
    face row 'Prepayments, other receivables and other assets' holds note 25's other receivables,
    which Other Receivables (CP) publishes: Other Current Assets is 9,244,348 / 6,286,448, the rules
    route's figures, not 15,062,723 / 13,315,135."""
    doc, ctx, _rec = run("1966settle")
    assert _value(doc, "bs_ca__other_receivables_cp") == Decimal("5818375")
    assert _value(doc, "bs_ca__other_current_assets") == Decimal("9244348")
    assert _value(doc, "bs_ca__other_current_assets", period="prior") == Decimal("6286448")
    assert any(line.startswith("note_sourced:face_settlement: bs_ca__other_current_assets")
               for line in ctx.logs)
    off, _ctx, _rec = run("1966settle", checks_off=True)
    assert _value(off, "bs_ca__other_current_assets") == Decimal("15062723")


def test_a_row_of_an_accounting_policy_note_is_refused(run):
    """Finding fix_matrix:policy_note on 688411: a policy note says how a balance is measured, not
    what it is. The "(3)" list marker of 五、11, read by the rules as a row of -3, given as current
    FVTOCI is refused — the rule the rules reader applies (`line_item_notes.is_policy_note`)."""
    doc, ctx, _rec = run("411")
    assert _value(doc, FVTOCI) is None
    assert "whole_pdf:refused:policy_note:consolidated:current:-3" in _flags(doc, FVTOCI)
    off, _ctx, _rec = run("411", checks_off=True)
    assert _value(off, FVTOCI) == Decimal("-3")
