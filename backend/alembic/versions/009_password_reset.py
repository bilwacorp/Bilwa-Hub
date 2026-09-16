"""users.password_reset_token_hash / password_reset_expires_at — self-service
"forgot password" (see api/routers/auth.py's forgot-password/reset-password).

Revision ID: 009_password_reset
Revises: 008_expiry_reminder
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '009_password_reset'
down_revision: Union[str, None] = '008_expiry_reminder'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password_reset_token_hash', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('password_reset_expires_at', sa.DateTime(), nullable=True))
    op.create_index('ix_users_password_reset_token_hash', 'users', ['password_reset_token_hash'])


def downgrade() -> None:
    op.drop_index('ix_users_password_reset_token_hash', table_name='users')
    op.drop_column('users', 'password_reset_expires_at')
    op.drop_column('users', 'password_reset_token_hash')
