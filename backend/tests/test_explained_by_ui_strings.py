"""Every screen string that presents an explained difference exists in every locale.

The explanation itself is a server sentence translated through `_TR` (tests/test_review_locales.py).
What the Workspace inspector and the review cards add around it — the heads, the section and
remainder labels, the two deep links, and the divergence sentence the explanation sits in — are
frontend keys, and `translate` falls back to English silently, so a key missing from one locale
block is a zh/ar/fr reader shown English with nothing failing; a translation that drops a
placeholder is a sentence with its figure missing. Read as text, the way
tests/test_screen_stage_labels.py reads its dictionary: brace matching rather than indentation,
because the braces are the structure.
"""
from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"

KEYS = {
    # The divergence sentence the explanation sits in, too: it was English in every locale, and its
    # queue clause first appears with the explanation (the line's status is now set).
    FRONTEND / "i18n.ts": ("ws.explained.head", "ws.explained.showRow", "ws.explained.sections",
                           "ws.explained.remainder", "ws.explained.mapInQueue",
                           "ws.divergence.printed", "ws.divergence.shown", "ws.divergence.queued"),
    FRONTEND / "i18n" / "screens" / "review.ts": (
        "r.explainedHead", "r.explainedSections", "r.explainedRemainder", "r.explainedMapIt",
        "r.explainsHead", "r.explainsPartial"),
}
LOCALES = ("en", "zh", "ar", "fr")


def _blocks(source: str) -> dict[str, str]:
    """Each locale's object literal, by brace matching."""
    out: dict[str, str] = {}
    for m in re.finditer(r"\n  ([a-z]{2}(?:-[A-Za-z]+)?): \{", source):
        depth, i = 1, m.end()
        while depth:
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
            i += 1
        out[m.group(1)] = source[m.end():i - 1]
    return out


@pytest.mark.parametrize("path", sorted(KEYS, key=str), ids=lambda p: p.name)
def test_every_new_ui_string_is_in_every_locale(path):
    if not path.exists():                       # pragma: no cover - a backend-only checkout
        pytest.skip(f"{path} is not present")
    blocks = _blocks(path.read_text(encoding="utf-8"))
    missing = []
    for locale in LOCALES:
        block = blocks.get(locale, "")
        for key in KEYS[path]:
            m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', block)
            if not m or not m.group(1).strip():
                missing.append(f"{locale}: {key}")
    assert not missing, missing


@pytest.mark.parametrize("path", sorted(KEYS, key=str), ids=lambda p: p.name)
def test_every_locale_fills_in_the_same_figures(path):
    # "The difference is {diff}" translated without its placeholder prints a sentence with no
    # figure in it, and `.replace` has nothing to complain about.
    if not path.exists():                       # pragma: no cover - a backend-only checkout
        pytest.skip(f"{path} is not present")
    blocks = _blocks(path.read_text(encoding="utf-8"))

    def placeholders(locale: str, key: str) -> set[str]:
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', blocks[locale])
        return set(re.findall(r"\{[a-z0-9_]+\}", m.group(1))) if m else set()

    wrong = [f"{locale}: {key}" for key in KEYS[path] for locale in LOCALES
             if placeholders(locale, key) != placeholders("en", key)]
    assert not wrong, wrong
