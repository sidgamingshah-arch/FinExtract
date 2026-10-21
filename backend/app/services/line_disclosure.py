"""The disclosure a note line's figure was added up from: every amount that made it, and the
note text it was read from, quoted.

A LINE BUILT FROM NOTE-READ PARTS IS A SUM OF TRANSACTIONS, NOT A CAPTION. Related Party
Transactions is the case this was written for: the figure is the sales, purchases, leases, interest,
key management pay and other transactions the related-party note prints for the year, added up by
the line's cascade from one part per category. A reader handed that one number cannot check it,
and cannot see what the note says about the year-end balances and the guarantees that are, by
definition, NOT in it. So the line carries two things beside its figure:

  * ``items`` — every amount that entered the figure, per basis:period: the part (category) it
    went into, the caption and note it was cited from, the group the row is printed under (the
    counterparty of a mainland row, the nature of an HKFRS one), its page. Read off the parts' own trails
    (`LineItem.derivation`), which the model route and the note route both write, so this adds no
    reading of its own and cannot disagree with the arithmetic.
  * ``notes`` — the note text itself, AS THE FILING PRINTS IT (`NotesTable.source_text`), with its
    note number, heading and pages. Never a summary: a paraphrase of a disclosure is a second,
    unreviewed statement of it.

WHICH LINES. A printed ``derived`` line of the notes section whose every cascade term is one of
ITS OWN parts, each read from a note (`note_source` declared). That is configuration, not a list in code: Related Party
Transactions and Contingent liabilities qualify on the shipped set, and a line an author builds the
same way next year qualifies with no edit here.

WHICH NOTE TEXT. A table is the line's disclosure when one of its parts cited it, or when its own
heading or its chapter's heading carries every word of one of the parts' `note_terms`
(`line_item_notes.heading_covered`) — the same test that decides which notes a part may read. The
chapter is what brings the whole related-party chapter of a mainland filing along (十四、关联方及
关联交易 → its guarantees and its 应收应付 balances), and the per-table test is what keeps a note
number shared with other subjects from bringing them too.

RUN WHERE IT RUNS — at the end of the note-sourced stage, after every cascade has resolved and
BEFORE `PruneNotesStage` drops the notes no face line cites: a related-party note is cited by no
face row, so after pruning its text is gone.
"""
from __future__ import annotations

from app.services import derivation as _derivation
from app.services import line_item_notes

# A disclosure quoted in full can still be long; a mainland related-party chapter runs to five
# pages. This bounds ONE table's text, not the disclosure — the cut is said where it is made.
MAX_TABLE_CHARS = 6000


def disclosing_lines(items) -> dict[str, list]:
    """``{line key: [its parts, in cascade order]}`` for every line that carries a disclosure."""
    by_key = {i.key: i for i in items or ()}
    out: dict[str, list] = {}
    for item in items or ():
        if str(getattr(item, "type", "") or "") != "derived" or not getattr(item, "cascade", None):
            continue
        # A PRINTED NOTES LINE only. An internal part built from note parts (other receivables' net)
        # is a step in another line's arithmetic, and its notes are that line's to show.
        if (str(getattr(item, "namespace", "") or "") != "template"
                or str(getattr(item, "inherits", "") or "") != "notes"):
            continue
        refs: list[str] = []
        for rung in item.cascade:
            for term in rung.terms or ():
                if term.ref and term.ref not in refs:
                    refs.append(term.ref)
        parts = [by_key.get(r) for r in refs]
        if not parts or any(p is None or getattr(p, "parent", None) != item.key
                            or getattr(p, "note_source", None) is None for p in parts):
            continue
        out[item.key] = parts
    return out


def _entered(inp: dict) -> bool:
    status = inp.get("status")
    return status in _derivation.ENTERED if status is not None else bool(inp.get("counted", True))


def _groups_of(notes) -> dict:
    """``(page, caption, figure) -> group`` for every note row printed under a group — the
    counterparty of a mainland related-party row, the nature of an HKFRS one. A trail cites a row by
    its caption and page; the group is what tells "销售商品" to 湖北茉莉大药房 from 销售商品 to another."""
    out: dict = {}
    for table in notes or ():
        for item in getattr(table, "items", None) or ():
            group = (getattr(item, "group_hint", "") or "").strip()
            if not group:
                continue
            for ev in (getattr(item, "values", None) or {}).values():
                prov = getattr(ev, "provenance", None)
                page = getattr(prov, "page_index", None) if prov is not None else None
                if page is not None and ev.value is not None:
                    out.setdefault((page, item.raw_label.strip(), str(ev.value)), group)
    return out


def _same_figure(a, b) -> bool:
    try:
        from decimal import Decimal
        return Decimal(str(a)) == Decimal(str(b))
    except Exception:  # noqa: BLE001 - a figure that is not a number matches nothing
        return False


def line_items_of(parent_row, rows_by_key: dict, part_defs: list, notes=()) -> list[dict]:
    """Every amount that entered the parent's figure, flattened from its parts' trails."""
    label_of = {p.key: (p.label or p.key) for p in part_defs}
    groups = _groups_of(notes)
    out: list[dict] = []
    for slot, trail in sorted((getattr(parent_row, "derivation", None) or {}).items()):
        if not isinstance(trail, dict):
            continue
        basis, _, period = slot.partition(":")
        for term in trail.get("inputs") or ():
            key = term.get("canonical_key")
            if key not in label_of or not _entered(term):
                continue
            part_row = rows_by_key.get(key)
            part_trail = ((getattr(part_row, "derivation", None) or {}).get(slot)
                          if part_row is not None else None)
            inputs = [i for i in ((part_trail or {}).get("inputs") or ()) if _entered(i)]
            if not inputs:
                # A part read as one printed figure with no trail of its own still entered the sum.
                out.append({"basis": basis, "period": period, "part": key,
                            "category": label_of[key], "amount": term.get("used", term.get("value")),
                            "group": None, "caption": None, "note": None, "provenance": None})
                continue
            for inp in inputs:
                note = inp.get("note")
                caption = str(inp.get("label") or "")
                # The model route labels a cited row "note 十四、5: 销售商品"; the note is its own field.
                if note and caption.startswith(f"note {note}: "):
                    caption = caption[len(f"note {note}: "):]
                prov = inp.get("provenance") or {}
                bare = caption.split(" [", 1)[0].strip()
                group = next((g for (pg, cap, fig), g in groups.items()
                              if pg == prov.get("page_index") and cap == bare
                              and _same_figure(fig, inp.get("value"))), None)
                out.append({"basis": basis, "period": period, "part": key,
                            "category": label_of[key],
                            "amount": inp.get("used", inp.get("value")),
                            "group": group, "caption": caption or None, "note": note,
                            "provenance": inp.get("provenance")})
    return out


def _cited_notes(rows_by_key: dict, part_defs: list) -> set[str]:
    cited: set[str] = set()
    for p in part_defs:
        row = rows_by_key.get(p.key)
        for trail in ((getattr(row, "derivation", None) or {}).values() if row is not None else ()):
            if not isinstance(trail, dict):
                continue
            for inp in trail.get("inputs") or ():
                if inp.get("note") and _entered(inp):
                    cited.add(str(inp["note"]))
    return cited


def note_text_of(notes, part_defs: list, cited: set[str]) -> list[dict]:
    """The note tables that are this line's disclosure, in printed order, each quoted whole."""
    out: list[dict] = []
    for table in notes or ():
        number = str(getattr(table, "note_number", "") or "")
        title = str(getattr(table, "title", "") or "")
        chapter = str(getattr(table, "chapter_title", "") or "")
        covered = any(line_item_notes.heading_covered(p, title)
                      or (chapter and line_item_notes.heading_covered(p, chapter))
                      for p in part_defs)
        if not covered and number not in cited:
            continue
        text = " ".join(str(getattr(table, "source_text", "") or "").split())
        if not text:
            continue
        cut = len(text) > MAX_TABLE_CHARS
        out.append({"note": number or None, "title": title or None, "chapter": chapter or None,
                    "pages": [int(p) for p in (getattr(table, "source_pages", None) or ())],
                    "text": text[:MAX_TABLE_CHARS], "truncated": cut})
    return out


def attach(doc, line_item_set) -> int:
    """Write ``LineItem.disclosure`` on every disclosing line that has a row. Returns how many."""
    lines = disclosing_lines(getattr(line_item_set, "items", None) or ())
    if not lines:
        return 0
    rows_by_key = {li.canonical_key: li for li in doc.line_items if li.canonical_key}
    done = 0
    for key, part_defs in lines.items():
        row = rows_by_key.get(key)
        if row is None or not row.values:
            continue
        row.disclosure = {
            "items": line_items_of(row, rows_by_key, part_defs, doc.notes),
            "notes": note_text_of(doc.notes, part_defs, _cited_notes(rows_by_key, part_defs)),
        }
        done += 1
    return done
