"""WHICH PLACES IN THE FILING A LINE MAY TAKE ITS FIGURE FROM — the route, read once.

NEW FILE -> backend/app/services/line_item_routes.py

THE ROUTE USED TO BE READ IN FRAGMENTS, and the fragments disagreed. `stages.note_sourced.route_of`
mapped a blank route to `note_tables` for the note reader; `stages.note_sourced._lives_on_the_face`
answered "is this a face line" for the same stage's own filter; `services.note_sections.open_to`
tested for `anywhere` on its own; and `stages.map_ontology` — the ONE stage that binds a printed
face caption to a line — did not read the field at all. So a line whose author wrote "read this
from a note's rows" could still be handed a figure off the statement, because the only code that
could have refused it never asked.

WHAT THE FOUR ROUTES NOW MEAN, and it is the same sentence in every reader:

    face          the statement, and nothing else. No note is searched.
    note_tables   a note's ROWS, and nothing else. The statement is not read.
    prose         a SENTENCE in a note, and nothing else. The statement is not read.
    anywhere      any page of the filing — the statements, the notes, and the pages that are
                  neither.

`note_tables` AND `prose` REFUSING THE FACE IS THE CHANGE. Both used to carry the face implicitly:
`map_ontology` matched every printed caption against every concept's aliases whatever the concept's
route said, so a depreciation part declared `note_tables` could bind to a face row and publish the
income statement's own total. The row-terms gate caught the loudest case of that and it is not a
route check — it tests the caption against the line's vocabulary, which says nothing about WHERE
the caption was printed.

ABSENCE IS STILL "NOTHING WAS SAID". A line that declares no route is NOT treated as note-only:
`declared_route` returns "" for it and every predicate here is permissive. That is the convention
`section_scope`, `statement` and `claimable_under` already use, and it is load-bearing — 100 of
the 506 asked-about lines in the shipped set declare no route, and reading their silence as a
restriction would take the face away from lines no author ever restricted. Measured on that set:
357 lines declare `face`, 60 declare `note_tables`, 110 declare nothing, and NONE declares `prose`
or `anywhere`. So the ban below bites on exactly the 60 lines whose author asked for it, all 60 of
which declare a `note_source` for the note route to read.
"""
from __future__ import annotations

ROUTES = ("face", "note_tables", "prose", "anywhere")

#: The routes that say "not the statement" — the only two that refuse the face.
NOTE_ONLY_ROUTES = ("note_tables", "prose")


def declared_route(item) -> str:
    """The route this line DECLARES, or "" when it declares none or an unknown one.

    Deliberately not defaulted. A caller that wants the legacy note default asks for it by name
    (`stages.note_sourced.route_of`); a caller deciding whether to take something AWAY from a line
    must be able to tell "the author said notes" from "the author said nothing", and a function
    that silently answered `note_tables` for both made that impossible.
    """
    route = str(getattr(item, "route", "") or "").strip()
    return route if route in ROUTES else ""


def may_read_face(item) -> bool:
    """Whether a figure printed on the FACE of a statement may become this line's figure.

    False for a declared `note_tables` or `prose` line and true for everything else, silence
    included. This is the predicate `stages.map_ontology` consults before binding a printed face
    caption, and the one `stages.line_item_llm` consults before supplying the statement's rows as
    context or accepting a citation that names one.
    """
    return declared_route(item) not in NOTE_ONLY_ROUTES


def may_read_notes(item) -> bool:
    """Whether a NOTE may supply this line's figure.

    False only for a declared `face` line — the guard `stages.note_sourced._declared_items`
    already applied, restated here so both directions of the fence are in one file. Silence keeps
    the note route, which is what carrying a `note_source` has always meant.
    """
    return declared_route(item) != "face"


def reads_every_page(item) -> bool:
    """Whether this line may take a figure off a page that is NEITHER a statement nor a note.

    True only for `anywhere`, and it is the route's second effect after lifting the section
    narrowing in `services.note_sections.open_to`. It is read at extraction time — a page nothing
    reconstructed has no rows to cite, so the route has to widen the target set before the figures
    exist rather than after (`services.pdf_extract`).
    """
    return declared_route(item) == "anywhere"


def any_reads_every_page(line_item_set) -> bool:
    """Whether ANY line in this set declares `anywhere`, so extraction must widen to all pages.

    Asked once per run rather than per page. None of the shipped 527 lines declares it, so the
    widening is opt-in and costs a run nothing until an author asks for it.
    """
    return any(reads_every_page(i)
               for i in (getattr(line_item_set, "items", None) or ()))
