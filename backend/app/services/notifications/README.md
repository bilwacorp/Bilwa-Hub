# Notifications

Alerts BilwaCorp staff (every user holding the `admin` or `engineer` role —
see `recipients.py`) by email and WhatsApp when a fleet event needs their
attention: a support ticket is raised, or a client deployment reports a
pending renewal/upgrade request. Ported from PoultryPro-CBF's own
`services/notifications/` package, trimmed to this hub's two triggers, two
roles, and (deliberately) no DB-editable template UI — see "Differences from
PoultryPro-CBF" below.

## Architecture

```
NotificationService (service.py)
    -> repository.py                  creates a NotificationLog row (status=pending)
    -> tasks.send_email_task.delay(...)      enqueues a Celery task, returns immediately
    -> tasks.send_whatsapp_task.delay(...)   (WhatsApp channel — same pattern)

Celery worker (tasks.py, docker-compose.yml's celery-worker service)
    -> render.py               renders the Jinja2 template -> (html, text) / text
    -> providers/smtp.py       sends via aiosmtplib, raises typed exceptions on failure
    -> providers/whatsapp.py   POSTs to your configured gateway, raises typed exceptions on failure
    -> repository.py           updates NotificationLog: sending -> sent | failed
```

Nothing in this package ever sends a message inline on the request thread.
Business code should only ever import `NotificationService` (via the
`get_notification_service` FastAPI dependency, or directly as done by
`app/services/notification_triggers.py`) — never a provider or `tasks.py`.

## Triggers

`app/services/notification_triggers.py` is where the fleet events are wired
to this package:

- `notify_support_ticket_raised` — called from
  `api/routers/ingest.py`'s `ingest_support_ticket`, right after the
  `SupportTicket` row is created.
- `notify_subscription_request_raised` — called from `ingest.py`'s
  `ingest_heartbeat`. `pending_requests` arrives as this deployment's *full
  current list* on every heartbeat, not an event — the trigger diffs the
  incoming list's request `id`s against the immediately-preceding snapshot's
  to find ones that are genuinely new, so an unchanged pending request
  doesn't re-notify every 2 hours.
- `notify_subscription_expiring` — called from
  `core/expiry_reminder_scheduler.py`, a background poll loop (not an
  inbound event — nothing pushes "about to expire", so the hub checks for
  it) that wakes hourly and fires a one-time alert once a deployment's
  latest-known `expiry_date` falls within `EXPIRY_REMINDER_DAYS_BEFORE`
  days (default 7). Idempotent per exact `expiry_date` value
  (`Deployment.expiry_reminder_sent_for`) — never nags every tick, and a
  renewal that changes `expiry_date` (reflected on the deployment's next
  heartbeat) makes a fresh reminder eligible again automatically. Doesn't
  fire for a deployment that's already past its expiry with no future date
  on file — that's a distinct "already expired" case this doesn't cover.

All three fan out via `recipients_for_deployment()` (`recipients.py`): the staff
explicitly assigned to that deployment (Deployments → a deployment →
"Assigned Staff", `DeploymentStaffAssignment` / `PUT /deployments/{id}/staff`)
if any, otherwise every active `fleet_staff()` holder (everyone with
FLEET_MANAGE) — an unassigned deployment still notifies everyone rather than
going silent. One email per recipient with an email on file, one WhatsApp
message per recipient with a phone on file (`User.phone`, set from the Staff
page).

One more trigger lives outside `notification_triggers.py`, called directly
from `api/routers/auth.py`'s `forgot_password` endpoint instead: a self-
service password-reset email (`TEMPLATE_PASSWORD_RESET`,
`NotificationService.send_password_reset`), targeted at exactly the one
staff member who requested it — not a fan-out, so it doesn't go through
`recipients_for_deployment()`. Email only, no WhatsApp counterpart, and
never resendable — its `reset_url` context key is in
`constants.SENSITIVE_CONTEXT_KEYS` (the live one-time reset token is
embedded in that URL), so `NotificationLog.payload` only ever shows `"***"`
for it.

Adding a third trigger (a new template) is: a new `TEMPLATE_*` constant in
`constants.py`, a `templates/<key>.html` + `templates_whatsapp/<key>.txt`
pair, a `send_*_alert`/`send_*_whatsapp` convenience method on
`NotificationService`, and a call site in `notification_triggers.py` (or
directly on `NotificationService` wherever the event happens) — business
code never touches `tasks.py` or a provider directly.

## Setup

1. Set the SMTP env vars below in `backend/.env` (any standard SMTP server —
   self-hosted Postal, Hostinger/Titan, Gmail, ...).
2. Run Redis + the Celery worker alongside the API — both are already wired
   into `docker-compose.yml` (`redis`, `celery-worker`). Local dev:
   `celery -A app.services.notifications.tasks:celery_app worker --loglevel=info`
   (needs a local Redis; `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` default
   to `redis://localhost:6379/1`).
3. Send yourself a test email as staff: `POST /api/v1/notifications/test-email`
   with `{"recipient": "you@example.com"}`.

**Example — Hostinger/Titan mail:**
```
SMTP_HOST=smtp.hostinger.com
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_TLS=false
SMTP_USERNAME=you@yourdomain.com
SMTP_PASSWORD=your-mailbox-password
FROM_EMAIL=you@yourdomain.com
```

**Example — Postal (typically port 25 or 587 with STARTTLS):**
```
SMTP_HOST=postal.yourdomain.com
SMTP_PORT=587
SMTP_TLS=true
SMTP_USE_SSL=false
```

**Example — MXroute / cPanel-style mail hosting:**
```
SMTP_HOST=<your assigned server, e.g. sunfire.mxrouting.net>
SMTP_PORT=587
SMTP_USE_SSL=false
SMTP_TLS=true
SMTP_USERNAME=alerts@yourdomain.com   # full email address, not just "alerts"
SMTP_PASSWORD=<mailbox password, not the MXroute account/billing login>
FROM_EMAIL=alerts@yourdomain.com
```

### First-deploy gotchas seen in practice

- **Port 465 times out but 587 connects** — many cloud/VPS hosts block outbound
  port 465 (and sometimes 25) by default to curb spam, while leaving 587
  open, or vice versa. If SMTP connection attempts hang and time out (not an
  auth error), try the other port before assuming credentials are wrong. If
  every SMTP port times out, the host is blocking outbound SMTP wholesale —
  contact the hosting provider's support to have it lifted (routine
  request), or check the mail provider for a documented relay/submission
  endpoint on a non-standard port.
- **`535 Incorrect authentication data`** — the connection succeeded but the
  login was rejected. For MXroute and most cPanel-style mail hosts,
  `SMTP_USERNAME` must be the *full email address* (`alerts@yourdomain.com`),
  not just the mailbox's local part (`alerts`) — this is the most common
  cause. Also check for trailing whitespace on `SMTP_USERNAME`/`SMTP_PASSWORD`
  from copy-pasting into Dokploy's Environment tab, and that the password is
  the mailbox's own password, not an account/billing login.

## WhatsApp setup

`providers/whatsapp.py` doesn't know about any specific vendor — it POSTs a
Jinja2-rendered JSON body to `WHATSAPP_API_URL` with an auth header, both
fully driven by Settings.

**Example — Evolution API** (`POST /message/sendText/{instance}`, header `apikey: <token>`, no `Bearer` prefix):
```
WHATSAPP_API_URL=https://evolution.yourdomain.com/message/sendText/your-instance
WHATSAPP_AUTH_HEADER=apikey
WHATSAPP_AUTH_SCHEME=
WHATSAPP_API_KEY=your-evolution-api-key
WHATSAPP_PAYLOAD_TEMPLATE={"number": "{{ to }}", "text": {{ message | tojson }}}
```

**Example — Meta WhatsApp Cloud API:**
```
WHATSAPP_API_URL=https://graph.facebook.com/v20.0/<phone-number-id>/messages
WHATSAPP_AUTH_HEADER=Authorization
WHATSAPP_AUTH_SCHEME=Bearer
WHATSAPP_API_KEY=your-meta-permanent-or-system-user-token
WHATSAPP_PAYLOAD_TEMPLATE={"messaging_product": "whatsapp", "to": "{{ to }}", "type": "text", "text": {"body": {{ message | tojson }}}}
```

If your gateway needs a header beyond the single auth one (e.g. an instance
ID), set `WHATSAPP_EXTRA_HEADERS` to a JSON object string, e.g.
`{"X-Instance-Id": "abc123"}` — merged on top of the auth header.

A staff user only receives WhatsApp alerts once they have a phone number on
file (Staff page → edit → Phone) — a missing phone just means "email only",
same posture as a missing email.

## Environment variables

| Variable | Purpose |
|---|---|
| `NOTIFICATIONS_ENABLED` | Global kill switch — `false` stops every channel. Still logs the attempt as `cancelled`, never silently drops it. |
| `WHATSAPP_NOTIFICATIONS_ENABLED` | Narrower switch scoped to WhatsApp only, layered under the global one. |
| `SMTP_HOST` / `SMTP_PORT` | SMTP server |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | SMTP credentials |
| `SMTP_USE_SSL` | `true` for implicit TLS (port 465). Takes priority over `SMTP_TLS`. |
| `SMTP_TLS` | `true` for STARTTLS (port 587/25). Ignored when `SMTP_USE_SSL` is true. |
| `SMTP_TIMEOUT_SECONDS` | Connection timeout |
| `FROM_EMAIL` / `FROM_NAME` | Default sender identity |
| `REPLY_TO` | Optional default Reply-To |
| `WHATSAPP_API_URL` | Gateway send-message endpoint. Empty (default) leaves WhatsApp unconfigured — sends fail fast with a clear error. |
| `WHATSAPP_API_METHOD` | HTTP method (default `POST`) |
| `WHATSAPP_API_KEY` / `WHATSAPP_AUTH_HEADER` / `WHATSAPP_AUTH_SCHEME` | Auth header shape |
| `WHATSAPP_EXTRA_HEADERS` | Optional JSON object string of additional static headers |
| `WHATSAPP_PAYLOAD_TEMPLATE` | Jinja2 template rendered to the JSON request body |
| `WHATSAPP_TIMEOUT_SECONDS` | Request timeout |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis, dedicated DB index (default `redis://redis:6379/1` in compose) — this is a *real* dependency for this feature, unlike `REDIS_URL` (the Casbin watcher's best-effort pub/sub, which degrades gracefully without it) |

## Admin API (`/api/v1/notifications`, FLEET_MANAGE — admin + engineer)

- `GET /notifications` (aliased `/history`) — paginated list, filterable by
  `status`, `channel`, `recipient`.
- `GET /notifications/{id}` — detail.
- `POST /notifications/resend/{id}` — re-enqueues a failed/stuck send.
- `POST /notifications/test-email` / `POST /notifications/test-whatsapp` —
  sends a real test message through the configured provider.
- `DELETE /notifications/{id}` — removes a log entry.

## Security notes

- SMTP/WhatsApp errors never leak credentials — `providers/*.py` only log
  host/URL on failure, never the password/API key.
- Recipient addresses are validated (`email_validator`) before every send;
  WhatsApp recipients against a loose E.164-ish pattern (`core/phone.py`).
- `NotificationLog.payload` redacts anything in `constants.SENSITIVE_CONTEXT_KEYS`
  — no current template here passes a secret, but this is the same
  defense-in-depth PoultryPro-CBF's version of this package uses.

## Differences from PoultryPro-CBF's version of this package

- No `EmailTemplate`/`WhatsAppTemplate` DB tables or template-editor admin
  UI — templates are file-based only (`templates/`, `templates_whatsapp/`).
  Editing copy is a code change + deploy, not an in-app edit.
- No push channel (no mobile app in this hub).
- Kill switches are env vars (`core/config.py`), not a DB-backed `AppSetting`
  toggle with its own admin-UI switch.
- Recipients are resolved by Casbin role (`admin` + `engineer`, i.e.
  everyone with `FLEET_MANAGE`) rather than a fixed "admins of this tenant"
  concept — this hub has one flat staff list, not per-deployment admins.
