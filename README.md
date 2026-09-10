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

## Registering a new client deployment

1. Log in to the hub, go to Deployments → New Deployment. Copy the
   one-time `registration_token` shown (it's never shown again).
2. In that new client's own Dokploy project, set on the **celery-worker**
   service only: `HUB_URL` (this hub's base URL) and
   `HUB_REGISTRATION_TOKEN` (the token from step 1). Also add
   `FRONTEND_URL` there if it isn't already (see the plan — celery-worker
   didn't previously need it).
3. Redeploy that client. Within a few seconds of boot (and every 2h after)
   its `celery-worker` will register and start sending heartbeats — no
   further manual step.
4. If the token is lost before registration completes (e.g. a botched
   deploy), use "Reissue Token" on that deployment's detail page rather
   than creating a new Deployment row.

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
