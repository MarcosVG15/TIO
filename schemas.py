"""Every request and response shape the API exposes.

This file is the frontend contract. FastAPI turns it into an OpenAPI schema
at /openapi.json, which can be compiled straight into TypeScript types.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from DATABASE.ORM import ActivityType, BookingStatus, TripStatus

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class GoogleSignInRequest(BaseModel):
    id_token: str


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
    questionnaire: dict[str, Any] = Field(default_factory=dict)
    conversation: list[dict[str, Any]] = Field(default_factory=list)


class OnboardingOut(BaseModel):
    account_id: str
    personality_id: str
    vector_pending: bool


class OnboardingStatusOut(BaseModel):
    has_profile: bool
    embedding_status: Optional[str] = None
    ready: bool


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileOut(BaseModel):
    personality_id: str
    dietary_restriction: dict[str, Any]
    accessibility_needs: dict[str, Any]
    preferred_language: list[str]
    profile_paragraph: Optional[str] = None


class ProfileUpdate(BaseModel):
    """Hard constraints only. The paragraph is derived, never hand-edited."""

    dietary_restriction: Optional[dict[str, Any]] = None
    accessibility_needs: Optional[dict[str, Any]] = None
    preferred_language: Optional[list[str]] = None


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
