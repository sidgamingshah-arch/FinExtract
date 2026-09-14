r"""A LINE'S SECTION NARROWS WHICH NOTES IT READS — and unknown means open.

NEW FILE -> backend/tests/test_note_sections.py

WHAT `section_scope` DID AND DID NOT DO. For matching a printed caption it was already honoured:
`LineItemDef.claimable_under` gates on the banner the caption sits under, and that is its ONE
consumer in the application. For NOTES it was read nowhere — `line_item_notes`, `note_context`,
`note_sourced`, `line_item_llm` and `line_item_requests` contain zero references to it — so a line
declaring "I live among the current assets" still considered every note in the filing.

THE SIGNAL WAS CHOSEN BY MEASUREMENT, and the obvious candidate was a trap:

  * `NoteItem.section_hint`, the banner above the row, is UNUSABLE — 13% coverage across the
    twelve-filing corpus and the values are not sections: 'RMB RMB US$', 'BAIDU, INC.', 'COST',
    'CARRYING VALUES', 'PETROCHINA COMPANY LIMITED'.
  * `doc.links`, the note-to-face reconciliation, works: `FaceNoteLink.face_item_id` names the face
    row that cited the note, and that row's concept carries the section.

HOW FAR IT REACHES, which is why the rule is permissive: of 486 notes, 309 resolve to at least one
section but only 146 to EXACTLY ONE. 163 are cited from several sections and 177 from none, and one
filing's 168 notes resolve zero. So ~30% are narrowable. Narrowing on a signal absent from 70% of
notes would be starvation dressed as precision — the filing with 168 unresolved notes would lose its
whole note context — so UNKNOWN AND AMBIGUOUS BOTH MEAN OPEN.

WHAT IT FIXED, measured end to end on 2025032802704.pdf and pinned below. Note 5,
"LONG-TERM TIME DEPOSITS AND HELD-TO-MATURITY", is cited only from
`bs_nca__other_non_current_assets`, so it resolves to `bs_nca`. It was nonetheless being read by
`sub__fa_cp_afs_htm_note_total` — the CURRENT securities line, scoped `['bs_ca']` — which published
24,666 and 13,499. The non-current line `bs_nca__secur_and_other_fincl_assets_ltp` carried the SAME
two figures, so total assets counted them twice: 456,091 where 431,425 is right, a 24,666
overstatement, and the only visible trace was a plausible-looking number on the wrong line.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.note_sections import note_sections, open_to


class _Link:
    def __init__(self, note_number: str, face_item_id) -> None:
        self.note_number, self.face_item_id = note_number, face_item_id


class _Row:
    def __init__(self, key: str) -> None:
        self.id, self.canonical_key = uuid4(), key


class _Item:
    def __init__(self, key: str, scope: list[str] | None = None) -> None:
        self.key, self.section_scope = key, list(scope or ())


class _Doc:
    def __init__(self, rows, links) -> None:
        self.line_items, self.links = rows, links


class _Set:
    def __init__(self, items) -> None:
        self.items = items


def _world(*pairs: tuple[str, str]):
    """`(doc, set)` where each pair is (note number, the section of the face row citing it)."""
    rows, links, items = [], [], []
    for n, (note, section) in enumerate(pairs):
        row = _Row(f"face_{n}")
        rows.append(row)
        links.append(_Link(note, row.id))
        items.append(_Item(f"face_{n}", [section]))
    return _Doc(rows, links), _Set(items)


# ── resolving a note's section ───────────────────────────────────────────────────────────────────

def test_a_note_cited_from_one_section_resolves_to_it():
    doc, st = _world(("5", "bs_nca"))
    assert note_sections(doc, st) == {"5": {"bs_nca"}}


def test_a_note_cited_from_two_sections_keeps_both():
    """BOTH are kept rather than one picked: the caller's rule decides what to do with an
    ambiguous note, and this lookup must not discard the evidence that it IS ambiguous."""
    doc, st = _world(("7", "bs_ca"), ("7", "bs_nca"))
    assert note_sections(doc, st) == {"7": {"bs_ca", "bs_nca"}}


def test_a_note_nothing_cites_is_absent():
    """Absent, not empty — 177 of 486 notes in the corpus are in this state, and the caller reads
    absence as open."""
    doc, st = _world(("5", "bs_nca"))
    assert "9" not in note_sections(doc, st)


def test_a_link_whose_face_row_is_unknown_resolves_nothing():
    """A link may name a row this run did not map. The chain has to fail closed to UNRESOLVED
    rather than raise or invent a section."""
    doc, st = _world(("5", "bs_nca"))
    doc.links.append(_Link("6", uuid4()))
    assert note_sections(doc, st) == {"5": {"bs_nca"}}


def test_a_face_row_with_no_section_resolves_nothing():
    doc, st = _world(("5", "bs_nca"))
    row = _Row("face_x")
    doc.line_items.append(row)
    doc.links.append(_Link("8", row.id))
    st.items.append(_Item("face_x", []))
    assert "8" not in note_sections(doc, st)


# ── the rule: four ways to be open, one way to be closed ─────────────────────────────────────────

def test_a_line_with_no_scope_reads_every_note():
    """Unconstrained — the same convention an empty `section_scope` already has for matching."""
    assert open_to(_Item("x", []), "5", {"5": {"bs_nca"}}) is True


def test_an_unresolved_note_is_open_to_everything():
    """THE INVARIANT THAT MAKES THIS SAFE. 70% of notes are in this state; closing them would
    starve the context and lose figures, which is worse than carrying an irrelevant note."""
    assert open_to(_Item("x", ["bs_ca"]), "9", {"5": {"bs_nca"}}) is True


def test_a_note_of_several_sections_is_open_to_everything():
    """Cited from current AND non-current, so it belongs to neither exclusively."""
    assert open_to(_Item("x", ["bs_ca"]), "7", {"7": {"bs_ca", "bs_nca"}}) is True


def test_a_note_of_the_lines_own_section_is_open():
    assert open_to(_Item("x", ["bs_ca"]), "5", {"5": {"bs_ca"}}) is True


def test_a_note_of_exactly_one_other_section_is_closed():
    """THE ONLY CLOSURE, and the measured case: a long-term deposits note cited only from a
    non-current face row, read by the CURRENT securities line."""
    assert open_to(_Item("sub__fa_cp_afs_htm_note_total", ["bs_ca"]),
                   "5", {"5": {"bs_nca"}}) is False


def test_a_line_scoped_to_several_sections_is_open_to_any_of_them():
    assert open_to(_Item("x", ["bs_ca", "bs_nca"]), "5", {"5": {"bs_nca"}}) is True


# ── the seam: select_rows ignores narrowing unless it is given the map ───────────────────────────

def test_select_rows_without_the_map_narrows_nothing():
    """The parameter is optional and defaults to no narrowing, so every existing caller — and every
    test written before this — keeps its behaviour exactly."""
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
    from app.services.note_sourced import select_rows
    from decimal import Decimal

    row = NoteItem(raw_label="Total")
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("24666"), value_raw=Decimal("24666")))
    table = NotesTable(note_number="5", title="LONG-TERM TIME DEPOSITS AND HELD-TO-MATURITY")
    table.items = [row]

    class _Cfg:
        note_title_any = [r"held.to.maturity"]
        row_caption_any = [r"^\s*total\s*$"]
        row_caption_none: list[str] = []

    class _Line:
        key = "sub__fa_cp_afs_htm_note_total"
        section_scope = ["bs_ca"]
        note_source = _Cfg()

    line, notes, periods = _Line(), [table], {"current"}
    assert len(select_rows(line, notes, periods)) == 1, "no map must mean no narrowing"
    assert len(select_rows(line, notes, periods, {"5": {"bs_nca"}})) == 0, "the map must narrow"
    assert len(select_rows(line, notes, periods, {})) == 1, "an empty map resolves nothing"


# ── the face guard ───────────────────────────────────────────────────────────────────────────────

def test_a_face_section_line_is_not_offered_the_note_route():
    """"Where in the report does this line live?" decides WHICH SEARCH runs. A guard rather than a
    change: of the 396 items in the twelve face sections, none declares a `note_source`."""
    from app.services.line_item_config import load_shipped_set
    from app.stages.note_sourced import _declared_items

    st = load_shipped_set()
    declared = [i for i in st.items if i.note_source is not None]
    selected = _declared_items(st)
    assert len(selected) == len(declared), (
        "the shipped set has a face-section line declaring a note_source — read this test")

    sections = st.section_defaults
    for item in selected:
        section = sections.get(str(getattr(item, "inherits", "") or ""))
        assert section is None or section.where() != "face", item.key


def test_no_section_keeps_the_note_route():
    """NO SECTION MEANS OPEN. The three related-party Find lines name no `inherits`, and absence is
    "nothing was said" — never "face"."""
    from app.services.line_item_config import load_shipped_set
    from app.stages.note_sourced import _declared_items

    st = load_shipped_set()
    selected = {i.key for i in _declared_items(st)}
    for key in ("sub__rp_find_1", "sub__rp_find_2", "sub__rp_find_3"):
        assert key in selected, f"{key} lost the note route"


def test_where_is_one_definition_with_three_answers():
    """`face`, `notes` and `either` — and the third is not a degenerate case. Five sections are
    neither, and a reader must not fold them into one of the other two."""
    from app.services.line_item_config import load_shipped_set

    st = load_shipped_set(resolve=False)
    by_where: dict[str, list[str]] = {}
    for key, section in st.section_defaults.items():
        by_where.setdefault(section.where(), []).append(key)
    assert sorted(by_where) == ["either", "face", "notes"]
    assert len(by_where["face"]) == 12, by_where["face"]
    assert by_where["notes"] == ["notes"]
    assert sorted(by_where["either"]) == [
        "capital_and_lease_commitments", "credit_compliance", "off_balance_sheet_data",
        "statement_setup_controls", "supplemental_data"], by_where["either"]


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
