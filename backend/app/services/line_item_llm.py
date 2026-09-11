"""ONE REQUEST PER LINE ITEM (or per authored group) — the only place a model is asked anything
about which figure a line item carries.

NEW FILE -> backend/app/services/line_item_llm.py

WHY THIS EXISTS. `services.line_item_requests.plan_requests` decided WHICH line items share a
request and which notes each request carries, and nothing called it: it was a planner with no
executor. The thing that used to make the calls asked a different question at a different level —
`mapping.match_batch` took `items = [(item_id, source_label)]`, printed ROWS chunked by
(statement, basis, period), and asked "which concept is each of these rows?". All of that is gone
(see `stages.map_ontology` and the retired settings in `config.py`). This is what replaces it.

THE QUESTION IS NOT THE SAME QUESTION, and that is the whole reason the row machinery could not be
re-pointed:

  * A ROW REQUEST asked the model to CHOOSE A LINE for a caption it was shown. So it needed a
    candidate list, a statement/section gate to gradeS the answer against, a cap on how many
    concepts would fit in one call, and an "off-candidate" contract for an answer that went past
    the list. Every one of those exists only because the line was the unknown.
  * A LINE-ITEM REQUEST names the line and asks WHERE ITS FIGURE IS PRINTED. There is no list to
    choose from and nothing to force-fit: the answer is a CITATION — a note and a row caption — or
    "this filing does not state it", which is a real answer and not a failure.

SO THE ANSWER IS ONLY WORTH THE ROW BEHIND IT, and that is enforced rather than trusted.
`note_sourced.resolve_sources` matches each citation back against the extracted rows and takes the
page and the figure OFF THE ROW; the model is given no page index, no bbox and no licence to state
an amount. The one exception is a figure stated in PROSE, which belongs to no row — there the
amount is allowed, and it is checked against the note's own text before it is accepted, so the
model is LOCATING a printed number rather than supplying one. A citation that resolves to nothing
is reported as unresolved, not believed and not silently dropped.

WHAT IS NEVER ASKED ABOUT. `line_item_requests.asked_about` is the boundary and this module does
not have its own: a DERIVED PARENT is not offered, because its figure is its declared cascade's and
a number written straight onto the parent skips every rung — the run then loses which disclosure
the figure came from and the cross-check between rungs is never done. The number looks identical
either way, which is why it is refused rather than discouraged.

NON-INTERFERENCE WITH THE DETERMINISTIC ROUTE. This runs BEFORE `stages.note_sourced`, and every
site that stage writes a figure asks `_llm_holds` first. So where a request answered, the declared
`note_source`/cascade route is the OTHER way of reaching the figure rather than a correction of it,
and the deterministic stage fills what was left empty. Neither route silently overwrites the other.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

from app.services import line_item_requests, note_context, note_sourced
from app.services.mapping import SourceRef


# ── THE REPLY CONTRACT ────────────────────────────────────────────────────────────────────────
#
# NOT CONFIGURABLE, for the reason the row contract was not: the reply parser and the per-line
# attribution depend on its shape, and an answer naming a line nobody asked about — or returning a
# figure where a citation belongs — is not a worse answer but an unusable one. A deployment's
# opinion about how a statement should be READ travels in the authored guidance instead
# (`LineItemSet.prompt`, via `mapping.authored_guidance`), which is why the two are separate.
REPLY_CONTRACT = (
    "You locate figures in a financial statement's NOTES. For each line item you are given, say "
    "WHERE IN THE SUPPLIED NOTES its figure is printed. You are not choosing what the line is — "
    "the line, its definition and its inclusions are given to you.\n"
    "\n"
    "- Answer with the line item's `key`, exactly as given. Never a key that was not given to "
    "you, and never more than one answer for the same key.\n"
    "- An answer IS its `sources`. Each entry names the note and quotes the row CAPTION as the "
    "document prints it; the caption is matched back against the extracted rows to recover the "
    "page and the figure, so a paraphrase cannot be resolved. An answer with no `sources` is "
    "read as \"this filing does not state this line\", which is a valid and useful answer — give "
    "it rather than citing a row you are unsure of.\n"
    "- SEVERAL ROWS MAY MAKE UP ONE LINE. Name all of them: a note that splits a total by "
    "function prints a row per function and the line is their sum. Set `role` to \"component\" "
    "and give each entry's `sign` — -1 where the row is SUBTRACTED, +1 where it is added. Set "
    "`role` to \"whole\" (the default) when the rows you name each state the line's whole figure, "
    "in which case the first that resolves is used and the others are recorded as corroboration. "
    "If you are unsure, answer \"whole\": a duplicate declared whole is caught, a duplicate "
    "declared component is added in silence and overstates the line.\n"
    "- Do NOT state a figure. You are locating a printed number, not reporting one. THE ONE "
    "EXCEPTION is an amount stated in PROSE and in no table row ('Depreciation charges of "
    "approximately HK$529,841,000 are included in other operating expenses'): give that amount "
    "in the entry's `amount`, with the sentence in `quote`. It is checked against the note's own "
    "text and refused if it is not there, so give it exactly as printed and never round, convert "
    "or infer one.\n"
    "- Do not cite a note that was not supplied to you. The notes below are the ones this line's "
    "own configuration selected; if its figure is not in them, say so with an empty `sources`.\n"
    "- Return calibrated confidence in [0,1] — high only where the caption is unambiguous — and "
    "in `reason`, which criterion or wording makes the rows you named this line's. Each row's own "
    "page, note and caption are recovered here, so `reason` is the one part of the trace only you "
    "can supply."
)


class LineItemAnswer(BaseModel):
    """Where one line item's figure is printed, as the model reports it."""

    key: str = Field(description="the line item key you were given, verbatim")
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="the note rows this line's figure is printed on; empty means the supplied "
                    "notes do not state this line")
    # "whole" | "component". Deliberately a plain string rather than an enum: a model answering
    # outside the pair must be readable as an unexpected answer and defaulted, not rejected as a
    # schema violation that costs the whole reply.
    role: str = Field(default="whole")
    signs: list[int] = Field(
        default_factory=list,
        description="+1 added, -1 subtracted, one per `sources` entry, in the same order; "
                    "meaningful only when role is \"component\"")
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="")


class LineItemReply(BaseModel):
    """One answer per line item asked about."""

    # RESERVE THE WHOLE COMPLETION BUDGET FOR THIS JSON. On a reasoning model, reasoning tokens are
    # spent from the same allocation, and ASKING for them (`reasoning_effort`) can exhaust a small
    # structured reply before it emits any JSON at all — finish_reason=length with empty content,
    # and the whole request lost. Read by `adapters.openai_llm.build_body`, which withholds
    # `reasoning_effort` when it is set. Declared HERE, on the schema, rather than inferred from
    # this module's path: the previous test matched a module name and answered False the moment the
    # request moved files.
    compact_json_reply: ClassVar[bool] = True

    answers: list[LineItemAnswer] = Field(default_factory=list)

    @field_validator("answers", mode="before")
    @classmethod
    def _unwrap_schema_envelope(cls, value):
        """Accept ``{"items": [...]}`` where the schema asks for ``[...]``.

        THE SAME DEFECT THE ROW REPLY HAD, and it cost a whole call when it fired. Structured
        output here is model-agnostic — the response model's JSON Schema is embedded in the system
        prompt and the reply is validated with Pydantic — and some models echo the SCHEMA NODE for
        an array field (``{"type": "array", "items": [...]}``) instead of the array it describes.
        Measured at one call in six on one filing. A reply that fails to validate is not a partial
        answer: the whole request is lost, so the envelope is unwrapped rather than refused.
        """
        if isinstance(value, dict):
            for envelope in ("items", "answers", "value"):
                inner = value.get(envelope)
                if isinstance(inner, list):
                    return inner
        return value


@dataclass
class Outcome:
    """What one request produced, for the run log and the audit trail."""

    plan: line_item_requests.RequestPlan
    answered: tuple[str, ...] = ()
    empty: tuple[str, ...] = ()
    foreign: tuple[str, ...] = ()
    unresolved: int = 0
    error: str = ""


def line_item_payload(item, notes_for_item: tuple[str, ...]) -> dict:
    """One line item as the model is shown it — its own configuration, and nothing else's.

    BUILT FROM `LineItemDef`, NOT FROM THE ONTOLOGY CONCEPT, and that is the shape change this
    module is. `mapping._concept_payload` described a CONCEPT so that a caption could be matched
    against several of them, which is why it carried a candidate's `value_scope` and its
    match priority. Here the line is GIVEN, so what matters is everything an author wrote about
    THIS line: what it means, what counts, what does not, how it is printed, what it is confused
    with, and what its ROW is called. A candidate list would be the row question wearing a
    line-item hat.

    IT ALSO REACHES THE PARTS, which the concept payload could not. The working view is a
    projection of the MATCHABLE concepts and drops all 77 sub-line items, so a part — the layer
    that actually corresponds to a printed note row — had no concept entry to be described by.
    Reading the definition straight off the line item is what makes a part askable at all.

    EVERY FIELD HERE IS AUTHORED, and none is inferred from a parent. A derived line's parts carry
    their own recognition (aliases, `aliases_i18n`, regex and keyword hints, `row_terms`), which is
    where it belongs: the parent is never asked about.
    """
    ns = getattr(item, "note_source", None)
    entry: dict = {
        "key": item.key,
        "label": item.label or item.key,
        "notes_supplied": list(notes_for_item),
    }
    if getattr(item, "definition", ""):
        entry["definition"] = item.definition
    elif getattr(item, "description", ""):
        entry["definition"] = item.description
    for attr, name in (("include_criteria", "include"),
                       ("exclude_criteria", "exclude"),
                       ("confusable_with", "do_not_confuse_with")):
        values = list(getattr(item, attr, None) or ())
        if values:
            entry[name] = values
    # HOW THE FILING PRINTS IT, in both scripts. `aliases_i18n` is the per-locale half and it is
    # carried whole rather than filtered to the document's locale: a PRC filing prints a bilingual
    # heading as often as not, and the model is reading the notes rather than matching a string.
    aliases = list(getattr(item, "aliases", None) or ())
    if aliases:
        entry["printed_as"] = aliases
    i18n = getattr(item, "aliases_i18n", None) or {}
    if i18n:
        entry["printed_as_by_language"] = {
            lang: list(values) for lang, values in i18n.items() if values}
    # THE SENTENCE THAT SEPARATES THIS LINE FROM ITS LOOK-ALIKE. Authored for a reader and, until
    # the row path went, read by `_concept_payload`. It belongs here far more than it belonged
    # there: two notes whose headings differ by one word is exactly the case a locator gets wrong.
    if getattr(item, "section_disambiguation", ""):
        entry["how_to_tell_it_apart"] = item.section_disambiguation
    if ns is not None:
        # WHAT THE ROW IS CALLED, and the most useful field here of any of them. `row_terms` is the
        # author's statement of the CONTENT's name as against the note HEADING's — the
        # container-for-content error this whole layer exists around: a note heading names the
        # container, a row caption names the content, and a probe blended from both scored 0.000
        # against the very heading its line belongs to. It is also the floor the answer is checked
        # against (`line_item_notes.caption_agrees_with_row_terms`), so withholding it would grade
        # the model against a constraint it was never given.
        for attr, name in (("row_terms", "row_is_called"),
                           ("row_terms_none", "row_is_never_called"),
                           ("row_caption_any", "row_caption_matches"),
                           ("row_caption_none", "row_caption_must_not_match")):
            values = list(getattr(ns, attr, None) or ())
            if values:
                entry[name] = values
    if getattr(item, "prompt", ""):
        entry["instruction"] = item.prompt
    # WHETHER THIS LINE IS PRINTED AS A NEGATIVE. An expense disclosed in brackets and the same
    # expense disclosed unsigned are the same fact, and a locator told nothing about the
    # convention has no way to know a bracketed figure is the row it was looking for.
    if getattr(item, "sign_convention", ""):
        entry["sign_convention"] = str(item.sign_convention)
    return entry


def build_request(plan, by_key: dict, notes_of: dict, identified: list[dict]) -> dict:
    """The user message for one request: the lines, then the notes they share.

    THE NOTES GO ONCE, BESIDE THE LINES, not inside each one. That is the entire saving grouping
    buys and it is a shape decision rather than a budget one: measured on the reference filing the
    identified notes are 95,188 of a ~196,000-character request, and attaching them per line to a
    twelve-line group would be about a megabyte — which fails a provider outright rather than
    merely costing more. Each line says which of them ITS configuration selected
    (`notes_supplied`), so a shared request is still answerable line by line.
    """
    wanted = set(plan.notes)
    return {
        "line_items": [line_item_payload(by_key[k], notes_of.get(k, ()))
                       for k in plan.keys if k in by_key],
        # Only the notes THIS request's lines selected. An identified note for a line in another
        # request is not context, it is noise the model has to rule out.
        "notes": [n for n in identified if str(n.get("note", "")) in wanted] if wanted else [],
    }


def _amount(text: str) -> Decimal | None:
    try:
        return Decimal(str(text).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def figures_of(resolved: list[dict], signs: list[int], component: bool) -> dict[str, Decimal]:
    """The figure per column, from the rows the citations resolved to.

    TWO CASES, AND THEY MUST NOT BE CONFLATED:

      * COMPONENT — the rows are addends and the line is their SUM, per column, with each row's
        declared sign applied. A column no row states is absent rather than zero: a note that
        prints only the current year does not make the prior year nil.
      * WHOLE — each row states the line's whole figure, so the FIRST that resolves is taken and
        the rest are corroboration. Summing them would publish a figure printed nowhere, which is
        exactly what a face line and the note total behind it produce when added.

    The ``prose`` key is returned as it comes. A prose amount is stated in FULL while a table is
    stated in the statement's units, so the caller scales it — `stages.note_sourced` records the
    thousandfold error that follows from publishing one unscaled.
    """
    out: dict[str, Decimal] = {}
    if not component:
        for entry in resolved:
            for period, raw in (entry.get("figures") or {}).items():
                value = _amount(raw)
                if value is not None and period not in out:
                    out[period] = value
        return out
    for index, entry in enumerate(resolved):
        sign = signs[index] if index < len(signs) and signs[index] in (1, -1) else 1
        for period, raw in (entry.get("figures") or {}).items():
            value = _amount(raw)
            if value is None:
                continue
            out[period] = out.get(period, Decimal(0)) + (value * sign)
    return out


def ask(provider, system: str, request: dict, *, max_tokens: int) -> LineItemReply:
    """One provider call. Raises whatever the provider raises — the caller records it per request.

    The whole request is the unit of failure on purpose. A reply that does not validate is not a
    partial answer, and a request that fails leaves its lines to the deterministic route, which is
    a defined outcome rather than a degraded one.
    """
    reply, meta = provider.complete_structured(
        system=system,
        messages=[{"role": "user", "content": json.dumps(request, ensure_ascii=False, indent=2)}],
        response_schema=LineItemReply,
        max_tokens=max_tokens,
    )
    return reply, meta


def plan_and_notes(line_item_set, notes, settings):
    """``(plans, by_key, notes_of, identified)`` — everything a run needs, computed once.

    `identified_notes` is document-level (its IDF is a property of the filing), so it is built once
    here and sliced per request rather than rebuilt per call.
    """
    items = [i for i in (getattr(line_item_set, "items", None) or [])
             if line_item_requests.asked_about(i)]
    by_key = {i.key: i for i in items}
    plans = line_item_requests.plan_requests(line_item_set, notes, settings)
    notes_of = {p.keys[0] if len(p.keys) == 1 else k: p.notes
                for p in plans for k in p.keys}
    identified = note_context.identified_notes(line_item_set, notes)
    return plans, by_key, notes_of, identified


def resolve(answer: LineItemAnswer, notes) -> tuple[list[dict], list[dict], dict[str, Decimal]]:
    """``(resolved, unresolved, figures)`` for one answer. The model supplies none of the figures."""
    resolved, unresolved = note_sourced.resolve_sources(answer.sources, notes)
    component = str(answer.role or "").strip().lower() == "component"
    return resolved, unresolved, figures_of(resolved, list(answer.signs or ()), component)
