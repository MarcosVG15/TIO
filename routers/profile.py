from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import profiles
from DATABASE.ORM import Account
from deps import current_account
from schemas import ProfileOut, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileOut)
def get_profile(account: Account = Depends(current_account)) -> ProfileOut:
    """Everything the profile screen shows: identity, preferences, the
    questionnaire answers, and the derived travel summary.

    Always 200 for a signed-in user - a missing Personality is reported as
    onboarding_completed=false rather than a 404, because "hasn't onboarded"
    is a normal state, not an error.
    """
    try:
        return ProfileOut(**profiles.get_profile(account.account_id))
    except profiles.AccountMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account not found"
        ) from exc


@router.patch("", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    account: Account = Depends(current_account),
) -> ProfileOut:
    """Edit details, reset onboarding, or deactivate.

    Deactivating sets deleted_at, which immediately invalidates every existing
    session token - current_account only resolves live accounts. Signing in
    again reactivates, which is the only way back.
    """
    # exclude_unset keeps "field absent" distinct from "field set to null" -
    # the reset action depends on that difference.
    changes = payload.model_dump(exclude_unset=True)
    try:
        return ProfileOut(**profiles.update_profile(account.account_id, changes))
    except profiles.AccountMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account not found"
        ) from exc
