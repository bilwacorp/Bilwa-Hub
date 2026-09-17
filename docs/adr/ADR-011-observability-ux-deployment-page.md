# ADR-011: Observability Summary, Nav Grouping, Deployment Detail Page Restructure

Status: Accepted. Implemented in `frontend/src/App.tsx` (Phase 17) and
`frontend/src/pages/deployments/DeploymentDetailPage.tsx` (Phase 16 +
18). No backend changes and no new tables — every field Phase 16 asks
for and every section Phase 18 asks for already existed as data
somewhere in this codebase before this phase; the work here is
presentation, not new computation.

## Context

Phase 16 asks for a "summarized operational health model" per
deployment — last heartbeat, heartbeat age, API health, application
version, last deployment, last deployment result, current maintenance
state — explicitly warning not to turn this hub into "a full
observability platform." Phase 17 asks for a reorganized sidebar around
operational workflows, with a specific suggested nested tree (Fleet,
Support, Maintenance, Approvals, Engineering, etc., several with their
own sub-items like Escalations/Calendar/Active/History). Phase 18 gives
a specific suggested structure for the deployment detail page: Header
(client/environment/health/version + five action buttons including two
new ones), then Overview/Software/Support/Maintenance/Approvals/
Timeline/Technical sections. All three are grouped in one ADR because
Phase 16's fields all landed inside Phase 18's restructured page rather
than as a separate view — see Decision 1.

## Decision 1 — Phase 16 has no page of its own; its fields became Phase 18's Technical + Overview sections

Building a standalone "Observability" page would have duplicated data
the deployment detail page already had reachable — every field Phase 16
lists (`last heartbeat`, `heartbeat age`, `API health`, `application
version`, `last deployment`, `last deployment result`, `current
maintenance state`) is either already a column on `Deployment`/
`DeploymentSnapshot`, already computed by the existing `derived_status`
health logic (`DeploymentsListPage.tsx`'s `healthDot()` — extracted into
a matching `healthBadge()` helper on the detail page), or already
rendered somewhere on the old detail page under a different heading.
Phase 16's real ask — "summarized," "do not turn HUB into a full
observability platform," "dashboard should link to detailed monitoring
rather than reproduce Grafana/Sentry" — is a constraint on the *existing*
pages, not a mandate for a new one: the fleet dashboard (ADR-008) already
summarizes across all deployments, and this phase's job was to make the
*single-deployment* summary equally complete. Concretely: the old "Live
Health Check" section was renamed **Technical** and gained three summary
lines (Deployment URL, Last heartbeat, GitHub integration status) ahead
of the existing live health-check button/result, and a new **Overview**
label was added above the existing stat-card grid (Subscription/Plan/
Expiry/Usage/Heartbeat/Health) — satisfying Phase 16's list without a new
route, a new permission, or a new query.

## Decision 2 — Sidebar grouping is shallow (two real groups), not the doc's full nested tree

Phase 17's suggested navigation nests sub-items under Support
(Tickets/Escalations), Maintenance (Calendar/Active/History), Approvals
(My Approvals/All Approvals/History), and Engineering (Repositories/Pull
Requests/Releases) — none of which exist as separate pages in this
codebase, and none of which were built for this phase. Following Phase
17's own explicit rule — "Do not add pages merely because a database
table exists. Each page should answer an operational question." — none
of those four sub-groups were created: an "Escalations" page would just
be `SupportTicketsPage.tsx` pre-filtered by priority (already achievable
with the existing table's sort/filter), a "History" sub-page under
Approvals/Maintenance would duplicate status filtering the existing
single pages already support, and "Engineering" (Repositories/PRs/
Releases) has no dedicated pages in this app at all — that data is
surfaced inline on the deployment detail page's Software section and the
GitHub integration panel, not as fleet-wide standalone views.

What *did* change: `NAV_ITEMS` gained an optional `group` field, and only
two real groups were created — **Fleet** (Deployments/Customers/
Applications) and **Workflows** (Workflows/Approval Rules) — because
these are the only places where 2+ existing top-level pages are
genuinely one operational area under the doc's own taxonomy. A group
header only renders when the currently-visible (permission-filtered) set
of items in that group has 2 or more entries — an `engineer` role that
can only see one of the three Fleet pages sees it as a plain top-level
item with no orphaned "Fleet" header sitting above a single link. This
also fixed a genuine pre-existing bug surfaced while auditing the nav:
`ApprovalRulesPage.tsx` had a working route (`/workflow-rules`) with
zero nav link pointing at it — it's now reachable via the new Workflows
group.

## Decision 3 — Deployment detail page: labeling-and-addition, not a full physical reorder

Phase 18's suggested section order is Header → Overview → Software →
Support → Maintenance → Approvals → Timeline → Technical. The page was
restructured to match this **conceptually** — every one of those eight
sections now exists, in roughly that grouping — but existing blocks were
relabeled and new ones inserted around them rather than the entire page
being torn down and rebuilt in exact top-to-bottom doc order:

- **Header** — gained a `healthBadge()` pill and environment badge next
  to the client name (mirroring the fleet list's health indicator), and
  the action-button row was moved to sit directly under the header (was
  previously further down the page) with two new actions added: **Health
  Check** (already existed as a mutation, just relocated into the main
  action row) and **Schedule Maintenance** (genuinely new — opens a
  modal that creates a `MaintenanceWindow` pre-scoped to
  `deployment_id`, gated by `maintenance.create` AND `maintenance.view`
  since a caller who can't see the Maintenance section shouldn't be
  offered a button that creates something they can't then view).
- **Software** — this is the pre-existing "Lineage" section, renamed
  only; its repository/release/commit/last-deployment/deployment-actor
  fields already matched Phase 18's list exactly (Phase 4/5's work).
- **Support** (new) — up to 5 recent tickets for this deployment
  (`GET /tickets?deployment_id=`), each clickable through to
  `SupportTicketDetailPage`, gated by `tickets.view`.
- **Maintenance** (new) — Active/Upcoming/History buckets, pulling from
  `GET /maintenance-windows` filtered client-side to windows where
  `deployment_id` matches this deployment OR is null (fleet-wide),
  gated by `maintenance.view`. This is also where the new Schedule
  Maintenance modal's created window becomes visible without a page
  reload (query invalidation on the `['maintenance-windows']` key).
- **Approvals** — the pre-existing "Action Executions" section, relabeled
  "Approvals — Action Executions" with a comment noting it deliberately
  does not duplicate maintenance-window approval status (that lives in
  the new Maintenance section's own status badges instead).
- **Timeline** — unchanged; already existed exactly as Phase 18
  describes it (ADR-008 decision 1/8).
- **Technical** — see Decision 1 above.

**The explicit tradeoff**: doc order was not treated as a hard
requirement for physical placement, because this page was already one of
the largest/most-stateful pages in the frontend (renew/suspend/
change-plan/extend-expiry modals, lineage-edit modal, live health-check
polling, the timeline query, and now the maintenance/tickets queries and
the new schedule-maintenance modal all coexist in one component). A full
physical reorder of every existing block risked a regression in an
already-complex page for a benefit — matching the doc's exact visual
top-to-bottom order — that's cosmetic once every section exists and is
correctly labeled. This was a conscious choice, not an oversight: verify
against a future re-read of this page that every section from Phase 18's
list is present and correctly scoped (it is, per this ADR and the
browser-verified walkthrough performed for this phase), not that they
appear in the doc's exact pixel order.

## Consequences

- No new backend endpoints, permissions, or tables were needed for
  Phases 16/17/18 — everything reads from routes that already existed
  (`/tickets`, `/maintenance-windows`, `/deployments/{id}`,
  `/deployments/{id}/timeline`) except the new Schedule Maintenance
  modal's `POST /maintenance-windows` call, which already existed as an
  endpoint (`MaintenanceWindowsPage.tsx` already used it) and was simply
  given a second, deployment-pre-scoped entry point.
- A future phase adding a genuinely new fleet-wide concept (e.g. a real
  "Escalations" view distinct from filtering the existing tickets table)
  should re-open this ADR's Decision 2 reasoning rather than assume the
  doc's suggested nested nav was rejected outright — it was deferred for
  lack of a distinct underlying page, not judged permanently unnecessary.
- The Schedule Maintenance modal's lockout-mode copy ("Lockout-mode
  windows require admin approval before they're scheduled") reflects the
  existing Phase 7/8 approval-gating behavior for that one enforcement
  mode — this modal doesn't introduce new approval logic, it's a second
  UI entry point into the same `POST /maintenance-windows` flow
  `MaintenanceWindowsPage.tsx` already gated the same way.
