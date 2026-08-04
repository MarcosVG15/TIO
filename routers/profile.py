from __future__ import annotations

from fastapi import APIRouter, Depends

from DATABASE.ORM import Account
from deps import current_account, not_implemented
from schemas import ProfileOut, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileOut)
def get_profile(account: Account = Depends(current_account)) -> ProfileOut:
    """The signed-in user's travel profile.

    404 if they have not completed onboarding yet.
    """
    raise not_implemented("get profile")


@router.patch("", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    account: Account = Depends(current_account),
) -> ProfileOut:
    """Edit the hard constraints - dietary, accessibility, languages.

    These are filters, not preferences, so the user edits them directly
    rather than through the conversation. Changing them does not re-run the
    extraction and does not invalidate the vector.
    """
    raise not_implemented("update profile")
