"""The extraction screen must have a word for every stage the pipeline runs, in every locale.

NEW FILE -> backend/tests/test_screen_stage_labels.py

WHY THIS EXISTS. `docs/architecture/01-extraction-pipeline.md` carries a prose copy of the stage
list and `tests/test_docs_match_the_pipeline.py` holds it to `default_pipeline()`. The SCREEN
carries a third copy — `frontend/src/i18n/screens/extraction.ts`, keyed by the pipeline's own stage
identifier — and nothing checked it. So it drifted, exactly the way the doc had drifted before it
got a guard: SEVEN of twenty-one stages had no label at all.

WHAT THE READER SAW. `screens/ExtractionView.tsx::stageLabel` falls back to the raw identifier when
no locale holds a string, which is the right fallback and also why the drift was invisible in
testing — nothing crashed, nothing was blank. A run simply reported its progress as
`note_sourced`, `face_mapping_contract`, `assemble_components`. Six of the seven had been like that
for as long as those stages existed; `line_item_llm` was added this week.

SO THE FALLBACK IS NOT THE PROBLEM AND IS NOT BEING TESTED AWAY. It is what stops a new stage
rendering as an empty row, and it should stay. This asserts the labels are COMPLETE so the fallback
stays what it is — a safety net rather than the normal case.

CROSSING THE LANGUAGE BOUNDARY ON PURPOSE, for the reason the doc test gives: the stage list lives
in `default_pipeline()` and every other copy of it is a copy. A TypeScript test could check the
dict's shape but not that it matches the pipeline, because the pipeline is Python. So the check
belongs here, reading the file as text — the same thing `test_docs_match_the_pipeline.py` does with
a markdown file.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.core.pipeline import default_pipeline

I18N = (pathlib.Path(__file__).resolve().parent.parent.parent
        / "frontend" / "src" / "i18n" / "screens" / "extraction.ts")

KEY = re.compile(r'"ex\.stage\.([a-z_]+)"\s*:\s*"([^"]*)"')


@pytest.fixture(scope="module")
def source() -> str:
    if not I18N.exists():                       # pragma: no cover - a backend-only checkout
        pytest.skip(f"{I18N} is not present")
    return I18N.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stages() -> list[str]:
    return [stage.name for stage in default_pipeline().stages]


def _blocks(source: str) -> dict[str, str]:
    """Each locale's object literal, by brace matching rather than by indentation.

    Indentation would work today and break the first time the file is reformatted; the braces are
    the structure.
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"\n  ([a-z]{2}(?:-[A-Za-z]+)?): \{", source):
        locale = m.group(1)
        depth, i = 1, m.end()
        while depth:
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
            i += 1
        out[locale] = source[m.end():i - 1]
    return out


def test_the_file_declares_the_locales_it_is_typed_for(source):
    """`Record<Locale, …>` is a type, and a type does not fail a run — a missing locale block would
    be a compile error in the frontend and nothing here. Named so the count below means something."""
    found = _blocks(source)
    assert len(found) >= 2, found.keys()
    assert "en" in found


def test_every_pipeline_stage_has_a_label_in_every_locale(source, stages):
    """THE CHECK THIS FILE EXISTS FOR. A stage with no label reports as its own identifier, which is
    a safe fallback and a poor progress row."""
    missing: dict[str, list[str]] = {}
    for locale, block in _blocks(source).items():
        have = {m.group(1) for m in KEY.finditer(block)}
        gap = [s for s in stages if s not in have]
        if gap:
            missing[locale] = gap
    assert not missing, (
        f"stages with no label on the extraction screen: {missing}. Add "
        f'"ex.stage.<name>" to frontend/src/i18n/screens/extraction.ts — the pipeline owns the '
        f"list (app/core/pipeline.py::default_pipeline), this is a copy of it.")


def test_no_label_names_a_stage_the_pipeline_dropped(source, stages):
    """The other direction. A label for a retired stage is a row a reader will never see and a
    translator will keep maintaining."""
    known = set(stages)
    stale: dict[str, list[str]] = {}
    for locale, block in _blocks(source).items():
        extra = sorted({m.group(1) for m in KEY.finditer(block)} - known)
        if extra:
            stale[locale] = extra
    assert not stale, f"labels for stages the pipeline no longer runs: {stale}"


def test_no_label_is_empty_or_still_the_identifier(source):
    """An empty string is worse than the fallback — it renders a blank row where the identifier at
    least says which stage is running. A label equal to its own key is a placeholder someone meant
    to come back to."""
    bad: list[tuple[str, str, str]] = []
    for locale, block in _blocks(source).items():
        for m in KEY.finditer(block):
            name, label = m.group(1), m.group(2)
            if not label.strip() or label.strip() == name:
                bad.append((locale, name, label))
    assert not bad, bad


def test_the_english_labels_say_what_the_stage_does_not_how(source):
    """A progress row is read by whoever uploaded the filing, so it names the WORK and not the
    mechanism. `llm`, `regex` and `cascade` are implementation words that have leaked into
    user-facing strings before.

    Deliberately English-only: the other locales are translations of these, and asserting a word
    list against Arabic or Chinese would be asserting the translator's choices.
    """
    block = _blocks(source)["en"]
    leaked = [(m.group(1), m.group(2)) for m in KEY.finditer(block)
              if re.search(r"\b(llm|regex|cascade|rollup|ontology|schema)\b",
                           m.group(2), re.IGNORECASE)]
    assert not leaked, (
        f"implementation words in a progress label: {leaked}. The reader wants to know what is "
        f"happening to their filing.")
