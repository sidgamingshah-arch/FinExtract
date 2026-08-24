"""A statement whose rows carry one basis is served for either basis, and says which it served.

THE DEFECT THIS CLOSES. ``basis_values`` filters a row's values to the requested basis, so a
statement whose rows were all labelled one way returned NOTHING when the other was asked for. The
Workspace opens on Consolidated, so a filing extracted as company-only rendered an empty grid with
its figures one tab away — and an empty grid is indistinguishable from a statement the filing does
not contain. The endpoint's own comment described this as intended ("empty if the source didn't
present that basis"), which is why it survived.

WHAT IS NOT DONE, and the reason: the single basis is served under ITS OWN name, not relabelled
consolidated. A filing that prints only the Company's balance sheet is a company-only statement, and
captioning those figures Consolidated would state something false about a real number — the same
mistake ``company_only_markers`` exists in ``row_reconstruct`` to prevent, arrived at from the
display side. A document that identified no basis at all is already tagged consolidated at
extraction (``_basis_for`` returns it when no band is found), so that case needs nothing here.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.periods import bases_present, effective_basis


def _row(key: str, *bases: str) -> dict:
    return {"id": key, "source_label": key, "canonical_key": key, "role": "line",
            "values": [{"basis": b, "period_label": "current", "value": "100",
                        "provenance": None, "confidence": {}} for b in bases]}


# --- the resolver ------------------------------------------------------------------------------

def test_the_requested_basis_wins_whenever_it_holds_anything():
    rows = [_row("bs_current_assets__inventories", "consolidated", "standalone")]
    assert effective_basis(rows, "consolidated") == ("consolidated", "requested")
    assert effective_basis(rows, "standalone") == ("standalone", "requested")


def test_the_only_basis_in_the_document_answers_the_consolidated_view():
    """The fix. One basis means the filing drew no distinction to filter on, so the consolidated
    view — the default, and the reading a filing gets when nothing says otherwise — shows it."""
    rows = [_row("bs_current_assets__inventories", "standalone")]
    assert effective_basis(rows, "consolidated") == ("standalone", "only_basis_in_document")


def test_a_genuine_two_basis_filing_never_borrows_the_other_basis():
    """A page printing Group and Company side by side has two answers, and serving the Company's
    figures to someone who asked for the Group's is a wrong number, not a helpful one.

    Note WHERE that safety comes from: the first test in the resolver, not the count. With two bases
    in the vocabulary a filing carrying both always satisfies the request and returns before the
    fallback is reached — which is why the count below is tested separately rather than through this
    case, where it is unreachable."""
    rows = [_row("a", "consolidated"), _row("b", "standalone")]
    assert effective_basis(rows, "consolidated") == ("consolidated", "requested")
    assert effective_basis(rows, "standalone") == ("standalone", "requested")
    assert bases_present(rows) == ["consolidated", "standalone"]


def test_more_than_one_unrequested_basis_is_refused_rather_than_picked_between():
    """What the count actually holds, exercised where it is reachable.

    Past the early return the requested basis is absent, so today at most one other can exist and
    the count can only be 0 or 1 — it is not the two-basis guard it reads like. It earns its place
    against a vocabulary that grows: with two bases the caller did not ask for, there is no single
    "the only basis in the document", and an arbitrary pick between them would put figures on the
    face that the reader cannot attribute. An empty answer is the truthful one.
    """
    rows = [_row("a", "standalone"), _row("b", "combined")]
    assert bases_present(rows) == ["combined", "standalone"]
    assert effective_basis(rows, "consolidated") == ("consolidated", "requested")
    # …while ONE unrequested basis still substitutes, so the guard is not just refusing everything.
    assert effective_basis([_row("a", "standalone")], "consolidated") == (
        "standalone", "only_basis_in_document")


def test_a_value_with_no_basis_counts_as_consolidated():
    """The same reading ``basis_values`` uses, so the resolver cannot disagree with the filter."""
    rows = [{"values": [{"period_label": "current", "value": "1"}]}]
    assert bases_present(rows) == ["consolidated"]
    assert effective_basis(rows, "consolidated") == ("consolidated", "requested")


def test_asking_for_standalone_is_never_answered_with_the_groups_figures():
    """THE ASYMMETRY, and the reason the fallback is one-directional. Consolidated is the default
    view and the reading a filing gets when nothing says otherwise. Standalone is never a default:
    clicking it asks for the COMPANY's figures specifically, so answering with the Group's would be
    a wrong number. That request keeps its existing named refusal — ``basis_not_extracted``, which
    the grid states rather than showing a blank (see ``test_no_template_additions``)."""
    consolidated_only = [_row("bs_current_assets__inventories", "consolidated")]
    assert effective_basis(consolidated_only, "standalone") == ("standalone", "requested")


def test_nothing_extracted_leaves_the_request_alone():
    assert effective_basis([], "standalone") == ("standalone", "requested")


# --- through the endpoint ----------------------------------------------------------------------

def _seed(rows: list[dict]) -> str:
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun

    init_db()
    with SessionLocal() as session:
        doc = Document(filename="basis.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                       status="extracted")
        session.add(doc)
        session.flush()
        session.add(ExtractionRun(document_id=doc.id, status="succeeded", options={},
                                  result={"rows": rows, "filename": "basis.pdf"}))
        session.commit()
        return doc.id


def _grid(client, doc_id: str, basis: str) -> dict:
    return client.get(f"/api/v1/documents/{doc_id}/statement"
                      f"?statement=balance_sheet&basis={basis}").json()


def test_a_company_only_statement_is_no_longer_empty_on_the_consolidated_tab(client):
    """The reported symptom, end to end through the endpoint the Workspace calls."""
    doc_id = _seed([_row("bs_current_assets__inventories", "standalone")])
    body = _grid(client, doc_id, "consolidated")

    assert [r["v1"] for r in body["rows"] if r.get("v1") is not None] == [100.0]
    assert body["basis"] == "standalone"
    assert body["basis_requested"] == "consolidated"
    assert body["basis_substituted"] is True
    assert body["basis_reason"] == "only_basis_in_document"


def test_the_substitution_is_declared_rather_than_silent(client):
    """Serving the Company's figures under a tab captioned Consolidated without saying so would
    mislabel a real number — worse than the empty grid this replaces. The payload names both the
    basis asked for and the basis served, and the viewer chip carries the served one."""
    doc_id = _seed([_row("bs_current_assets__inventories", "standalone")])
    body = _grid(client, doc_id, "consolidated")

    assert body["basis"] != body["basis_requested"]
    assert [c["label"] for c in body["viewer"]["chips"]] == ["Standalone"]


def test_asking_for_the_basis_the_document_has_reports_no_substitution(client):
    doc_id = _seed([_row("bs_current_assets__inventories", "consolidated")])
    body = _grid(client, doc_id, "consolidated")

    assert body["basis_substituted"] is False and body["basis_reason"] == "requested"
    assert [r["v1"] for r in body["rows"] if r.get("v1") is not None] == [100.0]


def test_a_two_basis_document_keeps_its_two_answers(client):
    """Each tab shows its own figures and neither borrows the other's."""
    doc_id = _seed([{"id": "r", "source_label": "Inventories",
                     "canonical_key": "bs_current_assets__inventories", "role": "line",
                     "values": [
                         {"basis": "consolidated", "period_label": "current", "value": "100",
                          "provenance": None, "confidence": {}},
                         {"basis": "standalone", "period_label": "current", "value": "70",
                          "provenance": None, "confidence": {}}]}])

    con = _grid(client, doc_id, "consolidated")
    sep = _grid(client, doc_id, "standalone")
    assert [r["v1"] for r in con["rows"] if r.get("v1") is not None] == [100.0]
    assert [r["v1"] for r in sep["rows"] if r.get("v1") is not None] == [70.0]
    assert con["basis_substituted"] is False and sep["basis_substituted"] is False


# --- the run names the bases it has, so the screen opens on one -------------------------------

def test_the_run_names_the_bases_the_filing_labelled(client):
    """WHY THIS EXISTS. Serving the only basis a filing has (above) rescued the grid, but the
    Workspace still opened on Consolidated and then explained the mismatch it had just created. With
    the bases named, the screen offers what the filing contains and there is nothing to explain —
    the substitution stays as the safety net for a deep link or a stored choice.

    Read off the run's own rows, the same reading ``basis_values`` filters by, so the selector and
    the grid cannot disagree about which bases exist."""
    doc_id = _seed([_row("bs_current_assets__inventories", "standalone")])
    assert client.get(f"/api/v1/documents/{doc_id}/run").json()["bases"] == ["standalone"]


def test_a_two_basis_filing_names_both(client):
    doc_id = _seed([{"id": "r", "source_label": "Inventories",
                     "canonical_key": "bs_current_assets__inventories", "role": "line",
                     "values": [
                         {"basis": "consolidated", "period_label": "current", "value": "100",
                          "provenance": None, "confidence": {}},
                         {"basis": "standalone", "period_label": "current", "value": "70",
                          "provenance": None, "confidence": {}}]}])
    assert client.get(f"/api/v1/documents/{doc_id}/run").json()["bases"] == [
        "consolidated", "standalone"]


def test_a_run_with_nothing_extracted_names_no_basis(client):
    """An empty list is the client's signal to offer its built-in pair. It must not read as "this
    filing has no basis", which would leave the selector with nothing to show at all."""
    doc_id = _seed([])
    assert client.get(f"/api/v1/documents/{doc_id}/run").json()["bases"] == []
