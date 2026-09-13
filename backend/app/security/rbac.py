"""Role-based access control.

Three roles with a permission matrix. Configuration actions (editing the template,
publishing the line-item set, changing page scope, changing export inclusions, managing
documents) are **admin-controlled**; the analyst gets a deliberately simple flow
(view/edit values, notes, commentary, export). Reviewers sit in between.

Identity comes from a **session token** (``Authorization: Bearer …``) issued by
``POST /auth/login`` — see ``session.py``. For local dev, tests, and service-to-service
calls, an ``X-Role`` header is also accepted as an explicit credential, but only when
``auth.allow_role_header`` is enabled (config.toml) — turn it off in production so a
real session is the only way in. ``current_principal`` resolves the caller (401 if it
cannot); ``require()`` is a FastAPI dependency factory that returns 403 when the
resolved role lacks a permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, Header, HTTPException, status


class Role(str, Enum):
    ADMIN = "admin"
    REVIEWER = "reviewer"
    ANALYST = "analyst"


class Permission(str, Enum):
    # configuration & oversight — admin-controlled
    CONFIG_TEMPLATE = "config:template"     # author/edit templates
    # The single configuration engine. Was ``config:ontology`` when the ontology was a
    # separate, selectable configuration surface; line items is now the only one, so the
    # permission names it. The string is served to the client and checked with ``useCan``.
    CONFIG_LINE_ITEMS = "config:line_items"  # author/edit the line-item set (mapping config)
    CONFIG_SCOPE = "config:scope"
    # TRANSITIONAL ALIAS — delete with the last reference (B5: routes/ontologies.py is
    # removed, routes/line_items.py moves to CONFIG_LINE_ITEMS). It shares the
    # ``config:line_items`` value, so it is an *alias* member: it is absent from
    # ``set(Permission)`` and from every permission list served to the client, and
    # ``Permission("config:ontology")`` no longer resolves. It exists only so the two
    # call sites in files this change may not touch keep importing.
    CONFIG_ONTOLOGY = "config:line_items"
    CONFIG_EXPORT = "config:export"
    CONFIG_SETTINGS = "config:settings"     # LLM config / feature flags / interface / review toggle
    DOCUMENTS_MANAGE = "documents:manage"   # upload source documents
    ADMIN_USERS = "admin:users"             # manage users
    AUDIT_VIEW = "audit:view"               # inspect the run/token audit log
    # working permissions
    TEMPLATE_SELECT = "template:select"     # choose an existing output template (not author)
    PIPELINE_RUN = "pipeline:run"           # run the extraction pipeline end to end
    EXTRACTION_VIEW = "extraction:view"
    EXTRACTION_EDIT = "extraction:edit"
    REVIEW_VIEW = "review:view"
    REVIEW_RESOLVE = "review:resolve"
    REVIEW_SUBMIT = "review:submit"         # analyst sends final output for review
    REVIEW_FINALIZE = "review:finalize"     # reviewer finalizes the output
    NOTES_VIEW = "notes:view"
    COMMENTARY_VIEW = "commentary:view"
    ANALYSIS_RUN = "analysis:run"           # trigger a live LLM financial-analysis run
    EXPORT_RUN = "export:run"
    INTEGRITY_VIEW = "integrity:view"


_ALL = set(Permission)

# Base role→permission map. The three roles model a linear workflow:
#   Analyst  — upload, pick a template, run the pipeline end to end, submit for review.
#   Reviewer — review, correct and finalize the output, then deliver it.
#   Admin    — configuration (templates/line items/LLM/settings), users and audit logs.
# EXPORT_RUN vs REVIEW_SUBMIT for the analyst is resolved at runtime by the
# ``review_required`` flag (see ``effective_permissions``).
PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(_ALL),
    Role.REVIEWER: {
        Permission.EXTRACTION_VIEW, Permission.EXTRACTION_EDIT,
        Permission.REVIEW_VIEW, Permission.REVIEW_RESOLVE, Permission.REVIEW_FINALIZE,
        Permission.NOTES_VIEW, Permission.COMMENTARY_VIEW, Permission.ANALYSIS_RUN,
        Permission.EXPORT_RUN, Permission.INTEGRITY_VIEW,
    },
    Role.ANALYST: {
        Permission.DOCUMENTS_MANAGE, Permission.TEMPLATE_SELECT, Permission.PIPELINE_RUN,
        Permission.CONFIG_SCOPE,  # analysts confirm page scope as part of running the pipeline
        Permission.EXTRACTION_VIEW, Permission.EXTRACTION_EDIT,
        Permission.NOTES_VIEW, Permission.COMMENTARY_VIEW, Permission.ANALYSIS_RUN,
        Permission.INTEGRITY_VIEW, Permission.REVIEW_SUBMIT, Permission.EXPORT_RUN,
    },
}

# Which screens each role sees (drives the frontend nav). The Review Queue is the
# human-in-the-loop QA surface (unplaced face figures, failed validation rules, and subtotals
# that do not match their extracted components) and
# is available to everyone who works an extraction — the analyst included — independent
# of the ``review_required`` flag. That flag governs only the second-person reviewer
# SIGN-OFF (analyst submits vs. finalizes), never the QA screen itself.
#
# The analyst sees the ``template`` screen too, but as a **view-and-select** surface:
# they need to see the template they're extracting into and switch between existing
# templates (``TEMPLATE_SELECT``). Authoring/editing a template stays admin-only
# (``CONFIG_TEMPLATE``), enforced both on the write endpoints and on the screen's
# editing controls.
SCREENS_BY_ROLE: dict[Role, list[str]] = {
    # "audit" is the deployment-wide run trail — every run against every filing, with what each
    # spent. Admin only, and the only screen that crosses document ownership, which is why it is
    # gated on AUDIT_VIEW rather than on COMMENTARY_VIEW like the per-document trail beside it.
    Role.ADMIN: ["upload", "integrity", "scope", "extraction", "workspace", "notes", "review",
                 "commentary", "disclosures", "template", "line_items", "settings", "audit", "export"],
    # A reviewer sees the extraction screen to READ a run — its stages, its log, its rows — and
    # cannot start one: PIPELINE_RUN is the analyst's and the admin's. Granting the screen without
    # the permission is deliberate, not an oversight; the screen's own re-extract control is gated on
    # the permission, so a reviewer is never shown a button that would 403.
    Role.REVIEWER: ["integrity", "extraction", "workspace", "notes", "review", "commentary", "disclosures",
                    "export"],
    # Analysts run the operational pipeline; configuration surfaces (Template & Line Items
    # authoring, Settings) are admin-only. Analysts still pick a template on the Upload
    # screen (TEMPLATE_SELECT), they just don't get the authoring/config screen.
    # "extraction" is the screen that RUNS the pipeline and reports its progress. Every role that
    # works an extraction gets it, and no new Permission is needed: all three already hold
    # EXTRACTION_VIEW, and starting a run is separately gated on PIPELINE_RUN at the endpoint.
    Role.ANALYST: ["upload", "integrity", "scope", "extraction", "workspace", "notes", "review",
                   "commentary", "disclosures", "export"],
}


def parse_role(value: str | None) -> Role:
    try:
        return Role(value) if value else Role.ANALYST
    except ValueError:
        return Role.ANALYST


@dataclass
class Principal:
    """The authenticated caller: their role plus who/how they authenticated."""

    role: Role
    username: str
    name: str
    via: str  # "session" | "role-header"
    # THE EDITING SESSION, empty when there is none to attribute — the `X-Role` dev header and
    # every service call. An opaque handle, never the bearer token: it is written to the database
    # so a configuration version can record the sitting it belongs to.
    session_id: str = ""


def current_principal(
    authorization: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> Principal:
    """Resolve the caller from a session token, or the X-Role dev header if allowed.

    A valid ``Authorization: Bearer`` **session token is authoritative** and is checked
    first, so a real session's role can never be downgraded or escalated by a header.
    Only when there is no valid session is the ``X-Role`` header considered, and even
    then solely as a dev/service credential when ``auth.allow_role_header`` is enabled
    (it is **off by default** — turn it on explicitly for local dev / CI). Raises 401
    when neither yields a principal.
    """
    from app.config import get_settings

    from .session import resolve_session

    settings = get_settings()

    # 1. Session token is authoritative.
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            sess = resolve_session(token.strip())
            if sess is not None:
                return Principal(role=sess.user.role, username=sess.user.username,
                                 name=sess.user.name, via="session", session_id=sess.id)

    # 2. Dev/service fallback — only when there is no valid session and it is enabled.
    if x_role and settings.auth.allow_role_header:
        role = parse_role(x_role)
        return Principal(role=role, username=f"{role.value}@service",
                         name=f"{role.value.title()} (service)", via="role-header")

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def current_role(principal: Principal = Depends(current_principal)) -> Role:
    """FastAPI dependency: the authenticated caller's role."""
    return principal.role


def permissions_for(role: Role) -> set[Permission]:
    return PERMISSIONS.get(role, set())


def effective_permissions(role: Role) -> set[Permission]:
    """Static role permissions adjusted for the runtime ``review_required`` flag.

    When review is required the analyst *submits* the output for review and cannot
    deliver it (no EXPORT_RUN); when review is disabled the workflow closes at the
    analyst, who finalizes and exports directly (no REVIEW_SUBMIT). Other roles are
    unaffected.
    """
    from app.services.settings_state import get_review_required

    perms = set(permissions_for(role))
    if role is Role.ANALYST:
        if get_review_required():
            perms.discard(Permission.EXPORT_RUN)
        else:
            perms.discard(Permission.REVIEW_SUBMIT)
    return perms


def screens_for(role: Role) -> list[str]:
    """Screens visible to a role. The Review Queue (human-in-the-loop QA) is NOT gated
    by ``review_required`` — that flag only affects the reviewer sign-off, handled via
    ``effective_permissions`` (analyst submit vs. export)."""
    return list(SCREENS_BY_ROLE.get(role, []))


def has_permission(role: Role, perm: Permission) -> bool:
    return perm in effective_permissions(role)


def require(perm: Permission):
    """Dependency factory enforcing a permission; 403 if the role lacks it."""

    def _dep(role: Role = Depends(current_role)) -> Role:
        if not has_permission(role, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {role.value!r} lacks permission {perm.value!r}",
            )
        return role

    return _dep
