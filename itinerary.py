"""LLM composition of travel plans from a retrieved candidate pool.

Retrieval decides *what is worth considering*; this decides *what makes a trip*.
They are separate because they fail differently. Similarity search cannot tell
that three of its top five are across the country from each other, that a
market is pointless on a Monday, or that a group of five needs one member's
taste to lead on day two. A language model is good at exactly that, and
hopeless at knowing which places exist.

So the model never chooses places, only arranges them. It receives the pool and
must return `location_id`s drawn from it; anything else is dropped on the way
out and counted. A plan that names a restaurant which does not exist is worse
than a plan with one fewer stop, because the user only finds out on the day.

Three plans per request, with deliberately different shapes - one city in depth,
several cities, and one that leads with a theme. Asking for three variations of
the same idea produces three of the same trip; naming the shapes is what makes
the choice a real one. Regeneration reuses the pool and excludes what was shown
before, so a second press does not return the first answer with new adjectives.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Literal, Optional, Sequence
from uuid import UUID

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from recommend import Candidate, Pool, Traveller

load_dotenv()

log = logging.getLogger(__name__)

#: Matches user_extraction.MODEL_ID. Structured outputs are the reason for
#: pinning a dated snapshot rather than a floating alias.
MODEL_ID = os.getenv("PLANNER_MODEL", "gpt-4o-2024-08-06")

#: How much of the pool to show the model. The whole pool is usually fine, but
#: a cap keeps a pathological country from blowing the context window.
MAX_POOL_IN_PROMPT = 60

#: Descriptions are prose paragraphs; the model needs the gist, not all of it.
MAX_BLURB_CHARS = 240

PlanShape = Literal["single_city", "multi_city", "themed"]
PartOfDay = Literal["morning", "afternoon", "evening"]


ROLE = """You are a travel itinerary composer. You arrange places that already
exist into coherent days. You do not converse, greet, or explain yourself - you
emit the structured plan set only.

THE POOL IS THE WORLD

Every stop you place must use a location_id from the supplied pool. You may not
invent a place, and you may not name a place that is not in the pool, not even
as a suggestion in prose. If the pool cannot support a plan of the requested
length, produce a shorter plan and say so in tradeoffs. A plan that is honestly
thin is useful; a plan containing a restaurant that does not exist is not.

THREE DIFFERENT PLANS

Return exactly three, with these shapes, in this order:

1. single_city - one city (or metro area) in depth. Everything within easy
   local travel. This should be the safest, most restful option.
2. multi_city - two or three cities. Split the days between them and account
   for the fact that a travel day is not a sightseeing day.
3. themed - led by one strong idea drawn from the travellers' taste (food,
   architecture, water, nightlife, quiet). It may be one city or several. This
   is allowed to be the boldest of the three.

They must be genuinely different trips, not one trip described three ways. If
the pool only supports one city, say so in that plan's tradeoffs rather than
faking a second.

COHERENT DAYS

A day happens in one place. Do not put a morning in one city and an afternoon
two hours away. Order stops so that morning, afternoon and evening flow
geographically. Respect the travellers' stated pace: a slow pace means two or
three stops a day, packed means four or five. Put meals at meal times and
nightlife in the evening. Do not schedule the same location twice in one plan.

GROUPS

When there is more than one traveller you are given each person's fit score for
each place. Use them. Every member should have at least one stop that is
recognisably for them, and you should say whose taste a day leans on in that
day's summary. Never let one member's taste run the whole trip, even if their
scores are highest.

HONESTY

rationale: why this plan suits these specific travellers. Concrete, grounded in
their profile and the places chosen. No marketing language.

tradeoffs: what this plan gives up compared to the other two. Every plan has a
cost - more travel, less variety, a member less served, a thin evening. Name it
plainly. An empty or evasive tradeoffs field is a failed plan.

CONSTRAINTS YOU CANNOT VERIFY

Dietary restrictions and allergies are listed as constraints. You may suggest
restaurants from the pool, but you must never state or imply that a kitchen is
safe for an allergy. Where a meal matters and suitability is unverified, say in
the note that it needs checking ahead. Do not silently drop meals to avoid the
problem - travellers still have to eat."""


PROMPT = """Plan a trip to {country}.

<trip>
days: {days}
travellers: {traveller_count}
{extra}
</trip>

<travellers>
{travellers_json}
</travellers>

<constraints>
{constraints}
</constraints>

<pool>
{pool_json}
</pool>

The pool is every place available to you. `fit` is cosine similarity per
traveller (higher is better), `group_fit` is the least-satisfied member's score,
and `reserved_for` marks places retrieved specifically because they suit that
traveller."""


class PlannedStop(BaseModel):
    location_id: str = Field(description="Must be a location_id from the pool.")
    part_of_day: PartOfDay
    #: Short label for the timetable row, e.g. "Lunch at the covered market".
    title: str = Field(max_length=120)
    #: Why this stop, for these people, here. One or two sentences.
    note: str = Field(max_length=400)


class PlannedDay(BaseModel):
    day: int = Field(ge=1, description="1-based day number within the plan.")
    city: str
    #: What the day is for, and whose taste it leans on in a group.
    summary: str = Field(max_length=400)
    stops: list[PlannedStop]


class TripPlan(BaseModel):
    title: str = Field(max_length=120)
    shape: PlanShape
    cities: list[str]
    rationale: str = Field(max_length=900)
    tradeoffs: str = Field(max_length=600)
    days: list[PlannedDay]


class PlanSet(BaseModel):
    plans: list[TripPlan]


@dataclass
class PlanningResult:
    """Validated plans, plus what had to be corrected to get them."""

    plans: list[TripPlan]
    #: location_ids the model returned that were not in the pool. Should be
    #: empty; a non-empty list means the prompt is losing its grip.
    hallucinated: list[str]
    #: Stops removed because of the above.
    dropped_stops: int
    model: str

    @property
    def clean(self) -> bool:
        return not self.hallucinated


class PlanningError(Exception):
    """The planner could not produce usable plans."""


def _traveller_payload(traveller: Traveller) -> dict:
    return {
        "id": str(traveller.account_id),
        "name": traveller.name,
        # The paragraph is the taste signal the vectors came from, so it is
        # the most useful thing the model can read about this person.
        "profile": traveller.paragraph,
        "pace": traveller.travel_pace,
        "budget": traveller.budget_tier,
        "styles": list(traveller.travel_styles),
    }


def _candidate_payload(candidate: Candidate, travellers: Sequence[Traveller]) -> dict:
    blurb = (candidate.blurb or "").strip()
    if len(blurb) > MAX_BLURB_CHARS:
        blurb = blurb[:MAX_BLURB_CHARS].rsplit(" ", 1)[0] + "..."

    payload = {
        "location_id": str(candidate.location_id),
        "name": candidate.name,
        "city": candidate.city or candidate.region,
        "category": candidate.category,
        "subcategory": candidate.subcategory,
        "about": blurb or None,
        "group_fit": round(candidate.group_score, 3),
        "fit": {
            traveller.name: round(candidate.scores.get(traveller.account_id, 0.0), 3)
            for traveller in travellers
        },
    }

    # Only include facts that are actually known - a wall of nulls spends
    # tokens telling the model nothing.
    if candidate.price is not None:
        payload["price"] = float(candidate.price)
    if candidate.ticket_ref and candidate.ticket_ref != "none":
        payload["ticket"] = candidate.ticket_ref
    if candidate.wheelchair:
        payload["wheelchair"] = candidate.wheelchair
    if candidate.cuisine:
        payload["cuisine"] = candidate.cuisine
    if candidate.favourite_of:
        by_id = {t.account_id: t.name for t in travellers}
        payload["reserved_for"] = [
            by_id[a] for a in candidate.favourite_of if a in by_id
        ]
    return payload


def _constraints_block(pool: Pool) -> str:
    lines: list[str] = []

    dietary = sorted({need for t in pool.travellers for need in t.dietary})
    if dietary:
        holders = [
            f"{t.name}: {', '.join(sorted(t.dietary))}"
            for t in pool.travellers
            if t.dietary
        ]
        lines.append("Dietary (unverifiable - see your instructions): " + "; ".join(holders))

    access = [
        f"{t.name}: {', '.join(sorted(t.accessibility))}"
        for t in pool.travellers
        if t.accessibility
    ]
    if access:
        lines.append("Accessibility: " + "; ".join(access))
        lines.append(
            "The pool is already filtered to wheelchair-accessible places."
        )

    budgets = sorted({t.budget_tier for t in pool.travellers if t.budget_tier})
    if budgets:
        lines.append("Budget: " + ", ".join(budgets))

    paces = sorted({t.travel_pace for t in pool.travellers if t.travel_pace})
    if paces:
        lines.append(
            "Pace: " + ", ".join(paces)
            + (" (mixed - lean towards the slower)" if len(paces) > 1 else "")
        )

    lines.extend(pool.notes)
    return "\n".join(f"- {line}" for line in lines) if lines else "- none stated"


def _build_prompt(
    pool: Pool,
    days: int,
    avoid: Sequence[UUID],
    feedback: Optional[str],
) -> str:
    avoid_set = set(avoid)
    usable = [c for c in pool.candidates if c.location_id not in avoid_set]
    if not usable:
        raise PlanningError(
            "every candidate was excluded; nothing left to plan with"
        )

    shown = usable[:MAX_POOL_IN_PROMPT]

    extra_lines = []
    if pool.cities:
        top = ", ".join(f"{city} ({count})" for city, count, _ in pool.cities[:6])
        extra_lines.append(f"cities represented in the pool: {top}")
    if avoid_set:
        extra_lines.append(
            f"{len(avoid_set)} place(s) were already shown to this user and have "
            f"been removed from the pool - produce genuinely different plans"
        )
    if feedback:
        extra_lines.append(f"the traveller said: {feedback}")

    return PROMPT.format(
        country=pool.country,
        days=days,
        traveller_count=len(pool.travellers),
        extra="\n".join(extra_lines),
        travellers_json=json.dumps(
            [_traveller_payload(t) for t in pool.travellers], indent=1
        ),
        constraints=_constraints_block(pool),
        pool_json=json.dumps(
            [_candidate_payload(c, pool.travellers) for c in shown], indent=1
        ),
    )


def _validate(
    plan_set: PlanSet,
    pool: Pool,
    days: int,
) -> tuple[list[TripPlan], list[str], int]:
    """Strip anything the model made up; report what was stripped.

    Unknown ids are dropped rather than raising: two good plans and one thin
    one beats an error page. The count is returned so a rising number shows up
    as a prompt problem instead of quietly degrading recommendations.
    """
    known = {str(c.location_id) for c in pool.candidates}
    hallucinated: list[str] = []
    dropped = 0
    kept: list[TripPlan] = []

    for plan in plan_set.plans:
        clean_days: list[PlannedDay] = []
        for day in plan.days:
            if day.day > days:
                # Over-length is a spec violation, not a hallucination.
                dropped += len(day.stops)
                continue
            stops = []
            for stop in day.stops:
                if stop.location_id in known:
                    stops.append(stop)
                else:
                    hallucinated.append(stop.location_id)
                    dropped += 1
            if stops:
                clean_days.append(day.model_copy(update={"stops": stops}))
        if clean_days:
            kept.append(plan.model_copy(update={"days": clean_days}))

    if not kept:
        raise PlanningError(
            "no plan survived validation - every stop referenced a place that "
            "is not in the pool"
        )
    return kept, hallucinated, dropped


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise PlanningError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=key, max_retries=3)
    return _client


def compose(
    pool: Pool,
    days: int,
    avoid: Sequence[UUID] = (),
    feedback: Optional[str] = None,
    temperature: float = 0.7,
) -> PlanningResult:
    """Three plans for one pool.

    `avoid` and `feedback` are the regeneration path: pass the location_ids the
    user has already been shown, and optionally what they said about them, and
    the model works from what is left. Temperature is above zero on purpose -
    at zero, regeneration returns the same trip.
    """
    if days < 1:
        raise ValueError("a trip needs at least one day")

    prompt = _build_prompt(pool, days, avoid, feedback)

    try:
        completion = _get_client().chat.completions.parse(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": ROLE},
                {"role": "user", "content": prompt},
            ],
            response_format=PlanSet,
            temperature=temperature,
        )
    except Exception as exc:
        raise PlanningError(f"the planner call failed: {exc}") from exc

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise PlanningError("the planner returned no structured output")

    plans, hallucinated, dropped = _validate(parsed, pool, days)

    if hallucinated:
        log.warning(
            "planner invented %d location id(s) for %s: %s",
            len(hallucinated),
            pool.country,
            hallucinated[:5],
        )

    return PlanningResult(
        plans=plans,
        hallucinated=hallucinated,
        dropped_stops=dropped,
        model=MODEL_ID,
    )
