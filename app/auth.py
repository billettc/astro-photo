import secrets
from typing import Optional

from fastapi import Cookie, Request
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext

from app.config import ADMIN_PASSWORD, SECRET_KEY

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeSerializer(SECRET_KEY, salt="astro-photo-session")

SESSION_COOKIE = "astro_session"


class NotAuthenticated(Exception):
    """Raised when admin auth is required but missing."""


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    return pwd_context.verify(password, password_hash)


def create_admin_token() -> str:
    return serializer.dumps({"role": "admin", "nonce": secrets.token_hex(8)})


def is_admin_token(token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        data = serializer.loads(token)
        return data.get("role") == "admin"
    except BadSignature:
        return False


def create_album_unlock_token(slug: str) -> str:
    return serializer.dumps({"album": slug, "unlocked": True})


def is_album_unlocked(token: Optional[str], slug: str) -> bool:
    if not token:
        return False
    try:
        data = serializer.loads(token)
        return data.get("album") == slug and data.get("unlocked") is True
    except BadSignature:
        return False


def check_admin_password(password: str) -> bool:
    if not ADMIN_PASSWORD or not password:
        return False
    return secrets.compare_digest(password, ADMIN_PASSWORD)


def require_admin(astro_session: Optional[str] = Cookie(default=None)) -> None:
    if not is_admin_token(astro_session):
        raise NotAuthenticated()


def admin_from_request(request: Request) -> bool:
    return is_admin_token(request.cookies.get(SESSION_COOKIE))
