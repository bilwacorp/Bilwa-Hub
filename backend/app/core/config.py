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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
