"""Permission enforcement for the four github.* permissions, and
row-level visibility for the one deployment-scoped GitHub surface
(GET /deployments/{id}/github) — HUB-Expansion.md Phase 3's explicit
"unauthorized deployment IDs must follow the existing non-disclosing
behavior" requirement."""
from unittest.mock import patch

from app.integrations.github.models import DeploymentGitHubRepository, GitHubIntegration, GitHubRepository
from app.models import Deployment, DeploymentStaffAssignment, DeploymentStatus
from tests.conftest import as_user, make_user


async def _make_integration(db_session, *, org: str) -> GitHubIntegration:
    integration = GitHubIntegration(name=org, github_org=org, access_token_encrypted=None, webhook_secret_encrypted=None)
    db_session.add(integration)
    await db_session.flush()
    return integration


async def _make_repo(db_session, integration: GitHubIntegration, *, full_name: str, external_id: int) -> GitHubRepository:
    repo = GitHubRepository(
        integration_id=integration.id, external_id=external_id, full_name=full_name, name=full_name.split("/")[-1],
        owner=full_name.split("/")[0], html_url=f"https://github.com/{full_name}",
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _make_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    db_session.add(d)
    await db_session.flush()
    return d


async def test_github_view_required_to_list_repositories(client, no_role_user):
    as_user(no_role_user)
    resp = await client.get("/github/repositories")
    assert resp.status_code == 403


async def test_engineer_has_view_but_not_manage(client, db_session, engineer_user):
    as_user(engineer_user)
    assert (await client.get("/github/repositories")).status_code == 200
    create = await client.post("/github/integrations", json={
        "name": "x", "github_org": "x-org", "access_token": "t", "webhook_secret": "s",
    })
    assert create.status_code == 403


async def test_engineer_has_sync_and_test_connection(client, db_session, engineer_user):
    """Diagnostic/action-shaped, same tier as deployments.check_health —
    see core/permissions.py's comment on GITHUB_TEST_CONNECTION/GITHUB_SYNC."""
    integration = await _make_integration(db_session, org="acme-eng")
    repo = await _make_repo(db_session, integration, full_name="acme-eng/repo", external_id=1)
    as_user(engineer_user)
    # test-connection hits the real (fake-token) GitHub API and fails —
    # the point here is authorization (not 403), not connectivity.
    assert (await client.post(f"/github/integrations/{integration.id}/test-connection")).status_code != 403
    # sync just enqueues a Celery task — patched so this test doesn't
    # depend on a real broker being reachable to prove an authorization
    # fact.
    with patch("app.integrations.github.api.sync_repository_task.delay"):
        assert (await client.post(f"/github/repositories/{repo.id}/sync")).status_code != 403


async def test_admin_has_manage(client, admin_user):
    as_user(admin_user)
    resp = await client.post("/github/integrations", json={
        "name": "Acme", "github_org": "acme-admin", "access_token": "t", "webhook_secret": "s",
    })
    assert resp.status_code == 201


async def test_deployment_github_info_requires_deployments_view(client, no_role_user, db_session):
    integration = await _make_integration(db_session, org="acme-rlv")
    d = await _make_deployment(db_session, slug="rlv-gh-1")
    as_user(no_role_user)
    resp = await client.get(f"/deployments/{d.id}/github")
    assert resp.status_code == 403


async def test_unassigned_engineer_cannot_see_deployment_github_info(client, db_session, engineer_user):
    integration = await _make_integration(db_session, org="acme-rlv2")
    d = await _make_deployment(db_session, slug="rlv-gh-2")
    as_user(engineer_user)
    resp = await client.get(f"/deployments/{d.id}/github")
    assert resp.status_code == 404


async def test_assigned_engineer_sees_deployment_github_info(client, db_session, engineer_user):
    integration = await _make_integration(db_session, org="acme-rlv3")
    repo = await _make_repo(db_session, integration, full_name="acme-rlv3/repo", external_id=3)
    d = await _make_deployment(db_session, slug="rlv-gh-3")
    db_session.add(DeploymentStaffAssignment(deployment_id=d.id, user_id=engineer_user.id))
    db_session.add(DeploymentGitHubRepository(deployment_id=d.id, repository_id=repo.id, is_primary=True))
    await db_session.flush()

    as_user(engineer_user)
    resp = await client.get(f"/deployments/{d.id}/github")
    assert resp.status_code == 200
    body = resp.json()
    assert body["primary_repository"]["full_name"] == "acme-rlv3/repo"


async def test_engineer_cannot_see_github_info_for_someone_elses_deployment(client, db_session, engineer_user):
    integration = await _make_integration(db_session, org="acme-rlv4")
    mine = await _make_deployment(db_session, slug="rlv-gh-4a")
    other = await _make_deployment(db_session, slug="rlv-gh-4b")
    db_session.add(DeploymentStaffAssignment(deployment_id=mine.id, user_id=engineer_user.id))
    await db_session.flush()

    as_user(engineer_user)
    assert (await client.get(f"/deployments/{mine.id}/github")).status_code == 200
    assert (await client.get(f"/deployments/{other.id}/github")).status_code == 404


async def test_github_manage_only_permission_cannot_use_deployments_view_endpoint(client, db_session):
    """github.manage alone doesn't grant access to the deployment-scoped
    view — that route is gated by DEPLOYMENTS_VIEW, not any github.*
    permission (see api/routers/deployments.py's comment)."""
    from app.services import rbac
    role = "github_manage_only"
    await rbac.set_role_permissions(role, [("github", "view"), ("github", "manage")])
    user = await make_user(db_session, role=role)
    d = await _make_deployment(db_session, slug="rlv-gh-5")

    as_user(user)
    resp = await client.get(f"/deployments/{d.id}/github")
    assert resp.status_code == 403
