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
from sqlalchemy import func, or_, select

from DATABASE.ORM import Location, session_scope

load_dotenv()

log = logging.getLogger(__name__)

MODEL_ID = os.getenv("AIRPORT_MODEL", "gpt-4o-mini")

_IATA = re.compile(r"\A[A-Z]{3}\Z")

#: city|country -> code or None. None is cached too: a city with no airport
#: should not be asked about again.
_CACHE: dict[str, Optional[str]] = {}


class _Answer(BaseModel):
    #: Empty string when there is no commercial airport serving the city.
    iata: str = Field(default="", max_length=3)


def _clean(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    candidate = code.strip().upper()
    return candidate if _IATA.match(candidate) else None


def _from_corpus(city: str, country: Optional[str]) -> Optional[str]:
    """Look for an aerodrome the seed already tagged with an IATA code."""
    conditions = [
        func.lower(Location.city) == city.lower(),
        Location.tags.has_key("iata"),  # noqa: W601 - JSONB ? operator
    ]
    if country:
        conditions.append(
            or_(
                func.lower(Location.country) == country.lower(),
                func.lower(Location.region) == country.lower(),
            )
        )

    try:
        with session_scope() as session:
            row = session.scalar(
                select(Location).where(*conditions).limit(1)
            )
            if row is not None:
                return _clean((row.tags or {}).get("iata"))
    except Exception as exc:
        # A missing column or an odd JSONB value must not take flight search
        # down with it - the model fallback still works.
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


def iata_for_city(city: str, country: Optional[str] = None) -> Optional[str]:
    """The airport serving a city, or None if there is not one we can trust."""
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

    code = _from_corpus(city, country) or _from_model(city, country)
    _CACHE[cache_key] = code
    if code:
        log.info("resolved %s -> %s", city, code)
    return code
