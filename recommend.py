"""Candidate retrieval and group compromise.

Given a country and one or more travellers, produce the pool of real places a
plan can be built from. This module decides *what is worth considering*; it
does not write itineraries - that is `itinerary`.

Two ideas carry the whole thing.

**Retrieve wide, rank fair.** Retrieval is per member plus the group centroid,
so every member's neighbourhood is represented in the pool even when tastes
diverge. Ranking is then maximin: a candidate scores as its *least* satisfied
member. Ranking on the centroid instead is the classic failure - the mean of a
beach lover and a museum lover is a shopping centre, and everyone gets a trip
nobody asked for.

**Maximin alone is not enough.** A fairness floor finds things nobody objects
to, which trends towards the inoffensive. So selection also reserves a quota
per member from that member's own favourites. The result has a floor of
tolerability and a handful of things each person will recognise as theirs.

Hard constraints are handled where each belongs, which is not uniformly:

  * Accessibility is a SQL filter. `wheelchair` is a real, widely-populated
    OSM tag, and for someone who declared the need, an unknown value has to be
    excluded rather than assumed fine. This shrinks the pool, sometimes a lot,
    and the caller is told by how much.

  * Dietary restrictions are *not* a SQL filter, and this is deliberate. OSM
    has `diet:*` tags for some restaurants and nothing at all for allergies, so
    a filter would either drop every restaurant whose tags are silent or, worse,
    imply that what survived is safe. Instead they bias ranking where tags
    exist and are passed to the planner as explicit constraints, and the plan
    carries a caveat. Nothing in this system can promise a kitchen is nut-free.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select

import embeddings
import proposals
from DATABASE.ORM import (
    Account,
    ActivityType,
    GroupMember,
    Location,
    Personality,
    session_scope,
)

log = logging.getLogger(__name__)

#: How many candidates to pull per member, and for the centroid.
PER_MEMBER_K = 40

#: No single category may exceed this share of the pool, so a country full of
#: churches does not return a pool of nothing but churches.
MAX_CATEGORY_SHARE = 0.35

#: Places to guarantee each member from their own top picks.
DEFAULT_QUOTA_PER_MEMBER = 3

#: Categories that are not day activities. Hotels and stations are real places
#: worth storing, but a sightseeing pool full of them is noise.
EXCLUDED_CATEGORIES = (ActivityType.HOTEL, ActivityType.TRANSPORT)

#: OSM diet tag values that count as a yes.
_DIET_YES = ("yes", "only")

#: Which OSM diet tag, if any, evidences each declared restriction.
_DIET_TAGS = {
    "vegetarian": "diet:vegetarian",
    "vegan": "diet:vegan",
    "halal": "diet:halal",
    "kosher": "diet:kosher",
    "gluten_free": "diet:gluten_free",
}

#: Wheelchair values that satisfy a declared access need. NULL is absent on
#: purpose - see the module docstring.
_ACCESS_OK = ("yes", "designated")

# ---------------------------------------------------------------------------
# Ranking signals beyond the vector.
#
# The corpus is 6.8M OSM rows of which 91% have no wikidata id, no Wikipedia
# article and no photograph - bus stops, benches, individual shopfronts. Worse,
# `embedding_text` is templated ("X is a attraction in Y"), so every vector sits
# in nearly the same place and cosine alone cannot separate the Prado from a
# car park. These signals do the work the geometry currently cannot.
# ---------------------------------------------------------------------------

#: Relative weights of the cheap signals. Taste is deliberately not dominant
#: *yet*: with templated embedding_text the cosine spread across the whole
#: corpus is a few points, so leaning on it would be leaning on noise. Raise
#: TASTE_WEIGHT and drop the others once embedding_text carries real prose.
TASTE_WEIGHT = 0.45
POPULARITY_WEIGHT = 0.35
STYLE_WEIGHT = 0.20

#: A tagged-compatible restaurant gets this much of a nudge for each declared
#: dietary need it satisfies. Deliberately small - it reorders, it does not
#: decide, because absence of a tag is not evidence of unsuitability.
DIET_TAG_BONUS = 0.04

#: Pageview counts are extremely long-tailed - the Eiffel Tower has millions,
#: a regional museum a few hundred - so they are compressed logarithmically
#: before use. Roughly: 100k+ views saturates at 1.0.
_PAGEVIEW_SATURATION = 100_000
_SITELINK_SATURATION = 60

#: Which categories each declared travel style actually implies. Structured
#: traits the questionnaire already collects, and which the vector path ignored
#: entirely.
_STYLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "city_break": ("landmark", "museum", "shopping"),
    "beach": ("outdoor", "relaxation"),
    "nature": ("outdoor",),
    "road_trip": ("landmark", "outdoor"),
    "culture": ("museum", "landmark", "event"),
    "food": ("restaurant",),
    "nightlife": ("bar", "nightlife"),
    "adventure": ("outdoor",),
    "wellness": ("relaxation",),
    "family": ("outdoor", "landmark", "event"),
    "business": (),
}


class NoProfile(Exception):
    """A traveller has no usable taste vector yet."""


class EmptyPool(Exception):
    """Nothing in the corpus survived the filters."""


@dataclass(frozen=True)
class Traveller:
    """One person's taste vector plus the facts that constrain a plan."""

    account_id: UUID
    name: str
    vector: list[float]
    paragraph: Optional[str] = None
    dietary: frozenset[str] = frozenset()
    accessibility: frozenset[str] = frozenset()
    budget_tier: Optional[str] = None
    travel_pace: Optional[str] = None
    travel_styles: tuple[str, ...] = ()

    @property
    def needs_step_free(self) -> bool:
        return bool(
            self.accessibility & {"wheelchair_access", "step_free_access"}
        )


@dataclass
class Candidate:
    """A real place, scored against everyone going."""

    location_id: UUID
    name: str
    city: Optional[str]
    region: Optional[str]
    country: Optional[str]
    category: str
    subcategory: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    price: Optional[Decimal]
    ticket_ref: str
    wheelchair: Optional[str]
    cuisine: Optional[str]
    website: Optional[str]
    popularity: Optional[float]
    blurb: Optional[str]
    #: Denormalized primary image, mirrored onto Location from LocationImage.
    picture: Optional[str] = None
    #: Notability evidence. 91% of the corpus has none of these.
    wikidata_qid: Optional[str] = None
    wikipedia_title: Optional[str] = None
    pageviews: Optional[int] = None
    sitelinks: Optional[int] = None

    #: The individual signals, kept so a ranking can be explained rather than
    #: just asserted.
    taste_score: float = 0.0
    popularity_prior: float = 0.0
    style_score: float = 0.0
    #: Filled by the LLM re-rank stage when it runs.
    llm_score: Optional[int] = None
    llm_reason: Optional[str] = None
    tags: dict[str, Any] = field(default_factory=dict)

    #: The location's own embedding, needed only while scoring. Dropped before
    #: the pool is returned: 768 floats per candidate is pure weight to carry
    #: through planning and into a JSON response.
    vector: list[float] = field(default_factory=list, repr=False)

    #: account_id -> cosine similarity. Populated by `score_candidates`.
    scores: dict[UUID, float] = field(default_factory=dict)
    #: The fairness floor: the least satisfied member's similarity.
    group_score: float = 0.0
    #: group_score plus the popularity and diet nudges. What ranking sorts on.
    final_score: float = 0.0
    #: Members who had this in their personal top picks, for explainability.
    favourite_of: tuple[UUID, ...] = ()
    #: Why it is in the pool: "quota" (reserved for a member) or "shared".
    reason: str = "shared"

    @property
    def place_label(self) -> str:
        where = self.city or self.region or self.country or ""
        return f"{self.name} ({where})" if where else self.name


@dataclass
class Pool:
    """The candidate set a planner may draw from, and how it was built."""

    country: Optional[str]
    travellers: list[Traveller]
    candidates: list[Candidate]
    #: city -> how many candidates are there, best-first by aggregate score.
    cities: list[tuple[str, int, float]]
    #: Diagnostics worth surfacing rather than hiding.
    considered: int = 0
    dropped_for_access: int = 0
    notes: list[str] = field(default_factory=list)
    #: How many model-proposed names resolved to real rows, when that source
    #: ran. A falling hit rate means the corpus has a gap or the model drifted.
    proposal_hit_rate: Optional[float] = None
    proposals_used: int = 0

    @property
    def is_group(self) -> bool:
        return len(self.travellers) > 1


# ---------------------------------------------------------------------------
# Loading travellers
# ---------------------------------------------------------------------------


def _flags(value: Any) -> frozenset[str]:
    """JSONB {"vegan": true} -> {"vegan"}."""
    if not isinstance(value, dict):
        return frozenset()
    return frozenset(key for key, on in value.items() if on)


def _traveller_from_rows(account: Account, personality: Personality) -> Traveller:
    if personality.personality_vector is None:
        raise NoProfile(
            f"{account.name} has no taste vector yet "
            f"(embedding_status={personality.embedding_status.value})"
        )

    # The guard that stops a mismatched vector from producing confident
    # nonsense. See embeddings.require_same_space.
    embeddings.require_same_space(
        personality.embedding_model,
        personality.embedding_version,
        what=f"{account.name}'s profile vector",
    )

    return Traveller(
        account_id=account.account_id,
        name=account.name,
        vector=list(personality.personality_vector),
        paragraph=personality.profile_paragraph,
        dietary=_flags(personality.dietary_restriction),
        accessibility=_flags(personality.accessibility_needs),
        budget_tier=personality.budget_tier,
        travel_pace=personality.travel_pace,
        travel_styles=tuple(personality.travel_styles or ()),
    )


def load_travellers(
    account_ids: Sequence[UUID],
) -> list[Traveller]:
    """Fetch taste vectors and constraints for the people going.

    Raises NoProfile naming the person, rather than quietly planning for a
    subset - a group plan that silently ignored a member would be worse than
    an error the caller can act on.
    """
    if not account_ids:
        raise ValueError("no travellers requested")

    with session_scope() as session:
        rows = session.execute(
            select(Account, Personality)
            .join(Personality, Personality.account_id == Account.account_id)
            .where(
                Account.account_id.in_(list(account_ids)),
                Account.deleted_at.is_(None),
            )
        ).all()

        found = {account.account_id: (account, personality) for account, personality in rows}
        missing = [str(a) for a in account_ids if a not in found]
        if missing:
            raise NoProfile(
                "no profile for account(s): " + ", ".join(missing)
            )

        # Preserve the caller's order so the requester is first.
        return [_traveller_from_rows(*found[account_id]) for account_id in account_ids]


def list_countries() -> list[tuple[str, int]]:
    """Countries the corpus can actually plan for, most options first.

    Only rows with a vector count. A country present in the corpus but not yet
    embedded cannot be planned for, and offering it would produce an empty
    result the user cannot explain.
    """
    with session_scope() as session:
        rows = session.execute(
            select(Location.country, func.count())
            .where(Location.vec.is_not(None), Location.country.is_not(None))
            .group_by(Location.country)
            .order_by(func.count().desc())
        ).all()
    return [(country, count) for country, count in rows if country]


def list_cities(country: str, minimum: int = 1) -> list[tuple[str, int]]:
    """Cities within one country, most options first.

    The city list is derived from the corpus rather than a static gazetteer, so
    a city can only ever be offered for the country it actually sits in - which
    is what stops "Spain / Lisbon" being selectable at all.
    """
    with session_scope() as session:
        rows = session.execute(
            select(Location.city, func.count())
            .where(
                Location.vec.is_not(None),
                Location.city.is_not(None),
                or_(
                    func.lower(Location.country) == country.lower(),
                    func.lower(Location.region) == country.lower(),
                ),
            )
            .group_by(Location.city)
            .having(func.count() >= minimum)
            .order_by(func.count().desc())
        ).all()
    return [(city, count) for city, count in rows if city]


def city_in_country(city: str, country: str) -> bool:
    """Whether the corpus places this city in this country."""
    return any(name.lower() == city.lower() for name, _ in list_cities(country))


def group_member_ids(group_id: UUID) -> list[UUID]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(GroupMember.account_id).where(GroupMember.group_id == group_id)
            ).all()
        )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _base_filters(
    country: Optional[str],
    travellers: Sequence[Traveller],
    city: Optional[str] = None,
    notable_only: bool = False,
):
    """Hard filters. Everything here excludes rows outright.

    `country` is optional so the same path serves "somewhere in Spain" and
    "anywhere at all", which is what the discovery shelf asks for.
    """
    conditions = [
        Location.vec.is_not(None),
        Location.category.not_in(EXCLUDED_CATEGORIES),
    ]

    if notable_only:
        # 91% of the corpus is bus stops, benches and individual shopfronts
        # with no external evidence anyone has ever cared about them. A
        # wikidata entity, a Wikipedia article or a photograph is the cheapest
        # available proof that a place is somewhere you would go on purpose.
        conditions.append(
            or_(
                Location.wikidata_qid.is_not(None),
                Location.wikipedia_title.is_not(None),
                Location.picture.is_not(None),
            )
        )

    if city:
        # Narrowing to a city makes the whole plan local, which is the point of
        # offering the choice at all.
        conditions.append(func.lower(Location.city) == city.lower())

    if country:
        conditions.append(
            # OSM country naming is inconsistent in case; region catches rows
            # whose country tag never got populated but whose ingest bbox did.
            or_(
                func.lower(Location.country) == country.lower(),
                func.lower(Location.region) == country.lower(),
            )
        )

    # One member needing step-free access constrains the whole trip: the group
    # goes together, so a place they cannot enter is not a candidate for anyone.
    if any(t.needs_step_free for t in travellers):
        conditions.append(Location.wheelchair.in_(_ACCESS_OK))

    return and_(*conditions)


def _fetch_near(
    session,
    vector: Sequence[float],
    country: Optional[str],
    travellers: Sequence[Traveller],
    limit: int,
    city: Optional[str] = None,
    notable_only: bool = False,
) -> list[Location]:
    """The k nearest surviving rows to one vector.

    Ordering happens in Postgres so the HNSW index does the work; only `limit`
    rows cross the wire.
    """
    return list(
        session.scalars(
            select(Location)
            .where(_base_filters(country, travellers, city, notable_only))
            .order_by(Location.vec.cosine_distance(list(vector)))
            .limit(limit)
        ).all()
    )


def _picture_of(row: Location) -> Optional[str]:
    """Best available image for a place.

    `Location.picture` is a denormalized rollup that the seed may never have
    filled, so fall back to the `location_images` rows it was mirrored from.
    Prefers the thumbnail: these render in a 208px card, and shipping a 4MB
    original to fill it is wasteful.
    """
    if row.picture:
        return row.picture

    images = getattr(row, "images", None) or []
    if not images:
        return None
    # is_primary marks the one chosen for list views; otherwise take the first.
    best = next((i for i in images if getattr(i, "is_primary", False)), images[0])
    return getattr(best, "thumb_url", None) or getattr(best, "url", None)


def _to_candidate(row: Location) -> Candidate:
    return Candidate(
        location_id=row.location_id,
        name=row.name,
        city=row.city,
        region=row.region,
        country=row.country,
        category=row.category.value if row.category else "other",
        subcategory=row.subcategory,
        latitude=row.latitude,
        longitude=row.longitude,
        price=row.price,
        ticket_ref=row.ticket_ref.value if row.ticket_ref else "none",
        wheelchair=row.wheelchair,
        cuisine=row.cuisine,
        website=row.website,
        popularity=row.popularity_score,
        # embedding_text is the paragraph the vector came from, so it is the
        # most faithful description of what the place is like. Falling back to
        # the raw upstream prose keeps rows usable when it is absent.
        blurb=row.embedding_text or row.description,
        picture=_picture_of(row),
        wikidata_qid=row.wikidata_qid,
        wikipedia_title=row.wikipedia_title,
        pageviews=row.wikipedia_pageviews,
        sitelinks=row.wikidata_sitelinks,
        tags=dict(row.tags or {}),
        vector=list(row.vec) if row.vec is not None else [],
    )


def _assert_corpus_space(rows: Sequence[Location]) -> None:
    """Every location we are about to rank must be in our vector space.

    Checking the traveller's vector alone is not enough, and that gap is the
    whole failure this guard exists to prevent: a user embedded by this process
    and a corpus embedded by the seed job with a different model produce
    cosine distances that look perfectly reasonable and mean nothing.

    Checked against the rows actually retrieved rather than a sample, so a
    corpus that is half re-embedded is caught rather than passing on the luck
    of which row was sampled.
    """
    pairs = {(row.embedding_model, row.embedding_version) for row in rows}
    for model, version in pairs:
        embeddings.require_same_space(
            model, version, what=f"the location corpus ({len(rows)} rows retrieved)"
        )


def retrieve(
    country: Optional[str],
    travellers: Sequence[Traveller],
    per_member_k: int = PER_MEMBER_K,
    city: Optional[str] = None,
    notable_only: bool = False,
) -> tuple[list[Candidate], dict[UUID, list[UUID]], int]:
    """Build the raw candidate set.

    Returns (candidates, per-member favourite ids, rows examined).

    Retrieval is one query per member plus one for the centroid, unioned. The
    per-member queries are what stop a divergent member from being averaged
    out of existence; the centroid query is what surfaces places that genuinely
    suit everyone, which no individual query is guaranteed to rank highly.
    """
    by_id: dict[UUID, Candidate] = {}
    favourites: dict[UUID, list[UUID]] = {}
    examined = 0

    retrieved: list[Location] = []

    with session_scope() as session:
        for traveller in travellers:
            rows = _fetch_near(
                session,
                traveller.vector,
                country,
                travellers,
                per_member_k,
                city,
                notable_only,
            )
            examined += len(rows)
            retrieved.extend(rows)
            favourites[traveller.account_id] = [row.location_id for row in rows]
            for row in rows:
                by_id.setdefault(row.location_id, _to_candidate(row))

        if len(travellers) > 1:
            middle = embeddings.centroid([t.vector for t in travellers])
            rows = _fetch_near(
                session, middle, country, travellers, per_member_k, city, notable_only
            )
            examined += len(rows)
            retrieved.extend(rows)
            for row in rows:
                by_id.setdefault(row.location_id, _to_candidate(row))

        # Inside the session: the rows must still be attached to read their
        # provenance columns.
        _assert_corpus_space(retrieved)

    return list(by_id.values()), favourites, examined


# ---------------------------------------------------------------------------
# Scoring - pure functions, no database
# ---------------------------------------------------------------------------


def popularity_prior(candidate: Candidate) -> float:
    """0..1 estimate of "is this place actually worth going to".

    Wikipedia pageviews are the strongest honest signal available: they measure
    what people actually looked up, which no amount of tag-reading can. Wikidata
    sitelinks are the fallback for places with an entity but no article in the
    language we counted. Both are compressed logarithmically because the
    distribution spans six orders of magnitude and a linear scale would make
    everything except the top twenty places score zero.
    """
    best = 0.0

    if candidate.pageviews and candidate.pageviews > 0:
        best = max(
            best,
            min(1.0, math.log1p(candidate.pageviews) / math.log1p(_PAGEVIEW_SATURATION)),
        )
    if candidate.sitelinks and candidate.sitelinks > 0:
        best = max(
            best,
            min(1.0, math.log1p(candidate.sitelinks) / math.log1p(_SITELINK_SATURATION)),
        )
    if best == 0.0 and candidate.popularity:
        # Whatever the seed computed, when nothing better is recorded.
        best = max(0.0, min(1.0, float(candidate.popularity)))

    # Having an article or an entity at all is weak evidence of notability,
    # worth something even when no counts were collected.
    if best == 0.0 and (candidate.wikipedia_title or candidate.wikidata_qid):
        best = 0.15
    return best


def style_affinity(candidate: Candidate, travellers: Sequence[Traveller]) -> float:
    """0..1 for how well a place matches the declared travel styles.

    Uses the structured traits the questionnaire already collects and the
    vector path ignored completely. For a group this is the *share* of members
    whose styles the place serves, which keeps it consistent with the maximin
    idea: a place matching one member of four scores 0.25, not 1.0.
    """
    if not travellers:
        return 0.0

    served = 0
    for traveller in travellers:
        wanted: set[str] = set()
        for style in traveller.travel_styles:
            wanted.update(_STYLE_CATEGORIES.get(style, ()))
        if not wanted:
            # No styles declared: this signal cannot speak for them, so they
            # count as neutral rather than dissatisfied.
            served += 1
            continue
        if candidate.category in wanted:
            served += 1
        elif candidate.subcategory and candidate.subcategory.lower() in wanted:
            served += 1
    return served / len(travellers)


def _diet_bonus(candidate: Candidate, travellers: Sequence[Traveller]) -> float:
    """Small nudge for restaurants whose tags evidence a declared need.

    Only ever positive. A missing tag must not push a place down, because most
    of the corpus is simply untagged and silence is not a refusal.
    """
    if candidate.category != ActivityType.RESTAURANT.value:
        return 0.0
    needs = {need for traveller in travellers for need in traveller.dietary}
    if not needs:
        return 0.0

    satisfied = 0
    for need in needs:
        tag = _DIET_TAGS.get(need)
        if tag and str(candidate.tags.get(tag, "")).lower() in _DIET_YES:
            satisfied += 1
    return DIET_TAG_BONUS * satisfied


def score_candidates(
    candidates: Sequence[Candidate],
    travellers: Sequence[Traveller],
    favourites: Optional[dict[UUID, Sequence[UUID]]] = None,
) -> list[Candidate]:
    """Attach per-member, group and final scores. Mutates and returns.

    group_score is the minimum across members - the fairness floor. final_score
    adds the popularity prior and the diet nudge, and is what ranking uses.
    """
    favourite_sets = {
        account_id: set(ids) for account_id, ids in (favourites or {}).items()
    }

    for candidate in candidates:
        if not candidate.vector:
            raise ValueError(
                f"{candidate.name} has no vector to score against; retrieval "
                f"must populate Candidate.vector"
            )
        candidate.scores = {
            traveller.account_id: embeddings.cosine(traveller.vector, candidate.vector)
            for traveller in travellers
        }
        candidate.group_score = min(candidate.scores.values())

        # Three signals rather than one. See the weights above for why taste
        # does not dominate while embedding_text is templated.
        candidate.taste_score = candidate.group_score
        candidate.popularity_prior = popularity_prior(candidate)
        candidate.style_score = style_affinity(candidate, travellers)

        # The popularity and style boosts are scaled by how fairly the place
        # lands across the group. Without this, fame rescues a candidate one
        # member actively dislikes, and a famous nightclub outranks the quiet
        # museum everybody was happy with - which is exactly the failure the
        # maximin floor exists to prevent. Solo travellers are unaffected.
        best_member = max(candidate.scores.values()) if candidate.scores else 0.0
        if len(travellers) > 1 and best_member > 0:
            fairness = max(0.0, candidate.group_score / best_member)
        else:
            fairness = 1.0

        candidate.final_score = (
            TASTE_WEIGHT * candidate.taste_score
            + fairness
            * (
                POPULARITY_WEIGHT * candidate.popularity_prior
                + STYLE_WEIGHT * candidate.style_score
            )
            + _diet_bonus(candidate, travellers)
        )
        candidate.favourite_of = tuple(
            account_id
            for account_id, ids in favourite_sets.items()
            if candidate.location_id in ids
        )
    return list(candidates)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_pool(
    candidates: Sequence[Candidate],
    travellers: Sequence[Traveller],
    target: int,
    quota_per_member: int = DEFAULT_QUOTA_PER_MEMBER,
    max_category_share: float = MAX_CATEGORY_SHARE,
) -> list[Candidate]:
    """Choose `target` candidates: fair floor, personal quota, mixed categories.

    Order of operations matters. Quotas are filled first, because a reservation
    that only gets what is left over is not a reservation. The remainder is
    filled by final_score. The category cap applies throughout, so one member's
    quota cannot be five churches either.

    Pure: no database, no network. This is the part worth testing exhaustively.
    """
    if target <= 0:
        return []

    cap = max(1, math.ceil(target * max_category_share))
    chosen: dict[UUID, Candidate] = {}
    per_category: dict[str, int] = {}

    def room_for(candidate: Candidate) -> bool:
        return per_category.get(candidate.category, 0) < cap

    def take(candidate: Candidate, reason: str) -> bool:
        if candidate.location_id in chosen or not room_for(candidate):
            return False
        candidate.reason = reason
        chosen[candidate.location_id] = candidate
        per_category[candidate.category] = per_category.get(candidate.category, 0) + 1
        return True

    # Pass 1: each member's own favourites, best-for-them first. Round robin so
    # an earlier member cannot exhaust the category budget before a later one
    # is served.
    if quota_per_member > 0 and travellers:
        ranked_per_member = {
            traveller.account_id: sorted(
                candidates,
                key=lambda c, a=traveller.account_id: c.scores.get(a, 0.0),
                reverse=True,
            )
            for traveller in travellers
        }
        cursors = {traveller.account_id: 0 for traveller in travellers}
        taken = {traveller.account_id: 0 for traveller in travellers}

        for _ in range(quota_per_member):
            for traveller in travellers:
                account_id = traveller.account_id
                if taken[account_id] >= quota_per_member:
                    continue
                ranked = ranked_per_member[account_id]
                while cursors[account_id] < len(ranked):
                    candidate = ranked[cursors[account_id]]
                    cursors[account_id] += 1
                    if take(candidate, "quota"):
                        taken[account_id] += 1
                        break
                if len(chosen) >= target:
                    break
            if len(chosen) >= target:
                break

    # Pass 2: fill the rest on the fairness floor.
    for candidate in sorted(candidates, key=lambda c: c.final_score, reverse=True):
        if len(chosen) >= target:
            break
        take(candidate, "shared")

    # Pass 3: the category cap can leave the pool short on a lopsided corpus.
    # A smaller pool is better than a dishonest one, but an empty tail is
    # worse than a relaxed cap, so relax it rather than return too little.
    if len(chosen) < target:
        for candidate in sorted(candidates, key=lambda c: c.final_score, reverse=True):
            if len(chosen) >= target:
                break
            if candidate.location_id not in chosen:
                candidate.reason = "cap_relaxed"
                chosen[candidate.location_id] = candidate

    return sorted(chosen.values(), key=lambda c: c.final_score, reverse=True)


def effective_score(candidate: Candidate) -> float:
    """What a candidate should be ordered by, whatever stages have run.

    The LLM judgement when there is one, on the same 0..1 scale as the cheap
    blend so the two can be compared. Every ordering must go through this, or a
    later stage silently re-sorts by the cheap score and throws away the
    judgement that was just paid for.
    """
    if candidate.llm_score is not None:
        return candidate.llm_score / 100.0
    return candidate.final_score


def pick_across_countries(
    candidates: Sequence[Candidate],
    limit: int,
    per_country: int = 1,
) -> list[Candidate]:
    """Best candidates, spread over as many countries as possible.

    Similarity search has no reason to spread out: if the corpus holds more of
    one country than another, the whole top-k comes from it, and a "pick the
    place" shelf showing six Austrian attractions is not a choice. Taking the
    best from each country in turn gives the same ranking quality with a
    shelf that is actually browsable.

    Falls back to filling from what is left rather than returning short - a
    corpus with only two countries should still return `limit` rows.
    """
    if limit <= 0:
        return []

    by_country: dict[str, list[Candidate]] = {}
    for candidate in sorted(candidates, key=effective_score, reverse=True):
        key = (candidate.country or candidate.region or "").strip().lower() or "?"
        by_country.setdefault(key, []).append(candidate)

    # Countries ordered by their single best candidate, so the strongest match
    # overall still leads the shelf.
    order = sorted(
        by_country, key=lambda k: effective_score(by_country[k][0]), reverse=True
    )

    chosen: list[Candidate] = []
    for round_index in range(per_country):
        for key in order:
            bucket = by_country[key]
            if round_index < len(bucket):
                chosen.append(bucket[round_index])
                if len(chosen) >= limit:
                    return sorted(chosen, key=effective_score, reverse=True)

    # Still short: the corpus does not span enough countries. Top up on score.
    if len(chosen) < limit:
        taken = {c.location_id for c in chosen}
        for candidate in sorted(candidates, key=effective_score, reverse=True):
            if candidate.location_id not in taken:
                chosen.append(candidate)
                if len(chosen) >= limit:
                    break
    # Always ordered by what the card shows, never by the round-robin order.
    return sorted(chosen, key=effective_score, reverse=True)


@dataclass
class Destination:
    """A place you would book a trip to, rather than a single building.

    "Pick the place" means picking a city, not a museum: a card offering the
    Kunsthistorisches Museum is not a holiday, and cannot be compared against
    another city on any axis a traveller cares about. Individual places become
    the evidence for a destination rather than the recommendation itself.
    """

    city: str
    country: str
    #: The places that argue for it, best first. Also what a route is built
    #: from once the traveller picks this destination.
    places: list[Candidate] = field(default_factory=list)
    score: float = 0.0
    llm_score: Optional[int] = None
    llm_reason: Optional[str] = None

    @property
    def picture(self) -> Optional[str]:
        for place in self.places:
            if place.picture:
                return place.picture
        return None

    @property
    def categories(self) -> list[str]:
        """Distinct categories present, commonest first - what there is to do."""
        counts: dict[str, int] = {}
        for place in self.places:
            counts[place.category] = counts.get(place.category, 0) + 1
        return [c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1])]

    @property
    def label(self) -> str:
        return f"{self.city}, {self.country}" if self.country else self.city


def top_destinations(
    country: Optional[str] = None,
    days: int = 4,
    limit: int = 25,
    min_places: Optional[int] = None,
) -> list[Destination]:
    """Cities with enough worth doing to sustain a trip, straight from SQL.

    Deliberately not a vector search. "Which destinations are worth going to"
    is an aggregate question about a whole city, and nearest-neighbour is the
    wrong instrument for it: pulling the 400 rows closest to a taste vector
    spreads them across hundreds of cities, so none reaches a sensible floor
    and the shelf comes back empty. It is also the wrong instrument *here* in
    particular, because templated embedding_text makes those 400 rows an
    arbitrary 400 out of 6.8 million.

    So candidate destinations are generated by counting, and personalisation
    happens afterwards - the LLM judge reads the traveller and re-ranks. That
    ordering also means the shelf still works when the vector is useless.
    """
    floor = min_places if min_places is not None else max(3, days * 2)

    conditions = [
        Location.vec.is_not(None),
        Location.city.is_not(None),
        Location.category.not_in(EXCLUDED_CATEGORIES),
        or_(
            Location.wikidata_qid.is_not(None),
            Location.wikipedia_title.is_not(None),
            Location.picture.is_not(None),
        ),
    ]
    if country:
        conditions.append(
            or_(
                func.lower(Location.country) == country.lower(),
                func.lower(Location.region) == country.lower(),
            )
        )

    with session_scope() as session:
        rows = session.execute(
            select(
                Location.city,
                Location.country,
                func.count().label("places"),
                func.sum(func.coalesce(Location.wikipedia_pageviews, 0)).label("views"),
            )
            .where(*conditions)
            .group_by(Location.city, Location.country)
            .having(func.count() >= floor)
            # Total attention the city commands, then sheer volume of things to
            # do. Both are proxies for "somewhere people actually go".
            .order_by(func.sum(func.coalesce(Location.wikipedia_pageviews, 0)).desc(),
                      func.count().desc())
            .limit(limit)
        ).all()

        destinations: list[Destination] = []
        for city, city_country, places, views in rows:
            # The evidence: the best-known places there, which are also what a
            # route through the city would be built from.
            best = session.scalars(
                select(Location)
                .where(
                    *conditions,
                    Location.city == city,
                    Location.country == city_country,
                )
                .order_by(
                    Location.wikipedia_pageviews.desc().nullslast(),
                    Location.wikidata_sitelinks.desc().nullslast(),
                )
                .limit(max(12, days * 4))
            ).all()

            destination = Destination(city=city, country=city_country or "")
            destination.places = [_to_candidate(row) for row in best]
            destinations.append(destination)

    return destinations


def group_destinations(
    candidates: Sequence[Candidate],
    days: int = 4,
    min_places: Optional[int] = None,
) -> list[Destination]:
    """Roll scored places up into destinations worth a trip.

    A destination has to sustain the traveller's usual holiday, so somewhere
    with one excellent museum and nothing else is not offered - it would be a
    good afternoon and a bad week. `min_places` defaults to roughly two things
    a day, which is a slow pace and therefore a conservative floor.

    Scored on the mean of its best `days * 2` places rather than all of them:
    a city with four superb sights and two hundred bus shelters should not be
    dragged down by the shelters, and a city with one superb sight should not
    be lifted by it alone.
    """
    floor = min_places if min_places is not None else max(3, days * 2)

    grouped: dict[tuple[str, str], Destination] = {}
    for candidate in candidates:
        city = (candidate.city or candidate.region or "").strip()
        if not city:
            continue
        key = (city.lower(), (candidate.country or "").lower())
        destination = grouped.get(key)
        if destination is None:
            destination = Destination(city=city, country=candidate.country or "")
            grouped[key] = destination
        destination.places.append(candidate)

    out: list[Destination] = []
    for destination in grouped.values():
        destination.places.sort(key=effective_score, reverse=True)
        if len(destination.places) < floor:
            continue

        best = destination.places[: max(1, days * 2)]
        mean = sum(effective_score(p) for p in best) / len(best)

        # Somewhere with several kinds of thing to do sustains a trip better
        # than somewhere with six of the same. Small, so it breaks ties rather
        # than overturning quality.
        variety = min(len(destination.categories), 4) / 4.0
        destination.score = mean * 0.9 + variety * 0.1
        out.append(destination)

    out.sort(key=lambda d: d.score, reverse=True)
    return out


def rank_cities(candidates: Sequence[Candidate]) -> list[tuple[str, int, float]]:
    """(city, candidate count, mean final score), best first.

    What lets the planner offer a single-city plan and a multi-city one from a
    single pool: a city needs both enough to do and a good average fit, and
    ordering on the mean alone would hand back a village with one perfect cafe.
    """
    buckets: dict[str, list[float]] = {}
    for candidate in candidates:
        key = candidate.city or candidate.region
        if not key:
            continue
        buckets.setdefault(key, []).append(candidate.final_score)

    ranked = [
        (city, len(scores), sum(scores) / len(scores))
        for city, scores in buckets.items()
    ]
    # Count first, mean second: somewhere with four good options beats
    # somewhere with one great one when there are days to fill.
    ranked.sort(key=lambda row: (row[1] >= 3, row[2]), reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# The one call the API makes
# ---------------------------------------------------------------------------


def build_pool(
    country: Optional[str],
    account_ids: Sequence[UUID],
    target: int = 45,
    per_member_k: int = PER_MEMBER_K,
    quota_per_member: int = DEFAULT_QUOTA_PER_MEMBER,
    city: Optional[str] = None,
    one_per_country: bool = False,
    notable_only: bool = False,
    include_proposals: bool = False,
) -> Pool:
    """Everything above, in order, for one country and one set of travellers.

    `one_per_country` swaps the usual score-based selection for a spread across
    countries. It has to happen *here* rather than to the finished pool: on a
    lopsided corpus the score-based pass fills every slot with the dominant
    country before anything downstream gets a chance to spread it out.
    """
    travellers = load_travellers(account_ids)
    candidates, favourites, examined = retrieve(
        country, travellers, per_member_k, city, notable_only
    )

    # Second source, unioned rather than substituted. Vector search knows what
    # is in the database; a model knows which places people have actually heard
    # of. While embedding_text is templated the first is nearly blind, and this
    # is what puts the Alcazar in a Seville plan instead of the nearest bench.
    grounding = None
    if include_proposals and country:
        reference = travellers[0]
        named = proposals.propose(
            country=country,
            city=city,
            profile=reference.paragraph,
            styles=reference.travel_styles,
        )
        grounded, grounding = proposals.resolve(named, country, city)
        known = {c.location_id for c in candidates}
        for row in grounded:
            if row.location_id in known:
                # Already retrieved; keep the vector path's copy rather than
                # duplicating, but remember the model vouched for it.
                continue
            candidate = _to_candidate(row)
            candidate.reason = "proposed"
            candidate.llm_reason = grounding.matched.get(str(row.location_id)) or None
            candidates.append(candidate)
            examined += 1

    if not candidates:
        raise EmptyPool(
            f"no embedded locations in "
            f"{city + ', ' + country if city and country else country or 'the corpus'}"
            f" survived the filters (examined {examined} rows)"
        )

    scored = score_candidates(candidates, travellers, favourites)
    if one_per_country:
        selected = pick_across_countries(scored, target, per_country=1)
    else:
        selected = select_pool(
            scored, travellers, target=target, quota_per_member=quota_per_member
        )

    notes: list[str] = []
    if any(t.needs_step_free for t in travellers):
        notes.append(
            "Filtered to locations tagged wheelchair accessible. Places with no "
            "accessibility tag were excluded, so the pool is smaller than the "
            "corpus and some suitable places may be missing."
        )
    dietary = sorted({need for t in travellers for need in t.dietary})
    if dietary:
        notes.append(
            "Dietary needs (" + ", ".join(dietary) + ") are not filterable from "
            "the corpus and are passed to the planner as constraints. No "
            "restaurant here is verified as suitable."
        )

    # Scoring is done; the embeddings have no further use and would otherwise
    # be carried through planning and serialization.
    for candidate in selected:
        candidate.vector = []

    return Pool(
        country=country,
        travellers=list(travellers),
        candidates=selected,
        proposal_hit_rate=grounding.hit_rate if grounding else None,
        proposals_used=sum(1 for c in selected if c.reason == "proposed"),
        cities=rank_cities(selected),
        considered=examined,
        notes=notes,
    )
