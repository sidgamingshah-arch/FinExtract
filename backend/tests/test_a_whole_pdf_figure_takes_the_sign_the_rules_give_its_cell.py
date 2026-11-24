"""A WHOLE-PDF FIGURE ENDS WITH THE SIGN THE RULES GIVE THE CELL IT WAS READ OFF, FOR ITS LINE
(`stages.whole_pdf._RulesSigns`, `stages.whole_pdf._as_read`).

Every figure the model gives is found again on a cell the rules read (`services.whole_pdf_locate`).
Its sign is now the rules' own decision for that cell on that line — normalize run on a copy of the
rules' rows, and the rules' note-to-face split for a cell in a note — not a caption regex. Three
figures came out with the other sign before, each with a correct SCRIPTED answer (not a model's):

  * 688008 p227, the indirect method's 股份支付 (+58,848,996.62 / +122,677,641.08): any caption
    holding 支付 was negated, so Other Non-Cash Adjs(Oper) published -131,336,664.28 /
    -126,327,845.36 for the rules' -13,638,671.04 / 119,027,436.80;
  * 300319 p104, the parent's prior 其中：利息费用, printed -757,766.20 under 财务费用: the rules
    publish +757,766.20 (a credit printed under an unsigned expense), the route -757,766.20;
  * 1966 p184, note 7's lease interest (1,490 / 20,162): the rules' split writes it in the face's
    convention (finance costs printed (1,492,343)), -1,490 / -20,162; the route wrote +1,490 / +20,162.

Each case runs the REAL pipeline on the real filing's pages with the shipped stand-in answering from
a SCRIPT (`tests.whole_pdf_standin`), and a CONTROL runs the same answer with the rules' sign
decision switched off — the route as it was — and shows the wrong sign it published. A scripted
answer shows what the pipeline does with that answer, never what a model would answer.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from tests.whole_pdf_standin import filing_path, run_whole_pdf, script_figure, slot

#: The caption regex the route negated a cash-flow row by, before (the control puts it back).
_OLD_CAS_OUTFLOW = re.compile(r"支付|购建|購建|偿还|償還|分配股利")

OTHER = "cf_oper_indirect__other_non_cash_adjs_oper"
FV, INV, SBP = ("公允价值变动损失（收益以“－”号填列）", "投资损失（收益以“－”号填列）", "股份支付")
INTEREST = "is_pl__interest_expense"
LEASE = "is_pl__interest_on_lease_liabilities"
LEASE_CAPTION = "Interest on lease liabilities (note 16(b)) 租賃負債的利息（附註16(b)）"

CASES = {
    # 688008: the two indirect-method pages (p226-227; p227 prints the three rows below).
    "688008": ("688008_montage_2024_prc", [225, 226], {"units": "元", "lines": {OTHER: [
        script_figure("-13638671.04", page=227, caption=FV,
                      parts=[(FV, "-23888571.20"), (INV, "-48599096.46"), (SBP, "58848996.62")]),
        script_figure("119027436.80", page=227, caption=FV, period="prior",
                      parts=[(FV, "87328284.96"), (INV, "-90978489.24"), (SBP, "122677641.08")]),
    ]}}),
    # 300319: the income statements (p102-105); only the 其中 row is answered, not 财务费用.
    "300319": ("300319_maijie_2024_prc", list(range(101, 105)), {"units": "元", "lines": {INTEREST: [
        script_figure("847412.97", page=104, caption="其中：利息费用", basis="standalone"),
        script_figure("-757766.20", page=104, caption="其中：利息费用", basis="standalone",
                      period="prior"),
    ]}}),
    # 1966: the income statement (p102-103) and note 7, finance costs (p184).
    "1966": ("hkex_1966_ar", [101, 102, 183], {"units": "RMB'000", "lines": {LEASE: [
        script_figure("1490", page=184, caption=LEASE_CAPTION),
        script_figure("20162", page=184, caption=LEASE_CAPTION, period="prior"),
    ]}}),
}


def _needs(stem: str):
    return pytest.mark.skipif(not filing_path(stem).exists(), reason=f"{stem}.pdf not present")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    done: dict[tuple[str, bool], tuple] = {}

    def _run(name: str, *, control: bool = False):
        if (name, control) not in done:
            stem, pages, script = CASES[name]
            with pytest.MonkeyPatch.context() as mp:
                if control:
                    from app.stages import whole_pdf as stage

                    mp.setattr(stage, "_rules_signs", lambda *_a, **_k: None)
                    mp.setattr(stage, "_CAS_PAYMENT", _OLD_CAS_OUTFLOW)
                done[(name, control)] = run_whole_pdf(
                    mp, tmp_path_factory.mktemp(f"{name}{'_control' if control else ''}"), stem,
                    script, pages=pages)
        return done[(name, control)]
    return _run


def _value(doc, key, basis="consolidated", period="current"):
    _row, ev = slot(doc, key, basis, period)
    return None if ev is None else Decimal(str(ev.value))


def _flags(doc, key, basis="consolidated", period="current"):
    _row, ev = slot(doc, key, basis, period)
    return list(ev.confidence.flags) if ev is not None else []


@_needs("688008_montage_2024_prc")
def test_688008_share_based_payment_is_the_add_back_the_rules_read(run):
    """股份支付 is a share-based PAYMENT — a noun — printed as an add-back. Each row of the sum
    takes its own cell's sign, so the line is the rules' -13,638,671.04 / 119,027,436.80."""
    doc, ctx, _rec = run("688008")
    assert ctx.mapping_strategy == "whole_pdf"
    assert _value(doc, OTHER) == Decimal("-13638671.04")
    assert _value(doc, OTHER, period="prior") == Decimal("119027436.80")
    assert "whole_pdf:sign_of_rules_cell" in _flags(doc, OTHER)


@_needs("688008_montage_2024_prc")
def test_688008_control_the_caption_regex_negated_it(run):
    doc, _ctx, _rec = run("688008", control=True)
    assert _value(doc, OTHER) == Decimal("-131336664.28")
    assert _value(doc, OTHER, period="prior") == Decimal("-126327845.36")


@_needs("300319_maijie_2024_prc")
def test_300319_a_credit_printed_under_an_unsigned_expense_keeps_the_rules_sign(run):
    """The parent's prior 其中：利息费用 prints -757,766.20 under 财务费用: the rules publish
    +757,766.20, a contra line's breakdown of an unsigned expense read as printed and negated. The
    model answered no 财务费用 line: the parent is read on the line whose flip rule its caption
    matches, as normalize reads it. The current year's printed charge is a deduction, as before."""
    doc, ctx, _rec = run("300319")
    assert ctx.mapping_strategy == "whole_pdf"
    assert _value(doc, INTEREST, "standalone", "prior") == Decimal("757766.20")
    flags = _flags(doc, INTEREST, "standalone", "prior")
    assert "sign_rule:as_printed_under_unsigned_expense" in flags
    assert "whole_pdf:sign_of_rules_cell" in flags
    assert _value(doc, INTEREST, "standalone", "current") == Decimal("-847412.97")


@_needs("300319_maijie_2024_prc")
def test_300319_control_the_model_row_had_no_parent(run):
    doc, _ctx, _rec = run("300319", control=True)
    assert _value(doc, INTEREST, "standalone", "prior") == Decimal("-757766.20")


@_needs("hkex_1966_ar")
def test_1966_a_note_cell_is_written_in_the_face_convention_the_rules_split_writes(run):
    """Note 7 prints the lease interest as a positive charge; the face prints finance costs as
    (1,492,343), and note 7's rows add up to it only turned around — the rules' split publishes
    -1,490 / -20,162, and so does this route."""
    doc, ctx, _rec = run("1966")
    assert ctx.mapping_strategy == "whole_pdf"
    assert _value(doc, LEASE) == Decimal("-1490")
    assert _value(doc, LEASE, period="prior") == Decimal("-20162")
    flags = _flags(doc, LEASE)
    assert "whole_pdf:note_to_face_convention:-1" in flags
    assert "whole_pdf:sign_of_rules_cell" in flags


@_needs("hkex_1966_ar")
def test_1966_control_the_note_figure_was_written_as_printed(run):
    doc, _ctx, _rec = run("1966", control=True)
    assert _value(doc, LEASE) == Decimal("1490")
    assert _value(doc, LEASE, period="prior") == Decimal("20162")


# ── the decision itself, without a filing ─────────────────────────────────────────────────────

def test_a_payment_caption_is_a_verb_never_a_share_based_payment():
    """For a figure found on no cell the rules read, a cash-flow row is negated by a payment
    caption — 支付 the verb, not 股份支付 the noun."""
    from app.stages.whole_pdf import _CAS_PAYMENT

    for paid in ("购买商品、接受劳务支付的现金", "支付给职工以及为职工支付的现金", "支付的各项税费",
                 "支付其他与经营活动有关的现金", "购建固定资产、无形资产和其他长期资产支付的现金",
                 "投资支付的现金", "偿还债务支付的现金", "分配股利、利润或偿付利息支付的现金",
                 "其中：子公司支付给少数股东的股利、利润", "取得子公司及其他营业单位支付的现金净额"):
        assert _CAS_PAYMENT.search(paid), paid
    for noun in ("股份支付", "以权益结算的股份支付", "股份支付费用", "股权支付", "股份支付計入所有者權益的金額"):
        assert not _CAS_PAYMENT.search(noun), noun


class _Place:
    def __init__(self, at):
        self.at = at


class _Verdict:
    unchecked = False

    def __init__(self, n):
        self.kept = [{"at": i} for i in range(n)]

    def place_of(self, entry):
        return _Place(entry["at"])


class _Signs:
    """Stands in for `_RulesSigns`: (the rules' sign, the printed sign, flags) per row, or None."""

    def __init__(self, table):
        self.table = table

    def sign(self, place, key):
        return self.table[place.at]


def _reading(key, rows):
    from app.services import whole_pdf as W

    rows = [(c, Decimal(a)) for c, a in rows]
    return W.Reading(key=key, basis="consolidated", period="current",
                     amount=sum((a for _c, a in rows), Decimal(0)), caption=rows[0][0],
                     period_end="", part=0, check=W.Check("on_page", 0), on_face=True,
                     rows=rows if len(rows) > 1 else [])


def _items():
    import json

    from app.schemas.line_items import load_line_item_set
    from tests.whole_pdf_standin import HK_SET, TEMPLATES

    cfg = load_line_item_set(json.loads((TEMPLATES / HK_SET).read_text(encoding="utf-8")),
                             resolve=True)
    return {i.key: i for i in cfg.items}


def test_each_row_of_a_sum_takes_its_cells_sign_unless_the_model_took_it_out():
    """A row given as printed takes the rules' sign (a payment the outflow block negates); a payment
    the model gave negative and the rules negate stays negative; a row the model gave AGAINST its
    printed sign where the rules leave that sign alone is the model taking it out of the sum, and
    stays out; a row found on no cell keeps the route's own rule."""
    from app.stages.whole_pdf import _as_read

    items = _items()
    other = items["cf_investing__other_invest_cash_flows"]
    r = _reading(other.key, [("取得投资收益收到的现金", "45794434.86"),
                             ("支付其他与投资活动有关的现金", "34741410.02"),
                             ("购建固定资产…支付的现金", "-100"),
                             ("收到其他与投资活动有关的现金", "-11306000.00"),
                             ("股份支付", "7")])
    signs = _Signs({0: (1, 1, []), 1: (-1, 1, ["sign_normalised:cas_outflow"]),
                    2: (-1, 1, ["sign_normalised:cas_outflow"]), 3: (1, 1, []), 4: None})
    flags: list = []
    value, settled = _as_read(other, r, flags, _Verdict(5), signs)
    assert settled is True
    assert value == (Decimal("45794434.86") - Decimal("34741410.02") - Decimal("100")
                     - Decimal("11306000.00") + Decimal("7"))
    assert "sign_normalised:cas_outflow" in flags
    assert "whole_pdf:sign_of_rules_cell:4_of_5" in flags
    assert any(f.startswith("whole_pdf:row_sign_kept:收到其他与投资活动有关的现金") for f in flags)


def test_a_single_figure_takes_its_cells_sign_and_one_found_nowhere_is_left_to_normalize():
    from app.stages.whole_pdf import _as_read

    items = _items()
    interest = items[INTEREST]
    flags: list = []
    got = _as_read(interest, _reading(interest.key, [("其中：利息费用", "-757766.20")]), flags,
                   _Verdict(1), _Signs({0: (1, -1, ["sign_rule:as_printed_under_unsigned_expense"])}))
    assert got == (Decimal("757766.20"), True)
    assert "sign_rule:as_printed_under_unsigned_expense" in flags
    # Found on no cell: a statement line is normalized as a face row is, as before.
    assert _as_read(interest, _reading(interest.key, [("其中：利息费用", "-757766.20")]), [],
                    _Verdict(1), _Signs({0: None})) == (Decimal("-757766.20"), False)
