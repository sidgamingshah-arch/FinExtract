from __future__ import annotations

from app.schemas.languages import evaluate_parity
from app.schemas.loader import (
    load_ontology,
    load_template,
    validate_ontology_against_template,
    validate_template,
)

TEMPLATE = {
    "template_key": "ifrs_min",
    "name": "IFRS Minimal",
    "statements": [{
        "type": "balance_sheet",
        "sections": [{
            "node_id": "assets.current", "canonical_key": "assets.current",
            "label": "Current assets", "role": "subtotal",
            "label_i18n": {"en": "Current assets", "zh": "流动资产",
                           "ar": "الأصول المتداولة", "fr": "Actifs courants"},
            "children": [
                {"node_id": "assets.current.cash", "canonical_key": "assets.current.cash",
                 "label": "Cash", "role": "line",
                 "label_i18n": {"en": "Cash", "zh": "现金", "ar": "النقد", "fr": "Trésorerie"}},
            ],
            "rollup": {"op": "sum", "children": ["assets.current.cash"]},
        }],
        "identities": [],
    }],
}

ONTOLOGY = {
    "ontology_key": "ifrs_min_multi",
    "target_template_key": "ifrs_min",
    "supported_locales": ["en", "zh", "ar", "fr"],
    "number_format_by_locale": {
        "en": {"decimal": ".", "thousands": ","},
        "fr": {"decimal": ",", "thousands": " "},
        "zh": {"decimal": ".", "thousands": ","},
        "ar": {"decimal": ".", "thousands": ","},
    },
    "mappings": [{
        "canonical_key": "assets.current.cash",
        "aliases": ["Cash"],
        "aliases_i18n": {"en": ["Cash"], "zh": ["现金"], "ar": ["النقد"], "fr": ["Trésorerie"]},
    }],
}


def test_template_loads_and_validates():
    tpl = load_template(TEMPLATE)
    assert validate_template(tpl) == []
    assert "assets.current.cash" in tpl.all_canonical_keys()


def test_template_rejects_bad_rollup():
    bad = {**TEMPLATE}
    import copy
    bad = copy.deepcopy(TEMPLATE)
    bad["statements"][0]["sections"][0]["rollup"]["children"] = ["does.not.exist"]
    tpl = load_template(bad)
    errors = validate_template(tpl)
    assert any("unknown node_id" in e.message for e in errors)


def test_ontology_validates_against_template():
    tpl = load_template(TEMPLATE)
    ont = load_ontology(ONTOLOGY)
    assert validate_ontology_against_template(ont, tpl) == []


def test_ontology_rejects_unknown_key():
    tpl = load_template(TEMPLATE)
    bad = {**ONTOLOGY, "mappings": [{"canonical_key": "not.in.template", "aliases": ["X"]}]}
    ont = load_ontology(bad)
    errors = validate_ontology_against_template(ont, tpl)
    assert any("does not exist" in e.message for e in errors)


def test_language_parity_full_for_seed_set():
    tpl = load_template(TEMPLATE)
    ont = load_ontology(ONTOLOGY)
    parity = {p.locale: p for p in evaluate_parity(tpl, ont)}
    assert set(parity) == {"en", "zh", "ar", "fr"}
    # With per-locale labels, aliases, number formats, OCR packs, and UI bundles,
    # all four seed languages are fully supported (input = output parity).
    for loc in ("en", "zh", "ar", "fr"):
        assert parity[loc].supported, f"{loc} missing: {parity[loc].missing}"
    assert parity["ar"].rtl is True


# --- an UNDECLARED key is reported, not silently dropped --------------------------------------

def test_unknown_keys_finds_undeclared_fields_at_every_depth():
    """pydantic's default is extra='ignore', so an authoring mistake used to vanish: the
    definition published, reported success, and simply did not contain what was written."""
    import copy

    from app.schemas.loader import unknown_keys

    bad = copy.deepcopy(TEMPLATE)
    bad["inherits"] = "some_base_v1"                       # borrowed from another format
    bad["statements"][0]["sections"][0]["rollup"]["weights"] = [1, 1]   # nested, and plausible
    bad["statements"][0]["sections"][0]["canonical_keys"] = ["typo"]    # a mistyped field name

    found = unknown_keys(bad, load_template(bad))
    assert "inherits" in found
    assert "statements[0].sections[0].rollup.weights" in found
    assert "statements[0].sections[0].canonical_keys" in found


def test_unknown_keys_is_silent_on_the_shipped_definitions():
    """The guard must not accuse what the product itself ships."""
    import json
    from pathlib import Path

    from app.schemas.loader import unknown_keys

    d = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
    tpl = json.loads((d / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))
    ont = json.loads((d / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    assert unknown_keys(tpl, load_template(tpl)) == []
    assert unknown_keys(ont, load_ontology(ont)) == []
    # The v2.1 rulebook ships alongside them and goes through the same door (see
    # tests/test_ontology_v2.py for its section layer).
    v2 = json.loads((d / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    assert unknown_keys(v2, load_ontology(v2)) == []


def test_upload_refuses_a_template_with_an_undeclared_key(client):
    import copy

    bad = copy.deepcopy(TEMPLATE)
    bad["template_key"] = "strictness_probe"
    bad["inherits"] = "some_base_v1"
    r = client.post("/api/v1/templates", json={"definition": bad})
    assert r.status_code == 422
    assert any(e["location"] == "inherits" for e in r.json()["detail"]["errors"])


def test_upload_refuses_an_ontology_with_an_undeclared_key(client):
    """The same door, on the rulebook side — this is the shape the `inherits` question was about:
    a partial ontology relying on inheritance would otherwise publish with the inherited concepts
    simply absent, and pass the key cross-check because it only checks the keys that WERE sent."""
    import copy

    r = client.post("/api/v1/templates",
                    json={"definition": {**copy.deepcopy(TEMPLATE),
                                         "template_key": "strictness_probe_ont"}})
    assert r.status_code == 201

    bad = copy.deepcopy(ONTOLOGY)
    bad["target_template_key"] = "strictness_probe_ont"
    bad["inherits"] = "base_v1"
    bad["mappings"][0]["aliasses"] = ["mistyped field name"]
    r = client.post("/api/v1/ontologies", json={"definition": bad})
    assert r.status_code == 422
    locs = {e["location"] for e in r.json()["detail"]["errors"]}
    assert "inherits" in locs
    assert "mappings[0].aliasses" in locs


def test_reading_a_stored_definition_still_tolerates_an_undeclared_key():
    """The strictness is on the DOOR, not the read path. The same two loaders read every row
    already in the database, and those call sites do not all expect failure — one swallows the
    error into `template = None`, which marks an extraction SUCCEEDED with every structural
    check quietly gone. One bad row must not become an outage."""
    import copy

    stored = copy.deepcopy(TEMPLATE)
    stored["inherits"] = "some_base_v1"
    tpl = load_template(stored)                 # loads, does not raise
    assert validate_template(tpl) == []
    assert "assets.current.cash" in tpl.all_canonical_keys()
