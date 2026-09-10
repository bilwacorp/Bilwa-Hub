"""httpx wrapper for hub -> deployment calls, symmetric to PoultryOS-CBP's
services/hub_client.py. Decrypts the target deployment's action_key and
calls its /api/v1/hub/subscription/* endpoints (see that repo's
api/v1/routers/hub_integration.py — these shapes are the ground truth this
mirrors)."""
from typing import Any, Optional

import httpx

from app.models import Deployment
from app.services import crypto

_HTTP_TIMEOUT = 15.0


class DeploymentCallError(Exception):
    pass


async def _call(deployment: Deployment, method: str, path: str, json: Optional[dict] = None) -> dict:
    if not deployment.base_url:
        raise DeploymentCallError("Deployment has no base_url on file")
    if not deployment.action_key_encrypted:
        raise DeploymentCallError("Deployment has not completed registration yet (no action_key)")
    action_key = crypto.decrypt(deployment.action_key_encrypted)

    url = f"{deployment.base_url.rstrip('/')}/api/v1/hub{path}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.request(method, url, headers={"X-Hub-Api-Key": action_key}, json=json)
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


async def renew(deployment: Deployment, *, new_expiry_date: str, renewal_amount: Optional[float] = None) -> dict:
    body: dict[str, Any] = {"new_expiry_date": new_expiry_date}
    if renewal_amount is not None:
        body["renewal_amount"] = renewal_amount
    return await _call(deployment, "POST", "/subscription/renew", body)


async def suspend(deployment: Deployment, *, reason: str) -> dict:
    return await _call(deployment, "POST", "/subscription/suspend", {"reason": reason})


async def change_plan(deployment: Deployment, *, new_plan_id: str) -> dict:
    return await _call(deployment, "POST", "/subscription/change-plan", {"new_plan_id": new_plan_id})


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
