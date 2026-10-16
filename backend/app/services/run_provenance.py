"""What produced a run: the code, the settings, the reply contract and the engines.

A run already pins its template and its line-item set. That is half of what decides its figures; the
other half is the code that ran, the admin knobs in force (OCR, the model route, request grouping,
the evidence floors), the reply contract and default guidance written in code, and the OCR and
document engines it read pages with. None of those was recorded, so two runs of one filing could
differ and neither could say why.

:func:`snapshot` is recorded on the run's options under ``provenance``. :func:`settings_identity`
is the part of it that steers EXTRACTION — every knob except the ones that only change how long a
run takes — hashed, so the idempotency test can tell that a settings change asks for a new run.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

# Knobs that change how long a run takes and not what it finds. Everything else in
# ``settings_state.EXTRACTION_KNOBS`` is part of the reuse identity.
_TIMING_ONLY = frozenset({"llm_parallel_requests"})

_LIBRARIES = ("pydantic", "fastapi", "sqlalchemy", "pdfplumber", "pymupdf", "pypdf",
              "docling", "openpyxl", "rapidfuzz")


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def code_build() -> dict:
    """The commit and build the server runs, read once. ``GIT_COMMIT`` / ``BUILD_ID`` in the
    environment win (a container has no ``.git``); otherwise the checkout is asked."""
    commit = os.environ.get("GIT_COMMIT") or ""
    if not commit:
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
                capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            commit = ""
    return {"commit": commit or None, "build": os.environ.get("BUILD_ID") or None}


@lru_cache(maxsize=1)
def library_versions() -> dict:
    from importlib import metadata

    out = {}
    for name in _LIBRARIES:
        try:
            out[name] = metadata.version(name)
        except Exception:
            continue
    return out


def contract_hash() -> str:
    """One hash over the reply contract and the default guidance the code sends with a request."""
    from app.services.line_item_llm import REPLY_CONTRACT
    from app.services.mapping import DEFAULT_MAPPING_GUIDANCE

    return _digest({"reply_contract": REPLY_CONTRACT, "guidance": DEFAULT_MAPPING_GUIDANCE})


def settings_snapshot() -> dict:
    """Every extraction knob as it stands now (the Settings screen's values)."""
    from app.services.settings_state import extraction_config

    return dict(extraction_config()["values"])


def engines() -> dict:
    from app.config import get_settings

    s = get_settings()
    de = s.document_engine
    return {"ocr_engine": s.ocr.engine,
            "ocr_model": (s.ocr.azure_model if s.ocr.engine == "azure" else None),
            "document_engine": ("kensho" if getattr(de, "kensho_submit_url", "") else None),
            "llm_provider": s.llm.provider, "llm_model": s.llm.model}


def settings_identity(values: dict | None = None) -> str:
    """The hash of what in the settings steers extraction — the reuse identity's settings part."""
    values = settings_snapshot() if values is None else values
    steering = {k: v for k, v in values.items() if k not in _TIMING_ONLY}
    return _digest({"knobs": steering, "engines": engines(), "contract": contract_hash()})


def snapshot() -> dict:
    from app import __version__

    values = settings_snapshot()
    return {**code_build(), "engine_version": __version__, "settings": values,
            "settings_identity": settings_identity(values), "contract_hash": contract_hash(),
            **engines(), "libraries": library_versions()}
