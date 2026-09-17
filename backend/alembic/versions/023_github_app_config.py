"""GitHub App manifest flow — see docs/adr/ADR-004-github-app-auth.md
decision #9. Adds github_app_configs, a practical singleton table
holding the GitHub App's own credentials when they were produced by the
"Set up GitHub App" manifest flow rather than hand-configured via the
GITHUB_APP_* env vars (core/config.py) — the first DB-stored runtime
config in this codebase, a deliberate narrow exception (see the model's
own docstring).

Revision ID: 023_github_app_config
Revises: 022_github_app_auth
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '023_github_app_config'
down_revision: Union[str, None] = '022_github_app_auth'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    uuid_pk = postgresql.UUID(as_uuid=True)
    op.create_table(
        'github_app_configs',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('github_app_id', sa.String(50), nullable=False),
        sa.Column('slug', sa.String(200), nullable=False),
        sa.Column('html_url', sa.String(500), nullable=False),
        sa.Column('private_key_encrypted', sa.Text(), nullable=False),
        sa.Column('webhook_secret_encrypted', sa.Text(), nullable=False),
        sa.Column('created_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('github_app_configs')
