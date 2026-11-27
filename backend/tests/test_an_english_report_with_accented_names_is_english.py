"""AN ENGLISH REPORT WHOSE NAMES CARRY ACCENTS IS ENGLISH.

NEW FILE -> backend/tests/test_an_english_report_with_accented_names_is_english.py

`stages.language.detect_locale` made a document French when its first ~4,000 characters held three
accented letters. An English report from Mauritius prints them in its names — Lux Island Resorts
2025 ("BÉGA", "Floréal", "Réunion") came out locale=fr, so no English alias was used
("alias_locale_coverage locale=fr matchable=519 with_alias=0") and not one statement row was
mapped by its caption. The accents and the French statement words still say "perhaps French"; the
commonest function words of each language decide it.
"""
from __future__ import annotations

import pathlib

import pytest

from app.stages.language import detect_locale

BACKEND = pathlib.Path(__file__).resolve().parent.parent
LUX = BACKEND.parent / "testdata" / "filings" / "sem_lux_island_resorts_2025.pdf"


def test_accented_names_in_an_english_text_do_not_make_it_french():
    text = ("The Board of Directors of LUX ISLAND RESORTS LTD is pleased to present the integrated "
            "annual report of the Group for the year ended 30 June 2025. Our hotels in Réunion, "
            "the BÉGA team at Floréal and the resorts of the Maldives are reviewed in this report.")
    assert detect_locale(text) == "en"


def test_a_french_text_is_still_french():
    text = ("Le conseil d'administration présente le rapport annuel de la société et les états "
            "financiers du groupe pour l'exercice clos le 30 juin 2025. Le bilan et le compte de "
            "résultat sont présentés dans les notes.")
    assert detect_locale(text) == "fr"


@pytest.mark.skipif(not LUX.exists(), reason=f"{LUX.name} not present")
def test_lux_island_resorts_reads_as_english():
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.stages.ingest import IngestStage
    from app.stages.language import LanguageDetectStage

    doc = DocumentModel(filename=LUX.name)
    ctx = PipelineContext(raw_bytes=LUX.read_bytes())
    logs: list[str] = []
    ctx.log = logs.append
    doc = IngestStage().run(doc, ctx)
    assert doc.fmt.value == "pdf"
    assert LanguageDetectStage().run(doc, ctx).locale == "en"
    assert "language:locale=en" in logs
