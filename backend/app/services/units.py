"""The unit a figure was printed in against the unit it is published or compared in.

`row_reconstruct` reads each page's unit off its own header band ("RMB'000", "人民幣百萬元") and
writes it onto every fact it reads there, so a note printed in millions beside a face printed in
thousands carries its own scale. Two places then move a note figure next to a face figure: the
note route publishes a note row on a face line (`stages.note_sourced`), and the note-to-face tie
compares a note's rows with the face figure they explain (`stages.reconcile`). Neither compared
units, so a millions note published under a thousands face was a thousandfold understatement, and
the tie graded the same gap `unconfirmed`, which says nothing.

A UNIT IS COMPARED ONLY WHERE BOTH SIDES DECLARE IT. An unresolved unit is `UnitContext()` — no
currency, scale 1, no label — and that means "nothing was read", not "units of one". So a side whose
scale was not read is never rescaled, and a side whose currency was not read is never refused: the
figure is treated as it always was.

A PER-SHARE AMOUNT AND A SHARE COUNT ARE NOT MONEY (`line_item.Measure`). The row reader gives
every row of a page its header's scale, so a 10-K's earnings per share arrived "in millions" and its
weighted-average shares "in millions" too, where the header says the first is dollars per share and
the second a count in thousands. `stamp_measures` restates the unit of every fact on a line that
declares what it measures (`LineItemDef.measure`), and `compare` and `describe` read the measure
before the scale: a monetary scale never converts a per-share amount or a share count.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import NamedTuple

MONEY, PER_SHARE, SHARES = "money", "per_share", "shares"
#: The scale of a per-share amount printed in cents ("RMB cents", "HK cents", 港仙).
CENT = Decimal("0.01")


def measure_of(unit) -> str:
    """What a unit measures — `"money"` for a unit that does not say (`line_item.Measure`)."""
    return str(getattr(unit, "measure", None) or MONEY)


class UnitCheck(NamedTuple):
    """What it takes to put a printed figure into the target's unit.

    `factor` multiplies the printed figure (1 when nothing changes). `rescaled` is
    ``(from_scale, to_scale)`` as written in the `unit_rescaled:` flag, or None. `currency_mismatch`
    is ``(printed_currency, target_currency)`` when both are declared and differ; the figure must
    then not be used at all, because no factor converts one currency into another.
    """
    factor: Decimal
    rescaled: tuple[str, str] | None
    currency_mismatch: tuple[str, str] | None


SAME = UnitCheck(Decimal(1), None, None)


def scale_declared(unit) -> bool:
    """Whether a scale was READ for this unit: a label from the header, or a factor other than 1."""
    if unit is None:
        return False
    return bool(getattr(unit, "units_label", None)) or _scale(unit) != 1


def currency_declared(unit) -> bool:
    return unit is not None and bool(getattr(unit, "currency", "") or "")


def scale_text(scale) -> str:
    """A scale as the flags write it: "1000", "1000000" — never "1E+6"."""
    return format(Decimal(str(scale)).normalize(), "f")


def compare(printed_in, target) -> UnitCheck:
    """How a figure printed in `printed_in` becomes a figure in `target`'s unit.

    A declared currency on both sides that differs is a mismatch, whatever the scales say. A
    declared scale on both sides that differs is a rescale by printed / target. Anything else is
    `SAME`.

    A SCALE CONVERTS ONLY BETWEEN FIGURES THAT MEASURE THE SAME THING. A per-share amount beside a
    money figure, or a share count beside either, has no factor into the other's unit — the
    "millions" of the statement it is printed in is not its scale — so the two are compared as
    printed, which is how they were compared before either side said what it measures. Two share
    counts in different share scales (thousands, units) are rescaled like any two figures.
    """
    if currency_declared(printed_in) and currency_declared(target):
        a, b = str(printed_in.currency), str(target.currency)
        if a.upper() != b.upper():
            return UnitCheck(Decimal(1), None, (a, b))
    if measure_of(printed_in) != measure_of(target):
        return SAME
    if scale_declared(printed_in) and scale_declared(target):
        source, dest = _scale(printed_in), _scale(target)
        if source != dest and source and dest:
            return UnitCheck(source / dest, (scale_text(source), scale_text(dest)), None)
    return SAME


def _scale(unit) -> Decimal:
    try:
        return Decimal(str(getattr(unit, "scale_factor", 1) or 1))
    except ArithmeticError:
        return Decimal(1)


# THE SCALE NAMES A READER WRITES, by factor. A factor not listed is written as its multiplier
# alone, which is still exact.
#: The scale word of a units line that states figures in single units — 单位：元. A scale of one,
#: READ rather than assumed, so it is said as such ("units (x1)") and not as an unread scale.
UNITS_WORD = "units"

_SCALE_NAMES = {Decimal(10000): "ten thousands", Decimal(1000): "thousands", Decimal(100000): "lakhs", Decimal(1000000): "millions",
                Decimal(10000000): "crores", Decimal(1000000000): "billions"}

NOT_STATED = "not stated"


def describe(unit, fallback=None) -> str:
    """The unit a figure is printed in, as a request states it: ``"INR lakhs (x100,000)"``.

    WHAT THE RUN KNOWS, AND NOTHING IT DOES NOT. Each half — currency and scale — is the figure's
    own where it was read (`currency_declared`, `scale_declared`), else `fallback`'s (the
    document's unit), and a half known on neither side is said to be unknown rather than guessed.
    An Indian face row is the case the fallback exists for: its "(₹ in lakhs)" line is page chrome,
    so the row carries no scale of its own while the document's unit says lakhs — see
    `stages.note_sourced._statement_unit`. ``"not stated"`` when neither half is known.

    A per-share amount is said in its currency per share (``"USD per share"``) and a share count
    as shares in their own scale (``"shares (thousands)"``) — never in the document's scale, which
    is money's (see `stamp_measures`).
    """
    currency = (str(unit.currency) if currency_declared(unit)
                else str(fallback.currency) if currency_declared(fallback) else "")
    measure = measure_of(unit)
    if measure == SHARES:
        return str(getattr(unit, "units_label", None) or "").strip() or SHARES_WORD
    if measure == PER_SHARE:
        what = str(getattr(unit, "units_label", None) or "").strip() or PER_SHARE_WORDS
        return f"{currency} {what}" if currency else f"{what}, currency not stated"
    scale = _scale_words(unit, fallback)
    if currency and scale:
        return f"{currency}{',' if scale.startswith('as printed') else ''} {scale}"
    if currency:
        return f"{currency}, scale not stated"
    if scale:
        return f"{scale}, currency not stated"
    return NOT_STATED


def _scale_words(unit, fallback) -> str:
    """The scale half of `describe`: a resolved factor first, the figure's own before the fallback.

    A LABEL WITH A FACTOR OF 1 IS A SCALE THE READER SAW AND DID NOT RESOLVE: the row reader keeps
    the printed signal ("₹inlakhs") when its scale vocabulary has no word for it, and leaves the
    factor at 1. Stating that as "x1" would be a guess the run never made, so a resolved factor —
    the document's, on f1 — wins over it, and with none the label is quoted as printed.
    """
    for u in (unit, fallback):
        if u is not None and _scale(u) != 1:
            factor = _scale(u)
            name = _SCALE_NAMES.get(factor)
            multiplier = (f"x{int(factor):,}" if factor == factor.to_integral_value()
                          else f"x{scale_text(factor)}")
            return f"{name} ({multiplier})" if name else f"({multiplier})"
    for u in (unit, fallback):
        if label := str(getattr(u, "units_label", None) or "").strip():
            return "units (x1)" if label == UNITS_WORD else f"as printed '{label}'"
    return ""


def row_unit(row, fallback=None) -> str:
    """`describe` for a row: the unit most of its figures carry (matrix figures included)."""
    from collections import Counter

    seen = Counter(describe(getattr(ev, "unit_ctx", None), fallback)
                   for ev in (getattr(row, "values", None) or {}).values()
                   if getattr(ev, "value", None) is not None)
    return seen.most_common(1)[0][0] if seen else describe(None, fallback)


def block_units(entries: list[dict], fallback=None) -> str:
    """The unit a block states, given its rows' `_unit` (popped here).

    ONE UNIT FOR THE BLOCK WHEN ITS ROWS AGREE. Where they do not, the block says so and each row
    keeps its own `unit` — a block-level unit that is true of only some rows would be wrong about
    the rest.
    """
    units = [e.pop("_unit", None) for e in entries]
    distinct = {u for u in units if u}
    # A STATEMENT STATES ITS UNIT ONCE, on the page that titles it. A CAS statement prints
    # 单位：元 under its title and nothing over its continuation pages, so the rows read there carry
    # no scale — and a block that is one statement said "varies by row" when nothing varied. A row
    # whose scale was not read does not contradict the rows whose scale was: where the rest agree,
    # theirs is the block's.
    stated = {u for u in distinct if not _scale_unread(u)}
    if len(stated) == 1:
        distinct = stated
        units = [u if u in stated else None for u in units]
    if len(distinct) <= 1:
        return next(iter(distinct)) if distinct else describe(None, fallback)
    for entry, unit in zip(entries, units):
        if unit:
            entry["unit"] = unit
    return "varies by row; see each row's unit"


def _scale_unread(described: str) -> bool:
    """Whether a `describe` string says no scale was read."""
    return described == NOT_STATED or described.endswith(", scale not stated")


# ── PER-SHARE AMOUNTS AND SHARE COUNTS ────────────────────────────────────────────────────────
#
# A 10-K's statement of operations prints, under net income, "Earnings per share: Basic $ 6.12"
# and "Shares used in computing earnings per share: Basic 15,343,783", under a header such as
# "(In millions, except number of shares, which are reflected in thousands, and per-share
# amounts)". The row reader resolves ONE scale per page (`row_reconstruct._statement_unit`, the
# statements' first), so both rows carried scale 1,000,000: an export in absolute units published
# EPS as 6,120,000 and the share count a thousand times too large, and a request told the model
# both figures were in millions of dollars.
#
# THE LINE SAYS WHAT IT MEASURES, NOT THE CAPTION. "Basic" is printed twice, once per block, and
# only the heading above it tells the two apart — so the measure is taken from the configured line
# the row was bound to (`LineItemDef.measure`), wherever the binding came from (a caption, the
# model's citation), and a set whose lines declare none is not touched at all.

#: How a share count and a per-share amount are said (`describe`).
SHARES_WORD = "shares"
PER_SHARE_WORDS = "per share"
CENTS_PER_SHARE_WORDS = "cents per share"
_SHARE_SCALE_NAMES = {Decimal(1_000): "thousands", Decimal(1_000_000): "millions",
                      Decimal(1_000_000_000): "billions"}
_SHARE_SCALE_OF = {"thousand": Decimal(1_000), "million": Decimal(1_000_000),
                   "billion": Decimal(1_000_000_000), "000": Decimal(1_000)}

# "per share", "per-share", "per common share" — taken out of a text before it is read for what it
# says about SHARES, so "except per share amounts" says nothing about share counts.
_PER_SHARE_PHRASE = re.compile(
    r"\bper[\s-]+(?:common\s+|ordinary\s+|basic\s+|diluted\s+)?shares?\b", re.IGNORECASE)
# A scale stated FOR SHARES: "number of shares, which are reflected in thousands", "shares in
# millions", "Weighted-average shares outstanding (in thousands)", "Shares (000s)". The scale has to
# follow "in" or open a parenthesis: "repurchased 10 million shares for $1 billion" states none.
_SHARES_IN_SCALE = re.compile(
    r"\bshares?\b[^.;()\n]{0,60}?(?:\bin\s+|\(\s*(?:in\s+)?)"
    r"(thousand|million|billion|['’]?000)(?:s|'s)?\b", re.IGNORECASE)
# A header that EXCEPTS share counts from its scale without stating theirs: "(In thousands,
# except share and per share data)" — the shares are printed in units.
_EXCEPT_SHARES = re.compile(r"\bexcept\b[^()\n]*?\bshares?\b", re.IGNORECASE)
_MONEY_SCALE_WORD = re.compile(r"\b(?:thousands?|millions?|billions?)\b|['’]000", re.IGNORECASE)
# A per-share amount printed in cents: "Basic (RMB cents)", "(HK cents)", 港仙, 人民幣分.
_CENTS = re.compile(r"\bcents?\b|港仙|[(（]\s*仙\s*[)）]|(?:人民幣|人民币|港幣|港币)\s*分",
                    re.IGNORECASE)


def _candidates(text: str) -> list[str]:
    """Each printed line, then each parenthesised clause of the text with its lines rejoined — a
    header the text layer broke across two lines is still one clause."""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    joined = " ".join(" ".join(lines).split())
    return lines + re.findall(r"\(([^()]{1,300})\)", joined)


def share_scale(text: str) -> Decimal | None:
    """The scale `text` states for SHARE COUNTS, or None when it states none.

    A scale named for the shares themselves wins ("shares in thousands" -> 1,000). A units
    declaration that excepts shares without naming their scale ("except share and per share
    data") states that they are printed in units -> 1. A header that excepts only per-share amounts
    ("In thousands, except per share amounts") says nothing about shares HERE: the caller takes the
    statement's own scale, which is what such a header gives every figure it does not except.
    """
    found = [_PER_SHARE_PHRASE.sub(" ", c) for c in _candidates(text)]
    for line in found:
        m = _SHARES_IN_SCALE.search(line)
        if m:
            return _SHARE_SCALE_OF.get(m.group(1).lower().lstrip("'’"))
    for line in found:
        if _MONEY_SCALE_WORD.search(line) and _EXCEPT_SHARES.search(line):
            return Decimal(1)
    return None


def shares_label(scale: Decimal | None) -> str:
    """"shares", "shares (thousands)" — or "shares (scale not stated)" when none was read."""
    if scale is None:
        return f"{SHARES_WORD} (scale not stated)"
    if scale == 1:
        return SHARES_WORD
    name = _SHARE_SCALE_NAMES.get(Decimal(scale)) or f"x{scale_text(scale)}"
    return f"{SHARES_WORD} ({name})"


def per_share_unit(printed, document=None, *, cents: bool = False):
    """The unit of a per-share amount printed under `printed`'s header: the currency the figure (or
    else the document) declares, per share, at a scale of one — or of one cent."""
    from app.core.models.line_item import UnitContext

    currency = (str(printed.currency) if currency_declared(printed)
                else str(document.currency) if currency_declared(document) else "")
    return UnitContext(currency=currency, scale_factor=CENT if cents else Decimal(1),
                       units_label=CENTS_PER_SHARE_WORDS if cents else PER_SHARE_WORDS,
                       source_bbox_page=getattr(printed, "source_bbox_page", None),
                       measure=PER_SHARE)


def shares_unit(printed, document=None, *, stated: Decimal | None = None):
    """The unit of a share count printed under `printed`'s header.

    The scale the filing states for shares (`share_scale`) where it states one; else the scale the
    statement's header gives every figure it does not except — the figure's own where it was read,
    the document's where only that was — and "scale not stated" where neither was read. No
    currency: a share is not an amount of money.
    """
    from app.core.models.line_item import UnitContext

    scale = stated
    if scale is None:
        scale = (_scale(printed) if scale_declared(printed)
                 else _scale(document) if scale_declared(document) else None)
    return UnitContext(currency="", scale_factor=scale if scale is not None else Decimal(1),
                       units_label=shares_label(scale),
                       source_bbox_page=getattr(printed, "source_bbox_page", None),
                       measure=SHARES)


#: The flag a restated fact carries, served among its unit flags (`routes.extractions`).
MEASURE_FLAG = "unit_measure"


def _row_texts(row) -> list[str]:
    """What the page printed about a row: its caption, the heading and banner it sat under, and the
    captions of the rows a cited figure was taken from (`derivation` inputs) — a row the model's
    answer created carries the line's label, and the printed caption only there."""
    out = [str(getattr(row, attr, "") or "") for attr in ("source_label", "group_hint",
                                                           "section_hint")]
    for record in (getattr(row, "derivation", None) or {}).values():
        for item in ((record.get("inputs") or []) if isinstance(record, dict) else ()):
            if isinstance(item, dict) and item.get("label"):
                out.append(str(item["label"]))
    return [t for t in out if t.strip()]


def _page_of(ev) -> int | None:
    page = getattr(getattr(ev, "provenance", None), "page_index", None)
    if page is None:
        page = getattr(getattr(ev, "unit_ctx", None), "source_bbox_page", None)
    return int(page) if page is not None else None


def _header_pages(doc, page: int) -> list[int]:
    """The page a figure was printed on, then up to two pages before it classified as the same
    statement — where a statement continued from, and printed its header."""
    stmt = {int(getattr(p, "index", -1)): str(getattr(p, "statement", "") or "")
            for p in (getattr(doc, "pages", None) or ())}
    own = stmt.get(page, "")
    before = [i for i in sorted(stmt) if i < page and own and stmt[i] == own][-2:]
    return [page, *reversed(before)]


def _page_texts(raw: bytes | None, fmt: str, wanted: set[int]) -> dict[int, str]:
    """Plain text of the `wanted` pages (or sheets), read once. {} when nothing can be read: a
    scanned page has no text layer, and the statement's own scale then stands for its shares."""
    if not raw or not wanted:
        return {}
    try:
        if fmt == "pdf":
            import fitz

            pdf = fitz.open(stream=raw, filetype="pdf")
            return {i: pdf[i].get_text("text") or "" for i in sorted(wanted)
                    if 0 <= i < pdf.page_count}
        from app.services.derived import document_text

        return {i: t for i, t in document_text(raw, fmt) if i in wanted}
    except Exception:  # noqa: BLE001 - an unreadable page leaves the statement's scale standing
        return {}


def stamp_measures(doc, line_items, *, raw: bytes | None = None, log=None,
                   cited: dict[str, list[str]] | None = None) -> int:
    """Restate the unit of every figure on a line that measures per-share amounts or share counts.

    For each row bound to a line declaring `measure: per_share` or `shares`, every figure not
    already in that measure gets the unit `per_share_unit` / `shares_unit` gives it, and the flag
    `unit_measure:<measure>`. THE FIGURE IS NOT TOUCHED: it is the number the page printed, and
    only what it is said to be in changes. Idempotent — a figure already in its line's measure is
    left alone — so it runs after each route that can put a figure on a line (`NormalizeStage`
    for the caption match, `LineItemLlmStage` and `AssembleComponentsStage` after the model's
    citations).

    `cited` is ``{printed row id: [the lines whose answer cited it]}`` (`LineItemLlmStage`'s face
    claims). A bare "Basic" the model placed on a per-share line stays a printed row of its own,
    bound to no line, and its figure is the same per-share amount: a row every citing line of which
    declares one measure is restated in it too, so no export converts the printed copy as money.

    Returns how many figures were restated; 0, and nothing read, when no line declares a measure.
    """
    declared = {str(i.key): str(getattr(i, "measure", MONEY) or MONEY)
                for i in (getattr(line_items, "items", None) or ())}
    declared = {k: m for k, m in declared.items() if m != MONEY}
    if not declared:
        return 0
    by_citation: dict[str, str] = {}
    for rid, keys in (cited or {}).items():
        claimed = {declared.get(str(k), MONEY) for k in keys or ()}
        if len(claimed) == 1 and MONEY not in claimed:
            by_citation[str(rid)] = next(iter(claimed))

    def measure_of_row(row) -> str | None:
        return (declared.get(str(row.canonical_key or ""))
                or by_citation.get(str(getattr(row, "id", "") or "")))

    todo = [(row, ev, measure_of_row(row)) for row in (getattr(doc, "line_items", None) or ())
            if measure_of_row(row)
            for ev in (row.values or {}).values()
            if getattr(ev, "value_text", None) is None
            and measure_of(ev.unit_ctx) != measure_of_row(row)]
    if not todo:
        return 0
    document = getattr(doc, "unit_context", None)
    wanted = {p for _row, ev, measure in todo if measure == SHARES
              and _page_of(ev) is not None for p in _header_pages(doc, _page_of(ev))}
    fmt = str(getattr(getattr(doc, "fmt", None), "value", getattr(doc, "fmt", "")) or "")
    texts = _page_texts(raw, fmt, wanted)
    changed: dict[str, tuple[int, str, str]] = {}
    for row, ev, measure in todo:
        own = _row_texts(row)
        before = describe(ev.unit_ctx, document)
        if measure == PER_SHARE:
            unit = per_share_unit(ev.unit_ctx, document,
                                  cents=any(_CENTS.search(t) for t in own))
        else:
            stated = next((s for s in map(share_scale, own) if s is not None), None)
            page = _page_of(ev)
            for p in ([] if page is None else _header_pages(doc, page)):
                if stated is not None:
                    break
                stated = share_scale(texts.get(p, ""))
            unit = shares_unit(ev.unit_ctx, document, stated=stated)
        ev.unit_ctx = unit
        flag = f"{MEASURE_FLAG}:{measure}"
        if flag not in ev.confidence.flags:
            ev.confidence.flags.append(flag)
        name = (str(row.canonical_key) if str(row.canonical_key or "") in declared
                else f"{row.source_label!r} (cited)")
        n, was, _now = changed.get(name, (0, before, ""))
        changed[name] = (n + 1, was, describe(unit, document))
    if log:
        for key, (n, was, now) in sorted(changed.items()):
            log(f"units:measure:{key}: {n} figure(s) stated in {now} (read as {was})")
    return sum(n for n, _w, _n in changed.values())
