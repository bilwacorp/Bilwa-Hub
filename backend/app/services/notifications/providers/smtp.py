"""SMTP provider — sends transactional email through any standard SMTP
server/relay (Postal, Hostinger/Titan, Gmail, etc. — nothing here is
provider-specific). Credentials are only ever read from Settings, never
logged."""

import logging
from email.message import EmailMessage as MimeEmailMessage

import aiosmtplib
from email_validator import validate_email, EmailNotValidError

from app.core.config import settings
from app.services.notifications.exceptions import (
    EmailValidationException,
    ProviderException,
    SMTPConnectionException,
)
from app.services.notifications.providers.base import NotificationProvider, OutboundMessage

logger = logging.getLogger(__name__)

MAX_EMAIL_SIZE_BYTES = 20 * 1024 * 1024  # 20MB — generous headroom over most relays' own limit


class SMTPProvider(NotificationProvider):
    name = "smtp"

    async def validate(self, message: OutboundMessage) -> None:
        for address in [*message.to, *message.cc, *message.bcc]:
            try:
                validate_email(address, check_deliverability=False)
            except EmailNotValidError as e:
                raise EmailValidationException(address) from e

        size = len(message.html_body or "") + len(message.text_body or "")
        if size > MAX_EMAIL_SIZE_BYTES:
            raise ProviderException(
                f"Email payload ({size} bytes) exceeds the {MAX_EMAIL_SIZE_BYTES}-byte limit"
            )

    async def send(self, message: OutboundMessage) -> None:
        await self.validate(message)

        mime = MimeEmailMessage()
        mime["Subject"] = message.subject
        mime["From"] = f"{settings.FROM_NAME} <{settings.FROM_EMAIL}>" if settings.FROM_NAME else settings.FROM_EMAIL
        mime["To"] = ", ".join(message.to)
        if message.cc:
            mime["Cc"] = ", ".join(message.cc)
        reply_to = message.reply_to or settings.REPLY_TO
        if reply_to:
            mime["Reply-To"] = reply_to

        mime.set_content(message.text_body or "")
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")

        recipients = [*message.to, *message.cc, *message.bcc]

        try:
            await aiosmtplib.send(
                mime,
                recipients=recipients,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USERNAME or None,
                password=settings.SMTP_PASSWORD or None,
                use_tls=settings.SMTP_USE_SSL,
                start_tls=False if settings.SMTP_USE_SSL else settings.SMTP_TLS,
                timeout=settings.SMTP_TIMEOUT_SECONDS,
            )
        except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPConnectTimeoutError, OSError) as e:
            logger.warning("SMTP connection failed (host=%s port=%s): %s", settings.SMTP_HOST, settings.SMTP_PORT, e)
            raise SMTPConnectionException(f"Could not connect to SMTP host {settings.SMTP_HOST}:{settings.SMTP_PORT}") from e
        except aiosmtplib.SMTPException as e:
            logger.warning("SMTP send failed (host=%s): %s", settings.SMTP_HOST, e)
            raise ProviderException(f"SMTP send failed: {e}") from e

    async def health_check(self) -> bool:
        try:
            client = aiosmtplib.SMTP(
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT_SECONDS,
                use_tls=settings.SMTP_USE_SSL,
            )
            await client.connect()
            await client.quit()
            return True
        except Exception:
            return False
