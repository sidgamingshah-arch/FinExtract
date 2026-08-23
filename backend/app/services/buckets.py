"""Segment a filing's extracted source into the eight buckets an analyst reads it in.

THE TAXONOMY IS MIXED ON PURPOSE, because that is how a balance sheet and an income statement are
actually read: five of the buckets are BALANCE SHEET SECTIONS, two are WHOLE STATEMENTS, and one is
the remainder. So the resolver asks two questions in order — which statement, then which section —
rather than one.

WHY IT CANNOT RUN STRAIGHT AFTER PAGE CLASSIFICATION. Classification answers "which statement is
printed on this page". A balance sheet prints four or five of these buckets on ONE page, so the
page's statement can never separate them: only a row's own section can, and a row's section is
known once reconstruction has read its banner and mapping has resolved its concept. The segmentation
therefore runs at the END of the pipeline, over the finished document.

THE SECTION → BUCKET EDGE IS DERIVED, NOT TABULATED. A template's section ids carry the section
phrase (``bs_s2_current_assets``), and that phrase is already the codebase's section vocabulary —
``mapping.HEADING_ROW_SECTIONS`` holds the same eight tokens the extractor recognises as printed
banners. So the ordinal prefix is stripped and the remainder is looked up in that vocabulary, which
means a rulebook and this layer cannot drift into two different ideas of what "current assets" is.
The remainder is matched against that vocabulary as a PHRASE and not as a key, because a rulebook
writes the section's own wording ("equity and reserves") where the vocabulary writes the canonical
one ("equity") — see ``_bs_bucket_of_token``. A balance-sheet section no vocabulary phrase names is
reported in ``unknown_sections`` rather than quietly counted as Others.

WHAT LANDS IN OTHERS, and the distinction the store keeps:

* the balance sheet's own totals (``bs_top_level``: total assets, total liabilities and equity).
  These are not in any one section — they span them — so no section bucket can hold them without
  being wrong. Deliberate, and not a failure.
* the statement of changes in equity, which is the EQUITY bucket rather than Others: it is that
  section's movement, and an analyst asking for equity wants it.
* a row nothing could place. Also Others, but recorded in ``unresolved_face_item_ids``, because
  that one IS a coverage failure and must not be indistinguishable from the first.
"""
from __future__ import annotations

import re

from app.core.models.buckets import BucketedSource, BucketSegment
from app.core.models.document import DocumentModel
from app.core.models.enums import PageKind
from app.services.mapping import HEADING_ROW_SECTIONS

# In the reviewer's own order — this is a presentation vocabulary, and the order is the one an
# analyst reads a filing in, not alphabetical.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("non_current_assets", "Non-current assets"),
    ("current_assets", "Current assets"),
    ("non_current_liabilities", "Non-current liabilities"),
    ("current_liabilities", "Current liabilities"),
    ("equity", "Equity"),
    ("profit_and_loss", "P&L"),
    ("cash_flow", "Cash flow"),
    ("others", "Others"),
)
BUCKET_KEYS: tuple[str, ...] = tuple(k for k, _ in BUCKETS)
BUCKET_LABELS: dict[str, str] = dict(BUCKETS)
OTHERS = "others"

# The five balance-sheet section tokens, each of which is also a `HEADING_ROW_SECTIONS` member —
# asserted at import so a rename on either side is a startup failure, not a silent mis-file.
_BS_SECTION_BUCKETS: dict[str, str] = {
    "non_current_assets": "non_current_assets",
    "current_assets": "current_assets",
    "non_current_liabilities": "non_current_liabilities",
    "current_liabilities": "current_liabilities",
    "equity": "equity",
}
assert set(_BS_SECTION_BUCKETS) <= HEADING_ROW_SECTIONS, (
    "a balance-sheet bucket names a section phrase the extractor does not recognise as a banner")

# The vocabulary as word tuples, longest phrase first — the ordering IS the tie-break in
# ``_bs_bucket_of_token``, so it is built once here rather than re-sorted per row.
_BS_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = tuple(sorted(
    ((tuple(token.split("_")), bucket) for token, bucket in _BS_SECTION_BUCKETS.items()),
    key=lambda pair: len(pair[0]), reverse=True))


def _contains(words: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """Does ``phrase`` occur in ``words`` as a run of whole words?"""
    n = len(phrase)
    return any(words[i:i + n] == phrase for i in range(len(words) - n + 1))


def _bs_bucket_of_token(token: str) -> str | None:
    """The balance-sheet bucket a section phrase names, or ``None`` if none does — or if two do.

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
    hits = [(phrase, bucket) for phrase, bucket in _BS_PHRASES if _contains(words, phrase)]
    if not hits:
        return None
    best_phrase, best_bucket = hits[0]
    for phrase, bucket in hits[1:]:
        if bucket != best_bucket and not _contains(best_phrase, phrase):
            return None
    return best_bucket

# A statement whose every section falls in one bucket, so the section never has to be consulted.
_STATEMENT_BUCKETS: dict[str, str] = {
    "profit_and_loss": "profit_and_loss",
    "cash_flow": "cash_flow",
    # The statement of changes in equity IS the equity section's movement — see the module docstring.
    "equity_changes": "equity",
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
    return _SECTION_PREFIX.sub("", section or "").strip().lower()


def bucket_of(section: str | None, statement: str | None) -> tuple[str, str]:
    """``(bucket, reason)`` for one row's section and statement.

    ``reason`` is returned rather than logged because the caller has to tell a row that BELONGS in
    Others from one that only ended up there — see ``BucketedSource.unresolved_face_item_ids``.
    """
    # A section id carries its own statement, so the answer does not depend on the caller having a
    # page to read: a note's rows are placed from their sections alone.
    statement = statement or statement_of_section(section)
    if statement in _STATEMENT_BUCKETS:
        return _STATEMENT_BUCKETS[statement], "statement"
    token = section_token(section or "")
    if (bucket := _bs_bucket_of_token(token)) is not None:
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


def _section_by_key(ontology) -> dict[str, str]:
    """canonical_key -> its resolved ``section_scope``. Empty for an unresolved definition, which
    makes every row fall through to its page's statement rather than to a half-right section."""
    out: dict[str, str] = {}
    for m in getattr(ontology, "mappings", []) or []:
        if m.section_scope:
            out[m.canonical_key] = m.section_scope[0]
    return out


def _statement_by_page(doc: DocumentModel) -> dict[int, str]:
    return {p.index: p.statement for p in doc.pages if p.statement}


def _pages_of_item(li) -> list[int]:
    return sorted({ev.provenance.page_index for ev in li.values.values()
                   if ev.provenance is not None and ev.provenance.page_index is not None})


def segment_source(doc: DocumentModel, ontology=None) -> BucketedSource:
    """The whole segmentation. Every face row and every note lands in exactly one bucket."""
    section_by_key = _section_by_key(ontology) if ontology is not None else {}
    stmt_by_page = _statement_by_page(doc)
    note_pages = {p.index for p in doc.pages if p.kind == PageKind.NOTES}

    segments = {k: BucketSegment(bucket=k, label=BUCKET_LABELS[k]) for k in BUCKET_KEYS}
    out = BucketedSource(segments=[segments[k] for k in BUCKET_KEYS])
    unknown: set[str] = set()
    # Which buckets cite each note, and how often — the note's own bucket is decided from this.
    cited_by: dict[str, dict[str, int]] = {}

    for li in doc.line_items:
        pages = _pages_of_item(li)
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
        if pages and all(p in note_pages for p in pages):
            continue
        section = section_by_key.get(li.canonical_key or "")
        statement = next((stmt_by_page[p] for p in pages if p in stmt_by_page), None)
        bucket, reason = bucket_of(section, statement)
        seg = segments[bucket]
        seg.face_item_ids.append(str(li.id))
        if section and section not in seg.sections:
            seg.sections.append(section)
        for p in pages:
            if p not in seg.face_pages:
                seg.face_pages.append(p)
        if reason == "unresolved":
            out.unresolved_face_item_ids.append(str(li.id))
        elif reason == "unknown_section" and section:
            unknown.add(section)
        for ref in li.note_refs:
            for number in (ref.numbers or ([ref.raw] if ref.raw else [])):
                cited_by.setdefault(str(number), {}).setdefault(bucket, 0)
                cited_by[str(number)][bucket] += 1

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
            one, reason = _bucket_from_note_content(note, section_by_key)
            placed = [one]
        for bucket in placed:
            seg = segments[bucket]
            seg.note_numbers.append(note.note_number)
            for page in note.source_pages:
                if page not in seg.note_pages:
                    seg.note_pages.append(page)
            # Marked in every bucket holding it, so a reader that ADDS the buckets up can subtract
            # the overlap. The note's figures genuinely appear more than once in this store; what
            # stops that becoming a silent double count is that each copy says so.
            if len(placed) > 1 and note.note_number not in seg.shared_notes:
                seg.shared_notes.append(note.note_number)
        if reason == "unresolved":
            out.unresolved_note_numbers.append(note.note_number)

    for seg in out.segments:
        seg.face_pages.sort()
        seg.note_pages.sort()
        seg.sections.sort()
    out.unknown_sections = sorted(unknown)
    return out


def _bucket_from_note_content(note, section_by_key: dict[str, str]) -> tuple[str, str]:
    """A note no face row cites, placed from what its own rows mapped to.

    The face citation is the stronger signal and is tried first: a note titled "Trade and other
    receivables" whose rows the mapper could not place would otherwise land in Others while the face
    line pointing at it sits in current assets.
    """
    tally: dict[str, int] = {}
    for item in note.items:
        section = section_by_key.get(item.canonical_key or "")
        if not section:
            continue
        # No statement is passed: a note is printed on a notes page, so there is none to read, and
        # ``bucket_of`` derives it from the section id itself. Deriving it here as well would be the
        # same quantity computed in two places.
        bucket, reason = bucket_of(section, None)
        if reason in ("section", "statement"):
            tally[bucket] = tally.get(bucket, 0) + 1
    if not tally:
        return OTHERS, "unresolved"
    return max(tally.items(), key=lambda kv: (kv[1], -BUCKET_KEYS.index(kv[0])))[0], "note_content"
