# src/pipelines/CLAUDE.md

Scoped to this directory. Root [CLAUDE.md](../../CLAUDE.md) already covers the
SQL-not-LLM invariant, the three source paths, and the DLT-expectations
column-scoping gotcha — read that first. This file is what's left: conventions
for writing or editing a transform in here specifically.

## How a table gets defined and found

- The DLT table name is the Python function name — no file here passes
  `name=` to `@dlt.table`. Renaming a function renames its output table.
- Downstream tables reference upstream ones by string, via
  `dlt.read("table_name")`, never by importing the producing function. A
  rename means grepping every `dlt.read("old_name")` across this directory
  (and `daily_signals.py`, which reads four of the others) to update it.
- A new file is invisible to the pipeline until it's added to the `libraries:`
  list in [`resources/signals.pipeline.yml`](../../resources/signals.pipeline.yml).
  Order in that list doesn't matter — DLT resolves the DAG from the
  `dlt.read()` calls, not file order.

## expect_or_drop vs. expect

Both appear throughout this directory and mean different things:

- `@dlt.expect_or_drop` — the row is unusable without this (e.g. `has_value`,
  `has_symbol`). Drop it silently.
- `@dlt.expect` (no `_or_drop`) — the row is suspicious but dropping it would
  hide a gap worth seeing: `derived_q4_positive` (xbrl_metrics.py — a negative
  derived Q4 usually means a restatement), `grade_vocabulary_known`
  (rating_changes.py — an unmapped analyst grade is a vocabulary gap to add to
  `GRADE_SCALE`, not bad data), `qa_boundary_found` (transcript_turns.py — no
  detected handover may just mean a prepared-remarks-only call),
  `transcript_keeps_speaker` (chunk_and_embed.py — a NULL speaker means a join
  or split silently de-attributed a quote).

  Use `expect`, not `expect_or_drop`, when failing the check is a signal to
  fix code or config elsewhere, not a reason to lose the row.

## Bronze is append-only

`xbrl_facts.py`, `transcript_turns.py`, `rating_changes.py`, and the
news/trends branches of `chunk_and_embed.py` all open with a
`row_number()` window over `_ingested_at` (or `filed`) descending, filtered to
`_r = 1`, before doing anything else. Re-running an ingest job re-lands rows
that are already there, so every silver table sourced from a bronze table
dedupes first. A new source file should follow the same pattern rather than
assume bronze is insert-once.

## chunk_and_embed.py chunking, verified against the code

- `MAX_CHUNK_CHARS = 1500` is a target for retrieval quality, not a model
  limit — the embedding endpoint's real ceiling (~56,000 characters) is
  covered in root CLAUDE.md and ARCHITECTURE.md's Known risks. Don't re-derive
  it here.
- Transcript turns are packed into ~1500-character parts on sentence
  boundaries (never overlapping, never mid-sentence); a single sentence longer
  than the target still becomes its own part rather than being cut.
- News and industry-trend items are **not** split or windowed at all — each
  bronze row becomes exactly one chunk (title + summary concatenated),
  whatever its length. The module's own docstring says these "fall back to
  overlapping windows sized to the embedding model" — that's not what the
  code does; there's no windowing function to reuse here for a new prose
  source, and macro is not chunked into this table at all (see below).
- `chunk_id` is built from stable keys (`call_id:turn_index:part_index` for
  transcripts, a hash of `url` for news/trends), so re-running the pipeline on
  unchanged source data reproduces the same chunk rows rather than
  duplicating or re-triggering them. Change Data Feed is on
  (`delta.enableChangeDataFeed`) specifically so Vector Search can sync off
  that stability incrementally instead of re-indexing the whole table.

## Flag for the user: macro isn't in the prose/chunking path

Root CLAUDE.md's pipeline-path list puts `src/ingest/macro.py` under "Prose /
chunking, retrieved by the LLM," feeding `chunk_and_embed.py`. The code
disagrees: `bronze_macro` is pure numeric FRED series data (`series_id`,
`obs_date`, `value` — see `src/ingest/macro.py`), `macro_context.py` computes
`latest_value` / `change_30d` / `change_90d` / `direction_90d` entirely in
SQL, and `chunk_and_embed.py` never reads any macro table. Structurally,
`macro_context.py` belongs with the SQL-computed, no-LLM path (same
discipline as `xbrl_metrics.py`), not the retrieval path. This looks like a
real inaccuracy in root CLAUDE.md rather than staleness in this directory —
worth the user's call on whether to fix the root doc or whether a
text/commentary macro source was intended and never built.
