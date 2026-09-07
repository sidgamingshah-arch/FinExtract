"""Line-item definitions — the configuration surface behind the Line Items screen.

WHY THIS EXISTS. Eight concepts reach the output template, and each is assembled from parts that
until now lived only as Python. `services.deprec_impairment` alone reads THIRTEEN note-level
datasets and resolves them through a five-rung cascade; the template declares those eight lines
with ZERO children, so nothing outside that module knew the parts existed, let alone let anyone
change them. Widening one caption meant editing a 162-alternative regex and shipping a release.

A definition here says four things, and the TYPE decides which of them apply:

  extracted     read off the report. Where to look (ordered), which side of the balance sheet it
                is, and how to recognise the caption — aliases, one pattern, exclusions.
  calculated    a signed sum over other line items, fixed numbers, and absolute values.
  intermediate  the same arithmetic, but never published — an input to something else.
  derived       an ordered cascade; the first rung that resolves wins.

FLAT, NOT NESTED, and keyed by `parent`. A sub-line item is a line item, so nesting them would
mean two shapes for one thing and a different editor at each depth. Flat also survives the XLSX
round-trip the rulebook already has, which is what lets someone configure this without a
deployment.

ABSENT IS NOT EMPTY. A concept with no definition here behaves exactly as it does today — the
rulebook maps it and the five derivation services compute it. That is what makes this adoptable
one line at a time instead of as a migration.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

LineItemType = Literal["extracted", "calculated", "intermediate", "derived"]

# Where a caption may be read from, in the order they are searched. `notes` leads by default:
# a note states the figure the face only summarises, and for these eight the note IS the
# authoritative source — which is why the derivation services read notes and not the face.
SearchScope = Literal["notes", "income_statement", "balance_sheet", "cash_flow",
                      "changes_in_equity", "front_matter"]
DEFAULT_SCOPES: tuple[SearchScope, ...] = ("notes",)

# `from_section` resolves through the section banner the caption sits under. It is the right
# answer for a balance-sheet line printed INSIDE a section, and no answer at all for a statement
# total, which sits under none — those must say the side outright.
Side = Literal["from_section", "asset", "liability", "equity", "none"]

SECTION_SIDE: dict[str, Side] = {
    "current_assets": "asset", "non_current_assets": "asset",
    "current_liabilities": "liability", "non_current_liabilities": "liability",
    "equity": "equity",
}


class Term(BaseModel):
    """One addend of a formula: a line item, a fixed number, or the absolute value of a line item.

    `abs` is separate from `sign` on purpose. A filing prints accumulated depreciation as
    -1,842,330 or in brackets; `abs` says "take the magnitude", `sign` says "and subtract it".
    Collapsing them would make one of the two impossible to express.
    """

    ref: str = ""                      # another line item's key
    const: float | None = None         # …or a fixed number instead
    sign: Literal[1, -1] = 1
    abs: bool = False
    # WHAT THIS TERM'S ABSENCE MEANS. Three roles, because the shipped cascades need exactly
    # three and a boolean could only say two of them:
    #
    #   required    absent -> the whole rung fails. "Revenue minus cost of sales" is not
    #               revenue when the filing prints no cost of sales.
    #   any_of      at least one term of this role must be present for the rung to resolve, and
    #               those that are get summed. "The four operating-expense notes" is still a sum
    #               when a filing discloses three of them.
    #   adjustment  contributes when present, and NEVER justifies the rung on its own.
    #
    # The third is the one a boolean could not express, and its absence was a real defect: with
    # every term merely "optional", a rung whose only present figure was the cost-of-sales
    # DEDUCTION resolved to a negative depreciation charge — a figure assembled out of a
    # deduction with nothing to deduct it from. Caught by a test rather than by a filing, which
    # is the only reason it never reached an output.
    role: Literal["required", "any_of", "adjustment"] = "required"

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.ref) == (self.const is not None):
            raise ValueError("a term needs exactly one of `ref` or `const`")
        if self.const is not None and self.abs:
            raise ValueError("`abs` applies to a referenced line item, not to a fixed number")
        return self


class CascadeRung(BaseModel):
    """One attempt in a derived line's priority order.

    `terms` rather than a string expression: the rungs of the shipped cascades are all signed
    sums over named datasets (`pbt_depreciation - cos_depreciation`), and an expression language
    would need a parser, a precedence table and its own error reporting to say the same thing.
    """

    id: str                            # "P1", "Find_2" — as the run log and the audit trail name it
    terms: list[Term] = Field(default_factory=list)
    note: str = ""                     # why this rung exists, for the person reading the screen


class NoteSource(BaseModel):
    """Which note a sub-line item is read from, and which of its rows count.

    This is what the closed enumerations become. `note_title_any` replaces the hard-coded
    `_NOTE_HEADINGS` pattern per dataset, and `row_caption_any` replaces `_QUALIFYING_RE` — the
    162-alternative whitelist that refused a filing writing "Depreciation charge for the year".
    Both take patterns, so widening one is an edit here rather than a release.
    """

    note_title_any: list[str] = Field(default_factory=list)
    row_caption_any: list[str] = Field(default_factory=list)
    row_caption_none: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _patterns_compile(self):
        _refuse_uncompilable(
            ("note_title_any", self.note_title_any),
            ("row_caption_any", self.row_caption_any),
            ("row_caption_none", self.row_caption_none),
        )
        return self


def _refuse_uncompilable(*groups: tuple[str, list[str]]) -> None:
    """Every configured pattern must be a valid regex, checked where it is written.

    Not defensive padding. Splitting a shipped 34-alternative regex on ``|`` to seed this config
    tore ``^\\s*at\\s+(?:1|31)`` into ``^\\s*at\\s+(?:1`` and ``31)`` — two fragments that compile
    nowhere and match nothing. Read at extraction time that is a silent hole: the exclusion simply
    stops excluding, and the wrong rows get summed into a figure nobody can trace back. Refusing it
    here means a bad pattern is a load error on a screen that names it, which is the entire reason
    this configuration layer exists.
    """
    bad = []
    for field, values in groups:
        for i, raw in enumerate(values):
            try:
                re.compile(raw)
            except re.error as exc:
                bad.append(f"{field}[{i}] {raw!r}: {exc}")
    if bad:
        raise ValueError("pattern does not compile — " + "; ".join(bad))


class LineItemDef(BaseModel):
    """One configured line item."""

    key: str
    label: str = ""
    type: LineItemType = "extracted"
    description: str = ""
    # Whether it reaches the statement screens and the export. An intermediate never does; the
    # validator below enforces that rather than trusting whoever edits the file.
    in_output: bool = True
    # The line this one is a part of. Empty for a top-level output line.
    parent: str = ""
    # Sub-line items in the order they should be shown under their parent.
    order: int = 0

    # ── extracted ────────────────────────────────────────────────────────────────────────────
    scopes: list[SearchScope] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    side: Side = "none"
    # Whether a caption printed on the opposite side may fill this line. Off by default: a bare
    # "Cash" caption once resolved to an overdraft because nothing forbade it. On only for an
    # instrument that genuinely appears on both sides — a derivative, a swap, an option — where
    # one note table lists both and the row's own column is the only thing that separates them.
    allow_contra: bool = False
    aliases: list[str] = Field(default_factory=list)
    pattern: str = ""
    exclude: list[str] = Field(default_factory=list)
    note_source: NoteSource | None = None

    # ── calculated / intermediate ────────────────────────────────────────────────────────────
    terms: list[Term] = Field(default_factory=list)

    # ── derived ──────────────────────────────────────────────────────────────────────────────
    cascade: list[CascadeRung] = Field(default_factory=list)
    # The service that implements this cascade today, when one does. Present so a definition can
    # describe an existing derivation before anything is rewired to read it — the config and the
    # code can be compared before either is trusted.
    implemented_by: str = ""

    @model_validator(mode="after")
    def _coherent(self):
        if self.type == "intermediate":
            self.in_output = False
        if self.type in ("calculated", "intermediate") and not self.terms:
            raise ValueError(f"{self.key}: a {self.type} line needs at least one term")
        if self.type == "derived" and not (self.cascade or self.implemented_by):
            raise ValueError(f"{self.key}: a derived line needs a cascade or `implemented_by`")
        if self.side == "from_section" and "balance_sheet" not in self.scopes:
            raise ValueError(f"{self.key}: `from_section` needs the balance sheet in `scopes` — "
                             "there is no section banner anywhere else to read it from")
        _refuse_uncompilable(("pattern", [self.pattern] if self.pattern else []),
                             ("exclude", self.exclude))
        return self

    def resolved_side(self, section: str | None) -> Side:
        """The side in force, given the section a caption was actually found under.

        Returns "none" for `from_section` where the section says nothing — a statement total sits
        under no banner — so the caller can report an unanswered side instead of guessing one.
        """
        if self.side != "from_section":
            return self.side
        return SECTION_SIDE.get((section or "").strip().lower(), "none")
