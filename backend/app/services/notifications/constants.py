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
# app/workflow, app/approvals — sent to each candidate approver when a new
# WorkflowTask is created (see app/workflow/executor.py's
# _notify_candidate_approvers).
TEMPLATE_APPROVAL_REQUESTED = "approval_requested"

# The shared header/footer wrapper every email template extends via
# {% extends "base.html" %} — never sent standalone, only reachable through
# Jinja's loader.
TEMPLATE_BASE_LAYOUT = "base"

# Fields that must never be written to NotificationLog.payload or logged.
# None of these are sent by any template this hub currently has (self-
# service password reset was removed when login moved to Authentik SSO —
# see docs/adr/ADR-012-authentik-sso.md) — carried over from PoultryPro-
# CBF's notifications package as defense-in-depth in case a future
# template needs one of them.
SENSITIVE_CONTEXT_KEYS = {"password", "otp_code", "otp", "token", "reset_token", "reset_url", "secret"}
