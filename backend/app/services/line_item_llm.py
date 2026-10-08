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

from app.services import (line_item_notes, line_item_requests, line_item_routes, note_context,
                          note_sourced)
from app.services.mapping import SourceRef


# ── THE REPLY CONTRACT ────────────────────────────────────────────────────────────────────────
#
# NOT CONFIGURABLE, for the reason the row contract was not: the reply parser and the per-line
# attribution depend on its shape, and an answer naming a line nobody asked about — or returning a
# figure where a citation belongs — is not a worse answer but an unusable one. A deployment's
# opinion about how a statement should be READ travels in the authored guidance instead
# (`LineItemSet.prompt`, via `mapping.authored_guidance`), which is why the two are separate.
REPLY_CONTRACT = (
    "You locate figures in a filing's financial statements. For each line item you are given, say "
    "WHERE ITS FIGURE IS PRINTED. You are not choosing what the line is — the line, its "
    "definition and its inclusions are given to you.\n"
    "\n"
    "THREE PLACES A FIGURE IS PRINTED, and each entry of `sources` names ONE of them:\n"
    "- A NOTE ROW. Give the note number in `note` and the row caption in `caption`. The notes "
    "under `notes` are the ones this line's configuration selected. One note number can carry "
    "several blocks, each with its own `title`, and a caption such as 合计 or Total is printed in "
    "many of them: give the `title` of the block the row is in, exactly as given, in `table`. "
    "Where the row carries a `group` — the heading it is printed under, which on a related-party "
    "table is the line item a counterparty's balance belongs to (应收账款, 其他应付款) — give it "
    "in `group`, exactly as given. A citation naming a block the note does not have is refused, "
    "and so is a caption that still matches several rows with different figures.\n"
    "- A ROW ON THE FACE OF A STATEMENT. Give the statement token in `statement` — the same token "
    "the line carries as `statement` — and the row caption in `caption`, and leave `note` empty. "
    "The rows under `statement_rows` are that statement as the filing prints it, in order. Each "
    "block names its `entity`: `consolidated` is the group, `standalone` the parent company "
    "alone — a filing prints both, so the same caption appears in each.\n"
    "- A ROW ON ANY OTHER PAGE, where `other_pages` is supplied. Give that block's `page` number "
    "in `page` and the row caption in `caption`, and leave the note and the statement empty. "
    "These are the pages of the filing that are neither a statement nor a note, and only a line "
    "whose `read_from` says the whole filing may cite one.\n"
    "\n"
    "WHAT A FIGURE KEY IS. `figures` is keyed by the column's POSITION — `current`, `prior`, "
    "`col2` … . Where a statement block or a note row carries `columns`, it gives each key's "
    "printed heading, and a note row's holds from that row on: on a table wider than two "
    "columns, `current` and `prior` are its first two columns, not two years. `column_groups` "
    "names headings printed over several columns, in printed order.\n"
    "\n"
    "ONE COLUMN OF A ROW. Where a row's columns are levels, classes or measures rather than "
    "years — a fair-value hierarchy's Level 1 / Level 2 / Level 3, an asset class, 账面余额 | "
    "坏账准备 — and the line is one of them, give that column's heading in `column`, exactly as "
    "given in `columns` or `printed_columns` (or its key). Only that column's figure is taken, "
    "and it is filed under the period printed over it, or the period of the row's block. "
    "`printed_columns` lists every column the table prints, blank ones included: a column with "
    "no figure on the row is not a figure, and not a zero. A citation of a whole row whose "
    "columns are not years is refused — name the column.\n"
    "\n"
    "EACH LINE SAYS WHERE IT MAY BE READ FROM (`read_from`), and a citation outside that is "
    "refused. A line read from a note's rows carries no `statement` token and no statement rows "
    "of its own — its figure is in a note, and a row on the face is not it, however closely the "
    "caption matches. A line read from the face carries the statement and its printed rows.\n"
    "\n"
    "A SUPPLIED STATEMENT ROW MAY ALREADY NAME A LINE (`line`). That is the answer the lexical "
    "reader already reached for it, and it is what you are being asked about:\n"
    "- To CONFIRM it, cite that row. The figure is unchanged and your agreement is recorded.\n"
    "- To CORRECT it, cite the row you believe is this line's instead. The figure moves, and what "
    "it replaced is recorded.\n"
    "- To leave it alone, answer with an empty `sources`. The printed figure stands — UNLESS you "
    "have given that same printed row to a DIFFERENT line, in which case the row belongs to that "
    "line and this one is left empty. A printed row states one line's figure, so citing it for "
    "one line is also saying it is not another's. Where two lines could take the same row, cite "
    "it for the one you mean and leave the other empty deliberately.\n"
    "\n"
    "- Answer with the line item's `key`, exactly as given. Never a key that was not given to "
    "you, and never more than one answer for the same key.\n"
    "- An answer IS its `sources`. Each entry names where the row is — a note or a statement — and "
    "quotes the row CAPTION as the document prints it; the caption is matched back against the "
    "extracted rows to recover the page and the figure, so a paraphrase cannot be resolved. Give "
    "an empty `sources` rather than citing a row you are unsure of: it is a valid and useful "
    "answer, and it leaves any figure already found standing.\n"
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
    "- Do not cite a note or a statement that was not supplied to you, and do not cite a place "
    "this line's `read_from` excludes. If this line's figure is in nothing you were given, say so "
    "with an empty `sources` — that is a valid and useful answer, and where a statement row "
    "already named this line it leaves that figure standing.\n"
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
        description="the rows this line's figure is printed on — note rows, face rows, or both; "
                    "empty means neither the supplied notes nor the supplied statement state it, "
                    "and leaves any figure already found standing")
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


# THE STATEMENTS AS A FILING PRINTS THEM. `StatementType`'s members are engine tokens
# (`profit_and_loss`), and a request that names one is asking the model to read a word the document
# does not use. Kept beside the payload rather than on the enum because this is the only place that
# needs the printed form — everywhere else compares tokens.
# NOT "CONSOLIDATED". These said "Consolidated statement of financial position" on every line, and a
# line is not consolidated or standalone — the BLOCK it is read from is, and each statement block
# names its own `entity`. An Indian borrower's spread is read from the standalone statements, and a
# model told the line is "printed in" the consolidated one was being steered to the wrong block of a
# filing that prints both.
_STATEMENT_LABEL: dict[str, str] = {
    "balance_sheet": "Statement of financial position (balance sheet)",
    "profit_and_loss": "Statement of profit or loss",
    "cash_flow": "Statement of cash flows",
    "equity_changes": "Statement of changes in equity",
    "notes": "Notes to the financial statements",
    "covenants_supplemental": "Supplemental and covenant data",
    "statement_setup": "Reporting setup — currency, scale and period",
}


#: What each route permits, in the request's own words. Keyed by the token an author declares.
_ROUTE_LABEL = {
    "face": "the face of its statement only — its figure is not in a note",
    "note_tables": "a row of one of its notes only — its figure is not on the face of a statement",
    "prose": "a sentence in one of its notes only — its figure is not on the face of a statement",
    "anywhere": "anywhere in the filing — a statement, a note, or any other page supplied",
}


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
    # `note_source` IS NOT READ HERE ANY MORE. Every field of it this function used to send is a
    # DETERMINISTIC-route artefact (see the block below), and which NOTES the line is searched in —
    # the half `note_source` still decides — reaches the request as `notes_supplied`, resolved by
    # `services.line_item_notes` before the call rather than described to the model.
    entry: dict = {
        "key": item.key,
        "label": item.label or item.key,
        "notes_supplied": list(notes_for_item),
    }
    # ONE PROSE FIELD, not two with a fallback. This used to read `description` when `definition`
    # was empty — a branch that never fired, because all 539 lines declare a definition. The 85
    # `description` values went to `prompt` (77) and `definition` (8) when the field was removed.
    if getattr(item, "definition", ""):
        entry["definition"] = item.definition
    # NO `include` ANY MORE — the field is gone from the schema; see its tombstone there. `exclude`
    # remains, and is omitted entirely when empty rather than sent as an empty list: a key whose
    # value says nothing still costs the model a line to read and invites it to infer that the
    # author had nothing to exclude, which is not the same as not having been asked.
    if exclude := list(getattr(item, "exclude_criteria", None) or ()):
        entry["exclude"] = exclude
    # THE RECOGNITION FIELDS ARE NOT SENT, and that is now a contract rather than an omission.
    #
    # `aliases` / `aliases_i18n` used to travel as `printed_as` / `printed_as_by_language`, on the
    # reading that knowing how a filing spells a line helps a model find its row. They are the
    # DETERMINISTIC route's evidence — the alias index `services.mapping` matches on — and the
    # configuration now separates the two routes explicitly: what an author writes to make the
    # lexical matcher recognise a caption may not also steer the model, because then a blank
    # deterministic tab and a filled one ask the model two different questions and no answer is
    # attributable to the definition alone. An author who wants the model to know a spelling writes
    # it in the definition, where it is visibly part of the instruction.
    #
    # The same rule removed `row_is_called` / `row_is_never_called` / `row_caption_matches` /
    # `row_caption_must_not_match` below, and with them the `row_terms` floor the answer used to be
    # graded against (`stages.line_item_llm`) — grading against a constraint the request never
    # carried is the unfairness this split exists to remove. `row_terms` is still read by the
    # DETERMINISTIC readers (`stages.map_ontology`, `services.note_sourced`), which is where it now
    # belongs and its only remaining home.
    # WHERE THE LINE IS PRINTED, and it replaces a sentence that said the same thing in prose.
    #
    # `section_disambiguation` used to be sent here as `how_to_tell_it_apart`, on 375 of the 518
    # lines the model is asked about. Measured, its 395 authored values hold THIRTEEN distinct
    # strings and every one of them is `"Bind only to {statement} / {section}."` — generated from
    # the line's own placement, not authored about the line. It was still the only placement signal
    # the request carried, so deleting it would have removed real information; the fix is to send
    # the placement itself.
    #
    # THE STATEMENT GOES AND THE SECTION KEY DOES NOT. `bs_nca` means nothing to a reader of a
    # filing, and naming an engine key was the generated sentence's other flaw — it is also why
    # `do_not_confuse_with` is no longer sent: its four lines carried canonical keys
    # (`bs_nca__land_use_rights`) that appear nowhere in the request. The field stays, because
    # `line_item_matching._mutually_confusable` reads it to settle two contested captions against
    # each other; what went is a payload key the model could not act on.
    # `.value`, NOT `str()`. `StatementType` subclasses `str` AND `Enum`, and `Enum.__str__` wins:
    # `str(StatementType.BALANCE_SHEET)` is `"StatementType.BALANCE_SHEET"`, which matches no label
    # and would have reached the model as "StatementType.BALANCE SHEET" on every one of 441 lines.
    raw = getattr(item, "statement", None)
    statement = str(getattr(raw, "value", raw) or "")
    if statement:
        entry["printed_in"] = _STATEMENT_LABEL.get(statement, statement.replace("_", " "))
        # THE TOKEN AS WELL AS THE PROSE, so the line can be joined to its rows. `printed_in` is a
        # reader's label ("Statement of financial position (balance sheet)") while
        # `services.face_context` keys its blocks by the token, and a model asked to match one
        # against the other is being asked to guess at a mapping neither side states.
        # ONLY FOR A LINE THAT MAY BE READ OFF THE FACE. The token is the JOIN KEY a citation
        # names to point at a printed statement row, so sending it to a line whose route says the
        # statement is not its source is an invitation to cite a row that will then be refused
        # (`services.note_sourced.resolve_sources`, `allow_face=False`). `printed_in` stays either
        # way: "Notes to the financial statements" is where those 19 lines say they are printed,
        # and it is prose rather than a key the model can cite.
        if line_item_routes.may_read_face(item):
            entry["statement"] = statement
    # WHERE THIS LINE MAY BE READ FROM, said in one word rather than left to be inferred.
    #
    # The routes are not all the same question and the request used to express none of them. A
    # `note_tables` line is not "a line that happens to have notes attached" — its author said the
    # figure is printed in a note and NOT on the statement — and a request that merely omitted the
    # statement block left the model to guess whether the statement was absent or forbidden. The
    # four values are the four routes, spelled for a reader rather than as engine tokens, and
    # `line_item_llm.REPLY_CONTRACT` says what each one permits a citation to name.
    #
    # OMITTED FOR A LINE THAT DECLARES NO ROUTE, which is 100 of the 506 asked-about lines.
    # Absence is "nothing was said", and stating a permission the author never wrote would make
    # the silent majority read as the permissive route by assertion instead of by convention.
    if route := line_item_routes.declared_route(item):
        entry["read_from"] = _ROUTE_LABEL[route]
    # `prompt` IS NO LONGER A SECOND FIELD. Definition and prompt are one authored thing now — the
    # merge is done on the way in (`schemas.line_items.LineItemDef.definition`), so there is nothing
    # left to append here and no `instruction` key. A set written before the merge still loads, and
    # its `prompt` is folded into `definition` by the loader rather than re-appended here, so the
    # request carries the same words either way and only one key can hold them.
    # WHETHER THIS LINE IS PRINTED AS A NEGATIVE. An expense disclosed in brackets and the same
    # expense disclosed unsigned are the same fact, and a locator told nothing about the
    # convention has no way to know a bracketed figure is the row it was looking for.
    if getattr(item, "sign_convention", ""):
        entry["sign_convention"] = str(item.sign_convention)
    return entry


def build_request(plan, by_key: dict, notes_of: dict, identified: list[dict],
                  face: list[dict] | None = None,
                  other_pages: list[dict] | None = None) -> dict:
    """The user message for one request: the lines, then the notes and the statement they share.

    THE NOTES GO ONCE, BESIDE THE LINES, not inside each one. That is the entire saving grouping
    buys and it is a shape decision rather than a budget one: measured on the reference filing the
    identified notes are 95,188 of a ~196,000-character request, and attaching them per line to a
    twelve-line group would be about a megabyte — which fails a provider outright rather than
    merely costing more. Each line says which of them ITS configuration selected
    (`notes_supplied`), so a shared request is still answerable line by line.

    AND THE STATEMENT GOES THE SAME WAY, for a line that has no notes to be given. A face line
    selects none, so its note block is empty and the contract's advice in that position is to
    answer with an empty `sources` — 343 of the 506 asked-about lines. `face` is
    `services.face_context.face_rows`, the printed rows of the statements this request's lines are
    gated to; it is supplied once for the same reason the notes are, and each line says which
    statement is its own (`printed_in`, already sent).

    `face` DEFAULTS TO NONE RATHER THAN BEING REQUIRED, so every existing caller — the audit
    scripts among them — keeps working and a run with no face context is the behaviour that was
    there before.

    `other_pages` IS THE `anywhere` ROUTE'S OWN BLOCK, and it is supplied only to a request that
    holds a line declaring that route. It is `services.face_context.other_page_rows` — the printed
    rows of the pages that are neither a statement nor a note, which exist at all only because
    that declaration widened the extractor. Sent last because it is the least likely place a
    figure is printed and the most expensive block to read: it is every remaining page of the
    filing, where the note and statement blocks are a selection.
    """
    wanted = set(plan.notes)
    out = {
        "line_items": [line_item_payload(by_key[k], notes_of.get(k, ()))
                       for k in plan.keys if k in by_key],
        # Only the notes THIS request's lines selected. An identified note for a line in another
        # request is not context, it is noise the model has to rule out.
        "notes": [n for n in identified if str(n.get("note", "")) in wanted] if wanted else [],
    }
    # OMITTED ENTIRELY WHEN EMPTY, not sent as `[]`. A key whose value says nothing still costs the
    # model a line to read and invites it to infer that the statement was looked for and not found
    # — the same reason `line_item_payload` omits an empty `exclude`.
    if face:
        out["statement_rows"] = face
    # SAME RULE, ONE LEVEL WIDER: omitted entirely when empty rather than sent as an empty list,
    # so a request for a line with no `anywhere` declaration says nothing about other pages rather
    # than saying there are none.
    if other_pages:
        out["other_pages"] = other_pages
    return out


# THE FULL-DOCUMENT LAYOUT, said once in the system text. It replaces nothing in the contract
# above; it says where the notes and statements now are, because the contract's sentence about
# `notes` ("the ones this line's configuration selected") describes the other mode.
FULL_DOCUMENT_GUIDE = (
    "THE WHOLE DOCUMENT IS SENT ONCE, BEFORE THE QUESTIONS. The first message is the filing: "
    "`notes` is EVERY note it prints, not only those a line selected, and `statement_rows` is "
    "every statement as printed. Each line item's `notes_supplied` still names the notes its "
    "configuration points to — look there first — but a figure printed in any note or statement "
    "of the document may be cited. The second message holds the line items to answer.")


def build_document(doc, line_item_set, *, cited=None) -> dict:
    """The whole document, in the shape a request's note and statement blocks already have.

    Every extracted note (`note_context.identified_notes(..., every_note=True)`, filing order) and
    every statement's printed rows (`face_context.face_rows` over every labelled statement page),
    plus the other-pages block where the extractor built one. Built ONCE per run and sent
    unchanged on every request, so it is byte-identical from call to call — the property a prompt
    cache keys on. Nothing in it varies per request: no line, no count, no time.
    """
    from app.services import face_context
    statements = {str(getattr(pg, "statement", "") or "") for pg in (getattr(doc, "pages", None) or ())}
    statements.discard("")
    statements.discard("None")
    out = {"notes": note_context.identified_notes(line_item_set, doc.notes, cited=cited,
                                                  every_note=True),
           "statement_rows": face_context.face_rows(doc, sorted(statements))}
    other = face_context.other_page_rows(doc)
    if other:
        out["other_pages"] = other
    return out


def document_message(document: dict) -> str:
    """The first user message of a full-document request. Deterministic: fixed lead-in, fixed keys."""
    return ("THE DOCUMENT (every note and statement of this filing). The questions follow in the "
            "next message.\n" + json.dumps(document, ensure_ascii=False, indent=2))


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


def combine_terms(resolved: list[dict], unresolved: list[dict], signs: list[int],
                  component: bool, fallback_period: str) -> tuple[dict, list[dict]]:
    """Every cited term in the model's OWN order — verified or not — summed, plus what was not.

    ``(figures, unverified)``. `figures` is per period as before; `unverified` lists the terms that
    contributed a figure nobody could confirm, so the caller can flag the line and mark them.

    WHY AN UNVERIFIED TERM IS STILL COUNTED, which reverses the older rule that a figure the model
    stated rather than located is simply refused. A component answer is ARITHMETIC: "this line is
    500,000 less 120,000". Dropping the 120,000 does not make the claim smaller, it makes it a
    DIFFERENT claim — and the old path published the 500,000 alone, as the line's figure, with
    nothing saying a term had gone. Worse, `signs` is positional and was not filtered alongside the
    entries, so dropping the first term moved the second to index 0 and a declared deduction
    published as an addition. Both are fixed by keeping every term, in position, and reporting which
    could not be confirmed.

    THREE KINDS OF TERM, and only the third is new:

      * VERIFIED ROW — resolved against an extracted row, figures per period. Counted.
      * REFUSED CAPTION — resolved, so the figure is a real extracted one; only the caption failed
        the row-terms floor. Counted, and reported unverified: the number is real, its identity is
        not.
      * STATED BUT NOT FOUND — the model gave an amount that is not in the note's text. Counted at
        `fallback_period` because a bare amount carries no column, and reported unverified. This is
        the one that publishes a figure nobody located, which is why the caller must raise a review
        flag rather than merely log it.

    A citation with no figure at all — no matching row and no stated amount — contributes nothing
    and is not in `unverified` either: there is no term to mark, only a citation that named nothing.
    """
    terms: list[tuple[int, dict, bool]] = [(int(e.get("at", i)), e, True)
                                           for i, e in enumerate(resolved)]
    terms += [(int(e.get("at", 10_000 + i)), e, False) for i, e in enumerate(unresolved)]
    terms.sort(key=lambda x: x[0])

    out: dict[str, Decimal] = {}
    unverified: list[dict] = []
    first_whole: set[str] = set()
    for at, entry, was_resolved in terms:
        sign = signs[at] if at < len(signs) and signs[at] in (1, -1) else 1
        figures = dict(entry.get("figures") or {})
        confirmed = was_resolved and not entry.get("row_terms_refused")
        if not figures and not was_resolved:
            stated = _amount(entry.get("amount"))
            if stated is None:
                continue                      # named nothing — no term to count or to mark
            figures = {fallback_period: str(stated)}
        if not figures:
            continue
        if not confirmed:
            unverified.append({**entry, "sign": sign})
        for period, raw in figures.items():
            value = _amount(raw)
            if value is None:
                continue
            if component:
                out[period] = out.get(period, Decimal(0)) + (value * sign)
            elif period not in first_whole:
                # WHOLE: each citation states the line's own figure, so the first that resolves is
                # taken and the rest corroborate. Summing them would publish a figure printed
                # nowhere — a face line plus the note total behind it.
                out[period] = value
                first_whole.add(period)
    return out, unverified


def ask(provider, system: str, request: dict, *, max_tokens: int,
        document: str | None = None) -> LineItemReply:
    """One provider call. Raises whatever the provider raises — the caller records it per request.

    The whole request is the unit of failure on purpose. A reply that does not validate is not a
    partial answer, and a request that fails leaves its lines to the deterministic route, which is
    a defined outcome rather than a degraded one.
    """
    # WITH A DOCUMENT, TWO MESSAGES: the document first — identical on every call, so the prompt
    # up to its end is a reusable prefix — and the question after it. Without one, the request is
    # one message as before.
    question = {"role": "user", "content": json.dumps(request, ensure_ascii=False, indent=2)}
    messages = ([{"role": "user", "content": document}, question] if document else [question])
    reply, meta = provider.complete_structured(
        system=system,
        messages=messages,
        response_schema=LineItemReply,
        max_tokens=max_tokens,
    )
    return reply, meta


def plan_and_notes(line_item_set, notes, settings, *, doc=None):
    """``(plans, by_key, notes_of, identified)`` — everything a run needs, computed once.

    `identified_notes` is document-level (its IDF is a property of the filing), so it is built once
    here and sliced per request rather than rebuilt per call.

    `doc` IS OPTIONAL AND ONLY THE CITATIONS NEED IT. Passing it lets `note_sets` put the notes the
    filing prints against a line's face row ahead of anything scored — see
    `line_item_notes.cited_notes`. Every caller that has a document should pass it; the ones that
    do not (the audit scripts, which are handed reconstructed note tables and no face rows) keep
    the pure-scoring behaviour rather than being rewritten to fake a document.
    """
    items = [i for i in (getattr(line_item_set, "items", None) or [])
             if line_item_requests.asked_about(i)]
    by_key = {i.key: i for i in items}
    cited = line_item_notes.cited_notes(doc)
    plans = line_item_requests.plan_requests(line_item_set, notes, settings,
                                             cited=cited)
    notes_of = {p.keys[0] if len(p.keys) == 1 else k: p.notes
                for p in plans for k in p.keys}
    identified = note_context.identified_notes(line_item_set, notes, cited=cited)
    return plans, by_key, notes_of, identified


def resolve(answer: LineItemAnswer, notes, face=None, *, allow_face: bool = True,
            pages=None, allow_pages: bool = False, allow_rows: bool = True, sections=None
            ) -> tuple[list[dict], list[dict], dict[str, Decimal]]:
    """``(resolved, unresolved, figures)`` for one answer. The model supplies none of the figures.

    `face` is `services.face_context.face_index(doc)` — what a citation naming a STATEMENT rather
    than a note is looked up in. It defaults to None so a caller with no document (the audit
    scripts) keeps working, and a citation naming a statement then reports unresolved rather than
    silently resolving against the notes.

    `allow_rows=False` is a `prose` line: `services.line_item_routes.may_read_table_rows`. Its
    author said the figure is in a sentence, so a citation naming a table ROW — in a note as much as
    on the face — is refused, and the prose branch (an amount verified against the note's own text)
    is the one way it is answered.

    `allow_face=False` is a line whose route says the face is not its source — `note_tables` or
    `prose`. Its request carries no statement block and a citation naming one is refused with that
    as the reason (`services.note_sourced.resolve_sources`).

    `pages` / `allow_pages` are the same pair for the `anywhere` route: the index of the rows on
    pages that are neither a statement nor a note, and whether THIS line may cite one. Both are
    off by default, so a caller that knows nothing about the route keeps the behaviour it had.

    `sections` are the banner tokens of the line's own `section_scope`; they decide between face
    rows printed with the same caption under different banners (`note_sourced.resolve_sources`).
    """
    resolved, unresolved = note_sourced.resolve_sources(answer.sources, notes, face,
                                                        allow_face=allow_face,
                                                        pages=pages, allow_pages=allow_pages,
                                                        allow_rows=allow_rows, sections=sections)
    component = str(answer.role or "").strip().lower() == "component"
    return resolved, unresolved, figures_of(resolved, list(answer.signs or ()), component)
