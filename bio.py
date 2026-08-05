"""AI-written profile bios.

Generates a suggestion and returns it - it is never saved here. The user
sees it, edits it if they want, then PATCHes /api/profile. A bio the person
never agreed to should not appear on their card.
"""

from __future__ import annotations

import os
from typing import Optional
from uuid import UUID

from openai import OpenAI
from sqlalchemy import select

from DATABASE.ORM import Account, Personality, session_scope

MODEL_ID = "gpt-4o-mini"
MAX_CHARS = 300

ROLE = """You write short profile bios for a travel app.

RULES

One or two sentences, first person, under 280 characters.

Concrete and specific. Name real things - a city, a kind of place, a habit.
Never generic filler: no "I love to travel", no "exploring new cultures",
no "wanderlust", no "adventure seeker".

No name, no age, no contact details, no emoji, no hashtags.

Write only the bio. No quotes around it, no preamble, no alternatives.

Good: "Lisbon-based, chasing good coffee and older architecture. Happiest
walking a city with no plan until something turns up."

Bad: "I am an adventurous traveller who loves exploring new places and
meeting new people!"

If the material below is too thin to say anything specific, write a short
honest line about being new here rather than inventing detail."""


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
        messages=[
            {"role": "system", "content": ROLE},
            {
                "role": "user",
                "content": (
                    f"Write the bio for {display_name or 'this traveller'} "
                    f"using only this:\n\n{material}"
                ),
            },
        ],
    )

    text = (completion.choices[0].message.content or "").strip()
    # Models sometimes wrap the answer in quotes despite being told not to.
    text = text.strip('"').strip("'").strip()
    return text[:MAX_CHARS]
