"""Password hashing.

Argon2id via argon2-cffi, which is the current OWASP recommendation. The
library's defaults are sensible; do not lower them for speed.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Verifying against this when no account exists keeps the failure path as slow
# as the success path, so response time cannot be used to discover which
# addresses are registered.
_DUMMY_HASH = _hasher.hash("not-a-real-password")

MIN_LENGTH = 8
# Argon2 hashes whatever you give it. Without a ceiling, a multi-megabyte
# password is a cheap way to burn server CPU.
MAX_LENGTH = 128


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def burn_time() -> None:
    """Spend the same CPU as a real verification, for unknown accounts."""
    verify_password(_DUMMY_HASH, "not-a-real-password-either")
