"""Segment a filing's extracted source into the thirteen sections an analyst reads it in.

THE TAXONOMY IS THE FACE OF THE STATEMENTS, section by section: the balance sheet's five, the income
statement's four (income, expenses, interest, and the non-operating remainder), the cash-flow
statement's three activities, and the statement of changes in equity. Plus ``others`` for what none
of them names — which is not a failure and is measured, see below.

SECTION FIRST, statement second — and that is a change from the eight-bucket taxonomy this replaces,
where P&L and cash flow were WHOLE-STATEMENT buckets and the statement therefore answered for every
row on the page. Splitting them means the section is the only thing that can separate one activity
from another, so a P&L or cash-flow row whose concept resolved no section is now Others/unresolved
rather than filed under its page. That is a coverage fact becoming visible, not a row being lost:
``unresolved_face_item_ids`` names every one of them.

THE STATEMENT STILL ANSWERS FOR ONE THING. A row printed on the statement of changes in equity
belongs to that statement whatever its concept's section says, because the movement of a reserve
through the year is that statement's content and not the balance sheet's closing position.

WHY IT CANNOT RUN STRAIGHT AFTER PAGE CLASSIFICATION. Classification answers "which statement is
printed on this page". A balance sheet prints four or five of these buckets on ONE page, so the
page's statement can never separate them: only a row's own section can, and a row's section is
known once reconstruction has read its banner and mapping has resolved its concept. The segmentation
therefore runs at the END of the pipeline, over the finished document.

THE SECTION → BUCKET EDGE IS DERIVED, NOT TABULATED. A template's section ids carry the section
phrase (``bs_s2_current_assets``), so the ordinal prefix is stripped and the remainder is looked up
in the table below — which is also asserted to cover every banner the EXTRACTOR recognises
(``mapping.HEADING_ROW_SECTIONS``), so a section a page can produce geometrically always has a home.
The remainder is matched as a PHRASE and not as a key, because a rulebook writes the section's own
wording ("equity and reserves") where the table writes the canonical one ("equity") — see
``_bucket_of_token``. A section no phrase names is reported in ``unknown_sections`` rather than
quietly counted as Others.

WHAT LANDS IN OTHERS, and the distinction the store keeps:

* a statement's own totals (``bs_top_level``, ``pl_top_level``, ``cf_top_level``: total assets,
  profit for the year, cash at the end of the year). These are not in any one section — they span
  them — so no section bucket can hold them without being wrong. Deliberate, not a failure.
* a section this taxonomy does not name (other comprehensive income, the two "attributable to"
  sections). Listed explicitly in ``_OUTSIDE_TAXONOMY`` and reported with the section that produced
  them, because the alternative is being pulled into Income by the word "income".
* the statement of changes in equity, which had been the EQUITY bucket rather than Others: it is that
  section's movement, and an analyst asking for equity wants it.
* a row nothing could place. Also Others, but recorded in ``unresolved_face_item_ids``, because
  that one IS a coverage failure and must not be indistinguishable from the first.
"""
from __future__ import annotations

import re

from app.core.models.buckets import BucketedSource, BucketSegment
from app.core.models.document import DocumentModel
from app.core.models.enums import PageKind, PrintedIn
from app.services.mapping import (
    HEADING_ROW_SECTIONS,
    normalize_statement,
    section_of_banner,
    section_of_key,
)

# In the reviewer's own order — this is a presentation vocabulary, and the order is the one an
# analyst reads a filing in, not alphabetical.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("current_assets", "Current assets"),
    ("non_current_assets", "Non-current assets"),
    ("current_liabilities", "Current liabilities"),
    ("non_current_liabilities", "Non-current liabilities"),
    ("equity", "Equity & reserves"),
    ("income", "Income"),
    ("expenses", "Expenses"),
    ("interest", "Interest"),
    ("non_operating", "Non-operating income & expenses"),
    ("cash_flow_operating", "Cash flow from operations"),
    ("cash_flow_investing", "Cash flow from investing"),
    ("cash_flow_financing", "Cash flow from financing"),
    ("changes_in_equity", "Statement of changes in equity"),
    ("others", "Others"),
)
BUCKET_KEYS: tuple[str, ...] = tuple(k for k, _ in BUCKETS)
BUCKET_LABELS: dict[str, str] = dict(BUCKETS)
OTHERS = "others"

_COMPACT_SECTION_TOKENS: dict[str, str] = {
    "bs_nca": "non_current_assets",
    "bs_ca": "current_assets",
    "bs_ncl": "non_current_liabilities",
    "bs_cl": "current_liabilities",
    "bs_equity": "equity",
    "is_oci": "other_comprehensive_income",
    "cf_oper_indirect": "cash_flow_from_operating_activities",
    "cf_oper_direct": "cash_flow_from_operating_activities",
    "cf_investing": "cash_flow_from_investing_activities",
    "cf_financing": "cash_flow_from_financing_activities",
    "is_pl": "income_and_expenses",
    "is_retained": "adjustments_to_retained_profits",
}

# Section token → bucket. The tokens are the template's own section phrases with the statement and
# ordinal stripped (``bs_s2_current_assets`` → ``current_assets``), so this table and a rulebook
# cannot drift into two ideas of what a section is.
#
# INTEREST HAS NO ROW HERE, and cannot: no statement prints an "interest" section. Finance costs and
# interest income are printed among the non-operating items, and the two balance-sheet captions that
# read like interest ("Interests in associates", "Non-controlling interests") are not interest at
# all — which is exactly why a keyword rule in code would be wrong. A concept says so itself, in the
# rulebook, through ``analyst_bucket``; see :func:`bucket_of`.
_SECTION_BUCKETS: dict[str, str] = {
    # balance sheet
    "non_current_assets": "non_current_assets",
    "current_assets": "current_assets",
    "non_current_liabilities": "non_current_liabilities",
    "current_liabilities": "current_liabilities",
    "equity": "equity",
    # income statement. Tax is an expense; exceptional items are the non-operating remainder.
    "income": "income",
    "expenses": "expenses",
    "tax_expense": "expenses",
    "non_operating_expenses": "non_operating",
    "exceptional_items": "non_operating",
    # cash flow, by activity
    "cash_flow_from_operating_activities": "cash_flow_operating",
    "cash_flow_from_investing_activities": "cash_flow_investing",
    "cash_flow_from_financing_activities": "cash_flow_financing",
}
# Every section the EXTRACTOR can read off a printed banner must have a bucket, or a row it places
# geometrically would arrive here unplaceable. Asserted at import so a rename on either side is a
# startup failure rather than a silent mis-file. (The reverse does not hold and must not be
# asserted: the template declares P&L sections the extractor recognises no banner for.)
assert HEADING_ROW_SECTIONS <= set(_SECTION_BUCKETS), (
    "the extractor recognises a section banner no bucket claims: "
    f"{sorted(HEADING_ROW_SECTIONS - set(_SECTION_BUCKETS))}")

# Sections a filing really prints that this taxonomy does not name. Listed EXPLICITLY, because the
# phrase matching below would otherwise pull them in on a word they contain — "other comprehensive
# income" and "total comprehensive income attributable to" both contain "income", and neither is the
# income statement's revenue section. They land in Others with a reason that says which section they
# came from, so a whole section of a filing is never silently swallowed.
_OUTSIDE_TAXONOMY: frozenset[str] = frozenset({
    "other_comprehensive_income",
    "income_and_expenses",
    "adjustments_to_retained_profits",
    "profit_attributable_to",
    "total_comprehensive_income_attributable_to",
})

# The vocabulary as word tuples, longest phrase first — the ordering IS the tie-break in
# ``_bucket_of_token``, so it is built once here rather than re-sorted per row.
_SECTION_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = tuple(sorted(
    ((tuple(token.split("_")), bucket) for token, bucket in _SECTION_BUCKETS.items()),
    key=lambda pair: len(pair[0]), reverse=True))


def _contains(words: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """Does ``phrase`` occur in ``words`` as a run of whole words?"""
    n = len(phrase)
    return any(words[i:i + n] == phrase for i in range(len(words) - n + 1))


def _bucket_of_token(token: str) -> str | None:
    """The bucket a section phrase names, or ``None`` if none does — or if two do.

    WHY THIS IS NOT A DICTIONARY LOOKUP. A rulebook writes the section's OWN phrase, and that
    phrase only sometimes equals the canonical one: this filing's rulebook says ``equity_reserves``
    ("Equity and reserves") where the vocabulary says ``equity``, and an exact lookup sent every
    equity row — the whole bucket — to Others as an ``unknown_section``.

    WHY A SUBSTRING TEST WOULD BE WRONG, and why "longest wins" is safe. ``current_assets`` sits
    inside ``non_current_assets``, so a naive match would file the non-current section as current.
    It sits there as a SUFFIX, because English puts the modifier first — so the containing phrase is
    always the longer one, and taking the longest match resolves that collision every time. Matching
    on whole words (not characters) is what makes the run a phrase and not a coincidence.

    A token naming TWO buckets whose phrases are not nested — ``equity_and_non_current_liabilities``
    — is a genuine ambiguity, and gets ``None`` so the caller reports it in ``unknown_sections``
    instead of picking one of the two halves. That guard only sees a rival phrase that is itself a
    whole run: ``current_assets_and_liabilities`` splits ``current`` from ``liabilities`` and so
    reads as current assets, which is the closer of the two answers available from the phrase alone.
    """
    words = tuple(w for w in token.split("_") if w)
    hits = [(phrase, bucket) for phrase, bucket in _SECTION_PHRASES if _contains(words, phrase)]
    if not hits:
        return None
    best_phrase, best_bucket = hits[0]
    for phrase, bucket in hits[1:]:
        if bucket != best_bucket and not _contains(best_phrase, phrase):
            return None
    return best_bucket

# The one statement that answers for its rows whatever their concepts say. Its content IS the
# movement of the reserves through the year, which is a different fact from the balance sheet's
# closing position on the same reserves — so a row printed there belongs to it and not to equity.
#
# Profit-and-loss and cash flow used to be here, each a whole-statement bucket. They are not any
# more: the requested taxonomy splits them by section (income / expenses / interest / non-operating,
# and the three activities), and a statement cannot answer a question its own sections disagree on.
#
# ONE STATEMENT, TWO SPELLINGS, and this table was keyed on the one the CALLER never uses. The page
# classifier — and therefore ``_statement_by_page``, and therefore every face row reaching
# ``bucket_of`` — says "changes_in_equity"; ``StatementType``, which a rulebook's ``statement``
# field validates against, spells the same statement "equity_changes". So the lookup below never
# hit for a face row and the changes-in-equity bucket held nothing: on 澜起科技 688008 the
# "Equity & reserves" segment carried 61 rows of which 15 were balance-sheet equity, the other 46
# being the movements this bucket exists to separate from them. Both spellings are folded through
# ``normalize_statement`` before the lookup, the same way ``mapping`` folds them before the
# statement gate and for the same reason: a table the caller's value cannot be compared to is a
# table that buckets nothing.
_STATEMENT_BUCKETS: dict[str, str] = {
    "changes_in_equity": "changes_in_equity",
}

# ``bs_s2_current_assets`` -> ``current_assets``; ``bs_top_level`` -> ``top_level``.
_SECTION_PREFIX = re.compile(r"^(?:bs|pl|cf|eq)_(?:s\d+[a-z]?_)?")
_SECTION_STATEMENTS: dict[str, str] = {
    "bs": "balance_sheet", "pl": "profit_and_loss", "cf": "cash_flow", "eq": "equity_changes",
}


def statement_of_section(section: str | None) -> str | None:
    """The statement a template section id belongs to, read off its own prefix.

    A note is placed from the sections its rows mapped to, and there is no page to ask: a note on
    operating expenses is printed on a notes page, not on the income statement. Without this, every
    note whose rows resolve to a P&L or cash-flow section would fall to Others — the section token
    alone ("expenses", "cash_flow_from_operating_activities") only answers for the balance sheet.
    """
    head = (section or "").split("_", 1)[0]
    return _SECTION_STATEMENTS.get(head)


def section_token(section: str) -> str:
    """The section phrase a template section id carries, with its statement and ordinal stripped."""
    return (_COMPACT_SECTION_TOKENS.get(section or "")
            or _SECTION_PREFIX.sub("", section or "").strip().lower())


def bucket_of(section: str | None, statement: str | None,
              declared: str | None = None) -> tuple[str, str]:
    """``(bucket, reason)`` for one row's section, statement and DECLARED bucket.

    ``reason`` is returned rather than logged because the caller has to tell a row that BELONGS in
    Others from one that only ended up there — see ``BucketedSource.unresolved_face_item_ids``.

    ``declared`` is the concept's own ``analyst_bucket`` from the rulebook, and it wins outright.
    That is the only way Interest can exist as a tag: no statement prints an interest section, so
    the two P&L concepts that ARE interest (finance costs, interest income) are printed among the
    non-operating items and can only be identified by naming themselves. Data, not a keyword rule in
    code — and the reason it must be data is the balance sheet, where "Interests in associates" and
    "Non-controlling interests" would both be caught by any rule looking for the word.

    A declared bucket that names nothing in this vocabulary is refused rather than obeyed (the
    upload gate rejects it too, see ``schemas.loader``), because obeying it would create a
    fourteenth segment nothing renders and lose the rows into it.
    """
    if declared:
        if declared in BUCKET_LABELS:
            return declared, "declared"
        return OTHERS, "unknown_declared_bucket"
    # A section id carries its own statement, so the answer does not depend on the caller having a
    # page to read: a note's rows are placed from their sections alone.
    statement = normalize_statement(statement or statement_of_section(section)) or None
    if statement in _STATEMENT_BUCKETS:
        return _STATEMENT_BUCKETS[statement], "statement"
    token = section_token(section or "")
    if token in _OUTSIDE_TAXONOMY:
        # A section the filing really prints and this taxonomy does not name. Named so, not swept:
        # the reason travels with the row and the section appears in ``unknown_sections``.
        return OTHERS, "outside_taxonomy"
    if (bucket := _bucket_of_token(token)) is not None:
        return bucket, "section"
    if token == "top_level":
        # A statement's own totals span its sections; no section bucket can hold them.
        return OTHERS, "statement_total"
    # NO SECTION IS "NOTHING PLACED IT", whatever statement the page was. This used to read the
    # statement first, so a balance-sheet row the rulebook never mapped came back as
    # ``unknown_section`` with no section to name — it was then counted as neither unresolved nor
    # unknown, and a row nothing placed disappeared from both of the store's own measurements.
    if section:
        return OTHERS, "unknown_section"
    return OTHERS, "unresolved"


def _section_of_row(hint: str | None, canonical_key: str | None) -> str | None:
    """Section token for one row, from print context first and key namespace second.

    The section banner printed on the page is authoritative. When a condensed statement prints no
    banner, the mapped key's namespace is the fallback so face rows still land in a section bucket
    without consulting ontology section scopes.
    """
    return section_of_banner(hint or "") or section_of_key(canonical_key or "")


def _statement_by_page(doc: DocumentModel) -> dict[int, str]:
    return {p.index: p.statement for p in doc.pages if p.statement}


def _pages_of_item(li) -> list[int]:
    return sorted({ev.provenance.page_index for ev in li.values.values()
                   if ev.provenance is not None and ev.provenance.page_index is not None})


def segment_source(doc: DocumentModel, ontology=None) -> BucketedSource:
    """The whole segmentation. Every face row and every note lands in exactly one bucket."""
    note_numbers = {str(n.note_number) for n in doc.notes if n.note_number is not None}
    stmt_by_page = _statement_by_page(doc)
    note_pages = {p.index for p in doc.pages if p.kind == PageKind.NOTES}

    segments = {k: BucketSegment(bucket=k, label=BUCKET_LABELS[k]) for k in BUCKET_KEYS}
    out = BucketedSource(segments=[segments[k] for k in BUCKET_KEYS])
    unknown: set[str] = set()
    # Which buckets cite each note, and how often — the note's own bucket is decided from this.
    cited_by: dict[str, dict[str, int]] = {}

    for li in doc.line_items:
        from app.stages.face_mapping_contract import is_unclassified_face_key

        pages = _pages_of_item(li)
        # SAY WHERE THE ROW WAS PRINTED, for anything the reader could not. The face reader stamps
        # the rows it produces; what it cannot stamp is a row synthesised LATER — the residual sweep
        # builds face-shaped rows out of note items — and this is the last stage, holding both every
        # row and every page kind, so it is where the remainder is answered. Same test the partition
        # below uses, so the tag and the placement can never disagree.
        if li.printed_in is None:
            li.printed_in = (PrintedIn.NOTES if pages and all(p in note_pages for p in pages)
                             else PrintedIn.FACE)
        # A row printed on a NOTES page is part of that note, not of the face: ``extract_pdf`` reads
        # face and notes pages into the same ``line_items`` list, and the residual sweep synthesises
        # face-shaped rows from note items on top of that. Counting either as a face row would put
        # the note's money in the bucket twice — once through the note, once through the row.
        #
        # THE TEST IS THE PAGE'S CLASSIFICATION, NOT ``note_number``. That field holds the note a
        # row CITES ("Trade receivables … Note 15"), set from the printed reference column by
        # ``row_reconstruct``, not the note a row lives in — its own docstring says otherwise and is
        # wrong. Reading it as membership emptied the face buckets of every row that cites a note,
        # which on a real filing is most of them: a four-row balance sheet placed one row.
        # THE STAMP IS THE TEST, so the tag above and the placement here cannot disagree — they are
        # now one expression rather than two copies of it. Identical to the page test it replaces for
        # every row the reader produced, and different for exactly one case: a row a stage stamped
        # FACE whose figures were read off a NOTES page. That is what a decomposition looks like
        # (``map_ontology._split_from_disclosure`` publishes a face concept from the itemised rows of
        # the note that explains it), and excluding it would leave the section with neither the
        # components nor the aggregate they replaced.
        if li.printed_in is PrintedIn.NOTES:
            continue
        section = _section_of_row(getattr(li, "section_hint", None),
                      getattr(li, "canonical_key", None))
        statement = next((stmt_by_page[p] for p in pages if p in stmt_by_page), None)
        bucket, reason = bucket_of(section, statement, None)
        seg = segments[bucket]
        seg.face_item_ids.append(str(li.id))
        if section and section not in seg.sections:
            seg.sections.append(section)
        for p in pages:
            if p not in seg.face_pages:
                seg.face_pages.append(p)
        if is_unclassified_face_key(li.canonical_key):
            out.unresolved_face_item_ids.append(str(li.id))
        elif reason == "unresolved" and normalize_statement(statement) == "changes_in_equity":
            # Only changes-in-equity is allowed to stay unresolved at section granularity.
            out.unresolved_face_item_ids.append(str(li.id))
        elif reason == "unknown_section" and section:
            unknown.add(section)
        # WHICH NOTES THIS ROW CITES, resolved against the notes that exist by the same rule the
        # linker uses (``LineItem.cited_notes_among``) — so a note is filed under the section of
        # every row the API will show it under, and a row citing "16(b)" of a note table numbered
        # "16" files note 16 here instead of filing nothing and leaving the figure unexplained.
        for number in li.cited_notes_among(note_numbers):
            cited_by.setdefault(number, {}).setdefault(bucket, 0)
            cited_by[number][bucket] += 1

    for note in doc.notes:
        citing = cited_by.get(note.note_number) or {}
        if citing:
            # EVERY citing bucket gets the note, in presentation order. A note on borrowings split
            # across current and non-current belongs to both sections, and an analyst reading either
            # one needs it in front of them — so it is filed in both rather than in the bucket that
            # cites it more, with the other holding a pointer.
            placed = sorted(citing, key=BUCKET_KEYS.index)
            reason = "cited_from_face"
        else:
            # Notes are stored under the face rows that cite them; an uncited note remains
            # unresolved instead of being section-filed by note content alone.
            if note.note_number not in out.unresolved_note_numbers:
                out.unresolved_note_numbers.append(note.note_number)
            continue
        for bucket in placed:
            seg = segments[bucket]
            # ONE ENTRY PER NOTE NUMBER, not per table. This loop walks ``doc.notes``, which holds a
            # ``NotesTable`` per (heading occurrence, page) — so a note printed across two pages, or
            # with a continuation heading, arrives here twice and appended its number twice. The
            # count is served straight to the UI ("notes": len(note_numbers)), so a filing with 19
            # multi-table notes reported more notes in a section than it has. The adjacent
            # ``note_pages`` guard already works this way; this is the same rule for the numbers.
            if note.note_number not in seg.note_numbers:
                seg.note_numbers.append(note.note_number)
            for page in note.source_pages:
                if page not in seg.note_pages:
                    seg.note_pages.append(page)
            # Marked in every bucket holding it, so a reader that ADDS the buckets up can subtract
            # the overlap. The note's figures genuinely appear more than once in this store; what
            # stops that becoming a silent double count is that each copy says so.
            if len(placed) > 1 and note.note_number not in seg.shared_notes:
                seg.shared_notes.append(note.note_number)
        if reason == "unresolved" and note.note_number not in out.unresolved_note_numbers:
            out.unresolved_note_numbers.append(note.note_number)

    for seg in out.segments:
        seg.face_pages.sort()
        seg.note_pages.sort()
        seg.sections.sort()
    out.unknown_sections = sorted(unknown)
    return out


def _bucket_from_note_content(note) -> tuple[str, str]:
    """A note no face row cites, placed from what its own rows mapped to.

    The face citation is the stronger signal and is tried first: a note titled "Trade and other
    receivables" whose rows the mapper could not place would otherwise land in Others while the face
    line pointing at it sits in current assets.
    """
    tally: dict[str, int] = {}
    for item in note.items:
        section = _section_of_row(getattr(item, "section_hint", None),
                      getattr(item, "canonical_key", None))
        if not section:
            continue
        # No statement is passed: a note is printed on a notes page, so there is none to read, and
        # ``bucket_of`` derives it from the section id itself. Deriving it here as well would be the
        # same quantity computed in two places.
        bucket, reason = bucket_of(section, None, None)
        if reason in ("section", "statement"):
            tally[bucket] = tally.get(bucket, 0) + 1
    if not tally:
        return OTHERS, "unresolved"
    return max(tally.items(), key=lambda kv: (kv[1], -BUCKET_KEYS.index(kv[0])))[0], "note_content"
