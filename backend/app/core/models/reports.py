"""Result/report models emitted by the reconcile and validation stages."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from .enums import Severity


class ReconciliationEntry(BaseModel):
    face_item_id: str
    # The face line this entry is about, named by something that SURVIVES a re-run.
    # ``face_item_id`` is a per-run UUID, so a reader that identified the face by it would see
    # every entry change identity on every extraction — and the review queue keys a human
    # judgement on what it reads here. The canonical key (or the printed caption when the line
    # mapped to nothing) is the same face line in run 2 that it was in run 1.
    # Empty on runs stored before this field existed; readers must treat "" as "not named".
    face_key: str = ""
    note_number: str
    basis: str
    period_label: str | None = None
    raw_face: Decimal
    subtracted: Decimal
    reconciled: Decimal
    residual: Decimal
    within_tolerance: bool
    # "tied" | "untied" | "unconfirmed" — see app.services.reconcile. Only "untied" is a
    # discrepancy worth an analyst's time; "unconfirmed" means the note is not a breakdown
    # of this face figure, which is the normal case for an analysis or segment note.
    tie_status: str = "unconfirmed"
    relationship: str


class NoteBlockSubtotalCheck(BaseModel):
    """A subtotal a note printed on a bare line, against the block's own rows.

    This is arithmetic the FILING published, checked against itself, and it is a different claim
    from every other check in the system: a template rollup compares a face subtotal to the
    components the RULEBOOK declares, and a note tie compares a note's total to a face figure.
    This one compares a printed figure to the rows printed above it, inside one note, with no
    template and no mapping involved — so it is the only check that can corroborate a number the
    rulebook has never heard of.

    It matters most where the §20 decomposition reads a note: that split re-derives a block's total
    by SUMMING the block's details, and until this check existed nothing compared that sum to the
    figure the filing itself printed underneath them. Agreement was assumed; now it is verified, and
    a disagreement is served with the run (`note_block_subtotals`, plus a line in
    `failed_assertions`) instead of the computed figure standing in silently. It does not raise a
    review-queue card of its own yet — see ``stages.reconcile._check_block_subtotals``.
    """

    note_number: str
    # The sub-heading whose block this is, as printed ("Current tax:"). Named by the caption rather
    # than by a row id: a row id is a per-run UUID, and a reader comparing two runs needs the same
    # block to be the same block.
    block: str
    basis: str
    period_label: str
    printed: Decimal
    computed: Decimal
    # Kept even though it is exactly `printed - computed`. Every other check in the system reports
    # its own difference (`RuleResult.difference`, `ReconciliationEntry.residual`), and a consumer
    # that had to subtract these two itself would be the one place that re-derives a figure the
    # producer already knows — which is how two screens come to disagree about the same break.
    difference: Decimal
    within_tolerance: bool
    # How many of the block's rows the sum is over. Always at least two — a block with fewer is not
    # recorded at all, because a check that could not run must never read as one that ran and held.
    # Reported anyway, because "printed 1,200 = 600 + 600" and "printed 1,200 = the sum of nine
    # rows" are different amounts of corroboration and the reader cannot see the rows from here.
    component_count: int


class ReconciliationReport(BaseModel):
    entries: list[ReconciliationEntry] = Field(default_factory=list)
    failed_assertions: list[str] = Field(default_factory=list)
    # Kept separate from `entries`, which are note→FACE ties. These never involve a face line, are
    # evaluated whether or not any face line cites the note, and answer a different question.
    note_block_subtotals: list[NoteBlockSubtotalCheck] = Field(default_factory=list)


class RuleResult(BaseModel):
    rule_id: str
    kind: str
    scope_key: str
    status: str                      # pass | fail | skipped | error
    expected: Decimal | None = None
    actual: Decimal | None = None
    difference: Decimal | None = None
    details: dict = Field(default_factory=dict)


class StructuralReport(BaseModel):
    """Template-structure validation (rollups + declared identities) as ``RuleResult`` rows.

    Both outcomes are kept: a relation actually checked (``pass``/``fail``) and one that could
    not be checked because a participant was never extracted (``skipped``, with the reason).
    Keeping the skips is the honest half — it says how much of the structure a run could
    verify, so a nearly-unverified extraction can't read as a clean one.
    """

    results: list[RuleResult] = Field(default_factory=list)
    failed_assertions: list[str] = Field(default_factory=list)

    def evaluated(self) -> list[RuleResult]:
        return [r for r in self.results if r.status in ("pass", "fail")]

    def failures(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == "fail"]

    def skipped(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == "skipped"]


class ReviewItemModel(BaseModel):
    rule_id: str
    category: str
    severity: Severity
    target_type: str
    target_id: str
    expected: Decimal | None = None
    actual: Decimal | None = None
    difference: Decimal | None = None
    message: str
    scope_key: str
