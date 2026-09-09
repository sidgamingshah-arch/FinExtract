"""Real validation checks feed the review queue (Req 11): balance identity and note ties,
plus the ConfidenceStage setting the validation sub-signal."""
from __future__ import annotations


def _row(key, cur):
    return {"canonical_key": key,
            "values": [{"basis": "consolidated", "period_label": "current", "value": str(cur)}]}


def _recon(residual, *, face=1000, note="9", face_key="bs_ca__trade_receivables", **extra):
    """One reconciliation entry as the stage writes it — per FACE LINE, per note, per column.

    ``face_key`` is what names the face line across runs (stages/reconcile.py); ``face_item_id`` is
    a per-run UUID and is deliberately not what any reader keys on.
    """
    ent = {"note_number": note, "basis": "consolidated", "period_label": "current",
           "face_key": face_key,
           "raw_face": face, "residual": residual, "within_tolerance": residual == 0}
    ent.update(extra)
    return ent


def test_a_note_that_is_not_a_breakdown_of_the_face_figure_is_not_a_finding():
    """Most cited notes are not decompositions — an analysis of profit before tax, a segment
    table, a commitments schedule. Their totals are nowhere near the face figure, and asserting
    "does not tie" against them buries the real findings (it produced 246 non-findings on a
    single real filing)."""
    from app.api.routes.documents import _accounting_checks

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 100)]
    assert _accounting_checks(rows, [_recon(250)], "en") == []          # 25% off
    assert _accounting_checks(rows, [_recon(-15_645_284_739, face=88_611)], "en") == []


def test_the_grade_is_derived_for_runs_stored_before_it_existed():
    """An older run carries only within_tolerance; the grade is determinable from the stored
    numbers, so those runs report correctly without re-extraction."""
    from app.services.reconcile import tie_status

    assert tie_status({"raw_face": 1000, "residual": 0, "within_tolerance": True}) == "tied"
    assert tie_status({"raw_face": 1000, "residual": 20, "within_tolerance": False}) == "untied"
    assert tie_status({"raw_face": 1000, "residual": 250,
                       "within_tolerance": False}) == "unconfirmed"
    # An explicit grade always wins over the derivation.
    assert tie_status({"raw_face": 1000, "residual": 250, "tie_status": "untied"}) == "untied"


def test_review_queue_includes_failed_checks():
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90)]
    review = _build_review(rows, "doc.pdf", "en", [_recon(20)])
    # One, not two: the untied note that used to raise the second card is no longer a finding.
    assert any(t["label"] == "Checks" and t["count"] == 1 for t in review["tabs"])
    assert any(c["type"] == "balance" for c in review["checks"])
    assert not [c for c in review["checks"] if c["type"] == "note_tie"]


def test_the_balance_identity_runs_when_the_filing_never_prints_the_totals():
    """HK/PRC statements often print no "Total assets" line at all — they run the section
    subtotals and "Total assets less current liabilities". Requiring the printed total meant
    the identity check silently never ran on exactly those filings."""
    from app.api.routes.documents import _accounting_checks

    rows = [_row("bs_non_current_assets__total_non_current_assets", 60),
            _row("bs_current_assets__total_current_assets", 40),
            _row("bs_equity__total_equity", 25),
            _row("bs_non_current_liabilities__total_non_current_liabilities", 30),
            _row("bs_current_liabilities__total_current_liabilities", 50)]   # 100 vs 105
    bal = next(c for c in _accounting_checks(rows, [], "en") if c["type"] == "balance")
    assert bal["delta"] == "-5"
    assert any("derived" in str(line[0]).lower() for line in bal["calc"])

    # ...and it passes silently when the derived sides agree.
    rows[-1] = _row("bs_current_liabilities__total_current_liabilities", 45)
    assert _accounting_checks(rows, [], "en") == []


def test_confidence_stage_sets_validation_on_balance_mismatch():
    from app.core.models.document import DocumentModel
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.stage import PipelineContext
    from app.stages.confidence import ConfidenceStage

    def li(key, val):
        item = LineItem(canonical_key=key)
        item.set_value(ExtractedValue(value=val, value_raw=val, basis=Basis.CONSOLIDATED,
                                      period_label="current"))
        return item

    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [li("bs_total_assets", 100), li("bs_total_equity_and_liabilities", 90)]
    ConfidenceStage().run(doc, PipelineContext(raw_bytes=b""))
    ev = next(iter(doc.line_items[0].values.values()))
    assert ev.confidence.validation == 0.4 and "balance_mismatch" in ev.confidence.flags
    assert ev.confidence.overall < ev.confidence.mapping   # validation caps overall


def test_per_value_confidence_exposed(client):
    """Each extracted value carries its own confidence vector (mapping/validation/overall/
    weakest + flags), not just a single per-row number (Req 9)."""
    import time

    from tests.fixtures.generate import make_native_pdf

    doc_id = client.post("/api/v1/documents",
                         files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}).json()["id"]
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
    for _ in range(100):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)

    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    valued = next(r for r in rows if r.get("values"))
    conf = valued["values"][0]["confidence"]
    assert {"mapping", "validation", "overall", "weakest", "flags"} <= set(conf)
    assert isinstance(conf["overall"], (int, float)) and 0.0 <= conf["overall"] <= 1.0
    assert isinstance(conf["flags"], list)


def test_several_printed_lines_mapping_to_one_concept_are_summed_not_dropped():
    """Concepts legitimately absorb more than one printed line — three depreciation lines roll
    into "Depreciation and amortisation", two tax payments into "Income tax paid", and an
    "Others" bucket exists to catch a handful. Showing only the first would drop the rest from
    the statement with nothing to indicate a figure went missing."""
    from app.api.routes.documents import _build_statement

    key = "cf_cash_flow_from_operating_activities__income_tax_paid"
    rows = [
        {"canonical_key": key, "source_label": "PRC corporate income tax paid",
         "values": [{"basis": "consolidated", "period_label": "current", "value": "-559917"}]},
        {"canonical_key": key, "source_label": "PRC land appreciation tax paid",
         "values": [{"basis": "consolidated", "period_label": "current", "value": "-44488"}]},
    ]
    stmt = _build_statement(rows, None, "cash_flow", "f.pdf")
    row = next(r for r in stmt["rows"] if r["id"] == key)
    assert row["v1"] == -604405                      # both lines, not just the first
    # What went into it is enumerated, not described — see the traceability test below.
    assert [c["label"] for c in row["contributions"]] == ["PRC corporate income tax paid",
                                                         "PRC land appreciation tax paid"]


def test_a_concept_with_one_source_line_is_unchanged():
    from app.api.routes.documents import _build_statement

    key = "cf_cash_flow_from_operating_activities__income_tax_paid"
    rows = [{"canonical_key": key, "source_label": "Income tax paid",
             "values": [{"basis": "consolidated", "period_label": "current", "value": "-100"}]}]
    row = next(r for r in _build_statement(rows, None, "cash_flow", "f.pdf")["rows"]
               if r["id"] == key)
    assert row["v1"] == -100 and row["contributions"] is None


def test_a_combined_figure_lists_every_contributing_line_with_its_own_source():
    """Clicking a combined figure — "Others", or any concept several printed lines map to — has
    to show the arithmetic and let each part be traced back. A figure that matches no single line
    on the page is otherwise unexplainable: the analyst cannot tell a correct aggregation from a
    mis-mapping without seeing which lines went in and where each was printed."""
    from app.api.routes.documents import _build_statement

    key = "cf_cash_flow_from_operating_activities__income_tax_paid"

    def src(page):
        return {"source_kind": "native", "page_index": page,
                "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.21}}

    rows = [
        {"canonical_key": key, "source_label": "PRC corporate income tax paid",
         "mapping_method": "exact",
         "values": [{"basis": "consolidated", "period_label": "current", "value": "-559917",
                     "provenance": src(109)},
                    {"basis": "consolidated", "period_label": "prior", "value": "-846790",
                     "provenance": src(109)}]},
        {"canonical_key": key, "source_label": "PRC land appreciation tax paid",
         "mapping_method": "residual",
         "values": [{"basis": "consolidated", "period_label": "current", "value": "-44488",
                     "provenance": src(110),
                     "confidence": {"flags": ["residual_combined"]}}]},
    ]
    row = next(r for r in _build_statement(rows, None, "cash_flow", "f.pdf")["rows"]
               if r["id"] == key)

    assert row["v1"] == -604405
    # The arithmetic is shown, not described — as DISPLAY. It must not travel in `formula`, which
    # the client prefills into its formula box and sends back with the next edit: the server would
    # then evaluate "-559,917 + -44,488" and its result would override the figure the analyst had
    # just typed.
    assert row["arithmetic"] == "-559,917 + -44,488"
    assert row["formula"] is None
    assert row["inspector"]["result"] == "-604,405"

    got = row["contributions"]
    assert [c["label"] for c in got] == ["PRC corporate income tax paid",
                                         "PRC land appreciation tax paid"]
    assert [c["v1"] for c in got] == [-559917.0, -44488.0]
    assert got[0]["v2"] == -846790.0 and got[1]["v2"] is None
    # Each part keeps the page it was printed on, and a structured location to jump to.
    assert [c["src"] for c in got] == ["p.110", "p.111"]
    assert all(c["source"]["bbox"] for c in got)
    # A line that was ROUTED here is distinguished from one positively identified.
    assert [c["residual"] for c in got] == [False, True]


def test_a_single_source_row_carries_no_contributions():
    """Nothing to trace, so no breakdown is offered."""
    from app.api.routes.documents import _build_statement

    key = "cf_cash_flow_from_operating_activities__income_tax_paid"
    rows = [{"canonical_key": key, "source_label": "Income tax paid",
             "values": [{"basis": "consolidated", "period_label": "current", "value": "-100"}]}]
    row = next(r for r in _build_statement(rows, None, "cash_flow", "f.pdf")["rows"]
               if r["id"] == key)
    assert row["contributions"] is None


def test_every_review_tab_counts_exactly_what_it_selects():
    """A tab's count is the length of the list clicking it produces.

    The tabs carry the check TYPES they select rather than relying on their position matching an
    array on the client — positional agreement between a server list and a client array is what
    made the page-scope filter chips filter by the wrong page kind.
    """
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90),
            {"source_label": "Something the mapper could not place", "canonical_key": None,
             "values": [{"value": 5}]},
            {"source_label": "A shaky match", "canonical_key": "bs_cash",
             "mapping_confidence": 0.2, "flags": ["low_mapping_confidence"],
             "values": [{"value": 7}]}]
    review = _build_review(rows, "doc.pdf", "en", [_recon(20)])

    for tab in review["tabs"]:
        selected = (review["checks"] if tab["types"] is None
                    else [c for c in review["checks"] if c["type"] in tab["types"]])
        assert tab["count"] == len(selected), tab["label"]
    # The per-tab types partition the list: every check belongs to exactly one non-All tab, so a
    # finding can never be invisible under every filter.
    buckets = [t for t in review["tabs"] if t["types"] is not None]
    assert sum(t["count"] for t in buckets) == len(review["checks"])
    assert {c["type"] for c in review["checks"]} == {ty for t in buckets for ty in t["types"]
                                                    if any(c["type"] == ty
                                                           for c in review["checks"])}


def test_the_lines_with_no_finding_tile_counts_subtotals_and_totals_but_not_captions():
    """FINDING 9, from the real path's side: ONE definition of the population, in
    services/review_lines.py, called by this route and by the sample route.

    A subtotal and a total ARE lines — they are what the balance card and a section reconciliation
    name — so a subtotal no finding names is a line with no finding. A section HEADING is not a line
    at all: it carries no figure and no finding can be about it. The sample path counted only its
    ``kind == "item"`` rows, which excluded 6 subtotals and 4 totals from a population the real path
    included, under an identical label.
    """
    from app.api.routes.documents import _build_review
    from app.services.review_lines import is_statement_line

    rows = [{**_row("bs_total_assets", 100), "role": "total"},
            {**_row("bs_total_equity_and_liabilities", 90), "role": "total"},
            {**_row("bs_current_assets__total_current_assets", 40), "role": "subtotal",
             "mapping_confidence": 0.99},
            {**_row("bs_current_assets__inventories", 40), "role": "line",
             "mapping_confidence": 0.99},
            # A caption row: no figure of its own to be right or wrong about.
            {"canonical_key": None, "source_label": "Current assets", "role": "header",
             "values": []}]
    review = _build_review(rows, "d.pdf", "en")
    named = {n for c in review["checks"] for n in c["names"]}
    assert named == {"bs_total_assets", "bs_total_equity_and_liabilities"}   # the balance card
    # 4 lines in the population (the header is not one), 2 of them named → 2 with no finding.
    assert [is_statement_line(r) for r in rows] == [True, True, True, True, False]
    assert review["summary"]["passed"] == 2
    # THE ASSERTION THAT FAILS WITH THE ITEM-ONLY DEFINITION RESTORED: the two named lines are
    # TOTALS, so a population of plain lines only would answer 1 and count neither of them.
    assert review["summary"]["passed"] != len([r for r in rows if r.get("role") == "line"])


from app.api.routes.documents import (  # noqa: E402
    _relation_reported_elsewhere, _reported_assertions)


def _fail(target, *, difference, basis="consolidated", period="current", kind="rollup",
          rule_id=None, components=("bs_a", "bs_b")):
    """One FAILED arithmetic relation, shaped as the structural stage serializes it.

    Written from `details` outwards because that is what `_relation_reported_elsewhere` reads:
    target/basis/period_label decide which column the break is in, and `difference` is the break.
    """
    return {"status": "fail", "kind": kind, "rule_id": rule_id or f"{kind}:{target}",
            "scope_key": f"{basis}/{period}", "expected": 0.0, "actual": float(difference),
            "difference": float(difference),
            "details": {"target": target, "basis": basis, "period_label": period,
                        "op": "sum", "components": list(components),
                        "component_values": {c: "0" for c in components}}}


def _balance_card_review(structural):
    """A run whose BALANCE card is real, plus whatever relations we hand it.

    The balance card is produced by the real builder here, not hand-shaped: 1,000 of assets against
    900 of equity and liabilities makes it assert a 100 break on target `bs_total_assets`, in
    consolidated/current. That card is the thing that used to silence relations, so it has to be
    genuine — an earlier version of these tests supplied fixtures that produced NO suppressing card
    at all, and passed with the defect fully restored.
    """
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 1000), _row("bs_total_equity_and_liabilities", 900)]
    review = _build_review(rows, "doc.pdf", "en", [], structural=structural)
    balance = next(c for c in review["checks"] if c["type"] == "balance")
    assert balance["target"] == "bs_total_assets"
    assert abs(int((balance.get("evidence") or {})["diff"])) == 100
    return review


def _cards_above(review: dict) -> list[dict]:
    """The cards the relations are tested AGAINST — everything served before them.

    Not the whole payload: a relation card declares the same assertion its relation carries
    (`_ASSERTED_DIFF_KEY`), so a set built from a list containing it answers "already reported" for
    the relation itself. `_accounting_checks` passes the queue as it stands before the relations, and
    these tests have to ask the question the same way round.
    """
    return [c for c in review["checks"] if c["type"] != "structural"]


def test_a_relation_asserting_a_different_break_is_not_silenced_by_one_sharing_its_target():
    """Suppression matched a bare `details.target` and ignored what the card SAYS.

    Same target, a different statement about it: the balance card reports a 100 break between the
    two sides of the identity, while this relation reports a 2,500 break between total assets and
    its own components. The second was dropped from the queue and counted as "reported by a finding
    above", so a blocking break was reported nowhere at all.
    """
    rel = _fail("bs_total_assets", difference=2500)
    review = _balance_card_review([rel])
    assert any(c["type"] == "structural" and c.get("target") == "bs_total_assets"
               for c in review["checks"]), \
        "a 2,500 break must not be silenced by a card reporting 100 about the same line"
    assert not _relation_reported_elsewhere(rel, _reported_assertions(_cards_above(review)))


def test_a_consolidated_card_does_not_delete_a_break_in_the_standalone_column():
    """The suppression key carried no scope, while the balance card is hardcoded to
    consolidated/current — so it silenced a relation in a column it makes no statement about.

    The difference is deliberately the SAME 100 as the balance card's, so the column is the only
    thing that distinguishes them and the test cannot pass for any other reason.
    """
    rel = _fail("bs_total_assets", difference=100, basis="standalone")
    review = _balance_card_review([rel])
    assert any(c["type"] == "structural" and c.get("target") == "bs_total_assets"
               for c in review["checks"]), \
        "a standalone break must not be deleted by a consolidated card"
    assert not _relation_reported_elsewhere(rel, _reported_assertions(_cards_above(review)))


def test_one_break_reported_twice_is_still_suppressed():
    """The other half of the contract: suppression must keep WORKING, or the fix is just a revert.

    Same target, same column, same magnitude — a duplicate, and the analyst should not see one break
    twice. Sign is not part of the comparison: a rollup computes target − sum where the balance
    identity computes assets − (equity + liabilities), so one break legitimately reaches the two
    cards with opposite signs, and this relation carries −100 against the card's +100.
    """
    rel = _fail("bs_total_assets", difference=-100)
    review = _balance_card_review([rel])
    assert not any(c["type"] == "structural" for c in review["checks"]), \
        "a relation restating the balance card's own 100 break should not get a second card"
    # Suppressed AND accounted for: the coverage band's "reported by a finding above" count reads
    # this same predicate, so a dropped relation is never silently unaccounted for.
    assert _relation_reported_elsewhere(rel, _reported_assertions(_cards_above(review)))
