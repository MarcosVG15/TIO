# TIO — location corpus & embedding pipeline

## What you're working on

TIO is a travel-planning app: FastAPI + SQLAlchemy 2.0 + PostgreSQL 16 with
pgvector, React frontend in `web/`. Users onboard through a questionnaire plus a
short conversation; an LLM distils that into a taste profile, which is embedded
and matched against a corpus of real-world locations to drive recommendations.

Auth, accounts, and the ORM are done. **The location corpus does not exist yet
and neither does the embedding pipeline.** That is your job.

## Repo orientation

| Path | State |
|---|---|
| `DATABASE/ORM.py` | Complete. All tables, enums, `session_scope()`. Read this first. |
| `DATABASE/create_tables.py` | Complete. `--drop`, `--echo`, `--dry-run`. |
| `user_extraction.py` | Complete. LLM → `ExtractedProfile` (OpenAI structured outputs). |
| `pipeline.py` | **Stubs only.** Docstrings describe intended behaviour; every body is `...` or `NotImplementedError`. |
| `routers/discovery.py` | **Stubs.** `/destinations/recommended` and `/search` raise `not_implemented`. |
| `worker.py` | Thin entry point for the embedding worker loop. |
| `accounts.py`, `auth.py`, `routers/auth.py` | Working. Don't touch. |

## Design decisions already made — do not relitigate these

**Embedding dimension is 768.** `EMBEDDING_DIM` in `DATABASE/ORM.py`. Four
columns are typed `Vector(768)` with HNSW cosine indexes:
`personalities.personality_vector`, `locations.vec`, `itinerary_items.vec`,
`history.vec_snapshot`. Any model you pick must emit 768 natively or be
Matryoshka-truncatable to it.

**One embedding model for everything.** Users and locations must live in the
same vector space. Recommended: `google/embeddinggemma-300m` (768 native) via
`sentence-transformers`, running locally on GPU. Alternatives worth
benchmarking: `BAAI/bge-base-en-v1.5`, `nomic-embed-text-v1.5`,
`Qwen3-Embedding-0.6B` (truncate 1024→768). Do not use a hosted embedding API —
there's a local GPU and the corpus changes rarely.

**Symmetric paragraph embedding.** This is the core idea and the easiest thing
to get wrong. `user_extraction.py` produces a `profile_paragraph`: 80–150 words,
third person, present tense, opening "This traveller", covering taste (activity
affinities, pace, atmosphere, planning style, food as preference) and
*deliberately excluding* hard constraints and identifying details.

Locations must be described in **the same register** — a short prose paragraph
about what the place is like to visit, not a field dump. Embedding
`"Louvre, Rue de Rivoli, museum, €22"` against a taste essay produces
meaningless geometry. Raw facts (price, hours, accessibility) stay in columns
and act as SQL filters; only the character paragraph gets embedded.

Same model means same prefix convention on both sides. BGE/E5/Gemma-style models
are asymmetric about query vs. document prefixes — this is document-to-document,
so use the identical prefix for personalities and locations.

**No Google Places for the stored corpus.** Their terms forbid persisting POI
content locally (only `place_id` may be cached indefinitely). Fine for live
autocomplete, wrong for a seed corpus. Use OpenStreetMap via Overpass (ODbL),
Wikipedia/Wikivoyage for prose (CC BY-SA), optionally Foursquare's open Places
dump (Apache 2.0) later.

## Task 1 — schema migration (blocking, do first)

`pipeline.py` is written against columns that don't exist. Add them before
anything else; adding columns after seeding tens of thousands of rows is
painful.

To `Location`:
- `embedding_text: Text` — the exact paragraph that was embedded. Non-negotiable:
  it's what lets a model change trigger re-embedding without re-running the LLM.
- `embedding_model: str`, `embedding_version: str` — so
  `requeue_for_reembedding()` has something to compare against.
- `embedding_status: str` (`pending` / `processing` / `done` / `failed`),
  `embedding_attempts: int`, `embedding_error: Text | None`.
- `source: str`, `source_id: str`, with `UniqueConstraint("source", "source_id")`.
  Without this the seed script duplicates the corpus on every re-run.
- `source_hash: str` — hash of the upstream prose, so a refresh only re-flags
  `pending` when the text actually changed.
- `category: ActivityType` — the enum already exists; locations need it as a
  filterable column.

To `Personality`: the same `embedding_*` set, plus `profile_paragraph: Text`
(currently the paragraph is generated and thrown away — `pipeline.py`'s
`_persist_profile` docstring says it must be stored verbatim, but there's no
column for it).

Alembic is in `requirements.txt` but not initialised. Either set it up properly
or, if the database is still disposable, extend the ORM and re-run
`create_tables.py --drop`. Ask before dropping anything.

## Task 2 — `seed_locations.py`

A standalone batch script, **not** part of the API process. Four subcommands,
each independently re-runnable and resumable:

**`ingest`** — Overpass POST to `https://overpass-api.de/api/interpreter`,
one bbox per run. Filter `tourism` / `historic` / `amenity` tags. `out center`
so ways get a centroid. Map tags to columns, upsert on `(source, source_id)`
via `postgresql.insert(...).on_conflict_do_update(...)`, leave `vec` NULL and
`embedding_status='pending'`.

Mapping traps:
- POIs without a `name` tag are noise — drop them.
- `country` is never a POI tag. It comes from the job's arguments.
- `city` is unreliable (`addr:city` is sparse) — fall back to the queried region.
- `opening_hours` is OSM's own DSL string, **not** JSON. The column is JSONB, so
  wrap it: `{"raw": "Tu-Su 09:00-18:00", "format": "osm"}`. Parse later if ever.
- `fee=yes|no` → `TicketRequirement.PAID|FREE`, else `NONE`.
- Keep the `wikidata` and `wikipedia` tags — they're the join key for prose.
- The `on_conflict` `set_` must refresh volatile facts (address, hours, coords)
  but **never** clobber `embedding_text`, `vec`, or `embedding_status`.

**`dedupe`** — OSM returns the same place as both a node (entrance) and a way
(footprint). Collapse on `wikidata` QID where present; otherwise normalized name
within ~75m. Keep the way. Run before `describe` so no LLM budget is wasted.

**`describe`** — generate `embedding_text`. POIs with a `wikipedia` tag: fetch
the intro extract (Wikimedia action API, `prop=extracts&exintro&explaintext`,
20 titles per call, real `User-Agent` with contact info required), then have an
LLM rewrite it into the character paragraph described above. Expect 15–30%
coverage. The rest get a deterministic template built from tags (`cuisine`,
`outdoor_seating`, `heritage`, `wheelchair`, `description`) — don't send
tag-only POIs to the LLM, there's no signal in the input.

**`embed`** — load the model once, `SELECT ... WHERE embedding_status='pending'
LIMIT 512`, encode the batch on GPU in fp16, write back, loop until dry.
L2-normalize before storing. This mirrors `pipeline.process_next_embedding()`
but batched — one-row-at-a-time is right for live onboarding and absurd for a
bulk seed.

Bulk-load ops: build the HNSW index *after* loading, not before. `_hnsw()` is in
`__table_args__` so `create_tables.py` creates it empty and every insert then
pays index maintenance. For a large seed, drop the index, load, recreate — and
raise `maintenance_work_mem` first, it's the biggest lever on build time.

## Task 3 — `pipeline.py`

Implement the stubs against their existing docstrings, which are accurate and
were written deliberately. Key contracts: the LLM extraction happens *before*
the transaction opens, never inside it; the row itself is the job queue (no
separate queue table); `_claim_pending` uses `SELECT ... FOR UPDATE SKIP LOCKED`;
failures increment `embedding_attempts` and return to `pending` until a cap,
then `failed`.

## Task 4 — `routers/discovery.py`

`/destinations/recommended` should be one SQL query, not pure cosine:
- **Hard filters in `WHERE`** — accessibility, dietary, budget, geography. These
  are constraints, never distances. A wheelchair-inaccessible museum ranking
  third because the vibe matched is a product failure; it's precisely why the
  extraction prompt keeps constraints out of the paragraph.
- `vec <=> :personality_vector` as the primary score.
- A small-weight popularity prior. Pure content similarity has no notion of
  "this place is actually good" and will surface obscure POIs over loved ones.
- Diversity — cosine top-20 returns twenty near-identical museums. Over-fetch and
  cap per category/city.

Later, `History.vec_snapshot` (already in the schema) becomes the feedback loop:
average the vectors of highly-rated trips into a revealed-taste vector and blend
it with the declared `personality_vector`, shifting weight toward revealed taste
as ratings accumulate.

## Local database

Do **not** develop against production. `docker-compose.yml` already runs
`pgvector/pgvector:pg16` on host port **5433**:

```
docker compose up -d db
python DATABASE/create_tables.py
```

Connection string for local work (the compose credentials are dev-only defaults,
not secrets):

```
postgresql+psycopg://postgres:postgres@localhost:5433/tio_database
```

`DATABASE/ORM.py` reads `DATABASE_URL` from `.env` at the project root. `.env` is
gitignored and holds real credentials — never print it, never commit it, never
paste its contents into a chat or a bug report.

## Suggested order

1. Schema migration, verify with `create_tables.py --dry-run`.
2. `ingest` one city, ~2,000 rows. Stop and read 30 rows by hand — the tag
   mapping is a first guess and needs tuning against what actually comes back.
3. `dedupe`, then `describe --limit 50`. Read the paragraphs. If they don't read
   like the user-side `profile_paragraph`, fix the prompt before scaling; no
   embedding model compensates for mismatched register.
4. `embed`, then sanity-check retrieval: hand-write three fake personality
   paragraphs, embed them, eyeball the top 10 for each.
5. Only then scale to more cities.

Overpass is a donated shared service — one city per call, pause between them.
Past country scale, switch to a Geofabrik `.osm.pbf` extract with `pyosmium`:
same tags, same mapping code, no rate limits.
