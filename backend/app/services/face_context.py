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
        out[str(getattr(ev, "period_label", "") or "?")] = str(ev.value)
    return out


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
    want = {str(s) for s in (statements or ()) if str(s)}
    if not want:
        return []
    stmt_of_page = {int(getattr(p, "index", -1)): str(getattr(p, "statement", "") or "")
                    for p in (getattr(doc, "pages", None) or ())}

    by_statement: dict[str, list[tuple[int, dict]]] = {}
    for row in (getattr(doc, "line_items", None) or ()):
        page = _page_of(row)
        statement = stmt_of_page.get(page if page is not None else -1, "")
        if statement not in want:
            continue
        figures = _figures(row)
        if not figures:
            continue
        entry: dict = {"caption": getattr(row, "source_label", "") or "", "figures": figures}
        if banner := (getattr(row, "section_hint", None) or "").strip():
            entry["under"] = banner
        if sub := (getattr(row, "group_hint", "") or "").strip():
            entry["subheading"] = sub
        # THE DETERMINISTIC ROUTE'S PROPOSAL. Omitted for a row the mapper could not place —
        # `stages.face_mapping_contract` keys those `engine_unclassified_face__…`, which names no
        # line and would read as one.
        key = str(getattr(row, "canonical_key", "") or "")
        if key and not key.startswith("engine_unclassified"):
            entry["line"] = key
        by_statement.setdefault(statement, []).append((page if page is not None else 0, entry))

    out: list[dict] = []
    for statement in sorted(by_statement):
        rows = [e for _p, e in sorted(by_statement[statement], key=lambda pe: pe[0])]
        out.append({"statement": statement, "rows": rows})
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
    """
    stmt_of_page = {int(getattr(p, "index", -1)): str(getattr(p, "statement", "") or "")
                    for p in (getattr(doc, "pages", None) or ())}
    out: list[tuple[str, str, object]] = []
    for row in (getattr(doc, "line_items", None) or ()):
        caption = getattr(row, "source_label", "") or ""
        if not caption:
            continue
        page = _page_of(row)
        out.append((stmt_of_page.get(page if page is not None else -1, ""), caption, row))
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
