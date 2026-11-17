"""WHAT THE OVERALL SWEEP FOUND — four behaviour changes of the day's work, each measured by
running every filing in the corpus (25 test filings, six 10-Ks, Madrigal and three synthetic
10-Ks) on the code before the day's commits (24e660d) and after, and reading each moved figure
against the page.

* LAI SUN 2024-25 prints its current assets' subtotal on a bare line, then "Assets classified as
  held for sale", then "Total current assets". The reader captions such a subtotal with the rows
  it totals ("Properties under development + Completed properties for sale + …"), a caption it
  composed — and the mapper read "completed properties for sale" in it and put the whole of
  current assets, 13,991,585, into inventories beside the 40,904 printed
  (`row_reconstruct.COMPOSED_CAPTION_FLAG`).
* PETROCHINA 2024's "Fair value changes in equity investment measured at fair value through other
  comprehensive income" lost its line (-109 / 64 swept into other reserves) once its OCI block
  heading was read as a heading: the line's pattern required the heading glued on, and two
  internal FVTOCI note parts excluded only the glued form (the HK set).
* WALMART FY2025's debt note prints its issuances as "April 18, 2024  $750  April 15, 2027  Fixed
  3.900%  746": read as a figure, the principal broke the rows, and four issuances and two
  repayments left the note (`row_reconstruct._dollar_amount_in_a_text_row`).
* BLACKROCK 2025's notes were pruned by stray "citations" — a 10-K cites no notes on its faces —
  and its investments note went (`stages.prune_notes`).
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import row_reconstruct as R
from app.services.row_reconstruct import Word, build_line_items, us_10k_rows
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


def _row(y: float, cells: list[tuple[str, float]], h: float = 0.010) -> list[Word]:
    out = []
    for text, x0 in cells:
        x = x0
        for part in text.split(" "):
            w = 0.0055 * max(len(part), 1)
            out.append(Word(text=part, bbox=BBox(x0=x, y0=y, x1=x + w, y1=y + h)))
            x += w + 0.004
    return out


def _right(text: str, x1: float) -> tuple[str, float]:
    return text, x1 - 0.0055 * len(text)


@pytest.fixture(scope="module")
def hk_set():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


# ── Lai Sun: a caption the reader composed binds nothing ───────────────────────────────────────

def _lai_sun_current_assets() -> list[Word]:
    rows = [("Properties under development", "4,539,771", "4,794,588"),
            ("Completed properties for sale", "3,396,565", "3,728,569"),
            ("Inventories", "40,904", "47,131"),
            ("Cash and cash equivalents", "6,014,345", "6,257,469")]
    words = (_row(0.08, [("2025", 0.66), ("2024", 0.84)])
             + _row(0.10, [("CURRENT ASSETS", 0.03)]))
    y = 0.12
    for caption, a, b in rows:
        words += _row(y, [(caption, 0.05), _right(a, 0.72), _right(b, 0.90)])
        y += 0.015
    words += _row(y, [_right("13,991,585", 0.72), _right("14,827,757", 0.90)])
    words += _row(y + 0.015, [("Assets classified as held for sale", 0.05), _right("—", 0.72),
                              _right("113,744", 0.90)])
    words += _row(y + 0.03, [("Total current assets", 0.03), _right("13,991,585", 0.72),
                             _right("14,941,501", 0.90)])
    return words


def test_a_subtotal_captioned_with_its_rows_says_its_caption_was_composed():
    items, _ = build_line_items(_lai_sun_current_assets(), page_index=187, document_id="d",
                                source_kind="native", statement="balance_sheet")
    composed = [li for li in items if R.COMPOSED_CAPTION_FLAG in li.confidence.flags]
    assert [li.source_label for li in composed] == [(
        "Properties under development + Completed properties for sale + Inventories + "
        "Cash and cash equivalents")]
    assert {str(v.value) for v in composed[0].values.values()} == {"13991585", "14827757"}
    printed = [li for li in items if R.COMPOSED_CAPTION_FLAG not in li.confidence.flags]
    assert "Total current assets" in {li.source_label for li in printed}


def _face_row(caption: str, current: str, prior: str, *, composed: bool = False) -> LineItem:
    li = LineItem(source_label=caption, section_hint="CURRENT ASSETS",
                  role=LineRole.SUBTOTAL if composed else LineRole.LINE)
    for period, value in (("current", current), ("prior", prior)):
        li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                    value=Decimal(value), value_raw=Decimal(value),
                                    provenance=Provenance(page_index=187)))
    if composed:
        li.confidence.flags.append(R.COMPOSED_CAPTION_FLAG)
    return li


def test_the_mapper_binds_no_line_by_a_composed_caption(hk_set):
    from app.stages.map_ontology import MapOntologyStage

    rows = [_face_row("Completed properties for sale", "3396565", "3728569"),
            _face_row("Inventories", "40904", "47131"),
            _face_row("Properties under development + Completed properties for sale + Inventories",
                      "13991585", "14827757", composed=True)]
    for i, r in enumerate(rows):
        r.ordinal = i
    doc = DocumentModel(filename="lai_sun.pdf")
    doc.pages = [PageSource(index=187, kind=PageKind.FACE, statement="balance_sheet")]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(hk_set)
    ctx.line_items = hk_set
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    keys = {li.source_label[:20]: li.canonical_key for li in doc.line_items}
    assert keys["Inventories"] == "bs_ca__inventories"
    assert not (keys["Properties under dev"] or "").startswith("bs_ca__")


# ── PetroChina: the FVOCI movement with its block heading read as a heading ────────────────────

@pytest.fixture(scope="module")
def hk_matcher(hk_set):
    from app.services.mapping import OntologyMatcher

    return OntologyMatcher(build_working_view(hk_set), locale="en", settings=get_settings())


_FVOCI = "measured at fair value through other comprehensive income"


@pytest.mark.parametrize("caption", [
    f"Fair value changes in equity investment {_FVOCI}",
    f"Fair value changes in equity investments {_FVOCI}",
    f"Items that will not be reclassified to profit or loss Fair value changes in equity investment {_FVOCI}",
])
@pytest.mark.parametrize("section", [None, "Other comprehensive income"])
def test_the_fvoci_movement_reaches_its_line_with_or_without_its_heading(hk_matcher, caption,
                                                                         section):
    hit = hk_matcher.match(caption, statement="profit_and_loss", section=section)
    assert hit is not None and hit.canonical_key == "is_oci__unreal_gain_loss_investments", hit


def test_the_fvtoci_note_parts_still_take_their_own_captions(hk_matcher):
    hit = hk_matcher.match("Financial assets at fair value through other comprehensive income",
                           statement="profit_and_loss", section=None)
    assert hit is not None and hit.canonical_key == "sub__fa_cp_fvtoci_note_total"


# ── Walmart: a dollar amount in a row of terms ─────────────────────────────────────────────────

def _scan(cells):
    line = sorted(_row(0.1, cells), key=lambda w: w.bbox.x0)
    with us_10k_rows(True):
        label, _note, values = R._scan_row(line)
    return [w.text for w in label], [w.text for w in values]


def test_a_dollar_amount_followed_by_words_is_a_cell_of_text():
    label, values = _scan([("April 18,", 0.05), ("$750", 0.25), ("April 15,", 0.35),
                           ("Fixed", 0.55), ("3.900%", 0.65), _right("746", 0.90)])
    assert "$750" in label and values == ["746"]


def test_a_dollar_amount_followed_by_figures_only_is_a_figure():
    label, values = _scan([("Total assets", 0.03), _right("$169,998", 0.72),
                           _right("$138,615", 0.90)])
    assert label == ["Total", "assets"] and values == ["$169,998", "$138,615"]


# ── BlackRock: a 10-K's notes are not pruned by stray citations ────────────────────────────────

def _notes_doc() -> DocumentModel:
    doc = DocumentModel(filename="blk.pdf")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE), PageSource(index=1, kind=PageKind.NOTES)]
    li = LineItem(source_label="Equipment and computer software")
    li.note_number = "3"                                # a useful life, read as a note number
    li.values["k"] = ExtractedValue(period_label="current", basis=Basis.CONSOLIDATED, value=None,
                                    provenance=Provenance(source_kind="pdf", page_index=0))
    doc.line_items = [li]
    doc.notes = [NotesTable(note_number=n, title=f"Note {n}", source_pages=[1])
                 for n in ("3", "5", "7")]
    return doc


@pytest.mark.parametrize("ten_k,kept", [(True, ["3", "5", "7"]), (False, ["3"])])
def test_a_10k_keeps_its_notes_and_any_other_filing_is_pruned_as_before(ten_k, kept):
    from app.stages.prune_notes import PruneNotesStage

    doc = _notes_doc()
    ctx = PipelineContext()
    ctx.settings.extraction.prune_unreferenced_notes = True
    ctx.us_10k_filing = ten_k
    PruneNotesStage().run(doc, ctx)
    assert [n.note_number for n in doc.notes] == kept
