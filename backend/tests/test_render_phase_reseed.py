"""A RENDER-PHASE RE-SEED MUST KEY ON AN IDENTIFIER, NEVER ON THE SERVED VALUE.

NEW FILE -> backend/tests/test_render_phase_reseed.py

WHAT THIS COSTS WHEN IT IS WRONG, measured on the running app. `RequestGroups` re-seeded its draft
from the server like this::

    const served = set.request_groups ?? [];
    const [seed, setSeed] = useState(served);
    if (seed !== served) { setSeed(served); setDraft(served); }

`!==` on an array is a REFERENCE comparison, and `?? []` mints a brand new array on every render
whenever the field is absent — which is every shipped configuration, because `request_groups` is
null rather than `[]`. So the condition was permanently true: setSeed -> render -> a new `[]` ->
setSeed, until React aborted with "Too many re-renders. React limits the number of renders to
prevent an infinite loop." That error is thrown from `renderWithHooks`, so it is not catchable by
the component itself; `ScreenErrorBoundary` caught it and the WHOLE LINE ITEMS SCREEN went blank on
the first keystroke in its search box.

`MasterPrompt` on the Line Items screen carried the identical two lines and survived — its `served`
is `?? ""`, a STRING, which compares by value. That is luck, not design: the pattern's safety
depended on the TYPE of a server field that nothing stops an author changing.

SO THE INVARIANT IS STRUCTURAL RATHER THAN A REVIEW HABIT: a re-seed that runs during render
compares an IDENTIFIER — the version id — which is a string, is stable across renders, and is
exactly what the pattern's own comment always said the trigger was ("re-seed when the server serves
a different version"). Comparing the served VALUE is forbidden here whatever its current type.

WHY A SOURCE TEST. There is no vitest setup in `frontend/`, and the Playwright suite needs both
servers and takes six minutes — it would catch this, and it would catch it long after the commit.
This is the same idiom `test_origin_contract` and `test_review_queue_scope` use: read the source,
assert the property, fail on the commit that breaks it.
"""
from __future__ import annotations

import pathlib
import re

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"

# `if (<a> !== <b>) { … set…(` at the COMPONENT BODY'S OWN TOP LEVEL — the render-phase re-seed this
# file is about. Written to match the SHAPE rather than a specific variable name, so a copy of the
# pattern under other names is still found.
#
# THREE THINGS THIS HAS TO GET RIGHT, and the first two were once the same line.
#
#  * The BODY MAY SPAN LINES. It used to have to be a one-liner (`\{\s*set\w+\(`), which was true
#    of both sites until the re-seed learned to protect a dirty draft: it now reads the cleanliness
#    of the box before deciding whether to overwrite it, so the setter is no longer the first
#    statement in the block. A guard that only matches a one-liner silently stops guarding the
#    moment the code it guards grows a second line — and both tests below then pass on nothing,
#    which is why `test_the_pattern_is_still_used_somewhere` exists.
#  * The LEFT OPERAND MAY BE DOTTED. The cell carries the served value alongside the id now
#    (`seed.id`), because "unchanged from what was served" is a question only the previous served
#    value can answer. `\w+` does not match that.
#  * IT IS THE RENDER PHASE OR IT IS NOTHING. The hazard is setting state DURING RENDER, which
#    re-renders immediately; the same comparison inside a `useEffect` with a dependency array is
#    not this bug. Anchored on two-space indentation, which is the component body's own level in
#    this codebase — measured, dropping the anchor pulls in three `useEffect` re-seeds
#    (`Template.tsx` ×2, `Workspace.tsx` ×1) that compare freshly-built strings perfectly safely
#    and would read as offenders against the allow-list below.
RESEED = re.compile(
    r"^  if\s*\(\s*(?P<left>[\w.]+)\s*!==\s*(?P<right>[\w.]+)\s*\)\s*\{"
    r"(?:[^{}]|\{[^{}]*\})*?set\w+\(", re.M | re.S)


def _sources() -> list[pathlib.Path]:
    return sorted(p for p in FRONTEND.rglob("*.tsx") if p.is_file())


def test_the_pattern_is_still_used_somewhere():
    """If this finds nothing the test below cannot fail, and a guard that cannot fail is worse than
    no guard — it reads as a property being enforced."""
    found = [p for p in _sources() if RESEED.search(p.read_text(encoding="utf-8"))]
    assert found, "no render-phase re-seed found at all; has the pattern been renamed?"


def test_every_render_phase_reseed_compares_an_identifier():
    """THE INVARIANT. The compared right-hand side must be an id, not the served value.

    Accepted names are spelled out rather than pattern-matched on "id", so adding a new one is a
    deliberate act with this docstring in front of it.
    """
    allowed = {"versionId", "id", "runId", "documentId", "setId", "key"}
    offenders: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for m in RESEED.finditer(text):
            right = m.group("right")
            if right in allowed:
                continue
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(FRONTEND).as_posix()}:{line} compares {right!r}")

    assert not offenders, (
        "a render-phase re-seed compares the SERVED VALUE rather than an identifier. If that value "
        "is ever an object or array — `?? []` builds a fresh one every render — the comparison is "
        "permanently true and the component re-renders until React aborts, blanking the screen. "
        "Key it on the version id instead:\n  " + "\n  ".join(offenders))


def test_the_two_known_sites_are_both_keyed_on_the_version():
    """Named, so a regression in either is reported as itself rather than as a generic offender."""
    for name in ("components/RequestGroups.tsx", "screens/LineItems.tsx"):
        text = (FRONTEND / name).read_text(encoding="utf-8")
        assert "if (seed.id !== versionId) {" in text, (
            f"{name} no longer re-seeds on the version id")


def test_neither_site_re_seeds_over_a_draft_somebody_is_typing():
    """THE OTHER HALF, and it is a data-loss bug rather than a crash.

    `versionId` advances on every publish from the Line Items screen — the author's own line-item
    save included, since all four mutations invalidate ``["line-items"]`` and the mutation awaits
    the refetch. So an unconditional re-seed is a no-op whenever the box is clean, and its only
    observable effect is to discard a prompt or a grouping somebody was in the middle of writing,
    with no message. Saving a line item wiped the master prompt box.

    Asserted as the shape of the decision rather than its outcome, for the reason the module
    docstring gives: there is no vitest in `frontend/`, and the Playwright suite needs both servers.
    """
    for name, setter in (("components/RequestGroups.tsx", "setDraft"),
                         ("screens/LineItems.tsx", "setText")):
        text = (FRONTEND / name).read_text(encoding="utf-8")
        assert f"if (clean) {setter}(served);" in text, (
            f"{name} re-seeds unconditionally, which discards whatever was being typed")
