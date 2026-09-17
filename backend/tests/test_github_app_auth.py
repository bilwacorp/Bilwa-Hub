"""app/integrations/github/app_auth.py — App JWT minting, installation
token caching/refresh, and installation upsert idempotency. See
docs/adr/ADR-004-github-app-auth.md for the design this verifies."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.integrations.github import app_auth
from app.integrations.github.client import GitHubApiError
from app.integrations.github.models import GitHubAuthMode, GitHubIntegration, GitHubIntegrationStatus
from app.services import crypto


def _rsa_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def app_configured(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_APP_ID", "12345")
    monkeypatch.setattr(settings, "GITHUB_APP_SLUG", "bilwacorp-hub")
    monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY", _rsa_private_key_pem())


def test_app_jwt_unconfigured_raises_without_signing_anything():
    with pytest.raises(GitHubApiError, match="not configured"):
        app_auth._app_jwt()


def test_app_jwt_is_a_valid_rs256_token_with_correct_claims(app_configured):
    token = app_auth._app_jwt()
    claims = jwt.get_unverified_claims(token)
    assert claims["iss"] == "12345"
    assert claims["exp"] - claims["iat"] > 60  # backdated iat + forward exp, not a zero-width window
    assert claims["exp"] - claims["iat"] <= 660  # under GitHub's 10-minute cap (9 min + 60s skew allowance)


async def test_get_installation_token_mints_and_caches_a_fresh_token(app_configured, db_session):
    integration = GitHubIntegration(
        name="Acme", github_org="acme", auth_mode=GitHubAuthMode.github_app, installation_id=999,
        status=GitHubIntegrationStatus.connected,
    )
    db_session.add(integration)
    await db_session.flush()

    with patch(
        "app.integrations.github.app_auth._app_request", new_callable=AsyncMock,
        return_value={"token": "ghs_minted", "expires_at": "2099-01-01T00:00:00Z"},
    ) as mock_request:
        token = await app_auth.get_installation_token(db_session, integration)

    assert token == "ghs_minted"
    mock_request.assert_awaited_once_with("POST", "/app/installations/999/access_tokens")
    assert crypto.decrypt(integration.access_token_encrypted) == "ghs_minted"
    assert integration.access_token_expires_at.year == 2099


async def test_get_installation_token_reuses_cache_without_a_new_request(app_configured, db_session):
    integration = GitHubIntegration(
        name="Acme", github_org="acme", auth_mode=GitHubAuthMode.github_app, installation_id=1000,
        status=GitHubIntegrationStatus.connected,
        access_token_encrypted=crypto.encrypt("ghs_cached"),
        access_token_expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db_session.add(integration)
    await db_session.flush()

    with patch("app.integrations.github.app_auth._app_request", new_callable=AsyncMock) as mock_request:
        token = await app_auth.get_installation_token(db_session, integration)

    assert token == "ghs_cached"
    mock_request.assert_not_awaited()


async def test_get_installation_token_refreshes_when_close_to_expiry(app_configured, db_session):
    """Within the refresh buffer counts as expired, not just literally past it."""
    integration = GitHubIntegration(
        name="Acme", github_org="acme", auth_mode=GitHubAuthMode.github_app, installation_id=1001,
        status=GitHubIntegrationStatus.connected,
        access_token_encrypted=crypto.encrypt("ghs_stale"),
        access_token_expires_at=datetime.utcnow() + timedelta(seconds=30),
    )
    db_session.add(integration)
    await db_session.flush()

    with patch(
        "app.integrations.github.app_auth._app_request", new_callable=AsyncMock,
        return_value={"token": "ghs_refreshed", "expires_at": "2099-01-01T00:00:00Z"},
    ):
        token = await app_auth.get_installation_token(db_session, integration)

    assert token == "ghs_refreshed"


async def test_get_installation_token_without_installation_id_raises(db_session):
    integration = GitHubIntegration(name="Acme", github_org="acme", auth_mode=GitHubAuthMode.github_app)
    db_session.add(integration)
    await db_session.flush()
    with pytest.raises(GitHubApiError, match="no GitHub App installation"):
        await app_auth.get_installation_token(db_session, integration)


async def test_upsert_installation_creates_then_reuses_the_same_row(db_session):
    first = await app_auth.upsert_installation(db_session, 42, "acme-org", None)
    second = await app_auth.upsert_installation(db_session, 42, "acme-org", None)
    assert first.id == second.id

    rows = (
        await db_session.execute(select(GitHubIntegration).where(GitHubIntegration.installation_id == 42))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].auth_mode == GitHubAuthMode.github_app
    assert rows[0].status == GitHubIntegrationStatus.connected
