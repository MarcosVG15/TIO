"""Trip suggestions.

One POST produces three whole trips. The work splits in two: `recommend` decides
which real places are worth considering and how to be fair to a group, and
`itinerary` arranges a subset of them into days. This router is only glue and
error translation - it holds no planning logic of its own.

Nothing here writes to the database. A suggestion is a proposal; it becomes
durable when the user saves one as a Trip.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

import embeddings
import itinerary as planner
import recommend
from DATABASE.ORM import Account, GroupMember, session_scope
from deps import current_account
from schemas import (
    LocationOut,
    PlannedDayOut,
    PlannedStopOut,
    SuggestRequest,
    SuggestResponse,
    TripPlanOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/planner", tags=["planner"])

#: How many places to hand the planner. Enough for three distinct plans over a
#: fortnight without spending the context window on places it will not use.
POOL_TARGET = 45


def _resolve_travellers(group_id: str | None, account: Account) -> list[UUID]:
    """Whose taste the plan has to satisfy.

    Membership is checked rather than trusted: without it, anyone could plan
    against a stranger's profile by passing their group id, which leaks taste
    and location data.
    """
    if not group_id:
        return [account.account_id]

    try:
        group_uuid = UUID(group_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="group not found"
        )

    with session_scope() as session:
        members = list(
            session.scalars(
                select(GroupMember.account_id).where(
                    GroupMember.group_id == group_uuid
                )
            ).all()
        )

    if account.account_id not in members:
        # Same 404 as a missing group, so this cannot be used to discover which
        # group ids exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="group not found"
        )

    # Caller first, so they are the reference traveller in the prompt.
    return [account.account_id] + [m for m in members if m != account.account_id]


def _parse_avoid(raw: list[str]) -> list[UUID]:
    """Ignore unparseable ids rather than rejecting the request.

    These come back from a previous response, so a malformed one is a client
    bug; failing the whole regeneration over it would strand the user on a
    screen with a dead button.
    """
    out: list[UUID] = []
    for value in raw:
        try:
            out.append(UUID(value))
        except ValueError:
            continue
    return out


@router.post("/suggest", response_model=SuggestResponse)
def suggest(
    payload: SuggestRequest,
    account: Account = Depends(current_account),
) -> SuggestResponse:
    """Three different trips for a country.

    Pass `avoid_location_ids` (from a previous response's
    `considered_location_ids`) to regenerate without repeating yourself, and
    `feedback` to say what was wrong with the last set.
    """
    # Dates first, and as a plain sentence. A Pydantic validator would return
    # `detail` as a list of error objects, which the frontend cannot render -
    # the user would see nothing at all rather than a red line telling them
    # what is wrong.
    problem = payload.date_problem()
    if problem:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem
        )

    account_ids = _resolve_travellers(payload.group_id, account)

    # A city has to be in the country the caller named. Checked against the
    # corpus rather than a gazetteer, because the corpus is what the plan will
    # be built from - a city that is real but unrepresented is just as unusable
    # as one in the wrong country.
    if payload.city and not recommend.city_in_country(payload.city, payload.country):
        available = [name for name, _ in recommend.list_cities(payload.country)][:12]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{payload.city} is not a city we can plan for in "
                f"{payload.country}."
                + (f" Try: {', '.join(available)}." if available else "")
            ),
        )

    try:
        pool = recommend.build_pool(
            country=payload.country,
            account_ids=account_ids,
            target=POOL_TARGET,
            city=payload.city,
            # Two candidate sources. Vector search knows the corpus; a model
            # knows which places people have heard of. The planner needs both,
            # because a plan full of obscure rows is the failure users notice.
            include_proposals=True,
            notable_only=True,
        )
    except recommend.NoProfile as exc:
        # 409, not 404: the account exists, it is just not ready to be planned
        # for. The frontend routes this to "finish onboarding".
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except embeddings.SpaceMismatch as exc:
        # A configuration fault, not a user error. Loud on purpose - the
        # alternative is silently ranking against meaningless distances.
        log.error("embedding space mismatch: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendations are misconfigured on the server.",
        ) from exc
    except recommend.EmptyPool as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nothing to suggest in {payload.country} yet.",
        ) from exc

    by_id = {str(c.location_id): c for c in pool.candidates}

    try:
        result = planner.compose(
            pool=pool,
            days=payload.days,
            avoid=_parse_avoid(payload.avoid_location_ids),
            feedback=payload.feedback,
        )
    except planner.PlanningError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not build a plan right now. Try again.",
        ) from exc

    if not result.clean:
        # Already stripped from the response; recorded so a rising rate shows
        # up as a prompt regression rather than as vague user complaints.
        log.warning(
            "planner invented %d location(s) for %s",
            len(result.hallucinated),
            payload.country,
        )

    names = [t.name for t in pool.travellers]
    plans = [
        TripPlanOut(
            title=plan.title,
            shape=plan.shape,
            cities=plan.cities,
            rationale=plan.rationale,
            tradeoffs=plan.tradeoffs,
            days=[
                PlannedDayOut(
                    day=day.day,
                    city=day.city,
                    summary=day.summary,
                    stops=[
                        _stop_out(by_id[stop.location_id], stop, pool.travellers)
                        for stop in day.stops
                        if stop.location_id in by_id
                    ],
                )
                for day in plan.days
            ],
        )
        for plan in result.plans
    ]

    return SuggestResponse(
        country=pool.country,
        days=payload.days,
        travellers=names,
        plans=plans,
        notes=pool.notes,
        # Everything the planner could have used, not just what it chose, so a
        # regenerate actually explores new ground.
        considered_location_ids=list(by_id),
    )


def _stop_out(
    candidate: recommend.Candidate,
    stop: planner.PlannedStop,
    travellers: list[recommend.Traveller],
) -> PlannedStopOut:
    by_name: dict[str, float] = {}
    if len(travellers) > 1:
        # Only meaningful for a group - for one person it is the same number on
        # every row, and the UI has nothing to compare it against.
        by_name = {
            traveller.name: round(candidate.scores.get(traveller.account_id, 0.0), 3)
            for traveller in travellers
        }

    return PlannedStopOut(
        location=LocationOut(
            location_id=str(candidate.location_id),
            name=candidate.name,
            country=candidate.country,
            city=candidate.city or candidate.region,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
        ),
        part_of_day=stop.part_of_day,
        title=stop.title,
        note=stop.note,
        category=candidate.category,
        fit=by_name,
    )
