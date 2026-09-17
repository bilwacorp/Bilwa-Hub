"""HUB-Expansion.md Phase 15 — "Deployment URLs and callback targets must
not become an SSRF vulnerability." `Deployment.base_url` is entirely
client-controlled: it's set exactly once, by a self-registering
deployment's own `RegisterRequest.base_url` (api/routers/register.py) —
there is no staff-facing edit endpoint for it at all — and every later
staff action (renew/suspend/health-check/maintenance push,
services/deployment_client.py) makes an authenticated outbound call
(carrying the decrypted `action_key`) to whatever's stored there. A
single-use registration token doesn't vouch for the URL it arrives
with; this is a real, previously-flagged (Phase 0 audit) gap, not a
hypothetical one.

Two layers, matching the phase's own "protocols / URL format / network
restrictions where practical" checklist:
1. `validate_url_format` — synchronous, cheap: scheme must be http(s),
   hostname must be present and non-empty, and a literal IP host is
   checked directly against the private/loopback/link-local/reserved
   ranges. Called from RegisterRequest's own Pydantic validator so a
   malformed/obviously-unsafe URL never even reaches the database.
2. `assert_hostname_resolves_publicly` — async, best-effort: resolves
   the hostname and rejects it if EVERY answer, or even one answer, is
   private/loopback/link-local/reserved. This is what actually catches
   a public-looking hostname that resolves to an internal address
   (DNS rebinding's simpler cousin) — a check `validate_url_format`
   alone can't do for a non-literal-IP host. Called once, at
   registration time (not on every outbound call — this hub has no
   precedent for a per-call DNS-pinning proxy, and re-resolving on every
   renew/suspend/health-check would add latency to an interactive staff
   action for a check whose value is almost entirely at onboarding
   time). A hostname that starts safe and is later re-pointed at an
   internal address via DNS is a real residual risk this doesn't close
   — see docs/security/integration-security.md for that tradeoff
   written out.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def _is_unsafe_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def validate_url_format(url: str) -> str:
    """Raises ValueError with a caller-safe message; returns `url`
    unchanged so this composes directly as a Pydantic field_validator."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme must be one of {sorted(_ALLOWED_SCHEMES)}, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if _is_unsafe_ip(parsed.hostname):
        raise ValueError("URL must not point at a private, loopback, link-local, or reserved address")
    return url


async def assert_hostname_resolves_publicly(url: str, *, timeout_seconds: float = 5.0) -> None:
    """Best-effort — see module docstring. Raises ValueError if resolution
    fails outright (treated as unsafe: better to reject a typo'd hostname
    at registration time than accept an unreachable/misconfigured one) or
    if any resolved address is private/loopback/link-local/reserved."""
    hostname = urlparse(url).hostname
    if hostname is None:
        raise ValueError("URL must include a hostname")
    if _is_unsafe_ip(hostname):
        raise ValueError("URL must not point at a private, loopback, link-local, or reserved address")
    loop = asyncio.get_event_loop()
    try:
        infos = await asyncio.wait_for(
            loop.run_in_executor(None, socket.getaddrinfo, hostname, None), timeout=timeout_seconds,
        )
    except (socket.gaierror, asyncio.TimeoutError) as e:
        raise ValueError(f"Could not resolve hostname {hostname!r}: {e}") from e
    for family, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        if _is_unsafe_ip(ip_str):
            raise ValueError(f"Hostname {hostname!r} resolves to a private/internal address ({ip_str})")
