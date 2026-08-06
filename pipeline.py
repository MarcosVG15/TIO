from __future__ import annotations

import logging
import signal
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence
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


class _StopFlag:
    """Lets `docker compose stop` end the worker between batches.

    Without this, SIGTERM kills the process wherever it happens to be, which
    can be after a paid embedding call but before the vector is written - the
    row goes back to pending and the call is paid for twice. An Event rather
    than a bare bool so the idle wait is interruptible: the container stops in
    milliseconds instead of after a full poll interval.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        for received in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(received, self._handle)
            except ValueError:
                # Not the main thread - the caller is embedding in-process
                # (a test, or a one-off script) and owns its own lifecycle.
                pass

    def _handle(self, signum, _frame) -> None:
        log.info("signal %s received, finishing the current batch", signum)
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> None:
        self._event.wait(seconds)


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

    def run_embedding_worker(
        self,
        poll_interval: float = 5.0,
        batch_size: int = 16,
        max_attempts: int = 3,
        stale_after: timedelta = timedelta(minutes=15),
    ) -> None:
        """Long-running loop. Runs as its own process.

        Sleeps only when there was nothing to do, so a backlog drains at full
        speed instead of one batch per poll interval.
        """
        stop = _StopFlag()
        log.info(
            "embedding worker started (model=%s)", self.processor.space.describe()
        )

        # A crash between claiming a row and writing its vector leaves the row
        # in 'processing' with nobody working on it. Recover those on startup,
        # otherwise they are stuck until someone notices by hand.
        recovered = self.requeue_stale_processing(stale_after)
        if recovered:
            log.info("requeued %d row(s) abandoned by a previous run", recovered)

        while not stop.requested:
            try:
                done = self.embed_pending_batch(batch_size, max_attempts)
            except Exception:
                # A loop that dies on one bad batch stops embedding for
                # everybody. Log it and carry on; the row's own attempt
                # counter is what eventually gives up on it.
                log.exception("embedding batch failed")
                done = 0

            if done == 0:
                stop.wait(poll_interval)

        log.info("embedding worker stopped")

    def embed_pending_batch(self, batch_size: int = 16, max_attempts: int = 3) -> int:
        """Embed one batch of pending profiles. Returns how many were written.

        Three transactions on purpose. The claim commits before the API call so
        that a slow OpenAI response is not holding a row lock, and the write is
        separate so a failure marks the row rather than rolling back the claim.
        """
        claimed = self._claim_pending(batch_size)
        if not claimed:
            return 0

        space = self.processor.space
        try:
            vectors = self.processor.embed_profiles([text for _, text in claimed])
        except Exception as exc:
            # The whole batch shares one API call, so one failure fails all of
            # them. Attempts are per row, so a persistently bad row is retried
            # a bounded number of times and then parked as 'failed'.
            log.warning("embedding call failed for %d row(s): %s", len(claimed), exc)
            self._record_failure([pid for pid, _ in claimed], str(exc), max_attempts)
            return 0

        with session_scope() as session:
            for (personality_id, _), vector in zip(claimed, vectors):
                personality = session.get(Personality, personality_id)
                if personality is None:
                    continue  # deleted while we were embedding
                personality.personality_vector = vector
                personality.embedding_model = space.model
                personality.embedding_version = space.version
                personality.embedding_status = EmbeddingStatus.DONE
                personality.embedding_error = None

        log.info("embedded %d profile(s)", len(claimed))
        return len(claimed)

    def _claim_pending(self, batch_size: int) -> list[tuple[UUID, str]]:
        """Take ownership of up to `batch_size` pending rows.

        SKIP LOCKED is what makes it safe to run more than one worker: two
        processes claiming concurrently get disjoint batches instead of one
        blocking on the other.
        """
        with session_scope() as session:
            rows = session.scalars(
                select(Personality)
                .where(
                    Personality.embedding_status == EmbeddingStatus.PENDING,
                    Personality.profile_paragraph.is_not(None),
                )
                .order_by(Personality.created_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            ).all()

            claimed: list[tuple[UUID, str]] = []
            for personality in rows:
                paragraph = self.processor.paragraph_of(personality)
                if paragraph is None:
                    # Queued without a paragraph: nothing to embed, and leaving
                    # it pending would spin the worker on it forever.
                    personality.embedding_status = EmbeddingStatus.FAILED
                    personality.embedding_error = "no profile_paragraph to embed"
                    continue
                personality.embedding_status = EmbeddingStatus.PROCESSING
                claimed.append((personality.personality_id, paragraph))

            return claimed

    def _record_failure(
        self,
        personality_ids: Sequence[UUID],
        error: str,
        max_attempts: int,
    ) -> None:
        """Count the attempt, and park the row once it has had enough."""
        with session_scope() as session:
            for personality_id in personality_ids:
                personality = session.get(Personality, personality_id)
                if personality is None:
                    continue
                personality.embedding_attempts += 1
                personality.embedding_error = error[:2000]
                personality.embedding_status = (
                    EmbeddingStatus.FAILED
                    if personality.embedding_attempts >= max_attempts
                    else EmbeddingStatus.PENDING
                )

    def requeue_stale_processing(self, older_than: timedelta) -> int:
        """Return abandoned 'processing' rows to the queue.

        Uses updated_at as the proxy for when the row was claimed, since
        claiming is the last thing that touched it.
        """
        cutoff = datetime.now(timezone.utc) - older_than
        with session_scope() as session:
            rows = session.scalars(
                select(Personality).where(
                    Personality.embedding_status == EmbeddingStatus.PROCESSING,
                    Personality.updated_at < cutoff,
                )
            ).all()
            for personality in rows:
                personality.embedding_status = EmbeddingStatus.PENDING
            return len(rows)


def _as_flags(values) -> dict[str, bool]:
    """Enum list -> {"vegan": true} JSONB.

    A map rather than an array so Postgres can index individual keys, which
    is how the hard-constraint filters will be written.
    """
    return {getattr(v, "value", str(v)): True for v in values}
