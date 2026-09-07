"""``never_sweep`` names things, and an entry that names nothing is refused rather than shipped.

THE BUG THIS FILE EXISTS FOR. 358 of the 462 concepts in ``output_csv_hk_ontology.json`` shipped
carrying ``never_sweep: ["True"]`` — the literal string. The field is the list of concepts and
prose a residual bucket must never absorb however similar the wording, and it works by NAMING:
:func:`app.stages.residual._residuals` looks each entry up as a ``canonical_key`` and expands a hit
to that concept's captions in every locale, keeping a miss as prose that
:func:`app.stages.residual._vetoed_by_never_sweep` substring-matches against a candidate caption.
"True" is a miss, so it became the one-word sentence "true", and the only caption that can be a
substring of "true" is "true" — which no filing prints. On all 358 concepts the protection was
inert, and on the 347 of them that are not residuals it was never even read.

The provenance is a spreadsheet round-trip: ``scripts/build_output_csv_template.py`` reads the
ontology workbook's "Never sweep" column as ``_split_comma(_cv(r, "Never sweep"))``, so a TRUE
ticked into a cell whose contract is a comma-separated list of keys arrives as ``["True"]``.

The tests below are in three groups: what "True" actually did on both consumer paths (so the
diagnosis is reproducible rather than asserted), that the shipped seed no longer carries it, and
that the schema now refuses all four boolean spellings so the round-trip cannot re-introduce it
silently. The last group is the durable one — the seed can be rebuilt from a workbook at any time.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.loader import load_ontology
from app.schemas.ontology import OntologyMapping
from app.services.mapping import normalize_label
from app.services.rollups import section_members
from app.stages.residual import (
    _dedicated_captions,
    _read_terms,
    _residuals,
    _untested_dedicated,
    _vetoed_by_never_sweep,
)

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
_SEED = _SAMPLES / "output_csv_hk_ontology.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(_SEED.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ontology(raw: dict):
    """Resolved, the way the extraction worker loads it — ``_residuals`` needs the section layer."""
    return load_ontology(copy.deepcopy(raw), resolve=True)


@lru_cache(maxsize=1)
def _terms():
    """The shipped framework's own terms — the sweep is read from the rulebook, not hard-coded."""
    return _read_terms(load_ontology(json.loads(_SEED.read_text(encoding="utf-8")),
                                     resolve=True).residual_framework)


def _fake_ontology(*concepts):
    """The two attributes ``_residuals`` reads off a definition, with no other rulebook in the way."""
    return type("Ont", (), {"mappings": list(concepts), "section_defaults": {}})()


def _residual(entries, **kwargs):
    """One residual carrying ``entries``, expanded the way :func:`_residuals` expands them.

    ``model_construct`` deliberately, for the boolean cases: the validator added with this file is
    what makes ``["True"]`` unconstructible through ``OntologyMapping``, so reproducing the shipped
    bug now requires bypassing the very gate that ends it. Going through the constructor here would
    make these two tests assert the gate a second time instead of the behaviour that motivated it.
    """
    concept = OntologyMapping.model_construct(
        canonical_key="sec__others", value_scope="exclusive_residual",
        section_scope=["sec"], never_sweep=list(entries), **kwargs)
    built = _residuals(_fake_ontology(concept), _terms())
    assert len(built) == 1
    return built[0]


# ── 1. what "True" did, on both paths ─────────────────────────────────────────────────────────

def test_a_boolish_entry_names_no_concept_so_it_lands_on_the_prose_path():
    """Path A. ``_residuals`` resolves an entry against ``canonical_key`` first; nothing in any
    rulebook is keyed "True", so the caption index it should have populated stays empty."""
    res = _residual(["True"])

    assert res.never_keys == {}                      # no concept, so no captions to protect
    assert res.never_prose == ("true",)              # kept as a one-word sentence instead


def test_the_prose_true_cannot_veto_any_caption_a_filing_would_print(ontology):
    """Path B. The prose branch asks whether the candidate caption is a substring of the sentence,
    so "true" can only veto "true". Measured against every caption the rulebook itself knows —
    label, default aliases and every locale in ``aliases_i18n`` — it refuses none of them."""
    res = _residual(["True"])

    captions = set()
    for m in ontology.mappings:
        captions.update(c for c in [m.label or "", *(m.aliases or [])] if c)
        for per_locale in (m.aliases_i18n or {}).values():
            captions.update(c for c in (per_locale or []) if c)
    assert len(captions) > 1_500                     # 1,993 at the time of writing
    assert [c for c in captions if _vetoed_by_never_sweep(res, c)] == []

    # The captions the bug actually cost protection for — a subtotal the mapper missed is the case
    # `_vetoed_by_never_sweep`'s own docstring names — and the veto that should have caught them.
    for caption in ("Total current liabilities", "Total equity", "其他应收款"):
        assert _vetoed_by_never_sweep(res, caption) is None
    # …while an entry that NAMES something does refuse them.
    named = _residual(["a caption reading total current liabilities"])
    assert _vetoed_by_never_sweep(named, "Total current liabilities") is not None

    # And "true" is not harmless-because-unreachable in principle: it vetoes exactly one string.
    assert _vetoed_by_never_sweep(res, "True") == "true"


def test_a_non_residual_concepts_never_sweep_is_never_read_at_all():
    """347 of the 358 were ``exclusive_leaf``. ``_residuals`` iterates only over
    ``exclusive_residual`` concepts, so for those the field had no consumer in the first place —
    which is why "this concept must never be swept" cannot be what the entry was expressing."""
    leaf = OntologyMapping(canonical_key="sec__trade_receivables", value_scope="exclusive_leaf",
                           section_scope=["sec"], never_sweep=["bs_cl__total_current_liabilities"])

    assert _residuals(_fake_ontology(leaf), _terms()) == []


def test_never_swept_at_all_is_already_prohibition_4_not_a_missing_field(ontology):
    """The reading that would have JUSTIFIED a boolean — "this concept must never be swept AT
    ALL" — is already implemented, generically, for every dedicated concept in every section:
    prohibition 4 (``never_untested``) refuses a row printed with a dedicated concept's own caption
    inside that concept's own section. It is switched on in this rulebook and it covers every one
    of the 347 leaf concepts that carried ``["True"]``, with nothing authored per concept. So a new
    boolean field would have been a second, weaker copy of a live switch."""
    terms = _read_terms(ontology.residual_framework)
    assert "never_untested" in terms.prohibitions

    captions = _dedicated_captions(ontology, section_members(ontology))
    leaves = [m for m in ontology.mappings
              if m.value_scope != "exclusive_residual" and m.unit_of_account != "subtotal"
              and m.section_scope and m.label]
    assert len(leaves) > 300
    unprotected = [m.canonical_key for m in leaves
                   if _untested_dedicated(captions, m.section_scope[0], m.label) is None]
    assert unprotected == []


# ── 2. the shipped seed ───────────────────────────────────────────────────────────────────────

def test_the_shipped_seed_carries_no_boolish_never_sweep(raw: dict):
    """The 358 blocks are gone, and gone by DELETION: a concept that meant nothing now says
    nothing, matching the 104 concepts that never carried the key. Left as ``[]`` it would still
    read as a considered answer in the ontology editor's "Never sweep" column."""
    boolish = {m["canonical_key"]: m["never_sweep"] for m in raw["mappings"]
               if any(str(e).strip().lower() in ("true", "false")
                      for e in (m.get("never_sweep") or []))}

    assert boolish == {}
    assert [m["canonical_key"] for m in raw["mappings"] if m.get("never_sweep") == []] == []
    assert len(raw["mappings"]) == 462          # nothing else was dropped with the blocks


def test_the_seed_still_loads_and_builds_its_eleven_residuals(ontology):
    """The other half of a deletion: removing a key must not remove a residual. All 11 still build,
    now with an honestly empty veto index rather than an index of one useless sentence."""
    res = _residuals(ontology, _read_terms(ontology.residual_framework))

    assert len(res) == 11
    assert all(r.never_keys == {} and r.never_prose == () for r in res)
    assert all(r.sweepable and not r.conflicts for r in res)


# ── 3. the schema refuses it, so the workbook round-trip cannot bring it back ─────────────────

@pytest.mark.parametrize("entry", ["True", "False", "true", "false", "TRUE", " True ", True, False])
def test_a_boolish_never_sweep_entry_is_refused_by_the_schema(entry):
    """All four spellings, in both types. JSON ``true`` would already fail as "Input should be a
    valid string", which tells the author nothing about why; the validator runs ``mode="before"``
    so every form arrives at the one message that names the alternatives."""
    with pytest.raises(ValidationError) as excinfo:
        OntologyMapping(canonical_key="sec__others", never_sweep=[entry])

    message = str(excinfo.value)
    assert "names nothing" in message
    assert "prohibition 4" in message               # points at the switch that DOES exist


def test_a_boolish_entry_is_refused_even_beside_legitimate_ones():
    """The check is per entry, not "does the list look plausible": ``["Total equity", "True"]`` is
    the shape a workbook produces when a real key and a ticked cell share the column."""
    with pytest.raises(ValidationError):
        OntologyMapping(canonical_key="sec__others",
                        never_sweep=["bs_equity__total_equity", "True"])


def test_the_refusal_reaches_the_upload_gate(raw: dict):
    """``load_ontology`` is what an uploaded rulebook goes through, so the guard has to bite there
    and not only on a directly constructed model. Fed the file as it shipped, it refuses."""
    regressed = copy.deepcopy(raw)
    regressed["mappings"][0]["never_sweep"] = ["True"]

    with pytest.raises(ValidationError, match="names nothing"):
        load_ontology(regressed)


def test_entries_that_name_something_still_validate():
    """The guard must not cost the field its two legitimate shapes — a canonical key and a
    sentence — including one that merely CONTAINS a boolean word."""
    ok = OntologyMapping(canonical_key="sec__others", never_sweep=[
        "bs_current_liabilities__total_current_liabilities",
        "any row printed in the equity or assets sections",
        "a caption whose note says the figure is true and fair",   # not a boolean, kept
    ])

    assert len(ok.never_sweep) == 3
    assert normalize_label(ok.never_sweep[1]).startswith("any row printed")
