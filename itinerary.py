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
from concurrent.futures import ThreadPoolExecutor, wait
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

#: How much of the pool to show the model.
#:
#: Sized by the token budget, not the context window. Composition runs as three
#: concurrent calls, so the pool is sent three times - and the account's limit
#: is 30,000 tokens per minute, which at the old cap of 60 allowed fewer than
#: two "generate" presses a minute before every call started returning 429.
#: A 429 costs far more than a thin pool: the client backs off several seconds
#: and the browser has already given up.
#:
#: Twenty-eight is still four or five choices per day for a week-long trip,
#: which is more than any plan uses.
MAX_POOL_IN_PROMPT = 28

#: Descriptions are prose paragraphs; the model needs the gist, not all of it.
#: Halved for the same reason as the pool cap - this is per candidate, so it is
#: multiplied by the pool size and then by three.
MAX_BLURB_CHARS = 110

PlanShape = Literal["single_city", "multi_city", "themed"]
PartOfDay = Literal["morning", "afternoon", "evening"]


ROLE = """You are a travel itinerary composer. You arrange places that already
exist into coherent days. You do not converse, greet, or explain yourself - you
emit the structured plan set only.

LANGUAGE

Write every title, summary, note, rationale and tradeoff in English, whatever
language the place names are in. Keep each place's own name exactly as the pool
gives it - "Findenigkofel / Monte Lodin" is what the signs say and what a map
will show - but everything you write around it is English prose.

THE POOL IS THE WORLD

Every stop you place must use a `ref` number from the supplied pool, copied
exactly. Refs are small integers - use the number, not the name. You may not
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

GETTING THERE

You may be given the cheapest recent fare to each candidate city. Where two
cities suit the traveller similarly, prefer the cheaper one and say so in the
tradeoffs. Never state a fare as a guaranteed price, and never invent one for a
city you were not given.

When fares are supplied, treat the first and last day as travel days: arrival
eats a morning and departure eats an afternoon, so do not fill them as if the
traveller were already there.

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


#: Sliced out of ROLE for the single-plan calls. Kept as a constant so the
#: two never drift: if the section is reworded, this still removes it.
_THREE_PLANS_BLOCK = ROLE[ROLE.index("THREE DIFFERENT PLANS"):ROLE.index("COHERENT DAYS")]

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

<getting_there>
{flights}
</getting_there>

<pool>
{pool_json}
</pool>

The pool is every place available to you. Use the `ref` number to refer to a
place. `fit` is cosine similarity per traveller (higher is better), `group_fit`
is the least-satisfied member's score, and `reserved_for` marks places retrieved
specifically because they suit that traveller."""


class PlannedStop(BaseModel):
    #: The pool entry's `ref` number. An integer rather than a UUID because a
    #: model copies a small number reliably and a 36-character identifier does
    #: not - see the module note on why a stripped plan is the worst outcome.
    ref: int = Field(description="A ref number from the pool.")
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
    #: ref -> Candidate, for the ordering the model was actually shown. Callers
    #: resolve stops through this rather than guessing.
    by_ref: dict = None

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


def _candidate_payload(
    candidate: Candidate, travellers: Sequence[Traveller], ref: int
) -> dict:
    blurb = (candidate.blurb or "").strip()
    if len(blurb) > MAX_BLURB_CHARS:
        blurb = blurb[:MAX_BLURB_CHARS].rsplit(" ", 1)[0] + "..."

    payload = {
        "ref": ref,
        "name": candidate.name,
        "city": candidate.city or candidate.region,
        "category": candidate.category,
        "subcategory": candidate.subcategory,
        "about": blurb or None,
        "group_fit": round(candidate.group_score, 3),
    }

    # Per-member scores only when there is more than one member. For a solo
    # traveller `fit` is `group_fit` repeated under their name, and the pool is
    # sent three times a request against a tight token ceiling.
    if len(travellers) > 1:
        payload["fit"] = {
            traveller.name: round(candidate.scores.get(traveller.account_id, 0.0), 3)
            for traveller in travellers
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
    flights: str = "",
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

    prompt = PROMPT.format(
        country=pool.country,
        days=days,
        traveller_count=len(pool.travellers),
        extra="\n".join(extra_lines),
        travellers_json=json.dumps(
            [_traveller_payload(t) for t in pool.travellers], indent=1
        ),
        constraints=_constraints_block(pool),
        flights=flights or "No flight information was checked.",
        pool_json=json.dumps(
            [
                _candidate_payload(c, pool.travellers, index + 1)
                for index, c in enumerate(shown)
            ],
            # Compact. Pretty-printing spends roughly a fifth of the pool's
            # tokens on whitespace the model does not read, and the pool is
            # sent once per concurrent call.
            separators=(",", ":"),
        ),
    )
    # The validator needs the exact ordering the model was shown, or a ref
    # would resolve to a different place than the one it chose.
    return prompt, shown


def _validate(
    plan_set: PlanSet,
    shown: Sequence[Candidate],
    days: int,
) -> tuple[list[TripPlan], list[str], int]:
    """Resolve refs to real places; strip anything that does not resolve.

    Out-of-range refs are dropped rather than raising: two good plans and one
    thin one beats an error page. The count is returned so a rising number
    shows up as a prompt problem instead of quietly degrading plans.
    """
    known = {index + 1 for index in range(len(shown))}
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
                if stop.ref in known:
                    stops.append(stop)
                else:
                    hallucinated.append(str(stop.ref))
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
    """The shared client, with a timeout that fits the caller's budget.

    Both settings here were actively harmful before. The default timeout is ten
    minutes, so a hung connection held a request open long past any point the
    browser was still listening; and `max_retries=3` meant one transient failure
    silently spent three times the latency budget before returning anything.
    On a path the browser abandons after twelve seconds, a retry is not
    resilience - it is a guaranteed failure that costs more.

    One retry, bounded. If the first call and one retry both miss the deadline,
    the right answer is two plans instead of three, not a third attempt.
    """
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise PlanningError("OPENAI_API_KEY is not set")
        _client = OpenAI(
            api_key=key,
            max_retries=1,
            timeout=float(os.getenv("PLANNER_TIMEOUT", "20")),
        )
    return _client


#: One instruction per shape, for the parallel path. The wording mirrors the
#: THREE DIFFERENT PLANS section of the prompt, because a plan built by a call
#: that was told something slightly different is a plan that does not match the
#: set it is shown in.
_SHAPE_BRIEFS: dict[str, str] = {
    "single_city": (
        "one city (or metro area) in depth, everything within easy local "
        "travel. The safest, most restful of the options."
    ),
    "multi_city": (
        "two or three cities. Split the days between them and account for the "
        "fact that a travel day is not a sightseeing day."
    ),
    "themed": (
        "led by one strong idea drawn from the travellers' taste (food, "
        "architecture, water, nightlife, quiet). One city or several. This one "
        "is allowed to be the boldest."
    ),
}


#: The system prompt with the three-plans section removed. Each parallel call
#: produces ONE plan and is told so explicitly, so shipping instructions about
#: returning three is both confusing and - sent three times a request against a
#: 30,000 token-per-minute ceiling - expensive.
ROLE_SINGLE = ROLE.replace(_THREE_PLANS_BLOCK, "")


def _one_plan(prompt: str, shape: str, temperature: float) -> Optional[TripPlan]:
    """Ask for a single plan of one shape. Returns None if that call fails."""
    brief = _SHAPE_BRIEFS.get(shape, "")
    instruction = (
        f"{prompt}\n\nRETURN ONE PLAN ONLY\n\n"
        f"Ignore the instruction above to return three. Return exactly one "
        f"plan, with shape set to \"{shape}\": {brief}"
    )
    try:
        completion = _get_client().chat.completions.parse(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": ROLE_SINGLE},
                {"role": "user", "content": instruction},
            ],
            response_format=TripPlan,
            temperature=temperature,
        )
        # Inside the try on purpose. An empty `choices` array - upstream
        # truncation, a filtered response - raises IndexError here, and with
        # this outside the guard it escaped the worker thread and turned one
        # survivable shape failure into a failed request.
        return completion.choices[0].message.parsed
    except Exception as exc:  # noqa: BLE001 - one shape failing is survivable
        log.warning("planner call for shape %s failed: %s", shape, exc)
        return None


def compose_parallel(
    pool: Pool,
    days: int,
    avoid: Sequence[UUID] = (),
    feedback: Optional[str] = None,
    temperature: float = 0.7,
    flights: str = "",
    shapes: Sequence[str] = ("single_city", "multi_city", "themed"),
    deadline: Optional[float] = None,
) -> PlanningResult:
    """The same three plans, as three concurrent calls instead of one.

    Wall-clock is what forces this. One call producing three full itineraries
    writes three times as many tokens as one producing a single itinerary, and
    output tokens are almost all of the latency - so the single call lands
    around twenty-five seconds while the browser abandons the request at
    twelve. Three calls in parallel finish in roughly the time of the slowest
    one.

    It also degrades better. If one shape fails or comes back unusable the
    other two are still real plans, whereas a single call failing leaves
    nothing at all. Two good options beat an error page.
    """
    if days < 1:
        raise ValueError("a trip needs at least one day")

    prompt, shown = _build_prompt(pool, days, avoid, feedback, flights)

    # Wait for what arrives in time, not for all of it. Measured composition
    # varies from 4s to 12.6s for the same input - the mean is comfortable and
    # the tail is not - so waiting for the slowest of three calls is a coin
    # flip against a browser that gives up at twelve seconds. Returning two
    # good plans is a far better outcome than returning nothing, and the
    # traveller cannot tell that a third was ever intended.
    executor = ThreadPoolExecutor(max_workers=len(shapes))
    try:
        futures = [
            executor.submit(_one_plan, prompt, shape, temperature)
            for shape in shapes
        ]
        finished, unfinished = wait(futures, timeout=deadline)
        for late in unfinished:
            late.cancel()
        if unfinished:
            log.warning(
                "composition deadline hit: %d of %d plan(s) arrived in %.1fs",
                len(finished), len(futures), deadline or 0.0,
            )
    finally:
        # wait=False so a call still in flight cannot hold the response open;
        # its own client timeout ends it. Blocking here would undo the deadline.
        executor.shutdown(wait=False, cancel_futures=True)

    results = []
    for future in futures:
        if future not in finished:
            continue
        try:
            results.append(future.result())
        except Exception as exc:  # noqa: BLE001 - one shape failing is survivable
            log.warning("a planner call raised: %s", exc)

    produced = [plan for plan in results if plan is not None]
    if not produced:
        raise PlanningError("every planner call failed")

    plans, hallucinated, dropped = _validate(PlanSet(plans=produced), shown, days)

    if hallucinated:
        log.warning(
            "planner invented %d location ref(s) for %s: %s",
            len(hallucinated),
            pool.country,
            hallucinated[:5],
        )

    return PlanningResult(
        plans=plans,
        hallucinated=hallucinated,
        dropped_stops=dropped,
        model=MODEL_ID,
        by_ref={index + 1: candidate for index, candidate in enumerate(shown)},
    )


def compose(
    pool: Pool,
    days: int,
    avoid: Sequence[UUID] = (),
    feedback: Optional[str] = None,
    temperature: float = 0.7,
    flights: str = "",
) -> PlanningResult:
    """Three plans for one pool.

    `avoid` and `feedback` are the regeneration path: pass the location_ids the
    user has already been shown, and optionally what they said about them, and
    the model works from what is left. Temperature is above zero on purpose -
    at zero, regeneration returns the same trip.
    """
    if days < 1:
        raise ValueError("a trip needs at least one day")

    prompt, shown = _build_prompt(pool, days, avoid, feedback, flights)

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

    plans, hallucinated, dropped = _validate(parsed, shown, days)

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
        by_ref={index + 1: candidate for index, candidate in enumerate(shown)},
    )
