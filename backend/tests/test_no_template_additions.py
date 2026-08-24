"""NO LINE THE TEMPLATE DOES NOT DEFINE MAY REACH A STATEMENT.

A row mapped to a concept the template does not declare is reported as ``unmapped`` — the queue's
"reaches no line in the output" category — and not under a kind of its own. It used to have one
(``off_template``); the queue now carries three categories, and this row belongs in the first of
them because that is what it IS from the output's point of view: an extracted face figure that
appears on no line of the spread. What the card still does is NAME the concept it mapped to, which
is the difference between "nothing recognised this caption" and "the rulebook and the template
disagree" — different fixes, same category.

Reported by the user as two symptoms of one defect: "recheck whether any line item can be added to
the template and where — there should be no additions", and "Gross Profit and the other calculated
totals still render at the END of the template on the front end despite the positioning the spec
fixed".

They were the same hole. The statement grid emitted the template's skeleton and then appended an
"Other extracted items" section holding every row whose canonical_key carried the statement's prefix
but was not one of the template's declared children — the in-statement successor of the
Additional-items view that had already been removed for exactly this reason. That both ADDED lines
the template does not define, and (for a run pinned to a superseded template, which every run is
pinned to something) put the very lines whose position a template revision had corrected back at the
end of the spread.

Removing the bucket cannot make a real extracted figure disappear, so the four properties are pinned
together here:

* the template's skeleton is the WHOLE statement, in the template's order — ``pl_gross_profit`` sits
  where the template puts it and not after everything;
* a mapped row the template does not declare reaches NO row of the grid;
* …and is reported as exactly one ``off_template`` review finding, carrying the ``remap`` offer that
  resolves it — so the figure is visible and actionable rather than merely gone;
* a matrix statement (changes in equity), whose rows are movements rather than template lines, is
  REFUSED outright when the run's template declares no such statement;
* and the statement says whether its shape came from a template version that has since been
  superseded, which is the other way a corrected line order still renders wrong.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.api.routes.documents import (
    _build_review, _build_statement, _row_ref, _t)

API = "/api/v1"
_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
_LOCALES = ("en", "zh", "ar", "fr")


def _shipped() -> dict:
    return json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text())


def _v(period, value, *, basis="consolidated", y=0.2):
    """One extracted figure with the label geometry ``_prov_anchor`` needs to tell rows apart."""
    return {"basis": basis, "period_label": period, "value": str(value),
            "provenance": {"source_kind": "native", "page_index": 0,
                           "bbox": {"x0": 0.60, "y0": y, "x1": 0.70, "y1": y + 0.017},
                           "label_bbox": {"x0": 0.10, "y0": y, "x1": 0.40, "y1": y + 0.017}}}


def _row(key, label, cur=None, *, conf=1.0, y=0.2, prior=None):
    vals = []
    if cur is not None:
        vals.append(_v("current", cur, y=y))
    if prior is not None:
        vals.append(_v("prior", prior, y=y))
    return {"canonical_key": key, "source_label": label, "mapping_confidence": conf,
            "mapping_method": "exact", "values": vals}


# A two-line balance sheet and nothing else. Deliberately free of rollups, so the only findings a
# row can raise here are the row-shaped ones.
MINI = {
    "schema_version": 1, "template_key": "mini", "name": "Mini",
    "statements": [{"type": "balance_sheet", "label": "Balance sheet", "sections": [
        {"node_id": "s1", "label": "Current assets", "role": "header", "children": [
            {"canonical_key": "bs_current_assets__inventories", "label": "Inventories",
             "role": "line"},
            {"canonical_key": "bs_current_assets__cash", "label": "Cash", "role": "line"}]}]}]}

# Off the template, but wearing a canonical key the balance sheet's own prefix would accept — which
# is precisely what the deleted bucket keyed on.
OFF_KEY = "bs_current_assets__prepaid_rates"


def _mini_grid(rows, locale="en"):
    return _build_statement(rows, MINI, "balance_sheet", "f.pdf", locale=locale)


# ------------------------------------------------------------------------------------------------
# 1. Nothing the template does not define reaches the grid
# ------------------------------------------------------------------------------------------------
def test_a_mapped_row_the_template_does_not_declare_produces_no_statement_row():
    rows = [_row("bs_current_assets__inventories", "Inventories", 100, y=0.20),
            _row(OFF_KEY, "Prepaid rates and government levies", 4_200, y=0.30)]
    grid = _mini_grid(rows)

    ids = [r["id"] for r in grid["rows"]]
    assert OFF_KEY not in ids
    # …and no row anywhere carries its figure, under any id.
    assert 4_200 not in [r.get("v1") for r in grid["rows"]]
    # The grid is the template's skeleton exactly: one heading and its two declared lines.
    assert ids == ["sec_s1", "bs_current_assets__inventories", "bs_current_assets__cash"]


def test_no_other_extracted_items_section_is_served_in_any_locale():
    """The heading the bucket rendered under. Checked per locale because the label went through
    ``_t``, so an English-only assertion would pass while a zh/ar/fr reader still saw the section."""
    rows = [_row("bs_current_assets__inventories", "Inventories", 100, y=0.20),
            _row(OFF_KEY, "Prepaid rates and government levies", 4_200, y=0.30)]
    for locale in _LOCALES:
        grid = _mini_grid(rows, locale=locale)
        assert "sec_other" not in [r["id"] for r in grid["rows"]], locale
        forbidden = _t("Other extracted items", locale)
        assert forbidden not in [r.get("label") for r in grid["rows"]], locale


def test_a_row_that_mapped_to_nothing_reaches_no_grid_at_all():
    """Existing behaviour, pinned: an unmapped row has no concept to file under, and inventing one
    from its printed caption would put a line on the spread that the template does not define. It
    stays off the grid whether or not a template was pinned — the queue is where it is reported."""
    rows = [_row("bs_current_assets__inventories", "Inventories", 100, y=0.20),
            {**_row(None, "Something nobody could place", 77, y=0.30), "mapping_confidence": None}]
    assert [r["id"] for r in _mini_grid(rows)["rows"]] == [
        "sec_s1", "bs_current_assets__inventories", "bs_current_assets__cash"]
    # And on the no-template fallback, which renders the extracted concepts themselves.
    untemplated = _build_statement(rows, None, "balance_sheet", "f.pdf")
    assert [r["id"] for r in untemplated["rows"]] == ["bs_current_assets__inventories"]
    assert "Something nobody could place" not in [r.get("label") for r in untemplated["rows"]]


# ------------------------------------------------------------------------------------------------
# 2. …and the figure is reported instead of dropped
# ------------------------------------------------------------------------------------------------
def test_an_empty_comparative_grid_says_which_of_the_two_reasons_it_is_empty():
    """Both used to be one indistinguishable blank under a callout asserting that figures are read
    deterministically from the source — so an analyst went back to the filing hunting for lines that
    were never going to appear."""
    rows = [_row("cf_operating__income_tax_paid", "Income tax paid", -100, y=0.20)]
    # MINI declares a balance sheet only, so a cash flow has no declared shape at all. This is where
    # the deleted bucket used to put the extracted rows.
    no_such = _build_statement(rows, MINI, "cash_flow", "f.pdf")
    assert no_such["rows"] == []
    assert no_such["refused"]["reason"] == "statement_not_in_template"
    assert no_such["refused"]["message"] == no_such["viewer"]["callout"]

    # Declared, but the filing carried no standalone column for it.
    no_basis = _build_statement([_row("bs_current_assets__inventories", "Inventories", 100)],
                                MINI, "balance_sheet", "f.pdf", basis="standalone")
    assert no_basis["rows"] == []
    assert no_basis["refused"]["reason"] == "basis_not_extracted"

    # Served → nothing to explain.
    served = _build_statement([_row("bs_current_assets__inventories", "Inventories", 100)],
                              MINI, "balance_sheet", "f.pdf")
    assert served["rows"] and served["refused"] is None


def test_the_off_template_row_is_exactly_one_review_finding_with_a_working_remap_offer():
    off = _row(OFF_KEY, "Prepaid rates and government levies", 4_200, y=0.30)
    review = _build_review([_row("bs_current_assets__inventories", "Inventories", 100, y=0.20),
                            off], "d.pdf", "en", template_def=MINI)

    assert [c["type"] for c in review["checks"]] == ["unmapped"]
    card = review["checks"][0]
    # The figure the grid no longer shows is printed on the card, so it is visible somewhere.
    assert ["Value", "4200", False] in card["calc"]
    assert card["calc"][1] == ["Mapped to", OFF_KEY, True]
    # The offer names the row by the handle the re-map endpoint resolves rows with, and the concept
    # it is filed under now.
    assert card["remap"]["row_ref"] == _row_ref(off)
    assert card["remap"]["current_key"] == OFF_KEY
    assert card["remap"]["remapped"] is None
    # Its own identity: caption + label geometry + the concept, so two off-template rows on one page
    # are two findings and a re-run filing the row elsewhere is a different claim.
    assert card["subject"]["k"] == "unmapped"
    assert card["subject"]["key"] == OFF_KEY
    assert card["subject"]["label"] == "prepaid rates and government levies"
    assert card["subject_key"] and card["evidence_digest"]
    # The row is not counted as a line with no finding.
    assert review["summary"]["passed"] == 1


def test_the_finding_is_not_raised_when_the_run_pinned_no_template():
    """A run with no template has no declaration for a row to be outside of, and the grid then
    renders the extracted concepts themselves — so raising "not on any statement" would be the
    queue contradicting the grid inside one payload."""
    rows = [_row(OFF_KEY, "Prepaid rates and government levies", 4_200, y=0.30)]
    assert [c["type"] for c in _build_review(rows, "d.pdf", "en")["checks"]] == []
    assert [r["id"] for r in _build_statement(rows, None, "balance_sheet", "f.pdf")["rows"]] \
        == [OFF_KEY]


def test_a_statement_level_total_is_declared_even_though_it_is_no_re_map_target():
    """``bs_total_assets`` is a section with no children — how the template declares a
    statement-level total — and it is excluded from ``_remap_targets`` because a printed figure must
    not be written onto a computed line. That exclusion is a different question from whether the
    template declares the line: reading the target list as the declaration would raise an
    "on no line of the output" finding against a row the reader can plainly see on the grid."""
    from app.api.routes.documents import _remap_targets

    template = _shipped()
    rows = [_row("bs_total_assets", "Total assets", 9_000, y=0.20)]
    assert "bs_total_assets" not in {t["canonical_key"] for t in _remap_targets(template, "en")}
    grid = _build_statement(rows, template, "balance_sheet", "f.pdf")
    assert any(r["id"] == "bs_total_assets" for r in grid["rows"])
    # No card of any kind: the row is on the grid, and a printed total whose components were never
    # extracted is a coverage fact rather than a defect (it used to raise `uncomputed`, which is
    # retired — see tests/test_calculated_and_gaps.py). The assertion that matters is the first one:
    # the row is not reported as reaching no line.
    review = _build_review(rows, "d.pdf", "en", template_def=template)
    assert "unmapped" not in [c["type"] for c in review["checks"]]


def test_a_pinned_template_that_declares_nothing_refuses_the_grid_AND_names_every_figure():
    """A definition with an empty ``statements`` list is valid — the schema defaults it and the
    publish gate does not demand one. Deciding "is there a template" by asking whether any key is
    declared read that as "no template", so the grid refused every statement while the queue raised
    nothing: every figure appearing nowhere and named by no finding, under a refusal whose own
    sentence promises they are in the review queue."""
    empty = {"schema_version": 1, "template_key": "hollow", "name": "Hollow", "statements": []}
    rows = [_row("bs_current_assets__inventories", "Inventories", 100, y=0.20),
            _row("bs_current_assets__cash", "Cash", 50, y=0.24)]
    grid = _build_statement(rows, empty, "balance_sheet", "f.pdf")
    assert grid["rows"] == []
    assert grid["refused"]["reason"] == "statement_not_in_template"
    # …and the promise the refusal makes is kept: both figures are named.
    checks = _build_review(rows, "d.pdf", "en", template_def=empty)["checks"]
    assert [c["type"] for c in checks] == ["unmapped", "unmapped"]
    assert [c["remap"]["current_key"] for c in checks] == [
        "bs_current_assets__inventories", "bs_current_assets__cash"]


def test_a_declared_statement_with_no_line_items_says_that_rather_than_blaming_the_page_scope():
    """Three causes of an empty grid, and naming the wrong one sends the analyst to the wrong place:
    widening the page scope does not fix a template with no lines under a statement."""
    hollow_stmt = {**MINI, "statements": [{"type": "balance_sheet", "label": "BS", "sections": []}]}
    grid = _build_statement([_row("bs_current_assets__inventories", "Inventories", 100)],
                            hollow_stmt, "balance_sheet", "f.pdf")
    assert grid["rows"] == []
    assert grid["refused"]["reason"] == "template_declares_no_lines"
    assert "page scope" not in grid["refused"]["message"]


def test_an_empty_matrix_says_why_too():
    """The declared-but-empty case the comparative grid already answered."""
    empty = _build_statement([], _WITH_EQUITY, "changes_in_equity", "f.pdf")
    assert empty["rows"] == [] and empty["refused"]["reason"] == "basis_not_extracted"
    assert empty["refused"]["message"] == empty["viewer"]["callout"]


def test_a_row_mapped_to_a_declared_equity_line_is_still_reported():
    """``_declared_line_keys`` must skip matrix statements. The matrix renders movement rows and never
    consults the skeleton, so counting an equity statement's declared lines as grid keys made a row
    mapped to one reach no face and raise no finding at once — declared enough to be silent."""
    declared_equity = {**MINI, "statements": [
        *MINI["statements"],
        {"type": "changes_in_equity", "label": "Changes in equity", "sections": [
            {"node_id": "eqs", "label": "Movements", "children": [
                {"canonical_key": "eq_dividends", "label": "Dividends paid", "role": "line"}]}]}]}
    # An ordinary two-column row, so it is not matrix-shaped and the matrix cannot show it.
    row = _row("eq_dividends", "Dividends paid", -500, y=0.20)
    assert _build_statement([row], declared_equity, "changes_in_equity", "f.pdf")["rows"] == []
    assert "eq_dividends" not in [r["id"] for r in
                                  _build_statement([row], declared_equity, "balance_sheet",
                                                   "f.pdf")["rows"]]
    assert [c["type"] for c in _build_review([row], "d.pdf", "en",
                                             template_def=declared_equity)["checks"]] \
        == ["unmapped"]


# DELETED: test_the_off_template_card_inherits_what_the_low_confidence_card_was_about.
#
# Its subject was that this card collapsed its `delta` to the mapping score and fingerprinted
# the confidence band WHEN THE MAPPING WAS ALSO WEAK -- so that pre-empting the low-confidence
# card did not swallow what that card was about. There is no low-confidence card to pre-empt any
# more: a weak mapping raises nothing, and this card reports a different fact (the figure
# reaches no line of the spread) whose severity does not depend on how confidently it was
# mapped. The property it defended cannot be violated because the thing it protected is gone.

def test_the_tabs_still_partition_the_queue_with_both_routes_into_the_category_present():
    """A type with no tab of its own is a finding invisible under every filter.

    Both ways into the one row-shaped category are present below — a row mapped to an undeclared
    concept, and a row nothing claimed — because they are one type and therefore one tab. When they
    were two types this test proved the second had a tab; now it proves they did not become two.
    """
    review = _build_review([_row(OFF_KEY, "Prepaid rates", 4_200, y=0.30),
                            {**_row(None, "Unplaceable", 5, y=0.40), "mapping_confidence": None}],
                           "d.pdf", "en", template_def=MINI)
    buckets = [t for t in review["tabs"] if t["types"] is not None]
    assert sum(t["count"] for t in buckets) == len(review["checks"])
    for tab in buckets:
        assert tab["count"] == len([c for c in review["checks"] if c["type"] in tab["types"]])


# ------------------------------------------------------------------------------------------------
# 3. The template's ORDER is the statement's order — the user's item 4, from the API side
# ------------------------------------------------------------------------------------------------
def _expected_skeleton(template: dict, statement_type: str) -> list[str]:
    """The row ids one statement's template declares, in order — derived HERE from the raw JSON
    rather than from the route's own walk, so the assertion is independent of the code it checks."""
    stmt = next(s for s in template["statements"] if s["type"] == statement_type)
    out: list[str] = []
    for sec in stmt["sections"]:
        children = [c for c in sec.get("children") or [] if c.get("canonical_key")]
        if not children:
            if sec.get("canonical_key"):
                out.append(sec["canonical_key"])
            continue
        out.append(f"sec_{sec.get('node_id', '')}")
        out += [c["canonical_key"] for c in children]
    return out


def test_the_profit_and_loss_is_exactly_the_templates_rows_in_the_templates_order():
    template = _shipped()
    expected = _expected_skeleton(template, "profit_and_loss")
    # An off-template row present alongside the real ones: this is the state that produced the
    # symptom, because the bucket appended it (and, with a stale template, the totals) after
    # everything the template declares.
    rows = [_row("pl_income__revenue_from_operations", "Revenue", 45_230, y=0.20),
            _row("pl_expenses__cost_of_goods_sold", "Cost of sales", -18_330, y=0.24),
            _row("pl_share_of_associates_alien", "Share of an associate (not on this template)",
                 120, y=0.28)]
    grid = _build_statement(rows, template, "profit_and_loss", "f.pdf")

    assert [r["id"] for r in grid["rows"]] == expected
    # The user's line, named: gross profit sits at the template's own index for it, which is between
    # cost of sales and the operating expenses — not after the last section.
    idx = expected.index("pl_gross_profit")
    assert grid["rows"][idx]["id"] == "pl_gross_profit"
    assert idx < len(grid["rows"]) - 1
    assert grid["rows"][-1]["id"] == expected[-1] != "pl_gross_profit"
    # …and it is a computed total, so it carries a figure derived from the lines above it rather
    # than a printed one: 45,230 − 18,330.
    assert grid["rows"][idx]["v1"] == 26_900


def test_every_statement_of_the_shipped_template_renders_only_its_own_lines():
    template = _shipped()
    for statement_type in ("balance_sheet", "profit_and_loss", "cash_flow"):
        expected = _expected_skeleton(template, statement_type)
        prefix = expected[-1].split("_", 1)[0]
        rows = [_row(k, k, 10, y=0.2 + 0.02 * i)
                for i, k in enumerate(expected[:3] + [f"{prefix}_not_on_this_template"])
                if not k.startswith("sec_")]
        grid = _build_statement(rows, template, statement_type, "f.pdf")
        assert [r["id"] for r in grid["rows"]] == expected, statement_type


# ------------------------------------------------------------------------------------------------
# 4. The matrix statement: refused when the template declares no such statement
# ------------------------------------------------------------------------------------------------
def _movement(label, y=0.2):
    """An equity movement: two NAMED component cells, which is what makes it a matrix row."""
    return {"source_label": label, "canonical_key": None, "values": [
        {"basis": "consolidated", "period_label": "Retained profits", "value": "-8401124",
         "provenance": {"source_kind": "native", "page_index": 9,
                        "bbox": {"x0": 0.55, "y0": y, "x1": 0.62, "y1": y + 0.017}}},
        {"basis": "consolidated", "period_label": "Total equity", "value": "-8401124",
         "provenance": {"source_kind": "native", "page_index": 9,
                        "bbox": {"x0": 0.80, "y0": y, "x1": 0.88, "y1": y + 0.017}}}]}


_WITH_EQUITY = {**MINI, "statements": [*MINI["statements"],
                                       {"type": "changes_in_equity",
                                        "label": "Changes in equity", "sections": []}]}


def test_a_template_that_declares_no_equity_statement_refuses_the_matrix():
    """The tab is out of the nav, but the route, the builder and the ``StatementKey`` are live, so a
    deep link still arrives here. It used to arrive at every parsed movement and every detected
    column, with no reference at all to what the template declares."""
    rows = [_movement("Loss for the year", y=0.20), _movement("Dividends paid", y=0.24)]
    refused = _build_statement(rows, MINI, "changes_in_equity", "f.pdf")

    assert refused["rows"] == []
    assert refused["columns"] == []
    # Refused out loud, and machine-readably: an empty grid on its own reads as "the extraction
    # found nothing", which sends the analyst back to the filing after figures that were never
    # going to be shown.
    assert refused["refused"]["reason"] == "statement_not_in_template"
    assert refused["refused"]["message"] == refused["viewer"]["callout"]
    for locale in _LOCALES:
        localized = _build_statement(rows, MINI, "changes_in_equity", "f.pdf", locale=locale)
        assert localized["rows"] == []
        assert localized["refused"]["reason"] == "statement_not_in_template", locale
        # The sentence is localized, not left in English over a blank spread.
        assert localized["refused"]["message"], locale
        if locale != "en":
            assert localized["refused"]["message"] != refused["refused"]["message"], locale


def test_the_matrix_is_served_in_full_when_the_template_declares_the_statement():
    """Row by row is not the unit of the decision: a movement is not a template line item, so the
    template can only declare the statement. Declared, every parsed movement shows as printed."""
    rows = [_movement("Loss for the year", y=0.20), _movement("Dividends paid", y=0.24)]
    served = _build_statement(rows, _WITH_EQUITY, "changes_in_equity", "f.pdf")
    assert [r["label"] for r in served["rows"]] == ["Loss for the year", "Dividends paid"]
    assert [c["key"] for c in served["columns"]] == ["Retained profits", "Total equity"]
    assert served["refused"] is None


def test_a_movement_on_a_declared_matrix_is_not_reported_as_reaching_no_spread():
    """The matrix decides per STATEMENT and the queue decides per ROW, so the two can disagree about
    one row unless the queue knows the matrix showed it. Unfixed, this payload rendered the movement
    on the equity face and reported it as appearing on no spread at the same time."""
    mapped = {**_movement("Loss for the year", y=0.20),
              "canonical_key": "eq_movement__loss_for_the_year", "mapping_confidence": 1.0}
    served = _build_statement([mapped], _WITH_EQUITY, "changes_in_equity", "f.pdf")
    assert [r["label"] for r in served["rows"]] == ["Loss for the year"]
    assert "unmapped" not in [c["type"] for c in
                                 _build_review([mapped], "d.pdf", "en",
                                               template_def=_WITH_EQUITY)["checks"]]
    # …and with the statement NOT declared the row reaches nothing, so the finding IS raised.
    assert _build_statement([mapped], MINI, "changes_in_equity", "f.pdf")["rows"] == []
    assert [c["type"] for c in _build_review([mapped], "d.pdf", "en",
                                             template_def=MINI)["checks"]] == ["unmapped"]


def test_no_template_at_all_still_serves_the_movements_it_extracted():
    """Nothing to refuse against — the same fallback the comparative grid takes."""
    rows = [_movement("Loss for the year", y=0.20)]
    served = _build_statement(rows, None, "changes_in_equity", "f.pdf")
    assert [r["label"] for r in served["rows"]] == ["Loss for the year"]


def test_a_refused_matrix_hides_no_figure_because_the_movements_are_in_the_queue():
    """The honesty condition on the refusal: a movement that mapped to no concept is an
    ``unmapped`` finding, with the re-map offer that files it onto a line."""
    rows = [_movement("Loss for the year", y=0.20)]
    review = _build_review(rows, "d.pdf", "en", template_def=MINI)
    assert [c["type"] for c in review["checks"]] == ["unmapped"]
    assert review["checks"][0]["remap"]["row_ref"] == _row_ref(rows[0])


# ------------------------------------------------------------------------------------------------
# 5. A run pinned to a superseded template says so
# ------------------------------------------------------------------------------------------------
def _session():
    from app.db.base import SessionLocal, init_db

    init_db()
    return SessionLocal()


def _seed(session, rows, template_def=None, *, template_key=None, version=1):
    """A document with one succeeded run, optionally pinned to a stored template version."""
    from app.db.models import Document, ExtractionRun, TemplateVersion

    doc = Document(filename="f.pdf", fmt="pdf", byte_size=1, page_count=1,
                   content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                   status="extracted")
    session.add(doc)
    session.flush()
    options: dict = {}
    if template_def is not None:
        tv = TemplateVersion(template_key=template_key or f"t-{uuid.uuid4().hex[:8]}",
                             name="t", version=version, definition=template_def)
        session.add(tv)
        session.flush()
        options["template_version_id"] = tv.id
    run = ExtractionRun(document_id=doc.id, status="succeeded", options=options,
                        result={"rows": rows, "filename": "f.pdf"})
    session.add(run)
    session.commit()
    return doc.id


def _publish_next(session, template_key: str, version: int, definition: dict) -> None:
    from app.db.models import TemplateVersion

    session.add(TemplateVersion(template_key=template_key, name="t", version=version,
                                definition=definition))
    session.commit()


def _statement(client, doc_id, statement="balance_sheet"):
    r = client.get(f"{API}/documents/{doc_id}/statement", params={"statement": statement})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_run_on_the_newest_template_version_is_not_reported_as_superseded(client):
    key = f"sup-{uuid.uuid4().hex[:8]}"
    with _session() as s:
        doc_id = _seed(s, [_row("bs_current_assets__inventories", "Inventories", 100)],
                       template_def=MINI, template_key=key, version=1)
    block = _statement(client, doc_id)["superseded_template"]
    assert block == {"superseded": False, "run_version": 1, "latest_version": 1,
                     "template_key": key}


def test_a_run_pinned_to_an_older_template_version_says_so_with_both_numbers(client):
    """A run cannot be un-pinned, so a document extracted before a revision keeps rendering the old
    shape — which is how a corrected line order still looks wrong. Stated, never acted on: nothing
    re-extracts on its own, because a fresh run discards every manual value on this one."""
    key = f"sup-{uuid.uuid4().hex[:8]}"
    with _session() as s:
        doc_id = _seed(s, [_row("bs_current_assets__inventories", "Inventories", 100)],
                       template_def=MINI, template_key=key, version=1)
        # A later revision of the SAME key. `_publish` leaves `is_published` False, so a check that
        # filtered on that flag would report this run as current.
        _publish_next(s, key, 3, MINI)
    block = _statement(client, doc_id)["superseded_template"]
    assert block == {"superseded": True, "run_version": 1, "latest_version": 3,
                     "template_key": key}
    # The spread itself is unchanged — the run is still pinned to what it ran against.
    assert [r["id"] for r in _statement(client, doc_id)["rows"]] == [
        "sec_s1", "bs_current_assets__inventories", "bs_current_assets__cash"]


def test_a_run_with_no_template_has_no_superseded_block_to_report(client):
    with _session() as s:
        doc_id = _seed(s, [_row("bs_current_assets__inventories", "Inventories", 100)])
    assert _statement(client, doc_id)["superseded_template"] is None


# ------------------------------------------------------------------------------------------------
# 6. The off-template finding resolves, over HTTP, through the re-map endpoint
# ------------------------------------------------------------------------------------------------
def test_the_off_template_finding_is_resolved_by_re_mapping_the_row(client):
    """The whole point of raising the finding rather than rendering the row: it is actionable. The
    ``row_ref`` on the card has to resolve to the stored row, and once it is re-mapped onto a line
    the template declares, the figure appears on the grid and the finding goes away."""
    off = _row(OFF_KEY, "Prepaid rates and government levies", 4_200, y=0.30)
    with _session() as s:
        doc_id = _seed(s, [_row("bs_current_assets__inventories", "Inventories", 100, y=0.20),
                           off], template_def=MINI)

    review = client.get(f"{API}/documents/{doc_id}/review").json()
    card = next(c for c in review["checks"] if c["type"] == "unmapped")
    assert card["remap"]["row_ref"] == _row_ref(off)
    assert OFF_KEY not in [r["id"] for r in _statement(client, doc_id)["rows"]]

    r = client.post(f"{API}/documents/{doc_id}/review/remap",
                    json={"row_ref": card["remap"]["row_ref"],
                          "canonical_key": "bs_current_assets__cash",
                          "reason": "checked p.1 — it is a prepayment held in cash"})
    assert r.status_code == 200, r.text

    moved = next(x for x in _statement(client, doc_id)["rows"]
                 if x["id"] == "bs_current_assets__cash")
    assert moved["v1"] == 4_200                       # on the grid, on a line the template declares
    after = client.get(f"{API}/documents/{doc_id}/review").json()
    assert [c["type"] for c in after["checks"]] == []
