"""Text to vector, and the guard that keeps two vectors comparable.

One model embeds everything. A user paragraph and a location paragraph are only
comparable if they were produced by the same model with the same settings, so
this module is the single place that decides what those are, and the single
place that can answer "were these two vectors made the same way".

That guard is not defensive padding. Cosine similarity between vectors from
different models does not error - it returns a plausible-looking number that
means nothing, and the failure surfaces as "the recommendations are bad"
weeks later. Every read path checks the space first and refuses instead.

Configuration, because the location corpus was embedded before this module
existed and its model is whatever seeded it:

    EMBEDDING_MODEL     default "text-embedding-3-small"
    EMBEDDING_VERSION   default derived from the model and dimension

Find out what the corpus actually used with:

    SELECT embedding_model, embedding_version, vector_dims(vec), count(*)
    FROM locations WHERE vec IS NOT NULL GROUP BY 1,2,3;

and set the environment to match before embedding a single user.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional, Sequence

from dotenv import load_dotenv
from openai import OpenAI

from DATABASE.ORM import EMBEDDING_DIM

load_dotenv()

#: Both text-embedding-3 models accept a `dimensions` argument and re-normalize
#: the truncated result, so either can produce the 768 the schema is built for.
DEFAULT_MODEL = "text-embedding-3-small"

#: Anything that changes the geometry without changing the model name belongs
#: here - the output dimension, and any reshaping of the input text. A vector
#: built under a different convention is silently unusable against the corpus,
#: which is exactly what `embedding_version` exists to detect.
def _default_version(model: str) -> str:
    return f"openai-d{EMBEDDING_DIM}"


#: The API tops out around 8191 tokens per input. Truncating on characters
#: avoids a tokenizer dependency for a limit no profile paragraph approaches -
#: a 150-word paragraph is about 200 tokens.
MAX_CHARS = 24_000

#: Conservative. The API permits far more per call, but a smaller batch fails
#: smaller and retries cheaper.
MAX_BATCH = 128


class EmbeddingError(Exception):
    """The embedding provider could not be used."""


class SpaceMismatch(Exception):
    """Two sets of vectors were not produced the same way.

    Raised instead of returning a similarity that would be meaningless.
    """


@dataclass(frozen=True)
class Space:
    """Which model produced a vector, and how."""

    model: str
    version: str
    dim: int

    def describe(self) -> str:
        return f"{self.model} / {self.version} / {self.dim}d"


def space() -> Space:
    model = os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL
    version = os.getenv("EMBEDDING_VERSION") or _default_version(model)
    return Space(model=model, version=version, dim=EMBEDDING_DIM)


def require_same_space(
    model: Optional[str],
    version: Optional[str],
    *,
    what: str,
) -> None:
    """Refuse to go on if `what` was embedded differently from what we produce.

    A NULL model or version means the row predates this bookkeeping. That is
    treated as a mismatch rather than waved through: an unlabelled vector is
    exactly the case this guard exists to catch.
    """
    ours = space()
    if model == ours.model and version == ours.version:
        return
    raise SpaceMismatch(
        f"{what} was embedded with "
        f"{model or 'an unrecorded model'} / {version or 'unrecorded version'}, "
        f"but this process produces {ours.describe()}. Comparing them would "
        f"return meaningless similarities. Set EMBEDDING_MODEL and "
        f"EMBEDDING_VERSION to match the corpus, or re-embed."
    )


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """Lazily built, so importing this module never requires a key."""
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise EmbeddingError("OPENAI_API_KEY is not set")
        # More retries than the SDK default: a transient 429 during a corpus
        # pass should cost seconds, not a failed row.
        _client = OpenAI(api_key=key, max_retries=5)
    return _client


def _prepare(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        # A zero vector is not a neutral answer - it sits at cosine 0 from
        # everything and quietly poisons every ranking it enters.
        raise ValueError("refusing to embed empty text")
    return cleaned[:MAX_CHARS]


def normalize(vector: Sequence[float]) -> list[float]:
    """Scale to unit length, so cosine similarity is a plain dot product.

    OpenAI already returns unit vectors, including for truncated dimensions,
    which makes this a no-op on their output. It is here so that similarity
    stays correct if a vector ever arrives from somewhere else.
    """
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0:
        raise ValueError("cannot normalize a zero vector")
    return [component / magnitude for component in vector]


def embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed several texts, preserving order.

    Batched, because one call per row turns a corpus pass into an afternoon
    of round trips.
    """
    if not texts:
        return []

    prepared = [_prepare(text) for text in texts]
    ours = space()
    client = _get_client()
    out: list[list[float]] = []

    for start in range(0, len(prepared), MAX_BATCH):
        chunk = prepared[start : start + MAX_BATCH]
        try:
            response = client.embeddings.create(
                model=ours.model,
                input=list(chunk),
                dimensions=ours.dim,
            )
        except Exception as exc:  # network, auth, rate limit past retries
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        # The API documents input order preservation, but the index is
        # authoritative and sorting by it costs nothing.
        for item in sorted(response.data, key=lambda d: d.index):
            vector = item.embedding
            if len(vector) != ours.dim:
                raise EmbeddingError(
                    f"expected {ours.dim} dimensions, got {len(vector)} - "
                    f"the model or dimensions setting is wrong"
                )
            out.append(normalize(vector))

    return out


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


# ---------------------------------------------------------------------------
# Similarity
#
# Small enough to do in Python: a few hundred candidates against a handful of
# members is a few hundred thousand multiplications. Not worth a numpy
# dependency, and doing it here keeps the ranking logic testable without a
# database or an API key.
# ---------------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]. 1.0 is identical taste."""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def centroid(vectors: Sequence[Sequence[float]]) -> list[float]:
    """Mean direction of several vectors, unit length.

    Used only to *retrieve* - places near the middle of a group are worth
    having in the candidate pool. It is deliberately not used to rank: the
    mean of a beach lover and a museum lover is a shopping centre, and
    ranking on it is how a group plan ends up pleasing nobody.
    """
    if not vectors:
        raise ValueError("no vectors to average")
    width = len(vectors[0])
    totals = [0.0] * width
    for vector in vectors:
        if len(vector) != width:
            raise ValueError("cannot average vectors of different dimensions")
        for index, component in enumerate(vector):
            totals[index] += component
    return normalize([total / len(vectors) for total in totals])
