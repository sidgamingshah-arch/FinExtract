"""A TEMPLATE'S OWN WORKBOOK, FILLED IN — the output format of a template that ships one.

NEW FILE -> backend/app/services/export_workbook.py

ICON is the first: its output is the Company v3 workbook it was built from, stored as
``app/sample/workbooks/output_csv_icon_v1.xlsx`` with a map (``output_csv_icon_v1.json``, written by
``scripts/build_icon_pair.py``) of where each ICON line sits in it. The workbook is the bank's own
layout and this module does not reinterpret it: a copy is opened, its first three sheets — Profit
and Loss Statement, Balance Sheet, Additional Information — are filled, and everything else in it
(its other sheets, styling, sheet protection, drop-down lists, formulas) is left as it was.

WHAT IS WRITTEN

* THE HEADER BLOCK of the first sheet, which the other two read by formula: the borrower's name,
  Standalone or Consolidated, and per period column the year type, the statement date and the
  number of months; the currency and the denomination once, in the first period column, which the
  later columns copy by formula. Only values the workbook's own drop-down lists allow are written.
* EVERY INPUT ROW, by the UUID in column A: an unlocked cell with no formula, one value column per
  period, oldest first (the workbook's growth formula reads the column to the LEFT as the earlier
  year). The figure is the one the Workspace shows (`rollups.figures_as_shown`), in the chosen
  denomination. A line the template subtracts as a magnitude (excise duty, accumulated
  depreciation and amortisation, the equity dividend) is written as one, because the workbook's
  formulas subtract it as printed. A line ICON only derives (a heading over two parts such as
  "Investments (other than long term)") is not written: the workbook's totals add its parts, and a
  figure there would invite counting them twice.
* NOTHING INTO A FORMULA — with one exception. A total the filing prints WITHOUT its parts ("Cost
  of materials consumed" with no imported/indigenous split) has its printed figure in ICON and
  nothing for the workbook's formula to add up. That one cell is given the printed figure in place
  of its formula, with a comment saying so; where the workbook's totals bypass the row and add its
  parts instead (receivables, raw-material stock), the figure goes on the part the line's own
  definition names for that case (`printed_only_to` in the map).
* THE DIVIDEND RATE, which the workbook takes as an input, from the template's KPI of the same name.
  The workbook's other ratios are its own formulas and are left to compute.

The workbook is saved to recalculate in full when it is opened, so every total, ratio and the
balance-sheet check is the workbook's own arithmetic over the figures written. Of its thirty period
columns only the filing's own years are shown; the rest are hidden, not removed.
"""
from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path

WORKBOOKS = Path(__file__).resolve().parent.parent / "sample" / "workbooks"

# The workbook's DENOMINATION drop-down, keyed by the scale each one means. The export's `units`
# parameter speaks the other screens' words ("lakh", "crore", "absolute"); both are accepted.
_DENOMINATIONS = {1.0: "Actuals", 1e3: "Thousands", 1e5: "Lakhs", 1e6: "Millions", 1e7: "Crores"}
_TARGETS = {"actuals": 1.0, "absolute": 1.0, "units": 1.0, "thousand": 1e3, "thousands": 1e3,
            "lakh": 1e5, "lakhs": 1e5, "million": 1e6, "millions": 1e6, "crore": 1e7,
            "crores": 1e7}
_FINANCIAL_TYPE = {"standalone": "Standalone", "consolidated": "Consolidated"}
# The workbook's CURRENCY drop-down.
_CURRENCIES = set(("AED ARS AUD BDT BGN BHD BRL CAD CHF CNY DKK EUR GBP HKD HUF IDR ILS INR JMD JPY "
                   "KRW KWD LKR MMK MXN MYR NOK NPR NZD OMR PHP PLN QAR RUB SAR SEK SGD THB USD "
                   "ZAR").split())
# Oldest first: the workbook's growth formula compares each column with the one to its left.
_PERIODS = ("prior", "current")
_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
           "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
           "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
           "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\.?,?\s+(\d{{4}})\b", re.I)
_MONTH_DAY_YEAR = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_NUMERIC = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_PART_YEAR = (("nine months", 9), ("six months", 6), ("half year", 6), ("half-year", 6),
              ("three months", 3), ("quarter", 3))
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")

_PRINTED_TOTAL_NOTE = ("FinEx: the filing prints this total but not its parts, so its printed "
                       "figure is entered here in place of the formula.")
_PRINTED_PART_NOTE = ("FinEx: the filing prints only the total of this group; it is entered on "
                      "this row, the one the line's definition names when no split is printed.")
_PRIOR_DATE_NOTE = ("FinEx: taken as one year before the current year-end; the comparative "
                    "column's own date was not read from the filing.")


def workbook_layout(template_key: str | None) -> dict | None:
    """The map of a template's workbook, or None when the template ships none."""
    if not template_key or not _SAFE_KEY.match(template_key):
        return None
    path = WORKBOOKS / f"{template_key}.json"
    if not path.is_file():
        return None
    layout = json.loads(path.read_text(encoding="utf-8"))
    if not (WORKBOOKS / layout.get("workbook", "")).is_file():
        return None
    return layout


def has_workbook(template_def: dict | None) -> bool:
    return workbook_layout((template_def or {}).get("template_key")) is not None


def _basis_of(rows: list[dict], wanted: str | None) -> str:
    present = {str(v.get("basis") or "consolidated") for r in rows for v in (r.get("values") or [])}
    if wanted in present:
        return wanted
    return "standalone" if "standalone" in present else "consolidated"


def _dates_in(text: str) -> list[date]:
    out = []
    for d, m, y in _DAY_MONTH_YEAR.findall(text or ""):
        out.append((int(y), _MONTHS[m.lower()], int(d)))
    for m, d, y in _MONTH_DAY_YEAR.findall(text or ""):
        out.append((int(y), _MONTHS[m.lower()], int(d)))
    for d, m, y in _NUMERIC.findall(text or ""):
        out.append((int(y), int(m), int(d)))
    found = []
    for y, m, d in out:
        try:
            found.append(date(y, m, d))
        except ValueError:
            continue
    return found


def _year_before(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:                       # 29 February
        return d.replace(year=d.year - 1, day=28)


def _period_facts(rows: list[dict], basis: str, periods: list[str]) -> dict[str, dict]:
    """Per period: the statement date the filing printed (the most frequent one), and the months."""
    seen: dict[str, dict[date, int]] = {p: {} for p in periods}
    months: dict[str, int] = {p: 12 for p in periods}
    for r in rows:
        for v in (r.get("values") or []):
            p = str(v.get("period_label") or "")
            if p not in seen or str(v.get("basis") or "consolidated") != basis:
                continue
            text = str(v.get("period_display") or "")
            for d in _dates_in(text):
                seen[p][d] = seen[p].get(d, 0) + 1
            low = text.lower()
            for words, n in _PART_YEAR:
                if words in low:
                    months[p] = n
    out: dict[str, dict] = {}
    for p in periods:
        best = max(seen[p].items(), key=lambda kv: (kv[1], kv[0]))[0] if seen[p] else None
        out[p] = {"date": best, "months": months[p], "inferred": False}
    # THE COMPARATIVE'S OWN DATE. A filing's column headings are not always read per column, and a
    # comparative that comes back carrying the current year's date (or none) is the previous
    # financial year: Schedule III requires the immediately preceding reporting period.
    if "current" in out and "prior" in out and out["current"]["date"]:
        cur = out["current"]["date"]
        if out["prior"]["date"] in (None, cur) or out["prior"]["date"] > cur:
            out["prior"] = {**out["prior"], "date": _year_before(cur), "inferred": True}
    return out


def _rollups(template_def: dict) -> tuple[dict[str, list[str]], set[str]]:
    """``(each calculated line's children, the lines subtracted as a magnitude)``, from the
    template's own rollups (``children`` and ``cost_magnitude_children``)."""
    kids: dict[str, list[str]] = {}
    magnitudes: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            roll = node.get("rollup") or {}
            if node.get("canonical_key") and roll.get("children"):
                kids[str(node["canonical_key"])] = [str(c) for c in roll["children"]]
                magnitudes.update(str(c) for c in (roll.get("cost_magnitude_children") or ()))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(template_def)
    return kids, magnitudes


def _derived_only(line_item_set) -> set[str]:
    out: set[str] = set()
    for item in (getattr(line_item_set, "items", None) or ()):
        if str(getattr(item, "extraction_mode", "") or "") == "derive":
            out.add(item.key)
    return out


def _scale(source_units: dict | None, units: str | None) -> tuple[float, str | None]:
    """``(factor, denomination)``: the factor turns a figure as printed into the denomination."""
    try:
        src = float((source_units or {}).get("scale_factor") or 0) or None
    except (TypeError, ValueError):
        src = None
    target = _TARGETS.get(str(units or "").strip().lower()) if units else None
    if src is None:
        # The filing declared no scale: the figures are written as printed and no denomination is
        # claimed for them, because naming one would be a guess about every figure in the file.
        return 1.0, None
    if target is None:
        target = src
    return src / target, _DENOMINATIONS.get(target)


def build_template_workbook(rows: list[dict], template_def: dict, *, line_item_set=None,
                            basis: str | None = None, units: str | None = None,
                            source_units: dict | None = None, entity: str | None = None) -> bytes:
    """The template's workbook with this run's figures in it, as .xlsx bytes."""
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.utils import column_index_from_string

    from app.services.derived import compute_ratios
    from app.services.rollups import figures_as_shown

    layout = workbook_layout(template_def.get("template_key"))
    if layout is None:
        raise ValueError(f"template {template_def.get('template_key')!r} ships no workbook")
    book = openpyxl.load_workbook(WORKBOOKS / layout["workbook"])
    basis = _basis_of(rows, basis)
    present = {str(v.get("period_label") or "") for r in rows for v in (r.get("values") or [])
               if str(v.get("basis") or "consolidated") == basis}
    periods = [p for p in _PERIODS if p in present]
    factor, denomination = _scale(source_units, units)
    facts = _period_facts(rows, basis, periods)
    kids, magnitudes = _rollups(template_def)
    derived = _derived_only(line_item_set)
    shown = {p: figures_as_shown(template_def, rows, basis, p) for p in periods}
    ratios = {p: {r["key"]: r.get("value") for r in compute_ratios(rows, basis=basis, period=p,
                                                                   template_def=template_def)}
              for p in periods}

    head = layout["header"]
    hs = book[head["sheet"]]
    first = column_index_from_string(head["first_value_column"])
    hrow = head["rows"]

    def put_header(field: str, col: int, value, note: str | None = None) -> None:
        if field in hrow and value not in (None, ""):
            cell = hs.cell(hrow[field], col)
            cell.value = value
            if note:
                cell.comment = Comment(note, "FinEx")

    put_header("BORROWER_NAME", first, (entity or "").strip() or None)
    put_header("FINANCIAL_TYPE", first, _FINANCIAL_TYPE.get(basis))
    ccy = str((source_units or {}).get("currency") or "").strip().upper()
    if ccy in _CURRENCIES:
        put_header("CURRENCY", first, ccy)
    if "DENOMINATION" in hrow:
        # Written whether or not one is known: the template's own "Crores" would otherwise stand
        # over figures that are not in crores.
        hs.cell(hrow["DENOMINATION"], first).value = denomination
    for i, p in enumerate(periods):
        col = first + i
        put_header("YEAR_TYPE", col, "Audited")
        d = facts[p]["date"]
        if d is not None:
            put_header("STATEMENT_DATE", col, d, _PRIOR_DATE_NOTE if facts[p]["inferred"] else None)
            hs.cell(hrow["STATEMENT_DATE"], col).number_format = "yyyy-mm-dd"
        put_header("NO_OF_MONTHS", col, facts[p]["months"])

    rows_of = layout["rows"]
    printed_only_to = layout.get("printed_only_to") or {}
    placed: dict[tuple[str, str], float] = {}

    def value_of(key: str, p: str):
        v = shown[p].get(key)
        if v is None:
            return None
        v = abs(v) if key in magnitudes else v
        return round(v * factor, 6)

    def cell_of(key: str, p: str):
        spot = rows_of[key]
        return book[spot["sheet"]].cell(spot["row"], first + periods.index(p))

    def is_formula(cell) -> bool:
        return isinstance(cell.value, str) and cell.value.startswith("=")

    def is_input(cell) -> bool:
        # PER CELL, not per row: Surplus in P&L and accumulated depreciation and amortisation are
        # inputs in the first period column and ROLL FORWARD by formula in the later ones.
        return not is_formula(cell) and not cell.protection.locked

    comments: dict[tuple[str, str], str] = {}
    # Inputs first, so a printed-only total can find its part's slot still empty.
    for key in rows_of:
        if key.startswith("kpi:") or key in derived:
            continue
        for p in periods:
            v = value_of(key, p)
            if v is not None and is_input(cell_of(key, p)):
                placed[(key, p)] = v
    for key in rows_of:
        if key.startswith("kpi:") or key in derived:
            continue
        for p in periods:
            v = value_of(key, p)
            cell = cell_of(key, p)
            if not is_formula(cell):
                continue
            if v is None:
                if not kids.get(key) and all(value_of(key, q) is None for q in periods):
                    # A roll-forward on a line this filing carries no figure on: left in place it
                    # adds the period's movement to an empty balance — this year's retained profit
                    # a second time beside an "Other equity" that already holds it.
                    placed[(key, p)] = None
                    comments[(key, p)] = (f"FinEx: cleared; the filing carries no figure on this "
                                          f"line, so the roll-forward {cell.value} would add this "
                                          f"period's movement to an empty balance.")
                continue
            parts = kids.get(key)
            if not parts:
                # A ROLL-FORWARD ("=F65+'Profit and Loss Statement'!G96"): the workbook carries
                # Surplus in P&L and accumulated depreciation and amortisation forward from the
                # period before. Against ICON's conventions that double counts — a filing that
                # prints its reserves as one "Other equity" has them on Other Reserves, and a
                # filing that prints only net fixed assets has no accumulated depreciation — so
                # the balance the filing PRINTS goes in, and the formula is kept in the comment.
                placed[(key, p)] = v
                comments[(key, p)] = (f"FinEx: the filing's balance is entered in place of the "
                                      f"workbook's roll-forward {cell.value}")
                continue
            if any(shown[p].get(c) is not None for c in parts):
                continue
            to = printed_only_to.get(key)
            if to:
                if (to, p) not in placed and is_input(cell_of(to, p)):
                    placed[(to, p)] = v
                    comments[(to, p)] = _PRINTED_PART_NOTE
                continue
            placed[(key, p)] = v
            comments[(key, p)] = f"{_PRINTED_TOTAL_NOTE} Formula: {cell.value}"
    for (key, p), v in placed.items():
        cell_of(key, p).value = v
    for (key, p), note in comments.items():
        cell_of(key, p).comment = Comment(note, "FinEx")
    # THE KPIs THE WORKBOOK TAKES AS INPUTS (the dividend rate). Its other ratios are its own
    # formulas, and `is_input` leaves them to compute.
    for key in rows_of:
        if not key.startswith("kpi:"):
            continue
        kpi = key.split(":", 1)[1]
        for p in periods:
            v = ratios[p].get(kpi)
            cell = cell_of(key, p)
            if isinstance(v, (int, float)) and is_input(cell):
                cell.value = round(float(v), 6)

    # THE FILING'S YEARS ARE SHOWN, NOT THE TEMPLATE'S THIRTY. The workbook has a value column for
    # each of 30 periods; an annual report fills two, and the other 28 would show the template's
    # zeros, a TRUE balance check and the copied currency. They are hidden rather than removed:
    # their formulas and the workbook's layout stay as the bank designed them.
    if periods:
        last = first + int(head.get("periods") or 30) - 1
        for title in layout["sheets"]:
            _hide_columns(book[title], first + len(periods), last)

    book.calculation.fullCalcOnLoad = True
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _hide_columns(ws, start: int, end: int) -> None:
    """Hide columns ``start``..``end`` (1-based), splitting any width setting that spans the edge.

    A sheet stores widths as ranges — the template's period columns are one ``<col min=6 max=35>``
    — so hiding part of a range means cutting it into the part shown and the part hidden, each with
    the range's own width; two overlapping ranges are a file Excel offers to repair.
    """
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.dimensions import ColumnDimension

    if start > end:
        return
    dims = ws.column_dimensions
    covered = False
    for key, dim in list(dims.items()):
        lo, hi = dim.min or 0, dim.max or 0
        if hi < start or lo > end:
            continue
        covered = True
        pieces = [(lo, start - 1, dim.hidden), (max(lo, start), min(hi, end), True),
                  (end + 1, hi, dim.hidden)]
        del dims[key]
        for a, b, hidden in pieces:
            if a > b:
                continue
            dims[get_column_letter(a)] = ColumnDimension(
                ws, index=get_column_letter(a), width=dim.width, customWidth=dim.customWidth,
                hidden=hidden, min=a, max=b)
    if not covered:
        dims[get_column_letter(start)] = ColumnDimension(
            ws, index=get_column_letter(start), hidden=True, min=start, max=end)


def workbook_filename(document_name: str, template_def: dict) -> str:
    stem = re.sub(r"[^\w .()-]+", "_", document_name or "extract").strip() or "extract"
    label = re.sub(r"[^\w .()-]+", "_", str(template_def.get("name") or "workbook")).strip()
    return f"{stem} - {label}.xlsx"


__all__ = ["build_template_workbook", "has_workbook", "workbook_filename", "workbook_layout"]
