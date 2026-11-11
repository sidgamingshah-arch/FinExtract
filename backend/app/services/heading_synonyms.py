"""ONE SHARED LIST OF ACCOUNTING SYNONYMS, for finding a line item's notes by their headings.

THE HEADING SEARCH COUNTS WORDS. `line_item_notes.notes_for_line_item` scores a line's note terms
against every note heading by the words they share, weighted by how rare each word is in the
filing's headings. Nothing in it knows that "fixed assets" and "property, plant and equipment" are
one thing — the two share no word and score 0.000 — and a configured line reaches a note headed in
other words only where its author wrote those words into its note terms, line by line.

So the synonyms are written down ONCE, here, by concept, in English, Simplified and Traditional
Chinese. A line whose own term IS one of a concept's spellings is shown, besides the notes its own
search found, the notes whose heading PRINTS another of that concept's spellings (`note_sets`,
`via="synonym"`). Nothing is guessed: every spelling the list can add is on this page, and a reader
can see which one a heading printed.

WHY A LOOKUP AND NOT MORE WORDS IN THE SEARCH, and it is measured. The first version added the
spellings to the line's search words. Over the eighteen reference filings it changed the notes of
388 of 2,826 line-filings and pushed out 98 notes the line's own words had found, and many of its
additions were wrong for a reason the word count cannot see: Chinese is compared in two-character
pieces, so 贸易应付款项 (trade payables) shares 应付 and 付款 with 其他应付款 (other payables) and
长期应付款, and a bare 借款 matched every 短期借款 and 长期借款 heading. A heading that PRINTS the
spelling does not have that problem.

THE RULES:

* A LINE TAKES A CONCEPT ONLY WHEN ONE OF ITS TERMS *IS* ONE OF ITS SPELLINGS — after its measure
  word is set aside ("Net trade receivables" is "trade receivables", 无形资产净额 is 无形资产). A term
  that merely CONTAINS one is about something else: "Profit before income tax" is not income tax,
  "Insurance contract liabilities" are not contract liabilities, 向中央银行借款 is not bank borrowing.
* A HEADING PRINTS A SPELLING as a phrase — its English words consecutively, plurals folded, or its
  Chinese characters in a row, Traditional read as Simplified. A SHORT spelling — one English word,
  or two Chinese characters — only when it is the heading's whole English or whole Chinese text:
  "Revenue" is not DEFERRED REVENUE, 借款 is not 长期借款.
* THE LONGEST SPELLING A HEADING PRINTS DECIDES ITS CONCEPT. 递延所得税资产 prints 所得税 inside
  递延所得税, so it is deferred tax's heading and not income tax's; 保险合同负债 is insurance's and
  not contract liabilities'. Two groups below exist only to say so.
* A SPELLING ANOTHER PART OF THE SAME FIGURE NAMES IS THAT PART'S. The configuration splits some
  figures into parts by the heading their note carries — depreciation on "property and equipment"
  is one part, on "fixed assets" / 固定资产 another, and the parent adds the parts up. So a sibling
  part's own spelling is never added to a line (`extras_by_key`), and the parts stay as split.

WHAT IT CHANGES: which notes a line's model request is shown, AFTER everything the configuration
found and at most `line_item_notes.SYNONYM_CAP` of them. Not the claims that give each note to one
part (`claimed_notes`), not the deterministic route, not the document's note text. Off with
`extraction.llm_note_synonyms`.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from app.services.han import to_simplified

# One tuple per concept: English first, then Simplified, then Traditional. A Traditional spelling
# that reads the same in Simplified is listed for the reader; it is compared as Simplified.
GROUPS: tuple[tuple[str, ...], ...] = (
    ("property, plant and equipment", "property and equipment", "plant and equipment",
     "fixed assets", "tangible fixed assets",
     "固定资产", "物业、厂房及设备", "物业及设备", "物业、机器及设备",
     "固定資產", "物業、廠房及設備", "物業及設備", "物業、機器及設備"),
    ("investment properties", "investment property", "投资性房地产", "投资物业", "投資物業"),
    ("intangible assets", "无形资产", "無形資產"),
    ("right-of-use assets", "right of use assets", "使用权资产", "使用權資產"),
    ("construction in progress", "在建工程"),
    ("goodwill", "商誉", "商譽"),
    ("inventories", "存货", "存貨"),
    ("trade receivables", "accounts receivable", "trade debtors",
     "应收账款", "贸易应收款项", "應收賬款", "應收貿易賬款", "貿易應收款項"),
    ("other receivables", "其他应收款", "其他应收款项", "其他应收账款", "其他應收款", "其他應收款項",
     "其他應收賬款"),
    ("bills receivable", "notes receivable", "应收票据", "應收票據"),
    ("prepayments", "预付款项", "预付账款", "預付款項", "預付賬款"),
    ("trade payables", "accounts payable", "trade creditors",
     "应付账款", "贸易应付款项", "應付賬款", "應付貿易賬款", "貿易應付款項"),
    ("other payables", "其他应付款", "其他应付账款", "其他應付款", "其他應付賬款"),
    ("bills payable", "notes payable", "应付票据", "應付票據"),
    ("contract liabilities", "合同负债", "合約負債"),
    ("lease liabilities", "租赁负债", "租賃負債"),
    ("bank borrowings", "bank loans", "interest-bearing bank borrowings", "银行借款", "银行贷款",
     "銀行借款", "銀行貸款"),
    ("short-term borrowings", "short term borrowings", "短期借款"),
    ("long-term borrowings", "long term borrowings", "长期借款", "長期借款"),
    ("current portion of non-current liabilities", "non-current liabilities due within one year",
     "一年内到期的非流动负债", "一年內到期的非流動負債"),
    ("provisions", "预计负债", "預計負債"),
    ("deferred tax", "deferred taxation", "deferred income tax",
     "递延所得税", "递延税项", "遞延所得稅", "遞延稅項"),
    ("income tax", "income tax expense", "所得税", "所得税费用", "所得稅", "所得稅開支"),
    ("cash and cash equivalents", "cash and bank balances", "bank balances and cash",
     "货币资金", "现金及现金等价物", "现金和现金等价物", "银行结余及现金",
     "貨幣資金", "現金及現金等價物", "銀行結餘及現金"),
    ("share capital", "issued capital", "capital stock", "股本", "实收资本", "實收資本"),
    ("long-term prepaid expenses", "长期待摊费用", "長期待攤費用"),
    ("financial assets at fair value through profit or loss", "交易性金融资产",
     "以公允价值计量且其变动计入当期损益的金融资产", "交易性金融資產", "按公平值計入損益之金融資產"),
    ("revenue", "revenues", "turnover", "营业收入", "營業收入"),
    ("cost of sales", "cost of revenue", "cost of goods sold", "营业成本", "销售成本",
     "營業成本", "銷售成本"),
    ("administrative expenses", "general and administrative expenses",
     "管理费用", "行政开支", "管理費用", "行政開支"),
    ("selling and distribution expenses", "selling and distribution costs", "distribution costs",
     "selling expenses", "selling and marketing expenses",
     "销售费用", "销售及分销开支", "分销成本", "銷售費用", "銷售及分銷開支", "分銷成本"),
    ("research and development expenses", "研发费用", "研發費用"),
    ("finance costs", "finance cost", "财务费用", "融资成本", "財務費用", "融資成本"),
    ("staff costs", "employee benefit expenses", "employee benefits expense", "职工薪酬",
     "员工成本", "雇员福利开支", "職工薪酬", "員工成本", "僱員福利開支"),
    ("related party transactions", "related parties", "related party",
     "关联方", "关联交易", "关连人士", "關聯方", "關聯交易", "關連人士"),
    ("contingent liabilities", "contingencies", "commitments and contingencies",
     "或有事项", "或有负债", "或然负债", "或有事項", "或有負債", "或然負債"),
    ("earnings per share", "loss per share", "每股收益", "每股盈利", "每股亏损", "每股虧損"),
    ("dividends", "股利", "股息"),
    # THESE TWO EXIST TO WIN A HEADING FROM A SHORTER SPELLING ABOVE (the third rule): an
    # insurer's 保险合同负债 prints 合同负债, a payroll payable's 应付职工薪酬 prints 职工薪酬.
    ("insurance contract liabilities", "保险合同负债", "保險合同負債"),
    ("employee benefits payable", "应付职工薪酬", "應付職工薪酬"),
)

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_WORD = re.compile(r"[a-z]+")
_CONTINUED = re.compile(r"\((?:continued|cont'?d)\)|（續）|\(續\)|（续）|\(续\)", re.IGNORECASE)


def _fold(word: str) -> str:
    """An English plural folded, as `line_item_notes._fold` folds it: "parties" is "party",
    "assets" "asset"; "loss" stays."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def shape(text: str) -> tuple[tuple[str, ...], str]:
    """`text` as it is compared: its English words in order, lower case and plural-folded, and its
    Chinese characters in order, as Simplified. Punctuation, figures and "(Continued)" are not
    part of it — 物業、廠房及設備 is 物业厂房及设备, "Right-of-use" is right, of, use."""
    folded = _CONTINUED.sub(" ", to_simplified(str(text or "")).lower())
    return tuple(_fold(w) for w in _WORD.findall(folded)), "".join(_HAN.findall(folded))


@dataclass(frozen=True)
class Spelling:
    text: str                   # as listed
    group: int
    words: tuple[str, ...]
    han: str

    @property
    def short(self) -> bool:
        """One English word, or two Chinese characters: printed only as a whole heading."""
        return (len(self.words) <= 1 and not self.han) or (not self.words and len(self.han) <= 2)


SPELLINGS: tuple[Spelling, ...] = tuple(
    Spelling(text, group, *shape(text))
    for group, spellings in enumerate(GROUPS) for text in spellings)


def _run(longer: tuple[str, ...], shorter: tuple[str, ...]) -> bool:
    n = len(shorter)
    return any(longer[i:i + n] == shorter for i in range(len(longer) - n + 1))


def _prints(words: tuple[str, ...], han: str, spelling: Spelling) -> bool:
    if not spelling.words and not spelling.han:
        return False
    if spelling.short:
        return (words == spelling.words) if spelling.words else (han == spelling.han)
    if spelling.words and not _run(words, spelling.words):
        return False
    return not spelling.han or spelling.han in han


def _covers(longer: Spelling, shorter: Spelling) -> bool:
    if shorter.han and longer.han:
        return len(longer.han) > len(shorter.han) and shorter.han in longer.han
    if shorter.words and longer.words:
        return len(longer.words) > len(shorter.words) and _run(longer.words, shorter.words)
    return False


def printed(heading: str) -> list[Spelling]:
    """The listed spellings `heading` prints, less any a longer spelling of ANOTHER concept that
    it also prints contains (the third rule)."""
    words, han = shape(heading)
    found = [s for s in SPELLINGS if _prints(words, han, s)]
    return [s for s in found
            if not any(t.group != s.group and _covers(t, s) for t in found)]


def _subject(term: str) -> str:
    """A term without its measure word (`line_item_notes._MEASURE`): "Net Trade Receivables" is
    "Trade Receivables", 无形资产净额 is 无形资产."""
    from app.services.line_item_notes import _MEASURE
    out = str(term or "")
    for pattern in _MEASURE:
        out = pattern.sub(" ", out)
    return out


def named_groups(terms: Iterable[str]) -> list[int]:
    """The concepts a line's terms name, in the list's order: a term whose shape, measure word set
    aside, IS a spelling's (the first rule)."""
    shapes = {shape(_subject(t)) for t in terms if str(t or "").strip()}
    shapes.discard(((), ""))
    return sorted({s.group for s in SPELLINGS if (s.words, s.han) in shapes})


def _owned_by(spelling: Spelling, others: list[tuple[tuple[str, ...], str]]) -> bool:
    """Whether a sibling's term names this spelling: it IS the spelling, or the spelling holds it
    as a phrase — "tangible fixed assets" is the fixed-asset part's when that part says "fixed
    assets". NOT a sibling term that holds the spelling: "depreciation of property, plant and
    equipment" is a cash-flow part's term about its own line, not a claim on the heading."""
    for words, han in others:
        if (words, han) == (spelling.words, spelling.han):
            return True
        term = Spelling("", -1, words, han)
        if not term.short and _prints(spelling.words, spelling.han, term):
            return True
    return False


def spellings_for(terms: Iterable[str], *, owned: Iterable[str] = (),
                  contested: Iterable[int] = ()) -> list[str]:
    """Every spelling of every concept a line's terms name — its own included, so a heading in the
    other number or the other script is found too — except one a sibling part names (`owned`, the
    terms of the parts it is combined with). Of a concept a sibling ALSO names (`contested`), only
    the line's own spellings: the parts split that concept between them by spelling, and a
    spelling neither wrote down is not this list's to hand to one of them."""
    others = [shape(t) for t in owned if str(t or "").strip()]
    groups = set(named_groups(terms))
    shared = set(contested)
    own = {shape(_subject(t)) for t in terms if str(t or "").strip()}
    out = []
    for s in SPELLINGS:
        if s.group not in groups or _owned_by(s, others):
            continue
        if s.group in shared and (s.words, s.han) not in own:
            continue
        out.append(s.text)
    return out


def line_terms(item) -> list[str]:
    """The words a line is searched for its note by: its authored `note_terms`, or — for a line
    nobody gave note terms — its label and every alias it is printed under. The same words the
    heading search itself reads (`line_item_notes.line_names`)."""
    source = getattr(item, "note_source", None)
    authored = [str(t) for t in (getattr(source, "note_terms", None) or ()) if str(t or "").strip()]
    if authored:
        return authored
    # Imported here: `line_item_notes` is the heavier module, and reads nothing from this one.
    from app.services.line_item_notes import printed_names
    return [str(getattr(item, "label", "") or ""), *printed_names(item)]


def siblings(line_item_set) -> dict[str, set[str]]:
    """key -> the keys of the parts it is combined WITH: the other parts of its parent, and the
    other terms of every cascade rung it appears in."""
    items = list(getattr(line_item_set, "items", None) or ())
    out: dict[str, set[str]] = {}
    by_parent: dict[str, set[str]] = {}
    for item in items:
        parent = str(getattr(item, "parent", "") or "")
        if parent:
            by_parent.setdefault(parent, set()).add(item.key)
    for keys in by_parent.values():
        for key in keys:
            out.setdefault(key, set()).update(keys - {key})
    for item in items:
        for rung in getattr(item, "cascade", None) or ():
            refs = {str(getattr(t, "ref", "") or "") for t in (getattr(rung, "terms", None) or ())}
            refs.discard("")
            for ref in refs:
                out.setdefault(ref, set()).update(refs - {ref})
    return out


def extras_by_key(line_item_set) -> dict[str, tuple[str, ...]]:
    """key -> the spellings a heading may print for this list to add a note to that line; a line
    naming no concept is left out. Built from the WHOLE set, because a line's siblings — whose
    spellings are theirs — need not be asked about themselves."""
    items = list(getattr(line_item_set, "items", None) or ())
    terms = {item.key: line_terms(item) for item in items}
    near = siblings(line_item_set)
    out: dict[str, tuple[str, ...]] = {}
    groups = {key: set(named_groups(t)) for key, t in terms.items()}
    for item in items:
        others = sorted(near.get(item.key, ()))
        owned = [t for other in others for t in terms.get(other, ())]
        contested = set().union(*(groups.get(other, set()) for other in others))
        spellings = spellings_for(terms[item.key], owned=owned, contested=contested)
        if spellings:
            out[item.key] = tuple(spellings)
    return out


@lru_cache(maxsize=16384)
def _printed_texts(heading: str) -> tuple[str, ...]:
    """`printed`, as the listed texts — read once per heading, not once per line asking."""
    return tuple(s.text for s in printed(heading))


def heading_prints_one(heading: str, spellings: Iterable[str]) -> str | None:
    """The first of `spellings` that `heading` prints, the third rule applied; None when none."""
    wanted = set(spellings)
    return next((text for text in _printed_texts(str(heading or "")) if text in wanted), None)
