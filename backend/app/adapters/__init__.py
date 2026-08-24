"""Concrete adapter implementations, registered into the global registry.

Importing this package registers all built-in adapters (currently stubs plus the
local object store). Real engines (PaddleOCR, PP-Structure, Anthropic, etc.) are
added here behind their optional-dependency extras without touching the core.
"""
from __future__ import annotations

from app.config import get_settings
from app.ports.object_store import LocalObjectStore
from app.ports.registry import registry

from .stubs import (
    StubLlmProvider,
    StubOcrProvider,
    StubTableStructureProvider,
)


def register_builtins() -> None:
    settings = get_settings()

    registry.register("ocr", "stub", StubOcrProvider)
    registry.register("table", "stub", StubTableStructureProvider)
    registry.register("llm", "stub", StubLlmProvider)

    # Real Claude adapter — registered under its own id so it's available when
    # llm.provider = "anthropic". The class is imported lazily and constructs its
    # SDK client only on first use, so registering it needs neither the SDK nor a key.
    from .anthropic_llm import AnthropicLlmProvider

    registry.register("llm", "anthropic", AnthropicLlmProvider)

    # OpenAI-compatible adapter (OpenAI, TokenRouter, OpenRouter, vLLM, …) — selected
    # when llm.provider = "openai" / "openai_compatible". Uses httpx at call time; the
    # base_url + model come from config.toml [llm], the key from llm.api_key_env.
    from .openai_llm import OpenAiLlmProvider

    registry.register("llm", "openai", OpenAiLlmProvider)
    registry.register("llm", "openai_compatible", OpenAiLlmProvider)

    # Azure OpenAI — the DEFAULT provider (GPT-5 mini). Same Chat Completions body as above, but the
    # deployment is addressed on the customer's own resource and authenticated with an api-key
    # header, so it needs its own adapter rather than a base_url. Lazy like the others: registering
    # it needs neither a key nor a resource.
    from .azure_openai_llm import AzureOpenAiLlmProvider

    registry.register("llm", "azure_openai", AzureOpenAiLlmProvider)
    registry.register("llm", "azure", AzureOpenAiLlmProvider)

    # Real OCR adapters (scanned pages) — registered under their ids so they're available
    # when ocr.engine selects them. Both are lazy: the engine + models load only on first
    # use, so registering needs neither the extra nor the models.
    #   - docling: the recommended FREE, pip-only OCR (layout + OCR + table structure),
    #     no system binary, no cloud. Install with the 'docling' extra.
    #   - azure: Azure AI Document Intelligence (cloud layout+OCR+tables); needs an endpoint
    #     + key. Uses httpx (a dependency) — no SDK required.
    #   - paddleocr: alternative, via the 'ocr' extra.
    from .azure_doc_intelligence import AzureDocIntelligenceProvider
    from .docling_ocr import DoclingOcrProvider
    from .paddle_ocr import PaddleOcrProvider

    registry.register("ocr", "docling", DoclingOcrProvider)
    registry.register("ocr", "azure", AzureDocIntelligenceProvider)
    registry.register("ocr", "azure_document_intelligence", AzureDocIntelligenceProvider)
    registry.register("ocr", "paddleocr", PaddleOcrProvider)

    registry.register(
        "object_store", "local",
        lambda: LocalObjectStore(settings.object_store_root),
    )


register_builtins()
