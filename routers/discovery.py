"""Search and recommendations.

Both depend on the Location table having rows and on the personality vector
existing, so they unblock once the embedding pipeline runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from DATABASE.ORM import Account
from deps import current_account, not_implemented

router = APIRouter(tags=["discovery"])


@router.get("/destinations/recommended")
def recommended_destinations(account: Account = Depends(current_account)):
    """Locations ranked against the caller's personality vector.

    Needs a profile with a non-null personality_vector, so this stays
    unavailable until onboarding and the embedding worker are implemented.
    """
    raise not_implemented("recommended destinations")


@router.get("/search")
def search(q: str = "", account: Account = Depends(current_account)):
    """Free-text search over locations."""
    raise not_implemented("search")
