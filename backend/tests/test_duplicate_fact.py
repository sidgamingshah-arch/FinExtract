"""One economic fact printed twice is one fact, not two amounts to add.

The rulebook says so — ``global_rules.duplicate_fact_rule``: "The same economic fact on the face
and in a note is one fact with multiple evidence references, not two additive values. This also
applies to two face captions that report the same amount." It was declared and then only appended
to the LLM's system prompt (``mapping.py``), so a run with no LLM reachable — which is every run in
an air-gapped deployment, and was the run that surfaced this — summed the duplicates.

The enforcement here is deliberately NOT gated on the rulebook declaring the field. Not
double-counting one fact is arithmetic hygiene, not a policy a rulebook may switch off: a filing
that wanted the two amounts added would be a filing whose bottom line is twice its own bottom line.
"""
from __future__ import annotations

from app.services.periods import caption_key, concept_value, summable


def _row(label: str, value: str, page: int | None, *, period: str = "current",
         edited: bool = False) -> dict:
    val: dict = {"basis": "consolidated", "period_label": period, "value": value}
    if page is not None:
        val["provenance"] = {"page_index": page}
    row: dict = {"source_label": label, "canonical_key": "k", "values": [val]}
    if edited:
        # The shape `periods.edited_for` actually reads — a row-level slot list, not a marker on
        # the value. Writing it the other way made the edit test pass through the dedup instead.
        row["edited_slots"] = [f"consolidated/{period}"]
    return row


def _total(group: list[dict]) -> float | None:
    return concept_value(group, "consolidated", "current")


# --- the two cases a real filing produced -------------------------------------------------------

def test_a_bottom_line_restated_on_the_next_statement_is_not_doubled():
    """FROM A REAL RUN. The income statement (p.185) ends "LOSS FOR THE YEAR (3,237,857)" and the
    statement of comprehensive income (p.186) opens by restating it. Both map to
    ``profit_for_the_year``, and the spread reported -6,475,714 — a filing's headline loss, doubled."""
    group = [_row("LOSS FOR THE YEAR", "-3237857", 185),
             _row("LOSS FOR THE YEAR", "-3237857", 186)]
    assert _total(group) == -3237857.0


def test_the_same_note_figure_cited_from_two_notes_is_counted_once():
    """FROM THE SAME RUN. Lease interest of 40,170 is printed in note 8 (finance costs, p.248) and
    again in note 16(b) (leases, p.268), and the two captions carry DIFFERENT cited-note runs —
    "…16(b),(c)" and "…8, 16(b)" — so folding case and punctuation alone does not make them equal.
    The unrelated put-option line on the same page still adds."""
    group = [_row("Interest on lease liabilities 16(b),(c)", "40170", 248),
             _row("Interest on put option liabilities", "4466", 248),
             _row("Interest on lease liabilities 8, 16(b)", "40170", 268)]
    assert _total(group) == 44636.0


# --- and the three cases that must keep adding --------------------------------------------------

def test_several_printed_lines_of_one_concept_still_add():
    """The reason the sum is the default. Four dividend captions map to "Dividends received", and
    a rule that collapsed them would under-report the figure by 750,759."""
    group = [_row("Dividend received from an associate", "3933", 193),
             _row("Dividends received from joint ventures", "743260", 193),
             _row("Dividends received from financial assets at FVOCI", "200", 193),
             _row("Dividends received from financial assets at FVPL", "7299", 193)]
    assert _total(group) == 754692.0


def test_one_statement_printing_the_same_caption_twice_printed_it_twice():
    """A different location is required, and this is why. Two rows with the same caption and the
    same amount on ONE page are two lines the filing chose to print — a schedule listing the same
    fee for two entities, say — and those add. Collapsing them would silently halve the figure."""
    group = [_row("Management fee", "100", 12), _row("Management fee", "100", 12)]
    assert _total(group) == 200.0


def test_the_same_caption_with_different_amounts_is_two_facts():
    """The amount is required. "Bank borrowings" appears twice on a balance sheet — the current
    portion and the non-current one — and they are two facts that add to the concept."""
    group = [_row("Bank borrowings", "10886034", 188), _row("Bank borrowings", "10437690", 188)]
    assert _total(group) == 21323724.0


def test_two_unrelated_captions_with_equal_amounts_are_two_facts():
    """The caption is required. Equal amounts collide by coincidence, small ones especially, and a
    rule keyed on the amount alone would drop a real line."""
    group = [_row("Repairs and maintenance", "119", 12), _row("Bank charges", "119", 12)]
    assert _total(group) == 238.0


# --- the invariants the rest of the app depends on ----------------------------------------------

def test_an_analysts_edit_still_replaces_the_whole_figure():
    """``concept_value``'s existing contract, unchanged: a manual value is the answer for the line,
    not one more contributor. Checked here because the dedup runs in the same function."""
    # The analyst's figure is DIFFERENT from the printed one, so the assertion separates
    # "the edit won" from "the dedup happened to give the same answer".
    group = [_row("LOSS FOR THE YEAR", "-3237857", 185),
             _row("LOSS FOR THE YEAR", "-5000000", 186, edited=True)]
    assert _total(group) == -5000000.0


def test_the_lines_shown_add_up_to_the_figure_shown():
    """THE HONESTY INVARIANT, and the reason ``summable`` returns rows rather than just a total.
    The inspector lists every contributing caption and the workbook prints the same list; if the
    total quietly left one out, the column would not sum to its own total and the reader would be
    left guessing which line was dropped."""
    group = [_row("LOSS FOR THE YEAR", "-3237857", 185),
             _row("LOSS FOR THE YEAR", "-3237857", 186),
             _row("Exchange realignments", "243212", 186)]
    counted = summable(group, "consolidated", "current")
    assert sum(n for _, n in counted) == concept_value(group, "consolidated", "current")
    assert [r["source_label"] for r, _ in counted] == ["LOSS FOR THE YEAR", "Exchange realignments"]


def test_a_caption_that_is_only_a_note_reference_keeps_its_own_identity():
    """``caption_key`` strips a trailing reference run, and a caption made of nothing else would
    collapse to the empty string — comparing equal to every other stripped-to-nothing caption."""
    assert caption_key({"source_label": "16(b)"}) == "16 b"
    assert caption_key({"source_label": "Interest on lease liabilities 16(b),(c)"}) == (
        "interest on lease liabilities")
    # A real trailing word is not a reference.
    assert caption_key({"source_label": "Loss for the year"}) == "loss for the year"


def test_the_workbook_says_which_line_it_did_not_add():
    """The Excel note lists the same captions, and a workbook has nothing to click: the line the
    total skipped has to say so on the sheet."""
    from app.services.export import _contribution_note

    group = [_row("LOSS FOR THE YEAR", "-3237857", 185),
             _row("LOSS FOR THE YEAR", "-3237857", 186)]
    note = _contribution_note(group, "consolidated", "current")
    assert note is not None
    assert note.count("same fact printed above — not added") == 1
    assert "Total = -3,237,857" in note
