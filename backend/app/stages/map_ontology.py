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
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation

import app.adapters  # noqa: F401 - registers configured LLM adapters
from app.core.models import DocumentModel
from app.core.models.enums import (AllocationStatus, Basis, LineRole, MappingMethod,
                                   PrintedIn)
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services import note_context
from app.services.caption_shape import prose_reasons
from app.services.mapping import (
    OntologyMatcher,
    normalize_label,
    normalize_statement,
    section_of_banner,
)
from app.ports.registry import registry
from app.services.rollups import section_members


_MATRIX_COLUMN = re.compile(r"^(?:current|prior|col\d+)$", re.IGNORECASE)
_CLOSING_BALANCE = re.compile(r"^(?:at\b|closing\s+(?:balance|net)|net\s+(?:book|carrying))",
                              re.IGNORECASE)


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


def _orientation_accounting_for(parent, hits: dict, tol: Decimal) -> tuple[int, list[str]]:
    """Which sign orientation makes the disclosed components account for the aggregate.

    Returns ``(orientation, columns still not accounted for)``. The orientation is ``1`` when the
    components sum to the aggregate exactly as the note printed them and ``-1`` when their NEGATION
    does; the column list is empty only when ONE orientation accounts for EVERY column the aggregate
    carries a figure in. ``(0, ...)`` means neither did.

    This is the arithmetic support ``global_rules.parent_child_allocation`` asks for, and the only
    one of its four kinds this stage can test. A column the components are silent about is a
    failure, not a column to skip: publishing components for one period and nothing for the other
    leaves the other period's figure deleted from the statement.

    WHY A FLIP IS LEGITIMATE AT ALL, and why it is not a liberty taken with a reported number. A
    note is a schedule OF a charge and prints the charge as a positive amount. The face presents
    that same charge in the flow of the statement it sits in, which for a P&L expense means
    negative -- "Income tax expense (189,504)". Neither is wrong. What decides which orientation the
    template wants is THE TEMPLATE'S OWN ROLLUP: it makes profit for the year the SUM of profit
    before tax and the tax line, which only holds with the tax carried negative (on the filing this
    was measured against, -8,211,620 + -189,504 = -8,401,124, the printed figure). So the
    AGGREGATE'S PRINTED SIGN is the convention of record -- the arithmetic the template asserts is
    written against it -- and components published in the note's own orientation would break the
    very subtotal they were read in order to satisfy.

    ONE ORIENTATION FOR EVERY COLUMN, NEVER ONE PER COLUMN. Flipping per column would accept a
    disclosure whose components are right in one period and sign-wrong in the other, which is
    precisely the error this gate exists to catch. Requiring a single orientation keeps the flip a
    statement about PRESENTATION -- true of the whole disclosure or of none of it -- and leaves a
    real per-column sign error failing, as it should.

    A ONE-COLUMN FILING GETS LESS PROTECTION FROM THIS, unavoidably. With a single period there is
    nothing for the orientation to be consistent WITH, so components of the right magnitude and the
    wrong sign cannot be told from a presentation difference. What still corroborates them there is
    the magnitude itself, which is what the tolerance tests, and the same figures have to satisfy
    the section's rollup afterwards.
    """
    totals: dict[tuple[str, str], Decimal] = {}
    for sources in hits.values():
        for col, (value, _ev) in _summed_columns(sources).items():
            totals[col] = totals.get(col, Decimal(0)) + value

    def unaccounted(orientation: int) -> list[str]:
        out: list[str] = []
        for ev in parent.values.values():
            if ev.value is None:
                continue
            col = (ev.basis.value, ev.period_label or "")
            got = totals.get(col)
            if got is None or abs(Decimal(ev.value) - orientation * got) > tol:
                out.append(f"{col[0]}/{col[1]}")
        return out

    as_disclosed = unaccounted(1)
    if not as_disclosed:
        return 1, []
    if not unaccounted(-1):
        return -1, []
    # Neither orientation accounts for it. The failure reported is the AS-DISCLOSED one, because
    # that is the comparison an analyst can repeat against the printed note without first being
    # told that a flip was tried and also failed.
    return 0, as_disclosed


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
      rulebook overrides it: ``hkfrs_hk_china_ontology.json`` states the policy as a section default
      — "Concepts default to face_only: true. Notes are evidence for a face amount, never an
      independent source of one, unless note_use is decomposition_allowed" — and then names its one
      exception on the tax section, with the reason: "HKEX filings routinely print only 'Income tax
      expense' on the face and split current/deferred in the tax note. Decomposition from that note
      is permitted because the split is a reconciliation of the face."

      WHICH RULEBOOK, MEASURED, because "the shipped rulebook" reads as one file and is two.
      Against ``hkfrs_hk_china_ontology.json`` + ``hkfrs_hk_china_template.json`` this returns
      exactly 1 pair — the tax aggregate over current/deferred tax — which is that author's stated
      intent rather than a limitation of this function. Against the pair that drives the output CSV,
      ``output_csv_hk_ontology.json`` + ``output_csv_hk_v1_template.json``, it returns 52, because
      there ``note_use: decomposition_allowed`` is the SECTION DEFAULT on 394 of 462 concepts rather
      than one named exception. So the "one exception" reading is an hkfrs property, not a property
      of this function, and the gates listed at the bottom of this docstring — not the rarity of the
      opt-in — are what keep the arm safe on the output_csv pair.

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


def _apply_prose_value(li, result, prose: list[dict]) -> None:
    """Put a prose-stated figure on the row, WITHOUT throwing away what the page printed.

    THE PRINTED FIGURE IS KEPT. A row reaching here already carries the amount its own line
    printed, and the prose figure is a different quantity — on laisun.pdf the row prints the TOTAL
    depreciation and the footnote states the operating-expense SHARE of it. Overwriting silently
    would lose the number a reader can see on the page, so the printed one stays in `value_raw`,
    the prose one becomes `value`, and the trail carries the sentence plus what was displaced.

    ONE SLOT, NOT ALL OF THEM. The sentence says which LINE the figure belongs to, not which
    column, so it is written to the row's current-period slot (or its only slot) and never
    broadcast across periods — a footnote's single amount spread over two years would assert a
    figure for a year the filing never gave one.
    """
    from app.services import derivation

    best = next((s for s in prose if s.get("figures", {}).get("prose")), None)
    if best is None:
        return
    try:
        amount = Decimal(str(best["figures"]["prose"]))
    except (InvalidOperation, ValueError, TypeError):
        return

    slots = list((li.values or {}).values())
    target = next((ev for ev in slots if str(getattr(ev, "period_label", "") or "") == "current"),
                  slots[0] if slots else None)
    if target is None:
        # No printed slot at all: the row exists only as a caption, so the prose figure is the
        # only figure it will ever have.
        li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current", value=amount,
                                    value_raw=amount))
        target = next(iter(li.values.values()))
    else:
        printed = target.value if target.value is not None else target.value_raw
        if printed is not None and printed != amount:
            li.confidence.flags.append(f"prose_value_displaced_printed:{printed}")
        if target.value_raw is None:
            target.value_raw = printed if printed is not None else amount
        target.value = amount

    basis = str(getattr(getattr(target, "basis", ""), "value", getattr(target, "basis", "")) or "")
    period = str(getattr(target, "period_label", "") or "")
    li.derivation = derivation.record(
        li.derivation, basis=basis, period_label=period,
        derivation=derivation.build(
            method="prose_sourced", formula=None,
            inputs=[{"label": s.get("caption") or f"note {s.get('note')}",
                     "note": s.get("note"), "value": str(s.get("figures", {}).get("prose", "")),
                     "provenance": s.get("provenance"),
                     # THE SENTENCE, verbatim. It is the only evidence for this figure, so the
                     # inspector shows a reviewer the words rather than asking them to trust it.
                     "excerpt": s.get("quote") or "",
                     "counted": True, "deducted": False}
                    for s in prose],
            result=amount, flags=["prose_sourced", f"note:{best.get('note')}"]))
    li.confidence.flags.append(f"prose_sourced_value:{best.get('note')}")
    li.is_computed = True

def _apply_result(li, result) -> bool:
    """Write one mapping decision onto a row. THE ONE PLACE A ROW IS WRITTEN.

    Hoisted out of `run()` unchanged when there were briefly two engines to choose between, so
    that neither could start flagging differently — one of them forgetting
    `low_mapping_confidence`, say, so a doubtful row reads as certain on one engine and not the
    other. There is now a single configuration engine (the line-item set), so the second caller
    is gone; the function stays hoisted and stays the only writer, which is the property that
    was worth having. It closes over nothing but its two arguments.
    """
    # Secur & Other Fincl Assets(CP)/(LTP) used to bind directly here on a bare "Financial
    # assets at fair value through profit or loss" face caption, at whatever figure was
    # printed. The rulebook's `extraction_mode` governs instead, and on the shipped set both
    # are "derive", so a face row with that caption is left unmatched here — the same rule
    # "derive" already gives every other computed concept. No service computes them any more:
    # their figures have to arrive from the configuration's own note sources.
    if result and result.canonical_key:
        li.canonical_key = result.canonical_key
        li.confidence.mapping = result.confidence
        li.confidence.method = result.method.value
        if result.allocation_status:
            li.confidence.flags.append(f"alloc:{result.allocation_status}")
        if result.method is MappingMethod.LLM and result.reason:
            # The model's own stated justification, surfaced (not just logged) so a reviewer
            # asking why a caption was mapped — or merged with others under one concept —
            # sees the reasoning behind the decision, not only its score.
            li.confidence.flags.append(f"llm_reason:{result.reason}")
        if result.rerouted_from:
            # The concept whose caption matched is not the one the row was filed under: the
            # section banner named a different variant of the same fact. Recorded per row,
            # because a reviewer looking at the comprehensive-income bottom line needs to
            # see that the caption on the page said "loss for the year".
            li.confidence.flags.append(f"section_reroute_from:{result.rerouted_from}")
        if getattr(result, "role", "whole") == "component":
            # ONE PART OF THE FIGURE, NOT THE FIGURE. A line item's amount is often the sum of
            # several printed rows — a note splitting depreciation by function prints four, and
            # all four belong on the operating-expense line. The rows are left where they are and
            # marked; `services.assemble_components` adds them up afterwards and writes the
            # derivation trail, because summing here would mean each row overwriting the last as
            # the loop reached it.
            #
            # MARKED RATHER THAN INFERRED. "Two rows share a key" cannot distinguish a genuine
            # component from a face line repeating the note total behind it — the second is the
            # same figure twice and adding it overstates the line. So only a row the model
            # DECLARED a component is summed, and two undeclared rows on one key stay the
            # `ambiguous_mapping` they have always been.
            li.confidence.flags.append(f"component_of:{result.canonical_key}")
            li.confidence.flags.append(f"component_sign:{result.sign}")
        # A FIGURE THE FILING STATES ONLY IN PROSE. `sources` entries flagged `prose` carry an
        # amount verified against the note's own text (`note_sourced._amount_in_text`), which is
        # what separates a figure the model LOCATED from one it supplied. It is written onto the
        # row because that is the only way it reaches the statement screens and the export — every
        # one of them reads `_serialize_rows`, and a value that is not on a row is not in that
        # list. Being flagged for review does not hold it back: nothing downstream suppresses a
        # reviewed value, which is what makes "print it and export it, and review it too"
        # achievable at once rather than a choice.
        prose = [s for s in (getattr(result, "sources", None) or ()) if s.get("prose")]
        if prose:
            _apply_prose_value(li, result, prose)
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


def _focus_answerability(matcher, focus_keys: set[str]) -> str:
    """The focus keys the loaded rulebook CANNOT return, as log fields. Observation only.

    A key in ``matcher._unmatchable`` — a locked residual (``alias_matching: disabled``) or a
    computed concept (``extraction_mode: derive``) — is unreachable by every deterministic tier AND
    absent from the LLM candidate payload (``_candidate_specs`` skips the same set). It still passes
    the section gate in `run`, so its rows are forwarded to the provider and paid for, and the answer
    the focus run was configured to get can never come back.

    That was invisible: the focus log reported ``keys=8`` and nothing else. Measured against the
    shipped ``output_csv_hk_ontology.json`` and the eight keys in ``config.toml``, four of them are
    unmatchable, so a run advertising eight focus concepts could return four.

    ``_unmatchable`` is read directly (a private attribute) rather than reimplemented from the
    definition, so this reports what the MATCHER actually excluded and cannot drift from it.
    """
    unmatchable = sorted(k for k in focus_keys if k in matcher._unmatchable)
    return (f"focus_keys_unmatchable={unmatchable}"
            f" focus_keys_answerable={len(focus_keys) - len(unmatchable)}")


def _alias_locale_coverage(matcher, locale: str | None) -> tuple[int, int, int]:
    """``(matchable, with_alias, llm_only)`` concepts for the document's locale. Observation only.

    HOW MANY OF THE RULEBOOK'S CONCEPTS CAN BE REACHED DETERMINISTICALLY IN THE SCRIPT THIS
    FILING IS PRINTED IN. A concept with no alias in the document's locale is not unreachable —
    the LLM tier still sees it in the candidate payload — but nothing about it is DECIDED by
    string evidence, so every row that should land on it depends on the model. That is a
    materially different quality level from a row an exact alias settled at 1.0, and until now
    the run record said nothing about it either way.

    Measured on the shipped ``output_csv_hk_ontology.json`` (462 concepts, 445 matchable): 287 of
    the 445 carry no ``aliases_i18n["zh"]`` at all (286 of those are Han-free outright), against
    ``hkfrs_hk_china_ontology.json``'s 169 of 169 bilingual. On a Chinese-only HK/PRC face
    statement that is the majority of the rulebook resting on the model alone.

    Counted over the MATCHABLE concepts only — ``matcher._unmatchable``, read directly for the
    same reason ``_focus_answerability`` does: a locked residual and a ``derive`` concept are
    indexed by no tier, so an alias on one of them describes nothing the run can use, and
    including them would report coverage the matcher does not have.

    ``en`` (and an undetected locale) additionally counts the locale-neutral ``aliases`` list,
    which is English by construction — see ``OntologyMapping.aliases_for``. No other locale gets
    that fallback, which is exactly what makes the figure for ``zh`` worth printing.

    The locale is used as an ``aliases_i18n`` key unnormalised, which is safe because
    ``stages.language.detect_locale`` only ever returns a bare ``en``/``zh``/``ar``/``fr`` — the
    same key space the rulebooks author. A region subtag would need folding first.
    """
    keys = [k for k in matcher._by_key if k not in matcher._unmatchable]
    neutral = locale in (None, "", "en")
    with_alias = 0
    for k in keys:
        m = matcher._by_key[k]
        if m.aliases_i18n.get(locale or "en") or (neutral and m.aliases):
            with_alias += 1
    return len(keys), with_alias, len(keys) - with_alias


class MapOntologyStage:
    name = "map_line_items"

    @staticmethod
    def _promote_reconciled_matrix_closings(doc: DocumentModel, matcher: OntologyMatcher,
                                            statement_of, ctx: PipelineContext) -> int:
        """Promote mapped category columns only when their closing row ties to its face parent."""
        parents_by_note: dict[str, list] = {}
        for parent in doc.line_items:
            for note_number in _cited_notes([parent]):
                parents_by_note.setdefault(note_number, []).append(parent)

        promoted = 0
        tolerance = Decimal(str(ctx.settings.extraction.recon_abs_tolerance))
        for table in doc.notes:
            parents = parents_by_note.get(str(table.note_number), [])
            if len(parents) != 1:
                continue
            parent = parents[0]
            statement = statement_of(parent)
            headers = {ev.period_label for item in table.items for ev in item.values.values()
                       if ev.period_label and not _MATRIX_COLUMN.match(ev.period_label)}
            mapped_headers = {
                header: matcher.match(header, statement=statement, section=parent.section_hint)
                for header in headers
            }
            mapped_headers = {header: result for header, result in mapped_headers.items()
                              if result and result.canonical_key}
            if len(mapped_headers) < 2:
                continue
            parent_values = [(key, value) for key, value in parent.values.items()
                             if value.value is not None]
            used_parent_columns: set[str] = set()
            for item in table.items:
                if item.role is not LineRole.LINE or not _CLOSING_BALANCE.match(item.raw_label or ""):
                    continue
                cells = [(ev.period_label, mapped_headers[ev.period_label].canonical_key, ev)
                         for ev in item.values.values() if ev.period_label in mapped_headers
                         and ev.value is not None]
                total = sum((ev.value for _header, _key, ev in cells), Decimal(0))
                matches = [(key, value) for key, value in parent_values
                           if key not in used_parent_columns and abs(value.value - total) <= tolerance]
                if len(matches) != 1:
                    continue
                parent_column, parent_value = matches[0]
                grouped: dict[str, list] = {}
                for header, key, ev in cells:
                    grouped.setdefault(key, []).append((header, ev))
                for key, values in grouped.items():
                    row = LineItem(source_label=values[0][0], canonical_key=key, ordinal=parent.ordinal,
                                   role=LineRole.LINE, printed_in=PrintedIn.FACE,
                                   note_number=str(table.note_number), section_hint=parent.section_hint)
                    value = sum((ev.value for _header, ev in values), Decimal(0))
                    source = values[0][1].model_copy(deep=True)
                    source.value_raw = value
                    source.value = value
                    source.period_label = parent_value.period_label
                    source.period_end = parent_value.period_end
                    source.period_display = parent_value.period_display
                    row.set_value(source)
                    row.confidence.mapping = min(parent.confidence.mapping or 0.75, 0.75)
                    row.confidence.method = MappingMethod.RULE.value
                    row.confidence.flags.extend((f"matrix_category:{item.raw_label}",
                                                 f"matrix_column:{key}",
                                                 f"split_from:{parent.canonical_key}"))
                    doc.line_items.append(row)
                    promoted += 1
                used_parent_columns.add(parent_column)
            if used_parent_columns:
                parent.role = LineRole.SUBTOTAL
                parent.confidence.flags.append("note_matrix_decomposed")
                parent.canonical_key = None
        return promoted

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        # The run's LLM call count BEFORE this stage. Captured once so the live figure published
        # from inside the batch loop and the roll-up at the end are the same assignment, rather
        # than an assignment followed by an accumulation that counts every call twice.
        _calls_base = ctx.llm_calls
        ontology = getattr(ctx, "ontology", None)
        if ontology is None or not doc.line_items:
            ctx.log("map_line_items:skipped(no ontology or no line items)")
            return doc

        llm_provider = None
        unavailable_reason = ""
        provider_id = getattr(ctx.settings.llm, "provider", "stub")
        if provider_id != "stub":
            try:
                llm_provider = registry.get("llm", provider_id)
            except Exception as exc:  # noqa: BLE001
                unavailable_reason = f"llm provider unavailable: {type(exc).__name__}: {exc}"
        else:
            unavailable_reason = "stub llm provider configured"

        # A second engine used to be selectable here (`extraction.mapping_engine`), branching to a
        # deterministic-only `_map_from_line_items`. Both the setting and that method are gone: the
        # line-item set is now the ONE configuration engine, so there is nothing to select between
        # and this stage always runs the full tier stack below. Do not reinstate the branch — a
        # second path is how two engines start deciding the same row differently.
        matcher = OntologyMatcher(ontology, locale=doc.locale, settings=ctx.settings,
                                  llm_provider=llm_provider)
        # Report the rulebook's per-locale alias coverage BEFORE any row is mapped, because it is a
        # property of the loaded file and the detected script, not of the outcome — it says up front
        # how much of this run can be settled deterministically. Carried onto the matcher's usage
        # counters too, so it sits beside the other roll-ups a reader of the run record already
        # consults rather than only in a log line. Observation only: nothing branches on it.
        _cov_matchable, _cov_with_alias, _cov_llm_only = _alias_locale_coverage(matcher, doc.locale)
        matcher.usage["alias_locale_matchable"] = _cov_matchable
        matcher.usage["alias_locale_with_alias"] = _cov_with_alias
        matcher.usage["alias_locale_llm_only"] = _cov_llm_only
        ctx.log(f"map_line_items:alias_locale_coverage locale={doc.locale or 'unknown'}"
                f" matchable={_cov_matchable} with_alias={_cov_with_alias}"
                f" llm_only={_cov_llm_only}")
        scope = ctx.settings.extraction.mapping_scope
        # TEMPORARY (focus-run routing) — see settings.extraction.llm_focus_keys, and remove both
        # together. When set, only rows that could be one of those concepts are forwarded to the
        # model; every other row keeps its DETERMINISTIC answer (the exact/alias tiers), which is
        # precisely what `llm_only_keys` destroys. `det_matcher` is provider-less — the same
        # construction `_match_chunk` already makes internally for its deterministic evidence.
        focus_keys = (set(ctx.settings.extraction.llm_focus_keys or ())
                      if getattr(ctx.settings.extraction, "llm_focus_only", False) else set())
        if getattr(ctx.settings.extraction, "llm_focus_only", False) and not focus_keys:
            # Asked to restrict and given nothing to restrict TO. Left unreported this would read
            # as a normal full run, so say it rather than silently ignoring the switch.
            ctx.log("map_line_items:focus_only_requested_but_no_focus_keys_configured")
        elif focus_keys:
            # OBSERVATION ONLY. A focus key naming a concept in `matcher._unmatchable` (a swept
            # residual or a `derive` concept) is a key the run CANNOT ANSWER: it is out of every
            # deterministic tier AND out of the LLM candidate payload (`_candidate_specs` skips
            # `_unmatchable`), yet it still passes the section gate below, so its rows are forwarded
            # and paid for and the concept can never come back. Measured against the shipped
            # `output_csv_hk_ontology.json` and the 8 keys in config.toml, 4 are in that state
            # (is_pl__deprec_and_impairment_oper_exp, is_pl__deprec_and_impairment_cos,
            # bs_nca__secur_and_other_fincl_assets_ltp, bs_ca__other_receivables_cp) — a focus run
            # that reported "keys=8" was reporting twice the concepts it could return. Logged HERE
            # as well as in the focus_routing line, because that line is on the BATCHED path only
            # and the per-line path below would otherwise say nothing about focus at all.
            ctx.log(f"map_line_items:focus_answerability {_focus_answerability(matcher, focus_keys)}")
        det_matcher = (OntologyMatcher(ontology, locale=doc.locale, settings=ctx.settings)
                       if focus_keys else None)
        focus_det = focus_llm = focus_sections_skipped = 0
        # PROSE CAPTIONS (extraction.skip_prose_captions, DEFAULT OFF). The per-line note pass
        # below runs over every LINE row of every extracted note, and on a real filing that means
        # sentences — 587 of 627 note rows on the reference English filing reached no concept,
        # the longest 419 characters. Each was its own paid call asking which balance-sheet
        # concept a sentence fragment is. See services.caption_shape for why the test is
        # script-aware rather than a character count.
        skip_prose = bool(getattr(ctx.settings.extraction, 'skip_prose_captions', False))
        prose_skipped: list[tuple[str, list[str]]] = []
        # …and the PER-LINE call sites. `match()` consults the provider even when an exact alias
        # already hit (the exact tier only short-circuits when the matcher has no provider), and
        # the note-item loop below runs over every LINE row of every extracted note — on a real
        # filing that is hundreds of single-row provider calls, which would dwarf the batch path
        # this routing exists to shrink. Under focus routing they use the provider-less matcher:
        # the focus concepts reach the model only through the batch path above, and
        # `notes__contingent_liabilities` is aliased exactly ("Contingent liabilities" resolves
        # deterministically at 1.0).
        line_matcher = det_matcher or matcher
        # Record the strategy for the run record: mapping by MEANING (LLM) and mapping by
        # string/rule evidence are very different quality levels, and the difference has to be
        # visible to whoever reads the output.
        # Provisional: confirmed after the run, because a provider can resolve (the adapter
        # constructs fine) and still fail every call — e.g. no API key. Claiming
        # "llm_description" on a run that made zero successful calls would overstate it.
        #
        # THE LABEL DESCRIBES THE PROVIDER, NOT THE ROUTING — and under focus routing those are
        # not the same run. config.toml ships `llm_focus_only = true` with 8 focus keys
        # (config.toml:152-183) against the 462 concepts of output_csv_hk_ontology.json, so all
        # but a handful of rows are settled by the deterministic tiers and never reach the model;
        # `matcher.llm_enabled` knows nothing about that, so "llm_description" on its own
        # reported a mostly-deterministic run as an LLM-mapped one.
        #
        # NOT RENAMED HERE, ON PURPOSE. frontend/src/screens/ExtractionView.tsx:1063 switches on
        # the LITERAL "llm_description" to decide whether to warn "No language model was
        # configured for this run" — so a new label shipped without the paired case there would
        # put that falsehood on screen for every default-configured run, which is a worse untruth
        # than the one being fixed. The correction rides in the REASON instead: it is the only
        # other mapping field the API serialises (routes/extractions.py:1358-1363), and it is
        # filled in with the measured row split at the focus_routing report below.
        ctx.mapping_strategy = "llm_description" if matcher.llm_enabled else "deterministic"
        ctx.mapping_strategy_reason = "" if matcher.llm_enabled else (
            unavailable_reason or "no llm provider resolved")
        ctx.log(f"map_line_items:strategy={ctx.mapping_strategy}(intended) scope={scope}"
                + (f" reason={ctx.mapping_strategy_reason}" if ctx.mapping_strategy_reason else ""))

        _apply = _apply_result

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
            # EVERY note and face row of the filing, with the IDF weights this filing's own
            # vocabulary implies. Built once and shared by every subgroup: document frequency is a
            # property of the document, so computing it per batch would make a note's weight depend
            # on which batch happened to be asking.
            context_pool = note_context.build_pool(doc, stmt_by_page)
            # THE NOTES THE CONFIGURATION NAMES, in full — rows and prose. Computed once for the
            # document and passed on every request: an author's `note_source` declaration is a
            # stronger statement of relevance than any similarity score, and the figure a footnote
            # states is reachable no other way.
            identified = note_context.identified_notes(
                getattr(ctx, "line_items", None), doc.notes)
            # THE SUB-LINE ITEMS THE MODEL MAY ANSWER WITH. Every focus concept is `derived` and
            # computed by cascade from these, so a sub-item is the layer that corresponds to a
            # printed note row — and the model learns their keys from each identified note's
            # `identified_for`.
            _cfg_items = getattr(getattr(ctx, "line_items", None), "items", None) or ()
            sub_item_keys = {i.key for i in _cfg_items if getattr(i, "parent", "")}
            # COMPUTED PARENTS, off the configuration: a declared cascade IS the statement that
            # this line's figure is arithmetic over its sub-lines, so the model is not asked about
            # it and an answer naming it is refused.
            computed_keys = {i.key for i in _cfg_items if getattr(i, "cascade", None)}
            if identified:
                ctx.log(f"map_line_items: {len(identified)} note(s) identified by configuration "
                        f"passed in full "
                        f"({sum(len(n.get('prose','')) for n in identified):,} chars of prose)")
            batched = unstated = 0
            # Each (statement, section) subgroup is an independent `match_batch` call — candidates
            # are scoped to its own statement and section banner, so nothing about one subgroup's
            # decision depends on another's. Collected here rather than called inline so every
            # subgroup across the WHOLE document can be dispatched to a bounded thread pool at
            # once: the run's LLM wall-clock time becomes roughly the slowest subgroup instead of
            # the sum of every one of them.
            tasks: list[tuple[str, list]] = []
            for statement, group in groups:
                if statement is None:
                    # No statement means no statement-scoped candidate list and no coherent
                    # neighbourhood, so these are decided one at a time rather than handed to a
                    # batch as if they were a statement.
                    unstated += len(group)
                    for li in group:
                        if _apply(li, line_matcher.match(li.source_label,
                                                         section=li.section_hint)):
                            mapped += 1
                    continue
                batched += len(group)
                # WHICH NOTES EACH ROW REFERENCES — the printed citation, which the selector treats
                # as evidence and admits unconditionally. Everything else it offers is chosen by
                # subject (`services.note_context`); the pool itself is built once for the whole
                # document, outside this loop, because IDF is a property of the filing.
                #
                # No longer restricted to the balance sheet and P&L. That restriction dated from
                # when the context was the full text of a cited note — unbounded, so it was only
                # affordable on two statements. The selector is bounded per row, so the cash-flow
                # and equity statements now get the same context they always needed: a cash-flow
                # line and the face row it reconciles to are exactly the relationship this shows.
                cited_notes = {str(line.id): _cited_notes([line]) for line in group}
                # Subsection-by-subsection: each statement batch is split by printed section banner.
                # Rows whose banner is unresolved are mapped together in an unconstrained subgroup.
                by_section: dict[str | None, list] = {}
                for li in group:
                    by_section.setdefault(section_of_banner(li.section_hint), []).append(li)
                for _section, subgroup in by_section.items():
                    if not focus_keys:
                        tasks.append((statement, subgroup, cited_notes))
                        continue
                    # TEMPORARY (focus-run routing). Two gates, cheapest first.
                    #
                    # (1) SECTION GATE: if no focus concept can claim a row in this
                    # (statement, section) at all, the subgroup cannot contain a focus item, so it
                    # is decided deterministically and costs no provider call. The scope test is
                    # the same one `_match_chunk` applies to its candidate list — statement, then
                    # the declared `section_scope` — so a subgroup is never skipped on a stricter
                    # rule than the mapper itself would use. An unresolved banner (`_section is
                    # None`) is treated as unconstrained, exactly as `_match_chunk` treats it.
                    in_scope = [k for k in focus_keys
                                if matcher._in_statement(k, statement)
                                and (_section is None
                                     or not matcher._sections_of(k)
                                     or _section in matcher._sections_of(k))]
                    if not in_scope:
                        for li in subgroup:
                            if _apply(li, det_matcher.match(
                                    li.source_label, statement=statement,
                                    section=li.section_hint)):
                                mapped += 1
                            focus_det += 1
                        focus_sections_skipped += 1
                        continue
                    # (2) ROW GATE: decide every row deterministically first. A row the
                    # deterministic tiers place on a NON-focus concept is already answered — keep
                    # that answer and spend nothing on it. A row that resolves to a focus concept
                    # (confirm/correct it) or resolves to NOTHING (the case that matters: a focus
                    # caption the rulebook has no alias for, e.g. Sales(Revenues), which would
                    # otherwise be lost) is forwarded to the model.
                    llm_rows = []
                    for li in subgroup:
                        res = det_matcher.match(li.source_label, statement=statement,
                                                section=li.section_hint)
                        if res and res.canonical_key and res.canonical_key not in focus_keys:
                            if _apply(li, res):
                                mapped += 1
                            focus_det += 1
                        else:
                            llm_rows.append(li)
                    if llm_rows:
                        tasks.append((statement, llm_rows, cited_notes))
                        focus_llm += len(llm_rows)

            # ONE CHUNK'S SIZE, read once and threaded into BOTH things it decides: the size
            # `match_batch` actually cuts a subgroup into, and the call-count denominator below.
            # Hoisted above `_run_task` because it is now an ARGUMENT to the call and not merely a
            # number to report.
            #
            # IT USED TO BE REPORT-ONLY. `match_batch` was called with no `chunk_size`, so
            # `size = chunk_size or self.BATCH_MAX_ITEMS` (services/mapping.py) fell through to the
            # class constant 25 on every run, and this was the single place the setting was read.
            # `llm_batch_max_items = 10`, set for a gateway that truncates a 25-decision reply,
            # therefore bought a log line claiming `chunk_size=10` and a progress bar planned for
            # 10 while the provider went on receiving 25 and went on truncating — and a truncated
            # batch is not a partial answer: the JSON fails to parse and the whole chunk drops to
            # the weaker per-line path, on a run that still calls itself LLM-mapped.
            #
            # SO IT IS A SEMANTIC KNOB, not a transport one, and lowering it re-baselines a
            # deployment: `_match_chunk` derives its section tokens from the rows in THIS chunk and
            # narrows the candidate list by them, and seeds the never-evicted keys from this chunk's
            # own deterministic answers. A smaller chunk is a narrower vocabulary per call. See the
            # setting's declaration in config.py, which says the same thing to whoever sets it.
            chunk = max(1, int(getattr(ctx.settings.extraction, "llm_batch_max_items",
                                       matcher.BATCH_MAX_ITEMS) or matcher.BATCH_MAX_ITEMS))

            def _run_task(task: tuple) -> tuple[list, dict]:
                statement, subgroup, cited_notes = task
                return subgroup, matcher.match_batch(
                    [(str(li.id), li.source_label) for li in subgroup],
                    statement=statement,
                    # A statement spans several section banners, so the banner is per row.
                    sections={str(li.id): li.section_hint for li in subgroup},
                    chunk_size=chunk,
                    context_pool=context_pool, cited_notes=cited_notes,
                    identified_notes=identified, notes=doc.notes,
                    sub_item_keys=sub_item_keys, computed_keys=computed_keys)

            max_workers = max(1, ctx.settings.extraction.llm_max_concurrency)
            # REPORT BEFORE THE FIRST CALL, so a reader learns how many there will be rather than
            # watching a still bar and guessing. This stage is the longest in the pipeline and the
            # only one that makes LLM calls, and until now nothing moved for the whole of it — not
            # the percentage, not the stage counter, not the log tail — because the only thing
            # that flushed them was the NEXT stage starting. A run that was working and a run that
            # had hung looked identical.
            # The count this stage starts from, so the live figure below can be an ASSIGNMENT.
            # `matcher.usage["calls"]` is cumulative for the matcher, so adding it each time round
            # the loop would count every earlier call again.
            # PLANNED IN PROVIDER CALLS, which is the number actually being asked about. One
            # task is one (statement, section) subgroup, but `match_batch` CHUNKS a subgroup at
            # `chunk` (above), so a 60-row subgroup is three calls at the shipped 25 and not one.
            # Reporting task count as call count would understate a large filing by a factor of
            # several. `chunk` is the value the calls are actually made with, so the plan and the
            # calls can no longer disagree.
            planned = sum(-(-len(rows) // chunk) for _stmt, rows, _note in tasks)
            ctx.emit_step(0, planned, "LLM call")
            ctx.log(f"map_line_items:llm_planned_calls={planned} batches={len(tasks)}"
                    f" rows={sum(len(t[1]) for t in tasks)} chunk_size={chunk}"
                    f" concurrency={max_workers}")
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                finished = 0
                for subgroup, results in pool.map(_run_task, tasks):
                    in_group = {str(li.id) for li in subgroup}
                    for iid, res in results.items():
                        if iid not in in_group:
                            ctx.log(f"map_line_items:foreign_item_id_ignored({iid})")
                            continue
                        if _apply(by_id[iid], res):
                            mapped += 1
                    # `pool.map` yields in the order the tasks were submitted, so this counts
                    # COMPLETED calls and not merely dispatched ones — the number the reader is
                    # actually asking about. One progress write per call: seconds apart, and it is
                    # what flushes the log tail as well.
                    finished += 1
                    # LIVE, not at the end of the stage. `ctx.llm_calls` was only rolled up once
                    # every call had finished, so "how many calls have completed" — the question
                    # actually being asked mid-run — read 0 for the whole of the longest stage.
                    made = matcher.usage["calls"]
                    ctx.llm_calls = _calls_base + made
                    # The DENOMINATOR grows if the estimate was low. A retry, or a chunk the
                    # provider forced smaller, means more calls than planned — and a counter that
                    # sticks at "12 / 12" while calls are still going out is worse than one that
                    # admits the plan moved.
                    ctx.emit_step(made, max(planned, made), "LLM call")
                    ctx.log(f"map_line_items:llm_batch {finished}/{len(tasks)}"
                            f" calls={made}/{max(planned, made)} rows={len(subgroup)}"
                            f" mapped_so_far={mapped}")
            # How the document was actually cut up, so "one statement, two pages, one call" is
            # verifiable from the run record instead of asserted in a docstring.
            ctx.log(f"map_line_items:groups={len(groups)} batched_rows={batched}"
                    f" per_line_rows={unstated} chunks={matcher.usage['batch_chunks']}"
                    f" max_chunk={matcher.usage['batch_max_items']}")
            if focus_keys:
                # TEMPORARY (focus-run routing). Reported rather than silent: a run that decided
                # most of its rows deterministically must not read as a full LLM mapping.
                # `keys=` counts what was CONFIGURED; the answerability fields say how many of those
                # the rulebook can actually return (see `_focus_answerability`). Without them the
                # line overstated the run's reach — 8 configured, 4 reachable, on the shipped pair.
                ctx.log(f"map_line_items:focus_routing keys={len(focus_keys)}"
                        f" {_focus_answerability(matcher, focus_keys)}"
                        f" rows_to_llm={focus_llm} rows_deterministic={focus_det}"
                        f" sections_skipped={focus_sections_skipped}")
                # …AND ONTO THE RUN RECORD, not only into the log. `mapping_strategy` says a
                # provider was configured; this row split is what makes that claim CHECKABLE,
                # and it is the entire difference between a focus run and a full LLM mapping —
                # 8 focus keys out of 462 concepts means the deterministic tiers answered nearly
                # everything. Written to `reason` rather than to a new field because `reason` is
                # already serialised beside the label (routes/extractions.py:1358-1363); see the
                # strategy assignment above for why the label itself is left as it is.
                # Overwritten below if the provider then made zero successful calls — that is a
                # degraded run, which outranks this.
                if matcher.llm_enabled:
                    ctx.mapping_strategy_reason = (
                        f"focus routing: keys={len(focus_keys)}"
                        f" rows_to_llm={focus_llm} rows_deterministic={focus_det}"
                        f" sections_skipped={focus_sections_skipped}")
        else:
            # The unbatched path: one match per row, each of which may be its own LLM call. Same
            # question, so the same report — and the total is known before the first one.
            total_rows = len(doc.line_items)
            ctx.emit_step(0, total_rows, "row")
            ctx.log(f"map_line_items:per_line_rows={total_rows} (unbatched path)")
            for done, li in enumerate(doc.line_items, start=1):
                if _apply(li, matcher.match(li.source_label, statement=_statement_of(li),
                                            section=li.section_hint)):
                    mapped += 1
                ctx.llm_calls = _calls_base + matcher.usage["calls"]
                # Every 10 rows, not every row: this path can carry hundreds and each report is a
                # small database write. The batched path reports per call because a call is seconds.
                if done % 10 == 0 or done == total_rows:
                    ctx.emit_step(done, total_rows, "row")

        # A note's pages do not carry a statement title, but its cited face row does. Map each
        # extracted detail row in that context so a disclosure can contribute a dedicated concept
        # without changing the source-faithful table structure.
        parents_by_note: dict[str, list] = {}
        for parent in doc.line_items:
            for note_number in _cited_notes([parent]):
                parents_by_note.setdefault(note_number, []).append(parent)
        mapped_notes = 0
        for table in doc.notes:
            parents = parents_by_note.get(str(table.note_number), [])
            statements = {_statement_of(parent) for parent in parents}
            sections = {parent.section_hint for parent in parents if parent.section_hint}
            statement = next(iter(statements)) if len(statements) == 1 else None
            section = next(iter(sections)) if len(sections) == 1 else None
            for item in table.items:
                if item.role is not LineRole.LINE or item.canonical_key:
                    continue
                if skip_prose:
                    why = prose_reasons(item.raw_label or "")
                    if why:
                        prose_skipped.append((item.raw_label or "", why))
                        continue
                item_statement, item_section = statement, section
                if normalize_label(item.raw_label) in {
                    "depreciation of property plant and equipment",
                    "depreciation of investment property",
                }:
                    item_statement, item_section = "profit_and_loss", "income_and_expenses"
                if _apply(item, line_matcher.match(item.raw_label, statement=item_statement,
                                                   section=item_section)):
                    mapped_notes += 1
        if prose_skipped:
            # Named, not merely counted: a suppressed caption is an answer withheld, and a
            # reviewer has to be able to see which rows and why.
            sample = "; ".join(f"{lab[:44]!r}({'+'.join(why)})"
                               for lab, why in prose_skipped[:3])
            ctx.log(f"map_line_items:prose_captions_skipped={len(prose_skipped)} {sample}")
        if mapped_notes:
            ctx.log(f"map_line_items:note_items_mapped={mapped_notes}")

        matrix_facts = self._promote_reconciled_matrix_closings(doc, line_matcher, _statement_of, ctx)
        if matrix_facts:
            ctx.log(f"map_line_items:note_matrix_facts={matrix_facts}")

        # Whole-document rules, which need every row to have a concept first.
        mapped -= self._enforce_containment(doc, ontology, ctx)
        self._check_equivalence(doc, ontology, ctx)
        # Escalating over the same declarations: containment above handled components printed on
        # the FACE; this handles components itemised in a cited DISCLOSURE; sole-components below
        # handles a subtotal with one declared child and nothing evidencing a split. Order matters —
        # a parent this pass decomposes has children filed by the time the last one looks, which is
        # exactly the condition that makes it decline.
        mapped += self._split_from_disclosure(doc, ontology, line_matcher, ctx)
        mapped += self._infer_sole_components(doc, ontology, ctx)
        self._adopt_template_roles(doc, ctx)

        # Roll the mapper's LLM usage up onto the context for the audit log.
        ctx.llm_input_tokens += matcher.usage["input_tokens"]
        ctx.llm_output_tokens += matcher.usage["output_tokens"]
        # ASSIGNED from the base, not accumulated: the batch loop above already published a live
        # count as each call finished, and `+=` here would add the matcher's cumulative total to a
        # figure that already contains it.
        ctx.llm_calls = _calls_base + matcher.usage["calls"]
        if matcher.usage["model"]:
            ctx.llm_model = matcher.usage["model"]
        # Report what ACTUALLY happened: zero successful calls means the deterministic
        # ensemble decided every line, whatever was configured.
        if matcher.llm_enabled and matcher.usage["calls"] == 0:
            ctx.mapping_strategy = "deterministic"
            ctx.mapping_strategy_reason = (
                matcher.usage.get("last_error")
                or "llm provider resolved but made no successful calls")
        ctx.log(f"map_line_items:mapped={mapped}/{len(doc.line_items)} llm_calls={matcher.usage['calls']}")
        # Named routes, not just a count: "the banner corrected 4 answers" is not reviewable, while
        # "pl_profit_for_the_year -> pl_total_comprehensive_income_for_the_year" is the one line of
        # the run record that says which figure moved and why.
        if matcher.usage["family_resolved"]:
            ctx.log(f"map_line_items:section_reroutes={matcher.usage['family_resolved']}"
                    f" routes={','.join(matcher.usage['family_routes'])}")
        if matcher.usage["computed_refused"]:
            # Rows whose caption named a concept the framework computes. Logged because the
            # alternative the mapper used to take — filing the figure on the nearest neighbouring
            # subtotal — showed up nowhere at all, and the statement still tied.
            ctx.log(f"map_line_items:computed_concept_rows={matcher.usage['computed_refused']}")
        if matcher.usage["confusable_ties"]:
            # `binding.order` step 6: answered with both candidates and a review flag rather than a
            # pick. Counted here because "the mapper declined N rows on purpose" reads very
            # differently from "the mapper failed on N rows".
            ctx.log(f"map_line_items:confusable_ties={matcher.usage['confusable_ties']}")
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
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        from app.services.rollups import calculated_nodes
        calculated = set(calculated_nodes(template or {}))
        extraction_mode = {m.canonical_key: m.extraction_mode for m in ontology.mappings}
        validation_totals = {
            key for key in calculated if extraction_mode.get(key) == "extract_or_derive"
        }
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
            # A template-calculated line is not an additive parent. Its printed amount remains
            # attached to the same key as validation evidence, while the statement/export serves
            # the independently calculated value from its components.
            if aggregate in validation_totals:
                continue
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
                li.confidence.flags.append(f"unfiled_aggregate:{aggregate}")
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
                    ctx.log(f"map_line_items:containment_unexplained({why}):{aggregate}"
                            f" columns={len(gaps)} components={','.join(present)}")
                unfiled += 1
            child_flag = f"alloc:{AllocationStatus.CHILD_COMPONENT.value}"
            for li in doc.line_items:
                # Named on the children too: a reviewer opening the empty aggregate needs the rows
                # that replaced it, and a reviewer opening a child needs to know why the parent is
                # empty. Once each — a concept can be a component of more than one declared group.
                if li.canonical_key in present and child_flag not in li.confidence.flags:
                    li.confidence.flags.append(child_flag)
            ctx.log(f"map_line_items:containment({why}):{aggregate}"
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
                * no dedicated component — there is then no template detail for the note to take priority
                    over, so the face row remains the complete fact;
                * the note's mapped and residual detail together do not ACCOUNT FOR the aggregate, per
                    (basis, period) column, within ``recon_abs_tolerance`` — the arithmetic is the only
                    support this stage can test for ``global_rules.parent_child_allocation``;
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

        NOTE ITEMS KEEP THE SAME MAPPING. ``NoteItem.canonical_key`` is persisted for the Notes view,
        reconciliation and note storage. A mapped note line carries its dedicated template key; an
        unmatched line in a reconciled partial split carries the original non-calculated face key.
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
        from app.services.rollups import calculated_nodes
        calculated_nodes_by_key = calculated_nodes(template_def or {})
        calculated = set(calculated_nodes_by_key)
        calculated_residuals = {
            key for key, node in calculated_nodes_by_key.items()
            if (node.get("rollup") or {}).get("reported_total_key")
        }
        decls = _same_section_decompositions(ontology)
        seen = {a for a, _c, _s in decls}
        decls = decls + [d for d in _note_permitted_decompositions(ontology, template_def)
                         if d[0] not in seen]
        seen = {a for a, _c, _s in decls}
        # Ordinary face lines may also be itemised by their cited notes. Opt-in is still explicit
        # (`note_use: decomposition_allowed`), and the arithmetic/cross-section/duplicate gates
        # below are identical to the declared aggregate path. Candidate children are every
        # dedicated leaf in the same subsection; only captions the note actually prints are used.
        #
        # "EXPLICIT" IS NOT "RARE", and which rulebook is loaded decides which: the gate here is
        # `note_use == decomposition_allowed` plus a single-entry `section_scope`, and that admits
        # 2 concepts of `hkfrs_hk_china_ontology.json` but 329 of `output_csv_hk_ontology.json`,
        # where `decomposition_allowed` is the section default rather than a named exception. On the
        # output_csv pair this arm is the normal case, so the gates below (one filed parent, a cited
        # note that prints at least two children, same section, arithmetic accounted in every
        # column) are the whole of the protection — do not read the opt-in as a second one.
        dynamic: set[str] = set()
        by_key = {m.canonical_key: m for m in ontology.mappings}
        for parent in doc.line_items:
            key = parent.canonical_key or ""
            mapping = by_key.get(key)
            if (not mapping or key in seen or key in calculated
                    or mapping.note_use != "decomposition_allowed"
                    or len(mapping.section_scope or []) != 1
                    or not _cited_notes([parent])):
                continue
            section = mapping.section_scope[0]
            children = [
                candidate.canonical_key for candidate in ontology.mappings
                if candidate.canonical_key != key
                and candidate.section_scope == [section]
                and candidate.canonical_key not in calculated
                and candidate.value_scope != "exclusive_residual"
                and candidate.extraction_mode != "do_not_extract"
            ]
            if children:
                decls.append((key, children, section))
                seen.add(key)
                dynamic.add(key)
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
                    ctx.log(f"map_line_items:split_declined({aggregate}):"
                            f" {len(parents)} rows carry it")
                continue
            parent = parents[0]
            if aggregate not in dynamic and any(
                    li.canonical_key in children for li in doc.line_items):
                if aggregate in calculated_residuals:
                    ctx.log(f"map_line_items:split_declined({aggregate}): component already filed")
                continue                  # a component is already filed; containment owns this case
            cited = _cited_notes([parent])
            tables = [t for t in doc.notes if str(t.note_number) in cited]
            if not tables:
                continue
            statement = stmt_by_page.get(_page_of(parent) or -1)

            def concepts_in(group: list) -> tuple[dict[str, list], list, list]:
                found: dict[str, list] = {}
                residual: list = []
                foreign: list = []
                for table in group:
                    for it in table.items:
                        if not it.values or it.role is not LineRole.LINE:
                            continue
                        res = matcher.match(it.raw_label or "", statement=statement,
                                            section=section)
                        if (res is None or res.canonical_key not in children) and it.group_hint:
                            # THE CAPTION SAID NOTHING, SO ASK THE SUB-HEADING IT WAS PRINTED
                            # UNDER. A note itemises by whatever dimension it likes, and the
                            # dimension is often not a concept at all: under
                            # "Under-provision in prior years, net:" the filing prints
                            # "Mainland China 136,626", and no alias, rule or model should make a
                            # geography into a tax concept. The sub-heading is what names it, which
                            # is also how a person reads the page.
                            #
                            # CAPTION FIRST, ALWAYS. The fallback only runs when the row's own
                            # words named none of THIS SECTION'S components, so a sub-heading can
                            # never override a row that named itself — "Current charge for the
                            # year:" does not get to re-home the CIT and LAT rows beneath it. The
                            # test is on the resolved KEY and not on ``res is None``, because an
                            # unmatched caption comes back as a result object carrying method
                            # ``UNMATCHED`` and a null key rather than as ``None``, and testing the
                            # object meant this fallback never ran at all. And several rows under
                            # one sub-heading all resolving to it is correct, not a collision: they
                            # are a breakdown of that one concept and ``_summed_columns`` adds them.
                            res = matcher.match(it.group_hint, statement=statement,
                                                section=section)
                        if res and res.canonical_key in children:
                            already_on_face = any(
                                li is not parent and li.canonical_key == res.canonical_key
                                for li in doc.line_items)
                            if aggregate in dynamic and already_on_face:
                                foreign.append((it, res.canonical_key))
                            else:
                                found.setdefault(res.canonical_key, []).append(it)
                        else:
                            # Distinguish a genuinely unmatched detail from a known concept in a
                            # different subsection. The former may retain the face key; treating
                            # the latter as residual would cross the printed section boundary.
                            outside = matcher.match(it.raw_label or "", statement=statement)
                            if outside and outside.canonical_key == aggregate:
                                residual.append(it)
                            elif outside and outside.canonical_key:
                                foreign.append((it, outside.canonical_key))
                            else:
                                residual.append(it)
                return found, residual, foreign

            # ONE TABLE AT A TIME, and the union of them only if no single table will do.
            #
            # A cited note number is not one table. ``notes_extract`` builds a ``NotesTable`` per
            # (note section, page), and a note routinely prints several: the filing this was
            # measured against has more than one table under 19 of its note numbers, and its tax
            # note prints the components on one page and the EFFECTIVE-RATE RECONCILIATION on the
            # next -- a derivation from profit before tax down to the same charge, not a
            # decomposition of it. The reconciliation restates one of the components under its own
            # caption ("LAT 90,588"), so reading the union of every table counted that amount TWICE
            # and the arithmetic gate was the only thing standing between a double count and a
            # published figure. Which way it fell was luck, not design.
            #
            # THE UNION IS STILL TRIED, SECOND, because one printed table that breaks across a page
            # becomes two ``NotesTable`` objects here for a reason that is about the PDF and not
            # about the disclosure — ``notes_extract`` cuts a section at each heading occurrence and
            # stamps it with the one page it was read from, so a continuation that RE-PRINTS its
            # heading ("11. Income tax (Continued)") starts a second table. Dropping the union
            # outright would refuse those, so instead it is the fallback: when a single table
            # accounts for the aggregate on its own, that is the table the filing meant, and nothing
            # else is read.
            #
            # THE LIMIT OF THIS, stated because it is not obvious: the unit separated here is the
            # heading, not the printed table. Two printed tables under ONE heading on ONE page are
            # one ``NotesTable``, and this pass cannot tell them apart — the arithmetic gate is
            # again the only thing between a restated component and a published figure there.
            def raw_accounts(group: list) -> bool:
                detail = [item for table in group for item in table.items
                          if item.values and item.role is LineRole.LINE]
                if not detail:
                    return False
                _orientation, unaccounted = _orientation_accounting_for(
                    parent, {"note_detail": detail}, tol)
                return not unaccounted

            # Arithmetic before semantics. A note number can contain many tables, and matching
            # every row in every table against a large ontology made one filing spend minutes in
            # this pass. Only a table whose raw line detail already accounts for the cited face
            # amount can qualify; all other tables are rejected without ontology calls.
            groups = [[table] for table in tables if raw_accounts([table])]
            if len(tables) > 1 and raw_accounts(tables):
                groups.append(tables)
            candidates = [(group, *concepts_in(group)) for group in groups]

            def accounting_for(pool: list) -> list[tuple[list, dict, list, int]]:
                out: list[tuple[list, dict, list, int]] = []
                for group, found, residual, foreign in pool:
                    if not found or foreign:
                        continue
                    # A calculated parent can only be validated from its declared children. An
                    # unmatched amount cannot be made a formula component by assigning it to the
                    # calculated total itself, so partial splits remain available only to leaves.
                    if aggregate in calculated and aggregate not in calculated_residuals and residual:
                        continue
                    accounted = {**found, **({aggregate: residual} if residual else {})}
                    orient, unaccounted = _orientation_accounting_for(parent, accounted, tol)
                    if not unaccounted:
                        out.append((group, found, residual, orient))
                return out

            singles = [c for c in candidates if len(c[0]) == 1]
            qualified = accounting_for(singles) or accounting_for(
                [c for c in candidates if len(c[0]) > 1])
            if not qualified:
                if not candidates:
                    ctx.log(f"map_line_items:split_declined({aggregate}):"
                            " cited note detail does not account for the face amount")
                    continue
                foreign = [entry for _g, _found, _residual, entries in candidates
                           for entry in entries]
                itemised = max((len(found) for _g, found, _r, _f in candidates), default=0)
                if foreign:
                    named = ",".join(sorted({key for _item, key in foreign}))
                    ctx.log(f"map_line_items:split_declined({aggregate}):"
                            f" note contains concepts outside {section}: {named}")
                elif itemised < 1:
                    ctx.log(f"map_line_items:split_declined({aggregate}):"
                            f" the disclosure itemises {itemised} of this section's concepts")
                else:
                    best = max(candidates, key=lambda c: len(c[1]))
                    accounted = {**best[1], **({aggregate: best[2]} if best[2] else {})}
                    _o, short = _orientation_accounting_for(parent, accounted, tol)
                    ctx.log(f"map_line_items:split_declined({aggregate}):"
                            f" components do not account for it in {','.join(short)}")
                continue
            if len(qualified) > 1:
                # Two tables of the same note each account for the aggregate. One of them is a
                # restatement of the other and this stage cannot tell which, so it reads neither.
                ctx.log(f"map_line_items:split_declined({aggregate}):"
                        f" {len(qualified)} tables in the cited note each account for it")
                continue
            tables, hits, residual_items, orientation = qualified[0]
            assignments = {**hits, **({aggregate: residual_items} if residual_items else {})}
            for key, sources in assignments.items():
                src = sources[0].raw_label or key
                if key == aggregate and aggregate.startswith("is_pl__interest_"):
                    # Preserve the parent caption for the adjusted aggregate, so the output shows
                    # that the overall interest line was modified by note decomposition.
                    src = parent.source_label or src
                row = LineItem(source_label=src, canonical_key=key,
                               ordinal=parent.ordinal, role=LineRole.LINE,
                               printed_in=PrintedIn.FACE, note_number=parent.note_number,
                               section_hint=parent.section_hint)
                columns = {(ev.basis.value, ev.period_label or "")
                           for ev in parent.values.values() if ev.value is not None}
                for col, total in _summed_columns(sources).items():
                    if col not in columns:
                        # A COLUMN THE AGGREGATE NEVER PRINTED IS NOT PART OF WHAT WAS CORROBORATED.
                        # The gate tests every column the aggregate carries; a column only the note
                        # has — an extra numeric column, a maturity or rate column extraction read
                        # as a period — was never tested against anything, and publishing it would
                        # put an unchecked figure on the face and, worse, apply the orientation to
                        # it: a sign decided by arithmetic that column took no part in.
                        continue
                    ev = total[1].model_copy(deep=True)
                    # ``value_raw`` keeps the note's own figure and ``value`` carries the face's
                    # convention, which is the contract those two fields already have: raw is what
                    # the page said, value is the sign-normalised number, and ``sign_normalised``
                    # records that the engine changed a reported sign so the pair is an audit trail
                    # rather than a silent rewrite.
                    ev.value_raw = total[0]
                    ev.value = total[0] * orientation
                    ev.sign_normalised = ev.sign_normalised or orientation < 0
                    row.set_value(ev)
                row.confidence.mapping = min(parent.confidence.mapping or 0.75, 0.75)
                row.confidence.method = MappingMethod.RULE.value
                row.confidence.flags.append(f"split_from:{aggregate}")
                if key == aggregate:
                    row.confidence.flags.append("note_residual_to_face_item")
                if orientation < 0:
                    # Visible on the row, because an analyst comparing it to the note will see the
                    # opposite sign there and the row has to say why.
                    row.confidence.flags.append("split_sign_flipped")
                if len(sources) > 1:
                    # The disclosure itemises finer than the template does, so this concept's figure
                    # is the sum of several disclosed rows. Said on the row, because its provenance
                    # can only point at one of them.
                    row.confidence.flags.append(f"split_summed_rows:{len(sources)}")
                doc.line_items.append(row)
                for source in sources:
                    source.canonical_key = key
                    source.confidence.mapping = row.confidence.mapping
                    source.confidence.method = MappingMethod.RULE.value
                    source.confidence.flags.append(f"supports_face_item:{aggregate}")
                added += 1
            if aggregate in calculated and aggregate not in calculated_residuals:
                parent.confidence.flags.append("reported_validation_for_calculated")
                parent.confidence.flags.append(
                    f"note_components:{','.join(sorted(assignments))}")
            else:
                parent.canonical_key = None
                if parent.role is LineRole.LINE:
                    parent.role = LineRole.SUBTOTAL
                parent.confidence.flags.append(
                    f"alloc:{AllocationStatus.PARENT_GROSS_EVIDENCE_ONLY.value}")
                parent.confidence.flags.append(f"note_decomposed_from:{aggregate}")
                parent.confidence.flags.append(
                    f"decomposed_into:{','.join(sorted(assignments))}")
            ctx.log(f"map_line_items:split({aggregate}) into {len(hits)} concepts"
                    f" plus {len(residual_items)} residual note rows"
                    f" from note {','.join(sorted({str(t.note_number) for t in tables}))}"
                    f"{' (signs flipped to the face convention)' if orientation < 0 else ''}")
        return added

    @staticmethod
    def _adopt_template_roles(doc: DocumentModel, ctx: PipelineContext) -> int:
        """A row that resolved to a calculated template node IS a subtotal. Returns rows promoted.

        A filing does not mark its subtotals. "Gross profit", "LOSS FROM OPERATING ACTIVITIES",
        "LOSS BEFORE TAX" and "LOSS FOR THE YEAR" are printed exactly like the lines they total, so
        reconstruction has nothing to read a role from and hands every one of them on as
        ``LineRole.LINE``. The TEMPLATE declares them totals, and once a row is filed against a
        template node that declaration is a fact about the row.

        TWO THINGS FOLLOW, and both are what the product owner asked for. A subtotal's figure is the
        SUM OF ITS CHILDREN rather than a reading of the page — ``rollups.figures_as_shown`` already
        prefers the computed figure over the printed one and keeps the printed one as evidence, and
        it can only do that for a node the template calls calculated. And a subtotal is NEVER SWEPT
        INTO A SECTION RESIDUAL: ``residual``'s eligibility list rules out ``SUBTOTAL`` and ``TOTAL``
        by role, so this promotion is what makes that rule apply. Before it, a subtotal the mapper
        could not place went into the section's Others — on a real HK income statement "LOSS FROM
        OPERATING ACTIVITIES" landed in ``pl_expenses__others`` alongside a genuine expense row, two
        rows on one concept, and the rulebook's own exclusion for that concept says in as many words
        "Section subtotals and statement totals".

        A ROW'S OWN ROLE IS NOT OVERRULED when reconstruction did manage to read one: a printed
        "Total" caption already arrives as ``TOTAL`` and stays that way. Only ``LINE`` is promoted,
        so this adds a verdict where there was none rather than replacing one.
        """
        from app.services.rollups import node_roles

        template = getattr(ctx, "template", None)
        template_def = (template.model_dump(mode="json")
                        if template is not None and hasattr(template, "model_dump") else template)
        roles = node_roles(template_def)
        if not roles:
            return 0
        wanted = {"subtotal": LineRole.SUBTOTAL, "total": LineRole.TOTAL}
        promoted = 0
        for li in doc.line_items:
            if li.role is not LineRole.LINE or not li.canonical_key:
                continue
            role = wanted.get((roles.get(li.canonical_key) or "").lower())
            if role is None:
                continue
            li.role = role
            promoted += 1
        if promoted:
            ctx.log(f"map_line_items:template_roles_adopted={promoted}")
        return promoted

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
                ctx.log(f"map_line_items:sole_component_declined({concept.canonical_key}):"
                        " a child of the subtotal is already filed")
                continue
            evidence = _sibling_evidence(doc, ontology, parents, siblings)
            if evidence:
                ctx.log(f"map_line_items:sole_component_declined({concept.canonical_key}):"
                        f" {evidence}")
                continue
            if len(parents) > 1:
                # Two rows filed on one subtotal is a mapping problem of its own, reported
                # elsewhere; inferring a child from an ambiguous total would compound it.
                ctx.log(f"map_line_items:sole_component_declined({concept.canonical_key}):"
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
            ctx.log(f"map_line_items:sole_component({concept.canonical_key}) from {aggregate}"
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
                        ctx.log(f"map_line_items:equivalence_conflict {a}={va.value}"
                                f" {b}={vb.value} column={col}")
        return conflicts
