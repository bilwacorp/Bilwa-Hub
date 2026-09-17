# ADR-009: RBAC Permission Audit and Security Review

Status: Accepted. Implemented in `app/core/url_safety.py`, `app/core/
rate_limit.py`, `api/routers/register.py`, `app/integrations/github/
webhook_api.py`, `app/schemas.py` (`RegisterRequest._safe_base_url`), and
migration `021_audit_ip_rbac_events.py`. No new permissions added — see
Decision 1 for why.

## Context

Phase 14 lists twelve "potential new permissions" and says to "add
permissions only where required." Phase 15 asks for a full security
review across sixteen specific concerns and calls out SSRF through
deployment URLs by name. Both phases are grouped in one ADR because
Phase 14's outcome (no new permissions) only makes sense next to Phase
15's outcome (two real gaps found and fixed) — together they show the
audit was done, not skipped.

## Decision 1 — Phase 14's twelve suggested permissions: none added

Each of the twelve was checked against the existing 45-permission catalog
in `core/permissions.py` before deciding whether it was missing:

| Suggested | Disposition |
|---|---|
| `integrations.view` | Already exists (migration `020_integrations_permission.py`) |
| `integrations.manage` | Rejected — the Integration Center page (ADR-008) is read-only plus one already-gated `github.manage`-protected test-connection button; there is no other mutating action on that page for a separate permission to protect |
| `github.view` / `github.manage` | Already exist |
| `releases.view` | Rejected — a release is rendered inline in the deployment detail page's Software section (Phase 4/5), gated by the existing `deployments.view`. Splitting it out would fragment one GET response's visibility into two permissions for no caller that needs one without the other, breaking the established GitHub-panel precedent (ADR-008 decision 1) of reusing `deployments.view` for everything a deployment's own detail page shows |
| `deployments.view_lineage` | Rejected — same reasoning as `releases.view`; lineage is part of the one deployment detail response |
| `deployments.view_timeline` | Rejected — same reasoning; the timeline (ADR-008 decision 1) is scoped by `deployment_id`, already covered by `deployments.view`/`deployments.view_all` |
| `operations.view_dashboard` | Already covered by `dashboard.view` (migration `019_dashboard_permission.py`), just a different name for the same action |
| `events.view` / `events.view_all` | Already exist |
| `actions.retry` | Already exists as `actions.retry` (Phase 12/13's retry-and-failure-handling work) |
| `maintenance.approve` | Rejected — this would contradict Phase 8's own explicit design that the workflow engine determines approvers via `ApprovalRule`s (`casbin_role`/`explicit_users` strategies), not a static permission checked at the route. A `maintenance.approve` permission would be a second, competing way to gate the same action and nothing in the approvals engine would ever consult it |

No `Permission` catalog migration was written, because nothing above
qualified as "required" under Phase 14's own instruction. This is a
negative result worth recording precisely because the temptation with a
list like this is to add all twelve to look thorough — six already exist
under a different name, and the other six would fragment a single-GET
response's visibility or re-implement a routing decision the workflow
engine already owns.

## Decision 2 — Security review findings and fixes

Phase 15's sixteen-item checklist, reviewed one by one:

**Fixed by this phase:**

- **SSRF through deployment URLs / arbitrary outbound requests.**
  `Deployment.base_url` (set at `/register` time, called back into by
  `services/deployment_client.py` for every renew/suspend/change-plan/
  health-check action) had no validation before this phase — a malicious
  or compromised deployment could register a `base_url` pointing at
  `169.254.169.254`, a private RFC1918 address, or `localhost`, and every
  future hub-initiated action call would hit it. Fixed with a two-layer
  check (`app/core/url_safety.py`): a synchronous scheme/hostname/
  literal-IP check (`validate_url_format`, wired into
  `RegisterRequest.base_url` as a Pydantic field validator — rejects
  before the request body even reaches the router) plus an asynchronous
  DNS-resolution check (`assert_hostname_resolves_publicly`, called from
  `register.py` after token validation) that resolves the hostname and
  rejects if any resolved address is private/loopback/link-local/
  reserved/multicast. The DNS check is deliberately ordered **after**
  token validation, not before — resolving an arbitrary attacker-supplied
  hostname on behalf of an unauthenticated caller would itself be a
  minor DNS-probing oracle, so an invalid token now gets rejected before
  the hub ever performs that lookup. The residual DNS-rebinding gap (a
  hostname that resolves publicly at registration time but is
  repointed at a private address before a later action call) is
  documented in the module's docstring as an accepted, not fixed, risk —
  closing it fully would require re-resolving on every single outbound
  call and pinning the resolved IP through the whole request, which
  `services/deployment_client.py` doesn't currently support and this
  phase didn't extend it to do.

- **Rate limiting.** Neither of this hub's two genuinely public,
  non-session endpoints (`POST /register`, `POST /github/webhooks/
  {integration_id}`) had any rate limit. Fixed with a minimal Redis
  fixed-window counter (`app/core/rate_limit.py`) — `/register` limited
  to 10/min per caller IP (guessing a registration token by brute force
  is the threat model), the GitHub webhook limited to 120/min per
  integration id (blunting a flood or signature-brute-force attempt, not
  modeling legitimate traffic precisely — GitHub itself sends at most a
  handful of webhooks per second even under heavy activity). Matches
  `core/cache.py`'s existing degrade-gracefully posture: if Redis is
  unreachable, `enforce_rate_limit` logs a warning and fails open rather
  than 500ing or blocking traffic — availability over strict enforcement,
  consistent with how Casbin's own cross-worker sync already treats
  Redis as optional. Deliberately **not** applied to any Casbin-gated
  staff route — those are already authenticated, authorized, and
  audited; a staff member hitting their own dashboard hard is not the
  threat this closes.

**Reviewed, found already correct, no change needed:**

- **Webhook verification / webhook spoofing** — `github/security.py`'s
  `verify_signature` already does constant-time HMAC-SHA256 comparison
  against the raw request body (`hmac.compare_digest`), and
  `webhook_api.py` reads `raw_body = await request.body()` before any
  JSON parsing, so the bytes verified are exactly the bytes GitHub
  signed — not a re-serialized object that could diverge on whitespace/
  key-ordering.
- **API credentials / action keys** — already covered by the two-
  credential design (`CLAUDE.md`'s "The two-credential design" section):
  `api_key` hash-only (verify-only use case), `action_key`
  Fernet-encrypted (must be presented back, so hash-only doesn't work).
  Nothing about this phase's SSRF or rate-limit work touches how either
  credential is stored or verified.
- **Replay attacks** — `github/webhook_api.py`'s unique constraint on
  `(integration_id, delivery_id)` already rejects a redelivered webhook
  as a no-op `duplicate_delivery`, predating this phase.
- **CSRF / session security** — the auth cookie is
  `SameSite=Lax; Path=/api` (see `CLAUDE.md`'s Docker/nginx section) and
  the frontend/backend are same-origin behind the one nginx proxy in
  front of this backend; there is no cross-origin form-post surface for
  a CSRF token to protect that `SameSite=Lax` doesn't already block.
- **Audit integrity** — no route anywhere (`api/routers/events.py` or
  otherwise) exposes PATCH/DELETE for `OperationalEvent` — see ADR-010
  for the full audit-immutability writeup, since Phase 19 asks for this
  explicitly and it's verified there with a test.
- **IDOR / row-level access** — every router taking a `deployment_id`
  already 404s via `_get_visible_or_404` for a caller outside their
  assigned scope (see `core/permissions.py`'s "Row-level visibility"
  section) — this predates Phase 15 and nothing in this batch changed
  that logic; it was reviewed, not modified.
- **Unsafe workflow expressions** — `workflow/parser.py`'s
  `validate_bpmn` already rejects `scriptTask`/`preScript`/`postScript`
  outright, and gateway conditions only ever go through `rule_engine`
  (`workflow/engine.py`'s `RuleScriptEngine`), a sandboxed grammar with
  no `eval`/`exec` path — predates this phase, reviewed and confirmed
  unchanged.
- **Authentication / authorization** — `core/deps.py`'s
  `token_version`-based revocation plus live `is_active` re-check on
  every request (not baked into the JWT) was already in place; Casbin
  enforces every permission check server-side, not just in the frontend
  nav guards.

No item on the checklist was left "reviewed" without either a fix or an
explicit reason it needed none — the two genuinely open gaps (SSRF,
rate limiting) were the only ones with no existing mitigation.

## Consequences

- `Deployment.base_url` can now only ever be set to a URL whose hostname
  resolves publicly at registration time — a legitimate deployment
  behind a normal public DNS name is unaffected; only literal private/
  loopback/reserved addresses and hostnames that resolve to them are
  rejected.
- `/register` and the GitHub webhook endpoint now return 429 under
  sustained abuse instead of accepting unlimited attempts — with no
  behavior change for normal traffic volumes.
- No new permissions were added to the catalog — the next phase to touch
  `core/permissions.py` should re-check this ADR's table before assuming
  a "missing" permission from a future doc's suggested list is actually
  missing.
