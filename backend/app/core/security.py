"""JWT issuing/decoding for this hub's own session cookie — ported
near-verbatim from PoultryOS-CBP's core/security.py. Password hashing
(bcrypt) was removed when staff login moved to Authentik SSO (see
api/routers/auth.py, services/oidc_client.py) — this module now only
signs/verifies the session token minted after a successful SSO round-trip."""
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from app.core.config import settings

ACCESS_TOKEN_COOKIE_NAME = "access_token"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
