"""Every request and response shape the API exposes.

This file is the frontend contract. FastAPI turns it into an OpenAPI schema
at /openapi.json, which can be compiled straight into TypeScript types.
"""

from __future__ import annotations

from datetime import date, datetime, time

# Aliases for the itinerary fields whose names shadow their own types. With
# `from __future__ import annotations`, `date: Optional[date] = None` resolves
# the annotation against the class namespace - where `date` is now the field's
# default, None - so the field silently becomes NoneType and rejects every real
# date. The alias breaks the shadowing. The wire name is unaffected.
from datetime import date as date_type, time as time_type
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

import passwords
from DATABASE.ORM import ActivityType, BookingStatus, TripStatus

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class GoogleSignInRequest(BaseModel):
    id_token: str


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=passwords.MIN_LENGTH, max_length=passwords.MAX_LENGTH
    )
    name: str = Field(min_length=1, max_length=120)
    surname: Optional[str] = Field(default=None, max_length=120)


class SignInRequest(BaseModel):
    email: EmailStr
    # No min_length here - rejecting a short password before checking it would
    # tell an attacker the length rule, and the answer is "wrong" either way.
    password: str = Field(min_length=1, max_length=passwords.MAX_LENGTH)


class AccountOut(BaseModel):
    account_id: str
    email: str
    name: str
    surname: Optional[str] = None
    auth_provider: str
    bio: Optional[str] = None
    avatar_url: Optional[str] = None


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    account: AccountOut


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


class OnboardingRequest(BaseModel):
    """What the questionnaire screen posts.

    questionnaire is a list of {"question": ..., "answer": ...} - an array,
    not an object. conversation is the same content rendered as a transcript
    of {"role", "content"} turns.

    Both are typed loosely on purpose: the frontend is generated, and a strict
    shape here turns a small wording change into a 422 the user sees.
    """

    questionnaire: list[dict[str, Any]] = Field(default_factory=list)
    conversation: list[dict[str, Any]] = Field(default_factory=list)


class OnboardingOut(BaseModel):
    account_id: str
    personality_id: str
    vector_pending: bool
    #: Read by the questionnaire screen when it finishes.
    onboarding_completed: bool = True
    profile_summary: Optional[str] = None


class OnboardingStatusOut(BaseModel):
    #: The routing gate the frontend reads. False sends the user into the
    #: onboarding flow, true sends them to the home screen.
    onboarding_completed: bool
    #: Whether a Personality row actually exists.
    has_profile: bool
    embedding_status: Optional[str] = None
    #: Whether the personality vector has been computed - recommendations are
    #: weak until this is true.
    ready: bool


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class Preferences(BaseModel):
    push_notifications: bool = True
    public_profile: bool = True


class ProfileOut(BaseModel):
    """Everything the profile screen renders."""

    account: AccountOut
    onboarding_completed: bool
    preferences: Preferences
    #: The raw question/answer pairs, echoed back so they can be reviewed.
    questionnaire: list[dict[str, Any]] = Field(default_factory=list)
    profile_summary: Optional[str] = None
    dietary_restriction: dict[str, Any] = Field(default_factory=dict)
    accessibility_needs: dict[str, Any] = Field(default_factory=dict)
    preferred_language: list[str] = Field(default_factory=list)
    embedding_status: Optional[str] = None
    ready: bool = False
    #: Set once the account is deactivated.
    deactivated_at: Optional[datetime] = None


class ProfileUpdate(BaseModel):
    """One PATCH endpoint serves three different frontend actions.

    Every field is optional and unset fields are left alone, so the caller
    only sends what it is changing:
      - editing details      {"name": ..., "surname": ...}
      - resetting onboarding {"questionnaire": null, "onboarding_completed": false}
      - deactivating         {"is_active": false, "deactivation_reason": ...}
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    surname: Optional[str] = Field(default=None, max_length=120)
    #: Explicit null clears it.
    bio: Optional[str] = Field(default=None, max_length=300)
    avatar_url: Optional[str] = Field(
        default=None, max_length=2048, validation_alias="avatarUrl"
    )

    push_notifications: Optional[bool] = None
    public_profile: Optional[bool] = None
    preferences: Optional[Preferences] = None

    #: Explicit null means "wipe my answers and send me back to onboarding".
    questionnaire: Optional[list[dict[str, Any]]] = None
    onboarding_completed: Optional[bool] = None

    #: false deactivates. Reactivating happens by signing in again.
    is_active: Optional[bool] = None
    deactivation_reason: Optional[str] = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Chat
#
# These use camelCase on the wire, unlike the snake_case auth and profile
# endpoints - that is what the chat screen reads. Aliases keep the Python
# side conventional; FastAPI serialises by alias.
# ---------------------------------------------------------------------------


class MemberOut(BaseModel):
    id: str
    name: str


class MessageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    body: str
    author_name: str = Field(serialization_alias="authorName")
    #: True when the caller sent it - drives which side of the thread it
    #: renders on, so it is per-viewer, not a property of the message.
    mine: bool
    created_at: datetime = Field(serialization_alias="createdAt")


class ConversationOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    is_group: bool = Field(serialization_alias="isGroup")
    members: list[MemberOut] = Field(default_factory=list)
    last_message: Optional[str] = Field(default=None, serialization_alias="lastMessage")
    unread_count: int = Field(default=0, serialization_alias="unreadCount")


class ConversationListOut(BaseModel):
    """Wrapped, not a bare array - the chat screen reads `data.conversations`
    and would throw on a plain list."""

    conversations: list[ConversationOut] = Field(default_factory=list)


class MessageListOut(BaseModel):
    """Wrapped for the same reason: the screen reads `data.messages`."""

    messages: list[MessageOut] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(default=None, max_length=150)
    is_group: bool = Field(default=False, validation_alias="isGroup")
    member_ids: list[str] = Field(
        default_factory=list, validation_alias="memberIds"
    )


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class MembersAdd(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_ids: list[str] = Field(validation_alias="memberIds")


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


class LocationOut(BaseModel):
    location_id: str
    name: str
    country: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    picture: Optional[str] = None


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------


class GroupChatRequest(BaseModel):
    create: bool = False
    name: Optional[str] = Field(default=None, max_length=150)


class TripCreate(BaseModel):
    """What the trip builder posts.

    Field names follow the frontend, not the ORM, because this is the contract
    the screen already speaks: it sends `destination` and an optional `title`,
    never `name`. Requiring `name` here made every "Generate itinerary" press
    fail with a 422 the user could not read.
    """

    #: Where they want to go. Free text - "Kyoto, Japan", "Spain".
    destination: str = Field(min_length=2, max_length=200)
    #: What to call the trip. Falls back to the destination.
    title: Optional[str] = Field(default=None, max_length=200)
    start_date: Optional[date_type] = None
    end_date: Optional[date_type] = None
    #: Free text describing the trip, as typed to a friend.
    prompt: Optional[str] = Field(default=None, max_length=2000)
    notes: Optional[str] = Field(default=None, max_length=2000)
    vibe: Optional[str] = Field(default=None, max_length=200)
    travellers: Optional[int] = Field(default=None, ge=1, le=20)
    #: Account ids travelling together.
    companions: list[str] = Field(default_factory=list, max_length=20)
    #: UI feature toggles, carried through untouched.
    features: list[str] = Field(default_factory=list, max_length=30)
    group_chat: Optional[GroupChatRequest] = None

    # Kept for API callers that speak the ORM's language rather than the
    # screen's. Neither is required.
    origin_location_id: Optional[str] = None
    budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    group_id: Optional[str] = None

    @property
    def trip_name(self) -> str:
        return (self.title or self.destination).strip()[:200]

    def date_problem(self) -> Optional[str]:
        """Why these dates cannot describe a trip being planned, or None.

        Stricter than the trip *record* needs to be: this endpoint plans a
        trip, and you cannot plan one into the past. Written as a sentence
        because the screen shows `detail` verbatim.
        """
        today = date_type.today()

        if self.end_date and not self.start_date:
            return "Pick a start date as well as an end date."
        if self.start_date and self.start_date < today:
            return "That start date has already passed - you cannot plan a trip backwards."
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                return "That trip ends before it starts - check the dates."
            span = (self.end_date - self.start_date).days + 1
            if span > MAX_TRIP_DAYS:
                return (
                    f"That is a {span}-day trip. Tio plans up to "
                    f"{MAX_TRIP_DAYS} days at a time."
                )
        if self.start_date and (self.start_date - today).days > MAX_DAYS_AHEAD:
            return "That start date is more than two years away."
        return None

    @property
    def days(self) -> Optional[int]:
        if self.start_date and self.end_date and self.end_date >= self.start_date:
            return (self.end_date - self.start_date).days + 1
        return None


class TripUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    status: Optional[TripStatus] = None


class PlannedItemOut(BaseModel):
    """One row of a generated day-by-day plan.

    Shaped for the trip screen, which reads day / part_of_day / title rather
    than the ORM's date / time / activity_type.
    """

    itinerary_id: str
    day: int
    part_of_day: str
    time: Optional[str] = None
    title: str
    description: Optional[str] = None
    completed: bool = False
    location: Optional[LocationOut] = None


class TripOut(BaseModel):
    trip_id: str
    name: str
    status: TripStatus
    is_group: bool
    start_date: Optional[date_type] = None
    end_date: Optional[date_type] = None
    budget_limit: Optional[Decimal] = None
    origin_location: Optional[LocationOut] = None
    group_id: Optional[str] = None
    #: Present on creation, when the plan was generated with the trip. The
    #: screen reads `trip.itinerary` straight after posting.
    itinerary: list[PlannedItemOut] = Field(default_factory=list)
    #: Cheapest indicative fares found for the destination, if any.
    flights: list["FlightSuggestionOut"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------


class ItineraryItemCreate(BaseModel):
    location_id: Optional[str] = None
    date: Optional[date_type] = None
    time: Optional[time_type] = None
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    activity_type: ActivityType = ActivityType.OTHER
    weather_dependent: bool = False
    cost: Optional[Decimal] = Field(default=None, ge=0)
    description: Optional[str] = None


class ItineraryItemUpdate(BaseModel):
    date: Optional[date_type] = None
    time: Optional[time_type] = None
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    activity_type: Optional[ActivityType] = None
    booking_status: Optional[BookingStatus] = None
    booking_ref: Optional[str] = None
    cost: Optional[Decimal] = Field(default=None, ge=0)
    description: Optional[str] = None


class ItineraryItemOut(BaseModel):
    itinerary_id: str
    trip_id: str
    location: Optional[LocationOut] = None
    date: Optional[date_type] = None
    time: Optional[time_type] = None
    duration_minutes: Optional[int] = None
    activity_type: ActivityType
    weather_dependent: bool
    booking_status: BookingStatus
    booking_ref: Optional[str] = None
    #: Present for anything bought elsewhere - flights, tickets.
    booking_url: Optional[str] = None
    cost: Optional[Decimal] = None
    description: Optional[str] = None
    #: Supplier payload for items that have one. Opaque to the frontend except
    #: for flights, where it carries carrier, stops and duration.
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Planning
#
# The suggestion engine. One request produces three whole trips, so these are
# read-only projections of what the planner returned rather than anything the
# client can create directly - a plan becomes durable only when the user saves
# it as a Trip.
# ---------------------------------------------------------------------------


#: A trip longer than this is not a trip, and the planner cannot fill it from
#: one country's pool without repeating itself.
MAX_TRIP_DAYS = 21

#: Far enough ahead for anyone planning seriously; beyond it the dates are
#: almost certainly a typo or a mis-set year.
MAX_DAYS_AHEAD = 730


class SuggestRequest(BaseModel):
    country: str = Field(min_length=2, max_length=120)
    #: Optional. Must be a city the corpus places inside `country` - validated
    #: against the corpus in the router, since only the database knows.
    city: Optional[str] = Field(default=None, max_length=150)
    #: Either give `days`, or give both dates and let the length be derived.
    #: Bounds are checked in date_problem(), not here: a Field constraint
    #: fails with a Pydantic error object the frontend cannot render.
    days: Optional[int] = None
    start_date: Optional[date_type] = None
    end_date: Optional[date_type] = None
    #: Plan for a group instead of just the caller. The caller must be a member.
    group_id: Optional[str] = None
    #: Regeneration: places already shown, so a second press differs.
    avoid_location_ids: list[str] = Field(default_factory=list, max_length=400)
    #: Free text from the traveller, e.g. "too many churches".
    feedback: Optional[str] = Field(default=None, max_length=500)
    #: Where they are flying from - an IATA code or a city name. Falls back to
    #: the home city on their profile. Without one, and without dates, no fares
    #: are checked and the plans come back without flights.
    origin: Optional[str] = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _derive_days(self) -> "SuggestRequest":
        """Fill `days` from the dates. Deliberately does not reject anything.

        Raising here produces a Pydantic error object - `detail` becomes a list
        of {type, loc, msg, ...} - and the frontend can only render `detail`
        when it is a plain string, so the user sees nothing. Every rejection
        therefore happens in the router, where the message is a sentence.
        """
        if self.start_date and self.end_date and self.end_date >= self.start_date:
            object.__setattr__(
                self, "days", (self.end_date - self.start_date).days + 1
            )
        return self

    def date_problem(self) -> Optional[str]:
        """The reason these dates cannot describe a real trip, or None.

        Written to be shown to a person, because it will be.
        """
        today = date_type.today()

        if self.end_date and not self.start_date:
            return "Pick a start date as well as an end date."

        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                return "That trip ends before it starts - check the dates."
            span = (self.end_date - self.start_date).days + 1
            if span > MAX_TRIP_DAYS:
                return (
                    f"That is a {span}-day trip. Tio plans up to "
                    f"{MAX_TRIP_DAYS} days at a time."
                )

        if self.start_date:
            if self.start_date < today:
                return "That start date has already passed."
            if (self.start_date - today).days > MAX_DAYS_AHEAD:
                return "That start date is more than two years away."

        if self.days is None:
            return "Tell us how long the trip is, or give both dates."
        if self.days < 1:
            return "A trip has to be at least one day."
        if self.days > MAX_TRIP_DAYS:
            return (
                f"That is a {self.days}-day trip. Tio plans up to "
                f"{MAX_TRIP_DAYS} days at a time."
            )
        return None


class PlannedStopOut(BaseModel):
    location: LocationOut
    part_of_day: str
    title: str
    note: str
    category: Optional[str] = None
    #: Per-traveller fit, keyed by display name. Empty for a solo trip.
    fit: dict[str, float] = Field(default_factory=dict)


class PlannedDayOut(BaseModel):
    day: int
    city: str
    summary: str
    stops: list[PlannedStopOut]


class TripPlanOut(BaseModel):
    title: str
    #: "single_city" | "multi_city" | "themed"
    shape: str
    cities: list[str]
    rationale: str
    #: What this plan gives up relative to the other two. Never empty.
    tradeoffs: str
    days: list[PlannedDayOut]


class FlightSuggestionOut(BaseModel):
    """Cheapest indicative return fare to one of the plan's cities."""

    city: str
    destination: str
    origin: str
    depart_date: date_type
    return_date: Optional[date_type] = None
    price: Decimal
    currency: str
    airline: Optional[str] = None
    stops: Optional[int] = None
    booking_url: str


class SuggestResponse(BaseModel):
    country: str
    days: int
    travellers: list[str]
    plans: list[TripPlanOut]
    #: One per candidate city that could be priced, cheapest first. Empty when
    #: no origin or dates were given, or the provider had nothing cached.
    flights: list[FlightSuggestionOut] = Field(default_factory=list)
    #: Shown next to any price. Never omitted when flights are present.
    flight_disclaimer: Optional[str] = None
    #: Caveats the UI must show - accessibility filtering, unverifiable diets.
    notes: list[str] = Field(default_factory=list)
    #: Every location the plans drew on, so "regenerate" can exclude them.
    considered_location_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------


class FlightOfferOut(BaseModel):
    origin: str
    destination: str
    depart_date: date
    return_date: Optional[date] = None
    price: Decimal
    currency: str
    airline: Optional[str] = None
    flight_number: Optional[str] = None
    stops: Optional[int] = None
    duration_minutes: Optional[int] = None
    #: Where to actually buy it.
    booking_url: str
    provider: str
    #: True when the price came from a cache. See flights.py - it means the
    #: fare is indicative and the checkout may reprice.
    is_cached: bool
    fetched_at: datetime


class FlightSearchResponse(BaseModel):
    offers: list[FlightOfferOut] = Field(default_factory=list)
    #: Shown verbatim next to the prices. Not optional: presenting a cached
    #: fare as bookable is how this feature misleads someone's budget.
    disclaimer: str


class SaveFlightRequest(BaseModel):
    """Pin a searched offer onto a trip's itinerary.

    The whole offer is echoed back rather than an id, because the provider's
    cache is not addressable - there is no offer to re-fetch by reference.
    """

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    depart_date: date
    return_date: Optional[date] = None
    price: Optional[Decimal] = Field(default=None, ge=0)
    currency: str = Field(default="EUR", max_length=8)
    airline: Optional[str] = Field(default=None, max_length=16)
    flight_number: Optional[str] = Field(default=None, max_length=16)
    stops: Optional[int] = Field(default=None, ge=0)
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    booking_url: str = Field(min_length=1, max_length=2048)
