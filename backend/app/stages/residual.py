"""Residual routing — a printed face line never disappears, on the terms the rulebook sets.

A statement is a complete document: every line the filing prints on the face of the balance
sheet, income statement or cash flow statement contributes to that section's subtotal, and the
subtotal to the statement's total. So an extracted face line that matched no specific concept
cannot simply be dropped — doing that silently removes real money from the statement and the
subtotals stop tying. It belongs in its own section's residual bucket ("Others"), which is what
that bucket is for.

Placing a line in the right section is the whole problem, and the statement's own structure
answers it: **a section runs up to its subtotal**. "Trade and bills payables" through to "Total
current liabilities" are the current liabilities; whatever is printed between the previous
subtotal and the next one belongs to the next one's section. That works where banners do not —
the income statement prints no section banners at all, and a cash flow continuation page often
shows only a truncated running header.

Three signals are used, strongest first:

1. the section banner above the row, when it names a section we know (``section_hint``);
2. the next section subtotal at a higher ordinal — the accounting structure above;
3. the section of the nearest preceding mapped line, for a trailing row after the last subtotal.

WHO DECIDES THE TERMS. With a ``schema_version: 2`` rulebook loaded, none of the sweep's terms are
this module's opinion: ``residual_framework`` states when the sweep runs, which rows it may take,
what a component must record, the identity the section must then satisfy, and the six conditions
that send a section to review — and each of those is read here (:func:`_read_terms`,
:class:`_Residual`, :func:`_reconcile`). A per-concept ``residual_policy`` overrides the framework
for one residual (``population``, ``cross_section``, ``notes_as_source``, ``plug``, ``itemise``),
and ``never_sweep`` names what that residual must never absorb however similar the wording. Editing
any of those in the ontology changes what this stage does; that is the point of authoring them.

Two terms are declarations the engine refuses rather than implements. ``plug_behaviour: forbidden``
and ``derivation: forbidden`` say a residual is never computed as (reported subtotal − mapped
children): a plug hides a missed row inside a plausible number, while a sum of components leaves the
loss visible as an ``unallocated_gap`` the reconciliation reports. So a residual whose own policy
asks to be plugged is a contradiction in the rulebook, and it is reported as one instead of being
honoured.

THE FIVE PROHIBITIONS are read the same way (:func:`_read_prohibitions`), and each one switches on
the guard named beside it in ``_PROHIBITIONS``: delete the sentence from the rulebook and the guard
goes with it. They are not restatements of the sweep's per-row terms — every one of them catches a
residual holding a figure the sweep never took, which is the failure a section subtotal cannot show.

WHAT IS PROSE HERE, and deliberately left as prose. ``sweep.candidate_set``, ``itemisation.rule``
and ``reconciliation.identity`` each state in one sentence what several read terms already do, so
driving the engine from the sentence would only add a second, weaker copy of a live switch:

* ``candidate_set`` ("every printed value row on the FACE … whose resolved section equals the
  residual's single section_scope value") is the population built from ``population``,
  ``eligibility`` 1-4 and ``notes_as_source``, all read below. Its one independent clause — a
  SINGLE section_scope — is enforced, under prohibition 5, because a residual scoped to two
  sections has no candidate set at all.
* ``itemisation.rule`` is ``aggregation: sum_of_components`` (read) plus the promise that a swept
  row keeps its own label and sign, which :func:`_component` does by construction.
* ``reconciliation.identity`` is the equation :func:`_reconcile` evaluates through
  ``rollups.reconcile_section``; the terms that can vary — ``tolerance``, ``on_failure``,
  ``sections_without_reported_subtotal`` — are all read.

Those three sentences are held by ``tests/test_residual_framework.py``:
``test_the_reconciliation_identity_and_the_candidate_set_are_performed_as_written`` is the reference
they point at, alongside the itemisation and aggregation tests beside it.

Rows deliberately NOT routed: anything that is itself a subtotal or total (it would be counted
twice), a section header, an attribution caption, a per-share figure, a narrative sentence, a
note-reference-only row, and rows with no value at all — the eligibility list the framework prints,
each entry with its own test here. A row whose SECTION cannot be resolved is not swept either: it
goes to review, because guessing a section is exactly the cross-section rescue the block forbids.

Whether the routing is *correct* is not a matter of opinion: once residuals are in place, each
section subtotal must equal the sum of its children. The framework's reconciliation identity checks
that here, per column, and the structural stage checks the template's rollups again later — so a
mistake shows up as a reported gap rather than as a plausible-looking number.

Without a v2 rulebook (a v1 ontology, or none at all) the template-driven routing below is used
unchanged: the template's ``__others`` keys, the same three signals, no itemisation to record.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.models import DocumentModel
from app.core.models.enums import LineRole
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.mapping import (
    normalize_label,
    section_of_banner,
    section_of_key,
    section_token_of_scope,
)
from app.services.rollups import reconcile_section, section_members

# How a residual assignment is recorded on the line, so the review queue and the statement
# inspector can both say the figure was combined rather than identified.
FALLBACK_ALLOC = "fallback_combined"


def _sections_from_template(template_def: dict) -> dict[str, list[tuple[str, str, str]]]:
    """Per statement type, the sections in printed order as (section_token, subtotal_key,
    others_key). A section with no residual bucket is skipped — there is nowhere to put a line.
    """
    out: dict[str, list[tuple[str, str, str]]] = {}
    for st in (template_def or {}).get("statements", []):
        rows: list[tuple[str, str, str]] = []
        for sec in st.get("sections", []):
            kids = [c for c in sec.get("children", []) if c.get("canonical_key")]
            if not kids:
                continue
            others = next((c["canonical_key"] for c in kids
                           if c["canonical_key"].endswith("__others")), None)
            subtotal = next((c["canonical_key"] for c in kids
                             if c.get("role") in ("subtotal", "total")), None)
            if not others:
                continue
            # The token between the statement prefix and "__" is the section namespace
            # ("bs_current_liabilities__others" -> "current_liabilities").
            token = others.split("__", 1)[0].split("_", 1)[1]
            rows.append((token, subtotal or "", others))
        if rows:
            out[st.get("type", "")] = rows
    return out


def _has_value(li) -> bool:
    return any(ev.value is not None or ev.value_raw is not None for ev in li.values.values())


def _statement_map(doc: DocumentModel):
    """(page -> statement) plus the per-row lookup both paths use."""
    stmt_by_page = {p.index: p.statement for p in doc.pages if p.statement}

    def statement_of(li) -> str | None:
        for ev in li.values.values():
            if ev.provenance is not None:
                return stmt_by_page.get(ev.provenance.page_index)
        return None

    return stmt_by_page, statement_of


# --- eligibility vocabulary --------------------------------------------------------------------
# Rows that carry a figure and still are not a component of their section. Hardcoded like
# ``mapping.SECTION_WORDS``, and for the same reason: this is a property of how statements are
# printed, not of one rulebook. Each test is only REACHED while the framework's eligibility list
# still names it, so the rulebook decides which of them apply (see `_read_terms`).
_ATTRIBUTION = re.compile(
    r"attributable to|owners of the (parent|company)|non-?controlling interests?:"
    r"|归属于|歸屬於|母公司拥有人|母公司擁有人|本公司拥有人|本公司擁有人", re.IGNORECASE)
# A per-share figure is a ratio in cents, not an amount: added into a section subtotal it is
# nonsense, and it is small enough that no rollup notices.
_PER_SHARE = re.compile(r"per share|per ordinary share|每股|hk cents|rmb cents", re.IGNORECASE)
# "Note 12", "附註12", "(a)", "" — a row whose caption identifies nothing at all.
_NOTE_REF_ONLY = re.compile(r"^\W*(?:note|notes|附注|附註)?\s*[\d.]*\s*[\w.()]{0,3}\W*$",
                            re.IGNORECASE)
# The bare sub-captions of a per-share block. An HKEX income statement prints
#
#     LOSS PER SHARE
#        Basic                          (1.23)
#        Diluted                        (1.23)
#
# so the only row whose CAPTION says "per share" is the heading, which carries no value and is
# dropped as a header anyway. The two rows that carry the figures are captioned "Basic" and
# "Diluted" — matched by nothing, swept into whatever section was nearest, and a per-share figure in
# cents added into the tax charge moves profit for the year by a rounding error nobody can find. This
# is what put "loss per share" inside Total tax expense on a real filing.
#
# Matched only UNDER a per-share heading (see ``_per_share_rows``), never on the caption alone:
# "Basic" is too generic a word to veto a row on by itself.
# Bilingual, because an HKEX statement prints both halves on one row: the real filing this was
# found on reads "– Basic －基本" and "– Diluted －攤薄". Matching only the English left both rows
# unrecognised and swept, which is the whole defect. Either language alone also matches, so a
# Chinese-only or English-only filing is covered by the same pattern.
_PER_SHARE_WORD = r"(?:basic|diluted|基本|攤薄|摊薄|稀釋|稀释)"
_PER_SHARE_SUB = re.compile(
    rf"^\W*{_PER_SHARE_WORD}(?:\s*(?:and|/|,|&|－|-|—)?\s*{_PER_SHARE_WORD})*"
    rf"\s*(?:\([^)]*\)|（[^）]*）)?\W*$",
    re.IGNORECASE)


def _per_share_rows(ordered: list) -> set:
    """Row ids inside a per-share block: the heading, and the sub-captions beneath it.

    The block ends at the first row that is neither — a per-share heading scopes its own
    "Basic"/"Diluted" pair and nothing further down the page.
    """
    out: set = set()
    in_block = False
    for row in ordered:
        label = _label(row)
        if _PER_SHARE.search(label):
            in_block = True
            out.add(row.id)
            continue
        if in_block and _PER_SHARE_SUB.match(label):
            out.add(row.id)
            continue
        if label:
            in_block = False
    return out

# Prose arrives as a row because it carries a figure. Routed into a section it moves that
# section's subtotal by whatever the sentence happened to contain.
_NARRATIVE = re.compile(r"comprise|as follows|the following|include[sd]? in|如下|包括以下",
                        re.IGNORECASE)
_MAX_CAPTION_WORDS = 12
_MAX_CAPTION_HAN = 30


def _label(row) -> str:
    """The caption of a face row or of a note row — the eligibility tests apply to both."""
    return getattr(row, "source_label", None) or getattr(row, "raw_label", "") or ""


def _is_narrative(label: str) -> bool:
    words = [w for w in re.split(r"\s+", label.strip()) if w]
    han = len(re.findall(r"[㐀-鿿]", label))
    return (len(words) > _MAX_CAPTION_WORDS or han > _MAX_CAPTION_HAN
            or bool(_NARRATIVE.search(label)))


# Phrase in the framework's eligibility list -> the test it switches on. The row is the argument;
# ``role`` and the caption are all that is needed.
_PER_SHARE_PHRASE = "per-share figure"
_EXCLUSIONS: tuple[tuple[str, object], ...] = (
    ("section subtotal", lambda row: row.role is LineRole.SUBTOTAL),
    ("statement total", lambda row: row.role is LineRole.TOTAL),
    ("section header", lambda row: row.role in (LineRole.HEADER, LineRole.SPACER)),
    ("attribution caption", lambda row: bool(_ATTRIBUTION.search(_label(row)))),
    (_PER_SHARE_PHRASE, lambda row: bool(_PER_SHARE.search(_label(row)))),
    ("narrative row", lambda row: _is_narrative(_label(row))),
    ("note-reference-only row", lambda row: bool(_NOTE_REF_ONLY.match(_label(row)))),
)

# Entry 5 of the framework's eligibility list, which needs the TARGET section and so cannot be a
# row-only test like the ones above: "the row's sign agrees with the section's sign_convention,
# where the section states one". Read the same way — delete the sentence and the veto goes.
_SIGN_AGREES = "sign agrees with the section"


# Phrase in the framework's ``prohibitions`` list -> the guard it switches on. Read the same way
# the review triggers are, and for the same reason: a prohibition the block stops stating stops
# being enforced, so the list is what a reviewer edits rather than a paragraph beside the code.
# Each needle is unique to its sentence, so no prohibition can switch on another one's guard.
_PROHIBITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # "Never a balancing plug." — audited on the finished residual (`_audit_prohibitions`).
    ("never_plug", ("plug",)),
    # "Never populated by alias, regex or embedding match."
    ("never_matched", ("alias",)),
    # "Never carries a value with no itemised components."
    ("never_unitemised", ("itemised component",)),
    # "Never absorbs a row a dedicated concept could have claimed but was not tested for."
    ("never_untested", ("dedicated concept",)),
    # "Never spans sections, statements, periods, entity scopes or units."
    ("never_spans", ("spans",)),
)

# The matchers prohibition 2 names. ``llm`` is deliberately not one of them: a model routing a
# leftover row into a section's Others is a judgement about the SECTION, not an alias match, and it
# is the plug audit that holds it to account for the figure it put there.
_MATCHED_METHODS = frozenset({"exact", "rule", "fuzzy", "embedding"})


def _read_prohibitions(lines) -> frozenset[str]:
    """The prohibitions the block states, as the ids of the guards they switch on."""
    out: set[str] = set()
    for raw in lines or []:
        text = str(raw).lower()
        out.update(name for name, needles in _PROHIBITIONS if all(n in text for n in needles))
    return frozenset(out)


@dataclass
class _Terms:
    """``residual_framework`` reduced to the switches the sweep runs on.

    Everything here is READ from the block. A term the block stops declaring stops applying — an
    eligibility phrase deleted from the list makes those rows eligible, a review trigger deleted
    stops firing — which is the only way the block can be the place a reviewer looks up what the
    pipeline does.
    """

    population: str = "sweep_only"
    after_dedicated: bool = True
    require_value: bool = True
    require_unclaimed: bool = True
    inside_section_only: bool = True
    exclusions: tuple[str, ...] = ()
    # Eligibility 5: the row's sign must agree with the section it would be swept into.
    sign_must_agree: bool = False
    cross_section: bool = False
    notes_as_source: bool = False
    plug_forbidden: bool = True
    derivation_forbidden: bool = True
    literal_others: tuple[str, ...] = ()
    itemise_required: bool = True
    component_fields: tuple[str, ...] = ()
    aggregation: str = "sum_of_components"
    rounding_unit: float = 1.0
    per_row_tolerance: bool = True
    gap_fact: str = ""
    gap_to_review: bool = False
    no_subtotal_unreconciled: bool = False
    triggers: dict[str, float] = field(default_factory=dict)
    prohibitions: frozenset[str] = frozenset()


def _number_in(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _read_triggers(lines) -> dict[str, float]:
    """The declared review triggers, as ids with the threshold their sentence quotes.

    The thresholds are parsed out rather than duplicated in code: "5% of the subtotal" is the
    rulebook's number, and a reviewer who edits it to 3% has to see the engine fire at 3%.
    """
    out: dict[str, float] = {}
    for raw in lines or []:
        text = str(raw).lower()
        if "unallocated_gap" in text:
            out["unallocated_gap"] = 0.0
        elif "exclude_hints" in text:
            out["vetoed_dedicated_match"] = 0.0
        elif "sign" in text and "opposite" in text:
            out["sign_opposite_to_section"] = 0.0
        elif "component count" in text:
            out["component_count"] = _number_in(text) or 6.0
        elif "component" in text and "%" in text:
            out["component_share_of_subtotal"] = _number_in(text) or 2.0
        elif "residual" in text and "%" in text:
            out["residual_share_of_subtotal"] = _number_in(text) or 5.0
    return out


def _read_terms(fw) -> _Terms:
    sweep = fw.sweep
    elig = " ".join(sweep.eligibility or []).lower()
    recon = fw.reconciliation
    tol = (recon.tolerance or "").lower()
    on_failure = (recon.on_failure or "").lower()
    no_subtotal = (recon.sections_without_reported_subtotal or "").lower()
    return _Terms(
        population=fw.population or "sweep_only",
        after_dedicated="after all dedicated" in (sweep.runs or "").lower(),
        require_value="printed a value" in elig,
        require_unclaimed="no dedicated concept" in elig,
        # Eligibility 4, which is about the row's SECTION rather than its role: a row printed
        # inside another section is ineligible however similar its wording. Read separately from
        # ``cross_section`` because the two forbid different things — see `_sweep`.
        inside_section_only="printed inside this section" in elig,
        exclusions=tuple(phrase for phrase, _ in _EXCLUSIONS if phrase in elig),
        sign_must_agree=_SIGN_AGREES in elig,
        cross_section=bool(sweep.cross_section),
        notes_as_source=bool(sweep.notes_as_source),
        plug_forbidden="forbidden" in (sweep.plug_behaviour or "").lower(),
        derivation_forbidden="forbidden" in (sweep.derivation or "").lower(),
        literal_others=tuple(re.findall(r"'([^']+)'", sweep.literal_others_caption or "")),
        itemise_required=bool(fw.itemisation.required),
        component_fields=tuple(fw.itemisation.component_fields or []),
        aggregation=fw.itemisation.aggregation or "sum_of_components",
        rounding_unit=_number_in(tol) or 1.0,
        per_row_tolerance="per contributing row" in tol,
        gap_fact="unallocated_gap" if "unallocated_gap" in on_failure else "",
        gap_to_review="review" in on_failure,
        no_subtotal_unreconciled="unreconciled" in no_subtotal,
        triggers=_read_triggers(fw.review_triggers),
        prohibitions=_read_prohibitions(fw.prohibitions),
    )


@dataclass
class _Residual:
    """One residual concept and the terms it is swept under."""

    key: str
    section: str
    token: str | None
    statement: str | None
    population: str = "sweep_only"
    cross_section: bool = False
    itemise: bool = True
    may_source_from_note: bool = False
    section_sign: str | None = None
    # False where the rulebook's own terms leave this residual with no candidate set to sweep —
    # today, a concept scoped to more than one section (prohibition 5).
    sweepable: bool = True
    # ``never_sweep`` entries that name a concept, expanded to that concept's own captions, and the
    # prose entries kept as text (see `_vetoed_by_never_sweep`).
    never_keys: dict[str, str] = field(default_factory=dict)   # normalised caption -> key
    never_prose: tuple[str, ...] = ()
    conflicts: list[str] = field(default_factory=list)
    rows: list[LineItem] = field(default_factory=list)
    components: list[dict] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)


def _declared(policy, name: str) -> bool:
    """Whether the concept's own policy DECLARES this term, rather than inheriting the framework's.

    "Has a policy" cannot stand in for "overrides this term": every residual here declares a
    ``residual_policy``, so reading the attribute off the model hands back the SCHEMA default for
    anything the author left out, and the framework value would never govern a single concept —
    the opposite of one definition governing all 13.
    """
    return policy is not None and name in policy.model_fields_set


def _residuals(ontology, terms: _Terms) -> list[_Residual]:
    """Every ``exclusive_residual`` concept, with the framework's terms and its own overrides."""
    by_key = {m.canonical_key: m for m in ontology.mappings}
    # The SECTION's expected sign, which is what review trigger 5 names. A residual overrides it
    # with "either" on itself (its own sign genuinely is indeterminate), so reading the resolved
    # concept here would make that trigger unfireable.
    section_sign: dict[str, str] = {}
    for name, sd in (ontology.section_defaults or {}).items():
        for scope in (sd.section_scope or [name]):
            if sd.sign_convention:
                section_sign[scope] = sd.sign_convention

    out: list[_Residual] = []
    for m in ontology.mappings:
        if m.value_scope != "exclusive_residual":
            continue
        policy = m.residual_policy
        section = (policy.section_scope if policy and policy.section_scope
                   else (m.section_scope[0] if m.section_scope else ""))
        if not section:
            continue
        statement = m.statement.value if getattr(m.statement, "value", None) else m.statement
        res = _Residual(
            key=m.canonical_key, section=section,
            token=_token_of(section, m.canonical_key), statement=statement,
            # The framework's value is the DEFAULT each of these inherits; the concept's own policy
            # overrides it only where the author wrote the term down (`_declared`).
            population=(policy.population if _declared(policy, "population") and policy.population
                        else terms.population) or "sweep_only",
            cross_section=(bool(policy.cross_section) if _declared(policy, "cross_section")
                           else terms.cross_section),
            itemise=(bool(policy.itemise) if _declared(policy, "itemise")
                     else terms.itemise_required),
            section_sign=section_sign.get(section),
        )
        # Prohibition 5, "never spans sections", and the clause ``candidate_set`` states as "the
        # residual's SINGLE section_scope value": a concept whose policy points at one section while
        # it inherits another has no candidate set, and taking the first of the two would sweep one
        # section's rows into the other section's bucket with both sections still tying.
        scopes = {s for s in (m.section_scope or []) if s}
        if policy and policy.section_scope:
            scopes.add(policy.section_scope)
        if len(scopes) > 1 and "never_spans" in terms.prohibitions:
            res.sweepable = False
            res.conflicts.append(f"spans_sections:{'/'.join(sorted(scopes))}")
        notes = (bool(policy.notes_as_source) if _declared(policy, "notes_as_source")
                 else terms.notes_as_source)
        # ``face_only`` and ``note_use`` are what decide whether a note may be a SOURCE at all:
        # the global rule is "notes are evidence for a face amount, never an independent source of
        # one, unless note_use is decomposition_allowed". A residual asking for the note without
        # its section permitting it is a contradiction, not a permission.
        permitted = m.note_use == "decomposition_allowed" or m.face_only is False
        res.may_source_from_note = notes and permitted
        if notes and not permitted:
            res.conflicts.append("notes_as_source_without_note_use")
        if policy and policy.plug and terms.plug_forbidden:
            res.conflicts.append("plug_forbidden_by_framework")
        if m.derivation and terms.derivation_forbidden:
            res.conflicts.append("derivation_forbidden_by_framework")
        if terms.itemise_required and not res.itemise:
            res.conflicts.append("itemisation_required_by_framework")
        for entry in m.never_sweep or []:
            target = by_key.get(entry)
            if target is None:
                res.never_prose = res.never_prose + (normalize_label(entry),)
                continue
            res.never_keys[normalize_label(target.label or entry)] = entry
            for alias in target.aliases_for(None):
                res.never_keys[normalize_label(alias)] = entry
        out.append(res)
    return out


def _token_of(section: str, canonical_key: str) -> str | None:
    """The banner token a section id names, falling back to the residual's own key.

    Both are tried because the two vocabularies are authored independently: a section id
    ("cf_s1_cash_flow_from_operating_activities") and a canonical key
    ("cf_cash_flow_from_operating_activities__others") must resolve to the same banner token, and
    when a rulebook renames a section the key is the one that still says what the section is.
    """
    return section_token_of_scope(section) or section_of_key(canonical_key)


def _sign_contradicts_section(res: _Residual, row) -> str | None:
    """The section's sign expectation this row would violate, or None.

    Only where the section states one: ``either`` sections (the cash flow's activities, whose
    lines genuinely go both ways) expect nothing and veto nothing. Only where the row is
    UNANIMOUS too — a row with a positive current year and a negative prior is a real movement,
    not a mis-section, and refusing it would drop a figure the statement needs.
    """
    want = res.section_sign
    if want not in ("positive_expected", "negative_expected"):
        return None
    figures = [v for ev in row.values.values()
               if (v := (ev.value if ev.value is not None else ev.value_raw)) is not None and v != 0]
    if not figures:
        return None
    if want == "positive_expected" and all(f < 0 for f in figures):
        return f"{res.section} expects positive"
    if want == "negative_expected" and all(f > 0 for f in figures):
        return f"{res.section} expects negative"
    return None


def _vetoed_by_never_sweep(res: _Residual, label: str) -> str | None:
    """The ``never_sweep`` entry that rules this caption out, if any.

    Two kinds of entry, both enforced. An entry naming a CONCEPT vetoes that concept's own
    captions: an unmapped row printed "Total current liabilities" would otherwise be swept into
    current liabilities' Others and double-count the entire section — the mapper normally claims it,
    and this is what happens on the run where it did not. An entry written as PROSE ("the tax rate
    reconciliation in the tax note, which is a rate analysis and not a charge") vetoes a caption the
    sentence names, which is the strongest reading available without asking a model.
    """
    norm = normalize_label(label)
    if not norm:
        return None
    if norm in res.never_keys:
        return res.never_keys[norm]
    # Two tokens minimum, so a one-word caption cannot collide with any sentence containing it.
    if len(norm.split()) >= 2 or len(norm) >= 4:
        for prose in res.never_prose:
            if norm in prose:
                return prose
    return None


_Captions = dict[str, dict[str, tuple[str, tuple[str, ...]]]]


def _dedicated_captions(ontology, members: dict) -> _Captions:
    """Per section, the exact captions its DEDICATED concepts answer to — prohibition 4's index.

    Exact normalised captions only, and only the section's ``dedicated`` concepts (not its
    subtotals, which the eligibility list and ``never_sweep`` already refuse). A caption that IS a
    dedicated concept's label or alias is a row that concept would have claimed had it been asked,
    so absorbing it means the sweep ran on a row nobody tested. Anything looser would refuse rows on
    a resemblance, which is the mapper's job and not a prohibition's.
    """
    by_key = {m.canonical_key: m for m in ontology.mappings}
    out: _Captions = {}
    for section, mem in members.items():
        idx: dict[str, tuple[str, tuple[str, ...]]] = {}
        for key in mem.dedicated:
            concept = by_key.get(key)
            if concept is None:
                continue
            entry = (key, tuple(concept.exclude_hints or []))
            for caption in [concept.label or "", *concept.aliases_for(None)]:
                norm = normalize_label(caption)
                if norm:
                    idx.setdefault(norm, entry)
        out[section] = idx
    return out


def _untested_dedicated(captions: _Captions, section: str, label: str) -> str | None:
    """The dedicated concept whose own caption this row is printed with, if any."""
    norm = normalize_label(label)
    found = captions.get(section, {}).get(norm) if norm else None
    if found is None:
        return None
    key, hints = found
    # A concept whose ``exclude_hints`` veto this caption WAS evaluated and refused it. That is
    # review trigger 4's business — recorded on the component — not a row nobody tested.
    if any(re.search(hint, label.lower()) for hint in hints):
        return None
    return key


def _col(ev: ExtractedValue) -> str:
    return f"{ev.basis.value}|{ev.period_label or ''}"


def _row_values(li) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for ev in li.values.values():
        val = ev.value if ev.value is not None else ev.value_raw
        if val is not None:
            out[_col(ev)] = val
    return out


def _fact_id(spec: str, doc: DocumentModel, li, ev: ExtractedValue, where: str) -> str:
    """``global_rules.source_fact_id`` composed for one value, in the authored order.

    The composition is the rulebook's ("document_id|entity_scope|period|currency|unit|page|
    table_or_note|source_row_id"), not this module's: a fact id that leaves out the period or the
    entity scope cannot distinguish the four columns a statement prints.
    """
    unit = doc.unit_context
    parts = {
        "document_id": str(doc.id),
        "entity_scope": ev.basis.value,
        "period": ev.period_label or "",
        "currency": ev.unit_ctx.currency or (unit.currency if unit else ""),
        "unit": ev.unit_ctx.units_label or (unit.units_label if unit else "") or "as_reported",
        "page": ("" if ev.provenance is None or ev.provenance.page_index is None
                 else str(ev.provenance.page_index)),
        "table_or_note": where,
        "source_row_id": str(li.id),
    }
    if not spec:
        return str(li.id)
    return "|".join(parts.get(p.strip(), "") for p in spec.split("|"))


def _semantic_score(norm: str, alias_norm: str) -> float:
    """Coverage-weighted similarity, deliberately the same shape as ``mapping._alias_similarity``.

    Used ONLY to decide whether a swept component looked like a dedicated concept that
    ``exclude_hints`` vetoed — review trigger 4. It never populates anything, so a divergence from
    the mapper's own scorer can cost a flag, never a figure.
    """
    from rapidfuzz import fuzz

    tokens = alias_norm.split()
    if not tokens or not norm:
        return 0.0
    caption = norm.split()
    hit = sum(1 for t in tokens if any(t == c or fuzz.ratio(t, c) >= 80 for c in caption))
    coverage = hit / len(tokens)
    return (fuzz.token_sort_ratio(norm, alias_norm) / 100.0) * (0.4 + 0.6 * coverage)


def _rejected_candidates(ontology, section: str, label: str, threshold: float) -> list[dict]:
    """Dedicated concepts in this section that the caption scored against and lost.

    Only the vetoed ones are recorded: a concept whose ``exclude_hints`` fired on a caption that
    otherwise matched it is the one rejection a reviewer has to see, because it is the difference
    between "no concept covers this row" and "a concept covers this row and was told not to".
    """
    norm = normalize_label(label)
    out: list[dict] = []
    for m in ontology.mappings:
        if (not m.section_scope or m.section_scope[0] != section
                or m.value_scope == "exclusive_residual" or not m.exclude_hints):
            continue
        best = max((_semantic_score(norm, normalize_label(a)) for a in m.aliases_for(None)),
                   default=0.0)
        if best < threshold:
            continue
        text = label.lower()
        hit = next((ex for ex in m.exclude_hints if re.search(ex, text)), None)
        if hit:
            out.append({"canonical_key": m.canonical_key, "score": round(best, 3),
                        "reason": f"exclude_hints:{hit}"})
    return out


def _component(terms: _Terms, ontology, doc: DocumentModel, li, res: _Residual, *,
               where: str, threshold: float) -> dict:
    """One itemised component, carrying exactly the fields ``itemisation.component_fields`` names.

    ``value``, ``sign_as_reported`` and ``source_fact_id`` are keyed by (basis, period): one row
    carries a figure per column, and collapsing them to a scalar would silently publish one column
    as though it were the row. The label and the reported sign are RETAINED — the row is never
    relabelled "Others", which is the whole difference between an itemised residual and a bucket.
    """
    values = {c: str(v) for c, v in _row_values(li).items()}
    signs: dict[str, str] = {}
    fact_ids: dict[str, str] = {}
    for ev in li.values.values():
        raw = ev.value_raw if ev.value_raw is not None else ev.value
        if raw is None:
            continue
        signs[_col(ev)] = "negative" if raw < 0 else "positive" if raw > 0 else "zero"
        fact_ids[_col(ev)] = _fact_id(
            getattr(ontology.global_rules, "source_fact_id", "") or "", doc, li, ev, where)
    available = {
        "source_row_label": li.source_label,
        "source_row_label_normalised": normalize_label(li.source_label or ""),
        "source_row_locale": doc.locale or ontology.locale or "",
        "value": values,
        "sign_as_reported": signs,
        "source_fact_id": fact_ids,
        "rejected_candidates": _rejected_candidates(ontology, res.section, li.source_label or "",
                                                    threshold),
    }
    fields = terms.component_fields or tuple(available)
    out = {name: available[name] for name in fields if name in available}
    out["source"] = where
    return out


def _by_canonical_key(doc: DocumentModel) -> tuple[dict[str, dict[str, float]],
                                                   dict[str, list]]:
    """Per concept, the figure the DOCUMENT publishes for it per column, and the rows behind it."""
    totals: dict[str, dict[str, float]] = {}
    rows: dict[str, list] = {}
    for li in doc.line_items:
        if not li.canonical_key:
            continue
        rows.setdefault(li.canonical_key, []).append(li)
        slot = totals.setdefault(li.canonical_key, {})
        for col, val in _row_values(li).items():
            slot[col] = slot.get(col, 0.0) + float(val)
    return totals, rows


def _note_refs(li) -> list[str]:
    """The note numbers a face row cites (same reading as ``stages.link_notes``, which has not
    run yet at this point in the pipeline — the links it builds are not available here)."""
    nums = [n for ref in li.note_refs for n in ref.numbers if n]
    if not nums and li.note_number:
        nums.append(li.note_number)
    return list(dict.fromkeys(nums))


class ResidualStage:
    name = "residual"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        ontology = getattr(ctx, "ontology", None)
        framework = getattr(ontology, "residual_framework", None) if ontology is not None else None
        if framework is not None and doc.line_items:
            return self._sweep(doc, ctx, ontology, framework)
        return self._route_by_template(doc, ctx)

    # --- the v2 sweep ------------------------------------------------------------------------
    def _sweep(self, doc: DocumentModel, ctx: PipelineContext, ontology,
               framework) -> DocumentModel:
        terms = _read_terms(framework)
        residuals = _residuals(ontology, terms)
        # ``population: sweep_only`` is what makes this stage the only way in. A residual declaring
        # anything else is not swept — it is populated by whatever that declaration names.
        sweep_only = [r for r in residuals if r.population == "sweep_only" and r.sweepable]
        # ONE SECTION, ONE BUCKET — the clause ``candidate_set`` states as "the residual's SINGLE
        # section_scope value", read from the other end. Two buckets claiming one section is the same
        # contradiction prohibition 5 forbids, arrived at from two concepts instead of one, and
        # ``{r.section: r for r in usable}`` used to settle it by POSITION IN THE RULEBOOK FILE: the
        # later concept won, the earlier one vanished with nothing logged, and re-ordering the
        # ``mappings`` array — which is presentation, not meaning — silently moved a section's rows
        # into a different bucket. Neither bucket sweeps the contested section now, and both say why.
        contested: dict[str, list[_Residual]] = {}
        for r in sweep_only:
            contested.setdefault(r.section, []).append(r)
        for section, claimants in contested.items():
            if len(claimants) < 2:
                continue
            keys = [c.key for c in claimants]
            for c in claimants:
                others = ",".join(k for k in keys if k != c.key)
                c.sweepable = False
                c.conflicts.append(f"section_contested_by:{others}")
        # Recomputed, not filtered in place: a contested bucket must be out of the note sourcing, the
        # cross-section rescue and the reconciliation too, not merely out of the section map.
        usable = [r for r in sweep_only if r.sweepable]
        by_section = {r.section: r for r in usable}
        for r in residuals:
            if r.conflicts:
                ctx.log(f"residual:rulebook_conflict({r.key}):{','.join(r.conflicts)}")
            if r.population != "sweep_only":
                ctx.log(f"residual:not_swept({r.key}):population={r.population}")
            elif not r.sweepable:
                ctx.log(f"residual:not_swept({r.key}):no single section to sweep")
        if not by_section:
            ctx.log("residual:skipped(no sweepable residual concepts)")
            return doc
        if terms.aggregation != "sum_of_components":
            # The only aggregation this stage implements. Saying so is the point: a rulebook asking
            # for something else must not be quietly served sums instead.
            ctx.log(f"residual:unsupported_aggregation({terms.aggregation});"
                    " components are summed")

        # ``sweep.runs``: after every dedicated concept in the section has been resolved. Sweeping
        # before mapping would absorb rows a dedicated concept was never asked about — prohibition
        # 4, and the one mistake that cannot be seen afterwards, since the section still ties.
        if terms.after_dedicated and not (getattr(ctx, "mapping_strategy", "")
                                          or any(li.confidence.method for li in doc.line_items)):
            ctx.log("residual:skipped(sweep runs after dedicated concepts are resolved; "
                    "no mapping has run)")
            return doc

        members = section_members(ontology)
        section_by_key = {m.canonical_key: m.section_scope[0] for m in ontology.mappings
                          if m.section_scope}
        subtotal_of = {k: sec for sec, mem in members.items() for k in mem.subtotals}
        threshold = ctx.settings.extraction.evidence_floor
        captions = _dedicated_captions(ontology, members)
        _, statement_of = _statement_map(doc)

        # Every section a row can be printed INSIDE, with the banner token and statement that place
        # it. Sections with no residual of their own are in here too: eligibility 4 needs to know
        # that a row was printed inside one, which is not the same answer as "no section resolved".
        # The ``*_top_level`` scopes are not sections at all — they hold the statement's own totals,
        # which no row is printed "inside".
        placeable: dict[str, tuple[str | None, str | None]] = {}
        for section, mem in members.items():
            if section.endswith("top_level"):
                continue
            res = by_section.get(section)
            placeable[section] = ((res.token if res else section_token_of_scope(section)),
                                  (res.statement if res else mem.statement))
        # …and with eligibility 4 deleted from the list, the signals walk PAST a section that has no
        # bucket to the nearest one that has — which is what this stage did before it read entry 4.
        if not terms.inside_section_only:
            placeable = {s: v for s, v in placeable.items() if s in by_section}

        # Prohibition 2, "never populated by alias, regex or embedding match": a residual is locked
        # out of the mapper's index by ``alias_matching: disabled``, and a concept that omits that
        # field unlocks the most attractive caption in the rulebook — "Others" fuzzes against almost
        # anything short. A row that arrives already claimed for a residual by one of those matchers
        # is released here, so the only way into a bucket is still the sweep, and the row is offered
        # to the section's dedicated concepts' terms rather than sitting in Others un-itemised.
        if "never_matched" in terms.prohibitions:
            residual_keys = {r.key for r in residuals}
            for li in doc.line_items:
                method = li.confidence.method or ""
                if li.canonical_key in residual_keys and method in _MATCHED_METHODS:
                    ctx.log(f"residual:released({li.canonical_key}):"
                            f"{method} match on {li.source_label!r}")
                    li.canonical_key = None
                    li.confidence.method = None
                    li.confidence.flags.append(f"residual_alias_populated:{method}")

        # A statement ends at its closing total ("Cash and cash equivalents at end of year",
        # "Total equity and liabilities"). Whatever is printed below that is narrative — the note on
        # what cash equivalents comprise, a signature block — and it arrives as rows because it
        # carries figures. The rulebook's statement-level totals are the ``*_top_level`` sections.
        statement_totals = {k for sec, mem in members.items() if sec.endswith("top_level")
                            for k in (mem.subtotals + mem.dedicated)}
        closing: dict[str, int] = {}
        for li in doc.line_items:
            if li.canonical_key in statement_totals:
                stmt = statement_of(li) or ""
                closing[stmt] = max(closing.get(stmt, li.ordinal), li.ordinal)

        ordered = sorted(doc.line_items, key=lambda li: li.ordinal)
        # The first position at which each section's OWN subtotal is printed. A banner naming a
        # section whose subtotal is already above this row is spent — see `_section_of_row`.
        closed_at: dict[str, int] = {}
        for position, row in enumerate(ordered):
            section = subtotal_of.get(row.canonical_key or "")
            if section is not None and section not in closed_at:
                closed_at[section] = position
        per_share = (_per_share_rows(ordered) if _PER_SHARE_PHRASE in terms.exclusions else set())

        swept = ineligible = unresolved = 0
        for idx, li in enumerate(ordered):
            if terms.require_unclaimed and li.canonical_key:
                continue
            if terms.require_value and not _has_value(li):
                continue
            stmt = statement_of(li)
            # Membership of a per-share BLOCK is tested at the per-share position in the ordering,
            # not after every other phrase: a bare "基本" is two characters, so the
            # note-reference-only catch-all matches it too, and a per-share row sent to review as an
            # unreadable caption tells the analyst to go and read a caption that was perfectly clear.
            # It is the same exclusion either way, so this only decides which reason is reported.
            reason = next((phrase for phrase, test in _EXCLUSIONS
                           if phrase in terms.exclusions
                           and (test(li)
                                or (phrase == _PER_SHARE_PHRASE and li.id in per_share))), None)
            if reason is None and "narrative row" in terms.exclusions:
                end = closing.get(stmt or "")
                if end is not None and li.ordinal > end:
                    reason = "narrative row"
            if reason is not None:
                # Not swept and not silent: the rulebook's ``unbound_row_policy`` says an
                # ineligible row is still emitted for review rather than dropped into a bucket.
                ineligible += 1
                li.confidence.flags.append(f"residual_ineligible:{reason}")
                continue

            section = self._section_of_row(idx, ordered, li, placeable, section_by_key,
                                           subtotal_of, stmt, statement_of, closed_at)
            target = by_section.get(section or "")
            if target is not None and target.statement and stmt and target.statement != stmt:
                target = None
            if (target is None and section and section not in by_section
                    and terms.inside_section_only):
                # Eligibility 4: the row WAS printed inside a section — one that has no residual of
                # its own. Walking on to the nearest section that does have one is the cross-section
                # rescue the list forbids under another name, and it is how a row printed under
                # "Profit attributable to owners of the parent" ends up inside the tax charge.
                ineligible += 1
                li.confidence.flags.append(
                    f"residual_ineligible:printed in another section({section})")
                continue
            if target is None:
                # ``cross_section: false`` — no rescue, however similar the wording. A residual
                # whose own policy sets it true may claim a row from elsewhere in its statement.
                rescue = [r for r in usable if r.cross_section
                          and (not stmt or r.statement == stmt)]
                target = rescue[0] if len(rescue) == 1 else None
            if target is None:
                unresolved += 1
                li.confidence.flags.append("residual_section_unresolved")
                continue

            veto = _vetoed_by_never_sweep(target, li.source_label or "")
            if veto is not None:
                ineligible += 1
                li.confidence.flags.append(f"residual_never_sweep:{veto}")
                continue

            wrong_way = (_sign_contradicts_section(target, li) if terms.sign_must_agree else None)
            if wrong_way is not None:
                # Review trigger 5 — "residual sign is opposite to the section's sign_convention" —
                # applied to the ROW before it is absorbed rather than to the bucket's total after.
                #
                # Measured on a real HKEX filing: a stale banner put cost of sales in the INCOME
                # section, whose sign_convention is positive_expected, and an 814,645 COST swept into
                # "Other income items". Total income came out at 32,097 against revenue of 868,375
                # and every total below it inherited the error. Checking the finished bucket cannot
                # prevent that — by then the figure is in the statement, and the trigger fires on a
                # number the analyst is already reading.
                #
                # A row whose sign contradicts the section it would be swept into is evidence that
                # the SECTION is wrong, not that the amount is. So it goes to review with its own
                # reason, which is the outcome the framework prefers everywhere else: no figure is
                # better than a plausible wrong one.
                ineligible += 1
                li.confidence.flags.append(f"residual_sign_contradicts_section:{wrong_way}")
                continue

            # Prohibition 4: this caption IS a dedicated concept of the section, so the concept
            # would have claimed the row had it been asked. Absorbing it instead leaves the section
            # tying while a named figure is filed as unexplained remainder.
            untested = (_untested_dedicated(captions, target.section, li.source_label or "")
                        if "never_untested" in terms.prohibitions else None)
            if untested is not None:
                ineligible += 1
                li.confidence.flags.append(f"residual_dedicated_not_tested:{untested}")
                ctx.log(f"residual:not_absorbed({target.key}):{li.source_label!r} is "
                        f"{untested}'s own caption")
                continue

            self._claim(li, target)
            if target.itemise:
                target.components.append(
                    _component(terms, ontology, doc, li, target, where="face",
                               threshold=threshold))
            if normalize_label(li.source_label or "") in {normalize_label(c)
                                                          for c in terms.literal_others}:
                # A row captioned "Others" is one unmapped face row and nothing more: no privileged
                # treatment, no dedicated concept, and its own label kept.
                li.confidence.flags.append("residual_literal_others_caption")
            swept += 1

        swept += self._sweep_notes(doc, ctx, ontology, terms, usable, section_by_key, threshold,
                                   captions)
        self._audit_prohibitions(ctx, terms, usable, doc)
        self._reconcile(doc, ctx, ontology, terms, usable, members)

        ctx.log(f"residual:swept={swept} ineligible={ineligible} section_unresolved={unresolved} "
                f"unmapped_remaining={sum(1 for li in doc.line_items if not li.canonical_key)}")
        return doc

    @staticmethod
    def _claim(li: LineItem, res: _Residual) -> None:
        li.canonical_key = res.key
        li.confidence.mapping = min(li.confidence.mapping or 0.5, 0.5)
        li.confidence.method = "residual"
        li.confidence.flags.append(f"alloc:{FALLBACK_ALLOC}")
        # Routed, not identified — it stays visible in review so an analyst can promote it
        # to a specific concept (or add an alias) rather than accept the bucket.
        li.confidence.flags.append("residual_combined")
        res.rows.append(li)

    @staticmethod
    def _section_of_row(idx: int, ordered: list, li, placeable: dict, section_by_key: dict,
                        subtotal_of: dict, statement: str | None, statement_of,
                        closed_at: dict[str, int]) -> str | None:
        """The rulebook section a row was printed inside, by the three signals in the docstring.

        ``placeable`` is every section a row can be printed inside — including the ones with no
        residual — because the question here is where the row WAS printed, not which bucket would
        accept it. Answering with the nearest section that happens to have a bucket is what
        eligibility 4 forbids, and the caller is the one that applies it.

        Every signal stops at the statement boundary. A row at the foot of the balance sheet must
        not be placed by the income statement's first subtotal: the structure of a different
        statement says nothing about where this row was printed.

        A BANNER THE STATEMENT HAS ALREADY CLOSED is not used, and ``closed_at`` is what says so.
        ``section_hint`` is STICKY — ``row_reconstruct`` carries the last recognised heading down the
        page until another replaces it — so a filing whose next heading is not recognised (printed
        with a figure on the same line, or as a running header on a continuation page) leaves every
        row below it wearing the previous section's banner. Signal 1 then answered with a section the
        row is demonstrably not in, and did it FIRST, so the accounting structure that contradicted it
        was never consulted: measured on a real HKEX filing, current assets swept into the
        non-current-asset bucket under a stale "NON-CURRENT ASSETS" banner.

        A section's own subtotal is the end of it. Once ``Total non-current assets`` is above this
        row, no banner can put the row back inside that section, and the structure below decides.
        """
        if li.section_hint:
            token = section_of_banner(li.section_hint)
            if token:
                for section, (sec_token, sec_stmt) in placeable.items():
                    if sec_token != token or (statement and sec_stmt != statement):
                        continue
                    if closed_at.get(section, len(ordered)) < idx:
                        break                   # spent: fall through to the structure below
                    return section
        for nxt in ordered[idx + 1:]:
            if statement and statement_of(nxt) not in (None, statement):
                break
            section = subtotal_of.get(nxt.canonical_key or "")
            if section is None:
                continue
            if section in placeable:
                return section
            # A STATEMENT total ("Total assets") reached before any section subtotal: this row's own
            # section printed none, so the answer is the section above it, not the one below.
            break
        for prev in reversed(ordered[:idx]):
            if statement and statement_of(prev) not in (None, statement):
                break
            section = section_by_key.get(prev.canonical_key or "")
            if section and section in placeable:
                return section
        return None

    def _sweep_notes(self, doc: DocumentModel, ctx: PipelineContext, ontology, terms: _Terms,
                     usable: list[_Residual], section_by_key: dict, threshold: float,
                     captions: _Captions) -> int:
        """Sweep a cited note's unclaimed rows into the one residual whose section permits it.

        The framework's ``notes_as_source`` is false, and ``face_only``/``note_use`` say why: a note
        is evidence for a face amount, not an independent source of one. The shipped rulebook
        overrides that NOWHERE — the tax note used to, and lost its residual when the tax bucket was
        removed — so this runs only for a rulebook that turns it on, and then only for the note
        tables the section's own rows cite: a note nobody cites is not part of this section.
        """
        sourcing = [r for r in usable if r.may_source_from_note]
        if not sourcing or not doc.notes:
            return 0
        added = 0
        next_ordinal = max((li.ordinal for li in doc.line_items), default=0) + 1
        for res in sourcing:
            cited = {str(n) for li in doc.line_items
                     if section_by_key.get(li.canonical_key or "") == res.section
                     for n in _note_refs(li)}
            for table in doc.notes:
                if str(table.note_number) not in cited:
                    continue
                for item in table.items:
                    if item.canonical_key or not _has_value(item):
                        continue
                    if any(phrase in terms.exclusions and test(item)
                           for phrase, test in _EXCLUSIONS
                           if phrase in ("section subtotal", "statement total", "section header",
                                         "narrative row", "note-reference-only row")):
                        continue
                    label = getattr(item, "raw_label", "") or ""
                    veto = _vetoed_by_never_sweep(res, label)
                    if veto is not None:
                        ctx.log(f"residual:note_row_vetoed({res.key}):{veto}")
                        continue
                    # Prohibition 4 again, and it bites harder in a note: the note's own breakdown
                    # lines are exactly the section's dedicated concepts ("Current tax", "Deferred
                    # tax"), and absorbing one of those into Others would double the charge — the
                    # face row for it is already loaded.
                    if "never_untested" in terms.prohibitions:
                        untested = _untested_dedicated(captions, res.section, label)
                        if untested is not None:
                            ctx.log(f"residual:not_absorbed({res.key}):note row {label!r} is "
                                    f"{untested}'s own caption")
                            continue
                    row = LineItem(source_label=label, canonical_key=res.key,
                                   ordinal=next_ordinal, note_number=str(table.note_number))
                    next_ordinal += 1
                    for ev in item.values.values():
                        row.set_value(ev.model_copy(deep=True))
                    self._claim(row, res)
                    # Never mistakable for a printed face row: the component records the note it
                    # came from, and so does the row a reviewer opens.
                    row.confidence.flags.append(f"residual_note_sourced:{table.note_number}")
                    doc.line_items.append(row)
                    if res.itemise:
                        res.components.append(
                            _component(terms, ontology, doc, row, res,
                                       where=f"note:{table.note_number}", threshold=threshold))
                    added += 1
        if added:
            ctx.log(f"residual:note_sourced_components={added}")
        return added

    @staticmethod
    def _audit_prohibitions(ctx: PipelineContext, terms: _Terms, usable: list[_Residual],
                            doc: DocumentModel) -> None:
        """The two prohibitions that are about the FINISHED residual rather than one row.

        Prohibitions 1 and 3 are the ones the sweep cannot check while it runs. A residual only
        holds a figure it did not sweep because something else wrote its key onto a row — the LLM
        gap-closer, a stored correction, a rerun — and the sweep skips claimed rows by design, so it
        never sees it. Neither failure shows anywhere else: the section's printed subtotal has no
        opinion about whether the bucket under it is itemised, so a residual publishing a figure
        with nothing behind it reads exactly like one that swept it.
        """
        if not {"never_plug", "never_unitemised"} & terms.prohibitions:
            return
        published, rows_by_key = _by_canonical_key(doc)
        for res in usable:
            swept: dict[str, float] = {}
            for li in res.rows:
                for col, val in _row_values(li).items():
                    swept[col] = swept.get(col, 0.0) + float(val)

            if "never_unitemised" in terms.prohibitions and res.rows and not res.components:
                for li in res.rows:
                    li.confidence.flags.append(f"residual_value_without_components:{res.section}")
                    li.confidence.mapping = min(li.confidence.mapping or 0.4, 0.4)
                ctx.log(f"residual:value_without_components({res.key}) rows={len(res.rows)}")
            if "never_plug" not in terms.prohibitions:
                continue
            # The rows carrying this key that the sweep did not take are where the unaccounted
            # figure is, so they are what a reviewer has to be sent to.
            taken = {li.id for li in res.rows}
            unsourced = [li for li in rows_by_key.get(res.key, []) if li.id not in taken]
            for col, total in sorted(published.get(res.key, {}).items()):
                diff = total - swept.get(col, 0.0)
                if abs(diff) <= terms.rounding_unit:
                    continue
                flag = f"residual_plug_suspected:{res.section}:{col}={diff:.0f}"
                for li in unsourced or res.rows:
                    li.confidence.flags.append(flag)
                    li.confidence.flags.append("low_mapping_confidence")
                ctx.log(f"residual:{flag} published={total} itemised={swept.get(col, 0.0)}")

    def _reconcile(self, doc: DocumentModel, ctx: PipelineContext, ontology, terms: _Terms,
                   usable: list[_Residual], members: dict) -> None:
        """``reported_section_subtotal − Σ(dedicated) − Σ(residual components) = 0``, per column.

        A break is never closed here. The framework says so twice — never adjust the residual, never
        adjust a dedicated concept — because both would produce a section that ties while carrying a
        figure nobody printed. The difference is emitted as an ``unallocated_gap`` and the section
        goes to review.
        """
        values, rows_by_key = _by_canonical_key(doc)
        report: list[dict] = []
        for res in usable:
            mem = members.get(res.section)
            if mem is None or not (res.rows or res.components):
                continue
            columns = sorted({col for li in res.rows for col in _row_values(li)})
            entry = {"residual": res.key, "section": res.section, "statement": res.statement,
                     "components": res.components, "conflicts": res.conflicts,
                     "reconciliation": [], "review_triggers": res.triggers}
            for col in columns:
                comps = [float(v) for li in res.rows
                         for c, v in _row_values(li).items() if c == col]
                recon = reconcile_section(
                    mem, lambda k, _c=col: values.get(k, {}).get(_c), comps,
                    rounding_unit=terms.rounding_unit, per_row_tolerance=terms.per_row_tolerance)
                entry["reconciliation"].append({
                    "column": col, "subtotal_key": recon.subtotal_key, "reported": recon.reported,
                    "dedicated": recon.dedicated_total, "residual": recon.residual_total,
                    "diff": recon.diff, "tolerance": recon.tolerance, "status": recon.status,
                    "components": recon.components,
                })
                self._apply_triggers(ctx, terms, res, recon, comps, col,
                                     rows_by_key.get(recon.subtotal_key, []))
            report.append(entry)
        if report:
            # Kept on the context (which is persisted as the run's log/record) so the itemisation
            # and its arithmetic are auditable rather than implied by the rows.
            ctx.residual_itemisation = report
            for entry in report:
                ctx.log(f"residual:itemised({entry['residual']}) "
                        f"components={len(entry['components'])} "
                        + " ".join(f"{r['column']}:{r['status']}"
                                   for r in entry["reconciliation"])
                        + (f" triggers={','.join(entry['review_triggers'])}"
                           if entry["review_triggers"] else ""))

    @staticmethod
    def _apply_triggers(ctx: PipelineContext, terms: _Terms, res: _Residual, recon, comps,
                        col: str, subtotal_rows: list) -> None:
        """Fire the declared review triggers for one column, on the rows a reviewer will open."""
        fired: list[str] = []
        reported = abs(recon.reported) if recon.reported is not None else None
        residual_total = sum(comps) if comps else 0.0

        pct = terms.triggers.get("residual_share_of_subtotal")
        if pct is not None and reported and abs(residual_total) > reported * pct / 100.0:
            fired.append(f"residual_share_of_subtotal>{pct:g}%")
        pct = terms.triggers.get("component_share_of_subtotal")
        if pct is not None and reported and any(abs(v) > reported * pct / 100.0 for v in comps):
            fired.append(f"component_share_of_subtotal>{pct:g}%")
        limit = terms.triggers.get("component_count")
        if limit is not None and len(res.components) > limit:
            fired.append(f"component_count>{limit:g}")
        if ("vetoed_dedicated_match" in terms.triggers
                and any(c.get("rejected_candidates") for c in res.components)):
            fired.append("vetoed_dedicated_match")
        want = res.section_sign
        if "sign_opposite_to_section" in terms.triggers and want and residual_total:
            if ((want == "positive_expected" and residual_total < 0)
                    or (want == "negative_expected" and residual_total > 0)):
                fired.append(f"sign_opposite_to_section:{want}")
        if recon.status == "unallocated_gap" and "unallocated_gap" in terms.triggers:
            fired.append("unallocated_gap")

        if recon.status == "unallocated_gap" and terms.gap_fact:
            gap = f"{terms.gap_fact}:{res.section}:{col}={recon.diff:.0f}"
            for li in res.rows:
                li.confidence.flags.append(gap)
            for li in subtotal_rows:
                li.confidence.flags.append(gap)
                if terms.gap_to_review:
                    # The subtotal row is the section's own claim about itself, and it is
                    # high-confidence until something contradicts it. Without this the gap would
                    # be recorded against the residual only and the section would still read as
                    # auto-approvable.
                    li.confidence.flags.append("low_mapping_confidence")
            ctx.log(f"residual:{gap} reported={recon.reported} dedicated={recon.dedicated_total}"
                    f" residual={recon.residual_total} tolerance={recon.tolerance}")
        elif recon.status == "no_reported_subtotal" and terms.no_subtotal_unreconciled:
            # Itemised, but nothing printed to check it against: lower confidence, and say why.
            for li in res.rows:
                li.confidence.flags.append(f"residual_unreconciled:{res.section}")
                li.confidence.mapping = min(li.confidence.mapping or 0.4, 0.4)

        for name in fired:
            flag = f"residual_review:{name}"
            if flag not in res.triggers:
                res.triggers.append(flag)
            for li in res.rows:
                if flag not in li.confidence.flags:
                    li.confidence.flags.append(flag)

    # --- the pre-v2 path, unchanged ------------------------------------------------------------
    def _route_by_template(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        sections = _sections_from_template(template or {})
        if not sections or not doc.line_items:
            ctx.log("residual:skipped(no template sections or no line items)")
            return doc

        _, statement_of = _statement_map(doc)

        # A statement ends at its closing total; see the v2 path for why anything below it is
        # narrative. Statement-level totals are the template's section-level nodes: canonical keys
        # with no "__" section namespace.
        closing: dict[str, int] = {}
        for st in (template or {}).get("statements", []):
            keys = {sec.get("canonical_key") for sec in st.get("sections", [])
                    if sec.get("canonical_key") and "__" not in sec["canonical_key"]}
            last = max((li.ordinal for li in doc.line_items if li.canonical_key in keys),
                       default=None)
            if last is not None:
                closing[st.get("type", "")] = last

        ordered = sorted(doc.line_items, key=lambda li: li.ordinal)
        routed = skipped_after_end = 0
        for idx, li in enumerate(ordered):
            # ``doc.line_items`` is the FACE of the statements; note detail lives in
            # ``doc.notes`` as NoteItems, so there is nothing to exclude on that basis here.
            # (Do not filter on ``note_number``: reconstruction sets it on a face row that
            # merely CITES a note, which is most of them.)
            if li.canonical_key:
                continue
            if li.role in (LineRole.SUBTOTAL, LineRole.TOTAL) or not _has_value(li):
                continue
            stmt = statement_of(li)
            secs = sections.get(stmt or "")
            if not secs:
                continue
            end = closing.get(stmt or "")
            if end is not None and li.ordinal > end:
                skipped_after_end += 1
                continue
            by_token = {tok: others for tok, _, others in secs}
            subtotal_to_token = {sub: tok for tok, sub, _ in secs if sub}

            target = None
            # 1. the banner printed above this row
            if li.section_hint:
                tok = section_of_banner(li.section_hint)
                if tok and tok in by_token:
                    target = by_token[tok]
            # 2. the next section subtotal below it — a section runs up to its subtotal
            if target is None:
                for nxt in ordered[idx + 1:]:
                    tok = subtotal_to_token.get(nxt.canonical_key or "")
                    if tok:
                        target = by_token.get(tok)
                        break
            # 3. a trailing row after the last subtotal keeps the section above it
            if target is None:
                for prev in reversed(ordered[:idx]):
                    tok = section_of_key(prev.canonical_key or "")
                    if tok and tok in by_token:
                        target = by_token[tok]
                        break
            if target is None:
                continue

            li.canonical_key = target
            li.confidence.mapping = min(li.confidence.mapping or 0.5, 0.5)
            li.confidence.method = "residual"
            li.confidence.flags.append(f"alloc:{FALLBACK_ALLOC}")
            # Routed, not identified — it stays visible in review so an analyst can promote it
            # to a specific concept (or add an alias) rather than accept the bucket.
            li.confidence.flags.append("residual_combined")
            routed += 1

        ctx.log(f"residual:routed={routed} after_statement_end={skipped_after_end} "
                f"unmapped_remaining={sum(1 for li in doc.line_items if not li.canonical_key)}")
        return doc
