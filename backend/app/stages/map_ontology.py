"""Ontology-mapping stage — applies the multi-strategy matching ensemble.

Wires each extracted ``LineItem`` through ``services.mapping.OntologyMatcher`` and
records the winning canonical key, method, and per-strategy confidence. The ontology
+ locale come from the extraction job; the LLM adapter is pulled from the
registry when configured.

Two things happen here that the matcher cannot do, because they are judgements about the WHOLE
document rather than about one caption:

* **batching** — the unit handed to ``match_batch`` is one (statement, basis, period), so a
  statement printed across two pages is decided whole (:func:`batch_groups`).
* **containment and equivalence** — a gross parent may not be filed alongside the children it
  contains (``is_gross_parent`` / ``children_if_decomposed``, and the same rule stated globally in
  ``global_rules.mutually_exclusive_groups``), and two captions the rulebook declares to be one fact
  (``equivalence``) may not disagree in silence. Both are only decidable once every row has a
  concept, so they run as a pass over the mapped document.
* **decomposition from a disclosure** — a combined caption whose components the filing itemises
  somewhere else is split into those components, but ONLY when every one of them belongs to the
  combined caption's own section (:meth:`MapOntologyStage._split_from_disclosure`).

The three run in escalating order over the same declarations, and each hands the case it cannot
settle to the next: children printed on the FACE -> containment; children itemised in a cited
DISCLOSURE -> the split; a subtotal with exactly one declared child and nothing evidencing a
split -> ``sole_component_of``.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.core.models import DocumentModel
from app.core.models.enums import AllocationStatus, LineRole, MappingMethod, PrintedIn
from app.core.models.line_item import LineItem
from app.core.stage import PipelineContext
from app.services.mapping import OntologyMatcher, normalize_label
from app.services.rollups import section_members


def _columns_of(li) -> set[tuple[str, str]]:
    """The (basis, period) columns one row carries."""
    return {(ev.basis.value, ev.period_label or "") for ev in li.values.values()}


def _page_of(li) -> int | None:
    return next((ev.provenance.page_index for ev in li.values.values()
                 if ev.provenance is not None), None)


def batch_groups(doc: DocumentModel,
                 stmt_by_page: dict[int, str]) -> list[tuple[str | None, list]]:
    """Partition the line items into the groups the mapper is called with, in print order.

    The unit is **(statement, basis, period)**, not the source page it used to be. Grouping by page
    decided a statement that spans two pages in two calls, so no cross-line judgement could span the
    break — and a page break is exactly where a filing is most likely to cut a section in half,
    leaving a subtotal in one call and the lines it is made of in the other.

    Basis and period belong in the key because they are what identify WHICH statement: an annual
    report prints the consolidated balance sheet and the company balance sheet under the same
    classifier verdict, in different column blocks. Merged, the model would be shown each caption
    twice and asked to map both rows to one concept.

    They are read PER PAGE — the union of the columns that page's rows carry — and not per row,
    because the columns are a property of the page's header bands. Per row, any line that happens to
    print no prior-period figure would be split off into a call of its own, which is the
    fragmentation this change exists to remove.

    Rows the classifier gave no statement for are grouped by PAGE (the fallback key) and returned
    with ``None`` as the statement, which the caller maps per line. A group with no statement is not
    what a batch is for: it gets no statement-scoped candidate list, so the whole ontology would be
    put in front of the model for the rows we are least able to place.
    """
    cols_by_page: dict[int | None, set[tuple[str, str]]] = {}
    for li in doc.line_items:
        cols_by_page.setdefault(_page_of(li), set()).update(_columns_of(li))

    groups: dict[tuple, list] = {}
    for li in sorted(doc.line_items, key=lambda x: x.ordinal):
        page = _page_of(li)
        statement = stmt_by_page.get(page) if page is not None else None
        if statement:
            key = ("stmt", statement, frozenset(cols_by_page.get(page) or ()))
        else:
            key = ("page", page)
        groups.setdefault(key, []).append(li)
    return [(k[1] if k[0] == "stmt" else None, items) for k, items in groups.items()]


def _pairs_to_keep_apart(ontology) -> list[tuple[str, list[str], str]]:
    """(aggregate, components, why) for every containment the rulebook declares.

    Two declarations, one rule. ``is_gross_parent`` + ``children_if_decomposed`` states it on the
    concept; ``global_rules.mutually_exclusive_groups`` states it globally, and its ``rule`` text is
    the authoritative wording ("Populate the aggregate only when the face prints a single
    undifferentiated 'Reserves' line. If any component is printed, populate components and leave the
    aggregate null."). Both are read, because either one being ignored makes the other a lie.
    """
    out: list[tuple[str, list[str], str]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for g in ontology.global_rules.mutually_exclusive_groups:
        if g.aggregate and g.components:
            out.append((g.aggregate, list(g.components), g.id or "mutually_exclusive_group"))
            seen.add((g.aggregate, tuple(g.components)))
    for m in ontology.mappings:
        if m.is_gross_parent and m.children_if_decomposed:
            if (m.canonical_key, tuple(m.children_if_decomposed)) in seen:
                continue
            out.append((m.canonical_key, list(m.children_if_decomposed), "is_gross_parent"))
    return out


# A DISCLOSURE'S OWN TOTAL ROW, which must never be counted as one of its components, is now
# answered by the row's ROLE — ``services.notes_extract.note_row_role`` decides it from the caption
# where the ``NoteItem`` is built, and this pass reads the verdict instead of re-deriving it.
#
# ``_DISCLOSURE_TOTAL`` used to live here, with a comment explaining that ``role`` could not answer
# the question because every ``NoteItem`` is built ``LineRole.LINE``. That was true of the row
# BUILDER rather than of the question, and the same regex was therefore needed by a second reader
# that did not have it: ``stages.reconcile``'s "a note's own subtotal isn't a detail" guard, which
# consequently never fired — a note's printed total was summed alongside the rows it totals, so on a
# real filing not one of 109 reconciliation entries graded ``tied`` and two false "does not tie"
# assertions reached the analyst. One definition, in the place that constructs the row, fixes both
# readers at once. The reasoning about WHY the test is an anchored prefix, and why it is not
# ``row_reconstruct._TOTAL_LABEL``, travelled with it.


def _summed_columns(sources: list) -> dict[tuple[str, str], tuple[Decimal, object]]:
    """(basis, period) -> (summed value, one contributing ExtractedValue to copy shape from).

    Summed because a disclosure routinely itemises finer than the template: "trade receivables --
    third parties" and "-- related parties" are two disclosed rows and one template concept. The
    kept ``ExtractedValue`` supplies the period, basis, unit context and provenance; only the number
    is replaced, so a summed figure still points at a printed row rather than at nothing.
    """
    out: dict[tuple[str, str], tuple[Decimal, object]] = {}
    for it in sources:
        for ev in it.values.values():
            if ev.value is None:
                continue
            col = (ev.basis.value, ev.period_label or "")
            prev = out.get(col)
            out[col] = ((prev[0] if prev else Decimal(0)) + Decimal(ev.value),
                        prev[1] if prev else ev)
    return out


def _columns_not_accounted_for(parent, hits: dict, tol: Decimal) -> list[str]:
    """The (basis, period) columns where the disclosed components do not sum to the aggregate.

    The arithmetic support ``global_rules.parent_child_allocation`` asks for, and the only one of its
    four kinds this stage can test. Every column the AGGREGATE carries a figure in must be accounted
    for: a column the components are silent about is a failure, not a column to skip, because
    publishing components for one period and nothing for the other leaves the other period's figure
    deleted from the statement.
    """
    totals: dict[tuple[str, str], Decimal] = {}
    for sources in hits.values():
        for col, (value, _ev) in _summed_columns(sources).items():
            totals[col] = totals.get(col, Decimal(0)) + value
    out: list[str] = []
    for ev in parent.values.values():
        if ev.value is None:
            continue
        col = (ev.basis.value, ev.period_label or "")
        got = totals.get(col)
        if got is None or abs(Decimal(ev.value) - got) > tol:
            out.append(f"{col[0]}/{col[1]}")
    return out


def _same_section_decompositions(ontology) -> list[tuple[str, list[str], str]]:
    """(aggregate, children, section) for every declared containment whose children ALL sit in the
    aggregate's OWN section.

    THIS IS THE GATE, and it is the whole of the product rule: a combined caption may be split into
    its components when those components belong where the combined caption is printed, and may not
    when they do not. Splitting across sections would mean deciding how much of one printed amount
    falls on each side of a boundary the page never drew — the twelve-month cut between current and
    non-current borrowings is the standard case, and the maturity profile that would settle it lives
    in a note this stage is not reading as arithmetic.

    Measured on the shipped rulebook: five of the seven declared gross parents pass (prepayments and
    other receivables, cash and cash equivalents, other payables and accruals, reserves, share of
    profit of associates and JVs) and two do not — ``pl_income__other_income`` and
    ``pl_expenses__other_expenses``, whose declared children sit in the non-operating and
    exceptional-item sections rather than in income and expenses.

    ``section_scope`` is read off the RESOLVED ontology (the loader applies ``inherits``), so a
    concept that states nothing itself is judged by its section's defaults, which is where almost
    every concept's scope actually comes from.
    """
    section_of = {m.canonical_key: tuple(m.section_scope or ()) for m in ontology.mappings}
    out: list[tuple[str, list[str], str]] = []
    for aggregate, children, _why in _pairs_to_keep_apart(ontology):
        own = section_of.get(aggregate, ())
        if len(own) != 1:
            # No single home section, so "the same section" is not a question this can answer.
            continue
        if children and all(section_of.get(k, ()) == own for k in children):
            out.append((aggregate, list(children), own[0]))
    return out


def _note_permitted_decompositions(ontology, template) -> list[tuple[str, list[str], str]]:
    """Aggregates the run's two definitions BETWEEN THEM authorise reading out of a note.

    THIS IS REQUIREMENT 20's automatic arm, and neither half of it is an inference:

    * WHICH LINES ARE THE COMPONENTS is the TEMPLATE's own ``rollup`` — "Income tax expense" is
      declared a subtotal over ``current_tax`` and ``deferred_tax``, so reading those two out of the
      tax note is reading the template's own arithmetic off the page the filing printed it on. The
      alternative, inferring the parent/child relation from which captions happen to appear in a
      note, is how a movement schedule gets mistaken for a decomposition.
    * WHETHER A NOTE MAY BE THE SOURCE is the RULEBOOK's ``note_use``. It fires by default and the
      rulebook overrides it: the shipped one states the policy as a section default —
      "Concepts default to face_only: true. Notes are evidence for a face amount, never an
      independent source of one, unless note_use is decomposition_allowed" — and then names its one
      exception on the tax section, with the reason: "HKEX filings routinely print only 'Income tax
      expense' on the face and split current/deferred in the tax note. Decomposition from that note
      is permitted because the split is a reconciliation of the face." So on the shipped rulebook
      this admits the tax aggregate and nothing else, which is the author's stated intent rather
      than a limitation of this function.

    WHY ``note_use`` AND NOT A NEW FIELD. ``stages.residual`` already reads it for the neighbouring
    question — may a residual sweep invent a face row sourced from a note — with the same meaning
    and the same override. The difference is only the default, and the reason for it: the sweep is
    FABRICATING a line the filing never printed, so it demands opt-in; this pass is READING an
    itemisation the filing did print, under a total the template already says is their sum. Two
    defaults, one field, because it is one question about the same rulebook statement — do not
    "unify" them into one default without re-reading both call sites.

    Every gate ``_split_from_disclosure`` already applies still applies, and they are what make the
    automatic arm safe: exactly one face row carries the aggregate, at least two of the declared
    children are itemised in the cited note, the note's own total is excluded by ROLE, and the
    components must account for the aggregate in every column or the split is declined.
    """
    from app.services.rollups import calculated_nodes

    permitted = {m.canonical_key for m in ontology.mappings
                 if getattr(m, "note_use", None) == "decomposition_allowed"}
    if not permitted:
        return []
    section_of = {m.canonical_key: tuple(m.section_scope or ()) for m in ontology.mappings}
    out: list[tuple[str, list[str], str]] = []
    for key, node in calculated_nodes(template).items():
        if key not in permitted:
            continue
        own = section_of.get(key, ())
        if len(own) != 1:
            # No single home section, so the section-scoped match below has no scope to ask under.
            continue
        children = [c for c in ((node.get("rollup") or {}).get("children") or [])
                    # THE SAME-SECTION RULE, which is the product decision this pass was built on:
                    # a component in another section is left alone. A rollup's children normally
                    # share its section, so this is a guard rather than a filter — but a template may
                    # declare a total over lines from two sections, and that is the case the rule is
                    # about.
                    if section_of.get(c, ()) == own]
        if len(children) >= 2:
            out.append((key, children, own[0]))
    return out


def _cited_notes(parents: list) -> set[str]:
    """The note numbers the subtotal's own rows cite — read the way ``stages.residual`` reads them,
    because ``link_notes`` has not run yet at this point in the pipeline."""
    out: set[str] = set()
    for li in parents:
        out.update(n for ref in li.note_refs for n in ref.numbers if n)
        if li.note_number:
            out.add(li.note_number)
    return out


def _sibling_evidence(doc: DocumentModel, ontology, parents: list,
                      siblings: list[str]) -> str:
    """Why a ``sole_component_of`` inference must be refused, or "" when nothing contradicts it.

    A sibling is EVIDENCED by its own captions — the rulebook's aliases for it, which is the only
    place those words are written down — appearing either on the face of the same statement or in a
    note the subtotal cites. The mapper having failed to claim such a row is exactly the case that
    matters: an unrecognised "Deferred taxation" line means the split exists and was missed, so
    asserting the whole charge is current would publish a figure the page contradicts.
    """
    by_key = {m.canonical_key: m for m in ontology.mappings}
    captions: list[tuple[str, str]] = []
    for key in siblings:
        m = by_key.get(key)
        if m is None:
            continue
        for caption in [m.label or "", *m.aliases_for(None)]:
            norm = normalize_label(caption)
            if norm:
                captions.append((norm, key))
    if not captions:
        return ""

    def hit(label: str) -> str:
        norm = normalize_label(label or "")
        return next((key for cap, key in captions if cap and cap in norm), "")

    pages = {p.index for p in doc.pages if p.statement and any(
        ev.provenance is not None and ev.provenance.page_index == p.index
        for li in parents for ev in li.values.values())}
    statements = {p.statement for p in doc.pages if p.index in pages}
    for li in doc.line_items:
        page = next((ev.provenance.page_index for ev in li.values.values()
                     if ev.provenance is not None), None)
        stmt = next((p.statement for p in doc.pages if p.index == page), None)
        if stmt not in statements:
            continue
        found = hit(li.source_label or "")
        if found:
            return f"{found} is printed on the face as {li.source_label!r}"
    cited = _cited_notes(parents)
    for table in doc.notes:
        if str(table.note_number) not in cited:
            continue
        for item in table.items:
            found = hit(getattr(item, "raw_label", "") or "")
            if found:
                return (f"{found} is disclosed in note {table.note_number} as "
                        f"{item.raw_label!r}")
    return ""


class MapOntologyStage:
    name = "map_ontology"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        ontology = getattr(ctx, "ontology", None)
        if ontology is None or not doc.line_items:
            ctx.log("map_ontology:skipped(no ontology or no line items)")
            return doc

        # Pull the configured LLM provider so mapping is description-based (see
        # services.mapping). Falls back to the deterministic ensemble if unavailable.
        llm_provider = None
        unavailable_reason = ""
        if ctx.settings.llm.provider == "stub":
            unavailable_reason = "llm provider is 'stub'"
        elif not ctx.settings.extraction.llm_mapping:
            unavailable_reason = "extraction.llm_mapping is disabled"
        else:
            try:
                llm_provider = ctx.registry.get("llm", ctx.settings.llm.provider)
            except Exception as exc:  # unknown/misconfigured provider (e.g. no API key)
                unavailable_reason = f"{ctx.settings.llm.provider} unavailable: {exc}"
                ctx.log(f"map_ontology:llm_unavailable({exc})")

        matcher = OntologyMatcher(ontology, locale=doc.locale, settings=ctx.settings,
                                  llm_provider=llm_provider)
        scope = ctx.settings.extraction.mapping_scope
        # Record the strategy for the run record: mapping by MEANING (LLM) and mapping by
        # string/rule evidence are very different quality levels, and the difference has to be
        # visible to whoever reads the output.
        # Provisional: confirmed after the run, because a provider can resolve (the adapter
        # constructs fine) and still fail every call — e.g. no API key. Claiming
        # "llm_description" on a run that made zero successful calls would overstate it.
        ctx.mapping_strategy = "llm_description" if matcher.llm_enabled else "deterministic"
        ctx.mapping_strategy_reason = "" if matcher.llm_enabled else (
            unavailable_reason or "no llm provider resolved")
        ctx.log(f"map_ontology:strategy={ctx.mapping_strategy}(intended) scope={scope}"
                + (f" reason={ctx.mapping_strategy_reason}" if ctx.mapping_strategy_reason else ""))

        def _apply(li, result) -> bool:
            if result and result.canonical_key:
                li.canonical_key = result.canonical_key
                li.confidence.mapping = result.confidence
                li.confidence.method = result.method.value
                if result.allocation_status:
                    li.confidence.flags.append(f"alloc:{result.allocation_status}")
                if result.rerouted_from:
                    # The concept whose caption matched is not the one the row was filed under: the
                    # section banner named a different variant of the same fact. Recorded per row,
                    # because a reviewer looking at the comprehensive-income bottom line needs to
                    # see that the caption on the page said "loss for the year".
                    li.confidence.flags.append(f"section_reroute_from:{result.rerouted_from}")
                if result.needs_review:
                    li.confidence.flags.append("low_mapping_confidence")
                return True
            if result and result.computed_claim:
                # The caption names a concept the framework COMPUTES (`extraction_mode: derive`), so
                # the matcher refused to bind the row to anything (services.mapping._computed_claim).
                # The row must not fall to the residual sweep either: an unclaimed face row with a
                # value is swept into its section's "Others", which would add a subtotal OF the
                # section back INTO the section under a different name — the same failure the
                # containment pass below marks a subtotal to avoid. A row that is a computed subtotal
                # is marked as one, which the sweep's own eligibility rules already exclude.
                if li.role is LineRole.LINE:
                    li.role = LineRole.SUBTOTAL
                li.confidence.flags.append(f"computed_concept_printed:{result.computed_claim}")
                li.confidence.flags.append("low_mapping_confidence")
            return False

        # Page -> statement, from the classifier. Mapping uses it to refuse concepts from a
        # different statement (a P&L caption resolving to a cash-flow key, etc.).
        stmt_by_page = {p.index: p.statement for p in doc.pages if p.statement}

        def _statement_of(li) -> str | None:
            for ev in li.values.values():
                if ev.provenance is not None:
                    return stmt_by_page.get(ev.provenance.page_index)
            return None

        mapped = 0
        if scope == "per_statement":
            # One grounded LLM call per (statement, basis, period) — see `batch_groups` for why the
            # unit is the statement and not the source page it used to be.
            groups = batch_groups(doc, stmt_by_page)
            by_id = {str(li.id): li for li in doc.line_items}
            batched = unstated = 0
            for statement, group in groups:
                if statement is None:
                    # No statement means no statement-scoped candidate list and no coherent
                    # neighbourhood, so these are decided one at a time rather than handed to a
                    # batch as if they were a statement.
                    unstated += len(group)
                    for li in group:
                        if _apply(li, matcher.match(li.source_label,
                                                    section=li.section_hint)):
                            mapped += 1
                    continue
                batched += len(group)
                results = matcher.match_batch(
                    [(str(li.id), li.source_label) for li in group],
                    statement=statement,
                    # A statement spans several section banners, so the banner is per row.
                    sections={str(li.id): li.section_hint for li in group})
                # Only ids from THIS group are applied. `by_id` spans the whole document, so an
                # id echoed from another group would otherwise apply this statement's decision to
                # a row in a different one — and an id belonging to no row at all crashed the run
                # outright. The matcher now filters these too; this is the belt to that braces,
                # because the cost of getting it wrong is a wrong figure on the face.
                in_group = {str(li.id) for li in group}
                for iid, res in results.items():
                    if iid not in in_group:
                        ctx.log(f"map_ontology:foreign_item_id_ignored({iid})")
                        continue
                    if _apply(by_id[iid], res):
                        mapped += 1
            # How the document was actually cut up, so "one statement, two pages, one call" is
            # verifiable from the run record instead of asserted in a docstring.
            ctx.log(f"map_ontology:groups={len(groups)} batched_rows={batched}"
                    f" per_line_rows={unstated} chunks={matcher.usage['batch_chunks']}"
                    f" max_chunk={matcher.usage['batch_max_items']}")
        else:
            for li in doc.line_items:
                if _apply(li, matcher.match(li.source_label, statement=_statement_of(li),
                                            section=li.section_hint)):
                    mapped += 1

        # Whole-document rules, which need every row to have a concept first.
        mapped -= self._enforce_containment(doc, ontology, ctx)
        self._check_equivalence(doc, ontology, ctx)
        # Escalating over the same declarations: containment above handled components printed on
        # the FACE; this handles components itemised in a cited DISCLOSURE; sole-components below
        # handles a subtotal with one declared child and nothing evidencing a split. Order matters —
        # a parent this pass decomposes has children filed by the time the last one looks, which is
        # exactly the condition that makes it decline.
        mapped += self._split_from_disclosure(doc, ontology, matcher, ctx)
        mapped += self._infer_sole_components(doc, ontology, ctx)

        # Roll the mapper's LLM usage up onto the context for the audit log.
        ctx.llm_input_tokens += matcher.usage["input_tokens"]
        ctx.llm_output_tokens += matcher.usage["output_tokens"]
        ctx.llm_calls += matcher.usage["calls"]
        if matcher.usage["model"]:
            ctx.llm_model = matcher.usage["model"]
        # Report what ACTUALLY happened: zero successful calls means the deterministic
        # ensemble decided every line, whatever was configured.
        if matcher.llm_enabled and matcher.usage["calls"] == 0:
            ctx.mapping_strategy = "deterministic"
            ctx.mapping_strategy_reason = (
                matcher.usage.get("last_error")
                or "llm provider resolved but made no successful calls")
        ctx.log(f"map_ontology:mapped={mapped}/{len(doc.line_items)} llm_calls={matcher.usage['calls']}")
        # Named routes, not just a count: "the banner corrected 4 answers" is not reviewable, while
        # "pl_profit_for_the_year -> pl_total_comprehensive_income_for_the_year" is the one line of
        # the run record that says which figure moved and why.
        if matcher.usage["family_resolved"]:
            ctx.log(f"map_ontology:section_reroutes={matcher.usage['family_resolved']}"
                    f" routes={','.join(matcher.usage['family_routes'])}")
        if matcher.usage["computed_refused"]:
            # Rows whose caption named a concept the framework computes. Logged because the
            # alternative the mapper used to take — filing the figure on the nearest neighbouring
            # subtotal — showed up nowhere at all, and the statement still tied.
            ctx.log(f"map_ontology:computed_concept_rows={matcher.usage['computed_refused']}")
        if matcher.usage["confusable_ties"]:
            # `binding.order` step 6: answered with both candidates and a review flag rather than a
            # pick. Counted here because "the mapper declined N rows on purpose" reads very
            # differently from "the mapper failed on N rows".
            ctx.log(f"map_ontology:confusable_ties={matcher.usage['confusable_ties']}")
        return doc

    @staticmethod
    def _unexplained_columns(parent, children: list, tol: Decimal) -> list[str]:
        """The columns where the printed components do not account for the aggregate.

        ``global_rules.parent_child_allocation`` says to subtract "only on explicit inclusion
        wording, hierarchy, reconciliation or arithmetic support", and the ARITHMETIC is the one of
        those four this stage can actually test: does the aggregate equal the sum of the components
        printed around it? Compared per (basis, period) column and only beyond
        ``recon_abs_tolerance``, because the same figure rounded in two places is not a disagreement.

        A column no component carries a figure in is skipped rather than reported: there is nothing
        to compare, not a disagreement of the whole aggregate.
        """
        out: list[str] = []
        for col, pv in parent.values.items():
            if pv.value is None:
                continue
            got = [li.values[col].value for li in children
                   if col in li.values and li.values[col].value is not None]
            if not got:
                continue
            if abs(pv.value - sum(got)) > tol:
                out.append(col)
        return out

    @staticmethod
    def _enforce_containment(doc: DocumentModel, ontology, ctx: PipelineContext) -> int:
        """A gross parent may not be FILED alongside the children it contains. Returns rows unfiled.

        The rulebook states this twice — ``is_gross_parent`` + ``children_if_decomposed`` on the
        concept, ``global_rules.mutually_exclusive_groups`` globally — and states the consequence
        once: "If any component is printed, populate components and leave the aggregate null."
        Filing both double-counts (equity gains its reserves twice) and every check still passes,
        because the statement remains internally consistent — it is just wrong.

        The aggregate's row keeps its value and provenance and loses only its canonical_key, and it
        is marked a SUBTOTAL. That second part is load-bearing: an unclaimed face row with a value is
        swept into its section's "Others" (stages.residual), which would ADD the aggregate back into
        the section under a different name — strictly worse than the double count this pass exists to
        prevent. A row equal to the sum of the children printed around it IS a subtotal, and the
        sweep's own eligibility rules already exclude those.

        Three sentences of ``global_rules`` are enforced here, and they are otherwise live only
        inside the LLM system prompt — so on a run with no provider configured they did nothing at
        all. ``parent_child_allocation``: "Never load a gross parent and its separately mapped
        children additively" is the pass itself; "Subtract only on … arithmetic support" and "If
        containment is uncertain, retain the parent as evidence and route to review" are the
        ``_unexplained_columns`` arm below. ``no_fabricated_split`` is the arm that keeps the
        aggregate when the face printed no component. The rest of those blocks is prose about a
        judgement (which wording evidences containment, how a note decomposition is reconciled) and
        stays prompt-only, because there is nothing deterministic to test it with.
        """
        pairs = _pairs_to_keep_apart(ontology)
        if not pairs:
            return 0
        tol = Decimal(str(ctx.settings.extraction.recon_abs_tolerance))
        unfiled = 0
        # Every pair is judged against the keys as MAPPED, snapshotted before this pass unfiles
        # anything. Reading `doc.line_items` live makes the outcome depend on the order concepts
        # happen to sit in the rulebook file: where a rulebook declares a chain (A contains B, B
        # contains C) and the page prints all three, processing B first unfiles it, so A then sees
        # no component present and is KEPT — leaving A and C both filed, which is precisely the
        # double count this pass exists to prevent. Move B after A in the file and the answer
        # changes. The snapshot makes a chain resolve the same way whichever order it is written in:
        # A and B are both unfiled as evidence, C stands. The shipped rulebook declares no chain
        # (tests/test_composite_caption_containment.py holds that), but an uploaded one may.
        as_mapped = {id(li): li.canonical_key for li in doc.line_items}
        printed = {k for k in as_mapped.values() if k}
        for aggregate, components, why in pairs:
            filed = [li for li in doc.line_items if as_mapped.get(id(li)) == aggregate]
            if not filed:
                continue
            present = [c for c in components if c in printed]
            if not present:
                # The face printed only the aggregate — keep it. This arm is also
                # ``global_rules.no_fabricated_split`` ("Where only a combined figure is reported,
                # load the combined concept and leave the children empty") as the deterministic path
                # honours it: nothing here invents a decomposition the page does not print.
                continue
            child_rows = [li for li in doc.line_items if as_mapped.get(id(li)) in present]
            for li in filed:
                if li.canonical_key is None:
                    continue          # an outer containment already unfiled it; do not count twice
                li.canonical_key = None
                if li.role is LineRole.LINE:
                    li.role = LineRole.SUBTOTAL
                li.confidence.flags.append(
                    f"alloc:{AllocationStatus.PARENT_GROSS_EVIDENCE_ONLY.value}")
                li.confidence.flags.append(f"contains_mapped_children:{','.join(present)}")
                # ``parent_child_allocation``: "If containment is uncertain, retain the parent as
                # evidence and route to review." Unfiling above is done on the strength of the
                # DECLARATION alone; the arithmetic is the support the same block asks for, and where
                # the printed components do not account for the aggregate the containment is not
                # confirmed on the page — either a component was not printed (or not extracted), or
                # the aggregate is not their sum. The row is still retained as evidence, and now it
                # is routed to review as well: without that, unfiling silently removes the
                # unexplained part of the figure from the statement and every remaining check ties.
                gaps = MapOntologyStage._unexplained_columns(li, child_rows, tol)
                if gaps:
                    li.confidence.flags.append(
                        f"containment_unexplained:{aggregate}:{len(gaps)}")
                    if "low_mapping_confidence" not in li.confidence.flags:
                        li.confidence.flags.append("low_mapping_confidence")
                    ctx.log(f"map_ontology:containment_unexplained({why}):{aggregate}"
                            f" columns={len(gaps)} components={','.join(present)}")
                unfiled += 1
            child_flag = f"alloc:{AllocationStatus.CHILD_COMPONENT.value}"
            for li in doc.line_items:
                # Named on the children too: a reviewer opening the empty aggregate needs the rows
                # that replaced it, and a reviewer opening a child needs to know why the parent is
                # empty. Once each — a concept can be a component of more than one declared group.
                if li.canonical_key in present and child_flag not in li.confidence.flags:
                    li.confidence.flags.append(child_flag)
            ctx.log(f"map_ontology:containment({why}):{aggregate}"
                    f" unfiled_rows={len(filed)} components={','.join(present)}")
        return unfiled

    def _split_from_disclosure(self, doc: DocumentModel, ontology, matcher,
                               ctx: PipelineContext) -> int:
        """Split a combined caption into the components the filing itemises elsewhere — but only
        into components that belong in the combined caption's OWN section. Returns rows added.

        THE RULE, and why the section is the boundary. A filing that prints one line for
        "Prepayments, other receivables and other assets" and itemises it in the note it cites is
        reporting the components; reading them is not an inference, it is reading. What IS an
        inference is deciding how much of a single printed amount falls on each side of a boundary
        the page never drew — the twelve-month cut between current and non-current borrowings, whose
        answer lives in a maturity table this stage does not read as arithmetic. So components in the
        aggregate's own section are published, and components anywhere else leave the aggregate
        standing. ``_same_section_decompositions`` is that gate.

        THE GATE IS ALSO STRUCTURAL, not just a test. Every note caption is matched with the
        aggregate's declared section as the candidate scope, so a concept outside that section cannot
        be produced at all: asked under current assets, a borrowings caption resolves to nothing.
        The section test and the section-scoped match are two expressions of one rule, and the
        second is the one that holds if the first is ever edited wrongly.

        WHAT IT REFUSES, each because publishing would be worse than leaving the aggregate:
        * fewer than two components — a single child is ``sole_component_of``'s question, and this
          pass must not answer it with a note row instead of that declaration's own evidence rules;
        * components that do not ACCOUNT FOR the aggregate, per (basis, period) column, within
          ``recon_abs_tolerance`` — the arithmetic is the only support this stage can test for
          ``global_rules.parent_child_allocation``, and a split that does not tie is a fabricated
          decomposition however plausible the captions;
        * a disclosure that itemises nothing this section claims, or none at all.

        A DISCLOSURE'S OWN TOTAL ROW is excluded BY ROLE, which is now the one answer to that
        question: ``services.notes_extract.note_row_role`` decides it from the caption where the
        ``NoteItem`` is built, so this pass and reconcile's identical guard read the same verdict.
        They did not. The caption test used to live here and reconcile's role test never fired,
        because every ``NoteItem`` was built ``LineRole.LINE`` — so a note's printed total was
        summed alongside the rows it totals and, on a real filing, not one of 109 reconciliation
        entries graded ``tied``. Counting the note's total as a component here would double the sum
        and fail the arithmetic gate, which is a safe failure; getting it right is what makes the
        common case work rather than silently decline.

        WHAT THE SPLIT ROWS CARRY. Each component's figures are the DISCLOSURE ROW'S OWN
        ``ExtractedValue`` copies, so click-to-source lands on the itemised line the figure was read
        from, on the page it was printed on. They are stamped ``printed_in = FACE`` because that is
        what they are — a face concept of this statement, sourced from the note — and the segment
        stage honours an explicit stamp (``services.buckets``). The aggregate is NOT deleted: it is
        un-filed and demoted to a subtotal carrying ``decomposed_into``, exactly as the containment
        pass treats a parent whose children were printed on the face, so the printed combined figure
        stays auditable and the section does not count the money twice.

        NOTE ITEMS ARE NOT MUTATED. ``NoteItem.canonical_key`` is a field nothing in the codebase
        writes, and two live readers consume it — reconcile's ``maps_to_distinct_template_line`` and
        ``buckets._bucket_from_note_content`` — so both are inert today. Writing it here would wake
        both inside a change about something else; the mapping is kept local to this pass and those
        two are left for their own change.
        """
        # TWO SOURCES OF CANDIDATES, ONE PASS. The declared containment pairs
        # (``is_gross_parent``/``mutually_exclusive_groups``) say "these two concepts may never both
        # be populated"; the note-permitted aggregates (§20) say "this total may be read out of its
        # note". Different authorities for the same act, so they share every gate below rather than
        # growing a second pass that could answer the same question differently. Declared pairs
        # first, so where a concept is both it is handled by the stronger statement.
        # The TEMPLATE is what declares which lines are a total's components, so the §20 arm needs
        # it. Taken off the context the same way the ontology is, and dumped to the plain dict shape
        # ``rollups.calculated_nodes`` reads — the same shape the routes hand it.
        template = getattr(ctx, "template", None)
        template_def = (template.model_dump(mode="json")
                        if template is not None and hasattr(template, "model_dump") else template)
        decls = _same_section_decompositions(ontology)
        seen = {a for a, _c, _s in decls}
        decls = decls + [d for d in _note_permitted_decompositions(ontology, template_def)
                         if d[0] not in seen]
        if not decls:
            return 0
        tol = Decimal(str(ctx.settings.extraction.recon_abs_tolerance))
        stmt_by_page = {p.index: p.statement for p in doc.pages if p.statement}
        added = 0
        for aggregate, children, section in decls:
            parents = [li for li in doc.line_items if li.canonical_key == aggregate]
            if len(parents) != 1:
                # Zero: either not printed, or the containment pass already un-filed it because the
                # children were on the face — nothing to do either way. More than one is a mapping
                # problem reported elsewhere, and decomposing an ambiguous total would compound it.
                if len(parents) > 1:
                    ctx.log(f"map_ontology:split_declined({aggregate}):"
                            f" {len(parents)} rows carry it")
                continue
            parent = parents[0]
            if any(li.canonical_key in children for li in doc.line_items):
                continue                  # a component is already filed; containment owns this case
            cited = _cited_notes([parent])
            tables = [t for t in doc.notes if str(t.note_number) in cited]
            if not tables:
                continue
            statement = stmt_by_page.get(_page_of(parent) or -1)
            rows = [it for t in tables for it in t.items
                    if it.values and it.role is LineRole.LINE]
            hits: dict[str, list] = {}
            for it in rows:
                res = matcher.match(it.raw_label or "", statement=statement, section=section)
                if res and res.canonical_key in children:
                    hits.setdefault(res.canonical_key, []).append(it)
            if len(hits) < 2:
                ctx.log(f"map_ontology:split_declined({aggregate}):"
                        f" the disclosure itemises {len(hits)} of this section's concepts")
                continue
            short = _columns_not_accounted_for(parent, hits, tol)
            if short:
                ctx.log(f"map_ontology:split_declined({aggregate}):"
                        f" components do not account for it in {','.join(short)}")
                continue
            for key, sources in hits.items():
                row = LineItem(source_label=sources[0].raw_label or key, canonical_key=key,
                               ordinal=parent.ordinal, role=LineRole.LINE,
                               printed_in=PrintedIn.FACE, note_number=parent.note_number,
                               section_hint=parent.section_hint)
                for col, total in _summed_columns(sources).items():
                    ev = total[1].model_copy(deep=True)
                    ev.value, ev.value_raw = total[0], total[0]
                    row.set_value(ev)
                row.confidence.mapping = min(parent.confidence.mapping or 0.75, 0.75)
                row.confidence.method = MappingMethod.RULE.value
                row.confidence.flags.append(f"split_from:{aggregate}")
                if len(sources) > 1:
                    # The disclosure itemises finer than the template does, so this concept's figure
                    # is the sum of several disclosed rows. Said on the row, because its provenance
                    # can only point at one of them.
                    row.confidence.flags.append(f"split_summed_rows:{len(sources)}")
                doc.line_items.append(row)
                added += 1
            parent.canonical_key = None
            if parent.role is LineRole.LINE:
                parent.role = LineRole.SUBTOTAL
            parent.confidence.flags.append(
                f"alloc:{AllocationStatus.PARENT_GROSS_EVIDENCE_ONLY.value}")
            parent.confidence.flags.append(f"decomposed_into:{','.join(sorted(hits))}")
            ctx.log(f"map_ontology:split({aggregate}) into {len(hits)} concepts"
                    f" from note {','.join(sorted({str(t.note_number) for t in tables}))}")
        return added

    @staticmethod
    def _infer_sole_components(doc: DocumentModel, ontology, ctx: PipelineContext) -> int:
        """A subtotal printed ALONE collapses onto the child the rulebook names, if nothing else is
        evidenced. The mirror image of containment above. Returns rows added.

        ``sole_component_of`` on a concept names the subtotal it is the sole component of. An HKEX
        income statement routinely prints one undifferentiated tax line — "Income tax
        credit/(expenses) 3,159" on the filing this was measured against — and splits current from
        deferred only in the tax note, or not at all. Left as the subtotal alone, the template's two
        tax children stay empty and the analyst has a total with nothing under it.

        Nothing is divided here, which is why ``global_rules.no_fabricated_split`` still holds and
        says so: the whole figure goes to one child, and it is refused the moment any SIBLING child
        is evidenced — mapped on the face, printed on the face under its own caption whether the
        mapper claimed it or not, or named in the subtotal's own cited note. That last one matters
        because the note is where the split usually is: a filing that discloses deferred tax in the
        note has a split, and asserting the whole charge is current would be a fabrication.

        The inferred row carries the subtotal's own figures and provenance, so click-to-source lands
        on the printed line it came from, and it is flagged ``inferred_sole_component`` — never
        mistakable for a caption the filing printed.
        """
        declared = [(m, m.sole_component_of) for m in ontology.mappings if m.sole_component_of]
        if not declared:
            return 0
        members = section_members(ontology)
        added = 0
        for concept, aggregate in declared:
            section = concept.section_scope[0] if concept.section_scope else ""
            siblings = [k for k in (members[section].dedicated if section in members else [])
                        if k != concept.canonical_key]
            parents = [li for li in doc.line_items if li.canonical_key == aggregate]
            if not parents:
                continue
            if any(li.canonical_key in (concept.canonical_key, *siblings)
                   for li in doc.line_items):
                ctx.log(f"map_ontology:sole_component_declined({concept.canonical_key}):"
                        " a child of the subtotal is already filed")
                continue
            evidence = _sibling_evidence(doc, ontology, parents, siblings)
            if evidence:
                ctx.log(f"map_ontology:sole_component_declined({concept.canonical_key}):"
                        f" {evidence}")
                continue
            if len(parents) > 1:
                # Two rows filed on one subtotal is a mapping problem of its own, reported
                # elsewhere; inferring a child from an ambiguous total would compound it.
                ctx.log(f"map_ontology:sole_component_declined({concept.canonical_key}):"
                        f" {len(parents)} rows carry {aggregate}")
                continue
            parent = parents[0]
            row = LineItem(source_label=concept.label or concept.canonical_key,
                           canonical_key=concept.canonical_key,
                           ordinal=parent.ordinal, role=LineRole.LINE,
                           note_number=parent.note_number)
            for ev in parent.values.values():
                row.set_value(ev.model_copy(deep=True))
            row.confidence.mapping = min(parent.confidence.mapping or 0.75, 0.75)
            row.confidence.method = MappingMethod.RULE.value
            row.confidence.flags.append(f"inferred_sole_component:{aggregate}")
            doc.line_items.append(row)
            added += 1
            ctx.log(f"map_ontology:sole_component({concept.canonical_key}) from {aggregate}"
                    f" columns={len(row.values)}")
        return added

    @staticmethod
    def _check_equivalence(doc: DocumentModel, ontology, ctx: PipelineContext) -> int:
        """Two captions the rulebook declares to be ONE fact must not disagree in silence.

        ``equivalence`` ("Net assets" ↔ "Total equity", relation
        ``identical_reported_amount``) says: populate whichever is printed, and "If both are printed
        and differ, route to review — do not average or pick." Both printed and EQUAL is the ordinary
        case and is left alone; both printed and different is a genuine finding — one of the two rows
        is mis-mapped, or the filing itself does not tie — and it is invisible otherwise, because
        each row is individually plausible and each subtotal it feeds still balances.

        Compared per (basis, period) column, and only beyond ``recon_abs_tolerance``: the same figure
        rounded in two places is not a disagreement.
        """
        tol = Decimal(str(ctx.settings.extraction.recon_abs_tolerance))
        pairs = {tuple(sorted((m.canonical_key, m.equivalence.with_)))
                 for m in ontology.mappings
                 if m.equivalence is not None and m.equivalence.with_}
        conflicts = 0
        for a, b in sorted(pairs):
            rows_a = [li for li in doc.line_items if li.canonical_key == a]
            rows_b = [li for li in doc.line_items if li.canonical_key == b]
            if not rows_a or not rows_b:
                continue                       # only one caption printed: nothing to disagree with
            for la in rows_a:
                for lb in rows_b:
                    for col, va in la.values.items():
                        vb = lb.values.get(col)
                        if vb is None or va.value is None or vb.value is None:
                            continue
                        if abs(va.value - vb.value) <= tol:
                            continue
                        for li, other in ((la, b), (lb, a)):
                            flag = f"equivalence_conflict:{other}"
                            if flag not in li.confidence.flags:
                                li.confidence.flags.append(flag)
                                li.confidence.flags.append("low_mapping_confidence")
                        conflicts += 1
                        ctx.log(f"map_ontology:equivalence_conflict {a}={va.value}"
                                f" {b}={vb.value} column={col}")
        return conflicts
