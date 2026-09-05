"""Restatement control: the same figure printed twice is one figure.

Every extraction specification in docs/ carries a version of the same instruction. The
depreciation logic states it twice — §3.1 "Do not add English and Traditional Chinese versions of
the same disclosure" and §3.4 "The same depreciation value may appear in multiple notes. Values
from different datasets are alternative sources and must not be added together" — and the
securities logic repeats it in §3.1 and again in §8's comparability rules as "No bilingual
duplication exists". A filing that prints its notes in both scripts, or restates one figure under
a second heading, otherwise contributes it twice to a sum that looks unremarkable.

What the duplicate looks like in the data is narrow: an identical amount, in an identical currency
and scale, arriving at the same running total from a DIFFERENT note. Both halves matter.

  - Identical amount alone is not enough. Two lines of one note may legitimately charge the same
    figure — two asset classes each depreciating 60 — so collapsing on the amount would delete a
    real component. The note is therefore part of the identity: a repeat inside one note is
    counted, a repeat across notes is not.
  - Currency and scale are part of the fingerprint because 500 thousand and 500 million are not
    the same figure, and two different currencies of the same magnitude are two disclosures.

The first occurrence is the source and later ones are recorded as corroborating evidence, never
dropped silently — a reviewer needs to see that the second printing was found and understood.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class RestatementLedger:
    """Tracks which fingerprints a single running total has already absorbed, and from where."""

    _source_by_fingerprint: dict[tuple, object] = field(default_factory=dict)

    def is_restatement(self, amount: Decimal, currency: str | None, scale: Decimal | None,
                       note: object) -> bool:
        """Whether this contribution restates one already counted from a different note.

        Records the contribution as the fingerprint's source when it is the first to arrive, so
        a caller that asks and then adds stays consistent with one that only asks.
        """
        fingerprint = (amount, currency, scale)
        earlier = self._source_by_fingerprint.get(fingerprint)
        if earlier is None and fingerprint not in self._source_by_fingerprint:
            self._source_by_fingerprint[fingerprint] = note
            return False
        return earlier != note

    def source_of(self, amount: Decimal, currency: str | None,
                  scale: Decimal | None) -> object | None:
        """Which note first supplied this fingerprint, for the evidence trail."""
        return self._source_by_fingerprint.get((amount, currency, scale))
