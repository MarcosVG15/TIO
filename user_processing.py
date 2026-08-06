"""Turn a stored profile into the vector the recommender matches against.

What gets embedded is the profile paragraph and nothing else. That is the whole
design, and it is the easiest part to get wrong: the obvious move is to embed
the profile record - hobbies, budget tier, dietary flags, the questionnaire
JSON - and it does not work. Location vectors come from a prose paragraph about
what a place is like to visit, so a user vector has to be prose in the same
register, or the two sit in unrelated regions of the space and the distances
stop meaning anything.

The hard facts are not lost, they are just not embedded. Dietary restrictions,
accessibility needs, budget and pace live in their own columns and act as SQL
filters and prompt constraints, where an exact answer is wanted rather than a
nearest neighbour.

See `embeddings` for the model and the space guard.
"""

from __future__ import annotations

from typing import Optional, Sequence

import embeddings
from embeddings import Space


class UserProcessor:
    """Embeds profile paragraphs.

    Holds no state beyond the space it was built for, so it is cheap to
    construct per request and safe to keep for the life of a worker.
    """

    def __init__(self) -> None:
        self._space = embeddings.space()

    @property
    def space(self) -> Space:
        """Which model this instance produces vectors with.

        Written to `Personality.embedding_model` / `embedding_version` so that
        a later model change can find the rows it invalidated.
        """
        return self._space

    def embed_profile(self, paragraph: str) -> list[float]:
        """One paragraph to one unit vector."""
        return embeddings.embed_one(paragraph)

    def embed_profiles(self, paragraphs: Sequence[str]) -> list[list[float]]:
        """Batched form, results in the same order as the input."""
        return embeddings.embed(paragraphs)

    @staticmethod
    def paragraph_of(personality) -> Optional[str]:
        """The text to embed for a Personality row, or None if there is none.

        A row without a paragraph is not an error - extraction is allowed to
        fail and leave the raw intake for a later retry - so callers skip it
        rather than recording a failure against it.
        """
        paragraph = getattr(personality, "profile_paragraph", None)
        if paragraph and paragraph.strip():
            return paragraph
        return None
