"""Adapter ports — the swappability contract.

The pipeline core depends only on these ``Protocol``s, never on a concrete vendor.
Concrete implementations register into the ``Registry`` keyed by id; configuration
selects them. This is what makes the "decide infra later" choice cost-free.
"""
from __future__ import annotations

from .fx import FxConverter
from .llm import LlmMeta, LlmProvider
from .object_store import ObjectStore
from .ocr import OcrProvider, OcrResult, OcrWord
from .registry import Registry, registry
from .table_structure import TableStructureProvider

__all__ = [
    "FxConverter",
    "LlmProvider", "LlmMeta",
    "ObjectStore",
    "OcrProvider", "OcrResult", "OcrWord",
    "TableStructureProvider",
    "Registry", "registry",
]
