"""THE PRINTED STATEMENT, AS CONTEXT FOR A LINE READ OFF IT — the face's answer to `note_context`.

NEW FILE -> backend/app/services/face_context.py

WHY IT EXISTS. `services.note_context.identified_notes` supplies the notes a line's configuration
selected, and a request carrying it can be answered. A line read off the FACE selects no note, so
`note_sets` produces no entry for it and the request it gets carries an EMPTY note block — and the
reply contract's own advice to a model in that position is to answer with an empty `sources`.
Measured on the shipped set, 343 of the 506 asked-about lines are in exactly that position. There
was nothing in the request to locate the figure in, so this is the block that gives a face line
something to answer from.

KEYED BY STATEMENT, NOT BY SECTION, AND THAT IS MEASURED RATHER THAN CONVENIENT. Grouping the rows
by their printed banner was the obvious shape and it is the wrong one. Run over 000709's 255 face
rows:

  * THE STATEMENT RESOLVES FOR ALL OF THEM — 0 rows with no statement — because it comes off the
    page the row was read from, which the classifier settled.
  * THE BANNER RESOLVES FOR 163, so section-keying would silently DROP 92 rows, a third of the
    statement, from the context of the very lines that need it.
  * AND WHERE IT DOES RESOLVE IT IS SOMETIMES WRONG: 12 changes-in-equity rows came back labelled
    `cash_flow_from_financing_activities`, 10 profit-and-loss rows as `equity`, 8 more as
    `non_current_liabilities`. `services.note_sections`' docstring already records why — on a page
    full of headings the nearest preceding banner is whatever the typesetter printed last — and a
    section block assembled on that signal would hand a balance-sheet line a block of mislabelled
    income-statement rows.

So the rows are supplied AS THE FILING PRINTS THEM, in page and print order, for the statement the
line is gated to. The banner travels as a LABEL on each row (`under`) rather than as a filter: it
is what the page said, the model may weigh it, and a wrong one costs a hint rather than a row.

EVERY ROW, NOT ONLY THE UNCLAIMED ONES. A row already matched to a line says so (`line`), and that
is the point rather than redundancy: it is the deterministic route's proposal, which is what makes
a request answerable as "confirm or correct this" instead of "find this from nothing". It is also
the context that makes a caption legible at all — that "Buildings" sits between "Land" and "Plant
and machinery" is most of what identifies it.

ONCE PER REQUEST, NOT ONCE PER LINE, for the reason `identified_notes` gives at length: a block
attached to each of a request's line items is the same block multiplied by the request's size, and
at 88 lines on one statement that fails the provider outright rather than merely costing more.
"""
from __future__ import annotations

import re

from app.services import units
from app.services.gap_explanation import _BOILERPLATE
from app.services.mapping import normalize_statement


def _figures(row) -> dict[str, str]:
    """The row's figures by period label.

    `column_index` values are skipped, the same rule `note_context` applies: those are the columns
    of a matrix page (a changes-in-equity grid), where the columns are equity components rather
    than periods, so their labels are not periods and would read as invented ones.
    """
    out: dict[str, str] = {}
    for ev in (getattr(row, "values", None) or {}).values():
        if getattr(ev, "column_index", None) is not None:
            continue
        if getattr(ev, "value", None) is None:
            continue
        out[str(getattr(ev, "period_label", "") or "?")] = printed_figure(ev)
    return out


def printed_figure(ev) -> str:
    """The figure AS THE PAGE PRINTS IT: `value_raw`, not the sign-normalised `value`.

    A statement block is the statement as printed, and the engine's sign convention is not what
    was printed. 688008's consolidated income statement prints 财务费用 as -240,504,105.54 (net
    interest income) and 其中：利息费用 as 1,512,598.38; the block sent them as 240504105.54 and
    -1512598.38, the opposite of the page — so a model reading the block saw an expense line the
    filing reports as income, and a figure it could not find on the page it was told to read.
    `value` stays the fallback for a fact whose raw figure was never kept.
    """
    raw = getattr(ev, "value_raw", None)
    return str(raw if raw is not None else ev.value)


def _statement_of(row, stmt_of_page: dict, page) -> str:
    """The statement a face row was PRINTED on: the stamp `pdf_extract` puts on a row read from
    one side of a page that holds two statements (`buckets.PRINTED_ON_FLAG`), else its page's.

    A page holds two statements where one ends part way down and the next begins, and where a
    2-up spread prints a balance sheet beside a statement of profit and loss — Asian Paints' PDF
    page 184. Looked up by page alone, the balance sheet's rows were sent in the profit-and-loss
    block and the balance-sheet requests had no standalone block at all."""
    from app.services.buckets import PRINTED_ON_FLAG

    for flag in (getattr(getattr(row, "confidence", None), "flags", None) or ()):
        if str(flag).startswith(f"{PRINTED_ON_FLAG}:"):
            return normalize_statement(str(flag).split(":", 1)[1])
    return stmt_of_page.get(page if page is not None else -1, "")


def _page_of(row) -> int | None:
    """The page this row was read from, off its first provenanced figure.

    `LineItem.statement_id` is the field this ought to read and it is written by nothing — grep
    finds no producer and no other consumer — so the page's own classification is the only
    statement a face row actually has.
    """
    for ev in (getattr(row, "values", None) or {}).values():
        prov = getattr(ev, "provenance", None)
        page = getattr(prov, "page_index", None) if prov is not None else None
        if page is not None:
            return int(page)
    return None


def face_rows(doc, statements) -> list[dict]:
    """The printed rows of each named statement, in page and print order. One entry per statement.

    `statements` is the set of statement tokens a request's lines are gated to — `RequestPlan.
    sections` carries the `(statement, section)` pairs and only the statement half selects here,
    for the reason in the module docstring.

    A row with no figure at all is omitted: it is a heading or a spacer, and a caption with nothing
    beside it cannot be the answer to "where is this line's figure printed".
    """
    # BOTH SIDES THROUGH THE FOLD, because one statement has two spellings and this join is where
    # that bites. `StatementType` — what a line DECLARES — spells it `equity_changes`; the page
    # classifier and every downstream caller say `changes_in_equity`. So a line gated to the
    # statement of changes in equity asked for `equity_changes`, no page carried that, and the
    # block came back EMPTY: the model was shown nothing for the one statement whose rows it most
    # needs supplied, and the failure looked like "this filing has no such page".
    #
    # `mapping.normalize_statement` is the designated owner of the fold — see `_STATEMENT_SPELLINGS`
    # there, whose own comment says a declaration the gate cannot compare to the classifier's
    # verdict scopes nothing. The other three statements spell the same either way, so this is a
    # no-op for them and the equity statement is the whole of its effect.
    want = {normalize_statement(str(s)) for s in (statements or ()) if str(s)}
    want.discard("")
    if not want:
        return []
    stmt_of_page = {int(getattr(p, "index", -1)):
                    normalize_statement(str(getattr(p, "statement", "") or ""))
                    for p in (getattr(doc, "pages", None) or ())}
    notes_pages = _notes_pages(doc)
    supplement = supplement_pages(doc)
    framework_of = {int(getattr(p, "index", -1)): str((getattr(p, "evidence", None) or {})
                                                       .get("framework") or "")
                    for p in (getattr(doc, "pages", None) or ())}
    chrome = set(getattr(doc, "page_chrome", None) or ())
    doc_unit = getattr(doc, "unit_context", None)

    # ONE BLOCK PER STATEMENT AND ENTITY. A mainland filing prints the group's statements and the
    # parent company's own, and the two blocks were one: 000709's 货币资金 arrived twice — 31.8bn
    # and 24.5bn — with nothing to say which was the group's. And an HKEX table printing Group and
    # Company side by side was ONE row whose two `current` figures shared a key, so the company's
    # overwrote the group's. Each figure now goes under its own entity.
    by_block: dict[tuple[str, str, str, str], list[tuple[int, dict]]] = {}
    legends: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in (getattr(doc, "line_items", None) or ()):
        page = _page_of(row)
        statement = _statement_of(row, stmt_of_page, page)
        if statement not in want:
            continue
        if not _shown_on_face(row, page, notes_pages):
            continue
        caption = getattr(row, "source_label", "") or ""
        unit = units.row_unit(row, doc_unit)
        for entity, (figures, legend) in _figures_by_entity(row).items():
            entry: dict = {"caption": caption, "figures": figures, "_unit": unit}
            banner = (getattr(row, "section_hint", None) or "").strip()
            if banner and not is_identity_line(banner, chrome):
                entry["under"] = banner
            if sub := (getattr(row, "group_hint", "") or "").strip():
                entry["subheading"] = sub
            # THE DETERMINISTIC ROUTE'S PROPOSAL. Omitted for a row the mapper could not place —
            # `stages.face_mapping_contract` keys those `engine_unclassified_face__…`, which names
            # no line and would read as one.
            key = str(getattr(row, "canonical_key", "") or "")
            if key and not key.startswith("engine_unclassified"):
                entry["line"] = key
            # THE CASH-FLOW SUPPLEMENT IS ITS OWN BLOCK. A mainland filing prints the indirect
            # reconciliation (现金流量表补充资料) in the notes, and the reader reads it as the cash
            # flow's — rightly, it is where the indirect lines' figures are. But sent inside the
            # cash-flow block it read as the statement's own continuation: 净利润, 固定资产折旧,
            # 财务费用 and 经营性应收项目的减少 appended to the face after 期末现金及现金等价物余额.
            part = supplement.get(page, "") if statement == "cash_flow" else ""
            # ONE BLOCK PER STATEMENT SET, too: see `stages.classify.statement_frameworks`.
            block_key = (statement, entity, part, framework_of.get(page, "") if page is not None
                         else "")
            by_block.setdefault(block_key, []).append(
                (page if page is not None else 0, entry))
            seen = legends.setdefault(block_key, {})
            for label, heading in legend.items():
                seen.setdefault(label, heading)

    # THE SET THIS TEMPLATE READS, when the filing prints two. The HK template is built on
    # HKFRS / IFRS, and an H-share filer's IFRS set is the one its lines are answered from: its
    # blocks come first and keep the reader's proposals. The other set (CAS) is KEPT APART — its
    # own block after them, named by its standards, with no row proposed for a line, and saying
    # it is there only for a figure the IFRS statements do not print on their face (PetroChina's
    # CAS balance sheet prints short-term borrowings without the current portion of long-term
    # borrowings, which its IFRS face includes and its IFRS note 29 splits out).
    frameworks = {k[3] for k in by_block if k[3]}
    read = (PREFERRED_FRAMEWORK if PREFERRED_FRAMEWORK in frameworks
            else sorted(frameworks)[0]) if len(frameworks) > 1 else None
    out: list[dict] = []
    for key in sorted(by_block, key=lambda k: (k[0], bool(read and k[3] and k[3] != read),
                                               _ENTITY_ORDER.get(k[1], 9), k[2], k[3])):
        statement, entity, part, framework = key
        rows = [e for _p, e in sorted(by_block[key], key=lambda pe: pe[0])]
        apart = bool(read and framework and framework != read)
        if apart:
            rows = [{k: v for k, v in e.items() if k != "line"} for e in rows]
        block: dict = {"statement": statement, "entity": entity}
        if framework and read:
            block["set"] = framework
        if apart:
            pages = sorted({p for p, _e in by_block[key]})
            block["kept_apart"] = (f"the {framework} statements (pages {pages[0] + 1}-"
                                   f"{pages[-1] + 1}), not the {read} set this template reads: "
                                   f"answer from the {read} block, and cite this block only for "
                                   f"a figure the {read} statements do not print")
        if part:
            block["part"] = "supplement"
            block["title"] = part
            block["printed_in"] = "notes"
        if legends.get(key):
            # The printed heading of each figure key — the period's date, as the page states it.
            block["columns"] = legends[key]
        # THE UNIT THE FIGURES ARE IN, as the run knows it — see `units.describe`.
        block["unit"] = units.block_units(rows, doc_unit)
        block["rows"] = rows
        out.append(block)
    return out


_ENTITY_ORDER = {"consolidated": 0, "standalone": 1}
# The standards the HK template is built on — see `face_rows`.
PREFERRED_FRAMEWORK = "IFRS"


# The supplement's own title. The classifier marks the supplement only where it also finds the
# reconciliation's closing row; 000685 titles it "79、现金流量表补充资料" and the mark was not set, so
# its standalone cash-flow block ran on into 现金的期末余额 and the foreign-currency rows after it.
_SUPPLEMENT_TITLE = re.compile(r"现金流量表补充资料|現金流量表補充資料")


def supplement_pages(doc) -> dict[int, str]:
    """``{page index: printed title}`` for every page of a cash-flow supplement printed in the notes.

    The classifier marks the page that TITLES the supplement (`evidence["supplement"]`, see
    `stages.classify._cf_supplement_extent`); the pages after it that it continues onto are the
    face pages of the same statement up to the next titled page.
    """
    from app.core.models.enums import PageKind

    out: dict[int, str] = {}
    title = ""
    last = None
    for p in sorted(getattr(doc, "pages", None) or (), key=lambda p: int(getattr(p, "index", -1))):
        ev = getattr(p, "evidence", None) or {}
        index = int(getattr(p, "index", -1))
        face = getattr(p, "kind", None) is PageKind.FACE
        printed = str(ev.get("matched_title") or "")
        if face and (ev.get("supplement") or _SUPPLEMENT_TITLE.search(printed)):
            title = printed or "现金流量表补充资料"
        elif not face or ev.get("matched_title") or last is None or index != last + 1:
            title = ""
        if title:
            out[index] = title
        last = index
    return out

# A COMPANY IDENTITY LINE — the CIN, the registered office, the name banner, "Annual Report
# 2024-25". It is printed at the top of every statement page, so the nearest-banner rule made it
# the `under` of every row above the first real heading: f1's whole standalone profit and loss
# arrived "under" its CIN. It names the company, not a section.
_IDENTITY_LINE = re.compile(
    r"(?i:^\W*CIN\b|\bregistered\s+office\b|\bannual\s+report\b)"
    r"|^[A-Z0-9&.,'()\s-]*\b(?:LIMITED|LTD\.?|PLC|INC\.?)\W*$")


def is_identity_line(text: str, chrome=()) -> bool:
    """Whether `text` is a company identity line or a line of the page template.

    `chrome` is `DocumentModel.page_chrome`, the lines the reader found printed at the head or
    foot of page after page; a line the filing repeats that way is furniture whatever it says.
    """
    from app.services.pdf_extract import _chrome_key

    t = (text or "").strip()
    return bool(t) and (bool(_IDENTITY_LINE.search(t)) or _chrome_key(t) in set(chrome or ()))


# THE SECOND LINE OF A TWO-LINE COLUMN HEADING. A face prints "Note | Year ended 31 March | Year
# ended 31 March" over "No. | 2025 | 2024", and the reader took the lower line for a row: f1's
# standalone profit and loss opened with a row "No." whose figures were 2025 and 2024. The caption
# is a heading word of the note-reference or particulars column, and every figure is a year — the
# two together, because neither alone says the row is a heading.
_HEADING_CAPTION = re.compile(
    r"(?i)^\W*(?:note\s*no|note|notes|no|sr\.?\s*no|s\.?\s*no|particulars|year|years)\W*"
    r"(?P<unit>(?:(?:hk|us|s|nt)?\$|rmb|hkd|usd|人民[币幣]|港[币幣元])\s*[’'`]?\s*(?:0{3})?\W*)?$")
_YEAR = re.compile(r"^(?:19|20)\d\d$")
# THE "'000" OF A UNITS HEADING, read as a figure. 0160 prints its column heading as "Notes HK$'000
# HK$'000", the reader split "HK$'" from "000", and every balance-sheet and cash-flow block of the
# filing opened with a row "Notes HK$'" whose current figure was 0.
_UNIT_ZEROS = re.compile(r"^0+(?:\.0+)?$")


def is_column_heading_row(row) -> bool:
    """Whether `row` is a line of the statement's column heading read as though it were a row.

    The caption is a heading word of the note-reference or particulars column, and every figure is
    a year — or, where the caption carries the currency of a units heading, the "000" that heading
    printed after it.
    """
    m = _HEADING_CAPTION.match(getattr(row, "source_label", "") or "")
    if not m:
        return False
    figures = [str(getattr(ev, "value", "")).strip()
               for ev in (getattr(row, "values", None) or {}).values()
               if getattr(ev, "value", None) is not None]
    return bool(figures) and all(_YEAR.match(f) or (m.group("unit") and _UNIT_ZEROS.match(f))
                                 for f in figures)


def _shown_on_face(row, page, notes_pages) -> bool:
    """Whether a statement block may carry `row` — the one rule `face_rows` and `face_index` share.

    WHAT THE RUN ITSELF MARKS AS A NOTE STAYS OUT of a statement block: a row read off a page the
    classifier called notes, or one `buckets` placed as printed in the notes. The statement block
    is that statement as printed, and a note row in it reads as a line of the face. (A notes page
    the classifier read AS a statement is not second-guessed here.)

    THE STATEMENT'S FOOTER AND SIGNATURE BLOCK — "The accompanying notes …", "As per our report of
    even date", the signatories, place and date — is not a row of the statement. The pattern is
    `gap_explanation`'s, which already refuses these as missing lines.

    A LINE OF THE COLUMN HEADING read as a row is not one either (`is_column_heading_row`).
    """
    if page in notes_pages or _printed_in_notes(row):
        return False
    if _BOILERPLATE.search(getattr(row, "source_label", "") or ""):
        return False
    return not is_column_heading_row(row) and not is_signature_date_row(row)


# THE DATE OF SIGNING, read as a row. A statement's signature block ends "Mumbai | Mumbai | 29th May
# | 2026": the place under each signatory and the date, whose year stands in a figure column. Asian
# Paints' balance sheet sent it as a row "Mumbai Mumbai 29th May" with the figure 2026. A row is
# one when its caption ENDS on a month (and its day) and its every figure is a year — the day may
# have been read off as a figure of its own.
_MONTH_WORD = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
               r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DAY_MONTH_END = re.compile(rf"(?i)\b(?:{_MONTH_WORD}|{_MONTH_WORD}[\s,]+\d{{1,2}}(?:st|nd|rd|th)?)\W*$")


def is_signature_date_row(row) -> bool:
    """Whether `row` is the signature block's place-and-date line read as though it were a row."""
    if not _DAY_MONTH_END.search(getattr(row, "source_label", "") or ""):
        return False
    figures = [str(getattr(ev, "value", "")).strip()
               for ev in (getattr(row, "values", None) or {}).values()
               if getattr(ev, "value", None) is not None]
    return bool(figures) and all(_YEAR.match(f) for f in figures)


def _notes_pages(doc) -> set[int]:
    from app.core.models.enums import PageKind

    return {int(getattr(p, "index", -1)) for p in (getattr(doc, "pages", None) or ())
            if getattr(p, "kind", None) is PageKind.NOTES}


def _printed_in_notes(row) -> bool:
    return str(getattr(getattr(row, "printed_in", None), "value",
                       getattr(row, "printed_in", None)) or "").lower() == "notes"


def _figures_by_entity(row) -> dict[str, tuple[dict[str, str], dict[str, str]]]:
    """``{entity: (figures by period label, printed heading by period label)}`` for one row.

    The heading is the column's own (`ExtractedValue.column_heading`), else the date the page
    prints over it (`period_display`). Matrix values are skipped, as in :func:`_figures`.
    """
    out: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
    for ev in (getattr(row, "values", None) or {}).values():
        if getattr(ev, "column_index", None) is not None or getattr(ev, "value", None) is None:
            continue
        basis = getattr(ev, "basis", None)
        entity = str(getattr(basis, "value", basis) or "consolidated")
        figures, legend = out.setdefault(entity, ({}, {}))
        label = str(getattr(ev, "period_label", "") or "?")
        figures[label] = printed_figure(ev)
        heading = getattr(ev, "column_heading", None) or getattr(ev, "period_display", None)
        if heading:
            legend[label] = str(heading)
    return out


def face_index(doc) -> list[tuple[str, str, object]]:
    """`(statement, caption, row)` for every printed face row, for resolving a citation against.

    THE RESOLVER'S SIDE OF THE BLOCK ABOVE. `face_rows` builds what the model is SHOWN; this builds
    what a citation is looked up IN, and the two must agree about which rows exist or the model can
    be shown a row it is then told does not resolve.

    NO STATEMENT FILTER HERE, deliberately, where `face_rows` takes one. A citation names the
    statement itself, so filtering twice would mean a model that cited the right caption under the
    wrong statement got "no such row" instead of a statement mismatch — and those are different
    facts to report. `resolve_sources` compares the statement and says which it was.

    A ROW WITH NO CAPTION IS OMITTED, because a citation is matched BY caption and an empty one
    would match every citation whose own caption normalised to nothing.

    THE SAME ROWS `face_rows` SENDS, apart from the statement filter: a row it leaves out — a notes
    page's, one printed in the notes, a footer or signature line, a column-heading line, one with
    no figure — is left out here too (`_shown_on_face`), so a citation can only land on a row the
    model was shown. Before, the lookup still held every row, and a citation of a caption the
    request never carried could resolve to a note row of the same wording.
    """
    # FOLDED, for the reason `face_rows` gives at length: the two sides of this join spell the
    # equity statement differently, and a citation naming the spelling its own line declares must
    # resolve against the spelling the page carries.
    stmt_of_page = {int(getattr(p, "index", -1)):
                    normalize_statement(str(getattr(p, "statement", "") or ""))
                    for p in (getattr(doc, "pages", None) or ())}
    notes_pages = _notes_pages(doc)
    out: list[tuple[str, str, object]] = []
    for row in (getattr(doc, "line_items", None) or ()):
        caption = getattr(row, "source_label", "") or ""
        if not caption:
            continue
        page = _page_of(row)
        if not _shown_on_face(row, page, notes_pages):
            continue
        out.append((_statement_of(row, stmt_of_page, page), caption, row))
    return out


def _pages_that_are_neither(doc) -> dict[int, object]:
    """The pages that are NEITHER a note NOR a classified statement face, by index.

    THE COMPLEMENT OF WHAT IS NORMALLY READ, stated as the same predicate
    `services.pdf_extract` selects its targets with, inverted — a notes page, or a page whose
    classifier verdict is one of the active statements, is read on every run; everything else is
    read only because a line declares `route: anywhere`. Keeping the two in one shape matters: a
    page that drifted between the two definitions would either be offered as an "other" page while
    already being in the statement block, or be reconstructed and then shown to nobody.
    """
    from app.core.models.enums import PageKind
    from app.services.statements import ACTIVE_STATEMENTS

    out: dict[int, object] = {}
    for page in (getattr(doc, "pages", None) or ()):
        if getattr(page, "kind", None) is PageKind.NOTES:
            continue
        if str(getattr(page, "statement", "") or "") in ACTIVE_STATEMENTS:
            continue
        out[int(getattr(page, "index", -1))] = page
    return out


def _is_printed(row) -> bool:
    """Whether this row was READ OFF A PAGE, rather than synthesised by the requesting stage.

    `stages.line_item_llm._write` CREATES A ROW for a line that had none, and gives it the cited
    row's provenance — so it lands on the cited page and, with nothing excluding it, would appear
    in the next request's block as though the filing had printed it there. Its caption is the
    LINE'S LABEL, not a printed caption, so a later citation could match a row nobody typeset.

    `confidence.method` is the test because this stage is the only writer of `llm` onto a face row
    at this point in the pipeline, and it is the same field `_llm_holds` and the export already
    read to mean "the model answered this".

    ASKED ONLY OF THE OTHER-PAGE BLOCK, and `face_rows` has the same exposure. It is left alone
    here rather than fixed in passing: the statement blocks are what every run sends, so changing
    which rows they carry is a behaviour change to measure against filings rather than to slip in
    beside a new route.
    """
    return not str(getattr(getattr(row, "confidence", None), "method", "") or "").lower().endswith(
        "llm")


def other_page_rows(doc) -> list[dict]:
    """The printed rows of every page that is neither a statement nor a note. One entry per page.

    WHAT `route: anywhere` IS FOR, and what it could not reach until the extractor was widened to
    read these pages at all (`services.pdf_extract`). A five-year summary, a directors' report
    table, a schedule the classifier could not name — each prints captions with figures beside
    them, and no other route can see any of it.

    KEYED BY PAGE, because a page is all the identity such a row has. It sits on no statement (its
    page carries no statement verdict, or one outside the active set) and inside no note, so the
    two join keys every other block uses are both absent. `page` is the page's POSITION IN THE
    FILE, 1-based, and `printed_page` is the folio the page prints when it prints one — both,
    because they routinely differ by several pages and a reader checking the work needs the folio
    while the citation is resolved against the position.

    A ROW WITH NO FIGURE OR NO CAPTION IS OMITTED, for the same two reasons `face_rows` omits
    them: a caption with nothing beside it cannot be where a figure is printed, and a figure with
    no caption cannot be cited.

    NO DETERMINISTIC PROPOSAL TRAVELS WITH THESE ROWS, and there is none to travel. `stages.
    map_ontology` gates a caption match on the page's statement, so a page with no statement
    reaches no alias index — these rows are unclaimed by construction, which is why the block
    carries no `line` key where `face_rows` does.
    """
    others = _pages_that_are_neither(doc)
    by_page: dict[int, list[dict]] = {}
    for row in (getattr(doc, "line_items", None) or ()):
        page = _page_of(row)
        if page is None or page not in others:
            continue
        if not _is_printed(row):
            continue
        caption = (getattr(row, "source_label", "") or "").strip()
        if not caption:
            continue
        figures = _figures(row)
        if not figures:
            continue
        by_page.setdefault(page, []).append({"caption": caption, "figures": figures})
    out: list[dict] = []
    for page in sorted(by_page):
        entry: dict = {"page": page + 1, "rows": by_page[page]}
        if folio := str(getattr(others[page], "printed_page", "") or "").strip():
            entry["printed_page"] = folio
        out.append(entry)
    return out


def other_page_index(doc) -> list[tuple[int, str, object]]:
    """`(page, caption, row)` for every row `other_page_rows` shows — what a citation resolves in.

    `page` IS THE SAME 1-BASED NUMBER THE BLOCK SHOWS, so the model copies back what it was given
    and no side converts. A citation whose page is off by one resolves to nothing rather than to
    the neighbouring page's row, because the caption has to match as well — the same belt-and-
    braces the statement arm uses, and the reason a mis-cited page is reported instead of silently
    publishing the wrong figure.
    """
    others = _pages_that_are_neither(doc)
    out: list[tuple[int, str, object]] = []
    for row in (getattr(doc, "line_items", None) or ()):
        if not _is_printed(row):
            continue
        caption = (getattr(row, "source_label", "") or "").strip()
        if not caption:
            continue
        page = _page_of(row)
        if page is None or page not in others:
            continue
        out.append((page + 1, caption, row))
    return out
