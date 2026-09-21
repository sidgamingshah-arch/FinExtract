"""A NOTE ROUTE REFUSES THE FACE, AND `anywhere` REACHES EVERY PAGE — the route made exclusive.

NEW FILE -> backend/tests/test_route_exclusivity.py

WHAT WAS WRONG. `route` was four values and three of them were descriptions rather than rules. The
face was searched for EVERY line whatever the field said, because `stages.map_ontology` — the one
stage that binds a printed statement caption to a line — did not read the field at all. So a line
whose author wrote "this figure is printed in a note" could still publish a row off the income
statement, and the only guard that ever caught it was `row_terms`, which tests the CAPTION and
knows nothing about WHERE the caption was printed. `anywhere` was worse: it promised the whole
filing and could not read a single page outside the statements and the notes, because nothing
reconstructed those pages.

THE THREE RULES PINNED HERE, each at the place it is now enforced:

  * A DECLARED `note_tables` OR `prose` LINE TAKES NO FACE FIGURE — not from the deterministic
    caption match (`map_ontology`), and not from a model citation naming a statement
    (`resolve_sources`, `allow_face=False`). Its request carries no statement block either
    (`line_item_requests.face_statements`) and no `statement` token to cite.
  * SILENCE IS NOT A REFUSAL. A line declaring no route keeps the face, which is 100 of the 506
    asked-about lines in the shipped set and the convention every other gate here uses.
  * `anywhere` READS EVERY PAGE. Extraction widens to all of them when a line declares it
    (`pdf_extract`), those pages are supplied as `other_pages` (`face_context.other_page_rows`),
    a citation may name one by page, and the figure that comes back is flagged because its SCALE
    was never verified for a page the classifier could not name.
"""
from __future__ import annotations

import json as _json
import pathlib as _pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import load_line_item_set
from app.services import face_context, line_item_llm, line_item_requests, line_item_routes
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

FACE_PAGE, OTHER_PAGE = 5, 40
_SEED = (_pathlib.Path(__file__).resolve().parent.parent
         / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(autouse=True)
def _restore_extraction_settings():
    """`get_settings` IS `@lru_cache`D — the settings object is shared with the whole suite.

    Twice now a test in this area has turned the provider on and left it on, which passes in
    isolation and takes an unrelated module down in the suite. Restored unconditionally.
    """
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = (
            was[0], was[1], was[2], was[3])


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(_json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)


def _row(caption, *, value, key=None, page=FACE_PAGE, method="exact"):
    li = LineItem(source_label=caption, canonical_key=key)
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal(value), value_raw=Decimal(value),
                                    provenance=Provenance(page_index=page))
    if key:
        li.confidence.method = method
        li.confidence.mapping = 1.0
    return li


class _Item:
    """The two fields every predicate here reads. A `LineItemDef` would carry 60 more."""

    def __init__(self, key="k", route=None, statements=(), section_scope=()):
        self.key = key
        self.route = route
        self.statements = tuple(statements)
        self.section_scope = tuple(section_scope)


# ── THE PREDICATE ────────────────────────────────────────────────────────────────────────────

def test_a_note_route_refuses_the_face_and_silence_does_not():
    assert not line_item_routes.may_read_face(_Item(route="note_tables"))
    assert not line_item_routes.may_read_face(_Item(route="prose"))
    assert line_item_routes.may_read_face(_Item(route="face"))
    assert line_item_routes.may_read_face(_Item(route="anywhere"))
    assert line_item_routes.may_read_face(_Item(route=None)), "silence is not a refusal"
    assert line_item_routes.may_read_face(_Item(route="")), "silence is not a refusal"


def test_an_unknown_route_reads_as_silence_rather_than_as_itself():
    """A set authored against a later schema must not have its lines re-routed by a token this
    build does not know. `declared_route` returns "" for it, so every predicate is permissive."""
    assert line_item_routes.declared_route(_Item(route="somewhere_new")) == ""
    assert line_item_routes.may_read_face(_Item(route="somewhere_new"))
    assert not line_item_routes.reads_every_page(_Item(route="somewhere_new"))


def test_the_shipped_set_asks_for_this_on_sixty_lines_and_no_more(shipped):
    """THE BLAST RADIUS, measured rather than asserted about. The ban bites on the lines whose
    author declared a note route and on nothing else."""
    from app.services.line_item_requests import asked_about

    asked = [i for i in shipped.items if asked_about(i)]
    refused = [i for i in asked if not line_item_routes.may_read_face(i)]
    # 510 since the two direct-method TAX face parts joined the set; both declare `route: face`,
    # so the ban's blast radius grows by exactly two, as it did for the two 营业外 parts.
    # 509, not 510: Find 1 stopped declaring a route at all. It is computed from other lines, so
    # it reads neither the face nor the notes and the ban has nothing to bite on for it.
    # 510 since Find 3 split into a gross half and an allowance half: the 关联方应收应付款项 note prints 账面余额 and 坏账准备 and no net column, so the 淨金額 the spec asks for is computed: the two halves both declare `route: note_tables`, while Find 3 itself
    # stopped declaring a route at all, so the ban's blast radius grows by one.
    assert len(asked) == 521, len(asked)   # 511: two leaves gained, their derived parent no
    # longer asked about — a derived parent's figure is its cascade's.
    # 61 since Find 3's two halves both read notes while Find 3 itself no longer declares a
    # route: the refused population grows by one for the same reason `asked` did.
    assert len(refused) == 68, len(refused)   # 68 with the related-party TRADE RECEIVABLE's two note readings — the 账面余额 gross and the 坏账准备 allowance it is net of, one part per `measure` of the same 应收账款 group   # 66 for the same reason the part count moved: the two related-party payable groups   # 62 for the same reason the note-sourced count
    # moved: two readings of the other-receivables net, each reading its own notes.
    # BOTH NOTE ROUTES, since the six functional depreciation splits declare `prose`: the ban is
    # about the FACE, and `note_tables` and `prose` refuse it for the same reason — the author said
    # the figure is printed in a note. Which PART of a note each one reads is a different question,
    # asserted in `test_note_sourced`.
    assert {line_item_routes.declared_route(i) for i in refused} == {"note_tables", "prose"}
    assert sum(1 for i in refused
               if line_item_routes.declared_route(i) == "prose") == 6, sorted(
        i.key for i in refused if line_item_routes.declared_route(i) == "prose")
    # Every one of them declares the note source the route sends it to, so the ban takes a figure
    # away from no line without giving it another way to be read.
    assert all(getattr(i, "note_source", None) is not None for i in refused)
    assert not any(line_item_routes.reads_every_page(i) for i in shipped.items), \
        "no shipped line declares `anywhere`, so the page widening costs a shipped run nothing"


# ── THE DETERMINISTIC PROPOSAL ───────────────────────────────────────────────────────────────

def _two_line_set(route):
    """A set of two lines on one statement, the first carrying `route` and an alias that matches.

    A PURPOSE-BUILT SET RATHER THAN THE SHIPPED ONE, because the shipped set cannot express this
    case: all 60 of its `note_tables` lines declare ZERO aliases (their recognition is authored as
    `note_source.row_caption_any`, on all 60), so no printed statement caption reaches one through
    the alias index today. That makes the refusal a GUARD on the shipped set rather than a fix to
    it — and a guard is exactly what has to be tested against configuration that trips it, since
    the config screen offers `route` and the alias list on every line and the combination is one
    edit away.
    """
    return load_line_item_set({
        "section_defaults": {"pl": {"statement": "profit_and_loss",
                                    "section_scope": ["pl_expenses"]}},
        "items": [
            {"key": "sub__x", "label": "Depreciation of buildings", "inherits": "pl",
             "route": route, "aliases": ["Depreciation of buildings"],
             "definition": "d", "type": "extracted"},
            {"key": "pl__other", "label": "Other operating expenses", "inherits": "pl",
             "route": "face", "aliases": ["Other operating expenses"],
             "definition": "d", "type": "extracted"},
        ]}, resolve=True)


def _map(route, caption):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.map_ontology import MapOntologyStage

    line_items = _two_line_set(route)
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=FACE_PAGE, kind=PageKind.FACE, statement="profit_and_loss")]
    doc.line_items = [_row(caption, value="1234", key=None)]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = line_items
    ctx.ontology = build_working_view(line_items)
    MapOntologyStage().run(doc, ctx)
    return doc.line_items[0], ctx


def test_map_ontology_binds_a_face_caption_when_the_route_permits_it():
    """THE CONTROL. Without it the refusal below proves only that the caption never matched."""
    row, _ctx = _map("face", "Depreciation of buildings")
    assert row.canonical_key == "sub__x", row.canonical_key


def test_map_ontology_refuses_to_bind_a_face_caption_to_a_note_only_line():
    """The same caption, the same statement, the route changed — and the row stays unclaimed."""
    row, ctx = _map("note_tables", "Depreciation of buildings")
    assert row.canonical_key != "sub__x", (
        "a printed statement caption was bound to a line whose route says its figure is in a note")
    assert any("route_refused_face" in line for line in ctx.logs), \
        f"the refusal happened silently: {[line for line in ctx.logs if 'refused' in line]}"


def test_map_ontology_refuses_a_prose_line_the_same_way():
    row, ctx = _map("prose", "Depreciation of buildings")
    assert row.canonical_key != "sub__x", row.canonical_key
    assert any("route_refused_face" in line for line in ctx.logs), ctx.logs[-3:]


def test_a_line_that_declares_no_route_still_takes_the_face_caption():
    """SILENCE IS NOT A REFUSAL, end to end. 100 of the 506 asked-about lines are in this state
    and reading their silence as a restriction would empty lines no author ever restricted."""
    row, _ctx = _map(None, "Depreciation of buildings")
    assert row.canonical_key == "sub__x", row.canonical_key


# ── THE REQUEST ──────────────────────────────────────────────────────────────────────────────

def test_a_note_only_line_is_sent_no_statement_token_to_cite(shipped):
    note_only = next(i for i in shipped.items
                     if line_item_routes.declared_route(i) == "note_tables")
    payload = line_item_llm.line_item_payload(note_only, ("7",))
    assert "statement" not in payload, "the join key for citing a face row was sent anyway"
    assert payload["read_from"].startswith("a row of one of its notes only")


def test_a_face_line_is_sent_the_statement_token_and_the_route_it_declares(shipped):
    face = next(i for i in shipped.items if line_item_routes.declared_route(i) == "face"
                and getattr(i, "statement", None))
    payload = line_item_llm.line_item_payload(face, ())
    assert payload["statement"]
    assert payload["read_from"].startswith("the face of its statement only")


def test_a_line_that_declares_no_route_is_told_nothing_about_one(shipped):
    silent = next(i for i in shipped.items if not line_item_routes.declared_route(i))
    assert "read_from" not in line_item_llm.line_item_payload(silent, ())


def test_a_plan_of_only_note_only_lines_is_supplied_no_statement_rows():
    plan = line_item_requests.RequestPlan(
        name="p", keys=("a", "b"), notes=("7",),
        sections=(("profit_and_loss", "pl_expenses"),))
    by_key = {"a": _Item("a", route="note_tables", statements=("profit_and_loss",)),
              "b": _Item("b", route="prose", statements=("profit_and_loss",))}
    assert line_item_requests.face_statements(plan, by_key) == set()


def test_a_mixed_plan_is_supplied_the_statement_its_face_line_may_read():
    """The note-set grouping knows nothing about routes, so one request can hold both. The block
    is supplied for the face line — and the note-only line's own CITATION is what is refused."""
    plan = line_item_requests.RequestPlan(
        name="p", keys=("a", "b"), notes=("7",),
        sections=(("profit_and_loss", "pl_expenses"),))
    by_key = {"a": _Item("a", route="note_tables", statements=("profit_and_loss",)),
              "b": _Item("b", route="face", statements=("profit_and_loss",),
                         section_scope=("pl_expenses",))}
    assert line_item_requests.face_statements(plan, by_key) == {"profit_and_loss"}


# ── THE CITATION ─────────────────────────────────────────────────────────────────────────────

def _face_doc():
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=FACE_PAGE, kind=PageKind.FACE, statement="balance_sheet")]
    doc.line_items = [_row("Buildings", value="4200", key="bs_nca__buildings")]
    return doc


def test_a_statement_citation_is_refused_for_a_note_only_line():
    res, un = resolve_sources([SourceRef(statement="balance_sheet", caption="Buildings")],
                              [], face_context.face_index(_face_doc()), allow_face=False)
    assert not res, "a note-only line published a figure off the statement"
    assert "read from its notes" in un[0]["why"], un[0]["why"]


def test_the_refusal_says_the_place_was_wrong_and_not_the_caption():
    """An empty face index would also produce no figure, and would say "no printed row on
    balance_sheet matches that caption" — which sends an author to fix the wrong thing."""
    _res, refused = resolve_sources([SourceRef(statement="balance_sheet", caption="Buildings")],
                                    [], face_context.face_index(_face_doc()), allow_face=False)
    _res2, absent = resolve_sources([SourceRef(statement="balance_sheet", caption="Buildings")],
                                    [], [])
    assert refused[0]["why"] != absent[0]["why"]
    assert "matches that caption" in absent[0]["why"]
