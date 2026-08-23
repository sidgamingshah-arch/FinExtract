"""Whose figures a PAGE presents, when no column header on it says.

The two mechanisms that existed before this file both need something printed in the value area:
``row_reconstruct._basis_bands`` needs a two-basis column header ("Group | Company") over the
figures, and ``scope_selection.entity_scope.company_only_markers`` needs a specific line to be
present on the face. An HKEX filing prints the Company's statement of financial position on its
OWN page, past the notes, titled only "STATEMENT OF FINANCIAL POSITION" — no column header names
an entity and the marker line need not appear. Both mechanisms are silent, the page reads as
consolidated, and because it shares every label with the Group's balance sheet the spread ADDS the
Company's figures to the Group's.

The classifier already knows: ``classify._scope_of`` resolves the page's entity scope from its
title and its position relative to the notes, and writes it to ``PageSource.scope``. These tests
are about that verdict reaching the figures.
"""
from __future__ import annotations

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat
from app.core.stage import PipelineContext
from app.stages.classify import ClassifyStage
from app.stages.extract import ExtractStage
from app.stages.ingest import IngestStage


def _run(data: bytes) -> tuple[DocumentModel, list[str]]:
    doc = DocumentModel(filename="filing.pdf", fmt=DocFormat.PDF)
    ctx = PipelineContext(raw_bytes=data)
    for stage in (IngestStage(), ClassifyStage(), ExtractStage()):
        doc = stage.run(doc, ctx)
    return doc, ctx.logs


def _by_page(doc: DocumentModel) -> dict[int, dict[str, dict[tuple[str, str], object]]]:
    """{page_index: {source_label: {(basis, period): value}}} — the figures as extracted."""
    out: dict[int, dict[str, dict[tuple[str, str], object]]] = {}
    for li in doc.line_items:
        for ev in li.values.values():
            page = ev.provenance.page_index if ev.provenance else None
            if page is None:
                continue
            slot = (ev.basis.value, ev.period_label or "")
            out.setdefault(page, {}).setdefault(li.source_label, {})[slot] = ev.value
    return out


@pytest.fixture(scope="module")
def filing():
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_company_statement_after_notes_pdf

    return _run(make_company_statement_after_notes_pdf())


def test_the_classifier_already_knows_which_page_is_the_companys(filing):
    """The input to everything below, asserted separately so a failure downstream is never
    misread as the classifier having changed its mind."""
    doc, _ = filing
    scopes = {p.index: p.scope for p in doc.pages}
    assert scopes == {0: "consolidated", 1: None, 2: "company"}, scopes


def test_a_company_only_page_is_not_extracted_as_the_group(filing):
    """THE DEFECT. Both pages print "Investment properties"; the Group's is 36,683 and the
    Company's is 647. Tagged the same, they map to one canonical key and the spread adds them —
    which is how a real filing reported total non-current assets of 60,586,316 against a printed
    53,035,061."""
    doc, _ = filing
    pages = _by_page(doc)
    assert pages[0]["Investment properties"][("consolidated", "current")] == 36683
    assert pages[2]["Investment properties"][("standalone", "current")] == 647
    # No figure from the Company's page may be filed as the Group's, and vice versa.
    assert {b for slots in pages[0].values() for b, _ in slots} == {"consolidated"}
    assert {b for slots in pages[2].values() for b, _ in slots} == {"standalone"}


def test_the_page_scope_decision_is_in_the_run_log(filing):
    """A basis decision that moves numbers must be auditable from the log, the way
    ``entity_scope=two_basis_header`` and ``entity_scope=company_only`` already are."""
    _, logs = filing
    assert any("page=2" in m and "entity_scope=" in m for m in logs), [
        m for m in logs if "entity_scope" in m]


def test_the_document_reports_both_bases(filing):
    """What the Workspace reads to decide which basis tabs to offer: a filing carrying a Company
    statement genuinely has two answers, and ``periods.bases_present`` is where that shows up."""
    doc, _ = filing
    assert sorted({ev.basis.value for li in doc.line_items
                   for ev in li.values.values()}) == ["consolidated", "standalone"]


# --- the two false positives that would move numbers the wrong way -----------------------------

def test_the_filers_own_name_is_not_a_scope_marker():
    """THE BLOCKER an adversarial pass caught. ``_title_candidates`` joins the issuer-name line onto
    the statement title so a title split over two lines is matched as one, which means the text the
    scope is read from is "SUNRISE DEVELOPMENT COMPANY LIMITED BALANCE SHEET". Reading Company out
    of the filer's NAME labelled the GROUP's balance sheet as the Company's — and because the title
    path needs no notes-region gate and no corroboration, it did so on page one of any filing whose
    statements are titled without the word consolidated."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_issuer_named_untokened_face_pdf

    doc, logs = _run(make_issuer_named_untokened_face_pdf())
    face = next(p for p in doc.pages if p.statement)
    assert "COMPANY LIMITED" in str(face.evidence.get("matched_title"))
    assert face.scope is None
    # One set of figures, left where they were: the Group's by default.
    assert sorted({ev.basis.value for li in doc.line_items
                   for ev in li.values.values()}) == ["consolidated"]
    # And the refusal is stated, so a missing basis is distinguishable from a wrong one.
    assert any("entity_scope=unresolved(face_after_notes" in m for m in logs), logs


def test_position_past_the_notes_is_not_enough_on_its_own():
    """The corroboration the notes-region inference now requires. ``seen_notes`` is a latch that one
    front-matter line can set — a registered-office address matches ``_NOTE_ONE`` — so "this page is
    past the notes" is not evidence that it is the Company's. What IS evidence is that the page
    RE-presents a statement the filing already showed as the Group's, because that duplication is
    what makes two sets of figures collide on one canonical key. This filing never presents a Group
    statement, so its face page has nothing to repeat."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_issuer_named_untokened_face_pdf

    doc, _ = _run(make_issuer_named_untokened_face_pdf())
    latched = [p for p in doc.pages if p.kind.value == "notes"]
    assert latched, "the fixture must actually latch the notes walk, or it proves nothing"
    assert all(p.scope != "company" for p in doc.pages)


def test_an_untitled_continuation_page_inherits_its_runs_entity():
    """A statement names its entity once, at the top of the run — the same way it names its TYPE,
    which the classifier already carries forward. Resolved per page instead, the continuation gets
    no verdict, so a Company statement spanning two pages would have its second page fall back to
    the Group and its figures added there."""
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_consolidated_statement_spanning_two_pages_pdf

    doc, logs = _run(make_consolidated_statement_spanning_two_pages_pdf())
    assert [p.scope for p in doc.pages] == ["consolidated", "consolidated"]
    assert doc.pages[1].evidence.get("matched_title") is None
    assert any("entity_scope=carried(consolidated)" in m for m in logs), logs


def test_a_traditional_chinese_consolidated_title_is_read_as_the_group():
    """Traditional HK usage is 綜合 = consolidated, and 綜合損益及其他全面收益表 is the GROUP's income
    statement. Refusing it as a consolidation marker because a comprehensive-income token follows
    answered the STATEMENT question inside the SCOPE test, and on a bilingual filing — whose Chinese
    statements repeat the English ones past the notes — it labelled the Group's figures as the
    Company's."""
    from app.stages.classify import _scope_of

    for title in ("綜合損益及其他全面收益表", "綜合全面收益表", "綜合財務狀況表"):
        assert _scope_of(title, [], 1.0, repeat_after_notes=True)[0] == "consolidated", title
    # The Simplified form keeps its comprehensive-income lookahead, and 合并 is its consolidation token.
    assert _scope_of("合并资产负债表", [], 1.0)[0] == "consolidated"


def test_a_page_scope_never_relabels_a_note():
    """``PageSource.scope`` is assigned inside the classifier's face branch and describes a
    STATEMENT. A note listing the Company's investments in subsidiaries belongs to the consolidated
    statements it is a note TO, so relabelling its basis would break the note-to-face tie."""
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word, build_line_items

    words = [Word(text="Investments in subsidiaries", bbox=BBox(x0=0.1, y0=0.5, x1=0.4, y1=0.52)),
             Word(text="6,826", bbox=BBox(x0=0.7, y0=0.5, x1=0.78, y1=0.52))]
    logs: list[str] = []
    items, _ = build_line_items(words, page_index=7, document_id=None, source_kind="native",
                                on_face=False, page_scope="company", log=logs.append)
    assert items, "the fixture must produce a row, or it proves nothing"
    assert {ev.basis.value for i in items for ev in i.values.values()} == {"consolidated"}
    assert not any("entity_scope" in m for m in logs), logs


# --- consumers that could assume one basis and now cannot ---------------------------------------
#
# Every one of these indexed extracted data by canonical_key LAST-WINS. That was harmless while a
# filing carried one basis: the Group's row was the only row, so which one survived did not matter.
# Labelling the Company's statement makes a key genuinely held by two rows, and last-wins then picks
# by page order — the Company's page comes after the Group's — so the consolidated answer, the one
# every default view asks for, is the one that goes missing.

def test_the_balance_identity_is_checked_on_both_bases():
    """``confidence`` kept one LineItem per key, so the identity was checked against whichever page
    came last and the other basis got no validation signal at all — a Group balance sheet that does
    not balance would pass unflagged because the Company's did."""
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.stages.confidence import ConfidenceStage, _ASSETS, _EQ_LIAB

    def item(key: str, basis: Basis, value: str) -> LineItem:
        li = LineItem(canonical_key=key, source_label=key, ordinal=0)
        li.values[f'{{"basis":"{basis.value}","period_end":null,"period_label":"current"}}'] = (
            ExtractedValue(value=value, basis=basis, period_label="current"))
        return li

    # The Group's identity is BROKEN; the Company's holds. Company rows last, as page order puts them.
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [item(_ASSETS, Basis.CONSOLIDATED, "100"),
                      item(_EQ_LIAB, Basis.CONSOLIDATED, "140"),
                      item(_ASSETS, Basis.STANDALONE, "50"),
                      item(_EQ_LIAB, Basis.STANDALONE, "50")]
    ConfidenceStage().run(doc, PipelineContext())

    signals = {(li.canonical_key, ev.basis.value): (ev.confidence.validation, ev.confidence.flags)
               for li in doc.line_items for ev in li.values.values()}
    assert signals[(_ASSETS, "consolidated")][0] == 0.4
    assert "balance_mismatch" in signals[(_ASSETS, "consolidated")][1]
    assert signals[(_ASSETS, "standalone")][0] == 1.0


def test_a_company_row_does_not_drop_a_netting_rule():
    """``netting`` indexed rows last-wins while its value lookup is basis-exact, so once a Company
    row shared a key the consolidated target resolved to None — and a rule whose target cannot be
    read is dropped for the whole run, indistinguishably from a rule that did not apply."""
    from app.services.netting import _value

    rows = [
        {"canonical_key": "bs_ca__cash", "source_label": "Cash and cash equivalents",
         "values": [{"basis": "consolidated", "period_label": "current", "value": "3026571"}]},
        {"canonical_key": "bs_ca__cash", "source_label": "Cash and cash equivalents",
         "values": [{"basis": "standalone", "period_label": "current", "value": "14394"}]},
    ]
    by_key = {}
    for r in rows:
        by_key.setdefault(r["canonical_key"], []).append(r)
    assert _value(by_key, "bs_ca__cash", "consolidated", "current") == 3026571
    assert _value(by_key, "bs_ca__cash", "standalone", "current") == 14394


def test_a_company_row_does_not_zero_the_groups_figures_in_the_commentary():
    """``commentary`` built a last-wins index and read it through ``derived._value``, whose miss is
    turned into a hard 0.0 by the caller's ``or 0.0``. So the Company's row displacing the Group's
    did not make a ratio unavailable — it made it a ratio computed off zero."""
    from app.services.derived import _group_by_key, _value

    rows = [
        {"canonical_key": "pl_income__revenue", "source_label": "TURNOVER",
         "values": [{"basis": "consolidated", "period_label": "current", "value": "4995768"}]},
        {"canonical_key": "pl_income__revenue", "source_label": "TURNOVER",
         "values": [{"basis": "standalone", "period_label": "current", "value": "0"}]},
    ]
    grouped = _group_by_key(rows)
    assert _value(grouped, "pl_income__revenue", "consolidated", "current") == 4995768.0
    # Last-wins is what this replaces: it would answer for the standalone row only.
    last_wins = {r["canonical_key"]: r for r in rows}
    assert _value(last_wins, "pl_income__revenue", "consolidated", "current") is None
