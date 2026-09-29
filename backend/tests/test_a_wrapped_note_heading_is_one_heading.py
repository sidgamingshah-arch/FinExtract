"""A HEADING THAT WRAPPED ONTO THE NEXT LINE IS ONE HEADING — and only where it really wrapped.

NEW FILE -> backend/tests/test_a_wrapped_note_heading_is_one_heading.py

WHY THE TRUNCATION HAPPENS, which is also why the repair belongs here and not in configuration. An
HKEX filing sets the English and the Han in two columns, so one printed heading arrives as ONE word
row carrying both — and when the heading is too long for its column it wraps, leaving half of each
language on the row below. China SCE's note 26 prints

    26. FINANCIAL ASSETS AT FAIR VALUE
        THROUGH PROFIT OR LOSS

and the walker kept the first row, so the title was

    'FINANCIAL ASSETS AT FAIR VALUE 26. 按公允值計量且其變動計'

cut before the words that say WHICH fair-value category it is. `sub__fa_cp_fvtpl_note_total` names
the full phrase, so it matched nothing and five focus lines — the FVTPL family, current and
non-current — were sent no declared note for that filing. Authoring around it would have meant
guessing FVTPL against FVTOCI from a heading truncated before the word that distinguishes them, and
the two are `any_of` siblings of a SUM, so a wrong guess adds one balance twice.

MEASURED OVER THE FIVE REFERENCE FILINGS: 4 headings join — 1966's notes 24, 26 and 29 and kaming's
note 19, every one a real wrap — for +5 (line, declared note) pairs, 0 lost and 1 of 342 corpus
note titles newly claimed by anything.

THE TWO WRONG VERSIONS THIS FILE KEEPS OUT, both measured, because the guard is the whole content
of the change and a looser one is worse than none:

  1. ANY title-only row. That swallowed the opening PROSE line of a CAS note — 000709's 五、23
     became "、 预计负债 如果与或有事项相关的义务同时符合以下条件，本公司将其确认为预计负债：" — and
     patterns then matched words inside the sentence: six contingent-liability lines claimed the
     provisions POLICY note, 44 such pairs on 000709 and 46 on 300319. It also LOST four real pairs
     per CAS filing, the related-party receivable and payable lines, whose patterns are anchored.
  2. Caps-and-length only. That still swallowed the COLUMN HEADER BAND and the CSRC applicability
     marker, neither a sentence and both short: 000709's 七、13 became "、 使用权资产 项目 房屋及
     建筑物 机器设备 土地使用权 合计" and 688008's 七、13 became "其他流动资产 √适用□不适用" — 57
     titles on 000709, 69 on 300319, and the same four pairs lost.

Requiring BOTH lines to carry Latin AND the heading to carry Han is what names the real cause: the
wrap is an artefact of the bilingual column merge, so the repair applies exactly where that merge
happens and a CAS filing — one language, one column — is untouched.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word, _group_rows, row_tolerance


def _w(text: str, x0: float, y0: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + 0.06, y1=y0 + 0.010))


# THE PAGE IS NORMALISED TO [0, 1], so the step has to keep the longest line on the page — the
# English sentence below is eight words. `_group_rows` reads the y coordinate, so the x spacing
# only has to be monotonic and inside the page.
_STEP = 0.065


def _words(*lines):
    words = []
    for i, line in enumerate(lines):
        for j, token in enumerate(line):
            words.append(_w(token, 0.06 + j * _STEP, 0.10 + i * 0.020))
    return words


# THE NUMBER IS PRINTED ONCE PER LANGUAGE COLUMN, so the merged row carries it twice — "26.
# FINANCIAL ASSETS AT FAIR VALUE 26. 按公允值計量且其變動計" — and `_HEADING`, which anchors the
# number at the start, reads the first and leaves the second inside the title. The fixtures below
# spell that out rather than tidying it, because a fixture that puts the number once is a row this
# walker never sees and the first draft of these tests failed against it.
def _titles(*lines) -> dict[str, str]:
    tables = extract_note_tables(_words(*lines), page_index=1, document_id=None,
                                 source_kind="native")
    return {str(t.note_number or ""): str(t.title or "") for t in tables}


# ── the wrap is joined ────────────────────────────────────────────────────────────────────────

def test_a_bilingual_heading_that_wrapped_is_joined() -> None:
    """1966's note 26, which is the whole reason this exists."""
    got = _titles(
        ["26.", "FINANCIAL", "ASSETS", "AT", "FAIR", "VALUE", "26.", "按公允值計量且其變動計"],
        ["THROUGH", "PROFIT", "OR", "LOSS", "入損益的金融資產"],
        ["Listed", "equity", "investments", "1,000"],
    )

    assert got["26"] == ("FINANCIAL ASSETS AT FAIR VALUE 26. 按公允值計量且其變動計 "
                         "THROUGH PROFIT OR LOSS 入損益的金融資產")


def test_the_joined_heading_is_what_the_shipped_pattern_names() -> None:
    """THE END OF THE WIRE. The join is worth nothing unless the authored pattern now reaches it,
    and it reaches it through the script projection in `note_context.title_variants` — the two
    scripts arrive INTERLEAVED, so the raw joined string still does not match.
    """
    import re

    from app.services.note_context import matches_title

    got = _titles(
        ["26.", "FINANCIAL", "ASSETS", "AT", "FAIR", "VALUE", "26.", "按公允值計量且其變動計"],
        ["THROUGH", "PROFIT", "OR", "LOSS", "入損益的金融資產"],
        ["Listed", "equity", "investments", "1,000"],
    )
    shipped = re.compile(r"financial\s+assets?\s+at\s+fair\s+value\s+through\s+profit\s+or\s+loss",
                         re.IGNORECASE)

    assert matches_title(shipped, got["26"])


# ── and only where it really wrapped ──────────────────────────────────────────────────────────

def test_a_cas_column_header_band_is_not_a_heading_continuation() -> None:
    """000709's 七、13. Short, no sentence punctuation, and not a heading — the case the
    caps-and-length guard alone still swallowed."""
    got = _titles(
        ["13.", "使用权资产"],
        ["项目", "房屋及建筑物", "机器设备", "合计"],
        ["账面原值", "1,000", "2,000", "3,000"],
    )

    assert got["13"] == "使用权资产"


def test_a_csrc_applicability_marker_is_not_a_heading_continuation() -> None:
    """688008's 七、13 — "applicable / not applicable", printed under a great many CAS headings."""
    got = _titles(["13.", "其他流动资产"], ["√适用□不适用"], ["项目", "1,000"])

    assert got["13"] == "其他流动资产"


def test_a_notes_opening_prose_line_is_not_a_heading_continuation() -> None:
    """000709's 五、23, and the first version of this joined it."""
    got = _titles(
        ["23.", "预计负债"],
        ["如果与或有事项相关的义务同时符合以下条件，本公司将其确认为预计负债："],
        ["项目", "1,000"],
    )

    assert got["23"] == "预计负债"


def test_an_english_sentence_is_not_a_heading_continuation() -> None:
    """kaming's note 8 opens "Loss before taxation is arrived at after charging" — and
    `sub__pbt_depreciation` happens to NAME `arrived at after charging`, so joining it would have
    made six lines match by luck. That gap is closed in configuration instead, by naming the
    spelling a filing at a loss prints.
    """
    got = _titles(
        ["8.", "LOSS", "BEFORE", "TAXATION", "除稅前虧損"],
        ["Loss", "before", "taxation", "is", "arrived", "at", "after", "charging"],
        ["Depreciation", "1,000"],
    )

    assert got["8"] == "LOSS BEFORE TAXATION 除稅前虧損"


def test_a_period_band_is_not_a_heading_continuation() -> None:
    """It carries no value column either, so without `_TABLE_BAND` every table's heading would
    swallow its own period row."""
    got = _titles(["26.", "FINANCIAL", "ASSETS", "按公允值"], ["2023", "2022"], ["Total", "1,000"])

    assert got["26"] == "FINANCIAL ASSETS 按公允值"


def test_a_monolingual_heading_is_never_joined() -> None:
    """The discriminator, stated as a test: an all-Latin heading did not come through a bilingual
    column merge, so it cannot have wrapped this way and nothing is joined onto it."""
    got = _titles(["26.", "FINANCIAL", "ASSETS", "AT", "FAIR", "VALUE"],
                  ["THROUGH", "PROFIT", "OR", "LOSS"], ["Total", "1,000"])

    assert got["26"] == "FINANCIAL ASSETS AT FAIR VALUE"


def test_a_row_that_opens_its_own_note_is_never_swallowed() -> None:
    got = _titles(["26.", "FINANCIAL", "ASSETS", "按公允值"],
                  ["27.", "OTHER", "PAYABLES", "其他應付款項"], ["Total", "1,000"])

    assert set(got) >= {"26", "27"}
    assert got["26"] == "FINANCIAL ASSETS 按公允值"


# ── the script projection, which is what makes the join reach a pattern ───────────────────────

def test_each_script_of_a_bilingual_heading_is_offered_on_its_own() -> None:
    """WHY THE JOIN NEEDS THIS BESIDE IT. The two columns merge into one row, so the joined heading
    has the scripts INTERLEAVED and the enumerator in the middle. Every authored pattern is written
    in one script and in reading order, so the form it needs is the heading's run in that script.
    """
    from app.services.note_context import title_variants

    got = title_variants("FINANCIAL ASSETS AT FAIR VALUE 26. 按公允值計量且其變動計 "
                         "THROUGH PROFIT OR LOSS 入損益的金融資產")

    assert "FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS" in got
    # HAN JOINS WITH NOTHING BETWEEN, because the space between two Han runs is the column merge's
    # and removing it reconstructs the phrase the filing prints.
    assert "按公允值計量且其變動計入損益的金融資產" in got


def test_a_monolingual_heading_gains_no_variant() -> None:
    """The projection is bounded to the interleaved case: one run is the whole heading, so adding
    it again would match nothing new and every existing heading keeps exactly the variants it had.
    """
    from app.services.note_context import title_variants

    assert title_variants("Inventories") == ("Inventories",)
    assert title_variants("、其他应收款") == ("、其他应收款", "其他应收款")


def test_the_leading_enumerator_variant_still_comes_second() -> None:
    """`_LEAD` stripping is what 44% of CAS headings depend on, and the projections are appended
    after it rather than in place of it."""
    from app.services.note_context import title_variants

    got = title_variants("、 固定资产")

    assert got[0] == "、 固定资产"
    assert got[1] == "固定资产"


# ── and the configuration half of the same gap ────────────────────────────────────────────────

def test_a_filing_at_a_loss_prints_loss_and_the_declaration_now_says_so() -> None:
    """THE OTHER HALF, kept here because the two were measured together and the wrong fix for this
    one was to let the join swallow note 8's prose.

    kaming prints "LOSS BEFORE TAXATION 8. 除稅前虧損" where 1966 prints "PROFIT BEFORE TAX", and
    the shared pattern once named only profit — so the depreciation parts that read that note were
    sent no declared note on a filing that made a loss.

    NOW BY MEANING. The parts that read the profit-before-tax note carry `note_terms` ("before tax",
    "除稅前") instead of a title pattern, and a heading is covered when it holds every word of one
    term — so profit and loss headings are covered alike, and the neighbouring LOSS PER SHARE note,
    a different disclosure entirely, is not.
    """
    import json
    import pathlib

    from app.core.models import NotesTable
    from app.schemas.line_items import load_line_item_set
    from app.services.line_item_notes import covered_notes

    seed = json.loads((pathlib.Path(__file__).resolve().parents[1]
                       / "app/sample/templates/output_csv_hk_line_items.json")
                      .read_text(encoding="utf-8"))
    st = load_line_item_set(seed, resolve=True)
    carriers = [i for i in st.items
                if i.key in ("sub__pbt_depreciation", "sub__pbt_oper_exp_depreciation")]
    assert len(carriers) == 2, [i.key for i in carriers]

    for item in carriers:
        assert not item.note_source.note_title_any, item.key
        for heading in ("LOSS BEFORE TAXATION 8. 除稅前虧損",
                        "PROFIT BEFORE TAX (Continued) 8. 除稅前溢利（續）"):
            assert covered_notes(item, [NotesTable(note_number="8", title=heading)]) == ("8",), \
                (item.key, heading)
        # AND NOT THE NEIGHBOURING LOSS NOTE, which is a different disclosure entirely.
        assert covered_notes(item, [NotesTable(note_number="14", title="LOSS PER SHARE 14. 每股虧損")]) == (), \
            item.key
