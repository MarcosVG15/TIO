"""Hotel prices and booking links, from Travelpayouts' Hotellook API.

    python hotels.py Barcelona 2026-09-01 2026-09-05

Configuration - the same account as flights, so one token covers both:

    TRAVELPAYOUTS_TOKEN    API token from the Travelpayouts dashboard
    TRAVELPAYOUTS_MARKER   affiliate marker, embedded in booking links
    HOTEL_PROVIDER         "hotellook" (default) or "none" to disable

What these prices are, precisely: Hotellook's cached "price from" per hotel for
the requested dates. They are indicative, exactly like the flight fares - a
starting rate someone was quoted recently, not an offer we can honour. Every
figure that reaches a traveller has to be labelled that way, and the booking
link is the only route to a real, bookable price.

Degrading cleanly is a requirement, not a nicety. A trip plan whose flights and
places are real is still worth showing when the hotel API is down or the
account has no hotel access; a 500 from the whole planner because accommodation
could not be priced is not. Every failure here returns "no offers" rather than
raising, apart from misconfiguration, which is worth shouting about.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(8.0, connect=4.0)

#: Hotellook's cached-prices endpoint. Returns one entry per hotel with a
#: "price from" for the stay, which is what a budget line needs.
_CACHE_URL = "https://engine.hotellook.com/api/v2/cache.json"
#: Resolves a free-text city to Hotellook's own location id.
_LOOKUP_URL = "https://engine.hotellook.com/api/v2/lookup.json"


class HotelError(Exception):
    """Any hotel lookup failure."""


class NotConfigured(HotelError):
    """No token, so no hotel search is possible."""


@dataclass(frozen=True)
class HotelOffer:
    """One indicative nightly rate, plus where to go and book it."""

    city: str
    name: str
    check_in: date
    check_out: date
    #: Per night, which is what a budget line multiplies out.
    nightly_price: Decimal
    currency: str
    nights: int
    stars: Optional[int] = None
    #: Hotellook's own 0..10 guest rating, when present.
    rating: Optional[float] = None
    distance_km: Optional[float] = None
    #: Never empty - an offer nobody can book is not worth showing.
    booking_url: str = ""
    provider: str = "hotellook"
    #: When we read it, not when it was quoted.
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_cached: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_price(self) -> Decimal:
        return (self.nightly_price * self.nights).quantize(Decimal("0.01"))

    def summary(self) -> str:
        """One line for an itinerary row."""
        stars = f" {self.stars}*" if self.stars else ""
        return (
            f"{self.name}{stars}, {self.city} - {self.nightly_price} "
            f"{self.currency}/night x {self.nights} = {self.total_price} "
            f"{self.currency}"
        )


class HotelProvider(Protocol):
    """What the planner depends on. Implement this to swap providers."""

    name: str

    def search(
        self,
        city: str,
        check_in: date,
        check_out: date,
        adults: int = 1,
        currency: str = "EUR",
        limit: int = 5,
    ) -> list[HotelOffer]:
        ...


class NullProvider:
    """Hotel search switched off. Returns nothing rather than failing."""

    name = "none"

    def search(self, *args: Any, **kwargs: Any) -> list[HotelOffer]:
        return []


def _nights(check_in: date, check_out: date) -> int:
    """At least one - a same-day check-out is still a night's rate."""
    return max(1, (check_out - check_in).days)


def _as_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HotellookProvider:
    """Cached nightly rates from Hotellook, with affiliate booking links."""

    name = "hotellook"

    def __init__(
        self,
        token: Optional[str] = None,
        marker: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.token = token or os.getenv("TRAVELPAYOUTS_TOKEN") or ""
        self.marker = marker or os.getenv("TRAVELPAYOUTS_MARKER") or ""
        if not self.token:
            raise NotConfigured("TRAVELPAYOUTS_TOKEN is not set")
        # Injectable so the search logic is testable without the network.
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=_TIMEOUT)
        return self._client

    def booking_url(self, city: str, check_in: date, check_out: date, adults: int) -> str:
        """Where the traveller completes the booking.

        Search-by-city rather than deep-linking one hotel: the cached rate is
        indicative, so sending someone to a specific hotel page implies a
        precision the price does not have. The search lands on the same city
        and dates and lets them see what is genuinely available.
        """
        params = [
            f"destination={httpx.URL(path=city).path.lstrip('/')}",
            f"checkIn={check_in.isoformat()}",
            f"checkOut={check_out.isoformat()}",
            f"adults={max(1, adults)}",
        ]
        if self.marker:
            params.append(f"marker={self.marker}")
        return "https://search.hotellook.com/?" + "&".join(params)

    def _location_id(self, city: str) -> Optional[int]:
        """Hotellook's id for a city name, or None if it does not know it."""
        try:
            response = self._http().get(
                _LOOKUP_URL,
                params={
                    "query": city,
                    "lang": "en",
                    "lookFor": "city",
                    "limit": 1,
                    "token": self.token,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.info("hotel lookup failed for %r: %s", city, exc)
            return None

        locations = (payload.get("results") or {}).get("locations") or []
        if not locations:
            return None
        return _as_int(locations[0].get("id"))

    def search(
        self,
        city: str,
        check_in: date,
        check_out: date,
        adults: int = 1,
        currency: str = "EUR",
        limit: int = 5,
    ) -> list[HotelOffer]:
        """Cheapest cached rates for a city and stay.

        Never raises on a provider failure. Accommodation is one line of a plan
        and the rest of the plan is still useful without it; a planner that
        500s because a hotel API had a bad minute is worse than one that says
        nothing about hotels.
        """
        if check_out <= check_in:
            return []

        nights = _nights(check_in, check_out)
        try:
            response = self._http().get(
                _CACHE_URL,
                params={
                    "location": city,
                    "checkIn": check_in.isoformat(),
                    "checkOut": check_out.isoformat(),
                    "adults": max(1, adults),
                    "currency": currency.lower(),
                    "limit": max(1, limit),
                    "token": self.token,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.info("hotel search failed for %r: %s", city, exc)
            return []

        # The endpoint returns a bare list; tolerate a wrapped object too
        # rather than trusting one undocumented shape.
        rows = payload if isinstance(payload, list) else payload.get("hotels") or []
        link = self.booking_url(city, check_in, check_out, adults)

        offers: list[HotelOffer] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("hotelName") or row.get("name")
            # priceFrom is the whole stay on this endpoint, so divide it back
            # out - a budget reads per night and multiplies by nights itself.
            stay_total = _as_decimal(row.get("priceFrom") or row.get("priceAvg"))
            if not name or stay_total is None:
                continue
            nightly = (stay_total / nights).quantize(Decimal("0.01"))
            offers.append(
                HotelOffer(
                    city=city,
                    name=str(name),
                    check_in=check_in,
                    check_out=check_out,
                    nightly_price=nightly,
                    currency=currency.upper(),
                    nights=nights,
                    stars=_as_int(row.get("stars")),
                    rating=_as_float(row.get("rating")),
                    distance_km=_as_float(row.get("distance")),
                    booking_url=link,
                    raw=row,
                )
            )

        offers.sort(key=lambda o: o.nightly_price)
        return offers[:limit]


def get_provider() -> HotelProvider:
    """The configured provider, or a null one when hotels are switched off."""
    choice = (os.getenv("HOTEL_PROVIDER") or "hotellook").lower()
    if choice in {"none", "off", "null"}:
        return NullProvider()
    try:
        return HotellookProvider()
    except NotConfigured:
        log.info("no TRAVELPAYOUTS_TOKEN; hotel search disabled")
        return NullProvider()


def search_hotels(
    city: str,
    check_in: date,
    check_out: date,
    adults: int = 1,
    currency: str = "EUR",
    limit: int = 5,
    provider: Optional[HotelProvider] = None,
) -> list[HotelOffer]:
    """Cheapest indicative rates for a city, or an empty list."""
    return (provider or get_provider()).search(
        city, check_in, check_out, adults=adults, currency=currency, limit=limit
    )


def _main() -> int:
    if len(sys.argv) < 4:
        print(__doc__.strip().splitlines()[2].strip())
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    city = sys.argv[1]
    check_in = date.fromisoformat(sys.argv[2])
    check_out = date.fromisoformat(sys.argv[3])

    provider = get_provider()
    if provider.name == "none":
        print("Hotel search is not configured - set TRAVELPAYOUTS_TOKEN in .env")
        return 1

    offers = search_hotels(city, check_in, check_out)
    if not offers:
        print(f"no cached rates for {city} on those dates")
        return 1
    for offer in offers:
        print(offer.summary())
        print(f"    {offer.booking_url}")
    print("\nPrices are indicative and cached - confirm on the booking page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
