"""HUB-Expansion.md Phase 11 — Integration Center. api/routers/
integrations.py never returns a secret and always includes all five
cards (github/cicd/email/whatsapp/monitoring) regardless of what's
configured."""
from app.integrations.github.models import GitHubIntegration, GitHubIntegrationStatus
from tests.conftest import as_user


async def test_no_role_user_is_forbidden(client, no_role_user):
    as_user(no_role_user)
    resp = await client.get("/integrations")
    assert resp.status_code == 403


async def test_five_cards_present_with_no_integrations_configured(client, admin_user):
    as_user(admin_user)
    resp = await client.get("/integrations")
    assert resp.status_code == 200
    keys = [c["key"] for c in resp.json()]
    assert "github" in keys
    assert "cicd" in keys
    assert "email" in keys
    assert "whatsapp" in keys
    assert "monitoring" in keys
    for card in resp.json():
        assert card["status"] == "not_configured"
        assert card["connected"] is False


async def test_configured_github_integration_shows_its_own_card(client, db_session, admin_user):
    integration = GitHubIntegration(
        name="Acme Org", github_org="acme", status=GitHubIntegrationStatus.connected,
        access_token_encrypted=None, webhook_secret_encrypted=None,
    )
    db_session.add(integration)
    await db_session.flush()

    as_user(admin_user)
    resp = await client.get("/integrations")
    github_cards = [c for c in resp.json() if c["key"].startswith("github")]
    assert len(github_cards) == 1
    assert github_cards[0]["name"] == "Acme Org"
    assert github_cards[0]["status"] == "connected"
    assert github_cards[0]["connected"] is True
    assert github_cards[0]["integration_id"] == str(integration.id)
    # Never exposes a secret.
    assert "access_token" not in github_cards[0]
    assert "webhook_secret" not in github_cards[0]
