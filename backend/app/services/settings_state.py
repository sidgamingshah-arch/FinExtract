"""Runtime-mutable settings overlay.

Most settings come from ``config.toml`` / env and change only on redeploy. A few are
meant to be flipped live by an admin from the Settings screen:

  * ``ui_localization``  — localize the whole interface.
  * ``review_required``  — require a reviewer step (else the workflow closes at analyst).
  * NOT the LLM. It is defined only in ``config.toml``'s ``[llm]`` table (see ``app.config``).
    It used to be editable here and persisted, and a saved value outranked the file and the
    environment alike — so the file could name one gateway while every call went to another.
    Rows an older release stored under the ``llm`` scope are deleted at startup, and logged.
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

**No secrets are persisted.**
"""
from __future__ import annotations

from copy import deepcopy
from typing import NamedTuple

from app.config import get_settings
from app.ports.registry import registry

# Where an override is applied when it is loaded back. One namespace per settings object, so a
# short name like "model" cannot be confused between them.
SCOPE_FEATURES = "features"
SCOPE_LLM = "llm"
SCOPE_EXTRACTION = "extraction"

_RUNTIME: dict = {}
# …and of the extraction defaults, so "restore defaults" means the shipped config values
# rather than whatever the last edit happened to be.
_EXTRACTION_DEFAULTS: dict | None = None
# Whether the persisted overrides have been read into this process yet.
_LOADED = False

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
    Knob("llm_mapping", "bool", "Ask the model about line items",
         "Which CONCEPT a printed caption is, is always decided deterministically — by exact "
         "alias and the rule tier's authored hints — and this switch does not change that. What "
         "it controls is whether the configured provider is asked about LINE ITEMS and the notes "
         "they name: which printed row inside a claimed note holds the figure. Off, or with no "
         "provider, the note-sourced tiers decide that too."),
    Knob("llm_focus_only", "bool", "Ask the model only about focus line items",
         "Restrict the run's requests to the line items named by extraction.llm_focus_keys; every "
         "other line keeps what the deterministic tiers gave it. Use it to spend a small provider "
         "budget on the lines under review instead of on the whole filing. With no focus keys "
         "configured this does nothing and says so in the run log."),
    Knob("llm_request_grouping", "choice", "How line items share a request",
         "Each request carries the NOTES its line items asked for, and that note block is almost "
         "all of what a request costs — measured at 95,188 of a ~196,000-character request, going "
         "out byte-identical in every call of a run. Grouping lets line items needing the same "
         "notes pay for one copy. \"none\" is one request per line item: the baseline, and the "
         "only mode whose answer is attributable to a single line, because nothing else shared "
         "the call. \"identical\" groups lines whose selected note sets match exactly — safe by "
         "construction, since no line receives a note it did not ask for. \"similar\" groups "
         "lines whose note sets overlap by at least the threshold below, which saves more and can "
         "bleed: lines sharing a call can influence each other's answers. \"manual\" uses the "
         "groups authored on the line-item set and asks about everything else one line at a time.",
         choices=("none", "identical", "similar", "manual")),
    Knob("llm_document_context", "choice", "What the model reads with each question",
         "\"selected\" sends each request only the notes and statements its line items selected: "
         "the smallest requests. \"full\" sends the WHOLE document with every request — every "
         "note and every statement — so the model always has the full filing in view. The "
         "document goes first and is identical on every request of a run, so a provider that "
         "caches prompts charges the cached rate for it after the first request; the run log "
         "shows input, cached and output tokens per request and a summary at the end. Combine "
         "with grouping to send the document fewer times.",
         choices=("selected", "full")),
    Knob("ocr_scanned_pages", "bool", "Read scanned pages (OCR)",
         "A page with a text layer is always read from that layer, exactly as printed — OCR would "
         "only re-guess characters the file already holds — so this changes nothing for a native "
         "PDF. It decides what happens to a page with NO text layer (a scan, a photographed "
         "page): on, the page is rendered and read by the configured OCR engine — Docling, which "
         "runs entirely on this server from its pre-installed models and sends nothing anywhere; "
         "off, the page is skipped and the run log says so. Reading a scanned page is much slower "
         "than reading text, and OCR can misread a digit: each figure's source records that it "
         "was read by OCR, so check a scanned filing's figures against the page."),
    Knob("document_reader", "choice", "Document extraction engine",
         "\"off\" reads every page as before: from the PDF's own text, or with the OCR engine for "
         "a page that has none. \"kensho\" sends the filing to Kensho Extract once and reads the "
         "pages chosen below from its answer — Kensho's text and table cells, placed where it saw "
         "them, then read into rows and columns exactly as a native page is. This SENDS THE PDF "
         "OUTSIDE this server, to the address set in config.toml [document_engine]; the status "
         "below says whether that address and a token are configured. A page Kensho does not "
         "return, or a run where it fails, is read as usual and the run log says so.",
         choices=("off", "kensho")),
    Knob("document_reader_pages", "choice", "Pages the document engine reads",
         "\"scanned\" uses the engine only for pages with no text layer, in place of OCR — a "
         "native page is still read from its own text, exactly as printed. \"all\" reads every "
         "page from the engine's answer instead of the PDF's text layer: for filings whose text "
         "layer is broken or scrambled. Only read when an engine is chosen above.",
         choices=("scanned", "all")),
    Knob("llm_parallel_requests", "number", "Requests sent at once",
         "How many line-item requests are in flight together. The first request of a run is "
         "always sent on its own — it fills the provider's prompt cache — and the rest follow "
         "this many at a time. Replies are processed in the same order whatever this is, so it "
         "changes how long a run takes and not what it finds. Lower it if the provider starts "
         "refusing requests for rate limits; 1 sends one request at a time.",
         minimum=1, maximum=16, step=1),
    Knob("llm_group_similarity", "number", "How much note overlap counts as similar",
         "Only read when grouping is \"similar\". The fraction of two line items' selected notes "
         "that must be the same (Jaccard overlap) before they share a request. 1.0 is the same "
         "thing as \"identical\"; lower it to group more aggressively and pay for fewer copies "
         "of the note block, at the cost of lines receiving notes they did not ask for.",
         minimum=0.0, maximum=1.0, step=0.05),
    Knob("llm_gap_routing", "bool", "Close subtotal gaps (LLM)",
         "When a section subtotal computed from the template's lines differs from the printed "
         "one, offer the model the extracted lines that reached no statement and ask which "
         "belong in that section's Others. Only groups that close the difference in BOTH "
         "periods are offered. Off, the difference stays a review item."),
    Knob("llm_contingent_liabilities", "bool", "Contingent liabilities narrative (LLM)",
         "Classification and every total are always deterministic, computed only from the "
         "filing's own figures. Enabled, the model rewrites the summary paragraph and each "
         "unclassified item's short statement in clearer English, grounded only in the supplied "
         "facts. Off, or with no provider configured, the deterministic prose is shown."),
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

    # THE LLM IS NOT STORED HERE ANY MORE (see the module docstring). A row an older release
    # saved would otherwise sit in the table looking like configuration; it is removed, and what
    # it said is logged so nobody wonders where their setting went.
    llm_stored = _stored(SCOPE_LLM)
    if llm_stored:
        import logging
        logging.getLogger(__name__).warning(
            "Removed LLM settings saved from the Settings screen (%s): the LLM is defined only in "
            "config.toml [llm].", ", ".join(f"{k}={v!r}" for k, v in sorted(llm_stored.items())))
        _forget(SCOPE_LLM)
    _warn_if_provider_unregistered()

    ex_stored = _stored(SCOPE_EXTRACTION)
    if ex_stored:
        try:
            set_extraction_config(**ex_stored, persist=False)
        except ValueError:
            # A stored value that is no longer valid (a knob's range tightened between
            # releases) must not stop the app from starting; the config default stands.
            pass


def _seed() -> None:
    global _EXTRACTION_DEFAULTS
    feats = get_settings().features
    _RUNTIME.setdefault("ui_localization", feats.ui_localization)
    _RUNTIME.setdefault("review_required", feats.review_required)
    _RUNTIME.setdefault("seed_demo", feats.seed_demo)
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


def _warn_if_provider_unregistered() -> None:
    """Say at startup, not per stage, when config.toml names an LLM provider no adapter answers to."""
    import logging

    import app.adapters  # noqa: F401 — import for its registration side effect

    provider = get_settings().llm.provider
    available = registry.available("llm")
    if provider not in available:
        logging.getLogger(__name__).warning(
            "config.toml [llm] provider=%r is not a registered LLM adapter (available: %s); "
            "LLM steps will be skipped or refused.", provider, ", ".join(sorted(available)))


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
    global _EXTRACTION_DEFAULTS, _LOADED
    _RUNTIME.clear()
    _LOADED = False
    if persisted:
        for scope in (SCOPE_FEATURES, SCOPE_LLM, SCOPE_EXTRACTION):
            _forget(scope)
    if _EXTRACTION_DEFAULTS is not None:
        ex = get_settings().extraction
        for k, v in _EXTRACTION_DEFAULTS.items():
            setattr(ex, k, v)
        _EXTRACTION_DEFAULTS = None
