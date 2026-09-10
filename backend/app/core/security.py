"""Password hashing + JWT issuing/decoding — ported near-verbatim from
PoultryOS-CBP's core/security.py (same primitives: bcrypt, python-jose)."""
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from app.core.config import settings

ACCESS_TOKEN_COOKIE_NAME = "access_token"

# Fixed bcrypt hash of an arbitrary, unused password — compared against on a
# login lookup miss so an unknown-username response takes the same time as a
# wrong-password one (bcrypt dominates latency here). See
# verify_password_constant_time.
_DUMMY_PASSWORD_HASH = "$2b$12$heNaGBBYDVFfNtMQ2JZ5cuFY/HZwCVhTooKrkiEFF/r3TbPxK6iOi"


def verify_password_constant_time(plain_password: str, hashed_password: Optional[str]) -> bool:
    return bcrypt.checkpw(plain_password.encode(), (hashed_password or _DUMMY_PASSWORD_HASH).encode())


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


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
