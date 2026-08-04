"""Embedding worker.

    python worker.py

Runs as its own process, separate from the API. Polls for Personality rows
with embedding_status = 'pending', embeds them, writes the vector back.
"""

from pipeline import Pipeline


def main() -> int:
    Pipeline().run_embedding_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
