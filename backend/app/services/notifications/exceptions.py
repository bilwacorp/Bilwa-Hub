class NotificationException(Exception):
    """Base class for all notification subsystem errors."""


class ProviderException(NotificationException):
    """A provider (SMTP, WhatsApp) failed to send. Wraps the underlying
    transport error so callers never need to catch aiosmtplib/httpx
    exceptions directly."""


class SMTPConnectionException(ProviderException):
    """Could not connect to / authenticate with the SMTP server. Distinguished
    from ProviderException so Celery's autoretry_for can retry connection
    failures but not e.g. template errors."""


class TemplateNotFoundException(NotificationException):
    def __init__(self, template_name: str):
        super().__init__(f"Notification template not found: {template_name!r}")
        self.template_name = template_name


class EmailValidationException(NotificationException):
    def __init__(self, address: str):
        super().__init__(f"Invalid email address: {address!r}")
        self.address = address


class WhatsAppConfigurationException(NotificationException):
    """The WhatsApp channel was used before WHATSAPP_API_URL (and friends)
    were configured in Settings — distinct from a runtime send failure."""


class WhatsAppConnectionException(ProviderException):
    """Could not reach the configured WhatsApp gateway. Distinguished from
    ProviderException so Celery's autoretry_for can retry connection/timeout
    failures but not e.g. a malformed payload template."""


class WhatsAppValidationException(NotificationException):
    def __init__(self, recipient: str):
        super().__init__(f"Invalid WhatsApp recipient number: {recipient!r}")
        self.recipient = recipient
