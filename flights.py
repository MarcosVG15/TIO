"""Cheap-flight lookup, behind a provider interface.

The interface is not speculative generality. Amadeus decommissioned its
Self-Service API on 17 July 2026 and deactivated existing keys, which is exactly
what happens to code that hardcodes one flight provider. `FlightProvider` is the
seam that makes swapping one a day's work instead of a rewrite.

The default provider is Travelpayouts (Aviasales data), because it is the only
option that satisfies all three requirements at once: free, no accreditation, and
a booking link in the response.

What it is honestly not: a booking engine. Prices come from a rolling cache of
what real users were quoted on Aviasales in the last 48 hours, so an offer here
is *indicative*. The booking link hands the traveller to Aviasales, who reprices
live - the number they pay may differ from the number we showed. Every offer
carries `fetched_at` and `is_cached` so the UI can say so rather than implying a
guarantee. Presenting a stale cache price as a bookable fare is the one way this
feature can actually harm someone's trip budget.

If you later want real bookable fares with a confirmed price, Duffel is the
migration target: paid per confirmed order, but it books. The seam is here for
that.

Configuration:

    TRAVELPAYOUTS_TOKEN    API token (free, from the Travelpayouts dashboard)
    TRAVELPAYOUTS_MARKER   affiliate marker, embedded in booking links
    FLIGHT_PROVIDER        "travelpayouts" (default) or "null" to disable
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Protocol, Sequence

import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

#: Cache-backed cheapest-price endpoint. v1 rather than the newer real-time
#: search because it needs no request signing and no accreditation.
_API_ROOT = "https://api.travelpayouts.com"

#: Where a booking link points. The API returns a path fragment to append.
_BOOKING_ROOT = "https://www.aviasales.com/search"

_TIMEOUT = httpx.Timeout(12.0, connect=5.0)

#: IATA codes are three letters; city codes are too. Anything else is a typo or
#: an injection attempt, and both belong out of a URL.
_IATA_LENGTH = 3


class FlightError(Exception):
    """The flight provider could not answer."""


class NotConfigured(FlightError):
    """No provider credentials, so flight search is switched off."""


class BadRoute(FlightError):
    """The provider rejected the route itself, not the request.

    Distinct from a provider fault because the caller can act on it: an
    unrecognised airport code or a pair with no service between them is the
    user's input, and answering "the flight service is broken" would send them
    to support instead of to their typo. The provider's own wording is carried
    through - "airport KIV: not flightable" is already the right message.
    """


@dataclass(frozen=True)
class FlightOffer:
    """One indicative fare, plus where to go and buy it."""

    origin: str
    destination: str
    depart_date: date
    return_date: Optional[date]
    price: Decimal
    currency: str
    #: Marketing carrier IATA code, when the provider gives one.
    airline: Optional[str] = None
    flight_number: Optional[str] = None
    #: 0 for direct.
    stops: Optional[int] = None
    duration_minutes: Optional[int] = None
    #: Where the traveller completes the purchase. Never None - an offer with
    #: nowhere to book it is not worth showing.
    booking_url: str = ""
    provider: str = "travelpayouts"
    #: When we read it, not when it was quoted. See the module docstring.
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    #: True when the price came from a cache rather than a live search.
    is_cached: bool = True
    #: The provider's own payload, kept so a saved flight can be re-rendered
    #: without another call.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_return(self) -> bool:
        return self.return_date is not None

    def summary(self) -> str:
        """One line for an itinerary row."""
        leg = f"{self.origin} to {self.destination}"
        carrier = f" on {self.airline}" if self.airline else ""
        if self.flight_number:
            carrier += f" {self.flight_number}"
        stops = ""
        if self.stops is not None:
            stops = " direct" if self.stops == 0 else f" ({self.stops} stop)"
        return f"Flight {leg}{carrier}{stops} - {self.price} {self.currency}"


class FlightProvider(Protocol):
    """What the API layer depends on. Implement this to swap providers."""

    name: str

    def search(
        self,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: Optional[date] = None,
        adults: int = 1,
        currency: str = "eur",
        limit: int = 10,
    ) -> list[FlightOffer]:
        ...


def _validate_iata(code: str, what: str) -> str:
    cleaned = (code or "").strip().upper()
    if len(cleaned) != _IATA_LENGTH or not cleaned.isalpha():
        raise FlightError(
            f"{what} must be a 3-letter IATA code, got {code!r}"
        )
    return cleaned


def _ddmm(value: date) -> str:
    """Aviasales encodes dates as DDMM inside the search path."""
    return f"{value.day:02d}{value.month:02d}"


class NullProvider:
    """Answers every search with nothing.

    So that a deployment with no flight credentials returns an empty list and a
    clear reason, rather than 500ing on every trip screen.
    """

    name = "null"

    def search(self, *args, **kwargs) -> list[FlightOffer]:
        raise NotConfigured(
            "flight search is not configured - set TRAVELPAYOUTS_TOKEN"
        )


class TravelpayoutsProvider:
    """Cheapest cached fares from the Aviasales data API."""

    name = "travelpayouts"

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
        # Injectable so the search logic can be tested without the network.
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=_TIMEOUT)
        return self._client

    def booking_url(
        self,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: Optional[date],
        adults: int,
        link_fragment: Optional[str] = None,
    ) -> str:
        """Where to send the traveller to buy the ticket.

        The API returns a ready-made path fragment for some endpoints; prefer it,
        because it carries the exact itinerary the price belongs to. Otherwise
        build the standard search path, which lands on the same route and dates
        but lets Aviasales pick the flight.
        """
        if link_fragment:
            path = link_fragment if link_fragment.startswith("/") else f"/{link_fragment}"
            separator = "&" if "?" in path else "?"
            url = f"https://www.aviasales.com{path}"
        else:
            # Normalized here rather than trusting the caller: this method is
            # public and builds a URL a traveller will open, and Aviasales does
            # not resolve a lowercase route path.
            origin = _validate_iata(origin, "origin")
            destination = _validate_iata(destination, "destination")
            leg = f"{origin}{_ddmm(depart_date)}{destination}"
            if return_date:
                leg += _ddmm(return_date)
            url = f"{_BOOKING_ROOT}/{leg}{max(1, adults)}"
            separator = "?"

        # The marker is how Travelpayouts attributes the referral. Without it
        # the link still works, it just earns nothing.
        if self.marker:
            url = f"{url}{separator}marker={self.marker}"
        return url

    def search(
        self,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: Optional[date] = None,
        adults: int = 1,
        currency: str = "eur",
        limit: int = 10,
    ) -> list[FlightOffer]:
        origin = _validate_iata(origin, "origin")
        destination = _validate_iata(destination, "destination")

        # Month granularity, not an exact day. The cache is populated by what
        # other people actually searched for, so asking for one specific date
        # on one specific route almost always returns nothing - measured as
        # zero fares on every route tried, including London to Rome, while the
        # same route with a month returns plenty. The response carries each
        # fare's real departure date, so precision is not lost, only demanded
        # in the wrong place.
        params = {
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date.strftime("%Y-%m"),
            "currency": currency.lower(),
            "token": self.token,
        }
        if return_date:
            params["return_date"] = return_date.strftime("%Y-%m")

        try:
            response = self._http().get(
                f"{_API_ROOT}/v1/prices/cheap",
                params=params,
                headers={"X-Access-Token": self.token},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # A 400 here is the provider judging the route, and its body says
            # why in words worth showing. Anything else is its problem.
            if exc.response.status_code == 400:
                raise BadRoute(_provider_error(exc.response)) from exc
            raise FlightError(
                f"flight provider returned {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise FlightError(f"could not reach the flight provider: {exc}") from exc
        except ValueError as exc:
            raise FlightError("flight provider returned invalid JSON") from exc

        if not payload.get("success", True):
            raise FlightError(str(payload.get("error") or "flight lookup failed"))

        return self._parse(
            payload, origin, destination, depart_date, return_date, adults,
            currency, limit,
        )

    def _parse(
        self,
        payload: dict,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: Optional[date],
        adults: int,
        currency: str,
        limit: int,
    ) -> list[FlightOffer]:
        """Flatten {"data": {DEST: {"0": {...}, "1": {...}}}} into offers.

        The shape is nested by destination and then by an arbitrary index key,
        and the provider is loose about which fields appear, so every field is
        read defensively - a missing airline is not a reason to drop a fare.
        """
        offers: list[FlightOffer] = []
        by_destination = payload.get("data") or {}
        if not isinstance(by_destination, dict):
            return []

        for dest_code, entries in by_destination.items():
            if not isinstance(entries, dict):
                continue
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                price = entry.get("price")
                if price is None:
                    continue
                try:
                    amount = Decimal(str(price))
                except Exception:
                    continue

                offers.append(
                    FlightOffer(
                        origin=origin,
                        destination=(dest_code or destination).upper(),
                        depart_date=_as_date(entry.get("departure_at"), depart_date),
                        return_date=_as_date(entry.get("return_at"), return_date),
                        price=amount,
                        currency=currency.upper(),
                        airline=entry.get("airline"),
                        flight_number=(
                            str(entry["flight_number"])
                            if entry.get("flight_number") is not None
                            else None
                        ),
                        stops=_as_int(entry.get("transfers")),
                        duration_minutes=_as_int(entry.get("duration")),
                        booking_url=self.booking_url(
                            origin,
                            (dest_code or destination).upper(),
                            depart_date,
                            return_date,
                            adults,
                            entry.get("link") or entry.get("ticket_link"),
                        ),
                        provider=self.name,
                        is_cached=True,
                        raw=dict(entry),
                    )
                )

        offers.sort(key=lambda offer: offer.price)
        return offers[:limit]


def _provider_error(response: httpx.Response) -> str:
    """The provider's own explanation, or a generic one if it did not give a
    parseable body."""
    try:
        message = (response.json() or {}).get("error")
    except ValueError:
        message = None
    return str(message) if message else "that route was rejected by the flight provider"


def _as_date(value: Any, fallback: Optional[date]) -> Any:
    """Provider timestamps are ISO-ish strings; fall back rather than fail."""
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return fallback


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_provider() -> FlightProvider:
    """The configured provider, or one that explains why there isn't one."""
    choice = (os.getenv("FLIGHT_PROVIDER") or "travelpayouts").lower()
    if choice == "null":
        return NullProvider()
    if choice == "travelpayouts":
        try:
            return TravelpayoutsProvider()
        except NotConfigured:
            # Deliberately not fatal: the rest of the app works fine without
            # flight search, and a missing optional key should not stop it.
            log.info("no TRAVELPAYOUTS_TOKEN; flight search disabled")
            return NullProvider()
    raise FlightError(f"unknown FLIGHT_PROVIDER {choice!r}")


def search_flights(
    origin: str,
    destination: str,
    depart_date: date,
    return_date: Optional[date] = None,
    adults: int = 1,
    currency: str = "eur",
    limit: int = 10,
    provider: Optional[FlightProvider] = None,
) -> list[FlightOffer]:
    """Cheapest indicative fares for a route, cheapest first."""
    if depart_date < date.today():
        raise FlightError("depart_date is in the past")
    if return_date and return_date < depart_date:
        raise FlightError("return_date is before depart_date")
    if adults < 1:
        raise FlightError("a flight needs at least one passenger")

    return (provider or get_provider()).search(
        origin=origin,
        destination=destination,
        depart_date=depart_date,
        return_date=return_date,
        adults=adults,
        currency=currency,
        limit=limit,
    )


def _main() -> int:
    """Credential check.

        python flights.py                       # LON -> BCN, 30 days out
        python flights.py LON BCN 2026-09-15

    Exists so "is my token working" is one command rather than a debugging
    session inside the API.
    """
    import argparse
    from datetime import timedelta

    parser = argparse.ArgumentParser(description="Check flight provider credentials.")
    parser.add_argument("origin", nargs="?", default="LON")
    parser.add_argument("destination", nargs="?", default="BCN")
    parser.add_argument("depart", nargs="?", default=None, help="YYYY-MM-DD")
    parser.add_argument("--currency", default="eur")
    args = parser.parse_args()

    when = (
        date.fromisoformat(args.depart)
        if args.depart
        else date.today() + timedelta(days=30)
    )

    provider = get_provider()
    print(f"provider: {provider.name}")
    if isinstance(provider, TravelpayoutsProvider):
        print(f"token:    ...{provider.token[-4:]} ({len(provider.token)} chars)")
        print(f"marker:   {provider.marker or '(not set - links will not earn)'}")
    print(f"route:    {args.origin} -> {args.destination} on {when}\n")

    try:
        offers = search_flights(
            args.origin, args.destination, when, currency=args.currency, limit=5
        )
    except NotConfigured as exc:
        print(f"NOT CONFIGURED: {exc}")
        print("Set TRAVELPAYOUTS_TOKEN in .env - see the module docstring.")
        return 2
    except FlightError as exc:
        print(f"FAILED: {exc}")
        return 1

    if not offers:
        # A valid token with nothing cached for the route is a normal answer,
        # not a failure - saying so avoids a pointless credential hunt.
        print("No cached fares for that route. The token works; try a busier one.")
        return 0

    for offer in offers:
        print(f"  {offer.price:>8} {offer.currency}  {offer.summary()}")
        print(f"           {offer.booking_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
