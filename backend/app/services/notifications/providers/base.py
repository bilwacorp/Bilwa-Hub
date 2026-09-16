"""Provider interface every notification channel (SMTP, WhatsApp today; SMS/
Push/Slack later) implements. Business code (service.py) only ever talks to
this interface — it never imports a concrete provider directly."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OutboundMessage:
    """Channel-agnostic message. SMTP maps this onto a MIME email; WhatsApp
    uses `to` + `text_body` and ignores the rest."""
    to: list[str]
    subject: str
    html_body: Optional[str] = None
    text_body: Optional[str] = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: Optional[str] = None


class NotificationProvider(ABC):
    """Abstract provider. `name` is what's stored in NotificationLog.provider."""

    name: str

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        """Send the message. Raise ProviderException (or a subclass) on failure —
        never let the underlying transport's exception type leak to callers."""

    @abstractmethod
    async def validate(self, message: OutboundMessage) -> None:
        """Raise a *ValidationException if the message can't be sent as-is
        (bad addresses, no content, etc). Called before send()."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Best-effort connectivity check. Never raises — returns False on
        any failure so callers can surface provider health without a try/except."""
