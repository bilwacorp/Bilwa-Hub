# ADR-003: GitHub Integration

Status: Accepted. Implemented in migration `015_github_integration.py`.

## Context

`HUB-Expansion.md` Phase 3 asks for a GitHub integration domain: HUB
maintains the *operational relationship* between customer/deployment and
GitHub repository/PR/issue/release/commit — GitHub itself stays the
system of record for source and engineering activity. See
`docs/integrations/github.md` for the full design; this ADR records the
decisions worth defending independently.

## Decisions

**1. Personal/fine-grained access token, not a GitHub App — at first.** A
GitHub App needs its own registration flow (manifest, private key,
installation token exchange/caching) with no existing precedent in this
codebase to build on. The phase brief explicitly allows this tradeoff ("if
GitHub App auth would create excessive complexity, document the tradeoff
and use the simplest secure implementation"). `GitHubIntegration.auth_mode`
is still a real column (native enum, `pat`/`github_app`) so a future
GitHub-App mode is a schema-compatible addition, not a rewrite. **Update:**
implemented in migration `022_github_app_auth.py` — see
`docs/adr/ADR-004-github-app-auth.md`. Confirms the prediction below: it
was additive (two new nullable columns, a new module, no changes to any
existing PAT-mode row or call site's behavior).

**2. `Deployment ↔ GitHubRepository` is many-to-many, not one-to-one.**
The brief is explicit: "do not assume one repository = one deployment."
A deployment can pull from more than one repo (separate frontend/
backend); a repo can serve more than one deployment (a shared codebase
across single-tenant deployments). No `Customer`/`Application` table
exists yet (Phase 4) to hang a cleaner hierarchy off of — building this
edge as a genuine M:N join table now, rather than a placeholder 1:1 FK,
means Phase 4 extends a correct relationship instead of migrating away
from a wrong one.

**3. `GitHubPullRequest.state`/`GitHubIssue.state` are plain strings; the
integration's own status columns are native enums.** Extends ADR-001's
rule (`OperationalEvent.event_type`) to this domain: a value sourced from
an external system's vocabulary that HUB doesn't control (GitHub's own
"open"/"closed") stays a string, since a native enum would need a
migration if GitHub's vocabulary ever changes; a value HUB itself defines
and controls (`GitHubIntegration.status`, `GitHubWebhookEvent.status`)
stays a native enum for consistency with every other status column in
this codebase.

**4. GitHub-sourced `OperationalEvent`s never populate `actor_id`.**
`actor_id` is `UUID`-typed; a GitHub login isn't a HUB user id. Rather
than widen that column's type — which would ripple across every existing
Phase 1/12/13 emission site — GitHub events use `actor_type=
"github_user"`, `actor_id=NULL`, and put the login in `event_metadata`.
Narrower, backward-compatible, and consistent with "do not rewrite
existing architecture."

**5. `OperationalEvent.deployment_id` is only set when a repository maps
to exactly one deployment.** Given decision 2 (M:N mapping), a repository
with zero or several mapped deployments has no single correct
`deployment_id` to guess at. Verified in
`tests/test_github_webhooks.py::test_event_gets_deployment_id_only_when_repository_maps_to_exactly_one_deployment`.
The deployment-scoped panel (`GET /deployments/{id}/github`) therefore
never relies on this field for correctness — it joins through
`DeploymentGitHubRepository` directly, the same way `tickets.py`/
`maintenance.py` already join through their own FK rather than trusting
a denormalized column.

**6. One Celery app, not two.** `docker-compose.yml`'s `celery-worker`
command only imports `app.services.notifications.tasks`. Rather than
relocate the shared `celery_app` (riskier — touches a working deploy
config) or run a second broker connection (new infrastructure the brief
says not to add without proof it's needed), that module gained one
bottom-of-file import of `app.integrations.github.tasks`, which registers
GitHub's `@celery_app.task(...)`-decorated functions on the same app
object at worker-boot time. Verified directly: both domains' task names
appear in `celery_app.tasks` after import.

**7. Repository/PR/issue/release browsing is not row-scoped; the
deployment-scoped panel is, via the existing mechanism.** The brief's own
suggested permission list has no `github.view_all` companion to
`github.view` — unlike `deployments.view`/`deployments.view_all` — which
is the signal that fleet-wide GitHub browsing wasn't meant to be scoped
per-customer the way tickets/maintenance are. The one place customer
confidentiality *does* apply (a deployment's own detail page) reuses
`deployments.py`'s `_get_visible_or_404` directly rather than inventing a
parallel scoping mechanism for GitHub specifically.

**8. `sync.py` and `webhooks.py` share one set of upsert functions
(`upsert.py`).** GitHub's webhook payload for `pull_request`/`issues`/
`release` embeds the *same* resource representation the REST API returns
for that resource — one parser per resource type, used by both the
manual-sync path and the webhook-processing path, so "synced" data and
"webhooked" data are parsed identically rather than by two independently-
maintained code paths that could drift.

## Consequences

- Adding GitHub App auth later is additive (new columns + a new code path
  behind `client.py`'s existing token-resolution seam), not a rewrite of
  every call site — decision 1's whole point.
- Phase 4 (`Customer`/`Application`) has a real M:N edge to build
  *upward* from (`Deployment ↔ GitHubRepository`) instead of needing to
  migrate a wrong 1:1 assumption away first.
- Phase 5 (CI/CD) can reuse the exact same package shape
  (`app/integrations/<name>/{models,client,security,webhooks,sync,tasks,
  api,webhook_api}.py`) and the same "register tasks on the one shared
  celery_app via a bottom-of-file import" pattern — see
  `docs/architecture/target-state.md`'s "Integration layer" section.
- `GitHubWebhookEvent` already persists `workflow_run`/`check_run`
  deliveries today (any event type without a handler in `webhooks.py`
  still gets a row, just no domain-row/OperationalEvent side effect) — so
  Phase 5 will have real historical webhook data to backfill from once it
  adds handlers for those event types, rather than starting from zero.
