"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import accounts
from auth import AuthError, decode_session_token
from DATABASE.ORM import Account

bearer = HTTPBearer(auto_error=False)


def current_account(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> Account:
    """Resolve the caller from their bearer token.

    Any endpoint that declares this dependency is authenticated - the account
    comes from the signed token, never from the request body.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        account_id = decode_session_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    account = accounts.get_by_id(account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account not found"
        )
    return account


def not_implemented(what: str) -> HTTPException:
    """Uniform 501 for skeleton endpoints, so the frontend can tell the
    difference between 'not built yet' and 'broken'."""
    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f"{what} is not implemented yet"
    )
