"""THE WHOLE-PDF REQUESTS ON A REAL FILING, end to end (688008, 澜起科技, CAS, 256 pages): the run
as the shipped config makes it ("statements" pages, 100 pages a request), the stand-in answering
from a SCRIPT (`tests.whole_pdf_standin`).

What it shows: the first request is the smallest; both parts holding the notes carry the 17
statement faces, and a third holds the statement faces alone, where the statements' face lines are
asked; the auditor's report (pages 147-149) is sent, so the Accountant, which the report alone
prints, is read and checked on its page; and the Securities family (Find 1, Level 3, the LTP
parts) is one request. Every figure is the script's — what the pipeline does with an answer, never what a model
would answer. One run, about 40 seconds.
"""
from __future__ import annotations

import pytest

from tests.whole_pdf_standin import filing_path, run_whole_pdf, script_figure, slot

STEM = "688008_montage_2024_prc"
ACCOUNTANT = "statement_setup_controls__accountant"
FIRM = "安永华明会计师事务所（特殊普通合伙）"
FAMILY = ["sub__fa_cp_fvtpl_note_total", "sub__fa_cp_other_fincl_assets_note_total",
          "sub__fa_cp_level_3_printed_total", "sub__fa_cp_level_3_rows",
          "sub__ltp_fvtpl_note_total", "sub__ltp_face_other_equity_instrument_investments"]

pytestmark = pytest.mark.skipif(not filing_path(STEM).exists(), reason=f"{STEM}.pdf not present")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    script = {"units": "元", "lines": {
        "sub__cp_face_trading_fincl_assets": [
            script_figure("1783494750.68", page=150, caption="交易性金融资产")],
        "sub__fa_cp_fvtpl_note_total": [script_figure("1783494750.68", page=182, caption="合计")],
        ACCOUNTANT: {"text": FIRM, "pages": [149]}}}
    with pytest.MonkeyPatch.context() as mp:
        return run_whole_pdf(mp, tmp_path_factory.mktemp("e2e"), STEM, script,
                             whole_pdf_pages="statements")


def _asked(rec):
    return [c for c in rec.calls if c["schema"] == "WholePdfReply"]


def test_the_first_request_is_the_smallest(run):
    _doc, ctx, rec = run
    asked = _asked(rec)
    # 5 requests to each part holding the notes (the three statements' lines read from the notes
    # or anywhere in one), and the three statements to the faces alone.
    assert len(asked) == 13 and all(c.get("scripted") for c in asked)
    sizes = [len(c["line_keys"]) for c in asked]
    assert sizes[0] == min(sizes) == 10 and ctx.whole_pdf["first_request"]["statement"] == (
        "statement_setup")
    assert sorted(len(c["line_keys"]) for c in asked if c["documents"][0]["part"] == 3
                  ) == [81, 107, 208]


def test_both_parts_carry_the_faces_and_the_auditors_report_is_sent(run):
    """The stand-in records the report pages each part it was sent holds (0-based)."""
    _doc, ctx, rec = run
    held = {}
    for c in _asked(rec):
        d = c["documents"][0]
        held[d["part"]] = set(d["report_pages"])
    faces = {p - 1 for p in ctx.whole_pdf["pages_in_every_part"]}
    assert sorted(held) == [1, 2, 3] and len(faces) == 17
    assert all(faces <= pages for pages in held.values())
    assert [len(pages) for _n, pages in sorted(held.items())] == ctx.whole_pdf["pages_per_part"]
    assert {146, 147, 148} <= held[1] and not {146, 147, 148} & held[2]
    # The statements alone: the 17 faces and the report's pages, which 688008's classifier calls
    # faces of no statement.
    assert held[3] == faces | {146, 147, 148}
    assert not ctx.whole_pdf["pages_not_sent"]


def test_the_accountant_is_read_off_the_auditors_report(run):
    doc, ctx, _rec = run
    row, ev = next(((r, v) for r in doc.line_items if r.canonical_key == ACCOUNTANT
                    for v in r.values.values()), (None, None))
    assert row is not None and ev.value_text == FIRM
    assert ev.provenance.page_index == 148 and "llm_text:page 149" in row.confidence.flags
    assert ctx.whole_pdf["texts_refused"] == 0


def test_the_securities_family_is_one_request(run):
    _doc, _ctx, rec = run
    holding = [c for c in _asked(rec) if "sub__fa_cp_fvtpl_note_total" in c["line_keys"]]
    assert len(holding) == 2                                   # one per part
    assert all(set(FAMILY) <= set(c["line_keys"]) for c in holding)
    _row, ev = slot(_doc, "bs_ca__secur_and_other_fincl_assets_cp")
    assert ev is not None and str(ev.value) == "1783494750.68"
