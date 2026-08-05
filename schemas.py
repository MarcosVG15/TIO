"""Every request and response shape the API exposes.

This file is the frontend contract. FastAPI turns it into an OpenAPI schema
at /openapi.json, which can be compiled straight into TypeScript types.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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


class TripCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    origin_location_id: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    is_group: bool = False
    group_id: Optional[str] = None


class TripUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    status: Optional[TripStatus] = None


class TripOut(BaseModel):
    trip_id: str
    name: str
    status: TripStatus
    is_group: bool
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_limit: Optional[Decimal] = None
    origin_location: Optional[LocationOut] = None
    group_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------


class ItineraryItemCreate(BaseModel):
    location_id: Optional[str] = None
    date: Optional[date] = None
    time: Optional[time] = None
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    activity_type: ActivityType = ActivityType.OTHER
    weather_dependent: bool = False
    cost: Optional[Decimal] = Field(default=None, ge=0)
    description: Optional[str] = None


class ItineraryItemUpdate(BaseModel):
    date: Optional[date] = None
    time: Optional[time] = None
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
    date: Optional[date] = None
    time: Optional[time] = None
    duration_minutes: Optional[int] = None
    activity_type: ActivityType
    weather_dependent: bool
    booking_status: BookingStatus
    booking_ref: Optional[str] = None
    cost: Optional[Decimal] = None
    description: Optional[str] = None
