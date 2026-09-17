# GitHub Integration (HUB-Expansion.md Phase 3)

Written before implementation, per Phase 3's own instructions — this is
the design, checked against the actual repo state (post Phase 0/1/12/13),
not a retrospective description.

## What already exists and gets reused, unchanged

- **Encrypted credential storage**: `services/crypto.py`'s Fernet
  encrypt/decrypt, keyed by `HUB_ENCRYPTION_KEY`. Already used for
  `Deployment.action_key_encrypted`. GitHub's access token and webhook
  secret reuse this exact module — no new key, no new crypto code.
- **OperationalEvent**: the cross-domain event log (Phase 1,
  `app/models.py`, `services/events.py`'s `record_event()`). GitHub events
  are new *rows* in this same table via new `event_type` string constants
  in `core/event_types.py` — not a second event system.
- **Machine-to-machine auth pattern**: `register.py`/`ingest.py` already
  establish "some inbound endpoints are authenticated by a shared secret,
  not Casbin" (see `core/permissions.py`'s own comment on this). GitHub's
  webhook endpoint follows the identical shape: no
  `Depends(require_permission(...))`, authenticated by HMAC signature
  instead.
- **Row-level visibility**: `deployments.py`'s `_get_visible_or_404` +
  `services/deployment_scope.assigned_deployment_ids`. Reused as-is for
  the one deployment-scoped GitHub surface (`GET /deployments/{id}/github`)
  — no new visibility mechanism.
- **Celery**: the existing `celery_app` in
  `services/notifications/tasks.py` (broker/backend from
  `settings.CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`, already the
  `celery-worker` container's entrypoint). GitHub's tasks register on
  this *same* app object — not a second Celery app. See "Celery task
  registration" below for the one wrinkle this creates.
- **Permission philosophy**: one permission per meaningful action, not
  per CRUD verb (`core/permissions.py`'s own stated principle). Four new
  permissions, matching the phase brief exactly: `github.view`,
  `github.manage`, `github.test_connection`, `github.sync`.
- **Domain package shape**: `app/workflow/`, `app/rules/`,
  `app/approvals/` are each a self-contained package with their own
  `models.py`/`schemas.py`/`api.py`, mounted into `main.py` directly
  (unlike the simpler CRUD resources under `app/api/routers/`, which share
  the central `app/models.py`/`schemas.py`). GitHub is large and
  self-contained enough to follow the *former* shape:
  `app/integrations/github/`.

## What does NOT exist yet, and what Phase 3 does about it

- **No `Customer` or `Application/Product` table.** The Phase 0 audit
  already flagged this (`docs/architecture/current-state.md`) and
  `Deployment` still holds `client_name` directly. The brief's
  `Customer → Application → Deployment → GitHub Repository` chain can
  only be built as far as `Deployment → GitHub Repository` right now.
  Per the brief's own "do not over-engineer Phase 4 prematurely," this
  phase does **not** invent `Customer`/`Application` tables just to have
  somewhere to hang the diagram — it builds the
  `Deployment ↔ GitHubRepository` link as a genuine many-to-many (a
  deployment can have more than one repo — e.g. separate frontend/backend
  repos — and a repo can serve more than one deployment — e.g. a
  single-tenant-per-deployment product with one shared codebase), so
  Phase 4 has a real edge to extend upward from once `Application` exists.
- **No GitHub App registration flow — at first.** A GitHub App needs its
  own GitHub-side registration (app manifest, private key, installation
  flow, installation-token exchange/caching) — real added complexity with
  no existing precedent in this codebase to build on. Phase 3 uses a
  **personal/fine-grained access token per integration**, encrypted at
  rest with the same `services/crypto.py` Fernet scheme as
  `action_key_encrypted`. This is the documented tradeoff the phase brief
  explicitly allows for ("if GitHub App auth would create excessive
  complexity, document the tradeoff and use the simplest secure
  implementation"). The credential is stored behind one function
  (`client.py`'s internal token resolution) — swapping to GitHub App
  auth later means changing that one function, not every call site. **This
  prediction held**: GitHub App auth was added later, additively — see
  "GitHub App auth mode" below and `docs/adr/ADR-004-github-app-auth.md`.

## Data model

New package `app/integrations/github/models.py` (own `Base.metadata`,
same as `workflow/models.py`/`rules/models.py`):

```
GitHubIntegration
  id, name, github_org (unique), auth_mode ('pat' | 'github_app' — only
    'pat' implemented; the column exists so a future GitHub App mode
    doesn't need a migration to add the enum value later... actually it
    DOES need one, since this is a native Postgres enum — see "Enums vs
    strings" below), access_token_encrypted, webhook_secret_encrypted,
    status ('connected' | 'disconnected' | 'error'), last_synced_at,
    last_webhook_at, last_error, last_error_at, created_by, created_at,
    updated_at

GitHubRepository
  id, integration_id FK, external_id (GitHub's numeric repo id, unique),
  full_name (unique, "org/repo"), name, owner, default_branch, html_url,
  is_active, last_synced_at, created_at

DeploymentGitHubRepository   (join table, composite PK — same shape as
                               DeploymentStaffAssignment)
  deployment_id FK, repository_id FK, is_primary, created_at

GitHubPullRequest
  id, repository_id FK, external_id, number, title, state (GitHub's own
    "open"/"closed" — see "Enums vs strings"), is_draft, author_login,
    html_url, merge_commit_sha, opened_at, merged_at, closed_at,
    github_updated_at
  unique(repository_id, number)

GitHubIssue
  id, repository_id FK, external_id, number, title, state, author_login,
  html_url, opened_at, closed_at, github_updated_at
  unique(repository_id, number)

GitHubRelease
  id, repository_id FK, external_id, tag_name, name, html_url,
  target_commit_sha, is_prerelease, is_draft, published_at, created_at
  unique(repository_id, tag_name)

GitHubCommit   (only commits actually referenced by a push/PR/release —
                 never a full commit-log mirror)
  id, repository_id FK, sha, message, author_name, author_email,
  author_login, html_url, committed_at
  unique(repository_id, sha)

GitHubWebhookEvent
  id, integration_id FK, delivery_id (unique — the idempotency key),
  event_type, payload (JSON), signature_valid, status ('received' |
    'processing' | 'processed' | 'failed'), error, correlation_id,
  received_at, processed_at
```

### Enums vs strings — extending ADR-001's reasoning

`app/models.py`'s `OperationalEvent` already established the rule this
codebase now follows: a column is a native Postgres enum only when the
value set is small, closed, and **owned by HUB**; a column sourced from
an external system's own vocabulary is a plain string, because HUB can't
control when that vocabulary changes and a native enum would need a
migration for every new value GitHub introduces.

Applied here:
- `GitHubIntegration.status`, `GitHubWebhookEvent.status` — HUB-owned,
  small, closed → native enum.
- `GitHubPullRequest.state`, `GitHubIssue.state` — GitHub's own field
  (currently `open`/`closed`, but GitHub has changed PR-state-adjacent
  vocabulary before, e.g. draft PRs) → plain string, `is_draft` split out
  as its own boolean rather than folded into `state`.
- `GitHubIntegration.auth_mode` — this one **is** a native enum despite
  only one value (`pat`) being implemented, because it's small, closed,
  and HUB-owned (HUB decides what auth modes it supports, GitHub doesn't
  hand HUB an open-ended vocabulary here) — adding `github_app` later is
  a deliberate HUB-side decision that should go through a migration
  anyway (it needs new columns — app id, private key — not just a new
  enum value), so the migration cost of extending it isn't the problem
  the string-vs-enum rule is trying to avoid.

### `entity_id`/`actor_id` type mismatch, resolved

`OperationalEvent.actor_id` is `UUID`. A GitHub actor (whoever opened a
PR, pushed a commit, ...) is identified by a GitHub login/numeric id, not
a HUB UUID. Rather than widen `actor_id`'s type (which would touch every
existing emission site across Phases 1/12/13 — explicitly out of scope:
"do not rewrite existing architecture"), GitHub-sourced events use
`actor_type="github_user"`, `actor_id=NULL`, and put the GitHub login in
`event_metadata`. `entity_id` has no such problem — GitHub entities
(repository/PR/issue/release) get real HUB-side UUID rows, so `entity_id`
points at those normally.

### `deployment_id` on GitHub events, given the many-to-many mapping

An `OperationalEvent` for a GitHub happening only gets `deployment_id`
set when the event's repository maps to **exactly one** deployment at
that moment — an unambiguous case. When a repository maps to zero or
several deployments, `deployment_id` stays `NULL` and the event is still
fully findable via `entity_type`/`entity_id` (the repository/PR/etc.) or
`correlation_id`. This means the deployment-scoped GitHub panel
(`GET /deployments/{id}/github`) does **not** rely on `OperationalEvent
.deployment_id` for correctness — it joins through
`DeploymentGitHubRepository` directly, the same way `tickets.py`/
`maintenance.py` already join through their own FK rather than trusting
a denormalized field.

### Correlation / causation

Every `GitHubWebhookEvent` gets its own `correlation_id` (generated at
receipt, stored on the row — same "durable column set once" shape as
`WorkflowInstance.correlation_id` from Phase 12/13) and every
`OperationalEvent` emitted while processing that delivery reuses it — so
"this one push notification produced these N operational events" is one
query. `causation_id` is **not populated** in this phase, same decision
ADR-001 made for Phase 1: it's reserved for Phase 6 (support ticket →
GitHub issue → PR → ...) once a support ticket can actually reference a
GitHub issue, which doesn't exist yet. Wiring `causation_id` now, with
nothing upstream or downstream to chain to, would just be an unused
column with extra code around it.

## Authentication model

Per-integration personal/fine-grained access token, Fernet-encrypted at
rest (`GitHubIntegration.access_token_encrypted`), decrypted only inside
`app/integrations/github/client.py` to build the `Authorization: Bearer`
header for a request — never logged, never returned by any API response
(schemas expose `has_token: bool`, never the token itself, same shape as
`Deployment`'s schemas never echoing `action_key`).

## Webhooks

`POST /github/webhooks/{integration_id}` (public — no Casbin dependency,
same shape as `register.py`/`ingest.py`).

1. Read the raw request body bytes (signature verification needs the
   exact bytes GitHub signed, not a re-serialized JSON object).
2. Verify `X-Hub-Signature-256` via HMAC-SHA256 over the raw body using
   the integration's decrypted `webhook_secret_encrypted`, compared with
   `hmac.compare_digest`. Invalid/missing signature → `401`, and a
   `github.webhook_failed` `OperationalEvent` (signature_valid=False row
   still persisted for audit — see "Audit integrity" below).
3. Insert a `GitHubWebhookEvent` row keyed by `X-GitHub-Delivery`
   (unique). A duplicate delivery (GitHub's own redelivery-on-timeout
   behavior) hits the unique constraint — caught, and the endpoint
   returns `200` immediately without enqueueing a second processing task.
   This is the actual idempotency mechanism, not a best-effort check.
4. Return `202` immediately (before any GitHub-API calls or DB writes
   beyond the one insert) and enqueue `github.process_webhook_event` on
   the existing Celery app.
5. The Celery task does the heavy work: parse the payload for the given
   `event_type`, upsert the relevant `GitHubRepository`/`GitHubPullRequest`
   /`GitHubIssue`/`GitHubRelease`/`GitHubCommit` rows, and call
   `record_event()` for each resulting `OperationalEvent`, all in one
   DB transaction per delivery.

Supported event types for this phase: `issues`, `pull_request`, `push`,
`release`. `workflow_run`/`check_run` (CI/CD-relevant) are deliberately
**not** handled yet — Phase 5 owns that, and the brief says not to build
CI/CD prematurely; `GitHubWebhookEvent` still persists any event type
HUB doesn't yet parse (for forward-compatible audit trail), it just
doesn't try to interpret it into domain rows/OperationalEvents.

## Celery task registration — the one wrinkle

`docker-compose.yml`'s `celery-worker` command is
`celery -A app.services.notifications.tasks:celery_app worker`. Celery's
`-A module:app` only *imports that one module* — it doesn't auto-discover
sibling packages. Rather than relocate the shared `celery_app` object (a
bigger, riskier change touching a working deploy config) or run a second
worker process (a second piece of infrastructure the brief says not to
add without proof it's needed), `services/notifications/tasks.py` gains
one bottom-of-file import:
`from app.integrations.github import tasks as _github_tasks  # noqa: F401`.
Importing that module executes its `@celery_app.task(...)` decorators
against the *same* `celery_app` instance, registering GitHub's tasks on
it — no docker-compose.yml change, no second broker connection, no new
concept.

## Synchronization

- **Manual/initial sync** (`POST /github/repositories/{id}/sync`,
  `github.sync` permission): fetches the repo's current metadata plus the
  first page (100 items) of PRs/issues/releases from the GitHub REST API
  and upserts. Deliberately **not** paginated beyond page 1 in this
  phase — correct for a repo with a normal amount of history, a known
  limitation for a very old/active one (see Known Limitations in the ADR).
- **Webhook-driven sync** keeps things "reasonably current" per the
  brief's own phrasing — a repo nobody ever manually syncs and gets no
  webhook traffic simply has no local data yet, which is the correct,
  honest state (not stale data pretending to be current).
- **Retries**: `app/integrations/github/client.py` raises
  `GitHubRateLimitError` (403 with `x-ratelimit-remaining: 0`, or a plain
  429) and `GitHubApiError` (everything else 4xx/5xx or a transport
  failure) — mirrors `services/deployment_client.py`'s
  `DeploymentCallError` shape exactly, for the same reason: callers catch
  one clear exception type instead of raw `httpx` exceptions. Celery
  tasks list `GitHubRateLimitError` (and connection-level `GitHubApiError`
  subclass, see client.py) in `autoretry_for`, with `retry_backoff=True`
  — the same `autoretry_for`/`retry_backoff`/`retry_jitter`/`max_retries`
  shape `services/notifications/tasks.py` already uses for SMTP/WhatsApp
  retries. No new retry framework.

## Integration health

`GitHubIntegration.status`/`last_synced_at`/`last_webhook_at`/
`last_error`/`last_error_at` are updated at the four points that matter:
a successful/failed sync, and a received/failed webhook. `GET
/github/integrations` returns these fields directly — this *is* the data
Phase 11's Integration Center will render later; Phase 3 doesn't build
that page, just makes sure the fields exist and are kept current.

## Authorization

Four permissions (`core/permissions.py`, migration-seeded): `github.view`,
`github.manage`, `github.test_connection`, `github.sync`. Following the
established "engineer keeps action permissions, not admin-config ones"
pattern (migrations 011/012/013/014): `admin` gets all four; `engineer`
gets `view`/`test_connection`/`sync` (diagnostic/action-shaped, same
tier as `deployments.check_health`) but not `manage` (creating/editing an
integration's credentials is config-shaped, same tier as
`deployments.create`).

Repository/PR/issue/release browsing (`GET /github/...`) is **not**
row-scoped the way deployments/tickets/maintenance are — a GitHub
repository isn't inherently one customer's confidential data the way a
support ticket is, and the brief's own suggested permission list has no
`github.view_all` companion permission (unlike `deployments.view`/
`deployments.view_all`), which is the tell that row-scoping wasn't
intended here. The one place row-scoping *does* apply is the
deployment-scoped view — `GET /deployments/{id}/github` — which goes
through `deployments.py`'s existing `_get_visible_or_404` exactly like
every other route that takes a `deployment_id`.

## Frontend

One page, `pages/github/GitHubPage.tsx`, internal tabs (Integrations /
Repositories / Pull Requests / Issues / Releases) rather than five routed
pages with a new nested-sidebar-group UI framework — the existing
`Sidebar`/`NAV_ITEMS` shape (`App.tsx`) is a flat list with no
collapsible-group support, and building one is a UI-framework change
disproportionate to "keep this lightweight." One new flat nav entry
("GitHub") is added instead. `DeploymentDetailPage.tsx` gets one new
section reading `GET /deployments/{id}/github`.

## GitHub App auth mode (added later — see ADR-004)

A second `GitHubIntegration.auth_mode`, `github_app`, alongside the `pat`
mode above (both coexist; nothing here changes PAT-mode behavior). Full
design/reasoning in `docs/adr/ADR-004-github-app-auth.md` — summary:

- **One GitHub App, configured via `Settings`** (`GITHUB_APP_ID`,
  `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_WEBHOOK_SECRET`
  — `core/config.py`), not per-integration DB columns. Empty/unconfigured
  by default.
- **Installation flow only** (`app/integrations/github/app_auth.py`) — no
  OAuth user-login exchange, since HUB only ever needs to act as the
  installed app, never as a GitHub user. `GET /github/app/install-url`
  (`github.manage`) returns
  `https://github.com/apps/{slug}/installations/new?state=<signed>`;
  GitHub redirects back to `GET /github/app/callback` after install.
- **The `installation` webhook (`POST /github/app/webhooks`, the App's
  one shared webhook URL) is the actual source of truth for provisioning
  a `GitHubIntegration` row** (`app_auth.upsert_installation`,
  idempotent by `installation_id`) — the callback redirect is UX/
  attribution only and degrades gracefully if it's ever dropped.
- **Installation access tokens are minted via a JWT signed with the
  App's private key** (`app_auth._app_jwt`, RS256 via the
  `python-jose[cryptography]` dependency already used for staff login),
  then cached (encrypted, reusing `access_token_encrypted`) with
  `access_token_expires_at` and refreshed automatically —
  `client.py`'s `_resolve_token` branches on `auth_mode` so every existing
  GitHub API call (`test_connection`/`get_repository`/`list_*`) works
  unchanged for either mode.
- **Repository access is auto-registered from the installation's own
  webhooks** — no manual "add repository" step needed for a github_app-
  mode integration (it's still available, and still the only option for
  PAT mode). `installation`'s `repositories` list (initial grant) and
  `installation_repositories`'s `repositories_added`/`repositories_removed`
  (later changes) are both handled asynchronously via `webhooks.py`'s
  normal `_HANDLERS` dispatch (`app_auth.register_repositories`/
  `deactivate_repositories`). A removed repo is deactivated
  (`GitHubRepository.is_active=False`), never deleted, and reactivated in
  place if access is re-granted later.
- **The GitHub App itself can be auto-created via GitHub's Manifest
  flow** ("Set up GitHub App" in the UI, ADR-004 decision #9) — no
  Developer Settings form-filling, no env vars to paste. `POST
  /github/app/manifest` builds the manifest from the request's own
  origin; the frontend submits it to github.com via a hidden auto-submit
  form; `GET /github/app/manifest-callback` (the one route here that
  *requires* `github.manage` auth, unlike the public install `/callback`)
  exchanges the resulting one-time code and stores the App's credentials
  in a new `GitHubAppConfig` DB row. `app_auth._load_app_credentials`
  resolves DB row vs. env vars (DB-first) everywhere the App's
  credentials are needed — manually configuring `GITHUB_APP_*` still
  works exactly as before if you never run this flow.

## What Phase 3 deliberately does not do

- No GitHub App auth (see Authentication above — added later, see
  "GitHub App auth mode" above).
- No pagination beyond page 1 of any GitHub list endpoint.
- No CI/CD event handling (`workflow_run`/`check_run` webhooks are
  received and persisted, not interpreted) — Phase 5.
- No `Customer`/`Application` tables — Phase 4.
- No ticket ↔ issue linking — Phase 6.
- No Integration Center UI — Phase 11 (the health fields it will read
  already exist).
