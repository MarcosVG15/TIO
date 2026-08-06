"""LLM compatibility scoring for a shortlist of places.

The last stage of a cascade, and the only one that reads a place the way a
person would. The stages before it are cheap and blunt on purpose:

  1. SQL gate      6.8M rows -> ~600k that something external vouches for
  2. Vector search ~600k -> a few hundred nearest the taste vector
  3. Cheap signals popularity, declared travel styles, dietary tags -> ~25
  4. This         ~25 -> scored, explained, ordered

Only the last stage is paid, and it sees a couple of dozen rows, so a shelf
costs one small call. Doing it the other way round - asking a model to rank six
million places - is the version that cannot work.

Why it earns its place: `embedding_text` in this corpus is templated ("X is a
attraction in Y"), so every vector sits in nearly the same spot and cosine
cannot tell a cathedral from a car park. A model reading the name, category,
city and what facts we hold *can*, and it can say why in a sentence the card
can show. It is a patch over a weak vector space, not a replacement for fixing
it - see the module notes in `recommend`.

The score it returns is a compatibility judgement, not a probability. It is
shown as a percentage because that is what the card renders, and it is the
model's opinion rather than a measurement - which is why a failure here falls
back to the cheap ranking rather than showing nothing.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Sequence

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from recommend import Candidate, Traveller

load_dotenv()

log = logging.getLogger(__name__)

MODEL_ID = os.getenv("RERANK_MODEL", "gpt-4o-mini")

#: How many candidates the model sees. Enough that it has real choices, few
#: enough that one call is cheap and fast.
MAX_CANDIDATES = 25

#: Descriptions are templated and near-identical; a fragment is plenty.
MAX_BLURB = 120

ROLE = """You judge how well specific places suit a specific traveller.

You are given one traveller's profile and a shortlist of real places. For each
place, return a compatibility score from 0 to 100 and one short reason.

SCORING

Use the whole range. A shortlist where everything scores 70-80 is useless - the
point is to separate them. Anchor roughly:

  85-100  strongly suits this traveller specifically
  60-84   good fit, they would likely enjoy it
  35-59   fine but unremarkable for them
  0-34    poor fit, wrong sort of place for this person

Judge fit to THIS traveller, not general fame. A famous nightclub is a poor fit
for someone who wants quiet mornings, and should score low even though it is
well known. Equally, a small regional museum can score high for the right
person.

Weigh what you are told: their pace, their declared styles, what the profile
paragraph says they enjoy. Where a place's category clearly conflicts with
their stated preferences, score it low and say so.

REASONS

One sentence, under 120 characters, addressed to the traveller as "you".
Concrete and specific to why THIS place suits THEM - not a description of the
place, and never generic praise.

Good: "Quiet cloisters and almost no crowds before ten, which is how you like
to start."
Good: "Loud, late and packed - the opposite of the slow mornings you asked
for."
Bad: "A beautiful historic site worth visiting."

You must return exactly one entry per place, using the location_id given. Do
not invent places and do not omit any."""


class Judgement(BaseModel):
    location_id: str
    score: int = Field(ge=0, le=100)
    reason: str = Field(max_length=200)


class Judgements(BaseModel):
    places: list[Judgement]


class RerankError(Exception):
    """The judge could not be used. Callers fall back to the cheap ranking."""


def _traveller_block(travellers: Sequence[Traveller]) -> str:
    lines: list[str] = []
    for traveller in travellers:
        lines.append(f"- {traveller.name}")
        if traveller.paragraph:
            lines.append(f"  profile: {traveller.paragraph}")
        if traveller.travel_styles:
            lines.append(f"  styles: {', '.join(traveller.travel_styles)}")
        if traveller.travel_pace:
            lines.append(f"  pace: {traveller.travel_pace}")
        if traveller.budget_tier:
            lines.append(f"  budget: {traveller.budget_tier}")
        if traveller.dietary:
            lines.append(f"  dietary: {', '.join(sorted(traveller.dietary))}")
        if traveller.accessibility:
            lines.append(f"  accessibility: {', '.join(sorted(traveller.accessibility))}")
    return "\n".join(lines)


def _place_line(candidate: Candidate) -> str:
    bits = [f"{candidate.location_id} | {candidate.name}"]
    where = ", ".join(p for p in (candidate.city, candidate.country) if p)
    if where:
        bits.append(where)
    bits.append(candidate.category)
    if candidate.subcategory:
        bits.append(candidate.subcategory)
    if candidate.cuisine:
        bits.append(f"cuisine: {candidate.cuisine}")
    if candidate.wikipedia_title:
        bits.append("has a Wikipedia article")
    if candidate.pageviews:
        bits.append(f"{candidate.pageviews} pageviews")
    blurb = (candidate.blurb or "").strip()
    if blurb:
        bits.append(blurb[:MAX_BLURB])
    return " | ".join(bits)


def score_candidates(
    candidates: Sequence[Candidate],
    travellers: Sequence[Traveller],
    limit: int = MAX_CANDIDATES,
) -> list[Candidate]:
    """Attach llm_score and llm_reason to the shortlist, in place.

    Returns the same candidates re-ordered by the model's judgement. On any
    failure the input order is returned untouched: a shelf ranked by the cheap
    signals is worth far more than an error page.
    """
    shortlist = list(candidates)[:limit]
    if not shortlist:
        return []

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        log.info("no OPENAI_API_KEY; skipping the compatibility re-rank")
        return shortlist

    prompt = (
        f"<traveller>\n{_traveller_block(travellers)}\n</traveller>\n\n"
        f"<places>\n"
        + "\n".join(_place_line(c) for c in shortlist)
        + "\n</places>\n\n"
        f"Score all {len(shortlist)} places."
    )

    try:
        completion = OpenAI(api_key=key, max_retries=2).chat.completions.parse(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": ROLE},
                {"role": "user", "content": prompt},
            ],
            response_format=Judgements,
            # Low but not zero: this is a judgement, and pinning it to zero
            # makes the model cling to one anchor for every place.
            temperature=0.3,
        )
        parsed = completion.choices[0].message.parsed
    except Exception as exc:
        log.warning("compatibility re-rank failed, using cheap ranking: %s", exc)
        return shortlist

    if parsed is None:
        return shortlist

    by_id = {str(c.location_id): c for c in shortlist}
    judged = 0
    for place in parsed.places:
        candidate = by_id.get(place.location_id)
        if candidate is None:
            # Invented id. Dropped rather than trusted - the same guard the
            # itinerary composer uses.
            log.warning("re-rank returned unknown location_id %s", place.location_id)
            continue
        candidate.llm_score = place.score
        candidate.llm_reason = place.reason.strip() or None
        judged += 1

    if judged == 0:
        return shortlist

    # Anything the model skipped keeps its cheap ranking, mapped onto the same
    # 0-100 scale so the two are comparable rather than interleaved randomly.
    for candidate in shortlist:
        if candidate.llm_score is None:
            candidate.llm_score = max(0, min(100, round(candidate.final_score * 100)))

    shortlist.sort(key=lambda c: (c.llm_score or 0, c.final_score), reverse=True)
    log.info("compatibility re-rank scored %d/%d places", judged, len(shortlist))
    return shortlist
