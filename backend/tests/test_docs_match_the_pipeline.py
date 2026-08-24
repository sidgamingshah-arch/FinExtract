"""The architecture note's stage list is held to the pipeline it describes.

WHY THIS EXISTS. ``docs/architecture/01-extraction-pipeline.md`` states, correctly, that
``default_pipeline()`` is the only place the stage order lives and that no third copy should be
made. But the doc itself carries a fourth thing — a PROSE copy of that list, and the count in its
own heading — and nothing checked it. A stage was added to the pipeline and the doc went on saying
"Fourteen stages" and listing fourteen, which is the failure mode the doc warns about, arrived at
through the door the doc does not guard.

A prose list cannot be generated from the code without making the document unreadable, so it is
checked instead: the names, their order, and the count. What each stage DOES is still prose a human
writes and a human reviews — this only stops the inventory going quietly out of date.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.core.pipeline import default_pipeline

DOC = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "docs/architecture/01-extraction-pipeline.md")

# The words the heading may use, so "Fifteen stages" is checked rather than trusted.
_NUMBER_WORDS = {
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
}


@pytest.fixture(scope="module")
def doc() -> str:
    if not DOC.exists():                     # the docs tree is not shipped in every checkout
        pytest.skip(f"{DOC} not present")
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stages() -> list[str]:
    return [s.name for s in default_pipeline().stages]


def _prose_order(doc: str) -> list[str]:
    """The dot-separated stage list the document prints, however it is line-wrapped."""
    m = re.search(r"`(ingest\s*·[^`]+)`", doc)
    assert m, "the document no longer prints a `·`-separated stage list"
    return [part.strip() for part in re.split(r"·", m.group(1)) if part.strip()]


def test_the_documents_stage_list_is_the_pipelines_own_order(doc, stages):
    """Names AND order. A reordering matters as much as an omission: the document explains why each
    stage sits where it does — gap closing before the checks, segment last — and a list in the wrong
    order makes those explanations describe a pipeline nobody runs."""
    assert _prose_order(doc) == stages


def test_every_stage_has_its_own_numbered_entry(doc, stages):
    """The list at the top is an index; each stage also has to be described. A stage named in the
    index and explained nowhere is how a reader concludes it does nothing."""
    numbered = re.findall(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*\s*\(([^)]*)\)", doc, re.MULTILINE)
    described = " ".join(f"{name} {where}" for _n, name, where in numbered)
    missing = [s for s in stages
               if s not in described
               and f"stages/{'language' if s == 'language_detect' else s}.py" not in described]
    assert missing == [], f"stages with no numbered entry: {missing}"


def test_the_headline_count_matches_the_pipeline(doc, stages):
    """The number a reader takes away at a glance, and the one that was wrong."""
    word = _NUMBER_WORDS.get(len(stages))
    assert word, f"add {len(stages)} to _NUMBER_WORDS"
    head = doc[:doc.index("1. **")]
    assert re.search(rf"\b{word}\b", head, re.IGNORECASE), (
        f"the document does not say it has {word} stages; the pipeline has {len(stages)}")


def test_the_document_does_not_name_a_stage_the_pipeline_dropped(doc, stages):
    """The other direction. A retired stage left in the list reads as a step that still runs — and
    this document already carries a section about one that was described and never built."""
    listed = _prose_order(doc)
    assert [s for s in listed if s not in stages] == []
