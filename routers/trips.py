from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from DATABASE.ORM import Account
from deps import current_account, not_implemented
from schemas import (
    ItineraryItemCreate,
    ItineraryItemOut,
    ItineraryItemUpdate,
    TripCreate,
    TripOut,
    TripUpdate,
)

router = APIRouter(tags=["trips"])


@router.get("/trips", response_model=list[TripOut])
def list_trips(
    status: Optional[str] = None,
    account: Account = Depends(current_account),
) -> list[TripOut]:
    """Every trip the caller can see - their own, plus any belonging to a
    group they are a member of. Soft-deleted trips are excluded.

    Returns an empty list when there are none; an account with no trips is a
    normal state, not an error.

    `status` is accepted but not yet applied - it is typed as a free string
    rather than the TripStatus enum so that a filter like "current", which is
    a date question rather than a status value, does not 422.
    """
    # No trip can exist yet - create_trip is still unimplemented - so an empty
    # list is the truthful answer rather than a placeholder.
    return []


@router.post("/trips", response_model=TripOut, status_code=status.HTTP_201_CREATED)
def create_trip(
    payload: TripCreate,
    account: Account = Depends(current_account),
) -> TripOut:
    """Create a trip. Pass is_group=true with a group_id for a shared trip,
    or leave both out for a solo one.
    """
    raise not_implemented("create trip")


@router.get("/trips/{trip_id}", response_model=TripOut)
def get_trip(
    trip_id: str,
    account: Account = Depends(current_account),
) -> TripOut:
    """One trip. 404 if it does not exist or the caller is not a member.

    Deliberately the same 404 either way - distinguishing them would let
    someone probe for which trip ids exist.
    """
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="trip not found"
    )


@router.patch("/trips/{trip_id}", response_model=TripOut)
def update_trip(
    trip_id: str,
    payload: TripUpdate,
    account: Account = Depends(current_account),
) -> TripOut:
    """Rename, reschedule, rebudget, or change status."""
    raise not_implemented("update trip")


@router.delete("/trips/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trip(
    trip_id: str,
    account: Account = Depends(current_account),
) -> None:
    """Soft delete - stamps deleted_at. The row and its vectors survive so
    the taste signal in History stays intact.
    """
    raise not_implemented("delete trip")


@router.get("/trips/{trip_id}/itinerary", response_model=list[ItineraryItemOut])
def list_itinerary(
    trip_id: str,
    account: Account = Depends(current_account),
) -> list[ItineraryItemOut]:
    """The trip's activities, ordered by date then time.

    Empty list when the trip has nothing scheduled.
    """
    return []


@router.post(
    "/trips/{trip_id}/itinerary",
    response_model=ItineraryItemOut,
    status_code=status.HTTP_201_CREATED,
)
def add_itinerary_item(
    trip_id: str,
    payload: ItineraryItemCreate,
    account: Account = Depends(current_account),
) -> ItineraryItemOut:
    """Add an activity to the trip."""
    raise not_implemented("add itinerary item")


@router.patch("/itinerary/{itinerary_id}", response_model=ItineraryItemOut)
def update_itinerary_item(
    itinerary_id: str,
    payload: ItineraryItemUpdate,
    account: Account = Depends(current_account),
) -> ItineraryItemOut:
    """Move, rebook, or re-cost a single activity."""
    raise not_implemented("update itinerary item")


@router.delete(
    "/itinerary/{itinerary_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_itinerary_item(
    itinerary_id: str,
    account: Account = Depends(current_account),
) -> None:
    """Hard delete - itinerary items carry no history worth preserving."""
    raise not_implemented("delete itinerary item")
