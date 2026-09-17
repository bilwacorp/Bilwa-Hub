# HUB-Expansion.md Progress Tracker

Read this before doing anything else. It exists so a fresh agent picking
this up tomorrow doesn't have to re-derive what's already built, re-read
every prior turn, or accidentally redo/undo settled work. `git log
--oneline` is the authoritative change history; this file is the
higher-level map of *why* things are where they are and *what's next*.

This file is updated through Phase 19 (Audit Requirements) — every
numbered `HUB-Expansion.md` phase except 2 (subsumed, see its own entry
below) is now landed. Phase 20 (test suite) was done as a Phase-0
pull-forward, well before Phase 19. Committed on `main`. Run `git log
--oneline` for the exact current HEAD — don't trust a hardcoded hash
here going stale.

## Where to start reading (in this order)

1. `docs/architecture/current-state.md` — Phase 0 audit, frozen at that
   point in time (pre-expansion). What existed, what didn't, known bugs.
2. `docs/architecture/target-state.md` — living doc, updated as phases
   land. Shows the target diagram with what's actually built annotated
   in, and the reusable "integration package" shape.
3. `docs/adr/ADR-001-operational-event-model.md`,
   `ADR-002-execution-state-machine.md`, `ADR-003-github-integration.md`,
   `ADR-004-deployment-lineage.md`, `ADR-005-cicd-integration.md`,
   `ADR-006-support-engineering-link.md`,
   `ADR-007-maintenance-lifecycle-and-approvals.md`,
   `ADR-008-timeline-dashboard-integration-center.md`,
   `ADR-009-rbac-audit-and-security-review.md`,
   `ADR-010-audit-requirements.md`,
   `ADR-011-observability-ux-deployment-page.md` — the specific design
   tradeoffs made and why, per phase.
4. `docs/integrations/github.md` — full GitHub integration design.
5. `docs/security/integration-security.md` — Phase 3's security review.

## Completed phases (do NOT redo — extend/reuse instead)

**Phase 0 — Repository audit.** `docs/architecture/current-state.md`.
Found: zero test coverage anywhere (fixed by Phase 20 below), no generic
audit log (fixed by Phase 1), no SSRF hardening on
`Deployment.base_url` used by `deployment_client.py`'s outbound calls
(fixed by Phase 15 — see ADR-009), the exact "approved but execution
failed" bug later fixed in Phase 12/13.

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
once per session). 123 tests as of Phase 19. **Run with:**
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
Releases) + a GitHub panel on `DeploymentDetailPage.tsx`. This is the
**template package shape** for a future integration that has its OWN
credentials/webhook endpoint (e.g. Monitoring) — see `target-state.md`'s
"Integration layer" section before building a new one from scratch.
Phase 5's CI/CD integration deliberately did NOT follow this shape since
GitHub Actions rides this same package's webhook — see that phase's own
entry below and ADR-005.

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

**Phase 5 — CI/CD webhook integration.** `app/integrations/cicd/`
(`provider.py`'s `DeploymentProvider`/`DeploymentEventData` abstraction +
`github_actions.py`'s `GitHubActionsProvider`) plus one new case in
`app/integrations/github/webhooks.py`'s existing `_HANDLERS` dict:
`workflow_run`. A successful, deploy-named (`name`/`path` contains
"deploy") workflow run creates a `DeploymentRelease` row via
`services/lineage.record_provider_deployment_event` (new — factored
`_match_release_by_tag` out of Phase 4's `infer_release_from_heartbeat`
for reuse here), `source=github_actions`. Deliberately did NOT create a
new `app/integrations/cicd_github/` package following Phase 3's full
shape — GitHub Actions rides Phase 3's existing GitHub webhook
(no separate credentials/endpoint of its own), so only the provider
abstraction + one handler case were needed; see ADR-005 for the full
reasoning and for why `workflow_run` was chosen over GitHub's separate
Deployments API (`deployment_status`). No migration — reuses Phase 4's
`DeploymentRelease` table and `DeploymentReleaseSource.github_actions`
enum value verbatim. No frontend changes — `DeploymentDetailPage.tsx`'s
Lineage panel (Phase 4) already renders whatever `current_release` is on
file regardless of source.

**Phase 6 — Support ↔ Engineering link.** `app/models.py`'s
`TicketLinkType`/`SupportTicketLink` (migration `017`). A ticket can link
to a GitHub issue/PR/release or a maintenance window —
`SupportTicket.deployment_id` (Phase 1) already covers "affected
deployment," so that's not a separate `TicketLinkType` member; no
`incident` member either (no Incident concept anywhere in this
codebase — see ADR-006 decision 1). `app/services/ticket_links.py`:
`resolve_link_display` (label/url/status per link, denormalized like
Phase 4's `DeploymentReleaseOut`) and `ticket_timeline` — the "ticket
timeline" HUB-Expansion.md asks for, built by aggregating existing
`OperationalEvent` rows across the ticket + its deployment + everything
it's linked to, keyed by entity reference (NOT a shared/propagated
`correlation_id` — see ADR-006 decision 4 for why that specifically
doesn't work for retroactive many-to-one linking, and why this also
effectively resolves Phase 2's "stitch a ticket's correlation_id to a
GitHub issue's" gap). New endpoints: `GET/POST /tickets/{id}/links`,
`DELETE /tickets/{id}/links/{link_id}`, `GET /tickets/{id}/timeline`.
New permission `tickets.manage_links` (admin + engineer). No automatic
ticket-closing logic was added (HUB-Expansion.md's explicit instruction)
and no ticket-assignee feature either (unrelated to this phase's actual
scope — see ADR-006 decision 2). Frontend: a new `SupportTicketDetailPage.tsx`
(`/tickets/:ticketId`) with Links (add/remove, target picker scoped to
the ticket's own deployment's linked GitHub repo(s)/maintenance windows)
and Timeline sections — `SupportTicketsPage.tsx`'s inline status-update
modal moved there too. Full details: ADR-006.

**Phase 7 — Maintenance lifecycle.** `MaintenanceWindowStatus` widened
from 4 to 9 values (migration `018`) — `draft`/`approval_required`/
`approved`/`notification`/`failed` are new; `planned`/`in_progress` are
NOT renamed to the doc's own "scheduled"/"active" wording (see ADR-007
decision 1 for why that's the correct reading of "existing behavior must
remain compatible"). New `MaintenanceWindow` columns: `expected_impact`,
`actual_impact`, `approved_by`. New endpoint `POST /maintenance-windows/
{id}/submit` (draft → gate-or-schedule decision). `core/
maintenance_scheduler.py` now: promotes `approved` → `planned` every
tick, treats `notification` identically to `planned` everywhere, and —
important fix pulled in from Phase 9's own needs — actually calls
`record_event()` on auto-transitions (`maintenance.started`/
`maintenance.completed`), where it previously only logged. Still binary
single-deployment-or-fleet-wide targeting — a `MaintenanceWindowDeployment`
join table for "specific multiple deployments" was deliberately deferred,
see ADR-007 "Consequences." Frontend: `MaintenanceWindowsPage.tsx` gained
expected-impact input, a save-as-draft checkbox, a Submit action, and the
new statuses in its badge map.

**Phase 8 — Generic operational approvals.** Verified `app/approvals/
integration.py`'s `start_approval()` + `app/approvals/hooks.py`'s
`register_completion_hook()` ALREADY are the "request approval / provide
context / register completion callback" interface the phase brief
describes — zero changes to `app/workflow/` or `app/rules/`. The actual
work is `app/approvals/maintenance_hooks.py` (new, ~100 lines), the
second business domain on that interface after `deployment_hooks.py`,
gating Phase 7's `approval_required` state. Migration `018` also seeds a
fourth built-in gated workflow, `maintenance_window_approval` (same
BPMN-template shape as migration 012's three deployment ones, routed to
'admin'), active by default. No `DeploymentActionExecution`-style
execution-tracking table for maintenance — there's no second external
call to retry, see ADR-007 decision 3.

**Phase 9 — Unified operational timeline.** `GET /deployments/{id}/timeline`
(`api/routers/deployments.py`) — deliberately simpler than Phase 6's
`ticket_timeline`: `deployment_id` is already the one column nearly
every domain's events carry, so this is a plain filtered `OperationalEvent`
query, no entity-type union needed. Frontend: new shared
`components/ui/EventTimeline.tsx` (factored out of Phase 6's
`SupportTicketDetailPage.tsx`) + a "Timeline" section on
`DeploymentDetailPage.tsx`. No new tables, no new permission (reuses
`deployments.view`'s existing row-scoping).

**Phase 10 — Operations dashboard.** `GET /dashboard`
(`app/services/dashboard.py`, migration `019` seeds `dashboard.view`) —
fleet/deployments/support/maintenance/approvals/integrations summaries +
an "Attention Required" list, every number a real query over existing
data (no new tables). Row-scoped like every other router for
deployment-tied sections; Approvals/parts of Integrations zero out
(not 403) when the caller lacks that domain's own view permission — see
ADR-008 decision 2. "Outdated version"/"high risk"/"unassigned ticket"/
"escalated"/"awaiting engineering" are documented proxies with no
first-class flag anywhere else in this codebase — see ADR-008 decision 3
for the exact definition of each before reusing or changing one.
Frontend: new `/dashboard` page, first in the nav.

**Phase 11 — Integration Center.** `GET /integrations`
(`api/routers/integrations.py`, migration `020` seeds
`integrations.view`) — one card per configured `GitHubIntegration`
(or a placeholder if none), plus single CI/CD/Email/WhatsApp/Monitoring
cards. Never makes a live external call itself (the one exception, the
GitHub "Test Connection" button, reuses the already-existing
`POST /github/integrations/{id}/test-connection` endpoint) and never
returns a secret. CI/CD's card derives status from whether any
`DeploymentRelease.source IN (github_actions, ci_cd)` row exists —
consistent with ADR-005's "rides the GitHub webhook" design. Email/
WhatsApp read `NotificationLog` gated by whether the relevant SMTP/
WhatsApp setting is non-empty. Monitoring is a permanent
`not_configured` placeholder — no monitoring integration exists.
Frontend: new `/integrations` page.

**Phase 14/15 — RBAC permission audit + security review.** ADR-009. No
new permissions added to `core/permissions.py` — all twelve of Phase
14's "potential new permissions" were checked against the existing
45-permission catalog and either already existed under a different name
or were deliberately rejected (see ADR-009 decision 1's table before
assuming any of them is still missing). Phase 15's sixteen-item security
checklist found two real, previously-flagged gaps and fixed both:
SSRF hardening on `Deployment.base_url` (`app/core/url_safety.py` —
two-layer sync format/literal-IP check + async DNS-resolution check,
wired into `RegisterRequest` and `api/routers/register.py`) and rate
limiting on the two genuinely public endpoints (`app/core/rate_limit.py`
— fail-open Redis fixed-window counter, applied to `/register` by caller
IP and the GitHub webhook by integration id, never to a Casbin-gated
staff route). Every other checklist item was reviewed and found already
correct with no code change — see ADR-009 for the item-by-item writeup.

**Phase 19 — Audit requirements.** ADR-010. `api/routers/users.py` and
`api/routers/rbac.py` had zero `record_event()` calls before this phase
— now emit ten new event types (`STAFF_*`/`ROLE_*` in
`event_types.py`) covering create/update/role-change/deactivate/
reactivate/password-reset for staff and create/rename/delete/
permissions-updated for roles, each with before/after state in
`metadata.changes`. New `OperationalEvent.actor_ip` column (migration
`021_audit_ip_rbac_events.py`) populated via a `contextvars`-based
request-scoped IP (`app/core/request_context.py` + `main.py`'s
`client_ip_middleware` reading `X-Real-IP`) — zero changes needed to any
of the ~30 pre-existing `record_event()` call sites. Audit immutability
(no PATCH/DELETE anywhere for `OperationalEvent`) was already true by
omission; verified with a test rather than new code
(`test_events_router_has_no_write_endpoints`).

**Phase 16/17/18 — Observability / Frontend UX / Deployment detail
page.** ADR-011. Phase 16 has no page of its own — its seven summary
fields (heartbeat/health/version/last-deployment/maintenance-state)
landed inside Phase 18's restructured Overview + Technical sections,
since building a standalone page would have duplicated data already
reachable from the detail page. Phase 17's sidebar gained exactly two
real groups — **Fleet** (Deployments/Customers/Applications) and
**Workflows** (Workflows/Approval Rules) — not the doc's full suggested
nested tree (Escalations/Calendar/Engineering sub-pages etc. were
deliberately not built per the doc's own "don't add pages merely because
a table exists" rule); a group header only renders when 2+ of that
group's items are visible to the caller's permissions. Fixed a
pre-existing bug found while auditing the nav: `ApprovalRulesPage.tsx`
had a working route with no nav link at all — now reachable via
Workflows. `DeploymentDetailPage.tsx` restructured into Header (health/
environment badges + consolidated action row, two new actions: Health
Check relocated into it, Schedule Maintenance genuinely new) / Overview /
Software (renamed from Lineage) / Technical (renamed+expanded from Live
Health Check) / **Support** (new — up to 5 recent tickets) /
**Maintenance** (new — Active/Upcoming/History) / Approvals (relabeled
from Action Executions) / Timeline — labeling-and-addition, not a full
physical doc-order reorder (see ADR-011's explicit tradeoff writeup).

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

**Phase 2 — Correlation IDs (formal, cross-system).** Resolved for the
ticket ↔ GitHub case by Phase 6's entity-reference timeline aggregation
(see that phase's entry above and ADR-006 decision 4) rather than a
shared `correlation_id` scheme — `OperationalEvent.causation_id` remains
unpopulated and is not expected to be the mechanism a future cross-system
chain uses; follow Phase 6's aggregation pattern instead if another
"stitch two independently-caused chains together" need comes up.

Every other numbered phase (1, 3-20) is landed as of this tracker's
last update (Phase 19) — see "Completed phases" above. Phase 2 above is
the only remaining unaddressed phase reference in the whole doc, and
it's resolved-by-substitution, not actually pending work.

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
