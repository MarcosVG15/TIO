from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from DATABASE.ORM import Account, Personality, session_scope
from user_extraction import ExtractedProfile, UserExtractor
from user_processing import UserProcessor


@dataclass
class OnboardingResult:
    """What onboard_user hands back to the caller."""

    account_id: UUID
    personality_id: UUID
    vector_pending: bool


class Pipeline:

    def __init__(self):
        """Instantiate the embedder once and reuse it across calls.

        No engine is built here - session_scope() in ORM.py already owns
        the engine and session factory.
        """
        ...


    def onboard_user(
        self,
        account_id: UUID,
        questionnaire: dict,
        conversation: list[dict],
    ) -> OnboardingResult:
        """Public entry point. Runs stages 1-3 in order and returns once the
        profile is durably committed.

        account_id comes from the caller's verified session token, never from
        a request body - so a client cannot onboard as somebody else.

        Does NOT wait for the embedding - that happens later in the worker.
        Everything before the commit is disposable; everything after it is
        retryable.
        """
        raise NotImplementedError("Pipeline.onboard_user is not implemented yet")

    def _load_account(self, session, account_id: UUID) -> Account:
        """Fetch the Account the session token points at.

        Sign-in already created it (accounts.get_or_create_from_google), so a
        miss here means the row was deleted mid-request.
        """
        ...

    def _extract_profile(
        self,
        questionnaire: dict,
        conversation: list[dict],
    ) -> ExtractedProfile:
        """Stage 1. Hand the raw intake to UserExtractor and get back a
        validated profile. Pure - touches no database.

        Slow and paid, so it happens before the transaction opens, never
        inside it.
        """
        ...

    def _persist_profile(
        self,
        session,
        account: Account,
        profile: ExtractedProfile,
    ) -> Personality:
        """Stage 2. Write the Personality row: hard constraints into their
        own columns, profile_paragraph stored verbatim, personality_vector
        left NULL.

        The paragraph must be persisted exactly as embedded, so a future
        model migration can re-embed without re-running the LLM.
        """
        ...

    def _enqueue_embedding(self, session, personality: Personality) -> None:
        """Stage 3. Mark the row as needing a vector by setting
        embedding_status = 'pending'.

        The row is the job - there is no separate queue table.
        """
        ...

    # ------------------------------------------------------------------
    # Worker path (dequeue side)
    # ------------------------------------------------------------------

    def run_embedding_worker(self, poll_interval: float = 5.0) -> None:
        """Long-running loop. Calls process_next_embedding() until there is
        no work, sleeps for poll_interval, repeats.

        Run as a separate process from the API.
        """
        ...

    def process_next_embedding(self) -> bool:
        """Claim one pending row, embed it, write the result.

        Returns True if a row was processed, False if the queue was empty -
        that is what tells the worker loop when to sleep.
        """
        ...

    def _claim_pending(self, session) -> Optional[Personality]:
        """Atomically take the oldest pending row and flip it to 'processing'.

        Uses SELECT ... FOR UPDATE SKIP LOCKED so two workers can never grab
        the same row. Returns None when the queue is empty.
        """
        ...

    def _write_vector(
        self,
        session,
        personality: Personality,
        vector: list[float],
    ) -> None:
        """Success path. Store the vector, stamp the model/version used, set
        embedding_status = 'done'.
        """
        ...

    def _record_failure(
        self,
        session,
        personality: Personality,
        error: Exception,
    ) -> None:
        """Failure path. Increment embedding_attempts and save the error.

        Back to 'pending' if attempts remain, otherwise 'failed' so a poison
        row cannot spin forever.
        """
        ...

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def requeue_for_reembedding(self, embedding_version: str) -> int:
        """Bulk-mark every row not on the current embedding version as
        pending, so the existing worker re-embeds them. Returns the count.

        This is the whole model-migration story.
        """
        ...
