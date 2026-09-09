"""Supported-language registry — enforces input = output multilingual parity.

A language counts as ``supported`` only when ALL five parity artifacts exist:
OCR pack, locale number-format rules, LINE-ITEM aliases for the active template — on an
item the matcher actually indexes, see ``evaluate_parity`` —
template ``label_i18n``, and a UI translation bundle. The API exposes the supported
set and the UI offers only those languages — so the input set and output set are, by
construction, identical.

The seed set is English, Chinese, Arabic (RTL), and French.

The alias half used to be measured over a stored ontology. There is one configuration engine now:
what arrives here is the matcher's WORKING VIEW of a ``LineItemSet`` (``services.working_view``),
which is an ``OntologyDefinition`` only as an in-memory shape — it is never stored, never served
and never selectable. The parameter keeps its internal name for that reason; the SERVED name does
not (``line_item_aliases``).
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.ontology import OntologyDefinition
from app.schemas.template import TemplateDefinition


class LanguageParity(BaseModel):
    locale: str
    name: str
    rtl: bool = False
    has_ocr_pack: bool = False
    has_number_format: bool = False
    has_line_item_aliases: bool = False
    has_template_labels: bool = False
    has_ui_bundle: bool = False

    @property
    def supported(self) -> bool:
        return all([
            self.has_ocr_pack,
            self.has_number_format,
            self.has_line_item_aliases,
            self.has_template_labels,
            self.has_ui_bundle,
        ])

    @property
    def missing(self) -> list[str]:
        checks = {
            "ocr_pack": self.has_ocr_pack,
            "number_format": self.has_number_format,
            "line_item_aliases": self.has_line_item_aliases,
            "template_labels": self.has_template_labels,
            "ui_bundle": self.has_ui_bundle,
        }
        return [name for name, ok in checks.items() if not ok]


# Seed set confirmed with the user. ``rtl`` and OCR-pack availability are static
# facts about the language/engine; the other three flags are computed per
# template + line-item set by ``evaluate_parity`` below.
SEED_LANGUAGES: dict[str, dict] = {
    "en": {"name": "English", "rtl": False, "has_ocr_pack": True},
    "zh": {"name": "Chinese", "rtl": False, "has_ocr_pack": True},
    "ar": {"name": "Arabic", "rtl": True, "has_ocr_pack": True},
    "fr": {"name": "French", "rtl": False, "has_ocr_pack": True},
}

# UI bundles present in the frontend. Backend tracks the fact for the parity gate.
UI_BUNDLES: set[str] = {"en", "zh", "ar", "fr"}


def evaluate_parity(
    template: TemplateDefinition | None,
    ontology: OntologyDefinition | None,
    locales: list[str] | None = None,
) -> list[LanguageParity]:
    """Compute parity for each seed (or requested) locale against the given
    template + configuration. With no template/configuration, only the static facts are known.

    ``ontology`` is the matcher's working view of the line-item set in force — built by
    ``services.working_view.build_working_view``, never loaded from a store. The name is internal
    and kept so the matcher-side vocabulary reads the same on both sides of the adapter.

    ``locales`` may only NARROW ``SEED_LANGUAGES`` — it is intersected with it, never
    substituted for it, so this registry stays code-authoritative. The one caller
    (``api/routes/languages.py``) passes ``features.supported_locales`` from config.toml, a
    free-form list of strings with no validator in front of it; a deployment can take a
    locale off the menu but can never put one on it.
    """
    # WHY AN INTERSECTION AND NOT A REPLACEMENT. ``seed`` below falls back to
    # ``{"name": loc, "rtl": False, "has_ocr_pack": False}`` for an unknown key, so a config
    # typo passed straight through would be REPORTED as a real language that merely lacks an
    # OCR pack — ``has_ui_bundle`` would be the only thing keeping "de" out of the picker, and
    # it is a fact about the frontend, not a decision this function should lean on. Ordered by
    # SEED_LANGUAGES rather than by the argument so the response order cannot depend on how a
    # config file happened to be typed.
    #
    # An intersection that comes out EMPTY (absent list, or a list naming only unknown locales)
    # is treated as "no narrowing asked for", NOT as "no language is supported": an empty answer
    # WIDENS in practice, because the switcher falls back to its own hardcoded four-locale list
    # when ``fully_supported`` is empty (frontend/src/components/shell/LanguageSwitcher.tsx:10,19).
    requested = set(locales or ())
    locales = [loc for loc in SEED_LANGUAGES if loc in requested] or list(SEED_LANGUAGES)
    # The line items the MATCHER actually indexes. A locked residual (``alias_matching:
    # disabled``) and a computed item (``extraction_mode: derive``) are excluded from every
    # alias tier and from the LLM candidate payload, so an alias on one of them is read by
    # nothing and cannot make a locale extractable. Mirrors the ``_unmatchable`` set in
    # ``services.mapping.OntologyMatcher`` — restated rather than imported, because a schema
    # module must not depend on a service.
    matchable = [m for m in (ontology.mappings if ontology else ())
                 if m.alias_matching != "disabled" and m.extraction_mode != "derive"]
    result: list[LanguageParity] = []
    for loc in locales:
        seed = SEED_LANGUAGES.get(loc, {"name": loc, "rtl": False, "has_ocr_pack": False})

        has_number_format = bool(ontology and loc in ontology.number_format_by_locale)
        # WHAT WENT WRONG. This was ``any(loc in m.aliases_i18n or loc == "en" for m in
        # ontology.mappings)`` — a bound that ADMITS rather than refuses, on two counts. It ran
        # over EVERY item, so an alias on an item no tier indexes still asserted the locale;
        # and it tested for the KEY, so a locale key mapped to an empty list asserted it too.
        # Measured through the working view of the shipped ``output_csv_hk_line_items.json``
        # (475 definitions -> 462 indexable items, 445 matchable): 12 of its 17 unmatchable items
        # carry a ``zh`` alias, and 2 matchable items carry an EMPTY ``zh`` list — while 287 of the
        # 445 matchable items carry no ``zh`` alias at all, which is what the flag is meant to be
        # about. So the locale is now asserted only from a NON-EMPTY alias list on an item the
        # matcher indexes.
        # ``or loc == "en"`` is kept for "en" ALONE: ``aliases_for`` folds the plain, locale-neutral
        # ``aliases`` list in for every locale and those are English by construction, so English is
        # covered even with no ``aliases_i18n["en"]`` (measured: 66 of the 445 have no ``en`` key).
        # That reasoning does not transfer to any other locale, which is precisely why the clause
        # was wrong as written.
        has_line_item_aliases = bool(matchable) and (
            loc == "en" or any(m.aliases_i18n.get(loc) for m in matchable)
        )
        has_template_labels = bool(
            template and all(loc in n.label_i18n or loc == "en"
                             for n in template.all_nodes())
        )
        result.append(LanguageParity(
            locale=loc,
            name=seed["name"],
            rtl=seed["rtl"],
            has_ocr_pack=seed["has_ocr_pack"],
            has_number_format=has_number_format,
            has_line_item_aliases=has_line_item_aliases,
            has_template_labels=has_template_labels,
            has_ui_bundle=loc in UI_BUNDLES,
        ))
    return result
