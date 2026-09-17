"""HUB-Expansion.md Phase 19 — Audit Requirements. Adds
`operational_events.actor_ip` (populated automatically by
services/events.record_event() via app/core/request_context's
contextvar — see app/main.py's client_ip_middleware). No permission
changes and no other schema changes: the other half of this phase
(users.py/rbac.py now emitting OperationalEvent rows for staff/role
changes, previously emitting none at all) is pure application code, not
a schema change — see docs/adr/ADR-010-audit-requirements.md.

Revision ID: 021_audit_ip_rbac_events
Revises: 020_integrations_permission
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '021_audit_ip_rbac_events'
down_revision: Union[str, None] = '020_integrations_permission'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('operational_events', sa.Column('actor_ip', sa.String(45), nullable=True))


def downgrade() -> None:
    op.drop_column('operational_events', 'actor_ip')
