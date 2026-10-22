"""The Ind AS set reads related parties the way the HK set does.

Two rules the HK set gained and the Ind AS set, made from it, did not:

- Related Party Transactions is a FIGURE, the total of the transactions the related-party note
  prints for the year, added up from one internal part per category (sales, purchases, leases,
  interest, key managerial compensation, other), with every amount and the note quoted beside it.
  The Ind AS line still asked the model for "the complete relevant disclosure" of a note it could
  only cite rows of, and the export wrote Yes/No from a presence scan.
- A related-party balance moved out of a face line lands on the related-party line of the same
  section. Other Receivables (CP) deducted the related-party balances its note states are inside
  it, and Due from Related Parties (CP) was a face-only line, so nothing received them and total
  current assets fell short by the amount deducted.

An Indian related-party note is English and prints its own words: "Transactions with related
parties during the year", "Compensation to key managerial personnel", "Remuneration", "Dividend
paid", "Contributions during the year". Asian Paints 2025-26 (note 34, PDF p275) prints
"Total compensation paid to key managerial personnel 44.90 / 23.74" (crores).
"""
from __future__ import annotations

import json
import pathlib
import re
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import line_disclosure, line_item_notes
from app.services.line_items import evaluate
from app.stages.note_sourced import NoteSourcedStage, _children_by_parent

_TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
HK = _TEMPLATES / "output_csv_hk_line_items.json"
INDAS = _TEMPLATES / "output_csv_indas_line_items.json"
RPT = "notes__related_party_transactions"
PARTS = ("sub__rpt_sales", "sub__rpt_purchases", "sub__rpt_leases", "sub__rpt_interest",
         "sub__rpt_key_management", "sub__rpt_other")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def _load(path):
    return load_line_item_set(json.loads(path.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def indas():
    return _load(INDAS)


@pytest.fixture(scope="module")
def hk():
    return _load(HK)


def _items(cfg):
    return {i.key: i for i in cfg.items}


# --- the configuration --------------------------------------------------------------------------

def test_the_line_is_the_hk_set_s_sum_of_one_part_per_category(indas, hk):
    mine, theirs = _items(indas)[RPT], _items(hk)[RPT]
    assert str(mine.type) == "derived"
    assert not mine.aliases, "a derived line is unmatchable by caption"

    def shape(line):
        return [(r.id, r.terms_op, [(t.ref, t.role, t.sign) for t in r.terms])
                for r in line.cascade]

    assert shape(mine) == shape(theirs)
    assert [t.ref for t in mine.cascade[0].terms] == list(PARTS)
    for word in ("balances", "guarantees", "not added"):
        assert word in mine.definition.lower(), word


@pytest.mark.parametrize("key", PARTS)
def test_every_part_is_an_internal_note_part_in_ind_as_words(indas, key):
    part = _items(indas)[key]
    assert part.parent == RPT
    assert part.namespace == "internal" and part.in_output is False
    assert str(getattr(part.statement, "value", part.statement)) == "notes"
    assert part.route == "note_tables"
    assert not part.aliases
    terms = set(part.note_source.note_terms)
    assert {"related party transactions", "related party disclosures",
            "transactions with related parties during the year"} <= terms
    # No Chinese in what steers the model or selects its rows. (A veto alternation carrying Han
    # beside English stays as the HK set has it: `test_the_ind_as_set` pins every veto.)
    ns = part.note_source
    assert not _HAN.search(" ".join([part.definition, part.label, *ns.note_terms,
                                     *ns.row_terms, *ns.row_caption_any])), key


def test_the_parts_read_by_the_model_take_no_row_deterministically(indas):
    """Only key managerial pay is read off its own table; the five other parts carry no row
    rules, so a title match can point the model at the note and never publish a row."""
    from app.services.note_sourced import select_rows

    table = NotesTable(note_number="32", title="Related party disclosures", page_index=161)
    row = NoteItem(raw_label="Sale of goods/services", note_number="32")
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("1.32"), provenance=Provenance(page_index=161)))
    table.items.append(row)
    for key in PARTS:
        if key == "sub__rpt_key_management":
            continue
        part = _items(indas)[key]
        assert not part.note_source.row_caption_any, key
        assert select_rows(part, [table]) == [], key


# --- what the model is sent ---------------------------------------------------------------------

def _table(number, title, caption, amount, *, chapter="", page=275):
    row = NoteItem(raw_label=caption, note_number=number)
    row.values["k"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=page))
    return NotesTable(note_number=number, title=title, chapter_title=chapter, items=[row],
                      source_pages=[page], source_text=f"{title} {caption} {amount}")


def _ind_notes() -> list[NotesTable]:
    """Asian Paints' shape: an other-financial-assets note and note 34's tables."""
    return [
        _table("8", "Other financial assets", "Security deposits", "120.50", page=250),
        _table("34", "Related Party Transactions", "Revenue from Sale of Products", "10.73",
               page=276),
        _table("34", "Compensation to key managerial personnel of the Parent Company",
               "Total compensation paid to key managerial personnel", "44.90",
               chapter="Related Party Transactions"),
    ]


@pytest.mark.parametrize("key", PARTS)
def test_each_part_is_sent_the_related_party_note_and_not_other_financial_assets(indas, key):
    part = _items(indas)[key]
    hits = line_item_notes.notes_for_line_item(part, line_item_notes.header_pool(_ind_notes()))
    got = [h.note for h in hits]
    assert "34" in got, got
    assert "8" not in got, got


# --- key managerial pay, read off its own table ---------------------------------------------------

@pytest.mark.parametrize("caption, taken", [
    ("Total compensation paid to key managerial personnel", True),
    ("Total compensation paid to key management personnel", True),
    ("Key managerial personnel remuneration", True),
    ("Short-term employee benefits", False),
    ("Share-based payments", False),
])
def test_both_sets_take_the_compensation_total_in_either_spelling(indas, hk, caption, taken):
    for cfg in (indas, hk):
        ns = _items(cfg)["sub__rpt_key_management"].note_source
        hit = any(re.search(p, caption, re.IGNORECASE) for p in ns.row_caption_any)
        veto = any(re.search(p, caption, re.IGNORECASE) for p in ns.row_caption_none)
        assert (hit and not veto) is taken, (cfg.line_items_key, caption)


def _llm_part(key: str, label: str, amount: str, note: str, page: int) -> LineItem:
    row = LineItem(source_label=key, canonical_key=key)
    row.values["c"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount))
    row.derivation = {"consolidated:current": {
        "method": "line_item_llm:1_cited_row", "result": amount, "version": 2, "op": "first",
        "inputs": [{"label": label, "value": amount, "note": note, "counted": True,
                    "status": "used", "used": amount, "provenance": {"page_index": page}}]}}
    return row


def test_the_line_adds_its_parts_and_quotes_the_note(indas):
    doc = DocumentModel(filename="f.pdf")
    doc.notes = _ind_notes()
    doc.line_items = [_llm_part("sub__rpt_sales", "Revenue from Sale of Products", "10.73", "34",
                                276)]
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = indas
    NoteSourcedStage().run(doc, ctx)
    line = next(li for li in doc.line_items if li.canonical_key == RPT)
    figure = next(ev.value for ev in line.values.values() if ev.period_label == "current")
    # 10.73 of sales read by the model, 44.90 of key managerial pay read off its table.
    assert figure == Decimal("10.73") + Decimal("44.90")
    items = {(i["part"], i["amount"]) for i in line.disclosure["items"]}
    assert items == {("sub__rpt_sales", "10.73"), ("sub__rpt_key_management", "44.90")}
    quoted = {n["note"] for n in line.disclosure["notes"]}
    assert quoted == {"34"}
    assert RPT in line_disclosure.disclosing_lines(indas.items)


# --- the related-party balance that leaves other receivables --------------------------------------

FACE = Decimal("1250.00")
RP = Decimal("2.43")


@pytest.mark.parametrize("parts", [
    {"sub__cp_other_receivables_net": FACE, "sub__cp_face_other_receivables": FACE},
    {"sub__cp_face_other_receivables": FACE},
])
def test_what_leaves_other_receivables_arrives_on_the_current_due_from_line(indas, parts):
    defs = _items(indas)
    inputs = dict(parts, sub__cp_other_receivables_rp=RP)
    other = evaluate(defs["bs_ca__other_receivables_cp"], inputs)
    due_from = evaluate(defs["bs_ca__due_from_related_parties_cp"], inputs)
    assert other.value == FACE - RP
    assert due_from.value == RP
    assert other.value + due_from.value == FACE


def test_the_due_from_line_is_the_hk_set_s_cascade_with_its_printed_row_as_a_part(indas, hk):
    mine, theirs = _items(indas), _items(hk)
    line = mine["bs_ca__due_from_related_parties_cp"]
    assert str(line.type) == "derived" and not line.aliases
    assert all(not r.carved_from_face for r in line.cascade)
    assert [(t.ref, t.role, t.sign, t.received) for r in line.cascade for t in r.terms] == [
        (t.ref, t.role, t.sign, t.received)
        for r in theirs["bs_ca__due_from_related_parties_cp"].cascade for t in r.terms]

    face = mine["sub__rp_face_due_from_cp"]
    assert face.parent == "bs_ca__due_from_related_parties_cp"
    assert face.namespace == "internal" and face.in_output is False
    assert str(getattr(face.statement, "value", face.statement)) == "balance_sheet"
    assert list(face.section_scope) == ["bs_ca"]
    assert {"Due from related parties", "Amounts due from related parties"} <= set(face.aliases)
    assert not any(_HAN.search(a) for a in face.aliases)
    printed = Decimal("4.00")
    assert evaluate(line, {"sub__rp_face_due_from_cp": printed}).value == printed
    assert evaluate(line, {"sub__rp_face_due_from_cp": printed,
                           "sub__cp_other_receivables_rp": RP}).value == printed + RP
    assert evaluate(line, {}).value is None


def test_every_rung_of_other_receivables_deducts_what_the_due_from_line_receives(indas):
    defs = _items(indas)
    for rung in defs["bs_ca__other_receivables_cp"].cascade:
        assert any(t.ref == "sub__cp_other_receivables_rp" and t.sign == -1
                   for t in rung.terms), rung.id
    kids = _children_by_parent(list(defs.values()))
    assert "sub__cp_other_receivables_rp" in kids["bs_ca__due_from_related_parties_cp"]
