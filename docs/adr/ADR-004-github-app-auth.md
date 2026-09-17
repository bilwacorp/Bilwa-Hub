# ADR-004: GitHub App auth mode

Status: Accepted. Implemented in migration `022_github_app_auth.py`.

## Context

ADR-003 deliberately deferred GitHub App auth in favor of a
personal/fine-grained access token, pasted manually alongside a manually
pasted webhook secret. That's still correct and secure, but it's a worse
connection experience than the one-click "pick an org, pick some repos"
install flow GitHub Apps (and tools like Dokploy) offer. This ADR adds
that flow as a second `GitHubIntegration.auth_mode`, alongside — not
replacing — the PAT mode from ADR-003.

## Decisions

**1. Installation flow only, not "Sign in with GitHub."** HUB never needs
to act *as* a GitHub user — only as an installed app with repository
access. That means no OAuth client_id/client_secret/user-code exchange at
all: App ID + a PEM private key (to sign a short-lived App JWT) + the
installation id GitHub hands back after install is the complete
credential set. Simpler than a full OAuth app, and there's nothing to
narrow later — a user-identity flow was never in scope.

**2. One GitHub App serves every integration/installation; app
credentials are Settings, not DB columns.** `GITHUB_APP_ID`/
`GITHUB_APP_SLUG`/`GITHUB_APP_PRIVATE_KEY`/`GITHUB_APP_WEBHOOK_SECRET`
live in `core/config.py`, the same tier as `HUB_ENCRYPTION_KEY`/
`WHATSAPP_*` — optional (empty by default), required only if this mode is
used. Each `GitHubIntegration` row just stores `installation_id`; there's
no "register a second App" use case at this hub's scale, and per-App
credentials on every integration row would mean re-entering the same
private key N times for no benefit.

**3. The `installation` webhook is the source of truth for provisioning;
the OAuth-style callback is best-effort UX only.** GitHub's install flow
redirects the browser to a callback URL with `installation_id`, but that
redirect can be dropped (closed tab, network blip) without GitHub
retrying it — unlike a webhook delivery, which GitHub does retry.
`app_auth.upsert_installation()` is called from both the callback and the
`installation` webhook handler and is idempotent (get-or-create by
`installation_id`), so an integration is correctly provisioned the first
time *either* one succeeds, and correctly not duplicated if both do.

**4. The callback's `state` param is signed but not load-bearing.** It
reuses `core/security.py`'s existing `create_access_token`/`decode_token`
(HS256/`SECRET_KEY`) rather than a new crypto primitive, purely to
best-effort attribute `GitHubIntegration.created_by` to whoever clicked
"Connect with GitHub." An invalid, expired, or missing `state` degrades to
`created_by=None` — per decision 3, the callback was never the mechanism
that actually secures or provisions the integration, so failing loudly
here would only make the UX worse for zero security benefit.

**5. GitHub Apps have exactly one webhook URL (app-level), not one per
installation — so the existing per-integration `/github/webhooks/{id}`
path doesn't apply.** A new `POST /github/app/webhooks` endpoint, verified
against the one shared `GITHUB_APP_WEBHOOK_SECRET`, resolves the target
`GitHubIntegration` from `payload["installation"]["id"]` instead of a URL
path segment, then delegates to the same delivery-insert +
Celery-dispatch helper (`_accept_webhook_delivery`, factored out of the
existing per-integration handler) so both endpoints produce identical
`GitHubWebhookEvent` rows and idempotency behavior.

**6. `GitHubIntegration.access_token_encrypted` is reused, not
duplicated, for the cached installation token.** Same Fernet column, two
lifetime semantics depending on `auth_mode`: unlimited for a PAT, ~1h
(cached, refreshed via `access_token_expires_at`) for an installation
token. A separate `installation_token_encrypted` column would just be the
same secret-storage code path twice.

**7. `python-jose[cryptography]` (already a dependency, used for staff
login's HS256 tokens) also signs the App's RS256 JWT.** No new dependency
— `jose.jwt.encode` accepts a PEM string directly with `algorithm="RS256"`
via its `cryptography` backend, which `core/security.py` already pulls in.

**8. Repository access is auto-registered from the installation's own
webhooks, not left to a manual "add repository" step.** `installation`
(`action=created`)'s payload carries the initial repository list;
`installation_repositories` (`action=added`/`removed`) carries deltas
after that. Both are handled the same way every other GitHub Apps event is: parsed asynchronously by `webhooks.py`'s `_HANDLERS` dispatch (new
`_handle_installation`/`_handle_installation_repositories` entries)
after `webhook_api.py`'s fast path persists the delivery — no special
synchronous path, except `installation`'s own `action=deleted`, which
still needs to resolve/mark the integration *before* a
`GitHubWebhookEvent` row can even reference it, so that part alone stays
in `webhook_api.py`. A removed repo is deactivated
(`GitHubRepository.is_active=False`, `app_auth.deactivate_repositories`),
never deleted — its `GitHubPullRequest`/`Issue`/`Release`/`Commit` rows
still FK to it, and losing GitHub access shouldn't erase HUB's own
history. Reinstalling (or re-granting access) reactivates the same row
by `external_id` rather than creating a duplicate.

**9. The GitHub App itself can be created via GitHub's Manifest flow
("Set up GitHub App"), not just hand-registered — DB-first, env-var-
fallback, and this hub's first DB-stored runtime config.** Decisions 1-8
above still required an admin to manually register a GitHub App via
GitHub's Developer Settings UI and paste four credentials into env vars.
`POST /github/app/manifest` (`app/integrations/github/api.py`) builds the
manifest JSON (webhook/setup/redirect URLs derived from the authenticated
request's own Origin — this hub is single-origin, per CLAUDE.md — not a
user-typed field or a new Settings var); the frontend POSTs it to
`https://github.com/settings/apps/new` (or the org-scoped variant) via an
auto-submitting hidden form, since GitHub's manifest flow needs a real
top-level navigation to render its own confirmation page, not a fetch.
GitHub redirects to `GET /github/app/manifest-callback` with a one-time
`code`, exchanged (`app_auth.exchange_manifest_code`, no auth header —
this one GitHub endpoint needs none) for the new App's id/slug/private
key/webhook secret, persisted into a new `GitHubAppConfig` row
(`app_auth.save_app_config`, upsert-in-place — a practical singleton).

`app_auth._load_app_credentials(db)` is the one place that now resolves
which source wins: the DB row if present, else the `GITHUB_APP_*` env
vars (decisions 1-8, unchanged), else `None`. Every reader of
`settings.GITHUB_APP_*` switched to this. This is a deliberate,
**narrow** exception to "config is either Casbin-gated domain data or a
Settings env var" (confirmed by exploration — no prior
`AppSetting`/singleton-table precedent exists anywhere in this
codebase) — not a new general pattern to reach for elsewhere. It exists
here specifically because this one credential set can now be *generated
by the running app itself*, which a `.env` file fundamentally can't do.

The manifest-callback is the one route in this whole subsystem that
**requires** `github.manage` authentication, unlike the install
`/callback` (decision 3/4), which must stay public since a real
installation can legitimately happen from GitHub's side with no live Hub
session at all. The manifest flow has no such legitimate unauthenticated
case — it can only ever be reached right after an admin submitted the
manifest form from inside this Hub's own UI — so requiring auth closes
off "an attacker's own App adopted as this hub's App" for free, and no
extra signed `state` param is layered on top (GitHub's `code` is already
single-use, so there's nothing left for one to add here).

## Consequences

- PAT-mode integrations are entirely unaffected — `client.py`'s
  `_resolve_token` branches on `auth_mode`, and every existing PAT call
  site (`sync.py`, `api.py`) is unchanged in behavior, only in signature
  (each gained a `db` parameter, needed so a github_app-mode token refresh
  can persist the newly cached token).
- A future second GitHub App (e.g. one HUB wants to offer per a
  white-labeled deployment) would need decision 2 revisited — not
  expected at this hub's current single-org-per-integration scale.
- `GitHubRepository.is_active` (already a column, previously unused by
  any query or UI) is now load-bearing — the Repositories tab shows a
  removed repo struck through with an "Removed from installation" badge
  rather than silently keeping stale rows indistinguishable from live
  ones.
- Manual "add repository" (PAT mode's only option, still available for
  github_app-mode integrations too) and the webhook-driven auto-
  registration above share nothing but the `external_id` unique
  constraint — deliberately: manual add fetches the real REST API object
  (`sync.add_repository`, has `default_branch`/`html_url` from GitHub
  directly), while the webhook path only has GitHub's minimal repository
  shape and derives `owner`/`html_url` from `full_name`. A manual sync
  afterward corrects any derived field once the real API data is fetched.
- An ops team that already configured `GITHUB_APP_*` env vars sees no
  change — the DB row only exists once someone actually runs "Set up
  GitHub App," and env vars keep working as the fallback either way.
  Clearing the DB row (no UI for this yet, direct DB access only) reverts
  to whatever's in the env vars.
- Re-running "Set up GitHub App" replaces the stored config in place — the
  *old* GitHub App itself is left behind on GitHub, still existing but no
  longer referenced by this hub; deleting it there is a manual follow-up
  step this flow doesn't automate.
