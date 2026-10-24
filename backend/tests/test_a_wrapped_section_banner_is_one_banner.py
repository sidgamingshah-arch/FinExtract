"""A SECTION BANNER WRAPPED ONTO A SECOND PRINTED LINE IS ONE BANNER.

China SCE (hkex_1966) sets its cash flow banners in a narrow caption column, so each one wraps:

    CASH FLOWS FROM OPERATING   經營活動現金流量
      ACTIVITIES

Each line was read as a banner of its own and the second replaced the first, so the operating,
investing and financing sections were all named "ACTIVITIES", which names no section. Two faults
followed. The mapper had no section to gate on, so the investing rows (purchases of property and
equipment, acquisitions and disposals of subsidiaries, restricted cash, pledged and time deposits)
went to other financing cash flows and interest paid and dividends paid to operating; financing
cash flow read -6,733,273 / -19,717,514 against the -13,389,527 / -21,253,827 the filing prints.
And the request text could not tell the sections apart: the investing total closed "ACTIVITIES",
and the financing rows after it, read under the same name, were sent under no section at all.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import PageKind
from app.core.models.geometry import BBox
from app.services import face_context
from app.services.mapping import section_of_banner
from app.services.row_reconstruct import Word, build_line_items

_W, _H = 595.0, 842.0
_CHAR, _SPACE = 4.3, 2.2
_LEFT, _HANG, _INDENT = 62.0, 66.0, 72.0
_HAN_X = 250.0
_CUR, _PRI = 425.0, 520.0
_STEP = 12.0


def _line(text: str, x0: float, y: float) -> list[Word]:
    out, x = [], x0
    for t in text.split():
        out.append(Word(text=t, bbox=BBox(x0=x / _W, y0=y / _H,
                                          x1=(x + _CHAR * len(t)) / _W, y1=(y + 8) / _H)))
        x += _CHAR * len(t) + _SPACE
    return out


def _right(text: str, x1: float, y: float) -> list[Word]:
    return _line(text, x1 - _CHAR * len(text), y)


def _page(*lines) -> list[Word]:
    """`lines` are (caption, x0), (caption, x0, han) or (caption, x0, current, prior)."""
    words: list[Word] = []
    y = 120.0
    words += _right("2023", _CUR, 100.0) + _right("2022", _PRI, 100.0)
    for spec in lines:
        caption, x0 = spec[0], spec[1]
        words += _line(caption, x0, y)
        if len(spec) == 3:
            words += _line(spec[2], _HAN_X, y)
        elif len(spec) == 4:
            words += _right(spec[2], _CUR, y) + _right(spec[3], _PRI, y)
        y += _STEP
    return words


def _items(statement: str, *lines):
    items, _ = build_line_items(_page(*lines), page_index=4, document_id=None,
                                source_kind="native", statement=statement, on_face=True)
    return items


_SCE_CASH_FLOW = (
    ("CASH FLOWS FROM OPERATING", _LEFT, "經營活動現金流量"),
    ("ACTIVITIES", _HANG),
    ("Profit/(loss) before tax", _INDENT, "(8,211,620)", "819,428"),
    ("Net cash flows from operating activities", _LEFT, "5,094,092", "16,429,204"),
    ("CASH FLOWS FROM INVESTING", _LEFT, "投資活動現金流量"),
    ("ACTIVITIES", _HANG),
    ("Purchases of items of property and equipment", _INDENT, "(72,602)", "(131,797)"),
    ("Decrease in restricted cash", _INDENT, "2,301,692", "407,615"),
    ("Net cash flows from investing activities", _LEFT, "4,044,304", "(2,604,642)"),
    ("CASH FLOWS FROM FINANCING", _LEFT, "融資活動現金流量"),
    ("ACTIVITIES", _HANG),
    ("Interest paid", _INDENT, "(2,085,544)", "(3,242,856)"),
    ("New bank and other borrowings", _INDENT, "942,574", "11,142,125"),
    ("Net cash flows used in financing activities", _LEFT, "(13,389,527)", "(21,253,827)"),
)


def test_a_wrapped_cash_flow_banner_is_read_whole():
    got = {li.source_label: li.section_hint for li in _items("cash_flow", *_SCE_CASH_FLOW)}
    assert got["Profit/(loss) before tax"] == "CASH FLOWS FROM OPERATING ACTIVITIES 經營活動現金流量"
    assert got["Purchases of items of property and equipment"] == (
        "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動現金流量")
    assert got["Interest paid"] == "CASH FLOWS FROM FINANCING ACTIVITIES 融資活動現金流量"


def test_each_wrapped_banner_names_its_own_section_for_the_mapper():
    """`section_hint` is what the mapper gates on: a row under "ACTIVITIES" was gated to nothing."""
    got = {li.source_label: section_of_banner(li.section_hint)
           for li in _items("cash_flow", *_SCE_CASH_FLOW)}
    assert got["Profit/(loss) before tax"] == "cash_flow_from_operating_activities"
    assert got["Decrease in restricted cash"] == "cash_flow_from_investing_activities"
    assert got["New bank and other borrowings"] == "cash_flow_from_financing_activities"
    assert got["Net cash flows used in financing activities"] == (
        "cash_flow_from_financing_activities")


def test_the_rows_after_the_investing_total_are_sent_under_financing():
    """The request text: before, the financing rows were read under the "ACTIVITIES" the investing
    total had closed, and were sent under no section."""
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=4, kind=PageKind.FACE, statement="cash_flow")]
    doc.line_items = _items("cash_flow", *_SCE_CASH_FLOW)
    sent = {r["caption"]: r.get("under")
            for r in face_context.face_rows(doc, {"cash_flow"})[0]["rows"]}
    financing = "CASH FLOWS FROM FINANCING ACTIVITIES 融資活動現金流量"
    assert sent["Interest paid"] == financing
    assert sent["New bank and other borrowings"] == financing
    assert sent["Decrease in restricted cash"] == "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動現金流量"


def test_a_banner_beneath_a_banner_is_not_its_tail():
    """"ASSETS" over "NON-CURRENT ASSETS": the second line names a section on its own."""
    got = {li.source_label: li.section_hint for li in _items(
        "balance_sheet",
        ("ASSETS", _LEFT),
        ("NON-CURRENT ASSETS", _LEFT),
        ("Investment properties", _INDENT, "100", "90"),
    )}
    assert got["Investment properties"] == "NON-CURRENT ASSETS"


def test_a_one_line_banner_over_an_all_caps_line_keeps_its_own_reading():
    """A banner printed whole on one line is complete: the ALL-CAPS line under it is not its tail,
    because the banner's own words already name the section."""
    got = {li.source_label: li.section_hint for li in _items(
        "cash_flow",
        ("CASH FLOWS FROM OPERATING ACTIVITIES", _LEFT),
        ("ADJUSTMENTS", _HANG),
        ("Finance costs", _INDENT, "1,490,853", "900,962"),
    )}
    assert got["Finance costs"] == "ADJUSTMENTS"


def test_a_banner_line_set_apart_from_the_line_above_is_not_its_tail():
    """A heading line printed a paragraph below the line above is not that line's continuation."""
    words = _page(("CASH FLOWS FROM FINANCING", _LEFT),)
    words += _line("ACTIVITIES", _HANG, 120.0 + 4 * _STEP)
    words += _line("Interest paid", _INDENT, 120.0 + 5 * _STEP)
    words += _right("(2,085,544)", _CUR, 120.0 + 5 * _STEP)
    words += _right("(3,242,856)", _PRI, 120.0 + 5 * _STEP)
    items, _ = build_line_items(words, page_index=4, document_id=None, source_kind="native",
                                statement="cash_flow", on_face=True)
    assert {li.source_label: li.section_hint for li in items}["Interest paid"] == "ACTIVITIES"
