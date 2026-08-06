"""Embedding worker.

    python worker.py           # long-running loop, what the container runs
    python worker.py --once    # drain the queue and exit
    python worker.py --status  # report the queue without embedding anything
    python worker.py --verify  # prove the config reproduces the corpus vectors
    python worker.py --extract # rebuild profile paragraphs that were never made

Runs as its own process, separate from the API. Polls for Personality rows with
embedding_status = 'pending', embeds them, writes the vector back.

--once and --status exist for operating it by hand: when a profile is stuck at
pending, the first question is whether the worker is running at all, and the
answer should not require reading container logs. Both are safe to run while
the loop is also running - claiming uses FOR UPDATE SKIP LOCKED, so two
processes take disjoint batches instead of fighting.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import embeddings
from DATABASE.ORM import EmbeddingStatus, Personality, session_scope
from pipeline import Pipeline


def _status() -> int:
    """What is in the queue, and whether this process could drain it."""
    from sqlalchemy import func, select

    space = embeddings.space()
    print(f"embedding space: {space.describe()}")

    with session_scope() as session:
        rows = session.execute(
            select(Personality.embedding_status, func.count())
            .group_by(Personality.embedding_status)
        ).all()
        counts = {status.value: count for status, count in rows}

        # Pending with no paragraph never drains - it is not a backlog, it is a
        # stuck row, and it looks identical from the outside.
        unembeddable = session.scalar(
            select(func.count())
            .select_from(Personality)
            .where(
                Personality.embedding_status == EmbeddingStatus.PENDING,
                Personality.profile_paragraph.is_(None),
            )
        )

    print("\nprofiles by embedding_status:")
    for name in ("pending", "processing", "done", "failed"):
        print(f"  {name:<12} {counts.get(name, 0)}")

    if unembeddable:
        print(
            f"\n  {unembeddable} pending row(s) have no profile_paragraph and "
            f"cannot be embedded.\n"
            f"  Their onboarding extraction failed - re-run onboarding for them."
        )
    return 0


def _verify(samples: int = 3) -> int:
    """Re-embed locations the seed already embedded, and compare.

    This is the only way to be *sure* the configuration is right. The space
    guard compares recorded strings, which catches a wrong label but not a
    wrong prefix, a wrong dimensionality, or a hosted model that differs from
    the local one. Re-embedding known text and measuring the distance to the
    stored vector tests the thing that actually matters.

    A cosine near 1.0 means user vectors will land in the same space as the
    corpus. Anything lower means they will not, and recommendations would be
    confident nonsense.
    """
    from sqlalchemy import select

    from DATABASE.ORM import Location

    space = embeddings.space()
    print(f"identity  : {space.describe()}")
    print(f"provider  : {os.getenv('EMBEDDING_PROVIDER') or embeddings.DEFAULT_PROVIDER}")
    print(f"task type : {embeddings.task_type()}\n")

    with session_scope() as session:
        rows = session.scalars(
            select(Location)
            .where(
                Location.vec.is_not(None),
                Location.embedding_text.is_not(None),
            )
            .limit(samples)
        ).all()
        cases = [
            (row.name, row.embedding_text, list(row.vec), row.embedding_model,
             row.embedding_version)
            for row in rows
        ]

    if not cases:
        print("No embedded locations with embedding_text - nothing to compare against.")
        return 1

    print(f"corpus rows record: {cases[0][3]} / {cases[0][4]}")
    if cases[0][3] != space.model or cases[0][4] != space.version:
        print(
            "  ^ these differ from the identity above. Set EMBEDDING_MODEL and\n"
            "    EMBEDDING_VERSION to the corpus values, or the guard will refuse\n"
            "    to serve recommendations."
        )
    print()

    try:
        fresh = embeddings.embed([text for _, text, _, _, _ in cases])
    except embeddings.EmbeddingError as exc:
        print(f"could not embed: {exc}")
        return 1

    worst = 1.0
    for (name, _, stored, _, _), new in zip(cases, fresh):
        similarity = embeddings.cosine(stored, new)
        worst = min(worst, similarity)
        print(f"  {similarity:6.4f}  {name[:50]}")

    print()
    if worst > 0.99:
        print("MATCH - this configuration reproduces the corpus vectors.")
        return 0
    if worst > 0.90:
        print(
            f"CLOSE but not exact (worst {worst:.4f}). Usually a prefix or\n"
            f"dimensionality difference. Do not embed users until this is 1.0."
        )
        return 1
    print(
        f"MISMATCH (worst {worst:.4f}). This is a different model or a different\n"
        f"prefix. User vectors built this way would be meaningless against the\n"
        f"corpus - fix the configuration before running --once."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="TIO embedding worker.")
    parser.add_argument(
        "--once", action="store_true", help="drain the pending queue and exit"
    )
    parser.add_argument(
        "--status", action="store_true", help="report the queue and exit"
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="rebuild missing profile paragraphs from stored onboarding answers",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-embed known locations and check they match the stored vectors",
    )
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    if args.status:
        return _status()

    if args.verify:
        return _verify()

    if args.extract:
        counts = Pipeline().reextract_missing_paragraphs()
        print(f"rebuilt {counts['fixed']} paragraph(s) and requeued them")
        if counts["still_failing"]:
            print(f"{counts['still_failing']} still produced nothing usable")
        if counts["no_intake"]:
            print(
                f"{counts['no_intake']} have no stored questionnaire at all - "
                f"those people must redo onboarding"
            )
        return 0

    pipeline = Pipeline()

    if args.once:
        total = 0
        while True:
            done = pipeline.embed_pending_batch(args.batch_size)
            if done == 0:
                break
            total += done
        print(f"embedded {total} profile(s)")
        return 0

    pipeline.run_embedding_worker(batch_size=args.batch_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
