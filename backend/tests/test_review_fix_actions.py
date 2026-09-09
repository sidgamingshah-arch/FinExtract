"""Which findings offer a mechanical fix, and what happens after one is applied.

"Apply fix" could never be honest for every check: a balance-identity mismatch has two sides and
the arithmetic cannot say which is wrong, while a sign anomaly names exactly one figure. So exactly
one check type carries a ``fix_action`` — a structural relation whose ``sign_suspect`` resolves to a
single contributing, unedited, non-zero, non-calculated row — and every other type carries ``null``
with prose instead of a control that either does nothing or invents an answer.

The other half of the honesty is what the card does AFTER the fix: ``run.result["structural"]`` is
written once by the pipeline and nothing recomputes it on an edit, so the relation is not
re-evaluated until the next extraction and the card does NOT disappear. That is asserted here
explicitly, because a card that survives its own fix looks like a button that did nothing unless it
says why.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fitz")

from tests.fixtures.generate import make_native_pdf

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"

_TARGET = "bs_current_assets__total_current_assets"
_SUSPECT = "bs_current_assets__inventories"
_OTHER = "bs_current_assets__trade_receivables"


@pytest.fixture(scope="module")
def template_def() -> dict:
    return json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))


def _row(key, cur, label=None, **extra):
    r = {"canonical_key": key, "source_label": label or key,
         "values": [{"basis": "consolidated", "period_label": "current", "value": str(cur)}]}
    r.update(extra)
    return r


def _structural(**detail_overrides) -> list[dict]:
    """One failed template relation, with the sign-suspect hypothesis the engine emits."""
    details = {"target": _TARGET, "components": [_SUSPECT, _OTHER],
               "op": "sum", "statement": "balance_sheet", "basis": "consolidated",
               "period_label": "current", "tolerance": 1, "severity": "warning",
               "component_values": {_SUSPECT: "-2200", _OTHER: "1310"},
               "assumed_zero": [], "sign_suspect": _SUSPECT}
    details.update(detail_overrides)
    return [{"rule_id": f"rollup:{_TARGET}", "kind": "rollup", "status": "fail",
             "scope_key": "consolidated/current", "expected": -890, "actual": 3510,
             "details": details}]


def _rows(inventories=-2200, **extra):
    return [_row(_SUSPECT, inventories, "Inventories", **extra),
            _row(_OTHER, 1310, "Trade receivables"),
            _row(_TARGET, 3510, "Total current assets")]


def _structural_card(rows, template_def, structural=None):
    from app.api.routes.documents import _build_review

    checks = _build_review(rows, "d.pdf", "en", [], structural or _structural(),
                           template_def)["checks"]
    return next(c for c in checks if c["type"] == "structural")


def test_a_single_sign_suspect_yields_a_flip_action_naming_the_figure_it_changes(template_def):
    card = _structural_card(_rows(), template_def)
    fix = card["fix_action"]
    assert fix == {"kind": "flip_sign", "canonical_key": _SUSPECT, "basis": "consolidated",
                   "period": "current", "label": "Inventories",
                   "from": -2200.0, "to": 2200.0,
                   "from_display": "-2,200", "to_display": "2,200",
                   "comment": fix["comment"]}
    # The comment names the rule and the concept, so `edit_comments` records WHY the sign moved.
    assert f"rollup:{_TARGET}" in fix["comment"] and _SUSPECT in fix["comment"]
    # Nothing is formatted in the browser: both displays come from the server.
    assert fix["to_display"] == f"{fix['to']:,.0f}"


def test_no_flip_when_the_engine_named_no_suspect(template_def):
    """`_sign_suspect` declines to name a candidate when two tie — a wrong pointer is worse than
    none. That judgement is reused, never re-derived here."""
    assert _structural_card(_rows(), template_def,
                            _structural(sign_suspect=None))["fix_action"] is None


def test_no_flip_when_two_printed_lines_map_to_the_suspect_concept(template_def):
    """PATCH replaces a multi-row concept's SUM with the typed figure, so flipping a composed
    concept destroys the composition — and which printed line carries the wrong sign is precisely
    what a human has to decide."""
    rows = _rows() + [_row(_SUSPECT, -300, "Consumables")]
    assert _structural_card(rows, template_def)["fix_action"] is None


def test_no_flip_when_the_slot_already_carries_a_typed_figure(template_def):
    """A machine flip must never overwrite a figure an analyst entered — which also makes the
    button un-clickable twice. A revert brings it back."""
    edited = _rows(edited=True, edited_slots=["consolidated/current"])
    assert _structural_card(edited, template_def)["fix_action"] is None


def test_no_flip_without_a_template(template_def):
    """Without the template the calculated-subtotal exclusion cannot be tested at all, and a fix
    that cannot be checked is not offered."""
    assert _structural_card(_rows(), None)["fix_action"] is None


def test_no_flip_on_a_template_calculated_subtotal(template_def):
    """Flipping a calculated subtotal writes an override the rollup then honours, papering over
    the mis-signed component that is the actual defect."""
    rows = [_row(_TARGET, -3510, "Total current assets"), _row(_OTHER, 1310)]
    assert _structural_card(rows, template_def,
                            _structural(sign_suspect=_TARGET))["fix_action"] is None
    # Positive control on the SAME rows: being calculated is the only thing that refused it.
    plain = _structural_card(rows, template_def, _structural(sign_suspect=_OTHER))
    assert plain["fix_action"]["canonical_key"] == _OTHER


def test_no_flip_when_the_suspect_has_no_figure_or_a_zero_one(template_def):
    assert _structural_card([_row(_OTHER, 1310)], template_def)["fix_action"] is None
    assert _structural_card(_rows(inventories=0), template_def)["fix_action"] is None


def test_no_other_check_type_offers_a_fix(template_def):
    """balance, calculated_mismatch and unmapped all carry null — none of them implies a single edit.

    Two kinds are absent from that list because they are no longer raised at all: ``note_tie``, and
    ``low_confidence`` (a mapping's strength is not a review finding). ``uncomputed`` is gone too, for
    the same reason — see tests/test_calculated_and_gaps.py.
    """
    from app.api.routes.documents import _build_review

    rows = [
        # balance: 100 vs 90
        _row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90),
        # calculated_mismatch on the template's current-assets subtotal
        _row(_SUSPECT, 2200), _row(_OTHER, 1310), _row(_TARGET, 9999),
        {"source_label": "Unplaceable", "canonical_key": None,
         "values": [{"basis": "consolidated", "period_label": "current", "value": "5"}]},
        # A template line, weakly matched — kept in the input on purpose. It raises no card, and this
        # test asserts over every card there is, so leaving it in proves the weak row adds none.
        {"source_label": "A shaky match",
         "canonical_key": "bs_current_assets__cash_and_cash_equivalents",
         "mapping_confidence": 0.2, "flags": ["low_mapping_confidence"],
         "values": [{"basis": "consolidated", "period_label": "current", "value": "7"}]},
    ]
    recon = [{"note_number": "9", "basis": "consolidated", "period_label": "current",
              "raw_face": 1000, "residual": 20, "within_tolerance": False}]
    checks = _build_review(rows, "d.pdf", "en", recon, [], template_def)["checks"]
    types = {c["type"] for c in checks}
    assert {"balance", "unmapped", "calculated_mismatch"} <= types
    # The three retired kinds, none of which may come back through this path.
    assert not types & {"note_tie", "low_confidence", "uncomputed", "off_template"}
    for c in checks:
        assert c["fix_action"] is None, c["type"]
        # …and nothing but a structural card can claim edited inputs.
        assert c["inputs_edited"] is False and c["inputs_edited_note"] == ""


def test_an_edited_input_is_declared_on_the_card_rather_than_recomputing_the_relation(
        template_def):
    """Covers a Workspace edit exactly as it covers the flip button: the relation is not
    re-evaluated until the next extraction, so the card says so."""
    edited = _rows(edited=True, edited_slots=["consolidated/current"])
    card = _structural_card(edited, template_def)
    assert card["inputs_edited"] is True
    assert card["inputs_edited_keys"] == [_SUSPECT]
    assert "re-evaluated on the next extraction" in card["inputs_edited_note"]

    clean = _structural_card(_rows(), template_def)
    assert clean["inputs_edited"] is False and clean["inputs_edited_keys"] == []
    assert clean["inputs_edited_note"] == ""


def test_the_flip_the_server_prescribes_is_an_ordinary_edit_and_is_revertible(client):
    """There is no new mutation endpoint: the client sends the fix_action to the SAME PATCH every
    other edit uses, so the flip snapshots `_original` (the existing DELETE reverts it) and records
    its reason in `edit_comments`. After it lands the structural card is still there, now carrying
    inputs_edited — the test asserts that it does NOT disappear."""
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("flip.pdf", make_native_pdf(),
                                         "application/pdf")}).json()["id"]
    # Line items is the single configuration engine: ``GET /ontologies`` and the run's
    # ``ontology_version_id`` pin are gone with the ontology store, so the shipped
    # configuration is read from ``/line-items/versions`` and pinned as a
    # ``line_item_version_id``.
    from app.sample.reference import shipped_line_items_key

    cfgs = client.get("/api/v1/line-items/versions").json()
    cfg = next(c for c in cfgs if c["line_items_key"] == shipped_line_items_key())
    tpl = next(t for t in client.get("/api/v1/templates").json()
               if t["template_key"] == cfg["target_template_key"])
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    for _ in range(200):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        time.sleep(0.05)

    # Force the one situation a flip is offered for, on the stored run: a failed relation whose
    # single sign suspect is a plain, unedited, non-zero, non-calculated row.
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter(
            ExtractionRun.document_id == doc_id).one()
        result = dict(run.result)
        rows = [r for r in result["rows"] if r.get("canonical_key") not in (_SUSPECT, _OTHER)]
        rows += [_row(_SUSPECT, -2200, "Inventories"), _row(_OTHER, 1310, "Trade receivables")]
        result["rows"] = rows
        result["structural"] = _structural()
        run.result = result
        session.commit()

    card = next(c for c in client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"]
                if c["type"] == "structural")
    fix = card["fix_action"]
    assert fix and fix["canonical_key"] == _SUSPECT

    patched = client.patch(f"/api/v1/documents/{doc_id}/line-items/{fix['canonical_key']}",
                          json={"value": fix["to"], "formula": "", "basis": fix["basis"],
                                "period": fix["period"], "comment": fix["comment"]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["current"] == fix["to"]
    assert patched.json()["comment"] == fix["comment"]

    after = next(c for c in client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"]
                 if c["type"] == "structural")
    assert after["subject_key"] == card["subject_key"]      # the card did NOT vanish
    assert after["fix_action"] is None                      # and cannot be clicked twice
    assert after["inputs_edited"] is True
    assert _SUSPECT in after["inputs_edited_keys"] and after["inputs_edited_note"]

    # The existing revert undoes it, because the flip was an ordinary edit.
    assert client.delete(
        f"/api/v1/documents/{doc_id}/line-items/{fix['canonical_key']}").status_code == 200
    back = next(c for c in client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"]
                if c["type"] == "structural")
    assert back["inputs_edited"] is False
    assert back["fix_action"] == fix
