"""HUB-Expansion.md Phase 6 — Support <-> Engineering link. Two things
live here: resolving a SupportTicketLink's display fields (label/url/
target_status), and building a ticket's timeline by aggregating
OperationalEvent rows across the ticket itself, its deployment, and
everything it's linked to. See docs/adr/ADR-006-support-engineering-link.md
for why this is an aggregation-by-entity-reference rather than a shared-
correlation_id scheme."""
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.integrations.github.models import GitHubIssue, GitHubPullRequest, GitHubRelease
from app.models import MaintenanceWindow, OperationalEvent, SupportTicket, SupportTicketLink, TicketLinkType

# link_type -> the OperationalEvent.entity_type its target's own events use
# (see core/event_types.py) — every one of these constants already exists,
# emitted by Phase 3 (GitHub) and pre-existing maintenance handling.
_ENTITY_TYPE_FOR_LINK = {
    TicketLinkType.github_issue: et.ENTITY_GITHUB_ISSUE,
    TicketLinkType.github_pull_request: et.ENTITY_GITHUB_PULL_REQUEST,
    TicketLinkType.github_release: et.ENTITY_GITHUB_RELEASE,
    TicketLinkType.maintenance_window: et.ENTITY_MAINTENANCE_WINDOW,
}


async def target_exists(db: AsyncSession, link_type: TicketLinkType, target_id) -> bool:
    model = {
        TicketLinkType.github_issue: GitHubIssue,
        TicketLinkType.github_pull_request: GitHubPullRequest,
        TicketLinkType.github_release: GitHubRelease,
        TicketLinkType.maintenance_window: MaintenanceWindow,
    }[link_type]
    return (await db.get(model, target_id)) is not None


async def resolve_link_display(db: AsyncSession, link: SupportTicketLink) -> tuple[str, Optional[str], Optional[str]]:
    """(label, url, target_status). A target that's since been deleted
    (e.g. a maintenance window that was hard-deleted) still returns a
    label — the link row itself is never silently hidden — just with no
    url/status."""
    if link.link_type == TicketLinkType.github_issue:
        issue = await db.get(GitHubIssue, link.target_id)
        if issue is None:
            return "GitHub issue (no longer available)", None, None
        return f"GitHub #{issue.number}: {issue.title}", issue.html_url, issue.state
    if link.link_type == TicketLinkType.github_pull_request:
        pr = await db.get(GitHubPullRequest, link.target_id)
        if pr is None:
            return "GitHub PR (no longer available)", None, None
        return f"PR #{pr.number}: {pr.title}", pr.html_url, pr.state
    if link.link_type == TicketLinkType.github_release:
        release = await db.get(GitHubRelease, link.target_id)
        if release is None:
            return "GitHub release (no longer available)", None, None
        return release.name or release.tag_name, release.html_url, None
    if link.link_type == TicketLinkType.maintenance_window:
        window = await db.get(MaintenanceWindow, link.target_id)
        if window is None:
            return "Maintenance window (no longer available)", None, None
        return f"Maintenance: {window.description}", None, window.status.value
    raise ValueError(f"Unknown link_type {link.link_type}")  # pragma: no cover — TicketLinkType is exhaustive above


async def ticket_timeline(db: AsyncSession, ticket: SupportTicket) -> list[OperationalEvent]:
    """Every OperationalEvent for the ticket itself, its deployment, and
    everything it's linked to — merged and sorted chronologically. This
    IS the "ticket timeline" HUB-Expansion.md Phase 6 asks for: no new
    event-emission code needed, since Phase 1/3/pre-existing maintenance
    handling already emit into this one table for each of those entities
    — this just unions the right WHERE clauses."""
    links = (await db.execute(select(SupportTicketLink).where(SupportTicketLink.ticket_id == ticket.id))).scalars().all()

    conditions = [
        (OperationalEvent.entity_type == et.ENTITY_TICKET) & (OperationalEvent.entity_id == ticket.id),
        (OperationalEvent.entity_type == et.ENTITY_DEPLOYMENT) & (OperationalEvent.entity_id == ticket.deployment_id),
    ]
    for link in links:
        entity_type = _ENTITY_TYPE_FOR_LINK[link.link_type]
        conditions.append((OperationalEvent.entity_type == entity_type) & (OperationalEvent.entity_id == link.target_id))

    return (await db.execute(
        select(OperationalEvent).where(or_(*conditions)).order_by(OperationalEvent.created_at)
    )).scalars().all()
