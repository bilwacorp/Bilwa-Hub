# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## What this project is

BilwaCorp Fleet Hub — a centralized service every PoultryOS-CBP client
deployment (each its own single-tenant, separately hosted instance)
registers with on first boot. Every deployment then reports a heartbeat
every 2 hours (subscription status, usage, pending renewal/upgrade
requests), so BilwaCorp staff can see the whole fleet — and act on it
(renew, suspend, change plan, review a request) — from one place instead
of logging into each deployment separately. Also collects support tickets
relayed from each deployment and tracks maintenance windows across the
fleet.

This is **Phase 1**: registration, heartbeat, inbound actions, support
tickets, maintenance windows. No permission catalog/roles UI (one hardcoded
staff role), no per-device sessions, no notification emails from the hub
itself. See `README.md`'s "What's deliberately NOT in Phase 1" and the
original design plan quoted there for the full rationale.

**Connecting a new client deployment to this hub is a full runbook of its
own — see `docs/CONNECTING_A_DEPLOYMENT.md` before doing this for real.**
Short version: create a pending `Deployment` + single-use
`registration_token` on the hub, set `HUB_URL`/`HUB_REGISTRATION_TOKEN` on
that client's `celery-worker` service only, redeploy — the client
auto-registers and starts heartbeating with no further manual step.

---

## Commands

### Backend
```bash
cd backend
cp .env.example .env   # DATABASE_URL, SECRET_KEY, HUB_ENCRYPTION_KEY (required, see below)
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend
```bash
cd frontend
cp .env.example .env   # VITE_API_BASE_URL
npm install
npm run dev      # dev server
npm run build    # tsc + vite build
```

### Whole stack (Docker)
```bash
docker compose up --build   # postgres + backend + frontend; needs POSTGRES_PASSWORD,
                            # SECRET_KEY, HUB_ENCRYPTION_KEY (+ optional HUB_PUBLIC_URL)
                            # in a .env next to docker-compose.yml
```
`docker-compose.yml` is also the Dokploy deploy unit — see README's
"Deploying (Dokploy)". `frontend`'s nginx serves the SPA and proxies `/api`
to `backend` on the compose network, so it's one origin (the auth cookie is
`SameSite=Lax; Path=/api` — cross-origin would break it) and `backend`
never needs its own public domain.

URLs: Backend `http://localhost:8000/api/v1` (no `/docs` Swagger check done
yet — verify it's enabled the same way PoultryOS-CBP's is if you need it).
Frontend `http://localhost:5174` (dev) / port `80` on the `frontend`
container (compose).

`HUB_ENCRYPTION_KEY` is **required**, not optional — it's the Fernet key
the hub uses to decrypt a deployment's `action_key` when calling back in to
run an action (`services/crypto.py`). Generate one with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Seeded login
`alembic/versions/002_seed_admin.py` creates one staff account:
`admin` / `ChangeMe@2026`. No self-service password-change endpoint in
Phase 1 — change it by editing `users.hashed_password` (bcrypt) directly,
or re-seed against a fresh DB with a different constant.

---

## Architecture

### Backend — `backend/app/`

```
main.py              FastAPI app, CORS, lifespan (Casbin enforcer init + watcher)
models.py            User (staff), Deployment, DeploymentSnapshot, SupportTicket,
                      MaintenanceWindow — single file, mirrors PoultryOS-CBP's
                      models/models.py convention
schemas.py           All Pydantic request/response schemas, one file
core/config.py        Settings — DATABASE_URL, SECRET_KEY, HUB_ENCRYPTION_KEY (required),
                      REDIS_URL, FRONTEND_URL, BACKEND_CORS_ORIGINS
core/deps.py          Ported from PoultryOS-CBP: _BearerOrCookie + get_current_user
                      (token_version-based revocation). Deliberately dropped: UserSession/
                      "sid" per-device tracking, mobile-vs-web client split — Phase 1 has
                      exactly one staff login, web-only.
core/permissions.py   Single STAFF_MANAGE permission, granted to the one seeded 'admin'
                      role. require_permission()'s shape is ported from PoultryOS-CBP so a
                      later phase can grow a real per-resource catalog without changing how
                      routes are gated. register.py/ingest.py are NOT Casbin-gated at all —
                      they're machine-to-machine, shared-secret authenticated instead.
core/casbin_enforcer.py, core/casbin_watcher.py, core/rbac_model.conf
                      Ported near-verbatim from PoultryOS-CBP — generic, no app-specific
                      content in rbac_model.conf.
services/crypto.py    Fernet encrypt/decrypt for Deployment.action_key_encrypted — mirrors
                      PoultryOS-CBP's User.totp_secret_encrypted pattern (reversible storage
                      is the exception, not the rule; see "Two credentials" below).
services/deployment_client.py
                      httpx wrapper for hub -> deployment calls (renew/suspend/change-plan/
                      extend-expiry/review-request). Decrypts the target deployment's
                      action_key, calls its /api/v1/hub/subscription/* endpoints — see that
                      repo's api/v1/routers/hub_integration.py for the ground-truth shapes
                      this mirrors. Catches transport-level failures (DNS, connection
                      refused) as DeploymentCallError, not a raw httpx exception, so callers
                      get a clean 502 instead of an opaque 500.
api/routers/
  auth.py             login/logout/refresh/me — MFA, phone/WhatsApp OTP, and mobile
                      refresh-token pairing all dropped (ported subset only)
  register.py         POST /register — public, single-use registration_token auth, not JWT
  ingest.py            POST /ingest/heartbeat, POST /ingest/support-ticket — api_key bearer
                      auth (hash-compared against Deployment.api_key_hash)
  deployments.py        staff-only (STAFF_MANAGE): create pending deployment + token, list,
                      detail (+ latest snapshot), reissue-token, and the inbound-action
                      triggers (renew/suspend/change-plan/extend-expiry/review-request)
  tickets.py            staff-only: list/detail/update support tickets
  maintenance.py        staff-only: plain CRUD on maintenance windows, no automation
```

### Frontend — `frontend/src/`

```
lib/api.ts, lib/utils.ts     axios instance + cn()/formatDate() etc., mirrors PoultryOS-CBP
components/ui/                Button, Input, Select, Modal, Badge, DataTable, Tabs — copied
                              verbatim from PoultryOS-CBP's frontend/src/components/ui/
                              (generic, no product-specific logic)
pages/auth/LoginPage.tsx
pages/deployments/DeploymentsListPage.tsx    client name, status, plan, expiry, last-
                                              heartbeat health indicator
pages/deployments/DeploymentDetailPage.tsx   latest snapshot (including pending_requests
                                              from the heartbeat payload directly — no
                                              separate live round-trip in Phase 1), renew/
                                              suspend/change-plan/extend-expiry action
                                              buttons, request-review actions
pages/tickets/SupportTicketsPage.tsx         fleet-wide ticket table + status update
pages/maintenance/MaintenanceWindowsPage.tsx list + create/edit, no automation
```

### The two-credential design (read before touching auth/registration code)

Both directions of hub<->deployment traffic need a credential, and each is
issued by whichever side only needs to *verify* it, never present it back:

- **`api_key`** — issued by **this hub** at registration. The deployment
  stores it and presents it on every outbound call (heartbeat, ticket
  push). This hub stores only `sha256(api_key)` — it never has to present
  this value again, only verify it, so hash-only storage is correct and
  sufficient (see `api/routers/ingest.py`).
- **`action_key`** — generated by **the deployment itself** and sent to
  this hub inside the `/register` request body. This hub, unlike with
  `api_key`, *must* be able to present this value later (when it calls
  back in to run an action) — so hash-only storage doesn't work here. It's
  stored **Fernet-encrypted** (`Deployment.action_key_encrypted`,
  `services/crypto.py`), mirroring PoultryOS-CBP's own precedent for
  reversible secrets (`User.totp_secret_encrypted`). This extra bar
  (encryption, not plaintext) exists because this hub aggregates this
  credential for *every* client deployment in one place — much higher
  blast radius than any single deployment's own locally-stored secret.

Never "simplify" this to a single shared key without re-reading this
section and `docs/CONNECTING_A_DEPLOYMENT.md`'s credential-reference table
— the asymmetry is deliberate, not an oversight.

### Registration can't be a literal migration

If you're tempted to make onboarding "more automatic" by having a
migration call out over HTTP: don't. There's no precedent for it in either
this repo or PoultryOS-CBP, and a migration-time network call has no
retry/backoff semantics and can block the whole container from booting if
the other side is unreachable at exactly the wrong moment. Registration
lives in a Celery-beat task on the *deployment* side
(`hub_sync_tasks.py` in PoultryOS-CBP) precisely so a hub outage just means
"try again next scheduled run," never a boot failure.

---

## Environment variables (`backend/.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres, async driver |
| `SECRET_KEY` | JWT signing key (staff login) |
| `HUB_ENCRYPTION_KEY` | **Required.** Fernet key for `action_key` encryption — the hub cannot run its inbound-action feature without it |
| `REDIS_URL` | Casbin cross-worker policy sync (`core/casbin_watcher.py`) |
| `BACKEND_CORS_ORIGINS` | This hub's own frontend origin(s) |
| `FRONTEND_URL` | This hub's own frontend URL — not request-routing related, kept for parity with PoultryOS-CBP's Settings shape |

Frontend env: `VITE_API_BASE_URL`.
