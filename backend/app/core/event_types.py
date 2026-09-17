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
ENTITY_GITHUB_INTEGRATION = "github_integration"
ENTITY_CUSTOMER = "customer"
ENTITY_APPLICATION = "application"
ENTITY_DEPLOYMENT_RELEASE = "deployment_release"
ENTITY_USER = "user"
ENTITY_ROLE = "role"

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
# HUB-Expansion.md Phase 6 — see app/services/ticket_links.py.
TICKET_LINK_ADDED = "ticket.link_added"
TICKET_LINK_REMOVED = "ticket.link_removed"

# ── maintenance windows ───────────────────────────────────────────────────
MAINTENANCE_CREATED = "maintenance.created"
MAINTENANCE_UPDATED = "maintenance.updated"
MAINTENANCE_DELETED = "maintenance.deleted"
# HUB-Expansion.md Phase 7 — lifecycle events the scheduler/approval hook
# now emit (previously the scheduler only logged; see
# core/maintenance_scheduler.py's _auto_transition and app/approvals/
# maintenance_hooks.py).
MAINTENANCE_APPROVAL_REQUESTED = "maintenance.approval_requested"
MAINTENANCE_APPROVED = "maintenance.approved"
MAINTENANCE_APPROVAL_REJECTED = "maintenance.approval_rejected"
MAINTENANCE_STARTED = "maintenance.started"
MAINTENANCE_COMPLETED = "maintenance.completed"
MAINTENANCE_FAILED = "maintenance.failed"

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
GITHUB_APP_INSTALLED = "github.app_installed"
GITHUB_APP_UNINSTALLED = "github.app_uninstalled"
GITHUB_APP_CONFIGURED = "github.app_configured"

# ── deployment lineage (HUB-Expansion.md Phase 4 — see app/models.py's
# Customer/Application/DeploymentRelease and app/services/lineage.py) ─────
CUSTOMER_CREATED = "customer.created"
CUSTOMER_UPDATED = "customer.updated"
APPLICATION_CREATED = "application.created"
APPLICATION_UPDATED = "application.updated"
DEPLOYMENT_LINEAGE_UPDATED = "deployment.lineage_updated"
DEPLOYMENT_RELEASE_RECORDED = "deployment.release_recorded"

# ── staff / RBAC (HUB-Expansion.md Phase 19 — sensitive-action audit).
# Previously NEITHER users.py NOR rbac.py emitted any OperationalEvent at
# all — a real gap for exactly the actions Phase 19's own list calls out
# (who can do what, and who changed it). ──────────────────────────────────
STAFF_CREATED = "staff.created"
STAFF_UPDATED = "staff.updated"
STAFF_ROLE_CHANGED = "staff.role_changed"
STAFF_DEACTIVATED = "staff.deactivated"
STAFF_REACTIVATED = "staff.reactivated"
STAFF_PASSWORD_RESET = "staff.password_reset"
ROLE_CREATED = "role.created"
ROLE_RENAMED = "role.renamed"
ROLE_DELETED = "role.deleted"
ROLE_PERMISSIONS_UPDATED = "role.permissions_updated"
