"""Document extraction engine port: a whole PDF in, every page's positioned words out.

Distinct from ``ports.ocr``: an OCR engine is handed one rendered page image per call, a document
engine (Kensho Extract) is handed the PDF once and answers for every page. Both answer in the
same unit — words with normalized [0,1] top-left boxes — so the readers downstream treat a page
the same whichever read it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.ports.ocr import OcrWord


@runtime_checkable
class DocumentEngine(Protocol):
    id: str

    def read_pdf(self, data: bytes) -> dict[int, list[OcrWord]]:
        """Every page the engine read, by 0-based page index. A page it returned no text for
        is absent, not an empty list, so the caller can fall back to its own reading."""
        ...
