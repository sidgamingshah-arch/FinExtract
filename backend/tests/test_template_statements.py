"""The front end's statement tabs are the TEMPLATE's, not a list held in the client.

THE DEFECT THIS CLOSES. The Workspace offered exactly three tabs — balance sheet, P&L, cash flow —
regardless of what the configured template declared. Upload a template with no cash flow and the
Cash flow tab stayed, rendering an empty grid an analyst cannot tell from a filing that omits the
statement. Upload one WITH changes in equity and no tab appeared, because the entry point was
suppressed on the grounds that the statement "is not part of the reviewed set" — which is exactly
the judgement a template exists to record.

Read off the template the RUN was pinned to rather than the newest one stored, so publishing a
template cannot re-tab a spread that already exists. That is the same rule ``_template_for_run``
holds for findings and labels, applied to the tabs.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest

from app.services.statements import ORDER, TITLES, declared_statements

_SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def shipped() -> dict:
    return json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text())


def _tpl(*statements: tuple[str, int]) -> dict:
    """A template declaring exactly these statements, each with `n` sections."""
    return {"template_key": "t", "name": "t", "statements": [
        {"type": st, "sections": [{"node_id": f"{st}_s{i}", "canonical_key": f"{st}_s{i}",
                                   "label": f"{st} {i}", "role": "header"}
                                  for i in range(n)]}
        for st, n in statements]}


# --- the vocabulary is shared, not copied ------------------------------------------------------

def test_the_importer_reads_the_same_statement_list_the_api_serves():
    """A screen offering a statement the workbook importer would refuse is a disagreement with no
    owner. They are the same object, so they cannot drift."""
    from app.services.template_xlsx import _STATEMENT_TITLE

    assert _STATEMENT_TITLE is TITLES


def test_the_shipped_template_declares_the_three_the_client_used_to_hardcode(shipped):
    """Why this change is invisible today and still worth making: the shipped template happens to
    declare exactly the three the client hardcoded, so nothing moves until a template differs."""
    assert [s["key"] for s in declared_statements(shipped)] == [
        "balance_sheet", "profit_and_loss", "cash_flow"]
    assert all(s["sections"] > 0 for s in declared_statements(shipped))


# --- what a differing template gets ------------------------------------------------------------

def test_a_template_without_cash_flow_offers_no_cash_flow_tab():
    """The empty-grid case, which is the one an analyst reports as a bug."""
    keys = [s["key"] for s in declared_statements(_tpl(("balance_sheet", 3), ("profit_and_loss", 2)))]
    assert keys == ["balance_sheet", "profit_and_loss"]


def test_a_template_declaring_changes_in_equity_does_offer_it():
    """The other direction, and the deliberate suppression this replaces. The statement is offered
    because a template asked for it, not because the client was told to stop hiding it."""
    keys = [s["key"] for s in declared_statements(
        _tpl(("balance_sheet", 3), ("changes_in_equity", 2)))]
    assert keys == ["balance_sheet", "changes_in_equity"]


def test_the_order_is_the_templates_own_not_this_modules():
    """The template is the authority on presentation — the rule the rulebook's concept order already
    follows. A template that prints its income statement first is served that way."""
    keys = [s["key"] for s in declared_statements(
        _tpl(("profit_and_loss", 2), ("cash_flow", 2), ("balance_sheet", 3)))]
    assert keys == ["profit_and_loss", "cash_flow", "balance_sheet"]
    assert keys != [k for k in ORDER if k in keys], "served in the module's order, not the template's"


def test_a_statement_with_no_sections_is_not_offered():
    """It cannot render a grid, and a tab that can only ever be empty is worse than no tab: the
    analyst cannot tell "the filing did not state it" from "the template has nothing to put there"."""
    keys = [s["key"] for s in declared_statements(_tpl(("balance_sheet", 3), ("cash_flow", 0)))]
    assert keys == ["balance_sheet"]


def test_a_malformed_definition_yields_nothing_rather_than_a_broken_tab():
    """An empty list is the client's signal to fall back to its built-in set, so garbage must reach
    it as "cannot say" rather than as a tab keyed on nonsense."""
    assert declared_statements(None) == []
    assert declared_statements({}) == []
    assert declared_statements({"statements": "not a list"}) == []
    assert declared_statements({"statements": [{"type": "chairman_letter",
                                               "sections": [{"node_id": "x"}]}]}) == []
    # One statement declared twice is served once.
    assert [s["key"] for s in declared_statements(
        _tpl(("balance_sheet", 2), ("balance_sheet", 3)))] == ["balance_sheet"]


# --- the endpoint ------------------------------------------------------------------------------

def _run_with_template(template_def: dict | None) -> str:
    """A document with a stored succeeded run pinned to `template_def`."""
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as session:
        tid = None
        if template_def is not None:
            tv = TemplateVersion(template_key=template_def["template_key"], name="t", version=1,
                                 definition=template_def)
            session.add(tv)
            session.flush()
            tid = tv.id
        doc = Document(filename="tabs.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                       status="extracted")
        session.add(doc)
        session.flush()
        session.add(ExtractionRun(document_id=doc.id, status="succeeded",
                                  template_version_id=tid, options={},
                                  result={"rows": [], "filename": "tabs.pdf"}))
        session.commit()
        return doc.id


def test_the_run_endpoint_names_the_statements_its_own_template_declares(client):
    doc_id = _run_with_template(_tpl(("profit_and_loss", 2), ("changes_in_equity", 1)))
    body = client.get(f"/api/v1/documents/{doc_id}/run").json()

    assert [s["key"] for s in body["statements"]] == ["profit_and_loss", "changes_in_equity"]
    assert body["statements"][0]["title"] == "Profit & loss"


def test_a_run_with_no_template_says_so_instead_of_guessing_one(client):
    """`_template_for_run` deliberately has no fallback — a spread laid out on a template the
    analyst never chose is worse than one that says nothing. An empty list tells the client to use
    its own built-in set, which is a different thing from "this template has no statements"."""
    doc_id = _run_with_template(None)
    assert client.get(f"/api/v1/documents/{doc_id}/run").json()["statements"] == []


# --- the whole path: upload a template, extract, read the tabs ---------------------------------

def test_a_template_declaring_the_equity_statement_is_accepted_at_all():
    """THE DEFECT THIS CLOSES, and it made one of the four statements un-uploadable.

    ``StatementType`` spells the statement ``equity_changes``; the page classifier, the workbook
    importer's `Statement` column, the API and the front end all say ``changes_in_equity``.
    ``mapping.normalize_statement`` folded the two for COMPARISON — so a rulebook authored either way
    scoped correctly — but nothing folded them on the way IN. A template workbook with a "Changes in
    equity" row passed the importer's own validation and was then refused by the schema with an enum
    error naming four values, one of which the importer never produces. So the tabs could never align
    to an equity sheet, because an equity sheet could not be stored.

    Asserted against the schema directly rather than through ``POST /templates``: publishing a rival
    version of the shipped template key inside a session-scoped database changes which template later
    tests resolve, and this seam does not need a publish to be proven.
    """
    from app.schemas.loader import load_template

    definition = {"template_key": "eq_probe", "name": "eq", "statements": [
        {"type": "changes_in_equity", "sections": [{
            "node_id": "eq_s1", "canonical_key": "eq_s1", "label": "Movements", "role": "header",
            "children": [{"node_id": "eq_sc", "canonical_key": "eq_share_capital",
                          "label": "Share capital", "role": "line"}]}]}]}
    loaded = load_template(definition)

    # Stored under the enum's value, which is what a definition validates to…
    assert loaded.statements[0].type.value == "equity_changes"
    # …and served to the client under the spelling every other part of the system uses.
    assert [st["key"] for st in declared_statements(definition)] == ["changes_in_equity"]


def test_the_two_spellings_reach_the_same_served_statement():
    """Either spelling in a stored definition serves one key, so a template authored through the
    workbook and one authored through the API cannot produce two different tabs."""
    from_enum = declared_statements(_tpl(("changes_in_equity", 1)))
    assert [s["key"] for s in from_enum] == ["changes_in_equity"]
    stored_as_enum = {"statements": [{"type": "equity_changes",
                                      "sections": [{"node_id": "x"}]}]}
    assert [s["key"] for s in declared_statements(stored_as_enum)] == ["changes_in_equity"]
