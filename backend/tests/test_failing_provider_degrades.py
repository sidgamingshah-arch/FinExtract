"""THE FOCUS LIST IS A LIST OF LINE ITEMS, and it has to name the ones a run can answer.

WHAT THIS FILE USED TO BE ABOUT, and why almost none of it survives. `extraction.llm_focus_keys`
named concepts whose printed ROWS were forwarded to a model while every other row kept its
deterministic answer. That gate had a measured defect: it computed each forwarded row's
deterministic answer and then DISCARDED it, so a row whose batch the provider refused ended up
with no concept at all — strictly worse than never configuring a provider. On laisun.pdf, with
every call refused by a free tier's 8,000-tokens-per-minute limit against ~40,000-token requests,
Sales(Revenues) went 4,995,768 -> 2,609,259 and Secur & Other Fincl Assets (CP) 174,822 -> empty.
Two figures lost to a model that had not spoken.

THAT DEFECT CANNOT RECUR, because the discard cannot: rows are not forwarded to a model at all.
A run's requests are about LINE ITEMS and they run in their own stage BEFORE the deterministic
`note_sourced` reader, so a request that fails has written nothing and the declared route then
fills the line exactly as it would with no provider configured. That property is asserted where it
now lives — `tests/test_line_item_requests_run.py::
test_a_request_that_fails_leaves_its_lines_to_the_deterministic_route`.

WHAT REMAINS HERE is the other half of the original file, and it is unchanged in meaning: the
shipped focus list has to name lines a run can actually return, and it has to name the PARTS and
not only the eight wholes — every whole is `derived`, computed by cascade from its parts, so a list
of wholes alone would restrict a run to lines nothing is ever asked about.
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
from app.services.line_item_requests import asked_about
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


def _shipped_extraction() -> dict:
    """config.toml's [extraction] as SHIPPED — read off the file rather than the settings object,
    which other tests reconfigure and the Focus line items master overwrites at startup."""
    import tomllib

    return tomllib.loads((SEED.parents[3] / "config.toml").read_text(encoding="utf-8"))["extraction"]


def test_focus_routing_ships_off_and_its_list_still_ships():
    """THE PREMISE, as it ships now. Focus-only is OFF in config.toml, at the user's instruction: a
    run asks the model about every line `asked_about` admits, not only the focus list. The list
    still ships — it seeds the Focus line items master (`services.focus_master`), which an admin
    keeps on the Settings screen — and the test below pins what it names, which is what a run
    restricted to it can return once an admin switches focus-only on."""
    shipped = _shipped_extraction()
    assert shipped["llm_focus_only"] is False
    assert len(shipped["llm_focus_keys"]) >= 1


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
    ASKED OF `line_item_requests.asked_about`, which is where this boundary lives now.
    `mapping._llm_withheld` was the same set and is retired: its two readers were the candidate
    payload a printed row was shown and the shortlist a per-caption call was given, and both went
    with the row request. A boundary asserted in a set nothing consults is not a boundary.
    """
    # THE NAMED KEYS ONLY. A trailing "*" puts a whole set in focus by prefix — ICON's three
    # patterns, and the seven groups of the 10-K set's additional items — so it names no line of
    # this set and is not part of the count this test pins.
    #
    # PER TEMPLATE: the named keys counted are the HK template's list (this set's); the patterns
    # are every template's, and none may begin a key of this set.
    lists = _shipped_extraction()["llm_focus_keys"]
    named = list(lists["output_csv_hk_v1"])
    focus = {k for k in named if not k.endswith("*")}
    patterns = {k for keys in lists.values() for k in keys if k.endswith("*")}
    assert not {k for k in named if k.endswith("*")}
    assert patterns == {"pl_icon__*", "bs_icon__*", "cov_icon__*", "notes_cash__*", "notes_debt__*",
                        "notes_tax__*", "notes_assets__*", "notes_audit__*", "notes_contingencies__*",
                        "notes_related__*"}
    by_key = {i.key: i for i in shipped.items}
    assert not any(k.startswith(tuple(p[:-1] for p in patterns)) for k in by_key)
    withheld = sorted(k for k in focus if k in by_key and not asked_about(by_key[k]))
    nameable = [k for k in focus if k in by_key and asked_about(by_key[k])]

    # 87 since the other-receivables NET and its LOSS ALLOWANCE each became a two-rung cascade and
    # each swapped its own entry for its two leaves: a derived parent is never asked about, so
    # leaving the parent here named a line no request could answer while the rows that CAN be
    # cited went unoffered.
    #
    # 94 SINCE THE RELATED-PARTY BALANCE PARTS WERE NAMED — Find 2, Find 3's gross and allowance,
    # the trade receivable's gross and allowance, and the two payables. Every one reads a mainland
    # 关联方应收应付款项 table, and none had been asked about: on a CAS filing the model never saw
    # the note that tabulates a related party's balance by line item. All seven are parts, so the
    # nameable count moves with them and the withheld count does not.
    assert len(focus) == 56, len(focus)   # 56: the derived Find 3 swapped its entry for its two leaves — Find 3 split into the fair-value hierarchy's printed Level 3 total (sub__fa_cp_level_3_printed_total) and its asset rows (sub__fa_cp_level_3_rows), sub__fa_cp_level_3_total a cascade over the two  # 55 before; 55: the seventeen retired parts the list still named left it with the Focus line items master — keys no shipped set holds  # 72 before; 72: sub__expense_notes_depreciation joined the depreciation parts in the focus list  # 71 before; 71: Other Receivables (CP): the grid gross/allowance readings and six carve-outs went; the related-party carve-out reads the note by meaning  # 79 before; 79: Contingent liabilities: nine parts folded into one line-item sum over the contingencies note  # 87 before; 87: the four Securities (LTP) Find 2 and non-current-split parts left the set;   # 91: the three Securities (CP) Find 2 and non-current parts left the set and the focus list
    # 69 nameable parts — see the item census in `test_retired_derivations`.
    assert len(nameable) == 48, len(nameable)   # 48: the two Level 3 leaves in place of the derived Find 3, which no request can answer  # 47 before; 47: sub__expense_notes_depreciation joined the depreciation parts in the focus list  # 46 before; 46: Other Receivables (CP): the grid gross/allowance readings and six carve-outs went; the related-party carve-out reads the note by meaning  # 54 before; 54: Contingent liabilities: nine parts folded into one line-item sum over the contingencies note  # 62 before; 62: the four Securities (LTP) Find 2 and non-current-split parts left the set;   # 66: the three Securities (CP) Find 2 and non-current parts left the set
    # THE UNREACHABLE ONES ARE THE WHOLES, every one of them — a part that turned up in this list
    # would mean the layer meant to be answerable had been withheld.
    assert all(not getattr(by_key[k], "parent", "") for k in withheld), withheld
    assert len(withheld) == 8, withheld
    # …and the nameable ones are the parts, every one of them. The complement of the assertion
    # above, and what pins "77" to a fact about the configuration rather than to a tally.
    assert all(getattr(by_key[k], "parent", "") for k in nameable)
