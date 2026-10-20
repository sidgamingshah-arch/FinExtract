"""INSIDE A CHAPTER A MAINLAND NOTE NUMBER ONLY RISES (`notes_extract.extract_note_tables`).

000883's notes minted notes from things that are not notes: the 固定资产 note's movement rows
("2.本期增加金额", "4.期末余额", printed with their figures on the rows below) became 七、2 and
七、4, so 交易性金融资产's note number carried the fixed-asset movement table; construction projects
named by capacity ("99MW 农光互补", "2X1000MW 超超临界") became 七、99 and 七、2; and after the
cash-flow supplement, which is read as a face and resets the note carry, its sections 2、3、4、 became
notes and every real note after them was out of reach. A heading whose number does not step
forward from the open note is a row of that note; a page with no carry continues from the last note
its chapter read.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word

H = 0.0107


def _w(text: str, x0: float, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _read(words, chapter, carry=None):
    return extract_note_tables(words, page_index=187, document_id="d", source_kind="native",
                               chapter=chapter, carry_note=carry)


def _header(y):
    return [_w("项目", 0.198, 0.228, y), _w("期末余额", 0.533, 0.600, y),
            _w("期初余额", 0.735, 0.800, y)]


def _figures(label, a, b, y):
    return [_w(label, 0.151, 0.300, y), _w(a, 0.560, 0.664, y), _w(b, 0.790, 0.892, y)]


def test_a_numbered_movement_row_stays_in_its_note():
    words = ([_w("21、", 0.151, 0.180, 0.100), _w("固定资产", 0.186, 0.260, 0.100)] + _header(0.130)
             + _figures("固定资产", "65,174,285,084.63", "57,867,322,351.65", 0.150)
             + [_w("2.本期增加金额", 0.151, 0.300, 0.180)]
             + _figures("（1）购置", "1,000.00", "2,000.00", 0.200))
    tables = _read(words, ["七", 7, "合并财务报表项目注释"])
    assert {t.note_number for t in tables if t.items} == {"七、21"}, [
        (t.note_number, t.title) for t in tables]


def test_a_project_named_by_its_capacity_is_not_a_note():
    words = ([_w("22、", 0.151, 0.180, 0.100), _w("在建工程", 0.186, 0.260, 0.100)] + _header(0.130)
             + [_w("99MW", 0.151, 0.200, 0.160), _w("农光互补", 0.205, 0.280, 0.160)]
             + _figures("项目", "7,254,899,558.00", "7,254,891,162.25", 0.180))
    tables = _read(words, ["七", 7, "合并财务报表项目注释"])
    assert {t.note_number for t in tables} == {"七、22"}, [(t.note_number, t.title) for t in tables]


def test_a_page_with_no_carry_continues_from_the_chapters_last_note():
    chapter = ["七", 7, "合并财务报表项目注释", "78"]
    words = ([_w("2、", 0.151, 0.170, 0.100), _w("本期支付的取得子公司的现金净额", 0.175, 0.450, 0.100)]
             + _header(0.130) + _figures("购买子公司", "163,089,997.81", "476,764,436.98", 0.150)
             + [_w("81、", 0.151, 0.180, 0.300), _w("外币货币性项目", 0.186, 0.300, 0.300)]
             + _header(0.330) + _figures("货币资金", "6,568,662.00", "51,823.11", 0.350))
    tables = _read(words, chapter)
    got = [(t.note_number, t.title.lstrip("、 ")) for t in tables if t.items]
    assert got == [("七、78", "本期支付的取得子公司的现金净额"), ("七、81", "外币货币性项目")], got
    assert chapter[3] == "81", "the chapter remembers the note it ended on"


def test_a_filing_with_no_chapters_numbers_as_it_always_did():
    words = ([_w("21", 0.151, 0.170, 0.100), _w("PROPERTY", 0.186, 0.300, 0.100)] + _header(0.130)
             + _figures("Buildings", "1,000", "900", 0.150)
             + [_w("2", 0.151, 0.170, 0.300), _w("REVENUE", 0.186, 0.300, 0.300)] + _header(0.330)
             + _figures("Sales", "5,000", "4,000", 0.350))
    tables = _read(words, [None, 0, ""])
    assert [t.note_number for t in tables if t.items] == ["21", "2"]
