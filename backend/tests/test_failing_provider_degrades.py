"""A configured provider whose calls all fail must degrade TO the deterministic path, not below it.

THE DEFECT, measured on laisun.pdf. With `llm_mapping = false` the run produced Sales(Revenues)
4,995,768 and Secur & Other Fincl Assets (CP) 174,822, both read off the face by the caption tier.
With a provider configured whose every call was refused — the free tier's 8,000 tokens-per-minute
limit against requests of roughly 40,000, so `llm_calls` was 0 and the model never answered anything
— the SAME filing produced 2,609,259 and empty. Two figures were lost to a model that had not spoken.

WHERE IT WAS. The focus-routing row gate in `stages.map_ontology` decides each row deterministically
first, then forwards to the model any row that resolved to a focus concept (to confirm or correct
it) or to nothing. It computed that deterministic answer and then dropped it. So a forwarded row
whose batch was refused ended up with no concept at all — strictly worse than never having
configured a provider, because the caption tier's answer had already been found and thrown away.

WHY THE REST OF THE SUITE COULD NOT SEE IT. Every other check either runs with no provider (where
the row gate is not reached) or with a provider that ANSWERS (where the model's answer legitimately
wins). The failure mode needs a provider that is present and useless, which is exactly what a
rate-limited free tier is — the most likely production configuration of the three.

These tests use a stub that only ever raises, so nothing here depends on a network, a key, or a
model's judgement: the defect is the DISCARD, and the discard is deterministic.
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
from app.services.working_view import build_working_view
from app.stages.map_ontology import MapOntologyStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


class Useless:
    """Present, constructs fine, and refuses every call — a rate-limited free tier."""

    id = "useless"

    def __init__(self) -> None:
        self.calls = 0

    def complete_structured(self, **_):
        self.calls += 1
        raise RuntimeError(
            "Gateway returned 413: Request too large ... tokens per minute (TPM): Limit 8000, "
            "Requested 40237")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _row(label: str, amount: str, page: int = 0) -> LineItem:
    li = LineItem(source_label=label, role=LineRole.LINE, section_hint=None)
    li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                value=Decimal(amount), value_raw=Decimal(amount),
                                provenance=Provenance(page_index=page)))
    return li


def _run(shipped, rows, *, llm: bool, statement: str = "profit_and_loss"):
    """One mapping stage over `rows`, with the provider either absent or present-and-useless."""
    # The statement reaches the stage through PAGE CLASSIFICATION, not through a row attribute.
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement=statement)]
    doc.line_items = list(rows)

    provider = Useless()
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.settings.extraction.llm_mapping = llm
    if llm:
        ctx.registry.register("llm", "useless", lambda: provider)
        ctx.settings.llm.provider = "useless"
    else:
        ctx.settings.llm.provider = "stub"
    MapOntologyStage().run(doc, ctx)
    return doc, ctx, provider


def _keys(doc):
    return {li.canonical_key for li in doc.line_items if li.canonical_key}


def _focus_captions(shipped):
    """Every (focus key, caption, statement) the caption tier ACTUALLY resolves — discovered, not
    assumed.

    THE TESTS ARE WORTHLESS WITHOUT THIS. The row gate only forwards a row whose deterministic
    answer IS a focus key, so a caption that resolves to nothing never reaches the code path under
    test, and the "did we lose a concept" comparison would be empty-versus-empty. A first version of
    this file took the first declared alias of each focus key and passed vacuously for exactly that
    reason — two of its five checks skipped, and the one that mattered compared nothing to nothing.

    Measured on the shipped configuration: 4 of the 8 focus keys have a caption resolving to them at
    EXACT/1.0 — Sales(Revenues), Secur & Other Fincl Assets(CP), Due from Related Parties(LTP) and
    Contingent liabilities.
    """
    from app.services.mapping import OntologyMatcher

    view = build_working_view(shipped)
    matcher = OntologyMatcher(view, locale="en", settings=get_settings())
    focus = set(get_settings().extraction.llm_focus_keys or ())
    out = []
    for mapping in view.mappings:
        if mapping.canonical_key not in focus:
            continue
        for alias in (mapping.aliases_for("en") or []):
            for statement in ("profit_and_loss", "balance_sheet", "notes"):
                result = matcher.match(alias, statement=statement, section=None)
                if result and result.canonical_key == mapping.canonical_key:
                    out.append((mapping.canonical_key, alias, statement))
                    break
            else:
                continue
            break
    return out


def test_the_configuration_really_does_route_by_focus_keys():
    """The premise. With focus routing off, no row is forwarded to confirm-or-correct and the
    discard this file is about cannot happen."""
    settings = get_settings()
    assert getattr(settings.extraction, "llm_focus_only", False) is True
    assert len(settings.extraction.llm_focus_keys or ()) >= 1


def test_the_probe_captions_really_do_resolve_deterministically(shipped):
    """Asserted so nothing below can pass vacuously — see `_focus_captions`."""
    pairs = _focus_captions(shipped)
    assert len(pairs) >= 4, pairs


def test_a_failing_provider_degrades_to_the_deterministic_path_not_below_it(shipped):
    """THE REGRESSION ITSELF. Each caption mapped twice — once with no provider, once with a
    provider that refuses every call. The second must not lose a concept the first found."""
    for key, alias, statement in _focus_captions(shipped):
        doc_off, _ctx, _p = _run(shipped, [_row(alias, "4995768")], llm=False,
                                 statement=statement)
        doc_on, ctx_on, provider = _run(shipped, [_row(alias, "4995768")], llm=True,
                                        statement=statement)

        assert key in _keys(doc_off), f"{alias!r} did not resolve to {key} even deterministically"
        assert provider.calls >= 1, f"{alias!r} was never forwarded, so nothing was at risk"
        assert ctx_on.llm_calls in (0, None), "the stub cannot have produced a successful call"

        lost = _keys(doc_off) - _keys(doc_on)
        assert not lost, (
            f"{alias!r}: a provider that answered NOTHING cost the run {sorted(lost)}. A "
            f"configured-but-failing provider must degrade to the deterministic path, never below "
            f"it — the caption tier's answer was already computed and must not be discarded.")


def test_the_recovered_row_keeps_the_deterministic_figure(shipped):
    """Not merely "a concept is present": the FIGURE must be the one the caption tier read, since a
    recovered mapping carrying the wrong number is not a recovery."""
    def figure(doc, key):
        for li in doc.line_items:
            if li.canonical_key == key:
                for ev in li.values.values():
                    if ev.value is not None:
                        return ev.value
        return None

    for key, alias, statement in _focus_captions(shipped):
        doc_off, _c1, _p1 = _run(shipped, [_row(alias, "4995768")], llm=False,
                                 statement=statement)
        doc_on, _c2, _p2 = _run(shipped, [_row(alias, "4995768")], llm=True,
                                statement=statement)
        assert figure(doc_off, key) is not None, alias
        assert figure(doc_on, key) == figure(doc_off, key), alias


def test_the_fallback_is_reported_rather_than_silent(shipped):
    """A row that was paid for and came back unanswered carries the LEXICAL answer, not a judged
    one. That is a real quality difference, so the run says it happened instead of quietly looking
    like a successful mapping."""
    key, alias, statement = _focus_captions(shipped)[0]

    doc_off, _ctx, _p = _run(shipped, [_row(alias, "4995768")], llm=False, statement=statement)
    assert key in _keys(doc_off), alias

    _doc, ctx, _provider = _run(shipped, [_row(alias, "4995768")], llm=True, statement=statement)
    assert any("deterministic_fallback_applied" in line for line in ctx.logs), ctx.logs[-8:]


def test_a_model_that_does_answer_still_wins(shipped):
    """The other half, so the fallback cannot become "the model is ignored". Asking exists to
    confirm or CORRECT the deterministic reading, so a real answer must still take precedence — the
    fallback applies only to a row left with no concept at all."""
    from app.services.mapping import LlmBatchDecision

    key, alias, statement = _focus_captions(shipped)[0]
    target = "bs_nca__land"
    if target not in {i.key for i in shipped.items}:
        pytest.skip("no distinct concept available to answer with")

    class Answers:
        id = "answers"

        def complete_structured(self, *, system, messages, response_schema, **_):
            payload = json.loads(messages[-1]["content"])
            if response_schema is LlmBatchDecision:
                return response_schema.model_validate({"mappings": [
                    {"item_id": item["item_id"], "canonical_key": target, "confidence": 0.95,
                     "reason": "answered",
                     "sources": [{"note": "7", "caption": "x"}]}
                    for item in payload["source_items"]]}), {}
            return response_schema.model_validate(
                {"canonical_key": target, "confidence": 0.95}), {}

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement=statement)]
    doc.line_items = [_row(alias, "4995768")]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    ctx.settings.extraction.llm_mapping = True
    ctx.registry.register("llm", "answers", lambda: Answers())
    ctx.settings.llm.provider = "answers"
    MapOntologyStage().run(doc, ctx)

    # The model answered every forwarded row, so the fallback must not have fired — otherwise it
    # would be overwriting judged answers with lexical ones.
    assert not any("deterministic_fallback_applied" in line for line in ctx.logs), (
        "the deterministic fallback fired even though the model answered the row")


# ── what the focus log claims about a run's reach ─────────────────────────────────────────────

def test_the_focus_log_measures_what_the_MODEL_can_name(shipped):
    """A NUMBER THAT LIED IN THE DIRECTION THAT MATTERS.

    `_focus_answerability` reports how many configured focus concepts a run could actually come
    back with. It measured `_unmatchable`, which was the whole story until the extract-only rule
    landed — after which `_concept_payload` withholds `_llm_withheld`, a strictly larger set. On the
    shipped configuration `_unmatchable` catches 4 of the 8 focus keys, so the line reported
    "answerable=4" on a run where exactly ONE (`is_pl__sales_revenues`) could be named.

    That is not cosmetic. A live 45-call run against gemini-flash-lite-latest returned no change to
    any of the eight figures, and one nameable concept out of eight is precisely what predicts
    that — while the log had advertised four times the reach. Both counts are now reported, because
    "no tier can bind it" and "the model is not offered it" are different facts.
    """
    from app.services.mapping import OntologyMatcher
    from app.stages.map_ontology import _focus_answerability

    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    focus = set(get_settings().extraction.llm_focus_keys or ())
    line = _focus_answerability(matcher, focus)

    nameable = {k for k in focus if k not in matcher._llm_withheld}
    assert f"focus_keys_nameable_by_model={len(nameable)}" in line, line
    # The claim must be about the model's own gate, so it moves when that gate moves.
    assert "focus_keys_withheld_from_model=" in line
    assert "focus_keys_unmatchable_by_any_tier=" in line
    # And it must not overstate: every key it calls nameable really is on the offer.
    offered = {c["canonical_key"]
               for c in matcher._concept_payload(matcher._by_priority(list(matcher._by_key)))}
    assert nameable <= offered | {k for k in nameable if k not in matcher._by_key}, sorted(nameable)


def test_the_focus_list_names_the_parts_and_not_only_the_wholes(shipped):
    """THE NUMBER THIS TEST EXISTS TO MAKE SOMEONE JUSTIFY, and it has now moved once.

    It used to assert the reach was exactly `["is_pl__sales_revenues"]` — 1 nameable concept out of
    8 configured — which was the measured consequence of the extract-only rule: seven of the eight
    focus concepts are `derived` or `extract_or_derive`, so the model is not offered them, and
    forwarding a row whose deterministic answer is one of those spends a call on a concept the run
    cannot come back with. A live 45-call run changed none of the eight figures, which is exactly
    what 1-of-8 predicts.

    `config.toml` now names the 77 PARTS as well as the 8 wholes. A part is `extract`, carries no
    alias, and is the layer a note actually prints, so it is what a call can usefully answer. The
    reach is 77 of 85, and the 8 that remain unreachable are precisely the wholes — which is
    correct, because a whole's figure is computed by its cascade and is not the model's to give.

    AND THAT COUNT MOVED AGAIN, from 78 to 77, which is the hole this number was always meant to
    expose. `is_pl__sales_revenues` is a `derived` whole with an eight-rung cascade AND
    `extraction_mode: extract`, because a filing that prints the subtotal must have the printed row
    read — so a rule keyed on the mode alone OFFERED it, and it was the one whole the model could
    name. It is now withheld by its TYPE (`_computed_parent`), so the reach is the 77 parts and
    nothing else: every whole withheld, every part offered, with no case left where the two
    declarations disagree.
    """
    from app.services.mapping import OntologyMatcher

    matcher = OntologyMatcher(build_working_view(shipped), locale="en", settings=get_settings())
    focus = set(get_settings().extraction.llm_focus_keys or ())
    withheld = sorted(k for k in focus if k in matcher._llm_withheld)
    nameable = [k for k in focus if k not in matcher._llm_withheld]

    assert len(focus) == 85, len(focus)
    assert len(nameable) == 77, len(nameable)
    # THE UNREACHABLE ONES ARE THE WHOLES, every one of them — a part that turned up in this list
    # would mean the layer meant to be answerable had been withheld.
    by_key = {i.key: i for i in shipped.items}
    assert all(not getattr(by_key[k], "parent", "") for k in withheld), withheld
    assert len(withheld) == 8, withheld
    # …and the nameable ones are the parts, every one of them. The complement of the assertion
    # above, and what pins "77" to a fact about the configuration rather than to a tally.
    assert all(getattr(by_key[k], "parent", "") for k in nameable)
