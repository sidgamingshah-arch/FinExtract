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
`section_scope`, `statement` and `claimable_under` already use, and it is load-bearing — 99 of the
511 asked-about lines in the shipped set declare no route, and reading their silence as a
restriction would take the face away from lines no author ever restricted.

MEASURED ON THE SHIPPED SET (535 lines): 361 declare `face`, 58 `note_tables`, 6 `prose`, 110
nothing, and none `anywhere`. So the ban below bites on 64 lines, 62 of which declare a
`note_source` for the note route to read; the other two are DERIVED parents
(`sub__cp_other_receivables_net`, `sub__rp_find_3`) whose figure is their cascade's, so a route
says nothing about them either way and the matcher locks them out regardless.

THE SIX `prose` LINES ARE THE DEPRECIATION SPLITS BY FUNCTION — R&D, selling and marketing, G&A,
other operating expenses, the profit-before-tax note's operating-expense callout, and cost of
sales. They are also the only six lines in the set carrying prose vocabulary
(`note_source.prose_subject` + `prose_landed_in`), which is what makes `prose` the accurate route
rather than an added restriction: a functional split of a depreciation charge is disclosed in a
SENTENCE ("Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are
included in 'other operating expenses'"), and a note TABLE that appears to state one is almost
always the note total the split is a component of — the mistake
`line_item_notes.caption_agrees_with_row_terms` exists to catch after the fact, refused at the
route instead.
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


def may_read_table_rows(item) -> bool:
    """Whether a figure printed in a TABLE ROW may become this line's figure.

    THE THIRD SIDE OF THE FENCE, and it was missing. `may_read_face` says whether the STATEMENT may
    supply the figure and `may_read_notes` whether a NOTE may, so between them a `prose` line is
    refused the face and allowed its note — which is right, because the sentence it wants is IN a
    note. What neither asks is whether a TABLE ROW may supply it, and that is exactly what `prose`
    denies: its author said the figure is stated in a sentence and tabulated nowhere.

    MEASURED, before this existed: `sub__ga_depreciation` declares `route: prose`, and a citation
    naming note 9's row "Depreciation of property, plant and equipment" RESOLVED and took 12,345
    off that row. `stages.note_sourced` was the only reader honouring the route — it opens
    `[] if route_of(item) == "prose"` — while the citation path consulted only `may_read_face`,
    which is true of a face row and says nothing about a note's.

    False for `prose` and true for everything else, silence included: a line carrying a
    `note_source` has always meant its figure is tabulated.
    """
    return declared_route(item) != "prose"


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

    Asked once per run rather than per page. None of the shipped 535 lines declares it, so the
    widening is opt-in and costs a run nothing until an author asks for it.
    """
    return any(reads_every_page(i)
               for i in (getattr(line_item_set, "items", None) or ()))
