from __future__ import annotations

from datetime import date as date_type, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select

from DATABASE.ORM import (
    Account,
    GroupMember,
    ItineraryItem,
    Location,
    Trip,
    TripStatus,
    session_scope,
)
from deps import current_account, not_implemented
from schemas import (
    ItineraryItemCreate,
    ItineraryItemOut,
    ItineraryItemUpdate,
    LocationOut,
    TripCreate,
    TripOut,
    TripUpdate,
)

router = APIRouter(tags=["trips"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _visible_trips(account: Account):
    """Trips the caller may see: their own, plus any their groups own.

    One condition rather than two queries, so ordering stays correct across
    both sources.
    """
    member_groups = (
        select(GroupMember.group_id)
        .where(GroupMember.account_id == account.account_id)
        .scalar_subquery()
    )
    return or_(
        Trip.owner_account_id == account.account_id,
        Trip.group_id.in_(member_groups),
    )


def _load_trip(session, trip_id: str, account: Account) -> Trip:
    """One trip the caller may see, or 404.

    The same 404 whether the trip is missing or simply not theirs -
    distinguishing them would let someone probe which trip ids exist.
    """
    try:
        trip_uuid = UUID(trip_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="trip not found"
        )

    trip = session.scalar(
        select(Trip).where(
            Trip.trip_id == trip_uuid,
            Trip.deleted_at.is_(None),
            _visible_trips(account),
        )
    )
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="trip not found"
        )
    return trip


def _location_out(location: Optional[Location]) -> Optional[LocationOut]:
    if location is None:
        return None
    return LocationOut(
        location_id=str(location.location_id),
        name=location.name,
        country=location.country,
        city=location.city,
        latitude=location.latitude,
        longitude=location.longitude,
        picture=getattr(location, "picture_url", None),
    )


def _trip_out(trip: Trip) -> TripOut:
    return TripOut(
        trip_id=str(trip.trip_id),
        name=trip.name,
        status=trip.status,
        is_group=trip.is_group,
        start_date=trip.start_date,
        end_date=trip.end_date,
        budget_limit=trip.budget_limit,
        origin_location=_location_out(trip.origin_location),
        group_id=str(trip.group_id) if trip.group_id else None,
    )


def _item_out(item: ItineraryItem) -> ItineraryItemOut:
    minutes = (
        int(item.duration.total_seconds() // 60) if item.duration is not None else None
    )
    return ItineraryItemOut(
        itinerary_id=str(item.itinerary_id),
        trip_id=str(item.trip_id),
        location=_location_out(item.location),
        date=item.date,
        time=item.time,
        duration_minutes=minutes,
        activity_type=item.activity_type,
        weather_dependent=item.weather_dependent,
        booking_status=item.booking_status,
        booking_ref=item.booking_ref,
        booking_url=item.booking_url,
        cost=item.cost,
        description=item.description,
        details=dict(item.details or {}),
    )


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------


@router.get("/trips", response_model=list[TripOut])
def list_trips(
    status: Optional[str] = None,
    account: Account = Depends(current_account),
) -> list[TripOut]:
    """Every trip the caller can see - their own, plus any belonging to a
    group they are a member of. Soft-deleted trips are excluded.

    Returns an empty list when there are none; an account with no trips is a
    normal state, not an error.

    `status` is a date question rather than a TripStatus value, which is why it
    is typed as a free string:

      "past"     finished: end_date is before today, or it was completed
      "current"  everything unfinished, soonest first - live, upcoming, drafts

    An unrecognised value is ignored rather than 422'd, so a frontend passing
    something else still gets a usable list.
    """
    today = date_type.today()

    with session_scope() as session:
        query = select(Trip).where(Trip.deleted_at.is_(None), _visible_trips(account))

        wanted = (status or "").strip().lower()
        if wanted == "past":
            query = query.where(
                or_(
                    Trip.end_date < today,
                    Trip.status.in_([TripStatus.COMPLETED, TripStatus.CANCELLED]),
                )
            ).order_by(Trip.start_date.desc().nullslast())
        elif wanted == "current":
            query = query.where(
                or_(Trip.end_date.is_(None), Trip.end_date >= today),
                Trip.status.not_in([TripStatus.COMPLETED, TripStatus.CANCELLED]),
            ).order_by(Trip.start_date.asc().nullslast())
        else:
            query = query.order_by(Trip.start_date.desc().nullslast())

        return [_trip_out(trip) for trip in session.scalars(query).all()]


@router.post("/trips", response_model=TripOut, status_code=status.HTTP_201_CREATED)
def create_trip(
    payload: TripCreate,
    account: Account = Depends(current_account),
) -> TripOut:
    """Create a trip. Pass is_group=true with a group_id for a shared trip,
    or leave both out for a solo one.
    """
    if payload.is_group and not payload.group_id:
        # Mirrors the group_trip_needs_group CHECK constraint so the caller gets
        # a readable message rather than an IntegrityError.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a group trip needs a group_id",
        )
    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date is before start_date",
        )

    with session_scope() as session:
        group_uuid: Optional[UUID] = None
        if payload.group_id:
            try:
                group_uuid = UUID(payload.group_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="group not found"
                )
            # Membership is checked, not trusted: otherwise anyone could file a
            # trip into a group they do not belong to.
            member = session.scalar(
                select(GroupMember).where(
                    GroupMember.group_id == group_uuid,
                    GroupMember.account_id == account.account_id,
                )
            )
            if member is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="group not found"
                )

        origin_uuid: Optional[UUID] = None
        if payload.origin_location_id:
            try:
                origin_uuid = UUID(payload.origin_location_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="origin_location_id is not a valid id",
                )
            if session.get(Location, origin_uuid) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="origin location not found",
                )

        trip = Trip(
            owner_account_id=account.account_id,
            name=payload.name.strip(),
            origin_location_id=origin_uuid,
            group_id=group_uuid,
            is_group=payload.is_group,
            start_date=payload.start_date,
            end_date=payload.end_date,
            budget_limit=payload.budget_limit,
            status=TripStatus.DRAFT,
        )
        session.add(trip)
        session.flush()
        return _trip_out(trip)


@router.get("/trips/{trip_id}", response_model=TripOut)
def get_trip(
    trip_id: str,
    account: Account = Depends(current_account),
) -> TripOut:
    """One trip. 404 if it does not exist or the caller is not a member.

    Deliberately the same 404 either way - distinguishing them would let
    someone probe for which trip ids exist.
    """
    with session_scope() as session:
        return _trip_out(_load_trip(session, trip_id, account))


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


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------


@router.get("/trips/{trip_id}/itinerary", response_model=list[ItineraryItemOut])
def list_itinerary(
    trip_id: str,
    account: Account = Depends(current_account),
) -> list[ItineraryItemOut]:
    """The trip's activities, ordered by date then time.

    Empty list when the trip has nothing scheduled.
    """
    with session_scope() as session:
        trip = _load_trip(session, trip_id, account)
        items = session.scalars(
            select(ItineraryItem)
            .where(ItineraryItem.trip_id == trip.trip_id)
            # nullslast so unscheduled items sit at the bottom rather than
            # ahead of day one.
            .order_by(
                ItineraryItem.date.asc().nullslast(),
                ItineraryItem.time.asc().nullslast(),
            )
        ).all()
        return [_item_out(item) for item in items]


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
    """Add an activity to the trip.

    For flights use POST /trips/{trip_id}/flights instead - it also records the
    carrier, the leg and the booking link.
    """
    with session_scope() as session:
        trip = _load_trip(session, trip_id, account)

        location_uuid: Optional[UUID] = None
        if payload.location_id:
            try:
                location_uuid = UUID(payload.location_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="location_id is not a valid id",
                )
            if session.get(Location, location_uuid) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="location not found"
                )

        item = ItineraryItem(
            trip_id=trip.trip_id,
            location_id=location_uuid,
            date=payload.date,
            time=payload.time,
            duration=(
                timedelta(minutes=payload.duration_minutes)
                if payload.duration_minutes is not None
                else None
            ),
            activity_type=payload.activity_type,
            weather_dependent=payload.weather_dependent,
            cost=payload.cost,
            description=payload.description,
        )
        session.add(item)
        session.flush()
        return _item_out(item)


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
