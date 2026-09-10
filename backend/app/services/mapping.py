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

from typing import Literal

import json
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator
from rapidfuzz import fuzz, process

from app.config import Settings, get_settings
from app.core.models.enums import MappingMethod
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.han import has_han, to_simplified
from app.services import note_sourced
from app.services.note_context import subject_tokens as _context_tokens


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


class SourceRef(BaseModel):
    """WHERE THE MODEL TOOK A FIGURE FROM — a note and a row caption, as printed.

    A CITATION, NOT A PROVENANCE, and the difference is the whole point. The model is never given
    a page index or a bounding box, so a location it STATED would look authoritative and point
    wherever it guessed. What it gives instead is text this codebase can look up — the note number
    and the caption — and `resolve_sources` matches that back against the extracted rows to
    recover the real page and figure. A citation that resolves to nothing is reported as
    unresolved rather than believed.

    SEVERAL ARE EXPECTED. A figure is routinely stated across more than one printed row, and each
    row named here is resolved and reported separately so a reviewer sees all of them.
    """

    note: str = Field(default="", description="the note number as printed, e.g. \"7\" or \"七、9\"")
    caption: str = Field(default="", description="the row caption, quoted as the document prints it")
    quote: str = Field(default="", description="the sentence it came from, when the figure is "
                                               "stated in prose rather than in a table row")
    # THE FIGURE, and the ONLY place the model may state one.
    #
    # Everywhere else the contract forbids it: the model decides WHICH LINE a caption is, and
    # a figure it restated would be a number nobody printed. The exception is a figure stated
    # in PROSE — "^ Depreciation charges of approximately HK$529,841,000 … are included in
    # 'other operating expenses'" — which belongs to no extracted row, so there is nothing to
    # read it off. Without this the line stays empty however plainly the filing states it.
    #
    # IT IS VERIFIED, NOT TRUSTED. `resolve_sources` requires the amount to appear in the
    # cited note's own text before it is accepted, so the model is LOCATING a printed number
    # rather than supplying one. A figure that is not in the prose is refused.
    amount: str = Field(default="", description="the figure as printed, ONLY when it is stated "
                                               "in prose and belongs to no table row; it must "
                                               "appear verbatim in the note text you quote")


class LlmBatchItem(BaseModel):
    item_id: str
    canonical_key: str = Field(description="chosen key, or \"\" if none fits")
    # WHERE IT CAME FROM, required whenever the answer is not one of the offered candidates.
    #
    # The candidates are suggestions and the model may answer past them — deliberately, because
    # that is what lets a caption reach the concept its section did not predict. What makes it safe
    # is traceability: an off-candidate answer is worth exactly as much as the printed row behind
    # it, so the model names the row and the framework resolves the row itself.
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="required when canonical_key was NOT among the candidates: the note and row "
                    "caption(s) the figure is printed on. One entry per row; several are expected "
                    "when a figure is stated across more than one row.")
    confidence: float = Field(ge=0, le=1)
    allocation_status: str = ""
    reason: str = Field(default="", description="brief justification grounded in meaning/criteria")
    # IS THIS ROW THE WHOLE FIGURE, OR ONE PART OF IT?
    #
    # WHY THE MODEL HAS TO SAY. A line item's figure is often the SUM of several printed rows — a
    # note that splits depreciation by function prints four, and all four belong on the operating
    # expense line. But a face line repeating a note total is the SAME figure printed twice, and
    # adding those two overstates the line. Both cases look identical afterwards: a bigger number,
    # on a statement that still balances because the parent's own row was consumed. Nothing
    # downstream can tell them apart, which is why this is declared rather than inferred from "two
    # rows share a key".
    #
    # `whole` is the default and the conservative answer: two rows both claiming to be the whole
    # figure stay an `ambiguous_mapping`, which is the protection that already exists. `component`
    # is what licenses the sum, and it commits the model to a claim the reconciliation can then
    # check — a component set whose total does not tie to the printed subtotal shows up.
    role: Literal["whole", "component"] = Field(
        default="whole",
        description='"whole" if this row IS the figure for that concept; "component" if it is one '
                    'part of it and other rows supply the rest')
    # THE ARITHMETIC, for a component. A cascade spelled "the wider disclosure less the
    # cost-of-sales share" consumes one of its inputs negatively, and a contributions list that
    # shows it positive does not add up to the figure above it.
    sign: Literal[-1, 1] = Field(
        default=1,
        description="+1 if this component is added, -1 if it is subtracted; ignored for `whole`")


class LlmBatchDecision(BaseModel):
    """Per-statement decision over many captions at once, so cross-line judgements
    (parent/child containment, residualisation, 'Others') have full context."""

    mappings: list[LlmBatchItem] = Field(default_factory=list)

    @field_validator("mappings", mode="before")
    @classmethod
    def _unwrap_schema_envelope(cls, value):
        """Accept ``{"items": [...]}`` where the schema asks for ``[...]``.

        Structured output here is obtained model-agnostically: the response model's JSON Schema is
        embedded in the system prompt and the reply is validated with Pydantic. Some models echo
        the SCHEMA NODE for an array field — ``{"type": "array", "items": [...]}`` or just
        ``{"items": [...]}`` — instead of the array the node describes. The decisions are all
        present and correct; only the envelope is wrong.

        MEASURED: one of six mapping calls on the 四创电子 filing came back this way and raised

            ValidationError: mappings — Input should be a valid array [type=list_type]

        which discarded a whole chunk of 6 captions. The chunk then fell back per line, so the
        cost was silent — the run reported a successful LLM strategy while a sixth of its
        decisions had been thrown away on a wrapper.

        Narrow on purpose: only a mapping whose payload is a list under a schema-envelope key is
        unwrapped, and anything else is passed through untouched so the real validation error is
        still raised. This never invents an entry — an envelope with no list stays invalid.
        """
        if isinstance(value, dict):
            for envelope in ("items", "mappings"):
                inner = value.get(envelope)
                if isinstance(inner, list):
                    return inner
        return value


# THE SYSTEM PROMPT IS TWO THINGS, and they are separated because only one of them is safe to
# configure.
#
# _LLM_REPLY_CONTRACT is how the answer must be SHAPED and how it must cite what it decided. The
# reply parser, the per-item attribution and the audit trail all depend on it: an answer that
# names an item_id nobody asked about, omits one that was asked, or returns a figure instead of a
# key is not a worse answer — it is an unusable one, and the failure surfaces as "the model
# returned nothing usable" rather than as a configuration mistake. So it is NOT editable, and it
# is always sent first.
#
# The judgement half — map by meaning rather than by shared words, respect the exclusions, prefer
# the definition — is a deployment's opinion about how captions should be read, and lives in
# configuration as `LineItemSet.prompt`. DEFAULT_MAPPING_GUIDANCE below is the wording that ships;
# it is seeded into the configuration file rather than being a fallback here, so that editing it in
# one place is the whole story and an empty prompt means "no guidance beyond the contract" rather
# than "quietly restore whatever the code used to say".
_LLM_REPLY_CONTRACT = (
    "You are given a raw line-item caption from a financial statement (with any context) and "
    "candidate concepts, each with: canonical_key, a definition, inclusion criteria (include), "
    "exclusion criteria (exclude), concepts it is easily confused with, and its value_scope.\n"
    "HOW TO ANSWER — this part is fixed and must be followed exactly:\n"
    "- Choose at most ONE canonical_key per item.\n"
    "- THE CANDIDATES AND THEIR ALIASES ARE SUGGESTIONS, NOT A MENU. They are the concepts "
    "this row's statement and section make likely, and the aliases are examples of how each "
    "is usually printed. You are NOT required to use either. If the right concept for a "
    "caption is one that was not offered, answer with it.\n"
    "- WHENEVER YOUR ANSWER IS NOT ONE OF THE OFFERED CANDIDATES, you MUST say where in the "
    "document you took it from, in `sources`: the note number and the row caption(s), as "
    "printed. One entry PER ROW — several are expected where a figure is stated across more "
    "than one row, and each row you name is traced separately. An off-candidate answer with "
    "no `sources` is discarded, because a mapping nobody can trace to a printed row is not "
    "reviewable.\n"
    "- Quote a caption as the document prints it. It is matched back against the extracted "
    "rows to recover the page and the figure, so a paraphrase cannot be resolved, and a page "
    "or a figure you state from memory would look authoritative and point at the wrong "
    "place.\n"
    "- EVERY LINE YOU MAY ANSWER WITH IS IN THE CANDIDATES OR IN `identified_for`. Lines "
    "whose figures this framework works out for itself — from a declared calculation over "
    "their own sub-lines — are deliberately not offered, and naming one is refused however "
    "well you cite it. Where a note prints such a breakdown, answer with the SUB-LINE it "
    "prints: each `identified_notes` entry names the sub-lines it was identified for, in "
    "`identified_for`, and those are the keys to use.\n"
    "- Cite the item by the `item_id` you were given, and the concept by its exact "
    "`canonical_key`. Never return an item_id that was not given to you.\n"
    "- Do not output values, figures or amounts — you are deciding which line a caption is, "
    "not what it is worth. THE ONE EXCEPTION is a figure stated in PROSE: where a note's "
    "narrative states an amount that appears in no table row ('Depreciation charges of "
    "approximately HK$529,841,000 are included in other operating expenses'), give that "
    "amount in the matching `sources` entry's `amount`, with the sentence in `quote`. It is "
    "checked against the note's own text and refused if it is not there, so give it exactly "
    "as printed and never round, convert or infer one.\n"
    "- If no candidate genuinely fits, return an empty canonical_key. That is a valid answer.\n"
    "- Return calibrated confidence in [0,1] (high only when unambiguous), and when it is clear, "
    "an allocation_status describing how the value relates to parents/children.\n"
    "- SEVERAL ROWS MAY BELONG ON ONE CONCEPT, and you must say which case you are in. Set "
    "`role` to \"component\" when this row is one PART of that concept's figure and other rows "
    "supply the rest — a note that splits a total by function prints several rows and all of them "
    "belong on the one line. Set `role` to \"whole\" (the default) when this row IS the figure. "
    "Do NOT mark a row as a component when it repeats an amount another row already accounts for: "
    "a face line and the note total behind it are the same figure printed twice, and adding them "
    "overstates the line. If you are unsure, answer \"whole\" — a duplicate declared whole is "
    "caught, a duplicate declared component is added silently.\n"
    "- For a component, set `sign` to -1 when it is SUBTRACTED and +1 when it is added, and give "
    "`reason` for that row specifically — which criterion or wording makes it part of this "
    "concept. Each component's own page, note and caption are already recorded, so `reason` is "
    "the one part of the trace only you can supply."
)

# The shipped judgement wording, seeded into `LineItemSet.prompt`. Kept here as the source of that
# seed — and referenced by the test that holds the two in step — not as a runtime fallback.
DEFAULT_MAPPING_GUIDANCE = (
    "Map each caption to the concept whose definition and criteria best match what the caption "
    "REPRESENTS. Rely on financial meaning, not string similarity or shared words. Respect the "
    "exclusion criteria and the confusable-with warnings."
)

# Appended for the BATCH path only. The base instruction opens "You map a single raw line-item
# caption", which is false when several are decided at once, and it never says what a section is —
# so a model told an item's section had no way to know the word was binding. Kept separate from
# the contract so correcting the batch framing cannot silently rewrite the reply rules.
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


# ══ CAPTION NORMALISATION: THE SHAPE IS CODE, THE CHARACTER INVENTORY IS VOCABULARY ═══════════
#
# Each pattern in the block below is two things welded together, and only one of them belongs in
# Python.
#
# THE SHAPE is mechanism: "a bracket whose content is QUOTED", "an enumerator followed by a
# delimiter and NOT by a digit", "a closing bracket with nothing on the line that opened it". The
# shape is what makes `Profit/(loss) before tax` survive `_ABBREV_GLOSS`, `七、70` survive
# `_CAS_LINE_PREFIX` and `b) Trade receivables` survive `_CAS_ORPHAN_HEAD` — each of those a
# refusal, and a configurable refusal is not one. So the shapes stay here, as templates.
#
# THE INVENTORY is vocabulary: which bracket widths a filing prints, which marks it quotes with,
# which characters it numbers its face lines with, which word it writes before a note number. Each
# entry is a claim about the PRINTED page, it is wrong the moment a filing prints something the
# list does not carry, and it lived as a string literal inside a regex — so a reviewer could read
# all 475 line-item definitions and never discover that ＂ is not a quote mark as far as this
# module is concerned.
#
# MEASURED, which is why the inventory is the half worth moving. With `_ABBREV_GLOSS` disabled,
# 1,050 of the rulebook's 1,993 caption resolutions change and 74 land on a DIFFERENT concept
# (`scripts/parity_normalisation.py`'s negative control; the fold itself moves on 1,993 of 24,029
# corpus entries). `demo_code_vs_config.py` section 2 runs the live consequence:
# `Land use rights ("LUR")` reaches `bs_nca__land_use_rights` at the exact tier, confidence 1.0,
# while the same caption printed with a full-width quote (＂) or a CJK vertical corner bracket
# (﹁﹂) — marks this inventory does not carry — folds to 'land use rights lur' and lands on
# `bs_nca__land` through the rule tier. A different asset, both non-current, so the sheet still
# ties. RE-MEASURED, because the received version of this story says "unflagged" and that is not
# what either matcher does: it comes back at 0.6 with "several rule hints fired; ambiguous" and
# `needs_review=True`, under the 0.85 auto-accept. So the cost is a review-queue entry on a
# caption whose alias the rulebook already carries — and what caught it was the ambiguity check
# counting hints, not anything able to notice that a quote mark was missed. Until now there was
# nowhere to say the mark counts; `tests/test_normalisation_vocabulary.py` now says it in three
# list entries and watches the caption arrive at the right concept, EXACT and unflagged.
#
# THE TEMPLATES INTERPOLATE WITH `%(name)s`, not with `str.format` or an f-string, because every
# pattern here carries a `{1,3}`-shaped quantifier that both would read as a replacement field —
# and not with `string.Template`, because `$` is an anchor two of them use. No pattern contains a
# literal `%`, so `%`-formatting has nothing to escape.
_BUILTIN_CAPTION_INVENTORY: dict[str, list] = {
    # BRACKETS AS PAIRS, not as an opener list and a closer list, because a bracket width IS a
    # pair: two independent lists let a set declare three openers and two closers, and every
    # pattern here that opens a bracket also closes one.
    "brackets": [["(", ")"], ["（", "）"]],
    # The marks a filing quotes a coined abbreviation with — straight, curly, and the CJK corner
    # and lenticular brackets a Chinese filing uses. `_ABBREV_GLOSS` explains why the QUOTES and
    # not the parenthesis are the signal, and this list is exactly the measured blast radius above.
    "quote_marks": ['"', "'", "“", "”", "‘", "’", "「", "」", "『", "』", "《", "》"],
    # The word printed before a note number. Stored SINGULAR: the template accepts the plural,
    # because English morphology is mechanism and a set made to declare "note" and "notes"
    # separately will eventually declare only one of them.
    "note_word_latin": ["note"],
    # BOTH Han spellings are needed even though `to_simplified` runs inside `normalize_label`:
    # `_NOTE_CITATION` fires BEFORE the fold (a citation has to be gone before the Han run is
    # folded), so 附註 arriving from a Hong Kong filing never reaches the Simplified 附注.
    "note_word_han": ["附註", "附注"],
    # Colons, for a leading note citation and for the CAS component markers.
    "colon_marks": [":", "："],
    # Digits, for `_CAS_LINE_PREFIX`'s refusal lookahead — the one that keeps 七、70 unmatchable.
    # Full-width included because a mainland filing prints note references in full-width digits.
    "digit_ranges": [["0030", "0039"], ["FF10", "FF19"]],
    # THE HAN RANGES, AS CODEPOINTS AND NOT AS CHARACTERS, because one of them is not the range its
    # author typed. The literal this replaced spelled the third range `豈-﫿`, which reads as
    # U+F900 (CJK COMPATIBILITY IDEOGRAPH-F900) and IS U+8C48 — U+F900's canonical decomposition,
    # i.e. what an NFC pass over the source file leaves behind. `han._CJK` carries the same three
    # ranges with F900 intact, so the two are different sets: measured, `_HAN_RUN` matches 한
    # (U+D55C) and U+E000 while `has_han` does not, because 8C48-FAFF swallows the Hangul syllables
    # and the private-use area whole. Not reachable on today's captions — `label_segments` gates on
    # `has_han` first, so a Hangul-only caption never arrives — but a caption mixing Hangul with
    # Han loses its split: `label_segments('매출 销售成本')` returns one segment where the corrected
    # range returns three. PRESERVED EXACTLY AS SHIPPED, deliberately: correcting it is a
    # four-character edit that `scripts/parity_normalisation.py` would report as a fold change, and
    # that is a decision for whoever measures it, not a side effect of moving the list. Written as
    # codepoints so the question is at least askable in review.
        # F900, NOT 8C48. The literal this replaced spelled the third range's low bound `豈`, which is
    # U+8C48 — the canonical DECOMPOSITION of U+F900, i.e. exactly what an NFC pass over this
    # source file leaves behind. So an editor or tool silently rewrote a code point and the range
    # became 8C48-FAFF: it starts in the middle of the main CJK block it already covers and runs
    # through HANGUL (AC00-D7AF) and the PRIVATE USE AREA (E000-F8FF) to get to FAFF. Measured
    # before the fix: `_HAN_RUN` matched 한 (U+D55C) and U+E000 as Han.
    #
    # `han._CJK` carries U+F900 intact and is the reference — `han.has_han('한')` was already
    # False while `_HAN_RUN` said True, so the two modules disagreed about what Chinese is. The
    # range meant is CJK Compatibility Ideographs, F900-FAFF.
    "han_ranges": [["3400", "4DBF"], ["4E00", "9FFF"], ["F900", "FAFF"]],
    # `_CAS_ORPHAN_HEAD` requires PROOF that the fragment is Han, and asks for two of the three
    # ranges above. Declared separately because the shipped literals were separate: collapsing them
    # into one entry would widen a REFUSAL as a side effect of a migration, which is the one
    # direction never to move by accident.
    "han_ranges_required": [["3400", "4DBF"], ["4E00", "9FFF"]],
    # The CAS face enumerators — 一、营业总收入 through 十、… — reused by the （一）sub-enumerator.
    "cas_enumerators": ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"],
    # What may follow a Han enumerator. The full stop is here because a filing prints 一.营业总收入
    # as well as 一、营业总收入.
    "cas_enumerator_delimiters": ["、", "."],
    # What may follow an ARABIC one, which the shipped pattern deliberately keeps NARROWER: "1." is
    # a numbered paragraph in prose far more often than it is a face line, so only the ideographic
    # comma counts. Its own entry because that asymmetry is a judgement about printed pages, and
    # folding the two entries together would widen the strip without anyone saying so.
    "cas_arabic_delimiters": ["、"],
    # The component markers a CAS face prints instead of an enumerator, on a line beneath a total.
    # A marker longer than one character is matched with `\s*` between its characters — see
    # `_spaced`, and 递延所得税资产减少（增加以“－” 号填列）for a real filing breaking a line inside one.
    "cas_component_markers": ["其中", "加", "减"],
    # The sign-convention parenthetical's own wording, split where the filing breaks the line.
    "cas_sign_note_words": ["号", "填列"],
}

# Characters a regex character class cannot carry literally. REFUSED rather than escaped: escaping
# would make the built source differ from the literal it has to reproduce character for character,
# and none of these is punctuation a financial statement prints that a caption pattern hunts for.
_CLASS_UNSAFE = frozenset("]\\^-")


def _class_body(chars: Iterable[str], *, what: str) -> str:
    """The inside of a `[...]`, members in DECLARED order.

    Order is preserved rather than sorted because a character class is a SET — the engine cannot
    tell two orderings apart — but the order is the only thing that makes the built source
    comparable, character for character, against the literal it replaced. That comparison is the
    whole proof this migration is not a rewrite (`tests/test_normalisation_vocabulary.py`).
    """
    out: list[str] = []
    for ch in chars:
        if not isinstance(ch, str) or len(ch) != 1:
            raise ValueError(f"caption inventory {what}: {ch!r} is not a single character, so it "
                             f"cannot be a member of a character class")
        if ch in _CLASS_UNSAFE:
            raise ValueError(f"caption inventory {what}: {ch!r} cannot appear unescaped in a "
                             f"character class")
        out.append(ch)
    return "".join(out)


def _range_body(pairs: Iterable, *, what: str) -> str:
    """`lo-hi` runs for a `[...]`, from HEX CODEPOINT pairs — see `han_ranges` for why not chars."""
    out: list[str] = []
    for pair in pairs:
        try:
            lo, hi = (int(str(x), 16) for x in pair)
        except (TypeError, ValueError):
            raise ValueError(f"caption inventory {what}: {pair!r} is not a [low, high] pair of "
                             f"hex codepoints") from None
        if lo > hi:
            raise ValueError(f"caption inventory {what}: {pair!r} runs backwards")
        out.append(f"{chr(lo)}-{chr(hi)}")
    return "".join(out)


def _spaced(parts: Iterable[str]) -> str:
    r"""The parts joined by `\s*`, because the filing breaks the line between them.

    Called two ways on purpose. Over a STRING it separates that marker's characters (其中 ->
    `其\s*中`); over a LIST it separates the words of a phrase (号, 填列 -> `号\s*填列`). Both are
    the same fact — 递延所得税资产减少（增加以“－” 号填列）is a real caption and the whitespace inside
    it is whatever row reconstruction left behind — at two granularities, so one helper says it
    once.
    """
    return r"\s*".join(parts)


def _marker_branches(markers: Iterable[str], colon: str) -> str:
    r"""The component-marker alternatives: one branch per multi-character marker, then ONE class
    holding all the single-character ones.

    Two shapes because the literal this replaced had two — `其\s*中\s*[：:]|[加减]\s*[：:]` — and
    the reason is `_spaced` above: 加 and 减 have nothing to break a line inside, so a class is
    both shorter and exactly equivalent, while 其中 must tolerate the break.
    """
    markers = list(markers)
    tail = rf"\s*[{colon}]"
    branches = [_spaced(m) + tail for m in markers if len(m) > 1]
    if (single := [m for m in markers if len(m) == 1]):
        branches.append(f"[{_class_body(single, what='cas_component_markers')}]{tail}")
    return "|".join(branches)


def _placeholders(inv: dict[str, list]) -> dict[str, str]:
    """The inventory rendered as the fragments the templates interpolate.

    TWO SPELLINGS PER BRACKET AND COLON SET (`open`/`open_rev`) rather than one, because the
    literals this replaced spell the same set in two orders: the four Latin-lineage patterns write
    `[(（]` and `[:：]`, the three CAS ones write `[（(]` and `[：:]`. The sets are identical and no
    regex engine can tell them apart, so the difference is cosmetic — but it is cosmetic in the
    SOURCE, and the source is what the equivalence test compares. One inventory rendered two ways
    keeps both spellings byte-exact without becoming two answers to one question.
    """
    brackets = [list(p) for p in inv["brackets"]]
    if any(len(p) != 2 for p in brackets):
        raise ValueError(f"caption inventory brackets: {inv['brackets']!r} — each entry is an "
                         f"[open, close] pair, because every pattern that opens one closes one")
    opens = [o for o, _ in brackets]
    closes = [c for _, c in brackets]
    colons = list(inv["colon_marks"])
    note_latin = "|".join(f"{w}s?" for w in inv["note_word_latin"])
    note_han = "|".join(inv["note_word_han"])
    arabic = list(inv["cas_arabic_delimiters"])
    return {
        "open": _class_body(opens, what="brackets"),
        "open_rev": _class_body(reversed(opens), what="brackets"),
        "close": _class_body(closes, what="brackets"),
        "close_rev": _class_body(reversed(closes), what="brackets"),
        "quote": _class_body(inv["quote_marks"], what="quote_marks"),
        "note_any": f"{note_latin}|{note_han}",
        "note_han": note_han,
        "note_latin": note_latin,
        "colon": _class_body(colons, what="colon_marks"),
        "colon_rev": _class_body(reversed(colons), what="colon_marks"),
        "digit": _range_body(inv["digit_ranges"], what="digit_ranges"),
        "han": _range_body(inv["han_ranges"], what="han_ranges"),
        "han_req": _range_body(inv["han_ranges_required"], what="han_ranges_required"),
        "enum": _class_body(inv["cas_enumerators"], what="cas_enumerators"),
        "enum_delim": _class_body(inv["cas_enumerator_delimiters"],
                                  what="cas_enumerator_delimiters"),
        # A single delimiter is written bare, as the literal did, and `re.escape` covers the case
        # where someone declares "." — outside a class it would otherwise match any character.
        "arabic_delim": (re.escape(arabic[0]) if len(arabic) == 1
                         else f"[{_class_body(arabic, what='cas_arabic_delimiters')}]"),
        "markers": _marker_branches(inv["cas_component_markers"],
                                    _class_body(reversed(colons), what="colon_marks")),
        "sign_words": _spaced(inv["cas_sign_note_words"]),
    }


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
_ABBREV_GLOSS_TEMPLATE = (
    r"""[%(open)s]\s*                     # an opening bracket, either width
        [%(quote)s]\s*        # …whose content opens with a quotation mark
        [^%(close)s]*?                      # the abbreviation itself, never crossing the bracket
        \s*[%(quote)s]\s*     # …and closes with one
        [%(close)s]""")

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
# EVERY FORM IS DELIMITED, and the delimiter is what makes this safe. The first version of this
# pattern made both brackets optional and was unanchored, so it matched a bare "notes <digits>"
# ANYWHERE — and because the digit run is capped at a note number's length, a four-digit year was
# eaten only PARTLY: "Senior notes 2025" normalised to "senior 5", deleting the head noun the
# shipped rulebook carries four aliases for and fabricating a token that appears nowhere in the
# caption. The row then matched nothing and was swept into its section's residual, where every
# subtotal still tied and nothing reported it. Hence brackets REQUIRED for the Latin form, the bare
# CJK marker allowed because the rulebook names it unbracketed, and a leading citation allowed only
# with the colon that delimits it. ``(?!\d)`` is the other half of the repair: a digit run longer
# than a note number is not a note number, and half of one is not either.
#
# A DANGLING CITATION counts as one. The last alternative matches an opening bracket and the word
# with NOTHING after it — "Deferred tax credited for the year (note" — which is what a caption
# truncated mid-citation looks like, and it is exactly what a real filing produced: the row printed
# "Deferred tax credited for the year (note 32) 年內計入遞延稅項（附註32）" and reached the matcher
# with everything after "(note" lost. The truncation itself is a row-reconstruction defect and
# belongs to that module; recognising the stump as the pointer it is costs nothing and is right
# regardless, because a caption never ENDS on the word "note" as part of a concept's name.
_NOTE_CITATION_TEMPLATE = (
    # (note 12), （附註12）, (note 16(b)) — bracketed, the form the rulebook names.
    r"[%(open)s]\s*(?:%(note_any)s)\s*\.?\s*\d{1,3}(?!\d)[a-z]?"
    r"(?:\s*[%(open)s][a-z0-9]{1,3}[%(close)s])?\s*[%(close)s]"
    # 附註12 — the bare CJK marker, which the rulebook names unbracketed.
    r"|(?:%(note_han)s)\s*\d{1,3}(?!\d)"
    # "Note 15: Trade receivables" — a citation LEADING a caption, delimited by its colon.
    r"|^\s*%(note_latin)s\s*\.?\s*\d{1,3}(?!\d)[a-z]?\s*[%(colon)s]"
    # "... (note" — a citation truncated mid-word by row reconstruction.
    r"|[%(open)s]\s*(?:%(note_any)s)\s*$")

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
_BRACKETED_NUMBER_TEMPLATE = r"[%(open)s]\s*\d{1,4}\s*[%(close)s]"

# A TRAILING NUMERIC NOTE MARKER with no "note" word, common in HKEX captions:
# "Right-of-use assets 16(a)", "Lease liabilities 22(b)". It is a pointer to a
# note table, not part of the concept name.
_TRAILING_NUMERIC_NOTE_TEMPLATE = r"\b\d{1,3}\s*[%(open)s][a-z0-9]{1,3}[%(close)s]\s*$"

# THE CAS FACE FORMAT NUMBERS ITS OWN LINES, and the number is not part of the concept's name:
# 一、营业总收入, 二、营业总成本, 三、营业利润 … and a component beneath one of those is marked
# 其中：/加：/减： instead. `row_reconstruct._CAS_STATEMENT_LINE` reads that same enumeration as
# structural evidence that a line is a statement-level total, which is what it is for there. Here
# it is noise: it stands between the printed caption and the alias that names it.
#
# MEASURED, because the size of this was not obvious: against the 57 face captions CAS prescribes,
# the rulebook resolved 45 when they were fed bare and 0 — not fewer, ZERO — when each was fed
# with the prefix a mainland filing actually prints. Punctuation stripping below does not rescue
# it: it turns 一、营业总收入 into "一 营业总收入", which is not "营业总收入".
#
# SAFE BECAUSE IT IS SYMMETRIC. `normalize_label` is applied to every alias as well as every
# caption (see `OntologyMatcher.__init__`), so an alias that itself carries a marker — the
# rulebook ships 减：预期信用损失准备 — is stripped identically and still matches. The only way
# this can lose a mapping is by COLLISION, two distinct aliases on different concepts folding
# to one string, which is counted in the ambiguity check rather than assumed absent.
#
# THE DIGIT LOOKAHEAD IS LOAD-BEARING, for the reason `_CAS_STATEMENT_LINE` documents: the same
# pages print NOTE REFERENCES in the very same shape — 七、61, 七、70 — and one of those arrives
# as a row's entire label when the caption beside it is lost. Stripping there would leave a bare
# "70" to be matched against the rulebook, turning an unmatchable label into a plausibly
# matchable one. A note reference must stay unmatchable.
_CAS_LINE_PREFIX_TEMPLATE = (
    r"^\s*(?:"
    r"[%(enum)s]+\s*[%(enum_delim)s]\s*(?![%(digit)s])"   # 一、营业总收入 — but never 七、70
    r"|\d{1,2}\s*%(arabic_delim)s\s*(?![%(digit)s])"      # 1、营业收入, the Arabic-numeral variant
    r"|[%(open_rev)s]\s*[%(enum)s]{1,3}\s*[%(close_rev)s]"  # （一）应收账款 sub-enumerator
    r"|%(markers)s"                                       # 其中：营业收入, 加：…, 减：库存股
    r")\s*"
)

# THE CAS SIGN-CONVENTION PARENTHETICAL, which a mainland face caption carries about ITSELF:
# 三、营业利润（亏损以“－”号填列）, 四、利润总额（亏损总额以“－”号填列）, 资产减值损失（损失以“-”号填列）,
# 存货的减少（增加以“－”号填列）. It tells the reader that a loss is printed with a minus sign. It is
# an instruction about presentation and no more part of the concept's name than a note citation is
# — and unlike a note citation it survived, because it is neither bracketed-numeric nor a quoted
# abbreviation gloss, so `_BRACKETED_NUMBER` and `_ABBREV_GLOSS` both pass over it.
#
# MEASURED, and the size of it is why this is here: on 四创电子 (11077098) TWENTY-THREE captions
# carry it and it defeated the match on every one. The whole bottom of the income statement was
# unreachable — 三、营业利润 -253,234,645.30 landed on Other Operating Expenses, while
# 四、利润总额 -252,374,537.99 and 五、净利润 -245,867,912.27 reached no concept at all — because
# the punctuation fold turns the parenthetical into word tokens ("营业利润 亏损以 号填列") that no
# alias in any rulebook carries. 营业利润 / 利润总额 / 净利润 / 资产减值损失 / 公允价值变动收益 are
# all aliased; none of them could fire.
#
# Bounded content and the two bracket widths, so it cannot run across a caption. `号\s*填列`
# because the filing breaks the line inside it: 递延所得税资产减少（增加以“－” 号填列）.
_CAS_SIGN_NOTE_TEMPLATE = (
    r"[%(open_rev)s][^%(open_rev)s%(close)s]{0,24}%(sign_words)s\s*[%(close_rev)s]")

# THE ORPHANED TAIL OF THE CAPTION ABOVE, left on the front of this one by a wrap merge:
# "填列） 三、营业利润（亏损以“－”号填列）" is 资产处置收益's closing fragment glued to 营业利润's head.
# Stripping the sign note above leaves "填列） 三、营业利润", so the fragment has to go too or the
# caption still names nothing.
#
# RECOGNISED BY BEING IMPOSSIBLE, not by being short: a closing bracket with nothing on the line
# that opened it cannot be the start of a caption any filing prints. `[^（(]` cannot cross an
# opening bracket, so a legitimate leading parenthetical — （一）综合收益总额, "Profit/(loss) before
# tax", "(Loss)/profit" — is never matched. A Han character is required as well, which keeps this
# off the English path entirely: "b) Trade receivables" is left alone.
_CAS_ORPHAN_HEAD_TEMPLATE = (
    r"^[^%(open_rev)s]*?[%(han_req)s][^%(open_rev)s]{0,10}[%(close_rev)s]\s*")

# A bilingual filing prints one caption in both scripts: "REVENUE 收益",
# "Cost of sales 銷售成本". Matching the concatenation dilutes every score (half the string is
# always "wrong" for a single-language alias), so each script's run is also matched on its own
# and the best segment wins.
_HAN_RUN_TEMPLATE = r"[%(han)s]+(?:\s*[%(han)s]+)*"


@dataclass(frozen=True)
class CaptionPatterns:
    """The eight compiled caption patterns, in the order `normalize_label` applies them.

    ONE OBJECT rather than eight arguments, so a caller cannot hand the fold half a vocabulary:
    seven of these strip and `han_run` splits, and a caption stripped by a declared inventory but
    split by the built-in one is a state no filing corresponds to. It is also what makes the
    equivalence test a single comparison — build from the built-in inventory, compare all eight
    sources to the literals they replaced.
    """

    abbrev_gloss: re.Pattern[str]
    note_citation: re.Pattern[str]
    trailing_numeric_note: re.Pattern[str]
    bracketed_number: re.Pattern[str]
    cas_sign_note: re.Pattern[str]
    cas_orphan_head: re.Pattern[str]
    cas_line_prefix: re.Pattern[str]
    han_run: re.Pattern[str]


def build_caption_patterns(declared: Mapping[str, list] | None = None) -> CaptionPatterns:
    """Compile the caption patterns from a declared character inventory.

    EMPTY MEANS THE BUILT-IN, entry by entry, exactly as `line_item_matching.Vocabulary` already
    does for the nine migrated tables: a set declaring nothing must fold captions precisely as it
    did before any of this was configurable, or adding the field would silently change the answers
    of every rulebook already written. `tests/test_normalisation_vocabulary.py` holds the eight
    literals this replaced and proves the built-in path rebuilds each one CHARACTER FOR CHARACTER,
    which is the only claim strong enough here — `scripts/parity_normalisation.py` pins 24,029
    captions and says itself that its corpus is a floor and not a certificate.

    AN ENTRY NO TEMPLATE READS IS REFUSED, not ignored. A declared inventory that looks like it
    carries a mark and does not is worse than one that carries nothing, because it reads as a
    control — the same objection this whole migration answers, arriving from the other side.
    """
    inv = {k: list(v) for k, v in _BUILTIN_CAPTION_INVENTORY.items()}
    for key, value in (declared or {}).items():
        if key not in inv:
            raise ValueError(f"caption inventory: {key!r} is not read by any caption pattern — "
                             f"known entries are {', '.join(sorted(inv))}")
        if value:            # empty declares nothing, not "nothing is allowed" — see the docstring
            inv[key] = list(value)
    ph = _placeholders(inv)
    return CaptionPatterns(
        abbrev_gloss=re.compile(_ABBREV_GLOSS_TEMPLATE % ph, re.VERBOSE),
        note_citation=re.compile(_NOTE_CITATION_TEMPLATE % ph, re.IGNORECASE),
        trailing_numeric_note=re.compile(_TRAILING_NUMERIC_NOTE_TEMPLATE % ph, re.IGNORECASE),
        bracketed_number=re.compile(_BRACKETED_NUMBER_TEMPLATE % ph),
        cas_sign_note=re.compile(_CAS_SIGN_NOTE_TEMPLATE % ph),
        cas_orphan_head=re.compile(_CAS_ORPHAN_HEAD_TEMPLATE % ph),
        cas_line_prefix=re.compile(_CAS_LINE_PREFIX_TEMPLATE % ph),
        han_run=re.compile(_HAN_RUN_TEMPLATE % ph),
    )


# The eight field names, derived from the dataclass rather than retyped — a ninth pattern added
# to `CaptionPatterns` is then compared by `install_caption_inventory` automatically instead of
# being silently excluded from the changed-check.
_CAPTION_PATTERN_FIELDS: tuple[str, ...] = tuple(CaptionPatterns.__dataclass_fields__)

_CAPTION_PATTERNS = build_caption_patterns()

# The individual names kept as aliases, because two consumers reach for them by name rather than
# through the fold: `tests/test_pattern_overlap.py` builds a `CaptionTransform` out of each CAS
# pattern's `.pattern`, and `scripts/demo_code_vs_config.py` prints two of them to argue that the
# disjointness is designed in. Aliases and not copies — same compiled objects, so there is still
# exactly one answer to "what does this module strip".
_ABBREV_GLOSS = _CAPTION_PATTERNS.abbrev_gloss
_NOTE_CITATION = _CAPTION_PATTERNS.note_citation
_TRAILING_NUMERIC_NOTE = _CAPTION_PATTERNS.trailing_numeric_note
_BRACKETED_NUMBER = _CAPTION_PATTERNS.bracketed_number
_CAS_SIGN_NOTE = _CAPTION_PATTERNS.cas_sign_note
_CAS_ORPHAN_HEAD = _CAPTION_PATTERNS.cas_orphan_head
_CAS_LINE_PREFIX = _CAPTION_PATTERNS.cas_line_prefix
_HAN_RUN = _CAPTION_PATTERNS.han_run

# What was installed, so a second install can tell "same inventory again" from "a different one".
_INSTALLED_INVENTORY: dict[str, list] | None = None


def _canonical(inventory: Mapping[str, list] | None) -> dict[str, list]:
    """One comparable shape, so JSON's lists and the built-in's tuples compare equal."""
    return {k: [list(v) if isinstance(v, (list, tuple)) else v for v in vals]
            for k, vals in (inventory or {}).items()}


def install_caption_inventory(declared: Mapping[str, list] | None,
                              *, source: str = "") -> bool:
    """Make a DECLARED character inventory the one this module folds captions with.

    Returns True if the fold changed. Safe to call with the built-in — that is a no-op.

    WHY THIS IS PROCESS-WIDE STATE, which normally deserves suspicion. The fold has to be
    SYMMETRIC: `normalize_label` is applied to every alias when the index is built and to every
    caption when one is matched, and an alias folded one way can never meet a caption folded
    another. Two inventories live in one process means the index and the lookup disagree and the
    matcher quietly stops finding things. So there is exactly one, and installing it is explicit.

    A CONFLICTING RE-INSTALL IS REFUSED for that reason. Two rulebooks with different inventories
    in one process cannot both be right, and silently taking the last one would make the answer
    depend on load order. The first install wins and the second raises.
    """
    global _CAPTION_PATTERNS, _INSTALLED_INVENTORY
    global _ABBREV_GLOSS, _NOTE_CITATION, _TRAILING_NUMERIC_NOTE, _BRACKETED_NUMBER
    global _CAS_SIGN_NOTE, _CAS_ORPHAN_HEAD, _CAS_LINE_PREFIX, _HAN_RUN

    wanted = _canonical(declared)
    if _INSTALLED_INVENTORY is not None and _canonical(_INSTALLED_INVENTORY) != wanted:
        raise ValueError(
            f"a caption inventory is already installed and this one differs"
            f"{f' (from {source})' if source else ''}. The fold must be symmetric across the "
            f"alias index and the captions matched against it, so one process holds exactly one. "
            f"Differing keys: "
            f"{sorted(k for k in set(wanted) | set(_canonical(_INSTALLED_INVENTORY)) if _canonical(_INSTALLED_INVENTORY).get(k) != wanted.get(k))}")

    rebuilt = build_caption_patterns(declared)
    changed = any(getattr(rebuilt, f).pattern != getattr(_CAPTION_PATTERNS, f).pattern
                  or getattr(rebuilt, f).flags != getattr(_CAPTION_PATTERNS, f).flags
                  for f in _CAPTION_PATTERN_FIELDS)
    _CAPTION_PATTERNS = rebuilt
    _INSTALLED_INVENTORY = dict(wanted)
    _ABBREV_GLOSS = rebuilt.abbrev_gloss
    _NOTE_CITATION = rebuilt.note_citation
    _TRAILING_NUMERIC_NOTE = rebuilt.trailing_numeric_note
    _BRACKETED_NUMBER = rebuilt.bracketed_number
    _CAS_SIGN_NOTE = rebuilt.cas_sign_note
    _CAS_ORPHAN_HEAD = rebuilt.cas_orphan_head
    _CAS_LINE_PREFIX = rebuilt.cas_line_prefix
    _HAN_RUN = rebuilt.han_run
    return changed


def normalize_label(text: str, patterns: CaptionPatterns | None = None) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace (locale-agnostic).

    Han text is folded to Simplified so a Traditional caption from a Hong Kong or Taiwan
    filing compares equal to a Simplified alias (and vice versa) — the same concept printed
    in the other script would otherwise never match.

    A quoted abbreviation gloss is dropped first — see ``_ABBREV_GLOSS`` for why the punctuation
    stripping below does not already do it. A mainland statement's own line numbering and its
    其中：/加：/减： component markers are dropped too (``_CAS_LINE_PREFIX``), as is the
    sign-convention parenthetical it carries about itself (``_CAS_SIGN_NOTE``) and a wrapped
    caption's orphaned tail left on the front of the next one (``_CAS_ORPHAN_HEAD``).

    ``patterns`` is how a DECLARED character inventory reaches this fold: pass the result of
    :func:`build_caption_patterns` and the sequence, the substitutions and the loop bound below
    are unchanged while the characters they hunt for come from the declaration. Omitting it uses
    the built-in inventory, so every existing caller — and there are forty-odd across the stages,
    the services and the scripts — folds exactly as before. Parameterised rather than swapped in
    at module level on purpose: two rulebooks live in one process (the incumbent matcher reads the
    ontology, ``line_item_matching`` reads the line-item set) and a global inventory would make
    the second one silently re-fold the first one's aliases.
    """
    p = patterns or _CAPTION_PATTERNS
    text = p.abbrev_gloss.sub(" ", text)
    text = p.note_citation.sub(" ", text)
    text = p.trailing_numeric_note.sub(" ", text)
    text = p.bracketed_number.sub(" ", text)
    text = to_simplified(text)
    # ORDER MATTERS HERE. The sign note goes first because a caption can carry more than one of
    # them — 公允价值变动收益（损失以“－”号填列） 信用减值损失（损失以“-”号填列） is two glued captions
    # with two — and removing them is what exposes the orphaned tail underneath. The tail goes
    # next, because it sits in FRONT of the enumerator the prefix rule is looking for
    # ("填列） 三、营业利润" only becomes "三、营业利润" once the fragment is gone).
    text = p.cas_sign_note.sub(" ", text)
    text = p.cas_orphan_head.sub("", text, count=1)
    # Folded to Simplified first so 減：/其中： in a Traditional filing reach the same rule, and
    # looped because a continuation line can carry both an enumerator and a marker. Bounded, so a
    # pathological caption of nothing but markers cannot spin.
    for _ in range(3):
        stripped = p.cas_line_prefix.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def label_segments(text: str, patterns: CaptionPatterns | None = None) -> list[str]:
    """The caption plus its per-script halves (Latin-only and Han-only), longest first.

    Returns just ``[text]`` for a single-script caption, so monolingual filings are unaffected.

    ``patterns`` carries a declared Han inventory, for the reason ``normalize_label`` gives —
    and the gate below is ``has_han``, whose range lives in ``services.han`` and is NOT the one
    declared here. See ``han_ranges``: the two sets differ today, and the gate is the narrower.
    """
    p = patterns or _CAPTION_PATTERNS
    if not text or not has_han(text):
        return [text]
    han = " ".join(p.han_run.findall(text)).strip()
    latin = p.han_run.sub(" ", text)
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
    # The LLM's own brief justification, when the method is LLM. Carried so a reviewer asking why a
    # caption was mapped (or merged with others) has the model's stated reasoning, not just a score.
    reason: str | None = None


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
    # See Candidate.reason. Set only when the winning method is LLM.
    reason: str | None = None
    # WHETHER THIS ROW IS THE WHOLE FIGURE FOR ITS CONCEPT, OR ONE PART OF IT — the model's own
    # declaration (`LlmBatchItem.role`), carried through so `_apply_result` can act on it. Only
    # ever "component" when the semantic tier said so: the deterministic tiers answer "which
    # concept is this caption", which is not a claim about completeness, so they leave it "whole"
    # and two of them on one key stay the `ambiguous_mapping` they have always been.
    role: str = "whole"
    # +1 added, -1 subtracted. Meaningless unless `role` is "component".
    sign: int = 1
    # WHAT THE MODEL CITED, AND WHAT THAT CITATION RESOLVED TO.
    #
    # The candidates offered are suggestions, so the model may answer past them — which is what
    # lets a caption reach the concept its section never predicted. Such an answer is worth
    # precisely as much as the printed row behind it, so `sources` carries the rows
    # `note_sourced.resolve_sources` matched (each with the page and figure taken OFF THE ROW), and
    # `unresolved_sources` carries the citations that matched nothing — a figure stated in prose
    # belongs to no row, and saying so is more useful than dropping it.
    sources: list[dict] = field(default_factory=list)
    unresolved_sources: list[dict] = field(default_factory=list)
    # True when the answer was NOT one of the concepts offered for this row. Not an error: it is
    # the model exercising the latitude the contract gives it. Carried so review can see that the
    # decision rests on a citation rather than on the offered shortlist.
    off_candidate: bool = False
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
    # THE NOTES, and the entry that makes the `notes` SCOPE ENFORCEABLE. `output_csv_hk_ontology`
    # declares `section_defaults.notes.section_scope = ["notes"]`, which 8 concepts inherit
    # (notes__related_party_transactions, notes__pledged_assets, notes__secure_borrowings,
    # notes__derivatives, notes__contingent_liabilities, notes__auditor_s_opinion, notes__notes,
    # notes__confirmed_with_rm). No entry here named the notes, so `section_token_of_scope("notes")`
    # returned None, `_scope_tokens` folded the declaration to frozenset() — and an EMPTY scope
    # means UNCONSTRAINED in `_in_section`. The declaration was therefore inert: measured on the
    # shipped file, each of the 5 that carries an alias ("Related party transactions", "Pledged
    # assets", "Contingent liabilities", "Derivatives", "Secured borrowings") matched at confidence
    # 1.0 under "Current assets", "NON-CURRENT ASSETS", "Operating activities" and "Revenue" alike,
    # wherever the row carried no statement verdict — the per-line path for an unclassified page,
    # and the note pass when the citing face rows disagree on their statement, both of which pass
    # `statement=None`, leaving the section gate the only structural constraint. Two of those
    # captions are shared aliases the banner is supposed to arbitrate, and the inert scope was
    # winning them outright: with the token real, "Derivatives" under a current-asset banner now
    # reaches bs_ca__deriv_and_hedg_assets_cp and "Related party transactions" under a non-current
    # banner reaches bs_nca__due_from_directors, while both keep the notes concept under a notes
    # banner. The other 63 concepts on non-notes scopes this vocabulary does not name
    # (statement_setup_controls, supplemental_data, credit_compliance, off_balance_sheet_data,
    # capital_and_lease_commitments) are untouched and stay unconstrained — a separate change,
    # because those five compact ids also have no analyst bucket.
    #
    # ONLY THE EXHAUSTED HEADINGS, and deliberately NOT the bare word in either script. English
    # "notes" is the head of "Notes payable" and "Note receivable", both of which are mapped
    # concepts; the Han 附注 / 附註 is worse, because it is the note-reference COLUMN HEADER on every
    # CSRC and HKEX Chinese statement — `row_reconstruct._NOTE_HDR` exists to recognise exactly that
    # cell, and `_is_banner_line` admits a one-token line when it is Han, so a bare-Han entry would
    # read a balance sheet's own column header as a banner and refuse every bs_ concept beneath it.
    # A banner that resolves to nothing constrains nothing; one that resolves WRONG refuses the
    # correct concept, which is the trade this table makes everywhere else too (see `income`).
    #
    # NO `_COMPACT_SECTION_TOKENS` ENTRY IS NEEDED and none is added: the scope id is literally
    # "notes", so `section_token_of_scope` resolves it through the `endswith` arm below, and no other
    # scope id in either shipped rulebook ends with "notes". Adding one would also change
    # `_COMPACT_SECTION_TOKENS` itself, which `services.buckets` imports BY IDENTITY and
    # `output_csv_hk_line_items.json`'s `vocabulary.scope_tokens` mirrors — a compact id with no
    # `_SECTION_BUCKETS`/`_OUTSIDE_TAXONOMY` home resolves to ("others", "unknown_section"), so that
    # pairing has to be made in those files, not here.
    ("notes", ("notes to the consolidated financial statements",
               "notes to the financial statements", "notes to financial statements",
               "财务报表附注")),
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
    ("income_and_expenses", ("income_and_expenses", "income and expenses", "income expenses")),
    ("adjustments_to_retained_profits", ("adjustments to retained profits",
                                         "adjustments to retained earnings")),
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


def known_captions(ontology) -> frozenset[str]:
    """Every caption the rulebook RECOGNISES, normalised — its labels and all of its aliases.

    WHAT THIS IS FOR: row reconstruction cannot tell a wrapped caption's first line from a line
    item that simply has no figures this year, because on a mainland filing the two are printed
    identically — the intra-cell leading equals the row pitch, so geometry says nothing. A CSRC
    balance sheet prints every template line whether the filer uses it or not, so 衍生金融资产 and
    应收票据 sit there empty and the wrap merge folded both into the next valued row:
    "衍生金融资产应收票据应收账款", holding 应收账款's figures. The caption the mapper then saw was
    not the caption the filing printed.

    What separates them is not shape but MEANING: a wrapped first line is an incomplete fragment
    ("Property, plant and"), while an empty line item is a complete caption the rulebook knows.
    English has a grammar test for the same question (``_is_wrapped_head``); Chinese has no
    whitespace to reason about, so the rulebook's own vocabulary is the evidence.

    Every locale's aliases, like ``_alias_index`` — a bilingual filing prints both scripts and
    recognising the printed text is locale-independent. Built as a plain set rather than by
    constructing an ``OntologyMatcher``, because extraction runs BEFORE mapping and has no reason
    to pay for the rest of the index.
    """
    out: set[str] = set()
    for m in getattr(ontology, "mappings", ()) or ():
        out.add(normalize_label(m.label or ""))
        for alias in (m.aliases or ()):
            out.add(normalize_label(alias))
        for locale_aliases in (getattr(m, "aliases_i18n", None) or {}).values():
            for alias in locale_aliases or ():
                out.add(normalize_label(alias))
    out.discard("")
    return frozenset(out)


def _review_cap(settings) -> int:
    """How many candidates a reviewed row carries — ``extraction.review_candidate_cap``.

    Read through a helper rather than inline so all four former literals (`ranked[:5]` ×3,
    `primary[:5]`) resolve to ONE number: they are the same shortlist reached by different exits,
    and a run where the UNMATCHED path showed five candidates and the accepted path showed three
    would be incoherent to the reviewer comparing two rows.

    A configured 0 means none, not "unbounded" — the `or` fallback is deliberately NOT used here,
    because a bound whose zero value restores a default is the polarity defect this codebase has
    already been bitten by twice (see `recon_rel_tolerance`).
    """
    cap = getattr(getattr(settings, "extraction", None), "review_candidate_cap", 5)
    return max(0, int(cap if cap is not None else 5))


def _det_cap(settings) -> int:
    """How many deterministic candidates are named to the model —
    ``extraction.llm_deterministic_candidate_cap``. Separate from `_review_cap` because one is a
    UI budget and the other is a request-payload budget; they were both 3-to-5 literals and are
    not the same decision."""
    cap = getattr(getattr(settings, "extraction", None),
                  "llm_deterministic_candidate_cap", 3)
    return max(0, int(cap if cap is not None else 3))


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

# The uploaded output template uses compact section ids rather than the descriptive ids used by
# the HKFRS template. Both name the same printed subsections and must resolve through one vocabulary.
_COMPACT_SECTION_TOKENS: dict[str, str] = {
    "bs_nca": "non_current_assets",
    "bs_ca": "current_assets",
    "bs_ncl": "non_current_liabilities",
    "bs_cl": "current_liabilities",
    "bs_equity": "equity",
    "is_pl": "income_and_expenses",
    "is_oci": "other_comprehensive_income",
    "is_retained": "adjustments_to_retained_profits",
    "cf_oper_indirect": "cash_flow_from_operating_activities",
    "cf_oper_direct": "cash_flow_from_operating_activities",
    "cf_investing": "cash_flow_from_investing_activities",
    "cf_financing": "cash_flow_from_financing_activities",
}

_COMPACT_KEY_SECTION_TOKENS: dict[str, str] = dict(_COMPACT_SECTION_TOKENS)

# Compact output templates can carry statement-tail sections inside keys that still
# use the broad `is_pl__` namespace. These overrides keep section gating aligned
# with the intended subsection rather than the namespace prefix.
_KEY_SECTION_OVERRIDES: dict[str, str] = {
    "is_pl__minority_interests_pl": "profit_attributable_to",
}


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
    if canonical_key in _KEY_SECTION_OVERRIDES:
        return _KEY_SECTION_OVERRIDES[canonical_key]
    descriptive = next((tok for tok, _ in SECTION_WORDS
                        if f"_{tok}__" in canonical_key), None)
    if descriptive:
        return descriptive
    return next((token for prefix, token in _COMPACT_KEY_SECTION_TOKENS.items()
                 if canonical_key.startswith(f"{prefix}__")), None)


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
    compact = _COMPACT_SECTION_TOKENS.get(scope_id)
    if compact:
        return compact
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
# neither is usable as the declaration. EVERY FIGURE IN THAT SENTENCE AND THE FIRST BULLET IS AN
# `hkfrs_hk_china_ontology.json` MEASUREMENT. The rulebook that drives the output CSV,
# `output_csv_hk_ontology.json`, measures differently on both, so it is given alongside — a reader
# who takes the hkfrs shape for "the shipped file" reads this as a protection that exists on the
# other rulebook too:
#   * `confusable_with` is a confusion graph, not a family. On hkfrs its 147 mutual pairs connect
#     into a single 47-concept component (share capital → reserves → NCI → the tax lines → both
#     bottom lines), so re-routing anywhere inside it would move an answer between concepts that
#     are different facts — the opposite of conservative. On output_csv_hk the field is declared on
#     4 of 462 concepts and its 2 mutual pairs are two isolated 2-concept components (buildings ↔
#     land use rights, trade-and-other receivables ↔ gross trade receivables) — no component to
#     re-route inside, and nothing a family could be read out of either.
#   * `section_disambiguation` is free-form prose, and only some of it names the sibling's key at
#     all. Scraping keys out of it would make a wording edit a behaviour change, and prose cannot
#     be told apart from "never confuse this with that", which means the opposite. On output_csv_hk
#     it is not sparse prose but a per-concept sentence — 395 of 462 concepts, 13 distinct values
#     ("Bind only to Balance Sheet / bs_ca.") — which scraping would make a bulk re-router.
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
        # When set, only these canonical_keys may be offered to the LLM; every other row is
        # decided by the deterministic ensemble alone (see `_llm`/`_match_chunk`).
        self._llm_only_keys = set(self.settings.extraction.llm_only_keys)
        # Guards `self.usage` mutations, which happen from worker threads when map_ontology runs
        # batch chunks concurrently (see stages/map_ontology.py).
        self._usage_lock = threading.Lock()
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
                      "computed_refused": 0,
                      # OBSERVATION ONLY — nothing branches on these three. They exist because the
                      # two counters above are STRUCTURALLY PINNED AT 0 on the rulebook that drives
                      # the output CSV (`output_csv_hk_ontology.json`, 462 concepts), so the run
                      # record read "no ties, no refusals" on a file where neither could ever be
                      # reported. Measured:
                      #
                      #  * `confusable_ties` needs a mutually-confusable pair, and 0 of that file's
                      #    462 concepts declare `confusable_with` at all (in
                      #    `hkfrs_hk_china_ontology.json`, 165 of 183 do — which is why every test
                      #    of step 6 passes while the shipped output path measures nothing). So
                      #    `exact_ties_seen` counts the shared-alias ties that actually occur and
                      #    `equal_priority_no_confusable` the subset that NOTHING in the rulebook
                      #    resolved — i.e. those settled by declaration order, which `binding.order`
                      #    step 6 forbids in as many words. See `_exact_tie`.
                      #  * `computed_refused` needs an entry in `_computed_alias_by_key`, and the
                      #    `_locked` `continue` in `__init__` runs BEFORE the `_computed_only`
                      #    branch that fills it, so a concept that is both locked and `derive` is
                      #    never indexed. 2 of that file's 3 `derive` concepts are also
                      #    `alias_matching: disabled`, leaving the index 1 entry deep out of 462.
                      #    `computed_index_skipped_locked` counts exactly that overlap.
                      "equal_priority_no_confusable": 0, "exact_ties_seen": 0,
                      "computed_index_skipped_locked": 0}
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
        self._label_index: dict[str, list[str]] = {}
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
        # ONLY `extraction_mode: extract` IS PUT IN FRONT OF THE MODEL. A concept the framework can
        # work out for itself is not the model's to guess at: if a line can be DERIVED, the declared
        # arithmetic derives it, and asking as well only creates a second answer that can disagree
        # with the first. Measured on laisun: the deterministic tiers read revenue off the face as
        # 4,995,768 and the LLM run mapped that row to nothing, so a low-precedence rung filled the
        # line with 2,609,259 instead — the model was asked about a row the alias tier had already
        # answered correctly, and answered worse.
        #
        # THIS IS NARROWER THAN `_unmatchable` AND IT IS AN LLM-ONLY BOUNDARY. It withholds the 38
        # `extract_or_derive` concepts too, and those must stay fully matchable by the DETERMINISTIC
        # tiers — `extract_or_derive` means the subtotal is sometimes printed and sometimes left to
        # arithmetic, so a filing that does print it must have the printed row read (see
        # `_computed_claim`). That is why this set is applied where candidates are offered rather
        # than folded into `_mappable_keys`, which the rule tier also reads: the printed row is
        # still read, just by the tier that reads captions instead of by the model.
        self._llm_withheld: set[str] = {m.canonical_key for m in ontology.mappings
                                        if m.extraction_mode != "extract"} | self._locked
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
                # OBSERVATION ONLY. This `continue` precedes the `_computed_only` branch below, so a
                # concept declared BOTH `alias_matching: disabled` and `extraction_mode: derive`
                # never reaches `_computed_alias_by_key` — its aliases are indexed nowhere, and
                # `_computed_claim` therefore cannot refuse a row that names it. Measured on
                # `output_csv_hk_ontology.json`: 2 of its 3 `derive` concepts carry the lock too
                # (`is_pl__deprec_and_impairment_oper_exp`,
                # `bs_nca__secur_and_other_fincl_assets_ltp`), so the index holds one entry and
                # `usage["computed_refused"]` can never leave 0. COUNTED, not reordered: which of
                # the two locks wins is a behaviour decision, and this change is measurement only.
                if m.extraction_mode == "derive":
                    # No `_usage_lock`: construction is single-threaded, and the matcher is not
                    # visible to a worker thread until `__init__` returns.
                    self.usage["computed_index_skipped_locked"] += 1
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
            label_keys = self._label_index.setdefault(normalize_label(m.label), [])
            if m.canonical_key not in label_keys:
                label_keys.append(m.canonical_key)
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

    def _prefer_label_owners(self, norm: str, keys: list[str]) -> list[str]:
        """Prefer concepts whose canonical label is the exact caption being matched.

        A concept may carry another concept's full label as an over-broad alias. Priority is useful
        for aliases of differing specificity, but it must never let that borrowed alias beat the
        concept actually named by the filing.
        """
        owners = set(self._label_index.get(norm) or [])
        exact = [key for key in keys if key in owners]
        return exact or keys

    def _exact(self, norm: str, allowed=None, reroute=None) -> Candidate | None:
        """An exact alias hit, preferring one the caller's scoping allows.

        ``allowed`` is a predicate over canonical keys (the statement/section/exclusion gate).
        When several concepts share the alias, the highest-``match_priority`` claimant that fits
        where the caption was printed wins. The rulebook's binding order runs the alias tier in
        descending priority and says in as many words never to pick by declaration order, which is
        what taking the first claimant was: 94 aliases in ``hkfrs_hk_china_ontology.json`` and 361
        in ``output_csv_hk_ontology.json`` are claimed by more than one concept, so for those the
        answer was decided by where an editor happened to add a row. Both counts are of the alias
        index this class builds, so they are what this method actually sees.

        ``reroute`` is consulted ONLY when every claimant was refused — a caption that is an alias
        of one leaf of a collision family, printed under the banner of another, is answered by the
        banner instead of being left unmapped.
        """
        keys = self._alias_index.get(norm) or []
        if not keys:
            return None
        if allowed is None:
            choices = self._prefer_label_owners(norm, keys)
            return Candidate(max(choices, key=self._priority_of), MappingMethod.EXACT, 1.0)
        ok = [k for k in keys if allowed(k)]
        if ok:
            choices = self._prefer_label_owners(norm, ok)
            return Candidate(max(choices, key=self._priority_of), MappingMethod.EXACT, 1.0)
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
        71 of ``hkfrs_hk_china_ontology.json``'s 94 shared aliases are claimed by a mutually-confusable
        pair sitting at the SAME priority (current vs non-current borrowings, notes payable,
        properties under development). For those, taking the higher priority is taking the first
        declared, which step 6 forbids in as many words. The banner normally separates them and this
        never fires; when it does not, the honest answer is both, for review.

        BOTH FIGURES ABOVE ARE hkfrs MEASUREMENTS, and naming the file matters: the rulebook that
        drives the output CSV, ``output_csv_hk_ontology.json``, declares ``confusable_with`` on 4 of
        its 462 concepts. Those 4 are two isolated pairs at equal priority (buildings ↔ land use
        rights, trade-and-other receivables ↔ gross trade receivables), so on that file step 6
        catches exactly the 8 shared aliases those pairs claim — and 208 of its 216 remaining
        equal-priority collisions (of 361 shared aliases) still fall through to :meth:`_exact`,
        which settles them by declaration order. That is what the two counters below exist to say
        out loud.
        """
        keys = [k for k in (self._alias_index.get(norm) or []) if allowed(k)]
        if len(keys) < 2:
            return []
        keys = self._prefer_label_owners(norm, keys)
        if len(keys) < 2:
            return []
        top = max(self._priority_of(k) for k in keys)
        contenders = [k for k in keys if self._priority_of(k) == top]
        if len(contenders) < 2:
            # `_confusable_tie` of a single key is [] anyway (it needs two that name each other);
            # returned here so the counters below only fire on a real tie.
            return []
        tied = self._confusable_tie(contenders)
        # OBSERVATION ONLY — no branch reads either counter. `usage["confusable_ties"]` can only
        # report the ties `confusable_with` DECLARES, and `output_csv_hk_ontology.json` declares it
        # on 4 of its 462 concepts (two mutual pairs, 8 shared aliases between them). So on that
        # file all but those 8 of its 216 equal-priority collisions fall through to `_exact`, which
        # settles them by declaration order (`max(..., key=_priority_of)` over an insertion-ordered
        # list) — precisely what `binding.order` step 6 forbids — and the run record showed almost
        # nothing at all.
        # `exact_ties_seen` is how often two concepts survived to the top priority on one alias;
        # `equal_priority_no_confusable` is the subset the rulebook gave nothing to separate them by.
        with self._usage_lock:
            self.usage["exact_ties_seen"] += 1
            if not tied:
                self.usage["equal_priority_no_confusable"] += 1
        return tied

    def _vetoed(self, canonical_key: str, caption: str) -> bool:
        r"""Whether the concept's ``exclude_hints`` rules this caption out.

        Raw string, because the body quotes a regex containing ``\S``. As a plain docstring that
        is an unrecognised escape: Python emits ``SyntaxWarning: invalid escape sequence '\S'`` on
        every import today and will raise ``SyntaxError`` in a future version.


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

        AN EXCLUSION OUTRANKS THE CONCEPT'S OWN ALIAS, and it has to: the whole point of the field
        is that an editor looking at a mis-mapping can add one line and have it stop. If an alias
        could override it there would be mis-mappings no exclusion could reach.

        THAT MAKES A SELF-CONTRADICTING CONCEPT A SILENT DEATH, so it is refused at LOAD time
        instead — see ``schemas.loader``. A hint broad enough to match the concept's own alias
        deletes that alias with no signal at all: the row maps to nothing, the alias sits in the
        file looking like it should have worked, and there is nowhere to look. Eight aliases in the
        shipped rulebook were dying that way, every one a NEGATION killed by the thing it negates —
        'Other revenue and gains' by 'revenue', 'Taxes other than income tax' by 'income tax',
        'Impairment losses on non-financial assets' by 'financial asset'. The first cost a real
        filing its income line. The fix is a narrower hint, which the loader now insists on.
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

    def _computed_claim(self, norm_segments: list[str], statement: str | None = None,
                        section: str | None = None) -> tuple[str, float] | None:
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

        THE CLAIM IS SCOPE-GATED, by the same ``statement`` and ``section_scope`` gates every other
        tier is subject to, and it has to be: the refusal reads only the WORDING of a caption, so
        without the gate a computed concept refused a row printed on a statement it does not even
        appear on. It changes nothing on the file as shipped — ``output_csv_hk_ontology.json``
        carries `alias_matching: disabled` on two of its three `derive` concepts, so
        `_computed_alias_by_key` holds exactly ONE entry and every claim comes from it. Measured
        with those two locks lifted, which is the change this gate exists to make survivable: the
        cash-flow caption "Depreciation of property, plant and equipment" is a verbatim alias of
        ``is_pl__deprec_and_impairment_oper_exp`` and so claimed it at 1.0 — beating the 0.54 the
        row's own concept ``cf_oper_indirect__depreciation`` scored — and seven cash-flow
        depreciation rows came back None. That concept is scoped to ``income_and_expenses``; the
        banner over those rows resolves to ``cash_flow_from_operating_activities``, so the section
        arm is what refuses the refusal. Note it is the SECTION arm carrying it and not the
        statement one: ``is_pl__…`` declares no ``statement`` and its key prefix is not one the
        namespace fallback knows, so `_in_statement` waves it through on every statement.

        The gate is ``section_scope`` alone, not :meth:`_in_section`: that method's empty-scope arm
        narrows a statement-level key to one leaf of a collision FAMILY, which is a decision about
        which matchable variant a banner names. A computed concept is in no family and no variant of
        it is matchable, so borrowing that arm could only refuse rows nothing else claims.
        """
        s = self.settings.extraction
        token = section_of_banner(section)
        best: tuple[str, float] | None = None
        for key, aliases in self._computed_alias_by_key.items():
            if not self._in_statement(key, statement):
                continue
            # Same shape as the batch path's section restriction: no declared scope is
            # UNCONSTRAINED (a statement-level subtotal may be printed anywhere), and an
            # unresolvable banner constrains nothing — see `_in_section`.
            scope = self._sections_of(key)
            if token and scope and token not in scope:
                continue
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

    def _refused_as_computed(self, norm_segments: list[str], rival: float,
                             statement: str | None = None,
                             section: str | None = None) -> str | None:
        """The computed concept to refuse this caption to, when nothing matchable claims it better.

        ``rival`` is the strength of the best claim a MATCHABLE concept has on the caption. A
        computed concept only takes the row off the table when it explains the caption at least as
        well: a caption another concept genuinely matches better is still that concept's row.

        ``statement`` and ``section`` are WHERE the caption was printed, and they are forwarded
        rather than dropped because a claim evaluated on wording alone refuses rows that belong to
        another statement's concept entirely — see :meth:`_computed_claim`.
        """
        claim = self._computed_claim(norm_segments, statement, section)
        if claim is None or claim[1] < rival:
            return None
        with self._usage_lock:
            self.usage["computed_refused"] += 1
        return claim[0]

    def _build_system(self) -> str:
        """The fixed reply contract, then the configured guidance, then the global policies.

        THE ORDER IS THE POINT. `_LLM_REPLY_CONTRACT` is first and is not configurable: it states
        how the answer must be shaped and cited, which the reply parser and the per-item
        attribution depend on. Everything after it is a deployment's opinion about how captions
        should be READ, and all of it comes from configuration — so an admin can change any rule
        of judgement without being able to produce a reply the system cannot use.
        """
        g = self.ontology.global_rules
        lines: list[str] = [_LLM_REPLY_CONTRACT]
        # THE CONFIGURED GUIDANCE (`LineItemSet.prompt`, carried here by `working_view`). The
        # shipped wording lives in the configuration file, seeded from
        # `DEFAULT_MAPPING_GUIDANCE` — so editing it there is the whole story, and an empty
        # prompt means "no guidance beyond the contract" rather than quietly restoring a literal.
        if getattr(self.ontology, "prompt", ""):
            lines.append("\n" + self.ontology.prompt.strip())
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
            # Also the configured cap rather than a literal — `llm_worked_examples_cap` was the
            # third payload knob declared in config and read by nothing. Its own note records why
            # the default is the literal it replaces and not `len(examples)`: raising it changes
            # what the rulebook tells the model, so that is a decision, not a default.
            for ex in self.ontology.worked_examples[
                    : max(0, int(getattr(self.settings.extraction,
                                         "llm_worked_examples_cap", 6) or 0))]:
                lines.append("- " + json.dumps(ex.model_dump(exclude_defaults=True), ensure_ascii=False))
        return "\n".join(lines)

    def _stratified_fill(self, keys: list[str]) -> list[str]:
        """``keys`` reordered so that a cut at ANY length keeps every section represented.

        A plain ``_by_priority`` fill is correct on the per-line path, where the shortlist has
        already been narrowed to the one row's section. It is wrong on the batch path. When a chunk
        carries an unresolvable banner the section restriction above is deliberately OFF, so the
        key set spans the whole statement — and ``match_priority`` correlates with statement-level
        totals, so a priority-ordered cut fills the list with ``bs_total_assets``-shaped concepts
        and evicts every ordinary leaf. Measured on the shipped balance sheet: a top-40-by-priority
        cut dropped ``bs_current_assets__inventories`` entirely.

        That would make the cap a second, quieter section restriction — precisely the refusal the
        unresolvable-banner rule exists to prevent, and it would arrive without the gate ever
        saying no. A bound on request size must not decide meaning.

        So the cut is spread instead: concepts bucketed by declared section, ordered by priority
        WITHIN each bucket, taken round-robin. If concepts have to be dropped, dropping them evenly
        is the honest way to do it.
        """
        buckets: dict[frozenset | None, list[str]] = {}
        for k in self._by_priority(keys):
            sections = self._sections_of(k)
            buckets.setdefault(frozenset(sections) if sections else None, []).append(k)
        # Statement-level concepts (those declaring no section) are taken first in each round: a
        # subtotal can be printed under any banner, which is the same reason they survive the
        # section restriction above.
        order = sorted(buckets, key=lambda b: (b is not None, sorted(b) if b else []))
        out: list[str] = []
        while any(buckets[b] for b in order):
            for b in order:
                if buckets[b]:
                    out.append(buckets[b].pop(0))
        return out

    def _concept_payload(self, keys: list[str]) -> list[dict]:
        """Candidate concepts with the criteria the LLM reasons over — definition, include/
        exclude, confusable-with (as labels), value_scope. Non-extracted headings skipped."""
        out = []
        for k in keys:
            m = self._by_key.get(k)
            if m is None or m.extraction_mode == "do_not_extract":
                continue
            # Also the choke point for the residual lock, the `derive` lock and the extract-only
            # rule, not only `_extractable_keys`/`_mappable_keys`: the capped shortlist in `match` is
            # assembled from the rule tier's keys rather than from either list, so a concept kept
            # out of one route has to be kept out of the other as well. A concept the model cannot
            # see is a concept the model cannot pick.
            #
            # `_llm_withheld` is a superset of `_unmatchable` (a locked residual is in it by
            # construction, and `derive` is not `extract`), so this one test does all three jobs.
            if k in self._llm_withheld:
                continue
            entry: dict = {
                "canonical_key": k,
                "label": m.label or k.replace("_", " "),
                "definition": m.meaning(),
                "value_scope": m.value_scope,
                # THE CAP IS THE CONFIGURED ONE, not a literal 4. `llm_example_aliases_cap` was
                # declared with a measured justification ("4 truncates 4 of the 40 candidates'
                # alias lists … 1,774 of the payload's 37,781 characters") and then read by
                # nothing, so the number in config governed no request and turning the knob did
                # nothing at all. The default is the literal it replaces, so shipped behaviour is
                # unchanged — what changes is that the setting now reaches the payload it
                # describes. Same liveness defect as the candidate cap in `_match_chunk`.
                "example_aliases": m.aliases_for(self.locale)[
                    : max(0, int(getattr(self.settings.extraction,
                                         "llm_example_aliases_cap", 4) or 0))],
            }
            # THIS CONCEPT'S OWN INSTRUCTION, beside its definition. Carried in the candidate
            # entry rather than appended to the system prompt because a call offers up to
            # `llm_candidate_cap` concepts: appending would stack forty instructions on one
            # request, most of them about concepts the caption is not, and the model would have no
            # way to tell which applied to what.
            if getattr(m, "prompt", ""):
                entry["instruction"] = m.prompt
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
            # The rulebook's own prose about the decisions this tier is here to make. "Sparse" was
            # an `hkfrs_hk_china_ontology.json` census and belongs to that file only: 18 / 8 / 7 of
            # its 183 concepts carry `section_disambiguation` / `derivation` / `is_gross_parent`
            # (the third was recorded as 2 and measures 7). On `output_csv_hk_ontology.json` — the
            # rulebook that drives the output CSV — the same three census as 395 / 0 / 32 of 462, so
            # the first is not sparse there at all: 395 concepts across 13 distinct sentences ("Bind
            # only to Balance Sheet / bs_ca."). Cheap on one file, a sentence per concept on the
            # other; either way it costs nothing where the editor wrote nothing, which is all the
            # guards below claim.
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

    # Per-candidate PROSE that is policy when every candidate says it, and a discriminator when
    # they disagree. Only prose fields are eligible: an identifying fact (`canonical_key`,
    # `definition`, `include`, `exclude`) belongs to its concept whatever the rest of the list
    # says, and folding one would state a concept's own criteria as though they governed the
    # others.
    _FOLDABLE_FIELDS = ("decomposition_rule", "section_disambiguation", "value_scope")
    # Below three candidates a fold saves nothing and costs locality, so it is not attempted.
    _FOLD_MIN_CANDIDATES = 3

    @classmethod
    def _fold_shared_fields(cls, candidates: list[dict]) -> tuple[list[dict], dict]:
        """State each policy field's prevailing value once, and keep it inline only where it differs.

        WHY. The candidate block exists to let the model DISCRIMINATE between concepts, and it is
        60% of the request. A policy field whose value is the same on most of the offered concepts
        discriminates almost nothing there: paying for it once per candidate buys repetition, not
        information about which candidate to choose. Measured on the shipped rulebook,
        `decomposition_rule` has 3 distinct values across 391 items — 358 of them the same sentence,
        which is `global_rules.no_fabricated_split` restated — and all 13 distinct
        `section_disambiguation` values are "Bind only to <statement> / <section>", which restates
        the `section` the source_item already carries and the instruction already enforces.

        IT IS A DEFAULT WITH EXCEPTIONS, SO NOTHING IS LOST. The prevailing value is stated once and
        every candidate that disagrees keeps its own inline, which overrides. Each concept's
        effective value is exactly what the rulebook authored, and the contingent-liabilities
        sentence that genuinely applies to one concept stays on that concept.

        TWO PRECONDITIONS, both about not asserting more than the rulebook says:

        * EVERY candidate must declare the field. A default lifted over a candidate that is SILENT
          would hand it a policy its author never wrote — the one way a size optimisation could
          change a decision. So silence anywhere in the block disables the fold for that field.
        * The prevailing value must be strictly more common than any other. Two values at four
          apiece have no default between them; naming one would be arbitrary.

        MEASURED PER REQUEST rather than listed in code, because the test is "does this field
        separate THESE candidates" — a property of the call. A batch scoped to one section folds its
        section sentence; a batch spanning five sections keeps all five inline, where they do
        discriminate.
        """
        if len(candidates) < cls._FOLD_MIN_CANDIDATES:
            return candidates, {}
        defaults: dict = {}
        for field in cls._FOLDABLE_FIELDS:
            values = [c.get(field) for c in candidates]
            if not all(values):                      # silence anywhere: no default may be asserted
                continue
            counts = Counter(values).most_common()
            if len(counts) > 1 and counts[0][1] == counts[1][1]:
                continue                             # no clear prevailing value
            if counts[0][1] < 2:
                continue
            defaults[field] = counts[0][0]
        if not defaults:
            return candidates, {}
        trimmed = [{k: v for k, v in c.items()
                    if not (k in defaults and v == defaults[k])}
                   for c in candidates]
        return trimmed, defaults

    def _criteria_boilerplate(self) -> frozenset[str]:
        """Words that appear in so many concepts' criteria that they cannot identify a subject.

        THE MIRROR OF THE POOL'S IDF, applied to the other side of the comparison.
        `note_context` discounts words this FILING prints everywhere; this discounts words this
        RULEBOOK writes everywhere. Both rest on the same argument — a word shared by everything
        distinguishes nothing — and the second is needed because the shipped criteria are machine
        generated: 462 of 462 definitions are the sentence "Extract the reported value for '<label>'
        from the stated section. Do not calculate or replace it, because the revised template does
        not designate this field as formula-driven."

        WITHOUT THIS FILTER THAT BOILERPLATE IS THE PROBE. Measured before it existed, the probe for
        `cf_financing__translation_adj_relating_to_cash` selected the trade-receivables note at
        0.381 on three shared words — `amounts`, `from`, `other` — every one of them from the
        template sentence and none of them from the concept. A context block about receivables was
        being attached to a cash-flow translation adjustment, and the request would have looked
        perfectly reasonable.

        THE FRACTION IS MEASURED, not chosen. Concept frequency over the shipped rulebook has a
        sharp cliff: 25 tokens appear in 76%-99.6% of the 462 concepts (the template sentence), and
        the next most common word is `balance` at 47%. Any threshold between 50% and 70% isolates
        exactly the boilerplate, so the default sits in the middle of that gap and no real financial
        vocabulary is near it.
        """
        cached = getattr(self, "_criteria_boilerplate_cache", None)
        if cached is not None:
            return cached
        fraction = float(getattr(self.settings.extraction,
                                 "llm_context_criteria_boilerplate_fraction", 0.6) or 0.0)
        concepts = list(self._by_key.values())
        if not concepts or fraction <= 0:
            self._criteria_boilerplate_cache = frozenset()
            return self._criteria_boilerplate_cache
        seen: Counter = Counter()
        for m in concepts:
            seen.update(set(_context_tokens(" ".join([
                m.label or "", m.meaning() or "", *(m.include or ()),
                *m.aliases_for(self.locale)[:4]]))))
        floor = fraction * len(concepts)
        self._criteria_boilerplate_cache = frozenset(t for t, c in seen.items() if c >= floor)
        return self._criteria_boilerplate_cache

    def _context_probe(self, caption: str, result: MappingResult) -> str:
        """The text a note or face row is scored against: what this caption might MEAN.

        The caption alone is too short to be a meaning — "Others", "Deferred taxation" and a wrapped
        fragment all share almost no content words with the note that explains them. So the probe
        adds the authored criteria of the concepts the deterministic tiers already consider possible
        for this row: their labels, definitions and include lists. Those sentences are the only
        written statement of what each concept means, which is what makes the resulting selection a
        judgement about subject matter rather than a caption-to-title string comparison.

        Deliberately the DETERMINISTIC candidates and not the whole offered list: scoring against
        all forty concepts of a statement would make every row's probe nearly the same text, and
        every row would then receive the same notes.
        """
        parts: list[str] = [caption]
        keys = [result.canonical_key] + [c.canonical_key for c in result.candidates]
        for key in list(dict.fromkeys(k for k in keys if k))[:_det_cap(self.settings) + 1]:
            m = self._by_key.get(key)
            if m is None:
                continue
            parts.append(m.label or key.replace("_", " "))
            parts.append(m.meaning() or "")
            parts.extend(m.include or ())
            parts.extend(m.aliases_for(self.locale)[:4])
        # The rulebook's own boilerplate removed, so what is left is the subject — see
        # `_criteria_boilerplate`.
        drop = self._criteria_boilerplate()
        return " ".join(t for t in _context_tokens(" ".join(p for p in parts if p))
                        if t not in drop)

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
        if self._llm_only_keys:
            keys = [k for k in keys if k in self._llm_only_keys]
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
                # `extraction.llm_line_max_tokens`, not the 512 literal that stood here: the
                # setting shipped declared and read by nothing, so the deployment number documented
                # in config.py (a model whose reasoning cannot be disabled spends the completion
                # budget thinking and returns empty content with finish_reason=length) could not
                # actually be moved. Default equals the literal, so this is a no-op until set.
                max_tokens=int(getattr(self.settings.extraction, "llm_line_max_tokens", 512)
                               or 512),
            )
        except Exception as exc:  # noqa: BLE001
            # Provider unreachable/misconfigured (commonly a missing API key) → the
            # deterministic ensemble decides. Record WHY: a run that silently degrades and
            # still reports itself as LLM-mapped overstates the quality of its own output.
            with self._usage_lock:
                self.usage["failures"] += 1
                if not self.usage["last_error"]:
                    self.usage["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            print(f"[mapping] llm call FAILED: {type(exc).__name__}: {exc}", flush=True)
            return None
        with self._usage_lock:
            self.usage["calls"] += 1
            self.usage["input_tokens"] += int(meta.get("input_tokens") or 0)
            self.usage["output_tokens"] += int(meta.get("output_tokens") or 0)
            self.usage["model"] = meta.get("model", self.usage["model"])
            call_no = self.usage["calls"]
        # Printed live (not just recorded) so a run in progress is visible in the server console
        # without waiting for the audit log entry the run writes only at the end.
        print(f"[mapping] llm call #{call_no} ok caption={raw[:60]!r} "
              f"in={meta.get('input_tokens')} out={meta.get('output_tokens')}", flush=True)
        key = (decision.canonical_key or "").strip()
        # `_unmatchable` as well as unknown: a concept was kept out of the payload precisely because
        # no printed caption may be bound to it, and a model naming one anyway is not a licence to
        # file the row there. A locked residual named by the model would put the figure in the
        # bucket that is supposed to be the section's UNEXPLAINED remainder.
        if not key or key not in self._by_key or key in self._unmatchable:
            return None
        return Candidate(key, MappingMethod.LLM, max(0.0, min(1.0, decision.confidence)),
                         allocation_status=(decision.allocation_status or "").strip() or None,
                         reason=(decision.reason or "").strip() or None)

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
        override = _KEY_SECTION_OVERRIDES.get(canonical_key)
        if override:
            return frozenset({override})
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
        with self._usage_lock:
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
        with self._usage_lock:
            self.usage["confusable_ties"] += 1
        # DELIBERATELY NOT CAPPED, unlike the review shortlists on the other exits from `match`
        # (`extraction.review_candidate_cap`). Those rank candidates by evidence and cut the tail;
        # here the tied set IS the answer — "it is one of these and the engine will not choose" —
        # so dropping a member removes the correct concept from the only list the reviewer is
        # shown, with nothing saying it was cut. Measured on output_csv_hk: the `is_oci__*` clique
        # ties five concepts sharing seven aliases and segment candidates take the list to eight,
        # so a cap of five would hide three. A truncated tie is an unfindable answer.
        return MappingResult(None, MappingMethod.UNMATCHED, 0.0,
                             [Candidate(k, MappingMethod.EXACT, 1.0)
                              for k in self._by_priority(tied)],
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

        exact identity is deterministic evidence; when an LLM is configured it still refines
        that evidence and may confirm or replace it. The rule tier and the model each contribute
        candidate evidence, the LLM makes the semantic, criteria-based call (the key driver), and
        cross-method agreement adjusts confidence and review routing. Falls back to the
        deterministic margin policy only when no LLM is configured.

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
        exact_candidate: Candidate | None = None

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
                # DO NOT SPEND A CALL ASKING ABOUT A CONCEPT THE MODEL MAY NOT NAME.
                #
                # An exact alias hit normally falls through to the semantic tier, which is right
                # when the model could name the same concept: it may know better, and the alias may
                # be a false hit. But when the hit names something in `_llm_withheld` the model
                # CANNOT return it — the key is off the candidate list and an answer naming it is
                # refused — so the call can only end in the same answer, a different one, or none.
                # For `extract_or_derive` in particular the printed row is exactly what must be
                # read, so locking in the alias is the intended outcome rather than a compromise.
                #
                # SCOPE, stated because it was originally mis-stated here: this is a cost-and-
                # coherence guard, NOT the fix for the lost-figure regression. That regression was
                # in the focus-routing ROW GATE (`stages.map_ontology`), which computed a row's
                # deterministic answer and then discarded it when forwarding the row to the model —
                # so a refused batch left the row unmapped. It is fixed there, by keeping that
                # answer as the fallback, and measured there. This guard is the same principle one
                # layer down, and it matters on the paths the row gate does not cover: a run with
                # `llm_focus_only` off sends every row through here.
                if not self.llm_enabled or exact.canonical_key in self._llm_withheld:
                    return MappingResult(exact.canonical_key, exact.method, 1.0, [exact], False,
                                         {"exact": 1.0}, allocation_status="direct_exclusive",
                                         rerouted_from=exact.rerouted_from)
                exact_candidate = exact
                scores["exact"] = 1.0
                break

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
        if exact_candidate:
            by_method["exact"] = {exact_candidate.canonical_key}
            pool.append(exact_candidate)
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
        #     Scoped like every other tier: the refusal is only the computed concept's to make where
        #     the rulebook puts that concept (see `_computed_claim`), or a cash-flow depreciation row
        #     is refused to a P&L concept whose alias it happens to share verbatim.
        computed = self._refused_as_computed(norm_segments,
                                             det_top.score if det_top is not None else 0.0,
                                             statement, section)
        if computed is not None:
            return MappingResult(None, MappingMethod.UNMATCHED, 0.0, ranked[:_review_cap(self.settings)], True, scores,
                                 allocation_status="unmapped_review", computed_claim=computed)

        # 4. LLM semantic decision (`binding.order` step 5) — the key driver, shown the
        #    deterministic shortlist, or the whole RESTRICTED set for a small ontology, plus each
        #    concept's criteria. Never the full ontology: the restriction is step 3's, applied above.
        if self.llm_enabled:
            # `_llm_withheld` and not `allowed_keys`: the deterministic tiers above ran on the full
            # restricted set, so a printed `extract_or_derive` subtotal has already had its chance
            # to be matched by caption. Only the OFFER to the model is narrowed.
            all_keys = [k for k in self._mappable_keys()
                        if k in allowed_keys and k not in self._llm_withheld]
            # Policy restriction (`extraction.llm_only_keys`): a row none of whose candidates are on
            # the allow-list is never OFFERED to the model at all — which must fall through to the
            # deterministic tiers below exactly as an unconfigured LLM would, not be reported as the
            # model having seen the row and abstained (the abstention branch just below).
            restricted_out = bool(self._llm_only_keys) and not any(
                k in self._llm_only_keys for k in all_keys)
            if restricted_out:
                llm = None
            else:
                if len(all_keys) <= s.extraction.llm_candidate_cap:
                    shortlist = all_keys
                else:
                    # Deterministic evidence first, then the rest of the RESTRICTED set to fill the
                    # cap. The fill is not padding: with the fuzzy tier gone the evidence here is a
                    # rule hit or nothing at all, and a section whose concepts merely have no hints
                    # authored on them would otherwise reach the model as a shortlist of one — or of
                    # none, which would leave the model to answer about a set it was never shown. The
                    # restriction to the section (step 3) is what keeps the fill honest;
                    # ``_by_priority`` below decides the reading order within it.
                    shortlist = list(dict.fromkeys(
                        ([exact_candidate.canonical_key] if exact_candidate else [])
                        + ([rule.canonical_key] if rule else [])
                        + self._by_priority(all_keys)))[: s.extraction.llm_candidate_cap]
                # Offered in descending match_priority, so the long specific concept is read before
                # the short generic one it collides with on token overlap: "Current portion of
                # long-term debt" (68) ahead of "Borrowings (current)" (60), a mutually
                # `confusable_with` pair the file happens to DECLARE the wrong way round, so
                # priority is doing real work here rather than agreeing with insertion order.
                #
                # The rulebook's own note on match_priority names a different pair ("Total assets
                # less current liabilities", 86, over "Total current liabilities", 82). That one can
                # no longer both be offered — every `*_top_level` total is `extract_or_derive` and
                # so withheld from the model by `_llm_withheld` — which is why the example here is
                # an in-section `extract` pair instead. Applied AFTER the cap on
                # purpose: priority decides what the model reads first, never which concepts it is
                # allowed to see, so a high-priority concept with no evidence behind it cannot evict
                # an evidenced one.
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
                    candidates=[llm] + ranked[:max(0, _review_cap(self.settings) - 1)], needs_review=needs_review, scores=scores,
                    allocation_status=alloc, agreement=["llm", *agreement], reason=llm.reason,
                )
            # A real provider failure or request error should not wipe out deterministic evidence.
            # Only a deliberate abstention (empty canonical_key) is a review-worthy no-answer — and
            # only when the row was actually offered to the model. Restricted out by policy, it was
            # never asked, and falls through to the deterministic tiers below like an unconfigured
            # LLM would.
            if restricted_out or self.usage["failures"] > 0:
                # Continue into the deterministic fallback below.
                pass
            else:
                return MappingResult(None, MappingMethod.UNMATCHED, 0.0, ranked[:_review_cap(self.settings)], True, scores,
                                     allocation_status="unmapped_review")

        # 5. Deterministic decision when no LLM is configured, or when the configured LLM failed.
        #    If a configured LLM abstained, the row is left UNMAPPED for a human rather than
        #    presenting unrefined deterministic evidence as a completed semantic decision.
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
                with self._usage_lock:
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
                candidates=primary[:_review_cap(self.settings)], needs_review=not accept, scores=scores,
                allocation_status="direct_exclusive" if accept else "unmapped_review",
            )

        # Nothing confident — route to review unmapped rather than guessing from the wording.
        return MappingResult(None, MappingMethod.UNMATCHED, 0.0, ranked[:_review_cap(self.settings)], True, scores,
                             allocation_status="unmapped_review")

    # One batch call's RESPONSE budget, and the chunk size it implies.
    #
    # Measured from the response envelope rather than guessed: `LlmBatchDecision` serialises one
    # decision as {"item_id": "<uuid>", "canonical_key": "…", "confidence": 0.95,
    # "allocation_status": "…"}, which is 238 characters for this file's longest canonical_key (101
    # chars) with an allocation_status set — measured by dumping the model, not estimated from the
    # schema. JSON made of UUIDs and long snake_case identifiers tokenises at roughly three
    # characters per token, so one decision costs about 80 response tokens.
    #
    # RAISED FROM 80 TO 95 when `role` and `sign` were added to the reply, and the test that
    # measures the envelope is what caught it: a 25-item batch serialises to 6,864 characters —
    # about 2,288 tokens — against the 2,256 the old slope allowed. Thirty-two tokens short of a
    # full batch, which does not degrade gracefully. A truncated reply is not a partial answer: the
    # JSON fails to parse, the whole chunk falls back to per-line matching, the cross-line context
    # the batch existed for is lost, and the run still reports itself as LLM-mapped. `"role":
    # "whole","sign":1` is ~26 characters, so the slope has to move with the schema — which is
    # exactly why that test measures rather than asserting a number.
    # 170, RAISED WITH THE SCHEMA AGAIN. `sources` is the third field to move this number, and it
    # moves it furthest because it is a LIST: an off-candidate answer cites the row or rows the
    # figure is printed on, and each entry carries a note, a caption as printed and sometimes the
    # sentence it came from. Measured on the real envelope — 84 tokens a decision with no citation,
    # 112 with one, 139 with two, 167 with three. A batch is 25 decisions, so a slope that assumed
    # the uncited case would truncate any reply where the model exercised the latitude the contract
    # now gives it, and a truncated reply does not parse: the whole chunk drops to the weaker
    # per-line path on a run that still calls itself LLM-mapped.
    #
    # Sized for THREE citations a decision rather than the average, because the cost of over-
    # reserving is a slightly smaller batch and the cost of under-reserving is a lost chunk.
    _BATCH_RESPONSE_TOKENS_PER_ITEM = 170
    _BATCH_RESPONSE_RESERVE = 256          # the envelope itself, plus a margin against truncation
    # A transport chunk, not a semantic boundary: section results are carried into the second-level
    # statement pass as preliminary mappings. This only bounds one structured response's size.
    BATCH_MAX_ITEMS = 25

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

    def _effective_batch_max_tokens(self, n_items: int) -> int:
        """Visible JSON budget for this mapping response.

        ``llm.max_tokens`` is the global ceiling for free-form calls. Sending that ceiling as the
        requested completion allocation for a small structured mapping response makes compatible
        gateways reserve millions of tokens and time out before they answer. The batch envelope
        itself determines the only budget this call needs.

        The FLOOR half of that comes from `extraction.llm_batch_response_floor_tokens`, because how
        much headroom a reply needs is a fact about the gateway and not about this vocabulary (the
        slope in :meth:`_batch_max_tokens` is the half that is, so it stays in code). It was the
        literal 8192 here while the setting was read by nothing: with
        FINEX_EXTRACTION__LLM_BATCH_RESPONSE_FLOOR_TOKENS=1234 the setting reported 1234 and this
        method still returned 8192 for both 1 and 25 items, so the declared control was inert and
        the operator had no lever on the documented silent failure (a truncated batch reply does not
        parse, the chunk falls back to the weaker per-line path, and the run still reports itself as
        LLM-mapped). ``or 0`` and not ``or 8192``: a configured 0 means "no floor, use the
        derivation", which is a real answer for a gateway that needs no headroom — restoring the
        default there would make 0 unsettable.
        """
        return max(int(getattr(self.settings.extraction, "llm_batch_response_floor_tokens", 8192)
                       or 0),
                   self._batch_max_tokens(n_items))

    def match_batch(self, items: list[tuple[str, str]],
                    statement: str | None = None,
                    sections: dict[str, str | None] | None = None,
                    preliminary: dict[str, MappingResult] | None = None,
                    require_complete: bool = False,
                    chunk_size: int | None = None,
                    context_pool=None,
                    cited_notes: dict[str, set[str]] | None = None,
                    identified_notes: list[dict] | None = None,
                    notes=None,
                    ) -> dict[str, MappingResult]:
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
        size = chunk_size or self.BATCH_MAX_ITEMS
        for start in range(0, len(items), size):
            chunk = items[start:start + size]
            out.update(self._match_chunk(
                chunk, statement, sec, preliminary or {}, require_complete=require_complete,
                context_pool=context_pool, cited_notes=cited_notes,
                identified_notes=identified_notes, notes=notes,
                ))
        return out

    def _match_chunk(self, items: list[tuple[str, str]], statement: str | None,
                     sec: dict[str, str | None],
                     preliminary: dict[str, MappingResult],
                     require_complete: bool = False,
                     retry_depth: int = 0,
                     context_pool=None,
                     cited_notes: dict[str, set[str]] | None = None,
                     identified_notes: list[dict] | None = None,
                     notes=None,
                     ) -> dict[str, MappingResult]:
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
        # Narrowed before the CAP, not only inside `_concept_payload`: the cap seeds itself from each
        # row's deterministic suggestion, so filtering only at payload time would let a withheld
        # concept take a seat in the shortlist and then be dropped, spending a slot on nothing.
        keys = [k for k in self._mappable_keys()
                if self._in_statement(k, statement) and k not in self._llm_withheld]
        if tokens and not unresolved:
            # One unresolvable banner and the restriction is off for the chunk: that row is
            # unconstrained by the gate (see `_in_section`), so narrowing the list would refuse it a
            # concept the gate would have allowed — a worse error than offering too much.
            #
            # GIVING A SCOPE A REAL TOKEN CAN COST IT ITS PLACE HERE, because a concept whose only
            # token no banner phrase can produce would be narrowed out of every chunk. Measured when
            # `notes` gained its token (see `SECTION_WORDS`): on a notes chunk the banner resolves to
            # `notes`, the list goes 445 -> 70 keys and all 8 notes__* concepts survive — including
            # `notes__contingent_liabilities`, a declared `llm_focus_key`. On the four classifier
            # statements they were never in `keys` to begin with: they declare `statement: notes`, so
            # `_in_statement` drops them one line above. No exemption is needed; a NEW token whose
            # banner phrases are absent from this table would need one.
            keys = [k for k in keys
                    if not self._sections_of(k) or (self._sections_of(k) & tokens)]
        if self._llm_only_keys:
            keys = [k for k in keys if k in self._llm_only_keys]

        fallback = OntologyMatcher(self.ontology, locale=self.locale, settings=self.settings)
        deterministic = {
            iid: preliminary.get(iid) or fallback.match(
                label, statement=statement, section=sec.get(iid))
            for iid, label in items
        }

        # …AND BOUND THE LIST, which the per-line path in `match` has always done and this one
        # never did. `_concept_payload` costs roughly 970 characters per concept, so an uncapped
        # balance-sheet chunk offers all 202 of that statement's concepts — 196,607 characters,
        # about 49k tokens — in EVERY call, whatever the chunk is asking about.
        #
        # MEASURED ON TWO REAL FILINGS. The provider refused the request outright (413
        # `request_too_large`) or rate-limited it on tokens-per-minute (429 "Requested 15931"),
        # every mapping call failed, and both runs completed reporting `strategy: "deterministic"`
        # with `llm_calls: 0` — a full-capability extraction silently degraded to the weaker path
        # with nothing in the output saying the model had never been asked.
        #
        # The bound already existed. `extraction.llm_candidate_cap` — code default 40, though this
        # deployment runs it at 16 from `.env` (`FINEX_EXTRACTION__LLM_CANDIDATE_CAP`), so do not
        # read 40 as the number in force — was applied in `match` alone, so the path that decides
        # essentially every statement row was the one path running unbounded, and the size of the
        # request scaled with the filing: the bigger the document,
        # the more certain it was to lose the LLM path entirely.
        #
        # SEEDS ARE NEVER EVICTED. A chunk carries up to BATCH_MAX_ITEMS captions, so capping by
        # priority alone could drop the very concept a row's own deterministic tier proposed —
        # which would grade the model on a list its answer was excluded from, the same defect the
        # section restriction above exists to prevent. Every row's suggestion and its top
        # candidates go in first and the cap bounds only the FILL, so the effective size is a
        # FLOOR of `llm_candidate_cap` rather than a ceiling on the evidence.
        cap = int(getattr(self.settings.extraction, "llm_candidate_cap", 0) or 0)
        if cap > 0 and len(keys) > cap:
            in_scope = set(keys)
            seeded = [
                k for iid, _ in items
                for k in ([deterministic[iid].canonical_key]
                          + [c.canonical_key for c in deterministic[iid].candidates[:_det_cap(self.settings)]])
                if k and k in in_scope
            ]
            seeded = list(dict.fromkeys(seeded))
            # The FILL is spread across sections, not taken in priority order — see
            # `_stratified_fill` for why priority order would turn the cap into a second, quieter
            # section restriction. `_by_priority` is applied again below to decide the reading
            # order, so the cap never chooses what the model may see when evidence points at it.
            keys = list(dict.fromkeys(
                seeded + self._stratified_fill(keys)))[:max(cap, len(seeded))]
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
        # WHAT WAS ACTUALLY OFFERED for this chunk. The gate below compares the model's answer
        # against this rather than against the whole configuration: answering past the offered set
        # is allowed and is the case that needs a citation.
        offered_keys = {c["canonical_key"] for c in candidates}
        # The rows a citation is resolved against — real extracted rows, so the page and figure come
        # off the document and never from the model.
        notes_for_sources = notes or ()
        caption_by_id = dict(items)
        # The section given to the model is the NORMALISED token, not the raw banner: the gate
        # downstream compares `section_of_key` against `section_of_banner`, so naming the raw text
        # would hand the model a vocabulary its answer is not judged in. A banner that normalises
        # to nothing — an umbrella ("EQUITY AND LIABILITIES"), a group heading ("Adjustments
        # for:"), anything unrecognised — is omitted rather than passed through, because the gate
        # lets those rows go anywhere and the prompt must not imply a constraint that is not real.
        def _sec_token(iid: str) -> str | None:
            return section_of_banner(sec.get(iid))

        # `deterministic` is built ABOVE, before the candidate cap: the cap is seeded from each
        # row's own deterministic evidence, so that evidence has to exist before the list is cut.

        # WHAT THE DOCUMENT SAYS ABOUT EACH ROW — several notes AND face rows from elsewhere in the
        # filing, chosen because they discuss the row's subject (`services.note_context`). This
        # replaces a field that carried only the note a row explicitly CITED, which supplied nothing
        # at all for the majority of face rows and could not show the relationship between two
        # printed rows that the `role` decision (whole vs component) is entirely about.
        #
        # Selected HERE rather than by the caller because the probe is built from each row's own
        # deterministic candidates, and those exist only inside this method.
        own_rows = {iid for iid, _ in items}
        context: dict[str, list[dict]] = {}
        if context_pool is not None and len(context_pool):
            ex = self.settings.extraction
            for iid, label in items:
                found = context_pool.select(
                    probe_text=self._context_probe(label, deterministic[iid]),
                    cited_refs=(cited_notes or {}).get(iid) or set(),
                    exclude_row_ids=own_rows,
                    notes_cap=int(getattr(ex, "llm_context_notes_cap", 3) or 0),
                    face_cap=int(getattr(ex, "llm_context_face_cap", 3) or 0),
                    char_budget=int(getattr(ex, "llm_context_char_budget", 1200) or 0),
                    min_score=float(getattr(ex, "llm_context_min_score", 0.35) or 0.0))
                if found:
                    context[iid] = found

        # THE PREVAILING VALUE OF EACH POLICY FIELD, stated once instead of once per candidate.
        # A candidate that disagrees keeps its own inline and that overrides, so no concept's
        # effective policy changes — see `_fold_shared_fields`.
        candidates, shared = self._fold_shared_fields(candidates)

        payload: dict = {
            "instruction": "Confirm or correct the deterministic evidence for every source_item, "
                           "then map it to exactly one candidate canonical_key by meaning and the "
                           "policies. Reference item_id and canonical_key; "
                           "do not output values. source_items are in the order they are printed "
                           "in the document. When an item carries a `section`, the concept you "
                           "choose must belong to that section. Each item's `context` is what this "
                           "filing says elsewhere about the same subject: a `note` is a breakdown "
                           "and a `face` row is a printed statement line, `cited: true` marks a "
                           "note the item's own text references, and an `amount` larger than the "
                           "item's own is the evidence that the item is a component rather than a "
                           "whole figure. `identified_notes` are the notes this filing's "
                           "configuration names as the source for a line, given in full with "
                           "their prose — a figure stated only in a footnote is found there and "
                           "nowhere else. `candidate_policy_defaults` applies to every candidate "
                           "except one that states its own value for the same field.",
            # THE EVIDENCE BEFORE THE CLOSED LIST. `source_items` and their context are read first
            # and `candidates` last, because the question is what each row means and the candidate
            # list is only the vocabulary the answer must be expressed in.
            # THE SUGGESTION IS FILTERED BY THE SAME SET AS THE CANDIDATE LIST. `deterministic` comes
            # from the provider-less `fallback` matcher above, whose tiers exclude only
            # `_unmatchable` — so without this it names concepts the candidate list withholds and
            # the refusal below would reject, which is the worst of the three states: the request
            # INVITES an answer it will then discard, and the model has no way to tell that the key
            # it was just shown is not one it may use.
            #
            # NOT HYPOTHETICAL, measured on the shipped config: "Total current assets", "Total
            # current liabilities", "Gross profit", "Profit before taxation", "Profit for the year"
            # and "Inventories" all resolve to a withheld concept, so a balance-sheet or P&L chunk
            # carrying any subtotal row hit this. Those rows lose nothing by the omission — a
            # concept is withheld precisely because the framework works it out for itself, and the
            # deterministic tier that produced the suggestion has already filed the figure.
            "source_items": [
                {"item_id": iid, "caption": label,
                 **({"section": tok} if (tok := _sec_token(iid)) else {}),
                 **({"deterministic_suggestion": suggestion}
                    if (suggestion := deterministic[iid].canonical_key)
                    and suggestion not in self._llm_withheld else {}),
                 "deterministic_candidates": [candidate.canonical_key
                                              for candidate in deterministic[iid].candidates[:_det_cap(self.settings)]
                                              if candidate.canonical_key not in self._llm_withheld],
                 **({"context": context[iid]} if context.get(iid) else {})}
                for iid, label in items
            ],
        }
        # What each section's residual is expected to absorb (`expected_components`), so "none of
        # these" is a licensed answer for the captions the rulebook already knows have no concept.
        expectations = self._residual_expectations(statement, tokens or None)
        if expectations:
            payload["residual_expectations"] = expectations
        # THE NOTES THE CONFIGURATION IDENTIFIES, in full and once per request.
        #
        # Not a similarity guess: an author declared in `note_source` that this note is where a
        # line's figure lives, which outranks any score — so the note is passed whole, every row
        # AND the surrounding prose. The prose is the reason: on laisun.pdf the operating-expense
        # share of depreciation is disclosed only in a footnote ("HK$529,841,000 … included in
        # 'other operating expenses'"), and 529841 appears in no row value anywhere in the
        # document, so neither a row-caption regex nor the row-based per-item context could reach
        # it.
        #
        # BESIDE `source_items`, NOT INSIDE EACH ONE. This is document-level evidence and it is
        # large — 24,910 characters on laisun. Repeating it per source item would be roughly a
        # megabyte on a 43-row request and would fail the provider rather than merely cost more.
        if identified_notes:
            payload["identified_notes"] = identified_notes
        if shared:
            payload["candidate_policy_defaults"] = shared
        # LAST, deliberately. Everything above is the question — the rows, what the filing says
        # about them, and the policies that govern the answer. This is the vocabulary the answer has
        # to be expressed in, and it is the largest block in the request; reading it first invites
        # the model to shop the list for a near-enough label instead of deciding what the row means.
        payload["candidates"] = candidates
        user = json.dumps(payload, ensure_ascii=False, indent=2)
        with self._usage_lock:
            self.usage["batch_chunks"] += 1
            self.usage["batch_max_items"] = max(self.usage["batch_max_items"], len(items))
        try:
            decision, meta = self.llm_provider.complete_structured(
                system=self._batch_system,
                messages=[{"role": "user", "content": user}],
                response_schema=LlmBatchDecision,
                max_tokens=self._effective_batch_max_tokens(len(items)),
            )
        except Exception as exc:  # noqa: BLE001
            # Record WHY, as `_llm` does. A truncated or refused batch used to fall back per line
            # in complete silence, so a run whose every batch failed still reported itself as
            # LLM-mapped with no error to point at.
            with self._usage_lock:
                self.usage["failures"] += 1
                if not self.usage["last_error"]:
                    self.usage["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
                last_error = self.usage["last_error"]
            print(f"[mapping] batch llm call FAILED ({len(items)} items): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            # DEGRADE LOUDLY, DO NOT DISCARD THE RUN.
            #
            # This used to raise, and the raise propagated out of Pipeline.run and failed the whole
            # extraction at stage 6 of 21. That was a deliberate choice, and the comment above says
            # what it was guarding: a batch that fell back "in complete silence", leaving a run
            # reporting itself as LLM-mapped with no error to point at. The concern was SILENCE,
            # not continuation — and silence is now impossible, because usage["failures"] and
            # usage["last_error"] are recorded here and map_ontology reports both onto the run's
            # mapping strategy and reason.
            #
            # What made the trade untenable: a real 210-page run died having completed twenty
            # stages, because one 18-row batch came back cut at column 5000. On a rate-limited free
            # tier a 429 does the same. Discarding a whole filing's work to avoid overstating the
            # confidence of a few rows is the wrong way round — the rows can be marked, the work
            # cannot be recovered.
            #
            # Returning {} hands these item_ids back UNANSWERED, which is a path match_batch
            # already has and already handles: its caller falls back to the deterministic tiers for
            # anything the batch did not resolve. Those rows get the lexical answer rather than a
            # judged one, every one of them carries LLM_BATCH_FAILED, and the run says so.
            return {iid: MappingResult(
                canonical_key=None, method="llm_batch_failed", confidence=0.0,
                candidates=[], needs_review=True,
                reason=f"batch of {len(items)} rows was not answered ({last_error}); "
                       f"this row fell back to the deterministic tiers",
            ) for iid, _ in items}
        with self._usage_lock:
            self.usage["calls"] += 1
            self.usage["input_tokens"] += int(meta.get("input_tokens") or 0)
            self.usage["output_tokens"] += int(meta.get("output_tokens") or 0)
            self.usage["model"] = meta.get("model", self.usage["model"])
            call_no = self.usage["calls"]
        # Printed live so a batch run's progress is visible in the server console as it happens.
        print(f"[mapping] batch llm call #{call_no} ok items={len(items)} "
              f"in={meta.get('input_tokens')} out={meta.get('output_tokens')}", flush=True)

        acc = self.settings.extraction.auto_accept_confidence
        out: dict[str, MappingResult] = {}
        answered_ids: set[str] = set()
        for d in decision.mappings:
            # An item_id we did not ask about is not a decision about anything. Unchecked, it
            # reached the caller and crashed the stage (`by_id[iid]` → KeyError, killing the whole
            # extraction), and an id that happened to be another group's real row silently applied
            # this statement's decision to that one. It also defeated the gate: the caption lookup
            # returned "" for an unknown id, and both caption-dependent arms of `_allowed` are
            # skipped when the caption is empty.
            if d.item_id not in caption_by_id:
                with self._usage_lock:
                    self.usage["batch_unknown_ids"] += 1
                continue
            answered_ids.add(d.item_id)
            key = (d.canonical_key or "").strip()
            # NO ANSWER, or a key that names no concept at all. The second is referential
            # integrity rather than an opinion about the answer: a key the configuration does not
            # carry cannot be stored, exported or reviewed.
            #
            # WHAT IS WITHHELD FROM THE CANDIDATE LIST IS WHAT IS REFUSED AS AN ANSWER, and the two
            # are the same set (`_unmatchable`) on purpose — see the cited-off-candidate branch
            # below, which is where the refusal now lives because a citation is the only thing that
            # could otherwise have unlocked it.
            if not key:
                continue
            # ONE MEMBERSHIP TEST, because there is one kind of concept. This carried a second set
            # (`sub_item_keys`) and an `is_sub_item` branch below it, for the 77 definitions the
            # working view used to drop for being off-template. They are ordinary concepts in
            # `_by_key` now — see `working_view._definition_of` — so admitting them needs no channel
            # of its own, and the branch that used to decide them separately is gone with it.
            if key not in self._by_key:
                with self._usage_lock:
                    self.usage["batch_refused"] += 1
                    self.usage["batch_unknown_key"] = self.usage.get("batch_unknown_key", 0) + 1
                continue
            # A concept from a different section than the row's banner is refused here for the
            # same reason it is refused in `match`. The model is now TOLD the section, so this is
            # a backstop rather than the only line of defence — and it still carries the two arms
            # that have nothing to do with sections: the concept's own exclusion criteria, and a
            # caption naming a mutually exclusive class.
            caption = caption_by_id[d.item_id]
            banner = sec.get(d.item_id)
            # AN ANSWER PAST THE OFFERED CANDIDATES IS ALLOWED, and traceability is what pays for
            # it. The candidates are the concepts this row's statement and section made likely;
            # the model is told they are suggestions, so a caption whose real concept was never
            # offered can still reach it. Two things are still required, and neither is a
            # constraint on the model's judgement:
            #
            #   * THE KEY MUST EXIST in the configuration. A key that names no concept cannot be
            #     stored, cannot be exported and cannot be reviewed — that is referential
            #     integrity, not an opinion about the answer.
            #   * IT MUST BE CITED. An off-candidate answer with no `sources` is discarded, because
            #     a mapping nobody can trace to a printed row is not reviewable — and the statement
            #     and section gates that would otherwise have caught a wrong one are deliberately
            #     not applied to it.
            #
            # A cited off-candidate answer therefore SKIPS the statement/section gate and is flagged
            # for review instead, with the rows its citation resolved to.
            off_candidate = key not in offered_keys
            # ONLY A CITED OFF-CANDIDATE ANSWER TAKES THE NEW PATH. An UNCITED one falls through to
            # the gates below exactly as it always did — and that is not merely conservative, it
            # preserves a correction this branch had quietly disabled: `_family_route` takes an
            # answer that is right about WHAT KIND of thing the row is and wrong only about which
            # section variant, and corrects it to the sibling the banner names. That answer is
            # off-candidate by definition (its section was not the row's), so refusing every
            # off-candidate answer here threw away every reroute — measured by
            # `test_a_banner_naming_two_leaves_of_a_family_refuses_rather_than_guessing`, which went
            # from a corrected mapping to none.
            if off_candidate and not (d.sources or ()):
                with self._usage_lock:
                    self.usage["batch_uncited_off_candidate"] = self.usage.get(
                        "batch_uncited_off_candidate", 0) + 1
                # A WITHHELD CONCEPT NAMED WITHOUT A CITATION is refused exactly as it always was.
                # Anything else falls through to the gates below, which is what keeps
                # `_family_route` working: an answer right about WHAT the row is and wrong only
                # about its section variant is off-candidate by definition, and correcting it is
                # better than losing it.
                if key in self._unmatchable:
                    with self._usage_lock:
                        self.usage["batch_refused"] += 1
                    continue
            if off_candidate and (d.sources or ()):
                # WHAT IS WITHHELD FROM THE CANDIDATE LIST IS REFUSED AS AN ANSWER, and this is the
                # one boundary the latitude above does not cross. The test is `_llm_withheld` —
                # exactly the set `_concept_payload` withholds — so the list the model is shown and
                # the answers it may give are ONE decision rather than two that drift apart. A
                # citation buys latitude about WHERE a figure came from; it does not buy a line the
                # framework was never going to ask about.
                #
                # Three reasons, counted separately because they mean different things to whoever
                # reads the counters:
                #
                #   * `_locked` — the section residuals, whose purpose is to carry the UNEXPLAINED
                #     remainder. A figure filed there does not merely risk a wrong mapping, it makes
                #     the reconciliation that would have REPORTED the gap tie instead. That is not
                #     the model saying where a figure came from; it is writing into the mechanism
                #     that audits the saying.
                #   * `extraction_mode: derive` — THERE IS NO LLM CALL FOR A COMPUTED LINE. Its
                #     figure comes from a declared cascade over its sub-line items: rung P1 sums the
                #     four operating-expense notes, P2 takes the profit-before-tax callout, P3 is
                #     the total less the cost-of-sales share. A figure accepted onto the parent
                #     bypasses all of it — the rung never runs, so the record loses WHICH of the
                #     filing's several disclosures the number came from, and the cross-check against
                #     the other rungs is skipped. The number arrives looking identical either way,
                #     which is why this is refused rather than merely deprioritised. It was a real
                #     hole: the prose path first wrote 529,841 straight onto
                #     `is_pl__deprec_and_impairment_oper_exp`. The answer is the SUB-ITEM
                #     (`sub__pbt_oper_exp_depreciation`), which is what P2 reads.
                #   * `extraction_mode: extract_or_derive` — derivable, so not the model's to guess
                #     at. The printed row is still read; the DETERMINISTIC tiers read it, and they
                #     ran on the full set before this point.
                if key in self._llm_withheld:
                    with self._usage_lock:
                        self.usage["batch_refused"] += 1
                        if key in self._locked:
                            counter = "batch_residual_named"
                        elif key in self._computed_only:
                            counter = "batch_computed_parent_named"
                        else:
                            counter = "batch_derivable_named"
                        self.usage[counter] = self.usage.get(counter, 0) + 1
                    continue
                resolved, unresolved = note_sourced.resolve_sources(d.sources, notes_for_sources)
                with self._usage_lock:
                    self.usage["batch_off_candidate"] = self.usage.get("batch_off_candidate", 0) + 1
                conf = max(0.0, min(1.0, d.confidence))
                out[d.item_id] = MappingResult(
                    key, MappingMethod.LLM, conf,
                    candidates=[Candidate(key, MappingMethod.LLM, conf, reason=d.reason)],
                    # ALWAYS reviewed: the gate that would have checked it was skipped, so a human
                    # sees every one of these rather than the engine deciding it is fine.
                    needs_review=True, scores={"llm": conf},
                    allocation_status=(d.allocation_status or "").strip() or None,
                    agreement=["llm"], reason=d.reason,
                    role=d.role, sign=d.sign,
                    sources=resolved, unresolved_sources=unresolved, off_candidate=True)
                continue
            # The same refusal `match` makes, for the same reason: a caption that names a concept the
            # framework COMPUTES is not the model's to re-home, and the model was never offered that
            # concept to name. The model reports no score on the deterministic scale, so what the
            # claim is weighed against is the caption's own alias evidence for the concept it chose.
            norm_segments = [n for n in (normalize_label(seg)
                                         for seg in label_segments(caption)) if n]
            # Scoped on this path too, and not only in `match`: the rival here is the caption's own
            # alias evidence for the concept the model chose, which is WEAKER than a deterministic
            # hit — measured 0.54 for "Depreciation of property, plant and equipment" against
            # `cf_oper_indirect__depreciation` — so an out-of-section computed claim at 1.0 wins by
            # more here than it does per-line. Gating one path and not the other would leave the
            # batch call, which decides essentially every statement row, refusing them.
            computed = self._refused_as_computed(
                norm_segments, self._alias_evidence(key, norm_segments), statement, banner)
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
                    with self._usage_lock:
                        self.usage["batch_refused"] += 1
                    continue
                self._record_route(key, target)
                rerouted_from, key = key, target
            conf = max(0.0, min(1.0, d.confidence))
            alloc = (d.allocation_status or "").strip() or (
                "direct_exclusive" if self._by_key[key].value_scope == "exclusive_leaf" else None)
            reason = (d.reason or "").strip() or None
            # THE MODEL'S COMPLETENESS DECLARATION, carried through unchanged. `role` is only ever
            # "component" because the model said so — a component licenses the sum and, unlike
            # "whole", is a claim the reconciliation can check. Read defensively: an older model
            # or a reply that omits the field is "whole", which is the conservative answer and
            # keeps the pre-existing `ambiguous_mapping` protection in force.
            role = getattr(d, "role", "whole") or "whole"
            sign = -1 if int(getattr(d, "sign", 1) or 1) < 0 else 1
            with self._usage_lock:
                if role == "component":
                    self.usage["components_declared"] = (
                        self.usage.get("components_declared", 0) + 1)
            out[d.item_id] = MappingResult(
                canonical_key=key, method=MappingMethod.LLM, confidence=conf,
                candidates=[Candidate(key, MappingMethod.LLM, conf, rerouted_from=rerouted_from,
                                      reason=reason)],
                needs_review=conf < acc, scores={"llm": conf},
                allocation_status=alloc, agreement=["llm"], rerouted_from=rerouted_from,
                reason=reason, role=role, sign=sign,
            )
        missing = [(iid, label) for iid, label in items if iid not in answered_ids]
        if missing and require_complete:
            if retry_depth:
                raise RuntimeError(
                    f"LLM omitted {len(missing)} required mapping rows after batch retry")
            out.update(self._match_chunk(
                missing, statement, sec, preliminary,
                require_complete=True, retry_depth=retry_depth + 1,
                context_pool=context_pool, cited_notes=cited_notes,
                identified_notes=identified_notes, notes=notes,
                ))

        # Section-level proposals may be incomplete; the required statement pass above corrects
        # them. No deterministic result is substituted for an omitted LLM decision.
        for iid, label in items:
            if iid not in out:
                out[iid] = MappingResult(
                    None, MappingMethod.UNMATCHED, 0.0, [], True, {"llm": 0.0},
                    allocation_status="unmapped_review")
        return out
