"""Why nothing was structurally verified on any filing, and what it cost.

`stages/structural` reported `evaluated=0 failed=0 not_evaluable=61` on both HK filings and
`structural:alarm code=UNVALIDATED statement=balance_sheet skipped=32` — a run that checked
nothing and said so only in an alarm nobody had to read. Two independent reasons, both in how the
rules reach the stage rather than in the arithmetic:

WHAT A RESIDUAL IS CALLED. A relation is normally skipped when a component was not extracted,
because a figure nobody read is not worth asserting against; the exception is a section whose
residual bucket has a home for every printed row, where a still-absent child is a line the filing
does not print. `_nil_when_absent` found those sections by the key suffix `__others` — the hkfrs
rulebook's convention. The output-CSV rulebook that drives the product names its eleven residuals
after what they hold (`bs_ca__other_current_assets`, `bs_equity__other_reserves`), so the set came
back EMPTY, every absent component counted as unknown, and every section rollup was skipped.

WHERE THE SECTION RULE IS WRITTEN. `section_relations` reads one sentence out of the validation
master. `api/routes/extractions` hands the stage `build_working_view(line_item_set)`, which carries
`residual_framework` and no `validation` at all — and `output_csv_hk_validation.json` ships empty
besides (`section_reconciliation: ""`, 0 identities, 0 guards). So not one section_reconciliation
relation was emitted on any of the five filings: every result was a template rollup, and the check
designed for exactly this case never ran.

WHAT IT COST, measured: a related-party receivable republished from the current into the
non-current section overstated China SCE's non-current assets by 3,339,974 and nothing reported
it. With the section reconciliation running, `bs_nca` ties to 2,486 on 49,227,234 — well inside a
per-row tolerance over 51 rows, and nowhere near 3,339,974.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.structural_checks import (_nil_when_absent, _residual_namespaces,
                                            section_relations)
from app.services.working_view import build_working_view
from app.schemas.loader import load_template

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "output_csv_hk_v1_template.json")
                                    .read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def working_view():
    """The object a real run hands the stage — see `api/routes/extractions`."""
    cfg = load_line_item_set(json.loads((_SAMPLES / "output_csv_hk_line_items.json")
                                        .read_text(encoding="utf-8")), resolve=True)
    return build_working_view(cfg)


# --- a residual is a declaration, not a spelling -------------------------------------------------

def test_the_shipped_rulebook_names_no_residual_others(template, working_view):
    """The premise. Not one of this template's 480 keys ends in `__others`, and eleven concepts
    declare `value_scope: exclusive_residual` instead."""
    assert not [k for k in template.all_canonical_keys() if k.endswith("__others")]
    declared = [m.canonical_key for m in working_view.mappings
                if getattr(m, "value_scope", None) == "exclusive_residual"]
    assert len(declared) == 11, declared


def test_the_swept_namespaces_are_read_from_that_declaration(template, working_view):
    swept = _residual_namespaces(template, working_view)

    assert {"bs_ca", "bs_nca", "bs_cl", "bs_ncl", "bs_equity", "is_pl"} <= swept
    # …and without the rulebook there is nothing to read, which is what the suffix test saw.
    assert _residual_namespaces(template, None) == set()


def test_a_leaf_of_a_swept_section_is_nil_when_absent(template, working_view):
    """What that unlocks: the section rollups have an answer for a line the filing does not
    print, so they can be evaluated at all."""
    nil = _nil_when_absent(template, working_view)

    assert len(nil) == 325, len(nil)
    for leaf in ("bs_nca__land", "bs_ca__raw_materials",
                 "bs_ca__due_from_related_parties_cp", "bs_cl__trade_financing"):
        assert nil.get(leaf) == "balance_sheet", leaf
    assert not _nil_when_absent(template, None)


def test_a_subtotal_is_never_nil_however_absent_its_own_children_are(template, working_view):
    """THE INVARIANT THAT SURVIVED AN ATTEMPT TO WIDEN IT. A recursive rule — a subtotal is nil
    when everything beneath it is missing and nil — looks right and is wrong: the section's money
    is not missing when its subtotal is, it was SWEPT INTO THE RESIDUAL, which is a component of
    the same relation. Zeroing the subtotal discounts it twice.

    Measured when it was widened: 20 of 26 failures on China SCE had assumed a calculated node was
    zero, among them `is_pl__profit_for_the_year`, whose check compared a printed -8,401,124
    against the -410,074 of minority interests alone.
    """
    nil = _nil_when_absent(template, working_view)

    for subtotal in ("bs_ca__net_trade_receivables", "bs_nca__net_fixed_assets",
                     "bs_ca__inventories", "is_pl__profit_loss_bef_extraord_items"):
        assert subtotal not in nil, subtotal


# --- and the section rule reaches the run --------------------------------------------------------

def test_the_section_reconciliation_runs_on_the_object_a_run_is_given(template, working_view):
    """The whole point: the stage is handed a working view, which has no `validation` block, and
    the rule is stated in the `residual_framework` the view does carry."""
    assert getattr(working_view, "validation", None) is None
    assert working_view.residual_framework.reconciliation.on_failure

    rels = section_relations(template, working_view)

    assert rels, "not one section reconciliation was emitted for a real run's ontology"
    by_section = {r.extra["section"]: r for r in rels}
    assert {"bs_nca", "bs_ca", "bs_cl", "bs_ncl", "bs_equity"} <= set(by_section)
    one = by_section["bs_nca"]
    assert one.kind == "section_reconciliation" and one.severity == "blocking"
    # The framework names the fact to emit and the consequence, exactly as the sentence does.
    assert one.extra["emits"] == "unallocated_gap"
    assert one.extra["blocks_auto_approval"] is True
    # `one rounding unit per contributing row`, read from the same block.
    assert one.tol_per_row is True


def test_an_empty_sentence_is_still_a_kill_switch(template):
    """The fallback must not take the switch away. A validation master that EXISTS and leaves the
    field empty has turned the check off deliberately — `test_validation_block` holds that — so
    the framework is read only where there is no master in the picture at all.
    """
    raw = json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    from app.schemas.loader import load_ontology

    hkfrs = load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json")
                                     .read_text(encoding="utf-8")))
    live = load_ontology(raw, resolve=True)
    assert section_relations(hkfrs, live), "the shipped sentence should emit the check"

    silenced = copy.deepcopy(raw)
    silenced["validation"]["section_reconciliation"] = ""
    assert section_relations(hkfrs, load_ontology(silenced, resolve=True)) == []
