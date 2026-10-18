"""Three readings the reader series lost on the Ind AS test filings, measured against 1a340c4.

* f1 note 6 prints its other financial assets as a "Non-current (unsecured, considered good)" table
  and a "Current (unsecured, considered good)" table. Once note rows carried the sub-heading they
  are printed under, the current part's veto "^current" refused every row of its OWN block by that
  heading, and Other Fincl Assets (CP) read nothing against the 30.35 / 30.25 the face prints.
* f1's cash flow statement prints "Cash and cash equivalents at the end of the year" over
  "(refer note 11)  1,023.13  824.56". With the reference read whole, the figures were filed under
  "(refer note 11)" alone, and the residual swept them into other financing cash flows: financing
  activities -1,375.77 against the (2,398.90) the statement prints.
* f5 note 34 prints "Depreciation on property, plant and equipment (note 3)  1,348.62  1,192.45".
  The "3)" was read as the current-year figure, every column shifted one to the right, and the
  depreciation split of the face's 1,521.10 was declined.

The word boxes are the filings' own, normalised to the page.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services.mapping import normalize_label
from app.services.note_sourced import resolve, select_rows
from app.services.row_reconstruct import Word, build_line_items

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app/sample/templates/output_csv_indas_line_items.json")


def _words(rows):
    return [Word(text=t, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1)) for t, x0, x1, y0, y1 in rows]


def _figures(li):
    return {ev.period_label: str(ev.value) for ev in li.values.values()}


# ── a sub-heading naming the part's own side ─────────────────────────────────────────────────────

def _row(caption, current, prior, group):
    row = NoteItem(raw_label=caption, note_number="6", group_hint=group)
    for label, amount in (("current", current), ("prior", prior)):
        row.set_value(ExtractedValue(basis=Basis.STANDALONE, period_label=label,
                                     value=Decimal(amount), provenance=Provenance(page_index=8)))
    return row


def _note_6():
    nc, cur = "Non-current (unsecured, considered good)", "Current (unsecured, considered good)"
    first = NotesTable(note_number="6", title="Other financial assets", page_index=8)
    first.items.extend([_row("Security deposits", "358.20", "336.85", nc)])
    second = NotesTable(note_number="6", title="Other financial assets", page_index=8)
    second.items.extend([_row("Interest accrued on fixed deposits", "21.60", "17.85", cur),
                         _row("Insurance claims receivable", "8.75", "12.40", cur)])
    return [first, second]


def _sum(item, tables):
    got = resolve(select_rows(item, tables, {"current", "prior"}), "sum")
    return {period: str(amount) for (_b, period), (amount, _t) in got.items()}


def test_each_side_of_a_headed_note_reads_its_own_block():
    by_key = {i.key: i for i in load_line_item_set(
        json.loads(SEED.read_text(encoding="utf-8")), resolve=True).items}
    tables = _note_6()
    assert _sum(by_key["sub__fa_cp_other_fincl_assets_note_total"], tables) == {
        "current": "30.35", "prior": "30.25"}
    assert _sum(by_key["sub__ltp_other_fincl_assets_note_total"], tables) == {
        "current": "358.20", "prior": "336.85"}


# ── a caption whose last line is only a note reference ───────────────────────────────────────────

CASH_AT_END = [
    ("Cash", 0.071, 0.105, 0.8375, 0.8474), ("and", 0.109, 0.136, 0.8375, 0.8474),
    ("cash", 0.14, 0.172, 0.8375, 0.8474), ("equivalents", 0.177, 0.258, 0.8375, 0.8474),
    ("at", 0.262, 0.276, 0.8375, 0.8474), ("the", 0.281, 0.304, 0.8375, 0.8474),
    ("beginning", 0.308, 0.377, 0.8375, 0.8474), ("of", 0.382, 0.395, 0.8375, 0.8474),
    ("the", 0.4, 0.423, 0.8375, 0.8474), ("year", 0.427, 0.458, 0.8375, 0.8474),
    ("824.56", 0.666, 0.715, 0.8375, 0.8474), ("644.94", 0.881, 0.929, 0.8375, 0.8474),
    ("Cash", 0.071, 0.108, 0.8527, 0.8626), ("and", 0.113, 0.143, 0.8527, 0.8626),
    ("cash", 0.147, 0.183, 0.8527, 0.8626), ("equivalents", 0.188, 0.28, 0.8527, 0.8626),
    ("at", 0.285, 0.301, 0.8527, 0.8626), ("the", 0.306, 0.332, 0.8527, 0.8626),
    ("end", 0.337, 0.366, 0.8527, 0.8626), ("of", 0.371, 0.387, 0.8527, 0.8626),
    ("the", 0.391, 0.417, 0.8527, 0.8626), ("year", 0.422, 0.457, 0.8527, 0.8626),
    ("(refer", 0.071, 0.116, 0.8667, 0.8766), ("note", 0.12, 0.156, 0.8667, 0.8766),
    ("11)", 0.161, 0.187, 0.8667, 0.8766),
    ("1,023.13", 0.646, 0.715, 0.8667, 0.8766), ("824.56", 0.876, 0.929, 0.8667, 0.8766),
]


def test_a_reference_line_finishes_the_caption_printed_above_it():
    # The rulebook recognises the first line as a complete caption — which is what keeps it from
    # being folded forward as an unfinished head, and what left the reference line on its own.
    known = frozenset({normalize_label("Cash and cash equivalents at the end of the year")})
    items, _ = build_line_items(_words(CASH_AT_END), page_index=4, document_id="d",
                                source_kind="native", on_face=True, known_captions=known)
    rows = [(li.source_label, _figures(li)) for li in items if li.values]
    assert ("Cash and cash equivalents at the end of the year (refer note 11)",
            {"current": "1023.13", "prior": "824.56"}) in rows, rows
    assert not any(label.startswith("(refer") for label, _f in rows), rows


# ── a note reference bracketed in one token ──────────────────────────────────────────────────────

NOTE_34 = [
    ("34.", 0.071, 0.099, 0.8585, 0.8698), ("Depreciation", 0.104, 0.22, 0.8585, 0.8698),
    ("and", 0.225, 0.259, 0.8585, 0.8698), ("amortisation", 0.264, 0.379, 0.8585, 0.8698),
    ("expense", 0.385, 0.46, 0.8585, 0.8698),
    ("Particulars", 0.071, 0.153, 0.8753, 0.8848), ("Year", 0.512, 0.546, 0.8753, 0.8848),
    ("ended", 0.551, 0.598, 0.8753, 0.8848), ("31", 0.603, 0.621, 0.8753, 0.8848),
    ("March", 0.626, 0.673, 0.8753, 0.8848), ("2025", 0.677, 0.715, 0.8753, 0.8848),
    ("Year", 0.726, 0.761, 0.8753, 0.8848), ("ended", 0.766, 0.813, 0.8753, 0.8848),
    ("31", 0.817, 0.836, 0.8753, 0.8848), ("March", 0.841, 0.887, 0.8753, 0.8848),
    ("2024", 0.892, 0.929, 0.8753, 0.8848),
    ("Depreciation", 0.071, 0.157, 0.8919, 0.9015), ("on", 0.161, 0.178, 0.8919, 0.9015),
    ("property,", 0.182, 0.244, 0.8919, 0.9015), ("plant", 0.248, 0.283, 0.8919, 0.9015),
    ("and", 0.287, 0.312, 0.8919, 0.9015), ("equipment", 0.316, 0.389, 0.8919, 0.9015),
    ("(note", 0.393, 0.429, 0.8919, 0.9015), ("3)", 0.433, 0.447, 0.8919, 0.9015),
    ("1,348.62", 0.655, 0.715, 0.8919, 0.9015), ("1,192.45", 0.87, 0.929, 0.8919, 0.9015),
    ("Depreciation", 0.071, 0.157, 0.9068, 0.9163), ("on", 0.161, 0.178, 0.9068, 0.9163),
    ("right-of-use", 0.182, 0.26, 0.9068, 0.9163), ("assets", 0.264, 0.307, 0.9068, 0.9163),
    ("(note", 0.311, 0.347, 0.9068, 0.9163), ("4)", 0.351, 0.365, 0.9068, 0.9163),
    ("148.30", 0.668, 0.715, 0.9068, 0.9163), ("126.84", 0.882, 0.929, 0.9068, 0.9163),
]


def test_a_note_reference_in_one_bracketed_token_stays_in_the_caption():
    items, _ = build_line_items(_words(NOTE_34), page_index=14, document_id="d",
                                source_kind="native", on_face=False)
    rows = [(li.source_label, _figures(li)) for li in items if li.values]
    assert rows == [
        ("Depreciation on property, plant and equipment (note 3)",
         {"current": "1348.62", "prior": "1192.45"}),
        ("Depreciation on right-of-use assets (note 4)", {"current": "148.30", "prior": "126.84"}),
    ], rows


# ── a known heading over rows that open on a capitalised connective ──────────────────────────────

NOTE_22 = [
    ("Particulars", 0.071, 0.153, 0.4364, 0.4459), ("Year", 0.534, 0.569, 0.4364, 0.4459),
    ("ended", 0.573, 0.621, 0.4364, 0.4459), ("31st", 0.625, 0.658, 0.4364, 0.4459),
    ("March,", 0.663, 0.715, 0.4364, 0.4459), ("Year", 0.749, 0.784, 0.4364, 0.4459),
    ("ended", 0.788, 0.835, 0.4364, 0.4459), ("31st", 0.840, 0.873, 0.4364, 0.4459),
    ("March,", 0.878, 0.929, 0.4364, 0.4459),
    ("2017", 0.677, 0.715, 0.4495, 0.4590), ("2016", 0.892, 0.929, 0.4495, 0.4590),
    ("Interest", 0.071, 0.131, 0.4661, 0.4756), ("income", 0.135, 0.190, 0.4661, 0.4756),
    ("On", 0.091, 0.110, 0.4810, 0.4905), ("bank", 0.114, 0.147, 0.4810, 0.4905),
    ("deposits", 0.151, 0.208, 0.4810, 0.4905),
    ("18.65", 0.676, 0.715, 0.4810, 0.4905), ("15.20", 0.891, 0.929, 0.4810, 0.4905),
    ("On", 0.091, 0.110, 0.4958, 0.5053), ("electricity", 0.114, 0.181, 0.4958, 0.5053),
    ("and", 0.185, 0.210, 0.4958, 0.5053), ("other", 0.214, 0.250, 0.4958, 0.5053),
    ("deposits", 0.255, 0.311, 0.4958, 0.5053),
    ("4.12", 0.685, 0.715, 0.4958, 0.5053), ("3.86", 0.900, 0.929, 0.4958, 0.5053),
    ("Dividend", 0.071, 0.138, 0.5107, 0.5202), ("income", 0.142, 0.197, 0.5107, 0.5202),
    ("From", 0.091, 0.125, 0.5255, 0.5350), ("subsidiary", 0.130, 0.198, 0.5255, 0.5350),
    ("company", 0.203, 0.265, 0.5255, 0.5350),
    ("10.20", 0.676, 0.715, 0.5255, 0.5350), ("-", 0.925, 0.929, 0.5255, 0.5350),
    ("From", 0.091, 0.125, 0.5404, 0.5499), ("current", 0.130, 0.179, 0.5404, 0.5499),
    ("investments", 0.183, 0.266, 0.5404, 0.5499),
    ("2.35", 0.685, 0.715, 0.5404, 0.5499), ("1.84", 0.900, 0.929, 0.5404, 0.5499),
]


def test_a_known_heading_opens_its_block_over_rows_that_start_with_a_connective():
    known = frozenset({normalize_label("Dividend income")})
    items, _ = build_line_items(_words(NOTE_22), page_index=12, document_id="d",
                                source_kind="native", on_face=False, known_captions=known)
    groups = {li.source_label: li.group_hint.rsplit(" / ", 1)[-1] for li in items if li.values}
    assert groups["On bank deposits"] == "Interest income", groups
    assert groups["From subsidiary company"] == "Dividend income", groups
    assert groups["From current investments"] == "Dividend income", groups
