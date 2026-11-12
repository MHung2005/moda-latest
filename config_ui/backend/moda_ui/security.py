"""Password hashing, camera URL encryption and session tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken

from .settings import get_settings


PBKDF2_ITERATIONS = 240_000


class SecretKeyError(RuntimeError):
    """MODA_CONFIG_SECRET is missing or malformed."""


def generate_secret_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _derived_fernet(purpose: bytes) -> Fernet:
    secret = get_settings().secret_key
    if not secret:
        raise SecretKeyError(
            "MODA_CONFIG_SECRET is not set. Generate one with: python -m moda_ui gen-key"
        )
    try:
        Fernet(secret.encode("ascii"))
    except (ValueError, TypeError) as error:
        raise SecretKeyError("MODA_CONFIG_SECRET must be a Fernet key") from error
    if purpose == b"camera":
        return Fernet(secret.encode("ascii"))
    digest = hashlib.sha256(secret.encode("ascii") + b"|" + purpose).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


# --- passwords -----------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations)
    )
    return hmac.compare_digest(candidate, base64.b64decode(digest))


# --- camera URLs ---------------------------------------------------------------------


def encrypt_url(url: str) -> str:
    return _derived_fernet(b"camera").encrypt(url.encode("utf-8")).decode("ascii")


def decrypt_url(token: str) -> str:
    try:
        return _derived_fernet(b"camera").decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise SecretKeyError(
            "Cannot decrypt a camera URL; MODA_CONFIG_SECRET changed?"
        ) from error


def mask_url(url: str) -> str:
    """rtsp://user:pass@host/path -> rtsp://user:***@host/path"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<URL không hợp lệ>"
    credentials, at, host = parts.netloc.rpartition("@")
    if not at or ":" not in credentials:
        return url
    user = credentials.split(":", 1)[0]
    return urlunsplit((parts.scheme, f"{user}:***@{host}", parts.path, parts.query, parts.fragment))


# --- sessions ------------------------------------------------------------------------


def create_session_token(user_id: int, role: str) -> str:
    payload = json.dumps({"uid": user_id, "role": role}).encode("utf-8")
    return _derived_fernet(b"session").encrypt(payload).decode("ascii")


def read_session_token(token: str) -> dict | None:
    ttl = get_settings().session_hours * 3600
    try:
        payload = _derived_fernet(b"session").decrypt(token.encode("ascii"), ttl=ttl)
    except (InvalidToken, ValueError):
        return None
    return json.loads(payload)
