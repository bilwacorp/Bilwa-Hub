"""Shared constants for the notifications subsystem — template names and the
default provider identifier. Channel/status values themselves live on the
NotificationChannel/NotificationStatus enums in app/models.py, the single
source of truth for anything persisted to notification_logs."""

DEFAULT_PROVIDER = "smtp"
DEFAULT_WHATSAPP_PROVIDER = "whatsapp"

# One file per notification type in templates/ (+ templates_whatsapp/ for
# the WhatsApp equivalent) — add a new constant + pair of template files to
# add a future alert type. See README.md's "Adding a new template" section.
TEMPLATE_SUPPORT_TICKET_RAISED = "support_ticket_raised"
TEMPLATE_SUBSCRIPTION_REQUEST_RAISED = "subscription_request_raised"
TEMPLATE_SUBSCRIPTION_EXPIRING = "subscription_expiring"

# The shared header/footer wrapper every email template extends via
# {% extends "base.html" %} — never sent standalone, only reachable through
# Jinja's loader.
TEMPLATE_BASE_LAYOUT = "base"

# Fields that must never be written to NotificationLog.payload or logged —
# no current template in this hub passes a secret, but this is the same
# last line of defense PoultryPro-CBF's notifications package uses, kept
# here so a future template (e.g. a one-time action link) doesn't have to
# reinvent it.
SENSITIVE_CONTEXT_KEYS = {"password", "otp_code", "otp", "token", "reset_token", "secret"}
