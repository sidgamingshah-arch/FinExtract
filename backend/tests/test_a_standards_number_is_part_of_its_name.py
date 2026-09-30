"""Two ways a note table's text was mis-read, both measured on 嘉民 (kaming) note 7, revenue.

1. "Revenue from contract with customers within the scope of HKFRS 15" is a HEADING with no
   figures. `_scan_row` reads a number as a figure, so its 15 became the row's current amount, and
   the model, asked for total revenue, cited it: the run published a revenue of 15 against a
   printed 868,375. A number right after a standard's name is part of the caption.
2. (NOT FIXED HERE, recorded so it is not forgotten.) "…is analysed as follows:" — the last line
   of the sentence introducing the table ends in a colon and is read as the table's sub-heading,
   so every row carries the group "follows:" and the uncaptioned total is captioned "follows".
   Refusing that line as a heading was tried: it also closed the block the uncaptioned total is
   promoted from, and the total row was dropped. The figures are right; only the caption is wrong.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _scan_row


def _words(*texts: str) -> list[Word]:
    # Page-relative coordinates, as the extractor produces them.
    out, x = [], 0.05
    for t in texts:
        w = 0.008 * len(t)
        out.append(Word(text=t, bbox=BBox(x0=x, y0=0.40, x1=x + w, y1=0.41)))
        x += w + 0.01
    return out


def _split(*texts: str):
    label, _note, values = _scan_row(_words(*texts))
    return " ".join(w.text for w in label), [w.text for w in values]


def test_a_standards_number_stays_in_the_caption():
    assert _split("within", "the", "scope", "of", "HKFRS", "15") == (
        "within the scope of HKFRS 15", [])
    assert _split("Leases", "under", "IFRS", "16") == ("Leases under IFRS 16", [])
    assert _split("Adoption", "of", "Ind", "AS", "115") == ("Adoption of Ind AS 115", [])


def test_a_figure_after_the_caption_is_still_a_figure():
    # The same shape with a real amount, and a standard's number followed by the row's figures.
    assert _split("Sales", "of", "properties", "599,703", "850,058") == (
        "Sales of properties", ["599,703", "850,058"])
    assert _split("Lease", "liabilities", "under", "HKFRS", "16", "1,234", "5,678") == (
        "Lease liabilities under HKFRS 16", ["1,234", "5,678"])
    # An English "as" is not a standard: "such as 15" keeps its 15 as a value.
    assert _split("items", "such", "as", "15")[1] == ["15"]
