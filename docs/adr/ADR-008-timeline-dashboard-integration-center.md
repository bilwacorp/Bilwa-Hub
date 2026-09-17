# ADR-008: Unified Timeline, Operations Dashboard, Integration Center

Status: Accepted. Implemented in `api/routers/deployments.py`'s
`get_deployment_timeline` (Phase 9), `app/services/dashboard.py` +
migration `019_dashboard_permission.py` (Phase 10), and `api/routers/
integrations.py` + migration `020_integrations_permission.py` (Phase 11).
No new tables for any of the three — every number and every timeline
entry is a query over data Phases 1-8 already produce.

## Context

Phase 9 asks for a unified per-deployment timeline aggregating
`OperationalEvent` rows and linked integration records. Phase 10 asks
for an executive dashboard across fleet/deployments/support/maintenance/
approvals/integrations, plus an "Attention Required" section it
explicitly says matters more than charts. Phase 11 asks for a Settings →
Integrations page showing status/last sync/last error/last webhook/test
connection for GitHub, CI/CD, Email, WhatsApp, and Monitoring, never
exposing a secret. All three are read-only views over existing data, so
they're grouped in one ADR rather than three.

## Decisions

**1. A deployment's timeline is a plain `WHERE deployment_id = :id`
query — deliberately simpler than Phase 6's `ticket_timeline`.** Phase
6's ticket timeline had to UNION several different entity types (the
ticket itself, its deployment, and each linked GitHub issue/PR/release/
maintenance window) because a ticket's own id isn't stamped onto most of
those events. A deployment's id, by contrast, is already the ONE column
nearly every domain's events carry at emission time: heartbeats (Phase
1), GitHub events unambiguously mapped to one deployment (Phase 3),
lineage/release events (Phase 4/5), ticket events for that deployment's
own tickets (Phase 1/6), and maintenance lifecycle events (Phase 7 — see
decision 4 below). No aggregation logic was needed beyond reading that
one column, newest first, capped at 200 rows.

**2. The dashboard's row-level visibility mirrors every other router's
"scoped unless view_all" rule — but approvals/integrations sections are
NOT deployment-scoped, and quietly zero out instead of 403ing.** A
workflow instance or a GitHub webhook delivery isn't inherently tied to
one deployment the way a ticket or maintenance window is (see
`core/permissions.py`'s "Row-level visibility" section for the
established pattern this extends). Rather than inventing a new
scoping rule for those two sections, `app/services/dashboard.py` checks
whether the caller holds `approvals.view`/`workflow_instances.view` (for
Approvals) or `github.view`/`notifications.view` (for parts of
Integrations) and returns zeros for that one section if not — a
dashboard is a summary of what the caller can already see elsewhere,
never a way to see more than their other permissions already allow.

**3. "Outdated version," "high risk," "unassigned ticket," "escalated
ticket," and "awaiting engineering" are documented proxies, not new
first-class concepts.** None of these exist anywhere else in this
codebase as a flag or column. Each is defined once, precisely, in
`app/services/dashboard.py`'s module docstring and the relevant
function's own docstring:
- *Outdated version* = the deployment's current `DeploymentRelease` has
  a `repository_id` (HUB knows the linked repo) but no `release_id`
  (the reported version didn't match any synced `GitHubRelease` tag) —
  this is the same "version mismatch" signal Phase 10's own worked
  example lists under Attention Required ("Deployment version differs
  from expected release"), computed once and reused for both the count
  and the attention item.
- *High risk* = the deployment has at least one `DeploymentActionExecution`
  currently `failed` (an approved action that never reached the
  deployment).
- *Unassigned ticket* = an open ticket whose deployment has zero
  `DeploymentStaffAssignment` rows (falls back to notifying every
  fleet-area staff member per `services/notifications/recipients.py`,
  but nobody specific owns it).
- *Escalated* = open AND `priority` in `("high", "urgent")` — the two
  values a real client submission actually sends.
- *Awaiting engineering* = `in_progress` AND has at least one
  `SupportTicketLink` (Phase 6) — handed off to engineering already.

A future phase that wants a truer definition of any of these replaces
the proxy in this one place, not per caller.

**4. Fixing `core/maintenance_scheduler.py` to actually call
`record_event()` was pulled into Phase 7's work (see ADR-007
"Consequences"), not deferred, because Phase 9's OWN worked example
("Sep 15 22:00 / Maintenance completed") needs a
`maintenance.completed` `OperationalEvent` to exist at all.** Before
this batch, the scheduler only `logger.info`'d its auto-transitions —
building Phase 9's timeline without also fixing that emission gap would
have shipped a feature whose own example couldn't actually render.

**5. The Integration Center never calls out to a live external API on
page load.** Every card's "status" is derived from already-stored data
(the last webhook/sync/error timestamp on file, or the last
`NotificationLog` outcome) — the same reasoning `docs/architecture/
target-state.md`'s "Integration layer" section already gives for why
Phase 3's `GitHubIntegration.status/last_synced_at/...` fields exist:
Phase 11 reads them directly rather than adding new health-tracking
columns or a new live-probe endpoint. The one exception is the existing,
already-permission-gated `POST /github/integrations/{id}/test-connection`
button, which already existed before this phase and does make a live
call — Phase 11 just surfaces it on a summary card instead of
duplicating its logic.

**6. CI/CD has no integration of its own to show — its card derives
status from whether any `DeploymentRelease` row with
`source IN (github_actions, ci_cd)` has ever been recorded.** Consistent
with ADR-005's decision that GitHub Actions rides the GitHub
integration's own webhook rather than having separate credentials; the
CI/CD card's `detail` text says exactly this and points at ADR-005
rather than pretending it's an independent connection with its own
health.

**7. Email/WhatsApp cards read `NotificationLog`, gated by whether
`settings.SMTP_HOST`/`WHATSAPP_API_URL` is non-empty — "configured" is a
settings check, not a database flag.** These channels are configured via
environment variables (see `CLAUDE.md`'s Environment variables table),
not admin-editable credentials rows like `GitHubIntegration` — there's
no row to query for "is this connected," so the card's `status` is
`not_configured` whenever the relevant setting is blank, and otherwise
`error` only if the most recent attempt for that channel failed more
recently than the most recent success.

**8. `EventTimeline` (frontend) was factored out of Phase 6's
`SupportTicketDetailPage.tsx` into a shared `components/ui/EventTimeline.tsx`
rather than copy-pasted into `DeploymentDetailPage.tsx`.** Both pages
render the exact same server-curated `OperationalEvent[]` shape the same
way (a vertical dot-and-line timeline); the only difference is which
endpoint fetches the list.

## Consequences

- `GET /dashboard` and `GET /integrations` are each one HTTP round trip
  doing several queries server-side — acceptable at this hub's scale
  (a handful of deployments, tickets, and windows), and avoids a second
  denormalized "summary" table that would need its own invalidation
  logic on every write path across five domains.
- The dashboard has no auto-refresh faster than 60s (matching every
  other polling page in this app) — "Attention Required" is not a
  real-time alerting feed, it's a summary a staff member checks
  periodically, consistent with the phase brief's framing of it as a
  page section, not a notification channel.
- Monitoring's card is permanently `not_configured` until a real
  monitoring integration is built — `target-state.md` already flagged
  this as "mentioned in HUB-Expansion.md's Examples section, not yet
  scheduled" before this phase; Phase 11 doesn't change that, it just
  gives that fact a visible home instead of leaving it undocumented.
