"""HUB-Expansion.md Phase 4 — Deployment Lineage (app/models.py's Customer/
Application/DeploymentRelease + Deployment.customer_id/application_id/
environment). See docs/adr/ADR-004-deployment-lineage.md for the full
design and docs/architecture/target-state.md's "Landed" diagram for how
this closes the "CUSTOMER — still just Deployment.client_name" gap Phase
3 left open.

Customer/Application are purely additive — Deployment.client_name is
untouched and every existing query/notification/UI keeps reading it
unchanged (see the plan's "DO NOT rewrite the existing application"
rule). This migration backfills one Customer row per distinct
client_name and links every existing Deployment to it, so the lineage
view has something to show immediately instead of every deployment
starting "uncustomered."

Deployment.environment defaults to 'production' — CLAUDE.md: every client
deployment is its own single-tenant, separately hosted instance, so
that's the correct default for 100% of rows that exist today.

DeploymentRelease.repository_id/release_id FK into github_repositories/
github_releases (migration 015) without app/models.py importing that
package's ORM classes — see DeploymentRelease's own docstring.

Seeds customers.view/manage, applications.view/manage, and
deployments.manage_lineage. 'admin' gets all five; 'engineer' gets
customers.view/applications.view (reference data it needs to read, same
tier as github.view) plus deployments.manage_lineage (an action
permission on a resource it already acts on, same "keeps every action
permission" pattern as migrations 011/014's assign_staff/actions.retry) —
not customers.manage/applications.manage, which stay admin-config-only
like staff.create/rbac.manage.

Revision ID: 016_deployment_lineage
Revises: 015_github_integration
Create Date: 2026-09-16
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '016_deployment_lineage'
down_revision: Union[str, None] = '015_github_integration'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

NEW_PERMISSIONS = [
    ("customers", "view", "View the customer directory"),
    ("customers", "manage", "Create/edit customers"),
    ("applications", "view", "View the application catalog"),
    ("applications", "manage", "Create/edit applications"),
    ("deployments", "manage_lineage", "Set a deployment's customer/application/environment and record its current release"),
]
ADMIN_GRANTS = {(r, a) for r, a, _ in NEW_PERMISSIONS}
ENGINEER_GRANTS = {("customers", "view"), ("applications", "view"), ("deployments", "manage_lineage")}


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
        "DO $$ BEGIN CREATE TYPE deploymentenvironment AS ENUM "
        "('production', 'staging', 'development', 'uat'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE deploymentreleasesource AS ENUM "
        "('manual', 'heartbeat_inferred', 'github_actions', 'ci_cd'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    environment_type = postgresql.ENUM('production', 'staging', 'development', 'uat', name='deploymentenvironment', create_type=False)
    release_source_type = postgresql.ENUM('manual', 'heartbeat_inferred', 'github_actions', 'ci_cd', name='deploymentreleasesource', create_type=False)

    op.create_table(
        'customers',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False, unique=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'applications',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False, unique=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.add_column('deployments', sa.Column('customer_id', uuid_pk, sa.ForeignKey('customers.id'), nullable=True))
    op.add_column('deployments', sa.Column('application_id', uuid_pk, sa.ForeignKey('applications.id'), nullable=True))
    op.add_column('deployments', sa.Column('environment', environment_type, nullable=False, server_default='production'))
    op.create_index('ix_deployments_customer_id', 'deployments', ['customer_id'])
    op.create_index('ix_deployments_application_id', 'deployments', ['application_id'])

    # Backfill: one Customer per distinct client_name, every existing
    # Deployment linked to its own. Slug is a simple lowercased/dashed
    # derivation of client_name with a short id suffix to guarantee
    # uniqueness even if two clients share a display name.
    rows = conn.execute(sa.text("SELECT id, client_name FROM deployments")).all()
    customer_id_by_name: dict[str, str] = {}
    for dep_id, client_name in rows:
        if client_name not in customer_id_by_name:
            new_id = str(uuid.uuid4())
            base_slug = "".join(c if c.isalnum() else "-" for c in client_name.lower()).strip("-") or "customer"
            slug = f"{base_slug}-{new_id[:8]}"
            conn.execute(
                sa.text("INSERT INTO customers (id, name, slug, created_at) VALUES (:id, :name, :slug, now())"),
                {"id": new_id, "name": client_name, "slug": slug},
            )
            customer_id_by_name[client_name] = new_id
        conn.execute(
            sa.text("UPDATE deployments SET customer_id = :cid WHERE id = :did"),
            {"cid": customer_id_by_name[client_name], "did": dep_id},
        )

    op.create_table(
        'deployment_releases',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('deployment_id', uuid_pk, sa.ForeignKey('deployments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.String(50), nullable=True),
        sa.Column('repository_id', uuid_pk, sa.ForeignKey('github_repositories.id'), nullable=True),
        sa.Column('release_id', uuid_pk, sa.ForeignKey('github_releases.id'), nullable=True),
        sa.Column('commit_sha', sa.String(40), nullable=True),
        sa.Column('source', release_source_type, nullable=False),
        sa.Column('deployed_by', sa.String(200), nullable=True),
        sa.Column('deployed_at', sa.DateTime, nullable=False),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_deployment_releases_deployment_id', 'deployment_releases', ['deployment_id'])

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

    op.drop_index('ix_deployment_releases_deployment_id', table_name='deployment_releases')
    op.drop_table('deployment_releases')

    op.drop_index('ix_deployments_application_id', table_name='deployments')
    op.drop_index('ix_deployments_customer_id', table_name='deployments')
    op.drop_column('deployments', 'environment')
    op.drop_column('deployments', 'application_id')
    op.drop_column('deployments', 'customer_id')

    op.drop_table('applications')
    op.drop_table('customers')

    op.execute("DROP TYPE IF EXISTS deploymentreleasesource")
    op.execute("DROP TYPE IF EXISTS deploymentenvironment")
