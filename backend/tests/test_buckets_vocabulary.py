"""``services.buckets`` and ``services.mapping`` must read a template's ids through ONE vocabulary.

WHY THIS FILE EXISTS. ``buckets`` held its own copy of two of mapping's tables, and the second copy
had drifted: the statement-prefix table said ``"eq" -> "equity_changes"`` where mapping says
``"eq" -> "changes_in_equity"``. That is the third appearance of exactly this pair of spellings in
two places — a ``SearchScope`` token spelled ``income_statement`` against the backend's
``profit_and_loss``, then the statement gate, where a declaration the gate could not compare to the
page classifier's verdict refused every line item in that statement on every page of it. The two
constants are now imported rather than restated, and these tests hold the seam shut:

* IDENTITY, not equality (:func:`test_scope_tokens_are_one_object`). Equal copies are what this
  codebase keeps producing; the assertion that cannot be satisfied by re-typing the table is ``is``.
* AGREEMENT THROUGH THE PUBLIC FUNCTIONS (:func:`test_every_prefix_agrees_through_the_readers`,
  :func:`test_every_scope_token_agrees_through_the_readers`), so a future divergence that reaches for
  a local override rather than a second literal fails too.
* CANONICAL SPELLING (:func:`test_every_prefix_speaks_the_canonical_spelling`). This is the test that
  would have caught the drift on its own, in either module: ``normalize_statement`` exists precisely
  because two spellings are in circulation, so a table naming statements is wrong unless every value
  it holds is already a fixed point of it. ``equity_changes`` is not.

The drift was LATENT and these tests are not evidence that anything mis-filed. No shipped rulebook
carries an ``eq``-prefixed key or section scope (0 of the 475 line-item definitions, 0 of the 462
ontology concepts, 0 of the 183 HKFRS ones), and both callers of ``statement_of_section`` fold its
answer through ``normalize_statement`` before comparing it — so the wrong spelling was corrected
downstream at every live call site. What was broken was the invariant, not an output: the public
function handed out an off-vocabulary spelling, and the one caller that passes a PREFIXED scope id
(``api.routes.documents._retag_row``, which reads ``concept.section_scope[0]``) is the live path an
``eq_`` scope in tomorrow's rulebook would take.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services import buckets, mapping

_SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
_PREFIXES = sorted(mapping._STATEMENT_OF_PREFIX)
_SCOPE_IDS = sorted(mapping._COMPACT_SECTION_TOKENS)


def test_scope_tokens_are_one_object() -> None:
    """Not two equal dicts — the same dict. A corrected duplicate diverges again."""
    assert buckets._COMPACT_SECTION_TOKENS is mapping._COMPACT_SECTION_TOKENS


def test_statement_prefixes_are_one_object() -> None:
    assert buckets._STATEMENT_OF_PREFIX is mapping._STATEMENT_OF_PREFIX


@pytest.mark.parametrize("prefix", _PREFIXES)
def test_every_prefix_agrees_through_the_readers(prefix: str) -> None:
    """The statement a namespace names is one answer, whether read off a SECTION id or a KEY.

    Both functions do the same thing — take the head of an underscore-separated id and look up the
    statement it stands for — so they are compared through the functions and not only through the
    table, which also catches a divergence introduced as a local override.
    """
    assert buckets.statement_of_section(f"{prefix}_s3_reserves") == (
        mapping.statement_of_key(f"{prefix}_retained_profits"))


@pytest.mark.parametrize("prefix", _PREFIXES)
def test_every_prefix_speaks_the_canonical_spelling(prefix: str) -> None:
    """Every statement this table names must already be the spelling everything compares in.

    ``normalize_statement`` is the fold between the two vocabularies in circulation
    (``StatementType`` says "equity_changes", the page classifier says "changes_in_equity"). A table
    of statements is only usable if folding its values changes nothing: a value that moves under the
    fold is one the caller has to remember to normalise, and the callers that forget are the bug.
    """
    statement = buckets.statement_of_section(f"{prefix}_s1_x")
    assert statement == mapping.normalize_statement(statement)


def test_prefix_tables_cover_the_same_namespaces() -> None:
    """No prefix known to one module and unknown to the other — a key the gate scopes to a statement
    and this module reads as having none would be filed by section alone."""
    assert set(buckets._STATEMENT_OF_PREFIX) == set(mapping._STATEMENT_OF_PREFIX)


@pytest.mark.parametrize("prefix", _PREFIXES)
def test_section_prefix_stripping_covers_every_prefix(prefix: str) -> None:
    """The ordinal-stripping regex is derived from the prefix table, so it cannot fall behind it.

    A prefix the regex does not strip leaves the section token with its ``xx_s2_`` head, which
    matches no section phrase — the whole statement arriving as ``unknown_section``.
    """
    assert buckets.section_token(f"{prefix}_s2_current_assets") == "current_assets"
    assert buckets.section_token(f"{prefix}_top_level") == "top_level"


@pytest.mark.parametrize("scope_id", _SCOPE_IDS)
def test_every_scope_token_agrees_through_the_readers(scope_id: str) -> None:
    """A compact scope id resolves to one section token, whichever module is asked.

    ``mapping.section_token_of_scope`` decides which printed banner may scope a concept; ``section_token``
    decides which analyst bucket the same concept's rows land in. Disagreement here does not raise:
    the concept is gated into a section and then filed under a different one, or under Others.
    """
    assert buckets.section_token(scope_id) == mapping.section_token_of_scope(scope_id)


@pytest.mark.parametrize("scope_id", _SCOPE_IDS)
def test_every_scope_token_has_a_declared_home(scope_id: str) -> None:
    """Every compact scope is either bucketed or explicitly outside the taxonomy — never unknown.

    ``unknown_section`` is the reason code for "a section id nothing in this vocabulary names", and a
    scope id mapping declares is by definition named. So a new compact scope added to mapping must
    arrive here as ``section`` (it has a bucket) or ``outside_taxonomy`` (``is_pl``, ``is_oci``,
    ``is_retained`` — sections a filing prints that these thirteen buckets deliberately do not name),
    and the third answer means the two tables have parted company again.
    """
    _bucket, reason = buckets.bucket_of(scope_id, None)
    assert reason in ("section", "outside_taxonomy")


@pytest.mark.parametrize("template", ["output_csv_hk_line_items.json"])
def test_shipped_vocabulary_matches_the_code_it_was_exported_from(template: str) -> None:
    """The seed's ``vocabulary`` block is these same two tables as CONFIG, and must not have aged.

    ``scripts/build_line_items.py`` writes them out of ``mapping``; the ported matcher falls back to
    the code constants when the config carries none (``line_item_matching.Vocabulary``). So a third
    spelling can enter through a stale seed as easily as through a stale copy, and it would enter
    silently — the fallback hides it.
    """
    vocab = json.loads((_SAMPLES / template).read_text(encoding="utf-8"))["vocabulary"]
    assert vocab["statement_prefixes"] == buckets._STATEMENT_OF_PREFIX
    assert vocab["scope_tokens"] == buckets._COMPACT_SECTION_TOKENS


def test_changes_in_equity_prefix_reaches_its_own_bucket() -> None:
    """The consequence, end to end: an ``eq_`` section id lands in the changes-in-equity bucket.

    No shipped rulebook has such a scope yet (see the module docstring), so this is the guard for the
    day one does rather than a reproduction of a failure. It passed before the deduplication too,
    because ``bucket_of`` folds through ``normalize_statement`` — which is exactly why the drift went
    unnoticed, and why the tests above assert on the spelling itself and not only on the bucket.
    """
    assert buckets.statement_of_section("eq_s1_share_capital") == "changes_in_equity"
    assert buckets.bucket_of("eq_s1_share_capital", None) == ("changes_in_equity", "statement")
