"""Google ID token verification and TIO session tokens.

Knows nothing about the database - it turns a Google token into verified
claims, and an account id into a session token.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

import jwt
from dotenv import load_dotenv
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent / ".env")

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
SESSION_ALGORITHM = "HS256"
SESSION_TTL = timedelta(days=30)
CLOCK_SKEW_SECONDS = 10
MIN_SECRET_LENGTH = 32


class AuthError(Exception):
    """Raised whenever a token cannot be trusted."""


class GoogleClaims(BaseModel):
    sub: str
    email: str
    email_verified: bool = False
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None


def allowed_audiences() -> set[str]:
    raw = os.getenv("CLIENT_ID", "")
    audiences = {part.strip() for part in raw.split(",") if part.strip()}
    if not audiences:
        raise AuthError("CLIENT_ID is not set in .env")
    return audiences


def _session_secret() -> str:
    secret = (os.getenv("SESSION_SECRET") or "").strip()
    if len(secret) < MIN_SECRET_LENGTH:
        raise AuthError(
            f"SESSION_SECRET must be at least {MIN_SECRET_LENGTH} characters "
            f'(got {len(secret)}). Generate one with: python -c "import '
            'secrets; print(secrets.token_urlsafe(32))"'
        )
    return secret


def validate_config() -> None:
    """Fail at startup rather than mid-request.

    Without this a blank SESSION_SECRET only surfaces when someone signs up -
    after their account has already been committed, leaving them unable to
    retry because the email is now taken.
    """
    _session_secret()
    allowed_audiences()


def verify_google_token(raw_token: str) -> GoogleClaims:
    """Cryptographically verify a Google ID token and return its claims.

    verify_oauth2_token checks the signature against Google's public keys and
    the expiry. Audience and issuer are checked here so that several client
    IDs (web / Android / iOS) can be accepted.
    """
    if not raw_token:
        raise AuthError("missing id_token")

    try:
        payload = google_id_token.verify_oauth2_token(
            raw_token,
            google_requests.Request(),
            clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
        )
    except (ValueError, google_exceptions.GoogleAuthError) as exc:
        raise AuthError(f"invalid Google token: {exc}") from exc

    if payload.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError(f"unexpected issuer: {payload.get('iss')}")

    audience = payload.get("aud")
    if audience not in allowed_audiences():
        raise AuthError("token was not issued for this application")

    claims = GoogleClaims(**payload)

    if not claims.email_verified:
        raise AuthError("Google account has an unverified email address")

    return claims


def create_session_token(account_id: UUID) -> tuple[str, int]:
    """Mint a TIO session token. Returns (token, seconds_until_expiry).

    Google's own token is not reused past sign-in - it expires in an hour and
    revalidating it would mean a network hop to Google on every request.
    """
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + SESSION_TTL
    token = jwt.encode(
        {"sub": str(account_id), "iat": issued_at, "exp": expires_at},
        _session_secret(),
        algorithm=SESSION_ALGORITHM,
    )
    return token, int(SESSION_TTL.total_seconds())


def decode_session_token(token: str) -> UUID:
    try:
        payload = jwt.decode(
            token, _session_secret(), algorithms=[SESSION_ALGORITHM]
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid session token: {exc}") from exc

    try:
        return UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthError("session token has no valid subject") from exc
