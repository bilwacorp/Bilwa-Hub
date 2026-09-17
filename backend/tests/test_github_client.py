"""app/integrations/github/client.py — GitHub API failure/retry/rate-limit
classification. Pure unit tests, no DB: httpx.AsyncClient.request is
patched to return a real httpx.Response (constructed in-memory, no
network) or raise, so these run without a live server or a real GitHub
token. `db=None` is safe everywhere here — these integrations are
auth_mode=pat (the unset default on a plain, un-flushed model instance),
and the pat branch of client._resolve_token never touches `db`."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.integrations.github.client import GitHubApiError, GitHubConnectionError, GitHubRateLimitError
from app.integrations.github.client import test_connection as gh_test_connection
from app.integrations.github.models import GitHubIntegration
from app.services import crypto


def _fake_integration(token_encrypted: str | None) -> GitHubIntegration:
    return GitHubIntegration(github_org="acme", name="Acme", access_token_encrypted=token_encrypted)


async def test_no_token_raises_without_making_a_request():
    integration = _fake_integration(None)
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        with pytest.raises(GitHubApiError, match="no access token"):
            await gh_test_connection(None, integration)
    mock_request.assert_not_called()


async def test_successful_call_returns_json():
    integration = _fake_integration(crypto.encrypt("ghp_fake"))
    response = httpx.Response(200, json={"login": "octocat"}, request=httpx.Request("GET", "https://api.github.com/user"))
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response):
        data = await gh_test_connection(None, integration)
    assert data["login"] == "octocat"


async def test_429_raises_rate_limit_error_with_retry_after():
    integration = _fake_integration(crypto.encrypt("ghp_fake"))
    response = httpx.Response(
        429, headers={"retry-after": "42"}, json={"message": "rate limited"},
        request=httpx.Request("GET", "https://api.github.com/user"),
    )
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response):
        with pytest.raises(GitHubRateLimitError) as exc_info:
            await gh_test_connection(None, integration)
    assert exc_info.value.retry_after_seconds == 42


async def test_403_with_exhausted_rate_limit_header_raises_rate_limit_error():
    integration = _fake_integration(crypto.encrypt("ghp_fake"))
    response = httpx.Response(
        403, headers={"x-ratelimit-remaining": "0"}, json={"message": "rate limited"},
        request=httpx.Request("GET", "https://api.github.com/user"),
    )
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response):
        with pytest.raises(GitHubRateLimitError):
            await gh_test_connection(None, integration)


async def test_plain_403_without_rate_limit_header_is_not_a_rate_limit_error():
    """A permissions-scope 403 (not rate limiting) must not be retried
    forever the way a real rate limit should be."""
    integration = _fake_integration(crypto.encrypt("ghp_fake"))
    response = httpx.Response(403, json={"message": "forbidden"}, request=httpx.Request("GET", "https://api.github.com/user"))
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response):
        with pytest.raises(GitHubApiError) as exc_info:
            await gh_test_connection(None, integration)
    assert not isinstance(exc_info.value, GitHubRateLimitError)


async def test_404_raises_plain_api_error_not_rate_limit():
    integration = _fake_integration(crypto.encrypt("ghp_fake"))
    response = httpx.Response(404, json={"message": "not found"}, request=httpx.Request("GET", "https://api.github.com/user"))
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response):
        with pytest.raises(GitHubApiError) as exc_info:
            await gh_test_connection(None, integration)
    assert not isinstance(exc_info.value, GitHubRateLimitError)


async def test_transport_failure_raises_connection_error():
    integration = _fake_integration(crypto.encrypt("ghp_fake"))
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, side_effect=httpx.ConnectTimeout("timed out")):
        with pytest.raises(GitHubConnectionError):
            await gh_test_connection(None, integration)
