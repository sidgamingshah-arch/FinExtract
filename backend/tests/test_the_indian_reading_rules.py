"""THE IND AS SET READS AN INDIAN FILING'S PUNCTUATION, NOT AN HKEX FILING'S.

NEW FILE -> backend/tests/test_the_indian_reading_rules.py

`normalisation` and `scope_selection` are the two blocks of a rulebook that govern EVERY page
rather than any concept, and in the Ind AS set both were the Hong Kong set's, verbatim. They are
EXECUTED, which is what makes that a defect rather than untidiness:

  * `row_reconstruct._pipeline_steps` matches each `normalisation.pipeline` sentence to a step by
    keyword and then harvests the QUOTED LITERALS out of that sentence (`_quoted_examples`) as the
    strings the step strips. The literals in force were "RMB'000", '人民幣千元', '(附注12)'.
  * `scope_selection.units_and_currency.signals` feeds the same annotation step and unit
    resolution; `entity_scope.signals` decides which column is the Group's.

WHAT AN INDIAN FILING PRINTS, measured on Asian Paints' Integrated Annual Report 2025-26:

    (₹ in Crores)     the balance sheet's scale annotation
    (` in Crores)     the SAME annotation on the facing page — the embedded font lacks the ₹ glyph
                      so the PDF substitutes a grave accent, and BOTH forms occur in one document
    (Refer note 36)   how it points at its own notes
    As at 31.03.2026  balance sheet column headers; the financial year ends 31 March
    Standalone / Consolidated   the two entity columns, where HKEX prints Company / Group

The grave accent is the one that looks like a typo and is not. A reader who "corrects" it to ₹
removes the only form that matches half the pages of the filing.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.row_reconstruct import _pipeline_steps, apply_pipeline

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
INDAS = TEMPLATES / "output_csv_indas_line_items.json"


@pytest.fixture(scope="module")
def indas():
    return load_line_item_set(json.loads(INDAS.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def steps(indas):
    return _pipeline_steps(indas.normalisation, indas.scope_selection)


def _fold(steps, text: str) -> str:
    return apply_pipeline(text, steps)


def test_every_declared_step_has_an_implementation(steps, indas):
    """A declared step no implementation recognises is skipped rather than guessed at, so a
    sentence nothing matches is a line of specification that governs nothing. Every sentence of
    this set's pipeline must reach a step."""
    assert len(steps) == len(indas.normalisation.pipeline), (
        [s for s, _ in steps], indas.normalisation.pipeline)


def test_the_traditional_to_simplified_fold_is_gone(steps):
    """The one declared step with nothing to do on an English filing. Keeping it would leave a
    line of the specification reading as though it governed something."""
    assert "t2s" not in {s for s, _ in steps}


@pytest.mark.parametrize("printed, expected", [
    ("Changes on account of amalgamation (Refer note 36)", "changes on account of amalgamation"),
    ("Investments in Subsidiaries and Associates (Refer Note 2A)",
     "investments in subsidiaries and associates"),
    ("Other Equity (Refer to note 13)", "other equity"),
    # The bare form an HKEX filing prints still strips — this widened the marker, it did not move it.
    ("Deferred tax credited for the year (note 32)", "deferred tax credited for the year"),
])
def test_the_indian_note_citation_is_stripped(steps, printed, expected):
    """"(Refer note 36)" is how a Schedule III statement points at its notes. Left in place the
    caption matches no alias in any rulebook, while the same caption without it matches."""
    assert _fold(steps, printed) == expected


def test_refer_and_note_alone_strip_nothing(steps):
    """THE CONTROL. The marker still requires digits, so the ordinary English words survive — a
    caption is not truncated because it happens to contain "note" or "refer"."""
    assert "refer" in _fold(steps, "Refer to the accompanying policies")
    assert "notes payable" in _fold(steps, "Interest on notes payable")


@pytest.mark.parametrize("printed", [
    "Inventories (₹ in Crores)",
    "Inventories (` in Crores)",
    "Inventories (` in crores)",
    "Inventories (₹ in Lakhs)",
    "Inventories Rs. in crores",
])
def test_the_rupee_scale_annotation_is_stripped(steps, printed):
    """BOTH THE SYMBOL AND ITS GRAVE-ACCENT STAND-IN. An Indian PDF whose embedded font lacks the
    ₹ glyph substitutes a backtick, and Asian Paints prints "(₹ in Crores)" on its balance sheet
    and "(` in Crores)" on the facing profit and loss — one document, both forms."""
    assert _fold(steps, printed) == "inventories"


def test_the_unit_signals_are_indian(indas):
    """`units_and_currency.signals` is read by unit resolution as well as by the annotation step,
    so an Indian filing's scales have to be declared and not merely handled downstream."""
    signals = " ".join(indas.scope_selection.units_and_currency.signals).lower()
    for token in ("crore", "lakh"):
        assert token in signals, (token, signals)
    assert "rmb" not in signals and "hk$" not in signals, signals


def test_the_entity_signals_name_standalone_and_consolidated(indas):
    """An Indian annual report presents the STANDALONE and the CONSOLIDATED statements in full, one
    after the other, so the entity is resolved for two complete sets rather than two columns of
    one. `row_reconstruct._COMPANY_WORDS` already matches `standalone`; declaring it is what lets a
    reviewer look the rule up where `binding.order` says it lives."""
    signals = {s.lower() for s in indas.scope_selection.entity_scope.signals}
    assert {"standalone", "consolidated"} <= signals, sorted(signals)
    assert indas.scope_selection.entity_scope.default == "consolidated"


def test_the_company_only_marker_names_a_key_this_set_has(indas):
    """The HK set's marker cited `bs_non_current_assets__investments_in_subsidiaries`, a key from
    another namespace that appears nowhere in this set — so the rule named nothing a reader could
    check it against."""
    markers = " ".join(indas.scope_selection.entity_scope.company_only_markers)
    assert "bs_nca__investment_in_subsidiaries" in markers, markers
    assert {i.key for i in indas.items} >= {"bs_nca__investment_in_subsidiaries"}


def test_the_note_column_rule_is_stated_in_a_field_the_schema_declares(indas):
    """WHERE A RULE IS WRITTEN DECIDES WHETHER IT SHIPS AT ALL.

    `Normalisation` declares exactly `note`, `pipeline` and `wrapped_caption_rule`, and
    `sample.reference.ensure_reference_data` REFUSES a pair carrying any key the schema does not,
    because an undeclared key "would be dropped in silence". A first attempt put this rule in
    `normalisation.note_column_rule` and the whole Ind AS pair stopped seeding — the set was simply
    absent from the database and from the configuration screen.
    """
    rule = indas.normalisation.wrapped_caption_rule.lower()
    assert "note" in rule and "not a figure" in rule, rule
    raw = json.loads(INDAS.read_text(encoding="utf-8"))
    assert set(raw["normalisation"]) <= {"note", "pipeline", "wrapped_caption_rule"}, (
        sorted(raw["normalisation"]))


def test_the_wrapped_caption_rule_is_about_schedule_iii(indas):
    """Schedule III prints a mandatory Note No. column between the caption and the figures, and its
    captions are long enough to wrap in the width that leaves — "Total Outstanding dues of
    creditors other than Micro Enterprises" / "and Small Enterprises" is one caption on two lines
    and matches neither half."""
    rule = indas.normalisation.wrapped_caption_rule
    assert "Schedule III" in rule, rule
    assert "hkex" not in rule.lower(), rule
