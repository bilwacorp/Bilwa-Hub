# Integration Security Review — GitHub (HUB-Expansion.md Phase 3)

Reviewed before considering Phase 3 complete, per the phase brief's own
checklist. Each item below states what was checked, what was found, and
what (if anything) was changed as a result.

## GitHub credential handling

Access token and webhook secret are Fernet-encrypted
(`services/crypto.py`, the same module/key as `Deployment
.action_key_encrypted`) in `GitHubIntegration.access_token_encrypted`/
`webhook_secret_encrypted`. Never returned by any API response —
`GitHubIntegrationOut` exposes only `has_access_token`/`has_webhook_secret`
booleans, built by hand in `api.py`'s `_integration_out()` rather than via
`model_validate(integration)`, specifically so no response schema field
can accidentally alias the encrypted (or decrypted) value. Verified in
`tests/test_github_credentials.py`: create/list/update never leak either
secret in the response body, and the stored value round-trips through
`crypto.decrypt()` correctly while never equaling the plaintext in the
database. Never logged — the only place a token is decrypted is
`client.py`'s `_request()`, which builds the `Authorization` header
in-memory and never logs it; error messages surfaced to callers come
from GitHub's own response body (`resp.text[:300]`), which never echoes
the request's own `Authorization` header back.

## Webhook signature validation

HMAC-SHA256 over the raw request body, compared with `hmac
.compare_digest` (timing-safe) — see `security.py`. Verified in
`tests/test_github_webhooks.py`: a valid signature is accepted, an
invalid one (`test_invalid_signature_is_rejected_and_not_persisted`,
`test_wrong_secret_is_rejected`) and a missing one
(`test_missing_signature_header_is_rejected`) are all rejected with 401
and none of them create a `GitHubWebhookEvent` row for the rejected
payload — only a `github.webhook_failed` `OperationalEvent`, so a flood
of invalid signature attempts is visible in the event log without
polluting the webhook-event table with content HUB never validated.

## Replay / duplicate events

The unique constraint on `GitHubWebhookEvent.delivery_id` is the actual
mechanism, not a best-effort pre-check — verified in
`test_duplicate_delivery_id_is_idempotent` by sending the identical
signed payload+headers twice and confirming exactly one row exists and
the second request returns `200 duplicate_delivery` without enqueueing a
second processing task.

## Authorization

Four permissions (`github.view`/`manage`/`test_connection`/`sync`), all
server-side via `require_permission(...)` — never a frontend-only gate.
Verified in `tests/test_github_authorization.py`: no permission at all →
403 on every github.* route; engineer (view/test_connection/sync, not
manage) can browse/sync/test but not create an integration; admin can.

## Deployment row-level visibility

The one deployment-scoped GitHub surface, `GET /deployments/{id}/github`,
reuses `deployments.py`'s existing `_get_visible_or_404` — same 404 (not
403, not a differently-shaped error) for a deployment the caller can't
see, whether it doesn't exist or simply isn't assigned to them. Verified:
`test_unassigned_engineer_cannot_see_deployment_github_info` (404),
`test_assigned_engineer_sees_deployment_github_info` (200),
`test_engineer_cannot_see_github_info_for_someone_elses_deployment` (200
for their own, 404 for the other), and — the one that would have caught
a scoping mistake most easily —
`test_github_manage_only_permission_cannot_use_deployments_view_endpoint`,
confirming `github.manage` alone does **not** grant access to this route
(it's gated by `DEPLOYMENTS_VIEW`, not any `github.*` permission).

## SSRF risk

Checked specifically because `services/deployment_client.py`'s own
`base_url` (staff-entered, used directly in outbound calls) was flagged
as an unhardened SSRF-adjacent surface in the Phase 0 audit
(`docs/architecture/current-state.md`). GitHub's client is structurally
safer: `client.py`'s `_API_BASE = "https://api.github.com"` is a
hardcoded constant, never derived from any request input, so there's no
equivalent "staff/attacker controls the host" vector at all.

The one place user input reaches a URL is `full_name` ("owner/repo"),
interpolated into `f"/repos/{full_name}"`. Passing an absolute URL
(`"http://evil.example/x"`) as `full_name` does **not** let httpx switch
hosts — the resulting string still starts with the literal `/repos/`
prefix, so it's always a relative path joined against `_API_BASE`, never
an absolute-URL override. Even so, this review added a strict
`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$` pattern to
`GitHubRepositoryAddRequest.full_name` (previously length-limited only)
as defense in depth against any character-injection edge case in how the
path gets built or logged — verified in `tests/test_github_validation.py`
(a malformed value is rejected with a clean 422, not a 500; a
path-traversal-shaped value is rejected the same way; a well-formed value
passes validation and reaches the real GitHub call).

## Rate limiting

**GitHub's** rate limits, outbound: `client.py` classifies a 429 or an
exhausted-primary-limit 403 as `GitHubRateLimitError`, which `tasks.py`
lists in `autoretry_for` with backoff+jitter — verified in
`tests/test_github_client.py` (429 and 403+`x-ratelimit-remaining: 0` are
classified correctly; a plain 403/404, which would never succeed on
retry, is not).

**HUB's own webhook endpoint**, inbound: not rate-limited. A caller who
knows (or brute-forces) a `GitHubIntegration` id can send unlimited
invalid-signature requests, each persisting one `github.webhook_failed`
`OperationalEvent` row. This is a known, unaddressed limitation, not an
oversight — this codebase has no rate-limiting middleware anywhere yet
(checked: no existing precedent to reuse), and adding one now would be
new infrastructure beyond this phase's scope per the brief's own "do not
introduce ... unless the existing codebase proves necessary." Worth
revisiting if this ever becomes a real abuse vector — see Known
Limitations in the completion summary.

## Secret leakage

Covered under "GitHub credential handling" above. Additionally checked:
`GitHubWebhookEvent.payload` stores GitHub's raw webhook body — this
never contains the webhook secret or access token (GitHub signs the
payload with the secret, it doesn't put the secret *in* the payload), so
persisting it verbatim for audit is safe.

## Audit / event integrity

No API route deletes an `OperationalEvent` or a `GitHubWebhookEvent` row
— consistent with the existing "audit records should not be casually
editable or deletable" principle (`HUB-Expansion.md` Phase 19,
already-satisfied per Phase 1's own design). A signature-failure attempt
is recorded (`github.webhook_failed`) even though nothing else about that
request is trusted, so a pattern of failed attempts is itself visible in
the event log.

## GitHub API failure handling

`GitHubApiError` (permanent — 404/422/bad-credentials) vs
`GitHubRateLimitError`/`GitHubConnectionError` (transient) are
distinguished specifically so Celery's `autoretry_for` only retries the
latter two — verified in `tests/test_github_client.py`. A permanent
failure during a webhook processing task is caught in
`tasks._process_webhook_event` and does **not** retry (returns cleanly,
worker stays healthy); a transient one re-raises so Celery's own
backoff/retry takes over.

## Not weakened

No existing security control (Casbin, JWT/cookie auth, row-level
visibility, the two-credential deployment design, the workflow engine's
sandboxed rule evaluator) was modified by this phase — every addition is
new, additive surface gated by the same mechanisms everything else uses.
