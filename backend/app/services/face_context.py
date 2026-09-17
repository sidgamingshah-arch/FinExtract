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
