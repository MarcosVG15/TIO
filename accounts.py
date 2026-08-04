"""The only place Account rows are read or written."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select

import passwords
from auth import GoogleClaims
from DATABASE.ORM import Account, AuthProvider, session_scope


class AccountConflict(Exception):
    """Raised when an email already belongs to a different identity."""


class InvalidCredentials(Exception):
    """Wrong password, unknown email, or an account with no password set."""


def _display_name(claims: GoogleClaims) -> str:
    return claims.given_name or claims.name or claims.email.split("@")[0]


def get_or_create_from_google(claims: GoogleClaims) -> Account:
    """Match on the Google subject id, never on email.

    `sub` is permanent; an email address can be renamed, or reassigned to a
    different person entirely in Google Workspace.
    """
    with session_scope() as session:
        account = session.scalar(
            select(Account).where(Account.user_id == claims.sub)
        )

        if account is not None:
            account.email = claims.email
            account.name = _display_name(claims)
            account.surname = claims.family_name
            account.email_verified = claims.email_verified
            # A returning user whose account was soft-deleted is revived.
            account.deleted_at = None
            return account

        collision = session.scalar(
            select(Account).where(Account.email == claims.email)
        )
        if collision is not None:
            raise AccountConflict(
                f"{claims.email} is already registered via "
                f"{collision.auth_provider.value}"
            )

        account = Account(
            user_id=claims.sub,
            email=claims.email,
            name=_display_name(claims),
            surname=claims.family_name,
            email_verified=claims.email_verified,
            auth_provider=AuthProvider.GOOGLE,
            account_metadata={"picture": claims.picture} if claims.picture else {},
        )
        session.add(account)
        session.flush()
        return account


def create_with_password(
    email: str,
    password: str,
    name: str,
    surname: Optional[str] = None,
) -> Account:
    """Register a local account. email_verified stays False - nothing has
    proven the address belongs to whoever typed it.
    """
    email = email.strip().lower()

    with session_scope() as session:
        existing = session.scalar(select(Account).where(Account.email == email))
        if existing is not None:
            raise AccountConflict(
                f"{email} is already registered via "
                f"{existing.auth_provider.value}"
            )

        account = Account(
            user_id=None,
            password_hash=passwords.hash_password(password),
            email=email,
            name=name.strip(),
            surname=surname.strip() if surname else None,
            email_verified=False,
            auth_provider=AuthProvider.EMAIL,
        )
        session.add(account)
        session.flush()
        return account


def authenticate(email: str, password: str) -> Account:
    """Check an email/password pair. Raises InvalidCredentials on any failure."""
    email = email.strip().lower()

    with session_scope() as session:
        account = session.scalar(select(Account).where(Account.email == email))

        if account is None:
            passwords.burn_time()
            raise InvalidCredentials("invalid email or password")

        if account.password_hash is None:
            # A provider account - the credential lives with Google, not here.
            passwords.burn_time()
            raise InvalidCredentials(
                f"this account signs in with {account.auth_provider.value}"
            )

        if not passwords.verify_password(account.password_hash, password):
            raise InvalidCredentials("invalid email or password")

        # Argon2 parameters get stronger over time; upgrade opportunistically
        # while we have the plaintext in hand.
        if passwords.needs_rehash(account.password_hash):
            account.password_hash = passwords.hash_password(password)

        # Same policy as Google sign-in: signing back in revives the account.
        account.deleted_at = None
        return account


def get_by_id(account_id: UUID) -> Optional[Account]:
    with session_scope() as session:
        return session.scalar(
            select(Account).where(
                Account.account_id == account_id,
                Account.deleted_at.is_(None),
            )
        )
