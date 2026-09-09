"""THE ONTOLOGY IS GONE. This file is both the record of what was retired with it and the guard
that stops it coming back.

The line-item set is the SINGLE configuration engine. There is no second registry to select, so
there is no `mapping_engine` switch, no `ontology_versions` table, no `/ontologies` route, and
nothing a user, an API consumer or an operator meets says "ontology". The tests below assert each
half of that, because the failure this closes was not a missing feature — the merged line-item
configuration SHIPPED, fully built and parity-proven, behind a switch that defaulted to the
ontology, so what a user configured on the Line Items screen affected nothing. A guard that only
watched the new path would not have caught that. These watch the OLD path's absence.

── WHAT WAS DELETED, WHAT IT PINNED, AND WHY THAT BEHAVIOUR IS GONE ─────────────────────────────

tests/test_mapping_engine_fallback.py
    Pinned the registry SWITCH: `Settings().extraction.mapping_engine == "ontology"` as the
    shipped default, and `typing.get_args(...)` on the field's `Literal` being exactly
    {"ontology", "line_items"} — plus the branch in `stages/map_ontology.py` that read the
    setting and routed a run to `LineItemMatcher` only when it said "line_items". Its own
    docstring called the line-item path "a WEAKER path by construction ... kept for a run that
    must complete with an unreachable gateway", and its first test was titled "the default engine
    is the incumbent". Retired because the switch is the defect: both statements are now false by
    construction. There is one engine, it is the line-item set, and it is not selectable. See
    `config.py` (the comment standing where the field was) and `stages/map_ontology.py:593`.
    Nothing about the matching MECHANISM was retired with it — that is proven code and still runs;
    only its INPUT changed, and `tests/test_line_item_matching.py` continues to hold it.

tests/test_ontology_download.py
    Pinned GET /ontologies/skeleton — the authoring aid that emitted a blank rulebook shaped for a
    template, and the round trip that the emitted JSON POSTs straight back and publishes
    (`SkeletonError`). Retired with `app/services/ontology_skeleton.py`: the artefact it produced
    was a stored, selectable, user-visible ontology, which is exactly the thing being removed.
    The equivalent need — "what shape does the configuration want" — is answered by the shipped
    line-item set itself (`app/sample/templates/output_csv_hk_line_items.json`, 475 items) and by
    the /line-items surface.

tests/test_ontology_xlsx.py
    Pinned the rulebook WORKBOOK endpoints: `build_ontology_xlsx` / `parse_ontology_xlsx`, their
    sheet contract (SHEET_CONCEPTS, SHEET_CONFIG, EMPTY_MARKER) and the lossless round trip, plus
    `OntologySheetError` on a workbook it could not read. Retired with
    `app/services/ontology_xlsx.py` — an import/export format for a configuration object that no
    longer exists. It is not a coverage loss in the pipeline: nothing in a run read it.

tests/test_ontology_edit.py
    Pinned PATCH /ontologies/{id}/mappings — editing one concept's aliases and getting a NEW
    stored version back rather than an in-place mutation, so a past run stayed explainable. The
    versioned-publish INVARIANT it protected is live and has moved, not vanished: it is now
    `line_item_versions` plus `extraction_runs.line_item_version_id`, the pin that makes a run
    reproducible. The item PATCH that replaces this endpoint is covered by the /line-items route
    tests and by the e2e run; what is retired is the ontology-shaped address it was reached at.

tests/test_ontology_edit_criteria.py
    Pinned the same publish-a-new-version contract for the mapping CRITERIA (definition,
    include / exclude / confusable-with) and for the NETTING rules, via PATCH on an
    `ontology_versions` row. Retired for the reason above — the criteria and the netting rules
    themselves survive in the merged model (`app/schemas/line_items.py`), addressed as line items.

tests/test_rbac.py — NOT deleted, renamed through
    `config:ontology` became `config:line_items`: one permission for the one configuration engine.
    The permission string is served to the client and checked with `useCan`, so it is a
    user-visible surface and had to lose the word. test_rbac.py now asserts the new string is
    granted to admin and withheld from analyst and reviewer; `test_no_role_is_granted_the_old_...`
    below asserts the old string reaches nobody.

WHAT DELIBERATELY STILL SAYS "ontology" AND IS FINE: internal Python symbols nobody outside the
codebase meets — `app/stages/map_ontology.py` (the proven matching mechanism, now fed a
LineItemSet), `app/services/ontology_projection.py` (the projection that builds its working view),
`app/schemas/ontology.py` (that view's in-memory types) — and the "what stood here" comments left
where a removed path used to be, including the ones inside route docstrings. Renaming those buys
tidiness at the cost of churn across thousands of lines, and none of them is a stored object, a
route, a setting or a label. The line the guards draw is therefore not "the word is absent from
the repo" but "the word is absent from everything a user or an operator can reach", which is why
the OpenAPI scan below looks at field and path NAMES and not at prose.
"""
from __future__ import annotations

import importlib

import pytest

API = "/api/v1"

# Every module that existed only to serve the ontology as a configuration surface. Each is
# asserted UNIMPORTABLE rather than merely unused: an unused module is one import away from a
# reinstated path, and this list is the whole retired surface.
RETIRED_MODULES = [
    "app.api.routes.ontologies",      # the route group: list/publish/skeleton/xlsx/PATCH
    "app.services.ontology_select",   # picked the ontology_versions row a run would use
    "app.services.ontology_skeleton", # emitted the blank rulebook (test_ontology_download.py)
    "app.services.ontology_xlsx",     # the workbook round trip (test_ontology_xlsx.py)
]


# ── 1. THE SWITCH ────────────────────────────────────────────────────────────────────────────────

def test_there_is_no_mapping_engine_setting():
    """`mapping_engine` is not absent-by-default, it is absent. A field defaulting to
    "line_items" would still be a switch, and a switch is what let the merged configuration ship
    inert."""
    from app.config import ExtractionSettings

    assert "mapping_engine" not in ExtractionSettings.model_fields
    # And not reachable under any other name that still selects an engine.
    assert not [f for f in ExtractionSettings.model_fields if "engine" in f]


def test_an_engine_setting_cannot_be_smuggled_in_through_the_environment():
    """`extra` on the settings model decides whether `FINEX_EXTRACTION__MAPPING_ENGINE=ontology`
    lands as an attribute the pipeline could read. Pydantic must refuse or drop it, not carry it."""
    from app.config import ExtractionSettings

    s = ExtractionSettings()
    assert not hasattr(s, "mapping_engine")


# ── 2. THE STORE ─────────────────────────────────────────────────────────────────────────────────

def test_there_is_no_ontology_table_and_the_run_pins_line_items_instead():
    """The versioned store is single, and a run still PINS the configuration it used — the
    prerequisite that made removing `ontology_version_id` safe rather than a loss of
    reproducibility."""
    import app.db.models  # noqa: F401  — registers every table on the shared metadata
    from app.db.base import Base
    from app.db.models import ExtractionRun

    assert "ontology_versions" not in Base.metadata.tables
    assert not [t for t in Base.metadata.tables if "ontolog" in t]
    # The replacement exists. Without it the assertions above would be a regression, not a merge.
    assert "line_item_versions" in Base.metadata.tables

    cols = {c.name for c in ExtractionRun.__table__.columns}
    assert "ontology_version_id" not in cols
    assert not [c for c in cols if "ontolog" in c]
    assert "line_item_version_id" in cols


# ── 3. THE API SURFACE ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("GET", f"{API}/ontologies"),
    ("POST", f"{API}/ontologies"),
    ("GET", f"{API}/ontologies/1"),
])
def test_the_ontology_endpoints_are_not_served(client, method, path):
    """404, not 401/403/405: the route does not exist. A 403 would mean the endpoint is still
    mounted and merely gated, which is not removal."""
    r = client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 404, f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_the_retired_modules_do_not_import(module):
    with pytest.raises(ImportError):
        importlib.import_module(module)


def test_no_field_or_path_in_the_served_openapi_says_ontology(client):
    """THE BROAD GUARD. Deliberately not an enumeration of endpoints — a re-introduction would
    arrive as a new endpoint or a new field, which an enumeration cannot see. This walks the whole
    served schema and fails on the word appearing in any NAME: a path, a schema component, a
    property, a parameter, a request/response field, an operationId, an enum value.

    Prose is excluded on purpose. The removal notes left where each ontology path used to stand
    ("`ontology_select.select_for_template` picked a row out of `ontology_versions`; there is no
    ontology to select") live in route docstrings, which FastAPI publishes as endpoint
    descriptions. Those comments are the reason nobody reinstates the path, so a scan that failed
    on them would pressure someone to delete the explanation. What a consumer PROGRAMS against is
    the names.
    """
    spec = client.get("/openapi.json")
    assert spec.status_code == 200, spec.text
    schema = spec.json()

    PROSE = {"description", "summary", "title", "detail"}
    offenders: list[str] = []

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if "ontolog" in str(key).lower():
                    offenders.append(f"{where}.{key} (name)")
                if key in PROSE and isinstance(value, str):
                    continue                      # free text — see the docstring above
                if key in {"name", "operationId"} and isinstance(value, str):
                    if "ontolog" in value.lower():
                        offenders.append(f"{where}.{key} = {value}")
                    continue
                if key == "enum" and isinstance(value, list):
                    for v in value:
                        if "ontolog" in str(v).lower():
                            offenders.append(f"{where}.enum = {v}")
                    continue
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")
        elif isinstance(node, str) and where.startswith("$ref"):
            if "ontolog" in node.lower():
                offenders.append(f"{where} = {node}")

    walk(schema.get("paths", {}), "paths")
    walk(schema.get("components", {}), "components")

    assert not offenders, "ontology surfaced in the served API: " + "; ".join(offenders)
    # Positive control, so the scan above cannot pass by walking an empty schema.
    assert any(p.startswith(f"{API}/line-items") or "/line-items" in p for p in schema["paths"]), \
        "the /line-items surface is missing — the scan proved nothing"


def test_no_role_is_granted_the_old_configuration_permission(auth, anon_client):
    """`config:ontology` is a string the client checks with `useCan`, so it is user-visible.
    `security/rbac.py` keeps it only as a transitional ALIAS sharing `config:line_items`' value —
    which means it must never appear in a served permission list, and `Permission("config:ontology")`
    must not resolve. Both are asserted, because an alias that leaks is a name the frontend can
    still branch on."""
    from app.security.rbac import Permission

    with pytest.raises(ValueError):
        Permission("config:ontology")
    assert not [p for p in Permission if "ontolog" in p.value]

    for role in ("admin", "reviewer", "analyst"):
        me = anon_client.get(f"{API}/me", headers=auth(role)).json()
        assert not [p for p in me["permissions"] if "ontolog" in p.lower()], role
        assert not [s for s in me["screens"] if "ontolog" in s.lower()], role
