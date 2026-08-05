"""TIO ORM - SQLAlchemy 2.0 models for PostgreSQL + pgvector."""

from __future__ import annotations

import enum
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Generator, Optional

from dotenv import load_dotenv
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

EMBEDDING_DIM = 768

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def get_database_url() -> str:
    """Read DATABASE_URL from the project .env."""
    load_dotenv(ENV_FILE)
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(f"DATABASE_URL is not set in {ENV_FILE}")
    return url


_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker[Session]] = None


def get_engine(echo: bool = False, **kwargs: Any) -> Engine:
    """Lazily built process-wide engine. Never created at import time."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_database_url(), echo=echo, pool_pre_ping=True, future=True, **kwargs
        )
    return _engine


def get_session() -> Session:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """with session_scope() as db: ...  - commits, or rolls back on error."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _hnsw(table: str, column: str) -> Index:
    return Index(
        f"ix_{table}_{column}_hnsw",
        column,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={column: "vector_cosine_ops"},
    )


def _pg_enum(python_enum: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        python_enum,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
    )


class AuthProvider(str, enum.Enum):
    GOOGLE = "google"
    APPLE = "apple"
    FACEBOOK = "facebook"
    EMAIL = "email"


class TripStatus(str, enum.Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    CONFIRMED = "confirmed"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MessageType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    LOCATION = "location"
    ITINERARY = "itinerary"
    SYSTEM = "system"


class ActivityType(str, enum.Enum):
    MUSEUM = "museum"
    RESTAURANT = "restaurant"
    BAR = "bar"
    HOTEL = "hotel"
    TRANSPORT = "transport"
    OUTDOOR = "outdoor"
    LANDMARK = "landmark"
    SHOPPING = "shopping"
    NIGHTLIFE = "nightlife"
    EVENT = "event"
    RELAXATION = "relaxation"
    OTHER = "other"


class BookingStatus(str, enum.Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TicketRequirement(str, enum.Enum):
    NONE = "none"
    FREE = "free"
    PAID = "paid"
    RESERVATION_REQUIRED = "reservation_required"
    GUIDED_TOUR_ONLY = "guided_tour_only"


class GroupRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class FriendshipStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    BLOCKED = "blocked"


class EmbeddingStatus(str, enum.Enum):
    """Queue state for a profile awaiting its vector.

    The Personality row is the job - there is no separate queue table.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class PostVisibility(str, enum.Enum):
    PUBLIC = "public"
    FOLLOWERS = "followers"
    PRIVATE = "private"


# Shared instances for the enums now used by more than one table. A second
# SAEnum with the same `name` is a distinct type object, so create_all emits
# CREATE TYPE for it twice and Postgres rejects the duplicate. Every table
# using these must reference the same object, not call _pg_enum again.
ACTIVITY_TYPE_ENUM = _pg_enum(ActivityType, "activity_type")
EMBEDDING_STATUS_ENUM = _pg_enum(EmbeddingStatus, "embedding_status")


class SoftDeleteMixin:
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, when: Optional[datetime] = None) -> None:
        self.deleted_at = when or datetime.now().astimezone()


class Account(SoftDeleteMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        # An account is reachable exactly one way: an external provider subject
        # (user_id) or a local password. Neither is required of the other.
        CheckConstraint(
            "(auth_provider = 'email' AND password_hash IS NOT NULL)"
            " OR (auth_provider <> 'email' AND user_id IS NOT NULL)",
            name="credentials_match_provider",
        ),
    )

    account_id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL for email/password accounts - there is no external subject id.
    # Postgres allows many NULLs under a UNIQUE constraint.
    user_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    # NULL for provider accounts - Google holds the credential, not us.
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    surname: Mapped[Optional[str]] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    # Google asserts this for us; email signups start False.
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Short self-introduction shown on people cards. Written by the user or
    #: generated from their profile - either way it is theirs to edit.
    bio: Mapped[Optional[str]] = mapped_column(String(300))
    #: Explicit avatar. Falls back to the provider picture in metadata when
    #: unset, so a Google user gets one without doing anything.
    avatar_url: Mapped[Optional[str]] = mapped_column(String(2048))
    # Attribute renamed: `metadata` is reserved on a declarative class.
    account_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    auth_provider: Mapped[AuthProvider] = mapped_column(
        _pg_enum(AuthProvider, "auth_provider"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    personality: Mapped[Optional["Personality"]] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )
    group_memberships: Mapped[list["GroupMember"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(back_populates="sender")
    owned_trips: Mapped[list["Trip"]] = relationship(back_populates="owner")
    posts: Mapped[list["Post"]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )
    post_likes: Mapped[list["PostLike"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    friendships_sent: Mapped[list["Friendship"]] = relationship(
        foreign_keys="Friendship.requester_id",
        back_populates="requester",
        cascade="all, delete-orphan",
    )
    friendships_received: Mapped[list["Friendship"]] = relationship(
        foreign_keys="Friendship.addressee_id",
        back_populates="addressee",
        cascade="all, delete-orphan",
    )
    post_comments: Mapped[list["PostComment"]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Account {self.email}>"


class Personality(Base):
    __tablename__ = "personalities"
    __table_args__ = (_hnsw("personalities", "personality_vector"),)

    personality_id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    discussion_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    dietary_restriction: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    accessibility_needs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    preferred_language: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False, default=list, server_default="{}"
    )

    # Structured traits worth filtering on. Everything softer than this stays
    # in profile_paragraph, where the embedding picks it up - a column per
    # nuance would be a migration every time the questionnaire changes.
    home_city: Mapped[Optional[str]] = mapped_column(String(120))
    home_country: Mapped[Optional[str]] = mapped_column(String(120))
    hobbies: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)), nullable=False, default=list, server_default="{}"
    )
    #: budget / pace stored as plain text rather than Postgres enums: the
    #: Pydantic extraction schema already constrains the values, and an enum
    #: type here would mean a migration to add one option.
    budget_tier: Mapped[Optional[str]] = mapped_column(String(20))
    travel_pace: Mapped[Optional[str]] = mapped_column(String(20))
    travel_styles: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list, server_default="{}"
    )
    #: The prose that gets embedded. Persisted verbatim so a later model
    #: change can re-embed without re-running the LLM and getting new text.
    profile_paragraph: Mapped[Optional[str]] = mapped_column(Text)

    personality_vector: Mapped[Optional[list[float]]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    # Queue state. The row is the job - there is no separate queue table.
    embedding_status: Mapped[EmbeddingStatus] = mapped_column(
        EMBEDDING_STATUS_ENUM,
        nullable=False,
        default=EmbeddingStatus.PENDING,
        server_default=EmbeddingStatus.PENDING.value,
        index=True,
    )
    embedding_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    embedding_error: Mapped[Optional[str]] = mapped_column(Text)
    #: Which model produced the vector, so a migration can find stale rows.
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120))
    #: Bumped when the prefix convention or pooling changes but the model name
    #: does not - embedding_model alone cannot detect that, and a user vector
    #: built under a different convention is silently unusable against the
    #: location corpus.
    embedding_version: Mapped[Optional[str]] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["Account"] = relationship(back_populates="personality")
    history: Mapped[list["History"]] = relationship(
        back_populates="personality", cascade="all, delete-orphan"
    )


class Conversation(SoftDeleteMixin, Base):
    __tablename__ = "conversations"

    chat_id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    chat_picture: Mapped[Optional[str]] = mapped_column(String(2048))

    group: Mapped[Optional["Group"]] = relationship(
        back_populates="conversation", uselist=False
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sent_at",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_chat_id_sent_at", "chat_id", "sent_at"),)

    message_id: Mapped[uuid.UUID] = _uuid_pk()
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.chat_id", ondelete="CASCADE"), nullable=False
    )
    # Null for system / assistant messages.
    sender_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="SET NULL")
    )
    share_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("locations.location_id", ondelete="SET NULL")
    )
    media_url: Mapped[Optional[str]] = mapped_column(String(2048))
    message_text: Mapped[Optional[str]] = mapped_column(Text)
    message_type: Mapped[MessageType] = mapped_column(
        _pg_enum(MessageType, "message_type"), nullable=False, default=MessageType.TEXT
    )
    # ERD column "share".
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    sender: Mapped[Optional["Account"]] = relationship(back_populates="messages")
    shared_location: Mapped[Optional["Location"]] = relationship(
        back_populates="shared_in_messages"
    )


class Group(SoftDeleteMixin, Base):
    __tablename__ = "groups"

    group_id: Mapped[uuid.UUID] = _uuid_pk()
    group_name: Mapped[str] = mapped_column(String(150), nullable=False)
    chat_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("conversations.chat_id", ondelete="SET NULL"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Optional["Conversation"]] = relationship(
        back_populates="group"
    )
    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    trips: Mapped[list["Trip"]] = relationship(back_populates="group")


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "account_id", name="uq_group_members_pair"),
    )

    group_member_id: Mapped[uuid.UUID] = _uuid_pk()
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[GroupRole] = mapped_column(
        _pg_enum(GroupRole, "group_role"), nullable=False, default=GroupRole.MEMBER
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Watermark for unread counts. NULL means they have never opened the
    #: chat, so everything in it is unread.
    last_read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    group: Mapped["Group"] = relationship(back_populates="members")
    account: Mapped["Account"] = relationship(back_populates="group_memberships")


class Location(Base):
    """A real-world place a traveller could go.

    Rows arrive from a batch seed (OpenStreetMap via Overpass, prose from
    Wikipedia) rather than from the API, so most of this table is provenance
    and queue state rather than user data.

    The split that matters: `embedding_text` is a character paragraph in the
    same register as Personality.profile_paragraph, and it is the ONLY thing
    embedded into `vec`. Hard facts - price, hours, wheelchair, cuisine - stay
    in their own columns and act as SQL filters. Embedding the facts alongside
    the prose would put "EUR 22" in the same space as a taste essay and the
    geometry stops meaning anything.
    """

    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name="latitude_range"
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="longitude_range",
        ),
        # The seed is re-runnable because of this: ingest upserts on it rather
        # than inserting, so a second pass over the same bbox updates instead
        # of duplicating the corpus. Both columns are nullable so rows created
        # by the app (no upstream source) are exempt - Postgres permits many
        # NULLs under a unique constraint.
        UniqueConstraint("source", "source_id", name="uq_locations_source_ref"),
        Index("ix_locations_country_name", "country", "name"),
        # Recommendation reads filter by city then category before ranking.
        Index("ix_locations_city_category", "city", "category"),
        # Bounding-box lookups until this becomes PostGIS.
        Index("ix_locations_lat_lon", "latitude", "longitude"),
        Index("ix_locations_wikidata_qid", "wikidata_qid"),
        # Dedupe's fallback pass groups by normalized name.
        Index("ix_locations_name_norm", "name_norm"),
        # The popularity prior sorts on this.
        Index("ix_locations_popularity_score", "popularity_score"),
        # Partial: the embed worker only ever asks for pending rows, and once
        # the corpus is seeded almost none are. A full index on the status
        # column would be mostly 'done' entries nobody queries.
        Index(
            "ix_locations_embedding_pending",
            "location_id",
            postgresql_where=text("embedding_status = 'pending'"),
        ),
        _hnsw("locations", "vec"),
    )

    location_id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Casefolded, accent-stripped, punctuation-free form of `name`. Written by
    #: ingest so dedupe is a SQL join instead of pulling the table into Python.
    name_norm: Mapped[Optional[str]] = mapped_column(String(255))
    country: Mapped[Optional[str]] = mapped_column(String(120))
    city: Mapped[Optional[str]] = mapped_column(String(150))
    #: The region the ingest job queried. `city` comes from addr:city, which is
    #: sparse on OSM POIs; this is the fallback that is always populated.
    region: Mapped[Optional[str]] = mapped_column(String(150))
    address: Mapped[Optional[str]] = mapped_column(String(512))
    # ERD "location: location" - swap for PostGIS Geography(POINT) if you need
    # radius queries.
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    # ---- classification -------------------------------------------------
    #: Coarse bucket, shared with ItineraryItem.activity_type. This is what
    #: the diversity cap counts over when it stops the top 20 from being
    #: twenty museums.
    category: Mapped[ActivityType] = mapped_column(
        ACTIVITY_TYPE_ENUM,
        nullable=False,
        default=ActivityType.OTHER,
        server_default=ActivityType.OTHER.value,
    )
    #: The raw OSM tag value the category was derived from ("viewpoint",
    #: "art_gallery"). Finer than `category` without needing a new enum member.
    subcategory: Mapped[Optional[str]] = mapped_column(String(64))

    # ---- filterable facts ------------------------------------------------
    ticket_ref: Mapped[TicketRequirement] = mapped_column(
        _pg_enum(TicketRequirement, "ticket_requirement"),
        nullable=False,
        default=TicketRequirement.NONE,
    )
    #: OSM's opening_hours is its own DSL ("Tu-Su 09:00-18:00"), not JSON, so
    #: it is stored wrapped: {"raw": "...", "format": "osm"}. Parse on read if
    #: ever needed; parsing at ingest would lose the original.
    opening_hour: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    #: 'yes' | 'no' | 'limited' | 'designated', straight from OSM. A hard
    #: filter in the recommendation query, never a distance term.
    wheelchair: Mapped[Optional[str]] = mapped_column(String(16))
    #: OSM's semicolon-separated cuisine list ("italian;pizza"). Feeds the
    #: dietary hard filter.
    cuisine: Mapped[Optional[str]] = mapped_column(String(255))
    website: Mapped[Optional[str]] = mapped_column(String(512))
    phone: Mapped[Optional[str]] = mapped_column(String(64))
    #: Every upstream tag, mapped or not. Cheap to carry and it means a change
    #: to the tag mapping can be replayed from the database instead of hitting
    #: Overpass for the whole corpus again.
    tags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # ---- provenance ------------------------------------------------------
    #: "osm" for the seed corpus, NULL for rows the app creates itself.
    source: Mapped[Optional[str]] = mapped_column(String(32))
    #: Upstream identity within `source`, e.g. "way/12345".
    source_id: Mapped[Optional[str]] = mapped_column(String(64))
    #: Hash of the upstream prose plus the tags that feed embedding_text. A
    #: refresh only flips embedding_status back to pending when this changes,
    #: so re-ingesting a city does not re-embed a corpus that did not move.
    source_hash: Mapped[Optional[str]] = mapped_column(String(64))
    #: Join key to Wikidata/Wikipedia, and dedupe's primary signal - OSM
    #: returns the same place as both a node and a way, and these agree.
    wikidata_qid: Mapped[Optional[str]] = mapped_column(String(24))
    wikipedia_lang: Mapped[Optional[str]] = mapped_column(String(8))
    wikipedia_title: Mapped[Optional[str]] = mapped_column(String(255))

    # ---- prose and embedding --------------------------------------------
    #: Upstream text as fetched (a Wikipedia intro extract), unedited. Kept
    #: separate from embedding_text so the prompt can be changed and rerun
    #: without re-fetching.
    description: Mapped[Optional[str]] = mapped_column(Text)
    #: The exact paragraph that produced `vec`. Non-negotiable: it is what
    #: lets a model swap re-embed the corpus without re-running the LLM and
    #: getting different text back.
    embedding_text: Mapped[Optional[str]] = mapped_column(Text)
    #: 'llm' when rewritten from prose, 'template' when built from tags alone.
    #: Worth knowing when the retrieval quality of a slice looks off.
    embedding_text_source: Mapped[Optional[str]] = mapped_column(String(16))
    vec: Mapped[Optional[list[float]]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120))
    embedding_version: Mapped[Optional[str]] = mapped_column(String(32))
    #: Queue state, same convention as Personality: the row is the job.
    embedding_status: Mapped[EmbeddingStatus] = mapped_column(
        EMBEDDING_STATUS_ENUM,
        nullable=False,
        default=EmbeddingStatus.PENDING,
        server_default=EmbeddingStatus.PENDING.value,
    )
    embedding_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    embedding_error: Mapped[Optional[str]] = mapped_column(Text)

    # ---- popularity ------------------------------------------------------
    #: Blended 0..1 prior. Content similarity has no notion of "this place is
    #: actually worth going to" and will happily rank an unnamed roadside
    #: bench above the Prado; this is the small-weight correction.
    popularity_score: Mapped[Optional[float]] = mapped_column(Float)
    #: Inputs to popularity_score, kept so the formula can be retuned without
    #: re-fetching. Pageviews are a genuine proxy for public interest.
    wikipedia_pageviews: Mapped[Optional[int]] = mapped_column(Integer)
    wikidata_sitelinks: Mapped[Optional[int]] = mapped_column(SmallInteger)
    #: Reserved for a licensed ratings source. OSM has no ratings, and Google
    #: / TripAdvisor / Yelp all forbid storing theirs, so these stay NULL
    #: until such a source exists - see LocationReview.
    rating: Mapped[Optional[float]] = mapped_column(Float)
    rating_count: Mapped[Optional[int]] = mapped_column(Integer)

    # ---- denormalized rollups (pre-existing columns) ---------------------
    #: Primary image URL, mirrored from LocationImage so a list view does not
    #: need the join. LocationImage remains the source of truth, because a URL
    #: with no licence and no attribution beside it is not safely displayable.
    picture: Mapped[Optional[str]] = mapped_column(String(2048))
    #: Rating rollup: {"rating": 4.4, "count": 812, "sources": [...]}.
    review: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    #: Last time an ingest run saw this POI upstream. A row whose last_seen_at
    #: falls far behind the rest of its region has probably been deleted from
    #: OSM; that is a retirement signal, not something to act on at ingest.
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    itinerary_items: Mapped[list["ItineraryItem"]] = relationship(
        back_populates="location"
    )
    origin_of_trips: Mapped[list["Trip"]] = relationship(
        back_populates="origin_location"
    )
    shared_in_messages: Mapped[list["Message"]] = relationship(
        back_populates="shared_location"
    )
    memories: Mapped[list["TripMemory"]] = relationship(back_populates="location")
    posts: Mapped[list["Post"]] = relationship(back_populates="location")
    images: Mapped[list["LocationImage"]] = relationship(
        back_populates="location",
        cascade="all, delete-orphan",
        order_by="(LocationImage.is_primary.desc(), LocationImage.created_at)",
    )
    reviews: Mapped[list["LocationReview"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Location {self.name!r} {self.source}/{self.source_id}>"


class LocationImage(Base):
    """A picture of a location, with the attribution needed to display it.

    A separate table rather than a URL column because the licence and credit
    are not optional metadata - CC BY-SA images are only usable *with* them,
    so storing a bare URL creates a row that cannot legally be shown.

    Sources are Wikimedia Commons and Wikipedia page images (CC, attributable)
    and OSM's own `image` tag. Deliberately not Google Places photos: their
    terms permit caching place_id and nothing else.
    """

    __tablename__ = "location_images"
    __table_args__ = (
        # url is too long to index directly (btree tops out around 2704
        # bytes), so uniqueness is enforced on its hash.
        UniqueConstraint("location_id", "url_sha256", name="uq_location_images_url"),
        Index("ix_location_images_location_id", "location_id"),
    )

    image_id: Mapped[uuid.UUID] = _uuid_pk()
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    thumb_url: Mapped[Optional[str]] = mapped_column(String(2048))
    #: "wikimedia_commons" | "wikipedia" | "osm"
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: SPDX-ish licence short name, e.g. "CC-BY-SA-4.0", "public-domain".
    license: Mapped[Optional[str]] = mapped_column(String(64))
    #: Ready-to-render credit line.
    attribution: Mapped[Optional[str]] = mapped_column(String(512))
    author: Mapped[Optional[str]] = mapped_column(String(255))
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    location: Mapped["Location"] = relationship(back_populates="images")


class LocationReview(Base):
    """A third-party review of a location.

    Empty by design for now. There is no open review corpus: OSM has none, and
    Google, TripAdvisor and Yelp all prohibit storing review text - that is a
    licensing wall, not a rate limit. Until a licensed feed exists the
    recommendation query uses Location.popularity_score instead, which is
    derived from Wikipedia pageviews and Wikidata sitelinks.

    The table exists so that adding such a feed is an insert rather than a
    migration, and so reviews never get flattened into the embedded paragraph
    - a review is evidence about a place, not a description of its character.
    """

    __tablename__ = "location_reviews"
    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR rating BETWEEN 0 AND 5", name="rating_range"
        ),
        UniqueConstraint("source", "source_review_id", name="uq_location_reviews_ref"),
        Index("ix_location_reviews_location_id", "location_id"),
    )

    review_id: Mapped[uuid.UUID] = _uuid_pk()
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_review_id: Mapped[Optional[str]] = mapped_column(String(128))
    rating: Mapped[Optional[float]] = mapped_column(Float)
    body: Mapped[Optional[str]] = mapped_column(Text)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    lang: Mapped[Optional[str]] = mapped_column(String(8))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    location: Mapped["Location"] = relationship(back_populates="reviews")


class Trip(SoftDeleteMixin, Base):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="date_order",
        ),
        CheckConstraint(
            "budget_limit IS NULL OR budget_limit >= 0", name="budget_non_negative"
        ),
        CheckConstraint(
            "(is_group = false) OR (group_id IS NOT NULL)",
            name="group_trip_needs_group",
        ),
        Index("ix_trips_group_id_start_date", "group_id", "start_date"),
        Index("ix_trips_owner_account_id", "owner_account_id"),
    )

    trip_id: Mapped[uuid.UUID] = _uuid_pk()
    # Who created it. Required, including for group trips - without this a
    # solo trip belongs to nobody and "list my trips" is unanswerable.
    # RESTRICT rather than CASCADE: deleting an account must never silently
    # take its trips (and their History vectors) with it.
    owner_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    origin_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        "origin_location", ForeignKey("locations.location_id", ondelete="SET NULL")
    )
    # Null for a solo trip. SET NULL keeps the trip and its vectors alive even
    # if a group is hard-deleted.
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("groups.group_id", ondelete="SET NULL")
    )
    is_group: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    budget_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    status: Mapped[TripStatus] = mapped_column(
        _pg_enum(TripStatus, "trip_status"), nullable=False, default=TripStatus.DRAFT
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner: Mapped["Account"] = relationship(back_populates="owned_trips")
    group: Mapped[Optional["Group"]] = relationship(back_populates="trips")
    origin_location: Mapped[Optional["Location"]] = relationship(
        back_populates="origin_of_trips"
    )
    posts: Mapped[list["Post"]] = relationship(back_populates="trip")
    itinerary_items: Mapped[list["ItineraryItem"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        order_by="(ItineraryItem.date, ItineraryItem.time)",
    )
    history: Mapped[list["History"]] = relationship(back_populates="trip")


class ItineraryItem(Base):
    __tablename__ = "itinerary_items"
    __table_args__ = (
        CheckConstraint("cost IS NULL OR cost >= 0", name="cost_non_negative"),
        Index("ix_itinerary_items_trip_id_date", "trip_id", "date", "time"),
        _hnsw("itinerary_items", "vec"),
    )

    itinerary_id: Mapped[uuid.UUID] = _uuid_pk()
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.trip_id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("locations.location_id", ondelete="SET NULL")
    )
    date: Mapped[Optional[date]] = mapped_column(Date)
    time: Mapped[Optional[time]] = mapped_column(Time)
    duration: Mapped[Optional[timedelta]] = mapped_column(Interval)
    activity_type: Mapped[ActivityType] = mapped_column(
        ACTIVITY_TYPE_ENUM,
        nullable=False,
        default=ActivityType.OTHER,
    )
    weather_dependent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    booking_status: Mapped[BookingStatus] = mapped_column(
        _pg_enum(BookingStatus, "booking_status"),
        nullable=False,
        default=BookingStatus.NOT_REQUIRED,
    )
    booking_ref: Mapped[Optional[str]] = mapped_column(String(120))
    cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    description: Mapped[Optional[str]] = mapped_column(Text)
    vec: Mapped[Optional[list[float]]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    trip: Mapped["Trip"] = relationship(back_populates="itinerary_items")
    location: Mapped[Optional["Location"]] = relationship(
        back_populates="itinerary_items"
    )
    memories: Mapped[list["TripMemory"]] = relationship(
        back_populates="itinerary_item", cascade="all, delete-orphan"
    )


class TripMemory(Base):
    __tablename__ = "trip_memories"

    memory_id: Mapped[uuid.UUID] = _uuid_pk()
    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("itinerary_items.itinerary_id", ondelete="CASCADE"), nullable=False
    )
    picture_url: Mapped[Optional[str]] = mapped_column(String(2048))
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        "location", ForeignKey("locations.location_id", ondelete="SET NULL")
    )

    itinerary_item: Mapped["ItineraryItem"] = relationship(back_populates="memories")
    location: Mapped[Optional["Location"]] = relationship(back_populates="memories")


class History(Base):
    """One traveller's rating of one trip.

    vec_snapshot freezes the trip embedding at rating time, so the taste signal
    outlives edits to the itinerary and the group being deleted. The FK to
    trips is RESTRICT to protect that.
    """

    __tablename__ = "history"
    __table_args__ = (
        CheckConstraint(
            "rating_trip IS NULL OR rating_trip BETWEEN 1 AND 10", name="rating_range"
        ),
    )

    personality_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("personalities.personality_id", ondelete="CASCADE"),
        primary_key=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        "trips_id", ForeignKey("trips.trip_id", ondelete="RESTRICT"), primary_key=True
    )
    rating_trip: Mapped[Optional[int]] = mapped_column(SmallInteger)
    review: Mapped[Optional[str]] = mapped_column(Text)
    vec_snapshot: Mapped[Optional[list[float]]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    personality: Mapped["Personality"] = relationship(back_populates="history")
    trip: Mapped["Trip"] = relationship(back_populates="history")


class Post(SoftDeleteMixin, Base):
    """Something a traveller shares - a photo, a note, a finished trip."""

    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint(
            "caption IS NOT NULL OR media_url IS NOT NULL",
            name="post_has_content",
        ),
        CheckConstraint(
            "rating IS NULL OR rating BETWEEN 1 AND 5", name="rating_range"
        ),
        # Feed queries are newest-first, per author and globally.
        Index("ix_posts_account_id_created_at", "account_id", "created_at"),
        Index("ix_posts_created_at", "created_at"),
    )

    post_id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    #: Optional - a post can be about a trip, a place, or neither.
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("trips.trip_id", ondelete="SET NULL")
    )
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("locations.location_id", ondelete="SET NULL")
    )

    caption: Mapped[Optional[str]] = mapped_column(Text)
    media_url: Mapped[Optional[str]] = mapped_column(String(2048))
    #: How the author rated the place, 1-5. Distinct from History.rating_trip,
    #: which scores a whole trip for the recommendation vectors.
    rating: Mapped[Optional[int]] = mapped_column(SmallInteger)
    visibility: Mapped[PostVisibility] = mapped_column(
        _pg_enum(PostVisibility, "post_visibility"),
        nullable=False,
        default=PostVisibility.PUBLIC,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    author: Mapped["Account"] = relationship(back_populates="posts")
    trip: Mapped[Optional["Trip"]] = relationship(back_populates="posts")
    location: Mapped[Optional["Location"]] = relationship(back_populates="posts")
    likes: Mapped[list["PostLike"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    comments: Mapped[list["PostComment"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class PostLike(Base):
    """A like. The composite primary key is what stops double-liking."""

    __tablename__ = "post_likes"

    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.post_id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    post: Mapped["Post"] = relationship(back_populates="likes")
    account: Mapped["Account"] = relationship(back_populates="post_likes")


class Friendship(Base):
    """A mutual connection, requested by one side and accepted by the other.

    Distinct from Follow: following is one-way and needs no consent, whereas
    only accepted friends can be added to a group chat.

    Stored as a single row per pair rather than two, so "are these two
    friends" is one lookup - but that means every read has to check both
    (requester, addressee) orderings.
    """

    __tablename__ = "friendships"
    __table_args__ = (
        CheckConstraint(
            "requester_id <> addressee_id", name="no_self_friendship"
        ),
        # "Who wants to be my friend" - the primary key only orders by
        # requester, so the inbox query needs its own index.
        Index("ix_friendships_addressee_status", "addressee_id", "status"),
    )

    requester_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), primary_key=True
    )
    addressee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[FriendshipStatus] = mapped_column(
        _pg_enum(FriendshipStatus, "friendship_status"),
        nullable=False,
        default=FriendshipStatus.PENDING,
        server_default=FriendshipStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    responded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    requester: Mapped["Account"] = relationship(
        foreign_keys=[requester_id], back_populates="friendships_sent"
    )
    addressee: Mapped["Account"] = relationship(
        foreign_keys=[addressee_id], back_populates="friendships_received"
    )


class PostComment(Base):
    """A comment on a post.

    Hard-deleted rather than soft: nothing references a comment, so keeping
    tombstones would only complicate the count on every feed row.
    """

    __tablename__ = "post_comments"
    __table_args__ = (
        Index("ix_post_comments_post_id_created_at", "post_id", "created_at"),
    )

    comment_id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.post_id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    post: Mapped["Post"] = relationship(back_populates="comments")
    author: Mapped["Account"] = relationship(back_populates="post_comments")


__all__ = [
    "Base",
    "EMBEDDING_DIM",
    "ENV_FILE",
    "get_database_url",
    "get_engine",
    "get_session",
    "session_scope",
    "Account",
    "Personality",
    "Conversation",
    "Message",
    "Group",
    "GroupMember",
    "Location",
    "LocationImage",
    "LocationReview",
    "Trip",
    "ItineraryItem",
    "TripMemory",
    "History",
    "Post",
    "PostLike",
    "PostComment",
    "Friendship",
    "AuthProvider",
    "TripStatus",
    "MessageType",
    "ActivityType",
    "BookingStatus",
    "TicketRequirement",
    "GroupRole",
    "PostVisibility",
    "EmbeddingStatus",
    "FriendshipStatus",
    "ACTIVITY_TYPE_ENUM",
    "EMBEDDING_STATUS_ENUM",
]
