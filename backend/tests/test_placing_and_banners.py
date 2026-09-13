r"""THE PLACING QUESTIONS: the banner the config's own vocabulary dropped, and what the template
already answers.

NEW FILE -> backend/tests/test_placing_and_banners.py

THREE THINGS, all of them measured on the configuration in force rather than reasoned about.

1. THE `notes` BANNER, AND THE SHADOWING THAT LOST IT. `Vocabulary.__init__` resolves the banner
   table as `tuple(... for b in self._v.section_banners) or SECTION_WORDS` — so a NON-EMPTY
   declaration REPLACES the shipped table rather than extending it. The set declared 18 banners
   where `mapping.SECTION_WORDS` ships 19, and the missing one was `notes`: the entry whose own
   comment in `mapping.py` records what its absence costs —

       "`section_token_of_scope("notes")` returned None, `_scope_tokens` folded the declaration to
       frozenset() — and an EMPTY scope means UNCONSTRAINED in `_in_section`. … each of the 5 that
       carries an alias ("Related party transactions", "Pledged assets", "Contingent liabilities",
       "Derivatives", "Secured borrowings") matched at confidence 1.0 under "Current assets",
       "NON-CURRENT ASSETS", "Operating activities" and "Revenue" alike"

   So the fix went into the Python fallback, and moving the vocabulary into configuration silently
   reverted it for the 8 lines scoped to the notes. The test that matters is not "the entry exists"
   but "the declaration does not fall behind the shipped table", because that is the failure mode:
   an inert scope constrains nothing and looks exactly like a scope nobody declared.

2. WHICH BANNER CHOICES CONSTRAIN NOTHING. Seven of the twenty offered scope ids resolve to no
   banner, and 72 lines rely on that — deliberately, for statement totals and for the five sections
   a filing prints no heading for. The screen marks them; this pins which they are, so one silently
   becoming constrained (or a constrained one going inert) is a test failure rather than a figure
   that moved.

3. WHAT THE TEMPLATE ALREADY PLACES. 462 of the configured lines, agreeing with the configuration
   on both statement and section, and 72 it does not place at all.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.line_item_matching import Vocabulary
from app.services.mapping import HEADING_ROW_SECTIONS, SECTION_WORDS

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
TEMPLATE = (pathlib.Path(__file__).resolve().parent.parent
            / "app" / "sample" / "templates" / "output_csv_hk_v1_template.json")

# The seven that name no printed banner, and why each is right to. Frozen deliberately: this is the
# list the screen marks "any banner", and a change either way is a change to what a row may claim.
_NO_BANNER = {
    # A statement total sits under whatever section was printed last above it, so no banner may
    # constrain it — `token_of_scope`'s own docstring says this about `*_top_level`.
    "bs_top_level",
    # Five compact sections a filing prints no heading for. `mapping.SECTION_WORDS`' comment names
    # exactly these as "untouched and stay unconstrained — a separate change".
    "statement_setup_controls", "supplemental_data", "off_balance_sheet_data",
    "credit_compliance", "capital_and_lease_commitments",
}


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def voc(shipped):
    return Vocabulary(shipped.vocabulary)


# ── 1. THE DECLARATION MUST NOT FALL BEHIND THE SHIPPED TABLE ─────────────────────────────────

def test_the_declared_banners_cover_every_shipped_one(shipped):
    """THE REGRESSION THIS FILE EXISTS FOR. A non-empty declaration replaces `SECTION_WORDS`
    wholesale, so a token present there and absent here is a constraint that silently stops
    constraining — and an inert scope is indistinguishable from an undeclared one."""
    declared = {b.token for b in shipped.vocabulary.section_banners}
    missing = [t for t, _ in SECTION_WORDS if t not in declared]
    assert not missing, (
        f"the set declares {len(declared)} banners and `mapping.SECTION_WORDS` ships "
        f"{len(SECTION_WORDS)}; these are shipped and not declared, so the scopes naming them are "
        f"inert: {missing}")


def test_each_declared_banner_keeps_the_shipped_headings(shipped):
    """A heading list that drifts from the shipped one is the same failure in a subtler form: the
    token still resolves, but a filing's actual wording stops matching it."""
    shipped_headings = dict(SECTION_WORDS)
    drift = {b.token: (tuple(b.headings), shipped_headings[b.token])
             for b in shipped.vocabulary.section_banners
             if b.token in shipped_headings and tuple(b.headings) != shipped_headings[b.token]}
    assert not drift, f"headings differ from the shipped table on {sorted(drift)}"


def test_the_notes_scope_actually_constrains(voc, shipped):
    """The eight lines the notes scope is FOR. Before the missing entry was restored every one of
    them was unconstrained, so a "Derivatives" or "Related party transactions" caption under a
    current-asset banner reached the notes concept at confidence 1.0."""
    assert voc.token_of_scope("notes") == "notes"
    on_notes = [i.key for i in shipped.items if "notes" in (i.section_scope or ())]
    assert len(on_notes) >= 8, on_notes
    assert "notes__derivatives" in on_notes


def test_the_notes_banner_is_not_a_heading_row_section(shipped):
    """`heading_row` admits a figureless row carrying only this heading as a section declaration.
    The eight balance-sheet and cash-flow banners are in `HEADING_ROW_SECTIONS` and the notes are
    not — and a bare Han 附注 is the note-reference COLUMN HEADER on every CSRC and HKEX statement,
    so reading one as a banner would refuse every `bs_` concept beneath it."""
    entry = next(b for b in shipped.vocabulary.section_banners if b.token == "notes")
    assert entry.heading_row is False
    assert ("notes" in HEADING_ROW_SECTIONS) is False
    # …and the deliberate omission: NOT the bare word, in either script.
    assert "notes" not in entry.headings
    assert not any(h in ("附注", "附註") for h in entry.headings)


def test_the_longest_heading_is_still_matched_first(shipped):
    """Order is load-bearing: "non current liabilities" must never be read as "current
    liabilities". Inserting a banner at the wrong index is how that breaks silently."""
    order = [b.token for b in shipped.vocabulary.section_banners]
    for longer, shorter in (("non_current_liabilities", "current_liabilities"),
                            ("non_current_assets", "current_assets")):
        assert order.index(longer) < order.index(shorter), f"{longer} must precede {shorter}"


# ── 2. WHICH CHOICES CONSTRAIN NOTHING ────────────────────────────────────────────────────────

def _offered(st) -> set[str]:
    ids = {s for d in st.items for s in d.section_scope if s}
    return ids | {s for sec in st.section_defaults.values() for s in sec.section_scope if s}


def test_exactly_the_expected_scope_ids_constrain_nothing(shipped, voc):
    """Pinned both ways. One of these becoming constrained would narrow where 72 lines may be
    claimed; a constrained one going inert would widen it — and both are silent."""
    inert = {s for s in _offered(shipped) if voc.token_of_scope(s) is None}
    assert inert == _NO_BANNER, (f"unexpectedly inert: {sorted(inert - _NO_BANNER)}; "
                                 f"no longer inert: {sorted(_NO_BANNER - inert)}")


def test_every_offered_scope_id_is_used_by_a_line_or_a_section(shipped):
    """The narrowing behind offering scope ids only. The raw banner tokens were offered beside
    them and 17 of 18 were used by nothing — the same sections in a second spelling, where the
    token loses the identity `inherits`, the analyst bucket and `section_defaults` are keyed on."""
    used = {s for d in shipped.items for s in (d.section_scope or ())}
    used |= {s for sec in shipped.section_defaults.values() for s in (sec.section_scope or ())}
    assert _offered(shipped) == used


def test_the_banner_tokens_are_a_second_spelling_of_the_same_sections(shipped, voc):
    """Why dropping them from the offer loses no option: each token a line could have named is
    reachable through the scope id that resolves to it."""
    tokens = {b.token for b in shipped.vocabulary.section_banners if b.token}
    resolved = {voc.token_of_scope(s) for s in _offered(shipped)} - {None}
    # Every banner any configured scope resolves to is a declared banner…
    assert resolved <= tokens
    # …and the one token a line names directly is itself a scope id, so nothing in force loses it.
    named = {s for d in shipped.items for s in (d.section_scope or ())} & tokens
    assert named <= _offered(shipped)


# ── 3. WHAT THE TEMPLATE ALREADY ANSWERS ──────────────────────────────────────────────────────

def _template_placing() -> dict[str, dict]:
    tpl = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}

    def walk(node, statement, section):
        key = node.get("canonical_key")
        if isinstance(key, str) and key and key not in out:
            out[key] = {"statement": statement, "section": section}
        for kid in node.get("children") or ():
            if isinstance(kid, dict):
                walk(kid, statement, section)

    for stmt in tpl.get("statements") or ():
        statement = str(stmt.get("type") or stmt.get("key") or "")
        for sec in stmt.get("sections") or ():
            walk(sec, statement, str(sec.get("canonical_key") or sec.get("node_id") or ""))
    return out


def test_the_template_and_the_configuration_agree_wherever_both_speak(shipped):
    """THE MEASUREMENT THAT DECIDED ITEM 3. Statement and section agree on every line the template
    places — which is what makes it honest for the screen to say the template settled them, and
    would have made deriving them possible. It is served rather than derived because of the next
    test."""
    placing = _template_placing()
    covered = [i for i in shipped.items if i.key in placing]
    assert len(covered) >= 460, len(covered)
    wrong = [(i.key, str(getattr(i.statement, "value", i.statement) or ""), i.inherits or "",
              placing[i.key])
             for i in covered
             if str(getattr(i.statement, "value", i.statement) or "")
             != placing[i.key]["statement"]
             or (i.inherits or "") != placing[i.key]["section"]]
    assert not wrong, f"{len(wrong)} line(s) disagree with the template, e.g. {wrong[:5]}"


def test_the_lines_the_template_does_not_place_are_the_internal_parts(shipped):
    """WHY THE FIELDS STAY AUTHORABLE. The template places no `internal` sub-line item, and those
    are the note-read parts — the lines an author edits most. Removing the controls would buy
    nothing exactly where it is needed, so the placing is explained and not taken away."""
    placing = _template_placing()
    missing = [i for i in shipped.items if i.key not in placing]
    assert missing, "the template places every line — this test's premise is gone"
    assert all(str(getattr(i, "namespace", "") or "") == "internal" for i in missing), (
        [i.key for i in missing
         if str(getattr(i, "namespace", "") or "") != "internal"][:8])
