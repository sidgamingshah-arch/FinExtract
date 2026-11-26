"""THE WHOLE-PDF REQUESTS ON A REAL FILING, end to end (688008, 澜起科技, CAS, 256 pages): every page
sent at 100 pages a request — the setting for a model whose window is under a million tokens,
where the notes go in three parts — the stand-in answering from a SCRIPT
(`tests.whole_pdf_standin`). The shipped 600 pages a request, on the 1,000,000-token window, is
the last test's run: the window check cuts the 256 pages into two parts.

What it shows: the first request is the smallest; every part holding the notes carries the 17
statement faces, and a last part holds the statement faces alone, where the statements' face lines
are asked; every page is sent, the auditor's report (pages 147-149) with the rest, so the
Accountant, which the report alone prints, is read and checked on its page; and the Securities
family (Find 1, Level 3, the LTP parts) is one request in each part. Every figure is the script's —
what the pipeline does with an answer, never what a model would answer. One run, about 40 seconds.
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
                             extraction={"whole_pdf_pages_per_request": 100})


def _asked(rec):
    return [c for c in rec.calls if c["schema"] == "WholePdfReply"]


def test_the_first_request_is_the_smallest(run):
    _doc, ctx, rec = run
    asked = _asked(rec)
    # 5 requests to each of the three parts holding the notes (the three statements' lines read
    # from the notes or anywhere in one), and the three statements to the faces alone.
    assert len(asked) == 18 and all(c.get("scripted") for c in asked)
    sizes = [len(c["line_keys"]) for c in asked]
    assert sizes[0] == min(sizes) == 10 and ctx.whole_pdf["first_request"]["statement"] == (
        "statement_setup")
    assert sorted(len(c["line_keys"]) for c in asked if c["documents"][0]["part"] == 4
                  ) == [81, 107, 208]


def test_every_part_carries_the_faces_and_every_page_is_sent(run):
    """The stand-in records the report pages each part it was sent holds (0-based)."""
    _doc, ctx, rec = run
    held = {}
    for c in _asked(rec):
        d = c["documents"][0]
        held[d["part"]] = set(d["report_pages"])
    faces = {p - 1 for p in ctx.whole_pdf["pages_in_every_part"]}
    assert sorted(held) == [1, 2, 3, 4] and len(faces) == 17
    assert all(faces <= pages for pages in held.values())
    assert [len(pages) for _n, pages in sorted(held.items())] == ctx.whole_pdf["pages_per_part"]
    assert ctx.whole_pdf["pages_sent"] == list(range(1, 257))
    assert set().union(*(held[n] for n in (1, 2, 3))) == set(range(256))
    assert {146, 147, 148} <= set().union(*(held[n] for n in (1, 2, 3)))
    # The statements alone: the 17 faces and the report's pages 145-149, which 688008's classifier
    # calls faces of no statement.
    assert held[4] == faces | {144, 145, 146, 147, 148}
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
    assert len(holding) == 3                                   # one per part holding the notes
    assert all(set(FAMILY) <= set(c["line_keys"]) for c in holding)
    _row, ev = slot(_doc, "bs_ca__secur_and_other_fincl_assets_cp")
    assert ev is not None and str(ev.value) == "1783494750.68"


def test_at_the_shipped_600_pages_the_window_cuts_the_notes_in_two(tmp_path):
    """The shipped defaults (600 pages a request, a 1,000,000-token window): 688008's 256 pages are
    over what the window holds beside a reply at 4,800 a picture, so they go as two parts holding
    the notes, each carrying the faces, and the statements alone: 13 requests where 100 pages a
    request makes 18 — the first still the smallest, the Securities family one request in each
    part, the Accountant still read off the auditor's report. (With the classifier's statement and
    note pages only, 110 pages, it was one part and 8 requests.)"""
    from app.config import ExtractionSettings

    shipped = ExtractionSettings().whole_pdf_pages_per_request
    assert shipped == 600
    script = {"units": "元", "lines": {
        "sub__fa_cp_fvtpl_note_total": [script_figure("1783494750.68", page=182, caption="合计")],
        ACCOUNTANT: {"text": FIRM, "pages": [149]}}}
    with pytest.MonkeyPatch.context() as mp:
        doc, ctx, rec = run_whole_pdf(mp, tmp_path, STEM, script,
                                      extraction={"whole_pdf_pages_per_request": shipped})
    asked = _asked(rec)
    assert ctx.whole_pdf["pages_per_part"] == [136, 137, 22] and len(asked) == 13
    sizes = [len(c["line_keys"]) for c in asked]
    assert sizes[0] == min(sizes) and ctx.whole_pdf["first_request"]["statement"] == (
        "statement_setup")
    holding = [c for c in asked if "sub__fa_cp_fvtpl_note_total" in c["line_keys"]]
    assert len(holding) == 2 and all(set(FAMILY) <= set(c["line_keys"]) for c in holding)
    assert any("window=1000000 reply_limit=128000" in line for line in ctx.logs)
    row = next(r for r in doc.line_items if r.canonical_key == ACCOUNTANT)
    assert next(iter(row.values.values())).value_text == FIRM
