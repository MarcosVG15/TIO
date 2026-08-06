"""Search and recommendations.

Both depend on the Location table having rows and on the personality vector
existing, so they unblock once the embedding pipeline runs.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

import accounts
import embeddings
import recommend
import rerank
from DATABASE.ORM import Account
from deps import current_account

log = logging.getLogger(__name__)

#: The judged shelf, per traveller. The judgement is a paid call of a few
#: seconds and the answer does not change between page loads, so serving it
#: from memory is what keeps the home page inside the request budget.
_SHELF_CACHE: dict[tuple, tuple[float, list]] = {}
SHELF_CACHE_TTL = 300.0

#: One rebuild at a time, and never inside a request. Without the guard a slow
#: aggregate under load would have every visitor start their own copy of it,
#: which is how a busy database becomes an unreachable one.
_REFRESHING = threading.Lock()


def _rebuild(country: Optional[str], days: int, limit: int) -> None:
    """Recompute the shelf off the request path, best effort."""
    if not _REFRESHING.acquire(blocking=False):
        return
    try:
        recommend.top_destinations(country=country, days=days, limit=25)
    except Exception as exc:  # noqa: BLE001 - a background retry may simply fail
        log.info("background destination refresh failed: %s", exc)
    finally:
        _REFRESHING.release()


def _schedule_refresh(country: Optional[str], days: int, limit: int) -> None:
    threading.Thread(
        target=_rebuild, args=(country, days, limit), daemon=True
    ).start()

router = APIRouter(tags=["discovery"])


#: How long a card's reason line can be before it stops being a line.
_REASON_CHARS = 150


def _match_percent(candidate, account_id) -> int:
    """The percentage the card shows.

    Prefers the model's compatibility judgement, because that is what the
    ordering used and a number that disagrees with the order is worse than no
    number. Falls back to the blended cheap score when the judge did not run.

    Not the raw cosine: with templated embedding_text those cluster in a narrow
    band and every card would read 43-45%, which tells the user nothing.
    """
    if candidate.llm_score is not None:
        return max(0, min(100, int(candidate.llm_score)))
    return max(0, min(100, round(candidate.final_score * 100)))


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
    """One line on why this place suits this traveller.

    The judge's sentence when there is one - it is about the person, which is
    what the card is for. The stored description is the fallback, and in this
    corpus it is templated ("X is a attraction in Y"), so it reads poorly and
    says nothing; that is a reason to fix embedding_text, not to hide it.
    """
    if candidate.llm_reason:
        return candidate.llm_reason
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
    days: int = Query(
        default=4, ge=1, le=21,
        description="Typical trip length. A destination must sustain it.",
    ),
    account: Account = Depends(current_account),
):
    """Destinations ranked against the caller's profile.

    Cities, not buildings. A card offering one museum is not a holiday and
    cannot be compared against another city on anything a traveller cares
    about, so individual places become the *evidence* for a destination and the
    destination is what is offered. `places` on each card is the raw material
    for a route once one is chosen.

    Returns 409 rather than an empty list when the caller has no vector yet, so
    the frontend can send them to finish onboarding instead of showing an empty
    shelf that looks like a bug.
    """
    # Who we are planning for. Loaded first so a missing vector is a clean 409
    # rather than a wasted aggregate over the whole corpus.
    try:
        travellers = recommend.load_travellers([account.account_id])
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

    cache_key = (account.account_id, (country or "").lower(), days, limit)
    cached = _SHELF_CACHE.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < SHELF_CACHE_TTL:
        return cached[1]

    # Candidates come from counting, not from nearest-neighbour: see
    # recommend.top_destinations for why. Personalisation is the next stage.
    stale = False
    try:
        grouped = recommend.top_destinations(country=country, days=days, limit=25)
    except SQLAlchemyError as exc:
        # The database is shared with a corpus enrichment job that reads
        # terabytes, so losing the race to a statement timeout is a normal
        # event, not an exceptional one. Falling back to the last shelf that
        # worked is almost always right: which cities are worth visiting does
        # not change minute to minute, and an out-of-date answer beats an
        # apology by a wide margin.
        log.warning("destination lookup failed, trying last good: %s", exc)
        grouped = recommend.last_good_destinations(country=country, days=days, limit=25)
        stale = True
        if not grouped:
            # Nothing has ever succeeded on this server, so there is genuinely
            # nothing to show. Only now is an error the honest answer.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Destinations are still being prepared. This usually takes "
                    "a few minutes on a new server."
                ),
            ) from exc

    if stale:
        # Serve it, then rebuild out of band so the next visitor gets fresh
        # data without anybody having waited for it.
        _schedule_refresh(country, days, limit)
    if not grouped:
        return {
            "destinations": [],
            "cities": [],
            "notes": [
                f"No destination in the corpus has enough to fill {days} days yet."
            ],
        }

    # Cheap per-place signals, so the evidence inside each destination is
    # ordered by fit rather than by raw fame alone.
    for destination in grouped:
        recommend.score_candidates(destination.places, travellers)
        destination.places.sort(key=recommend.effective_score, reverse=True)
        best = destination.places[: max(1, days * 2)]
        destination.score = (
            sum(recommend.effective_score(p) for p in best) / len(best) * 0.9
            + min(len(destination.categories), 4) / 4.0 * 0.1
        )
    grouped.sort(key=lambda d: d.score, reverse=True)

    judged = rerank.score_destinations(grouped, travellers)
    shelf = _one_per_country(judged, limit) if country is None else judged[:limit]

    payload = {
        "destinations": [
            {
                "id": f"{d.city}|{d.country}",
                "name": d.city,
                "country": d.country,
                "matchScore": max(0, min(100, int(
                    d.llm_score if d.llm_score is not None else round(d.score * 100)
                ))),
                "imageUrl": d.picture,
                # Always a list: the card calls .slice(0, 3) unconditionally.
                "tags": [c.replace("_", " ") for c in d.categories[:4]],
                "reason": d.llm_reason or (
                    f"{len(d.places)} places worth your time, enough for "
                    f"{days} days."
                ),
                # The raw material for a route through this destination.
                "places": [
                    {
                        "location_id": str(p.location_id),
                        "name": p.name,
                        "category": p.category,
                        "latitude": p.latitude,
                        "longitude": p.longitude,
                    }
                    for p in d.places[: max(6, days * 3)]
                ],
                "placeCount": len(d.places),
                "city": d.city,
                "score": round(d.score, 3),
            }
            for d in shelf
        ],
        "cities": [
            {"city": d.city, "options": len(d.places), "score": round(d.score, 3)}
            for d in judged[:20]
        ],
        "notes": [],
    }
    _SHELF_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def _one_per_country(destinations, limit: int):
    """At most one destination per country, best first.

    Six cities in one country is not a choice of where to go.
    """
    chosen, seen = [], set()
    for destination in destinations:
        key = (destination.country or "?").lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(destination)
        if len(chosen) >= limit:
            break
    if len(chosen) < limit:
        for destination in destinations:
            if destination not in chosen:
                chosen.append(destination)
                if len(chosen) >= limit:
                    break
    return chosen


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
