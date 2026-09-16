from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # No default — required means startup fails loudly instead of silently
    # running on a known value (mirrors PoultryOS-CBP's own config.py).
    DATABASE_URL: str
    DB_SSL_MODE: str = "require"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:5174"]
    REDIS_URL: str = "redis://localhost:6379"

    LOG_LEVEL: str = "INFO"
    APP_ENV: str = "development"

    # Public URL of this hub's own frontend — not currently used for anything
    # request-routing related, kept for parity with PoultryOS-CBP's Settings
    # shape and in case a future notification email needs to link back here.
    FRONTEND_URL: str = "http://localhost:5174"

    # Fernet key (generate with `python -c "from cryptography.fernet import
    # Fernet; print(Fernet.generate_key().decode())"`) — encrypts
    # Deployment.action_key_encrypted at rest. Required: unlike
    # PoultryOS-CBP's TOTP_ENCRYPTION_KEY (an optional feature), the hub
    # cannot run its core inbound-action feature at all without this.
    HUB_ENCRYPTION_KEY: str

    # ── maintenance scheduling (core/maintenance_scheduler.py) ────────────
    # How often the scheduler loop wakes to auto-transition window statuses
    # and push reminders. 5 min is plenty — windows are scheduled hours out.
    MAINTENANCE_SCHEDULER_INTERVAL_SECONDS: int = 300
    # A "reminder" push goes out this many hours before a window's start.
    MAINTENANCE_REMINDER_HOURS: int = 24
    # Heartbeat age (hours) past which a deployment shows "stale", then
    # "offline", on the deployments list. 4h = 2x the 2h heartbeat cadence.
    HEARTBEAT_STALE_HOURS: int = 4
    HEARTBEAT_OFFLINE_HOURS: int = 8

    # ── notifications (services/notifications/) ────────────────────────────
    # Celery broker/backend — a dedicated Redis DB index (1), separate from
    # nothing else in this app today (REDIS_URL/index 0 is the Casbin
    # watcher's best-effort pub/sub, which degrades gracefully without
    # Redis; Celery's broker is NOT optional — no worker can pick up a task
    # without it reachable).
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Global kill switches — env-only (no admin-UI toggle in this phase,
    # unlike PoultryPro-CBF's DB-backed AppSetting toggle). Both on by
    # default; WHATSAPP_NOTIFICATIONS_ENABLED is a narrower switch layered
    # underneath NOTIFICATIONS_ENABLED — both must be true for a WhatsApp
    # send to actually enqueue (see services/notifications/service.py).
    NOTIFICATIONS_ENABLED: bool = True
    WHATSAPP_NOTIFICATIONS_ENABLED: bool = True

    # SMTP — any standard relay (self-hosted Postal, Hostinger/Titan, Gmail,
    # ...), nothing here is provider-specific. See
    # services/notifications/README.md for setup examples.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    # true for implicit TLS from the first byte (port 465) — takes priority
    # over SMTP_TLS when set.
    SMTP_USE_SSL: bool = False
    # true to upgrade via STARTTLS after a plaintext connect (port 587/25).
    # Ignored when SMTP_USE_SSL is true.
    SMTP_TLS: bool = True
    SMTP_TIMEOUT_SECONDS: int = 15
    FROM_EMAIL: str = "alerts@bilwacorp.example"
    FROM_NAME: str = "BilwaCorp Fleet Hub"
    REPLY_TO: str = ""

    # WhatsApp — a generic HTTP gateway adapter (Evolution API, Meta Cloud
    # API, a Wasapi/Wassenger-style sender, ...). WHATSAPP_PAYLOAD_TEMPLATE
    # is the piece that actually adapts to your vendor's request shape — see
    # services/notifications/README.md for per-vendor examples. Empty
    # WHATSAPP_API_URL (default) leaves the channel unconfigured: sends fail
    # fast with a clear error instead of silently no-opping.
    WHATSAPP_API_URL: str = ""
    WHATSAPP_API_METHOD: str = "POST"
    WHATSAPP_API_KEY: str = ""
    WHATSAPP_AUTH_HEADER: str = "Authorization"
    WHATSAPP_AUTH_SCHEME: str = "Bearer"
    WHATSAPP_EXTRA_HEADERS: str = ""
    WHATSAPP_PAYLOAD_TEMPLATE: str = '{"to": "{{ to }}", "message": {{ message | tojson }}}'
    WHATSAPP_TIMEOUT_SECONDS: int = 15

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
