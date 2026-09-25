from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings

BCRYPT_ROUNDS = 12
STUDENT_MAX_AGE = timedelta(days=30)
ADMIN_MAX_AGE = timedelta(hours=8)
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def encode_token(payload: dict, secret: str, max_age: timedelta) -> str:
    now = datetime.now(UTC)
    body = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + max_age).timestamp()),
    }
    return jwt.encode(body, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def student_token(claims: dict) -> str:
    return encode_token(claims, settings.auth_secret, STUDENT_MAX_AGE)


def admin_token(admin_id: str) -> str:
    return encode_token({"sub": admin_id}, settings.admin_auth_secret, ADMIN_MAX_AGE)


def read_bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()
