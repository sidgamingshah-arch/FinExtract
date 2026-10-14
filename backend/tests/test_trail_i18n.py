"""Every word the "How it adds up" box and the Line Items sheet show exists in every locale.

NEW FILE -> backend/tests/test_trail_i18n.py

The box's sentences are built in the browser from `frontend/src/i18n/screens/trail.ts`, the sheet's
from `services.derivation._WORDS`, and the inspector's note on a derived figure from
`api/routes/documents._TR`. A key missing from a locale does not fail anywhere — the reader is
shown English, or the bare key — so completeness is asserted here, reading the TypeScript as text
the way `test_screen_stage_labels` does.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tests.test_screen_stage_labels import _blocks

FRONTEND = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
TRAIL = FRONTEND / "i18n" / "screens" / "trail.ts"
SCREENS = [FRONTEND / "components" / "TrailWorking.tsx", FRONTEND / "screens" / "Workspace.tsx"]
LOCALES = ("zh", "ar", "fr")
ENTRY = re.compile(r'"(trail\.[^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')
PLACEHOLDER = re.compile(r"\{[a-z]+\}")


@pytest.fixture(scope="module")
def dicts() -> dict[str, dict[str, str]]:
    if not TRAIL.exists():                      # pragma: no cover - a backend-only checkout
        pytest.skip(f"{TRAIL} is not present")
    return {locale: dict(ENTRY.findall(block))
            for locale, block in _blocks(TRAIL.read_text(encoding="utf-8")).items()}


def test_every_trail_key_is_in_every_locale(dicts):
    assert set(dicts) == {"en", *LOCALES}, sorted(dicts)
    en = dicts["en"]
    assert len(en) > 40, sorted(en)
    for locale in LOCALES:
        assert set(dicts[locale]) == set(en), (
            locale, sorted(set(en) ^ set(dicts[locale])))
        for key, text in dicts[locale].items():
            assert text.strip(), f"{locale} {key} is empty"
            # The figures are filled in by placeholder, so a translation that drops one loses a
            # number from the sentence.
            assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(en[key])), (
                locale, key, text)


def test_every_key_the_screen_uses_exists(dicts):
    en = dicts["en"]
    used: set[str] = set()
    prefixes: set[str] = set()
    for path in SCREENS:
        for literal in re.findall(r'"(trail\.[A-Za-z_.]+)"', path.read_text(encoding="utf-8")):
            (prefixes if literal.endswith(".") else used).add(literal)
    assert used, "the screens use no trail key at all"
    assert not sorted(used - set(en)), sorted(used - set(en))
    # A key built from a status, an operator or a reason code: every value it can take is there.
    expansions = {"trail.status.": ("same_row", "not_used", "missing"),
                  "trail.op.": ("max", "min", "first"),
                  "trail.reason.": ("not_selected", "alternative", "corroborates", "no_figure",
                                    "not_needed", "not_located", "refused")}
    for prefix in prefixes:
        assert prefix in expansions, f"{prefix} is built from a value this test does not know"
        for value in expansions[prefix]:
            assert prefix + value in en, prefix + value


def test_the_served_note_and_the_export_phrases_are_translated():
    from app.api.routes.documents import _TR
    from app.services.derivation import _WORDS

    note = ("Assembled by the {method} rule from the inputs below; each keeps its own figure and "
            "page — click one to jump to it in the document.")
    assert set(_TR[note]) >= set(LOCALES)
    assert all("{method}" in _TR[note][locale] for locale in LOCALES)
    for phrase, translations in _WORDS.items():
        assert set(translations) >= set(LOCALES), phrase
        for locale in LOCALES:
            assert translations[locale].strip(), (phrase, locale)
            assert set(PLACEHOLDER.findall(translations[locale])) == set(
                PLACEHOLDER.findall(phrase)), (phrase, locale)
