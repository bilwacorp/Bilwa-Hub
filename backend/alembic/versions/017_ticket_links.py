"""HUB-Expansion.md Phase 6 — Support <-> Engineering link
(app/models.py's TicketLinkType/SupportTicketLink). See
docs/adr/ADR-006-support-engineering-link.md for the full design.

Seeds tickets.manage_links: 'admin' and 'engineer' both get it — same
"keeps every action permission it already effectively had on this
resource" pattern as migration 011's tickets.update_status (engineer
already manages tickets end-to-end within its assigned deployments).

Revision ID: 017_ticket_links
Revises: 016_deployment_lineage
Create Date: 2026-09-17
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '017_ticket_links'
down_revision: Union[str, None] = '016_deployment_lineage'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

NEW_PERMISSIONS = [
    ("tickets", "manage_links", "Link a support ticket to a GitHub issue/PR/release or a maintenance window"),
]
ADMIN_GRANTS = {(r, a) for r, a, _ in NEW_PERMISSIONS}
ENGINEER_GRANTS = {("tickets", "manage_links")}


def _grant(conn, role: str, resource: str, action: str) -> None:
    exists = conn.execute(
        sa.text("SELECT 1 FROM casbin_rule WHERE ptype = 'p' AND v0 = :role AND v1 = :domain AND v2 = :resource AND v3 = :action"),
        {"role": role, "domain": DOMAIN, "resource": resource, "action": action},
    ).scalar()
    if not exists:
        conn.execute(
            sa.text("INSERT INTO casbin_rule (ptype, v0, v1, v2, v3) VALUES ('p', :role, :domain, :resource, :action)"),
            {"role": role, "domain": DOMAIN, "resource": resource, "action": action},
        )


def _seed_permission(conn, resource: str, action: str, description: str) -> None:
    exists = conn.execute(
        sa.text("SELECT 1 FROM permissions WHERE resource = :resource AND action = :action"),
        {"resource": resource, "action": action},
    ).scalar()
    if not exists:
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, description, created_at) "
                "VALUES (:id, :resource, :action, :description, now())"
            ),
            {"id": str(uuid.uuid4()), "resource": resource, "action": action, "description": description},
        )


def upgrade() -> None:
    conn = op.get_bind()
    uuid_pk = postgresql.UUID(as_uuid=True)

    op.execute(
        "DO $$ BEGIN CREATE TYPE ticketlinktype AS ENUM "
        "('github_issue', 'github_pull_request', 'github_release', 'maintenance_window'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    link_type = postgresql.ENUM(
        'github_issue', 'github_pull_request', 'github_release', 'maintenance_window',
        name='ticketlinktype', create_type=False,
    )

    op.create_table(
        'support_ticket_links',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('ticket_id', uuid_pk, sa.ForeignKey('support_tickets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('link_type', link_type, nullable=False),
        sa.Column('target_id', uuid_pk, nullable=False),
        sa.Column('created_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('ticket_id', 'link_type', 'target_id', name='uq_ticket_link_target'),
    )
    op.create_index('ix_support_ticket_links_ticket_id', 'support_ticket_links', ['ticket_id'])

    for resource, action, description in NEW_PERMISSIONS:
        _seed_permission(conn, resource, action, description)
    for resource, action in ADMIN_GRANTS:
        _grant(conn, "admin", resource, action)
    for resource, action in ENGINEER_GRANTS:
        _grant(conn, "engineer", resource, action)


def downgrade() -> None:
    conn = op.get_bind()
    for resource, action, _ in NEW_PERMISSIONS:
        conn.execute(sa.text(
            "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = :resource AND v3 = :action"
        ), {"domain": DOMAIN, "resource": resource, "action": action})
        conn.execute(sa.text(
            "DELETE FROM permissions WHERE resource = :resource AND action = :action"
        ), {"resource": resource, "action": action})

    op.drop_index('ix_support_ticket_links_ticket_id', table_name='support_ticket_links')
    op.drop_table('support_ticket_links')
    op.execute("DROP TYPE IF EXISTS ticketlinktype")
