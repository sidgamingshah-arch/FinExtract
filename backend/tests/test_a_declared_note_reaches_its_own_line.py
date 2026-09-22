"""A LINE'S OWN `note_title_any` REACHES ITS OWN REQUEST — the other half of `identified_notes`.

NEW FILE -> backend/tests/test_a_declared_note_reaches_its_own_line.py

THE ASYMMETRY THIS PINS, in the codebase's own words. `note_context.identified_notes` attaches a
pattern-claimed note's TEXT unconditionally and exempt from `_SEMANTIC_NOTE_BUDGET`, because "an
author declared it, which is a stronger statement than any score", and it ranks the filing's
printed citation BELOW that. `line_item_notes.note_sets` did the reverse: the citation took the one
priority slot and the ~650 authored `note_title_any` patterns took none, reaching a line's own set
only where their vocabulary happened to fall out of `note_probe`. `line_item_llm.build_request`
then INTERSECTS the two, so a note admitted on the declaration's authority could be absent from the
request of the line that declared it. Not a ranking preference — the two halves of one decision
disagreeing.

MEASURED WITH `scripts/note_context_to_llm.py` OVER THE FIVE REFERENCE FILINGS:

                                          before   after
    (line, declared note) pairs delivered  170/421  369/421      40.4% -> 87.6%
    lines sent notes but NOT the one they   21        0
      declare, which is present in the
      filing and already in identified_notes

THE TWO CORRECTIONS THIS FILE EXISTS TO KEEP. Both were measured failures of earlier versions of
the same change, and neither is visible from the delivery figure:

  1. DECLARING IS NOT DISPLACING. Promoting the declaration INTO `cap` displaced 6 scored notes on
     688008, and one of the 6 was the whole defect: `sub__ga_depreciation` declares the ASSET notes
     (投资性房地产, 固定资产, 在建工程 — where depreciation is charged FROM) and those plus a pooled
     note key filled all four slots and evicted 七、64 管理费用, scored 1.000, the note that
     actually PRINTS the G&A depreciation line.
  2. PRINTED ORDER IS NOT AN OPINION ABOUT RELEVANCE. Ordering the declared notes as the document
     prints them made 七、2 作为出租人 the FIRST note for the four long-term securities parts,
     ahead of 七、18 其他权益工具投资 which the probe scores 0.723. On the LLM route that alone took
     the column from 575,243,925.97 — the printed figure — to 8,629,412,600.11 and put
     `section_reconciliation:bs_nca` out by 8.05bn in both periods.

So: the declaration decides MEMBERSHIP, the score decides ORDER, and nothing is displaced.

WHAT MOVED, corpus-wide. Deterministic route: 0 figures and 0 structural statuses on all five
filings — `note_sets` feeds `line_item_llm` plans and nothing else. LLM route (spy): 0 structural
statuses on all five. Payload 5,771,270 -> 8,008,026 characters (+38.8%), and reachability by the
lines' own row gate 46.2% -> 48.7% of gradeable lines with 2 fewer lines sent no notes at all.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable, Provenance)
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import line_item_llm, line_item_notes, note_context


def _note(number: str, title: str, captions: tuple[str, ...] = ()) -> NotesTable:
    table = NotesTable(note_number=number, title=title, page_index=9)
    for caption in captions:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("1"), value_raw=Decimal("1"),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


# UNRELATED HEADINGS, because the header scorer is IDF-WEIGHTED: a token present in every heading
# distinguishes nothing, so a fixture of near-identical notes scores 0.000 everywhere and tests
# nothing. `test_cited_notes` learnt this the same way and says so.
_POOL = ("Inventories", "Share capital", "Property plant and equipment",
         "Trade and other receivables", "Bank borrowings", "Income tax",
         "Cash and cash equivalents", "Related party transactions",
         "Deferred taxation", "Segment information", "Share based payments",
         "Financial risk management")


def _filing(*extra: NotesTable) -> list[NotesTable]:
    notes = [_note(str(i + 1), title) for i, title in enumerate(_POOL)]
    notes.extend(extra)
    return notes


def _item(key: str, **kw) -> LineItemDef:
    kw.setdefault("label", key)
    return LineItemDef(key=key, **kw)


def _vias(hits) -> dict[str, str]:
    return {h.note: h.via for h in hits}


# ── the predicate ─────────────────────────────────────────────────────────────────────────────

def test_the_declaration_names_the_notes_of_this_filing_it_matches() -> None:
    item = _item("x", note_source=NoteSource(note_title_any=[r"^\s*Bank borrowings"]))

    assert line_item_notes.declared_notes(item, _filing()) == ("5",)


def test_a_declaration_matching_nothing_in_this_filing_names_nothing() -> None:
    """Not a defect — the disclosure is absent, so there is nothing to pass."""
    item = _item("x", note_source=NoteSource(note_title_any=[r"Convertible bonds"]))

    assert line_item_notes.declared_notes(item, _filing()) == ()


def test_the_note_NUMBER_is_matched_as_well_as_the_title() -> None:
    """Several declarations name a disclosure by its enumerator because the filing prints no
    heading a pattern could anchor. `identified_notes` honours that and so must this."""
    item = _item("x", note_source=NoteSource(note_title_any=[r"^7$"]))

    assert line_item_notes.declared_notes(item, _filing()) == ("7",)


def test_the_enumerator_a_heading_arrives_with_does_not_defeat_an_anchored_pattern() -> None:
    """THROUGH `note_context.matches_title`, which is the site `identified_notes` matches on: 44%
    of corpus headings arrive with a leading separator and almost every authored pattern is
    anchored, so searching the raw heading alone would score a claimed note as unclaimed."""
    notes = _filing(_note("20", "、 其他应收款"))
    item = _item("x", note_source=NoteSource(note_title_any=[r"^\s*(?:\d+[.、)]?\s*)?其他应收款"]))

    assert line_item_notes.declared_notes(item, notes) == ("20",)


def test_a_pattern_THAT_WILL_NOT_COMPILE_CANNOT_REACH_THE_SELECTOR() -> None:
    """WHERE THAT GUARD ACTUALLY LIVES, asserted because `declared_notes` also carries a
    `re.error` skip and a reader would reasonably assume that is the protection. It is not: the
    schema refuses the set outright, so the skip mirrors `identified_notes` for symmetry and is
    unreachable through any configuration a run can load. A test asserting the skip would be
    asserting dead code and would go quietly green if the schema check were ever removed.
    """
    import pytest
    with pytest.raises(ValueError, match="does not compile"):
        NoteSource(note_title_any=["[unterminated", r"^\s*Income tax"])


def test_a_line_declaring_no_patterns_names_nothing() -> None:
    assert line_item_notes.declared_notes(_item("x"), _filing()) == ()
    assert line_item_notes.declared_notes(
        _item("x", note_source=NoteSource(note_terms=["inventories"])), _filing()) == ()


# ── what reaches the line's own request ───────────────────────────────────────────────────────

def test_a_declared_note_is_delivered_where_no_probe_would_have_found_it() -> None:
    """THE GAIN, and the shape of all 21 corpus cases: the line's prose names nothing this filing
    prints, so scoring delivers the declared note not at all."""
    item = _item("x", note_source=NoteSource(note_terms=["nothing in these headings"],
                                             note_title_any=[r"^\s*Deferred taxation"]))
    notes = _filing()

    got = line_item_notes.note_sets([item], notes)[item.key]

    assert "9" in {h.note for h in got}
    assert _vias(got)["9"] == "declared", "the provenance must say it was not scored"
    assert [h.title for h in got if h.note == "9"] == ["Deferred taxation"]


def test_the_declaration_does_not_displace_a_scored_note_at_the_cap() -> None:
    """CORRECTION 1, and the reason this is exempt from `cap` rather than ranked inside it. The
    real case is `sub__ga_depreciation`: it declares the asset notes, where the charge originates,
    while the note that PRINTS the functional split scores 1.000 and is not declared anywhere. A
    declaration about where a charge comes from is not a statement that the split is unprinted.
    """
    item = _item("x", note_source=NoteSource(
        note_terms=["cash and cash equivalents", "bank borrowings"],
        note_title_any=[r"^\s*Segment information", r"^\s*Share based payments",
                        r"^\s*Financial risk management", r"^\s*Deferred taxation"]))
    notes = _filing()

    scored_only = line_item_notes.note_sets(
        [_item("x", note_source=NoteSource(
            note_terms=["cash and cash equivalents", "bank borrowings"]))], notes)["x"]
    got = line_item_notes.note_sets([item], notes)[item.key]

    assert {h.note for h in scored_only} <= {h.note for h in got}, (
        f"a scored note was pushed out: {[h.note for h in scored_only]} -> "
        f"{[h.note for h in got]}")
    assert {"9", "10", "11", "12"} <= {h.note for h in got}


def test_the_declared_notes_are_ordered_by_score_not_by_printed_order() -> None:
    """CORRECTION 2, and the one that moved a verified figure by 8.05bn. Note 12 prints last and is
    what the line is about; note 9 prints first and is a second, weaker claim. Printed order would
    lead with 9 — and the LLM route reads the first note it is offered.
    """
    item = _item("x", note_source=NoteSource(
        note_terms=["financial risk management"],
        note_title_any=[r"^\s*Deferred taxation", r"^\s*Financial risk management"]))

    got = line_item_notes.note_sets([item], _filing())[item.key]
    declared = [h.note for h in got if h.via == "declared"]

    assert declared == ["12", "9"], f"printed order, not score order: {declared}"


def test_a_declared_note_the_probe_scores_nothing_for_sorts_last_among_the_declared() -> None:
    """It keeps its place — membership is the declaration's call — and it ranks honestly."""
    item = _item("x", note_source=NoteSource(
        note_terms=["income tax"],
        note_title_any=[r"^\s*Share capital", r"^\s*Income tax"]))

    got = line_item_notes.note_sets([item], _filing())[item.key]

    assert [h.note for h in got if h.via == "declared"] == ["6", "2"]


def test_the_declaration_outranks_the_filings_own_citation() -> None:
    """`identified_notes` already ranks them this way: the citation spends budget and the authored
    pattern is exempt from it. An author's statement about THIS CONFIGURATION is the stronger of
    the two claims, and the two halves must not disagree about which.
    """
    item = _item("x", note_source=NoteSource(note_title_any=[r"^\s*Income tax"]))

    got = line_item_notes.note_sets([item], _filing(), cited={item.key: ("1",)})[item.key]

    assert [h.note for h in got][:2] == ["6", "1"]
    assert _vias(got) == {"6": "declared", "1": "cited"}


def test_note_selection_any_does_not_decline_the_declaration() -> None:
    """The field says a line's PRINTED reference is not to be trusted ahead of a score — a
    statement about the filing's claim. `identified_notes` records that it "no longer gates" the
    pattern pass, and a line whose author wrote the pattern and then declined to prefer it would
    be declaring two opposite things. 539 of 539 shipped lines declare neither value.
    """
    item = _item("x", note_selection="any",
                 note_source=NoteSource(note_title_any=[r"^\s*Income tax"]))

    got = line_item_notes.note_sets([item], _filing(), cited={item.key: ("1",)})[item.key]

    assert _vias(got).get("6") == "declared"
    assert "1" not in _vias(got), "the citation is still declined"


def test_a_line_that_declares_nothing_is_untouched() -> None:
    """The cited/scored trade is unchanged, which is what keeps `test_cited_notes` honest: on a
    line with no `note_title_any` this function must behave exactly as it did."""
    item = _item("x", note_source=NoteSource(note_terms=["cash and cash equivalents", "bank"]))
    notes = _filing()

    plain = line_item_notes.note_sets([item], notes, cap=2)[item.key]
    assert [(h.note, h.via) for h in plain] == [("7", "header"), ("5", "header")]

    with_citation = line_item_notes.note_sets([item], notes, cap=2,
                                              cited={item.key: ("1",)})[item.key]
    assert [(h.note, h.via) for h in with_citation] == [("1", "cited"), ("7", "header")]


# ── the two halves agree ──────────────────────────────────────────────────────────────────────

def test_the_note_a_line_declares_arrives_in_its_request_WITH_its_rows() -> None:
    """THE POINT OF ALL OF IT. `build_request` intersects the line's set with `identified_notes`,
    so a declared note absent from the set is a note whose text the model never sees however
    unconditionally the other half admitted it. This asserts the end of the wire, not the middle.
    """
    notes = _filing(_note("20", "Deferred consideration payable",
                          ("Deferred consideration payable", "Total")))
    item = _item("x", statement="balance_sheet", mode="extract",
                 note_source=NoteSource(note_terms=["nothing in these headings"],
                                        note_title_any=[r"^\s*Deferred consideration"]))
    cfg = LineItemSet(items=[item])

    from app.config import get_settings
    plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(
        cfg, notes, get_settings())
    plan = next(p for p in plans if item.key in p.keys)
    req = line_item_llm.build_request(plan, by_key, notes_of, identified)

    block = next(n for n in req["notes"] if str(n["note"]) == "20")
    assert [r["caption"] for r in block["rows"]] == ["Deferred consideration payable", "Total"]


def test_the_two_halves_claim_the_same_notes() -> None:
    """One predicate, asserted as one. `identified_notes` decides which note's TEXT travels and
    `declared_notes` decides which notes a line NAMES; if they can disagree, a line names a note
    with nothing under it or is refused a note the document already carries."""
    notes = _filing(_note("20", "、 其他应收款", ("其他应收款",)))
    item = _item("x", note_source=NoteSource(
        note_title_any=[r"^\s*(?:\d+[.、)]?\s*)?其他应收款", r"^\s*Income tax"]))
    cfg = LineItemSet(items=[item])

    named = set(line_item_notes.declared_notes(item, notes))
    carried = {str(n.get("note")) for n in note_context.identified_notes(cfg, notes)}

    assert named == {"20", "6"}
    assert named <= carried, f"declared but not carried: {named - carried}"
