"""Every `origin` the server can put on a statement row is one the front end can render.

WHY THIS TEST EXISTS. `origin` says where a displayed figure came from, and the Workspace grid
turns it into a chip through `ORIGIN_CHIP`, a `Record<Origin, …>` — a lookup TypeScript believes is
total. It is total over the union in `types.ts`, which is a claim about this server, and the claim
went stale: `derived` was added here (c67e723, the depreciation assembly) and never added there.

What that cost was not a missing chip. `ORIGIN_CHIP[origin].help` returned `undefined.help` at
runtime, threw inside render, and — with no error boundary in the app at the time — React unmounted
the whole tree. Clicking the P&L tab blanked the page on 6 of the 8 documents in the workspace:
exactly the 6 whose P&L carried a derived row, which is also why it looked like a P&L bug rather
than a contract bug. `derived` is produced only where a SERVICE assembled a figure from note lines,
and those services land on the income statement.

Two independent guards now exist and this is the outer one. The front end no longer trusts the
total type (`originChip` falls back to no chip, and a boundary catches anything else), so the next
missing value degrades instead of blanking — but degrading silently is still the wrong answer for a
figure whose provenance the reader is entitled to see. This test is what makes the omission LOUD,
at the commit that introduces it, on the side that introduced it.

DELIBERATELY A TEXT TEST over `types.ts`. Parsing TypeScript properly would need a toolchain the
backend suite does not have, and the thing being checked is a hand-maintained list of string
literals — the failure mode is a forgotten entry, which a regex sees perfectly well. It skips
rather than fails where the front end is absent, because a backend-only deployment is legitimate
and this is a repo-shape assertion, not a runtime one.
"""
from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
TYPES_TS = FRONTEND / "types.ts"
WORKSPACE_TSX = FRONTEND / "screens" / "Workspace.tsx"

# `extracted` is the ordinary case and the only origin `_CALC_NOTES` does not describe: there is no
# note to write about a figure that was simply read off the page. Everything else the server can
# emit is a key of that dict — see `_calculated_note`, which indexes it with the origin directly
# and would KeyError on anything missing, making it the authoritative list by construction.
ORDINARY = "extracted"


def _backend_origins() -> set[str]:
    from app.api.routes.documents import _CALC_NOTES

    return {ORDINARY} | set(_CALC_NOTES)


def _union_from_types_ts() -> set[str]:
    """The string literals of `StatementRow.origin` in the front end's own type."""
    src = TYPES_TS.read_text(encoding="utf-8")
    m = re.search(r"^\s*origin\?\s*:\s*([^;]+);", src, re.M)
    assert m, "could not find the `origin?:` field in frontend/src/types.ts"
    return set(re.findall(r'"([a-z_]+)"', m.group(1)))


def _origin_chip_keys() -> set[str]:
    """The keys of `ORIGIN_CHIP`, the table the grid actually indexes."""
    src = WORKSPACE_TSX.read_text(encoding="utf-8")
    m = re.search(r"const ORIGIN_CHIP[^=]*=\s*\{(.*?)\n\};", src, re.S)
    assert m, "could not find ORIGIN_CHIP in frontend/src/screens/Workspace.tsx"
    # Top-level keys only: a nested `help:`/`label:` is indented further than the entries.
    return set(re.findall(r"^  ([a-z_]+)\s*:", m.group(1), re.M))


pytestmark = pytest.mark.skipif(
    not TYPES_TS.exists() or not WORKSPACE_TSX.exists(),
    reason="front end not present in this checkout",
)


def test_the_front_end_type_lists_every_origin_the_server_sends():
    """A union that under-states what the API returns is not a narrower type, it is a wrong one."""
    backend, frontend = _backend_origins(), _union_from_types_ts()

    missing = backend - frontend
    assert not missing, (
        f"frontend/src/types.ts StatementRow.origin is missing {sorted(missing)}. "
        "The server emits these (see `_CALC_NOTES` in api/routes/documents.py); add them to the "
        "union AND to ORIGIN_CHIP in screens/Workspace.tsx."
    )


def test_the_front_end_declares_no_origin_the_server_cannot_send():
    """The other direction, which is a dead branch rather than a crash — but it means the chip
    table describes a state the reader can never be in, and the next person maintaining either
    side has to work out which of the two is lying."""
    backend, frontend = _backend_origins(), _union_from_types_ts()

    extra = frontend - backend
    assert not extra, (
        f"frontend/src/types.ts declares origins the server never emits: {sorted(extra)}. "
        "Either the server stopped sending them or they were never real."
    )


def test_every_declared_origin_has_a_chip():
    """THE LOOKUP THAT ACTUALLY THREW. The union and the table are maintained by hand in two
    files, and it is the table the grid indexes — a value present in the union and absent here is
    precisely the shape of the original defect."""
    assert _origin_chip_keys() == _union_from_types_ts(), (
        f"ORIGIN_CHIP keys {sorted(_origin_chip_keys())} do not match the "
        f"StatementRow.origin union {sorted(_union_from_types_ts())}"
    )


def test_the_chip_lookup_is_guarded_against_an_unknown_origin():
    """Belt as well as braces. The three tests above keep the lists in step; this one keeps the
    FALLBACK, so that a future mismatch costs the reader a chip and not the screen. Asserted on
    the helper's existence and on the two call sites going through it, because the crash came from
    indexing `ORIGIN_CHIP` directly and either site would have been enough to blank the page."""
    src = WORKSPACE_TSX.read_text(encoding="utf-8")

    assert re.search(r"function originChip\(", src), (
        "the guarded accessor `originChip` is gone from Workspace.tsx — without it a missing "
        "ORIGIN_CHIP entry unmounts the app again"
    )
    # Every read of the table other than its own definition and the accessor must be the accessor.
    # COMMENTS ARE EXCLUDED, and not as a convenience: the accessor's own docstring quotes the
    # expression that threw, which is the clearest thing to write there and must not read as a
    # violation. `as Record<string` excludes the accessor's cast, where the remaining
    # `typeof ORIGIN_CHIP[Origin]` is a TYPE index and cannot throw.
    def is_comment(line: str) -> bool:
        return line.lstrip().startswith(("*", "//", "/*"))

    direct = [
        line for line in src.splitlines()
        if re.search(r"ORIGIN_CHIP\s*\[", line)
        and "as Record<string" not in line
        and not is_comment(line)
    ]
    assert not direct, (
        "ORIGIN_CHIP is indexed directly, which is what threw: "
        + " | ".join(l.strip() for l in direct)
        + " — go through `originChip()` instead."
    )
