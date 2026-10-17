"""THE ROWS A CITATION RESOLVES IN ARE THE ROWS THE REQUEST CARRIES.

`face_context.face_rows` builds the statement blocks the model is shown; `face_context.face_index`
is what `note_sourced.resolve_sources` looks a statement citation up in. The blocks stopped carrying
notes-page rows, rows printed in the notes and footer/signature rows, and the lookup kept them all:
a citation of a caption the request never showed could still land on one of those rows. The two
now share one rule (`face_context._shown_on_face`), and these tests pin that they agree.

The second fault is the lower line of a two-line column heading: "Note | Year ended 31 March" over
"No. | 2025 | 2024". The reader took it for a row, and f1's standalone profit and loss opened with a
row "No." whose figures were 2025 and 2024.
"""
from __future__ import annotations

import ast
import builtins
from decimal import Decimal
from pathlib import Path

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind, PrintedIn
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import face_context

FACE, NOTES = 3, 40


def _row(caption, *values, page=FACE, printed_in=None):
    li = LineItem(source_label=caption, printed_in=printed_in)
    for label, v in zip(("current", "prior"), values):
        li.values[label] = ExtractedValue(basis=Basis.STANDALONE, period_label=label,
                                          value=Decimal(v), value_raw=Decimal(v),
                                          provenance=Provenance(page_index=page))
    return li


def _doc(rows):
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=FACE, kind=PageKind.FACE, statement="profit_and_loss"),
                 PageSource(index=NOTES, kind=PageKind.NOTES, statement="profit_and_loss")]
    doc.line_items = list(rows)
    return doc


def _shown(doc):
    return [r["caption"] for b in face_context.face_rows(doc, {"profit_and_loss"})
            for r in b["rows"]]


def _looked_up(doc):
    return [caption for _st, caption, _row in face_context.face_index(doc)]


def test_the_lookup_holds_only_the_rows_the_request_shows():
    doc = _doc([
        _row("No.", "2025", "2024"),
        _row("Revenue from operations", "77412", "70210"),
        _row("Revenue from operations", "77412", "70210", page=NOTES),
        _row("Other income", "512", "430", printed_in=PrintedIn.NOTES),
        _row("As per our report of even date", "1", "2"),
    ])
    assert _shown(doc) == ["Revenue from operations"]
    assert _looked_up(doc) == _shown(doc)


def test_a_citation_of_a_row_only_a_note_page_printed_finds_nothing():
    doc = _doc([_row("Depreciation", "900", "850", page=NOTES)])
    assert _looked_up(doc) == []


def test_the_year_line_of_a_column_heading_is_not_a_row():
    doc = _doc([_row("No.", "2025", "2024"), _row("Note No.", "2025", "2024"),
                _row("Particulars", "2025")])
    assert _shown(doc) == [] and _looked_up(doc) == []


def test_a_real_row_whose_figure_looks_like_a_year_is_kept():
    # Only the two together mark a heading line: a heading word as the caption AND years as the
    # figures. A printed amount of 2,025 on a real caption, or a note number in the note column,
    # is a row.
    doc = _doc([_row("Finance costs", "2025", "2024"), _row("No.", "23", "24")])
    assert _shown(doc) == ["Finance costs", "No."]
    assert _looked_up(doc) == _shown(doc)


def test_every_annotation_in_the_pdf_reader_names_something_it_imports():
    """`services.pdf_extract` annotated a local as ``tuple[LineItem, ...]`` without importing it.

    Postponed annotations keep that from failing at import, so only a linter saw it; this walks
    every annotation in the module and resolves each bare name against its globals.
    """
    from app.services import pdf_extract

    tree = ast.parse(Path(pdf_extract.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        ann = getattr(node, "annotation", None) if isinstance(
            node, (ast.AnnAssign, ast.arg)) else None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            ann = node.returns
        if ann is not None:
            names |= {n.id for n in ast.walk(ann) if isinstance(n, ast.Name)}
    missing = sorted(n for n in names
                     if n not in vars(pdf_extract) and not hasattr(builtins, n))
    assert missing == []
