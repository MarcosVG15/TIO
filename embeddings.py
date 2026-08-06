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

#: How vectors are computed. Deliberately separate from *identity* below: the
#: corpus was embedded by a GPU box running nomic locally, and this server has
#: no GPU, so the same vectors have to come from Nomic's hosted API instead.
#: Same weights, same space, different machine - the provider is an
#: implementation detail, the identity is the contract.
DEFAULT_PROVIDER = "ollama"

#: Where the model that built the corpus actually runs. CPU is fine here: the
#: GPU was needed to embed tens of thousands of location paragraphs, but a user
#: is one paragraph once at signup, and v2-moe activates only 305M of its 475M
#: parameters per token. A second per profile is not worth a GPU.
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text-v2-moe:latest"

#: Prepended by hand, because Ollama does not add task prefixes itself. Must
#: match the seed exactly - see prefix().
DEFAULT_PREFIX = "search_document: "

#: Nomic's hosted endpoint. The model name must match what embedded the corpus.
NOMIC_URL = "https://api-atlas.nomic.ai/v1/embedding/text"
DEFAULT_NOMIC_MODEL = "nomic-embed-text-v2-moe"

#: Nomic models are trained with task prefixes and the prefix changes the
#: vector. The API prepends it from `task_type`, so we must NOT also prepend it
#: by hand - doing both yields "search_document: search_document: ..." and a
#: quietly different vector. Both sides of the comparison use the same value,
#: per the symmetric design in the HANDOFF.
DEFAULT_TASK_TYPE = "search_document"

DEFAULT_OPENAI_MODEL = "text-embedding-3-small"

#: Anything that changes the geometry without changing the model name belongs
#: here - the output dimension, and any reshaping of the input text. A vector
#: built under a different convention is silently unusable against the corpus,
#: which is exactly what `embedding_version` exists to detect.
def _default_version(model: str) -> str:
    return f"{model}-d{EMBEDDING_DIM}"


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
    """The identity stamped on vectors we produce, and compared against rows.

    These two strings must equal whatever the seed job wrote into
    `locations.embedding_model` / `embedding_version`, exactly. They are not
    the provider: you can change how a vector is computed (GPU box -> hosted
    API) without changing what it *is*, and that is precisely the situation
    here. Find the corpus values with:

        SELECT embedding_model, embedding_version, count(*)
        FROM locations WHERE vec IS NOT NULL GROUP BY 1,2;
    """
    model = os.getenv("EMBEDDING_MODEL") or _default_identity_model()
    version = os.getenv("EMBEDDING_VERSION") or _default_version(model)
    return Space(model=model, version=version, dim=EMBEDDING_DIM)


def _default_identity_model() -> str:
    provider = (os.getenv("EMBEDDING_PROVIDER") or DEFAULT_PROVIDER).lower()
    if provider == "openai":
        return os.getenv("OPENAI_EMBED_MODEL") or DEFAULT_OPENAI_MODEL
    if provider == "nomic":
        return os.getenv("NOMIC_MODEL") or DEFAULT_NOMIC_MODEL
    return os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL


def task_type() -> str:
    return os.getenv("EMBEDDING_TASK_TYPE") or DEFAULT_TASK_TYPE


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


def prefix() -> str:
    """Text prepended before embedding, for providers that do not do it.

    Nomic models are trained with task prefixes and the prefix changes the
    vector materially - measured at cosine 0.95 between the same sentence
    embedded as search_document versus search_query. The seed prepends
    "search_document: " by hand to both locations and profiles, so anything
    reproducing those vectors must do exactly the same.
    """
    value = os.getenv("EMBEDDING_PREFIX")
    return DEFAULT_PREFIX if value is None else value


def _embed_ollama(prepared: Sequence[str]) -> list[list[float]]:
    """The same Ollama that embedded the corpus, over its HTTP API.

    This is the only provider that can reproduce the corpus exactly: Nomic's
    hosted API does not serve nomic-embed-text-v2 (it 500s; only v1.5 is
    available), so the model that built the corpus is reachable only from an
    Ollama running it.

    The worker does not have to sit on the same machine as the API - it needs
    a database connection and nothing else - so this can point at the GPU box
    over the network, or the worker can simply run there.
    """
    import httpx  # local: other providers must not require it

    base = (os.getenv("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
    model = os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    tag = prefix()
    out: list[list[float]] = []

    for start in range(0, len(prepared), MAX_BATCH):
        chunk = [tag + text for text in prepared[start : start + MAX_BATCH]]
        try:
            response = httpx.post(
                f"{base}/api/embed",
                json={"model": model, "input": chunk},
                # Generous: a CPU-only host embedding a batch is slow but the
                # work is rare, and a timeout here means a retried paid call.
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise EmbeddingError(
                f"ollama embedding request to {base} failed: {exc}"
            ) from exc

        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(chunk):
            raise EmbeddingError(
                f"ollama returned {len(vectors) if isinstance(vectors, list) else '?'}"
                f" vectors for {len(chunk)} inputs"
            )
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                raise EmbeddingError(
                    f"expected {EMBEDDING_DIM} dimensions, got {len(vector)} - "
                    f"OLLAMA_MODEL is probably not the model that built the corpus"
                )
            out.append(normalize(vector))

    return out


def prefix() -> str:
    """Text prepended before embedding, for providers that do not do it.

    Nomic models are trained with task prefixes and the prefix changes the
    vector materially - measured at cosine 0.951 between the same sentence
    embedded as search_document versus search_query. The seed prepends
    "search_document: " by hand to both locations and profiles, so anything
    reproducing those vectors must do exactly the same.

    Set EMBEDDING_PREFIX to "" to disable it; unset means the default.
    """
    value = os.getenv("EMBEDDING_PREFIX")
    return DEFAULT_PREFIX if value is None else value


def _embed_ollama(prepared: Sequence[str]) -> list[list[float]]:
    """The same Ollama build that embedded the corpus, over its HTTP API.

    The only provider that reproduces the corpus exactly: Nomic's hosted API
    does not serve v2-moe (it 500s; only v1.5 is available) and neither does
    HuggingFace's TEI, so the model is reachable only from an Ollama running
    it. Running that on CPU is the whole point - see DEFAULT_OLLAMA_URL.
    """
    import httpx  # local: the other providers must not require it

    base = (os.getenv("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
    model = os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    tag = prefix()
    out: list[list[float]] = []

    for start in range(0, len(prepared), MAX_BATCH):
        chunk = [tag + text for text in prepared[start : start + MAX_BATCH]]
        try:
            response = httpx.post(
                f"{base}/api/embed",
                json={"model": model, "input": chunk},
                # Generous, because CPU inference is slow and the first call
                # after a restart also pays to load the model into memory.
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise EmbeddingError(
                f"ollama embedding request to {base} failed: {exc}"
            ) from exc

        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(chunk):
            raise EmbeddingError(
                f"ollama returned "
                f"{len(vectors) if isinstance(vectors, list) else '?'} vectors "
                f"for {len(chunk)} inputs"
            )
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                raise EmbeddingError(
                    f"expected {EMBEDDING_DIM} dimensions, got {len(vector)} - "
                    f"OLLAMA_MODEL is probably not the model that built the corpus"
                )
            out.append(normalize(vector))

    return out


def _embed_nomic(prepared: Sequence[str]) -> list[list[float]]:
    """Nomic's hosted API, for the same model the corpus was embedded with.

    The task type is sent as a parameter rather than prepended to the text,
    because the API adds the prefix itself - doing both would double it and
    silently produce a different vector.
    """
    import httpx  # local: the OpenAI path must not require it

    key = os.getenv("NOMIC_API_KEY")
    if not key:
        raise EmbeddingError("NOMIC_API_KEY is not set")

    model = os.getenv("NOMIC_MODEL") or DEFAULT_NOMIC_MODEL
    out: list[list[float]] = []

    for start in range(0, len(prepared), MAX_BATCH):
        chunk = list(prepared[start : start + MAX_BATCH])
        try:
            response = httpx.post(
                NOMIC_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "texts": chunk,
                    "task_type": task_type(),
                    "dimensionality": EMBEDDING_DIM,
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise EmbeddingError(f"nomic embedding request failed: {exc}") from exc

        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(chunk):
            raise EmbeddingError(
                f"nomic returned {len(vectors) if isinstance(vectors, list) else '?'} "
                f"vectors for {len(chunk)} inputs"
            )
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                raise EmbeddingError(
                    f"expected {EMBEDDING_DIM} dimensions, got {len(vector)} - "
                    f"check NOMIC_MODEL and dimensionality"
                )
            out.append(normalize(vector))

    return out


def embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed several texts, preserving order.

    Batched, because one call per row turns a corpus pass into an afternoon
    of round trips.
    """
    if not texts:
        return []

    prepared = [_prepare(text) for text in texts]

    provider = (os.getenv("EMBEDDING_PROVIDER") or DEFAULT_PROVIDER).lower()
    if provider == "ollama":
        return _embed_ollama(prepared)
    if provider == "nomic":
        return _embed_nomic(prepared)
    if provider != "openai":
        raise EmbeddingError(f"unknown EMBEDDING_PROVIDER {provider!r}")

    ours = space()
    client = _get_client()
    out: list[list[float]] = []

    for start in range(0, len(prepared), MAX_BATCH):
        chunk = prepared[start : start + MAX_BATCH]
        try:
            response = client.embeddings.create(
                # The provider's own model name, not the identity string - the
                # two are only the same when the provider is also OpenAI.
                model=os.getenv("OPENAI_EMBED_MODEL") or DEFAULT_OPENAI_MODEL,
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
