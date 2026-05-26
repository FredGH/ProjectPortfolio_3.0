"""JWT RS256 handler — issue / verify tokens, auto-generate key pair on startup."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_HOURS", "8"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))

_private_key_pem: bytes | None = None
_public_key_pem: bytes | None = None


def _generate_key_pair() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


_FALLBACK_KEY_DIR = Path("/tmp/tca_jwt_keys")
_FALLBACK_PRIV = _FALLBACK_KEY_DIR / "private.pem"
_FALLBACK_PUB = _FALLBACK_KEY_DIR / "public.pem"


def load_keys() -> None:
    """Load or generate RS256 key pair. Called at application startup.

    Priority:
    1. Explicit env-var paths (JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH)
    2. Stable fallback path inside the container (/tmp/tca_jwt_keys/)
       — persists across uvicorn hot-reloads so tokens stay valid between reloads
    3. Generate fresh pair (last resort, invalidates existing tokens)
    """
    global _private_key_pem, _public_key_pem

    priv_path = os.environ.get("JWT_PRIVATE_KEY_PATH")
    pub_path = os.environ.get("JWT_PUBLIC_KEY_PATH")

    # Priority 1: explicit env-var paths
    if priv_path and pub_path and Path(priv_path).exists() and Path(pub_path).exists():
        _private_key_pem = Path(priv_path).read_bytes()
        _public_key_pem = Path(pub_path).read_bytes()
        logger.info("Loaded JWT RS256 key pair from %s / %s", priv_path, pub_path)
        return

    # Priority 2: stable fallback path — survives hot-reloads
    if _FALLBACK_PRIV.exists() and _FALLBACK_PUB.exists():
        _private_key_pem = _FALLBACK_PRIV.read_bytes()
        _public_key_pem = _FALLBACK_PUB.read_bytes()
        logger.info("Loaded JWT RS256 key pair from fallback path %s", _FALLBACK_KEY_DIR)
        return

    # Priority 3: generate and persist
    logger.info("Generating new JWT RS256 key pair …")
    _private_key_pem, _public_key_pem = _generate_key_pair()

    # Try to persist to explicit paths
    if priv_path and pub_path:
        Path(priv_path).parent.mkdir(parents=True, exist_ok=True)
        Path(priv_path).write_bytes(_private_key_pem)
        Path(pub_path).write_bytes(_public_key_pem)
        logger.info("Saved key pair to %s / %s", priv_path, pub_path)
        return

    # Persist to fallback path so next reload re-uses the same keys
    _FALLBACK_KEY_DIR.mkdir(parents=True, exist_ok=True)
    _FALLBACK_PRIV.write_bytes(_private_key_pem)
    _FALLBACK_PUB.write_bytes(_public_key_pem)
    logger.info("Saved JWT RS256 key pair to fallback path %s", _FALLBACK_KEY_DIR)


def _priv() -> bytes:
    if _private_key_pem is None:
        load_keys()
    return _private_key_pem  # type: ignore[return-value]


def _pub() -> bytes:
    if _public_key_pem is None:
        load_keys()
    return _public_key_pem  # type: ignore[return-value]


def issue_access_token(
    client_id: str,
    role: str,
    counterparty_id: str | None,
    legal_entity: str | None = None,
) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": client_id,
        "role": role,
        "counterparty_id": counterparty_id,
        "legal_entity": legal_entity,
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
        "token_type": "access",
    }
    return jwt.encode(payload, _priv(), algorithm=ALGORITHM)


def issue_refresh_token(client_id: str) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": client_id,
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "token_type": "refresh",
    }
    return jwt.encode(payload, _priv(), algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    """Verify JWT signature and expiry. Raises JWTError on failure."""
    return jwt.decode(token, _pub(), algorithms=[ALGORITHM])
