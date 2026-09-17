"""GitHub App auth mode — see docs/integrations/github.md's "GitHub App
auth mode" section and docs/adr/ADR-004-github-app-auth.md.
GitHubIntegration.auth_mode already reserved 'github_app' as an enum
value (015_github_integration); this migration adds the two columns that
mode actually needs: installation_id (GitHub's own id for the
installation, unique) and access_token_expires_at (the cached
installation-token's expiry — access_token_encrypted itself already
exists and is reused for the cached token, no new column for that).

Revision ID: 022_github_app_auth
Revises: 021_audit_ip_rbac_events
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '022_github_app_auth'
down_revision: Union[str, None] = '021_audit_ip_rbac_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('github_integrations', sa.Column('installation_id', sa.Integer(), nullable=True))
    op.create_unique_constraint('uq_github_integrations_installation_id', 'github_integrations', ['installation_id'])
    op.add_column('github_integrations', sa.Column('access_token_expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('github_integrations', 'access_token_expires_at')
    op.drop_constraint('uq_github_integrations_installation_id', 'github_integrations', type_='unique')
    op.drop_column('github_integrations', 'installation_id')
