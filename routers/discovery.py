"""Search and recommendations.

Both depend on the Location table having rows and on the personality vector
existing, so they unblock once the embedding pipeline runs.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

import accounts
import embeddings
import recommend
from DATABASE.ORM import Account
from deps import current_account

log = logging.getLogger(__name__)

router = APIRouter(tags=["discovery"])


@router.get("/destinations/recommended")
def recommended_destinations(
    country: Optional[str] = Query(
        default=None,
        description="Narrow to one country. Omitted searches the whole corpus.",
    ),
    limit: int = Query(default=12, ge=1, le=50),
    account: Account = Depends(current_account),
):
    """Locations ranked against the caller's personality vector.

    Places, not plans - use POST /planner/suggest for a day-by-day itinerary.
    Returns 409 rather than an empty list when the caller has no vector yet, so
    the frontend can send them to finish onboarding instead of showing an empty
    shelf that looks like a bug.
    """
    try:
        pool = recommend.build_pool(
            country=country,
            account_ids=[account.account_id],
            target=limit,
        )
    except recommend.NoProfile as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except embeddings.SpaceMismatch as exc:
        log.error("embedding space mismatch: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendations are misconfigured on the server.",
        ) from exc
    except recommend.EmptyPool:
        return {"destinations": [], "cities": [], "notes": []}

    return {
        "destinations": [
            {
                "location_id": str(candidate.location_id),
                "name": candidate.name,
                "city": candidate.city or candidate.region,
                "country": candidate.country,
                "category": candidate.category,
                "latitude": candidate.latitude,
                "longitude": candidate.longitude,
                "about": candidate.blurb,
                "score": round(candidate.final_score, 3),
            }
            for candidate in pool.candidates
        ],
        "cities": [
            {"city": city, "options": count, "score": round(score, 3)}
            for city, count, score in pool.cities
        ],
        "notes": pool.notes,
    }


@router.get("/search")
def search(
    q: str = "",
    friendsOnly: bool = False,
    account: Account = Depends(current_account),
):
    """Search. People work today; locations need the Location table populated.

    Returns {"users": [...], "locations": []} - the chat screen reads
    `.users`, and the empty locations key keeps the shape stable for whoever
    wires up destination search later.
    """
    return {
        "users": accounts.search_people(
            account.account_id, q, friends_only=friendsOnly
        ),
        "locations": [],
    }
