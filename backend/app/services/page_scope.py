"""Page-scope counts, derived from the page cards they sit above.

The header sentence ("Focused 8 of 84 pages · 76 pages skipped") and the filter chips are the
SAME fact as the grid of cards underneath, so they are counted here, once, from the cards. Two
routes serve that screen — the real document and the sample project — and the sample route did not
count at all: ``focused: 14, total: 84, skipped: 70`` were literals sitting over ten page cards,
so the screen read "Focused 8 of 84" with eight of ten cards in scope, and an "All 84" chip beside
ten. The filter chips were worse than wrong: the client maps them POSITIONALLY to page kinds
(all / face / notes / other), and the sample route served six differently-ordered labels, so
clicking one filtered by a kind it did not name.

Deriving both from ``cards`` makes the arithmetic unarguable, and makes the invariant that already
covers the real route (``total == len(pages)``) cover the sample one too.

The Page Scope screen now heads its pages with the count of pages a run READS (``read``, by the
reader's own rule — ``services.page_reading``) and lists the sections instead of these chips. The
counts and the chips stay in the payload, counted the same way, for the callers that read them.
"""
from __future__ import annotations

# Positional, and it must stay positional: chip INDEX is the page kind it counts (all / face /
# notes / other), so a label reordered here silently re-points a chip.
FILTER_LABELS = ("All pages", "Face", "Notes", "Other")

# Every card is one of these. "face" and "notes" are the pages extraction reads; everything the
# classifier put elsewhere is "other", which is also what the fourth chip counts.
_KINDS = ("face", "notes", "other")


def normalise_kind(kind: str | None) -> str:
    """A card's filterable kind. Anything that is not face or notes is other — including a card
    with no kind at all, which is how a page the classifier could not place arrives."""
    return kind if kind in ("face", "notes") else "other"


def scope_counts(cards: list[dict]) -> dict:
    """``filters``/``focused``/``total``/``skipped`` for a list of page cards.

    Counted off ``kind`` and ``included`` on each card, so a card that changes category or falls
    out of scope moves the header and its chip in the same breath.
    """
    counts = dict.fromkeys(_KINDS, 0)
    for c in cards:
        counts[normalise_kind(c.get("kind"))] += 1
    total = len(cards)
    focused = sum(1 for c in cards if c.get("included"))
    return {
        "filters": [{"label": FILTER_LABELS[0], "count": total},
                    {"label": FILTER_LABELS[1], "count": counts["face"]},
                    {"label": FILTER_LABELS[2], "count": counts["notes"]},
                    {"label": FILTER_LABELS[3], "count": counts["other"]}],
        "focused": focused,
        "total": total,
        "skipped": total - focused,
    }
