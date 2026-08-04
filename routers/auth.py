from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import accounts
from auth import AuthError, create_session_token, verify_google_token
from DATABASE.ORM import Account
from deps import current_account
from schemas import (
    AccountOut,
    GoogleSignInRequest,
    SessionOut,
    SignInRequest,
    SignUpRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def to_account_out(account: Account) -> AccountOut:
    return AccountOut(
        account_id=str(account.account_id),
        email=account.email,
        name=account.name,
        surname=account.surname,
        auth_provider=account.auth_provider.value,
    )


@router.post("/google", response_model=SessionOut)
def google_sign_in(payload: GoogleSignInRequest) -> SessionOut:
    """Exchange a Google ID token for a TIO session token.

    Send the raw signed token from the Google SDK. Creates the account on
    first sign-in, returns the existing one afterwards.
    """
    try:
        claims = verify_google_token(payload.id_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    try:
        account = accounts.get_or_create_from_google(claims)
    except accounts.AccountConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    token, expires_in = create_session_token(account.account_id)
    return SessionOut(
        access_token=token, expires_in=expires_in, account=to_account_out(account)
    )


@router.post("/sign-up", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def sign_up(payload: SignUpRequest) -> SessionOut:
    """Register with an email address and password.

    Returns a session token immediately - the address is recorded as
    unverified rather than blocking sign-in.
    """
    try:
        account = accounts.create_with_password(
            email=payload.email,
            password=payload.password,
            name=payload.name,
            surname=payload.surname,
        )
    except accounts.AccountConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    token, expires_in = create_session_token(account.account_id)
    return SessionOut(
        access_token=token, expires_in=expires_in, account=to_account_out(account)
    )


@router.post("/sign-in", response_model=SessionOut)
def sign_in(payload: SignInRequest) -> SessionOut:
    """Sign in with an email address and password."""
    try:
        account = accounts.authenticate(payload.email, payload.password)
    except accounts.InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    token, expires_in = create_session_token(account.account_id)
    return SessionOut(
        access_token=token, expires_in=expires_in, account=to_account_out(account)
    )


@router.get("/me", response_model=AccountOut)
def me(account: Account = Depends(current_account)) -> AccountOut:
    """The signed-in account. Use this to validate a stored token on app boot."""
    return to_account_out(account)
