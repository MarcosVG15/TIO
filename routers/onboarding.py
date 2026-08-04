from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from DATABASE.ORM import Account
from deps import current_account, not_implemented
from pipeline import Pipeline
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
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
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
    """
    raise not_implemented("onboarding status")
