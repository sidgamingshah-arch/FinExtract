"""Runtime-mutable settings overlay.

Most settings come from ``config.toml`` / env and change only on redeploy. A few are
meant to be flipped live by an admin from the Settings screen:

  * ``ui_localization``  — localize the whole interface.
  * ``review_required``  — require a reviewer step (else the workflow closes at analyst).
  * the LLM configuration — provider / model / base_url / temperature / max_tokens /
    timeout / api_key_env. Edits are applied onto the process-wide ``Settings.llm`` so
    the provider registry and adapters pick them up immediately. The **API key is never
    stored here** — only the *name* of the env var it is read from.
  * the EXTRACTION thresholds — the mapping ensemble's accept/candidate/margin bars and the
    reconciliation tolerances (see ``EXTRACTION_KNOBS``). Applied onto the process-wide
    ``Settings.extraction``, which the pipeline reads per run, so a change takes effect on the
    next extraction and never rewrites one that already happened.

Every change is PERSISTED to ``setting_overrides`` and re-applied at startup, so an admin's
edit survives a restart and is shared by every process against the same database. The
config file remains the source of the DEFAULTS — "restore defaults" means what config.toml
shipped, never the last value that happened to be stored.

The in-memory copies are a read-through cache of that table: reads never touch the database,
writes go to both. Two processes changing DIFFERENT settings do not interfere (one row per
setting); two changing the SAME one are last-write-wins, and the losing process keeps its own
value until it restarts or is told again — acceptable for an admin screen, and the reason the
table stores one row per setting rather than a single blob.

**No secrets are persisted.** The LLM API key is never written: only the NAME of the
environment variable it is read from.
"""
from __future__ import annotations

from copy import deepcopy
from typing import NamedTuple

from app.config import get_settings

# Where an override is applied when it is loaded back. One namespace per settings object, so a
# short name like "model" cannot be confused between them.
SCOPE_FEATURES = "features"
SCOPE_LLM = "llm"
SCOPE_EXTRACTION = "extraction"

_RUNTIME: dict = {}
# Snapshot of the config-file LLM defaults, captured once so reset() can restore them.
_LLM_DEFAULTS: dict | None = None
# …and of the extraction defaults, so "restore defaults" means the shipped config values
# rather than whatever the last edit happened to be.
_EXTRACTION_DEFAULTS: dict | None = None
# Whether the persisted overrides have been read into this process yet.
_LOADED = False

# LLM fields an admin may edit from the UI (the key itself is intentionally excluded).
LLM_EDITABLE = ("provider", "model", "base_url", "temperature", "max_tokens",
                "timeout_seconds", "api_key_env",
                # Azure addresses a DEPLOYMENT on the customer's own resource, so the resource, the
                # api-version and the deployment name are part of the address — as editable as
                # base_url is for OpenAI, and unusable if they are not.
                "azure_endpoint", "azure_api_version", "azure_deployment")


class Knob(NamedTuple):
    """One tunable extraction setting, described well enough for a UI to render and validate it
    without knowing anything about mapping.

    The bounds and the guidance live HERE rather than in the frontend, because they are facts
    about the pipeline: a threshold outside its range does not mean anything, and the note on
    each is what stops a well-meaning edit from quietly making mapping worse.
    """

    key: str
    kind: str            # "number" | "bool" | "choice"
    label: str
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()


# Every extraction setting an admin may change from the Settings screen. Changing one affects
# FUTURE extractions; it never rewrites a run that already happened.
EXTRACTION_KNOBS: tuple[Knob, ...] = (
    Knob("llm_mapping", "bool", "Map by meaning (LLM)",
         "When an LLM provider is configured, concepts are chosen by MEANING from each "
         "candidate's definition and criteria, and the lexical tiers only shortlist. Turn this "
         "off to force the deterministic ensemble even with a provider available."),
    Knob("llm_gap_routing", "bool", "Close subtotal gaps (LLM)",
         "When a section subtotal computed from the template's lines differs from the printed "
         "one, offer the model the extracted lines that reached no statement and ask which "
         "belong in that section's Others. Only groups that close the difference in BOTH "
         "periods are offered. Off, the difference stays a review item."),
    Knob("mapping_scope", "choice", "Mapping granularity",
         "per_statement decides all of a statement's captions in one call, so cross-line "
         "judgements (parent/child containment, residuals, 'Others') have full context. "
         "per_line is cheaper and less context-aware.",
         choices=("per_statement", "per_line")),
    Knob("evidence_floor", "number", "Alias evidence floor",
         "How nearly a printed caption must BE one of a concept's authored aliases before that "
         "counts as evidence. Nothing maps a row on wording alone — there is no string-similarity "
         "tier — so this decides only whether the deterministic evidence is strong enough to "
         "contradict the model's choice, and whether a caption is the printed name of a concept "
         "the framework computes rather than extracts.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("alias_coverage_floor", "number", "Alias coverage floor",
         "How much of the alias the caption must actually explain. This is what stops a short "
         "heading ('LIABILITIES') from being read as a much longer concept name that merely "
         "contains it.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("mapping_margin", "number", "Winner margin",
         "How far the winning concept must beat the runner-up before the mapping is accepted. "
         "Below the margin the mapping is declined rather than guessed, and the line is reported "
         "as reaching no template line at all.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("auto_accept_confidence", "number", "Auto-accept confidence",
         "Combined confidence at or above which a mapped line is treated as settled. A line "
         "mapped below it is still mapped and still shown, marked as a weak match on its own "
         "confidence badge — it raises no review item, because the answer to a weak match is a "
         "better match rather than an analyst's time. The count of them is served with the review "
         "queue so narrowing that queue cannot read as better data.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("recon_abs_tolerance", "number", "Reconciliation tolerance (absolute)",
         "Absolute floor when comparing a note total to the face figure it supports, in the "
         "document's own units.",
         minimum=0.0, maximum=1e9, step=1.0),
    Knob("recon_rel_tolerance", "number", "Reconciliation tolerance (relative)",
         "Relative band for the same comparison, so large figures are not held to sub-unit "
         "precision.",
         minimum=0.0, maximum=1.0, step=0.001),
    Knob("recon_corroboration_rel", "number", "Note-breakdown corroboration band",
         "How near a note total must come to the face figure before we accept the note really "
         "is a BREAKDOWN of it. Beyond this the note is recorded as 'not a breakdown' rather "
         "than reported as a mismatch — most cited notes are analyses or segment tables. "
         "Raising this turns more near-misses into review items; lowering it reports fewer.",
         minimum=0.0, maximum=1.0, step=0.01),
)

_KNOB_BY_KEY = {k.key: k for k in EXTRACTION_KNOBS}


def _persist(scope: str, values: dict) -> None:
    """Write these overrides to the database, one row per setting (upsert).

    Deliberately best-effort: a settings screen must not 500 because the override table is
    momentarily unavailable, and the value has already been applied in-process. The failure is
    that the change does not survive a restart, which is strictly better than losing the edit.
    """
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride

    try:
        with SessionLocal() as session:
            for key, value in values.items():
                row = session.execute(
                    select(SettingOverride).where(SettingOverride.scope == scope,
                                                  SettingOverride.key == key)
                ).scalar_one_or_none()
                if row is None:
                    session.add(SettingOverride(scope=scope, key=key, value={"v": value}))
                else:
                    row.value = {"v": value}
            session.commit()
    except Exception:  # noqa: BLE001 — persistence is best-effort; see above
        pass


def _forget(scope: str) -> None:
    """Drop every persisted override in a scope, so the config file's values apply again."""
    from sqlalchemy import delete

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride

    try:
        with SessionLocal() as session:
            session.execute(delete(SettingOverride).where(SettingOverride.scope == scope))
            session.commit()
    except Exception:  # noqa: BLE001
        pass


def _stored(scope: str) -> dict:
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride

    try:
        with SessionLocal() as session:
            rows = session.execute(
                select(SettingOverride).where(SettingOverride.scope == scope)).scalars().all()
            return {r.key: (r.value or {}).get("v") for r in rows}
    except Exception:  # noqa: BLE001 — a database without the table yet behaves as "no overrides"
        return {}


def load_persisted() -> None:
    """Re-apply the stored overrides onto this process's settings. Called once at startup.

    Order matters: the config-file DEFAULTS are captured FIRST (``_seed``), so "restore
    defaults" still means what config.toml shipped and not what was previously saved.
    """
    global _LOADED
    _seed()
    _LOADED = True

    feats = _stored(SCOPE_FEATURES)
    for key in ("ui_localization", "review_required", "seed_demo"):
        if key in feats and feats[key] is not None:
            _RUNTIME[key] = bool(feats[key])

    llm_stored = {k: v for k, v in _stored(SCOPE_LLM).items() if k in LLM_EDITABLE}
    if llm_stored:
        llm = get_settings().llm
        for key, value in llm_stored.items():
            if value is not None:
                setattr(llm, key, value)

    ex_stored = _stored(SCOPE_EXTRACTION)
    if ex_stored:
        try:
            set_extraction_config(**ex_stored, persist=False)
        except ValueError:
            # A stored value that is no longer valid (a knob's range tightened between
            # releases) must not stop the app from starting; the config default stands.
            pass


def _seed() -> None:
    global _LLM_DEFAULTS, _EXTRACTION_DEFAULTS
    feats = get_settings().features
    _RUNTIME.setdefault("ui_localization", feats.ui_localization)
    _RUNTIME.setdefault("review_required", feats.review_required)
    _RUNTIME.setdefault("seed_demo", feats.seed_demo)
    if _LLM_DEFAULTS is None:
        llm = get_settings().llm
        _LLM_DEFAULTS = {k: getattr(llm, k) for k in LLM_EDITABLE}
    if _EXTRACTION_DEFAULTS is None:
        ex = get_settings().extraction
        _EXTRACTION_DEFAULTS = {k.key: getattr(ex, k.key) for k in EXTRACTION_KNOBS}


def get_seed_demo() -> bool:
    """Whether the seeded sample project is currently loaded. Off = greenfield/empty."""
    _seed()
    return bool(_RUNTIME["seed_demo"])


def set_seed_demo(value: bool) -> bool:
    _seed()
    _RUNTIME["seed_demo"] = bool(value)
    _persist(SCOPE_FEATURES, {"seed_demo": _RUNTIME["seed_demo"]})
    return _RUNTIME["seed_demo"]


def get_ui_localization() -> bool:
    _seed()
    return bool(_RUNTIME["ui_localization"])


def set_ui_localization(value: bool) -> bool:
    _seed()
    _RUNTIME["ui_localization"] = bool(value)
    _persist(SCOPE_FEATURES, {"ui_localization": _RUNTIME["ui_localization"]})
    return _RUNTIME["ui_localization"]


def get_review_required() -> bool:
    _seed()
    return bool(_RUNTIME["review_required"])


def set_review_required(value: bool) -> bool:
    _seed()
    _RUNTIME["review_required"] = bool(value)
    _persist(SCOPE_FEATURES, {"review_required": _RUNTIME["review_required"]})
    return _RUNTIME["review_required"]


def set_llm_config(*, persist: bool = True, **fields) -> dict:
    """Apply admin LLM-config edits onto the live Settings.llm (never the API key).

    Only keys in ``LLM_EDITABLE`` are honoured; unknown keys and any ``api_key``/secret
    values are ignored — which is also what keeps the key out of the persisted rows, since
    only the honoured keys are written. Returns the resulting editable LLM config.
    """
    _seed()
    llm = get_settings().llm
    applied: dict = {}
    for key, value in fields.items():
        if key in LLM_EDITABLE and value is not None:
            setattr(llm, key, value)
            applied[key] = value
    if persist and applied:
        _persist(SCOPE_LLM, applied)
    return {k: getattr(llm, k) for k in LLM_EDITABLE}


def extraction_config() -> dict:
    """The extraction settings an admin may edit, with the shipped default for each.

    ``defaults`` is what the config file said at startup, so the UI can offer "restore defaults"
    and show at a glance which knobs have been moved away from them.
    """
    _seed()
    ex = get_settings().extraction
    return {
        "values": {k.key: getattr(ex, k.key) for k in EXTRACTION_KNOBS},
        "defaults": dict(_EXTRACTION_DEFAULTS or {}),
        "fields": [
            {"key": k.key, "kind": k.kind, "label": k.label, "help": k.help,
             "min": k.minimum, "max": k.maximum, "step": k.step,
             "choices": list(k.choices)}
            for k in EXTRACTION_KNOBS
        ],
    }


def set_extraction_config(*, persist: bool = True, **fields) -> dict:
    """Apply admin edits onto the live extraction settings, refusing anything out of range.

    Only the declared knobs are honoured. A value outside a knob's bounds is REJECTED rather
    than clamped: silently substituting a different threshold than the one an admin typed would
    make the screen lie about what the pipeline is doing.

    Raises ValueError naming the offending field.
    """
    _seed()
    ex = get_settings().extraction
    cleaned: dict = {}
    for key, value in fields.items():
        knob = _KNOB_BY_KEY.get(key)
        if knob is None or value is None:
            continue
        if knob.kind == "bool":
            cleaned[key] = bool(value)
            continue
        if knob.kind == "choice":
            if str(value) not in knob.choices:
                raise ValueError(
                    f"{key} must be one of {', '.join(knob.choices)} (got {value!r})")
            cleaned[key] = str(value)
            continue
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number (got {value!r})") from exc
        if knob.minimum is not None and num < knob.minimum:
            raise ValueError(f"{key} must be at least {knob.minimum} (got {num})")
        if knob.maximum is not None and num > knob.maximum:
            raise ValueError(f"{key} must be at most {knob.maximum} (got {num})")
        cleaned[key] = num
    for key, value in cleaned.items():
        setattr(ex, key, value)
    if persist and cleaned:
        _persist(SCOPE_EXTRACTION, cleaned)
    return extraction_config()


def reset_llm_config() -> dict:
    """Restore the LLM configuration to what the config file shipped.

    Like the extraction reset, the stored rows are DELETED rather than rewritten, so a later
    change to config.toml is picked up instead of masked by a saved copy of the old default.
    """
    _seed()
    llm = get_settings().llm
    for key, value in (_LLM_DEFAULTS or {}).items():
        setattr(llm, key, value)
    _forget(SCOPE_LLM)
    return {k: getattr(llm, k) for k in LLM_EDITABLE}


def reset_extraction_config() -> dict:
    """Restore every extraction knob to the value the config file shipped.

    The stored rows are DELETED rather than rewritten with the defaults, so a later change to
    config.toml is picked up instead of being masked by a saved copy of the old default.
    """
    _seed()
    ex = get_settings().extraction
    for key, value in (_EXTRACTION_DEFAULTS or {}).items():
        setattr(ex, key, value)
    _forget(SCOPE_EXTRACTION)
    return extraction_config()


def reset(*, persisted: bool = True) -> None:
    """Test helper: drop runtime overrides so config defaults are re-seeded.

    Also clears the persisted rows by default — otherwise one test's saved threshold would be
    re-applied to the next by ``load_persisted``.
    """
    global _LLM_DEFAULTS, _EXTRACTION_DEFAULTS, _LOADED
    _RUNTIME.clear()
    _LOADED = False
    if persisted:
        for scope in (SCOPE_FEATURES, SCOPE_LLM, SCOPE_EXTRACTION):
            _forget(scope)
    if _LLM_DEFAULTS is not None:
        llm = get_settings().llm
        for k, v in _LLM_DEFAULTS.items():
            setattr(llm, k, v)
        _LLM_DEFAULTS = None
    if _EXTRACTION_DEFAULTS is not None:
        ex = get_settings().extraction
        for k, v in _EXTRACTION_DEFAULTS.items():
            setattr(ex, k, v)
        _EXTRACTION_DEFAULTS = None
