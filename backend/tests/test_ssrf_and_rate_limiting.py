"""HUB-Expansion.md Phase 15 — SSRF hardening on Deployment.base_url
(app/core/url_safety.py) and rate limiting on public endpoints
(app/core/rate_limit.py). Both were explicitly flagged, unfixed gaps
carried in task-track.md since the Phase 0 audit."""
import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from app.core.rate_limit import enforce_rate_limit
from app.core.url_safety import assert_hostname_resolves_publicly, validate_url_format
from app.models import Deployment, DeploymentStatus
from app.schemas import RegisterRequest


# ── validate_url_format (synchronous, schema-level) ────────────────────────

def test_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="scheme"):
        validate_url_format("ftp://example.com")


def test_rejects_missing_hostname():
    with pytest.raises(ValueError, match="hostname"):
        validate_url_format("http://")


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "10.0.0.5", "0.0.0.0", "[::1]"])
def test_rejects_private_or_reserved_literal_ips(host):
    with pytest.raises(ValueError, match="private|reserved|loopback|link-local"):
        validate_url_format(f"http://{host}/")


def test_accepts_ordinary_public_https_url():
    assert validate_url_format("https://acme-client.example.com") == "https://acme-client.example.com"


def test_register_request_schema_rejects_unsafe_base_url():
    with pytest.raises(Exception):  # pydantic.ValidationError
        RegisterRequest(registration_token="x", base_url="http://127.0.0.1", action_key="y")


# ── assert_hostname_resolves_publicly (async, DNS-resolution layer) ───────

async def test_resolves_publicly_rejects_hostname_resolving_to_private_ip():
    with patch("app.core.url_safety.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
        with pytest.raises(ValueError, match="private"):
            await assert_hostname_resolves_publicly("http://sneaky.example.com")


async def test_resolves_publicly_accepts_hostname_resolving_to_public_ip():
    with patch("app.core.url_safety.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
        await assert_hostname_resolves_publicly("http://acme-client.example.com")  # no raise


async def test_resolves_publicly_rejects_unresolvable_hostname():
    import socket
    with patch("app.core.url_safety.socket.getaddrinfo", side_effect=socket.gaierror("not found")):
        with pytest.raises(ValueError, match="resolve"):
            await assert_hostname_resolves_publicly("http://does-not-exist.example.invalid")


# ── register.py endpoint wiring ─────────────────────────────────────────────

async def _make_pending_deployment(db_session, *, slug: str, token: str = "raw-reg-token") -> Deployment:
    d = Deployment(
        client_name=f"Client {slug}", slug=slug, status=DeploymentStatus.pending,
        registration_token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    db_session.add(d)
    await db_session.flush()
    return d


async def test_register_endpoint_rejects_bad_scheme_before_touching_db(client, db_session):
    resp = await client.post("/register", json={
        "registration_token": "whatever", "base_url": "ftp://evil.example.com", "action_key": "k",
    })
    assert resp.status_code == 422


async def test_register_endpoint_rejects_url_that_resolves_privately(client, db_session):
    await _make_pending_deployment(db_session, slug="ssrf-1")
    with patch("app.api.routers.register.assert_hostname_resolves_publicly", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.side_effect = ValueError("resolves to a private address")
        resp = await client.post("/register", json={
            "registration_token": "raw-reg-token", "base_url": "https://sneaky.example.com", "action_key": "k",
        })
    assert resp.status_code == 400
    assert "private" in resp.json()["detail"]


async def test_register_endpoint_succeeds_for_a_safe_public_url(client, db_session):
    await _make_pending_deployment(db_session, slug="ssrf-2")
    with patch("app.api.routers.register.assert_hostname_resolves_publicly", new_callable=AsyncMock):
        resp = await client.post("/register", json={
            "registration_token": "raw-reg-token", "base_url": "https://acme-client.example.com", "action_key": "k",
        })
    assert resp.status_code == 200


# ── rate limiting ────────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        pass


async def test_enforce_rate_limit_blocks_after_threshold():
    fake = _FakeRedis()
    with patch("app.core.rate_limit.get_redis", new_callable=AsyncMock, return_value=fake):
        for _ in range(3):
            await enforce_rate_limit("test-key", limit=3, window_seconds=60)
        with pytest.raises(Exception) as exc_info:
            await enforce_rate_limit("test-key", limit=3, window_seconds=60)
        assert exc_info.value.status_code == 429


async def test_enforce_rate_limit_fails_open_when_redis_unavailable():
    with patch("app.core.rate_limit.get_redis", new_callable=AsyncMock, return_value=None):
        # No exception even called far past any reasonable limit.
        for _ in range(10):
            await enforce_rate_limit("test-key-2", limit=1, window_seconds=60)


async def test_register_endpoint_rate_limited_by_ip(client, db_session):
    await _make_pending_deployment(db_session, slug="ssrf-3")
    fake = _FakeRedis()
    with patch("app.core.rate_limit.get_redis", new_callable=AsyncMock, return_value=fake), \
         patch("app.api.routers.register.assert_hostname_resolves_publicly", new_callable=AsyncMock):
        last = None
        for _ in range(11):
            last = await client.post("/register", json={
                "registration_token": "raw-reg-token", "base_url": "https://acme-client.example.com", "action_key": "k",
            })
        assert last.status_code == 429
