"""Canonical OperationalEvent.event_type / source / actor_type / entity_type
string catalog. Plain Python string constants, not a native Postgres enum
— unlike a bounded status column, this catalog is expected to keep
growing with nearly every future HUB-Expansion.md phase (Phase 3's GitHub
events, Phase 5's CI/CD events, Phase 7's maintenance-lifecycle events,
...), and a native enum would need an `ALTER TYPE ... ADD VALUE` migration
for every single new value. Mirrors core/permissions.py's ALL_PERMISSIONS
shape: a Python-side canonical list for a catalog that grows by
convention, not by schema change. Adding a new event type in a later
phase = add a constant here, no migration.

Only event types actually emitted today are defined below — see
HUB-Expansion.md's Phase 1 for the fuller "potential event types" list;
most of those (github.*, deployment.release_*, employee_reference.*)
belong to integrations that don't exist yet and would be dead constants
if added now.
"""

# ── sources (OperationalEvent.source — who/what emitted the event) ───────
SOURCE_HUB = "hub"              # a staff action against the hub's own API
SOURCE_DEPLOYMENT = "deployment"  # an inbound call from a client deployment
SOURCE_ENGINE = "engine"        # the workflow engine itself, not a business router
SOURCE_GITHUB = "github"        # an inbound GitHub webhook, or a sync fetch from the GitHub API

# ── actor types (OperationalEvent.actor_type) ─────────────────────────────
ACTOR_STAFF = "staff_user"
ACTOR_DEPLOYMENT = "deployment"
ACTOR_SYSTEM = "system"
# A GitHub login, not a HUB user — OperationalEvent.actor_id stays NULL for
# these (it's UUID-typed; a GitHub login isn't a HUB id) and the login
# lives in event_metadata instead. See docs/integrations/github.md.
ACTOR_GITHUB_USER = "github_user"

# ── entity types (OperationalEvent.entity_type — the polymorphic target) ──
ENTITY_DEPLOYMENT = "deployment"
ENTITY_TICKET = "ticket"
ENTITY_MAINTENANCE_WINDOW = "maintenance_window"
ENTITY_WORKFLOW_INSTANCE = "workflow_instance"
ENTITY_GITHUB_REPOSITORY = "github_repository"
ENTITY_GITHUB_PULL_REQUEST = "github_pull_request"
ENTITY_GITHUB_ISSUE = "github_issue"
ENTITY_GITHUB_RELEASE = "github_release"
ENTITY_GITHUB_COMMIT = "github_commit"
ENTITY_GITHUB_WEBHOOK_EVENT = "github_webhook_event"

# ── deployment lifecycle ──────────────────────────────────────────────────
DEPLOYMENT_REGISTERED = "deployment.registered"
DEPLOYMENT_HEARTBEAT_RECEIVED = "deployment.heartbeat_received"
DEPLOYMENT_HEALTH_CHECK_COMPLETED = "deployment.health_check_completed"

# ── deployment actions — renew/suspend/change_plan share one gated/
# immediate mechanism (app/approvals/deployment_hooks.py), so all three use
# the same _requested/_executed/_failed shape for internal consistency.
# HUB-Expansion.md's own Phase 1 list gives this full shape only for
# "renew" and single terminal names ("suspended", "plan_changed") for the
# other two — generalized here since the three actions are mechanically
# identical in this codebase.
DEPLOYMENT_RENEW_REQUESTED = "deployment.renew_requested"
DEPLOYMENT_RENEW_EXECUTED = "deployment.renew_executed"
DEPLOYMENT_RENEW_FAILED = "deployment.renew_failed"
DEPLOYMENT_SUSPEND_REQUESTED = "deployment.suspend_requested"
DEPLOYMENT_SUSPEND_EXECUTED = "deployment.suspend_executed"
DEPLOYMENT_SUSPEND_FAILED = "deployment.suspend_failed"
DEPLOYMENT_CHANGE_PLAN_REQUESTED = "deployment.change_plan_requested"
DEPLOYMENT_CHANGE_PLAN_EXECUTED = "deployment.change_plan_executed"
DEPLOYMENT_CHANGE_PLAN_FAILED = "deployment.change_plan_failed"

# extend-expiry and review-request have no approval gate today (see
# app/approvals/deployment_hooks.py — only renew/suspend/change_plan are
# ever gated), so there's no "_requested" variant for these two.
DEPLOYMENT_EXPIRY_EXTENDED = "deployment.expiry_extended"
DEPLOYMENT_EXPIRY_EXTEND_FAILED = "deployment.expiry_extend_failed"
DEPLOYMENT_SUBSCRIPTION_REQUEST_REVIEWED = "deployment.subscription_request_reviewed"
DEPLOYMENT_SUBSCRIPTION_REQUEST_REVIEW_FAILED = "deployment.subscription_request_review_failed"

# ── support tickets ────────────────────────────────────────────────────────
TICKET_CREATED = "ticket.created"
TICKET_UPDATED = "ticket.updated"
TICKET_RESOLVED = "ticket.resolved"

# ── maintenance windows ───────────────────────────────────────────────────
MAINTENANCE_CREATED = "maintenance.created"
MAINTENANCE_UPDATED = "maintenance.updated"
MAINTENANCE_DELETED = "maintenance.deleted"

# ── approvals / workflow engine (app/approvals/hooks.py's fire_if_terminal
# is the single choke point for all four of these — see its docstring) ────
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_APPROVED = "approval.approved"
APPROVAL_REJECTED = "approval.rejected"
APPROVAL_REASSIGNED = "approval.reassigned"
WORKFLOW_CANCELLED = "workflow.cancelled"
# Not in HUB-Expansion.md's example list — InstanceStatus.error (a rule
# that resolved to zero live candidates, see workflow/executor.py) is a
# real terminal state the doc's own catalog omitted.
WORKFLOW_ERRORED = "workflow.errored"

# ── deployment action execution retries (HUB-Expansion.md Phase 12/13 —
# see app/models.py's DeploymentActionExecution) ───────────────────────────
DEPLOYMENT_ACTION_RETRIED = "deployment.action_retried"

# ── GitHub integration (HUB-Expansion.md Phase 3 — see
# app/integrations/github/ and docs/integrations/github.md) ───────────────
GITHUB_WEBHOOK_RECEIVED = "github.webhook_received"
GITHUB_WEBHOOK_FAILED = "github.webhook_failed"
GITHUB_ISSUE_CREATED = "github.issue.created"
GITHUB_ISSUE_UPDATED = "github.issue.updated"
GITHUB_PULL_REQUEST_OPENED = "github.pull_request.opened"
GITHUB_PULL_REQUEST_UPDATED = "github.pull_request.updated"
GITHUB_PULL_REQUEST_MERGED = "github.pull_request.merged"
GITHUB_PULL_REQUEST_CLOSED = "github.pull_request.closed"
GITHUB_COMMIT_PUSHED = "github.commit.pushed"
GITHUB_RELEASE_PUBLISHED = "github.release.published"
GITHUB_REPOSITORY_SYNCED = "github.repository_synced"
GITHUB_REPOSITORY_SYNC_FAILED = "github.repository_sync_failed"
