from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError

import avatars
import bio
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


@router.post("/bio/generate")
def generate_bio(account: Account = Depends(current_account)) -> dict[str, str]:
    """Suggest a bio from the person's profile.

    Returns it without saving. The user reviews or edits it, then PATCHes
    /api/profile with {"bio": "..."} - a bio they never agreed to should not
    end up on their card.
    """
    try:
        return {"bio": bio.generate(account.account_id)}
    except bio.NoProfile as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete onboarding first - there is nothing to write from yet.",
        ) from exc
    except Exception as exc:  # OpenAI down, rate limited, bad key
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not generate a bio right now. Try again, or write your own.",
        ) from exc


@router.post("/avatar")
def upload_avatar(
    file: UploadFile = File(...),
    account: Account = Depends(current_account),
) -> dict[str, str]:
    """Store an uploaded profile picture and return its URL.

    Returns it without saving, the same way /profile/bio/generate does: the
    user sees the picture in the form and PATCHes /api/profile with
    {"avatarUrl": "..."} when they press Save. A stored row is not consent to
    put it on their profile.
    """
    # Read one byte past the limit rather than the whole stream - that is
    # enough to know it is too big, without holding an arbitrarily large
    # upload in memory to find out.
    data = file.file.read(avatars.MAX_BYTES + 1)

    try:
        url = avatars.store(account.account_id, data)
    except avatars.RejectedImage as exc:
        # 422, not 400: the request was well-formed, the content was not.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not save that picture right now. Try again.",
        ) from exc

    return {"avatar_url": url}


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
