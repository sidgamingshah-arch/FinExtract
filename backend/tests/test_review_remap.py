"""Resolving a row-shaped review finding by re-mapping it to a different template line.

THE GAP THIS CLOSES, reported by the user: "there is no way to resolve for review items — I should
be able to map it against a different line item." Both row-shaped cards already PROMISED it in
prose — the unmapped card's fix reads "Pick the correct template line item", the low-confidence
card's "Confirm the concept is correct or reassign it" — and the only write the screen offered was
the sign flip. A card telling the analyst to do something the product cannot do is worse than a card
that says nothing.

The properties that matter are the refusals, because the failure mode is silent: the card the
analyst was reading disappears whether the concept landed on their row or on a different one.

* an ambiguous row reference is REFUSED, never resolved to the first match;
* a target the run's template does not offer is refused, and so is a CALCULATED subtotal —
  writing a printed figure onto one produces a rollup that contradicts itself;
* the decision is RECORDED on the row (from, to, who, when, why) and the row's method and
  confidence move with it, so the finding it answered does not come back on the next fetch.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fitz")

from app.api.routes.documents import _build_review, _remap_offer, _remap_targets, _row_ref
from tests.fixtures.generate import make_unmapped_row_pdf

API = "/api/v1"
_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def template() -> dict:
    """The shipped template as the review builder receives it — a plain dict, not the model."""
    return json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text())


def _unmapped(label, value, y=0.2):
    return {"source_label": label, "canonical_key": None,
            "values": [{"basis": "consolidated", "period_label": "current", "value": str(value),
                        "provenance": {"source_kind": "native", "page_index": 0,
                                       "label_bbox": {"x0": 0.1, "y0": y, "x1": 0.4,
                                                      "y1": y + 0.017}}}]}


def _lowconf(label, key, value, y=0.3):
    return {"source_label": label, "canonical_key": key, "mapping_confidence": 0.2,
            "mapping_method": "fuzzy", "flags": ["low_mapping_confidence"],
            "values": [{"basis": "consolidated", "period_label": "current", "value": str(value),
                        "provenance": {"source_kind": "native", "page_index": 0,
                                       "label_bbox": {"x0": 0.1, "y0": y, "x1": 0.4,
                                                      "y1": y + 0.017}},
                        "confidence": {"mapping": 0.2, "flags": ["low_mapping_confidence"]}}]}


# --- what the card offers -----------------------------------------------------------------------

def test_the_row_shaped_card_carries_a_remap_offer_and_no_other_card_does(template):
    """``unmapped`` is the ONE row-shaped kind, and it is the only kind that offers a re-map.

    A weak mapping used to be a second one (``low_confidence``) and a mapping onto a concept the
    template declares nowhere a third (``off_template``). The first is no longer a finding at all; the
    second is raised as ``unmapped`` — the same category, reached the other way — and that path is
    what the second half of this test covers, because it is the case where the offer has a
    ``current_key`` to pre-select.
    """
    rows = [_unmapped("Deposits paid for acquisition of land", 60),
            # Mapped, confidently, onto a concept the template puts on no statement: it reaches no
            # line of the spread, so it is the other way into the unmapped category.
            {"source_label": "Sundry receivables", "canonical_key": "bs_ca__not_in_this_template",
             "mapping_confidence": 0.95,
             "values": [{"basis": "consolidated", "period_label": "current", "value": "25"}]}]
    review = _build_review(rows, "d.pdf", "en", template_def=template)
    cards = [c for c in review["checks"] if c["type"] == "unmapped"]
    by_label = {c["title"]: c for c in cards}
    assert set(by_label) == {"Deposits paid for acquisition of land", "Sundry receivables"}

    for card in cards:
        offer = card["remap"]
        assert offer["row_ref"] and offer["remapped"] is None
        assert offer["label"] == card["title"]
    # Nothing claimed the first caption, so there is no concept to pre-select…
    assert by_label["Deposits paid for acquisition of land"]["remap"]["current_key"] == ""
    # …and the second mapped to one the template does not declare, which the offer carries so the
    # analyst can see what it was placed on before choosing where it belongs.
    assert by_label["Sundry receivables"]["remap"]["current_key"] == "bs_ca__not_in_this_template"
    # Every other builder is explicit about having no offer rather than leaving the key absent.
    for c in review["checks"]:
        if c["type"] != "unmapped":
            assert c["remap"] is None, c["type"]


def test_the_targets_are_served_once_and_exclude_what_cannot_hold_a_figure(template):
    review = _build_review([_unmapped("Deposits paid", 60)], "d.pdf", "en", template_def=template)
    targets = review["remap_targets"]
    keys = [t["canonical_key"] for t in targets]

    assert keys and len(keys) == len(set(keys))
    # A calculated subtotal is not a target: a printed figure written onto one is overwritten by
    # its own rollup, or overrides it and hides the component that is the actual defect.
    assert "bs_total_assets" not in keys and "pl_gross_profit" not in keys
    assert "bs_current_assets__inventories" in keys
    # Grouped for a select an analyst can navigate: 180-odd flat options is a list nobody reads.
    assert all(t["statement"] and t["section"] and t["label"] for t in targets)
    # Not repeated per card — the card carries the row handle only.
    assert "candidates" not in review["checks"][0]["remap"]


def test_no_template_means_no_targets_and_so_no_offer_that_could_only_fail(template):
    review = _build_review([_unmapped("Deposits paid", 60)], "d.pdf", "en")
    assert review["remap_targets"] == []


def test_the_row_handle_does_not_move_when_the_figure_does(template):
    """A value-dependent handle would send the analyst's chosen concept to a row that had merely
    been re-priced. ``_prov_anchor`` anchors on the caption's geometry for the same reason."""
    a = _unmapped("Deposits paid for acquisition of land", 60)
    b = _unmapped("Deposits paid for acquisition of land", 60123)
    assert _row_ref(a) == _row_ref(b)
    # …and two different captions on the same page are different rows.
    assert _row_ref(a) != _row_ref(_unmapped("Other receivables", 60))
    # The handle is NOT the subject key: one row wearing two findings has one handle, and
    # re-mapping must not change the identity of the thing being re-mapped.
    review = _build_review([a], "d.pdf", "en", template_def=template)
    card = review["checks"][0]
    assert card["remap"]["row_ref"] != card["subject_key"]


# --- the endpoint -------------------------------------------------------------------------------

def _extracted(client) -> str:
    """One filing through the worker with the shipped template and rulebook attached."""
    doc_id = client.post(f"{API}/documents",
                         files={"file": ("bs.pdf", make_unmapped_row_pdf(),
                                         "application/pdf")}).json()["id"]
    ont = next(o for o in client.get(f"{API}/ontologies").json()
               if o["ontology_key"] == "hkfrs_hk_china")
    tpl = next(t for t in client.get(f"{API}/templates").json()
               if t["template_key"] == ont["target_template_key"])
    client.post(f"{API}/documents/{doc_id}/extractions",
                json={"template_version_id": tpl["id"], "ontology_version_id": ont["id"]})
    for _ in range(200):
        if client.get(f"{API}/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    assert client.get(f"{API}/documents/{doc_id}/run").json()["status"] == "succeeded"
    return doc_id


def _offer(client, doc_id) -> tuple[dict, dict]:
    """The first card carrying a re-map offer, with the review payload it came from."""
    review = client.get(f"{API}/documents/{doc_id}/review").json()
    card = next(c for c in review["checks"] if c.get("remap"))
    return review, card


def test_a_row_is_re_mapped_end_to_end_and_the_finding_it_answered_goes_away(client):
    doc_id = _extracted(client)
    review, card = _offer(client, doc_id)
    ref = card["remap"]["row_ref"]
    was = card["remap"]["current_key"]
    target = next(t["canonical_key"] for t in review["remap_targets"]
                  if t["canonical_key"] != was)

    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": ref, "canonical_key": target,
                          "reason": "Traced to p.1; it is inventory."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["from"] == was and body["to"] == target
    assert body["remap"]["by"] == "admin" and body["remap"]["at"]
    assert body["remap"]["reason"] == "Traced to p.1; it is inventory."

    # The stored row moved, and says who moved it.
    rows = client.get(f"{API}/documents/{doc_id}/run").json()["result"]["rows"]
    moved = next(x for x in rows if _row_ref(x) == ref)
    assert moved["canonical_key"] == target
    assert moved["mapping_method"] == "manual_remap" and moved["mapping_confidence"] == 1.0
    assert f"remapped_by_reviewer:{was or 'unmapped'}->{target}" in moved["flags"]
    # The finding is answered, so the flag that raises it goes too — a card that comes back
    # re-mapped AND still low-confidence reads as the action having failed.
    assert "low_mapping_confidence" not in moved["flags"]

    after = client.get(f"{API}/documents/{doc_id}/review").json()
    assert ref not in [c["remap"]["row_ref"] for c in after["checks"] if c.get("remap")]


def test_the_re_map_is_visible_on_the_card_it_would_otherwise_leave_no_trace_on(client):
    """The answered finding is gone from the queue, so without this the only evidence of a human
    decision is the absence of a card."""
    doc_id = _extracted(client)
    review, card = _offer(client, doc_id)
    ref = card["remap"]["row_ref"]
    # A target that leaves the row visible in the queue: mapped, so no longer unmapped, but the
    # re-map note is what proves the decision was recorded on the row itself.
    target = next(t["canonical_key"] for t in review["remap_targets"]
                  if t["canonical_key"] != card["remap"]["current_key"])
    client.post(f"{API}/documents/{doc_id}/review/remap",
                json={"row_ref": ref, "canonical_key": target, "reason": "it is inventory"})

    rows = client.get(f"{API}/documents/{doc_id}/run").json()["result"]["rows"]
    row = next(x for x in rows if _row_ref(x) == ref)
    offer = _remap_offer(row, "en")
    assert offer["remapped"]["to"] == target
    assert target in offer["remapped_note"] and "admin" in offer["remapped_note"]


def test_un_mapping_is_the_way_back_and_nothing_is_lost(client):
    """"" is the analyst's judgement that the row belongs to no concept — and the only route back
    from a re-map that started from unmapped."""
    doc_id = _extracted(client)
    review, card = _offer(client, doc_id)
    ref, was = card["remap"]["row_ref"], card["remap"]["current_key"]
    target = next(t["canonical_key"] for t in review["remap_targets"]
                  if t["canonical_key"] != was)
    client.post(f"{API}/documents/{doc_id}/review/remap",
                json={"row_ref": ref, "canonical_key": target})

    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": ref, "canonical_key": "", "reason": "not a template line"})
    assert r.status_code == 200 and r.json()["to"] == ""
    rows = client.get(f"{API}/documents/{doc_id}/run").json()["result"]["rows"]
    row = next(x for x in rows if _row_ref(x) == ref)
    assert row["canonical_key"] is None and row["mapping_method"] == "manual_unmap"
    assert row["remap"]["from"] == target        # where it came back from, kept


def _inject(rows_to_add: list[dict]) -> None:
    """Append rows to the latest run, for the cases the synthetic filing does not produce."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as s:
        run = s.query(ExtractionRun).order_by(ExtractionRun.created_at.desc()).first()
        result = dict(run.result)
        result["rows"] = [*result["rows"], *rows_to_add]
        run.result = result
        flag_modified(run, "result")
        s.commit()


def test_re_mapping_a_weakly_mapped_row_clears_the_flag_that_marked_it(client):
    """A human's choice is not a weak match, and the flag has to say so.

    THE ROUTE IN CHANGED AND THE INVARIANT DID NOT. A weak mapping used to raise its own review card,
    and this test used to reach the endpoint through it; a mapping's strength is no longer one of the
    three things the queue reports, so there is no card and the endpoint is called directly — which is
    also how the Workspace calls it, from the row rather than from a card. What ``low_mapping_confidence``
    still decides is real and unchanged: the row's confidence badge in the grid, the ``weak_mappings``
    count the queue serves beside its findings, and whether the row is eligible for auto-accept. Left
    in place, a row a human placed by hand reads as a guess forever.
    """
    doc_id = _extracted(client)
    row = _lowconf("Sundry receivables",
                   "bs_current_assets__prepayments_other_receivables_and_other_assets", 25, y=0.71)
    _inject([row])
    ref = _row_ref(row)

    # It is counted as weak and raises NO card — the two halves of the decision this queue made.
    review = client.get(f"{API}/documents/{doc_id}/review").json()
    assert review["weak_mappings"] >= 1
    assert not [c for c in review["checks"] if (c.get("remap") or {}).get("row_ref") == ref]

    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": ref, "canonical_key": "bs_current_assets__inventories",
                          "reason": "it is stock, not a receivable"})
    assert r.status_code == 200, r.text

    moved = next(x for x in client.get(f"{API}/documents/{doc_id}/run").json()["result"]["rows"]
                 if _row_ref(x) == ref)
    assert moved["canonical_key"] == "bs_current_assets__inventories"
    assert "low_mapping_confidence" not in moved["flags"]
    assert moved["mapping_confidence"] == 1.0
    # …and on the VALUE too, which is what the grid colours each figure from.
    conf = moved["values"][0]["confidence"]
    assert conf["mapping"] == 1.0 and "low_mapping_confidence" not in conf["flags"]

    # …and the count comes down with the flag, so the tile stops reporting a weak mapping nobody has.
    after = client.get(f"{API}/documents/{doc_id}/review").json()
    assert after["weak_mappings"] == review["weak_mappings"] - 1


def test_a_target_the_template_does_not_offer_is_refused(client):
    doc_id = _extracted(client)
    _review, card = _offer(client, doc_id)
    ref = card["remap"]["row_ref"]

    for key in ("bs_no_such_concept", "bs_total_assets"):
        r = client.post(f"{API}/documents/{doc_id}/review/remap",
                        json={"row_ref": ref, "canonical_key": key})
        assert r.status_code == 422, key
        assert key in r.json()["detail"]
    # …and the row is untouched by either refusal.
    rows = client.get(f"{API}/documents/{doc_id}/run").json()["result"]["rows"]
    assert next(x for x in rows if _row_ref(x) == ref).get("remap") is None


def test_an_unknown_row_reference_is_a_404_not_a_silent_no_op(client):
    doc_id = _extracted(client)
    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": "0" * 64,
                          "canonical_key": "bs_current_assets__inventories"})
    assert r.status_code == 404 and "matches" in r.json()["detail"]


def test_re_mapping_a_row_to_the_concept_it_already_carries_is_refused(client):
    doc_id = _extracted(client)
    _review, card = _offer(client, doc_id)
    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": card["remap"]["row_ref"],
                          "canonical_key": card["remap"]["current_key"]})
    assert r.status_code == 409 and "already" in r.json()["detail"]


def test_an_ambiguous_row_reference_is_refused_rather_than_resolved_to_the_first_match(client):
    """Two rows can share an anchor: a page with no label geometry falls back to the printed line's
    vertical band, and two sub-tables on one baseline collide. Writing the analyst's concept onto
    whichever came first would move a real figure onto a concept nobody chose, and the card they
    were reading would have disappeared either way."""
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun
    from sqlalchemy.orm.attributes import flag_modified

    doc_id = _extracted(client)
    twin = {"source_label": "Others", "canonical_key": None,
            "values": [{"basis": "consolidated", "period_label": "current", "value": "1234",
                        "provenance": {"source_kind": "native", "page_index": 0}}]}
    with SessionLocal() as s:
        run = s.query(ExtractionRun).order_by(ExtractionRun.created_at.desc()).first()
        result = dict(run.result)
        # Same caption, same page, no geometry at all: `_prov_anchor` cannot discriminate them.
        result["rows"] = [*result["rows"], twin, {**twin, "values":
                                                 [{**twin["values"][0], "value": "5678"}]}]
        run.result = result
        flag_modified(run, "result")
        s.commit()

    ref = _row_ref(twin)
    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": ref, "canonical_key": "bs_current_assets__inventories"})
    assert r.status_code == 409
    assert "share that reference" in r.json()["detail"]
    rows = client.get(f"{API}/documents/{doc_id}/run").json()["result"]["rows"]
    assert [x for x in rows if _row_ref(x) == ref and x["canonical_key"]] == []


def test_the_analyst_who_owns_the_extraction_may_re_map_it_and_anonymous_may_not(
        anon_client, auth):
    """Re-mapping a printed row is an EXTRACTION edit, so it is gated exactly where the value edit
    is: every working role holds ``extraction:edit``, and gating it on ``review:resolve`` instead
    would deny an analyst the correction the role map entitles them to. Unauthenticated is 401."""
    doc_id = anon_client.post(f"{API}/documents",
                              files={"file": ("rm.pdf", make_unmapped_row_pdf(), "application/pdf")},
                              headers=auth("analyst")).json()["id"]
    ont = next(o for o in anon_client.get(f"{API}/ontologies", headers=auth("analyst")).json()
               if o["ontology_key"] == "hkfrs_hk_china")
    tpl = next(t for t in anon_client.get(f"{API}/templates", headers=auth("analyst")).json()
               if t["template_key"] == ont["target_template_key"])
    anon_client.post(f"{API}/documents/{doc_id}/extractions",
                     json={"ontology_version_id": ont["id"], "template_version_id": tpl["id"]},
                     headers=auth("analyst"))
    for _ in range(200):
        r = anon_client.get(f"{API}/documents/{doc_id}/run", headers=auth("analyst"))
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    review = anon_client.get(f"{API}/documents/{doc_id}/review",
                             headers=auth("analyst")).json()
    card = next(c for c in review["checks"] if c.get("remap"))
    body = {"row_ref": card["remap"]["row_ref"],
            "canonical_key": next(t["canonical_key"] for t in review["remap_targets"]
                                  if t["canonical_key"] != card["remap"]["current_key"]),
            "reason": "checked p.1"}

    assert anon_client.post(f"{API}/documents/{doc_id}/review/remap", json=body).status_code == 401
    ok = anon_client.post(f"{API}/documents/{doc_id}/review/remap", json=body,
                          headers=auth("analyst"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["remap"]["by"] == "analyst"


def test_remap_targets_come_from_the_run_s_own_template(template):
    """Not from the newest seeded one. A target list built from a template the analyst never chose
    offers concepts the run cannot hold — the same self-contradiction ``_template_for_run`` exists
    to close."""
    keys = {t["canonical_key"] for t in _remap_targets(template, "en")}
    trimmed = {"statements": [{"type": "balance_sheet", "sections": [
        {"canonical_key": "bs_s2_current_assets", "label": "Current assets", "role": "header",
         "children": [{"canonical_key": "bs_current_assets__inventories", "label": "Inventories",
                       "role": "line"}]}]}]}
    assert {t["canonical_key"] for t in _remap_targets(trimmed, "en")} == \
        {"bs_current_assets__inventories"}
    assert len(keys) > 1


# --- the section tag follows the concept -------------------------------------------------------

def test_a_re_map_moves_the_rows_analyst_section_with_it(client):
    """The tag a reader groups by is derived from the concept, so it cannot be left behind. A row
    re-mapped from a current asset to a non-current one and still tagged Current assets would be
    served under the wrong heading with nothing in the row itself to show it."""
    doc_id = _extracted(client)
    _review, card = _offer(client, doc_id)
    ref = card["remap"]["row_ref"]
    target = "bs_non_current_assets__property_plant_and_equipment"

    before = next(x for x in client.get(f"{API}/documents/{doc_id}/run").json()["result"]["rows"]
                  if _row_ref(x) == ref)
    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": ref, "canonical_key": target, "reason": "it is a building"})
    assert r.status_code == 200, r.text

    result = client.get(f"{API}/documents/{doc_id}/run").json()["result"]
    moved = next(x for x in result["rows"] if _row_ref(x) == ref)
    assert moved["bucket"] == "non_current_assets"
    assert moved["bucket_label"] == "Non-current assets"
    assert moved["section"] == "bs_s1_non_current_assets"
    assert moved["bucket"] != before.get("bucket")

    # …and the stored segmentation the section screens read moved with it, so the row is not in two
    # sections at once.
    members = {seg["bucket"]: seg["face_item_ids"] for seg in result["buckets"]["segments"]}
    assert moved["id"] in members["non_current_assets"]
    assert sum(1 for ids in members.values() if moved["id"] in ids) == 1
    # A row a human has just placed is not a row nothing could place. Left in the coverage list it
    # would be counted as uncovered by the index AND served with unresolved:true under its new
    # section — the analyst's own correction reading as having failed.
    detail = client.get(f"{API}/documents/{doc_id}/buckets/non_current_assets").json()
    assert [r["unresolved"] for r in detail["rows"] if r["id"] == moved["id"]] == [False]


def test_un_mapping_a_row_takes_its_section_away_and_reports_it_unplaced(client):
    """A row belonging to no concept belongs to no section either — and Others has to say it is
    there because nothing placed it, not because it belongs there."""
    doc_id = _extracted(client)
    _review, card = _offer(client, doc_id)
    ref = card["remap"]["row_ref"]
    client.post(f"{API}/documents/{doc_id}/review/remap",
                json={"row_ref": ref, "canonical_key": "bs_current_assets__inventories",
                      "reason": "first, map it"})
    client.post(f"{API}/documents/{doc_id}/review/remap",
                json={"row_ref": ref, "canonical_key": "", "reason": "on reflection, nothing"})

    result = client.get(f"{API}/documents/{doc_id}/run").json()["result"]
    row = next(x for x in result["rows"] if _row_ref(x) == ref)
    assert row["bucket"] is None and row["section"] is None and row["bucket_label"] is None
    members = {seg["bucket"]: seg["face_item_ids"] for seg in result["buckets"]["segments"]}
    assert row["id"] in members["others"]
    assert row["id"] in result["buckets"]["unresolved_face_item_ids"]


def _store_result(result: dict) -> None:
    """Write a whole result back onto the latest run — for the row shapes the synthetic filing does
    not produce (here: a row printed inside a note, which the residual sweep makes on real
    filings)."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as s:
        run = s.query(ExtractionRun).order_by(ExtractionRun.created_at.desc()).first()
        run.result = result
        flag_modified(run, "result")
        s.commit()


def test_re_mapping_a_row_printed_in_a_note_does_not_make_it_a_face_row(client):
    """A row printed INSIDE a note is not a face row whatever concept a reviewer maps it to. The
    queue offers a re-map on it like any other unmapped row, and counting it as a face row would put
    the note's money in the section twice — once through the note, once through the row. The tag
    still follows the concept; only the face membership is withheld."""
    doc_id = _extracted(client)
    result = client.get(f"{API}/documents/{doc_id}/run").json()["result"]
    row = next(r for r in result["rows"] if r.get("bucket") == "current_assets")

    # Make it a note row in the stored result, which is the shape the residual sweep produces.
    row["printed_in"] = "notes"
    _store_result(result)
    before = client.get(f"{API}/documents/{doc_id}/run").json()["result"]["buckets"]
    seen_before = sum(1 for seg in before["segments"] if row["id"] in seg["face_item_ids"])

    client.post(f"{API}/documents/{doc_id}/review/remap",
                json={"row_ref": _row_ref(row),
                      "canonical_key": "bs_non_current_assets__property_plant_and_equipment",
                      "reason": "it details the PPE note"})

    after = client.get(f"{API}/documents/{doc_id}/run").json()["result"]
    moved = next(r for r in after["rows"] if r["id"] == row["id"])
    # The tag followed the concept…
    assert moved["bucket"] == "non_current_assets"
    # …and the face membership did not move, so no section gained a note row.
    seen_after = sum(1 for seg in after["buckets"]["segments"]
                     if row["id"] in seg["face_item_ids"])
    assert seen_after == seen_before
    assert row["id"] not in next(seg["face_item_ids"] for seg in after["buckets"]["segments"]
                                 if seg["bucket"] == "non_current_assets")


def test_placing_a_row_by_hand_stops_it_being_counted_as_unplaced(client):
    """The coverage measurement is membership too. Un-mapping puts the row in
    ``unresolved_face_item_ids`` (that is what it means); re-mapping it has to take it out again, or
    the index counts a placed row as uncovered and the detail route serves it with
    ``unresolved: true`` under its new section — the analyst's own correction reading as a failure.

    The un-map comes first deliberately: without it the id was never in the list, and an assertion
    that it is absent afterwards passes whether or not anything clears it."""
    doc_id = _extracted(client)
    _review, card = _offer(client, doc_id)
    ref = card["remap"]["row_ref"]

    client.post(f"{API}/documents/{doc_id}/review/remap",
                json={"row_ref": ref, "canonical_key": "", "reason": "nothing, for now"})
    unmapped = client.get(f"{API}/documents/{doc_id}/run").json()["result"]
    row_id = next(r["id"] for r in unmapped["rows"] if _row_ref(r) == ref)
    assert row_id in unmapped["buckets"]["unresolved_face_item_ids"], (
        "the un-map must put the row in the coverage list, or this test proves nothing")

    client.post(f"{API}/documents/{doc_id}/review/remap",
                json={"row_ref": ref, "canonical_key": "bs_current_assets__inventories",
                      "reason": "traced to p.1: inventory"})
    after = client.get(f"{API}/documents/{doc_id}/run").json()["result"]
    assert row_id not in after["buckets"]["unresolved_face_item_ids"]

    index = client.get(f"{API}/documents/{doc_id}/buckets").json()
    assert index["unresolved_face_rows"] == 0
    detail = client.get(f"{API}/documents/{doc_id}/buckets/current_assets").json()
    assert [r["unresolved"] for r in detail["rows"] if r["id"] == row_id] == [False]
