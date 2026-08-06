"""Hotel prices and booking links, from Nuitee Connect (LiteAPI).

    python hotels.py Barcelona 2026-09-01 2026-09-05 ES

Configuration:

    LITEAPI_KEY        private key, server side only (sand_... or prod_...)
    LITEAPI_BASE       override the base URL; defaults to the v3.0 production host
    HOTEL_PROVIDER     "liteapi" (default) or "none" to disable

Two calls, not one, and that shape is forced by the API: hotels are found by
country and city, then priced by id for a specific stay. Hotellook did both at
once and returned a "price from"; this returns real bookable rates, which is
worth the extra round trip - but it is why hotel lookup is capped to one city
per plan and run concurrently with everything else.

The rate returned here is the TOTAL for the stay, not a nightly figure. That is
the opposite of what Hotellook gave, and getting it backwards would show a four
night total as a nightly rate - so the conversion happens once, here, and
`HotelOffer` continues to carry a nightly price because that is what a budget
line reads.

Degrading cleanly is a requirement. A plan whose flights and places are real is
still worth showing when hotels are unavailable, so every failure returns "no
offers" rather than raising - except missing configuration, which is worth
shouting about.
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

#: Nuitee Connect v3. Hotel discovery and rating live under one host.
_DEFAULT_BASE = "https://api.liteapi.travel/v3.0"


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
    #: The provider's own guest rating, when present.
    rating: Optional[float] = None
    distance_km: Optional[float] = None
    #: Never empty - an offer nobody can book is not worth showing.
    booking_url: str = ""
    provider: str = "liteapi"
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


class LiteApiProvider:
    """Real bookable rates from Nuitee Connect."""

    name = "liteapi"

    def __init__(
        self,
        key: Optional[str] = None,
        base: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.key = key or os.getenv("LITEAPI_KEY") or ""
        self.base = (base or os.getenv("LITEAPI_BASE") or _DEFAULT_BASE).rstrip("/")
        if not self.key:
            raise NotConfigured("LITEAPI_KEY is not set")
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=_TIMEOUT, headers={"X-API-Key": self.key}
            )
        return self._client

    def hotels_in(
        self, city: str, country_code: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Hotel records for a city. `countryCode` is required by the API."""
        try:
            response = self._http().get(
                f"{self.base}/data/hotels",
                params={
                    "countryCode": country_code.upper(),
                    "cityName": city,
                    "limit": max(1, limit),
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.info("hotel lookup failed for %s/%s: %s", city, country_code, exc)
            return []
        data = payload.get("data") if isinstance(payload, dict) else payload
        return [row for row in (data or []) if isinstance(row, dict)]

    def rates_for(
        self,
        hotel_ids: list[str],
        check_in: date,
        check_out: date,
        adults: int,
        currency: str,
        nationality: str,
    ) -> dict[str, Any]:
        """hotelId -> cheapest rate object, for one stay."""
        if not hotel_ids:
            return {}
        body = {
            "hotelIds": hotel_ids,
            "checkin": check_in.isoformat(),
            "checkout": check_out.isoformat(),
            "occupancies": [{"adults": max(1, adults), "rooms": 1}],
            "currency": currency.upper(),
            "guestNationality": (nationality or "GB").upper()[:2],
            # Their own cap, so a slow supplier cannot outlast our budget.
            "timeout": 5,
        }
        try:
            response = self._http().post(f"{self.base}/hotels/rates", json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.info("hotel rates failed for %d hotel(s): %s", len(hotel_ids), exc)
            return {}

        cheapest: dict[str, Any] = {}
        for entry in (payload.get("data") or []):
            if not isinstance(entry, dict):
                continue
            hotel_id = str(entry.get("hotelId") or "")
            best: Optional[Decimal] = None
            best_rate: Optional[dict[str, Any]] = None
            for room in entry.get("roomTypes") or []:
                for rate in (room or {}).get("rates") or []:
                    for total in ((rate or {}).get("retailRate") or {}).get("total") or []:
                        amount = _as_decimal((total or {}).get("amount"))
                        if amount is None:
                            continue
                        if best is None or amount < best:
                            best, best_rate = amount, {
                                "amount": amount,
                                "currency": total.get("currency") or currency.upper(),
                                "rate": rate,
                            }
            if hotel_id and best_rate:
                cheapest[hotel_id] = best_rate
        return cheapest

    def search(
        self,
        city: str,
        check_in: date,
        check_out: date,
        adults: int = 1,
        currency: str = "EUR",
        limit: int = 5,
        country_code: Optional[str] = None,
        nationality: str = "GB",
    ) -> list[HotelOffer]:
        """Cheapest bookable rates for a city and stay, or an empty list.

        Never raises on a provider failure: accommodation is one line of a plan
        and the rest is still useful without it.
        """
        if check_out <= check_in or not country_code:
            # No country code means the API cannot be queried at all - it is a
            # required parameter, not an optional filter.
            return []

        nights = _nights(check_in, check_out)
        found = self.hotels_in(city, country_code, limit=max(limit * 3, 10))
        if not found:
            return []

        by_id = {str(h.get("id")): h for h in found if h.get("id")}
        priced = self.rates_for(
            list(by_id)[:20], check_in, check_out, adults, currency, nationality
        )

        offers: list[HotelOffer] = []
        for hotel_id, rate in priced.items():
            hotel = by_id.get(hotel_id) or {}
            total = rate["amount"]
            # Their amount is the whole stay. HotelOffer carries a nightly
            # figure because that is what a budget line reads, so divide once
            # here - and total_price multiplies it straight back.
            nightly = (total / nights).quantize(Decimal("0.01"))
            offers.append(
                HotelOffer(
                    city=city,
                    name=str(hotel.get("name") or "Hotel"),
                    check_in=check_in,
                    check_out=check_out,
                    nightly_price=nightly,
                    currency=str(rate["currency"]),
                    nights=nights,
                    stars=_as_int(hotel.get("stars")),
                    rating=_as_float(hotel.get("rating")),
                    booking_url=(
                        f"https://www.google.com/search?q="
                        f"{httpx.URL(path=str(hotel.get('name') or city)).path.lstrip('/')}"
                        f"+{city}+hotel"
                    ),
                    provider=self.name,
                    is_cached=False,
                    raw={"hotel": hotel, "rate": rate.get("rate")},
                )
            )

        offers.sort(key=lambda o: o.nightly_price)
        return offers[:limit]


def get_provider() -> HotelProvider:
    """The configured provider, or a null one when hotels are switched off."""
    choice = (os.getenv("HOTEL_PROVIDER") or "liteapi").lower()
    if choice in {"none", "off", "null"}:
        return NullProvider()
    try:
        return LiteApiProvider()
    except NotConfigured:
        log.info("no LITEAPI_KEY; hotel search disabled")
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
