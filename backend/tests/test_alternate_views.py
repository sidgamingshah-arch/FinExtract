"""Statements that are not two-column comparatives.

Two shapes break the current/prior assumption the rest of the app is built on:

* a statement of CHANGES IN EQUITY, whose columns are equity components (issued capital, each
  reserve, retained profits, non-controlling interests, total equity) and whose rows are
  movements through the year — forcing it into current/prior files a component under a period
  that does not exist;
* a page whose TEXT IS DRAWN SIDEWAYS, which wide statements use to fit a dozen columns — there
  the words advance bottom-to-top, so grouping them into rows by shared y finds no rows at all
  and the page yields nothing.
"""
from __future__ import annotations

from app.core.models.geometry import BBox


def _val(period, value, page=106, x0=0.3, col=None):
    return {"basis": "consolidated", "period_label": period, "value": str(value),
            "column_index": col,
            "provenance": {"source_kind": "native", "page_index": page,
                           "bbox": {"x0": x0, "y0": 0.2, "x1": x0 + 0.05, "y1": 0.21}}}


def test_the_equity_statement_is_served_as_a_matrix_with_named_columns():
    from app.api.routes.documents import _build_statement

    rows = [
        {"source_label": "At 1 January 2023", "canonical_key": None, "values": [
            _val("Issued capital", 365064, x0=0.30),
            _val("Retained profits", 12_000, x0=0.55),
            _val("Total equity", 36_625_241, x0=0.80)]},
        {"source_label": "Loss for the year", "canonical_key": None, "values": [
            _val("Retained profits", -8_401_124, x0=0.55),
            _val("Total equity", -8_401_124, x0=0.80)]},
        {"source_label": "At 31 December 2023", "canonical_key": None, "values": [
            _val("Issued capital", 365064, x0=0.30),
            _val("Retained profits", 3_598_876, x0=0.55),
            _val("Total equity", 20_482_326, x0=0.80)]},
    ]
    d = _build_statement(rows, None, "changes_in_equity", "f.pdf")

    assert d["layout"] == "matrix"
    # Columns are the component headers read off the page, ordered as PRINTED (by where the
    # figures sit), not alphabetically and not in dict order.
    assert [c["key"] for c in d["columns"]] == ["Issued capital", "Retained profits",
                                               "Total equity"]
    assert d["periods"] == []                    # a component is not a period

    labels = [r["label"] for r in d["rows"]]
    assert labels == ["At 1 January 2023", "Loss for the year", "At 31 December 2023"]
    closing = d["rows"][-1]
    assert closing["cells"]["Total equity"] == 20_482_326
    assert closing["cells"]["Issued capital"] == 365064
    # An opening/closing balance is the matrix's own subtotal line.
    assert closing["kind"] == "subtotal" and d["rows"][1]["kind"] == "item"
    # v1/v2 stay empty so nothing downstream reads a component as a period.
    assert closing["v1"] is None and closing["v2"] is None
    assert closing["source"] is not None          # still traceable to the page


def test_comparative_rows_never_leak_into_the_matrix():
    """Both shapes coexist in one extraction. A comparative row labels its values positionally
    ("current"/"prior"), a matrix row by column name — that is the discriminator, and without it
    every balance-sheet row appeared in the equity statement and "current" became a column."""
    from app.api.routes.documents import _build_statement

    rows = [
        {"source_label": "Trade receivables", "canonical_key": "bs_current_assets__trade_receivables",
         "values": [_val("current", 198_330, page=103), _val("prior", 466_350, page=103)]},
        {"source_label": "At 1 January 2023", "canonical_key": None, "values": [
            _val("Issued capital", 365064), _val("Total equity", 36_625_241)]},
    ]
    d = _build_statement(rows, None, "changes_in_equity", "f.pdf")
    assert [c["key"] for c in d["columns"]] == ["Issued capital", "Total equity"]
    assert [r["label"] for r in d["rows"]] == ["At 1 January 2023"]


def test_a_single_named_cell_is_not_a_matrix_row():
    """A movement in equity touches at least its own component and a total column, so one named
    cell is chrome — a stray figure, not a row of the statement."""
    from app.api.routes.documents import _build_statement

    rows = [{"source_label": "stray", "canonical_key": None,
             "values": [_val("Total equity", 5)]}]
    assert _build_statement(rows, None, "changes_in_equity", "f.pdf")["rows"] == []


def test_sideways_text_is_read_in_reading_space():
    """A 90°-rotated page: the words advance bottom-to-top, so page-space y is constant along a
    printed row. Read as-is there are no rows; transformed into reading space the row is
    recovered — and provenance keeps the PAGE box so the highlight still lands where the figure
    is drawn."""
    from app.services.pdf_extract import _to_reading_space

    # Two words side by side on a printed row, drawn rotated: they share an x on the page.
    a = BBox(x0=0.20, y0=0.70, x1=0.21, y1=0.80)
    b = BBox(x0=0.20, y0=0.40, x1=0.21, y1=0.50)
    ra, rb = _to_reading_space(a, 90), _to_reading_space(b, 90)
    # In reading space they now share a row (same y band) and differ across it.
    assert abs(ra.y0 - rb.y0) < 1e-9
    assert ra.x0 != rb.x0
    # ...and the transform stays inside the unit square.
    for box in (ra, rb):
        assert 0.0 <= box.x0 <= box.x1 <= 1.0
        assert 0.0 <= box.y0 <= box.y1 <= 1.0


def test_reading_space_is_a_no_op_for_an_upright_page():
    """The overwhelming majority of pages are upright; they must be untouched."""
    from app.services.pdf_extract import _to_reading_space

    box = BBox(x0=0.1, y0=0.2, x1=0.3, y1=0.25)
    assert _to_reading_space(box, 0) is box


def test_a_rotated_word_keeps_its_page_box_for_click_to_source():
    from app.services.row_reconstruct import Word

    page_box = BBox(x0=0.20, y0=0.70, x1=0.21, y1=0.80)
    reading = BBox(x0=0.70, y0=0.79, x1=0.80, y1=0.80)
    w = Word(text="1,000", bbox=reading, page_bbox=page_box)
    assert w.source_bbox is page_box
    # An upright word has one box and it serves both purposes.
    assert Word(text="x", bbox=page_box).source_bbox is page_box


def test_the_dominant_direction_wins_over_a_stray_rotated_stamp():
    """A watermark or a sideways stamp must not decide the whole page's orientation."""
    from app.services.pdf_extract import text_rotation

    class FakePage:
        def __init__(self, lines):
            self._lines = lines

        def get_text(self, kind):
            assert kind == "dict"
            return {"blocks": [{"lines": self._lines}]}

    body = [{"dir": (1.0, 0.0), "spans": [{"text": "x" * 400}]}]
    stamp = [{"dir": (0.0, -1.0), "spans": [{"text": "CONFIDENTIAL"}]}]
    assert text_rotation(FakePage(body + stamp)) == 0
    # ...but a genuinely sideways page is detected.
    sideways = [{"dir": (0.0, -1.0), "spans": [{"text": "y" * 400}]}]
    assert text_rotation(FakePage(sideways + [{"dir": (1.0, 0.0),
                                               "spans": [{"text": "page 12"}]}])) == 90


def test_the_equity_statement_must_close_at_the_balance_sheets_equity():
    """The one relation that crosses two statements — and worth checking precisely because the
    two sides are extracted by completely different readers (a matrix reader and a two-column
    reader), so their agreement is evidence rather than a restatement."""
    from app.api.routes.documents import _accounting_checks, _build_review, _equity_closing

    def equity_rows(closing):
        return [
            {"source_label": "At 1 January 2023", "canonical_key": None, "values": [
                _val("Retained profits", 1), _val("Total equity", 36_625_241)]},
            {"source_label": "Loss for the year", "canonical_key": None, "values": [
                _val("Retained profits", -1), _val("Total equity", -16_142_915)]},
            {"source_label": "At 31 December 2023", "canonical_key": None, "values": [
                _val("Retained profits", 0), _val("Total equity", closing)]},
        ]

    def bs_equity(total):
        return [{"canonical_key": "bs_equity__total_equity", "source_label": "Total equity",
                 "values": [{"basis": "consolidated", "period_label": "current",
                             "value": str(total)}]}]

    # The LAST balance line is the closing one, not the opening one — and it comes back with the NAME
    # the review queue indicts it under (its canonical_key, or the printed caption when the matrix row
    # mapped to nothing, as these do).
    assert _equity_closing(equity_rows(20_482_326), "consolidated") == (
        "At 31 December 2023", 20_482_326.0, "At 31 December 2023")

    # Agreement is silent.
    agreeing = equity_rows(20_482_326) + bs_equity(20_482_326)
    assert [c["type"] for c in _accounting_checks(agreeing, [], "en")] == []

    # Disagreement is a finding that names the gap.
    conflicting = equity_rows(20_482_326) + bs_equity(19_000_000)
    tie = next(c for c in _accounting_checks(conflicting, [], "en") if c["type"] == "equity_tie")
    assert tie["delta"] == "1,482,326"

    # BOTH lines the card indicts are named: the balance sheet's equity total AND the equity
    # statement's closing row. The card used to name only the first, on the strength of a comment
    # saying the closing row "is a matrix row, not one of `rows`" — but `_equity_closing` iterates
    # `_matrix_rows(rows, basis)`, which FILTERS `rows`, so the row it returns is one of them. The
    # header tile counts the lines no finding names, and it was counting this one.
    assert tie["names"] == ["At 31 December 2023", "bs_equity__total_equity"]
    # …and the header tile therefore stops counting the closing row as a line with no finding. Mapped
    # rows, with a confident mapping, so the only card in the payload is the equity tie: an unmapped
    # matrix row would be indicted by its OWN card and the count could not tell the two apart.
    mapped = [{**r, "canonical_key": f"eq_{i}", "mapping_confidence": 0.99}
              for i, r in enumerate(equity_rows(20_482_326))] + bs_equity(19_000_000)
    review = _build_review(mapped, "d.pdf", "en")
    assert [c["type"] for c in review["checks"]] == ["equity_tie"]
    assert review["checks"][0]["names"] == ["bs_equity__total_equity", "eq_2"]
    # THE ASSERTION THAT FAILS WITH THE DEFECT RESTORED: naming only the balance-sheet total leaves
    # 3 of these 4 lines reading as having no finding while a card indicts 2 of them.
    assert review["summary"]["passed"] == len(mapped) - 2

    # The CAPTION of the row taken as the closing balance is printed on the card, so it is part of
    # what a reviewer accepted and it is fingerprinted. A re-run that picks a different closing row
    # carrying the same figure is a different claim, and it used to keep the acceptance in silence.
    assert tie["calc"][0][0] == "At 31 December 2023"
    assert tie["evidence"]["closing_label"] == "at 31 december 2023"
    renamed = [dict(r) for r in conflicting]
    renamed[2] = {**renamed[2], "source_label": "At 31 December 2023 (restated)"}
    moved = next(c for c in _accounting_checks(renamed, [], "en") if c["type"] == "equity_tie")
    assert moved["subject"] == tie["subject"]                 # the same claim is being checked…
    assert moved["evidence"] != tie["evidence"]                   # …about a different row
    # Re-wrapped whitespace and case are NOT a different row: that is re-parsing, not a change.
    respaced = [dict(r) for r in conflicting]
    respaced[2] = {**respaced[2], "source_label": "At  31 December  2023"}
    same = next(c for c in _accounting_checks(respaced, [], "en") if c["type"] == "equity_tie")
    assert same["evidence"] == tie["evidence"]


def test_no_equity_statement_means_no_cross_statement_check():
    """A filing whose equity statement was not extracted must not be accused of anything."""
    from app.api.routes.documents import _accounting_checks, _equity_closing

    rows = [{"canonical_key": "bs_equity__total_equity", "source_label": "Total equity",
             "values": [{"basis": "consolidated", "period_label": "current", "value": "100"}]}]
    assert _equity_closing(rows, "consolidated") is None
    assert [c["type"] for c in _accounting_checks(rows, [], "en")] == []


def test_a_sideways_row_is_read_in_the_order_it_is_printed():
    """The 180° trap. Both a correct quarter-turn and its opposite turn a constant-x column of
    words into a row, so "the words share a y band" cannot tell them apart — and the opposite one
    reverses the statement: its columns run total-equity-first and its movements run from the
    closing balance back to the opening. Direction is therefore asserted, not just alignment.

    A page whose words advance UP the page (``dir=(0,-1)``, reported as rotation 90 — a quarter
    turn clockwise brings it upright): along a printed row the word printed FIRST sits at the
    LARGER page y, and the NEXT printed row sits at a larger page x.
    """
    from app.services.pdf_extract import _to_reading_space

    first = _to_reading_space(BBox(x0=0.20, y0=0.70, x1=0.21, y1=0.80), 90)
    then = _to_reading_space(BBox(x0=0.20, y0=0.40, x1=0.21, y1=0.50), 90)
    next_row = _to_reading_space(BBox(x0=0.30, y0=0.70, x1=0.31, y1=0.80), 90)
    assert first.x1 <= then.x0                       # printed first ⇒ earlier across the row
    assert abs(first.y0 - then.y0) < 1e-9            # ...and on the same row
    assert next_row.y0 > first.y0                    # the next printed row is further down


def test_a_sideways_row_the_other_way_up_is_also_read_in_printed_order():
    """The mirror case, ``dir=(0,+1)`` (rotation 270): words advance DOWN the page, so the word
    printed first sits at the smaller y, and the next printed row sits at a smaller x."""
    from app.services.pdf_extract import _to_reading_space

    first = _to_reading_space(BBox(x0=0.30, y0=0.40, x1=0.31, y1=0.50), 270)
    then = _to_reading_space(BBox(x0=0.30, y0=0.70, x1=0.31, y1=0.80), 270)
    next_row = _to_reading_space(BBox(x0=0.20, y0=0.40, x1=0.21, y1=0.50), 270)
    assert first.x1 <= then.x0
    assert abs(first.y0 - then.y0) < 1e-9
    assert next_row.y0 > first.y0


def test_the_reported_angle_is_the_one_that_brings_the_page_upright():
    """``PageSource.rotation`` is filled from the PDF's ``/Rotate`` by the ingest stage and from
    the text's own direction by the extract stage, and the integrity report renders both with one
    sentence — so the two have to mean the same thing: the CLOCKWISE turn that makes the page
    readable. Text running up the page needs a quarter-turn clockwise; text running down needs
    three."""
    from app.services.pdf_extract import _dir_rotation

    assert _dir_rotation((1.0, 0.0)) == 0
    assert _dir_rotation((-1.0, 0.0)) == 180
    assert _dir_rotation((0.0, -1.0)) == 90
    assert _dir_rotation((0.0, 1.0)) == 270
    assert _dir_rotation(None) == 0                  # a line with no direction reads upright


class _FakePage:
    """Just enough of a PyMuPDF page: the word list and the line dict have to agree."""

    def __init__(self, lines):
        self._lines = lines          # (dir, [(x0, y0, x1, y1, text), …])

    def get_text(self, kind):
        if kind == "dict":
            return {"blocks": [{"lines": [
                {"dir": d, "bbox": (min(w[0] for w in ws), min(w[1] for w in ws),
                                    max(w[2] for w in ws), max(w[3] for w in ws)),
                 "spans": [{"text": " ".join(w[4] for w in ws)}]}
                for d, ws in self._lines]}]}
        assert kind == "words"
        return [(*w, 0, 0, 0) for _d, ws in self._lines for w in ws]


def test_a_sideways_page_drops_the_chrome_that_is_still_printed_upright():
    """A page that prints its statement sideways prints its page number, its running header and
    (an HKEX filing) the statement's own title the normal way up. Those words are a quarter-turn
    from the body, so putting them through the body's transform drops them into the middle of the
    matrix — inventing rows and gluing the running header onto a caption."""
    from app.services.pdf_extract import _native_words

    body = [(10.0, 60.0, 20.0, 70.0, "Retained"), (10.0, 40.0, 20.0, 50.0, "profits")]
    header = [(30.0, 90.0, 60.0, 95.0, "Annual"), (62.0, 90.0, 80.0, 95.0, "Report")]
    page = _FakePage([((0.0, -1.0), body), ((1.0, 0.0), header)])
    words = _native_words(page, 100.0, 100.0, rotation=90)
    assert [w.text for w in words] == ["Retained", "profits"]
    # An upright page has no other angle to disagree with, so nothing is dropped there.
    upright = _FakePage([((1.0, 0.0), body + header)])
    assert len(_native_words(upright, 100.0, 100.0, rotation=0)) == 4


def test_a_word_no_line_claims_is_kept():
    """The word list and the line boxes come from two different calls into the text extractor. A
    word no line covers has no direction to judge, and deleting a figure is worse than keeping a
    stray one — so an unclaimed word stays."""
    from app.services.pdf_extract import _native_words

    class _Mismatched:
        def get_text(self, kind):
            if kind == "dict":
                return {"blocks": [{"lines": [
                    {"dir": (0.0, -1.0), "bbox": (10.0, 40.0, 20.0, 70.0),
                     "spans": [{"text": "Retained profits"}]}]}]}
            return [(10.0, 60.0, 20.0, 70.0, "Retained", 0, 0, 0),
                    (90.0, 5.0, 95.0, 9.0, "orphan", 0, 0, 0)]

    got = [w.text for w in _native_words(_Mismatched(), 100.0, 100.0, rotation=90)]
    assert got == ["Retained", "orphan"]


def test_matrix_columns_are_ordered_as_printed_even_when_the_page_is_sideways():
    """On a sideways page the columns advance down the page's y, so ordering them by the value
    box's x0 — which is what the box now honestly reports — would return the statement's ROW axis.
    The printed position each fact recorded at extraction is what orders them."""
    from app.api.routes.documents import _matrix_columns

    # x0 ascending is deliberately the REVERSE of the printed order, as a rotated page's would be.
    rows = [{"source_label": "At 1 January 2023", "canonical_key": None, "values": [
        _val("Total equity", 3, x0=0.20, col=2),
        _val("Retained profits", 2, x0=0.50, col=1),
        _val("Issued capital", 1, x0=0.80, col=0)]}]
    assert _matrix_columns(rows, "consolidated") == [
        "Issued capital", "Retained profits", "Total equity"]
    # With no recorded position anywhere — an Excel matrix, or a run from before it was recorded
    # — the page geometry is still what there is.
    for v in rows[0]["values"]:
        v.pop("column_index")
    assert _matrix_columns(rows, "consolidated") == [
        "Total equity", "Retained profits", "Issued capital"]
