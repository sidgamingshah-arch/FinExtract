"""The Page Scope screen must have a word for every string it shows, in every locale, and name
the sections the server serves.

WHY THIS IS A BACKEND TEST. There is no frontend unit runner, and the two facts checked here cross
the language boundary: the screen's section ids are the server's `page_reading.SECTIONS`, and its
strings live in `frontend/src/i18n/screens/scope.ts`, where `translate` falls back to English and
then to the key — so a missing string renders as "sc.pick.move" with nothing failing. The idiom is
`test_screen_stage_labels.py`'s: read the source as text.
"""
from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
SCREENS = [FRONTEND / "screens" / "Scope.tsx", FRONTEND / "screens" / "ScopePages.tsx"]
I18N = FRONTEND / "i18n" / "screens" / "scope.ts"
LOCALES = ("en", "zh", "ar", "fr")


@pytest.fixture(scope="module")
def sources() -> dict[pathlib.Path, str]:
    if not all(p.exists() for p in [*SCREENS, I18N]):     # pragma: no cover - backend-only checkout
        pytest.skip("the frontend sources are not present")
    return {p: p.read_text(encoding="utf-8") for p in [*SCREENS, I18N]}


def _blocks(source: str) -> dict[str, dict[str, str]]:
    """Each locale's entries, by brace matching rather than by indentation."""
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"\n  ([a-z]{2}): \{", source):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(source[i], 0)
            i += 1
        body = source[m.end():i - 1]
        out[m.group(1)] = dict(re.findall(r'"(sc\.[^"]+)":\s*"((?:[^"\\]|\\.)*)"', body))
    return out


def _used_keys(sources) -> set[str]:
    keys: set[str] = set()
    for path in SCREENS:
        keys |= set(re.findall(r'\bt\("(sc\.[A-Za-z0-9_.]+)"\)', sources[path]))
        # Built keys: `sc.pick.${action}` and `sc.stmt.${id}`.
        if "`sc.pick.${action}`" in sources[path]:
            keys |= {"sc.pick.add", "sc.pick.move", "sc.pick.remove"}
    return keys | {f"sc.stmt.{s}" for s in ("balance_sheet", "profit_and_loss", "cash_flow",
                                             "changes_in_equity")}


def test_every_scope_string_the_screen_uses_exists_in_every_locale(sources):
    blocks = _blocks(sources[I18N])
    assert set(blocks) == set(LOCALES)
    used = _used_keys(sources)
    assert len(used) > 50, sorted(used)            # the screen was read, not an empty match
    placeholder = re.compile(r"\{(\w+)\}")
    for key in sorted(used):
        en = blocks["en"].get(key)
        assert en, f"{key} has no English string"
        for locale in LOCALES[1:]:
            value = blocks[locale].get(key)
            assert value, f"{key} has no {locale} string"
            assert set(placeholder.findall(value)) == set(placeholder.findall(en)), \
                f"{key} ({locale}) does not fill the same placeholders as English"


def test_the_screen_names_the_sections_the_server_serves(sources):
    from app.services.page_reading import SECTIONS

    m = re.search(r"export const SECTION_IDS[^=]*=\s*\[(.*?)\]", sources[SCREENS[1]], re.S)
    assert m, "ScopePages.tsx declares no SECTION_IDS"
    assert re.findall(r'"([a-z_]+)"', m.group(1)) == list(SECTIONS)
