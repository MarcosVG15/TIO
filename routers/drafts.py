"""Trip drafts, and the three-options screen.

Both of these exist because the rebuilt frontend calls them and nothing served
them: `GET /api/trips/drafts` fell through to `GET /api/trips/{trip_id}` and
came back "trip not found", and `POST /api/trips/suggestions` was a 405.

Registered before `trips.router` in api.py, and that ordering is load-bearing.
FastAPI matches in registration order, so with `/trips/{trip_id}` first the
literal paths here would never be reached - which is precisely the bug this
module fixes.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_type
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

import airports
import budget as budget_model
import embeddings
import itinerary as planner
import recommend
import trip_costing
from DATABASE.ORM import Account, TripDraft, session_scope
from deps import current_account
from routers.trips import _split_destination

log = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["drafts"])

#: Enough to compose from without making the prompt enormous.
POOL_TARGET = 60

#: The browser aborts every request after twelve seconds (AbortSignal.timeout
#: in the bundle), and an aborted request is indistinguishable from a dead
#: server - it shows "Could not connect to the server". So the endpoint must
#: finish inside that, and the only way to guarantee it is to bound each stage
#: rather than hope the total lands under the line. Measured composition alone
#: ranges from 4.1s to 12.6s for identical input.
REQUEST_BUDGET = 10.5

#: Left for costing after composition: flight and hotel lookups, which have
#: their own provider timeouts and run concurrently.
COSTING_RESERVE = 2.0

#: Never give composition less than this - below it, nothing useful returns and
#: we would have spent the pool build for nothing.
MIN_COMPOSE = 4.0

#: suggestion_id -> the plan behind that card, so choosing one does not throw
#: it away. Without this, "Build this itinerary" re-plans from scratch: another
#: half-minute of waiting, and a trip that may not be the trip on the card the
#: traveller actually picked. Bounded and short-lived because it only has to
#: survive the seconds between seeing three options and choosing one.
_CHOSEN: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
CHOSEN_TTL = 1800.0
CHOSEN_MAX = 300


def remember_suggestion(suggestion_id: str, payload: dict[str, Any]) -> None:
    """Keep a composed plan against its card id."""
    _CHOSEN[suggestion_id] = {"at": time.monotonic(), **payload}
    while len(_CHOSEN) > CHOSEN_MAX:
        _CHOSEN.popitem(last=False)


def recall_suggestion(suggestion_id: Optional[str]) -> Optional[dict[str, Any]]:
    """The plan behind a card, or None if it has aged out."""
    if not suggestion_id:
        return None
    entry = _CHOSEN.get(suggestion_id)
    if entry is None:
        return None
    if time.monotonic() - entry["at"] > CHOSEN_TTL:
        _CHOSEN.pop(suggestion_id, None)
        return None
    return entry


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


def _blank_to_none(value: Any) -> Any:
    """Treat an empty string as absent.

    The plan screen initialises its form state to "" rather than null, so an
    untouched date field arrives as an empty string. Pydantic rejects that as
    an invalid date and the whole request 422s before any handler runs - which
    the screen reports as "Could not connect to the server", because it cannot
    render Pydantic's list-shaped detail either. Two bugs stacked into one
    unhelpful message.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value

class DraftIn(BaseModel):
    """Whatever the planning screen currently holds.

    Permissive on purpose: a draft is an unfinished form, so half of it is
    expected to be missing and none of it is worth rejecting. Validation
    belongs at the point the trip is actually created, not here - refusing to
    save a draft because its dates are backwards would lose the user's work to
    protect them from a mistake they have not made yet.
    """

    # Both spellings accepted. The screen builds its form state in camelCase
    # and converts to snake_case for /trips/suggestions - but posts the draft
    # object through unconverted, while the code that reads drafts back expects
    # snake_case. So it writes startDate and reads start_date, and the dates
    # vanish. Accepting either and always returning snake_case fixes it here
    # rather than waiting for a frontend rebuild to fix it there.
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    title: Optional[str] = None
    destination: Optional[str] = None
    start_date: Optional[date_type] = Field(
        default=None, validation_alias=AliasChoices("start_date", "startDate")
    )
    end_date: Optional[date_type] = Field(
        default=None, validation_alias=AliasChoices("end_date", "endDate")
    )
    travellers: Optional[int] = None
    vibe: Optional[str] = None
    notes: Optional[str] = None
    prompt: Optional[str] = None
    budget: Optional[float] = None
    currency: Optional[str] = None
    features: list[str] = Field(default_factory=list)
    companions: list[dict[str, Any]] = Field(default_factory=list)
    group_chat: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("group_chat", "groupChat"),
    )
    #: Present when the screen is updating a draft rather than creating one.
    draft_id: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("draft_id", "draftId", "id")
    )

    _blank = field_validator(
        "start_date", "end_date", "budget", "travellers", mode="before"
    )(_blank_to_none)


def _draft_out(row: TripDraft) -> dict[str, Any]:
    """The stored form, plus the two fields the server owns."""
    payload = dict(row.payload or {})
    payload["draft_id"] = str(row.draft_id)
    payload["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return payload


@router.get("/drafts")
def list_drafts(account: Account = Depends(current_account)) -> dict[str, Any]:
    """Every draft this account has, most recently touched first."""
    with session_scope() as session:
        rows = session.scalars(
            select(TripDraft)
            .where(TripDraft.account_id == account.account_id)
            .order_by(TripDraft.updated_at.desc())
        ).all()
        return {"drafts": [_draft_out(row) for row in rows]}


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
def save_draft(
    payload: DraftIn,
    account: Account = Depends(current_account),
) -> dict[str, Any]:
    """Create a draft, or update one when `draft_id` is supplied.

    Upsert rather than separate create and update endpoints, because the
    screen autosaves: it does not know or care whether this is the first save,
    and giving it two calls to choose between would only invite it to choose
    wrong.
    """
    # mode="json" so dates become ISO strings. The payload goes into JSONB,
    # which cannot serialise a date object - and the screen reads these back
    # as strings anyway.
    body = payload.model_dump(mode="json", exclude_none=False)
    draft_id = body.pop("draft_id", None)
    # extra="allow" keeps the camelCase originals alongside the fields they
    # populated, so drop them: the stored payload is echoed back verbatim and
    # two spellings of the same value is how they drift apart.
    for duplicate in ("startDate", "endDate", "groupChat", "draftId", "id"):
        body.pop(duplicate, None)

    with session_scope() as session:
        row = None
        if draft_id:
            try:
                row = session.scalar(
                    select(TripDraft).where(
                        TripDraft.draft_id == uuid.UUID(str(draft_id)),
                        TripDraft.account_id == account.account_id,
                    )
                )
            except ValueError:
                row = None  # not a uuid: treat as a new draft rather than 400

        if row is None:
            row = TripDraft(account_id=account.account_id, payload=body)
            session.add(row)
        else:
            row.payload = body

        session.flush()
        session.refresh(row)
        return {"draft": _draft_out(row)}


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    draft_id: str,
    account: Account = Depends(current_account),
) -> None:
    """Discard a draft. Idempotent - deleting a gone draft is a success."""
    try:
        target = uuid.UUID(draft_id)
    except ValueError:
        return None

    with session_scope() as session:
        session.execute(
            delete(TripDraft).where(
                TripDraft.draft_id == target,
                TripDraft.account_id == account.account_id,
            )
        )
    return None


# ---------------------------------------------------------------------------
# Three options
# ---------------------------------------------------------------------------



class SuggestionsIn(BaseModel):
    """What the plan screen sends when it asks for options."""

    model_config = ConfigDict(populate_by_name=True)

    destination: str = Field(min_length=2, max_length=200)
    start_date: Optional[date_type] = Field(
        default=None, validation_alias=AliasChoices("start_date", "startDate")
    )
    end_date: Optional[date_type] = Field(
        default=None, validation_alias=AliasChoices("end_date", "endDate")
    )

    _empty_dates = field_validator(
        "start_date", "end_date", "budget", mode="before"
    )(_blank_to_none)
    travellers: int = Field(default=1, ge=1, le=20)
    vibe: Optional[str] = None
    notes: Optional[str] = None
    budget: Optional[float] = None
    currency: str = "EUR"
    title: Optional[str] = None
    prompt: Optional[str] = None
    features: list[str] = Field(default_factory=list)
    companions: list[dict[str, Any]] = Field(default_factory=list)
    #: Regenerate: places already shown, and what was wrong with them. The
    #: button that says "show me different ones" is worth nothing if the next
    #: set is the same set.
    avoid_location_ids: list[str] = Field(default_factory=list)
    feedback: Optional[str] = None

    def days(self) -> int:
        if self.start_date and self.end_date:
            return max(1, (self.end_date - self.start_date).days + 1)
        return 4

    def date_problem(self) -> Optional[str]:
        """A sentence, not a validation error list.

        Same reasoning as TripCreate: Pydantic returns `detail` as a list of
        objects and the frontend renders only strings, so a validator here
        would show the user nothing at all.
        """
        if self.end_date and not self.start_date:
            return "Please choose a start date as well as an end date."
        if not (self.start_date and self.end_date):
            return None
        if self.end_date < self.start_date:
            return "The end date is before the start date."
        if self.start_date < date_type.today():
            return "The start date is in the past - you cannot travel back in time."
        if (self.end_date - self.start_date).days > 40:
            return "That trip is longer than 40 days, which is more than Tio can plan."
        return None


_COMPLEXITY_LABELS = {1: "One city, no moving", 2: "Two cities", 3: "Three cities"}


def _suggestion_out(
    plan: Any,
    *,
    request: SuggestionsIn,
    costed: dict[str, Any],
    pace: Optional[str],
) -> dict[str, Any]:
    """One card, in the shape the screen reads."""
    cities = [c for c in (getattr(plan, "cities", None) or []) if c]
    complexity = max(1, len(set(cities)))

    rows: list[dict[str, Any]] = []
    highlights: list[str] = []
    for day in getattr(plan, "days", []) or []:
        for stop in getattr(day, "stops", []) or []:
            rows.append({
                "day": getattr(day, "day", 1),
                "time": "",
                "part_of_day": getattr(stop, "part_of_day", "") or "",
                "title": getattr(stop, "title", "") or "",
                "description": getattr(stop, "note", "") or "",
            })
            title = getattr(stop, "title", "")
            if title and len(highlights) < 4:
                highlights.append(title)

    budget = costed.get("budget") or {}
    # TIO's cut, derived here rather than invented by the model. Scaled by how
    # many cities the plan strings together - see budget.planning_fee.
    fee = budget_model.planning_fee(
        Decimal(str(budget.get("total") or 0)),
        cities=complexity,
        days=len({d.get("day") for d in rows}) or 1,
    )
    return {
        "suggestion_id": str(uuid.uuid4()),
        "name": getattr(plan, "title", "Option"),
        "tagline": (getattr(plan, "rationale", "") or "")[:180],
        "pace": pace or "balanced",
        "vibe": request.vibe or "",
        "complexity": complexity,
        "complexity_label": _COMPLEXITY_LABELS.get(
            complexity, f"{complexity} cities"
        ),
        # Only ever the computed total. No fee, markup or saving is invented
        # here - see the note in the endpoint docstring.
        "estimated_cost": budget.get("total"),
        "currency": budget.get("currency", request.currency),
        **fee,
        "highlights": highlights,
        "itinerary": rows,
        # Extra, ignored by the current screen but the honest part of the
        # number above: which lines were quoted and which were estimated.
        "budget_detail": budget,
        "flights": costed.get("flights"),
        "accommodation": costed.get("accommodation"),
        "tradeoffs": getattr(plan, "tradeoffs", "") or "",
        "cities": cities,
    }


@router.post("/suggestions")
def trip_suggestions(
    payload: SuggestionsIn,
    account: Account = Depends(current_account),
) -> dict[str, Any]:
    """Three different trips for one destination, each costed.

    Deliberately not returning a fee, markup or "savings" figure even though
    the screen has fields for them. Those are commercial numbers and I do not
    know the rules behind them; a plausible-looking invented fee is the one
    kind of wrong answer a traveller would actually act on. The screen treats
    them as optional, so they are simply absent until someone tells me what
    they should be.
    """
    # Logged before the date check, not after it. With the log line below the
    # check, a request rejected on its dates produced no entry at all - which
    # is indistinguishable from one that never arrived, and sent me looking for
    # a timeout twice.
    log.info(
        "suggestions requested: %r (%s to %s, %d traveller(s), budget %s %s)",
        payload.destination,
        payload.start_date,
        payload.end_date,
        payload.travellers,
        payload.budget,
        payload.currency,
    )

    problem = payload.date_problem()
    if problem:
        log.info("suggestions rejected: %s", problem)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem
        )

    # Stage timings, logged on every call. This endpoint has to fit inside the
    # browser's twelve seconds and has already been slow for two different
    # reasons; without per-stage numbers in the log, diagnosing a third means
    # guessing.
    marks: list[tuple[str, float]] = []
    began = time.monotonic()

    def mark(label: str) -> None:
        marks.append((label, time.monotonic() - began))

    country, city = _split_destination(payload.destination)
    days = payload.days()

    try:
        pool = recommend.build_pool(
            country=country,
            account_ids=[account.account_id],
            target=POOL_TARGET,
            city=city,
            # No model-proposed places here. That is a second LLM round trip,
            # and this endpoint answers a click that the browser abandons after
            # twelve seconds - see the timing note below.
            include_proposals=False,
            notable_only=True,
        )
    except recommend.NoProfile as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except embeddings.SpaceMismatch as exc:
        log.error("embedding space mismatch: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendations are misconfigured on the server.",
        ) from exc
    except recommend.EmptyPool as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nothing to suggest for {payload.destination} yet.",
        ) from exc
    except SQLAlchemyError as exc:
        # Retrieval is bounded by a statement timeout, so this is the ordinary
        # outcome when a filtered vector search cannot use the index - not an
        # exceptional one. Left unhandled it surfaced as a 500, which tells the
        # traveller nothing and tells us nothing either.
        log.warning("suggestions retrieval failed for %s: %s", country, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Tio could not search {country} quickly enough just now. "
                "Try a specific city, or try again in a moment."
            ),
        ) from exc

    mark("pool")

    avoid: list[uuid.UUID] = []
    for raw in payload.avoid_location_ids:
        try:
            avoid.append(uuid.UUID(str(raw)))
        except ValueError:
            continue

    try:
        # Parallel, not the single call: this endpoint answers a button press
        # that the browser abandons after twelve seconds, and one call writing
        # three itineraries takes roughly twice that.
        # Whatever is left of the budget once the pool is built, minus what
        # costing needs. Composition returns the plans that arrived in time
        # rather than waiting for the slowest, so a bad tail costs one option
        # instead of the whole response.
        spent = time.monotonic() - began
        compose_deadline = max(
            MIN_COMPOSE, REQUEST_BUDGET - spent - COSTING_RESERVE
        )
        result = planner.compose_parallel(
            pool=pool,
            days=days,
            avoid=avoid,
            feedback=payload.feedback,
            deadline=compose_deadline,
        )
    except planner.PlanningError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not build plans right now. Try again.",
        ) from exc

    mark("compose")

    traveller = pool.travellers[0] if pool.travellers else None
    home = getattr(traveller, "home_city", None)
    origin = None
    if payload.start_date and home:
        # Corpus only. Resolving an airport through the model costs a round
        # trip per city, and three plans plus an origin is four of them - more
        # than the whole request is allowed to take.
        origin = airports.iata_for_city(home, allow_model=False)

    # Concurrently, because each plan's costing is a handful of network calls -
    # fares, hotel rates - and doing three plans' worth in series took longer
    # than composing them did. They share nothing, so there is nothing to
    # serialise for.
    def cost(plan):
        return trip_costing.cost_plan(
            plan,
            start_date=payload.start_date,
            travellers=payload.travellers,
            budget_tier=getattr(traveller, "budget_tier", None),
            origin_iata=origin,
            country=country,
            by_ref=result.by_ref,
            currency=payload.currency,
            resolve_iata=lambda city, country=None: airports.iata_for_city(
                city, country, allow_model=False
            ),
        )

    with ThreadPoolExecutor(max_workers=max(1, len(result.plans))) as workers:
        costs = list(workers.map(cost, result.plans))

    suggestions = [
        _suggestion_out(
            plan,
            request=payload,
            costed=costed,
            pace=getattr(traveller, "travel_pace", None),
        )
        for plan, costed in zip(result.plans, costs)
    ]

    # Keep each plan against the id its card carries, so choosing one can use
    # the plan the traveller actually saw instead of composing a new one.
    for card, plan, costed in zip(suggestions, result.plans, costs):
        remember_suggestion(card["suggestion_id"], {
            "plan": plan,
            "costed": costed,
            "by_ref": result.by_ref,
            "country": country,
            "days": days,
        })

    mark("cost")
    log.info(
        "suggestions for %s: %s",
        payload.destination,
        " ".join("%s=%.2fs" % (label, at) for label, at in marks),
    )

    return {
        "suggestions": suggestions,
        # So a regenerate can ask for something genuinely different rather
        # than rolling the dice on model temperature.
        "considered_location_ids": [
            str(c.location_id) for c in (result.by_ref or {}).values()
        ],
    }
