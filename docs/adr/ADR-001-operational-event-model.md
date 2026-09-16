# ADR-001: Operational Event Model

Status: Accepted. Implemented in migration `013_operational_events.py`.

## Context

`HUB-Expansion.md`'s Phase 1 calls for a generic `OperationalEvent` table so
the hub can answer "what happened, when, and who/what did it" across every
domain (deployments, tickets, maintenance, approvals) without every future
integration inventing its own audit trail. The Phase 0 audit
(`docs/architecture/current-state.md`) found no existing cross-domain audit
log — only `WorkflowHistory` (workflow-instance-scoped) and
`NotificationLog` (notification-attempt-scoped) exist.

## Decisions

**1. `event_type`/`source`/`actor_type`/`entity_type` are plain strings, not
native Postgres enums** — the one deliberate deviation from this
codebase's otherwise-universal "status columns are native enums" convention
(see migrations 001/006/012). Every other status-shaped column in this app
is a small, closed set that changes rarely. `event_type` is the opposite: it
is expected to grow by several new values in nearly every future
`HUB-Expansion.md` phase (GitHub events in Phase 3, CI/CD events in Phase 5,
maintenance-lifecycle events in Phase 7, ...). A native enum would need an
`ALTER TYPE ... ADD VALUE` migration for every single new value forever.
Instead, `core/event_types.py` holds a Python-side canonical catalog —
mirroring `core/permissions.py`'s `ALL_PERMISSIONS` shape, which has the
same "grows by convention, not by schema change" property. `status`
(`OperationalEventStatus`: info/pending/success/failure) is a genuinely
small, closed set and stays a native enum for consistency.

**2. `entity_type`/`entity_id` is a polymorphic reference with no FK** —
same shape as `WorkflowInstance.business_object_type`/`_id`
(`workflow/models.py`), which is the precedent this mirrors. A single FK
can't point at more than one target table, and `MaintenanceWindow` rows are
hard-deleted (`maintenance.py`'s `delete_window`) — an event describing a
now-gone window would break a strict FK. `deployment_id` *is* a real FK,
since `deployments` rows are never deleted by this app (no delete endpoint
exists).

**3. `OperationalEvent` does not duplicate `WorkflowHistory`** — the two
tables serve different granularities on purpose. `WorkflowHistory` stays
the detailed per-instance/per-task trail for the approval engine (every
task created/approved/rejected/reassigned). `OperationalEvent` holds exactly
one coarse event per instance-level terminal transition
(`approval.requested`/`approved`/`rejected`, `workflow.cancelled`/
`errored`) plus, separately, the domain-specific event the business router
already knows about (`deployment.renew_requested`/`_executed`/`_failed`,
etc.). Phase 9's unified timeline is expected to read from both tables and
merge them, not replace either.

**4. Correlation, not causation, is Phase 1's scope** — `correlation_id` is
populated everywhere (defaults to a fresh id when the caller has no
existing chain to join); `causation_id` exists in the schema per the Phase 1
field list but is never populated yet. For the one flow that already spans
multiple requests today — a gated deployment action's request → approve/
reject → deferred execution — a single `correlation_id` is generated once
in `deployment_hooks.request_or_execute` and threaded through via a new
`WorkflowInstance.correlation_id` column (migration `013`), so every event
in that chain shares it regardless of how many separate HTTP requests
(request, approve, the async completion hook) it spans. Full causation
trees (ticket → GitHub issue → PR → release → deployment) are Phase 2 scope,
once there's something upstream to chain against.

**5. Every gated action generalizes to the same event-name shape** —
`HUB-Expansion.md`'s own example list spells out the full
`_requested/_approved/_rejected/_executed/_failed` shape only for "renew"
and gives single terminal names ("suspended", "plan_changed") for suspend/
change-plan. Since all three go through the exact same
`deployment_hooks.request_or_execute` mechanism, `core/event_types.py`
generalizes the fuller shape to all three for internal consistency
(`deployment.suspend_requested`/`_executed`/`_failed`, etc.) rather than
mixing naming schemes per action.

**6. `record_event()` never commits** — it only calls `db.add(...)`, so an
event lands in the exact same transaction as the business mutation it
describes (`services/events.py`). This follows the Phase 0 audit's
database-first-integrity principle: an event and the change it records must
succeed or fail together, not risk the event being lost to a second,
separate commit.

## Consequences

- Adding a new event type in a later phase is a one-line addition to
  `core/event_types.py`, no migration.
- The generic `approval.approved`/`rejected` events (emitted from
  `approvals/hooks.py`'s `fire_if_terminal`) never carry `deployment_id` —
  that code has no business assuming `business_object_id` is always a
  deployment id, since Phase 8 will route non-deployment operational
  workflows (maintenance, high-risk-deployment) through the same engine.
  Only the domain-specific events (emitted from `deployment_hooks.py`,
  which *does* know it's a deployment) are deployment-scoped. A query for
  "everything about deployment X" must not assume every related event has
  `deployment_id` set — see `api/routers/events.py`'s row-level visibility,
  which treats `deployment_id IS NULL` as always-visible for this reason,
  same as `maintenance.py`'s fleet-wide-window pattern.
- Two new Casbin permissions (`events.view`/`events.view_all`), granted to
  `admin` (both) and `engineer` (`view` only) — same pattern as every prior
  granular-permission migration.
