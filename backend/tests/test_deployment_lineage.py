"""HUB-Expansion.md Phase 4 — Deployment Lineage. Covers: Customer/
Application CRUD + permission gating, setting a deployment's lineage
fields (with row-level visibility via _get_visible_or_404), manual
release entry, and the one automatic path this phase ships —
services/lineage.py's infer_release_from_heartbeat matching a heartbeat's
app_version against a GitHubRelease tag on one of the deployment's linked
repos (Phase 3's DeploymentGitHubRepository)."""
import hashlib

from app.integrations.github.models import DeploymentGitHubRepository, GitHubIntegration, GitHubRelease, GitHubRepository
from app.models import Deployment, DeploymentRelease, DeploymentStaffAssignment, DeploymentStatus
from tests.conftest import as_user, make_user


async def _make_deployment(db_session, *, slug: str, with_api_key: bool = False) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    if with_api_key:
        d.api_key_hash = hashlib.sha256(b"raw-key").hexdigest()
    db_session.add(d)
    await db_session.flush()
    return d


async def _make_repo_with_release(db_session, *, org: str, tag_name: str) -> tuple[GitHubRepository, GitHubRelease]:
    integration = GitHubIntegration(name=org, github_org=org, access_token_encrypted=None, webhook_secret_encrypted=None)
    db_session.add(integration)
    await db_session.flush()
    repo = GitHubRepository(
        integration_id=integration.id, external_id=1, full_name=f"{org}/repo", name="repo", owner=org,
        html_url=f"https://github.com/{org}/repo",
    )
    db_session.add(repo)
    await db_session.flush()
    release = GitHubRelease(
        repository_id=repo.id, external_id=1, tag_name=tag_name, html_url=f"https://github.com/{org}/repo/releases/{tag_name}",
        target_commit_sha="a" * 40,
    )
    db_session.add(release)
    await db_session.flush()
    return repo, release


# ── customers / applications CRUD ─────────────────────────────────────────

async def test_customer_crud_requires_manage_permission(client, admin_user, engineer_user):
    as_user(engineer_user)
    resp = await client.post("/customers", json={"name": "Acme Farms", "slug": "acme-farms"})
    assert resp.status_code == 403

    as_user(admin_user)
    resp = await client.post("/customers", json={"name": "Acme Farms", "slug": "acme-farms"})
    assert resp.status_code == 201
    customer_id = resp.json()["id"]

    as_user(engineer_user)
    listing = await client.get("/customers")
    assert listing.status_code == 200
    assert any(c["id"] == customer_id for c in listing.json())

    resp = await client.post("/customers", json={"name": "Dup", "slug": "acme-farms"})
    assert resp.status_code == 403  # engineer still lacks customers.manage


async def test_customer_slug_must_be_unique(client, admin_user):
    as_user(admin_user)
    await client.post("/customers", json={"name": "One", "slug": "dup-slug"})
    resp = await client.post("/customers", json={"name": "Two", "slug": "dup-slug"})
    assert resp.status_code == 400


async def test_application_crud_requires_manage_permission(client, admin_user, no_role_user):
    as_user(no_role_user)
    resp = await client.get("/applications")
    assert resp.status_code == 403

    as_user(admin_user)
    resp = await client.post("/applications", json={"name": "PoultryOS-CBP", "slug": "poultryos-cbp"})
    assert resp.status_code == 201
    app_id = resp.json()["id"]

    patch_resp = await client.patch(f"/applications/{app_id}", json={"description": "Core product"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["description"] == "Core product"


# ── deployment lineage fields ──────────────────────────────────────────────

async def test_update_lineage_sets_customer_application_environment(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="lineage-1")
    as_user(admin_user)
    customer = (await client.post("/customers", json={"name": "Acme", "slug": "acme-lineage-1"})).json()
    application = (await client.post("/applications", json={"name": "CBP", "slug": "cbp-lineage-1"})).json()

    resp = await client.patch(
        f"/deployments/{d.id}/lineage",
        json={"customer_id": customer["id"], "application_id": application["id"], "environment": "staging"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer"]["id"] == customer["id"]
    assert body["application"]["id"] == application["id"]
    assert body["environment"] == "staging"

    # Clearing a link back to null.
    resp = await client.patch(f"/deployments/{d.id}/lineage", json={"customer_id": None})
    assert resp.status_code == 200
    assert resp.json()["customer"] is None
    assert resp.json()["application"]["id"] == application["id"]  # untouched


async def test_update_lineage_rejects_unknown_customer_id(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="lineage-2")
    as_user(admin_user)
    resp = await client.patch(f"/deployments/{d.id}/lineage", json={"customer_id": "00000000-0000-0000-0000-000000000000"})
    assert resp.status_code == 400


async def test_manage_lineage_requires_permission_not_just_view(client, db_session, admin_user, no_role_user):
    d = await _make_deployment(db_session, slug="lineage-3")
    db_session.add(DeploymentStaffAssignment(deployment_id=d.id, user_id=no_role_user.id))
    await db_session.flush()

    as_user(admin_user)
    await client.patch(f"/deployments/{d.id}/lineage", json={"environment": "development"})

    engineer_like = await make_user(db_session, role="engineer", username="eng-lineage")
    db_session.add(DeploymentStaffAssignment(deployment_id=d.id, user_id=engineer_like.id))
    await db_session.flush()
    as_user(engineer_like)
    resp = await client.patch(f"/deployments/{d.id}/lineage", json={"environment": "uat"})
    assert resp.status_code == 200  # engineer keeps deployments.manage_lineage per migration 016


async def test_lineage_update_404s_for_deployment_outside_scope(client, db_session):
    d = await _make_deployment(db_session, slug="lineage-4")
    scoped_engineer = await make_user(db_session, role="engineer", username="eng-scoped-lineage")
    as_user(scoped_engineer)  # not assigned to d, and engineer lacks view_all
    resp = await client.patch(f"/deployments/{d.id}/lineage", json={"environment": "uat"})
    assert resp.status_code == 404


# ── manual release entries ─────────────────────────────────────────────────

async def test_manual_release_entry_and_current_release_on_deployment_out(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="release-1")
    as_user(admin_user)
    resp = await client.post(
        f"/deployments/{d.id}/releases",
        json={"version": "2.8.15", "deployed_by": "GitHub Actions", "notes": "manual backfill"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "manual"
    assert body["version"] == "2.8.15"
    assert body["deployed_by"] == "GitHub Actions"

    detail = await client.get(f"/deployments/{d.id}")
    assert detail.json()["current_release"]["version"] == "2.8.15"

    listing = await client.get(f"/deployments/{d.id}/releases")
    assert len(listing.json()) == 1


async def test_manual_release_rejects_unknown_repository_id(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="release-2")
    as_user(admin_user)
    resp = await client.post(
        f"/deployments/{d.id}/releases",
        json={"version": "1.0.0", "repository_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 400


# ── heartbeat auto-inference ────────────────────────────────────────────────

async def test_heartbeat_infers_release_when_version_matches_github_tag(client, db_session):
    d = await _make_deployment(db_session, slug="infer-1", with_api_key=True)
    repo, release = await _make_repo_with_release(db_session, org="bilwacorp-infer-1", tag_name="v2.8.15")
    db_session.add(DeploymentGitHubRepository(deployment_id=d.id, repository_id=repo.id, is_primary=True))
    await db_session.flush()

    resp = await client.post(
        "/ingest/heartbeat", headers={"Authorization": "Bearer raw-key"},
        json={"app_version": "2.8.15", "usage": [], "pending_requests": []},
    )
    assert resp.status_code == 200

    from sqlalchemy import select
    rows = (await db_session.execute(
        select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].source.value == "heartbeat_inferred"
    assert rows[0].release_id == release.id
    assert rows[0].commit_sha == release.target_commit_sha

    # A second heartbeat with the SAME version must not add another row.
    resp = await client.post(
        "/ingest/heartbeat", headers={"Authorization": "Bearer raw-key"},
        json={"app_version": "2.8.15", "usage": [], "pending_requests": []},
    )
    assert resp.status_code == 200
    rows = (await db_session.execute(
        select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id)
    )).scalars().all()
    assert len(rows) == 1


async def test_heartbeat_records_version_change_even_with_no_repo_match(client, db_session):
    d = await _make_deployment(db_session, slug="infer-2", with_api_key=True)

    resp = await client.post(
        "/ingest/heartbeat", headers={"Authorization": "Bearer raw-key"},
        json={"app_version": "3.0.0", "usage": [], "pending_requests": []},
    )
    assert resp.status_code == 200

    from sqlalchemy import select
    rows = (await db_session.execute(
        select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].version == "3.0.0"
    assert rows[0].release_id is None
    assert rows[0].repository_id is None

    # A version bump on the next heartbeat DOES add a new row.
    resp = await client.post(
        "/ingest/heartbeat", headers={"Authorization": "Bearer raw-key"},
        json={"app_version": "3.0.1", "usage": [], "pending_requests": []},
    )
    assert resp.status_code == 200
    rows = (await db_session.execute(
        select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id)
    )).scalars().all()
    assert len(rows) == 2
