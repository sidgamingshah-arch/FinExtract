"""Match a printed caption to a line item — the FUNCTIONALITY half of the merge.

The schema merge made `LineItemDef` able to carry every declaration a concept makes; the seed
build moved all 462 across. Neither of those changed what the pipeline does, because the
line-item layer had no matcher: for an extracted line `services.line_items.evaluate` was one line
returning whatever it had been handed. Every declaration on the screen — aliases, hints, vetoes,
the gate — was read by nothing. This module reads them.

WHAT IT PORTS, and from where. `OntologyMatcher`'s DETERMINISTIC path, tier for tier:

    the gate            `_allowed`      = statement AND section AND not vetoed      (mapping:1649)
    locks               `_unmatchable`  = alias_matching disabled, extraction_mode derive
    exact alias         `_exact`        gate handed IN, then label ownership, then priority
                                                                                    (mapping:1083)
                                                                                    (mapping:1137)
    rule tier           `_rule`         regex_hints then keyword_hints, priority-ordered, on BOTH
                                        the raw-lowercased and the normalised caption (mapping:1193)

THE SEMANTIC TIER IS NOT PORTED, and that is a scope statement rather than an omission. The LLM
tier consumes the same per-concept payload — `definition`, `include_criteria`,
`exclude_criteria` — which the merged model now carries in full, so it can be
pointed at this registry without changing what it is shown. What it cannot be is proven equivalent
the way the deterministic path can, and the deterministic path is what decides a figure when no
LLM is configured.

THERE IS NO PER-CONCEPT ACCEPT BAR, and its removal was a decision rather than an oversight.
`LineItemDef` used to carry `min_confidence_to_auto_accept: float = 0.85`, and it was read by
nothing — not here and NOT BY THE INCUMBENT, which gates acceptance on the global knob
`settings.extraction.auto_accept_confidence` (0.80) at mapping:1682, :1863, :1919 and :2219. It
carried 0.85 on all 462 projected definitions and 0 of 475 overrode it.

Enforcing it instead of deleting it would have invented a policy nobody authored, and a costly
one: the per-item default was STRICTER than the live global bar, so switching it on would newly
route to review every row scoring between 0.80 and 0.85. The global knob is the one bar. Re-add a
per-item bar only together with the code that reads it and the policy that justifies it.

NORMALISATION IS IMPORTED, NEVER REIMPLEMENTED. `normalize_label`, `label_segments`,
`section_of_banner` and `normalize_statement` all come from `services.mapping`. Two copies of a
nine-step normalisation is how the two models diverge in the first place, and the whole point of
this exercise was to stop having two answers to one question. `scripts/parity_line_items.py`
holds the port to that standard: every caption in the rulebook, through both engines, same answer.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.schemas.line_items import LineItemDef, LineItemSet, MappingVocabulary
# The FALLBACKS, for a set that declares no `vocabulary` block. Imported rather than copied so an
# undeclared vocabulary and the incumbent are the same objects, not two versions of one list.
from app.services.mapping import (
    _COMPACT_SECTION_TOKENS,
    _STATEMENT_OF_PREFIX,
    _STATEMENT_SPELLINGS,
    EXCLUSIVE_VOCABULARIES,
    HEADING_ROW_SECTIONS,
    SECTION_WORDS,
    MappingMethod,
    label_segments,
    normalize_label,
)

class Vocabulary:
    """The mapping vocabularies, READ FROM THE SET rather than from Python constants.

    THIS IS THE POINT OF THE CLASS. Every question it answers — what section a printed banner
    names, which statement a key's namespace implies, whether a caption names an exclusive class —
    decides which line item a caption resolves to. All of it lived in `services.mapping` as module
    constants that no configuration could reach, so someone could read all 475 definitions and
    still not be able to say why a row landed where it did, or change it without a release.

    EMPTY FALLS BACK TO THE BUILT-IN. A set that declares no `vocabulary` block must behave
    exactly as it did before the block existed — otherwise adding these fields to the schema would
    silently change the answers of every set already written. So each accessor prefers the
    declaration and drops through to the shipped constant when the declaration is absent, and the
    fallbacks are the SAME objects `mapping` uses rather than copies of them.
    """

    def __init__(self, vocab: MappingVocabulary | None):
        self._v = vocab or MappingVocabulary()

        # Banner order is load-bearing — longest heading first, so "non current liabilities" is
        # never read as "current liabilities". The declaration preserves list order; the fallback
        # is the shipped tuple, which is already ordered.
        self._banners: tuple[tuple[str, tuple[str, ...]], ...] = (
            tuple((b.token, tuple(b.headings)) for b in self._v.section_banners)
            or SECTION_WORDS)
        self._umbrella = tuple(self._v.umbrella_banners)
        self._heading_rows = frozenset(
            b.token for b in self._v.section_banners if b.heading_row) or HEADING_ROW_SECTIONS
        self._scope_tokens = dict(self._v.scope_tokens) or dict(_COMPACT_SECTION_TOKENS)
        self._prefixes = dict(self._v.statement_prefixes) or dict(_STATEMENT_OF_PREFIX)
        self._spellings = dict(self._v.statement_spellings) or dict(_STATEMENT_SPELLINGS)
        self._vocabs: tuple[tuple[str, ...], ...] = (
            tuple(tuple(x.members) for x in self._v.exclusive_vocabularies)
            or EXCLUSIVE_VOCABULARIES)
        self._words = {w: re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE)
                       for vocab in self._vocabs for w in vocab}

    # ── statements ───────────────────────────────────────────────────────────────────────────

    @property
    def statements(self) -> frozenset[str]:
        """The statements the gate narrows by — the FOUR the key namespace encodes.

        Not the seven `StatementType` declares: `notes`, `statement_setup` and
        `covenants_supplemental` are places a caption can be printed, not statements to refuse a
        line item for belonging elsewhere. Restating this as "all seven" refused
        `bs_ca__total_assets` under a statement-setup banner and `bs_nca__options_nca` in the
        notes — 3 of the 133 parity disagreements, and the last 3 to go.
        """
        return frozenset(self._prefixes.values())

    def normalize_statement(self, statement) -> str:
        """One spelling per statement, whichever vocabulary it arrived in.

        The classifier says `changes_in_equity`; `StatementType` spells it `equity_changes`. A
        declaration the gate cannot compare to the classifier's verdict refuses every line item in
        that statement, on every page of it.
        """
        if statement is None:
            return ""
        raw = getattr(statement, "value", statement)
        text = str(raw).strip().lower()
        return self._spellings.get(text, text)

    def statement_of_key(self, key: str) -> str | None:
        """The statement a key's namespace names — the fallback for a line item declaring none."""
        return self._prefixes.get((key or "").split("_", 1)[0])

    # ── sections ─────────────────────────────────────────────────────────────────────────────

    def section_of_banner(self, text: str | None) -> str | None:
        """The section a printed banner names, or None when it names none, or spans several."""
        if not text:
            return None
        folded = normalize_label(text)
        for umbrella in self._umbrella:
            if umbrella.spans_sections(folded):
                return None
        if not self._umbrella and (("equity" in folded or "权益" in folded)
                                   and ("liabilit" in folded or "负债" in folded)):
            return None                  # the built-in umbrella rule, for a set declaring none
        for token, headings in self._banners:
            if any(h in folded for h in headings):
                return token
        return None

    def token_of_scope(self, scope_id: str) -> str | None:
        """The banner token a `section_scope` id names, or None when it names no section.

        A scope id carries the section's printed POSITION as well as its name
        ("bs_s4_non_current_liabilities") while a banner names the section itself, so the token is
        read off the END of an id. Longest-first for the same reason the banners are:
        "bs_s1_non_current_assets" also ends with "current_assets".

        `*_top_level` ids name no section and return None — those are the statement-level totals,
        which no banner may constrain, because a section hint is the nearest PRECEDING banner and a
        statement total routinely carries the banner of the last section printed above it.
        """
        if (compact := self._scope_tokens.get(scope_id)):
            return compact
        return next((token for token, _ in self._banners if scope_id.endswith(token)), None)

    def is_heading_row_section(self, token: str | None) -> bool:
        """Whether a row carrying only this heading, with no figures, may declare the section."""
        return bool(token) and token in self._heading_rows

    # ── exclusive classes ────────────────────────────────────────────────────────────────────

    def names_a_different_class(self, key: str, caption: str,
                                sections: frozenset[str] | None = None) -> bool:
        """Whether the caption names a member of an exclusive vocabulary the line item is not in.

        Which member the LINE ITEM is in is read from its resolved sections when the caller has
        them, and off the key otherwise. Only refuses when the caption names exactly ONE member
        and it is not the line item's own: a caption naming two ("cash flows from operating and
        investing activities") is a genuine combined line and is left to the ordinary tiers.
        """
        key_text = (key or "").lower()
        declared = " ".join(sorted(sections)).lower() if sections else ""
        for vocab in self._vocabs:
            in_scope = [w for w in vocab if w in declared]
            in_key = in_scope or [w for w in vocab if w in key_text]
            if len(in_key) != 1:
                continue                 # not in this vocabulary, or ambiguous
            in_caption = [w for w in vocab if self._words[w].search(caption)]
            if len(in_caption) == 1 and in_caption[0] != in_key[0]:
                return True
        return False


@dataclass
class LineItemMatch:
    """One caption's resolution, and enough to explain or audit it."""

    key: str | None
    method: str
    confidence: float
    needs_review: bool = False
    # Every key that survived the gate and claimed this caption, when more than one did. Populated
    # for a forbidden tie so the review item can list them, empty otherwise.
    tied: list[str] = field(default_factory=list)
    # Why nothing matched, or why this one did — one short phrase, for the run log.
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.key is not None

    def as_mapping_result(self) -> "_AppliedResult":
        """This result in the shape `stages.map_ontology._apply` reads.

        AN ADAPTER RATHER THAN A SHARED TYPE, deliberately. `_apply` is the one function both
        engines write rows through, and it reads seven attributes off whatever it is handed —
        `canonical_key`, `confidence`, `method`, `allocation_status`, `reason`, `rerouted_from`,
        `needs_review`. Renaming this class's fields to match would couple the ported matcher's
        vocabulary to the incumbent's; widening `_apply` to accept two shapes would put an
        isinstance branch in the one place every row passes through. So the translation lives here,
        where the difference is, and `_apply` stays unaware there are two engines.

        THREE FIELDS ARE HONESTLY EMPTY. `rerouted_from` needs the concept-family re-routing, and
        this rulebook declares no families at all (all nine lifted from `mapping.CONCEPT_FAMILIES`
        name keys it does not define). `computed_claim` needs the refusal that stops a caption
        naming a COMPUTED concept being re-homed onto a neighbour — the `extraction_mode: derive`
        locks keep such a concept out of every tier here, so no caption reaches it, but the
        positive claim the incumbent records is not reproduced. `allocation_status` is set only for
        an exact hit, which is the one case its meaning is unambiguous.
        """
        return _AppliedResult(
            canonical_key=self.key,
            confidence=self.confidence,
            method=self.method,
            needs_review=self.needs_review,
            reason=self.reason,
            allocation_status=("direct_exclusive"
                               if self.method is MappingMethod.EXACT and self.key else None),
        )


@dataclass
class _AppliedResult:
    """What `map_ontology._apply` expects. See `LineItemMatch.as_mapping_result`."""

    canonical_key: str | None
    confidence: float
    method: object
    needs_review: bool = False
    reason: str = ""
    allocation_status: str | None = None
    # Not produced by the deterministic port — see the adapter's docstring for why each is empty
    # rather than fabricated.
    rerouted_from: str | None = None
    computed_claim: str | None = None


class LineItemMatcher:
    """The deterministic tiers, over a `LineItemSet`.

    Built from the RESOLVED set: the gate is what this class is for, and 462 of 462 definitions
    declare `statement` on none of themselves and inherit all of it. Handed an unresolved set,
    every gate check would pass and the matcher would be a plain alias lookup.
    """

    def __init__(self, line_items: LineItemSet):
        self.set = line_items
        # Every vocabulary the gate consults, read off the set. Nothing below reaches for a
        # module constant, which is what makes the config the authority rather than a description.
        self.vocab = Vocabulary(line_items.vocabulary)
        self.by_key: dict[str, LineItemDef] = {d.key: d for d in line_items.items}

        # DECLARATION ORDER IS RECORDED, because it is the last tie-break and both engines use it.
        # For a caption whose claimants tie on every principled field, `OntologyMatcher` answers by
        # whichever `_alias_index` saw first, which is file order; keeping this index
        # insertion-ordered reproduces that exactly instead of leaving it to dict iteration luck.
        #
        # NO SHIPPED CAPTION IS IN THAT POSITION TODAY — the last one, `presented in`, was denied
        # on both claimants when `tests/test_configuration_invariants._UNBREAKABLE_TIE_CEILING`
        # reached 0. Recorded anyway: the fallback is in both engines, so the two have to agree on
        # it whether or not the current configuration exercises it, and a configurator can author
        # a tied pair at any time.
        self._order: dict[str, int] = {d.key: i for i, d in enumerate(line_items.items)}

        # Unreachable by MATCHING, for three different declared reasons.
        #
        #   alias_matching == "disabled" — the section "Others" buckets, populated by the residual
        #   sweep alone. A residual's caption is the most attractive one in the rulebook: "Others"
        #   matches almost anything short, and a figure landing there is a figure in the bucket
        #   that is supposed to be the section's UNEXPLAINED remainder, so the reconciliation that
        #   would have reported the gap instead ties.
        #
        #   extraction_mode == "derive" — the framework computes it and a filing does not print
        #   it. Offering it as a candidate asserts a row on the page IS the derived subtotal, which
        #   then overwrites the computation with whatever caption happened to match.
        #
        #   type == "derived" — A DERIVED PARENT'S FIGURE IS ITS CASCADE'S AND NOTHING ELSE'S: not
        #   the model's, not an alias's, not a semantic probe's. Settled by instruction and not a
        #   trade-off to be reopened. THIS SET IS THE ONE THAT MATTERS for that, and the mirror of
        #   it in `mapping.OntologyMatcher` is not enough on its own: this class owns the alias
        #   index, the regex tier and the description probe, so a key absent only from the other
        #   set still binds here. Measured: with the rule in `mapping` alone, "TURNOVER" still
        #   resolved to `is_pl__sales_revenues`.
        #
        #   The nine still DECLARE aliases, regex hints and a competitive `match_priority` — read
        #   those as unused history rather than as a signal that matching was intended.
        self._unmatchable: set[str] = {
            d.key for d in line_items.items
            # `alias_matching == "disabled"` WAS THE FIRST CLAUSE AND IS GONE. It locked the
            # section residuals out of every tier, and the others master removes the field — the
            # framework's prohibition 2 ("never populated by alias, regex or embedding match") is
            # implemented in `stages.residual` as a RELEASE, so a row a matcher claims for a bucket
            # is handed back and swept instead. The buckets stay out of the LLM's reach through
            # `_never_asked`, which reads the marker rather than the lock.
            if d.extraction_mode == "derive"
            or str(getattr(d, "type", "") or "") == "derived"
        }

        # alias -> claimants, insertion-ordered, every locale folded in. `aliases_for(None)` is the
        # default list plus the English anchor; the per-locale lists are added explicitly so a Han
        # alias is reachable without the caller knowing which locale a page was printed in.
        self._alias_index: dict[str, list[str]] = defaultdict(list)
        self._label_index: dict[str, list[str]] = defaultdict(list)
        for d in line_items.items:
            names = [d.label, *d.aliases]
            for locale_aliases in d.aliases_i18n.values():
                names.extend(locale_aliases)
            for raw in names:
                norm = normalize_label(raw or "")
                if norm and d.key not in self._alias_index[norm]:
                    self._alias_index[norm].append(d.key)
            label_norm = normalize_label(d.label or "")
            if label_norm and d.key not in self._label_index[label_norm]:
                self._label_index[label_norm].append(d.key)

    # ── the gate ─────────────────────────────────────────────────────────────────────────────

    def _priority_of(self, key: str) -> int:
        """Declared `match_priority`, or 0 when none is declared.

        0 rather than a mid-scale guess: a set that declares no priority anywhere must degenerate
        to exactly the ordering it had before priorities existed.
        """
        d = self.by_key.get(key)
        return d.match_priority if (d is not None and d.match_priority is not None) else 0


    def _vetoed(self, key: str, caption: str) -> bool:
        """Whether this line item's `exclude_hints` rule the caption out.

        AN EXCLUSION OUTRANKS THE LINE ITEM'S OWN ALIAS, and it has to: the point of the field is
        that someone looking at a mis-mapping can add one line and have it stop. Matched
        case-insensitively against the RAW caption — `re.IGNORECASE` rather than lowercasing the
        pattern, because lowercasing would silently invert `\\S`, `\\B`, `\\W` and `\\D`.
        """
        d = self.by_key.get(key)
        if d is None or not d.exclude_hints:
            return False
        return any(re.search(ex, caption, re.IGNORECASE) for ex in d.exclude_hints)

    def _allowed(self, key: str, statement: str | None, section: str | None,
                 caption: str) -> bool:
        """The conjunction, in ONE place so no call site can apply only part of the scoping.

        That single-place rule is why `mapping._allowed` exists and it is repeated here for the
        same reason: the gate is handed INTO the alias lookup as a predicate rather than applied to
        its output, because when two line items claim one alias the winner has to be the one that
        fits where the caption was printed.
        """
        d = self.by_key.get(key)
        if d is None:
            return False
        if key in self._unmatchable:
            return False
        want = self.vocab.normalize_statement(statement)
        # Both sides folded to one spelling — `equity_changes` and `changes_in_equity` are the
        # same statement under two names, and a raw compare refuses every definition on it.
        if want in self.vocab.statements and not d.claimable_on(
                want, normalize=self.vocab.normalize_statement):
            return False
        banner = self.vocab.section_of_banner(section)
        # The resolver is what makes this the incumbent's gate rather than a string compare: a
        # scope id carries printed position as well as name, and `*_top_level` names no section, so
        # the statement-level totals must stay claimable under every banner in their statement.
        if banner and not d.claimable_under(banner, resolve=self.vocab.token_of_scope):
            return False
        if caption and self._names_a_different_class(d, caption, banner):
            return False
        return not self._vetoed(key, caption)

    def _names_a_different_class(self, d: LineItemDef, caption: str,
                                 banner: str | None) -> bool:
        """The fourth gate: the caption names an exclusive class this line item is not in.

        Ported because `mapping._allowed` is a conjunction of FOUR constraints and applying three
        is not applying the gate. "Net cash used in investing activities" and "Net cash flows used
        in financing activities" differ by one word in seven, which similarity scores at 0.92, and
        the consequence is silent: the financing figure filed under investing, investing showing
        two figures summed, financing empty.
        """
        tokens = frozenset(t for s in d.section_scope
                           if (t := self.vocab.token_of_scope(s)))
        return self.vocab.names_a_different_class(d.key, caption, tokens)

    def mappable_keys(self, statement: str | None = None, section: str | None = None,
                      caption: str = "") -> list[str]:
        """The restricted candidate set — `binding.order` step 3, applied BEFORE any tier runs.

        Restricting first rather than filtering each tier's output reaches the same winner but a
        different shortlist, and the shortlist is what a semantic tier is shown.
        """
        return [d.key for d in self.set.items
                if self._allowed(d.key, statement, section, caption)]

    # ── tie handling ─────────────────────────────────────────────────────────────────────────

    def _prefer_label_owners(self, norm: str, keys: list[str]) -> list[str]:
        """Prefer line items whose own label IS the caption being matched.

        A line item may carry another's full label as an over-broad alias. Priority is useful
        between aliases of differing specificity, but it must never let a borrowed alias beat the
        line item the filing actually named.
        """
        owners = set(self._label_index.get(norm) or [])
        exact = [k for k in keys if k in owners]
        return exact or keys

    # ── the tiers ────────────────────────────────────────────────────────────────────────────

    def _exact(self, norm: str, statement: str | None, section: str | None,
               caption: str) -> str | None:
        """An exact normalised-alias hit that the gate allows.

        Order, and it is the rulebook's: gate, then label ownership, then highest priority, then
        declaration order. `max` over an insertion-ordered list keeps the first declared on a full
        tie, which is what the other engine does.
        """
        keys = [k for k in (self._alias_index.get(norm) or [])
                if self._allowed(k, statement, section, caption)]
        if not keys:
            return None
        choices = self._prefer_label_owners(norm, keys)
        return max(choices, key=lambda k: (self._priority_of(k), -self._order.get(k, 0)))

    def _rule(self, raw: str, allowed: set[str]) -> tuple[str | None, float]:
        """regex_hints, then keyword_hints, in descending priority, with exclude_hints as a veto.

        Run on BOTH spellings of the caption: as printed (lowercased) and normalised the way the
        alias tier normalises it. Authored hints are anchored far more often than not, and an
        anchored hint never fires on a real caption — `^net cash.*investing activities$` is
        defeated by "Net cash flows from/(used in) investing activities 投資活動…". The raw
        spelling is kept because a hint may deliberately target punctuation normalisation removes.
        """
        text = raw.lower()
        norm = normalize_label(raw)
        spellings = (text, norm) if norm and norm != text else (text,)
        hits: list[str] = []
        for d in self.set.items:
            if d.key in self._unmatchable or d.key not in allowed:
                continue
            if any(re.search(ex, t, re.IGNORECASE)
                   for ex in d.exclude_hints for t in spellings):
                continue
            if any(re.search(rx, t, re.IGNORECASE) for rx in d.regex_hints for t in spellings):
                hits.append(d.key)
            elif d.keyword_hints and any(all(kw.lower() in t for kw in d.keyword_hints)
                                         for t in spellings):
                hits.append(d.key)
        if not hits:
            return None, 0.0
        # Several hits stay ambiguous — below every accept threshold — but the key reported is the
        # highest-priority claimant, since that is what a shortlist carries forward.
        best = max(hits, key=lambda k: (self._priority_of(k), -self._order.get(k, 0)))
        return best, (0.95 if len(hits) == 1 else 0.6)

    def match(self, caption: str, statement: str | None = None,
              section: str | None = None) -> LineItemMatch:
        """Resolve one printed caption deterministically.

        `statement` is what the page classifier decided; `section` is the banner the row sat under.
        Both may be None and both being None is not an error — it simply leaves the gate with
        nothing to narrow by, which is the situation a caller with an unclassified page has.

        Tried on the whole caption and on each script's half: either alone can be an exact alias,
        so "REVENUE 收益" resolves the way the monolingual "Revenue" would.
        """
        segments = label_segments(caption)

        for seg in segments:
            seg_norm = normalize_label(seg)
            if not seg_norm:
                continue
            # THE FORBIDDEN-TIE STEP IS GONE WITH `confusable_with`. It refused a
            # mutually-declared pair at equal priority and sent both keys to review. Measured, it
            # fired on 8 of the set's 238 equal-priority alias collisions — 230 already resolved by
            # file order — and on those 8 file order was wrong, so each is now settled by an
            # `exclude_hints` entry on the line the caption is NOT. See the tombstone in
            # `schemas/line_items.py`.
            hit = self._exact(seg_norm, statement, section, caption)
            if hit:
                return LineItemMatch(
                    hit, MappingMethod.EXACT, 1.0, reason="exact alias")

        allowed = set(self.mappable_keys(statement, section, caption))
        key, score = self._rule(caption, allowed)
        if key:
            return LineItemMatch(key, MappingMethod.RULE, score,
                                 needs_review=score < 0.95,
                                 reason="rule hint" if score >= 0.95
                                        else "several rule hints fired; ambiguous")

        return LineItemMatch(None, MappingMethod.UNMATCHED, 0.0, needs_review=True,
                             reason="no alias and no rule hint the gate allows")
