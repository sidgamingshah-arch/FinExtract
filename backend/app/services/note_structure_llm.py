"""AI-first structuring for note matrices that cannot fit the two-period parser."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from app.core.models.enums import Basis
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.ports.llm import LlmProvider
from app.services.notes_extract import note_row_role
from app.services.row_reconstruct import Word


class MatrixCell(BaseModel):
    column: str = Field(min_length=1)
    value_text: str = Field(min_length=1)


class MatrixRow(BaseModel):
    section: str | None = Field(default=None, min_length=2)
    label: str = Field(min_length=2)
    cells: list[MatrixCell] = Field(min_length=1)


class StructuredNote(BaseModel):
    columns: list[str] = Field(min_length=2, max_length=12)
    rows: list[MatrixRow] = Field(min_length=1, max_length=250)


_SYSTEM = """You structure a financial-statement note table from positioned OCR/native-PDF tokens.
Use exact source text for every row label, column header, section heading, and numeric cell; do not
calculate, infer, combine, or invent values. Omit prose, page headers, dates used only as running
captions, and incomplete text fragments. Every value_text must exactly match a numeric source token.
Retain every distinct schedule and movement row: do not return only closing or net carrying-value
balances. This includes rows such as "Exchange realignment" and "Amortisation provided during the
year" whenever they appear in the source. Keep only rows with a substantive label and at least one
numeric cell. Emit its printed schedule heading in `section`, such as "Cost" or "Accumulated
depreciation and impairment".

For a simple two-column comparative schedule, return exactly "Current year", "Prior year" in that
order, placing the source current and comparative values under them. These normalized headers are
allowed even if the source prints dates or years. Do NOT use this normalization for a category
matrix. A property, plant and equipment note commonly has columns such as "Buildings", "Plant and
equipment", "Right-of-use assets", and "Total". For every category matrix, return every exact visible
category header, including "Total", and emit every numeric cell under its own category column. Do
not collapse category columns to Total, and do not combine values from different asset classes.
For a matrix spanning two periods, retain the printed category headers; distinguish repeated headers
with their printed period heading where necessary. Omit a dash cell rather than using it as a value."""

_PRESENTATION_HEADERS = frozenset({"Current year", "Prior year"})


def _source_words(words: list[Word]) -> list[dict]:
    return [{"text": word.text, "x0": round(word.bbox.x0, 4), "y0": round(word.bbox.y0, 4),
             "x1": round(word.bbox.x1, 4), "y1": round(word.bbox.y1, 4)} for word in words]


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "").replace("(", "-").replace(")", ""))
    except (InvalidOperation, AttributeError):
        return None


def _visible_phrase(text: str, token_by_text: dict[str, list[Word]]) -> bool:
    """Whether the model's label/header can be traced to words visible in the source."""
    return text in token_by_text or all(part in token_by_text for part in text.split())


def structure_note_matrix(provider: LlmProvider, *, note_number: str, title: str,
                          words: list[Word], page_index: int,
                          document_id: str | None, source_kind: str) -> NotesTable | None:
    """Return a validated AI-structured matrix, or ``None`` so malformed input is dropped."""
    prompt = json.dumps({"note_number": note_number, "title": title,
                         "presentation_contract": {
                             "comparative_columns": ["Current year", "Prior year"],
                                 "rule": "Use these exact headers when the note has current and prior values. "
                                     "Return every schedule and movement row, including exchange and "
                                     "amortisation rows when printed."},
                         "tokens": _source_words(words)}, ensure_ascii=False)
    result, _meta = provider.complete_structured(
        system=_SYSTEM, messages=[{"role": "user", "content": prompt}],
        response_schema=StructuredNote, temperature=0.0, max_tokens=8192)
    structured = result if isinstance(result, StructuredNote) else StructuredNote.model_validate(result)
    columns = [column.strip() for column in structured.columns]
    token_by_text: dict[str, list[Word]] = {}
    for word in words:
        token_by_text.setdefault(word.text.strip(), []).append(word)
    valid_headers = set(columns) == _PRESENTATION_HEADERS or all(
        _visible_phrase(column, token_by_text) for column in columns)
    if len(set(columns)) != len(columns) or not valid_headers:
        return None
    table = NotesTable(note_number=note_number, title=title, source_pages=[page_index])
    items_by_schedule_and_label: dict[tuple[str, str], NoteItem] = {}
    emitted_columns: dict[tuple[str, str], set[str]] = {}
    for ordinal, row in enumerate(structured.rows):
        label = row.label.strip()
        section = row.section.strip() if row.section else ""
        if not _visible_phrase(label, token_by_text) or (section and not _visible_phrase(section, token_by_text)):
            continue
        identity = (section, label)
        item = items_by_schedule_and_label.get(identity)
        if item is None:
            item = NoteItem(raw_label=label, ordinal=ordinal, role=note_row_role(label), group_hint=section)
            items_by_schedule_and_label[identity] = item
            emitted_columns[identity] = set()
            table.items.append(item)
        for cell in row.cells:
            column, value_text = cell.column.strip(), cell.value_text.strip()
            value, matches = _decimal(value_text), token_by_text.get(value_text)
            if column not in columns or value is None or not matches or column in emitted_columns[identity]:
                continue
            word = matches.pop(0)
            provenance = Provenance(document_id=document_id, page_index=page_index,
                                    bbox=word.bbox, label_bbox=word.bbox,
                                    text_snippet=f"{label} {value_text}", source_kind=source_kind)
            item.set_value(ExtractedValue(value_raw=value, value=value, basis=Basis.CONSOLIDATED,
                                          period_label=column, period_display=column,
                                          provenance=provenance))
            emitted_columns[identity].add(column)
    table.items = [item for item in table.items if item.values]
    return table if table.items else None