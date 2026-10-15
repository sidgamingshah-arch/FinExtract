"""Which findings offer a mechanical fix, and what happens after one is applied.

"Apply fix" could never be honest for every check: a balance-identity mismatch has two sides and
the arithmetic cannot say which is wrong, while a sign anomaly names exactly one figure. So exactly
one check type carries a ``fix_action`` — a structural relation whose ``sign_suspect`` resolves to a
single contributing, unedited, non-zero, non-calculated row — and every other type carries ``null``
with prose instead of a control that either does nothing or invents an answer.

The other half of the honesty is what the card does AFTER the fix. The review route evaluates the
relations again from the rows as they now stand (``documents._structural_for_run``), so a flip that
makes the relation hold takes its card away, and a revert brings it back. A relation served as the
run STORED it (``_build_review`` handed a stored list, or a run whose configuration no longer
loads) is not re-evaluated until the next extraction, and its card says so rather than looking like
a button that did nothing.
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


def test_an_edited_input_is_declared_on_a_stored_relations_card(template_def):
    """A relation served as stored is not re-evaluated until the next extraction, so its card says
    so — for a Workspace edit exactly as for the flip button."""
    edited = _rows(edited=True, edited_slots=["consolidated/current"])
    card = _structural_card(edited, template_def)
    assert card["inputs_edited"] is True
    assert card["inputs_edited_keys"] == [_SUSPECT]
    assert "re-evaluated on the next extraction" in card["inputs_edited_note"]

    clean = _structural_card(_rows(), template_def)
    assert clean["inputs_edited"] is False and clean["inputs_edited_keys"] == []
    assert clean["inputs_edited_note"] == ""


def test_on_a_relation_evaluated_from_the_rows_an_edit_is_said_to_be_in_it(template_def):
    """Evaluated from the rows as they now stand, the relation already carries the edit: the card
    says it is evaluated with the edited figure, not that it waits for the next extraction."""
    from app.api.routes.documents import _EDITED_INPUT_LIVE, _build_review

    edited = _rows(edited=True, edited_slots=["consolidated/current"])
    card = next(c for c in _build_review(edited, "d.pdf", "en", [], _structural(), template_def,
                                         structural_live=True)["checks"]
                if c["type"] == "structural")
    assert card["inputs_edited"] is True and card["inputs_edited_keys"] == [_SUSPECT]
    assert card["inputs_edited_note"] == _EDITED_INPUT_LIVE
    assert "next extraction" not in card["inputs_edited_note"]


def _seeded_run(template_def: dict, rows: list[dict]) -> str:
    """A document whose latest run is these rows against this template, and no line-item set."""
    import uuid

    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as s:
        doc = Document(filename="flip.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"flip-{uuid.uuid4().hex[:8]}", name="Flip",
                             version=1, definition=template_def)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id},
                            result={"rows": rows, "filename": "flip.pdf", "pages": []}))
        s.commit()
        return doc.id


def _relation_card(review: dict) -> dict | None:
    return next((c for c in review["checks"] if c["type"] == "structural"
                 and c["target"] == _TARGET), None)


def test_the_flip_the_server_prescribes_is_an_ordinary_edit_and_is_revertible(client,
                                                                              template_def):
    """There is no new mutation endpoint: the client sends the fix_action to the SAME PATCH every
    other edit uses, so the flip snapshots `_original` (the existing DELETE reverts it) and records
    its reason in `edit_comments`. The relation is evaluated again from the edited rows, so once the
    flip lands the relation holds and its card is gone; the revert brings the card back.

    Nothing is written into ``result["structural"]``: the route evaluates the relation from the
    rows — a current-assets section printed with inventories mis-signed, where flipping inventories
    is the one sign change that makes the printed total hold."""
    doc_id = _seeded_run(template_def, _rows())

    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    card = _relation_card(review)
    assert card is not None, [c["id"] for c in review["checks"]]
    assert review["coverage"]["evaluated_from"] == "rows"
    fix = card["fix_action"]
    assert fix and fix["canonical_key"] == _SUSPECT and fix["to"] == 2200.0

    patched = client.patch(f"/api/v1/documents/{doc_id}/line-items/{fix['canonical_key']}",
                          json={"value": fix["to"], "formula": "", "basis": fix["basis"],
                                "period": fix["period"], "comment": fix["comment"]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["current"] == fix["to"]
    assert patched.json()["comment"] == fix["comment"]

    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert _relation_card(after) is None                    # the relation holds now
    assert after["coverage"]["aggregate"]["failed"] < review["coverage"]["aggregate"]["failed"]

    # The existing revert undoes it, because the flip was an ordinary edit.
    assert client.delete(
        f"/api/v1/documents/{doc_id}/line-items/{fix['canonical_key']}").status_code == 200
    back = _relation_card(client.get(f"/api/v1/documents/{doc_id}/review").json())
    assert back is not None and back["inputs_edited"] is False
    assert back["fix_action"] == fix
    assert back["subject_key"] == card["subject_key"]
