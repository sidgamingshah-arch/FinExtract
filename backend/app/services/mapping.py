"""Ontology mapping — a combination of methods, with the LLM as the key driver.

Mapping a printed source line to a canonical template concept is done by **meaning**,
not string similarity — but as an *ensemble*: every method contributes and they
corroborate one another. No single methodology is forced out.

1. exact / normalized lexical  (free, unambiguous → short-circuits)
2. rule-based                  (regex / keyword hints, minus exclude hints)
3. **LLM semantic decision**   (the key driver): shown each candidate's criteria
   (definition, include/exclude, confusable-with, value_scope) plus the ontology's global
   policies + worked examples, it chooses by meaning — so "Amounts due from customers",
   "Receivables from clients" and "Trade debtors" all resolve to ``trade_receivables``
   with no lexical alias hit.

Combination policy: exact wins outright; otherwise the LLM decides but is corroborated by
the deterministic methods — agreement raises confidence, a strong lexical disagreement
lowers it and flags review (the agreeing methods are recorded). When no LLM is configured
(``extraction.llm_mapping=false`` or provider ``stub``) or it abstains, the deterministic
ensemble decides with a margin-over-runner-up accept. Each value also carries an
``allocation_status`` so parent/child/residual handling stays auditable. Winning method,
confidence and per-strategy scores are recorded.

THERE IS NO EMBEDDING TIER EITHER. It shipped as evidence and a shortlist, and it never ran: the
mapping stage constructs this matcher with an LLM provider and no embedding provider, the only
registered provider was a stub that raises, and it contributed nothing to any row of any real filing.
Two settings and a Settings-screen knob governed it. Machinery that cannot execute is worse than
absent, because it reads like a capability — and as a capability it was the same KIND of judgement
the fuzzy tier was, resemblance rather than criteria, so it would have rated "Profit before
exceptional items and tax" against "Profit before tax" too. Meaning is the semantic tier's job, with
each concept's definition, include/exclude and confusable-with in front of it.

THERE IS NO FUZZY TIER, and its removal is a return to the declared contract rather than a
simplification of it. The rulebook's own ``binding.order`` names four things: resolve the
statement, resolve the section, restrict the candidate set to that section, then "Rule tier: exact
normalised alias, then regex_hints, in descending match_priority", then the semantic tier over the
restricted set. String similarity appears nowhere in it. Carried as an extra tier it decided rows
on wording alone — measured on the shipped rulebook, "Profit before exceptional items and tax" was
filed as ``pl_profit_before_tax`` at 0.61, accepted and unflagged, two subtotals that differ by
exactly the exceptional items, so the figure landed on the wrong line of the P&L and the statement
still tied. A caption no alias, no rule and no model can place is now left unmapped for a human,
which is a visible gap instead of a plausible wrong answer.

Alias SIMILARITY survives as a measurement (:meth:`OntologyMatcher._alias_similarity`) and not as a
decision: two guards need to recognise "this caption is that alias, near enough" — the corroboration
that tells whether the deterministic evidence dissents from the model, and the refusal that stops a
caption naming a COMPUTED concept from being re-homed onto a neighbouring one. Neither can map a row
on its own.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

from app.config import Settings, get_settings
from app.core.models.enums import MappingMethod
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.han import has_han, to_simplified


class LlmMappingDecision(BaseModel):
    """Structured output for a single description-based mapping decision."""

    canonical_key: str = Field(description="the chosen canonical key, or \"\" if none fits")
    confidence: float = Field(ge=0, le=1)
    allocation_status: str = Field(
        default="",
        description="how the value relates to others: direct_exclusive | child_component | "
                    "parent_gross_evidence_only | calculated_residual | fallback_combined | unmapped_review",
    )
    reason: str = Field(default="", description="brief justification grounded in meaning/criteria")


class LlmBatchItem(BaseModel):
    item_id: str
    canonical_key: str = Field(description="chosen key, or \"\" if none fits")
    confidence: float = Field(ge=0, le=1)
    allocation_status: str = ""


class LlmBatchDecision(BaseModel):
    """Per-statement decision over many captions at once, so cross-line judgements
    (parent/child containment, residualisation, 'Others') have full context."""

    mappings: list[LlmBatchItem] = Field(default_factory=list)


_LLM_SYSTEM = (
    "You map a single raw line-item caption from a financial statement to ONE canonical "
    "concept, by MEANING. You are given the caption (with any context) and candidate "
    "concepts, each with: canonical_key, a definition, inclusion criteria (include), "
    "exclusion criteria (exclude), concepts it is easily confused with, and its value_scope. "
    "Choose the candidate whose definition and criteria best match what the caption "
    "represents — rely on financial meaning, not string similarity or shared words. Respect "
    "the exclusion criteria and the confusable-with warnings. If no candidate genuinely "
    "fits, return an empty canonical_key. Return calibrated confidence in [0,1] (high only "
    "when unambiguous) and, when clear, an allocation_status describing how the value "
    "relates to parents/children."
)

# Appended for the BATCH path only. The base instruction opens "You map a single raw line-item
# caption", which is false when several are decided at once, and it never says what a section is —
# so a model told an item's section had no way to know the word was binding. Kept separate from
# `_LLM_SYSTEM` so correcting the batch framing cannot silently rewrite the per-line prompt.
_LLM_BATCH_ADDENDUM = (
    "\n\nThis request carries SEVERAL captions from one statement at once, in the order they are "
    "printed in the document. Decide them together: a caption's meaning is often fixed by the "
    "lines around it — a parent and the children that make it up, a subtotal and the lines above "
    "it, a residual 'Others' that is whatever the section's named lines do not account for.\n"
    "An item may carry a `section`: the normalised heading it was printed under (for example "
    "`current_assets`, `equity`, `current_liabilities`). It is BINDING — choose a concept whose "
    "canonical_key belongs to that section. It is what separates captions the document prints "
    "identically in more than one place: an 'Others' line, or the 'Non-controlling interests' "
    "printed once under the profit split and again under the total-comprehensive split. An item "
    "with no `section` is unconstrained: decide it on meaning alone.\n"
    "`residual_expectations` lists, per section, the kinds of caption that section is EXPECTED to "
    "have no dedicated concept for. When an item's caption is one of those, return an empty "
    "canonical_key instead of the nearest candidate: a later deterministic step files it in that "
    "section's remainder, itemised under its own label. Choosing the nearest concept instead puts "
    "the figure on a specific wrong line and the section still adds up, which is the one error "
    "nothing downstream can detect.\n"
    "Return one entry per item_id you were given, and never an item_id that was not given to you."
)


# A QUOTED ABBREVIATION GLOSS: the short name a filing introduces for a term it has just written
# out, in brackets, in quotes — 'PRC corporate income tax ("CIT")',
# 'PRC land appreciation tax ("LAT")', '中國企業所得稅（「企業所得稅」）'.
#
# WHY THE PUNCTUATION STRIPPER BELOW DOES NOT ALREADY HANDLE IT. It turns every non-word character
# into a space, so the brackets and quotes do disappear on their own — and the abbreviation SURVIVES
# AS A WORD: 'prc corporate income tax cit', which is not the alias 'prc corporate income tax' and
# matches nothing. That one extra token is the whole defect, and it is why the exact tier misses a
# caption whose alias the rulebook already carries.
#
# Measured on a real bilingual HKEX filing: it is what kept note 11's tax split unreadable, and so
# kept the face's single "Income tax expense" line un-decomposed even though the note itemises it
# exactly (633,137 + 90,588 + 136,626 − 670,847 = 189,504).
#
# THE QUOTES ARE THE SIGNAL, and requiring them is what keeps this narrow. A parenthetical is not
# always a gloss, and most carry meaning that must not be dropped: 'Profit/(loss) before tax',
# 'Credited/(charged) to profit or loss during the year', 'Pledged deposits (note (b))'. None is
# quoted, so none is touched. Only a bracket whose content is wrapped in quotation marks — straight,
# curly, or the CJK corner and lenticular brackets a Chinese filing uses — reads as the filing naming
# an abbreviation for itself, which is a fact about the PROSE and not about the figure.
_ABBREV_GLOSS = re.compile(
    r"""[(（]\s*                     # an opening bracket, either width
        ["'“”‘’「」『』《》]\s*        # …whose content opens with a quotation mark
        [^)）]*?                      # the abbreviation itself, never crossing the bracket
        \s*["'“”‘’「」『』《》]\s*     # …and closes with one
        [)）]""",
    re.VERBOSE)

# A NOTE CITATION printed inside the caption — "Deferred tax credited for the year (note 32)",
# "Depreciation of right-of-use assets (note 16(b))", "受限制現金（附註(a)）". It is a POINTER to
# where the detail lives, never part of the concept's name, and the punctuation stripper leaves the
# words behind exactly as the gloss above does: 'deferred tax credited for the year note 32', which
# no alias equals. Before this, such a caption fell through to the fuzzy tier and was decided by
# resemblance; now it is decided by the alias the rulebook actually carries.
#
# DUPLICATED FROM ``row_reconstruct._NOTE_MARKER``, ON PURPOSE AND UNDER PROTEST. That module
# imports THIS one (``section_of_banner``), so this one cannot import it back, and the two patterns
# serve pipelines with different contracts: there, one declared step of the rulebook-authored
# normalisation pipeline, which a rulebook can reorder or drop; here, the matcher's own internal
# normalisation, which is not the rulebook's to change. The shared home is a caption module both
# could import, and that is the right eventual fix — but a note reference is one shape and both
# copies must recognise it, so a change to either belongs in both until then.
#
# A DANGLING CITATION counts as one. The second alternative below matches an opening bracket and the
# word with NOTHING after it — "Deferred tax credited for the year (note" — which is what a caption
# truncated mid-citation looks like, and it is exactly what a real filing produced: the row printed
# "Deferred tax credited for the year (note 32) 年內計入遞延稅項（附註32）" and reached the matcher
# with everything after "(note" lost. The truncation itself is a row-reconstruction defect and
# belongs to that module; recognising the stump as the pointer it is costs nothing and is right
# regardless, because a caption never ENDS on the word "note" as part of a concept's name. Anchored
# to the end of the string so "Note 15: Trade receivables" — where the word leads — is untouched.
_NOTE_CITATION = re.compile(
    r"[(（]?\s*(?:notes?|附註|附注)\s*\.?\s*\d{1,3}[a-z]?(?:\s*[(（][a-z0-9]{1,3}[)）])?\s*[)）]?"
    r"|[(（]\s*(?:notes?|附註|附注)\s*$",
    re.IGNORECASE)

# A BRACKETED BARE NUMBER — "(32)", "（32）", "(2022)". Two things leave one behind, and both are
# noise rather than name:
#
# * ``label_segments`` splits a bilingual caption by SCRIPT, and a note citation printed in Chinese
#   is half Han and half digits: "（附註32）" loses 附註 to the Han segment and leaves "（32）" in the
#   Latin one, where the pattern above no longer recognises it as a citation. That residue is why
#   "Deferred tax credited for the year (note 32) 年內計入遞延稅項（附註32）" still missed its alias
#   after note citations were stripped.
# * a year in brackets ("(2022)") is a comparative marker, not part of the concept.
#
# It is NOT the same as ``row_reconstruct._TRAILING_PAREN_DIGITS``, which is anchored to the end of
# the caption: this has to fire mid-string, because the Latin segment of a bilingual caption keeps
# the residue wherever the Chinese half was. Bare digits only — "(a)", "(b)", "(i)" are sub-item
# letters that DO distinguish captions ("Pledged deposits (note (b))") and are left alone.
_BRACKETED_NUMBER = re.compile(r"[(（]\s*\d{1,4}\s*[)）]")


def normalize_label(text: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace (locale-agnostic).

    Han text is folded to Simplified so a Traditional caption from a Hong Kong or Taiwan
    filing compares equal to a Simplified alias (and vice versa) — the same concept printed
    in the other script would otherwise never match.

    A quoted abbreviation gloss is dropped first — see ``_ABBREV_GLOSS`` for why the punctuation
    stripping below does not already do it.
    """
    text = _BRACKETED_NUMBER.sub(" ", _NOTE_CITATION.sub(" ", _ABBREV_GLOSS.sub(" ", text)))
    text = to_simplified(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# A bilingual filing prints one caption in both scripts: "REVENUE 收益",
# "Cost of sales 銷售成本". Matching the concatenation dilutes every score (half the string is
# always "wrong" for a single-language alias), so each script's run is also matched on its own
# and the best segment wins.
_HAN_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+(?:\s*[㐀-䶿一-鿿豈-﫿]+)*")


def label_segments(text: str) -> list[str]:
    """The caption plus its per-script halves (Latin-only and Han-only), longest first.

    Returns just ``[text]`` for a single-script caption, so monolingual filings are unaffected.
    """
    if not text or not has_han(text):
        return [text]
    han = " ".join(_HAN_RUN.findall(text)).strip()
    latin = _HAN_RUN.sub(" ", text)
    latin = re.sub(r"\s+", " ", latin).strip()
    out = [text] + [p for p in (latin, han) if p and p != text]
    return list(dict.fromkeys(out))


@dataclass
class Candidate:
    canonical_key: str
    method: MappingMethod
    score: float
    allocation_status: str | None = None
    # Set when the concept whose evidence matched is NOT the one being proposed: the caption
    # matched one leaf of a collision family and the banner named another. Carried so the decision
    # stays auditable instead of looking like an ordinary hit on the concept it was corrected to.
    rerouted_from: str | None = None


@dataclass
class MappingResult:
    canonical_key: str | None
    method: MappingMethod
    confidence: float
    candidates: list[Candidate] = field(default_factory=list)
    needs_review: bool = False
    scores: dict[str, float] = field(default_factory=dict)  # per-strategy best score
    allocation_status: str | None = None                    # how the value was derived
    agreement: list[str] = field(default_factory=list)      # methods that corroborated the pick
    rerouted_from: str | None = None                        # see Candidate.rerouted_from
    # Set when the row was left unmapped because its caption names a concept the framework COMPUTES
    # (`OntologyMatcher._computed_claim`). Carried, not merely counted, because the caller has to act
    # on it: such a row is a subtotal, and an unclaimed face row with a value is otherwise swept into
    # its section's "Others" — which would add the section's own subtotal back into the section.
    computed_claim: str | None = None


# Canonical keys are namespaced by statement SECTION as well as by statement
# (bs_non_current_liabilities__…, bs_current_assets__…), and a statement prints the same caption
# under two of them: "Interest-bearing bank and other borrowings" appears once under non-current
# liabilities and once under current, as do senior notes and lease liabilities. The caption
# cannot distinguish them; the banner above the row can. Each entry maps a section token found in
# a key to the words a banner uses for it, in English and in Han (folded to Simplified by
# ``normalize_label``).
#
# Module-level, and free of any ontology: the vocabulary is a property of how statements are
# printed, and other stages need to read a banner without paying to build a matcher.
SECTION_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Longest first: "non current liabilities" must not be read as "current liabilities".
    ("non_current_liabilities", ("non current liabilities", "noncurrent liabilities",
                                "非流动负债", "长期负债")),
    ("non_current_assets", ("non current assets", "noncurrent assets",
                           "非流动资产", "长期资产")),
    ("current_liabilities", ("current liabilities", "流动负债")),
    ("current_assets", ("current assets", "流动资产")),
    ("equity", ("equity", "capital and reserves", "权益", "股本及储备", "资本及储备")),
    # The cash flow statement is namespaced the same way, and its captions repeat across
    # activities: "Interest received" and "Dividends received" appear under both operating and
    # investing, "Acquisition of subsidiaries" under investing and financing.
    ("cash_flow_from_operating_activities", ("operating activities", "经营活动", "营运活动")),
    ("cash_flow_from_investing_activities", ("investing activities", "投资活动")),
    ("cash_flow_from_financing_activities", ("financing activities", "融资活动", "筹资活动")),
    # The income statement ends with the SAME two captions twice — "Owners of the parent" and
    # "Non-controlling interests" — once splitting profit for the year and once splitting total
    # comprehensive income. Only the sub-heading above them tells the pairs apart; without it
    # both land on one concept and are added into a meaningless total. The comprehensive-income
    # heading is tested first because it contains "attributable to" as well.
    # Other comprehensive income, tested BEFORE the attribution entry below and before the bare
    # "income" entry at the bottom, and load-bearing against both.
    #
    # Against "income": the OCI section's scope id is `pl_s8_other_comprehensive_income`, and
    # `section_token_of_scope` reads the token off the END of an id — so without this entry that id
    # resolves to `income`, the REVENUE section. Every OCI concept would then be admitted under a
    # revenue banner and refused under its own.
    #
    # Against the attribution entry: that one matches the bare word "comprehensive", which appears
    # in "Other comprehensive income" too. Tested second, it would claim the OCI banner and scope it
    # to the attribution captions. Tested first, this entry is narrow enough not to take the banner
    # it is not for: "Total comprehensive income attributable to" does not contain "other".
    ("other_comprehensive_income", ("other comprehensive income", "其他综合收益", "其他綜合收益",
                                    "其他全面收益", "其他全面收入")),
    # "comprehensive" is the whole distinction, and it has to be matched on its own: a filing
    # reporting a loss prints "Total comprehensive LOSS attributable to", and a filing covering
    # both prints "income/(loss)". Requiring the word "income" missed every one of those, which
    # sent the comprehensive-income split into the profit split — the exact collapse this entry
    # exists to prevent. The profit split never says "comprehensive".
    ("total_comprehensive_income_attributable_to", ("comprehensive", "全面收", "全面亏")),
    ("profit_attributable_to",
     ("profit attributable", "loss attributable", "attributable to",
      "溢利归属", "亏损归属", "应占溢利")),
    # The income statement's ordinary sections. Absent until now, which meant `section_of_key`
    # returned None for every `pl_income__*`, `pl_expenses__*`, `pl_non_operating_expenses__*`,
    # `pl_exceptional_items__*` and `pl_tax_expense__*` key — 34 of the 173 shipped concepts — so
    # `_in_section` waved all of them through and a banner like "REVENUE" was captured as a
    # section_hint that normalised to nothing. The two attributable-to families above were the
    # only part of the P&L a banner could scope.
    #
    # ORDER IS LOAD-BEARING TWICE OVER. `section_of_key` matches "_<tok>__" as a substring, and
    # "_non_operating_expenses__" CONTAINS "_expenses__" — so the compound must be tested first,
    # exactly as non_current_liabilities is tested before current_liabilities. And
    # `section_of_banner` returns the first match, so "Income tax expense" must reach tax_expense
    # before it can reach income.
    ("non_operating_expenses", ("non operating", "nonoperating", "非经营", "非营运")),
    ("exceptional_items", ("exceptional", "非经常性", "特殊项目")),
    # "income tax" is deliberately absent: it is contained in three BALANCE-SHEET captions the
    # ontology maps as their own concepts — deferred income tax assets, prepaid income tax, income
    # tax payable — and any of those reaching this function as a banner would scope the row to
    # tax_expense and refuse every bs_ concept under it. "Income tax expense" still resolves here,
    # via "tax expense".
    ("tax_expense", ("tax expense", "taxation", "税项", "所得税")),
    ("expenses", ("expenses", "开支", "费用")),
    # Deliberately NOT the bare words "income" / "收入": a banner naming the income section says
    # revenue or turnover, while "income" appears in captions all over a filing (deferred income
    # tax, other comprehensive income). A banner that resolves to the WRONG section is worse than
    # one that resolves to nothing, because the gate then refuses the correct concept.
    ("income", ("revenue", "turnover", "营业额", "营业收入", "收益")),
)


def section_of_banner(text: str | None) -> str | None:
    """The section a banner names, or None when it names none we recognise.

    An umbrella banner that spans more than one section ("EQUITY AND LIABILITIES", which IFRS
    statements print above the Equity / Non-current / Current sub-banners) scopes nothing on its
    own: reading it as "equity" would refuse every liability concept beneath it. Those return
    None so the constraint simply does not apply.
    """
    if not text:
        return None
    folded = normalize_label(text)
    if ("equity" in folded or "权益" in folded) and ("liabilit" in folded or "负债" in folded):
        return None
    for token, words in SECTION_WORDS:
        if any(w in folded for w in words):
            return token
    return None


# The sections a statement prints as a HEADING ROW, and the only ones a data-less row may declare.
#
# Every other family in ``SECTION_WORDS`` is matched on words that are themselves complete line-item
# captions, so a row carrying one of them is far more likely to be an item with no figures than a
# heading: ``income`` matches "Revenue" and "Turnover", ``tax_expense`` matches "Taxation",
# ``other_comprehensive_income`` matches the OCI subtotal itself, ``profit_attributable_to`` matches
# "attributable to" inside "Profit attributable to owners of the parent", and ``exceptional_items`` /
# ``non_operating_expenses`` / ``total_comprehensive_income_attributable_to`` match bare fragments
# ("exceptional", "non operating", "comprehensive").
#
# The eight below are unambiguous: no concept in a statement is CAPTIONED "Current assets" or
# "Operating activities", so a row that says only that is a heading. The cost of the exclusion is
# that a profit-and-loss section heading in a spreadsheet sets no section — those rows keep falling
# back to the accounting structure, which is what they did before, so nothing regresses.
HEADING_ROW_SECTIONS: frozenset[str] = frozenset({
    "non_current_assets", "current_assets", "non_current_liabilities", "current_liabilities",
    "equity", "cash_flow_from_operating_activities", "cash_flow_from_investing_activities",
    "cash_flow_from_financing_activities",
})


def section_of_banner_only(text: str | None) -> str | None:
    """The section a label names when the label is NOTHING BUT that banner, else None.

    :func:`section_of_banner` matches a section phrase ANYWHERE in the text, which is right where
    geometry has already established that the text is a standalone heading — a printed line with no
    figures beside it, on a statement face. It is wrong where there is no geometry to lean on.

    A spreadsheet row is the case in point. "Label column has text, value columns are empty" is a
    banner sometimes and a line item with no data for either period the rest of the time, and
    substring matching cannot tell them apart: "Equity investments designated at FVOCI" contains
    "equity" and would scope every row beneath it — a non-current asset declaring the equity section.
    "Total current assets" contains "current assets" and is the LAST row of its section, so reading
    it as a banner scopes the NEXT one.

    The same distinction bites on a PAGE, which is why ``row_reconstruct._is_banner_line`` reads
    through here too. A caption wrapping over two printed lines puts "Equity investments designated"
    on a line of its own, and substring matching took that for the equity banner — truncating the
    item to "at FVOCI" and scoping every row below to equity while it is a non-current asset.
    Exhaustion refuses it, because "equity" does not account for "investments designated".

    Callers whose evidence is weaker than a printed label-only line add ``HEADING_ROW_SECTIONS`` on
    top of this; the function itself does not, so the page path still recognises a profit-and-loss
    banner ("Other comprehensive income") that a data-less spreadsheet row is not trusted to declare.

    So this requires the label to be EXHAUSTED by section phrases. One phrase ("Current assets"), or
    several where a filing puts both languages in one cell ("Current assets 流動資產"), and nothing
    else. Anything left over means the label says something the section vocabulary does not cover,
    which makes it a caption.

    The umbrella rule is inherited rather than restated: ``section_of_banner`` returns None for a
    banner spanning more than one section ("EQUITY AND LIABILITIES"), and this returns None whenever
    it does.
    """
    token = section_of_banner(text)
    if token is None:
        return None
    remaining = normalize_label(text)
    for _tok, words in SECTION_WORDS:
        for word in words:
            remaining = remaining.replace(word, " ")
    return token if not remaining.strip() else None


def section_of_key(canonical_key: str) -> str | None:
    """The section namespace a canonical key sits in, or None for a key that has none
    (``bs_total_assets``, ``pl_profit_before_tax``). Matched longest-first, since
    "bs_non_current_liabilities__x" also contains "_current_liabilities__".

    This is the FALLBACK reading of a concept's section, not the authoritative one: a v2 rulebook
    declares ``section_scope`` and that is what the gate reads (see
    :meth:`OntologyMatcher._sections_of`). A key name is a naming convention an editor can break
    without meaning to; ``section_scope`` is a statement of intent.
    """
    return next((tok for tok, _ in SECTION_WORDS if f"_{tok}__" in canonical_key), None)


def sections_of_key(canonical_key: str) -> frozenset[str]:
    """:func:`section_of_key` as a set, empty for a key carrying no section namespace.

    The set is the shape everything downstream compares against, because ``section_scope`` is a
    LIST and a concept may legitimately be declared in two sections. A key name can only ever
    express one, which is one of the reasons it is the fallback and not the source.
    """
    tok = section_of_key(canonical_key)
    return frozenset([tok]) if tok else frozenset()


# The statement each canonical-key namespace stands for — the FALLBACK reading of a concept's
# statement, for a v1 concept that declares none (see :meth:`OntologyMatcher._statement_of`).
_STATEMENT_OF_PREFIX: dict[str, str] = {"bs": "balance_sheet", "pl": "profit_and_loss",
                                        "cf": "cash_flow", "eq": "changes_in_equity"}

# One statement, two spellings. The page classifier and every caller say "changes_in_equity", while
# ``StatementType`` — the enum a rulebook's ``statement`` field validates against — spells the same
# statement "equity_changes". Both sides are folded through here before they are compared, because a
# declaration the gate cannot compare to the classifier's verdict does not scope anything: it
# refuses every concept in that statement, on every page.
_STATEMENT_SPELLINGS: dict[str, str] = {"equity_changes": "changes_in_equity"}


def statement_of_key(canonical_key: str) -> str | None:
    """The statement a canonical key's namespace names, or None for a key outside the four."""
    return _STATEMENT_OF_PREFIX.get(canonical_key.split("_", 1)[0])


def normalize_statement(statement) -> str:
    """One spelling for one statement, whichever vocabulary it arrived in (see the map above)."""
    if statement is None:
        return ""
    raw = statement.value if hasattr(statement, "value") else str(statement)
    return _STATEMENT_SPELLINGS.get(raw, raw)


def section_token_of_scope(scope_id: str) -> str | None:
    """The banner token a v2 ``section_scope`` id names, or None when it names no section.

    The two vocabularies are authored for different readers and cannot simply be compared. A scope
    id is authored per statement and carries the section's PRINTED POSITION as well as its name
    ("bs_s4_non_current_liabilities", "cf_s1_cash_flow_from_operating_activities"), while
    ``SECTION_WORDS`` is keyed on the section itself, because that is what a banner names. The id
    ends with the section it is, so the token is read off the end.

    Longest-first for the same reason ``section_of_key`` is: "bs_s1_non_current_assets" also ends
    with "current_assets", and reading it as the current-asset section would let a current-asset
    banner claim every non-current concept.

    ``*_top_level`` scopes name no section and return None. Those concepts are the statement-level
    totals ("bs_top_level" holds bs_total_assets, bs_net_assets), which no banner may constrain —
    ``section_hint`` is the nearest PRECEDING banner, so a statement total routinely carries the
    banner of the last section printed above it.
    """
    return next((tok for tok, _ in SECTION_WORDS if scope_id.endswith(tok)), None)


# Vocabularies a caption can name only ONE member of. IAS 7 divides cash flows into exactly three
# activities and a statement labels each subtotal with its own, so a caption saying "financing
# activities" is not the investing subtotal under any reading.
#
# This is not a similarity judgement the fuzzy scorer may trade off. "Net cash used in investing
# activities" and "Net cash flows used in financing activities" differ by one word in seven, which
# token similarity scores at 0.92 — above any threshold anyone would pick. The consequence is
# silent and expensive: the financing figure is filed under investing, investing then shows two
# figures summed, and the financing line has none. The structural check catches the arithmetic
# afterwards, but the caption said which line it was all along.
#
# Add a vocabulary here only when naming one member genuinely rules out the others for every
# filing — this gate cannot be overridden by evidence, so a merely-usually-true grouping would
# refuse correct mappings.
EXCLUSIVE_VOCABULARIES: tuple[tuple[str, ...], ...] = (
    ("operating", "investing", "financing"),
)
_WORD = {w: re.compile(rf"\b{w}\b", re.IGNORECASE)
         for vocab in EXCLUSIVE_VOCABULARIES for w in vocab}


def _names_a_different_class(canonical_key: str, caption: str,
                             sections: frozenset[str] | None = None) -> bool:
    """Whether the caption names a member of an exclusive vocabulary that the concept is not in.

    Which member the CONCEPT is in is read from its resolved sections when the caller has them
    (``cash_flow_from_investing_activities`` names the activity, as the section vocabulary does),
    and off the canonical key otherwise — so it still needs no ontology authoring and still holds
    for any template that names its sections after the thing they contain
    (``cf_cash_flow_from_investing_activities__…``). Declaration first for the same reason the
    section gate reads ``section_scope``: a key name is a convention an editor can break, and a
    concept redeclared into another activity must be judged by where the rulebook now puts it.
    """
    key = (canonical_key or "").lower()
    declared = " ".join(sorted(sections)).lower() if sections else ""
    for vocab in EXCLUSIVE_VOCABULARIES:
        in_scope = [w for w in vocab if w in declared]
        # The key namespace only when the declaration names no member of this vocabulary at all —
        # a concept scoped to no section (a statement-level cash-flow total) keeps the key reading.
        in_key = in_scope or [w for w in vocab if w in key]
        if len(in_key) != 1:
            continue                      # the concept is not in this vocabulary, or is ambiguous
        in_caption = [w for w in vocab if _WORD[w].search(caption)]
        # Only refuse when the caption names exactly one member and it is not the concept's own.
        # A caption naming two ("cash flows from operating and investing activities") is a genuine
        # combined line and is left to the ordinary tiers to judge.
        if len(in_caption) == 1 and in_caption[0] != in_key[0]:
            return True
    return False


# Concepts that a statement prints under ONE caption and that differ only in which section
# variant of the same fact they are. The banner above the row is the only evidence that separates
# them, so when a decision names the right kind of thing and the wrong variant the banner settles
# it: the answer is corrected to the sibling the banner names instead of being discarded. Discarding
# it drops the row to a weaker path, and for the P&L bottom line that loses the largest figure on
# the statement — a wrapped bilingual "TOTAL COMPREHENSIVE / LOSS FOR THE YEAR" reaches the matcher
# as the bare fragment "LOSS FOR THE YEAR", which is an alias of the OTHER bottom line.
#
# These are declared here and not read out of the rulebook, which is a compromise worth naming.
# The v2 ontology describes every one of these collisions — `section_disambiguation` prose on 18
# concepts, and `binding.order` step 6 nominating mutual `confusable_with` as the tie set — but
# neither is usable as the declaration:
#   * `confusable_with` is a confusion graph, not a family. Its mutual pairs connect into a single
#     47-concept component in the shipped file (share capital → reserves → NCI → the tax lines →
#     both bottom lines), so re-routing anywhere inside it would move an answer between concepts
#     that are different facts — the opposite of conservative.
#   * `section_disambiguation` is free-form prose, and only some of it names the sibling's key at
#     all. Scraping keys out of it would make a wording edit a behaviour change, and prose cannot
#     be told apart from "never confuse this with that", which means the opposite.
# A typed family block on the schema is the right home; see the integrator note. Until it exists,
# a rulebook that does not contain these keys simply has no families and nothing re-routes.
CONCEPT_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lease_liability", ("bs_current_liabilities__current_lease_liabilities",
                         "bs_non_current_liabilities__non_current_lease_liabilities")),
    ("borrowings", ("bs_current_liabilities__current_borrowings",
                    "bs_non_current_liabilities__non_current_borrowings")),
    # Notes payable only. Bonds payable was in this family and had to come out: the template has no
    # current-bonds node, so "CURRENT LIABILITIES" identified exactly one leaf — current NOTES
    # payable — and a row printed "Bonds payable" was re-routed onto a different instrument at
    # confidence 1.0, replacing an honest residual with a specific wrong line that the subtotal
    # still ties to. A bond is not a note in the wrong section.
    ("notes_payable", ("bs_current_liabilities__current_notes_payable",
                       "bs_non_current_liabilities__non_current_notes_payable")),
    ("properties_under_development", ("bs_current_assets__properties_under_development",
                                      "bs_non_current_assets__properties_under_development")),
    # HKAS 7.31 permits interest received in either activity, so the printed section is the whole
    # answer and the caption is byte-identical in both.
    ("interest_received", ("cf_cash_flow_from_operating_activities__interest_received",
                           "cf_cash_flow_from_investing_activities__interest_received")),
    # HKAS 7.33 says the same about interest PAID — operating or financing, one filing's choice —
    # and the revised cash flow gives the operating section its own concept for it. Both print
    # "Interest paid" / 已付利息 exactly, so without this pair the two are one caption with two
    # homes and whichever concept scores first takes it.
    ("interest_paid", ("cf_cash_flow_from_operating_activities__interest_paid",
                       "cf_cash_flow_from_financing_activities__interest_paid")),
    ("nci_attribution", (
        "bs_equity__non_controlling_interests",
        "pl_profit_attributable_to__non_controlling_interests",
        "pl_total_comprehensive_income_attributable_to__non_controlling_interests",
    )),
    ("owners_of_parent", ("pl_profit_attributable_to__owners_of_the_parent",
                          "pl_total_comprehensive_income_attributable_to__owners_of_the_parent")),
    ("pl_bottom_line", ("pl_profit_for_the_year",
                        "pl_total_comprehensive_income_for_the_year")),
)

# The bottom-line pair is the one family whose leaves carry NO section namespace, so it cannot be
# identified by `section_of_key` and cannot lean on the same-thing rule below. It gets its own
# banner test, and that test has to be far narrower than the section vocabulary.
#
# `SECTION_WORDS` identifies the comprehensive-income SECTION by the bare word "comprehensive",
# deliberately, so that "comprehensive loss" and "income/(loss)" both match. Reusing it here fired
# the re-route on "STATEMENT OF COMPREHENSIVE INCOME" — an ordinary HKEX page title, captured as the
# section_hint for every row on the page. "Profit for the year" was filed as total comprehensive
# income at confidence 1.0, two different figures collapsed onto one concept, and
# pl_profit_for_the_year was left empty. Nothing downstream sees it: the subtotals still tie.
#
# So the evidence required is the word TOTAL bound to "comprehensive" — the wording a filing uses
# for the line ITSELF, not for the statement it appears in — and never the attribution sub-heading,
# which introduces the owners/NCI split rather than the bottom line.
_TCI_BOTTOM_LINE = re.compile(r"total\s+comprehensive|全面(?:亏损|虧損|收益|收入|损益)?\s*总?總?额")
_ATTRIBUTION = re.compile(r"attributable|归属|歸屬")


def _names_the_comprehensive_bottom_line(banner: str | None) -> bool:
    if not banner:
        return False
    folded = normalize_label(banner)
    return bool(_TCI_BOTTOM_LINE.search(folded)) and not _ATTRIBUTION.search(folded)

_FAMILY_MEMBERS: dict[str, tuple[str, ...]] = {
    key: members for _name, members in CONCEPT_FAMILIES for key in members
}

# The words that distinguish one SECTION VARIANT of a concept from another. Stripping them leaves
# the thing itself, which is what two members of a family must have in common. ("cuurent" used to
# be matched here too, for a typo in a shipped canonical key; the balance-sheet revision fixed the
# key, so matching the misspelling would now only hide a fresh one.)
_VARIANT_PREFIX = re.compile(r"^(non[_-]?current|current)_")


def _the_thing_itself(canonical_key: str) -> str | None:
    """What a key is ABOUT, with its section namespace and current/non-current wording removed.

    None for a key carrying no section namespace at all: such a key names a statement-level figure,
    and a statement-level figure has no section variant to be confused with.
    """
    _, sep, leaf = canonical_key.partition("__")
    if not sep:
        return None
    return _VARIANT_PREFIX.sub("", leaf)


def _is_variant_of(a: str, b: str) -> bool:
    """Whether two keys are the SAME THING in different sections.

    This is the whole licence for re-routing. A variant pair is one concept the filing may print in
    either of two sections — current vs non-current lease liabilities, interest received under
    operating vs investing — where the banner is the only evidence and the caption is often
    byte-identical. Anything else is a different concept that merely resembles its sibling, and
    "correcting" a decision onto it replaces a defensible answer with a confident wrong one:
    bonds payable is not notes payable, deferred revenue is not deferred income, and profit for the
    year is not total comprehensive income. Each of those was declared as a family and each produced
    a wrong figure that the subtotal checks could not see, because nothing was arithmetically
    inconsistent — only wrong.
    """
    ta, tb = _the_thing_itself(a), _the_thing_itself(b)
    return ta is not None and ta == tb


def family_leaf_named_by(canonical_key: str, banner: str | None,
                         sections_of=None) -> str | None:
    """The sibling of ``canonical_key`` that ``banner`` identifies, or None.

    None whenever the banner settles nothing — the key is in no family, the banner names no section
    we recognise, that section identifies no leaf, or it identifies more than one — and None when
    the leaf it identifies is the key itself, because then there is nothing to correct. Every one of
    those is a refusal to guess: this function is the only thing that may overrule a decision, so it
    answers only where the answer is forced.

    ``sections_of`` is how a caller supplies the RULEBOOK's reading of which section each sibling is
    in (:meth:`OntologyMatcher._sections_of`); it defaults to the key namespace, which is all a
    caller holding no ontology has. Which sibling the banner names must follow the declaration for
    the same reason the gate does — otherwise a concept redeclared into another section is still
    re-routed by its key name, and the row is corrected onto a concept the rulebook no longer puts
    there.
    """
    sections = sections_of or sections_of_key
    members = _FAMILY_MEMBERS.get(canonical_key)
    if not members:
        return None
    # The bottom-line pair, on its own narrow evidence. Only one direction is answerable: a banner
    # can say "this line IS the total comprehensive one", but no banner says "this line is merely
    # profit", so a caption already mapped to the comprehensive line is never moved off it.
    if canonical_key == "pl_profit_for_the_year":
        return ("pl_total_comprehensive_income_for_the_year"
                if _names_the_comprehensive_bottom_line(banner) else None)
    if canonical_key == "pl_total_comprehensive_income_for_the_year":
        return None
    want = section_of_banner(banner)
    if not want:
        return None
    named = [k for k in members if want in sections(k)]
    if len(named) != 1 or named[0] == canonical_key:
        return None
    # Last gate, and the one that does not depend on the declaration being right: re-route only
    # between two spellings of the same thing.
    if not _is_variant_of(canonical_key, named[0]):
        return None
    return named[0]


class OntologyMatcher:
    """Runs the ensemble for one ontology + locale."""

    def __init__(
        self,
        ontology: OntologyDefinition,
        locale: str | None = None,
        settings: Settings | None = None,
        llm_provider=None,
    ):
        self.ontology = ontology
        self.locale = locale or ontology.locale
        self.settings = settings or get_settings()
        self.llm_provider = llm_provider
        # Description-based LLM mapping is the primary strategy when a provider is present
        # and not disabled in config.
        self.llm_enabled = bool(llm_provider) and self.settings.extraction.llm_mapping
        # Token/usage accounting for the audit log (read by the mapping stage).
        # `failures`/`last_error` exist so a run whose LLM calls all failed can report itself
        # as deterministic (what it actually was) instead of as LLM-mapped.
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "model": "",
                      "failures": 0, "last_error": "",
                      # Batch decisions thrown away, counted so the cost of the batch path is
                      # measurable: ids we never asked about, and answers the scoping gate
                      # refused. Both used to vanish on a bare `continue`.
                      "batch_unknown_ids": 0, "batch_refused": 0,
                      # How the statement-wise batch was actually cut up: one entry per provider
                      # call, so "one statement, two pages, one call" is verifiable from the run
                      # record rather than asserted in a docstring.
                      "batch_chunks": 0, "batch_max_items": 0,
                      # Wrong-variant answers the banner corrected instead of discarding, and the
                      # distinct routes taken. A silent correction is worse than a refusal: it puts
                      # a figure on a different line of the face with nothing to point at.
                      "family_resolved": 0, "family_routes": [],
                      # `binding.order` step 6: two concepts in each other's `confusable_with` that
                      # the evidence rates equally and nothing resolved. Counted because the answer
                      # is "both, for review", and a review item nobody can measure is a review
                      # item nobody prioritises.
                      "confusable_ties": 0,
                      # Rows refused because the caption names a concept the framework COMPUTES
                      # (`extraction_mode: derive`). Counted because the alternative the engine used
                      # to take — filing the figure on the nearest neighbouring subtotal — left no
                      # trace at all: see `_computed_claim`.
                      "computed_refused": 0}
        # System prompt = the base instruction + the ontology's own extraction policies and
        # worked examples, so the LLM follows one consistent, auditable rulebook.
        self._system = self._build_system()
        # The batch path decides many captions at once and is told each row's section, neither of
        # which the base instruction describes. Additive, so the per-line prompt is unchanged.
        self._batch_system = self._system + _LLM_BATCH_ADDENDUM

        # Precompute normalized alias → key index for the exact tier, and a concept
        # index (key → mapping) for description lookups.
        # alias → EVERY concept that claims it. Two sections legitimately share a caption:
        # "Owners of the parent" and "Non-controlling interests" appear under both the profit
        # split and the comprehensive-income split, and "Others" appears in every section. Keeping
        # one key per alias made the section-appropriate concept unreachable — the caption
        # resolved to the wrong section's concept, was refused by the section gate, and the row
        # ended up unmapped even though its concept existed.
        self._alias_index: dict[str, list[str]] = {}
        self._alias_by_key: dict[str, list[str]] = {}
        # The aliases of the COMPUTED concepts, indexed apart from the matchable ones and never
        # reachable through any tier. They are kept because a caption that names one is evidence
        # about the row — see `_computed_claim` for what is done with it.
        self._computed_alias_by_key: dict[str, list[str]] = {}
        self._by_key: dict[str, OntologyMapping] = {}
        # Concepts a v2 rulebook declares unreachable by MATCHING: the section "Others" buckets,
        # populated by the residual sweep alone (a section's parent minus its confirmed children).
        # Keyed on `alias_matching` alone, not on the conjunction with match_priority 0 /
        # exclusive_residual / never_sweep that the shipped file also carries, because that field is
        # the declared switch and a conjunction lets one omitted field silently unlock a bucket.
        # Locking matters because a residual's caption is the most attractive one in the ontology:
        # "Others" fuzzy-matches almost anything short, and a model offered a bucket will use it for
        # a row it cannot place. Either way the figure lands in the bucket that is supposed to be
        # the section's *unexplained* remainder, and the reconciliation that would have reported the
        # gap now ties. The sweep still assigns them — it reads the template, not this index.
        self._locked: set[str] = {m.canonical_key for m in ontology.mappings
                                  if m.alias_matching == "disabled"}
        # `extraction_mode: "derive"` — a concept the framework COMPUTES and a filing does not
        # print. It stays extractable (that is what `derive` means, and `_extractable_keys` still
        # reports it), but it is not something a printed caption may be bound to: offering it as a
        # mapping candidate asserts that a row on the page IS the derived subtotal, which then
        # overwrites the computation with whatever caption happened to fuzz against it. Keeping it
        # out is only half of it: a row whose caption names one is REFUSED rather than handed to the
        # nearest neighbour — see `_computed_claim`. The neighbouring value `extract_or_derive` is the
        # one that means "sometimes printed, sometimes arithmetic", and those concepts stay fully
        # matchable, since a filing that does print the subtotal must have the printed row read.
        self._computed_only: set[str] = {m.canonical_key for m in ontology.mappings
                                         if m.extraction_mode == "derive"}
        # One set for every index and payload below: a concept no printed caption may reach,
        # whether because it is a swept residual or because it is computed.
        self._unmatchable: set[str] = self._locked | self._computed_only
        # `binding.order` is a v2 declaration, and step 6 below (a tie between mutually-confusable
        # concepts is emitted for review, never picked by declaration order) is an implementation OF
        # it — so it is enabled by its presence. A v1 rulebook declares no binding order and no
        # `section_disambiguation` for the tie to be resolved by, so it keeps the behaviour it was
        # authored against: the highest-priority claimant, ties to the first declared.
        self._binding_order: list[str] = list(ontology.binding.order) if ontology.binding else []
        # canonical_key -> the sections the RULEBOOK says the concept may claim a row in, read off
        # `section_scope` (see `_sections_of` for why the declaration and not the key name).
        self._section_scope: dict[str, frozenset[str]] = {}
        for m in ontology.mappings:
            self._by_key[m.canonical_key] = m
            self._section_scope[m.canonical_key] = self._scope_tokens(m)
            if m.canonical_key in self._locked:
                continue         # a swept residual: its caption is never matched, nor weighed below
            # Index EVERY locale's aliases, not just the document's. A bilingual filing prints
            # both scripts on the same line, so restricting the index to the detected locale
            # makes a Chinese-only caption unmatchable in a document detected as English.
            # Recognising the printed text is locale-independent; the locale still orders
            # `aliases_for` for display/scoring preference.
            aliases = list(dict.fromkeys(
                m.aliases_for(self.locale)
                + [a for locale_aliases in m.aliases_i18n.values() for a in locale_aliases]
            ))
            if m.canonical_key in self._computed_only:
                self._computed_alias_by_key[m.canonical_key] = [
                    normalize_label(a) for a in aliases]
                continue
            self._alias_by_key[m.canonical_key] = [normalize_label(a) for a in aliases]
            for a in aliases:
                keys = self._alias_index.setdefault(normalize_label(a), [])
                if m.canonical_key not in keys:
                    keys.append(m.canonical_key)

    # -- individual tiers -------------------------------------------------

    @staticmethod
    def _scope_tokens(m: OntologyMapping) -> frozenset[str]:
        """The banner tokens one concept's ``section_scope`` resolves to.

        Empty means UNCONSTRAINED, and it is reached two ways that must not be told apart here: a
        concept declaring only a ``*_top_level`` scope (a statement total, which no banner may
        constrain) and a v1 concept declaring no scope at all, which falls back to its key namespace
        — also empty for a statement-level key. Both are "no banner refuses this concept".
        """
        if not m.section_scope:
            return sections_of_key(m.canonical_key)
        return frozenset(t for s in m.section_scope if (t := section_token_of_scope(s)))

    def _priority_of(self, canonical_key: str) -> int:
        """The concept's declared ``match_priority``, or 0 when it declares none.

        0 rather than a mid-scale guess: a v1 rulebook declares no priority anywhere, so every
        concept ties and every ordering below degenerates to exactly the order it had before.
        """
        m = self._by_key.get(canonical_key)
        return m.match_priority if (m is not None and m.match_priority is not None) else 0

    def _by_priority(self, keys: list[str]) -> list[str]:
        """Highest ``match_priority`` first, ties left in the order given (the sort is stable)."""
        return sorted(keys, key=self._priority_of, reverse=True)

    def _exact(self, norm: str, allowed=None, reroute=None) -> Candidate | None:
        """An exact alias hit, preferring one the caller's scoping allows.

        ``allowed`` is a predicate over canonical keys (the statement/section/exclusion gate).
        When several concepts share the alias, the highest-``match_priority`` claimant that fits
        where the caption was printed wins. The rulebook's binding order runs the alias tier in
        descending priority and says in as many words never to pick by declaration order, which is
        what taking the first claimant was: 83 aliases in the shipped file are claimed by more than
        one concept, so for those the answer was decided by where an editor happened to add a row.

        ``reroute`` is consulted ONLY when every claimant was refused — a caption that is an alias
        of one leaf of a collision family, printed under the banner of another, is answered by the
        banner instead of being left unmapped.
        """
        keys = self._alias_index.get(norm) or []
        if not keys:
            return None
        if allowed is None:
            return Candidate(max(keys, key=self._priority_of), MappingMethod.EXACT, 1.0)
        ok = [k for k in keys if allowed(k)]
        if ok:
            return Candidate(max(ok, key=self._priority_of), MappingMethod.EXACT, 1.0)
        for k in keys:
            target = reroute(k) if reroute is not None else None
            if target:
                return Candidate(target, MappingMethod.EXACT, 1.0, rerouted_from=k)
        return None

    def _mutually_confusable(self, a: str, b: str) -> bool:
        """Whether each concept names the other in its ``confusable_with``.

        MUTUAL on purpose. The field is a directed confusion graph and a one-way edge is usually a
        warning about a bigger concept ("do not confuse this leaf with that subtotal"); only a pair
        that each names the other is the rulebook saying these two are mistaken for one another,
        which is the set ``binding.order`` step 6 nominates.
        """
        ma, mb = self._by_key.get(a), self._by_key.get(b)
        return bool(ma and mb and b in ma.confusable_with and a in mb.confusable_with)

    def _confusable_tie(self, keys: list[str]) -> list[str]:
        """The subset of equally-rated concepts that ``binding.order`` step 6 forbids picking between.

        Returns [] unless at least two of them name each other in ``confusable_with``, and [] for a
        rulebook that declares no ``binding.order`` at all (see `_binding_order`). Order is preserved
        so the review item lists them as the tiers ranked them.
        """
        if not self._binding_order:
            return []
        tied = [k for k in keys
                if any(other != k and self._mutually_confusable(k, other) for other in keys)]
        return tied if len(tied) > 1 else []

    def _exact_tie(self, norm: str, allowed) -> list[str]:
        """Concepts claiming this exact alias that step 6 says may not be separated by priority.

        An alias claimed by two concepts is settled by descending ``match_priority`` (step 4) — but
        38 of the shipped file's 83 shared aliases are claimed by a mutually-confusable pair sitting
        at the SAME priority (current vs non-current borrowings, notes payable, properties under
        development). For those, taking the higher priority is taking the first declared, which step
        6 forbids in as many words. The banner normally separates them and this never fires; when it
        does not, the honest answer is both, for review.
        """
        keys = [k for k in (self._alias_index.get(norm) or []) if allowed(k)]
        if len(keys) < 2:
            return []
        top = max(self._priority_of(k) for k in keys)
        return self._confusable_tie([k for k in keys if self._priority_of(k) == top])

    def _vetoed(self, canonical_key: str, caption: str) -> bool:
        """Whether the concept's ``exclude_hints`` rule this caption out.

        The field is named exclude and the ontology editor presents it as "never map a caption
        like this here", so it has to hold across every tier. Applying it only inside the rule
        tier meant an excluded caption could still arrive via fuzzy or an alias — an editor
        would add the exclusion, see nothing change, and have no way to fix a mis-mapping.

        MATCHED CASE-INSENSITIVELY, and that is not cosmetic. ``text`` is lowercased here while the
        pattern comes verbatim from the rulebook, so a hint typed in the case a human naturally uses
        — "Finance Cost", "Non-current Assets", "Trade payables" — could never match anything. Every
        one of the 415 hints shipped in the rulebook happens to be lowercase, so nothing was broken;
        the trap was waiting for the next editor, and a reviewer adding thirteen hints through the
        workbook hit all thirteen. ``re.IGNORECASE`` rather than lowercasing the pattern, because
        lowercasing would silently invert the meaning of ``\S``, ``\B``, ``\W`` and ``\D``.

        ``regex_hints`` is matched the same way in :meth:`_rule` for the same reason.
        """
        m = self._by_key.get(canonical_key)
        if m is None or not m.exclude_hints:
            return False
        text = caption.lower()
        return any(re.search(ex, text, re.IGNORECASE) for ex in m.exclude_hints)

    def _rule(self, raw: str, keys: set[str] | None = None) -> Candidate | None:
        """The rule tier of ``binding.order`` step 4: regex / keyword hints, in DESCENDING
        ``match_priority``, with ``exclude_hints`` as a hard veto.

        ``keys`` is the restricted candidate set (step 3); None means the whole rulebook, which is
        what a caller with no statement or section to restrict by has.

        The ordering is the fix step 4 asks for: when several concepts' hints fire, the winner used
        to be whichever the file declared first — so an editor adding a broad ``regex_hints`` entry
        high in the file pre-empted every specific concept below it, and the only way to find out
        was to read the JSON in order.
        """
        # Both spellings of the caption: as printed (lowercased) and NORMALISED the way the alias
        # tier normalises it. An authored hint is anchored far more often than not — the rulebook's
        # own are — and an anchored hint never fired on a real caption, because the printed line
        # carries punctuation and a bilingual tail that the anchors then have to account for:
        # "^net cash.*investing activities$" is defeated by "Net cash flows from/(used in) investing
        # activities 投資活動…". The rulebook says the rule tier runs on the normalised alias, so it
        # sees the normalised text too; the raw one is kept because a hint may deliberately target
        # punctuation normalisation removes.
        text = raw.lower()
        norm = normalize_label(raw)
        candidates = (text, norm) if norm and norm != text else (text,)
        hits: list[str] = []
        for m in self.ontology.mappings:
            # A locked residual is unreachable by matching, and a regex or keyword hint authored on
            # one would otherwise reopen the hole the alias index was closed for. A `derive` concept
            # is out for the other reason: it is computed, not read off the page.
            if m.canonical_key in self._unmatchable:
                continue
            if keys is not None and m.canonical_key not in keys:
                continue
            if any(re.search(ex, t, re.IGNORECASE) for ex in m.exclude_hints for t in candidates):
                continue
            hit = False
            if any(re.search(rx, t, re.IGNORECASE) for rx in m.regex_hints for t in candidates):
                hit = True
            elif m.keyword_hints and any(all(kw.lower() in t for kw in m.keyword_hints)
                                         for t in candidates):
                hit = True
            if hit:
                hits.append(m.canonical_key)
        if not hits:
            return None
        # Several hits stay ambiguous (0.6, below every accept threshold) — but the key reported is
        # the highest-priority claimant, since that is the one the shortlist and `det_top` carry
        # forward to the semantic tier.
        return Candidate(max(hits, key=self._priority_of), MappingMethod.RULE,
                         0.95 if len(hits) == 1 else 0.6)

    @staticmethod
    def _alias_coverage(caption: str, alias: str) -> float:
        """Share of the ALIAS's words the caption accounts for, tolerating misspellings.

        Direction matters: a section heading ("LIABILITIES") is trivially contained in a
        longer alias ("non-current lease liabilities"), so measuring how much of the *caption*
        is explained rewards fragments. Measuring how much of the *alias* is explained is what
        separates a real match from a substring of one.

        Token matching is itself fuzzy, because exact identity would punish the typos this
        tier exists to absorb ("trade recievables" covers "receivables").
        """
        alias_tokens = alias.split()
        if not alias_tokens:
            return 0.0
        caption_tokens = caption.split()
        if not caption_tokens:
            return 0.0
        hit = 0
        for at in alias_tokens:
            if any(at == ct or fuzz.ratio(at, ct) >= 80 for ct in caption_tokens):
                hit += 1
        return hit / len(alias_tokens)

    def _alias_similarity(self, norm: str, alias: str) -> float:
        """How nearly this caption IS that alias — a measurement, never a decision.

        No tier maps a row on this. It answers two questions that need a notion of "near enough
        to be the same wording": whether the deterministic evidence dissents from the model's
        choice, and whether a caption is the printed name of a concept the framework COMPUTES
        (which must then not be re-homed onto a neighbour). Both compare a caption to an alias
        the rulebook authored; neither can produce a mapping.

        Deliberately NOT ``token_set_ratio``: that scores 100 whenever the caption's tokens
        are a subset of the alias's, so every heading and wrapped-line fragment scored a
        perfect 1.0 against some longer concept (observed on a real filing: "LIABILITIES" ->
        non-current lease liabilities at 1.00). ``token_sort_ratio`` keeps length differences
        visible, and the coverage factor pulls down a claim that only explains a small part of
        the alias it claims to be.
        """
        base = fuzz.token_sort_ratio(norm, alias) / 100.0
        coverage = self._alias_coverage(norm, alias)
        return base * (0.4 + 0.6 * coverage)

    def _alias_evidence(self, canonical_key: str, norm_segments: list[str]) -> float:
        """How well the caption explains any ONE alias of a concept — 1.0 for exact identity.

        One scale for every alias claim, so two concepts' claims on one caption are comparable.
        """
        best = 0.0
        for alias in self._alias_by_key.get(canonical_key) or []:
            for norm in norm_segments:
                if not alias or not norm:
                    continue
                score = 1.0 if alias == norm else self._alias_similarity(norm, alias)
                best = max(best, score)
        return best

    def _computed_claim(self, norm_segments: list[str]) -> tuple[str, float] | None:
        """The COMPUTED concept this caption is evidence for, and how strong — or None.

        ``extraction_mode: derive`` means the framework computes the concept and no printed caption
        may be bound to it, which is why it is out of every tier and out of every payload. Hiding a
        concept is not the same as refusing a row, though, and that gap was the defect: with its own
        concept unreachable the caption's next-best evidence is a DIFFERENT concept, and the tiers
        below file the figure there. Measured on the shipped rulebook, "Profit before exceptional
        items and tax" — the verbatim alias of its one `derive` concept — was filed as
        ``pl_profit_before_tax`` by the fuzzy tier at 0.61, accepted, unflagged. Those two subtotals
        differ by exactly the exceptional items, so the figure lands on the wrong line of the P&L and
        the statement still ties: the class of error nothing downstream can see.

        Evidence is only reported at the strength a MATCH would have been accepted at — exact alias
        identity, or an alias similarity clearing both ``evidence_floor`` and
        ``alias_coverage_floor``. Anything weaker is a coincidence of wording, and it must not
        refuse a row that some other concept has real evidence for.

        ``extract_or_derive`` is deliberately NOT here. That value means the subtotal is sometimes
        printed and sometimes left to arithmetic, so a row printed with its caption IS the concept
        and must be matched — those concepts stay fully matchable and claim nothing through here.
        `derive` is the one value that says the face does not print it at all.
        """
        s = self.settings.extraction
        best: tuple[str, float] | None = None
        for key, aliases in self._computed_alias_by_key.items():
            for alias in aliases:
                for norm in norm_segments:
                    if not alias or not norm:
                        continue
                    if alias == norm:
                        score = 1.0
                    else:
                        score = self._alias_similarity(norm, alias)
                        if (score < s.evidence_floor
                                or self._alias_coverage(norm, alias) < s.alias_coverage_floor):
                            continue
                    if best is None or score > best[1]:
                        best = (key, score)
        return best

    def _refused_as_computed(self, norm_segments: list[str], rival: float) -> str | None:
        """The computed concept to refuse this caption to, when nothing matchable claims it better.

        ``rival`` is the strength of the best claim a MATCHABLE concept has on the caption. A
        computed concept only takes the row off the table when it explains the caption at least as
        well: a caption another concept genuinely matches better is still that concept's row.
        """
        claim = self._computed_claim(norm_segments)
        if claim is None or claim[1] < rival:
            return None
        self.usage["computed_refused"] += 1
        return claim[0]

    def _build_system(self) -> str:
        """Base instruction + the ontology's global extraction policies + worked examples."""
        g = self.ontology.global_rules
        lines: list[str] = [_LLM_SYSTEM]
        policies: list[str] = []
        policies += list(g.parent_child_allocation)
        if g.duplicate_fact_rule:
            policies.append(g.duplicate_fact_rule)
        if g.other_income_rule:
            policies.append(g.other_income_rule)
        policies += list(g.others_policy)
        if g.totals_policy:
            policies.append(g.totals_policy)
        if g.no_fabricated_split:
            policies.append(g.no_fabricated_split)
        if policies:
            lines.append("\nPolicies to follow:")
            lines += [f"- {p}" for p in policies]
        if self.ontology.worked_examples:
            lines.append("\nWorked examples:")
            for ex in self.ontology.worked_examples[:6]:
                lines.append("- " + json.dumps(ex.model_dump(exclude_defaults=True), ensure_ascii=False))
        return "\n".join(lines)

    def _concept_payload(self, keys: list[str]) -> list[dict]:
        """Candidate concepts with the criteria the LLM reasons over — definition, include/
        exclude, confusable-with (as labels), value_scope. Non-extracted headings skipped."""
        out = []
        for k in keys:
            m = self._by_key.get(k)
            if m is None or m.extraction_mode == "do_not_extract":
                continue
            # Also the choke point for the residual lock and the `derive` lock, not only
            # `_extractable_keys`/`_mappable_keys`: the capped shortlist in `match` is assembled from
            # the rule tier's keys rather than from either list, so a concept kept out of one
            # route has to be kept out of the other as well. A concept the model cannot see is a
            # concept the model cannot pick.
            if k in self._unmatchable:
                continue
            entry: dict = {
                "canonical_key": k,
                "label": m.label or k.replace("_", " "),
                "definition": m.meaning(),
                "value_scope": m.value_scope,
                "example_aliases": m.aliases_for(self.locale)[:4],
            }
            if m.include:
                entry["include"] = m.include
            if m.exclude:
                entry["exclude"] = m.exclude
            if m.confusable_with:
                entry["confusable_with"] = [
                    (self._by_key[c].label or c) for c in m.confusable_with if c in self._by_key
                ]
            if m.decomposition_rule:
                entry["decomposition_rule"] = m.decomposition_rule
            # The rulebook's own prose about the decisions this tier is here to make. All three are
            # sparse in the shipped file (18 / 8 / 2 concepts), so this costs input tokens only where
            # the editor actually wrote something.
            if m.section_disambiguation:
                # WHICH of two look-alike captions this is. The semantic tier is the only reader
                # that can act on it: the deterministic tiers compare strings, and step 6 hands a
                # tie between two mutually-confusable concepts here precisely because the answer is
                # in this sentence ("Bind by printed section only", "adjacent to trade payables").
                entry["section_disambiguation"] = m.section_disambiguation
            if m.derivation:
                # How the concept is computed when the face does not print it. For an
                # `extract_or_derive` subtotal that is the difference between "this row IS the
                # subtotal" and "the subtotal is arithmetic and this row is one of its components".
                entry["derivation"] = m.derivation
                entry["extraction_mode"] = m.extraction_mode
            if m.is_gross_parent:
                # Containment, stated to the model as well as enforced after it
                # (stages.map_ontology): a gross parent may not be filed alongside the children it
                # contains, so a filing that prints the components must not also claim this concept.
                entry["is_gross_parent"] = True
                entry["children_if_decomposed"] = list(m.children_if_decomposed)
            if m.equivalence is not None and m.equivalence.with_:
                # One economic fact under two captions ("Net assets" / "Total equity"). Named so the
                # model does not treat the twin as a rival reading of the caption.
                entry["same_fact_as"] = {"canonical_key": m.equivalence.with_,
                                         "relation": m.equivalence.relation,
                                         "rule": m.equivalence.rule}
            out.append(entry)
        return out

    def _residual_expectations(self, statement: str | None,
                               sections: set[str] | None = None) -> list[dict]:
        """What each section's residual is EXPECTED to absorb — ``expected_components``.

        The buckets themselves are locked out of every candidate list, which leaves the model with
        no licence to answer "none of these" for the captions the rulebook already knows have no
        dedicated concept ("Bank overdrafts", "Contract assets", "Club memberships"). Offered a list
        of concepts and a caption, a model picks the nearest one; the figure then lands on a specific
        wrong line instead of in the section remainder, and the section still ties.

        So the expectations are handed over WITHOUT naming the bucket's canonical_key: the answer
        being licensed is an empty key, which routes the row to the sweep that owns those buckets.
        """
        out: list[dict] = []
        for key in sorted(self._locked):
            m = self._by_key.get(key)
            if m is None or not m.expected_components:
                continue
            if statement and not self._in_statement(key, statement):
                continue
            tokens = self._sections_of(key)
            if sections is not None and not (tokens & sections):
                continue
            out.append({"section": ", ".join(sorted(tokens)) or "statement",
                        "captions_with_no_dedicated_concept": list(m.expected_components)})
        return out

    def _extractable_keys(self) -> list[str]:
        """The concepts the framework may EXTRACT — by matching a caption or by computing.

        Locked residuals are excluded even though they are extracted: they are extracted by the
        section sweep, which reads the template and never comes through here. Leaving them in put
        every section's "Others" bucket in front of the model on every call.
        """
        return [k for k, m in self._by_key.items()
                if m.extraction_mode != "do_not_extract" and k not in self._locked]

    def _mappable_keys(self) -> list[str]:
        """The concepts a printed CAPTION may be bound to — extractable, minus the computed ones.

        The narrower of the two sets, and the one every candidate list is built from. A `derive`
        concept is extractable but not mappable: see `_computed_only`.
        """
        return [k for k in self._extractable_keys() if k not in self._computed_only]

    def _llm(self, raw: str, context: str | None, keys: list[str]) -> Candidate | None:
        """Description/criteria-based decision — the key driver in the ensemble."""
        if self.llm_provider is None:
            return None
        candidates = self._concept_payload(keys)
        if not candidates:
            return None
        user = json.dumps({"caption": raw, "context": context or "", "candidates": candidates},
                          ensure_ascii=False, indent=2)
        try:
            decision, meta = self.llm_provider.complete_structured(
                system=self._system,
                messages=[{"role": "user", "content": user}],
                response_schema=LlmMappingDecision,
                max_tokens=512,
            )
        except Exception as exc:  # noqa: BLE001
            # Provider unreachable/misconfigured (commonly a missing API key) → the
            # deterministic ensemble decides. Record WHY: a run that silently degrades and
            # still reports itself as LLM-mapped overstates the quality of its own output.
            self.usage["failures"] += 1
            if not self.usage["last_error"]:
                self.usage["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            return None
        self.usage["calls"] += 1
        self.usage["input_tokens"] += int(meta.get("input_tokens") or 0)
        self.usage["output_tokens"] += int(meta.get("output_tokens") or 0)
        self.usage["model"] = meta.get("model", self.usage["model"])
        key = (decision.canonical_key or "").strip()
        # `_unmatchable` as well as unknown: a concept was kept out of the payload precisely because
        # no printed caption may be bound to it, and a model naming one anyway is not a licence to
        # file the row there. A locked residual named by the model would put the figure in the
        # bucket that is supposed to be the section's UNEXPLAINED remainder.
        if not key or key not in self._by_key or key in self._unmatchable:
            return None
        return Candidate(key, MappingMethod.LLM, max(0.0, min(1.0, decision.confidence)),
                         allocation_status=(decision.allocation_status or "").strip() or None)

    # -- orchestration ----------------------------------------------------

    # The statements this gate knows. Canonical keys are ALSO namespaced by statement
    # (bs_/pl_/cf_/eq_...), which is the fallback reading — see `_statement_of`.
    _STATEMENTS = frozenset(_STATEMENT_OF_PREFIX.values())

    def _statement_of(self, canonical_key: str) -> str | None:
        """The statement the RULEBOOK says the concept is on, or None when nothing says.

        ``statement`` is the declaration and is what this reads; the key namespace
        (:func:`statement_of_key`) is only the fallback, for a v1 concept that declares none and for
        a key from outside this rulebook. Exactly the argument `_sections_of` makes about sections,
        and it had the same hole: the statement gate read ``canonical_key.split("_", 1)[0]``, so
        setting ``section_defaults.bs_s5_current_liabilities.statement`` to ``profit_and_loss`` left
        every current-liability concept still gated as a balance-sheet concept. The rulebook — the
        only place a reviewer can look up what the pipeline does — said one thing and the engine did
        another, and nothing in the output showed the disagreement.
        """
        m = self._by_key.get(canonical_key)
        declared = normalize_statement(m.statement) if m is not None else ""
        return declared or statement_of_key(canonical_key)

    def _in_statement(self, canonical_key: str, statement: str | None) -> bool:
        """False only when the concept clearly belongs to a DIFFERENT statement than the caption.

        Unknown statements, and concepts that neither the rulebook nor their key namespace places on
        one, are always allowed — the constraint suppresses confident cross-statement errors without
        silently dropping concepts it cannot place.
        """
        want = normalize_statement(statement)
        if want not in self._STATEMENTS:
            return True
        have = self._statement_of(canonical_key)
        return have is None or have == want

    def _section_of(self, text: str | None) -> str | None:
        return section_of_banner(text)

    def _sections_of(self, canonical_key: str) -> frozenset[str]:
        """The sections the RULEBOOK says this concept may claim a row in.

        ``section_scope`` is the declaration and is what this reads — the point of
        ``binding.order``'s "a concept can only claim a row printed inside one of its section_scope
        values". The key namespace is only the fallback, for a concept that declares no scope (every
        v1 concept, and any v2 concept whose scope ids name no section this vocabulary knows).

        They agree across all 173 concepts of the shipped file, which is exactly why reading the key
        was survivable and not defensible: the two diverge the moment someone declares a concept in
        a section its key name does not match, and then the gate quietly obeys the key while the
        rulebook — the only place a reviewer can look up what the pipeline does — says otherwise.
        """
        declared = self._section_scope.get(canonical_key)
        if declared is not None:
            return declared
        # A key from outside this rulebook (a caller checking the gate against a template key).
        return sections_of_key(canonical_key)

    def _in_section(self, canonical_key: str, section: str | None) -> bool:
        """False only when the concept's ``section_scope`` excludes the section the banner names.

        Concepts scoped to no section (``bs_total_assets``, ``pl_profit_before_tax`` — a
        ``*_top_level`` scope, or no scope and no section namespace in the key) and unrecognised
        banners are always allowed: like the statement constraint, this suppresses a confident wrong
        answer without dropping concepts it cannot place.
        """
        want = section_of_banner(section)
        if not want:
            return True
        have = self._sections_of(canonical_key)
        if not have:
            # A key with no section namespace is normally unconstrained. The exception is one leaf
            # of a collision family: both P&L bottom lines are statement-level keys, so without this
            # arm "LOSS FOR THE YEAR" under a "TOTAL COMPREHENSIVE" banner is waved through onto
            # `pl_profit_for_the_year` — the largest figure on the statement filed as the wrong fact
            # at confidence 1.0, with the comprehensive-income line left empty. Only ever narrows:
            # the banner must identify exactly one leaf, and a different one.
            return family_leaf_named_by(canonical_key, section,
                                        sections_of=self._sections_of) is None
        return want in have

    def _family_route(self, canonical_key: str, statement: str | None,
                      section: str | None, caption: str) -> str | None:
        """The sibling to re-route a REFUSED answer to, or None to let the refusal stand.

        Called only where the gate has already said no, so an answer the gate accepts is never
        rewritten. The destination goes through the same gate: re-routing may correct which variant
        of a fact was chosen, never smuggle a concept past the statement, exclusion or
        exclusive-vocabulary arms — and never into a concept no match may reach at all: a locked
        residual, or a computed (`derive`) concept.
        """
        target = family_leaf_named_by(canonical_key, section, sections_of=self._sections_of)
        if target is None or target not in self._by_key or target in self._unmatchable:
            return None
        if not self._allowed(target, statement, section, caption):
            return None
        return target

    def _record_route(self, from_key: str, to_key: str) -> None:
        """Count a re-route and remember the route, following `batch_refused`/`batch_unknown_ids`.

        Distinct routes only, and capped: what an auditor needs is which concepts were corrected,
        not one entry per row of a three-hundred-row filing.
        """
        self.usage["family_resolved"] += 1
        routes = self.usage["family_routes"]
        route = f"{from_key}->{to_key}"
        if route not in routes and len(routes) < 20:
            routes.append(route)

    def _allowed(self, canonical_key: str, statement: str | None,
                 section: str | None, caption: str = "") -> bool:
        """Whether a concept may be considered for a caption printed here.

        Two of the four constraints are structural, not lexical: the statement the page is, and
        the section banner the row sits under. The third is the concept's own declared exclusions,
        and the fourth is the caption naming a mutually exclusive class the concept is not in.
        They are combined in one place so no call site can apply only part of the scoping.
        """
        return (self._in_statement(canonical_key, statement)
                and self._in_section(canonical_key, section)
                and not (caption and self._vetoed(canonical_key, caption))
                and not (caption and _names_a_different_class(
                    canonical_key, caption, self._sections_of(canonical_key))))

    def _answer_confusable_tie(self, raw_label: str, context: str | None,
                               tied: list[str]) -> MappingResult:
        """``binding.order`` step 6: resolve a mutual-``confusable_with`` tie, or emit both.

        The semantic tier is offered exactly the tied concepts, so the decision is made on the one
        piece of evidence that can settle it — each concept's ``section_disambiguation``, which
        `_concept_payload` carries. With no provider, or when the model abstains or answers outside
        the pair, both are emitted as candidates and the row is routed to review. Never a pick by
        declaration order, which is what "highest priority, ties to the first declared" was for the
        38 shared aliases in the shipped file whose claimants sit at equal priority.
        """
        llm = self._llm(raw_label, context, self._by_priority(tied)) if self.llm_enabled else None
        if llm is not None and llm.canonical_key in tied:
            alloc = llm.allocation_status or (
                "direct_exclusive"
                if self._by_key[llm.canonical_key].value_scope == "exclusive_leaf" else None)
            return MappingResult(
                canonical_key=llm.canonical_key, method=MappingMethod.LLM, confidence=llm.score,
                candidates=[llm], needs_review=llm.score < self.settings.extraction.auto_accept_confidence,
                scores={"exact": 1.0, "llm": llm.score}, allocation_status=alloc,
                agreement=["llm", "exact"],
            )
        self.usage["confusable_ties"] += 1
        return MappingResult(None, MappingMethod.UNMATCHED, 0.0,
                             [Candidate(k, MappingMethod.EXACT, 1.0) for k in tied],
                             True, {"exact": 1.0}, allocation_status="unmapped_review")

    @staticmethod
    def _best_per_key(cands: list[Candidate]) -> list[Candidate]:
        """Highest-scoring candidate per concept, best first (segments can both propose one)."""
        best: dict[str, Candidate] = {}
        for c in cands:
            cur = best.get(c.canonical_key)
            if cur is None or c.score > cur.score:
                best[c.canonical_key] = c
        return sorted(best.values(), key=lambda c: c.score, reverse=True)

    def match(self, raw_label: str, context: str | None = None,
              statement: str | None = None, section: str | None = None) -> MappingResult:
        """A COMBINATION of methods — no single one is authoritative:

        exact identity short-circuits (free); otherwise the rule tier and the model each
        contribute candidate evidence, the LLM makes the semantic, criteria-based call
        (the key driver), and cross-method agreement adjusts confidence and review routing.
        Falls back to the deterministic margin policy when no LLM is configured/abstains.

        ``statement`` is the statement the caption was printed on (``balance_sheet``,
        ``profit_and_loss``, ``cash_flow``, ``changes_in_equity``) when the page classifier
        determined it. Concepts belonging to a *different* statement are then excluded: a
        balance-sheet caption must not resolve to a cash-flow concept just because the words
        overlap ("Finance costs" appears on both), which is otherwise a whole class of
        confidently-wrong mapping.

        ``section`` is the section banner the row was printed under ("NON-CURRENT
        LIABILITIES", 流動負債). Statements print one caption under two banners — a property
        developer's "Interest-bearing bank and other borrowings" and "Senior notes and domestic
        bonds" each appear once as non-current and once as current — so without the banner the
        two rows are indistinguishable and collapse onto one concept.
        """
        segments = label_segments(raw_label)
        norm = normalize_label(raw_label)
        norm_segments = [n for n in (normalize_label(seg) for seg in segments) if n]
        s = self.settings
        scores: dict[str, float] = {}

        def _ok(k: str) -> bool:
            return self._allowed(k, statement, section, raw_label)

        # 1. Exact normalized-alias identity — unambiguous and free. Tried on the caption and,
        #    for a bilingual line, on each script's half (either alone can be an exact alias).
        for seg in segments:
            seg_norm = normalize_label(seg)
            # `binding.order` step 6, before step 4 is allowed to settle it by priority: two
            # concepts that claim this alias, sit at the same priority and name each other as
            # confusable are a tie the rulebook forbids breaking by declaration order. The semantic
            # tier gets the pair (with each one's `section_disambiguation`); if there is no semantic
            # tier, or it abstains, both are emitted and the row goes to review.
            tied = self._exact_tie(seg_norm, allowed=_ok)
            if tied:
                return self._answer_confusable_tie(raw_label, context, tied)
            # The scoping gate is handed to the alias lookup rather than applied after it: when
            # two concepts claim the same alias, the one that fits where this caption was printed
            # has to be the one returned.
            exact = self._exact(
                seg_norm,
                allowed=_ok,
                # An alias of one family leaf printed under another's banner is corrected here too,
                # not only on the batch path: the alias is real evidence of WHAT the row is, and
                # dropping it would trade a confidently-wrong answer for an unmapped row rather than
                # for the right one. The per-line path is also all there is when no LLM is running.
                reroute=lambda k: self._family_route(k, statement, section, raw_label))
            if exact:
                if exact.rerouted_from:
                    self._record_route(exact.rerouted_from, exact.canonical_key)
                return MappingResult(exact.canonical_key, exact.method, 1.0, [exact], False,
                                     {"exact": 1.0}, allocation_status="direct_exclusive",
                                     rerouted_from=exact.rerouted_from)

        # 2. `binding.order` step 3 — RESTRICT the candidate set to the concepts the rulebook lets a
        #    row printed here be bound to, BEFORE any matching runs. It used to be a filter applied
        #    to each tier's OUTPUT, which reaches the same winner but not the same shortlist: the
        #    capped top-8 handed to the semantic tier was drawn from a ranking in which out-of-section
        #    concepts had already taken places, so a restricted concept could be squeezed off the list
        #    by one the gate was about to refuse anyway.
        allowed_keys = {k for k in self._mappable_keys() if _ok(k)}

        # 3. Deterministic evidence (each method contributes; none forced out). The rule tier runs
        #    per script segment and keeps the best claim, so "REVENUE 收益" is read as well as the
        #    monolingual "Revenue" would be.
        rule = next((r for r in (self._rule(seg, allowed_keys) for seg in segments) if r), None)
        by_method: dict[str, set[str]] = {}
        pool: list[Candidate] = []
        if rule:
            scores["rule"] = rule.score
            by_method["rule"] = {rule.canonical_key}
            pool.append(rule)
        best_by_key: dict[str, Candidate] = {}
        for c in pool:
            cur = best_by_key.get(c.canonical_key)
            if cur is None or c.score > cur.score:
                best_by_key[c.canonical_key] = c
        # Score first, then declared priority: priority is an ordering, not a score, so it may only
        # settle a tie between two concepts the evidence rates equally. Which of those the shortlist
        # cap kept, and which one `det_top` reported, was previously dict insertion order.
        ranked = sorted(best_by_key.values(),
                        key=lambda c: (c.score, self._priority_of(c.canonical_key)), reverse=True)
        det_top = ranked[0] if ranked else None

        # 3b. A caption that names a concept the framework COMPUTES may not be re-homed onto whatever
        #     happens to score next best. `derive` keeps the concept out of every tier and out of the
        #     payload, which makes its own row unmappable — and then the tiers below file the figure
        #     on a neighbouring subtotal instead. Refused here, above the semantic tier as well as
        #     the deterministic one, because the model is not shown the concept either and answers
        #     the same way. Only when the computed claim is at least as strong as the best claim a
        #     matchable concept has (see `_refused_as_computed`).
        computed = self._refused_as_computed(norm_segments,
                                             det_top.score if det_top is not None else 0.0)
        if computed is not None:
            return MappingResult(None, MappingMethod.UNMATCHED, 0.0, ranked[:5], True, scores,
                                 allocation_status="unmapped_review", computed_claim=computed)

        # 4. LLM semantic decision (`binding.order` step 5) — the key driver, shown the
        #    deterministic shortlist, or the whole RESTRICTED set for a small ontology, plus each
        #    concept's criteria. Never the full ontology: the restriction is step 3's, applied above.
        if self.llm_enabled:
            all_keys = [k for k in self._mappable_keys() if k in allowed_keys]
            if len(all_keys) <= s.extraction.llm_candidate_cap:
                shortlist = all_keys
            else:
                # Deterministic evidence first, then the rest of the RESTRICTED set to fill the cap.
                # The fill is not padding: with the fuzzy tier gone the evidence here is a rule hit
                # or nothing at all, and a section whose concepts merely have no hints authored on
                # them would otherwise reach the model as a shortlist of one — or of none, which
                # would leave the model to answer about a set it was never shown. The restriction to
                # the section (step 3) is what keeps the fill honest; ``_by_priority`` below decides
                # the reading order within it.
                shortlist = list(dict.fromkeys(
                    ([rule.canonical_key] if rule else [])
                    + self._by_priority(all_keys)))[: s.extraction.llm_candidate_cap]
            # Offered in descending match_priority, so the long specific concept is read before the
            # short generic one it collides with on token overlap ("Total assets less current
            # liabilities", 86, ahead of "Total current liabilities", 82 — the pair the rulebook's
            # own note on match_priority calls out). Applied AFTER the cap on purpose: priority
            # decides what the model reads first, never which concepts it is allowed to see, so a
            # high-priority concept with no evidence behind it cannot evict an evidenced one.
            shortlist = self._by_priority(shortlist)
            llm = self._llm(raw_label, context, shortlist)
            if llm is not None:
                scores["llm"] = llm.score
                # Corroboration across methods — agreement raises confidence, a strong
                # lexical disagreement lowers it and flags review.
                agreement = [meth for meth, keys in by_method.items() if llm.canonical_key in keys]
                conf = llm.score
                if agreement:
                    conf = min(1.0, llm.score + 0.10 * (1.0 - llm.score))
                elif det_top is not None and det_top.score >= s.extraction.evidence_floor:
                    conf = llm.score * 0.85
                needs_review = (
                    conf < s.extraction.auto_accept_confidence
                    or (not agreement and det_top is not None
                        and det_top.score >= s.extraction.evidence_floor)
                )
                alloc = llm.allocation_status
                if alloc is None:
                    scope = self._by_key[llm.canonical_key].value_scope
                    alloc = "direct_exclusive" if scope == "exclusive_leaf" else None
                return MappingResult(
                    canonical_key=llm.canonical_key, method=MappingMethod.LLM, confidence=conf,
                    candidates=[llm] + ranked[:4], needs_review=needs_review, scores=scores,
                    allocation_status=alloc, agreement=["llm", *agreement],
                )

        # 5. Deterministic decision (no LLM configured, or the LLM abstained). Exact already
        #    returned above, so the rule tier is all that is left — and if it does not claim the
        #    row, the row is left UNMAPPED for a human. There is deliberately no last-resort match
        #    on resemblance: a caption nothing can place is a visible gap, where a plausible wrong
        #    answer is a figure on the wrong line of a statement that still ties.
        #
        #    First, though, step 6: when the top-scoring concepts are rated IDENTICALLY and name each
        #    other as confusable, no deterministic method can separate them and the tie-break below
        #    would fall to declared priority and then to declaration order. Emit both and route to
        #    review, which is what the rulebook asks for and what a reviewer can act on — a coin-flip
        #    at confidence 0.95 is not reviewable, because nothing about it looks uncertain.
        if det_top is not None:
            tie = self._confusable_tie([c.canonical_key for c in ranked
                                        if c.score == det_top.score])
            if tie:
                self.usage["confusable_ties"] += 1
                return MappingResult(None, MappingMethod.UNMATCHED, 0.0,
                                     [c for c in ranked if c.canonical_key in tie], True, scores,
                                     allocation_status="unmapped_review")
        if rule and rule.score >= 0.9:
            return MappingResult(rule.canonical_key, rule.method, rule.score, [rule], False,
                                 {**scores, "rule": rule.score}, allocation_status="direct_exclusive")

        # Only the rule tier reaches here (a single-claimant hit returned above at 0.95), so this
        # is the AMBIGUOUS rule hit: several concepts' hints fired, the score is 0.6, and the key
        # reported is the highest-priority claimant. It is named rather than dropped — a reviewer
        # given a candidate can confirm or re-map it, where an unmapped row makes them find it —
        # and it carries needs_review, because 0.6 clears no accept bar.
        primary = [c for c in ranked if c.method is MappingMethod.RULE]
        if primary:
            top = primary[0]
            runner = primary[1].score if len(primary) > 1 else 0.0
            accept = (top.score >= s.extraction.auto_accept_confidence
                      and (top.score - runner) >= s.extraction.mapping_margin)
            return MappingResult(
                canonical_key=top.canonical_key, method=top.method, confidence=top.score,
                candidates=primary[:5], needs_review=not accept, scores=scores,
                allocation_status="direct_exclusive" if accept else "unmapped_review",
            )

        # Nothing confident — route to review unmapped rather than guessing from the wording.
        return MappingResult(None, MappingMethod.UNMATCHED, 0.0, ranked[:5], True, scores,
                             allocation_status="unmapped_review")

    # One batch call's RESPONSE budget, and the chunk size it implies.
    #
    # Measured from the response envelope rather than guessed: `LlmBatchDecision` serialises one
    # decision as {"item_id": "<uuid>", "canonical_key": "…", "confidence": 0.95,
    # "allocation_status": "…"}, which is 238 characters for this file's longest canonical_key (101
    # chars) with an allocation_status set — measured by dumping the model, not estimated from the
    # schema. JSON made of UUIDs and long snake_case identifiers tokenises at roughly three
    # characters per token, so one decision costs about 80 response tokens.
    _BATCH_RESPONSE_TOKENS_PER_ITEM = 80
    _BATCH_RESPONSE_RESERVE = 256          # the envelope itself, plus a margin against truncation
    # The largest chunk whose worst-case response still fits a conventional 8k completion budget with
    # headroom — and comfortably more than one HKEX statement page (40-60 rows), so the two-page
    # statement this batching exists to keep whole is still decided in ONE call.
    BATCH_MAX_ITEMS = 80

    @classmethod
    def _batch_max_tokens(cls, n_items: int) -> int:
        """The response budget for a chunk of ``n_items`` decisions.

        Derived from the chunk instead of reusing ``settings.llm.max_tokens`` (or the 4096 constant
        that stood here), because that number is a REQUEST cap shared with every other call in the
        app and has nothing to do with how many decisions were asked for. At 80 tokens a decision it
        truncates a batch of ~48 items, and a truncated batch response is not a partial answer: the
        JSON fails to parse, the whole chunk falls back to per-line matching, and the cross-line
        context the batch existed for is lost silently — the run still reports itself as LLM-mapped.
        """
        return cls._BATCH_RESPONSE_RESERVE + n_items * cls._BATCH_RESPONSE_TOKENS_PER_ITEM

    def match_batch(self, items: list[tuple[str, str]],
                    statement: str | None = None,
                    sections: dict[str, str | None] | None = None) -> dict[str, MappingResult]:
        """Batch mapping: decide many captions in one grounded LLM call so cross-line judgements
        (containment, residual, 'Others') have context. The model references the provided item_ids
        and candidate keys — it never invents a value; values/provenance stay on the deterministic
        LineItems. Falls back to per-line matching for anything the batch call can't resolve, or
        entirely when no LLM is configured.

        The batch is ONE STATEMENT — the caller groups by (statement, basis, period) and no longer
        by source page (``stages.map_ontology``), so a statement printed across two pages is decided
        whole. That is what the judgements above need: a subtotal and the lines it is made of, or a
        section and its residual, routinely straddle the page break.

        Large statements are CHUNKED at ``BATCH_MAX_ITEMS`` rather than sent as one unbounded call,
        each chunk with its own derived response budget (:meth:`_batch_max_tokens`). Chunks are
        contiguous slices of print order, so a chunk boundary is the only place cross-line context
        is lost, instead of every page boundary.

        ``items`` is a list of (item_id, source_label). ``sections`` maps an item_id to the
        section banner that item was printed under; a batch spans several sections, so the banner
        is per item, not per batch.

        The banner is given to the MODEL as well as being enforced after it answers. It used to
        be enforcement only: the model decided blind to the banner and a cross-section answer was
        then discarded, dropping the row to the weaker per-line path. Withholding the one piece of
        context the answer is graded on is how a caption that only its banner can disambiguate
        ("Others", the two "Non-controlling interests" of a comprehensive-income statement) got
        decided wrong and then thrown away.

        A refused answer inside a declared collision family (``CONCEPT_FAMILIES``) is now RE-ROUTED
        rather than discarded, because the banner already says which sibling was meant. Every other
        refusal — wrong statement, the concept's own exclusions, a caption naming a mutually
        exclusive class — still stands and is still counted as ``batch_refused``."""
        sec = sections or {}
        if not self.llm_enabled or not items:
            return {iid: self.match(label, statement=statement, section=sec.get(iid))
                    for iid, label in items}
        out: dict[str, MappingResult] = {}
        for start in range(0, len(items), self.BATCH_MAX_ITEMS):
            chunk = items[start:start + self.BATCH_MAX_ITEMS]
            out.update(self._match_chunk(chunk, statement, sec))
        return out

    def _match_chunk(self, items: list[tuple[str, str]], statement: str | None,
                     sec: dict[str, str | None]) -> dict[str, MappingResult]:
        """One provider call over at most ``BATCH_MAX_ITEMS`` captions. See :meth:`match_batch`."""
        # `binding.order` step 3, on the batch path: RESTRICT the offered concepts before the call.
        # Only concepts from THIS statement, and only from the sections this chunk was actually
        # printed under — plus the statement-level concepts, which belong to no section and must stay
        # reachable for a subtotal printed anywhere. Until now the whole statement was offered and a
        # wrong-section answer was refused AFTER the model gave it, which spends the call, drops the
        # row to the per-line path, and grades the model on a constraint it was never given a
        # candidate list under.
        tokens = {tok for iid, _ in items if (tok := section_of_banner(sec.get(iid)))}
        unresolved = any(section_of_banner(sec.get(iid)) is None for iid, _ in items)
        keys = [k for k in self._mappable_keys() if self._in_statement(k, statement)]
        if tokens and not unresolved:
            # One unresolvable banner and the restriction is off for the chunk: that row is
            # unconstrained by the gate (see `_in_section`), so narrowing the list would refuse it a
            # concept the gate would have allowed — a worse error than offering too much.
            keys = [k for k in keys
                    if not self._sections_of(k) or (self._sections_of(k) & tokens)]
        # Descending match_priority, for the reason the per-line shortlist is ordered that way: one
        # batch offers a whole statement, so the order the model reads the list in is the only
        # ranking it gets.
        candidates = self._concept_payload(self._by_priority(keys))
        # No candidate to choose from is not a question worth asking. `_llm` already guards this;
        # the batch path did not, so a statement the ontology covers no concepts for (changes in
        # equity against the shipped ontology) spent a real provider call on an empty candidate
        # list and then fell back per line anyway.
        if not candidates:
            return {iid: self.match(label, statement=statement, section=sec.get(iid))
                    for iid, label in items}
        caption_by_id = dict(items)
        # The section given to the model is the NORMALISED token, not the raw banner: the gate
        # downstream compares `section_of_key` against `section_of_banner`, so naming the raw text
        # would hand the model a vocabulary its answer is not judged in. A banner that normalises
        # to nothing — an umbrella ("EQUITY AND LIABILITIES"), a group heading ("Adjustments
        # for:"), anything unrecognised — is omitted rather than passed through, because the gate
        # lets those rows go anywhere and the prompt must not imply a constraint that is not real.
        def _sec_token(iid: str) -> str | None:
            return section_of_banner(sec.get(iid))

        payload: dict = {
            "instruction": "Map each source_item to exactly one candidate canonical_key by "
                           "meaning, applying the policies. Reference item_id and canonical_key; "
                           "do not output values. source_items are in the order they are printed "
                           "in the document. When an item carries a `section`, the concept you "
                           "choose must belong to that section.",
            "source_items": [
                {"item_id": iid, "caption": label,
                 **({"section": tok} if (tok := _sec_token(iid)) else {})}
                for iid, label in items
            ],
            "candidates": candidates,
        }
        # What each section's residual is expected to absorb (`expected_components`), so "none of
        # these" is a licensed answer for the captions the rulebook already knows have no concept.
        expectations = self._residual_expectations(statement, tokens or None)
        if expectations:
            payload["residual_expectations"] = expectations
        user = json.dumps(payload, ensure_ascii=False, indent=2)
        self.usage["batch_chunks"] += 1
        self.usage["batch_max_items"] = max(self.usage["batch_max_items"], len(items))
        try:
            decision, meta = self.llm_provider.complete_structured(
                system=self._batch_system,
                messages=[{"role": "user", "content": user}],
                response_schema=LlmBatchDecision,
                max_tokens=self._batch_max_tokens(len(items)),
            )
        except Exception as exc:  # noqa: BLE001
            # Record WHY, as `_llm` does. A truncated or refused batch used to fall back per line
            # in complete silence, so a run whose every batch failed still reported itself as
            # LLM-mapped with no error to point at.
            self.usage["failures"] += 1
            if not self.usage["last_error"]:
                self.usage["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            return {iid: self.match(label, statement=statement, section=sec.get(iid))
                    for iid, label in items}
        self.usage["calls"] += 1
        self.usage["input_tokens"] += int(meta.get("input_tokens") or 0)
        self.usage["output_tokens"] += int(meta.get("output_tokens") or 0)
        self.usage["model"] = meta.get("model", self.usage["model"])

        acc = self.settings.extraction.auto_accept_confidence
        out: dict[str, MappingResult] = {}
        for d in decision.mappings:
            # An item_id we did not ask about is not a decision about anything. Unchecked, it
            # reached the caller and crashed the stage (`by_id[iid]` → KeyError, killing the whole
            # extraction), and an id that happened to be another group's real row silently applied
            # this statement's decision to that one. It also defeated the gate: the caption lookup
            # returned "" for an unknown id, and both caption-dependent arms of `_allowed` are
            # skipped when the caption is empty.
            if d.item_id not in caption_by_id:
                self.usage["batch_unknown_ids"] += 1
                continue
            key = (d.canonical_key or "").strip()
            # Unknown, or a concept the payload deliberately withheld — a locked residual or a
            # computed one. See `_llm` for why naming it is not a licence to file the row there;
            # the per-line fallback at the bottom decides these rows instead.
            if not key or key not in self._by_key or key in self._unmatchable:
                continue
            # A concept from a different section than the row's banner is refused here for the
            # same reason it is refused in `match`. The model is now TOLD the section, so this is
            # a backstop rather than the only line of defence — and it still carries the two arms
            # that have nothing to do with sections: the concept's own exclusion criteria, and a
            # caption naming a mutually exclusive class.
            caption = caption_by_id[d.item_id]
            banner = sec.get(d.item_id)
            # The same refusal `match` makes, for the same reason: a caption that names a concept the
            # framework COMPUTES is not the model's to re-home, and the model was never offered that
            # concept to name. The model reports no score on the deterministic scale, so what the
            # claim is weighed against is the caption's own alias evidence for the concept it chose.
            norm_segments = [n for n in (normalize_label(seg)
                                         for seg in label_segments(caption)) if n]
            computed = self._refused_as_computed(
                norm_segments, self._alias_evidence(key, norm_segments))
            if computed is not None:
                out[d.item_id] = MappingResult(
                    None, MappingMethod.UNMATCHED, 0.0, [], True, {"llm": 0.0},
                    allocation_status="unmapped_review", computed_claim=computed)
                continue
            rerouted_from: str | None = None
            if not self._allowed(key, statement, banner, caption):
                # Before discarding it: when the answer is right about WHAT KIND of thing the row is
                # and wrong only about which section variant, the banner names the sibling and the
                # answer is corrected rather than lost. Discarding drops the row to the per-line
                # path, which sees one caption with no neighbours and is exactly why the batch call
                # exists — and for a bottom line whose caption arrives as a wrapped fragment there
                # is nothing left for that path to work from.
                target = self._family_route(key, statement, banner, caption)
                if target is None:
                    self.usage["batch_refused"] += 1
                    continue
                self._record_route(key, target)
                rerouted_from, key = key, target
            conf = max(0.0, min(1.0, d.confidence))
            alloc = (d.allocation_status or "").strip() or (
                "direct_exclusive" if self._by_key[key].value_scope == "exclusive_leaf" else None)
            out[d.item_id] = MappingResult(
                canonical_key=key, method=MappingMethod.LLM, confidence=conf,
                candidates=[Candidate(key, MappingMethod.LLM, conf, rerouted_from=rerouted_from)],
                needs_review=conf < acc, scores={"llm": conf},
                allocation_status=alloc, agreement=["llm"], rerouted_from=rerouted_from,
            )
        # Per-line fallback for any items the batch omitted.
        for iid, label in items:
            if iid not in out:
                out[iid] = self.match(label, statement=statement, section=sec.get(iid))
        return out
