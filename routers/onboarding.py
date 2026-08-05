from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from DATABASE.ORM import Account
from deps import current_account
from pipeline import AccountMissing, Pipeline
from schemas import OnboardingOut, OnboardingRequest, OnboardingStatusOut

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

pipeline = Pipeline()


@router.post("", response_model=OnboardingOut)
def submit_onboarding(
    payload: OnboardingRequest,
    account: Account = Depends(current_account),
) -> OnboardingOut:
    """Turn the questionnaire and chat transcript into a stored profile.

    Returns as soon as the profile is committed. vector_pending is True
    because the embedding is produced afterwards by the worker - poll
    /api/onboarding/status to find out when it is ready.
    """
    try:
        result = pipeline.onboard_user(
            account_id=account.account_id,
            questionnaire=payload.questionnaire,
            conversation=payload.conversation,
        )
    except AccountMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account not found"
        ) from exc

    return OnboardingOut(
        account_id=str(result.account_id),
        personality_id=str(result.personality_id),
        vector_pending=result.vector_pending,
    )


@router.get("/status", response_model=OnboardingStatusOut)
def onboarding_status(
    account: Account = Depends(current_account),
) -> OnboardingStatusOut:
    """Whether this account has a profile, and whether its vector is ready.

    ready == False means recommendations will be weak - show a spinner or a
    generic fallback rather than an empty state.

    Not an error case: a brand new account legitimately has no profile, and
    that is exactly what routes them into the questionnaire.

    onboarding_completed tracks whether they answered, not whether the vector
    exists - the embedding lands seconds later and must not bounce them back
    through onboarding in the meantime.
    """
    return OnboardingStatusOut(**pipeline.profile_status(account.account_id))
