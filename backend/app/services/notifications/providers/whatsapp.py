"""WhatsApp provider — a generic HTTP adapter, not tied to any one vendor.
Self-hosted WhatsApp gateways (Evolution API, Wasapi/Wassenger-style senders,
Meta's Cloud API, ...) all boil down to "POST some JSON to a URL, with some
auth header". WHATSAPP_API_URL/_PAYLOAD_TEMPLATE/_AUTH_* (Settings) describe
exactly that shape, so pointing this at a different gateway is a .env change,
never a code change — see README.md for per-vendor template examples."""

import json
import logging
from urllib.parse import urlsplit

import httpx
from jinja2 import Environment

from app.core.config import settings
from app.core.phone import PHONE_RE, normalize_phone
from app.services.notifications.exceptions import (
    ProviderException,
    WhatsAppConfigurationException,
    WhatsAppConnectionException,
    WhatsAppValidationException,
)
from app.services.notifications.providers.base import NotificationProvider, OutboundMessage

logger = logging.getLogger(__name__)

_payload_env = Environment(autoescape=False)
_payload_env.filters["tojson"] = lambda v: json.dumps(v)


class WhatsAppProvider(NotificationProvider):
    name = "whatsapp"

    async def validate(self, message: OutboundMessage) -> None:
        if not settings.WHATSAPP_API_URL:
            raise WhatsAppConfigurationException(
                "WHATSAPP_API_URL is not set — configure the WhatsApp gateway before sending"
            )
        if not (message.text_body or message.subject):
            raise ProviderException("WhatsApp message has no text content to send")
        for recipient in message.to:
            if not PHONE_RE.match(normalize_phone(recipient)):
                raise WhatsAppValidationException(recipient)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.WHATSAPP_API_KEY:
            value = (
                f"{settings.WHATSAPP_AUTH_SCHEME} {settings.WHATSAPP_API_KEY}".strip()
                if settings.WHATSAPP_AUTH_SCHEME
                else settings.WHATSAPP_API_KEY
            )
            headers[settings.WHATSAPP_AUTH_HEADER] = value
        if settings.WHATSAPP_EXTRA_HEADERS:
            try:
                headers.update(json.loads(settings.WHATSAPP_EXTRA_HEADERS))
            except json.JSONDecodeError:
                logger.warning("WHATSAPP_EXTRA_HEADERS is not valid JSON — ignoring")
        return headers

    def _render_payload(self, to: str, text: str) -> str:
        try:
            rendered = _payload_env.from_string(settings.WHATSAPP_PAYLOAD_TEMPLATE).render(to=to, message=text)
        except Exception as e:
            raise ProviderException(f"WHATSAPP_PAYLOAD_TEMPLATE failed to render: {e}") from e
        try:
            json.loads(rendered)
        except json.JSONDecodeError as e:
            raise ProviderException(
                f"WHATSAPP_PAYLOAD_TEMPLATE did not render to valid JSON: {e}"
            ) from e
        return rendered

    async def send(self, message: OutboundMessage) -> None:
        await self.validate(message)

        text = message.text_body or message.subject or ""
        headers = self._headers()
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=settings.WHATSAPP_TIMEOUT_SECONDS) as client:
            for recipient in message.to:
                to = normalize_phone(recipient)
                body = self._render_payload(to, text)
                try:
                    response = await client.request(
                        settings.WHATSAPP_API_METHOD,
                        settings.WHATSAPP_API_URL,
                        content=body,
                        headers=headers,
                    )
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    logger.warning("WhatsApp gateway unreachable (url=%s): %s", settings.WHATSAPP_API_URL, e)
                    raise WhatsAppConnectionException(f"Could not reach WhatsApp gateway at {settings.WHATSAPP_API_URL}") from e
                except httpx.HTTPError as e:
                    errors.append(f"{to}: {e}")
                    continue

                if response.status_code >= 300:
                    errors.append(f"{to}: HTTP {response.status_code} — {response.text[:500]}")

        if errors:
            raise ProviderException("WhatsApp send failed for: " + "; ".join(errors))

    async def health_check(self) -> bool:
        if not settings.WHATSAPP_API_URL:
            return False
        origin = urlsplit(settings.WHATSAPP_API_URL)
        base = f"{origin.scheme}://{origin.netloc}"
        try:
            async with httpx.AsyncClient(timeout=settings.WHATSAPP_TIMEOUT_SECONDS) as client:
                await client.get(base)
            return True
        except Exception:
            return False
