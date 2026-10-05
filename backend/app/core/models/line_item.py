"""Line items, extracted values, notes, and the face↔note link.

A ``LineItem`` holds a *dict of values keyed by (basis, period)* rather than a
single value, so consolidated and standalone (each with current + prior year) are
represented uniformly and reconciliation/validation operate per basket.
"""
from __future__ import annotations

import re
from collections.abc import Container
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from .confidence import ConfidenceVector
from .enums import (
    Basis,
    PrintedIn,
    LineRole,
    LinkRelationship,
    ReconciliationRole,
    SignConvention,
    ValueSource,
)
from .geometry import Provenance


class UnitContext(BaseModel):
    """Detected scale + currency for a value, at its most-specific scope."""

    # Empty when the document declares no currency — asserting one (this used to default to INR)
    # mislabels every filing that isn't from that jurisdiction. Consumers render "" as unknown.
    currency: str = ""
    scale_factor: Decimal = Decimal(1)   # lakh=1e5, crore=1e7, thousand=1e3, million=1e6
    units_label: str | None = None
    source_bbox_page: int | None = None


class NoteRef(BaseModel):
    raw: str
    numbers: list[str] = Field(default_factory=list)   # "5", ranges expanded
    subrefs: list[str] = Field(default_factory=list)    # "12(a)"


_NOTE_BASE = re.compile(r"^\s*(\d{1,3})")


def _chapter_match(token: str | None, available: Container[str]) -> str | None:
    """The chapter-qualified note a BARE citation names, when exactly one chapter offers it.

    A mainland filing numbers its notes within each top-level chapter, so a note's identity is
    "七、9" (``services.notes_extract.qualified_note_number``) and the face normally prints the
    same — the 附注 column reads 七、9. A BILINGUAL filing is the case this exists for: a chapter
    heading can appear on its notes pages while the face cites plain "15", and the citation would
    then match nothing at all and the row be left with no note behind it.

    ONLY WHEN ONE CHAPTER OFFERS THE NUMBER. If two do, the bare citation genuinely does not say
    which — that ambiguity is the defect chapter-qualification exists to end, and guessing a
    chapter here would reintroduce it one row at a time. Unlinked is the honest answer.

    ``available`` is a Container, which need not be iterable, so the candidates are formed from
    the token rather than by scanning: there are at most a handful of chapters a filing can use.
    """
    bare = (token or "").strip()
    if not bare or "、" in bare:
        return None                       # already qualified, or nothing to qualify
    hits = [f"{numeral}、{bare}" for numeral in _CHAPTER_NUMERALS
            if f"{numeral}、{bare}" in available]
    return hits[0] if len(hits) == 1 else None


def base_note_number(token: str | None) -> str | None:
    """The parent note a printed sub-reference belongs to — ``"16(b)"`` -> ``"16"``.

    ``None`` for a citation that is already a bare number, so a caller can tell "this token has a
    parent" from "this token IS the parent" without comparing strings.
    """
    m = _NOTE_BASE.match(token or "")
    if m is None:
        return None
    return None if m.group(1) == (token or "").strip() else m.group(1)


# Every CJK numeral a top-level chapter is printed with. A filing has never needed past 二十-odd,
# and the list is finite on purpose: ``_chapter_match`` forms candidates from it rather than
# iterating the note index, which is a Container and need not be iterable.
_CHAPTER_NUMERALS: tuple[str, ...] = tuple(
    [c for c in "一二三四五六七八九"] + ["十"]
    + [f"十{c}" for c in "一二三四五六七八九"]
    + [f"{t}十" for t in "二三四五六七八九"]
    + [f"{t}十{c}" for t in "二三四五六七八九" for c in "一二三四五六七八九"]
)


class ValueKey(BaseModel, frozen=True):
    basis: Basis
    period_end: date | None = None
    period_label: str | None = None


class ExtractedValue(BaseModel):
    value_raw: Decimal | None = None      # exactly as printed (paren-negatives applied)
    value: Decimal | None = None          # sign-normalized (units NOT applied)
    # A FACT THAT IS WORDS RATHER THAN A FIGURE — an audit opinion's wording, a going-concern
    # statement, or prose the model wrote for a line whose `output_structure` asks for it.
    #
    # A SEPARATE FIELD, AND THE THREE ABOVE STAY STRICTLY `Decimal`. Widening `value` to
    # `Decimal | str` was the obvious move and it is the wrong one: a survey of this repository
    # found 139 sites that read a value, 45 of which would raise on a string. Almost every one of
    # them is already written as `if ev.value is None: continue` or
    # `ev.value if ev.value is not None else ev.value_raw` — so a text fact that leaves `value` as
    # None is skipped correctly by all of them, with no edit and no chance of a site being missed.
    # Widening would have turned each of those guards into a hole.
    #
    # SO THE ARITHMETIC NEVER SEES IT, and that is the point rather than a limitation: a sentence
    # has no business in a subtotal, a balance identity or a sign check. Contributing 0 to a total
    # would be worse than being absent, because the total would still balance and nothing would say
    # a line had been skipped.
    #
    # NO SEPARATE `kind` DISCRIMINATOR, deliberately. `value_text is not None` already answers
    # "is this fact words?", and it cannot disagree with itself; a `kind` field could say "prose"
    # beside an empty `value_text`, which is the two-places-storing-one-quantity bug this codebase
    # refuses elsewhere. What the line was CONFIGURED to hold is `LineItemDef.output_structure`;
    # what it actually got is this.
    value_text: str | None = None

    @model_validator(mode="after")
    def _a_fact_is_words_or_a_figure_and_never_both(self):
        """Refuse a fact that carries text AND a number. The invariant the whole design rests on.

        WITHOUT THIS THE INVARIANT IS A CONVENTION, and the survey that chose this design found
        exactly how a convention breaks here: pydantic parses `Decimal` in lax mode, so a phrase
        LIFTED FROM THE PAGE whose text happens to read "2024", "3" or "1000" is accepted into
        `value` as a number. From that point it is indistinguishable from a figure — it is summed
        into subtotals (`structural_checks.collect_values`), negated by the unsigned-expense pass
        (`stages.normalize`), and subtracted in the note-to-face tie. An audit-opinion year or a
        covenant threshold printed as text would become a fabricated financial figure, and every
        total containing it would still balance.

        So the two states are made mutually exclusive at construction, where no code path can miss
        it, rather than asserted by each producer. `sign_normalised` goes with them: it records that
        the engine FLIPPED a reported sign, and a sentence has no sign to flip.
        """
        if self.value_text is None:
            return self
        clashes = [name for name in ("value", "value_raw", "reconciled")
                   if getattr(self, name) is not None]
        if clashes:
            raise ValueError(
                f"a fact carrying `value_text` must carry no figure, but {', '.join(clashes)} "
                f"{'is' if len(clashes) == 1 else 'are'} also set — text and a number on one fact "
                f"is how a phrase reading '2024' becomes a figure that every total then balances "
                f"around. Put the words in `value_text` and leave the figures unset.")
        if self.sign_normalised:
            raise ValueError(
                "`sign_normalised` records that a reported figure's sign was flipped, and a text "
                "fact has no sign to flip")
        return self
    # True when the sign of ``value`` was FLIPPED away from ``value_raw`` by the rulebook's
    # ``global_rules.sign_convention.unsigned_source`` rule — a filing that prints its expenses as
    # unsigned positives. The rulebook asks for the transformation to be recorded on the fact ("set
    # sign_normalised: true on the fact so the transformation is auditable") precisely because it is
    # the one place the engine changes a reported number's sign: ``value_raw`` still holds what the
    # page said, so the two together are the audit trail.
    sign_normalised: bool = False
    reconciled: Decimal | None = None     # after §20 subtraction; always derived from raw
    basis: Basis
    period_end: date | None = None
    period_label: str | None = None
    # Human-readable column header captured from the document (e.g. "31 March 2025"), for
    # DISPLAY only. period_label stays the positional key ("current"/"prior"/…) used for all
    # lookups; this never participates in ValueKey or value matching.
    period_display: str | None = None
    # For a matrix statement (named component columns, not periods): which column this fact was
    # printed in, counting from the left. The column ORDER is part of a statement of changes in
    # equity's meaning — issued capital through to total equity — and it cannot be recovered from
    # ``provenance.bbox`` once the page is sideways, because there the columns advance down the
    # page's y. None for every ordinary period-keyed fact, which has no column axis to record.
    column_index: int | None = None
    # THE PRINTED HEADING OF THE COLUMN this figure stands in — "Leasehold improvements 租賃物業裝修",
    # "2024年12月31日" — as `row_reconstruct.column_headings` read it off the table's header band.
    # DISPLAY ONLY, like ``period_display``: ``period_label`` stays the positional key every reader
    # keys on. It exists so a wide table reaches the model with its columns named.
    column_heading: str | None = None
    # THE PERIOD THE PRINTED HEADER STATES OVER THAT COLUMN — its own heading or a group over it:
    # 期末余额 / 期末公允价值 / "2024" against the table's or the filing's newest year → "current",
    # 期初余额 / 上年 / the year before → "prior" — and None when the header states none, as over
    # a fair-value level or an asset class. Annotation, like ``column_heading``: ``period_label``
    # stays positional, and on a fair-value table its "prior" is the 合计 column, not last year.
    column_period: Literal["current", "prior"] | None = None
    unit_ctx: UnitContext = Field(default_factory=UnitContext)
    provenance: Provenance | None = None
    confidence: ConfidenceVector = Field(default_factory=ConfidenceVector)

    @property
    def key(self) -> ValueKey:
        return ValueKey(basis=self.basis, period_end=self.period_end, period_label=self.period_label)


class LineItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    statement_id: UUID | None = None
    # THE FACE ROW THIS ROW IS PRINTED AS A BREAKDOWN OF — a mainland face's 其中 ("of which") group,
    # whose figure is already inside that row's. Set by `row_reconstruct` from the page geometry,
    # for every line of the breakdown and not only the one carrying the 其中： marker (see
    # `row_reconstruct._breakdown_parent`). Structure, not a verdict: the residual sweep is what
    # decides that such a row must not be added beside its parent.
    parent_id: UUID | None = None

    source_label: str = ""                # text exactly as printed
    display_label: str | None = None      # canonical label (locale-resolved)
    # The section banner this row was printed under ("NON-CURRENT LIABILITIES", 流動負債).
    # A statement prints the same caption under two sections — "Interest-bearing bank and other
    # borrowings" appears once as non-current and once as current — so the caption alone cannot
    # say which concept it is. Mapping uses this to tell them apart.
    section_hint: str | None = None
    # The sub-heading printed above this row WITHIN its section — "Current charge for the year:",
    # "Under-provision in prior years, net:", "Adjustments for:". Distinct from ``section_hint``
    # because it must not scope the row the way a banner does (the rows under "Adjustments for:" are
    # still that section's rows), and kept because some captions have no meaning without it: a tax
    # note itemising an under-provision by geography prints a row whose caption is "Mainland China",
    # and only the sub-heading says what the figure is.
    group_hint: str = ""
    # THE HEADINGS PRINTED OVER SEVERAL COLUMNS of this row's table, in printed order — "Property and
    # equipment", "Right-of-use assets". Not attached to a column: which columns one spans is not
    # something the words alone settle. Display only; see ``ExtractedValue.column_heading``.
    column_groups: list[str] = Field(default_factory=list)
    # EVERY VALUE COLUMN THIS ROW'S TABLE PRINTS, left to right, by heading — the ones no figure
    # stands in included (a blank 第一层次 beside a populated 第三层次). Read from the table's header,
    # and only where its figures were validated against it; [] when no header was read. Display and
    # annotation only; see ``row_reconstruct.read_printed_columns``.
    printed_columns: list[str] = Field(default_factory=list)
    # THIS ROW'S CAPTION IS NOT THE FILING'S OWN. A note prints a block's total on a bare line —
    # the caption is the sub-heading two rows up, and a typesetter does not repeat it — so
    # reconstruction gives the row that heading and says here that it did. Two readers need to know:
    # a reader deciding whether this row's arithmetic may be checked against the rows above it (only
    # a borrowed caption means "I am their total"), and a person looking at a caption that does not
    # appear on the page in that position.
    caption_borrowed: bool = False
    # THE ROWS THIS SUBTOTAL IS THE TOTAL OF, by their ordinal on the page, recorded by the
    # reconstruction that promoted the row. NOT re-derived downstream from `group_hint`: a
    # sub-heading's scope is not closed at the end of its block, so rows printed after it still
    # carry it — and re-deriving pooled those rows into the sum and reported an arithmetically
    # correct note as broken. The builder knows exactly which rows it counted; this is that list.
    component_ordinals: list[int] = Field(default_factory=list)
    canonical_key: str | None = None
    template_node_id: str | None = None
    ordinal: int = 0
    role: LineRole = LineRole.LINE
    # Face or note — set by the reader that produced the row, from the page's own classification,
    # and back-filled for anything synthesised later by the segment stage. None only for a row
    # nothing could attribute, which is reported rather than defaulted: guessing "face" would put a
    # note's money onto the statement.
    printed_in: PrintedIn | None = None

    values: dict[str, ExtractedValue] = Field(default_factory=dict)  # keyed by ValueKey json
    sign_convention: SignConvention = SignConvention.NATURAL
    note_refs: list[NoteRef] = Field(default_factory=list)
    # THE NOTE THIS ROW IS TIED TO — the one it cites, or the one its figure was read from. NOT a
    # statement that the row lives inside a note; the rows printed inside a note are ``NoteItem``s
    # on a ``NotesTable``, never ``LineItem``s, so no value of this field can mean that.
    #
    # The old comment here said "set when this item lives inside a note", and it cost real work
    # before anyone checked it: a reader that trusted it dropped every face row carrying a note
    # reference — which on a filing that prints a note column is nearly all of them — and a
    # four-row balance sheet published one row. The four writers say what it actually holds:
    #
    #   * ``row_reconstruct`` and ``excel_extract`` set it from the note reference PRINTED beside
    #     the row ("Trade receivables … 15"), alongside the same value in ``note_refs``. A citation
    #     by the face, and the common case.
    #   * ``residual._sweep_notes`` sets it on a face row it SYNTHESISES from a note item, naming
    #     where the figure came from. Still a pointer at a note, from the other direction.
    #   * ``map_ontology`` copies the parent's value onto a sole-component row split off it, so the
    #     derived row cites what the row it came from cited.
    #
    # Every reader is consistent with that and none needs the membership reading: ``link_notes`` and
    # ``residual`` fall back to it for a row with no parsed ``note_refs``, ``map_ontology`` and
    # ``prune_notes`` build "notes the face cites" from it (which is what decides that a note is
    # published at all), and the API serves it as the row's note chip.
    #
    # WHETHER A ROW IS FACE OR NOTE IS THE PAGE'S CLASSIFICATION, not this field — ``extract_pdf``
    # reads face and notes pages into one ``line_items`` list, so the page kind is the only thing
    # that separates them (see ``services.buckets``).
    note_number: str | None = None
    reconciliation_role: ReconciliationRole = ReconciliationRole.NONE

    formula: dict | None = None
    # HOW a computed figure was reached, per ``basis:period`` — see services.derivation. A concept
    # assembled out of note datasets rather than read off a caption is not checkable on its figure
    # alone, so where a row has one this carries the rule that produced it and the note lines it
    # consumed, which the statement inspector renders as contributions with click-to-source.
    # OPTIONAL, and empty on most rows: it is set by whatever assembled the figure, not by a stage
    # that fills it on every run.
    derivation: dict | None = None
    is_computed: bool = False
    source: ValueSource = ValueSource.MACHINE
    confidence: ConfidenceVector = Field(default_factory=ConfidenceVector)

    def cited_notes(self) -> list[str]:
        """Every note number this row cites, AS PRINTED, in citation order.

        One definition of "which notes does this row point at", because three stages need it and
        each had written its own: ``link_notes`` (what to link), ``prune_notes`` (what to publish)
        and ``services.buckets`` (which section to file a note under). They disagreed, so a filing
        could have a note published but unlinked, or linked but filed in the wrong section.

        BOTH SPELLINGS OF A SUB-REFERENCE. ``NoteRef`` has a ``subrefs`` field for "16(b)", but
        neither reader uses it — ``row_reconstruct`` and ``excel_extract`` both put the whole
        printed token into ``numbers``, sub-reference and all. Reading only ``subrefs`` therefore
        reads a field that is always empty, so both are read here.

        ``note_number`` is the fallback for a row whose reference was scanned from the note column
        without being parsed into a ``NoteRef`` — the shape ``residual`` gives a face row it
        synthesised out of a note item.
        """
        out: list[str] = []
        for ref in self.note_refs:
            for token in (*ref.numbers, *ref.subrefs):
                token = (token or "").strip()
                if token and token not in out:
                    out.append(token)
        if not out and self.note_number:
            out.append(self.note_number.strip())
        return [t for t in out if t]

    def cited_notes_among(self, available: Container[str]) -> list[str]:
        """The notes this row cites THAT EXIST in ``available``, in citation order.

        A SUB-REFERENCE FALLS BACK TO ITS PARENT NOTE. A filing writes "16(b)" beside a face row and
        prints one note table numbered "16" with (a), (b), (c) inside it — so the citation as
        printed matches no table, and the row was left with no note behind it at all: unlinked,
        its note unpublished, and its section holding nothing that explains the figure.

        The parent is used ONLY when the citation itself names nothing, so a filing that numbers
        the table "16(b)" links to that table exactly, and no row is ever tied to both a sub-note
        and its parent (which would let the reconciliation subtract the same detail twice).

        A BARE CITATION ALSO REACHES A CHAPTER-QUALIFIED NOTE, when exactly one chapter offers
        that number — see :func:`_chapter_match`. A mainland filing's notes are identified by
        chapter and number together ("七、9"), and the face usually prints the same thing; but a
        bilingual filing can carry a chapter heading on its notes pages while its face cites bare
        numbers, and then a citation that names one unambiguous note must not go unlinked.
        """
        out: list[str] = []
        for token in self.cited_notes():
            hit = token if token in available else base_note_number(token)
            if hit is None or hit not in available:
                hit = _chapter_match(token, available)
            if hit and hit in available and hit not in out:
                out.append(hit)
        return out

    def set_value(self, ev: ExtractedValue) -> None:
        self.values[ev.key.model_dump_json()] = ev

    def get_value(self, basis: Basis, period_end: date | None = None,
                  period_label: str | None = None) -> ExtractedValue | None:
        key = ValueKey(basis=basis, period_end=period_end, period_label=period_label)
        return self.values.get(key.model_dump_json())


class NoteItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    notes_table_id: UUID | None = None
    parent_id: UUID | None = None
    raw_label: str = ""
    canonical_key: str | None = None
    values: dict[str, ExtractedValue] = Field(default_factory=dict)
    ordinal: int = 0
    role: LineRole = LineRole.LINE
    # The banner the row was printed under, carried over from the ``LineItem`` reconstruction
    # produced. ``notes_extract`` calls ``build_line_items``, so the section was always computed —
    # and, until this field existed, always discarded at the conversion. That mattered because
    # ``residual._sweep_notes`` synthesises face rows FROM note items, and those rows then reached
    # the sweep with no section, so the first of its three signals was unavailable for every figure
    # sourced from a note.
    section_hint: str | None = None
    # The sub-heading printed above this row inside the note — see ``LineItem.group_hint``. A note
    # is where breakdown-dimension captions live ("Mainland China", "Third parties"), so this is
    # the field that carries their meaning.
    group_hint: str = ""
    # The headings printed over several columns of this row's table — see ``LineItem.column_groups``.
    column_groups: list[str] = Field(default_factory=list)
    # Every value column this row's table section prints, blank ones included, in printed order —
    # see ``LineItem.printed_columns``. [] when no header was read.
    printed_columns: list[str] = Field(default_factory=list)
    # WHICH PERIOD THIS MOVEMENT ROW BELONGS TO — "current" or "prior", and "" when the note says
    # nothing. Set only for a row inside an asset MOVEMENT table, where the period is stated on the
    # BLOCK rather than on the column.
    #
    # WHY A ROW-AXIS PERIOD EXISTS AT ALL. An asset note's columns are asset CLASSES, and its
    # comparative year is a second block of ROWS, so the year is printed once above or below a
    # block and never beside the figure. Nothing else in the model can express that:
    # ``ExtractedValue`` keys a figure by (basis, period), and on these rows the period slot is
    # already spent on the class name ("Hotel property") or on a positional fallback ("col2").
    #
    # THE FAILURE IT FIXES, measured. ``sub__prepaid_lease_depreciation`` published 28,154 as the
    # right-of-use depreciation charge on 2025041600195.pdf. That note prints the charge twice, once
    # per year, and the only column whose label survived as a period was the first — so the line
    # summed one asset class from 2024 with the same class from 2023: 16,847 + 11,307 = 28,154, a
    # number that appears nowhere in the filing. The real charges are 77,707 and 66,870.
    #
    # NOT WRITTEN INTO ``values``. Adding a "current"-keyed figure to these rows would have been the
    # smaller change and it is the wrong one: ``stages.reconcile`` builds a note's total by summing
    # its details, so a row gaining a second figure would inflate that total and could turn an
    # honest "unconfirmed" into a confident false tie. This field and ``total_slot`` are read by
    # ``services.note_sourced.select_rows`` and by nothing else, so no existing consumer moves.
    period_hint: str = ""
    # THE ``period_label`` OF THE ONE VALUE ON THIS ROW THAT TOTALS THE OTHERS, and "" when no value
    # does. Identified by arithmetic, not by reading a header: the total is the figure equal to the
    # sum of the rest of the row, which is script-agnostic and self-checking, where a header match
    # would have to know "Total", "合計", "總計" and every layout that omits the word.
    #
    # Needed because a movement row carries one figure PER ASSET CLASS and the line wants the charge
    # for the whole class of assets. Summing every value on the row would double it — the classes
    # and their total are all present — so the total column is named here rather than guessed at
    # downstream.
    total_slot: str = ""
    # THIS ROW'S CAPTION IS NOT THE FILING'S OWN. A note prints a block's total on a bare line —
    # the caption is the sub-heading two rows up, and a typesetter does not repeat it — so
    # reconstruction gives the row that heading and says here that it did. Two readers need to know:
    # a reader deciding whether this row's arithmetic may be checked against the rows above it (only
    # a borrowed caption means "I am their total"), and a person looking at a caption that does not
    # appear on the page in that position.
    caption_borrowed: bool = False
    # THE ROWS THIS SUBTOTAL IS THE TOTAL OF, by their ordinal on the page, recorded by the
    # reconstruction that promoted the row. NOT re-derived downstream from `group_hint`: a
    # sub-heading's scope is not closed at the end of its block, so rows printed after it still
    # carry it — and re-deriving pooled those rows into the sum and reported an arithmetically
    # correct note as broken. The builder knows exactly which rows it counted; this is that list.
    component_ordinals: list[int] = Field(default_factory=list)
    provenance: Provenance | None = None
    confidence: ConfidenceVector = Field(default_factory=ConfidenceVector)

    def set_value(self, ev: ExtractedValue) -> None:
        self.values[ev.key.model_dump_json()] = ev


class NotesTable(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    note_number: str
    title: str = ""
    #: The top-level CHAPTER heading this note was printed under, title included —
    #: "关联方及关联交易", "合并财务报表项目注释". Empty for a filing that prints no chapters, which is
    #: every English one.
    #:
    #: CARRIED BECAUSE AUTHORED PATTERNS DESCRIBE IT. A mainland filing numbers three levels deep —
    #: the chapter, the note, the table inside it — and only the innermost caption reaches `title`:
    #: chapter 十二 "关联方及关联交易" yields notes titled "、本企业的母公司情况：". So a `note_source`
    #: whose `note_title_any` names the chapter, which is how a human describes where a figure
    #: lives, matched nothing. `notes_extract.read_chapter` has always parsed the title; it was
    #: used to set `basis` from 母公司 and to write a log line, and then dropped.
    chapter_title: str = ""
    basis: Basis | None = None
    source_pages: list[int] = Field(default_factory=list)
    source_text: str = ""
    #: THE NOTE'S NARRATIVE ONLY — its text with the lines it TABULATES removed. `None` means
    #: nothing computed it (a hand-built table, or a route that does not need it), which readers
    #: distinguish from `""`, "computed, and this note is all table".
    #:
    #: WHY IT IS A SEPARATE FIELD. `source_text` is every word of the section joined with spaces,
    #: tables included, and three services want exactly that: `contingent_liabilities` classifies
    #: the whole narrative, `note_context` offers the whole note to a model, and
    #: `note_sourced._amount_in_text` checks a cited amount against anything the note printed.
    #: Only the PROSE ROUTE must not see a table, and on China SCE 1966 it did: the flattened
    #: profit-before-tax note has no sentence punctuation, so `select_prose` read the entire note
    #: as ONE sentence, matched "depreciation … included in … operating expenses" across its
    #: tabulated rows, and took the FIRST grouped amount in it — 17,475,980, the cost of properties
    #: sold — as the operating-expense depreciation charge.
    prose_text: str | None = None
    items: list[NoteItem] = Field(default_factory=list)


class FaceNoteLink(BaseModel):
    """Link between a face line item and the note detail it decomposes into.

    The backbone of Requirement 20 (note→face subtraction reconciliation).
    """

    id: UUID = Field(default_factory=uuid4)
    face_item_id: UUID
    notes_table_id: UUID
    note_number: str
    note_detail_item_ids: list[UUID] = Field(default_factory=list)
    relationship: LinkRelationship = LinkRelationship.ONE_TO_ONE
    coverage: Decimal = Decimal(1)        # fraction of the note total consumed by this link
    link_type: str = "explicit_note_ref"  # explicit_note_ref | inferred | manual
    confidence: float = 1.0
