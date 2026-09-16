"""HUB-Expansion.md Phase 3 — GitHub integration (app/integrations/github/).
Creates the eight GitHub domain tables (native Postgres enum types only
for the three HUB-owned closed-set status/mode columns — GitHubPullRequest
.state/GitHubIssue.state stay plain strings, since those mirror GitHub's
own external vocabulary; see docs/integrations/github.md's "Enums vs
strings"). Seeds the new github.view/github.manage/github.test_connection
/github.sync permissions: 'admin' gets all four, 'engineer' gets view/
test_connection/sync (same "keeps action/diagnostic permissions, not
admin-config ones" pattern as migrations 011/012/013/014).

Revision ID: 015_github_integration
Revises: 014_deployment_action_executions
Create Date: 2026-09-16
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '015_github_integration'
down_revision: Union[str, None] = '014_deployment_action_executions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

NEW_PERMISSIONS = [
    ("github", "view", "View configured GitHub integrations, repositories, pull requests, issues, and releases"),
    ("github", "manage", "Create/edit a GitHub integration's credentials"),
    ("github", "test_connection", "Test a GitHub integration's stored credentials"),
    ("github", "sync", "Trigger a GitHub repository sync"),
]
ADMIN_GRANTS = {(r, a) for r, a, _ in NEW_PERMISSIONS}
ENGINEER_GRANTS = {("github", "view"), ("github", "test_connection"), ("github", "sync")}


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

    op.execute("DO $$ BEGIN CREATE TYPE githubauthmode AS ENUM ('pat', 'github_app'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.execute(
        "DO $$ BEGIN CREATE TYPE githubintegrationstatus AS ENUM ('connected', 'disconnected', 'error'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE githubwebhookeventstatus AS ENUM ('received', 'processing', 'processed', 'failed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    auth_mode = postgresql.ENUM('pat', 'github_app', name='githubauthmode', create_type=False)
    integration_status = postgresql.ENUM('connected', 'disconnected', 'error', name='githubintegrationstatus', create_type=False)
    webhook_status = postgresql.ENUM('received', 'processing', 'processed', 'failed', name='githubwebhookeventstatus', create_type=False)

    op.create_table(
        'github_integrations',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('github_org', sa.String(200), nullable=False, unique=True),
        sa.Column('auth_mode', auth_mode, nullable=False, server_default='pat'),
        sa.Column('access_token_encrypted', sa.Text, nullable=True),
        sa.Column('webhook_secret_encrypted', sa.Text, nullable=True),
        sa.Column('status', integration_status, nullable=False, server_default='disconnected'),
        sa.Column('last_synced_at', sa.DateTime, nullable=True),
        sa.Column('last_webhook_at', sa.DateTime, nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('last_error_at', sa.DateTime, nullable=True),
        sa.Column('created_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'github_repositories',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('integration_id', uuid_pk, sa.ForeignKey('github_integrations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.Integer, nullable=False, unique=True),
        sa.Column('full_name', sa.String(300), nullable=False, unique=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('owner', sa.String(200), nullable=False),
        sa.Column('default_branch', sa.String(200), nullable=False, server_default='main'),
        sa.Column('html_url', sa.String(500), nullable=False),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('last_synced_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_github_repositories_integration_id', 'github_repositories', ['integration_id'])

    op.create_table(
        'deployment_github_repositories',
        sa.Column('deployment_id', uuid_pk, sa.ForeignKey('deployments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('repository_id', uuid_pk, sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('is_primary', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_deployment_github_repositories_repository_id', 'deployment_github_repositories', ['repository_id'])

    op.create_table(
        'github_pull_requests',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('repository_id', uuid_pk, sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.Integer, nullable=False),
        sa.Column('number', sa.Integer, nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('is_draft', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('author_login', sa.String(200), nullable=True),
        sa.Column('html_url', sa.String(500), nullable=False),
        sa.Column('merge_commit_sha', sa.String(40), nullable=True),
        sa.Column('opened_at', sa.DateTime, nullable=False),
        sa.Column('merged_at', sa.DateTime, nullable=True),
        sa.Column('closed_at', sa.DateTime, nullable=True),
        sa.Column('github_updated_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('repository_id', 'number', name='uq_github_pr_repo_number'),
    )
    op.create_index('ix_github_pull_requests_repository_id', 'github_pull_requests', ['repository_id'])

    op.create_table(
        'github_issues',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('repository_id', uuid_pk, sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.Integer, nullable=False),
        sa.Column('number', sa.Integer, nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('author_login', sa.String(200), nullable=True),
        sa.Column('html_url', sa.String(500), nullable=False),
        sa.Column('opened_at', sa.DateTime, nullable=False),
        sa.Column('closed_at', sa.DateTime, nullable=True),
        sa.Column('github_updated_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('repository_id', 'number', name='uq_github_issue_repo_number'),
    )
    op.create_index('ix_github_issues_repository_id', 'github_issues', ['repository_id'])

    op.create_table(
        'github_releases',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('repository_id', uuid_pk, sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.Integer, nullable=False),
        sa.Column('tag_name', sa.String(200), nullable=False),
        sa.Column('name', sa.String(300), nullable=True),
        sa.Column('html_url', sa.String(500), nullable=False),
        sa.Column('target_commit_sha', sa.String(40), nullable=True),
        sa.Column('is_prerelease', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('is_draft', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('published_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('repository_id', 'tag_name', name='uq_github_release_repo_tag'),
    )
    op.create_index('ix_github_releases_repository_id', 'github_releases', ['repository_id'])

    op.create_table(
        'github_commits',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('repository_id', uuid_pk, sa.ForeignKey('github_repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sha', sa.String(40), nullable=False),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('author_name', sa.String(200), nullable=True),
        sa.Column('author_email', sa.String(300), nullable=True),
        sa.Column('author_login', sa.String(200), nullable=True),
        sa.Column('html_url', sa.String(500), nullable=False),
        sa.Column('committed_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('repository_id', 'sha', name='uq_github_commit_repo_sha'),
    )
    op.create_index('ix_github_commits_repository_id', 'github_commits', ['repository_id'])

    op.create_table(
        'github_webhook_events',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('integration_id', uuid_pk, sa.ForeignKey('github_integrations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('delivery_id', sa.String(100), nullable=False, unique=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('payload', postgresql.JSON, nullable=False),
        sa.Column('signature_valid', sa.Boolean, nullable=False),
        sa.Column('status', webhook_status, nullable=False, server_default='received'),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('correlation_id', uuid_pk, nullable=False),
        sa.Column('received_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('processed_at', sa.DateTime, nullable=True),
    )
    op.create_index('ix_github_webhook_events_integration_id', 'github_webhook_events', ['integration_id'])

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

    op.drop_index('ix_github_webhook_events_integration_id', table_name='github_webhook_events')
    op.drop_table('github_webhook_events')
    op.drop_index('ix_github_commits_repository_id', table_name='github_commits')
    op.drop_table('github_commits')
    op.drop_index('ix_github_releases_repository_id', table_name='github_releases')
    op.drop_table('github_releases')
    op.drop_index('ix_github_issues_repository_id', table_name='github_issues')
    op.drop_table('github_issues')
    op.drop_index('ix_github_pull_requests_repository_id', table_name='github_pull_requests')
    op.drop_table('github_pull_requests')
    op.drop_index('ix_deployment_github_repositories_repository_id', table_name='deployment_github_repositories')
    op.drop_table('deployment_github_repositories')
    op.drop_index('ix_github_repositories_integration_id', table_name='github_repositories')
    op.drop_table('github_repositories')
    op.drop_table('github_integrations')

    op.execute("DROP TYPE IF EXISTS githubwebhookeventstatus")
    op.execute("DROP TYPE IF EXISTS githubintegrationstatus")
    op.execute("DROP TYPE IF EXISTS githubauthmode")
