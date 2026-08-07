"""Turn a composed plan into a costed one: flights, hotels, tickets, total.

The division of labour that makes this safe. The model composes - which places,
which order, which city on which day - and it is never asked for a price, an
airline or a hotel name. This module reads the plan it produced, works out how
many nights fall in each city, and asks the providers what those things
actually cost. A number a traveller sees therefore came from Travelpayouts, from
Hotellook, or from `locations.price`, or it is labelled an estimate.

That is the same rule as the `ref` system one layer up: the model cannot invent
a place, and it must not be able to invent a fare either. A hallucinated museum
is embarrassing; a hallucinated 240 EUR flight that does not exist is a
traveller at an airport.

Providers are injectable, and every one of them is allowed to return nothing.
A plan with no fares and no hotel rates is still a plan worth showing - it just
has a thinner budget - so nothing in here raises when a lookup comes back empty.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional, Sequence

import budget as budget_model
import countries
import flights as flight_service
import hotels as hotel_service
from airports import iata_for_city

log = logging.getLogger(__name__)

#: A plan that hops through more cities than this is not a holiday, and pricing
#: a hotel for each would be a lot of calls for a plan nobody would book.
MAX_CITY_BLOCKS = 6


class CityStay:
    """One unbroken run of days in a single city."""

    def __init__(self, city: str, first_day: int, last_day: int) -> None:
        self.city = city
        self.first_day = first_day
        self.last_day = last_day
        #: True for the last stay of the trip, which is one night shorter than
        #: its day count: you check out on the final morning rather than
        #: sleeping through it. Set by city_stays once it knows which is last.
        self.is_final = False

    @property
    def nights(self) -> int:
        """Nights slept, not days spent.

        A three-day trip is two nights. Counting days as nights overcharged
        every plan by one night on its final stay - and accommodation is the
        largest estimated line, so a two-night city break was quoted 50% high.
        """
        days = self.last_day - self.first_day + 1
        return max(1, days - 1 if self.is_final else days)

    def check_in(self, start: date) -> date:
        return start + timedelta(days=self.first_day - 1)

    def check_out(self, start: date) -> date:
        """Derived from nights, not from the day number.

        Returning `start + last_day` asked the provider for one night too many
        on the final stay - so the rate quoted was for a longer booking than the
        trip, and `nights` being right locally did not help because the price
        came back already wrong.
        """
        return self.check_in(start) + timedelta(days=self.nights)

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"CityStay({self.city!r}, d{self.first_day}-d{self.last_day})"


def city_stays(days: Iterable[Any]) -> list[CityStay]:
    """Group consecutive days in the same city into stays.

    Consecutive rather than merely equal: a plan that goes Vienna, Salzburg,
    Vienna is two Vienna stays and two hotel bookings, not one of three nights.
    Collapsing them would quietly price a trip nobody could take.
    """
    stays: list[CityStay] = []
    for day in days:
        city = (getattr(day, "city", None) or "").strip()
        number = getattr(day, "day", None)
        if not city or not isinstance(number, int):
            continue
        if stays and stays[-1].city == city and stays[-1].last_day == number - 1:
            stays[-1].last_day = number
        else:
            stays.append(CityStay(city, number, number))
    if stays:
        # The trip ends here, so this stay has one fewer night than it has days.
        stays[-1].is_final = True
    return stays


def _ticket_costs(
    plan: Any,
    by_ref: Optional[dict[int, Any]],
    travellers: int,
) -> tuple[Decimal, int, int]:
    """Sum the entry prices we actually know.

    Returns (total, priced, unpriced). The two counts matter: `locations.price`
    is sparsely populated, so a small total usually means we know few prices
    rather than that the places are free - and a budget that says "Attractions:
    12.00" for a week of museums is worse than one that admits the gap.
    """
    if not by_ref:
        return Decimal("0"), 0, 0

    total = Decimal("0")
    priced = unpriced = 0
    for day in getattr(plan, "days", []) or []:
        for stop in getattr(day, "stops", []) or []:
            candidate = by_ref.get(getattr(stop, "ref", None))
            if candidate is None:
                continue
            price = getattr(candidate, "price", None)
            if price is None:
                unpriced += 1
                continue
            total += Decimal(str(price)) * max(1, travellers)
            priced += 1
    return total, priced, unpriced


def _flight_for(
    origin: str,
    city: str,
    country: Optional[str],
    depart: date,
    ret: Optional[date],
    travellers: int,
    currency: str,
    search: Callable[..., list],
    resolve_iata: Callable[..., Optional[str]],
) -> Optional[Any]:
    """Cheapest fare into the plan's first city, or None."""
    destination = resolve_iata(city, country)
    if not destination or destination == origin:
        return None
    try:
        offers = search(
            origin=origin,
            destination=destination,
            depart_date=depart,
            return_date=ret,
            adults=max(1, travellers),
            currency=currency.lower(),
        )
    except Exception as exc:
        # Includes BadRoute for a pair the provider does not serve, which is
        # ordinary rather than exceptional: plenty of city pairs have no
        # cached fare at all.
        log.info("no fare for %s-%s: %s", origin, destination, exc)
        return None
    return offers[0] if offers else None


def cost_plan(
    plan: Any,
    *,
    start_date: Optional[date],
    travellers: int = 1,
    budget_tier: Optional[str] = None,
    origin_iata: Optional[str] = None,
    country: Optional[str] = None,
    by_ref: Optional[dict[int, Any]] = None,
    currency: str = "EUR",
    flight_search: Optional[Callable[..., list]] = None,
    hotel_search: Optional[Callable[..., list]] = None,
    resolve_iata: Optional[Callable[..., Optional[str]]] = None,
    nationality: Optional[str] = None,
) -> dict[str, Any]:
    """Price a composed plan. Never raises on a provider failure.

    `start_date` is optional because a plan can be generated before dates are
    chosen. Without it there are no stays to price and no fares to look up, so
    the result carries the ticket costs and the living estimate only - which is
    still worth showing, and still honest about what it is.
    """
    flight_search = flight_search or flight_service.search_flights
    hotel_search = hotel_search or hotel_service.search_hotels
    # Injectable because resolving a city to an airport reads the database and
    # can fall back to a model call. That is a real dependency on a code path
    # whose job is arithmetic, and leaving it implicit made this module
    # untestable without a live database.
    resolve_iata = resolve_iata or iata_for_city

    stays = city_stays(getattr(plan, "days", []) or [])[:MAX_CITY_BLOCKS]
    day_count = len(getattr(plan, "days", []) or [])
    money = budget_model.Budget(currency=currency, travellers=max(1, travellers))

    notes: list[str] = []
    flight_out: Optional[dict[str, Any]] = None
    accommodation: list[dict[str, Any]] = []

    # ---- flights ---------------------------------------------------------
    if origin_iata and stays and start_date:
        ret = start_date + timedelta(days=day_count)
        offer = _flight_for(
            origin_iata, stays[0].city, country, start_date, ret,
            travellers, currency, flight_search, resolve_iata,
        )
        if offer is not None:
            # The provider quotes for the party when adults is passed, so this
            # is the trip total rather than a per-person figure.
            flight_out = {
                "origin": offer.origin,
                "destination": offer.destination,
                "depart_date": offer.depart_date.isoformat(),
                "return_date": (
                    offer.return_date.isoformat() if offer.return_date else None
                ),
                "price": float(offer.price),
                "currency": offer.currency,
                "airline": offer.airline,
                "stops": offer.stops,
                "booking_url": offer.booking_url,
                "is_cached": offer.is_cached,
                "summary": offer.summary(),
            }
            money.add(
                "Flights",
                Decimal(str(offer.price)),
                grounded=True,
                source=f"{offer.provider} cached fare",
                detail=offer.summary(),
            )
            if offer.return_date is None:
                notes.append(
                    "Only a one-way fare was available for this route; the "
                    "return leg is not included in the total."
                )
        else:
            notes.append(
                "No cached fare was found for this route, so flights are not "
                "in the total."
            )

    # ---- accommodation ---------------------------------------------------
    if start_date:
        for stay in stays:
            # country_code is required by the provider, not optional - without
            # it the search returns nothing at all, which is why accommodation
            # never appeared even with a working key. nationality affects which
            # rates are offered; the traveller's own country is the honest
            # answer when we know it.
            offers = hotel_search(
                stay.city,
                stay.check_in(start_date),
                stay.check_out(start_date),
                adults=max(1, travellers),
                currency=currency,
                limit=3,
                country_code=countries.iso2(country),
                nationality=nationality or "GB",
            )
            if not offers:
                # Estimated rather than omitted. Accommodation is usually the
                # second-largest line in a trip, so leaving it out understates
                # the total badly enough to mislead - and the whole point of
                # the grounded flag is that we can include it and say so.
                budget_model.accommodation_estimate(
                    money, stay.nights, budget_tier, stay.city
                )
                notes.append(
                    f"No live hotel rate for {stay.city}; accommodation is "
                    f"estimated for {stay.nights} night(s)."
                )
                continue
            best = offers[0]
            accommodation.append({
                "city": stay.city,
                "name": best.name,
                "stars": best.stars,
                "check_in": best.check_in.isoformat(),
                "check_out": best.check_out.isoformat(),
                "nights": best.nights,
                "nightly_price": float(best.nightly_price),
                "total_price": float(best.total_price),
                "currency": best.currency,
                "booking_url": best.booking_url,
                "summary": best.summary(),
            })
            money.add(
                f"Accommodation - {stay.city}",
                best.total_price,
                grounded=True,
                source=f"{best.provider} cached rate",
                detail=best.summary(),
            )

    # ---- tickets ---------------------------------------------------------
    tickets, priced, unpriced = _ticket_costs(plan, by_ref, travellers)
    if priced:
        money.add(
            "Attractions and tickets",
            tickets,
            grounded=True,
            source=f"listed prices for {priced} place(s)",
            detail=(
                f"{unpriced} place(s) have no price recorded and are not "
                "included" if unpriced else None
            ),
        )
    elif unpriced:
        notes.append(
            f"No entry prices are recorded for any of the {unpriced} places "
            "in this plan, so tickets are not in the total."
        )

    # ---- everything nobody quotes ---------------------------------------
    budget_model.living_costs(money, day_count, budget_tier)

    return {
        "flights": flight_out,
        "accommodation": accommodation,
        "budget": money.to_dict(),
        "nights_by_city": [
            {"city": s.city, "nights": s.nights, "from_day": s.first_day}
            for s in stays
        ],
        "notes": notes,
    }
