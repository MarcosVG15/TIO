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


#: How long a card's reason line can be before it stops being a line.
_REASON_CHARS = 150


def _match_percent(candidate, account_id) -> int:
    """Cosine similarity as the percentage the card shows.

    Deliberately the raw similarity rather than a flattering curve. Nomic
    cosines for genuinely good matches sit around 0.5-0.7, so these read lower
    than a marketing "94% match" - but they are the number the ranking actually
    used, and inventing a rescale would make the figure decorative.
    """
    score = candidate.scores.get(account_id, candidate.group_score)
    return max(0, min(100, round(score * 100)))


def _tags(candidate) -> list[str]:
    """Short labels for the card. Always a list, never None."""
    values = [candidate.category, candidate.subcategory, candidate.city]
    seen: list[str] = []
    for value in values:
        if not value:
            continue
        label = str(value).replace("_", " ").strip()
        if label and label.lower() not in {s.lower() for s in seen}:
            seen.append(label)
    return seen


def _reason(candidate) -> str:
    """One line on why this place is worth a look.

    The embedded paragraph is the most faithful description we hold - it is
    literally what the match was computed from - so a trimmed version of it is
    honest, unlike a generated sentence that could drift from the vector.
    """
    text = (candidate.blurb or "").strip()
    if not text:
        return f"Matched to your travel profile in {candidate.city or candidate.country or 'this area'}."
    if len(text) <= _REASON_CHARS:
        return text
    return text[:_REASON_CHARS].rsplit(" ", 1)[0] + "..."


@router.get("/destinations/countries")
def list_countries(account: Account = Depends(current_account)):
    """Countries that can actually be planned for, most options first.

    Sourced from the corpus, not a static list: offering a country with no
    embedded locations produces an empty plan the user cannot explain.
    """
    return {
        "countries": [
            {"country": country, "options": count}
            for country, count in recommend.list_countries()
        ]
    }


@router.get("/destinations/cities")
def list_cities(
    country: str = Query(min_length=2, max_length=120),
    account: Account = Depends(current_account),
):
    """Cities inside one country, most options first.

    This is what makes an impossible pairing unselectable rather than merely
    rejected: the picker can only ever offer cities the corpus places in the
    country that was chosen.
    """
    return {
        "country": country,
        "cities": [
            {"city": city, "options": count}
            for city, count in recommend.list_cities(country)
        ],
    }


@router.get("/destinations/recommended")
def recommended_destinations(
    country: Optional[str] = Query(
        default=None,
        description="Narrow to one country. Omitted searches the whole corpus.",
    ),
    limit: int = Query(default=6, ge=1, le=50),
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
            # Retrieve well beyond the limit so there is a spread of countries
            # to choose between. A straight top-6 off the index is whatever the
            # corpus happens to hold most of - which is how six Austrian
            # attractions end up being the entire shelf.
            target=limit,
            per_member_k=200,
            # No country named means "show me anywhere", and a shelf of six
            # places in one country is not a choice.
            one_per_country=country is None,
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

    shelf = pool.candidates[:limit]

    return {
        # Field names match what the home screen reads, not what reads best in
        # isolation. `tags` in particular must always be a list: the card calls
        # .slice(0, 3) on it unconditionally, so a missing key is a TypeError
        # that takes the whole page down rather than one empty card.
        "destinations": [
            {
                "id": str(candidate.location_id),
                "name": candidate.name,
                "country": candidate.country or candidate.city or "",
                "matchScore": _match_percent(candidate, account.account_id),
                "imageUrl": candidate.picture,
                "tags": _tags(candidate),
                "reason": _reason(candidate),
                # Kept for callers that want the underlying numbers.
                "location_id": str(candidate.location_id),
                "city": candidate.city or candidate.region,
                "category": candidate.category,
                "latitude": candidate.latitude,
                "longitude": candidate.longitude,
                "score": round(candidate.final_score, 3),
            }
            for candidate in shelf
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
