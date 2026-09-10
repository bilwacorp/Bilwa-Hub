"""Fernet encryption for Deployment.action_key_encrypted — mirrors
PoultryOS-CBP's services/totp.py pattern for User.totp_secret_encrypted.
Nothing here ever persists a plaintext action_key; the encrypted column is
the only place it's stored on the hub."""
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.HUB_ENCRYPTION_KEY.encode())


def encrypt(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as e:
        raise ValueError("action_key could not be decrypted — wrong HUB_ENCRYPTION_KEY?") from e
