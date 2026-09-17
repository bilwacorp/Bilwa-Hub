"""HUB-Expansion.md Phase 19 — "audit: ... IP where appropriate." A
contextvar rather than threading `request: Request` through every one of
the ~30 existing `record_event()` call sites across every router: `app/
main.py`'s middleware sets this once per request, and `services/
events.py`'s `record_event()` reads it automatically. A background task
(the maintenance scheduler, a Celery task) has no request in flight, so
this reads back None there — correctly: "where appropriate" excludes
system-originated events, which already carry `actor_type=system` and no
`actor_id` for the same reason."""
import contextvars
from typing import Optional

_client_ip: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("client_ip", default=None)


def set_client_ip(ip: Optional[str]) -> None:
    _client_ip.set(ip)


def get_client_ip() -> Optional[str]:
    return _client_ip.get()
