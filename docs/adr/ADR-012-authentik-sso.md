# ADR-012: Authentik SSO replaces password login

Status: Accepted. Implemented in migration `024_authentik_sso.py`.

## Context

BilwaCorp now runs its own self-hosted Authentik instance as the org's
identity provider. Staff login for this hub was previously username/
password (bcrypt hash in `users.hashed_password`, JWT session cookie) —
this ADR replaces that entirely with Authentik OIDC, with no password
fallback: the org wants one place (Authentik) that controls who can sign
in and with what credential policy, not a second password database living
inside this hub.

## Decisions

**1. OIDC Authorization Code + PKCE, not SAML.** Authentik supports both;
OIDC is the simpler flow for a FastAPI backend acting as a confidential
client, and this hub already has the pieces to speak it (`httpx` for the
HTTP calls, `python-jose` for JWT verification — see decision 5). PKCE
(S256) is layered on even though the client is confidential (holds
`client_secret`, does the code↔token exchange server-side) — negligible
added cost, extra protection if the secret is ever weakened or leaked.

**2. Full replacement, no dual-mode.** `POST /auth/login` (password),
`POST /auth/forgot-password`, and `POST /auth/reset-password` are removed
outright, not kept as a fallback path. A hub with two valid ways to sign
in as the same staff account is a wider attack surface for no benefit
once Authentik is the org's real source of truth for staff identity.

**3. No auto-provisioning — email-match against a pre-created `User`
row.** `GET /auth/callback` looks up `User.email` against the id_token's
`email` claim; a miss or an inactive match fails cleanly (redirect to
`/login?error=no_account`), with no `User` row ever created from the IdP
side. An admin still creates staff rows from the Staff page exactly as
before, just without a password field — see decision 8.

**4. State/nonce/PKCE ride in one short-lived HttpOnly cookie, not
Redis/DB.** This hub has no per-device session store (a deliberate Phase
1 posture — see `README.md`), and `REDIS_URL` elsewhere in this app is
optional/best-effort, not a hard dependency anything can lean on. `GET
/auth/login` packs `state`+`nonce`+PKCE `code_verifier` into one cookie
(`sso_flow`, 5-minute TTL, scoped to `/api/v1/auth`); `GET /auth/callback`
reads it back and compares `state` against the query param. The cookie
itself — HttpOnly, Secure, narrow path, short TTL — is the CSRF boundary;
nothing about it needs signing.

**5. `python-jose[cryptography]` (already a dependency) verifies
Authentik's RS256 id_token — no new dependency.** It's already used for
this hub's own HS256 session JWT and, per ADR-004 decision 7, the GitHub
App's RS256 JWT. `python-jose` has no OIDC-discovery/JWKS-caching client
of its own, so `services/oidc_client.py` hand-rolls that part (discovery
document + JWKS fetch, both `httpx`, both already a dependency) and hands
`python-jose` the matched JWK dict directly for signature verification.

**6. `AUTHENTIK_ISSUER`/`AUTHENTIK_CLIENT_ID`/`AUTHENTIK_CLIENT_SECRET`/
`AUTHENTIK_REDIRECT_URI` are required, no default — deliberately
deviating from the GitHub App's optional-empty-default convention
(`core/config.py`).** The GitHub App mode is additive to an
always-available PAT mode, so leaving it unconfigured is a valid, common
state. There is no equivalent fallback here: an unset Authentik setting
means staff can never sign in at all, so `Settings()` should fail hub
startup loudly rather than let that surface later as a confusing runtime
404/500 on first login attempt.

**7. Logout performs RP-initiated logout, not just a local cookie
clear.** `POST /auth/logout` clears this hub's session cookie and a
second short-lived `sso_id_token` cookie (the raw id_token, set at
login, kept only for this purpose), then hands the frontend an
`end_session_endpoint` URL (from Authentik's discovery document) with
`id_token_hint` to do a full browser redirect through. Without this, a
staff member clicking "Sign in with Authentik" right after logging out
would silently re-authenticate with no credential prompt, since
Authentik's own session cookie would still be live — logout would appear
broken. This costs one extra cookie and one extra discovery-document
field; accepted as worth it for correct logout behavior.

**8. `users.hashed_password` becomes nullable, not dropped —
lower-risk than a destructive column removal.** `password_reset_token_hash`/
`password_reset_expires_at` are left exactly as they were (already
nullable, simply unused) rather than also being altered or dropped. A
clean removal was considered (this hub's usual "delete completely what's
unused" convention) and rejected here specifically because a column drop
is much harder to walk back on a live deployment than a nullability
change — this migration favors an easy rollback over schema tidiness.
`StaffUserCreate.email` becomes **required** (the DB column itself stays
nullable, for old rows) — a staff row created without one could never
sign in via Authentik.

## Consequences

- Nothing downstream of login changes: `core/deps.py`'s `get_current_user`
  (JWT-in-cookie validation, `token_version` revocation) and all of
  Casbin/RBAC/deployment-scoping work exactly as before, since this hub
  still mints its own signed session JWT/cookie after a successful SSO
  round-trip.
- The `staff.reset_password` permission and
  `POST /users/{id}/reset-password` endpoint are retired (migration
  `024_authentik_sso.py` deletes the permission catalog row and any grant
  of it) — there's no password left for an admin to reset.
- Self-service password-reset notifications
  (`services/notifications/service.py`'s `send_password_reset`,
  `TEMPLATE_PASSWORD_RESET`, `templates/password_reset.html`) are removed
  — nothing sends this email anymore.
- **Operational**: the seeded `admin` user (`002_seed_admin.py`) has no
  guaranteed `email` set. Before cutover on any real deployment, an admin
  must set a matching `email` on at least one active `User` row (Staff
  page, or directly in the DB) so someone can still sign in post-cutover
  — this is a manual per-deployment step, not something a migration can
  fix retroactively.
- A future "invite a new staff member via email" flow, if ever built,
  would still need this same pre-create step today — there is no
  self-service signup, by design (decision 3).
