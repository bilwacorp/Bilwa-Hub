# BilwaCorp Fleet Hub — Phase 1

Centralized service that every PoultryOS-CBP client deployment registers
with on first boot and reports a heartbeat to every 2 hours (subscription
status, usage, pending renewal/upgrade requests), so BilwaCorp staff can see
the whole fleet — and act on it (renew, suspend, change plan, review a
request) — from one place instead of logging into each deployment
separately. Also collects support tickets and tracks maintenance windows
across the fleet.

See `/Users/gopalsmac/.claude/plans/partitioned-dreaming-kazoo.md` on the
machine this was built on for the full design rationale (the two-credential
model, why registration can't be a literal Alembic migration, edge cases).

## Seeded login

Migration `002_seed_admin.py` creates one staff account:

- Username: `admin`
- Password: `ChangeMe@2026`

Change it before any real deployment — there's no self-service
password-change endpoint in Phase 1; update the `users.hashed_password`
column directly (bcrypt) or re-run the seed with a different `PASSWORD`
constant against a fresh DB.

## Backend

```
cd backend
cp .env.example .env   # fill in DATABASE_URL, SECRET_KEY, HUB_ENCRYPTION_KEY
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

`HUB_ENCRYPTION_KEY` is required (not optional) — it's the Fernet key that
lets the hub decrypt a deployment's `action_key` when calling back in to
run a renew/suspend/etc. action. Generate one with:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Frontend

```
cd frontend
cp .env.example .env   # VITE_API_BASE_URL
npm install
npm run dev
```

## Deploying (Dokploy)

The whole hub is one `docker-compose.yml` — `postgres` + `backend` +
`frontend` (nginx serving the SPA and proxying `/api` to `backend` on the
compose network, so everything is one origin and the auth cookie works).

1. In Dokploy, **Create Service → Compose**, point it at this repo
   (`main` branch), compose file `docker-compose.yml`.
2. **Environment tab** — set:
   - `POSTGRES_PASSWORD` — any strong random string
   - `SECRET_KEY` — random, 32+ chars
   - `HUB_ENCRYPTION_KEY` — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `HUB_PUBLIC_URL` — the public URL you'll give this hub, e.g. `https://hub.bilwacorp.example`
3. **Domains tab** — add your domain, routed to service **`frontend`**, port **`80`**. Leave `backend` and `postgres` with no domain.
4. Deploy. `backend`'s container runs `alembic upgrade head` on every start,
   so the schema + seeded admin are created automatically.
5. Log in with `admin` / `ChangeMe@2026` and **change the password
   immediately** (see below).

Postgres runs in the stack with a named volume (`hub_pgdata`). If you'd
rather use a Dokploy-managed database for automated backups, delete the
`postgres` service from the compose file and point `DATABASE_URL` at the
managed one.

## Connecting a new client deployment

Full runbook (prerequisites, exact API calls, network requirements,
troubleshooting, the credential model): **`docs/CONNECTING_A_DEPLOYMENT.md`**
— also summarized in `CLAUDE.md`. Short version:

1. Log in to the hub, go to Deployments → New Deployment (or
   `POST /api/v1/deployments`). Copy the one-time `registration_token`
   shown (it's never shown again).
2. In that new client's own Dokploy project, set on the **celery-worker**
   service only: `HUB_URL`, `HUB_REGISTRATION_TOKEN` (from step 1), and
   `FRONTEND_URL` if it isn't already set there.
3. Redeploy that client. Within seconds of boot (and every 2h after) its
   `celery-worker` registers and starts sending heartbeats — no further
   manual step.
4. Lost the token before registration completed? Use "Reissue Token" on
   that deployment's detail page rather than creating a new row.

## What's deliberately NOT in Phase 1

- No permission catalog / roles UI on the hub — one hardcoded `admin` role.
- No per-device session management (no `UserSession`/"sid" claim) — just
  `token_version`-based revocation on password change.
- No local ticket table on the PoultryOS-CBP side — tickets are relayed
  straight to the hub, which is the sole source of truth for them.
- No automation on maintenance windows (no reminders, no auto-transitions)
  — plain scheduling records.
- The hub's "Change Plan" action requires knowing the target deployment's
  plan UUID by hand (the hub doesn't mirror each deployment's plan
  catalog) — fine for occasional use, worth a follow-up if it's used often.
