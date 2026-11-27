"""Language-detection stage.

Sets the document's primary ``locale`` (drives OCR pack selection, locale-aware
number parsing, and ontology alias selection). A dependency-free script-based
heuristic covers the seed set (en / zh / ar / fr); a statistical detector
(``fasttext``) can be plugged in behind the ``lang`` extra for finer results.
"""
from __future__ import annotations

import re

from app.core.models import DocumentModel
from app.core.stage import PipelineContext

_ARABIC = re.compile(r"[؀-ۿ]")
_CJK = re.compile(r"[一-鿿]")
_FRENCH_HINT = re.compile(
    r"\b(bilan|actif|passif|produits|charges|résultat|capitaux|trésorerie)\b", re.I
)
_FRENCH_ACCENTS = re.compile(r"[àâçéèêëîïôûùüÿœ]", re.I)


# THE WORDS A TEXT IS WRITTEN IN, not the letters its names are spelled with. Three accented
# letters made a document French, and an English report from Mauritius prints them in its names —
# Lux Island Resorts 2025 ("BÉGA", "Floréal", "Réunion") came out locale=fr, so no English alias was
# used ("alias_locale_coverage locale=fr matchable=519 with_alias=0") and not one statement row was
# mapped by its caption. The accents and the statement words still say "perhaps French"; the
# commonest function words of each language decide it.
_ENGLISH_WORDS = re.compile(r"\b(?:the|and|of|for|with|to|is|are|was|were|this|that|our|by)\b",
                            re.I)
_FRENCH_WORDS = re.compile(r"\b(?:le|la|les|des|du|et|pour|avec|dans|est|sont|une|aux|nous|sur)\b",
                           re.I)


def detect_locale(text: str) -> str:
    if not text:
        return "en"
    if _ARABIC.search(text):
        return "ar"
    if _CJK.search(text):
        return "zh"
    if _FRENCH_HINT.search(text) or len(_FRENCH_ACCENTS.findall(text)) >= 3:
        if len(_FRENCH_WORDS.findall(text)) >= len(_ENGLISH_WORDS.findall(text)):
            return "fr"
    return "en"


class LanguageDetectStage:
    name = "language_detect"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        # Sample text from native pages (OCR text is added later for scanned docs).
        sample = ""
        data = ctx.raw_bytes
        if data and doc.fmt.value == "pdf":
            try:
                import fitz

                pdf = fitz.open(stream=data, filetype="pdf")
                for page in pdf:
                    sample += (page.get_text("text") or "")[:2000]
                    if len(sample) > 4000:
                        break
                pdf.close()
            except Exception:  # noqa: BLE001
                pass
        doc.locale = detect_locale(sample)
        ctx.log(f"language:locale={doc.locale}")
        return doc
