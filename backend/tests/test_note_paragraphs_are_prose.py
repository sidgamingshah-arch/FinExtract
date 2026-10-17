"""A note's running paragraphs are its prose, not its rows — and the table above them reads whole.

Reduced from f1's note 17 'Non-current borrowings' (an Ind AS filing, figures in lakhs), as the model
received it before this change:

  * the "Nature of security and terms of repayment" paragraphs below the table became rows, one per
    wrapped line, each carrying the numbers inside its sentence — "(sanctioned ₹" {current: 9000.00},
    "equal quarterly instalments of ₹" {current: 250.00, prior: 17, col2: 31} — and the table's own
    current column was keyed `current_col1`;
  * the caption-less subtotal 8,946.34 / 9,248.90 was missing from the rows;
  * "Less: Current maturities of long-term borrowings (included in note 21)" lost its "21)";
  * note 10's nil row "Trade receivables considered good - Secured  -  -" was joined into the
    caption of the row beneath it.

And from the prose side (`note_context.dedupe_prose`): the second loan paragraph lost the phrases it
shares with the first, and note 32 kept a stray "Year" where its two pages join.

The coordinates are the filing's, normalised to an A4 page.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import LineRole
from app.core.models.geometry import BBox
from app.services.note_context import dedupe_prose
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word, _group_rows, _line_tol

_W, _H = 595.0, 842.0
_CHAR = 4.3              # points per character at the filing's 8pt body text
_SPACE = 2.2             # points between two words of one line
_LEFT, _RIGHT = 42.0, 553.0
_CUR, _PRI = 425.0, 553.0     # right edges of the two figure columns


def _word(text: str, x0: float, y: float) -> Word:
    x1 = x0 + _CHAR * len(text)
    return Word(text=text, bbox=BBox(x0=x0 / _W, y0=y / _H, x1=min(x1, _W) / _W, y1=(y + 8) / _H))


def _cell(text: str, x0: float, y: float) -> list[Word]:
    out, x = [], x0
    for tok in text.split():
        out.append(_word(tok, x, y))
        x += _CHAR * len(tok) + _SPACE
    return out


def _right(text: str, x1: float, y: float) -> list[Word]:
    width = sum(_CHAR * len(t) for t in text.split()) + _SPACE * (len(text.split()) - 1)
    return _cell(text, x1 - width, y)


def _justified(text: str, y: float, x0: float = _LEFT, x1: float = _RIGHT - 6) -> list[Word]:
    toks = text.split()
    ink = sum(_CHAR * len(t) for t in toks)
    gap = (x1 - x0 - ink) / (len(toks) - 1)
    out, x = [], x0
    for tok in toks:
        out.append(_word(tok, x, y))
        x += _CHAR * len(tok) + gap
    return out


def _row(caption: str, x0: float, y: float, cur: str | None = None, pri: str | None = None):
    out = _cell(caption, x0, y) if caption else []
    if cur is not None:
        out += _right(cur, _CUR, y)
    if pri is not None:
        out += _right(pri, _PRI, y)
    return out


PARAGRAPH = [
    "(a) Term loan from State Bank of India (sanctioned ₹ 9,000.00 lakhs) is secured by a first",
    "pari passu charge on the Company's movable and immovable property, plant and equipment,",
    "both present and future. It is repayable in 24 equal quarterly instalments of ₹ 375.00",
    "lakhs commencing June 2022; 12 instalments are outstanding as at 31 March 2025.",
]


def _note_17() -> list[Word]:
    words: list[Word] = []
    words += _cell("17. Non-current borrowings", _LEFT, 574)
    words += _cell("Particulars", _LEFT, 588) + _right("As at 31 March 2025", _CUR, 588) \
        + _right("As at 31 March 2024", _PRI, 588)
    words += _row("Secured", _LEFT, 602)
    words += _row("Term loans from banks", 54, 615)
    words += _row("State Bank of India", 66, 627, "4,500.00", "6,000.00")
    words += _row("HDFC Bank Limited", 66, 640, "4,250.00", "3,000.00")
    words += _row("Vehicle loans from banks", 54, 652, "196.34", "248.90")
    words += _row("", _LEFT, 665, "8,946.34", "9,248.90")
    words += _row("Less: Current maturities of long-term borrowings (included in", _LEFT, 677)
    words += _row("note 21)", _LEFT, 689, "(2,592.67)", "(2,334.15)")
    words += _row("Total", _LEFT, 701, "6,353.67", "6,914.75")
    words += _cell("Nature of security and terms of repayment", _LEFT, 717)
    for i, line in enumerate(PARAGRAPH):
        y = 729 + 12 * i
        words += _justified(line, y) if i < len(PARAGRAPH) - 1 else _cell(line, _LEFT, y)
    return words


def _tables():
    return extract_note_tables(_note_17(), page_index=11, document_id=None, source_kind="native")


def _figures(item) -> dict[str, Decimal]:
    return {ev.period_label: ev.value for ev in item.values.values()}


# ── the paragraph is prose ────────────────────────────────────────────────────────────────────

def test_the_paragraph_lines_are_not_rows():
    (table,) = _tables()
    captions = [it.raw_label for it in table.items]
    assert not any("sanctioned" in c or "instalments" in c or "pari passu" in c
                   or "Nature of security" in c for c in captions), captions


def test_the_tables_own_columns_read_current_and_prior():
    (table,) = _tables()
    sbi = next(it for it in table.items if it.raw_label.endswith("State Bank of India"))
    assert _figures(sbi) == {"current": Decimal("4500.00"), "prior": Decimal("6000.00")}
    assert all(set(_figures(it)) <= {"current", "prior"} for it in table.items)


def test_the_paragraph_still_reaches_the_prose_whole():
    (table,) = _tables()
    for line in PARAGRAPH:
        assert line in table.source_text, line
        assert line in table.prose_text, line


def test_a_table_row_a_header_band_and_an_ageing_row_are_not_running_text():
    from app.services.notes_extract import running_text_words
    rows = _group_rows(_note_17(), _line_tol(_note_17()), fold=None)
    running = running_text_words(rows)
    for row in rows:
        text = " ".join(w.text for w in sorted(row, key=lambda w: w.bbox.x0))
        if text.startswith(("Particulars", "State Bank", "Less:", "Total")):
            assert not any(id(w) in running for w in row), text
    ageing = (_cell("(i) Undisputed trade", _LEFT, 100)
              + [w for i, f in enumerate(["10,486.15", "2,641.95", "196.40", "62.70", "25.20",
                                          "-", "13,412.40"])
                 for w in _right(f, 230 + 52 * i, 100)])
    assert not running_text_words([ageing])
    # A column-header band set at a regular pitch across the full width (1966's property note) is
    # evenly spaced and full width, and still not a sentence.
    band = [w for i in range(9) for w in _right("RMB'000", 150 + 50 * i, 100)]
    assert not running_text_words([band])


# ── the table reads whole ─────────────────────────────────────────────────────────────────────

def test_the_caption_less_subtotal_is_a_row_under_its_heading():
    (table,) = _tables()
    sub = [it for it in table.items if _figures(it).get("current") == Decimal("8946.34")]
    assert len(sub) == 1, [it.raw_label for it in table.items]
    assert sub[0].raw_label == "Secured"
    assert sub[0].role is LineRole.SUBTOTAL
    assert _figures(sub[0])["prior"] == Decimal("9248.90")


def test_a_bare_line_that_adds_up_to_nothing_above_is_not_promoted():
    words = _note_17()
    words = [w for w in words if w.text not in ("8,946.34", "9,248.90")]
    words += _row("", _LEFT, 665, "8,000.00", "9,000.00")
    (table,) = extract_note_tables(words, page_index=11, document_id=None, source_kind="native")
    assert not any(_figures(it).get("current") == Decimal("8000.00") for it in table.items)


def test_a_wrapped_caption_keeps_the_bracket_it_closes():
    (table,) = _tables()
    assert any(it.raw_label == "Less: Current maturities of long-term borrowings "
                               "(included in note 21)" for it in table.items), \
        [it.raw_label for it in table.items]


def test_a_row_of_nil_dashes_is_not_joined_into_the_next_caption():
    words = _cell("10. Trade receivables", _LEFT, 200)
    words += _cell("Particulars", _LEFT, 214) + _right("As at 31 March 2025", _CUR, 214) \
        + _right("As at 31 March 2024", _PRI, 214)
    words += _row("Trade receivables considered good - Secured", _LEFT, 229, "-", "-")
    words += _row("Trade receivables considered good - Unsecured", _LEFT, 241,
                  "13,412.40", "12,146.85")
    words += _row("Total", _LEFT, 253, "13,412.40", "12,146.85")
    (table,) = extract_note_tables(words, page_index=9, document_id=None, source_kind="native")
    captions = [it.raw_label for it in table.items]
    assert "Trade receivables considered good - Unsecured" in captions, captions
    assert not any("Secured -" in c for c in captions), captions


# ── the prose, joined across fragments ────────────────────────────────────────────────────────

def test_a_shared_phrase_inside_a_sentence_survives_the_join():
    first = ("(a) Term loan from State Bank of India is secured by a first pari passu charge on the "
             "Company's property. It carries interest at the three-month MCLR plus 0.40%.")
    second = ("(b) Term loan from HDFC Bank Limited for the capacity expansion at Waluj is secured "
              "by a first pari passu charge on property, plant and equipment. It carries interest "
              "at the three-month T-bill rate plus 2.10%; 17 instalments are outstanding.")
    got = dedupe_prose([first, second])
    assert second in got, got


def test_the_join_cuts_a_header_band_whole_and_leaves_no_stray_word():
    band = "Year ended 31 March 2025 Year ended 31 March 2024"
    first = f"{band} Consumption of stores and spares 1,986.42 1,804.26"
    second = f"(a) Payment to auditors Particulars {band} As auditor Statutory audit fee 24.00 22.00"
    got = dedupe_prose([first, second])
    assert "Particulars As auditor" in got, got
    assert got.count("Year") == 2, got
