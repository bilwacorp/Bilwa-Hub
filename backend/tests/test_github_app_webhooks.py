"""The GitHub App's single-URL webhook endpoint (POST /github/app/webhooks)
and the install-url/callback endpoints — see docs/adr/ADR-004-github-app-
auth.md. Mirrors test_github_webhooks.py's style for the per-integration
endpoint; the key differences under test here are: one shared secret
(not per-integration), resolving the target integration from
payload["installation"]["id"] instead of a URL path segment, and the
`installation` event provisioning/deprovisioning the integration row
itself."""
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.core.config import settings
from app.integrations.github import app_auth
from app.integrations.github.models import GitHubIntegration, GitHubIntegrationStatus, GitHubRepository, GitHubWebhookEvent
from app.integrations.github.webhooks import process_webhook_event
from app.models import OperationalEvent
from tests.conftest import as_user

_SECRET = "app-whsec-test"


def _configure_app_env(monkeypatch, *, webhook_secret: str = _SECRET) -> None:
    """_load_app_credentials' env-fallback only kicks in once GITHUB_APP_ID
    AND GITHUB_APP_PRIVATE_KEY are both set (docs/adr/ADR-004-github-app-
    auth.md decision #9) — setting the webhook secret alone isn't
    "configured" any more than it was before this DB-first refactor."""
    monkeypatch.setattr(settings, "GITHUB_APP_ID", "12345")
    monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY", "not-a-real-key-but-non-empty")
    monkeypatch.setattr(settings, "GITHUB_APP_WEBHOOK_SECRET", webhook_secret)


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _headers(event_type: str, delivery_id: str) -> dict:
    return {"Content-Type": "application/json", "X-GitHub-Event": event_type, "X-GitHub-Delivery": delivery_id}


INSTALLATION_PAYLOAD = {
    "action": "created",
    "installation": {"id": 7001, "account": {"login": "acme-org"}},
}

ISSUE_PAYLOAD = {
    "action": "opened",
    "installation": {"id": 7002, "account": {"login": "acme-org2"}},
    "issue": {
        "id": 601, "number": 3, "title": "Bug", "state": "open", "user": {"login": "octocat"},
        "html_url": "https://github.com/acme/repo/issues/3",
        "created_at": "2026-09-16T10:00:00Z", "updated_at": "2026-09-16T10:00:00Z", "closed_at": None,
    },
    "repository": {
        "id": 8001, "full_name": "acme/repo", "name": "repo", "owner": {"login": "acme"},
        "default_branch": "main", "html_url": "https://github.com/acme/repo",
    },
    "sender": {"login": "octocat"},
}


async def test_installation_created_provisions_integration(client, db_session, monkeypatch):
    _configure_app_env(monkeypatch)
    body = json.dumps(INSTALLATION_PAYLOAD).encode()
    resp = await client.post("/github/app/webhooks", content=body, headers={**_headers("installation", "a-1"), "X-Hub-Signature-256": _sign(body)})
    assert resp.status_code == 202

    integration = (
        await db_session.execute(select(GitHubIntegration).where(GitHubIntegration.installation_id == 7001))
    ).scalar_one()
    assert integration.auth_mode.value == "github_app"
    assert integration.status == GitHubIntegrationStatus.connected
    assert integration.github_org == "acme-org"

    events = (await db_session.execute(select(OperationalEvent).where(OperationalEvent.event_type == "github.app_installed"))).scalars().all()
    assert len(events) == 1


async def test_installation_deleted_disconnects_integration(client, db_session, monkeypatch):
    _configure_app_env(monkeypatch)
    from app.integrations.github import app_auth
    integration = await app_auth.upsert_installation(db_session, 7003, "acme-org3", None)
    await db_session.commit()

    payload = {"action": "deleted", "installation": {"id": 7003, "account": {"login": "acme-org3"}}}
    body = json.dumps(payload).encode()
    resp = await client.post("/github/app/webhooks", content=body, headers={**_headers("installation", "a-2"), "X-Hub-Signature-256": _sign(body)})
    assert resp.status_code == 202  # a real delivery now, routed through _accept_webhook_delivery

    await db_session.refresh(integration)
    assert integration.status == GitHubIntegrationStatus.disconnected


async def test_wrong_shared_secret_is_rejected(client, monkeypatch):
    _configure_app_env(monkeypatch)
    body = json.dumps(INSTALLATION_PAYLOAD).encode()
    resp = await client.post(
        "/github/app/webhooks", content=body,
        headers={**_headers("installation", "a-3"), "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert resp.status_code == 401


async def test_unconfigured_secret_rejects_every_delivery(client, monkeypatch):
    _configure_app_env(monkeypatch, webhook_secret="")
    body = json.dumps(INSTALLATION_PAYLOAD).encode()
    resp = await client.post(
        "/github/app/webhooks", content=body,
        headers={**_headers("installation", "a-4"), "X-Hub-Signature-256": _sign(body)},
    )
    assert resp.status_code == 401


async def test_domain_event_self_heals_missing_integration_and_enqueues_processing(client, db_session, monkeypatch):
    """An issues/pull_request/etc. delivery for an installation HUB has
    never seen (e.g. arrived before the `installation` webhook) still
    provisions the integration and gets processed — see
    docs/adr/ADR-004-github-app-auth.md decision 3."""
    _configure_app_env(monkeypatch)
    body = json.dumps(ISSUE_PAYLOAD).encode()
    with patch("app.integrations.github.webhook_api.process_webhook_event_task.delay") as mock_delay:
        resp = await client.post("/github/app/webhooks", content=body, headers={**_headers("issues", "a-5"), "X-Hub-Signature-256": _sign(body)})
    assert resp.status_code == 202
    mock_delay.assert_called_once()

    integration = (
        await db_session.execute(select(GitHubIntegration).where(GitHubIntegration.installation_id == 7002))
    ).scalar_one()
    webhook_event = (await db_session.execute(select(GitHubWebhookEvent).where(GitHubWebhookEvent.delivery_id == "a-5"))).scalar_one()
    assert webhook_event.integration_id == integration.id


async def test_duplicate_delivery_is_idempotent(client, monkeypatch):
    _configure_app_env(monkeypatch)
    body = json.dumps(ISSUE_PAYLOAD).encode()
    headers = {**_headers("issues", "a-6"), "X-Hub-Signature-256": _sign(body)}
    first = await client.post("/github/app/webhooks", content=body, headers=headers)
    assert first.status_code == 202
    second = await client.post("/github/app/webhooks", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate_delivery"


async def test_install_url_requires_github_manage(client, engineer_user):
    as_user(engineer_user)
    resp = await client.get("/github/app/install-url")
    assert resp.status_code == 403


async def test_install_url_400s_when_app_not_configured(client, admin_user, monkeypatch):
    as_user(admin_user)
    monkeypatch.setattr(settings, "GITHUB_APP_ID", "")
    resp = await client.get("/github/app/install-url")
    assert resp.status_code == 400


async def test_install_url_returns_a_github_url_when_configured(client, admin_user, monkeypatch):
    as_user(admin_user)
    monkeypatch.setattr(settings, "GITHUB_APP_ID", "12345")
    monkeypatch.setattr(settings, "GITHUB_APP_SLUG", "bilwacorp-hub")
    monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY", "irrelevant-for-this-endpoint")
    resp = await client.get("/github/app/install-url")
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://github.com/apps/bilwacorp-hub/installations/new?state=")


async def test_status_reflects_configuration(client, admin_user, monkeypatch):
    as_user(admin_user)
    resp = await client.get("/github/app/status")
    assert resp.json() == {"configured": False}

    monkeypatch.setattr(settings, "GITHUB_APP_ID", "12345")
    monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY", "irrelevant-for-this-endpoint")
    resp = await client.get("/github/app/status")
    assert resp.json() == {"configured": True}


async def test_manifest_requires_github_manage(client, engineer_user):
    as_user(engineer_user)
    resp = await client.post("/github/app/manifest", json={})
    assert resp.status_code == 403


async def test_manifest_is_well_formed_for_a_personal_account(client, admin_user):
    as_user(admin_user)
    resp = await client.post("/github/app/manifest", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_url"] == "https://github.com/settings/apps/new"
    manifest = body["manifest"]
    assert manifest["hook_attributes"]["url"] == "http://test/api/v1/github/app/webhooks"
    assert manifest["redirect_url"] == "http://test/api/v1/github/app/manifest-callback"
    assert manifest["setup_url"] == "http://test/api/v1/github/app/callback"
    assert manifest["name"] == "BilwaCorp Fleet Hub"
    assert manifest["default_permissions"]["contents"] == "read"
    assert "issues" in manifest["default_events"]


async def test_manifest_target_url_is_org_scoped_when_github_org_given(client, admin_user):
    as_user(admin_user)
    resp = await client.post("/github/app/manifest", json={"name": "Custom Name", "github_org": "bilwacorp"})
    body = resp.json()
    assert body["target_url"] == "https://github.com/organizations/bilwacorp/settings/apps/new"
    assert body["manifest"]["name"] == "Custom Name"


async def test_manifest_callback_requires_authentication(client):
    resp = await client.get("/github/app/manifest-callback", params={"code": "whatever"})
    assert resp.status_code == 401


async def test_manifest_callback_persists_config_on_successful_exchange(client, db_session, admin_user):
    as_user(admin_user)
    exchange_result = {
        "id": 4242, "slug": "bilwacorp-hub", "name": "BilwaCorp Fleet Hub",
        "html_url": "https://github.com/apps/bilwacorp-hub", "pem": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        "webhook_secret": "generated-secret", "client_id": "Iv1.unused", "client_secret": "unused",
    }
    with patch("app.integrations.github.api.app_auth.exchange_manifest_code", new_callable=AsyncMock, return_value=exchange_result):
        resp = await client.get("/github/app/manifest-callback", params={"code": "one-time-code"}, follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "app_setup=1" in resp.headers["location"]

    from app.integrations.github.models import GitHubAppConfig
    config = (await db_session.execute(select(GitHubAppConfig))).scalar_one()
    assert config.github_app_id == "4242"
    assert config.created_by == admin_user.id

    events = (await db_session.execute(select(OperationalEvent).where(OperationalEvent.event_type == "github.app_configured"))).scalars().all()
    assert len(events) == 1


async def test_manifest_callback_redirects_with_error_on_failed_exchange(client, admin_user):
    as_user(admin_user)
    from app.integrations.github.client import GitHubApiError
    with patch("app.integrations.github.api.app_auth.exchange_manifest_code", new_callable=AsyncMock, side_effect=GitHubApiError("bad code")):
        resp = await client.get("/github/app/manifest-callback", params={"code": "bad-code"}, follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "app_setup_error=1" in resp.headers["location"]


async def test_manifest_callback_without_code_redirects_with_error(client, admin_user):
    as_user(admin_user)
    resp = await client.get("/github/app/manifest-callback", follow_redirects=False)
    assert "app_setup_error=1" in resp.headers["location"]


async def test_installation_created_registers_initial_repositories(db_session):
    """The `repositories` list on an `installation` created payload —
    see docs/adr/ADR-004-github-app-auth.md's follow-up on auto-
    registering repos, and app_auth.register_repositories."""
    integration = await app_auth.upsert_installation(db_session, 7101, "acme-repos", None)
    payload = {
        "action": "created",
        "installation": {"id": 7101, "account": {"login": "acme-repos"}},
        "repositories": [
            {"id": 9101, "name": "backend", "full_name": "acme-repos/backend", "private": True},
            {"id": 9102, "name": "frontend", "full_name": "acme-repos/frontend", "private": True},
        ],
    }
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="r-1", event_type="installation", payload=payload, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()

    await process_webhook_event(db_session, webhook_event, integration)

    rows = (
        await db_session.execute(select(GitHubRepository).where(GitHubRepository.integration_id == integration.id))
    ).scalars().all()
    assert {r.full_name for r in rows} == {"acme-repos/backend", "acme-repos/frontend"}
    assert all(r.is_active for r in rows)
    assert all(r.owner == "acme-repos" for r in rows)


async def test_installation_repositories_added_then_removed(db_session):
    integration = await app_auth.upsert_installation(db_session, 7102, "acme-repos2", None)
    add_payload = {
        "action": "added", "installation": {"id": 7102},
        "repositories_added": [{"id": 9201, "name": "svc", "full_name": "acme-repos2/svc"}],
        "repositories_removed": [],
    }
    add_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="r-2", event_type="installation_repositories", payload=add_payload, signature_valid=True,
    )
    db_session.add(add_event)
    await db_session.flush()
    await process_webhook_event(db_session, add_event, integration)

    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 9201))).scalar_one()
    assert repo.is_active is True

    remove_payload = {
        "action": "removed", "installation": {"id": 7102},
        "repositories_added": [], "repositories_removed": [{"id": 9201, "name": "svc", "full_name": "acme-repos2/svc"}],
    }
    remove_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="r-3", event_type="installation_repositories", payload=remove_payload, signature_valid=True,
    )
    db_session.add(remove_event)
    await db_session.flush()
    await process_webhook_event(db_session, remove_event, integration)

    await db_session.refresh(repo)
    assert repo.is_active is False


async def test_reinstalling_reactivates_rather_than_duplicates_a_removed_repository(db_session):
    integration = await app_auth.upsert_installation(db_session, 7103, "acme-repos3", None)
    repo = GitHubRepository(
        integration_id=integration.id, external_id=9301, full_name="acme-repos3/svc", name="svc",
        owner="acme-repos3", html_url="https://github.com/acme-repos3/svc", is_active=False,
    )
    db_session.add(repo)
    await db_session.flush()

    payload = {
        "action": "created", "installation": {"id": 7103, "account": {"login": "acme-repos3"}},
        "repositories": [{"id": 9301, "name": "svc", "full_name": "acme-repos3/svc"}],
    }
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="r-4", event_type="installation", payload=payload, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()
    await process_webhook_event(db_session, webhook_event, integration)

    await db_session.refresh(repo)
    assert repo.is_active is True
    rows = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 9301))).scalars().all()
    assert len(rows) == 1  # reactivated in place, not duplicated


async def test_full_uninstall_deactivates_every_repository_immediately(client, db_session, monkeypatch):
    """Unlike installation_repositories' explicit removed-list, a full
    `installation.deleted` carries no repository list at all — every repo
    that integration owned must be deactivated, done synchronously in
    webhook_api.py (not deferred to the async handler)."""
    _configure_app_env(monkeypatch)
    integration = await app_auth.upsert_installation(db_session, 7104, "acme-repos4", None)
    repo = GitHubRepository(
        integration_id=integration.id, external_id=9401, full_name="acme-repos4/svc", name="svc",
        owner="acme-repos4", html_url="https://github.com/acme-repos4/svc",
    )
    db_session.add(repo)
    await db_session.commit()

    payload = {"action": "deleted", "installation": {"id": 7104, "account": {"login": "acme-repos4"}}}
    body = json.dumps(payload).encode()
    resp = await client.post(
        "/github/app/webhooks", content=body, headers={**_headers("installation", "r-5"), "X-Hub-Signature-256": _sign(body)},
    )
    assert resp.status_code == 202

    await db_session.refresh(repo)
    assert repo.is_active is False


async def test_callback_upserts_and_redirects_even_without_valid_state(client, db_session):
    with patch(
        "app.integrations.github.api.app_auth.get_installation_info",
        return_value={"account": {"login": "acme-callback"}},
    ):
        resp = await client.get(
            "/github/app/callback", params={"installation_id": 9001, "setup_action": "install", "state": "garbage"},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 307)
    assert "installed=1" in resp.headers["location"]

    integration = (
        await db_session.execute(select(GitHubIntegration).where(GitHubIntegration.installation_id == 9001))
    ).scalar_one()
    assert integration.created_by is None  # invalid state -> no attribution, but still provisioned
