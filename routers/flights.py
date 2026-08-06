"""Flight search, and pinning a fare onto a trip.

Prices are indicative - see `flights` for why. Every response carries a
disclaimer, and the endpoint refuses to omit it, because the difference between
"this flight costs 89 EUR" and "this flight cost 89 EUR to somebody in the last
48 hours" is somebody's holiday budget.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

import flights as flight_service
from DATABASE.ORM import (
    Account,
    ActivityType,
    BookingStatus,
    GroupMember,
    ItineraryItem,
    Trip,
    session_scope,
)
from deps import current_account
from schemas import (
    FlightOfferOut,
    FlightSearchResponse,
    ItineraryItemOut,
    SaveFlightRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["flights"])

DISCLAIMER = (
    "Prices come from recent searches by other travellers and are indicative, "
    "not a quote. The airline or agent reprices at checkout."
)


def _offer_out(offer: flight_service.FlightOffer) -> FlightOfferOut:
    return FlightOfferOut(
        origin=offer.origin,
        destination=offer.destination,
        depart_date=offer.depart_date,
        return_date=offer.return_date,
        price=offer.price,
        currency=offer.currency,
        airline=offer.airline,
        flight_number=offer.flight_number,
        stops=offer.stops,
        duration_minutes=offer.duration_minutes,
        booking_url=offer.booking_url,
        provider=offer.provider,
        is_cached=offer.is_cached,
        fetched_at=offer.fetched_at,
    )


@router.get("/flights/search", response_model=FlightSearchResponse)
def search(
    origin: str = Query(min_length=3, max_length=3, description="IATA code"),
    destination: str = Query(min_length=3, max_length=3, description="IATA code"),
    depart_date: date_type = Query(description="YYYY-MM-DD"),
    return_date: Optional[date_type] = Query(default=None),
    adults: int = Query(default=1, ge=1, le=9),
    currency: str = Query(default="eur", max_length=3),
    limit: int = Query(default=10, ge=1, le=30),
    account: Account = Depends(current_account),
) -> FlightSearchResponse:
    """Cheapest known fares for a route, cheapest first.

    Authenticated even though it reads no user data: it spends a third-party
    rate limit, and an open endpoint would let anyone burn it.
    """
    try:
        offers = flight_service.search_flights(
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            adults=adults,
            currency=currency,
            limit=limit,
        )
    except flight_service.NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Flight search is not configured on this server.",
        ) from exc
    except flight_service.FlightError as exc:
        # Provider faults and bad input both land here; the message is written
        # to be safe to show.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return FlightSearchResponse(
        offers=[_offer_out(offer) for offer in offers],
        disclaimer=DISCLAIMER,
    )


def _load_writable_trip(session, trip_id: str, account: Account) -> Trip:
    """The trip, if this caller is allowed to change it.

    Owner or group member. A 404 rather than a 403 for someone else's trip, so
    trip ids cannot be probed.
    """
    try:
        trip_uuid = UUID(trip_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="trip not found"
        )

    trip = session.scalar(
        select(Trip).where(Trip.trip_id == trip_uuid, Trip.deleted_at.is_(None))
    )
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="trip not found"
        )

    if trip.owner_account_id == account.account_id:
        return trip

    if trip.group_id is not None:
        member = session.scalar(
            select(GroupMember).where(
                GroupMember.group_id == trip.group_id,
                GroupMember.account_id == account.account_id,
            )
        )
        if member is not None:
            return trip

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="trip not found"
    )


@router.post(
    "/trips/{trip_id}/flights",
    response_model=ItineraryItemOut,
    status_code=status.HTTP_201_CREATED,
)
def save_flight(
    trip_id: str,
    payload: SaveFlightRequest,
    account: Account = Depends(current_account),
) -> ItineraryItemOut:
    """Pin a searched fare onto the trip as a transport item.

    Stored as `booking_status = pending`: we did not sell anything, and the
    traveller still has to buy it through `booking_url`. Marking it confirmed
    would tell them they have a seat they do not have.

    The offer is sent back in full rather than by reference because the
    provider's cache is not addressable - there is no id to re-fetch.
    """
    with session_scope() as session:
        trip = _load_writable_trip(session, trip_id, account)

        summary = flight_service.FlightOffer(
            origin=payload.origin.upper(),
            destination=payload.destination.upper(),
            depart_date=payload.depart_date,
            return_date=payload.return_date,
            price=payload.price if payload.price is not None else Decimal("0"),
            currency=payload.currency.upper(),
            airline=payload.airline,
            flight_number=payload.flight_number,
            stops=payload.stops,
            duration_minutes=payload.duration_minutes,
            booking_url=payload.booking_url,
        ).summary()

        item = ItineraryItem(
            trip_id=trip.trip_id,
            # Flights have no Location row - the corpus is places to visit, not
            # airports - so the leg lives in description and details.
            location_id=None,
            date=payload.depart_date,
            activity_type=ActivityType.TRANSPORT,
            weather_dependent=False,
            booking_status=BookingStatus.PENDING,
            booking_ref=payload.flight_number,
            booking_url=payload.booking_url,
            cost=payload.price,
            description=summary,
            details={
                "kind": "flight",
                "origin": payload.origin.upper(),
                "destination": payload.destination.upper(),
                "depart_date": payload.depart_date.isoformat(),
                "return_date": (
                    payload.return_date.isoformat() if payload.return_date else None
                ),
                "airline": payload.airline,
                "flight_number": payload.flight_number,
                "stops": payload.stops,
                "duration_minutes": payload.duration_minutes,
                "currency": payload.currency.upper(),
                # So the UI can age the price rather than presenting it as
                # current forever.
                "price_is_indicative": True,
            },
        )
        session.add(item)
        session.flush()

        return ItineraryItemOut(
            itinerary_id=str(item.itinerary_id),
            trip_id=str(item.trip_id),
            location=None,
            date=item.date,
            time=item.time,
            duration_minutes=payload.duration_minutes,
            activity_type=item.activity_type,
            weather_dependent=item.weather_dependent,
            booking_status=item.booking_status,
            booking_ref=item.booking_ref,
            booking_url=item.booking_url,
            cost=item.cost,
            description=item.description,
            details=dict(item.details),
        )
