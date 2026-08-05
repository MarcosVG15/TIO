import os
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

MODEL_ID = "gpt-4o-2024-08-06"

ROLE = """You are a travel-profile extraction engine. You convert onboarding
material into one structured record. You do not converse, greet, or
explain - you emit the record only.

RULES

1. Extract only what is stated or clearly implied. Never invent a
preference to fill a field. If something was not covered, leave the
field empty or null.

2. Hard constraints (dietary, accessibility, language) require an
explicit statement. "I had a great vegetarian meal in Rome" is not a
dietary restriction. "I don't eat meat" is. When in doubt, omit -
a missed constraint is recoverable, an invented one is not.

3. The questionnaire outranks the conversation on hard constraints.
The conversation outranks the questionnaire on soft preferences,
since people reveal more taste in conversation than in a form.

4. If the two sources contradict each other on a hard constraint, take
the more restrictive value and lower your confidence score.

PROFILE PARAGRAPH

One paragraph, 80-150 words, third person, present tense, starting
"This traveller".

Include: activity affinities, pace, atmosphere, planning style,
food interests as taste (cuisines enjoyed, dining style).

Exclude: any name or identifying detail; dietary restrictions,
accessibility needs, and languages; hedging like "seems to" or
"might enjoy"; anything not grounded in the source material.

Write plain declarative prose. No lists, no headings, no markdown.

STRUCTURED TRAITS

home_city / home_country: where they are based, not somewhere they have
visited. Leave null unless they say where they live or are from.

hobbies: interests outside travel, lowercase, one or two words each
("photography", "rock climbing"). Not travel preferences - those belong in
travel_styles. Maximum twelve.

preferred_languages: ISO-ish short names of languages they speak
("en", "fr", "es"). Only what they state.

travel_pace and travel_styles: pick only from the allowed values, and only
where the source supports it. An empty list is better than a guess.

SHORT BIO

One or two sentences, first person, under 300 characters, written to be read
by another traveller rather than by a machine. Concrete and specific - what
they actually like, where they are from - never generic filler like "I love
to travel and explore new places". No name, no age, no contact details.

Example: "Lisbon-based, chasing good coffee and older architecture. Happiest
walking a city with no plan until something interesting turns up."

Leave it null if the sources are too thin to write something honest."""

PROMPT = """Extract a travel profile from the two sources below.

<questionnaire>
{questionnaire_json}
</questionnaire>

<conversation>
{conversation_json}
</conversation>

If a source is empty, work from the other one alone and lower your
confidence score accordingly."""


class DietaryRestriction(str, Enum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    HALAL = "halal"
    KOSHER = "kosher"
    GLUTEN_FREE = "gluten_free"
    LACTOSE_INTOLERANT = "lactose_intolerant"
    NUT_ALLERGY = "nut_allergy"
    SHELLFISH_ALLERGY = "shellfish_allergy"


class AccessibilityNeed(str, Enum):
    WHEELCHAIR_ACCESS = "wheelchair_access"
    STEP_FREE_ACCESS = "step_free_access"
    HEARING_ASSISTANCE = "hearing_assistance"
    VISUAL_ASSISTANCE = "visual_assistance"
    SERVICE_ANIMAL = "service_animal"


class BudgetTier(str, Enum):
    BUDGET = "budget"
    MODERATE = "moderate"
    LUXURY = "luxury"


class TravelPace(str, Enum):
    PACKED = "packed"
    BALANCED = "balanced"
    SLOW = "slow"


class TravelStyle(str, Enum):
    CITY_BREAK = "city_break"
    BEACH = "beach"
    NATURE = "nature"
    ROAD_TRIP = "road_trip"
    CULTURE = "culture"
    FOOD = "food"
    NIGHTLIFE = "nightlife"
    ADVENTURE = "adventure"
    WELLNESS = "wellness"
    FAMILY = "family"
    BUSINESS = "business"


class ExtractedProfile(BaseModel):
    # --- hard constraints: these become SQL filters ---
    dietary_restrictions: list[DietaryRestriction] = Field(default_factory=list)
    accessibility_needs: list[AccessibilityNeed] = Field(default_factory=list)
    preferred_languages: list[str] = Field(default_factory=list)
    budget_tier: Optional[BudgetTier] = None

    # --- structured traits: filterable, and they sharpen the embedding ---
    home_city: Optional[str] = Field(default=None, max_length=120)
    home_country: Optional[str] = Field(default=None, max_length=120)
    hobbies: list[str] = Field(default_factory=list, max_length=12)
    travel_pace: Optional[TravelPace] = None
    travel_styles: list[TravelStyle] = Field(default_factory=list)

    # --- the prose that actually gets embedded ---
    profile_paragraph: str = Field(min_length=1, max_length=1200)
    #: One or two sentences, first person, for the traveller card. Distinct
    #: from profile_paragraph, which is third person and written for a
    #: similarity model rather than for another human to read.
    short_bio: Optional[str] = Field(default=None, max_length=300)

    confidence: float = Field(ge=0.0, le=1.0)


class UserExtractor:
    def __init__(self, questionnaire: list[dict], conversation: list[dict]):
        self.questionnaire = questionnaire
        self.conversation = conversation
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _build_user_message(self) :
        return PROMPT.format(
            questionnaire_json=self.questionnaire,
            conversation_json=self.conversation,
        )

    def extract(self) -> ExtractedProfile:
        completion = self.client.chat.completions.parse(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": ROLE},
                {"role": "user", "content": self._build_user_message()},
            ],
            response_format=ExtractedProfile,
        )
        return completion.choices[0].message.parsed
