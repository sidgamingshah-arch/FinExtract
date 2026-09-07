from __future__ import annotations

import pytest

from app.core.models.enums import MappingMethod
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.mapping import OntologyMatcher, normalize_label


def _ontology() -> OntologyDefinition:
    return OntologyDefinition(
        ontology_key="test",
        target_template_key="t",
        mappings=[
            OntologyMapping(
                canonical_key="assets.current.cash",
                aliases=["Cash and cash equivalents", "Cash & bank balances"],
                keyword_hints=["cash"],
                exclude_hints=["restricted cash"],
            ),
            OntologyMapping(
                canonical_key="assets.current.receivables",
                aliases=["Trade receivables", "Trade and other receivables"],
            ),
        ],
    )


def test_normalize_label():
    assert normalize_label("Cash & Bank  Balances!") == "cash bank balances"


# --- what normalisation drops before the exact tier sees a caption -------------------------------
#
# Six things are stripped ahead of the punctuation fold, each because the fold turns the printed
# decoration into a SPACE and leaves what was inside it behind as a WORD — so the caption gains a
# token no alias carries and the exact tier misses a concept the rulebook already names. They are
# on the hot path of the entire mapper, which is the reason each has its own case here: the risk
# they carry is not that they fail to fire but that they fire on a caption that meant something.

@pytest.mark.parametrize("printed,expected", [
    # A QUOTED ABBREVIATION GLOSS: the short name a filing introduces for a term it just wrote out.
    ('PRC corporate income tax (“CIT”)', "prc corporate income tax"),
    ("PRC land appreciation tax (\"LAT\")", "prc land appreciation tax"),
    ("中國企業所得稅（「企業所得稅」）", "中国企业所得税"),
    # ...and the parentheticals that are NOT glosses, which carry meaning and must survive. None is
    # quoted, which is exactly what tells them apart.
    ("Profit/(loss) before tax", "profit loss before tax"),
    ("Credited/(charged) to profit or loss", "credited charged to profit or loss"),
    # A PRINTED NOTE CITATION: a pointer to where the detail lives, never part of the name.
    ("Trade receivables (note 15)", "trade receivables"),
    ("Depreciation of right-of-use assets (note 16(b))", "depreciation of right of use assets"),
    ("Right-of-use assets 16(a)", "right of use assets"),
    ("受限制現金（附註12）", "受限制现金"),
    ("Note 15: Trade receivables", "trade receivables"),
    # A citation truncated mid-word by row reconstruction is still a citation.
    ("Deferred tax credited for the year (note", "deferred tax credited for the year"),
    # ...but an unbracketed "notes <number>" is NOT one, and this is the case that regressed: with
    # optional brackets and no anchor, "Senior notes 2025" normalised to "senior 5" — the head noun
    # deleted and a token fabricated, on a caption four shipped concepts carry aliases for.
    ("Senior notes 2025", "senior notes 2025"),
    ("Convertible notes 2024", "convertible notes 2024"),
    ("Senior notes and domestic bonds", "senior notes and domestic bonds"),
    ("Notes payable", "notes payable"),
    # A BRACKETED BARE NUMBER: what label_segments leaves in the Latin half when it splits a
    # bilingual caption by script and the Chinese citation's 附註 goes with the Han half.
    ("Deferred tax credited for the year (32)", "deferred tax credited for the year"),
    # ...and the sub-item letters that DO distinguish captions, which are not numbers.
    ("Pledged deposits (a)", "pledged deposits a"),
    # A MAINLAND STATEMENT'S OWN LINE NUMBERING, and the 其中：/加：/减： markers it uses for a
    # component of the line above. The CAS face format numbers its statement-level lines; the
    # number names the line's POSITION, never the concept. Left in place the punctuation fold
    # turns 一、营业总收入 into "一 营业总收入" and no alias in any rulebook carries that token —
    # measured against the 57 face captions CAS prescribes, 45 resolved bare and 0 resolved
    # prefixed.
    ("一、营业总收入", "营业总收入"),
    ("二、营业总成本", "营业总成本"),
    ("三、营业利润", "营业利润"),
    ("十一、其他综合收益", "其他综合收益"),
    ("其中：营业收入", "营业收入"),
    ("加：营业外收入", "营业外收入"),
    ("减：库存股", "库存股"),
    ("（一）应收账款", "应收账款"),
    ("1、营业收入", "营业收入"),
    # Traditional script reaches the same rule, because the fold to Simplified runs first.
    ("減：預期信用損失準備", "预期信用损失准备"),
    # A NOTE REFERENCE IS PRINTED IN THE SAME SHAPE AS AN ENUMERATOR — 七、61, 七、70 — and one
    # arrives as a row's entire label when the caption beside it is lost. Stripping there would
    # leave a bare number to be matched against the rulebook, promoting an unmatchable label to a
    # plausibly matchable one. It must stay unmatchable, so the digit lookahead refuses it.
    ("七、70", "七 70"),
    ("七、61", "七 61"),
    # 减 inside a word is not a 减： marker, and this is the caption that would be gutted if the
    # colon were optional: 资产减值损失 is a concept in its own right.
    ("资产减值损失", "资产减值损失"),
    ("信用减值损失", "信用减值损失"),
    # THE CAS SIGN-CONVENTION PARENTHETICAL, which the caption carries about ITSELF: it says a
    # loss is printed with a minus sign. Measured on 四创电子 (11077098), 23 captions carry it and
    # it defeated the match on every one — the whole bottom of the income statement was
    # unreachable while 营业利润 / 利润总额 / 净利润 sat in the rulebook already aliased.
    ("三、营业利润（亏损以“－”号填列）", "营业利润"),
    ("四、利润总额（亏损总额以“－”号填列）", "利润总额"),
    ("五、净利润（净亏损以“－”号填列）", "净利润"),
    ("资产减值损失（损失以“-”号填列）", "资产减值损失"),
    ("投资收益（损失以“-”号填列）", "投资收益"),
    ("公允价值变动收益（损失以“－”号填列）", "公允价值变动收益"),
    ("存货的减少（增加以“－”号填列）", "存货的减少"),
    # The filing breaks the line INSIDE the parenthetical, so the space is load-bearing.
    ("递延所得税资产减少（增加以“－” 号填列）", "递延所得税资产减少"),
    # THE ORPHANED TAIL of the caption above, left on the front of this one by a wrap merge.
    # A closing bracket with nothing on the line that opened it cannot begin a real caption.
    ("填列） 三、营业利润（亏损以“－”号填列）", "营业利润"),
    ("“－”号填列） 其他", "其他"),
    # ...and the parentheticals that are NOT sign notes, which name the concept and must survive.
    ("实收资本（或股本）", "实收资本 或股本"),
    ("所有者权益（或股东权益）合计", "所有者权益 或股东权益 合计"),
    # A LEADING PARENTHETICAL THAT OPENS PROPERLY IS NOT AN ORPHAN. `[^（(]` cannot cross an
    # opening bracket, so each of these is left for the rules that do handle it.
    ("（一）综合收益总额", "综合收益总额"),
    ("Profit/(loss) before tax", "profit loss before tax"),
    ("(Loss)/profit for the year", "loss profit for the year"),
    # A Han character is required in the orphan, which keeps the rule off the English path.
    ("b) Trade receivables", "b trade receivables"),
    ("Deprec & Impairment(COS)", "deprec impairment cos"),
])
def test_a_printed_decoration_is_dropped_but_a_meaning_is_not(printed, expected):
    assert normalize_label(printed) == expected


def test_exact_match_early_exits():
    m = OntologyMatcher(_ontology())
    r = m.match("Cash and cash equivalents")
    assert r.canonical_key == "assets.current.cash"
    assert r.method == MappingMethod.EXACT and r.confidence == 1.0


def test_a_typo_is_left_for_a_human_rather_than_guessed():
    """There is no string-similarity tier: a misspelling maps to NOTHING and says so.

    It used to be absorbed, which is the same mechanism that filed "Profit before exceptional items
    and tax" as ``profit_before_tax`` on the shipped rulebook — two subtotals differing by exactly
    the exceptional items, so the figure landed on the wrong line and the statement still tied. A
    caption nothing can place is now a visible gap. The remedy is authored, not inferred: an alias
    for the wording the filer actually prints, or a rule hint."""
    m = OntologyMatcher(_ontology())
    r = m.match("Trade recievables")  # misspelled
    assert r.canonical_key is None and r.needs_review

    ont = _ontology()
    next(x for x in ont.mappings
         if x.canonical_key == "assets.current.receivables").aliases.append("Trade recievables")
    fixed = OntologyMatcher(ont).match("Trade recievables")
    assert fixed.canonical_key == "assets.current.receivables"
    assert fixed.method is MappingMethod.EXACT


def test_unmatched_routes_to_review():
    m = OntologyMatcher(_ontology())
    r = m.match("Deferred tax liability (net)")
    assert r.canonical_key is None or r.needs_review


def test_exclude_hints_veto_a_concept_across_every_tier():
    """The field is named exclude and the ontology editor presents it as "never map a caption
    like this here", so it has to hold everywhere — not only in the rule tier. While it applied
    to rules alone, an excluded caption still arrived via fuzzy or an alias, and an analyst
    adding the exclusion to fix a mis-mapping would see nothing change.
    """
    from app.schemas.ontology import OntologyDefinition, OntologyMapping
    from app.services.mapping import OntologyMatcher

    onto = OntologyDefinition(
        ontology_key="t", target_template_key="t", locale="en",
        mappings=[
            OntologyMapping(
                canonical_key="cf_cash_flow_from_investing_activities__disposal_of_subsidiaries",
                label="Disposal of subsidiaries",
                aliases=["Disposal of subsidiaries"],
                # A P&L add-back shares the wording but is not a movement of cash.
                exclude_hints=[r"\b(gain|loss)s? on disposal\b"],
            ),
        ],
    )
    m = OntologyMatcher(onto, locale="en")

    # The caption the concept is for still maps.
    assert m.match("Disposal of subsidiaries").canonical_key.endswith("disposal_of_subsidiaries")
    # The excluded wording does not — via the alias/exact tier...
    assert m.match("Gain on disposal of subsidiaries").canonical_key is None
    # ...nor via fuzzy, which is where it used to slip through.
    assert m.match("Gain on disposal of subsidiaries, net").canonical_key is None


def test_a_hint_typed_in_the_case_a_human_uses_still_fires():
    """THE DEFECT THIS CLOSES, found on a reviewer's own edits and silent by construction.

    A hint is a REGEX, and it is searched against a caption the matcher has already lowercased while
    the pattern is taken verbatim from the rulebook. So a hint typed the way anyone writes a caption
    — "Finance Cost", "Non-current Assets", "Short term" — matched nothing, ever. Every one of the
    415 hints shipped in the rulebook happens to be lowercase, so no shipped behaviour was wrong and
    no test failed; the trap was waiting for the next person to add one. A reviewer added thirteen
    through the ontology workbook and hit it thirteen times out of thirteen: each exclusion looked
    accepted, appeared in the workbook, and did nothing.

    Both hint families are asserted, because they are read at two different sites.
    """
    onto = OntologyDefinition(
        ontology_key="t", target_template_key="t", locale="en",
        mappings=[
            OntologyMapping(
                canonical_key="pl_expenses__others",
                label="Other expenses",
                # The second alias is the one the exclusion has to beat. Without it the excluded
                # caption reaches no tier of this concept and the assertion below would hold for the
                # wrong reason — the exclusion never being consulted at all.
                aliases=["Other expenses", "Finance costs and other items"],
                # Capitalised exactly as a reviewer typed it into the workbook.
                exclude_hints=["Finance Cost"],
            ),
            OntologyMapping(
                canonical_key="bs_current_liabilities__bills_payable",
                label="Bills payable",
                # No alias to match on: the regex hint is the only route to this concept.
                regex_hints=[r"Bills? Payable"],
            ),
        ],
    )
    m = OntologyMatcher(onto, locale="en")

    # The concept still claims the caption it is for.
    assert m.match("Other expenses").canonical_key == "pl_expenses__others"
    # …and the capitalised exclusion is honoured against a caption printed in ordinary sentence case,
    # which is the pairing that failed: pattern "Finance Cost" against text "finance costs …". This
    # caption is an exact alias of the concept, so only the exclusion can keep it out.
    assert m.match("Finance costs and other items").canonical_key != "pl_expenses__others"
    # The other site. A capitalised regex hint reaches its concept.
    assert m.match("Bills payable").canonical_key == "bs_current_liabilities__bills_payable"
