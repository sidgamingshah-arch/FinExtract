"""THE WHOLE-PDF QUESTION SENDS EACH LINE'S WHOLE DEFINITION, and says in words what the line's
`exclude_hints` rule out (`services.whole_pdf.definition_of`, `not_this_line`, `hint_caption`).

It cut every definition at 240 characters, and 452 of the 519 lines the HK set asked have longer
ones — so the sentences recent fixes wrote for the model were never sent: Sales' CAS rule (only
主营业务收入; 营业收入 and 营业总收入 are not this line), the contingent-liability parts' one-currency
rule, Level 3's "a total row that prints that column blank or nil reads 0" and its exclusions
(findings question_carries_only_240_chars, whole_pdf_definitions_cut_at_240,
whole_pdf_definitions_truncated, prompt_truncated_selectors_absent). The rules route vetoes the
captions an `exclude_hints` regex names; the model was never told them.

What is measured here is the question's TEXT, built from the shipped HK set. No model is called.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W
from app.services.line_item_llm import estimated_tokens

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def hk():
    return load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


def _questions(item_set):
    """Every request's question for a two-part filing, as the stage builds it."""
    chunks = W.chunk_lines(W.lines_to_ask(item_set), 250, item_set)
    part = W.Part(pages=list(range(100)), pdf=b"")
    return chunks, [W.question(c, part, 2, 2, item_set, carried=17) for c in chunks]


def _whole(item) -> str:
    return " ".join((item.definition or "").split())


def test_every_asked_line_is_sent_with_its_whole_definition(hk):
    chunks, questions = _questions(hk)
    cut = 0
    for chunk, text in zip(chunks, questions, strict=True):
        for item in chunk:
            assert _whole(item) in text, item.key
            cut += len(_whole(item)) > 240
    assert cut > 400            # the lines that lost their tail before: 452 of 519 at HEAD


@pytest.mark.parametrize("key,sentence", [
    # 346a9a2's CAS rule for the face's revenue part.
    ("sub__face_principal_revenue", "营业收入 and 营业总收入"),
    # 8aec37f's blank Level 3 column and the rows part's exclusions.
    ("sub__fa_cp_level_3_printed_total", "blank or nil reads 0"),
    ("sub__fa_cp_level_3_rows", "Level 3 reconciliation"),
    # The one-currency rule on the contingent-liability note part.
    ("sub__cl_contingency_note_exposures", "another currency"),
])
def test_the_sentences_past_240_characters_reach_the_model(hk, key, sentence):
    by_key = {item.key: item for item in hk.items}
    whole = _whole(by_key[key])
    assert sentence in whole and whole.index(sentence) > 240, "the sentence was past the cut"
    chunks, questions = _questions(hk)
    text = next(q for c, q in zip(chunks, questions, strict=True) if key in {i.key for i in c})
    row = next(line for line in text.splitlines() if line.startswith(f"- {key}:"))
    assert sentence in row and not row.endswith("…")


@pytest.mark.parametrize("hint,read", [
    (r"^Land\s+use\s+rights$", [("is", "Land use rights")]),
    (r"^Trade\s+Receivables\(Gross\)$", [("is", "Trade Receivables(Gross)")]),
    (r"^(?:资产净值|净资产)", [("begins", "资产净值"), ("begins", "净资产")]),
    (r"^\s*total\b", [("begins", "total")]),
    (r"interest", [("contains", "interest")]),
    (r"营业收入", [("contains", "营业收入")]),
    # Not guessed at: an optional group, a character class, a wildcard.
    (r"^net current\s*\(?\s*(?:assets|liabilities)", []),
    (r"^[^a-z]*[营營][业業]利[润潤][^a-z]*$", []),
    (r"net cash .*operating activities", []),
])
def test_an_exclude_hint_is_read_as_captions_or_not_at_all(hint, read):
    assert W.hint_caption(hint) == read


def test_what_the_hints_rule_out_is_said_where_the_definition_does_not_say_it(hk):
    by_key = {item.key: item for item in hk.items}
    buildings = W.definition_of(by_key["bs_nca__buildings"])
    assert "Not this line: a row captioned “其他无形资产”" in buildings
    # A caption the definition already names is not said again: Sales' face part names 营业收入
    # and 营业总收入 in its own words.
    face = by_key["sub__face_principal_revenue"]
    assert "营业收入" in _whole(face) and W.not_this_line(face, _whole(face)) == ""
    # Every line whose hints add something ends its definition with the sentence, once.
    added = [i for i in W.lines_to_ask(hk) if W.not_this_line(i, _whole(i))]
    assert added and all(W.definition_of(i).count("Not this line:") >= 1 for i in added)
    assert "Not this line" in W.SYSTEM


def test_the_whole_definitions_stay_within_the_providers_limits(hk):
    """The largest question (the balance sheet's, 208 lines) is about 200,000 characters, an
    estimated 52,000 tokens; with a 100-page part (about 160,000 page-image and 60,000 text
    tokens) that is far inside a one-million-token window, and the request's size budget is
    grown to the question's own size, so a part is cut to leave it room (`Plan.overhead_bytes`)."""
    _chunks, questions = _questions(hk)
    widest = max(questions, key=len)
    assert 150_000 < len(widest) < 300_000
    assert estimated_tokens(widest) + estimated_tokens(W.SYSTEM) < 100_000
    for text in questions:
        assert len(text.encode("utf-8")) + len(W.SYSTEM.encode("utf-8")) < 1024 * 1024
