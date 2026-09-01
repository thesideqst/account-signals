# src/app/CLAUDE.md

FastAPI app (`app.py`, single file). See root [CLAUDE.md](../../CLAUDE.md) for
the secret-decoding gotcha (`stt_key`), why this file uses `psycopg` directly
instead of Lakehouse Federation, and the `app.yml` `postgres:` vs `database:`
trap. This file is what's specific to the routes, the STT/grading flow, and
audio streaming.

## Route map

- **Diagnostics** — `/api/health`, `/api/audio-check/{account_id}`. Exist
  because this workspace uses a PAT and app logs need OAuth, so these are the
  only way the app can explain what it can actually reach (see their
  docstrings).
- **Reads** — `/api/accounts`, `/api/briefing/{account_id}`,
  `/api/episode/{briefing_id}`, `/api/episodes/{account_id}`,
  `/api/answers/{briefing_id}`, `/api/sources/{account_id}` (pulls from the
  episode's own `lineage` JSON, not a fresh query — a fresh "recent items"
  query once put a Moderna piece next to an NVIDIA episode),
  `/api/grade/{account_id}`, `/api/run/{run_id}` (polls a job run; maps
  task_key → human text via the `STEP` dict), `/api/roundtrip/{account_id}`
  (reads the same answer count from both Postgres and Unity Catalog via
  `statement_execution`, to show the sync lag side by side).
- **Audio** — `/api/audio/{account_id}`, `/api/audio-by-id/{briefing_id}`.
  See below.
- **Writes** — `/api/recap/{account_id}` (POST), `/api/answer/{account_id}`
  (POST), `/api/topic/{account_id}` (POST), `/api/generate/{account_id}`
  (POST), `/api/episode/{briefing_id}` (DELETE, hides rather than deletes —
  `gold_briefing_serving` is a synced table; full story in ARCHITECTURE.md
  "Known risks" and SCOPE.md 2026-08-31).
- `/` reads and returns `static/index.html` off disk on every request — not
  mounted via `StaticFiles`.

## Two recall paths, graded at two different times

- **`/api/recap`** — free-form spoken recap. Transcribes, then only
  `INSERT`s into `app.recall_recaps`. No grading happens in-request; it's
  graded later by the nightly `src/grading/grade.py` job (root CLAUDE.md)
  once the recap has synced back to Unity Catalog.
- **`/api/answer`** — one targeted comprehension question. Transcribes, then
  grades immediately in-request against `GRADE_PROMPT` via
  `workspace().serving_endpoints.query(name="databricks-gpt-oss-120b")`, and
  `INSERT`s the score/verdict/missed+teach into `app.recap_answers`. This is
  a second, separate LLM-grading path from `src/grading/grade.py`'s nightly
  job — different code, different table, different model call, immediate
  instead of batched (the docstring reasons: "feedback that arrives tomorrow
  does not teach anyone anything").
- The Whisper multipart-upload logic is duplicated rather than shared: a
  `_transcribe()` helper exists and is used by `/api/answer`, but
  `/api/recap`'s handler (`submit_recap`) rebuilds the same multipart request
  inline instead of calling it. Changing STT behavior (timeout, error
  handling, model) means changing both.

## `/api/audio/{account_id}` — Range requests (added in `4285a29`)

- Downloads the whole MP3 from the UC Volume into memory
  (`resp.contents.read()`) on every request, then slices in Python — not a
  ranged read against the Volume. Fine at a few MB per file; the code comment
  above `data = resp.contents.read()` flags this as the thing to fix if files
  grow.
- `_parse_range()` implements RFC 7233: 206 + `Content-Range` for a valid
  single range, 416 with `Content-Range: bytes */<total>` for a range past
  EOF, plain 200 for no/absent `Range` header, and a malformed or multi-range
  header is silently ignored (served as a full 200) rather than rejected —
  multi-range would need a `multipart/byteranges` body that no browser media
  element actually requests.
- The reason this exists at all: advertising `Accept-Ranges: bytes` without
  honoring `Range` used to break playback outright — Chrome asks for a range,
  gets a 200 with the whole body back, and stalls at readyState 0 (the
  0:00/0:00 player). See the comment above `rng = _parse_range(...)`.
- **`/api/audio-by-id/{briefing_id}`** (past-episode playback) was not
  updated by that commit — it still sends `Accept-Ranges: bytes` but never
  calls `_parse_range()`, always returning the full file as a plain 200. It
  has the exact advertise-without-honor shape the comment above warns about,
  just on the second audio endpoint. Worth knowing before assuming seeking
  works on past episodes.

## `requirements.txt`

`databricks-sdk>=0.102.0` is pinned because the Databricks Apps runtime ships
an older SDK without `WorkspaceClient.postgres` — omitting the pin fails at
runtime with `AttributeError`, not at install time.

## `app.yaml`

Sets `CATALOG`, `SCHEMA`, `BRIEFING_JOB_ID`, `DATABRICKS_WAREHOUSE_ID`,
`LAKEBASE_ENDPOINT` — root CLAUDE.md already lists these under
"Workspace-specific IDs to update on a fresh deploy." `PGHOST` /
`PGDATABASE` / `PGUSER` / `PGPASSWORD` / `PGPORT` are injected by the
attached `postgres` resource, not set here.

## `/api/generate` concurrency guard

Before starting a job run, it lists `active_only=True` runs for
`BRIEFING_JOB_ID` and 409s if one is already in flight, rather than letting
two runs race. The docstring explains why a race is worse than a wait: both
runs append to `gold_briefing`, and the serving table join picks up the
newest briefing row — if run A narrates while run B has already written a
newer briefing, run A's audio join finds nothing and the episode publishes
silent.
