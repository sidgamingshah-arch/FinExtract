"""A CITED FIGURE SAYS WHERE IT WAS READ, and a line shows the notes behind it (Workspace rows,
`documents._build_statement`).

Every line the model answers records a trail of the rows it cited (`stages.line_item_llm`). The
Workspace read any trail as an assembly: the inspector tagged the figure "computed" — "Assembled by
the line_item_llm:1_cited_row rule from the inputs below" — and a total "assembled from notes", for
figures read off one printed row on the face (Madrigal's 10-K, where nearly every line is an answer).

And a US 10-K prints no note references on its statements, nor do many mainland filings, so the
NOTE column was empty on every line.

Pinned here:
  * a citation taken whole is "read from the face" or "read from note N", never "computed";
  * a rule's assembly (note_sourced) is still "computed";
  * a total the model read whole off the page is printed, not "derived"; a sum of face rows is
    derived "from printed rows", not "from notes";
  * where the statements print no note reference, the NOTE column shows the note the figure was
    read from, else the notes whose title names the line — marked as such, and only notes the run
    read; where any reference is printed, the column is the printed references and nothing else.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.api.routes.documents import _build_statement
from app.api.routes.extractions import _serialize_rows
from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import load_line_item_set
from app.services import note_sourced
from app.services.note_titles import notes_named, title_of
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TITLES = {"4": "4. Inventory", "8": "8. Debt", "5": "Property and Equipment, Net",
          "2": "Summary of Significant Accounting Policies"}


@pytest.fixture(scope="module")
def usgaap():
    st = load_line_item_set(json.loads((TEMPLATES / "output_csv_usgaap_line_items.json")
                                       .read_text(encoding="utf-8")), resolve=True)
    tpl = json.loads((TEMPLATES / "output_csv_usgaap_v1_template.json").read_text(encoding="utf-8"))
    return st, tpl


def _row(label, key, figures, *, notes=(None,), rollup="line_item_llm", combine="whole",
         printed_note=None):
    li = LineItem(source_label=label, canonical_key=key)
    if printed_note:
        li.note_number = printed_note
    for period, value in zip(("current", "prior"), figures):
        li.set_value(ExtractedValue(
            basis=Basis.CONSOLIDATED, period_label=period, value=Decimal(value),
            value_raw=Decimal(value),
            provenance=Provenance(page_index=4, bbox=BBox(x0=0.6, y0=0.3, x1=0.7, y1=0.31))))
        inputs = [{"label": f"{label} ({i})", "value": value, "note": n,
                   "provenance": {"page_index": 4}} for i, n in enumerate(notes)]
        li.derivation = note_sourced.derivation.record(
            li.derivation, basis="consolidated", period_label=period,
            derivation=note_sourced.trail(
                rollup=rollup, item_label=label, amount=Decimal(value), inputs=inputs,
                display=({"op": "sum" if combine == "component" else "first", "combine": combine}
                         if rollup == "line_item_llm" else None)))
    return li


def _rows_of(usgaap, line_items, statement="balance_sheet", titles=TITLES):
    st, tpl = usgaap
    doc = DocumentModel(filename="m.pdf")
    doc.line_items = line_items
    out = _build_statement(_serialize_rows(doc, build_working_view(st)), tpl, statement, "m.pdf",
                           line_item_set=st, note_titles=titles)
    found = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("id") and o.get("kind") in ("item", "subtotal", "total"):
                found.setdefault(o["id"], o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(out)
    return found


def test_a_figure_read_off_the_face_is_not_computed(usgaap):
    row = _rows_of(usgaap, [_row("Inventories", "bs_ca__inventories", ("74841", "34068"))])[
        "bs_ca__inventories"]
    assert row["origin"] == "extracted"
    assert row["inspector"]["tag"] == "read from the face"
    assert "not computed" in row["inspector"]["note"]
    assert "Assembled" not in row["inspector"]["note"] and "note line" not in row["inspector"]["note"]


def test_a_figure_read_from_a_note_names_the_note(usgaap):
    row = _rows_of(usgaap, [_row("Loan payable, net of discount", "bs_ncl__long_term_debt",
                                 ("339881", "117569"), notes=("8",))])["bs_ncl__long_term_debt"]
    assert row["inspector"]["tag"] == "read from note 8"
    assert (row["note"], row["note_by"], row["notes"]) == ("8", "figure", ["8"])


def test_a_rules_assembly_is_still_computed(usgaap):
    row = _rows_of(usgaap, [_row("Inventories", "bs_ca__inventories", ("74841", "34068"),
                                 notes=("4", "4"), rollup="note_rows")])["bs_ca__inventories"]
    assert row["inspector"]["tag"] == "computed"


def test_a_total_read_whole_off_the_page_is_printed_not_derived(usgaap):
    row = _rows_of(usgaap, [_row("Total liabilities", "bs_cl__total_liabilities",
                                 ("656900", "287864"))])["bs_cl__total_liabilities"]
    # Printed, and not checkable by its components (none was extracted) — what it was before the
    # model answered. It used to read "assembled from notes" with its one face row as the note line.
    assert row["origin"] == "reported_uncomputed"


def test_a_total_added_from_face_rows_is_derived_from_the_face(usgaap):
    row = _rows_of(usgaap, [_row("Total liabilities", "bs_cl__total_liabilities",
                                 ("656900", "287864"), notes=(None, None),
                                 combine="component")])["bs_cl__total_liabilities"]
    assert (row["origin"], row["derived_from"]) == ("derived", "face")
    assert row["inspector"]["tag"] == "added on the face"


def test_a_line_shows_the_note_its_title_names_when_nothing_is_printed(usgaap):
    rows = _rows_of(usgaap, [_row("Inventories", "bs_ca__inventories", ("74841", "34068")),
                             _row("Accounts payable", "bs_cl__accounts_payable", ("48878", "43599"))])
    inv = rows["bs_ca__inventories"]
    assert (inv["note"], inv["note_by"], inv["notes"]) == ("4", "title", ["4"])
    # No note is named by "Accounts payable": the column stays empty rather than guess.
    assert rows["bs_cl__accounts_payable"]["note"] is None
    assert "note_by" not in rows["bs_cl__accounts_payable"]


def test_a_filing_that_prints_references_shows_only_what_it_printed(usgaap):
    rows = _rows_of(usgaap, [_row("Inventories", "bs_ca__inventories", ("74841", "34068")),
                             _row("Accounts payable", "bs_cl__accounts_payable",
                                  ("48878", "43599"), printed_note="12")])
    assert rows["bs_ca__inventories"]["note"] is None
    assert "note_by" not in rows["bs_ca__inventories"]
    assert rows["bs_cl__accounts_payable"]["note"] == "12"
    assert "note_by" not in rows["bs_cl__accounts_payable"]


def test_a_note_the_run_did_not_read_is_never_named(usgaap):
    rows = _rows_of(usgaap, [_row("Loan payable", "bs_ncl__long_term_debt", ("339881", "117569"),
                                  notes=("31",))])
    assert rows["bs_ncl__long_term_debt"]["note"] != "31"


@pytest.mark.parametrize("terms, want", [
    (["Inventories", "Inventory"], ["4"]),
    (["Property and equipment, net"], ["5"]),
    (["Long-term debt"], []),            # "Debt" is not named by a longer caption
    (["Other"], []),                     # a one-word caption names only a whole title
    (["应收账款"], ["七、4", "七、3"]),
])
def test_which_titles_a_lines_captions_name(terms, want):
    titles = {**TITLES, "七、3": "、 以摊余成本计量的应收账款", "七、4": "应收账款坏账准备"}
    assert notes_named(terms, titles) == want


def test_a_titles_number_is_not_part_of_it():
    assert title_of("Note 12 – Commitments") == "Commitments"
    assert title_of("七、 应收账款") == "应收账款"
    assert title_of("4. Inventory") == "Inventory"
