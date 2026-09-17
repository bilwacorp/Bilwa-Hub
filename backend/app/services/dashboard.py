"""HUB-Expansion.md Phase 10 — Operations Dashboard. One function per
section, all fed by real queries over data every earlier phase already
produces — no new tables, no placeholder numbers. `build_dashboard` is
the one entry point `api/routers/dashboard.py` calls.

Row-level visibility: every deployment-scoped section is narrowed to
`deployment_ids` (None = fleet-wide, i.e. the caller holds
`deployments.view_all`) exactly like deployments.py/tickets.py/
maintenance.py already do — see core/permissions.py's "Row-level
visibility" section. Approvals/integrations sections are NOT
deployment-scoped (a workflow instance or a GitHub webhook isn't
inherently tied to one deployment the same way); instead, each of those
sections quietly zeroes out — rather than making the whole dashboard
403 — when the caller lacks that domain's own view permission
(`approvals.view`, `workflow_instances.view`, `github.view`,
`notifications.view`). A dashboard is a summary of what the caller can
already see elsewhere, never a way to see more.

"Outdated version" / "high risk" / "unassigned ticket" / "escalated
ticket" / "awaiting engineering" have no first-class flag anywhere in
this codebase — each is a documented, real, computable proxy (see each
function's own docstring for the exact definition), not an invented
concept with its own new column. A future phase that wants a truer
definition should replace the proxy in one place here, not re-derive it
per caller.
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.permissions import (
    APPROVALS_VIEW, GITHUB_VIEW, NOTIFICATIONS_VIEW, WORKFLOW_INSTANCES_VIEW, has_permission,
)
from app.integrations.github.models import GitHubWebhookEvent, GitHubWebhookEventStatus
from app.models import (
    Deployment, DeploymentActionExecution, DeploymentActionExecutionStatus, DeploymentRelease, DeploymentSnapshot,
    DeploymentStaffAssignment, MaintenanceWindow, MaintenanceWindowStatus, NotificationLog, NotificationStatus,
    SupportTicket, SupportTicketLink, SupportTicketStatus, User,
)
from app.schemas import (
    DashboardApprovalsSummary, DashboardAttentionItem, DashboardDeploymentsSummary, DashboardFailedActionItem,
    DashboardFleetSummary, DashboardIntegrationsSummary, DashboardMaintenanceSummary, DashboardOut,
    DashboardReleaseItem, DashboardSupportSummary,
)
from app.services.maintenance_query import currently_active_windows
from app.workflow.models import InstanceStatus, WorkflowInstance, WorkflowTask, WorkflowTaskStatus

_RECENT_WINDOW = timedelta(hours=24)


async def _visible_deployments(db: AsyncSession, deployment_ids: Optional[list]) -> list[Deployment]:
    conditions = [Deployment.id.in_(deployment_ids)] if deployment_ids is not None else []
    return (await db.execute(select(Deployment).where(*conditions))).scalars().all()


async def _latest_snapshot_ages(db: AsyncSession, deployment_ids: list) -> dict:
    """deployment_id -> seconds since its most recent heartbeat, for every
    id that has ever sent one. Bulk (one query), not one lookup per
    deployment — same reasoning as api/routers/deployments.py's
    _assigned_staff_map."""
    if not deployment_ids:
        return {}
    latest_subq = (
        select(DeploymentSnapshot.deployment_id, func.max(DeploymentSnapshot.received_at).label("received_at"))
        .where(DeploymentSnapshot.deployment_id.in_(deployment_ids))
        .group_by(DeploymentSnapshot.deployment_id)
        .subquery()
    )
    rows = (await db.execute(select(latest_subq.c.deployment_id, latest_subq.c.received_at))).all()
    now = datetime.utcnow()
    return {dep_id: (now - received_at).total_seconds() for dep_id, received_at in rows}


async def _fleet_summary(db: AsyncSession, deployments: list[Deployment]) -> tuple[DashboardFleetSummary, dict]:
    ids = [d.id for d in deployments]
    ages = await _latest_snapshot_ages(db, ids)
    active_windows = await currently_active_windows(db)
    under_maintenance_ids = {
        d.id for d in deployments
        if any(w.deployment_id is None or w.deployment_id == d.id for w in active_windows)
    }

    healthy = warning = offline = unknown = 0
    for d in deployments:
        if d.id in under_maintenance_ids:
            continue
        age = ages.get(d.id)
        if age is None:
            unknown += 1
        elif age > settings.HEARTBEAT_OFFLINE_HOURS * 3600:
            offline += 1
        elif age > settings.HEARTBEAT_STALE_HOURS * 3600:
            warning += 1
        else:
            healthy += 1

    summary = DashboardFleetSummary(
        total=len(deployments), healthy=healthy, warning=warning, offline=offline, unknown=unknown,
        under_maintenance=len(under_maintenance_ids),
    )
    return summary, ages


async def _deployments_summary(
    db: AsyncSession, deployments: list[Deployment], ages: dict,
) -> tuple[DashboardDeploymentsSummary, list[DashboardAttentionItem]]:
    ids = [d.id for d in deployments]
    names = {d.id: d.client_name for d in deployments}
    attention: list[DashboardAttentionItem] = []

    recent_releases = []
    outdated = 0
    if ids:
        # Most recent DeploymentRelease per deployment — same "one query,
        # first row per id wins (deployed_at desc)" shape as
        # services/lineage.current_releases_map.
        rows = (await db.execute(
            select(DeploymentRelease).where(DeploymentRelease.deployment_id.in_(ids))
            .order_by(DeploymentRelease.deployment_id, DeploymentRelease.deployed_at.desc())
        )).scalars().all()
        latest_by_deployment: dict = {}
        for r in rows:
            latest_by_deployment.setdefault(r.deployment_id, r)
        for dep_id, release in latest_by_deployment.items():
            if release.version and release.repository_id is not None and release.release_id is None:
                # "Deployment version differs from expected release" —
                # HUB knows this repo but couldn't match the reported
                # version to any synced GitHubRelease tag.
                outdated += 1
                attention.append(DashboardAttentionItem(
                    kind="version_mismatch", severity="warning", deployment_id=dep_id,
                    message=f"{names.get(dep_id, dep_id)} is running version '{release.version}', which doesn't match any known release.",
                ))
        recent_releases = sorted(latest_by_deployment.values(), key=lambda r: r.deployed_at, reverse=True)[:5]

    failed_executions = []
    high_risk_ids: set = set()
    if ids:
        failed_rows = (await db.execute(
            select(DeploymentActionExecution).where(
                DeploymentActionExecution.deployment_id.in_(ids),
                DeploymentActionExecution.status == DeploymentActionExecutionStatus.failed,
            ).order_by(DeploymentActionExecution.updated_at.desc())
        )).scalars().all()
        high_risk_ids = {e.deployment_id for e in failed_rows}
        failed_executions = failed_rows[:5]
        for e in failed_rows:
            attention.append(DashboardAttentionItem(
                kind="action_execution_failed", severity="critical", deployment_id=e.deployment_id,
                message=f"{names.get(e.deployment_id, e.deployment_id)}'s approved {e.action_key.replace('deployment_', '')} failed to execute: {e.last_error or 'unknown error'}.",
            ))

    missing_heartbeat = 0
    for d in deployments:
        age = ages.get(d.id)
        if age is None or age > settings.HEARTBEAT_OFFLINE_HOURS * 3600:
            missing_heartbeat += 1
            attention.append(DashboardAttentionItem(
                kind="heartbeat_overdue", severity="critical" if age else "warning", deployment_id=d.id,
                message=f"{d.client_name} has {'never sent a heartbeat' if age is None else 'not sent a heartbeat in over ' + str(settings.HEARTBEAT_OFFLINE_HOURS) + 'h'}.",
            ))

    summary = DashboardDeploymentsSummary(
        recently_deployed=[
            DashboardReleaseItem(deployment_id=r.deployment_id, client_name=names.get(r.deployment_id, str(r.deployment_id)), version=r.version, deployed_at=r.deployed_at)
            for r in recent_releases
        ],
        recently_failed=[
            DashboardFailedActionItem(
                deployment_id=e.deployment_id, client_name=names.get(e.deployment_id, str(e.deployment_id)),
                action_key=e.action_key, last_error=e.last_error, last_attempted_at=e.last_attempted_at,
            )
            for e in failed_executions
        ],
        outdated_versions=outdated, missing_heartbeat=missing_heartbeat, high_risk=len(high_risk_ids),
    )
    return summary, attention


async def _support_summary(
    db: AsyncSession, deployment_ids: Optional[list],
) -> tuple[DashboardSupportSummary, list[DashboardAttentionItem]]:
    """'Unassigned' = the ticket's deployment has no DeploymentStaffAssignment
    row (falls back to notifying every fleet-area staff member — see
    services/notifications/recipients.py — but nobody is specifically on
    the hook for it). 'Escalated' = open and high/urgent priority (this
    codebase's priority field is free-text on ingest; 'high'/'urgent' are
    the two values PoultryOS-CBP's own ticket form actually sends).
    'Awaiting engineering' = in_progress AND has at least one
    SupportTicketLink (Phase 6) — i.e. handed off to engineering already."""
    conditions = [SupportTicket.deployment_id.in_(deployment_ids)] if deployment_ids is not None else []
    tickets = (await db.execute(select(SupportTicket).where(*conditions))).scalars().all()
    open_tickets = [t for t in tickets if t.status == SupportTicketStatus.open]

    assigned_deployment_ids_with_staff = set((await db.execute(select(DeploymentStaffAssignment.deployment_id))).scalars().all())
    unassigned = [t for t in open_tickets if t.deployment_id not in assigned_deployment_ids_with_staff]
    escalated = [t for t in open_tickets if t.priority in ("high", "urgent")]

    in_progress_ids = [t.id for t in tickets if t.status == SupportTicketStatus.in_progress]
    awaiting_engineering = 0
    if in_progress_ids:
        linked_ticket_ids = set((await db.execute(
            select(SupportTicketLink.ticket_id).where(SupportTicketLink.ticket_id.in_(in_progress_ids)).distinct()
        )).scalars().all())
        awaiting_engineering = len(linked_ticket_ids)

    attention = [
        DashboardAttentionItem(
            kind="ticket_unassigned", severity="warning", deployment_id=t.deployment_id,
            message=f"Ticket '{t.subject}' has no assigned staff on its deployment.",
        )
        for t in unassigned[:10]
    ]
    summary = DashboardSupportSummary(
        open=len(open_tickets), unassigned=len(unassigned), escalated=len(escalated), awaiting_engineering=awaiting_engineering,
    )
    return summary, attention


async def _maintenance_summary(
    db: AsyncSession, deployment_ids: Optional[list],
) -> tuple[DashboardMaintenanceSummary, list[DashboardAttentionItem]]:
    conditions = []
    if deployment_ids is not None:
        conditions.append(or_(MaintenanceWindow.deployment_id.is_(None), MaintenanceWindow.deployment_id.in_(deployment_ids)))
    windows = (await db.execute(select(MaintenanceWindow).where(*conditions))).scalars().all()

    upcoming_statuses = (
        MaintenanceWindowStatus.planned, MaintenanceWindowStatus.notification,
        MaintenanceWindowStatus.approved, MaintenanceWindowStatus.approval_required,
    )
    now = datetime.utcnow()
    upcoming = [w for w in windows if w.status in upcoming_statuses and w.scheduled_start > now]
    active = [w for w in windows if w.status == MaintenanceWindowStatus.in_progress]
    failed = [w for w in windows if w.status == MaintenanceWindowStatus.failed]

    attention = [
        DashboardAttentionItem(
            kind="maintenance_starting_soon", severity="warning", deployment_id=w.deployment_id,
            message=f"Maintenance ({w.description}) starts within 24h.",
        )
        for w in upcoming if w.scheduled_start - now <= timedelta(hours=24)
    ]
    summary = DashboardMaintenanceSummary(upcoming=len(upcoming), active=len(active), failed=len(failed))
    return summary, attention


async def _approvals_summary(db: AsyncSession, user_id: str) -> tuple[DashboardApprovalsSummary, list[DashboardAttentionItem]]:
    if not await has_permission(user_id, *APPROVALS_VIEW) or not await has_permission(user_id, *WORKFLOW_INSTANCES_VIEW):
        return DashboardApprovalsSummary(pending=0, overdue=0, recently_approved=0, recently_rejected=0), []

    now = datetime.utcnow()
    pending_tasks = (await db.execute(
        select(WorkflowTask).where(WorkflowTask.status == WorkflowTaskStatus.pending)
    )).scalars().all()
    overdue_tasks = [t for t in pending_tasks if t.due_at is not None and t.due_at < now]

    since = now - _RECENT_WINDOW
    recently_approved = (await db.execute(
        select(func.count(WorkflowInstance.id)).where(WorkflowInstance.status == InstanceStatus.completed, WorkflowInstance.completed_at >= since)
    )).scalar() or 0
    recently_rejected = (await db.execute(
        select(func.count(WorkflowInstance.id)).where(WorkflowInstance.status == InstanceStatus.rejected, WorkflowInstance.completed_at >= since)
    )).scalar() or 0

    attention = [
        DashboardAttentionItem(
            kind="approval_overdue", severity="critical",
            message=f"Approval task '{t.task_name or t.step_key}' has been waiting since {t.created_at.isoformat()}.",
        )
        for t in overdue_tasks[:10]
    ]
    summary = DashboardApprovalsSummary(
        pending=len(pending_tasks), overdue=len(overdue_tasks),
        recently_approved=recently_approved, recently_rejected=recently_rejected,
    )
    return summary, attention


async def _integrations_summary(db: AsyncSession, user_id: str) -> tuple[DashboardIntegrationsSummary, list[DashboardAttentionItem]]:
    since = datetime.utcnow() - _RECENT_WINDOW
    attention: list[DashboardAttentionItem] = []

    github_failures = 0
    if await has_permission(user_id, *GITHUB_VIEW):
        github_failures = (await db.execute(
            select(func.count(GitHubWebhookEvent.id)).where(
                GitHubWebhookEvent.status == GitHubWebhookEventStatus.failed, GitHubWebhookEvent.received_at >= since,
            )
        )).scalar() or 0
        if github_failures:
            attention.append(DashboardAttentionItem(
                kind="github_webhook_failing", severity="warning",
                message=f"{github_failures} GitHub webhook deliveries failed to process in the last 24h.",
            ))

    # Not permission-gated separately — DeploymentActionExecution rows are
    # already scoped to visible deployments by the caller (see
    # build_dashboard), same tier as the Deployments section above.
    deployment_callback_failures = (await db.execute(
        select(func.count(DeploymentActionExecution.id)).where(
            DeploymentActionExecution.status == DeploymentActionExecutionStatus.failed,
            DeploymentActionExecution.updated_at >= since,
        )
    )).scalar() or 0

    notification_failures = 0
    if await has_permission(user_id, *NOTIFICATIONS_VIEW):
        notification_failures = (await db.execute(
            select(func.count(NotificationLog.id)).where(
                NotificationLog.status == NotificationStatus.failed, NotificationLog.created_at >= since,
            )
        )).scalar() or 0
        if notification_failures:
            attention.append(DashboardAttentionItem(
                kind="notifications_failing", severity="warning",
                message=f"{notification_failures} notifications failed to send in the last 24h.",
            ))

    summary = DashboardIntegrationsSummary(
        github_webhook_failures=github_failures, deployment_callback_failures=deployment_callback_failures,
        notification_failures=notification_failures,
    )
    return summary, attention


async def build_dashboard(db: AsyncSession, current_user: User, deployment_ids: Optional[list]) -> DashboardOut:
    deployments = await _visible_deployments(db, deployment_ids)
    fleet, ages = await _fleet_summary(db, deployments)
    deployments_summary, deployments_attention = await _deployments_summary(db, deployments, ages)
    support_summary, support_attention = await _support_summary(db, deployment_ids)
    maintenance_summary, maintenance_attention = await _maintenance_summary(db, deployment_ids)
    approvals_summary, approvals_attention = await _approvals_summary(db, str(current_user.id))
    integrations_summary, integrations_attention = await _integrations_summary(db, str(current_user.id))

    return DashboardOut(
        fleet=fleet, deployments=deployments_summary, support=support_summary, maintenance=maintenance_summary,
        approvals=approvals_summary, integrations=integrations_summary,
        attention=[*deployments_attention, *support_attention, *maintenance_attention, *approvals_attention, *integrations_attention],
    )
