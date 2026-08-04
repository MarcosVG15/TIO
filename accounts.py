"""The only place Account rows are read or written."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select

from auth import GoogleClaims
from DATABASE.ORM import Account, AuthProvider, session_scope


class AccountConflict(Exception):
    """Raised when an email already belongs to a different identity."""


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
            auth_provider=AuthProvider.GOOGLE,
            account_metadata={"picture": claims.picture} if claims.picture else {},
        )
        session.add(account)
        session.flush()
        return account


def get_by_id(account_id: UUID) -> Optional[Account]:
    with session_scope() as session:
        return session.scalar(
            select(Account).where(
                Account.account_id == account_id,
                Account.deleted_at.is_(None),
            )
        )
