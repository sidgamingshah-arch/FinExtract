"""``expected_components`` on the shipped output-CSV rulebook: the authored prose, checked.

WHAT THIS FIELD IS. Each LOCKED residual bucket names the kinds of caption its section is expected
to have no dedicated concept for — "Bank overdrafts", "Contract assets", "Club memberships". A row
matching one of those is supposed to reach NO concept and be swept into the section remainder,
itemised under its own label, rather than being filed on the nearest specific line (which puts the
figure on a wrong line while the section still ties — the one error nothing downstream detects).

ITS CONSUMER IS RETIRED, AND THE TESTS THAT MEASURED IT ARE GONE WITH IT.
``services.mapping._residual_expectations`` published the list as a batch payload field
("captions_with_no_dedicated_concept") that licensed the model to answer with an EMPTY
canonical_key. There is no such answer to license: nothing asks a model which concept a printed
row is, so a row the deterministic tiers cannot place is already unmapped and the sweep already
sees it — which is the outcome the prose existed to obtain.

WHY THE FIELD AND THESE TESTS STAY. The wording is authored VOCABULARY about what each section
legitimately leaves unexplained, and it is worth keeping correct: it documents the sweep's own
boundary for whoever reads the configuration, and it is the content any future reader would want.
What the tests below hold is its HYGIENE — no entry is key-shaped, every bucket carries prose, each
list stays narrow, and no phrase is another concept's caption. That last one is not shared between
the two shipped rulebooks and must not be copied across: hkfrs names "Bank overdrafts", which here
is ``bs_cl__overdrafts``.

WHICH FILE GOVERNS. Line items is the single configuration engine, so what a run reads comes from
the ``line_item_versions`` row seeded from ``output_csv_hk_line_items.json``. The rulebook JSON
read below is the GENERATOR INPUT that set is projected from (``scripts/build_line_items.py``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_ontology
from app.services.mapping import OntologyMatcher, normalize_label

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"
LINE_ITEMS = TEMPLATES / "output_csv_hk_line_items.json"

# The three statements a filing is spread against. `changes_in_equity` is deliberately absent: the
# rulebook covers it with no concepts at all, and the batch path abandons a chunk with an empty
# candidate list before the payload is assembled.
STATEMENTS = ("balance_sheet", "profit_and_loss", "cash_flow")


@pytest.fixture(scope="module")
def ontology():
    return load_ontology(json.loads(ONTOLOGY.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(ontology):
    return OntologyMatcher(ontology, locale="en", settings=get_settings())


@pytest.fixture(scope="module")
def residuals(ontology):
    return [m for m in ontology.mappings if m.value_scope == "exclusive_residual"]


def test_no_entry_is_key_shaped(residuals):
    """A `__` or a `|` here is a canonical_key, or a pipe-joined list of them, in a field the
    consumer publishes as caption prose. That is how the field got filled by mistake once already:
    ``build_output_csv_template.py`` harvested a pipe-delimited key column and shipped one joined
    string on 31 concepts (all of them gross parents, none of them locked, so nothing read it)."""
    for m in residuals:
        for phrase in m.expected_components:
            assert "__" not in phrase, f"{m.canonical_key}: {phrase!r} is a canonical_key"
            assert "|" not in phrase, f"{m.canonical_key}: {phrase!r} is a joined key list"


# ── and every bucket carries some, narrowly ──────────────────────────────────────────────────────

def test_all_eleven_buckets_carry_prose(residuals):
    assert len(residuals) == 11
    naked = [m.canonical_key for m in residuals if not m.expected_components]

    assert naked == [], f"these buckets license no empty answer: {naked}"


def test_each_list_stays_narrow(residuals):
    """3 to 8. An over-broad list is the mirror-image error and just as silent: a caption that DOES
    have a dedicated concept gets routed to the sweep, and the section still ties."""
    for m in residuals:
        assert 3 <= len(m.expected_components) <= 8, (
            f"{m.canonical_key} carries {len(m.expected_components)} phrases")


def test_no_phrase_is_another_concepts_caption(matcher, ontology, residuals):
    """The check that keeps the list honest, and the reason hkfrs's wording was not copied.

    A phrase is compared against the label and every locale's aliases of the concepts sharing the
    bucket's section, under the rulebook's own normalisation. Same-section only, because the sweep
    is section-bound and there is no cross-section rescue: "Proposed final dividend" in the equity
    section cannot reach ``bs_cl__proposed_dividends`` however the model answers.

    Equality, not containment: 'Retained Profits' is an alias of all six ``is_retained`` concepts
    and 'Other Comprehensive Income' of the OCI subtotal, so a containment rule reports those
    pre-existing over-broad aliases rather than anything about these phrases.
    """
    for m in residuals:
        sections = matcher._sections_of(m.canonical_key)
        captions: dict[str, str] = {}
        for other in ontology.mappings:
            if other.canonical_key == m.canonical_key:
                continue
            if not (matcher._sections_of(other.canonical_key) & sections):
                continue
            names = [other.label, *other.aliases,
                     *(a for locale in other.aliases_i18n.values() for a in locale)]
            for name in names:
                if name:
                    captions.setdefault(normalize_label(name), other.canonical_key)

        for phrase in m.expected_components:
            owner = captions.get(normalize_label(phrase))
            assert owner is None, (
                f"{m.canonical_key} expects {phrase!r}, but {owner} already claims that caption in "
                f"{sorted(sections)} — the sweep would take a row that has a concept")


# ── and the CONFIGURATION a run actually reads carries it ────────────────────────────────────────

def test_the_configuration_licenses_the_empty_answer_on_every_bucket():
    """THE FILE THAT GOVERNS. Every residual bucket in the line-item set must carry its prose.

    THIS TEST USED TO BE ``test_the_line_items_file_mirrors_the_ontology``, and its premise was that
    both files were shipped and ``extraction.mapping_engine`` decided which one answered — so the
    two had to agree or the product had two behaviours. That switch is deleted. Line items is the
    single configuration engine: a run maps against a ``line_item_versions`` row seeded from
    ``output_csv_hk_line_items.json``, and the rulebook JSON is the generator input that set is
    projected from (``scripts/build_line_items.py``), not a second engine to keep in step with.

    So the assertion is no longer a mirror check, it is the real one, made against the set itself
    and repeating every property the rulebook half is held to above: eleven ``exclusive_residual``
    definitions, each with 3 to 8 phrases, none of them key-shaped. A mirror check would have passed
    on a set whose prose was as empty as the rulebook's once was — which is the shipped state this
    whole module exists to have caught.
    """
    st = load_line_item_set(json.loads(LINE_ITEMS.read_text(encoding="utf-8")))
    buckets = [d for d in st.items if d.value_scope == "exclusive_residual"]

    assert len(buckets) == 11
    naked = [d.key for d in buckets if not d.expected_components]
    assert naked == [], f"these buckets license no empty answer: {naked}"
    for d in buckets:
        assert 3 <= len(d.expected_components) <= 8, (
            f"{d.key} carries {len(d.expected_components)} phrases")
        for phrase in d.expected_components:
            assert "__" not in phrase, f"{d.key}: {phrase!r} is a canonical_key"
            assert "|" not in phrase, f"{d.key}: {phrase!r} is a joined key list"
