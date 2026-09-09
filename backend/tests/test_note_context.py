"""The context selector: what it offers, what it refuses to offer, and what it never drops.

Every test here is about ONE property that, if it broke, would break silently — the request would
still be well-formed and the run would still produce mappings, just worse ones. That is the failure
mode this file exists for.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.note_context import ContextPool, ContextUnit, build_pool


def _note(ref: str, title: str, *captions: str) -> ContextUnit:
    return ContextUnit(kind="note", ref=ref, title=title, captions=captions)


def _face(row_id: str, caption: str, *, statement="balance_sheet", amount="") -> ContextUnit:
    return ContextUnit(kind="face", ref=statement, captions=(caption,),
                       amount=amount, row_id=row_id)


# A filing-shaped pool: several notes on different subjects, and face rows on two statements.
# "total", "group" and "the" appear across most units on purpose — they are the words IDF has to
# discount to nothing, and the tests below depend on it doing so.
def _pool() -> ContextPool:
    return ContextPool([
        _note("12", "Trade and other receivables",
              "Trade receivables from third parties", "Less: loss allowance",
              "Prepayments and deposits", "Total group receivables"),
        _note("13", "Inventories",
              "Raw materials", "Work in progress", "Finished goods", "Total group inventories"),
        _note("14", "Property, plant and equipment",
              "Leasehold land and buildings", "Plant and machinery",
              "Accumulated depreciation", "Total group carrying amount"),
        _note("15", "Share capital",
              "Ordinary shares issued and fully paid", "Total group share capital"),
        _face("f1", "Trade and other receivables", amount="118400"),
        _face("f2", "Inventories", amount="42100"),
        _face("f3", "Trade receivables from third parties", amount="96200"),
        _face("f4", "Depreciation of property, plant and equipment",
              statement="profit_and_loss", amount="8300"),
    ])


def test_the_note_about_the_same_subject_is_offered_and_the_others_are_not():
    """The whole point: subject, not position in the document."""
    got = _pool().select(probe_text="Trade and other receivables from third parties, "
                                    "net of loss allowance for expected credit losses",
                         notes_cap=1, face_cap=0)
    assert [u["ref"] for u in got] == ["12"], got


def test_a_cited_note_is_offered_even_when_nothing_about_it_resembles_the_caption():
    """A printed reference is EVIDENCE. A similarity score is an inference, and must not be able to
    overrule one — this is the behaviour the old cited-note-only field guaranteed, and losing it
    while adding the new selection would be a regression dressed as an improvement."""
    got = _pool().select(probe_text="Ordinary shares issued and fully paid share capital",
                         cited_refs={"13"}, notes_cap=1, face_cap=0)
    refs = [u["ref"] for u in got]
    assert "13" in refs, refs
    assert got[0]["cited"] is True
    # And it is marked, so the model can weigh it differently from a resemblance.
    assert all("cited" not in u for u in got[1:])


def test_a_cited_note_is_never_crowded_out_by_the_cap():
    """The cap bounds the INFERRED units. A citation is not a candidate for eviction."""
    got = _pool().select(probe_text="Inventories of raw materials and finished goods",
                         cited_refs={"15"}, notes_cap=1, face_cap=0)
    assert [u["ref"] for u in got] == ["15"], got


def test_notes_and_face_rows_are_capped_separately():
    """One cap over both lets a filing with forty notes crowd out the face rows — and the face rows
    are the ones carrying the whole-vs-component evidence, which is the reason face rows are in the
    context at all."""
    got = _pool().select(probe_text="Trade and other receivables from third parties",
                         notes_cap=1, face_cap=2, char_budget=10_000)
    kinds = [u["kind"] for u in got]
    assert kinds.count("note") == 1 and kinds.count("face") == 2, got


def test_a_row_is_never_offered_as_context_for_itself():
    """The batch's own rows are already in `source_items`. Offering one back as corroboration
    produces a request that agrees with itself."""
    probe = "Trade and other receivables from third parties"
    both = _pool().select(probe_text=probe, notes_cap=0, face_cap=3, char_budget=10_000)
    assert {u["rows"][0] for u in both} >= {"Trade and other receivables",
                                            "Trade receivables from third parties"}
    kept = _pool().select(probe_text=probe, exclude_row_ids={"f3"},
                          notes_cap=0, face_cap=3, char_budget=10_000)
    captions = {u["rows"][0] for u in kept}
    assert "Trade receivables from third parties" not in captions
    # The exclusion removes ONE row, not the neighbourhood: the other face row still comes back.
    assert "Trade and other receivables" in captions


def test_a_face_row_carries_its_amount_because_the_amount_is_the_evidence():
    """A note row of 4,000 beside a face line of 12,345 is a component; the same figure twice is one
    fact printed twice. Both look identical without the numbers."""
    got = _pool().select(probe_text="Trade receivables from third parties",
                         notes_cap=0, face_cap=1, char_budget=10_000)
    assert got and got[0]["amount"]


def test_a_word_in_every_unit_earns_exactly_no_weight():
    """There is no stopword list — IDF over the document's own units is what replaces it, and that
    only works if the zero is EXACT. A formula that bottoms out near 0.69 instead lets two words of
    boilerplate select an unrelated note, which is a wrong request that looks like a right one."""
    pool = ContextPool([
        _note("1", "Total group consolidated receivables", "Trade debtors"),
        _note("2", "Total group consolidated inventories", "Finished goods"),
    ])
    assert pool.idf["total"] == 0.0 and pool.idf["group"] == 0.0
    assert pool.select(probe_text="total group consolidated", notes_cap=2, face_cap=2) == []


def test_boilerplate_scores_below_a_subject_even_when_it_is_not_universal():
    """The realistic case: "total group" is on half this filing's notes, not all of them, so its
    weight is small rather than zero. It must still lose decisively to a subject match, or the
    threshold is doing the work that the weighting is supposed to do."""
    pool = _pool()
    unit = next(u for u in pool.units if u.ref == "13")
    boiler = pool._score(unit, set("total group".split()))
    subject = pool._score(unit, set("raw materials work in progress finished goods".split()))
    assert boiler < subject / 2, (boiler, subject)


def test_a_subject_the_filing_never_discusses_gets_no_context_at_all():
    """Padding a request with the three least-unrelated notes is worse than sending none: it spends
    tokens to point the model at other subjects."""
    got = _pool().select(probe_text="Actuarial assumptions for the defined benefit pension scheme",
                         notes_cap=3, face_cap=3)
    assert got == [], got


def test_the_character_budget_cuts_the_tail_and_never_the_head():
    """The budget is applied to the ORDERED list, so what it drops is the least relevant unit. If it
    cut blindly it would be a bound that changes which evidence the model sees."""
    full = _pool().select(probe_text="Trade and other receivables from third parties",
                          notes_cap=3, face_cap=3, char_budget=10_000)
    cut = _pool().select(probe_text="Trade and other receivables from third parties",
                         notes_cap=3, face_cap=3, char_budget=120)
    assert 0 < len(cut) < len(full)
    assert cut == full[:len(cut)]


def test_at_least_one_unit_survives_however_small_the_budget():
    """A budget smaller than the first unit must not silently produce an empty context — the caller
    would read that as 'the filing says nothing about this row'."""
    got = _pool().select(probe_text="Trade and other receivables from third parties",
                         notes_cap=3, face_cap=3, char_budget=1)
    assert len(got) == 1


def test_the_selection_is_deterministic():
    """Two runs over one filing must produce the same request, or a difference in output can never
    be attributed."""
    a = _pool().select(probe_text="Inventories of raw materials", notes_cap=3, face_cap=3)
    b = _pool().select(probe_text="Inventories of raw materials", notes_cap=3, face_cap=3)
    assert a == b


def test_every_face_row_is_reachable_and_not_one_per_statement():
    """The de-duplication key. Face units share `ref` (the statement), so keying on it would admit
    exactly one row per statement — the difference between offering the neighbourhood and offering
    one arbitrary line, and invisible in the output either way."""
    got = _pool().select(probe_text="Trade and other receivables from third parties inventories",
                         notes_cap=0, face_cap=3, char_budget=10_000)
    assert len({u["rows"][0] for u in got}) == len(got) >= 2, got


def test_the_pool_reads_note_rows_rather_than_the_page_dump():
    """`source_text` is the page the note was parsed from — mostly accounting-policy narrative,
    which shares high-IDF words with everything. `items` are the note's own rows, which is what a
    breakdown consists of. The fallback exists for a note that produced no rows."""
    doc = SimpleNamespace(
        notes=[
            SimpleNamespace(note_number="12", title="Receivables",
                            items=[SimpleNamespace(raw_label="Trade receivables")],
                            source_text="pages of policy narrative " * 40),
            SimpleNamespace(note_number="13", title="Inventories", items=[],
                            source_text="Raw materials\nFinished goods"),
        ],
        line_items=[])
    pool = build_pool(doc)
    by_ref = {u.ref: u for u in pool.units}
    assert by_ref["12"].captions == ("Trade receivables",)
    assert by_ref["13"].captions == ("Raw materials", "Finished goods")


def test_the_pool_reads_the_real_document_models():
    """`build_pool` walks the real `DocumentModel` — `li.id`, `li.source_label`, the provenance
    page index inside `li.values`. A duck-typed stand-in cannot catch a field renamed on the model,
    and the symptom would be an empty pool: every request silently loses its context while the run
    still reports itself as LLM-mapped.
    """
    from decimal import Decimal

    from app.core.models import DocumentModel
    from app.core.models.enums import Basis
    from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable,
                                           Provenance)

    doc = DocumentModel(filename="f.pdf")
    doc.notes = [NotesTable(note_number="12", title="Trade and other receivables",
                            items=[NoteItem(raw_label="Trade receivables from third parties")])]
    doc.line_items = [LineItem(
        source_label="Trade and other receivables", section_hint="Current assets",
        values={"c": ExtractedValue(basis=Basis.CONSOLIDATED, value=Decimal("118400"),
                                    provenance=Provenance(page_index=3))})]

    pool = build_pool(doc, {3: "balance_sheet"})
    notes = [u for u in pool.units if u.kind == "note"]
    faces = [u for u in pool.units if u.kind == "face"]
    assert [u.captions for u in notes] == [("Trade receivables from third parties",)]
    assert len(faces) == 1
    face = faces[0]
    assert face.captions == ("Trade and other receivables",)
    assert face.ref == "balance_sheet"          # resolved through the provenance page index
    assert face.amount == "118400"              # the figure the component decision turns on
    assert face.row_id == str(doc.line_items[0].id)
    assert face.section == "Current assets"


def test_an_empty_pool_selects_nothing_instead_of_raising():
    """A filing with no notes and no rows is a real input (a single-page extract), and it must
    produce a request with no context rather than an error."""
    assert ContextPool([]).select(probe_text="anything") == []
