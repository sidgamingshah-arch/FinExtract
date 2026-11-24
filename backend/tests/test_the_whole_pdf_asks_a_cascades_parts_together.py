"""THE PARTS ONE CASCADE ADDS UP ARE ASKED IN ONE REQUEST, and a part nothing reads is not asked
(`services.whole_pdf.families`, `chunk_lines`, `sibling_groups`, `lines_to_ask`).

Securities (CP)'s Find 1 parts were split over three requests that never saw each other — the
balance sheet's, the notes' and "other" — only because the shipped set declares `statement: notes`
on three of the six and none on the rest. Nothing stopped one holding being read into two of them:
688008's 结构性存款 (1,629,362,905.47) sits inside 交易性金融资产 (1,783,494,750.68), and read into
both FVTPL and "other financial assets" it makes CP 3,412,857,656.15 (finding
find1_siblings_double_count_whole_pdf). And `bs_ca_residual_L3` — no definition, no parent, read by
no cascade — was asked blind as "Residual L3 [PART; read from the statement]" (findings
residual_L3_asked_blind, residual_l3_asked_without_definition).

Measured on the shipped HK set; no model is called.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
CP = "bs_ca__secur_and_other_fincl_assets_cp"
LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
FIND_1 = ["sub__fa_cp_fvtpl_note_total", "sub__fa_cp_fvtoci_note_total",
          "sub__fa_cp_afs_htm_note_total", "sub__fa_cp_debt_investments_note_total",
          "sub__fa_cp_investment_and_money_market_securities_note_total",
          "sub__fa_cp_other_fincl_assets_note_total"]
LEVEL_3 = ["sub__fa_cp_level_3_printed_total", "sub__fa_cp_level_3_rows"]


def _set(name="output_csv_hk_line_items.json"):
    return load_line_item_set(json.loads((TEMPLATES / name).read_text(encoding="utf-8")),
                              resolve=True)


@pytest.fixture(scope="module")
def hk():
    return _set()


def test_a_part_nothing_reads_is_not_asked(hk):
    asked = W.lines_to_ask(hk)
    keys = {item.key for item in asked}
    assert "bs_ca_residual_L3" not in keys
    # Every part still asked reaches a line: a cascade, a formula or its parent reads it.
    read = W.read_by(hk)
    assert all(item.key in read for item in asked if W.is_part(item))
    # Only that one line goes: every other extracted line is asked as before.
    by_key = {item.key: item for item in hk.items}
    gone = {item.key for item in hk.items if item.type in W.ASKED_TYPES
            and item.extraction_mode != "derive"} - keys
    assert gone == {"bs_ca_residual_L3"} and not by_key["bs_ca_residual_L3"].definition


@pytest.mark.parametrize("name", ["output_csv_indas_line_items.json",
                                  "output_csv_usgaap_line_items.json",
                                  "output_csv_icon_line_items.json"])
def test_no_other_shipped_set_loses_a_line(name):
    st = _set(name)
    assert len(W.lines_to_ask(st)) == sum(
        1 for item in st.items if item.type in W.ASKED_TYPES and item.extraction_mode != "derive")


def test_the_securities_family_is_asked_in_one_request(hk):
    chunks = W.chunk_lines(W.lines_to_ask(hk), 250, hk)
    holding = {key: i for i, c in enumerate(chunks) for key in (item.key for item in c)}
    family = FIND_1 + LEVEL_3 + ["sub__ltp_fvtpl_note_total", "sub__ltp_fvtoci_note_total",
                                 "sub__ltp_face_other_equity_instrument_investments"]
    assert len({holding[k] for k in family}) == 1
    home = chunks[holding[FIND_1[0]]]
    assert home.statement == "other"
    # Before, the six Find 1 parts went in three requests.
    plain = W.chunk_lines(W.lines_to_ask(hk), 250)
    before = {k: i for i, c in enumerate(plain) for k in (item.key for item in c)}
    assert len({before[k] for k in FIND_1}) == 2 and len({before[k] for k in FIND_1 + [
        "sub__ltp_face_other_equity_instrument_investments"]}) == 3


def test_a_statements_own_lines_never_join_another_statements_request(hk):
    chunks = W.chunk_lines(W.lines_to_ask(hk), 250, hk)
    for chunk in chunks:
        assert {W.statement_of(i) for i in chunk if not W.is_part(i)} <= {chunk.statement}
    # The balance sheet's own lines are all in its one request.
    own = [i.key for i in W.lines_to_ask(hk)
           if W.statement_of(i) == "balance_sheet" and not W.is_part(i)]
    sheet = next(c for c in chunks if c.statement == "balance_sheet")
    assert set(own) <= {i.key for i in sheet}
    assert sorted(i.key for c in chunks for i in c) == sorted(i.key for i in W.lines_to_ask(hk))


def test_the_question_says_one_holding_goes_into_at_most_one_sibling(hk):
    from tests.whole_pdf_standin import standin_module

    chunks = W.chunk_lines(W.lines_to_ask(hk), 250, hk)
    home = next(c for c in chunks if FIND_1[0] in {i.key for i in c})
    text = W.question(home, W.Part(pages=[0, 1], pdf=b""), 1, 2, hk, carried=1)
    assert "Added together" in text and "at most one part of a group" in text
    assert f"Into {CP}: {', '.join(FIND_1)}." in text
    assert any(line.startswith(f"Into {LTP}: ") for line in text.splitlines())
    assert "Added together" in W.SYSTEM and "at most one of them" in W.SYSTEM
    # The group is a sentence, not a line asked: the stand-in (as a model would) reads the asked
    # lines off the question and finds exactly the request's.
    assert standin_module().asked_whole_pdf_lines(text) == [i.key for i in home]
    # A request with no family says nothing of it.
    setup = next(c for c in chunks if c.statement == "statement_setup")
    assert "Added together" not in W.question(setup, W.Part(pages=[0], pdf=b""), 1, 1, hk)
