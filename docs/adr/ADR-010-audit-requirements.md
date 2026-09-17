# ADR-010: Sensitive-Action Audit Coverage and Actor IP Capture

Status: Accepted. Implemented in `app/core/event_types.py` (new
`STAFF_*`/`ROLE_*` event types), `api/routers/users.py`,
`api/routers/rbac.py`, `app/core/request_context.py` (new),
`app/main.py`'s `client_ip_middleware`, `app/services/events.py`, and
migration `021_audit_ip_rbac_events.py` (adds `operational_events
.actor_ip`).

## Context

Phase 19 lists thirteen fields a sensitive action's audit record should
carry — actor, action, resource, resource ID, deployment, timestamp, IP
where appropriate, correlation ID, approval instance, previous state, new
state, outcome, failure reason — and says audit records "should not be
casually editable or deletable." `OperationalEvent` (ADR-001) already
carries eleven of the thirteen for every domain that emits it. Two gaps
existed: staff/RBAC management had **zero** `record_event()` calls
anywhere (verified by grep before starting — `users.py` and `rbac.py`
were completely unaudited), and no event anywhere carried the caller's
IP address, since `record_event()`'s signature has no way to receive
per-request data without threading it through ~30 existing call sites.

## Decision 1 — Staff/RBAC actions get their own event types, not reused generic ones

Ten new event types were added (`STAFF_CREATED`, `STAFF_UPDATED`,
`STAFF_ROLE_CHANGED`, `STAFF_DEACTIVATED`, `STAFF_REACTIVATED`,
`STAFF_PASSWORD_RESET`, `ROLE_CREATED`, `ROLE_RENAMED`, `ROLE_DELETED`,
`ROLE_PERMISSIONS_UPDATED`) rather than routing everything through one
generic `staff.updated`/`role.updated` pair, matching this codebase's
existing convention of one event type per distinct action rather than
one per resource (e.g. `deployments.py` already has separate
`DEPLOYMENT_RENEWED`/`DEPLOYMENT_SUSPENDED`/`DEPLOYMENT_PLAN_CHANGED`
rather than one `DEPLOYMENT_UPDATED`). `update_user`'s single PATCH
route can change several fields in one call, so its event type is
computed by priority (role change > deactivation > reactivation > plain
field update) — a role change is the most operationally significant of
the possible simultaneous edits, so it wins if the caller changed the
role and something else in the same request.

**Before/after state**: every mutation captures a `changes: dict` of
`{"from": ..., "to": ...}` per changed field, built **before** the ORM
attributes are mutated (reading the old value after mutation would just
read back the new one). `set_role_permissions` and `delete_role` do the
same for a role's permission set, using the pre-existing `current`/
`role_perms` query results already computed by the route for its own
response — no extra query was added purely for the audit record.

**One deliberate scope cut**: `update_role`'s `ROLE_RENAMED` event fires
only when `body.name` actually changes, not on every call to that route
— a description-only edit is not "sensitive" in the sense Phase 19 means
(nothing about who-can-do-what changed), and auditing every keystroke of
a description edit would add noise without adding traceability of an
actual permission-relevant action.

**The password itself is never logged.** `reset_password`'s
`STAFF_PASSWORD_RESET` event metadata is `{"username": ...}` only — the
new password value never touches `record_event()`'s metadata argument,
matching the existing `SENSITIVE_CONTEXT_KEYS` precedent in
`services/notifications/` that keeps a password-reset URL out of
`NotificationLog.payload`.

## Decision 2 — Actor IP via a contextvar + middleware, not a parameter added to every call site

`record_event()` is called from roughly thirty places across every
router in this codebase. Adding an `ip: Optional[str]` parameter to its
signature would have required threading `request: Request` through every
one of those call sites (most of which don't currently take a `Request`
parameter at all) just to read one header. Instead, `app/core/
request_context.py` defines a single `contextvars.ContextVar[Optional[str]]`
plus `set_client_ip`/`get_client_ip`. A new `client_ip_middleware` in
`main.py` (registered before the existing `request_id_middleware`) reads
`X-Real-IP` (falling back to `request.client.host`) once per request and
sets it into the contextvar; `record_event()` reads it back via
`get_client_ip()` with no signature change and no caller-side change
required anywhere. `X-Real-IP` is trusted here because this backend has
exactly one public ingress — the nginx proxy in `frontend/nginx.conf`
(see `CLAUDE.md`'s Docker/compose section) — which sets that header
itself; there is no other path a request can reach this backend by that
could forge it.

`OperationalEvent.actor_ip` is nullable — every event whose actor is
`ACTOR_SYSTEM` (a scheduler tick, a completion hook, a background retry)
has no request in flight at all and is correctly left null rather than
recording e.g. the last web request's IP by accident (the contextvar
resets per-request by FastAPI's own per-request context, so a background
task running outside a request never sees a stale value).

## Decision 3 — Audit immutability is already satisfied, verified by test rather than by new code

Phase 19 says audit records must not be "casually editable or
deletable." `api/routers/events.py` (the one router touching
`OperationalEvent`) was already append-only before this phase — it has a
list endpoint and nothing else, no PATCH/DELETE/PUT for any event, by
omission rather than by an explicit permission check that could be
misconfigured. `test_audit_requirements.py`'s
`test_events_router_has_no_write_endpoints` asserts this by calling
PATCH/DELETE against `/events/{fake_id}` and expecting 404 (no route
pattern matches at all, not 405 — there's no dynamic `/events/{id}`
route registered at any method). No code change was needed here; the
test exists so a future PR that adds an events-editing endpoint has to
consciously delete or update this test rather than silently
reintroducing editability.

## Consequences

- `users.py`/`rbac.py` now produce a full audit trail for exactly the
  actions Phase 19's own field list implies matter most — "who can do
  what, and who changed it" — closing a gap that existed since staff
  management was first added, well before this HUB-Expansion.md work
  began.
- Every event from this phase forward (not retroactively — existing rows
  keep `actor_ip = NULL`) carries the caller's IP with zero changes to
  any of the ~30 pre-existing `record_event()` call sites.
- `OperationalEvent`'s "previous state"/"new state" fields (Phase 19's
  list) are satisfied per-event via `metadata.changes`, not by adding
  two new top-level columns — consistent with ADR-001's original
  decision to keep `OperationalEvent` a single generic table with a JSON
  metadata column rather than a per-domain schema.
