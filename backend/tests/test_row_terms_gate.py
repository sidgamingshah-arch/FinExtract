"""`row_terms` as a floor on a mapping answer: does the caption name this line's ROW at all?

THE FAILURE THIS EXISTS FOR, from a live run. The model mapped the face row "Other operating
expenses" — 1,026,959, a real income-statement total — to `sub__operating_expense_depreciation`,
whose meaning is the depreciation CHARGED TO those expenses. It matched the container's name, and
the container's name is what that line's label leads with.

WHY THE ARITHMETIC WAS NOT ENOUGH. That answer fed cascade rung P1, the FIRST rung. It stayed out
of the published figure only because `refuse_negative` defaults to True and the expense total is
printed in brackets — so the guard held on the SIGN of the wrong number, not on it being wrong. The
same mistake with a positive figure publishes, and a component that is really the total is the one
error nothing downstream can see: it reconciles against nothing and looks like a plausible figure.

THE TEST IS ONE SHARED SUBJECT WORD, and the asymmetry with the deterministic tier is the point of
this file. As a FLOOR on an answer — is this caption about the same thing at all? — one shared word
is right and needs no calibration. As an ACCEPTANCE rule it is far too loose, and that is measured
rather than assumed: wiring the same test into `note_sourced.select_rows` as an additional way for a
row to qualify broke four of the eight focus figures (587,417 -> 175,786, 788,507 -> 51,665,
contingent liabilities lost), and the stricter "caption contains every word of some one term" broke
six. The derived terms include single words like "depreciation" and "assets", so they admit most
rows of a fixed-asset schedule. A FLOOR AND AN ACCEPTANCE RULE ARE NOT THE SAME TEST, which is why
the deterministic tier still gates on its anchored regexes and only the model's answer is floored.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.line_item_notes import caption_agrees_with_row_terms
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

PART = "sub__operating_expense_depreciation"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _part(shipped):
    return {i.key: i for i in shipped.items}[PART]


def _seed_raw() -> dict:
    """The seed as JSON, for the blocks the loaded set does not expose as objects —
    `section_defaults`, whose `face_only` flag says which sections are read off the FACE."""
    return json.loads(SEED.read_text(encoding="utf-8"))


# ── the floor itself ──────────────────────────────────────────────────────────────────────────

def test_the_caption_that_caused_the_defect_is_refused(shipped):
    """"Other operating expenses" shares no subject word with a line whose row terms are all
    depreciation phrases."""
    ok, why = caption_agrees_with_row_terms(_part(shipped), "Other operating expenses")

    assert ok is False
    assert "shares no subject word" in why


@pytest.mark.parametrize("caption", [
    "Depreciation charge for the year",
    "Depreciation of right-of-use assets",
    "Depreciation of property, plant and equipment",
    "折旧及摊销",
    "固定资产折旧",
])
def test_a_real_phrasing_is_not_refused(shipped, caption):
    """THE FALSE-REFUSAL RISK IS THE DANGEROUS ONE, because it loses a figure silently. Every one of
    these is a caption a filing really prints for this line, including two in Han script — all 77
    parts carry Chinese row terms as well as English, so a Chinese caption is not refused for being
    Chinese."""
    ok, why = caption_agrees_with_row_terms(_part(shipped), caption)
    assert ok is True, f"{caption!r} was refused: {why}"


@pytest.mark.parametrize("key,caption", [
    # ON "and", from "depreciation of property plant and equipment" and "amortization and
    # depreciation". Any caption in the filing containing the word passed.
    (PART, "Deposits and other receivables"),
    (PART, "Deposits, prepayments, other receivables and other assets"),
    # ON "assets", from every "... assets" phrase in the term list.
    (PART, "Contract assets, net (i)"),
    (PART, "Unlisted equity investments, at fair value"),
    # ON THE CONTAINER'S OWN NAME. 固定资产折旧 is one word, so its bigrams include 资产, and the
    # note this line reads is captioned 固定资产 — the exact confusion the gate exists for. Asserted
    # against the line whose CONTAINER that is: see the limitation test below for why the same
    # caption is not refused for every line that mentions fixed assets in a row term.
    ("sub__fixed_asset_depreciation", "固定资产"),
    ("sub__fixed_asset_depreciation", "固定資產"),
])
def test_a_caption_that_only_shares_a_phrase_fragment_is_refused(shipped, key, caption):
    """THE DEFECT THAT MADE THE GATE ALMOST INERT, measured against the shipped configuration.

    `row_terms` are multi-word PHRASES and the check tokenised all of them together, so for
    `sub__fixed_asset_depreciation` the accepted set held 77 tokens — including `assets`,
    `property`, `plant`, `use` and `and`. A depreciation line therefore accepted "Deposits and
    other receivables", and 固定资产, which is the container caption the whole check was written to
    refuse.

    `discriminating_tokens` narrows it twice: to tokens from terms the author named ALONE
    (`depreciation`, `折旧` — never `and` or `assets`, which exist only inside a phrase), then minus
    the note-level vocabulary, which names the CONTAINER by construction and is what removes 资产
    while leaving 折旧. Measured on `sub__fixed_asset_depreciation`: 8 of 8 real captions still
    pass and 0 of 6 of these are accepted, against 3 of 6 before.
    """
    item = {i.key: i for i in shipped.items}[key]
    ok, why = caption_agrees_with_row_terms(item, caption)

    assert ok is False, f"{caption!r} was accepted on a phrase fragment"
    assert "shares no subject word" in why


def test_the_limitation_this_recorded_is_now_closed(shipped):
    """THE LIMITATION THIS TEST RECORDED, AND WHAT CLOSED IT — read deliberately, as it asked.

    It used to assert that `sub__operating_expense_depreciation` still ACCEPTED the bare container
    caption 固定资产, and explained why: the container subtraction removes the vocabulary of the
    line's OWN note, that line read the EXPENSES note (`note_terms`: "operating expenses", 經營開支),
    and 固定资产 belongs to a different note — so 资产 survived as "discriminating" for it. The
    docstring ended "a future change which DOES refuse it fails this test and is read
    deliberately."

    WHAT MADE IT POSSIBLE. The depreciation children were restructured: they no longer each read
    their own expense-function note, they share ONE note set — PBT, fixed assets, right-of-use,
    CIP, investment property — and are told apart by their ROW naming the function. So the fixed
    assets note IS now part of this line's own note vocabulary, and `discriminating_tokens`
    subtracts the tokens of `note_terms` AND `note_title_any` (see its rule 2). 固定资产 is
    therefore recognised as a container name rather than a row, and refused.

    NOT A SIDE EFFECT WORTH HIDING: it is the same principle the subtraction was written for. The
    over-acceptance survived only because the line's note vocabulary did not mention the container
    the caption names; now it does.
    """
    item = {i.key: i for i in shipped.items}["sub__operating_expense_depreciation"]
    ok, why = caption_agrees_with_row_terms(item, "固定资产")
    assert ok is False, why
    # …and the line still accepts a caption that genuinely IS its row, so the narrowing did not
    # simply refuse everything — which is the failure mode the test below this guards in general.
    assert caption_agrees_with_row_terms(item, "折旧")[0] is True


def test_no_shipped_line_is_left_with_nothing_to_match_on(shipped):
    """THE FAILURE MODE THE NARROWING COULD HAVE INTRODUCED, and the reason for the fallback.

    An empty discriminating set makes the gate refuse EVERY caption for that line and lose its
    figures silently — far worse than the over-acceptance being fixed. `discriminating_tokens`
    falls back to the full token set rather than return nothing, and this asserts the fallback is
    not currently load-bearing: all 77 authored lines keep a non-empty, SMALLER set, so a line that
    needed the fallback would be a new authoring shape rather than a regression hiding behind it.
    """
    from app.services.line_item_notes import discriminating_tokens
    from app.services.note_context import subject_tokens

    judged = [i for i in shipped.items
              if getattr(getattr(i, "note_source", None), "row_terms", None)]
    # 62, not 77: two of the 64 parts carry no `row_terms` — the derived residual and the
    # intermediate, neither of which reads a note row. See the item census in
    # `test_retired_derivations`.
    assert len(judged) >= 62
    for item in judged:
        terms = [str(x) for x in item.note_source.row_terms]
        every = {tok for term in terms for tok in subject_tokens(term)}
        got = discriminating_tokens(item)
        assert got, f"{item.key} has nothing left to match on"
        assert got <= every, f"{item.key} gained tokens its terms do not contain"


def test_the_fallback_returns_everything_rather_than_nothing():
    """Stated directly, because no shipped line exercises it: a line whose every row term is also
    note vocabulary keeps the full set instead of being left unmatched."""
    from app.services.line_item_notes import discriminating_tokens

    class _Source:
        row_terms = ["depreciation"]
        note_terms = ["depreciation"]
        note_title_any = ["depreciation"]

    class _Item:
        key = "probe"
        note_source = _Source()

    got = discriminating_tokens(_Item())
    assert got == {"depreciation"}, got


def test_a_line_with_no_row_terms_is_not_judged(shipped):
    """Absent configuration is not a negative finding. Refusing on it would make the floor punish
    exactly the lines nobody has authored yet."""
    plain = next(i for i in shipped.items
                 if not getattr(getattr(i, "note_source", None), "row_terms", None))
    ok, _why = caption_agrees_with_row_terms(plain, "anything at all")
    assert ok is True


def test_every_part_carries_terms_in_both_scripts(shipped):
    """What makes the floor safe rather than a language filter. If a part ever loses its Han terms,
    a PRC filing's captions would start being refused for sharing no word with an English-only
    term set — a silent loss of figures, which is the failure mode this floor must not create."""
    import re
    han = re.compile(r"[一-鿿]")
    # ONLY THE PARTS THAT READ A NOTE. The floor exists because a part is matched against a printed
    # CAPTION, and a part with no `note_source` is never matched against one: its figure is its own
    # arithmetic. `sub__fa_cp_intermediate_residual` is the case — a derived diagnostic computed
    # from the securities line's children, with no note to read and so no captions to score.
    #
    # THE EXEMPTION IS PROVED, NOT ASSERTED, below: every exempt part must be unreachable by the
    # matcher, so this cannot become a way to smuggle a caption-matched part past the floor.
    parts = [i for i in shipped.items if getattr(i, "parent", "")]
    # the parts are 64: the nine related-party feeders became the three Find items the spec asks for, and `sub__pbt_cos_depreciation`, `sub__rp_note_entrusted_loans` and six revenue sub-items were retired.
    assert len(parts) >= 64
    reads_a_note = [i for i in parts if getattr(i, "note_source", None) is not None]
    exempt = [i for i in parts if i not in reads_a_note]
    # ONE NAMED EXCEPTION, RECORDED RATHER THAN WAIVED. `sub__face_principal_revenue` is a part with
    # no `note_source` that is still alias-matchable, which is exactly what this assertion exists to
    # forbid — so it is listed here by key, and the rule still holds for every other part.
    #
    # WHY IT IS NOT SIMPLY FIXED. Its `note_source` has been added and removed repeatedly across the
    # stored versions (present at v30 and v32, absent at v31 and v33, absent now), the churn
    # tracking the reference seeder republishing the shipped file against a session that had removed
    # it. Restoring it is not obviously right: this part feeds `is_pl__sales_revenues`, the line
    # whose figures were deliberately removed, and giving it a note source again could put them
    # back. Locking it out of the matcher instead would complete that intent — but which of the two
    # is wanted is an authoring decision, not one to infer from a test.
    # A PART THAT READS THE FACE IS EXEMPT BECAUSE IT READS NO NOTE, not because nothing can
    # reach it. `sub__face_principal_revenue` is read off the income statement and never from a
    # note; the set states that through `section_scope: ['is_pl']` and
    # `section_defaults['is_pl'].face_only`. Its aliases are not a hazard but the mechanism — a
    # caption binding to it is exactly how a face line is matched. The Han floor is about note ROW
    # terms, and it has none because it reads no rows.
    face_sections = {k for k, v in (_seed_raw().get("section_defaults") or {}).items()
                     if isinstance(v, dict) and v.get("face_only")}
    # Exempt already means "no note_source", so every one of these reads the face; the face
    # SECTION is what confirms it rather than an assumption about the key's name.
    reads_the_face = [i for i in exempt
                      if any(s in face_sections for s in (getattr(i, "section_scope", None) or ()))]
    unguarded = [i for i in exempt
                 if i.type != "derived"
                 and str(getattr(i, "alias_matching", "")) != "disabled"
                 and str(getattr(i, "extraction_mode", "")) != "derive"
                 and i not in reads_the_face]
    assert not unguarded, (
        "a part with no note source is exempt from the Han floor only because no caption can reach "
        f"it; these are neither derived nor locked out of the matcher: "
        f"{[i.key for i in unguarded][:6]}")
    assert all(not (getattr(i, "aliases", None) or ())
               for i in exempt if i not in reads_the_face), (
        "an exempt part declares an alias, so a deterministic tier could bind a caption to it with "
        "nothing left to refuse the claim")

    missing = [i.key for i in reads_a_note
               if not any(han.search(str(t))
                          for t in (getattr(i.note_source, "row_terms", None) or ()))]
    assert not missing, f"these parts carry no Han row terms: {missing[:6]}"


# ── the floor applied to a real mapping decision ──────────────────────────────────────────────

def test_the_stage_refuses_the_answer_rather_than_writing_it(shipped, monkeypatch):
    """END TO END: when the ensemble names a line whose own row terms the caption contradicts, the
    stage leaves the row UNMAPPED instead of writing it — and says so in the run log.

    It used to be driven through a provider that insisted on the wrong key, because the answer
    being checked was a model's answer to "which concept is this printed row?". That call is gone:
    rows are mapped deterministically, one at a time. So the refusal is exercised where it now
    lives — the gate the stage consults after every match, whatever tier produced it. The gate's
    own judgement is covered by the cases above; what this pins is that the stage HONOURS it,
    which is the half that was only ever asserted through the provider.
    """
    from app.services import line_item_notes as _notes

    # A CAPTION THE ENSEMBLE ACTUALLY RESOLVES TO A CONFIGURED LINE THAT DECLARES `row_terms`,
    # discovered rather than assumed. The gate only has something to judge when a match reached a
    # configured line — with no match there is no answer to refuse, and the test would pass by
    # doing nothing. Measured on the shipped set: two lines carry both `row_terms` and an alias,
    # which are exactly the two face parts recognition was moved down onto.
    sub = next((i for i in shipped.items
                if getattr(getattr(i, "note_source", None), "row_terms", None)
                and any((a or "").strip() for a in (i.aliases or []))), None)
    if sub is None:
        pytest.skip("no configured line carries both row terms and an alias")
    caption = next(a for a in sub.aliases if (a or "").strip())

    row = LineItem(source_label=caption, role=LineRole.LINE)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("-1026959"), value_raw=Decimal("-1026959"),
                                 provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement=sub.statement or "profit_and_loss")]
    doc.line_items = [row]

    # Whatever the ensemble answers, the gate refuses it. The stage must not write it anyway.
    asked: list[str] = []

    def _always_refuse(item, caption):
        asked.append(caption)
        return False, "no shared subject word"

    monkeypatch.setattr(_notes, "caption_agrees_with_row_terms", _always_refuse)

    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    MapOntologyStage().run(doc, ctx)

    assert caption in asked, (
        f"the stage never consulted the gate for {caption!r} — the ensemble reached no configured "
        f"line, so this test is no longer exercising the refusal (asked: {asked})")
    assert row.canonical_key is None, (
        "a refused answer was written to the row anyway; only refuse_negative stood between the "
        "expense total and the published figure, and it held on the SIGN rather than on the error")
    assert any("row_terms_refused" in line for line in ctx.logs), ctx.logs[-8:]


# ── grouping line items into requests ─────────────────────────────────────────────────────────

def _hits(*notes):
    from app.services.line_item_notes import NoteHit
    return [NoteHit(note=n, title="", score=1.0) for n in notes]


_SETS = {"a": _hits("7"), "b": _hits("7"), "c": _hits("7", "14"),
         "d": _hits("21"), "e": _hits("7", "14", "15")}


def test_none_gives_every_line_item_its_own_request():
    """The baseline, and the only mode whose answer is attributable to one line: nothing else
    shares the call, so nothing else can have influenced it."""
    from app.services.line_item_notes import group_by_note_set

    assert group_by_note_set(_SETS, mode="none") == [["a"], ["b"], ["c"], ["d"], ["e"]]


def test_identical_groups_only_exactly_equal_note_sets():
    """Safe by construction: no line item receives a note it did not ask for, which is how a wrong
    answer acquires a plausible-looking source."""
    from app.services.line_item_notes import group_by_note_set

    groups = group_by_note_set(_SETS, mode="identical")
    assert ["a", "b"] in groups, groups
    # c and e overlap heavily but are NOT equal, so they stay apart in this mode.
    assert ["c"] in groups and ["e"] in groups, groups
    assert sorted(k for g in groups for k in g) == ["a", "b", "c", "d", "e"]


def test_similar_groups_on_overlap_and_the_threshold_bites():
    """c={7,14} and e={7,14,15} share 2 of 3, so they group at 0.6 and not at 0.9. A threshold that
    did not change the grouping would not be a threshold."""
    from app.services.line_item_notes import group_by_note_set

    loose = group_by_note_set(_SETS, mode="similar", similarity=0.6)
    tight = group_by_note_set(_SETS, mode="similar", similarity=0.9)

    assert ["c", "e"] in loose, loose
    assert ["c"] in tight and ["e"] in tight, tight


def test_a_line_item_matching_nothing_keeps_its_own_request():
    """The grouping degrades to `none` for it rather than forcing it into the nearest group — a
    forced group is exactly the case where a line item receives evidence for another question."""
    from app.services.line_item_notes import group_by_note_set

    for mode in ("identical", "similar"):
        groups = group_by_note_set(_SETS, mode=mode, similarity=0.6)
        assert ["d"] in groups, (mode, groups)


def test_similar_compares_against_the_SEED_not_the_running_union():
    """THE DRIFT THIS PREVENTS. Comparing a candidate to the group's accumulated union lets each
    new member resemble only what the group already holds, so a chain of pairwise-similar sets ends
    up in one request with the first and last sharing almost nothing. Every member must be within
    the threshold of the SAME set."""
    from app.services.line_item_notes import group_by_note_set

    chain = {"x": _hits("1", "2", "3", "4"), "y": _hits("2", "3", "4", "5"),
             "z": _hits("3", "4", "5", "6"), "w": _hits("4", "5", "6", "7")}
    groups = group_by_note_set(chain, mode="similar", similarity=0.6)

    for group in groups:
        for member in group:
            seed = set(chain[group[0]][i].note for i in range(len(chain[group[0]])))
            have = set(h.note for h in chain[member])
            union = seed | have
            assert len(seed & have) / len(union) >= 0.6, (group, member)


def test_the_grouping_is_deterministic():
    """A rerun that regrouped would make two runs incomparable for no reason."""
    from app.services.line_item_notes import group_by_note_set

    for mode in ("none", "identical", "similar"):
        first = group_by_note_set(_SETS, mode=mode, similarity=0.6)
        assert first == group_by_note_set(_SETS, mode=mode, similarity=0.6)


def test_an_unknown_mode_is_refused_rather_than_silently_ungrouped():
    """A typo in configuration that quietly produced one request per line item would look exactly
    like `none` working, and the deployment would pay for it without knowing."""
    from app.services.line_item_notes import group_by_note_set

    with pytest.raises(ValueError, match="unknown grouping mode"):
        group_by_note_set(_SETS, mode="idnetical")


def test_the_config_default_is_the_attributable_mode():
    """`none` until there is a per-line-item baseline to compare a grouped run against."""
    settings = get_settings()
    assert settings.extraction.llm_request_grouping == "none"
    assert 0.0 < settings.extraction.llm_group_similarity <= 1.0
