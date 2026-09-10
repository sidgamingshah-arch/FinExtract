"""`row_terms` as a floor on the model's answer: does the caption name this line's ROW at all?

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
    parts = [i for i in shipped.items if getattr(i, "parent", "")]
    assert len(parts) >= 77
    missing = [i.key for i in parts
               if not any(han.search(str(t))
                          for t in (getattr(i.note_source, "row_terms", None) or ()))]
    assert not missing, f"these parts carry no Han row terms: {missing[:6]}"


# ── the floor applied to a real mapping decision ──────────────────────────────────────────────

def test_the_stage_refuses_the_answer_rather_than_writing_it(shipped):
    """END TO END, on the row that caused it: the model names the part for the expense total, and
    the row is left unmapped instead of feeding rung P1."""
    from app.services.mapping import LlmBatchDecision

    class Insists:
        """A provider that makes exactly the mistake the live run made."""

        id = "insists"

        def complete_structured(self, *, system, messages, response_schema, **_):
            payload = json.loads(messages[-1]["content"])
            if response_schema is LlmBatchDecision:
                return response_schema.model_validate({"mappings": [
                    {"item_id": item["item_id"], "canonical_key": PART, "confidence": 0.9,
                     "reason": "matched on the words operating expenses"}
                    for item in payload["source_items"]]}), {}
            return response_schema.model_validate(
                {"canonical_key": PART, "confidence": 0.9}), {}

    row = LineItem(source_label="Other operating expenses", role=LineRole.LINE)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("-1026959"), value_raw=Decimal("-1026959"),
                                 provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    doc.line_items = [row]

    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.settings.extraction.llm_mapping = True
    ctx.registry.register("llm", "insists", lambda: Insists())
    ctx.settings.llm.provider = "insists"
    MapOntologyStage().run(doc, ctx)

    assert row.canonical_key != PART, (
        "the expense total was written onto a depreciation component; only refuse_negative stood "
        "between that and the published figure, and it held on the SIGN rather than on the error")
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
