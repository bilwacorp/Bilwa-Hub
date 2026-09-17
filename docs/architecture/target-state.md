# Target Architecture

Companion to `docs/architecture/current-state.md` (the Phase 0 snapshot,
frozen at that point in time — read it for what existed before any
`HUB-Expansion.md` work landed). This file tracks where the architecture
is heading and what's actually landed so far, updated as later phases
complete rather than frozen like the Phase 0 snapshot.

## Landed

```
                CUSTOMER (Phase 4: Customer table, additive — Deployment
                   │      .client_name is untouched, customer_id is a
                   │      nullable FK; migration 016 backfilled one
                   │      Customer per distinct client_name)
                   ▼
             DEPLOYMENT ────────────────────────────┐
                   │                                │
      ┌────────────┼─────────────┐                  │
      ▼            ▼             ▼                  ▼
   VERSION       HEALTH        BILLING      GITHUB REPOSITORY (Phase 3, M:N —
      │            │             │           a deployment can map to >1 repo,
      ▼            ▼             ▼           a repo can serve >1 deployment)
   RELEASE      HEARTBEAT      PLAN                  │
  (Phase 4:         │                     ┌──────────┼──────────┐
   DeploymentRelease│                     ▼          ▼          ▼
   — historized,     │                PULL REQUEST  ISSUE     COMMIT
   links a version   │                (Phase 3)     (Phase 3) (Phase 3,
   to a GitHubRelease│                                          push-sourced only)
   /commit when a     │
   heartbeat's version │
   matches a linked repo's
   release tag, else just
   records the version
   change itself)
      │
      ▼
   GITHUB (Phase 3: GitHubIntegration, credentials + webhook,
           per-org; repositories/PRs/issues/releases/commits
           synced + webhook-kept-current)
      │
   PR / COMMIT (Phase 3 — not yet linked upstream to a ticket)
      │
      ▼
  DEPLOYMENT ── APPLICATION (Phase 4: Application table, a nullable
      │          Deployment.application_id — grouping only; no edge of
      │          its own to GitHubRepository, see ADR-004 decision 2)
      │
      │          ENVIRONMENT (Phase 4: Deployment.environment, native
      │          enum, defaults 'production')
      ▼
  MAINTENANCE (Phase 7 not started — still create/update/delete only,
               no draft/scheduled/approval-required lifecycle)
      │
      ▼
   SUPPORT (tickets exist; not yet linked to a GitHub issue — Phase 6)
      │
      ▼
   APPROVAL (Phase 12/13: gates renew/suspend/change_plan, with a real
             execution state machine + retry + idempotency — not yet
             extended to maintenance/other actions, that's Phase 8)
      │
      ▼
    AUDIT (Phase 1: OperationalEvent — GitHub events flow into this same
           table via Phase 3, not a second event system; Phase 4's
           customer/application/lineage changes flow into it too)
```

Every `OperationalEvent`-driven action (deployment lifecycle, tickets,
maintenance, approvals, GitHub, lineage) is traceable WHO → WHAT → WHEN →
WHICH DEPLOYMENT → WHICH EXTERNAL SYSTEM → RESULT today, for the domains
that exist. The one gap left in that chain is the one Phase 4 didn't
close: ticket ↔ GitHub issue (Phase 6) — WHY (the business reason an
action was taken) still lives only in a ticket's own free-text
description or a PR's title, not as a structured link. Release ↔ deployed
version (previously the other gap) closed with Phase 4's
`DeploymentRelease` table — see ADR-004.

## Integration layer (Phase 3's shape, reused by future integrations)

```
          ┌────────────────┐
          │  Fleet HUB      │
          │  (FastAPI)      │
          └───────┬─────────┘
                  │
         app/integrations/<name>/
          ├── models.py    (own tables, FK into app/models.py's
          │                 Deployment where a real M:N/1:N relationship
          │                 exists — never a copy of core models)
          ├── client.py    (httpx wrapper, <Name>ApiError/
          │                 <Name>ConnectionError/<Name>RateLimitError —
          │                 same shape as services/deployment_client.py's
          │                 DeploymentCallError)
          ├── security.py  (webhook signature verification, if the
          │                 integration has webhooks)
          ├── webhooks.py  (payload -> local rows + OperationalEvent,
          │                 called only from tasks.py)
          ├── sync.py      (manual/initial sync, shares parsers with
          │                 webhooks.py where payload shapes overlap)
          ├── tasks.py     (Celery tasks registered on the ONE shared
          │                 celery_app from services/notifications/
          │                 tasks.py — see that module's bottom-of-file
          │                 import)
          ├── api.py       (Casbin-gated management/browsing router)
          └── webhook_api.py (public, signature-authenticated router —
                              same "machine-authenticated, not Casbin"
                              shape as register.py/ingest.py)
                  │
                  ▼
          EXTERNAL SYSTEM (GitHub today; CI/CD — Phase 5 — and
                            Monitoring — mentioned in HUB-Expansion.md's
                            "Examples" section, not yet scheduled — would
                            follow this same package shape)
```

This shape is now proven out once (GitHub) and is the template Phase 5's
CI/CD integration should follow rather than inventing its own — same
credential-encryption reuse (`services/crypto.py`), same webhook-
idempotency-via-unique-constraint mechanism, same Celery-app reuse via a
bottom-of-file import, same OperationalEvent emission (no second event
system per integration).

## Still not started (unchanged from the Phase 0 audit unless noted)

- Phase 2 (formal cross-system correlation) — partially exercised: Phase
  1/12/13's `correlation_id` threading and Phase 3's per-webhook-delivery
  `correlation_id` both work today for their own chains, but nothing yet
  stitches a *support ticket's* correlation_id to a *GitHub issue's* —
  that needs Phase 6's ticket ↔ issue link to exist first.
- Phase 5 (CI/CD) — `GitHubWebhookEvent` already persists
  `workflow_run`/`check_run` deliveries (any event type HUB doesn't parse
  survives as an unprocessed row, per Phase 3's design), so Phase 5 can
  start from real historical data instead of only new events once it
  lands.
- Phase 6 (support ↔ engineering link), Phase 7 (maintenance lifecycle),
  Phase 8 (generic operational approvals beyond deployment actions),
  Phase 9 (unified timeline UI), Phase 10 (dashboard), Phase 11
  (Integration Center UI — Phase 3 already keeps the health fields
  Phase 11 will read).
- Phase 14/15/19 (RBAC/security/audit) — largely satisfied incrementally
  by 1/3/12/13's own permission and audit additions; a dedicated pass
  hasn't been done. See `docs/security/integration-security.md` for the
  Phase 3-specific security review.
