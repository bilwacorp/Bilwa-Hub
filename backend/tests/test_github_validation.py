"""Input validation on the one GitHub field that gets interpolated into a
URL path (client.py's get_repository builds "/repos/{full_name}") — a
strict pattern, not just length limits, is the server-side guard against
a crafted value being anything other than an "owner/repo" name. Also
confirms a 422 here is FastAPI's normal Pydantic error-list shape, not a
500 — the same shape frontend/src/lib/utils.ts's errorMessage() already
knows how to render safely (see the earlier 422-toast-crash fix)."""
from tests.conftest import as_user


async def test_malformed_full_name_is_rejected_with_422_not_500(client, db_session, admin_user):
    integration_row = await _make_integration_row(db_session)
    as_user(admin_user)
    resp = await client.post(f"/github/integrations/{integration_row}/repositories", json={"full_name": "not-a-valid-owner-repo"})
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], list)  # FastAPI's Pydantic error-list shape


async def test_full_name_with_path_traversal_like_content_is_rejected(client, db_session, admin_user):
    integration_row = await _make_integration_row(db_session)
    as_user(admin_user)
    resp = await client.post(f"/github/integrations/{integration_row}/repositories", json={"full_name": "../../etc/passwd"})
    assert resp.status_code == 422


async def test_valid_full_name_passes_validation(client, db_session, admin_user):
    """Doesn't assert success (no real GitHub token) — just that a
    well-formed value clears validation and reaches the (failing, for
    lack of real credentials) GitHub call, i.e. never a 422."""
    integration_row = await _make_integration_row(db_session)
    as_user(admin_user)
    resp = await client.post(f"/github/integrations/{integration_row}/repositories", json={"full_name": "octocat/Hello-World"})
    assert resp.status_code != 422


async def _make_integration_row(db_session) -> str:
    from app.integrations.github.models import GitHubIntegration
    from app.services import crypto
    integration = GitHubIntegration(name="x", github_org="x-org-validation", access_token_encrypted=crypto.encrypt("fake"))
    db_session.add(integration)
    await db_session.flush()
    return str(integration.id)
