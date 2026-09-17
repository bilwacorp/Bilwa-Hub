# ADR-005: CI/CD Webhook Integration

Status: Accepted. Implemented in `app/integrations/cicd/` +
`app/integrations/github/webhooks.py`'s `workflow_run` handler. No
migration — reuses Phase 4's `DeploymentRelease` table and
`DeploymentReleaseSource.github_actions` enum value, both already added
by migration `016` in anticipation of this phase (see ADR-004 decision 5
and its "Consequences" section).

## Context

`HUB-Expansion.md` Phase 5 asks for "an integration abstraction rather
than coupling HUB to one CI provider," with incoming events
authenticated, validated, idempotent, persisted, processed
asynchronously, and linked to the correct deployment. Phase 3 already
built exactly that machinery for GitHub webhooks in general
(`webhook_api.py`'s HMAC verification + unique `delivery_id` + Celery
dispatch); Phase 5's job is deciding *which* GitHub webhook event
represents "a deployment happened" and turning it into a
`DeploymentRelease` row, behind an interface that doesn't hard-code
"GitHub Actions" into the recording logic itself.

## Decisions

**1. `workflow_run`, not GitHub's separate Deployments API
(`deployment_status`).** GitHub ships two independent, unrelated
mechanisms that both plausibly mean "a deploy happened": a plain Actions
workflow completing, and the Deployments API's `deployment`/
`deployment_status` events (which require a repo to explicitly create a
`Deployment` object via that API — typically only repos using GitHub
Environments do this). `task-track.md`'s own Phase 4-era note already
flagged `workflow_run` as the event Phase 3 was persisting-but-not-
processing, and this hub's actual client deployments are Dokploy-based
(see `CLAUDE.md`), not deployed through GitHub's Deployments API — so
`workflow_run` is the signal a real client repo here will actually emit,
where `deployment_status` might never fire at all. `deployment_status`
support can be added later as a second, independent event handler if a
repo that uses it shows up — nothing in this design precludes it.

**2. "Is this workflow run a deploy?" is a name/path heuristic, not a
certainty.** GitHub's API has no first-class "this run was a deployment"
flag on `workflow_run`. `app/integrations/cicd/github_actions.py`'s
`_looks_like_deploy` checks whether the workflow's `name` or file `path`
contains "deploy" (case-insensitive) — matching the overwhelmingly common
convention (`Deploy`, `deploy.yml`, `deploy-production.yml`). This is a
deliberate, documented v1 scope cut: a repo whose deploy workflow is
named something else is silently not picked up, and Phase 4's manual
`POST /deployments/{id}/releases` entry point remains the correction path
for exactly that case — same "best-effort, not a hard requirement" shape
Phase 4 already established for heartbeat-based version matching (ADR-004
decision 6).

**3. `DeploymentProvider` is a payload parser, not a live API client.**
`receive_deployment_event(payload) -> Optional[DeploymentEventData]` is
the only method every provider must implement; `get_commit`/
`get_deployment_status` are trivial field reads off the already-parsed
event. There is deliberately no `get_release()` that calls out to GitHub's
REST API — release resolution reuses the same tag-matching helper Phase
4's heartbeat inference already has (`services/lineage._match_release_by_tag`,
factored out of that function for this reuse) against data Phase 3's
sync/webhooks already persisted. Per `HUB-Expansion.md` rule 5 ("do not
introduce unnecessary infrastructure"), there's no case yet where
answering these questions needs a fresh outbound call HUB doesn't already
have a cheaper path to.

**4. A `workflow_run` whose repository maps to zero or more than one
`Deployment` is skipped entirely, not recorded with `deployment_id=NULL`.**
Every other GitHub event handler (`_deployment_id_for`) leaves
`OperationalEvent.deployment_id` `NULL` in the ambiguous case and still
records the event — that's fine there because an `OperationalEvent` can
describe something with no single deployment owner. A `DeploymentRelease`
row cannot: `deployment_id` is `NOT NULL` (it's meaningless to say "this
version is now running" without saying *where*), so the only correct
behavior for an ambiguous or unmapped repo is to record nothing. The
webhook itself still ends up `processed`, and the full payload is still
preserved in `github_webhook_events.payload` — no information is lost,
just no `DeploymentRelease` side effect fires.

**5. Every CI/CD deployment event is recorded unconditionally — no
"same version as last time" dedup.** This is the one place Phase 5's
recording function (`services/lineage.record_provider_deployment_event`)
differs from Phase 4's heartbeat path
(`infer_release_from_heartbeat`, which skips a heartbeat reporting the
same version already on file). A CI/CD event is itself already the
authoritative "a deploy just happened" signal — redeploying the exact
same version is still a real, distinct deploy occurrence worth a row
(e.g. a rollback-and-redeploy), and the webhook's own `delivery_id`
uniqueness is the actual idempotency guard against a duplicate *delivery*
of the *same* event, which is the failure mode that matters here.

**6. `version` resolution (a semver-looking `head_branch`) is computed
by the caller (`webhooks.py`'s handler), not inside the provider or the
recording function.** `head_branch` is often a plain branch name
("main") with no version meaning at all; `app/integrations/cicd/
github_actions.py`'s `looks_like_version()` is a GitHub-Actions-specific
heuristic (a provider-specific detail) applied before calling the
provider-agnostic `record_provider_deployment_event`, keeping that
function's contract the same regardless of which provider produced the
event.

## Consequences

- No new database migration — this phase is entirely new code wired
  into infrastructure Phase 3/4 already built. `DeploymentReleaseSource
  .ci_cd` (the generic non-GitHub-Actions value) remains unused; a future
  non-GitHub CI provider would implement `DeploymentProvider` and use
  that value instead of `github_actions`.
- `check_run` (job/step-level granularity, not deployment-level) remains
  unhandled, same as before this phase — it was never a Phase 5
  candidate, only `workflow_run` was.
- A future `deployment_status` handler (decision 1) would be a second,
  independent addition to `_HANDLERS` plus a second `DeploymentProvider`-
  shaped parse in `github_actions.py` (or a new provider file) — it does
  not require touching `record_provider_deployment_event` or the
  `DeploymentProvider` interface itself.
