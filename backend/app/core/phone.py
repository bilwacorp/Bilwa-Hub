"""Shared phone-number normalization/validation for the WhatsApp channel
(services/notifications/providers/whatsapp.py) and the Staff phone field
(api/routers/users.py) — ported from PoultryPro-CBF's app/core/phone.py."""

import re

# Loose E.164-ish check — deliberately permissive since gateways disagree on
# whether they want a leading "+". Validation runs against the *normalized*
# (digits + optional leading "+") form.
PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def normalize_phone(raw: str) -> str:
    return re.sub(r"[^\d+]", "", raw or "")


def is_valid_phone(raw: str) -> bool:
    return bool(PHONE_RE.match(normalize_phone(raw)))
