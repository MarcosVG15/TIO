"""City to airport code.

Flight search speaks IATA; the planner speaks city names. Nothing in the schema
bridges them, so this does, cheapest source first:

  1. the corpus  - OSM tags aerodromes with `iata`, and the seed keeps every
                   upstream tag in `Location.tags`, so the answer is often
                   already in the database
  2. a language model - reliable for commercial airports, which is the only
                   kind anyone flies into
  3. give up      - a city with no resolvable airport simply gets no fares,
                   which degrades the plan rather than breaking it

Answers are memoized for the life of the process. City-to-airport does not
change, and the alternative is paying for the same lookup on every plan.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text

from DATABASE.ORM import Location, session_scope

load_dotenv()

log = logging.getLogger(__name__)

MODEL_ID = os.getenv("AIRPORT_MODEL", "gpt-4o-mini")

_IATA = re.compile(r"\A[A-Z]{3}\Z")

#: city|country -> code or None. None is cached too: a city with no airport
#: should not be asked about again.
_CACHE: dict[str, Optional[str]] = {}

#: The cities people actually fly from and to, resolved without touching the
#: database or a model. The corpus only knows an airport when OSM happened to
#: tag an aerodrome inside that city's rows, which for somewhere like London is
#: not reliable - and the request path that needs this cannot afford a model
#: round trip, so a miss means no fares at all rather than slow fares. A short
#: table of the obvious answers is worth more here than any amount of
#: cleverness.
_WELL_KNOWN: dict[str, str] = {
    "london": "LON", "paris": "PAR", "madrid": "MAD", "barcelona": "BCN",
    "rome": "ROM", "milan": "MIL", "venice": "VCE", "florence": "FLR",
    "naples": "NAP", "lisbon": "LIS", "porto": "OPO", "amsterdam": "AMS",
    "berlin": "BER", "munich": "MUC", "frankfurt": "FRA", "hamburg": "HAM",
    "vienna": "VIE", "salzburg": "SZG", "innsbruck": "INN", "zurich": "ZRH",
    "geneva": "GVA", "brussels": "BRU", "copenhagen": "CPH", "stockholm": "STO",
    "oslo": "OSL", "helsinki": "HEL", "dublin": "DUB", "edinburgh": "EDI",
    "manchester": "MAN", "prague": "PRG", "budapest": "BUD", "warsaw": "WAW",
    "krakow": "KRK", "athens": "ATH", "istanbul": "IST", "seville": "SVQ",
    "valencia": "VLC", "malaga": "AGP", "palma": "PMI", "ibiza": "IBZ",
    "nice": "NCE", "lyon": "LYS", "marseille": "MRS", "toulouse": "TLS",
    "bordeaux": "BOD", "ljubljana": "LJU", "zagreb": "ZAG", "split": "SPU",
    "dubrovnik": "DBV", "bucharest": "BUH", "sofia": "SOF", "reykjavik": "REK",
    "tokyo": "TYO", "kyoto": "OSA", "osaka": "OSA", "seoul": "SEL",
    "bangkok": "BKK", "singapore": "SIN", "dubai": "DXB", "doha": "DOH",
    "new york": "NYC", "los angeles": "LAX", "chicago": "CHI", "boston": "BOS",
    "san francisco": "SFO", "miami": "MIA", "toronto": "YTO", "montreal": "YMQ",
    "mexico city": "MEX", "rio de janeiro": "RIO", "sao paulo": "SAO",
    "buenos aires": "BUE", "cape town": "CPT", "johannesburg": "JNB",
    "cairo": "CAI", "marrakesh": "RAK", "casablanca": "CAS", "sydney": "SYD",
    "melbourne": "MEL", "auckland": "AKL", "delhi": "DEL", "mumbai": "BOM",
}


class _Answer(BaseModel):
    #: Empty string when there is no commercial airport serving the city.
    iata: str = Field(default="", max_length=3)


def _clean(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    candidate = code.strip().upper()
    return candidate if _IATA.match(candidate) else None


def _from_corpus(city: str, country: Optional[str]) -> Optional[str]:
    """Look for an aerodrome the seed already tagged with an IATA code.

    Matched case-sensitively against `city`, which is what makes this usable.
    `lower(city) = lower(:city)` cannot use the index on `city`, so it becomes
    a sequential scan of the whole locations table - eight million rows for one
    airport code, once per city in a plan. That was the single largest cost in
    the suggestions endpoint and it was invisible on a small test corpus.

    Case-sensitivity is safe here because the caller passes a city name that
    came out of this same table, so it already matches the stored spelling.
    """
    conditions = [
        Location.city == city,
        Location.tags.has_key("iata"),  # noqa: W601 - JSONB ? operator
    ]
    if country:
        conditions.append(
            or_(Location.country == country, Location.region == country)
        )

    try:
        with session_scope() as session:
            # Bounded regardless. If this ever does fall back to a scan - a
            # planner choice, a missing index - it must give up rather than
            # spend the request's entire budget looking for an airport code.
            session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
            row = session.scalar(
                select(Location).where(*conditions).limit(1)
            )
            if row is not None:
                return _clean((row.tags or {}).get("iata"))
    except Exception as exc:
        # A missing column, an odd JSONB value, or the timeout above must not
        # take flight search down with it - the model fallback still works.
        log.debug("corpus airport lookup failed for %s: %s", city, exc)
    return None


def _from_model(city: str, country: Optional[str]) -> Optional[str]:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None

    where = f"{city}, {country}" if country else city
    try:
        completion = OpenAI(api_key=key, max_retries=2).chat.completions.parse(
            model=MODEL_ID,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Give the IATA code of the main commercial airport "
                        "serving a city. Three letters, uppercase. If the city "
                        "has no commercial airport, or you are not confident, "
                        "return an empty string rather than guessing - a wrong "
                        "code sends someone to another country."
                    ),
                },
                {"role": "user", "content": where},
            ],
            response_format=_Answer,
            temperature=0,
        )
        parsed = completion.choices[0].message.parsed
        return _clean(parsed.iata) if parsed else None
    except Exception as exc:
        log.warning("airport lookup failed for %s: %s", where, exc)
        return None


def iata_for_city(
    city: str,
    country: Optional[str] = None,
    allow_model: bool = True,
) -> Optional[str]:
    """The airport serving a city, or None if there is not one we can trust.

    `allow_model=False` restricts the answer to what the corpus already knows.
    The model fallback is accurate but costs a round trip of a second or more,
    and on a request the browser abandons after twelve seconds that is a bad
    trade for a fare line: a suggestion card without a flight price is still a
    good suggestion, whereas one that never arrives is nothing at all.
    """
    city = (city or "").strip()
    if not city:
        return None

    # Already a code - the caller passed "SVQ" rather than "Seville".
    direct = _clean(city)
    if direct and len(city.strip()) == 3:
        return direct

    cache_key = f"{city.lower()}|{(country or '').lower()}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # Before the database: it is free, it is correct, and it is the difference
    # between a suggestion card showing a fare and showing nothing.
    known = _WELL_KNOWN.get(city.casefold())
    if known:
        _CACHE[cache_key] = known
        return known

    code = _from_corpus(city, country)
    if code is None and allow_model:
        code = _from_model(city, country)
    elif code is None:
        # Not cached: a later call that is allowed to ask the model should
        # still get the chance to.
        return None
    _CACHE[cache_key] = code
    if code:
        log.info("resolved %s -> %s", city, code)
    return code
