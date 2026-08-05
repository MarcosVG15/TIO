from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select

from DATABASE.ORM import (
    Account,
    EmbeddingStatus,
    Personality,
    session_scope,
)
from user_extraction import ExtractedProfile, UserExtractor
from user_processing import UserProcessor

log = logging.getLogger(__name__)


class AccountMissing(Exception):
    """The session token pointed at an account that no longer exists."""


@dataclass
class OnboardingResult:
    """What onboard_user hands back to the caller."""

    account_id: UUID
    personality_id: UUID
    vector_pending: bool


class Pipeline:

    def __init__(self) -> None:
        self.processor = UserProcessor()

    # ------------------------------------------------------------------
    # Onboarding path (enqueue side)
    # ------------------------------------------------------------------

    def onboard_user(
        self,
        account_id: UUID,
        questionnaire: list[dict],
        conversation: list[dict],
    ) -> OnboardingResult:
        """Store the user's profile and queue it for embedding.

        account_id comes from the caller's verified session token, never from
        a request body - a client cannot onboard as somebody else.

        Extraction runs before the transaction opens: it is slow and paid, and
        holding a database transaction across a network call to OpenAI would
        pin a connection for the duration.
        """
        profile = self._extract_profile(questionnaire, conversation)

        with session_scope() as session:
            account = self._load_account(session, account_id)
            personality = self._persist_profile(
                session, account, profile, questionnaire, conversation
            )
            self._enqueue_embedding(personality)
            session.flush()

            return OnboardingResult(
                account_id=account.account_id,
                personality_id=personality.personality_id,
                vector_pending=True,
            )

    def _load_account(self, session, account_id: UUID) -> Account:
        account = session.scalar(
            select(Account).where(
                Account.account_id == account_id,
                Account.deleted_at.is_(None),
            )
        )
        if account is None:
            raise AccountMissing(str(account_id))
        return account

    def _extract_profile(
        self,
        questionnaire: list[dict],
        conversation: list[dict],
    ) -> Optional[ExtractedProfile]:
        """Turn the raw intake into a validated profile.

        Returns None rather than raising if the LLM call fails. Losing a
        user's onboarding because OpenAI had a bad minute is a worse outcome
        than storing the raw answers now and enriching them later - the raw
        intake is kept either way, so this is recoverable.
        """
        try:
            return UserExtractor(questionnaire, conversation).extract()
        except Exception as exc:
            log.warning("extraction failed, storing raw intake only: %s", exc)
            return None

    def _persist_profile(
        self,
        session,
        account: Account,
        profile: Optional[ExtractedProfile],
        questionnaire: list[dict],
        conversation: list[dict],
    ) -> Personality:
        """Create or update the account's Personality row.

        Re-onboarding overwrites rather than creating a second row, because
        personalities.account_id is unique.
        """
        personality = session.scalar(
            select(Personality).where(Personality.account_id == account.account_id)
        )
        if personality is None:
            personality = Personality(account_id=account.account_id)
            session.add(personality)

        # The raw intake is always stored, extracted or not, so a failed
        # extraction can be retried later from the original answers.
        personality.discussion_json = {
            "questionnaire": questionnaire,
            "conversation": conversation,
        }

        if profile is not None:
            personality.dietary_restriction = _as_flags(profile.dietary_restrictions)
            personality.accessibility_needs = _as_flags(profile.accessibility_needs)
            personality.preferred_language = list(profile.preferred_languages)
            personality.profile_paragraph = profile.profile_paragraph

            personality.home_city = profile.home_city
            personality.home_country = profile.home_country
            personality.hobbies = list(profile.hobbies)
            personality.budget_tier = (
                profile.budget_tier.value if profile.budget_tier else None
            )
            personality.travel_pace = (
                profile.travel_pace.value if profile.travel_pace else None
            )
            personality.travel_styles = [s.value for s in profile.travel_styles]

            # Only seed the bio - never overwrite one the user has edited.
            if profile.short_bio and not account.bio:
                account.bio = profile.short_bio

        return personality

    def _enqueue_embedding(self, personality: Personality) -> None:
        """Mark the row as needing a vector.

        Nothing is queued when there is no paragraph - the extraction failed,
        so there is nothing to embed until it is retried.
        """
        if not personality.profile_paragraph:
            return
        personality.embedding_status = EmbeddingStatus.PENDING
        personality.embedding_attempts = 0
        personality.embedding_error = None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def profile_status(self, account_id: UUID) -> dict[str, Any]:
        """What the frontend needs to decide between onboarding and home."""
        with session_scope() as session:
            personality = session.scalar(
                select(Personality).where(Personality.account_id == account_id)
            )
            if personality is None:
                return {
                    "onboarding_completed": False,
                    "has_profile": False,
                    "embedding_status": None,
                    "ready": False,
                }
            return {
                # Having answered is what completes onboarding. The vector
                # arriving later must not send the user back through it.
                "onboarding_completed": True,
                "has_profile": True,
                "embedding_status": personality.embedding_status.value,
                "ready": personality.personality_vector is not None,
            }

    # ------------------------------------------------------------------
    # Worker path (dequeue side)
    # ------------------------------------------------------------------

    def run_embedding_worker(self, poll_interval: float = 5.0) -> None:
        """Long-running loop. Runs as its own process."""
        raise NotImplementedError("the embedding worker is not implemented yet")


def _as_flags(values) -> dict[str, bool]:
    """Enum list -> {"vegan": true} JSONB.

    A map rather than an array so Postgres can index individual keys, which
    is how the hard-constraint filters will be written.
    """
    return {getattr(v, "value", str(v)): True for v in values}
