r"""ONE PRINTED FIGURE ON TWO LINE ITEMS — detected deterministically, decided by the model.

NEW FILE -> backend/tests/test_shared_figures.py

THE CASE, measured. `sub__ppe_depreciation` and `sub__fixed_asset_depreciation` both read
366,943,014.10 from the same note on 8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf, because PP&E's note
titles carried 固定资产 — the fixed-assets line's whole subject. Their parent's rung sums all five
asset lines, so the charge published doubled: 237,254,155.34 against a true 118,627,077.67.

WHY THE MODEL COULD NOT ALREADY CATCH IT, which is what makes a stage the right answer rather than
a prompt change. The twelve depreciation parts do share one request, so the model sees them side by
side — but `line_item_llm.build_request` states the contract: "a shared request is still answerable
line by line". Each line carries its own `notes_supplied` and is answered on its own; nothing asks
whether a row may be claimed twice. The parent that ADDS them is `type: derived`, so `asked_about`
returns False and it is never offered. The sum where the doubling happens appears in no request.

WHAT IS ASSERTED HERE. Detection is arithmetic and is tested exhaustively; the decision is the
provider's and is tested through a stub that answers a fixed way. The two halves are separable on
purpose — the detector must never depend on a model being configured, because with no provider the
stage has to leave every figure exactly where it was.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.services.shared_figures import Collision, find_collisions, resolve


def _val(amount: str, *, page: int = 7, box: tuple | None = (0.6, 0.3, 0.7, 0.31),
         period: str = "current", basis: Basis = Basis.CONSOLIDATED,
         caption: str = "Provided for the year") -> ExtractedValue:
    prov = None
    if page is not None:
        prov = Provenance(page_index=page, text_snippet=caption,
                          value_bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3])
                          if box else None)
    return ExtractedValue(basis=basis, period_label=period, value=Decimal(amount),
                          value_raw=Decimal(amount), provenance=prov)


def _line(key: str, label: str, *values: ExtractedValue) -> LineItem:
    li = LineItem(source_label=label, canonical_key=key, role=LineRole.LINE)
    for v in values:
        li.set_value(v)
    return li


def _doc(*lines: LineItem) -> DocumentModel:
    doc = DocumentModel(filename="probe.pdf")
    doc.line_items = list(lines)
    return doc


# ── detection ────────────────────────────────────────────────────────────────────────────────────

def test_one_cell_on_two_lines_is_a_collision():
    """THE CASE THE STAGE EXISTS FOR: the same page, the same box, the same amount, one slot."""
    doc = _doc(_line("sub__ppe_depreciation", "PP&E note", _val("366943014.10")),
               _line("sub__fixed_asset_depreciation", "Fixed assets note", _val("366943014.10")))
    cols = find_collisions(doc, {"sub__ppe_depreciation": "", "sub__fixed_asset_depreciation": ""})
    assert len(cols) == 1, cols
    assert sorted(cols[0].keys) == ["sub__fixed_asset_depreciation", "sub__ppe_depreciation"]
    assert cols[0].amount == Decimal("366943014.10")
    assert cols[0].caption == "Provided for the year"


def test_the_same_number_from_different_pages_is_not_a_collision():
    """Two lines reporting 1,000 from two pages are two disclosures that happen to agree. Keying
    on the amount alone would put every round figure in a filing into a contest."""
    doc = _doc(_line("a", "A", _val("1000", page=7)),
               _line("b", "B", _val("1000", page=41)))
    assert find_collisions(doc, {"a": "", "b": ""}) == []


def test_the_same_cell_in_different_slots_is_not_a_collision():
    """A figure is contested within one (basis, period). The consolidated and standalone columns
    are different statements, and current is not prior."""
    doc = _doc(_line("a", "A", _val("500", period="current")),
               _line("b", "B", _val("500", period="prior")))
    assert find_collisions(doc, {"a": "", "b": ""}) == []
    doc2 = _doc(_line("a", "A", _val("500", basis=Basis.CONSOLIDATED)),
                _line("b", "B", _val("500", basis=Basis.STANDALONE)))
    assert find_collisions(doc2, {"a": "", "b": ""}) == []


def test_a_parent_carrying_its_childs_figure_is_not_a_collision():
    """A cascade or rollup is MEANT to carry a child's figure up — that is the declared
    arithmetic. Reporting it would put every rollup in the filing into a contest and ask the model
    to re-decide something the configuration settled."""
    doc = _doc(_line("parent", "Parent", _val("900")),
               _line("child", "Child", _val("900")))
    assert find_collisions(doc, {"parent": "", "child": "parent"}) == []


def test_a_grandparent_is_not_a_collision_either():
    """Ancestry is followed all the way up, not one level."""
    doc = _doc(_line("top", "Top", _val("900")),
               _line("leaf", "Leaf", _val("900")))
    assert find_collisions(doc, {"top": "", "mid": "top", "leaf": "mid"}) == []


def test_a_parent_cycle_does_not_hang():
    """A bad edit can make two keys each other's parent. The walk must terminate."""
    doc = _doc(_line("a", "A", _val("900")), _line("b", "B", _val("900")))
    assert find_collisions(doc, {"a": "b", "b": "a"}) == []


def test_two_unrelated_lines_under_one_parent_still_collide():
    """SIBLINGS ARE NOT ANCESTORS, and this is the measured case — the five asset lines are
    siblings whose parent ADDS them, which is exactly why one figure on two of them doubles."""
    doc = _doc(_line("ppe", "PP&E", _val("100")), _line("fixed", "Fixed", _val("100")))
    cols = find_collisions(doc, {"ppe": "deprec", "fixed": "deprec", "deprec": ""})
    assert len(cols) == 1 and sorted(cols[0].keys) == ["fixed", "ppe"]


def test_a_computed_figure_is_never_compared():
    """A derived parent, a residual remainder and an assembled total are computed, not printed, so
    they carry no box — and a computed figure equalling a printed one is arithmetic working."""
    doc = _doc(_line("a", "A", _val("400")),
               _line("b", "B", _val("400", page=None)))
    assert find_collisions(doc, {"a": "", "b": ""}) == []


def test_one_key_on_several_carrier_rows_is_one_claimant():
    """A concept spread over several rows is one line item, not two claimants of its own figure."""
    doc = _doc(_line("a", "A", _val("700")), _line("a", "A again", _val("700")),
               _line("b", "B", _val("700")))
    cols = find_collisions(doc, {"a": "", "b": ""})
    assert len(cols) == 1
    assert sorted(cols[0].keys) == ["a", "b"], cols[0].keys


def test_a_missing_figure_is_not_a_claim():
    """A slot present but empty claims nothing."""
    doc = _doc(_line("a", "A", _val("100")),
               _line("b", "B", ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                              value=None, value_raw=None,
                                              provenance=Provenance(page_index=7))))
    assert find_collisions(doc, {"a": "", "b": ""}) == []


# ── the note-sourced case, which is the one that matters ─────────────────────────────────────────
#
# A FIGURE WRITTEN BY `stages.note_sourced` CARRIES NO PROVENANCE. Measured on
# 8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf: `sub__ppe_depreciation` and
# `sub__fixed_asset_depreciation` both hold 366,943,014.10 with `provenance=None`, `page=None`,
# `box=None`. A cell comparison cannot see them — and they are exactly the collisions worth
# catching, because a cascade then ADDS the two lines together. 98% of a filing's values carry a
# box; the 2% that do not are these.
#
# The first version of this detector compared cells only. Run against the real filing with the
# overlapping note titles restored it found NOTHING, which is why these tests exist: the trail is
# where a note-sourced figure keeps the rows behind it.

def _sourced(key: str, label: str, amount: str, rows: list[tuple[str, str, str]],
             *, basis: str = "consolidated", period: str = "current") -> LineItem:
    """A line filled the way `note_sourced` fills one: a value with NO provenance, and a
    derivation naming the note rows it was assembled from."""
    li = LineItem(source_label=label, canonical_key=key, role=LineRole.LINE)
    li.set_value(ExtractedValue(basis=Basis(basis), period_label=period,
                                value=Decimal(amount), value_raw=Decimal(amount)))
    li.derivation = {
        f"{basis}:{period}": {
            "method": f"note_sourced:sum_of_{len(rows)}_rows",
            "formula": " + ".join(r[1] for r in rows),
            "result": amount,
            "inputs": [{"label": cap, "note": note, "value": val, "counted": True,
                        "provenance": None}
                       for note, cap, val in rows],
            "flags": [],
        }
    }
    return li


_ROWS = [("7", "（1）计提", "366943014.10")]


def test_two_note_sourced_lines_on_the_same_note_row_collide():
    """THE MEASURED CASE. Neither value has provenance, so only the trail can identify them."""
    doc = _doc(_sourced("sub__ppe_depreciation", "PP&E", "366943014.10", _ROWS),
               _sourced("sub__fixed_asset_depreciation", "Fixed", "366943014.10", _ROWS))
    assert all(getattr(next(iter(li.values.values())), "provenance", None) is None
               for li in doc.line_items), "premise gone — these values now carry provenance"
    cols = find_collisions(doc, {"sub__ppe_depreciation": "deprec",
                                 "sub__fixed_asset_depreciation": "deprec", "deprec": ""})
    assert len(cols) == 1, cols
    assert sorted(cols[0].keys) == ["sub__fixed_asset_depreciation", "sub__ppe_depreciation"]
    # The caption and note reach the question even though the VALUE carried neither.
    assert cols[0].caption == "（1）计提" and cols[0].note_number == "7"


def test_two_lines_summing_different_note_rows_do_not_collide():
    """Same amount, different rows behind it — two disclosures that happen to agree."""
    doc = _doc(_sourced("a", "A", "100", [("7", "charge", "100")]),
               _sourced("b", "B", "100", [("9", "charge", "100")]))
    assert find_collisions(doc, {"a": "", "b": ""}) == []


def test_only_the_rows_actually_taken_count():
    """An `alternatives` rollup records the candidates it did NOT take. Two lines offered the same
    candidates but taking different ones are not claiming one figure."""
    a = _sourced("a", "A", "100", [("7", "taken", "100")])
    b = _sourced("b", "B", "100", [("7", "taken", "100")])
    b.derivation["consolidated:current"]["inputs"] = [
        {"label": "other", "note": "7", "value": "100", "counted": True, "provenance": None},
        {"label": "taken", "note": "7", "value": "100", "counted": False, "provenance": None},
    ]
    assert find_collisions(_doc(a, b), {"a": "", "b": ""}) == []


def test_a_trail_with_no_counted_rows_is_not_comparable():
    li = _sourced("a", "A", "100", [("7", "x", "100")])
    li.derivation["consolidated:current"]["inputs"] = [
        {"label": "x", "note": "7", "value": "100", "counted": False, "provenance": None}]
    other = _sourced("b", "B", "100", [("7", "x", "100")])
    other.derivation["consolidated:current"]["inputs"] = []
    assert find_collisions(_doc(li, other), {"a": "", "b": ""}) == []


# ── the decision ─────────────────────────────────────────────────────────────────────────────────

class _Provider:
    """A provider that answers a fixed way, so the decision path is tested without a network."""

    def __init__(self, keep: str, confidence: float = 0.9, boom: bool = False) -> None:
        self.keep, self.confidence, self.boom = keep, confidence, boom
        self.calls = 0

    def complete_structured(self, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("provider unreachable")
        from app.services.shared_figures import SharedFigureDecision
        return SharedFigureDecision(keep=self.keep, rationale="because",
                                    confidence=self.confidence), {"model": "test"}


def _collision() -> Collision:
    return Collision(basis="consolidated", period="current", amount=Decimal("100"),
                     page_index=6, caption="Provided for the year", note_number="7",
                     claimants=[("ppe", "PP&E", "", 0, "k0"), ("fixed", "Fixed", "", 1, "k1")])


def test_the_model_can_keep_it_on_one_line():
    keep, why, conf = resolve(_Provider("ppe"), _collision(), {})
    assert (keep, why, conf) == ("ppe", "because", 0.9)


def test_the_model_can_keep_it_on_both():
    """"Both" IS an answer — two different cuts of one disclosure, and the filing means each."""
    assert resolve(_Provider("both"), _collision(), {})[0] == "both"


def test_a_key_that_was_not_offered_keeps_both():
    """Treating an unrecognised string as "drop everything else" would let one malformed reply
    delete every claimant's figure."""
    assert resolve(_Provider("sub__something_else"), _collision(), {})[0] == "both"


def test_an_unreachable_provider_keeps_both():
    """The answer that changes nothing is the safe one."""
    p = _Provider("ppe", boom=True)
    assert resolve(p, _collision(), {})[0] == "both"
    assert p.calls == 1


def test_a_low_confidence_answer_keeps_both():
    assert resolve(_Provider("ppe", confidence=0.1), _collision(), {},
                   min_confidence=0.5)[0] == "both"


# ── the stage ────────────────────────────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, cfg) -> None:
        self.line_items = cfg
        self.locale = "en"
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)


class _Cfg:
    def __init__(self, items) -> None:
        self.items = items


class _Item:
    def __init__(self, key: str, parent: str = "") -> None:
        self.key, self.parent, self.definition = key, parent, ""


def _contested_doc() -> DocumentModel:
    return _doc(_line("ppe", "PP&E note", _val("366943014.10")),
                _line("fixed", "Fixed assets note", _val("366943014.10")))


def test_the_stage_changes_nothing_without_a_provider(monkeypatch):
    """THE PROPERTY THAT MAKES IT SAFE ON BY DEFAULT. With `provider: stub` the contest is
    recorded and flagged and both figures stay — so every deterministic measurement of this
    pipeline is unaffected by the stage existing."""
    from app.config import get_settings
    from app.stages.shared_figures import SharedFiguresStage

    s = get_settings()
    monkeypatch.setattr(s.llm, "provider", "stub", raising=False)
    doc = _contested_doc()
    ctx = _Ctx(_Cfg([_Item("ppe", "deprec"), _Item("fixed", "deprec"), _Item("deprec")]))
    SharedFiguresStage().run(doc, ctx)

    assert len(doc.shared_figures) == 1
    assert doc.shared_figures[0]["claimed_by"] and doc.shared_figures[0]["keep"] == ""
    assert all(len(li.values) == 1 for li in doc.line_items), "a figure was removed with no provider"
    assert any("shared_figure_unresolved" in f
               for li in doc.line_items for f in li.confidence.flags)


def test_the_stage_reports_nothing_without_configuration():
    """No ancestry means no way to tell a rollup from a contest, so it declines rather than
    asking the model to re-decide declared arithmetic."""
    from app.stages.shared_figures import SharedFiguresStage

    doc = _contested_doc()
    ctx = _Ctx(_Cfg([]))
    SharedFiguresStage().run(doc, ctx)
    assert doc.shared_figures == []
    assert any("no line-item configuration" in m for m in ctx.logs), ctx.logs


def test_the_stage_removes_the_figure_from_the_loser(monkeypatch):
    """The whole point: on an explicit answer the figure is REMOVED from the other line — not
    zeroed, because a zero asserts the filing disclosed nothing."""
    from app.config import get_settings
    from app.ports.registry import registry
    from app.stages.shared_figures import SharedFiguresStage

    s = get_settings()
    monkeypatch.setattr(s.llm, "provider", "test-provider", raising=False)
    # The stage resolves the provider through this same instance, so patching `get` on it is what
    # substitutes the stub — `app.ports.registry` is the Registry object, not a module holding one.
    monkeypatch.setattr(registry, "get", lambda kind, name: _Provider("fixed"), raising=False)
    doc = _contested_doc()
    ctx = _Ctx(_Cfg([_Item("ppe", "deprec"), _Item("fixed", "deprec"), _Item("deprec")]))
    SharedFiguresStage().run(doc, ctx)

    kept = {li.canonical_key: len(li.values) for li in doc.line_items}
    assert kept == {"fixed": 1, "ppe": 0}, kept
    assert doc.shared_figures[0]["keep"] == "fixed"
    ppe = next(li for li in doc.line_items if li.canonical_key == "ppe")
    assert any(f.startswith("shared_figure_removed:kept_on_fixed") for f in ppe.confidence.flags)


def test_keep_on_both_leaves_both_figures(monkeypatch):
    from app.config import get_settings
    from app.ports.registry import registry
    from app.stages.shared_figures import SharedFiguresStage

    s = get_settings()
    monkeypatch.setattr(s.llm, "provider", "test-provider", raising=False)
    monkeypatch.setattr(registry, "get", lambda kind, name: _Provider("both"), raising=False)
    doc = _contested_doc()
    ctx = _Ctx(_Cfg([_Item("ppe", "deprec"), _Item("fixed", "deprec"), _Item("deprec")]))
    SharedFiguresStage().run(doc, ctx)

    assert all(len(li.values) == 1 for li in doc.line_items)
    assert doc.shared_figures[0]["keep"] == "both"
    assert all(any("shared_figure_kept_on_both" in f for f in li.confidence.flags)
               for li in doc.line_items)


def test_the_stage_is_in_the_pipeline_before_reconcile():
    """POSITION IS THE DESIGN. After every route that writes a figure, and before reconcile — a
    tie checked against a figure counted twice would report a tie that is not true."""
    from app.core.pipeline import default_pipeline

    names = [s.name for s in default_pipeline().stages]
    assert "shared_figures" in names, names
    assert names.index("shared_figures") < names.index("reconcile")
    assert names.index("shared_figures") > names.index("note_sourced")
    assert names.index("shared_figures") > names.index("assemble_components")


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
