"""WHICH FIGURES ARE IN REVIEW, AND WHY — the one predicate (`services.review_state`).

A review flag sits on the thing that is in doubt. A doubt about one figure lives on that figure's
slot (line, basis, period); a doubt about the caption lives on the row and means every slot. The
measured defect this answers: on 300319 the prior-year current-assets reconciliation differs by
565,202.29 while the current year is 0, and the CURRENT figures of trade receivables gross,
prepayments and total current assets went to review too, because the flag was on the row.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import review_state as RS

REVIEW = RS.REVIEW_FLAG


# ── the object side: what the cascades ask ─────────────────────────────────────────────────────

def _ev(period: str, value="100", flags=(), mapping=1.0, basis=Basis.CONSOLIDATED):
    ev = ExtractedValue(basis=basis, period_label=period,
                        value=None if value is None else Decimal(value),
                        value_raw=None if value is None else Decimal(value))
    ev.confidence.flags.extend(flags)
    ev.confidence.mapping = mapping
    return ev


def _li(*values, flags=(), method="alias", mapping=0.95) -> LineItem:
    row = LineItem(source_label="x", canonical_key="bs_ca__prepayments_cp")
    for ev in values:
        row.set_value(ev)
    row.confidence.flags.extend(flags)
    row.confidence.method = method
    row.confidence.mapping = mapping
    return row


def test_a_slot_flag_puts_that_slot_in_review_and_not_its_sibling():
    row = _li(_ev("current"), _ev("prior", flags=[REVIEW]))
    assert RS.in_review(row, "consolidated", "prior")
    assert not RS.in_review(row, "consolidated", "current")


def test_a_part_in_review_on_the_slot_is_review():
    row = _li(_ev("current", flags=["part_in_review:bs_ca__x"]), _ev("prior"))
    assert RS.in_review(row, "consolidated", "current")
    assert not RS.in_review(row, "consolidated", "prior")


def test_a_row_flag_on_a_rules_row_is_the_captions_doubt_and_means_every_slot():
    row = _li(_ev("current"), _ev("prior"), flags=[REVIEW])
    assert RS.in_review(row, "consolidated", "current")
    assert RS.in_review(row, "consolidated", "prior")


def test_a_row_flag_on_a_model_row_is_not_read_as_every_slot():
    """A whole-PDF model row has no caption: an old stored run wrote its row flag for ONE slot."""
    row = _li(_ev("current", flags=["whole_pdf", REVIEW], mapping=0.3),
              _ev("prior", flags=["whole_pdf"], mapping=0.9),
              flags=["whole_pdf", REVIEW], method="llm", mapping=0.3)
    assert RS.in_review(row, "consolidated", "current")
    assert not RS.in_review(row, "consolidated", "prior")


def test_the_cascades_ask_the_flags_only_never_a_score():
    """A weak caption score (a 0.75 split row) is shown in review, but it is not carried up to a
    computed line: `in_review` is flags only."""
    row = _li(_ev("current"), mapping=0.75)
    assert not RS.in_review(row, "consolidated", "current")
    assert RS.reasons((), 1.0, (), 0.75, "alias") == (RS.CAPTION_SCORE,)


def test_a_slot_with_no_figure_is_never_in_review():
    row = _li(_ev("current", value=None, flags=[REVIEW]), flags=[REVIEW])
    assert not RS.in_review(row, "consolidated", "current")
    assert not RS.in_review(row, "consolidated", "prior")          # no slot at all


# ── the reasons, one slot at a time ────────────────────────────────────────────────────────────

def test_the_reasons_for_one_slot():
    t = 0.80
    # rule 1: the slot's own flag
    assert RS.reasons([REVIEW], 1.0, [], 0.95, "alias", threshold=t) == (RS.FIGURE,)
    # rule 2: a rules row's flag — the caption
    assert RS.reasons([], 1.0, [REVIEW], 0.95, "alias", threshold=t) == (RS.CAPTION,)
    # … and a model row's is not
    assert RS.reasons(["whole_pdf"], 0.9, ["whole_pdf", REVIEW], 0.9, "llm", threshold=t) == ()
    # rule 3: a rules caption score below the threshold, for a slot with no score of its own
    assert RS.reasons([], 0.75, [], 0.75, "alias", threshold=t) == (RS.CAPTION_SCORE,)
    # rule 4: an own-scored slot by its own score — the row's (here the best) score is not asked
    assert RS.reasons(["whole_pdf"], 0.3, ["whole_pdf"], 0.9, "llm", threshold=t) == (
        RS.FIGURE_SCORE,)
    assert RS.reasons(["whole_pdf"], 0.9, ["whole_pdf"], 0.3, "llm", threshold=t) == ()
    # a fill slot ("whole_pdf:rules_reading") carries no score of its own: its row's is asked
    assert RS.reasons(["whole_pdf:rules_reading"], 0.9, ["whole_pdf:rules_reading"], 0.6,
                      "alias", threshold=t) == (RS.CAPTION_SCORE,)


def test_an_edit_answers_the_figures_doubt_and_not_the_captions():
    assert RS.reasons([REVIEW], 1.0, [], 0.95, "alias", edited=True) == ()
    assert RS.reasons(["whole_pdf"], 0.3, ["whole_pdf"], 0.9, "llm", edited=True) == ()
    assert RS.reasons([REVIEW], 1.0, [REVIEW], 0.95, "alias", edited=True) == (RS.CAPTION,)
    assert RS.reasons([], 0.6, [], 0.6, "alias", edited=True) == (RS.CAPTION_SCORE,)


def test_the_reason_flags_are_the_doubts_not_the_breadcrumbs():
    flags = ["whole_pdf", "whole_pdf:on_page", "whole_pdf:not_found", REVIEW,
             "whole_pdf:rules_reading_in_a_section_that_differs:bs_ca:consolidated:prior:565202.29",
             "part_in_review:bs_ca__x", "sign_rule:expense_negative"]
    assert RS.reason_flags(flags) == [
        "whole_pdf:not_found",
        "whole_pdf:rules_reading_in_a_section_that_differs:bs_ca:consolidated:prior:565202.29",
        "part_in_review:bs_ca__x"]


# ── the dict side: a served row ────────────────────────────────────────────────────────────────

def _v(period, value="100", flags=(), mapping=1.0, basis="consolidated"):
    return {"basis": basis, "period_label": period, "value": value,
            "confidence": {"mapping": mapping, "flags": list(flags)}}


def _row(*values, flags=(), method="alias", mapping=0.95, **extra):
    return {"canonical_key": "bs_ca__prepayments_cp", "source_label": "预付款项",
            "mapping_method": method, "mapping_confidence": mapping, "flags": list(flags),
            "values": list(values), **extra}


def test_a_served_row_names_the_slots_in_review():
    model = _row(_v("current", flags=["whole_pdf", "whole_pdf:not_found", REVIEW], mapping=0.3),
                 _v("prior", flags=["whole_pdf", "whole_pdf:on_page"], mapping=0.9),
                 flags=["whole_pdf"], method="llm", mapping=0.9)
    assert RS.row_review(model, 0.8) == {"row": False, "slots": ["consolidated/current"]}
    assert RS.line_weak(model, 0.8)

    caption = _row(_v("current"), _v("prior"), flags=[REVIEW])
    assert RS.row_review(caption, 0.8) == {
        "row": True, "slots": ["consolidated/current", "consolidated/prior"]}

    weak_score = _row(_v("current", mapping=0.75), mapping=0.75)
    assert RS.row_review(weak_score, 0.8) == {"row": True, "slots": ["consolidated/current"]}

    clean = _row(_v("current"), _v("prior"))
    assert RS.row_review(clean, 0.8) == {"row": False, "slots": []}
    assert not RS.line_weak(clean, 0.8)


def test_an_old_payloads_model_row_reads_per_slot():
    """Stored before this change: the bare row flag, and the row score the minimum of the slots'.
    Read through the model-row exemption and the slots' own scores, it is per slot."""
    old = _row(_v("current", flags=["whole_pdf", "whole_pdf:not_found", REVIEW], mapping=0.3),
               _v("prior", flags=["whole_pdf", "whole_pdf:on_page"], mapping=0.9),
               flags=["whole_pdf", REVIEW], method="llm", mapping=0.3)
    assert RS.row_review(old, 0.8) == {"row": False, "slots": ["consolidated/current"]}


def test_a_blank_line_with_a_row_doubt_is_weak_with_no_slot_named():
    """The supplemental not-determined row (`api.routes.extractions._supp_row`): no figure, a row
    flag. Listed as a line, with zero figures."""
    blank = _row(flags=["supplemental:template_declared", "not_determined", REVIEW],
                 method="supplemental", mapping=0.0)
    assert RS.row_review(blank, 0.8) == {"row": True, "slots": []}
    assert RS.line_weak(blank, 0.8)
    # An empty model row (every figure refused) scores 0.0 and has nothing scored of its own.
    refused = _row(flags=["whole_pdf", "whole_pdf:refused:column:consolidated:current:5"],
                   method="llm", mapping=0.0)
    assert RS.line_weak(refused, 0.8)


def test_an_edited_slot_leaves_review_and_a_caption_doubt_does_not():
    machine = [_v("current", "100", flags=["whole_pdf", REVIEW], mapping=0.3),
               _v("prior", "90", flags=["whole_pdf"], mapping=0.9)]
    model = _row(_v("current", "101", flags=["whole_pdf", REVIEW], mapping=0.3),
                 _v("prior", "90", flags=["whole_pdf"], mapping=0.9),
                 flags=["whole_pdf"], method="llm", mapping=0.9,
                 edited=True, edited_slots=["consolidated/current"], _original=machine)
    assert RS.row_review(model, 0.8)["slots"] == []
    assert not RS.line_weak(model, 0.8)
    caption = _row(_v("current", "41"), flags=[REVIEW], edited=True,
                   edited_slots=["consolidated/current"], _original=[_v("current", "40")])
    assert RS.row_review(caption, 0.8)["slots"] == ["consolidated/current"]
    # A run edited before slots were recorded: the row-level edit meant every slot. Its snapshot
    # was keyed by period label.
    legacy = _row(_v("current", "101", flags=[REVIEW]), edited=True, _original={"current": "100"})
    assert RS.row_review(legacy, 0.8)["slots"] == []


def test_a_save_that_keeps_the_machines_figure_answers_nothing():
    """The Workspace's Save sends the figure on show with the note: a note-only Save, or Save
    pressed with nothing retyped, records the slot in `edited_slots` but leaves the figure as the
    machine read it — its doubt stands."""
    machine = [_v("current", "100", flags=["whole_pdf", "whole_pdf:not_found", REVIEW],
                  mapping=0.3)]

    def edited(value, **extra):
        return _row(_v("current", value, flags=["whole_pdf", "whole_pdf:not_found", REVIEW],
                       mapping=0.3), flags=["whole_pdf"], method="llm", mapping=0.3,
                    edited=True, edited_slots=["consolidated/current"], _original=machine,
                    **extra)

    for same in ("100", "100.0", "100.00"):
        assert RS.row_review(edited(same), 0.8)["slots"] == ["consolidated/current"], same
    assert RS.row_review(edited("101"), 0.8)["slots"] == []       # a changed figure answers it
    # A formula of the analyst's drives the figure: answered, even where it gives the same figure.
    assert RS.row_review(edited("100", formulas={"consolidated/current": "=A+B"}),
                         0.8)["slots"] == []
    # No snapshot to compare with: nothing says the figure changed, so it stays in review.
    no_snapshot = edited("101")
    no_snapshot.pop("_original")
    assert RS.row_review(no_snapshot, 0.8)["slots"] == ["consolidated/current"]
    # A slot the edit made (the machine had none there) carries no machine doubt to answer.
    made = _row(_v("prior", "5", flags=[REVIEW]), edited=True,
                edited_slots=["consolidated/prior"], _original=[])
    assert RS.row_review(made, 0.8)["slots"] == []


def test_annotate_adds_the_review_state_and_writes_nothing_back():
    stored = [_row(_v("current"), _v("prior", flags=[REVIEW, "whole_pdf:rules_gap_unchecked:"
                                                     "cf_financing:consolidated:prior"])),
              {"canonical_key": None, "source_label": "unmapped", "values": []}]
    before = __import__("copy").deepcopy(stored)
    out = RS.annotate(stored, 0.8)
    assert stored == before
    row = out[0]
    assert row["review_slots"] == ["consolidated/prior"] and row["review_row"] is False
    cur, prior = row["values"]
    assert cur["in_review"] is False and cur["review_reasons"] == []
    assert prior["in_review"] is True
    assert prior["review_reasons"] == [
        RS.FIGURE, "whole_pdf:rules_gap_unchecked:cf_financing:consolidated:prior"]
    assert row["flags"] == stored[0]["flags"]                   # served as stored
    assert out[1]["review_slots"] == [] and out[1]["review_row"] is False
