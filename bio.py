"""AI-written profile bios.

Generates a suggestion and returns it - it is never saved here. The user
sees it, edits it if they want, then PATCHes /api/profile. A bio the person
never agreed to should not appear on their card.
"""

from __future__ import annotations

import os
import random
from typing import Optional
from uuid import UUID

from openai import OpenAI
from sqlalchemy import select

from DATABASE.ORM import Account, Personality, session_scope

MODEL_ID = "gpt-4o-mini"
MAX_CHARS = 300

#: High enough that pressing Generate twice gives genuinely different bios
#: rather than the same sentence reworded. A bio is a piece of voice, not an
#: extraction, so there is no single correct answer to converge on - which is
#: exactly the case where a low temperature buys nothing and costs character.
TEMPERATURE = 1.0

ROLE = """You write short profile bios for a travel app.

Write like the person, not like a brochure. The reader is another traveller
deciding whether they would get on with them, so voice matters more than
completeness. Specific beats flattering; a small honest detail beats a big
vague claim.

RULES

One to three sentences, first person, under 280 characters.

Concrete and specific. Name real things - a city, a kind of place, a habit,
a time of day. Never generic filler: no "I love to travel", no "exploring
new cultures", no "wanderlust", no "adventure seeker", no "always up for
anything".

NEVER open with "Based in", "Living in", or the city name. That is the one
opening this app already has too many of. You will be given an angle to open
on instead - use it.

Languages: if any are given, at least one MUST appear, worked into a sentence
as something the person does - "happy to argue about food in French or
Spanish" - never as a list. Never write "I speak English, French".

No name, no age, no contact details, no emoji, no hashtags.

Write only the bio. No quotes around it, no preamble, no alternatives.

Good: "Lisbon-based, chasing good coffee and older architecture. Happiest
walking a city with no plan until something turns up."

Good: "Up before the light for the good ridgelines, in bed early enough to
regret it. Will detour an hour for a proper market. English or Portuguese,
both improved by wine."

Bad: "I am an adventurous traveller who loves exploring new places and
meeting new people!"

Bad: "Based in Toulouse. I speak English, French and Spanish. I enjoy
hiking, diving and food markets."

If the material below is too thin to say anything specific, write a short
honest line about being new here rather than inventing detail."""

#: ISO-ish codes are what the questionnaire stores; nobody wants to read "fr"
#: in their own bio, and the model writes better prose from real names.
_LANGUAGE_NAMES = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "de": "German",
    "nl": "Dutch",
    "ca": "Catalan",
    "eu": "Basque",
    "gl": "Galician",
    "ar": "Arabic",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "el": "Greek",
    "tr": "Turkish",
    "he": "Hebrew",
    "hi": "Hindi",
    "cs": "Czech",
    "ro": "Romanian",
    "hu": "Hungarian",
    "uk": "Ukrainian",
}


#: One is picked at random per call. Temperature alone does not buy variety
#: here - a small model asked the same question reaches for the same opening
#: every time ("Based in Toulouse..."), and no amount of telling it not to
#: fixes that reliably. Changing the instruction changes the answer.
OPENING_ANGLES = (
    "Open with a habit or a routine, not a place.",
    "Open with an opinion they would defend.",
    "Open with what they are usually doing early in the morning.",
    "Open with a small, specific confession or contradiction.",
    "Open with the kind of place they go out of their way for.",
    "Open with what they always end up doing on the first evening somewhere.",
    "Open with something they are bad at, lightly.",
)


def language_name(code: str) -> str:
    """"fr" -> "French". Anything unrecognised is passed through as written,
    so a user who typed "Occitan" gets Occitan rather than losing it."""
    cleaned = (code or "").strip()
    return _LANGUAGE_NAMES.get(cleaned.lower(), cleaned)


class NoProfile(Exception):
    """Nothing to write from - the person has not onboarded."""


def _source_material(session, account_id: UUID) -> Optional[str]:
    personality = session.scalar(
        select(Personality).where(Personality.account_id == account_id)
    )
    if personality is None:
        return None

    parts: list[str] = []
    if personality.profile_paragraph:
        parts.append(personality.profile_paragraph)
    if personality.home_city or personality.home_country:
        where = ", ".join(
            p for p in (personality.home_city, personality.home_country) if p
        )
        parts.append(f"Based in {where}.")
    if personality.preferred_language:
        spoken = [
            language_name(code)
            for code in personality.preferred_language
            if (code or "").strip()
        ]
        if spoken:
            parts.append("Speaks: " + ", ".join(spoken) + ".")
    if personality.hobbies:
        parts.append("Hobbies: " + ", ".join(personality.hobbies) + ".")
    if personality.travel_styles:
        parts.append("Travel styles: " + ", ".join(personality.travel_styles) + ".")
    if personality.travel_pace:
        parts.append(f"Pace: {personality.travel_pace}.")

    return "\n".join(parts) if parts else None


def generate(account_id: UUID) -> str:
    """Suggest a bio. Does not persist it."""
    with session_scope() as session:
        account = session.get(Account, account_id)
        material = _source_material(session, account_id)
        display_name = account.name if account else None

    if not material:
        raise NoProfile("complete onboarding first")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    completion = client.chat.completions.create(
        model=MODEL_ID,
        max_tokens=160,
        temperature=TEMPERATURE,
        # Discourages the model from reaching for the same phrasing every
        # time, which is what makes a second press feel like a real retry.
        presence_penalty=0.4,
        frequency_penalty=0.3,
        messages=[
            {"role": "system", "content": ROLE},
            {
                "role": "user",
                "content": (
                    f"Write the bio for {display_name or 'this traveller'} "
                    f"using only this:\n\n{material}\n\n"
                    f"{random.choice(OPENING_ANGLES)}"
                ),
            },
        ],
    )

    text = (completion.choices[0].message.content or "").strip()
    # Models sometimes wrap the answer in quotes despite being told not to.
    text = text.strip('"').strip("'").strip()
    return text[:MAX_CHARS]
