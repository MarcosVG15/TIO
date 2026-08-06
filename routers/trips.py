from __future__ import annotations

import logging
from datetime import date as date_type, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import or_, select

import itinerary as planner
import maps
import recommend
from DATABASE.ORM import (
    Account,
    ActivityType,
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
    PlannedItemOut,
    TripCreate,
    TripOut,
    TripUpdate,
)

log = logging.getLogger(__name__)

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


def _trip_centre(trip: Trip):
    """Mean position of the trip's located stops, for a map to open on."""
    points = [
        {"latitude": i.location.latitude, "longitude": i.location.longitude}
        for i in (trip.itinerary_items or [])
        if i.location is not None
        and i.location.latitude is not None
        and i.location.longitude is not None
    ]
    return maps.centroid(points)


def _trip_out(trip: Trip) -> TripOut:
    centre = _trip_centre(trip)
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
        lat=centre[0] if centre else None,
        lng=centre[1] if centre else None,
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
    background: BackgroundTasks,
    account: Account = Depends(current_account),
) -> TripOut:
    """Create a trip and generate its day-by-day plan.

    The trip is created and returned immediately; the plan is built afterwards.

    That split is forced by the frontend, which aborts every request after
    twelve seconds. Composing three itineraries takes two LLM calls and rather
    longer than that, so doing it inline produced "Could not connect to the
    server. Your plan wasn't generated." - the work had in fact started, and
    the browser simply stopped listening.

    The screen already copes: when a trip response carries no itinerary it
    fetches GET /trips/{id}/itinerary separately, which returns the plan once
    it lands.
    """
    problem = payload.date_problem()
    if problem:
        # A plain string, not a Pydantic error object: the screen renders
        # `detail` verbatim and shows nothing at all when it is a list, which
        # is why an impossible date range used to fail silently.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem
        )

    group_uuid: Optional[UUID] = None
    if payload.group_id:
        try:
            group_uuid = UUID(payload.group_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="group not found"
            )

    with session_scope() as session:
        if group_uuid is not None:
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
            name=payload.trip_name,
            origin_location_id=origin_uuid,
            group_id=group_uuid,
            is_group=group_uuid is not None,
            start_date=payload.start_date,
            end_date=payload.end_date,
            budget_limit=payload.budget_limit,
            status=TripStatus.DRAFT,
        )
        session.add(trip)
        session.flush()
        trip_id = trip.trip_id
        created = _trip_out(trip)

    # After the response, not before it. See the docstring: two LLM calls do
    # not fit in the browser's twelve-second budget.
    background.add_task(_generate_itinerary, trip_id, payload, account)
    return created


def _generate_itinerary(
    trip_id: UUID,
    payload: TripCreate,
    account: Account,
) -> list[PlannedItemOut]:
    """Build and persist a plan for a freshly created trip.

    Every failure path returns an empty list rather than raising: the trip
    exists either way, and a plan that could not be built is something the user
    can retry from, not a reason to lose the trip.
    """
    days = payload.days or 3
    country, city = _split_destination(payload.destination)

    try:
        pool = recommend.build_pool(
            country=country,
            account_ids=[account.account_id],
            target=45,
            city=city,
            include_proposals=True,
            notable_only=True,
        )
    except Exception as exc:
        log.info("no pool for %s: %s", payload.destination, exc)
        return []

    try:
        result = planner.compose(
            pool=pool,
            days=days,
            feedback=payload.prompt or payload.notes or payload.vibe,
        )
    except Exception as exc:
        log.warning("could not compose a plan for %s: %s", payload.destination, exc)
        return []

    if not result.plans:
        return []

    # Resolved through the mapping the composer actually showed the model,
    # not by guessing at ids.
    by_ref = result.by_ref or {}
    chosen = result.plans[0]
    out: list[PlannedItemOut] = []

    with session_scope() as session:
        for day in chosen.days:
            for order, stop in enumerate(day.stops):
                candidate = by_ref.get(stop.ref)
                if candidate is None:
                    continue
                item = ItineraryItem(
                    trip_id=trip_id,
                    location_id=candidate.location_id,
                    date=(
                        payload.start_date + timedelta(days=day.day - 1)
                        if payload.start_date
                        else None
                    ),
                    activity_type=_activity_type(candidate.category),
                    description=stop.note,
                    # day / part_of_day / title are what the screen reads, and
                    # none is an ORM column - a plan is a different shape from a
                    # booking. JSONB keeps them with the row rather than in a
                    # parallel table.
                    details={
                        "kind": "planned",
                        "day": day.day,
                        "part_of_day": stop.part_of_day,
                        "title": stop.title,
                        "city": day.city,
                        "order": order,
                    },
                )
                session.add(item)
                session.flush()
                out.append(
                    PlannedItemOut(
                        itinerary_id=str(item.itinerary_id),
                        day=day.day,
                        part_of_day=stop.part_of_day,
                        title=stop.title,
                        description=stop.note,
                        completed=False,
                        location=LocationOut(
                            location_id=str(candidate.location_id),
                            name=candidate.name,
                            country=candidate.country,
                            city=candidate.city,
                            latitude=candidate.latitude,
                            longitude=candidate.longitude,
                            picture=candidate.picture,
                        ),
                    )
                )
    return out


def _split_destination(destination):
    """Recover a country from the screen's single free-text box.

    "Kyoto, Japan" gives ("Japan", "Kyoto"); "Spain" gives ("Spain", None).
    Last comma-separated part is the country by convention.
    """
    parts = [p.strip() for p in destination.split(",") if p.strip()]
    if len(parts) >= 2:
        return parts[-1], parts[0]
    return destination.strip(), None


def _activity_type(category):
    try:
        return ActivityType(category)
    except ValueError:
        return ActivityType.OTHER


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


@router.get("/trips/{trip_id}/map")
def trip_map(
    trip_id: str,
    account: Account = Depends(current_account),
):
    """The trip as GeoJSON, plus what a map needs to open on it.

    A FeatureCollection: one Point per located stop, one LineString per day in
    visiting order. Renders unmodified in Leaflet, MapLibre, Mapbox or QGIS.

    Each day's LineString also carries a Google Maps walking-directions link,
    which is the part that is useful *today* - the deployed frontend has no map
    library, so a link the traveller can open beats a map view that needs a
    frontend release.

    Stops with no coordinates are counted in `properties.unlocated` rather than
    dropped silently: a route missing two of its five stops should be visibly
    incomplete.
    """
    with session_scope() as session:
        trip = _load_trip(session, trip_id, account)
        items = session.scalars(
            select(ItineraryItem)
            .where(ItineraryItem.trip_id == trip.trip_id)
            .order_by(
                ItineraryItem.date.asc().nullslast(),
                ItineraryItem.time.asc().nullslast(),
            )
        ).all()

        stops = []
        for item in items:
            details = dict(item.details or {})
            location = item.location
            stops.append(
                {
                    "itinerary_id": str(item.itinerary_id),
                    "name": location.name if location else None,
                    "title": details.get("title") or item.description,
                    "day": details.get("day") or _day_from_dates(trip, item),
                    "part_of_day": details.get("part_of_day"),
                    "order": details.get("order") or 0,
                    "category": item.activity_type.value,
                    "completed": item.booking_status.value == "confirmed",
                    "latitude": location.latitude if location else None,
                    "longitude": location.longitude if location else None,
                }
            )
        trip_name = trip.name

    return maps.geojson(stops, trip_name)


def _day_from_dates(trip, item) -> int:
    """Day number when the plan metadata is absent - a hand-added item."""
    if trip.start_date and item.date:
        return max(1, (item.date - trip.start_date).days + 1)
    return 1


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
