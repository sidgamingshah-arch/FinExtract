"""Which line-item set is in force for a template: **the latest one**.

This module replaces ``services/ontology_select``, which is deleted. There is no longer a rulebook
to choose between — line items is the single configuration engine, so the only question left is
which stored VERSION of that configuration the next run reads, and it is asked of
``line_item_versions``. Nothing here should ever grow a second engine to pick from again.

More than one set can target the same template, and every consumer used to decide for itself by
taking the highest ``version`` among the matches. That answers the wrong question — ``version``
counts EDITS to one configuration, so it cannot compare two different ones — and with two sets both
at version 1 the choice fell through to whichever row the database returned first, making the
product's mapping behaviour a property of insertion order.

The answer is simply the most recently stored one. An admin uploads a set, or corrects a line item
from the configuration screen, or start-up refreshes the shipped file: whichever of those happened
last is what the next run maps against. Nothing outranks recency, because there is no honest sense
in which an older configuration is more current than a newer one.

WHAT WAS HERE AND IS GONE, so nobody reinstates it. The retired module ranked on five tests —
declared supersession, the shipped key, declaring anything at all, incumbency, then version — each
added to work around the previous one, and their net effect was that publishing the current
configuration beside an obsolete one changed nothing. What they were really guarding is a skeleton
upload becoming the configuration a real extraction runs on, and that belongs at the door rather
than in a ranking: the ``/line-items`` publish gate refuses a set that recognises nothing, where an
author is present to be told why.

``metadata.supersedes``, ``superseded_keys`` and ``_supersedes`` are gone with it. Supersession was
ontology-metadata LABELLING — "has this rulebook been declared replaced?" — it explicitly no longer
changed which configuration runs, and its only two consumers were an ontology API field and a
frontend badge that both go away with the ontology surface. ``_shipped()`` is gone for the same
reason: it existed only to feed that labelling, and "which set does this repo ship?" is answered
where the shipped files live (``sample.reference``), not from here.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session


def versions_for_template(session: Session, template_key: str) -> list:
    """Every stored line-item set targeting ``template_key``, newest edit of each key first."""
    from app.db.models import LineItemVersion

    return list(session.execute(
        select(LineItemVersion)
        .where(LineItemVersion.target_template_key == template_key)
        .order_by(LineItemVersion.line_items_key, LineItemVersion.version.desc())
    ).scalars().all())


def select_for_template(session: Session, template_key: str):
    """The line-item set in force for a template, or None when none targets it.

    ONE TEST: **the latest set wins.** Whatever was stored most recently for this template —
    uploaded by an admin, or published by correcting a line item from the configuration screen, or
    refreshed from the shipped file at start-up — is what the next run maps against.

    That is the whole rule, and it replaces five. The five were: drop declared supersessions, prefer
    the shipped key, prefer a set that declares a supersession, prefer the incumbent key, then
    highest version. Every one of them after the first was added to work around the one before it —
    "prefer the shipped key" existed only to defeat "prefer the incumbent", which existed only to
    defeat "highest version", which had let a skeleton win on sort order. A precedence hierarchy
    nobody asked for, whose net effect was that publishing the current configuration beside an old
    one changed nothing, because the old one had been seen first and said it superseded something.

    THE HOLE THE OLD TESTS WERE REALLY GUARDING was a skeleton upload — every item a stub with no
    aliases at all — becoming the configuration a real extraction runs on. That is not a precedence
    question and ranking cannot answer it honestly: a configuration that recognises nothing is not a
    lower-priority configuration, it is not a configuration. It is refused at the door instead, on
    publish, where an author is present to be told why.

    ``created_at`` decides, because "latest" means latest in time and a version number only counts
    edits WITHIN one key — it cannot compare two different configurations, which is the mistake this
    rule was originally written to fix. Version and id break an exact tie so the answer is stable
    rather than a property of row order.
    """
    rows = versions_for_template(session, template_key)
    if not rows:
        return None
    return max(rows, key=lambda r: (r.created_at, r.version, r.id))
