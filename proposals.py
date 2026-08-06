"""Model-proposed places, grounded against the corpus.

A second candidate source, unioned with vector retrieval rather than replacing
it. The two fail in opposite directions, which is the whole point of running
both:

  vector search   knows what is *in the database* but, while embedding_text is
                  templated, cannot tell a cathedral from a car park
  a language model knows that Seville means the Alcazar and the Plaza de Espana,
                  but will cheerfully invent a museum that does not exist

So the model proposes names and the database decides which are real. Anything
that does not resolve is dropped and counted - the same grounding rule the
itinerary composer uses, applied one stage earlier.

Matching is the hard part. OpenStreetMap stores local names ("Reales Alcazares
de Sevilla"), not the English ones a model reaches for first, so the prompt
asks for the local name and resolution tries several strategies before giving
up. `Location.name_norm` exists for exactly this - the ORM notes it is written
by ingest "so dedupe is a SQL join instead of pulling the table into Python".
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Sequence

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from DATABASE.ORM import Location, session_scope

load_dotenv()

log = logging.getLogger(__name__)

MODEL_ID = os.getenv("PROPOSAL_MODEL", "gpt-4o-mini")

#: How many names to ask for. Generous, because a meaningful share will not
#: resolve and the survivors are what matter.
DEFAULT_COUNT = 24

ROLE = """You name real, specific places a traveller could visit.

You are given a destination and one traveller's profile. Return well-known,
individually-visitable places that exist there.

NAMES

Give the name as it appears locally, in the local language, because these are
matched against OpenStreetMap data. "Reales Alcazares de Sevilla", not "Royal
Palace of Seville". "Museo del Prado", not "Prado Museum". If you are unsure of
the local form, give the most common one.

Give the specific place, never a category or an area: "Mercado de Triana", not
"the markets" and not "the old town".

WHAT TO CHOOSE

A mix, weighted towards this traveller. Include the obvious landmarks a first
visit would want, and some quieter places that suit their stated tastes - but
everything must be a real, named place you are confident exists.

Do not invent. If you are not sure a place is real, leave it out. A shorter
honest list is better than a padded one; the names are checked against a
database and anything unrecognised is discarded.

Spread across the destination rather than clustering on one street."""


class Proposal(BaseModel):
    name: str = Field(max_length=200)
    city: Optional[str] = Field(default=None, max_length=150)
    #: One short line on why it suits this traveller. Carried through to the
    #: card when the place resolves.
    why: str = Field(default="", max_length=200)


class Proposals(BaseModel):
    places: list[Proposal]


@dataclass
class Grounding:
    """What resolution produced, including what it could not find."""

    #: Location ids that matched, with the model's reason for each.
    matched: dict[str, str]
    #: Names the model gave that are not in the corpus. Worth logging: a rising
    #: rate means either the model is drifting or the corpus has a gap.
    unmatched: list[str]

    @property
    def hit_rate(self) -> float:
        total = len(self.matched) + len(self.unmatched)
        return len(self.matched) / total if total else 0.0


def normalize(name: str) -> str:
    """Casefolded, accent-stripped, punctuation-free - the `name_norm` form.

    Reproduced here rather than imported because the seed job that writes
    name_norm lives outside this repo. Resolution never relies on this alone
    for that reason: it is one strategy among several.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", stripped.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def propose(
    country: str,
    city: Optional[str],
    profile: Optional[str],
    styles: Sequence[str] = (),
    count: int = DEFAULT_COUNT,
) -> list[Proposal]:
    """Ask the model for places. Returns [] on any failure - this is an
    additional source, and losing it should degrade the pool, not break it."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return []

    where = f"{city}, {country}" if city else country
    prompt = (
        f"Destination: {where}\n"
        f"Traveller: {profile or 'no profile available'}\n"
        f"Declared styles: {', '.join(styles) if styles else 'none stated'}\n\n"
        f"Name up to {count} places."
    )

    try:
        completion = OpenAI(api_key=key, max_retries=2).chat.completions.parse(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": ROLE},
                {"role": "user", "content": prompt},
            ],
            response_format=Proposals,
            temperature=0.4,
        )
        parsed = completion.choices[0].message.parsed
    except Exception as exc:
        log.warning("place proposal failed, using retrieval alone: %s", exc)
        return []

    return list(parsed.places)[:count] if parsed else []


def _country_clause(country: Optional[str]):
    if not country:
        return None
    return or_(
        func.lower(Location.country) == country.lower(),
        func.lower(Location.region) == country.lower(),
    )


def resolve(
    proposals: Sequence[Proposal],
    country: Optional[str],
    city: Optional[str] = None,
) -> tuple[list[Location], Grounding]:
    """Turn proposed names into real rows. Unrecognised names are dropped.

    Several strategies in order of confidence, stopping at the first hit:

      1. wikipedia_title  - models often produce the article title verbatim
      2. name_norm        - the column ingest wrote for exactly this join
      3. name ILIKE       - covers a name_norm convention that differs from ours
      4. prefix ILIKE     - "Museo del Prado" against "Museo Nacional del Prado"

    Deliberately no fuzzy/trigram stage: without pg_trgm installed it would be a
    sequential scan over 6.8M rows, and with a low threshold it matches the
    wrong place confidently, which is worse than not matching at all.
    """
    matched: dict[str, str] = {}
    unmatched: list[str] = []
    rows: list[Location] = []
    seen: set = set()

    if not proposals:
        return [], Grounding(matched={}, unmatched=[])

    country_clause = _country_clause(country)

    with session_scope() as session:
        for proposal in proposals:
            name = (proposal.name or "").strip()
            if not name:
                continue

            wanted_city = (proposal.city or city or "").strip()
            found: Optional[Location] = None

            for condition in (
                func.lower(Location.wikipedia_title) == name.lower(),
                Location.name_norm == normalize(name),
                func.lower(Location.name) == name.lower(),
                Location.name.ilike(f"%{name}%"),
            ):
                filters = [condition, Location.vec.is_not(None)]
                if country_clause is not None:
                    filters.append(country_clause)
                if wanted_city:
                    filters.append(func.lower(Location.city) == wanted_city.lower())

                found = session.scalar(
                    # Most-linked first: several rows can share a name, and the
                    # one with a wikidata entity is the one people mean.
                    select(Location)
                    .where(*filters)
                    .order_by(
                        Location.wikidata_sitelinks.desc().nullslast(),
                        Location.wikipedia_pageviews.desc().nullslast(),
                    )
                    .limit(1)
                )
                if found is not None:
                    break

            if found is None:
                unmatched.append(name)
                continue
            if found.location_id in seen:
                continue

            seen.add(found.location_id)
            rows.append(found)
            matched[str(found.location_id)] = proposal.why or ""

        # Detach: the caller uses these outside the session.
        for row in rows:
            session.expunge(row)

    grounding = Grounding(matched=matched, unmatched=unmatched)
    log.info(
        "grounded %d/%d proposed places (%.0f%%)",
        len(matched),
        len(matched) + len(unmatched),
        grounding.hit_rate * 100,
    )
    return rows, grounding
