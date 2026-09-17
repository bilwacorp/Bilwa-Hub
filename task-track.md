# HUB-Expansion.md Progress Tracker

Read this before doing anything else. It exists so a fresh agent picking
this up tomorrow doesn't have to re-derive what's already built, re-read
every prior turn, or accidentally redo/undo settled work. `git log
--oneline` is the authoritative change history; this file is the
higher-level map of *why* things are where they are and *what's next*.

This file is updated through Phase 4 (deployment lineage), committed on
`main`. Run `git log --oneline` for the exact current HEAD — don't trust
a hardcoded hash here going stale as more phases land.

## Where to start reading (in this order)

1. `docs/architecture/current-state.md` — Phase 0 audit, frozen at that
   point in time (pre-expansion). What existed, what didn't, known bugs.
2. `docs/architecture/target-state.md` — living doc, updated as phases
   land. Shows the target diagram with what's actually built annotated
   in, and the reusable "integration package" shape.
3. `docs/adr/ADR-001-operational-event-model.md`,
   `ADR-002-execution-state-machine.md`, `ADR-003-github-integration.md`,
   `ADR-004-deployment-lineage.md` — the specific design tradeoffs made
   and why, per phase.
4. `docs/integrations/github.md` — full GitHub integration design.
5. `docs/security/integration-security.md` — Phase 3's security review.

## Completed phases (do NOT redo — extend/reuse instead)

**Phase 0 — Repository audit.** `docs/architecture/current-state.md`.
Found: zero test coverage anywhere (fixed by Phase 20 below), no generic
audit log (fixed by Phase 1), no SSRF hardening on
`deployment_client.py`'s outbound calls (still true — see "Known gaps"),
the exact "approved but execution failed" bug later fixed in Phase 12/13.

**422-toast crash fix** (not a numbered phase, a bug the expansion doc
flagged). `frontend/src/lib/utils.ts`'s `errorMessage()` is now the one
shared helper — 11 pages that had their own broken copy were fixed to use
it. If you add a new page with a mutation that can 422, use
`errorMessage(e, 'fallback')`, never roll your own.

**Phase 1 — OperationalEvent.** `app/models.py`'s `OperationalEvent` +
`app/services/events.py`'s `record_event()` + `app/core/event_types.py`'s
string catalog + `app/api/routers/events.py`. Every domain since (Phase
3's GitHub included) emits into this SAME table — never build a second
event/audit system. `correlation_id` threading pattern: generate once per
logical operation, pass it through every `record_event()` call for that
operation. Frontend: `/events` page with entity/status/correlation
filters.

**Phase 12/13 — Execution state machine + idempotency.**
`app/models.py`'s `DeploymentActionExecution`/`DeploymentActionAttempt` +
`app/approvals/deployment_hooks.py`'s `_execute()`. Fixes the real bug
where an approved renew/suspend/change_plan could silently fail to reach
the deployment. `GET/POST /deployments/{id}/action-executions[/retry]`.
Frontend: "Action Executions" section on `DeploymentDetailPage.tsx` with
a Retry button. Deliberately did NOT move execution to Celery (see
ADR-002 decision 6) — that's still a legitimate future improvement, not
done.

**Phase 20 (pulled forward, scoped to what exists) — Test suite.**
`backend/tests/` didn't exist before this session; now has a full
Postgres-backed harness (`conftest.py` — real dedicated test DB
`bilwacorp_hub_test`, per-test transaction rollback via
`join_transaction_mode="create_savepoint"`, Casbin enforcer initialized
once per session). 66 tests as of Phase 4. **Run with:**
```
cd backend && source .venv/bin/activate && python -m pytest tests/ -v
```
No fixture/harness rework needed for future phases — just add
`test_<phase>_*.py` files and reuse `client`/`db_session`/`admin_user`/
`engineer_user`/`no_role_user`/`as_user()`/`make_user()` from
`tests/conftest.py`.

**Phase 3 — GitHub integration.** `app/integrations/github/` (own
package: `models.py`/`client.py`/`security.py`/`sync.py`/`webhooks.py`/
`upsert.py`/`tasks.py`/`api.py`/`webhook_api.py`). Migration `015`.
Permissions `github.view/manage/test_connection/sync`. Frontend:
`/github` page (tabs: Integrations/Repositories/Pull Requests/Issues/
Releases) + a GitHub panel on `DeploymentDetailPage.tsx`. This is now
the **template package shape** for any future integration (Phase 5's
CI/CD, or Monitoring) — see `target-state.md`'s "Integration layer"
section before building a new one from scratch.

**Phase 4 — Deployment Lineage.** `app/models.py`'s `Customer`/
`Application`/`DeploymentRelease` + `Deployment.customer_id`/
`application_id`/`environment`. Migration `016` backfills one `Customer`
per distinct `client_name` (additive — `client_name` itself is untouched,
see ADR-004 decision 1). `Application` sits above Phase 3's `Deployment
↔ GitHubRepository` M:N without adding a second edge of its own (ADR-004
decision 2 — a deliberate scope cut, not an oversight). `DeploymentRelease`
is historized like `DeploymentSnapshot`, not a mutable column; the most
recent row per deployment is its "current" release
(`services/lineage.current_releases_map`). Two ways a row gets created
today: `POST /deployments/{id}/releases` (manual, `deployments.
manage_lineage`) and `services/lineage.infer_release_from_heartbeat`
(automatic, wired into `ingest.py`'s heartbeat handler — matches the
heartbeat's `app_version` against a `GitHubRelease` tag on one of the
deployment's linked repos; no match still records the version change with
`repository_id`/`release_id`/`commit_sha` left NULL). `source=
github_actions`/`ci_cd` are defined in the enum but **not populated by
anything yet** — that's Phase 5's job, see ADR-004 decision 5/consequence
2. New permissions: `customers.view/manage`, `applications.view/manage`,
`deployments.manage_lineage` (all in migration `016`). Frontend:
`/customers` and `/applications` pages (simple CRUD, admin-managed) + a
"Lineage" panel on `DeploymentDetailPage.tsx` (Customer/Application/
Environment/Current version/Release/Commit/Repository/Deployed/Deployed
by, with Edit-lineage and Record-release modals). Full details: ADR-004.

Key things a future phase MUST reuse, not reinvent:
- `services/crypto.py` for any new encrypted credential.
- The ONE shared `celery_app` in `services/notifications/tasks.py` —
  register a new domain's tasks via a bottom-of-file import in that file
  (see its own comment), never a second Celery app/broker.
- `app/db/session.py`'s `make_celery_sessionmaker()` for any new Celery
  tasks module's DB access.
- `<Domain>ApiError`/`<Domain>ConnectionError`/`<Domain>RateLimitError`
  exception shape (mirrors `deployment_client.DeploymentCallError` and
  `github/client.py`'s trio) for any new outbound HTTP client.
- Native Postgres enum ONLY for HUB-owned closed sets; plain string for
  anything sourced from an external system's own vocabulary (ADR-001,
  extended in ADR-003).

## Not started — HUB-Expansion.md phases still pending

**Phase 2 — Correlation IDs (formal, cross-system).** Partially exercised
already (Phase 1/12/13's request→approve→execute chain, Phase 3's
per-webhook-delivery chain both work). What's NOT done: nothing stitches
a support ticket's correlation_id to a GitHub issue's — needs Phase 6
first. Don't build this standalone; it'll fall out of Phase 6.

**Phase 5 — CI/CD integration (recommended next).** `GitHubWebhookEvent`
already persists `workflow_run`/`check_run` deliveries today (any event
type without a handler in `webhooks.py` still gets a row — check
`github_webhook_events` table for real historical data to backfill from
once handlers exist). Reuse the Phase 3 package shape exactly. Also the
natural place to start populating `DeploymentRelease.source=
github_actions`/`ci_cd` (Phase 4 already defined the enum values and the
table shape for this — see ADR-004 decision 5 and its "Consequences"
section — don't re-design the lineage table, just insert rows into it
from the new webhook handler).

**Phase 6 — Support ↔ Engineering link.** Ticket → GitHub issue → PR →
release → deployment. Nothing wired yet; `SupportTicket` has no FK/
reference to any GitHub entity.

**Phase 7 — Maintenance lifecycle.** Still just create/update/delete;
no draft/scheduled/approval-required/notification/active/completed/
failed lifecycle. `MaintenanceWindowStatus` enum would need extending.

**Phase 8 — Generic operational approvals.** The workflow/approval
engine (Phase pre-existing, gates renew/suspend/change_plan only) hasn't
been extended to maintenance or other action types.

**Phase 9 — Unified operational timeline (UI).** No dedicated timeline
page. `OperationalEvent` + `WorkflowHistory` + GitHub tables all have the
data; nothing aggregates them into one per-deployment view yet. The
`/events` page (Phase 1) is the closest thing today, but it's a flat
filtered log, not a curated timeline.

**Phase 10 — Operations dashboard.** Not started at all.

**Phase 11 — Integration Center UI.** Not started — but Phase 3's
`GitHubIntegration.status/last_synced_at/last_webhook_at/last_error(_at)`
fields are exactly the data this page will read. When building it, query
those fields directly; don't add new health-tracking columns.

**Phase 14/15/19 — RBAC/Security/Audit passes.** Largely satisfied
incrementally by what 1/3/12/13 already added (each phase did its own
mini security review — see the ADRs and `integration-security.md`). No
dedicated fleet-wide pass has been done. Known specific gaps still open:
- `services/deployment_client.py`'s outbound calls still have no SSRF
  hardening (flagged in Phase 0 audit, never fixed — `base_url` is
  staff-entered and used directly, no scheme/host allowlist).
- No rate limiting on HUB's own inbound webhook endpoint (flagged in
  Phase 3's security review, not fixed — no existing precedent in this
  codebase for rate-limiting middleware).

**Phase 16/17/18 — Observability / Frontend UX / Deployment detail
page.** Partially organic (deployment detail page already accreted
Health/Action-Executions/GitHub sections across phases) but no dedicated
pass against the doc's suggested structure.

## Known limitations carried forward (see prior phase docs for full detail)

- No frontend automated test framework exists anywhere in this repo
  (Vitest/RTL, etc.) — every frontend change so far has been verified by
  live browser walkthrough (Claude-in-Chrome) + `tsc`/`vite build`, not
  automated tests. If a future phase wants real frontend tests, standing
  up that framework is its own decision point — ask the user first,
  it's a repo-wide infra choice, not a per-phase one.
- GitHub integration: PAT only (no GitHub App), no pagination beyond
  page 1 of any GitHub list endpoint.
- Zero pagination-beyond-first-page anywhere in the GitHub domain by
  design for this pass — see ADR-003 if extending sync depth.

## Environment / how to resume

- Local Postgres running, dev DB `bilwacorp_hub_local`, test DB
  `bilwacorp_hub_test` (auto-recreated by the pytest suite each run).
- Backend: `cd backend && source .venv/bin/activate && uvicorn
  app.main:app --reload` (needs `.env` — already present, not committed).
- Frontend: `cd frontend && npm run dev`.
- Redis is expected running locally for Celery (`.delay()` calls will
  fail fast — 2s timeout — if it's down; harmless in dev, but the
  webhook endpoint's response would 500 if Redis is fully unreachable
  since `.delay()` isn't wrapped in try/except there today).
- Seeded login: `admin` / `ChangeMe@2026`.
