"""A real HK income statement, and the four things that stopped it being read.

The fixture is transcribed from a published HKEX annual report (see
``make_hk_income_statement_pdf``). Four separate defects were measured on it, and they are grouped
here because they only show up together — a flat income statement prints no section banners at all,
so the section gate is off and every one of these rests on the caption alone.

    Other revenue and gains          -> UNMAPPED, though the concept declares that exact alias
    Tax                              -> UNMAPPED, discarded as "a caption that identifies nothing"
    LOSS FROM OPERATING ACTIVITIES   -> pl_expenses__others, a total filed as an expense line
    every subtotal                   -> LineRole.LINE, so nothing knew to compute it

The third is the expensive one. It put a statement total in the expenses section's residual
alongside a genuine expense row — two rows on one concept — and the rulebook's own exclusion for
that concept says, in as many words, "Section subtotals and statement totals".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat, LineRole
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def statement():
    from tests.fixtures.generate import make_hk_income_statement_pdf

    ontology = load_ontology(
        json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")), resolve=True)
    template = load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))
    ctx = PipelineContext(raw_bytes=make_hk_income_statement_pdf())
    ctx.ontology, ctx.template = ontology, template
    doc = default_pipeline().run(DocumentModel(filename="is.pdf", fmt=DocFormat.PDF), ctx)
    return doc, ctx, template


def _by_label(doc) -> dict[str, object]:
    return {(li.source_label or "").strip(): li for li in doc.line_items}


# --- a short caption is still a caption -----------------------------------------------------------

@pytest.mark.parametrize("caption,is_reference", [
    # The regression: every part of the old pattern was optional and it ended in [\w.()]{0,3}, so
    # ANY caption of three characters or fewer was ruled out as naming nothing.
    ("Tax", False),
    ("VAT", False),
    ("Fee", False),
    ("Cost of sales", False),
    # A reference is a number or a bracketed letter.
    ("Notes", True),
    ("Note 12", True),
    ("note 12(a)", True),
    ("附註12", True),
    ("12", True),
    ("12.", True),
    ("(a)", True),
    ("(iv)", True),
    ("", True),
])
def test_only_a_reference_shaped_caption_identifies_nothing(caption, is_reference):
    from app.stages.residual import _NOTE_REF_ONLY

    assert bool(_NOTE_REF_ONLY.match(caption)) is is_reference


# --- a concept may not delete its own alias -------------------------------------------------------

def test_a_concept_whose_hint_deletes_its_own_alias_is_refused_at_load():
    """An exclusion outranks an alias at match time, and it has to — the field exists so an editor
    looking at a mis-mapping can add one line and have it stop. The consequence is that a hint broad
    enough to match the concept's OWN alias deletes it with no signal at all, so the contradiction
    is refused at the door instead. The message has to name all three parts or the fix needs a
    search."""
    definition = {
        "schema_version": 1, "ontology_key": "t", "name": "t",
        "target_template_key": "t", "target_template_version": 1,
        "mappings": [{
            "canonical_key": "pl_income__other_income",
            "label": "Other income",
            "aliases": ["Other revenue and gains"],
            "exclude_hints": ["revenue"],
        }],
    }
    with pytest.raises(ValueError) as err:
        load_ontology(definition, resolve=False)

    message = str(err.value)
    assert "pl_income__other_income" in message
    assert "Other revenue and gains" in message
    assert "revenue" in message


def test_the_shipped_rulebook_declares_no_such_contradiction():
    """Eight aliases were dying this way when the check was written, every one a NEGATION killed by
    the thing it negates — 'Taxes other than income tax' by 'income tax', 'Impairment losses on
    non-financial assets' by 'financial asset'. Each hint has been narrowed."""
    load_ontology(json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")),
                  resolve=True)


def test_the_narrowed_hints_still_keep_out_what_they_were_written_for():
    """Narrowing must not disarm the exclusion. Anchoring 'revenue' to '^revenue' still keeps the
    top-line caption off other income; it just no longer eats 'Other revenue and gains'."""
    from app.services.mapping import OntologyMatcher

    ontology = load_ontology(
        json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")), resolve=True)
    matcher = OntologyMatcher(ontology)

    assert matcher._vetoed("pl_income__other_income", "Revenue from contracts with customers")
    assert not matcher._vetoed("pl_income__other_income", "Other revenue and gains")
    assert matcher._vetoed("pl_expenses__taxes_and_surcharges", "Income tax expense")
    assert not matcher._vetoed("pl_expenses__taxes_and_surcharges", "Taxes other than income tax")


# --- the template says which captions are subtotals -----------------------------------------------

def test_a_row_on_a_calculated_node_takes_the_templates_role(statement):
    """A filing does not mark its subtotals: "Gross profit" and "LOSS BEFORE TAX" are printed
    exactly like the lines they total, so reconstruction has no role to read and hands every one on
    as LINE. The template declares them, and once a row is filed against a template node that
    declaration is a fact about the row."""
    doc, _ctx, _tpl = statement
    rows = _by_label(doc)

    assert rows["Gross profit"].role is LineRole.TOTAL
    assert rows["LOSS FROM OPERATING ACTIVITIES"].role is LineRole.TOTAL
    assert rows["LOSS BEFORE TAX"].role is LineRole.TOTAL
    assert rows["LOSS FOR THE YEAR"].role is LineRole.TOTAL
    assert rows["Tax"].role is LineRole.SUBTOTAL
    # An ordinary line is untouched.
    assert rows["Administrative expenses"].role is LineRole.LINE


def test_only_an_unread_role_is_promoted():
    """A printed "Total" caption already arrives as TOTAL. The promotion adds a verdict where there
    was none; it does not replace one reconstruction managed to read."""
    from app.core.models.line_item import LineItem
    from app.stages.map_ontology import MapOntologyStage

    doc = DocumentModel(filename="f.pdf")
    already = LineItem(source_label="Total equity", canonical_key="pl_gross_profit",
                       role=LineRole.SUBTOTAL)
    plain = LineItem(source_label="Gross profit", canonical_key="pl_gross_profit")
    doc.line_items += [already, plain]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = load_template(
        json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))

    promoted = MapOntologyStage._adopt_template_roles(doc, ctx)

    assert promoted == 1
    assert already.role is LineRole.SUBTOTAL, "a role already read was overruled"
    assert plain.role is LineRole.TOTAL


def test_a_statement_total_is_not_swept_into_a_section_residual(statement):
    """THE EXPENSIVE ONE. "LOSS FROM OPERATING ACTIVITIES" had no alias, so it fell through to the
    residual router and landed in pl_expenses__others — alongside "Fair value losses on investment
    properties, net", two rows on one concept, in a bucket whose own rulebook exclusion reads
    "Section subtotals and statement totals"."""
    doc, _ctx, _tpl = statement
    rows = _by_label(doc)

    assert rows["LOSS FROM OPERATING ACTIVITIES"].canonical_key == "pl_operating_profit_ebit"
    others = [li for li in doc.line_items if li.canonical_key == "pl_expenses__others"]
    assert len(others) == 1, [li.source_label for li in others]
    assert others[0].source_label.startswith("Fair value losses")


def test_every_printed_line_of_the_statement_is_filed(statement):
    """The product rule: a line on the face of a statement does not get to be unmapped. The only
    row here without a concept is the "Notes" column header, which names no figure."""
    doc, _ctx, _tpl = statement
    unmapped = [(li.source_label or "").strip() for li in doc.line_items if not li.canonical_key]

    assert unmapped == ["Notes"], unmapped


def test_each_subtotal_is_the_sum_of_its_children(statement):
    """And the other half of the rule: the figure SERVED for a subtotal is computed from its
    children, with the printed one kept as evidence. Every subtotal on this statement is computable
    and every one agrees with what the filing printed — which is what makes a disagreement worth a
    review card rather than noise."""
    from app.api.routes.extractions import _serialize_rows
    from app.services.rollups import calculated_nodes, evaluate_rows

    doc, _ctx, template = statement
    template_def = template.model_dump(mode="json")
    rows = _serialize_rows(doc)
    computed = evaluate_rows(template_def, rows, "consolidated", "current")

    printed = {}
    for r in rows:
        key = r.get("canonical_key")
        for v in (r.get("values") or []):
            if key and v.get("basis") == "consolidated" and v.get("period_label") == "current":
                printed[key] = float(v["value"])

    checked = 0
    for key in calculated_nodes(template_def):
        c, p = computed.get(key), printed.get(key)
        if c is None or not c.computable or p is None:
            continue
        assert abs(c.value - p) < 1, (key, c.value, p)
        checked += 1
    # The five the filing actually prints: gross profit, operating profit, profit before tax,
    # the tax subtotal and profit for the year.
    assert checked >= 5, checked
