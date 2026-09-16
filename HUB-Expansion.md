# BilwaCorp Fleet Hub — Engineering Evolution Prompt

You are working on an existing production-oriented application called **BilwaCorp Fleet Hub**.

Your job is NOT to rewrite the application from scratch.

Your job is to inspect the existing codebase, understand its architecture, preserve existing working behavior, and incrementally evolve it into a **Software Operations Control Plane** for BilwaCorp.

The application manages a fleet of independently hosted **PoultryOS-CBP single-tenant customer deployments**.

The existing application already contains:

* Deployment registration
* Deployment heartbeat
* Deployment health checks
* Subscription information
* Usage information
* Renewal
* Suspension
* Plan changes
* Expiry extension
* Support tickets
* Maintenance windows
* Staff accounts
* Casbin RBAC
* Row-level deployment visibility
* Email notifications
* WhatsApp notifications
* Celery + Redis background processing
* Audit/history concepts
* BPMN workflow definitions
* BPMN workflow versions
* Workflow instances
* Approval tasks
* Approval inbox
* Sandboxed workflow rule evaluation
* Deployment action credentials
* Deployment API credentials

Current stack:

Backend:

* FastAPI
* Python
* SQLAlchemy 2.x async
* asyncpg
* Alembic
* Pydantic v2
* Casbin

Async:

* Celery
* Redis

Workflow:

* SpiffWorkflow
* BPMN
* bpmn-js
* sandboxed rule engine

Frontend:

* React 18
* TypeScript
* Vite
* React Router
* TanStack Query
* Zustand
* React Hook Form
* Tailwind CSS

Infrastructure:

* Docker Compose
* nginx
* Dokploy
* PostgreSQL

---

# PRIMARY OBJECTIVE

Evolve Fleet Hub from:

"deployment management + support + maintenance"

into:

"the operational control plane that connects customers, deployments, support, engineering changes, maintenance, releases, approvals, and operational health."

The HUB should become the place where BilwaCorp operations staff can answer:

* What deployments do we have?
* Which customers are affected?
* What version is running?
* What changed?
* Who deployed it?
* Which GitHub PR produced it?
* Is the deployment healthy?
* Are there open support issues?
* Is maintenance scheduled?
* Who approved the maintenance?
* Who approved a production-impacting action?
* What incidents are active?
* Which deployments are overdue for attention?
* Which actions are waiting for approval?
* What happened to a particular deployment over time?
* What happened to a particular support ticket?
* What code/release/deployment is associated with a ticket?
* Who performed a particular operational action?

Do NOT turn HUB into an accounting system, full HRIS, or GitHub replacement.

Instead, integrate with specialized systems.

---

# ARCHITECTURAL PRINCIPLE

HUB should become the operational source of truth for:

* Customer deployment state
* Fleet state
* Operational actions
* Maintenance
* Support
* Operational approvals
* Deployment history
* Integration events
* Operational audit history

External systems remain authoritative for their own domains.

Examples:

GitHub:

* repositories
* commits
* pull requests
* releases

CI/CD:

* builds
* deployment execution

HR:

* employee identity and employment status

Billing/accounting:

* financial records

Monitoring:

* detailed metrics/logs/traces

HUB stores references and operational state rather than attempting to replace these systems.

---

# NON-NEGOTIABLE RULES

## 1. DO NOT rewrite the existing application.

First inspect the repository.

Understand:

* directory structure
* models
* routers
* services
* dependencies
* migrations
* frontend routes
* frontend components
* permission catalog
* Casbin enforcement
* workflow architecture
* notification architecture
* Celery architecture
* deployment client
* authentication
* audit/history implementation

Before changing anything, produce an architectural assessment.

## 2. Preserve existing behavior.

Existing working flows must continue to work unless there is a documented reason to change them.

Especially preserve:

* deployment registration
* heartbeat
* health check
* renewal
* suspension
* plan changes
* expiry extension
* ticket creation
* maintenance
* RBAC
* row-level visibility
* workflow approvals
* notification behavior

## 3. Database-first integrity.

Never rely on frontend checks for authorization.

All important authorization must remain server-side.

All important state transitions must be persisted.

## 4. Every important operational action must be traceable.

For an action, we should be able to determine:

* who requested it
* who approved it
* what object it affected
* when it happened
* what the previous state was
* what the new state was
* what external system was called
* external response/status
* whether it succeeded
* whether it failed
* whether retry occurred

## 5. Do not introduce unnecessary infrastructure.

Do not introduce Kafka, Kubernetes, Elasticsearch, Temporal, or another large distributed system unless the existing architecture genuinely requires it.

Existing:

FastAPI + PostgreSQL + Redis + Celery

is sufficient for the first implementation.

---

# TARGET ARCHITECTURE

The target architecture should look conceptually like:

```
                ┌─────────────────────┐
                │     BilwaCorp       │
                │      Fleet HUB      │
                │                     │
                │ Deployments         │
                │ Customers           │
                │ Support             │
                │ Maintenance         │
                │ Approvals           │
                │ Releases            │
                │ Integrations        │
                │ Audit               │
                │ Operational Health  │
                └──────────┬──────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
       GitHub            CI/CD          Monitoring
          │                │                │
          └────────────────┼────────────────┘
                           │
                     Integration Layer
                           │
                           ▼
                   PoultryOS-CBP Fleet
```

The architecture should remain modular.

---

# PHASE 0 — REPOSITORY AUDIT

Before implementing anything:

1. Inspect the repository.
2. Identify backend modules.
3. Identify frontend modules.
4. Identify all database models.
5. Identify all Alembic migrations.
6. Identify Casbin permissions.
7. Identify workflow models and services.
8. Identify notification services.
9. Identify deployment API client.
10. Identify existing audit/event/history mechanisms.
11. Identify all existing tests.
12. Identify technical debt and known bugs.

Create:

docs/architecture/current-state.md

Include:

* current architecture
* data flow
* authentication flow
* authorization flow
* deployment communication
* workflow execution
* notification flow
* current audit model
* integration opportunities
* risks

DO NOT implement the new architecture until this audit is complete.

---

# PHASE 1 — OPERATIONAL EVENT MODEL

Introduce a generic internal event model.

The purpose is to create a consistent operational history without replacing domain tables.

Possible concept:

OperationalEvent

Fields should include approximately:

* id
* event_type
* source
* actor_type
* actor_id
* entity_type
* entity_id
* deployment_id nullable
* customer_id nullable
* correlation_id
* causation_id nullable
* timestamp
* status
* metadata JSONB

Potential event types:

employee_reference.created
employee_reference.updated

deployment.registered
deployment.heartbeat_received
deployment.health_check_started
deployment.health_check_completed
deployment.health_changed

deployment.renew_requested
deployment.renew_approved
deployment.renew_rejected
deployment.renew_executed
deployment.renew_failed

deployment.suspended
deployment.restored
deployment.plan_changed

maintenance.created
maintenance.approved
maintenance.started
maintenance.completed
maintenance.failed

ticket.created
ticket.updated
ticket.escalated
ticket.resolved

workflow.started
workflow.completed
workflow.cancelled

approval.requested
approval.approved
approval.rejected
approval.reassigned

github.repository_linked
github.pull_request_linked
github.release_linked

deployment.release_started
deployment.release_completed
deployment.release_failed

integration.received
integration.failed

Do not over-engineer this.

The event model is primarily for:

* traceability
* correlation
* operational timeline
* integration
* auditing

---

# PHASE 2 — CORRELATION IDs

Introduce correlation IDs for multi-step operations.

Example:

Support ticket:

SUP-1024

creates engineering issue:

GH-824

creates PR:

PR-832

creates release:

v2.8.15

creates deployment:

DEP-901

All should be traceable through a common correlation chain.

Support:

SUP-1024
↓
GH-824
↓
PR-832
↓
Release v2.8.15
↓
Deployment DEP-901
↓
Health verification

Implement this without forcing every object into one table.

Use explicit references plus correlation IDs.

---

# PHASE 3 — GITHUB INTEGRATION

Build a GitHub integration module.

Do NOT make HUB a GitHub replacement.

HUB should store:

* GitHub organization
* repository
* repository ID
* URL
* linked product/application
* optional deployment mapping

Add support for:

* repository synchronization
* pull request references
* release references
* commit references
* webhook ingestion

Start with read/reference capabilities.

Potential model:

GitHubRepository

* id
* external_id
* organization
* name
* full_name
* url
* default_branch
* active

GitHubReleaseReference

* id
* repository_id
* external_id
* tag
* commit_sha
* published_at
* url

GitHubPullRequestReference

* id
* repository_id
* external_id
* number
* title
* state
* author
* merged_at
* url

Deployment should optionally reference:

* repository
* release
* commit SHA

---

# PHASE 4 — DEPLOYMENT LINEAGE

Upgrade the deployment model so an operator can understand exactly what is running.

A deployment should expose:

Customer
Application
Environment
Current version
Release
Commit SHA
Repository
Deployment time
Deployment actor
Deployment source
Health status
Last heartbeat
Maintenance status

Example:

CLIENT ABC

Version:
2.8.15

Repository:
bilwacorp/poultryos-cbp

Release:
v2.8.15

Commit:
83ad92f

Deployed:
2026-09-16 14:42

Deployed by:
GitHub Actions

Health:
Healthy

Last heartbeat:
2 minutes ago

---

# PHASE 5 — CI/CD WEBHOOK INTEGRATION

Create an integration abstraction rather than coupling HUB to one CI provider.

Example interface:

DeploymentProvider

Methods conceptually:

* receive_deployment_event()
* get_deployment_status()
* get_release()
* get_commit()

Initial implementation can support GitHub Actions or the current deployment infrastructure.

Incoming events should be:

1. authenticated
2. validated
3. idempotent
4. persisted
5. processed asynchronously where appropriate
6. linked to the correct deployment

Never blindly trust webhook payloads.

Implement webhook signature verification where supported.

---

# PHASE 6 — SUPPORT ↔ ENGINEERING LINK

This is a major feature.

Allow a support ticket to link to:

* GitHub issue
* GitHub PR
* release
* deployment
* maintenance
* incident

Example:

SUP-1842

Client:
ABC Farms

Problem:
Feed calculation incorrect

Engineering issue:
GitHub #829

PR:
GitHub #841

Release:
v2.8.15

Affected deployment:
ABC-001

Status:
Resolved

The ticket timeline should show:

Ticket created
↓
Assigned
↓
Engineering issue created
↓
PR merged
↓
Release created
↓
Deployment updated
↓
Health verified
↓
Ticket resolved

Do not automatically close tickets without explicit business rules.

---

# PHASE 7 — MAINTENANCE LIFECYCLE

Expand maintenance into a proper operational lifecycle.

Maintenance should support:

* draft
* scheduled
* approval required
* approved
* notification
* active
* completed
* failed
* cancelled

Maintenance can affect one or multiple deployments.

Store:

* affected deployments
* reason
* severity
* start time
* end time
* expected impact
* actual impact
* created by
* approved by
* execution status

Severity levels:

NOTICE
READ_ONLY
LOCKOUT

Existing behavior must remain compatible.

---

# PHASE 8 — GENERIC OPERATIONAL APPROVALS

The existing SpiffWorkflow engine is valuable.

Do NOT replace it.

Expand its use carefully.

The workflow engine should support operational workflows such as:

* subscription renewal
* suspension
* plan change
* production maintenance
* high-risk deployment
* emergency operational action

The workflow engine should remain generic.

Business services should only need to:

1. request approval
2. provide context
3. register completion callback

The workflow engine determines:

* workflow
* rules
* approvers
* tasks
* state
* history

Never bypass workflow approval by trusting frontend state.

---

# PHASE 9 — OPERATIONAL TIMELINE

Create a unified timeline for deployments.

Example:

CLIENT ABC

Timeline:

Sep 16 14:42
Production deployment completed

Sep 16 14:35
Production deployment approved

Sep 16 14:31
Release v2.8.15 created

Sep 16 13:50
PR #841 merged

Sep 16 13:10
Support ticket SUP-1842 linked

Sep 16 12:00
Heartbeat received

Sep 15 22:00
Maintenance completed

The timeline should aggregate events from HUB's own event model and linked integration records.

Do not duplicate entire external systems.

---

# PHASE 10 — OPERATIONS DASHBOARD

Build an executive/operations dashboard.

The dashboard should answer:

## Fleet

Total deployments
Healthy
Warning
Offline
Unknown

## Deployments

Recently deployed
Recently failed
Outdated versions
Missing heartbeat
High-risk deployments

## Support

Open tickets
Unassigned tickets
Escalated tickets
Tickets awaiting engineering

## Maintenance

Upcoming maintenance
Active maintenance
Failed maintenance

## Approvals

Pending approvals
Overdue approvals
Recently approved
Recently rejected

## Integrations

GitHub webhook failures
Deployment callback failures
Notification failures

## Operational attention

Create an "Attention Required" section.

Examples:

* Deployment heartbeat overdue
* Deployment failed to upgrade
* Approval waiting > X hours
* Maintenance starts soon
* Ticket has no owner
* External integration failing
* Deployment version differs from expected release
* Approved action failed to execute

This is more important than adding dozens of charts.

---

# PHASE 11 — INTEGRATION CENTER

Create an admin page:

Settings → Integrations

Initial integrations:

GitHub
CI/CD
Email
WhatsApp
Monitoring

Each integration should show:

Status
Connected/disconnected
Last successful sync
Last error
Last webhook
Configuration
Test connection

Do not expose secrets.

Credentials must be encrypted or stored using the existing secure secret approach.

Never log credentials.

---

# PHASE 12 — RETRY AND FAILURE HANDLING

The existing approval flow currently has a known weakness:

If an approved deployment action fails, the workflow can complete while the actual deployment action fails.

Fix this.

The correct lifecycle should distinguish:

APPROVED

from

EXECUTION_PENDING

from

EXECUTING

from

EXECUTED

from

EXECUTION_FAILED

Example:

Approval:
APPROVED

Deployment action:
FAILED

The operator should see:

"Approved but execution failed."

Provide:

* retry
* failure reason
* attempt count
* last attempted timestamp
* operator
* external response
* retry history

Retry must be idempotent.

Never blindly repeat money-moving actions.

For financial actions such as renewal, use idempotency keys.

---

# PHASE 13 — IDEMPOTENCY

Every external action should support idempotency.

Example:

renewal request:

idempotency_key =
hub-action-{approval_instance_id}

If the same action is retried, the deployment must not accidentally renew twice.

Apply this principle to:

* renew
* suspend
* plan change
* maintenance commands
* deployment actions

Where the remote deployment API supports idempotency, use it.

Where it does not, design a local action state machine that prevents unsafe duplicate execution.

---

# PHASE 14 — RBAC

Preserve the existing Casbin architecture.

Add permissions only where required.

Potential new permissions:

integrations.view
integrations.manage
github.view
github.manage
releases.view
deployments.view_lineage
deployments.view_timeline
operations.view_dashboard
events.view
events.view_all
actions.retry
maintenance.approve

Do NOT create broad permissions like:

system.manage

unless absolutely necessary.

Continue following the existing principle:

One permission should protect one meaningful action.

Row-level visibility must continue to apply to deployment-related data.

---

# PHASE 15 — SECURITY

Review the complete system for:

* authentication
* authorization
* webhook verification
* secret storage
* secret exposure
* API credentials
* action keys
* replay attacks
* CSRF
* session security
* rate limiting
* audit integrity
* IDOR
* row-level access
* unsafe workflow expressions
* SSRF through deployment URLs
* arbitrary outbound requests
* webhook spoofing

Especially review outbound calls from HUB to customer deployments.

Deployment URLs and callback targets must not become an SSRF vulnerability.

Validate:

* allowed protocols
* URL format
* network restrictions where practical
* authentication
* timeouts
* response size
* redirects
* retries

Do not weaken the existing two-credential design.

---

# PHASE 16 — OBSERVABILITY

Do not turn HUB into a full observability platform.

Instead, provide a summarized operational health model.

For every deployment:

* last heartbeat
* heartbeat age
* API health
* application version
* last deployment
* last deployment result
* current maintenance state

Allow external monitoring integrations later.

The dashboard should link to detailed monitoring rather than reproduce Grafana/Sentry/etc.

---

# PHASE 17 — FRONTEND UX

The frontend should be organized around operational workflows.

Recommended navigation:

Dashboard

Fleet
├── Deployments
├── Customers
├── Releases
└── Health

Support
├── Tickets
└── Escalations

Maintenance
├── Calendar
├── Active
└── History

Approvals
├── My Approvals
├── All Approvals
└── History

Engineering
├── Repositories
├── Pull Requests
└── Releases

Integrations

Workflows

Notifications

Staff

Roles & Permissions

Audit / Events

Settings

Do not add pages merely because a database table exists.

Each page should answer an operational question.

---

# PHASE 18 — DEPLOYMENT DETAIL PAGE

The deployment detail page should become the most useful page in the application.

Suggested structure:

HEADER

Client ABC
Production
Healthy

Version 2.8.15

Actions:
Renew
Suspend
Change Plan
Health Check
Schedule Maintenance

Then:

## Overview

Subscription
Plan
Expiry
Usage
Heartbeat
Health

## Software

Repository
Release
Commit
Last deployment
Deployment actor

## Support

Open tickets
Recent tickets

## Maintenance

Upcoming
Active
History

## Approvals

Pending
Recent

## Timeline

Unified operational history

## Technical

Deployment URL
API status
Last heartbeat
Integration status

Respect RBAC and row-level visibility everywhere.

---

# PHASE 19 — AUDIT REQUIREMENTS

For sensitive actions, audit:

* actor
* action
* resource
* resource ID
* deployment
* timestamp
* IP where appropriate
* correlation ID
* approval instance
* previous state
* new state
* outcome
* failure reason

Audit records should not be casually editable or deletable.

Do not allow normal administrators to silently erase audit history.

---

# PHASE 20 — TESTING

Before considering any phase complete, test:

Backend:

* unit tests
* API tests
* permission tests
* row-level scope tests
* workflow tests
* integration tests
* idempotency tests
* webhook authentication tests

Frontend:

* type checking
* build
* critical workflow tests

End-to-end:

1. Register deployment
2. Receive heartbeat
3. Create support ticket
4. Link ticket to GitHub issue
5. Create PR reference
6. Merge PR
7. Receive release/deployment event
8. Update deployment lineage
9. Create maintenance
10. Request approval
11. Approve
12. Execute
13. Simulate failure
14. Retry
15. Verify audit history
16. Verify timeline

Also test unauthorized access using IDs belonging to another deployment.

Expected behavior:

404 or equivalent non-disclosing response.

---

# IMPLEMENTATION STRATEGY

Work incrementally.

For each phase:

1. Inspect existing code.
2. Write a short implementation plan.
3. Identify affected files.
4. Implement backend.
5. Implement migrations.
6. Implement frontend.
7. Add tests.
8. Run migrations.
9. Run backend tests.
10. Run frontend typecheck/build.
11. Run relevant E2E tests.
12. Review for regressions.
13. Summarize changes.

Do not modify unrelated code.

Do not perform large refactors unless required.

---

# IMPORTANT EXISTING ISSUE

The repository currently has a frontend problem where some 422 validation responses are rendered directly into a toast and can cause the page to crash.

The known affected pattern exists on approximately nine pages.

Before adding major new frontend functionality:

1. Identify the shared error-handling pattern.
2. Fix it centrally if possible.
3. Make error rendering safe.
4. Do not render arbitrary objects directly.
5. Show useful human-readable validation errors.

Do not blindly duplicate error handling on every page.

---

# DOCUMENTATION

Maintain:

docs/architecture/current-state.md
docs/architecture/target-state.md
docs/integrations/github.md
docs/integrations/deployment-provider.md
docs/operations/deployment-lineage.md
docs/operations/support-engineering-flow.md
docs/operations/maintenance.md
docs/security/integration-security.md
docs/workflows/approval-engine.md

Also maintain an ADR directory for major architectural decisions.

Examples:

ADR-001 Operational Event Model
ADR-002 GitHub Integration
ADR-003 Deployment Lineage
ADR-004 External Integration Architecture
ADR-005 Idempotent Operational Actions

---

# CODING PRINCIPLES

Prefer:

* small services
* explicit interfaces
* typed schemas
* database constraints
* transaction boundaries
* idempotency
* explicit state machines
* server-side authorization
* asynchronous external calls
* structured errors
* structured logs
* deterministic workflows

Avoid:

* giant service classes
* circular dependencies
* direct cross-module database manipulation
* frontend-only authorization
* hidden side effects
* arbitrary eval
* secrets in logs
* unbounded retries
* synchronous external calls inside critical request transactions
* database coupling between external applications

---

# FINAL TARGET

At the end of this evolution, an operator should be able to open HUB and understand:

```
                CUSTOMER
                   │
                   ▼
             DEPLOYMENT
                   │
      ┌────────────┼─────────────┐
      ▼            ▼             ▼
   VERSION       HEALTH        BILLING
      │            │             │
      ▼            ▼             ▼
   RELEASE      HEARTBEAT      PLAN
      │
      ▼
   GITHUB
      │
   PR / COMMIT
      │
      ▼
  DEPLOYMENT
      │
      ▼
  MAINTENANCE
      │
      ▼
   SUPPORT
      │
      ▼
   APPROVAL
      │
      ▼
    AUDIT
```

And trace any important action from:

WHO
→ WHAT
→ WHY
→ WHICH CUSTOMER
→ WHICH DEPLOYMENT
→ WHICH CODE
→ WHICH APPROVAL
→ WHICH EXTERNAL SYSTEM
→ RESULT
→ WHEN

The purpose of this system is not to collect data for the sake of collecting data.

The purpose is:

**"No operational action should disappear into a black box."**

Start by auditing the existing repository.

Do not start coding until you understand the current architecture and can explain what already exists.
