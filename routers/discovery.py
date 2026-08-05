"""Search and recommendations.

Both depend on the Location table having rows and on the personality vector
existing, so they unblock once the embedding pipeline runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

import accounts
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
    """Search. People work today; locations need the Location table populated.

    Returns {"users": [...], "locations": []} - the chat screen reads
    `.users`, and the empty locations key keeps the shape stable for whoever
    wires up destination search later.
    """
    return {
        "users": accounts.search_people(account.account_id, q),
        "locations": [],
    }
