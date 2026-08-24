# RBAC & financial-analysis commentary

## Role-based access control

Three roles with a permission matrix (`backend/app/security/rbac.py`), modelling a linear
workflow: **Analyst → (Reviewer) → deliver**, with **Admin** owning configuration and oversight.

The screen lists are `SCREENS_BY_ROLE` in that file, and they are the same 11 ids
`frontend/src/screens/config.ts::SCREENS` registers.

| Role | Workflow responsibility | Sees (screens) |
|---|---|---|
| **analyst** | Upload a document, **choose** an output template, run the pipeline end to end (integrity → scope → **extraction** → QA in the Review Queue), then **submit the final output for review**. | upload, integrity, scope, **extraction**, workspace, notes, **review (QA)**, commentary, export — 9 |
| **reviewer** | **Review & finalize** the analyst's output, then deliver it. | integrity, **extraction**, workspace, notes, review, commentary, export — 7 |
| **admin** | Configuration (templates, ontology, **LLM config**, extraction thresholds, feature flags, the review toggle, the sample project), users, and the **audit log**. | all 11 screens incl. Template & Ontology and **Settings** |

A reviewer holds the **extraction** screen to READ the latest run and cannot start one:
`pipeline:run` belongs to the analyst and the admin, and the screen's re-extract control is
gated on the permission, so a reviewer is never shown a button that would 403. What the
reviewer actually gets is the run's **rows, rulebook and status** — the read path is
`GET /documents/{id}/run`, which serves `{run_id, status, rulebook, result}` and **not** the
per-stage progress record or the log tail (those are on `GET /extractions/{run_id}`, and the
progress panel is rendered only on the `pipeline:run` path). `rbac.py`'s own comment says a
reviewer reads "its stages, its log, its rows"; the first two do not reach them today, which
is a defect in the endpoint (or in the comment), not a documented feature.

Representative permissions: `documents:manage`, `template:select` (analyst *chooses* a
template) vs `config:template` (admin *authors* one), `pipeline:run`, `extraction:edit`,
`review:submit` (analyst hand-off) vs `review:finalize` (reviewer), `export:run`,
`config:settings`, `audit:view`.

### Reviewer sign-off toggle (`review_required`)

Admin-controlled feature flag (Settings screen; default **on**). It governs the
**second-person sign-off only**:

- **On** — the analyst *submits* the output for review (`review:submit`, no `export:run`);
  the reviewer reviews, finalizes and delivers.
- **Off** — the workflow **closes at the analyst**, who finalizes and exports directly
  (`export:run`, no `review:submit`).

Crucially, this flag never removes the **human-in-the-loop Review Queue** (the accounting
checks — balance, subtotals, signs, the template's and rulebook's declared arithmetic —
plus the one row-shaped finding: a face figure that reaches no line of the output). That QA
screen stays available to the analyst in both
modes — the flag only changes who performs the final sign-off/hand-off. Implemented as
`effective_permissions(role)` (adjusts the analyst's export vs submit at runtime); screen
visibility is unaffected by the flag.

**Enforcement** — `require(permission)` is a FastAPI dependency on every config/mutating
endpoint (create template/ontology, edit/revert line item, template config data, export,
settings PATCH, …) returning **403** when the role lacks the permission. The whole
`/projects` router additionally requires an authenticated principal (401 otherwise).
`GET /me` returns the caller's user, role, permissions, and the screens the role may see.

**Identity — session login.** Identity is a **session bearer token** issued by
`POST /auth/login` (see `backend/app/security/session.py` and
[08-configuration-and-auth.md](08-configuration-and-auth.md)). `current_principal`
resolves the `Authorization: Bearer …` session **first and treats it as authoritative**
(so a header can never escalate or downgrade a logged-in user), falling back to an
`X-Role` dev/service header only when there is no valid session **and** only when
`auth.allow_role_header` is enabled — which is **off by default**. It raises **401**
when neither yields a principal. The permission model and its server-side enforcement
are real; swapping the in-memory session store for a real IdP (OIDC/SAML) does not
change the matrix.

**Frontend** — the app renders a **login screen** until a session exists; `GET /me`
drives everything after. The nav rail filters to `me.screens`; routes are guarded
(`RequireScreen` redirects a role away from a screen it can't see); a top-bar user menu
shows the signed-in user and a sign-out button; and gated controls are hidden/disabled via
`useCan(permission)` — template/ontology authoring and the extraction thresholds
(`config:template` / `config:ontology` / `config:settings`), export "Include" options,
page-scope toggles, the Extraction screen's re-extract button (`pipeline:run`), the review
queue's accept/withdraw (`review:resolve`) and its flip-sign and **re-map** actions
(`extraction:edit`). The server still enforces regardless of the UI.

## Financial-analysis commentary (the Analysis tab)

A one-page, data-driven commentary derived from the *extracted* statements
(`backend/app/services/commentary.py`). Two endpoints, one builder: a real uploaded
document's commentary is `GET /documents/{id}/commentary`
(`build_commentary_from_rows` over that run's rows), and the seeded sample's is
`GET /projects/{id}/commentary` (`build_commentary`). Permission `commentary:view`;
localized via the `locale` param, with the catalog strings localized on the way out
(`documents.py::_localize_commentary`).

- **Ratios** computed from the statement rows: current ratio, debt-to-equity, equity
  ratio, interest coverage, net (pre-tax) margin, YoY revenue growth, cash ratio, asset
  turnover — each tone-coded (good/warn/bad) by threshold.
- **Strengths & risks** are *selected from a fixed, localizable catalog* by threshold
  (e.g. debt-to-equity ≤ 0.5 → "conservatively financed"; open review items →
  "figures provisional pending sign-off"), so the commentary is genuine analysis rather
  than free-form text, and every emitted string is translatable (en/zh/ar/fr).
- **Year-on-year trends** — a period-over-period block (FY25 vs FY24, from the prior-year
  `v2` column) for revenue, pre-tax profit, net margin, operating cash flow, total assets,
  equity, debt-to-equity and interest coverage. Each carries a direction, a delta
  (YoY % for amounts, percentage-points for margins, absolute change for ratios), and a
  favourable/tone flag that understands lower-is-better metrics (a falling debt-to-equity
  is *good*). Trends also feed momentum-based strengths (deleveraging, margin expansion).
- **Headline + assessment** summarise the position; a **data-quality** note surfaces that
  figures are provisional while review items are open. Open review items temper an
  otherwise-strong headline down to "mixed".
- It is an automated analytical summary, **not investment advice** (stated in the UI).

The frontend renders it as a printable one-pager (`frontend/src/screens/Commentary.tsx`):
headline + assessment, a tone-coded metric grid, a **year-on-year trends grid**, and
strengths/risks columns.

## Audit log & LLM runs

The Analysis screen also carries an **audit log** (`GET /projects/{id}/audit`) — a table of
LLM/extraction runs showing **input and output token usage separately** (plus the total),
the model, action and timestamp. Each run's id combines the **entity name + date/time**
(`services/audit.make_run_id`, e.g. `reliance-industries-ltd-20260807-021455`), which is also
the `ExtractionRun` primary key.

The ledger itself is **process-local and in memory** (`services/audit.py`) — it is lost on
restart and not shared between processes. The durable table it stands in for is the
unbuilt `EditEvent` / run ledger; swapping it in does not change these signatures. The runs
themselves are durable: `extraction_runs` rows survive, they simply do not carry the token
columns.

The run is **analyst-driven** — analyst, reviewer and admin hold `analysis:run` and can
trigger a live **`POST /projects/{id}/analysis`**: it calls the configured LLM provider on
the project's figures (`services/analysis_llm.py`) and records a real audit entry with the
provider's token usage. Viewing the log needs only `commentary:view`. Runs against an
unconfigured/unreachable provider are recorded as `failed` (tokens shown as "—").
