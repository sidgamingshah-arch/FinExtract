"""TWO ENTITIES ARE TWO FIGURES, AND A COMPUTED LINE MAY STAND ON ITS PARTS' ROWS.

Two things the reply contract and the stage disagreed about, measured on blind answers:

* A line that names no basis is asked with both the consolidated and the standalone statement
  blocks. Asked for current tax, the answer cited Current Tax and the earlier-years row in BOTH
  blocks as components, and the stage added all four: 2,992.59 where the group prints 1,550.07.
  Each entity's rows now make that entity's figure, filed under its own basis
  (`services.line_item_llm.split_by_entity`).
* The contract said citing a printed row for one line says it is not another's, so a computed
  line (net intangibles = 商誉 + 无形资产) whose figure stands on its parts' rows was left blank:
  citing them would have emptied goodwill. A line computed from other lines may cite their rows
  without taking them (`stages.line_item_llm._ancestry`).
"""
from __future__ import annotations

import json as _json
import pathlib as _pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import load_line_item_set
from app.services import line_item_llm
from app.stages.line_item_llm import LineItemLlmStage

FACE_PAGE = 5
BUILDINGS, LAND = "bs_nca__buildings", "bs_nca__land"
NET_INTANGIBLES, GOODWILL = "bs_nca__net_intangibles", "bs_nca__goodwill"
_TEMPLATES = _pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(autouse=True)
def _restore_extraction_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = was


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(_json.loads(
        (_TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8")), resolve=True)


def _face_row(caption, *, value, key, basis=Basis.CONSOLIDATED, page=FACE_PAGE):
    li = LineItem(source_label=caption, canonical_key=key)
    li.values["v"] = ExtractedValue(basis=basis, period_label="current", value=Decimal(value),
                                    value_raw=Decimal(value), provenance=Provenance(page_index=page))
    li.confidence.method = "exact"
    li.confidence.mapping = 1.0
    return li


def _run(shipped, answers: dict[str, dict], rows, keys, pages=None):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view

    doc = DocumentModel(filename="ar.pdf")
    doc.pages = pages or [PageSource(index=FACE_PAGE, kind=PageKind.FACE,
                                     statement="balance_sheet")]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = list(keys)

    class _P:
        id = "answers"

        def complete_structured(self, *, system, messages, response_schema, **_):
            asked = [e["key"] for e in _json.loads(messages[-1]["content"])["line_items"]]
            return response_schema.model_validate(
                {"answers": [{"key": k, "confidence": 0.9, "reason": "r", **answers[k]}
                             for k in asked if k in answers]}), {}

    ctx.registry.register("llm", "answers", lambda: _P())
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    return doc, ctx


def _figures(doc, key) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for row in doc.line_items:
        if row.canonical_key != key:
            continue
        for ev in row.values.values():
            if ev.value is not None and ev.period_label == "current":
                b = str(getattr(ev.basis, "value", ev.basis))
                out[b] = out.get(b, Decimal(0)) + Decimal(ev.value)
    return out


def _two_entity_rows():
    return [_face_row("Buildings", value="100", key=BUILDINGS),
            _face_row("Land", value="40", key=LAND),
            _face_row("Buildings", value="60", key=BUILDINGS, basis=Basis.STANDALONE),
            _face_row("Land", value="30", key=LAND, basis=Basis.STANDALONE)]


def _cite(caption, entity=None):
    out = {"statement": "balance_sheet", "caption": caption}
    if entity:
        out["entity"] = entity
    return out


# ── the pure grouping ──────────────────────────────────────────────────────────────────────────

def test_an_answer_naming_two_entities_is_grouped_per_entity_in_order():
    resolved = [{"at": 0, "entity": "consolidated", "basis": "consolidated"},
                {"at": 1, "entity": "standalone", "basis": "standalone"},
                {"at": 2, "entity": "consolidated", "basis": "consolidated"},
                {"at": 3, "basis": "standalone", "note": "7"}]
    unresolved = [{"at": 4, "entity": "standalone"}, {"at": 5}]
    groups = line_item_llm.split_by_entity(resolved, unresolved)
    assert [[e["at"] for e in g[0]] for g in groups] == [[0, 2], [1, 3]]
    assert [[e["at"] for e in g[1]] for g in groups] == [[5], [4]]


def test_an_answer_naming_one_entity_or_none_is_one_group_as_before():
    one = [{"at": 0, "entity": "consolidated"}, {"at": 1}]
    assert line_item_llm.split_by_entity(one, []) == [(one, [])]
    none = [{"at": 0, "basis": "consolidated"}, {"at": 1, "basis": "standalone"}]
    assert line_item_llm.split_by_entity(none, []) == [(none, [])]


# ── the stage ──────────────────────────────────────────────────────────────────────────────────

def test_components_cited_in_both_entities_are_added_per_entity(shipped):
    """Before: all four rows were added under one basis (230)."""
    answer = {"role": "component", "signs": [1, 1, 1, 1],
              "sources": [_cite("Buildings", "consolidated"), _cite("Land", "consolidated"),
                          _cite("Buildings", "standalone"), _cite("Land", "standalone")]}
    doc, ctx = _run(shipped, {BUILDINGS: answer}, _two_entity_rows(), [BUILDINGS])
    assert _figures(doc, BUILDINGS) == {"consolidated": Decimal("140"),
                                        "standalone": Decimal("90")}, ctx.logs[-12:]


def test_a_whole_is_chosen_per_entity(shipped):
    """Before: the consolidated row was taken and the standalone one was read as a disagreeing
    corroboration of it, so the standalone figure was never written and the line went to review."""
    answer = {"sources": [_cite("Buildings", "consolidated"), _cite("Buildings", "standalone")]}
    doc, ctx = _run(shipped, {BUILDINGS: answer}, _two_entity_rows(), [BUILDINGS])
    assert _figures(doc, BUILDINGS) == {"consolidated": Decimal("100"),
                                        "standalone": Decimal("60")}, ctx.logs[-12:]
    flags = [f for r in doc.line_items if r.canonical_key == BUILDINGS
             for f in r.confidence.flags]
    assert not [f for f in flags if f.startswith("llm_citations_disagree")], flags


def test_a_computed_line_citing_its_parts_row_does_not_empty_the_part(shipped):
    """002273: net intangibles = 商誉 + 无形资产. Goodwill is left as printed; the computed line
    cites the 商誉 row as a component. Before: goodwill was emptied as 'claimed by' its parent."""
    rows = [_face_row("商誉", value="500", key=GOODWILL)]
    answer = {"role": "component", "signs": [1], "sources": [_cite("商誉")]}
    doc, ctx = _run(shipped, {NET_INTANGIBLES: answer, GOODWILL: {"sources": []}}, rows,
                    [NET_INTANGIBLES, GOODWILL])
    assert _figures(doc, GOODWILL) == {"consolidated": Decimal("500")}, ctx.logs[-12:]
    assert _figures(doc, NET_INTANGIBLES) == {"consolidated": Decimal("500")}
    flags = [f for r in doc.line_items for f in r.confidence.flags]
    assert not [f for f in flags if f.startswith("llm_printed_row_claimed_by")], flags


def test_a_computed_line_and_its_part_citing_one_row_both_keep_it(shipped):
    rows = [_face_row("商誉", value="500", key=GOODWILL)]
    doc, ctx = _run(shipped, {NET_INTANGIBLES: {"role": "component", "signs": [1],
                                                "sources": [_cite("商誉")]},
                              GOODWILL: {"sources": [_cite("商誉")]}},
                    rows, [NET_INTANGIBLES, GOODWILL])
    assert _figures(doc, GOODWILL) == {"consolidated": Decimal("500")}, ctx.logs[-12:]
    assert _figures(doc, NET_INTANGIBLES) == {"consolidated": Decimal("500")}


def test_unrelated_lines_sharing_a_row_are_still_settled(shipped):
    """The kinship is the definition's, not any two lines': Land citing the Buildings row still
    takes it from Buildings."""
    rows = [_face_row("Buildings", value="9999", key=BUILDINGS)]
    doc, _ = _run(shipped, {LAND: {"sources": [_cite("Buildings")]}, BUILDINGS: {"sources": []}},
                  rows, [BUILDINGS, LAND])
    assert _figures(doc, BUILDINGS) == {}


def test_the_ancestry_follows_a_calculated_lines_terms(shipped):
    from app.stages.line_item_llm import _ancestry
    up = _ancestry(shipped)
    assert NET_INTANGIBLES in up(GOODWILL)
    assert NET_INTANGIBLES in up("bs_nca__other_intangible_assets")
    assert LAND not in up(BUILDINGS) and BUILDINGS not in up(LAND)


# ── what the model is told ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("contract", [line_item_llm.REPLY_CONTRACT,
                                      line_item_llm.FACE_ONLY_REPLY_CONTRACT],
                         ids=["with_notes", "face_only"])
def test_the_contract_says_both_rules(contract):
    assert "Where both entities print the line, cite each entity's rows" in contract
    assert "never the two added together" in contract
    assert ("A line whose definition computes it from other lines may cite the rows of those "
            "lines") in contract


def test_no_icon_definition_says_less_twice():
    items = _json.loads((_TEMPLATES / "output_csv_icon_line_items.json").read_text("utf-8"))
    items = items["items"] if isinstance(items, dict) else items
    bad = [i["key"] for i in items if " less Less" in str(i.get("definition") or "")]
    assert not bad, bad
    net = next(i for i in items if i["key"] == "pl_icon__net_sales")
    assert "less excise duty" in net["definition"]
