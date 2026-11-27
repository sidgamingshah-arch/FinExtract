"""A STATEMENT OF CHANGES IN EQUITY PRINTING THE GROUP'S TABLE OVER THE COMPANY'S IS TWO TABLES.

A Mauritian filing prints both entities' equity statements on one page, each under its own
heading ("THE GROUP" / "THE COMPANY") and column header. Read as one matrix with one basis:

* Moroil 2025 (printed page 70): the group's seven columns and the company's five clustered into
  eight bands, one of them nothing but nil dashes with no heading, so the matrix was unnamed and
  the page read NO rows in any run; its dividends of 43,264 reached no line.
* Phoenix Beverages 2025 (p130): the 51 company values were filed as the group's.

Now each table is read on the basis its own heading names
(`row_reconstruct._equity_entity_blocks`), the notes column is left out of the captions ("Dividends
31" -> "Dividends", "Transfer 17(b)" -> "Transfer"), and the movements read down the
retained-earnings column are kept apart by basis (`equity_matrix.collapse_movement_rows`):
Phoenix's 2024 other comprehensive loss there is the group's (22,270), not (22,270) + (22,116).
(Moroil's balances are dated month first, "Balance at July 1, 2024"; read as dates they would
start the transpose on every filing that dates its equity statement so, and on Laurus Labs and
Chemplast Sanmar, whose balance sheets print one lumped "Other equity", the transposed components
were added beside it. Not done: Moroil's movements still reach no line.)
Figures as printed, in Rs'000 / MUR '000.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.enums import Basis, PageKind

BACKEND = pathlib.Path(__file__).resolve().parent.parent
FILINGS = BACKEND.parent / "testdata" / "filings"
SEED = BACKEND / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"


def _read(stem: str, pages: tuple[int, ...]):
    pdf = FILINGS / f"{stem}.pdf"
    if not pdf.exists():
        pytest.skip(f"{pdf.name} not present")
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view
    from app.stages.classify import ClassifyStage
    from app.stages.extract import ExtractStage
    from app.stages.ingest import IngestStage
    from app.stages.integrity import IntegrityStage
    from app.stages.language import LanguageDetectStage

    doc = DocumentModel(filename=pdf.name)
    ctx = PipelineContext(raw_bytes=pdf.read_bytes())
    logs: list[str] = []
    ctx.log = logs.append
    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    ctx.ontology, ctx.line_items = build_working_view(shipped), shipped
    for stage in (IngestStage(), IntegrityStage(), LanguageDetectStage(), ClassifyStage()):
        doc = stage.run(doc, ctx)
    for page in doc.pages:
        if page.index not in pages and page.kind in (PageKind.FACE, PageKind.NOTES):
            page.kind = PageKind.OTHER
    doc = ExtractStage().run(doc, ctx)
    return doc, logs


@pytest.fixture(scope="module")
def moroil():
    return _read("sem_moroil_2025", (68,))


@pytest.fixture(scope="module")
def phoenix():
    return _read("sem_phoenix_beverages_2025", (66,))


def _matrix_rows(doc, caption: str):
    return [li for li in doc.line_items if li.source_label == caption and li.values
            and all(getattr(ev, "column_index", None) is not None for ev in li.values.values())]


def _bases(li) -> set[Basis]:
    return {ev.basis for ev in li.values.values()}


def test_moroils_two_tables_are_read_each_on_its_own_basis(moroil):
    doc, logs = moroil
    assert any(line.startswith("extract:page=68:equity_entity_blocks=consolidated(")
               and ",company(" in line for line in logs), [l for l in logs if "page=68" in l]
    dividends = _matrix_rows(doc, "Dividends")
    assert sorted(tuple(sorted(b.value for b in _bases(li))) for li in dividends) == [
        ("consolidated",), ("consolidated",), ("standalone",), ("standalone",)]
    assert not [li for li in doc.line_items if (li.source_label or "").startswith("Dividends 31")]
    assert not [li for li in doc.line_items if (li.source_label or "").startswith("Transfer 17")]


def test_phoenixs_company_table_is_the_companys(phoenix):
    doc, _logs = phoenix
    profit = _matrix_rows(doc, "Profit for the year")
    by_basis = {next(iter(_bases(li))): max(ev.value for ev in li.values.values())
                for li in profit if len(_bases(li)) == 1}
    assert len(profit) == 4
    assert {b.value for b in by_basis} == {"consolidated", "standalone"}
    assert Decimal("736426") in [max(ev.value for ev in li.values.values()) for li in profit
                                 if _bases(li) == {Basis.STANDALONE}]


def test_phoenixs_movements_are_kept_apart_by_basis(phoenix):
    from app.services.equity_matrix import MOVEMENT_FLAG

    doc, _logs = phoenix
    got: dict = {}
    for li in doc.line_items:
        for ev in li.values.values():
            if MOVEMENT_FLAG in ev.confidence.flags and (li.source_label or "").startswith(
                    "Other comprehensive income"):
                got[(ev.basis.value, ev.period_label)] = ev.value
    assert got[("consolidated", "prior")] == Decimal("-22270")
    assert got[("standalone", "prior")] == Decimal("-22116")
    assert got[("consolidated", "current")] == Decimal("72569")
    dividends = [li for li in doc.line_items if li.source_label == "Dividends"
                 and any(MOVEMENT_FLAG in ev.confidence.flags for ev in li.values.values())]
    assert len(dividends) == 1
    assert {(ev.basis.value, ev.period_label): ev.value for ev in dividends[0].values.values()} == {
        ("consolidated", "current"): Decimal("-386505"),
        ("consolidated", "prior"): Decimal("-368413"),
        ("standalone", "current"): Decimal("-386505"),
        ("standalone", "prior"): Decimal("-368413")}
