# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## What this project is

BilwaCorp Fleet Hub — a centralized service every PoultryOS-CBP client
deployment (each its own single-tenant, separately hosted instance)
registers with on first boot. Every deployment then reports a heartbeat
every 2 hours (subscription status, usage, pending renewal/upgrade
requests), so BilwaCorp staff can see the whole fleet — and act on it
(renew, suspend, change plan, review a request) — from one place instead
of logging into each deployment separately. Also collects support tickets
relayed from each deployment and tracks maintenance windows across the
fleet.

This is **Phase 1**: registration, heartbeat, inbound actions, support
tickets, maintenance windows. No per-device sessions. See `README.md`'s
"What's deliberately NOT in Phase 1" and the original design plan quoted
there for the full rationale.

Staff user management + two roles (`admin`, `engineer`) landed after
Phase 1 as a small follow-on. A dynamic permission catalog + Roles &
Permissions UI landed after that (`api/routers/rbac.py`,
`frontend/src/pages/rbac/`) — an admin can now create custom roles and
pick exactly which of the six catalog permissions each one holds, not
just toggle between the two seeded roles. See "The permission model"
below.

Email + WhatsApp staff notifications (a support ticket raised, a
renewal/upgrade request raised) landed after that — see
`services/notifications/` below. Unlike the Phase-1 plan's original "no
notification emails from the hub itself" scoping, this is now a real,
Celery-backed subsystem (`celery-worker`/`redis` in `docker-compose.yml`).

An embedded BPMN workflow + approval engine (`app/workflow/`, `app/rules/`,
`app/approvals/`) landed after that, ported from PoultryPro-CBF's own
engine of the same name — see "The workflow & approval engine" below. It
gates `deployments.renew`/`.suspend`/`.change_plan` behind an admin-
configurable approval step instead of running them immediately.

**Connecting a new client deployment to this hub is a full runbook of its
own — see `docs/CONNECTING_A_DEPLOYMENT.md` before doing this for real.**
Short version: create a pending `Deployment` + single-use
`registration_token` on the hub, set `HUB_URL`/`HUB_REGISTRATION_TOKEN` on
that client's `celery-worker` service only, redeploy — the client
auto-registers and starts heartbeating with no further manual step.

---

## Commands

### Backend
```bash
cd backend
cp .env.example .env   # DATABASE_URL, SECRET_KEY, HUB_ENCRYPTION_KEY (required, see below)
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend
```bash
cd frontend
cp .env.example .env   # VITE_API_BASE_URL
npm install
npm run dev      # dev server
npm run build    # tsc + vite build
```

### Whole stack (Docker)
`docker-compose.yml` (`redis` + `backend` + `celery-worker` + `frontend`, DB
external) is the Dokploy deploy unit — see README's "Deploying (Dokploy)"
for env vars and network setup. `frontend`'s nginx serves the SPA and
proxies `/api` to the backend via the `hub-api-internal` alias on a private
network, so it's one origin (the auth cookie is `SameSite=Lax; Path=/api`
— cross-origin would break it) and `backend` never needs its own public
domain. `backend` is also on `dokploy-network` to reach the managed
Postgres, with an entrypoint route-fix (`backend/docker-entrypoint.sh`,
ported from PoultryOS-CBP) so its outbound calls to client deployments
aren't blackholed — same dual-network gotcha this host has. `celery-worker`
(same image, same entrypoint/network setup as `backend` since it makes its
own outbound SMTP/WhatsApp-gateway calls) renders and sends every
`services/notifications/` alert; `redis` is its broker, internal to
`hub_internal` only.

URLs: Backend `http://localhost:8000/api/v1` (no `/docs` Swagger check done
yet — verify it's enabled the same way PoultryOS-CBP's is if you need it).
Frontend `http://localhost:5174` (dev) / port `80` on the `frontend`
container (compose).

`HUB_ENCRYPTION_KEY` is **required**, not optional — it's the Fernet key
the hub uses to decrypt a deployment's `action_key` when calling back in to
run an action (`services/crypto.py`). Generate one with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Seeded login
`alembic/versions/002_seed_admin.py` creates one staff account:
`admin` / `ChangeMe@2026`. Additional staff (either role) are created from
the "Staff" page (admin-only) once logged in — see "The permission model"
above. Still no *self*-service password-change endpoint — an admin resets
another user's password via that page's "Reset password" action; to
change the seeded admin's own password before a second admin exists, edit
`users.hashed_password` (bcrypt) directly, or re-seed against a fresh DB
with a different constant.

---

## Architecture

### Backend — `backend/app/`

```
main.py              FastAPI app, CORS, lifespan (Casbin enforcer init + watcher)
models.py            User (staff), Deployment, DeploymentSnapshot, SupportTicket,
                      MaintenanceWindow — single file, mirrors PoultryOS-CBP's
                      models/models.py convention
schemas.py           All Pydantic request/response schemas, one file
core/config.py        Settings — DATABASE_URL, SECRET_KEY, HUB_ENCRYPTION_KEY (required),
                      REDIS_URL, FRONTEND_URL, BACKEND_CORS_ORIGINS
core/deps.py          Ported from PoultryOS-CBP: _BearerOrCookie + get_current_user
                      (token_version-based revocation). Deliberately dropped: UserSession/
                      "sid" per-device tracking, mobile-vs-web client split — Phase 1 has
                      exactly one staff login, web-only.
core/permissions.py   ALL_PERMISSIONS — the six-permission catalog (staff/deployments/tickets/
                      maintenance/notifications/rbac, all '.manage') — see "The permission
                      model" below. require_permission()'s shape is ported from
                      PoultryOS-CBP. register.py/ingest.py are NOT Casbin-gated at all — they're
                      machine-to-machine, shared-secret authenticated instead.
core/casbin_enforcer.py, core/casbin_watcher.py, core/rbac_model.conf
                      Ported near-verbatim from PoultryOS-CBP — generic, no app-specific
                      content in rbac_model.conf.
services/rbac.py      Role assignment for staff users (get_role/set_role) plus the RBAC
                      catalog/role CRUD backing api/routers/rbac.py (list_roles,
                      get/set_role_permissions, rename/delete-role policy rewrites,
                      count_users_with_permission, would_orphan_permission) — all on top of
                      the Casbin enforcer's domain-aware RBAC API. A user's role lives only
                      as a Casbin `g` grouping row, not a column on User.
services/deployment_scope.py
                      assigned_deployment_ids(db, user_id) — the one query every row-level
                      visibility check (deployments.py/tickets.py/maintenance.py) shares.
                      See "The permission model" above.
services/crypto.py    Fernet encrypt/decrypt for Deployment.action_key_encrypted — mirrors
                      PoultryOS-CBP's User.totp_secret_encrypted pattern (reversible storage
                      is the exception, not the rule; see "Two credentials" below).
services/deployment_client.py
                      httpx wrapper for hub -> deployment calls (renew/suspend/change-plan/
                      extend-expiry/review-request). Decrypts the target deployment's
                      action_key, calls its /api/v1/hub/subscription/* endpoints — see that
                      repo's api/v1/routers/hub_integration.py for the ground-truth shapes
                      this mirrors. Catches transport-level failures (DNS, connection
                      refused) as DeploymentCallError, not a raw httpx exception, so callers
                      get a clean 502 instead of an opaque 500.
services/notifications/ Email (SMTP) + WhatsApp (generic HTTP gateway) staff alerts, ported
                      from PoultryPro-CBF's package of the same name — see its own README.md
                      for full architecture/setup. NotificationService.send_* creates a
                      NotificationLog row and enqueues a Celery task (celery-worker container)
                      that does the actual render+send; nothing sends inline on the request
                      thread. No DB-editable templates here (unlike PoultryPro-CBF) —
                      templates/ + templates_whatsapp/ are file-based only.
services/notification_triggers.py
                      Where three fleet events are wired in: a support ticket raised
                      (ingest.py's ingest_support_ticket), a subscription renewal/upgrade
                      request raised (ingest.py's ingest_heartbeat — pending_requests arrives
                      as the deployment's full current list every heartbeat, so this diffs
                      against the prior snapshot to find genuinely new requests), and a
                      subscription about to expire (core/expiry_reminder_scheduler.py's
                      hourly poll loop — not an inbound event, so the hub has to check for
                      it itself; idempotent per exact expiry_date via
                      Deployment.expiry_reminder_sent_for). All three fan out via
                      recipients.recipients_for_deployment() — a deployment's explicitly
                      assigned staff (DeploymentStaffAssignment) if any, else every active
                      deployments.view_all holder (recipients.fleet_staff() — only they can
                      actually see an unassigned deployment at all, see "The permission
                      model") — to whichever of email/WhatsApp each recipient has on file
                      (User.email / User.phone).
api/routers/
  auth.py             login/logout/refresh/me — MFA, phone/WhatsApp OTP, and mobile
                      refresh-token pairing all dropped (ported subset only). Self-service
                      forgot-password/reset-password (public, unauthenticated) landed later —
                      hash-only reset token (users.password_reset_token_hash, 30min TTL,
                      single-use), always 204 regardless of whether the username/email
                      exists so the endpoint can't be used to enumerate staff accounts.
                      Emails via services/notifications (TEMPLATE_PASSWORD_RESET) — the
                      reset_url context key is in SENSITIVE_CONTEXT_KEYS so the live token
                      never lands in NotificationLog.payload.
  register.py         POST /register — public, single-use registration_token auth, not JWT
  ingest.py            POST /ingest/heartbeat, POST /ingest/support-ticket — api_key bearer
                      auth (hash-compared against Deployment.api_key_hash)
  deployments.py        One permission per action (deployments.view/view_all/create/renew/
                      suspend/change_plan/extend_expiry/check_health/review_request/
                      assign_staff): create pending deployment + token, list, detail
                      (+ latest snapshot), reissue-token, the inbound-action triggers, and
                      GET staff-options / PUT {id}/staff (narrows that deployment's
                      notification fan-out, see services/notifications/recipients.py).
                      staff-options is registered ahead of GET /{deployment_id} so the
                      literal path segment isn't swallowed by the dynamic one. Every route
                      taking a deployment_id 404s via _get_visible_or_404 if the caller
                      isn't assigned to it and lacks deployments.view_all — see "The
                      permission model" above.
  tickets.py            tickets.view/view_all/update_status — same row-level visibility
                      scoping as deployments.py (a ticket's deployment_id must be in the
                      caller's assigned set, or they hold tickets.view_all).
  maintenance.py        maintenance.view/view_all/create/update/delete — same scoping,
                      except fleet-wide windows (deployment_id IS NULL) are never scoped.
  users.py              staff.view/create/update/reset_password: staff account CRUD
                      (deactivate, not hard delete — MaintenanceWindow.created_by FKs to
                      users.id) + role assignment. Guards against self-lockout (can't
                      deactivate/change your own role) and against dropping the last
                      active staff.update holder.
  notifications.py      notifications.view/resend/delete/test_send: notification history
                      — see services/notifications/README.md. No row-level scoping (a
                      notification log isn't tied to one deployment the same way).
  rbac.py               rbac.view (read-only endpoints) / rbac.manage (role CRUD, set a
                      role's permissions) — both admin-only at cutover. See "The
                      permission model" above.
workflow/              BPMN process shape — WorkflowDefinition/WorkflowVersion (design-time,
                      services.py) + WorkflowInstance/WorkflowTask/WorkflowHistory/
                      WorkflowVariable (runtime, executor.py) + the SpiffWorkflow adapter
                      (engine.py) and BPMN parser/validator (parser.py). api.py is
                      `/workflows` (definition/version CRUD) + `/workflow-instances`
                      (read-only + cancel — see "The workflow & approval engine" below for
                      why there's no direct "start an instance" endpoint).
rules/                 The routing config that decides who approves each step —
                      ApprovalRule/RuleCondition/RuleAction, a sandboxed expression
                      evaluator (evaluator.py, shared with BPMN gateway conditions via
                      workflow/engine.py's RuleScriptEngine), and resolvers.py (the two
                      approver-resolution strategies: casbin_role, explicit_users).
                      api.py is `/workflow-rules`.
approvals/              The thin glue a business router uses to gate an action:
                      integration.start_approval (called from api/routers/deployments.py's
                      action_renew/suspend/change_plan), hooks.py's completion-hook
                      registry (one-directional — approvals/workflow never imports a
                      business module), and deployment_hooks.py, the one place
                      deployments.py's gated routes actually touch this subsystem — see
                      "The workflow & approval engine" below. api.py is `/approvals`
                      (the day-to-day approve/reject/reassign inbox surface).
```

### Frontend — `frontend/src/`

Design system (`tailwind.config.js`, `src/index.css`, `public/*.png`) is
ported from PoultryPro-CBF's frontend — same color/fontSize/spacing/
borderRadius/boxShadow tokens, same Inter font, and the real BilwaCorp
brand assets (`favicon.png`, `apple-touch-icon.png`, `logo-dark.png` —
light-surface wordmark, `logo-light.png` — dark-surface/inverted, for any
future dark-surface use) copied over from PoultryPro-CBF's `public/`
(`logo-sq-alpha-dark.png` / `logo-sq-alpha-trimmed.png` there). The
Shell/sidebar in `App.tsx` (240px fixed sidebar, left-accent-bar nav
treatment, avatar+role+logout footer) is a simplified version of that
repo's `components/layout/Sidebar.tsx` — no search/refresh/status-popover/
docs-link/mobile-bottom-sheet, this hub doesn't have those features; the
mobile equivalent here is a plain slide-in drawer instead.

```
lib/api.ts, lib/utils.ts     axios instance + cn()/formatDate() etc., mirrors PoultryOS-CBP
components/ui/                Button, Input, Select, Modal, Badge, DataTable, Tabs, Card,
                              PasswordInput — Button/Input/Select/Modal/Badge/Tabs/DataTable
                              copied verbatim from PoultryOS-CBP's frontend/src/components/ui/
                              (generic, no product-specific logic); Card ported from
                              PoultryPro-CBF's equivalent; PasswordInput is hub-specific
pages/auth/LoginPage.tsx
pages/deployments/DeploymentsListPage.tsx    client name, status, plan, expiry, last-
                                              heartbeat health indicator
pages/deployments/DeploymentDetailPage.tsx   latest snapshot (including pending_requests
                                              from the heartbeat payload directly — no
                                              separate live round-trip in Phase 1), renew/
                                              suspend/change-plan/extend-expiry action
                                              buttons, request-review actions, Assigned Staff
                                              checklist (narrows this deployment's notification
                                              recipients — see CLAUDE.md's deployments.py entry)
pages/tickets/SupportTicketsPage.tsx         fleet-wide ticket table + status update
pages/maintenance/MaintenanceWindowsPage.tsx list + create/edit, no automation
pages/users/StaffUsersPage.tsx               staff.manage: staff table (role/active inline
                                              editors, reset-password) — role select is
                                              populated from GET /rbac/roles, not hardcoded
pages/notifications/NotificationsPage.tsx    notification history table (filter by status/
                                              channel/recipient), resend/delete, test-email/
                                              test-whatsapp buttons
pages/rbac/RolesPage.tsx                     rbac.manage: role list (name/description/
                                              built-in-vs-custom Badge), create/delete —
                                              is_system roles get no delete button
pages/rbac/RolePermissionsPage.tsx           rbac.manage: one checkbox per catalog permission
                                              for a given role, dirty-tracked Save/Reset
pages/workflows/MyApprovalsPage.tsx          approvals.view/.act: the day-to-day inbox —
                                              pending tasks assigned/candidate to you (or,
                                              with approvals.act_any, every pending task),
                                              approve/reject with an optional comment
pages/workflows/WorkflowListPage.tsx         workflows.view/.create: definition list, the
                                              is_active toggle (this IS the on/off switch for
                                              a gated action — see "The workflow & approval
                                              engine" below)
pages/workflows/WorkflowDetailPage.tsx       workflows.view: one definition's versions +
                                              which approval rules target it
pages/workflows/WorkflowDesignerPage.tsx     workflows.update/.publish: the bpmn-js visual
                                              editor for one draft version — see
                                              designer/BpmnDesigner.tsx
pages/workflows/designer/                    BpmnDesigner.tsx (bpmn-js Modeler + properties
                                              panel wrapper), ApprovalPropertiesProvider.tsx
                                              (custom "Approval routing" properties group:
                                              stepKey/ruleKey/casbinResource/casbinAction/
                                              dueInHours — written in plain function calls,
                                              no JSX, since the panel renders with Preact not
                                              React), spiffProperties.ts (read/write helpers
                                              for the <spiffworkflow:properties> extension
                                              element), spiffworkflowModdle.json (the moddle
                                              extension schema), blankBpmn.ts (starter
                                              diagram for a new version) — all ported
                                              near-verbatim from PoultryPro-CBF's own
                                              designer/ of the same name, which is generic
                                              (no branch/module concept in any of these files)
pages/workflows/ApprovalRulesPage.tsx        workflow_rules.view/.create/.update/.delete:
                                              rule CRUD — conditions (rule-engine
                                              expressions) + one action (assign_approver
                                              w/ casbin_role or explicit_users strategy,
                                              auto_approve, skip_step, or set_variable)

App.tsx's Shell nav items and route guards (RequirePermission) are gated by
useAuthStore().can('resource.action') — fed by /auth/me's `permissions`
field — not by a hardcoded role name (see "The permission model" above).
```

### The permission model (roles, permission catalog, row-level visibility)

45 Casbin permissions, all defined in `core/permissions.py`'s
`ALL_PERMISSIONS` — one per *action*, not one per router. E.g.
`deployments.renew`, `deployments.suspend`, `deployments.check_health`,
`deployments.assign_staff` are four separate permissions a role can hold
independently, gated per-route (`Depends(require_permission(*PERM))` on
each route function, not one blanket `APIRouter(dependencies=[...])`).
Full resource list: `deployments` (10 actions incl. `view`/`view_all`),
`tickets` (3), `maintenance` (5), `notifications` (4), `staff` (4), `rbac`
(2 — `view`/`manage`), `workflows` (6), `workflow_rules` (5),
`workflow_instances` (2), `approvals` (4 — see "The workflow & approval
engine" below for what each of these seventeen gates).

- **`Role`** (`app/models.py`) — dynamic, admin-creatable role *metadata*
  (name, description, `is_system`). `is_system=True` on the two seeded
  roles (`admin`, `engineer`) blocks renaming/deleting them via the
  Roles & Permissions UI (`api/routers/rbac.py`, `frontend/src/pages/rbac/`)
  — their *permissions* can still be edited, just not their name.
- **`Permission`** (`app/models.py`) — the fixed, migration-seeded catalog
  the UI's checkbox grid renders against (grouped by resource —
  `RolePermissionsPage.tsx`). New permissions are added by a migration
  (mirroring `ALL_PERMISSIONS`), never invented at runtime.
- **`casbin_rule`** stays the actual enforcement source of truth — `Role`/
  `Permission` are metadata layered on top, kept in sync by
  `services/rbac.py` (never edited directly). A user's role is still a
  Casbin `g` grouping row, not a column on `User` — one role per user,
  replaced wholesale on change, not stacked (this hub didn't adopt
  multi-role-per-user when it added the catalog, unlike PoultryPro-CBF's
  equivalent system).

**History**: two hardcoded permissions/roles → `010_rbac_catalog.py` split
into one coarse permission per resource (behavior-preserving: nobody's
access changed) → `011_granular_permissions.py` split each resource's one
`.manage` into one permission per action, **and was NOT
behavior-preserving for `engineer` by design** (see below).

**Row-level visibility** (deployments / tickets / maintenance): a user
assigned to specific deployments (`DeploymentStaffAssignment`, see
`api/routers/deployments.py`'s `PUT .../staff`) only sees *those*
deployments, plus their tickets and (deployment-specific) maintenance
windows — fleet-wide maintenance windows (`deployment_id IS NULL`) are
never scoped. Holding `<resource>.view_all` bypasses this. As of migration
011, **only `admin` holds any `view_all` permission** — `engineer` keeps
every *action* it had before, but only against deployments explicitly
assigned to it; an engineer with nothing assigned sees an empty fleet.
Enforced server-side (`_get_visible_or_404` in each of those three
routers — a deployment_id the caller isn't scoped to 404s from *every*
route that takes one, not just the list/detail GETs, via
`services/deployment_scope.assigned_deployment_ids`), not just hidden in
the frontend. `services/notifications/recipients.py`'s `fleet_staff()`
(the fallback recipient set for an *unassigned* deployment's alerts) was
updated to match: it's `deployments.view_all` holders only now, not "every
role with any fleet-area permission" — notifying someone who'd 404 trying
to open the link would be pointless.

Since roles are custom now, anything that used to check `role == "admin"`
by name had to become a permission check instead — most notably the
`fleet_staff()` fix above and the frontend's route/nav guards
(`useAuthStore().can()`, fed by `/auth/me`'s `permissions: string[]`
field, `App.tsx`'s `RequirePermission`). Grepping for a literal
`"admin"`/`"engineer"` string anywhere outside a migration or the two seed
rows themselves is a sign something was missed. `RequirePermission`
renders an inline "no access" message rather than redirecting to another
gated route (e.g. `/deployments`) — a role with no view permission
anywhere would otherwise bounce between two failing redirects forever.

`api/routers/users.py` and `api/routers/rbac.py` guard footguns directly
(not via Casbin — Casbin has no concept of "don't let this go to zero"):
an admin can't deactivate or change their own role (must ask another
admin); no edit may drop the last active `staff.update` holder
(`rbac.count_users_with_permission`); no role edit/delete may orphan
`rbac.manage` entirely (`rbac.would_orphan_permission`) — checked by
resource+action, not by hardcoding "the admin role", since a custom role
could also hold either permission. Deactivating/resetting a user's
password bumps `token_version` (`core/deps.get_current_user` already
checks it) — but a deactivated user is rejected immediately regardless,
since `is_active` is re-checked live on every request, not just baked
into the JWT.

### The workflow & approval engine (read before touching `app/workflow/`, `app/rules/`, `app/approvals/`)

Ported from PoultryPro-CBF's own `workflow`/`rules`/`approvals` packages,
which run a real embedded BPMN engine (SpiffWorkflow) plus a sandboxed
expression language (`rule_engine`) for approval routing — not a
lightweight "N-approvals" gate. This was a deliberate, informed choice:
before building it, the scope was researched against CBF's actual
implementation (~5000 lines across backend+frontend) and the user was
explicitly offered a lighter alternative via `AskUserQuestion` and chose
the full engine anyway, knowing the size. Don't "simplify" this back down
to a lightweight gate without that same conversation happening again.

**What's stripped from CBF's version, and why**: this hub is single-org,
not multi-branch, so every `branch_id` column/param/scoping clause in
CBF's tables is gone — an instance's visibility is just "hold the right
permission", not "and only within your branch". The `branch_manager` and
`linked_supervisor` approver-resolution strategies are gone too (no
branch-manager or batch/farm-supervisor concept here) — only
`casbin_role` and `explicit_users` remain (`rules/models.py`'s
`ApproverStrategy`). CBF's hidden `bilwacorp_engineer` role
(`workflows.manage_system`) that can still edit a published `is_system`
workflow/rule has no hub equivalent — here, `is_system=True` simply
freezes a definition/rule's *structure* for *everyone* once it has a
published version (`workflow/services.py`'s `_assert_definition_mutable`,
`rules/services.py`'s `update_rule`); only the `is_active` toggle stays
editable. Push notifications are gone (email only, via
`NotificationService.send_approval` — this hub has no mobile app).
`workflow/executor.py`'s `instance_code` is a random `WF-XXXXXXXX` string
generated inline, not CBF's `core/idempotency.create_with_code_retry`
helper (never ported here) — a collision at 4 random bytes isn't worth a
retry loop at this hub's scale.

**How the gate actually works** (`app/approvals/deployment_hooks.py`):
`deployments.py`'s `action_renew`/`action_suspend`/`action_change_plan`
routes call `deployment_hooks.request_or_execute(...)`, which checks
whether an **active workflow definition with a published version** exists
for the action's key (`deployment_renew`/`deployment_suspend`/
`deployment_change_plan`). If not, it runs exactly like before this
feature existed — calls `deployment_client.renew/suspend/change_plan`
immediately and returns its raw response. If one exists, it starts a
workflow instance instead (via `approvals/integration.start_approval`)
and returns `{"approval_required": true, "instance_id", "status",
"message"}` — the frontend (`DeploymentDetailPage.tsx`'s `actionToast`)
checks for this shape and shows "Approval requested" instead of assuming
the action ran. There is **no separate settings toggle** for "is this
action gated" — this hub has no `AppSetting` table — the seeded
definition's own `is_active` flag (and whether it has a published
version) *is* the switch, editable from the Workflows page with no
migration needed to turn it off.

Once approved, `deployment_hooks._on_renew_complete` (registered via
`approvals/hooks.register_completion_hook` at import time — see
`app/main.py`'s `from app.approvals import deployment_hooks  # noqa`)
fires and makes the real `deployment_client` call, reading back the
`new_expiry_date`/`renewal_amount`/`reason`/`new_plan_id` that were seeded
as `WorkflowVariable` rows when the instance started. A `DeploymentCallError`
here (deployment unreachable) is caught and logged as a warning, not
raised — the *approval* already succeeded and committed; a transport
failure calling the now-approved action back to the deployment is a
separate, later failure mode (currently: logged only, no retry — a stuck
`completed` instance whose deployment call failed is visible via
`GET /workflow-instances/{id}/history`, not yet resurfaced anywhere else).

**Migration `012_workflow_engine.py`** creates all nine workflow/rules
tables (native Postgres enum types for every status/type column, matching
`migration 001`'s `DO $$ ... CREATE TYPE ... EXCEPTION$$` convention — do
NOT switch these to plain `String` columns, the ORM's `Enum(...)` type
expects a matching native type) and seeds three built-in
(`is_system=True`), already-published, one-human-step workflow
definitions — `deployment_renew`/`deployment_suspend`/
`deployment_change_plan` — each with one `casbin_role`-strategy rule
routing to the `admin` role. `admin` is granted every new permission;
`engineer` is granted only `approvals.view`/`approvals.act`/
`workflow_instances.view` (can act on a task if ever routed to them, and
see status — not manage workflow/rule config), mirroring migration 011's
"keep every action permission, not the admin-config ones" pattern.

**BPMN gotchas** (all verified by hand against SpiffWorkflow 3.1.2 — see
`workflow/engine.py` and `workflow/parser.py`'s own docstrings for the
full list): a task's routing metadata (`stepKey`, `ruleKey`,
`casbinResource`, `casbinAction`, `dueInHours`) lives in a custom
`<spiffworkflow:properties>` extension element, not as plain BPMN
attributes — `workflow/parser.py`'s `validate_bpmn` rejects any user/manual
task with no `stepKey` (the executor can't look up a rule without one),
and rejects `scriptTask`/`preScript`/`postScript` outright (arbitrary code
execution from admin-authored BPMN is not acceptable — gateway conditions
instead go through `rule_engine`, a sandboxed grammar with no eval/exec
path, via `engine.py`'s `RuleScriptEngine`). Element ids `Start`/`End` are
reserved by SpiffWorkflow's own implicit root/terminal tasks and silently
drop that element's connections if reused — also checked at validation
time.

Rule resolution never hardcodes an approver: `workflow/executor.py`'s
`advance()` looks up a matching `ApprovalRule` for every new human task via
`rules/services.find_first_matching_rule`, and a step with no matching
rule (or one that resolves to zero live candidates) moves the whole
instance to `error` rather than silently stalling forever — a workflow
stuck with no rule is a configuration bug that should be visible
immediately, not an approval task nobody can ever act on.

### The two-credential design (read before touching auth/registration code)

Both directions of hub<->deployment traffic need a credential, and each is
issued by whichever side only needs to *verify* it, never present it back:

- **`api_key`** — issued by **this hub** at registration. The deployment
  stores it and presents it on every outbound call (heartbeat, ticket
  push). This hub stores only `sha256(api_key)` — it never has to present
  this value again, only verify it, so hash-only storage is correct and
  sufficient (see `api/routers/ingest.py`).
- **`action_key`** — generated by **the deployment itself** and sent to
  this hub inside the `/register` request body. This hub, unlike with
  `api_key`, *must* be able to present this value later (when it calls
  back in to run an action) — so hash-only storage doesn't work here. It's
  stored **Fernet-encrypted** (`Deployment.action_key_encrypted`,
  `services/crypto.py`), mirroring PoultryOS-CBP's own precedent for
  reversible secrets (`User.totp_secret_encrypted`). This extra bar
  (encryption, not plaintext) exists because this hub aggregates this
  credential for *every* client deployment in one place — much higher
  blast radius than any single deployment's own locally-stored secret.

Never "simplify" this to a single shared key without re-reading this
section and `docs/CONNECTING_A_DEPLOYMENT.md`'s credential-reference table
— the asymmetry is deliberate, not an oversight.

### Registration can't be a literal migration

If you're tempted to make onboarding "more automatic" by having a
migration call out over HTTP: don't. There's no precedent for it in either
this repo or PoultryOS-CBP, and a migration-time network call has no
retry/backoff semantics and can block the whole container from booting if
the other side is unreachable at exactly the wrong moment. Registration
lives in a Celery-beat task on the *deployment* side
(`hub_sync_tasks.py` in PoultryOS-CBP) precisely so a hub outage just means
"try again next scheduled run," never a boot failure.

---

## Environment variables (`backend/.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres, async driver |
| `SECRET_KEY` | JWT signing key (staff login) |
| `HUB_ENCRYPTION_KEY` | **Required.** Fernet key for `action_key` encryption — the hub cannot run its inbound-action feature without it |
| `REDIS_URL` | Casbin cross-worker policy sync (`core/casbin_watcher.py`) — optional, degrades gracefully without it |
| `BACKEND_CORS_ORIGINS` | This hub's own frontend origin(s) |
| `FRONTEND_URL` | This hub's own frontend URL — used to build the deployment-detail link embedded in notification emails/WhatsApp messages (`services/notification_triggers.py`) |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | **Required for notifications.** Unlike `REDIS_URL` above, this is a real dependency — no email/WhatsApp alert sends without the `celery-worker` container reaching it. See `services/notifications/README.md` for this + the `SMTP_*`/`FROM_*`/`WHATSAPP_*`/`NOTIFICATIONS_ENABLED` variables. |
| `GITHUB_APP_ID` / `GITHUB_APP_SLUG` / `GITHUB_APP_PRIVATE_KEY` / `GITHUB_APP_WEBHOOK_SECRET` | Optional — enables the GitHub App ("Connect with GitHub") auth mode alongside the existing personal-access-token mode. Empty `GITHUB_APP_ID` (default) means unconfigured; the PAT mode needs none of these. See `docs/integrations/github.md`'s "GitHub App auth mode" and `docs/adr/ADR-004-github-app-auth.md`. |

Frontend env: `VITE_API_BASE_URL`.
