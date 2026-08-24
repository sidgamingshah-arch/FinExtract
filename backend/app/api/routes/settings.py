"""Application settings — the admin-facing view of configuration.

``GET /settings`` returns a non-secret snapshot (LLM/OCR/extraction config,
auth flags, feature flags). Any authenticated user may read it — the frontend needs
the ``ui_localization`` flag to decide whether to localize the interface. ``PATCH
/settings`` lets an admin change the runtime-mutable settings: the feature flags, the
LLM configuration, and the EXTRACTION thresholds (the mapping ensemble's bars and the
reconciliation tolerances). Everything else is config.toml / env and shown read-only.

The extraction knobs are described BY THE BACKEND — ``extraction_fields`` carries each
one's bounds, step and an explanation — so the Settings screen renders and validates from
the same definition the API enforces, instead of a second copy that can drift. A value out
of range is a 422 naming the field, never a silently clamped substitute.

Secrets are never returned: for the LLM key we report only whether the configured
environment variable is populated (``key_configured``), never the key itself.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.security import Permission, current_principal, require
from app.services.settings_state import (
    extraction_config,
    get_review_required,
    get_seed_demo,
    get_ui_localization,
    set_llm_config,
    reset_extraction_config,
    reset_llm_config,
    set_extraction_config,
    set_review_required,
    set_seed_demo,
    set_ui_localization,
)

router = APIRouter(tags=["settings"])


def _snapshot() -> dict:
    s = get_settings()
    return {
        "features": {
            "ui_localization": get_ui_localization(),
            "review_required": get_review_required(),
            "seed_demo": get_seed_demo(),
            "default_output_locale": s.features.default_output_locale,
            "supported_locales": s.features.supported_locales,
        },
        "llm": {
            "provider": s.llm.provider,
            "model": s.llm.model,
            "temperature": s.llm.temperature,
            "max_tokens": s.llm.max_tokens,
            "timeout_seconds": s.llm.timeout_seconds,
            "base_url": s.llm.base_url,
            "api_key_env": s.llm.api_key_env,
            "key_configured": bool(os.environ.get(s.llm.api_key_env)),
            "azure_endpoint": s.llm.azure_endpoint,
            "azure_api_version": s.llm.azure_api_version,
            # What Azure will actually be asked for, deployment fallback resolved, so the screen
            # shows the address that will be used rather than the field that was filled in.
            "azure_deployment": s.llm.azure_deployment_name(),
        },
        "ocr": {"engine": s.ocr.engine, "languages": s.ocr.languages, "dpi": s.ocr.dpi},
        # Editable at runtime by an admin. ``fields`` describes each knob — bounds, step and
        # what it does — so the Settings screen renders and validates from the backend's own
        # definition instead of a second copy that can drift from it; ``defaults`` is what the
        # config file shipped, for "restore defaults".
        "extraction": extraction_config()["values"],
        "extraction_defaults": extraction_config()["defaults"],
        "extraction_fields": extraction_config()["fields"],
        "auth": {
            "allow_role_header": s.auth.allow_role_header,
            "demo_mode": s.auth.demo_mode,
            "session_ttl_minutes": s.auth.session_ttl_minutes,
        },
    }


@router.get("/settings", dependencies=[Depends(current_principal)])
def get_settings_snapshot() -> dict:
    return _snapshot()


class LlmConfigPatch(BaseModel):
    # Non-secret LLM configuration. The API key is NEVER accepted here — only the name
    # of the env var it is read from (api_key_env). Any extra fields are ignored.
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    api_key_env: str | None = None
    # Azure OpenAI address. Not secret: the resource host, the api-version and the deployment name.
    azure_endpoint: str | None = None
    azure_api_version: str | None = None
    azure_deployment: str | None = None


class SettingsPatch(BaseModel):
    # Runtime-mutable settings; the rest are config.toml / env driven.
    ui_localization: bool | None = None
    review_required: bool | None = None
    seed_demo: bool | None = None  # load (true) / clear (false) the sample project
    llm: LlmConfigPatch | None = None
    # Restore the LLM configuration to what config.toml shipped.
    reset_llm: bool | None = None
    # Mapping / reconciliation thresholds, as {knob: value}. Deliberately NOT a field-per-knob
    # model: that is a second list of the knobs, and it silently drifted from the real one —
    # a knob missing from it was dropped by validation while the request still returned 200.
    # ``settings_state.set_extraction_config`` is the single definition; it ignores keys that
    # are not knobs and rejects values out of a knob's range.
    extraction: dict[str, float | bool | str] | None = None
    # Restore every extraction knob to the value config.toml shipped.
    reset_extraction: bool | None = None


@router.patch("/settings", dependencies=[Depends(require(Permission.CONFIG_SETTINGS))])
def update_settings(body: SettingsPatch) -> dict:
    if body.ui_localization is not None:
        set_ui_localization(body.ui_localization)
    if body.review_required is not None:
        set_review_required(body.review_required)
    if body.seed_demo is not None:
        set_seed_demo(body.seed_demo)
    if body.reset_llm:
        reset_llm_config()
    if body.llm is not None:
        set_llm_config(**body.llm.model_dump(exclude_none=True))
    if body.reset_extraction:
        reset_extraction_config()
    if body.extraction is not None:
        try:
            set_extraction_config(**body.extraction)
        except ValueError as exc:
            # A threshold outside its range is a client error naming the offending field, not a
            # silently clamped value — the screen must never show a number the pipeline is not
            # actually using.
            raise HTTPException(422, str(exc)) from exc
    return _snapshot()
