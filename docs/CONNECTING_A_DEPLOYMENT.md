# Connecting a client deployment to the fleet hub

This is the operator runbook for onboarding one PoultryOS-CBP client
deployment (or any future product built the same way) to this hub. For the
design rationale (why two separate credentials, why registration can't be a
literal Alembic migration, the full edge-case table) see the plan this was
built from — quoted in full in `README.md`'s header. This doc is the
step-by-step "how do I actually do it" version, kept accurate against the
real endpoint paths and schemas in this repo, not the plan's sketch.

## How the connection works, in one paragraph

A client deployment and the hub establish trust exactly once, at
registration, by exchanging two different secrets — the hub issues one
(`api_key`, so the deployment can prove itself when it phones home), and the
deployment issues the other (`action_key`, so the hub can prove itself when
it calls back in to run an action). After that handshake, the deployment's
Celery worker pushes a heartbeat every 2 hours (and once immediately at
boot), and the hub can call back into that deployment's `/api/v1/hub/*`
endpoints at any time using the `action_key` it was given. Neither side ever
needs to trust anything about the other beyond that one shared secret pair.

## Prerequisites

- This hub is deployed and reachable over HTTPS from the client
  deployment's `celery-worker` container (see "Network requirements"
  below — this is an *outbound* requirement on the client's side).
- The client deployment is reachable over HTTPS from the hub (an *inbound*
  requirement on the client's side, in the other direction — only needed
  if you intend to use hub-triggered actions; a deployment that only wants
  to appear on the fleet dashboard and get its heartbeat picked up doesn't
  strictly need this, but Phase 1 doesn't have a way to disable inbound
  actions per-deployment, so keep both directions open for now).
- You (BilwaCorp staff) are logged into this hub with the seeded admin
  account (see `README.md` — change the password on a real deployment).
- You have the client deployment's Dokploy project open, specifically its
  `celery-worker` service's Environment tab.

## Step by step

### 1. Create the pending deployment record on the hub

```
POST {HUB_URL}/api/v1/deployments
Authorization: Bearer <your hub staff JWT>
Content-Type: application/json

{ "client_name": "Sri Balaji Poultry Farms", "slug": "sri-balaji" }
```

Response:

```json
{
  "id": "5e2f...",
  "client_name": "Sri Balaji Poultry Farms",
  "slug": "sri-balaji",
  "status": "pending",
  "registration_token": "AbCdEf...=="
}
```

**Copy `registration_token` now — it is shown exactly once and the hub
never stores it in a recoverable form (only its hash).** If you lose it
before step 3 completes, use the reissue endpoint in step 5 rather than
creating a second `Deployment` row for the same client.

Doing this from the hub's own UI instead: Deployments → New Deployment,
same effect, same one-time token display.

### 2. Set two (or three) env vars on the client's `celery-worker` service only

In that client's Dokploy project, on the **`celery-worker` service**
specifically (not `backend`, not `celery-beat` — see "Why celery-worker
only" below):

| Variable | Value |
|---|---|
| `HUB_URL` | This hub's base URL, e.g. `https://hub.bilwacorp.internal` |
| `HUB_REGISTRATION_TOKEN` | The `registration_token` from step 1 |
| `FRONTEND_URL` | The client's own public frontend URL, if not already set there — `celery-worker` didn't previously need this var for anything else, but the registration handshake sends it as `base_url` so the hub knows where to call back in later |

### 3. Redeploy the client

Redeploy (or just restart) that client's `celery-worker` container so it
picks up the new env vars. Within a few seconds of boot — the deployment's
`main.py` lifespan kicks one heartbeat attempt immediately at startup — it
will:

1. `POST {HUB_URL}/api/v1/register` with its generated `action_key` +
   `base_url` + the registration token.
2. Store the hub's response (`deployment_id`, `api_key`) locally.
3. Send its first real heartbeat: `POST {HUB_URL}/api/v1/ingest/heartbeat`.

No further manual step. The recurring 2-hour Celery-beat schedule
(`hub-heartbeat` in that repo's `notifications/tasks.py`) takes over from
here — see that repo's own `services/hub_client.py` /
`services/hub_sync_tasks.py` for exactly what it does each run.

### 4. Verify on the hub

- Deployments list should show this client's status as `active` with a
  recent "last heartbeat."
- The deployment's detail page should show its current plan, expiry,
  usage, and any pending renewal/upgrade requests.
- Try a harmless action (e.g. Extend Expiry by the same date it already
  has) to prove the inbound direction works too — this exercises the
  `action_key` the deployment generated in step 3, not anything from step 1.

### 5. If something goes wrong before step 3 completes

`POST /api/v1/deployments/{id}/reissue-token` — mints a fresh single-use
token and resets that row to `pending`. Use this instead of creating a new
`Deployment` row for the same client (a second row for the same client
would just be a duplicate no one links to the first one's history).

## Why `celery-worker` only, not `backend`

- `backend` (the FastAPI process handling normal user traffic) receives
  inbound hub calls, but that check reads the locally stored `action_key`
  out of the database (`AppSetting`), not an env var — so `backend` needs
  no hub-specific env at all.
- `celery-beat` only schedules tasks — it publishes a message to Redis and
  never executes a task body itself, so it never needs `HUB_URL` either.
- Only `celery-worker` actually executes `hub_sync.heartbeat` /
  `hub_sync.push_ticket`, which is why it's the only service that needs
  `HUB_URL`/`HUB_REGISTRATION_TOKEN`/`FRONTEND_URL`.

## Network requirements

| Direction | From | To | Needed for |
|---|---|---|---|
| Outbound | client's `celery-worker` | this hub | registration, heartbeat, support-ticket relay |
| Inbound | this hub | client's `backend` (via its public `base_url`) | renew/suspend/change-plan/extend-expiry, reviewing a subscription request |

Both directions are plain HTTPS, no VPN/private networking required — the
trust boundary is the shared secret pair exchanged at registration, not
network position. If a client's infra sits behind a firewall that blocks
inbound traffic entirely, registration and heartbeats still work fine
(outbound-only); only the hub-triggered actions in the second row won't be
reachable until that's opened up.

## Credential reference

| Secret | Issued by | Stored by (reversible?) | Presented by | Used for |
|---|---|---|---|---|
| `registration_token` | hub | hub (hash only) | client, once | one-time proof this client was pre-approved |
| `api_key` | hub | hub (hash only) | client, every heartbeat/ticket push | client → hub authentication |
| `action_key` | client | hub (Fernet-encrypted — reversible, since the hub must present it back) | hub, every inbound action call | hub → client authentication |

The asymmetry (hub stores `api_key` as an irreversible hash but
`action_key` reversibly encrypted) isn't an inconsistency — it's because
only one direction ever needs the *presenting* side's secret decrypted
again later. See `backend/app/services/crypto.py` for the Fernet
encrypt/decrypt helpers and `HUB_ENCRYPTION_KEY` in `.env`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `POST /register` → 404 | Wrong/mistyped `HUB_REGISTRATION_TOKEN`, or it was set on the wrong service |
| `POST /register` → 409 | Token already consumed — either it already registered successfully (check the deployment's status on the hub) or someone reused a token meant for a different client. Reissue a fresh one rather than debugging further |
| Heartbeat never appears, no errors on the client | `HUB_URL` unreachable from `celery-worker` specifically — check that container's own network/egress, not just the `backend` container's |
| Hub action returns "connection failed" (502-shaped error) | The target deployment's `base_url` is wrong, unreachable, or its `backend` service is down — this is the *inbound* direction; it's independent of whether heartbeats are working |
| Hub action returns 401 | The deployment's stored `action_key` doesn't match what the hub has — this shouldn't normally drift once registered; if it does, the only Phase 1 recovery is reissuing a token and re-registering from scratch |

## Reference: every endpoint involved

| Direction | Method + path | Auth |
|---|---|---|
| staff → hub | `POST /api/v1/deployments` | staff JWT |
| staff → hub | `GET /api/v1/deployments`, `GET /api/v1/deployments/{id}` | staff JWT |
| staff → hub | `POST /api/v1/deployments/{id}/reissue-token` | staff JWT |
| client → hub | `POST /api/v1/register` | single-use `registration_token` in body |
| client → hub | `POST /api/v1/ingest/heartbeat` | `Authorization: Bearer <api_key>` |
| client → hub | `POST /api/v1/ingest/support-ticket` | `Authorization: Bearer <api_key>` |
| hub → client | `POST {client base_url}/api/v1/hub/subscription/renew` \| `/suspend` \| `/change-plan` \| `/extend-expiry` | `X-Hub-Api-Key: <action_key>` |
| hub → client | `PATCH {client base_url}/api/v1/hub/subscription/requests/{id}` | `X-Hub-Api-Key: <action_key>` |
