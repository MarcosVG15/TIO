"""Fares for the cities a plan might use.

Sits between `recommend` (which cities are worth going to) and `itinerary`
(which one to build days in), because getting there is part of whether a trip
is a good idea. A destination that fits perfectly and costs four hundred euros
to reach is often a worse suggestion than a near-miss at eighty, and the
planner cannot weigh that unless somebody tells it.

Everything here degrades rather than fails. No origin, no dates, no token, no
route, provider down - the plan is simply built without fares, because a trip
with no flight attached is still a trip and an error page is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import airports
import flights as flight_service

log = logging.getLogger(__name__)

#: How many cities to price. Each is one provider call, and the planner only
#: ever builds days in a handful of them.
MAX_CITIES = 6


@dataclass
class CityFare:
    """The cheapest indicative return fare to one city."""

    city: str
    country: Optional[str]
    destination_iata: str
    offer: flight_service.FlightOffer

    @property
    def price(self):
        return self.offer.price

    @property
    def is_return(self) -> bool:
        return self.offer.return_date is not None

    def summary(self) -> str:
        kind = "return" if self.is_return else "one way"
        return (
            f"{self.city}: {self.offer.price} {self.offer.currency} {kind} "
            f"({self.offer.origin}-{self.destination_iata})"
        )


def origin_code(explicit: Optional[str], home_city: Optional[str]) -> Optional[str]:
    """Where the traveller is flying from.

    An explicit origin wins; otherwise the home city from their profile, which
    is the whole reason onboarding collects it.
    """
    if explicit:
        return airports.iata_for_city(explicit)
    if home_city:
        return airports.iata_for_city(home_city)
    return None


def fares_for_cities(
    origin: str,
    cities: Sequence[tuple[str, Optional[str]]],
    depart: date,
    ret: Optional[date] = None,
    adults: int = 1,
    currency: str = "eur",
) -> list[CityFare]:
    """Cheapest fare to each city, cheapest city first.

    One provider call per city, capped. Cities whose airport cannot be resolved
    or that have no cached route are skipped silently - the planner treats a
    missing fare as "unknown", not as "unreachable".
    """
    found: list[CityFare] = []

    for city, country in list(cities)[:MAX_CITIES]:
        code = airports.iata_for_city(city, country)
        if not code or code == origin:
            continue

        # Round trip first, then one way. The provider's cache is keyed on the
        # exact dates searched, and far fewer people search a specific return
        # pair than a single outbound - measured on this account, LON-BCN has
        # a one-way fare and no return fare for the same dates. A one-way price
        # is worth showing; it is labelled as one, and it still indicates
        # whether a city is cheap or dear to reach.
        offers = []
        for attempt_return in ([ret, None] if ret else [None]):
            try:
                offers = flight_service.search_flights(
                    origin=origin,
                    destination=code,
                    depart_date=depart,
                    return_date=attempt_return,
                    adults=adults,
                    currency=currency,
                    limit=1,
                )
            except flight_service.BadRoute:
                # No service on that pair at all. Normal, not an error, and
                # retrying one-way will not help.
                break
            except flight_service.FlightError as exc:
                log.info("fare lookup failed for %s: %s", city, exc)
                break
            if offers:
                break

        if offers:
            found.append(
                CityFare(city=city, country=country, destination_iata=code,
                         offer=offers[0])
            )

    found.sort(key=lambda f: f.price)
    return found


def prompt_block(fares: Sequence[CityFare], origin: Optional[str]) -> str:
    """What the itinerary composer is told about getting there.

    Deliberately explicit that these are indicative: the composer writes the
    tradeoffs paragraph, and it should not tell a traveller a fare is
    guaranteed when the provider only reports what somebody else paid.
    """
    if not origin:
        return (
            "No origin airport known, so no fares were checked. Do not mention "
            "flight costs or travel days."
        )
    if not fares:
        return (
            f"Flying from {origin}, but no cached fares were found for these "
            f"cities. Do not invent prices."
        )

    lines = [f"Flying from {origin}. Cheapest recent fares:"]
    lines.extend(f"  - {fare.summary()}" for fare in fares)
    lines.append(
        "These are indicative, from recent searches, not quotes. Prefer a "
        "cheaper city when the fit is otherwise similar, and say so in the "
        "tradeoffs. Day one starts after arrival and the last day ends before "
        "the flight home - do not schedule a full morning on either."
    )
    return "\n".join(lines)
