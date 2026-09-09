"""Note→face reconciliation wired end-to-end (Requirement 20): the pipeline builds
face↔note links, the reconcile stage ties the note total back to the face figure and
writes the reconciled value, and the result surfaces on the notes endpoint, the export,
and the checks engine."""
from __future__ import annotations

import io
import time

import pytest

pytest.importorskip("fitz")

from tests.fixtures.generate import make_multipage_pdf


def _await_run(client, doc_id: str) -> dict:
    for _ in range(100):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            return r.json()["result"]
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


def test_reconcile_stage_links_and_ties_note_to_face():
    from app.services.documents import run_extraction

    doc, _ = run_extraction(make_multipage_pdf(), filename="multi.pdf")
    assert doc.links, "expected a face↔note link built from the 'Note 14' reference"
    assert doc.reconciliation is not None and doc.reconciliation.entries
    e = next(e for e in doc.reconciliation.entries if e.note_number == "14")
    # Face cash 1,204 == note detail 204 + 1,000 → ties, residual 0.
    assert e.raw_face == 1204 and e.residual == 0 and e.within_tolerance
    # Reconciled written back onto the face value (idempotent; from raw).
    cash = next(li for li in doc.line_items if "Cash" in li.source_label)
    ev = next(iter(cash.values.values()))
    assert ev.reconciled == 1204


def test_every_entry_names_its_face_line_by_something_that_survives_a_re_run():
    """The reconcile stage records one entry per FACE LINE, so an entry has to say which face line.

    ``face_item_id`` is a fresh UUID on every extraction, so a reader keyed on it sees every entry
    change identity each run. The review queue serves ONE card per note covering every untied face
    line on it and fingerprints that whole set, which is only possible if the entries are named by
    something stable — the canonical key, or the printed caption for a line that mapped to nothing.
    """
    from app.services.documents import run_extraction

    doc, _ = run_extraction(make_multipage_pdf(), filename="multi.pdf")
    entries = doc.reconciliation.entries
    assert entries
    assert all(e.face_key for e in entries)
    faces = {str(li.id): li for li in doc.line_items}
    for e in entries:
        face = faces[e.face_item_id]
        assert e.face_key == (face.canonical_key or face.source_label)
    # Two runs of the same filing agree on the names, and disagree on the per-run ids.
    again, _ = run_extraction(make_multipage_pdf(), filename="multi.pdf")
    assert [e.face_key for e in again.reconciliation.entries] == [e.face_key for e in entries]
    assert {e.face_item_id for e in again.reconciliation.entries} \
        != {e.face_item_id for e in entries}


def test_the_notes_screen_names_every_face_line_a_note_fails_to_tie_to():
    """The Notes screen's reconciliation sentence took the best-graded entry and printed ITS
    residual. With a note that breaks down several face lines, "the note total does not tie —
    residual 20" sat above a second face line out by 2,000,000."""
    from app.api.routes.documents import _reconciliation_text

    def ent(face_key, residual, face):
        return {"note_number": "12", "basis": "consolidated", "period_label": "current",
                "face_key": face_key, "raw_face": face, "subtracted": 0, "reconciled": face,
                "residual": residual, "within_tolerance": False, "tie_status": "untied"}

    one = _reconciliation_text([ent("bs_ca__face_a", 20, 1000)], 12)
    assert "does not tie to the face figure" in one and "20" in one
    # Even the single-entry sentence says which column it compared: reconciliation runs per (face
    # line, basis, period), so one residual with no column named is a figure a reader cannot place.
    assert "(consolidated/current)" in one

    several = _reconciliation_text([ent("bs_ca__face_a", 20, 1000),
                                    ent("bs_ca__face_b", 2_000_000, 99_000_000)], 12)
    assert "2 of the face lines" in several
    assert "bs_ca__face_a (consolidated/current) 20" in several
    assert "bs_ca__face_b (consolidated/current) 2,000,000" in several


def test_the_notes_sentence_counts_face_lines_and_not_reconciliation_entries():
    """FINDING 6. ``_TIE_PERIODS`` is ("current", "prior") and reconcile records one entry per (face
    line, note, basis, period), so an ordinary comparative filing holds TWO entries for ONE face line.
    The sentence counted entries while calling them "face lines" — "does not tie to 2 of the face lines
    it supports" over one face line, listed twice, under two different residuals, with no column named.
    """
    from app.api.routes.documents import _reconciliation_text

    def ent(period, residual):
        return {"note_number": "12", "basis": "consolidated", "period_label": period,
                "face_key": "bs_ca__trade_receivables", "raw_face": 1000, "subtracted": 0,
                "reconciled": 1000, "residual": residual, "within_tolerance": False,
                "tie_status": "untied"}

    text = _reconciliation_text([ent("current", 20), ent("prior", 5000)], 12)
    # ONE face line, on TWO columns. Both quantities are stated, each as itself.
    assert "does not tie to 1 of the face line it supports" in text
    assert "on 2 of the columns compared" in text
    # THE ASSERTION THAT FAILS WITH THE DEFECT RESTORED: the old sentence said "2 of the face lines".
    assert "2 of the face lines" not in text
    # …and each residual says which column it belongs to, so the same face line printed twice is two
    # readable facts rather than a contradiction.
    assert "bs_ca__trade_receivables (consolidated/current) 20" in text
    assert "bs_ca__trade_receivables (consolidated/prior) 5,000" in text


def test_the_notes_sentences_reconciled_clause_names_its_own_column():
    """The "Face figure X less Y … → reconciled Z" clause is taken from ``mine[0]`` — the best-graded
    entry, one column — while the residual list below it spans every column. Unlabelled, the two read
    as one statement about the note."""
    from app.api.routes.documents import _reconciliation_text

    def ent(basis, period, residual):
        return {"note_number": "12", "basis": basis, "period_label": period,
                "face_key": "bs_ca__trade_receivables", "raw_face": 1000, "subtracted": 300,
                "reconciled": 700, "residual": residual, "within_tolerance": False,
                "tie_status": "untied"}

    text = _reconciliation_text([ent("standalone", "prior", 5000),
                                 ent("consolidated", "current", 20)], 12)
    assert "reconciled 700 (consolidated/current)." in text


def test_reconciliation_surfaces_on_notes_endpoint_and_export(client):
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("multi.pdf", make_multipage_pdf(), "application/pdf")}
    ).json()["id"]
    # Line items is the single configuration engine: ``GET /ontologies`` and the run's
    # ``ontology_version_id`` pin are gone with the ontology store, so the shipped
    # configuration is read from ``/line-items/versions`` and pinned as a
    # ``line_item_version_id``.
    from app.sample.reference import shipped_line_items_key

    cfgs = client.get("/api/v1/line-items/versions").json()
    cfg = next((c for c in cfgs if c["line_items_key"] == shipped_line_items_key()), cfgs[0])
    tpls = client.get("/api/v1/templates").json()
    tpl = next((t for t in tpls if t["template_key"] == cfg["target_template_key"]), tpls[0])
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    result = _await_run(client, doc_id)
    assert result["reconciliation"], "reconciliation entries should be stored on the run"

    detail = client.get(f"/api/v1/documents/{doc_id}/notes/14").json()
    assert detail["reconciliation"] and "tie" in detail["reconciliation"].lower()

    import openpyxl
    x = client.get(f"/api/v1/documents/{doc_id}/export",
                   params={"fmt": "excel", "layout": "statement"})
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    text = " | ".join(str(v) for row in wb["Note details"].iter_rows(values_only=True)
                      for v in row if v)
    assert "Reconciliation" in text and "residual" in text.lower()


def test_tie_status_grades_a_note_against_its_face():
    """The GRADE, which is what decides whether a note tie is a finding at all.

    Asked of ``services/reconcile.py::tie_status`` — the live grader that the reconcile stage, the
    note detail route and the export all read. It used to be asked through
    ``services.checks.check_reconciliation``, an adapter that turned these entries into
    ``note_tie`` check objects; that module had no caller in the app and is deleted, and note-tie
    CARDS are not raised any more either. The three-way grade survives both, because it is what the
    stage stores on every entry and what the note detail prose is written from.
    """
    from app.services.reconcile import tie_status

    def grade(**over):
        base = {"note_number": "14", "basis": "consolidated", "period_label": "current",
                "raw_face": 1204, "residual": 0, "within_tolerance": True}
        return tie_status({**base, **over})

    # The breakdown adds up: the note IS a decomposition of the face figure, and it ties.
    assert grade() == "tied"
    # It is a decomposition and it does NOT add up — the case worth a human's time.
    assert grade(note_number="9", raw_face=1000, residual=20, within_tolerance=False) == "untied"
    # A note whose total is nowhere near the face figure is not a breakdown of it (an analysis
    # note, a segment table), so there is nothing to assert either way and nothing is claimed.
    assert grade(note_number="8", raw_face=1000, residual=250,
                 within_tolerance=False) == "unconfirmed"


def test_the_review_fixtures_reconciliation_entry_is_the_shape_the_stage_emits():
    """A FIXTURE THAT LIES REPORTS COVERAGE THAT DOES NOT EXIST — the same audit round 1's guard
    fixture failed (``_guard_row`` built a ``details.target`` ``_guard_slot`` never writes, so the
    parametrized assertion above it ran against nothing).

    The note-tie tests in ``test_review_checks``/``test_review_judgement`` hand-build entries, so this
    holds those fixtures to the real stage: no INVENTED field (a key the producer never writes would
    be read by no consumer, and the test would prove nothing), and the omissions named out loud —
    ``_recon`` leaves out ``tie_status``, which every real entry carries, so tests built on it exercise
    the legacy DERIVATION in ``services.reconcile.tie_status`` rather than the stored grade. Both paths
    are live (a run stored before the grade existed takes the first), and ``_untied`` carries the grade
    so the current path is covered too.
    """
    from app.services.documents import run_extraction

    from tests.test_review_checks import _recon
    from tests.test_review_judgement import _untied

    doc, _ = run_extraction(make_multipage_pdf(), filename="multi.pdf")
    real = {k for e in doc.reconciliation.entries
            for k in e.model_dump(mode="json")}
    assert real, "the fixture filing must produce reconciliation entries"
    for fixture in (_recon(20), _untied(20, face=1000, face_key="bs_ca__face_a")):
        assert set(fixture) <= real, set(fixture) - real
    # The omissions, stated rather than discovered: every one is a field no note-tie CARD reads.
    assert real - set(_untied(20, face=1000, face_key="k")) == {
        "face_item_id", "reconciled", "subtracted", "relationship"}
    assert "tie_status" not in _recon(20) and "tie_status" in _untied(20, face=1, face_key="k")
