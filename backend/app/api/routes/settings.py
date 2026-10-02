"""Application settings — the admin-facing view of configuration.

``GET /settings`` returns a non-secret snapshot (LLM/OCR/extraction config,
auth flags, feature flags). Any authenticated user may read it — the frontend needs
the ``ui_localization`` flag to decide whether to localize the interface. ``PATCH
/settings`` lets an admin change the runtime-mutable settings: the feature flags and the
EXTRACTION thresholds (the mapping ensemble's bars and the reconciliation tolerances).
Everything else is config.toml / env and shown read-only — and the LLM is config.toml
[llm] ONLY (``app.config``): a PATCH that tries to set it is a 400 naming the file.

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

from app.api.deps import default_locale
from app.config import get_settings
from app.security import Permission, current_principal, require
from app.adapters.docling_ocr import docling_status
from app.services.settings_state import (
    extraction_config,
    get_review_required,
    get_seed_demo,
    get_ui_localization,
    reset_extraction_config,
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
            # Read through `deps.default_locale` — the SAME resolver the locale-bearing routes
            # now take their default from (api/deps.py:output_locale) — so this row reports the
            # locale requests will actually be answered in, blank config value normalised to
            # "en" included. It used to echo the raw field while all 18 of those routes carried
            # their own `Query("en")`, which is how the screen came to report "zh" as a default
            # nothing applied.
            "default_output_locale": default_locale(),
            "supported_locales": s.features.supported_locales,
        },
        # READ-ONLY, for diagnosis: the LLM is defined only in config.toml [llm] (app.config).
        "llm": {
            "defined_in": "config.toml [llm]",
            "provider": s.llm.provider,
            "model": s.llm.model,
            # No "temperature". It was echoed here, and this echo was the ONLY read of
            # ``s.llm.temperature`` anywhere in app/ — nothing forwarded it to a provider call, so
            # the screen reported a setting the requests did not carry. Temperature is fixed at
            # ports.llm's default 0.0; see config.LlmSettings.
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
        "ocr": {"engine": s.ocr.engine, "languages": s.ocr.languages, "dpi": s.ocr.dpi,
                # Whether the offline engine can actually run here — the package and its models
                # on disk — so the screen can say so beside the switch that turns it on.
                "docling": docling_status(s)},
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


class SettingsPatch(BaseModel):
    # Runtime-mutable settings; the rest are config.toml / env driven.
    ui_localization: bool | None = None
    review_required: bool | None = None
    seed_demo: bool | None = None  # load (true) / clear (false) the sample project
    # NOT SETTABLE. Declared only so a client that still sends them is told where the LLM is
    # defined (400) instead of receiving a 200 that changed nothing.
    llm: dict | None = None
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
    if body.llm is not None or body.reset_llm is not None:
        raise HTTPException(400, "The LLM is not editable at run time: it is defined only in "
                                 "backend/config.toml [llm]. Edit that file and restart.")
    if body.ui_localization is not None:
        set_ui_localization(body.ui_localization)
    if body.review_required is not None:
        set_review_required(body.review_required)
    if body.seed_demo is not None:
        set_seed_demo(body.seed_demo)
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
