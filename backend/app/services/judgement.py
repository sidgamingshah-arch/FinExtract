"""Identity for a human judgement on a review finding — content-derived, never id-derived.

A reviewer who examines a finding and records that the figures stand is making a claim about
WHAT was on the card, not about where that card happened to sit in a list. The row-shaped check
builder keys its ``id`` on the ROW INDEX of the extracted line — ``chk-unmapped-{i}`` — and a row
index moves whenever extraction composition changes: one more line reconstructed, one fewer heading
suppressed, a different page order. An id-keyed acceptance would then silently land on a DIFFERENT
line item after a re-run, marking a real problem as vouched for by a named person who never saw it.
That is strictly worse than having no acceptance mechanism at all, so identity here is the sha256 of
the finding's canonicalized SUBJECT — the semantic thing being judged — and never the check id.

NO LINE NUMBERS AND NO DEAD NAMES IN THIS FILE'S PROSE. It named two builders and two line numbers,
and by the time three separate reviewers read it one of the builders (``chk-lowconf-{i}``) had been
deleted and both line numbers pointed at unrelated code. Each of them re-filed the bug this design
already prevents, because a post-mortem written in the present tense reads as a live defect. The
argument above needs no citation to stand; ``_ROW_SHAPED_TYPES`` in api/routes/documents.py is the
current answer to "which builders are row-shaped", and it is one grep away.

The second hash, over the finding's EVIDENCE, is what makes an acceptance withdraw itself when
the figures move: the same subject carrying different numbers is a different claim, and it is
reported as ``stale`` rather than quietly kept. The digest is recomputed here from the stored
evidence on every read; it is deliberately NOT a column on the table, because a derived value
persisted beside its source is the two-places-computing-one-quantity bug.

This is the ONLY module in the codebase that hashes, and it is pure: no DB, no FastAPI, and no
translation. Localized strings arrive as already-bound callables from the route that owns the
review vocabulary, which is also what keeps ``subject_key`` and ``evidence_digest`` byte-identical
in all four locales — identity must not depend on who is looking.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

STATUS_OPEN = "open"
STATUS_ACCEPTED = "accepted"
STATUS_STALE = "stale"
# The identity scheme failed: two findings in one payload share a subject_key and carry DIFFERENT
# evidence, so nothing in the subject tells them apart. No judgement may be attributed to any of
# them — see `apply_judgements` for why an honest refusal is the only correct answer here.
STATUS_CONFLICT = "conflict"

# Rank used to sort the served queue. Conflict first — the queue cannot tell two findings apart,
# so it cannot tell the truth about either until extraction distinguishes them. Stale next:
# someone vouched for a figure that has since moved, which is more urgent than a finding nobody
# has looked at, because it carries a name against numbers that no longer exist.
_RANK = {STATUS_CONFLICT: 0, STATUS_STALE: 1, STATUS_OPEN: 2, STATUS_ACCEPTED: 3}


def canonical(obj) -> str:
    """One byte-stable spelling of a JSON-able object.

    Sorted keys and no whitespace: a dict that differs only in insertion order is the same
    subject, and two runs that build the same subject must hash alike.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def subject_key(subject: dict) -> str:
    """The identity of WHAT was judged. See the module docstring for why it is not the check id."""
    return hashlib.sha256(canonical(subject).encode()).hexdigest()


def evidence_digest(evidence: dict) -> str:
    """The identity of the FIGURES that were judged, so a changed figure reads as ``stale``."""
    return hashlib.sha256(canonical(evidence).encode()).hexdigest()


def q(x):
    """Quantize a figure to the whole unit the card actually prints.

    Every arithmetic card renders with ``:,.0f``, so sub-unit float drift between two runs is a
    change the human could not possibly have seen. Fingerprinting the raw float would withdraw an
    acceptance for a difference that was never on screen.
    """
    if x is None:
        return None
    return int(round(float(x)))


def norm(s) -> str:
    """A source label reduced to what identifies it: collapsed whitespace, case-folded.

    Re-parsing a page legitimately shifts a caption's spacing ("Trade  receivables" vs "Trade
    receivables"); that is not a different line item.
    """
    return " ".join(str(s or "").split()).casefold()


def rank(status: str) -> int:
    """Sort rank for a status — conflict, then stale, then open, then accepted."""
    return _RANK.get(status, _RANK[STATUS_OPEN])


def apply_judgements(
    checks: list[dict],
    rows: list[dict],
    *,
    label_fn: Callable[[dict], str],
    rows_fn: Callable[[dict, dict], list[list[str]]],
    changed_fn: Callable[[dict, list[str]], str],
    conflict_fn: Callable[[dict, int, bool], str],
) -> dict:
    """Attach each finding's judgement state, and report the judgements nothing matches.

    ``rows`` is the list of IN-FORCE accepted judgement rows as plain dicts
    (``subject_key``, ``subject``, ``evidence``, ``reason``, ``actor``, ``actor_role``, ``at``,
    ``run_id``). Every check in ``checks`` is mutated in place with ``status``, ``judgement``,
    ``ambiguous``, ``ambiguous_count``, ``conflict``, ``conflict_count``, ``conflict_note`` and
    ``judgement_withheld``.

    TWO FINDINGS SHARING ONE SUBJECT ARE NOT ONE CASE. The subject is built to discriminate (a
    content-derived source anchor, api/routes/documents.py::_prov_anchor), but no scheme can
    promise it always will, so the two possible outcomes are distinguished here rather than
    conflated:

    * same subject_key AND the same evidence_digest — the findings are indistinguishable in
      every respect the card showed, so one judgement legitimately covers all of them. That is
      ``ambiguous``, and the caption "accepting one accepts them all" is true;
    * same subject_key and DIFFERING evidence_digest — the identity scheme FAILED. These are
      demonstrably different claims (different figures were printed) that identity cannot tell
      apart. Attributing a stored judgement to any of them would put a named reviewer's verdict,
      reason and timestamp on figures they never saw, which the module docstring calls strictly
      worse than having no acceptance mechanism at all. So no judgement is attached to ANY card
      in such a group, the group is served as ``conflict``, and the endpoint refuses acceptance
      on that subject. An honest refusal is the correct product behaviour; a fabricated
      attribution is not.

    A withheld judgement is NOT reported as orphaned: the finding it was made on is still being
    raised, so "this judgement's finding is no longer raised" would be a second false statement.
    It is flagged on the cards as ``judgement_withheld`` instead.

    The four localized strings this needs are supplied bound: ``label_fn(subject)`` names an
    orphaned judgement's subject in prose, ``rows_fn(subject, evidence)`` renders the judged
    figures as ``[label, value]`` pairs — the same two-column shape as a check's ``calc``, so the
    client reuses one renderer and formats no figure itself — ``changed_fn(subject, keys)`` names
    the quantities that moved, and ``conflict_fn(subject, count, withheld)`` says plainly that the
    queue cannot tell these findings apart. Keeping the translation outside this module is what
    makes the two digests locale-independent.
    """
    index = {r["subject_key"]: r for r in rows}
    # The findings in THIS payload grouped by subject, so sharing a subject can be classified
    # before anything is attributed to anybody.
    groups: dict[str, list[dict]] = {}
    for c in checks:
        key = c.get("subject_key")
        if key:
            groups.setdefault(key, []).append(c)
    # A group whose members disagree about their evidence is a broken identity, not an ambiguity.
    conflicted = {key for key, group in groups.items()
                  if len({c.get("evidence_digest") for c in group}) > 1}

    counts = {STATUS_OPEN: 0, STATUS_ACCEPTED: 0, STATUS_STALE: 0, STATUS_CONFLICT: 0}
    matched: set[str] = set()
    for c in checks:
        key = c.get("subject_key")
        n = len(groups.get(key, ())) if key else 0
        row = index.get(key) if key else None
        c["conflict"] = key in conflicted
        c["conflict_count"] = n if c["conflict"] else 0
        # `ambiguous` promises "accepting one accepts them all", which is FALSE of a conflict
        # group — that caption on cards carrying different figures is how this got shipped.
        c["ambiguous"] = n > 1 and not c["conflict"]
        c["ambiguous_count"] = n if c["ambiguous"] else 0
        c["judgement_withheld"] = c["conflict"] and row is not None
        c["conflict_note"] = conflict_fn(c.get("subject") or {}, n, row is not None) \
            if c["conflict"] else ""
        if c["conflict"]:
            c["status"] = STATUS_CONFLICT
            c["judgement"] = None
            counts[STATUS_CONFLICT] += 1
            if row is not None:
                # Withheld, not orphaned: the finding is on the screen, so the judgement's
                # subject has not vanished — it just cannot be pinned to one of these cards.
                matched.add(key)
            continue
        if row is None:
            c["status"] = STATUS_OPEN
            c["judgement"] = None
            counts[STATUS_OPEN] += 1
            continue
        matched.add(key)
        stored = row.get("evidence") or {}
        # Recomputed here, never read off the table: a stored digest and the evidence it was
        # taken from are one quantity in two places, and they drift.
        same = evidence_digest(stored) == c.get("evidence_digest")
        changed = [] if same else _changed_keys(stored, c.get("evidence") or {})
        c["status"] = STATUS_ACCEPTED if same else STATUS_STALE
        counts[c["status"]] += 1
        c["judgement"] = {
            "verdict": "accepted",
            "actor": row.get("actor") or "",
            "actor_role": row.get("actor_role") or "",
            "at": row.get("at") or "",
            "reason": row.get("reason") or "",
            "run_id": row.get("run_id") or "",
            # The figures AS ACCEPTED, not as they now stand: on a stale card the point is what
            # the person was looking at when they vouched.
            "accepted_rows": rows_fn(row.get("subject") or {}, stored),
            "changed": changed,
            "changed_label": changed_fn(row.get("subject") or {}, changed) if changed else "",
        }

    orphaned = [
        {"subject_key": r["subject_key"], "subject_label": label_fn(r.get("subject") or {}),
         "actor": r.get("actor") or "", "actor_role": r.get("actor_role") or "",
         "at": r.get("at") or "", "reason": r.get("reason") or ""}
        for r in rows if r["subject_key"] not in matched
    ]
    return {"orphaned": orphaned,
            "counts": {STATUS_OPEN: counts[STATUS_OPEN],
                       STATUS_ACCEPTED: counts[STATUS_ACCEPTED],
                       STATUS_STALE: counts[STATUS_STALE],
                       STATUS_CONFLICT: counts[STATUS_CONFLICT]}}


def _changed_keys(stored: dict, current: dict) -> list[str]:
    """Which evidence keys differ, so the screen can name what moved rather than saying "something
    changed" over figures the reader then has to diff by eye."""
    return [k for k in sorted(set(stored) | set(current)) if stored.get(k) != current.get(k)]
