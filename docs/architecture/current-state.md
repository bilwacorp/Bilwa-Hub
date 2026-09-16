# Current-State Architecture Audit

Written as Phase 0 of `HUB-Expansion.md` — before any expansion code lands.
Everything below was verified against the actual repository on 2026-09-16
(migration files, model classes, router bodies, frontend source), not just
inferred from `CLAUDE.md`. Where this document adds detail `CLAUDE.md`
doesn't have, treat this file as the more precise one; where they overlap,
`CLAUDE.md` remains the canonical narrative for *why* things are shaped
this way.

## 1. What exists today (module inventory)

**Backend (`backend/app/`)**
- `models.py` — `User`, `Role`, `Permission`, `Deployment`, `DeploymentStaffAssignment`,
  `DeploymentSnapshot`, `SupportTicket`, `MaintenanceWindow`, `NotificationLog`, plus
  their enum types. Single file, no per-domain split.
- `core/` — config, JWT/cookie auth deps, Casbin enforcer + watcher + model conf,
  permission catalog (`permissions.py`), `deployment_scope.py` (row-level visibility),
  `maintenance_scheduler.py` + `expiry_reminder_scheduler.py` (two independent asyncio
  background loops started from `main.py`'s lifespan — **not Celery beat**, just
  `asyncio.create_task` loops inside the FastAPI process).
- `services/` — `crypto.py` (Fernet), `deployment_client.py` (outbound httpx to client
  deployments), `rbac.py`, `notifications/` (Celery-backed email+WhatsApp),
  `notification_triggers.py`.
- `workflow/`, `rules/`, `approvals/` — the ported SpiffWorkflow BPMN engine, the
  sandboxed rule evaluator, and the thin gate `deployments.py` calls into.
- `api/routers/` — `auth`, `register`, `ingest`, `deployments`, `tickets`,
  `maintenance`, `users`, `notifications`, `rbac`, plus `workflow/api.py`,
  `rules/api.py`, `approvals/api.py` mounted from `main.py`.

**Frontend (`frontend/src/`)** — pages for auth, deployments (list/detail),
tickets, maintenance, staff users, notifications, rbac (roles/permissions), and
workflows (my-approvals, workflow list/detail/designer, approval rules). One
`Shell` in `App.tsx` with permission-gated nav (`useAuthStore().can()`).

**Migrations (`backend/alembic/versions/`, in order)**
```
001_initial_schema        005_staff_roles_and_fleet_permission   009_password_reset
002_seed_admin             006_notifications                     010_rbac_catalog
003_maintenance_scheduling 007_deployment_staff_assignments       011_granular_permissions
004_maintenance_mode       008_expiry_reminder                    012_workflow_engine
```
Twelve migrations, all forward-only (no down-revision surprises checked here, but
worth a read before adding a 013 that touches enum types — see §7).

**Casbin permission catalog (`core/permissions.py`)** — 45 `(resource, action)`
pairs across 9 resources: `deployments` (9: view/view_all/create/renew/suspend/
change_plan/extend_expiry/check_health/review_request/assign_staff — actually 10,
see file for the exact list), `tickets` (3), `maintenance` (5), `notifications` (4),
`staff` (4), `rbac` (2), `workflows` (6), `workflow_rules` (5), `workflow_instances`
(2), `approvals` (3). `Role`/`Permission` DB rows are metadata only — `casbin_rule`
(`p`/`g` rows) is enforcement's actual source of truth.

**Existing audit/history mechanisms** — there is **no generic audit log today**.
What exists instead, per-domain:
- `WorkflowHistory` (`workflow/models.py`) — one row per workflow-instance state
  transition (task started/completed/approved/rejected/etc.), scoped to a single
  `WorkflowInstance`. This is the closest thing to an event log in the codebase,
  but it only covers approval-gated actions, not the ungated majority (e.g. a ticket
  status update, a maintenance window edit, a staff role change — none of these
  leave any row anywhere except the mutated row itself).
- `NotificationLog` — one row per notification attempt (channel, status, payload,
  error), not a general action log.
- `DeploymentSnapshot` — one row per heartbeat, an implicit time-series of
  subscription/usage state, but not of *actions taken*.
- Nothing records who ran `deployments.py`'s `action_renew`/`action_suspend`/
  `action_change_plan`/`action_extend_expiry`/`action_check_health`, what the
  previous state was, or what the deployment's raw response was — only the
  `DeploymentCallError` path is even logged (`logger.warning`, not persisted).
- No IP address is captured anywhere.

**Existing tests** — there are **none**. `find backend -iname '*test*'` and the
frontend equivalent, both excluding `.venv`/`node_modules`, return zero files.
No `pytest` test modules, no frontend test runner configured. Phase 20's testing
requirements (permission tests, row-level scope tests, idempotency tests, webhook
auth tests, E2E) start from a blank slate, not an existing suite to extend.

## 2. Data flow (registration → heartbeat → action)

1. Staff creates a pending `Deployment` (`POST /deployments`) → row with
   `status=pending`, a hashed single-use `registration_token`.
2. Client deployment's own Celery-beat task calls `POST /register` with that
   token + a self-generated `action_key`. Hub hashes+stores an `api_key` it
   mints, Fernet-encrypts and stores the client's `action_key`, flips
   `status=active`.
3. Every 2h, client calls `POST /ingest/heartbeat` (bearer = raw `api_key`,
   hash-compared against `api_key_hash`) → new `DeploymentSnapshot` row +
   `notification_triggers.py` diffs `pending_requests` against the prior
   snapshot to fire "new request raised" alerts.
4. Staff clicks an action button on `DeploymentDetailPage` → `deployments.py`'s
   `action_*` route → `approvals/deployment_hooks.request_or_execute` → either
   `deployment_client.<verb>()` runs immediately (decrypts `action_key`, calls
   the client's `/api/v1/hub/subscription/*`), or, if a published workflow
   exists for that key, starts an approval instance instead and returns early.

## 3. Authentication flow

Two independent auth mechanisms, deliberately not unified:
- **Staff (browser)**: JWT in an `httpOnly` cookie (`SameSite=Lax; Path=/api`),
  `core/deps.py`'s `_BearerOrCookie` + `get_current_user`, `token_version`-based
  revocation (bumped on password reset/deactivation, checked every request
  alongside a live `is_active` check — a deactivated user is rejected
  immediately, not just once the JWT expires).
- **Deployment → hub (machine)**: `register.py` uses a single-use
  `registration_token` (hash-compared, marked consumed); `ingest.py` uses a
  long-lived `api_key` (hash-compared). Neither goes through Casbin — both are
  explicitly carved out in `permissions.py`'s own comments.
- **Hub → deployment (machine, outbound)**: `deployment_client.py` decrypts
  `action_key_encrypted` and sends it as `X-Hub-Api-Key`. This is the one
  direction where the hub must *present*, not just verify, a secret — hence
  the Fernet round-trip instead of a hash.

## 4. Authorization flow

Casbin RBAC, domain `"default"`, one role per user (`g` grouping row, replaced
wholesale on change — no multi-role stacking). Every business route depends on
`require_permission(resource, action)` individually — there is no
router-level blanket dependency, so adding a new action to an existing router
means adding a new permission constant + a new `Depends(...)`, not editing a
shared list.

Row-level visibility is layered independently of Casbin: `deployments.py`,
`tickets.py`, `maintenance.py` each have a `_get_visible_or_404` that checks
`services/deployment_scope.assigned_deployment_ids(db, user_id)` unless the
caller holds `<resource>.view_all`. A caller who knows a `deployment_id`
outside their assigned set gets a 404 from *every* route taking that id, not
just list/detail — this pattern (404, not 403, to avoid confirming existence)
is the one Phase 20's "unauthorized access using another deployment's IDs"
tests should assert against, and any new resource (releases, GitHub refs,
lineage) that hangs off `deployment_id` needs the same helper reused, not
reinvented.

## 5. Deployment communication (hub ↔ client)

`services/deployment_client.py` is the only place the hub makes outbound calls
to a client deployment. Notable as-is behavior relevant to Phase 15 (Security)
and Phase 5 (webhooks):
- **No URL/SSRF hardening at all.** `_call()` builds `f"{deployment.base_url}/api/v1/hub{path}"`
  and calls `httpx.AsyncClient(timeout=15.0).request(...)` directly — no
  scheme allowlist, no private-IP/loopback/link-local blocking, no response
  size cap, no explicit `follow_redirects` (httpx defaults this to `False`,
  which happens to be safe, but it's implicit, not asserted). `base_url` is
  staff-entered at deployment-creation time, not attacker-controlled through
  any current inbound path — but Phase 3/5's webhook ingestion will introduce
  the first *externally-triggered* trigger for hub-initiated outbound calls,
  which is exactly when this gap becomes exploitable and needs closing first.
- `check_health()` is a live on-demand double GET (`/api/health`,
  `/api/health/db`), separate from the passive heartbeat-derived status shown
  on the list page; it swallows all `httpx.HTTPError` into `False` fields,
  never raising except for a missing `base_url`.
- Every write action (`renew`/`suspend`/`change_plan`/`extend_expiry`/
  `review_request`/`push_maintenance`/`push_ticket_status`) funnels through
  the same `_call()`, which raises `DeploymentCallError` (never a raw httpx
  exception) on both transport failure and any `>=400` response — callers
  can't currently distinguish "unreachable" from "deployment rejected the
  request," which matters for Phase 12's retry/failure taxonomy.
- **No idempotency key is sent on any of these calls today.** A retried
  `renew` call has no way for the client deployment to detect it's a repeat.

## 6. Workflow/approval execution

`approvals/deployment_hooks.py` is the single integration seam: `_is_gated()`
checks for an active `WorkflowDefinition` with a published version for one of
three hardcoded keys (`deployment_renew`/`deployment_suspend`/
`deployment_change_plan`). If gated, `integration.start_approval` creates a
`WorkflowInstance` and the route returns `{"approval_required": true, ...}`
instead of running anything. On instance completion, a registered hook
(`_on_renew_complete` etc.) reads back `WorkflowVariable` rows and calls
`deployment_client` directly.

**This is exactly the gap Phase 12 describes, confirmed in code**
(`deployment_hooks.py:82-87` etc.): the completion hook catches
`DeploymentCallError` and does `logger.warning(...)` — nothing is persisted.
An approved-but-unreachable renewal is `WorkflowInstance.status == completed`
forever, indistinguishable from a renewal that actually reached the client,
except by reading application logs. There is no `EXECUTION_PENDING` /
`EXECUTING` / `EXECUTED` / `EXECUTION_FAILED` state anywhere, no retry, no
attempt count. This is the highest-value, most concretely-scoped item in the
whole expansion doc — Phase 12/13 should probably be sequenced early, not at
position 12, since Phase 8 (more workflows gating more actions) directly
multiplies the blast radius of this gap.

Gateway/condition expressions run through `rule_engine` (`workflow/engine.py`'s
`RuleScriptEngine`), a sandboxed grammar with no eval/exec path — BPMN
`scriptTask`/`preScript`/`postScript` is rejected outright at validation time
(`workflow/parser.py`). This is a deliberate, already-correct security
boundary — nothing in the expansion plan should touch it.

## 7. Notification flow

`NotificationService.send_*` (in `services/notifications/`) creates a
`NotificationLog` row synchronously, then enqueues a Celery task on the
`celery-worker` container (broker = Redis, `CELERY_BROKER_URL`) that does the
actual SMTP/WhatsApp-gateway send and updates the log row's status. Three
triggers wire in today: ticket-raised, renewal/upgrade-request-raised (both
event-driven from `ingest.py`), and expiry-about-to-happen (poll-driven from
`core/expiry_reminder_scheduler.py`, an **asyncio loop inside the FastAPI
process**, not a Celery beat schedule — worth knowing before assuming
"anything periodic already goes through Celery," it doesn't). Recipients are
resolved by `recipients.recipients_for_deployment()`: a deployment's assigned
staff if any, else every `deployments.view_all` holder.

## 8. Known technical debt / confirmed bugs

**The 422-toast crash (flagged explicitly in `HUB-Expansion.md`), root-caused:**
Eleven frontend files declare a local
```ts
function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}
```
and pass its result straight to `toast.error(...)`. The `{ detail?: string }`
cast is a lie at runtime: FastAPI's 422 response body has `detail` as a
**list** of `{loc, msg, type, ...}` objects, not a string. When that shape
flows into `toast.error(arrayOfObjects)`, react-hot-toast renders it as a
child and React throws "Objects are not valid as a React child" — a live
crash, not just an ugly message. The affected files (confirmed by grep, not
estimated):
```
auth/LoginPage.tsx                 workflows/ApprovalRulesPage.tsx
auth/ResetPasswordPage.tsx         workflows/MyApprovalsPage.tsx
notifications/NotificationsPage.tsx workflows/WorkflowDesignerPage.tsx
rbac/RolePermissionsPage.tsx       workflows/WorkflowDetailPage.tsx
rbac/RolesPage.tsx                 workflows/WorkflowListPage.tsx
users/StaffUsersPage.tsx
```
**The fix already exists once in the codebase** —
`deployments/DeploymentDetailPage.tsx`'s `errMsg()` correctly narrows
`detail: unknown`, checks `typeof detail === 'string'` first, and falls back
to `detail[0].msg` when it's the Pydantic-error-list shape. `DeploymentsListPage.tsx`,
`MaintenanceWindowsPage.tsx`, and `SupportTicketsPage.tsx` use `toast` but
don't have this specific `errorDetail` helper — worth re-checking their own
error paths individually rather than assuming they're safe. Per
`HUB-Expansion.md`'s own instruction, the correct fix is to extract
`errMsg`/`errorDetail` into one shared `lib/` helper (matching
`DeploymentDetailPage.tsx`'s already-correct logic) and have all eleven
files import it, rather than patching each file's local copy independently.

**Other observations, not necessarily bugs but relevant to later phases:**
- `main.py` already has a `request_id_middleware` producing `X-Request-ID` per
  request and stashing it on `request.state` — this is a ready-made anchor for
  Phase 2's correlation IDs; the expansion shouldn't invent a second mechanism,
  it should decide whether `OperationalEvent.correlation_id` reuses this value
  or intentionally diverges (a correlation chain outlives one HTTP request,
  e.g. ticket → PR → release → deployment, so it likely needs its own
  generator — but the *first* event in a chain could seed from this).
- Swagger (`/api/docs`) is enabled, contrary to `CLAUDE.md`'s "no check done
  yet" note — worth updating that file separately, not in scope here.
- Two separate in-process asyncio background loops (`maintenance_scheduler`,
  `expiry_reminder_scheduler`) exist alongside Celery. Any new periodic job
  (e.g. Phase 5's webhook health polling, Phase 16's heartbeat-overdue sweep)
  should pick one pattern deliberately rather than adding a third mechanism —
  Celery beat is the better fit for anything that must survive a backend
  container restart mid-cycle, which these two asyncio loops do not.
- `Deployment.status = suspended` is explicitly documented as "not currently
  enforced by any gate" (`models.py`) — i.e., suspending a deployment on the
  hub doesn't yet block anything hub-side; it's advisory metadata today.

## 9. Integration opportunities (for the phases ahead)

- Phase 1's `OperationalEvent` table has one clear non-obvious integration
  point already in the codebase: `hooks.register_completion_hook` in
  `approvals/hooks.py` is a one-directional registry business modules already
  use to react to workflow completion without the workflow engine importing
  them back. The same one-directional-registry shape (not a circular import)
  should probably be reused for "emit an `OperationalEvent` on every
  significant state transition" rather than sprinkling `db.add(OperationalEvent(...))`
  calls ad hoc through every router.
- `WorkflowHistory` rows and the future `OperationalEvent` table will overlap
  in purpose for gated actions. Phase 1 should decide explicitly whether
  `OperationalEvent` rows are *also* written for workflow-covered actions
  (redundant with `WorkflowHistory` but consistent for the unified timeline in
  Phase 9), or whether Phase 9's timeline reads from both tables and merges
  them. The doc's "do not force every object into one table" principle
  suggests the latter, but it means the timeline query is a UNION across at
  least `OperationalEvent`, `WorkflowHistory`, `DeploymentSnapshot`, and
  eventually `GitHubPullRequestReference`/`GitHubReleaseReference` — worth
  designing that read path in Phase 1, not deferring it to Phase 9.
- `request_or_execute`'s `immediate` callback pattern in `deployment_hooks.py`
  is a good template for Phase 13's idempotency work: it's already the single
  choke point for renew/suspend/change_plan, so an idempotency-key parameter
  threaded through this one function (rather than through every caller)
  covers all three actions at once.

## 10. Risks / what NOT to disturb

- The two-credential design (`api_key` hash-only, `action_key` Fernet-
  encrypted) is load-bearing and explicitly protected in `CLAUDE.md`. Nothing
  in this expansion should collapse it into one shared secret even under
  pressure to simplify for GitHub/CI credentials — those are a *third*,
  separate credential class (Phase 3/5) and should get their own encrypted-at-
  rest column(s), not reuse `action_key`'s.
- Native Postgres enum types (`DO $$ ... CREATE TYPE ... EXCEPTION$$`,
  matching migration 001's convention) back every status column including
  the workflow engine's. Any new status/type column (event_type, actor_type,
  GitHub PR state, maintenance severity) must follow the same convention or
  the ORM's `Enum(...)` mapping breaks — do not switch to plain `String` for
  "flexibility."
- `is_system=True` freezes a workflow/rule's *structure* once published, but
  not its `is_active` toggle — any new gated action (Phase 8's "production
  maintenance," "high-risk deployment," etc.) should follow the exact same
  seeded-definition-as-on/off-switch pattern documented in `CLAUDE.md`, not
  introduce a parallel settings toggle (there is deliberately no
  `AppSetting` table).
- Zero test coverage today means every phase's "add tests" step is additive
  from scratch, not a regression-guarded extension — the risk of a silent
  regression while adding Phase 3+ features is real and higher than the doc's
  phrasing ("preserve existing behavior") might suggest at a glance, since
  there's no CI to catch a break. Phase 20 (or an equivalent lightweight test
  pass) is worth pulling earlier for the routers being touched first
  (deployments.py, approvals/deployment_hooks.py) rather than left last.
