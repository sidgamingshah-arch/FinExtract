"""A model that cites a SENTENCE as if it were a row — no `amount` given — is now read off the note.

Measured on 嘉民 (kaming): the model found the covenant in note 29 and cited "– consolidated
tangible net worth shall not be less than $1,…" as a caption. No extracted row matches a sentence,
no amount was given, and the citation was refused although the filing states the figure.

The amount is always taken from the NOTE'S text: from the cited words when they carry one that the
note prints, or from the rest of the sentence the cited words open. One amount or none — a
sentence stating several is refused and says so.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models import NotesTable
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

COVENANT = ("The Group's banking facilities are subject to the following financial covenants: "
            "– consolidated tangible net worth shall not be less than $1,100,000 at all times; "
            "– gearing shall not exceed 70%.")


def _note(text=COVENANT):
    return NotesTable(note_number="29", title="BANK LOANS", source_text=text)


def _one(ref, text=COVENANT):
    resolved, unresolved = resolve_sources([ref], [_note(text)])[:2]
    return resolved, unresolved


def test_the_sentence_a_caption_opens_gives_its_one_amount():
    resolved, unresolved = _one(SourceRef(
        note="29", caption="– consolidated tangible net worth shall not be less than $1,"))
    assert not unresolved
    assert resolved[0]["figures"] == {"prose": "1100000"}
    assert resolved[0]["prose"] and resolved[0]["sentence_located"]


def test_an_amount_the_cited_words_carry_is_used_when_the_note_prints_it():
    resolved, _ = _one(SourceRef(note="29", caption="tangible net worth not less than $1,100,000"))
    assert Decimal(resolved[0]["figures"]["prose"]) == Decimal("1100000")


def test_an_amount_the_note_does_not_print_is_never_taken_from_the_model():
    resolved, unresolved = _one(SourceRef(
        note="29", caption="tangible net worth shall not be less than $2,500,000"))
    # 2,500,000 is not in the note; the cited words then locate the sentence, whose amount is
    # the note's own 1,100,000 — the model's number is never the one published.
    assert all(r["figures"]["prose"] != "2500000" for r in resolved)


def test_a_sentence_stating_several_amounts_is_refused_and_says_so():
    text = "Net worth shall not be less than $1,100,000 and the current ratio covers $2,200,000."
    resolved, unresolved = _one(SourceRef(note="29", caption="Net worth shall not be less than"),
                                text)
    assert not resolved
    assert "states 2 amounts" in unresolved[0]["why"]


def test_words_the_note_does_not_contain_are_still_refused():
    resolved, unresolved = _one(SourceRef(note="29", caption="minimum cash balance of the group"))
    assert not resolved
    assert "no sentence of the note contains it" in unresolved[0]["why"]
