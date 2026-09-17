"""Permissions — a Casbin `(resource, action)` pair per gated *action*, not
per page/router. `casbin_rule` (p/g rows) is the enforcement source of
truth; `Role`/`Permission` (app/models.py) are DB-backed metadata layered
on top purely for the admin-facing Roles & Permissions UI
(api/routers/rbac.py) — a Permission row doesn't grant anything by itself,
it just makes a (resource, action) pair visible/toggleable in that UI.

History: Phase 1 shipped one coarse STAFF_MANAGE. Migration 005 added one
coarse FLEET_MANAGE (deployments+tickets+maintenance+notifications
together). Migration 010 split FLEET_MANAGE into one permission per
*resource* (deployments.manage, tickets.manage, ...) and added the
Role/Permission catalog tables. Migration 011 went one step further and
split each resource's one coarse '.manage' into one permission per
*action* (renew/suspend/change_plan/... for deployments; create/update/
delete for maintenance; etc.) plus a `.view`/`.view_all` pair per
deployment-scoped resource — see "Row-level visibility" below. Each cutover
was behavior-preserving for 'admin' (kept getting everything); 011 was NOT
behavior-preserving for 'engineer' by design — see migration 011's
docstring.

## Row-level visibility (deployments / tickets / maintenance)

A user assigned to specific deployments (DeploymentStaffAssignment, see
api/routers/deployments.py's PUT .../staff) should only see *those*
deployments, and — since a ticket/maintenance-window belongs to a
deployment — only their tickets and (deployment-specific) maintenance
windows too. Holding `<resource>.view_all` bypasses that scoping entirely
(only 'admin' holds it at cutover). This is enforced server-side in each
router's visible-or-404 helper (deployments.py's _get_visible_or_404 and
its tickets.py/maintenance.py equivalents), not just hidden in the
frontend — a user who can enumerate a deployment_id they're not assigned
to still gets a 404 from every route that takes one, not just the list/
detail GETs. Fleet-wide maintenance windows (deployment_id IS NULL) are
never scoped — they're not about any one deployment.

ALL_PERMISSIONS is the canonical catalog — new permissions are added here
*and* seeded into the `permissions` table by a migration, never invented
at runtime (see rbac.py's list_permissions, which just reads that table)."""
from fastapi import Depends, HTTPException, status

from app.core.casbin_enforcer import get_enforcer
from app.core.deps import get_current_user
from app.models import User

DEFAULT_DOMAIN = "default"

# register.py / ingest.py / the hub_integration equivalent on the
# deployment side are NOT Casbin-gated at all — they're machine-to-machine,
# authenticated by a shared secret instead (api/routers/ingest.py verifies
# the deployment's api_key_hash directly against the Deployment row, no
# Casbin involved).

# ── deployments ──────────────────────────────────────────────────────────
DEPLOYMENTS_VIEW = ("deployments", "view")
DEPLOYMENTS_VIEW_ALL = ("deployments", "view_all")
DEPLOYMENTS_CREATE = ("deployments", "create")
DEPLOYMENTS_RENEW = ("deployments", "renew")
DEPLOYMENTS_SUSPEND = ("deployments", "suspend")
DEPLOYMENTS_CHANGE_PLAN = ("deployments", "change_plan")
DEPLOYMENTS_EXTEND_EXPIRY = ("deployments", "extend_expiry")
DEPLOYMENTS_CHECK_HEALTH = ("deployments", "check_health")
DEPLOYMENTS_REVIEW_REQUEST = ("deployments", "review_request")
DEPLOYMENTS_ASSIGN_STAFF = ("deployments", "assign_staff")
DEPLOYMENTS_MANAGE_LINEAGE = ("deployments", "manage_lineage")

# ── tickets ───────────────────────────────────────────────────────────────
TICKETS_VIEW = ("tickets", "view")
TICKETS_VIEW_ALL = ("tickets", "view_all")
TICKETS_UPDATE_STATUS = ("tickets", "update_status")
TICKETS_MANAGE_LINKS = ("tickets", "manage_links")

# ── maintenance ───────────────────────────────────────────────────────────
MAINTENANCE_VIEW = ("maintenance", "view")
MAINTENANCE_VIEW_ALL = ("maintenance", "view_all")
MAINTENANCE_CREATE = ("maintenance", "create")
MAINTENANCE_UPDATE = ("maintenance", "update")
MAINTENANCE_DELETE = ("maintenance", "delete")

# ── notifications ────────────────────────────────────────────────────────
NOTIFICATIONS_VIEW = ("notifications", "view")
NOTIFICATIONS_RESEND = ("notifications", "resend")
NOTIFICATIONS_DELETE = ("notifications", "delete")
NOTIFICATIONS_TEST_SEND = ("notifications", "test_send")

# ── staff ─────────────────────────────────────────────────────────────────
STAFF_VIEW = ("staff", "view")
STAFF_CREATE = ("staff", "create")
STAFF_UPDATE = ("staff", "update")            # edit/deactivate/reactivate/change role
STAFF_RESET_PASSWORD = ("staff", "reset_password")

# ── rbac ──────────────────────────────────────────────────────────────────
RBAC_VIEW = ("rbac", "view")
RBAC_MANAGE = ("rbac", "manage")              # create/rename/delete roles, edit a role's permissions

# ── workflows (BPMN definitions/versions — the design-time surface) ───────
# Migration 012. Ported from PoultryPro-CBF's core/permissions.py naming,
# minus WORKFLOWS_MANAGE_SYSTEM (no hidden bypass role here — is_system is
# simply frozen for everyone once published) and WORKFLOW_INSTANCES_START
# (instances only ever start via the deployment-action integration, never a
# direct "start any workflow" API call — see app/approvals/deployment_hooks.py).
WORKFLOWS_VIEW = ("workflows", "view")
WORKFLOWS_CREATE = ("workflows", "create")
WORKFLOWS_UPDATE = ("workflows", "update")
WORKFLOWS_PUBLISH = ("workflows", "publish")
WORKFLOWS_ARCHIVE = ("workflows", "archive")
WORKFLOWS_DELETE = ("workflows", "delete")

# ── workflow rules (approval routing config) ───────────────────────────────
WORKFLOW_RULES_VIEW = ("workflow_rules", "view")
WORKFLOW_RULES_CREATE = ("workflow_rules", "create")
WORKFLOW_RULES_UPDATE = ("workflow_rules", "update")
WORKFLOW_RULES_DELETE = ("workflow_rules", "delete")
WORKFLOW_RULES_TEST = ("workflow_rules", "test")

# ── workflow instances (a running approval against a business object) ─────
WORKFLOW_INSTANCES_VIEW = ("workflow_instances", "view")
WORKFLOW_INSTANCES_CANCEL = ("workflow_instances", "cancel")

# ── approvals (acting on a workflow task) ───────────────────────────────────
APPROVALS_VIEW = ("approvals", "view")
APPROVALS_ACT = ("approvals", "act")
APPROVALS_REASSIGN = ("approvals", "reassign")
APPROVALS_ACT_ANY = ("approvals", "act_any")   # act on a task you're not a candidate/assignee for

# ── operational events (HUB-Expansion.md Phase 1 — see app/models.py's
# OperationalEvent and api/routers/events.py) ───────────────────────────────
EVENTS_VIEW = ("events", "view")
EVENTS_VIEW_ALL = ("events", "view_all")

# ── retrying a failed deployment action execution (HUB-Expansion.md Phase
# 12 — see app/models.py's DeploymentActionExecution) ───────────────────────
ACTIONS_RETRY = ("actions", "retry")

# ── GitHub integration (HUB-Expansion.md Phase 3 — see
# app/integrations/github/). Not row-scoped like deployments/tickets/
# maintenance — a repository isn't inherently one customer's confidential
# data, and there's no github.view_all companion permission (see
# docs/integrations/github.md's "Authorization" section for why). The one
# exception, GET /deployments/{id}/github, reuses DEPLOYMENTS_VIEW's own
# row-level scoping rather than a github.* permission at all. ─────────────
GITHUB_VIEW = ("github", "view")
GITHUB_MANAGE = ("github", "manage")                    # create/edit an integration's credentials
GITHUB_TEST_CONNECTION = ("github", "test_connection")   # diagnostic — same tier as deployments.check_health
GITHUB_SYNC = ("github", "sync")                         # trigger a repository sync

# ── customers / applications (HUB-Expansion.md Phase 4 — see
# app/models.py's Customer/Application). Not row-scoped — small,
# fleet-wide reference data, same tier as the GitHub catalog above. ────────
CUSTOMERS_VIEW = ("customers", "view")
CUSTOMERS_MANAGE = ("customers", "manage")
APPLICATIONS_VIEW = ("applications", "view")
APPLICATIONS_MANAGE = ("applications", "manage")

# ── dashboard (HUB-Expansion.md Phase 10) — one coarse permission for the
# whole aggregated view, row-scoped exactly like deployments.py wherever
# it counts per-deployment things; sub-sections needing a permission the
# caller lacks (e.g. approvals.view for the Approvals section) degrade to
# zeroed-out rather than 403ing the whole page — see services/dashboard.py.
DASHBOARD_VIEW = ("dashboard", "view")

# ── integration center (HUB-Expansion.md Phase 11) — read-only rollup
# over GitHub/CI-CD/email/WhatsApp/Monitoring health; never exposes a
# secret itself (see api/routers/integrations.py) — GITHUB_MANAGE/
# NOTIFICATIONS_TEST_SEND still gate the actual "test connection"
# actions each card's button calls.
INTEGRATIONS_VIEW = ("integrations", "view")

# (resource, action, description) — the migration-seeded catalog. Order
# here is also the order the admin UI's checkbox grid renders in within
# each resource's group.
ALL_PERMISSIONS: list[tuple[str, str, str]] = [
    (*DEPLOYMENTS_VIEW, "View deployments assigned to you"),
    (*DEPLOYMENTS_VIEW_ALL, "View every deployment, not just ones assigned to you"),
    (*DEPLOYMENTS_CREATE, "Register a new client deployment and issue its registration token"),
    (*DEPLOYMENTS_RENEW, "Renew a deployment's subscription"),
    (*DEPLOYMENTS_SUSPEND, "Suspend a deployment's subscription"),
    (*DEPLOYMENTS_CHANGE_PLAN, "Change a deployment's plan"),
    (*DEPLOYMENTS_EXTEND_EXPIRY, "Extend a deployment's subscription expiry date"),
    (*DEPLOYMENTS_CHECK_HEALTH, "Run an on-demand live health check against a deployment"),
    (*DEPLOYMENTS_REVIEW_REQUEST, "Approve or reject a deployment's renewal/upgrade request"),
    (*DEPLOYMENTS_ASSIGN_STAFF, "Assign staff to a deployment"),
    (*DEPLOYMENTS_MANAGE_LINEAGE, "Set a deployment's customer/application/environment and record its current release"),
    (*TICKETS_VIEW, "View support tickets from deployments assigned to you"),
    (*TICKETS_VIEW_ALL, "View support tickets from every deployment"),
    (*TICKETS_UPDATE_STATUS, "Update a support ticket's status"),
    (*TICKETS_MANAGE_LINKS, "Link a support ticket to a GitHub issue/PR/release or a maintenance window"),
    (*MAINTENANCE_VIEW, "View maintenance windows for deployments assigned to you (fleet-wide windows are always visible)"),
    (*MAINTENANCE_VIEW_ALL, "View maintenance windows for every deployment"),
    (*MAINTENANCE_CREATE, "Create a maintenance window"),
    (*MAINTENANCE_UPDATE, "Edit a maintenance window"),
    (*MAINTENANCE_DELETE, "Cancel/delete a maintenance window"),
    (*NOTIFICATIONS_VIEW, "View notification history"),
    (*NOTIFICATIONS_RESEND, "Resend a failed/cancelled notification"),
    (*NOTIFICATIONS_DELETE, "Delete a notification log entry"),
    (*NOTIFICATIONS_TEST_SEND, "Send a test email/WhatsApp message"),
    (*STAFF_VIEW, "View staff accounts"),
    (*STAFF_CREATE, "Create a staff account"),
    (*STAFF_UPDATE, "Edit, deactivate/reactivate, or change the role of a staff account"),
    (*STAFF_RESET_PASSWORD, "Reset a staff account's password"),
    (*RBAC_VIEW, "View the permission catalog and roles"),
    (*RBAC_MANAGE, "Create/delete custom roles and change which permissions any role holds"),
    (*WORKFLOWS_VIEW, "View workflow definitions and their BPMN diagrams"),
    (*WORKFLOWS_CREATE, "Create a new workflow definition"),
    (*WORKFLOWS_UPDATE, "Edit a workflow definition or its draft versions"),
    (*WORKFLOWS_PUBLISH, "Publish a workflow version"),
    (*WORKFLOWS_ARCHIVE, "Archive a workflow version"),
    (*WORKFLOWS_DELETE, "Delete a workflow definition"),
    (*WORKFLOW_RULES_VIEW, "View approval routing rules"),
    (*WORKFLOW_RULES_CREATE, "Create an approval routing rule"),
    (*WORKFLOW_RULES_UPDATE, "Edit an approval routing rule"),
    (*WORKFLOW_RULES_DELETE, "Delete an approval routing rule"),
    (*WORKFLOW_RULES_TEST, "Test an approval routing rule against sample input"),
    (*WORKFLOW_INSTANCES_VIEW, "View running/finished workflow instances and their history"),
    (*WORKFLOW_INSTANCES_CANCEL, "Cancel a running workflow instance"),
    (*APPROVALS_VIEW, "View the approvals inbox"),
    (*APPROVALS_ACT, "Approve or reject a task assigned/candidate to you"),
    (*APPROVALS_REASSIGN, "Reassign a pending approval task to another user"),
    (*APPROVALS_ACT_ANY, "Approve or reject any pending task, not just ones you're a candidate for"),
    (*EVENTS_VIEW, "View the operational event log for deployments assigned to you (non-deployment events are always visible)"),
    (*EVENTS_VIEW_ALL, "View the operational event log for every deployment"),
    (*ACTIONS_RETRY, "Retry a deployment action whose execution failed after approval"),
    (*GITHUB_VIEW, "View configured GitHub integrations, repositories, pull requests, issues, and releases"),
    (*GITHUB_MANAGE, "Create/edit a GitHub integration's credentials"),
    (*GITHUB_TEST_CONNECTION, "Test a GitHub integration's stored credentials"),
    (*GITHUB_SYNC, "Trigger a GitHub repository sync"),
    (*CUSTOMERS_VIEW, "View the customer directory"),
    (*CUSTOMERS_MANAGE, "Create/edit customers"),
    (*APPLICATIONS_VIEW, "View the application catalog"),
    (*APPLICATIONS_MANAGE, "Create/edit applications"),
    (*DASHBOARD_VIEW, "View the operations dashboard"),
    (*INTEGRATIONS_VIEW, "View the integration center (GitHub/CI-CD/email/WhatsApp/monitoring health)"),
]


async def has_permission(user_id: str, resource: str, action: str) -> bool:
    # enforce() itself is sync — it only reads the already-loaded in-memory
    # policy; only load_policy()/save_policy() touch the DB and need await.
    return get_enforcer().enforce(user_id, DEFAULT_DOMAIN, resource, action)


def require_permission(resource: str, action: str):
    async def _dep(current_user: User = Depends(get_current_user)) -> User:
        if not await has_permission(str(current_user.id), resource, action):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")
        return current_user
    return _dep
