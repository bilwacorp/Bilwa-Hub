"""GitHub credential handling — token/secret never leave the backend in
plaintext, never appear in an API response, and round-trip correctly
through services/crypto.py."""
from sqlalchemy import select

from app.integrations.github.models import GitHubIntegration
from app.services import crypto
from tests.conftest import as_user


async def test_create_integration_stores_encrypted_not_plaintext(client, db_session, admin_user):
    as_user(admin_user)
    resp = await client.post("/github/integrations", json={
        "name": "Acme", "github_org": "acme", "access_token": "ghp_supersecret", "webhook_secret": "whsec_supersecret",
    })
    assert resp.status_code == 201
    body = resp.json()

    # Never in the response...
    assert "access_token" not in body
    assert "webhook_secret" not in body
    assert "supersecret" not in str(body)
    assert body["has_access_token"] is True
    assert body["has_webhook_secret"] is True

    # ...but recoverable server-side, and not stored as plaintext.
    row = (await db_session.execute(select(GitHubIntegration).where(GitHubIntegration.id == body["id"]))).scalar_one()
    assert row.access_token_encrypted != "ghp_supersecret"
    assert row.webhook_secret_encrypted != "whsec_supersecret"
    assert crypto.decrypt(row.access_token_encrypted) == "ghp_supersecret"
    assert crypto.decrypt(row.webhook_secret_encrypted) == "whsec_supersecret"


async def test_list_integrations_never_returns_credentials(client, db_session, admin_user):
    as_user(admin_user)
    await client.post("/github/integrations", json={
        "name": "Acme", "github_org": "acme2", "access_token": "ghp_verysecret", "webhook_secret": "whsec_verysecret",
    })
    resp = await client.get("/github/integrations")
    assert resp.status_code == 200
    assert "ghp_verysecret" not in resp.text
    assert "whsec_verysecret" not in resp.text


async def test_update_integration_without_new_secret_leaves_stored_one_alone(client, db_session, admin_user):
    as_user(admin_user)
    create = await client.post("/github/integrations", json={
        "name": "Acme", "github_org": "acme3", "access_token": "ghp_original", "webhook_secret": "whsec_original",
    })
    integration_id = create.json()["id"]

    rename = await client.patch(f"/github/integrations/{integration_id}", json={"name": "Acme Renamed"})
    assert rename.status_code == 200
    assert rename.json()["name"] == "Acme Renamed"

    row = await db_session.get(GitHubIntegration, integration_id)
    assert crypto.decrypt(row.access_token_encrypted) == "ghp_original"


async def test_update_integration_can_rotate_the_token(client, db_session, admin_user):
    as_user(admin_user)
    create = await client.post("/github/integrations", json={
        "name": "Acme", "github_org": "acme4", "access_token": "ghp_old", "webhook_secret": "whsec_old",
    })
    integration_id = create.json()["id"]

    await client.patch(f"/github/integrations/{integration_id}", json={"access_token": "ghp_new"})
    row = await db_session.get(GitHubIntegration, integration_id)
    assert crypto.decrypt(row.access_token_encrypted) == "ghp_new"
