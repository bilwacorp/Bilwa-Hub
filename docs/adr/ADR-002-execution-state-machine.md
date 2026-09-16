# ADR-002: Deployment Action Execution State Machine + Idempotency

Status: Accepted. Implemented in migration `014_deployment_action_executions.py`.

## Context

`HUB-Expansion.md`'s Phase 12 identifies a real, confirmed bug (verified in
`docs/architecture/current-state.md`'s Phase 0 audit): once a gated
deployment action (renew/suspend/change_plan) is approved, the actual call
to the client deployment ran synchronously inside the *same request* that
approved the task, and a failure was only ever `logger.warning`'d —
`app/approvals/deployment_hooks.py`'s completion hooks swallowed
`DeploymentCallError` entirely. The approving staff member's HTTP response
showed a successful `WorkflowInstance` (`status: completed`) with no
indication the deferred call actually failed. There was no retry path at
all.

Phase 13 additionally asks for idempotency on every retryable external
action, so a retried call can't accidentally double-execute a money-moving
action (e.g. renewing twice).

## Decisions

**1. A new `DeploymentActionExecution` tracks the deferred call's own
state, separately from the approval's state** — `WorkflowInstance.status ==
completed` means "the approval succeeded"; `DeploymentActionExecution.status`
(`pending`/`executing`/`executed`/`failed`, matching the doc's own
`EXECUTION_PENDING`/`EXECUTING`/`EXECUTED`/`EXECUTION_FAILED` naming) means
"the deferred call to the deployment succeeded." These are now visibly
different facts — `GET /deployments/{id}/action-executions` can show
`failed` for a renewal whose approval shows `approved`.

**2. One `DeploymentActionExecution` row is created the moment approval is
*requested*, not when it completes** — so an operator can see "this is
queued, waiting on approval" before anyone has acted on it, not just after
a failure. `status` starts at `pending` and never goes to `executing`
until an approval actually completes (or a retry is triggered).

**3. `DeploymentActionAttempt` is a separate append-only child table**, one
row per attempt (mirrors `WorkflowHistory`'s existing "separate history
table, not a JSON blob on the parent" shape in this codebase) — this is
the "retry history" the doc asks for: attempt number, outcome, error,
raw response, who triggered it (`NULL` = the automatic first attempt from
the completion hook; a user id = a manual retry), start/finish timestamps.

**4. `_execute()` is the one function both the automatic path and manual
retry go through** — `app/approvals/deployment_hooks.py`'s three
near-identical `_on_*_complete` functions collapsed into one
`_on_gated_action_complete` plus a `_CLIENT_CALLS` dispatch dict, since the
state-transition/attempt-logging/event-emission logic is now identical
regardless of which of the three actions it is. This is also what makes
the guard against duplicate execution simple: `_execute()` is a no-op if
`status` is already `executing` (an attempt is in flight) or `executed`
(already succeeded) — Phase 13's "never blindly repeat money-moving
actions," enforced locally rather than trusted to the remote side.

**5. Idempotency key is `hub-action-{workflow_instance_id}`, sent as
`X-Idempotency-Key`** — exactly the shape Phase 13's own example gives.
Sent on every attempt of a given execution, including retries, so a
deployment that understands the header can dedupe a retried call itself.
This is additive to `services/deployment_client.py` (a deployment that
ignores the header sees no behavior change) — the real backstop remains
decision 4's local state guard, per the doc's own fallback: "Where the
remote deployment API supports idempotency, use it. Where it does not,
design a local action state machine..." We do both, but only control one.

**6. The call is still made synchronously (within the approve request, or
within the retry request) — this ADR deliberately does not move execution
to Celery.** The coding principles list "asynchronous external calls" as
a preference and this hub already has a Celery worker for notifications,
so dispatching the deployment_client call as a Celery task instead is a
legitimate further improvement. It's out of scope here: the confirmed bug
was invisible failure and no retry, not "the approve button blocks on a
network call" — fixing the latter is a larger, separate change (task
routing, idempotent task IDs, polling/webhook for the UI to learn the
outcome) that wasn't part of what was actually broken.

**7. `actions.retry` is one coarse permission, not per-action-type** —
matching the doc's Phase 14 permission list literally. Retrying is treated
as one meaningful capability regardless of whether the underlying action
is renew/suspend/change_plan, granted to both `admin` and `engineer` (same
"engineer keeps action permissions" pattern as every prior granular-
permission migration).

## Consequences

- `DeploymentDetailPage.tsx`'s new "Action Executions" section is the only
  place an operator can currently see "approved but execution failed" —
  Phase 9's future unified timeline should surface this too, reading from
  the same table rather than re-deriving it.
- Extending this to ungated actions (extend_expiry, review_request) or to
  maintenance pushes was deliberately not done: extend_expiry/
  review_request already surface failure synchronously to the caller (a
  502) since there's no deferred step to hide a failure behind, and
  maintenance pushes already have their own retry loop
  (`core/maintenance_scheduler.py`). The gap this ADR fixes is specific to
  the deferred-execution-after-approval path.
- A future move to Celery-dispatched execution (decision 6) would reuse
  `DeploymentActionExecution`/`DeploymentActionAttempt` as-is — the task
  would just call `_execute()` instead of the completion hook calling it
  inline — so this isn't a dead end if that's revisited later.
