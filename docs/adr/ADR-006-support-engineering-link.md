# ADR-006: Support ↔ Engineering Link

Status: Accepted. Implemented in migration `017_ticket_links.py`
(`app/models.py`'s `TicketLinkType`/`SupportTicketLink`) +
`app/services/ticket_links.py` + `api/routers/tickets.py`'s
`/tickets/{id}/links`/`/tickets/{id}/timeline` endpoints.

## Context

`HUB-Expansion.md` Phase 6 ("a major feature") asks a support ticket to
link to a GitHub issue/PR/release, a deployment, a maintenance window,
and an "incident," with a resulting ticket timeline showing the whole
lifecycle (created → assigned → issue created → PR merged → release
created → deployment updated → health verified → resolved). This also
closes the gap `task-track.md` flagged after Phase 1: `OperationalEvent
.causation_id`/formal cross-system correlation (Phase 2) was waiting on
something to actually link a ticket to — this phase is that something.

## Decisions

**1. No `incident` link type, and no `deployment` link type either.**
No `Incident` concept exists anywhere in this codebase, and inventing one
now — for a field the phase's own worked example never actually shows a
value for — would be exactly the kind of unnecessary infrastructure
`HUB-Expansion.md` rule 5 warns against; a future phase can add it if a
real incident-tracking need shows up. `deployment` isn't a `TicketLinkType`
member either, since `SupportTicket.deployment_id` (Phase 1) already
names the ticket's deployment — a second `TicketLinkType.deployment`
member would just be two ways to express the same fact. `TicketLinkType`
is therefore exactly four members: `github_issue`, `github_pull_request`,
`github_release`, `maintenance_window`.

**2. No ticket-assignee feature, even though the worked example's
timeline shows an "Assigned" step.** This codebase has no concept of
assigning a *ticket* to a person (unlike `DeploymentStaffAssignment` for
deployments) and building one is unrelated to "link a ticket to X" — the
actual ask of this phase. The timeline aggregation (decision 4) simply
won't show an "Assigned" step today, the same way it wouldn't show any
other event type nothing in this codebase currently emits. Adding ticket
assignment is a legitimate future feature, just not one this phase's
"link to X" scope implies.

**3. `SupportTicketLink.target_id` is a plain UUID with no FK constraint,
same as `OperationalEvent.entity_id`.** Which table it points into
depends on `link_type`, so a single FK isn't possible without either a
join table per target type (four tables for four link types is more
machinery than the problem needs) or a polymorphic-association pattern
this codebase already has working precedent for. Existence is validated
at creation time instead (`services/ticket_links.target_exists`), and a
`UNIQUE(ticket_id, link_type, target_id)` constraint prevents the same
link being added twice. Consistent with ADR-004 decision 4, `app/
models.py` still never imports the GitHub integration package — the
join lives in `services/ticket_links.py`, not an ORM `relationship()`.

**4. The "ticket timeline" is an aggregation query over the EXISTING
`OperationalEvent` table, keyed by entity reference — not a shared/
propagated `correlation_id`.** `OperationalEvent`'s own docstring (Phase
1) floated `causation_id` for exactly this "ticket -> PR -> release ->
deployment" chain, but propagating one correlation/causation id across
independently-timestamped, independently-caused events doesn't actually
work here: a GitHub issue's `github.issue.created` event is emitted (with
its own per-webhook-delivery `correlation_id`, see ADR-003) at the moment
the webhook arrives — which can be *before* any ticket ever links to it.
Retroactively rewriting that event's `correlation_id` once a link is
created later would mean mutating an append-only audit log after the
fact, and every *subsequent* event for that same issue (e.g. a second
`github.issue.updated`) would still arrive with its own fresh,
unrelated webhook-delivery `correlation_id` — there's no single id to
converge on. `services/ticket_links.ticket_timeline` sidesteps this
entirely: it unions `WHERE (entity_type, entity_id)` conditions for the
ticket itself, its deployment (`SupportTicket.deployment_id`), and every
`SupportTicketLink` target's own `entity_type`/`entity_id` pair — all
four of the entity-type constants involved (`ticket`, `deployment`,
`github_issue`, `github_pull_request`, `github_release`,
`maintenance_window`) already existed in `core/event_types.py` before
this phase. No new event-emission code was needed anywhere — Phase 1/3/
maintenance handling already emit into this one table for each of those
entities; this phase only had to union the right `WHERE` clauses. This
is also, in effect, Phase 2's formal cross-system correlation for the
ticket ↔ GitHub case: entity-reference aggregation, not a shared
correlation id, is the correct mechanism for retroactive many-to-one
linking.

**5. `tickets.manage_links` is a new permission, separate from
`tickets.update_status`.** Linking a ticket to engineering artifacts is a
distinct capability from changing its status; a future custom role might
reasonably want one without the other (e.g. a read-mostly triage role
that updates status but never touches engineering links). Both `admin`
and `engineer` get it in migration `017` — same "keeps every action
permission it already effectively had on this resource" pattern as
migration `011`'s `tickets.update_status` grant to `engineer`.

**6. "Do not automatically close tickets" is honored by adding NO
automation at all.** Linking a ticket to a merged PR or a published
release doesn't change the ticket's status, doesn't notify anyone beyond
the existing `ticket.link_added`/`ticket.link_removed` `OperationalEvent`
rows, and doesn't touch the workflow/approval engine. Status changes stay
exactly where they were before this phase: a manual `PATCH /tickets/{id}`
call gated by `tickets.update_status`.

**7. The frontend's "Add Link" target picker is scoped to the ticket's
own deployment's linked GitHub repo(s) (Phase 3's mapping) and, for
maintenance windows, that deployment's own windows plus fleet-wide
ones — not a fleet-wide search across every repo/window HUB knows
about.** A ticket only makes sense to link against artifacts relevant to
its own deployment. This reuses `GET /deployments/{id}/github` (Phase 3)
and `GET /maintenance-windows` (pre-existing) rather than adding new
list-with-ticket-context endpoints. A caller holding `tickets.manage_links`
but lacking `github.view`/`maintenance.view` sees an empty picker for
that link type — an accepted gap for a hypothetical custom role that
pairs those permissions oddly; both seeded roles (`admin`, `engineer`)
hold all three together, so this never bites in practice.

## Consequences

- A ticket's timeline is exactly as complete as the events already
  recorded for its deployment and whatever it's linked to — a ticket
  linked to a GitHub issue whose only event is `github.issue.created`
  (no PR/release ever recorded, or not yet linked) will show a short
  timeline, not a padded-out stepper with placeholder future steps. This
  is intentional: the doc's example list (created → assigned → issue →
  PR → release → deployment → health → resolved) is the *maximum*
  observable shape, not a guaranteed one.
- `GET /tickets/{id}/timeline` re-queries and re-unions on every call
  (no caching/materialization) — fine at this hub's scale (one ticket's
  timeline is a handful of rows across a handful of `WHERE` branches),
  and avoids a second, denormalized copy of data `OperationalEvent`
  already holds.
- A maintenance window that's hard-deleted after being linked to a
  ticket still shows as a link row (`services/ticket_links.
  resolve_link_display` returns "no longer available" rather than
  silently dropping it) — consistent with `OperationalEvent.entity_id`'s
  own "referenced row can no longer exist" tolerance (see that model's
  docstring).
