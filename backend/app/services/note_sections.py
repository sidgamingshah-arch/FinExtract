"""WHICH SECTION IS A NOTE ALIGNED TO — so `section_scope` can narrow a line's note candidates.

THE QUESTION. A line declaring `section_scope: ['bs_ca']` is saying "I live among the current
assets". For matching a printed caption that is already honoured — `LineItemDef.claimable_under`
gates on the banner the caption sits under. For NOTES nothing honoured it at all: which notes a
line reads was decided solely by `note_source.note_title_any`, and `section_scope` was not read
anywhere in the note path.

THE SIGNAL, chosen by measurement over the twelve-filing corpus rather than by preference:

  * `NoteItem.section_hint` — the banner printed above the row — is UNUSABLE. 13% coverage, and
    the values are not sections: 'RMB RMB US$', 'BAIDU, INC.', 'COST', 'CARRYING VALUES',
    'PETROCHINA COMPANY LIMITED'. On a notes page the nearest preceding banner is whatever the
    typesetter printed last.

  * `doc.links` — the note-to-face reconciliation `stages.link_notes` already computes — is the
    one that works. `FaceNoteLink.face_item_id` names the FACE ROW that cited the note, and that
    row's concept carries the section. A note cited from a current-assets line is a current-assets
    note, which is the semantics wanted.

HOW FAR IT REACHES, stated because it bounds what this can do. Of 486 notes: 309 (64%) resolve to
at least one section, but only 146 resolve to EXACTLY ONE. The other 163 are cited from several
sections at once and 177 from none — and one filing in the corpus (168 notes) resolves zero,
because none of its notes is cited from a face row this run mapped. So roughly 30% of notes are
narrowable and 70% are not.

WHICH IS WHY UNKNOWN MEANS OPEN. A note with no resolved section, or with several, stays eligible
for every line. Narrowing on a signal absent from 70% of notes would be starvation dressed as
precision: the filing with 168 unresolved notes would lose its entire note context, and losing a
figure is worse than carrying a note that turns out to be irrelevant. It is the same convention
`section_scope` already uses for an empty list and `statement` for None — absence is "nothing was
said", never "nothing is allowed".
"""
from __future__ import annotations

from app.services import line_item_routes


def note_sections(doc, line_item_set) -> dict[str, set[str]]:
    """`{note_number: {section_scope_id, …}}` for every note whose section could be resolved.

    A note appears here only when the chain completes: a link names a face row, that row carries a
    canonical key, and the key's definition declares a `section_scope`. A note absent from this map
    is OPEN — see the module docstring.

    Both sections are kept for a note cited from two, rather than picking one: the caller treats a
    multi-section note as open, and that decision belongs to the caller's rule rather than to this
    lookup silently discarding evidence.
    """
    by_id = {str(getattr(li, "id", "")): li for li in (getattr(doc, "line_items", None) or ())}
    by_key = {i.key: i for i in (getattr(line_item_set, "items", None) or ())}
    out: dict[str, set[str]] = {}
    for link in (getattr(doc, "links", None) or ()):
        number = str(getattr(link, "note_number", "") or "")
        if not number:
            continue
        row = by_id.get(str(getattr(link, "face_item_id", "") or ""))
        if row is None:
            continue
        item = by_key.get(str(getattr(row, "canonical_key", "") or ""))
        if item is None:
            continue
        scopes = {str(s) for s in (getattr(item, "section_scope", None) or ()) if s}
        if scopes:
            out.setdefault(number, set()).update(scopes)
    return out


def open_to(item, note_number: str, sections: dict[str, set[str]]) -> bool:
    """Whether this line may read that note, under its own `section_scope`.

    FIVE WAYS TO BE OPEN, and only one way to be closed:

      * the line's route is `anywhere` — see below;
      * the line declares no `section_scope` — unconstrained, nothing was said;
      * the note's section could not be resolved — 70% of them, see the module docstring;
      * the note is cited from SEVERAL sections, so it belongs to none of them exclusively;
      * the note's section is one the line names.

    Closed only when the note resolves to exactly one section and the line names a different one.
    That is the whole narrowing: it removes a note that is definitively another section's, and
    nothing else.

    `anywhere` IS WHAT MAKES THAT FIRST ARM, and it is the route's only behaviour anywhere in the
    pipeline. Measured before this: `anywhere` was accepted by the schema, offered by the config
    screen, described there as "looked for on the face AND in the note rows AND in prose — nothing
    is constrained", and then read by exactly one line of code — `note_sourced.route_of`, which
    passed it through to a stage that never branched on it. With a `note_source` it behaved as
    `note_tables`; without one the note stage never saw the line at all, so it behaved as `face`.
    It was decorative in every configuration, and zero of the shipped 527 lines declare it.

    THIS IS ONE OF THE ROUTE'S THREE EFFECTS, and the only one in this file. What it does here is
    stop the narrowing: a line whose author wrote "do not constrain it" should not then have its
    note search closed against a note that resolved to one section. The other two are elsewhere
    and both were added after this docstring first claimed the route could reach neither:

      * `services.pdf_extract` RECONSTRUCTS EVERY PAGE when any line declares `anywhere`. The
        sentence that used to stand here — that the route cannot reach a page outside the
        statements and the notes, because nothing reconstructs those pages — described the target
        set rather than a limit of the route, and the target set is what changed.
      * `services.note_sourced.resolve_sources` accepts a citation naming one of those pages, and
        `services.face_context.other_page_rows` supplies them, for `anywhere` lines only.

    AND THE ROUTE STILL CANNOT STOP THE FACE BEING SEARCHED, because it does not want to:
    `anywhere` is the widest route, so `line_item_routes.may_read_face` is true for it. It is
    `note_tables` and `prose` that refuse the face now, and `stages.map_ontology` reads the field
    to enforce it.
    """
    if line_item_routes.reads_every_page(item):
        return True
    scope = {str(s) for s in (getattr(item, "section_scope", None) or ()) if s}
    if not scope:
        return True
    resolved = sections.get(str(note_number or ""))
    if not resolved or len(resolved) != 1:
        return True
    return bool(resolved & scope)
