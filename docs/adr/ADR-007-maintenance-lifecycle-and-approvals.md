# ADR-007: Maintenance Lifecycle + Generic Operational Approvals

Status: Accepted. Implemented in migration `018_maintenance_lifecycle.py`
(`app/models.py`'s widened `MaintenanceWindowStatus` +
`MaintenanceWindow.expected_impact`/`actual_impact`/`approved_by`) and
`app/approvals/maintenance_hooks.py`.

## Context

`HUB-Expansion.md` Phase 7 asks for a real maintenance lifecycle
(draft/scheduled/approval required/approved/notification/active/
completed/failed/cancelled) with severity levels
(NOTICE/READ_ONLY/LOCKOUT), while Phase 8 asks to "expand" — not
replace — the existing SpiffWorkflow approval engine so business
services only need to "request approval, provide context, register
completion callback." These two phases turned out to be one piece of
work: Phase 7's `approval_required` state IS Phase 8's expanded engine
use, applied to a second business domain after
`app/approvals/deployment_hooks.py` (renew/suspend/change_plan).

## Decisions

**1. `planned`/`in_progress` are NOT renamed to the phase's own
"scheduled"/"active" vocabulary.** Renaming would touch every existing
reference across `core/maintenance_scheduler.py`, `services/
maintenance_push.py`, `api/routers/maintenance.py`, the frontend, and
prior tests — for a purely cosmetic difference in spelling. "Existing
behavior must remain compatible" is read here as "existing code, tests,
and any external consumer keying off these exact string values keeps
working," not "the enum member names must match the prose exactly."
`MaintenanceWindowStatus`'s own docstring documents the mapping.
`draft`/`approval_required`/`approved`/`notification`/`failed` are new
members with no prior spelling to preserve.

**2. Phase 8 required almost no new engine code.** `app/approvals/
integration.py`'s `start_approval()` and `app/approvals/hooks.py`'s
`register_completion_hook()` already ARE the "request approval / provide
context / register completion callback" interface the phase brief
describes — verified by re-reading `deployment_hooks.py`, which already
uses exactly this shape. Phase 8's real work is entirely in `app/
approvals/maintenance_hooks.py`, a new ~100-line file applying that same
interface to a second `business_object_type`
(`maintenance_window_approval`) — no changes to `app/workflow/` or
`app/rules/` at all. This is the concrete evidence the engine actually
is generic, not a claim taken on faith.

**3. `maintenance_hooks.py` has no `DeploymentActionExecution`-style
execution-tracking table, deliberately.** `deployment_hooks.py` needed
one because a REAL external HTTP call (to a client deployment) can fail
independently of the approval decision, and that failure needs its own
retry/attempt history (Phase 12/13). A maintenance window's "action" is
just a status transition plus the push machinery `services/
maintenance_push.py` already retries every scheduler tick regardless of
approval history — there's no second failure mode to track separately.

**4. Only two conditions make a window "high risk" enough to gate:
fleet-wide (`deployment_id IS NULL`) or `mode == "lockout"`.** A plain
single-deployment banner/read_only window — the overwhelming common
case, and everything this hub supported before Phase 7 — is NEVER
gated, so "existing behavior must remain compatible" holds for the case
that actually matters. `app/approvals/maintenance_hooks._is_high_risk`
is the one place this rule lives; it's intentionally narrower than "every
window ever," matching the doc's own "READ_ONLY"/"LOCKOUT" severity
framing (NOTICE alone was never meant to be the trigger).

**5. `maintenance_window_approval` is seeded ACTIVE by default (migration
018), same as the three deployment-action workflows (migration 012).**
This is a deliberate, real behavior change for the high-risk subset
(new fleet-wide/lockout windows now require admin approval where they
previously didn't) — not an oversight. The phase's own state list
(`approval_required` as a reachable status) only makes sense if
something actually reaches it; an admin can turn this off at any time by
deactivating/unpublishing the seeded definition from the Workflows page,
exactly like the three deployment actions.

**6. Approval outcomes map onto window status as: `completed` →
`approved` (then auto-promoted to `planned` on the next scheduler tick —
see decision 7), `rejected`/`cancelled` → back to `draft` (so the
submitter can revise and resubmit, rather than a permanently stuck
window), `error` → `failed` (a routing-config problem, not a business
outcome the submitter can fix by resubmitting).**

**7. `approved` is a real, briefly-persisted status, not skipped.**
`core/maintenance_scheduler.py`'s `_auto_transition` promotes every
`approved` window straight to `planned` at the START of each tick,
before that same tick's `to_start`/push-pending queries run — so an
approved window is picked up for scheduling within one scheduler
interval, with no additional wait condition. This satisfies the phase's
literal 9-state list (an `approved` row genuinely exists, however
briefly) without inventing a second condition to gate on.

**8. `notification` is purely informational — never a distinct wait
condition.** It's set the first time `services/maintenance_push.py`'s
`sync_window` successfully sends the advance-notice push to a `planned`
window's target(s). Every clock-driven query in the scheduler and
`services/maintenance_query.py` treats `planned` and `notification`
identically (see `maintenance_scheduler.py`'s `_SCHEDULED` tuple) — the
only thing `notification` communicates is "the advance notice already
went out," which the doc's own example flow lists as a real stage
between "approved" and "active."

**9. `failed` is a manual-only terminal status — nothing auto-detects
a maintenance failure.** Unlike `DeploymentActionExecution` (a real HTTP
call whose success/failure is directly observable), nothing in this
codebase's maintenance domain has a live signal that a window "went
badly" — the push to a deployment succeeding just means the deployment
received the window's schedule, not that the maintenance itself
succeeded. Staff mark `failed` by hand via the same `PATCH`
status-change path as marking `completed`, optionally filling in
`actual_impact` to record what went wrong.

**10. `expected_impact`/`actual_impact` are free text, same shape as
`description`.** No structured "impact taxonomy" was invented — the doc
doesn't ask for one, and this hub has no other structured-impact concept
to be consistent with.

## Consequences

- `core/maintenance_scheduler.py`'s `_auto_transition` now calls
  `record_event()` for `maintenance.started`/`maintenance.completed` —
  previously it only logged. This closes a real gap Phase 9's own
  "Maintenance completed" timeline example needed (see
  ADR-008) and is a deliberate behavior addition, not a side effect to
  work around.
- No multi-specific-deployment targeting was built (a window still
  targets exactly one deployment or the whole fleet, as before) — the
  phase's one-line "can affect one or multiple deployments" requirement
  was deliberately scope-cut to keep the total Phase 7-11 batch
  tractable. A `MaintenanceWindowDeployment` join table, additive to the
  existing `deployment_id` column exactly like ADR-004's `Customer`/
  `Application` pattern, is the natural follow-on if a real need for it
  shows up.
- `approval_required` windows have no "Cancel" affordance in the
  frontend today — cancelling one would need to also cancel its
  underlying `WorkflowInstance` (currently `running`), which isn't wired.
  Rejecting the approval task (already possible via the Approvals inbox)
  is the correct path back to `draft` today.
