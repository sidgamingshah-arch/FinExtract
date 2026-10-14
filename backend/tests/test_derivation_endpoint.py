"""A derived figure explains itself through the statement endpoint.

services.derivation READS the explanation off the row and the endpoint renders it; this checks it
survives serialization and reaches the inspector in the shape the client already renders — the step
where it was previously dropped, leaving a reviewer the winning priority and one page reference.

The derivation services that used to write these payloads are DELETED and those figures must now
come from configuration, so the trail exercised here is read from STORED ROWS WRITTEN BY EARLIER
RUNS — which is literally what `_doc_with_derived_row` seeds: an `extraction_runs.result` whose
`derivation` and `mapping_method` were recorded before the removal. That column is durable, the
rows outlive the code that wrote them, and the reader must keep folding them, so every assertion
below still guards live behaviour. The `deprec_impairment` spellings in the fixtures are stored
label text, not references to anything that runs today.
"""
from __future__ import annotations

import uuid

TEMPLATE = {
    "schema_version": 1, "template_key": "d", "name": "D",
    "statements": [{"type": "profit_and_loss", "sections": [
        {"node_id": "sec", "canonical_key": "is_pl", "label": "Income statement",
         "role": "header", "children": [
             {"node_id": "n1", "canonical_key": "is_pl__deprec_and_impairment_oper_exp",
              "label": "Deprec & Impairment(Oper Exp)", "role": "line", "rollup": None},
         ]},
    ]}],
}


def _value(period, value):
    return {"basis": "consolidated", "period_label": period, "value": str(value),
            "provenance": {"source_kind": "native", "page_index": 12}}


def _derivation(period, formula, inputs):
    # `method` is free-text a run wrote into the JSON column; this is the string a pre-removal run
    # left, kept verbatim because it is what the stored rows being folded actually say.
    return {f"consolidated:{period}": {
        "method": "deprec_impairment", "formula": formula, "result": "500",
        "inputs": inputs, "flags": ["ASSUMED_ZERO_COS_DEPRECIATION"]}}


def _input(dataset, note, label, value, page):
    return {"dataset": dataset, "note": note, "note_heading": f"Note {note}", "label": label,
            "value": str(value), "provenance": {"source_kind": "native", "page_index": page},
            "counted": True}


def _doc_with_derived_row(client, derivation: dict, values: list[dict]) -> str:
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as s:
        doc = Document(filename="d.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"d-{uuid.uuid4().hex[:8]}", name="D", version=1,
                             definition=TEMPLATE)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(
            document_id=doc.id, status="succeeded", options={"template_version_id": tv.id},
            result={"rows": [{
                "canonical_key": "is_pl__deprec_and_impairment_oper_exp",
                "source_label": "Deprec & Impairment(Oper Exp)",
                "mapping_method": "computed:deprec_impairment:P3",
                "derivation": derivation,
                "values": values,
            }], "filename": "d.pdf"}))
        s.commit()
        return doc.id


def _row(client, doc_id):
    body = client.get(f"/api/v1/documents/{doc_id}/statement",
                      params={"statement": "profit_and_loss", "basis": "consolidated"}).json()
    return next(r for r in body["rows"]
                if r["id"] == "is_pl__deprec_and_impairment_oper_exp")


def test_a_computed_row_carries_its_inputs_as_contributions(client):
    doc_id = _doc_with_derived_row(
        client,
        _derivation("current", "P3 · profit-before-tax depreciation − cost-of-sales depreciation",
                    [_input("pbt_depreciation", "6", "Depreciation of PPE", 700, 12),
                     _input("cos_depreciation", "7", "Depreciation", 200, 30)]),
        [_value("current", 500)])
    row = _row(client, doc_id)
    assert row["v1"] == 500
    assert [c["v1"] for c in row["contributions"]] == [700.0, 200.0]
    assert [c["method"] for c in row["contributions"]] == ["pbt_depreciation", "cos_depreciation"]


def test_the_rule_that_produced_the_figure_is_named_not_just_its_priority(client):
    doc_id = _doc_with_derived_row(
        client,
        _derivation("current", "P3 · profit-before-tax depreciation − cost-of-sales depreciation",
                    [_input("pbt_depreciation", "6", "Depreciation of PPE", 700, 12)]),
        [_value("current", 500)])
    row = _row(client, doc_id)
    assert "profit-before-tax depreciation" in row["inspector"]["formula"]
    assert row["arithmetic"] == row["inspector"]["formula"]


def test_each_input_keeps_its_own_page_for_click_to_source(client):
    doc_id = _doc_with_derived_row(
        client,
        _derivation("current", "P3",
                    [_input("pbt_depreciation", "6", "Depreciation of PPE", 700, 12),
                     _input("cos_depreciation", "7", "Depreciation", 200, 30)]),
        [_value("current", 500)])
    row = _row(client, doc_id)
    pages = [(c["source"] or {}).get("page_index") for c in row["contributions"]]
    assert pages == [12, 30]
    assert row["inspector"]["src"]


def test_an_input_is_labelled_by_its_note_so_repeated_captions_are_distinguishable(client):
    doc_id = _doc_with_derived_row(
        client,
        _derivation("current", "P1",
                    [_input("ga_depreciation", "28", "Depreciation", 100, 40),
                     _input("rd_depreciation", "29", "Depreciation", 60, 41)]),
        [_value("current", 160)])
    row = _row(client, doc_id)
    labels = [c["label"] for c in row["contributions"]]
    assert len(set(labels)) == 2
    assert all("note" in label for label in labels)


def test_both_periods_appear_on_one_contribution_row(client):
    derivation = {
        **_derivation("current", "P3", [_input("pbt_depreciation", "6", "Depn", 700, 12)]),
        **_derivation("prior", "P3", [_input("pbt_depreciation", "6", "Depn", 650, 12)]),
    }
    doc_id = _doc_with_derived_row(client, derivation,
                                   [_value("current", 500), _value("prior", 470)])
    row = _row(client, doc_id)
    assert len(row["contributions"]) == 1
    assert (row["contributions"][0]["v1"], row["contributions"][0]["v2"]) == (700.0, 650.0)


def test_a_row_with_no_derivation_is_unaffected(client):
    doc_id = _doc_with_derived_row(client, {}, [_value("current", 500)])
    row = _row(client, doc_id)
    # The row builder normalises an empty contributions list to null, so a caption-read row is
    # unchanged by this feature rather than gaining an empty explanation panel.
    assert not row["contributions"]
    assert row["inspector"]["tag"] != "computed"


# ── a trail that records its arithmetic ─────────────────────────────────────────────────────

def _doc_with_rows(client, rows: list[dict]) -> str:
    """`_doc_with_derived_row` for several rows carrying one line."""
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as s:
        doc = Document(filename="d.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"d-{uuid.uuid4().hex[:8]}", name="D", version=1,
                             definition=TEMPLATE)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id},
                            result={"rows": rows, "filename": "d.pdf"}))
        s.commit()
        return doc.id


def _cascade_v2(basis: str = "consolidated") -> dict:
    """The shape `stages.note_sourced._fill_by_cascade` writes: a rung over two parts, the second
    deducted, each input saying what it added."""
    from app.services import derivation

    return {f"{basis}:current": derivation.build(
        method="cascade:P3", formula="sub__pbt_depreciation + -sub__cos_depreciation",
        result="500",
        inputs=[{"label": "PBT note — total depreciation", "canonical_key": "sub__pbt_depreciation",
                 "value": "700", "counted": True, "deducted": False, "used": "700",
                 "status": "used", "sign": 1, "role": "required",
                 "provenance": {"source_kind": "native", "page_index": 12}},
                {"label": "Cost of sales — depreciation", "canonical_key": "sub__cos_depreciation",
                 "value": "200", "counted": True, "deducted": True, "used": "-200",
                 "status": "deducted", "sign": -1, "role": "adjustment",
                 "provenance": {"source_kind": "native", "page_index": 30}}],
        flags=["rung:P3"], display={"op": "sum", "missing": [], "refused": []})}


def _line(derivation: dict | None, values: list[dict], label: str = "Depreciation") -> dict:
    return {"canonical_key": "is_pl__deprec_and_impairment_oper_exp", "source_label": label,
            "mapping_method": None, "values": values,
            **({"derivation": derivation} if derivation else {})}


def test_a_derived_row_serves_how_it_adds_up(client):
    doc_id = _doc_with_rows(client, [_line(_cascade_v2(), [_value("current", 500)])])
    row = _row(client, doc_id)
    w = row["working1"]
    assert w["how"] == "adds_up" and w["sum"] == 500.0 and w["shown"] == 500.0
    assert [t["status"] for t in w["terms"]] == ["used", "deducted"]
    assert row["working2"] is None, "the prior year has no trail"
    assert row["inspector"]["formula"] == row["arithmetic"] == (
        "PBT note — total depreciation − Cost of sales — depreciation")
    assert row["inspector"]["note"].startswith("Assembled by the cascade:P3 rule")


def test_the_trail_on_one_of_two_carriers_is_the_one_shown(client):
    """A cascade that outranks the printed rows writes onto one of them and silences the other.
    The line used to read "Combined from 2 printed lines" with "366,944 + —"; it is the derived
    line it is, explained by its trail."""
    silenced = _line(None, [{**_value("current", 0), "value": None}], label="Financial assets (FVTPL)")
    carrier = _line(_cascade_v2(), [_value("current", 500)], label="Financial assets (FVOCI)")
    doc_id = _doc_with_rows(client, [silenced, carrier])
    row = _row(client, doc_id)
    assert row["inspector"]["tag"] == "computed"
    assert [c["canonical_key"] for c in row["contributions"]] == [
        "sub__pbt_depreciation", "sub__cos_depreciation"]
    assert row["working1"]["how"] == "adds_up"


def test_a_trail_for_the_other_basis_does_not_assemble_this_one_from_nothing(client):
    doc_id = _doc_with_rows(client, [_line(_cascade_v2("standalone"), [_value("current", 500)])])
    row = _row(client, doc_id)
    assert row["inspector"]["tag"] != "computed"
    assert "Assembled from 0" not in (row["inspector"]["note"] or "")
    assert not row["working1"]


def test_the_trail_note_is_translated(client):
    doc_id = _doc_with_rows(client, [_line(_cascade_v2(), [_value("current", 500)])])
    english = _row(client, doc_id)["inspector"]["note"]
    body = client.get(f"/api/v1/documents/{doc_id}/statement",
                      params={"statement": "profit_and_loss", "basis": "consolidated",
                              "locale": "zh"}).json()
    chinese = next(r for r in body["rows"]
                   if r["id"] == "is_pl__deprec_and_impairment_oper_exp")["inspector"]["note"]
    assert chinese != english and "cascade:P3" in chinese
