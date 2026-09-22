"""A SHORT CAPTION MAY NOT ANSWER A LONG CITATION, AND A PROSE AMOUNT IS THE NOTE'S WORD NOT THE MODEL'S.

NEW FILE -> backend/tests/test_a_citation_has_to_be_specific.py

Two holes in `note_sourced.resolve_sources`, both measured on 2025041600195 before this.

ONE — CONTAINMENT WAS UNBOUNDED IN THE DANGEROUS DIRECTION. The rule is "forgiving on punctuation
and strict on words: containment either way", which is right for a model that drops a footnote
marker or a comma. But a short caption is contained in almost anything: a citation of
`nowhere in this note` — a phrase naming no row at all — RESOLVED, to a figure of 355, because the
note has rows captioned `Note`, `In` and `No`, each a substring of `nowhereinthisnote`. The corpus
carries 1,744 captions of three normalised characters or fewer, so each one is a wildcard able to
answer a citation meant for another row.

TWO — THE PROSE WITNESS INCLUDED THE MODEL'S OWN QUOTE. The contract lets the model state a figure
in one place only, an amount printed in prose, and `_amount_in_text`'s docstring promises "the
number must be demonstrably printed". The witness was `source_text + " " + quote`, so a figure that
appeared nowhere in the filing was accepted whenever the sentence the model typed around it
contained the digits. That is self-certification.

AND FIXING TWO EXPOSED A THIRD: the witness was one fragment. A note arrives in fragments — note 51
arrives as eight, pages 170-177 — and only the eighth states 1,885,020, so asking the first whether
the note states it answered no about a note that does. The quote had been masking that.

WHY REFUSAL IS THE RIGHT ANSWER rather than a lower rank: `resolve_sources`' own contract is that
"a citation that resolves to nothing is returned as unresolved, not dropped and not believed", and
the caller keeps the mapping and flags it. A match onto the wrong row skips that entirely and
publishes a figure whose citation a reviewer has no reason to doubt.
"""
from __future__ import annotations

import pytest

from app.core.models.line_item import NoteItem, NotesTable
from app.services.mapping import SourceRef
from app.services.note_sourced import _caption_matches, _notes_by_number, resolve_sources


@pytest.mark.parametrize(
    "citation, printed, matches, why",
    [
        # EXACT EQUALITY ALWAYS PASSES, whatever the length — this is what a character floor would
        # have broken, and a filing prints both of these as whole captions.
        ("合计", "合计", True, "an exact match on a two-character caption is the commonest citation"),
        ("at", "at", True, "likewise in Latin script"),
        ("total", "total", True, "and the English total"),
        # FORGIVING ON PUNCTUATION, which is what containment is for.
        ("depreciationofpropertyplantandequipment",
         "depreciationofpropertyplantandequipment", True, "a dropped footnote marker"),
        ("deferredtaxnote12", "deferredtaxnote", True, "a caption whose closing paren was lost"),
        ("tradereceivables", "tradereceivablesnote27", True, "a dropped parenthetical"),
        # AND STRICT WHERE IT WAS NOT. Both of these resolved before.
        ("at31december2024", "at", False, "the measured defect: a bare At answering a full date"),
        ("nowhereinthisnote", "note", False, "a phrase naming no row, answered by a Note row"),
        ("total", "totalequity", False, "a fragment standing in for a different total"),
    ],
)
def test_the_specificity_floor(citation, printed, matches, why):
    assert _caption_matches(citation, printed) is matches, why


def test_an_empty_side_never_matches():
    """A citation with no caption goes down the prose route; it must not match every row first."""
    assert _caption_matches("", "anything") is False
    assert _caption_matches("anything", "") is False


def _fragment(number: str, text: str, captions=()) -> NotesTable:
    table = NotesTable(note_number=number, title="FINANCIAL INSTRUMENTS",
                       source_text=text)
    for cap in captions:
        table.items.append(NoteItem(raw_label=cap, note_number=number))
    return table


def test_every_fragment_of_a_note_is_the_witness():
    """The note states the figure; which of its eight tables states it is not the model's problem."""
    frags = [_fragment("51", "nothing here", ["Note"]),
             _fragment("51", "At 31 December 2024 ... 1,885,020")]
    assert len(_notes_by_number(frags, "51")) == 2
    resolved, unresolved = resolve_sources(
        [SourceRef(note="51", caption="", amount="1885020",
                   quote="At 31 December 2024 ... 1,885,020")],
        frags, None, allow_face=False)
    assert not unresolved, unresolved
    assert resolved and resolved[0]["figures"] == {"prose": "1885020"}


def test_a_figure_the_model_invented_is_refused_however_it_quotes_itself():
    """THE SAFETY PROPERTY, and the one the quote in the witness used to defeat.

    The quote is the model's own text. An amount that is nowhere in the note must be refused even
    when the sentence around it reads perfectly.
    """
    frags = [_fragment("51", "At 31 December 2024 ... 1,885,020")]
    resolved, unresolved = resolve_sources(
        [SourceRef(note="51", caption="", amount="9999999",
                   quote="The Group holds 9,999,999 of Level 3 assets")],
        frags, None, allow_face=False)
    assert not resolved, resolved
    assert unresolved and "does not appear" in unresolved[0]["why"]


def test_a_real_figure_is_accepted_even_when_the_quote_carries_no_digits():
    """The other direction of the same rule: the NOTE is the witness, so the quote's wording is
    free. A model that names the sentence by its subject rather than transcribing it is not
    fabricating anything."""
    frags = [_fragment("51", "At 31 December 2024 ... 1,885,020")]
    resolved, unresolved = resolve_sources(
        [SourceRef(note="51", caption="", amount="1885020",
                   quote="the Level 3 reconciliation's closing balance")],
        frags, None, allow_face=False)
    assert not unresolved, unresolved
    assert resolved and resolved[0]["figures"] == {"prose": "1885020"}
