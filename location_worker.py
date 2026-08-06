"""Embed the location corpus - the job that was missing.

    python location_worker.py            # embed notable rows, forever
    python location_worker.py --once     # one batch, then exit
    python location_worker.py --status   # report the queue, embed nothing
    python location_worker.py --all      # include rows with no notability

Why this exists. Locations were embedded by the external populate script, which
covered 97,530 rows and stopped; the other 8,159,347 sit at 'pending' with no
consumer anywhere in the deployed stack, because `worker.py` embeds *profiles*
(Personality) and nothing in this repo ever wrote Location.vec. That gap is not
visible from the outside: the rows look queued, and nothing is draining them.

Notability first, and by default only. Recommendation filters on notability -
a wikidata entity, a wikipedia article or a photograph - so a row without any
of the three cannot reach a shelf or an itinerary however good its vector is.
Embedding those first turns a job of 8.16M rows into one of roughly 610k for
the same user-visible result. `--all` exists for completeness, not for haste.

The vectors this produces must be interchangeable with the 97,530 that already
exist, so the space is stamped from `embeddings.space()` and the text is put
through the same prefix and provider as everything else. Getting that wrong
does not fail loudly - it produces plausible vectors that mean nothing next to
the ones already in the table.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select, text, update

import embeddings
from DATABASE.ORM import EmbeddingStatus, Location, session_scope

log = logging.getLogger("location_worker")

#: A row is worth embedding if anyone has ever bothered to record it as a
#: place: an entity, an article, or a photograph. Mirrors the gate in
#: recommend.top_destinations - if these two disagree, the worker spends its
#: time on rows the recommender will never show.
NOTABLE = or_(
    Location.wikidata_qid.is_not(None),
    Location.wikipedia_title.is_not(None),
    Location.picture.is_not(None),
)


class Stop:
    """Ctrl-C stops after the current batch rather than mid-write."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self, *_: object) -> None:
        if self._event.is_set():  # second interrupt: the user means it
            raise KeyboardInterrupt
        log.info("stopping after this batch")
        self._event.set()

    def wait(self, seconds: float) -> None:
        self._event.wait(seconds)


def _claim(batch_size: int, notable_only: bool) -> list[tuple[UUID, str]]:
    """Take ownership of up to `batch_size` pending rows with text to embed.

    Rows with no `embedding_text` are left alone rather than parked as failed.
    That is the opposite of what the profile worker does, and deliberately so:
    a profile without a paragraph is one broken onboarding, but a location
    without one may be millions of rows the populate script never described,
    and marking those 'failed' would destroy the queue to report a fact that
    --status can simply count.

    SKIP LOCKED so several of these can run at once - on the GPU box and on
    the server - without claiming each other's rows.
    """
    conditions = [
        Location.embedding_status == EmbeddingStatus.PENDING,
        Location.embedding_text.is_not(None),
    ]
    if notable_only:
        conditions.append(NOTABLE)

    with session_scope() as session:
        # Never queue behind the populate script. If a row this claim wants is
        # locked by a bulk insert or update, waiting on it would hold our own
        # locks while doing nothing, and a populate job doing thousands of
        # writes a second is exactly what should win that contest. Failing
        # fast costs one empty batch and the next poll picks up different rows.
        session.execute(text("SET LOCAL lock_timeout = '2s'"))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))
        rows = session.scalars(
            select(Location)
            .where(*conditions)
            # No ORDER BY on purpose. Sorting 8M pending rows on every batch
            # costs more than the embedding does, and claim order does not
            # matter once the queue is already filtered to what is worth doing.
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()

        claimed: list[tuple[UUID, str]] = []
        for row in rows:
            row.embedding_status = EmbeddingStatus.PROCESSING
            claimed.append((row.location_id, row.embedding_text or ""))
        return claimed


def reclaim_stale(older_than_minutes: int = 30) -> int:
    """Return rows stuck in 'processing' to the queue.

    A worker that is killed - a deploy, an OOM, a Ctrl-C during the embedding
    call - leaves its claimed rows marked 'processing' with nobody working on
    them. Nothing else ever looks at that state, so those rows would sit out
    the rest of the run: invisible to `--status` as backlog and never embedded.

    Time-based rather than owner-based because claims are not owned. Anything
    still 'processing' after half an hour cannot be a live batch: a batch is
    one model call over a few dozen short texts, and no such call runs that
    long without having failed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    with session_scope() as session:
        session.execute(text("SET LOCAL lock_timeout = '2s'"))
        result = session.execute(
            update(Location)
            .where(
                Location.embedding_status == EmbeddingStatus.PROCESSING,
                Location.updated_at < cutoff,
            )
            .values(embedding_status=EmbeddingStatus.PENDING)
        )
        return int(result.rowcount or 0)


def _release(location_ids: list[UUID], error: str) -> None:
    """Put a failed batch back on the queue, with the reason recorded.

    Back to 'pending' rather than 'failed': the usual cause is Ollama being
    restarted or briefly overloaded, which is not the row's fault, and a
    corpus-sized job must survive that without shedding rows permanently.
    """
    with session_scope() as session:
        session.execute(text("SET LOCAL lock_timeout = '2s'"))
        for location_id in location_ids:
            row = session.get(Location, location_id)
            if row is None:
                continue
            row.embedding_status = EmbeddingStatus.PENDING
            row.embedding_attempts = (row.embedding_attempts or 0) + 1
            row.embedding_error = error[:500]


def embed_batch(batch_size: int, notable_only: bool) -> int:
    """Embed one batch. Returns how many rows were written.

    Three transactions, matching the profile worker: the claim commits before
    the embedding call so a slow model is not holding row locks, and the write
    is separate so a failure can release the claim instead of rolling it back.
    """
    claimed = _claim(batch_size, notable_only)
    if not claimed:
        return 0

    space = embeddings.space()
    try:
        vectors = embeddings.embed([text for _, text in claimed])
    except Exception as exc:
        log.warning("embedding call failed for %d row(s): %s", len(claimed), exc)
        _release([lid for lid, _ in claimed], str(exc))
        return 0

    if len(vectors) != len(claimed):
        # Never observed, but a provider returning a short list would otherwise
        # zip silently and put the wrong vector on the wrong place.
        _release([lid for lid, _ in claimed], "provider returned %d vectors for %d texts"
                 % (len(vectors), len(claimed)))
        raise embeddings.EmbeddingError(
            f"expected {len(claimed)} vectors, got {len(vectors)}"
        )

    with session_scope() as session:
        for (location_id, _), vector in zip(claimed, vectors):
            row = session.get(Location, location_id)
            if row is None:
                continue  # deleted while we were embedding
            row.vec = vector
            row.embedding_model = space.model
            row.embedding_version = space.version
            row.embedding_status = EmbeddingStatus.DONE
            row.embedding_error = None

    return len(claimed)


def run(batch_size: int, notable_only: bool, poll_interval: float = 30.0) -> None:
    """Drain the queue until it is empty or we are asked to stop."""
    stop = Stop()
    signal.signal(signal.SIGINT, stop.request)
    signal.signal(signal.SIGTERM, stop.request)

    space = embeddings.space()
    log.info(
        "embedding locations as %s/%s (%s)",
        space.model,
        space.version,
        "notable only" if notable_only else "all rows",
    )

    freed = reclaim_stale()
    if freed:
        log.info("returned %d stale 'processing' row(s) to the queue", freed)

    done = 0
    started = time.monotonic()
    while not stop.requested:
        try:
            written = embed_batch(batch_size, notable_only)
        except Exception:
            # One bad batch must not end a job measured in hundreds of
            # thousands of rows.
            log.exception("batch failed")
            written = 0

        if written == 0:
            stop.wait(poll_interval)
            continue

        done += written
        elapsed = time.monotonic() - started
        rate = done / elapsed if elapsed > 0 else 0.0
        log.info("embedded %d rows (%.1f/s)", done, rate)

    log.info("stopped after %d rows", done)


def status() -> None:
    """Report the queue.

    One pass, not several. Every figure here needs a full scan of a
    corpus-sized table, and this is the command anyone watching progress will
    run repeatedly - asking three times for what one query can answer made it
    three times as slow for no extra information.
    """
    ready = NOTABLE & Location.embedding_text.is_not(None)
    with session_scope() as session:
        rows = session.execute(
            select(
                Location.embedding_status,
                func.count().label("rows"),
                func.count(Location.vec).label("have_vector"),
                func.count(Location.embedding_text).label("have_text"),
                func.count().filter(NOTABLE).label("notable"),
                func.count().filter(ready).label("ready"),
                func.count()
                .filter(NOTABLE & Location.embedding_text.is_(None))
                .label("no_text"),
            ).group_by(Location.embedding_status)
        ).all()

    space = embeddings.space()
    print(f"space: {space.model} / {space.version}")
    print(f"{'status':<12}{'rows':>12}{'vectors':>12}{'text':>12}{'notable':>12}")
    todo = no_text = 0
    for state, count, vectors, text, notable, ready_here, no_text_here in rows:
        name = state.value if hasattr(state, "value") else str(state)
        print(f"{name:<12}{count:>12,}{vectors:>12,}{text:>12,}{notable:>12,}")
        if state == EmbeddingStatus.PENDING:
            todo, no_text = ready_here, no_text_here

    print(f"\nnotable and ready to embed: {todo:,}")
    if no_text:
        # Not an error, but it caps what this worker can ever do, so it is
        # stated rather than left to be inferred from a queue that stops
        # draining while rows remain pending.
        print(
            f"notable but no embedding_text: {no_text:,}"
            "  <- this worker cannot embed these; the populate script writes"
            " that column"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="one batch, then exit")
    parser.add_argument("--status", action="store_true", help="report and exit")
    parser.add_argument(
        "--all",
        action="store_true",
        help="embed rows with no notability evidence too (8.16M rows)",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    if args.status:
        status()
        return 0

    notable_only = not args.all
    if args.once:
        written = embed_batch(args.batch_size, notable_only)
        print(f"embedded {written} row(s)")
        return 0

    run(args.batch_size, notable_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
