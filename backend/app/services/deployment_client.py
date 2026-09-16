"""httpx wrapper for hub -> deployment calls, symmetric to PoultryOS-CBP's
services/hub_client.py. Decrypts the target deployment's action_key and
calls its /api/v1/hub/subscription/* endpoints (see that repo's
api/v1/routers/hub_integration.py — these shapes are the ground truth this
mirrors)."""
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from app.models import Deployment
from app.services import crypto

_HTTP_TIMEOUT = 15.0
# Shorter than _HTTP_TIMEOUT above — this backs an on-demand "Check now"
# click a staff member is actively waiting on, not a background action; a
# slow/hanging deployment should read as "unreachable" in a few seconds,
# not leave the button spinning for 15.
_HEALTH_CHECK_TIMEOUT = 6.0


class DeploymentCallError(Exception):
    pass


async def _call(
    deployment: Deployment, method: str, path: str, json: Optional[dict] = None, *, idempotency_key: Optional[str] = None,
) -> dict:
    if not deployment.base_url:
        raise DeploymentCallError("Deployment has no base_url on file")
    if not deployment.action_key_encrypted:
        raise DeploymentCallError("Deployment has not completed registration yet (no action_key)")
    action_key = crypto.decrypt(deployment.action_key_encrypted)

    headers = {"X-Hub-Api-Key": action_key}
    if idempotency_key:
        # HUB-Expansion.md Phase 13 — sent on every retryable gated action
        # (see app/approvals/deployment_hooks.py) so a deployment that
        # understands this header can reject/dedupe a retried call itself.
        # A deployment that ignores it is unaffected — this is additive,
        # not a required contract change to PoultryOS-CBP's
        # hub_integration.py. Local dedup (never re-firing an already-
        # `executed` DeploymentActionExecution) is the real backstop.
        headers["X-Idempotency-Key"] = idempotency_key

    url = f"{deployment.base_url.rstrip('/')}/api/v1/hub{path}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, json=json)
    except httpx.HTTPError as e:
        # Transport-level failure (DNS, connection refused, timeout) — not an
        # HTTP error response, so it never reaches the status_code check
        # below. Callers (deployments.py's action_* routes) only ever catch
        # DeploymentCallError, so this must be raised as one too, not left
        # to propagate as a raw httpx exception (which the global handler
        # would otherwise turn into an opaque 500 instead of a 502).
        raise DeploymentCallError(f"{method} {path} -> connection failed: {e}") from e
    if resp.status_code >= 400:
        raise DeploymentCallError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def renew(
    deployment: Deployment, *, new_expiry_date: str, renewal_amount: Optional[float] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    body: dict[str, Any] = {"new_expiry_date": new_expiry_date}
    if renewal_amount is not None:
        body["renewal_amount"] = renewal_amount
    return await _call(deployment, "POST", "/subscription/renew", body, idempotency_key=idempotency_key)


async def suspend(deployment: Deployment, *, reason: str, idempotency_key: Optional[str] = None) -> dict:
    return await _call(deployment, "POST", "/subscription/suspend", {"reason": reason}, idempotency_key=idempotency_key)


async def change_plan(deployment: Deployment, *, new_plan_id: str, idempotency_key: Optional[str] = None) -> dict:
    return await _call(
        deployment, "POST", "/subscription/change-plan", {"new_plan_id": new_plan_id}, idempotency_key=idempotency_key,
    )


async def extend_expiry(deployment: Deployment, *, new_expiry_date: str) -> dict:
    return await _call(deployment, "POST", "/subscription/extend-expiry", {"new_expiry_date": new_expiry_date})


async def review_request(deployment: Deployment, *, request_id: str, status: str, review_note: Optional[str] = None) -> dict:
    return await _call(
        deployment, "PATCH", f"/subscription/requests/{request_id}",
        {"status": status, "review_note": review_note},
    )


async def push_maintenance(deployment: Deployment, *, windows: list[dict], notify: Optional[dict] = None) -> dict:
    """Replace the deployment's whole maintenance-window list. `windows` are
    MaintenanceWindowPublic.model_dump(mode="json") dicts. `notify`, when
    set ({window_id, kind}), tells the deployment to email its admins about
    that one window. Matches PoultryOS-CBP's api/v1/routers/hub_integration
    .py POST /hub/maintenance."""
    return await _call(deployment, "POST", "/maintenance", {"maintenance": windows, "notify": notify})


async def push_ticket_status(
    deployment: Deployment, *, hub_ticket_id: str, status: str, resolved_at: Optional[str] = None,
) -> dict:
    """Tells the deployment a ticket it relayed to us has changed status, so
    it can show that back to the client admin who raised it. Matches
    PoultryOS-CBP's api/v1/routers/hub_integration.py POST
    /hub/support-tickets/{hub_ticket_id}/status. Looked up by the ticket's
    hub-assigned id, since the deployment doesn't know our internal one."""
    return await _call(
        deployment, "POST", f"/support-tickets/{hub_ticket_id}/status",
        {"status": status, "resolved_at": resolved_at},
    )


async def check_health(deployment: Deployment) -> dict:
    """On-demand live probe of the deployment's own public /api/health and
    /api/health/db — the same endpoints its client-facing /health status
    page polls (see PoultryOS-CBP's HealthStatusPage.tsx / main.py). Doesn't
    go through _call(): these are unauthenticated (no X-Hub-Api-Key) and
    live outside /api/v1/hub.

    Independent of deployments.py's derived_status, which only reflects
    heartbeat freshness (up to ~2h stale) — this is a live round trip for
    the moment someone actually clicks "Check now", not a passive read of
    the last heartbeat.

    Never raises for an unreachable deployment or a failing check — that
    outcome IS the answer being asked for, so it comes back as
    api/database: False rather than a 502. Only raises DeploymentCallError
    when there's no base_url on file to even attempt."""
    if not deployment.base_url:
        raise DeploymentCallError("Deployment has no base_url on file")

    base = deployment.base_url.rstrip("/")
    result: dict[str, Any] = {
        "api": False, "database": False,
        "api_latency_ms": None, "db_latency_ms": None,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }

    async with httpx.AsyncClient(timeout=_HEALTH_CHECK_TIMEOUT) as client:
        t0 = time.monotonic()
        try:
            resp = await client.get(f"{base}/api/health")
            if resp.status_code == 200:
                result["api"] = True
                result["api_latency_ms"] = round((time.monotonic() - t0) * 1000)
        except httpx.HTTPError:
            pass

        t0 = time.monotonic()
        try:
            resp = await client.get(f"{base}/api/health/db")
            if resp.status_code == 200:
                data = resp.json()
                result["database"] = bool(data.get("database"))
                result["db_latency_ms"] = data.get("db_latency_ms")
        except httpx.HTTPError:
            pass

    return result
