"""A COMBINED TRADE AND OTHER PAYABLES ROW IS SPLIT BY ITS NOTE — THE USER'S DECISION (r9).

The user's words: "Trade payables split from the note. Where the face prints only a COMBINED row
for trade and other payables ... and the note prints the trade-payables part, Trade Payables(CP)
takes the note's trade part, and the combined line takes the printed combined row LESS that part (a
derived remainder; the user accepted that)."

Measured on the HKEX filings (answer-free captures at 985bc8c), four print that shape:

    filing        face row (current / prior)                                       note's trade part
    佳明 kaming   'Trade and other payables' 462,962 / 412,613                      note 20 272,534 / 157,164
    PetroChina    'Accounts payable and accrued liabilities' 338,513 / 363,650      note 27 160,002 / 170,441
    Lai Sun       'Creditors, other payables and accruals' 2,367,430 / 2,484,740    note 34 'Creditors' 346,061 / 286,277
    Hon Kwok      'Trade payables, other payables and accrued liabilities'          note 24 ageing 8,852 / 7,492
                  30,221 / 132,348

Three of the four face rows bound no line at all — they were swept into Other Current Liabilities,
although Trade and other payables(CP)'s definition names their captions — so the line now carries
them as aliases. BYD, China SCE and Far East Horizon print a trade payables row of their own, and
every CAS face prints 应付账款 on its own row: nothing moves there.

Two note parts read the trade part: the note's own trade payables row, and — where the note prints
none, as Hon Kwok's does — the ageing analysis of the trade payables. Trade Payables(CP) takes the
part (`first` over the part and the combined line, both REQUIRED, so it moves only beside a combined
row), and Trade and other payables(CP)'s `deducts_from_printed` rung takes the same part out of the
printed row, flagged `remainder_of_printed`. Total current liabilities cannot move.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import CascadeRung, load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.periods import concept_value
from app.services.working_view import build_working_view
from app.stages.note_sourced import (NOT_DEDUCTED_FROM_PRINTED, REMAINDER_OF_PRINTED,
                                     NoteSourcedStage)

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"

TP = "bs_cl__trade_payables_cp"
TOP = "bs_cl__trade_and_other_payables_cp"
ROW = "sub__tp_note_trade_payables"
AGE = "sub__tp_note_trade_payables_ageing"
CL = "CURRENT LIABILITIES"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _items(shipped):
    return {i.key: i for i in shipped.items}


# ── the configuration ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("part", [ROW, AGE])
def test_each_part_is_an_internal_note_part_wired_into_both_lines(shipped, part):
    items = _items(shipped)
    p = items[part]
    assert p.namespace == "internal" and p.in_output is False
    assert p.parent == TP
    assert str(getattr(p.statement, "value", p.statement)) == "notes"
    assert list(p.section_scope or []) == []
    assert p.route == "note_tables"
    # No face alias: no caption on any statement can bind a note part.
    assert not p.aliases and not any((p.aliases_i18n or {}).values())
    for line in (TP, TOP):
        refs = {t.ref for rung in items[line].cascade for t in rung.terms}
        assert part in refs, (line, refs)


def test_trade_payables_takes_the_part_only_beside_a_combined_row(shipped):
    rungs = _items(shipped)[TP].cascade
    assert [r.id for r in rungs] == ["TRADE_PART_OF_THE_COMBINED_ROW", "TRADE_PART_BY_ITS_AGEING"]
    for rung, part in zip(rungs, (ROW, AGE)):
        assert rung.terms_op == "first"
        assert [t.ref for t in rung.terms] == [part, TOP]
        assert all(t.role == "required" and t.sign == 1 for t in rung.terms)
        # A trade payables row the face prints of its own stands over the note's part.
        assert rung.outranks_printed is False and not rung.carved_from_face
        assert not rung.deducts_from_printed


def test_the_combined_line_takes_the_same_part_out_of_its_printed_row(shipped):
    rungs = _items(shipped)[TOP].cascade
    assert [r.id for r in rungs] == ["LESS_THE_TRADE_PART", "LESS_THE_TRADE_PART_BY_ITS_AGEING"]
    for rung, part in zip(rungs, (ROW, AGE)):
        assert rung.deducts_from_printed is True and rung.refuse_negative is True
        assert [(t.ref, t.role, t.sign) for t in rung.terms] == [(part, "required", 1)]


def test_both_lines_stay_face_lines(shipped):
    items = _items(shipped)
    assert str(items[TP].type) == "extracted" and str(items[TOP].type) == "extracted"
    assert "应付账款" in (items[TP].aliases_i18n or {}).get("zh", [])


def test_a_rung_that_deducts_from_the_printed_row_cannot_also_displace_it():
    with pytest.raises(ValueError):
        CascadeRung(id="X", terms=[{"ref": ROW}], deducts_from_printed=True, outranks_printed=True)
    with pytest.raises(ValueError):
        CascadeRung(id="X", terms=[{"ref": ROW}], deducts_from_printed=True, adds_to_printed=True)


@pytest.mark.parametrize("caption", [
    "Accounts payable and accrued liabilities",          # PetroChina
    "Creditors, other payables and accruals",            # Lai Sun
    "Trade payables, other payables and accrued liabilities",   # Hon Kwok
    "Trade and other payables 應付賬款及其他應付款項",        # 佳明
])
def test_the_combined_rows_bind_the_combined_line(shipped, caption):
    """The definition names these captions; three of them were swept into Other Current
    Liabilities, where no split could reach them."""
    matcher = OntologyMatcher(build_working_view(shipped))
    res = matcher.match(caption, statement="balance_sheet", section=CL)
    assert res is not None and res.canonical_key == TOP, (caption, res and res.canonical_key)


def test_a_trade_payables_row_still_binds_trade_payables(shipped):
    matcher = OntologyMatcher(build_working_view(shipped))
    for caption in ("Trade payables", "Trade and bills payables 貿易應付款項及票據"):
        res = matcher.match(caption, statement="balance_sheet", section=CL)
        assert res is not None and res.canonical_key == TP, caption


@pytest.mark.parametrize("key", [TP, TOP])
def test_the_definitions_say_it_and_the_ontology_carries_them(shipped, key):
    item = _items(shipped)[key]
    concepts = {m["canonical_key"]: m for m in
                json.loads(ONTOLOGY.read_text(encoding="utf-8"))["mappings"]}
    assert concepts[key]["definition"] == item.definition
    assert concepts[key]["aliases"] == item.aliases
    text = item.definition
    assert "Accounts payable and accrued liabilities" in text
    assert "Creditors, other payables and accruals" in text
    if key == TOP:
        assert "less the trade payables" in text and "remainder" in text
        assert "Do not take the trade payables out yourself" in text
    else:
        assert "takes the same figure out of 'Trade and other payables(CP)'" in text


# ── the stage ──────────────────────────────────────────────────────────────────────────────────

def _value(amount: str, period: str, page: int):
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period, value=Decimal(amount),
                          value_raw=Decimal(amount), provenance=Provenance(page_index=page))


def _note(number: str, title: str, rows, page: int) -> NotesTable:
    items = []
    for caption, (current, prior), *group in rows:
        item = NoteItem(raw_label=caption, group_hint=(group[0] if group else ""))
        if current is not None:
            item.values["c"] = _value(current, "current", page)
        if prior is not None:
            item.values["p"] = _value(prior, "prior", page)
        items.append(item)
    return NotesTable(note_number=number, title=title, items=items, source_pages=[page],
                      source_text=title)


def _face(key: str, caption: str, current: str, prior: str, note: str) -> LineItem:
    row = LineItem(source_label=caption, canonical_key=key, note_number=note)
    row.values["c"] = _value(current, "current", 63)
    row.values["p"] = _value(prior, "prior", 63)
    return row


def _run(shipped, face_rows, notes) -> DocumentModel:
    doc = DocumentModel(filename="payables.pdf")
    doc.notes = notes
    doc.line_items = list(face_rows)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx)


def _figure(doc, key, period="current"):
    rows = []
    for li in doc.line_items:
        if li.canonical_key != key:
            continue
        row = li.model_dump(mode="json")
        row["values"] = list((row.get("values") or {}).values())
        rows.append(row)
    got = concept_value(rows, "consolidated", period)
    return None if got is None else Decimal(str(got)).quantize(Decimal("1"))


KAMING_20 = [
    ("Trade payables 應付賬款", ("272534", "157164")),
    ("Payable for additions to investment properties 添置投資物業應付款項", ("2461", "74750")),
    ("Payable for additions to inventories of properties 添置物業存貨應付款項", ("1000", "11250")),
    ("Other payables and accrued charges 其他應付款項及應計費用", ("41949", "24676")),
    ("Rental and other deposits (note (ii)) 租金及其他按金（註(ii))", ("5930", "5210")),
    ("Rent receipts in advance 預收租金", ("77127", "86980")),
    ("Retentions payables (note (iii)) 應付保留款項（註(iii))", ("61961", "52583")),
]
KAMING_20_AGEING = [
    ("Less than 1 month 少於一個月", ("9643", "71363")),
    ("More than 1 month but less than 3 months 超過一個月但少於三個月", ("57171", "59183")),
    ("More than 3 months but less than 6 months 超過三個月但少於六個月", ("98220", "26618")),
    ("More than 6 months 超過六個月", ("107500", None)),
]


def _kaming_notes():
    return [_note("20", "TRADE AND OTHER PAYABLES 20. 應付賬款及其他應付款項", KAMING_20, 124),
            _note("20", "TRADE AND OTHER PAYABLES (continued) 20. 應付賬款及其他應付款項（續）",
                  KAMING_20_AGEING, 125)]


def test_the_combined_row_gives_up_the_trade_part_its_note_prints(shipped):
    """佳明 note 20: the trade row is read (the ageing analysis is the same money and is not added)."""
    face = _face(TOP, "Trade and other payables 應付賬款及其他應付款項", "462962", "412613", "20")
    doc = _run(shipped, [face], _kaming_notes())
    assert (_figure(doc, TP), _figure(doc, TP, "prior")) == (Decimal("272534"), Decimal("157164"))
    assert (_figure(doc, TOP), _figure(doc, TOP, "prior")) == (Decimal("190428"), Decimal("255449"))
    # Together they are the printed row, so Total Current Liabilities does not move.
    assert _figure(doc, TP) + _figure(doc, TOP) == Decimal("462962")
    assert _figure(doc, TP, "prior") + _figure(doc, TOP, "prior") == Decimal("412613")
    # A REMAINDER, and it says so: the printed figure stays on its row and in the trail.
    assert face.values["c"].value_raw == Decimal("462962")
    assert REMAINDER_OF_PRINTED in face.values["c"].confidence.flags
    assert any(f.startswith(f"{REMAINDER_OF_PRINTED}:consolidated:current:462962-272534")
               for f in face.confidence.flags)
    trail = face.derivation["consolidated:current"]
    assert REMAINDER_OF_PRINTED in trail["flags"]
    assert [i["used"] for i in trail["inputs"]] == ["462962", "-272534"]
    assert str(trail["result"]) == "190428"


def test_petrochinas_accounts_payable_and_accrued_liabilities_is_split_by_note_27(shipped):
    face = _face(TOP, "Accounts payable and accrued liabilities", "338513", "363650", "27")
    note = _note("27", "ACCOUNTS PAYABLE AND ACCRUED LIABILITIES", [
        ("Trade payable", ("160002", "170441")),
        ("Salaries and welfare payable", ("8095", "8592")),
        ("Dividends payable", ("265", "470")),
        ("Notes payable", ("14895", "20731")),
        ("Construction fee and equipment cost payable", ("112783", "119492")),
        ("Others (i)", ("42473", "43924")),
    ], 278)
    doc = _run(shipped, [face], [note])
    assert (_figure(doc, TP), _figure(doc, TP, "prior")) == (Decimal("160002"), Decimal("170441"))
    assert (_figure(doc, TOP), _figure(doc, TOP, "prior")) == (Decimal("178511"), Decimal("193209"))


def test_lai_suns_creditors_subtotal_is_the_trade_part_and_its_ageing_is_not_added(shipped):
    face = _face(TOP, "Creditors, other payables and accruals", "2367430", "2484740", "34")
    note = _note("34", "CREDITORS, OTHER PAYABLES AND ACCRUALS", [
        ("Not yet due or less than 30 days past due", ("243925", "217298"), "Creditors:"),
        ("31 — 60 days past due", ("34577", "21825"), "Creditors:"),
        ("61 — 90 days past due", ("7823", "9629"), "Creditors:"),
        ("Over 90 days past due", ("59736", "37525"), "Creditors:"),
        ("Creditors", ("346061", "286277"), "Creditors:"),
        ("Other payables and accruals", ("2029660", "1993534"), "Creditors:"),
        ("Put option liabilities (Note)", ("899059", "1104043"), "Creditors:"),
        ("Less: Portion classified as current", ("-2367430", "-2484740"), "Creditors:"),
        ("Non-current portion", ("907350", "899114"), "Creditors:"),
    ], 296)
    doc = _run(shipped, [face], [note])
    assert (_figure(doc, TP), _figure(doc, TP, "prior")) == (Decimal("346061"), Decimal("286277"))
    assert (_figure(doc, TOP), _figure(doc, TOP, "prior")) == (Decimal("2021369"),
                                                               Decimal("2198463"))


def test_hon_kwoks_note_prints_the_trade_part_only_as_its_ageing(shipped):
    face = _face(TOP, "Trade payables, other payables and accrued liabilities", "30221", "132348",
                 "24")
    note = _note("24", "TRADE PAYABLES, OTHER PAYABLES AND ACCRUED LIABILITIES",
                 [("Within 30 days", ("8852", "7492"))], 130)
    doc = _run(shipped, [face], [note])
    assert (_figure(doc, TP), _figure(doc, TP, "prior")) == (Decimal("8852"), Decimal("7492"))
    assert (_figure(doc, TOP), _figure(doc, TOP, "prior")) == (Decimal("21369"), Decimal("124856"))
    assert face.derivation["consolidated:current"]["method"] == \
        "cascade:LESS_THE_TRADE_PART_BY_ITS_AGEING"


def test_a_trade_payables_row_of_its_own_stands_and_the_combined_row_keeps_its_money(shipped):
    """The face prints trade payables on a row of its own: neither line moves."""
    trade = _face(TP, "Trade payables 應付賬款", "270000", "150000", "20")
    face = _face(TOP, "Trade and other payables 應付賬款及其他應付款項", "462962", "412613", "20")
    doc = _run(shipped, [trade, face], _kaming_notes())
    assert _figure(doc, TP) == Decimal("270000")
    assert _figure(doc, TOP) == Decimal("462962")
    assert REMAINDER_OF_PRINTED not in face.values["c"].confidence.flags


def test_without_a_combined_row_the_note_part_reaches_no_line(shipped):
    """The combined row bound nothing (it stayed in a residual): taking the part would count it
    twice, so Trade Payables(CP) does not take it."""
    swept = _face("bs_cl__other_current_liabilities", "Trade and other payables", "462962",
                  "412613", "20")
    doc = _run(shipped, [swept], _kaming_notes())
    assert _figure(doc, TP) is None
    assert _figure(doc, "bs_cl__other_current_liabilities") == Decimal("462962")


def test_a_part_larger_than_the_row_moves_neither_line(shipped):
    face = _face(TOP, "Trade and other payables 應付賬款及其他應付款項", "200000", "412613", "20")
    doc = _run(shipped, [face], _kaming_notes())
    # Current: 272,534 is more than the 200,000 printed — not the row's breakdown. Nothing moves.
    assert _figure(doc, TOP) == Decimal("200000") and _figure(doc, TP) is None
    part = next(li for li in doc.line_items if li.canonical_key == ROW)
    current = next(ev for ev in part.values.values() if ev.period_label == "current")
    assert NOT_DEDUCTED_FROM_PRINTED in current.confidence.flags
    # Prior is a real split and moves as a pair.
    assert (_figure(doc, TP, "prior"), _figure(doc, TOP, "prior")) == (Decimal("157164"),
                                                                       Decimal("255449"))


@pytest.mark.parametrize("title,rows", [
    ("TRADE AND BILLS PAYABLES", [("Trade payables", ("7865619", "9509889"))]),
    ("应付账款", [("1年以内", ("1000.00", "900.00"))]),
    ("ACCOUNTS PAYABLE", [("Within 1 year", ("246807", "257840"))]),
])
def test_a_note_that_is_not_a_combined_payables_note_is_not_read(shipped, title, rows):
    face = _face(TOP, "Trade and other payables", "462962", "412613", "30")
    doc = _run(shipped, [face], [_note("30", title, rows, 150)])
    assert _figure(doc, TOP) == Decimal("462962") and _figure(doc, TP) is None


# ── one declared split per row ─────────────────────────────────────────────────────────────────

def _itemised(shipped, *, line_items):
    """MapOntologyStage's note-itemisation arm over PetroChina's face row and note 27."""
    from app.core.models.document import PageSource
    from app.core.models.line_item import NoteRef
    from app.stages.map_ontology import MapOntologyStage

    ontology = build_working_view(shipped)
    template = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(
        encoding="utf-8"))
    face = LineItem(source_label="Accounts payable and accrued liabilities", canonical_key=TOP,
                    ordinal=0, note_number="27", section_hint=CL,
                    note_refs=[NoteRef(raw="27", numbers=["27"])])
    face.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                  value=Decimal("338513"), value_raw=Decimal("338513"),
                                  provenance=Provenance(page_index=0)))
    note = NotesTable(note_number="27", title="ACCOUNTS PAYABLE AND ACCRUED LIABILITIES",
                      source_pages=[1])
    for caption, amount in (("Trade payable", "160002"), ("Dividends payable", "265"),
                            ("Notes payable", "14895"), ("Others (i)", "163351")):
        item = NoteItem(raw_label=caption)
        item.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                      value=Decimal(amount), value_raw=Decimal(amount),
                                      provenance=Provenance(page_index=1)))
        note.items.append(item)
    doc = DocumentModel(filename="0857.pdf", line_items=[face], notes=[note],
                        pages=[PageSource(index=0, statement="balance_sheet")])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology, ctx.template = ontology, template
    if line_items is not None:
        ctx.line_items = line_items
    added = MapOntologyStage()._split_from_disclosure(
        doc, ontology, OntologyMatcher(ontology, llm_provider=None), ctx)
    return doc, ctx, added


def test_the_note_does_not_itemise_a_row_whose_line_declares_its_own_split(shipped):
    """Measured on PetroChina once its combined row bound the line: the generic itemisation also
    split 'Dividends payable' 265 out of note 27, so the line was the printed row less TWO parts,
    in scale 1 beside the statement's RMB millions. The declared split is the row's only one."""
    doc, ctx, added = _itemised(shipped, line_items=shipped)
    assert added == 0
    assert [li.canonical_key for li in doc.line_items] == [TOP]
    assert any(line.startswith(f"map_line_items:split_declined({TOP}): its cascade declares")
               for line in ctx.logs)


def test_without_the_declared_split_the_same_row_would_be_itemised(shipped):
    """The control: the same row and note with no line-item set on the context (no declaration to
    read) is itemised as before — which is what the declaration above stops."""
    doc, ctx, added = _itemised(shipped, line_items=None)
    assert added >= 1
    assert "bs_cl__dividends_payable" in {li.canonical_key for li in doc.line_items}


# ── the model's route ──────────────────────────────────────────────────────────────────────────

def _model_answer(key: str, current: str, prior: str, page: int = 124) -> LineItem:
    row = LineItem(source_label="Trade payables", canonical_key=key, note_number="20")
    row.confidence.method = "llm"
    row.values["c"] = _value(current, "current", page)
    row.values["p"] = _value(prior, "prior", page)
    return row


def test_a_model_answer_that_is_the_part_still_moves_it_off_the_combined_row(shipped):
    face = _face(TOP, "Trade and other payables 應付賬款及其他應付款項", "462962", "412613", "20")
    answer = _model_answer(TP, "272534", "157164")
    doc = _run(shipped, [face, answer], _kaming_notes())
    assert (_figure(doc, TP), _figure(doc, TOP)) == (Decimal("272534"), Decimal("190428"))


def test_a_model_answer_that_is_not_the_part_leaves_the_combined_row_whole(shipped):
    """The model answered Trade Payables(CP) with another figure; its cascade defers to that
    answer and does not take the part, so the combined row must not give it up either."""
    face = _face(TOP, "Trade and other payables 應付賬款及其他應付款項", "462962", "412613", "20")
    answer = _model_answer(TP, "100000", "90000")
    doc = _run(shipped, [face, answer], _kaming_notes())
    assert (_figure(doc, TP), _figure(doc, TOP)) == (Decimal("100000"), Decimal("462962"))
    assert REMAINDER_OF_PRINTED not in face.values["c"].confidence.flags
