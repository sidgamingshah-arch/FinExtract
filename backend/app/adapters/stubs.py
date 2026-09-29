"""Stub adapters.

These honour the port protocols but do no real work — they keep the app and tests
runnable without the heavy OCR/LLM dependencies. Each raises or returns an
empty/neutral result so a mis-wired pipeline fails loudly rather than silently.
"""
from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel

from app.ports.llm import LlmMessage, LlmMeta
from app.ports.ocr import OcrResult


class StubOcrProvider:
    id = "stub"

    def recognize(self, image_bytes: bytes, *, lang: str = "en") -> OcrResult:
        raise NotImplementedError(
            "OCR requested but no OCR engine is configured. Install the 'ocr' extra "
            "and set FINEX_OCR_PROVIDER to a real adapter (e.g. 'paddle')."
        )

    def detect_orientation(self, image_bytes: bytes) -> float:
        return 0.0


class StubTableStructureProvider:
    id = "stub"

    def extract_tables(self, image_bytes: bytes, words: OcrResult, *, page_index: int):
        raise NotImplementedError("No table-structure engine configured.")


class StubLlmProvider:
    id = "stub"

    def complete_structured(
        self,
        *,
        system: str,
        messages: Sequence[LlmMessage],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> tuple[BaseModel, LlmMeta]:
        raise NotImplementedError(
            "LLM disambiguation requested but no LLM adapter is configured. Install "
            "the 'llm' extra and set provider in config.toml [llm] (e.g. 'anthropic')."
        )
