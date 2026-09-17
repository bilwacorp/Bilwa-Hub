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
   populated 2 ways:  │                (Phase 3)     (Phase 3) (Phase 3,
   heartbeat-version-  │                                        push-sourced only)
   match (Phase 4) AND
   Phase 5's workflow_run
   webhook handler —
   see below)
      │
      ▼
   GITHUB (Phase 3: GitHubIntegration, credentials + webhook,
           per-org; repositories/PRs/issues/releases/commits
           synced + webhook-kept-current. Phase 5: the SAME webhook
           pipeline also handles `workflow_run` — a successful,
           deploy-named workflow run creates a DeploymentRelease row
           via app/integrations/cicd/'s DeploymentProvider abstraction,
           source='github_actions' — see ADR-005)
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
               no draft/scheduled/approval-required lifecycle. Phase 6:
               CAN be a SupportTicketLink target — see SUPPORT below)
      │
      ▼
   SUPPORT (Phase 6: SupportTicketLink — a ticket can link to a GitHub
            issue/PR/release or a maintenance window;
            GET /tickets/{id}/timeline aggregates OperationalEvent rows
            across the ticket + its deployment + everything it's linked
            to, by entity reference — not a shared correlation_id, see
            ADR-006 decision 4)
      │
      ▼
   APPROVAL (Phase 12/13: gates renew/suspend/change_plan, with a real
             execution state machine + retry + idempotency — not yet
             extended to maintenance/other actions, that's Phase 8)
      │
      ▼
    AUDIT (Phase 1: OperationalEvent — GitHub events flow into this same
           table via Phase 3, not a second event system; Phase 4's
           customer/application/lineage changes flow into it too; Phase
           6's ticket timeline reads from it rather than adding a
           second aggregation store)
```

Every `OperationalEvent`-driven action (deployment lifecycle, tickets,
maintenance, approvals, GitHub, lineage) is traceable WHO → WHAT → WHEN →
WHICH DEPLOYMENT → WHICH EXTERNAL SYSTEM → RESULT today, for every domain
that exists. Ticket ↔ GitHub issue (the one gap left after Phase 4) closed
with Phase 6's `SupportTicketLink` + timeline aggregation — see ADR-006.
Phase 2's formal cross-system correlation is effectively subsumed by that
same mechanism for the ticket case (ADR-006 decision 4 explains why a
shared/propagated `correlation_id` doesn't actually work for retroactive
many-to-one linking, and why entity-reference aggregation is used
instead).

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

This shape is proven out once (GitHub) and is the template a future
integration with its OWN credentials/webhook endpoint (Monitoring, or a
non-GitHub CI system) should follow — same credential-encryption reuse
(`services/crypto.py`), same webhook-idempotency-via-unique-constraint
mechanism, same Celery-app reuse via a bottom-of-file import, same
OperationalEvent emission (no second event system per integration).

**Phase 5 (CI/CD) deliberately did NOT create a new `app/integrations/
cicd_github/` package following this shape.** GitHub Actions has no
separate credentials, webhook URL, or signature secret of its own — it's
delivered through the exact same GitHub App webhook Phase 3 already
verifies and persists. Duplicating a whole `models.py`/`security.py`/
`webhook_api.py`/`tasks.py` stack for "GitHub, but CI/CD flavored" would
mean a second signature-verification path and a second webhook endpoint
GitHub would need configuring against, for events that already arrive at
Phase 3's one. Phase 5 instead added a small `app/integrations/cicd/`
package holding ONLY the provider abstraction (`provider.py`'s
`DeploymentProvider`/`DeploymentEventData`, `github_actions.py`'s
`GitHubActionsProvider`) and one new case in Phase 3's existing
`webhooks.py` `_HANDLERS` dict (`workflow_run`) — see ADR-005 for the
full reasoning. A CI system that genuinely has its own credentials/
webhook (unlike GitHub Actions here) should still follow the full
package shape above.

## Still not started (unchanged from the Phase 0 audit unless noted)

- Phase 7 (maintenance lifecycle — still create/update/delete only),
  Phase 8 (generic operational approvals beyond deployment actions),
  Phase 9 (unified timeline UI — Phase 6's per-ticket timeline is the
  first curated aggregation over `OperationalEvent`, but nothing
  fleet-wide/per-deployment yet), Phase 10 (dashboard), Phase 11
  (Integration Center UI — Phase 3 already keeps the health fields
  Phase 11 will read).
- Phase 14/15/19 (RBAC/security/audit) — largely satisfied incrementally
  by 1/3/12/13's own permission and audit additions; a dedicated pass
  hasn't been done. See `docs/security/integration-security.md` for the
  Phase 3-specific security review.
