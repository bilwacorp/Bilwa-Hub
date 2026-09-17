# ADR-004: Deployment Lineage

Status: Accepted. Implemented in migration `016_deployment_lineage.py`.

## Context

`HUB-Expansion.md` Phase 4 asks the deployment model to expose enough
that an operator can understand exactly what is running: Customer,
Application, Environment, Current version, Release, Commit SHA,
Repository, Deployment time, Deployment actor, Deployment source, plus
the health/heartbeat/maintenance-status fields that already existed.
`docs/architecture/target-state.md`'s "Landed" diagram flagged the two
gaps this closes: `CUSTOMER — still just Deployment.client_name` and
`RELEASE ... not yet linked to a deployed version`. See
`docs/architecture/target-state.md` for how this fits the overall shape
and `task-track.md` for what's still open after this phase.

## Decisions

**1. `Customer`/`Application` are additive, not a replacement for
`Deployment.client_name`.** The plan's "DO NOT rewrite the existing
application" rule rules out threading a new required column through
every existing query/notification/UI that already reads `client_name`.
`Deployment.customer_id`/`application_id` are nullable FKs; every
existing code path keeps working unchanged whether or not they're set.
Migration `016` backfills one `Customer` row per distinct `client_name`
and links every existing `Deployment` to it, so the lineage view isn't
empty on day one — but `client_name` itself is untouched and remains the
field every other part of the app reads.

**2. `Application` sits above the Phase 3 `Deployment ↔ GitHubRepository`
edge, without touching it.** ADR-003 built that relationship as a real
M:N specifically so a future grouping layer could extend it rather than
migrate away from a wrong shape. This phase adds `Deployment.application_id`
as a separate, independent link — it does not give `Application` its own
edge to `GitHubRepository`. A deployment's "Repository" field is still
resolved from its own `DeploymentGitHubRepository` mapping (Phase 3),
not from its `Application`. Giving `Application` its own repository set
is a legitimate future extension if BilwaCorp ever has more than one
product line, but nothing in this phase's field list requires it, so it
isn't built speculatively.

**3. `DeploymentRelease` is a historized table, not a mutable "current
version" column on `Deployment`.** Same shape as `DeploymentSnapshot`
(one row per heartbeat) rather than `Deployment.status` (one mutable
column) — a real lineage view benefits from "what did this deployment run
before," and a single overwritten column would throw that away for free.
The most recent row by `deployed_at` is a deployment's "current" release;
`services/lineage.current_releases_map` is the one place that's computed,
batched the same way `_assigned_staff_map` already batches staff lookups.

**4. `DeploymentRelease.repository_id`/`release_id` are plain FK columns,
not `relationship()`s — and `app/models.py` never imports the GitHub
integration package.** ADR-003's own package shape (`app/integrations/
github/`) is meant to be a boundary: other domains link into it by ID,
not by importing its ORM classes. `services/lineage.py` and
`api/routers/deployments.py` resolve the join explicitly (the same
pattern `deployments.py`'s existing `GET .../github` panel already uses
for `GitHubRepository`/`GitHubRelease`), keeping the dependency direction
one-way (GitHub's own models already FK into `deployments`; nothing in
`app/models.py` FKs back the other way at the ORM-relationship level).
This also sidesteps the codebase's existing "never trigger a lazy load
under `AsyncSession`" rule (see `DeploymentActionExecutionOut`'s own
docstring) — there's no relationship to accidentally lazy-load in the
first place.

**5. `DeploymentReleaseSource` is a HUB-owned enum with two members this
phase never populates.** `manual` (staff-entered, `POST .../releases`)
and `heartbeat_inferred` (`services/lineage.infer_release_from_heartbeat`,
wired into `ingest.py`'s heartbeat handler) are the only sources reachable
today. `github_actions`/`ci_cd` exist in the enum now so Phase 5's future
CI/CD webhook integration is a schema-compatible addition — a new source
of rows into the same table — rather than a migration that widens the
enum later. This mirrors ADR-003's own precedent for `GitHubAuthMode.
github_app` (defined, not yet implemented).

**6. Heartbeat inference is best-effort matching, not a hard requirement.**
`infer_release_from_heartbeat` only writes a new `DeploymentRelease` row
when the reported `app_version` differs from the last one recorded for
that deployment (so a heartbeat repeating the same version is a no-op,
same "don't duplicate DeploymentSnapshot's job" reasoning). It tries to
resolve the version against a `GitHubRelease.tag_name` (`'2.8.15'` or
`'v2.8.15'`) on one of the deployment's linked repos; if none match (no
linked repo at all, or no release with that tag), it still records the
version change with `repository_id`/`release_id`/`commit_sha` left NULL.
A version bump is real lineage information even when it can't be
resolved to a GitHub artifact — recording nothing at all would silently
lose that history, and refusing to record anything without a GitHub match
would make this feature useless for any deployment without a configured
GitHub integration (still the common case — Phase 3's integration is
opt-in per org).

**7. `Environment` is a HUB-owned native enum (`production`/`staging`/
`development`/`uat`), defaulting to `production`.** Per `CLAUDE.md`, every
client deployment is its own single-tenant, separately hosted instance —
so `production` is the correct default for effectively every row that
exists today; the other three values exist for the rarer non-client
instance (an internal demo/pre-prod box) that still registers with this
same hub.

**8. New permissions: `customers.view`/`.manage`, `applications.view`/
`.manage`, `deployments.manage_lineage`.** Customer/Application CRUD
follows the `rbac.manage`/`staff.create` tier (admin-config, `engineer`
doesn't get `.manage`) since these are organizational reference data, not
day-to-day fleet actions — but `engineer` does get `.view` for both (same
tier as `github.view`) since it needs to read this data to understand the
deployments it's already scoped to. `deployments.manage_lineage` (setting
a deployment's own customer/application/environment link, and recording
a manual release) is a `deployments`-resource *action* permission instead
— `engineer` keeps it, following migration `011`/`014`'s established
"keeps every action permission it already effectively had" pattern for
this resource (see `assign_staff`, `actions.retry`).

## Consequences

- A heartbeat carrying an `app_version` this deployment hasn't reported
  before now also emits a `deployment.release_recorded` `OperationalEvent`
  in addition to the existing `deployment.heartbeat_received` one — see
  `tests/test_operational_events.py`'s updated assertion. This is
  intentional, not a side effect to work around.
- Phase 5 (CI/CD webhook integration) can insert `DeploymentRelease` rows
  with `source=github_actions`/`ci_cd` directly — the table, enum, and
  `deployed_by`/`deployed_at` shape already match the doc's own worked
  example (`Deployed by: GitHub Actions`) without a further migration.
- `Application` still has no edge to `GitHubRepository` of its own (see
  decision 2) — if BilwaCorp ever needs "every repo behind this product,
  across every deployment of it," that's a follow-on migration, not
  something this phase's tables can already answer.
